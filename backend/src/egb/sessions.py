import datetime
import hashlib
import json
import secrets
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from egb.datasets import sha256
from egb.registry import Registry

LOG_KINDS = Literal[
    "observation",
    "hypothesis",
    "prediction",
    "validity_check",
    "revision",
    "amendment",
    "outcome",
    "explanation",
    "decision",
]
TERMINAL = {"finished", "failed", "cancelled", "interrupted"}
STATE_PATTERNS = [
    "config.json",
    "config_history.jsonl",
    "environment/*.json",
    "jobs/*/results.csv",
    "jobs/*/cross_path_*.csv",
]


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(canonical(value)).hexdigest()


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class ConfigurationInput(BaseModel):
    implementation: str
    representation: str
    exploratory: bool = False


class Configuration(BaseModel):
    id: str
    implementation: str
    representation: str
    color_slot: int


class ConfigInput(BaseModel):
    tasks: list[str]
    datasets: list[str]
    candidates: list[ConfigurationInput]
    metrics: list[str]
    repetitions: int = Field(default=10, ge=1)
    base_seed: int = 123


class SessionConfig(BaseModel):
    tasks: list[str]
    datasets: list[str]
    candidates: list[Configuration]
    exploratory: list[str]
    metrics: list[str]
    repetitions: int
    base_seed: int

    def candidate(self, configuration_id: str) -> Configuration:
        for candidate in self.candidates:
            if candidate.id == configuration_id:
                return candidate
        raise KeyError(configuration_id)


class SessionCreate(BaseModel):
    name: str
    question: str
    config: ConfigInput


class SessionMeta(BaseModel):
    id: str
    name: str
    question: str
    created_at: str
    app_commit: str | None
    gaib_commit: str | None


class Session(BaseModel):
    meta: SessionMeta
    config: SessionConfig


class LogEntryInput(BaseModel):
    kind: LOG_KINDS
    text: str
    iteration: int = Field(ge=0)
    refers_to: list[str] = []
    prediction: dict | None = None
    source: str = "user"
    suggestion: dict | None = None


class LogEntry(LogEntryInput):
    seq: int
    id: str
    recorded_at: str
    state_manifest: list[tuple[str, str]]
    state_hash: str
    prev_entry_hash: str | None
    entry_hash: str


class LogCheck(BaseModel):
    entries: int
    problems: list[str]


def config_errors(config: ConfigInput, registry: Registry) -> list[str]:
    errors = [f"Unknown task {t}." for t in config.tasks if t not in registry.tasks]
    errors += [f"Unknown dataset {d}." for d in config.datasets if d not in registry.datasets]
    seen = set()
    for candidate in config.candidates:
        key = (candidate.implementation, candidate.representation)
        if key in seen:
            errors.append(
                f"{candidate.implementation} with {candidate.representation} is listed twice."
            )
        seen.add(key)
        if candidate.implementation not in registry.implementations:
            errors.append(f"Unknown implementation {candidate.implementation}.")
            continue
        try:
            registry.variant(candidate.implementation, candidate.representation)
        except KeyError:
            errors.append(f"{candidate.implementation} has no {candidate.representation} variant.")
        if registry.task_of(candidate.implementation).id not in config.tasks:
            errors.append(f"{candidate.implementation} is not for a selected task.")
    for metric in config.metrics:
        if metric not in registry.metrics:
            errors.append(f"Unknown metric {metric}.")
        elif not set(registry.metrics[metric].tasks) & set(config.tasks):
            errors.append(f"{metric} applies to none of the selected tasks.")
    return errors


def build_config(config: ConfigInput, previous: list[Configuration]) -> SessionConfig:
    known = {(c.implementation, c.representation): c for c in previous}
    next_number = max((int(c.id[1:]) for c in previous), default=0) + 1
    next_slot = max((c.color_slot for c in previous), default=0) + 1
    candidates, exploratory = [], []
    for item in config.candidates:
        existing = known.get((item.implementation, item.representation))
        if existing is None:
            existing = Configuration(
                id=f"c{next_number}",
                implementation=item.implementation,
                representation=item.representation,
                color_slot=next_slot,
            )
            next_number += 1
            next_slot += 1
        candidates.append(existing)
        if item.exploratory:
            exploratory.append(existing.id)
    return SessionConfig(
        tasks=config.tasks,
        datasets=config.datasets,
        candidates=candidates,
        exploratory=exploratory,
        metrics=config.metrics,
        repetitions=config.repetitions,
        base_seed=config.base_seed,
    )


class SessionStore:
    def __init__(self, root: Path, registry: Registry):
        self.root = root
        self.registry = registry
        self.lock = threading.Lock()
        root.mkdir(parents=True, exist_ok=True)

    def folder(self, session_id: str) -> Path:
        path = self.root / session_id
        if not (path / "session.json").exists():
            raise KeyError(session_id)
        return path

    def job_dir(self, session_id: str, job_id: str) -> Path:
        return self.folder(session_id) / "jobs" / job_id

    def create(
        self, data: SessionCreate, app_commit: str | None, gaib_commit: str | None
    ) -> Session:
        errors = config_errors(data.config, self.registry)
        if errors:
            raise ValueError(" ".join(errors))
        session_id = f"s-{datetime.date.today():%Y%m%d}-{secrets.token_hex(3)}"
        meta = SessionMeta(
            id=session_id,
            name=data.name,
            question=data.question,
            created_at=now(),
            app_commit=app_commit,
            gaib_commit=gaib_commit,
        )
        session = Session(meta=meta, config=build_config(data.config, []))
        path = self.root / session_id
        for sub in ("jobs", "environment"):
            (path / sub).mkdir(parents=True)
        (path / "session.json").write_text(meta.model_dump_json(indent=2))
        (path / "config.json").write_text(session.config.model_dump_json(indent=2))
        for name in ("log.jsonl", "jobs.jsonl"):
            (path / name).touch()
        return session

    def get(self, session_id: str) -> Session:
        path = self.folder(session_id)
        return Session(
            meta=SessionMeta.model_validate_json((path / "session.json").read_text()),
            config=SessionConfig.model_validate_json((path / "config.json").read_text()),
        )

    def metas(self) -> list[SessionMeta]:
        return [
            SessionMeta.model_validate_json(p.read_text())
            for p in sorted(self.root.glob("*/session.json"))
        ]

    def update_config(self, session_id: str, data: ConfigInput) -> SessionConfig:
        errors = config_errors(data, self.registry)
        if errors:
            raise ValueError(" ".join(errors))
        with self.lock:
            path = self.folder(session_id)
            current = SessionConfig.model_validate_json((path / "config.json").read_text())
            history = [
                Configuration.model_validate(c)
                for record in read_jsonl(path / "config_history.jsonl")
                for c in record["config"]["candidates"]
            ]
            updated = build_config(data, current.candidates + history)
            append_jsonl(
                path / "config_history.jsonl",
                {"replaced_at": now(), "config": current.model_dump()},
            )
            (path / "config.json").write_text(updated.model_dump_json(indent=2))
            return updated

    def job_statuses(self, session_id: str) -> dict[str, str]:
        return {
            r["job_id"]: r["status"] for r in read_jsonl(self.folder(session_id) / "jobs.jsonl")
        }

    def append_job_record(self, session_id: str, record: dict) -> None:
        append_jsonl(self.folder(session_id) / "jobs.jsonl", record)

    def state_manifest(self, session_id: str) -> list[tuple[str, str]]:
        path = self.folder(session_id)
        open_jobs = {
            j for j, status in self.job_statuses(session_id).items() if status not in TERMINAL
        }
        files = sorted(
            {
                f
                for pattern in STATE_PATTERNS
                for f in path.glob(pattern)
                if f.is_file() and not (f.parts[-3] == "jobs" and f.parts[-2] in open_jobs)
            }
        )
        return [(str(f.relative_to(path)), sha256(f)) for f in files]

    def log(self, session_id: str) -> list[LogEntry]:
        return [
            LogEntry.model_validate(r) for r in read_jsonl(self.folder(session_id) / "log.jsonl")
        ]

    def append_log(self, session_id: str, data: LogEntryInput) -> LogEntry:
        with self.lock:
            entries = self.log(session_id)
            known = {e.id for e in entries}
            missing = [ref for ref in data.refers_to if ref not in known]
            if missing:
                raise ValueError(f"Unknown log entries: {', '.join(missing)}.")
            manifest = self.state_manifest(session_id)
            seq = len(entries) + 1
            fields = data.model_dump() | {
                "seq": seq,
                "id": f"e{seq}",
                "recorded_at": now(),
                "state_manifest": manifest,
                "state_hash": digest(manifest),
                "prev_entry_hash": entries[-1].entry_hash if entries else None,
            }
            entry = LogEntry(**fields, entry_hash=digest(fields))
            append_jsonl(self.folder(session_id) / "log.jsonl", entry.model_dump())
            return entry

    def verify_log(self, session_id: str) -> LogCheck:
        problems = []
        previous = None
        current = dict(self.state_manifest(session_id))
        entries = self.log(session_id)
        for entry in entries:
            fields = entry.model_dump(exclude={"entry_hash"})
            if digest(fields) != entry.entry_hash:
                problems.append(f"{entry.id}: entry hash does not match its contents.")
            if entry.prev_entry_hash != previous:
                problems.append(f"{entry.id}: chain broken before this entry.")
            if digest(fields["state_manifest"]) != entry.state_hash:
                problems.append(f"{entry.id}: state hash does not match its manifest.")
            for f, file_hash in entry.state_manifest:
                if f.startswith(("jobs/", "environment/")) and current.get(f) != file_hash:
                    problems.append(f"{entry.id}: {f} changed or is missing.")
            previous = entry.entry_hash
        return LogCheck(entries=len(entries), problems=problems)
