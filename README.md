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
OpenBench 独立生成器为 USA 生成 36 份 YAML，其他区域各 18 份，包含成对比较和 spinup、CTL 单独评估，
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

`handoff` 默认使用 `--validate-scope selected`，只校验 `--only` 选中实验及其必需依赖。
所有实验都会校验 SPINUP；只有选中启用水库的 `E1_D`、`E4_DL`、`E5_DB` 或
`E7_ALL` 时才会校验 D_INIT 年份和 restart。因此，仅处理 `E0_CTL`、`E2_L`、
`E3_B` 或 `E6_LB` 时，未选中的 D_INIT 临时配置不会阻断交接。

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
