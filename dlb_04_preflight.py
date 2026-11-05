#!/usr/bin/env python3
"""02 — Read-only CoLM AAS preflight: inputs, Spinup prerequisites and E0–E7 readiness.

Checks configuration/time consistency, shared datasets, landdata, case restart,
GridRiverLake transaction/feature metadata, executable, and optional monthly forcing files.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from dlb_00_workflow_common import (WorkflowError,load_study,select,assignments,string,number,
 path_value,stamp,timefiles,constfiles,validate_restart_filenames,gridriver_file,
 gridriver_integrity,probe_netcdf,preprocessor_state,ROUTING_FIELDS,LEVEE_FIELDS,BIF_FIELDS,
 DAM_FIELDS,error_exit)


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nml-dir',required=True,type=Path)
    p.add_argument('--phase',choices=['inputs','spinup','dam-init','handoff','experiments','all'],default='all')
    p.add_argument('--only',default='all',help='experiments subset')
    p.add_argument('--exe',type=Path,help='Absolute or relative compiled colm.x path (required for run phases)')
    p.add_argument('--repo-root',type=Path,help='Optional CoLM checkout for include/define.h diagnostics')
    p.add_argument('--strict-macros',action='store_true',help='Fail on mismatched key source macros')
    p.add_argument('--check-netcdf',action='store_true',help='Open routing/reservoir NetCDF and check names')
    p.add_argument('--check-forcing-files',action='store_true',help='Check each JRA3Q monthly file by name (1996-2005)')
    args=p.parse_args()
    study=load_study(args.nml_dir)
    selected=select(study,args.only)
    errors=[];warnings=[];ok=[]

    def add(condition:bool,message:str,kind:str='error')->None:
        (ok if condition else (warnings if kind=='warn' else errors)).append(message)

    ref=study.spinup.conf
    add(study.case_root.is_dir(),f'case-root 存在: {study.case_root}')
    for key in ('DEF_dir_rawdata','DEF_dir_runtime','DEF_file_SoilInit','DEF_UnitCatchment_file','DEF_ReservoirPara_file'):
        path=path_value(ref,key)
        add(path.exists(),f'{key}: {path}')

    forcing_nml=path_value(ref,'DEF_forcing_namelist')
    add(forcing_nml.is_file(),f'强迫 namelist: {forcing_nml}')
    if forcing_nml.is_file():
        f=assignments(forcing_nml)
        add(string(f,'DEF_forcing%dataset').upper()=='JRA3Q','forcing dataset 应为 JRA3Q')
        cover=(number(f,'DEF_forcing%startyr'),int(f.get('def_forcing%startmo','1')),
               number(f,'DEF_forcing%endyr'),int(f.get('def_forcing%endmo','12')))
        add((cover[0],cover[1])<=(1996,1) and (cover[2],cover[3])>=(2005,12),
            f'forcing metadata 时间覆盖: {cover}')
        force_root=path_value(f,'DEF_dir_forcing')
        add(force_root.is_dir(),f'强迫数据根目录: {force_root}')
        if args.check_forcing_files and force_root.is_dir():
            add(string(f,'DEF_forcing%groupby').lower()=='month',
                '此逐月文件名检查只支持 DEF_forcing%groupby = month')
            for i in range(1,9):
                key=f'DEF_forcing%fprefix({i})'
                prefix=string(f,key)
                pp=force_root/prefix
                parent=pp.parent
                if not parent.is_dir():
                    errors.append(f'{key} 文件夹缺失: {parent}');continue
                # Filename-based check; does not prove NetCDF data values or time axis.
                candidates=[x.name for x in parent.iterdir() if x.is_file() and x.name.startswith(pp.name)]
                missing=[]
                for year in range(1996,2006):
                    for month in range(1,13):
                        yyyymm=f'{year:04d}{month:02d}'
                        yyyym=f'{year:04d}-{month:02d}'
                        yyyy_m=f'{year:04d}_{month:02d}'
                        if not any(yyyymm in name or yyyym in name or yyyy_m in name
                                   for name in candidates):
                            missing.append(yyyymm)
                add(not missing, f'{key} 逐月文件名 {120-len(missing)}/120；缺失示例: {missing[:4]}')
        elif force_root.is_dir():
            warnings.append('forcing 仅验证了配置/目录，未逐月验证实际文件；可使用 --check-forcing-files')

    all_runs=(study.spinup,*(([study.dam_init]) if study.dam_init else []),*study.experiments)
    for run in all_runs:
        for key in ('DEF_UnitCatchment_file','DEF_ReservoirPara_file','DEF_forcing_namelist','DEF_file_SoilInit'):
            add(path_value(run.conf,key)==path_value(ref,key),f'{run.label} 与 SPINUP 的 {key} 一致')
        add(string(run.conf,'DEF_WRST_FREQ').upper() in ('MONTHLY','YEARLY'),
            f'{run.label} restart 输出频率可提供年度边界状态')
        add(number(run.conf,'DEF_Runoff_SCHEME')==3,f'{run.label} Simple VIC runoff=3')
        add(not (run.conf.get('def_gridriverlake_floodfeedback','').lower()!='.false.'),
            f'{run.label} 洪泛→陆面反馈关闭')
    if args.repo_root:
        macros=args.repo_root.resolve()/'include/define.h'
        if not macros.is_file():
            errors.append(f'源码宏文件不存在: {macros}')
        else:
            state=preprocessor_state(macros)
            for key,expect in {'GRIDBASED':True,'GridRiverLakeFlow':True,'USEMPI':True,
                               'CaMa_Flood':False,'BGC':False,'CROP':False,
                               'MaterialTransport':False}.items():
                add(state.get(key)==expect,f'include/define.h {key} = {expect}',
                    'error' if args.strict_macros else 'warn')
            if not (state.get('LULC_IGBP_PFT') or state.get('LULC_IGBP_PC')):
                errors.append('既未定义 LULC_IGBP_PFT，也未定义 LULC_IGBP_PC')
            if not state.get('LULC_IGBP_PFT'):
                warnings.append('项目提纲计划使用 LULC_IGBP_PFT，当前源码可能是 LULC_IGBP_PC；需与初始场一致并完整重编')
            warnings.append('define.h 检查只能验证源码宏，不能证明现有 colm.x 的编译配置')

    if args.check_netcdf:
        route=path_value(ref,'DEF_UnitCatchment_file')
        damfile=path_value(ref,'DEF_ReservoirPara_file')
        if route.is_file():
            names,_=probe_netcdf(route)
            missing=set(ROUTING_FIELDS+LEVEE_FIELDS+BIF_FIELDS)-names
            add(not missing,f'河网 NetCDF 基础+堤防+分汊参数字段；缺: {sorted(missing)}')
        if damfile.is_file():
            names,_=probe_netcdf(damfile)
            missing=set(DAM_FIELDS)-names
            add(not missing,f'水库参数字段；缺: {sorted(missing)}')
    else:
        warnings.append('尚未检查路由 NetCDF 变量；可以加 --check-netcdf')

    if args.phase in ('spinup','dam-init','experiments','all'):
        exe=args.exe.resolve() if args.exe else None
        add(exe is not None and exe.is_file() and bool(exe.stat().st_mode & 0o111),
            f'CoLM 可执行文件: {exe or "未指定 --exe"}')
        if exe:
            needed=(('mksrfdata.x','mkinidata.x','colm.x') if args.phase in ('spinup','all') else
                    ('mkinidata.x',) if args.phase=='dam-init' else ('colm.x',))
            for name in needed:add((exe.parent/name).is_file(),f'阶段可执行文件: {exe.parent/name}')
    if args.phase in ('spinup','all'):
        run=study.spinup
        add(not (run.case_dir/'history').exists(),f'SPINUP 尚无 history（避免覆盖已有运行）')

    if args.phase in ('dam-init','handoff','all'):
        if not study.dam_init:
            errors.append('manifest 未定义 D_INIT')
        else:
            add((study.dam_init.case_dir/'landdata').is_dir(),
                f'D_INIT landdata 链接已就绪: {study.dam_init.case_dir/"landdata"}')
        fs=timefiles(study.spinup,2000);cs=constfiles(study.spinup)
        misses=validate_restart_filenames(fs,study.spinup,2000)+validate_restart_filenames(cs,study.spinup,None)
        add(not misses,f'SPINUP 2000 restart 可供交接: {misses[:5]}')

    if args.phase in ('handoff','all') and study.dam_init:
        fs=timefiles(study.dam_init,2000)
        try:
            grid=gridriver_file(fs,study.dam_init,2000)
            bad,notes=gridriver_integrity(grid,1,False,False)
            add(not bad,f'D_INIT gridriver 可供 D 相关组交接: {bad[:4]}')
            warnings.extend(f'D_INIT: {x}' for x in notes)
        except WorkflowError as exc:errors.append(f'D_INIT gridriver 读取失败: {exc}')

    if args.phase in ('experiments','all'):
        river_source=study.spinup.case_dir/'riverlake/unitcatchment_regional.nc'
        for run in selected:
            add((run.case_dir/'landdata').is_dir(),f'{run.label} landdata 已就绪')
            regional=run.case_dir/'riverlake/unitcatchment_regional.nc'
            add(regional.is_symlink() and regional.is_file() and
                regional.resolve()==river_source.resolve(),
                f'{run.label} regional routing 链接已就绪: {regional}')
            fs=timefiles(run,2000);cs=constfiles(run)
            misses=validate_restart_filenames(fs,run,2000)+validate_restart_filenames(cs,run,None)
            if misses:
                errors.append(f'{run.label} 2000 初始 restart 不完整: {misses[:5]}')
                continue
            try:
                grid=gridriver_file(fs,run,2000)
                bad,notes=gridriver_integrity(grid,run.dam,run.levee,run.bif)
                add(not bad,f'{run.label} gridriver restart 检查: {bad[:4]}')
                warnings.extend(f'{run.label}: {n}' for n in notes)
            except WorkflowError as exc:
                errors.append(f'{run.label} gridriver 读取失败: {exc}')
            if not (run.case_dir/'.aas_restart_handoff.json').is_file():
                warnings.append(f'{run.label}: 未找到 .aas_restart_handoff.json，可能是人工分发；请核对来源')
    for text in ok: print('PASS ',text)
    for text in warnings:print('WARN ',text)
    for text in errors:print('FAIL ',text)
    print(f'\nSUMMARY: {len(ok)} PASS, {len(warnings)} WARN, {len(errors)} FAIL')
    return 1 if errors else 0


if __name__=='__main__':
    try:sys.exit(main())
    except (WorkflowError,ValueError,OSError) as exc:sys.exit(error_exit(exc))
