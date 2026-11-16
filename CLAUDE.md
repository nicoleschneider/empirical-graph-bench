# empirical-graph-bench

Web app for hypothesis-driven graph benchmarking. Backend is FastAPI in
`backend/`, frontend is React + TypeScript (Vite) in `frontend/`. The
benchmark code is gaib, from HARD-graph-benchmark, called and never
reimplemented.

- Architecture, registry, interfaces, storage and API: `docs/architecture.md`.
- Visual and copy rules: `docs/design.md`.

All UI work must follow `docs/design.md`, including its review checklist.

The timed benchmark path stays inside gaib. Changes here must not alter
timing boundaries, warm-up, seeds or scoring; see section 1 of
`docs/architecture.md`.

Pull requests go to `nicoleschneider/empirical-graph-bench`. Nothing goes to
`osullik/umd_thesis`.
