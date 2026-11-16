import csv
import json
import os
import statistics
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from egb.analysis import AnalysisSettings, ConfigKey, Run, group_runs
from egb.registry import Registry

Comparer = Callable[[str, Path, Path], dict]


class CrossPath(BaseModel):
    hardware: str
    gaib_task: str
    dataset: str
    seed: int | None
    l1: float | None = None
    identical: bool | None = None
    nmi_between: float | None = None
    modularity_difference: float | None = None


class SavedOutput(BaseModel):
    config: ConfigKey
    dataset: str
    seed: int
    path: str


class Check(BaseModel):
    name: str
    value: float | bool | None
    expected: str
    passed: bool | None


class ValidityResult(BaseModel):
    targeted: ConfigKey
    baseline: ConfigKey
    dataset: str
    output: Literal["scores", "communities"]
    source: Literal["cross_path", "saved_outputs", "none"]
    seed: int | None
    l1: float | None
    identical: bool | None
    nmi_between: float | None
    modularity_difference: float | None
    checks: list[Check]
    side_effects: list[str]
    note: str | None


def read_cross_paths(path: Path, datasets: dict[str, str]) -> list[CrossPath]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    records = []
    for row in rows:
        common = {
            "hardware": row["hardware"],
            "gaib_task": row["task"],
            "dataset": datasets.get(row["dataset"], row["dataset"]),
        }
        if "l1_vs_nx_pagerank_gpu" in row:
            records.append(CrossPath(**common, seed=None, l1=float(row["l1_vs_nx_pagerank_gpu"])))
        else:
            records.append(
                CrossPath(
                    **common,
                    seed=int(row["seed"]),
                    identical=row["identical_partitions"] == "True",
                    nmi_between=float(row["nmi_between_partitions"]),
                    modularity_difference=float(row["modularity_cudf"])
                    - float(row["modularity_original"]),
                )
            )
    return records


def side_effects(targeted: ConfigKey, baseline: ConfigKey, registry: Registry) -> list[str]:
    effects = []
    if targeted.hardware != baseline.hardware:
        effects.append(f"Hardware changes from {baseline.hardware} to {targeted.hardware}.")
    new, old = (
        registry.implementations[targeted.implementation],
        registry.implementations[baseline.implementation],
    )
    if new.algorithm != old.algorithm:
        effects.append(
            f"Algorithm changes from {registry.algorithms[old.algorithm].name} to "
            f"{registry.algorithms[new.algorithm].name}."
        )
    if new.backend != old.backend:
        effects.append(f"Backend changes from {old.backend} to {new.backend}.")
    new_rep, old_rep = (
        registry.representations[targeted.representation],
        registry.representations[baseline.representation],
    )
    if new_rep.loader != old_rep.loader:
        effects.append(
            f"Loading changes from {old_rep.name} to {new_rep.name}. Compare end-to-end, not processing."
        )
    if (
        new_rep.precision != old_rep.precision
        and registry.algorithms[new.algorithm].precision_sensitive
    ):
        effects.append(f"Precision changes from {old_rep.precision} to {new_rep.precision}.")
    return effects


def cross_path_record(
    targeted: ConfigKey,
    baseline: ConfigKey,
    dataset: str,
    registry: Registry,
    records: list[CrossPath],
) -> CrossPath | None:
    variant = registry.variant(targeted.implementation, targeted.representation)
    if (
        variant.cross_path_baseline != baseline.representation
        or targeted.implementation != baseline.implementation
        or targeted.hardware != baseline.hardware
    ):
        return None
    for record in records:
        if (
            record.gaib_task == variant.gaib_task
            and record.dataset == dataset
            and record.hardware == targeted.hardware
        ):
            return record
    return None


def saved_pair(
    targeted: ConfigKey, baseline: ConfigKey, dataset: str, outputs: list[SavedOutput]
) -> tuple[int, Path, Path] | None:
    ours = {o.seed: o.path for o in outputs if o.config == targeted and o.dataset == dataset}
    theirs = {o.seed: o.path for o in outputs if o.config == baseline and o.dataset == dataset}
    common = sorted(set(ours) & set(theirs))
    if not common:
        return None
    return common[0], Path(ours[common[0]]), Path(theirs[common[0]])


def median_modularity_difference(
    targeted: ConfigKey, baseline: ConfigKey, dataset: str, runs: list[Run]
) -> float | None:
    groups = group_runs(runs)
    ours = [r.modularity for r in groups.get((targeted, dataset), []) if r.modularity is not None]
    theirs = [r.modularity for r in groups.get((baseline, dataset), []) if r.modularity is not None]
    if not ours or not theirs:
        return None
    return statistics.median(ours) - statistics.median(theirs)


def checks_for(result: dict, output: str, settings: AnalysisSettings) -> list[Check]:
    if output == "scores":
        l1 = result["l1"]
        return [
            Check(
                name="l1",
                value=l1,
                expected=f"<= {settings.validity_l1_tolerance:g}",
                passed=None if l1 is None else l1 <= settings.validity_l1_tolerance,
            )
        ]
    nmi, difference = result["nmi_between"], result["modularity_difference"]
    return [
        Check(
            name="identical", value=result["identical"], expected="true", passed=result["identical"]
        ),
        Check(
            name="nmi_between",
            value=nmi,
            expected=f">= {settings.validity_nmi_minimum:g}",
            passed=None if nmi is None else nmi >= settings.validity_nmi_minimum,
        ),
        Check(
            name="modularity_difference",
            value=difference,
            expected=f"within {settings.modularity_tolerance:g}",
            passed=None if difference is None else abs(difference) <= settings.modularity_tolerance,
        ),
    ]


def check_validity(
    targeted: ConfigKey,
    baseline: ConfigKey,
    dataset: str,
    runs: list[Run],
    registry: Registry,
    records: list[CrossPath],
    outputs: list[SavedOutput],
    comparer: Comparer,
    settings: AnalysisSettings,
) -> ValidityResult:
    task = registry.task_of(targeted.implementation)
    if task.id != registry.task_of(baseline.implementation).id:
        raise ValueError("The targeted and baseline configurations solve different tasks.")
    output = task.output
    result = {
        "l1": None,
        "identical": None,
        "nmi_between": None,
        "modularity_difference": median_modularity_difference(targeted, baseline, dataset, runs),
    }
    source, seed, note = "none", None, None
    record = cross_path_record(targeted, baseline, dataset, registry, records)
    pair = saved_pair(targeted, baseline, dataset, outputs)
    if record is not None:
        source, seed = "cross_path", record.seed
        result |= {
            k: v
            for k, v in record.model_dump(
                include={"l1", "identical", "nmi_between", "modularity_difference"}
            ).items()
            if v is not None
        }
    elif pair is not None:
        source, seed = "saved_outputs", pair[0]
        result |= comparer(output, pair[1], pair[2])
    else:
        note = (
            "No saved outputs for a common seed. Run both configurations with the same base seed."
        )
    if output == "scores":
        result["modularity_difference"] = None
    return ValidityResult(
        targeted=targeted,
        baseline=baseline,
        dataset=dataset,
        output=output,
        source=source,
        seed=seed,
        checks=checks_for(result, output, settings),
        side_effects=side_effects(targeted, baseline, registry),
        note=note,
        **result,
    )


def bench_comparer(bench_python: Path, gaib_dir: Path) -> Comparer:
    script = Path(__file__).with_name("compare_outputs.py")

    def compare(kind: str, targeted: Path, baseline: Path) -> dict:
        completed = subprocess.run(
            [str(bench_python), str(script), kind, str(targeted), str(baseline)],
            capture_output=True,
            text=True,
            check=False,
            env=os.environ | {"PYTHONPATH": str(gaib_dir)},
        )
        if completed.returncode != 0:
            raise RuntimeError(f"Output comparison failed: {completed.stderr.strip()}")
        return json.loads(completed.stdout)

    return compare
