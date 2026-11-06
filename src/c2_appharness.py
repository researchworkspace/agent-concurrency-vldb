"""Functional and concurrent checks of programs that run inside django-oscar or CTFd.

Usage: python -I c2_appharness.py run --app oscar|ctfd --engine E --output DIR --bin DIR [--ctfd-source DIR] [--generations DIR ...]
       python -I c2_appharness.py gates --output DIR
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import multiprocessing
import os
import random
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
import c0_harness as harness
import c1_run
import c2_apps as apps

VERSION = "agent-concurrency-c2-v1"
DATABASE = "c0"


def oscar_settings(engine: str, host: str, port: int, isolation: str | None) -> dict[str, Any]:
    from oscar import defaults

    if engine == "mysql":
        options: dict[str, Any] = {"unix_socket": host, "charset": "utf8mb4"}
        if isolation:
            options["isolation_level"] = isolation
        database = {"ENGINE": "django.db.backends.mysql", "NAME": DATABASE, "USER": "root",
                    "CONN_MAX_AGE": 0, "OPTIONS": options}
    else:
        database = {"ENGINE": "django.db.backends.postgresql", "NAME": DATABASE, "USER": "c0",
                    "HOST": host, "PORT": str(port), "CONN_MAX_AGE": 0}
    oscar_apps = [
        "oscar.config.Shop", "oscar.apps.analytics.apps.AnalyticsConfig",
        "oscar.apps.checkout.apps.CheckoutConfig", "oscar.apps.address.apps.AddressConfig",
        "oscar.apps.shipping.apps.ShippingConfig", "oscar.apps.catalogue.apps.CatalogueConfig",
        "oscar.apps.catalogue.reviews.apps.CatalogueReviewsConfig",
        "oscar.apps.communication.apps.CommunicationConfig",
        "oscar.apps.partner.apps.PartnerConfig", "oscar.apps.basket.apps.BasketConfig",
        "oscar.apps.payment.apps.PaymentConfig", "oscar.apps.offer.apps.OfferConfig",
        "oscar.apps.order.apps.OrderConfig", "oscar.apps.customer.apps.CustomerConfig",
        "oscar.apps.search.apps.SearchConfig", "oscar.apps.voucher.apps.VoucherConfig",
        "oscar.apps.wishlists.apps.WishlistsConfig", "oscar.apps.dashboard.apps.DashboardConfig",
        "oscar.apps.dashboard.reports.apps.ReportsDashboardConfig",
        "oscar.apps.dashboard.users.apps.UsersDashboardConfig",
        "oscar.apps.dashboard.orders.apps.OrdersDashboardConfig",
        "oscar.apps.dashboard.catalogue.apps.CatalogueDashboardConfig",
        "oscar.apps.dashboard.offers.apps.OffersDashboardConfig",
        "oscar.apps.dashboard.partners.apps.PartnersDashboardConfig",
        "oscar.apps.dashboard.pages.apps.PagesDashboardConfig",
        "oscar.apps.dashboard.ranges.apps.RangesDashboardConfig",
        "oscar.apps.dashboard.reviews.apps.ReviewsDashboardConfig",
        "oscar.apps.dashboard.vouchers.apps.VouchersDashboardConfig",
        "oscar.apps.dashboard.communications.apps.CommunicationsDashboardConfig",
        "oscar.apps.dashboard.shipping.apps.ShippingDashboardConfig",
    ]
    return {
        **{k: getattr(defaults, k) for k in dir(defaults) if k.isupper()},
        "SECRET_KEY": "c2",
        "USE_TZ": True,
        "DATABASES": {"default": database},
        "INSTALLED_APPS": [
            "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
            "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
            "django.contrib.sites", "django.contrib.flatpages", *oscar_apps,
            "widget_tweaks", "haystack", "treebeard", "sorl.thumbnail", "django_tables2",
        ],
        "SITE_ID": 1,
        "HAYSTACK_CONNECTIONS": {
            "default": {"ENGINE": "haystack.backends.simple_backend.SimpleEngine"}},
        "DEFAULT_AUTO_FIELD": "django.db.models.AutoField",
    }


def ctfd_url(engine: str, host: str, port: int) -> str:
    if engine == "mysql":
        return f"mysql+pymysql://root@/{DATABASE}?unix_socket={host}"
    return f"postgresql+psycopg2://c0@/{DATABASE}?host={host}&port={port}"


def ctfd_app(engine: str, host: str, port: int, source: str) -> Any:
    os.chdir(source)
    sys.path.insert(0, source)
    import flask_sqlalchemy
    from sqlalchemy.pool import NullPool

    get_options = flask_sqlalchemy._EngineConnector.get_options
    if not getattr(get_options, "c2", False):
        def without_pool_sizes(self: Any, sa_url: Any, echo: Any) -> Any:
            sa_url, options = get_options(self, sa_url, echo)
            if options.get("poolclass") is NullPool:
                options.pop("pool_size", None)
                options.pop("pool_recycle", None)
            return sa_url, options

        without_pool_sizes.c2 = True
        flask_sqlalchemy._EngineConnector.get_options = without_pool_sizes

    from CTFd import create_app
    from CTFd.config import TestingConfig

    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = ctfd_url(engine, host, port)
        SQLALCHEMY_ENGINE_OPTIONS = {"poolclass": NullPool}
        CACHE_TYPE = "null"
        SERVER_NAME = "localhost"

    return create_app(Config)


def migrate(args: argparse.Namespace) -> None:
    if args.app == "oscar":
        import django
        from django.conf import settings

        settings.configure(**oscar_settings(args.engine, args.host, args.port, None))
        django.setup()
        from django.core.management import call_command

        call_command("migrate", verbosity=0)
    else:
        app = ctfd_app(args.engine, args.host, args.port, args.ctfd_source)
        with app.app_context():
            from CTFd.utils import set_config

            set_config("user_mode", "users")


def migrate_server(app: str, engine: str, db: Any, ctfd_source: str | None, log: Path) -> None:
    command = [sys.executable, str(Path(__file__)), "migrate", "--app", app, "--engine", engine,
               "--host", str(db.sockets), "--port", str(db.port)]
    command += ["--ctfd-source", ctfd_source] if ctfd_source else []
    with log.open("a", encoding="utf-8") as stream:
        subprocess.run(command, check=True, stdout=stream, stderr=stream, timeout=900)


def reset(db: Any, engine: str, app: str, statements: list[str]) -> None:
    tables = apps.TABLES[app]
    if engine == "mysql":
        own = db.execute("SELECT CONNECTION_ID()").fetchone()[0]
        for row in db.execute("SHOW FULL PROCESSLIST").fetchall():
            if row[3] == DATABASE and row[0] != own:
                with contextlib.suppress(Exception):
                    db.execute(f"KILL {int(row[0])}")
        db.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in tables:
            db.execute(f"DELETE FROM {table}")
        db.execute("SET FOREIGN_KEY_CHECKS = 1")
        for statement in statements:
            db.execute(statement)
        return
    db.admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{DATABASE}' AND pid <> pg_backend_pid()"
    )
    db.admin.execute(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
    for statement in statements:
        db.admin.execute(statement)
    for table in tables:
        db.admin.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        )


def app_sqlstate(error: BaseException) -> str | None:
    seen: set[int] = set()
    pending: list[Any] = [error]
    while pending:
        item = pending.pop()
        if item is None or id(item) in seen:
            continue
        seen.add(id(item))
        state = harness.sqlstate(item) if item is error else None
        if state:
            return state
        code = getattr(item, "pgcode", None)
        if isinstance(code, str):
            return code
        if type(item).__module__.startswith("pymysql") and item.args and isinstance(
                item.args[0], int):
            return "40001" if item.args[0] == 1213 else f"MY{item.args[0]}"
        pending += [getattr(item, "orig", None), item.__cause__, item.__context__]
    return None


def ctfd_request(function: Any, db: Any, retryable: list[str]) -> Any:
    from sqlalchemy import event

    seen: list[str | None] = []
    event.listen(db.engine, "handle_error",
                 lambda context: seen.append(app_sqlstate(context.original_exception)))

    def call(kwargs: dict[str, Any]) -> Any:
        seen.clear()
        original = db.session
        connection = db.engine.connect()
        outer = connection.begin()
        nested = [connection.begin_nested()]
        scoped = db.create_scoped_session(options={"bind": connection, "binds": {}})

        @event.listens_for(scoped(), "after_transaction_end")
        def restart(session: Any, transaction: Any) -> None:
            if not nested[0].is_active and outer.is_active:
                nested[0] = connection.begin_nested()

        db.session = scoped
        try:
            try:
                result = function(**kwargs)
            except Exception as error:
                with contextlib.suppress(Exception):
                    outer.rollback()
                state = next((s for s in seen if s in retryable), None)
                if state:
                    raise harness.ObservedFailure(state) from error
                raise
            state = next((s for s in seen if s in retryable), None)
            if state:
                with contextlib.suppress(Exception):
                    outer.rollback()
                raise harness.ObservedFailure(state)
            with contextlib.suppress(Exception):
                scoped.flush()
            outer.commit()
            return result
        finally:
            db.session = original
            with contextlib.suppress(Exception):
                scoped.remove()
            with contextlib.suppress(Exception):
                connection.close()

    return call


def boot(config: dict[str, Any]) -> tuple[Any, Any]:
    app_name = config["app"]
    engine = config["engine"]
    retryable = config["retry"]["sqlstates"]
    if app_name == "oscar":
        import django
        from django.conf import settings

        settings.configure(**oscar_settings(
            engine, config["host"], config["port"],
            config["isolation"] if engine == "mysql" else None))
        django.setup()
        from django.db import connection

        finish = connection.close
        context: Any = contextlib.nullcontext
    else:
        application = ctfd_app(engine, config["host"], config["port"], config["ctfd_source"])
        application.app_context().push()
        from CTFd.models import db

        def finish() -> None:
            db.session.remove()

        def context() -> Any:
            return application.test_request_context("/api/v1/", method="POST")

    spec = importlib.util.spec_from_file_location("c2_implementation", config["path"])
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, config["function"])
    if not config.get("request_transaction"):
        plain = lambda kwargs: function(**kwargs)
    elif app_name == "oscar":
        plain = harness.django_request(function, retryable)
    else:
        plain = ctfd_request(function, db, retryable)

    def call(kwargs: dict[str, Any]) -> Any:
        with context():
            return plain(kwargs)

    return call, finish


def client(config: dict[str, Any], inbox: Any, outbox: Any, barrier: Any) -> None:
    try:
        call, finish = boot(config)
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
        spread = config.get("spread_seconds", 0)
        outcomes = []
        for kwargs in message["calls"]:
            if spread:
                time.sleep(random.uniform(0, spread))
            started = time.perf_counter()
            attempts = 0
            while True:
                attempts += 1
                try:
                    result = call(kwargs)
                    ok = result.get("ok") if isinstance(result, dict) else None
                    outcome: dict[str, Any] = {"ok": ok if isinstance(ok, bool) else None}
                    if outcome["ok"] is False:
                        outcome["error"] = str(result.get("error"))[:80]
                except Exception as error:
                    outcome = {
                        "exception": type(error).__name__,
                        "sqlstate": app_sqlstate(error),
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


class Clients(harness.Clients):

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


def functional(db: Any, engine: str, item: dict[str, Any], config: dict[str, Any],
               timeout: float, isolation: str) -> dict:
    handler = apps.RULES[item["task"]]["handler"]
    spec = apps.FUNCTIONAL[handler]
    config = {**config, "isolation": isolation}
    db.set_isolation(isolation)
    reset(db, engine, item["app"], apps.seed(handler, "functional", engine))
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
    return {"passed": all(calls_ok) and all(state_ok), "calls_ok": calls_ok,
            "state_ok": state_ok, "outcomes": outcomes}


def concurrent(db: Any, engine: str, item: dict[str, Any], config: dict[str, Any],
               arm: dict[str, Any], trials: int, settings: dict[str, Any]) -> dict[str, Any]:
    rule = item["task"]
    workers = settings["workers"]
    plans = [apps.request_plan(rule, w) for w in range(workers)]
    statements = apps.seed(rule, "concurrent", engine)
    db.set_isolation(arm["isolation"])
    reset(db, engine, item["app"], statements)
    config = {**config, "retries": arm["retries"],
              "request_transaction": arm.get("request_transaction", False),
              "isolation": arm["isolation"], "spread_seconds": settings.get("spread_seconds", 0)}
    pool = Clients(workers, config, settings["trial_timeout_seconds"])
    summary: dict[str, Any] = {
        "trials": 0, "violating_trials": 0, "violations": Counter(), "exceptions": Counter(),
        "rejections": Counter(), "successes": [], "seconds": [], "retries": 0,
        "retries_exhausted": 0, "exception_messages": {}, "hung": False,
        "setup_error": pool.setup_error,
    }
    try:
        for _ in range(trials if not pool.setup_error else 0):
            reset(db, engine, item["app"], statements)
            started = time.perf_counter()
            replies = pool.run(plans, sync=True)
            elapsed = time.perf_counter() - started
            if any(r is None or "outcomes" not in r for r in replies):
                summary["hung"] = True
                break
            outcomes = [o for r in replies if r for o in r["outcomes"]]
            found = apps.invariant(rule, db.value)
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


def serve(args: argparse.Namespace) -> None:
    protocol = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    import c2_feedback

    plan = c1_run.configuration()
    loop = c2_feedback.configuration()["loop_self"]
    engine = plan["engines"][args.engine]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    root = Path(args.scratch or tempfile.gettempdir())
    sockets = Path(args.sockets or tempfile.gettempdir())
    settings = c1_run.settings(plan)
    with c1_run.server(args.engine, Path(args.bin), root, sockets, plan, work / "server.log") as db:
        migrate_server(args.app, args.engine, db, args.ctfd_source, work / "migrate.log")
        protocol.write(json.dumps({"ready": True, "engine": args.engine, "app": args.app}) + "\n")
        for count, line in enumerate(sys.stdin):
            request = json.loads(line)
            handler = request["handler"]
            if apps.HANDLERS[handler]["app"] != args.app:
                raise ValueError(f"{handler} does not run in {args.app}")
            path = work / "implementations" / f"{count}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(request["code"], "utf-8")
            rules = apps.HANDLERS[handler]["rules"]
            framework = apps.APPS[args.app]["framework"]
            items = [{"task": rule, "handler": handler, "app": args.app, "framework": framework,
                      "source": "generated", "path": str(path)} for rule in rules]
            config = base_config(items[0], args.engine, db, plan, args.ctfd_source)
            level = default_level(plan, args.engine, framework)
            if request["kind"] == "check":
                check: dict[str, Any] = {"functional": functional(
                    db, args.engine, items[0], config, plan["trial_timeout_seconds"], level)}
                if request.get("concurrent") and check["functional"]["passed"]:
                    arm = engine["arms"][level]
                    trials = plan["loop"]["concurrent_trials"]
                    check["concurrent"] = {
                        item["task"]: {**concurrent(db, args.engine, item, config, arm, trials,
                                                    settings), "workers": settings["workers"]}
                        for item in items}
                protocol.write(json.dumps(check, default=str) + "\n")
                continue
            test_path = work / "tests" / f"{count}.py"
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(request["test"], "utf-8")
            outcome_path = work / "outcomes" / f"{count}.json"
            outcome_path.parent.mkdir(parents=True, exist_ok=True)
            outcome_path.unlink(missing_ok=True)
            runner_config = {
                **config, "task": handler, "isolation": level,
                "runs": loop["self_test_runs"],
                "request_timeout_seconds": loop["request_timeout_seconds"],
                "test_path": str(test_path), "outcome_path": str(outcome_path),
                "protected_session": db.value(
                    "SELECT CONNECTION_ID()" if args.engine == "mysql"
                    else "SELECT pg_backend_pid()"),
            }
            config_path = work / "outcomes" / f"{count}.config.json"
            config_path.write_text(json.dumps(runner_config), "utf-8")
            db.set_isolation(level)
            with contextlib.suppress(subprocess.TimeoutExpired):
                subprocess.run(
                    [sys.executable, str(FOLDER / "c2_selftest.py"), "run", "--config",
                     str(config_path)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=loop["run_timeout_seconds"], check=False)
            if outcome_path.exists():
                outcome = json.loads(outcome_path.read_text("utf-8"))
            else:
                outcome = {"runs": loop["self_test_runs"], "failed": loop["self_test_runs"],
                           "first_failure": "The test did not finish within the time limit.",
                           "first_failure_kind": "timeout", "seconds": []}
            protocol.write(json.dumps(outcome, default=str) + "\n")
            reset(db, args.engine, args.app, [])


def inventory(output: Path, app: str, generations: list[Path]) -> list[dict[str, Any]]:
    items = []
    for (handler, variant), code in sorted(apps.REFERENCES.items()):
        if apps.HANDLERS[handler]["app"] != app:
            continue
        path = output / "implementations" / "reference" / f"{handler}__{variant}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, "utf-8")
        for rule in apps.HANDLERS[handler]["rules"]:
            items.append({"id": f"reference:{rule}:{variant}", "source": "reference",
                          "variant": variant, "task": rule, "handler": handler, "app": app,
                          "framework": apps.APPS[app]["framework"], "path": str(path)})
    for folder in generations:
        for record_path in sorted((folder / "generations").glob("*.json")):
            record = json.loads(record_path.read_text("utf-8"))
            handler = record["task"]
            if apps.HANDLERS[handler]["app"] != app:
                continue
            path = None
            if record["code"] is not None:
                file = output / "implementations" / record["model"] / f"{record_path.stem}.py"
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(record["code"], "utf-8")
                path = str(file)
            for rule in apps.HANDLERS[handler]["rules"]:
                item = {"id": f"generated:{record['model']}:{record_path.stem}:{rule}",
                        "source": "generated", "task": rule, "handler": handler, "app": app,
                        **{k: record[k] for k in ("model", "framework", "condition", "seed")},
                        "mode": record.get("mode", "single"), "path": path}
                for key in ("loop_passed", "repair_rounds"):
                    if key in record:
                        item[key] = record[key]
                items.append(item)
    return items


def base_config(item: dict[str, Any], engine: str, db: Any, plan: dict,
                ctfd_source: str | None) -> dict[str, Any]:
    return {
        "app": item["app"], "engine": engine, "framework": item["framework"],
        "function": apps.HANDLERS[item["handler"]]["function"], "path": item["path"],
        "host": str(db.sockets), "port": db.port, "ctfd_source": ctfd_source,
        "retry": {**plan["retry"], "sqlstates": plan["engines"][engine]["retry_codes"]},
        "retries": 0,
    }


def default_level(plan: dict, engine: str, framework: str) -> str:
    return plan["engines"][engine]["framework_defaults"][apps.DEFAULT_KEY[framework]]


def run(args: argparse.Namespace) -> None:
    plan = c1_run.configuration()
    engine = plan["engines"][args.engine]
    output = Path(args.output)
    generations = [Path(g) for g in args.generations or []]
    c1_run.claim(output, {
        "version": VERSION, "engine": args.engine, "app": args.app,
        "c1_plan_sha256": harness.file_hash(c1_run.PLAN),
        "apps_sha256": harness.file_hash(FOLDER / "c2_apps.py"),
        "appharness_sha256": harness.file_hash(Path(__file__)),
        "harness_sha256": harness.file_hash(FOLDER / "c0_harness.py"),
        "generations": sorted(str(g.resolve()) for g in generations),
    })
    items = inventory(output, args.app, generations)
    items = [i for i in items if not args.match or args.match in i["id"]]
    items = items[: args.limit] if args.limit else items
    root = Path(args.scratch or tempfile.gettempdir())
    sockets = Path(args.sockets or tempfile.gettempdir())
    started = time.time()
    log = output / f"{args.engine}.log"
    with c1_run.server(args.engine, Path(args.bin), root, sockets, plan, log) as db:
        migrate_server(args.app, args.engine, db, args.ctfd_source, output / "migrate.log")
        environment = {"engine": args.engine,
                       "server_version": c1_run.server_version(args.engine, db),
                       "python": sys.version.split()[0],
                       "app": apps.APPS[args.app]}
        for item in items:
            target = output / "results" / f"{item['id'].replace(':', '__')}.json"
            if target.exists():
                continue
            result: dict[str, Any] = {**item, "environment": environment}
            if item["path"] is None:
                result["functional"] = {"passed": False, "no_code": True}
            else:
                config = base_config(item, args.engine, db, plan, args.ctfd_source)
                level = default_level(plan, args.engine, item["framework"])
                result["functional"] = functional(db, args.engine, item, config,
                                                  plan["trial_timeout_seconds"], level)
                if result["functional"]["passed"]:
                    trials = args.trials or plan[f"{item['source']}_trials"]
                    result["concurrent"] = {
                        name: concurrent(db, args.engine, item, config, arm, trials,
                                         c1_run.settings(plan))
                        for name, arm in engine["arms"].items()
                    }
            harness.atomic_write(target, result)
            print(f"{item['id']} functional={result['functional']['passed']}", flush=True)
    harness.atomic_write(output / "completion.json", {
        "results": len(list((output / "results").glob("*.json"))), "items": len(items),
        "seconds": round(time.time() - started, 1)})


def gates(args: argparse.Namespace) -> None:
    plan = c1_run.configuration()
    output = Path(args.output)
    manifest = json.loads((output / "manifest.json").read_text("utf-8"))
    engine = manifest["engine"]
    rows = [r for r in c1_run.load_results(output) if r["source"] == "reference"]
    share = plan["restored_success_share"]
    trials = args.trials or plan["reference_trials"]

    def clean(r: dict[str, Any]) -> bool:
        return (r["functional"]["passed"]
                and all(c["violating_trials"] == 0 and not c["hung"]
                        for c in r["concurrent"].values())
                and c1_run.restored_at(r["concurrent"]["request replay"], trials,
                                       apps.RULES[r["task"]]["optimal_successes"], share))

    def detected(r: dict[str, Any]) -> bool:
        arm = next(n for n, a in plan["engines"][engine]["arms"].items()
                   if a["isolation"] == default_level(plan, engine, r["framework"])
                   and not a["retries"])
        return bool(r.get("concurrent") and r["concurrent"][arm]["violating_trials"])

    correct = [r for r in rows if r["variant"] == "correct"]
    buggy = [r for r in rows if r["variant"] == "buggy"]
    result = {
        "engine": engine, "app": manifest["app"], "references": len(rows),
        "correct_clean": bool(correct) and all(clean(r) for r in correct),
        "correct_failing": sorted(r["id"] for r in correct if not clean(r)),
        "buggy_violate_at_default": f"{sum(detected(r) for r in buggy)}/{len(buggy)}",
        "buggy_missed": sorted(r["id"] for r in buggy if not detected(r)),
    }
    print(json.dumps(result))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    r = commands.add_parser("run")
    r.add_argument("--app", required=True, choices=sorted(apps.APPS))
    r.add_argument("--engine", required=True, choices=["postgresql", "mysql"])
    r.add_argument("--output", required=True)
    r.add_argument("--bin", required=True, help="directory with the PostgreSQL and MySQL servers")
    r.add_argument("--generations", nargs="*")
    r.add_argument("--ctfd-source", help="CTFd checkout at the pinned release (CTFd only)")
    r.add_argument("--scratch")
    r.add_argument("--sockets")
    r.add_argument("--limit", type=int, default=0, help="tests only: first N items")
    r.add_argument("--match", default="", help="tests only: items whose id contains this")
    r.add_argument("--trials", type=int, default=0, help="tests only: trials per arm")
    v = commands.add_parser("serve")
    v.add_argument("--app", required=True, choices=sorted(apps.APPS))
    v.add_argument("--engine", required=True, choices=["postgresql", "mysql"])
    v.add_argument("--bin", required=True)
    v.add_argument("--work", required=True)
    v.add_argument("--ctfd-source")
    v.add_argument("--scratch")
    v.add_argument("--sockets")
    g = commands.add_parser("gates", help="reference gates of a run")
    g.add_argument("--output", required=True)
    g.add_argument("--trials", type=int, default=0, help="the run's --trials, if it set one")
    m = commands.add_parser("migrate")
    m.add_argument("--app", required=True, choices=sorted(apps.APPS))
    m.add_argument("--engine", required=True, choices=["postgresql", "mysql"])
    m.add_argument("--host", required=True)
    m.add_argument("--port", type=int, required=True)
    m.add_argument("--ctfd-source")
    args = parser.parse_args()
    if getattr(args, "app", None) == "ctfd" and not args.ctfd_source:
        parser.error("--ctfd-source is required for CTFd")
    {"run": run, "serve": serve, "migrate": migrate, "gates": gates}[args.command](args)


if __name__ == "__main__":
    main()
