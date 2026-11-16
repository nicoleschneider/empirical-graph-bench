import json
from pathlib import Path

from egb.datasets import coerce
from egb.jobs import JobSpec
from egb.registry import Registry
from egb.validity import CrossPath, SavedOutput, read_cross_paths
from egb.worker import read_rows


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


def saved_outputs(session_dir: Path) -> list[SavedOutput]:
    return [
        SavedOutput.model_validate(record)
        for path in sorted(session_dir.glob("jobs/*/outputs/index.json"))
        for record in json.loads(path.read_text())
    ]


def cross_paths(session_dir: Path, registry: Registry) -> list[CrossPath]:
    datasets = {d.results_name: d.id for d in registry.datasets.values()}
    return [
        record
        for path in sorted(session_dir.glob("jobs/*/cross_path_*.csv"))
        for record in read_cross_paths(path, datasets)
    ]
