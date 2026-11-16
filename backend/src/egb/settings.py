from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

BUILT_IN_ENTRIES = Path(str(files("egb") / "entries"))
BUILT_IN_ANALYSIS = Path(str(files("egb") / "analysis.yaml"))


@dataclass(frozen=True)
class Settings:
    gaib_root: Path
    data_dir: Path
    sessions_dir: Path
    bench_python: Path
    lock_file: Path
    entries: tuple[Path, ...]
    poll_seconds: float
    analysis_config: Path

    @property
    def gaib_dir(self) -> Path:
        return self.gaib_root / "gaib"

    @property
    def datasets_dir(self) -> Path:
        return self.data_dir / "datasets"

    @property
    def manifest(self) -> Path:
        return self.datasets_dir / "manifest.json"

    @property
    def pagerank_references(self) -> Path:
        return self.data_dir / "pagerank_references"

    @property
    def data_jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def dataset_properties(self) -> Path:
        return self.data_dir / "dataset_properties.csv"

    @property
    def hardware_file(self) -> Path:
        return self.data_dir / "hardware.json"
