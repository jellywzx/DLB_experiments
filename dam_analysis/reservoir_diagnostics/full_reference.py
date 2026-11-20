"""ID-matched daily reference cube + original monthly reservoir vectors (read-only)."""
from pathlib import Path
import re
import numpy as np
import pandas as pd
import xarray as xr
from io_openbench import ITEMS, canonical_id, unit_factor, sha, read_station_table

DEFAULT_REFERENCE=Path('/share/home/dq013/zhwei/OpenBench/Reference/Station/Anth/Dam/ResOpsUS/OpenBench_ResOpsUS_Daily_full.nc')
MODEL_VARS={'inflow':'qresv_in','outflow':'qresv_out','storage':'volresv'}


def reference_ids(values):
    result=[]
    for raw in values:
        text=raw.decode() if isinstance(raw,bytes) else str(raw)
        match=re.fullmatch(r'ResOpsUS_([0-9]+)',text)
        if not match:raise ValueError(f'Invalid reference station_id: {text!r}')
        result.append(canonical_id(match.group(1)))
    if len(result)!=len(set(result)):raise ValueError('Duplicate reference GRanD IDs')
    return result


def daily_index(values, label):
    index=pd.DatetimeIndex(values)
    if index.hasnans or not index.is_monotonic_increasing or index.normalize().duplicated().any():
        raise ValueError(f'{label}: invalid, unsorted or duplicate daily dates')
    offsets=set((index-index.normalize()).total_seconds())
    if len(offsets)>1:raise ValueError(f'{label}: inconsistent daily timestamp offsets')
    return index


def cleaned(values):
    a=np.asarray(values,float).copy();a[~np.isfinite(a)|(abs(a)>=1e30)]=np.nan
    return a


def model_paths(config, simulation, variables):
    sim=config['simulation'][simulation];root=Path(sim['root_dir'])
    if str(sim.get('data_groupby','')).lower()!='month':raise ValueError('Full reference mode requires monthly reservoir history')
    prefixes={sim.get('variables',{}).get(ITEMS[v],{}).get('prefix',sim.get('prefix')) for v in variables if v in ITEMS}
    if len(prefixes)!=1 or None in prefixes:raise ValueError('Reservoir variables must share one monthly history prefix')
    prefix=next(iter(prefixes));suffixes={sim.get('variables',{}).get(ITEMS[v],{}).get('suffix',sim.get('suffix','')) or '' for v in variables}
    if len(suffixes)!=1:raise ValueError('Reservoir variables must share one suffix')
    suffix=next(iter(suffixes));start,end=config['project']['years']
    return [root/f'{prefix}{year:04d}-{month:02d}{suffix}.nc' for year in range(start,end+1) for month in range(1,13)]


def discover_full(root, manifest, simulation, reference, variables, path, requested_ids):
    config=manifest['canonical_config'];project=config['project'];path=Path(path).resolve()
    paths=model_paths(config,simulation,variables)
    missing=[str(p) for p in paths if not p.is_file()]
    if missing:raise FileNotFoundError(f'Missing monthly history: {missing[0]} ({len(missing)} files)')
    with xr.open_dataset(path) as ds:
        ids=reference_ids(ds.station_id.values)
        if ds.station_id.dims!=('station',):raise ValueError('station_id must have dimension station')
        science=[v for v in variables if ITEMS[v] in ds and v!='storage']
        if not science:raise ValueError('Reference has no enabled inflow/outflow science variables')
        stations=pd.DataFrame({'ID':ids,'DamName':ds.station_name.values.astype(str) if 'station_name' in ds else ids,
                               'LON_org':ds.lon.values,'LAT_org':ds.lat.values})
        for name in ['qc_excluded_periods','inconsistencies_noted','agency','country_code']:
            if name in ds and ds[name].dims==('station',):stations[name]=ds[name].values.astype(str)
        time_support={ITEMS[v]:ds[ITEMS[v]].attrs.get('data_meta_time_support','unverified') for v in science}
        metadata={k:str(ds.attrs.get(k,'')) for k in ['timezone_type','timezone_definition','history','data_meta_sources']}
    with xr.open_dataset(paths[0]) as ds:
        mids=[canonical_id(x) for x in ds.resv_GRAND_ID.values]
        if len(mids)!=len(set(mids)):raise ValueError('Duplicate model IDs')
    audit=stations.copy();audit['in_model_first_month']=audit.ID.isin(mids)
    audit['valid_coordinates']=np.isfinite(audit.LON_org)&np.isfinite(audit.LAT_org)
    audit['in_region']=audit.LON_org.between(*project.get('lon_range',[-180,180]))&audit.LAT_org.between(*project.get('lat_range',[-90,90]))
    audit['mask_value']=np.nan
    maskpath=project.get('basin_mask')
    if maskpath:
        with xr.open_dataset(maskpath) as ds:
            coords=audit.valid_coordinates&audit.in_region
            z=ds.basin_mask.sel(lon=xr.DataArray(audit.loc[coords,'LON_org'].values,dims='point'),lat=xr.DataArray(audit.loc[coords,'LAT_org'].values,dims='point'),method='nearest')
            audit.loc[coords,'mask_value']=z.values
        audit['in_basin']=np.isfinite(audit.mask_value)&(audit.mask_value>0)
    else:audit['in_basin']=True
    audit['requested']=True if requested_ids is None else audit.ID.isin([canonical_id(x) for x in requested_ids])
    ok=audit.in_model_first_month&audit.valid_coordinates&audit.in_region&audit.in_basin&audit.requested
    audit['selected']=ok
    audit['exclusion_reason']=[';'.join(name for flag,name in [(r.in_model_first_month,'NO_MODEL_ID'),(r.valid_coordinates,'INVALID_COORDINATES'),(r.in_region,'OUTSIDE_REGION'),(r.in_basin,'OUTSIDE_BASIN'),(r.requested,'NOT_REQUESTED')] if not flag) for r in audit.itertuples()]
    stations=audit.loc[ok].copy()
    if requested_ids:
        absent=set(map(canonical_id,requested_ids))-set(stations.ID)
        if absent:raise ValueError(f'Requested IDs fail reference/model/region/basin selection: {sorted(absent)}')
    if stations.empty:raise ValueError('No reservoirs pass reference/model/region/basin selection')
    stations['ref_lon']=stations.LON_org;stations['ref_lat']=stations.LAT_org
    stations['use_syear']=int(project['years'][0]);stations['use_eyear']=int(project['years'][1]);stations['Flag']=True
    metrics={};inventory=[root/'run_manifest.json',path]
    for v,item in ITEMS.items():
        p=root/'metrics'/f'{item}_stn_{reference}_{simulation}_evaluations.csv'
        metrics[v]=read_station_table(p) if p.is_file() else pd.DataFrame(columns=['ID'])
        if p.is_file():inventory.append(p)
    return dict(root=root,manifest=manifest,config=config,simulation=simulation,reference=reference,
                variables=science,stations=stations,start=int(project['years'][0]),end=int(project['years'][1]),
                metrics=metrics,matching={v:{} for v in science},inventory=inventory,mode='full',reference_file=path,
                history_paths=paths,selection_audit=audit,reference_time_support=time_support,reference_metadata=metadata,
                requested_ids=None if requested_ids is None else [canonical_id(x) for x in requested_ids],
                reference_signature=(path.stat().st_size,path.stat().st_mtime_ns))


def prepare_full(context):
    """Open reference once, each model month once; retain ID-specific sparse arrays only."""
    stations=context['stations'];ids=stations.ID.tolist();variables=context['variables'];start,end=context['start'],context['end']
    pieces={v:[] for v in MODEL_VARS};counts={ident:0 for ident in ids};offsets=set();records=[]
    for path in context['history_paths']:
        before=path.stat()
        with xr.open_dataset(path) as ds:
            mids=[canonical_id(x) for x in ds.resv_GRAND_ID.values]
            if len(mids)!=len(set(mids)):raise ValueError(f'Duplicate model IDs: {path}')
            mapping={ident:j for j,ident in enumerate(mids)};present=[i for i in ids if i in mapping]
            index=daily_index(ds.time.values,str(path));offsets.update((index-index.normalize()).total_seconds())
            for ident in present:counts[ident]+=1
            for v,var in MODEL_VARS.items():
                if var not in ds:
                    if v in variables:raise ValueError(f'{path}: missing {var}')
                    continue
                if set(ds[var].dims)!={'time','reservoir'}:raise ValueError(f'Invalid model variable dimensions: {var}')
                factor=unit_factor(ds[var].attrs.get('units',''),v)
                values=cleaned(ds[var].isel(reservoir=[mapping[i] for i in present]).transpose('time','reservoir').values)*factor
                pieces[v].append(pd.DataFrame(values,index=index,columns=present).reindex(columns=ids))
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError(f'History changed during read: {path}')
        records.append(dict(path=str(path),size=path.stat().st_size,mtime_ns=path.stat().st_mtime_ns,verification='SIZE_MTIME_ONLY; sparse reservoir arrays read; not full-file content verification'))
        print(f'Read history {path.name}',flush=True)
    for v in pieces:
        if pieces[v]:
            combined=pd.concat(pieces[v]);daily_index(combined.index,f'model {v} across months');pieces[v]=combined
        else:pieces[v]=pd.DataFrame()
    # Enforce the all-month ID intersection; missing periods never become fictitious zero flows.
    complete={ident for ident,count in counts.items() if count==len(context['history_paths'])}
    audit=context['selection_audit'];audit['model_months_present']=audit.ID.map(counts).fillna(0).astype(int)
    lost=audit.selected&~audit.ID.isin(complete);audit.loc[lost,'selected']=False;audit.loc[lost,'exclusion_reason']='MODEL_ID_MISSING_IN_SOME_MONTHS'
    requested=context.get('requested_ids')
    if requested and set(requested)-complete:raise ValueError('Requested IDs missing from some monthly histories')
    context['stations']=stations[stations.ID.isin(complete)].copy();ids=context['stations'].ID.tolist()
    if not ids:raise ValueError('No target IDs present in every history month')
    observed={};qc=[]
    with xr.open_dataset(context['reference_file']) as ds:
        mapping={ident:j for j,ident in enumerate(reference_ids(ds.station_id.values))}
        sub=ds.isel(station=[mapping[i] for i in ids]).sel(time=slice(f'{start}-01-01',f'{end}-12-31T23:59:59'))
        index=daily_index(sub.time.values,'reference');offsets.update((index-index.normalize()).total_seconds())
        for v in variables:
            var=ITEMS[v]
            if set(sub[var].dims)!={'station','time'}:raise ValueError(f'Invalid reference dimensions: {var}')
            factor=unit_factor(sub[var].attrs.get('units',''),v)
            observed[v]=pd.DataFrame(cleaned(sub[var].transpose('time','station').values)*factor,index=index,columns=ids)
            for name in [f'resops_{v}_filter_reason',f'resops_{v}_accepted_count',f'resops_{v}_rejected_count']:
                if name in sub:
                    values=sub[name].transpose('time','station').values
                    for j,ident in enumerate(ids):
                        qc.append(dict(ID=ident,variable=v,field=name,finite_days=int(np.isfinite(values[:,j]).sum()),nonzero_days=int((np.isfinite(values[:,j])&(values[:,j]!=0)).sum()),
                                       sum_value=float(np.nansum(values[:,j])),interpretation='SOURCE_DIAGNOSTIC_BEFORE_MANUAL_QC; science values remain authoritative'))
    stat=context['reference_file'].stat()
    if (stat.st_size,stat.st_mtime_ns)!=context['reference_signature']:raise ValueError('Reference changed during read')
    context['qc_summary']=qc
    if len(offsets)>1:raise ValueError(f'Reference/model daily timestamp offsets differ: {sorted(offsets)}; no automatic shift')
    offset=pd.Timedelta(seconds=next(iter(offsets),43200));index=pd.date_range(f'{start}-01-01',f'{end}-12-31',freq='D')+offset
    loaded={}
    for ident in ids:
        frame=pd.DataFrame(index=index);audits=[]
        for v in MODEL_VARS:
            frame[f'obs_{v}']=observed[v][ident].reindex(index) if v in observed else np.nan
            frame[f'sim_{v}']=pieces[v][ident].reindex(index) if ident in pieces[v] else np.nan
            if v not in variables:continue
            n=int(np.isfinite(frame[[f'obs_{v}',f'sim_{v}']]).all(axis=1).sum())
            audits.append(dict(ID=ident,variable=v,requested_start=f'{start}-01-01',requested_end=f'{end}-12-31',units='m3/s',
                               ref_files=str(context['reference_file']),sim_files=';'.join(str(p) for p in context['history_paths']),
                               ref_finite_n=int(frame[f'obs_{v}'].notna().sum()),sim_finite_n=int(frame[f'sim_{v}'].notna().sum()),
                               ref_skip_reason='' if frame[f'obs_{v}'].notna().any() else 'NO_FINITE_SCIENCE_REFERENCE_AFTER_SOURCE_QC',sim_skip_reason='',
                               n_pair=n,expected_days=len(frame),coverage=n/len(frame),timestamp_offset_seconds=offset.total_seconds(),
                               status='PAIRED' if n else 'NO_VALID_PAIRS',in_evaluation_table=ident in set(context['metrics'][v].ID),reference_time_support=context['reference_time_support'][ITEMS[v]]))
        frame.index.name='time';loaded[ident]=(frame,audits,[])
    context['model_records']=records
    return loaded
