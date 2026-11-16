from collections import defaultdict
from typing import Literal, Protocol

from pydantic import BaseModel

from egb.analysis import Analysis, AnalysisSettings, ConfigKey, Summary
from egb.predictions import Prediction
from egb.registry import Registry
from egb.sessions import LogEntry
from egb.validity import side_effects


class Observation(BaseModel):
    id: str
    kind: str
    configurations: list[ConfigKey]
    datasets: list[str]
    facts: dict[str, float | str]
    statement: str


class Change(BaseModel):
    kind: Literal["representation", "algorithm", "hardware", "datasets", "validity"]
    source: ConfigKey | None
    target: ConfigKey | None
    datasets: list[str]


class PredictionDraft(BaseModel):
    text: str
    prediction: Prediction | None
    note: str | None = None


class ValidityCheckDraft(BaseModel):
    text: str
    check: Literal["l1", "identical", "nmi_between", "modularity_difference"]
    expected: str
    targeted: ConfigKey
    baseline: ConfigKey
    datasets: list[str]


class Suggestion(BaseModel):
    key: str
    rank: int
    score: float
    source: str
    rule: str
    hypothesis: str
    observation_ids: list[str]
    change: Change
    predictions: list[PredictionDraft]
    validity_checks: list[ValidityCheckDraft]
    caveats: list[str]


class SuggestionRequest(BaseModel):
    analysis: Analysis
    observations: list[Observation]
    alternatives: list[ConfigKey]
    history: list[LogEntry]
    limit: int = 10


class SuggestionResponse(BaseModel):
    suggester: str
    status: Literal["complete", "unavailable"]
    message: str | None
    suggestions: list[Suggestion]


class HypothesisSuggester(Protocol):
    id: str

    async def suggest(self, request: SuggestionRequest) -> SuggestionResponse: ...


def seconds(value: float) -> str:
    return f"{value:.3g} s"


class Namer:
    def __init__(self, registry: Registry):
        self.registry = registry

    def algorithm(self, key: ConfigKey) -> str:
        return self.registry.algorithms[
            self.registry.implementations[key.implementation].algorithm
        ].name

    def short(self, key: ConfigKey) -> str:
        representation = self.registry.representations[key.representation].name
        return f"{key.hardware} {self.algorithm(key)}, {representation}"

    def metric(self, metric_id: str) -> str:
        if metric_id in self.registry.metrics:
            return self.registry.metrics[metric_id].name.lower().replace("etl", "ETL")
        return metric_id.replace("_", " ")

    def datasets(self, ids: list[str]) -> str:
        return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + f" and {ids[-1]}"

    def values(self, pairs: list[tuple[str, str]]) -> str:
        return self.datasets([f"{value} on {dataset}" for value, dataset in pairs])


def summary_index(analysis: Analysis) -> dict[tuple[ConfigKey, str], Summary]:
    return {(s.config, s.dataset): s for s in analysis.summaries}


def observe(analysis: Analysis, registry: Registry) -> list[Observation]:
    names = Namer(registry)
    found = []
    for b in analysis.bottlenecks:
        phase = "ETL" if b.dominant == "etl" else "Processing"
        found.append(
            Observation(
                id=f"phase:{b.config.id}:{b.dataset}",
                kind="phase_share",
                configurations=[b.config],
                datasets=[b.dataset],
                facts={
                    "dominant": b.dominant,
                    "share": b.share,
                    "end_to_end_s": b.end_to_end_s,
                    "fastest": b.fastest.id,
                    "fastest_end_to_end_s": b.fastest_end_to_end_s,
                },
                statement=f"{phase}: {b.share:.0%} of end-to-end time. {names.short(b.config)}, {b.dataset}.",
            )
        )
    for d in analysis.differences:
        if d.kind == "faster_processing_slower_end_to_end":
            statement = (
                f"{names.short(d.config)} processes faster but is slower end to end on {d.dataset}: "
                f"{seconds(d.values['config_end_to_end_s'])} against {seconds(d.values['other_end_to_end_s'])}."
            )
        else:
            metric = d.metrics[0]
            statement = (
                f"{names.short(d.config)} is faster on {d.dataset} with different {names.metric(metric)}: "
                f"{d.values[f'config_{metric}']:.4g} against {d.values[f'other_{metric}']:.4g}."
            )
        found.append(
            Observation(
                id=f"{d.kind}:{d.config.id}:{d.other.id}:{d.dataset}",
                kind=d.kind,
                configurations=[d.config, d.other],
                datasets=[d.dataset],
                facts=d.values,
                statement=statement,
            )
        )
    for c in analysis.crossovers:
        winners = ", ".join(f"{names.short(k)} on {d}" for d, k in c.winners.items())
        found.append(
            Observation(
                id=f"crossover:{c.task}",
                kind="crossover",
                configurations=sorted(set(c.winners.values()), key=lambda k: k.id),
                datasets=list(c.winners),
                facts={f"edges:{d}": e for d, e in c.edges.items()},
                statement=f"Fastest {registry.tasks[c.task].name.lower()} configuration changes with dataset: {winners}.",
            )
        )
    return found


class RuleSuggester:
    id = "rules"

    def __init__(self, registry: Registry, settings: AnalysisSettings):
        self.registry = registry
        self.settings = settings
        self.names = Namer(registry)

    async def suggest(self, request: SuggestionRequest) -> SuggestionResponse:
        measured = {s.config for s in request.analysis.summaries}
        candidates = [a for a in request.alternatives if a not in measured]
        suggestions = (
            self.etl_dominates(request.analysis, candidates)
            + self.processing_dominates(request.analysis, candidates)
            + self.different_effectiveness(request.analysis)
            + self.dataset_dependent(request.analysis)
        )
        recorded = {e.suggestion.get("key") for e in request.history if e.suggestion}
        fresh = sorted(
            (s for s in suggestions if s.key not in recorded), key=lambda s: (-s.score, s.key)
        )
        ranked = [
            s.model_copy(update={"rank": i + 1}) for i, s in enumerate(fresh[: request.limit])
        ]
        return SuggestionResponse(
            suggester=self.id, status="complete", message=None, suggestions=ranked
        )

    def losses(
        self, analysis: Analysis, share_of: Literal["etl", "processing"], minimum: float
    ) -> dict[ConfigKey, list[Summary]]:
        index = summary_index(analysis)
        found: dict[ConfigKey, list[Summary]] = defaultdict(list)
        for b in analysis.bottlenecks:
            share = index[(b.config, b.dataset)].etl_share
            if share_of == "processing":
                share = 1 - share
            if share >= minimum and b.config != b.fastest:
                found[b.config].append(index[(b.config, b.dataset)])
        return found

    def fastest_of(self, analysis: Analysis, summary: Summary) -> Summary:
        index = summary_index(analysis)
        bottleneck = next(
            b
            for b in analysis.bottlenecks
            if b.config == summary.config and b.dataset == summary.dataset
        )
        return index[(bottleneck.fastest, summary.dataset)]

    def time_predictions(
        self, analysis: Analysis, subject: ConfigKey, losing: list[Summary]
    ) -> list[PredictionDraft]:
        by_reference: dict[ConfigKey, list[Summary]] = defaultdict(list)
        for summary in losing:
            by_reference[self.fastest_of(analysis, summary).config].append(summary)
        drafts = []
        for reference, group in sorted(by_reference.items(), key=lambda item: item[0].id):
            medians = [self.fastest_of(analysis, s).end_to_end_s.median for s in group]
            places = self.names.values([(seconds(m), s.dataset) for m, s in zip(medians, group)])
            drafts.append(
                PredictionDraft(
                    text=f"{self.names.short(subject)}: end-to-end time below {places} "
                    f"({self.names.short(reference)} medians).",
                    prediction=Prediction(
                        metric="end_to_end_s",
                        subject=subject,
                        relation="<",
                        reference=reference,
                        datasets=[s.dataset for s in group],
                    ),
                )
            )
        return drafts

    def gap(self, analysis: Analysis, losing: list[Summary]) -> float:
        return max(
            s.end_to_end_s.median / self.fastest_of(analysis, s).end_to_end_s.median for s in losing
        )

    def same_output_checks(
        self, targeted: ConfigKey, baseline: ConfigKey, datasets: list[str]
    ) -> list[ValidityCheckDraft]:
        if self.registry.task_of(targeted.implementation).output == "scores":
            return [
                ValidityCheckDraft(
                    text=f"Scores match {self.names.short(baseline)} within L1 "
                    f"{self.settings.validity_l1_tolerance:g}.",
                    check="l1",
                    expected=f"<= {self.settings.validity_l1_tolerance:g}",
                    targeted=targeted,
                    baseline=baseline,
                    datasets=datasets,
                )
            ]
        return [
            ValidityCheckDraft(
                text=f"Partitions identical to {self.names.short(baseline)} at the same seed.",
                check="identical",
                expected="true",
                targeted=targeted,
                baseline=baseline,
                datasets=datasets,
            ),
            ValidityCheckDraft(
                text=f"Modularity within {self.settings.modularity_tolerance:g} of "
                f"{self.names.short(baseline)}.",
                check="modularity_difference",
                expected=f"within {self.settings.modularity_tolerance:g}",
                targeted=targeted,
                baseline=baseline,
                datasets=datasets,
            ),
        ]

    def etl_dominates(self, analysis: Analysis, candidates: list[ConfigKey]) -> list[Suggestion]:
        suggestions = []
        for config, losing in self.losses(
            analysis, "etl", self.settings.etl_dominant_share
        ).items():
            current = self.registry.representations[config.representation]
            options = [
                c
                for c in candidates
                if c.hardware == config.hardware
                and c.implementation == config.implementation
                and c.representation != config.representation
            ]
            if not options:
                continue
            target = min(
                options,
                key=lambda c: (
                    self.registry.representations[c.representation].precision != current.precision,
                    c.representation,
                ),
            )
            datasets = [s.dataset for s in losing]
            caveats = side_effects(target, config, self.registry)
            others = [
                self.registry.representations[o.representation].name for o in options if o != target
            ]
            if others:
                caveats.append(f"Other options: {', '.join(others)}.")
            share = max(s.etl_share for s in losing)
            suggestions.append(
                Suggestion(
                    key=f"etl-dominates:{config.id}:{target.id}",
                    rank=0,
                    score=self.gap(analysis, losing),
                    source="rules:etl-dominates",
                    rule="etl-dominates",
                    hypothesis=(
                        f"{self.names.short(config)} loses end to end on {self.names.datasets(datasets)} "
                        f"because of loading (ETL up to {share:.0%}), not computation."
                    ),
                    observation_ids=[f"phase:{config.id}:{s.dataset}" for s in losing],
                    change=Change(
                        kind="representation", source=config, target=target, datasets=datasets
                    ),
                    predictions=self.time_predictions(analysis, target, losing),
                    validity_checks=self.same_output_checks(target, config, datasets),
                    caveats=caveats,
                )
            )
        return suggestions

    def quality_metric(self, config: ConfigKey) -> str:
        task = self.registry.task_of(config.implementation).id
        return next(
            m.id
            for m in self.registry.metrics.values()
            if task in m.tasks and m.better == "higher" and not m.requires_ground_truth
        )

    def processing_dominates(
        self, analysis: Analysis, candidates: list[ConfigKey]
    ) -> list[Suggestion]:
        suggestions = []
        for config, losing in self.losses(
            analysis, "processing", self.settings.processing_dominant_share
        ).items():
            task = self.registry.task_of(config.implementation).id
            algorithm = self.registry.implementations[config.implementation].algorithm
            for target in candidates:
                target_algorithm = self.registry.implementations[target.implementation].algorithm
                if self.registry.task_of(target.implementation).id != task:
                    continue
                if target.hardware == config.hardware and target_algorithm != algorithm:
                    kind = "algorithm"
                elif (
                    target.hardware != config.hardware
                    and target_algorithm == algorithm
                    and target.representation == config.representation
                    and self.registry.implementations[target.implementation].requires
                ):
                    kind = "hardware"
                else:
                    continue
                suggestions.append(self.processing_change(analysis, config, target, losing, kind))
        return suggestions

    def processing_change(
        self,
        analysis: Analysis,
        config: ConfigKey,
        target: ConfigKey,
        losing: list[Summary],
        kind: str,
    ) -> Suggestion:
        datasets = [s.dataset for s in losing]
        share = min(1 - s.etl_share for s in losing)
        predictions = self.time_predictions(analysis, target, losing)
        caveats = []
        checks = []
        if kind == "algorithm":
            quality = self.quality_metric(config)
            values = [s.effectiveness[quality].median for s in losing if quality in s.effectiveness]
            places = self.names.values([(f"{v:.4g}", s.dataset) for v, s in zip(values, losing)])
            predictions.append(
                PredictionDraft(
                    text=f"{self.names.short(target)}: {self.names.metric(quality)} below {places} "
                    f"({self.names.short(config)} medians).",
                    prediction=Prediction(
                        metric=quality,
                        subject=target,
                        relation="<",
                        reference=config,
                        datasets=datasets,
                    ),
                )
            )
            caveats.append(
                f"Algorithm changes from {self.names.algorithm(config)} to {self.names.algorithm(target)}; "
                f"outputs will differ."
            )
            change_text = f"switching to {self.names.algorithm(target)}"
        else:
            checks = self.same_output_checks(target, config, datasets)
            change_text = f"moving to {target.hardware}"
        return Suggestion(
            key=f"processing-dominates:{config.id}:{target.id}",
            rank=0,
            score=self.gap(analysis, losing),
            source="rules:processing-dominates",
            rule="processing-dominates",
            hypothesis=(
                f"{self.names.short(config)} loses on {self.names.datasets(datasets)} because processing "
                f"dominates (at least {share:.0%}); {change_text} cuts it."
            ),
            observation_ids=[f"phase:{config.id}:{s.dataset}" for s in losing],
            change=Change(kind=kind, source=config, target=target, datasets=datasets),
            predictions=predictions,
            validity_checks=checks,
            caveats=caveats,
        )

    def different_effectiveness(self, analysis: Analysis) -> list[Suggestion]:
        suggestions = []
        index = summary_index(analysis)
        for d in analysis.differences:
            if d.kind != "faster_with_different_effectiveness":
                continue
            metric = d.metrics[0]
            ours, theirs = d.values[f"config_{metric}"], d.values[f"other_{metric}"]
            relation = "<" if ours < theirs else ">"
            word = "below" if relation == "<" else "above"
            suggestions.append(
                Suggestion(
                    key=f"different-effectiveness:{d.config.id}:{d.other.id}:{d.dataset}",
                    rank=0,
                    score=abs(ours - theirs) / max(abs(theirs), 1e-12),
                    source="rules:different-effectiveness",
                    rule="different-effectiveness",
                    hypothesis=(
                        f"{self.names.short(d.config)} is faster on {d.dataset} but its output differs from "
                        f"{self.names.short(d.other)}."
                    ),
                    observation_ids=[f"{d.kind}:{d.config.id}:{d.other.id}:{d.dataset}"],
                    change=Change(
                        kind="validity", source=d.other, target=d.config, datasets=[d.dataset]
                    ),
                    predictions=[
                        PredictionDraft(
                            text=f"{self.names.short(d.config)}: {self.names.metric(metric)} stays {word} "
                            f"{theirs:.4g} on {d.dataset} ({self.names.short(d.other)} median).",
                            prediction=Prediction(
                                metric=metric,
                                subject=d.config,
                                relation=relation,
                                reference=d.other,
                                datasets=[d.dataset],
                            ),
                        )
                    ],
                    validity_checks=self.same_output_checks(d.config, d.other, [d.dataset]),
                    caveats=[
                        f"{index[(d.config, d.dataset)].runs} and {index[(d.other, d.dataset)].runs} runs."
                    ],
                )
            )
        return suggestions

    def dataset_dependent(self, analysis: Analysis) -> list[Suggestion]:
        suggestions = []
        measured = {(s.task, s.dataset) for s in analysis.summaries}
        for c in analysis.crossovers:
            ordered = list(c.winners)
            small, large = c.winners[ordered[0]], c.winners[ordered[-1]]
            boundary = next(i for i, d in enumerate(ordered) if c.winners[d] != small)
            low, high = c.edges[ordered[boundary - 1]], c.edges[ordered[boundary]]
            unmeasured = [
                d.id for d in self.registry.datasets.values() if (c.task, d.id) not in measured
            ]
            between = [d for d in unmeasured if low < self.registry.datasets[d].edges < high]
            note = None
            prediction = None
            if between:
                prediction = Prediction(
                    metric="end_to_end_s",
                    subject=large,
                    relation="<",
                    reference=small,
                    datasets=between,
                )
            else:
                note = "No registered dataset lies in that range. Add one before recording."
            suggestions.append(
                Suggestion(
                    key=f"dataset-dependent:{c.task}:{small.id}:{large.id}",
                    rank=0,
                    score=1.0,
                    source="rules:dataset-dependent",
                    rule="dataset-dependent",
                    hypothesis=(
                        f"The fastest {self.registry.tasks[c.task].name.lower()} configuration depends on graph "
                        f"size: {self.names.short(small)} up to {low:,} edges, "
                        f"{self.names.short(large)} from {high:,}."
                    ),
                    observation_ids=[f"crossover:{c.task}"],
                    change=Change(kind="datasets", source=None, target=None, datasets=between),
                    predictions=[
                        PredictionDraft(
                            text=f"The crossover lies between {low:,} and {high:,} edges.",
                            prediction=prediction,
                            note=note,
                        )
                    ],
                    validity_checks=[],
                    caveats=[f"Measured on {len(ordered)} datasets."],
                )
            )
        return suggestions


def runnable_alternatives(
    registry: Registry, capabilities: dict[str, list[str]]
) -> list[ConfigKey]:
    found = []
    for hardware, available in sorted(capabilities.items()):
        for implementation in registry.implementations.values():
            for variant in implementation.variants:
                needed = ["cpu", *registry.requirements(implementation.id, variant.representation)]
                if set(needed) <= set(available):
                    found.append(
                        ConfigKey(
                            hardware=hardware,
                            implementation=implementation.id,
                            representation=variant.representation,
                        )
                    )
    return found


class AgentSuggester:
    id = "agent"

    async def suggest(self, request: SuggestionRequest) -> SuggestionResponse:
        return SuggestionResponse(
            suggester=self.id,
            status="unavailable",
            message="Agent suggestions are not available yet. Use the rule-based suggester.",
            suggestions=[],
        )
