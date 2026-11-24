"""Single-shot generation and the sequential-test and concurrency-test repair loops (benchmark stage).

Usage: python -I generate.py single --model MODEL --seeds main --output DIR
       python -I generate.py loop --model MODEL --mode loop_seq|loop_conc --seeds main --start DIR --output DIR --checker-python PY --pg-bin DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

FOLDER = Path(__file__).resolve().parent
sys.path.insert(0, str(FOLDER))
import tasks
import loop_feedback as feedback
import checks

VERSION = checks.VERSION
BLOCK = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S)
THINK = re.compile(r"<think>.*?</think>", re.S)
FINAL = "<|channel|>final<|message|>"


def atomic_write(path: Path, value: Any) -> None:
    checks.harness.atomic_write(path, value)


def file_hash(path: Path) -> str:
    return checks.harness.file_hash(path)


def answer_text(output: str, final_channel: bool) -> str:
    if final_channel:
        if FINAL not in output:
            return ""
        text = output.rsplit(FINAL, 1)[1]
        for end in ("<|return|>", "<|end|>", "<|call|>"):
            text = text.split(end, 1)[0]
        return text.strip()
    return THINK.sub("", output).strip()


def extract(answer: str, function: str) -> str | None:
    for block in BLOCK.findall(answer):
        if re.search(rf"^\s*def {re.escape(function)}\s*\(", block, re.M):
            return block
    return None


def jobs(plan: dict[str, Any], samples: list[int], conditions: list[str]) -> list[dict]:
    g = plan["generation"]
    return [
        {"task": t, "framework": f, "condition": c, "seed": s}
        for t in g["tasks"]
        for f in g["frameworks"]
        for c in conditions
        for s in samples
    ]


def stem(job: dict[str, Any], mode: str = "single") -> str:
    name = f"{job['task']}__{job['framework']}__{job['condition']}__s{job['seed']}"
    return name if mode == "single" else f"{name}__{mode}"


def pin_hub_kernels(entries: list[dict[str, str]]) -> dict[str, str]:
    if not entries:
        return {}
    from huggingface_hub import snapshot_download

    paths = {e["repo_id"]: snapshot_download(e["repo_id"], revision=e["revision"]) for e in entries}
    os.environ["LOCAL_KERNELS"] = ":".join(f"{repo}={path}" for repo, path in paths.items())
    return paths


class SamplingBackend:

    def __init__(self, model: dict[str, Any], max_input_tokens: int) -> None:
        import torch
        import transformers

        if not torch.cuda.is_available():
            raise RuntimeError("generation requires an allocated CUDA GPU")
        self.torch = torch
        self.model_spec = model
        kernels = pin_hub_kernels(model.get("hub_kernels", []))
        self.max_input_tokens = max_input_tokens
        common = {"revision": model["revision"], "local_files_only": True}
        if model.get("tokenizer") == "mistral_common":
            self.tokenizer = transformers.MistralCommonBackend.from_pretrained(
                model["model_id"], **common
            )
        else:
            self.tokenizer = transformers.AutoTokenizer.from_pretrained(model["model_id"], **common)
        loader = {
            "causal": transformers.AutoModelForCausalLM,
            "image_text_to_text": transformers.AutoModelForImageTextToText,
        }[model["loader"]]
        dtype = model["dtype"] if model["dtype"] == "auto" else getattr(torch, model["dtype"])
        extra: dict[str, Any] = {}
        if model.get("dequantize_fp8"):
            shipped = transformers.AutoConfig.from_pretrained(
                model["model_id"], **common
            ).quantization_config
            settings = shipped if isinstance(shipped, dict) else shipped.to_dict()
            extra["quantization_config"] = transformers.FineGrainedFP8Config.from_dict(
                {**settings, "dequantize": True}
            )
        self.model = loader.from_pretrained(
            model["model_id"],
            dtype=dtype,
            attn_implementation=model["attention"],
            device_map="cuda",
            **common,
            **extra,
        )
        self.model.eval()
        self.identity = {
            "backend": "transformers",
            **model,
            "model_class": type(self.model).__name__,
            "weight_dtypes": sorted({str(p.dtype) for p in self.model.parameters()}),
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "device": torch.cuda.get_device_name(0),
            "hub_kernels": kernels,
        }

    def encode(self, messages: list[dict[str, str]]) -> Any:
        kwargs = dict(self.model_spec["template_kwargs"])
        try:
            encoded = self.tokenizer.apply_chat_template(
                messages,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
                **kwargs,
            )
        except (TypeError, ValueError):
            encoded = self.tokenizer.apply_chat_template(
                messages, tokenize=True, return_dict=True, return_tensors="pt", **kwargs
            )
        return {k: v.to("cuda") for k, v in encoded.items() if hasattr(v, "to")}

    def generate(self, messages: list[dict[str, str]], seed: int) -> dict[str, Any]:
        s = self.model_spec["sampling"]
        inputs = self.encode(messages)
        length = int(inputs["input_ids"].shape[1])
        if length > self.max_input_tokens:
            raise ValueError("input budget exceeded; silent truncation is forbidden")
        self.torch.manual_seed(seed)
        self.torch.cuda.manual_seed_all(seed)
        cap = self.model_spec["max_new_tokens"]
        started = time.perf_counter()
        with self.torch.inference_mode():
            produced = self.model.generate(
                **inputs,
                do_sample=True,
                temperature=s["temperature"],
                top_p=s["top_p"],
                top_k=s["top_k"],
                repetition_penalty=s["repetition_penalty"],
                max_new_tokens=cap,
            )
        new = produced[0, length:]
        final_channel = bool(self.model_spec.get("final_channel"))
        output = self.tokenizer.decode(new, skip_special_tokens=not final_channel)
        return {
            "output": output,
            "answer": answer_text(output, final_channel),
            "input_tokens": length,
            "output_tokens": int(new.shape[0]),
            "seconds": time.perf_counter() - started,
            "cap_reached": int(new.shape[0]) >= cap,
        }


class DiagnosticBackend:

    identity = {"backend": "diagnostic", "revision": "agent-concurrency-benchmark-diagnostic"}

    def generate(self, messages: list[dict[str, str]], seed: int) -> dict[str, Any]:
        text = messages[0]["content"]
        task = next(t for t in tasks.TASKS if tasks.TASKS[t]["spec"] in text)
        framework = "sqlalchemy" if "SQLAlchemy" in text else "django"
        variant = "buggy" if len(messages) == 1 else "correct"
        code = tasks.REFERENCES[(task, framework, variant)]
        output = f"Here is the module.\n\n```python{code}```\n"
        return {
            "output": output,
            "answer": output,
            "input_tokens": sum(len(m["content"]) for m in messages) // 4,
            "output_tokens": len(code) // 4,
            "seconds": 0.0,
            "cap_reached": False,
        }


class Journal:
    def __init__(self, path: Path, ceiling: int) -> None:
        self.path = path
        self.ceiling = ceiling
        self.memo: dict[str, dict[str, Any]] = {}
        if path.exists():
            for line in path.read_text("utf-8").splitlines():
                if line:
                    row = json.loads(line)
                    self.memo[row["sha"]] = row
        self.native = len(self.memo)

    def call(self, backend: Any, model: dict, messages: list, seed: int, meta: dict) -> dict:
        identity = {k: model[k] for k in ("model_id", "revision", "sampling", "template_kwargs")}
        key = json.dumps([identity, model["max_new_tokens"], seed, messages], sort_keys=True)
        sha = hashlib.sha256(key.encode("utf-8")).hexdigest()
        row = self.memo.get(sha)
        if row is None:
            if self.native >= self.ceiling:
                raise RuntimeError("generation ceiling reached; preserve the journal and inspect")
            row = {"sha": sha, **meta, "seed": seed, **backend.generate(messages, seed)}
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.memo[row["sha"]] = row
            self.native += 1
        return row


def claim(output: Path, manifest: dict[str, Any]) -> None:
    checks.claim(output, manifest)


def base_manifest(args: argparse.Namespace, model: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "version": VERSION,
        "kind": kind,
        "model": args.model,
        **{k: model[k] for k in ("model_id", "revision")},
        "plan_sha256": file_hash(checks.PLAN),
        "generator_sha256": file_hash(Path(__file__)),
        "feedback_sha256": file_hash(FOLDER / "loop_feedback.py"),
        "tasks_sha256": file_hash(FOLDER / "tasks.py"),
        "diagnostic": args.diagnostic,
        "seeds": args.seeds,
    }


def make_backend(args: argparse.Namespace, model: dict[str, Any], plan: dict) -> Any:
    if args.diagnostic:
        return DiagnosticBackend()
    return SamplingBackend(model, plan["generation"]["max_input_tokens"])


SEED_SETS = {"pilot": "pilot_samples", "main": "main_samples", "all": "samples"}


def samples_of(args: argparse.Namespace, plan: dict) -> list[int]:
    return plan["generation"][SEED_SETS[args.seeds]]


def single(args: argparse.Namespace) -> None:
    plan = checks.configuration()
    model = plan["models"][args.model]
    if model["source"] != "benchmark":
        raise ValueError("the base models reuse their base-stage single-shot answers")
    output = Path(args.output) / args.model
    claim(output, base_manifest(args, model, "single"))
    backend = make_backend(args, model, plan)
    atomic_write(output / "backend.json", json.loads(json.dumps(backend.identity, default=str)))
    journal = Journal(output / "journal.jsonl", plan["generation"]["maximum_generations_per_model"])
    selected = jobs(plan, samples_of(args, plan), plan["generation"]["conditions"])
    selected = selected[: args.limit] if args.limit else selected
    for job in selected:
        record_path = output / "generations" / f"{stem(job)}.json"
        if record_path.exists():
            continue
        messages = [
            {
                "role": "user",
                "content": tasks.prompt(job["task"], job["framework"], job["condition"]),
            }
        ]
        row = journal.call(backend, model, messages, job["seed"], {**job, "round": 0})
        function = tasks.TASKS[job["task"]]["function"]
        atomic_write(
            record_path,
            {
                "model": args.model,
                **job,
                "mode": "single",
                "sha": row["sha"],
                "code": extract(row["answer"], function),
                "output_tokens": row["output_tokens"],
                "cap_reached": row["cap_reached"],
            },
        )
        print(f"{stem(job)} tokens={row['output_tokens']} seconds={row['seconds']:.1f}", flush=True)
    atomic_write(
        output / "completion.json",
        {
            "records": len(list((output / "generations").glob("*.json"))),
            "jobs": len(selected),
            "native_generations": journal.native,
        },
    )


def starting_answer(start: Path, job: dict[str, Any]) -> tuple[str, str | None]:
    record = json.loads((start / "generations" / f"{stem(job)}.json").read_text("utf-8"))
    rows = {}
    for line in (start / "journal.jsonl").read_text("utf-8").splitlines():
        if line:
            row = json.loads(line)
            rows[row["sha"]] = row
    row = rows[record["sha"]]
    return row.get("answer", row["output"]), record["code"]


def loop(args: argparse.Namespace) -> None:
    plan = checks.configuration()
    model = plan["models"][args.model]
    settings = plan["loop"]
    concurrent = args.mode == "loop_conc"
    output = Path(args.output) / f"{args.model}-{args.mode}"
    start = Path(args.start)
    manifest = base_manifest(args, model, args.mode)
    manifest["start"] = str(start.resolve())
    manifest["start_manifest"] = json.loads((start / "manifest.json").read_text("utf-8"))
    claim(output, manifest)
    backend = make_backend(args, model, plan)
    atomic_write(output / "backend.json", json.loads(json.dumps(backend.identity, default=str)))
    journal = Journal(output / "journal.jsonl", plan["generation"]["maximum_generations_per_model"])
    checker = feedback.Checker(args.checker_python, args.pg_bin, output / "checker", args.scratch)
    selected = jobs(plan, samples_of(args, plan), [settings["condition"]])
    selected = selected[: args.limit] if args.limit else selected
    try:
        for job in selected:
            record_path = output / "generations" / f"{stem(job, args.mode)}.json"
            if record_path.exists():
                continue
            task, framework = job["task"], job["framework"]
            function = tasks.TASKS[task]["function"]
            answer, code = starting_answer(start, job)
            messages = [{"role": "user", "content": tasks.prompt(task, framework, "plain")}]
            history = []
            rounds = 0
            while True:
                check = None if code is None else checker.check(task, framework, code, concurrent)
                note = feedback.feedback_text(task, check)
                history.append(
                    {
                        "round": rounds,
                        "has_code": code is not None,
                        "functional": bool(check and check["functional"]["passed"]),
                        "concurrent_violations": (check or {})
                        .get("concurrent", {})
                        .get("violating_trials"),
                        "passed": note is None,
                    }
                )
                if note is None or rounds == settings["max_repair_rounds"]:
                    break
                rounds += 1
                messages += [
                    {"role": "assistant", "content": answer},
                    {"role": "user", "content": note},
                ]
                seed = job["seed"] + settings["round_seed_offset"] * rounds
                row = journal.call(backend, model, messages, seed, {**job, "round": rounds})
                answer = row["answer"]
                code = extract(answer, function)
                history[-1]["sha_next"] = row["sha"]
            atomic_write(
                record_path,
                {
                    "model": args.model,
                    **job,
                    "mode": args.mode,
                    "code": code,
                    "repair_rounds": rounds,
                    "loop_passed": history[-1]["passed"],
                    "history": history,
                },
            )
            print(
                f"{stem(job, args.mode)} rounds={rounds} passed={history[-1]['passed']}", flush=True
            )
    finally:
        checker.close()
    atomic_write(
        output / "completion.json",
        {
            "records": len(list((output / "generations").glob("*.json"))),
            "jobs": len(selected),
            "native_generations": journal.native,
        },
    )


def load_check(args: argparse.Namespace) -> None:
    plan = checks.configuration()
    model = plan["models"][args.model]
    started = time.perf_counter()
    backend = make_backend(args, model, plan)
    loaded = time.perf_counter() - started
    job = {"task": "room_book", "framework": "sqlalchemy", "condition": "plain", "seed": 100}
    messages = [{"role": "user", "content": tasks.prompt("room_book", "sqlalchemy", "plain")}]
    row = backend.generate(messages, job["seed"])
    code = extract(row["answer"], "book")
    result = {
        "model": args.model,
        "identity": json.loads(json.dumps(backend.identity, default=str)),
        "load_seconds": round(loaded, 1),
        "output_tokens": row["output_tokens"],
        "seconds": round(row["seconds"], 1),
        "cap_reached": row["cap_reached"],
        "parsed": code is not None,
        "answer_head": row["answer"][:400],
    }
    if not args.diagnostic:
        import torch

        result["max_memory_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 1)
    atomic_write(Path(args.output) / f"{args.model}.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "identity"}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    models = sorted(checks.configuration()["models"])
    s = commands.add_parser("single")
    lp = commands.add_parser("loop")
    lc = commands.add_parser("load-check")
    for p in (s, lp, lc):
        p.add_argument("--model", required=True, choices=models)
        p.add_argument("--output", required=True)
        p.add_argument("--diagnostic", action="store_true")
        p.add_argument(
            "--seeds",
            choices=sorted(SEED_SETS),
            default="main",
            help="pilot: 100-101; main: 102-109, the confirmatory seeds; all: 100-109",
        )
        p.add_argument("--limit", type=int, default=0, help="tests only: first N jobs")
    lp.add_argument("--mode", required=True, choices=["loop_seq", "loop_conc"])
    lp.add_argument("--start", required=True, help="the model's single-shot generation directory")
    lp.add_argument("--checker-python", required=True, help="Python of the harness environment")
    lp.add_argument("--pg-bin", required=True)
    lp.add_argument("--scratch")
    args = parser.parse_args()
    {"single": single, "loop": loop, "load-check": load_check}[args.command](args)


if __name__ == "__main__":
    main()
