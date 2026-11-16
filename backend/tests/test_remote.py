import csv
import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import yaml
from test_e2e import comparable, events, same_l1

SERVER_START_SECONDS = 60
PROBE_SECONDS = 120
LONG_REPETITIONS = 300
PAPER_RESULTS = Path("results/mac_m5max_20261019/results_cpu.csv")
SUPPORTS = ["louvain-networkx", "pagerank-networkx"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def standin(pytestconfig: pytest.Config) -> Path:
    data = pytestconfig.getoption("--data-dir")
    if data is None:
        pytest.skip("Remote test needs --data-dir with a stand-in set up by egb remote setup.")
    workdir = (data / "remote-standin").resolve()
    if not (workdir / "bench-env" / "bin" / "python").exists():
        pytest.skip(f"No stand-in environment at {workdir}. Run egb remote setup first.")
    return workdir


@pytest.fixture(scope="module")
def gaib_root(pytestconfig: pytest.Config) -> Path:
    return pytestconfig.getoption("--gaib-root").resolve()


def broken_workdir(root: Path, standin: Path) -> Path:
    workdir = root / "broken"
    (workdir / "bench-env" / "bin").mkdir(parents=True)
    (workdir / "gaib").symlink_to(standin / "gaib")
    python = workdir / "bench-env" / "bin" / "python"
    python.write_text(
        "#!/bin/bash\n"
        'case "$*" in *runner.py*) echo "boom" >&2; exit 7;; esac\n'
        f'exec {standin / "bench-env" / "bin" / "python"} "$@"\n'
    )
    python.chmod(0o755)
    return workdir


@pytest.fixture(scope="module")
def client(
    pytestconfig, standin: Path, gaib_root: Path, tmp_path_factory
) -> Iterator[httpx.Client]:
    root = tmp_path_factory.mktemp("remote")
    python = (standin / "bench-env" / "bin" / "python").resolve()
    base = {
        "transport": "local-shell",
        "python": str(python),
        "supports": SUPPORTS,
        "poll_seconds": 1,
    }
    targets = {
        "targets": [
            {"name": "standin", "workdir": str(standin), **base},
            {"name": "broken", "workdir": str(broken_workdir(root, standin)), **base},
            {"name": "short", "workdir": str(standin), "max_job_minutes": 0.02, **base},
            {
                "name": "nowhere",
                "host": "localhost",
                "workdir": "/tmp/egb-nowhere",
                "supports": SUPPORTS,
                "connect_timeout_seconds": 3,
            },
        ]
    }
    (root / "targets.yaml").write_text(yaml.safe_dump(targets))
    port = free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "egb",
            "--gaib-root",
            str(gaib_root),
            "--data-dir",
            str(pytestconfig.getoption("--data-dir").resolve()),
            "--sessions-dir",
            str(root / "sessions"),
            "--targets",
            str(root / "targets.yaml"),
            "serve",
            "--port",
            str(port),
        ]
    )
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60) as http:
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
        deadline = time.monotonic() + PROBE_SECONDS
        while any(h["status"] == "checking" for h in http.get("/api/hardware").json()):
            assert time.monotonic() < deadline, "targets still checking"
            time.sleep(1)
        yield http
    server.terminate()
    server.wait()


@pytest.fixture(scope="module")
def session_id(client: httpx.Client) -> str:
    candidates = [
        {
            "hardware": "standin",
            "implementation": "louvain-networkx",
            "representation": "networkx-text",
            "exploratory": True,
        },
        {
            "hardware": "standin",
            "implementation": "pagerank-networkx",
            "representation": "networkx-text",
            "exploratory": True,
        },
        {
            "hardware": "broken",
            "implementation": "louvain-networkx",
            "representation": "networkx-text",
        },
        {
            "hardware": "short",
            "implementation": "louvain-networkx",
            "representation": "networkx-text",
        },
        {
            "hardware": "nowhere",
            "implementation": "louvain-networkx",
            "representation": "networkx-text",
        },
        {
            "hardware": "local",
            "implementation": "louvain-networkx",
            "representation": "networkx-text",
        },
    ]
    response = client.post(
        "/api/sessions",
        json={
            "name": "Remote check",
            "question": "Do remote runs match?",
            "config": {
                "tasks": ["community-detection", "centrality"],
                "datasets": ["email-Eu-core"],
                "candidates": candidates,
                "metrics": ["end_to_end_s", "modularity", "nmi", "pagerank_l1"],
                "repetitions": 2,
                "base_seed": 123,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["meta"]["id"]


def start(client: httpx.Client, session_id: str, configurations: list[str], **extra) -> list[dict]:
    response = client.post(
        f"/api/sessions/{session_id}/jobs",
        json={
            "configurations": configurations,
            "datasets": ["email-Eu-core"],
            "kind": "exploratory",
            **extra,
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


@pytest.fixture(scope="module")
def remote_job(client: httpx.Client, session_id: str) -> dict:
    spec = start(client, session_id, ["c1", "c2"])
    assert [s["hardware"] for s in spec] == ["standin"]
    return {"spec": spec[0], "events": events(client, spec[0]["id"])}


def test_target_statuses(client: httpx.Client) -> None:
    hardware = {h["id"]: h for h in client.get("/api/hardware").json()}
    assert hardware["standin"]["status"] == "reachable"
    assert hardware["standin"]["specs"]["hostname"]
    assert hardware["broken"]["status"] == "reachable"
    assert hardware["nowhere"]["status"] == "unavailable"
    assert hardware["nowhere"]["reason"] == "Host unreachable."
    statuses = [c for c in client.get("/api/configurations").json() if c["hardware"] == "standin"]
    blocked = {c["implementation"]: c["reasons"][0] for c in statuses if not c["runnable"]}
    assert blocked["label-propagation-networkx"] == "Not in this target's supports list."
    assert blocked["louvain-nx-cugraph"] == "Not in this target's supports list."


def test_unreachable_target_rejected(client: httpx.Client, session_id: str) -> None:
    response = client.post(
        f"/api/sessions/{session_id}/jobs",
        json={"configurations": ["c5"], "datasets": ["email-Eu-core"], "kind": "exploratory"},
    )
    assert response.status_code == 400
    assert "Host unreachable." in response.json()["detail"]


def test_status_updates(remote_job: dict) -> None:
    types = [e["type"] for e in remote_job["events"]]
    assert types[0] == "queued"
    assert types.index("started") < types.index("running") < types.index("run_completed")
    assert types[-1] == "finished", remote_job["events"][-1]
    assert sum(t == "run_completed" for t in types) == 4


def test_rows_match_gaib_runner(remote_job: dict, tmp_path: Path, gaib_root: Path) -> None:
    rows = [e["data"]["row"] for e in remote_job["events"] if e["type"] == "run_completed"]
    assert {r["hardware"] for r in rows} == {"standin"}
    invocation = remote_job["spec"]["invocations"][0]
    argv = list(invocation["argv"])
    for flag, value in (
        ("--output-directory", tmp_path / "reports"),
        ("--results-csv", tmp_path / "results.csv"),
    ):
        argv[argv.index(flag) + 1] = str(value)
    (tmp_path / "cwd").mkdir()
    subprocess.run(argv, cwd=tmp_path / "cwd", check=True, capture_output=True)
    with (tmp_path / "results.csv").open() as f:
        direct = list(csv.DictReader(f))
    assert [comparable(r) for r in rows] == [comparable(r) for r in direct]
    assert all(same_l1(a["pagerank_l1"], d["pagerank_l1"]) for a, d in zip(rows, direct))
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
    ]
    for row in rows:
        assert {c: row[c] for c in columns} == {
            c: paper[(row["task"], row["seed"])][c] for c in columns
        }


def test_results_and_provenance(client: httpx.Client, session_id: str, remote_job: dict) -> None:
    rows = client.get(f"/api/sessions/{session_id}/results").json()
    remote = [r for r in rows if r["job_id"] == remote_job["spec"]["id"]]
    assert {r["configuration"] for r in remote} == {"c1", "c2"}
    summary = client.get(f"/api/sessions/{session_id}/summary").json()
    assert {s["config"]["hardware"] for s in summary} >= {"standin"}
    environments = client.get(f"/api/sessions/{session_id}/environment").json()
    snapshot = next(e for e in environments.values() if e.get("target") == "standin")
    assert snapshot["runner"] == "local-shell"
    assert snapshot["hostname"]
    assert snapshot["specs"]["cpu"]
    assert snapshot["gaib"]["commit"].startswith("dbb9903")
    assert snapshot["gaib"]["uncommitted_changes"] == 0
    assert snapshot["worker_sha256"]
    assert snapshot["lock_file"]["sha256"]
    job = client.get(f"/api/jobs/{remote_job['spec']['id']}").json()
    assert job["status"] == "finished"
    finished = remote_job["events"][-1]["data"]["remote"]
    assert finished["lock_acquired_at"] and finished["finished_at"] and finished["hostname"]


def test_saved_outputs_arrive(client: httpx.Client, session_id: str, remote_job: dict) -> None:
    validity = client.get(
        f"/api/sessions/{session_id}/validity",
        params={
            "targeted": "standin/louvain-networkx/networkx-text",
            "baseline": "standin/louvain-networkx/networkx-text",
            "dataset": "email-Eu-core",
        },
    )
    assert validity.status_code == 200, validity.text
    assert validity.json()["source"] == "saved_outputs"
    assert validity.json()["identical"] is True


def test_job_failure(client: httpx.Client, session_id: str) -> None:
    spec = start(client, session_id, ["c3"])[0]
    stream = events(client, spec["id"])
    assert stream[-1]["type"] == "failed"
    assert stream[-1]["data"]["reason"] == "gaib exited with code 7."
    assert "boom" in stream[-1]["data"]["tail"]


def test_cancellation(client: httpx.Client, session_id: str) -> None:
    spec = start(client, session_id, ["c1"], repetitions=LONG_REPETITIONS)[0]
    with client.stream("GET", f"/api/jobs/{spec['id']}/events", timeout=PROBE_SECONDS) as response:
        for line in response.iter_lines():
            if line.startswith("data: ") and json.loads(line[6:])["type"] == "run_completed":
                break
    assert client.delete(f"/api/jobs/{spec['id']}").status_code == 202
    stream = events(client, spec["id"])
    assert stream[-1]["type"] == "cancelled"
    completed = sum(e["type"] == "run_completed" for e in stream)
    assert 0 < completed < LONG_REPETITIONS
    leftover = subprocess.run(
        ["pgrep", "-f", spec["id"]], check=False, capture_output=True, text=True
    )
    assert leftover.stdout.strip() == ""


def test_timeout(client: httpx.Client, session_id: str) -> None:
    spec = start(client, session_id, ["c4"], repetitions=LONG_REPETITIONS)[0]
    stream = events(client, spec["id"])
    assert stream[-1]["type"] == "failed"
    assert stream[-1]["data"]["reason"] == "Stopped after 0.02 minutes."


def test_mixed_request_splits_by_hardware(client: httpx.Client, session_id: str) -> None:
    specs = start(client, session_id, ["c1", "c6"], repetitions=1)
    assert sorted(s["hardware"] for s in specs) == ["local", "standin"]
    for spec in specs:
        assert events(client, spec["id"])[-1]["type"] == "finished"
