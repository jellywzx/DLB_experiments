#!/usr/bin/env python3
"""03 — Generate staged CoLM LSF or Slurm scripts and an opt-in submit helper.

The generator never submits jobs. Generated submit_batch.sh defaults to --dry-run,
and when explicitly --submit, runs preflight with NetCDF & forcing-file checks.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import shlex
import sys
from pathlib import Path
from dlb_00_workflow_common import WorkflowError,load_study,error_exit


def sq(s:object)->str:return shlex.quote(str(s))


def hpc_time(raw:str,scheduler:str)->str:
    m=re.fullmatch(r'(\d{1,4}):(\d\d)(?::(\d\d))?',raw)
    if not m or int(m[2])>=60 or int(m[3] or 0)>=60:
        raise WorkflowError('--walltime 格式应 HH:MM:SS 或 HH:MM')
    hh,mm,ss=int(m[1]),int(m[2]),int(m[3] or 0)
    if scheduler=='lsf':
        total_minutes=hh*60+mm+(1 if ss else 0)
        return f'{total_minutes//60:02d}:{total_minutes%60:02d}'
    return f'{hh:02d}:{mm:02d}:{ss:02d}'


def script_body(label:str,case:str,nml:Path,run_dir:Path,steps:list[str],job_dir:Path,
                log_dir:Path,scheduler:str,queue:str,cores:int,tpn:int,mem:str,walltime:str,
                launcher:str,workdir:Path,mpi_root:Path|None,intel_env:Path|None)->str:
    name=case[:100]
    log_rel=Path(os.path.relpath(log_dir,job_dir))
    nml_rel=os.path.relpath(nml,job_dir)
    lines=['#!/usr/bin/env bash']
    if scheduler=='lsf':
        lines.extend([f'#BSUB -J {name}',f'#BSUB -q {queue}',f'#BSUB -n {cores}',
                      f'#BSUB -W {walltime}',f'#BSUB -R "span[ptile={tpn}]"',
                      f'#BSUB -R "rusage[mem={mem}]"',
                      f'#BSUB -o {log_rel}/{label}.%J.out',
                      f'#BSUB -e {log_rel}/{label}.%J.err'])
    else:
        nodes=math.ceil(cores/tpn)
        lines.extend([f'#SBATCH --job-name={name}',f'#SBATCH --partition={queue}',
                      f'#SBATCH --nodes={nodes}',f'#SBATCH --ntasks={cores}',
                      f'#SBATCH --ntasks-per-node={tpn}',f'#SBATCH --time={walltime}',
                      f'#SBATCH --mem={mem}',f'#SBATCH --output={log_rel}/{label}.%j.out',
                      f'#SBATCH --error={log_rel}/{label}.%j.err'])
    lines.extend(['','set -euo pipefail',
                  'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"'])
    if intel_env:
        lines.append(f'source {sq(intel_env)}')
    lines.extend(['export OMP_NUM_THREADS=1','export HDF5_USE_FILE_LOCKING=FALSE','ulimit -s unlimited',
        f'cd {sq(workdir)}',f'RUN_DIR={sq(run_dir)}',f'NML="$SCRIPT_DIR"/{sq(nml_rel)}',
        f'MARKER="$SCRIPT_DIR/markers/{label}.success"',
        'test -f "$NML" || { echo "Missing NML: $NML" >&2; exit 1; }',
        'mkdir -p "$(dirname "$MARKER")"',
        'rm -f "$MARKER"  # delete only this script-owned success marker',
        'echo "START $(date -Is)  $(hostname)"',
        f'echo "CASE {case} ; NML $NML ; STEPS {" ".join(steps)}"',
        ])
    if launcher=='mpirun':
        launch=sq((mpi_root/'bin/mpirun') if mpi_root else 'mpirun')+f' -np {cores}'
    elif launcher=='srun':launch='srun --ntasks='+str(cores)
    else:launch='yhrun -n '+str(cores)
    for step in steps:
        executable=run_dir/f'{step}.x'
        lines.extend([f'EXE={sq(executable)}',
                      'test -x "$EXE" || { echo "Missing executable: $EXE" >&2; exit 1; }',
                      f'echo "STEP {step} START $(date -Is)"',
                      f'{launch} "$EXE" "$NML"',
                      f'echo "STEP {step} FINISH $(date -Is)"'])
    lines.extend(['touch "$MARKER"','echo "FINISH $(date -Is)"',''])
    return '\n'.join(lines)


def submit_helper(scheduler:str,job_dir:Path,nml_dir:Path,exe:Path,script_dir:Path)->str:
    ext='lsf' if scheduler=='lsf' else 'slurm'
    submit='bsub' if scheduler=='lsf' else 'sbatch'
    preflight=script_dir/'dlb_04_preflight.py'
    nml_rel=os.path.relpath(nml_dir,job_dir)
    # Restrict --only to known literal labels; use a gate on every submitted case.
    return f'''#!/usr/bin/env bash
# Safe submission helper. Only --submit can call {submit}.
set -euo pipefail
MODE=dry-run
STAGE=experiments
ONLY=all
while (($#)); do
  case "$1" in
    --dry-run) MODE=dry-run ;;
    --submit) MODE=submit ;;
    --stage) shift; STAGE="${{1:?missing stage}}" ;;
    --only) shift; ONLY="${{1:?missing labels}}" ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done
case "$STAGE" in spinup|dam-init|experiments) ;; *) echo "stage must be spinup/dam-init/experiments" >&2; exit 2;; esac
SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
JOB_DIR="$SCRIPT_DIR"
NML_DIR="$(cd -- "$SCRIPT_DIR"/{sq(nml_rel)} && pwd)"
EXE={sq(exe)}
PREFLIGHT={sq(preflight)}
if [[ "$STAGE" == spinup ]]; then
  JOBS=(SPINUP)
  PHASE=spinup
  ONLY=all
elif [[ "$STAGE" == dam-init ]]; then
  JOBS=(D_INIT)
  PHASE=dam-init
  ONLY=all
else
  # Start potentially expensive BIF experiments earlier; no scientific dependency among the eight.
  ORDER=(E7_ALL E6_LB E5_DB E3_B E4_DL E1_D E2_L E0_CTL)
  PHASE=experiments
  if [[ "$ONLY" == all ]]; then
    JOBS=("${{ORDER[@]}}")
  else
    IFS=',' read -r -a JOBS <<< "$ONLY"
    for j in "${{JOBS[@]}}"; do
      case "$j" in E0_CTL|E1_D|E2_L|E3_B|E4_DL|E5_DB|E6_LB|E7_ALL) ;;
        *) echo "Invalid experiment: $j" >&2; exit 2;; esac
    done
  fi
fi
if [[ "$MODE" == submit ]]; then
  {sq(sys.executable)} "$PREFLIGHT" --nml-dir "$NML_DIR" --phase "$PHASE" \\
    --only "$ONLY" --exe "$EXE" --check-netcdf --check-forcing-files
fi
for j in "${{JOBS[@]}}"; do
  JOB="$JOB_DIR/submit_${{j}}.{ext}"
  [[ -f "$JOB" ]] || {{ echo "Missing job: $JOB" >&2; exit 2; }}
  if [[ "$MODE" == submit ]]; then
    echo "SUBMIT: $JOB"
    {'(cd "$JOB_DIR" && bsub < "$JOB")' if scheduler=='lsf' else '(cd "$JOB_DIR" && sbatch "$JOB")'}
  else
    echo "DRY-RUN: {'bsub <' if scheduler=='lsf' else 'sbatch'} $JOB"
  fi
done
'''


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nml-dir',required=True,type=Path)
    p.add_argument('--scheduler',required=True,choices=['lsf','slurm'])
    p.add_argument('--exe',required=True,type=Path)
    p.add_argument('--out-dir',type=Path,help='Default: <nml-dir>/jobs_<scheduler>')
    p.add_argument('--log-dir',type=Path,help='Scheduler stdout/stderr directory (default: out-dir)')
    p.add_argument('--queue',required=True,help='Site queue or partition (e.g. normal / deimos)')
    p.add_argument('--cores',type=int,default=480)
    p.add_argument('--tasks-per-node',type=int,default=48)
    p.add_argument('--memory',default='150G',help='Site-specific memory request (default example: 150G)')
    p.add_argument('--walltime',default='24:00:00')
    p.add_argument('--launcher',choices=['mpirun','srun','yhrun'])
    p.add_argument('--mpi-root',type=Path,help='Optional MPI root; uses bin/mpirun')
    p.add_argument('--intel-env',type=Path,help='Optional environment script sourced by each job')
    p.add_argument('--workdir',type=Path,help='Working directory, defaults to executable parent')
    p.add_argument('--overwrite',action='store_true')
    p.add_argument('--allow-draft',action='store_true',help=argparse.SUPPRESS)
    args=p.parse_args()
    study=load_study(args.nml_dir,allow_draft=args.allow_draft)
    exe=args.exe.expanduser().resolve()
    workdir=(args.workdir or exe.parent).expanduser().resolve()
    output=(args.out_dir or (study.nml_dir/f'jobs_{args.scheduler}')).expanduser().resolve()
    log_dir=(args.log_dir or output).expanduser().resolve()
    if output in (study.nml_dir,study.case_root,Path('/')):
        raise WorkflowError('作业文件目录必须与 nml/case 根目录分开')
    if any(c.isspace() for c in str(output)):
        raise WorkflowError('LSF/Slurm 输出路径不能包含空白字符；请选择无空格的 jobs 目录')
    if not (1<=args.cores<=100000) or not (1<=args.tasks_per_node<=args.cores):
        raise WorkflowError('cores / tasks-per-node 范围错误')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',args.queue):
        raise WorkflowError('队列名应只包含字母、数字、下划线、点或减号')
    if not re.fullmatch(r'\d+(?:[GMK])?',args.memory,re.I):
        raise WorkflowError('--memory 示例 150G、64G、100000')
    launcher=args.launcher or ('mpirun' if args.scheduler=='lsf' else 'srun')
    wall=hpc_time(args.walltime,args.scheduler)
    files={}
    runs=(study.spinup,*(([study.dam_init]) if study.dam_init else []),*study.experiments)
    for run in runs:
        steps=list(run.spec.get('steps') or
                   (['mksrfdata','mkinidata','colm'] if run.label=='SPINUP' else
                    ['mkinidata'] if run.label=='D_INIT' else ['colm']))
        files[f'submit_{run.label}.{"lsf" if args.scheduler=="lsf" else "slurm"}']=script_body(
          run.label,run.case,run.nml,exe.parent,steps,output,log_dir,args.scheduler,args.queue,
          args.cores,args.tasks_per_node,args.memory,wall,launcher,workdir,
          args.mpi_root.resolve() if args.mpi_root else None,
          args.intel_env.resolve() if args.intel_env else None)
    files['submit_batch.sh']=submit_helper(args.scheduler,output,study.nml_dir,exe,Path(__file__).resolve().parent)
    exists=[output/k for k in files if (output/k).exists()]
    if exists and not args.overwrite:
        raise WorkflowError('已有作业文件，默认不覆盖: '+', '.join(str(x) for x in exists))
    output.mkdir(parents=True,exist_ok=True)
    log_dir.mkdir(parents=True,exist_ok=True)
    for name,contents in files.items():
        path=output/name
        path.write_text(contents,encoding='utf-8')
        path.chmod(0o755 if name.endswith('.sh') else 0o644)
    print(f'Generated {len(files)} scheduler files at {output}')
    print('No jobs submitted. Preview: bash submit_batch.sh --dry-run --stage experiments')
    print('Site parameters require validation. Submission is opt-in: --submit.')
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (WorkflowError,OSError,ValueError) as exc:sys.exit(error_exit(exc))
