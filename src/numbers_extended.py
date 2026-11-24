"""Paper numbers of the extended stage (self-test and MySQL-checked loops, applications, sustained load).

Usage: python numbers_extended.py EXTENDED_RUN BENCHMARK_RUN report-base.implementations.json OUT.json [--load postgresql=FOLDER]
"""

import collections as C
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks as T
import checks as R
import report_extended as A
import apps as APPS

args, argv = [], iter(sys.argv[1:])
for a in argv:
    if a == "--load" or a.startswith("--load="):
        engine, _, folder = (next(argv) if a == "--load" else a.split("=", 1)[1]).partition("=")
        A.LOAD_FOLDERS[engine] = folder
    else:
        args.append(a)
ext, bench, base_path, out_path = Path(args[0]), Path(args[1]), Path(args[2]), Path(args[3])
plan = R.configuration()
hyp = A.loop_checker.configuration()["hypotheses"]
Z = hyp["confidence_z"]
HELD = set(hyp["confirmatory_samples"])
MODELS = ("qwen25coder32b", "qwen3coder30b", "qwen38_27b", "devstral2_24b", "gptoss120b", "seedcoder8b")
out = {}


def share(rows):
    s = A.violating_share(rows, Z)
    return {"n": s["n"], "k": s["k"], "share": s["share"], "lower": s["lower"], "upper": s["upper"]}


def status(r):
    if r is None or not r["functional"]:
        return "nonfunctional"
    return "violating" if r["violating"] else "clean"


def ukey(r):
    return (r["model"], r["task"], r["framework"], r["seed"])


bench_pg = A.benchmark_rows(plan, "postgresql", A.load(bench / "harness-postgresql"), HELD)
bench_my = A.benchmark_rows(plan, "mysql", A.load(bench / "harness-mysql"), HELD)
e1_pg = A.benchmark_rows(plan, "postgresql", A.load(ext / "harness-e1-postgresql"), HELD)
e1_my = A.benchmark_rows(plan, "mysql", A.load(ext / "harness-e1-mysql"), HELD)


def pick(rows, mode):
    return {ukey(r): r for r in rows if r["mode"] == mode and r["test"] and r["condition"] == "plain"}


start = pick(bench_pg, "single")
for r in json.loads(base_path.read_text("utf-8")):
    if (r["condition"] == "plain" and r["seed"] in HELD and r["task"] in T.TASKS
            and T.TASKS[r["task"]]["hazard"] != T.CONTROL_CLASS):
        k = (r["model"], r["task"], r["framework"], r["seed"])
        start.setdefault(k, {"model": r["model"], "functional": r["functional"],
                             "violating": bool(r["functional"] and r["read committed"]["violating"])})
loops = {"loop_seq": pick(bench_pg, "loop_seq"), "loop_conc": pick(bench_pg, "loop_conc"),
         "loop_self": pick(e1_pg, "loop_self")}
out["e1_starting_answers"] = len(start)
out["e1_modes"] = {"single": share(list(start.values())),
                   **{m: share(list(v.values())) for m, v in loops.items()}}
out["e1_modes_by_model"] = {
    m: {mode: share([r for r in rows.values() if r["model"] == m])
        for mode, rows in [("single", start)] + list(loops.items())} for m in MODELS}
out["e1_transitions"] = {
    mode: dict(C.Counter(f"{status(start.get(k))}->{status(r)}" for k, r in rows.items()))
    for mode, rows in loops.items()}

gen = [g for g in A.records(ext) if g["mode"] == "loop_self" and g["task"] in T.TASKS
       and g["seed"] in HELD and T.TASKS[g["task"]]["hazard"] != T.CONTROL_CLASS]
out["e1_self_records"] = len(gen)
out["e1_self_test_use"] = dict(C.Counter(A.test_use(g) for g in gen))
out["e1_self_rounds"] = dict(C.Counter(g["repair_rounds"] for g in gen))
out["e1_self_passed"] = sum(bool(g["loop_passed"]) for g in gen)
out["e1_self_tests_revised"] = sum(len(g.get("tests") or []) > 1 for g in gen)


def first_selftest(g):
    return next((h["selftest"] for h in g["history"] if h.get("selftest")), None)


detect = C.Counter()
for g in gen:
    s = start.get((g["model"], g["task"], g["framework"], g["seed"]))
    if status(s) != "violating":
        continue
    t = first_selftest(g)
    detect["violating_start"] += 1
    if t is None:
        detect["no_test_run"] += 1
    elif t.get("failed"):
        detect["first_test_failed"] += 1
    else:
        detect["first_test_passed"] += 1
out["e1_self_detection"] = dict(detect)

passed = [r for r in e1_pg if r["mode"] == "loop_self" and r["test"] and r["loop_passed"]]
out["e1_self_false_pass"] = share(passed)
out["e1_self_false_pass_by_model"] = {m: share([r for r in passed if r["model"] == m]) for m in MODELS}
use = {(g["model"], g["task"], g["framework"], g["seed"]): A.test_use(g) for g in gen}
first_cls = {(g["model"], g["task"], g["framework"], g["seed"]):
             (g["tests"][0]["classification"] if g.get("tests") else {}) for g in gen}
fp = C.Counter()
for r in passed:
    if r["functional"]:
        c = first_cls.get(ukey(r), {})
        kind = ("state" if c.get("asserts_state") else "results_only" if c.get("asserts_results")
                else "other")
        fp[f"{kind}:{'violating' if r['violating'] else 'clean'}"] += 1
out["e1_self_passed_by_assertion"] = dict(fp)

my = pick(e1_my, "loop_conc_my")
my_on_pg = pick(e1_pg, "loop_conc_my")
out["e1_my_modes"] = {"loop_conc_my_on_mysql": share(list(my.values())),
                      "loop_conc_my_on_postgresql": share(list(my_on_pg.values())),
                      "loop_conc_on_mysql": share(list(pick(bench_my, "loop_conc").values())),
                      "loop_conc_on_postgresql": share(list(loops["loop_conc"].values()))}
out["e1_my_passed"] = {
    "on_mysql": share([r for r in my.values() if r["loop_passed"]]),
    "on_postgresql": share([my_on_pg[k] for k, r in my.items() if r["loop_passed"] and k in my_on_pg]),
}
bench_conc_my = pick(bench_my, "loop_conc")
out["e1_conc_passed"] = {
    "on_postgresql": share([r for r in loops["loop_conc"].values() if r["loop_passed"]]),
    "on_mysql": share([bench_conc_my[k] for k, r in loops["loop_conc"].items()
                       if r["loop_passed"] and k in bench_conc_my]),
}
out["e1_my_rounds"] = dict(C.Counter(r["repair_rounds"] for r in my.values()))

e2 = {}
for engine in A.ENGINES:
    results = []
    for app in APPS.APPS:
        results += A.load(ext / f"harness-e2-{app}-{engine}")
    e2[engine] = A.app_rows(plan, engine, results, HELD)
    trials, success = plan["generated_trials"], plan["restored_success_share"]
    ser = {}
    for r in results:
        if r["source"] == "generated" and r["seed"] in HELD and r["functional"]["passed"]:
            opt = APPS.RULES[r["task"]]["optimal_successes"]
            ser[r["id"]] = (R.restored_at(r["concurrent"]["serializable"], trials, opt, success),
                            R.violates(r["concurrent"]["serializable"]),
                            R.violates(r["concurrent"]["serializable retry"]))
    for row, r in zip([x for x in e2[engine]], [r for r in results if r["source"] == "generated" and r["seed"] in HELD]):
        if row["functional"]:
            row["ser_restored"], row["ser_violating"], row["retry_violating"] = ser[r["id"]]

out["e2_programs"] = {e: {"rule_rows": len(rows),
                          "programs": len({(r["model"], r["mode"], r["handler"], r["condition"], r["seed"]) for r in rows})}
                      for e, rows in e2.items()}
out["e2_functional_single"] = {}
for engine, rows in e2.items():
    progs = {}
    for r in rows:
        if r["mode"] == "single":
            progs[(r["model"], r["handler"], r["condition"], r["seed"])] = r["functional"]
    out["e2_functional_single"][engine] = {
        m: {"functional": sum(v for k, v in progs.items() if k[0] == m),
            "programs": sum(1 for k in progs if k[0] == m)} for m in MODELS}
out["e2_by_rule"] = {
    engine: {f"{mode}:{cond}:{rule}": share([r for r in rows if r["mode"] == mode and r["condition"] == cond and r["task"] == rule])
             for mode, cond in (("single", "plain"), ("single", "hint"), ("loop_seq", "plain"), ("loop_conc", "plain"), ("loop_self", "plain"))
             for rule in APPS.RULES} for engine, rows in e2.items()}
out["e2_single_plain_by_model"] = {
    engine: {m: share([r for r in rows if r["mode"] == "single" and r["condition"] == "plain" and r["model"] == m]) for m in MODELS}
    for engine, rows in e2.items()}


def remedies(rows):
    v = [r for r in rows if r["functional"] and r["violating"]]
    return {"violating": len(v),
            "ser_still_violating": sum(r["ser_violating"] for r in v),
            "ser_restored": sum(r["ser_restored"] for r in v),
            "retry_still_violating": sum(r["retry_violating"] for r in v),
            "retry_restored": sum(r["retry_restored"] for r in v),
            "replay_restored": sum(r["replay_restored"] for r in v)}


out["e2_remedies"] = {engine: {"all": remedies(rows),
                               **{app: remedies([r for r in rows if r["app"] == app]) for app in APPS.APPS},
                               **{f"{app}:single": remedies([r for r in rows if r["app"] == app and r["mode"] == "single"]) for app in APPS.APPS}}
                      for engine, rows in e2.items()}
out["e2_loop_self_passed"] = {engine: {"passed_rows": share([r for r in rows if r["mode"] == "loop_self" and r["loop_passed"]])}
                              for engine, rows in e2.items()}

out["e3_spread"] = A.load_summary(ext)
out["e3_tables"] = A.load_tables(ext)
out["e3_folders"] = {e: A.load_folder(e) for e in A.ENGINES}

import random
import statistics

modes = [("single", start)] + list(loops.items())
out["supplementary_yield"] = {mode: {"cases": len(start),
                              "clean": sum(status(rows.get(k)) == "clean" for k in start)}
                       for mode, rows in modes}

def cluster_ci(rows_by_mode, reps=10000, seed=20261106):
    cells = sorted({(k[0], k[1], k[2]) for rows in rows_by_mode.values() for k in rows})
    by_cell = {m: C.defaultdict(lambda: [0, 0]) for m in rows_by_mode}
    for m, rows in rows_by_mode.items():
        for k, r in rows.items():
            if r is not None and r["functional"]:
                c = by_cell[m][(k[0], k[1], k[2])]
                c[0] += 1
                c[1] += bool(r["violating"])
    rng = random.Random(seed)
    draws = {m: [] for m in rows_by_mode}
    diffs = C.defaultdict(list)
    for _ in range(reps):
        sample = [cells[rng.randrange(len(cells))] for _ in cells]
        shares = {}
        for m in rows_by_mode:
            n = sum(by_cell[m][c][0] for c in sample)
            k = sum(by_cell[m][c][1] for c in sample)
            shares[m] = k / n if n else float("nan")
            draws[m].append(shares[m])
        diffs["loop_seq-single"].append(shares["loop_seq"] - shares["single"])
        diffs["loop_self-loop_conc"].append(shares["loop_self"] - shares["loop_conc"])
        diffs["loop_seq-loop_self"].append(shares["loop_seq"] - shares["loop_self"])

    def q(v):
        v = sorted(v)
        return [round(v[int(0.025 * len(v))], 3), round(v[int(0.975 * len(v)) - 1], 3)]
    return {"cells": len(cells), "share_ci": {m: q(v) for m, v in draws.items()},
            "difference_ci": {d: q(v) for d, v in diffs.items()}}


out["supplementary_cluster_ci"] = cluster_ci(dict(modes))

_completed, _load_results = A.completed_throughput, A.load_results
A.completed_throughput = lambda r: r.get("success_throughput") if r.get("requests") else None
out["supplementary_e3_successes"] = A.load_tables(ext)
A.completed_throughput = _completed
A.load_results = lambda folder: [r for r in _load_results(folder)
                                 if r["task"] not in ("coupon_redeem", "username_signup")]
out["supplementary_e3_without_exhausting_tasks"] = A.load_tables(ext)
A.load_results = _load_results
absolute = {}
for engine in A.ENGINES:
    runs = [r for r in _load_results(ext / A.load_folder(engine))
            if r["population"] == "reference" and "replay" not in r["arm"]]
    cells = C.defaultdict(list)
    for r in runs:
        cells[r["condition"]["name"] if isinstance(r["condition"], dict) else r["condition"]].append(r)
    absolute[engine] = {
        cond: {"requests_per_second_median": round(statistics.median(r["throughput"] for r in rs)),
               "requests_per_second_range": [round(min(r["throughput"] for r in rs)),
                                             round(max(r["throughput"] for r in rs))],
               "p99_ms_median": round(1000 * statistics.median(r["p99_seconds"] for r in rs), 1),
               "rejected_share_by_task": {
                   t: round(statistics.median(r["rejections"] / r["requests"] for r in rs
                                              if r["task"] == t), 2)
                   for t in sorted({r["task"] for r in rs})}}
        for cond, rs in sorted(cells.items())}
out["supplementary_e3_reference_absolute"] = absolute

hang_only = C.Counter()
for folder in [bench / "harness-postgresql", bench / "harness-mysql"] + sorted(ext.glob("harness-e*-*")):
    for r in A.load(folder):
        for arm, c in (r.get("concurrent") or {}).items():
            if isinstance(c, dict) and "hung" in c:
                hang_only["violating"] += bool(c.get("violating_trials"))
                hang_only["hang_only"] += bool(c["hung"] and not c.get("violating_trials"))
out["supplementary_hang_only"] = dict(hang_only)

out_path.write_text(json.dumps(out, indent=1, default=float) + "\n", "utf-8")
print(json.dumps({k: v for k, v in out.items() if not k.startswith("e3_tables")}, indent=1)[:9000])
