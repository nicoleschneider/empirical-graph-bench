from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from egb.targets import Target, load_targets
from egb.transport import SshTransport, remote_script, ssh_reason

BASE = {"name": "lab", "host": "lab", "workdir": "/scratch/egb", "supports": ["louvain-networkx"]}


def write(tmp_path: Path, target: dict) -> Path:
    path = tmp_path / "targets.yaml"
    path.write_text(yaml.safe_dump({"targets": [target]}))
    return path


@pytest.mark.parametrize("field", ["password", "identity_file", "key", "user"])
def test_credential_fields_rejected(tmp_path: Path, field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_targets(write(tmp_path, BASE | {field: "secret"}), {"louvain-networkx"})


def test_target_rules() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        Target(**BASE | {"name": "local"})
    with pytest.raises(ValidationError, match="need a host"):
        Target(**BASE | {"host": None})
    with pytest.raises(ValidationError, match="absolute"):
        Target(**BASE | {"workdir": "egb"})
    with pytest.raises(ValidationError, match="take no host"):
        Target(**BASE | {"transport": "local-shell"})


def test_unknown_implementation_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown implementations nope"):
        load_targets(write(tmp_path, BASE | {"supports": ["nope"]}), {"louvain-networkx"})


def test_ssh_never_prompts_or_skips_host_keys(tmp_path: Path) -> None:
    argv = SshTransport(Target(**BASE), tmp_path).argv("true")
    options = [argv[i + 1] for i, a in enumerate(argv) if a == "-o"]
    assert "BatchMode=yes" in options
    assert not any(o.startswith("StrictHostKeyChecking") for o in options)
    assert not any(a in argv for a in ("-i", "-p", "-l"))
    assert argv[argv.index("--") - 1] == "lab"


def test_remote_script_quotes_workdir() -> None:
    script = remote_script(
        Target(**BASE | {"workdir": "/scratch/a b", "setup": ["module load x"]}), "true"
    )
    assert script.splitlines() == [
        "set -e",
        "mkdir -p '/scratch/a b'",
        "module load x",
        "cd '/scratch/a b'",
        "true",
    ]


@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        ("ssh: connect to host x port 22: Connection refused", "Host unreachable."),
        ("ssh: connect to host x port 22: Operation timed out", "Host unreachable."),
        ("Host key verification failed.", "Host key unknown. Connect once with ssh to accept it."),
        ("me@x: Permission denied (publickey).", "SSH needs a key in your agent."),
        (
            "ssh: Could not resolve hostname x: nodename nor servname provided",
            "Host name not found.",
        ),
        ("something else", "SSH failed: something else"),
    ],
)
def test_ssh_failure_reasons(stderr: str, reason: str) -> None:
    assert ssh_reason(stderr) == reason
