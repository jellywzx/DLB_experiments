#!/usr/bin/env python3
"""Generate and optionally submit full-slot single-host LSF OpenBench array jobs."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from datetime import datetime,timezone
from pathlib import Path


SELECTIONS=(
    ('D','E1_D','select_d'),('L','E2_L','select_l'),('B','E3_B','select_b'),
    ('DL','E4_DL','select_dl'),('DB','E5_DB','select_db'),
    ('LB','E6_LB','select_lb'),('DLB','E7_ALL','select_dlb'),
)


def sq(value:object)->str:return shlex.quote(str(value))


def output_dir(config:Path)->Path:
    for line in config.read_text(encoding='utf-8').splitlines():
        if re.match(r'^\s{2}output_dir\s*:',line):
            raw=line.split(':',1)[1].strip().strip('"\'')
            if not raw:break
            return Path(raw).expanduser().resolve()
    raise ValueError(f'missing project.output_dir: {config}')


def has_results(path:Path)->bool:
    if not path.exists():return False
    if not path.is_dir():return True
    return next(path.iterdir(),None) is not None


def log_outcome(path:Path)->str|None:
    log=path/'run.log'
    if not log.is_file():return None
    with log.open('rb') as stream:
        stream.seek(0,2);stream.seek(max(0,stream.tell()-262144))
        text=stream.read().decode('utf-8',errors='replace')
    summaries=re.findall(r'All phases complete:.*?\b(\d+) errors?\b',text)
    if summaries:return 'failed' if int(summaries[-1]) else 'success'
    summaries=re.findall(r'Evaluation phase:.*?\b(\d+) failed\b',text)
    if summaries and int(summaries[-1]):return 'failed'
    return None


def result_state(path:Path)->str:
    if path.is_symlink() or not path.is_dir():return 'unknown'
    if (path/'.dlb_openbench_running').exists():return 'running'
    failed=any((path/name).is_file() for name in ('.openbench_failed','failed.marker'))
    success=any((path/name).is_file() for name in ('.openbench_success','success.marker'))
    outcome=log_outcome(path)
    # Older wrappers could write success despite OpenBench reporting errors.
    if outcome=='failed':return 'failed'
    if failed and (success or outcome=='success'):return 'conflicting'
    if failed:return 'failed'
    if success or outcome=='success':return 'success'
    return 'unknown'


def result_token(path:Path)->str:
    stat=path.stat()
    return f'{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}'


def retry_task(config:Path,output:Path,token:str,openbench:Path,cores:int)->int:
    """Execute one immutable retry snapshot; never called by the preview path."""
    lock=output.parent/f'.{output.name}.retry.lock'
    with lock.open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'SKIP retry already active: {output}');return 0
        state=result_state(output)
        if state!='failed' or result_token(output)!=token:
            print(f'SKIP retry snapshot no longer matches: {output} state={state}');return 0
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup=output.parent/'.dlb_openbench_backups'/f'{output.name}.{stamp}.{uuid.uuid4().hex}'
        backup.parent.mkdir(parents=True,exist_ok=True)
        output.rename(backup)
        print(f'BACKUP {output} -> {backup}',flush=True)
        output.mkdir()
        running=output/'.dlb_openbench_running';running.touch()
        # An interrupted process deliberately leaves its running marker for review.
        status=subprocess.run([str(openbench),'check',str(config)],check=False).returncode
        if not status:
            status=subprocess.run([str(openbench),'run',str(config),'--cores',str(cores)],check=False).returncode
        if not status and log_outcome(output)=='failed':status=1
        running.unlink()
        (output/('.openbench_failed' if status else '.openbench_success')).touch()
        return status


def safe_field(value:object,label:str)->str:
    text=str(value)
    if not text or '\t' in text or '\n' in text or '\r' in text:
        raise ValueError(f'invalid {label}: {text!r}')
    return text


def write_atomic(path:Path,text:str)->None:
    temporary=path.with_name(f'.{path.name}.tmp.{os.getpid()}')
    temporary.write_text(text,encoding='utf-8')
    os.replace(temporary,path)


def job_script(label:str,count:int,mapping:Path,openbench:Path,logs:Path,queue:str,
               cores:int,slots_per_node:int,memory:str,walltime:str,
               max_concurrent:int|None,retry:bool=False)->str:
    array=f'openbench_{label}[1-{count}]'+(f'%{max_concurrent}' if max_concurrent else '')
    lines=['#!/usr/bin/env bash',f'#BSUB -J "{array}"',
        f'#BSUB -q {queue}',f'#BSUB -n {slots_per_node}',f'#BSUB -W {walltime}',
        '#BSUB -R "span[hosts=1]"',f'#BSUB -R "rusage[mem={memory}]"',
        f'#BSUB -o {logs}/openbench_{label}.%J.%I.out',
        f'#BSUB -e {logs}/openbench_{label}.%J.%I.err',
        '#BSUB -env "all,LSB_STDOUT_DIRECT=Y"','',
        'set -euo pipefail',f'MAPPING={sq(mapping)}',f'OPENBENCH={sq(openbench)}',
        'INDEX=${LSB_JOBINDEX:?LSB_JOBINDEX is required}',
        "ROW=$(awk -F '\\t' -v i=\"$INDEX\" '$1 == i {print; exit}' \"$MAPPING\")",
        '[[ -n "$ROW" ]] || { echo "No mapping for index $INDEX" >&2; exit 2; }',
        "IFS=$'\\t' read -r _ PROJECT CONFIG OUTPUT TOKEN <<< \"$ROW\"",
        '[[ -f "$CONFIG" ]] || { echo "Missing config: $CONFIG" >&2; exit 2; }',
        '[[ -x "$OPENBENCH" ]] || { echo "OpenBench is not executable: $OPENBENCH" >&2; exit 2; }',
        'if [[ -e "$OUTPUT" ]]; then',
        '  if [[ ! -d "$OUTPUT" ]] || [[ -n "$(find "$OUTPUT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then',
        '    echo "SKIP existing result: $PROJECT -> $OUTPUT"',
        '    exit 0',
        '  fi',
        'fi',
        'mkdir -p "$OUTPUT"',
        'RUNNING="$OUTPUT/.dlb_openbench_running"',
        'FAILED="$OUTPUT/.openbench_failed"',
        'SUCCESS="$OUTPUT/.openbench_success"',
        'touch "$RUNNING"',
        'rm -f "$FAILED"',
        'echo "START $(date -Is) $(hostname) $PROJECT"',
        'if "$OPENBENCH" check "$CONFIG" && "$OPENBENCH" run "$CONFIG" --cores '+str(cores)+'; then',
        '  rm -f "$RUNNING" "$FAILED"',
        '  touch "$SUCCESS"',
        '  echo "FINISH $(date -Is) $PROJECT"',
        'else',
        '  status=$?',
        '  rm -f "$RUNNING" "$SUCCESS"',
        '  touch "$FAILED"',
        '  echo "FAILED $(date -Is) $PROJECT status=$status" >&2',
        '  exit "$status"',
        'fi','']
    if retry:
        start=lines.index('if [[ -e "$OUTPUT" ]]; then')
        lines=lines[:start]+[
            f'exec {sq(sys.executable)} {sq(Path(__file__).resolve())} --_retry-task '
            f'"$CONFIG" "$OUTPUT" "$TOKEN" "$OPENBENCH" {cores}','']
    else:
        index=next(i for i,line in enumerate(lines) if line.startswith('if "$OPENBENCH" check'))
        lines[index]=lines[index].replace('; then',
            f' && {sq(sys.executable)} {sq(Path(__file__).resolve())} --_result-ok "$OUTPUT"; then')
    return '\n'.join(lines)


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('experiment_dir',type=Path)
    p.add_argument('-D',dest='select_d',action='store_true',help='Select E1_D')
    p.add_argument('-L',dest='select_l',action='store_true',help='Select E2_L')
    p.add_argument('-B',dest='select_b',action='store_true',help='Select E3_B')
    p.add_argument('-DL',dest='select_dl',action='store_true',help='Select E4_DL')
    p.add_argument('-DB',dest='select_db',action='store_true',help='Select E5_DB')
    p.add_argument('-LB',dest='select_lb',action='store_true',help='Select E6_LB')
    p.add_argument('-DLB',dest='select_dlb',action='store_true',help='Select E7_ALL')
    p.add_argument('--all',dest='all_projects',action='store_true',help='Select every OpenBench project')
    p.add_argument('--retry-failed',action='store_true',
                   help='Retry only confirmed failed outputs; archive old results at task startup')
    p.add_argument('--openbench-exe',type=Path,
                   help='OpenBench executable; default: openbench from the current PATH')
    p.add_argument('--queue',default='normal')
    p.add_argument('--cores',type=int,default=2)
    p.add_argument('--slots-per-node',type=int,default=48,
                   help='LSF slots reserved on one host; full-node only when this equals its capacity (default: 48)')
    p.add_argument('--memory',default='150G')
    p.add_argument('--walltime',default='24:00')
    p.add_argument('--max-concurrent',type=int,default=5,
                   help='Maximum simultaneous array tasks (default: 5)')
    p.add_argument('--out-dir',type=Path,help='Default: EXP_DIR/config/openbench_jobs')
    p.add_argument('--log-dir',type=Path,help='Default: EXP_DIR/logs/openbench')
    p.add_argument('--overwrite',action='store_true',help='Replace generated mapping/job files only')
    p.add_argument('--submit',action='store_true',help='Submit with bsub; default is dry-run')
    args=p.parse_args()

    requested=[(short,treatment) for short,treatment,dest in SELECTIONS if getattr(args,dest)]
    if args.all_projects and requested:p.error('--all cannot be combined with experiment selectors')
    if not args.all_projects and not requested:
        p.error('select --all or at least one of -D/-L/-B/-DL/-DB/-LB/-DLB')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',args.queue):p.error('invalid --queue')
    if not 1<=args.cores<=100000:p.error('--cores must be >= 1')
    if not 1<=args.slots_per_node<=100000:p.error('--slots-per-node must be >= 1')
    if args.cores>args.slots_per_node:p.error('--cores cannot exceed --slots-per-node')
    if not re.fullmatch(r'[1-9][0-9]*(?:[KMGTP])?',args.memory,re.I):p.error('invalid --memory')
    if not re.fullmatch(r'\d{1,4}:[0-5]\d',args.walltime):p.error('--walltime must be HH:MM')
    if args.max_concurrent is not None and args.max_concurrent<1:p.error('--max-concurrent must be >= 1')

    root=args.experiment_dir.expanduser().resolve();manifest_path=root/'manifest.yaml'
    if not manifest_path.is_file():raise ValueError(f'missing experiment manifest: {manifest_path}')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    projects=manifest.get('evaluation',{}).get('projects',[])
    if not isinstance(projects,list) or not projects:raise ValueError('manifest has no OpenBench projects')
    if args.all_projects:label='all';selected=projects
    else:
        label='_'.join(short for short,_ in requested)
        treatments={treatment for _,treatment in requested}
        selected=[item for item in projects if item.get('treatment') in treatments]
    if not selected:raise ValueError(f'no OpenBench projects selected for {label}')
    if args.retry_failed:label+='_retry_failed'

    config_root=(root/'config/openbench').resolve();seen_projects=set();seen_configs=set();pending=[];skipped=[]
    for item in selected:
        project=safe_field(item.get('project'),'project')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',project):raise ValueError(f'unsafe project name: {project!r}')
        config=(root/safe_field(item.get('config'),'config')).resolve()
        try:config.relative_to(config_root)
        except ValueError as exc:raise ValueError(f'config escapes config/openbench: {config}') from exc
        if project in seen_projects:raise ValueError(f'duplicate project: {project}')
        if config in seen_configs:raise ValueError(f'duplicate config: {config}')
        seen_projects.add(project);seen_configs.add(config)
        if not config.is_file():raise ValueError(f'missing OpenBench config: {config}')
        output=output_dir(config)/project
        safe_field(config,'config');safe_field(output,'output_dir')
        if args.retry_failed:
            state=result_state(output)
            print(f'STATE {project}: {state} (manifest={item.get("status","missing")})')
            if state=='failed':pending.append((project,config,output))
            else:skipped.append((project,output))
        elif has_results(output):skipped.append((project,output))
        else:pending.append((project,config,output))

    print(f'Selection {label}: selected={len(selected)} pending={len(pending)} skipped={len(skipped)}')
    for project,output in skipped:
        reason='not a confirmed failed result' if args.retry_failed else 'existing result'
        print(f'SKIP {reason}: {project} -> {output}')
    if not pending:
        print('No jobs to generate or submit.')
        return 0

    out_dir=(args.out_dir.expanduser().resolve() if args.out_dir else root/'config/openbench_jobs')
    log_dir=(args.log_dir.expanduser().resolve() if args.log_dir else root/'logs/openbench')
    mapping=out_dir/f'openbench_{label}_projects.tsv';job=out_dir/f'submit_openbench_{label}_array.lsf'
    if args.retry_failed and job.is_file() and not args.overwrite:
        entries=[line for line in job.read_text(encoding='utf-8').splitlines() if line.startswith('MAPPING=')]
        if len(entries)!=1:raise ValueError(f'invalid retry snapshot: {job}; use --overwrite')
        fields=shlex.split(entries[0][len('MAPPING='):])
        if len(fields)!=1:raise ValueError(f'invalid retry mapping: {job}')
        mapping=Path(fields[0])
        if mapping.parent!=out_dir or not mapping.name.startswith(f'openbench_{label}_'):
            raise ValueError(f'retry mapping escapes snapshot directory: {mapping}')
    existing=mapping.exists() or job.exists()
    if existing and not args.overwrite:
        if not mapping.is_file() or not job.is_file():
            raise ValueError(f'incomplete generated job files for {label}; use --overwrite')
        if not args.submit:
            raise ValueError(f'generated job files already exist for {label}; use --submit or --overwrite')
        print(f'Reusing generated job snapshot: {job}')
        print('Resource options are read from that snapshot; use --overwrite --submit to regenerate it.')
    else:
        detected=args.openbench_exe or shutil.which('openbench')
        if not detected:
            raise ValueError('openbench not found in PATH; activate its environment or use --openbench-exe')
        openbench=Path(detected).expanduser().resolve()
        if not openbench.is_file() or not os.access(openbench,os.X_OK):
            raise ValueError(f'OpenBench is not executable: {openbench}')
        out_dir.mkdir(parents=True,exist_ok=True);log_dir.mkdir(parents=True,exist_ok=True)
        if args.retry_failed:
            # Submitted jobs retain an immutable mapping even after --overwrite.
            mapping=out_dir/f'openbench_{label}_{uuid.uuid4().hex}_projects.tsv'
        rows=['index\tproject\tconfig\toutput_dir'+('\ttoken' if args.retry_failed else '')]
        rows.extend(f'{i}\t{project}\t{config}\t{output}'+
                    (f'\t{result_token(output)}' if args.retry_failed else '')
                    for i,(project,config,output) in enumerate(pending,1))
        write_atomic(mapping,'\n'.join(rows)+'\n')
        write_atomic(job,job_script(label,len(pending),mapping,openbench,log_dir,args.queue,
                                    args.cores,args.slots_per_node,args.memory,args.walltime,
                                    args.max_concurrent,retry=args.retry_failed))
        job.chmod(0o755)
        print(f'Generated {len(pending)} jobs: {job}')
    if not args.submit:
        print(f'DRY-RUN: bsub < {job}')
        return 0
    bsub=shutil.which('bsub')
    if not bsub:raise ValueError('bsub not found in PATH')
    result=subprocess.run([bsub],input=job.read_text(encoding='utf-8'),text=True,
                          cwd=out_dir,capture_output=True,check=False)
    if result.stdout:print(result.stdout,end='')
    if result.stderr:print(result.stderr,end='',file=sys.stderr)
    if result.returncode:raise ValueError(f'bsub failed with status {result.returncode}')
    return 0


if __name__=='__main__':
    try:
        if len(sys.argv)==3 and sys.argv[1]=='--_result-ok':
            sys.exit(1 if log_outcome(Path(sys.argv[2]))=='failed' else 0)
        if len(sys.argv)==7 and sys.argv[1]=='--_retry-task':
            sys.exit(retry_task(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],
                                Path(sys.argv[5]),int(sys.argv[6])))
        sys.exit(main())
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
