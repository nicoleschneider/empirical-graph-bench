import os
import platform
import re
import shutil
import subprocess
from pathlib import Path

CUDA_CHECK = "import nx_cugraph, cupy; assert cupy.cuda.runtime.getDeviceCount() > 0"
CUDF_CHECK = "import cudf; cudf.Series([1, 2]).sum()"


def command_output(argv: list[str]) -> str | None:
    if shutil.which(argv[0]) is None:
        return None
    result = subprocess.run(argv, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def sysctl(key: str) -> str | None:
    return command_output(["sysctl", "-n", key])


def optional_int(value: str | None) -> int | None:
    return None if value is None else int(value)


def mac_specs() -> dict:
    groups = []
    for level in range(optional_int(sysctl("hw.nperflevels")) or 0):
        groups.append(
            {
                "name": sysctl(f"hw.perflevel{level}.name") or f"level {level}",
                "physical_cores": int(sysctl(f"hw.perflevel{level}.physicalcpu") or 0),
            }
        )
    return {
        "model": sysctl("hw.model"),
        "cpu": sysctl("machdep.cpu.brand_string") or platform.processor(),
        "physical_cores": optional_int(sysctl("hw.physicalcpu")),
        "logical_cores": optional_int(sysctl("hw.logicalcpu")),
        "core_groups": groups,
        "memory_bytes": optional_int(sysctl("hw.memsize")),
        "os_version": platform.mac_ver()[0],
    }


def linux_specs() -> dict:
    cpuinfo = Path("/proc/cpuinfo").read_text()
    models = re.findall(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
    cores = set()
    for block in cpuinfo.split("\n\n"):
        fields = dict(re.findall(r"^(physical id|core id)\s*:\s*(\d+)$", block, re.MULTILINE))
        if len(fields) == 2:
            cores.add((fields["physical id"], fields["core id"]))
    memory = re.search(r"^MemTotal:\s*(\d+) kB$", Path("/proc/meminfo").read_text(), re.MULTILINE)
    return {
        "model": None,
        "cpu": models[0] if models else platform.processor(),
        "physical_cores": len(cores) or None,
        "logical_cores": os.cpu_count(),
        "core_groups": [],
        "memory_bytes": int(memory.group(1)) * 1024 if memory else None,
        "os_version": platform.freedesktop_os_release().get("PRETTY_NAME", platform.release()),
    }


def nvidia_gpus() -> tuple[list[dict], str | None]:
    listing = command_output(
        ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"]
    )
    if not listing:
        return [], None
    gpus = [
        dict(zip(["name", "driver", "memory"], [part.strip() for part in line.split(",")]))
        for line in listing.splitlines()
    ]
    version = re.search(r"CUDA Version:\s*([\d.]+)", command_output(["nvidia-smi"]) or "")
    return gpus, version.group(1) if version else None


def detect_specs() -> dict:
    system = platform.system()
    details = mac_specs() if system == "Darwin" else linux_specs()
    gpus, cuda_version = nvidia_gpus()
    return {
        "os": system,
        "machine": platform.machine(),
        "hostname": platform.node(),
        "gpus": gpus,
        "cuda_version": cuda_version,
        **details,
    }


def bench_check(bench_python: str, code: str) -> bool:
    return (
        subprocess.run([bench_python, "-c", code], check=False, capture_output=True).returncode == 0
    )


def detect_capabilities(
    specs: dict, bench_python: str, missing_hint: str
) -> tuple[list[str], dict[str, str]]:
    if not Path(bench_python).exists():
        reason = f"Benchmark environment not found at {bench_python}. {missing_hint}"
        return [], {"cpu": reason, "cuda": reason, "cudf": reason}
    capabilities = ["cpu"]
    unavailable = {}
    if not specs["gpus"]:
        unavailable["cuda"] = "No NVIDIA GPU found."
        unavailable["cudf"] = "No NVIDIA GPU found."
        return capabilities, unavailable
    if bench_check(bench_python, CUDA_CHECK):
        capabilities.append("cuda")
    else:
        unavailable["cuda"] = "nx-cugraph cannot use the GPU in the benchmark environment."
    if "cuda" in capabilities and bench_check(bench_python, CUDF_CHECK):
        capabilities.append("cudf")
    else:
        unavailable["cudf"] = "cudf is not usable in the benchmark environment."
    return capabilities, unavailable


def default_label(specs: dict) -> str:
    parts = [specs["cpu"]]
    if specs.get("memory_bytes"):
        parts.append(f"{round(specs['memory_bytes'] / 2**30)}gb")
    return re.sub(r"[^a-z0-9]+", "-", " ".join(parts).lower()).strip("-")[:64]


def git_state(path: str) -> dict:
    commit = subprocess.run(
        ["git", "-C", path, "rev-parse", "HEAD"], check=False, capture_output=True, text=True
    )
    status = subprocess.run(
        ["git", "-C", path, "status", "--porcelain"], check=False, capture_output=True, text=True
    )
    changes = [line for line in status.stdout.splitlines() if line.strip()]
    return {
        "commit": commit.stdout.strip() or None,
        "dirty": bool(changes),
        "uncommitted_changes": len(changes),
    }
