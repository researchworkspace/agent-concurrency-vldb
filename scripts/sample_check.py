"""Re-check a sample of released programs and compare the verdicts with the released ones.

Usage: python3 scripts/sample_check.py make --data DATA --engine E --out DIR [--per-task N]
       python3 scripts/sample_check.py compare --data DATA --engine E --run RUN_OUTPUT
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import c0_tasks as tasks
import c1_run

MODELS = ("qwen38_27b", "devstral2_24b", "gptoss120b", "seedcoder8b")


def released(data: Path, engine: str) -> dict[str, dict]:
    folder = data / "agent-concurrency-c1-main" / f"harness-{engine}"
    return {r["id"]: r for r in c1_run.load_results(folder)}


def verdicts(plan: dict, r: dict) -> dict[str, tuple[bool, bool]]:
    optimum = tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"]
    trials = plan[f"{r['source']}_trials"]
    share = plan["restored_success_share"]
    return {arm: (c1_run.violates(c), c1_run.restored_at(c, trials, optimum, share))
            for arm, c in r.get("concurrent", {}).items()}


def make(args: argparse.Namespace) -> None:
    plan = c1_run.configuration()
    data, out = Path(args.data), Path(args.out)
    rows = released(data, args.engine)
    chosen: dict[str, list[str]] = {}
    order = list(tasks.TASKS)
    rank = lambda item: ((MODELS.index(item[1]["model"]) - order.index(item[1]["task"])) % len(MODELS)
                         if item[1].get("model") in MODELS and item[1]["task"] in order else 0, item[0])
    for rid, r in sorted(rows.items(), key=rank):
        if (r["source"] != "generated" or r.get("mode", "single") != "single"
                or r["model"] not in MODELS or not r["functional"]["passed"]
                or r["condition"] != "plain"):
            continue
        default = c1_run.default_arm(plan, args.engine, r["framework"])
        c = r["concurrent"][default]
        if c["violating_trials"] < 15 or not verdicts(plan, r)["request replay"][1]:
            continue
        if len(chosen.setdefault(r["task"], [])) < args.per_task:
            chosen[r["task"]].append(rid)
    (out / "generations").mkdir(parents=True, exist_ok=True)
    for task, ids in sorted(chosen.items()):
        for rid in ids:
            _, model, stem = rid.split(":", 2)
            source = data / "agent-concurrency-c1-main" / "generations" / model / "generations" / f"{stem}.json"
            shutil.copy2(source, out / "generations" / f"{stem}__{model}.json")
            print(f"{task:16} {rid}")
    print(f"{sum(map(len, chosen.values()))} programs in {out / 'generations'}")


def compare(args: argparse.Namespace) -> None:
    plan = c1_run.configuration()
    rows = released(Path(args.data), args.engine)
    differ = checked = 0
    for r in c1_run.load_results(Path(args.run)):
        if r["source"] != "generated":
            continue
        stem = r["id"].split(":", 2)[2].removesuffix(f"__{r['model']}")
        old = rows[f"generated:{r['model']}:{stem}"]
        mine, theirs = verdicts(plan, r), verdicts(plan, old)
        if r["functional"]["passed"] != old["functional"]["passed"]:
            print(f"DIFFERS  {stem}: functional {r['functional']['passed']} (released "
                  f"{old['functional']['passed']})")
            differ += 1
        for arm in sorted(theirs):
            checked += 1
            if mine.get(arm) != theirs[arm]:
                differ += 1
                print(f"DIFFERS  {stem} [{arm}]: violating/restored {mine.get(arm)} "
                      f"(released {theirs[arm]})")
    print(f"{checked} program-arm verdicts compared; {differ} differ")
    sys.exit(1 if differ else 0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    m = sub.add_parser("make")
    m.add_argument("--out", required=True)
    m.add_argument("--per-task", type=int, default=1)
    c = sub.add_parser("compare")
    c.add_argument("--run", required=True)
    for p in (m, c):
        p.add_argument("--data", required=True)
        p.add_argument("--engine", choices=("postgresql", "mysql"), default="postgresql")
    args = parser.parse_args()
    {"make": make, "compare": compare}[args.mode](args)


if __name__ == "__main__":
    main()
