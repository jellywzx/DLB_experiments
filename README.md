# CoLM DLB 区域实验工作流

本目录用于创建和管理水库（D）、堤防（L）与分汊（B）的区域全因子实验，包括 CoLM namelist、LSF/Slurm 作业、restart 交接、阶段检查和 OpenBench 配置。

所有工具都不会隐式提交作业。restart 工具默认 dry-run，只有显式使用 `--apply` 才会写入 case；提交助手只有显式使用 `--submit` 才会调用调度器。

## 本机服务器配置（weizx24）

本机使用独立配置 `configs/servers/weizx24.json`，无需修改旧服务器默认值。
优先级为 **显式命令行参数 > 选中的服务器配置 > 原有默认值**。
配置文件的选择顺序为 `--site-config` > 环境变量 `DLB_SITE_CONFIG`；两者均未设置时沿用原入口。
需要 Python 3.10 或更新版本。配置文件中的相对路径以该 JSON 所在目录为基准，命令行相对路径以当前目录为基准。

```bash
export DLB_ROOT=/home/weizx24/weizx24/DLB_experiments_new
export DLB_SITE="$DLB_ROOT/configs/servers/weizx24.json"

# 只读检查全部区域：目录、掩膜、源码字段、可执行权限、NetCDF 变量及逐月 forcing 文件名。
python3 "$DLB_ROOT/dlb_04_preflight.py" --site-config "$DLB_SITE" \
  --check-netcdf --check-forcing-files

# 草稿预览：仍缺少模型能力时也能查看完整配置；此目录不能用于正式提交。
python3 "$DLB_ROOT/dlb_01_create_experiment.py" EXP101 local Mekong \
  --site-config "$DLB_SITE" --output-dir "$DLB_ROOT/EXP101_local_Mekong_draft" --draft

# 问题解决后，在新目录中正式生成；不会运行模型或提交作业。
python3 "$DLB_ROOT/dlb_01_create_experiment.py" EXP102 local Mekong \
  --site-config "$DLB_SITE"
```

日常使用可以打开一次全局开关，后续创建、单独生成 namelist/作业、服务器预检都无需重复传 `--site-config`：

```bash
export DLB_SITE_CONFIG="$DLB_ROOT/configs/servers/weizx24.json"

python3 "$DLB_ROOT/dlb_01_create_experiment.py" EXP103 local Mekong --draft
python3 "$DLB_ROOT/dlb_04_preflight.py" --check-netcdf --check-forcing-files

# 本条命令临时关闭自动加载；恢复原默认值，显式参数仍有效。
python3 "$DLB_ROOT/dlb_01_create_experiment.py" --no-site-config --help

# 关闭当前终端的全局开关。
unset DLB_SITE_CONFIG
```

如需新终端默认启用，可自行将以下一行写入 `~/.bashrc`：

```bash
export DLB_SITE_CONFIG=/home/weizx24/weizx24/DLB_experiments_new/configs/servers/weizx24.json
```

仅设置 `DLB_SITE` 不会自动加载配置；该变量仍可用于显式 `--site-config "$DLB_SITE"`。
显式 `--site-config` 可覆盖全局配置，单个资源/路径参数又可覆盖配置内的对应值。
配置路径错误时明确报错，不会静默回退。
已有实验的 `--experiment-dir` 再生成、`--nml-dir` 预检及 OpenBench 再生成默认使用实验记录，
不受全局开关影响；OpenBench 仍可通过显式 `--site-config` 或 `--mask-root` 更换掩膜路径。

本机默认使用 Slurm `second`、1 节点、144 MPI 进程、100G、48 小时、exclusive，
从作业环境的 PATH 查找 `mpirun`，不固定节点、不加载旧 MPI/Intel 脚本。
例如，使用 `--cores 72 --tasks-per-node 72 --no-exclusive` 可覆盖资源；
需要固定节点时指定 `--node tms13`。节点数由进程数和每节点任务数向上取整。
资源设置来自参考脚本，尚未通过真实调度器提交验证。

三个生成入口均支持 `--site-config`。单独生成 namelist 和 Slurm 的示例：

```bash
python3 "$DLB_ROOT/dlb_02_generate_namelists.py" --site-config "$DLB_SITE" \
  --out-dir /tmp/dlb_mekong_colm --region Mekong --include-dam-init --draft

python3 "$DLB_ROOT/dlb_03_generate_jobs.py" --site-config "$DLB_SITE" \
  --nml-dir /tmp/dlb_mekong_colm --out-dir /tmp/dlb_mekong_jobs --allow-draft

# 正式生成后检查具体实验；草稿会明确拒绝。
python3 "$DLB_ROOT/dlb_04_preflight.py" \
  --nml-dir "$DLB_ROOT/EXP102_local_Mekong/config/colm" --phase spinup \
  --exe /home/weizx24/weizx24/colm/CoLM_0914/run/colm.x \
  --check-netcdf --check-forcing-files
```

本机配置关闭 namelist 生成器附带的旧式 LSF 输出，由作业生成器统一生成 Slurm。
`--lsf` 可显式开启旧式 LSF 输出；它仍保留旧服务器的脚本实现，本机请使用上面的 Slurm 入口。

DLB 基础模板位于 `templates/dlb_base.nml`，参考 Amazon 文件的陆面设置，明确设置
`DEF_TRACER_NUM = 0`。原有区域边界、网格、1996–1999 spinup、2000–2005 模拟及评估规则继续由生成器控制。
USA/SA/Mekong 使用 06min routing，Global 使用 15min；Amazon 0.25° 泥沙实验不会替换现有区域设计。

实验的 `run_manifest.json`、`manifest.yaml` 和 `provenance/site_config.json` 记录配置来源、SHA256 和生效值。
`provenance/input_validation.txt` 记录未解决的问题。预检使用实验记录中的配置值，避免服务器 JSON 后续修改影响已有实验。
源码检查核对 `NAMELIST /nl_colm/` 及其派生类型字段；不预处理所有编译分支，也不能证明已有二进制的编译配置。

本机只读检查结果见 `reports/weizx24_preflight.txt`。当前 CoLM 源码缺少
`DEF_UnitCatchment_regional` 和 `DEF_GridRiverLake_FloodFeedback`，正式生成会失败。
草稿会保留这两个字段并记录问题；不会自动删除字段或改动 CoLM 源码。
`--strict-macros` 可将源码宏不一致提升为失败。

新版继续由 `dlb_08_generate_openbench.py` 独立生成 OpenBench 配置：USA 生成 36 份，其他区域 18 份，
包含成对比较及 spinup、CTL 单独评估。新版的径流参考源 `OpenBench_Streamflow_Daily` 保持不变。

已有实验可分别再生成，原配置会备份，CoLM 再生成继续拒绝已有运行产物的实验：

```bash
python3 "$DLB_ROOT/dlb_02_generate_namelists.py" \
  --experiment-dir /path/to/experiment --overwrite
python3 "$DLB_ROOT/dlb_08_generate_openbench.py" /path/to/experiment --overwrite
```

CoLM 再生成使用实验记录中的模板、本机配置和 MPI/Slurm 资源，无需追加 `--site-config`；
OpenBench 再生成默认读取已记录的掩膜根目录，也可用 `--mask-root` 或 `--site-config` 覆盖。
作业脚本继续使用新版相对路径规则，移动实验目录后可继续预览提交。

本机配置的实验父目录已经指向新仓库；`mask_root` 仍引用旧仓库的
`/home/weizx24/weizx24/DLB_experiments/basin_mask/outputs`，请保留该数据目录。

## 1. 路径和默认值

建议先设置工具目录，后续命令可以直接复制：

```bash
export DLB_ROOT=/share/home/dq134/wzx/DLB_experiments
```

创建器当前默认值：

```text
CoLM 仓库       /share/home/dq134/wzx/CoLM/CoLM202X_1030
case 根目录     /share/home/dq134/wzx/CoLM/cases
实验父目录      /share/home/dq134/wzx/CoLM/DLB_nml
forcing         JRA3Q
评估年份        2001–2005
LSF queue       normal
MPI ranks       240
每节点任务数    48
内存            150G
walltime        24:00:00
```

查看任一工具的完整参数：

```bash
python "$DLB_ROOT/dlb_01_create_experiment.py" --help
python "$DLB_ROOT/dlb_02_generate_namelists.py" --help
python "$DLB_ROOT/dlb_03_generate_jobs.py" --help
python "$DLB_ROOT/dlb_04_preflight.py" --help
python "$DLB_ROOT/dlb_05_restart_handoff.py" --help
python "$DLB_ROOT/dlb_06_stage_status.py" --help
python "$DLB_ROOT/dlb_07_refresh_experiment.py" --help
python "$DLB_ROOT/dlb_08_generate_openbench.py" --help
```

## 2. 创建完整实验

### 2.1 最简命令

三个位置参数依次为实验编号、短名称和区域：

```bash
python "$DLB_ROOT/dlb_01_create_experiment.py" EXP002 control Mekong
```

区域可选：

```text
USA  SA  Mekong  Global
```

短名称只能包含小写字母、数字、点、下划线和连字符，例如：

```text
control
dam_test
jra3q-v2
resolution_0p1
```

默认输出为：

```text
/share/home/dq134/wzx/CoLM/DLB_nml/EXP002_control_Mekong
```

### 2.2 指定完整输出目录

`--output-dir` 表示最终实验目录本身：

```bash
python "$DLB_ROOT/dlb_01_create_experiment.py" EXP002 control Mekong \
  --output-dir /share/home/dq134/wzx/my_runs/mekong_control
```

目标目录必须不存在。创建器会在同级临时目录中完成生成和校验，再原子改名。

### 2.3 只指定实验父目录

`--experiments-root` 表示父目录，最终目录名仍为 `EXPnnn_slug_REGION`：

```bash
python "$DLB_ROOT/dlb_01_create_experiment.py" EXP002 control Mekong \
  --experiments-root /share/home/dq134/wzx/my_runs
```

得到：

```text
/share/home/dq134/wzx/my_runs/EXP002_control_Mekong
```

`--output-dir` 与 `--experiments-root` 不能同时使用。

### 2.4 覆盖服务器默认路径和资源

```bash
python "$DLB_ROOT/dlb_01_create_experiment.py" EXP003 sensitivity USA \
  --repo-root /path/to/CoLM202X \
  --case-root /path/to/cases \
  --forcing-nml JRA3Q \
  --rawdata /path/to/CoLMrawdata \
  --runtime /path/to/CoLMruntime \
  --soil-init /path/to/soilstate.nc \
  --mpi-root /path/to/mpi \
  --intel-env /path/to/intel-env \
  --queue normal \
  --cores 240 \
  --tasks-per-node 48 \
  --memory 150G \
  --walltime 24:00:00 \
  --eval-start-year 2001 \
  --eval-end-year 2005 \
  --output-dir /path/to/EXP003_sensitivity_USA
```

评估年份必须满足：

```text
2000 <= eval-start-year <= eval-end-year <= 2005
```

创建器只生成配置，不创建模型 case、不运行 CoLM/OpenBench，也不提交作业。

## 3. 实验目录结构

```text
EXP002_control_Mekong/
├── README.md
├── manifest.yaml
├── artifacts.yaml
├── checksums.sha256
├── validation_report.txt
├── config/
│   ├── colm/
│   ├── jobs/
│   └── openbench/
├── logs/
├── provenance/
├── summary/
└── openbench_output/
```

进入实验目录后，后续示例都可直接运行：

```bash
export EXP_DIR=/share/home/dq134/wzx/CoLM/DLB_nml/EXP002_control_Mekong
cd "$EXP_DIR"
```

## 4. 单独生成 namelist

通常应使用创建器。只有需要旧式平铺输出或调试配置时，才直接调用 namelist 生成器。

### 4.1 单区域平铺输出

```bash
python "$DLB_ROOT/dlb_02_generate_namelists.py" \
  --repo-root /share/home/dq134/wzx/CoLM/CoLM202X_1030 \
  --out-dir /path/to/generated_namelists \
  --case-root /share/home/dq134/wzx/CoLM/cases \
  --forcing-nml JRA3Q \
  --region USA
```

省略 `--region` 会同时生成 USA、SA、Mekong 和 Global。

### 4.2 生成 schema-v2 模板配置

```bash
python "$DLB_ROOT/dlb_02_generate_namelists.py" \
  --repo-root /share/home/dq134/wzx/CoLM/CoLM202X_1030 \
  --out-dir /path/to/config/colm \
  --case-root /share/home/dq134/wzx/CoLM/cases \
  --forcing-nml JRA3Q \
  --region Mekong \
  --study-id EXP002 \
  --study-slug control \
  --include-dam-init \
  --no-lsf \
  --eval-start-year 2001 \
  --eval-end-year 2005
```

如需检查 routing/reservoir NetCDF 关键变量，在上面的完整命令末尾追加 `--check-netcdf`。如需把源码宏不一致视为失败，追加 `--strict-build`。两个选项可以同时使用。

`--draft` 只用于输入尚未齐备时预览配置；draft 配置不能进入正式运行流程。`--overwrite` 只覆盖生成器负责的同名文件，使用前应先检查输出目录。

### 4.3 重新生成现有实验的 CoLM 配置

以下命令从现有 manifest 恢复路径和资源参数，只原子替换
`config/colm/` 和 `config/jobs/`：

```bash
python "$DLB_ROOT/dlb_02_generate_namelists.py" \
  --experiment-dir "$EXP_DIR" \
  --overwrite
```

原配置会备份到 `provenance/backups/<UTC时间>/`。如果已有 success marker、
history 或 restart 运行产物，生成器会拒绝覆盖，此时应创建新实验编号。

## 5. 生成 LSF 或 Slurm 作业

### 5.1 LSF

创建器默认已经在 `config/jobs/` 中生成 LSF。若需要单独重新生成，建议写入新目录：

```bash
python "$DLB_ROOT/dlb_03_generate_jobs.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --scheduler lsf \
  --exe /share/home/dq134/wzx/CoLM/CoLM202X_1030/run/colm.x \
  --out-dir "$EXP_DIR/config/jobs_lsf" \
  --log-dir "$EXP_DIR/logs/lsf" \
  --queue normal \
  --cores 240 \
  --tasks-per-node 48 \
  --memory 150G \
  --walltime 24:00:00 \
  --launcher mpirun \
  --mpi-root /share/home/dq089/soft/mpich-4.2.3-intel \
  --intel-env /share/home/dq089/soft/intel-env
```

### 5.2 Slurm

将 `YOUR_SLURM_PARTITION` 换成目标集群真实 partition：

```bash
python "$DLB_ROOT/dlb_03_generate_jobs.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --scheduler slurm \
  --exe /share/home/dq134/wzx/CoLM/CoLM202X_1030/run/colm.x \
  --out-dir "$EXP_DIR/config/jobs_slurm" \
  --log-dir "$EXP_DIR/logs/slurm" \
  --queue YOUR_SLURM_PARTITION \
  --cores 240 \
  --tasks-per-node 48 \
  --memory 150G \
  --walltime 24:00:00 \
  --launcher srun
```

若目标文件已存在，生成器默认停止。只有确认需要替换脚本时才追加：

```bash
--overwrite
```

生成内容包括 SPINUP、D_INIT、E0–E7 共 10 个作业和 `submit_batch.sh`。阶段命令为：

```text
SPINUP  mksrfdata -> mkinidata -> colm
D_INIT  mkinidata
E0–E7   colm
```

## 6. 阶段预检

预检只读，不修改 case。

### 6.1 输入数据

```bash
python "$DLB_ROOT/dlb_04_preflight.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --phase inputs
```

### 6.2 SPINUP

```bash
python "$DLB_ROOT/dlb_04_preflight.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --phase spinup \
  --exe /share/home/dq134/wzx/CoLM/CoLM202X_1030/run/colm.x \
  --repo-root /share/home/dq134/wzx/CoLM/CoLM202X_1030 \
  --check-netcdf \
  --check-forcing-files
```

### 6.3 D_INIT、handoff 和正式实验

```bash
python "$DLB_ROOT/dlb_04_preflight.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --phase dam-init \
  --exe /share/home/dq134/wzx/CoLM/CoLM202X_1030/run/colm.x

python "$DLB_ROOT/dlb_04_preflight.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --phase handoff

python "$DLB_ROOT/dlb_04_preflight.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --phase experiments \
  --exe /share/home/dq134/wzx/CoLM/CoLM202X_1030/run/colm.x
```

只检查部分正式实验：

```bash
python "$DLB_ROOT/dlb_04_preflight.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --phase experiments \
  --only E0_CTL,E1_D,E7_ALL \
  --exe /share/home/dq134/wzx/CoLM/CoLM202X_1030/run/colm.x
```

`--strict-macros` 会把源码宏不一致由警告提升为失败。

## 7. 提交 SPINUP

创建器默认生成 LSF，因此以下使用 `config/jobs`。Slurm 时将目录换为 `config/jobs_slurm`，提交助手会自动使用 `sbatch`。

先 dry-run：

```bash
bash "$EXP_DIR/config/jobs/submit_batch.sh" --dry-run --stage spinup
```

确认无误后显式提交：

```bash
bash "$EXP_DIR/config/jobs/submit_batch.sh" --submit --stage spinup
```

`--submit` 会先执行严格 preflight；检查不通过时不会提交。

## 8. 准备和提交 D_INIT

### 8.1 建立 D_INIT landdata 链接

默认只显示计划：

```bash
python "$DLB_ROOT/dlb_05_restart_handoff.py" \
  prepare-dam-init \
  --nml-dir "$EXP_DIR/config/colm"
```

确认源、目标正确后才写入：

```bash
python "$DLB_ROOT/dlb_05_restart_handoff.py" \
  prepare-dam-init \
  --nml-dir "$EXP_DIR/config/colm" \
  --apply
```

该命令会创建 D_INIT case 目录并建立指向 SPINUP landdata 的软链接，不运行模型。

### 8.2 提交 D_INIT

```bash
bash "$EXP_DIR/config/jobs/submit_batch.sh" --dry-run --stage dam-init
bash "$EXP_DIR/config/jobs/submit_batch.sh" --submit --stage dam-init
```

## 9. restart 交接

D_INIT 完成后，先执行 dry-run：

```bash
python "$DLB_ROOT/dlb_05_restart_handoff.py" \
  handoff \
  --nml-dir "$EXP_DIR/config/colm"
```

确认后交接全部实验：

```bash
python "$DLB_ROOT/dlb_05_restart_handoff.py" \
  handoff \
  --nml-dir "$EXP_DIR/config/colm" \
  --apply
```

同时执行 SHA256 校验：

```bash
python "$DLB_ROOT/dlb_05_restart_handoff.py" \
  handoff \
  --nml-dir "$EXP_DIR/config/colm" \
  --hash \
  --apply
```

只处理部分实验：

```bash
python "$DLB_ROOT/dlb_05_restart_handoff.py" \
  handoff \
  --nml-dir "$EXP_DIR/config/colm" \
  --only E0_CTL,E1_D,E7_ALL
```

非 D 组的全部 restart 来自 SPINUP；D 组的非 gridriver restart 来自 SPINUP，完整 gridriver restart 来自 D_INIT。工具拒绝覆盖已有 restart、history 或交接记录。

## 10. 提交正式实验

预览全部 E0–E7：

```bash
bash "$EXP_DIR/config/jobs/submit_batch.sh" --dry-run --stage experiments
```

提交全部实验：

```bash
bash "$EXP_DIR/config/jobs/submit_batch.sh" --submit --stage experiments
```

只预览或提交指定实验：

```bash
bash "$EXP_DIR/config/jobs/submit_batch.sh" \
  --dry-run \
  --stage experiments \
  --only E0_CTL,E1_D,E7_ALL

bash "$EXP_DIR/config/jobs/submit_batch.sh" \
  --submit \
  --stage experiments \
  --only E0_CTL,E1_D,E7_ALL
```

脚本不会自动建立阶段依赖。必须人工确认前一阶段完成后再进入下一阶段。

## 11. 查看阶段状态

文本输出：

```bash
python "$DLB_ROOT/dlb_06_stage_status.py" \
  --nml-dir "$EXP_DIR/config/colm"
```

若作业 marker 在自定义 jobs 目录：

```bash
python "$DLB_ROOT/dlb_06_stage_status.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --jobs-dir "$EXP_DIR/config/jobs_slurm"
```

JSON 输出：

```bash
python "$DLB_ROOT/dlb_06_stage_status.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --json
```

要求 E0–E7 全部完成，否则返回非零退出码：

```bash
python "$DLB_ROOT/dlb_06_stage_status.py" \
  --nml-dir "$EXP_DIR/config/colm" \
  --require-complete
```

## 12. OpenBench

处理组 YAML 比较 E0_CTL 与一个处理组，其中 DLB 组使用
`streamflow_DLB.yaml` 和 `inundation_DLB.yaml`。每个流域还生成 spinup
和 ctl 的单实验评估配置：

```text
streamflow_spinup.yaml  inundation_spinup.yaml
streamflow_ctl.yaml     inundation_ctl.yaml
```

spinup 评估年份为其运行期 1996–1999；ctl 使用创建实验时指定的评估年份。
径流使用 `OpenBench_Streamflow_Daily`，淹没使用 `GIEMS_v2_LowRes`。USA 共生成 36 份
YAML，SA、Mekong 和 Global 各生成 18 份。

检查单个配置：

```bash
openbench check "$EXP_DIR/config/openbench/mekong/streamflow_D.yaml"
```

运行单个配置：

```bash
openbench run "$EXP_DIR/config/openbench/mekong/streamflow_D.yaml"
```

OpenBench 输出路径已经写入 YAML，统一位于实验目录的 `openbench_output/`。

修改 OpenBench 生成代码或评估年份后，可以单独覆盖 YAML，不会改动
CoLM namelist、jobs、case 或已有 OpenBench 输出：

```bash
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" \
  --eval-start-year 2001 \
  --eval-end-year 2005 \
  --overwrite
```

旧 YAML 和覆盖前的 manifest 会备份到 `provenance/backups/<UTC时间>/`。
不加 `--overwrite` 时，如果 `config/openbench/` 已有配置，命令会安全停止。

## 13. 刷新实验记录

只刷新当前文件状态：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR"
```

使用自定义 jobs marker 目录：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR" \
  --jobs-dir "$EXP_DIR/config/jobs_slurm"
```

登记一个 OpenBench 项目成功：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR" \
  --openbench-result EXP002_control_Mekong_mekong_streamflow_D=success
```

状态可为：

```text
success  failed  missing
```

一次登记多个项目时重复参数：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR" \
  --openbench-result EXP002_control_Mekong_mekong_streamflow_D=success \
  --openbench-result EXP002_control_Mekong_mekong_inundation_D=failed
```

所有自动检查通过后标记最终验证完成：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR" \
  --mark-validated
```

状态顺序：

```text
created
-> spinup_complete
-> dam_init_complete
-> handoff_complete
-> colm_complete
-> openbench_complete
-> validated
```

refresh 会更新 `manifest.yaml`、`artifacts.yaml`、`checksums.sha256` 和 `validation_report.txt`，并保留人工填写的 hypothesis 与 conclusion。

## 14. 测试和脚本语法检查

运行完整回归测试：

```bash
python -m unittest -v "$DLB_ROOT/tests/test_experiment_templates.py"
```

检查 Python 语法：

```bash
python -m py_compile "$DLB_ROOT"/dlb_*.py
```

检查生成的 LSF：

```bash
bash -n "$EXP_DIR"/config/jobs/*.lsf \
  "$EXP_DIR/config/jobs/submit_batch.sh"
```

检查生成的 Slurm 脚本：

```bash
bash -n "$EXP_DIR"/config/jobs_slurm/*.slurm \
  "$EXP_DIR/config/jobs_slurm/submit_batch.sh"
```

## 15. 重要安全规则

- 创建器不会创建 case，也不会运行 CoLM/OpenBench 或提交作业。
- restart 工具默认 dry-run；只有 `--apply` 会写入 case。
- 提交助手默认 dry-run；只有 `--submit` 会调用 `bsub` 或 `sbatch`。
- `--output-dir` 与 `--experiments-root` 互斥。
- `--overwrite` 只应用于配置或作业脚本生成，不应用于模型结果。
- 不要在已有 restart/history 的 case 上强行重新交接。
- 不要把 `--draft` 生成的配置用于正式模拟。
