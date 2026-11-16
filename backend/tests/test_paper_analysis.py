import asyncio
from pathlib import Path

import pytest
from paper_fixture import CAPABILITIES, EXPLORATION, GPU, MAC, SETTINGS, XEON, key, load

from egb.analysis import Run, analyse
from egb.predictions import Prediction, evaluate
from egb.registry import Registry
from egb.suggesters import RuleSuggester, SuggestionRequest, observe, runnable_alternatives
from egb.validity import check_validity, read_cross_paths

LOUVAIN_CUDF = "cluster_tron68_louvain_cudf_20261022"
LPA = "mac_m5max_lpa_20261022/results_cpu.csv"


@pytest.fixture(scope="module")
def results(pytestconfig: pytest.Config) -> Path:
    return pytestconfig.getoption("--gaib-root").resolve() / "gaib" / "results"


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
    assert "web-Stanford" in prediction.datasets
    assert suggestion.predictions[0].text == (
        "nexus-2080ti-gpu PageRank: end-to-end time below "
        "macbook-m5max-48gb PageRank medians on 5 datasets."
    )


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
        metrics = [p.prediction.metric for p in suggestion.predictions]
        assert set(metrics) == {"end_to_end_s", "modularity"}
        assert metrics[-1] == "modularity"
    mac_time = found[MAC].predictions[0]
    assert mac_time.prediction.reference == key(GPU, "louvain-nx-cugraph")
    assert "email-Eu-core" in mac_time.prediction.datasets


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
