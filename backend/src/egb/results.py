import statistics
from pathlib import Path

from pydantic import BaseModel

from egb.jobs import JobSpec
from egb.registry import Registry
from egb.runners import read_rows
from egb.sessions import SessionConfig


class MetricSummary(BaseModel):
    unit: str
    median: float
    min: float
    max: float
    runs: int


class SummaryRow(BaseModel):
    configuration: str
    dataset: str
    runs: int
    metrics: dict[str, MetricSummary]


def coerce(value: str) -> int | float | str | None:
    if value == "":
        return None
    for kind in (int, float):
        try:
            return kind(value)
        except ValueError:
            pass
    return value


def job_specs(session_dir: Path) -> list[JobSpec]:
    return [
        JobSpec.model_validate_json(p.read_text())
        for p in sorted(session_dir.glob("jobs/*/spec.json"))
    ]


def session_rows(session_dir: Path, statuses: dict[str, str]) -> list[dict]:
    rows = []
    for spec in job_specs(session_dir):
        datasets = {item.results_dataset: item.dataset for item in spec.items}
        for raw in read_rows(session_dir / "jobs" / spec.id / "results.csv"):
            row = {key: coerce(value) for key, value in raw.items()}
            row |= {
                "job_id": spec.id,
                "job_status": statuses.get(spec.id),
                "configuration": spec.configuration_of(raw["task"], raw["dataset"]),
                "dataset_id": datasets.get(raw["dataset"]),
            }
            rows.append(row)
    return rows


def metric_value(row: dict, columns: list[str]) -> float | None:
    values = [row.get(column) for column in columns]
    if any(not isinstance(v, (int, float)) for v in values):
        return None
    return float(sum(values))


def summarise(rows: list[dict], config: SessionConfig, registry: Registry) -> list[SummaryRow]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        if row["configuration"] is not None:
            groups.setdefault((row["configuration"], row["dataset_id"]), []).append(row)
    summary = []
    for (configuration, dataset), group in sorted(groups.items()):
        task = registry.task_of(config.candidate(configuration).implementation).id
        metrics = {}
        for metric_id in config.metrics:
            metric = registry.metrics[metric_id]
            if task not in metric.tasks:
                continue
            values = [v for v in (metric_value(r, metric.columns) for r in group) if v is not None]
            if values:
                metrics[metric_id] = MetricSummary(
                    unit=metric.unit,
                    median=statistics.median(values),
                    min=min(values),
                    max=max(values),
                    runs=len(values),
                )
        summary.append(
            SummaryRow(
                configuration=configuration, dataset=dataset, runs=len(group), metrics=metrics
            )
        )
    return summary
