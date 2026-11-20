#!/usr/bin/env python3
"""Analyze already evaluated daily reservoirs from any compatible OpenBench run."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import html
import platform
import sys
import numpy as np
import pandas as pd
from io_openbench import discover, load_station, read_parameters, sha, ITEMS
from diagnostics import metrics, seasonal, joint, balance_proxies, events
from full_reference import DEFAULT_REFERENCE, prepare_full

HERE=Path(__file__).resolve().parent


def dump_json(path,value):
    def convert(obj):
        if isinstance(obj,Path):return str(obj)
        if isinstance(obj,np.integer):return int(obj)
        if isinstance(obj,np.floating):return float(obj)
        if isinstance(obj,np.bool_):return bool(obj)
        raise TypeError(type(obj).__name__)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=convert),encoding='utf-8')


def reconcile(context, table):
    rows=[]
    for row in table.to_dict('records'):
        v=row['variable'];source=context['metrics'][v];source=source[source.ID==row['ID']]
        for key in ['KGE','correlation','rSD','percent_bias']:
            expected=float(pd.to_numeric(source.iloc[0].get(key,np.nan),errors='coerce')) if len(source) else np.nan
            actual=row[key]
            if not len(source):status='NO_OPENBENCH_ROW'
            elif not np.isfinite(expected) and not np.isfinite(actual):status='BOTH_UNDEFINED'
            elif np.isclose(expected,actual,rtol=1e-4,atol=1e-5,equal_nan=True):status='MATCH'
            else:status='SOURCE_DIFFERENCE' if context.get('mode')=='full' else 'MISMATCH'
            rows.append(dict(ID=row['ID'],variable=v,metric=key,openbench_value=expected,recomputed_value=actual,
                             difference=actual-expected,status=status,rtol=1e-4,atol=1e-5))
    return pd.DataFrame(rows)


def write_report(out,context,station_summary,table,checks,event_table):
    mismatches=int((checks.status=='MISMATCH').sum())
    paired=table[table.n>0]
    lines=[f'水库联合诊断：{context["config"]["project"]["name"]}',
           f'评估时段：{context["start"]}-01-01—{context["end"]}-12-31；日尺度。',
           f'模拟：{context["simulation"]}；参考：{context["reference"]}。',
           f'最终匹配清单中本次纳入{len(station_summary)}座；至少一个变量有有效配对的{int(station_summary.has_pairs.sum())}座。',
           f'指标交叉检查：{int((checks.status=="MATCH").sum())}项一致，{int((checks.status=="BOTH_UNDEFINED").sum())}项共同未定义，{mismatches}项差异。',
           '以评估目录中已处理的配对NetCDF为依据；不重新提取或重跑CoLM/OpenBench；不重复时间偏移或单位转换。',
           '配对日期逐变量确定；联合诊断另用六条序列共同有效日期。缺测保持NaN，不补零/插值。',
           '库容时间支持未核实：指标为评估标签上的重算/探索性比较，不作严格库存验证。图中容量是输入参数容量，不保证等于运行时有效容量。',
           'I−O与日均库容差只是代理：缺少日界库存和完整源汇账本，不能据此宣称完整水量守恒。',
           'A/B/C/D分类沿用参考脚本的探索性阈值；正式类别均为UNDETERMINED。无拓扑核实的水库不做上下游误差传播归因。',
           '指标重算采用共同有效样本、KGE2009、总体标准差ddof=0。与保存的OpenBench表以rtol=1e-4、atol=1e-5比较，原始差值同时保存。',
           '本程序不识别生产模拟的精确源码版本；输入manifest中的版本只作来源记录。']
    lines+=['','变量汇总（每库先算指标；分位数不加权、不代表流域总水量）：']
    for v in context['variables']:
        z=paired[paired.variable==v]
        lines.append(f'{v}: {len(z)}座有配对；PBIAS中位数={z.percent_bias.median():.2f}%；KGE中位数={z.KGE.median():.3f}。')
    lines+=['','逐库结果：']
    for row in station_summary.to_dict('records'):
        ident=row['ID'];z=table[table.ID==ident]
        lines.append(f'{ident} {row["DamName"]} | 共同有效日数={row["n_joint"]} | 探索性类别={row["provisional_class"]}')
        for m in z.to_dict('records'):
            lines.append(f'  {m["variable"]}: N={m["n"]}, PBIAS={m["percent_bias"]:.2f}%, KGE={m["KGE"]:.3f}, RMSE={m["RMSE"]:.3f} {m["units"]}; {m["status"]}')
    lines+=['','数据缺失与排除说明（完整字段见tables/matching_audit.csv）：']
    audit=pd.read_csv(out/'tables/matching_audit.csv',dtype={'ID':str}).fillna('')
    for r in audit[audit.n_pair==0].to_dict('records'):
        lines.append(f'{r["ID"]} {r["variable"]}: ref={r["ref_skip_reason"] or r["ref_files"] or "missing"}; sim={r["sim_skip_reason"] or "available; inspect finite count"}')
    if len(event_table):
        lines+=['','高流量事件是观测入流锚定的候选窗口，不是已确认同一洪水；全部候选状态见tables/events.csv。',str(event_table.status.value_counts().to_dict())]
    lines+=['','图件：figures/*_timeseries、*_seasonal_scatter、*_process_proxy、all_*；可选事件图见figures/events。',
            '完整复核：tables/metrics.csv、metric_crosschecks.csv、seasonal.csv、joint_diagnostics.csv、data_quality.csv、selection_audit.csv；data为逐库配对序列及代理。',
            'provenance.json记录输入/程序SHA256、版本和参数；validation.json为独立产物检查结果。']
    if context.get('mode')=='full':
        lines[3]=f'从新参考文件与模型ID、区域和流域条件重建集合：{len(station_summary)}座，至少一个变量有配对的{int(station_summary.has_pairs.sum())}座。'
        lines[4]=f'新旧来源对照：{int((checks.status=="MATCH").sum())}项一致，{int((checks.status=="SOURCE_DIFFERENCE").sum())}项差异；来源变化不作为计算失败。'
        lines[5]=f'观测来自 {context["reference_file"]}；模拟按真实GRanD ID从原始月历史提取。旧29座清单不参与筛选。'
        lines[7]='新参考无库容变量：只展示可用模拟库容，不混入旧参考；库容评价、三变量分类及库存守恒代理不可计算。'
        lines[9]='本模式正式及探索性三变量类别均不可判定；只有入出流配对与候选事件分析。'
        lines[10]='新参考科学变量已应用源QC；保留负入流，不用raw_mean替代。QC统计是手工QC前诊断，不能直接视作最终排除日数。'
        lines.extend(['', '参考时间支持：'+str(context['reference_time_support']), '来源日历标签按原样配对；noon只是索引，未验证物理日界。', '负/近零观测分母使PBIAS及KGE均值比难以解释；详见metrics.csv的pbias_applicability及抵消比。'])
    (out/'report.txt').write_text('\n'.join(lines),encoding='utf-8')
    esc=html.escape
    doc=['<!doctype html><html lang="zh"><meta charset="utf-8"><title>Reservoir diagnostics</title>',
         '<style>body{font:16px system-ui;max-width:1200px;margin:30px auto;line-height:1.6;padding:16px}img{width:100%;height:auto}table{border-collapse:collapse;font-size:13px}td,th{border:1px solid #ddd;padding:5px}pre{white-space:pre-wrap}.note{color:#6c4d00}</style>',
         '<h1>水库联合诊断</h1>','<pre>'+esc('\n'.join(lines[:15]))+'</pre>',
         '<h2>总体统计</h2>',station_summary.to_html(index=False,escape=True)]
    for path in sorted((out/'figures').glob('all_*.png')):
        doc.append(f'<img loading="lazy" src="{path.relative_to(out)}" alt="{path.stem}">')
    for row in station_summary.to_dict('records'):
        ident=row['ID'];doc.extend([f'<h2>{esc(ident)} {esc(row["DamName"])}</h2>',table[table.ID==ident].to_html(index=False,escape=True)])
        for suffix in ['timeseries','seasonal_scatter','process_proxy']:
            path=out/'figures'/f'{ident}_{suffix}.png'
            if path.exists():doc.append(f'<img loading="lazy" src="{path.relative_to(out)}" alt="{ident} {suffix}">')
        for path in sorted((out/'figures/events').glob(f'{ident}_*.png')):
            doc.append(f'<img loading="lazy" src="{path.relative_to(out)}" alt="{path.stem}">')
    doc.append('<h2>解释与限制</h2><pre>'+esc('\n'.join(lines))+'</pre></html>')
    (out/'report.html').write_text('\n'.join(doc),encoding='utf-8')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evaluation-dir',required=True,type=Path)
    parser.add_argument('--output-dir',type=Path,help='Must not exist; default: timestamped runs directory beside code')
    parser.add_argument('--reference-mode',choices=['full','paired'],default='full',help='full: new reference cube + original model; paired: legacy evaluated pairs')
    parser.add_argument('--reference-file',type=Path,default=DEFAULT_REFERENCE)
    parser.add_argument('--simulation');parser.add_argument('--reference')
    parser.add_argument('--ids',nargs='+');parser.add_argument('--parameters',type=Path)
    parser.add_argument('--events',action='store_true');parser.add_argument('--event-radius',type=int,default=14)
    parser.add_argument('--max-event-plots',type=int,default=2,help='Per reservoir, largest complete candidate windows; tables retain all candidates')
    parser.add_argument('--dry-run',action='store_true',help='Read and validate all selected station data without writing output')
    args=parser.parse_args(argv)
    if args.event_radius<1 or args.max_event_plots<0:parser.error('Invalid event options')
    context=discover(args.evaluation_dir,args.simulation,args.reference,args.ids,reference_file=args.reference_file if args.reference_mode=='full' else None)
    params,param_path=read_parameters(context,args.parameters)
    # Preflight all data before creating any output directory.
    loaded=prepare_full(context) if context.get('mode')=='full' else {}
    if context.get('mode')!='full':
        for row in context['stations'].itertuples(index=False):
            loaded[row.ID]=load_station(context,pd.Series(row._asdict()))
    print(f'Preflight: {len(loaded)} reservoirs, {context["start"]}–{context["end"]}, {context["variables"]}',flush=True)
    if args.dry_run:
        counts={v:sum(any(a['variable']==v and a['n_pair']>0 for a in data[1]) for data in loaded.values()) for v in context['variables']}
        print(json.dumps(dict(paired_reservoirs=counts,selected_ids=context['stations'].ID.tolist()),ensure_ascii=False));return 0
    stamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')
    out=(args.output_dir or HERE/'runs'/f'{context["config"]["project"]["name"]}_{args.reference_mode}_{stamp}').resolve()
    if out==context['root'] or context['root'] in out.parents:
        raise ValueError('Output must be outside original evaluation directory')
    out.mkdir(parents=True,exist_ok=False)
    for directory in ['data','tables','figures','figures/events']:(out/directory).mkdir(exist_ok=True)
    from plotting import station_plots,process_plot,summary_plots,location_plot,event_plot
    allmetrics=[];allaudits=[];allseasonal=[];alljoint=[];allquality=[];allevents=[];input_records=list(context.get('model_records',[]));selection=[];summaries=[]
    for row in context['stations'].itertuples(index=False):
        ident=row.ID;name=str(getattr(row,'DamName',ident));frame,audits,records=loaded[ident]
        print(f'Plotting {ident} {name}',flush=True)
        input_records.extend(records)
        par=params[params.ID==ident]
        capacity=float(par.iloc[0].get('capacity_mcm',np.nan)) if len(par) else np.nan
        for audit in audits:
            audit.update(DamName=name,parameter_present=bool(len(par)),capacity_mcm=capacity)
            v=audit['variable'];m=metrics(frame[f'obs_{v}'],frame[f'sim_{v}'])
            normalized=m['RMSE']/capacity if v=='storage' and np.isfinite(capacity) and capacity>0 else np.nan
            allmetrics.append(dict(ID=ident,DamName=name,variable=v,units=audit['units'],status=audit['status'],
                                   interpretation='PROVISIONAL_STORAGE_TIME_SUPPORT' if v=='storage' else 'EVALUATION_LABEL_PAIRING',
                                   provisional_nrmse_capacity=normalized,**m))
        allaudits.extend(audits)
        for col in frame:
            values=frame[col]
            for flag,mask in [('NEGATIVE_RETAINED',values<0),('ABOVE_PARAMETER_CAPACITY_RETAINED',(values>capacity) if col.endswith('storage') and np.isfinite(capacity) else pd.Series(False,index=frame.index))]:
                for time,value in values[mask].items():allquality.append(dict(ID=ident,series=col,time=str(time),value=value,flag=flag))
        seasons=pd.DataFrame(seasonal(frame,ident));allseasonal.extend(seasons.to_dict('records'))
        j=joint(frame,ident,capacity)
        if context.get('mode')=='full':j.update(classification_status='NOT_COMPUTABLE_NO_REFERENCE_STORAGE',provisional_class='UNDETERMINED')
        alljoint.append(j)
        summary=dict(ID=ident,DamName=name,has_pairs=any(a['n_pair']>0 for a in audits),capacity_mcm=capacity,
                     n_joint=j['n_joint'],provisional_class=j['provisional_class'])
        for a in audits:summary[a['variable']+'_n']=a['n_pair']
        summaries.append(summary)
        frame.to_csv(out/'data'/f'{ident}_daily.csv')
        proxy=balance_proxies(frame);proxy.to_csv(out/'data'/f'{ident}_process_proxy.csv')
        station_plots(frame,name,ident,capacity,seasons,out/'figures')
        process_plot(frame,proxy,name,ident,out/'figures')
        if args.events:
            candidates=events(frame,ident,args.event_radius);allevents.extend(candidates)
            complete=[e for e in candidates if e['status']=='CANDIDATE_WINDOW_COMPLETE']
            for e in sorted(complete,key=lambda e:e['obs_inflow_peak'],reverse=True)[:args.max_event_plots]:
                event_plot(frame,e,name,out/'figures/events')
    table=pd.DataFrame(allmetrics);audits=pd.DataFrame(allaudits);summ=pd.DataFrame(summaries)
    checks=reconcile(context,table)
    for v in context['variables']:
        match=context['matching'][v]
        sets={'final_selected':set(context['stations'].ID),'metric_table':set(context['metrics'][v].ID),
              'matching_selected':set(map(str,match.get('selected_ids',[])))}
        union=set.union(*sets.values())
        for ident in sorted(union,key=int):
            selection.append(dict(ID=ident,variable=v,**{key:ident in ids for key,ids in sets.items()}))
    objects={'metrics':table,'matching_audit':audits,'seasonal':pd.DataFrame(allseasonal),
             'joint_diagnostics':pd.DataFrame(alljoint),'metric_crosschecks':checks,'reservoir_summary':summ,
             'selection_audit':pd.DataFrame(selection),'data_quality':pd.DataFrame(allquality,columns=['ID','series','time','value','flag']),
             'events':pd.DataFrame(allevents) if allevents else pd.DataFrame(columns=['ID','event','anchor','status'])}
    if context.get('mode')=='full':
        objects['selection_audit']=context['selection_audit']
        objects['reference_qc_summary']=pd.DataFrame(context.get('qc_summary',[]))
    for name,df in objects.items():df.to_csv(out/'tables'/f'{name}.csv',index=False)
    summary_plots(table,context['stations'],audits,out/'figures');location_plot(context['stations'],table,context,out/'figures')
    for path in context['inventory']:
        input_records.append(dict(path=str(path),sha256=sha(path),size=path.stat().st_size))
    if param_path:
        input_records.append(dict(path=str(param_path),sha256=sha(param_path),size=param_path.stat().st_size))
    maskpath=context['config']['project'].get('basin_mask')
    if maskpath and Path(maskpath).is_file():input_records.append(dict(path=maskpath,sha256=sha(maskpath),size=Path(maskpath).stat().st_size))
    sources=[dict(path=str(path),sha256=sha(path)) for path in sorted(HERE.glob('*.py'))]
    dump_json(out/'provenance.json',dict(evaluation_dir=context['root'],output_dir=out,arguments=vars(args),
             project=context['config']['project'],simulation=context['simulation'],reference=context['reference'],
             source_files=sources,input_files=list({r['path']:r for r in input_records}.values()),
             python=sys.version,platform=platform.platform(),numpy=np.__version__,pandas=pd.__version__,
             storage_time_support='absent' if context.get('mode')=='full' else 'unverified',time_alignment='exact_source_calendar_labels_no_shift',
             reference_mode=args.reference_mode,reference_file=context.get('reference_file'),reference_metadata=context.get('reference_metadata'),reference_time_support=context.get('reference_time_support'),
             classification_thresholds=dict(positive_pbias_pct=10,storage_bias_fraction=.05,storage_nrmse=.1,outflow_KGE=.5),
             input_manifest_generated_at=context['manifest'].get('generated_at')))
    write_report(out,context,summ,table,checks,objects['events'])
    # Validation is independent and checks exported values as well as input fingerprints.
    from validate_outputs import validate
    result=validate(out);dump_json(out/'validation.json',result)
    print(f'OUTPUT={out}',flush=True);print(json.dumps({k:v for k,v in result.items() if k!='checks'},ensure_ascii=False),flush=True)
    return 0 if result['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
