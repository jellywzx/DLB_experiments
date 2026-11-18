#!/usr/bin/env python3
"""Convert CoLM (time, reservoir) output into GRanD-ID matched station files.

Default is metadata-only preview. --apply extracts the requested variable and
atomically updates ONE existing OpenBench YAML, backing it up in a new snapshot.
No OpenBench evaluation or scheduler command is executed.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

import numpy as np
import xarray as xr
import yaml

VARIABLES = {
    'Dam_Inflow': ('qresv_in', 'm3 s-1'),
    'Dam_Outflow': ('qresv_out', 'm3 s-1'),
    'Dam_Water_Storage': ('volresv', 'm3'),
}
LIST_NAMES = {0.1: 'ResOpsUS_reservoir_matched_06min.csv',
              0.25: 'ResOpsUS_reservoir_matched_15min.csv'}


def reservoir_ids(ds: xr.Dataset) -> dict[int, int]:
    if 'resv_GRAND_ID' not in ds or ds.resv_GRAND_ID.dims != ('reservoir',):
        raise ValueError('missing resv_GRAND_ID(reservoir); never infer IDs from array order or nearest coordinates')
    values = np.asarray(ds.resv_GRAND_ID.values)
    if not np.all(np.isfinite(values)) or np.any(values <= 0) or np.any(values != np.floor(values)):
        raise ValueError('invalid/non-positive resv_GRAND_ID')
    ids = [int(value) for value in values]
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate resv_GRAND_ID; cannot choose a reservoir column')
    return {station: index for index, station in enumerate(ids)}


def source_unit(value: str, variable: str) -> str:
    unit = value.lower().replace('^', '').replace(' ', '')
    allowed = {'m3'} if variable == 'volresv' else {'m3/s', 'm3s-1'}
    if unit not in allowed:
        raise ValueError(f'{variable}: unsupported source units {value!r}; explicit conversion is required')
    return 'm3' if variable == 'volresv' else 'm3 s-1'


def read_stations(path: Path, project: dict) -> list[dict]:
    expected = LIST_NAMES.get(float(project['grid_res']))
    if path.name != expected or path.parent.name != 'list':
        raise ValueError(f'use prepared list/{expected}; ResOpsUS selects this filename by grid resolution')
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        required = {'ID', 'SYEAR', 'EYEAR', 'LON', 'LAT', 'Flag', 'DIR'}
        if not required <= set(reader.fieldnames or []):
            raise ValueError('station list is missing required columns')
        rows = list(reader)
    ids = [int(row['ID']) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate observation IDs; prepare the reference list first')
    lat_min, lat_max = project.get('lat_range', [-90, 90])
    lon_min, lon_max = project.get('lon_range', [-180, 180])
    start, end = project['years']
    selected = []
    for row in rows:
        if (row['Flag'].lower() in {'1', 'true'}
                and lat_min <= float(row['LAT']) <= lat_max
                and lon_min <= float(row['LON']) <= lon_max
                and int(row['SYEAR']) <= end and int(row['EYEAR']) >= start):
            if not Path(row['DIR']).is_absolute() or not Path(row['DIR']).is_file():
                raise ValueError(f'prepare absolute valid observation DIR first: {row["DIR"]}')
            selected.append(row)
    if not selected:
        raise ValueError('no ResOpsUS observations in the requested region/years; cannot evaluate Amazon with this list')
    return selected


def history_files(sim: dict, variable: str, years: list[int]) -> list[Path]:
    if sim.get('data_type') != 'grid' or sim.get('data_groupby') != 'Month':
        raise ValueError('expected original monthly grid configuration from dlb_08; do not convert twice')
    binding = sim['variables'][variable]
    prefix = binding.get('prefix', sim.get('prefix'))
    if not isinstance(prefix, str) or not prefix or Path(prefix).name != prefix:
        raise ValueError('missing/unsafe original history filename prefix')
    root = Path(sim['root_dir']).expanduser().resolve()
    paths = [root/f'{prefix}{year:04d}-{month:02d}.nc'
             for year in range(years[0], years[1]+1) for month in range(1, 13)]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f'missing monthly history ({len(missing)}): {missing[0]}')
    return paths


def inspect_history(paths: list[Path], name: str) -> set[int]:
    """Read IDs and time metadata only; compute IDs present in every month."""
    common = None
    for path in paths:
        with xr.open_dataset(path) as ds:
            ids = set(reservoir_ids(ds))
            if name not in ds or set(ds[name].dims) != {'time', 'reservoir'} or ds[name].ndim != 2:
                raise ValueError(f'{path}: missing {name}(time, reservoir); CTL may not emit reservoir output')
            source_unit(ds[name].attrs.get('units', ''), name)
            if not ds.sizes.get('time'):
                raise ValueError(f'{path}: empty time axis')
            common = ids if common is None else common & ids
    return common or set()


def extract(paths: list[Path], name: str, ids: list[int], missing_value: float) -> xr.DataArray:
    pieces = []
    for path in paths:
        with xr.open_dataset(path) as ds:
            mapping = reservoir_ids(ds)
            source_unit(ds[name].attrs.get('units', ''), name)
            data = ds[name].isel(reservoir=[mapping[station] for station in ids]).transpose('time', 'reservoir').load()
            # Decode CF fill values in xarray, plus CoLM's unannotated -1.e36.
            data = data.where(np.isfinite(data) & ~np.isclose(data, missing_value, rtol=1e-6, atol=0))
            data = xr.DataArray(data.values, dims=('time', 'station'),
                                coords={'time': ds.time.values, 'station': ids})
            pieces.append(data)
    combined = xr.concat(pieces, dim='time')
    index = combined.indexes['time']
    if not index.is_monotonic_increasing or index.has_duplicates:
        raise ValueError('history time must be increasing and unique; overlapping months/restarts require review')
    # Do not guess daily end-of-window offsets or alter time coordinates.
    for station in ids:
        if not bool(combined.sel(station=station).notnull().any()):
            raise ValueError(f'{name}: station {station} has no valid simulation samples')
    return combined


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True, help='One generated reservoir YAML to update')
    parser.add_argument('--station-list', type=Path, required=True, help='Prepared ResOpsUS 06min or 15min CSV')
    parser.add_argument('--out-dir', type=Path, required=True, help='Snapshot parent outside config/openbench')
    parser.add_argument('--simulation', action='append', help='Explicit subset of simulation labels; repeatable')
    parser.add_argument('--missing-value', type=float, default=-1e36, help='Unannotated CoLM missing sentinel')
    parser.add_argument('--apply', action='store_true', help='Extract station data and atomically update YAML')
    args = parser.parse_args()
    config_path = args.config.expanduser().resolve()
    original = config_path.read_bytes()
    config = yaml.safe_load(original)
    variables = config['evaluation']['variables']
    if len(variables) != 1 or variables[0] not in VARIABLES:
        raise ValueError('expected one of Dam_Inflow, Dam_Outflow, Dam_Water_Storage')
    variable = variables[0]
    name, unit = VARIABLES[variable]
    if config['reference'].get(variable) != 'ResOpsUS':
        raise ValueError('this adapter requires ResOpsUS')
    if config['project'].get('tim_res') != 'Day':
        raise ValueError('this adapter expects daily evaluation')
    simulations = config['simulation']
    if args.simulation:
        unknown = set(args.simulation) - set(simulations)
        if unknown:
            raise ValueError(f'unknown simulations: {sorted(unknown)}')
        simulations = {label: sim for label, sim in simulations.items() if label in args.simulation}
    if not simulations:
        raise ValueError('no simulations selected')
    if any(not re.fullmatch(r'[A-Za-z0-9_.-]+', label) or label in {'.', '..'} for label in simulations):
        raise ValueError('unsafe simulation label')
    station_list = args.station_list.expanduser().resolve()
    rows = read_stations(station_list, config['project'])
    paths_by_sim = {}
    source_stats = {}
    matched = {int(row['ID']) for row in rows}
    for label, sim in simulations.items():
        paths = history_files(sim, variable, config['project']['years'])
        available = inspect_history(paths, name)
        print(f'{label}: {len(paths)} months, {len(available)} IDs present in every file')
        matched &= available
        paths_by_sim[label] = paths
        for path in paths:
            stat = path.stat()
            source_stats[path] = (stat.st_size, stat.st_mtime_ns)
    if not matched:
        raise ValueError('no common resv_GRAND_ID matched to ResOpsUS across the selected simulations')
    rows = [row for row in rows if int(row['ID']) in matched]
    ids = [int(row['ID']) for row in rows]
    out_dir = args.out_dir.expanduser().resolve()
    for parent in config_path.parents:
        if parent.name == 'openbench' and out_dir.is_relative_to(parent):
            raise ValueError('--out-dir must be outside config/openbench so regeneration cannot delete extracted data')
    print(f'{variable}: matched {len(ids)} common stations by exact GRanD ID: {ids}')
    print(f'Selected simulations: {list(simulations)}; omitted: {sorted(set(config["simulation"])-set(simulations))}')
    print(f'Output snapshot parent: {out_dir}; time coordinates preserved without an assumed offset')
    if not args.apply:
        print('DRY-RUN: metadata only; no time-series arrays read and no files written. Use --apply to extract.')
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    snapshot = Path(tempfile.mkdtemp(prefix='reservoir_', dir=out_dir))
    updated = copy.deepcopy(config)
    updated['simulation'] = {}
    try:
        (snapshot/'original.yaml').write_bytes(original)
        audit = {'matching': 'exact resv_GRAND_ID == ResOpsUS ID; no nearest-neighbour matching',
                 'config_sha256': hashlib.sha256(original).hexdigest(),
                 'station_list': str(station_list),
                 'station_list_sha256': hashlib.sha256(station_list.read_bytes()).hexdigest(),
                 'variable': variable, 'station_ids': ids, 'missing_value': args.missing_value,
                 'omitted_simulations': sorted(set(config['simulation'])-set(simulations)), 'sources': {}}
        for label, sim in simulations.items():
            directory = snapshot/label
            directory.mkdir()
            data = extract(paths_by_sim[label], name, ids, args.missing_value)
            years = [int(data.time.dt.year.min()), int(data.time.dt.year.max())]
            station_csv = directory/'stations.csv'
            with station_csv.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=['ID','SYEAR','EYEAR','LON','LAT','DIR'])
                writer.writeheader()
                for row in rows:
                    station = int(row['ID'])
                    series = data.sel(station=station, drop=True).rename(name).to_dataset()
                    series[name].attrs = {'units': unit, 'source_variable': name}
                    series.attrs['resv_GRAND_ID'] = station
                    series.attrs['matching'] = 'exact GRanD ID'
                    target = directory/f'{station}.nc'
                    series.to_netcdf(target)
                    writer.writerow({'ID':station,'SYEAR':years[0],'EYEAR':years[1],
                                     'LON':row['LON'],'LAT':row['LAT'],'DIR':str(target)})
            entry = copy.deepcopy(sim)
            entry.update(data_type='stn', root_dir=str(directory), data_groupby='single',
                         fulllist=str(station_csv), prefix=None, suffix=None,
                         variables={variable: {'varname':name,'varunit':unit}})
            updated['simulation'][label] = entry
            audit['sources'][label] = [
                {'path':str(path),'size':source_stats[path][0],'mtime_ns':source_stats[path][1]}
                for path in paths_by_sim[label]]
        reference = updated['reference'].setdefault('overrides', {}).setdefault('ResOpsUS', {})
        reference.update(root_dir=str(station_list.parent.parent), fulllist=str(station_list),
                         data_type='stn', tim_res='Day', data_groupby='single',
                         years=[min(int(row['SYEAR']) for row in rows), max(int(row['EYEAR']) for row in rows)])
        updated['comparison']['enabled'] = len(simulations) > 1
        if len(simulations) == 1:
            updated['comparison']['items'] = []
        (snapshot/'matching.json').write_text(json.dumps(audit, indent=2)+'\n')
        text = yaml.safe_dump(updated, sort_keys=False)
        (snapshot/'prepared.yaml').write_text(text)
        for path, expected in source_stats.items():
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != expected:
                raise ValueError(f'history changed during extraction: {path}; wait for model output to finish')
        if config_path.read_bytes() != original:
            raise ValueError('configuration changed during extraction; refusing to replace it')
        # Use a same-directory temporary file for atomic replacement, including
        # when the station snapshot lives on a different filesystem.
        with tempfile.NamedTemporaryFile(mode='w', dir=config_path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        try:
            os.chmod(temporary, config_path.stat().st_mode & 0o777)
            os.replace(temporary, config_path)
        finally:
            temporary.unlink(missing_ok=True)
    except Exception:
        shutil.rmtree(snapshot)
        raise
    print(f'Updated {config_path}; backup and matching audit: {snapshot}')
    print('No evaluation submitted. Regenerate dlb_09 job snapshots after reviewing this YAML.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
