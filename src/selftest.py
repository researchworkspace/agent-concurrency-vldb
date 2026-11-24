"""Self-test loop: the model writes a concurrency test, which runs against its own program."""

from __future__ import annotations

import argparse
import ast
import json
import linecache
import multiprocessing
import os
import queue
import sys
import time
import traceback
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
import tasks

MAX_REQUESTS = 16
TEST_FUNCTION = "test_concurrency"
TEST_FILE = "test_concurrency.py"


TEST_REQUEST = """Your module passes the application's sequential tests. In production, `{function}` is called by many concurrent HTTP requests. Write a concurrency test for it: a Python function `test_concurrency()` that raises `AssertionError` when `{function}` misbehaves under concurrent requests.

The test runs in a harness that already provides these functions (do not import or define them):

- `fresh_database()`: {fresh} The harness calls it before every run; call it again if your test needs a clean state.
- `db_execute(sql, params=None)`: runs one SQL statement on the application's {engine} database and returns the result rows as a list of tuples (an empty list for statements that return no rows). Use it to insert the rows your test needs and to inspect the database afterwards. Parameters use the `%s` placeholder.
- `run_concurrently(requests)`: `requests` is a list of at most {limit} dicts of keyword arguments for `{function}`. Each request is sent to your current module by its own client process, and all requests are released at the same moment. It returns a list with, for each request in order, the dict that `{function}` returned, or `{{"exception": "<ExceptionClass>: <message>"}}` if it raised.

The tables are:

```sql
{ddl}
```

The harness runs your test {runs} times. Respond with the test in a single ```python code block that defines `test_concurrency()`."""

FEEDBACK = """Your concurrency test failed in {failed} of {runs} runs. The first failure:

```
{message}
```

Fix the module so that the test passes. If you conclude that the test itself is wrong, you may also give a corrected `test_concurrency()` in a second ```python code block. Respond with the complete module in a ```python code block."""


FRESH = {
    "benchmark": "drops and recreates the application's tables, empty.",
    "app": "empties the tables below (and rows of other tables that refer to them).",
}


def test_request(task: str, runs: int, engine: str = "PostgreSQL") -> str:
    return TEST_REQUEST.format(
        function=tasks.TASKS[task]["function"],
        fresh=FRESH["benchmark"],
        engine=engine,
        limit=MAX_REQUESTS,
        ddl=";\n".join(tasks.TASKS[task]["ddl"]) + ";",
        runs=runs,
    )


def app_test_request(handler: str, runs: int) -> str:
    import apps

    return TEST_REQUEST.format(
        function=apps.HANDLERS[handler]["function"],
        fresh=FRESH["app"],
        engine="PostgreSQL",
        limit=MAX_REQUESTS,
        ddl=apps.SCHEMA[apps.HANDLERS[handler]["app"]],
        runs=runs,
    )


def selftest_feedback(outcome: dict[str, Any]) -> str | None:
    if outcome["failed"] == 0:
        return None
    return FEEDBACK.format(
        failed=outcome["failed"], runs=outcome["runs"], message=outcome["first_failure"][-1500:]
    )


def classify(code: str | None) -> dict[str, Any]:
    if code is None:
        return {"parsed": False}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {"parsed": False}
    function = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == TEST_FUNCTION),
        None,
    )
    if function is None:
        return {"parsed": True, "defines_test": False}

    def calls(node: ast.AST, name: str) -> bool:
        return any(
            isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id == name
            for c in ast.walk(node)
        )

    origin: dict[str, str] = {}
    for node in ast.walk(function):
        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        source = (
            "state"
            if calls(value, "db_execute")
            else "results"
            if calls(value, "run_concurrently")
            else None
        )
        if source is None:
            used = {n.id for n in ast.walk(value) if isinstance(n, ast.Name)}
            source = next((origin[u] for u in used if u in origin), None)
        if source:
            for target in targets:
                for n in ast.walk(target):
                    if isinstance(n, ast.Name):
                        origin[n.id] = source
    kinds: set[str] = set()
    asserts = [n for n in ast.walk(function) if isinstance(n, ast.Assert)]
    for node in asserts:
        if calls(node, "db_execute"):
            kinds.add("state")
        if calls(node, "run_concurrently"):
            kinds.add("results")
        kinds |= {origin[n.id] for n in ast.walk(node) if isinstance(n, ast.Name) and n.id in origin}
    return {
        "parsed": True,
        "defines_test": True,
        "concurrent": calls(function, "run_concurrently"),
        "assertions": len(asserts),
        "asserts_state": "state" in kinds,
        "asserts_results": "results" in kinds,
    }


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def boot_benchmark(config: dict[str, Any]) -> tuple[Any, Any]:
    sys.path.insert(0, config["support"])
    mysql = config["engine"] == "mysql"
    if config["framework"] == "django":
        import django
        from django.conf import settings

        if mysql:
            database = {
                "ENGINE": "django.db.backends.mysql",
                "NAME": "app",
                "USER": "root",
                "HOST": config["host"],
                "CONN_MAX_AGE": 0,
                "OPTIONS": {"isolation_level": config["isolation"]},
            }
        else:
            database = {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "app",
                "USER": "app",
                "HOST": config["host"],
                "PORT": str(config["port"]),
                "CONN_MAX_AGE": 0,
            }
        settings.configure(
            DATABASES={"default": database},
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

        if mysql:
            engine = create_engine(
                "mysql+mysqldb://root@localhost/app",
                connect_args={"unix_socket": config["host"]},
                poolclass=NullPool,
            )
        else:
            engine = create_engine(
                "postgresql+psycopg://app@/app",
                connect_args={"host": config["host"], "port": config["port"]},
                poolclass=NullPool,
            )
        prefix = [sessionmaker(bind=engine)]

        def finish() -> None:
            return None

    import importlib.util

    spec = importlib.util.spec_from_file_location("implementation_under_test", config["path"])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, config["function"])

    def call(kwargs: dict[str, Any]) -> Any:
        return function(*prefix, **kwargs)

    return call, finish


def client(config: dict[str, Any], inbox: Any, outbox: Any, barrier: Any) -> None:
    try:
        if config.get("app"):
            import app_harness

            call, finish = app_harness.boot(config)
        else:
            call, finish = boot_benchmark(config)
    except BaseException as error:
        outbox.put({"setup_error": f"{type(error).__name__}: {error}"[:500]})
        return
    outbox.put({"ready": True})
    message = inbox.get()
    if message is None:
        return
    try:
        barrier.wait(timeout=30)
    except Exception:
        outbox.put({"result": {"exception": "BarrierBroken: clients did not start together"}})
        return
    try:
        value = call(message)
        result = jsonable(value) if isinstance(value, dict) else {"returned": repr(value)[:200]}
    except BaseException as error:
        result = {"exception": f"{type(error).__name__}: {error}"[:300]}
    try:
        finish()
    except Exception:
        pass
    outbox.put({"result": result})


def run_requests(config: dict[str, Any], requests: list[dict[str, Any]], timeout: float) -> list:
    context = multiprocessing.get_context("spawn")
    count = len(requests)
    barrier = context.Barrier(count)
    inboxes = [context.Queue() for _ in range(count)]
    outboxes = [context.Queue() for _ in range(count)]
    processes = [
        context.Process(target=client, args=(config, i, o, barrier), daemon=True)
        for i, o in zip(inboxes, outboxes)
    ]
    for p in processes:
        p.start()
    try:
        deadline = time.monotonic() + timeout
        for outbox in outboxes:
            reply = outbox.get(timeout=max(0.1, deadline - time.monotonic()))
            if "setup_error" in reply:
                raise RuntimeError(f"the module could not be loaded: {reply['setup_error']}")
        for inbox, kwargs in zip(inboxes, requests):
            inbox.put(kwargs)
        results = []
        for outbox in outboxes:
            try:
                results.append(outbox.get(timeout=max(0.1, deadline - time.monotonic()))["result"])
            except queue.Empty:
                results.append({"exception": "Timeout: the request did not finish in time"})
        return results
    finally:
        for p in processes:
            p.join(timeout=2)
            if p.is_alive():
                p.kill()
                p.join(timeout=5)


class Database:

    def __init__(self, config: dict[str, Any]) -> None:
        self.engine = config["engine"]
        self.task = config["task"]
        self.protected = config.get("protected_session")
        self.app = config.get("app")
        if self.engine == "mysql":
            import MySQLdb

            self.connection = MySQLdb.connect(unix_socket=config["host"], user="root", database="app")
            self.connection.autocommit(True)
        else:
            import psycopg

            self.connection = psycopg.connect(
                host=config["host"], port=config["port"], user="app", dbname="app", autocommit=True
            )

    def execute(self, sql: str, params: Any = None) -> list[tuple]:
        if not isinstance(sql, str):
            raise TypeError("db_execute expects an SQL string")
        cursor = self.connection.cursor()
        cursor.execute(sql, params) if params is not None else cursor.execute(sql)
        if cursor.description is None:
            return []
        return [tuple(row) for row in cursor.fetchall()]

    def fresh(self) -> None:
        if self.app:
            self.empty_app_tables()
            return
        if self.engine == "mysql":
            import mysql_server

            own = self.execute("SELECT CONNECTION_ID()")[0][0]
            for row in self.execute("SHOW FULL PROCESSLIST"):
                if row[3] == "app" and row[0] not in (own, self.protected):
                    try:
                        self.execute(f"KILL {int(row[0])}")
                    except Exception:
                        pass
            self.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in tasks.TASKS[self.task]["tables"]:
                self.execute(f"DROP TABLE IF EXISTS {table}")
            self.execute("SET FOREIGN_KEY_CHECKS = 1")
            statements = mysql_server.DDL[self.task]
        else:
            self.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'app' AND pid <> pg_backend_pid() AND pid <> %s",
                [self.protected or 0],
            )
            for table in tasks.TASKS[self.task]["tables"]:
                self.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
            statements = tasks.TASKS[self.task]["ddl"]
        for statement in statements:
            self.execute(statement)


    def end_other_sessions(self) -> None:
        if self.engine == "mysql":
            own = self.execute("SELECT CONNECTION_ID()")[0][0]
            for row in self.execute("SHOW FULL PROCESSLIST"):
                if row[3] == "app" and row[0] not in (own, self.protected):
                    try:
                        self.execute(f"KILL {int(row[0])}")
                    except Exception:
                        pass
        else:
            self.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = 'app' AND pid <> pg_backend_pid() AND pid <> %s",
                [self.protected or 0],
            )

    def empty_app_tables(self) -> None:
        import apps

        self.end_other_sessions()
        tables = apps.TABLES[self.app]
        if self.engine == "mysql":
            self.execute("SET FOREIGN_KEY_CHECKS = 0")
            for table in tables:
                self.execute(f"DELETE FROM {table}")
            self.execute("SET FOREIGN_KEY_CHECKS = 1")
        else:
            self.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")


def failure_text(error: BaseException) -> str:
    frames = [f for f in traceback.extract_tb(error.__traceback__) if f.filename == TEST_FILE]
    lines = ["Traceback (most recent call last):\n", *traceback.format_list(frames)]
    lines += traceback.format_exception_only(type(error), error)
    return "".join(lines)


def run(args: argparse.Namespace) -> None:
    config = json.loads(Path(args.config).read_text("utf-8"))
    db = Database(config)
    timeout = config["request_timeout_seconds"]

    def run_concurrently(requests: Any) -> list[dict[str, Any]]:
        if not isinstance(requests, (list, tuple)) or not all(isinstance(r, dict) for r in requests):
            raise TypeError("run_concurrently expects a list of keyword-argument dicts")
        if not 1 <= len(requests) <= MAX_REQUESTS:
            raise ValueError(f"run_concurrently takes 1 to {MAX_REQUESTS} requests")
        return run_requests(config, [dict(r) for r in requests], timeout)

    namespace: dict[str, Any] = {
        "__name__": "selftest_module",
        "fresh_database": db.fresh,
        "db_execute": db.execute,
        "run_concurrently": run_concurrently,
    }
    outcome: dict[str, Any] = {"runs": 0, "failed": 0, "first_failure": "", "seconds": []}
    try:
        source = Path(config["test_path"]).read_text("utf-8")
        linecache.cache[TEST_FILE] = (len(source), None, source.splitlines(True), TEST_FILE)
        exec(compile(source, TEST_FILE, "exec"), namespace)
        test = namespace[TEST_FUNCTION]
    except BaseException as error:
        outcome.update(runs=config["runs"], failed=config["runs"], load_error=True,
                       first_failure=failure_text(error))
        Path(config["outcome_path"]).write_text(json.dumps(outcome), "utf-8")
        return
    for _ in range(config["runs"]):
        db.fresh()
        started = time.perf_counter()
        try:
            test()
        except BaseException as error:
            outcome["failed"] += 1
            if not outcome["first_failure"]:
                kind = "assertion" if isinstance(error, AssertionError) else "error"
                outcome["first_failure_kind"] = kind
                outcome["first_failure"] = failure_text(error)
        outcome["runs"] += 1
        outcome["seconds"].append(round(time.perf_counter() - started, 3))
    Path(config["outcome_path"]).write_text(json.dumps(outcome), "utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    r = commands.add_parser("run")
    r.add_argument("--config", required=True)
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
