"""Paired-date metrics and explicitly provisional reservoir process diagnostics."""
import numpy as np
import pandas as pd
from scipy.signal import find_peaks


def metrics(obs, sim):
    o, s = np.asarray(obs,float), np.asarray(sim,float)
    mask = np.isfinite(o) & np.isfinite(s); o, s = o[mask], s[mask]
    out = dict(n=len(o), **{k:np.nan for k in ['KGE','correlation','rSD','percent_bias','RMSE','NSE','mean_obs','mean_sim','mean_bias']})
    reasons = []
    if not len(o):
        out['undefined_reasons'] = 'no_valid_pairs'; return out
    out.update(mean_obs=o.mean(), mean_sim=s.mean(), mean_bias=(s-o).mean(), RMSE=np.sqrt(np.mean((s-o)**2)))
    if o.sum() != 0:
        out['percent_bias'] = 100*(s-o).sum()/o.sum()
    else:
        reasons.append('zero_observed_sum')
    # Both standard deviations use population ddof=0, as in OpenBench xarray.std.
    if len(o) >= 2 and np.ptp(o) > 0:
        out['rSD'] = s.std()/o.std()
        out['NSE'] = 1-((s-o)**2).sum()/((o-o.mean())**2).sum()
        if np.ptp(s) > 0:
            out['correlation'] = np.corrcoef(o,s)[0,1]
        else:
            reasons.append('constant_simulation')
    else:
        reasons.append('constant_or_single_observation')
    if np.isfinite(out['correlation']) and np.isfinite(out['rSD']) and o.mean() != 0:
        out['KGE'] = 1-np.sqrt((out['correlation']-1)**2+(out['rSD']-1)**2+(s.mean()/o.mean()-1)**2)
    out['undefined_reasons'] = ';'.join(reasons)
    return out


def seasonal(frame, identifier):
    rows = []
    for v in ['inflow','outflow','storage']:
        z = frame[[f'obs_{v}',f'sim_{v}']].copy()
        z.loc[~np.isfinite(z).all(axis=1),:] = np.nan
        for month in range(1,13):
            a = z[z.index.month == month]
            rows.append(dict(ID=identifier,variable=v,month=month,n_pair=int(a.notna().all(axis=1).sum()),
                             obs_mean=a.iloc[:,0].mean(),sim_mean=a.iloc[:,1].mean(),
                             obs_std=a.iloc[:,0].std(),sim_std=a.iloc[:,1].std()))
    return rows


def joint(frame, identifier, capacity):
    valid = np.isfinite(frame).all(axis=1)
    z = frame[valid]
    mi = metrics(z.obs_inflow,z.sim_inflow); mo = metrics(z.obs_outflow,z.sim_outflow); ms = metrics(z.obs_storage,z.sim_storage)
    bias = ms['mean_bias']/capacity if np.isfinite(capacity) and capacity>0 else np.nan
    nr = ms['RMSE']/capacity if np.isfinite(capacity) and capacity>0 else np.nan
    provisional = 'UNDETERMINED'
    if len(z) >= 3 and np.isfinite(bias):
        if mi['percent_bias']>10 and mo['percent_bias']>10:
            provisional = 'A' if bias>.05 else 'B'
        elif mi['percent_bias']>10 and mo['KGE']>=.5 and abs(mo['percent_bias'])<=10 and abs(bias)<=.05 and nr<=.1 and ms['correlation']>=.5:
            provisional = 'C'
        else:
            provisional = 'D'
    return dict(ID=identifier,n_joint=int(valid.sum()),joint_inflow_pbias=mi['percent_bias'],joint_outflow_pbias=mo['percent_bias'],
                joint_outflow_KGE=mo['KGE'],provisional_storage_bias_fraction_capacity=bias,provisional_storage_nrmse=nr,
                provisional_class=provisional,classification_status='PROVISIONAL_TIME_SUPPORT_UNVERIFIED',
                formal_class='UNDETERMINED',full_water_balance='NOT_TESTABLE_WITH_DAILY_MEAN_STOCK_AND_INCOMPLETE_SOURCE_SINK_LEDGER')


def balance_proxies(frame):
    result = pd.DataFrame(index=frame.index)
    step = frame.index.to_series().diff() == pd.Timedelta(days=1)
    for side in ['obs','sim']:
        q = frame[f'{side}_inflow']-frame[f'{side}_outflow']
        delta = frame[f'{side}_storage'].diff().where(step)
        result[f'{side}_in_minus_out_m3s'] = q
        result[f'{side}_delta_storage_mcm'] = delta
        result[f'{side}_provisional_residual_mcm'] = delta-(q+q.shift(1))*.5*86400/1e6
    return result


def events(frame, identifier, radius=14):
    q = frame.obs_inflow; finite = q.dropna()
    if len(finite)<30 or finite.std()==0:
        return []
    peaks,_ = find_peaks(q.fillna(-np.inf).values,height=finite.quantile(.9),prominence=finite.std()*.5,distance=30)
    records = []
    cols = [f'{side}_{v}' for side in ['obs','sim'] for v in ['inflow','outflow']]
    for event, peak in enumerate(peaks,1):
        lo,hi = peak-radius,peak+radius
        row = dict(ID=identifier,event=event,anchor=str(frame.index[peak]),radius_days=radius,
                   status='CANDIDATE_WINDOW_COMPLETE',limitation='daily_mean_peaks;causal_event_identity_unverified')
        if lo<0 or hi>=len(frame):
            row['status']='INCOMPLETE_BOUNDARY_WINDOW'
        elif any(abs(int(peak)-int(other))<=2*radius for other in peaks if other!=peak):
            row['status']='OVERLAPPING_EVENT_WINDOWS'
        else:
            window=frame.iloc[lo:hi+1]
            if not np.isfinite(window[cols]).all().all():
                row['status']='INCOMPLETE_FLOW_COVERAGE'
            else:
                flags=[]
                for col in cols:
                    a=window[col]; maximum=a.max(); dates=a.index[a==maximum]
                    row[col+'_peak']=maximum;row[col+'_peak_date']=str(dates[0])
                    if len(dates)!=1 or dates[0] in [window.index[0],window.index[-1]]:
                        flags.append(col)
                row['start']=str(window.index[0]);row['end']=str(window.index[-1])
                if flags:
                    row['status']='AMBIGUOUS_PEAK';row['limitation']+=';'+','.join(flags)
                else:
                    for side in ['obs','sim']:
                        inflow=row[side+'_inflow_peak']
                        row[side+'_attenuation']=1-row[side+'_outflow_peak']/inflow if inflow>0 else np.nan
                        row[side+'_lag_days']=(pd.Timestamp(row[side+'_outflow_peak_date'])-pd.Timestamp(row[side+'_inflow_peak_date'])).days
        records.append(row)
    return records
