#!/usr/bin/env python3
"""04 — Read-only AAS status for SPINUP, D_INIT, handoff, and E0–E7."""
# 用法：python3 dlb_06_stage_status.py --nml-dir /path/to/config/colm --json --workers 4
# 默认 4 个检查进程；--workers 1 使用串行检查，进度写入 stderr。
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dlb_parallel import add_workers_argument, ordered_checks

from dlb_00_workflow_common import (WorkflowError, constfiles, error_exit,
    gridriver_file, gridriver_integrity, load_study, month_files, timefiles,
    validate_restart_filenames)


def marker(dirs:tuple[Path,...],label:str)->bool:
    return any((root/'markers'/(label+'.success')).is_file() for root in dirs)


def restart_ready(run,year:int,grid_only:bool=False)->tuple[bool,list[str]]:
    files=timefiles(run,year)
    if grid_only:
        try:
            path=gridriver_file(files,run,year)
            errors,notes=gridriver_integrity(path,run.dam,run.levee,run.bif)
            return not errors,errors+notes
        except WorkflowError as exc:return False,[str(exc)]
    errors=validate_restart_filenames(files,run,year)+validate_restart_filenames(constfiles(run),run,None)
    if errors:return False,errors
    try:
        path=gridriver_file(files,run,year)
        bad,notes=gridriver_integrity(path,run.dam,run.levee,run.bif)
        return not bad,bad+notes
    except WorkflowError as exc:return False,[str(exc)]


def check_run(task)->dict:
    kind,run,jobdirs,start_year,end_year=task
    if kind in ('spinup','dam_init'):
        ready,notes=restart_ready(run,2000,grid_only=kind=='dam_init')
        succeeded=marker(jobdirs,'SPINUP' if kind=='spinup' else 'D_INIT')
        return {'stage':kind,'case':run.case,'complete':ready and succeeded,
                'restart_2000' if kind=='spinup' else 'gridriver_2000':ready,
                'success_marker':succeeded,'details':notes[:6]}
    ready,notes=restart_ready(run,2000)
    handoff={'experiment':run.label,'ready':ready,
             'record':(run.case_dir/'.aas_restart_handoff.json').is_file(),'details':notes[:4]}
    final,notes=restart_ready(run,2006)
    count,total,missing=month_files(run,start_year,end_year)
    succeeded=marker(jobdirs,run.label)
    experiment={'experiment':run.label,'case':run.case,
        'complete':final and count==total and succeeded,'final_restart_2006':final,
        'monthly_history':count,'monthly_expected':total,
        'success_marker':succeeded,'missing_month_examples':missing[:4],'details':notes[:4]}
    return {'handoff':handoff,'experiment':experiment}


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nml-dir',required=True,type=Path)
    p.add_argument('--jobs-dir',type=Path)
    p.add_argument('--json',action='store_true')
    p.add_argument('--require-complete',action='store_true')
    p.add_argument('--allow-nonstandard-schedule',action='store_true',
                   help=argparse.SUPPRESS)
    p.add_argument('--allow-draft',action='store_true',help=argparse.SUPPRESS)
    add_workers_argument(p)
    args=p.parse_args()
    study=load_study(args.nml_dir,allow_draft=args.allow_draft,validate_schedule=False)
    jobdirs=((args.jobs_dir.resolve(),) if args.jobs_dir else
             (study.nml_dir/'jobs_lsf',study.nml_dir/'jobs_slurm',study.nml_dir.parent/'jobs'))
    records=[]

    tasks=[('spinup',study.spinup,jobdirs,study.eval_start_year,study.eval_end_year)]
    if study.dam_init:
        tasks.append(('dam_init',study.dam_init,jobdirs,study.eval_start_year,study.eval_end_year))
    tasks.extend(('experiment',run,jobdirs,study.eval_start_year,study.eval_end_year)
                 for run in study.experiments)
    checked=ordered_checks(check_run,tasks,args.workers,'CoLM status',processes=True)
    records.append(checked[0])
    dam_complete=False
    offset=1
    if study.dam_init:
        records.append(checked[1]);dam_complete=checked[1]['complete'];offset=2
    handoff_rows=[item['handoff'] for item in checked[offset:]]
    handoff_complete=all(x['ready'] and x['record'] for x in handoff_rows)
    records.append({'stage':'handoff','complete':handoff_complete,'experiments':handoff_rows})
    experiment_rows=[item['experiment'] for item in checked[offset:]]
    colm_complete=all(x['complete'] for x in experiment_rows)
    records.append({'stage':'experiments','complete':colm_complete,'experiments':experiment_rows})
    result={'case_root':str(study.case_root),'spinup_complete':records[0]['complete'],
            'dam_init_complete':dam_complete,'handoff_complete':handoff_complete,
            'colm_complete':colm_complete,'records':records}
    if args.json:print(json.dumps(result,indent=2,ensure_ascii=False))
    else:
        print(f'Case root: {study.case_root}')
        for item in records:print(f"{item['stage']:12} {'COMPLETE' if item['complete'] else 'INCOMPLETE'}")
        for row in experiment_rows:
            print(f"  {row['experiment']:7} history={row['monthly_history']:02d}/{row['monthly_expected']:02d} marker={row['success_marker']} final={row['final_restart_2006']}")
    return 1 if args.require_complete and not colm_complete else 0


if __name__=='__main__':
    try:sys.exit(main())
    except (WorkflowError,OSError,ValueError) as exc:sys.exit(error_exit(exc))
