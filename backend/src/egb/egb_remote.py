import argparse
import datetime
import hashlib
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import machine
import worker

HERE = Path(__file__).resolve().parent
TERMINAL = {"finished", "failed", "cancelled"}


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_status(job: Path) -> dict:
    path = job / "status.json"
    return json.loads(path.read_text()) if path.exists() else {"state": "missing"}


def write_status(job: Path, **changes) -> dict:
    status = read_status(job) | changes | {"updated": now()}
    (job / "status.json.part").write_text(json.dumps(status, indent=2))
    os.replace(job / "status.json.part", job / "status.json")
    return status


def probe(flags) -> dict:
    specs = machine.detect_specs()
    capabilities, unavailable = machine.detect_capabilities(
        specs, flags.bench_python, f"Run egb remote setup {flags.target}."
    )
    environment = None
    if Path(flags.bench_python).exists():
        completed = subprocess.run(
            [flags.bench_python, str(HERE / "probe.py")],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            environment = json.loads(completed.stdout)
    lock = Path(flags.lock_file)
    return {
        "specs": specs,
        "capabilities": capabilities,
        "unavailable": unavailable,
        "benchmark_environment": environment,
        "gaib": machine.git_state(flags.gaib) if Path(flags.gaib).exists() else None,
        "lock_file": {"path": str(lock), "sha256": sha256(lock)} if lock.exists() else None,
        "worker_sha256": sha256(Path(__file__)),
    }


def verify(entries: list[dict]) -> dict:
    states = {}
    for entry in entries:
        files = [Path(entry["raw"]), Path(entry["file"])]
        if not all(f.exists() for f in files):
            state = "missing"
        else:
            state = "verified" if sha256(files[0]) == entry["sha256"] else "mismatch"
        if states.get(entry["id"]) in (None, "verified"):
            states[entry["id"]] = state
    return states


def start(job: Path) -> dict:
    write_status(job, state="submitted", submitted_at=now(), hostname=os.uname().nodename)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "run", str(job)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=(job / "worker.log").open("ab"),
        start_new_session=True,
    )
    return write_status(job, pgid=process.pid)


def run(job: Path) -> None:
    cancelled = {"value": False}

    def on_term(signum, frame):
        cancelled["value"] = True

    signal.signal(signal.SIGTERM, on_term)
    write_status(job, state="waiting")
    handle = worker.open_lock()
    worker.lock(handle)
    exit_codes = []
    try:
        write_status(job, state="running", lock_acquired_at=now())
        kept = []
        for invocation in json.loads((job / "invocations.json").read_text()):
            if cancelled["value"]:
                break
            Path(invocation["cwd"]).mkdir(parents=True, exist_ok=True)
            results = Path(invocation["results_csv"]) if invocation["results_csv"] else None
            rows_before = len(worker.read_rows(results)) if results else 0
            cache = Path(invocation["cwd"]) / "cache"
            cache_before = worker.cache_files(cache)
            with (job / "stdout.log").open("ab") as log:
                child = subprocess.Popen(
                    invocation["argv"],
                    cwd=invocation["cwd"],
                    env=os.environ | invocation["env"],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                code = child.wait()
            exit_codes.append(code)
            if code != 0 or cancelled["value"]:
                break
            if invocation["driver"] == "gaib-runner":
                try:
                    kept += worker.keep_first_outputs(
                        worker.read_rows(results)[rows_before:],
                        list(worker.cache_files(cache) - cache_before),
                        job / "outputs",
                    )
                except ValueError as error:
                    write_status(job, outputs_warning=str(error))
        (job / "outputs").mkdir(exist_ok=True)
        (job / "outputs" / "raw_index.json").write_text(json.dumps(kept, indent=2))
    finally:
        worker.unlock(handle)
    if cancelled["value"]:
        write_status(job, state="cancelled", exit_codes=exit_codes, finished_at=now())
    elif exit_codes and exit_codes[-1] != 0:
        write_status(
            job,
            state="failed",
            exit_codes=exit_codes,
            finished_at=now(),
            reason=f"exited with code {exit_codes[-1]}",
        )
    else:
        write_status(job, state="finished", exit_codes=exit_codes, finished_at=now())


def status(job: Path, offset: int) -> dict:
    results = job / "results.csv"
    return {"status": read_status(job), "rows": worker.read_rows(results)[offset:]}


def cancel(job: Path) -> dict:
    current = read_status(job)
    if current.get("state") in TERMINAL or "pgid" not in current:
        return current
    try:
        os.killpg(current["pgid"], signal.SIGTERM)
    except ProcessLookupError:
        return write_status(
            job, state="cancelled", reason="process already gone", finished_at=now()
        )
    return current


def main() -> None:
    ap = argparse.ArgumentParser(allow_abbrev=False)
    commands = ap.add_subparsers(dest="command", required=True)
    p = commands.add_parser("probe")
    p.add_argument("--target", required=True)
    p.add_argument("--bench-python", required=True)
    p.add_argument("--gaib", required=True)
    p.add_argument("--lock-file", required=True)
    commands.add_parser("verify")
    for name in ("start", "run", "cancel"):
        commands.add_parser(name).add_argument("job")
    s = commands.add_parser("status")
    s.add_argument("job")
    s.add_argument("offset", type=int)
    flags = ap.parse_args()
    if flags.command == "probe":
        print(json.dumps(probe(flags)))
    elif flags.command == "verify":
        print(json.dumps(verify(json.load(sys.stdin))))
    elif flags.command == "start":
        print(json.dumps(start(Path(flags.job))))
    elif flags.command == "run":
        run(Path(flags.job))
    elif flags.command == "status":
        print(json.dumps(status(Path(flags.job), flags.offset)))
    else:
        print(json.dumps(cancel(Path(flags.job))))


if __name__ == "__main__":
    main()
