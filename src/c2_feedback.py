"""Checker for the C2 loops on either engine, including the self-test runs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
PLAN = FOLDER / "c2-plan.json"
VERSION = "agent-concurrency-c2-v1"


def configuration() -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(PLAN.read_text("utf-8"))
    if plan["version"] != VERSION:
        raise ValueError("C2 plan differs from the implemented code")
    return plan


def serve(args: argparse.Namespace) -> None:
    protocol = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    import c0_harness as harness
    import c1_run

    c1 = c1_run.configuration()
    c2 = configuration()
    engine_name = args.engine
    engine = c1["engines"][engine_name]
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    root = Path(args.scratch or tempfile.gettempdir())
    sockets = Path(args.sockets or tempfile.gettempdir())
    settings = c1_run.settings(c1)
    with c1_run.server(engine_name, Path(args.bin), root, sockets, c1, work / "server.log") as db:
        protocol.write(json.dumps({"ready": True, "engine": engine_name}) + "\n")
        for count, line in enumerate(sys.stdin):
            request = json.loads(line)
            task, framework = request["task"], request["framework"]
            path = work / "implementations" / f"{count}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(request["code"], "utf-8")
            item = {"task": task, "framework": framework, "source": "generated", "path": str(path)}
            config = c1_run.base_config(item, engine_name, db, work, c1)
            level = engine["framework_defaults"][framework]
            if request["kind"] == "check":
                check: dict[str, Any] = {
                    "functional": harness.functional(
                        db, item, config, c1["trial_timeout_seconds"], level
                    )
                }
                if request.get("concurrent") and check["functional"]["passed"]:
                    arm = engine["arms"][level]
                    trials = c1["loop"]["concurrent_trials"]
                    c = harness.concurrent(db, item, config, arm, trials, settings)
                    check["concurrent"] = {**c, "workers": settings["workers"]}
                protocol.write(json.dumps(check, default=str) + "\n")
                continue
            loop = c2["loop_self"]
            test_path = work / "tests" / f"{count}.py"
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(request["test"], "utf-8")
            outcome_path = work / "outcomes" / f"{count}.json"
            outcome_path.parent.mkdir(parents=True, exist_ok=True)
            outcome_path.unlink(missing_ok=True)
            runner_config = {
                **config,
                "task": task,
                "isolation": level,
                "runs": loop["self_test_runs"],
                "request_timeout_seconds": loop["request_timeout_seconds"],
                "test_path": str(test_path),
                "outcome_path": str(outcome_path),
                "protected_session": db.value(
                    "SELECT CONNECTION_ID()" if engine_name == "mysql" else "SELECT pg_backend_pid()"
                ),
            }
            config_path = work / "outcomes" / f"{count}.config.json"
            config_path.write_text(json.dumps(runner_config), "utf-8")
            db.set_isolation(level)
            try:
                subprocess.run(
                    [sys.executable, str(FOLDER / "c2_selftest.py"), "run", "--config",
                     str(config_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=loop["run_timeout_seconds"],
                    check=False,
                )
            except subprocess.TimeoutExpired:
                pass
            if outcome_path.exists():
                outcome = json.loads(outcome_path.read_text("utf-8"))
            else:
                outcome = {"runs": loop["self_test_runs"], "failed": loop["self_test_runs"],
                           "first_failure": "The test did not finish within the time limit.",
                           "first_failure_kind": "timeout", "seconds": []}
            protocol.write(json.dumps(outcome, default=str) + "\n")
            db.reset(task, [])


class Checker:

    def __init__(self, python: str, binaries: str, engine: str, work: Path,
                 scratch: str | None, app: str | None = None,
                 ctfd_source: str | None = None) -> None:
        script = FOLDER / "c2_appharness.py" if app else Path(__file__)
        command = [python, "-I", str(script), "serve", "--bin", binaries,
                   "--engine", engine, "--work", str(work)]
        command += ["--app", app] if app else []
        command += ["--ctfd-source", ctfd_source] if ctfd_source else []
        command += ["--scratch", scratch] if scratch else []
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1
        )
        ready = self.process.stdout.readline() if self.process.stdout else ""
        if not ready or not json.loads(ready).get("ready"):
            raise RuntimeError("the checker did not start; see the job's stderr")

    def _ask(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        reply = self.process.stdout.readline()
        if not reply:
            raise RuntimeError("the checker stopped; see the job's stderr")
        return json.loads(reply)

    def check(self, task: str, framework: str, code: str, concurrent: bool) -> dict[str, Any]:
        return self._ask({"kind": "check", "task": task, "handler": task, "framework": framework,
                          "code": code, "concurrent": concurrent})

    def selftest(self, task: str, framework: str, code: str, test: str) -> dict[str, Any]:
        return self._ask({"kind": "selftest", "task": task, "handler": task,
                          "framework": framework, "code": code, "test": test})

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        self.process.wait(timeout=300)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    s = commands.add_parser("serve")
    s.add_argument("--bin", required=True, help="directory with pg_ctl/initdb and mysqld")
    s.add_argument("--engine", required=True, choices=["postgresql", "mysql"])
    s.add_argument("--work", required=True)
    s.add_argument("--scratch")
    s.add_argument("--sockets")
    serve(parser.parse_args())


if __name__ == "__main__":
    main()
