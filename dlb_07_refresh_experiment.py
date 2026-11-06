#!/usr/bin/env python3
"""Refresh AAS experiment status, artifacts, checksums, and validation report."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime,timezone
from pathlib import Path


def sha256(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()


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
    for project in manifest['evaluation']['projects']:
        name=project['project'];output=root/'openbench_output'/name
        if name in updates:project['status']=updates[name]
        elif (output/'.openbench_success').is_file() or (output/'success.marker').is_file():project['status']='success'
        elif (output/'.openbench_failed').is_file() or (output/'failed.marker').is_file():project['status']='failed'

    status_cmd=[sys.executable,str(Path(__file__).resolve().parent/'dlb_06_stage_status.py'),
                '--nml-dir',str(root/'config/colm'),'--json']
    colm_manifest=json.loads((root/'config/colm/run_manifest.json').read_text(encoding='utf-8'))
    if colm_manifest.get('draft'):status_cmd.append('--allow-draft')
    if args.allow_nonstandard_schedule:status_cmd.append('--allow-nonstandard-schedule')
    if args.jobs_dir:status_cmd.extend(['--jobs-dir',str(args.jobs_dir.resolve())])
    result=subprocess.run(status_cmd,text=True,capture_output=True,check=False)
    if result.returncode:raise ValueError(f'stage status failed: {result.stderr or result.stdout}')
    stages=json.loads(result.stdout)
    configs=sorted((root/'config/openbench').rglob('*.yaml'))
    expected=len(manifest['evaluation']['projects'])
    config_errors=[]
    if len(configs)!=expected:config_errors.append(f'OpenBench YAML count {len(configs)} != {expected}')
    for path in configs:
        text=path.read_text(encoding='utf-8')
        if '__' in text:config_errors.append(f'unresolved placeholder: {path.relative_to(root)}')
        for token in ('project:','evaluation:','reference:','simulation:','comparison:'):
            if token not in text:config_errors.append(f'{path.relative_to(root)} missing {token}')
    jobs=sorted((root/'config/jobs').glob('*'))
    for path in jobs:
        if path.suffix in ('.lsf','.slurm') or path.name.endswith('.sh'):
            checked=subprocess.run(['bash','-n',str(path)],capture_output=True,text=True)
            if checked.returncode:config_errors.append(f'bash -n failed: {path.relative_to(root)}')
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
                                       'missing':statuses.count('missing'),'expected':len(statuses)}
    manifest_path.write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')

    artifact_files=[root/'README.md',root/'manifest.yaml',root/'config/colm/run_manifest.json',*configs,*jobs]
    artifact_rows=[]
    for path in artifact_files:
        if path.is_file():artifact_rows.append({'path':str(path.relative_to(root)),'bytes':path.stat().st_size,'sha256':sha256(path)})
    artifacts={'schema_version':1,'experiment':manifest['name'],'refreshed_at':manifest['refreshed_at'],
               'artifacts':artifact_rows}
    (root/'artifacts.yaml').write_text(json.dumps(artifacts,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    checksum_files=[p for p in sorted(root.rglob('*')) if p.is_file() and
                    p.name not in ('checksums.sha256','validation_report.txt','artifacts.yaml') and
                    'openbench_output' not in p.parts and 'logs' not in p.parts]
    (root/'checksums.sha256').write_text(''.join(
        f'{sha256(path)}  {path.relative_to(root)}\n' for path in checksum_files),encoding='utf-8')
    report=['PASS' if automatic_ok else 'INCOMPLETE',f'status: {status}',
        f'spinup_complete: {stages["spinup_complete"]}',f'dam_init_complete: {stages["dam_init_complete"]}',
        f'handoff_complete: {stages["handoff_complete"]}',f'colm_complete: {stages["colm_complete"]}',
        f'openbench: success={statuses.count("success")} failed={statuses.count("failed")} missing={statuses.count("missing")}',
        f'openbench_yaml: {len(configs)}/{expected}',*('ERROR: '+x for x in config_errors)]
    (root/'validation_report.txt').write_text('\n'.join(report)+'\n',encoding='utf-8')
    print(f'{manifest["name"]}: {status}')
    print(report[6]);print(report[7])
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
