import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from paper_fixture import GPU, MAC, SETTINGS, XEON, exploration_request

from egb.agent import AgentSuggester, load_agent_settings
from egb.registry import Registry
from egb.sessions import LogEntry
from egb.settings import BUILT_IN_AGENT
from egb.suggesters import RuleSuggester, SuggestionRequest

KEY = "sk-test-not-a-real-key"
AGENT = load_agent_settings(BUILT_IN_AGENT)


def config(hardware: str, implementation: str, representation: str = "networkx-text") -> dict:
    return {
        "hardware": hardware,
        "implementation": implementation,
        "representation": representation,
    }


def hypothesis(baseline: dict, target: dict, dimension: str, **changes) -> dict:
    item = {
        "observation": "ETL is 87 % of GPU PageRank time on web-Stanford.",
        "dimension": dimension,
        "baseline": baseline,
        "target": target,
        "hypothesis": "Loading, not computation, makes GPU PageRank lose.",
        "prediction": {
            "metric": "end_to_end_s",
            "relation": "<",
            "threshold_mode": "reference",
            "reference": config(MAC, "pagerank-networkx"),
            "value": 0,
            "datasets": ["web-Stanford"],
        },
        "prediction_text": "GPU PageRank with cudf below 4.87 s on web-Stanford.",
        "rationale": "Loading dominates GPU time; cudf loads on the device.",
    }
    return item | changes


GPU_PAGERANK = config(GPU, "pagerank-nx-cugraph")
GPU_PAGERANK_F64 = config(GPU, "pagerank-nx-cugraph", "cudf-float64")


class FakeMessages:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            model=kwargs["model"],
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps({"hypotheses": self.payload}))],
            usage=SimpleNamespace(
                input_tokens=10_000,
                output_tokens=2_000,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
        )


def agent(registry: Registry, payload=(), error=None, environ=None):
    messages = FakeMessages(list(payload), error)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    suggester = AgentSuggester(
        registry,
        SETTINGS,
        AGENT,
        RuleSuggester(registry, SETTINGS),
        client_factory=lambda key, settings: client,
        environ={AGENT.api_key_env: KEY} if environ is None else environ,
    )
    return suggester, messages


@pytest.fixture(scope="module")
def request_(pytestconfig: pytest.Config, registry: Registry) -> SuggestionRequest:
    return exploration_request(
        pytestconfig.getoption("--gaib-root").resolve() / "gaib" / "results", registry
    )


def test_missing_key_falls_back_to_rules(registry: Registry, request_: SuggestionRequest) -> None:
    suggester, messages = agent(registry, environ={})
    response = asyncio.run(suggester.suggest(request_))
    assert response.suggester == "rules"
    assert response.fallback_reason == "Agent off: ANTHROPIC_API_KEY is not set."
    assert response.suggestions and messages.calls == []


def test_api_failure_falls_back(registry: Registry, request_: SuggestionRequest) -> None:
    error = anthropic.APIConnectionError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )
    response = asyncio.run(agent(registry, error=error)[0].suggest(request_))
    assert response.suggester == "rules"
    assert response.fallback_reason == "Agent call failed: no connection."


def test_request_body(registry: Registry, request_: SuggestionRequest) -> None:
    suggester, messages = agent(
        registry, [hypothesis(GPU_PAGERANK, GPU_PAGERANK_F64, "representation")]
    )
    asyncio.run(suggester.suggest(request_))
    body = messages.calls[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default"
    assert "temperature" not in body and "extra_body" not in body
    schema = body["output_config"]["format"]["schema"]
    hardware = schema["properties"]["hypotheses"]["items"]["properties"]["target"]["properties"][
        "hardware"
    ]
    assert hardware["enum"] == sorted([MAC, XEON, GPU])
    context = json.loads(body["messages"][0]["content"])
    assert set(context) == {"workload", "configurations", "summaries", "validity_flags", "trail"}
    assert KEY not in json.dumps(body)


def test_valid_suggestion_with_repair(registry: Registry, request_: SuggestionRequest) -> None:
    payload = [hypothesis(GPU_PAGERANK, GPU_PAGERANK_F64, "algorithm")]
    response = asyncio.run(agent(registry, payload)[0].suggest(request_))
    assert response.suggester == "agent"
    suggestion = response.suggestions[0]
    assert suggestion.source == "agent:claude-opus-5-5"
    assert suggestion.dimension == "representation"
    assert suggestion.repairs == ["Dimension corrected from algorithm to representation."]
    assert suggestion.predictions[0].prediction.reference.hardware == MAC
    assert response.calls[0].cost_usd == pytest.approx(0.08)


def test_invalid_suggestions_rejected(registry: Registry, request_: SuggestionRequest) -> None:
    mac_louvain = config(MAC, "louvain-networkx")
    payload = [
        hypothesis(
            config(MAC, "pagerank-networkx"),
            config(MAC, "pagerank-nx-cugraph", "cudf-float64"),
            "representation",
        ),
        hypothesis(GPU_PAGERANK, config(MAC, "pagerank-networkx", "networkx-text"), "hardware"),
        hypothesis(
            mac_louvain,
            config(MAC, "label-propagation-networkx"),
            "algorithm",
            rationale="Label propagation is a much faster community detection algorithm on this Apple CPU.",
            prediction=hypothesis(mac_louvain, mac_louvain, "algorithm")["prediction"]
            | {"reference": mac_louvain, "datasets": ["email-Eu-core"]},
        ),
    ]
    response = asyncio.run(agent(registry, payload)[0].suggest(request_))
    assert response.suggester == "rules"
    assert response.fallback_reason == "No agent suggestion passed validation."
    reasons = [r.reasons for r in response.rejected]
    assert "Target cannot run on any available hardware." in reasons[0]
    assert "Changes 2 settings: hardware, representation." in reasons[0]
    assert "Target already has results." in reasons[1]
    assert reasons[2] == ["Rationale has 13 words; the limit is 12."]


def test_reference_must_solve_same_task(registry: Registry, request_: SuggestionRequest) -> None:
    mac_louvain = config(MAC, "louvain-networkx")
    payload = [hypothesis(mac_louvain, config(MAC, "label-propagation-networkx"), "algorithm")]
    response = asyncio.run(agent(registry, payload)[0].suggest(request_))
    assert response.rejected[0].reasons == ["Reference solves a different task."]


def test_threshold_must_come_from_data(registry: Registry, request_: SuggestionRequest) -> None:
    def fixed(value: float) -> dict:
        prediction = hypothesis(GPU_PAGERANK, GPU_PAGERANK_F64, "representation")["prediction"]
        return prediction | {"threshold_mode": "value", "value": value}

    payload = [
        hypothesis(GPU_PAGERANK, GPU_PAGERANK_F64, "representation", prediction=fixed(4.0)),
        hypothesis(
            GPU_PAGERANK,
            config(GPU, "pagerank-nx-cugraph", "cudf-float32"),
            "representation",
            prediction=fixed(4.874379091022722),
        ),
    ]
    response = asyncio.run(agent(registry, payload)[0].suggest(request_))
    assert response.rejected[0].reasons == ["Threshold does not match any measured median."]
    accepted = response.suggestions[0]
    assert accepted.repairs == [
        f"Threshold tied to the {MAC}/pagerank-networkx/networkx-text median."
    ]


def test_repeat_of_trail_rejected(registry: Registry, request_: SuggestionRequest) -> None:
    recorded = LogEntry(
        seq=1,
        id="e1",
        recorded_at="2026-11-16T10:00:00-05:00",
        kind="hypothesis",
        text="Earlier.",
        iteration=1,
        suggestion={"change": {"source": GPU_PAGERANK, "target": GPU_PAGERANK_F64}},
        state_manifest=[],
        state_hash="sha256:x",
        prev_entry_hash=None,
        entry_hash="sha256:y",
    )
    payload = [hypothesis(GPU_PAGERANK, GPU_PAGERANK_F64, "representation")]
    with_history = request_.model_copy(update={"history": [recorded]})
    response = asyncio.run(agent(registry, payload)[0].suggest(with_history))
    assert response.rejected[0].reasons == ["Repeats a hypothesis in the trail."]


def test_key_never_logged(registry: Registry, request_: SuggestionRequest, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    response = asyncio.run(
        agent(registry, [hypothesis(GPU_PAGERANK, GPU_PAGERANK_F64, "representation")])[0].suggest(
            request_
        )
    )
    assert KEY not in caplog.text
    assert KEY not in response.model_dump_json()


def test_agent_settings_file_has_no_key() -> None:
    assert "sk-" not in Path(BUILT_IN_AGENT).read_text()


def test_representation_change_needs_end_to_end(
    registry: Registry, request_: SuggestionRequest
) -> None:
    prediction = hypothesis(GPU_PAGERANK, GPU_PAGERANK_F64, "representation")["prediction"] | {
        "metric": "etl_s"
    }
    payload = [hypothesis(GPU_PAGERANK, GPU_PAGERANK_F64, "representation", prediction=prediction)]
    response = asyncio.run(agent(registry, payload)[0].suggest(request_))
    assert response.rejected[0].reasons == [
        "Phase times differ by loading path; predict end-to-end time instead."
    ]
