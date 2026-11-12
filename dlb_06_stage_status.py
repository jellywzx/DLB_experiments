#!/usr/bin/env python3
"""04 — Read-only AAS status for SPINUP, D_INIT, handoff, and E0–E7."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nml-dir',required=True,type=Path)
    p.add_argument('--jobs-dir',type=Path)
    p.add_argument('--json',action='store_true')
    p.add_argument('--require-complete',action='store_true')
    p.add_argument('--allow-nonstandard-schedule',action='store_true',
                   help=argparse.SUPPRESS)
    p.add_argument('--allow-draft',action='store_true',help=argparse.SUPPRESS)
    args=p.parse_args()
    study=load_study(args.nml_dir,allow_draft=args.allow_draft,validate_schedule=False)
    jobdirs=((args.jobs_dir.resolve(),) if args.jobs_dir else
             (study.nml_dir/'jobs_lsf',study.nml_dir/'jobs_slurm',study.nml_dir.parent/'jobs'))
    records=[]

    spin_restart,spin_notes=restart_ready(study.spinup,2000)
    spin_marker=marker(jobdirs,'SPINUP')
    records.append({'stage':'spinup','case':study.spinup.case,
        'complete':spin_restart and spin_marker,'restart_2000':spin_restart,
        'success_marker':spin_marker,'details':spin_notes[:6]})

    dam_complete=False
    if study.dam_init:
        dam_restart,dam_notes=restart_ready(study.dam_init,2000,grid_only=True)
        dam_marker=marker(jobdirs,'D_INIT')
        dam_complete=dam_restart and dam_marker
        records.append({'stage':'dam_init','case':study.dam_init.case,
            'complete':dam_complete,'gridriver_2000':dam_restart,
            'success_marker':dam_marker,'details':dam_notes[:6]})

    handoff_rows=[]
    for run in study.experiments:
        ready,notes=restart_ready(run,2000)
        record=(run.case_dir/'.aas_restart_handoff.json').is_file()
        handoff_rows.append({'experiment':run.label,'ready':ready,
                             'record':record,'details':notes[:4]})
    handoff_complete=all(x['ready'] and x['record'] for x in handoff_rows)
    records.append({'stage':'handoff','complete':handoff_complete,'experiments':handoff_rows})

    experiment_rows=[]
    for run in study.experiments:
        final,notes=restart_ready(run,2006)
        count,total,missing=month_files(run,study.eval_start_year,study.eval_end_year)
        succeeded=marker(jobdirs,run.label)
        experiment_rows.append({'experiment':run.label,'case':run.case,
            'complete':final and count==total and succeeded,'final_restart_2006':final,
            'monthly_history':count,'monthly_expected':total,
            'success_marker':succeeded,'missing_month_examples':missing[:4],
            'details':notes[:4]})
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
