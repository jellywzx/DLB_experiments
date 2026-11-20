#!/usr/bin/env python3
"""Generate and optionally submit full-slot single-host LSF OpenBench array jobs."""

# 用法示例（在已激活 OpenBench 环境的 shell 中执行；各组按需选择）：
#   DLB_ROOT=/share/home/dq134/wzx/DLB_experiments
#   EXP_DIR=/path/to/experiment  # 替换为目标实验目录，需先用 dlb_08 生成 OpenBench 配置
# openbench 不在 PATH 时，在预览和提交命令中添加 --openbench-exe /path/to/openbench。
#
# 1. 普通运行：先预览，再提交。输出目录非空的项目会提示并跳过。
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --submit
# 若显示 pending=0、SKIP existing result，表示没有待提交项目；需要重跑时使用第 2 组。
#
# 2. 覆盖重跑：目录非空也加入待运行清单，成功、失败或状态不明的旧结果均可重算。
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --overwrite --submit
# 预览会显示 OVERWRITE non-empty output directory，不移动旧结果。
# 正式任务启动时，将旧目录归档到输出根目录的 .dlb_openbench_backups/，再创建干净目录运行。
# 有 .dlb_openbench_running 标记的项目仍会跳过；排队期间目录身份发生变化也会跳过。
# 覆盖提交必须保留 --overwrite，并按当前目录状态和资源参数重新生成覆盖作业。
#
# 3. 仅重试确认失败的项目：不重跑成功或状态不明的结果，旧目录在任务启动时归档。
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --retry-failed --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --retry-failed --submit
# 再次失败后，或需更新重试清单/资源时，重新生成重试快照并预览：
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --retry-failed --overwrite --dry-run
# 核对后生成并提交本轮重试作业（保留 --overwrite）：
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --retry-failed --overwrite --submit
# --retry-failed 与 --overwrite 同用时，仍只选择确认失败的项目。
#
# 4. 限定评估项目：将上述命令的 --all 替换为所需选择器。
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -L -B --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -LB --overwrite --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --ctl --spinup --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --comparisons --dry-run
# -D/-L/-B/-DL/-DB/-LB/-DLB 分别对应 E1_D/E2_L/E3_B/E4_DL/E5_DB/E6_LB/E7_ALL。
# -L -B 选择两个单项处理，-LB 选择组合处理 E6_LB；--all 不能与其他选择器一起使用。
# 可追加 --variables streamflow dam_storage，仅提交所选实验组中这些变量的项目。
# 变量使用模板名（也可带 .yaml/.yml），不区分大小写；不指定则保留全部变量。
#
# 5. 自定义资源的覆盖重跑示例：预览、提交均带相同资源参数。
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --overwrite --cores 4 --slots-per-node 48 --queue normal --memory 150G --walltime 24:00 --max-concurrent 20 --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --overwrite --cores 4 --slots-per-node 48 --queue normal --memory 150G --walltime 24:00 --max-concurrent 20 --submit
# --cores 为每个评估使用的核数，--slots-per-node 为每个任务在单节点申请的 slots，
# --max-concurrent 为数组任务最大并发数；--cores 不得大于 --slots-per-node。
#
# 6. 指定变量：--variables 与实验选择器取交集，预览和提交使用相同选择范围。
# 仅提交 D 和 LB 两组的径流、库容评估：
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D -LB --variables streamflow dam_storage --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D -LB --variables streamflow dam_storage --submit
# 所有实验组中，只选择水库入流、出流和库容：
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --variables dam_inflow dam_outflow dam_storage --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" --all --variables dam_inflow dam_outflow dam_storage --submit
# 仅重试 D 组径流评估中确认失败的项目：
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --variables streamflow --retry-failed --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --variables streamflow --retry-failed --submit
# 覆盖重跑 D 组库容评估，旧结果在任务启动时归档：
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --variables dam_storage --overwrite --dry-run
#   python3 "$DLB_ROOT/dlb_09_submit_openbench.py" "$EXP_DIR" -D --variables dam_storage --overwrite --submit
# 标准变量名：streamflow、inundation_fraction、inundation_area、dam_inflow、dam_outflow、dam_storage。
# 变量必须已在目标实验 manifest 中登记；未知名称会报错并列出可用值。
# 名称不区分大小写，可带 .yaml/.yml；不指定 --variables 时保留所有变量。
# 不同变量组合使用独立的数组作业和快照；默认仍跳过输出目录非空的项目。
#
# 7. 快捷入口也可指定变量（历史 .sh 入口需先更新）：
#   bash "$EXP_DIR/config/openbench_jobs/submit_openbench_D.sh" --variables streamflow dam_storage --dry-run
#   bash "$EXP_DIR/config/openbench_jobs/submit_openbench_D.sh" --variables streamflow dam_storage --submit
# 若历史入口提示 Unsupported option，或主脚本提示 submission entry differs，
# 可先仅备份并更新快捷入口；在已激活 OpenBench 环境的 shell 中执行：
#   (cd "$DLB_ROOT" && python3 -c 'import sys; from pathlib import Path; from dlb_09_submit_openbench import generate_submit_entries; generate_submit_entries(Path(sys.argv[1]).expanduser().resolve(), overwrite=True)' "$EXP_DIR")
# 此更新只处理入口 .sh，不生成数组、不重跑评估、不提交任务。
#
# --dry-run 完全不写文件；不带 --dry-run/--submit 时只生成作业文件；仅 --submit 调用 bsub。
# 覆盖前备份已有作业文件至作业目录的 .dlb_backups/；旧结果在任务启动时另行归档。
# 不加 --overwrite 时，已有且匹配的作业快照会复用，资源参数以快照为准。
# 作业文件：$EXP_DIR/config/openbench_jobs/；调度器日志：$EXP_DIR/logs/openbench/。
# 更多参数：python3 "$DLB_ROOT/dlb_09_submit_openbench.py" --help
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import uuid
from datetime import datetime,timezone
from pathlib import Path

from dlb_overwrite import backup_files
from dlb_openbench_state import output_dir,task_state,sha256,atomic_json


SELECTIONS=(
    ('D','E1_D','select_d'),('L','E2_L','select_l'),('B','E3_B','select_b'),
    ('DL','E4_DL','select_dl'),('DB','E5_DB','select_db'),
    ('LB','E6_LB','select_lb'),('DLB','E7_ALL','select_dlb'),
)


def sq(value:object)->str:return shlex.quote(str(value))


def output_dir(config:Path)->Path:
    for line in config.read_text(encoding='utf-8').splitlines():
        if re.match(r'^\s{2}output_dir\s*:',line):
            raw=line.split(':',1)[1].strip().strip('"\'')
            if not raw:break
            return Path(raw).expanduser().resolve()
    raise ValueError(f'missing project.output_dir: {config}')


def has_results(path:Path)->bool:
    if not path.exists():return False
    if not path.is_dir():return True
    return next(path.iterdir(),None) is not None


def log_outcome(path:Path)->str|None:
    log=path/'run.log'
    if not log.is_file():return None
    with log.open('rb') as stream:
        stream.seek(0,2);stream.seek(max(0,stream.tell()-262144))
        text=stream.read().decode('utf-8',errors='replace')
    summaries=re.findall(r'All phases complete:.*?\b(\d+) errors?\b',text)
    if summaries:return 'failed' if int(summaries[-1]) else 'success'
    summaries=re.findall(r'Evaluation phase:.*?\b(\d+) failed\b',text)
    if summaries and int(summaries[-1]):return 'failed'
    return None


def result_state(path:Path)->str:
    if path.is_symlink() or not path.is_dir():return 'unknown'
    if (path/'.dlb_openbench_running').exists():return 'running'
    failed=any((path/name).is_file() for name in ('.openbench_failed','failed.marker'))
    success=any((path/name).is_file() for name in ('.openbench_success','success.marker'))
    outcome=log_outcome(path)
    # Older wrappers could write success despite OpenBench reporting errors.
    if outcome=='failed':return 'failed'
    if failed and (success or outcome=='success'):return 'conflicting'
    if failed:return 'failed'
    if success or outcome=='success':return 'success'
    return 'unknown'


def result_token(path:Path)->str:
    if not path.exists():return 'missing'
    stat=path.stat()
    return f'{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}'


def retry_task(config:Path,output:Path,token:str,openbench:Path,cores:int,*,overwrite:bool=False)->int:
    """Execute one immutable rerun snapshot; never called by the preview path."""
    output.parent.mkdir(parents=True,exist_ok=True)
    lock=output.parent/f'.{output.name}.retry.lock'
    with lock.open('a') as handle:
        try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(f'SKIP retry already active: {output}');return 0
        state=result_state(output)
        if (output.is_symlink() or (output.exists() and not output.is_dir())
            or state=='running' or (not overwrite and state!='failed')
            or result_token(output)!=token):
            print(f'SKIP retry snapshot no longer matches: {output} state={state}');return 0
        if output.exists():
            stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            backup=output.parent/'.dlb_openbench_backups'/f'{output.name}.{stamp}.{uuid.uuid4().hex}'
            backup.parent.mkdir(parents=True,exist_ok=True)
            output.rename(backup)
            print(f'BACKUP {output} -> {backup}',flush=True)
        output.mkdir()
        running=output/'.dlb_openbench_running';running.touch()
        # An interrupted process deliberately leaves its running marker for review.
        status=subprocess.run([str(openbench),'check',str(config)],check=False).returncode
        if not status:
            status=subprocess.run([str(openbench),'run',str(config),'--cores',str(cores)],check=False).returncode
        if not status and log_outcome(output)=='failed':status=1
        running.unlink()
        (output/('.openbench_failed' if status else '.openbench_success')).touch()
        return status


def safe_field(value:object,label:str)->str:
    text=str(value)
    if not text or '\t' in text or '\n' in text or '\r' in text:
        raise ValueError(f'invalid {label}: {text!r}')
    return text


def write_atomic(path:Path,text:str)->None:
    temporary=path.with_name(f'.{path.name}.tmp.{os.getpid()}')
    temporary.write_text(text,encoding='utf-8')
    os.replace(temporary,path)


def job_script(label:str,count:int,mapping:Path,openbench:Path,logs:Path,queue:str,
               cores:int,slots_per_node:int,memory:str,walltime:str,
               max_concurrent:int|None,retry:bool=False,overwrite:bool=False)->str:
    array=f'openbench_{label}[1-{count}]'+(f'%{max_concurrent}' if max_concurrent else '')
    lines=['#!/usr/bin/env bash',f'#BSUB -J "{array}"',
        f'#BSUB -q {queue}',f'#BSUB -n {slots_per_node}',f'#BSUB -W {walltime}',
        '#BSUB -R "span[hosts=1]"',f'#BSUB -R "rusage[mem={memory}]"',
        f'#BSUB -o {logs}/openbench_{label}.%J.%I.out',
        f'#BSUB -e {logs}/openbench_{label}.%J.%I.err',
        '#BSUB -env "all,LSB_STDOUT_DIRECT=Y"','',
        'set -euo pipefail',f'MAPPING={sq(mapping)}',f'OPENBENCH={sq(openbench)}',
        'INDEX=${LSB_JOBINDEX:?LSB_JOBINDEX is required}',
        "ROW=$(awk -F '\\t' -v i=\"$INDEX\" '$1 == i {print; exit}' \"$MAPPING\")",
        '[[ -n "$ROW" ]] || { echo "No mapping for index $INDEX" >&2; exit 2; }',
        "IFS=$'\\t' read -r _ PROJECT CONFIG OUTPUT TOKEN <<< \"$ROW\"",
        '[[ -f "$CONFIG" ]] || { echo "Missing config: $CONFIG" >&2; exit 2; }',
        '[[ -x "$OPENBENCH" ]] || { echo "OpenBench is not executable: $OPENBENCH" >&2; exit 2; }',
        'if [[ -e "$OUTPUT" ]]; then',
        '  if [[ ! -d "$OUTPUT" ]] || [[ -n "$(find "$OUTPUT" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then',
        '    echo "SKIP existing result: $PROJECT -> $OUTPUT"',
        '    exit 0',
        '  fi',
        'fi',
        'mkdir -p "$OUTPUT"',
        'RUNNING="$OUTPUT/.dlb_openbench_running"',
        'FAILED="$OUTPUT/.openbench_failed"',
        'SUCCESS="$OUTPUT/.openbench_success"',
        'touch "$RUNNING"',
        'rm -f "$FAILED"',
        'echo "START $(date -Is) $(hostname) $PROJECT"',
        'if "$OPENBENCH" check "$CONFIG" && "$OPENBENCH" run "$CONFIG" --cores '+str(cores)+'; then',
        '  rm -f "$RUNNING" "$FAILED"',
        '  touch "$SUCCESS"',
        '  echo "FINISH $(date -Is) $PROJECT"',
        'else',
        '  status=$?',
        '  rm -f "$RUNNING" "$SUCCESS"',
        '  touch "$FAILED"',
        '  echo "FAILED $(date -Is) $PROJECT status=$status" >&2',
        '  exit "$status"',
        'fi','']
    if retry or overwrite:
        task_flag='--_retry-task' if retry else '--_overwrite-task'
        start=lines.index('if [[ -e "$OUTPUT" ]]; then')
        lines=lines[:start]+[
            f'exec {sq(sys.executable)} {sq(Path(__file__).resolve())} {task_flag} '
            f'"$CONFIG" "$OUTPUT" "$TOKEN" "$OPENBENCH" {cores}','']
    else:
        index=next(i for i,line in enumerate(lines) if line.startswith('if "$OPENBENCH" check'))
        lines[index]=lines[index].replace('; then',
            f' && {sq(sys.executable)} {sq(Path(__file__).resolve())} --_result-ok "$OUTPUT"; then')
    return '\n'.join(lines)


def generate_submit_entries(root:Path,*,overwrite:bool=False,dry_run:bool=False)->list[Path]:
    """Render lazy submission entries without requiring OpenBench or a scheduler."""
    destination=root/'config/openbench_jobs'
    selectors=[(short,'-'+short) for short,_,_ in SELECTIONS]
    selectors.extend((name,'--'+name) for name in ('ctl','spinup','comparisons','all'))
    files={}
    for label,selector in selectors:
        text=f'''#!/usr/bin/env bash
# OpenBench {label}: preview by default; only --submit calls bsub.
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${{BASH_SOURCE[0]}}")" && pwd)"
EXP_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
ARGS=("$@")
while (($#)); do
  case "$1" in
    --variables)
      shift
      (($#)) && [[ "$1" != -* ]] || {{ echo "Missing value: --variables" >&2; exit 2; }}
      while (($#)) && [[ "$1" != -* ]]; do shift; done ;;
    --variables=*) shift ;;
    --submit|--dry-run|--overwrite|--retry-failed|--rerun-unverified|-h|--help) shift ;;
    --openbench-exe|--queue|--cores|--slots-per-node|--memory|--walltime|--max-concurrent|--out-dir|--log-dir)
      (($# >= 2)) || {{ echo "Missing value: $1" >&2; exit 2; }}
      shift 2 ;;
    --openbench-exe=*|--queue=*|--cores=*|--slots-per-node=*|--memory=*|--walltime=*|--max-concurrent=*|--out-dir=*|--log-dir=*) shift ;;
    *) echo "Unsupported option (experiment selection is fixed): $1" >&2; exit 2 ;;
  esac
done
exec {sq(sys.executable)} {sq(Path(__file__).resolve())} "$EXP_DIR" {selector} "${{ARGS[@]}}"
'''
        files[destination/f'submit_openbench_{label}.sh']=text
    # Check all conflicts before writing any entry. Do not follow user symlinks.
    for path,text in files.items():
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError(f'unsafe submission entry: {path}')
        if path.exists() and path.read_text(encoding='utf-8')!=text and not overwrite:
            raise ValueError(f'submission entry differs: {path}; use --overwrite')
    changed=[path for path,text in files.items() if path.exists() and path.read_text(encoding='utf-8')!=text]
    if dry_run:return list(files)
    if overwrite:backup_files(changed,destination)
    destination.mkdir(parents=True,exist_ok=True)
    for path,text in files.items():
        if not path.exists() or path.read_text(encoding='utf-8')!=text:
            write_atomic(path,text)
        if not os.access(path,os.X_OK):path.chmod(path.stat().st_mode|0o111)
    return list(files)


def main()->int:
    p=argparse.ArgumentParser(description=__doc__,allow_abbrev=False)
    p.add_argument('experiment_dir',type=Path)
    p.add_argument('-D',dest='select_d',action='store_true',help='Select E1_D')
    p.add_argument('-L',dest='select_l',action='store_true',help='Select E2_L')
    p.add_argument('-B',dest='select_b',action='store_true',help='Select E3_B')
    p.add_argument('-DL',dest='select_dl',action='store_true',help='Select E4_DL')
    p.add_argument('-DB',dest='select_db',action='store_true',help='Select E5_DB')
    p.add_argument('-LB',dest='select_lb',action='store_true',help='Select E6_LB')
    p.add_argument('-DLB',dest='select_dlb',action='store_true',help='Select E7_ALL')
    p.add_argument('--ctl',action='store_true',help='Select control evaluation projects')
    p.add_argument('--spinup',action='store_true',help='Select spinup evaluation projects')
    p.add_argument('--comparisons',action='store_true',help='Select eight-experiment comparison projects')
    p.add_argument('--all',dest='all_projects',action='store_true',help='Select every OpenBench project')
    p.add_argument('--variables',nargs='+',action='extend',metavar='NAME',
                   help='Filter selected groups by variable template names (case-insensitive; .yaml/.yml optional); default: all variables')
    p.add_argument('--retry-failed',action='store_true',
                   help='Retry only confirmed failed outputs; archive old results at task startup')
    p.add_argument('--openbench-exe',type=Path,
                   help='OpenBench executable; default: openbench from the current PATH')
    p.add_argument('--queue',default='normal')
    p.add_argument('--cores',type=int,default=4)
    p.add_argument('--slots-per-node',type=int,default=48,
                   help='LSF slots reserved on one host; full-node only when this equals its capacity (default: 48)')
    p.add_argument('--memory',default='150G')
    p.add_argument('--walltime',default='24:00')
    p.add_argument('--max-concurrent',type=int,default=20,
                   help='Maximum simultaneous array tasks (default: 20)')
    p.add_argument('--out-dir',type=Path,help='Default: EXP_DIR/config/openbench_jobs')
    p.add_argument('--log-dir',type=Path,help='Default: EXP_DIR/logs/openbench')
    p.add_argument('--overwrite',action='store_true',help='Replace job files and rerun selected outputs; archive old directories at task startup, skip running outputs')
    p.add_argument('--rerun-unverified',action='store_true',
                   help='Legacy compatibility option; use --overwrite to rerun existing outputs')
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--submit',action='store_true',help='Submit with bsub; default is dry-run')
    mode.add_argument('--dry-run',action='store_true',help='Preview only; never write files or call bsub')
    args=p.parse_args()

    requested=[(short,treatment) for short,treatment,dest in SELECTIONS if getattr(args,dest)]
    for name in ('ctl','spinup','comparisons'):
        if getattr(args,name):requested.append((name,name))
    if args.all_projects and requested:p.error('--all cannot be combined with experiment selectors')
    if not args.all_projects and not requested:
        p.error('select --all, --ctl, --spinup, --comparisons or at least one of -D/-L/-B/-DL/-DB/-LB/-DLB')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+',args.queue):p.error('invalid --queue')
    if not 1<=args.cores<=100000:p.error('--cores must be >= 1')
    if not 1<=args.slots_per_node<=100000:p.error('--slots-per-node must be >= 1')
    if args.cores>args.slots_per_node:p.error('--cores cannot exceed --slots-per-node')
    if not re.fullmatch(r'[1-9][0-9]*(?:[KMGTP])?',args.memory,re.I):p.error('invalid --memory')
    if not re.fullmatch(r'\d{1,4}:[0-5]\d',args.walltime):p.error('--walltime must be HH:MM')
    if args.max_concurrent is not None and args.max_concurrent<1:p.error('--max-concurrent must be >= 1')

    root=args.experiment_dir.expanduser().resolve();manifest_path=root/'manifest.yaml'
    if not manifest_path.is_file():raise ValueError(f'missing experiment manifest: {manifest_path}')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    projects=manifest.get('evaluation',{}).get('projects',[])
    if not isinstance(projects,list) or not projects:raise ValueError('manifest has no OpenBench projects')
    variables=set()
    if args.variables:
        available={item.get('type') for item in projects if isinstance(item.get('type'),str)}
        by_name={name.lower():name for name in available}
        for raw in args.variables:
            name=re.sub(r'\.(?:yaml|yml)$','',raw,flags=re.I).lower()
            if name not in by_name:
                raise ValueError(f'unknown variable template: {raw}; available: {", ".join(sorted(available)) or "none (manifest projects need type fields)"}')
            canonical=by_name[name]
            if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]*',canonical):
                raise ValueError(f'unsafe variable template name: {canonical!r}')
            variables.add(canonical)
    if args.all_projects:label='all';selected=projects
    else:
        label='_'.join(short for short,_ in requested)
        treatments={treatment for _,treatment in requested}
        selected=[item for item in projects if item.get('treatment') in treatments]
    if variables:
        selected=[item for item in selected if item.get('type') in variables]
        label+='__vars_'+'__'.join(sorted(variables))
    if not selected:raise ValueError(f'no OpenBench projects selected for {label}')
    if args.retry_failed:label+='_retry_failed'
    elif args.overwrite:label+='_overwrite'

    config_root=(root/'config/openbench').resolve();seen_projects=set();seen_configs=set();pending=[];skipped=[];selection=[]
    for item in selected:
        project=safe_field(item.get('project'),'project')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',project):raise ValueError(f'unsafe project name: {project!r}')
        config=(root/safe_field(item.get('config'),'config')).resolve()
        try:config.relative_to(config_root)
        except ValueError as exc:raise ValueError(f'config escapes config/openbench: {config}') from exc
        if project in seen_projects:raise ValueError(f'duplicate project: {project}')
        if config in seen_configs:raise ValueError(f'duplicate config: {config}')
        seen_projects.add(project);seen_configs.add(config)
        if not config.is_file():raise ValueError(f'missing OpenBench config: {config}')
        output=output_dir(config)/project
        selection.append({'project':project,'config':str(config),'output_dir':str(output),
                          'config_sha256':sha256(config)})
        safe_field(config,'config');safe_field(output,'output_dir')
        if args.retry_failed:
            state=result_state(output)
            print(f'STATE {project}: {state} (manifest={item.get("status","missing")})')
            if state=='failed':pending.append((project,config,output))
            else:skipped.append((project,output))
        elif args.overwrite:
            if output.is_symlink() or (output.exists() and not output.is_dir()):
                raise ValueError(f'unsafe overwrite output (expected a directory): {output}')
            if result_state(output)=='running':
                skipped.append((project,output))
            else:
                if has_results(output):
                    print(f'OVERWRITE non-empty output directory: {project} -> {output}; archive old results at task startup')
                pending.append((project,config,output))
        elif has_results(output):skipped.append((project,output))
        else:pending.append((project,config,output))

    print(f'Selection {label}: selected={len(selected)} pending={len(pending)} skipped={len(skipped)}')
    for project,output in skipped:
        reason=('not a confirmed failed result' if args.retry_failed else
                'running result' if args.overwrite else 'existing result (non-empty output; use --overwrite to rerun)')
        print(f'SKIP {reason}: {project} -> {output}')
    entries=generate_submit_entries(root,overwrite=args.overwrite,dry_run=args.dry_run)
    print(f'Submission entries: {len(entries)} at {root/"config/openbench_jobs"}')
    if not pending:
        if args.dry_run:print('DRY-RUN: no files or backups written.')
        print('No jobs to generate or submit.')
        return 0

    out_dir=(args.out_dir.expanduser().resolve() if args.out_dir else root/'config/openbench_jobs')
    log_dir=(args.log_dir.expanduser().resolve() if args.log_dir else root/'logs/openbench')
    mapping=out_dir/f'openbench_{label}_projects.tsv';job=out_dir/f'submit_openbench_{label}_array.lsf'
    if args.retry_failed and job.is_file() and not args.overwrite:
        entries=[line for line in job.read_text(encoding='utf-8').splitlines() if line.startswith('MAPPING=')]
        if len(entries)!=1:raise ValueError(f'invalid retry snapshot: {job}; use --overwrite')
        fields=shlex.split(entries[0][len('MAPPING='):])
        if len(fields)!=1:raise ValueError(f'invalid retry mapping: {job}')
        mapping=Path(fields[0])
        if mapping.parent!=out_dir or not mapping.name.startswith(f'openbench_{label}_'):
            raise ValueError(f'retry mapping escapes snapshot directory: {mapping}')
    snapshot=out_dir/f'openbench_{label}_snapshot.json'
    expected={'selection':selection,'pending':[project for project,_,_ in pending]}
    existing=mapping.exists() or job.exists()
    if existing and not args.overwrite:
        if not mapping.is_file() or not job.is_file():
            raise ValueError(f'incomplete generated job files for {label}; use --overwrite')
        try:saved=json.loads(snapshot.read_text())
        except (OSError,ValueError):raise ValueError('missing/invalid job snapshot; use --overwrite')
        if (any(saved.get(k)!=v for k,v in expected.items()) or saved.get('mapping_sha256')!=sha256(mapping)
            or saved.get('job_sha256')!=sha256(job)):
            raise ValueError('stale job snapshot; regenerate with --overwrite')
        print(f'Reusing generated job snapshot: {job}')
        print('Resource options are read from that snapshot. Use --overwrite to regenerate job files and rerun selected outputs (old results are archived).')
    else:
        detected=args.openbench_exe or shutil.which('openbench')
        if not detected:
            raise ValueError('openbench not found in PATH; activate its environment or use --openbench-exe')
        openbench=Path(detected).expanduser().resolve()
        if not openbench.is_file() or not os.access(openbench,os.X_OK):
            raise ValueError(f'OpenBench is not executable: {openbench}')
        if args.dry_run:
            print(f'DRY-RUN: would generate {len(pending)} jobs at {job}; no files or backups written; no jobs submitted.')
            return 0
        if args.overwrite:backup_files([mapping,job,snapshot],out_dir)
        out_dir.mkdir(parents=True,exist_ok=True);log_dir.mkdir(parents=True,exist_ok=True)
        if args.retry_failed or args.overwrite:
            # Submitted jobs retain an immutable mapping even after --overwrite.
            mapping=out_dir/f'openbench_{label}_{uuid.uuid4().hex}_projects.tsv'
        rows=['index\tproject\tconfig\toutput_dir'+('\ttoken' if args.retry_failed or args.overwrite else '')]
        rows.extend(f'{i}\t{project}\t{config}\t{output}'+
                    (f'\t{result_token(output)}' if args.retry_failed or args.overwrite else '')
                    for i,(project,config,output) in enumerate(pending,1))
        write_atomic(mapping,'\n'.join(rows)+'\n')
        write_atomic(job,job_script(label,len(pending),mapping,openbench,log_dir,args.queue,
                                    args.cores,args.slots_per_node,args.memory,args.walltime,
                                    args.max_concurrent,retry=args.retry_failed,overwrite=args.overwrite))
        job.chmod(0o755)
        atomic_json(snapshot,{**expected,'mapping_sha256':sha256(mapping),'job_sha256':sha256(job)})
        print(f'Generated {len(pending)} jobs: {job}')
    if not args.submit:
        mode='DRY-RUN' if args.dry_run else 'NOT SUBMITTED'
        print(f'{mode}: bsub < {job}')
        return 0
    bsub=shutil.which('bsub')
    if not bsub:raise ValueError('bsub not found in PATH')
    result=subprocess.run([bsub],input=job.read_text(encoding='utf-8'),text=True,
                          cwd=out_dir,capture_output=True,check=False)
    if result.stdout:print(result.stdout,end='')
    if result.stderr:print(result.stderr,end='',file=sys.stderr)
    if result.returncode:raise ValueError(f'bsub failed with status {result.returncode}')
    return 0


if __name__=='__main__':
    try:
        if len(sys.argv)==3 and sys.argv[1]=='--_result-ok':
            sys.exit(1 if log_outcome(Path(sys.argv[2]))=='failed' else 0)
        if len(sys.argv)==7 and sys.argv[1] in ('--_retry-task','--_overwrite-task'):
            sys.exit(retry_task(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],
                                Path(sys.argv[5]),int(sys.argv[6]),overwrite=sys.argv[1]=='--_overwrite-task'))
        sys.exit(main())
    except (ValueError,OSError,json.JSONDecodeError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr);sys.exit(2)
