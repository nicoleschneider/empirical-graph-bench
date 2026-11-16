import json
from pathlib import Path

import pytest

from egb.registry import Registry, load_registry
from egb.settings import BUILT_IN_ENTRIES


def test_paper_entries(registry: Registry) -> None:
    assert set(registry.tasks) == {"community-detection", "centrality"}
    assert set(registry.algorithms) == {"louvain", "label-propagation", "pagerank"}
    assert set(registry.representations) == {"networkx-text", "cudf-float32", "cudf-float64"}
    gaib_tasks = {v.gaib_task for i in registry.implementations.values() for v in i.variants}
    assert gaib_tasks == {
        "NX_LOUVAIN",
        "NX_LOUVAIN_GPU",
        "NX_LOUVAIN_GPU_CUDF",
        "NX_LPA",
        "NX_PAGERANK",
        "NX_PAGERANK_GPU",
        "NX_PAGERANK_GPU_CUDF",
        "NX_PAGERANK_GPU_CUDF_F64",
    }


def test_datasets_match_gaib_manifest(registry: Registry, pytestconfig: pytest.Config) -> None:
    manifest_path = pytestconfig.getoption("--gaib-root") / "gaib" / "datasets" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert set(registry.datasets) == set(manifest)
    for name, record in manifest.items():
        hashes = {f["file"]: (f["raw_file"], f["sha256"]) for f in record["files"]}
        dataset = registry.datasets[name]
        assert hashes[dataset.graph.file] == (dataset.graph.raw_file, dataset.graph.sha256)
        if dataset.ground_truth is not None:
            assert hashes[dataset.ground_truth.file] == (
                dataset.ground_truth.raw_file,
                dataset.ground_truth.sha256,
            )


def test_cudf_needs_cuda_and_cudf(registry: Registry) -> None:
    assert registry.requirements("pagerank-nx-cugraph", "cudf-float64") == ["cuda", "cudf"]
    assert registry.requirements("pagerank-networkx", "networkx-text") == []


def test_duplicate_entry_rejected(tmp_path: Path) -> None:
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "centrality.yaml").write_text(
        "id: centrality\nname: Again\noutput: scores\n"
    )
    with pytest.raises(ValueError, match="defined twice"):
        load_registry([BUILT_IN_ENTRIES, tmp_path])


def test_unknown_reference_rejected(tmp_path: Path) -> None:
    (tmp_path / "algorithms").mkdir()
    (tmp_path / "algorithms" / "bfs.yaml").write_text(
        "id: bfs\nname: BFS\ntask: traversal\norientation: dataset\nparameters: {}\n"
    )
    with pytest.raises(ValueError, match="unknown task traversal"):
        load_registry([BUILT_IN_ENTRIES, tmp_path])
