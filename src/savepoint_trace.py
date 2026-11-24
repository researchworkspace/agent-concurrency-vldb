"""Plain-SQL lock trace around a savepoint rollback on MySQL and PostgreSQL.

Usage: python -I savepoint_trace.py --engine mysql|postgresql --bin DIR --output DIR [--scratch DIR]
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness
import tasks
import checks
import savepoint_diagnosis as diagnose

TASK = diagnose.TASK
CHECK = diagnose.CHECK
EXISTING = ((1, 3), (20, 22), (30, 31))
VARIANTS = ("no_savepoint", "savepoint_rollback", "savepoint_release",
            "check_then_savepoint_rollback", "savepoint_rollback_for_update")


class Traced(diagnose.Session):
    def __init__(self, db: Any, engine: str, name: str) -> None:
        super().__init__(db, engine, name)
        self.cursor.execute("SELECT CONNECTION_ID()" if engine == "mysql" else "SELECT pg_backend_pid()")
        self.connection_id = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT @@session.transaction_isolation, @@session.autocommit"
                            if engine == "mysql" else "SHOW default_transaction_isolation")
        self.session_settings = list(self.cursor.fetchone())


def sql(db: Any, engine: str, statement: str) -> Any:
    return db.execute(statement) if engine == "mysql" else db.admin.execute(statement)


def snapshot(db: Any, engine: str, sessions: list[Traced]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if engine == "mysql":
        locks = diagnose.mysql_locks(db, sessions)
        for s in sessions:
            rows = db.execute(
                "SELECT trx_id, trx_state, trx_started, trx_isolation_level, trx_lock_structs, "
                "trx_rows_locked, trx_tables_locked FROM information_schema.innodb_trx "
                f"WHERE trx_mysql_thread_id = {int(s.connection_id)}").fetchall()
            out[s.name] = {"connection_id": s.connection_id,
                           "transaction": [[str(v) for v in r] for r in rows],
                           "locks": [lock for lock in locks if lock["session"] == s.name]}
    else:
        for s in sessions:
            rows = sql(db, engine,
                "SELECT locktype, relation::regclass::text, page, tuple, mode, granted "
                f"FROM pg_locks WHERE pid = {int(s.connection_id)} AND locktype <> 'virtualxid' "
                "ORDER BY mode, locktype").fetchall()
            xact = sql(db, engine,
                "SELECT backend_xid::text, xact_start::text, state FROM pg_stat_activity "
                f"WHERE pid = {int(s.connection_id)}").fetchall()
            out[s.name] = {"connection_id": s.connection_id,
                           "transaction": [list(map(str, r)) for r in xact],
                           "locks": [list(map(str, r)) for r in rows]}
    return out


def trace(args: argparse.Namespace) -> None:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    engine = args.engine
    plan = diagnose.plan_with_statistics()
    seed = tasks.TASKS[TASK]["concurrent"]["seed"]
    results: dict[str, Any] = {"engine": engine, "purpose": __doc__.split("\n\n")[0]}
    begin = "START TRANSACTION" if engine == "mysql" else "BEGIN ISOLATION LEVEL SERIALIZABLE"
    with checks.server(engine, Path(args.bin), Path(args.scratch or tempfile.gettempdir()),
                       Path(args.sockets), plan, output / f"{engine}-trace.log") as db:
        results["server_version"] = checks.server_version(engine, db)
        if engine == "mysql":
            results["server_settings"] = [list(map(str, r)) for r in db.execute(
                "SELECT @@global.transaction_isolation, @@global.autocommit, @@innodb_lock_wait_timeout, "
                "@@innodb_rollback_on_timeout, @@innodb_deadlock_detect").fetchall()]
            results["bookings_indexes"] = [list(map(str, r)) for r in db.execute(
                "SELECT INDEX_NAME, COLUMN_NAME, SEQ_IN_INDEX FROM information_schema.STATISTICS "
                "WHERE TABLE_NAME = 'bookings' ORDER BY INDEX_NAME, SEQ_IN_INDEX").fetchall()]
        for table in ("empty", "populated"):
            for variant in VARIANTS:
                db.reset(TASK, seed)
                if table == "populated":
                    for start, end in EXISTING:
                        sql(db, engine, "INSERT INTO bookings (room_id, start_hour, end_hour) "
                                        f"VALUES (1, {start}, {end})")
                a, b = Traced(db, engine, "A"), Traced(db, engine, "B")
                steps: list[dict[str, Any]] = []

                def step(s: Traced, statement: str) -> None:
                    record = s.run(statement)
                    record["after"] = snapshot(db, engine, [a, b])
                    steps.append(record)

                def check(s: Traced, start: int, end: int) -> None:
                    query = CHECK.format(start=start, end=end)
                    step(s, begin)
                    if variant == "check_then_savepoint_rollback":
                        step(s, query)
                        step(s, "SAVEPOINT sp")
                        step(s, "SELECT 1")
                        step(s, "ROLLBACK TO SAVEPOINT sp")
                        return
                    if variant != "no_savepoint":
                        step(s, "SAVEPOINT sp")
                    step(s, query + (" FOR UPDATE" if variant.endswith("for_update") else ""))
                    if variant.startswith("savepoint_rollback"):
                        step(s, "ROLLBACK TO SAVEPOINT sp")
                    elif variant == "savepoint_release":
                        step(s, "RELEASE SAVEPOINT sp")

                check(a, 9, 11)
                check(b, 10, 12)
                for s, (start, end) in ((b, (10, 12)), (a, (9, 11))):
                    step(s, "INSERT INTO bookings (room_id, start_hour, end_hour) "
                            f"VALUES (1, {start}, {end})")
                step(a, "COMMIT")
                step(b, "COMMIT")
                sessions = {s.name: {"connection_id": s.connection_id, "settings": s.session_settings}
                            for s in (a, b)}
                a.close()
                b.close()
                overlaps = db.value(
                    "SELECT COUNT(*) FROM bookings x JOIN bookings y ON x.room_id = y.room_id "
                    "AND x.id < y.id AND x.start_hour < y.end_hour AND y.start_hour < x.end_hour")
                results[f"{table}/{variant}"] = {"sessions": sessions, "steps": steps,
                                                 "overlapping_pairs": overlaps}
                failed = [(st["session"], st["sql"][:24], st["error"]["code"] or st["error"]["sqlstate"])
                          for st in steps if st.get("ok") is False]
                print(f"{engine} {table}/{variant}: overlapping pairs {overlaps}; failed {failed}",
                      flush=True)
    harness.atomic_write(output / f"trace-{engine}.json", results)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine", choices=("mysql", "postgresql"), required=True)
    parser.add_argument("--bin", required=True, help="directory with the server binaries")
    parser.add_argument("--output", required=True)
    parser.add_argument("--scratch")
    parser.add_argument("--sockets", default="/tmp", help="short directory for server sockets")
    trace(parser.parse_args())


if __name__ == "__main__":
    main()
