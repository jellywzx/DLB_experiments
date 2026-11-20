# 通用水库联合诊断

以 OpenBench **日尺度**评估目录的 manifest 提供实验范围、时段和模拟路径，默认使用新汇总参考文件及原始模型水库历史；旧配对数据模式仍可显式选择。支持其他流域、年份、模拟方案及参考来源采用相同产物结构的评估。原有实验、评估结果和 `missouri_joint_2001_2002` 均保持只读。

## 本次新增功能

- 将原来固定六座水库和2001–2002年的分析扩展为由评估manifest、最终站点清单及配对文件驱动的通用入口。
- 自动继承评估时段，逐变量审计匹配、有效样本、单位与时间标签，保留缺变量和无有效观测水库的说明。
- 新增逐库三变量时间序列、季节循环/散点、探索性调蓄代理、候选事件，以及总体指标、覆盖率和空间对照图。
- 重算并核对已有OpenBench指标，保存输入/代码指纹，通过独立验证器检查导出的数据、图件和输入未变动情况。
- 提供合成数据回归测试，覆盖库容重复换算、缺测、重复日期、文件重叠、时间标签不一致和全球掩膜的区域裁剪。

本目录在Git中仅维护代码、测试和说明；实验数据、生成图件、运行结果、日志及缓存不提交。下文的实际运行数字是本地验证记录，GitHub仓库不包含对应数据或图件。

## 2026-11-20：新汇总参考文件模式（默认）

默认 `--reference-mode full`，默认参考文件：
`/share/home/dq013/zhwei/OpenBench/Reference/Station/Anth/Dam/ResOpsUS/OpenBench_ResOpsUS_Daily_full.nc`。
可通过 `--reference-file` 指定其他同结构文件；不修改参考文件或旧OpenBench评价。

```bash
python analyze.py --evaluation-dir /path/to/evaluation --reference-file /path/to/OpenBench_ResOpsUS_Daily_full.nc --dry-run
python analyze.py --evaluation-dir /path/to/evaluation --reference-file /path/to/OpenBench_ResOpsUS_Daily_full.nc --output-dir /path/to/new_results
# 原有已评估配对文件模式：保持旧29座名单和严格指标重现检查
python analyze.py --evaluation-dir /path/to/evaluation --reference-mode paired --output-dir /path/to/legacy_results
```

- 新参考有526个唯一 `station_id`，从 `ResOpsUS_<GRanD ID>` 严格解析；`station`只是下标。使用科学变量 `Dam_Inflow/Dam_Outflow`，不使用 `resops_*_raw_mean` 补缺。
- 新模式从参考全部ID开始，以模型ID、原始参考坐标经纬范围、原始参考坐标最近邻流域掩膜>0及可选 `--ids` 重建集合。旧参考CSV、旧29座清单和旧指标表都不作为筛选门槛。掩膜采样坐标及值见 `selection_audit.csv`；该表保留所有参考候选的逐步结果。
- 模型按配置路径/prefix逐月读取 `resv_GRAND_ID` 与 `qresv_in/qresv_out/volresv`，每月重新查ID，最终取所有月的ID交集。只读取所选水库的一维向量，不读取全球网格；每月仅开一次。
- 继承评估起止年，按精确源时间标签配对。重复日期、跨月日期重叠或日内标签不一致均报错；不自动把午夜与noon合并。源文件明确noon只是索引，不能据此确认物理日界。
- 使用科学变量已有QC掩膜，保留负入流；源QC诊断（人工QC前）按库/变量汇总至 `reference_qc_summary.csv`，不能把其计数等同最终有效/剔除天数。逐站QC元数据保存在筛选审计表，源全局QC说明、时间支持及SHA256记录在provenance。
- **此新文件没有参考库容**：只展示模型库容，参考库容保持NaN；不混用旧参考库容，禁用库容指标与三变量分类/代理图。输入参数容量仅作模型图上的辅助线。
- `metric_crosschecks.csv` 与旧OpenBench表比较时把不同数值标记为 `SOURCE_DIFFERENCE`，不要求新来源重现旧分数，也不把来源差异当计算失败；只在paired模式要求严格一致。没有旧行时明确记为 `NO_OPENBENCH_ROW`。差异可包含参考值、配对样本和原始模拟精度变化，单个分数差异不做唯一归因。
- `metrics.csv` 新增观测有符号和、绝对值和、最小值、负值比例及抵消比；`pbias_applicability` 提示非正分母或强抵消。负PBIAS在负观测均值时不能解释为低估；KGE中的均值比也受影响。指标不剪裁、负值不删改。
- 原始月历史只记录大小/mtime并检查读取期间及验证时未变，不为哈希而扫描无关全球网格；这是元数据一致性检查，不是月历史内容的逐字节哈希证明。新参考与配置仍记录SHA256。

下列原有“数据与科学解释”条目中涉及最终站点CSV、已处理配对NetCDF和库容观测的行为，仅适用于 `--reference-mode paired`。其余缺测保留、科学解释边界和输出方式继续适用。

## 使用

环境：`/share/home/dq134/wzx/openbench/openbench_1015/.venv/bin/python`。依赖 numpy、pandas、xarray、scipy、matplotlib、Pillow 及可用 NetCDF 引擎；测试另需 pytest。不联网下载底图。

```bash
python analyze.py --evaluation-dir /path/to/openbench_case --dry-run
python analyze.py --evaluation-dir /path/to/openbench_case --output-dir /path/to/new_results
python analyze.py --evaluation-dir /path/to/openbench_case --ids 293 319 --events --max-event-plots 2
```

运行入口为本目录下 `analyze.py`。默认输出位于本目录 `runs/<项目名>_<上海时区时间戳>`；显式输出目录必须尚不存在，且位于原评估目录外。`--dry-run` 读取检查全部选中配对数据，不创建输出目录。多个模拟/参考来源须用 `--simulation`、`--reference` 指定。`--parameters` 可指定坝参数 NetCDF；默认尝试模拟历史目录同级 `riverlake/unitcatchment_regional.nc`，不存在时保留缺参数状态，仍绘制可用数据。

评估时段由 `canonical_config.project.years` 决定，并与站点 `use_syear/use_eyear` 取交集。实际文件不足时完整请求日期范围保留缺测；不擅自缩短时段掩盖覆盖问题。当前支持日尺度；其他时间分辨率明确拒绝。水库身份使用精确 GRanD ID，不以名称或邻近位置替代。

## 数据与科学解释

- 最终 `Flag=True` 站点全部保留。较大的 matching JSON 候选集合只用于集合对照，不作为本次最终评估清单。`--ids` 可选择最终清单中的子集。
- 从 `data/stn_<参考>_<模拟>` 或等价双下划线目录读取配对文件；按 manifest 声明选择变量。单站非时间维仅可为单元素；多站维或未知单位明确报错。
- 以配对文件的 **units** 转换到流量 m³/s、库容 MCM。已处理的 MCM 不再除以一百万，manifest 的原始模拟单位不会覆盖实际文件单位。
- 继承现有时间标签，无二次偏移。重复日期、文件重叠、一天多条数据、参考与模拟不同日内时间标签均报错，不静默归一化到同一天。NetCDF 填充值由 xarray 解码，明显巨大哨兵和非有限值保留为 NaN；负值仅标记，不裁剪。
- 单变量指标采用各自共同有限样本；联合诊断另用六条序列共同有效日期。季节循环采用逐变量配对日期，并输出每月样本数。KGE2009、相关系数、rSD、PBIAS 与已有表对照，标准差 ddof=0。CSV 保留完整指标值；总体热图/位置图只截断色标，不修改数值。
- **库存时间支持未核实**：复算库存指标保留以便与已有评估核对，但解释为探索性。参数库容不保证等于模型运行时有效容量。所有正式调蓄类别均为 UNDETERMINED。
- 参考脚本的探索性类别：A=入/出流PBIAS均>10%且库容偏差/容量>5%；B=入/出流均>10%且库容偏差/容量≤5%；C=入流高估、出流KGE≥0.5且绝对偏差≤10%、库容绝对归一化偏差≤5%且归一化RMSE≤10%且r≥0.5；其余D。没有完整三变量或可靠正容量时 UNDETERMINED。不能据此自动宣称坝模块有效或无效。
- I−O 与日均库容变化仅为代理，缺少日界库存和完整源汇账本，不检验严格守恒。不把按名称排列的水库当成级联关系，不自动推断误差传播。
- `--events` 以观测入流90%分位数/峰值锚定候选窗口，默认半径14日。拒绝缺数据、边界或重叠窗口；峰值并列或落窗口边界不计算削峰/滞时。候选身份仍需物理核实，不能将日均峰当瞬时洪峰。所有候选保存到CSV；只对每库最大若干个完整候选绘图（默认2），`--max-event-plots 0` 只生成表。
- 本程序依据既有处理数据，不默认再次扫描原始月历史或原始观测；未提供原始数据独立重处理审计，不据当前源码推断生产二进制版本。

## 输出

- `report.html`：中文浏览报告，引用相对路径图件；分享时须保留整个结果目录。
- `report.txt`：中文诊断、缺失说明和方法限制。
- `figures/`：逐库时间序列、季节循环/散点、条件满足时的调蓄代理；总体指标热图、覆盖率、入/出流偏差、位置图。全部 PNG 与 PDF。
- `data/`：逐库配对日序列、调蓄代理。
- `tables/`：`metrics.csv`、`matching_audit.csv`、`selection_audit.csv`、`reservoir_summary.csv`、`seasonal.csv`、`joint_diagnostics.csv`、`metric_crosschecks.csv`、`data_quality.csv`、`events.csv`。
- `provenance.json`：输入与代码 SHA256、实际评估配置、软件版本和诊断阈值。
- `validation.json`：独立重算样本数/RMSE/PBIAS、指标表对照、图件检查、输入未改动与代码指纹检查。

匹配记录中存在、但无有限参考数据的站点保留模拟线和说明；不捏造观测或填零。空间图优先原始参考坝址 `LON_org/LAT_org`，其余使用评估参考坐标；可用时叠加本地流域掩膜，不以河网格点代表真实坝址。

## 检查

```bash
python -m pytest tests -q
python validate_outputs.py /path/to/results
```

独立验证器不修改已有结果。OpenBench指标比较容差为 `rtol=1e-4, atol=1e-5`，用于处理已保存float32配对数据及CSV的舍入差异，差值完整保存；存在超限差异时分析进程返回非零并在验证结果中列明，不能视作通过。

## 旧paired模式历史运行（2026-11-19）

2026-11-19 已对 `EXP000_spinup_USA_missouri_dam_all_E1_D` 的2001–2005年日评估执行，启用 `--events --max-event-plots 2`。

结果：本地文件 `runs/EXP000_spinup_USA_missouri_dam_all_E1_D_20261119_164704/report.html`（未提交）。29座最终匹配水库全部保留，28座至少有一个变量有效配对；入流25、出流27、库容28座。Guernsey（901）三变量均有评估生成的“请求时段无有限参考数据”跳过记录；保留模拟图与明确说明。

12项测试通过；320项现有OpenBench指标全部在上述容差内重现，0项超限。最终890项产物/输入检查通过，PNG和PDF各124份。364条高流量候选窗口中94条为数据完整且峰值无歧义的候选，仍未确认同一物理洪水；每库最多选2条绘图。已目视检查Fresno时间序列、总体热图及位置图。空间图使用全球掩膜的区域裁剪，并明确设置评估范围；本次该图单独重绘步骤记录在 `provenance.json.postprocessing` 中。

测试环境出现过第三方NetCDF/NumPy兼容性及弃用警告；实际读取、写出、数值核验通过。时间轴绘图产生AutoDateLocator自动选择间隔提示，生成图件经核验可用。本次未修改第三方依赖或模型代码。

### 新模式验证进度（2026-11-20）

16项单元/回归测试通过；包含跨月模型列重排、新来源差异状态和负分母指标标记。两座水库合成案例完整执行读取、绘图、中文报告及独立验证，63项检查通过（8张PNG及8份PDF）。真实2001–2005年预检重建80座区域/流域/模型ID匹配水库，入流58座、出流76座有配对；307、753、870、884、895均入选。旧paired模式预检仍为29座（入流25、出流27、库容28座）。

`selection_audit.csv`的`model_months_present`仅对最初通过空间及首月ID筛选的目标计数；其他候选记0代表未进入逐月提取，不表示已核查其全部月份均不存在。源`qc_excluded_periods`、名称/坐标等元数据在该表按站保留；具体评估窗口的有效性以matching_audit.csv为准。

新模式真实全量运行完成：`runs/EXP000_spinup_USA_missouri_dam_all_E1_D_full_20261120_151655/report.html`。80座纳入，76座至少一个变量有配对；入流58座、出流76座，五座干流水库均有双变量配对。164张PNG及164份PDF，1311项独立验证通过；事件功能本次未启用。当前QC元数据描述来源处理，不代表科学准确性已得到外部验证。未提交Git。

## 单页水库位置总览

`plot_reservoir_overview.py --run-dir /path/to/diagnostic_run [--out-dir /path/to/new_map_dir]` 从本次实际纳入的水库清单和原始参考坐标绘制总图。地图逐点标注GRanD ID，右侧列出完整ID—名称索引，颜色表示入/出流配对可用性，五座已核实干流水库加星号。输出PNG、PDF、位置索引CSV及来源/标签重叠检查JSON；默认在独立时间戳目录创建结果，不覆盖原诊断结果。
