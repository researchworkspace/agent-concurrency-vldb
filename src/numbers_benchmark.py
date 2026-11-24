"""Paper numbers of the benchmark stage (RQ1-RQ5).

Usage: python numbers_benchmark.py BENCHMARK_RUN report-base.implementations.json OUT.json
"""

import collections as C
import glob
import json
import os
import re
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks as T
import checks as R

plan = R.configuration()
base = Path(sys.argv[1])
base_rows = json.loads(Path(sys.argv[2]).read_text("utf-8"))
out_path = Path(sys.argv[3])
Z = plan["hypotheses"]["confidence_z"]
TRIALS = plan["generated_trials"]
SHARE = plan["restored_success_share"]
HELD_OUT = set(plan["hypotheses"]["confirmatory_samples"])
ENGINES = ("postgresql", "mysql")
CLASSES = ("check_then_insert", "write_skew", "predicate", "multi_row")
MODELS = ("qwen25coder32b", "qwen3coder30b", "qwen38_27b", "devstral2_24b", "gptoss120b", "seedcoder8b")
NEW = ("qwen38_27b", "devstral2_24b", "gptoss120b", "seedcoder8b")


def key(r):
    return (r["model"], r.get("mode", "single"), r["task"], r["framework"], r["condition"], r["seed"])


def load(engine):
    return {key(r): r for r in R.load_results(base / f"harness-{engine}") if r["source"] == "generated"}


D = {e: load(e) for e in ENGINES}


def optimum(task):
    return T.TASKS[task]["concurrent"]["optimal_successes"]


def cls(task):
    return T.TASKS[task]["hazard"]


def default(engine, r):
    return r["concurrent"][R.default_arm(plan, engine, r["framework"])]


def status(engine, r):
    if not r["functional"]["passed"]:
        return "nonfunctional"
    return "violating" if R.violates(default(engine, r)) else "clean"


def restored(r, arm):
    return R.restored_at(r["concurrent"][arm], TRIALS, optimum(r["task"]), SHARE)


def held(r):
    return r["seed"] in HELD_OUT and cls(r["task"]) != T.CONTROL_CLASS


def prop(k, n):
    return R.proportion(k, n, Z)


def code_of(engine, r):
    stem = Path(r["path"]).name if r.get("path") else None
    if not stem:
        return ""
    p = base / f"harness-{engine}" / "implementations" / r["model"] / stem
    return p.read_text("utf-8") if p.exists() else ""


out = {}

report = json.loads((base / "report-benchmark.json").read_text("utf-8"))
prev = {}
for h, cond in (("H1", "plain"), ("H2", "hint")):
    for m, v in report["hypotheses"][h]["models"].items():
        for c, x in v["classes"].items():
            prev[f"{m}|{cond}|{c}"] = {"k": x["k"], "n": x["n"]}
for r in base_rows:
    if r["class"] in CLASSES and r["functional"]:
        cell = prev.setdefault(f"{r['model']}|{r['condition']}|{r['class']}", {"k": 0, "n": 0})
        cell["n"] += 1
        cell["k"] += r["read committed"]["violating"]
out["prevalence"] = prev

modes = {}
by_model = {}
for (m, mode, task, fw, cond, seed), r in D["postgresql"].items():
    if cond != "plain" or not held(r):
        continue
    s = status("postgresql", r)
    modes.setdefault(mode, C.Counter())[s] += 1
    by_model.setdefault(f"{m}|{mode}", C.Counter())[s] += 1
for r in base_rows:
    if r["condition"] != "plain" or r["seed"] not in HELD_OUT or r["class"] not in CLASSES:
        continue
    s = "nonfunctional" if not r["functional"] else ("violating" if r["read committed"]["violating"] else "clean")
    modes.setdefault("single", C.Counter())[s] += 1
    by_model.setdefault(f"{r['model']}|single", C.Counter())[s] += 1
out["modes"] = {k: dict(v) for k, v in modes.items()}
out["modes_by_model"] = {k: dict(v) for k, v in by_model.items()}
for mode, cnt in modes.items():
    f = cnt["violating"] + cnt["clean"]
    out.setdefault("mode_share", {})[mode] = prop(cnt["violating"], f)

start = {}
for (m, mode, task, fw, cond, seed), r in D["postgresql"].items():
    if mode == "single" and cond == "plain" and held(r):
        start[(m, task, fw, seed)] = status("postgresql", r)
for r in base_rows:
    if r["condition"] == "plain" and r["seed"] in HELD_OUT and r["class"] in CLASSES:
        start[(r["model"], r["task"], r["framework"], r["seed"])] = (
            "nonfunctional" if not r["functional"] else ("violating" if r["read committed"]["violating"] else "clean")
        )
trans = {}
for (m, mode, task, fw, cond, seed), r in D["postgresql"].items():
    if mode == "single" or not held(r):
        continue
    a = start.get((m, task, fw, seed))
    trans.setdefault(mode, C.Counter())[f"{a}->{status('postgresql', r)}"] += 1
out["transitions"] = {k: dict(v) for k, v in trans.items()}

rounds = C.Counter()
loop_pass = C.Counter()
for (m, mode, *_), r in D["postgresql"].items():
    if mode == "loop_conc" and held(r):
        rounds[r.get("repair_rounds", 0)] += 1
        loop_pass[bool(r.get("loop_passed"))] += 1
out["loop_conc_rounds"] = dict(rounds)
out["loop_conc_passed"] = {str(k): v for k, v in loop_pass.items()}

pair = C.Counter()
mysql_only = []
rr_rc = C.Counter()
for k, p in D["postgresql"].items():
    q = D["mysql"].get(k)
    if not q or not held(p):
        continue
    if p["functional"]["passed"] and q["functional"]["passed"]:
        a, b = R.violates(default("postgresql", p)), R.violates(default("mysql", q))
        pair[f"pg_{'v' if a else 'c'}|my_{'v' if b else 'c'}"] += 1
        if b and not a:
            d = default("mysql", q)
            mysql_only.append(
                {
                    "id": "|".join(map(str, k)),
                    "violating_trials": d["violating_trials"],
                    "hung": d["hung"],
                    "violations": d.get("violations"),
                    "postgresql_success_share": sum(default("postgresql", p)["successes"])
                    / (optimum(p["task"]) * TRIALS),
                }
            )
        rr, rc = R.violates(q["concurrent"]["repeatable read"]), R.violates(q["concurrent"]["read committed"])
        rr_rc[f"rr_{'v' if rr else 'c'}|rc_{'v' if rc else 'c'}"] += 1
out["engine_pairs"] = dict(pair)
out["engine_mysql_only"] = mysql_only
out["mysql_rr_rc"] = dict(rr_rc)

rem = {}
cat = {}
by_class = {}
replay_failures = []
for e in ENGINES:
    cnt = C.Counter()
    cats = C.Counter()
    for k, r in D[e].items():
        if not held(r) or not r["functional"]["passed"] or not R.violates(default(e, r)):
            continue
        cnt["violating"] += 1
        ser, ret, rep = (r["concurrent"][a] for a in ("serializable", "serializable retry", "request replay"))
        cnt["ser_still"] += R.violates(ser)
        cnt["ser_restored"] += restored(r, "serializable")
        cnt["retry_still"] += R.violates(ret)
        cnt["retry_restored"] += restored(r, "serializable retry")
        cnt["replay_still"] += R.violates(rep)
        cnt["replay_restored"] += restored(r, "request replay")
        if restored(r, "serializable retry"):
            c_ = "restored"
        elif R.violates(ret):
            c_ = "still_violates"
        elif ret["exceptions"]:
            c_ = "raises"
        else:
            c_ = "falls_short"
        cats[c_] += 1
        bc = by_class.setdefault(f"{e}|{cls(r['task'])}", C.Counter())
        bc["violating"] += 1
        bc["retry_restored"] += restored(r, "serializable retry")
        bc["replay_restored"] += restored(r, "request replay")
        if not restored(r, "request replay"):
            replay_failures.append(
                {
                    "engine": e,
                    "id": "|".join(map(str, k)),
                    "violating_trials": rep["violating_trials"],
                    "hung": rep["hung"],
                    "exceptions": rep["exceptions"],
                    "success_share": sum(rep["successes"]) / (optimum(r["task"]) * max(rep["trials"], 1)),
                    "trials": rep["trials"],
                }
            )
    n = cnt["violating"]
    rem[e] = {
        **cnt,
        "retry_unrestored": prop(n - cnt["retry_restored"], n),
        "replay_unrestored": prop(n - cnt["replay_restored"], n),
        "ser_restored_share": prop(cnt["ser_restored"], n),
        "retry_restored_share": prop(cnt["retry_restored"], n),
        "replay_restored_share": prop(cnt["replay_restored"], n),
    }
    cat[e] = dict(cats)
out["remedies"] = rem
out["retry_categories"] = cat
out["remedies_by_class"] = {k: dict(v) for k, v in by_class.items()}
out["replay_failures"] = replay_failures

LOCK = re.compile(r"select_for_update|with_for_update|FOR UPDATE", re.I)
ADVISORY = re.compile(r"advisory|GET_LOCK", re.I)
ISO = re.compile(r"isolation_level|SERIALIZABLE|REPEATABLE", re.I)
CATCHALL = re.compile(r"except\s+(Exception|BaseException)\b|except\s*:")
pat = {}
for k, r in D["postgresql"].items():
    if not held(r) or not r["functional"]["passed"]:
        continue
    src = code_of("postgresql", r)
    s = status("postgresql", r)
    g = pat.setdefault(f"{r.get('mode', 'single')}|{s}", C.Counter())
    g["n"] += 1
    g["row_lock"] += bool(LOCK.search(src))
    g["advisory"] += bool(ADVISORY.search(src))
    g["isolation"] += bool(ISO.search(src))
    g["catch_all"] += bool(CATCHALL.search(src))
    g["any_protection"] += bool(LOCK.search(src) or ADVISORY.search(src) or ISO.search(src))
out["code_patterns"] = {k: dict(v) for k, v in pat.items()}

sweep = {}
for e in ENGINES:
    for folder in sorted(glob.glob(str(base / f"sweep-{e}-*"))):
        rows = R.load_results(Path(folder))
        level = rows[0]["level"]["name"]
        ref = {}
        for r in rows:
            if r["population"] == "reference":
                arm = R.default_arm(plan, e, r["framework"])
                ref[(r["task"], r["framework"])] = statistics.median(r["concurrent"][arm]["seconds"])
        for r in rows:
            base_s = ref.get((r["task"], r["framework"]))
            for arm, c in r["concurrent"].items():
                arm_kind = "replay" if arm == "request replay" else "default"
                cell = sweep.setdefault(f"{e}|{level}|{r['population']}|{arm_kind}", {"ratios": [], "retries": 0, "trials": 0, "succ": 0, "poss": 0, "violating": 0, "n": 0})
                cell["n"] += 1
                if base_s and c["seconds"]:
                    cell["ratios"].append(statistics.median(c["seconds"]) / base_s)
                cell["retries"] += c["retries"]
                cell["trials"] += c["trials"]
                cell["succ"] += sum(c["successes"])
                cell["poss"] += r["optimum"] * c["trials"]
                cell["violating"] += R.violates(c)
for cell in sweep.values():
    ratios = sorted(cell.pop("ratios"))
    q = statistics.quantiles(ratios, n=4) if len(ratios) >= 2 else [ratios[0]] * 3
    cell["ratio_median"], cell["ratio_q1"], cell["ratio_q3"] = statistics.median(ratios), q[0], q[2]
    cell["replays_per_trial"] = cell["retries"] / cell["trials"]
    cell["success_share"] = cell.pop("succ") / cell.pop("poss")
out["sweep"] = sweep

out["hypotheses"] = report["hypotheses"]
out["reference_gates"] = report["reference_gates"]
out_path.write_text(json.dumps(out, indent=1, sort_keys=True, default=str), "utf-8")
print("wrote", out_path)
