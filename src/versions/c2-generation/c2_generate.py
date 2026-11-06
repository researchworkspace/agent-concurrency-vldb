"""Application single-shot generation and the C2 loops (self-test, MySQL-checked, application loops).

Usage: python -I c2_generate.py single --model MODEL --seeds main --output DIR
       python -I c2_generate.py loop --model MODEL --mode MODE [--suite apps --app APP] --seeds main --start DIR --output DIR --checker-python PY --bin DIR
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
import c0_tasks as tasks
import c1_feedback
import c1_generate as c1g
import c1_run
import c2_apps as apps
import c2_feedback
import c2_selftest as selftest

VERSION = c2_feedback.VERSION
TEST_BLOCK = re.compile(rf"^\s*def {selftest.TEST_FUNCTION}\s*\(", re.M)


def extract_test(answer: str) -> str | None:
    for block in c1g.BLOCK.findall(answer):
        if TEST_BLOCK.search(block):
            return block
    return None


def extract_module(answer: str, function: str) -> str | None:
    blocks = [b for b in c1g.BLOCK.findall(answer)
              if re.search(rf"^\s*def {re.escape(function)}\s*\(", b, re.M)]
    plain = [b for b in blocks if not TEST_BLOCK.search(b)]
    return (plain or blocks or [None])[0]


class Benchmark:

    name = "benchmark"

    @staticmethod
    def jobs(c1: dict, samples: list[int], conditions: list[str], app: str | None = None) -> list:
        return c1g.jobs(c1, samples, conditions)

    @staticmethod
    def prompt(job: dict[str, Any]) -> str:
        return tasks.prompt(job["task"], job["framework"], job["condition"])

    @staticmethod
    def function(job: dict[str, Any]) -> str:
        return tasks.TASKS[job["task"]]["function"]

    @staticmethod
    def feedback(job: dict[str, Any], check: dict[str, Any] | None) -> str | None:
        return c1_feedback.feedback_text(job["task"], check)

    @staticmethod
    def test_request(job: dict[str, Any], runs: int) -> str:
        return selftest.test_request(job["task"], runs)


class Apps:

    name = "apps"

    @staticmethod
    def jobs(c1: dict, samples: list[int], conditions: list[str], app: str | None = None) -> list:
        handlers = c2_feedback.configuration()["apps"]["handlers"]
        return [
            {"task": h, "framework": apps.framework_of(h), "condition": c, "seed": s}
            for h in handlers if app is None or apps.app_of(h) == app
            for c in conditions
            for s in samples
        ]

    @staticmethod
    def prompt(job: dict[str, Any]) -> str:
        return apps.prompt(job["task"], job["condition"])

    @staticmethod
    def function(job: dict[str, Any]) -> str:
        return apps.HANDLERS[job["task"]]["function"]

    @staticmethod
    def feedback(job: dict[str, Any], check: dict[str, Any] | None) -> str | None:
        return apps.feedback_text(job["task"], check)

    @staticmethod
    def test_request(job: dict[str, Any], runs: int) -> str:
        return selftest.app_test_request(job["task"], runs)


SUITES = {"benchmark": Benchmark, "apps": Apps}


class ChunkedPrefill:

    def __init__(self, backend: Any, settings: dict[str, Any]) -> None:
        self.backend = backend
        self.above = settings["above_input_tokens"]
        self.chunk = settings["chunk_tokens"]
        self.identity = {**backend.identity, "prefill_chunk": {
            "above_input_tokens": self.above, "chunk_tokens": self.chunk}}

    def generate(self, messages: list[dict[str, str]], seed: int) -> dict[str, Any]:
        length = int(self.backend.encode(messages)["input_ids"].shape[1])
        if length <= self.above:
            return self.backend.generate(messages, seed)
        config = self.backend.model.generation_config
        config.prefill_chunk_size = self.chunk
        try:
            row = self.backend.generate(messages, seed)
        finally:
            config.prefill_chunk_size = None
        return {**row, "prefill_chunk_tokens": self.chunk}


def make_backend(args: argparse.Namespace, model: dict[str, Any], c1: dict, c2: dict) -> Any:
    if args.diagnostic:
        return DiagnosticBackend()
    backend = c1g.make_backend(args, model, c1)
    settings = c2["generation"]["prefill_chunk"]
    return ChunkedPrefill(backend, settings) if args.model in settings["models"] else backend


class DiagnosticBackend:

    identity = {"backend": "diagnostic", "revision": "agent-concurrency-c2-diagnostic-v1"}

    TEST = '''
import c0_tasks


def test_concurrency():
    task = {task!r}
    for statement in c0_tasks.TASKS[task]["concurrent"]["seed"]:
        db_execute(statement)
    requests = [c0_tasks.request_plan(task, w)[0] for w in range(8)]
    run_concurrently(requests)
    found = c0_tasks.invariant(task, lambda sql: db_execute(sql)[0][0])
    assert not found, found
'''

    def generate(self, messages: list[dict[str, str]], seed: int) -> dict[str, Any]:
        text = messages[0]["content"]
        variant = "buggy" if len(messages) == 1 else "correct"
        handler = next((h for h in apps.HANDLERS if apps.HANDLERS[h]["spec"] in text), None)
        testing = messages[-1]["content"].startswith(
            "Your module passes the application's sequential")
        if handler is not None:
            code = (apps.ORACLE_TEST.format(handler=handler) if testing
                    else apps.REFERENCES[(handler, variant)])
            output = f"Here it is.\n\n```python{code}```\n"
            return {"output": output, "answer": output, "input_tokens": 0,
                    "output_tokens": len(code) // 4, "seconds": 0.0, "cap_reached": False}
        task = next(t for t in tasks.TASKS if tasks.TASKS[t]["spec"] in text)
        framework = "sqlalchemy" if "SQLAlchemy" in text else "django"
        if testing:
            code = self.TEST.format(task=task)
        else:
            code = tasks.REFERENCES[(task, framework, variant)]
        output = f"Here it is.\n\n```python{code}```\n"
        return {"output": output, "answer": output, "input_tokens": 0,
                "output_tokens": len(code) // 4, "seconds": 0.0, "cap_reached": False}


def manifest(args: argparse.Namespace, model: dict[str, Any]) -> dict[str, Any]:
    m = c1g.base_manifest(args, model, args.mode)
    m["version"] = VERSION
    m["c1_version"] = c1_run.VERSION
    m["c2_plan_sha256"] = c1g.file_hash(c2_feedback.PLAN)
    m["c2_generator_sha256"] = c1g.file_hash(Path(__file__))
    m["c2_feedback_sha256"] = c1g.file_hash(FOLDER / "c2_feedback.py")
    m["c2_selftest_sha256"] = c1g.file_hash(FOLDER / "c2_selftest.py")
    m["c2_apps_sha256"] = c1g.file_hash(FOLDER / "c2_apps.py")
    m["c2_appharness_sha256"] = c1g.file_hash(FOLDER / "c2_appharness.py")
    m["suite"] = args.suite
    if getattr(args, "start", None):
        m["start"] = str(Path(args.start).resolve())
        m["start_manifest"] = json.loads((Path(args.start) / "manifest.json").read_text("utf-8"))
    return m


def single(args: argparse.Namespace) -> None:
    c1 = c1_run.configuration()
    c2 = c2_feedback.configuration()
    model = c1["models"][args.model]
    output = Path(args.output) / args.model
    c1g.claim(output, manifest(args, model) | {"mode": "single"})
    backend = make_backend(args, model, c1, c2)
    c1g.atomic_write(output / "backend.json", json.loads(json.dumps(backend.identity, default=str)))
    journal = c1g.Journal(output / "journal.jsonl",
                          c1["generation"]["maximum_generations_per_model"])
    selected = Apps.jobs(c1, c1g.samples_of(args, c1), c2["apps"]["conditions"])
    selected = selected[: args.limit] if args.limit else selected
    for job in selected:
        record_path = output / "generations" / f"{c1g.stem(job)}.json"
        if record_path.exists():
            continue
        messages = [{"role": "user", "content": Apps.prompt(job)}]
        row = journal.call(backend, model, messages, job["seed"], {**job, "round": 0})
        c1g.atomic_write(record_path, {
            "model": args.model, **job, "mode": "single", "sha": row["sha"],
            "code": c1g.extract(row["answer"], Apps.function(job)),
            "output_tokens": row["output_tokens"], "cap_reached": row["cap_reached"],
        })
        print(f"{c1g.stem(job)} tokens={row['output_tokens']} seconds={row['seconds']:.1f}",
              flush=True)
    c1g.atomic_write(output / "completion.json", {
        "records": len(list((output / "generations").glob("*.json"))),
        "jobs": len(selected), "native_generations": journal.native})


def loop(args: argparse.Namespace) -> None:
    c1 = c1_run.configuration()
    c2 = c2_feedback.configuration()
    suite = SUITES[args.suite]
    model = c1["models"][args.model]
    name = f"{args.model}-{args.mode}" + (f"-{args.app}" if args.suite == "apps" else "")
    output = Path(args.output) / name
    c1g.claim(output, manifest(args, model))
    backend = make_backend(args, model, c1, c2)
    c1g.atomic_write(output / "backend.json", json.loads(json.dumps(backend.identity, default=str)))
    journal = c1g.Journal(output / "journal.jsonl",
                          c1["generation"]["maximum_generations_per_model"])
    if args.suite == "apps":
        if args.mode not in c2["apps"]["loop_modes"] or not args.app:
            raise ValueError("an apps loop is loop_seq, loop_conc or loop_self, with --app")
        engine = c2["apps"]["loop_modes"][args.mode]["engine"]
        concurrent = c2["apps"]["loop_modes"][args.mode].get("concurrent", False)
        condition = "plain"
    else:
        if args.mode not in ("loop_self", "loop_conc_my"):
            raise ValueError("C2's benchmark loops are loop_self and loop_conc_my")
        engine = c2[args.mode]["engine"]
        concurrent = True
        condition = c2[args.mode]["condition"]
    checker = c2_feedback.Checker(args.checker_python, args.bin, engine, output / "checker",
                                  args.scratch, args.app if args.suite == "apps" else None,
                                  args.ctfd_source)
    selected = suite.jobs(c1, c1g.samples_of(args, c1), [condition], args.app)
    selected = selected[: args.limit] if args.limit else selected

    def run_one(job, *rest):
        if args.mode == "loop_self":
            return loop_self_job(job, *rest, suite=suite)
        return loop_check_job(job, *rest, suite=suite, concurrent=concurrent)
    try:
        for job in selected:
            record_path = output / "generations" / f"{c1g.stem(job, args.mode)}.json"
            if record_path.exists():
                continue
            record = run_one(job, args, c1, c2, model, backend, journal, checker)
            c1g.atomic_write(record_path, {"model": args.model, **job, "mode": args.mode, **record})
            print(f"{c1g.stem(job, args.mode)} rounds={record['repair_rounds']} "
                  f"passed={record['loop_passed']}", flush=True)
    finally:
        checker.close()
    c1g.atomic_write(output / "completion.json", {
        "records": len(list((output / "generations").glob("*.json"))),
        "jobs": len(selected),
        "native_generations": journal.native,
    })


def loop_check_job(job, args, c1, c2, model, backend, journal, checker, *, suite,
                   concurrent: bool) -> dict[str, Any]:
    settings = c1["loop"]
    task, framework = job["task"], job["framework"]
    function = suite.function(job)
    answer, code = c1g.starting_answer(Path(args.start), job)
    messages = [{"role": "user", "content": suite.prompt({**job, "condition": "plain"})}]
    history: list[dict[str, Any]] = []
    rounds = 0
    while True:
        check = None if code is None else checker.check(task, framework, code, concurrent)
        note = suite.feedback(job, check)
        c = (check or {}).get("concurrent") or {}
        violations = (c.get("violating_trials") if "violating_trials" in c
                      else {rule: s["violating_trials"] for rule, s in c.items()} or None)
        history.append({
            "round": rounds,
            "has_code": code is not None,
            "functional": bool(check and check["functional"]["passed"]),
            "concurrent_violations": violations,
            "passed": note is None,
        })
        if note is None or rounds == settings["max_repair_rounds"]:
            break
        rounds += 1
        messages += [{"role": "assistant", "content": answer}, {"role": "user", "content": note}]
        seed = job["seed"] + settings["round_seed_offset"] * rounds
        row = journal.call(backend, model, messages, seed, {**job, "round": rounds})
        answer = row["answer"]
        code = c1g.extract(answer, function)
        history[-1]["sha_next"] = row["sha"]
    return {"code": code, "repair_rounds": rounds, "loop_passed": history[-1]["passed"],
            "history": history}


def loop_self_job(job, args, c1, c2, model, backend, journal, checker, *,
                  suite) -> dict[str, Any]:
    settings = c2["loop_self"]
    runs = settings["self_test_runs"]
    task, framework = job["task"], job["framework"]
    function = suite.function(job)
    answer, code = c1g.starting_answer(Path(args.start), job)
    messages = [{"role": "user", "content": suite.prompt({**job, "condition": "plain"})}]
    history: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    test: str | None = None
    rounds = 0

    def ask(content: str, seed: int, meta: dict[str, Any]) -> dict[str, Any]:
        messages.extend([{"role": "assistant", "content": answer},
                         {"role": "user", "content": content}])
        return journal.call(backend, model, messages, seed, {**job, **meta})

    while True:
        entry: dict[str, Any] = {"round": rounds, "has_code": code is not None}
        history.append(entry)
        check = None if code is None else checker.check(task, framework, code, False)
        entry["functional"] = bool(check and check["functional"]["passed"])
        if not entry["functional"]:
            note = suite.feedback(job, check)
        else:
            if test is None:
                seed = (job["seed"] + settings["test_seed_offset"]
                        + settings["round_seed_offset"] * rounds)
                row = ask(suite.test_request(job, runs), seed,
                          {"round": rounds, "request": "test"})
                answer = row["answer"]
                test = extract_test(answer)
                tests.append({"round": rounds, "sha": row["sha"], "code": test,
                              "classification": selftest.classify(test)})
                entry["test_requested"] = True
            if test is None:
                outcome = {"runs": runs, "failed": runs, "no_test": True,
                           "first_failure": "Your answer did not contain a ```python code block "
                           f"that defines `{selftest.TEST_FUNCTION}()`."}
            else:
                outcome = checker.selftest(task, framework, code, test)
            entry["selftest"] = {k: outcome.get(k) for k in
                                 ("runs", "failed", "first_failure_kind", "load_error", "no_test")}
            entry["selftest"]["first_failure"] = outcome.get("first_failure", "")[-600:]
            note = selftest.selftest_feedback(outcome)
        entry["passed"] = note is None
        if note is None or rounds == settings["max_repair_rounds"]:
            break
        rounds += 1
        row = ask(note, job["seed"] + settings["round_seed_offset"] * rounds, {"round": rounds})
        answer = row["answer"]
        code = extract_module(answer, function)
        revised = extract_test(answer) if test is not None else None
        if revised is not None and revised != code:
            test = revised
            tests.append({"round": rounds, "sha": row["sha"], "code": test,
                          "classification": selftest.classify(test)})
            entry["test_revised_next"] = True
        entry["sha_next"] = row["sha"]
    return {"code": code, "test": test, "tests": tests, "repair_rounds": rounds,
            "loop_passed": history[-1]["passed"], "history": history}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sp = commands.add_parser("single", help="E2: single-shot answers to the handler prompts")
    sp.add_argument("--model", required=True, choices=sorted(c1_run.configuration()["models"]))
    sp.add_argument("--output", required=True)
    sp.add_argument("--diagnostic", action="store_true")
    sp.add_argument("--seeds", choices=sorted(c1g.SEED_SETS), default="main")
    sp.add_argument("--limit", type=int, default=0, help="tests only: first N jobs")
    sp.set_defaults(suite="apps", mode="single")
    lp = commands.add_parser("loop")
    lp.add_argument("--model", required=True, choices=sorted(c1_run.configuration()["models"]))
    lp.add_argument("--mode", required=True,
                    choices=["loop_self", "loop_conc_my", "loop_seq", "loop_conc"])
    lp.add_argument("--suite", choices=sorted(SUITES), default="benchmark")
    lp.add_argument("--app", choices=sorted(apps.APPS), help="apps suite: the application")
    lp.add_argument("--ctfd-source", help="apps suite, CTFd: CTFd checkout at the pinned release")
    lp.add_argument("--output", required=True)
    lp.add_argument("--start", required=True, help="the model's single-shot generation directory")
    lp.add_argument("--checker-python", required=True, help="Python of the harness environment")
    lp.add_argument("--bin", required=True, help="directory with the PostgreSQL and MySQL servers")
    lp.add_argument("--scratch")
    lp.add_argument("--diagnostic", action="store_true")
    lp.add_argument("--seeds", choices=sorted(c1g.SEED_SETS), default="main")
    lp.add_argument("--limit", type=int, default=0, help="tests only: first N jobs")
    args = parser.parse_args()
    single(args) if args.command == "single" else loop(args)


if __name__ == "__main__":
    main()
