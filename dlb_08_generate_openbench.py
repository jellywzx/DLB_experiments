#!/usr/bin/env python3
"""Generate or atomically replace OpenBench YAML for one DLB experiment."""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime,timezone
from pathlib import Path

import yaml

from dlb_site_config import parse_site_args,basin_specs

MASK_ROOT=Path('/share/home/dq134/wzx/openbench/postprocess/basin_mask/outputs')
BASINS={
    'USA':(
        ('missouri_before_confluence',[36.0,51.0],[-115.0,-89.0],
         MASK_ROOT/'basin_masks_06min/missouri/missouri_basin.nc'),
        ('mississippi_cairo_to_mouth',[28.0,40.0],[-108.0,-88.0],
         MASK_ROOT/'basin_masks_06min/lower_mississippi/lower_mississippi_basin.nc'),
    ),
    'SA':(('amazon',[-22.0,11.0],[-82.0,-35.0],MASK_ROOT/'basin_masks_06min/amazon/amazon_basin.nc'),),
    'Mekong':(('mekong',[9.0,34.0],[93.0,109.0],MASK_ROOT/'basin_masks_06min/mekong/mekong_basin.nc'),),
    'Global':(('global',[-90.0,90.0],[-180.0,180.0],None),),
}
TREATMENTS=('E1_D','E2_L','E3_B','E4_DL','E5_DB','E6_LB','E7_ALL')


TEMPLATE_ROOT=Path(__file__).resolve().parent/'templates/openbench'


class TemplateLoader(yaml.SafeLoader):
    """Reject duplicate mapping keys instead of silently discarding template fields."""


def unique_mapping(loader,node):
    result={}
    for key_node,value_node in node.value:
        key=loader.construct_object(key_node,deep=True)
        if key in result:raise ValueError(f'duplicate YAML field: {key}')
        result[key]=loader.construct_object(value_node,deep=True)
    return result


TemplateLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,unique_mapping)


def load_templates(directory:Path)->dict[str,dict]:
    paths=sorted([*directory.glob('*.yaml'),*directory.glob('*.yml')])
    if not paths:raise ValueError(f'no variable YAML templates found: {directory}')
    templates={};variables=set()
    for path in paths:
        try:
            template=yaml.load(path.read_text(encoding='utf-8'),Loader=TemplateLoader)
            if not isinstance(template,dict):raise ValueError('expected a YAML mapping')
            for section in ('project','evaluation','reference','comparison','statistics'):
                if not isinstance(template.get(section),dict):
                    raise ValueError(f'missing or invalid {section} mapping')
            if not isinstance(template['project'].get('tim_res'),str) or not template['project']['tim_res']:
                raise ValueError('missing or invalid project.tim_res')
            selected=template['evaluation'].get('variables')
            if (not isinstance(selected,list) or len(selected)!=1 or
                not isinstance(selected[0],str) or not selected[0]):
                raise ValueError('evaluation.variables must contain exactly one variable')
            variable=selected[0]
            if set(template['reference'])!={variable} or not template['reference'][variable]:
                raise ValueError(f'reference must define only {variable}')
            for label,values in (('metrics',template.get('metrics')),('scores',template.get('scores')),
                                 ('comparison.items',template['comparison'].get('items')),
                                 ('statistics.items',template['statistics'].get('items'))):
                if not isinstance(values,list) or any(not isinstance(x,str) or not x for x in values):
                    raise ValueError(f'missing or invalid {label} list')
            if not isinstance(template['statistics'].get('enabled'),bool):
                raise ValueError('missing or invalid statistics.enabled')
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',path.stem):
                raise ValueError(f'unsafe output template name: {path.stem}')
            if path.stem in templates:raise ValueError(f'output naming conflict: {path.stem}')
            if variable in variables:raise ValueError(f'duplicate variable: {variable}')
        except (OSError,UnicodeError,yaml.YAMLError,ValueError,TypeError) as exc:
            raise ValueError(f'invalid OpenBench template {path}: {exc}') from exc
        variables.add(variable);templates[path.stem]=template
    return templates


def openbench_config(template:dict,project:str,output:Path,years:tuple[int,int],grid:float,
                     basin,runs:list[dict])->dict:
    _,lats,lons,mask=basin
    config=copy.deepcopy(template)
    config['project'].update(name=project,output_dir=str(output),years=list(years),
                             min_year_threshold=1,grid_res=grid,
                             lat_range=list(lats),lon_range=list(lons),num_cores=2)
    config['project'].pop('basin_mask',None)
    if mask:config['project']['basin_mask']=str(mask)
    # Variable names, units and time offsets belong to OpenBench's CoLM2024 profile.
    config['simulation']={run['label']:{
        'model':'CoLM2024','root_dir':str(run['history_root']),'data_type':'grid',
        'tim_res':'Day','grid_res':grid,'data_groupby':'Month',
        'variables':{variable:{'prefix':run['prefix'],'suffix':None}
                     for variable in config['evaluation']['variables']}} for run in runs}
    config['comparison']['enabled']=len(runs)>1
    if len(runs)==1:config['comparison']['items']=[]
    return config


def write_json_atomic(path:Path,value:dict)->None:
    temporary=path.with_name(f'.{path.name}.tmp.{os.getpid()}')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    os.replace(temporary,path)


def main()->int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('experiment_dir',type=Path)
    p.add_argument('--template-dir',type=Path,default=TEMPLATE_ROOT,
                   help='Directory of variable YAML templates (default: %(default)s)')
    p.add_argument('--eval-start-year',type=int)
    p.add_argument('--eval-end-year',type=int)
    p.add_argument('--output-root',type=Path,
                   help=argparse.SUPPRESS)
    p.add_argument('--no-refresh',action='store_true',help=argparse.SUPPRESS)
    p.add_argument('--overwrite',action='store_true',
                   help='Atomically replace only config/openbench and back up the old YAML')
    p.add_argument('--mask-root',type=Path,help='Basin masks; default from recorded server configuration')
    args=parse_site_args(p,use_env=False)  # Existing experiment keeps its recorded mask root
    templates=load_templates(args.template_dir)
    root=args.experiment_dir.resolve();manifest_path=root/'manifest.yaml'
    if not manifest_path.is_file():raise ValueError(f'missing experiment manifest: {manifest_path}')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    region=manifest['region'];name=manifest['name']
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',name):
        raise ValueError(f'unsafe experiment name: {name!r}')
    if region not in BASINS:raise ValueError(f'unsupported region: {region}')
    colm_path=root/manifest['configuration']['colm']
    colm=json.loads(colm_path.read_text(encoding='utf-8'))
    default_eval=manifest.get('evaluation',{})
    start=args.eval_start_year if args.eval_start_year is not None else default_eval.get('start_year',2001)
    end=args.eval_end_year if args.eval_end_year is not None else default_eval.get('end_year',2005)
    if not 2000<=start<=end<=2005:raise ValueError('evaluation years must be within 2000–2005')
    saved=manifest.get('site_config',{}).get('effective',{})
    mask_root=args.mask_root or Path(default_eval.get('mask_root',saved.get('mask_root',MASK_ROOT)))
    basins=basin_specs(BASINS,region,mask_root,MASK_ROOT)
    mask_issues=[f'missing basin mask: {mask}' for _,_,_,mask in basins if mask and not mask.is_file()]
    if mask_issues and not colm.get('draft'):raise ValueError('\n'.join(mask_issues))
    for issue in mask_issues:print('WARNING [draft]: '+issue,file=sys.stderr)
    case_root=Path(colm['paths']['case_root'])
    runs=colm['runs']['experiments'][region]
    labels=[x['label'] for x in runs]
    if len(labels)!=len(set(labels)):raise ValueError('duplicate CoLM experiment labels')
    missing={'E0_CTL',*TREATMENTS}-set(labels)
    if missing:raise ValueError('missing CoLM experiments: '+', '.join(sorted(missing)))
    by_label={x['label']:{'label':x['label'],'history_root':case_root/x['case']/'history',
                          'prefix':x['case']+'_hist_unitcat_'} for x in runs}
    spinup=colm['runs']['spinup'][region]
    spinup_run={'label':'spinup','history_root':case_root/spinup['case']/'history',
                'prefix':spinup['case']+'_hist_unitcat_'}
    output_root=(args.output_root.resolve() if args.output_root else root/'openbench_output')
    destination=root/'config/openbench'
    has_existing=destination.exists() and any(destination.iterdir())
    if has_existing and not args.overwrite:
        raise ValueError(f'OpenBench configuration already exists: {destination}; use --overwrite')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=Path(tempfile.mkdtemp(prefix='.openbench.',dir=str(destination.parent)))
    old_projects={x['project']:x for x in default_eval.get('projects',[])}
    projects=[];grid=0.25 if region=='Global' else 0.1
    combinations=[('DLB' if label=='E7_ALL' else label.split('_',1)[1],label,
                   [by_label['E0_CTL'],by_label[label]],(start,end)) for label in TREATMENTS]
    combinations.extend([('spinup','spinup',[spinup_run],(1996,1999)),
                         ('ctl','ctl',[by_label['E0_CTL']],(start,end)),
                         ('comparisons','comparisons',
                          [by_label[label] for label in ('E0_CTL',*TREATMENTS)],(start,end))])
    seen_projects=set();seen_configs=set();seen_outputs=set()
    try:
        for basin in basins:
            basin_dir=temporary/basin[0];basin_dir.mkdir(exist_ok=True)
            for short,label,selected_runs,years in combinations:
                for kind,template in templates.items():
                    project=f'{name}_{basin[0]}_{kind}_{short}'
                    filename=f'{kind}_{short}.yaml';path=basin_dir/filename
                    relative=f'config/openbench/{basin[0]}/{filename}'
                    result_dir=output_root/project
                    for value,seen in ((project,seen_projects),(relative,seen_configs),(result_dir,seen_outputs)):
                        if value in seen:raise ValueError(f'output naming conflict: {value}')
                        seen.add(value)
                    config=openbench_config(template,project,output_root,years,grid,basin,selected_runs)
                    text=yaml.safe_dump(config,sort_keys=False,allow_unicode=True)
                    path.write_text(text,encoding='utf-8')
                    old=old_projects.get(project,{});old_config=root/old.get('config','')
                    status=(old.get('status','missing') if old_config.is_file() and
                            old_config.read_text(encoding='utf-8')==text else 'missing')
                    projects.append({'project':project,'config':relative,'output_dir':str(result_dir),
                                     'basin':basin[0],'type':kind,'treatment':label,'status':status})
        expected=len(basins)*len(templates)*len(combinations)
        if len(projects)!=expected:raise ValueError(f'internal YAML count error: {len(projects)} != {expected}')
        backup=None
        if has_existing:
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup=root/'provenance/backups'/stamp
            backup.mkdir(parents=True)
            shutil.copytree(destination,backup/'openbench')
            shutil.copy2(manifest_path,backup/'manifest.yaml')
        old_dir=None
        if destination.exists():
            old_dir=destination.with_name(f'.openbench.old.{os.getpid()}')
            os.rename(destination,old_dir)
        try:
            os.rename(temporary,destination)
            manifest.setdefault('configuration',{})['openbench']='config/openbench'
            manifest['evaluation']={'start_year':start,'end_year':end,'projects':projects,
                                    'mask_root':str(mask_root),'validation_issues':mask_issues}
            manifest['openbench_generated_at']=datetime.now(timezone.utc).isoformat()
            write_json_atomic(manifest_path,manifest)
        except Exception:
            if destination.exists():shutil.rmtree(destination)
            if old_dir and old_dir.exists():os.rename(old_dir,destination)
            raise
        if old_dir and old_dir.exists():shutil.rmtree(old_dir)
    finally:
        if temporary.exists():shutil.rmtree(temporary,ignore_errors=True)
    print(f'Generated {len(projects)} OpenBench YAML files at {destination}')
    if has_existing:
        print(f'Previous OpenBench configuration backed up at {backup}')
        print('WARNING: Regenerating YAML does not re-evaluate existing results. '
              'DLB jobs skip nonempty output directories; use OpenBench directly with '
              '--force after backing up results when re-evaluation is required.',file=sys.stderr)
    if not args.no_refresh:
        refresh=Path(__file__).resolve().parent/'dlb_07_refresh_experiment.py'
        result=subprocess.run([sys.executable,str(refresh),str(root)],text=True,
                              capture_output=True,check=False)
        if result.returncode:raise ValueError(f'refresh failed: {result.stderr or result.stdout}')
        print(result.stdout.strip())
    return 0


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
