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

### Decision: submodule, run in a separate benchmark environment

1. HARD-graph-benchmark is a submodule at `vendor/HARD-graph-benchmark`,
   pinned to `dbb9903` (main, 2026-10-25). The URL is the fork,
   `https://github.com/nicoleschneider/HARD-graph-benchmark.git`, never
   `osullik/umd_thesis`.
2. It is not installed. The LocalRunner runs gaib's own scripts as
   subprocesses, with the working directory set to a per-job scratch folder
   (`jobs/<job_id>/work/cwd`). gaib's relative paths then land inside that
   folder, and an `exit()` ends the subprocess, not the server.
3. The scripts run in a benchmark environment built from gaib's own lock
   file, not in the backend's environment. `egb setup-bench` builds it with
   uv: Python 3.11.9 and `requirements-lock-cpu.txt`. On the Mac its package
   list is identical to the paper's Mac freeze.
4. The runner calls gaib through its command lines, with no app code in
   between:
   - NetworkX loading: write gaib definition JSONs into the job folder, then
     run `runner.py --hardware --definitions --output-directory
     --results-csv --repetitions --base-seed --pagerank-reference-directory`.
     Its `__main__` block calls `Runner.run_all_experiments`, which keeps
     gaib's warm-up on the karate club graph, its run order, the seed rule
     (base seed + run index), its timing points and its scoring.
   - cudf loading: run `iterations/representation_test.py` with
     `PYTHONPATH` set to `gaib/` and its flags (`--task`, `--dtype`,
     `--undirected`, `--repetitions`, `--base-seed`, `--results-csv`,
     `--hardware`, `--manifest`, `--pagerank-reference-directory`). This keeps
     its 3-node warm-up, its `cp.cuda.Device().synchronize()` calls and its
     cross-path validity CSVs.
   - Dataset downloads: run `download_datasets.py` with a datasets list
     written from the registry.
   - Dataset properties: run `dataset_properties.py` over every downloaded
     dataset, writing `data/dataset_properties.csv` (vertices, edges, degree
     statistics, clustering, components). On the four datasets downloaded
     here it matches the paper's committed `dataset_properties.csv` exactly.
5. The backend does not import gaib. It reads gaib's CSVs as written, so it
   needs none of gaib's dependencies.
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

`backend/tests/test_e2e.py` runs email-Eu-core with Louvain and PageRank, 2
repetitions, seeds 123 and 124, through the API. It then reruns the same
definitions with `runner.py` directly and compares every non-timing column.
It also compares them with the paper's Mac rows in
`gaib/results/mac_m5max_20261019/results_cpu.csv`. Seed, vertices, edges,
communities, NMI (0.5894387968016631 at seed 123), modularity and top-100
overlap match exactly. PageRank L1 matches to a relative 1e-12; see below.

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
- `pagerank_l1` is not bit-reproducible across processes. gaib sums over a
  set union of vertex ids, whose order follows Python's per-process string
  hashing. The same scores give 0.004905715876046686 to
  0.004905715876046691 on email-Eu-core across `PYTHONHASHSEED` values.

## 2. Components

```
frontend/  React + TypeScript (Vite). Renders steps, forms and tables from API data.
backend/   FastAPI. Registry, sessions, job queue, analysis, suggesters.
vendor/HARD-graph-benchmark   gaib, pinned submodule.
data/      Benchmark environment, datasets (gaib layout and manifest),
           PageRank reference cache, download jobs, run lock.
sessions/  One folder per session.
```

The backend never runs a benchmark in its own process. gaib's scripts never
talk to the API; they write files and the backend reads them.

## 3. Registry

Every extensible concept is a registry entry: a YAML file validated by a
Pydantic model. Built-in entries live in `backend/src/egb/entries/<kind>/`,
one file per entry, named by its id. `--extra-entries` adds directories with
the same layout; a duplicate id is an error. The UI lists, filters and renders entries from the API. Adding an
entry needs no frontend change.

Entries that need code (a driver, a metric formula, a suggestion rule) name
it by import path, for example `egb.metrics.ratio_of_medians`. Entries that
take parameters declare them as JSON Schema; the frontend renders a form
from the schema.

The backend checks compatibility, not the UI. `GET /api/configurations`
lists every implementation and representation pair with whether it can run
on the local machine, and if not, why ("Needs cuda. No NVIDIA GPU found.").

### Kinds

| Kind | Declares | Paper entries |
|---|---|---|
| `task` | Problem being solved, its output type, metrics that apply | `community-detection`, `centrality` |
| `algorithm` | Task, parameters, required graph orientation | `louvain`, `label-propagation`, `pagerank` |
| `implementation` | Algorithm, library, backend, hardware requirements, driver | `networkx`, `nx-cugraph` per algorithm |
| `representation` | Loading path, in-memory structure, precision, where graph construction is timed | `networkx-text`, `cudf-float32`, `cudf-float64` |
| `dataset` | Files, hashes, size, orientation, ground truth, source URL | the five SNAP graphs |
| `metric` | Columns (summed), unit, direction, applicable tasks, whether ground truth is needed | see below |
| `runner` | Runner class and its settings schema (later) | `local`, later `ssh`, `slurm` |
| `suggester` | Suggester class and its settings (later) | `rules`, later `agent` |

Hardware is not a registry entry. The local machine is detected at startup
(see Hardware below); remote machines will be detected the same way.

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
| centrality | pagerank | `alpha=0.85` (`Task.PAGERANK_ALPHA`) | dataset's own |

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
the downloaded archive's name and SHA-256 (as in gaib's manifest), the
extracted file name, vertex and edge counts, orientation and ground-truth
file. A dataset counts as verified when the extracted file exists and its
archive matches the hash. Hashes are computed once per file version and
cached; while a job runs, an uncached file shows as unverified instead of
being hashed. Downloads run gaib's `download_datasets.py` through the run
queue, so they never overlap a timed run.

Metrics:

The registry holds the eight per-run metrics: `etl_s`, `processing_s`,
`end_to_end_s` (`etl_s + processing_s`), `modularity`,
`predicted_communities`, `nmi` (needs ground truth), `pagerank_l1` and
`pagerank_top100` (against the reference). The analysis adds `etl_share`,
speedups with intervals, and the validity measures (L1 between score
vectors, identical partitions, NMI between partitions, modularity
difference); see section 5.

The notebook's formulas for end-to-end time, ETL share and the ratio of
medians are ported into `egb/analysis.py`. Tests reproduce the paper's
medians and its 0.85 to 1.11 interval from the committed results. The
notebook's sleep check is not ported yet.

Hardware:

The first version has one hardware entry: the machine the backend runs on.
Nothing about it is assumed from the gaib work. On startup the backend
detects its specs (chip or CPU model, core counts, memory, OS and version,
GPUs with driver and CUDA version when `nvidia-smi` exists) and derives its
capabilities from them: `cpu` when the benchmark environment exists;
`cuda` when `nvidia-smi` lists a GPU and the benchmark environment can import
nx-cugraph and see a device; `cudf` when it can also run a cudf operation.
The checks run in the benchmark environment, because that is where gaib
runs. The hardware label written to the results CSV defaults to a slug of
the detected CPU and memory (`apple-m5-max-48gb` on the Mac), and the
practitioner can rename it with `PATCH /api/hardware/local`; the name is
kept in `data/hardware.json`. On the Mac this machine is
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

    async def run(self, handle: JobHandle) -> None: ...
    async def cancel(self, handle: JobHandle) -> None: ...
```

A `JobQueue` owns the queue and hands one `JobHandle` at a time to its
runner. The handle carries the `JobSpec`, the job folder, the event list and
a callback that appends status records to `jobs.jsonl`.

- `JobSpec` is fully resolved before it reaches a runner: the gaib
  invocations (argv, working directory, extra environment variables,
  results CSV), the job items mapping each gaib `task` and dataset name to
  a configuration, repetitions, base seed and hardware label. A runner does
  not consult the registry beyond verifying dataset hashes.
- Job kinds: `exploratory` (step 2), `targeted` (step 5, names a
  hypothesis), `download`.
- `JobEvent` types: `queued`, `started`, `environment` (snapshot hash),
  `run_completed` (configuration and the new results row), `finished`,
  `failed` (reason and the last 20 lines of `stdout.log`), `cancelled`.
  Status events also go to `jobs.jsonl`.
- A runner writes the job folder described in section 6 and the shared
  data directory, nothing else.

**LocalRunner.** Before the first invocation it verifies every dataset's
hash and takes the environment snapshot. Then it runs each invocation with
the benchmark interpreter, in `jobs/<job_id>/work/cwd`, with stdout and
stderr going to `stdout.log`, as on the cluster. It does not read stdout
while a job runs and sets no `PYTHONUNBUFFERED`, so output buffering matches
a paper run. Progress comes from the results CSV, which gaib appends to and
fsyncs once per run: the runner checks it once per `--poll-seconds`
(default 1 s) and emits `run_completed` for each new row. A row that maps
to no configuration logs a warning. On success the `work/` folder is
deleted. Cancelling sends SIGTERM to the process group; partial rows stay
and the job is marked cancelled.

**RemoteRunner (proposed).** Same interface, over SSH to machines declared
in a targets file. No scheduler integration. The design is in
`docs/remote.md`.

## 5. Analysis and hypothesis suggestions

### Analysis (`egb/analysis.py`)

Rows in the paper's schema become runs keyed by a configuration key,
`hardware/implementation/representation`, found from each row's `hardware`
and gaib `task` through the registry. Keys, not session ids, are what
analysis, predictions and validity checks use, so results from several
machines can sit side by side later.

- **Summary** per configuration and dataset: run count; median, min and
  max of `etl_s`, `processing_s` and end-to-end time; median ETL share
  (median over runs of `etl_s / end_to_end_s`, as in the notebook);
  median, min and max of each effectiveness column present.
- **Bottleneck** per configuration and dataset: the dominant phase and its
  share, with the fastest configuration for the same task and dataset.
- **Differences** between configurations of the same task on the same
  dataset: `faster_processing_slower_end_to_end`, and
  `faster_with_different_effectiveness` (modularity or NMI more than 0.01
  apart, top-100 overlap more than 0.01 apart, L1 more than 1e-6 apart, or
  community counts more than 25% apart).
- **Crossovers**: tasks whose fastest configuration changes with dataset,
  ordered by edge count.
- **Comparisons**: speedup of each configuration against a baseline, as a
  ratio of medians with a bootstrap 95% interval. The bootstrap is the
  notebook's `ratio_of_medians`, with the same sampling order, so it
  reproduces the notebook's intervals.

Thresholds and bootstrap settings live in `egb/analysis.yaml`
(`--analysis-config` to override): 10,000 resamples, seed 123.

### Validity checks (`egb/validity.py`)

A targeted configuration is checked against a baseline on one dataset.

- Score outputs: L1 distance between the two score vectors, expected at
  most 1e-14.
- Partitions: identical or not, NMI between them (expected at least 0.99),
  and modularity difference (expected within 0.01).
- Side effects: every factor that differs besides the intended one:
  hardware, algorithm, backend, loading path (with "compare end-to-end, not
  processing"), and precision when the algorithm is precision-sensitive
  (`precision_sensitive` on the algorithm entry; PageRank yes, unweighted
  Louvain and label propagation no).

Sources, in order:

1. gaib's cross-path CSVs, when the targeted variant's
   `cross_path_baseline` is the baseline's representation on the same
   implementation and hardware. `representation_test.py` computes these in
   the same process as the run.
2. Saved outputs. After each `runner.py` invocation, the LocalRunner copies
   the run-0 result of every definition from gaib's `cache/` folder into
   `jobs/<job_id>/outputs/`, with an index. Comparing two saved outputs runs
   `egb/compare_outputs.py` in the benchmark environment, using gaib's
   `Evaluator` for L1 and NMI. Only run 0 is kept, so both configurations
   need the same base seed.
3. Otherwise the result says no outputs exist for a common seed.

Validity checks are refused with 409 while a job runs, because they load
outputs and compute NMI.

### Prediction evaluation (`egb/predictions.py`)

A structured prediction is:

```json
{"metric": "end_to_end_s",
 "subject": {"hardware": "nexus-2080ti-gpu", "implementation": "louvain-nx-cugraph", "representation": "cudf-float32"},
 "relation": "<",
 "reference": {"hardware": "macbook-m5max-48gb", "implementation": "louvain-networkx", "representation": "networkx-text"},
 "datasets": ["email-Eu-core"]}
```

`reference` (a configuration) or `threshold` (a number), not both. Metrics:
the three phases, `etl_share`, and the effectiveness columns. For each
dataset the evaluator forms the ratio of medians, predicted larger over
predicted smaller, with a bootstrap 95% interval:

| Interval | Status | Symbol |
|---|---|---|
| Above 1 | held | ✓ |
| Below 1 | refuted | ✗ |
| Includes 1 | narrow | ≈ |
| Missing runs | pending | · |

The prediction's status is refuted if any dataset is refuted, then pending,
then narrow, otherwise held. On the paper's data, the second hypothesis
(GPU cudf Louvain below the Mac on email-Eu-core) evaluates to narrow with
interval 0.85 to 1.11, the paper's statistical tie.

### Suggester interface (`egb/suggesters.py`)

```python
class HypothesisSuggester(Protocol):
    id: str

    async def suggest(self, request: SuggestionRequest) -> SuggestionResponse: ...
```

`SuggestionRequest`:

- `analysis`: the structured analysis above.
- `observations`: the same facts as statements with ids
  ("ETL: 87% of end-to-end time. nexus-2080ti-gpu PageRank, NetworkX
  loading, web-Stanford."), for suggesters that read text.
- `alternatives`: configuration keys runnable on the available hardware.
  The API passes the local machine's; already measured ones are skipped.
- `history`: the session's log. A suggestion whose `key` appears in a
  recorded entry's `suggestion` is not offered again.
- `limit`.

`SuggestionResponse`: `suggester`, `status` (`complete` or `unavailable`),
`message`, and ranked `suggestions`. Each `Suggestion` has a stable `key`,
`rank`, `score`, `source`, `rule`, a one-sentence `hypothesis`,
`observation_ids`, a `change` (`representation`, `algorithm`, `hardware`,
`datasets` or `validity`, with source and target keys), prediction drafts
(sentence plus structured prediction), validity check drafts, and caveats.

Predictions are drafts until recorded. The practitioner edits the sentence
and the structured fields, then records it. After that the entry is
immutable; changes are new `revision` or `amendment` entries.

**RuleSuggester (now).** Four rules. Each pre-fills predictions with
medians from the data.

| Rule | Fires when | Suggests | Pre-filled prediction |
|---|---|---|---|
| `etl-dominates` | ETL share ≥ 50% on datasets where the configuration is not fastest, and another representation of its implementation is runnable | Representation change, preferring the same precision | End-to-end below the fastest configuration's medians on those datasets; L1 or partition validity checks |
| `processing-dominates` | Processing share ≥ 50 % on any dataset, whether or not it is fastest; the prediction's reference is the fastest other configuration, or the configuration itself when it has no competitor | Algorithm change on the same hardware, or hardware change to an accelerated implementation of the same algorithm and representation | End-to-end below the fastest medians; for algorithm changes, quality below the current medians |
| `different-effectiveness` | A faster configuration's effectiveness differs | Validity check against the slower one | The difference persists, in its observed direction |
| `dataset-dependent` | The fastest configuration changes with dataset | Datasets between the crossover sizes | The larger-graph winner wins on them; if none is registered, a note to add one |

Ranking: largest end-to-end gap the change could close (slower median over
fastest median), then key. On the paper's initial exploration (Mac, Xeon and
GPU with NetworkX loading, five datasets) it suggests, in order: label
propagation on the Xeon and on the Mac, cudf float64 loading for GPU
PageRank ("end-to-end time below ... 4.87 s on web-Stanford ..."), validity
checks for GPU Louvain's different community counts, cudf loading for GPU
Louvain on email-Eu-core, and broader datasets between 16,706 and 147,892
edges.

**AgentSuggester (later).** Same interface. It now returns `unavailable`
with the message "Agent suggestions are not available yet. Use the
rule-based suggester." Prompt files will live in `backend/prompts/`.

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
  suggestions/<request_id>.json  each suggestion request and its response
  jobs/<job_id>/
    spec.json             resolved JobSpec, with the baseline for targeted jobs
    command.jsonl         one line per invocation: argv, working directory, extra environment variables
    environment.json      this job's environment snapshot
    definitions/*.json    gaib definitions, for gaib-runner jobs
    results.csv           paper schema (gaib's runner.RESULTS_COLUMNS)
    cross_path_l1.csv     representation-test PageRank jobs
    cross_path_louvain.csv  representation-test Louvain jobs
    reports/*.json        gaib's per-run JSON reports
    outputs/              run-0 partitions or scores per definition, with index.json
    stdout.log
    work/                 gaib's working directory, deleted on success
```

Shared across sessions, under a data directory set by a CLI option:
`bench-env/`, `datasets/` in gaib's layout with its `manifest.json`,
`pagerank_references/`, `jobs/<job_id>/` with `jobs/jobs.jsonl` for download
and properties jobs, `dataset_properties.csv`,
`hardware.json`, and the run lock `.egb.lock`. gaib keys the reference cache by dataset hash,
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
  "state_manifest": [["jobs/j-20261116-055702-6f3e/results.csv", "..."]],
  "state_hash": "sha256:...",
  "prev_entry_hash": "sha256:...",
  "entry_hash": "sha256:..."
}
```

- `details`: free-form structured data. Outcomes store the overall status,
  each prediction's evaluation, side effects and failed validity checks;
  decisions store the chosen configuration and caveats per task.
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
- `state_manifest` lists `(relative path, SHA-256 of file)` for
  `config.json`, `config_history.jsonl`, every environment snapshot, and the
  `results.csv` and cross-path CSVs of every job that has ended. Files of a
  job still running are left out, since they are still growing.
  `state_hash` is the SHA-256 of the manifest. Together they prove which
  results existed when the entry was recorded: a prediction whose manifest
  does not list the iteration's results was recorded before them.
- `prev_entry_hash` chains entries. `entry_hash` covers every other field.
  `GET .../log/verify` recomputes each entry hash, the chain and each state
  hash, and checks that every job and environment file a manifest lists is
  unchanged.
- A targeted experiment (step 5) must name a recorded hypothesis with at
  least one prediction. The backend rejects it otherwise. Exploratory jobs
  (step 2) need none.

### Environment snapshot

Taken at job start, before any timed process. The Python, platform and
package fields come from `egb/probe.py` run with the benchmark interpreter,
so they describe what actually ran. Fields: hardware label, hostname, OS and
version, CPU model and core counts, memory, GPU name, driver and CUDA
version from `nvidia-smi` when present, Python version, the package freeze,
the lock file name and hash, gaib's submodule commit and dirty flag, and
this app's commit. Snapshots are stored by content hash; a job references
its snapshot. A changed environment within a session shows up as a new
hash.

## 7. API

All routes are under `/api`. Request and response bodies are Pydantic
models. The frontend generates its TypeScript types from the OpenAPI
schema. "Built" marks what exists now.

Registry and machine:

| Method | Path | Purpose | Built |
|---|---|---|---|
| GET | `/registry` | All entries, grouped by kind | yes |
| GET | `/registry/{kind}` | Entries of one kind | yes |
| GET | `/registry/{kind}/{id}` | One entry | yes |
| GET | `/configurations` | Every implementation and representation pair, runnable or the reasons not | yes |
| GET | `/hardware` | Hardware with detected specs and capabilities | yes |
| PATCH | `/hardware/local` | Rename the hardware label | yes |
| GET | `/datasets` | Each dataset: missing, unverified, verified or mismatch | yes |
| POST | `/datasets/{id}/download` | Queue a download job | yes |
| POST | `/datasets/properties` | Queue a properties job over downloaded datasets; `GET /datasets` then includes them | yes |
| GET | `/queue` | Running job and queued jobs, across sessions | yes |

Sessions:

| Method | Path | Purpose | Built |
|---|---|---|---|
| GET | `/sessions` | List | yes |
| POST | `/sessions` | Create from name, question and config | yes |
| GET | `/sessions/{sid}` | Metadata and config | yes |
| PATCH | `/sessions/{sid}/config` | Change configuration; the old version goes to `config_history.jsonl` | yes |
| GET | `/sessions/{sid}/steps` | Workflow steps with status | later |
| GET | `/sessions/{sid}/export` | Zip of the session folder | later |

Jobs:

| Method | Path | Purpose | Built |
|---|---|---|---|
| POST | `/sessions/{sid}/jobs` | Queue configurations × datasets; targeted jobs name a hypothesis and optionally a baseline | yes |
| GET | `/sessions/{sid}/jobs` | Jobs with status | yes |
| GET | `/jobs/{jid}` | One job: spec, status, events | yes |
| GET | `/jobs/{jid}/events` | Server-sent events stream | yes |
| DELETE | `/jobs/{jid}` | Cancel | yes |

Results and analysis:

| Method | Path | Purpose | Built |
|---|---|---|---|
| GET | `/sessions/{sid}/results` | Rows joined with configuration ids | yes |
| GET | `/sessions/{sid}/summary` | Phase medians and ranges, ETL share, effectiveness per configuration and dataset | yes |
| GET | `/sessions/{sid}/environment` | Snapshots used in the session | yes |
| GET | `/sessions/{sid}/comparisons?baseline=&measure=` | Speedups with intervals against a baseline key | yes |
| GET | `/sessions/{sid}/validity?targeted=&baseline=&dataset=` | Validity of one configuration against another | yes |
| GET | `/jobs/{jid}/validity` | Validity of a targeted job against its baseline | yes |
| GET | `/sessions/{sid}/analysis` | Summaries, bottlenecks, differences, crossovers | yes |
| GET | `/sessions/{sid}/observations` | Observations as statements | yes |

Hypotheses:

| Method | Path | Purpose | Built |
|---|---|---|---|
| GET | `/sessions/{sid}/log` | All entries | yes |
| POST | `/sessions/{sid}/log` | Append an entry | yes |
| GET | `/sessions/{sid}/log/verify` | Recompute the chain and check recorded files | yes |
| POST | `/sessions/{sid}/suggestions` | Run a suggester (`rules` or `agent`); stores and returns the response | yes |
| GET | `/sessions/{sid}/suggestions/{rid}` | A stored suggestion response | yes |
| POST | `/sessions/{sid}/log/{eid}/evaluate` | Evaluate a recorded structured prediction | yes |
| GET | `/sessions/{sid}/log/table` | The log as a LaTeX table, like `hypotheses.tex` | later |
| GET | `/sessions/{sid}/report.md` | Markdown report with provenance | yes |

## 8. Run scheduling

Runs execute one at a time, so they do not distort each other's timings.

- One FIFO queue per machine, shared by all sessions. LocalRunner has one
  worker slot. A job is a set of definitions; gaib runs their repetitions
  in sequence inside one process, with one warm-up, as in the paper.
- Remote hardware gets its own queue per target. A remote job never blocks
  a local one, and the reverse.
- On startup the backend takes an exclusive lock on `.egb.lock` in the data
  directory, so a second backend on the same machine cannot start a parallel
  queue, even with a different sessions directory.
- On startup, jobs left queued or running by a previous backend are marked
  `interrupted`.
- While a job runs, the backend does no analysis work of its own. Analysis
  endpoints read files on request; suggestion requests wait in the queue
  behind the running job unless the suggester is remote.
- The frontend receives progress over server-sent events, not by polling.
- The environment snapshot records that the app's own processes were
  running. A port of the notebook's sleep check, to flag runs whose gaps
  suggest interference, is still to come.

## 9. Workflow steps

The left rail shows the six steps of Algorithm 1, one per `State` line.
Steps 3 to 6 sit inside a bracket marked "Repeat until decided", the
algorithm's `Until`. Below it, Decision is the algorithm's `Return`. Each
screen shows the algorithm's guiding question under its title. The current
step takes the accent color.

| Step | Algorithm 1 | Screen | What it does |
|---|---|---|---|
| 1. Characterize | Characterize W and choose metrics M | Workload, Configurations | Tasks, datasets and their properties; hardware, algorithms and loading, repetitions. Read-only once a session exists. |
| 2. Explore | Run exploratory experiments on C0 ⊂ C; observe | Explore | Creates the session, runs C0 on every dataset with live progress, then per-dataset ETL and processing bars, phase medians with the dominant phase highlighted, and an effectiveness table. |
| 3. Hypothesize | Form hypotheses H with falsifiable predictions; record them | Hypothesize | Suggestions from the rules or agent suggester. Each hypothesis and prediction is editable: statement, metric, comparison, threshold (a configuration's median or a fixed value) and datasets, with the resulting thresholds per dataset. Record locks it and shows its time and entry id. |
| 4. Design and 5. Run | Add the implicated configuration to C; run it | Test | The diff view of the one changed setting against the baseline. Run test adds the target to C if needed and runs it as a targeted job with the baseline named. Validity checks run when it finishes. |
| 6. Compare | Compare results with predictions and explain them | Result | Each prediction's status (✓ ✗ ≈) with medians, threshold, ratio, 95 % interval and runs; side effects and failed validity checks; an explanation. Iterate or Decide records the outcome. |
| Return | Return decision and the recorded evidence | Decision | The chosen configuration per task, with caveats, the full trail, provenance, and export as Markdown or PDF. |

The right panel is the iteration trail: a swimlane with one lane per task,
grouped by hardware, and one column per iteration (Explore, 1, 2, ...,
Chosen), like the paper's case study figure. Each cell shows the
configuration tested in that lane and its outcome status.

The decision screen preselects, per task, the configuration that is fastest
on most datasets among those whose quality metric (modularity, or top-100
overlap) is within 0.01 of the best. Its caveats list quality gaps, every
prediction outcome for that task, and the side effects of the chosen
configuration's test.

### Report

`GET /api/sessions/{sid}/report.md` returns the session as Markdown: the
decision and caveats; the trail with timestamps; each prediction's per-dataset
medians, ratio, interval and status; summaries per configuration and dataset;
and provenance (session, app and gaib commits, log verification, each
environment snapshot with hardware, Python, package count and lock file hash,
and every job). The PDF export prints the Decision screen, which carries the
same decision, trail and provenance; the print stylesheet hides the rail, the
trail panel and buttons.

## 10. Open questions

- Incremental Louvain (`NX_LOUVAIN_INCREMENTAL`) exists in gaib but is not
  in the case study. It is left out of the registry for now.
- On a CPU-only machine the demo can replicate the baselines and the label
  propagation hypothesis, but not the comparisons against GPU Louvain or
  the cudf hypotheses. Those need a remote runner.
