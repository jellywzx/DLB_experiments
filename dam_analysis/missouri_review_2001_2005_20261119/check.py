"""Read-only Missouri selection and signed-PBIAS audit. Writes only to a NEW --out directory."""
from pathlib import Path
import argparse,json,hashlib,sys
import numpy as np,pandas as pd,xarray as xr
P=Path('/share/home/dq134/wzx/CoLM/DLB_nml/EXP000_spinup_USA')
E=P/'openbench_output/EXP000_spinup_USA_missouri_dam_all_E1_D'
D=Path('/share/home/dq134/wzx/DLB_experiments/dam_analysis')
R=D/'reservoir_diagnostics/runs/EXP000_spinup_USA_missouri_dam_all_E1_D_20261119_164704'
OLD=D/'missouri_joint_2001_2002'
REF=Path('/share/home/dq013/zhwei/OpenBench/Reference/Station/Anth/Dam/ResOpsUS')
RAW=Path('/share/home/dq013/zhwei/OpenBench/Data_ref/stn_ref/Dam_River_Lake/ResOpsUS/source/time_series_all')
SRC=Path('/share/home/dq134/wzx/openbench/openbench_1015/src')
IDS=[307,753,870,884,895]
EXT=[872,893,936,953,956,958,963]
VARS={'inflow':'qresv_in','outflow':'qresv_out','storage':'volresv'}
ITEM={'inflow':'Dam_Inflow','outflow':'Dam_Outflow','storage':'Dam_Water_Storage'}
inputs=set()
def used(p):inputs.add(Path(p));return Path(p)
def readcsv(p,**kw):return pd.read_csv(used(p),**kw)
def readjson(p):return json.loads(used(p).read_text())
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1048576),b''):h.update(b)
 return h.hexdigest()
def series(p,var):
 with xr.open_dataset(used(p)) as ds:
  z=ds[var].squeeze(drop=True);s=pd.Series(np.asarray(z.values,float),index=pd.to_datetime(ds.time.values));s[~np.isfinite(s)|(abs(s)>=1e30)]=np.nan
  return s,z.attrs.get('units','')
def table(df):return df.to_string(index=False,float_format=lambda x:f'{x:.6g}')

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
 manifest=readjson(E/'run_manifest.json');cfg=manifest['canonical_config'];project=cfg['project']
 plist=Path(cfg['reference']['overrides']['ResOpsUS']['fulllist']);prep=readcsv(plist);original=readcsv(REF/'list/ResOpsUS_reservoir_matched_06min.csv')
 station=readcsv(E/'stn_ResOpsUS_E1_D_list.csv');selection=readcsv(R/'tables/selection_audit.csv');matching=readcsv(R/'tables/matching_audit.csv')
 old=readcsv(OLD/'runs/20261116_134143/tables/reservoir_matching_audit.csv');evalmetrics=readcsv(R/'tables/metrics.csv')
 matches={v:readjson(E/'reservoir_matching'/f'{ITEM[v]}_ResOpsUS_E1_D.json') for v in VARS}
 history=Path(cfg['simulation']['E1_D']['root_dir']);maskpath=Path(project['basin_mask'])
 mask=xr.open_dataset(used(maskpath)).basin_mask
 route=history.parent/'riverlake/unitcatchment_regional.nc'
 with xr.open_dataset(used(route)) as ds:
  damids=ds.dam_GRAND_ID.values;damseq=ds.dam_seq.values;rx=ds.seq_lon.values;ry=ds.seq_lat.values
 # Read only target IDs, three reservoir variables and time metadata from 60 existing files.
 model={v:[] for v in VARS};modelrows=[]
 for year in range(2001,2006):
  for month in range(1,13):
   path=history/f'EXP000_spinup_USA_D_0p1_hist_unitcat_{year}-{month:02d}.nc'
   with xr.open_dataset(used(path)) as ds:
    ids=ds.resv_GRAND_ID.values;pos=[int(np.where(ids==i)[0][0]) for i in IDS]
    modelrows.append(dict(path=str(path),**{str(i):bool(i in ids) for i in IDS}))
    for v,var in VARS.items():
     a=ds[var].isel(reservoir=pos).transpose('time','reservoir').values.astype(float);a[(abs(a)>=1e30)|~np.isfinite(a)]=np.nan
     model[v].append(pd.DataFrame(a,index=pd.to_datetime(ds.time.values),columns=IDS))
  print('model year',year,flush=True)
 model={v:pd.concat(parts) for v,parts in model.items()}
 pd.DataFrame(modelrows).to_csv(out/'model_file_presence.csv',index=False)
 ar=[];maskrows=[]
 for ident in IDS:
  o=old[old.grand_id==ident].iloc[0];refpath=REF/'dataset'/f'ResOpsUS_{ident}.nc'
  for kind,lon,lat in [('old_reference',o.reference_lon,o.reference_lat),('parameter_dam',o.model_lon,o.model_lat),('routing_cell',float(rx[int(damseq[np.where(damids==ident)[0][0]])-1]),float(ry[int(damseq[np.where(damids==ident)[0][0]])-1]))]:
   val=mask.sel(lon=lon,lat=lat,method='nearest');maskrows.append(dict(ID=ident,coordinate_kind=kind,lon=lon,lat=lat,mask_lon=float(val.lon),mask_lat=float(val.lat),mask_value=float(val)))
  for v in VARS:
   obs,units=series(refpath,v);obs=obs.loc['2001':'2005'];sim=model[v][ident]
   ar.append(dict(ID=ident,name=o.reservoir,variable=v,in_prepared_list=bool((prep.ID==ident).any()),in_original_06min_list=bool((original.ID==ident).any()),
    in_common_ids=str(ident) in matches[v]['common_ids'],in_selected_ids=str(ident) in matches[v]['selected_ids'],
    in_final_station=bool((station.ID==ident).any()),in_selection_audit=bool(((selection.ID==ident)&(selection.variable==v)).any()),
    in_matching_audit=bool(((matching.ID==ident)&(matching.variable==v)).any()),
    model_months_present=sum(r[str(ident)] for r in modelrows),model_finite_n=int(sim.notna().sum()),model_mean=float(sim.mean()),
    ref_exists=refpath.exists(),ref_finite_n=int(obs.notna().sum()),ref_mean=float(obs.mean()),ref_units=units,ref_path=str(refpath),
    old_pair_n=int(o[v+'_paired_n']),old_reference_csv=o.reference_file))
 a=pd.DataFrame(ar);a.to_csv(out/'mainstem_evidence.csv',index=False);pd.DataFrame(maskrows).to_csv(out/'mainstem_mask_samples.csv',index=False)
 # Reproduce actual 60 -> 29 selection from the current whitelist. This writes no evaluation files.
 flags=prep.Flag.astype(str).str.lower().isin(['1','true']);clo=np.maximum(prep.SYEAR,2001);chi=np.minimum(prep.EYEAR,2005)
 candidates=prep[flags&((chi-clo)>=project['min_year_threshold'])&prep.LON.between(*project['lon_range'])&prep.LAT.between(*project['lat_range'])].copy()
 common=set(map(int,matches['inflow']['common_ids']));candidates=candidates[candidates.ID.isin(common)]
 vals=mask.sel(lon=xr.DataArray(candidates.LON.values,dims='station'),lat=xr.DataArray(candidates.LAT.values,dims='station'),method='nearest').values
 kept=candidates.loc[vals.astype(bool)]
 repro={'prepared_rows':len(prep),'original_rows':len(original),'candidate_count':len(candidates),'mask_kept':len(kept),
        'candidate_ids_equal_saved_selected':set(candidates.ID)==set(map(int,matches['inflow']['selected_ids'])),
        'kept_ids_equal_final':set(kept.ID)==set(station.ID),
        'five_ids_in_prepared':sorted(set(IDS)&set(prep.ID)), 'five_ids_in_original':sorted(set(IDS)&set(original.ID))}
 (out/'selection_reproduction.json').write_text(json.dumps(repro,indent=2))
 # Signed-flow statistics, always on identical finite dates for observed and simulated values.
 stats=[];cross=[];sensitivity=[]
 for ident in EXT:
  for v in VARS:
   prefix=ITEM[v];folder=E/'data/stn_ResOpsUS_E1_D'
   op=folder/f'{prefix}_ref_{ident}_2001_2005.nc';sp=folder/f'{prefix}_sim_{ident}_2001_2005.nc'
   if not op.is_file() or not sp.is_file():continue  # No paired metric exists for missing variables.
   obs,ou=series(op,v);sim,su=series(sp,VARS[v]);obs,sim=obs.align(sim,join='inner');good=np.isfinite(obs)&np.isfinite(sim);o=obs[good];s=sim[good]
   pb=100*(s-o).sum()/o.sum() if o.sum()!=0 else np.nan
   metric=evalmetrics[(evalmetrics.ID==ident)&(evalmetrics.variable==v)].iloc[0]
   for side,z in [('obs',o),('sim',s)]:
    positive=z[z>0].sum();negative=z[z<0].sum()
    row=dict(ID=ident,name=metric.DamName,variable=v,side=side,n=len(z),units=ou if side=='obs' else su,mean=z.mean(),minimum=z.min(),maximum=z.max(),
     negative_n=int((z<0).sum()),negative_fraction=float((z<0).mean()),zero_n=int((z==0).sum()),signed_sum=z.sum(),positive_sum=positive,negative_sum=negative,
     cancellation_ratio=abs(z.sum())/abs(z).sum() if abs(z).sum()>0 else np.nan,pbias_pct=pb,mean_bias=(s-o).mean(),rmse=np.sqrt(((s-o)**2).mean()))
    if v!='storage':row.update(signed_volume_MCM=z.sum()*86400/1e6,positive_volume_MCM=positive*86400/1e6,negative_volume_MCM=negative*86400/1e6)
    stats.append(row)
   # Original processed-reference values and raw source CSV are independent cross-checks.
   full,fu=series(REF/'dataset'/f'ResOpsUS_{ident}.nc',v)
   norm=lambda z:pd.Series(z.values,index=z.index.normalize())
   oo=norm(obs);ff=norm(full).reindex(oo.index)
   rawpath=RAW/f'ResOpsUS_{ident}.csv';raw=readcsv(rawpath);rawindex=pd.to_datetime(raw.date)
   rr=pd.Series(pd.to_numeric(raw[v],errors='coerce').values,index=rawindex).reindex(oo.index)
   goodf=np.isfinite(oo)&np.isfinite(ff);goodr=np.isfinite(oo)&np.isfinite(rr)
   cross.append(dict(ID=ident,variable=v,processed_ref_path=str(op),original_ref_path=str(REF/'dataset'/f'ResOpsUS_{ident}.nc'),raw_csv_path=str(rawpath),
    processed_original_mask_equal=bool(np.array_equal(np.isfinite(oo),np.isfinite(ff))),processed_original_max_abs_diff=float(abs(oo[goodf]-ff[goodf]).max()),
    processed_raw_mask_equal=bool(np.array_equal(np.isfinite(oo),np.isfinite(rr))),processed_raw_max_abs_diff=float(abs(oo[goodr]-rr[goodr]).max()),
    pbias_matches_saved=bool(np.isclose(pb,metric.percent_bias,rtol=1e-10,atol=1e-7))))
   if v!='storage':
    denom=abs(o).sum();sensitivity.append(dict(ID=ident,variable=v,n=len(o),signed_pbias=pb,absolute_observed_sum=denom,
     signed_bias_over_abs_observed_sum_pct=100*(s-o).sum()/denom if denom else np.nan,
     denominator_warning='NOT_STANDARD_PBIAS; sensitivity only; no negative-value clipping',
     positive_only_days=int((o>0).sum()),negative_days=int((o<0).sum())))
 st=pd.DataFrame(stats);st.to_csv(out/'extreme_statistics.csv',index=False);pd.DataFrame(cross).to_csv(out/'reference_crosschecks.csv',index=False);pd.DataFrame(sensitivity).to_csv(out/'denominator_sensitivity.csv',index=False)
 # Preserve specific file-line evidence, not entire logs.
 sourcewindows={SRC/'openbench/data/custom/ResOpsUS_filter.py':[(1,45),(76,138)],SRC/'openbench/data/basin_mask.py':[(145,190)],SRC/'openbench/data/reservoir.py':[(235,257)],
  D.parent/'dlb_prepare_resopsus.py':[(25,80)],OLD/'analyze.py':[(20,34),(330,385)],D/'reservoir_diagnostics/analyze.py':[(94,123)],
  SRC/'openbench/core/metrics.py':[(98,116),(350,373)],P/'config/openbench_manual/missouri/dam_all.yaml':[(1,48)]}
 excerpts=[]
 for path,ranges in sourcewindows.items():
  lines=used(path).read_text().splitlines()
  for lo,hi in ranges:excerpts.append('\n'+str(path)+'\n'+'\n'.join(f'{i+1}: {lines[i]}' for i in range(lo-1,min(hi,len(lines)))))
 log=used(E/'run.log').read_text().splitlines()
 for i,l in enumerate(log):
  if '60 common stations' in l or '60 -> 29' in l or 'Applied unit conversion for sim station data' in l:excerpts.append(f'{E/"run.log"}:{i+1}: {l}')
 (out/'evidence_excerpts.txt').write_text('\n'.join(excerpts))
 # Report A: distinguish verified exclusion stage from unknown origin of the whitelist omission.
 cmd=f'OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 /share/home/dq134/wzx/openbench/openbench_1015/.venv/bin/python {Path(__file__).resolve()} --out /tmp/missouri_review_NEW'
 lines=['A. 五座主要干流水库未纳入本次评价的具体原因','',
 '结论：307、753、870、884、895均不在本次配置指定的参考站点白名单中。这是已核实的、位于模型ID匹配和流域掩膜之前的排除原因；不是本次联合诊断绘图漏画，也不是这些水库没有模型/参考数据。',
 '当前上游原始06min参考清单同样不含这五个ID。未找到该原始清单制作时逐坝排除日志，故不能进一步声称它们当初因空间误差、匹配阈值或某个物理规则被移除。准备脚本中的重复ID排除规则不能解释这五个ID，因为原始清单中已无其任何行。',
 '', '配置与证据路径：',str(P/'config/openbench_manual/missouri/dam_all.yaml'),str(E/'run_manifest.json'),str(plist),str(REF/'list/ResOpsUS_reservoir_matched_06min.csv'),
 'canonical_config.reference.overrides.ResOpsUS.fulllist明确指定上述白名单；配置只给出参考来源和清单路径，未显式列出五坝ID。三变量均使用此清单。',
 '', '筛选链：参考白名单159行 → 范围、Flag、时段和精确模型ID匹配后60行 → Missouri格点掩膜后29行。',json.dumps(repro,ensure_ascii=False,indent=2),
 '现有代码的时间筛选要求 use_eyear-use_syear >= min_year，检查坐标LON/LAT，而非LON_org/LAT_org；匹配为精确GRanD ID，无最近邻替代。该路径中五坝从白名单入口就不存在。',
 '当前文件可精确重现保存的60候选和29最终ID集合；这是对当前文件的复核。未保存当时清单逐字节快照，不能将现存源码/清单的校验和冒充历史构建证据。',
 '', '审计表语义：',str(R/'tables/selection_audit.csv'),str(R/'tables/matching_audit.csv'),
 'selection_audit只覆盖最终清单、指标表和matching_selected的并集；五坝未进matching_selected，因此该表连“被排除”行都没有。matching_audit只覆盖最终Flag=True清单中的29座。二者都不是全体模型/参考ID的逐级拒绝日志。',
 'reservoir_matching/*.json的common_ids含全部1712个跨月模型共同ID；其中含五坝，不等于它们已被选入参考候选。selected_ids为60，五坝均不在。',
 '', '逐坝逐变量存在性与有效样本数（完整表 mainstem_evidence.csv）：',table(a[['ID','name','variable','model_months_present','model_finite_n','ref_finite_n','in_prepared_list','in_selected_ids','in_final_station']]),
 '', '反事实掩膜核验（并非重新评价）：',table(pd.DataFrame(maskrows)),
 '五坝旧核实坝址、参数坝址及真实路由格点的掩膜值均为1，坐标也在配置经纬范围内；有有效参考和模型数据。因此没有证据将其排除归因于本次流域掩膜、模型位置近邻失败或变量全缺测。由于白名单缺少这些行，无法观察一套不存在的白名单坐标；这里明确使用旧核实位置与当前路由位置作旁证。',
 '', '与2001–2002诊断的比较：',str(OLD/'analyze.py'),str(OLD/'runs/20261116_134143/tables/reservoir_matching_audit.csv'),
 '旧脚本TARGETS显式列五个可靠ID和一个未匹配的Fort Randall；从原始ResOpsUS CSV及月历史按精确ID独立提取，并核对名称/坝址。它不从这份159行参考白名单选对象，也不依赖本次OpenBench最终29座清单。故旧结果成功与本次白名单遗漏并不矛盾；新工具目前忠实反映了给定评估的选择范围，并非全Missouri水库完整性验证。',
 '', '未执行修复：没有补写白名单、修改掩膜或重跑评估；仅报告入口覆盖缺陷。若后续修复，应在新评估项目中明确纳入五坝并重新核验身份/时间支持。',
 '', '复现命令（--out必须是尚不存在的目录）：',cmd,
 f"rg -n '60 common stations|60 -> 29' {E/'run.log'}",
 'Python检查清单ID：',f"pd.read_csv('{plist}').query('ID in [307,753,870,884,895]')",'', '详细路径、行号见 evidence_excerpts.txt；原文件与代码SHA256见 fingerprints.json。']
 (out/'report_A_mainstem_exclusion.txt').write_text('\n'.join(lines))
 flow=st[st.variable!='storage'];obs=flow[flow.side=='obs'];sim=flow[flow.side=='sim']
 lines=['B. 极端PBIAS的数学原因、数据特征及指标适用性','',
 '重点核查Keyhole、Norton、Kirwin、Bonny，并补充现有流量指标绝对PBIAS≥1000%的其他站点：Angostura（893）、Enders（936）、Webster（963）。四个重点站点的负分母结论不扩展为其余三站均有负均值；应逐行读取统计。',
 '公式：PBIAS = 100×Σ(S−O)/ΣO = 100×(mean(S)/mean(O)−1)，所有项必须取相同有限配对日期。日均流量累计体积V=ΣQ×86400/10^6 MCM。缺测日不补零，累计体积只代表有效配对日，不冒充完整五年总量。库容只给均值/最小值等统计，不把库容逐日和当成累计流量。',
 '', '结论：四站入流ΣO均为负，正负观测相互抵消，净均值小而负；模拟均值为正。负PBIAS在这里不能解读为模型低估，它是负分母导致的符号反转和放大。四站出流均值为正但偏小，模拟显著更大，出流正PBIAS确实表示相对有符号总出流偏高；Bonny的观测出流变幅也极小，rSD易放大。',
 '', '观测统计（同变量模拟使用同样日期）：',table(obs[['ID','name','variable','n','mean','minimum','negative_fraction','signed_volume_MCM','positive_volume_MCM','negative_volume_MCM','cancellation_ratio','pbias_pct']]),
 '', '模拟统计：',table(sim[['ID','name','variable','n','mean','minimum','negative_fraction','signed_volume_MCM','pbias_pct','mean_bias','rmse']]),
 '', 'cancellation_ratio=|ΣO|/Σ|O|。接近0表明正负抵消，而不是所有日流量都接近0。配对样本不足也独立报告；不混用全有效观测均值和不同日期的模拟均值。',
 '', '参考来源交叉核验：',table(pd.DataFrame(cross)),
 '上述负值已存在于本次配对参考NetCDF、原始参考NetCDF和原始ResOpsUS逐库CSV中；按日标签比较保留缺测，差异量见表。未发现由本次绘图或PBIAS计算新产生负值的证据。即使多层一致，也只证明数据链一致，不证明负入流的物理含义或观测估算方法已核实。未核实它们是净入流、水量平衡反演、录入问题或实际回流；本报告不作此类归因。',
 '', '其他数学影响：KGE含beta=mean(S)/mean(O)，故同样受负/近零观测均值影响；不能把极低KGE全部视为时间过程极差。相关系数与rSD不含均值分母，但观测波动很小/接近常量时也不稳定。RMSE、MAE和绝对平均偏差可补充，但不解除数据定义问题。',
 '', '适用性与解读：',
 '1. 对四站入流，PBIAS数值可复现，但作为“偏高/偏低百分比”及跨库排名不适用；保留原值并标注SIGNED_OR_NEAR_ZERO_OBS_DENOMINATOR，不擅自删负值或截断到0。',
 '2. 对出流，正的小分母使巨大百分比成为真实的相对量，同时应报告m³/s平均偏差、RMSE与MCM累计差，避免仅据百分比判断绝对水量影响。',
 '3. 对库容，低观测平均库存也能产生大PBIAS；它不等同于容量误差。库容时间支持未核实，仍限探索性。全部12项变量/两侧统计见extreme_statistics.csv。',
 '4. denominator_sensitivity.csv另列100×Σ(S−O)/Σ|O|，仅展示抵消效应，明确不是标准PBIAS，不替换原评价。不得以筛掉负观测之后的高分宣称修复。',
 '', '实际路径：配对数据目录',str(E/'data/stn_ResOpsUS_E1_D'),'已有指标',str(E/'metrics'),'原始参考NetCDF',str(REF/'dataset'),'原始CSV',str(RAW),
 '', '复现命令：',cmd,
 '', '输入和评价结果未被修改；未重新运行CoLM/OpenBench；未提交Git。脚本以只读方式访问输入；fingerprints.json记录核查时的SHA256（大型月历史仅记录大小和mtime）。未保存读取前后双份哈希，不能将其称为前后逐字节不变性证明。详细明细见同目录CSV。']
 (out/'report_B_extreme_pbias.txt').write_text('\n'.join(lines))
 # Compare existing evaluation products with the previous independent diagnosis fingerprints.
 prior=readjson(R/'provenance.json');integrity=[]
 for record in prior['input_files']:
  path=Path(record['path'])
  if E in path.parents:integrity.append(dict(path=str(path),sha256_matches_previous_diagnostics=sha(path)==record['sha256']))
 (out/'existing_evaluation_integrity.json').write_text(json.dumps(integrity,indent=2))
 if not all(r['sha256_matches_previous_diagnostics'] for r in integrity):raise ValueError('Existing evaluation fingerprint changed')
 for name in ['report_A_mainstem_exclusion.txt','report_B_extreme_pbias.txt']:
  path=out/name;path.write_text(path.read_text()+f'\n补充完整性核验：与上次联合诊断保存的输入SHA256对照，{len(integrity)}个既有评估目录文件全部一致，详见 existing_evaluation_integrity.json。\n')
 used(Path(__file__))
 # Hash all read source files; full histories are fingerprinted by size/mtime to avoid reading unrelated global grids.
 records=[]
 for p in sorted(inputs):
  stat=p.stat();rec=dict(path=str(p),size=stat.st_size,mtime_ns=stat.st_mtime_ns)
  if p.parent==history:rec['verification']='READ_ONLY_SIZE_MTIME_METADATA; target arrays only'
  else:rec['sha256']=sha(p)
  records.append(rec)
 (out/'fingerprints.json').write_text(json.dumps(records,indent=2))
 checks={'mainstem_all_60_months':all(a.model_months_present==60),'all_five_absent_whitelist':not a.in_prepared_list.any(),
 'all_five_reference_has_data':bool((a.ref_finite_n>0).all()),'selection_60_reproduced':repro['candidate_ids_equal_saved_selected'],
 'selection_29_reproduced':repro['kept_ids_equal_final'],'extreme_pbias_reproduced':all(r['pbias_matches_saved'] for r in cross),
 'reference_masks_match':all(r['processed_original_mask_equal'] and r['processed_raw_mask_equal'] for r in cross)}
 (out/'checks.json').write_text(json.dumps(checks,indent=2));print(json.dumps(repro,indent=2));print(json.dumps(checks,indent=2));print(table(obs[['ID','variable','mean','minimum','negative_fraction','signed_volume_MCM','pbias_pct']]))
 if not all(checks.values()):raise SystemExit('Audit consistency checks failed; inspect reports')
if __name__=='__main__':main()
