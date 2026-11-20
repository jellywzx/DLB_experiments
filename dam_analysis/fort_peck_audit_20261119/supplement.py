from pathlib import Path
import xarray as xr,numpy as np,pandas as pd,json,hashlib,subprocess
from collections import deque
O=Path(__file__).resolve().parent;C=Path('/share/home/dq061/wzx/CoLM/cases/EXP000_spinup_USA_D_0p1');S=Path('/share/home/dq061/wzx/CoLM/CoLM202X_1030');r=xr.open_dataset(C/'riverlake/unitcatchment_regional.nc');u=pd.read_csv(O/'upstream_units.csv').regional_seq.values-1;src=r.seq_src_index.values
F=Path('/share/home/dq013/zhwei/colm/data/CoLMruntime/unitcatchment/grid_routing_data_06min.nc');f=xr.open_dataset(F)
x,y,nxt=[r[z].values for z in ['seq_lon','seq_lat','seq_next']];n=len(nxt)
count=np.bincount(nxt[nxt>0]-1,minlength=n);todo=deque(np.where(count==0)[0]);num=0
while todo:
 j=todo.popleft();num+=1;k=nxt[j]-1
 if k>=0:
  count[k]-=1
  if count[k]==0:todo.append(k)
extra={'regional_cycle_units':int(n-num),'regional_bbox':[float(x.min()),float(x.max()),float(y.min()),float(y.max())]}
for v in ['inpmat_x','inpmat_y','inpmat_area','topo_area','topo_uparea','seq_x','seq_y']:
 rv=r[v].isel(nseqmax=u).values;fv=f[v].isel(nseqmax=src[u]-1).values;extra[v+'_full_regional_equal']=bool(np.array_equal(rv,fv,equal_nan=True))
xx=r.inpmat_x.values[u];yy=r.inpmat_y.values[u];w=r.inpmat_area.values[u];valid=(xx>0)&(yy>0)&(w>0)
# Independent raw mapping area check and grid excess allocation check.
extra['input_area_sum_km2']=float(w[valid].sum()/1e6);extra['topo_area_sum_km2']=float(r.topo_area.values[u].sum()/1e6)
rows=[]
for j in u:
 k=nxt[j]-1
 if k<0:continue
 dx=np.radians(x[k]-x[j]);dy=np.radians(y[k]-y[j]);dist=2*6371*np.arcsin(np.sqrt(np.sin(dy/2)**2+np.cos(np.radians(y[j]))*np.cos(np.radians(y[k]))*np.sin(dx/2)**2))
 if dist>30 or r.topo_basin.values[j]!=r.topo_basin.values[k]:rows.append({'seq':j+1,'next':k+1,'distance_km':dist,'basin_change':bool(r.topo_basin.values[j]!=r.topo_basin.values[k])})
pd.DataFrame(rows,columns=['seq','next','distance_km','basin_change']).to_csv(O/'connection_flags.csv',index=False);extra['connection_flags_count']=len(rows)
# NWIS exact available span and target qualifiers.
raw=Path('/share/home/dq134/wzx/openbench/Streamflow/USGS_dv/discharge/06115200.rdb.gz');d=pd.read_csv(raw,compression='gzip',sep='\t',comment='#',dtype=str).iloc[1:];target=d[d.datetime.between('2001-01-01','2002-12-31')];extra['USGS_file_dates']=[d.datetime.min(),d.datetime.max()];extra['USGS_2001_2002_rows']=len(target);extra['USGS_2001_2002_qualifiers']=target['81104_00060_00003_cd'].value_counts().to_dict()
# Current artifact fingerprints cannot substitute for an archived launch-time binary hash.
files=[S/'run/colm.x',S/'.bld/MOD_Grid_RiverLakeFlow.o',C/'riverlake/unitcatchment_regional.nc',S/'main/HYDRO/MOD_Grid_RiverLakeFlow.F90',S/'main/HYDRO/MOD_Grid_RiverLakeHist.F90',S/'main/HYDRO/MOD_Grid_RiverLakeNetwork.F90',raw]
records=[]
for p in files:
 h=hashlib.sha256()
 with p.open('rb') as fp:
  for chunk in iter(lambda:fp.read(1024*1024),b''):h.update(chunk)
 records.append({'path':str(p),'sha256':h.hexdigest(),'size':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns})
(O/'fingerprints.json').write_text(json.dumps(records,indent=2));(O/'supplement.json').write_text(json.dumps(extra,indent=2));print(json.dumps(extra,indent=2))
# Capture source evidence with explicit source-only scope.
chunks=[]
for name,windows in [('MOD_Grid_RiverLakeNetwork.F90',[(220,245),(508,543),(710,723),(1090,1115)]),('MOD_Grid_RiverLakeFlow.F90',[(420,447),(550,580),(855,935),(1060,1080),(1308,1318),(1390,1410)]),('MOD_Grid_RiverLakeHist.F90',[(600,620),(665,683)])]:
 lines=(S/'main/HYDRO'/name).read_text().splitlines()
 for a,b in windows:chunks.append('\n'+name+' SOURCE ONLY\n'+'\n'.join(f'{i+1}: {lines[i]}' for i in range(a-1,min(b,len(lines)))))
(O/'source_evidence.txt').write_text('\n'.join(chunks))
