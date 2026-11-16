#!/usr/bin/env python3
"""Reproducible Missouri joint diagnosis; original data and outputs are read-only."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr
from scipy.signal import find_peaks
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

OB = Path('/share/home/dq134/wzx/openbench/openbench_1015')
sys.path.insert(0, str(OB/'src'))
from openbench.data.reservoir import reservoir_index, reservoir_unit

TARGETS = [('Fort Peck',307,'Montana'),('Garrison',753,'North Dakota'),('Oahe',870,'South Dakota'),
           ('Big Bend',884,'South Dakota'),('Fort Randall',None,'South Dakota'),('Gavins Point',895,'South Dakota')]
VARS = {'inflow':'qresv_in','outflow':'qresv_out','storage':'volresv'}
ITEMS = {'inflow':'Dam_Inflow','outflow':'Dam_Outflow','storage':'Dam_Water_Storage'}
RAW = Path('/share/home/dq013/zhwei/OpenBench/Data_ref/stn_ref/Dam_River_Lake/ResOpsUS')
CASE = Path('/share/home/dq061/wzx/CoLM/cases/EXP000_spinup_USA_D_0p1')
SOURCE = Path('/share/home/dq061/wzx/CoLM/CoLM202X_1030')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def norm(text):
    if isinstance(text,bytes): text=text.decode('utf-8')
    return re.sub('[^a-z0-9]','',str(text).lower()).removesuffix('dam')


def distance_km(lon1,lat1,lon2,lat2):
    x1,y1,x2,y2=np.radians([lon1,lat1,lon2,lat2])
    return float(6371*2*np.arcsin(np.sqrt(np.sin((y2-y1)/2)**2+np.cos(y1)*np.cos(y2)*np.sin((x2-x1)/2)**2)))


def clean(values):
    a=np.asarray(values,dtype=float).copy()
    a[~np.isfinite(a)|np.isclose(a,-1e36,rtol=1e-6,atol=0)]=np.nan
    return a


def paired(obs,sim):
    a=np.asarray(obs,float);b=np.asarray(sim,float)
    mask=np.isfinite(a)&np.isfinite(b)
    return a[mask],b[mask],mask


def metrics(obs,sim):
    o,s,mask=paired(obs,sim)
    keys=['kge','nse','correlation','pbias_pct','rsd','mean_ratio','rmse','mean_bias','mean_obs','mean_sim']
    result={key:np.nan for key in keys}; result['n']=len(o)
    reasons=[]
    if len(o)<3:
        result['undefined_reasons']='fewer_than_3_valid_pairs'
        return result
    result.update(rmse=float(np.sqrt(np.mean((s-o)**2))),mean_bias=float(np.mean(s-o)),mean_obs=float(o.mean()),mean_sim=float(s.mean()))
    so,ss=o.std(ddof=1),s.std(ddof=1)
    if so>0:
        result['rsd']=float(ss/so)
        result['nse']=float(1-np.sum((s-o)**2)/np.sum((o-o.mean())**2))
    else: reasons.append('zero_observed_std:NSE_rSD_KGE_undefined')
    if so>0 and ss>0: result['correlation']=float(np.corrcoef(o,s)[0,1])
    else: reasons.append('zero_std:correlation_undefined')
    if o.mean()!=0:
        result['mean_ratio']=float(s.mean()/o.mean())
        result['pbias_pct']=float(100*np.sum(s-o)/np.sum(o))
    else: reasons.append('zero_observed_mean:PBIAS_mean_ratio_KGE_undefined')
    if all(np.isfinite(result[k]) for k in ['correlation','rsd','mean_ratio']):
        result['kge']=float(1-np.sqrt((result['correlation']-1)**2+(result['rsd']-1)**2+(result['mean_ratio']-1)**2))
    result['undefined_reasons']=';'.join(reasons)
    return result


def stock_metrics(obs,sim,index,capacity):
    out=metrics(obs,sim)
    o,s,mask=paired(obs,sim)
    out['nrmse_capacity']=out['rmse']/capacity if np.isfinite(capacity) and capacity>0 else np.nan
    if not np.isfinite(capacity) or capacity<=0:
        out['undefined_reasons']+=';nonpositive_or_missing_capacity'
    out['range_obs']=float(np.ptp(o)) if len(o)>=3 else np.nan
    out['range_sim']=float(np.ptp(s)) if len(s)>=3 else np.nan
    out['range_error']=out['range_sim']-out['range_obs']
    frame=pd.DataFrame({'obs':obs,'sim':sim},index=index)
    frame.loc[~mask,:]=np.nan
    seasonal=frame.groupby(frame.index.month).agg(['mean','count'])
    good=(seasonal[('obs','count')]>=15)&(seasonal[('sim','count')]>=15)
    out['seasonal_months']=int(good.sum())
    if len(seasonal)==12 and good.all():
        oc=seasonal[('obs','mean')];sc=seasonal[('sim','mean')]
        out['seasonal_cycle_rmse']=float(np.sqrt(np.mean((sc-oc)**2)))
        out['seasonal_amplitude_error']=float(np.ptp(sc)-np.ptp(oc))
        out['seasonal_peak_month_error']=float((int(sc.idxmax())-int(oc.idxmax())+6)%12-6)
    else:
        out.update(seasonal_cycle_rmse=np.nan,seasonal_amplitude_error=np.nan,seasonal_peak_month_error=np.nan)
        out['undefined_reasons']+=';fewer_than_12_months_with_15_pairs'
    return out,seasonal


def consecutive_difference(series):
    out=series.diff()
    return out.where(series.index.to_series().diff()==pd.Timedelta(days=1))


def balance_proxies(frame):
    out=pd.DataFrame(index=frame.index)
    for side in ['obs','sim']:
        q=frame[f'{side}_inflow']-frame[f'{side}_outflow']
        ds=consecutive_difference(frame[f'{side}_storage'])
        out[f'{side}_in_minus_out_m3s']=q
        out[f'{side}_delta_reported_storage_mcm']=ds
        out[f'{side}_centered_mean_storage_proxy_residual_mcm']=ds-(q+q.shift(1))*0.5*86400/1e6
        out[f'{side}_end_day_storage_proxy_residual_mcm']=ds-q*86400/1e6
    return out


def classify(frame,capacity,storage_support='unverified'):
    good=np.isfinite(frame[[f'{side}_{v}' for side in ['obs','sim'] for v in VARS]]).all(axis=1)
    f=frame.loc[good];result={'n_joint':int(good.sum())}
    mi=metrics(f.obs_inflow,f.sim_inflow);mo=metrics(f.obs_outflow,f.sim_outflow)
    ms=metrics(f.obs_storage,f.sim_storage)
    bias=ms['mean_bias']/capacity if capacity>0 else np.nan
    nr=ms['rmse']/capacity if capacity>0 else np.nan
    high_i=mi['pbias_pct']>10; high_o=mo['pbias_pct']>10
    reasonable=(abs(bias)<=0.05 and nr<=0.1 and ms['correlation']>=0.5)
    c='D'
    if high_i and high_o and bias>0.05: c='A'
    elif high_i and high_o and np.isfinite(bias) and bias<=0.05: c='B'
    elif high_i and mo['kge']>=0.5 and abs(mo['pbias_pct'])<=10 and reasonable: c='C'
    result.update(provisional_class=c,diagnosis_class=c if storage_support=='daily_mean' else 'D',
                  classification_status='CONFIRMED_TIME_SUPPORT' if storage_support=='daily_mean' else 'PROVISIONAL_STORAGE_TIME_SUPPORT',
                  joint_inflow_pbias_pct=mi['pbias_pct'],joint_outflow_pbias_pct=mo['pbias_pct'],joint_outflow_kge=mo['kge'],
                  provisional_storage_bias_fraction_capacity=bias,provisional_storage_nrmse=nr,
                  first30_storage_bias_mcm=metrics(frame.obs_storage.iloc[:30],frame.sim_storage.iloc[:30])['mean_bias'])
    return result


def detect_events(frame):
    """Candidate high-flow events anchored to observed inflow, never independent maxima."""
    q=frame.obs_inflow
    finite=q[np.isfinite(q)]
    if len(finite)<30 or finite.std()==0: return []
    threshold=float(finite.quantile(.9)); prominence=float(finite.std()*.5)
    # Missing positions cannot form events; reject every incomplete event window below.
    candidates=np.where(np.isfinite(q),q,-np.inf)
    peaks,_=find_peaks(candidates,height=threshold,prominence=prominence,distance=30)
    records=[]
    for number,peak in enumerate(peaks,1):
        for radius in [7,14,21]:
            lo,hi=peak-radius,peak+radius
            record=dict(event=number,anchor=str(frame.index[peak].date()),radius_days=radius,
                        threshold_q90=threshold,provenance='OBSERVED_INFLOW_CANDIDATE_NOT_VALIDATED_FLOOD')
            if lo<0 or hi>=len(frame):
                records.append(dict(record,status='INCOMPLETE_BOUNDARY_WINDOW'));continue
            if any(abs(int(other)-int(peak))<=2*radius for other in peaks if other!=peak):
                records.append(dict(record,status='OVERLAPPING_EVENT_WINDOWS'));continue
            window=frame.iloc[lo:hi+1]
            cols=[f'{side}_{v}' for side in ['obs','sim'] for v in ['inflow','outflow']]
            if not np.isfinite(window[cols]).all().all():
                records.append(dict(record,status='INCOMPLETE_FLOW_COVERAGE'));continue
            record.update(start=str(window.index[0].date()),end=str(window.index[-1].date()))
            flags=[]
            for side in ['obs','sim']:
                dates={};maxima={}
                for var in ['inflow','outflow']:
                    s=window[f'{side}_{var}'];value=s.max();maxima[var]=float(value)
                    tied=s.index[s==value];dates[var]=tied[0]
                    if len(tied)!=1 or dates[var] in [window.index[0],window.index[-1]]:
                        flags.append(f'{side}_{var}_peak_ambiguous_or_on_boundary')
                record.update({f'{side}_peak_inflow':maxima['inflow'],f'{side}_peak_outflow':maxima['outflow'],
                               f'{side}_peak_inflow_date':str(dates['inflow'].date()),f'{side}_peak_outflow_date':str(dates['outflow'].date()),
                               f'{side}_attenuation':1-maxima['outflow']/maxima['inflow'] if maxima['inflow']>0 else np.nan,
                               f'{side}_lag_days':(dates['outflow']-dates['inflow']).days})
            record['status']='AMBIGUOUS_EVENT_PAIRING' if flags else 'CANDIDATE_WINDOW_COMPLETE'
            record['limitations']=';'.join(flags) or 'daily_mean_peaks_only;causal_event_identity_unverified'
            record['attenuation_error']=record['sim_attenuation']-record['obs_attenuation']
            record['lag_error_days']=record['sim_lag_days']-record['obs_lag_days']
            if flags:
                for key in ['obs_attenuation','sim_attenuation','obs_lag_days','sim_lag_days','attenuation_error','lag_error_days']:
                    record[key]=np.nan
            records.append(record)
    return records


def read_parameters(path):
    with xr.open_dataset(path) as ds:
        fields={'grand_id':'dam_GRAND_ID','dam_name':'dam_DamName','dam_seq':'dam_seq','lon':'dam_DamLon',
                'lat':'dam_DamLat','capacity_mcm':'dam_TotalVol_mcm'}
        assert ds.dam_TotalVol_mcm.attrs['units']=='million cubic meters'
        rows={key:ds[value].values for key,value in fields.items()}
        rows['dam_name']=[v.decode() if isinstance(v,bytes) else str(v) for v in rows['dam_name']]
        frame=pd.DataFrame(rows)
        if frame.grand_id.duplicated().any(): raise ValueError(f'duplicate parameter GRanD IDs: {path}')
        return frame


def observations(path,index):
    raw=pd.read_csv(path,na_values=['NA','NaN','nan','-9999','-9999.0'])
    dates=pd.to_datetime(raw.date,errors='raise')
    if dates.duplicated().any() or not dates.is_monotonic_increasing:
        raise ValueError(f'duplicate or non-monotonic observed dates: {path}')
    frame=pd.DataFrame({v:clean(pd.to_numeric(raw[v],errors='raise')) for v in VARS},index=dates)
    frame=frame.reindex(index)
    return frame


def read_histories(args,ids,index):
    result={id:[] for id in ids}; inventory=[]
    for period in pd.period_range(args.start,args.end,freq='M'):
        path=args.history/f'{args.prefix}{period.year:04d}-{period.month:02d}.nc'
        if not path.is_file():
            inventory.append({'path':str(path),'status':'MISSING'});continue
        with xr.open_dataset(path) as ds:
            mapping=reservoir_index(ds)
            dates=pd.DatetimeIndex(ds.time.values)
            if dates.has_duplicates or not dates.is_monotonic_increasing:
                raise ValueError(f'duplicate or non-monotonic simulation time: {path}')
            # Native CoLM daily histories label the interval centre. No inherited user offset is applied.
            if not (dates==dates.normalize()+pd.Timedelta(hours=12)).all():
                raise ValueError(f'Unrecognized history time support; inspect before aligning: {path}')
            if dates.normalize().duplicated().any(): raise ValueError(f'not daily data: {path}')
            if 'history_window_seconds' in ds and not np.allclose(ds.history_window_seconds,86400):
                raise ValueError(f'non-full-day averaging window: {path}')
            units={}
            for v,raw in VARS.items():
                if set(ds[raw].dims)!={'time','reservoir'}: raise ValueError(f'inconsistent variable axis: {path}:{raw}')
                units[v]=reservoir_unit(ds[raw].attrs.get('units',''),ITEMS[v])
            available=[id for id in ids if str(id) in mapping]
            for id in available:
                pos=mapping[str(id)]
                fields={}
                for v,raw in VARS.items():
                    a=clean(ds[raw].isel(reservoir=pos).values)
                    fields[v]=a/1e6 if v=='storage' and units[v]=='m3' else a
                result[id].append(pd.DataFrame(fields,index=dates.normalize()))
            stat=path.stat()
            inventory.append(dict(path=str(path),status='READ',size=stat.st_size,mtime_ns=stat.st_mtime_ns,
                                  first_time=str(dates[0]),last_time=str(dates[-1]),units=units,
                                  indices={str(id):mapping.get(str(id)) for id in ids},
                                  window_metadata_present='history_window_seconds' in ds))
    for id in ids:
        if result[id]:
            frame=pd.concat(result[id])
            if frame.index.duplicated().any(): raise ValueError(f'duplicate simulated dates across monthly files: {id}')
            result[id]=frame.reindex(index)
        else: result[id]=pd.DataFrame(np.nan,index=index,columns=list(VARS))
    return result,inventory


def plot_timeseries(name,id,frame,capacity,out,storage_support):
    fig,axes=plt.subplots(3,1,figsize=(11,8),sharex=True)
    for ax,(v,letter) in zip(axes,zip(VARS,'ABC')):
        ax.plot(frame.index,frame[f'obs_{v}'],color='#232323',lw=1.0,label='Observed')
        ax.plot(frame.index,frame[f'sim_{v}'],color='#1976a3',lw=1.0,label='Simulated')
        m=metrics(frame[f'obs_{v}'],frame[f'sim_{v}'])
        ax.set_ylabel(f"{v.title()}\n{'MCM' if v=='storage' else 'm³ s⁻¹'}")
        text=f"KGE={m['kge']:.2f}  PBIAS={m['pbias_pct']:.1f}%  N={m['n']}"
        if v=='storage':
            sm,_=stock_metrics(frame.obs_storage,frame.sim_storage,frame.index,capacity)
            text+=f"\nRMSE={sm['rmse']:.1f} MCM  nRMSE(cap)={sm['nrmse_capacity']:.3f}"
            if storage_support!='daily_mean': text='EXPLORATORY: storage time support unverified\n'+text
        ax.text(.01,.96,letter+'  '+text,transform=ax.transAxes,va='top',fontsize=8,
                bbox=dict(facecolor='white',alpha=.85,edgecolor='none'))
        ax.grid(alpha=.2);ax.set_xlim(frame.index[0],frame.index[-1])
    axes[0].legend(loc='upper right',frameon=False,ncol=2)
    axes[0].set_title(f'{name} | GRanD ID {id if id is not None else "UNVERIFIED"}',loc='left',weight='bold')
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    if id is None:
        for ax in axes: ax.text(.5,.45,'NO_REFERENCE / NO VERIFIED US MODEL MATCH\nID 2356 explicitly excluded',ha='center',transform=ax.transAxes,color='#a34332')
    fig.tight_layout()
    stem=norm(name)
    for suffix in ['png','pdf']: fig.savefig(out/f'{stem}_inflow_outflow_storage.{suffix}',dpi=300)
    plt.close(fig)


def plot_process(name,proxy,out):
    fig,axes=plt.subplots(3,1,figsize=(11,8),sharex=True)
    suffixes=['in_minus_out_m3s','delta_reported_storage_mcm','centered_mean_storage_proxy_residual_mcm']
    labels=['Inflow − Outflow (m³ s⁻¹)','Δ reported/mean storage (MCM)','Approximate residual (MCM)']
    for ax,suffix,label in zip(axes,suffixes,labels):
        for side,color in [('obs','#232323'),('sim','#1976a3')]:
            ax.plot(proxy.index,proxy[f'{side}_{suffix}'],label=side,color=color,lw=.8)
        ax.axhline(0,lw=.5,color='gray');ax.set_ylabel(label);ax.grid(alpha=.2)
    axes[0].legend(frameon=False,ncol=2)
    axes[0].set_title(f'{name}: daily regulation proxies (NOT a numerical conservation test)',loc='left')
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    fig.tight_layout()
    for ext in ['png','pdf']: fig.savefig(out/f'{norm(name)}_regulation_proxies.{ext}',dpi=300)
    plt.close(fig)


def source_evidence(args,out):
    specs=[('main/HYDRO/MOD_Grid_RiverLakeFlow.F90',['acctime_resv(irsv) =','a_volresv   (irsv) =','volwater = volwater -','volwater = max(volwater','qresv_in(irsv) =','qresv_in(ucat2resv(i)) =']),
           ('main/HYDRO/MOD_Grid_RiverLakeHist.F90',['volresv_local =','qresv_in_local =','qresv_out_local =']),
           ('main/HYDRO/MOD_Grid_RiverLakeHistRoute.F90',['resv_GRAND_ID','history_window_seconds']),
           ('main/HYDRO/MOD_Catch_Reservoir.F90',['volresv_total (irsv) =','qresv_normal','qresv_adjust']),
           ('share/MOD_NetCDFSerial.F90',["CASE ('DAILY')",'minutes = minutes - 720'])]
    records=[];chunks=[]
    for relative,tokens in specs:
        p=args.source/relative
        if not p.is_file(): records.append(dict(path=str(p),status='MISSING'));continue
        text=p.read_text();lines=text.splitlines(); chosen=set()
        for i,l in enumerate(lines):
            if any(token in l for token in tokens): chosen.update(range(max(0,i-3),min(len(lines),i+5)))
        chunks.append(str(p)+'\n'+'\n'.join(f'{i+1}: {lines[i]}' for i in sorted(chosen)))
        records.append(dict(path=str(p),sha256=sha(p),status='SOURCE_ONLY_BINARY_UNVERIFIED'))
    (out/'source_evidence.txt').write_text('\n\n'.join(chunks))
    return records


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--start',default='2001-01-01');p.add_argument('--end',default='2002-12-31')
    p.add_argument('--history',type=Path,default=CASE/'history')
    p.add_argument('--prefix',default='EXP000_spinup_USA_D_0p1_hist_unitcat_')
    p.add_argument('--params',type=Path,default=CASE/'riverlake/unitcatchment_regional.nc')
    p.add_argument('--global-params',type=Path,default=Path('/share/home/dq013/zhwei/colm/data/CoLMruntime/unitcatchment/grid_routing_data_06min.nc'))
    p.add_argument('--attributes',type=Path,default=RAW/'source/attributes/reservoir_attributes.csv')
    p.add_argument('--series',type=Path,default=RAW/'source/time_series_all')
    p.add_argument('--source',type=Path,default=SOURCE)
    args=p.parse_args()
    if args.out.exists() and any(args.out.iterdir()): p.error('output directory must be new/empty; originals are never overwritten')
    args.out.mkdir(parents=True,exist_ok=True)
    for directory in ['figures','tables','report','data','provenance']: (args.out/directory).mkdir()
    index=pd.date_range(args.start,args.end,freq='D')
    if index.empty: raise ValueError('empty requested date range')
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,
                         'pdf.fonttype':42,'savefig.bbox':'tight'})
    params=read_parameters(args.params); global_params=read_parameters(args.global_params)
    attrs=pd.read_csv(args.attributes)
    ids=[id for _,id,_ in TARGETS if id is not None]
    sim,inventory=read_histories(args,ids,index)
    audits=[];allmetrics=[];diagnostics=[];events=[];quality=[];frames={};seasonal_rows=[]
    for order,(name,id,state) in enumerate(TARGETS,1):
        audit=dict(order=order,reservoir=name,resopsus_id=id,grand_id=id,requested_start=args.start,requested_end=args.end,
                   mapping='EXACT_ID_AND_NAME_AND_COORDINATES',status='NO_REFERENCE',storage_time_support='UNVERIFIED',
                   capacity_mcm=np.nan)
        frame=pd.DataFrame(np.nan,index=index,columns=[f'{side}_{v}' for side in ['obs','sim'] for v in VARS])
        capacity=np.nan
        if id is not None:
            rows=params[params.grand_id==id];refs=attrs[attrs.DAM_ID==id]
            if len(rows)!=1 or len(refs)!=1: raise ValueError(f'{name}: nonunique/missing parameter or attribute ID')
            dam=rows.iloc[0];ref=refs.iloc[0]
            if norm(dam.dam_name)!=norm(name) or norm(ref.DAM_NAME)!=norm(name) or ref.STATE!=state:
                raise ValueError(f'{name}: identity disagreement: {dam.dam_name}, {ref.DAM_NAME}, {ref.STATE}')
            km=distance_km(dam.lon,dam.lat,ref.LONG,ref.LAT)
            if km>5: raise ValueError(f'{name}: dam/observation location mismatch {km:.1f} km')
            capacity=float(dam.capacity_mcm)
            audit.update(dam_seq=int(dam.dam_seq),model_name=dam.dam_name,reference_name=ref.DAM_NAME,
                         model_lon=dam.lon,model_lat=dam.lat,reference_lon=ref.LONG,reference_lat=ref.LAT,
                         location_distance_km=km,capacity_mcm=capacity,reference_agency=ref.AGENCY_CODE,
                         status='MATCHED_CALENDAR_LABELS_PHYSICAL_DAY_BOUNDARY_UNVERIFIED')
            path=args.series/f'ResOpsUS_{id}.csv'
            if path.is_file():
                obs=observations(path,index)
                audit.update(reference_file=str(path),reference_sha256=sha(path))
                for v in VARS:
                    frame[f'obs_{v}']=obs[v];frame[f'sim_{v}']=sim[id][v]
            else: audit.update(status='NO_REFERENCE',reference_file=str(path))
        else:
            rejected=global_params[global_params.grand_id==2356]
            audit.update(mapping='NO_VERIFIED_US_FORT_RANDALL_ID',model_status='NO_VERIFIED_US_MODEL_PARAMETER',
                         rejected_id=2356,rejected_reason='Canadian same-name reservoir; prohibited by task',
                         rejected_lon=float(rejected.lon.iloc[0]) if len(rejected)==1 else np.nan,
                         rejected_lat=float(rejected.lat.iloc[0]) if len(rejected)==1 else np.nan)
        frames[name]=frame
        for v in VARS:
            m=metrics(frame[f'obs_{v}'],frame[f'sim_{v}'])
            row=dict(order=order,reservoir=name,grand_id=id,variable=v,units='MCM' if v=='storage' else 'm3/s',
                     alignment='calendar_labels_no_shift',kge_version='Gupta2009_sample_std_ddof1',**m)
            if v=='storage':
                sm,seasonal=stock_metrics(frame.obs_storage,frame.sim_storage,index,capacity)
                row.update({f'provisional_{k}':val for k,val in sm.items()})
                for key in ['rmse','kge','pbias_pct','mean_bias','correlation','nrmse_capacity','range_obs','range_sim','range_error',
                            'seasonal_cycle_rmse','seasonal_amplitude_error','seasonal_peak_month_error']:
                    row[key]=np.nan
                row['comparison_status']='UNVERIFIED_STORAGE_TIME_SUPPORT' if id else 'NO_REFERENCE'
                row['undefined_reasons']='Model is time-weighted daily mean; ACE_OMA storage daily statistic/hour/timezone not documented locally'
                for month in seasonal.index:
                    seasonal_rows.append(dict(reservoir=name,grand_id=id,month=month,obs_mean=seasonal.loc[month,('obs','mean')],
                        sim_mean=seasonal.loc[month,('sim','mean')],n=seasonal.loc[month,('obs','count')],status='EXPLORATORY_STORAGE'))
            else: row['comparison_status']='CALENDAR_DAY_METRIC_TIMEZONE_UNVERIFIED' if id else 'NO_REFERENCE'
            allmetrics.append(row)
            _,_,mask=paired(frame[f'obs_{v}'],frame[f'sim_{v}'])
            dates=index[mask]
            audit.update({f'{v}_obs_n':int(frame[f'obs_{v}'].notna().sum()),f'{v}_sim_n':int(frame[f'sim_{v}'].notna().sum()),
                          f'{v}_paired_n':int(mask.sum()),f'{v}_paired_first':str(dates[0].date()) if len(dates) else '',
                          f'{v}_paired_last':str(dates[-1].date()) if len(dates) else ''})
            for side in ['obs','sim']:
                s=frame[f'{side}_{v}']
                negative=s<0
                for date,value in s[negative].items(): quality.append(dict(reservoir=name,date=str(date.date()),variable=v,side=side,value=value,flag='NEGATIVE_VALUE_RETAINED_REVIEW_REQUIRED'))
                if v=='storage' and capacity>0:
                    for date,value in s[s>capacity].items(): quality.append(dict(reservoir=name,date=str(date.date()),variable=v,side=side,value=value,flag='EXCEEDS_PARAMETER_CAPACITY_NOT_CLIPPED'))
        audits.append(audit)
        proxy=balance_proxies(frame)
        diagnostic=dict(order=order,reservoir=name,grand_id=id,**classify(frame,capacity),
                        reference_status=audit['status'],full_water_balance_status='无法完整检验',
                        full_water_balance_reason='No interval-end reservoir states or complete other-source/sink ledger; volresv is daily mean',
                        storage_support='model_daily_time_weighted_mean_vs_observation_daily_statistic_unverified')
        if id is None: diagnostic.update(diagnosis_class='D',provisional_class='D',classification_status='NO_REFERENCE')
        for side in ['obs','sim']:
            series=proxy[f'{side}_centered_mean_storage_proxy_residual_mcm']
            diagnostic[f'{side}_proxy_residual_rmse_mcm']=float(np.sqrt((series**2).mean())) if series.notna().any() else np.nan
            diagnostic[f'{side}_proxy_residual_n']=int(series.notna().sum())
        diagnostics.append(diagnostic)
        for row in detect_events(frame): events.append(dict(reservoir=name,grand_id=id,**row))
        combined=frame.join(proxy)
        combined.to_csv(args.out/'data'/f'{norm(name)}_daily.csv',index_label='date')
        plot_timeseries(name,id,frame,capacity,args.out/'figures','unverified')
        if id: plot_process(name,proxy,args.out/'figures')
    propagation=[]
    for (up,_,_),(down,_,_) in zip(TARGETS[:-1],TARGETS[1:]):
        a=frames[up];b=frames[down]
        eo=a.sim_outflow-a.obs_outflow;ei=b.sim_inflow-b.obs_inflow
        m=metrics(eo,ei)
        propagation.append(dict(upstream=up,downstream=down,n=m['n'],same_day_error_correlation=m['correlation'],
                                status='EXPLORATORY_NOT_CAUSAL' if m['n']>=3 else 'NO_REFERENCE',
                                limitation='lateral_inflow_and_travel_time_not_separated'))
    tables={'reservoir_metrics':allmetrics,'reservoir_matching_audit':audits,'reservoir_diagnostics':diagnostics,
            'high_flow_events':events,'storage_seasonal_cycle':seasonal_rows,'error_propagation':propagation,'quality_flags':quality}
    for name,rows in tables.items():
        df=pd.DataFrame(rows)
        if df.empty: df=pd.DataFrame(columns=['reservoir','status','reason'])
        df.to_csv(args.out/'tables'/f'{name}.csv',index=False)
    evidence=source_evidence(args,args.out/'provenance')
    search=dict(found_raw_root=str(RAW),searched_roots=['/share/home/dq013/zhwei/OpenBench','/share/home/dq134/wzx/cama',
          '/share/home/dq134/wzx/openbench','/share/home/dq061/wzx/openbench'],
          search_limits='maxdepth 7; pruned .git/.venv/cache/output/openbench_output',
          inaccessible=['Data_ref/grid_ref/Forcing/CARE','Data_ref/Temporary/soilmoisture','simulations/LS3MIP'],
          missing=['Reliable US Fort Randall observation ID/file','Observation storage daily statistic and physical day boundary',
                   'History interval-boundary reservoir storage and complete source/sink ledger','Binary-matched build/source provenance'])
    # Existing evaluation evidence is inventoried only; no old result is overwritten or treated as truth.
    prior=Path('/share/home/dq134/wzx/CoLM/DLB_nml/EXP000_spinup_USA')
    previous=[]
    for root in [prior/'config/openbench/missouri',prior/'config/openbench_manual/missouri',prior/'openbench_output']:
        if root.is_dir():
            previous.extend(str(x) for x in root.glob('dam_*.yaml'))
            previous.extend(str(x) for x in root.glob('*missouri*dam*/run_manifest.json'))
    observation_crosschecks=[]
    for name,id,_ in TARGETS:
        if id is None: continue
        path=Path('/share/home/dq013/zhwei/OpenBench/Reference/Station/Anth/Dam/ResOpsUS/dataset')/f'ResOpsUS_{id}.nc'
        if not path.is_file():
            observation_crosschecks.append(dict(grand_id=id,status='PROCESSED_NETCDF_MISSING'));continue
        with xr.open_dataset(path) as ds:
            for v in VARS:
                series=pd.Series(clean(ds[v].values.squeeze()),index=pd.DatetimeIndex(ds.time.values).normalize()).reindex(index)
                expected=frames[name][f'obs_{v}']
                o,s,valid=paired(expected,series)
                observation_crosschecks.append(dict(grand_id=id,variable=v,n=int(valid.sum()),
                     raw_csv_matches_processed_netcdf=bool(np.allclose(o,s,equal_nan=True,rtol=1e-6)),
                     finite_mask_equal=bool(np.array_equal(np.isfinite(expected),np.isfinite(series)))))
    nml=Path('/share/home/dq061/wzx/CoLM/DLB_nml/EXP000_spinup_USA/config/colm/EXP000_spinup_USA_D_0p1.nml')
    switches={}
    if nml.is_file():
        for line in nml.read_text().splitlines():
            if line.lstrip().startswith('!'): continue
            if '=' in line:
                key,value=line.split('=',1)
                if key.strip() in ['DEF_Reservoir_Method','DEF_USE_BIFURCATION','DEF_USE_LEVEE','DEF_GridRiverLake_FloodFeedback',
                                   'DEF_USE_Dynamic_Lake','DEF_Runoff_SCHEME','DEF_HIST_FREQ','DEF_HIST_groupby']:
                    switches[key.strip()]=value.split('!')[0].strip()
    provenance=dict(observation_crosschecks=observation_crosschecks,model_namelist=dict(path=str(nml),sha256=sha(nml) if nml.is_file() else None,switches=switches),args={k:str(v) for k,v in vars(args).items()},history_files=inventory,source_evidence=evidence,
                    parameters={'path':str(args.params),'sha256':sha(args.params)},attributes={'path':str(args.attributes),'sha256':sha(args.attributes)},
                    existing_openbench_evidence=previous,script_sha256=sha(__file__),search=search,
                    time_policy='Native noon labels map to their own date; no user registry offset; physical operator day unresolved',
                    storage_policy='Do not equate unknown snapshots to daily means. Formal storage metrics NaN; explicitly provisional calendar-label statistics retained.',
                    classification_thresholds={'positive_flow_pbias_pct':10,'high_storage_bias_fraction_capacity':.05,
                    'reasonable_storage_nrmse':.1,'reasonable_storage_correlation':.5,'good_outflow_kge':.5,'good_outflow_abs_pbias_pct':10},
                    numerical_conservation_test='UNAVAILABLE; proxy residuals are not numerical errors')
    (args.out/'provenance/manifest.json').write_text(json.dumps(provenance,indent=2,ensure_ascii=False)+'\n')
    write_report(args,allmetrics,audits,diagnostics,propagation,events)
    print(json.dumps({'output':str(args.out),'reservoirs':len(audits),'matched_references':sum(r['grand_id'] is not None for r in audits),
                      'metrics_rows':len(allmetrics),'event_rows':len(events),'quality_flags':len(quality)},ensure_ascii=False),flush=True)


def write_report(args,rows,audits,diags,propagation,events):
    def fmt(value): return f'{value:.3f}' if isinstance(value,(float,np.floating)) and np.isfinite(value) else str(value)
    report=[f'# Missouri 六座干流水库联合诊断：{args.start}—{args.end}',
      '\n## 结论与适用范围',
      '分析直接使用 E1_D 原始月历史和 ResOpsUS 原始 CSV。仅五座可做可靠空间/ID 匹配；Fort Randall 为 NO_REFERENCE，亦无可靠美国模型参数匹配。ID 2356 坚决排除。',
      '流量按原始日标签配对，未移动日期、插值或补零；操作方物理日界/时区没有本地证据。流量指标是日标签基准结果，时间对齐仍有物理不确定性。',
      '模型 volresv 为路由子步时间加权日均。ResOpsUS 表只声明 Daily/MCM，ACE_OMA 对库存统计方式、具体小时及日界未说明。故严格库存指标记 NaN；provisional_* 和图中标记 EXPLORATORY 的统计仅供量级、变化范围和季节性筛查，不能当作日均库存的已验证评价。',
      '## 数据与时间一致性',
      f'- 原始属性：`{args.attributes}`；时序：`{args.series}`。',f'- 参数：`{args.params}`；历史：`{args.history}`。',
      '- 逐文件通过 resv_GRAND_ID 查列，不使用 dam_seq 或数组位置直接索引历史。dam_seq 仅用于参数审计。名称、州和经纬度距离共同核对（阈值 5 km）。',
      '- qresv_in/qresv_out 文件真实单位 m^3/s，保持 m3/s；volresv 的 m^3 除以 1e6 转 MCM；观测 README 明示相同目标单位。',
      '- 重复/非单调时间直接报错。缺月在 manifest 标 MISSING，日网格保持 NaN；负值和超过参数库容记录 quality_flags，不裁剪。',
      '- 历史 noon 时间标签映射当天，未使用旧用户配置的 -1 day 偏移。无 history_window_* 时，仅能从文件日频率及现存源码推断完整日窗，缺少可执行文件对应源码版本证明。',
      '\n|水库|ID|dam_seq|位置距离 km|入流有效对|出流有效对|库存标签有效对|状态|', '|---|---:|---:|---:|---:|---:|---:|---|']
    for a in audits:
        report.append('|'+ '|'.join(str(a.get(k,'')) for k in ['reservoir','grand_id','dam_seq','location_distance_km','inflow_paired_n','outflow_paired_n','storage_paired_n','status'])+'|')
    report += ['\n## 指标定义',
      'KGE 为 Gupta 2009：1−sqrt[(r−1)²+(α−1)²+(β−1)²]，α=std(sim)/std(obs)，β=mean(sim)/mean(obs)，标准差 ddof=1。',
      'NSE=1−Σ(sim−obs)²/Σ(obs−mean(obs))²；PBIAS=100Σ(sim−obs)/Σobs，正值表示模拟高估。每变量全部指标使用同一个成对有限值掩膜；少于3对、零分母/零标准差分别记 NaN 并解释。',
      '库存 nRMSE 仅以参数 dam_TotalVol_mcm 归一化，非均值/极差归一化。月气候态仅共同有效日，12个月各至少15对；输出幅度误差、峰值月份差、月循环 RMSE。仅两年，不能代表长期气候态。',
      '\n## 逐库结果与联合证据']
    for name,id,_ in TARGETS:
        d=next(x for x in diags if x['reservoir']==name)
        report += [f'\n### {name}（ID {id if id else "未核实"}）']
        if id is None:
            report += ['NO_REFERENCE：本地原始属性表无可信 Fort Randall 条目；区域参数未找到可信美国同名坝。全局 ID 2356 位于 (-56.779,48.171)，不可替代。图为缺数据占位说明，不是模拟结果。'];continue
        for var in VARS:
            row=next(x for x in rows if x['reservoir']==name and x['variable']==var)
            if var!='storage':
                report.append(f"- {var}：N={row['n']}，KGE={fmt(row['kge'])}，NSE={fmt(row['nse'])}，r={fmt(row['correlation'])}，PBIAS={fmt(row['pbias_pct'])}%，rSD={fmt(row['rsd'])}，均值比={fmt(row['mean_ratio'])}，RMSE={fmt(row['rmse'])} m3/s。")
            else:
                report.append(f"- 库存探索性标签比较：N={row['n']}，RMSE={fmt(row['provisional_rmse'])} MCM，nRMSE(cap)={fmt(row['provisional_nrmse_capacity'])}，均值偏差={fmt(row['provisional_mean_bias'])} MCM，r={fmt(row['provisional_correlation'])}。共同日变化范围 obs/sim={fmt(row['provisional_range_obs'])}/{fmt(row['provisional_range_sim'])} MCM；月循环 RMSE={fmt(row['provisional_seasonal_cycle_rmse'])} MCM。严格评价尚不能成立。")
        report += [f"- 联合掩膜 N={d['n_joint']}；探索性类别 **{d['provisional_class']}**，正式保守类别 **{d['diagnosis_class']}（时间支持未核实）**。共同掩膜入/出流 PBIAS={fmt(d['joint_inflow_pbias_pct'])}%/{fmt(d['joint_outflow_pbias_pct'])}%；库存偏差/参数库容={fmt(d['provisional_storage_bias_fraction_capacity'])}。",
          f"- 模型日均库存的中心差分代理残差 RMSE={fmt(d['sim_proxy_residual_rmse_mcm'])} MCM；这只是近似调蓄一致性量，不是数值守恒误差。",
          f"- 评价首30日库存标签偏差={fmt(d['first30_storage_bias_mcm'])} MCM，不等于模拟2000年初始库存误差。初值归因需2000年起算状态/重启记录。",
          f'- [三面板时序](../figures/{norm(name)}_inflow_outflow_storage.png)；[调蓄代理](../figures/{norm(name)}_regulation_proxies.png)。']
    report += ['\n## A/B/C/D 分类与根因边界',
      '探索性阈值：流量高估 PBIAS>10%；库存高估为均值偏差>5%总库容；库存较合理要求绝对偏差≤5%库容、nRMSE≤0.1、r≥0.5；出流较好要求KGE≥0.5且|PBIAS|≤10%。这些是明确可复查的筛查阈值，不是物理定律。',
      'A=入出流均高估且库存高估；B=入出流均高估而库存未明显高估；C=入流高估、出流较好且库存较合理；其余D。由于库存时间支持不明，正式类别保守为D/不确定，另列 provisional_class，不能据此确认根因。',
      'A 需要检查上游来水、容量/水位曲线和初值；B 与来水误差向出流传递相容，但也可能有净源汇、日界或调度差异；C 与模型调蓄改善出流相容，不能证明调度参数正确。',
      '\n## 上下游误差传播',
      '按六座坝顺序只比较相邻条目，Fort Randall 缺失不跳过当作直接相邻。计算上游出流误差与下游入流误差的同日相关；不能排除共同气象驱动、河段汇流和侧向来水。']
    for r in propagation: report.append(f"- {r['upstream']} → {r['downstream']}：N={r['n']}，误差相关={fmt(r['same_day_error_correlation'])}，{r['status']}。")
    report += ['\n## 调蓄、事件及水量守恒',
      '逐日 I−O、库存变化及两种时间支持假设下的简化残差见 data/*_daily.csv。模拟均值库存采用 R̄=ΔS̄−0.5[(I−O)t+(I−O)t−1]·86400/1e6；在窗内流量变化时也只是近似。日末假设代理另列，不择优选择。',
      '事件由观测入流90分位阈值、0.5σ突出度、至少30天间隔确定，采用同一锚点±14天主窗口，并检查±7、±21天敏感性。边界截断、窗口重叠、缺测、峰值并列/落边界均标记，不强行配对。削峰率=1−Qout,peak/Qin,peak，峰现差=tout,peak−tin,peak；均为日均峰值，非瞬时洪峰。候选高流量过程未独立确认是同一洪水，因果配对仍待人工核实。',
      f'- 事件记录数（含窗口敏感性与不可计算标记）：{len(events)}，详见 tables/high_flow_events.csv。',
      '**无法完整检验数值水量守恒。** 当前 Grid 分支先计算 V*=V−visible_hflux·dt，再令 V=max(V*,0)，并有内流洼地容量约束；水库 qin 在限流/上游传播阶段还会重算。历史对状态和通量分别按路由子步时间积分取平均。应使用完整子步净通量及裁剪/边界账本核算，不能以日均状态差代替边界状态差。实际 nml：Reservoir_Method=1（Funato）、BIFURCATION=false、LEVEE=false、FloodFeedback=false、Dynamic_Lake=true、Runoff_SCHEME=3；因此本次不应把关闭的洪泛反馈当作既成误差来源，湖泊/陆面交换和具体净通量计入方式仍需完整账本。缺少日界库存、逐项源汇及执行文件版本证据，代理残差不等于数值错误。',
      '\n## 优先核查与 AAS 论文建议',
      '1. 向 ACE Omaha 原始数据元信息确认库存时刻、物理日界、流量是否反推自水量平衡；先解决时间支持，不能靠平移使 KGE 最大而自行选择日期。',
      '2. 核对该实际可执行文件对应的源码/构建哈希。重点 MOD_Grid_RiverLakeFlow.F90（净通量、限水与时间积分）、MOD_Grid_RiverLakeHist.F90（acctime_resv归一）、MOD_Grid_RiverLakeHistRoute.F90（ID与时间窗）；摘录与哈希见 provenance/source_evidence.txt。',
      '3. 核查 MOD_Catch_Reservoir.F90 的 Funato 分区阈值、dam_TotalVol_mcm/ConVol/FldVol、Qn/Qf、库容曲线是否覆盖/修改参数容量。只有在入流误差分离后再讨论调度参数。',
      '4. 对初始库存，增加2000年起算/重启状态检查；对连续性，增加逐日边界库存及各源汇累计量输出作为独立后续实验，不在本次修改模型物理。',
      '5. AAS Missouri 部分应展示流域顺序三变量图和成对样本数，区分水文来水与运行调度两层误差；明确5座可匹配、Fort Randall缺失、2001–2002为短时窗，避免推广为长期或洪水普遍结论。',
      '6. 报告严格与探索性库存结果的区别；不以两个正 PBIAS 断言 Dam 方案无效，不以出流改善单独断言调度正确。',
      '\n## 复现与输出',
      f'命令参数与所有月文件、观测、参数、源码证据见 `{args.out}/provenance/manifest.json`。原始观测、模型输出及既有 OpenBench 产物均未覆盖。正式表为 tables/reservoir_metrics.csv；探索性库存量带 provisional_ 前缀。']
    (args.out/'report/missouri_reservoir_diagnostics.md').write_text('\n\n'.join(report)+'\n')


if __name__=='__main__': main()
