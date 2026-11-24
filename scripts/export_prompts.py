"""Write every first-turn prompt of the study to prompts/.

Usage: python3 scripts/export_prompts.py [OUT_DIR]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import apps
import selftest
import tasks


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "prompts"
    runs = json.loads((ROOT / "src" / "plan-extended.json").read_text("utf-8"))["loop_self"]["self_test_runs"]
    written = 0
    for task in tasks.TASKS:
        for framework in tasks.FRAMEWORKS:
            for condition in tasks.CONDITIONS:
                path = out / "benchmark" / f"{task}__{framework}__{condition}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(tasks.prompt(task, framework, condition), "utf-8")
                written += 1
        path = out / "loops" / f"self_test_request__{task}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(selftest.test_request(task, runs), "utf-8")
        written += 1
    for handler in apps.HANDLERS:
        for condition in apps.CONDITIONS:
            path = out / "apps" / f"{handler}__{condition}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(apps.prompt(handler, condition), "utf-8")
            written += 1
        path = out / "loops" / f"self_test_request__{handler}.txt"
        path.write_text(selftest.app_test_request(handler, runs), "utf-8")
        written += 1
    print(f"{written} prompts written to {out}")


if __name__ == "__main__":
    main()
