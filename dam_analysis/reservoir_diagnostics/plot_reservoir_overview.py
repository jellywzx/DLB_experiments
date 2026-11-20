#!/usr/bin/env python3
"""One-page reservoir location/ID map plus complete name and paired-data index."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse,json
import numpy as np
import pandas as pd
import xarray as xr
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import cartopy.crs as ccrs
from cartopy.io.shapereader import Reader
from shapely.geometry import box
from io_openbench import sha

MAINSTEM={307,753,870,884,895}
COLORS={'Both':'#217b68','Outflow only':'#2879b7','Inflow only':'#d88826','No paired data':'#969696'}


def arrange_labels(ax, coordinates, labels):
    """Resolve screen-space label collisions; leader lines retain actual point positions."""
    ax.figure.canvas.draw();renderer=ax.figure.canvas.get_renderer()
    anchor=ax.transData.transform(coordinates)
    centers=anchor+np.array([12.,9.]);sizes=[]
    texts=[]
    for xy,label in zip(coordinates,labels):
        t=ax.text(*xy,label,ha='center',va='center',fontsize=8.2,zorder=8,
                  bbox={'facecolor':'white','edgecolor':'none','alpha':.85,'pad':.12})
        texts.append(t)
    ax.figure.canvas.draw()
    for t in texts:
        b=t.get_window_extent(renderer);sizes.append([b.width+5,b.height+4])
    half=np.array(sizes)/2;bounds=ax.get_window_extent(renderer)
    # Deterministic pairwise minimum-overlap relaxation, with a weak anchor pull.
    for iteration in range(1800):
        shift=np.clip((anchor+np.array([12.,9.])-centers)*.008,-.5,.5)
        collisions=0
        for i in range(len(centers)):
            for j in range(i):
                delta=centers[i]-centers[j];overlap=half[i]+half[j]-abs(delta)
                if (overlap>0).all():
                    k=int(np.argmin(overlap));sign=1 if delta[k]>=0 else -1
                    amount=min(overlap[k]*.55+1,7);shift[i,k]+=sign*amount;shift[j,k]-=sign*amount;collisions+=1
            # Keep labels away from point symbols, including their own anchor.
            delta=centers[i]-anchor;overlap=half[i]+4-abs(delta)
            for j in np.where((overlap>0).all(axis=1))[0]:
                k=int(np.argmin(overlap[j]));sign=1 if delta[j,k]>=0 else -1
                shift[i,k]+=sign*min(overlap[j,k]*.5+1,5)
        centers+=np.clip(shift,-10,10)
        centers[:,0]=np.clip(centers[:,0],bounds.x0+half[:,0]+4,bounds.x1-half[:,0]-4)
        centers[:,1]=np.clip(centers[:,1],bounds.y0+half[:,1]+4,bounds.y1-half[:,1]-4)
        if collisions==0 and iteration>120:break
    remaining=0
    for i in range(len(centers)):
        for j in range(i):
            if ((half[i]+half[j]-abs(centers[i]-centers[j]))>0).all():remaining+=1
    for i,t in enumerate(texts):
        target=ax.transData.inverted().transform(centers[i]);t.set_position(target)
        ax.plot([coordinates[i,0],target[0]],[coordinates[i,1],target[1]],color='#606c70',lw=.45,alpha=.8,zorder=6)
    return remaining


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--run-dir',type=Path,required=True);ap.add_argument('--out-dir',type=Path)
    args=ap.parse_args();run=args.run_dir.resolve();provenance=json.loads((run/'provenance.json').read_text())
    summary=pd.read_csv(run/'tables/reservoir_summary.csv',dtype={'ID':int})
    selection=pd.read_csv(run/'tables/selection_audit.csv',dtype={'ID':int})
    locations=selection[['ID','LON_org','LAT_org']].drop_duplicates('ID')
    frame=summary.merge(locations,on='ID',how='left',validate='one_to_one').sort_values('ID').reset_index(drop=True)
    assert len(frame)==len(summary) and frame[['LON_org','LAT_org']].notna().all().all()
    frame['status']=np.select([(frame.inflow_n>0)&(frame.outflow_n>0),frame.outflow_n>0,frame.inflow_n>0],['Both','Outflow only','Inflow only'],default='No paired data')
    frame['mainstem_verified']=frame.ID.isin(MAINSTEM)
    frame['coordinate_source']='original reference lon/lat; not CaMa allocated point'
    out=args.out_dir or Path(__file__).resolve().parent/'runs'/('reservoir_overview_'+datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S'))
    out.mkdir(parents=True,exist_ok=False);frame.to_csv(out/'reservoir_location_index.csv',index=False)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'pdf.fonttype':42})
    fig=plt.figure(figsize=(24,16),dpi=110)
    pc=ccrs.PlateCarree();projection=ccrs.AlbersEqualArea(central_longitude=-103,central_latitude=44,standard_parallels=(36,49))
    ax=fig.add_axes([.025,.155,.585,.72],projection=projection)
    project=provenance['project'];extent=[*project['lon_range'],*project['lat_range']];ax.set_extent(extent,crs=pc);ax.set_facecolor('#f7f8f5')
    cache=Path('/share/home/dq134/.local/share/cartopy/shapefiles/natural_earth')
    basemap=[]
    maskpath=Path(project['basin_mask'])
    with xr.open_dataset(maskpath) as ds:
        lat_slice=slice(extent[3],extent[2]) if ds.lat[0]>ds.lat[-1] else slice(extent[2],extent[3])
        mask=ds.basin_mask.sel(lon=slice(extent[0],extent[1]),lat=lat_slice)
        ax.contourf(mask.lon,mask.lat,mask.values,levels=[.5,1.5],colors=['#e5efdc'],transform=pc,zorder=1)
        ax.contour(mask.lon,mask.lat,mask.values,levels=[.5],colors=['#779563'],linewidths=.8,transform=pc,zorder=2)
    clip=box(*[extent[0],extent[2],extent[1],extent[3]])
    for path,color,width in [(cache/'cultural/ne_10m_admin_0_boundary_lines_land.shp','#969b9d',.7),
                             (cache/'physical/ne_10m_rivers_lake_centerlines.shp','#9db9c6',.48)]:
        if path.is_file():
            geometries=[g for g in Reader(path).geometries() if g.intersects(clip)]
            ax.add_geometries(geometries,pc,facecolor='none',edgecolor=color,linewidth=width,zorder=3);basemap.append(str(path))
    gl=ax.gridlines(draw_labels=True,x_inline=False,y_inline=False,linewidth=.35,color='#aaaaaa',alpha=.5,rotate_labels=False)
    gl.top_labels=False;gl.right_labels=False;gl.xlabel_style={'size':9};gl.ylabel_style={'size':9}
    for status,color in COLORS.items():
        part=frame[frame.status==status];ax.scatter(part.LON_org,part.LAT_org,s=36,c=color,edgecolor='white',lw=.65,transform=pc,zorder=5)
    stars=frame[frame.mainstem_verified]
    ax.scatter(stars.LON_org,stars.LAT_org,s=155,marker='*',c=[COLORS[v] for v in stars.status],edgecolor='#a3302f',lw=1.3,transform=pc,zorder=7)
    xy=projection.transform_points(pc,frame.LON_org.values,frame.LAT_org.values)[:,:2]
    remaining=arrange_labels(ax,xy,frame.ID.astype(str).tolist())
    ax.text(.025,.97,'CANADA',transform=ax.transAxes,color='#858585',fontsize=10,va='top')
    # Full lookup index is part of this single figure, rather than a separate report.
    for col,indices in enumerate(np.array_split(np.arange(len(frame)),2)):
        tab=fig.add_axes([.65+col*.175,.16,.17,.72]);tab.set_axis_off()
        tab.text(0,1.03,'GRanD ID   Reservoir name',weight='bold',fontsize=11,transform=tab.transAxes)
        for k,index in enumerate(indices):
            row=frame.iloc[index];yy=1-(k+.5)/len(indices)
            tab.scatter(.01,yy,s=24,color=COLORS[row.status],transform=tab.transAxes,clip_on=False)
            tab.text(.035,yy,f'{row.ID:>4}  {row.DamName}'+(' *' if row.mainstem_verified else ''),fontsize=9,va='center',transform=tab.transAxes,color='#8f2828' if row.mainstem_verified else '#252b2d')
    first,last=project['years'];fig.text(.04,.95,f'Missouri Basin | All {len(frame)} selected reservoirs',fontsize=25,weight='bold')
    fig.text(.04,.919,f'{first}–{last} daily diagnosis  •  GRanD IDs shown at original reference locations',fontsize=14,color='#4d5659')
    fig.text(.65,.935,'ID / name lookup',fontsize=17,weight='bold')
    handles=[Line2D([0],[0],marker='o',ls='',markerfacecolor=color,markeredgecolor='white',ms=8,label=f'{status} ({int((frame.status==status).sum())})') for status,color in COLORS.items() if (frame.status==status).any()]
    handles.append(Line2D([0],[0],marker='*',ls='',markerfacecolor='white',markeredgecolor='#a3302f',ms=13,label='Five verified mainstem reservoirs'))
    fig.legend(handles=handles,loc='lower left',bbox_to_anchor=(.035,.065),ncol=3,frameon=False,fontsize=11)
    fig.text(.04,.045,'Green outline/shading: Missouri basin mask  |  Leader lines connect labels to true point locations  |  * = mainstem ID in lookup',fontsize=10,color='#535e61')
    fig.text(.04,.025,'Availability means finite observation–simulation pairs within the analysis period; no-pair reservoirs remain shown. No observed storage is included.',fontsize=10,color='#535e61')
    name='missouri_all_reservoirs_overview'
    fig.savefig(out/(name+'.png'),dpi=240,bbox_inches='tight');fig.savefig(out/(name+'.pdf'),bbox_inches='tight');plt.close(fig)
    from PIL import Image
    with Image.open(out/(name+'.png')) as im:im.verify()
    record=dict(run_dir=str(run),reservoir_count=len(frame),unique_ids=int(frame.ID.nunique()),labels=len(frame),label_collision_pairs=remaining,
                verified_mainstem_ids=stars.ID.tolist(),counts=frame.status.value_counts().to_dict(),basemap_files=basemap,
                input_sha256={str(p):sha(p) for p in [run/'tables/reservoir_summary.csv',run/'tables/selection_audit.csv',run/'provenance.json',maskpath]},
                plotting_script_sha256=sha(Path(__file__)),png_decode_pass=True)
    (out/'map_sources_and_validation.json').write_text(json.dumps(record,indent=2))
    print('OUTPUT='+str(out));print(json.dumps(record,indent=2))

if __name__=='__main__':main()
