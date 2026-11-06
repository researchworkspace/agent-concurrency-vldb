"""Functional and concurrent checks of generated programs on PostgreSQL (C0).

Usage: python -I c0_harness.py run --output DIR --pg-bin DIR [--generations DIR ...] [--references-only]
       python -I c0_harness.py report --output DIR [--generations DIR ...] --save FILE
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import multiprocessing
import os
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
PLAN = FOLDER / "c0-plan.json"
sys.path.insert(0, str(FOLDER))
import c0_tasks as tasks

VERSION = "agent-concurrency-c0-v1"


def configuration() -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(PLAN.read_text("utf-8"))
    if plan["version"] != VERSION:
        raise ValueError("C0 plan differs from the implemented harness")
    return plan


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n", "utf-8")
    os.replace(temporary, path)


class Postgres:

    port = 5432

    def __init__(
        self, binaries: Path, root: Path, sockets: Path, settings: dict[str, str], log: Path
    ) -> None:
        self.binaries = binaries
        self.root = Path(tempfile.mkdtemp(prefix="c0pg-", dir=root))
        self.sockets = Path(tempfile.mkdtemp(prefix="c0s-", dir=sockets))
        self.settings = settings
        self.log = log
        self.admin: Any = None

    def __enter__(self) -> Postgres:
        data = self.root / "data"
        subprocess.run(
            [
                str(self.binaries / "initdb"),
                "-D",
                str(data),
                "-U",
                "c0",
                "--auth=trust",
                "--encoding=UTF8",
                "--locale=C",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        options = {
            **self.settings,
            "listen_addresses": "''",
            "port": str(self.port),
            "unix_socket_directories": str(self.sockets),
        }
        flags = " ".join(f"-c {key}={value}" for key, value in sorted(options.items()))
        subprocess.run(
            [
                str(self.binaries / "pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(self.log),
                "-o",
                flags,
                "-w",
                "start",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        with self.connect("postgres") as admin:
            admin.execute("CREATE DATABASE c0")
        self.admin = self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        if self.admin is not None:
            self.admin.close()
        subprocess.run(
            [
                str(self.binaries / "pg_ctl"),
                "-D",
                str(self.root / "data"),
                "-m",
                "immediate",
                "stop",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
        )
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.sockets, ignore_errors=True)

    def connect(self, name: str = "c0") -> Any:
        import psycopg

        return psycopg.connect(
            host=str(self.sockets), port=self.port, user="c0", dbname=name, autocommit=True
        )

    def set_isolation(self, level: str) -> None:
        with self.connect("postgres") as admin:
            admin.execute(f"ALTER DATABASE c0 SET default_transaction_isolation TO '{level}'")

    def reset(self, task: str, seed: list[str]) -> None:
        db = self.admin
        db.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = 'c0' AND pid <> pg_backend_pid()"
        )
        for table in tasks.TASKS[task]["tables"]:
            db.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
        for statement in [*tasks.TASKS[task]["ddl"], *seed]:
            db.execute(statement)

    def value(self, sql: str) -> Any:
        row = self.admin.execute(sql).fetchone()
        return row[0] if row else None


def support_directory(root: Path, task: str, framework: str) -> Path:
    folder = root / "support" / task / framework
    if framework == "sqlalchemy":
        target = folder / "app_models.py"
    else:
        (folder / "shop").mkdir(parents=True, exist_ok=True)
        (folder / "shop" / "__init__.py").write_text("", "utf-8")
        target = folder / "shop" / "models.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(tasks.models_module(task, framework), "utf-8")
    return folder


def sqlstate(error: BaseException) -> str | None:
    seen: set[int] = set()
    pending: list[Any] = [error]
    while pending:
        item = pending.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        state = getattr(item, "sqlstate", None)
        if isinstance(state, str):
            return state
        pending += [getattr(item, "orig", None), item.__cause__, item.__context__]
    return None


def client(config: dict[str, Any], inbox: Any, outbox: Any, barrier: Any) -> None:
    try:
        sys.path.insert(0, config["support"])
        if config["framework"] == "django":
            import django
            from django.conf import settings

            settings.configure(
                DATABASES={
                    "default": {
                        "ENGINE": "django.db.backends.postgresql",
                        "NAME": "c0",
                        "USER": "c0",
                        "HOST": config["host"],
                        "PORT": str(config["port"]),
                        "CONN_MAX_AGE": 0,
                    }
                },
                INSTALLED_APPS=["shop"],
                USE_TZ=True,
                DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            )
            django.setup()
            from django.db import connection

            prefix: list[Any] = []
            finish = connection.close
        else:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from sqlalchemy.pool import NullPool

            engine = create_engine(
                "postgresql+psycopg://c0@/c0",
                connect_args={"host": config["host"], "port": config["port"]},
                poolclass=NullPool,
            )
            prefix = [sessionmaker(bind=engine)]

            def finish() -> None:
                return None

        spec = importlib.util.spec_from_file_location("c0_implementation", config["path"])
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        function = getattr(module, config["function"])
    except BaseException as error:
        outbox.put({"setup_error": f"{type(error).__name__}: {error}"[:500]})
        return
    outbox.put({"ready": True})
    while True:
        message = inbox.get()
        if message is None:
            return
        if message["sync"]:
            try:
                barrier.wait(timeout=30)
            except Exception:
                outbox.put({"barrier_broken": True})
                continue
        retry = config["retry"]
        outcomes = []
        for kwargs in message["calls"]:
            started = time.perf_counter()
            attempts = 0
            while True:
                attempts += 1
                try:
                    result = function(*prefix, **kwargs)
                    ok = result.get("ok") if isinstance(result, dict) else None
                    outcome: dict[str, Any] = {"ok": ok if isinstance(ok, bool) else None}
                    if outcome["ok"] is False:
                        outcome["error"] = str(result.get("error"))[:80]
                except Exception as error:
                    outcome = {
                        "exception": type(error).__name__,
                        "sqlstate": sqlstate(error),
                        "message": str(error)[:200],
                    }
                try:
                    finish()
                except Exception:
                    pass
                if outcome.get("sqlstate") in retry["sqlstates"] and attempts <= config["retries"]:
                    cap = retry["backoff_cap_seconds"]
                    time.sleep(
                        random.uniform(0, min(cap, retry["backoff_base_seconds"] * 2**attempts))
                    )
                    continue
                break
            outcome["attempts"] = attempts
            outcome["seconds"] = round(time.perf_counter() - started, 4)
            outcomes.append(outcome)
        outbox.put({"outcomes": outcomes})


class Clients:

    def __init__(self, count: int, config: dict[str, Any], timeout: float) -> None:
        context = multiprocessing.get_context("spawn")
        self.timeout = timeout
        self.barrier = context.Barrier(count)
        self.inboxes = [context.Queue() for _ in range(count)]
        self.outboxes = [context.Queue() for _ in range(count)]
        self.processes = [
            context.Process(target=client, args=(config, inbox, outbox, self.barrier), daemon=True)
            for inbox, outbox in zip(self.inboxes, self.outboxes)
        ]
        for process in self.processes:
            process.start()
        self.setup_error: str | None = None
        for reply in self.collect(timeout * 2):
            if reply is None or "setup_error" in reply:
                self.setup_error = (reply or {}).get("setup_error", "client did not start")
                break

    def collect(self, timeout: float) -> list[dict[str, Any] | None]:
        deadline = time.monotonic() + timeout
        replies: list[dict[str, Any] | None] = []
        for outbox in self.outboxes:
            try:
                replies.append(outbox.get(timeout=max(0.1, deadline - time.monotonic())))
            except queue.Empty:
                replies.append(None)
        return replies

    def run(self, plans: list[list[dict[str, Any]]], sync: bool) -> list[dict[str, Any] | None]:
        for inbox, calls in zip(self.inboxes, plans):
            inbox.put({"calls": calls, "sync": sync})
        return self.collect(self.timeout)

    def close(self) -> None:
        for inbox in self.inboxes:
            try:
                inbox.put(None)
            except Exception:
                pass
        for process in self.processes:
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)


def functional(db: Postgres, item: dict[str, Any], config: dict[str, Any], timeout: float) -> dict:
    spec = tasks.TASKS[item["task"]]["functional"]
    db.set_isolation("read committed")
    db.reset(item["task"], spec["seed"])
    pool = Clients(1, config, timeout)
    try:
        if pool.setup_error:
            return {"passed": False, "setup_error": pool.setup_error}
        reply = pool.run([[kwargs for kwargs, _ in spec["calls"]]], sync=False)[0]
    finally:
        pool.close()
    if reply is None:
        return {"passed": False, "hung": True}
    outcomes = reply["outcomes"]
    calls_ok = [o.get("ok") is expected for o, (_, expected) in zip(outcomes, spec["calls"])]
    state_ok = [db.value(sql) == expected for sql, expected in spec["state"]]
    return {
        "passed": all(calls_ok) and all(state_ok),
        "calls_ok": calls_ok,
        "state_ok": state_ok,
        "outcomes": outcomes,
    }


def concurrent(
    db: Postgres,
    item: dict[str, Any],
    config: dict[str, Any],
    arm: dict[str, Any],
    trials: int,
    harness: dict[str, Any],
) -> dict[str, Any]:
    task = item["task"]
    workers = harness["workers"]
    plans = [tasks.request_plan(task, w) for w in range(workers)]
    db.set_isolation(arm["isolation"])
    db.reset(task, tasks.TASKS[task]["concurrent"]["seed"])
    config = {**config, "retries": arm["retries"]}
    pool = Clients(workers, config, harness["trial_timeout_seconds"])
    summary: dict[str, Any] = {
        "trials": 0,
        "violating_trials": 0,
        "violations": Counter(),
        "exceptions": Counter(),
        "rejections": Counter(),
        "successes": [],
        "seconds": [],
        "retries": 0,
        "retries_exhausted": 0,
        "exception_messages": {},
        "hung": False,
        "setup_error": pool.setup_error,
    }
    try:
        for _ in range(trials if not pool.setup_error else 0):
            db.reset(task, tasks.TASKS[task]["concurrent"]["seed"])
            started = time.perf_counter()
            replies = pool.run(plans, sync=True)
            elapsed = time.perf_counter() - started
            if any(r is None or "outcomes" not in r for r in replies):
                summary["hung"] = True
                break
            outcomes = [o for r in replies if r for o in r["outcomes"]]
            found = tasks.invariant(task, db.value)
            summary["trials"] += 1
            summary["violating_trials"] += bool(found)
            summary["violations"].update(found)
            summary["successes"].append(sum(o.get("ok") is True for o in outcomes))
            summary["seconds"].append(round(elapsed, 4))
            summary["retries"] += sum(o["attempts"] - 1 for o in outcomes)
            for o in (o for o in outcomes if "exception" in o):
                key = f"{o['exception']}:{o['sqlstate']}"
                summary["exceptions"][key] += 1
                summary["exception_messages"].setdefault(key, o["message"])
                if o["sqlstate"] in config["retry"]["sqlstates"] and arm["retries"]:
                    summary["retries_exhausted"] += 1
            summary["rejections"].update(o["error"] for o in outcomes if o.get("ok") is False)
    finally:
        pool.close()
    for key in ("violations", "exceptions", "rejections"):
        summary[key] = dict(summary[key])
    return summary


def inventory(output: Path, generations: list[Path], references_only: bool) -> list[dict]:
    items = []
    for (task, framework, variant), code in sorted(tasks.REFERENCES.items()):
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
    for folder in [] if references_only else generations:
        for record_path in sorted((folder / "generations").glob("*.json")):
            record = json.loads(record_path.read_text("utf-8"))
            item = {
                "id": f"generated:{record['model']}:{record_path.stem}",
                "source": "generated",
                **{k: record[k] for k in ("model", "task", "framework", "condition", "seed")},
            }
            if record["code"] is None:
                item["path"] = None
            else:
                path = output / "implementations" / record["model"] / f"{record_path.stem}.py"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(record["code"], "utf-8")
                item["path"] = str(path)
            items.append(item)
    return items


def run(args: argparse.Namespace) -> None:
    plan = configuration()
    harness = plan["harness"]
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": VERSION,
        "plan_sha256": file_hash(PLAN),
        "harness_sha256": file_hash(Path(__file__)),
        "tasks_sha256": file_hash(FOLDER / "c0_tasks.py"),
        "generations": sorted(str(Path(g).resolve()) for g in args.generations or []),
        "references_only": args.references_only,
    }
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text("utf-8")) != manifest:
            raise ValueError("existing output belongs to a different run; use a new output path")
    else:
        atomic_write(manifest_path, manifest)
    items = inventory(output, [Path(g) for g in args.generations or []], args.references_only)
    items = items[: args.limit] if args.limit else items
    root = Path(args.scratch or tempfile.gettempdir())
    started = time.time()
    sockets = Path(args.sockets or tempfile.gettempdir())
    log = output / "postgres.log"
    with Postgres(Path(args.pg_bin), root, sockets, harness["server_settings"], log) as db:
        environment = {
            "server_version": db.value("SHOW server_version"),
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
                config = {
                    "framework": item["framework"],
                    "function": tasks.TASKS[item["task"]]["function"],
                    "path": item["path"],
                    "host": str(db.sockets),
                    "port": db.port,
                    "support": str(support_directory(output, item["task"], item["framework"])),
                    "retry": harness["retry"],
                    "retries": 0,
                }
                timeout = harness["trial_timeout_seconds"]
                result["functional"] = functional(db, item, config, timeout)
                if result["functional"]["passed"]:
                    trials = harness[f"{item['source']}_trials"]
                    result["concurrent"] = {
                        name: concurrent(db, item, config, arm, trials, harness)
                        for name, arm in harness["arms"].items()
                    }
            atomic_write(target, result)
            print(f"{item['id']} functional={result['functional']['passed']}", flush=True)
    atomic_write(
        output / "completion.json",
        {
            "results": len(list((output / "results").glob("*.json"))),
            "items": len(items),
            "seconds": round(time.time() - started, 1),
        },
    )


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def wilson_lower(hits: int, total: int, z: float) -> float:
    if not total:
        return 0.0
    p = hits / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - margin) / (1 + z * z / total)


def restored(task: str, c: dict[str, Any], trials: int, share: float) -> bool:
    optimal = tasks.TASKS[task]["concurrent"]["optimal_successes"]
    return (
        c["trials"] == trials
        and not c["hung"]
        and c["violating_trials"] == 0
        and not c["exceptions"]
        and sum(c["successes"]) >= share * optimal * trials
    )


def proportion(hits: int, total: int, z: float) -> dict[str, Any]:
    return {
        "n": total,
        "k": hits,
        "share": rate(hits, total),
        "lower": wilson_lower(hits, total, z),
    }


def report(args: argparse.Namespace) -> dict[str, Any]:
    plan = configuration()
    rule = plan["decision"]
    z = rule["confidence_z"]
    harness = plan["harness"]
    arms = list(harness["arms"])
    first, retry_arm = arms[0], arms[-1]
    results = [
        json.loads(p.read_text("utf-8"))
        for p in sorted((Path(args.output) / "results").glob("*.json"))
    ]

    references: dict[str, Any] = {}
    separates = restores = bool(results)
    for r in (r for r in results if r["source"] == "reference"):
        row: dict[str, Any] = {"functional": r["functional"]["passed"]}
        optimal = tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"]
        for arm, c in r.get("concurrent", {}).items():
            row[arm] = {
                "trials": c["trials"],
                "violating_trials": c["violating_trials"],
                "trial_detection": rate(c["violating_trials"], c["trials"]),
                "success_share": rate(sum(c["successes"]), optimal * c["trials"]),
                "exceptions": c["exceptions"],
                "retries": c["retries"],
                "hung": c["hung"],
            }
        references[r["id"]] = row
        if not row["functional"] or set(row) != {"functional", *arms}:
            separates = False
            continue
        concurrent = r["concurrent"]
        if r["variant"] == "correct":
            separates &= all(
                concurrent[arm]["trials"] == harness["reference_trials"]
                and concurrent[arm]["violating_trials"]
                <= rule["reference_correct_max_violating_trials"]
                for arm in arms
            )
            separates &= row[first]["success_share"] == 1.0
            restores &= restored(r["task"], concurrent[retry_arm], harness["reference_trials"], 1.0)
        else:
            detection = row[first]["trial_detection"] or 0.0
            separates &= (
                1 - (1 - detection) ** harness["generated_trials"]
                >= rule["generated_detection_target"]
            )

    implementations = []
    for r in (r for r in results if r["source"] == "generated"):
        row = {k: r[k] for k in ("model", "framework", "task", "condition", "seed")}
        row["class"] = tasks.TASKS[r["task"]]["hazard"]
        row["code"] = r["path"] is not None
        row["functional"] = r["functional"]["passed"]
        for arm, c in r.get("concurrent", {}).items():
            optimal = tasks.TASKS[r["task"]]["concurrent"]["optimal_successes"]
            row[arm] = {
                "violating": c["violating_trials"] > 0 or c["hung"],
                "exceptions": sum(c["exceptions"].values()),
                "success_share": rate(sum(c["successes"]), optimal * c["trials"]),
                "median_seconds": sorted(c["seconds"])[len(c["seconds"]) // 2]
                if c["seconds"]
                else None,
            }
        if row["functional"]:
            c = r["concurrent"][retry_arm]
            row[retry_arm]["restored"] = restored(
                r["task"], c, harness["generated_trials"], rule["restored_success_share"]
            )
        implementations.append(row)

    passes: dict[str, Any] = {}
    for row in implementations:
        cell = passes.setdefault(f"{row['model']}|{row['framework']}", Counter())
        cell["implementations"] += 1
        cell["functional"] += row["functional"]
    for cell in passes.values():
        cell["rate"] = rate(cell["functional"], cell["implementations"])
        cell["included"] = (cell["rate"] or 0) >= rule["min_functional_pass_rate"]
    analysed = [
        row
        for row in implementations
        if row["functional"] and passes[f"{row['model']}|{row['framework']}"]["included"]
    ]

    def classes(model: str, condition: str, test: dict[str, Any]) -> dict[str, Any]:
        out = {}
        for name in tasks.TEST_CLASSES:
            rows = [
                r
                for r in analysed
                if r["model"] == model and r["condition"] == condition and r["class"] == name
            ]
            cell = proportion(sum(r[first]["violating"] for r in rows), len(rows), z)
            cell["passes"] = (
                cell["n"] >= rule["min_class_implementations"]
                and cell["share"] >= test["share"]
                and cell["lower"] >= test["lower_bound"]
            )
            out[name] = cell
        return out

    hypotheses: dict[str, Any] = {"H1": {}, "H2": {}}
    for model in plan["models"]:
        for key, condition in (("H1", "plain"), ("H2", "hint")):
            test = rule[key.lower()]
            cells = classes(model, condition, test)
            hypotheses[key][model] = {
                "classes": cells,
                "holds": sum(c["passes"] for c in cells.values()) >= test["classes"],
            }
    anomalous = [r for r in analysed if r["class"] in tasks.TEST_CLASSES and r[first]["violating"]]
    h3 = proportion(sum(not r[retry_arm]["restored"] for r in anomalous), len(anomalous), z)
    h3["holds"] = (
        h3["n"] >= rule["h3"]["min_implementations"]
        and h3["share"] >= rule["h3"]["share"]
        and h3["lower"] >= rule["h3"]["lower_bound"]
    )
    hypotheses["H3"] = h3
    holds = {
        "H1": all(v["holds"] for v in hypotheses["H1"].values()),
        "H2": all(v["holds"] for v in hypotheses["H2"].values()),
        "H3": h3["holds"],
    }
    covered = all(
        any(passes.get(f"{m}|{f}", {}).get("included") for f in tasks.FRAMEWORKS)
        for m in plan["models"]
    )
    if not (separates and restores):
        outcome = "INVALID_HARNESS"
    elif not implementations:
        outcome = "NOT_RUN"
    elif not covered:
        outcome = "INCONCLUSIVE"
    elif not holds["H1"]:
        outcome = "NO_GO"
    elif holds["H2"] and holds["H3"]:
        outcome = "GO"
    else:
        outcome = "NARROW"

    cells: dict[str, Any] = {}
    for row in implementations:
        cell = cells.setdefault(f"{row['model']}|{row['task']}|{row['condition']}", Counter())
        cell["implementations"] += 1
        cell["functional"] += row["functional"]
        if not row["functional"]:
            continue
        for arm in arms:
            cell[f"violating|{arm}"] += row[arm]["violating"]
            cell[f"raising|{arm}"] += row[arm]["exceptions"] > 0
        cell[f"not_restored|{retry_arm}"] += not row[retry_arm]["restored"]
    shares: dict[str, Any] = {}
    for task in tasks.TASKS:
        for arm in arms:
            values = [r[arm]["success_share"] for r in analysed if r["task"] == task and arm in r]
            if values:
                shares[f"{task}|{arm}"] = sum(values) / len(values)

    throughput: dict[str, Any] = {}
    for folder in args.generations or []:
        journal = Path(folder) / "journal.jsonl"
        if journal.exists():
            rows = [json.loads(line) for line in journal.read_text("utf-8").splitlines() if line]
            tokens = sum(row["output_tokens"] for row in rows)
            seconds = sum(row["seconds"] for row in rows)
            throughput[Path(folder).name] = {
                "calls": len(rows),
                "output_tokens": tokens,
                "mean_output_tokens": rate(tokens, len(rows)),
                "tokens_per_second": rate(tokens, int(seconds) or 1),
                "cap_reached": sum(bool(row["cap_reached"]) for row in rows),
            }
    summary = {
        "version": VERSION,
        "outcome": outcome,
        "gates": {
            "references_separate": separates,
            "retry_restores_correct_references": restores,
            "functional_pass": {k: dict(v) for k, v in sorted(passes.items())},
            "every_generator_covered": covered,
        },
        "holds": holds,
        "hypotheses": hypotheses,
        "references": references,
        "cells": {k: dict(v) for k, v in sorted(cells.items())},
        "mean_success_share": shares,
        "throughput": throughput,
    }
    if args.save:
        atomic_write(Path(args.save), summary)
        atomic_write(Path(args.save).with_suffix(".implementations.json"), implementations)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    r = commands.add_parser("run")
    r.add_argument("--output", required=True)
    r.add_argument("--pg-bin", required=True, help="directory with initdb and pg_ctl")
    r.add_argument("--generations", action="append", help="a c0_generate.py model folder")
    r.add_argument("--references-only", action="store_true")
    r.add_argument("--scratch", help="where the private cluster lives (default: temp dir)")
    r.add_argument("--sockets", help="short directory for the server socket (default: temp dir)")
    r.add_argument("--limit", type=int, default=0, help="tests only: first N implementations")
    s = commands.add_parser("report")
    s.add_argument("--output", required=True)
    s.add_argument("--generations", action="append")
    s.add_argument("--save")
    args = parser.parse_args()
    if args.command == "run":
        run(args)
    else:
        print(json.dumps(report(args), indent=1))


if __name__ == "__main__":
    main()
