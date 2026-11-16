import json
from pathlib import Path

import pytest

from egb.datasets import DatasetStore
from egb.hardware import Hardware, Specs
from egb.jobs import JobRequest, configuration_statuses, local_paths, resolve_session_job
from egb.registry import Registry
from egb.sessions import ConfigInput, ConfigurationInput, SessionCreate, SessionStore
from egb.settings import BUILT_IN_ANALYSIS, BUILT_IN_ENTRIES, Settings


def hardware(capabilities: list[str]) -> Hardware:
    specs = Specs(
        model=None,
        cpu="Test CPU",
        physical_cores=1,
        logical_cores=1,
        core_groups=[],
        memory_bytes=None,
        os="Linux",
        os_version="test",
        machine="x86_64",
        gpus=[],
        cuda_version=None,
    )
    return Hardware(
        id="local",
        label="test-cpu",
        runner="local",
        status="reachable",
        specs=specs,
        capabilities=capabilities,
        unavailable={"cuda": "No NVIDIA GPU found.", "cudf": "No NVIDIA GPU found."},
    )


@pytest.fixture
def setup(tmp_path: Path, registry: Registry):
    settings = Settings(
        gaib_root=tmp_path / "hard",
        data_dir=tmp_path / "data",
        sessions_dir=tmp_path / "sessions",
        bench_python=tmp_path / "python",
        lock_file=tmp_path / "lock.txt",
        entries=(BUILT_IN_ENTRIES,),
        poll_seconds=1.0,
        analysis_config=BUILT_IN_ANALYSIS,
        targets_file=tmp_path / "targets.yaml",
    )
    datasets = DatasetStore(settings.datasets_dir)
    email = registry.datasets["email-Eu-core"]
    folder = datasets.graph_path(email).parent
    folder.mkdir(parents=True)
    for entry in (email.graph, email.ground_truth):
        (folder / entry.file).write_text("0 1\n")
        (folder / entry.raw_file).write_bytes(b"")
    store = SessionStore(settings.sessions_dir, registry)
    config = ConfigInput(
        tasks=["community-detection", "centrality"],
        datasets=["email-Eu-core"],
        candidates=[
            ConfigurationInput(implementation="louvain-networkx", representation="networkx-text"),
            ConfigurationInput(implementation="pagerank-networkx", representation="networkx-text"),
            ConfigurationInput(implementation="pagerank-nx-cugraph", representation="cudf-float64"),
        ],
        metrics=["end_to_end_s"],
        repetitions=2,
    )
    session = store.create(SessionCreate(name="x", question="y", config=config), None, None)
    return settings, datasets, store, session


def test_gpu_configurations_blocked_on_cpu(registry: Registry) -> None:
    statuses = {
        (s.implementation, s.representation): s
        for s in configuration_statuses(registry, hardware(["cpu"]))
    }
    assert statuses[("louvain-networkx", "networkx-text")].runnable
    blocked = statuses[("pagerank-nx-cugraph", "cudf-float64")]
    assert not blocked.runnable
    assert blocked.reasons == [
        "Needs CUDA and cudf.",
        "No NVIDIA GPU found.",
    ]


def test_runner_job_mirrors_gaib_definitions(setup, registry: Registry) -> None:
    settings, datasets, store, session = setup
    job_dir = store.job_dir(session.meta.id, "j1")
    spec = resolve_session_job(
        JobRequest(configurations=["c1", "c2"], datasets=["email-Eu-core"], kind="exploratory"),
        session,
        [],
        registry,
        hardware(["cpu"]),
        datasets,
        local_paths(settings, job_dir),
        "j1",
        job_dir,
    )
    assert [i.driver for i in spec.invocations] == ["gaib-runner"]
    argv = spec.invocations[0].argv
    assert argv[argv.index("--repetitions") + 1] == "2"
    assert argv[argv.index("--base-seed") + 1] == "123"
    definitions = [
        json.loads(p.read_text()) for p in sorted((job_dir / "definitions").glob("*.json"))
    ]
    assert [(d["TASK"], d["DIRECTED"], d["GPU"]) for d in definitions] == [
        ("NX_LOUVAIN", False, False),
        ("NX_PAGERANK", True, False),
    ]
    assert definitions[0]["GT_FILE_PATH"].endswith("email-Eu-core-department-labels.txt")
    assert definitions[1]["GT_FILE_PATH"] is None
    assert spec.configuration_of("NX_PAGERANK", "email-Eu-core") == "c2"


def test_blocked_configuration_rejected(setup, registry: Registry) -> None:
    settings, datasets, store, session = setup
    with pytest.raises(ValueError, match="c3: Needs CUDA and cudf"):
        resolve_session_job(
            JobRequest(configurations=["c3"], datasets=["email-Eu-core"], kind="exploratory"),
            session,
            [],
            registry,
            hardware(["cpu"]),
            datasets,
            local_paths(settings, store.job_dir(session.meta.id, "j1")),
            "j1",
            store.job_dir(session.meta.id, "j1"),
        )


def test_representation_job_on_cuda_machine(setup, registry: Registry) -> None:
    settings, datasets, store, session = setup
    spec = resolve_session_job(
        JobRequest(configurations=["c3"], datasets=["email-Eu-core"], kind="exploratory"),
        session,
        [],
        registry,
        hardware(["cpu", "cuda", "cudf"]),
        datasets,
        local_paths(settings, store.job_dir(session.meta.id, "j1")),
        "j1",
        store.job_dir(session.meta.id, "j1"),
    )
    invocation = spec.invocations[0]
    assert invocation.driver == "gaib-representation-test"
    assert invocation.argv[invocation.argv.index("--dtype") + 1] == "float64"
    assert "--undirected" not in invocation.argv
    assert invocation.env == {"PYTHONPATH": str(settings.gaib_dir)}


def test_targeted_job_needs_hypothesis(setup, registry: Registry) -> None:
    settings, datasets, store, session = setup
    with pytest.raises(ValueError, match="needs a recorded hypothesis"):
        resolve_session_job(
            JobRequest(configurations=["c1"], datasets=["email-Eu-core"], kind="targeted"),
            session,
            [],
            registry,
            hardware(["cpu"]),
            datasets,
            local_paths(settings, store.job_dir(session.meta.id, "j1")),
            "j1",
            store.job_dir(session.meta.id, "j1"),
        )
