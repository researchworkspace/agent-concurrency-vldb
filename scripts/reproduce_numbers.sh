#!/usr/bin/env bash
# Recompute the paper's numbers and figures from the data archive and compare with results/.
# Usage: bash scripts/reproduce_numbers.sh DATA OUT
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=$(cd "${1:?usage: reproduce_numbers.sh DATA OUT}" && pwd)
OUT=$(mkdir -p "${2:?usage: reproduce_numbers.sh DATA OUT}" && cd "$2" && pwd)
PY="${PYTHON:-python3}"
SRC="$ROOT/src"
C0="$DATA/agent-concurrency-c0-main"
C1="$DATA/agent-concurrency-c1-main"
C2="$DATA/agent-concurrency-c2-main"
DIAG="$DATA/agent-concurrency-c2-diagnostics"

echo "== C0 report (C0's checker, src/versions/c0)"
C0SRC="$OUT/c0-src"
mkdir -p "$C0SRC"
cp "$SRC/c0_tasks.py" "$SRC/c0-plan.json" "$C0SRC/"
cp "$SRC/versions/c0/c0_harness.py" "$C0SRC/"
"$PY" -I "$C0SRC/c0_harness.py" report --output "$C0/harness" \
    --generations "$C0/generations/qwen25coder32b" --generations "$C0/generations/qwen3coder30b" \
    --save "$OUT/c0-report.json" > "$OUT/c0-report.txt"

echo "== C1 report (H1-H7)"
"$PY" -I "$SRC/c1_run.py" report --runs "$C1/harness-postgresql" "$C1/harness-mysql" \
    --sweeps "$C1"/sweep-*/ --save "$OUT/c1-report.json" > "$OUT/c1-report.txt"

echo "== C1 paper numbers"
"$PY" -I "$SRC/c1_results_analysis.py" "$C1" "$ROOT/results/c0-report.implementations.json" \
    "$OUT/c1-paper-numbers.json" > "$OUT/c1-paper-numbers.txt"

echo "== C2 report (H8-H12; PostgreSQL load from the one-shard-at-a-time rerun)"
"$PY" -I "$SRC/c2_analysis.py" report --c2 "$C2" --c1 "$C1" --load postgresql=load-postgresql-2 \
    --save "$OUT/c2-report.json" > "$OUT/c2-report.txt"

echo "== C2 paper numbers"
"$PY" -I "$SRC/c2_results_analysis.py" "$C2" "$C1" "$ROOT/results/c0-report.implementations.json" \
    "$OUT/c2-paper-numbers.json" --load postgresql=load-postgresql-2 > "$OUT/c2-paper-numbers.txt"

echo "== Replay ablation report"
"$PY" -I "$SRC/c2_ablation.py" report --c1 "$C1" --output "$DIAG/ablation" \
    --save "$OUT/c2-ablation-report.json" > /dev/null

echo "== Compare with results/"
FAILED=0
for NAME in c0-report.json c0-report.implementations.json c1-report.json c1-paper-numbers.json \
    c2-report.json c2-paper-numbers.json c2-ablation-report.json; do
    python3 -I "$ROOT/scripts/compare_json.py" "$OUT/$NAME" "$ROOT/results/$NAME" || FAILED=1
done

if "$PY" -c 'import matplotlib' 2>/dev/null; then
    echo "== Figures 4-7"
    "$PY" -I "$ROOT/analysis/make_figures.py" "$OUT/c1-paper-numbers.json" "$OUT/figures" \
        --c2 "$OUT/c2-paper-numbers.json"
    ls "$OUT/figures"
else
    echo "== Figures skipped: $PY has no matplotlib (pip install matplotlib==3.9.4)"
fi
if [[ "$FAILED" -ne 0 ]]; then
    echo "Some regenerated files differ from results/; see above."
    exit 1
fi
echo "All regenerated files match results/ ($OUT)"
