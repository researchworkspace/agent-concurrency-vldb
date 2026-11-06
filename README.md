# Isolation anomalies in LLM-written database code: artifact

This repository holds the code, prompts, derived results and instructions for the PVLDB
Experiment, Analysis & Benchmark paper *Passing Sequential Tests, Broken Under Load: Isolation
Anomalies in LLM-Written Database Code*. The raw results (generated programs, model answers,
loop records and every concurrency check) are in a separate data archive,
`agent-concurrency-vldb-data.tar.gz` (see [Data](#data)).

The study generates request handlers with six open coding models, checks each handler for
functional correctness, then runs it under concurrent requests on PostgreSQL 16.15 and MySQL 9.7.4
in up to six isolation and retry configurations. Every number, verdict and figure in the paper can
be recomputed from the data archive in a few minutes without a database or GPU
(`scripts/reproduce_numbers.sh`). The checks themselves can be rerun from the released programs
on any Linux machine with conda; generating new programs needs an 80 GB GPU.

## Contents

| Path | Contents |
| --- | --- |
| `src/` | The code that produced the data, flat because the modules import each other. Comments and descriptive notes are trimmed; the code is unchanged. `SHA256SUMS` lists every file. |
| `src/c0-plan.json`, `c1-plan.json`, `c2-plan.json` | Plans read by the code: tasks, models with revisions and sampling settings, configurations, trial counts, held-out seeds, software pins, and the decision criteria H1 to H12 with their thresholds. |
| `src/versions/` | Two earlier versions of files that wrote part of the data (see [Code versions](#code-versions)). |
| `prompts/` | Every first-turn prompt: 32 benchmark prompts, 6 application prompts, and the self-test loop's request for a test. |
| `results/` | The derived files the paper reads: criteria reports and the paper's numbers. |
| `analysis/make_figures.py` | Figures 4 to 7 from `results/c1-paper-numbers.json` and `results/c2-paper-numbers.json`. |
| `scripts/` | Environment setup, smoke test, number reproduction, hash check, prompt export. |
| `docs/environment/` | Package lists of the environments used (conda list and pip freeze). |

The phases are named C0, C1 and C2 in file names. C0 checked the two Qwen coders on PostgreSQL.
C1 added four models, MySQL, the sequential-test and concurrency-test repair loops, request retry
and request replay, and the contention sweep. C2 added the self-test loop, the MySQL-checked loop,
the two real applications and the sustained-load measurement. The replay ablation and the
savepoint lock trace are descriptive diagnostics on C1's programs.

## Paper map

| Paper | Produced by | Read from |
| --- | --- | --- |
| Table 1 (tasks), Figure 2 (prompt), Section 3 | `src/c0_tasks.py` | `prompts/benchmark/` |
| Table 2 (application rules) | `src/c2_apps.py` | `prompts/apps/` |
| Table 3 (models), Table 4 (configurations) | plans | `src/c1-plan.json` (`models`, `engines`) |
| Table 5 (criteria) | plans; `c1_run.py report`, `c2_analysis.py report` | `results/c1-report.json` (H1 to H7), `results/c2-report.json` (H8 to H12) |
| Reference validation (Section 3) | `c1_run.py run`, `c2_appharness.py gates` | `results/c1-paper-numbers.json` (`reference_gates`) |
| Figure 4, RQ1 | `c1_results_analysis.py` | `c1-paper-numbers.json` (`prevalence`) |
| Table 6, Figure 5, RQ2 | `c1_results_analysis.py`, `c2_results_analysis.py` | `modes`, `transitions`; `e1_*` |
| Tables 7 and 8, RQ3 | `c1_results_analysis.py`, `c2_results_analysis.py` | `engine_pairs`, `engine_mysql_only`, `mysql_rr_rc`; `e1_my_*` |
| Table 9, Figure 6, RQ4 | `c1_results_analysis.py`, `c2_ablation.py report` | `remedies`, `retry_categories`, `replay_failures`; `results/c2-ablation-report.json` |
| Figure 7, Table 10, RQ5 | `c1_results_analysis.py`, `c2_results_analysis.py` | `sweep`; `e3_tables`, `e3_spread` |
| Table 11, RQ6 | `c2_results_analysis.py` | `e2_*` |
| Section 5.2 (code patterns) | `c1_results_analysis.py` | `code_patterns` |
| Supplementary numbers in the text (yields, clustered intervals, hang-only verdicts, absolute throughput) | `c2_results_analysis.py` | `supplementary_*` |
| MySQL savepoint behaviour (RQ4) | `c2_diagnose.py`, `c2_savepoint.py` | data: `agent-concurrency-c2-diagnostics/` |

Figure 1 is a single-shot Qwen2.5-Coder-32B program for `coupon_redeem` (Django, hint prompt),
stored with the other C0 programs in `agent-concurrency-c0-main/generations/qwen25coder32b/`.

## Requirements

- **Number reproduction:** Python 3.9 or later; matplotlib for the figures. No database, no GPU.
- **Checks:** Linux x86_64, conda (conda-forge), git, bash 4 or later. The study used 16 cores and
  32 to 64 GB of memory per check job; load runs used 40 cores. Each check starts its own private
  PostgreSQL or MySQL server from the conda environment, so no system database is needed and no
  root access is used. Server sockets go to the system temporary directory unless `--sockets` names
another; keep that path short.
- **Generation:** one NVIDIA H100 80 GB (or equal) and 160 GB of host memory; gpt-oss-120b needs
  all of it. Roughly 350 GB of disk for the model snapshots.

Software versions (from the plans):

| Environment | Pins |
| --- | --- |
| check | Python 3.11.18, PostgreSQL 16.15, MySQL 9.7.4, SQLAlchemy 2.0.54, Django 5.2.17, psycopg 3.2.3, mysqlclient 2.3.0 |
| oscar | the check environment plus django-oscar 4.2.1 and the pins in `c2-plan.json` |
| ctfd | CTFd 3.8.9 (commit `5f345fb`) with its pinned requirements and psycopg2-binary 2.9.10 |
| gen | Python 3.11, torch 2.14.1 (CUDA 12.6), transformers 5.20.2, accelerate 1.15.0, kernels 0.17.2, mistral-common 1.12.0, huggingface-hub 2.2.0, safetensors 0.8.0 |

C0's two generation runs used torch 2.6.0+cu124 and transformers 4.57.1, as each generation
folder's `backend.json` records.

## Quick checks

```bash
tar -xzf agent-concurrency-vldb-data.tar.gz            # creates agent-concurrency-vldb-data/
DATA=$PWD/agent-concurrency-vldb-data
(cd "$DATA" && shasum -a 256 -c SHA256SUMS --quiet)   # archive integrity

python3 scripts/verify_hashes.py "$DATA"/agent-concurrency-c{0,1,2}-main
bash scripts/reproduce_numbers.sh "$DATA" out/numbers   # a few minutes
```

`verify_hashes.py` hashes `src/` and compares it with the code hashes in every manifest of the
released runs. `reproduce_numbers.sh` recomputes the C0 report, both criteria reports, the paper's
numbers and the ablation report, compares each with `results/`, and draws Figures 4 to 7 into
`out/numbers/figures/` when matplotlib is available (`PYTHON=... bash scripts/reproduce_numbers.sh`
selects the interpreter). It prints `identical` for each file.

Smoke test of the checks (about 40 minutes on 16 cores):

```bash
bash scripts/setup_env.sh env check        # about 10 minutes
bash scripts/setup_env.sh env apps         # optional: django-oscar and CTFd
bash scripts/smoke_test.sh env out/smoke "$DATA"
```

It runs the 32 benchmark references on both engines (every correct reference stays clean and every
buggy reference violates at the framework default), re-checks eight released programs on
PostgreSQL under all five configurations and compares their verdicts with the released ones, and,
if the application environments exist, runs the application references on both engines.

## Full reproduction

All commands run from the repository root with `SRC=src`, `ENV=env` (from `setup_env.sh`),
`OUT` a new output folder and `TMP` a scratch directory. Each command resumes if it is rerun with
the same output folder. Times are those of the study on 16 cores unless stated.

### Models

Download each model in `src/c1-plan.json` (`models`) at its pinned `revision` into one cache, and
set `HF_HUB_CACHE` to it:

```bash
env/gen/bin/python -I -c 'import json, sys
from huggingface_hub import snapshot_download
for m in json.load(open("src/c1-plan.json"))["models"].values():
    print(snapshot_download(m["model_id"], revision=m["revision"]))'
```

### Generation (GPU)

```bash
# C0: the two Qwen coders, single shot
python -I $SRC/c0_generate.py --model qwen25coder32b --output $OUT/c0/generations
# C1: single shot for the other four models, then both loops for all six
python -I $SRC/c1_generate.py single --model qwen38_27b --seeds main --output $OUT/c1/generations
python -I $SRC/c1_generate.py loop --model qwen38_27b --mode loop_seq --seeds main \
    --start $OUT/c1/generations/qwen38_27b --output $OUT/c1/generations \
    --checker-python $ENV/check/bin/python --pg-bin $ENV/check/bin --scratch $TMP
# C2: application single shot and loops, self-test loop, MySQL-checked loop
python -I $SRC/c2_generate.py single --model qwen38_27b --seeds main --output $OUT/c2/generations
python -I $SRC/c2_generate.py loop --model qwen38_27b --suite apps --app oscar --mode loop_self \
    --seeds main --start $OUT/c2/generations/qwen38_27b --output $OUT/c2/generations \
    --checker-python $ENV/oscar/bin/python --bin $ENV/check/bin --ctfd-source $ENV/ctfd-src --scratch $TMP
python -I $SRC/c2_generate.py loop --model qwen38_27b --mode loop_conc_my --seeds main \
    --start $OUT/c1/generations/qwen38_27b --output $OUT/c2/generations \
    --checker-python $ENV/check/bin/python --bin $ENV/check/bin --scratch $TMP
```

`python` is `$ENV/gen/bin/python`. Repeat for each model (`qwen25coder32b`, `qwen3coder30b`,
`qwen38_27b`, `devstral2_24b`, `gptoss120b`, `seedcoder8b`), loop mode (`loop_seq`, `loop_conc`;
in C2 also `loop_self` on each application and on the benchmark, and `loop_conc_my`) and
application (`oscar` with the oscar checker, `ctfd` with the ctfd checker). The loops of the Qwen
coders start from their C0 folders. Each single-shot or loop run took up to 12 hours on one H100.
Sampling is seeded, but GPU
kernels are not bitwise deterministic, so regenerated answers can differ from the released ones;
the checks below can run on the released programs instead.

### Checks (CPU)

```bash
P=$ENV/check/bin/python; B=$ENV/check/bin
# C1: every C1 generation folder plus the Qwen coders' C0 folders, on each engine
$P -I $SRC/c1_run.py run --engine postgresql --output $OUT/c1/harness-postgresql --bin $B \
    --scratch $TMP --generations GENERATION_FOLDERS...
$P -I $SRC/c1_run.py summary --output $OUT/c1/harness-postgresql --save $OUT/c1/harness-postgresql-summary.json
# contention sweep: levels w2, w4, w8, w16, w8-spread50ms on each engine
$P -I $SRC/c1_run.py sweep --engine postgresql --level w8 --run $OUT/c1/harness-postgresql \
    --output $OUT/c1/sweep-postgresql-w8 --bin $B --scratch $TMP
$P -I $SRC/c1_run.py report --runs $OUT/c1/harness-postgresql $OUT/c1/harness-mysql \
    --sweeps $OUT/c1/sweep-*/ --save $OUT/c1/c1-report.json
# C2 E1: self-test and MySQL-checked loop answers, on each engine
$P -I $SRC/c1_run.py run --engine mysql --output $OUT/c2/harness-e1-mysql --bin $B \
    --scratch $TMP --generations $OUT/c2/generations/*-loop_self $OUT/c2/generations/*-loop_conc_my
# C2 E2: application programs (single shot and that application's loops), on each engine
$ENV/oscar/bin/python -I $SRC/c2_appharness.py run --app oscar --engine postgresql \
    --output $OUT/c2/harness-e2-oscar-postgresql --bin $B --scratch $TMP \
    --ctfd-source $ENV/ctfd-src --generations APPLICATION_FOLDERS...
# C2 E3: sustained load, 20 shards per engine on 40 cores each
$P -I $SRC/c2_load.py run --engine postgresql --run $OUT/c1/harness-postgresql \
    --output $OUT/c2/load-postgresql/shard-00 --bin $B --scratch $TMP --shards 20 --shard 0
$P -I $SRC/c2_analysis.py report --c2 $OUT/c2 --c1 $OUT/c1 --save $OUT/c2/c2-report.json
```

| Step | Items (programs and references) | Time per engine (PostgreSQL / MySQL) |
| --- | --- | --- |
| C0 checks | 672 | 0.5 h (PostgreSQL only) |
| C1 checks | 2,592 | 11.0 h / 3.6 h |
| C1 sweep, per level | 1,237 / 1,177 | 0.4 to 1.2 h |
| C2 E1 checks | 1,568 | 8.1 h / 3.5 h |
| C2 E2 checks, per application | 484 | 2.8 to 4.7 h / 1.3 to 2.2 h |
| C2 E3, per shard | 20 shards | 1.7 to 1.9 h on 40 cores |
| Replay ablation, per shard | 4 shards | 0.4 to 0.7 h |

The released E3 measurement on PostgreSQL ran its 20 shards one after another on nodes of the same
type (folder `load-postgresql-2`); pass `--load postgresql=load-postgresql-2` to
`c2_analysis.py report` and `c2_results_analysis.py` to read it.

### Diagnostics

```bash
$P -I $SRC/c2_ablation.py run --engine postgresql --bin $B --c1 $OUT/c1 --output $OUT/ablation \
    --shard 0 --shards 4 --scratch $TMP
$P -I $SRC/c2_ablation.py report --c1 $OUT/c1 --output $OUT/ablation --save $OUT/c2-ablation-report.json
$P -I $SRC/c2_savepoint.py --engine mysql --bin $B --output $OUT/savepoint-trace --scratch $TMP
$P -I $SRC/c2_diagnose.py savepoint --engine mysql --bin $B --output $OUT/devstral-mysql --scratch $TMP
$P -I $SRC/c2_diagnose.py program --bin $B --c1 $OUT/c1 --output $OUT/devstral-mysql --trials 5 --scratch $TMP
```

### Running on Slurm

The study ran each step above as a Slurm job or job array on one node per task (16 cores and
32 to 64 GB for checks, 40 cores and 64 GB for load shards, one H100 and 160 GB for generation),
with the engine, model, level or shard chosen from the array index. Any scheduler works; each
command is independent once its inputs exist.

## Definitions

A program **violates** in a configuration when at least one of its 20 trials (8 concurrent
clients) ends with the task's invariant broken, or a trial hangs. It is **restored** when all 20
trials keep the invariant, none hangs or raises an error to the client, and its reported successes
reach the plan's share (`restored_success_share`) of the serial optimum. The verdict checks the
final database state and the count of requests the handler reports as successful, not each reply.
Criteria use the held-out seeds 102 to 109 only.

Configurations of the replay ablation (`c2_ablation.py`), each run on the same programs in one job:

| Configuration | Transactions | Isolation | Retry |
| --- | --- | --- | --- |
| default | the handler's own | framework default (PostgreSQL READ COMMITTED; MySQL READ COMMITTED for Django, REPEATABLE READ for SQLAlchemy) | none |
| serializable retry | the handler's own | SERIALIZABLE | up to 10 retries of requests whose error reaches the client |
| request default | one per request | framework default | none |
| request serializable | one per request | SERIALIZABLE | none |
| request retry | one per request | SERIALIZABLE | up to 10 retries of requests whose error (cause chain) carries 40001, 40P01 or MySQL 1205; an abort the handler catches stays caught |
| request replay | one per request | SERIALIZABLE | as request retry, with the connection watched, so a caught abort still fails the request and replays it |

The request configurations use the request wrappers in `c0_harness.py` (Django's atomic block;
SQLAlchemy's outer connection transaction with savepoint sessions). The population is every
held-out, functional test-class program that violates at its default in C1 (949 on PostgreSQL, 921
on MySQL), grouped by its C1 serializable-retry verdict, plus every reference program.

## Code versions

Every output folder records in its `manifest.json` the SHA-256 of the files that wrote it. The
released sources have comments, docstrings and descriptive plan notes removed, with the code
unchanged (identical syntax trees and identical plan settings), and the manifests in the data
archive carry the hashes of these released files. `verify_hashes.py` maps each manifest hash to a
file. Two folders of the data were written by
earlier versions, shipped in `src/versions/`:

- `versions/c0/c0_harness.py` checked C0's programs (`agent-concurrency-c0-main/harness`).
  C1 later extended this file with MySQL and the request configurations; `reproduce_numbers.sh`
  uses the C0 version for the C0 report.
- `versions/c2-generation/c2_generate.py` wrote 53 of the 54 C2 generation folders. The current
  version, which wrote the remaining folder, adds two changes: a loop whose next request would
  exceed the model's input budget stops as if its round limit were reached, and complete folders
  are skipped. The paper counts the stopped loops as not passing.

Other recorded changes to the plan: the PostgreSQL E3 measurement was repeated one shard at a time
because the first measurement (folder `load-postgresql`, kept in the data, report
`agent-concurrency-c2-main/c2-report.json`) had noisy repetitions; the repeat (`load-postgresql-2`,
median coefficient of variation 0.011) is the one the paper reports, and it leaves H8 to H12
unchanged (`results/c2-report.json`).

## Data

`agent-concurrency-vldb-data/` holds four folders, with the layout the scripts expect:

| Folder | Contents |
| --- | --- |
| `agent-concurrency-c0-main` | C0 generations (`generations/<model>/generations/*.json`), checks (`harness/results`), references |
| `agent-concurrency-c1-main` | C1 generations (single shot and loops, with `journal.jsonl` of every model answer), checks per engine (`harness-<engine>/results/*.json`, the checked programs under `implementations/`), sweeps, `c1-report.json` |
| `agent-concurrency-c2-main` | C2 generations (including each loop's `checker/` records and the self-written tests), E1 and E2 checks, E3 load shards, reports |
| `agent-concurrency-c2-diagnostics` | `ablation/<engine>/results/*.json` (every program under all six configurations, with per-trial times, successes and retries), `savepoint-trace/trace-<engine>.json`, `devstral-mysql/` |

The folder `agent-concurrency-c2-main/generations/qwen38_27b-loop_self-ctfd` was interrupted
and resumed with the current `c2_generate.py`; `manifest-first.json` is the manifest of its first
part. Each generation record keeps the program, the model's output, the seed, the sampling settings
and, for loops, the outcome of every round; the feedback text of a round is produced from that
outcome by `c1_feedback.py`, `c2_apps.py` or `c2_selftest.py`. Each check result keeps the
functional test outcome and, per configuration, the trials, violating trials, successes,
seconds, retries and exceptions.

Left out of the archive (listed with sizes in `EXCLUDED.tsv`): database server logs (about 18 GB),
scheduler logs and Python caches. File paths inside the results are those of the cluster that
ran them; the scripts resolve every input relative to the folders above.

Notes on the diagnostics:

- In the MySQL savepoint trace, the `information_schema.innodb_trx` query returned no rows, so the
  trace does not show transaction identifiers. That the two sessions keep one open transaction
  across the rollback rests on the control variant `check_then_savepoint_rollback`, whose locks,
  taken before the savepoint, remain after it.
- Throughput in the sustained-load tables counts completed requests. A completed request is one
  the handler reported as successful under a correct final state; it does not certify that each
  business operation took place as the client intended.

## License

Code: MIT License (`LICENSE`). Data and prompts: CC BY 4.0. The CTFd and django-oscar excerpts in
the application prompts are quoted from those projects under their licenses (Apache 2.0 and
BSD 3-Clause).
