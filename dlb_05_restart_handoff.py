#!/usr/bin/env python3
"""01 — Transactional D_INIT preparation and E0–E7 restart handoff.

The default is a read-only dry run.  Only --apply creates landdata and regional
routing links, restart directories, and per-case handoff records.  Source files
are never modified and existing restart/history products are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

from dlb_00_workflow_common import (WorkflowError, constfiles, error_exit,
    gridriver_file, gridriver_integrity, load_study, select, stamp, timefiles,
    validate_restart_filenames, validate_study_schedule)


def renamed(path:Path,source_case:str,target_case:str)->str:
    prefix=source_case+'_restart_'
    if not path.name.startswith(prefix):
        raise WorkflowError(f'拒绝改名非 restart 文件: {path.name}')
    return target_case+path.name[len(source_case):]


def digest(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()


def copy_one(source:Path,target:Path,source_case:str,target_case:str,hashes:bool)->dict:
    destination=target/renamed(source,source_case,target_case)
    shutil.copy2(source,destination)
    if source.stat().st_size!=destination.stat().st_size:
        raise WorkflowError(f'复制后文件大小不同: {destination}')
    item={'source':str(source),'target':destination.name,'bytes':destination.stat().st_size}
    if hashes:
        before,after=digest(source),digest(destination)
        if before!=after:raise WorkflowError(f'SHA256 不一致: {destination}')
        item['sha256']=after
    return item


def verify_empty_target(run)->list[str]:
    issues=[]
    if run.restart.exists() or run.restart.is_symlink():issues.append(f'restart 已存在: {run.restart}')
    if run.history.is_dir() and any(run.history.iterdir()):issues.append(f'history 已有内容: {run.history}')
    if (run.case_dir/'.aas_restart_handoff.json').exists():issues.append('交接记录已存在')
    land=run.case_dir/'landdata'
    if land.exists() or land.is_symlink():issues.append(f'landdata 已存在，拒绝替换: {land}')
    river=run.case_dir/'riverlake'
    if river.exists() or river.is_symlink():issues.append(f'riverlake 已存在，拒绝替换: {river}')
    return issues


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',nargs='?',choices=['prepare-dam-init','handoff'],default='handoff')
    p.add_argument('--nml-dir',required=True,type=Path)
    p.add_argument('--only',default='all',help='handoff: all or E0_CTL,E2_L,...')
    p.add_argument('--validate-scope',choices=['selected','all'],default='selected',
                   help='handoff schedule validation scope (default: selected)')
    p.add_argument('--apply',action='store_true')
    p.add_argument('--hash',action='store_true')
    p.add_argument('--allow-draft',action='store_true',help=argparse.SUPPRESS)
    args=p.parse_args()
    study=load_study(args.nml_dir,allow_draft=args.allow_draft,validate_schedule=False)
    land_source=study.spinup.case_dir/'landdata'
    if not land_source.is_dir():raise WorkflowError(f'SPINUP landdata 不存在: {land_source}')

    if args.action=='prepare-dam-init':
        validate_study_schedule(study)
        if not study.dam_init:raise WorkflowError('manifest 未定义 D_INIT')
        run=study.dam_init
        land=run.case_dir/'landdata'
        if land.exists() or land.is_symlink():raise WorkflowError(f'目标已存在，拒绝覆盖: {land}')
        print(f'D_INIT landdata: {land} -> {land_source.resolve()}')
        if not args.apply:
            print('DRY RUN 完成。无文件写入；确认后追加 --apply。')
            return 0
        run.case_dir.mkdir(parents=True,exist_ok=True)
        land.symlink_to(land_source.resolve(),target_is_directory=True)
        print('D_INIT landdata 链接已创建；未运行模型或提交作业。')
        return 0

    targets=select(study,args.only)
    validate_study_schedule(study,None if args.validate_scope=='all' else targets)
    spin=study.spinup
    river_source=spin.case_dir/'riverlake/unitcatchment_regional.nc'
    if not river_source.is_file():
        raise WorkflowError(f'SPINUP regional routing 不存在: {river_source}')
    spin_time=timefiles(spin,2000)
    spin_const=constfiles(spin)
    bad=validate_restart_filenames(spin_time,spin,2000)+validate_restart_filenames(spin_const,spin,None)
    if bad:raise WorkflowError('SPINUP restart 不完整: '+'; '.join(bad))
    spin_grid=gridriver_file(spin_time,spin,2000)
    dam_grid=None
    if any(run.dam for run in targets):
        if not study.dam_init:raise WorkflowError('D 相关组需要 manifest 中的 D_INIT')
        dam_files=timefiles(study.dam_init,2000)
        dam_grid=gridriver_file(dam_files,study.dam_init,2000)
        errors,_=gridriver_integrity(dam_grid,1,False,False)
        if errors:raise WorkflowError('D_INIT gridriver 不完整: '+'; '.join(errors))
    errors=[]
    for run in targets:
        errors.extend(f'{run.label}: {x}' for x in verify_empty_target(run))
        donor=dam_grid if run.dam else spin_grid
        grid_errors,notes=gridriver_integrity(donor,run.dam,run.levee,run.bif)
        errors.extend(f'{run.label}: {x}' for x in grid_errors)
        print(f'{run.label:7} non-grid=SPINUP gridriver={"D_INIT" if run.dam else "SPINUP"}')
        print(f'        regional routing -> {river_source.resolve()}')
        for note in notes:print('  NOTE:',note)
    if errors:raise WorkflowError('交接预检失败:\n  '+'\n  '.join(errors))
    if not args.apply:
        print('DRY RUN 完成。无文件写入；确认后追加 --apply。')
        return 0

    stage=Path(tempfile.mkdtemp(prefix='.aas_handoff_',dir=str(study.case_root)))
    installed=[];created_cases=[]
    try:
        plans={}
        for run in targets:
            root=stage/run.case
            const_dir=root/'const';time_dir=root/stamp(2000)
            const_dir.mkdir(parents=True);time_dir.mkdir()
            files={'const':[],'time':[]}
            for source in spin_const:
                files['const'].append(copy_one(source,const_dir,spin.case,run.case,args.hash))
            for source in spin_time:
                if source==spin_grid and run.dam:continue
                files['time'].append(copy_one(source,time_dir,spin.case,run.case,args.hash))
            if run.dam:
                files['time'].append(copy_one(dam_grid,time_dir,study.dam_init.case,run.case,args.hash))
            staged=list(time_dir.glob('*.nc'))
            missing=validate_restart_filenames(staged,run,2000)
            if missing:raise WorkflowError(f'{run.label} 临时组装验证失败: {missing}')
            plans[run.label]=(root,files)

        for run in targets:
            existed=run.case_dir.exists()
            run.case_dir.mkdir(parents=True,exist_ok=True)
            if not existed:created_cases.append(run.case_dir)
            root,files=plans[run.label]
            run.restart.mkdir()
            installed.append(run.restart)
            (root/'const').rename(run.restart/'const')
            (root/stamp(2000)).rename(run.restart/stamp(2000))
            land=run.case_dir/'landdata'
            land.symlink_to(land_source.resolve(),target_is_directory=True)
            installed.append(land)
            river=run.case_dir/'riverlake'
            river.mkdir()
            installed.append(river)
            regional=river/'unitcatchment_regional.nc'
            regional.symlink_to(river_source.resolve())
            record={'schema_version':2,'source_case':spin.case,
                'gridriver_source_case':study.dam_init.case if run.dam else spin.case,
                'regional_routing_source':str(river_source.resolve()),
                'target_case':run.case,'stamp':stamp(2000),
                'switches':{'dam':run.dam,'levee':run.levee,'bifurcation':run.bif},
                'files':files,'verification':'sha256' if args.hash else 'size'}
            record_path=run.case_dir/'.aas_restart_handoff.json'
            record_path.write_text(json.dumps(record,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
            installed.append(record_path)
            print(f'INSTALLED {run.label}')
    except Exception:
        for path in reversed(installed):
            if path.is_symlink() or path.is_file():path.unlink(missing_ok=True)
            elif path.is_dir():shutil.rmtree(path)
        for path in reversed(created_cases):
            try:path.rmdir()
            except OSError:pass
        raise
    finally:
        shutil.rmtree(stage,ignore_errors=True)
    print('交接完成；源文件未修改，未运行 CoLM 或提交作业。')
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (WorkflowError,OSError,ValueError) as exc:sys.exit(error_exit(exc))
