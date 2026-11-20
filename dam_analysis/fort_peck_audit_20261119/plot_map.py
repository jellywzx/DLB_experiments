from pathlib import Path
import numpy as np,pandas as pd,json,xarray as xr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from shapely.geometry import box
from cartopy.io.shapereader import Reader
O=Path(__file__).resolve().parent
r=xr.open_dataset('/share/home/dq061/wzx/CoLM/cases/EXP000_spinup_USA_D_0p1/riverlake/unitcatchment_regional.nc')
s=json.loads((O/'summary.json').read_text());g=pd.read_csv(O/'input_grid_area.csv');u=pd.read_csv(O/'upstream_units.csv').regional_seq.values-1
x,y,rn,a=[r[v].values for v in ['seq_lon','seq_lat','seq_next','topo_uparea']]
ri=s['regional_dam_seq']-1;land=s['landusky_seq']-1;milk=s['milk_seq']-1
lon=g.lon.values;lat=g.lat.values;frac=g.fraction_to_FortPeck.values;weighted=g.assigned_area_km2.values;gcan=g.canada_center.values
# Reconstruct Milk's upstream via inverse seq_next.
rev={}
for j,k in enumerate(rn):
 if k>0:rev.setdefault(int(k)-1,[]).append(j)
seen={milk};stack=[milk]
while stack:
 j=stack.pop()
 for k in rev.get(j,[]):
  if k not in seen:seen.add(k);stack.append(k)
mu=np.array(sorted(seen))
recs=Reader('/share/home/dq134/.local/share/cartopy/shapefiles/natural_earth/cultural/ne_10m_admin_0_boundary_lines_land.shp').records()
local_border=[v.geometry.intersection(box(-116,48.8,-104,49.2)) for v in recs if v.attributes.get('ADM0_LEFT')=='Canada' and v.attributes.get('ADM0_RIGHT')=='United States of America' and v.attributes.get('TYPE')=='Land']
fig,axs=plt.subplots(1,2,figsize=(14,7));
for ax in axs:
 ax.set_facecolor('#f7f7f3');ax.scatter(lon,lat,c=frac,s=5,cmap='Greens',vmin=0,vmax=1,marker='s',rasterized=True)
 allids=u[rn[u]>0];allseg=np.stack([np.c_[x[allids],y[allids]],np.c_[x[rn[allids]-1],y[rn[allids]-1]]],axis=1);ax.add_collection(LineCollection(allseg,colors='#6d9bb8',linewidths=.28,alpha=.65))
 ids2=u[(rn[u]>0)&(a[u]>1e9)];seg=np.stack([np.c_[x[ids2],y[ids2]],np.c_[x[rn[ids2]-1],y[rn[ids2]-1]]],axis=1);ax.add_collection(LineCollection(seg,colors='#1769aa',linewidths=.65))
 ids2=mu[(rn[mu]>0)&(a[mu]>2e9)];seg=np.stack([np.c_[x[ids2],y[ids2]],np.c_[x[rn[ids2]-1],y[rn[ids2]-1]]],axis=1);ax.add_collection(LineCollection(seg,colors='#d48725',linewidths=.7))
 for z in local_border:
  if z.is_empty:continue
  for g in getattr(z,'geoms',[z]):
   if g.geom_type=='LineString':ax.plot(*g.xy,c='black',lw=.8)
 ax.plot(x[ri],y[ri],'r*',ms=12);ax.plot(x[land],y[land],'ko',ms=4);ax.plot(x[milk],y[milk],'o',c='#d48725',ms=4); ax.plot(-106.35,48.05,'kx',ms=5)
 ax.set_xlabel('Longitude');ax.set_ylabel('Latitude');ax.grid(alpha=.2);ax.set_aspect(1/np.cos(np.radians(47)))
axs[0].set_xlim(-114,-105);axs[0].set_ylim(44,50.5);axs[1].set_xlim(-109.2,-105.8);axs[1].set_ylim(47.2,49.3)
for ax in axs:
 ax.annotate('Fort Peck', (x[ri],y[ri]),xytext=(4,-16),textcoords='offset points',color='red');ax.annotate('06115200 Landusky',(x[land],y[land]),xytext=(4,7),textcoords='offset points',fontsize=8)
axs[0].text(-113.8,49.2,'CANADA',fontsize=10);axs[0].text(-113.8,48.7,'USA',fontsize=10)
axs[1].annotate('Downstream confluence: 164248',(-106.35,48.05),xytext=(-130,45),textcoords='offset points',fontsize=8,arrowprops={'arrowstyle':'->'})
fig.suptitle('Fort Peck upstream: runoff-grid area (green), upstream network (blue), Milk River (orange)\n'+f'{len(u)} units; mapped area {weighted.sum():,.0f} km²; Canadian cell centers {int(gcan.sum())}; border fringe upper bound 46.6 km²')
fig.tight_layout();fig.savefig(O/'fort_peck_upstream.png',dpi=220);fig.savefig(O/'fort_peck_upstream.pdf');plt.close(fig)
