#!/usr/bin/env python3
r"""Generate CoLM202X AAS river-lake 2^3 factorial running namelists.

Generate USA, SA, Mekong, and Global configurations:

    python3 dlb_02_generate_namelists.py \
        --repo-root /path/to/CoLM202X \
        --out-dir /path/to/generated_namelists \
        --case-root /path/to/colm_cases \
        --forcing-nml JRA3Q

Use ``--region USA`` (or SA, Mekong, Global) to generate only one domain.

USA/SA/Mekong use 0.1-degree land and history grids with 06min routing data.
Global uses a 0.5-degree land grid, 15min routing data, and 0.25-degree history
output. Raw-data, runtime-data, and soil-initialization paths are fixed below.

One LSF submission script is generated beside each running namelist. Submit
one with ``bsub < CASE_NAME.lsf``; the generator never submits jobs itself.

READ-ONLY with respect to the CoLM repository, model cases, and input datasets.
The script writes only into --out-dir; it does not build, run, submit, or copy restarts.

Verified against zhongwangwei/CoLM202X master @
c545a96c0aa94f5323861bd3ad7929b7dcfd27f2 (2026-11-01 UTC).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from dlb_00_workflow_common import WorkflowError,preprocessor_state

from dlb_site_config import parse_site_args,unsupported_fields,input_issues,KEYS

BASE_TEMPLATE = Path('run/examples/Amazon_Grid_25km_IGBP_VG_RiverLake.nml')
DEFINE_FILE = Path('include/define.h')
RAWDATA = Path('/share/home/dq013/zhwei/colm/data/CoLMrawdata')
RUNTIME = Path('/share/home/dq013/zhwei/colm/data/CoLMruntime')
SOIL_INIT = Path('/share/home/dq013/zhwei/colm/data/soilstate/soilstate.nc')
MPI_ROOT = Path('/share/home/dq089/soft/mpich-4.2.3-intel')
REGIONS = {
    # USA/SA: CoLM202X_amazon_0929/run; Mekong: CoLM202X_1030/run.
    'USA': {
        'bounds': (25.0, 50.0, -125.0, -65.0), 'land_res': 0.1,
        'routing': '06min', 'history_res': 0.1, 'regional': True, 'label': '0p1',
    },
    'SA': {
        'bounds': (-56.0, 13.0, -82.0, -34.0), 'land_res': 0.1,
        'routing': '06min', 'history_res': 0.1, 'regional': True, 'label': '0p1',
    },
    'Mekong': {
        'bounds': (8.0, 36.0, 93.0, 111.0), 'land_res': 0.1,
        'routing': '06min', 'history_res': 0.1, 'regional': True, 'label': '0p1',
    },
    'Global': {
        'bounds': (-90.0, 90.0, -180.0, 180.0), 'land_res': 0.5,
        'routing': '15min', 'history_res': 0.25, 'regional': False, 'label': '0p5',
    },
}
ROUTING_GATES = (
    'riv_height', 'riv_veloct', 'discharge', 'floodfrc',
    'volresv', 'qresv_in', 'qresv_out',
)
EXPERIMENTS = (
    ('E0_CTL', 'ctl', 0, False, False),
    ('E1_D',   'D',   1, False, False),
    ('E2_L',   'L',   0, True,  False),
    ('E3_B',   'B',   0, False, True),
    ('E4_DL',  'DL',  1, True,  False),
    ('E5_DB',  'DB',  1, False, True),
    ('E6_LB',  'LB',  0, True,  True),
    ('E7_ALL', 'DLB', 1, True,  True),
)

# Verified in the user's experiment outline; optional NetCDF check uses netCDF4.
NETWORK_VARS = (
    'seq_x', 'seq_y', 'seq_next', 'inpmat_x', 'inpmat_y',
    'inpmat_area', 'topo_rivelv', 'topo_rivhgt', 'topo_rivlen',
    'topo_rivman', 'topo_rivwth', 'topo_rivstomax', 'topo_area',
    'topo_fldhgt',
)
LEVEE_VARS = ('levee_frc', 'levee_hgt')
BIF_VARS = (
    'bifurcation_upst', 'bifurcation_down', 'bifurcation_distance',
    'bifurcation_elevation', 'bifurcation_width', 'bifurcation_manning',
)
DAM_VARS = (
    'dam_seq', 'dam_year', 'dam_GRAND_ID', 'dam_TotalVol_mcm',
    'dam_ConVol_mcm', 'dam_Qn', 'dam_Qf',
)

ASSIGNMENT = re.compile(r'^\s*(DEF_[\w%]+)\s*=\s*(.*?)\s*$', re.I)
FORTRAN_GROUP = re.compile(r'^\s*&nl_colm\s*(?:!.*)?$', re.I | re.M)
GROUP_END = re.compile(r'^\s*/\s*(?:!.*)?$', re.M)


def die(message: str) -> None:
    raise ValueError(message)


def quote(value: Path | str) -> str:
    text = str(value)
    if '\n' in text or '\r' in text:
        die('Namelist string values cannot contain newlines')
    if len(text) > 255:
        die(f'Path exceeds CoLM character(len=256): {text}')
    return "'" + text.replace("'", "''") + "'"


def logical(value: bool) -> str:
    return '.true.' if value else '.false.'


def extract(text: str, key: str) -> str | None:
    for line in text.splitlines():
        match = ASSIGNMENT.match(line)
        if match and match.group(1).lower() == key.lower():
            return match.group(2).split('!', 1)[0].strip()
    return None


def unquote(value: str | None) -> str:
    if value is None:
        return ''
    value = value.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"')
    return value


def patched(template: str, changes: dict[str, str]) -> str:
    """Upsert exact DEF_... assignments; avoid duplicate keys and preserve other lines."""
    if len(FORTRAN_GROUP.findall(template)) != 1 or len(GROUP_END.findall(template)) != 1:
        die('Template must contain exactly one &nl_colm group ending with /')
    names = {key.lower(): (key, value) for key, value in changes.items()}
    seen: set[str] = set()
    result: list[str] = []
    for line in template.splitlines():
        match = ASSIGNMENT.match(line)
        key = match.group(1).lower() if match else ''
        if key in names:
            if key not in seen:
                label, value = names[key]
                result.append(f'   {label} = {value}')
                seen.add(key)
            # Drop duplicate assignments to avoid inconsistent Fortran reads.
        else:
            result.append(line)
    missing = [f'   {label} = {value}' for key, (label, value)
               in names.items() if key not in seen]
    end_positions = [i for i, line in enumerate(result) if GROUP_END.match(line)]
    if len(end_positions) != 1:
        die('Template end marker was lost')
    result[end_positions[0]:end_positions[0]] = ['', '   ! AAS factorial settings', *missing, '']
    updated = '\n'.join(result).rstrip() + '\n'
    for key in changes:
        hits = sum(1 for line in updated.splitlines()
                   if (m := ASSIGNMENT.match(line)) and m.group(1).lower() == key.lower())
        if hits != 1:
            die(f'Expected exactly one assignment for {key}, found {hits}')
    return updated


def history_text(land_fields: tuple[str, ...]) -> str:
    fields = list(dict.fromkeys([*ROUTING_GATES, *land_fields]))
    return ('&nl_colm_history\n'
            '   ! All other land history fields default to .false.\n'
            '   ! River-lake routing fields are selected below.\n'
            + ''.join(f'   DEF_hist_vars%{name} = .true.\n' for name in fields)
            + '/\n')


def lsf_text(case_name: str, nml_path: Path, run_dir: Path, log_dir: Path,
             preprocess: bool, only_steps: tuple[str, ...] | None = None) -> str:
    """Render an LSF script following the reference run/*.lsf resource layout."""
    nml = shlex.quote(str(nml_path))
    run = shlex.quote(str(run_dir))
    logs = shlex.quote(str(log_dir))
    repo = shlex.quote(str(run_dir.parent))
    if only_steps is not None:
        steps = [(f'{step}.x', step) for step in only_steps]
    else:
        steps = []
        if preprocess:
            steps.extend((('mksrfdata.x', 'srf'), ('mkinidata.x', 'ini')))
        steps.append(('colm.x', 'colm'))
    commands = ''.join(
        f'run_step ./{executable} "$LOG_DIR/{case_name}_{suffix}_${{RUN_TAG}}.log"\n'
        for executable, suffix in steps
    )
    executable_checksums = ' '.join(
        f'"$RUN_DIR/{executable}"' for executable, _ in steps)
    return f'''#!/bin/bash
#BSUB -J {case_name}
#BSUB -q normal
#BSUB -o {log_dir}/{case_name}_lsf_%J.out
#BSUB -e {log_dir}/{case_name}_lsf_%J.err
#BSUB -n 240
#BSUB -R rusage[mem=150G]
#BSUB -R "span[ptile=48]"

set -euo pipefail

NML_FILE={nml}
RUN_DIR={run}
LOG_DIR={logs}
COLM_REPO={repo}
RUN_TAG="${{LSB_JOBID:-manual_$(date +%Y%m%dT%H%M%S)}}"
MPI_NP=240
MPI={shlex.quote(str(MPI_ROOT / 'bin' / 'mpirun'))}

source /share/home/dq089/soft/intel-env >/dev/null 2>&1
export OMP_NUM_THREADS=1
export HDF5_USE_FILE_LOCKING=FALSE
ulimit -s unlimited

mkdir -p "$LOG_DIR"

{{
    echo "case: {case_name}"
    echo "job id: ${{LSB_JOBID:-manual}}"
    echo "job start: $(date --iso-8601=seconds)"
    echo "host: $(hostname)"
    echo "mpirun: $MPI"
    echo "branch: $(git -C "$COLM_REPO" branch --show-current)"
    echo "commit: $(git -C "$COLM_REPO" rev-parse HEAD)"
    echo "git status:"
    git -C "$COLM_REPO" status --short
    echo "configuration checksum:"
    sha256sum "$NML_FILE"
    echo "executable checksums:"
    sha256sum {executable_checksums}
    echo "linked libraries:"
    ldd "$RUN_DIR/colm.x" | egrep 'libnetcdf|libnetcdff|libhdf5|libmpi|libmpifort|libiomp5|libifcore|not found' || true
}} > "$LOG_DIR/environment_${{RUN_TAG}}.txt"

cd "$RUN_DIR"

run_step() {{
    executable="$1"
    step_log="$2"
    echo "Starting $executable at $(date --iso-8601=seconds); log: $step_log"
    if "$MPI" -np "$MPI_NP" "$executable" "$NML_FILE" > "$step_log" 2>&1; then
        return 0
    else
        rc=$?
        echo "$executable failed with exit code $rc; see $step_log" >&2
        return "$rc"
    fi
}}

echo "Job {case_name} started at $(date --iso-8601=seconds) on $(hostname)"
{commands}echo "Job {case_name} finished at $(date --iso-8601=seconds)"
'''


def required_path(path: Path, kind: str, draft: bool) -> None:
    if not path.exists():
        if draft:
            print(f'WARNING [draft]: missing {kind}: {path}', file=sys.stderr)
        else:
            die(f'Missing {kind}: {path}')


def validate_forcing(path: Path, draft: bool) -> dict[str, object]:
    info: dict[str, object] = {'path': str(path), 'checked': False}
    if not path.is_file():
        required_path(path, 'forcing namelist', draft)
        return info
    text = path.read_text(encoding='utf-8')
    if not re.search(r'^\s*&nl_colm_forcing\b', text, re.I | re.M):
        die(f'Forcing file is not &nl_colm_forcing: {path}')
    ds = unquote(extract(text, 'DEF_forcing%dataset'))
    st = extract(text, 'DEF_forcing%startyr')
    en = extract(text, 'DEF_forcing%endyr')
    sm = extract(text, 'DEF_forcing%startmo')
    em = extract(text, 'DEF_forcing%endmo')
    if not st or not en:
        die('Forcing namelist is missing DEF_forcing%startyr / endyr')
    try:
        start_year, end_year = int(st), int(en)
        start_month = int(sm) if sm else 1
        end_month = int(em) if em else 12
    except ValueError as exc:
        die(f'Cannot parse forcing time coverage: {exc}')
    info.update(dataset=ds, first=f'{start_year:04d}-{start_month:02d}',
                last=f'{end_year:04d}-{end_month:02d}')
    failures = []
    if ds.upper() != 'JRA3Q':
        failures.append(f'dataset is {ds!r}, expected JRA3Q')
    if (start_year, start_month) > (1996, 1):
        failures.append(f'forcing begins {start_year}-{start_month:02d}, after 1996-01')
    if (end_year, end_month) < (2005, 12):
        failures.append(f'forcing ends {end_year}-{end_month:02d}, before 2005-12')
    if failures:
        message = '; '.join(failures)
        if draft:
            print(f'WARNING [draft]: {message}', file=sys.stderr)
        else:
            die(message + '. Check ACTUAL JRA3Q data, then supply a correct --forcing-nml.')
    else:
        info['checked'] = True
    # Metadata checks cannot prove the underlying forcing NetCDF files exist.
    return info


def check_macros(repo: Path) -> list[str]:
    path = repo / DEFINE_FILE
    if not path.is_file():
        return [f'Cannot inspect build macros: {path}']
    try:state=preprocessor_state(path)
    except WorkflowError as exc:die(str(exc))
    desired = {
        'GRIDBASED': True, 'GridRiverLakeFlow': True, 'USEMPI': True,
        'LULC_IGBP_PFT': True, 'CaMa_Flood': False, 'BGC': False,
        'CROP': False, 'URBAN_MODEL': False, 'LULCC': False,
        'MaterialTransport': False,
    }
    warnings = [f'{key}: define.h={state.get(key, "unknown")}, study requires {needed}'
                for key, needed in desired.items() if state.get(key) is not needed]
    warnings.append('Macros describe the SOURCE tree, not necessarily the already-built executable; '
                    'rebuild after changing include/define.h.')
    return warnings


def check_netcdf(routing_file: Path, reservoir_file: Path) -> None:
    try:
        from netCDF4 import Dataset
    except ImportError as exc:
        die('For --check-netcdf, install netCDF4 (e.g. pip install netCDF4)')
    with Dataset(routing_file, 'r') as ds:
        names = set(ds.variables)
        missing = sorted(set(NETWORK_VARS + LEVEE_VARS + BIF_VARS) - names)
        if missing:
            die(f'Routing NetCDF missing required fields: {", ".join(missing)}')
    with Dataset(reservoir_file, 'r') as ds:
        names = set(ds.variables)
        missing = sorted(set(DAM_VARS) - names)
        if missing:
            die(f'Reservoir NetCDF missing required fields: {", ".join(missing)}')


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_command(command:list[str])->None:
    result=subprocess.run(command,text=True,capture_output=True,check=False)
    if result.returncode:
        raise ValueError(result.stderr or result.stdout or 'command failed')


def regenerate_experiment(root:Path,overwrite:bool)->int:
    root=root.resolve();manifest_path=root/'manifest.yaml'
    colm_dir=root/'config/colm';jobs_dir=root/'config/jobs'
    if not overwrite:
        die('--experiment-dir requires --overwrite; only config/colm and config/jobs are replaced')
    if not manifest_path.is_file() or not (colm_dir/'run_manifest.json').is_file():
        die(f'Not a generated experiment directory: {root}')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    old=json.loads((colm_dir/'run_manifest.json').read_text(encoding='utf-8'))
    markers=list((jobs_dir/'markers').glob('*')) if (jobs_dir/'markers').is_dir() else []
    case_root=Path(old['paths']['case_root'])
    runs=[*old['runs']['spinup'].values(),*old['runs']['dam_init'].values()]
    for values in old['runs']['experiments'].values():runs.extend(values)
    products=[]
    for run in runs:
        case=case_root/run['case']
        for dirname in ('history','restart','restarts'):
            path=case/dirname
            if path.is_dir():
                products.extend(x for x in path.rglob('*') if x.is_file() or x.is_symlink())
    if markers or products:
        sample=[*(str(x) for x in markers[:3]),*(str(x) for x in products[:3])]
        die('Refusing to overwrite CoLM configuration after run products exist: '+', '.join(sample))
    region=manifest['region'];resources=old.get('resources',{})
    repo=Path(old['paths']['repo_root']);forcing=old['forcing']['dataset']
    staging=Path(tempfile.mkdtemp(prefix='.colm-regenerate.',dir=str(root)))
    staged_colm=staging/'colm';staged_jobs=staging/'jobs'
    final_log=root/'logs'
    command=[sys.executable,str(Path(__file__).resolve()),
        '--repo-root',str(repo),'--out-dir',str(staged_colm),'--case-root',str(case_root),
        '--forcing-nml',forcing,'--region',region,'--study-id',manifest['experiment_id'],
        '--study-slug',manifest['slug'],'--rawdata',old['paths']['rawdata'],
        '--runtime',old['paths']['runtime'],'--soil-init',old['paths']['soil_init'],
        '--log-dir',str(final_log),'--no-lsf','--include-dam-init',
        '--eval-start-year',str(old['evaluation']['start_year']),
        '--eval-end-year',str(old['evaluation']['end_year'])]
    # Regeneration uses the saved effective profile, never a mutable server JSON.
    site=old.get('site_config',{})
    snapshot=None
    if site.get('path'):
        snapshot=staging/'site_config.json'
        snapshot.write_text(json.dumps({k:v for k,v in site['effective'].items()
                                        if k in KEYS and (v is not None or k in {'mpi_root','intel_env','node'})})+'\n')
        command.extend(['--site-config',str(snapshot)])
    if old.get('base_template'):command.extend(['--template',old['base_template']])
    if old.get('draft'):command.append('--draft')
    try:
        run_command(command)
        scheduler=resources.get('scheduler','lsf')
        jobs=[sys.executable,str(Path(__file__).resolve().parent/'dlb_03_generate_jobs.py'),
            '--nml-dir',str(staged_colm),'--scheduler',scheduler,
            '--exe',str(repo/'run/colm.x'),'--out-dir',str(staged_jobs),
            '--log-dir',str(final_log),'--queue',resources.get('queue','normal'),
            '--cores',str(resources.get('cores',240)),
            '--tasks-per-node',str(resources.get('tasks_per_node',48)),
            '--memory',resources.get('memory','150G'),
            '--walltime',resources.get('walltime','24:00:00')]
        if snapshot:jobs.extend(['--site-config',str(snapshot)])
        for key in ('launcher','node'):
            if resources.get(key):jobs.extend(['--'+key,str(resources[key])])
        jobs.append('--exclusive' if resources.get('exclusive',False) else '--no-exclusive')
        if resources.get('mpi_root'):jobs.extend(['--mpi-root',resources['mpi_root']])
        if resources.get('intel_env'):jobs.extend(['--intel-env',resources['intel_env']])
        if old.get('draft'):jobs.append('--allow-draft')
        run_command(jobs)
        new_manifest=json.loads((staged_colm/'run_manifest.json').read_text(encoding='utf-8'))
        new_manifest['resources']=resources
        new_manifest['site_config']=site
        if snapshot:
            job_metadata=json.loads((staged_jobs/'site_config.json').read_text())
            job_metadata.update(path=site['path'],sha256=site['sha256'])
            (staged_jobs/'site_config.json').write_text(json.dumps(job_metadata,indent=2)+'\n')
            snapshot.unlink()
        (staged_colm/'run_manifest.json').write_text(
            json.dumps(new_manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        replacements=((str(staged_colm),str(colm_dir)),(str(staged_jobs),str(jobs_dir)),
                      (str(staging),str(root)))
        for path in staging.rglob('*'):
            if path.is_file():
                text=path.read_text(encoding='utf-8')
                for source,target in replacements:text=text.replace(source,target)
                path.write_text(text,encoding='utf-8')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup=root/'provenance/backups'/stamp;backup.mkdir(parents=True)
        shutil.copytree(colm_dir,backup/'colm')
        shutil.copytree(jobs_dir,backup/'jobs')
        shutil.copy2(manifest_path,backup/'manifest.yaml')
        old_colm=colm_dir.with_name(f'.colm.old.{os.getpid()}')
        old_jobs=jobs_dir.with_name(f'.jobs.old.{os.getpid()}')
        moved_colm=False;moved_jobs=False
        try:
            os.rename(colm_dir,old_colm);moved_colm=True
            os.rename(jobs_dir,old_jobs);moved_jobs=True
            os.rename(staged_colm,colm_dir);os.rename(staged_jobs,jobs_dir)
            manifest['colm_regenerated_at']=datetime.now(timezone.utc).isoformat()
            temporary=manifest_path.with_name(f'.manifest.tmp.{os.getpid()}')
            temporary.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
            os.replace(temporary,manifest_path)
        except Exception:
            if old_colm.exists() and colm_dir.exists():shutil.rmtree(colm_dir)
            if old_jobs.exists() and jobs_dir.exists():shutil.rmtree(jobs_dir)
            if moved_colm and old_colm.exists():os.rename(old_colm,colm_dir)
            if moved_jobs and old_jobs.exists():os.rename(old_jobs,jobs_dir)
            raise
        shutil.rmtree(old_colm);shutil.rmtree(old_jobs)
        run_command([sys.executable,str(Path(__file__).resolve().parent/'dlb_07_refresh_experiment.py'),
                     str(root)])
    finally:
        shutil.rmtree(staging,ignore_errors=True)
    print(f'Regenerated CoLM namelists and jobs at {root}')
    print(f'Previous configuration backed up at {backup}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--experiment-dir',type=Path,
                        help='Regenerate config/colm and config/jobs for an existing experiment')
    parser.add_argument('--repo-root', type=Path,
                        help='Local checkout of zhongwangwei/CoLM202X')
    parser.add_argument('--out-dir', type=Path,
                        help='Only this directory will be written')
    parser.add_argument('--case-root', type=Path,
                        help='DEF_dir_output: model will create subdirectories per case')
    parser.add_argument('--study-id',
                        help='Optional EXPnnn identifier used as a case-name prefix')
    parser.add_argument('--study-slug',
                        help='Optional short study slug (requires --study-id)')
    parser.add_argument('--rawdata', type=Path, default=RAWDATA)
    parser.add_argument('--runtime', type=Path, default=RUNTIME)
    parser.add_argument('--soil-init', type=Path, default=SOIL_INIT)
    parser.add_argument('--log-dir', type=Path,
                        help='LSF log directory (default: OUT_DIR/logs)')
    parser.add_argument('--no-lsf', action='store_true',
                        help='Generate configuration only; dlb_03_generate_jobs.py owns jobs')
    parser.add_argument('--lsf',dest='no_lsf',action='store_false',help='Explicitly enable legacy LSF output')
    parser.add_argument('--include-dam-init', action='store_true',
                        help='Also generate the dedicated 2000 D_INIT namelist')
    parser.add_argument('--eval-start-year', type=int, default=2001)
    parser.add_argument('--eval-end-year', type=int, default=2005)
    parser.add_argument('--forcing-nml', metavar='DATASET',
                        help='Dataset name resolved as REPO_ROOT/run/forcing/DATASET.nml')
    parser.add_argument('--region', choices=REGIONS,
                        help='Generate one domain (default: USA, SA, Mekong, and Global)')
    parser.add_argument('--template', type=Path,
                        help='Full main namelist; default is repo run/examples/Amazon_..._RiverLake.nml')
    parser.add_argument('--draft', action='store_true',
                        help='Generate non-runnable draft even if inputs or forcing years are absent')
    parser.add_argument('--check-netcdf', action='store_true',
                        help='Validate essential reservoir/levee/bifurcation NetCDF variables')
    parser.add_argument('--strict-build', action='store_true',
                        help='Also fail if checked-in define.h macros disagree with study')
    parser.add_argument('--overwrite', action='store_true',
                        help='Overwrite only generated names, never delete unrelated files')
    parser.add_argument('--forcing-root',type=Path)
    args = parse_site_args(parser,recorded_options=('--experiment-dir',))

    if args.experiment_dir:
        if args.site_config:die('--experiment-dir uses its recorded server profile; omit --site-config')
        forbidden=(args.repo_root,args.out_dir,args.case_root,args.forcing_nml,args.study_id,args.study_slug)
        if any(x is not None for x in forbidden):
            die('--experiment-dir cannot be combined with flat-generation path/study options')
        return regenerate_experiment(args.experiment_dir,args.overwrite)
    missing=[name for name,value in (('--repo-root',args.repo_root),('--out-dir',args.out_dir),
                                      ('--case-root',args.case_root),('--forcing-nml',args.forcing_nml))
             if value is None]
    if missing:die('Flat generation requires '+', '.join(missing))

    repo = args.repo_root.resolve()
    out = args.out_dir.resolve()
    case_root = args.case_root.resolve()
    rawdata = args.rawdata.resolve()
    runtime = args.runtime.resolve()
    soil = args.soil_init.resolve()
    if bool(args.study_id) != bool(args.study_slug):
        die('--study-id and --study-slug must be supplied together')
    if args.study_id and not re.fullmatch(r'EXP\d{3,}', args.study_id):
        die('--study-id must match EXPnnn (at least three digits)')
    if args.study_slug and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.study_slug):
        die('--study-slug must contain only letters, digits, underscores and hyphens')
    if not (2000 <= args.eval_start_year <= args.eval_end_year <= 2005):
        die('Evaluation years must satisfy 2000 <= start <= end <= 2005')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.forcing_nml):
        die('--forcing-nml must be a dataset name such as JRA3Q (without path or .nml)')
    forcing = (repo / 'run' / 'forcing' / f'{args.forcing_nml}.nml').resolve()
    log_dir = (args.log_dir or (out / 'logs')).resolve()
    selected_regions = [args.region] if args.region else list(REGIONS)
    routing_files = {
        name: runtime / 'unitcatchment' / f"grid_routing_data_{REGIONS[name]['routing']}.nc"
        for name in selected_regions
    }
    template = (args.template or (repo / BASE_TEMPLATE)).resolve()

    if out == repo or out == case_root or out == Path('/'):
        die('Refusing to use repository root, case root or / as output directory')

    required_path(template, 'model namelist template', False)
    required_path(repo / 'share/MOD_Namelist.F90', 'model source code', False)
    for path, name in ((rawdata, 'rawdata directory'), (runtime, 'runtime directory'),
                       (soil, 'soil initialization file'),
                       (case_root, 'case root directory')):
        required_path(path, name, args.draft)
    for routing in dict.fromkeys(routing_files.values()):
        required_path(routing, 'routing/reservoir NetCDF', args.draft)
    info = validate_forcing(forcing, args.draft)
    if args.check_netcdf:
        if args.draft:
            die('--check-netcdf cannot be combined with --draft')
        for routing in dict.fromkeys(routing_files.values()):
            check_netcdf(routing, routing)

    macro_warnings = check_macros(repo)
    for w in macro_warnings:
        print(f'NOTE [build]: {w}', file=sys.stderr)
    actual_mismatches = [w for w in macro_warnings if w.startswith(('GRIDBASED:', 'GridRiverLakeFlow:',
        'USEMPI:', 'LULC_IGBP_PFT:', 'CaMa_Flood:', 'BGC:', 'CROP:', 'URBAN_MODEL:', 'LULCC:',
        'MaterialTransport:'))]
    if args.strict_build and actual_mismatches:
        die('Build macro checks failed; inspect include/define.h, then make clean and rebuild')

    validation_issues = input_issues(args,selected_regions) if args.site_config else []
    original = template.read_text(encoding='utf-8')
    if not FORTRAN_GROUP.search(original):
        die('The model template is not a &nl_colm namelist')

    texts = {
        'history_spinup.nml': '&nl_colm_history\n   ! Spinup uses DEF_HIST_vars_out_default=.true.\n/\n',
        'history_CTL.nml': '&nl_colm_history\n   ! CTL uses DEF_HIST_vars_out_default=.true.\n/\n',
        'history_riverlake.nml': history_text(()),
    }
    rows = []
    spinup_filenames = []
    region_manifest = {}
    run_manifest = {'spinup': {}, 'dam_init': {}, 'experiments': {}}
    for region_name in selected_regions:
        config = REGIONS[region_name]
        edges, edgen, edgew, edgee = config['bounds']
        land_res = config['land_res']
        history_res = config['history_res']
        resolution_label = config['label']
        routing = routing_files[region_name]
        regional = logical(config['regional'])

        common = {
            'DEF_domain%edges': f'{edges:g}',
            'DEF_domain%edgen': f'{edgen:g}',
            'DEF_domain%edgew': f'{edgew:g}',
            'DEF_domain%edgee': f'{edgee:g}',
            'DEF_simulation_time%greenwich': '.true.',
            'DEF_simulation_time%start_month': '1',
            'DEF_simulation_time%start_day': '1',
            'DEF_simulation_time%start_sec': '0',
            'DEF_simulation_time%end_month': '12',
            'DEF_simulation_time%end_day': '31',
            'DEF_simulation_time%end_sec': '86400',
            'DEF_simulation_time%spinup_month': '1',
            'DEF_simulation_time%spinup_day': '1',
            'DEF_simulation_time%spinup_sec': '0',
            'DEF_simulation_time%spinup_repeat': '0',
            'DEF_simulation_time%timestep': '1800.',
            'DEF_dir_rawdata': quote(rawdata),
            'DEF_dir_runtime': quote(runtime),
            'DEF_dir_output': quote(case_root),
            'DEF_GRIDBASED_lon_res': f'{land_res:g}',
            'DEF_GRIDBASED_lat_res': f'{land_res:g}',
            'DEF_UnitCatchment_file': quote(routing),
            'DEF_UnitCatchment_regional': regional,
            'DEF_ReservoirPara_file': quote(routing),
            'DEF_GRIDBASED_ROUTING_MAX_DT': '3600.',
            'DEF_GridRiverLake_FloodFeedback': '.false.',
            'DEF_Runoff_SCHEME': '3',
            'DEF_USE_VariablySaturatedFlow': '.true.',
            'DEF_USE_Dynamic_Lake': '.true.',
            'DEF_USE_SoilInit': '.true.',
            'DEF_file_SoilInit': quote(soil),
            'DEF_LAI_MONTHLY': '.true.',
            'DEF_LAI_CHANGE_YEARLY': '.false.',
            'DEF_forcing_namelist': quote(forcing),
            'DEF_HIST_lon_res': f'{history_res:g}',
            'DEF_HIST_lat_res': f'{history_res:g}',
            'DEF_WRST_FREQ': "'MONTHLY'",
            'DEF_HIST_groupby': "'MONTH'",
            'DEF_HIST_mode': "'one'",
            'DEF_HIST_WriteBack': '.false.',
            'DEF_REST_CompressLevel': '1',
            'DEF_HIST_CompressLevel': '4',
            'DEF_HIST_vars_out_default': '.false.',
        }

        case_prefix = (f'{args.study_id}_{args.study_slug}_{region_name}'
                       if args.study_id else region_name)
        spinup_case = f'{case_prefix}_spinup_{resolution_label}'
        shared = dict(common)
        shared.update({
            'DEF_CASE_NAME': quote(spinup_case),
            'DEF_simulation_time%start_year': '1996',
            'DEF_simulation_time%end_year': '1999',
            'DEF_simulation_time%spinup_year': '1996',
            'DEF_HIST_FREQ': "'DAILY'",
            'DEF_HIST_vars_namelist': quote(out / 'history_spinup.nml'),
            'DEF_HIST_vars_out_default': '.true.',
            'DEF_Reservoir_Method': '0',
            'DEF_USE_LEVEE': '.false.',
            'DEF_USE_BIFURCATION': '.false.',
        })
        spinup_filename = f'{spinup_case}.nml'
        spinup_filenames.append(spinup_filename)
        texts[spinup_filename] = patched(original, shared)
        if not args.no_lsf:
            texts[f'{spinup_case}.lsf'] = lsf_text(
                spinup_case, out / spinup_filename, repo / 'run', log_dir, True)
        run_manifest['spinup'][region_name] = {
            'label': 'spinup', 'case': spinup_case, 'nml_file': spinup_filename,
            'history_file': 'history_spinup.nml',
            'steps': ['mksrfdata', 'mkinidata', 'colm'],
            'period': {'start_year': 1996, 'end_year': 1999},
        }

        dam_init_filename = None
        if args.include_dam_init:
            dam_init_case = f'{case_prefix}_D_INIT_{resolution_label}'
            dam_init_settings = dict(common)
            dam_init_settings.update({
                'DEF_CASE_NAME': quote(dam_init_case),
                'DEF_simulation_time%start_year': '2000',
                'DEF_simulation_time%end_year': '2000',
                'DEF_simulation_time%spinup_year': '2000',
                'DEF_HIST_FREQ': "'DAILY'",
                'DEF_HIST_vars_namelist': quote(out / 'history_riverlake.nml'),
                'DEF_HIST_vars_out_default': '.false.',
                'DEF_Reservoir_Method': '1',
                'DEF_USE_LEVEE': '.false.',
                'DEF_USE_BIFURCATION': '.false.',
            })
            dam_init_filename = f'{dam_init_case}.nml'
            texts[dam_init_filename] = patched(original, dam_init_settings)
            if not args.no_lsf:
                texts[f'{dam_init_case}.lsf'] = lsf_text(
                    dam_init_case, out / dam_init_filename, repo / 'run', log_dir, False,
                    ('mkinidata',))
            run_manifest['dam_init'][region_name] = {
                'label': 'D_INIT', 'case': dam_init_case,
                'nml_file': dam_init_filename,
                'history_file': 'history_riverlake.nml',
                'steps': ['mkinidata'],
                'period': {'start_year': 2000, 'end_year': 2000},
                'landdata_source': spinup_case,
            }

        fingerprints = []
        experiment_filenames = []
        for label, name_part, dam, levee, bif in EXPERIMENTS:
            is_ctl = label == 'E0_CTL'
            history = 'history_CTL.nml' if is_ctl else 'history_riverlake.nml'
            case_name = f'{case_prefix}_{name_part}_{resolution_label}'
            settings = dict(common)
            settings.update({
                'DEF_CASE_NAME': quote(case_name),
                'DEF_simulation_time%start_year': '2000',
                'DEF_simulation_time%end_year': '2005',
                'DEF_simulation_time%spinup_year': '2000',
                'DEF_HIST_FREQ': "'DAILY'",
                'DEF_HIST_vars_namelist': quote(out / history),
                'DEF_HIST_vars_out_default': logical(is_ctl),
                'DEF_Reservoir_Method': str(dam),
                'DEF_USE_LEVEE': logical(levee),
                'DEF_USE_BIFURCATION': logical(bif),
            })
            filename = f'{case_name}.nml'
            experiment_filenames.append(filename)
            texts[filename] = patched(original, settings)
            lsf_filename = f'{case_name}.lsf'
            if not args.no_lsf:
                texts[lsf_filename] = lsf_text(
                    case_name, out / filename, repo / 'run', log_dir, is_ctl)
            invariant = {k.lower(): v for k, v in settings.items()
                         if k.lower() not in {'def_case_name', 'def_hist_vars_namelist',
                                              'def_hist_vars_out_default',
                                              'def_reservoir_method', 'def_use_levee',
                                              'def_use_bifurcation'}}
            fingerprints.append(invariant)
            rows.append({'region': region_name, 'experiment': label,
                         'case_name': case_name, 'dam': dam,
                         'levee': logical(levee), 'bif': logical(bif),
                         'from': '2000-01-01', 'through': '2005-12-31',
                         'history': history, 'nml_file': filename,
                         'lsf_file': '' if args.no_lsf else lsf_filename})
            run_manifest['experiments'].setdefault(region_name, []).append({
                'label': label, 'case': case_name, 'nml_file': filename,
                'history_file': history, 'steps': ['colm'],
                'switches': {'reservoir_method': dam, 'levee': levee,
                             'bifurcation': bif},
                'period': {'start_year': 2000, 'end_year': 2005},
                'landdata_source': spinup_case,
                'restart_sources': {
                    'default': spinup_case,
                    'gridriver': (run_manifest['dam_init'].get(region_name, {})
                                  .get('case', spinup_case) if dam else spinup_case),
                },
            })
        if any(f != fingerprints[0] for f in fingerprints[1:]):
            die(f'Inconsistent settings among {region_name} experiments')

        for name in [spinup_filename, *([dam_init_filename] if dam_init_filename else []),
                     *experiment_filenames]:
            expected = {
                'DEF_domain%edges': f'{edges:g}',
                'DEF_domain%edgen': f'{edgen:g}',
                'DEF_domain%edgew': f'{edgew:g}',
                'DEF_domain%edgee': f'{edgee:g}',
                'DEF_GRIDBASED_lon_res': f'{land_res:g}',
                'DEF_GRIDBASED_lat_res': f'{land_res:g}',
                'DEF_HIST_lon_res': f'{history_res:g}',
                'DEF_HIST_lat_res': f'{history_res:g}',
                'DEF_UnitCatchment_regional': regional,
                'DEF_GridRiverLake_FloodFeedback': '.false.',
                'DEF_GRIDBASED_ROUTING_MAX_DT': '3600.',
            }
            for key, value in expected.items():
                if extract(texts[name], key) != value:
                    die(f'Internal validation failed: {name}: {key}')
        ctl_filename = experiment_filenames[0]
        if extract(texts[spinup_filename], 'DEF_HIST_vars_out_default') != '.true.':
            die(f'Internal validation failed: {region_name} spinup history variables')
        if extract(texts[ctl_filename], 'DEF_HIST_vars_out_default') != '.true.':
            die(f'Internal validation failed: {region_name} CTL history variables')
        for name in experiment_filenames[1:]:
            if extract(texts[name], 'DEF_HIST_vars_out_default') != '.false.':
                die(f'Internal validation failed: {name} history defaults')
            expected_history = str(out / 'history_riverlake.nml')
            if unquote(extract(texts[name], 'DEF_HIST_vars_namelist')) != expected_history:
                die(f'Internal validation failed: {name} must use history_riverlake.nml')

        region_manifest[region_name] = {
            'domain': {'south': edges, 'north': edgen, 'west': edgew, 'east': edgee},
            'unitcatchment_regional': bool(config['regional']),
            'land_resolution_degrees': land_res,
            'routing_grid': str(routing),
            'routing_resolution': config['routing'],
            'history_resolution_degrees': history_res,
        }

    if not info.get('checked'):
        validation_issues.append(f'Forcing metadata unverified: {forcing}')
    if args.site_config:
        validation_issues.extend(unsupported_fields(repo,list(texts.values())))
        if not args.draft and validation_issues:
            die('Input/source compatibility failed:\n'+'\n'.join(validation_issues))
    from io import StringIO
    csv_buffer = StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    texts['experiments.csv'] = csv_buffer.getvalue()
    meta = {
        'schema_version': 2,
        'study': 'AAS CoLM 2^3: reservoir x levee x bifurcation',
        'study_id': args.study_id,
        'study_slug': args.study_slug,
        'source_repository': 'https://github.com/zhongwangwei/CoLM202X',
        'reviewed_upstream_commit': 'c545a96c0aa94f5323861bd3ad7929b7dcfd27f2',
        'base_template': str(template),
        'base_template_sha256': file_hash(template),
        'forcing': info,
        'draft': bool(args.draft),
        'site_config': args.site_metadata,
        'validation_issues': validation_issues,
        'regions': region_manifest,
        'common_spinup': '1996-01-01 to 1999-12-31',
        'experiment_interval': '2000-01-01 to 2005-12-31',
        'evaluation': {'start_year': args.eval_start_year,
                       'end_year': args.eval_end_year},
        'evaluation_interval': (f'{args.eval_start_year}-01-01 to '
                                f'{args.eval_end_year}-12-31'),
        'paths': {'repo_root': str(repo), 'case_root': str(case_root),
                  'rawdata': str(rawdata), 'runtime': str(runtime),
                  'soil_init': str(soil), 'log_dir': str(log_dir)},
        'runs': run_manifest,
        'restart_hand_off': 'Copy/rename 1999-12-31 restart from SPINUP into EACH experiment case; not automated here.',
        'repo_source_macro_notes': macro_warnings,
        'hist': 'CTL enables all history variables; other experiments use history_riverlake.nml',
    }
    texts['run_manifest.json'] = json.dumps(meta, indent=2, ensure_ascii=False) + '\n'

    if out.exists() and not out.is_dir():
        die(f'Output exists but is not a directory: {out}')
    exists = [out / name for name in texts if (out / name).exists()]
    if exists and not args.overwrite:
        die('Output files already exist (use a fresh --out-dir or --overwrite): '
            + ', '.join(str(p) for p in exists))
    # All source/input checks and render validations have passed. Now write.
    out.mkdir(parents=True, exist_ok=True)
    if not args.no_lsf and log_dir == out / 'logs':
        log_dir.mkdir(exist_ok=True)
    for name, contents in texts.items():
        dest = out / name
        dest.write_text(contents, encoding='utf-8')
        if dest.suffix == '.lsf':
            dest.chmod(dest.stat().st_mode | 0o100)
            syntax = subprocess.run(
                ['bash', '-n', str(dest)], capture_output=True, text=True, check=False)
            if syntax.returncode:
                detail = syntax.stderr.strip() or syntax.stdout.strip()
                die(f'Generated LSF syntax check failed for {dest}: {detail}')

    run_count = len(selected_regions) * (len(EXPERIMENTS) + 1 + int(args.include_dam_init))
    lsf_count = 0 if args.no_lsf else run_count
    print(f'Generated {run_count} running namelists, {lsf_count} LSF scripts '
          f'and 3 history namelists in {out}')
    print('Domains        : ' + ', '.join(selected_regions))
    print('Common spinups : ' + ', '.join(spinup_filenames))
    print(f'Output cases   : {case_root}/<region>_<experiment>_<land-resolution>')
    print(f'Forcing check : {"PASS" if info.get("checked") else "DRAFT/UNVERIFIED"}')
    print('No CoLM run, job submission, restart copy or GitHub modification occurred.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, UnicodeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
