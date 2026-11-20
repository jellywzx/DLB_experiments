"""Independently verify exported counts, metrics, images, and input immutability."""
from pathlib import Path
import json
import argparse
import numpy as np
import pandas as pd
from PIL import Image
from io_openbench import sha


def validate(directory):
    root=Path(directory);checks=[]
    def check(name,condition,detail=''):
        checks.append(dict(check=name,passed=bool(condition),detail=detail))
    summary=pd.read_csv(root/'tables/reservoir_summary.csv',dtype={'ID':str})
    metrics=pd.read_csv(root/'tables/metrics.csv',dtype={'ID':str})
    audit=pd.read_csv(root/'tables/matching_audit.csv',dtype={'ID':str})
    cross=pd.read_csv(root/'tables/metric_crosschecks.csv',dtype={'ID':str})
    check('unique_reservoir_ids',not summary.ID.duplicated().any())
    check('unique_metric_rows',not metrics.duplicated(['ID','variable']).any())
    check('legacy_metric_agreement_no_computation_mismatch',not (cross.status=='MISMATCH').any(),f'{int((cross.status=="MISMATCH").sum())} mismatches')
    provenance=json.loads((root/'provenance.json').read_text())
    project=provenance['project'];first,last=project['years'];expected_start=pd.Timestamp(int(first),1,1);expected_end=pd.Timestamp(int(last),12,31)
    for row in summary.itertuples():
        frame=pd.read_csv(root/'data'/f'{row.ID}_daily.csv',parse_dates=['time']).set_index('time')
        check(row.ID+'_unique_daily',not frame.index.normalize().duplicated().any() and frame.index.is_monotonic_increasing)
        check(row.ID+'_within_evaluation',frame.index.min().normalize()>=expected_start and frame.index.max().normalize()<=expected_end)
        for rec in metrics[metrics.ID==row.ID].itertuples():
            v=rec.variable;o=frame[f'obs_{v}'].values;s=frame[f'sim_{v}'].values;valid=np.isfinite(o)&np.isfinite(s)
            check(f'{row.ID}_{v}_n',valid.sum()==rec.n)
            a=audit[(audit.ID==row.ID)&(audit.variable==v)].iloc[0]
            check(f'{row.ID}_{v}_coverage',len(frame)==a.expected_days and valid.sum()==a.n_pair)
            if valid.any():
                oo,ss=o[valid],s[valid]
                check(f'{row.ID}_{v}_RMSE',np.isclose(np.sqrt(np.mean((ss-oo)**2)),rec.RMSE,rtol=1e-10,atol=1e-9))
                if oo.sum()!=0:check(f'{row.ID}_{v}_PBIAS',np.isclose(100*(ss-oo).sum()/oo.sum(),rec.percent_bias,rtol=1e-10,atol=1e-8))
        for kind in ['timeseries','seasonal_scatter']:
            check(f'{row.ID}_{kind}_exists',(root/'figures'/f'{row.ID}_{kind}.png').is_file())
    images=list((root/'figures').rglob('*.png'))
    for path in images:
        try:
            with Image.open(path) as im:im.verify()
            ok=True
        except Exception:ok=False
        check('png_'+path.stem,ok)
        pdf=path.with_suffix('.pdf');check('pdf_'+path.stem,pdf.is_file() and pdf.open('rb').read(5)==b'%PDF-')
    for rec in provenance['input_files']:
        path=Path(rec['path'])
        ok=path.is_file() and (sha(path)==rec['sha256'] if 'sha256' in rec else path.stat().st_size==rec['size'] and path.stat().st_mtime_ns==rec['mtime_ns'])
        check('unchanged_input_'+rec['path'],ok,rec.get('verification','SHA256'))
    for rec in provenance['source_files']:
        check('source_sha_'+Path(rec['path']).name,sha(rec['path'])==rec['sha256'])
    return dict(passed=all(x['passed'] for x in checks),checks_count=len(checks),reservoirs=len(summary),
                with_pairs=int(summary.has_pairs.sum()),png_count=len(images),pdf_count=len(list((root/'figures').rglob('*.pdf'))),
                checks=checks)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('directory',type=Path);args=parser.parse_args()
    result=validate(args.directory);print(json.dumps({k:v for k,v in result.items() if k!='checks'},indent=2))
    for c in result['checks']:
        if not c['passed']:print(c)
    raise SystemExit(0 if result['passed'] else 1)
