# Missouri 三变量联合诊断

独立读取 E1_D 原始月历史、ResOpsUS 原始 CSV 与坝参数；不修改模型物理、原始数据、OpenBench 配置或既有评估结果。

## 已实际运行

- 计算节点：`node111`，环境：`/share/home/dq134/wzx/openbench/openbench_1015/.venv`。
- 时段：2001-01-01—2002-12-31，24 个月历史均存在；五座可靠匹配，Fort Randall 为 `NO_REFERENCE`，未采用加拿大 ID 2356。
- 实际结果：[runs/20261116_134143](runs/20261116_134143)。[主报告](runs/20261116_134143/report/missouri_reservoir_diagnostics.md)、[核验补充](runs/20261116_134143/report/verification_notes.md)、[210 项检查](runs/20261116_134143/report/validation.json)。
- 测试：12 passed；产物：18 行变量指标、6 行匹配审计和联合诊断、96 条候选事件窗口记录、6 份逐日数据、11 张 PNG（300 dpi）和对应 11 份 PDF。Fort Randall 的三面板图只显示缺数据说明。

## 复现命令

```bash
bash /share/home/dq134/wzx/DLB_experiments/dam_analysis/missouri_joint_2001_2002/run_node111.sh
```

该脚本通过 SSH 在 node111 激活上述环境，执行测试、分析和产物验证。每次创建新的时间戳结果目录，不覆盖非空目录。输入路径可通过 `python analyze.py --help` 所列参数调整。

本次首次分析执行上述命令；增加时间支持拒绝测试和独立产物检查后，又执行：

```bash
ssh -o BatchMode=yes node111 'bash -s' <<'NODE'
set -euo pipefail
cd /share/home/dq134/wzx/DLB_experiments/dam_analysis/missouri_joint_2001_2002
source /share/home/dq134/wzx/openbench/openbench_1015/.venv/bin/activate
export MPLBACKEND=Agg OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
python -m pytest test_analyze.py -q --disable-warnings
python validate_outputs.py runs/20261116_134143
NODE
```

验证器拒绝覆盖已有 `report/validation.json`。再次完整复现请运行包装脚本，生成新目录。

## 文件职责

- `analyze.py`：身份与空间核对、按每月真实 ID 提取、单位与时间审计、指标、候选事件、调蓄代理、图表、报告和来源记录。复用 OpenBench 的严格 ID/单位解析器。
- `test_analyze.py`：指标公式、有效掩膜、零分母、库容归一化、跨月列重排、缺测保留、日期重复、非连续差分、事件窗口、未知时间支持拒绝、加拿大同名坝排除。
- `validate_outputs.py`：检查真实产物的配对样本、指标重算、缺测、单位、源脚本哈希、图片/PDF、参考数据一致性和严格/探索性区分。
- `run_node111.sh`：运行入口。

## 使用边界

模型原始日历史为 noon 标签；不应用用户目录旧模型配置的 -1 day 偏移。现存源码将入/出流和库存都按路由子步积分后求日均，不能把库存当日末值。原始 ResOpsUS 的 ACE_OMA 元信息未明确库存每日统计时刻或方法、物理日界/时区，且缺少生成本批输出的二进制构建证据。因此库存严格指标留空（NaN），探索性结果单列为 `provisional_*`；流量为原始日标签配对结果。所有缺测保持 NaN，无插值或补零。

完整数值守恒检验缺少日界状态、完整源汇/裁剪账本及构建版本，标记“无法完整检验”；日均库存差和 I−O 计算的残差仅为代理。候选事件的同一洪水身份还需人工确认，日均峰值不能代替瞬时洪峰。分类阈值、实际搜索路径、输入文件、源码摘录与哈希均保存在结果目录。

无 Git commit 或 push。

## 六座水库空间位置图

脚本：`plot_reservoir_locations.py`；最终图：`spatial_map_20261116_final/missouri_six_reservoir_locations.png` 和同名 PDF。
配套 `reservoir_locations.csv` 记录每个点的坐标/身份状态/来源，`sources.json` 记录流域掩膜和区域河网路径。
五座使用已审计的 ResOpsUS 坝址；Fort Randall 使用 USGS-06453000 坝区站点坐标作位置代理，未赋予 ResOpsUS ID，未修改 NO_REFERENCE 状态。
底图复用 `openbench_dams_rivers_common.py` 的 Missouri 掩膜与路由映射；蓝色为上游面积至少 5,000 km² 的 CoLM 河网连接。

在 node111 激活指定环境后执行（输出目录须不存在）：

```bash
python plot_reservoir_locations.py --out spatial_map_20261116_final
```
