import csv
import json
import math
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

TIMING_COLUMNS = {
    "timestamp",
    "etl_s",
    "processing_s",
    "total_s",
    "nmi_s",
    "modularity_s",
    "pagerank_score_s",
}
PAPER_RESULTS = Path("results/mac_m5max_20261019/results_cpu.csv")
SERVER_START_SECONDS = 60
JOB_SECONDS = 600
L1_RELATIVE_TOLERANCE = 1e-12


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def events(client: httpx.Client, job_id: str) -> list[dict]:
    collected = []
    with client.stream("GET", f"/api/jobs/{job_id}/events", timeout=JOB_SECONDS) as response:
        for line in response.iter_lines():
            if line.startswith("data: "):
                collected.append(json.loads(line[len("data: ") :]))
    return collected


@pytest.fixture(scope="module")
def data_dir(pytestconfig: pytest.Config) -> Path:
    path = pytestconfig.getoption("--data-dir")
    if path is None:
        pytest.skip("End-to-end test needs --data-dir with a built bench-env.")
    return path.resolve()


@pytest.fixture(scope="module")
def gaib_root(pytestconfig: pytest.Config) -> Path:
    return pytestconfig.getoption("--gaib-root").resolve()


@pytest.fixture(scope="module")
def client(
    data_dir: Path, gaib_root: Path, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[httpx.Client]:
    port = free_port()
    sessions = tmp_path_factory.mktemp("sessions")
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "egb",
            "--gaib-root",
            str(gaib_root),
            "--data-dir",
            str(data_dir),
            "--sessions-dir",
            str(sessions),
            "serve",
            "--port",
            str(port),
        ]
    )
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30) as http:
        deadline = time.monotonic() + SERVER_START_SECONDS
        while True:
            try:
                http.get("/api/hardware").raise_for_status()
                break
            except httpx.TransportError:
                if time.monotonic() > deadline or server.poll() is not None:
                    server.kill()
                    raise
                time.sleep(0.5)
        yield http
    server.terminate()
    server.wait()


@pytest.fixture(scope="module")
def finished_job(client: httpx.Client) -> dict:
    status = {d["id"]: d["state"] for d in client.get("/api/datasets").json()}
    if status["email-Eu-core"] != "verified":
        download = client.post("/api/datasets/email-Eu-core/download").json()
        assert events(client, download["id"])[-1]["type"] == "finished"
    session = client.post(
        "/api/sessions",
        json={
            "name": "email-Eu-core check",
            "question": "Do app runs match gaib's runner?",
            "config": {
                "tasks": ["community-detection", "centrality"],
                "datasets": ["email-Eu-core"],
                "candidates": [
                    {
                        "implementation": "louvain-networkx",
                        "representation": "networkx-text",
                        "exploratory": True,
                    },
                    {
                        "implementation": "pagerank-networkx",
                        "representation": "networkx-text",
                        "exploratory": True,
                    },
                ],
                "metrics": [
                    "etl_s",
                    "processing_s",
                    "end_to_end_s",
                    "modularity",
                    "predicted_communities",
                    "nmi",
                    "pagerank_l1",
                    "pagerank_top100",
                ],
                "repetitions": 2,
                "base_seed": 123,
            },
        },
    )
    assert session.status_code == 201, session.text
    session_id = session.json()["meta"]["id"]
    job = client.post(
        f"/api/sessions/{session_id}/jobs",
        json={"configurations": ["c1", "c2"], "datasets": ["email-Eu-core"], "kind": "exploratory"},
    )
    assert job.status_code == 202, job.text
    stream = events(client, job.json()["id"])
    return {"session_id": session_id, "spec": job.json(), "events": stream}


def comparable(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in TIMING_COLUMNS | {"pagerank_l1"}}


def same_l1(a: str, b: str) -> bool:
    if a == "" or b == "":
        return a == b
    return math.isclose(float(a), float(b), rel_tol=L1_RELATIVE_TOLERANCE)


def test_gpu_configurations_report_reason(client: httpx.Client) -> None:
    hardware = client.get("/api/hardware").json()[0]
    statuses = client.get("/api/configurations").json()
    blocked = [s for s in statuses if not s["runnable"]]
    if "cuda" in hardware["capabilities"]:
        pytest.skip("This machine has CUDA; nothing to report as blocked.")
    assert {(s["implementation"], s["representation"]) for s in blocked} == {
        ("louvain-nx-cugraph", "networkx-text"),
        ("louvain-nx-cugraph", "cudf-float32"),
        ("pagerank-nx-cugraph", "networkx-text"),
        ("pagerank-nx-cugraph", "cudf-float32"),
        ("pagerank-nx-cugraph", "cudf-float64"),
    }
    assert all(s["reasons"] and s["reasons"][0].startswith("Needs cuda.") for s in blocked)


def test_job_streams_progress(finished_job: dict) -> None:
    types = [e["type"] for e in finished_job["events"]]
    assert types[0] in {"queued", "started"}
    assert types[-1] == "finished", finished_job["events"][-1]
    completed = [e for e in finished_job["events"] if e["type"] == "run_completed"]
    assert [(e["data"]["configuration"], e["data"]["row"]["run_index"]) for e in completed] == [
        ("c1", "0"),
        ("c1", "1"),
        ("c2", "0"),
        ("c2", "1"),
    ]


def test_results_and_summary(client: httpx.Client, finished_job: dict) -> None:
    session_id = finished_job["session_id"]
    rows = client.get(f"/api/sessions/{session_id}/results").json()
    louvain = {r["seed"]: r for r in rows if r["configuration"] == "c1"}
    assert round(louvain[123]["nmi"], 4) == 0.5894
    summary = {
        s["configuration"]: s for s in client.get(f"/api/sessions/{session_id}/summary").json()
    }
    assert summary["c1"]["runs"] == 2
    assert set(summary["c1"]["metrics"]) == {
        "etl_s",
        "processing_s",
        "end_to_end_s",
        "modularity",
        "predicted_communities",
        "nmi",
    }
    assert set(summary["c2"]["metrics"]) == {
        "etl_s",
        "processing_s",
        "end_to_end_s",
        "pagerank_l1",
        "pagerank_top100",
    }
    environment = client.get(f"/api/sessions/{session_id}/environment").json()
    assert len(environment) == 1


def test_matches_gaib_runner(
    finished_job: dict, data_dir: Path, gaib_root: Path, tmp_path: Path
) -> None:
    spec = finished_job["spec"]
    invocation = spec["invocations"][0]
    argv = list(invocation["argv"])
    for flag, value in (
        ("--output-directory", tmp_path / "reports"),
        ("--results-csv", tmp_path / "results.csv"),
    ):
        argv[argv.index(flag) + 1] = str(value)
    (tmp_path / "work" / "cwd").mkdir(parents=True)
    subprocess.run(argv, cwd=tmp_path / "work" / "cwd", check=True, capture_output=True)
    with (tmp_path / "results.csv").open() as f:
        direct = list(csv.DictReader(f))
    app = [e["data"]["row"] for e in finished_job["events"] if e["type"] == "run_completed"]
    assert [comparable(r) for r in app] == [comparable(r) for r in direct]
    assert all(same_l1(a["pagerank_l1"], d["pagerank_l1"]) for a, d in zip(app, direct))


def test_matches_paper_rows(finished_job: dict, gaib_root: Path) -> None:
    with (gaib_root / "gaib" / PAPER_RESULTS).open() as f:
        paper = {
            (r["task"], r["seed"]): r for r in csv.DictReader(f) if r["dataset"] == "email-Eu-core"
        }
    columns = [
        "seed",
        "vertices",
        "edges",
        "predicted_communities",
        "nmi",
        "modularity",
        "pagerank_top100",
        "library_versions",
    ]
    for event in finished_job["events"]:
        if event["type"] == "run_completed":
            row = event["data"]["row"]
            expected = paper[(row["task"], row["seed"])]
            assert {c: row[c] for c in columns} == {c: expected[c] for c in columns}
            assert same_l1(row["pagerank_l1"], expected["pagerank_l1"])
