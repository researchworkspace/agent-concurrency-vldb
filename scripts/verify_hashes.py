"""Check src/ against src/SHA256SUMS and against the code hashes in the released manifests.

Usage: python3 scripts/verify_hashes.py [RUN_FOLDER ...]
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

FIELDS = {
    "plan_sha256": ("c0-plan.json", "c1-plan.json", "c2-plan.json"),
    "c1_plan_sha256": ("c1-plan.json",),
    "c2_plan_sha256": ("c2-plan.json",),
    "tasks_sha256": ("c0_tasks.py",),
    "harness_sha256": ("c0_harness.py",),
    "runner_sha256": ("c1_run.py",),
    "mysql_sha256": ("c1_mysql.py",),
    "generator_sha256": ("c0_generate.py", "c1_generate.py"),
    "feedback_sha256": ("c1_feedback.py",),
    "c2_generator_sha256": ("c2_generate.py",),
    "c2_feedback_sha256": ("c2_feedback.py",),
    "c2_selftest_sha256": ("c2_selftest.py",),
    "c2_apps_sha256": ("c2_apps.py",),
    "apps_sha256": ("c2_apps.py",),
    "c2_appharness_sha256": ("c2_appharness.py",),
    "appharness_sha256": ("c2_appharness.py",),
    "load_sha256": ("c2_load.py",),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def known() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in sorted(SRC.rglob("*")):
        if p.is_file() and p.suffix in (".py", ".json"):
            out.setdefault(p.name, []).append(sha(p))
    return out


def check_sums() -> int:
    bad = 0
    for line in (SRC / "SHA256SUMS").read_text("utf-8").splitlines():
        digest, name = line.split(maxsplit=1)
        if sha(SRC / name) != digest:
            print(f"MODIFIED  src/{name}")
            bad += 1
    print(f"src/SHA256SUMS: {'OK' if not bad else f'{bad} files differ'}")
    return bad


def walk(manifest: dict, prefix: str = ""):
    for key, value in manifest.items():
        if isinstance(value, dict):
            yield from walk(value, f"{prefix}{key}.")
        elif isinstance(value, str) and key in FIELDS:
            yield f"{prefix}{key}", key, value


def check_data(data: Path) -> int:
    versions = known()
    matched: Counter = Counter()
    missing: list[str] = []
    for path in sorted(data.rglob("manifest.json")):
        for full, key, value in walk(json.loads(path.read_text("utf-8"))):
            names = [n for n in FIELDS[key] if value in versions.get(n, [])]
            if names:
                matched[(key, names[0], value[:12])] += 1
            else:
                missing.append(f"{path.relative_to(data)}: {full}={value[:12]}")
    for (key, name, digest), count in sorted(matched.items()):
        print(f"OK  {key:22} {name:24} {digest}  ({count} manifests)")
    for line in missing:
        print(f"NO MATCH  {line}")
    print(f"{sum(matched.values())} hash fields match shipped code; {len(missing)} do not")
    return len(missing)


def main() -> None:
    bad = check_sums()
    for folder in sys.argv[1:]:
        print(f"== {folder}")
        bad += check_data(Path(folder))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
