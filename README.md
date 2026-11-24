# Isolation anomalies in LLM-written database code: artifact

This repository holds the code, prompts, derived results and instructions for the PVLDB
Experiment, Analysis & Benchmark paper *Passing Sequential Tests, Broken Under Load: Isolation
Anomalies in LLM-Written Database Code*. The raw results (generated programs, model answers,
loop records and every concurrency check) are in a separate data archive,
`agent-concurrency-vldb-data.tar.gz`, on Zenodo:
[doi:10.5281/zenodo.23998871](https://doi.org/10.5281/zenodo.23998871) (see [Data](#data)).

The study generates request handlers with six open coding models, checks each handler for
functional correctness, then runs it under concurrent requests on PostgreSQL 16.15 and MySQL 9.7.4
in up to six isolation and retry configurations. It has three parts, which name the files below:

- **base**: the eight benchmark tasks, the check harness, and the single-shot answers of the two
  Qwen coders on PostgreSQL;
- **benchmark**: four further models, MySQL, the sequential-test and concurrency-test repair
  loops, request retry and request replay, and the contention sweep (criteria H1 to H7);
- **extended**: the self-test and MySQL-checked loops, rules from two real applications
  (django-oscar and CTFd), and sustained load (criteria H8 to H12).

The replay ablation and the savepoint diagnostics are descriptive runs on the benchmark programs.

## Reproducing the paper's results

Every number, criterion verdict and figure in the paper is recomputed from the data archive in a
few minutes, without a database or GPU:

```bash
bash scripts/fetch_data.sh data                      # download, unpack, check SHA256SUMS
DATA=$PWD/data/agent-concurrency-vldb-data
python3 scripts/verify_hashes.py "$DATA"/{base,benchmark,extended}
pip install matplotlib==3.9.4                        # optional, for the figures
bash scripts/reproduce_numbers.sh "$DATA" out/numbers
```

`verify_hashes.py` hashes `src/` and compares it with the code hashes recorded in every manifest
of the released runs, so the data are tied to this code. `reproduce_numbers.sh` recomputes the
reports and numbers below from the raw check results, compares each with `results/` (printing
`identical` per file), and draws Figures 4 to 7 into `out/numbers/figures/` when matplotlib is
available (`PYTHON=... bash scripts/reproduce_numbers.sh` selects the interpreter).

| File in `results/` | Script | Contents |
| --- | --- | --- |
| `report-base.json`, `report-base.implementations.json` | `src/earlier/harness.py report` | per-program verdicts of the two Qwen coders' single-shot answers |
| `report-benchmark.json` | `src/checks.py report` | criteria H1 to H7 |
| `numbers-benchmark.json` | `src/numbers_benchmark.py` | RQ1 to RQ5: prevalence, loop modes and transitions, engine pairs, remedies, sweep, code patterns |
| `report-extended.json` | `src/report_extended.py report` | criteria H8 to H12 |
| `numbers-extended.json` | `src/numbers_extended.py` | self-test and MySQL-checked loops, applications, sustained load, supplementary text numbers |
| `numbers-breakdowns.json` | `src/numbers_breakdowns.py` | breakdowns and sensitivity checks in the text (framework, without the Qwen coders, without `username_signup`, control task, p99 latency) |
| `report-replay-ablation.json` | `src/replay_ablation.py report` | the six configurations of the replay ablation |

### Paper map

| Paper | Produced by | Read from |
| --- | --- | --- |
| Table 1 (tasks), Figure 2 (prompt), Section 3 | `src/tasks.py` | `prompts/benchmark/` |
| Table 2 (application rules) | `src/apps.py` | `prompts/apps/` |
| Table 3 (models), Table 4 (configurations) | plans | `src/plan-benchmark.json` (`models`, `engines`) |
| Table 5 (criteria) | `checks.py report`, `report_extended.py report` | `report-benchmark.json` (H1 to H7), `report-extended.json` (H8 to H12) |
| Reference validation (Section 3) | `checks.py run`, `app_harness.py gates` | `numbers-benchmark.json` (`reference_gates`) |
| Figure 4, RQ1 | `numbers_benchmark.py` | `numbers-benchmark.json` (`prevalence`) |
| Table 6, Figure 5, RQ2 | `numbers_benchmark.py`, `numbers_extended.py` | `modes`, `transitions`; `e1_*` |
| Tables 7 and 8, RQ3 | `numbers_benchmark.py`, `numbers_extended.py` | `engine_pairs`, `engine_mysql_only`, `mysql_rr_rc`; `e1_my_*` |
| Table 9, Figure 6, RQ4 | `numbers_benchmark.py`, `replay_ablation.py report` | `remedies`, `retry_categories`, `replay_failures`; `report-replay-ablation.json` |
| Figure 7, Table 10, RQ5 | `numbers_benchmark.py`, `numbers_extended.py` | `sweep`; `e3_tables`, `e3_spread` |
| Table 11, RQ6 | `numbers_extended.py` | `e2_*` |
| Section 5.2 (code patterns) | `numbers_benchmark.py` | `code_patterns` |
| Supplementary numbers in the text (yields, clustered intervals, hang-only verdicts, absolute throughput) | `numbers_extended.py` | `supplementary_*` |
| Breakdowns and sensitivity checks in the text | `numbers_breakdowns.py` | `numbers-breakdowns.json` |
| MySQL savepoint behaviour (RQ4) | `savepoint_diagnosis.py`, `savepoint_trace.py` | data: `diagnostics/savepoint-programs/`, `diagnostics/savepoint-trace/` |

Figure 1 is a single-shot Qwen2.5-Coder-32B program for `coupon_redeem` (Django, hint prompt),
stored in `base/generations/qwen25coder32b/` of the data archive.

## Contents

| Path | Contents |
| --- | --- |
| `src/` | The code that produced the data, flat because the modules import each other. `SHA256SUMS` lists every file. |
| `src/plan-base.json`, `plan-benchmark.json`, `plan-extended.json` | Plans read by the code: tasks, models with revisions and sampling settings, configurations, trial counts, held-out seeds, software pins, and the criteria H1 to H12 with their thresholds. |
| `src/earlier/` | Two earlier versions of files that wrote part of the data (see [Code versions](#code-versions)). |
| `prompts/` | Every first-turn prompt: 32 benchmark prompts, 6 application prompts, and the self-test loop's request for a test (`scripts/export_prompts.py` writes them from `src/`). |
| `results/` | The derived files the paper reads (table above). |
| `analysis/make_figures.py` | Figures 4 to 7 from `numbers-benchmark.json` and `numbers-extended.json`. |
| `scripts/` | Data download, hash check, number reproduction, environment setup, smoke test, prompt export. |
| `docs/environment/` | Package lists of the environments used (conda list and pip freeze). |

| Source file | Role |
| --- | --- |
| `tasks.py`, `harness.py` | Benchmark tasks with reference programs; the concurrency check of one program in one configuration |
| `checks.py`, `mysql_server.py` | Check runs, contention sweep and criteria report over both engines; private MySQL servers |
| `generate_base.py`, `generate.py`, `generate_extended.py` | Generation: base single shot; benchmark single shot and repair loops; application, self-test and MySQL-checked loops |
| `loop_feedback.py`, `loop_checker.py`, `selftest.py` | Feedback of the repair loops; the loops' checker; the self-test loop |
| `apps.py`, `app_harness.py` | Application rules (django-oscar, CTFd) and their checks |
| `sustained_load.py` | Sustained-load measurement |
| `numbers_benchmark.py`, `report_extended.py`, `numbers_extended.py`, `numbers_breakdowns.py` | Reports and paper numbers |
| `replay_ablation.py`, `savepoint_diagnosis.py`, `savepoint_trace.py` | Diagnostics |

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
| oscar | the check environment plus django-oscar 4.2.1 and the pins in `plan-extended.json` |
| ctfd | CTFd 3.8.9 (commit `5f345fb`) with its pinned requirements and psycopg2-binary 2.9.10 |
| gen | Python 3.11, torch 2.14.1 (CUDA 12.6), transformers 5.20.2, accelerate 1.15.0, kernels 0.17.2, mistral-common 1.12.0, huggingface-hub 2.2.0, safetensors 0.8.0 |

The base generation runs (the Qwen coders' single-shot answers) used torch 2.6.0+cu124,
transformers 4.57.1 and an input limit of 8,192 tokens (`plan-base.json`), as each generation
folder's `backend.json` records; their repair loops used the gen environment.

## Smoke test of the checks

About 40 minutes on 16 cores:

```bash
bash scripts/setup_env.sh env check        # about 10 minutes
bash scripts/setup_env.sh env apps         # optional: django-oscar and CTFd
bash scripts/smoke_test.sh env out/smoke "$DATA"
```

It runs the 32 benchmark references on both engines (every correct reference stays clean and every
buggy reference violates at the framework default), re-checks eight released programs on
PostgreSQL under all five configurations and compares their verdicts with the released ones, and,
if the application environments exist, runs the application references on both engines.

## Full rerun

All commands run from the repository root with `SRC=src`, `ENV=env` (from `setup_env.sh`),
`OUT` a new output folder and `TMP` a scratch directory. Each command resumes if it is rerun with
the same output folder. Times are those of the study on 16 cores unless stated.

### Models

Download each model in `src/plan-benchmark.json` (`models`) at its pinned `revision` into one
cache, and set `HF_HUB_CACHE` to it:

```bash
env/gen/bin/python -I -c 'import json
from huggingface_hub import snapshot_download
for m in json.load(open("src/plan-benchmark.json"))["models"].values():
    print(snapshot_download(m["model_id"], revision=m["revision"]))'
```

### Generation (GPU)

```bash
# base: the two Qwen coders, single shot
python -I $SRC/generate_base.py --model qwen25coder32b --output $OUT/base/generations
# benchmark: single shot for the other four models, then both loops for all six
python -I $SRC/generate.py single --model qwen38_27b --seeds main --output $OUT/benchmark/generations
python -I $SRC/generate.py loop --model qwen38_27b --mode loop_seq --seeds main \
    --start $OUT/benchmark/generations/qwen38_27b --output $OUT/benchmark/generations \
    --checker-python $ENV/check/bin/python --pg-bin $ENV/check/bin --scratch $TMP
# extended: application single shot and loops, self-test loop, MySQL-checked loop
python -I $SRC/generate_extended.py single --model qwen38_27b --seeds main --output $OUT/extended/generations
python -I $SRC/generate_extended.py loop --model qwen38_27b --suite apps --app oscar --mode loop_self \
    --seeds main --start $OUT/extended/generations/qwen38_27b --output $OUT/extended/generations \
    --checker-python $ENV/oscar/bin/python --bin $ENV/check/bin --ctfd-source $ENV/ctfd-src --scratch $TMP
python -I $SRC/generate_extended.py loop --model qwen38_27b --mode loop_conc_my --seeds main \
    --start $OUT/benchmark/generations/qwen38_27b --output $OUT/extended/generations \
    --checker-python $ENV/check/bin/python --bin $ENV/check/bin --scratch $TMP
```

`python` is `$ENV/gen/bin/python`. Repeat for each model (`qwen25coder32b`, `qwen3coder30b`,
`qwen38_27b`, `devstral2_24b`, `gptoss120b`, `seedcoder8b`), loop mode (`loop_seq`, `loop_conc`;
in the extended part also `loop_self` on each application and on the benchmark, and
`loop_conc_my`) and application (`oscar` with the oscar checker, `ctfd` with the ctfd checker).
The loops of the Qwen coders start from their base folders. Each single-shot or loop run took up
to 12 hours on one H100. Sampling is seeded, but GPU kernels are not bitwise deterministic, so
regenerated answers can differ from the released ones; the checks below can run on the released
programs instead.

### Checks (CPU)

```bash
P=$ENV/check/bin/python; B=$ENV/check/bin
# benchmark: every benchmark generation folder plus the Qwen coders' base folders, on each engine
$P -I $SRC/checks.py run --engine postgresql --output $OUT/benchmark/harness-postgresql --bin $B \
    --scratch $TMP --generations GENERATION_FOLDERS...
$P -I $SRC/checks.py summary --output $OUT/benchmark/harness-postgresql \
    --save $OUT/benchmark/harness-postgresql-summary.json
# contention sweep: levels w2, w4, w8, w16, w8-spread50ms on each engine
$P -I $SRC/checks.py sweep --engine postgresql --level w8 --run $OUT/benchmark/harness-postgresql \
    --output $OUT/benchmark/sweep-postgresql-w8 --bin $B --scratch $TMP
$P -I $SRC/checks.py report --runs $OUT/benchmark/harness-postgresql $OUT/benchmark/harness-mysql \
    --sweeps $OUT/benchmark/sweep-*/ --save $OUT/benchmark/report-benchmark.json
# extended E1: self-test and MySQL-checked loop answers, on each engine
$P -I $SRC/checks.py run --engine mysql --output $OUT/extended/harness-e1-mysql --bin $B --scratch $TMP \
    --generations $OUT/extended/generations/*-loop_self $OUT/extended/generations/*-loop_conc_my
# extended E2: application programs (single shot and that application's loops), on each engine
$ENV/oscar/bin/python -I $SRC/app_harness.py run --app oscar --engine postgresql \
    --output $OUT/extended/harness-e2-oscar-postgresql --bin $B --scratch $TMP \
    --ctfd-source $ENV/ctfd-src --generations APPLICATION_FOLDERS...
# extended E3: sustained load, 20 shards per engine on 40 cores each
$P -I $SRC/sustained_load.py run --engine postgresql --run $OUT/benchmark/harness-postgresql \
    --output $OUT/extended/load-postgresql/shard-00 --bin $B --scratch $TMP --shards 20 --shard 0
$P -I $SRC/report_extended.py report --extended $OUT/extended --benchmark $OUT/benchmark \
    --save $OUT/extended/report-extended.json
```

| Step | Items (programs and references) | Time per engine (PostgreSQL / MySQL) |
| --- | --- | --- |
| Base checks | 672 | 0.5 h (PostgreSQL only) |
| Benchmark checks | 2,592 | 11.0 h / 3.6 h |
| Benchmark sweep, per level | 1,237 / 1,177 | 0.4 to 1.2 h |
| Extended E1 checks | 1,568 | 8.1 h / 3.5 h |
| Extended E2 checks, per application | 484 | 2.8 to 4.7 h / 1.3 to 2.2 h |
| Extended E3, per shard | 20 shards | 1.7 to 1.9 h on 40 cores |
| Replay ablation, per shard | 4 shards | 0.4 to 0.7 h |

The released E3 measurement on PostgreSQL ran its 20 shards one after another on nodes of the
same type; run the shards the same way for comparable throughput.

### Diagnostics

```bash
$P -I $SRC/replay_ablation.py run --engine postgresql --bin $B --benchmark $OUT/benchmark \
    --output $OUT/replay-ablation --shard 0 --shards 4 --scratch $TMP
$P -I $SRC/replay_ablation.py report --benchmark $OUT/benchmark --output $OUT/replay-ablation \
    --save $OUT/report-replay-ablation.json
$P -I $SRC/savepoint_trace.py --engine mysql --bin $B --output $OUT/savepoint-trace --scratch $TMP
$P -I $SRC/savepoint_diagnosis.py savepoint --engine mysql --bin $B --output $OUT/savepoint-programs --scratch $TMP
$P -I $SRC/savepoint_diagnosis.py program --bin $B --benchmark $OUT/benchmark \
    --output $OUT/savepoint-programs --trials 5 --scratch $TMP
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

Configurations of the replay ablation (`replay_ablation.py`), each run on the same programs in one
job:

| Configuration | Transactions | Isolation | Retry |
| --- | --- | --- | --- |
| default | the handler's own | framework default (PostgreSQL READ COMMITTED; MySQL READ COMMITTED for Django, REPEATABLE READ for SQLAlchemy) | none |
| serializable retry | the handler's own | SERIALIZABLE | up to 10 retries of requests whose error reaches the client |
| request default | one per request | framework default | none |
| request serializable | one per request | SERIALIZABLE | none |
| request retry | one per request | SERIALIZABLE | up to 10 retries of requests whose error (cause chain) carries 40001, 40P01 or MySQL 1205; an abort the handler catches stays caught |
| request replay | one per request | SERIALIZABLE | as request retry, with the connection watched, so a caught abort still fails the request and replays it |

The request configurations use the request wrappers in `harness.py` (Django's atomic block;
SQLAlchemy's outer connection transaction with savepoint sessions). The population is every
held-out, functional test-class program that violates at its default in the benchmark checks
(949 on PostgreSQL, 921 on MySQL), grouped by its serializable-retry verdict, plus every
reference program.

## Code versions

Every output folder records in its `manifest.json` the SHA-256 of the files that wrote it, and
these hashes are those of the files in `src/`; `verify_hashes.py` maps each manifest hash to a
file. Two folders of the data were written by earlier versions, shipped in `src/earlier/`:

- `earlier/harness.py` checked the base programs (`base/harness`). The benchmark part later
  extended this file with MySQL and the request configurations; `reproduce_numbers.sh` uses the
  earlier version for the base report.
- `earlier/generate_extended.py` wrote 53 of the 54 extended generation folders. The current
  version, which wrote the remaining folder, adds two changes: a loop whose next request would
  exceed the model's input budget stops as if its round limit were reached, and complete folders
  are skipped. The paper counts the stopped loops as not passing.

The PostgreSQL E3 measurement was repeated one shard at a time because the first measurement had
noisy repetitions. The repeat (`extended/load-postgresql`, median coefficient of variation 0.011)
is the one the paper reports. The first measurement is kept (`extended/load-postgresql-first`,
with its report `extended/report-extended-first-load.json`); it leaves H8 to H12 unchanged
(`report_extended.py report --load postgresql=load-postgresql-first` reads it).

## Data

The archive ([doi:10.5281/zenodo.23998871](https://doi.org/10.5281/zenodo.23998871), 19.8 MB,
CC BY 4.0) unpacks to `agent-concurrency-vldb-data/`, with the layout the scripts expect:

| Folder | Contents |
| --- | --- |
| `base/` | The Qwen coders' single-shot generations (`generations/<model>/generations/*.json`), their checks (`harness/results`), references |
| `benchmark/` | Generations (single shot and loops, with `journal.jsonl` of every model answer), checks per engine (`harness-<engine>/results/*.json`, the checked programs under `implementations/`), sweeps, `report-benchmark.json` |
| `extended/` | Generations (including each loop's `checker/` records and the self-written tests), E1 and E2 checks, E3 load shards, reports |
| `diagnostics/` | `replay-ablation/<engine>/results/*.json` (every program under all six configurations, with per-trial times, successes and retries), `savepoint-trace/trace-<engine>.json`, `savepoint-programs/` |

The folder `extended/generations/qwen38_27b-loop_self-ctfd` was interrupted and resumed with the
current `generate_extended.py`; `manifest-first.json` is the manifest of its first part. Each
generation record keeps the program, the model's output, the seed, the sampling settings and, for
loops, the outcome of every round; the feedback text of a round is produced from that outcome by
`loop_feedback.py`, `apps.py` or `selftest.py`. Each check result keeps the functional test
outcome and, per configuration, the trials, violating trials, successes, seconds, retries and
exceptions.

Left out of the archive (listed with sizes in `EXCLUDED.tsv`): database server logs (about 18 GB),
scheduler logs and Python caches. File paths and error messages inside the results are those
recorded on the cluster that ran them, under the folder names given in `RENAMES.tsv`; the scripts
resolve every input relative to the folders above.

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
