import datetime
import json
import secrets
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Literal

from pydantic import BaseModel, Field

from egb.datasets import DatasetStore
from egb.hardware import Hardware
from egb.registry import Registry
from egb.sessions import LogEntry, Session, now
from egb.settings import Settings

CAPABILITY_NAMES = {"cpu": "the benchmark environment", "cuda": "CUDA", "cudf": "cudf"}


class JobRequest(BaseModel):
    configurations: list[str]
    datasets: list[str]
    kind: Literal["exploratory", "targeted"]
    hypothesis: str | None = None
    baseline: str | None = None
    repetitions: int | None = Field(default=None, ge=1)
    base_seed: int | None = None


class JobItem(BaseModel):
    configuration: str
    implementation: str
    representation: str
    dataset: str
    gaib_task: str
    results_dataset: str


class Invocation(BaseModel):
    driver: str
    argv: list[str]
    cwd: str
    env: dict[str, str]
    results_csv: str | None


class JobSpec(BaseModel):
    id: str
    session_id: str | None
    kind: Literal["exploratory", "targeted", "download", "properties"]
    hypothesis: str | None
    baseline: str | None = None
    hardware: str = "local"
    hardware_label: str
    repetitions: int | None
    base_seed: int | None
    datasets: list[str]
    items: list[JobItem]
    invocations: list[Invocation]
    created_at: str

    def configuration_of(self, gaib_task: str, results_dataset: str) -> str | None:
        for item in self.items:
            if item.gaib_task == gaib_task and item.results_dataset == results_dataset:
                return item.configuration
        return None


class ConfigurationStatus(BaseModel):
    hardware: str
    implementation: str
    representation: str
    algorithm: str
    task: str
    gaib_task: str
    runnable: bool
    reasons: list[str]


def new_job_id() -> str:
    return f"j-{datetime.datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"


def blockers(
    registry: Registry, hardware: Hardware, implementation: str, representation: str
) -> list[str]:
    if hardware.status != "reachable":
        return [hardware.reason or "Not reachable."]
    if hardware.supports is not None and implementation not in hardware.supports:
        return ["Not in this target's supports list."]
    needed = ["cpu"] + registry.requirements(implementation, representation)
    missing = [c for c in needed if c not in hardware.capabilities]
    if not missing:
        return []
    names = [CAPABILITY_NAMES.get(c, c) for c in missing]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    explanations = list(
        dict.fromkeys(hardware.unavailable[c] for c in missing if c in hardware.unavailable)
    )
    return [f"Needs {listed}.", *explanations]


def configuration_statuses(registry: Registry, hardware: Hardware) -> list[ConfigurationStatus]:
    statuses = []
    for implementation in registry.implementations.values():
        for variant in implementation.variants:
            reasons = blockers(registry, hardware, implementation.id, variant.representation)
            statuses.append(
                ConfigurationStatus(
                    hardware=hardware.id,
                    implementation=implementation.id,
                    representation=variant.representation,
                    algorithm=implementation.algorithm,
                    task=registry.task_of(implementation.id).id,
                    gaib_task=variant.gaib_task,
                    runnable=not reasons,
                    reasons=reasons,
                )
            )
    return statuses


def hypothesis_errors(hypothesis: str | None, log: list[LogEntry]) -> list[str]:
    if hypothesis is None:
        return ["A targeted experiment needs a recorded hypothesis."]
    if not any(e.id == hypothesis and e.kind == "hypothesis" for e in log):
        return [f"{hypothesis} is not a recorded hypothesis."]
    if not any(e.kind == "prediction" and hypothesis in e.refers_to for e in log):
        return [f"{hypothesis} has no recorded prediction."]
    return []


def work_dir(job_dir: PurePath) -> PurePath:
    return job_dir / "work" / "cwd"


def write_definition(
    path: Path,
    source: PurePath,
    ground_truth: PurePath | None,
    task: str,
    directed: bool,
    gpu: bool,
) -> None:
    path.write_text(
        json.dumps(
            {
                "SOURCE_DATA": str(source),
                "GT_FILE_PATH": None if ground_truth is None else str(ground_truth),
                "GT_GRANULARITY": "standard",
                "WORKING_DIRECTORY": "temp_workspace",
                "OUTPUT_DIRECTORY": "experiment_results",
                "TASK": task,
                "WEIGHTED": False,
                "SNAP": True,
                "DIRECTED": directed,
                "GPU": gpu,
            },
            indent=4,
        )
    )


@dataclass(frozen=True)
class RunPaths:
    bench_python: PurePath
    gaib_dir: PurePath
    datasets: PurePath
    manifest: PurePath
    pagerank_references: PurePath
    job_dir: PurePath


def local_paths(settings: Settings, job_dir: Path) -> RunPaths:
    return RunPaths(
        bench_python=settings.bench_python,
        gaib_dir=settings.gaib_dir,
        datasets=settings.datasets_dir,
        manifest=settings.manifest,
        pagerank_references=settings.pagerank_references,
        job_dir=job_dir,
    )


def runner_invocation(paths: RunPaths, label: str, repetitions: int, base_seed: int) -> Invocation:
    job_dir = paths.job_dir
    return Invocation(
        driver="gaib-runner",
        argv=[
            str(paths.bench_python),
            str(paths.gaib_dir / "runner.py"),
            "--hardware",
            label,
            "--definitions",
            str(job_dir / "definitions"),
            "--output-directory",
            str(job_dir / "reports"),
            "--results-csv",
            str(job_dir / "results.csv"),
            "--repetitions",
            str(repetitions),
            "--base-seed",
            str(base_seed),
            "--pagerank-reference-directory",
            str(paths.pagerank_references),
        ],
        cwd=str(work_dir(job_dir)),
        env={},
        results_csv=str(job_dir / "results.csv"),
    )


def representation_invocation(
    paths: RunPaths,
    label: str,
    repetitions: int,
    base_seed: int,
    graphs: list[PurePath],
    script_task: str,
    dtype: str,
    undirected: bool,
) -> Invocation:
    job_dir = paths.job_dir
    argv = [
        str(paths.bench_python),
        str(paths.gaib_dir / "iterations" / "representation_test.py"),
        *[str(g) for g in graphs],
        "--task",
        script_task,
        "--dtype",
        dtype,
        "--repetitions",
        str(repetitions),
        "--results-csv",
        str(job_dir / "results.csv"),
        "--hardware",
        label,
        "--base-seed",
        str(base_seed),
        "--manifest",
        str(paths.manifest),
        "--pagerank-reference-directory",
        str(paths.pagerank_references),
    ]
    if undirected:
        argv.append("--undirected")
    return Invocation(
        driver="gaib-representation-test",
        argv=argv,
        cwd=str(work_dir(job_dir)),
        env={"PYTHONPATH": str(paths.gaib_dir)},
        results_csv=str(job_dir / "results.csv"),
    )


def resolve_session_job(
    request: JobRequest,
    session: Session,
    log: list[LogEntry],
    registry: Registry,
    hardware: Hardware,
    datasets: DatasetStore | None,
    paths: RunPaths,
    job_id: str,
    job_dir: Path,
) -> JobSpec:
    config = session.config
    errors = []
    known = {c.id for c in config.candidates}
    errors += [
        f"{c} is not a candidate configuration." for c in request.configurations if c not in known
    ]
    errors += [f"{d} is not in the workload." for d in request.datasets if d not in config.datasets]
    if request.baseline is not None and request.baseline not in known:
        errors.append(f"Baseline {request.baseline} is not a candidate configuration.")
    if request.kind == "targeted":
        errors += hypothesis_errors(request.hypothesis, log)
    if errors:
        raise ValueError(" ".join(errors))
    for configuration_id in request.configurations:
        candidate = config.candidate(configuration_id)
        errors += [
            f"{configuration_id}: {r}"
            for r in blockers(
                registry, hardware, candidate.implementation, candidate.representation
            )
        ]
    for configuration_id in request.configurations:
        if config.candidate(configuration_id).hardware != hardware.id:
            errors.append(f"{configuration_id} is not on {hardware.id}.")
    for dataset_id in request.datasets if datasets is not None else []:
        if datasets.status(registry.datasets[dataset_id], compute=False).state == "missing":
            errors.append(f"{dataset_id} is not downloaded.")
    if errors:
        raise ValueError(" ".join(errors))

    repetitions = request.repetitions or config.repetitions
    base_seed = config.base_seed if request.base_seed is None else request.base_seed
    (job_dir / "definitions").mkdir(parents=True)
    items, script_groups = [], {}
    uses_runner = False
    for configuration_id in request.configurations:
        candidate = config.candidate(configuration_id)
        variant = registry.variant(candidate.implementation, candidate.representation)
        algorithm = registry.algorithms[
            registry.implementations[candidate.implementation].algorithm
        ]
        for dataset_id in request.datasets:
            dataset = registry.datasets[dataset_id]
            directed = algorithm.orientation == "dataset" and dataset.directed
            items.append(
                JobItem(
                    configuration=configuration_id,
                    implementation=candidate.implementation,
                    representation=candidate.representation,
                    dataset=dataset_id,
                    gaib_task=variant.gaib_task,
                    results_dataset=dataset.results_name,
                )
            )
            folder = paths.datasets / dataset.directory
            graph = folder / dataset.graph.file
            if variant.driver == "gaib-runner":
                ground_truth = (
                    folder / dataset.ground_truth.file
                    if algorithm.task == "community-detection" and dataset.ground_truth is not None
                    else None
                )
                write_definition(
                    job_dir
                    / "definitions"
                    / f"{len(items):03d}_{dataset.results_name}_{variant.gaib_task}.json",
                    graph,
                    ground_truth,
                    variant.definition_task,
                    directed,
                    variant.gpu,
                )
                uses_runner = True
            else:
                dtype = registry.representations[candidate.representation].precision
                script_groups.setdefault((variant.script_task, dtype, not directed), []).append(
                    graph
                )
    invocations = (
        [runner_invocation(paths, hardware.label, repetitions, base_seed)] if uses_runner else []
    )
    for (script_task, dtype, undirected), graphs in script_groups.items():
        invocations.append(
            representation_invocation(
                paths,
                hardware.label,
                repetitions,
                base_seed,
                graphs,
                script_task,
                dtype,
                undirected and script_task == "pagerank",
            )
        )
    return JobSpec(
        id=job_id,
        session_id=session.meta.id,
        kind=request.kind,
        hypothesis=request.hypothesis,
        baseline=request.baseline,
        hardware=hardware.id,
        hardware_label=hardware.label,
        repetitions=repetitions,
        base_seed=base_seed,
        datasets=request.datasets,
        items=items,
        invocations=invocations,
        created_at=now(),
    )


def resolve_dataset_job(
    kind: Literal["download", "properties"],
    dataset_ids: list[str],
    registry: Registry,
    hardware: Hardware,
    settings: Settings,
    job_id: str,
    job_dir: Path,
) -> JobSpec:
    unknown = [d for d in dataset_ids if d not in registry.datasets]
    if unknown:
        raise ValueError(f"Unknown datasets: {', '.join(unknown)}.")
    if not dataset_ids:
        raise ValueError("No datasets to process. Download one first.")
    if "cpu" not in hardware.capabilities:
        raise ValueError(hardware.unavailable["cpu"])
    job_dir.mkdir(parents=True)
    listing = job_dir / "datasets.json"
    listing.write_text(
        json.dumps(
            [
                {
                    "name": d.id,
                    "directory": d.directory,
                    "graph_url": d.graph.url,
                    "ground_truth_url": None if d.ground_truth is None else d.ground_truth.url,
                    "directed": d.directed,
                }
                for d in (registry.datasets[i] for i in dataset_ids)
            ],
            indent=4,
        )
    )
    common = [
        "--datasets-list",
        str(listing),
        "--datasets-directory",
        str(settings.datasets_dir),
        "--manifest",
        str(settings.manifest),
    ]
    if kind == "download":
        argv = [
            str(settings.bench_python),
            str(settings.gaib_dir / "download_datasets.py"),
            *common,
        ]
    else:
        argv = [
            str(settings.bench_python),
            str(settings.gaib_dir / "dataset_properties.py"),
            *common,
            "--output",
            str(settings.dataset_properties),
        ]
    invocation = Invocation(
        driver=f"gaib-{kind}", argv=argv, cwd=str(work_dir(job_dir)), env={}, results_csv=None
    )
    return JobSpec(
        id=job_id,
        session_id=None,
        kind=kind,
        hypothesis=None,
        hardware_label=hardware.label,
        repetitions=None,
        base_seed=None,
        datasets=dataset_ids,
        items=[],
        invocations=[invocation],
        created_at=now(),
    )
