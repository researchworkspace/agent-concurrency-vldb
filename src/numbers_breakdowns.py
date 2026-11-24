"""Breakdowns and sensitivity checks reported in the paper's text.

Framework and model breakdowns, the criteria without the two Qwen coders, username_signup
sensitivity, the control task, self-test loop outcomes, burst times and sustained-load p99 latency.

Usage: python numbers_breakdowns.py BENCHMARK_RUN EXTENDED_RUN report-base.implementations.json OUT.json
"""

from __future__ import annotations

import collections as C
import json
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import checks
import report_extended as A
import tasks

benchmark, extended, base_path, out_path = (Path(a) for a in sys.argv[1:5])
plan = checks.configuration()
Z = plan["hypotheses"]["confidence_z"]
HELD = set(plan["hypotheses"]["confirmatory_samples"])
BASE_MODELS = {m for m, v in plan["models"].items() if v["source"] == "base"}
NEW = ("qwen38_27b", "devstral2_24b", "gptoss120b", "seedcoder8b")
ENGINES = ("postgresql", "mysql")
BENCHMARK_MODES = {"single", "loop_seq", "loop_conc"}
out: dict = {}


def share(rows, key="violating"):
    n, k = len(rows), sum(bool(r[key]) for r in rows)
    p = checks.proportion(k, n, Z)
    return {"n": n, "k": k, "share": round(p["share"], 3) if n else None,
            "lower": round(p["lower"], 3) if n else None, "upper": round(p["upper"], 3) if n else None}


def without_base(rows):
    return [r for r in rows if r["model"] not in BASE_MODELS]


def key(r):
    return (r["model"], r["mode"], r["task"], r["framework"], r["condition"], r["seed"])


def cell(r):
    return (r["model"], r["task"], r["framework"], r["seed"])


def status(r):
    return "nonfunctional" if not r or not r["functional"] else ("violating" if r["violating"] else "clean")


rows = {e: A.benchmark_rows(plan, e, A.load(benchmark / f"harness-{e}"), HELD)
        + A.benchmark_rows(plan, e, A.load(extended / f"harness-e1-{e}"), HELD) for e in ENGINES}

single = [r for r in rows["postgresql"] if r["mode"] == "single"]
have = {(r["model"], r["task"], r["framework"], r["condition"], r["seed"]) for r in single}
for r in json.loads(base_path.read_text("utf-8")):
    k = (r["model"], r["task"], r["framework"], r["condition"], r["seed"])
    if r["seed"] in HELD and r["task"] in tasks.TASKS and k not in have:
        single.append({"model": r["model"], "mode": "single", "task": r["task"],
                       "framework": r["framework"], "condition": r["condition"], "seed": r["seed"],
                       "test": tasks.TASKS[r["task"]]["hazard"] != tasks.CONTROL_CLASS,
                       "functional": r["functional"],
                       "violating": bool(r["functional"] and r["read committed"]["violating"])})


def postgresql_mode(mode, condition="plain"):
    source = single if mode == "single" else rows["postgresql"]
    return [r for r in source if r["mode"] == mode and r["test"] and r["condition"] == condition
            and r["functional"]]


control = {}
for e in ENGINES:
    for mode in ("single", "loop_seq", "loop_conc", "loop_self", "loop_conc_my"):
        source = single if (mode == "single" and e == "postgresql") else rows[e]
        rs = [r for r in source if r["task"] == "stock_purchase" and r["mode"] == mode]
        if rs:
            control[f"{e}|{mode}"] = {"programs": len(rs), **share([r for r in rs if r["functional"]])}
out["control_stock_purchase"] = control

out["loops_by_framework"] = {f"{mode}|{fw}": share([r for r in postgresql_mode(mode) if r["framework"] == fw])
                             for mode in ("single", "loop_seq", "loop_conc", "loop_self")
                             for fw in ("django", "sqlalchemy")}

population = {e: [r for r in rows[e] if r["mode"] in BENCHMARK_MODES] for e in ENGINES}
mysql = {key(r): r for r in population["mysql"] if r["test"]}
pairs, differ = C.Counter(), []
for r in population["postgresql"]:
    m = mysql.get(key(r))
    if r["test"] and m and r["functional"] and m["functional"]:
        pairs[f"{r['framework']}|{r['mode']}|pg={r['violating']}|my={m['violating']}"] += 1
        differ.append({"model": r["model"], "violating": r["violating"] != m["violating"]})
out["engine_pairs_by_framework"] = dict(sorted(pairs.items()))
criteria = {"engine_defaults_differ": {"all": share(differ), "without_base_models": share(without_base(differ))}}

remedies = {}
for e in ENGINES:
    violating = [r for r in population[e] if r["test"] and r["functional"] and r["violating"]]
    retry = [{"model": r["model"], "violating": not r["retry_restored"]} for r in violating]
    replay = [{"model": r["model"], "violating": r["replay_restored"]} for r in violating]
    criteria[f"retry_unrestored|{e}"] = {"all": share(retry), "without_base_models": share(without_base(retry))}
    criteria[f"replay_restored|{e}"] = {"all": share(replay), "without_base_models": share(without_base(replay))}
    for fw in ("django", "sqlalchemy"):
        x = [r for r in violating if r["framework"] == fw]
        remedies[f"{e}|{fw}"] = {"violating": len(x),
                                 "retry_unrestored": share([{"violating": not r["retry_restored"]} for r in x]),
                                 "replay_restored": sum(r["replay_restored"] for r in x)}
    every = [r for r in rows[e] if r["test"] and r["functional"] and r["violating"]]
    remedies[f"{e}|all_loops"] = {
        "violating": len(every),
        "retry_unrestored": share([{"violating": not r["retry_restored"]} for r in every]),
        "replay_unrestored": len(every) - sum(r["replay_restored"] for r in every)}
out["remedies_by_framework"] = remedies

criteria["sequential_loop"] = {"all": share(postgresql_mode("loop_seq")),
                               "without_base_models": share(without_base(postgresql_mode("loop_seq")))}
passed = [r for r in rows["postgresql"] if r["mode"] == "loop_conc" and r["test"] and r["loop_passed"]
          and r["condition"] == "plain" and r["functional"]]
criteria["concurrency_loop_passed"] = {"all": share(passed), "without_base_models": share(without_base(passed))}


def closure(keep):
    s = {m: share([r for r in postgresql_mode(m) if keep(r["model"])])["share"]
         for m in ("loop_seq", "loop_conc", "loop_self")}
    return round((s["loop_seq"] - s["loop_self"]) / (s["loop_seq"] - s["loop_conc"]), 3)


criteria["gap_closure"] = {"all": closure(lambda m: True),
                           "without_base_models": closure(lambda m: m not in BASE_MODELS)}
passed = [r for r in rows["postgresql"] if r["mode"] == "loop_self" and r["test"] and r["loop_passed"]
          and r["functional"]]
criteria["self_test_false_pass"] = {"all": share(passed), "without_base_models": share(without_base(passed))}
passed = [r for r in rows["mysql"] if r["mode"] == "loop_conc_my" and r["test"] and r["loop_passed"]
          and r["functional"]]
criteria["mysql_loop_passed"] = {"all": share(passed), "without_base_models": share(without_base(passed))}
for e in ENGINES:
    app = []
    for name in ("oscar", "ctfd"):
        app += A.app_rows(plan, e, A.load(extended / f"harness-e2-{name}-{e}"), HELD)
    if e == "postgresql":
        for rule in sorted({r["task"] for r in app}):
            x = [r for r in app if r["task"] == rule and r["mode"] == "single"
                 and r["condition"] == "plain" and r["functional"]]
            criteria[f"app_prevalence|{rule}"] = {"all": share(x), "without_base_models": share(without_base(x))}
    replay = [{"model": r["model"], "violating": r["replay_restored"]}
              for r in app if r["functional"] and r["violating"]]
    criteria[f"app_replay_restored|{e}"] = {"all": share(replay),
                                            "without_base_models": share(without_base(replay))}
out["criteria_without_base_models"] = criteria

username = {}
for condition, (minimum, lower, classes) in (("plain", (0.25, 0.10, 3)), ("hint", (0.10, 0.03, 2))):
    for dropped in (False, True):
        per_model = {}
        for m in NEW:
            met, cells = 0, []
            for cls in ("check_then_insert", "write_skew", "predicate", "multi_row"):
                x = [r for r in single if r["model"] == m and r["condition"] == condition and r["functional"]
                     and tasks.TASKS[r["task"]]["hazard"] == cls
                     and not (dropped and r["task"] == "username_signup")]
                s = share(x)
                ok = s["n"] >= 8 and s["share"] >= minimum and s["lower"] >= lower
                met += ok
                cells.append(f"{cls}:{s['k']}/{s['n']}{'*' if ok else ''}")
            per_model[m] = {"classes_met": met, "classes_needed": classes, "cells": cells}
        username[f"{condition}|{'without_username_signup' if dropped else 'all_tasks'}"] = per_model
    x = [r for r in single if r["condition"] == condition and r["test"] and r["functional"]]
    username[f"{condition}|pooled"] = {"all_tasks": share(x),
                                       "without_username_signup": share([r for r in x if r["task"] != "username_signup"])}
    username[f"{condition}|username_signup_new_models"] = share(
        [r for r in x if r["task"] == "username_signup" and r["model"] in NEW])
for mode in ("loop_seq", "loop_conc", "loop_self"):
    username[f"{mode}|without_username_signup"] = share(
        [r for r in postgresql_mode(mode) if r["task"] != "username_signup"])
patterns = {"advisory": r"advisory|pg_advisory", "serializable": r"SERIALIZABLE", "lock_table": r"LOCK\s+TABLE",
            "for_update": r"select_for_update|with_for_update|FOR UPDATE", "unique_index": r"UNIQUE"}
mechanisms = C.Counter()
for folder in (benchmark / "harness-postgresql", extended / "harness-e1-postgresql"):
    for r in checks.load_results(folder):
        if (r["source"] != "generated" or r["task"] != "username_signup" or r["seed"] not in HELD
                or not r["functional"]["passed"]
                or checks.violates(r["concurrent"][checks.default_arm(plan, "postgresql", r["framework"])])):
            continue
        code = (folder / "implementations" / r["model"] / Path(r["path"]).name).read_text("utf-8")
        found = [k for k, p in patterns.items() if re.search(p, code, re.I)]
        primary = next((k for k in ("advisory", "serializable", "lock_table", "unique_index") if k in found), "other")
        mechanisms["clean"] += 1
        mechanisms[primary] += 1
username["clean_mechanisms"] = dict(mechanisms)
out["username_signup"] = username

start = {cell(r): r for r in single if r["condition"] == "plain" and r["test"]}
final = {cell(r): r for r in rows["postgresql"] if r["mode"] == "loop_self" and r["test"] and r["condition"] == "plain"}
records = {cell(g): g for g in A.records(extended)
           if g["mode"] == "loop_self" and g["task"] in tasks.TASKS and g["seed"] in HELD}
outcomes, revised = C.Counter(), C.Counter()
for k, s in start.items():
    if status(s) != "violating" or status(final.get(k)) != "violating":
        continue
    g = records[k]
    last = g["history"][-1].get("selftest") or {}
    if g["loop_passed"]:
        kind = "passed_own_test"
    elif g["repair_rounds"] < 3:
        kind = "stopped_by_input_limit"
    elif last.get("first_failure_kind") == "assertion":
        kind = "rounds_used_test_still_asserts"
    elif last.get("first_failure_kind") == "error":
        kind = "rounds_used_test_errors"
    else:
        kind = "other"
    outcomes[kind] += 1
    revised["revised_test" if len(g.get("tests") or []) > 1 else "kept_first_test"] += 1
out["self_test_loop_unrepaired"] = {"outcomes": dict(outcomes), "tests": dict(revised)}

burst = {}
for e in ENGINES:
    for folder in sorted(benchmark.glob(f"sweep-{e}-*")):
        rs = checks.load_results(folder)
        level = rs[0]["level"]["name"]
        ref = [statistics.median(r["concurrent"][checks.default_arm(plan, e, r["framework"])]["seconds"])
               for r in rs if r["population"] == "reference"]
        unchanged = [statistics.median(r["concurrent"]["request replay"]["seconds"])
                     for r in rs if r["population"] == "replay_restored"]
        burst[f"{e}|{level}"] = {"reference_default_seconds": round(statistics.median(ref), 4),
                                 "unchanged_replay_seconds": round(statistics.median(unchanged), 4)}
out["burst_median_trial_seconds"] = burst

latency = {}
for e in ENGINES:
    grouped = C.defaultdict(list)
    for r in A.load_results(extended / A.load_folder(e)):
        if r.get("p99_seconds"):
            grouped[(r["id"], r["population"], r["task"], r["framework"], r["arm"], r["condition"]["name"])].append(r)
    p99 = {k: statistics.mean(x["p99_seconds"] for x in v) for k, v in grouped.items()}
    reference = {(k[2], k[3], k[5]): v for k, v in p99.items() if k[1] == "reference" and "replay" not in k[4]}
    cells = C.defaultdict(list)
    for k, v in p99.items():
        b = reference.get((k[2], k[3], k[5]))
        if b and not (k[1] == "reference" and "replay" not in k[4]):
            cells[f"{k[5]}:{k[1]}:{'replay' if 'replay' in k[4] else 'default'}"].append(v / b)
    latency[e] = {c: {"programs": len(v), "p99_ratio_median": round(statistics.median(v), 2)}
                  for c, v in sorted(cells.items())}
out["load_p99_ratio"] = latency

out_path.write_text(json.dumps(out, indent=1, sort_keys=True), "utf-8")
print("wrote", out_path)
