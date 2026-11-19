"""Read existing OpenBench station products without reprocessing the simulation."""
from pathlib import Path
import hashlib
import json
import re
import numpy as np
import pandas as pd
import xarray as xr

ITEMS = {'inflow': 'Dam_Inflow', 'outflow': 'Dam_Outflow', 'storage': 'Dam_Water_Storage'}

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def canonical_id(value):
    text = str(value).strip()
    if not re.fullmatch(r'[0-9]+(?:\.0+)?', text):
        raise ValueError(f'Invalid GRanD ID: {value!r}')
    return str(int(float(text)))


def unit_factor(unit, variable):
    """Convert processed file units, never the original manifest units."""
    u = str(unit).lower().replace('³', '3').replace('^', '').replace('**', '')
    u = u.replace(' ', '').replace('_', '').replace('−', '-')
    if variable == 'storage':
        if u in {'mcm', 'millioncubicmeters', 'millioncubicmetres', '106m3', '1e6m3'}:
            return 1.
        if u in {'m3', 'cubicmeters', 'cubicmetres'}:
            return 1e-6
    else:
        if u in {'m3/s', 'm3s-1', 'm3sec-1', 'm3s−1'}:
            return 1.
        if u in {'cfs', 'ft3/s', 'ft3s-1'}:
            return .028316846592
    raise ValueError(f'Unsupported {variable} units: {unit!r}')


def read_station_table(path):
    frame = pd.read_csv(path, dtype={'ID': str})
    frame['ID'] = frame.ID.map(canonical_id)
    if frame.ID.duplicated().any():
        raise ValueError(f'Duplicate IDs: {path}')
    return frame


def discover(root, simulation=None, reference=None, ids=None):
    root = Path(root).resolve()
    manifest = json.loads((root/'run_manifest.json').read_text())
    config = manifest['canonical_config']
    sims = list(config['simulation'])
    if simulation is None:
        if len(sims) != 1:
            raise ValueError(f'Select --simulation from {sims}')
        simulation = sims[0]
    if simulation not in sims:
        raise ValueError(f'Unknown simulation {simulation}; choices: {sims}')
    sources = config['reference']['sources']
    refs = sorted(set(v for item, v in sources.items() if item in ITEMS.values()))
    if reference is None:
        if len(refs) != 1:
            raise ValueError(f'Select --reference from {refs}')
        reference = refs[0]
    enabled = [v for v, item in ITEMS.items() if item in config['evaluation']['variables'] and sources.get(item) == reference]
    if not enabled:
        raise ValueError('No supported dam variables for this reference')
    project = config['project']
    if str(project['tim_res']).lower() not in {'day', 'daily', '1d', 'd'}:
        raise ValueError('This tool currently supports daily reservoir evaluations only')
    start, end = [int(x) for x in project['years']]
    stations_path = root/f'stn_{reference}_{simulation}_list.csv'
    stations = read_station_table(stations_path)
    flags = stations.get('Flag', pd.Series(True, index=stations.index)).astype(str).str.lower().isin(['true', '1'])
    stations = stations.loc[flags].copy()
    if ids is not None:
        requested = {canonical_id(i) for i in ids}
        missing = requested-set(stations.ID)
        if missing:
            raise ValueError(f'IDs not in final matched station list: {sorted(missing)}')
        stations = stations[stations.ID.isin(requested)].copy()
    if stations.empty:
        raise ValueError('No matched stations selected')
    tables, matching, inventory = {}, {}, [root/'run_manifest.json', stations_path]
    for v in enabled:
        item = ITEMS[v]
        path = root/'metrics'/f'{item}_stn_{reference}_{simulation}_evaluations.csv'
        tables[v] = read_station_table(path) if path.exists() else pd.DataFrame(columns=['ID'])
        if path.exists():
            inventory.append(path)
        path = root/'reservoir_matching'/f'{item}_{reference}_{simulation}.json'
        if path.exists():
            matching[v] = json.loads(path.read_text())
            inventory.append(path)
        else:
            matching[v] = {}
    return dict(root=root, manifest=manifest, config=config, simulation=simulation, reference=reference,
                variables=enabled, start=start, end=end, stations=stations, metrics=tables,
                matching=matching, inventory=inventory)


def find_files(context, variable, side, identifier):
    """Only search this source pair; exclude jpg/report copies and unrelated runs."""
    root = context['root']; ref = context['reference']; sim = context['simulation']
    directories = [root/'data'/f'stn_{ref}_{sim}', root/'data'/f'stn__{ref}__{sim}']
    paths = []
    prefix = f'{ITEMS[variable]}_{side}_{identifier}_'
    for directory in directories:
        paths.extend(directory.glob(prefix+'*.nc'))
    return sorted(set(paths))


def read_series(paths, variable, varname):
    parts, provenance = [], []
    for path in paths:
        with xr.open_dataset(path) as ds:
            if varname not in ds:
                raise ValueError(f'{path}: missing declared variable {varname}')
            data = ds[varname]
            if 'time' not in data.dims:
                raise ValueError(f'{path}: variable has no time dimension')
            for dim in tuple(data.dims):
                if dim != 'time':
                    if data.sizes[dim] != 1:
                        raise ValueError(f'{path}: station variable has non-singleton {dim}')
                    data = data.isel({dim: 0})
            index = pd.DatetimeIndex(data.time.values)
            if index.hasnans or not index.is_monotonic_increasing or index.duplicated().any():
                raise ValueError(f'{path}: invalid, duplicate or unsorted timestamps')
            if index.normalize().duplicated().any():
                raise ValueError(f'{path}: multiple records per day')
            unit = data.attrs.get('units', '')
            factor = unit_factor(unit, variable)
            raw = np.asarray(data.values, dtype=float)
            # NetCDF fill values are decoded by xarray; reject nonfinite and obvious sentinels.
            bad = ~np.isfinite(raw) | (np.abs(raw) >= 1e30)
            raw[bad] = np.nan
            parts.append(pd.Series(raw*factor, index=index))
            provenance.append(dict(path=str(path), variable=varname, units=unit, factor=factor,
                                   rows=len(raw), sha256=sha(path), size=path.stat().st_size,
                                   first=str(index[0]) if len(index) else None,
                                   last=str(index[-1]) if len(index) else None))
    if not parts:
        return pd.Series(dtype=float), provenance
    series = pd.concat(parts).sort_index()
    if series.index.duplicated().any() or series.index.normalize().duplicated().any():
        raise ValueError('Overlapping station files or duplicate dates; cannot choose a record silently')
    return series, provenance


def load_station(context, row):
    lo = max(context['start'], int(row.get('use_syear', context['start'])))
    hi = min(context['end'], int(row.get('use_eyear', context['end'])))
    if lo > hi:
        raise ValueError(f'{row.ID}: station years outside project years')
    start, end = pd.Timestamp(lo, 1, 1), pd.Timestamp(hi, 12, 31)
    namelists = context['manifest']['resolved_runtime']['namelists']
    columns, audits, records = {}, [], []
    hours = set()
    for v in context['variables']:
        item = ITEMS[v]; audit = dict(ID=row.ID, variable=v, requested_start=str(start.date()),
                                      requested_end=str(end.date()), units='MCM' if v == 'storage' else 'm3/s')
        for side, source, group in [('ref', context['reference'], 'reference'), ('sim', context['simulation'], 'simulation')]:
            paths = find_files(context, v, side, row.ID)
            varname = namelists[group][item][f'{source}_varname']
            series, prov = read_series(paths, v, varname)
            records.extend(prov)
            if len(series):
                series = series[(series.index.normalize() >= start) & (series.index.normalize() <= end)]
            hours.update(int(x) for x in ((series.index-series.index.normalize()).total_seconds() if len(series) else []))
            columns[f'{"obs" if side == "ref" else "sim"}_{v}'] = series
            audit[side+'_files'] = ';'.join(str(p) for p in paths)
            audit[side+'_finite_n'] = int(np.isfinite(series).sum())
            skips = []
            if not paths:
                for directory in [context['root']/'data'/f'stn_{context["reference"]}_{context["simulation"]}', context['root']/'data'/f'stn__{context["reference"]}__{context["simulation"]}']:
                    for p in directory.glob(f'{item}_{side}_{row.ID}_*.skip.txt'):
                        skips.append(p.read_text().strip());records.append(dict(path=str(p),sha256=sha(p),size=p.stat().st_size))
            audit[side+'_skip_reason'] = ';'.join(skips)
        audit['in_evaluation_table'] = row.ID in set(context['metrics'][v].ID)
        audits.append(audit)
    # Never silently normalize different physical timestamps to the same calendar label.
    if len(hours) > 1:
        raise ValueError(f'{row.ID}: inconsistent daily timestamp offsets {sorted(hours)}; audit alignment first')
    offset = pd.Timedelta(seconds=next(iter(hours), 43200))
    index = pd.date_range(start+offset, end+offset, freq='D')
    frame = pd.DataFrame(columns).reindex(index)
    for v in ITEMS:
        for side in ['obs', 'sim']:
            col = f'{side}_{v}'
            if col not in frame:
                frame[col] = np.nan
    frame.index.name = 'time'
    for audit in audits:
        v = audit['variable']; n = int(np.isfinite(frame[[f'obs_{v}',f'sim_{v}']]).all(axis=1).sum())
        audit.update(n_pair=n, expected_days=len(frame), coverage=n/len(frame), timestamp_offset_seconds=offset.total_seconds(),
                     status='PAIRED' if n else 'NO_VALID_PAIRS')
    return frame, audits, records


def read_parameters(context, override=None):
    if override:
        path = Path(override)
    else:
        history = Path(context['config']['simulation'][context['simulation']]['root_dir'])
        path = history.parent/'riverlake/unitcatchment_regional.nc'
    if not path.is_file():
        if override:
            raise FileNotFoundError(path)
        return pd.DataFrame(columns=['ID']), None
    with xr.open_dataset(path) as ds:
        data = {'ID':[canonical_id(x) for x in ds.dam_GRAND_ID.values]}
        for name, field in [('parameter_name','dam_DamName'),('capacity_mcm','dam_TotalVol_mcm'),('lon','dam_DamLon'),('lat','dam_DamLat'),('dam_seq','dam_seq')]:
            if field in ds:
                values = ds[field].values
                if field == 'dam_TotalVol_mcm':
                    values = values*unit_factor(ds[field].attrs.get('units',''), 'storage')
                data[name] = [v.decode() if isinstance(v,bytes) else v for v in values]
    result = pd.DataFrame(data)
    if result.ID.duplicated().any():
        raise ValueError('Duplicate dam IDs in parameter file')
    return result, path
