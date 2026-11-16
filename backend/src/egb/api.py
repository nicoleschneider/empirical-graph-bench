import asyncio
import contextlib
import fcntl
import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from egb.datasets import DatasetStatus, DatasetStore
from egb.hardware import Hardware, detect_local, store_label
from egb.jobs import (
    ConfigurationStatus,
    JobRequest,
    JobSpec,
    configuration_statuses,
    new_job_id,
    resolve_download,
    resolve_session_job,
)
from egb.registry import KINDS, load_registry
from egb.results import SummaryRow, job_specs, session_rows, summarise
from egb.runners import JobEvent, JobHandle, JobQueue, LocalRunner, git_state
from egb.sessions import (
    TERMINAL,
    ConfigInput,
    LogCheck,
    LogEntry,
    LogEntryInput,
    Session,
    SessionConfig,
    SessionCreate,
    SessionMeta,
    SessionStore,
    append_jsonl,
    now,
    read_jsonl,
)
from egb.settings import Settings


class LabelUpdate(BaseModel):
    label: str


class JobView(BaseModel):
    spec: JobSpec
    status: str | None
    events: list[JobEvent]


class QueueView(BaseModel):
    running: str | None
    queued: list[str]


class Core:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.registry = load_registry(list(settings.entries))
        self.hardware = detect_local(settings.bench_python, settings.hardware_file)
        self.datasets = DatasetStore(settings.datasets_dir)
        self.sessions = SessionStore(settings.sessions_dir, self.registry)
        self.runner = LocalRunner(settings, lambda: self.hardware, self.datasets, self.registry)
        self.queue = JobQueue(self.runner)
        self.gaib_commit = git_state(settings.gaib_root)["commit"]
        self.app_commit = git_state(Path(__file__).parent)["commit"]
        settings.downloads_dir.mkdir(parents=True, exist_ok=True)

    @property
    def downloads_log(self) -> Path:
        return self.settings.downloads_dir / "jobs.jsonl"

    def mark_interrupted(self) -> None:
        for meta in self.sessions.metas():
            for job_id, status in self.sessions.job_statuses(meta.id).items():
                if status not in TERMINAL:
                    self.sessions.append_job_record(
                        meta.id, {"job_id": job_id, "status": "interrupted", "at": now()}
                    )
        for job_id, status in self.download_statuses().items():
            if status not in TERMINAL:
                append_jsonl(
                    self.downloads_log, {"job_id": job_id, "status": "interrupted", "at": now()}
                )

    def download_statuses(self) -> dict[str, str]:
        return {r["job_id"]: r["status"] for r in read_jsonl(self.downloads_log)}

    def job_location(self, job_id: str) -> tuple[Path, str | None]:
        for path in self.settings.sessions_dir.glob(f"*/jobs/{job_id}"):
            return path, path.parent.parent.name
        path = self.settings.downloads_dir / job_id
        if path.exists():
            return path, None
        raise HTTPException(404, f"No job {job_id}.")

    def stored_status(self, job_id: str, session_id: str | None) -> str | None:
        statuses = (
            self.download_statuses()
            if session_id is None
            else self.sessions.job_statuses(session_id)
        )
        return statuses.get(job_id)

    def session(self, session_id: str) -> Session:
        try:
            return self.sessions.get(session_id)
        except KeyError:
            raise HTTPException(404, f"No session {session_id}.")


def bad_request(error: ValueError) -> HTTPException:
    return HTTPException(400, str(error))


@contextlib.contextmanager
def exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"Another backend is running jobs from {path.parent}.")
        yield


def create_app(settings: Settings) -> FastAPI:
    core = Core(settings)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        with exclusive_lock(settings.data_dir / ".egb.lock"):
            core.mark_interrupted()
            worker = asyncio.create_task(core.queue.work())
            yield
            if core.queue.current is not None:
                await core.runner.cancel(core.queue.current)
            worker.cancel()

    app = FastAPI(title="empirical-graph-bench", lifespan=lifespan)
    app.state.core = core

    @app.get("/api/registry")
    def registry() -> dict:
        return core.registry.model_dump()

    @app.get("/api/registry/{kind}")
    def registry_kind(kind: str) -> dict:
        if kind not in KINDS:
            raise HTTPException(404, f"No registry kind {kind}.")
        return getattr(core.registry, kind)

    @app.get("/api/registry/{kind}/{entry_id}")
    def registry_entry(kind: str, entry_id: str) -> dict:
        entries = registry_kind(kind)
        if entry_id not in entries:
            raise HTTPException(404, f"No {kind} entry {entry_id}.")
        return entries[entry_id].model_dump()

    @app.get("/api/hardware")
    def hardware() -> list[Hardware]:
        return [core.hardware]

    @app.patch("/api/hardware/local")
    def rename_hardware(update: LabelUpdate) -> Hardware:
        try:
            store_label(settings.hardware_file, update.label)
        except ValueError as error:
            raise bad_request(error)
        core.hardware = core.hardware.model_copy(update={"label": update.label})
        return core.hardware

    @app.get("/api/configurations")
    def configurations() -> list[ConfigurationStatus]:
        return configuration_statuses(core.registry, core.hardware)

    @app.get("/api/datasets")
    def datasets() -> list[DatasetStatus]:
        return [
            core.datasets.status(d, compute=not core.queue.busy)
            for d in core.registry.datasets.values()
        ]

    @app.post("/api/datasets/{dataset_id}/download", status_code=202)
    async def download(dataset_id: str) -> JobSpec:
        job_id = new_job_id()
        job_dir = settings.downloads_dir / job_id
        try:
            spec = resolve_download(
                [dataset_id], core.registry, core.hardware, settings, job_id, job_dir
            )
        except ValueError as error:
            raise bad_request(error)
        (job_dir / "spec.json").write_text(spec.model_dump_json(indent=2))
        handle = JobHandle(
            spec, job_dir, None, lambda record: append_jsonl(core.downloads_log, record)
        )
        await core.queue.submit(handle)
        return spec

    @app.get("/api/queue")
    def queue() -> QueueView:
        running = core.queue.current.spec.id if core.queue.current else None
        return QueueView(running=running, queued=core.queue.queued())

    @app.get("/api/sessions")
    def sessions() -> list[SessionMeta]:
        return core.sessions.metas()

    @app.post("/api/sessions", status_code=201)
    def create_session(data: SessionCreate) -> Session:
        try:
            return core.sessions.create(data, core.app_commit, core.gaib_commit)
        except ValueError as error:
            raise bad_request(error)

    @app.get("/api/sessions/{session_id}")
    def session(session_id: str) -> Session:
        return core.session(session_id)

    @app.patch("/api/sessions/{session_id}/config")
    def update_config(session_id: str, data: ConfigInput) -> SessionConfig:
        core.session(session_id)
        try:
            return core.sessions.update_config(session_id, data)
        except ValueError as error:
            raise bad_request(error)

    @app.get("/api/sessions/{session_id}/log")
    def log(session_id: str) -> list[LogEntry]:
        core.session(session_id)
        return core.sessions.log(session_id)

    @app.post("/api/sessions/{session_id}/log", status_code=201)
    def append_log(session_id: str, data: LogEntryInput) -> LogEntry:
        core.session(session_id)
        try:
            return core.sessions.append_log(session_id, data)
        except ValueError as error:
            raise bad_request(error)

    @app.get("/api/sessions/{session_id}/log/verify")
    def verify_log(session_id: str) -> LogCheck:
        core.session(session_id)
        return core.sessions.verify_log(session_id)

    @app.post("/api/sessions/{session_id}/jobs", status_code=202)
    async def start_job(session_id: str, request: JobRequest) -> JobSpec:
        current = core.session(session_id)
        job_id = new_job_id()
        job_dir = core.sessions.job_dir(session_id, job_id)
        try:
            spec = resolve_session_job(
                request,
                current,
                core.sessions.log(session_id),
                core.registry,
                core.hardware,
                core.datasets,
                settings,
                job_id,
                job_dir,
            )
        except ValueError as error:
            raise bad_request(error)
        (job_dir / "spec.json").write_text(spec.model_dump_json(indent=2))
        handle = JobHandle(
            spec,
            job_dir,
            core.sessions.folder(session_id) / "environment",
            lambda record: core.sessions.append_job_record(session_id, record),
        )
        await core.queue.submit(handle)
        return spec

    @app.get("/api/sessions/{session_id}/jobs")
    def session_jobs(session_id: str) -> list[JobView]:
        core.session(session_id)
        return [job(spec.id) for spec in job_specs(core.sessions.folder(session_id))]

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> JobView:
        job_dir, session_id = core.job_location(job_id)
        spec = JobSpec.model_validate_json((job_dir / "spec.json").read_text())
        handle = core.queue.handles.get(job_id)
        if handle is not None:
            return JobView(spec=spec, status=handle.status, events=handle.events)
        return JobView(spec=spec, status=core.stored_status(job_id, session_id), events=[])

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        handle = core.queue.handles.get(job_id)
        if handle is None:
            view = job(job_id)
            stored = JobEvent(seq=0, type=view.status or "unknown", at=now(), data={})
            return StreamingResponse(
                iter([f"event: {stored.type}\ndata: {stored.model_dump_json()}\n\n"]),
                media_type="text/event-stream",
            )

        async def stream() -> AsyncIterator[str]:
            index = 0
            while True:
                async with handle.changed:
                    await handle.changed.wait_for(lambda seen=index: len(handle.events) > seen)
                for event in handle.events[index:]:
                    yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
                index = len(handle.events)
                if handle.done:
                    return

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.delete("/api/jobs/{job_id}", status_code=202)
    async def cancel_job(job_id: str) -> JobView:
        if job_id not in core.queue.handles or core.queue.handles[job_id].done:
            raise HTTPException(409, f"{job_id} is not queued or running.")
        await core.queue.cancel(job_id)
        return job(job_id)

    @app.get("/api/sessions/{session_id}/results")
    def results(session_id: str) -> list[dict]:
        core.session(session_id)
        return session_rows(
            core.sessions.folder(session_id), core.sessions.job_statuses(session_id)
        )

    @app.get("/api/sessions/{session_id}/summary")
    def summary(session_id: str) -> list[SummaryRow]:
        current = core.session(session_id)
        rows = session_rows(
            core.sessions.folder(session_id), core.sessions.job_statuses(session_id)
        )
        return summarise(rows, current.config, core.registry)

    @app.get("/api/sessions/{session_id}/environment")
    def environment(session_id: str) -> dict[str, dict]:
        core.session(session_id)
        folder = core.sessions.folder(session_id) / "environment"
        return {p.stem: json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))}

    return app
