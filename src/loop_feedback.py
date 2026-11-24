"""Checker for the repair loops: sequential tests, the concurrency test, and their feedback text."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
import harness
import tasks

INVARIANTS = {
    "stock_purchase": (
        "a product's stock never goes negative, and its stock plus the quantities in its "
        "orders always equals the starting stock"
    ),
    "coupon_redeem": (
        "a coupon is redeemed at most `max_uses` times in total, and no user redeems the "
        "same coupon twice"
    ),
    "oncall_leave": "at least one doctor stays on call",
    "username_signup": "no two members share a username",
    "joint_withdraw": "the total balance of a customer's accounts never goes below zero",
    "seat_reserve": "the seats reserved for an event never exceed its capacity",
    "room_book": "no two bookings of the same room overlap in time",
    "wallet_transfer": "no wallet balance goes negative, and the total balance is unchanged",
}

CLOSING = (
    "Fix the module so that these tests pass. Respond with the complete module in a single "
    "```python code block."
)


def call_text(task: str, kwargs: dict[str, Any]) -> str:
    arguments = ", ".join(f"{k}={v!r}" for k, v in kwargs.items())
    return f"{tasks.TASKS[task]['function']}({arguments})"


def outcome_text(outcome: dict[str, Any]) -> str:
    if "exception" in outcome:
        return f"raised {outcome['exception']}: {outcome.get('message', '')}".strip()
    if outcome.get("ok") is True:
        return "returned ok=True"
    if outcome.get("ok") is False:
        return f"returned ok=False (error {outcome.get('error')!r})"
    return "did not return a dict with a boolean `ok`"


def feedback_text(task: str, check: dict[str, Any] | None) -> str | None:
    function = tasks.TASKS[task]["function"]
    if check is None:
        return (
            f"Your answer did not contain a ```python code block that defines `{function}`. "
            + CLOSING
        )
    f = check["functional"]
    if not f["passed"]:
        if f.get("setup_error"):
            return f"Importing your module failed: {f['setup_error']}\n\n{CLOSING}"
        if f.get("hung"):
            return f"The test suite timed out while calling `{function}`.\n\n{CLOSING}"
        spec = tasks.TASKS[task]["functional"]
        lines = ["The application's tests called the function in this order on a fresh database:"]
        for k, (outcome, (kwargs, expected)) in enumerate(zip(f["outcomes"], spec["calls"]), 1):
            verdict = "ok" if f["calls_ok"][k - 1] else "FAILED"
            lines.append(
                f"{k}. `{call_text(task, kwargs)}`: expected ok={expected}, "
                f"{outcome_text(outcome)} [{verdict}]"
            )
        for (sql, expected), good in zip(spec["state"], f["state_ok"]):
            if not good:
                lines.append(f"After the calls, `{sql}` should return {expected} but did not.")
        return "\n".join(lines) + "\n\n" + CLOSING
    if passed(check):
        return None
    c = check["concurrent"]
    lines = [
        "The sequential tests pass. A concurrency test then had "
        f"{c['workers']} clients call `{function}` at the same time, each making its usual "
        f"requests, on a fresh database each run."
    ]
    if c["violating_trials"]:
        lines.append(
            f"In {c['violating_trials']} of {c['trials']} runs the database ended in a state "
            f"that breaks the rule: {INVARIANTS[task]}."
        )
    if c["exceptions"]:
        count = sum(c["exceptions"].values())
        key, message = next(iter(c["exception_messages"].items()))
        lines.append(
            f"{count} requests raised an exception to the caller, for example "
            f"{key.split(':')[0]}: {message}"
        )
    if c["hung"]:
        lines.append("A run did not finish within the time limit.")
    return "\n".join(lines) + "\n\n" + CLOSING


def passed(check: dict[str, Any] | None) -> bool:
    if check is None or not check["functional"]["passed"]:
        return False
    c = check.get("concurrent")
    return not c or not (c["violating_trials"] or c["exceptions"] or c["hung"])


def serve(args: argparse.Namespace) -> None:
    protocol = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    import checks

    plan = checks.configuration()
    loop = plan["loop"]
    engine = plan["engines"]["postgresql"]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    root = Path(args.scratch or tempfile.gettempdir())
    sockets = Path(args.sockets or tempfile.gettempdir())
    settings = {
        "workers": plan["workers"],
        "trial_timeout_seconds": plan["trial_timeout_seconds"],
        "spread_seconds": 0,
    }
    with harness.Postgres(
        Path(args.pg_bin), root, sockets, engine["server_settings"], work / "postgres.log"
    ) as db:
        protocol.write(json.dumps({"ready": True}) + "\n")
        for count, line in enumerate(sys.stdin):
            request = json.loads(line)
            task, framework = request["task"], request["framework"]
            path = work / "implementations" / f"{count}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(request["code"], "utf-8")
            item = {"task": task, "framework": framework, "source": "generated"}
            config = {
                "engine": "postgresql",
                "framework": framework,
                "function": tasks.TASKS[task]["function"],
                "path": str(path),
                "host": str(db.sockets),
                "port": db.port,
                "support": str(harness.support_directory(work, task, framework)),
                "retry": {**plan["retry"], "sqlstates": engine["retry_codes"]},
                "retries": 0,
            }
            level = engine["framework_defaults"][framework]
            check: dict[str, Any] = {
                "functional": harness.functional(
                    db, item, config, plan["trial_timeout_seconds"], level
                )
            }
            if request.get("concurrent") and check["functional"]["passed"]:
                arm = engine["arms"][level]
                c = harness.concurrent(db, item, config, arm, loop["concurrent_trials"], settings)
                check["concurrent"] = {**c, "workers": settings["workers"]}
            protocol.write(json.dumps(check, default=str) + "\n")


class Checker:

    def __init__(self, python: str, pg_bin: str, work: Path, scratch: str | None) -> None:
        import subprocess

        command = [python, "-I", str(Path(__file__)), "serve", "--pg-bin", pg_bin]
        command += ["--work", str(work)] + (["--scratch", scratch] if scratch else [])
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
        )
        ready = self.process.stdout.readline() if self.process.stdout else ""
        if not ready or not json.loads(ready).get("ready"):
            raise RuntimeError("the checker did not start; see the job's stderr")

    def check(self, task: str, framework: str, code: str, concurrent: bool) -> dict[str, Any]:
        assert self.process.stdin and self.process.stdout
        request = {"task": task, "framework": framework, "code": code, "concurrent": concurrent}
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        reply = self.process.stdout.readline()
        if not reply:
            raise RuntimeError("the checker stopped; see the job's stderr")
        return json.loads(reply)

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=120)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("serve")
    s.add_argument("--pg-bin", required=True)
    s.add_argument("--work", required=True)
    s.add_argument("--scratch")
    s.add_argument("--sockets")
    args = parser.parse_args()
    serve(args)


if __name__ == "__main__":
    main()
