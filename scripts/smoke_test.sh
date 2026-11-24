#!/usr/bin/env bash
# Reference gates on both engines, a released-program re-check and the application references.
# Usage: bash scripts/smoke_test.sh PREFIX OUT [DATA]
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PREFIX=$(cd "${1:?usage: smoke_test.sh PREFIX OUT [DATA]}" && pwd)
OUT=$(mkdir -p "${2:?usage: smoke_test.sh PREFIX OUT [DATA]}" && cd "$2" && pwd)
DATA="${3:-}"
SCRATCH="${SCRATCH:-${TMPDIR:-/tmp}}"
PY="$PREFIX/check/bin/python"
BIN="$PREFIX/check/bin"
FAILED=0

echo "== 1. code hashes"
if [[ -n "$DATA" ]]; then
    python3 -I "$ROOT/scripts/verify_hashes.py" "$DATA"/{base,benchmark,extended} || FAILED=1
else
    python3 -I "$ROOT/scripts/verify_hashes.py" || FAILED=1
fi

echo "== 2. benchmark references"
for ENGINE in postgresql mysql; do
    "$PY" -I "$ROOT/src/checks.py" run --engine "$ENGINE" --output "$OUT/references-$ENGINE" \
        --bin "$BIN" --scratch "$SCRATCH" > "$OUT/references-$ENGINE.txt"
    "$PY" -I "$ROOT/src/checks.py" summary --output "$OUT/references-$ENGINE" \
        --save "$OUT/references-$ENGINE-summary.json" > /dev/null
    "$PY" -I -c 'import json, sys
r = json.load(open(sys.argv[1]))
g = r["reference_gates"]
ok = g["correct_clean"] and not g["buggy_missed"] and g["references"] == 32
print(r["engine"], json.dumps(g), "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)' "$OUT/references-$ENGINE-summary.json" || FAILED=1
done

if [[ -n "$DATA" ]]; then
    echo "== 3. released programs, re-checked on PostgreSQL"
    python3 -I "$ROOT/scripts/sample_check.py" make --data "$DATA" --engine postgresql \
        --out "$OUT/sample-postgresql"
    "$PY" -I "$ROOT/src/checks.py" run --engine postgresql --output "$OUT/sample-postgresql-run" \
        --bin "$BIN" --scratch "$SCRATCH" --generations "$OUT/sample-postgresql" \
        > "$OUT/sample-postgresql-run.txt"
    python3 -I "$ROOT/scripts/sample_check.py" compare --data "$DATA" --engine postgresql \
        --run "$OUT/sample-postgresql-run" || FAILED=1
fi

if [[ -x "$PREFIX/oscar/bin/python" && -x "$PREFIX/ctfd/bin/python" ]]; then
    echo "== 4. application references"
    for APP in oscar ctfd; do
        APY="$PREFIX/$APP/bin/python"
        for ENGINE in postgresql mysql; do
            "$APY" -I "$ROOT/src/app_harness.py" run --app "$APP" --engine "$ENGINE" \
                --output "$OUT/references-$APP-$ENGINE" --bin "$BIN" --scratch "$SCRATCH" \
                --ctfd-source "$PREFIX/ctfd-src" > "$OUT/references-$APP-$ENGINE.txt" 2>&1
            "$APY" -I "$ROOT/src/app_harness.py" gates --output "$OUT/references-$APP-$ENGINE" \
                | tail -1 | tee "$OUT/references-$APP-$ENGINE-gates.json" | python3 -I -c 'import json, sys
g = json.loads(sys.stdin.read())
ok = g["correct_clean"] and not g["buggy_missed"] and not g["correct_failing"]
print(g["app"], g["engine"], json.dumps(g), "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)' || FAILED=1
        done
    done
else
    echo "== 4. application references skipped (run: bash scripts/setup_env.sh $PREFIX apps)"
fi

if [[ "$FAILED" -ne 0 ]]; then
    echo "SMOKE TEST FAILED; see the lines above and $OUT"
    exit 1
fi
echo "SMOKE TEST PASSED ($OUT)"
