import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from io_openbench import unit_factor, read_series, discover, load_station, ITEMS
from diagnostics import metrics, balance_proxies, events


def write_series(path,times,values,unit='mcm',var='storage'):
    xr.Dataset({var:('time',values,{'units':unit})},coords={'time':times}).to_netcdf(path)


def test_processed_storage_not_converted_twice(tmp_path):
    path=tmp_path/'a.nc';t=pd.date_range('2001-01-01',periods=2)
    write_series(path,t,[10.,20.]);s,records=read_series([path],'storage','storage')
    assert s.tolist()==[10.,20.] and records[0]['factor']==1
    write_series(tmp_path/'b.nc',t,[10e6,20e6],unit='m3')
    converted,_=read_series([tmp_path/'b.nc'],'storage','storage')
    np.testing.assert_allclose(converted,s)


def test_unknown_units_fail():
    with pytest.raises(ValueError):unit_factor('acre feet per fortnight','storage')


def test_duplicate_days_rejected(tmp_path):
    path=tmp_path/'a.nc';write_series(path,pd.to_datetime(['2001-01-01','2001-01-01']),[1.,2.])
    with pytest.raises(ValueError):read_series([path],'storage','storage')


def test_overlapping_months_rejected(tmp_path):
    t=pd.date_range('2001-01-01',periods=2)
    for name in ['a','b']:write_series(tmp_path/(name+'.nc'),t,[1.,2.])
    with pytest.raises(ValueError):read_series(list(tmp_path.glob('*.nc')),'storage','storage')


def test_pair_mask_and_pbias():
    result=metrics([1,2,np.nan,4],[2,4,900,np.nan])
    assert result['n']==2 and result['percent_bias']==100
    assert result['rSD']==2 and result['KGE']==pytest.approx(1-np.sqrt(2))


def test_zero_and_constant_series():
    m=metrics([0,0,0],[1,2,3]);assert np.isnan(m['percent_bias']) and np.isnan(m['KGE'])
    m=metrics([1,2,3],[2,2,2]);assert m['rSD']==0 and np.isnan(m['correlation'])
    assert metrics([np.nan],[1])['n']==0


def test_balance_does_not_bridge_missing_days():
    t=pd.to_datetime(['2001-01-01','2001-01-03'])
    d=pd.DataFrame({f'{s}_{v}':[1.,2.] for s in ['obs','sim'] for v in ITEMS},index=t)
    p=balance_proxies(d);assert p.obs_delta_storage_mcm.isna().all()


def build_run(tmp_path):
    root=tmp_path/'eval';root.mkdir();(root/'metrics').mkdir();(root/'data/stn_Obs_Test').mkdir(parents=True)
    namelists={'reference':{},'simulation':{}}
    for v,item in ITEMS.items():
        namelists['reference'][item]={'Obs_varname':v};namelists['simulation'][item]={'Test_varname':v}
    manifest={'canonical_config':{'project':{'years':[2004,2004],'tim_res':'Day'},'simulation':{'Test':{'root_dir':'unused'}},
              'reference':{'sources':{v:'Obs' for v in ITEMS.values()}},'evaluation':{'variables':list(ITEMS.values())}},
              'resolved_runtime':{'namelists':namelists}}
    (root/'run_manifest.json').write_text(json.dumps(manifest))
    pd.DataFrame([dict(ID=307,DamName='Fort Peck',Flag=True,use_syear=2004,use_eyear=2004)]).to_csv(root/'stn_Obs_Test_list.csv',index=False)
    return root


def test_dynamic_period_missing_variables_and_no_padding_zero(tmp_path):
    root=build_run(tmp_path);folder=root/'data/stn_Obs_Test';times=pd.date_range('2004-01-01T12:00',periods=2)
    write_series(folder/'Dam_Inflow_ref_307_2004_2004.nc',times,[1.,np.nan],'m3 s-1','inflow')
    write_series(folder/'Dam_Inflow_sim_307_2004_2004.nc',times,[2.,3.],'m3 s-1','inflow')
    context=discover(root);frame,audit,_=load_station(context,context['stations'].iloc[0])
    assert len(frame)==366 and frame.index[0]==times[0]
    assert frame.obs_storage.isna().all() and frame.obs_inflow.notna().sum()==1
    assert next(a for a in audit if a['variable']=='inflow')['n_pair']==1
    assert not (frame.obs_inflow==0).any()


def test_mismatched_daily_labels_rejected(tmp_path):
    root=build_run(tmp_path);folder=root/'data/stn_Obs_Test'
    for side,hour in [('ref',0),('sim',12)]:
        t=pd.date_range(f'2004-01-01T{hour:02d}:00',periods=2)
        write_series(folder/f'Dam_Inflow_{side}_307_2004_2004.nc',t,[1.,2.],'m3 s-1','inflow')
    context=discover(root)
    with pytest.raises(ValueError,match='timestamp offsets'):load_station(context,context['stations'].iloc[0])


def test_station_ids_not_in_final_list_fail(tmp_path):
    root=build_run(tmp_path)
    with pytest.raises(ValueError,match='final matched'):discover(root,ids=['999'])


def test_events_reject_incomplete_window():
    index=pd.date_range('2001-01-01',periods=120);q=np.ones(120);q[60]=50
    d=pd.DataFrame({f'{s}_{v}':q.copy() for s in ['obs','sim'] for v in ITEMS},index=index)
    d.loc[index[62],'obs_outflow']=np.nan
    result=events(d,'307');assert result and result[0]['status']=='INCOMPLETE_FLOW_COVERAGE'


def test_global_mask_does_not_expand_location_map(tmp_path, monkeypatch):
    import plotting
    path=tmp_path/'global.nc'
    xr.Dataset({'mask':(('lat','lon'),np.array([[0,0,0,0],[0,1,1,0],[0,0,0,0]]))},coords={'lat':[80,45,-80],'lon':[-170,-110,-100,170]}).to_netcdf(path)
    stations=pd.DataFrame({'ID':['307'],'LON_org':[-106.4],'LAT_org':[48.]})
    table=pd.DataFrame([{'ID':'307','variable':v,'percent_bias':10.} for v in ITEMS])
    context={'config':{'project':{'basin_mask':str(path),'lon_range':[-115,-89],'lat_range':[36,51]}}}
    observed=[]
    def inspect(fig,path):
        observed.extend([ax.get_xlim() for ax in fig.axes[:3]])
        plotting.plt.close(fig)
    monkeypatch.setattr(plotting,'save',inspect)
    plotting.location_plot(stations,table,context,tmp_path)
    assert observed==[(-115.,-89.)]*3


def full_run(tmp_path):
    from full_reference import discover_full
    root=tmp_path/'run';root.mkdir();history=tmp_path/'history';history.mkdir()
    ids=['ResOpsUS_307','ResOpsUS_753','ResOpsUS_999']
    times=pd.to_datetime(['2001-01-01T12:00','2001-01-02T12:00'])
    ref=tmp_path/'reference.nc'
    xr.Dataset({'Dam_Inflow':(('station','time'),[[np.nan,2.],[3.,4.],[5.,6.]],{'units':'m3 s-1'}),
                'Dam_Outflow':(('station','time'),np.ones((3,2)),{'units':'m3 s-1'}),
                'resops_inflow_raw_mean':(('station','time'),np.full((3,2),999.)),
                'station_name':('station',['Fort Peck','Garrison','Outside'])},
               coords={'station':[0,1,2],'station_id':('station',ids),'lon':('station',[-106.,-101.,0.]),'lat':('station',[48.,47.,0.]),'time':times}).to_netcdf(ref)
    for month in range(1,13):
        order=[307,753] if month%2 else [753,307]
        vals=np.array([[float(i) for i in order]]*2)
        xr.Dataset({'resv_GRAND_ID':('reservoir',order),
                    'qresv_in':(('time','reservoir'),vals,{'units':'m3/s'}),
                    'qresv_out':(('time','reservoir'),vals+1,{'units':'m3/s'}),
                    'volresv':(('time','reservoir'),vals*1e6,{'units':'m3'})},
                   coords={'time':pd.date_range(f'2001-{month:02d}-01T12:00',periods=2)}).to_netcdf(history/f'h_{2001}-{month:02d}.nc')
    manifest={'canonical_config':{'project':{'years':[2001,2001],'lon_range':[-115,-89],'lat_range':[36,51]},
                                 'simulation':{'E1':{'root_dir':str(history),'data_groupby':'Month','prefix':'h_'}}}}
    context=discover_full(root,manifest,'E1','ResOpsUS',list(ITEMS),ref,None)
    return context,ref


def test_full_reference_restores_ids_without_old_csv_and_reorders_model(tmp_path):
    from full_reference import prepare_full
    c,ref=full_run(tmp_path)
    assert set(c['stations'].ID)=={'307','753'} and c['variables']==['inflow','outflow']
    loaded=prepare_full(c);frame=loaded['307'][0]
    assert frame.loc['2001-02-01','sim_inflow'].iloc[0]==307
    assert frame.obs_storage.isna().all() and frame.sim_storage.dropna().iloc[0]==307
    assert frame.loc['2001-01-01','obs_inflow'].isna().all()  # never substitute raw_mean=999
    assert c['selection_audit'].set_index('ID').loc['999','selected']==False


def test_full_reference_id_validation():
    from full_reference import reference_ids
    assert reference_ids(['ResOpsUS_307'])==['307']
    with pytest.raises(ValueError,match='Duplicate'):reference_ids(['ResOpsUS_307','ResOpsUS_307'])
    with pytest.raises(ValueError,match='Invalid'):reference_ids(['station_307'])


def test_new_reference_difference_is_not_legacy_failure():
    from analyze import reconcile
    table=pd.DataFrame([dict(ID='307',variable='inflow',KGE=1.,correlation=1.,rSD=1.,percent_bias=0.)])
    context={'mode':'full','metrics':{'inflow':pd.DataFrame([dict(ID='307',KGE=-1.,correlation=.2,rSD=2.,percent_bias=50.)])}}
    assert set(reconcile(context,table).status)=={'SOURCE_DIFFERENCE'}
    context['mode']='paired';assert set(reconcile(context,table).status)=={'MISMATCH'}


def test_signed_pbias_is_labelled_not_clipped():
    m=metrics([-2.,1.],[5.,5.])
    assert m['pbias_applicability']=='SIGNED_OR_ZERO_OBS_DENOMINATOR'
    assert m['percent_bias']<0 and m['mean_bias']>0 and m['obs_negative_fraction']==.5
