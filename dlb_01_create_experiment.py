#!/usr/bin/env python3
"""Create one atomic, configuration-only DLB regional experiment suite."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime,timezone
from pathlib import Path

from dlb_site_config import parse_site_args,basin_specs,input_issues
from dlb_08_generate_openbench import BASINS,MASK_ROOT

DEFAULT_ROOT=Path('/share/home/dq134/wzx/CoLM/DLB_nml')
DEFAULT_REPO_ROOT=Path('/share/home/dq134/wzx/CoLM/CoLM202X_1030')
DEFAULT_CASE_ROOT=Path('/share/home/dq134/wzx/CoLM/cases')
DEFAULT_RAW=Path('/share/home/dq013/zhwei/colm/data/CoLMrawdata')
DEFAULT_RUNTIME=Path('/share/home/dq013/zhwei/colm/data/CoLMruntime')
DEFAULT_SOIL=Path('/share/home/dq013/zhwei/colm/data/soilstate/soilstate.nc')
DEFAULT_MPI=Path('/share/home/dq089/soft/mpich-4.2.3-intel')
DEFAULT_INTEL=Path('/share/home/dq089/soft/intel-env')
REGIONS=('USA','SA','Mekong','Global')


def fail(message:str)->None:raise ValueError(message)


def run_checked(command:list[str])->None:
    result=subprocess.run(command,text=True,capture_output=True,check=False)
    if result.returncode:
        raise ValueError(f'Command failed ({result.returncode}): {" ".join(command)}\n{result.stderr or result.stdout}')


def git_value(repo:Path,*arguments:str)->str|None:
    result=subprocess.run(['git','-C',str(repo),*arguments],text=True,capture_output=True)
    return result.stdout.strip() if result.returncode==0 else None


def render_static_templates(template_root:Path,target_root:Path,values:dict[str,str])->None:
    for source in template_root.rglob('*.template'):
        relative=source.relative_to(template_root)
        destination=target_root/relative.with_suffix('')
        text=source.read_text(encoding='utf-8')
        for key,value in values.items():text=text.replace(f'__{key}__',value)
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(text,encoding='utf-8')


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('experiment_id');p.add_argument('slug');p.add_argument('region',choices=REGIONS)
    p.add_argument('--repo-root',type=Path,default=DEFAULT_REPO_ROOT,
        help=f'CoLM checkout (default: {DEFAULT_REPO_ROOT})')
    p.add_argument('--case-root',type=Path,default=DEFAULT_CASE_ROOT,
        help=f'CoLM case root (default: {DEFAULT_CASE_ROOT})')
    p.add_argument('--forcing-nml',default='JRA3Q')
    p.add_argument('--eval-start-year',type=int,default=2001);p.add_argument('--eval-end-year',type=int,default=2005)
    p.add_argument('--rawdata',type=Path,default=DEFAULT_RAW);p.add_argument('--runtime',type=Path,default=DEFAULT_RUNTIME)
    p.add_argument('--soil-init',type=Path,default=DEFAULT_SOIL);p.add_argument('--mpi-root',type=Path,default=DEFAULT_MPI)
    p.add_argument('--intel-env',type=Path,default=DEFAULT_INTEL)
    p.add_argument('--queue',default='normal');p.add_argument('--cores',type=int,default=240)
    p.add_argument('--tasks-per-node',type=int,default=48);p.add_argument('--memory',default='150G')
    p.add_argument('--walltime',default='24:00:00')
    p.add_argument('--mask-root',type=Path,default=MASK_ROOT)
    p.add_argument('--forcing-root',type=Path)
    p.add_argument('--template',type=Path)
    p.add_argument('--scheduler',choices=['lsf','slurm'],default='lsf')
    p.add_argument('--launcher',choices=['mpirun','srun','yhrun'])
    p.add_argument('--exclusive',action=argparse.BooleanOptionalAction,default=False)
    p.add_argument('--node')
    location=p.add_mutually_exclusive_group()
    location.add_argument('--output-dir',type=Path,
        help='Exact final experiment directory (default: EXPERIMENTS_ROOT/EXPnnn_slug_REGION)')
    location.add_argument('--experiments-root',type=Path,default=DEFAULT_ROOT,
        help=f'Parent directory for generated experiments (default: {DEFAULT_ROOT})')
    p.add_argument('--draft',action='store_true',help=argparse.SUPPRESS)
    args=parse_site_args(p)
    if not re.fullmatch(r'EXP\d{3,}',args.experiment_id):fail('experiment_id must match EXPnnn')
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*',args.slug):fail('slug must be lowercase letters/digits/._-')
    if not 2000<=args.eval_start_year<=args.eval_end_year<=2005:fail('evaluation years must be within 2000–2005')
    repo=args.repo_root.resolve();case_root=args.case_root.resolve()
    name=f'{args.experiment_id}_{args.slug}_{args.region}'
    target=(args.output_dir.resolve() if args.output_dir else
            args.experiments_root.resolve()/name)
    if target==Path('/'):
        fail('refusing to use / as --output-dir')
    root=target.parent
    if target.exists() or target.is_symlink():fail(f'experiment already exists: {target}')
    resolution='0p5' if args.region=='Global' else '0p1'
    case_prefix=name
    cases=[f'{case_prefix}_spinup_{resolution}',f'{case_prefix}_D_INIT_{resolution}']
    cases.extend(f'{case_prefix}_{suffix}_{resolution}' for suffix in ('ctl','D','L','B','DL','DB','LB','DLB'))
    collisions=[case_root/case for case in cases if (case_root/case).exists() or (case_root/case).is_symlink()]
    if collisions:fail('case already exists: '+', '.join(map(str,collisions)))
    basins=basin_specs(BASINS,args.region,args.mask_root,MASK_ROOT)
    issues=input_issues(args,[args.region],[basins]) if args.site_config else []
    for mask in (x[3] for x in basins):
        if mask and not mask.is_file() and not any(str(mask) in x for x in issues):
            issues.append(f'missing verified basin mask: {mask}')
    if issues and not args.draft:fail('Input validation failed:\n'+'\n'.join(issues))
    root.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix=f'.{name}.',dir=str(root)))
    script_dir=Path(__file__).resolve().parent
    try:
        for rel in ('config/colm','config/jobs','logs','provenance','summary','openbench_output'):
            (staging/rel).mkdir(parents=True)
        generator=[sys.executable,str(script_dir/'dlb_02_generate_namelists.py'),
            '--repo-root',str(repo),'--out-dir',str(staging/'config/colm'),'--case-root',str(case_root),
            '--forcing-nml',args.forcing_nml,'--region',args.region,
            '--study-id',args.experiment_id,'--study-slug',args.slug,
            '--rawdata',str(args.rawdata),'--runtime',str(args.runtime),'--soil-init',str(args.soil_init),
            '--log-dir',str(staging/'logs'),'--no-lsf','--include-dam-init',
            '--eval-start-year',str(args.eval_start_year),'--eval-end-year',str(args.eval_end_year)]
        if args.site_config:generator.extend(['--site-config',str(args.site_config.absolute())])
        if args.template:generator.extend(['--template',str(args.template)])
        if args.forcing_root:generator.extend(['--forcing-root',str(args.forcing_root)])
        if args.draft:generator.append('--draft')
        run_checked(generator)
        jobs=[sys.executable,str(script_dir/'dlb_03_generate_jobs.py'),
            '--nml-dir',str(staging/'config/colm'),'--scheduler',args.scheduler,
            '--exe',str(repo/'run/colm.x'),'--out-dir',str(staging/'config/jobs'),
            '--log-dir',str(staging/'logs'),
            '--queue',args.queue,'--cores',str(args.cores),'--tasks-per-node',str(args.tasks_per_node),
            '--memory',args.memory,'--walltime',args.walltime]
        if args.site_config:jobs.extend(['--site-config',str(args.site_config.absolute())])
        for option in ('mpi_root','intel_env','launcher','node'):
            if getattr(args,option):jobs.extend(['--'+option.replace('_','-'),str(getattr(args,option))])
        jobs.append('--exclusive' if args.exclusive else '--no-exclusive')
        if args.draft:jobs.append('--allow-draft')
        run_checked(jobs)
        manifest=json.loads((staging/'config/colm/run_manifest.json').read_text())
        manifest['resources']={'scheduler':args.scheduler,'queue':args.queue,'cores':args.cores,
            'tasks_per_node':args.tasks_per_node,'memory':args.memory,'walltime':args.walltime,
            'mpi_root':str(args.mpi_root.resolve()) if args.mpi_root else None,
            'intel_env':str(args.intel_env.resolve()) if args.intel_env else None,
            'launcher':args.launcher or ('mpirun' if args.scheduler=='lsf' else 'srun'),
            'exclusive':args.exclusive,'node':args.node}
        manifest['site_config']=args.site_metadata
        manifest['validation_issues']=list(dict.fromkeys(manifest.get('validation_issues',[])+issues))
        (staging/'config/colm/run_manifest.json').write_text(
            json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        render_static_templates(script_dir/'templates/dlb_region_experiment',staging,
            {'EXP_NAME':name,'EXP_DIR':str(target),'TOOLS_DIR':str(script_dir)})
        meta={'schema_version':2,'experiment_id':args.experiment_id,'name':name,'slug':args.slug,
              'region':args.region,'status':'created','site_config':args.site_metadata,
              'created_at':datetime.now(timezone.utc).isoformat(),
              'hypothesis':None,'conclusion':None,
              'source':{'colm_repository':str(repo),'colm_commit':git_value(repo,'rev-parse','HEAD'),
                        'colm_worktree_clean':git_value(repo,'status','--porcelain')==''},
              'configuration':{'colm':'config/colm/run_manifest.json','jobs':'config/jobs',
                               'openbench':'config/openbench'},
              'evaluation':{'start_year':args.eval_start_year,'end_year':args.eval_end_year,
                            'projects':[]}}
        (staging/'manifest.yaml').write_text(json.dumps(meta,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
        run_checked([sys.executable,str(script_dir/'dlb_08_generate_openbench.py'),str(staging),
                     '--eval-start-year',str(args.eval_start_year),
                     '--eval-end-year',str(args.eval_end_year),
                     '--output-root',str(target/'openbench_output'),'--mask-root',str(args.mask_root),'--no-refresh'])
        expected=len(json.loads((staging/'manifest.yaml').read_text())['evaluation']['projects'])
        (staging/'provenance/site_config.json').write_text(json.dumps(args.site_metadata,indent=2)+'\n')
        (staging/'provenance/input_validation.txt').write_text(
            '\n'.join(manifest['validation_issues'])+'\n' if manifest['validation_issues'] else 'Input/source checks passed. Binary compatibility remains unverified.\n')
        artifacts={'schema_version':1,'experiment':name,'artifacts':[]}
        (staging/'artifacts.yaml').write_text(json.dumps(artifacts,indent=2)+'\n',encoding='utf-8')
        # Files are assembled in a sibling temporary directory, but every runtime
        # path must point at the final atomically-renamed experiment directory.
        for path in staging.rglob('*'):
            if path.is_file():
                text=path.read_text(encoding='utf-8',errors='strict')
                if str(staging) in text:
                    path.write_text(text.replace(str(staging),str(target)),encoding='utf-8')
        for job in (staging/'config/jobs').glob('*'):
            if job.suffix in ('.lsf','.slurm') or job.name.endswith('.sh'):
                result=subprocess.run(['bash','-n',str(job)],capture_output=True,text=True)
                if result.returncode:fail(f'bash syntax error {job}: {result.stderr}')
        unresolved=[]
        for path in staging.rglob('*'):
            if path.is_file() and '__' in path.read_text(encoding='utf-8',errors='ignore'):unresolved.append(path)
        if unresolved:fail('unresolved placeholders: '+', '.join(map(str,unresolved)))
        run_checked([sys.executable,str(script_dir/'dlb_07_refresh_experiment.py'),str(staging)])
        os.rename(staging,target)
    except Exception:
        shutil.rmtree(staging,ignore_errors=True)
        raise
    print(f'Created {target}')
    print(f'Cases were checked for collisions but not created under {case_root}.')
    print(f'Generated {expected} OpenBench YAML files; no jobs were submitted.')
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
