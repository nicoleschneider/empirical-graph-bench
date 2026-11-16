import asyncio
import json
import shlex
import tempfile
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from egb.datasets import DatasetStore
from egb.hardware import Hardware, Specs
from egb.jobs import JobSpec, RunPaths
from egb.machine import git_state
from egb.registry import Registry
from egb.runners import JobHandle, index_outputs, log_tail, report_rows, store_environment
from egb.settings import Settings
from egb.targets import Target
from egb.transport import Transport, TransportError, checked, fetch, send

WORKER_FILES = ["egb_remote.py", "machine.py", "worker.py", "probe.py"]
COLLECTED = [
    "results.csv",
    "cross_path_l1.csv",
    "cross_path_louvain.csv",
    "reports",
    "outputs",
    "stdout.log",
    "status.json",
    "worker.log",
]
PACKAGE = Path(__file__).parent
REMOTE_DONE = {"finished", "failed", "cancelled"}
BACKOFF_LIMIT_SECONDS = 60
COMMAND_TIMEOUT_SECONDS = 120
TRANSFER_TIMEOUT_SECONDS = 3600


class RemoteRunner:
    id = "remote"

    def __init__(
        self,
        target: Target,
        transport: Transport,
        settings: Settings,
        registry: Registry,
        local_datasets: DatasetStore,
        gaib_commit: str,
        hardware: Callable[[], Hardware],
    ):
        self.target = target
        self.transport = transport
        self.settings = settings
        self.registry = registry
        self.local_datasets = local_datasets
        self.gaib_commit = gaib_commit
        self.hardware = hardware
        root = PurePosixPath(target.workdir)
        self.worker_dir = root / "egb"
        self.bench_python = root / "bench-env" / "bin" / "python"
        self.gaib_root = root / "gaib" / gaib_commit
        self.datasets_dir = root / "datasets"
        self.jobs_dir = root / "jobs"
        self.timed_out: set[str] = set()

    def paths(self, job_id: str) -> RunPaths:
        return RunPaths(
            bench_python=self.bench_python,
            gaib_dir=self.gaib_root / "gaib",
            datasets=self.datasets_dir,
            manifest=self.datasets_dir / "manifest.json",
            pagerank_references=PurePosixPath(self.target.workdir) / "pagerank_references",
            job_dir=self.jobs_dir / job_id,
        )

    def worker(self, *args: str) -> str:
        quoted = " ".join(shlex.quote(a) for a in args)
        return f"{shlex.quote(self.target.python)} {self.worker_dir / 'egb_remote.py'} {quoted}"

    async def install_worker(self) -> None:
        await send(
            self.transport, PACKAGE, WORKER_FILES, str(self.worker_dir), COMMAND_TIMEOUT_SECONDS
        )

    async def probe_raw(self) -> dict:
        await self.install_worker()
        out = await checked(
            self.transport,
            self.worker(
                "probe",
                "--target",
                self.target.name,
                "--bench-python",
                str(self.bench_python),
                "--gaib",
                str(self.gaib_root),
                "--lock-file",
                str(self.gaib_root / self.target.lock_file),
            ),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return json.loads(out)

    def unreachable(self, reason: str) -> Hardware:
        return Hardware(
            id=self.target.name,
            label=self.target.name,
            runner=self.target.transport,
            host=self.target.host,
            status="unavailable",
            reason=reason,
            specs=None,
            capabilities=[],
            unavailable={},
            supports=self.target.supports,
        )

    async def probe(self) -> Hardware:
        try:
            raw = await self.probe_raw()
        except TransportError as error:
            return self.unreachable(str(error))
        reason = None
        if raw["gaib"] is None:
            reason = f"Code not installed. Run egb remote setup {self.target.name}."
        elif not raw["capabilities"]:
            reason = raw["unavailable"]["cpu"]
        return Hardware(
            id=self.target.name,
            label=self.target.name,
            runner=self.target.transport,
            host=self.target.host,
            status="unavailable" if reason else "reachable",
            reason=reason,
            specs=Specs.model_validate(raw["specs"]),
            capabilities=raw["capabilities"],
            unavailable=raw["unavailable"],
            supports=self.target.supports,
        )

    async def setup(self, report: Callable[[str], None]) -> None:
        report("Installing the worker.")
        await self.install_worker()
        report(f"Sending gaib {self.gaib_commit[:7]}.")
        with tempfile.TemporaryDirectory() as scratch:
            bundle = Path(scratch) / "gaib.bundle"
            process = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.settings.gaib_root),
                "bundle",
                "create",
                str(bundle),
                "HEAD",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, err = await process.communicate()
            if process.returncode != 0:
                raise TransportError(f"git bundle failed: {err.decode().strip()}")
            await send(
                self.transport,
                Path(scratch),
                ["gaib.bundle"],
                str(self.worker_dir),
                TRANSFER_TIMEOUT_SECONDS,
            )
        root = shlex.quote(str(self.gaib_root))
        await checked(
            self.transport,
            f"if [ ! -d {root}/.git ]; then git clone --quiet {self.worker_dir / 'gaib.bundle'} {root} && "
            f"git -C {root} checkout --quiet {self.gaib_commit}; fi",
            timeout=TRANSFER_TIMEOUT_SECONDS,
        )
        report("Building the benchmark environment.")
        env = PurePosixPath(self.target.workdir) / "bench-env"
        await checked(
            self.transport,
            f"if [ ! -x {env}/bin/python ]; then {shlex.quote(self.target.python)} -m venv {env}; fi && "
            f"{env}/bin/python -m pip install --quiet -r {self.gaib_root / self.target.lock_file}",
            timeout=TRANSFER_TIMEOUT_SECONDS,
        )
        report("Done.")

    def dataset_entries(self, spec: JobSpec) -> list[dict]:
        entries = []
        for dataset_id in spec.datasets:
            dataset = self.registry.datasets[dataset_id]
            for entry in [dataset.graph, dataset.ground_truth]:
                if entry is not None:
                    folder = self.datasets_dir / dataset.directory
                    entries.append(
                        {
                            "id": dataset_id,
                            "raw": str(folder / entry.raw_file),
                            "file": str(folder / entry.file),
                            "sha256": entry.sha256,
                        }
                    )
        return entries

    async def verify(self, entries: list[dict]) -> dict[str, str]:
        with tempfile.NamedTemporaryFile("w", suffix=".json") as listing:
            json.dump(entries, listing)
            listing.flush()
            out = await checked(
                self.transport, self.worker("verify"), Path(listing.name), COMMAND_TIMEOUT_SECONDS
            )
        return json.loads(out)

    async def sync_datasets(self, spec: JobSpec, job_dir: Path) -> None:
        entries = self.dataset_entries(spec)
        states = await self.verify(entries)
        missing = [d for d, state in states.items() if state != "verified"]
        if not missing:
            return
        if self.target.datasets == "download":
            paths = self.paths(spec.id)
            listing = job_dir / "download.json"
            listing.write_text(
                json.dumps(
                    [
                        {
                            "name": d,
                            "directory": self.registry.datasets[d].directory,
                            "graph_url": self.registry.datasets[d].graph.url,
                            "ground_truth_url": None
                            if self.registry.datasets[d].ground_truth is None
                            else self.registry.datasets[d].ground_truth.url,
                            "directed": self.registry.datasets[d].directed,
                        }
                        for d in missing
                    ]
                )
            )
            await send(
                self.transport,
                job_dir,
                ["download.json"],
                str(paths.job_dir),
                COMMAND_TIMEOUT_SECONDS,
            )
            await checked(
                self.transport,
                " ".join(
                    shlex.quote(str(a))
                    for a in [
                        self.bench_python,
                        paths.gaib_dir / "download_datasets.py",
                        "--datasets-list",
                        paths.job_dir / "download.json",
                        "--datasets-directory",
                        self.datasets_dir,
                        "--manifest",
                        paths.manifest,
                    ]
                ),
                timeout=TRANSFER_TIMEOUT_SECONDS,
            )
            return
        members = ["manifest.json"]
        for dataset_id in missing:
            dataset = self.registry.datasets[dataset_id]
            for entry in [dataset.graph, dataset.ground_truth]:
                if entry is not None:
                    for name in (entry.raw_file, entry.file):
                        if not (self.settings.datasets_dir / dataset.directory / name).exists():
                            raise TransportError(f"{dataset_id} is not downloaded on this machine.")
                        members.append(f"{dataset.directory}/{name}")
        await send(
            self.transport,
            self.settings.datasets_dir,
            members,
            str(self.datasets_dir),
            TRANSFER_TIMEOUT_SECONDS,
        )

    def environment(self, raw: dict) -> dict:
        return {
            "hardware_label": self.target.name,
            "runner": self.target.transport,
            "target": self.target.name,
            "host": self.target.host,
            "hostname": raw["specs"]["hostname"],
            "specs": raw["specs"],
            "capabilities": raw["capabilities"],
            "benchmark_environment": raw["benchmark_environment"],
            "lock_file": raw["lock_file"],
            "gaib": raw["gaib"],
            "app": git_state(str(PACKAGE)),
            "worker_sha256": raw["worker_sha256"],
            "poll_seconds": self.target.poll_seconds,
        }

    async def prepare(self, handle: JobHandle) -> bool:
        spec = handle.spec
        await handle.emit("started", target=self.target.name)
        raw = await self.probe_raw()
        if raw["gaib"] is None or raw["benchmark_environment"] is None:
            await handle.emit("failed", reason=f"Run egb remote setup {self.target.name}.")
            return False
        await self.sync_datasets(spec, handle.job_dir)
        states = await self.verify(self.dataset_entries(spec))
        problems = [f"{d} hash {s}." for d, s in states.items() if s != "verified"]
        if problems:
            await handle.emit("failed", reason=" ".join(problems))
            return False
        (handle.job_dir / "invocations.json").write_text(
            json.dumps([i.model_dump() for i in spec.invocations], indent=2)
        )
        await send(
            self.transport,
            handle.job_dir,
            ["spec.json", "invocations.json", "definitions"],
            str(self.paths(spec.id).job_dir),
            COMMAND_TIMEOUT_SECONDS,
        )
        await handle.emit("environment", sha256=store_environment(handle, self.environment(raw)))
        await checked(
            self.transport,
            self.worker("start", str(self.paths(spec.id).job_dir)),
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        return True

    async def run(self, handle: JobHandle) -> None:
        try:
            if not handle.resumed and not await self.prepare(handle):
                return
            if handle.cancel_requested:
                await self.cancel(handle)
        except TransportError as error:
            await handle.emit("failed", reason=str(error))
            return
        await self.watch(handle)

    async def watch(self, handle: JobHandle) -> None:
        job = str(self.paths(handle.spec.id).job_dir)
        seen, state, started, failing_since = 0, None, time.monotonic(), None
        delay = self.target.poll_seconds
        while True:
            try:
                out = await checked(
                    self.transport,
                    self.worker("status", job, str(seen)),
                    timeout=COMMAND_TIMEOUT_SECONDS,
                )
                failing_since, delay = None, self.target.poll_seconds
            except TransportError as error:
                failing_since = failing_since or time.monotonic()
                if time.monotonic() - failing_since > self.target.lost_after_minutes * 60:
                    await handle.emit(
                        "lost", reason=f"{error} Check the job on {self.target.name}."
                    )
                    return
                await handle.emit("connection", reason=f"{error} Retrying.")
                await asyncio.sleep(delay)
                delay = min(delay * 2, BACKOFF_LIMIT_SECONDS)
                continue
            current = json.loads(out)
            if current["rows"]:
                seen += await report_rows(handle, current["rows"], 0)
            remote_state = current["status"].get("state")
            if remote_state != state and remote_state in ("waiting", "running"):
                await handle.emit(remote_state)
            state = remote_state
            if remote_state in REMOTE_DONE:
                await self.collect(handle, current["status"])
                return
            if (
                time.monotonic() - started > self.target.max_job_minutes * 60
                and not handle.cancel_requested
            ):
                self.timed_out.add(handle.spec.id)
                await self.cancel(handle)
            await asyncio.sleep(self.target.poll_seconds)

    async def collect(self, handle: JobHandle, status: dict) -> None:
        job = str(self.paths(handle.spec.id).job_dir)
        try:
            await fetch(self.transport, job, COLLECTED, handle.job_dir, TRANSFER_TIMEOUT_SECONDS)
        except TransportError as error:
            await handle.emit("lost", reason=f"{error} Results are still on {self.target.name}.")
            return
        raw_index = handle.job_dir / "outputs" / "raw_index.json"
        if raw_index.exists():
            index_outputs(handle, json.loads(raw_index.read_text()))
        await self.transport.run(f"rm -rf {shlex.quote(job)}/work", timeout=COMMAND_TIMEOUT_SECONDS)
        state = status["state"]
        if handle.spec.id in self.timed_out:
            await handle.emit(
                "failed",
                reason=f"Stopped after {self.target.max_job_minutes:g} minutes.",
                remote=status,
            )
        elif state == "finished":
            await handle.emit("finished", remote=status)
        elif state == "cancelled":
            await handle.emit("cancelled", remote=status)
        else:
            await handle.emit(
                "failed",
                reason=f"gaib {status.get('reason', 'failed')}.",
                tail=log_tail(handle.job_dir / "stdout.log"),
                remote=status,
            )

    async def cancel(self, handle: JobHandle) -> None:
        handle.cancel_requested = True
        try:
            await checked(
                self.transport,
                self.worker("cancel", str(self.paths(handle.spec.id).job_dir)),
                timeout=COMMAND_TIMEOUT_SECONDS,
            )
        except TransportError as error:
            await handle.emit("connection", reason=f"Cancel not delivered: {error}")
