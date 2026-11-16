from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    name: str


class Task(Entry):
    output: Literal["communities", "scores"]


class Algorithm(Entry):
    task: str
    orientation: Literal["undirected", "dataset"]
    parameters: dict[str, float | int | str | None]


class Variant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    representation: str
    driver: Literal["gaib-runner", "gaib-representation-test"]
    gaib_task: str
    definition_task: str | None = None
    gpu: bool = False
    script_task: Literal["louvain", "pagerank"] | None = None


class Implementation(Entry):
    algorithm: str
    library: str
    backend: str
    requires: list[str]
    variants: list[Variant]


class Representation(Entry):
    loader: str
    precision: Literal["float32", "float64"]
    requires: list[str]


class DatasetFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file: str
    raw_file: str
    url: str
    sha256: str


class Dataset(Entry):
    directory: str
    directed: bool
    vertices: int
    edges: int
    graph: DatasetFile
    ground_truth: DatasetFile | None = None

    @property
    def results_name(self) -> str:
        return Path(self.graph.file).stem


class Metric(Entry):
    unit: str
    better: Literal["lower", "higher", "none"]
    columns: list[str]
    tasks: list[str]
    requires_ground_truth: bool


KINDS: dict[str, type[Entry]] = {
    "tasks": Task,
    "algorithms": Algorithm,
    "implementations": Implementation,
    "representations": Representation,
    "datasets": Dataset,
    "metrics": Metric,
}


class Registry(BaseModel):
    tasks: dict[str, Task]
    algorithms: dict[str, Algorithm]
    implementations: dict[str, Implementation]
    representations: dict[str, Representation]
    datasets: dict[str, Dataset]
    metrics: dict[str, Metric]

    def variant(self, implementation: str, representation: str) -> Variant:
        for variant in self.implementations[implementation].variants:
            if variant.representation == representation:
                return variant
        raise KeyError(f"{implementation} has no {representation} variant")

    def requirements(self, implementation: str, representation: str) -> list[str]:
        needed = self.implementations[implementation].requires
        needed = needed + self.representations[representation].requires
        return sorted(set(needed))

    def task_of(self, implementation: str) -> Task:
        return self.tasks[self.algorithms[self.implementations[implementation].algorithm].task]


def load_entries(directory: Path, kind: str) -> dict[str, Entry]:
    model = KINDS[kind]
    entries = {}
    for path in sorted((directory / kind).glob("*.yaml")):
        entry = model.model_validate(yaml.safe_load(path.read_text()))
        if entry.id != path.stem:
            raise ValueError(f"{path}: id {entry.id} does not match the file name")
        entries[entry.id] = entry
    return entries


def reference_errors(registry: Registry) -> list[str]:
    errors = []
    for algorithm in registry.algorithms.values():
        if algorithm.task not in registry.tasks:
            errors.append(f"algorithm {algorithm.id}: unknown task {algorithm.task}")
    for implementation in registry.implementations.values():
        if implementation.algorithm not in registry.algorithms:
            errors.append(
                f"implementation {implementation.id}: unknown algorithm {implementation.algorithm}"
            )
        for variant in implementation.variants:
            if variant.representation not in registry.representations:
                errors.append(
                    f"implementation {implementation.id}: unknown representation {variant.representation}"
                )
            if variant.driver == "gaib-runner" and variant.definition_task is None:
                errors.append(
                    f"implementation {implementation.id}: gaib-runner variant needs definition_task"
                )
            if variant.driver == "gaib-representation-test" and variant.script_task is None:
                errors.append(
                    f"implementation {implementation.id}: gaib-representation-test variant needs script_task"
                )
    for metric in registry.metrics.values():
        for task in metric.tasks:
            if task not in registry.tasks:
                errors.append(f"metric {metric.id}: unknown task {task}")
    return errors


def load_registry(directories: list[Path]) -> Registry:
    kinds = {kind: {} for kind in KINDS}
    for directory in directories:
        for kind in KINDS:
            for entry_id, entry in load_entries(directory, kind).items():
                if entry_id in kinds[kind]:
                    raise ValueError(f"{kind} entry {entry_id} is defined twice")
                kinds[kind][entry_id] = entry
    registry = Registry(**kinds)
    errors = reference_errors(registry)
    if errors:
        raise ValueError("; ".join(errors))
    return registry
