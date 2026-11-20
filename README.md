# CoLM DLB 区域实验工作流

覆盖、备份与纯预览的约定见[统一覆盖规则](OVERWRITE_RULES.md)。

本目录用于创建和管理水库（D）、堤防（L）与分汊（B）的区域全因子实验，包括 CoLM namelist、LSF/Slurm 作业、restart 交接、阶段检查和 OpenBench 配置。

所有工具都不会隐式提交作业。restart 工具默认 dry-run，只有显式使用 `--apply` 才会写入 case；提交助手只有显式使用 `--submit` 才会调用调度器。

## 本服务器使用说明（weizx24 / land01）

本机使用 `integrate/weizx24-site` 分支和 `configs/servers/weizx24.json`。
需要 Python 3.10 或更新版本。以下命令均在本服务器终端执行。

### 1. 进入仓库并打开全局配置开关

```bash
cd /home/weizx24/weizx24/DLB_experiments_new
export DLB_ROOT=/home/weizx24/weizx24/DLB_experiments_new
export DLB_SITE_CONFIG="$DLB_ROOT/configs/servers/weizx24.json"
```

设置一次后，当前终端中创建实验、单独生成 namelist/作业和服务器预检均会自动读取本机配置，
无需在每条命令后添加 `--site-config`。新终端需要重新设置；若希望每次登录自动启用，
可自行将下面两行加入 `~/.bashrc`：

```bash
export DLB_ROOT=/home/weizx24/weizx24/DLB_experiments_new
export DLB_SITE_CONFIG="$DLB_ROOT/configs/servers/weizx24.json"
```

确认当前分支及配置开关：

```bash
git branch --show-current
printf '%s\n' "$DLB_SITE_CONFIG"
```

预期分支为 `integrate/weizx24-site`，配置路径以 `configs/servers/weizx24.json` 结尾。
仅设置 `DLB_SITE` 不会自动加载配置。

### 2. 检查服务器输入与模型兼容性

```bash
python3 "$DLB_ROOT/dlb_04_preflight.py" \
  --check-netcdf --check-forcing-files
```

该命令只读检查全部区域的数据目录、SoilInit、流域掩膜、模型可执行文件权限、源码字段、
routing/reservoir NetCDF 变量，以及 JRA3Q 1996–2005 的 960 个逐月文件名。
文件名检查不验证 NetCDF 内容或时间轴；源码检查也不能证明已有二进制的编译配置。

当前预检仍有两个 CoLM 源码接口阻塞项：

```text
DEF_UnitCatchment_regional
DEF_GridRiverLake_FloodFeedback
```

在解决这两个问题前，预检非零退出和正式生成报错属于预期结果；可先使用下一步的草稿模式。
详细记录见 [本机预检报告](reports/weizx24_preflight.txt) 和 [仓库迁移报告](reports/weizx24_migration.txt)。
`--strict-macros` 可将源码宏不一致提升为失败。

### 3. 生成一套实验草稿

以湄公河为例：

```bash
python3 "$DLB_ROOT/dlb_01_create_experiment.py" EXP103 local Mekong --draft
export EXP_DIR="$DLB_ROOT/EXP103_local_Mekong"
```

三个位置参数分别是实验编号、短名称和区域。区域支持 `USA`、`SA`、`Mekong`、`Global`。
短名称使用小写字母、数字、点、下划线或连字符。目标实验目录必须不存在；重复执行时请换实验编号，
或用 `--output-dir /path/to/new_experiment` 指定新的完整目标目录，并相应更新 `EXP_DIR`。

生成的主要内容：

| 路径 | 内容 |
|---|---|
| `config/colm/` | SPINUP、D_INIT、E0–E7 namelist 和运行 manifest |
| `config/jobs/` | 10 个 Slurm 作业脚本及 `submit_batch.sh` |
| `config/openbench/` | OpenBench 评估配置 |
| `provenance/input_validation.txt` | 尚未解决的输入或源码问题 |
| `provenance/site_config.json` | 配置来源、SHA256 和生效值 |

查看草稿和预览提交命令：

```bash
cat "$EXP_DIR/provenance/input_validation.txt"
less "$EXP_DIR/config/jobs/submit_SPINUP.slurm"
bash "$EXP_DIR/config/jobs/submit_batch.sh" --dry-run --stage spinup
```

`--dry-run` 只打印提交命令。草稿不能用于正式运行，提交助手的正式预检会拒绝草稿。
创建器不创建模型 case、不运行 CoLM/OpenBench，也不提交作业。

### 4. 本机路径、资源及覆盖方法

| 配置项 | 当前值 |
|---|---|
| CoLM 根目录 | `/home/weizx24/weizx24/colm/CoLM_0914` |
| 实验父目录 | `/home/weizx24/weizx24/DLB_experiments_new` |
| 模型 case 根目录 | `/home/weizx24/weizx24/cases` |
| rawdata | `/shr03/CoLMrawdata` |
| runtime | `/shr03/CoLMruntime` |
| SoilInit | `/shr03/CoLMruntime/soilstate.nc` |
| forcing 配置 | CoLM 根目录下 `run/forcing/JRA3Q.nml` |
| forcing 数据 | `/shr03/CoLM_Forcing/JRA3Q/` |
| 流域掩膜 | `/home/weizx24/weizx24/DLB_experiments/basin_mask/outputs` |
| Slurm 资源 | `second`、1 节点、144 进程、100G、48 小时、exclusive |
| MPI 启动 | 作业环境 PATH 中的 `mpirun` |

掩膜仍引用旧仓库的数据目录，请保留该目录。默认不固定计算节点，也不加载旧服务器的 MPI/Intel 脚本。
节点数按进程数和每节点任务数向上取整；资源设置尚未通过真实作业提交验证。

配置优先级为 **显式命令行参数 > 选中的服务器配置 > 原有默认值**。
配置文件选择顺序为 `--site-config` > `DLB_SITE_CONFIG`；两者均未设置时沿用旧服务器默认值。
JSON 中的相对路径以该文件所在目录为基准，命令行及环境变量中的相对路径以当前工作目录为基准。
配置路径错误会明确报错。

例如，生成使用 72 进程、不申请 exclusive 的另一套草稿：

```bash
python3 "$DLB_ROOT/dlb_01_create_experiment.py" EXP104 small Mekong \
  --cores 72 --tasks-per-node 72 --no-exclusive --draft
```

需要固定节点时添加 `--node tms13`。显式切换其他服务器配置时使用 `--site-config /path/to/server.json`。

关闭当前终端的全局开关：

```bash
unset DLB_SITE_CONFIG
```

也可给单条命令添加 `--no-site-config` 临时忽略全局配置。关闭后会恢复旧服务器默认值，
再次运行本机生成命令前可重新启用：

```bash
export DLB_SITE_CONFIG="$DLB_ROOT/configs/servers/weizx24.json"
```

### 5. 单独生成和再生成配置

通常直接使用创建器即可。若只需要 namelist 和 Slurm 草稿，可分别执行；下列目标目录也应尚不存在：

```bash
python3 "$DLB_ROOT/dlb_02_generate_namelists.py" \
  --out-dir /tmp/dlb_mekong_colm --region Mekong --include-dam-init --draft

python3 "$DLB_ROOT/dlb_03_generate_jobs.py" \
  --nml-dir /tmp/dlb_mekong_colm --out-dir /tmp/dlb_mekong_jobs --allow-draft
```

本机由作业生成器统一生成 Slurm，关闭 namelist 生成器附带的旧式 LSF 输出。
`--lsf` 可显式开启旧式 LSF，但它保留旧服务器的脚本实现，本机使用上面的 Slurm 入口。

已有实验可以分别再生成，替换前的配置会备份到 `provenance/backups/`：

```bash
python3 "$DLB_ROOT/dlb_02_generate_namelists.py" \
  --experiment-dir "$EXP_DIR" --overwrite

python3 "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" --overwrite
```

CoLM 再生成拒绝已有运行产物的实验。它使用实验记录中的模板、本机配置和资源，
不受全局开关或服务器 JSON 后续编辑影响。已有实验的 `--nml-dir` 预检以及 OpenBench 再生成
也默认读取实验记录；OpenBench 可显式使用 `--mask-root` 或 `--site-config` 更换掩膜根目录。
作业脚本采用相对路径，移动实验目录后可继续预览提交。

### 6. 兼容性问题解决后正式使用

确认 CoLM 支持所需字段、源码宏与二进制一致后，重新执行第 2 步预检，
再使用新实验编号、不带 `--draft` 生成正式配置：

```bash
python3 "$DLB_ROOT/dlb_01_create_experiment.py" EXP105 production Mekong
export EXP_DIR="$DLB_ROOT/EXP105_production_Mekong"

python3 "$DLB_ROOT/dlb_04_preflight.py" \
  --nml-dir "$EXP_DIR/config/colm" --phase spinup \
  --exe /home/weizx24/weizx24/colm/CoLM_0914/run/colm.x \
  --check-netcdf --check-forcing-files

bash "$EXP_DIR/config/jobs/submit_batch.sh" --dry-run --stage spinup
```

之后按本文后面的 SPINUP → D_INIT → restart 交接 → E0–E7 流程操作。
正式提交需要显式使用 `--submit`；各阶段完成并核验后再进入下一阶段。

本机基础模板 `templates/dlb_base.nml` 明确设置 `DEF_TRACER_NUM = 0`。
保留原有区域边界、网格、1996–1999 spinup、2000–2005 模拟及评估规则：
USA/SA/Mekong 使用 06min routing，Global 使用 15min，参考 Amazon 文件不会将其统一改成 0.25° 泥沙实验。
OpenBench 独立生成器为 USA 生成 120 份 YAML，其他区域各 60 份，包含成对比较、八组实验汇总比较和 spinup、CTL 单独评估，
径流参考源为 `OpenBench_Streamflow_Daily`。

## 1. 通用入口与旧服务器默认值

以下 `/share/...` 路径用于旧服务器示例。本机用户请保留上节设置的 `DLB_ROOT` 和全局开关，
无需执行下面的旧路径 `export`；后续阶段命令可以继续引用本机的 `DLB_ROOT` 与 `EXP_DIR`。

旧服务器工具目录示例：

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
walltime        168:00:00
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
python "$DLB_ROOT/dlb_09_submit_openbench.py" --help
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
  --walltime 168:00:00 \
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

`--draft` 只用于输入尚未齐备时生成配置；draft 配置不能进入正式运行流程。`--overwrite` 的覆盖范围与备份位置见[统一覆盖规则](OVERWRITE_RULES.md)。

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
  --walltime 168:00:00 \
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
  --walltime 168:00:00 \
  --launcher srun
```

若目标文件已存在，生成器默认停止。只有确认需要替换脚本时才追加：

```bash
--overwrite
```

如果为断点续跑等临时需求有意修改了 namelist 的起止时间，重新生成作业时追加：

```bash
--allow-nonstandard-schedule
```

该选项只跳过标准年份范围校验，不跳过 case 名称、过程开关、输出路径等结构检查。
`dlb_06_stage_status.py` 默认允许这类续跑年份，因为它只负责按 manifest
记录的原始实验目标检查完成状态；无需追加该选项。`dlb_07_refresh_experiment.py`
保留同名选项以兼容已有命令。

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

D_INIT 默认生成为 `2000–2000`，也支持将 namelist 配置为起始年在
1996–2000 内、结束年为 2000 的多年度初始化。工作流以 namelist 中的实际年份
为准，不会自动改写 `run_manifest.json` 中的 `period`。

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

`handoff` 默认使用 `--validate-scope selected`，只校验 `--only` 选中实验及其必需依赖。
所有实验都会校验 SPINUP；只有选中启用水库的 `E1_D`、`E4_DL`、`E5_DB` 或
`E7_ALL` 时才会校验 D_INIT 年份和 restart。因此，仅处理 `E0_CTL`、`E2_L`、
`E3_B` 或 `E6_LB` 时，未选中的 D_INIT 临时配置不会阻断交接。

脚本根据 SPINUP 的 `DEF_UnitCatchment_regional` 判断 routing 模式。Regional
routing 会分发 `unitcatchment_regional.nc` 软链接；Global routing 不要求也不会
创建该文件或对应的 `riverlake` 目录。

如需在交接前对 SPINUP、D_INIT 和全部 E0–E7 运行时间配置执行完整严格校验，显式使用：

```bash
python "$DLB_ROOT/dlb_05_restart_handoff.py" \
  handoff \
  --nml-dir "$EXP_DIR/config/colm" \
  --only E0_CTL,E2_L,E3_B \
  --validate-scope all
```

`prepare-dam-init` 始终使用完整校验，`--validate-scope` 仅影响 `handoff`。该选项不会跳过
restart 完整性、GridRiver 特征或目标覆盖保护检查。

非 D 组的全部 restart 来自 SPINUP；D 组的非 gridriver restart 来自 SPINUP，完整 gridriver restart 来自 D_INIT。
即使 D_INIT 运行多年，交接也固定读取并安装 `2000-001-00000` 时间戳的 GridRiver
restart。工具拒绝覆盖已有 restart、history 或交接记录。

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

状态检查会读取 namelist 中的 case 名、输出目录和过程开关来定位结果，但不会要求
续跑时临时修改的起止年份仍为标准范围。整个实验是否完成仍以
`run_manifest.json` 的原始目标为准：评估期月度 history、最终 restart 和对应的
success marker 必须全部存在。配置与科学约束检查仍由生成、作业生成和 preflight
阶段负责。

## 12. OpenBench

配置由 `templates/openbench/` 中的变量 YAML 模板与实验 `manifest.yaml`、
`config/colm/run_manifest.json` 结构化合并生成，需要使用安装了 PyYAML 的 Python
环境（可使用 OpenBench 的虚拟环境），并使用支持 `project.basin_mask` 的 OpenBench
版本。Python 不再保存各变量的指标、评分列表，
也不再把两个淹没变量放进同一个 YAML；科学评估规则和指标公式保持不变。

默认掩膜根目录为本项目的 `basin_mask/`（`--mask-root` 也以这一层为根目录）：
Lower Mississippi 使用 `huc08_mask_output/WBD_HUC2_08_Lower_Mississippi_mask.nc`，
Missouri、Amazon、Mekong 使用 `outputs/basin_masks_06min/<流域>/<流域>_basin.nc`。
已有实验记录的旧本机 `openbench/postprocess/basin_mask/outputs` 根目录，在未显式指定
`--mask-root` 时会自动切换到上述新目录；已有 YAML 需按下方命令重新生成才会更新。

| 模板 | 变量 | 参考数据 | 时间分辨率 |
| --- | --- | --- | --- |
| `streamflow.yaml` | Streamflow | OpenBench_Streamflow_Daily | Day |
| `inundation_fraction.yaml` | Inundation_Fraction | GIEMS_v2_LowRes | Month |
| `inundation_area.yaml` | Inundation_Area | GIEMS_v2_LowRes | Month |

每份 YAML 仅评估一个变量。处理组 `D、L、B、DL、DB、LB、DLB` 分别对应
E1_D 至 E7_ALL，各自与 E0_CTL 成对比较。每个变量还生成 spinup 和 ctl
单实验配置，例如：

```text
config/openbench/mekong/
    streamflow_D.yaml
    inundation_fraction_D.yaml
    inundation_area_D.yaml
    ...
    streamflow_spinup.yaml
    inundation_fraction_spinup.yaml
    inundation_area_spinup.yaml
    streamflow_ctl.yaml
    inundation_fraction_ctl.yaml
    inundation_area_ctl.yaml
    streamflow_comparisons.yaml
    inundation_fraction_comparisons.yaml
    inundation_area_comparisons.yaml
```

spinup 使用 1996–1999，其他配置使用实验指定的评估年份。数量动态计算为
“流域数 × 模板数 × 10 组”：三个模板时 USA 两个流域共 60 份，SA、Mekong、Global
各 30 份。manifest 项目清单同步更新，记录唯一的项目名、配置文件和实际结果目录。

`*_comparisons.yaml` 是每变量的独立八组比较项目，按 E0_CTL、E1_D、E2_L、E3_B、
E4_DL、E5_DB、E6_LB、E7_ALL 顺序包含全部正式实验，不包含 spinup。
项目名以 `_comparisons` 结尾，manifest 的 `treatment` 为 `comparisons`，
结果写入独立的 `openbench_output/<project>`。它使用该变量模板的 reference、
时间分辨率、metrics、scores、statistics 和 comparison.items。

默认 Taylor_Diagram、HeatMap 比较八组模拟；Diff_Plot 沿用 OpenBench 的原生行为，
覆盖八组模拟的 28 个两两组合，实际图数还取决于指标、评分及数据。
该项目由 OpenBench 正常评估后出图，可能重复部分成对项目的评估工作；
它不会自动从已有成对项目目录拼接结果，也不默认使用 `--comparison-only`。

新增 OpenBench 已支持的变量时，在 `templates/openbench/` 新增一个 `.yaml`
文件，或复制现有模板并修改以下字段，无需给生成器增加变量分支：

```yaml
project:
  tim_res: Month
evaluation:
  variables: [Evapotranspiration]
reference:
  Evapotranspiration: GLEAM_v4.2a_LowRes
metrics: [RMSE, correlation]
scores: [Overall_Score]
comparison:
  items: [Taylor_Diagram, HeatMap, Diff_Plot]
statistics:
  enabled: false
  items: []
```

文件名（如 `evapotranspiration.yaml`）决定输出文件和项目名的变量前缀。
每个模板必须只定义一个变量，`reference` 必须对应该变量；模板文件名前缀和变量名
均不得重复。缺失字段、YAML 解析失败、重复字段/变量、输出命名冲突会明确报错。
使用标准 YAML，不使用 Jinja2 或 `!include`；生成结果完整独立，运行时无需模板。
可用 `--template-dir /path/to/templates` 选择另一套模板。

修改某变量的 `metrics`、`scores`、`reference`、`project.tim_res`、
`comparison.items` 或 `statistics` 时，直接编辑对应模板。
生成器填入项目名、输出根目录、年份、BASINS 区域/掩膜和网格、模拟 history 路径；
成对比较和八组汇总比较启用 `comparison`，单实验关闭比较并清空比较项。
CoLM2024、日数据、按月分组和 `_hist_unitcat_` 文件前缀规则保持不变；变量名映射、
单位和时间偏移继续由 OpenBench 的 CoLM2024 profile 负责。
指标适用性、Reference Registry、数据扫描、配置/数据检查、缓存和断点续跑也均由
OpenBench 提供，DLB 只保留非交互式配置生成和既有提交入口。

检查单个配置：

```bash
openbench check "$EXP_DIR/config/openbench/mekong/streamflow_D.yaml"
```

运行单个配置：

```bash
openbench run "$EXP_DIR/config/openbench/mekong/streamflow_D.yaml"
```

OpenBench 在 YAML 的 `project.output_dir` 下追加 `project.name`，实际结果目录为
`openbench_output/<project>`；不要把项目名再次写入 YAML 的输出根目录。

`dlb_01` 创建实验时会自动生成 11 个 OpenBench 提交入口，位于
`config/openbench_jobs/`。对历史实验，有效运行一次 `dlb_09`（仍需指定选择器）
也会生成全部入口，即使该次选择没有待运行项目；显式 --dry-run 不生成入口。

单组入口为 `submit_openbench_D.sh`、`submit_openbench_L.sh`、
`submit_openbench_B.sh`、`submit_openbench_DL.sh`、`submit_openbench_DB.sh`、
`submit_openbench_LB.sh`、`submit_openbench_DLB.sh`，以及
`submit_openbench_ctl.sh`、`submit_openbench_spinup.sh`、
`submit_openbench_comparisons.sh`。每组覆盖该组所有流域和指标。
`submit_openbench_all.sh` 覆盖上述十组，提交一个总数组，统一使用并发上限。

```bash
# 先激活 OpenBench 环境，或传入 --openbench-exe /path/to/openbench
bash "$EXP_DIR/config/openbench_jobs/submit_openbench_D.sh"
bash "$EXP_DIR/config/openbench_jobs/submit_openbench_D.sh" --submit
bash "$EXP_DIR/config/openbench_jobs/submit_openbench_all.sh" --dry-run
bash "$EXP_DIR/config/openbench_jobs/submit_openbench_all.sh" --submit
```

入口默认只生成/复用 LSF 作业并预览，只有 `--submit` 调用 `bsub`；
`--dry-run` 与 `--submit` 不能同时使用。入口固定实验选择，支持传入
`--openbench-exe`、资源参数、`--out-dir`、`--log-dir`、`--retry-failed`、
`--rerun-unverified`、`--overwrite`。`dlb_09` 也可直接使用新增的 `--ctl`、`--spinup`。
入口始终放在实验的 `config/openbench_jobs/`；`--out-dir` 仅改变数组作业及快照位置。
入口不保存生成时的资源选项，运行时使用 `dlb_09` 默认值或传入值；已有数组快照
仍按原规则复用，修改资源需 `--overwrite`。OpenBench 资源独立于 CoLM 配置，
继续使用 LSF，包括 CoLM 使用 Slurm 的实验。

初始化只生成 `.sh`，无需安装或激活 OpenBench。首次运行入口时才检查可执行文件、
生成 `.lsf` 和映射快照；有效运行 `dlb_09` 也会先生成入口，再按所选范围处理数组作业。
内容相同的入口直接复用，内容不同需 `--overwrite`；不覆盖符号链接。
生成入口不会运行 OpenBench，也不会提交 CoLM 作业。全部入口与单组入口是不同提交方式，
同一批任务请选择其中一种，避免重复排队。

批量生成 LSF 独占节点作业时，每个选项直接对应一套实验：`-D`、`-L`、
`-B` 分别选择 E1_D、E2_L、E3_B，`-DL`、`-DB`、`-LB`、`-DLB`
分别选择对应的组合实验。分开的选项表示同时提交多套实验，例如 `-L -B`
同时选择 E2_L 和 E3_B；只有 `-LB` 才选择 E6_LB。

```bash
source .venv/bin/activate

# 默认只生成作业并预览 bsub 命令
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" \
  -L -B \
  --queue normal --cores 2 --slots-per-node 48 --memory 150G --walltime 24:00 \
  --max-concurrent 5

# 确认预览后才实际提交
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" \
  -L -B \
  --submit
```

所有支持 `--overwrite` 的脚本遵循[统一覆盖规则](OVERWRITE_RULES.md)：默认保护已有内容，
覆盖前备份，显式 `--dry-run` 完全不写文件。`dlb_09` 不带 `--dry-run/--submit` 时仅生成作业；
只有 `--submit` 调用调度器。普通提交可复用匹配快照，过期快照需显式授权重新生成。
`dlb_09 --overwrite` 同时授权归档旧结果并重跑所选项目；覆盖提交也需带上该参数。

脚本默认从当前 `PATH` 查找 `openbench`，并将绝对路径写入 LSF 作业，因此计算
节点不需要再次激活虚拟环境。如果不使用 `source` 激活环境，也可以显式传入
`--openbench-exe /path/to/openbench`。

对当前实验目录的**所有 OpenBench 项目**使用 `--all`（不是扫描其他实验目录）：
它选择 manifest 中全部流域、全部变量的成对比较、spinup、ctl 和八组汇总比较项目。
三个模板时单流域共选择 30 个项目，USA 共 60 个；实际提交数量取决于任务状态，
成功且配置未变、正在运行和未经确认的历史结果不会自动进入待运行列表。

```bash
# 设置工具目录和需要评估的实验目录，EXP_DIR 请替换为实际路径
export DLB_ROOT=/share/home/dq134/wzx/DLB_experiments
export EXP_DIR=/share/home/dq134/wzx/CoLM/DLB_nml/EXP000_spinup_Mekong

# 1. 仅生成全部 pending 项目的清单和 LSF 脚本，不提交；纯预览请加 --dry-run
# 先激活 OpenBench 环境；或增加 --openbench-exe /path/to/openbench
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all

# 2. 覆盖重跑预览，不写文件；正式提交须保留相同资源参数及 --overwrite
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite \
  --queue normal --cores 4 --slots-per-node 48 \
  --memory 150G --walltime 24:00 --max-concurrent 20 --dry-run

# 3. 核对后，重新生成覆盖作业并提交
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite --submit

# 4. 仅在人工检查、备份历史结果后，显式允许 unverified 项目重跑
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" \
  --all --overwrite --dry-run
# 再核对清单，确认后提交；保留同一个显式重跑选项
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" \
  --all --overwrite --submit
```

以上步骤按需要选择执行，不必连续执行全部步骤。生成的文件位于
`$EXP_DIR/config/openbench_jobs/`：`openbench_all_projects.tsv`、
`submit_openbench_all_array.lsf` 和 `openbench_all_snapshot.json`。
`--all` 只选择项目；`--overwrite` 授权覆盖重跑，任务启动时归档旧结果。

通常只需两步：先 dry-run，核对清单后直接加 `--submit`；第二步不需要
`--overwrite`。例如提交全部变量的八组比较项目：

```bash
# 第一步：生成并预览；若已有有效快照，直接复用，不改写文件
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --comparisons
# 第二步：直接提交前一步的有效快照
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --comparisons --submit
```

显式 `--dry-run` 可以重复执行，不生成作业、备份或提交入口。
不带 `--dry-run` 可先生成作业文件，再由普通 `--submit` 复用有效快照。
使用 `--overwrite --dry-run` 后，覆盖提交仍需 `--overwrite --submit`，并再次传入资源参数。

只选择全部变量的八组比较项目，可使用以下命令（默认仅生成作业并预览）：

```bash
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --comparisons
# 同时选择 E1_D 成对比较和八组汇总比较
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --comparisons
```

`--comparisons` 在三个模板时每流域选择 3 个项目，USA 选择 6 个。
旧实验需要先重新生成 YAML 和 manifest，才会有这些项目；仅更新代码不会改变旧实验。
使用 `--all` 选择 manifest 中的全部 OpenBench 项目，包含八组汇总比较；
`--all` 不能与 `--comparisons` 或其他实验选择项组合。未指定实验选择时脚本会
停止，不会默认提交全部任务。每个 YAML 是一个 LSF array 元素，默认最多同时运行
20 个、每个评估使用 4 cores，并使用 `span[hosts=1]` 将默认的 48 个 slots 放在同一节点。`normal` 队列
不接受 `-x`，所以这里不是 LSF 的绝对独占：只有当节点在 LSF 中总容量正好为
48 slots 且不允许超配时，申请 48 slots 才会占满该节点，普通作业无法再进入。
如果节点容量不同，应通过 `--slots-per-node` 指定实际值；管理员特权作业或不消耗
这些 slots 的特殊任务仍可能共享节点。节点不足时由 LSF 排队。

普通模式会提示输出目录非空并跳过，同时提示可用 `--overwrite` 重跑。
`dlb_09 --overwrite` 会重新生成作业并允许重算所选项目，包括成功、失败和状态不明的旧结果；
有 `.dlb_openbench_running` 标记的项目仍跳过。预览不会移动结果或执行 OpenBench。
正式任务启动时先核对目录身份并获取项目锁，将旧目录归档到输出根目录的
`.dlb_openbench_backups/<project>.<UTC时间>.<唯一标识>/`，然后创建干净目录重新评估。
排队期间目录身份发生变化的任务会跳过，重复执行同一快照也不会再次覆盖新结果。

```bash
# 先预览；会显示 OVERWRITE non-empty output directory 和待运行数量
python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite --dry-run
# 正式提交时也必须带 --overwrite；按本次目录状态和资源参数重新生成覆盖作业
python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite --submit
```

覆盖作业使用 `_overwrite` 名称及唯一 mapping 路径，与普通作业分开。
`--retry-failed --overwrite` 仍只筛选确认失败的项目，`--overwrite` 用于更新该重试快照。
重跑失败项目时使用 `--retry-failed`，可与 `--all` 或 `-D`、`-DL` 等选择器组合：

```bash
# 仅筛选失败项目，生成重试快照并预览；不移动已有结果、不提交任务
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --retry-failed

# 确认后提交预览时生成的快照
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --retry-failed --submit

# 后续另一轮重试，或需要调整资源时，重新生成快照
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -DL --retry-failed --overwrite
```

失败依据为输出目录内的 `.openbench_failed`、`failed.marker`，或者 `run.log`
最终摘要 `All phases complete: ... N errors`（N > 0）；没有最终摘要时，也识别
`Evaluation phase: ... N failed`（N > 0）。日志中的普通 ERROR、WARNING 不单独作为
重试依据。旧脚本可能在 OpenBench 内部失败但退出码为 0 时写入成功标记，因此明确的
失败摘要优先于成功标记；新版生成的普通作业和重试作业均检查这一情况。

在 `--retry-failed` 模式下，有 `.dlb_openbench_running` 的项目总是跳过；成功、状态不明、其他标记冲突、符号链接
输出目录也跳过。manifest 状态仅用于显示，单凭 manifest 的 failed 不会触发重试。
残留 running 标记需在人工确认任务已停止后处理，脚本不自动判断调度器任务是否存活。

每个重试任务实际启动后会获取项目级锁，并再次核对失败状态与快照中的目录身份。
确认可重试后，将原输出目录完整移动到其同级目录
`.dlb_openbench_backups/<project>.<UTC时间>.<唯一标识>/`，再创建干净输出目录，依次运行
`openbench check` 和 `openbench run`。不会复用旧 NaN 预处理文件，也不删除原结果；
没有足够权限完成备份时任务失败停止。任务中断后可能留下 running 标记供人工核查。

重试的 LSF 脚本、日志和清单使用 `_retry_failed` 名称，与第一轮普通作业分开。
每次生成的重试 mapping 路径均唯一，`--overwrite` 不修改已生成的旧 mapping，避免
排队中的作业读取新清单。重复执行同一快照不会再次重跑已替换输出目录的项目；
对再次失败的项目需用 `--overwrite` 生成新快照。不要删除仍被排队作业引用的清单。
计算节点必须能读取脚本中记录的提交工具路径及 Python 解释器路径。

如需重算成功或状态不明的项目，使用上述 `--overwrite` 模式，旧目录在任务启动时自动归档。
任务状态由 `dlb_openbench_state.py` 统一判定，提交入口和刷新脚本使用相同规则：

| 状态 | 判断依据 | 默认处理 |
| --- | --- | --- |
| `not_run` | 没有运行记录或结果 | pending |
| `completed_unchanged` | 可信成功记录与当前 YAML SHA256 一致 | 跳过，manifest status 为 success |
| `config_changed` | 有成功记录，但 YAML SHA256 已变化 | pending |
| `failed` | 最近一次可信记录失败，或 running 记录已无锁持有者 | pending，保留部分结果 |
| `running` | 项目执行锁被持有 | 跳过重复任务 |
| `unverified` | 存在旧结果，但无有效可信记录（含损坏的记录） | 阻止自动运行并提示 |

运行记录位于 `openbench_output/<project>/.dlb_openbench_run.json`，含项目、配置路径、
本次 YAML 的 SHA256、状态和时间；只有 `check`、`run` 均返回 0，且结束时配置
仍一致，才原子写入 success 记录。此记录只用于 DLB 任务识别，不是 OpenBench 缓存。
旧 `.openbench_success`、`success.marker` 和 manifest 中的历史 success 不会被提升为
可信成功。旧记录路径与当前配置路径不符也视为 unverified。

**结果目录没有单独的 success/failed 文件是正常的。** 当前执行器不再创建
`.openbench_success`、`.openbench_failed` 或 `success.marker`，而是统一更新
`.dlb_openbench_run.json`。该文件以 `.` 开头，普通 `ls` 不显示，需用 `ls -a`。

```bash
# PROJECT 替换为需要查看的完整项目名称
PROJECT=EXP000_spinup_SA_amazon_inundation_area_D
ls -la "$EXP_DIR/openbench_output/$PROJECT"
python -m json.tool \
  "$EXP_DIR/openbench_output/$PROJECT/.dlb_openbench_run.json"

# 根据当前 YAML、运行记录和执行锁，刷新 manifest 中的状态
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR"
```

JSON 的 `status` 表示最近一次执行记录：`running` 为已开始、尚未登记结束，
`success` 为 check/run 均成功且结束时配置未变，`failed` 为执行失败；
成功时有 `success_at`，失败时有 `error`。时间字段采用 UTC（`+00:00`），
北京时间需加 8 小时。记录中的 `running` 本身不能证明任务仍在运行：
刷新时会结合文件锁判断，无锁的遗留 running 记录按中断失败处理。
即使 JSON 写着 success，当前 YAML 已变化时，刷新也会将任务识别为 config_changed。

`Job <作业号> is submitted` 只表示 LSF 接受了提交，不表示评估已开始或成功。
尚在排队、或在执行器写入记录前就失败的任务，可能还没有新的运行记录。
此时检查 LSF 状态及 `$EXP_DIR/logs/openbench/` 中对应作业号和数组索引的
`openbench_all.<作业号>.<索引>.out`、`.err`（其他选择器使用相应的文件名前缀）。
旧结果和旧 marker 会保留，但不会自动成为当前配置的可信成功记录。

作业启动时重新校验 mapping 和 YAML 的 SHA256；排队期间修改 YAML 或覆盖了
mapping，旧任务会拒绝执行，提示重新生成作业。运行过程中检测到配置变化会记录
failed，不能登记为当前配置成功。每项目在输出根目录的 `.dlb_openbench_locks/`
持有 `flock` 锁，避免不同选择器或不同作业重复运行；锁由操作系统在退出时释放，
运行记录无锁但仍为 running 时按中断失败重试，不需要人工删除锁文件。
计算节点必须能访问生成作业时的 Python 环境、DLB 执行器和支持跨节点 flock 的共享文件系统。

对于状态不明的历史结果，使用 `--overwrite` 显式允许归档后重跑；
`--rerun-unverified` 为旧接口保留参数，当前提交器的覆盖授权以 `--overwrite` 为准。
目录非空时默认输出如下：

```text
SKIP existing result (non-empty output; use --overwrite to rerun): ...
No jobs to generate or submit.
```

如果仍显示 18 个项目、使用合并的 `inundation_*.yaml`，说明该单流域实验仍使用
旧版配置和 manifest；当前六个变量模板加八组汇总比较应为每流域 60 个项目。
另外检查 YAML 的 `project.output_dir`：应为实验的 `openbench_output` 根目录，
不要包含项目名，否则 OpenBench 再追加项目名后会出现
`openbench_output/<project>/<project>`。更正路径不会搬移或删除原目录中的结果。

准备将旧实验迁移到当前模板并重新评估时，先确认 `EXP_DIR`、人工检查和备份旧结果，
再按以下步骤操作（使用已安装 PyYAML 和 OpenBench 的 Python 环境）：

```bash
# 1. 更新实验 YAML 和 manifest；原配置自动备份，不删除旧评估结果
# 这一步仅在需要迁移旧配置时执行；已手动定制的 YAML 应先备份，避免定制被模板替换
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" --overwrite

# 2. 显式允许历史结果重跑，重新生成全部 pending 项目的作业，只预览、不提交
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" \
  --all --overwrite --dry-run

# 3. 核对项目数量、输出路径及清单后，按当前状态重新生成覆盖作业并提交
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" \
  --all --overwrite --submit
```

若不需要迁移模板、现有 YAML 的输出路径也正确，可直接从第 2 步开始。
`--overwrite` 会重跑所选的已有结果，跳过有 running 标记的项目；不向 OpenBench 添加 `--force`。

修改变量模板或评估年份后，可以单独重新生成 YAML，不会改动
CoLM namelist、jobs、case 或已有 OpenBench 输出：

```bash
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" \
  --eval-start-year 2001 \
  --eval-end-year 2005 \
  --overwrite
```

旧 YAML 和覆盖前的 manifest 会备份到 `provenance/backups/<UTC时间>/`。
不指定 `--templates` 且不加 `--overwrite` 时，如果 `config/openbench/` 已有配置，命令会安全停止。
先在临时目录生成全部 YAML、检查命名冲突，再原子替换配置目录和 manifest；
替换失败会恢复原配置。旧版合并的 `inundation_*.yaml` 会随旧配置目录一起备份，
新版使用 `inundation_fraction_*.yaml` 和 `inundation_area_*.yaml`。

**重新生成 YAML 不代表已有结果已按新配置重新评估。** 目录非空时默认跳过；
使用 `dlb_09 --overwrite` 授权归档旧目录并重新评估。
修改模板后先重生成 YAML，或直接修改实验 YAML，然后重生成作业快照：

```bash
# 只生成清单和作业，预览待运行状态；不提交
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite
# 核对之后才正式提交（只有 --submit 调用 bsub；覆盖提交仍需 --overwrite）
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite --submit
```

`dlb_09 --overwrite` 替换作业文件并授权重跑，旧结果先归档，再使用干净目录运行。
覆盖提交按当前资源参数重新生成快照。所有新执行仍正常调用
`openbench check` 和 `openbench run`，不默认附加 `--force`。
需要强制重算或特殊续跑时，人工备份后直接使用 OpenBench 的 `--force`、`--resume`
（以所用版本帮助为准）；直接运行 OpenBench 不会创建 DLB 可信运行记录。

SHA256 针对生成后 YAML 的原始字节，因此注释/排版变更也会触发 config_changed。
它不跟踪外部数据、Registry、模型 profile 或 OpenBench 软件版本变化；这些变化
是否需要重算由 OpenBench 自身机制及用户判断。没有实现第二套评估缓存。

## 13. 刷新实验记录

`dlb_08` 生成配置后会自动调用 `dlb_07` 刷新，其中的 CoLM 状态检查由 `dlb_06` 执行。
三个脚本均支持 `--workers N`，默认 4 路；`--workers 1` 使用串行路径。
CoLM 状态检查使用独立进程，文件检查及哈希使用线程；不同阶段依次执行。
进度与阶段耗时实时写入 stderr，`dlb_06 --json` 的 stdout 仍可直接解析。

```bash
# 默认 4 路；如需调整并发数量，参数会沿刷新链路传递
python3 "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" --overwrite --workers 8
# 直接刷新，或用 1 路降低共享存储压力
python3 "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR" --workers 1
```

`--workers` 控制配置管理和状态检查，不修改 YAML 的 OpenBench 评估核心数，
也不修改 `dlb_09 --cores`。YAML 生成、备份及配置替换仍按原事务流程串行执行。
提高并发数的效果取决于共享存储性能，可根据阶段计时调整；不保证同比提速。


只刷新当前文件状态：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR"
```

使用自定义 jobs marker 目录：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR" \
  --jobs-dir "$EXP_DIR/config/jobs_slurm"
```

### 按模板新增或覆盖评估配置

`dlb_08` 的 `--templates` 接受一个或多个模板名或文件名；来源目录默认是
`templates/openbench/`，也可通过 `--template-dir` 指定其他目录。不能在模板名中传入目录路径。

```bash
# 预览：只新增三份水库模板所缺少的配置；已有文件跳过
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" \
  --templates dam_inflow dam_outflow dam_storage --dry-run

# 执行新增，保留已有文件（包括已转换的 stn 配置）
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" \
  --templates dam_inflow dam_outflow dam_storage

# 预览：仅重新生成储量模板对应的配置
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" \
  --templates dam_storage.yaml --overwrite --dry-run

# 执行：新增缺失的储量配置，并覆盖已有储量配置；其他模板配置保留
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" \
  --templates dam_storage --overwrite
```

| 参数 | 已有目标 YAML | 其他模板生成的 YAML |
| --- | --- | --- |
| `--templates ...` | 跳过，只补齐缺失文件 | 保留 |
| `--templates ... --overwrite` | 覆盖，并新增缺失文件 | 保留 |
| 不指定 `--templates`，加 `--overwrite` | 按来源目录全部模板重新生成 | 整体替换旧目录，沿用旧行为 |

每个被选模板仍覆盖当前区域的全部流域及十种实验组合；这里不提供按单一流域或处理选择。
输出逐文件的 `ADD`、`OVERWRITE`、`SKIP` 清单和数量。`--dry-run` 不写配置、manifest 或备份，
也不执行刷新；目标全部跳过时同样不更新时间戳或创建备份。

指定模板时，manifest 中未修改的项目登记保留，新项目追加，覆盖项目在原位置更新。
配置内容完全相同则保留原状态，变化后重置状态，正常刷新入口仍会依据实际运行记录重新计算状态。
原始评估默认年份和掩膜根目录等公共元数据保留；显式传入的新年份、输出目录或掩膜参数
只作用于本次实际生成的 YAML。`--no-refresh` 是内部参数，不建议普通操作跳过刷新。

已有 YAML 无项目登记、项目名与路径/输出目录冲突、或未选中的登记配置丢失时会报错，
不会自动认领文件或删除记录。选中模板的缺失文件则允许重新补齐。
有实际改动时，原配置目录和 manifest 备份到 `provenance/backups/<UTC时间>/`；
通过临时目录完成更新，配置替换或 manifest 写入失败会恢复原配置。
刷新失败会明确报告，但已提交的配置更新仍保留，处理刷新错误后可单独运行 `dlb_07`。

**水库配置注意：** 不加 `--overwrite` 会保留已转为站点类型的 YAML。
覆盖对应模板会恢复为模板生成的网格配置，需要再次运行水库站点转换；未选中的水库模板不受影响。
评估结果、外部站点数据快照和已有作业文件不被删除。配置更新后，应先完成所需数据准备，
再用 `dlb_09 --overwrite` 预览覆盖作业，并用 `--overwrite --submit` 提交重新评估。

### 水库入流、出流和储量评估

`templates/openbench/` 新增三个独立日尺度模板，`dlb_08` 会自动发现，无需修改生成器：

| 模板 | OpenBench 变量 | CoLM profile 候选变量 | metrics | scores |
| --- | --- | --- | --- | --- |
| `dam_inflow.yaml` | `Dam_Inflow` | `qresv_in` | `KGE, correlation, rSD, percent_bias` | 七项评分（见下文） |
| `dam_outflow.yaml` | `Dam_Outflow` | `qresv_out` | `KGE, NSE, percent_bias` | 七项评分（见下文） |
| `dam_storage.yaml` | `Dam_Water_Storage` | `volresv` | `RMSE, NRMSE` | 七项评分（见下文） |

当前三份模板均启用 `Overall_Score`、`nBiasScore`、`nRMSEScore`、`nPhaseScore`、
`nIavScore`、`nSpatialScore`、`nSeasonalityScore`。可自行修改模板中的评分列表，
再按需选择覆盖；新增模式不会把模板修改写入已经存在的 YAML。

参考源均为 `ResOpsUS`。加上原有三个模板，每流域现在生成 60 个项目（六份模板乘十种实验组合），
USA 两流域生成 120 个。`--all` 会包含新增的水库项目；当前没有按模板名筛选的提交参数。

**运行前限制：** 实际检查过
`EXP000_spinup_SA_D_0p1_hist_unitcat_2005-08.nc` 的元数据，三个变量均为
`(time, reservoir)`，入流/出流单位 `m^3/s`，储量单位 `m^3`。
现有生成器写入的模拟类型是 `grid`；变量名称 fallback 并不能把 reservoir 维度转换成网格。
针对这种输出，先使用下文的 `dlb_prepare_reservoir_evaluation.py` 按
`resv_GRAND_ID` 精确提取成站点时序，再执行评估；无需将水库向量插值到网格，也不改变 `dlb_08`。
CTL 或未启用水库的实验是否提供有效水库输出也需单独确认，不能将缺测、填充值或不存在的水库当作零值。

#### 准备 ResOpsUS 观测注册

本地参考目录为 `/share/home/dq013/zhwei/OpenBench/Reference/Station/Anth/Dam/ResOpsUS`。
抽查 `ResOpsUS_41.nc` 的元数据，观测变量为 `inflow/outflow/storage`，对应单位是
`m3 s-1/m3 s-1/million cubic meters`。本地 OpenBench 支持将 `m3` 转为统一的 `mcm`；
因此储量 RMSE 的结果单位应以评估产物属性为准，不能默认是 m³。原始储量和储量距平不能混用。

内置 ResOpsUS 注册的目录和 1908–1957 年范围不适用于此本地数据。辅助脚本只读 CSV 元数据，
将两种分辨率的清单生成独立副本，将 `DIR` 改成真实观测文件的绝对路径，并注册到当前用户的
`~/.openbench/references/dlb_resopsus.yaml`。不会改动原观测、原清单或 OpenBench 内置注册表。
目录创建、清单副本和注册写入都需要 `--apply`：

```bash
# 使用已安装 PyYAML 的现代 Python 环境；应在将运行评估的账号下执行
export DLB_ROOT=/share/home/dq134/wzx/DLB_experiments

# 只预览，检查两个清单和观测文件是否存在
python "$DLB_ROOT/dlb_prepare_resopsus.py"

# 生成清单副本，并写入当前账号的用户注册目录
python "$DLB_ROOT/dlb_prepare_resopsus.py" --apply

# 日后需要更新注册时，备份旧注册并生成新的清单快照
python "$DLB_ROOT/dlb_prepare_resopsus.py" --apply --overwrite
```

此处提供的原始清单存在同 ID 不同匹配位置的歧义：06min 的 `137, 557, 726, 1781`，
15min 的 `557, 726`。脚本默认拒绝准备，避免替你选择位置。可以先人工整理一份独立来源清单，
通过 `--source-dir` 指定；也可以明确排除每个清单内这些 ID 的**全部记录**（不会选择其中一行）：

```bash
python "$DLB_ROOT/dlb_prepare_resopsus.py" --exclude-duplicate-ids
python "$DLB_ROOT/dlb_prepare_resopsus.py" --exclude-duplicate-ids --apply
```

排除仅作用于新清单副本，原始文件保留。两个分辨率独立排除，各自的有效 ID 集合可能不同。

两种清单都准备好后，OpenBench 原生 ResOpsUS filter 按模拟分辨率自动选择：
0.1° 对应 `06min`，0.25° 对应 `15min`。各站点保留自己的起止年份，注册总年份仅表示启用站点的覆盖包络，
不保证每个变量在整个区间都有有效观测。已有 OpenBench 进程需重启以加载注册。
若设置 `OPENBENCH_HOME`，默认注册目录改为 `$OPENBENCH_HOME/.openbench`；准备和评估必须使用一致的环境。
`--user-dir` 用于显式指定配置目录（例如隔离验证）；普通评估仍须保证 OpenBench 实际加载同一个目录。
其他用户注册文件也可能覆盖 ResOpsUS，应检查最终解析结果。不要在正在运行的评估期间切换该注册源。

此本地 `06min` 清单共 167 座水库，`15min` 共 142 座，均没有 Amazon 范围内站点。
ResOpsUS filter 会按经纬度范围和年份筛选，没有匹配时明确报 `No ResOpsUS stations selected`。
站点清单匹配坐标本身不等于已经验证了模拟水库 ID；仍需核对实际水库和流域归属。

#### 生成配置和作业

先生成配置，然后完成水库 ID 匹配及站点数据准备，再生成作业：

```bash
export EXP_DIR=/实际的实验目录

# 重新生成全部模板配置；备份旧配置，但会替换手工定制的实验 YAML
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" --overwrite

# 此处先按下节完成 dam_inflow/outflow/storage 的站点转换

# 生成作业快照并预览；-D 会选择所有模板的 D 项目
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --overwrite

# 核对清单、数据维度和观测覆盖后，重新生成覆盖作业并提交
python "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --overwrite --submit
```

当前 `dlb_09` 默认提示并跳过非空输出目录；加 `--overwrite` 后允许归档旧目录并重新评估。
生成配置、注册数据源和作业 dry-run 都不代表科学评估已成功。

#### 将 `(time, reservoir)` 转为站点时序

`dlb_prepare_reservoir_evaluation.py` 接受**一份已经生成的水库评估 YAML**。
它读取该配置的历史目录和文件前缀，对每个月的 `resv_GRAND_ID(reservoir)` 与
ResOpsUS 清单 `ID` 做精确匹配，允许每个月水库列顺序不同，不使用数组下标作为水库 ID，
也不使用坐标最近邻。仅保留区域、年份范围内，且在所有选定模拟的所有月份中均存在的观测 ID。
其他未匹配观测不参与该项目，最终 ID 和来源文件记录在 `matching.json`。

```bash
# 指向参考准备脚本生成的副本，不能使用包含重复 ID 的原始清单
export RESOPSUS_LIST=/准备脚本打印的清单目录/ResOpsUS_reservoir_matched_06min.csv

# 示例为 USA 的 Missouri 流域、E1_D 入流；默认只检查元数据，不读取流量/储量数组、不写文件
python "$DLB_ROOT/dlb_prepare_reservoir_evaluation.py" \
  --config "$EXP_DIR/config/openbench/missouri/dam_inflow_D.yaml" \
  --station-list "$RESOPSUS_LIST" \
  --out-dir "$EXP_DIR/config/reservoir_station_data" \
  --simulation E1_D

# 核对匹配 ID 后，添加 --apply 才提取时序并替换这一份 YAML
python "$DLB_ROOT/dlb_prepare_reservoir_evaluation.py" \
  --config "$EXP_DIR/config/openbench/missouri/dam_inflow_D.yaml" \
  --station-list "$RESOPSUS_LIST" \
  --out-dir "$EXP_DIR/config/reservoir_station_data" \
  --simulation E1_D --apply
```

出流和储量分别将 `--config` 改为 `dam_outflow_D.yaml`、`dam_storage_D.yaml`；
其他流域/处理使用实际 YAML 路径和配置中的模拟标签。Global 必须使用 15min 清单。

不加 `--simulation` 时保留并检查配置中的全部模拟。CTL 若没有水库输出会明确失败，
**不会自动将 CTL 删除或填零**；只有显式指定 `--simulation E1_D` 才会将此配置改为 E1_D 单独评估，
因此上例不再包含与 CTL 的两两比较。若多个实验都有有效水库输出，可重复指定
`--simulation` 保留所需实验，使用各实验共同匹配的 ID。仅保留一个模拟时自动关闭跨模拟比较图。

`--apply` 将选中变量写成每站一个 `time` 维度 NetCDF，生成对应模拟站点 CSV，再将 YAML 的
模拟类型改为 `stn`，显式指定变量名、单位和站点清单。时序保留原始时间坐标，不猜测平移；
抽查的示例是每日中午时间戳，对应日窗口，最终与观测的日尺度对齐交由 OpenBench 处理。
CF 缺测值由 xarray 解码；另将 CoLM 未标注的 `-1e36` 转为 NaN，可用 `--missing-value` 指定其他哨兵。
重复时间、非单调时间、无效/重复 GRanD ID、缺月、未知单位或全缺测站点会停止处理。

每次写入创建独立数据快照，包含 `original.yaml` 备份、`prepared.yaml`、匹配审计及来源文件信息；
只有提取全部成功后才原子替换原 YAML。清单路径作为该 YAML 的参考源 override 固定，
不依赖运行账号重新注册 ResOpsUS。快照和观测目录必须对实际运行账号可读。
快照目录必须在 `config/openbench` 外，以免被后续生成配置时删除。
不支持对已转换的站点 YAML 再次转换；需要更新源数据时，从原模板重新生成 YAML 或恢复备份后再准备。
重新运行 `dlb_08 --overwrite` 会恢复网格配置，因此必须再次执行此准备步骤；
之后使用 `dlb_09 --overwrite` 更新作业快照，避免继续引用旧配置。

该脚本不会增加观测覆盖：Amazon 没有对应 ResOpsUS 站点时直接停止，不能通过技术转换得到观测评估结果。

#### 指标解释与尚未实现的诊断

- 原始 KGE 使用相关系数 r、标准差比 α 和均值比 β。`correlation` 对应 r，`rSD` 对应 α；
  β 未独立输出，可按同一时间范围和有效样本下的 `1 + percent_bias / 100` 推导。
  此处 PBIAS 正值表示模拟高估；观测均值/方差为零时，相关比值指标可能为 NaN。
- `NRMSE` 是 `RMSE / abs(mean(observation))`，不是按容量或极差归一化，也不是 `nRMSEScore`。
- `nSeasonalityScore` 比较多年平均月循环幅度，`nPhaseScore` 比较其峰值月份，均需要完整的 12 个月循环。
  它们不是洪水事件峰现时间差；完整蓄放水过程还需要逐月气候态曲线。
- 洪峰削减率、事件峰现时间差和水量平衡残差尚未实现，需要联合读取入流、出流和储量。
  `APFB` 是模拟与观测年最大流量偏差，不能替代削峰率；水量平衡还需核对储量采样时刻、流量平均时段，
  以及库面降水、蒸发、取水等额外项，不能将简化残差直接解释为全部守恒误差。

核实已有可信成功记录后，手动登记一个 OpenBench 项目成功：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR" \
  --openbench-result EXP002_control_Mekong_mekong_streamflow_D=success
```

人工 `success` 只有在成功记录与当前 YAML 一致时才接受；不能用于确认旧 marker。
`failed`、`missing` 可降低本次刷新报告状态，另保留 `manual_status` 注记，
不伪造运行记录。后续刷新仍重新检查运行证据。所有项目都具备匹配成功记录才可能
进入 `openbench_complete`；每项目的 `task_state` 和汇总计数区分六种状态。

状态可为：

```text
success  failed  missing
```

一次登记多个项目时重复参数：

```bash
python "$DLB_ROOT/dlb_07_refresh_experiment.py" "$EXP_DIR" \
  --openbench-result EXP002_control_Mekong_mekong_streamflow_D=success \
  --openbench-result EXP002_control_Mekong_mekong_inundation_fraction_D=failed
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
- 显式 `--dry-run` 不写文件；`dlb_09` 默认只生成作业；只有 `--submit` 调用调度器。
- `--output-dir` 与 `--experiments-root` 互斥。
- `dlb_09 --overwrite` 允许覆盖重跑 OpenBench 结果（任务启动时先归档）；不覆盖 CoLM 模型结果。
- 不要在已有 restart/history 的 case 上强行重新交接。
- 不要把 `--draft` 生成的配置用于正式模拟。
