# empirical-graph-bench

A web app that walks a practitioner through hypothesis-driven graph
benchmarking (Algorithm 1 of the HARD paper): exploratory runs,
observations, recorded hypotheses and predictions, targeted experiments,
and a configuration decision with its evidence. It runs the benchmark code
from [HARD-graph-benchmark](https://github.com/nicoleschneider/HARD-graph-benchmark)
(gaib) unchanged.

Status: skeleton only. See `docs/architecture.md` and `docs/design.md`.

## Setup

Requires Python 3.11 with [uv](https://docs.astral.sh/uv/), and Node.js 24.

```
cd backend
uv sync
```

```
cd frontend
npm install
```

## Run

```
cd frontend
npm run dev
```

The backend has no app yet.
