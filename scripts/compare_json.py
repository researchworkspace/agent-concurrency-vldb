"""Compare a regenerated JSON file with the shipped one.

Usage: python3 scripts/compare_json.py REGENERATED SHIPPED
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

RUN = re.compile(r".*?/(agent-concurrency-[^/]+/.*)$")


def normal(value):
    if isinstance(value, str):
        match = RUN.match(value)
        return match.group(1) if match else value
    return value


def diff(a, b, where: str, out: list[str]) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b), key=str):
            if key not in a or key not in b:
                out.append(f"{where}/{key}: only in {'shipped' if key in b else 'regenerated'}")
            else:
                diff(a[key], b[key], f"{where}/{key}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{where}: length {len(a)} vs shipped {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{where}[{i}]", out)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12):
            out.append(f"{where}: {a} vs shipped {b}")
    elif normal(a) != normal(b):
        out.append(f"{where}: {str(a)[:80]!r} vs shipped {str(b)[:80]!r}")


def main() -> None:
    mine, shipped = (json.loads(Path(p).read_text("utf-8")) for p in sys.argv[1:3])
    out: list[str] = []
    diff(mine, shipped, "", out)
    for line in out[:50]:
        print(f"  {line}")
    if len(out) > 50:
        print(f"  ... {len(out) - 50} more")
    print(f"{Path(sys.argv[1]).name}: {'identical' if not out else f'{len(out)} differences'}")
    sys.exit(1 if out else 0)


if __name__ == "__main__":
    main()
