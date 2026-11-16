# empirical-graph-bench

A web app that walks a practitioner through hypothesis-driven graph
benchmarking (Algorithm 1 of the HARD paper): exploratory runs,
observations, recorded hypotheses and predictions, targeted experiments,
and a configuration decision with its evidence. It runs the benchmark code
from [HARD-graph-benchmark](https://github.com/nicoleschneider/HARD-graph-benchmark)
(gaib) unchanged, as a pinned submodule.

Status: backend core only. See `docs/architecture.md` and `docs/design.md`.

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

The API is at `http://127.0.0.1:8000/api`, with interactive docs at
`http://127.0.0.1:8000/docs`. Download a dataset before running on it:

```
curl -X POST http://127.0.0.1:8000/api/datasets/email-Eu-core/download
```

## Test

```
cd backend
uv run pytest
uv run pytest --data-dir=../data
```

The second command adds the end-to-end test. It starts a server, runs
email-Eu-core with Louvain and PageRank, and compares the rows with a
direct `runner.py` run and with the paper's Mac results.
