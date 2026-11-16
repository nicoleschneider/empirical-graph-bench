import asyncio
import hashlib
import json
import logging
import os
import shutil
import signal
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from egb import worker
from egb.datasets import DatasetStore, sha256
from egb.hardware import Hardware
from egb.jobs import Invocation, JobSpec
from egb.machine import git_state
from egb.registry import Registry
from egb.sessions import canonical, now
from egb.settings import Settings

PROBE = Path(__file__).with_name("probe.py")
STATUS_EVENTS = {
    "queued",
    "started",
    "waiting",
    "running",
    "finished",
    "failed",
    "cancelled",
    "lost",
}
DONE = {"finished", "failed", "cancelled", "lost"}
TAIL_LINES = 20

logger = logging.getLogger(__name__)


class JobEvent(BaseModel):
    seq: int
    type: str
    at: str
    data: dict


class JobHandle:
    def __init__(
        self,
        spec: JobSpec,
        job_dir: Path,
        environment_dir: Path | None,
        record: Callable[[dict], None],
        resumed: bool = False,
    ):
        self.spec = spec
        self.job_dir = job_dir
        self.environment_dir = environment_dir
        self.record = record
        self.resumed = resumed
        self.status = "new"
        self.events: list[JobEvent] = []
        self.changed = asyncio.Condition()
        self.process: asyncio.subprocess.Process | None = None
        self.cancel_requested = False

    @property
    def done(self) -> bool:
        return self.status in DONE

    async def emit(self, event_type: str, **data) -> None:
        event = JobEvent(seq=len(self.events), type=event_type, at=now(), data=data)
        if event_type in STATUS_EVENTS:
            self.status = event_type
            self.record({"job_id": self.spec.id, "status": event_type, "at": event.at, **data})
        async with self.changed:
            self.events.append(event)
            self.changed.notify_all()


class Runner(Protocol):
    id: str

    async def run(self, handle: JobHandle) -> None: ...

    async def cancel(self, handle: JobHandle) -> None: ...


def log_tail(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(errors="replace").splitlines()[-TAIL_LINES:]


def store_environment(handle: JobHandle, snapshot: dict) -> str:
    digest = hashlib.sha256(canonical(snapshot)).hexdigest()
    text = json.dumps(snapshot, indent=2)
    (handle.job_dir / "environment.json").write_text(text)
    if handle.environment_dir is not None:
        target = handle.environment_dir / f"{digest}.json"
        if not target.exists():
            target.write_text(text)
    return digest


async def report_rows(handle: JobHandle, rows: list[dict], seen: int) -> int:
    for index, row in enumerate(rows[seen:], start=seen):
        configuration = handle.spec.configuration_of(row["task"], row["dataset"])
        if configuration is None:
            logger.warning("Row %d matches no configuration in job %s", index, handle.spec.id)
        await handle.emit("run_completed", configuration=configuration, row=row)
    return len(rows)


def index_outputs(handle: JobHandle, kept: list[dict]) -> None:
    items = {(i.gaib_task, i.results_dataset): i for i in handle.spec.items}
    folder = handle.job_dir / "outputs"
    index_path = folder / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    for entry in kept:
        item = items[(entry["gaib_task"], entry["dataset"])]
        index.append(
            {
                "config": {
                    "hardware": entry["hardware"],
                    "implementation": item.implementation,
                    "representation": item.representation,
                },
                "dataset": item.dataset,
                "seed": entry["seed"],
                "path": str(folder / entry["file"]),
            }
        )
    index_path.write_text(json.dumps(index, indent=2))


async def acquire_machine_lock(handle: JobHandle):
    lock = worker.open_lock()
    if not worker.try_lock(lock):
        await handle.emit("waiting")
        await asyncio.to_thread(worker.lock, lock)
    return lock


class LocalRunner:
    id = "local"

    def __init__(
        self,
        settings: Settings,
        hardware: Callable[[], Hardware],
        datasets: DatasetStore,
        registry: Registry,
    ):
        self.settings = settings
        self.hardware = hardware
        self.datasets = datasets
        self.registry = registry

    async def environment(self) -> dict:
        process = await asyncio.create_subprocess_exec(
            str(self.settings.bench_python),
            str(PROBE),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"Environment probe failed: {err.decode().strip()}")
        hardware = self.hardware()
        return {
            "hardware_label": hardware.label,
            "runner": "local",
            "hostname": os.uname().nodename,
            "specs": hardware.specs.model_dump(),
            "capabilities": hardware.capabilities,
            "benchmark_environment": json.loads(out),
            "lock_file": {
                "path": str(self.settings.lock_file),
                "sha256": sha256(self.settings.lock_file),
            },
            "gaib": git_state(str(self.settings.gaib_root)),
            "app": git_state(str(Path(__file__).parent)),
        }

    def dataset_problems(self, spec: JobSpec) -> list[str]:
        problems = []
        for dataset_id in spec.datasets:
            state = self.datasets.status(self.registry.datasets[dataset_id], compute=True).state
            if state != "verified":
                problems.append(f"{dataset_id}: {state}.")
        return problems

    async def run(self, handle: JobHandle) -> None:
        spec = handle.spec
        await handle.emit("started")
        if spec.kind != "download":
            problems = self.dataset_problems(spec)
            if problems:
                await handle.emit("failed", reason=" ".join(problems))
                return
        environment = store_environment(handle, await self.environment())
        await handle.emit("environment", sha256=environment)
        lock = await acquire_machine_lock(handle)
        try:
            await handle.emit("running")
            for invocation in spec.invocations:
                code = await self.invoke(handle, invocation)
                if handle.cancel_requested:
                    await handle.emit("cancelled")
                    return
                if code != 0:
                    await handle.emit(
                        "failed",
                        reason=f"{invocation.driver} exited with code {code}.",
                        tail=log_tail(handle.job_dir / "stdout.log"),
                    )
                    return
        finally:
            worker.unlock(lock)
        shutil.rmtree(handle.job_dir / "work")
        await handle.emit("finished", environment=environment)

    async def invoke(self, handle: JobHandle, invocation: Invocation) -> int:
        Path(invocation.cwd).mkdir(parents=True, exist_ok=True)
        with (handle.job_dir / "command.jsonl").open("a") as f:
            f.write(invocation.model_dump_json() + "\n")
        results = Path(invocation.results_csv) if invocation.results_csv else None
        seen = len(worker.read_rows(results)) if results else 0
        rows_before = seen
        cache = Path(invocation.cwd) / "cache"
        cache_before = worker.cache_files(cache)
        with (handle.job_dir / "stdout.log").open("ab") as log:
            handle.process = await asyncio.create_subprocess_exec(
                *invocation.argv,
                cwd=invocation.cwd,
                env=os.environ | invocation.env,
                stdout=log,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            while handle.process.returncode is None:
                try:
                    await asyncio.wait_for(
                        handle.process.wait(), timeout=self.settings.poll_seconds
                    )
                except TimeoutError:
                    pass
                if results:
                    seen = await report_rows(handle, worker.read_rows(results), seen)
        if handle.process.returncode == 0 and invocation.driver == "gaib-runner":
            try:
                kept = worker.keep_first_outputs(
                    worker.read_rows(results)[rows_before:],
                    list(worker.cache_files(cache) - cache_before),
                    handle.job_dir / "outputs",
                )
                index_outputs(handle, kept)
            except ValueError as error:
                logger.warning("Job %s: %s; outputs not kept", handle.spec.id, error)
        return handle.process.returncode

    async def cancel(self, handle: JobHandle) -> None:
        handle.cancel_requested = True
        if handle.process is not None and handle.process.returncode is None:
            os.killpg(handle.process.pid, signal.SIGTERM)


class JobQueue:
    def __init__(self, runner: Runner):
        self.runner = runner
        self.pending: asyncio.Queue[JobHandle] = asyncio.Queue()
        self.handles: dict[str, JobHandle] = {}
        self.current: JobHandle | None = None

    @property
    def busy(self) -> bool:
        return self.current is not None

    def queued(self) -> list[str]:
        return [h.spec.id for h in self.handles.values() if h.status == "queued"]

    async def submit(self, handle: JobHandle) -> None:
        self.handles[handle.spec.id] = handle
        if not handle.resumed:
            await handle.emit("queued")
        await self.pending.put(handle)

    async def work(self) -> None:
        while True:
            handle = await self.pending.get()
            if handle.cancel_requested and not handle.resumed:
                continue
            self.current = handle
            try:
                await self.runner.run(handle)
            except Exception as error:
                logger.exception("Job %s failed", handle.spec.id)
                await handle.emit("failed", reason=str(error))
            finally:
                self.current = None

    async def cancel(self, job_id: str) -> None:
        handle = self.handles[job_id]
        if handle.status == "queued":
            handle.cancel_requested = True
            await handle.emit("cancelled")
        elif not handle.done:
            await self.runner.cancel(handle)
