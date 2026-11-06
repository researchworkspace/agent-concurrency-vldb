#!/usr/bin/env bash
# Create the pinned environments under PREFIX (check, apps, gen).
# Usage: bash scripts/setup_env.sh PREFIX [check|apps|gen|all]
set -euo pipefail
PREFIX=$(mkdir -p "${1:?usage: setup_env.sh PREFIX [check|apps|gen|all]}" && cd "$1" && pwd)
PART="${2:-check}"
CONDA="${CONDA:-conda}"
SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")/../src" && pwd)
field() {  # field PLAN KEY... : a value from a plan; lists print one item per line
    python3 -I -c 'import json, sys
value = json.load(open(sys.argv[1]))
for key in sys.argv[2:]:
    value = value[key]
print("\n".join(value) if isinstance(value, list) else value)' "$SRC/$1" "${@:2}"
}

setup_check() {
    if [[ ! -x "$PREFIX/check/bin/mysqld" ]]; then
        mapfile -t PACKAGES < <(field c0-plan.json environment packages; field c1-plan.json mysql_environment packages)
        "$CONDA" create -y -p "$PREFIX/check" -c conda-forge --override-channels "${PACKAGES[@]}"
    fi
    "$CONDA" list -p "$PREFIX/check" > "$PREFIX/check-environment.txt"
    "$PREFIX/check/bin/python" -I -c 'import sqlalchemy, django, psycopg, MySQLdb; print("check env: SQLAlchemy", sqlalchemy.__version__, "Django", django.__version__, "psycopg", psycopg.__version__)'
    "$PREFIX/check/bin/postgres" --version
    "$PREFIX/check/bin/mysqld" --version
}

setup_apps() {
    [[ -x "$PREFIX/check/bin/python" ]] || { echo "ERROR: run the check part first" >&2; exit 1; }
    if [[ ! -x "$PREFIX/oscar/bin/python" ]]; then
        "$PREFIX/check/bin/python" -m venv --system-site-packages "$PREFIX/oscar"
        mapfile -t PACKAGES < <(field c2-plan.json apps_environment oscar packages)
        "$PREFIX/oscar/bin/python" -m pip install --no-cache-dir "${PACKAGES[@]}"
    fi
    "$PREFIX/oscar/bin/python" -m pip freeze > "$PREFIX/oscar-environment.txt"
    COMMIT=$(field c2-plan.json apps_environment ctfd commit)
    if [[ ! -d "$PREFIX/ctfd-src/.git" ]]; then
        git clone --branch "$(field c2-plan.json apps_environment ctfd tag)" --depth 1 \
            "$(field c2-plan.json apps_environment ctfd repository)" "$PREFIX/ctfd-src"
    fi
    if [[ "$(git -C "$PREFIX/ctfd-src" rev-parse HEAD)" != "$COMMIT" ]]; then
        echo "ERROR: CTFd checkout is not at $COMMIT" >&2
        exit 1
    fi
    if [[ ! -x "$PREFIX/ctfd/bin/python" ]]; then
        "$PREFIX/check/bin/python" -m venv "$PREFIX/ctfd"
        mapfile -t EXTRA < <(field c2-plan.json apps_environment ctfd extra)
        "$PREFIX/ctfd/bin/python" -m pip install --no-cache-dir -r "$PREFIX/ctfd-src/requirements.txt" "${EXTRA[@]}"
        CHECK_SITE=$("$PREFIX/check/bin/python" -I -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
        CTFD_SITE=$("$PREFIX/ctfd/bin/python" -I -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
        printf '%s\n' "$CHECK_SITE" > "$CTFD_SITE/zz_c1_harness.pth"
    fi
    "$PREFIX/ctfd/bin/python" -m pip freeze > "$PREFIX/ctfd-environment.txt"
    "$PREFIX/oscar/bin/python" -I -c 'import sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
import c2_apps, oscar
wrong = c2_apps.verify_excerpts("oscar", Path(oscar.__file__).parent) + c2_apps.verify_excerpts("ctfd", Path(sys.argv[2]) / "CTFd")
print("excerpts verbatim" if not wrong else f"EXCERPTS DIFFER: {wrong}")
sys.exit(1 if wrong else 0)' "$SRC" "$PREFIX/ctfd-src"
}

setup_gen() {
    if [[ ! -x "$PREFIX/gen/bin/python" ]]; then
        "$CONDA" create -y -p "$PREFIX/gen" -c conda-forge --override-channels \
            "python=$(field c1-plan.json generation_environment python)" pip
        "$PREFIX/gen/bin/python" -m pip install --no-cache-dir \
            "$(field c1-plan.json generation_environment torch package)" \
            --index-url "$(field c1-plan.json generation_environment torch index_url)"
        mapfile -t PACKAGES < <(field c1-plan.json generation_environment packages)
        "$PREFIX/gen/bin/python" -m pip install --no-cache-dir "${PACKAGES[@]}"
    fi
    "$PREFIX/gen/bin/python" -m pip freeze > "$PREFIX/gen-environment.txt"
}

case "$PART" in
    check) setup_check ;;
    apps) setup_apps ;;
    gen) setup_gen ;;
    all) setup_check; setup_apps; setup_gen ;;
    *) echo "usage: setup_env.sh PREFIX [check|apps|gen|all]" >&2; exit 2 ;;
esac
echo "Environment part '$PART' ready under $PREFIX"
