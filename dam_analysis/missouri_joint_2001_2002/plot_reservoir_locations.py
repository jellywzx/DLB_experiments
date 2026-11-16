#!/usr/bin/env python3
"""Plot six Missouri mainstem dam locations using existing audit/mask/routing data."""
import argparse
import json
import sys
from pathlib import Path

import cartopy.crs as ccrs
from cartopy.io.shapereader import Reader
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
import xarray as xr

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from openbench_dams_rivers_common import (load_standard_basin_mask, basin_boundary_arrays,
    routing_destination_indices, routing_segment_indices, routing_segments)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, default=ROOT/'runs/20261116_134143/tables/reservoir_matching_audit.csv')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        raise FileExistsError(f'Choose a new output directory: {args.out}')
    audit = pd.read_csv(args.audit)
    assert len(audit) == 6 and 2356 not in audit.grand_id.dropna().values
    points = audit[['order','reservoir','grand_id','reference_lon','reference_lat','status']].copy()
    points['coordinate_source'] = str(args.audit)
    points['location_type'] = 'ResOpsUS dam location'
    randall = points.reservoir == 'Fort Randall'
    # USGS station at the dam is a spatial proxy, never a ResOpsUS identity/data substitution.
    points.loc[randall, ['reference_lon','reference_lat']] = [-98.5534157087737,43.0649947028587]
    url = 'https://waterdata.usgs.gov/monitoring-location/06453000/statistics/'
    points.loc[randall,'coordinate_source'] = url
    points.loc[randall,'location_type'] = 'USGS-06453000 at-dam station; location proxy only'
    assert points[['reference_lon','reference_lat']].notna().all().all()
    assert points.loc[randall,'grand_id'].isna().all()
    basin = load_standard_basin_mask('missouri')
    lon, lat, mask = basin_boundary_arrays(basin)
    routing = Path('/share/home/dq061/wzx/CoLM/cases/EXP000_spinup_USA_D_0p1/riverlake/unitcatchment_regional.nc')
    with xr.open_dataset(routing) as ds:
        x,y,seq,nxt,area = [ds[k].values for k in ['seq_lon','seq_lat','seq','seq_next','topo_uparea']]
        assert ds.topo_uparea.attrs['units'] == 'm2'
    dest = routing_destination_indices(seq,nxt)
    src,_ = routing_segment_indices(x,y,dest,(-116,-89,36,51),source_mask=area>=5e9)
    segments = routing_segments(x,y,src,dest)
    args.out.mkdir(parents=True)
    points.to_csv(args.out/'reservoir_locations.csv',index=False)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'pdf.fonttype':42})
    projection = ccrs.AlbersEqualArea(central_longitude=-103,central_latitude=44,standard_parallels=(38,49))
    pc = ccrs.PlateCarree()
    fig, axes = plt.subplots(1,2,figsize=(13,7.3),subplot_kw={'projection':projection},gridspec_kw={'width_ratios':[1,1.12]})
    fig.subplots_adjust(left=.045,right=.98,bottom=.16,top=.87,wspace=.12)
    cache = Path('/share/home/dq134/.local/share/cartopy/shapefiles/natural_earth')
    bounds = cache/'cultural/ne_50m_admin_0_boundary_lines_land.shp'
    lakes = cache/'physical/ne_10m_lakes.shp'
    for ax,extent in zip(axes,[(-115,-89,36,51),(-109,-95,41.5,49.5)]):
        ax.set_extent(extent,crs=pc)
        ax.set_facecolor('#f8f7f2')
        ax.contourf(lon,lat,mask.astype(float),levels=[.5,1.5],colors=['#e2eddb'],transform=pc,zorder=1)
        ax.contour(lon,lat,mask.astype(float),levels=[.5],colors=['#78916b'],linewidths=.8,transform=pc,zorder=2)
        ax.add_collection(LineCollection(segments,colors='#6d9fba',linewidths=.45,transform=pc,zorder=3))
        if bounds.is_file(): ax.add_geometries(Reader(bounds).geometries(),pc,facecolor='none',edgecolor='#999999',linewidth=.65,zorder=2)
        if lakes.is_file(): ax.add_geometries(Reader(lakes).geometries(),pc,facecolor='#b9d8e8',edgecolor='none',zorder=2)
        gl = ax.gridlines(draw_labels=True,linewidth=.4,color='gray',alpha=.35,x_inline=False,y_inline=False,rotate_labels=False)
        gl.top_labels=False; gl.right_labels=False
        gl.xlabel_style={'size':8};gl.ylabel_style={'size':8}
        for row in points.itertuples():
            ax.scatter(row.reference_lon,row.reference_lat,s=65 if row.order==5 else 50,
                       marker='D' if row.order==5 else 'o',facecolor='white' if row.order==5 else '#b33e35',
                       edgecolor='#ca7a21' if row.order==5 else 'white',linewidth=1.4,transform=pc,zorder=6)
        ax.text(.94,.93,'N\n↑',transform=ax.transAxes,ha='center',va='center',fontsize=15)
    axes[0].set_title('(a) Missouri basin and study reach',loc='left',fontsize=12,pad=14)
    axes[1].set_title('(b) Six mainstem reservoirs',loc='left',fontsize=12,pad=14)
    axes[0].add_patch(Rectangle((-109,41.5),14,8,fill=False,edgecolor='#444444',linestyle='--',linewidth=.9,transform=pc,zorder=5))
    for row in points.itertuples():
        axes[0].annotate(str(row.order),(row.reference_lon,row.reference_lat),xycoords=pc._as_mpl_transform(axes[0]),
                         xytext=(5,5),textcoords='offset points',fontsize=8,weight='bold',zorder=7)
    offsets=[(12,18),(-15,22),(-18,13),(15,12),(-18,-12),(0,18)]
    for row,(dx,dy) in zip(points.itertuples(),offsets):
        identity = 'ResOpsUS ID unverified*' if row.order==5 else f'GRanD ID {int(row.grand_id)}'
        axes[1].annotate(f'{row.order}  {row.reservoir}\n{identity}',(row.reference_lon,row.reference_lat),
                         xycoords=pc._as_mpl_transform(axes[1]),xytext=(dx,dy),textcoords='offset points',
                         ha='left' if dx>0 else 'right',va='bottom' if dy>0 else 'top',fontsize=9,
                         bbox={'facecolor':'white','edgecolor':'none','alpha':.85,'pad':2},
                         arrowprops={'arrowstyle':'-','color':'#555555','lw':.65},zorder=8)
    fig.suptitle('Missouri River | Six mainstem reservoir locations',fontsize=18,weight='bold',y=.97)
    fig.text(.5,.915,'Numbered from upstream to downstream',ha='center',fontsize=11,color='#555555')
    handles=[Line2D([],[],marker='o',ls='',color='#b33e35',label='Matched ResOpsUS dam'),
             Line2D([],[],marker='D',ls='',markerfacecolor='white',color='#ca7a21',label='Fort Randall: location only'),
             Line2D([],[],color='#6d9fba',lw=1,label='CoLM routing (upstream area ≥ 5,000 km²)'),
             Line2D([],[],color='#78916b',lw=2,label='Missouri basin mask')]
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.065),ncol=2,frameon=False,fontsize=9)
    fig.text(.5,.025,'* Fort Randall uses the USGS-06453000 at-dam location; no ResOpsUS ID assigned. Canadian ID 2356 excluded.',
             ha='center',fontsize=8,color='#555555')
    for suffix in ['png','pdf']: fig.savefig(args.out/f'missouri_six_reservoir_locations.{suffix}',dpi=300,bbox_inches='tight')
    plt.close(fig)
    (args.out/'sources.json').write_text(json.dumps({'audit':str(args.audit),'routing':str(routing),
        'basin_mask':str(basin.path),'fort_randall_location_source':url,'source_checked':'2026-11-16',
        'fort_randall_note':'Location proxy only; NO_REFERENCE remains unchanged; 2356 excluded.',
        'routing_segment_count':len(segments)},indent=2)+'\n')
    print(args.out)


if __name__ == '__main__': main()
