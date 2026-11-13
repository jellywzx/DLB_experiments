# CoLM DLB 区域实验工作流

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
OpenBench 独立生成器为 USA 生成 60 份 YAML，其他区域各 30 份，包含成对比较、八组实验汇总比较和 spinup、CTL 单独评估，
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

`--submit` 会直接复用前一次 dry-run 生成的作业快照。只有需要修改 queue、cores、
slots、memory、walltime、并发数或 OpenBench 路径并重新生成作业时，才使用
`--overwrite --submit`；`--overwrite` 始终只替换作业清单和 LSF 脚本。

脚本默认从当前 `PATH` 查找 `openbench`，并将绝对路径写入 LSF 作业，因此计算
节点不需要再次激活虚拟环境。如果不使用 `source` 激活环境，也可以显式传入
`--openbench-exe /path/to/openbench`。

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

脚本会自动跳过已有非空输出目录的项目，不覆盖成功、失败或部分结果。这个检查
会在生成任务清单和作业真正启动时各执行一次，以覆盖排队期间结果发生变化的情况。
如需重新计算，应先人工备份或移走对应的 `openbench_output/<project>`，脚本不会
自动删除任何评估结果。

修改变量模板或评估年份后，可以单独重新生成 YAML，不会改动
CoLM namelist、jobs、case 或已有 OpenBench 输出：

```bash
python "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" \
  --eval-start-year 2001 \
  --eval-end-year 2005 \
  --overwrite
```

旧 YAML 和覆盖前的 manifest 会备份到 `provenance/backups/<UTC时间>/`。
不加 `--overwrite` 时，如果 `config/openbench/` 已有配置，命令会安全停止。
先在临时目录生成全部 YAML、检查命名冲突，再原子替换配置目录和 manifest；
替换失败会恢复原配置。旧版合并的 `inundation_*.yaml` 会随旧配置目录一起备份，
新版使用 `inundation_fraction_*.yaml` 和 `inundation_area_*.yaml`。

**重新生成 YAML 不代表已有结果已按新配置重新评估。** DLB 提交入口仍跳过所有
非空结果目录；`--overwrite` 只重生成配置，不能绕过该保护。旧结果的 success marker
和 manifest 状态也不能证明已使用新模板完成评估。

需要重算时，先人工备份相关结果，然后检查新配置并直接使用 OpenBench 的 `--force`
绕过其增量缓存（以下是操作示例，不由生成器自动执行）：

```bash
CONFIG="$EXP_DIR/config/openbench/mekong/streamflow_D.yaml"
openbench check "$CONFIG"
# 人工备份该项目结果后，在合适计算资源上执行
openbench run "$CONFIG" --force
```

若要保留原目录中的结果，可使用 OpenBench 自带的输出根目录覆盖选项，将新结果写到
另一个目录：`openbench run "$CONFIG" --output-dir /path/to/new-output --force`。
若继续走 DLB LSF 入口，应先人工移走并保留旧结果，再重新生成作业清单；旧作业快照
需要用提交脚本的 `--overwrite` 更新。DLB 不自动删除结果，也不维护另一套缓存或
配置指纹。普通续跑复用 OpenBench 的缓存及所用版本支持的 `--resume`；以
`openbench run --help` 为准，修改评估配置后需要重算时使用 `--force`。

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
- 提交助手默认 dry-run；只有 `--submit` 会调用 `bsub` 或 `sbatch`。
- `--output-dir` 与 `--experiments-root` 互斥。
- `--overwrite` 只应用于配置或作业脚本生成，不应用于模型结果。
- 不要在已有 restart/history 的 case 上强行重新交接。
- 不要把 `--draft` 生成的配置用于正式模拟。
