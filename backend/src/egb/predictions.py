import statistics
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from egb.analysis import AnalysisSettings, ConfigKey, Interval, Run, group_runs, ratio_of_medians

PREDICTABLE = (
    "etl_s",
    "processing_s",
    "end_to_end_s",
    "etl_share",
    "modularity",
    "nmi",
    "predicted_communities",
    "pagerank_l1",
    "pagerank_top100",
)
SYMBOLS = {"held": "✓", "refuted": "✗", "narrow": "≈", "pending": "·"}


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Literal[PREDICTABLE]
    subject: ConfigKey
    relation: Literal["<", ">"]
    reference: ConfigKey | None = None
    threshold: float | None = None
    datasets: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def one_threshold(self) -> "Prediction":
        if (self.reference is None) == (self.threshold is None):
            raise ValueError("Give either a reference configuration or a threshold value.")
        return self


class DatasetEvaluation(BaseModel):
    dataset: str
    status: Literal["held", "refuted", "narrow", "no_data"]
    subject_median: float | None
    threshold: float | None
    ratio: Interval | None
    subject_runs: int
    reference_runs: int | None


class Evaluation(BaseModel):
    status: Literal["held", "refuted", "narrow", "pending"]
    symbol: str
    method: str
    datasets: list[DatasetEvaluation]


def values(runs: list[Run], metric: str) -> list[float]:
    return [getattr(r, metric) for r in runs if getattr(r, metric) is not None]


def inverted(interval: Interval) -> Interval:
    return interval.model_copy(
        update={"point": 1 / interval.point, "low": 1 / interval.high, "high": 1 / interval.low}
    )


def ratio_status(interval: Interval) -> str:
    if interval.low > 1:
        return "held"
    if interval.high < 1:
        return "refuted"
    return "narrow"


def predicted_ratio(
    prediction: Prediction,
    subject: list[float],
    reference: list[float] | float,
    settings: AnalysisSettings,
) -> Interval:
    resamples, seed = settings.bootstrap_resamples, settings.bootstrap_seed
    if prediction.relation == ">":
        return ratio_of_medians(subject, reference, resamples, seed)
    if isinstance(reference, float):
        return inverted(ratio_of_medians(subject, reference, resamples, seed))
    return ratio_of_medians(reference, subject, resamples, seed)


def evaluate_dataset(
    prediction: Prediction, dataset: str, groups: dict, settings: AnalysisSettings
) -> DatasetEvaluation:
    subject = values(groups.get((prediction.subject, dataset), []), prediction.metric)
    if prediction.reference is None:
        reference: list[float] | float = prediction.threshold
        reference_runs = None
    else:
        reference = values(groups.get((prediction.reference, dataset), []), prediction.metric)
        reference_runs = len(reference)
    if not subject or (isinstance(reference, list) and not reference):
        return DatasetEvaluation(
            dataset=dataset,
            status="no_data",
            subject_median=None,
            threshold=None,
            ratio=None,
            subject_runs=len(subject),
            reference_runs=reference_runs,
        )
    subject_median = statistics.median(subject)
    threshold = reference if isinstance(reference, float) else statistics.median(reference)
    if subject_median == 0 or threshold == 0:
        held = (
            subject_median < threshold if prediction.relation == "<" else subject_median > threshold
        )
        return DatasetEvaluation(
            dataset=dataset,
            status="held" if held else "refuted",
            subject_median=subject_median,
            threshold=threshold,
            ratio=None,
            subject_runs=len(subject),
            reference_runs=reference_runs,
        )
    ratio = predicted_ratio(prediction, subject, reference, settings)
    return DatasetEvaluation(
        dataset=dataset,
        status=ratio_status(ratio),
        subject_median=subject_median,
        threshold=threshold,
        ratio=ratio,
        subject_runs=len(subject),
        reference_runs=reference_runs,
    )


def overall(statuses: list[str]) -> str:
    if "refuted" in statuses:
        return "refuted"
    if "no_data" in statuses:
        return "pending"
    if "narrow" in statuses:
        return "narrow"
    return "held"


def evaluate(prediction: Prediction, runs: list[Run], settings: AnalysisSettings) -> Evaluation:
    groups = group_runs(runs)
    datasets = [evaluate_dataset(prediction, d, groups, settings) for d in prediction.datasets]
    status = overall([d.status for d in datasets])
    method = (
        f"Ratio of medians, predicted larger over predicted smaller. Held when the bootstrap 95% "
        f"interval is above 1, refuted when below 1, narrow when it includes 1. "
        f"{settings.bootstrap_resamples} resamples, seed {settings.bootstrap_seed}."
    )
    return Evaluation(status=status, symbol=SYMBOLS[status], method=method, datasets=datasets)
