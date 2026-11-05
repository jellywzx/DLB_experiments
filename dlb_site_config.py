"""Server profiles and read-only CoLM source/input compatibility checks."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
from pathlib import Path

PATH_KEYS = {'repo_root','case_root','experiments_root','rawdata','runtime','soil_init',
             'mpi_root','intel_env','mask_root','template','exe','workdir','forcing_root'}
STRING_KEYS = {'forcing_nml','scheduler','queue','memory','walltime','launcher','node'}
INT_KEYS = {'cores','tasks_per_node'}
BOOL_KEYS = {'exclusive','no_lsf'}
KEYS = PATH_KEYS | STRING_KEYS | INT_KEYS | BOOL_KEYS


def load_profile(path: Path) -> dict:
    path = path.expanduser().resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) - KEYS:
        raise ValueError(f'Invalid server profile keys: {path}')
    for key, value in data.items():
        if value is None:
            if key not in {'mpi_root','intel_env','node'}:
                raise ValueError(f'{key} cannot be null')
        elif key in INT_KEYS and (type(value) is not int or value < 1):
            raise ValueError(f'{key} must be a positive integer')
        elif key in BOOL_KEYS and type(value) is not bool:
            raise ValueError(f'{key} must be boolean')
        elif key in PATH_KEYS | STRING_KEYS and not isinstance(value, str):
            raise ValueError(f'{key} must be a string')
        if key in PATH_KEYS and value is not None:
            p = Path(value).expanduser()
            data[key] = (path.parent / p).resolve() if not p.is_absolute() else p
    for key, choices in {'scheduler': {'slurm','lsf'}, 'launcher': {'mpirun','srun','yhrun'}}.items():
        if key in data and data[key] not in choices:
            raise ValueError(f'Invalid {key}: {data[key]}')
    return data


def parse_site_args(parser, *, use_env=True, recorded_options=()):
    """CLI profile > DLB_SITE_CONFIG > legacy defaults.

    Existing-experiment operations retain their recorded settings unless a CLI
    profile is explicitly provided. --no-site-config disables automatic loading.
    """
    help_text='JSON server profile (default: DLB_SITE_CONFIG); explicit options override it'
    selection=parser.add_mutually_exclusive_group()
    selection.add_argument('--site-config',type=Path,help=help_text)
    selection.add_argument('--no-site-config',action='store_true',
                           help='Ignore DLB_SITE_CONFIG for this command')
    preliminary=argparse.ArgumentParser(add_help=False)
    preliminary.add_argument('--site-config',type=Path)
    preliminary.add_argument('--no-site-config',action='store_true')
    preliminary.add_argument('-h','--help',action='store_true')
    for option in recorded_options:preliminary.add_argument(option)
    selected,_=preliminary.parse_known_args()
    if selected.help:
        return parser.parse_args()  # argparse prints help without opening a profile
    recorded=any(getattr(selected,option.lstrip('-').replace('-','_')) is not None
                 for option in recorded_options)
    profile_path=selected.site_config
    source='cli' if profile_path else 'default'
    if selected.no_site_config:
        source='disabled'
    elif not profile_path and use_env and not recorded:
        env_path=os.environ.get('DLB_SITE_CONFIG')
        if env_path:
            profile_path=Path(env_path)
            source='environment'
    profile=load_profile(profile_path) if profile_path else {}
    actions={a.dest:a for a in parser._actions}
    for key in profile:
        if key in actions:actions[key].required=False
    parser.set_defaults(**{k:v for k,v in profile.items() if k in actions},
                        site_config=profile_path)
    args=parser.parse_args()
    for key,value in vars(args).items():
        if key in PATH_KEYS and isinstance(value,Path):
            setattr(args,key,value.expanduser().absolute())
    effective={**profile,**{k:v for k,v in vars(args).items() if k in KEYS}}
    args.site_metadata={
        'path':str(profile_path.expanduser().absolute()) if profile_path else None,
        'sha256':hashlib.sha256(profile_path.expanduser().read_bytes()).hexdigest() if profile_path else None,
        'selection':source,
        'effective':{k:str(v) if isinstance(v,Path) else v for k,v in effective.items()}}
    return args


def basin_specs(basins, region, mask_root, original_root):
    return tuple((name,lats,lons,mask_root / mask.relative_to(original_root) if mask else None)
                 for name,lats,lons,mask in basins[region])


def unsupported_fields(repo: Path, texts: list[str]) -> list[str]:
    """Check nl_colm roots against NAMELIST, and members against their derived type declarations.

    This does not preprocess macros or validate the already-built executable.
    """
    path = repo / 'share/MOD_Namelist.F90'
    if not path.is_file():
        return [f'Missing model namelist source: {path}']
    source = '\n'.join(line.split('!',1)[0] for line in path.read_text().splitlines() if not line.lstrip().startswith('#'))
    joined = re.sub(r'&\s*\n\s*&?', ' ', source)
    match = re.search(r'\bnamelist\s*/\s*nl_colm\s*/([^\n]*)',joined,re.I)
    if not match:
        return [f'Cannot find NAMELIST /nl_colm/ declaration: {path}']
    declared = set(re.findall(r'\bdef_\w+',match[1].lower()))
    keys = set()
    for text in texts:
        if re.search(r'^\s*&nl_colm\s*$',text,re.I|re.M):
            keys.update(re.findall(r'^\s*(DEF_[\w%]+)\s*=',text,re.I|re.M))
    joined_source = re.sub(r'&\s*\n\s*&?', ' ', source).lower()
    types = {}
    for declaration in re.finditer(r'^\s*type\s*(?:::\s*)?(\w+)\s*\n(.*?)^\s*end\s*type\b',
                                   joined_source,re.M|re.S):
        fields = set()
        for line in declaration[2].splitlines():
            if '::' in line:
                for item in line.split('::',1)[1].split(','):
                    field = re.match(r'\s*(\w+)',item)
                    if field:fields.add(field[1])
        types[declaration[1]] = fields
    bindings = {root:typ for typ,root in re.findall(
        r'type\s*\(\s*(\w+)\s*\)\s*::\s*(def_\w+)',joined_source)}
    errors = []
    for key in sorted(keys,key=str.lower):
        root,_,member = key.lower().partition('%')
        if root not in declared or (member and member not in types.get(bindings.get(root),set())):
            errors.append(f'Unsupported namelist field {key}: {path}')
    return errors


def input_issues(args, regions, basins=None):
    issues = []
    repo = args.repo_root
    checks = [(args.rawdata,'directory'),(args.runtime,'directory'),(args.case_root,'directory'),
              (args.soil_init,'file'),(repo/'run/forcing'/f'{args.forcing_nml}.nml','file')]
    for region in regions:
        res = '15min' if region == 'Global' else '06min'
        checks.append((args.runtime/'unitcatchment'/f'grid_routing_data_{res}.nc','file'))
    for name in ('mksrfdata.x','mkinidata.x','colm.x'):
        path = repo/'run'/name
        if not path.is_file() or not os.access(path,os.X_OK):
            issues.append(f'Missing/non-executable model binary: {path}')
    for specs in basins or []:
        for _,_,_,mask in specs:
            if mask: checks.append((mask,'file'))
    forcing = repo/'run/forcing'/f'{args.forcing_nml}.nml'
    if forcing.is_file():
        from dlb_00_workflow_common import assignments,path_value
        actual = path_value(assignments(forcing),'DEF_dir_forcing')
        checks.append((actual,'directory'))
        expected = getattr(args,'forcing_root',None)
        if expected and actual.resolve() != expected.resolve():
            issues.append(f'Forcing root mismatch: {actual}; expected {expected}')
    for path,kind in dict.fromkeys(checks):
        if not (path.is_dir() if kind == 'directory' else path.is_file()):
            issues.append(f'Missing {kind}: {path}')
    return issues


def audit_site(args):
    """Audit a profile without creating experiments or cases; return errors and notes."""
    from dlb_08_generate_openbench import BASINS,MASK_ROOT
    from dlb_02_generate_namelists import BASE_TEMPLATE,validate_forcing,check_macros
    from dlb_00_workflow_common import assignments,string,path_value,probe_netcdf,ROUTING_FIELDS,LEVEE_FIELDS,BIF_FIELDS,DAM_FIELDS
    regions = [args.region] if args.region else list(BASINS)
    specs = [basin_specs(BASINS,r,args.mask_root,MASK_ROOT) for r in regions]
    errors = input_issues(args,regions,specs)
    template = args.template or args.repo_root/BASE_TEMPLATE
    if not template.is_file():
        errors.append(f'Missing base template: {template}')
    else:
        # Fields added to the local base by the workflow, including D/L/B switches.
        extra = ('DEF_UnitCatchment_regional','DEF_ReservoirPara_file','DEF_GridRiverLake_FloodFeedback',
                 'DEF_Reservoir_Method','DEF_USE_LEVEE','DEF_USE_BIFURCATION','DEF_HIST_vars_namelist')
        synthetic = '&nl_colm\n'+''.join(f'{key} = 0\n' for key in extra)+'/\n'
        errors.extend(unsupported_fields(args.repo_root,[template.read_text(),synthetic]))
    forcing = args.repo_root/'run/forcing'/f'{args.forcing_nml}.nml'
    if forcing.is_file():
        try: validate_forcing(forcing,False)
        except ValueError as exc: errors.append(str(exc))
    notes = check_macros(args.repo_root)
    if args.strict_macros:
        errors.extend(x for x in notes if ': define.h=' in x)
    if args.check_netcdf:
        for res in sorted({'15min' if r=='Global' else '06min' for r in regions}):
            path = args.runtime/'unitcatchment'/f'grid_routing_data_{res}.nc'
            if path.is_file():
                try:
                    names,_ = probe_netcdf(path)
                    missing = set(ROUTING_FIELDS+LEVEE_FIELDS+BIF_FIELDS+DAM_FIELDS)-names
                    if missing:errors.append(f'Routing/reservoir NetCDF {path} missing: {sorted(missing)}')
                except Exception as exc:errors.append(f'Cannot inspect NetCDF {path}: {exc}')
    else:notes.append('Routing/reservoir NetCDF variable checks not requested (--check-netcdf).')
    if args.check_forcing_files and forcing.is_file():
        errors.extend(forcing_month_issues(forcing))
    else:notes.append('Forcing files not checked month by month (--check-forcing-files).')
    notes.append('Source checks do not prove compiled binary compatibility; no model or scheduler command was executed.')
    return list(dict.fromkeys(errors)),notes


def forcing_month_issues(forcing: Path):
    """Check YYYYMM, YYYY-MM and YYYY_MM monthly file names, 1996–2005."""
    from dlb_00_workflow_common import assignments,string,path_value
    f=assignments(forcing)
    if string(f,'DEF_forcing%groupby').lower()!='month':
        return ['Monthly forcing check requires groupby=month']
    root=path_value(f,'DEF_dir_forcing')
    errors=[]
    for i in range(1,9):
        prefix=root/string(f,f'DEF_forcing%fprefix({i})')
        names=[p.name for p in prefix.parent.glob(prefix.name+'*') if p.is_file()]
        missing=[f'{y:04d}{m:02d}' for y in range(1996,2006) for m in range(1,13)
                 if not any(re.search(rf'(?<!\d){y:04d}[-_]?{m:02d}(?!\d)',n) for n in names)]
        if missing:errors.append(f'Forcing {prefix}: {len(missing)} missing months; examples {missing[:4]}')
    return errors
