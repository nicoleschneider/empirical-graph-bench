import json
from pathlib import Path

import pytest

from egb.registry import Registry
from egb.sessions import ConfigInput, ConfigurationInput, LogEntryInput, SessionCreate, SessionStore


def paper_config(**changes) -> ConfigInput:
    values = {
        "tasks": ["community-detection", "centrality"],
        "datasets": ["email-Eu-core"],
        "candidates": [
            ConfigurationInput(
                implementation="louvain-networkx", representation="networkx-text", exploratory=True
            ),
            ConfigurationInput(implementation="pagerank-nx-cugraph", representation="cudf-float64"),
        ],
        "metrics": ["end_to_end_s", "nmi", "pagerank_l1"],
        "repetitions": 2,
    }
    return ConfigInput(**values | changes)


@pytest.fixture
def store(tmp_path: Path, registry: Registry) -> SessionStore:
    return SessionStore(tmp_path, registry)


def test_create_writes_folder(store: SessionStore) -> None:
    session = store.create(
        SessionCreate(name="Demo", question="Which configuration?", config=paper_config()),
        None,
        None,
    )
    folder = store.folder(session.meta.id)
    assert {p.name for p in folder.iterdir()} == {
        "session.json",
        "config.json",
        "log.jsonl",
        "jobs.jsonl",
        "jobs",
        "environment",
    }
    assert [c.id for c in session.config.candidates] == ["c1", "c2"]
    assert session.config.exploratory == ["c1"]


def test_invalid_variant_rejected(store: SessionStore) -> None:
    config = paper_config(
        candidates=[
            ConfigurationInput(implementation="louvain-networkx", representation="cudf-float64")
        ]
    )
    with pytest.raises(ValueError, match="has no cudf-float64 variant"):
        store.create(SessionCreate(name="x", question="y", config=config), None, None)


def test_config_update_keeps_ids_and_colors(store: SessionStore) -> None:
    session = store.create(SessionCreate(name="x", question="y", config=paper_config()), None, None)
    updated = store.update_config(
        session.meta.id,
        paper_config(
            candidates=[
                ConfigurationInput(
                    implementation="pagerank-nx-cugraph", representation="cudf-float64"
                ),
                ConfigurationInput(
                    implementation="pagerank-networkx", representation="networkx-text"
                ),
            ]
        ),
    )
    assert [(c.id, c.color_slot) for c in updated.candidates] == [("c2", 2), ("c3", 3)]
    history = (store.folder(session.meta.id) / "config_history.jsonl").read_text().splitlines()
    assert len(history) == 1


def test_log_is_chained_and_verifiable(store: SessionStore) -> None:
    session = store.create(SessionCreate(name="x", question="y", config=paper_config()), None, None)
    sid = session.meta.id
    first = store.append_log(
        sid, LogEntryInput(kind="hypothesis", text="Loading dominates.", iteration=1)
    )
    second = store.append_log(
        sid,
        LogEntryInput(
            kind="prediction", text="GPU below 4.9 s.", iteration=1, refers_to=[first.id]
        ),
    )
    assert second.prev_entry_hash == first.entry_hash
    assert store.verify_log(sid).problems == []
    log_path = store.folder(sid) / "log.jsonl"
    lines = log_path.read_text().splitlines()
    tampered = json.loads(lines[0]) | {"text": "Processing dominates."}
    log_path.write_text("\n".join([json.dumps(tampered), lines[1]]) + "\n")
    assert store.verify_log(sid).problems == ["e1: entry hash does not match its contents."]


def test_log_rejects_unknown_reference(store: SessionStore) -> None:
    session = store.create(SessionCreate(name="x", question="y", config=paper_config()), None, None)
    with pytest.raises(ValueError, match="Unknown log entries: e9"):
        store.append_log(
            session.meta.id,
            LogEntryInput(kind="outcome", text="Met.", iteration=1, refers_to=["e9"]),
        )
