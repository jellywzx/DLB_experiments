#!/usr/bin/env bash
set -euo pipefail
ssh -o BatchMode=yes node111 'bash -s' <<'NODE'
set -euo pipefail
ANALYSIS_ROOT=/share/home/dq134/wzx/DLB_experiments/dam_analysis/missouri_joint_2001_2002
cd "$ANALYSIS_ROOT"
source /share/home/dq134/wzx/openbench/openbench_1015/.venv/bin/activate
export MPLBACKEND=Agg OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
python -m pytest test_analyze.py -q --disable-warnings
mkdir -p runs
RUN_OUT="$ANALYSIS_ROOT/runs/$(TZ=Asia/Shanghai date +%Y%m%d_%H%M%S)"
printf 'RESULT_DIR=%s\n' "$RUN_OUT"
python analyze.py --start 2001-01-01 --end 2002-12-31 --out "$RUN_OUT" 2>&1 | tee "$RUN_OUT.log"
python validate_outputs.py "$RUN_OUT"
NODE
