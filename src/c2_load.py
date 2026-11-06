"""Sustained load: completed-request throughput of the configurations, by shard.

Usage: python -I c2_load.py run --engine E --run C1_RUN --output DIR --bin DIR --shards N --shard I
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
import c0_harness as harness
import c0_tasks as tasks
import c1_run
import c2_feedback

VERSION = c2_feedback.VERSION


class Keys:

    def __init__(self, n: int, theta: float) -> None:
        self.n = n
        self.theta = theta
        if theta:
            weights = [1.0 / (k + 1) ** theta for k in range(n)]
            total = sum(weights)
            running, self.cdf = 0.0, []
            for w in weights:
                running += w / total
                self.cdf.append(running)

    def draw(self, rng: random.Random) -> int:
        if not self.theta:
            return rng.randrange(self.n)
        return min(bisect.bisect_left(self.cdf, rng.random()), self.n - 1)


def rows(statement: str, values: list[tuple]) -> tuple[str, list[tuple]]:
    return statement, values


OVERLAP = ("SELECT COUNT(*) FROM bookings a JOIN bookings b ON a.room_id = b.room_id "
           "AND a.id < b.id AND a.start_hour < b.end_hour AND b.start_hour < a.end_hour")


def overlap_check(span: int) -> str:
    if span <= 0:
        return "SELECT 0"
    if span > 8:
        return OVERLAP
    offsets = " UNION ALL ".join(f"SELECT {d} AS d" for d in range(1 - span, span))
    return (f"SELECT COUNT(*) FROM bookings a CROSS JOIN ({offsets}) AS o JOIN bookings b "
            "ON b.room_id = a.room_id AND b.start_hour = a.start_hour + o.d AND a.id < b.id "
            "AND a.start_hour < b.end_hour AND b.start_hour < a.end_hour")


def workload_specs(p: dict[str, Any]) -> dict[str, dict[str, Any]]:
    w = p["workloads"]
    s, c, u, j = w["stock_purchase"], w["coupon_redeem"], w["username_signup"], w["joint_withdraw"]
    e, r, t = w["seat_reserve"], w["room_book"], w["wallet_transfer"]
    return {
        "stock_purchase": {
            "keys": s["products"],
            "seed": lambda: [rows("INSERT INTO products (id, name, stock) VALUES (%s, %s, %s)",
                                  [(k + 1, f"p{k}", s["stock"]) for k in range(s["products"])])],
            "request": lambda rng, k: {"product_id": k + 1, "quantity": 1},
            "checks": {
                "negative_stock": "SELECT COUNT(*) FROM products WHERE stock < 0",
                "stock_not_conserved": (
                    "SELECT COUNT(*) FROM products p WHERE p.stock + COALESCE((SELECT SUM(o.quantity) "
                    f"FROM orders o WHERE o.product_id = p.id), 0) <> {s['stock']}"
                ),
            },
        },
        "coupon_redeem": {
            "keys": c["coupons"],
            "seed": lambda: [rows("INSERT INTO coupons (code, max_uses) VALUES (%s, %s)",
                                  [(f"C{k}", c["max_uses"]) for k in range(c["coupons"])])],
            "request": lambda rng, k: {"code": f"C{k}", "user_id": rng.randrange(c["users"]) + 1},
            "checks": {
                "over_redeemed": (
                    "SELECT COUNT(*) FROM coupons c WHERE (SELECT COUNT(*) FROM redemptions r "
                    "WHERE r.code = c.code) > c.max_uses"
                ),
                "duplicate_redemption": (
                    "SELECT COUNT(*) FROM (SELECT code, user_id FROM redemptions "
                    "GROUP BY code, user_id HAVING COUNT(*) > 1) AS d"
                ),
            },
        },
        "username_signup": {
            "keys": u["usernames"],
            "seed": lambda: [],
            "request": lambda rng, k: {"username": f"user{k}",
                                       "email": f"user{k}.{rng.randrange(10**9)}@example.org"},
            "checks": {
                "duplicate_username": (
                    "SELECT COUNT(*) FROM (SELECT username FROM members GROUP BY username "
                    "HAVING COUNT(*) > 1) AS d"
                ),
            },
        },
        "joint_withdraw": {
            "keys": j["customers"] * 2,
            "seed": lambda: [rows(
                "INSERT INTO bank_accounts (id, customer_id, balance) VALUES (%s, %s, %s)",
                [(k + 1, k // 2 + 1, j["balance"]) for k in range(j["customers"] * 2)])],
            "request": lambda rng, k: {"account_id": k + 1, "amount": j["amount"]},
            "checks": {
                "overdrawn": (
                    "SELECT COUNT(*) FROM (SELECT customer_id, SUM(balance) AS total FROM "
                    "bank_accounts GROUP BY customer_id) AS t WHERE t.total < 0"
                ),
                "balance_not_conserved": (
                    "SELECT COUNT(*) FROM (SELECT a.customer_id, SUM(a.balance + COALESCE(w.total, "
                    "0)) AS total FROM bank_accounts a LEFT JOIN (SELECT account_id, SUM(amount) "
                    "AS total FROM withdrawals GROUP BY account_id) AS w ON w.account_id = a.id "
                    f"GROUP BY a.customer_id) AS t WHERE t.total <> {2 * j['balance']}"
                ),
            },
        },
        "seat_reserve": {
            "keys": e["events"],
            "seed": lambda: [rows("INSERT INTO events (id, name, capacity) VALUES (%s, %s, %s)",
                                  [(k + 1, f"e{k}", e["capacity"]) for k in range(e["events"])])],
            "request": lambda rng, k: {"event_id": k + 1, "seats": rng.randint(1, e["max_seats"])},
            "checks": {
                "over_capacity": (
                    "SELECT COUNT(*) FROM events v WHERE (SELECT COALESCE(SUM(r.seats), 0) FROM "
                    "reservations r WHERE r.event_id = v.id) > v.capacity"
                ),
            },
        },
        "room_book": {
            "keys": r["rooms"],
            "seed": lambda: [rows("INSERT INTO rooms (id, name) VALUES (%s, %s)",
                                  [(k + 1, f"r{k}") for k in range(r["rooms"])])],
            "request": lambda rng, k: (lambda start: {
                "room_id": k + 1, "start_hour": start,
                "end_hour": start + rng.randint(1, r["max_length"])})(rng.randrange(r["hours"])),
            "check_setup": ["CREATE INDEX bookings_check ON bookings (room_id, start_hour)"],
            "checks": {"overlapping_bookings": overlap_check},
            "span": "SELECT COALESCE(MAX(end_hour - start_hour), 0) FROM bookings",
        },
        "wallet_transfer": {
            "keys": t["wallets"],
            "seed": lambda: [rows("INSERT INTO wallets (id, owner, balance) VALUES (%s, %s, %s)",
                                  [(k + 1, f"w{k}", t["balance"]) for k in range(t["wallets"])])],
            "request": lambda rng, k: {
                "from_wallet_id": k + 1,
                "to_wallet_id": (k + rng.randrange(1, t["wallets"])) % t["wallets"] + 1,
                "amount": t["amount"],
            },
            "checks": {
                "negative_balance": "SELECT COUNT(*) FROM wallets WHERE balance < 0",
                "money_not_conserved": (
                    "SELECT CASE WHEN COALESCE(SUM(balance), 0) <> "
                    f"{t['wallets'] * t['balance']} THEN 1 ELSE 0 END FROM wallets"
                ),
            },
        },
    }


UNLIMITED = {"postgresql": "SET statement_timeout = 0", "mysql": "SET SESSION max_execution_time = 0"}


def seed(db: Any, engine: str, task: str, spec: dict[str, Any], indexes: list[str]) -> None:
    db.reset(task, [])
    cursor = db.admin.cursor()
    for statement in indexes:
        cursor.execute(statement)
    for statement, values in spec["seed"]():
        for start in range(0, len(values), 1000):
            cursor.executemany(statement, values[start:start + 1000])


def refused(db: Any) -> int:
    row = db.admin.cursor()
    row.execute("SHOW GLOBAL STATUS LIKE 'Connection_errors_max_connections'")
    return int(row.fetchone()[1])


def measure(db: Any, engine: str, item: dict[str, Any], config: dict[str, Any], arm: dict,
            condition: dict[str, Any], repetition: int, load: dict[str, Any]) -> dict[str, Any]:
    task = item["task"]
    spec = workload_specs(load)[task]
    keys = Keys(spec["keys"], condition["theta"])
    clients = condition["clients"]
    db.set_isolation(arm["isolation"])
    indexes = load["indexes"][engine][task] if condition.get("indexes", True) else []
    seed(db, engine, task, spec, indexes)
    config = {**config, "retries": arm["retries"], "isolation": arm["isolation"],
              "request_transaction": arm.get("request_transaction", False), "spread_seconds": 0}
    refused_before = refused(db) if engine == "mysql" else 0
    pool = harness.Clients(clients, config, load["setup_timeout_seconds"])
    result: dict[str, Any] = {"setup_error": pool.setup_error}
    if pool.setup_error:
        pool.close()
        return result
    rngs = [random.Random(f"{load['seed']}:{item['id']}:{condition['name']}:{repetition}:{w}")
            for w in range(clients)]
    batch = load["batch"]
    warmup, window = load["warmup_seconds"], load["measure_seconds"]
    started = time.perf_counter()
    stop = started + warmup + window
    pending: dict[int, float] = {}

    def send(w: int) -> None:
        calls = [spec["request"](rngs[w], keys.draw(rngs[w])) for _ in range(batch)]
        pool.inboxes[w].put({"calls": calls, "sync": False})
        pending[w] = time.perf_counter()

    for w in range(clients):
        send(w)
    tally: dict[str, Any] = defaultdict(int)
    kinds: dict[str, int] = {}
    messages: dict[str, str] = {}
    latencies: list[float] = []
    hung = False
    while pending:
        progressed = False
        for w in list(pending):
            try:
                reply = pool.outboxes[w].get_nowait()
            except Exception:
                continue
            progressed = True
            sent, ended = pending.pop(w), time.perf_counter()
            if sent - started >= warmup and ended <= stop:
                for o in reply["outcomes"]:
                    tally["requests"] += 1
                    tally["attempts"] += o["attempts"]
                    if "exception" in o:
                        tally["exceptions"] += 1
                        kind = f"{o['exception']}:{o['sqlstate']}"
                        kinds[kind] = kinds.get(kind, 0) + 1
                        messages.setdefault(kind, o["message"])
                    elif o.get("ok") is True:
                        tally["successes"] += 1
                    else:
                        tally["rejections"] += 1
                    latencies.append(o["seconds"])
            if ended < stop:
                send(w)
        if not progressed:
            if time.perf_counter() > stop + load["drain_timeout_seconds"]:
                hung = True
                break
            time.sleep(0.001)
    pool.close()
    latencies.sort()

    def quantile(q: float) -> float | None:
        return latencies[min(len(latencies) - 1, int(q * len(latencies)))] if latencies else None

    db.admin.cursor().execute(UNLIMITED[engine])
    checked = time.perf_counter()
    for statement in spec.get("check_setup", []):
        db.admin.cursor().execute(statement)
    span = int(db.value(spec["span"])) if "span" in spec else None
    violations = {name: int(db.value(sql(span) if callable(sql) else sql) or 0)
                  for name, sql in spec["checks"].items()}
    result["check_seconds"] = round(time.perf_counter() - checked, 3)
    if engine == "mysql":
        result["refused_connections"] = refused(db) - refused_before
    result.update({
        "requests": tally["requests"],
        "successes": tally["successes"],
        "rejections": tally["rejections"],
        "exceptions": tally["exceptions"],
        "retries_or_replays": tally["attempts"] - tally["requests"],
        "throughput": round(tally["requests"] / window, 2),
        "success_throughput": round(tally["successes"] / window, 2),
        "p50_seconds": quantile(0.50),
        "p99_seconds": quantile(0.99),
        "violations": {k: v for k, v in violations.items() if v},
        "exception_kinds": kinds,
        "exception_messages": messages,
        "hung": hung,
    })
    return result


def population(c1: dict, engine: str, main: Path, load: dict[str, Any]) -> list[dict[str, Any]]:
    rows_ = c1_run.sweep_population(c1, engine, c1_run.load_results(main))
    rows_ = [r for r in rows_ if r["task"] in load["tasks"]]
    for r in rows_:
        parts = Path(r["path"]).parts
        r["path"] = str(main.joinpath(*parts[parts.index("implementations"):]))
    chosen = [r for r in rows_ if r["population"] == "reference"]
    cells: dict[tuple, list] = defaultdict(list)
    for r in rows_:
        if r["population"] != "reference":
            cells[(r["task"], r["framework"], r["population"])].append(r)
    for key in sorted(cells):
        group = sorted(cells[key], key=lambda r: r["id"])
        random.Random(f"{load['seed']}:{':'.join(key)}").shuffle(group)
        chosen += group[: load["per_cell"]]
    return chosen


def arms_for(c1: dict, engine: str, row: dict[str, Any]) -> list[str]:
    default = c1_run.default_arm(c1, engine, row["framework"])
    return {"reference": [default, "request replay"], "loop_repaired": [default],
            "replay_restored": ["request replay"]}[row["population"]]


def unit_name(r: dict[str, Any], arm: str, cond: dict[str, Any], rep: int) -> str:
    return f"{r['id'].replace(':', '__')}__{arm.replace(' ', '-')}__{cond['name']}__r{rep}"


def run(args: argparse.Namespace) -> None:
    c1 = c1_run.configuration()
    load = c2_feedback.configuration()["load"]
    if args.seconds:
        load = {**load, "warmup_seconds": 1, "measure_seconds": args.seconds}
    main = Path(args.run)
    main_manifest = json.loads((main / "manifest.json").read_text("utf-8"))
    if main_manifest["engine"] != args.engine:
        raise ValueError("the main run belongs to another engine")
    output = Path(args.output)
    c1_run.claim(output, {
        "version": VERSION, "engine": args.engine, "run": str(main.resolve()),
        "run_manifest": main_manifest, "shard": args.shard, "shards": args.shards,
        "plan_sha256": harness.file_hash(c2_feedback.PLAN),
        "load_sha256": harness.file_hash(Path(__file__)),
        "harness_sha256": harness.file_hash(FOLDER / "c0_harness.py"),
    })
    chosen = population(c1, args.engine, main, load)
    groups = [(r, arm, cond) for r in chosen for arm in arms_for(c1, args.engine, r)
              for cond in load["conditions"]
              if r["population"] in cond.get("populations", [r["population"]])]
    random.Random(f"{load['seed']}:shards").shuffle(groups)
    units = [(r, arm, cond, rep) for rep in range(load["repetitions"])
             for r, arm, cond in groups[args.shard::args.shards]]
    if args.match:
        units = [u for u in units if args.match in unit_name(*u)]
    units = units[: args.limit] if args.limit else units
    root = Path(args.scratch or tempfile.gettempdir())
    sockets = Path(args.sockets or tempfile.gettempdir())
    started = time.time()
    overrides = load["server_settings"][args.engine]
    engines = {**c1["engines"], args.engine: {
        **c1["engines"][args.engine],
        "server_settings": {**c1["engines"][args.engine]["server_settings"], **overrides}}}
    served = {**c1, "engines": engines}
    with c1_run.server(args.engine, Path(args.bin), root, sockets, served, output / "server.log") as db:
        for r, arm, cond, rep in units:
            name = unit_name(r, arm, cond, rep)
            target = output / "results" / f"{name}.json"
            if target.exists():
                continue
            config = c1_run.base_config(r, args.engine, db, output, c1)
            measured = measure(db, args.engine, r, config, c1["engines"][args.engine]["arms"][arm],
                               cond, rep, load)
            harness.atomic_write(target, {
                **{k: r[k] for k in ("id", "source", "population", "task", "framework")},
                **{k: r[k] for k in ("model", "mode", "seed") if k in r},
                "arm": arm, "condition": cond, "repetition": rep, **measured,
            })
            print(f"{name} throughput={measured.get('throughput')}", flush=True)
    harness.atomic_write(output / "completion.json", {
        "results": len(list((output / "results").glob("*.json"))), "units": len(units),
        "seconds": round(time.time() - started, 1)})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    r = commands.add_parser("run")
    r.add_argument("--engine", required=True, choices=["postgresql", "mysql"])
    r.add_argument("--run", required=True, help="C1's main harness output for this engine")
    r.add_argument("--output", required=True)
    r.add_argument("--bin", required=True)
    r.add_argument("--shard", type=int, default=0)
    r.add_argument("--shards", type=int, default=1)
    r.add_argument("--scratch")
    r.add_argument("--sockets")
    r.add_argument("--limit", type=int, default=0, help="tests only: first N units")
    r.add_argument("--match", default="", help="tests only: units whose name contains this")
    r.add_argument("--seconds", type=float, default=0, help="tests only: measured window")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
