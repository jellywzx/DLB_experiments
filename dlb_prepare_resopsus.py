#!/usr/bin/env python3
"""Prepare ResOpsUS station lists and a user overlay; default is read-only preview.

Only CSV metadata is read. No observation or simulation arrays are processed.
Both routing resolutions are prepared because OpenBench's ResOpsUS filter
chooses list/ResOpsUS_reservoir_matched_{06,15}min.csv from ref.root_dir.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile

import yaml

DEFAULT_SOURCE = Path('/share/home/dq013/zhwei/OpenBench/Reference/Station/Anth/Dam/ResOpsUS')
LIST_NAMES = ('ResOpsUS_reservoir_matched_06min.csv',
              'ResOpsUS_reservoir_matched_15min.csv')
VARIABLES = {
    'Dam_Inflow': {'varname': 'inflow', 'varunit': 'm3 s-1'},
    'Dam_Outflow': {'varname': 'outflow', 'varunit': 'm3 s-1'},
    'Dam_Water_Storage': {'varname': 'storage', 'varunit': 'million cubic meters'},
}


def prepare_list(source: Path, name: str, exclude_duplicates: bool = False) -> tuple[str, list[dict]]:
    """Preserve matching coordinates and IDs; rebase legacy DIR fields."""
    with (source/'list'/name).open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        fields = reader.fieldnames
        required = {'ID', 'SYEAR', 'EYEAR', 'LON', 'LAT', 'Flag', 'DIR'}
        if not fields or not required.issubset(fields):
            raise ValueError(f'{name}: missing CSV columns: {sorted(required-set(fields or []))}')
        rows = list(reader)
    if not rows:
        raise ValueError(f'{name}: empty station list')
    counts = Counter(row['ID'] for row in rows)
    duplicates = sorted(station for station, count in counts.items() if count > 1)
    if duplicates:
        if not exclude_duplicates:
            raise ValueError(f'{name}: ambiguous duplicate IDs: {duplicates}; review the source list or '
                             'use --exclude-duplicate-ids to exclude ALL rows for these IDs')
        print(f'WARNING {name}: excluding ALL rows for duplicate IDs: {duplicates}')
        rows = [row for row in rows if row['ID'] not in duplicates]
    seen = set()
    for row in rows:
        station = row['ID']
        if not station.isascii() or not station.isdigit() or station in seen:
            raise ValueError(f'{name}: invalid/duplicate station ID: {station!r}')
        seen.add(station)
        if int(row['SYEAR']) > int(row['EYEAR']):
            raise ValueError(f'{name}: reversed years for station {station}')
        if not (-180 <= float(row['LON']) <= 180 and -90 <= float(row['LAT']) <= 90):
            raise ValueError(f'{name}: invalid coordinates for station {station}')
        flag = row['Flag'].strip().lower()
        if flag not in {'true', 'false', '1', '0'}:
            raise ValueError(f'{name}: invalid Flag for station {station}')
        # Numeric flags avoid pandas treating the string "False" as truthy.
        row['Flag'] = '1' if flag in {'true', '1'} else '0'
        path = source/'dataset'/f'ResOpsUS_{station}.nc'
        if not path.is_file():
            raise ValueError(f'{name}: missing observation: {path}')
        row['DIR'] = str(path.resolve())
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue(), rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--user-dir', type=Path,
                        help='OpenBench config directory; default: $OPENBENCH_HOME/.openbench or ~/.openbench')
    parser.add_argument('--apply', action='store_true', help='Write prepared lists and user reference overlay')
    parser.add_argument('--overwrite', action='store_true', help='Back up and replace this helper\'s existing overlay')
    parser.add_argument('--exclude-duplicate-ids', action='store_true',
                        help='Exclude ALL rows for ambiguous repeated IDs; never choose one coordinate automatically')
    args = parser.parse_args()
    source = args.source_dir.expanduser().resolve()
    user_dir = (args.user_dir or Path(os.environ.get('OPENBENCH_HOME', str(Path.home())))/'.openbench').expanduser().resolve()
    overlay = user_dir/'references/dlb_resopsus.yaml'
    prepared = {}
    all_rows = []
    for name in LIST_NAMES:
        content, rows = prepare_list(source, name, args.exclude_duplicate_ids)
        prepared[name] = content
        all_rows.extend(rows)
        print(f'{name}: {len(rows)} stations; DIR -> {source}/dataset/ResOpsUS_<ID>.nc')
    active = [row for row in all_rows if row['Flag'] == '1']
    if not active:
        raise ValueError('no enabled ResOpsUS stations')
    years = [min(int(row['SYEAR']) for row in active), max(int(row['EYEAR']) for row in active)]
    print(f'Reference coverage envelope from enabled CSV rows: {years} (not per-variable availability)')
    print(f'User overlay: {overlay}')
    if overlay.exists() and not args.overwrite:
        raise ValueError(f'overlay exists: {overlay}; use --overwrite to back it up and replace it')
    print('No NetCDF arrays read; reservoir-to-station simulation matching still requires validation.')
    if not args.apply:
        print('DRY-RUN: no files written; use --apply to prepare lists and register ResOpsUS for this user.')
        return 0
    overlay.parent.mkdir(parents=True, exist_ok=True)
    snapshots = user_dir/'dlb_resopsus'
    snapshots.mkdir(parents=True, exist_ok=True)
    snapshot = Path(tempfile.mkdtemp(prefix='snapshot_', dir=snapshots))
    try:
        (snapshot/'list').mkdir()
        for name, content in prepared.items():
            (snapshot/'list'/name).write_text(content, encoding='utf-8')
        if overlay.exists():
            backup = snapshot/'previous_dlb_resopsus.yaml'
            shutil.copy2(overlay, backup)
            print(f'Previous overlay backed up: {backup}')
        catalog = {'ResOpsUS': {
            'name': 'ResOpsUS', 'description': 'ResOpsUS with DLB absolute station paths',
            'category': 'Water', 'root_dir': str(snapshot), 'data_type': 'stn',
            'tim_res': 'Day', 'data_groupby': 'single', 'timezone': 0,
            'years': years, 'variables': VARIABLES,
        }}
        temporary = snapshot/'overlay.tmp'
        temporary.write_text(yaml.safe_dump(catalog, sort_keys=False), encoding='utf-8')
        os.replace(temporary, overlay)
    except Exception:
        shutil.rmtree(snapshot)
        raise
    print(f'Prepared reference lists: {snapshot}/list')
    print('Registered ResOpsUS. Restart OpenBench processes to reload the user registry.')
    if args.user_dir:
        print('With --user-dir, configure the OpenBench process to use the same user directory.')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, csv.Error) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(2)
