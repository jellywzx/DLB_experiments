# 统一覆盖规则

适用于 `dlb_02`、`dlb_03`、`dlb_08`、`dlb_09` 和 `dlb_prepare_resopsus.py`。

- 默认保护已有内容：根据工作流拒绝冲突、保留、跳过或复用；不隐式覆盖。
- `--overwrite` 授权替换脚本负责的选定范围；替换前先完成备份，备份失败则停止覆盖。
- 显式 `--dry-run` 不写配置、作业、备份、manifest 或提交入口，不运行刷新和调度提交。
  预览会检查已有输入与覆盖保护，但不等于实际生成或科学评估验证。
- 覆盖不隐含模型运行或调度提交。生成器仅生成文件，`dlb_09 --submit` 才提交，
  ResOpsUS helper 必须 `--apply` 才写入；`--dry-run` 与这些执行参数互斥。

| 脚本 | 授权覆盖的范围 | 备份位置 |
|---|---|---|
| dlb_02 普通生成 | 本次生成的同名文件；保留无关文件 | 输出目录 `.dlb_backups/<时间和唯一标识>/` |
| dlb_02 --experiment-dir | 整体替换 config/colm、config/jobs，更新 manifest | 实验 `provenance/backups/<时间>/` |
| dlb_03 | 本次生成的作业、提交助手、站点配置；保留无关文件 | 输出目录 `.dlb_backups/<时间和唯一标识>/` |
| dlb_08 --templates | 只更新选中模板；保留未选中文件及项目 | 实验 `provenance/backups/<时间>/` |
| dlb_08 不指定 --templates | 整体替换 config/openbench 及相应项目登记 | 同上 |
| dlb_09 | 作业、提交入口、快照；允许所选评估结果归档后重跑 | 作业目录 `.dlb_backups/`；结果在任务启动时移到输出根目录 `.dlb_openbench_backups/` |
| dlb_prepare_resopsus | 本 helper 的用户 overlay；不改观测源数据 | 用户目录 `dlb_resopsus/snapshot_*/previous_dlb_resopsus.yaml` |

整体替换目录时，目录内手工文件也将退出当前配置，旧副本保留在备份中。
CoLM 实验已存在运行标记或 history/restart 产物时，`dlb_02 --experiment-dir` 仍拒绝覆盖。
OpenBench 有 running 标记的项目仍跳过，任务启动时再次核对目录身份。
`--retry-failed --overwrite` 仍只选择确认失败的项目，不扩展为所有已有结果。

```bash
# 纯预览；首次生成也可以使用 --dry-run
python3 "$DLB_ROOT/dlb_02_generate_namelists.py" --experiment-dir "$EXP_DIR" --overwrite --dry-run
python3 "$DLB_ROOT/dlb_08_generate_openbench.py" "$EXP_DIR" --overwrite --dry-run
python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite --dry-run

# 覆盖提交：根据当前目录状态重新生成作业；自定义资源参数须在此再次传入
python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite --submit
```

`dlb_02/03/08` 默认仍为生成模式；ResOpsUS helper 默认纯预览。
`dlb_09` 不带 `--dry-run` 或 `--submit` 时只生成文件并显示提交命令；
普通提交可以复用有效快照。`--overwrite --dry-run` 不创建新快照，后续覆盖提交须保留
`--overwrite`，而且不能依赖预览保存资源参数。

备份不表示所有文件写入均具备事务回滚：`dlb_02` 普通生成、`dlb_03` 及 `dlb_09`
多文件生成失败时可能已有部分新文件，但覆盖前的旧文件可从备份恢复。
