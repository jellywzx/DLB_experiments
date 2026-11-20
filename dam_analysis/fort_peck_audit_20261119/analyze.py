from pathlib import Path
import json, gzip, io, hashlib
import numpy as np,pandas as pd,xarray as xr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from cartopy.io.shapereader import Reader
from shapely.geometry import box
O=Path(__file__).resolve().parent
C=Path('/share/home/dq061/wzx/CoLM/cases/EXP000_spinup_USA_D_0p1')
F=Path('/share/home/dq013/zhwei/colm/data/CoLMruntime/unitcatchment/grid_routing_data_06min.nc')
r=xr.open_dataset(C/'riverlake/unitcatchment_regional.nc');f=xr.open_dataset(F)
def up(nxt,s):
 order=np.argsort(nxt); vals=nxt[order]; seen={int(s)}; stack=[int(s)]
 while stack:
  v=stack.pop(); a,b=np.searchsorted(vals,[v,v],side='left')[0],np.searchsorted(vals,v,side='right')
  for k in order[a:b]+1:
   if int(k) not in seen:seen.add(int(k));stack.append(int(k))
 return np.array(sorted(seen))-1
rn=r.seq_next.values;fn=f.seq_next.values
ri=int(r.dam_seq.values[np.where(r.dam_GRAND_ID.values==307)[0][0]])-1
fi=int(f.dam_seq.values[np.where(f.dam_GRAND_ID.values==307)[0][0]])-1
u=up(rn,ri+1);fu=up(fn,fi+1);src=r.seq_src_index.values
x=r.seq_lon.values;y=r.seq_lat.values;a=r.topo_uparea.values
summary={'regional_dam_seq':ri+1,'full_dam_seq':fi+1,'regional_count_including_dam':len(u),'full_count_including_dam':len(fu),'missing_full_upstream':len(set(fu+1)-set(src[u])),'extra_regional_upstream':len(set(src[u])-set(fu+1)),'seq_is_1based_row':bool(np.array_equal(r.seq.values,np.arange(1,len(rn)+1))),'dam_lon':float(x[ri]),'dam_lat':float(y[ri])}
# Whole regional topology consistency against source, plus explicit upstream list.
expected=np.where(rn>0,src[np.maximum(rn-1,0)],rn)
summary['regional_full_next_mismatches']=int(np.sum(expected!=fn[src-1]))
summary['duplicate_source_rows']=len(src)-len(np.unique(src));summary['duplicate_xy']=len(x)-len(set(zip(r.seq_x.values,r.seq_y.values)))
summary['self_loops']=int(np.sum(rn==np.arange(1,len(rn)+1)))
ups=r.seq_upst.values[:,u];ups_count=r.seq_upn.values[u]
bad=[]
for col,j in enumerate(u):
 actual=set(np.where(rn==j+1)[0]+1);declared=set(ups[:int(ups_count[col]),col]);
 if actual!=declared:bad.append(int(j+1))
summary['upstream_table_mismatch']=bad
# Physical runoff input grid areas and fractional assignment by mapping partition.
mx=r.inpmat_x.values;my=r.inpmat_y.values;ma=r.inpmat_area.values
valid=(mx>0)&(my>0)&(ma>0);ids=(my-1)*3600+mx-1
allweights=np.bincount(ids[valid],weights=ma[valid],minlength=3600*1800)
uv=valid[u];uid=ids[u][uv];uw=ma[u][uv];targetweights=np.bincount(uid,weights=uw,minlength=3600*1800)
gid=np.where(targetweights>0)[0];gy,gx=gid//3600,gid%3600
lon=r.lon.values[gx];lat=r.lat.values[gy]
area=6371.22**2*np.radians(.1)*(np.sin(np.radians(lat+.05))-np.sin(np.radians(lat-.05)))
frac=targetweights[gid]/allweights[gid];weighted=area*frac
# This cached country boundary explicitly identifies Canada/USA and spans the entire target bbox.
bound=Path('/share/home/dq134/.local/share/cartopy/shapefiles/natural_earth/cultural/ne_10m_admin_0_boundary_lines_land.shp')
records=list(Reader(bound).records());border=[z for z in records if {'Canada','United States of America'} <= set(str(v) for v in z.attributes.values())]
print('BORDER',len(border),flush=True)
if not border:
 for z in records:
  if z.geometry.intersects(box(-114,48.9,-105,49.1)):print(z.attributes,flush=True)
# Along this local border the boundary dataset is the 49th parallel, use its intersected bounds.
local_border=[z.geometry.intersection(box(-116,48.8,-104,49.2)) for z in border]
summary['border_attributes']=[z.attributes for z in border];summary['border_local_bounds']=[list(z.bounds) for z in local_border if not z.is_empty]
can=y[u]>49.;gcan=lat>49.
summary.update(upstream_center_canada=int(can.sum()),input_cells_canada=int(gcan.sum()),input_canada_area_km2=float(weighted[gcan].sum()),grid_unique_cells=len(gid),grid_fractional_area_km2=float(weighted.sum()),grid_full_cells_area_km2=float(area.sum()),input_matrix_area_km2=float(uw.sum()/1e6),topo_area_sum_km2=float(r.topo_area.values[u].sum()/1e6),topo_uparea_km2=float(a[ri]/1e6),area_vs_149000_pct=float((weighted.sum()/149000-1)*100),max_mapping_share=float(frac.max()),mapping_negative_entries=int((ma<0).sum()),upstream_bbox=[float(x[u].min()),float(x[u].max()),float(y[u].min()),float(y[u].max())])
# duplicate mapping entries within a row would repeat the same source grid area.
summary['duplicate_input_pairs_in_upstream_rows']=sum(int(len(ids[j][valid[j]])-len(np.unique(ids[j][valid[j]]))) for j in u)
pd.DataFrame({'regional_seq':u+1,'full_seq':src[u],'lon':x[u],'lat':y[u],'seq_next':rn[u],'canada_center':can,'topo_area_m2':r.topo_area.values[u],'topo_uparea_m2':a[u]}).to_csv(O/'upstream_units.csv',index=False)
pd.DataFrame({'grid_x_1based':gx+1,'grid_y_1based':gy+1,'lon':lon,'lat':lat,'fraction_to_FortPeck':frac,'spherical_area_km2':area,'assigned_area_km2':weighted,'canada_center':gcan}).to_csv(O/'input_grid_area.csv',index=False)
# Grid match and spatial candidates; upstream path required for Landusky.
def candidates(name,xx,yy):
 dist=6371*2*np.arcsin(np.sqrt(np.sin(np.radians(y-yy)/2)**2+np.cos(np.radians(y))*np.cos(np.radians(yy))*np.sin(np.radians(x-xx)/2)**2))
 ix=np.argsort(dist)[:15]
 pd.DataFrame({'seq':ix+1,'lon':x[ix],'lat':y[ix],'distance_km':dist[ix],'uparea_km2':a[ix]/1e6,'upstream_FortPeck':np.isin(ix,u),'next':rn[ix]}).to_csv(O/(name+'_candidates.csv'),index=False)
 return ix,dist
li,ld=candidates('Landusky',-108.687744,47.631431)
mi,md=candidates('Milk_Nashua',-106.3643138889,48.1300527778)
land=int(li[0]);milk=int(mi[0]);mu=up(rn,milk+1)
path=[];j=milk
while j>=0 and len(path)<10000 and j not in path:path.append(j);j=int(rn[j])-1
inter=set(path)&set(u)
summary.update(landusky_seq=land+1,landusky_uparea_km2=float(a[land]/1e6),landusky_distance_km=float(ld[land]),milk_seq=milk+1,milk_uparea_km2=float(a[milk]/1e6),milk_path_hits_FortPeck_upstream=sorted(int(v+1) for v in inter),milk_upstream_count=len(mu),milk_canada_centers=int((y[mu]>49).sum()))
fp_path=[];j=ri
while j>=0 and j not in fp_path:fp_path.append(j);j=int(rn[j])-1
joint=[v for v in path if v in fp_path]
summary['milk_first_common_downstream']=int(joint[0]+1) if joint else None
pd.DataFrame({'seq':np.array(path)+1,'lon':x[path],'lat':y[path],'next':rn[path]}).to_csv(O/'milk_downstream_path.csv',index=False)
# Major direct and nested sections, selected by area, with unique rows.
direct=np.where(rn==ri+1)[0]
select=[ri,land,milk]+list(direct)
for xx,yy in [(-110.5,47.9),(-111.0,47.5),(-109.4,47.3)]:
 ix=np.where(np.isin(np.arange(len(x)),u))[0];select.append(int(ix[np.argmin((x[ix]-xx)**2+(y[ix]-yy)**2)]))
select=list(dict.fromkeys(select));names={ri:'FortPeck_cell_outflow',land:'Landusky',milk:'Milk_Nashua'}
series=[]
for year in [2001,2002]:
 for month in range(1,13):
  p=C/'history'/f'EXP000_spinup_USA_D_0p1_hist_unitcat_{year}-{month:02d}.nc'
  with xr.open_dataset(p) as d:
   q=d.f_discharge.isel(lat_ucat=xr.DataArray(r.seq_y.values[select]-1,dims='point'),lon_ucat=xr.DataArray(r.seq_x.values[select]-1,dims='point')).values
   ix=int(np.where(d.resv_GRAND_ID.values==307)[0][0]);qin=d.qresv_in.isel(reservoir=ix).values;qout=d.qresv_out.isel(reservoir=ix).values
   if year==2001 and month==1:
    masks=d.mask_complete_upstream.isel(lat_ucat=xr.DataArray(r.seq_y.values[u]-1,dims='point'),lon_ucat=xr.DataArray(r.seq_x.values[u]-1,dims='point')).values
    summary['mask_upstream_values_counts']={str(v):int((masks==v).sum()) for v in np.unique(masks)};summary['mask_FortPeck']=float(masks[np.where(u==ri)[0][0]])
   df=pd.DataFrame(q,index=pd.to_datetime(d.time.values).normalize(),columns=[names.get(j,f'seq_{j+1}') for j in select]);df['qresv_in']=qin;df['qresv_out']=qout;df['window_seconds']=d.history_window_seconds.values;series.append(df)
  print(year,month,flush=True)
sim=pd.concat(series);sim.index.name='date';sim.to_csv(O/'simulated_daily.csv')
summary['daily_duplicate_dates']=int(sim.index.duplicated().sum());summary['history_window_seconds']=list(np.unique(sim.window_seconds))
# Parse original NWIS discharge preserving qualifiers and missing data.
raw=Path('/share/home/dq134/wzx/openbench/Streamflow/USGS_dv/discharge/06115200.rdb.gz')
txt=gzip.open(raw,'rt').read();(O/'USGS_06115200_header.txt').write_text('\n'.join(v for v in txt.splitlines() if v.startswith('#')))
obs=pd.read_csv(io.StringIO(txt),sep='\t',comment='#',dtype=str);obs=obs.iloc[1:];cols=[v for v in obs if v.endswith('_00060_00003')];print('OBS_COLUMNS',cols,flush=True)
obs['date']=pd.to_datetime(obs.datetime);obs=obs.set_index('date');col=cols[0];o=pd.to_numeric(obs[col],errors='coerce')*.028316846592
sim['USGS_Landusky_m3s']=o.reindex(sim.index);sim.to_csv(O/'simulated_and_observed_daily.csv')
met=[]
for label,sl in [('2001',sim.loc['2001']),('2002',sim.loc['2002']),('2001-2002',sim)]:
 for n in sim.columns:
  if n in ['window_seconds','USGS_Landusky_m3s']:continue
  vals=sl[n];met.append({'period':label,'section':n,'seq':next((j+1 for j in select if names.get(j,f'seq_{j+1}')==n),None),'mean_m3s':vals.mean(),'min_m3s':vals.min(),'max_m3s':vals.max()})
 z=sl[['Landusky','USGS_Landusky_m3s']].dropna();summary['Landusky_'+label]={'n':len(z),'sim_mean':float(z.Landusky.mean()),'obs_mean':float(z.USGS_Landusky_m3s.mean()),'pbias_pct':float((z.Landusky.sum()/z.USGS_Landusky_m3s.sum()-1)*100),'correlation':float(z.corr().iloc[0,1])}
pd.DataFrame(met).to_csv(O/'flow_summary.csv',index=False)
summary['direct_upstream_seqs']=(direct+1).tolist();summary['direct_upstream_sum_mean']=float(sim[[names.get(j,f'seq_{j+1}') for j in direct]].sum(axis=1).mean());summary['qresv_in_mean']=float(sim.qresv_in.mean())
# Area is calculated from input-grid geometry; topo_area is reported only as a cross-check, never as sole watershed area.
fig,axs=plt.subplots(1,2,figsize=(14,7));
for ax in axs:
 ax.set_facecolor('#f7f7f3');ax.scatter(lon,lat,c=frac,s=5,cmap='Greens',vmin=0,vmax=1,marker='s',rasterized=True)
 ids2=u[(rn[u]>0)&(a[u]>1e9)];seg=np.stack([np.c_[x[ids2],y[ids2]],np.c_[x[rn[ids2]-1],y[rn[ids2]-1]]],axis=1);ax.add_collection(LineCollection(seg,colors='#1769aa',linewidths=.65))
 ids2=mu[(rn[mu]>0)&(a[mu]>2e9)];seg=np.stack([np.c_[x[ids2],y[ids2]],np.c_[x[rn[ids2]-1],y[rn[ids2]-1]]],axis=1);ax.add_collection(LineCollection(seg,colors='#d48725',linewidths=.7))
 for z in local_border:
  if z.is_empty:continue
  for g in getattr(z,'geoms',[z]):
   if g.geom_type=='LineString':ax.plot(*g.xy,c='black',lw=.8)
 ax.plot(x[ri],y[ri],'r*',ms=12);ax.plot(x[land],y[land],'ko',ms=4);ax.plot(x[milk],y[milk],'o',c='#d48725',ms=4)
 ax.set_xlabel('Longitude');ax.set_ylabel('Latitude');ax.grid(alpha=.2);ax.set_aspect(1/np.cos(np.radians(47)))
axs[0].set_xlim(-114,-105);axs[0].set_ylim(44,50.5);axs[1].set_xlim(-109.2,-105.8);axs[1].set_ylim(47.2,49.3)
for ax in axs:
 ax.annotate('Fort Peck', (x[ri],y[ri]),xytext=(4,-16),textcoords='offset points',color='red');ax.annotate('06115200 Landusky',(x[land],y[land]),xytext=(4,7),textcoords='offset points',fontsize=8)
axs[0].text(-113.8,49.2,'CANADA',fontsize=10);axs[0].text(-113.8,48.7,'USA',fontsize=10)
fig.suptitle('Fort Peck upstream: runoff-grid area (green), mainstem network (blue), Milk River (orange)\n'+f'{len(u)} units; mapped area {weighted.sum():,.0f} km²; Canadian cell centers {int(gcan.sum())}; border fringe upper bound 46.6 km²')
fig.tight_layout();fig.savefig(O/'fort_peck_upstream.png',dpi=220);fig.savefig(O/'fort_peck_upstream.pdf');plt.close(fig)
fig,axs=plt.subplots(2,1,figsize=(12,7),sharex=True)
axs[0].plot(sim.index,sim.Landusky,label='Model Landusky');axs[0].plot(sim.index,sim.USGS_Landusky_m3s,label='USGS 06115200');axs[0].legend();axs[1].plot(sim.index,sim.qresv_in,label='Fort Peck historical inflow');axs[1].plot(sim.index,sim[[names.get(j,f'seq_{j+1}') for j in direct]].sum(axis=1),label='Sum direct upstream discharge',alpha=.7);axs[1].legend()
for ax in axs:ax.set_ylabel('m³/s');ax.grid(alpha=.2)
fig.tight_layout();fig.savefig(O/'flow_diagnosis.png',dpi=200);plt.close(fig)
(O/'summary.json').write_text(json.dumps(summary,indent=2,default=lambda z:int(z) if isinstance(z,np.integer) else str(z)))
print(json.dumps(summary,indent=2,default=str),flush=True)
