#!/usr/bin/env bash
# Recompute the paper's numbers and figures from the data archive and compare them with results/.
# Usage: bash scripts/reproduce_numbers.sh DATA OUT
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DATA=$(cd "${1:?usage: reproduce_numbers.sh DATA OUT}" && pwd)
OUT=$(mkdir -p "${2:?usage: reproduce_numbers.sh DATA OUT}" && cd "$2" && pwd)
PY="${PYTHON:-python3}"
SRC="$ROOT/src"
BASE="$DATA/base"
BENCHMARK="$DATA/benchmark"
EXTENDED="$DATA/extended"
DIAGNOSTICS="$DATA/diagnostics"

echo "== Base report: the two Qwen coders on PostgreSQL (the harness version that checked them)"
BASE_SRC="$OUT/base-src"
mkdir -p "$BASE_SRC"
cp "$SRC/tasks.py" "$SRC/plan-base.json" "$SRC/earlier/harness.py" "$BASE_SRC/"
"$PY" -I "$BASE_SRC/harness.py" report --output "$BASE/harness" \
    --generations "$BASE/generations/qwen25coder32b" --generations "$BASE/generations/qwen3coder30b" \
    --save "$OUT/report-base.json" > "$OUT/report-base.txt"

echo "== Benchmark report: criteria H1-H7"
"$PY" -I "$SRC/checks.py" report --runs "$BENCHMARK/harness-postgresql" "$BENCHMARK/harness-mysql" \
    --sweeps "$BENCHMARK"/sweep-*/ --save "$OUT/report-benchmark.json" > "$OUT/report-benchmark.txt"

echo "== Benchmark numbers: RQ1-RQ5"
"$PY" -I "$SRC/numbers_benchmark.py" "$BENCHMARK" "$OUT/report-base.implementations.json" \
    "$OUT/numbers-benchmark.json" > "$OUT/numbers-benchmark.txt"

echo "== Extended report: criteria H8-H12"
"$PY" -I "$SRC/report_extended.py" report --extended "$EXTENDED" --benchmark "$BENCHMARK" \
    --save "$OUT/report-extended.json" > "$OUT/report-extended.txt"

echo "== Extended numbers: loops, applications, sustained load"
"$PY" -I "$SRC/numbers_extended.py" "$EXTENDED" "$BENCHMARK" "$OUT/report-base.implementations.json" \
    "$OUT/numbers-extended.json" > "$OUT/numbers-extended.txt"

echo "== Breakdowns and sensitivity checks"
"$PY" -I "$SRC/numbers_breakdowns.py" "$BENCHMARK" "$EXTENDED" "$OUT/report-base.implementations.json" \
    "$OUT/numbers-breakdowns.json" > /dev/null

echo "== Replay ablation report"
"$PY" -I "$SRC/replay_ablation.py" report --benchmark "$BENCHMARK" --output "$DIAGNOSTICS/replay-ablation" \
    --save "$OUT/report-replay-ablation.json" > /dev/null

echo "== Compare with results/"
FAILED=0
for NAME in report-base.json report-base.implementations.json report-benchmark.json numbers-benchmark.json \
    report-extended.json numbers-extended.json numbers-breakdowns.json report-replay-ablation.json; do
    python3 -I "$ROOT/scripts/compare_json.py" "$OUT/$NAME" "$ROOT/results/$NAME" || FAILED=1
done

if "$PY" -c 'import matplotlib' 2>/dev/null; then
    echo "== Figures 4-7"
    "$PY" -I "$ROOT/analysis/make_figures.py" "$OUT/numbers-benchmark.json" "$OUT/figures" \
        --extended "$OUT/numbers-extended.json"
    ls "$OUT/figures"
else
    echo "== Figures skipped: $PY has no matplotlib (pip install matplotlib==3.9.4)"
fi
if [[ "$FAILED" -ne 0 ]]; then
    echo "Some regenerated files differ from results/; see above."
    exit 1
fi
echo "All regenerated files match results/ ($OUT)"
