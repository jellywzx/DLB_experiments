#!/usr/bin/env python3
"""Refresh AAS experiment status, artifacts, checksums, and validation report."""
# 用法：python3 dlb_07_refresh_experiment.py /path/to/experiment --workers 4
# 默认 4 路检查；--workers 1 使用串行检查，进度和阶段耗时写入 stderr。
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path

from dlb_parallel import add_workers_argument, ordered_checks, timed_phase

from dlb_openbench_state import output_dir,task_state,STATES


def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()


def project_state(task)->str:
    root,project=task
    config=root/project['config']
    return task_state(project['project'],config,output_dir(config)/project['project'])


def check_config(task)->list[str]:
    root,path=task
    text=path.read_text(encoding='utf-8');errors=[]
    if '__' in text:errors.append(f'unresolved placeholder: {path.relative_to(root)}')
    for token in ('project:','evaluation:','reference:','simulation:','comparison:'):
        if token not in text:errors.append(f'{path.relative_to(root)} missing {token}')
    return errors


def check_job(task)->list[str]:
    root,path=task
    checked=subprocess.run(['bash','-n',str(path)],capture_output=True,text=True)
    return [f'bash -n failed: {path.relative_to(root)}'] if checked.returncode else []


def artifact_row(task)->dict:
    root,path=task
    return {'path':str(path.relative_to(root)),'bytes':path.stat().st_size,'sha256':sha256(path)}


def checksum_paths(root:Path)->list[Path]:
    paths=[]
    for directory,dirs,files in os.walk(root,followlinks=False):
        dirs[:]=[name for name in dirs if name not in ('openbench_output','logs')]
        for name in files:
            path=Path(directory)/name
            if (name not in ('checksums.sha256','validation_report.txt','artifacts.yaml') and
                    'openbench_output' not in path.parts and 'logs' not in path.parts and path.is_file()):
                paths.append(path)
    return sorted(paths)


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('experiment_dir',type=Path)
    p.add_argument('--jobs-dir',type=Path)
    p.add_argument('--openbench-result',action='append',default=[],metavar='PROJECT=STATUS',
                   help='STATUS is success, failed, or missing; repeatable')
    p.add_argument('--allow-nonstandard-schedule',action='store_true',
                   help='Refresh an experiment with intentional temporary start/end dates')
    p.add_argument('--mark-validated',action='store_true',
                   help='Set validated only when every automatic check already passes')
    add_workers_argument(p)
    args=p.parse_args()
    root=args.experiment_dir.resolve();manifest_path=root/'manifest.yaml'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    updates={}
    valid_status={'success','failed','missing'}
    for raw in args.openbench_result:
        if '=' not in raw:raise ValueError(f'invalid --openbench-result: {raw}')
        name,status=raw.rsplit('=',1)
        if status not in valid_status:raise ValueError(f'invalid OpenBench status: {status}')
        updates[name]=status
    known={x['project'] for x in manifest['evaluation']['projects']}
    unknown=set(updates)-known
    if unknown:raise ValueError('unknown OpenBench projects: '+', '.join(sorted(unknown)))
    projects=manifest['evaluation']['projects']
    states=ordered_checks(project_state,[(root,project) for project in projects],
                          args.workers,'OpenBench task states')
    for project,state in zip(projects,states):
        name=project['project']
        if updates.get(name)=='success' and state!='completed_unchanged':
            raise ValueError(f'cannot mark success without matching successful run record: {name} ({state})')
        project['task_state']=state
        project['status']='success' if state=='completed_unchanged' else state
        if name in updates:
            project['manual_status']=updates[name]
            if updates[name]!='success':project['status']=updates[name]

    status_cmd=[sys.executable,str(Path(__file__).resolve().parent/'dlb_06_stage_status.py'),
                '--nml-dir',str(root/'config/colm'),'--json','--workers',str(args.workers)]
    colm_manifest=json.loads((root/'config/colm/run_manifest.json').read_text(encoding='utf-8'))
    if colm_manifest.get('draft'):status_cmd.append('--allow-draft')
    if args.allow_nonstandard_schedule:status_cmd.append('--allow-nonstandard-schedule')
    if args.jobs_dir:status_cmd.extend(['--jobs-dir',str(args.jobs_dir.resolve())])
    with timed_phase('CoLM status subprocess'):
        result=subprocess.run(status_cmd,text=True,stdout=subprocess.PIPE,check=False)
        if result.returncode:
            raise ValueError(f'stage status failed (exit {result.returncode}): {result.stderr or result.stdout}')
    stages=json.loads(result.stdout)
    configs=sorted((root/'config/openbench').rglob('*.yaml'))
    expected=len(manifest['evaluation']['projects'])
    config_errors=[]
    if len(configs)!=expected:config_errors.append(f'OpenBench YAML count {len(configs)} != {expected}')
    for errors in ordered_checks(check_config,[(root,path) for path in configs],
                                 args.workers,'YAML checks'):
        config_errors.extend(errors)
    jobs=sorted((root/'config/jobs').glob('*'))
    scripts=[path for path in jobs if path.suffix in ('.lsf','.slurm') or path.name.endswith('.sh')]
    for errors in ordered_checks(check_job,[(root,path) for path in scripts],
                                 args.workers,'Shell syntax checks'):
        config_errors.extend(errors)
    statuses=[x.get('status','missing') for x in manifest['evaluation']['projects']]
    openbench_complete=bool(statuses) and all(x=='success' for x in statuses)
    automatic_ok=stages['colm_complete'] and openbench_complete and not config_errors
    if args.mark_validated and not automatic_ok:
        raise ValueError('cannot mark validated: CoLM/OpenBench/config checks are not all complete')
    if args.mark_validated or (manifest.get('status')=='validated' and automatic_ok):status='validated'
    elif openbench_complete and stages['colm_complete']:status='openbench_complete'
    elif stages['colm_complete']:status='colm_complete'
    elif stages['handoff_complete']:status='handoff_complete'
    elif stages['dam_init_complete']:status='dam_init_complete'
    elif stages['spinup_complete']:status='spinup_complete'
    else:status='created'
    manifest['status']=status
    manifest['refreshed_at']=datetime.now(timezone.utc).isoformat()
    manifest['stage_evidence']={k:stages[k] for k in ('spinup_complete','dam_init_complete','handoff_complete','colm_complete')}
    manifest['evaluation']['summary']={'success':statuses.count('success'),'failed':statuses.count('failed'),
                                       'missing':statuses.count('not_run')+statuses.count('missing'),'expected':len(statuses),
                                       **{state:sum(p.get('task_state')==state for p in manifest['evaluation']['projects']) for state in STATES}}
    manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

    artifact_files=[root/'README.md',root/'manifest.yaml',root/'config/colm/run_manifest.json',*configs,*jobs]
    artifact_rows=ordered_checks(artifact_row,[(root,path) for path in artifact_files if path.is_file()],
                                 args.workers,'Artifact hashes')
    artifacts={'schema_version':1,'experiment':manifest['name'],'refreshed_at':manifest['refreshed_at'],
               'artifacts':artifact_rows}
    (root/'artifacts.yaml').write_text(json.dumps(artifacts,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    with timed_phase('Checksum inventory'):
        checksum_files=checksum_paths(root)
    digests=ordered_checks(sha256,checksum_files,args.workers,'Checksum hashes')
    (root/'checksums.sha256').write_text(''.join(
        f'{digest}  {path.relative_to(root)}\n' for path,digest in zip(checksum_files,digests)),encoding='utf-8')
    report=['PASS' if automatic_ok else 'INCOMPLETE',f'status: {status}',
        f'spinup_complete: {stages["spinup_complete"]}',f'dam_init_complete: {stages["dam_init_complete"]}',
        f'handoff_complete: {stages["handoff_complete"]}',f'colm_complete: {stages["colm_complete"]}',
        f'openbench: success={statuses.count("success")} failed={statuses.count("failed")} not_run={statuses.count("not_run")}',
        f'openbench_yaml: {len(configs)}/{expected}',*('ERROR: '+x for x in config_errors)]
    (root/'validation_report.txt').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print(f'{manifest["name"]}: {status}')
    print(report[6]);print(report[7])
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
