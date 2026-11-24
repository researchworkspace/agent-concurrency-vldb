"""Functional and concurrent checks on PostgreSQL or MySQL, the contention sweep and the benchmark report (H1-H7).

Usage: python -I checks.py run --engine E --output DIR --bin DIR [--generations DIR ...]
       python -I checks.py summary --output DIR --save FILE
       python -I checks.py sweep --engine E --level L --run RUN --output DIR --bin DIR
       python -I checks.py report --runs PG_RUN MYSQL_RUN --sweeps DIR ... --save FILE
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
PLAN = FOLDER / "plan-benchmark.json"
sys.path.insert(0, str(FOLDER))
import harness
import tasks

VERSION = "agent-concurrency-benchmark"
ENGINES = ("postgresql", "mysql")


def configuration() -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(PLAN.read_text("utf-8"))
    if plan["version"] != VERSION or plan["base_version"] != harness.VERSION:
        raise ValueError("Benchmark plan differs from the implemented runner or harness")
    return plan


def references(engine: str) -> dict[tuple[str, str, str], str]:
    if engine == "mysql":
        import mysql_server

        return mysql_server.references()
    return dict(tasks.REFERENCES)


def server(engine: str, binaries: Path, root: Path, sockets: Path, plan: dict, log: Path) -> Any:
    settings = plan["engines"][engine]["server_settings"]
    if engine == "mysql":
        import mysql_server

        return mysql_server.MySQL(binaries, root, sockets, settings, log)
    return harness.Postgres(binaries, root, sockets, settings, log)


def server_version(engine: str, db: Any) -> str:
    return db.value("SELECT VERSION()" if engine == "mysql" else "SHOW server_version")


def inventory(output: Path, engine: str, generations: list[Path]) -> list[dict[str, Any]]:
    items = []
    for (task, framework, variant), code in sorted(references(engine).items()):
        path = output / "implementations" / "reference" / f"{task}__{framework}__{variant}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, "utf-8")
        items.append(
            {
                "id": f"reference:{task}:{framework}:{variant}",
                "source": "reference",
                "variant": variant,
                "task": task,
                "framework": framework,
                "path": str(path),
            }
        )
    for folder in generations:
        for record_path in sorted((folder / "generations").glob("*.json")):
            record = json.loads(record_path.read_text("utf-8"))
            item = {
                "id": f"generated:{record['model']}:{record_path.stem}",
                "source": "generated",
                **{k: record[k] for k in ("model", "task", "framework", "condition", "seed")},
                "mode": record.get("mode", "single"),
                "path": None,
            }
            for key in ("loop_passed", "repair_rounds"):
                if key in record:
                    item[key] = record[key]
            if record["code"] is not None:
                path = output / "implementations" / record["model"] / f"{record_path.stem}.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(record["code"], "utf-8")
                item["path"] = str(path)
            items.append(item)
    return items


def base_config(item: dict, engine: str, db: Any, output: Path, plan: dict) -> dict[str, Any]:
    return {
        "engine": engine,
        "framework": item["framework"],
        "function": tasks.TASKS[item["task"]]["function"],
        "path": item["path"],
        "host": str(db.sockets),
        "port": db.port,
        "support": str(harness.support_directory(output, item["task"], item["framework"])),
        "retry": {**plan["retry"], "sqlstates": plan["engines"][engine]["retry_codes"]},
        "retries": 0,
    }


def settings(plan: dict, workers: int | None = None, spread: float = 0) -> dict[str, Any]:
    return {
        "workers": workers or plan["workers"],
        "trial_timeout_seconds": plan["trial_timeout_seconds"],
        "spread_seconds": spread,
    }


def manifest_for(args: argparse.Namespace, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": VERSION,
        "engine": args.engine,
        "plan_sha256": harness.file_hash(PLAN),
        "runner_sha256": harness.file_hash(Path(__file__)),
        "harness_sha256": harness.file_hash(FOLDER / "harness.py"),
        "tasks_sha256": harness.file_hash(FOLDER / "tasks.py"),
        "mysql_sha256": harness.file_hash(FOLDER / "mysql_server.py"),
        **extra,
    }


def claim(output: Path, manifest: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    path = output / "manifest.json"
    if path.exists():
        if json.loads(path.read_text("utf-8")) != manifest:
            raise ValueError("existing output belongs to a different run; use a new output path")
    else:
        harness.atomic_write(path, manifest)


def run(args: argparse.Namespace) -> None:
    plan = configuration()
    engine = plan["engines"][args.engine]
    output = Path(args.output)
    generations = [Path(g) for g in args.generations or []]
    claim(
        output, manifest_for(args, {"generations": sorted(str(g.resolve()) for g in generations)})
    )
    items = inventory(output, args.engine, generations)
    items = items[: args.limit] if args.limit else items
    root = Path(args.scratch or tempfile.gettempdir())
    sockets = Path(args.sockets or tempfile.gettempdir())
    started = time.time()
    log = output / f"{args.engine}.log"
    with server(args.engine, Path(args.bin), root, sockets, plan, log) as db:
        environment = {
            "engine": args.engine,
            "server_version": server_version(args.engine, db),
            "python": sys.version.split()[0],
        }
        for item in items:
            target = output / "results" / f"{item['id'].replace(':', '__')}.json"
            if target.exists():
                continue
            result: dict[str, Any] = {**item, "environment": environment}
            if item["path"] is None:
                result["functional"] = {"passed": False, "no_code": True}
            else:
                config = base_config(item, args.engine, db, output, plan)
                level = engine["framework_defaults"][item["framework"]]
                result["functional"] = harness.functional(
                    db, item, config, plan["trial_timeout_seconds"], level
                )
                if result["functional"]["passed"]:
                    trials = plan[f"{item['source']}_trials"]
                    result["concurrent"] = {
                        name: harness.concurrent(db, item, config, arm, trials, settings(plan))
                        for name, arm in engine["arms"].items()
                    }
            harness.atomic_write(target, result)
            print(f"{item['id']} functional={result['functional']['passed']}", flush=True)
    harness.atomic_write(
        output / "completion.json",
        {
            "results": len(list((output / "results").glob("*.json"))),
            "items": len(items),
            "seconds": round(time.time() - started, 1),
        },
    )


def load_results(folder: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text("utf-8")) for p in sorted((folder / "results").glob("*.json"))]


def restored_at(c: dict[str, Any], trials: int, optimum: int, share: float) -> bool:
    return (
        c["trials"] == trials
        and not c["hung"]
        and c["violating_trials"] == 0
        and not c["exceptions"]
        and sum(c["successes"]) >= share * optimum * trials
    )


def default_arm(plan: dict, engine: str, framework: str) -> str:
    level = plan["engines"][engine]["framework_defaults"][framework]
    arms = plan["engines"][engine]["arms"]
    return next(n for n, a in arms.items() if a["isolation"] == level and not a["retries"])


def sweep_population(plan: dict, engine: str, results: list[dict]) -> list[dict[str, Any]]:
    share = plan["restored_success_share"]
    trials = plan["generated_trials"]
    chosen = []
    for r in results:
        if not r.get("concurrent"):
            continue
        if r["source"] == "reference":
            if r["variant"] == "correct":
                chosen.append({**r, "population": "reference"})
            continue
        if tasks.TASKS[r["task"]]["hazard"] == tasks.CONTROL_CLASS:
            continue
        default = r["concurrent"][default_arm(plan, engine, r["framework"])]
        optimal = tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"]
        if default["violating_trials"]:
            if restored_at(r["concurrent"]["request replay"], trials, optimal, share):
                chosen.append({**r, "population": "replay_restored"})
        elif (
            r.get("mode") == "loop_conc"
            and r.get("repair_rounds", 0) >= 1
            and restored_at(default, trials, optimal, share)
        ):
            chosen.append({**r, "population": "loop_repaired"})
    return chosen


def sweep_arms(plan: dict, engine: str, row: dict[str, Any]) -> list[str]:
    if row["population"] == "replay_restored":
        return ["request replay"]
    return [default_arm(plan, engine, row["framework"]), "request replay"]


def serial_optimum(db: Any, config: dict, task: str, workers: int, timeout: float) -> int:
    calls = [kwargs for w in range(workers) for kwargs in tasks.request_plan(task, w)]
    db.set_isolation("serializable")
    db.reset(task, tasks.TASKS[task]["concurrent"]["seed"])
    pool = harness.Clients(1, {**config, "isolation": "serializable"}, timeout)
    try:
        if pool.setup_error:
            raise RuntimeError(pool.setup_error)
        reply = pool.run([calls], sync=False)[0]
    finally:
        pool.close()
    if reply is None:
        raise RuntimeError(f"serial run of {task} hung")
    return sum(o.get("ok") is True for o in reply["outcomes"])


def sweep(args: argparse.Namespace) -> None:
    plan = configuration()
    engine = plan["engines"][args.engine]
    main = Path(args.run)
    main_manifest = json.loads((main / "manifest.json").read_text("utf-8"))
    if main_manifest["engine"] != args.engine or main_manifest["version"] != VERSION:
        raise ValueError("the main run belongs to another engine or plan")
    output = Path(args.output)
    levels = [lv for lv in plan["sweep"]["levels"] if args.level in (None, lv["name"])]
    if not levels:
        raise ValueError(f"unknown sweep level {args.level!r}")
    extra = {"run": str(main.resolve()), "run_manifest": main_manifest, "level": args.level}
    claim(output, manifest_for(args, extra))
    population = sweep_population(plan, args.engine, load_results(main))
    population = population[: args.limit] if args.limit else population
    root = Path(args.scratch or tempfile.gettempdir())
    sockets = Path(args.sockets or tempfile.gettempdir())
    timeout = plan["trial_timeout_seconds"]
    started = time.time()
    with server(args.engine, Path(args.bin), root, sockets, plan, output / "server.log") as db:
        optimum_path = output / "optimum.json"
        optimum: dict[str, int] = (
            json.loads(optimum_path.read_text("utf-8")) if optimum_path.exists() else {}
        )
        for level in levels:
            for r in (r for r in population if r["source"] == "reference"):
                key = f"{r['task']}:{r['framework']}:{level['workers']}"
                if key not in optimum:
                    config = base_config(r, args.engine, db, output, plan)
                    optimum[key] = serial_optimum(db, config, r["task"], level["workers"], timeout)
                    harness.atomic_write(optimum_path, optimum)
        for level in levels:
            load = settings(plan, level["workers"], level["spread_seconds"])
            for r in population:
                name = f"{level['name']}__{r['id'].replace(':', '__')}"
                target = output / "results" / f"{name}.json"
                if target.exists():
                    continue
                config = base_config(r, args.engine, db, output, plan)
                arms = sweep_arms(plan, args.engine, r)
                trials = plan["sweep"][f"{r['source']}_trials"]
                result = {
                    **{
                        k: r[k] for k in ("id", "source", "population", "task", "framework", "path")
                    },
                    **{k: r[k] for k in ("model", "condition", "seed", "mode") if k in r},
                    "level": level,
                    "optimum": optimum[f"{r['task']}:{r['framework']}:{level['workers']}"],
                    "concurrent": {
                        arm: harness.concurrent(db, r, config, engine["arms"][arm], trials, load)
                        for arm in arms
                    },
                }
                harness.atomic_write(target, result)
                print(name, flush=True)
    harness.atomic_write(
        output / "completion.json",
        {
            "results": len(list((output / "results").glob("*.json"))),
            "population": len(population),
            "levels": [lv["name"] for lv in levels],
            "seconds": round(time.time() - started, 1),
        },
    )


def reference_gates(plan: dict, engine: str, results: list[dict]) -> dict[str, Any]:
    share = plan["restored_success_share"]
    rows = [r for r in results if r["source"] == "reference"]
    correct = [r for r in rows if r["variant"] == "correct"]
    buggy = [r for r in rows if r["variant"] == "buggy"]
    clean = bool(correct) and all(
        r["functional"]["passed"]
        and all(c["violating_trials"] == 0 and not c["hung"] for c in r["concurrent"].values())
        and restored_at(
            r["concurrent"]["request replay"],
            plan["reference_trials"],
            tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"],
            share,
        )
        for r in correct
    )
    detected = [
        r["id"]
        for r in buggy
        if r.get("concurrent")
        and r["concurrent"][default_arm(plan, engine, r["framework"])]["violating_trials"]
    ]
    return {
        "references": len(rows),
        "correct_clean": clean,
        "buggy_violate_at_default": f"{len(detected)}/{len(buggy)}",
        "buggy_missed": sorted({r["id"] for r in buggy} - set(detected)),
    }


def summary(args: argparse.Namespace) -> dict[str, Any]:
    plan = configuration()
    results = load_results(Path(args.output))
    engine = json.loads((Path(args.output) / "manifest.json").read_text("utf-8"))["engine"]
    share = plan["restored_success_share"]
    trials = plan["generated_trials"]
    table: dict[str, Any] = defaultdict(Counter)
    references: dict[str, Any] = {}
    for r in results:
        if r["source"] == "reference":
            references[r["id"]] = {
                "functional": r["functional"]["passed"],
                **{a: c["violating_trials"] for a, c in (r.get("concurrent") or {}).items()},
            }
            continue
        cell = table[f"{r['model']}|{r.get('mode', 'single')}|{r['framework']}|{r['condition']}"]
        cell["generated"] += 1
        if not r["functional"]["passed"]:
            continue
        cell["functional"] += 1
        hazard = tasks.TASKS[r["task"]]["hazard"]
        if hazard == tasks.CONTROL_CLASS:
            continue
        cell["test_functional"] += 1
        default = r["concurrent"][default_arm(plan, engine, r["framework"])]
        if default["violating_trials"]:
            cell["violating"] += 1
            optimal = tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"]
            if restored_at(r["concurrent"]["request replay"], trials, optimal, share):
                cell["replay_restored"] += 1
            if restored_at(r["concurrent"]["serializable retry"], trials, optimal, share):
                cell["retry_restored"] += 1
    out = {
        "engine": engine,
        "reference_gates": reference_gates(plan, engine, results),
        "references": references,
        "cells": {k: dict(v) for k, v in table.items()},
    }
    if args.save:
        harness.atomic_write(Path(args.save), out)
    for key, cell in sorted(out["cells"].items()):
        print(key, cell)
    return out


def wilson(hits: int, total: int, z: float) -> tuple[float, float]:
    if not total:
        return 0.0, 1.0
    p = hits / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    scale = 1 + z * z / total
    return max(0.0, (centre - margin) / scale), min(1.0, (centre + margin) / scale)


def proportion(hits: int, total: int, z: float) -> dict[str, Any]:
    lower, upper = wilson(hits, total, z)
    return {
        "n": total,
        "k": hits,
        "share": hits / total if total else None,
        "lower": lower,
        "upper": upper,
    }


def violates(c: dict[str, Any]) -> bool:
    return bool(c["violating_trials"] or c["hung"])


def rows_of(plan: dict, engine: str, results: list[dict]) -> list[dict[str, Any]]:
    seeds = set(plan["hypotheses"]["confirmatory_samples"])
    share, trials = plan["restored_success_share"], plan["generated_trials"]
    rows = []
    for r in results:
        if r["source"] != "generated" or r["seed"] not in seeds:
            continue
        row = {
            k: r.get(k)
            for k in (
                "model",
                "mode",
                "task",
                "framework",
                "condition",
                "seed",
                "loop_passed",
                "repair_rounds",
            )
        }
        row["class"] = tasks.TASKS[r["task"]]["hazard"]
        row["test"] = row["class"] != tasks.CONTROL_CLASS
        row["functional"] = r["functional"]["passed"]
        if row["functional"]:
            optimal = tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"]
            c = r["concurrent"]
            row["violating"] = violates(c[default_arm(plan, engine, r["framework"])])
            row["retry_restored"] = restored_at(c["serializable retry"], trials, optimal, share)
            row["replay_restored"] = restored_at(c["request replay"], trials, optimal, share)
        rows.append(row)
    return rows


def key_of(row: dict[str, Any]) -> tuple:
    return tuple(row[k] for k in ("model", "mode", "task", "framework", "condition", "seed"))


def sweep_table(folders: list[Path]) -> dict[str, Any]:
    table: dict[str, Any] = {}
    for folder in folders:
        engine = json.loads((folder / "manifest.json").read_text("utf-8"))["engine"]
        complete = (folder / "completion.json").exists()
        for r in load_results(folder):
            for arm, c in r["concurrent"].items():
                key = f"{engine}|{r['level']['name']}|{r['population']}|{arm}"
                cell = table.setdefault(
                    key,
                    {
                        "implementations": 0,
                        "seconds": [],
                        "retries": 0,
                        "trials": 0,
                        "successes": 0,
                        "possible": 0,
                        "violating": 0,
                        "complete": complete,
                    },
                )
                cell["implementations"] += 1
                cell["seconds"] += c["seconds"]
                cell["retries"] += c["retries"]
                cell["trials"] += c["trials"]
                cell["successes"] += sum(c["successes"])
                cell["possible"] += r["optimum"] * c["trials"]
                cell["violating"] += violates(c)
                cell["complete"] &= complete
    for cell in table.values():
        seconds = sorted(cell.pop("seconds"))
        cell["median_trial_seconds"] = seconds[len(seconds) // 2] if seconds else None
        cell["retries_per_trial"] = cell["retries"] / cell["trials"] if cell["trials"] else None
        cell["success_share"] = cell["successes"] / cell["possible"] if cell["possible"] else None
    return table


def report(args: argparse.Namespace) -> dict[str, Any]:
    plan = configuration()
    rule = plan["hypotheses"]
    z = rule["confidence_z"]
    runs: dict[str, list[dict]] = {}
    for folder in map(Path, args.runs):
        manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
        if manifest["version"] != VERSION:
            raise ValueError(f"{folder} belongs to another plan")
        runs[manifest["engine"]] = load_results(folder)
    if set(runs) != set(ENGINES):
        raise ValueError("report needs one main run per engine")
    gates = {e: reference_gates(plan, e, runs[e]) for e in ENGINES}
    valid = all(g["correct_clean"] and not g["buggy_missed"] for g in gates.values())
    rows = {e: rows_of(plan, e, runs[e]) for e in ENGINES}
    pg = rows["postgresql"]

    floor: dict[str, Any] = {}
    for row in (r for r in pg if r["mode"] == "single"):
        cell = floor.setdefault(f"{row['model']}|{row['framework']}", Counter())
        cell["implementations"] += 1
        cell["functional"] += row["functional"]
    for cell in floor.values():
        cell["rate"] = cell["functional"] / cell["implementations"]
        cell["included"] = cell["rate"] >= rule["min_functional_pass_rate"]

    hypotheses: dict[str, Any] = {}
    new_models = sorted(
        {r["model"] for r in pg if r["mode"] == "single"}
        & {k for k, m in plan["models"].items() if m["source"] == "benchmark"}
    )
    for key in ("h1", "h2"):
        test = rule[key]
        per_model = {}
        for model in new_models:
            cells = {}
            for name in tasks.TEST_CLASSES:
                chosen = [
                    r
                    for r in pg
                    if r["model"] == model
                    and r["mode"] == "single"
                    and r["functional"]
                    and r["condition"] == test["condition"]
                    and r["class"] == name
                    and floor[f"{model}|{r['framework']}"]["included"]
                ]
                cell = proportion(sum(r["violating"] for r in chosen), len(chosen), z)
                cell["passes"] = (
                    cell["n"] >= test["min_class_implementations"]
                    and cell["share"] >= test["share"]
                    and cell["lower"] >= test["lower_bound"]
                )
                cells[name] = cell
            per_model[model] = {
                "classes": cells,
                "holds": sum(c["passes"] for c in cells.values()) >= test["classes"],
            }
        hypotheses[key.upper()] = {
            "models": per_model,
            "holds": bool(per_model) and all(m["holds"] for m in per_model.values()),
        }

    def functional_test(mode: str, rows_: list[dict]) -> list[dict]:
        return [r for r in rows_ if r["mode"] == mode and r["test"] and r["functional"]]

    seq = functional_test("loop_seq", pg)
    h3 = proportion(sum(r["violating"] for r in seq), len(seq), z)
    h3["holds"] = (
        h3["n"] >= rule["h3"]["min_implementations"]
        and h3["share"] >= rule["h3"]["share"]
        and h3["lower"] >= rule["h3"]["lower_bound"]
    )
    hypotheses["H3"] = h3

    passed = [r for r in pg if r["mode"] == "loop_conc" and r["test"] and r["loop_passed"]]
    held = [r for r in passed if r["functional"]]
    h4 = proportion(sum(r["violating"] for r in held), len(held), z)
    h4["loop_passed"] = len(passed)
    h4["not_functional_in_harness"] = len(passed) - len(held)
    h4["holds"] = (
        h4["n"] >= rule["h4"]["min_implementations"]
        and h4["share"] <= rule["h4"]["share_max"]
        and h4["upper"] <= rule["h4"]["upper_bound_max"]
    )
    hypotheses["H4"] = h4

    mysql = {key_of(r): r for r in rows["mysql"]}
    paired = [
        (r, mysql[key_of(r)])
        for r in pg
        if r["test"] and r["functional"] and key_of(r) in mysql and mysql[key_of(r)]["functional"]
    ]
    discordant = [(a, b) for a, b in paired if a["violating"] != b["violating"]]
    h5 = proportion(len(discordant), len(paired), z)
    h5["postgresql_only"] = sum(a["violating"] for a, _ in discordant)
    h5["mysql_only"] = sum(b["violating"] for _, b in discordant)
    h5["not_functional_on_mysql"] = sum(
        1
        for r in pg
        if r["test"] and r["functional"] and not mysql.get(key_of(r), {}).get("functional")
    )
    h5["holds"] = (
        h5["n"] >= rule["h5"]["min_implementations"]
        and h5["share"] <= rule["h5"]["share_max"]
        and h5["upper"] <= rule["h5"]["upper_bound_max"]
    )
    hypotheses["H5"] = h5

    for key, field in (("H6", "retry_restored"), ("H7", "replay_restored")):
        per_engine = {}
        for engine in ENGINES:
            anomalous = [
                r for r in rows[engine] if r["test"] and r["functional"] and r["violating"]
            ]
            if key == "H6":
                cell = proportion(sum(not r[field] for r in anomalous), len(anomalous), z)
                cell["holds"] = (
                    cell["n"] >= rule["h6"]["min_implementations"]
                    and cell["share"] >= rule["h6"]["share"]
                    and cell["lower"] >= rule["h6"]["lower_bound"]
                )
            else:
                cell = proportion(sum(r[field] for r in anomalous), len(anomalous), z)
                cell["unrestored_upper"] = 1 - cell["lower"]
                cell["holds"] = (
                    cell["n"] >= rule["h7"]["min_implementations"]
                    and cell["share"] >= rule["h7"]["share"]
                )
            per_engine[engine] = cell
        hypotheses[key] = {
            "engines": per_engine,
            "holds": all(c["holds"] for c in per_engine.values()),
        }

    cells: dict[str, Any] = {}
    for engine in ENGINES:
        for r in rows[engine]:
            cell = cells.setdefault(
                f"{engine}|{r['model']}|{r['mode']}|{r['condition']}", Counter()
            )
            cell["implementations"] += 1
            cell["functional"] += r["functional"]
            if r["test"] and r["functional"]:
                cell["test_functional"] += 1
                cell["violating"] += r["violating"]
                if r["violating"]:
                    cell["retry_restored"] += r["retry_restored"]
                    cell["replay_restored"] += r["replay_restored"]
    out = {
        "version": VERSION,
        "valid_harness": valid,
        "reference_gates": gates,
        "functional_floor": {k: dict(v) for k, v in floor.items()},
        "hypotheses": hypotheses,
        "holds": {k: v["holds"] for k, v in hypotheses.items()} if valid else None,
        "cells": {k: dict(v) for k, v in sorted(cells.items())},
        "sweep": sweep_table([Path(f) for f in args.sweeps]),
    }
    if args.save:
        harness.atomic_write(Path(args.save), out)
    print(json.dumps({"valid_harness": valid, "holds": out["holds"]}, indent=1))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    r = commands.add_parser("run")
    s = commands.add_parser("sweep")
    for p in (r, s):
        p.add_argument("--engine", choices=ENGINES, required=True)
        p.add_argument("--output", required=True)
        p.add_argument("--bin", required=True, help="directory with the engine's server binaries")
        p.add_argument("--scratch")
        p.add_argument("--sockets")
        p.add_argument("--limit", type=int)
    r.add_argument("--generations", nargs="*", help="generation directories (the base stage's format)")
    s.add_argument("--run", required=True, help="the engine's main `run` output")
    s.add_argument("--level", help="one sweep level by name; all levels when omitted")
    m = commands.add_parser("summary")
    m.add_argument("--output", required=True)
    m.add_argument("--save")
    rp = commands.add_parser("report")
    rp.add_argument("--runs", nargs=2, required=True, help="the two engines' main `run` outputs")
    rp.add_argument("--sweeps", nargs="*", default=[], help="`sweep` outputs (any engine, level)")
    rp.add_argument("--save")
    args = parser.parse_args()
    commands_by_name = {"run": run, "sweep": sweep, "summary": summary, "report": report}
    commands_by_name[args.command](args)


if __name__ == "__main__":
    main()
