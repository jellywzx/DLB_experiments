# CoLM AAS DLB 区域实验模板

本目录同时保留旧版平铺 namelist 生成接口，并提供完整的区域实验模板创建、分阶段 restart 交接、作业生成、状态检查和 OpenBench 配置生成。

## 创建一个模板实验

```bash
python dlb_01_create_experiment.py EXP001 baseline USA \
  --repo-root /path/to/CoLM202X \
  --case-root /path/to/cases \
  --forcing-nml JRA3Q \
  --eval-start-year 2001 \
  --eval-end-year 2005
```

可用 `--output-dir` 指定最终实验目录的完整路径：

```bash
python dlb_01_create_experiment.py EXP001 control Mekong \
  --repo-root /path/to/CoLM202X \
  --case-root /path/to/cases \
  --forcing-nml JRA3Q \
  --output-dir /path/to/my_experiment
```

也可使用 `--experiments-root /path/to/parent` 仅指定父目录；最终目录名仍自动生成为 `EXPnnn_slug_REGION`。两个参数不能同时使用。

区域可选 `USA`、`SA`、`Mekong`、`Global`。默认输出到：

```text
/share/home/dq134/wzx/CoLM/DLB_experiments/EXP001_baseline_USA
```

创建过程先在同级临时目录中生成并校验全部文件，再原子改名。目标实验目录或任一同名 case 已存在时会停止。创建器不会创建 case、运行 CoLM/OpenBench 或调用 `bsub`。

生成内容包括：

- `config/colm/`：spinup、D_INIT、E0–E7 namelist、history、CSV 和 schema-v2 manifest；
- `config/jobs/`：10 份 LSF 和默认 dry-run 的 `submit_batch.sh`；
- `config/openbench/`：USA 28 份，其余区域各 14 份 pairwise YAML；
- `manifest.yaml`、`artifacts.yaml`、`checksums.sha256`、`validation_report.txt`；
- `logs/`、`provenance/`、`summary/`、`openbench_output/`。

## 人工门禁流程

```bash
# 1. 预览/提交 spinup：mksrfdata -> mkinidata -> colm
bash config/jobs/submit_batch.sh --dry-run --stage spinup

# 2. spinup 完成后，为 D_INIT 建立 landdata 链接
python /share/home/dq134/wzx/CoLM/preprocess/dlb_05_restart_handoff.py \
  prepare-dam-init --nml-dir config/colm
# 确认 dry-run 后追加 --apply

# 3. D_INIT 只运行 2000 年 mkinidata
bash config/jobs/submit_batch.sh --dry-run --stage dam-init

# 4. 事务式交接；D 组 gridriver 来自 D_INIT，其余 restart 来自 spinup
python /share/home/dq134/wzx/CoLM/preprocess/dlb_05_restart_handoff.py \
  handoff --nml-dir config/colm
# 确认 dry-run 后追加 --apply

# 5. E0-E7 均只运行 colm.x
bash config/jobs/submit_batch.sh --dry-run --stage experiments
```

只有显式 `--submit` 才会提交作业。脚本不会自动建立 scheduler dependency。

各阶段只读检查：

```bash
python dlb_04_preflight.py --nml-dir config/colm --phase handoff
python dlb_06_stage_status.py --nml-dir config/colm
```

## OpenBench

每份配置只比较 `E0_CTL` 与一个处理组。径流使用 `GRDC_Daily`，淹没使用 `GIEMS_v2_LowRes`。

```bash
openbench check CONFIG.yaml
openbench run CONFIG.yaml
```

刷新状态并登记单个项目结果：

```bash
python dlb_07_refresh_experiment.py /path/to/EXP001_baseline_USA \
  --openbench-result PROJECT_NAME=success
```

状态顺序为 `created -> spinup_complete -> dam_init_complete -> handoff_complete -> colm_complete -> openbench_complete -> validated`。只有全部 OpenBench 项目成功后才进入 `openbench_complete`；最终使用 `--mark-validated`，且自动检查必须全部通过。`hypothesis` 和 `conclusion` 在 refresh 时会保留。

## 旧平铺接口

原命令仍可直接使用：

```bash
python dlb_02_generate_namelists.py \
  --repo-root /path/to/CoLM202X \
  --out-dir /path/to/generated \
  --case-root /path/to/cases \
  --forcing-nml JRA3Q \
  --region USA
```

模板创建器调用同一生成器的 `--study-id`、`--study-slug`、`--include-dam-init` 和 `--no-lsf` 模式；模板实例的 LSF 统一由 `dlb_03_generate_jobs.py` 生成。
