import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from paper_fixture import GPU, MAC, SETTINGS, XEON, exploration_request
from test_e2e import events

from egb.agent import AgentSuggester, load_agent_settings
from egb.registry import Registry
from egb.settings import BUILT_IN_AGENT
from egb.suggesters import RuleSuggester, SuggestionResponse

AGENT = load_agent_settings(BUILT_IN_AGENT)
SERVER_START_SECONDS = 60


@pytest.fixture(scope="module", autouse=True)
def live(pytestconfig: pytest.Config) -> None:
    if not pytestconfig.getoption("--live-agent"):
        pytest.skip("Live agent tests call the Claude API; pass --live-agent.")
    if not os.environ.get(AGENT.api_key_env):
        pytest.skip(f"{AGENT.api_key_env} is not set.")


def report(title: str, response: SuggestionResponse) -> None:
    lines = [
        f"\n== {title}",
        f"suggester: {response.suggester}",
        f"fallback: {response.fallback_reason}",
    ]
    for call in response.calls:
        lines.append(
            f"call: {call.model}, {call.latency_seconds} s, {call.input_tokens} in, "
            f"{call.output_tokens} out, ${call.cost_usd}, stop {call.stop_reason}"
        )
    for s in response.suggestions:
        lines.append(
            f"PASSED #{s.rank} [{s.dimension}] {s.change.source.id} -> {s.change.target.id}"
        )
        lines += [
            f"  observation: {s.observation}",
            f"  hypothesis: {s.hypothesis}",
            f"  prediction: {s.predictions[0].text}",
            f"  rationale: {s.rationale}",
            f"  repairs: {s.repairs}",
        ]
    for r in response.rejected:
        lines.append(f"REJECTED {r.hypothesis}\n  reasons: {r.reasons}")
    print("\n".join(lines))


def changes(response: SuggestionResponse) -> set[tuple[str, str, str, str]]:
    return {
        (
            s.dimension,
            s.change.source.hardware,
            s.change.source.implementation,
            s.change.target.representation,
        )
        for s in response.suggestions
    }


def test_paper_exploration(pytestconfig: pytest.Config, registry: Registry) -> None:
    results = pytestconfig.getoption("--gaib-root").resolve() / "gaib" / "results"
    suggester = AgentSuggester(registry, SETTINGS, AGENT, RuleSuggester(registry, SETTINGS))
    response = asyncio.run(suggester.suggest(exploration_request(results, registry)))
    report("Paper initial exploration (fixture)", response)
    assert response.suggester == "agent"
    found = changes(response)
    assert any(
        d == "representation" and h == GPU and i == "pagerank-nx-cugraph" for d, h, i, _ in found
    )
    assert any(
        d == "algorithm" and h in (MAC, XEON) and i == "louvain-networkx" for d, h, i, _ in found
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_live_session(pytestconfig: pytest.Config, tmp_path: Path) -> None:
    data = pytestconfig.getoption("--data-dir")
    if data is None:
        pytest.skip("Live session test needs --data-dir.")
    port = free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "egb",
            "--gaib-root",
            str(pytestconfig.getoption("--gaib-root").resolve()),
            "--data-dir",
            str(data.resolve()),
            "--sessions-dir",
            str(tmp_path / "sessions"),
            "--targets",
            str(tmp_path / "no-targets.yaml"),
            "serve",
            "--port",
            str(port),
        ]
    )
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=600) as client:
            deadline = time.monotonic() + SERVER_START_SECONDS
            while True:
                try:
                    client.get("/api/hardware").raise_for_status()
                    break
                except httpx.TransportError:
                    assert time.monotonic() < deadline
                    time.sleep(0.5)
            session = client.post(
                "/api/sessions",
                json={
                    "name": "Agent check",
                    "question": "Which configuration should we use?",
                    "config": {
                        "tasks": ["community-detection", "centrality"],
                        "datasets": ["email-Eu-core", "p2p-Gnutella31"],
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
                        "metrics": ["end_to_end_s", "modularity", "pagerank_l1"],
                        "repetitions": 10,
                    },
                },
            ).json()
            sid = session["meta"]["id"]
            jobs = client.post(
                f"/api/sessions/{sid}/jobs",
                json={
                    "configurations": ["c1", "c2"],
                    "datasets": ["email-Eu-core", "p2p-Gnutella31"],
                    "kind": "exploratory",
                },
            ).json()
            assert events(client, jobs[0]["id"])[-1]["type"] == "finished"
            record = client.post(
                f"/api/sessions/{sid}/suggestions", json={"suggester": "agent"}
            ).json()
            response = SuggestionResponse.model_validate(record["response"])
            report("Live session on this Mac", response)
            hardware = client.get("/api/hardware").json()[0]["label"]
            assert response.suggester == "agent"
            assert any(
                d == "algorithm" and h == hardware and i == "louvain-networkx"
                for d, h, i, _ in changes(response)
            )
            print(json.dumps(client.get(f"/api/sessions/{sid}/summary").json()[0]["config"]))
    finally:
        server.terminate()
        server.wait()
