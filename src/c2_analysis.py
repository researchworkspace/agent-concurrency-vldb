"""Criteria H8-H12 and the sustained-load tables.

Usage: python -I c2_analysis.py report --c2 C2_MAIN --c1 C1_MAIN [--load ENGINE=FOLDER] --save FILE
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
import c0_tasks as tasks
import c1_run
import c2_apps as apps
import c2_feedback

ENGINES = ("postgresql", "mysql")


def load(folder: Path) -> list[dict[str, Any]]:
    return c1_run.load_results(folder) if (folder / "results").is_dir() else []


def records(folder: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted((folder / "generations").glob("*/generations/*.json")):
        rows.append({**json.loads(path.read_text("utf-8")), "folder": path.parent.parent.name})
    return rows


def share(hits: int, total: int, z: float) -> dict[str, Any]:
    return c1_run.proportion(hits, total, z)


def benchmark_rows(plan: dict, engine: str, results: list[dict], seeds: set[int] | None):
    rows = []
    trials, success = plan["generated_trials"], plan["restored_success_share"]
    for r in results:
        if r["source"] != "generated" or (seeds and r["seed"] not in seeds):
            continue
        row = {k: r.get(k) for k in ("model", "mode", "task", "framework", "condition", "seed",
                                     "loop_passed", "repair_rounds")}
        row["test"] = tasks.TASKS[r["task"]]["hazard"] != tasks.CONTROL_CLASS
        row["functional"] = r["functional"]["passed"]
        if row["functional"]:
            c = r["concurrent"]
            optimum = tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"]
            row["violating"] = c1_run.violates(c[c1_run.default_arm(plan, engine, r["framework"])])
            row["retry_restored"] = c1_run.restored_at(c["serializable retry"], trials, optimum,
                                                       success)
            row["replay_restored"] = c1_run.restored_at(c["request replay"], trials, optimum,
                                                        success)
        rows.append(row)
    return rows


def app_rows(plan: dict, engine: str, results: list[dict], seeds: set[int] | None):
    import c2_appharness as harness

    rows = []
    trials, success = plan["generated_trials"], plan["restored_success_share"]
    arms = plan["engines"][engine]["arms"]
    for r in results:
        if r["source"] != "generated" or (seeds and r["seed"] not in seeds):
            continue
        row = {k: r.get(k) for k in ("model", "mode", "task", "handler", "app", "condition",
                                     "seed", "loop_passed", "repair_rounds")}
        row["functional"] = r["functional"]["passed"]
        if row["functional"]:
            c = r["concurrent"]
            level = harness.default_level(plan, engine, r["framework"])
            default = next(n for n, a in arms.items() if a["isolation"] == level and not a["retries"])
            optimum = apps.RULES[r["task"]]["optimal_successes"]
            row["violating"] = c1_run.violates(c[default])
            row["retry_restored"] = c1_run.restored_at(c["serializable retry"], trials, optimum,
                                                       success)
            row["replay_restored"] = c1_run.restored_at(c["request replay"], trials, optimum,
                                                        success)
        rows.append(row)
    return rows


def violating_share(rows: list[dict], z: float) -> dict[str, Any]:
    functional = [r for r in rows if r["functional"]]
    return share(sum(r["violating"] for r in functional), len(functional), z)


def test_use(record: dict[str, Any]) -> str:
    tests = record.get("tests") or []
    if not tests:
        return "never_asked" if not any(h.get("test_requested") for h in record["history"]) \
            else "no_test"
    first = tests[0]["classification"]
    loaded = next((not h["selftest"].get("load_error") for h in record["history"]
                   if h.get("selftest")), True)
    if not first.get("parsed") or not first.get("defines_test"):
        return "no_test"
    if not loaded:
        return "load_error"
    if not first.get("concurrent"):
        return "not_concurrent"
    if not first.get("assertions"):
        return "no_assertion"
    return "concurrent_with_assertion"


def pilot(args: argparse.Namespace) -> dict[str, Any]:
    plan = c1_run.configuration()
    z = plan["hypotheses"]["confidence_z"]
    out = Path(args.c2)
    gen = records(out)
    report: dict[str, Any] = {}
    for engine in ENGINES:
        results = []
        for app in apps.APPS:
            results += load(out / f"harness-e2-{app}-{engine}")
        rows = app_rows(plan, engine, results, None)
        single = [r for r in rows if r["mode"] == "single"]
        programs = {(r["model"], r["handler"], r["condition"], r["seed"]): r for r in single}
        by_model: dict[str, list] = defaultdict(list)
        for r in programs.values():
            by_model[r["model"]].append(r["functional"])
        report[f"e2_functional_rate_{engine}"] = {
            m: {"functional": sum(v), "programs": len(v), "rate": round(sum(v) / len(v), 3)}
            for m, v in sorted(by_model.items())}
        report[f"e2_violating_{engine}"] = {
            f"{rule}:{cond}": violating_share([r for r in single if r["task"] == rule
                                               and r["condition"] == cond], z)
            for rule in apps.RULES for cond in ("plain", "hint")}
        report[f"e2_loops_{engine}"] = {
            f"{mode}:{rule}": violating_share([r for r in rows if r["mode"] == mode
                                               and r["task"] == rule], z)
            for mode in ("loop_seq", "loop_conc", "loop_self") for rule in apps.RULES}
    for suite in ("benchmark", "apps"):
        selfs = [g for g in gen if g["mode"] == "loop_self"
                 and (g["task"] in apps.HANDLERS) == (suite == "apps")]
        report[f"loop_self_{suite}"] = {
            "answers": len(selfs),
            "test_use": dict(Counter(test_use(g) for g in selfs)),
            "loop_passed": sum(bool(g["loop_passed"]) for g in selfs),
            "repair_rounds": dict(Counter(g["repair_rounds"] for g in selfs)),
            "test_revised": sum(len(g.get("tests") or []) > 1 for g in selfs),
            "per_model_passed": dict(Counter(g["model"] for g in selfs if g["loop_passed"])),
        }
    mys = [g for g in gen if g["mode"] == "loop_conc_my"]
    report["loop_conc_my"] = {"answers": len(mys),
                              "loop_passed": sum(bool(g["loop_passed"]) for g in mys),
                              "repair_rounds": dict(Counter(g["repair_rounds"] for g in mys))}
    for engine in ENGINES:
        rows = benchmark_rows(plan, engine, load(out / f"harness-e1-{engine}"), None)
        report[f"e1_{engine}"] = {
            mode: {
                "violating_test_class": violating_share(
                    [r for r in rows if r["mode"] == mode and r["test"]], z),
                "passed_and_violating": violating_share(
                    [r for r in rows if r["mode"] == mode and r["test"] and r["loop_passed"]], z),
            } for mode in ("loop_self", "loop_conc_my")}
    report["e3"] = load_summary(out)
    return report


LOAD_FOLDERS: dict[str, str] = {}


def load_folder(engine: str) -> str:
    return LOAD_FOLDERS.get(engine, f"load-{engine}")


def load_results(folder: Path) -> list[dict[str, Any]]:
    paths = sorted(folder.glob("results/*.json")) + sorted(folder.glob("shard-*/results/*.json"))
    return [json.loads(p.read_text("utf-8")) for p in paths]


def completed_throughput(r: dict[str, Any]) -> float | None:
    if r.get("throughput") is None or not r.get("requests"):
        return None
    return r["throughput"] * (r["requests"] - (r.get("exceptions") or 0)) / r["requests"]


def failed_share(r: dict[str, Any]) -> float:
    return (r.get("exceptions") or 0) / r["requests"] if r.get("requests") else 0.0


def load_summary(out: Path) -> dict[str, Any]:
    summary = {}
    for engine in ENGINES:
        results = load_results(out / load_folder(engine)) if (out / load_folder(engine)).is_dir() else []
        if not results:
            continue
        units: dict[tuple, list] = defaultdict(list)
        for r in results:
            units[(r["id"], r["arm"], r["condition"]["name"])].append(r)
        spreads = []
        for reps in units.values():
            values = [v for v in map(completed_throughput, reps) if v]
            if len(values) >= 2 and statistics.mean(values):
                spreads.append(statistics.pstdev(values) / statistics.mean(values))
        summary[engine] = {
            "runs": len(results),
            "units": len(units),
            "median_cv": round(statistics.median(spreads), 4) if spreads else None,
            "p90_cv": round(sorted(spreads)[int(0.9 * (len(spreads) - 1))], 4) if spreads else None,
            "runs_with_exceptions": sum(bool(r.get("exceptions")) for r in results),
            "max_failed_share": round(max(map(failed_share, results)), 4),
            "refused_connections": sum(r.get("refused_connections") or 0 for r in results),
            "runs_violating": sum(bool(r.get("violations")) for r in results),
            "hung": sum(bool(r.get("hung")) for r in results),
            "setup_errors": sum(bool(r.get("setup_error")) for r in results),
        }
    return summary


def load_tables(out: Path) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for engine in ENGINES:
        results = load_results(out / load_folder(engine))
        mean: dict[tuple, float] = {}
        failed: dict[tuple, float] = {}
        grouped: dict[tuple, list] = defaultdict(list)
        for r in results:
            if completed_throughput(r) is not None:
                grouped[(r["id"], r["population"], r["task"], r["framework"], r["arm"],
                         r["condition"]["name"])].append(r)
        for key, reps in grouped.items():
            mean[key] = statistics.mean(completed_throughput(r) for r in reps)
            failed[key] = statistics.mean(failed_share(r) for r in reps)
        baseline = {(k[2], k[3], k[5]): v for k, v in mean.items()
                    if k[1] == "reference" and "replay" not in k[4]}
        cells: dict[tuple, list] = defaultdict(list)
        for key, value in mean.items():
            pid, population, task, framework, arm, condition = key
            base = baseline.get((task, framework, condition))
            if base:
                cells[(condition, population, "replay" if "replay" in arm else "default")].append(
                    (value / base, failed[key]))
        rows = {}
        for (condition, population, arm), pairs in sorted(cells.items()):
            ratios = [p[0] for p in pairs]
            shares = [p[1] for p in pairs]
            q = statistics.quantiles(ratios, n=4) if len(ratios) >= 2 else [ratios[0]] * 3
            rows[f"{condition}:{population}:{arm}"] = {
                "programs": len(ratios), "median": round(statistics.median(ratios), 3),
                "q1": round(q[0], 3), "q3": round(q[2], 3),
                "failed_median": round(statistics.median(shares), 4),
                "failed_max": round(max(shares), 4),
                "programs_with_failed": sum(s > 0 for s in shares)}
        tables[engine] = rows
    return tables


def report(args: argparse.Namespace) -> dict[str, Any]:
    plan = c1_run.configuration()
    hyp = c2_feedback.configuration()["hypotheses"]
    z = hyp["confidence_z"]
    seeds = set(hyp["confirmatory_samples"])
    c2, c1 = Path(args.c2), Path(args.c1)
    out: dict[str, Any] = {"draft_thresholds": hyp.get("draft", False)}

    h = hyp["h8"]
    c1_rows = benchmark_rows(plan, h["engine"], load(c1 / f"harness-{h['engine']}"), seeds)
    c2_rows = benchmark_rows(plan, h["engine"], load(c2 / f"harness-e1-{h['engine']}"), seeds)
    pool = [r for r in c1_rows + c2_rows if r["test"] and r["condition"] == "plain"
            and r["mode"] in ("loop_seq", "loop_conc", "loop_self")]

    def closure(rows: list[dict]) -> float | None:
        v = {}
        for mode in ("loop_seq", "loop_conc", "loop_self"):
            functional = [r for r in rows if r["mode"] == mode and r["functional"]]
            if not functional:
                return None
            v[mode] = sum(r["violating"] for r in functional) / len(functional)
        gap = v["loop_seq"] - v["loop_conc"]
        return (v["loop_seq"] - v["loop_self"]) / gap if gap > 0 else None

    units: dict[tuple, list] = defaultdict(list)
    for r in pool:
        units[(r["model"], r["task"], r["framework"], r["seed"])].append(r)
    keys = sorted(units)
    rng = random.Random(hyp["bootstrap"]["seed"])
    draws = []
    for _ in range(hyp["bootstrap"]["replicates"]):
        sample = [r for k in (rng.choice(keys) for _ in keys) for r in units[k]]
        value = closure(sample)
        if value is not None:
            draws.append(value)
    draws.sort()
    estimate = closure(pool)
    out["h8"] = {
        "shares": {m: violating_share([r for r in pool if r["mode"] == m], z)
                   for m in ("loop_seq", "loop_conc", "loop_self")},
        "closure": estimate,
        "interval": [draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]]
        if draws else None,
        "holds": estimate is not None and estimate >= h["closure"],
    }
    h = hyp["h9"]
    passed = [r for r in c2_rows if r["mode"] == "loop_self" and r["test"] and r["loop_passed"]]
    s = violating_share(passed, z)
    out["h9"] = {**s, "holds": s["share"] is not None and s["share"] >= h["share"]
                 and s["lower"] >= h["lower_bound"]}
    h = hyp["h10"]
    rows = benchmark_rows(plan, h["engine"], load(c2 / f"harness-e1-{h['engine']}"), seeds)
    s = violating_share([r for r in rows if r["mode"] == "loop_conc_my" and r["test"]
                         and r["loop_passed"]], z)
    out["h10"] = {**s, "holds": s["n"] >= h["min_implementations"] and s["share"] is not None
                  and s["share"] <= h["share_max"] and s["upper"] <= h["upper_bound_max"]}
    h = hyp["h11"]
    e2: dict[str, list] = {}
    for engine in ENGINES:
        results = []
        for app in apps.APPS:
            results += load(c2 / f"harness-e2-{app}-{engine}")
        e2[engine] = app_rows(plan, engine, results, seeds)
    per_rule = {rule: violating_share([r for r in e2[h["engine"]] if r["mode"] == "single"
                                       and r["condition"] == h["condition"]
                                       and r["task"] == rule], z) for rule in apps.RULES}
    meeting = [rule for rule, s in per_rule.items()
               if s["n"] >= h["min_rule_implementations"] and s["share"] >= h["share"]
               and s["lower"] >= h["lower_bound"]]
    out["h11"] = {"rules": per_rule, "meeting": meeting, "holds": len(meeting) >= h["rules"]}
    h = hyp["h12"]
    out["h12"] = {}
    for engine in ENGINES:
        violating = [r for r in e2[engine] if r["functional"] and r["violating"]]
        s = share(sum(r["replay_restored"] for r in violating), len(violating), z)
        retry = share(sum(r["retry_restored"] for r in violating), len(violating), z)
        out["h12"][engine] = {**s, "retry_restored": retry,
                              "holds": s["n"] >= h["min_implementations"]
                              and s["share"] >= h["share"]}
    out["h12"]["holds"] = all(out["h12"][e]["holds"] for e in ENGINES)
    out["e2_descriptive"] = {
        engine: {f"{mode}:{cond}:{rule}": violating_share(
            [r for r in e2[engine] if r["mode"] == mode and r["condition"] == cond
             and r["task"] == rule], z)
            for mode, cond in (("single", "plain"), ("single", "hint"), ("loop_seq", "plain"),
                               ("loop_conc", "plain"), ("loop_self", "plain"))
            for rule in apps.RULES} for engine in ENGINES}
    out["e3"] = {"spread": load_summary(c2), "relative_throughput": load_tables(c2)}
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("pilot")
    p.add_argument("--c2", required=True, help="the C2 pilot output")
    p.add_argument("--save")
    r = commands.add_parser("report")
    r.add_argument("--c2", required=True, help="the C2 main output")
    r.add_argument("--c1", required=True, help="the C1 main output")
    r.add_argument("--save")
    r.add_argument("--load", action="append", default=[], metavar="ENGINE=FOLDER",
                   help="E3 folder of an engine, if not load-<engine>")
    args = parser.parse_args()
    for item in getattr(args, "load", []):
        engine, _, folder = item.partition("=")
        if engine not in ENGINES or not folder or "/" in folder:
            raise SystemExit(f"--load takes ENGINE=FOLDER, got {item!r}")
        LOAD_FOLDERS[engine] = folder
    value = pilot(args) if args.command == "pilot" else report(args)
    text = json.dumps(value, indent=1, default=str)
    if args.save:
        Path(args.save).write_text(text + "\n", "utf-8")
    print(text)


if __name__ == "__main__":
    main()
