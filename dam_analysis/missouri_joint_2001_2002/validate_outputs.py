#!/usr/bin/env python3
"""Validate real analysis artifacts without modifying inputs or prior results."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

from analyze import TARGETS, VARS, metrics, norm, stock_metrics


def validate(root):
    checks = []

    def check(condition, label):
        if not condition:
            raise AssertionError(label)
        checks.append(label)

    manifest = json.loads((root/'provenance/manifest.json').read_text())
    audit = pd.read_csv(root/'tables/reservoir_matching_audit.csv')
    table = pd.read_csv(root/'tables/reservoir_metrics.csv')
    diag = pd.read_csv(root/'tables/reservoir_diagnostics.csv')
    check(len(audit) == 6 and len(table) == 18 and len(diag) == 6, 'six targets and 18 variable rows')
    check(set(audit.grand_id.dropna().astype(int)) == {307, 753, 870, 884, 895}, 'verified target IDs; 2356 excluded')
    check((audit.loc[audit.reservoir == 'Fort Randall', 'status'] == 'NO_REFERENCE').all(), 'Fort Randall NO_REFERENCE')
    check((diag.full_water_balance_status == '无法完整检验').all(), 'no unsupported numerical conservation claims')
    check(all(x['raw_csv_matches_processed_netcdf'] and x['finite_mask_equal']
              for x in manifest['observation_crosschecks']) and len(manifest['observation_crosschecks']) == 15,
          'all 15 raw CSV versus processed NetCDF comparisons agree including missing masks')
    check(len(manifest['history_files']) == 24 and all(x['status'] == 'READ' for x in manifest['history_files']),
          '24 actual monthly histories read')
    check(manifest['script_sha256'] == hashlib.sha256((Path(__file__).parent/'analyze.py').read_bytes()).hexdigest(),
          'analysis script matches run provenance')
    index = pd.date_range(manifest['args']['start'], manifest['args']['end'])
    for name, ident, _ in TARGETS:
        frame = pd.read_csv(root/f'data/{norm(name)}_daily.csv', index_col='date', parse_dates=True)
        check(frame.index.equals(index) and not frame.index.has_duplicates, f'{name}: daily date grid')
        record = audit.loc[audit.reservoir == name].iloc[0]
        if ident is None:
            check(frame.isna().all().all(), 'Fort Randall has no fabricated data')
        else:
            check(record.location_distance_km < 5, f'{name}: spatial match')
        for variable in VARS:
            row = table.loc[(table.reservoir == name) & (table.variable == variable)].iloc[0]
            expected = metrics(frame[f'obs_{variable}'], frame[f'sim_{variable}'])
            check(int(row['n']) == expected['n'] == int(record[f'{variable}_paired_n']), f'{name}/{variable}: paired counts')
            check(row.units == ('MCM' if variable == 'storage' else 'm3/s'), f'{name}/{variable}: units')
            if variable == 'storage' and ident is not None:
                expected, _ = stock_metrics(frame.obs_storage, frame.sim_storage, frame.index, record.capacity_mcm)
                for key in ['rmse', 'mean_bias', 'correlation', 'nrmse_capacity']:
                    check(pd.isna(row[key]), f'{name}/storage: strict {key} withheld')
                    check(np.isclose(row[f'provisional_{key}'], expected[key], equal_nan=True), f'{name}/storage: exploratory {key}')
            else:
                for key in ['kge', 'nse', 'correlation', 'pbias_pct', 'rsd', 'mean_ratio', 'rmse']:
                    check(np.isclose(row[key], expected[key], equal_nan=True), f'{name}/{variable}: {key}')
        for kind in ['inflow_outflow_storage'] + (['regulation_proxies'] if ident is not None else []):
            stem = root/'figures'/f'{norm(name)}_{kind}'
            with Image.open(stem.with_suffix('.png')) as im:
                check(im.width > 2000 and im.height > 1500 and all(abs(x-300) < 1 for x in im.info['dpi']), f'{name}/{kind}: 300 dpi PNG')
                im.verify()
            check(stem.with_suffix('.pdf').read_bytes().startswith(b'%PDF-'), f'{name}/{kind}: PDF')
    check(len(list((root/'figures').glob('*.png'))) == 11 and len(list((root/'figures').glob('*.pdf'))) == 11,
          'six joint plots and five process plots in both formats')
    # Real missing samples must survive the exported calendar grid.
    bigbend = pd.read_csv(root/'data/bigbend_daily.csv')
    check(bigbend.obs_outflow.isna().sum() == 49 and bigbend.obs_inflow.isna().sum() == 5, 'Big Bend real missing values preserved')
    return {'status': 'PASS', 'checks_passed': len(checks), 'checks': checks, 'result_directory': str(root)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result_directory', type=Path)
    args = parser.parse_args()
    result = validate(args.result_directory)
    destination = args.result_directory/'report/validation.json'
    if destination.exists():
        raise FileExistsError(f'Will not overwrite prior validation: {destination}')
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'checks'}, ensure_ascii=False))
