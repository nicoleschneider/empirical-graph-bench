import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from egb import machine

LOCAL_ID = "local"
LABEL_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"


class Gpu(BaseModel):
    name: str
    driver: str
    memory: str


class CoreGroup(BaseModel):
    name: str
    physical_cores: int


class Specs(BaseModel):
    model: str | None
    cpu: str
    hostname: str | None = None
    physical_cores: int | None
    logical_cores: int | None
    core_groups: list[CoreGroup]
    memory_bytes: int | None
    os: str
    os_version: str
    machine: str
    gpus: list[Gpu]
    cuda_version: str | None


class Hardware(BaseModel):
    id: str
    label: str
    runner: Literal["local", "ssh", "local-shell"]
    host: str | None = None
    status: Literal["reachable", "checking", "unavailable"]
    reason: str | None = None
    specs: Specs | None
    capabilities: list[str]
    unavailable: dict[str, str]
    supports: list[str] | None = None


def stored_label(path: Path) -> str | None:
    if not path.exists():
        return None
    return json.loads(path.read_text()).get(LOCAL_ID, {}).get("label")


def store_label(path: Path, label: str) -> None:
    if not re.match(LABEL_PATTERN, label):
        raise ValueError("Label must be lowercase letters, digits and hyphens.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({LOCAL_ID: {"label": label}}, indent=2))


def detect_local(bench_python: Path, label_file: Path) -> Hardware:
    specs = machine.detect_specs()
    capabilities, unavailable = machine.detect_capabilities(
        specs, str(bench_python), "Run setup-bench."
    )
    return Hardware(
        id=LOCAL_ID,
        label=stored_label(label_file) or machine.default_label(specs),
        runner="local",
        status="reachable",
        specs=Specs.model_validate(specs),
        capabilities=capabilities,
        unavailable=unavailable,
    )
