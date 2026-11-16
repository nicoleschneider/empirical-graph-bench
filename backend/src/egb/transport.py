import asyncio
import io
import shlex
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from egb.targets import Target

SSH_FAILURES = [
    ("Host key verification failed", "Host key unknown. Connect once with ssh to accept it."),
    ("REMOTE HOST IDENTIFICATION HAS CHANGED", "Host key changed. Check known_hosts."),
    ("Permission denied", "SSH needs a key in your agent."),
    ("Could not resolve hostname", "Host name not found."),
    ("Connection refused", "Host unreachable."),
    ("timed out", "Host unreachable."),
    ("No route to host", "Host unreachable."),
    ("Network is unreachable", "Host unreachable."),
]
SSH_UNREACHABLE = 255


class TransportError(Exception):
    pass


@dataclass
class Result:
    code: int
    stdout: bytes
    stderr: bytes


class Transport(Protocol):
    async def run(
        self, command: str, stdin: Path | None = None, timeout: float | None = None
    ) -> Result: ...


def ssh_reason(stderr: str) -> str:
    for marker, reason in SSH_FAILURES:
        if marker in stderr:
            return reason
    first = stderr.strip().splitlines()[0] if stderr.strip() else "no message"
    return f"SSH failed: {first}"


def remote_script(target: Target, command: str) -> str:
    steps = [
        "set -e",
        f"mkdir -p {shlex.quote(target.workdir)}",
        *target.setup,
        f"cd {shlex.quote(target.workdir)}",
        command,
    ]
    return "\n".join(steps)


async def execute(argv: list[str], stdin: Path | None, timeout: float | None) -> Result:
    source = await asyncio.to_thread(stdin.open, "rb") if stdin else None
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=source if source else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise TransportError("Timed out.")
    finally:
        if source:
            source.close()
    return Result(process.returncode, out, err)


class LocalShellTransport:
    def __init__(self, target: Target):
        self.target = target

    async def run(
        self, command: str, stdin: Path | None = None, timeout: float | None = None
    ) -> Result:
        return await execute(["bash", "-c", remote_script(self.target, command)], stdin, timeout)


class SshTransport:
    def __init__(self, target: Target, control_dir: Path):
        self.target = target
        self.control_dir = control_dir

    def argv(self, command: str) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.target.connect_timeout_seconds}",
            "-o",
            "ControlMaster=auto",
            "-o",
            f"ControlPath={self.control_dir}/%C",
            "-o",
            "ControlPersist=60s",
            self.target.host,
            "--",
            "bash -c " + shlex.quote(remote_script(self.target, command)),
        ]

    async def run(
        self, command: str, stdin: Path | None = None, timeout: float | None = None
    ) -> Result:
        self.control_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        result = await execute(self.argv(command), stdin, timeout)
        if result.code == SSH_UNREACHABLE:
            raise TransportError(ssh_reason(result.stderr.decode(errors="replace")))
        return result


def make_transport(target: Target, control_dir: Path) -> Transport:
    if target.transport == "ssh":
        return SshTransport(target, control_dir)
    return LocalShellTransport(target)


async def checked(
    transport: Transport, command: str, stdin: Path | None = None, timeout: float | None = None
) -> bytes:
    result = await transport.run(command, stdin, timeout)
    if result.code != 0:
        message = result.stderr.decode(errors="replace").strip().splitlines()
        raise TransportError(
            f"Remote command failed: {message[-1] if message else f'exit {result.code}'}"
        )
    return result.stdout


async def send(
    transport: Transport,
    root: Path,
    members: list[str],
    remote_dir: str,
    timeout: float | None = None,
) -> None:
    with tempfile.NamedTemporaryFile(suffix=".tar") as archive:
        with tarfile.open(archive.name, "w") as tar:
            for member in members:
                tar.add(root / member, arcname=member)
        await checked(
            transport,
            f"mkdir -p {shlex.quote(remote_dir)} && tar -C {shlex.quote(remote_dir)} -xf -",
            Path(archive.name),
            timeout,
        )


async def fetch(
    transport: Transport,
    remote_dir: str,
    members: list[str],
    local_dir: Path,
    timeout: float | None = None,
) -> None:
    quoted = " ".join(shlex.quote(m) for m in members)
    command = (
        f'cd {shlex.quote(remote_dir)} && set -- && for m in {quoted}; do [ -e "$m" ] && set -- "$@" "$m"; '
        'done; if [ "$#" -gt 0 ]; then tar -cf - "$@"; fi'
    )
    data = await checked(transport, command, timeout=timeout)
    if data:
        local_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            tar.extractall(local_dir, filter="data")
