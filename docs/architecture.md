# Architecture

The app walks a practitioner through Algorithm 1 of the paper,
hypothesis-driven HARD benchmarking (section 9). Given a workload and
candidate configurations, it ends with a configuration decision and the
recorded evidence for it.

The paper's case study (Louvain, label propagation and PageRank on five
SNAP graphs) is the demo session. The first version runs only on the local
machine, so the demo replicates the case study's CPU configurations on it.
GPU and Xeon configurations wait for remote runners. The app does not
import results from other machines.

The benchmark code is gaib, from
[nicoleschneider/HARD-graph-benchmark](https://github.com/nicoleschneider/HARD-graph-benchmark).
The app calls it and does not reimplement the timed path.

## Contents

1. [Importing gaib](#1-importing-gaib)
2. [Components](#2-components)
3. [Registry](#3-registry)
4. [Runner interface](#4-runner-interface)
5. [Hypothesis suggester interface](#5-hypothesis-suggester-interface)
6. [Session storage](#6-session-storage)
7. [API](#7-api)
8. [Run scheduling](#8-run-scheduling)
9. [Workflow steps](#9-workflow-steps)
10. [Open questions](#10-open-questions)

## 1. Importing gaib

### What gaib is, as a dependency

- Flat modules in `gaib/` (`runner.py`, `experiment.py`,
  `iterations/representation_test.py`), imported as `from experiment import
  ...`. There is no `pyproject.toml` and no package.
- Paths are relative to the working directory. `Experiment()` creates
  `../datasets` and `../temp_results`; `Task` writes `./cache`; the runner
  deletes and recreates `./temp_workspace` after every run.
- `run_experiment` calls `exit()` on a dataset hash mismatch.
- The two timed entry points are `Runner.run_all_experiments` (NetworkX
  loading, all CPU and original GPU rows) and the `__main__` block of
  `iterations/representation_test.py` (cudf loading rows). The second is not
  a function, so it can only be run as a script.
- Its environment is pinned by three lock files: `requirements-lock-cpu.txt`,
  `requirements-lock.txt` (GPU) and `requirements-lock-gpu-cudf.txt`.

### Options

| Option | Pins the code | Needs a gaib change | Timed path identical to the paper |
|---|---|---|---|
| Git submodule at a fixed commit, run by path | yes, commit in this repo's tree | no | yes |
| Editable install (`pip install -e`) | no, follows the checkout | yes, a `pyproject.toml` | yes, if the checkout is right |
| `pip install git+...@commit` | yes | yes, a `pyproject.toml` | yes |
| Copy the modules in | yes | no | drifts on the first edit |

### Recommendation: submodule, run in a separate benchmark environment

1. Add HARD-graph-benchmark as a submodule at `vendor/HARD-graph-benchmark`,
   pinned to `dbb9903` (main, 2026-10-25). The URL is the fork,
   `https://github.com/nicoleschneider/HARD-graph-benchmark.git`, never
   `osullik/umd_thesis`.
2. Do not install it. A worker process runs with `PYTHONPATH` set to
   `vendor/HARD-graph-benchmark/gaib` and its working directory set to a
   per-job scratch folder. gaib's relative paths then land inside that
   folder, and an `exit()` ends the worker, not the server.
3. The worker runs in a benchmark environment built from gaib's own lock
   file for that hardware, not in the backend's environment. This is the
   environment the paper's numbers came from.
4. The worker calls gaib exactly as its command lines do:
   - NetworkX loading: write gaib definition JSONs into the job folder, then
     `Runner(...).run_all_experiments(definitions, repetitions, base_seed)`.
     This keeps gaib's warm-up on the karate club graph, its run order, the
     seed rule (base seed + run index), its timing points and its scoring.
   - cudf loading: run `iterations/representation_test.py` as a script with
     its flags (`--task`, `--dtype`, `--undirected`, `--repetitions`,
     `--base-seed`, `--results-csv`, `--hardware`, `--manifest`,
     `--pagerank-reference-directory`). This keeps its 3-node warm-up, its
     `cp.cuda.Device().synchronize()` calls and its cross-path validity CSVs.
5. The backend imports gaib only for constants and scoring it reuses outside
   the timed path: `runner.RESULTS_COLUMNS`, `Task.PAGERANK_ALPHA` and
   `experiment.Evaluator`. For that it pins gaib's import dependencies at the
   lock file versions (networkx 3.2.1, scikit-learn 1.9.1, tqdm 4.66.2,
   numpy 2.4.6, pandas 3.0.6).
6. The environment snapshot of every job records the submodule commit, a
   dirty flag for the submodule tree, and the hash of the lock file used.

Why not editable: it needs a packaging change to gaib, it pins nothing on
its own, and it would install modules named `runner` and `experiment` into
the backend's import namespace. Why a subprocess at all: gaib's relative
paths and `exit()` calls need an isolated working directory and process, and
a separate process keeps the server's threads and garbage collector out of
the timed window.

Changing the pin is a commit in this repo. Upgrading gaib is a deliberate
step, and sessions record which commit produced each result.

### Equivalence check

A test runs one small definition (email-Eu-core, `NX_LOUVAIN`, 3 runs) two
ways: through the app's LocalRunner and through `python runner.py` directly,
in the same environment. Every non-timing column must be equal (seed,
vertices, edges, communities, NMI, modularity). Timing columns must have the
same set of keys and comparable medians.

### Measurement definitions inherited from gaib

These come from gaib and its analysis notebook. The app uses them unchanged.

- End-to-end time is `etl_s + processing_s`. `total_s` is stored but not
  used for comparisons: in `runner.py` it also includes the file copy and
  re-hash; in `representation_test.py` it does not.
- On the original GPU path, `processing_s` includes nx-cugraph's conversion
  of the NetworkX graph. On cudf paths that conversion is in ETL. Processing
  times are therefore not comparable across representations; end-to-end
  times are.
- Summaries are medians over runs. Speedups are ratios of medians with a
  95% bootstrap interval (10,000 resamples, seed 123).
- A run is flagged by the sleep check if its unaccounted wall-clock gap
  exceeds both 10 s and 10% of its accounted time.

## 2. Components

```
frontend/  React + TypeScript (Vite). Renders steps, forms and tables from API data.
backend/   FastAPI. Registry, sessions, job queue, analysis, suggesters.
  worker   Runs inside the benchmark environment. Calls gaib. Stdlib only.
vendor/HARD-graph-benchmark   gaib, pinned submodule.
data/      Shared datasets (gaib layout and manifest) and PageRank reference cache.
sessions/  One folder per session.
```

The backend never runs a benchmark in its own process. The worker never
talks to the API; it writes files and the backend reads them.

## 3. Registry

Every extensible concept is a registry entry: a YAML file validated by a
Pydantic model. Built-in entries live in `backend/registry/<kind>/`. A
configurable extra directory holds a user's own entries, with the same
layout. The UI lists, filters and renders entries from the API. Adding an
entry needs no frontend change.

Entries that need code (a driver, a metric formula, a suggestion rule) name
it by import path, for example `egb.metrics.ratio_of_medians`. Entries that
take parameters declare them as JSON Schema; the frontend renders a form
from the schema.

The backend checks compatibility, not the UI. `POST /api/configurations/validate`
answers whether an algorithm, implementation, representation, dataset and
hardware combination can run, and if not, why.

### Kinds

| Kind | Declares | Paper entries |
|---|---|---|
| `task` | Problem being solved, its output type, metrics that apply | `community-detection`, `node-ranking` |
| `algorithm` | Task, parameters, required graph orientation | `louvain`, `label-propagation`, `pagerank` |
| `implementation` | Algorithm, library, backend, hardware requirements, driver | `networkx`, `nx-cugraph` per algorithm |
| `representation` | Loading path, in-memory structure, precision, where graph construction is timed | `networkx-text`, `cudf-float32`, `cudf-float64` |
| `dataset` | Files, hashes, size, orientation, ground truth, source URL | the five SNAP graphs |
| `metric` | Column or formula, unit, direction, aggregation, applicable tasks | see below |
| `hardware` | Label, runner, benchmark environment, detected specs, capabilities | the local machine |
| `runner` | Runner class and its settings schema | `local`, later `ssh`, `slurm` |
| `suggester` | Suggester class and its settings | `rules`, later `agent` |

A configuration, the unit that gets a chart color, is the tuple
(hardware, implementation, representation). An algorithm is part of the
implementation entry, so label propagation versus Louvain is also a
configuration change.

### Paper case study mapped onto the registry

Tasks and algorithms:

| Task | Algorithm | gaib parameters | Orientation |
|---|---|---|---|
| community-detection | louvain | `weight=None, resolution=1, threshold=1e-7, seed` | undirected |
| community-detection | label-propagation | `asyn_lpa_communities(weight=None, seed)` | undirected |
| node-ranking | pagerank | `alpha=0.85` (`Task.PAGERANK_ALPHA`) | dataset's own |

Representations:

| Representation | Loading path | Precision | Graph construction timed in |
|---|---|---|---|
| `networkx-text` | `Experiment.etl`: `nx.read_edgelist` for SNAP files | float64 | ETL on CPU; nx-cugraph conversion falls in processing on GPU |
| `cudf-float32` | `cudf.read_csv` then `nxcg.from_pandas_edgelist`, no weight column | float32 (nx-cugraph default) | ETL |
| `cudf-float64` | same, with a float64 weight column of 1.0 | float64 | ETL |

Louvain runs unweighted, so precision does not apply to it;
`representation_test.py` rejects `--dtype float64` with Louvain. The
registry marks `cudf-float64` as PageRank only.

Implementation and representation to gaib driver and `task` value:

| Algorithm | Implementation | Representation | Driver | gaib `task` |
|---|---|---|---|---|
| louvain | networkx | networkx-text | `gaib-runner` | `NX_LOUVAIN` |
| louvain | nx-cugraph | networkx-text | `gaib-runner`, `GPU: true` | `NX_LOUVAIN_GPU` |
| louvain | nx-cugraph | cudf-float32 | `gaib-representation-test --task louvain` | `NX_LOUVAIN_GPU_CUDF` |
| label-propagation | networkx | networkx-text | `gaib-runner` | `NX_LPA` |
| pagerank | networkx | networkx-text | `gaib-runner` | `NX_PAGERANK` |
| pagerank | nx-cugraph | networkx-text | `gaib-runner`, `GPU: true` | `NX_PAGERANK_GPU` |
| pagerank | nx-cugraph | cudf-float32 | `gaib-representation-test --dtype float32` | `NX_PAGERANK_GPU_CUDF` |
| pagerank | nx-cugraph | cudf-float64 | `gaib-representation-test --dtype float64` | `NX_PAGERANK_GPU_CUDF_F64` |

Datasets come from gaib's `datasets/manifest.json` and `snap_datasets.json`:
email-Eu-core (with department ground truth), wiki-topcats, web-Stanford,
com-Amazon (undirected) and p2p-Gnutella31. The registry stores URL,
SHA-256, vertex and edge counts, orientation and ground-truth file. A user
dataset entry is a local file path plus its hash, computed at registration.

Metrics:

| Metric | Source | Unit | Better | Applies to |
|---|---|---|---|---|
| `etl_s` | column | s | lower | all |
| `processing_s` | column | s | lower | all |
| `end_to_end_s` | `etl_s + processing_s` | s | lower | all |
| `etl_share` | `etl_s / end_to_end_s` | % | none | all |
| `speedup` | ratio of medians, bootstrap 95% interval | × | higher | all, against a baseline |
| `modularity` | column | none | higher | community-detection |
| `nmi` | column, needs ground truth | none | higher | community-detection |
| `predicted_communities` | column | count | none | community-detection |
| `nmi_between_paths` | `cross_path_louvain.csv` | none | higher | validity check |
| `pagerank_l1` | column, against the reference | none | lower | node-ranking |
| `pagerank_top100` | column | fraction | higher | node-ranking |
| `l1_vs_original_path` | `cross_path_l1.csv` | none | lower | validity check |
| `unaccounted_s` | sleep check | s | lower | run validity |

Metric formulas the notebook defines (end-to-end, ETL share, ratio of
medians, sleep check) are ported into `egb.metrics`, with tests that
reproduce the notebook's numbers from the committed results.

Hardware:

The first version has one hardware entry: the machine the backend runs on.
Nothing about it is assumed from the gaib work. On startup the backend
detects its specs (chip or CPU model, core counts, memory, OS and version,
GPUs with driver and CUDA version when `nvidia-smi` exists) and derives its
capabilities from them: `cpu` always, `cuda` and `cudf` only when a CUDA GPU
and the cudf packages are present. The hardware label written to the
results CSV defaults to a slug built from the detected model, chip and
memory, and the practitioner can rename it. On the Mac this machine is
CPU-only, so the `nx-cugraph` implementations and both cudf
representations show as unavailable, with the missing capability as the
reason.

The benchmark environment for the local machine is built from
`requirements-lock-cpu.txt`; a CUDA machine uses `requirements-lock.txt` or
`requirements-lock-gpu-cudf.txt`. Remote hardware entries are added with
the remote runners, with specs detected on the remote machine the same way.

Paper configurations runnable on a CPU-only machine:

| Algorithm | Implementation | Representation | gaib `task` |
|---|---|---|---|
| louvain | networkx | networkx-text | `NX_LOUVAIN` |
| label-propagation | networkx | networkx-text | `NX_LPA` |
| pagerank | networkx | networkx-text | `NX_PAGERANK` |

## 4. Runner interface

```python
class Runner(Protocol):
    id: str

    def describe(self) -> RunnerInfo: ...
    def capabilities(self, hardware: HardwareEntry) -> set[str]: ...
    async def snapshot(self, hardware: HardwareEntry) -> EnvironmentSnapshot: ...
    async def start(self, job: JobSpec, job_dir: Path) -> None: ...
    def events(self, job_id: str) -> AsyncIterator[JobEvent]: ...
    async def cancel(self, job_id: str) -> None: ...
```

- `JobSpec` is fully resolved before it reaches a runner: driver, gaib
  definitions or script flags, dataset paths, repetitions, base seed,
  hardware label, benchmark environment. A runner does not consult the
  registry.
- `JobEvent` is one of `started`, `warm_up`, `run_completed` (with the new
  results row), `log` (a stdout line), `finished`, `failed` (exit code and
  last log lines), `cancelled`.
- A runner writes the job folder described in section 6 and nothing outside
  it.

**LocalRunner.** Starts the worker with the hardware entry's interpreter,
`PYTHONPATH` set to gaib, and the working directory set to
`jobs/<job_id>/work/cwd`. It emits `run_completed` by watching the results
CSV, which gaib appends to and fsyncs once per run. It parses stdout only
for `log` events. Cancelling sends SIGTERM to the process group; partial
rows stay and the job is marked cancelled.

**RemoteRunner (later).** Same interface. The SSH variant copies the job
folder to a remote scratch directory, runs the same worker in a remote
benchmark environment built from the same lock file, streams stdout, and
copies the job folder back. The Slurm variant submits the worker with
`sbatch`, as `gaib/hard-bench.sh` does, polls `squeue` and `sacct`, and
copies back on completion. Neither needs a UI change: hardware entries name
their runner, and the UI shows jobs, events and results the same way.

## 5. Hypothesis suggester interface

```python
class HypothesisSuggester(Protocol):
    id: str

    async def suggest(self, request: SuggestionRequest) -> list[Suggestion]: ...
```

`SuggestionRequest`:

- `observations`: computed facts, each with an id, kind, configurations,
  datasets, metric, value, unit and interval, and a one-line statement
  ("ETL: 87% of GPU time on web-Stanford").
- `alternatives`: registry entries compatible with the available hardware,
  so suggestions only propose runnable changes. On a CPU-only machine,
  representation swaps to cudf are not offered.
- `history`: the session's log, so a suggester does not repeat a tested
  hypothesis.
- `limit`.

`Suggestion`:

- `rank`, `score`, `source` (`rules:<rule id>` or `agent:<model>`)
- `hypothesis`: one sentence
- `observation_ids`: what it rests on
- `change`: the configuration change to run, as registry ids (from and to)
- `predictions`: one or more drafts, each with a sentence and a structured
  form the app can evaluate:
  `{metric, statistic, subject, relation, reference, datasets, tolerance}`
- `validity_checks`: drafts in the same form, for checks that only the
  intended factor changed
- `caveats`: known confounds, such as a precision change

Predictions are drafts until recorded. The practitioner edits the sentence
and the structured fields, then records it. After that the entry is
immutable; changes are new `revision` or `amendment` entries.

**Rule-based suggester (now).** Each rule is a registry entry with a
condition over observations and a template for the suggestion. The first
rules generalize the paper's three hypotheses. Unit tests feed each rule
hand-written observations shaped like the paper's and check the suggestion.
On a CPU-only machine only `processing-dominates` can fire, because the
others need a cudf representation:

| Rule | Condition | Suggested change |
|---|---|---|
| `loading-dominates` | ETL share of the slower configuration ≥ 50% and an alternative representation exists for its implementation | Swap the representation, predict end-to-end below the faster configuration's median |
| `size-crossover` | The faster configuration changes with dataset size | Same swap on the losing configuration, predict it wins on the smallest dataset |
| `processing-dominates` | Processing share ≥ 90% and another algorithm exists for the task | Swap the algorithm, predict end-to-end time and a quality drop |
| `precision-change` | A suggested change alters precision | Adds a caveat and a tolerance validity check |

Ranking: size of the gap the change could close, then estimated runtime
from earlier runs.

**Agent suggester (later).** Same interface, same output type, called by
the same endpoint. Suggestion requests are asynchronous from the start, so a
slow suggester needs no UI change. The prompt files live in
`backend/prompts/`.

## 6. Session storage

One folder per session, under a sessions directory set by a CLI option
(default `./sessions`).

```
sessions/<session_id>/
  session.json            id, name, decision question, created, app commit
  config.json             workload W (tasks, datasets), candidates C, exploratory
                          subset C0, metrics M, repetitions, base seed,
                          configuration colors
  config_history.jsonl    every earlier version of config.json, appended on change
  log.jsonl               append-only hypothesis log
  jobs.jsonl              append-only job records and status changes
  environment/
    <snapshot_hash>.json  one file per distinct environment
  jobs/<job_id>/
    spec.json             resolved JobSpec
    command.json          interpreter, argv, working directory, environment variables set
    definitions/*.json    gaib definitions, for gaib-runner jobs
    results.csv           paper schema (runner.RESULTS_COLUMNS)
    cross_path_l1.csv     representation-test PageRank jobs
    cross_path_louvain.csv  representation-test Louvain jobs
    reports/*.json        gaib's per-run JSON reports
    stdout.log
    work/                 gaib's working directory, deleted on success
```

Shared across sessions, under a data directory set by a CLI option:
`datasets/` in gaib's layout with its `manifest.json`, and
`pagerank_references/`. gaib keys the reference cache by dataset hash,
orientation, alpha, tolerance and iteration limit, so sharing it is safe.

### Results

Each job writes its own `results.csv`, so a row's configuration is known
from the job. The analysis layer reads all of them and joins them with
`jobs.jsonl`. The CSV columns are exactly `runner.RESULTS_COLUMNS`; the app
adds no columns. Rows written by `representation_test.py` leave the columns
it does not fill empty, as it already does.

### Hypothesis log

`log.jsonl` has one JSON object per line. The backend only appends. There is
no endpoint to edit or delete an entry.

```json
{
  "seq": 4,
  "id": "e4",
  "recorded_at": "2026-10-20T11:49:00-04:00",
  "iteration": 1,
  "kind": "amendment",
  "refers_to": ["e2"],
  "text": "cudf loading is forced to float64, then the original tolerance holds.",
  "prediction": null,
  "source": "user",
  "suggestion": null,
  "state_hash": "sha256:...",
  "prev_entry_hash": "sha256:...",
  "entry_hash": "sha256:..."
}
```

- `kind`: `observation`, `hypothesis`, `prediction`, `validity_check`,
  `revision`, `amendment`, `outcome`, `explanation`, `decision`. The first
  seven are the rows of the paper's hypothesis table; `explanation` and
  `decision` record steps 6 and 7 of Algorithm 1.
- `iteration`: which pass through steps 3 to 7 the entry belongs to.
- `prediction`: the structured form from section 5, for `prediction` and
  `validity_check` entries.
- `outcome` entries carry a status per referenced prediction: `met` (✓),
  `refuted` (✗) or `tie` (≈, the bootstrap interval contains 1), plus the
  computed values.
- `source` and `suggestion` record whether a suggester proposed the entry
  and how the practitioner edited it.
- `state_hash` is SHA-256 over a sorted manifest of
  `(relative path, SHA-256 of file)` for `config.json`, `config_history.jsonl`, every job's
  `results.csv` and cross-path CSVs, and every environment
  snapshot. It proves which results existed when the entry was recorded.
  A prediction whose state hash does not cover the iteration's results was
  recorded before them.
- `prev_entry_hash` chains entries. `entry_hash` covers every other field.
  `GET .../log/verify` recomputes the chain and the state hashes.
- A targeted experiment (step 5) must name a recorded hypothesis with at
  least one prediction. The backend rejects it otherwise. Exploratory jobs
  (step 2) need none.

### Environment snapshot

Taken by the worker at job start, inside the benchmark environment, so it
describes what actually ran. Fields: hardware label, hostname, OS and
version, CPU model and core counts, memory, GPU name, driver and CUDA
version from `nvidia-smi` when present, Python version, the package freeze,
the lock file name and hash, gaib's submodule commit and dirty flag, and
this app's commit. Snapshots are stored by content hash; a job references
its snapshot. A changed environment within a session shows up as a new
hash.

## 7. API

All routes are under `/api`. Request and response bodies are Pydantic
models. The frontend generates its TypeScript types from the OpenAPI
schema.

Registry:

| Method | Path | Purpose |
|---|---|---|
| GET | `/registry` | All entries, grouped by kind |
| GET | `/registry/{kind}` | Entries of one kind |
| GET | `/registry/{kind}/{id}` | One entry, with its parameter schema |
| POST | `/configurations/validate` | Can this combination run, and why not |

Hardware and queue:

| Method | Path | Purpose |
|---|---|---|
| GET | `/hardware` | Hardware entries with detected specs, capabilities and runner status |
| PATCH | `/hardware/{hid}` | Rename the hardware label |
| GET | `/queue` | Running job and queued jobs, across sessions |

Sessions:

| Method | Path | Purpose |
|---|---|---|
| GET | `/sessions` | List |
| POST | `/sessions` | Create, empty or from the demo workload |
| GET | `/sessions/{sid}` | Metadata, config and current step |
| PATCH | `/sessions/{sid}/config` | Change configuration; the old version goes to `config_history.jsonl` |
| GET | `/sessions/{sid}/steps` | Workflow steps with status |
| GET | `/sessions/{sid}/export` | Zip of the session folder |

Jobs:

| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions/{sid}/jobs` | Queue configurations × datasets; targeted jobs name a hypothesis |
| GET | `/sessions/{sid}/jobs` | Jobs with status |
| GET | `/jobs/{jid}` | One job, with its command and environment |
| GET | `/jobs/{jid}/events` | Server-sent events stream |
| DELETE | `/jobs/{jid}` | Cancel |

Results and analysis:

| Method | Path | Purpose |
|---|---|---|
| GET | `/sessions/{sid}/results` | Rows, JSON or CSV, joined with configuration ids |
| GET | `/sessions/{sid}/summary` | Medians and ranges per configuration and dataset |
| GET | `/sessions/{sid}/comparisons` | Speedups with intervals, given baseline and measure |
| GET | `/sessions/{sid}/validity` | Sleep check, cross-path checks, determinism |
| GET | `/sessions/{sid}/observations` | Computed observations |

Hypotheses:

| Method | Path | Purpose |
|---|---|---|
| POST | `/sessions/{sid}/suggestions` | Start a suggestion request; returns its id |
| GET | `/sessions/{sid}/suggestions/{rid}` | Status and ranked suggestions |
| GET | `/sessions/{sid}/log` | All entries |
| POST | `/sessions/{sid}/log` | Append an entry |
| POST | `/sessions/{sid}/log/{eid}/evaluate` | Compute outcome status for a prediction from results |
| GET | `/sessions/{sid}/log/verify` | Recompute hashes |
| GET | `/sessions/{sid}/log/table` | The log as a LaTeX table, like `hypotheses.tex` |

Environment:

| Method | Path | Purpose |
|---|---|---|
| GET | `/sessions/{sid}/environment` | Snapshots used in the session |

## 8. Run scheduling

Runs execute one at a time, so they do not distort each other's timings.

- One FIFO queue per machine, shared by all sessions. LocalRunner has one
  worker slot. A job is a set of definitions; gaib runs their repetitions
  in sequence inside one process, with one warm-up, as in the paper.
- Remote hardware gets its own queue per target. A remote job never blocks
  a local one, and the reverse.
- On startup the backend takes an exclusive lock file in the sessions
  directory, so a second backend instance cannot start a parallel queue.
- While a job runs, the backend does no analysis work of its own. Analysis
  endpoints read files on request; suggestion requests wait in the queue
  behind the running job unless the suggester is remote.
- The frontend receives progress over server-sent events, not by polling.
- The environment snapshot records that the app's own processes were
  running. The sleep check flags runs whose gaps suggest interference.

## 9. Workflow steps

The steps are Algorithm 1 of the paper, one screen each. Each step shows
the algorithm's guiding question.

Inputs: a workload W (tasks and datasets) and candidate configurations C
(hardware, algorithms, representations). Output: a configuration decision
and the evidence supporting it.

| Step | Algorithm 1 | Question shown | What the app does |
|---|---|---|---|
| 1. Characterize | Characterize W and choose metrics M | What are we studying? How should we measure it? | Pick tasks, datasets and C. Run gaib's `dataset_properties.py` on the datasets through the run queue. Choose M (per-phase time, effectiveness). |
| 2. Explore | Run exploratory experiments on C0 ⊂ C; observe | Which interactions suggest dominant bottlenecks or unexpected results? | Pick C0, run it, show summaries, ETL share and validity checks. Record observations. |
| 3. Hypothesize | Form hypotheses H with falsifiable predictions; record them | Why these results? What would show we're wrong? | Show suggestions. Edit and record hypotheses, predictions and validity checks. |
| 4. Design | Add to C any configuration H implicates | What configurations would isolate the hypothesized cause? | Add the implicated configurations to C, each linked to its hypothesis. |
| 5. Run | Run the experiments, measuring M | | Queue and run the targeted experiments. |
| 6. Compare | Compare results with predictions and explain them | Do observations match predictions? What explains results? | Compute each prediction's status. Record outcomes and explanations. |
| 7. Decide | Until the evidence supports a configuration decision | Do we understand enough to make a configuration decision? | Return to step 3, or record the decision. |
| 8. Report | Return decision and the recorded evidence | | Show the decision with the log, results and environment it rests on. Export the session. |

Steps 3 to 7 repeat. Each pass through them is an iteration, numbered in the
log, so the UI can show iteration 2, step 4.

`GET /sessions/{sid}/steps` returns this list with each step's status and
the current iteration. The UI marks the current step with the accent color.

## 10. Open questions

- Incremental Louvain (`NX_LOUVAIN_INCREMENTAL`) exists in gaib but is not
  in the case study. It is left out of the registry for now.
- On a CPU-only machine the demo can replicate the baselines and the label
  propagation hypothesis, but not the comparisons against GPU Louvain or
  the cudf hypotheses. Those need a remote runner.
