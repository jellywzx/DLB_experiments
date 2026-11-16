from types import SimpleNamespace
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import analyze as a
import numpy as np
import pandas as pd
import pytest
import xarray as xr


def test_metrics_perfect_and_one_common_mask():
    m=a.metrics([1,2,np.nan,4],[1,2,999,4])
    assert m['n']==3 and m['kge']==pytest.approx(1) and m['nse']==pytest.approx(1)
    assert m['rmse']==0 and m['pbias_pct']==0 and m['rsd']==1 and m['mean_ratio']==1


def test_kge_gupta2009_bias_sign():
    m=a.metrics([1,2,3,4],[2,4,6,8])
    assert m['rsd']==pytest.approx(2) and m['mean_ratio']==2 and m['pbias_pct']==100
    assert m['kge']==pytest.approx(1-np.sqrt(2))


def test_zero_denominators_and_short_samples():
    m=a.metrics([0,0,0],[1,1,1])
    for key in ['pbias_pct','mean_ratio','kge','nse','correlation','rsd']: assert np.isnan(m[key])
    assert m['rmse']==1 and 'zero_observed_mean' in m['undefined_reasons']
    assert np.isnan(a.metrics([1,np.nan],[1,2])['rmse'])


def test_capacity_normalization_and_seasonal_coverage():
    dates=pd.date_range('2001-01-01',periods=365)
    obs=np.sin(np.arange(365)/40)+10
    m,_=a.stock_metrics(obs,obs+1,dates,10)
    assert m['nrmse_capacity']==pytest.approx(.1) and m['seasonal_cycle_rmse']==pytest.approx(1)
    assert m['range_error']==pytest.approx(0)
    m,_=a.stock_metrics(obs,obs+1,dates,0)
    assert np.isnan(m['nrmse_capacity'])


def test_months_selected_by_real_ids_and_gaps_preserved(tmp_path):
    for month,ids in [(1,[307,753]),(2,[753,307])]:
        ds=xr.Dataset({'resv_GRAND_ID':('reservoir',ids)},coords={'time':pd.date_range(f'2001-{month:02d}-01T12:00',periods=2)})
        for key in ['qresv_in','qresv_out','volresv']:
            ds[key]=(('time','reservoir'),np.tile(ids,(2,1))* (1e6 if key=='volresv' else 1))
            ds[key].attrs['units']='m^3' if key=='volresv' else 'm^3/s'
        ds.to_netcdf(tmp_path/f'test_2001-{month:02d}.nc')
    args=SimpleNamespace(history=tmp_path,prefix='test_',start='2001-01-01',end='2001-02-28')
    index=pd.date_range(args.start,args.end)
    data,inventory=a.read_histories(args,[307,753],index)
    assert data[307].loc['2001-01-01','inflow']==307
    assert data[307].loc['2001-02-01','inflow']==307
    assert data[753].loc['2001-02-01','storage']==753
    assert np.isnan(data[307].loc['2001-01-03','inflow'])
    assert inventory[1]['indices']['307']==1


def test_observation_duplicates_raise_and_missing_dates_stay_missing(tmp_path):
    p=tmp_path/'obs.csv'
    pd.DataFrame({'date':['2001-01-01','2001-01-03'],'storage':[1,2],'inflow':[2,3],'outflow':[3,4]}).to_csv(p,index=False)
    d=a.observations(p,pd.date_range('2001-01-01',periods=3))
    assert d.loc['2001-01-02'].isna().all()
    raw=pd.read_csv(p);raw.loc[1,'date']='2001-01-01';raw.to_csv(p,index=False)
    with pytest.raises(ValueError,match='duplicate'): a.observations(p,pd.date_range('2001-01-01',periods=3))


def test_nonconsecutive_storage_difference_not_bridged():
    s=pd.Series([1,2,5],index=pd.to_datetime(['2001-01-01','2001-01-02','2001-01-04']))
    d=a.consecutive_difference(s)
    assert d.iloc[1]==1 and np.isnan(d.iloc[2])


def test_event_peaks_are_paired_within_one_observed_window():
    days=np.arange(120);frame=pd.DataFrame(index=pd.date_range('2001-01-01',periods=120))
    frame['obs_inflow']=100+300*np.exp(-((days-60)/3)**2)
    frame['obs_outflow']=50+150*np.exp(-((days-62)/3)**2)
    frame['sim_inflow']=100+360*np.exp(-((days-60)/3)**2)
    frame['sim_outflow']=50+180*np.exp(-((days-63)/3)**2)
    events=a.detect_events(frame)
    main=[r for r in events if r['radius_days']==14][0]
    assert main['obs_lag_days']==2 and main['sim_lag_days']==3
    assert main['obs_attenuation']==pytest.approx(.5)
    frame.iloc[60,0]=np.nan
    assert all(r['status']!='CANDIDATE_WINDOW_COMPLETE' for r in a.detect_events(frame))


def test_unknown_stock_support_cannot_produce_confirmed_A():
    f=pd.DataFrame({f'{side}_{v}':np.arange(10)+10+(100 if side=='sim' else 0)
                    for side in ['obs','sim'] for v in a.VARS},index=pd.date_range('2001-01-01',periods=10))
    d=a.classify(f,100)
    assert d['provisional_class']=='A' and d['diagnosis_class']=='D'
    assert a.classify(f,100,'daily_mean')['diagnosis_class']=='A'


def test_canadian_fort_randall_is_never_a_target():
    assert 2356 not in [id for _,id,_ in a.TARGETS]
    assert next(id for name,id,_ in a.TARGETS if name=='Fort Randall') is None
    ds=xr.Dataset({'resv_GRAND_ID':('reservoir',[307,307])})
    with pytest.raises(ValueError,match='Duplicate'): a.reservoir_index(ds)
    with pytest.raises(ValueError,match='units'): a.reservoir_unit('acre-feet','Dam_Water_Storage')


@pytest.mark.parametrize('hour,seconds,message', [(0,86400,'time support'), (12,43200,'averaging window')])
def test_unknown_time_support_is_rejected(tmp_path,hour,seconds,message):
    ds=xr.Dataset({'resv_GRAND_ID':('reservoir',[307]),
                   'history_window_seconds':('time',[seconds]*3)},
                  coords={'time':pd.date_range(f'2001-01-01T{hour:02d}:00',periods=3)})
    for variable in a.VARS.values():
        ds[variable]=(('time','reservoir'),np.ones((3,1)))
        ds[variable].attrs['units']='m^3' if variable=='volresv' else 'm^3/s'
    ds.to_netcdf(tmp_path/'test_2001-01.nc')
    args=SimpleNamespace(history=tmp_path,prefix='test_',start='2001-01-01',end='2001-01-03')
    with pytest.raises(ValueError,match=message):
        a.read_histories(args,[307],pd.date_range(args.start,args.end))
