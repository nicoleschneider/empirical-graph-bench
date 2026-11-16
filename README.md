# empirical-graph-bench

A web app that walks a practitioner through hypothesis-driven graph
benchmarking (Algorithm 1 of the HARD paper): exploratory runs,
observations, recorded hypotheses and predictions, targeted experiments,
and a configuration decision with its evidence. It runs the benchmark code
from [HARD-graph-benchmark](https://github.com/nicoleschneider/HARD-graph-benchmark)
(gaib) unchanged, as a pinned submodule.

Status: one full iteration works end to end on the local machine. See `docs/architecture.md` and `docs/design.md`.

## Setup

Requires Python 3.11 with [uv](https://docs.astral.sh/uv/), Node.js 24, and
git. Run these from the repository root.

```
git submodule update --init
uv sync --project backend
uv run --project backend egb setup-bench
```

`setup-bench` builds `data/bench-env`, the environment gaib runs in, from
gaib's `requirements-lock-cpu.txt` with Python 3.11.9.

```
cd frontend
npm install
```

## Run

```
uv run --project backend egb serve
```

In a second terminal:

```
cd frontend
npm run dev
```

The app is at `http://localhost:5173`. Vite forwards `/api` to the backend.

The API is at `http://127.0.0.1:8000/api`, with interactive docs at
`http://127.0.0.1:8000/docs`. Download a dataset before running on it:

```
curl -X POST http://127.0.0.1:8000/api/datasets/email-Eu-core/download
```

## Remote hardware

Declare SSH targets in `data/targets.yaml` (see `docs/remote.md`, section 2).
Connection details belong in `~/.ssh/config`; the app uses your keys and
agent and never asks for passwords. Then, once per target:

```
uv run --project backend egb remote setup <target>
```

This sends gaib at the pinned commit and builds the benchmark environment
on the remote machine; it needs the remote machine to reach PyPI. Real SSH
to another machine is untested; see `docs/remote.md`, section 13.

## Test

```
cd backend
uv run pytest
uv run pytest --data-dir=../data
```

The second command adds the end-to-end tests. The remote ones need a
`local-shell` stand-in target set up first:

```
uv run --project backend egb remote setup standin
```

with `standin` declared in `data/targets.yaml` as in `docs/remote.md`.

The local end-to-end test starts a server, runs
email-Eu-core with Louvain and PageRank, and compares the rows with a
direct `runner.py` run and with the paper's Mac results.
