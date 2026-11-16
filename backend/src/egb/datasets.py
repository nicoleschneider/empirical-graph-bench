import csv
import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from egb.registry import Dataset, DatasetFile

CHUNK_BYTES = 1 << 20


class DatasetStatus(BaseModel):
    id: str
    state: Literal["missing", "unverified", "verified", "mismatch"]
    path: str
    ground_truth: bool
    properties: dict[str, int | float | str | None] | None = None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DatasetStore:
    def __init__(self, root: Path):
        self.root = root
        self.hashes: dict[tuple[str, int, int], str] = {}

    def graph_path(self, dataset: Dataset) -> Path:
        return self.root / dataset.directory / dataset.graph.file

    def ground_truth_path(self, dataset: Dataset) -> Path | None:
        if dataset.ground_truth is None:
            return None
        return self.root / dataset.directory / dataset.ground_truth.file

    def cached_hash(self, path: Path, compute: bool) -> str | None:
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        if key not in self.hashes and compute:
            self.hashes[key] = sha256(path)
        return self.hashes.get(key)

    def file_state(self, path: Path, expected: str, compute: bool) -> str:
        if not path.exists():
            return "missing"
        digest = self.cached_hash(path, compute)
        if digest is None:
            return "unverified"
        return "verified" if digest == expected else "mismatch"

    def files_state(self, dataset: Dataset, entry: DatasetFile, compute: bool) -> list[str]:
        folder = self.root / dataset.directory
        if not (folder / entry.file).exists():
            return ["missing"]
        return [self.file_state(folder / entry.raw_file, entry.sha256, compute)]

    def status(self, dataset: Dataset, compute: bool) -> DatasetStatus:
        states = self.files_state(dataset, dataset.graph, compute)
        if dataset.ground_truth is not None:
            states += self.files_state(dataset, dataset.ground_truth, compute)
        state = next((s for s in ("missing", "mismatch", "unverified") if s in states), "verified")
        return DatasetStatus(
            id=dataset.id,
            state=state,
            path=str(self.graph_path(dataset)),
            ground_truth=dataset.ground_truth is not None,
        )


def dataset_properties(path: Path) -> dict[str, dict[str, int | float | str | None]]:
    if not path.exists():
        return {}
    with path.open(newline="") as f:
        return {
            row["dataset"]: {k: coerce(v) for k, v in row.items() if k != "dataset"}
            for row in csv.DictReader(f)
        }


def coerce(value: str) -> int | float | str | None:
    if value == "":
        return None
    for kind in (int, float):
        try:
            return kind(value)
        except ValueError:
            pass
    return value
