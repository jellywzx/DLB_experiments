from pathlib import Path
import pandas as pd,numpy as np,json
from PIL import Image
p=Path(__file__).resolve().parent;s=json.loads((p/'summary.json').read_text());a=pd.read_csv(p/'simulated_and_observed_daily.csv',parse_dates=['date']).set_index('date');b=pd.read_csv('/share/home/dq134/wzx/DLB_experiments/dam_analysis/missouri_joint_2001_2002/runs/20261116_134143/data/fortpeck_daily.csv',parse_dates=['date']).set_index('date');d=a[['qresv_in','Landusky','USGS_Landusky_m3s']].join(b[['obs_inflow','sim_inflow']]);sumup=a.seq_25873+a.seq_164196
v={'max_abs_inflow_minus_direct_upstream_m3s':float(abs(a.qresv_in-sumup).max()),'max_abs_inflow_minus_prior_audit_m3s':float(abs(d.qresv_in-d.sim_inflow).max()),'fortpeck_obs_mean':float(d.obs_inflow.mean()),'fortpeck_pbias_pct':float((d.qresv_in.sum()/d.obs_inflow.sum()-1)*100),'sim_increment_from_Landusky_m3s':float((d.qresv_in-d.Landusky).mean()),'obs_FortPeck_minus_Landusky_m3s':float((d.obs_inflow-d.USGS_Landusky_m3s).mean()),'Landusky_excess_m3s':float((d.Landusky-d.USGS_Landusky_m3s).mean()),'additional_excess_below_Landusky_m3s':float(((d.qresv_in-d.Landusky)-(d.obs_inflow-d.USGS_Landusky_m3s)).mean())}
(p/'flow_crosschecks.json').write_text(json.dumps(v,indent=2));print(v)
assert len(a)==730 and not a.index.duplicated().any();assert s['missing_full_upstream']==s['extra_regional_upstream']==0;assert len(pd.read_csv(p/'upstream_units.csv'))==1748;assert abs(a.qresv_in-sumup).max()<1e-8;assert abs(d.qresv_in-d.sim_inflow).max()<1e-8
for n in ['fort_peck_upstream.png','flow_diagnosis.png']:
 with Image.open(p/n) as im:im.verify()
(p/'validation.json').write_text(json.dumps({'rows':730,'unique_upstream_units':1748,'regional_full_upstream_set_equal':True,'history_equals_direct_upstream_sum':True,'history_matches_previous_raw_audit':True,'png_decode_pass':True,'scope':'read-only target-network analysis; no physical code edit or model rerun'},indent=2))
