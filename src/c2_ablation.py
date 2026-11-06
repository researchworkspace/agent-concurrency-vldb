"""Replay ablation over C1's remedies population, six configurations per program.

Usage: python -I c2_ablation.py run --engine E --bin DIR --c1 C1_MAIN --output DIR [--shard I --shards N]
       python -I c2_ablation.py report --c1 C1_MAIN --output DIR --save FILE
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c0_harness as harness
import c0_tasks as tasks
import c1_run

C0_CLIENT = harness.client
ARMS = ("default", "serializable retry", "request default", "request serializable",
        "request retry", "request replay")
CATEGORIES = ("retry restored", "retry unrestored", "retry violating")


def ablation_client(config: dict[str, Any], inbox: Any, outbox: Any, barrier: Any) -> None:
    if not config.get("watch", True):
        django_request, sqlalchemy_request = harness.django_request, harness.sqlalchemy_request
        harness.django_request = lambda function, retryable: django_request(function, [])
        harness.sqlalchemy_request = lambda function, engine, retryable: sqlalchemy_request(
            function, engine, [])
    C0_CLIENT(config, inbox, outbox, barrier)


def arms(plan: dict, engine: str, framework: str) -> dict[str, dict[str, Any]]:
    level = plan["engines"][engine]["framework_defaults"][framework]
    c1 = plan["engines"][engine]["arms"]
    request = {"request_transaction": True, "watch": False}
    return {
        "default": c1[c1_run.default_arm(plan, engine, framework)],
        "serializable retry": c1["serializable retry"],
        "request default": {"isolation": level, "retries": 0, **request},
        "request serializable": {"isolation": "serializable", "retries": 0, **request},
        "request retry": {"isolation": "serializable", "retries": 10, **request},
        "request replay": {**c1["request replay"], "watch": True},
    }


def population(plan: dict, engine: str, c1: Path) -> list[dict[str, Any]]:
    seeds = set(plan["hypotheses"]["confirmatory_samples"])
    trials, share = plan["generated_trials"], plan["restored_success_share"]
    folder = c1 / f"harness-{engine}"
    items = []
    for r in c1_run.load_results(folder):
        item = {k: r.get(k) for k in ("id", "source", "model", "mode", "task", "framework",
                                      "condition", "seed", "variant")}
        if r["source"] == "reference":
            item["category"] = f"reference {r['variant']}"
            item["path"] = str(folder / "implementations" / "reference"
                               / f"{r['task']}__{r['framework']}__{r['variant']}.py")
        else:
            if (r["seed"] not in seeds or tasks.TASKS[r["task"]]["hazard"] == tasks.CONTROL_CLASS
                    or not r["functional"]["passed"]):
                continue
            c = r["concurrent"]
            if not c1_run.violates(c[c1_run.default_arm(plan, engine, r["framework"])]):
                continue
            optimum = tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"]
            retry = c["serializable retry"]
            item["category"] = ("retry restored" if c1_run.restored_at(retry, trials, optimum, share)
                                else "retry violating" if c1_run.violates(retry)
                                else "retry unrestored")
            item["c1_replay_restored"] = c1_run.restored_at(c["request replay"], trials, optimum,
                                                            share)
            item["path"] = str(folder / "implementations" / r["model"]
                               / f"{r['id'].split(':', 2)[2]}.py")
        items.append(item)
    return sorted(items, key=lambda i: i["id"])


def verdicts(c: dict[str, Any], task: str, trials: int, share: float) -> dict[str, Any]:
    optimum = tasks.TASKS[task]["concurrent"]["optimal_successes"]
    return {"violating": c1_run.violates(c),
            "restored": c1_run.restored_at(c, trials, optimum, share),
            "success_share": (round(sum(c["successes"]) / (optimum * c["trials"]), 4)
                              if c["trials"] else None),
            "median_seconds": statistics.median(c["seconds"]) if c["seconds"] else None,
            "retries_per_trial": round(c["retries"] / c["trials"], 3) if c["trials"] else None}


def run(args: argparse.Namespace) -> None:
    harness.client = ablation_client
    plan = c1_run.configuration()
    engine = args.engine
    output = Path(args.output) / engine
    (output / "results").mkdir(parents=True, exist_ok=True)
    items = population(plan, engine, Path(args.c1))[args.shard::args.shards]
    items = items[: args.limit] if args.limit else items
    missing = [i["path"] for i in items if not Path(i["path"]).is_file()]
    if missing:
        raise FileNotFoundError(f"{len(missing)} programs missing, e.g. {missing[0]}")
    trials, share = plan["generated_trials"], plan["restored_success_share"]
    started = time.time()
    root = Path(args.scratch or tempfile.gettempdir())
    support = output / f"shard-{args.shard}"
    with c1_run.server(engine, Path(args.bin), root, Path(args.sockets), plan,
                       output / f"shard-{args.shard}.log") as db:
        environment = {"server_version": c1_run.server_version(engine, db),
                       "python": sys.version.split()[0]}
        for item in items:
            target = output / "results" / f"{item['id'].replace(':', '__')}.json"
            if target.exists():
                continue
            config = c1_run.base_config(item, engine, db, support, plan)
            result: dict[str, Any] = {**item, "environment": environment, "arms": {}}
            for name, arm in arms(plan, engine, item["framework"]).items():
                summary = harness.concurrent(db, item, {**config, "watch": arm.get("watch", True)},
                                             arm, trials, c1_run.settings(plan))
                result["arms"][name] = {"arm": arm, "summary": summary,
                                        **verdicts(summary, item["task"], trials, share)}
            harness.atomic_write(target, result)
            print(f"{item['id']} {item['category']}: restored "
                  f"{[n for n, a in result['arms'].items() if a['restored']]}", flush=True)
    harness.atomic_write(output / f"completion-{args.shard}.json",
                         {"items": len(items), "seconds": round(time.time() - started, 1)})


def report(args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for engine in ("postgresql", "mysql"):
        folder = Path(args.output) / engine / "results"
        results = [json.loads(p.read_text("utf-8")) for p in sorted(folder.glob("*.json"))]
        if not results:
            continue
        cells: dict[str, Any] = {}
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            groups[r["category"]].append(r)
        for category, rows in sorted(groups.items()):
            cell: dict[str, Any] = {"programs": len(rows)}
            for arm in ARMS:
                a = [r["arms"][arm] for r in rows]
                ratios = [x["median_seconds"] / r["arms"]["default"]["median_seconds"]
                          for x, r in zip(a, rows)
                          if x["median_seconds"] and r["arms"]["default"]["median_seconds"]]
                cell[arm] = {
                    "violating": sum(x["violating"] for x in a),
                    "restored": sum(x["restored"] for x in a),
                    "kept_unrestored": sum(not x["violating"] and not x["restored"] for x in a),
                    "with_exceptions": sum(bool(x["summary"]["exceptions"]) for x in a),
                    "median_success_share": statistics.median(
                        [x["success_share"] for x in a if x["success_share"] is not None] or [0]),
                    "median_time_ratio": round(statistics.median(ratios), 3) if ratios else None,
                }
            if "c1_replay_restored" in rows[0]:
                cell["c1_request_replay_restored"] = sum(r["c1_replay_restored"] for r in rows)
                cell["c1_agreement_request_replay"] = sum(
                    r["c1_replay_restored"] == r["arms"]["request replay"]["restored"] for r in rows)
            cells[category] = cell
        first = Counter(next((arm for arm in ARMS if r["arms"][arm]["restored"]), "none")
                        for r in results if r["category"] in CATEGORIES)
        out[engine] = {"results": len(results), "categories": cells,
                       "first_restoring_arm": dict(first),
                       "server_version": results[0]["environment"]["server_version"]}
    if args.save:
        harness.atomic_write(Path(args.save), out)
    print(json.dumps(out, indent=1))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("--engine", choices=("postgresql", "mysql"), required=True)
    r.add_argument("--bin", required=True, help="directory with the server binaries")
    r.add_argument("--shard", type=int, default=0)
    r.add_argument("--shards", type=int, default=1)
    r.add_argument("--limit", type=int)
    r.add_argument("--scratch")
    r.add_argument("--sockets", default="/tmp", help="short directory for server sockets")
    p = sub.add_parser("report")
    p.add_argument("--save")
    for q in (r, p):
        q.add_argument("--c1", required=True, help="C1's main output folder")
        q.add_argument("--output", required=True)
    args = parser.parse_args()
    {"run": run, "report": report}[args.mode](args)


if __name__ == "__main__":
    main()
