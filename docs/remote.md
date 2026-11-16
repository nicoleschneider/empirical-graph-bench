# Remote hardware

Design for running configurations on other machines over SSH, alongside
local runs. It implements the `Runner` interface in `docs/architecture.md`
section 4. Status: built and tested against a local stand-in transport;
never run over real SSH to another machine (section 13).

Scope: plain SSH to a machine that the app can reach. There is no scheduler
integration (no Slurm), no pull worker, and no claim that it works on any
particular cluster. The cluster we would use is behind a VPN that blocks SSH
from this Mac, so it stays untested.

## Contents

1. [Overview](#1-overview)
2. [Targets file](#2-targets-file)
3. [Authentication](#3-authentication)
4. [Remote layout and setup](#4-remote-layout-and-setup)
5. [Running a job](#5-running-a-job)
6. [Timing integrity](#6-timing-integrity)
7. [Hardware detection](#7-hardware-detection)
8. [Datasets](#8-datasets)
9. [Provenance](#9-provenance)
10. [Failure handling](#10-failure-handling)
11. [Sessions, queues and the UI](#11-sessions-queues-and-the-ui)
12. [API changes](#12-api-changes)
13. [Testing](#13-testing)
14. [Decisions for review](#14-decisions-for-review)

## 1. Overview

```
app (this Mac)                                  remote machine
--------------                                  --------------
RemoteRunner ──ssh──> egb_remote.py probe  ──>  specs, capabilities, environment
             ──ssh──> tar stream (job folder) ─> <workdir>/jobs/<job_id>/
             ──ssh──> egb_remote.py start  ──>  detached: flock, then gaib's
                                                runner.py / representation_test.py
             ──ssh──> egb_remote.py status ──>  status.json, results.csv rows
             <─ssh──  tar stream (results)  <──  results, reports, outputs, logs
```

- The app uses the system `ssh` binary as a subprocess. It does not
  implement SSH itself and never handles keys.
- `egb_remote.py` is a stdlib-only Python script that the app copies to
  the remote machine with three stdlib-only helpers (`machine.py` for
  hardware detection, `worker.py` for the run lock and gaib output handling,
  `probe.py` for the package list). Commands: `probe`, `verify`, `start`,
  `run` (the detached process), `status` and `cancel`. It does no timing
  itself; it starts gaib's own scripts. It needs Python 3.10 or later as
  `python` on the remote machine.
- The job folder sent to the remote machine is the same one LocalRunner
  builds (spec, gaib definitions, argv), with paths rewritten to the remote
  workdir.

## 2. Targets file

Remote machines are declared in a YAML file, `data/targets.yaml` by default
(`--targets` to override). It is not committed; `data/` is ignored.

```yaml
targets:
  - name: lab-gpu
    host: lab-gpu
    workdir: /scratch/me/egb
    setup:
      - module load Python3/3.11.9
    python: python3
    lock_file: requirements-lock.txt
    datasets: upload
    supports:
      - pagerank-networkx
      - pagerank-nx-cugraph
      - louvain-networkx
      - louvain-nx-cugraph
    poll_seconds: 10
    connect_timeout_seconds: 10
    max_job_minutes: 120
```

| Field | Meaning |
|---|---|
| `name` | Target id in the app and the hardware label in results. Lowercase letters, digits, hyphens; `local` is reserved. |
| `transport` | `ssh` (default) or `local-shell`, a test stand-in that runs the same commands on this machine without ssh. |
| `host` | What `ssh` is given: an alias from `~/.ssh/config`, or `user@host`. Required for `ssh`, not allowed for `local-shell`. |
| `workdir` | Absolute path on the remote machine for everything the app writes there. |
| `setup` | Shell commands run before every remote command (modules, `PATH`). |
| `python` | Interpreter, after `setup`, used to build the benchmark environment. |
| `lock_file` | gaib lock file for this machine, relative to the gaib root. |
| `datasets` | `upload` (from the app's `data/datasets`) or `download` (remote fetches from SNAP). |
| `supports` | Registry implementation ids allowed on this target. Detected capabilities still apply. |
| `poll_seconds` | How often the app checks a running job. |
| `connect_timeout_seconds` | SSH connect timeout. |
| `max_job_minutes` | Wall-clock limit per job, after which the app cancels it. |
| `lost_after_minutes` | How long polls may fail before the job is marked lost. |

A stand-in for testing, on this machine without SSH:

```yaml
  - name: standin
    transport: local-shell
    workdir: /absolute/path/to/data/remote-standin
    python: /path/to/python3.11
    supports: [louvain-networkx, pagerank-networkx]
    poll_seconds: 2
```

The schema forbids unknown keys, so a `password`, `identity_file` or similar
field is rejected when the file loads. Connection details beyond `host`
(user, port, key, jump host) belong in `~/.ssh/config`.

## 3. Authentication

- Every SSH call runs with `-o BatchMode=yes`, so ssh never prompts. A host
  that needs a password or passphrase fails, and the target shows as
  unavailable with "SSH needs a key in your agent."
- Keys and agent come from the user's own setup (`ssh-agent`,
  `~/.ssh/config`). The app passes no key paths and reads no key files.
- Host keys are checked against `~/.ssh/known_hosts` as ssh normally does.
  The app never sets `StrictHostKeyChecking=no`. An unknown host shows
  "Host key unknown. Connect once with ssh to accept it."
- Connections are multiplexed with `ControlMaster=auto` and a
  `ControlPath` under `data/ssh/` (mode 0700), with `ControlPersist=60s`.
  That avoids a handshake per poll.
- Logs and `command.jsonl` record the target name and the remote command,
  never the environment, the agent socket or any key material. stderr from
  ssh is kept only for the failure reason shown in the UI.

## 4. Remote layout and setup

```
<workdir>/
  egb_remote.py           copied by the app; replaced when its hash changes
  gaib/<commit>/          HARD-graph-benchmark checkout at the pinned commit
  bench-env/              benchmark environment built from lock_file
  datasets/               gaib layout with manifest.json
  pagerank_references/    gaib's reference cache
  jobs/<job_id>/          one folder per job
```

- **Code.** The app makes a `git bundle` of the pinned submodule commit and
  streams it over ssh; the remote side clones it into `gaib/<commit>/`. This
  needs no internet access on the remote machine and no GitHub credentials,
  and the remote checkout is a real git repository, so its commit and
  uncommitted-change count can be read there.
- **Environment.** `egb remote setup <target>` runs `setup`, then
  `python -m venv bench-env` and `bench-env/bin/pip install -r
  gaib/<commit>/<lock_file>`. This step needs the remote machine to reach
  PyPI (and pypi.nvidia.com for the GPU lock files). It is a one-time,
  explicit command, never run during a job.
- Every remote command is `ssh <host> -- bash -c '<setup>; cd <workdir>;
  ...'`, with arguments quoted by the app.

## 5. Running a job

1. **Prepare (app).** Resolve the job as for local runs, with the target's
   hardware label and remote paths. Write the job folder locally.
2. **Send.** Stream the job folder to `<workdir>/jobs/<job_id>/` with `tar`
   over ssh. tar avoids depending on rsync, which differs on macOS.
3. **Verify.** `egb_remote.py verify` hashes the job's datasets against the
   registry hashes on the remote side. Any mismatch fails the job before
   anything is timed.
4. **Start.** `egb_remote.py start <job_id>` forks a detached process
   (`setsid`, `nohup`) and returns at once with its process group id. The
   detached process takes the machine run lock (section 6), writes
   `status.json` (`waiting`, `running`, `finished`, `failed`,
   `cancelled`), runs each invocation in turn with stdout to `stdout.log`,
   and writes the exit codes.
5. **Poll.** Every `poll_seconds` the app runs `egb_remote.py status
   <job_id>`, which prints `status.json` and any new `results.csv` rows.
   New rows become `run_completed` events, as for local runs.
6. **Collect.** When the status is terminal, the app streams back
   `results.csv`, cross-path CSVs, `reports/`, `outputs/` (with paths
   rewritten), `stdout.log`, `environment.json` and `status.json` into the
   session's `jobs/<job_id>/`, then deletes the remote `work/` folder.

Because the remote process is detached, a dropped connection or an app
restart does not stop it. On startup the app resumes polling remote jobs
that `jobs.jsonl` shows as started, instead of marking them interrupted.

## 6. Timing integrity

- Runs are timed on the remote machine by gaib's own `runner.py` and
  `representation_test.py`, with the same arguments as locally. The app
  never times anything remotely and only results travel back.
- One run at a time per machine: the detached process holds an exclusive
  `flock` on `~/.cache/egb/run.lock` on that machine for the whole job. The
  LocalRunner takes the same per-user lock on its own machine. Two app
  instances, or a local and a "remote" target on the same physical machine,
  therefore never overlap. A job waiting for the lock shows as `waiting`.
- The app also keeps one queue per target, so it never submits two jobs to
  one target at once.
- During a run, the only other remote activity is the status poll: one
  short ssh command every `poll_seconds` (default 10 s) that reads two small
  files. The environment snapshot records the poll interval.
- Environment probing, dataset verification and file transfer happen before
  the lock is taken or after it is released.

## 7. Hardware detection

`egb_remote.py probe` returns the same fields the local detection returns
(`docs/architecture.md`, Hardware): CPU model, physical and logical cores,
core groups, memory, OS and version, GPUs with driver and memory from
`nvidia-smi`, and CUDA version. Capabilities are checked by running the
remote benchmark interpreter: `cuda` if nx-cugraph sees a device, `cudf` if
a cudf operation runs.

To keep one implementation, the detection functions move from
`egb/hardware.py` into a stdlib-only module that both the backend and
`egb_remote.py` use. The remote probe is that module plus a small entry
point; nothing else from the backend goes to the remote machine.

A configuration is runnable on a target when its implementation is listed
in `supports` and the capabilities it needs were detected. Otherwise it is
shown as unavailable with the reason, as for the local machine ("Needs
CUDA and cudf. No NVIDIA GPU found.", or "Not in this target's supports
list.").

## 8. Datasets

- `datasets: upload` (default): before a job, the app checks the remote
  manifest. For any dataset missing or with a different hash, it streams the
  downloaded archive (`raw_file`) and the extracted file from the app's
  `data/datasets/`. The remote side then hashes the archive against the
  registry's SHA-256.
- `datasets: download`: the remote side runs gaib's `download_datasets.py`
  for missing datasets, as the local download job does, then verifies.
- Either way, the job does not start unless every dataset verifies against
  the registry hash on the remote machine (section 5, step 3). The hash in
  the registry is the same one gaib's manifest records.

## 9. Provenance

Each remote job stores an environment snapshot in the session, like local
jobs, with these fields added or filled from the remote side:

- target name and `host` as given in the targets file;
- remote hostname from the machine itself;
- CPU model, cores, memory, GPU model, driver and CUDA version;
- gaib commit and uncommitted-change count of `gaib/<commit>/`;
- app commit and uncommitted-change count on the app side;
- benchmark environment: Python version, package list, lock file and hash;
- `egb_remote.py` hash;
- the poll interval;
- job timing: queued, lock acquired, started, finished.

The results CSV's `hostname` and `hardware` columns are written by gaib on
the remote machine. The analysis groups runs by hardware label, so remote
runs appear in summaries, comparisons, predictions and the trail like local
runs, under their own machine.

## 10. Failure handling

| Situation | Detection | Status shown | What happens |
|---|---|---|---|
| Host unreachable | ssh exit 255, timeout | ✗ Unavailable, "Host unreachable." | Target probe fails; jobs cannot be queued on it. |
| Key or host key problem | ssh stderr | ✗ Unavailable, with the specific reason | Same. |
| Environment missing | probe finds no `bench-env` | ✗ Unavailable, "Run egb remote setup lab-gpu." | Same. |
| Waiting for the run lock | `status.json` is `waiting` | · Waiting | Job is on the machine, not yet timed. |
| Connection lost while running | poll fails | · Running, "Connection lost, retrying." | Retries with backoff up to 10 minutes, then marks the job `lost` with "Check the job on lab-gpu." The remote run continues and a later poll can still collect it. |
| Job fails | exit code, `status.json` | ✗ Failed, with exit code and last log lines | Partial rows are collected. |
| Timeout | `max_job_minutes` passed | ✗ Failed, "Stopped after 120 minutes." | The app cancels it. |
| Cancellation | user | ✗ Cancelled | `egb_remote.py` sends SIGTERM to the process group; rows so far are collected. |
| Dataset mismatch | remote verify | ✗ Failed, "web-Stanford hash mismatch." | Nothing timed. |

## 11. Sessions, queues and the UI

- Session candidates carry a `hardware` field: `local` or a target name.
  Sessions saved before this change read as `local`.
- A job runs on one hardware. A request that mixes hardware is split into
  one job per hardware, each in that hardware's queue; the response is the
  list of jobs.
- **Configurations screen.** A Hardware table lists this machine and every
  target: connection, CPU, cores, memory, GPU, system, supported
  capabilities (✓ or ✗ for CPU, CUDA, cudf), and status: ✓ Ready (this
  machine), ✓ Reachable, · Checking, · Running, · Queued, or ✗ Unavailable
  with the reason. Remote rows have "Check again", which probes the target.
  The algorithms and loading table has a Hardware column, one row per
  hardware, implementation and loading, disabled with the reason where it
  cannot run ("Not in this target's supports list.", "Host unreachable.").
- Explore shows one progress block per machine. Results, summaries, charts,
  the trail (one lane group per machine) and the report show the hardware
  label as they do for this machine.

## 12. API changes

| Method | Path | Purpose |
|---|---|---|
| GET | `/hardware` | This machine and every target, with specs, capabilities, supports and status |
| POST | `/hardware/{id}/probe` | Probe a machine again |
| GET | `/configurations` | One row per hardware, implementation and loading |
| GET | `/queue` | Running and queued jobs per hardware |
| POST | `/sessions/{sid}/jobs` | Returns a list of jobs, one per hardware |

Candidates and job specs carry `hardware`. CLI: `egb remote setup <target>`
(worker, code, environment) and `egb remote check <target>` (probe).

The unit tests in `backend/tests/test_targets.py` cover the schema
rejecting credential fields, the reserved and required fields, the ssh
argument list (BatchMode on, no host key skipping, no key or user flags),
quoting of the remote script, and the classification of ssh errors.

## 13. Testing

Real SSH to another machine was not available: the cluster is behind a VPN,
and Remote Login is off on this Mac. The remote path is therefore tested
with the `local-shell` transport, which runs every remote command with
`bash -c` on this Mac instead of `ssh <host> bash -c`. Everything after the
transport is the same code: the worker script, setup by git bundle, the
benchmark environment built with `python -m venv` and pip from the lock
file, dataset upload and hash verification, the detached run under the
machine lock, polling, collection, and provenance.

`backend/tests/test_remote.py` (needs `--data-dir` and a stand-in set up
with `egb remote setup`) checks:

| Check | Result |
|---|---|
| Probe of a reachable target: specs, capabilities, supports list | Passes |
| An `ssh` target at `localhost`, where port 22 refuses | "Host unreachable.", and jobs on it are rejected |
| email-Eu-core, Louvain and PageRank, 2 repetitions, on the stand-in | Status events queued, started, running, 4 × run completed, finished |
| Those rows against a direct `runner.py` run of the same definitions | Every non-timing column equal; PageRank L1 within 1e-12 |
| Those rows against the paper's Mac rows | Seed, sizes, communities, NMI, modularity, top-100 equal |
| Provenance | Target, transport, remote hostname, specs, gaib commit with 0 uncommitted changes, lock file hash, worker hash, poll interval, and lock and finish times |
| Saved outputs collected and usable for validity checks | Passes |
| Job failure (benchmark Python exits 7 for `runner.py`) | Failed, "gaib exited with code 7.", log tail included |
| Cancellation after the first row | Cancelled, partial rows kept, no process left |
| Timeout (`max_job_minutes: 0.02`) | Failed, "Stopped after 0.02 minutes." |
| Local and remote configurations in one request | Split into two jobs; the remote job waits for the machine lock while the local job runs |

A UI pass ran an exploration on this Mac and the stand-in together and one
iteration on the stand-in. Remote rows appear in results, summaries, charts
and the trail under the target's name.

What this does not verify, compared with a real remote machine or cluster:

- **SSH itself:** key and agent use, `BatchMode`, host key checks,
  connection multiplexing, and ssh exit codes other than the
  connection-refused case. The classification of ssh errors is unit-level
  only.
- **A second machine:** different hostname, CPU, OS (Linux detection
  through `/proc` has never run), paths, and clock.
- **Network effects:** latency, bandwidth for dataset upload, connections
  dropping mid-job and recovering. The lost-connection path is untested.
- **Recovery after an app restart** with a remote job still running.
- **GPU, CUDA and cudf** on a remote machine; the stand-in has no GPU.
- **Module systems, shared filesystems and remote internet limits**, for
  example a remote machine that cannot reach PyPI during setup.
- **Any scheduler.** There is no Slurm support by design.
- **Timing independence.** The stand-in shares this Mac's CPU with the app,
  so its timings say nothing about remote hardware.

## 14. Decisions

1. **Code shipping by `git bundle`** rather than `git clone` on the remote
   side. It needs no internet or GitHub credentials on the remote machine.
2. **Environment built by an explicit `egb remote setup`,** never
   automatically, since it needs internet on the remote machine and takes
   minutes.
3. **A per-user machine run lock** (`~/.cache/egb/run.lock`) on both local
   and remote machines, so a "remote" target on the same physical machine
   cannot overlap local runs.
4. **Detached remote runs with polling** (default 10 s) rather than a
   long-lived SSH session, so runs survive dropped connections and app
   restarts.
5. **Candidates gain `hardware`.** This changes the session config format;
   existing sessions read as `local`.
6. **Datasets uploaded by default,** so remote machines need no internet for
   data.
7. **Remote hardware is labeled by target name** in results (`standin`,
   not a detected slug), so two targets on similar machines never merge in
   the analysis. Detected specs are in provenance.
8. **A `local-shell` transport** exists only so the remote path can be
   tested without SSH. It is a declared transport, not a hidden switch.
