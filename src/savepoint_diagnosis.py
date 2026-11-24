"""Savepoint pattern of two Devstral-Small-2 room_book programs that violate under replay on MySQL.

Checked hypothesis: InnoDB releases the read locks taken inside a savepoint when it is rolled back, so two SERIALIZABLE requests can both insert an overlapping booking.

Usage: python -I savepoint_diagnosis.py savepoint --engine mysql|postgresql --bin DIR --output DIR
       python -I savepoint_diagnosis.py program --bin DIR --benchmark BENCHMARK_RUN --output DIR [--trials N]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import tasks
import checks

TASK = "room_book"
PROGRAMS = ("room_book__django__hint__s104.py", "room_book__django__hint__s105.py")
CHECK = "SELECT id FROM bookings WHERE room_id = 1 AND start_hour < {end} AND end_hour > {start}"
VARIANTS = ("no_savepoint", "savepoint_rollback", "savepoint_release")


def plan_with_statistics() -> dict[str, Any]:
    plan = copy.deepcopy(checks.configuration())
    plan["engines"]["mysql"]["server_settings"].update({
        "performance_schema": "ON",
        "performance-schema-consumer-events-statements-history-long": "ON",
        "performance_schema_events_statements_history_long_size": "100000",
    })
    return plan


def error_of(error: BaseException) -> dict[str, Any]:
    code = getattr(error, "args", [None])[0] if error.args else None
    return {"type": type(error).__name__, "code": code if isinstance(code, int) else None,
            "sqlstate": getattr(error, "sqlstate", None), "message": str(error)[:300]}


class Session:

    def __init__(self, db: Any, engine: str, name: str) -> None:
        self.engine, self.name = engine, name
        self.connection = db.connect()
        self.cursor = self.connection.cursor()
        self.thread: int | None = None
        if engine == "mysql":
            self.cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            self.cursor.execute("SET SESSION innodb_lock_wait_timeout = 3")
            self.cursor.execute(
                "SELECT THREAD_ID FROM performance_schema.threads WHERE PROCESSLIST_ID = CONNECTION_ID()")
            self.thread = self.cursor.fetchone()[0]

    def run(self, sql: str) -> dict[str, Any]:
        started = time.perf_counter()
        step: dict[str, Any] = {"session": self.name, "sql": sql}
        try:
            self.cursor.execute(sql)
            try:
                step["rows"] = [list(r) for r in self.cursor.fetchall()]
            except Exception:
                step["rows"] = None
            step["ok"] = True
        except Exception as error:
            step["ok"] = False
            step["error"] = error_of(error)
        step["seconds"] = round(time.perf_counter() - started, 3)
        return step

    def close(self) -> None:
        try:
            self.connection.close()
        except Exception:
            pass


def mysql_locks(db: Any, sessions: list[Session]) -> list[dict[str, Any]]:
    names = {s.thread: s.name for s in sessions}
    rows = db.execute(
        "SELECT THREAD_ID, OBJECT_NAME, INDEX_NAME, LOCK_TYPE, LOCK_MODE, LOCK_STATUS, LOCK_DATA "
        "FROM performance_schema.data_locks ORDER BY THREAD_ID, OBJECT_NAME, INDEX_NAME").fetchall()
    return [{"session": names.get(r[0], r[0]), "table": r[1], "index": r[2], "type": r[3],
             "mode": r[4], "status": r[5], "data": r[6]} for r in rows]


def savepoint(args: argparse.Namespace) -> None:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    plan = plan_with_statistics()
    seed = tasks.TASKS[TASK]["concurrent"]["seed"]
    root = Path(args.scratch or tempfile.gettempdir())
    results: dict[str, Any] = {"engine": args.engine, "hypothesis": __doc__.split("\n\n")[1]}
    with checks.server(args.engine, Path(args.bin), root, Path(args.sockets), plan,
                       output / f"{args.engine}-savepoint.log") as db:
        results["server_version"] = checks.server_version(args.engine, db)
        for variant in VARIANTS:
            db.reset(TASK, seed)
            a, b = (Session(db, args.engine, n) for n in ("A", "B"))
            steps: list[dict[str, Any]] = []
            begin = ("START TRANSACTION" if args.engine == "mysql"
                     else "BEGIN ISOLATION LEVEL SERIALIZABLE")

            def check(s: Session, start: int, end: int) -> None:
                steps.append(s.run(begin))
                if variant != "no_savepoint":
                    steps.append(s.run("SAVEPOINT sp"))
                steps.append(s.run(CHECK.format(start=start, end=end)))
                if variant == "savepoint_rollback":
                    steps.append(s.run("ROLLBACK TO SAVEPOINT sp"))
                elif variant == "savepoint_release":
                    steps.append(s.run("RELEASE SAVEPOINT sp"))
                if args.engine == "mysql":
                    steps.append({"locks_after": f"{s.name} check",
                                  "locks": mysql_locks(db, [a, b])})

            check(a, 9, 11)
            check(b, 10, 12)
            for s, (start, end) in ((b, (10, 12)), (a, (9, 11))):
                steps.append(s.run(
                    f"INSERT INTO bookings (room_id, start_hour, end_hour) VALUES (1, {start}, {end})"))
                if args.engine == "mysql":
                    steps.append({"locks_after": f"{s.name} insert", "locks": mysql_locks(db, [a, b])})
            steps.append(a.run("COMMIT"))
            steps.append(b.run("COMMIT"))
            a.close()
            b.close()
            overlaps = db.value(
                "SELECT COUNT(*) FROM bookings x JOIN bookings y ON x.room_id = y.room_id "
                "AND x.id < y.id AND x.start_hour < y.end_hour AND y.start_hour < x.end_hour")
            results[variant] = {"steps": steps, "bookings": db.value("SELECT COUNT(*) FROM bookings"),
                                "overlapping_pairs": overlaps}
            print(f"{args.engine} {variant}: overlapping pairs {overlaps}; failed steps "
                  f"{[(st['session'], st['sql'][:30], st['error']['code'] or st['error']['sqlstate']) for st in steps if st.get('ok') is False]}",
                  flush=True)
    harness.atomic_write(output / f"savepoint-{args.engine}.json", results)


def program(args: argparse.Namespace) -> None:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    plan = plan_with_statistics()
    engine = "mysql"
    arm = plan["engines"][engine]["arms"]["request replay"]
    root = Path(args.scratch or tempfile.gettempdir())
    reference = output / "implementations" / "reference__room_book__django__correct.py"
    reference.parent.mkdir(parents=True, exist_ok=True)
    reference.write_text(checks.references(engine)[(TASK, "django", "correct")], "utf-8")
    items = [{"id": "reference:room_book:django:correct", "path": str(reference)}] + [
        {"id": f"devstral2_24b:{name[:-3]}",
         "path": str(Path(args.benchmark) / "harness-mysql" / "implementations" / "devstral2_24b" / name)}
        for name in PROGRAMS]
    with checks.server(engine, Path(args.bin), root, Path(args.sockets), plan, output / "mysql-program.log") as db:
        admin_thread = db.value(
            "SELECT THREAD_ID FROM performance_schema.threads WHERE PROCESSLIST_ID = CONNECTION_ID()")
        db.execute("UPDATE performance_schema.setup_consumers SET ENABLED = 'YES' "
                   "WHERE NAME IN ('events_statements_history', 'events_statements_history_long')")
        report: dict[str, Any] = {"server_version": checks.server_version(engine, db),
                                  "arm": arm, "items": {}}
        for raw in items:
            item = {**raw, "task": TASK, "framework": "django", "source": "diagnostic"}
            config = checks.base_config(item, engine, db, output, plan)
            trials = []
            for _ in range(args.trials):
                db.execute("TRUNCATE TABLE performance_schema.events_statements_history_long")
                summary = harness.concurrent(db, item, config, arm, 1, checks.settings(plan))
                rows = db.execute(
                    "SELECT h.THREAD_ID, h.EVENT_ID, h.SQL_TEXT, h.MYSQL_ERRNO, h.RETURNED_SQLSTATE, "
                    "h.ROWS_AFFECTED, h.ROWS_SENT FROM performance_schema.events_statements_history_long h "
                    f"WHERE h.THREAD_ID <> {int(admin_thread)} AND h.SQL_TEXT IS NOT NULL "
                    "ORDER BY h.TIMER_START").fetchall()
                statements = [{"thread": r[0], "event": r[1], "sql": (r[2] or "")[:400],
                               "errno": r[3], "sqlstate": r[4], "affected": r[5], "sent": r[6]}
                              for r in rows]
                trials.append({"summary": summary, "statements": statements,
                               "errors": [s for s in statements if s["errno"]]})
            report["items"][raw["id"]] = trials
            print(f"{raw['id']}: violating trials "
                  f"{sum(t['summary']['violating_trials'] for t in trials)} of {len(trials)}; "
                  f"statement errors {sorted({(s['errno'], s['sqlstate']) for t in trials for s in t['errors']})}",
                  flush=True)
    harness.atomic_write(output / "program-mysql.json", report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    s = sub.add_parser("savepoint")
    s.add_argument("--engine", choices=("mysql", "postgresql"), required=True)
    p = sub.add_parser("program")
    p.add_argument("--benchmark", required=True, help="the benchmark run folder")
    p.add_argument("--trials", type=int, default=5)
    for q in (s, p):
        q.add_argument("--bin", required=True, help="directory with the server binaries")
        q.add_argument("--output", required=True)
        q.add_argument("--scratch")
        q.add_argument("--sockets", default="/tmp", help="short directory for server sockets")
    args = parser.parse_args()
    {"savepoint": savepoint, "program": program}[args.mode](args)


if __name__ == "__main__":
    main()
