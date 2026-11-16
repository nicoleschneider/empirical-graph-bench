import csv
from pathlib import Path

from egb.analysis import ConfigKey, Run, analyse, load_analysis_settings, runs_from_rows
from egb.datasets import dataset_properties
from egb.registry import Registry
from egb.settings import BUILT_IN_ANALYSIS
from egb.suggesters import SuggestionRequest, observe, runnable_alternatives

EXPLORATION = [
    "mac_m5max_20261019/results_cpu.csv",
    "mac_m5max_additions_20261020/results_cpu.csv",
    "cluster_tron69_20261019/results_cpu.csv",
    "cluster_tron69_20261019/results_gpu.csv",
    "cluster_additions_7926352/results_cpu.csv",
    "cluster_additions_7926352/results_gpu.csv",
]
MAC, XEON, GPU = "macbook-m5max-48gb", "nexus-xeon4216-cpu", "nexus-2080ti-gpu"
CAPABILITIES = {MAC: ["cpu"], XEON: ["cpu"], GPU: ["cpu", "cuda", "cudf"]}
SPECS = {
    MAC: ("Apple M5 Max", []),
    XEON: ("Intel Xeon Silver 4216", []),
    GPU: ("Intel Xeon Silver 4216", ["NVIDIA GeForce RTX 2080 Ti"]),
}
SETTINGS = load_analysis_settings(BUILT_IN_ANALYSIS)


def key(hardware: str, implementation: str, representation: str = "networkx-text") -> ConfigKey:
    return ConfigKey(
        hardware=hardware, implementation=implementation, representation=representation
    )


def load(results: Path, names: list[str], registry: Registry) -> list[Run]:
    rows = []
    for name in names:
        with (results / name).open(newline="") as f:
            rows += list(csv.DictReader(f))
    return runs_from_rows(rows, registry)


def configurations(registry: Registry) -> list[dict]:
    rows = []
    for hardware, available in CAPABILITIES.items():
        for implementation in registry.implementations.values():
            for variant in implementation.variants:
                needed = {"cpu", *registry.requirements(implementation.id, variant.representation)}
                missing = sorted(needed - set(available))
                rows.append(
                    {
                        "hardware": hardware,
                        "hardware_status": "reachable",
                        "cpu": SPECS[hardware][0],
                        "gpus": SPECS[hardware][1],
                        "implementation": implementation.id,
                        "representation": variant.representation,
                        "algorithm": implementation.algorithm,
                        "task": registry.task_of(implementation.id).id,
                        "runnable": not missing,
                        "reasons": [f"Needs {', '.join(missing)}."] if missing else [],
                    }
                )
    return rows


def exploration_request(results: Path, registry: Registry) -> SuggestionRequest:
    analysis = analyse(load(results, EXPLORATION, registry), SETTINGS)
    properties = dataset_properties(results / "dataset_properties.csv")
    datasets = sorted({s.dataset for s in analysis.summaries})
    return SuggestionRequest(
        analysis=analysis,
        observations=observe(analysis, registry),
        alternatives=runnable_alternatives(registry, CAPABILITIES),
        history=[],
        workload={
            "tasks": {t: {"name": registry.tasks[t].name} for t in registry.tasks},
            "datasets": {
                d: {
                    "vertices": registry.datasets[d].vertices,
                    "edges": registry.datasets[d].edges,
                    "directed": registry.datasets[d].directed,
                    "properties": properties.get(d),
                }
                for d in datasets
            },
        },
        configurations=configurations(registry),
    )
