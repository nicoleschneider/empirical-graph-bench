import asyncio
import csv
from pathlib import Path

import pytest

from egb.analysis import ConfigKey, Run, analyse, load_analysis_settings, runs_from_rows
from egb.predictions import Prediction, evaluate
from egb.registry import Registry
from egb.settings import BUILT_IN_ANALYSIS
from egb.suggesters import RuleSuggester, SuggestionRequest, observe, runnable_alternatives
from egb.validity import check_validity, read_cross_paths

EXPLORATION = [
    "mac_m5max_20261019/results_cpu.csv",
    "mac_m5max_additions_20261020/results_cpu.csv",
    "cluster_tron69_20261019/results_cpu.csv",
    "cluster_tron69_20261019/results_gpu.csv",
    "cluster_additions_7926352/results_cpu.csv",
    "cluster_additions_7926352/results_gpu.csv",
]
LOUVAIN_CUDF = "cluster_tron68_louvain_cudf_20261022"
LPA = "mac_m5max_lpa_20261022/results_cpu.csv"
MAC, XEON, GPU = "macbook-m5max-48gb", "nexus-xeon4216-cpu", "nexus-2080ti-gpu"
CAPABILITIES = {MAC: ["cpu"], XEON: ["cpu"], GPU: ["cpu", "cuda", "cudf"]}
SETTINGS = load_analysis_settings(BUILT_IN_ANALYSIS)


def key(hardware: str, implementation: str, representation: str = "networkx-text") -> ConfigKey:
    return ConfigKey(
        hardware=hardware, implementation=implementation, representation=representation
    )


@pytest.fixture(scope="module")
def results(pytestconfig: pytest.Config) -> Path:
    return pytestconfig.getoption("--gaib-root").resolve() / "gaib" / "results"


def load(results: Path, names: list[str], registry: Registry) -> list[Run]:
    rows = []
    for name in names:
        with (results / name).open(newline="") as f:
            rows += list(csv.DictReader(f))
    return runs_from_rows(rows, registry)


@pytest.fixture(scope="module")
def exploration(results: Path, registry: Registry) -> list[Run]:
    return load(results, EXPLORATION, registry)


@pytest.fixture(scope="module")
def suggestions(exploration: list[Run], registry: Registry):
    analysis = analyse(exploration, SETTINGS)
    request = SuggestionRequest(
        analysis=analysis,
        observations=observe(analysis, registry),
        alternatives=runnable_alternatives(registry, CAPABILITIES),
        history=[],
    )
    return asyncio.run(RuleSuggester(registry, SETTINGS).suggest(request)).suggestions


def test_summary_matches_paper_medians(exploration: list[Run]) -> None:
    summaries = {(s.config, s.dataset): s for s in analyse(exploration, SETTINGS).summaries}
    web = summaries[(key(MAC, "pagerank-networkx"), "web-Stanford")]
    assert round(web.end_to_end_s.median, 2) == 4.87
    assert (
        round(summaries[(key(MAC, "louvain-networkx"), "email-Eu-core")].end_to_end_s.median, 3)
        == 0.057
    )


def test_representation_change_for_gpu_pagerank(suggestions) -> None:
    found = [
        s
        for s in suggestions
        if s.rule == "etl-dominates" and s.change.source == key(GPU, "pagerank-nx-cugraph")
    ]
    assert len(found) == 1
    suggestion = found[0]
    assert suggestion.change.target == key(GPU, "pagerank-nx-cugraph", "cudf-float64")
    prediction = suggestion.predictions[0].prediction
    assert prediction.reference == key(MAC, "pagerank-networkx")
    assert "4.87 s on web-Stanford" in suggestion.predictions[0].text


def test_algorithm_change_for_cpu_louvain(suggestions) -> None:
    found = {
        s.change.source.hardware: s
        for s in suggestions
        if s.rule == "processing-dominates" and s.change.kind == "algorithm"
    }
    assert set(found) == {MAC, XEON}
    for hardware, suggestion in found.items():
        assert suggestion.change.source == key(hardware, "louvain-networkx")
        assert suggestion.change.target == key(hardware, "label-propagation-networkx")
        assert [p.prediction.metric for p in suggestion.predictions] == [
            "end_to_end_s",
            "modularity",
        ]


def test_louvain_tie_matches_paper(
    results: Path, exploration: list[Run], registry: Registry
) -> None:
    runs = exploration + load(results, [f"{LOUVAIN_CUDF}/results_gpu_cudf_louvain.csv"], registry)
    prediction = Prediction(
        metric="end_to_end_s",
        subject=key(GPU, "louvain-nx-cugraph", "cudf-float32"),
        relation="<",
        reference=key(MAC, "louvain-networkx"),
        datasets=["email-Eu-core"],
    )
    evaluation = evaluate(prediction, runs, SETTINGS)
    ratio = evaluation.datasets[0].ratio
    assert evaluation.status == "narrow"
    assert evaluation.symbol == "≈"
    assert (round(ratio.low, 2), round(ratio.high, 2)) == (0.85, 1.11)


def test_label_propagation_prediction_refuted(
    results: Path, exploration: list[Run], registry: Registry
) -> None:
    runs = exploration + load(results, [LPA], registry)
    prediction = Prediction(
        metric="end_to_end_s",
        subject=key(MAC, "label-propagation-networkx"),
        relation="<",
        reference=key(GPU, "louvain-nx-cugraph"),
        datasets=["email-Eu-core", "p2p-Gnutella31", "com-Amazon", "web-Stanford", "wiki-topcats"],
    )
    evaluation = evaluate(prediction, runs, SETTINGS)
    assert evaluation.status == "refuted"
    assert {d.dataset for d in evaluation.datasets if d.status == "held"} == {"email-Eu-core"}


def test_louvain_validity_matches_paper(
    results: Path, exploration: list[Run], registry: Registry
) -> None:
    datasets = {d.results_name: d.id for d in registry.datasets.values()}
    records = read_cross_paths(results / LOUVAIN_CUDF / "cross_path_louvain.csv", datasets)
    targeted, baseline = (
        key(GPU, "louvain-nx-cugraph", "cudf-float32"),
        key(GPU, "louvain-nx-cugraph"),
    )
    found = {
        d: check_validity(targeted, baseline, d, exploration, registry, records, [], None, SETTINGS)
        for d in ("email-Eu-core", "web-Stanford", "com-Amazon", "p2p-Gnutella31")
    }
    assert found["email-Eu-core"].identical
    assert {d: round(v.nmi_between, 2) for d, v in found.items() if d != "email-Eu-core"} == {
        "web-Stanford": 0.93,
        "com-Amazon": 0.80,
        "p2p-Gnutella31": 0.24,
    }
    assert found["web-Stanford"].source == "cross_path"
    assert not any("Precision" in effect for effect in found["web-Stanford"].side_effects)


def test_no_hardware_change_when_accelerated_path_measured(suggestions) -> None:
    assert not [s for s in suggestions if s.change.kind == "hardware"]


def test_crossover_suggests_broader_datasets(suggestions) -> None:
    found = [s for s in suggestions if s.rule == "dataset-dependent"]
    assert len(found) == 1
    assert "16,706" in found[0].hypothesis
