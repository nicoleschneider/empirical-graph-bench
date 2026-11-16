from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from egb.hardware import LABEL_PATTERN, LOCAL_ID


class Target(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=LABEL_PATTERN)
    transport: Literal["ssh", "local-shell"] = "ssh"
    host: str | None = None
    workdir: str
    setup: list[str] = []
    python: str = "python3"
    lock_file: str = "requirements-lock-cpu.txt"
    datasets: Literal["upload", "download"] = "upload"
    supports: list[str]
    poll_seconds: float = Field(default=10, gt=0)
    connect_timeout_seconds: int = Field(default=10, gt=0)
    max_job_minutes: float = Field(default=120, gt=0)
    lost_after_minutes: float = Field(default=10, gt=0)

    @model_validator(mode="after")
    def consistent(self) -> "Target":
        if self.name == LOCAL_ID:
            raise ValueError(f"{LOCAL_ID} is reserved for this machine.")
        if not PurePosixPath(self.workdir).is_absolute():
            raise ValueError(f"{self.name}: workdir must be an absolute path.")
        if self.transport == "ssh" and not self.host:
            raise ValueError(f"{self.name}: ssh targets need a host.")
        if self.transport == "local-shell" and self.host:
            raise ValueError(f"{self.name}: local-shell targets take no host.")
        return self


class TargetsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[Target]


def load_targets(path: Path, implementations: set[str]) -> list[Target]:
    if not path.exists():
        return []
    targets = TargetsFile.model_validate(
        yaml.safe_load(path.read_text()) or {"targets": []}
    ).targets
    names = [t.name for t in targets]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Targets defined twice: {', '.join(duplicates)}.")
    for target in targets:
        unknown = sorted(set(target.supports) - implementations)
        if unknown:
            raise ValueError(f"{target.name}: unknown implementations {', '.join(unknown)}.")
    return targets
