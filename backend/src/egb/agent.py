import json
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Literal

import anthropic
import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from egb.analysis import AnalysisSettings, ConfigKey, Summary
from egb.predictions import PREDICTABLE, Prediction
from egb.registry import Registry
from egb.suggesters import (
    CallStats,
    Change,
    PredictionDraft,
    RejectedSuggestion,
    RuleSuggester,
    Suggestion,
    SuggestionRequest,
    SuggestionResponse,
    same_output_checks,
)
from egb.validity import side_effects

DIMENSIONS = ("hardware", "algorithm", "representation")
PHASES = ("etl_s", "processing_s", "end_to_end_s")
LOADING_DEPENDENT = ("etl_s", "processing_s", "etl_share")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
PROMPT = Path(__file__).with_name("prompts") / "agent_system.md"
BANNED = ("powerful", "seamless", "unlock", "effortless", "let's", "simply", "dive")


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    api_key_env: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_tokens: int
    timeout_seconds: float
    max_hypotheses: int
    rationale_max_words: int
    threshold_match_tolerance: float
    temperature: float | None
    server_fallback: bool
    price_per_million: dict[Literal["input", "output", "cache_read", "cache_write"], float]


def load_agent_settings(path: Path) -> AgentSettings:
    return AgentSettings.model_validate(yaml.safe_load(path.read_text()))


def config_schema(hardware: list[str], registry: Registry) -> dict:
    return {
        "type": "object",
        "properties": {
            "hardware": {"type": "string", "enum": sorted(hardware)},
            "implementation": {"type": "string", "enum": sorted(registry.implementations)},
            "representation": {"type": "string", "enum": sorted(registry.representations)},
        },
        "required": ["hardware", "implementation", "representation"],
        "additionalProperties": False,
    }


def output_schema(hardware: list[str], datasets: list[str], registry: Registry) -> dict:
    config = config_schema(hardware, registry)
    prediction = {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "enum": list(PREDICTABLE)},
            "relation": {"type": "string", "enum": ["<", ">"]},
            "threshold_mode": {"type": "string", "enum": ["reference", "value"]},
            "reference": config,
            "value": {"type": "number"},
            "datasets": {"type": "array", "items": {"type": "string", "enum": sorted(datasets)}},
        },
        "required": ["metric", "relation", "threshold_mode", "reference", "value", "datasets"],
        "additionalProperties": False,
    }
    hypothesis = {
        "type": "object",
        "properties": {
            "observation": {"type": "string"},
            "dimension": {"type": "string", "enum": list(DIMENSIONS)},
            "baseline": config,
            "target": config,
            "hypothesis": {"type": "string"},
            "prediction": prediction,
            "prediction_text": {"type": "string"},
            "rationale": {"type": "string"},
        },
        "required": [
            "observation",
            "dimension",
            "baseline",
            "target",
            "hypothesis",
            "prediction",
            "prediction_text",
            "rationale",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"hypotheses": {"type": "array", "items": hypothesis}},
        "required": ["hypotheses"],
        "additionalProperties": False,
    }


def summary_context(summary: Summary) -> dict:
    return {
        "config": summary.config.model_dump(),
        "dataset": summary.dataset,
        "runs": summary.runs,
        "etl_s": summary.etl_s.median,
        "processing_s": summary.processing_s.median,
        "end_to_end_s": summary.end_to_end_s.median,
        "etl_share": round(summary.etl_share, 4),
        "dominant_phase": "etl" if summary.etl_share >= 0.5 else "processing",
        "effectiveness": {name: spread.median for name, spread in summary.effectiveness.items()},
    }


def build_context(request: SuggestionRequest) -> dict:
    trail, flags = [], []
    for entry in request.history:
        details = entry.details or {}
        trail.append(
            {
                "id": entry.id,
                "iteration": entry.iteration,
                "kind": entry.kind,
                "text": entry.text,
                "prediction": entry.prediction.model_dump() if entry.prediction else None,
                "change": (entry.suggestion or {}).get("change"),
                "status": details.get("status"),
            }
        )
        if entry.kind == "outcome" and (
            details.get("side_effects") or details.get("failed_checks")
        ):
            flags.append(
                {
                    "iteration": entry.iteration,
                    "side_effects": details.get("side_effects", []),
                    "failed_checks": details.get("failed_checks", []),
                }
            )
    return {
        "workload": request.workload,
        "configurations": request.configurations,
        "summaries": [summary_context(s) for s in request.analysis.summaries],
        "validity_flags": flags,
        "trail": trail,
    }


def changed_settings(baseline: ConfigKey, target: ConfigKey, registry: Registry) -> list[str]:
    old = registry.implementations[baseline.implementation]
    new = registry.implementations[target.implementation]
    changed = []
    if baseline.hardware != target.hardware or (
        old.id != new.id and old.algorithm == new.algorithm
    ):
        changed.append("hardware")
    if old.algorithm != new.algorithm:
        changed.append("algorithm")
    if baseline.representation != target.representation:
        changed.append("representation")
    return changed


def metric_median(summary: Summary, metric: str) -> float | None:
    if metric == "etl_share":
        return summary.etl_share
    if metric in PHASES:
        return getattr(summary, metric).median
    spread = summary.effectiveness.get(metric)
    return spread.median if spread else None


def copy_problems(text: str, limit: int) -> list[str]:
    problems = []
    words = len(re.findall(r"[A-Za-z0-9][\w.,%/:+-]*", text))
    if words > limit:
        problems.append(f"Rationale has {words} words; the limit is {limit}.")
    if "\u2014" in text or "!" in text or any(re.search(rf"\b{b}\b", text.lower()) for b in BANNED):
        problems.append("Rationale breaks the copy rules.")
    return problems


class Validator:
    def __init__(
        self,
        registry: Registry,
        analysis: AnalysisSettings,
        settings: AgentSettings,
        request: SuggestionRequest,
    ):
        self.registry = registry
        self.analysis_settings = analysis
        self.settings = settings
        self.request = request
        self.summaries = request.analysis.summaries
        self.measured = {s.config for s in self.summaries}
        self.runnable = set(request.alternatives)
        self.workload = set(request.workload.get("datasets", {}))
        self.recorded_pairs = set()
        self.recorded_texts = set()
        for entry in request.history:
            change = (entry.suggestion or {}).get("change") or {}
            if change.get("source") and change.get("target"):
                self.recorded_pairs.add(
                    (ConfigKey(**change["source"]).id, ConfigKey(**change["target"]).id)
                )
            if entry.kind == "hypothesis":
                self.recorded_texts.add(entry.text.strip().lower())

    def summary(self, config: ConfigKey, dataset: str) -> Summary | None:
        return next(
            (s for s in self.summaries if s.config == config and s.dataset == dataset), None
        )

    def threshold(
        self, raw: dict, target: ConfigKey, reasons: list[str], repairs: list[str]
    ) -> dict | None:
        metric, datasets = raw["metric"], raw["datasets"]
        if raw["threshold_mode"] == "reference":
            reference = ConfigKey(**raw["reference"])
            if (
                reference.implementation not in self.registry.implementations
                or self.registry.task_of(reference.implementation)
                != self.registry.task_of(target.implementation)
            ):
                reasons.append("Reference solves a different task.")
                return None
            missing = [
                d
                for d in datasets
                if not self.summary(reference, d)
                or metric_median(self.summary(reference, d), metric) is None
            ]
            if missing:
                reasons.append(f"Reference has no {metric} results on {', '.join(missing)}.")
                return None
            return {"reference": reference, "threshold": None}
        value = raw["value"]
        if not math.isfinite(value) or len(datasets) != 1:
            reasons.append("A fixed threshold needs one dataset and a finite value.")
            return None
        task = self.registry.task_of(target.implementation).id
        for s in self.summaries:
            median = (
                metric_median(s, metric) if s.dataset == datasets[0] and s.task == task else None
            )
            if median and abs(median - value) <= self.settings.threshold_match_tolerance * abs(
                median
            ):
                repairs.append(f"Threshold tied to the {s.config.id} median.")
                return {"reference": s.config, "threshold": None}
        reasons.append("Threshold does not match any measured median.")
        return None

    def check(self, raw: dict, seen: set[tuple[str, str]]) -> tuple[Suggestion | None, list[str]]:
        reasons, repairs = [], []
        baseline, target = ConfigKey(**raw["baseline"]), ConfigKey(**raw["target"])
        if baseline not in self.measured:
            reasons.append("Baseline has no results.")
        try:
            self.registry.variant(target.implementation, target.representation)
        except KeyError:
            reasons.append("Target is not a registry configuration.")
            return None, reasons
        if target not in self.runnable:
            reasons.append("Target cannot run on any available hardware.")
        if target in self.measured:
            reasons.append("Target already has results.")
        changed = (
            changed_settings(baseline, target, self.registry)
            if baseline.implementation in self.registry.implementations
            else []
        )
        dimension = raw["dimension"]
        if len(changed) != 1:
            reasons.append(f"Changes {len(changed)} settings: {', '.join(changed) or 'none'}.")
        elif changed[0] != dimension:
            repairs.append(f"Dimension corrected from {dimension} to {changed[0]}.")
            dimension = changed[0]
        pair = (baseline.id, target.id)
        if pair in self.recorded_pairs or raw["hypothesis"].strip().lower() in self.recorded_texts:
            reasons.append("Repeats a hypothesis in the trail.")
        if pair in seen:
            reasons.append("Repeats another suggestion in this response.")
        raw_prediction = raw["prediction"]
        task = self.registry.task_of(target.implementation).id
        metric = raw_prediction["metric"]
        if (
            metric not in PHASES
            and metric != "etl_share"
            and task not in self.registry.metrics[metric].tasks
        ):
            reasons.append(f"{metric} does not apply to {task}.")
        if dimension == "representation" and metric in LOADING_DEPENDENT:
            reasons.append("Phase times differ by loading path; predict end-to-end time instead.")
        datasets = raw_prediction["datasets"]
        if not datasets or not set(datasets) <= self.workload:
            reasons.append("Prediction needs datasets from the workload.")
        threshold = (
            self.threshold(raw_prediction, target, reasons, repairs) if not reasons else None
        )
        reasons += copy_problems(raw["rationale"], self.settings.rationale_max_words)
        if reasons:
            return None, reasons
        try:
            prediction = Prediction(
                metric=metric,
                subject=target,
                relation=raw_prediction["relation"],
                datasets=datasets,
                **threshold,
            )
        except ValidationError as error:
            return None, [f"Prediction is not testable: {error.errors()[0]['msg']}."]
        seen.add(pair)
        return Suggestion(
            key=f"agent:{baseline.id}:{target.id}",
            rank=0,
            score=0,
            source=f"agent:{self.settings.model}",
            rule="agent",
            hypothesis=raw["hypothesis"],
            observation_ids=[],
            change=Change(kind=dimension, source=baseline, target=target, datasets=datasets),
            predictions=[PredictionDraft(text=raw["prediction_text"], prediction=prediction)],
            validity_checks=[]
            if dimension == "algorithm"
            else same_output_checks(
                self.registry, self.analysis_settings, target, baseline, datasets
            ),
            caveats=side_effects(target, baseline, self.registry),
            observation=raw["observation"],
            dimension=dimension,
            rationale=raw["rationale"],
            repairs=repairs,
        ), []

    def run(self, items: list[dict]) -> tuple[list[Suggestion], list[RejectedSuggestion]]:
        accepted, rejected, seen = [], [], set()
        for raw in items[: self.settings.max_hypotheses]:
            suggestion, reasons = self.check(raw, seen)
            if suggestion is None:
                rejected.append(
                    RejectedSuggestion(hypothesis=raw.get("hypothesis", ""), reasons=reasons)
                )
            else:
                accepted.append(
                    suggestion.model_copy(
                        update={"rank": len(accepted) + 1, "score": 1 / (len(accepted) + 1)}
                    )
                )
        for raw in items[self.settings.max_hypotheses :]:
            rejected.append(
                RejectedSuggestion(
                    hypothesis=raw.get("hypothesis", ""),
                    reasons=[f"More than {self.settings.max_hypotheses} hypotheses."],
                )
            )
        return accepted, rejected


def call_stats(message, settings: AgentSettings, latency: float) -> CallStats:
    usage = message.usage
    read = usage.cache_read_input_tokens or 0
    write = usage.cache_creation_input_tokens or 0
    prices = settings.price_per_million
    cost = (
        usage.input_tokens * prices["input"]
        + usage.output_tokens * prices["output"]
        + read * prices["cache_read"]
        + write * prices["cache_write"]
    ) / 1_000_000
    return CallStats(
        model=message.model,
        latency_seconds=round(latency, 2),
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=read,
        cache_write_tokens=write,
        cost_usd=round(cost, 4),
        stop_reason=message.stop_reason,
    )


def default_client(key: str, settings: AgentSettings):
    return anthropic.AsyncAnthropic(api_key=key, timeout=settings.timeout_seconds)


class AgentSuggester:
    id = "agent"

    def __init__(
        self,
        registry: Registry,
        analysis: AnalysisSettings,
        settings: AgentSettings,
        rules: RuleSuggester,
        client_factory: Callable = default_client,
        environ: Mapping[str, str] = os.environ,
    ):
        self.registry = registry
        self.analysis = analysis
        self.settings = settings
        self.rules = rules
        self.client_factory = client_factory
        self.environ = environ
        self.system = PROMPT.read_text()

    def request_body(self, request: SuggestionRequest) -> dict:
        hardware = sorted({c["hardware"] for c in request.configurations})
        datasets = sorted(request.workload.get("datasets", {}))
        body = {
            "model": self.settings.model,
            "max_tokens": self.settings.max_tokens,
            "system": self.system,
            "messages": [
                {"role": "user", "content": json.dumps(build_context(request), sort_keys=True)}
            ],
            "output_config": {
                "effort": self.settings.effort,
                "format": {
                    "type": "json_schema",
                    "schema": output_schema(hardware, datasets, self.registry),
                },
            },
        }
        if self.settings.server_fallback:
            body |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        if self.settings.temperature is not None:
            body["extra_body"] = {"temperature": self.settings.temperature}
        return body

    async def fallback(
        self, request: SuggestionRequest, reason: str, rejected=(), calls=()
    ) -> SuggestionResponse:
        response = await self.rules.suggest(request)
        return response.model_copy(
            update={
                "message": f"Rule suggestions shown. {reason}",
                "fallback_reason": reason,
                "rejected": list(rejected),
                "calls": list(calls),
            }
        )

    async def suggest(self, request: SuggestionRequest) -> SuggestionResponse:
        key = self.environ.get(self.settings.api_key_env)
        if not key:
            return await self.fallback(
                request, f"Agent off: {self.settings.api_key_env} is not set."
            )
        client = self.client_factory(key, self.settings)
        start = time.monotonic()
        try:
            message = await client.beta.messages.create(**self.request_body(request))
        except anthropic.APIStatusError as error:
            return await self.fallback(
                request, f"Agent call failed with status {error.status_code}."
            )
        except anthropic.APIConnectionError:
            return await self.fallback(request, "Agent call failed: no connection.")
        stats = call_stats(message, self.settings, time.monotonic() - start)
        if message.stop_reason != "end_turn":
            return await self.fallback(
                request, f"Agent stopped early: {message.stop_reason}.", calls=[stats]
            )
        text = next((b.text for b in message.content if b.type == "text"), "")
        try:
            items = json.loads(text)["hypotheses"]
        except (ValueError, KeyError):
            return await self.fallback(request, "Agent output was not valid JSON.", calls=[stats])
        accepted, rejected = Validator(self.registry, self.analysis, self.settings, request).run(
            items
        )
        if not accepted:
            return await self.fallback(
                request, "No agent suggestion passed validation.", rejected, [stats]
            )
        return SuggestionResponse(
            suggester=self.id,
            status="complete",
            message=None,
            suggestions=accepted,
            rejected=rejected,
            calls=[stats],
        )
