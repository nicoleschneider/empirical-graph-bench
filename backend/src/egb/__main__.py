import argparse
import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import uvicorn

from egb.api import create_app
from egb.datasets import DatasetStore
from egb.machine import git_state
from egb.registry import load_registry
from egb.remote import RemoteRunner
from egb.settings import BUILT_IN_ANALYSIS, BUILT_IN_ENTRIES, Settings
from egb.targets import load_targets
from egb.transport import make_transport

BENCH_PYTHON_VERSION = "3.11.9"


def settings_from(flags: argparse.Namespace) -> Settings:
    gaib_root = flags.gaib_root.resolve()
    data_dir = flags.data_dir.resolve()
    bench_python = flags.bench_python or data_dir / "bench-env" / "bin" / "python"
    return Settings(
        gaib_root=gaib_root,
        data_dir=data_dir,
        sessions_dir=flags.sessions_dir.resolve(),
        bench_python=bench_python.absolute(),
        lock_file=(gaib_root / flags.lock_file).resolve(),
        entries=(BUILT_IN_ENTRIES, *[p.resolve() for p in flags.extra_entries]),
        poll_seconds=flags.poll_seconds,
        analysis_config=flags.analysis_config.resolve(),
        targets_file=(flags.targets or data_dir / "targets.yaml").resolve(),
    )


def setup_bench(settings: Settings) -> None:
    if shutil.which("uv") is None:
        raise SystemExit("uv is required: https://docs.astral.sh/uv/")
    environment = settings.bench_python.parent.parent
    subprocess.run(["uv", "venv", "--python", BENCH_PYTHON_VERSION, str(environment)], check=True)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(settings.bench_python),
            "-r",
            str(settings.lock_file),
        ],
        check=True,
    )


async def remote_command(settings: Settings, action: str, name: str) -> None:
    registry = load_registry(list(settings.entries))
    targets = {
        t.name: t for t in load_targets(settings.targets_file, set(registry.implementations))
    }
    if name not in targets:
        raise SystemExit(f"No target {name} in {settings.targets_file}.")
    target = targets[name]
    runner = RemoteRunner(
        target,
        make_transport(target, settings.data_dir / "ssh"),
        settings,
        registry,
        DatasetStore(settings.datasets_dir),
        git_state(str(settings.gaib_root))["commit"],
        lambda: None,
    )
    if action == "setup":
        await runner.setup(print)
    hardware = await runner.probe()
    print(json.dumps(hardware.model_dump(exclude={"specs"}), indent=2))
    if hardware.specs:
        print(
            f"{hardware.specs.hostname}: {hardware.specs.cpu}, {hardware.specs.physical_cores} cores"
        )


def main() -> None:
    ap = argparse.ArgumentParser(prog="egb", allow_abbrev=False)
    ap.add_argument(
        "--gaib-root",
        type=Path,
        default=Path("vendor/HARD-graph-benchmark"),
        help="HARD-graph-benchmark checkout",
    )
    ap.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Datasets, PageRank references, downloads and the benchmark environment",
    )
    ap.add_argument(
        "--sessions-dir", type=Path, default=Path("sessions"), help="One folder per session"
    )
    ap.add_argument(
        "--bench-python",
        type=Path,
        default=None,
        help="Interpreter that runs gaib; defaults to the data directory's bench-env",
    )
    ap.add_argument(
        "--lock-file",
        type=Path,
        default=Path("requirements-lock-cpu.txt"),
        help="gaib lock file for the benchmark environment, relative to the gaib root",
    )
    ap.add_argument(
        "--extra-entries", type=Path, nargs="*", default=[], help="Extra registry directories"
    )
    ap.add_argument(
        "--poll-seconds", type=float, default=1.0, help="How often a running job's results are read"
    )
    ap.add_argument(
        "--analysis-config",
        type=Path,
        default=BUILT_IN_ANALYSIS,
        help="Thresholds and bootstrap settings for the analysis",
    )
    ap.add_argument(
        "--targets",
        type=Path,
        default=None,
        help="Remote targets file; defaults to targets.yaml in the data directory",
    )
    commands = ap.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="Run the API server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    commands.add_parser(
        "setup-bench", help="Build the benchmark environment from the gaib lock file"
    )
    remote = commands.add_parser("remote", help="Prepare or check a remote target")
    remote.add_argument("action", choices=["setup", "check"])
    remote.add_argument("target")
    flags = ap.parse_args()

    settings = settings_from(flags)
    if flags.command == "setup-bench":
        setup_bench(settings)
    elif flags.command == "remote":
        asyncio.run(remote_command(settings, flags.action, flags.target))
    else:
        uvicorn.run(create_app(settings), host=flags.host, port=flags.port)


if __name__ == "__main__":
    main()
