import logging
import statistics
from collections import defaultdict
from itertools import permutations
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict

from egb.registry import Registry

PHASES = ("etl_s", "processing_s", "end_to_end_s")
EFFECTIVENESS = ("modularity", "nmi", "predicted_communities", "pagerank_l1", "pagerank_top100")

logger = logging.getLogger(__name__)


class AnalysisSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    etl_dominant_share: float
    processing_dominant_share: float
    modularity_tolerance: float
    nmi_tolerance: float
    community_difference: float
    top100_tolerance: float
    l1_tolerance: float
    validity_l1_tolerance: float
    validity_nmi_minimum: float
    bootstrap_resamples: int
    bootstrap_seed: int


def load_analysis_settings(path: Path) -> AnalysisSettings:
    return AnalysisSettings.model_validate(yaml.safe_load(path.read_text()))


class ConfigKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    hardware: str
    implementation: str
    representation: str

    @property
    def id(self) -> str:
        return f"{self.hardware}/{self.implementation}/{self.representation}"

    @classmethod
    def parse(cls, key: str) -> "ConfigKey":
        parts = key.split("/")
        if len(parts) != 3:
            raise ValueError(f"{key} is not hardware/implementation/representation.")
        return cls(hardware=parts[0], implementation=parts[1], representation=parts[2])


class Run(BaseModel):
    config: ConfigKey
    task: str
    algorithm: str
    dataset: str
    run_index: int
    seed: int | None
    vertices: int
    edges: int
    etl_s: float
    processing_s: float
    end_to_end_s: float
    etl_share: float
    modularity: float | None
    nmi: float | None
    predicted_communities: float | None
    pagerank_l1: float | None
    pagerank_top100: float | None


def optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def runs_from_rows(rows: list[dict], registry: Registry) -> list[Run]:
    variants = {v.gaib_task: (i, v) for i in registry.implementations.values() for v in i.variants}
    datasets = {d.results_name: d.id for d in registry.datasets.values()}
    runs = []
    for row in rows:
        if row["task"] not in variants or row["dataset"] not in datasets:
            logger.warning(
                "Skipping row with unknown task %s or dataset %s", row["task"], row["dataset"]
            )
            continue
        implementation, variant = variants[row["task"]]
        etl, processing = float(row["etl_s"]), float(row["processing_s"])
        values = {name: optional_float(row.get(name)) for name in EFFECTIVENESS}
        runs.append(
            Run(
                config=ConfigKey(
                    hardware=str(row["hardware"]),
                    implementation=implementation.id,
                    representation=variant.representation,
                ),
                task=registry.task_of(implementation.id).id,
                algorithm=implementation.algorithm,
                dataset=datasets[row["dataset"]],
                run_index=int(row["run_index"]),
                seed=None if row.get("seed") in (None, "") else int(row["seed"]),
                vertices=int(row["vertices"]),
                edges=int(row["edges"]),
                etl_s=etl,
                processing_s=processing,
                end_to_end_s=etl + processing,
                etl_share=etl / (etl + processing),
                **values,
            )
        )
    return runs


class Interval(BaseModel):
    point: float
    low: float
    high: float
    resamples: int
    seed: int


def ratio_of_medians(
    numerator: list[float], denominator: list[float] | float, resamples: int, seed: int
) -> Interval:
    rng = np.random.default_rng(seed)
    numerator_samples = np.median(rng.choice(numerator, (resamples, len(numerator))), axis=1)
    if isinstance(denominator, float):
        point = float(np.median(numerator)) / denominator
        ratios = numerator_samples / denominator
    else:
        point = float(np.median(numerator) / np.median(denominator))
        ratios = numerator_samples / np.median(
            rng.choice(denominator, (resamples, len(denominator))), axis=1
        )
    low, high = np.percentile(ratios, [2.5, 97.5])
    return Interval(point=point, low=float(low), high=float(high), resamples=resamples, seed=seed)


class Spread(BaseModel):
    median: float
    min: float
    max: float


class Summary(BaseModel):
    config: ConfigKey
    task: str
    algorithm: str
    dataset: str
    vertices: int
    edges: int
    runs: int
    etl_s: Spread
    processing_s: Spread
    end_to_end_s: Spread
    etl_share: float
    effectiveness: dict[str, Spread]


def spread(values: list[float]) -> Spread:
    return Spread(median=statistics.median(values), min=min(values), max=max(values))


def group_runs(runs: list[Run]) -> dict[tuple[ConfigKey, str], list[Run]]:
    groups: dict[tuple[ConfigKey, str], list[Run]] = defaultdict(list)
    for run in runs:
        groups[(run.config, run.dataset)].append(run)
    return groups


def summarise(runs: list[Run]) -> list[Summary]:
    summaries = []
    for (config, dataset), group in sorted(
        group_runs(runs).items(), key=lambda item: (item[0][0].id, item[0][1])
    ):
        effectiveness = {}
        for name in EFFECTIVENESS:
            values = [getattr(r, name) for r in group if getattr(r, name) is not None]
            if values:
                effectiveness[name] = spread(values)
        summaries.append(
            Summary(
                config=config,
                task=group[0].task,
                algorithm=group[0].algorithm,
                dataset=dataset,
                vertices=group[0].vertices,
                edges=group[0].edges,
                runs=len(group),
                etl_s=spread([r.etl_s for r in group]),
                processing_s=spread([r.processing_s for r in group]),
                end_to_end_s=spread([r.end_to_end_s for r in group]),
                etl_share=statistics.median([r.etl_share for r in group]),
                effectiveness=effectiveness,
            )
        )
    return summaries


class Bottleneck(BaseModel):
    config: ConfigKey
    dataset: str
    dominant: Literal["etl", "processing"]
    share: float
    end_to_end_s: float
    fastest: ConfigKey
    fastest_end_to_end_s: float


class Difference(BaseModel):
    kind: Literal["faster_processing_slower_end_to_end", "faster_with_different_effectiveness"]
    config: ConfigKey
    other: ConfigKey
    task: str
    dataset: str
    values: dict[str, float]
    metrics: list[str]


class Crossover(BaseModel):
    task: str
    winners: dict[str, ConfigKey]
    edges: dict[str, int]


class Analysis(BaseModel):
    summaries: list[Summary]
    bottlenecks: list[Bottleneck]
    differences: list[Difference]
    crossovers: list[Crossover]


def by_task_and_dataset(summaries: list[Summary]) -> dict[tuple[str, str], list[Summary]]:
    groups: dict[tuple[str, str], list[Summary]] = defaultdict(list)
    for summary in summaries:
        groups[(summary.task, summary.dataset)].append(summary)
    return groups


def fastest(group: list[Summary]) -> Summary:
    return min(group, key=lambda s: s.end_to_end_s.median)


def bottlenecks(summaries: list[Summary]) -> list[Bottleneck]:
    groups = by_task_and_dataset(summaries)
    found = []
    for summary in summaries:
        best = fastest(groups[(summary.task, summary.dataset)])
        etl = summary.etl_share >= 0.5
        found.append(
            Bottleneck(
                config=summary.config,
                dataset=summary.dataset,
                dominant="etl" if etl else "processing",
                share=summary.etl_share if etl else 1 - summary.etl_share,
                end_to_end_s=summary.end_to_end_s.median,
                fastest=best.config,
                fastest_end_to_end_s=best.end_to_end_s.median,
            )
        )
    return found


def effectiveness_gaps(a: Summary, b: Summary, settings: AnalysisSettings) -> dict[str, float]:
    tolerances = {
        "modularity": settings.modularity_tolerance,
        "nmi": settings.nmi_tolerance,
        "pagerank_top100": settings.top100_tolerance,
        "pagerank_l1": settings.l1_tolerance,
    }
    gaps = {}
    for name, tolerance in tolerances.items():
        if name in a.effectiveness and name in b.effectiveness:
            gap = a.effectiveness[name].median - b.effectiveness[name].median
            if abs(gap) > tolerance:
                gaps[name] = gap
    if "predicted_communities" in a.effectiveness and "predicted_communities" in b.effectiveness:
        ours = a.effectiveness["predicted_communities"].median
        theirs = b.effectiveness["predicted_communities"].median
        if abs(ours - theirs) > settings.community_difference * max(ours, theirs):
            gaps["predicted_communities"] = ours - theirs
    return gaps


def pair_values(config: Summary, other: Summary, names: list[str]) -> dict[str, float]:
    values = {}
    for name in names:
        for prefix, summary in (("config", config), ("other", other)):
            source = getattr(summary, name) if name in PHASES else summary.effectiveness[name]
            values[f"{prefix}_{name}"] = source.median
    return values


def differences(summaries: list[Summary], settings: AnalysisSettings) -> list[Difference]:
    found = []
    for (task, dataset), group in sorted(by_task_and_dataset(summaries).items()):
        for a, b in permutations(group, 2):
            if a.end_to_end_s.median >= b.end_to_end_s.median:
                continue
            if b.processing_s.median < a.processing_s.median:
                found.append(
                    Difference(
                        kind="faster_processing_slower_end_to_end",
                        config=b.config,
                        other=a.config,
                        task=task,
                        dataset=dataset,
                        values=pair_values(b, a, ["processing_s", "end_to_end_s"]),
                        metrics=["processing_s", "end_to_end_s"],
                    )
                )
            gaps = effectiveness_gaps(a, b, settings)
            if gaps:
                found.append(
                    Difference(
                        kind="faster_with_different_effectiveness",
                        config=a.config,
                        other=b.config,
                        task=task,
                        dataset=dataset,
                        values=pair_values(a, b, ["end_to_end_s", *sorted(gaps)]),
                        metrics=sorted(gaps),
                    )
                )
    return found


def crossovers(summaries: list[Summary]) -> list[Crossover]:
    tasks: dict[str, dict[str, Summary]] = defaultdict(dict)
    for (task, dataset), group in by_task_and_dataset(summaries).items():
        tasks[task][dataset] = fastest(group)
    found = []
    for task, winners in sorted(tasks.items()):
        if len({w.config for w in winners.values()}) > 1:
            ordered = sorted(winners, key=lambda d: winners[d].edges)
            found.append(
                Crossover(
                    task=task,
                    winners={d: winners[d].config for d in ordered},
                    edges={d: winners[d].edges for d in ordered},
                )
            )
    return found


def analyse(runs: list[Run], settings: AnalysisSettings) -> Analysis:
    summaries = summarise(runs)
    return Analysis(
        summaries=summaries,
        bottlenecks=bottlenecks(summaries),
        differences=differences(summaries, settings),
        crossovers=crossovers(summaries),
    )


class Comparison(BaseModel):
    config: ConfigKey
    baseline: ConfigKey
    dataset: str
    measure: str
    speedup: Interval


def comparisons(
    runs: list[Run], baseline: ConfigKey, measure: str, settings: AnalysisSettings
) -> list[Comparison]:
    if measure not in PHASES:
        raise ValueError(f"Measure must be one of {', '.join(PHASES)}.")
    groups = group_runs(runs)
    found = []
    for (config, dataset), group in sorted(
        groups.items(), key=lambda item: (item[0][0].id, item[0][1])
    ):
        reference = groups.get((baseline, dataset))
        if config == baseline or reference is None or group[0].task != reference[0].task:
            continue
        found.append(
            Comparison(
                config=config,
                baseline=baseline,
                dataset=dataset,
                measure=measure,
                speedup=ratio_of_medians(
                    [getattr(r, measure) for r in reference],
                    [getattr(r, measure) for r in group],
                    settings.bootstrap_resamples,
                    settings.bootstrap_seed,
                ),
            )
        )
    return found
