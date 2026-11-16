import csv
import fcntl
import re
import shutil
from pathlib import Path

CACHE_STAMP = re.compile(r"_(\d{4}-\d{2}-\d{2}T[\d.-]+)\.txt$")
RUN_LOCK = Path.home() / ".cache" / "egb" / "run.lock"


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def cache_files(folder: Path) -> set[Path]:
    return set(folder.glob("*.txt")) if folder.exists() else set()


def cache_stamp(path: Path) -> str:
    return CACHE_STAMP.search(path.name).group(1)


def keep_first_outputs(rows: list[dict], files: list[Path], folder: Path) -> list[dict]:
    ordered = sorted(files, key=cache_stamp)
    if len(ordered) != len(rows):
        raise ValueError(f"{len(ordered)} gaib cache files for {len(rows)} runs")
    for path, row in zip(ordered, rows):
        if path.name[: CACHE_STAMP.search(path.name).start()] != row["task"]:
            raise ValueError(f"cache file {path.name} does not match task {row['task']}")
    folder.mkdir(parents=True, exist_ok=True)
    kept = []
    for path, row in zip(ordered, rows):
        if row["run_index"] != "0":
            continue
        name = f"{row['task']}_{row['dataset']}_seed{row['seed']}.txt"
        shutil.copyfile(path, folder / name)
        kept.append(
            {
                "gaib_task": row["task"],
                "dataset": row["dataset"],
                "seed": int(row["seed"]),
                "hardware": row["hardware"],
                "file": name,
            }
        )
    return kept


def open_lock():
    RUN_LOCK.parent.mkdir(parents=True, exist_ok=True)
    return RUN_LOCK.open("w")


def try_lock(handle) -> bool:
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def lock(handle) -> None:
    fcntl.flock(handle, fcntl.LOCK_EX)


def unlock(handle) -> None:
    fcntl.flock(handle, fcntl.LOCK_UN)
    handle.close()
