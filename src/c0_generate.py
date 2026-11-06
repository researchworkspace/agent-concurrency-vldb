"""Single-shot generation for C0's models.

Usage: python -I c0_generate.py --model MODEL --output DIR
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
PLAN = FOLDER / "c0-plan.json"
sys.path.insert(0, str(FOLDER))
import c0_tasks as tasks

VERSION = "agent-concurrency-c0-v1"
BLOCK = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S)


def configuration() -> dict[str, Any]:
    plan: dict[str, Any] = json.loads(PLAN.read_text("utf-8"))
    if plan["version"] != VERSION:
        raise ValueError("C0 plan differs from the implemented generator")
    return plan


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, indent=1, sort_keys=True) + "\n", "utf-8")
    os.replace(temporary, path)


def extract(output: str, function: str) -> str | None:
    for block in BLOCK.findall(output):
        if re.search(rf"^\s*def {re.escape(function)}\s*\(", block, re.M):
            return block
    return None


def jobs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"task": t, "framework": f, "condition": c, "seed": s}
        for t in plan["tasks"]
        for f in plan["frameworks"]
        for c in plan["conditions"]
        for s in plan["samples"]
    ]


class SamplingBackend:

    def __init__(self, model: dict[str, str], sampling: dict[str, Any]) -> None:
        import torch
        import transformers

        if not torch.cuda.is_available():
            raise RuntimeError("generation requires an allocated CUDA GPU")
        self.torch = torch
        self.sampling = sampling
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            model["model_id"], revision=model["revision"], local_files_only=True
        )
        self.model = transformers.AutoModelForCausalLM.from_pretrained(
            model["model_id"],
            revision=model["revision"],
            local_files_only=True,
            torch_dtype=getattr(torch, sampling["dtype"]),
            attn_implementation=sampling["attention"],
        ).to("cuda")
        self.model.eval()
        self.identity = {
            "backend": "transformers",
            **model,
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "device": torch.cuda.get_device_name(0),
            "sampling": sampling,
        }

    def generate(self, text: str, seed: int) -> dict[str, Any]:
        s = self.sampling
        rendered = self.tokenizer.apply_chat_template(
            [{"role": "user", "content": text}], tokenize=False, add_generation_prompt=True
        )
        inputs = self.tokenizer(rendered, return_tensors="pt", add_special_tokens=False).to("cuda")
        length = int(inputs["input_ids"].shape[1])
        if length > s["max_input_tokens"]:
            raise ValueError("input budget exceeded; silent truncation is forbidden")
        self.torch.manual_seed(seed)
        self.torch.cuda.manual_seed_all(seed)
        started = time.perf_counter()
        with self.torch.inference_mode():
            produced = self.model.generate(
                **inputs,
                do_sample=True,
                temperature=s["temperature"],
                top_p=s["top_p"],
                top_k=s["top_k"],
                repetition_penalty=s["repetition_penalty"],
                max_new_tokens=s["max_new_tokens"],
                pad_token_id=self.tokenizer.pad_token_id or self.tokenizer.eos_token_id,
            )
        new = produced[0, length:]
        return {
            "output": self.tokenizer.decode(new, skip_special_tokens=True),
            "input_tokens": length,
            "output_tokens": int(new.shape[0]),
            "seconds": time.perf_counter() - started,
            "cap_reached": int(new.shape[0]) >= s["max_new_tokens"],
        }


class DiagnosticBackend:

    identity = {"backend": "diagnostic", "revision": "agent-concurrency-c0-diagnostic-v1"}

    def generate(self, text: str, seed: int) -> dict[str, Any]:
        task = next(t for t in tasks.TASKS if tasks.TASKS[t]["spec"] in text)
        framework = "sqlalchemy" if "SQLAlchemy" in text else "django"
        variant = "correct" if seed % 2 == 0 else "buggy"
        code = tasks.REFERENCES[(task, framework, variant)]
        return {
            "output": f"Here is the module.\n\n```python{code}```\n",
            "input_tokens": len(text) // 4,
            "output_tokens": len(code) // 4,
            "seconds": 0.0,
            "cap_reached": False,
        }


def run(args: argparse.Namespace) -> None:
    plan = configuration()
    model = plan["models"][args.model]
    output = Path(args.output) / args.model
    backend: Any = (
        DiagnosticBackend() if args.diagnostic else SamplingBackend(model, plan["sampling"])
    )
    manifest = {
        "version": VERSION,
        "model": args.model,
        **model,
        "plan_sha256": file_hash(PLAN),
        "generator_sha256": file_hash(Path(__file__)),
        "tasks_sha256": file_hash(FOLDER / "c0_tasks.py"),
        "diagnostic": args.diagnostic,
    }
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text("utf-8")) != manifest:
            raise ValueError("existing output belongs to a different run; use a new output path")
    else:
        atomic_write(manifest_path, manifest)
    atomic_write(output / "backend.json", json.loads(json.dumps(backend.identity, default=str)))
    journal = output / "journal.jsonl"
    memo: dict[str, dict[str, Any]] = {}
    if journal.exists():
        for line in journal.read_text("utf-8").splitlines():
            if line:
                row = json.loads(line)
                memo[row["sha"]] = row
    native = len(memo)
    selected = jobs(plan)[: args.limit] if args.limit else jobs(plan)
    for job in selected:
        name = f"{job['task']}__{job['framework']}__{job['condition']}__s{job['seed']}"
        record_path = output / "generations" / f"{name}.json"
        if record_path.exists():
            continue
        text = tasks.prompt(job["task"], job["framework"], job["condition"])
        key = json.dumps([model, plan["sampling"], job["seed"], text], sort_keys=True)
        sha = hashlib.sha256(key.encode("utf-8")).hexdigest()
        row = memo.get(sha)
        if row is None:
            if native >= plan["maximum_generations_per_task"]:
                raise RuntimeError("generation ceiling reached; preserve the journal and inspect")
            row = {"sha": sha, **job, **backend.generate(text, job["seed"])}
            with journal.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            memo[sha] = row
            native += 1
        function = tasks.TASKS[job["task"]]["function"]
        atomic_write(
            record_path,
            {
                "model": args.model,
                **job,
                "sha": sha,
                "code": extract(row["output"], function),
                "output_tokens": row["output_tokens"],
                "cap_reached": row["cap_reached"],
            },
        )
        print(f"{name} tokens={row['output_tokens']} seconds={row['seconds']:.1f}", flush=True)
    atomic_write(
        output / "completion.json",
        {
            "records": len(list((output / "generations").glob("*.json"))),
            "jobs": len(selected),
            "native_generations": native,
            "journal_sha256": file_hash(journal) if journal.exists() else None,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, choices=sorted(configuration()["models"]))
    parser.add_argument("--output", required=True)
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="tests only: first N jobs")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
