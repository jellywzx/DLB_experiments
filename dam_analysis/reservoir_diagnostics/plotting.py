"""Publication/export figures; missing data remain visible as gaps."""
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import TwoSlopeNorm
from diagnostics import metrics

plt.rcParams.update({'font.family':'DejaVu Sans','font.size':9,'pdf.fonttype':42})
COLORS={'obs':'#303030','sim':'#1976a3'}
VARIABLES=['inflow','outflow','storage']


def save(fig, path):
    path=Path(path)
    fig.savefig(path.with_suffix('.png'),dpi=220,bbox_inches='tight')
    fig.savefig(path.with_suffix('.pdf'),bbox_inches='tight')
    plt.close(fig)


def station_plots(frame, name, identifier, capacity, seasonal, out):
    title=f'{name} | GRanD {identifier} | {frame.index[0]:%Y-%m-%d} to {frame.index[-1]:%Y-%m-%d}'
    fig,axes=plt.subplots(3,1,figsize=(12,8),sharex=True)
    for ax,v in zip(axes,VARIABLES):
        for side,label in [('obs','Observed'),('sim','Simulated')]:
            ax.plot(frame.index,frame[f'{side}_{v}'],lw=.85,color=COLORS[side],label=label)
        m=metrics(frame[f'obs_{v}'],frame[f'sim_{v}'])
        text=f'N={m["n"]}  KGE={m["KGE"]:.2f}  PBIAS={m["percent_bias"]:.1f}%'
        if not m['n']:
            text='NO VALID OBSERVATION / SIMULATION PAIRS'
        if v=='storage':
            text+='\nExploratory: stock time support unverified' if frame.obs_storage.notna().any() else '\nNO REFERENCE STORAGE; model curve only'
            if np.isfinite(capacity):
                ax.axhline(capacity,color='#999999',ls=':',lw=.8,label='Parameter capacity')
        ax.text(.01,.97,text,transform=ax.transAxes,va='top',fontsize=8,bbox={'facecolor':'white','alpha':.85,'edgecolor':'none'})
        ax.set_ylabel(v.title()+'\n'+('MCM' if v=='storage' else 'm³/s'));ax.grid(alpha=.2)
        ax.set_xlim(frame.index[0],frame.index[-1])
    axes[0].legend(loc='upper right',ncol=2);axes[0].set_title(title,loc='left')
    axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=max(1, int(np.ceil(len(frame)/30.44/9)))))
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    fig.tight_layout();save(fig,out/f'{identifier}_timeseries')
    fig,axes=plt.subplots(2,3,figsize=(13,7))
    for col,v in enumerate(VARIABLES):
        z=seasonal[seasonal.variable==v].sort_values('month')
        for side,label in [('obs','Observed'),('sim','Simulated')]:
            axes[0,col].plot(z.month,z[side+'_mean'],'o-',ms=3,color=COLORS[side],label=label)
        axes[0,col].set_xticks([1,3,5,7,9,12]);axes[0,col].set_xlabel('Month');axes[0,col].set_title(v.title())
        axes[0,col].set_ylabel('MCM' if v=='storage' else 'm³/s')
        pair=frame[[f'obs_{v}',f'sim_{v}']].dropna()
        ax=axes[1,col]
        if len(pair):
            ax.scatter(pair.iloc[:,0],pair.iloc[:,1],s=5,alpha=.25,color=COLORS['sim'],rasterized=True)
            lo=float(pair.min().min());hi=float(pair.max().max())
            if lo==hi:lo-=1;hi+=1
            ax.plot([lo,hi],[lo,hi],'k--',lw=.8);ax.set_xlim(lo,hi);ax.set_ylim(lo,hi)
        else:ax.text(.5,.5,'No valid pairs',ha='center',transform=ax.transAxes)
        ax.set_xlabel('Observed');ax.set_ylabel('Simulated')
        m=metrics(frame[f'obs_{v}'],frame[f'sim_{v}'])
        ax.text(.03,.97,f'N={m["n"]}\nr={m["correlation"]:.2f}\nRMSE={m["RMSE"]:.2f}',transform=ax.transAxes,va='top',fontsize=8)
    axes[0,0].legend(fontsize=8);fig.suptitle(title+'\nMonthly cycles use paired dates; storage comparison is exploratory')
    for ax in axes.flat:ax.grid(alpha=.18)
    fig.tight_layout();save(fig,out/f'{identifier}_seasonal_scatter')


def process_plot(frame, proxy, name, identifier, out):
    if not np.isfinite(frame).all(axis=1).any():
        return False
    fig,axes=plt.subplots(2,1,figsize=(12,6),sharex=True)
    for side,label in [('obs','Observed'),('sim','Simulated')]:
        axes[0].plot(proxy.index,proxy[f'{side}_in_minus_out_m3s'],label=label,color=COLORS[side],lw=.8)
        axes[1].plot(proxy.index,proxy[f'{side}_provisional_residual_mcm'],label=label,color=COLORS[side],lw=.8)
    axes[0].set_ylabel('I - O (m³/s)');axes[1].set_ylabel('Proxy residual (MCM/day)')
    axes[0].set_title(f'{name} | GRanD {identifier}\nExploratory storage-change proxy; NOT a full water-balance test',loc='left')
    for ax in axes:ax.legend(ncol=2);ax.grid(alpha=.2);ax.axhline(0,c='gray',lw=.5)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'));fig.tight_layout();save(fig,out/f'{identifier}_process_proxy')
    return True


def event_plot(frame, event, name, out):
    lo,hi=pd.Timestamp(event['start']),pd.Timestamp(event['end']);z=frame.loc[lo:hi]
    fig,axes=plt.subplots(2,1,figsize=(9,5),sharex=True)
    for ax,side in zip(axes,['obs','sim']):
        ax.plot(z.index,z[f'{side}_inflow'],label='Inflow');ax.plot(z.index,z[f'{side}_outflow'],label='Outflow')
        ax.legend();ax.set_ylabel(f'{side} (m³/s)');ax.grid(alpha=.2)
    axes[0].set_title(f'{name} | GRanD {event["ID"]} | Candidate {event["event"]}\nDaily means; flood identity unverified',loc='left')
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'));fig.tight_layout()
    save(fig,out/f'{event["ID"]}_event_{event["event"]}')


def summary_plots(table, stations, audits, out):
    order=stations.ID.tolist();names={r.ID:f'{r.ID} {r.DamName}' for r in stations.itertuples()}
    height=max(5,len(order)*.26+1.5)
    fig,axes=plt.subplots(1,4,figsize=(14,height),sharey=True)
    settings=[('KGE','KGE (color clipped)',-1,1,'RdYlGn'),('correlation','Correlation',-1,1,'RdYlGn'),('rSD','SD ratio (color clipped)',0,3,'viridis'),('percent_bias','PBIAS % (color clipped)',-100,100,'RdBu_r')]
    for ax,(metric,label,lo,hi,cmap) in zip(axes,settings):
        matrix=table.pivot(index='ID',columns='variable',values=metric).reindex(index=order,columns=VARIABLES)
        norm=TwoSlopeNorm(vmin=lo,vcenter=0,vmax=hi) if lo<0 else None
        kw={'norm':norm} if norm else {'vmin':lo,'vmax':hi}
        cm=plt.get_cmap(cmap).copy();cm.set_bad('#e4e4e4')
        im=ax.imshow(matrix.values,aspect='auto',cmap=cm,**kw)
        ax.set_xticks(range(3),['In','Out','Storage'],rotation=30);ax.set_title(label,fontsize=9)
        ax.set_yticks(range(len(order)),[names[i] for i in order]);fig.colorbar(im,ax=ax,shrink=.55)
    fig.suptitle('Paired-date metrics; gray = undefined / no pairs\nStorage statistics are exploratory; full values retained in CSV')
    fig.tight_layout();save(fig,out/'all_metric_heatmap')
    fig,ax=plt.subplots(figsize=(8,6));pivot=table.pivot(index='ID',columns='variable',values='percent_bias')
    if {'inflow','outflow'}<=set(pivot.columns):
        good=pivot[['inflow','outflow']].dropna()
        ax.scatter(good.inflow,good.outflow,c='#1976a3')
        for ident,z in good.iterrows():ax.annotate(ident,(z.inflow,z.outflow),fontsize=7,xytext=(3,3),textcoords='offset points')
        if len(good):
            lo=float(good.min().min());hi=float(good.max().max());ax.plot([lo,hi],[lo,hi],'k:',lw=.8)
    ax.axhline(0,c='gray',lw=.7);ax.axvline(0,c='gray',lw=.7);ax.grid(alpha=.2)
    ax.set_xlabel('Inflow PBIAS (%)');ax.set_ylabel('Outflow PBIAS (%)');ax.set_title('Inflow vs outflow bias | labels = GRanD ID')
    fig.tight_layout();save(fig,out/'all_inflow_outflow_bias')
    fig,ax=plt.subplots(figsize=(8,height));matrix=audits.pivot(index='ID',columns='variable',values='coverage').reindex(index=order,columns=VARIABLES)
    im=ax.imshow(matrix.values,aspect='auto',vmin=0,vmax=1,cmap='YlGnBu')
    ax.set_xticks(range(3),VARIABLES);ax.set_yticks(range(len(order)),[names[i] for i in order]);fig.colorbar(im,ax=ax,label='Paired days / requested days')
    ax.set_title('Paired data coverage');fig.tight_layout();save(fig,out/'all_coverage')


def location_plot(stations, table, context, out):
    lon='LON_org' if 'LON_org' in stations else 'ref_lon';lat='LAT_org' if 'LAT_org' in stations else 'ref_lat'
    coords=stations[['ID',lon,lat]].copy();coords[lon]=pd.to_numeric(coords[lon],errors='coerce');coords[lat]=pd.to_numeric(coords[lat],errors='coerce')
    if not coords[[lon,lat]].notna().all(axis=1).any():return False
    project=context['config']['project']
    xlim=project.get('lon_range') or [float(coords[lon].min())-2,float(coords[lon].max())+2]
    ylim=project.get('lat_range') or [float(coords[lat].min())-2,float(coords[lat].max())+2]
    fig,axes=plt.subplots(1,3,figsize=(14,6),sharex=True,sharey=True)
    maskpath=context['config']['project'].get('basin_mask')
    # Optional local basin outline, no network/download requirement.
    mask=None
    if maskpath and Path(maskpath).is_file():
        import xarray as xr
        with xr.open_dataset(maskpath) as ds:
            for var in ds.data_vars:
                da=ds[var]
                if set(da.dims)=={'lat','lon'}:
                    xs=slice(*xlim) if ds.lon.values[0]<ds.lon.values[-1] else slice(*xlim[::-1])
                    ys=slice(*ylim) if ds.lat.values[0]<ds.lat.values[-1] else slice(*ylim[::-1])
                    da=da.sel(lon=xs,lat=ys)
                    mask=(da.lon.values,da.lat.values,np.asarray(da.transpose('lat','lon').values));break
    for ax,v in zip(axes,VARIABLES):
        z=coords.merge(table[table.variable==v][['ID','percent_bias']],on='ID',how='left')
        if mask is not None:
            xx,yy,values=mask;finite=values[np.isfinite(values)]
            if len(finite) and np.ptp(finite)>0:ax.contour(xx,yy,np.where(np.isfinite(values),values,0),levels=[.5],colors='#9ba895',linewidths=.5)
        ax.scatter(z[lon],z[lat],c='#bbbbbb',s=32)
        finite=z.percent_bias.notna();im=ax.scatter(z.loc[finite,lon],z.loc[finite,lat],c=z.loc[finite,'percent_bias'],s=36,cmap='RdBu_r',vmin=-100,vmax=100,edgecolors='white',linewidths=.4)
        for row in z.itertuples(index=False,name=None):ax.annotate(str(row[0]),(row[1],row[2]),fontsize=6,xytext=(3,2),textcoords='offset points')
        ax.set_xlim(*xlim);ax.set_ylim(*ylim)
        ax.set_title(v.title());ax.set_xlabel('Longitude');ax.grid(alpha=.2);ax.set_aspect(1/np.cos(np.radians(z[lat].mean())))
    axes[0].set_ylabel('Latitude');fig.colorbar(im,ax=axes.tolist(),label='PBIAS % (color clipped to ±100)',shrink=.6)
    fig.suptitle('Reservoir locations from original reference coordinates | storage exploratory')
    save(fig,out/'all_reservoir_locations');return True
