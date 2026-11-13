"""DLB submission evidence and locked execution; OpenBench owns evaluation caching."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

RECORD='.dlb_openbench_run.json'
STATES=('not_run','completed_unchanged','config_changed','failed','running','unverified')


def sha256(path:Path)->str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_json(path:Path,value:dict)->None:
    temporary=path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


def output_dir(config:Path)->Path:
    data=yaml.safe_load(config.read_text(encoding='utf-8'))
    return Path(os.path.expandvars(data['project']['output_dir'])).expanduser().resolve()


def lock_path(output:Path)->Path:
    key=hashlib.sha256(str(output.resolve()).encode()).hexdigest()
    return output.parent/'.dlb_openbench_locks'/f'{key}.lock'


def is_running(output:Path)->bool:
    path=lock_path(output)
    if not path.exists():return False
    with path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        return False


def task_state(project:str,config:Path,output:Path,*,locked:bool=False)->str:
    if not locked and is_running(output):return 'running'
    try:
        record=json.loads((output/RECORD).read_text(encoding='utf-8'))
        valid=(record.get('project')==project and record.get('config')==str(config.resolve()) and
               record.get('version')==1 and isinstance(record.get('config_sha256'),str) and
               len(record['config_sha256'])==64)
        if valid and record.get('status')=='success' and record.get('success_at') and \
                record.get('check_returncode')==0 and record.get('run_returncode')==0:
            return 'completed_unchanged' if record['config_sha256']==sha256(config) else 'config_changed'
        if valid and record.get('status') in ('failed','running'):
            # A running record without a held lock is an interrupted attempt.
            return 'failed'
    except (OSError,ValueError,TypeError,AttributeError):pass
    if output.exists() and (not output.is_dir() or next(output.iterdir(),None) is not None):
        return 'unverified'
    return 'not_run'


def execute(project:str,config:Path,output:Path,digest:str,executable:Path,cores:int,
            rerun_unverified:bool=False)->int:
    lockfile=lock_path(output);lockfile.parent.mkdir(parents=True,exist_ok=True)
    with lockfile.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'SKIP running: {project}');return 0
        if sha256(config)!=digest:raise ValueError('stale config snapshot; regenerate jobs with --overwrite')
        # We hold the lock; inspect evidence without testing our own lock.
        state=task_state(project,config,output,locked=True)
        if state=='completed_unchanged':
            print(f'SKIP completed_unchanged: {project}');return 0
        if state=='unverified' and not rerun_unverified:
            print(f'BLOCK unverified existing result: {project}; use --rerun-unverified');return 2
        data=yaml.safe_load(config.read_text())
        if data['project']['name']!=project or output_dir(config)/project!=output:
            raise ValueError('project/output differs from task snapshot; regenerate jobs')
        output.mkdir(parents=True,exist_ok=True)
        record={'version':1,'project':project,'config':str(config.resolve()),'config_sha256':digest,
                'started_at':datetime.now(timezone.utc).isoformat(),'status':'running'}
        atomic_json(output/RECORD,record)
        try:
            for command in ([str(executable),'check',str(config)],
                            [str(executable),'run',str(config),'--cores',str(cores)]):
                if sha256(config)!=digest:raise ValueError('configuration changed during execution')
                result=subprocess.run(command,check=False,pass_fds=(lock.fileno(),))
                record[command[1]+'_returncode']=result.returncode
                if result.returncode:raise ValueError(f'OpenBench {command[1]} failed: {result.returncode}')
            if sha256(config)!=digest:raise ValueError('configuration changed during execution; success not recorded')
        except BaseException as exc:
            record.update(status='failed',finished_at=datetime.now(timezone.utc).isoformat(),error=str(exc))
            atomic_json(output/RECORD,record)
            raise
        record.update(status='success',success_at=datetime.now(timezone.utc).isoformat())
        atomic_json(output/RECORD,record)
        print(f'FINISH {project}');return 0


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mapping',type=Path,required=True);p.add_argument('--mapping-sha256',required=True)
    p.add_argument('--index',type=int,required=True);p.add_argument('--openbench',type=Path,required=True)
    p.add_argument('--cores',type=int,required=True);p.add_argument('--rerun-unverified',action='store_true')
    a=p.parse_args()
    content=a.mapping.read_bytes()
    if hashlib.sha256(content).hexdigest()!=a.mapping_sha256:
        raise ValueError('stale mapping snapshot; regenerate jobs with --overwrite')
    rows=[line.split('\t') for line in content.decode().splitlines()[1:]]
    row=next((row for row in rows if row[0]==str(a.index)),None)
    if row is None or len(row)!=5:raise ValueError('invalid task mapping index/schema')
    _,project,config,output,digest=row
    return execute(project,Path(config),Path(output),digest,a.openbench,a.cores,a.rerun_unverified)


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
