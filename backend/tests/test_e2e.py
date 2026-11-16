import csv
import json
import math
import os
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
            "--targets",
            str(sessions / "no-targets.yaml"),
            "serve",
            "--port",
            str(port),
        ],
        env={k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"},
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
                    {
                        "implementation": "label-propagation-networkx",
                        "representation": "networkx-text",
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
    stream = events(client, job.json()[0]["id"])
    return {"session_id": session_id, "spec": job.json()[0], "events": stream}


def comparable(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in TIMING_COLUMNS | {"pagerank_l1"}}


def same_l1(a: str, b: str) -> bool:
    if a == "" or b == "":
        return a == b
    return math.isclose(float(a), float(b), rel_tol=L1_RELATIVE_TOLERANCE)


def test_gpu_configurations_report_reason(client: httpx.Client) -> None:
    hardware = client.get("/api/hardware").json()[0]
    statuses = client.get("/api/configurations").json()
    blocked = [s for s in statuses if not s["runnable"] and s["hardware"] == "local"]
    if "cuda" in hardware["capabilities"]:
        pytest.skip("This machine has CUDA; nothing to report as blocked.")
    assert {(s["implementation"], s["representation"]) for s in blocked} == {
        ("louvain-nx-cugraph", "networkx-text"),
        ("louvain-nx-cugraph", "cudf-float32"),
        ("pagerank-nx-cugraph", "networkx-text"),
        ("pagerank-nx-cugraph", "cudf-float32"),
        ("pagerank-nx-cugraph", "cudf-float64"),
    }
    assert all(s["reasons"] and s["reasons"][0].startswith("Needs CUDA") for s in blocked)


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
    hardware = client.get("/api/hardware").json()[0]["label"]
    summary = {
        s["config"]["implementation"]: s
        for s in client.get(f"/api/sessions/{session_id}/summary").json()
    }
    assert summary["louvain-networkx"]["runs"] == 2
    assert summary["louvain-networkx"]["config"]["hardware"] == hardware
    assert set(summary["louvain-networkx"]["effectiveness"]) == {
        "modularity",
        "predicted_communities",
        "nmi",
    }
    assert set(summary["pagerank-networkx"]["effectiveness"]) == {"pagerank_l1", "pagerank_top100"}
    assert 0 < summary["pagerank-networkx"]["etl_share"] < 1
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


def key(hardware: str, implementation: str) -> str:
    return f"{hardware}/{implementation}/networkx-text"


def test_analysis_endpoints(client: httpx.Client, finished_job: dict) -> None:
    session_id = finished_job["session_id"]
    hardware = client.get("/api/hardware").json()[0]["label"]
    analysis = client.get(f"/api/sessions/{session_id}/analysis").json()
    assert {b["dominant"] for b in analysis["bottlenecks"]} <= {"etl", "processing"}
    observations = client.get(f"/api/sessions/{session_id}/observations").json()
    assert observations and all(o["statement"] for o in observations)
    comparisons = client.get(
        f"/api/sessions/{session_id}/comparisons",
        params={"baseline": key(hardware, "louvain-networkx")},
    )
    assert comparisons.status_code == 200, comparisons.text
    rules = client.post(
        f"/api/sessions/{session_id}/suggestions", json={"suggester": "rules"}
    ).json()
    assert rules["response"]["status"] == "complete"
    stored = client.get(f"/api/sessions/{session_id}/suggestions/{rules['id']}").json()
    assert stored == rules
    agent = client.post(
        f"/api/sessions/{session_id}/suggestions", json={"suggester": "agent"}
    ).json()
    assert agent["response"]["suggester"] == "rules"
    assert agent["response"]["fallback_reason"] == "Agent off: ANTHROPIC_API_KEY is not set."


def test_targeted_job_validity_and_evaluation(client: httpx.Client, finished_job: dict) -> None:
    session_id = finished_job["session_id"]
    hardware = client.get("/api/hardware").json()[0]["label"]
    hypothesis = client.post(
        f"/api/sessions/{session_id}/log",
        json={"kind": "hypothesis", "text": "Label propagation is faster.", "iteration": 1},
    ).json()
    prediction = client.post(
        f"/api/sessions/{session_id}/log",
        json={
            "kind": "prediction",
            "text": "Label propagation end-to-end time below Louvain on email-Eu-core.",
            "iteration": 1,
            "refers_to": [hypothesis["id"]],
            "prediction": {
                "metric": "end_to_end_s",
                "subject": {
                    "hardware": hardware,
                    "implementation": "label-propagation-networkx",
                    "representation": "networkx-text",
                },
                "relation": "<",
                "reference": {
                    "hardware": hardware,
                    "implementation": "louvain-networkx",
                    "representation": "networkx-text",
                },
                "datasets": ["email-Eu-core"],
            },
        },
    )
    assert prediction.status_code == 201, prediction.text
    job = client.post(
        f"/api/sessions/{session_id}/jobs",
        json={
            "configurations": ["c3"],
            "datasets": ["email-Eu-core"],
            "kind": "targeted",
            "hypothesis": hypothesis["id"],
            "baseline": "c1",
        },
    )
    assert job.status_code == 202, job.text
    assert events(client, job.json()[0]["id"])[-1]["type"] == "finished"
    evaluation = client.post(
        f"/api/sessions/{session_id}/log/{prediction.json()['id']}/evaluate"
    ).json()
    assert evaluation["status"] in {"held", "refuted", "narrow"}
    assert evaluation["datasets"][0]["ratio"]["resamples"] == 10000
    validity = client.get(f"/api/jobs/{job.json()[0]['id']}/validity")
    assert validity.status_code == 200, validity.text
    result = validity.json()[0]
    assert result["source"] == "saved_outputs"
    assert result["identical"] is False
    assert 0 <= result["nmi_between"] <= 1
    assert "Algorithm changes from Louvain to Label propagation." in result["side_effects"]
    assert client.get(f"/api/sessions/{session_id}/log/verify").json()["problems"] == []
