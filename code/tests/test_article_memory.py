from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time

import pytest
from pydantic import ValidationError

from optomind_optics.harness.article_memory import (
    ArticleMemoryStore,
    DuplicateRecordError,
    EvidenceLevel,
    FactMutationError,
    FactRecord,
    FactStatus,
    MethodEvidence,
    RunMemoryRecord,
    UnknownRecordError,
)


def _canonical(model) -> str:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True)


def _evidence(evidence_id: str = "ev-1", **overrides) -> MethodEvidence:
    fields = dict(
        evidence_id=evidence_id,
        source="s2-snippet",
        scope="broadband AR 450-700 nm",
        query="anti-reflection coating multilayer",
        excerpt_hash="abc123",
        evidence_level=EvidenceLevel.snippet,
        recorded_at="2026-08-15T00:00:00Z",
        artifact_reference="METHOD_RESEARCH.json",
    )
    fields.update(overrides)
    return MethodEvidence(**fields)


def _memory(memory_id: str = "mem-1", **overrides) -> RunMemoryRecord:
    fields = dict(
        memory_id=memory_id,
        run_id="run-1",
        event_type="graph.record",
        graph_node_id="node-1",
        artifact_ids=["EXPERIMENT_GRAPH.json"],
        operational_note="Baseline node recorded.",
        recorded_at="2026-08-15T00:00:01Z",
    )
    fields.update(overrides)
    return RunMemoryRecord(**fields)


def _fact(fact_id: str = "fact-1", **overrides) -> FactRecord:
    fields = dict(
        fact_id=fact_id,
        statement="Mean reflectance below 0.8 percent over 450-700 nm.",
        source_artifact_ids=["PHYSICS_ACCEPTANCE_CERTIFICATE.json"],
        recorded_at="2026-08-15T00:00:02Z",
    )
    fields.update(overrides)
    return FactRecord(**fields)


_SUPERSEDE_SCRIPT = """
import sys, time
from pathlib import Path
from optomind_optics.harness.article_memory import ArticleMemoryStore, FactRecord

path, existing_id, new_id, ready_file, start_file = sys.argv[1:6]
Path(ready_file).write_text("ready", encoding="utf-8")
deadline = time.time() + 20.0
while not Path(start_file).exists():
    if time.time() > deadline:
        print("DENIED:TIMEOUT")
        raise SystemExit(1)
    time.sleep(0.01)
store = ArticleMemoryStore(Path(path))
try:
    store.supersede_fact(
        existing_id,
        FactRecord(
            fact_id=new_id,
            statement="concurrent correction",
            source_artifact_ids=["PHYSICS_ACCEPTANCE_CERTIFICATE.json"],
        ),
    )
    print("SUCCESS")
except Exception as exc:
    print(f"DENIED:{type(exc).__name__}")
"""


def test_evidence_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        MethodEvidence(scope="s", query="q", excerpt_hash="h")  # source missing
    with pytest.raises(ValidationError):
        MethodEvidence(source="s", query="q", excerpt_hash="h")  # scope missing
    with pytest.raises(ValidationError):
        MethodEvidence(source="s", scope="s", excerpt_hash="h")  # query missing
    with pytest.raises(ValidationError):
        MethodEvidence(source="s", scope="s", query="q")  # excerpt_hash missing
    with pytest.raises(ValidationError):
        MethodEvidence(
            source="s", scope="s", query="q", excerpt_hash="h", evidence_level="maybe"
        )


def test_memory_and_fact_round_trip_preserve_empty_optional_fields() -> None:
    memory = RunMemoryRecord(
        memory_id="m1",
        run_id="r1",
        event_type="graph.record",
        operational_note="note",
    )
    raw = json.loads(memory.model_dump_json())
    assert raw["artifact_ids"] == []
    assert raw["graph_node_id"] is None
    assert RunMemoryRecord.model_validate_json(
        memory.model_dump_json()
    ).model_dump() == memory.model_dump()

    fact = _fact()
    fact_raw = json.loads(fact.model_dump_json())
    assert fact_raw["metadata"] == {}
    assert fact_raw["supersedes_id"] is None
    assert FactRecord.model_validate_json(fact.model_dump_json()).model_dump() == fact.model_dump()


def test_deterministic_serialization() -> None:
    first = _evidence()
    second = _evidence()
    assert first.model_dump_json() == second.model_dump_json()
    assert _canonical(first) == _canonical(second)
    assert _canonical(_memory()) == _canonical(_memory())
    assert _canonical(_fact()) == _canonical(_fact())


def test_fact_requires_source_artifact_ids() -> None:
    with pytest.raises(ValidationError):
        FactRecord.model_validate(
            {"fact_id": "f1", "statement": "claim without source"}
        )
    with pytest.raises(ValidationError):
        FactRecord(
            fact_id="f1", statement="claim", source_artifact_ids=[]
        )


def test_store_persists_all_domains_and_rejects_duplicates(tmp_path) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    store.add_evidence(_evidence())
    store.add_run_memory(_memory())
    store.add_fact(_fact())

    assert store.get_evidence("ev-1") == _evidence()
    assert store.get_run_memory("mem-1") == _memory()
    assert store.get_fact("fact-1") == _fact()

    with pytest.raises(DuplicateRecordError, match="Duplicate"):
        store.add_evidence(_evidence())
    with pytest.raises(DuplicateRecordError, match="Duplicate"):
        store.add_run_memory(_memory())
    with pytest.raises(DuplicateRecordError, match="Duplicate"):
        store.add_fact(_fact())

    reopened = ArticleMemoryStore(tmp_path / "memory.sqlite")
    assert reopened.evidence_records() == [store.get_evidence("ev-1")]
    assert reopened.run_memory_records() == [store.get_run_memory("mem-1")]
    assert reopened.fact_records() == [store.get_fact("fact-1")]


def test_fact_supersede_appends_without_mutating_old(tmp_path) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    original = _fact()
    store.add_fact(original)
    correction = _fact(
        fact_id="fact-2",
        statement="Corrected mean reflectance below 0.6 percent.",
        source_artifact_ids=["PHYSICS_ACCEPTANCE_CERTIFICATE.json", "OBJECTIVE_REPORT.json"],
    )
    new_fact = store.supersede_fact("fact-1", correction)

    old_view = store.get_fact("fact-1")
    assert old_view.status == FactStatus.superseded
    assert old_view.superseded_by_id == "fact-2"
    assert old_view.statement == original.statement
    assert new_fact.supersedes_id == "fact-1"
    assert new_fact.status == FactStatus.active

    # The persisted payload for the old fact is byte-identical (append-only).
    import sqlite3

    connection = sqlite3.connect(str(tmp_path / "memory.sqlite"))
    try:
        row = connection.execute(
            "SELECT payload_json FROM facts WHERE fact_id='fact-1'"
        ).fetchone()
    finally:
        connection.close()
    assert json.loads(row[0]) == original.model_dump(mode="json")


def test_fact_cannot_supersede_already_superseded_or_itself(tmp_path) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    store.add_fact(_fact("fact-1"))
    store.supersede_fact("fact-1", _fact("fact-2", statement="second"))
    with pytest.raises(FactMutationError, match="already superseded"):
        store.supersede_fact("fact-1", _fact("fact-3", statement="third"))
    with pytest.raises(FactMutationError, match="supersede itself"):
        store.supersede_fact("fact-2", _fact("fact-2", statement="self"))
    with pytest.raises(FactMutationError, match="declares supersedes_id"):
        store.supersede_fact(
            "fact-2",
            _fact("fact-3", statement="wrong parent", supersedes_id="fact-9"),
        )


def test_fact_lineage_is_deterministic(tmp_path) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    store.add_fact(_fact("fact-1", statement="v1"))
    store.supersede_fact("fact-1", _fact("fact-2", statement="v2"))
    store.supersede_fact("fact-2", _fact("fact-3", statement="v3"))

    first = store.fact_lineage("fact-3")
    second = store.fact_lineage("fact-3")
    assert [item.fact_id for item in first] == ["fact-1", "fact-2", "fact-3"]
    assert [item.fact_id for item in second] == ["fact-1", "fact-2", "fact-3"]
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]


def test_run_memory_never_returned_as_facts(tmp_path) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    store.add_run_memory(_memory())
    store.add_fact(_fact())
    facts = store.fact_records()
    assert all(isinstance(item, FactRecord) for item in facts)
    assert all(item.fact_id != "mem-1" for item in facts)
    with pytest.raises(TypeError, match="FactRecord"):
        store.add_fact(_memory())
    with pytest.raises(TypeError, match="FactRecord"):
        store.supersede_fact("fact-1", _memory())
    with pytest.raises(TypeError, match="RunMemoryRecord"):
        store.add_run_memory(_fact())


def test_no_secret_material_is_persisted(tmp_path) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    secret = "sk-super-secret-3f9a"
    evidence = MethodEvidence.model_validate(
        {**_evidence().model_dump(), "api_key": secret}
    )
    fact = FactRecord.model_validate({**_fact().model_dump(), "api_key": secret})
    store.add_evidence(evidence)
    store.add_fact(fact)
    snapshot_text = json.dumps(store.snapshot(), sort_keys=True)
    assert secret not in snapshot_text
    db_text = (tmp_path / "memory.sqlite").read_text(encoding="utf-8", errors="replace")
    assert secret not in db_text


def test_snapshot_is_deterministic_and_reopen_stable(tmp_path) -> None:
    path = tmp_path / "memory.sqlite"
    store = ArticleMemoryStore(path)
    store.add_evidence(_evidence())
    store.add_run_memory(_memory())
    store.add_fact(_fact())
    first = store.snapshot()
    second = store.snapshot()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    reopened = ArticleMemoryStore(path)
    assert json.dumps(reopened.snapshot(), sort_keys=True) == json.dumps(
        first, sort_keys=True
    )


def test_unknown_ids_fail_clearly(tmp_path) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    with pytest.raises(UnknownRecordError, match="evidence_id"):
        store.get_evidence("missing")
    with pytest.raises(UnknownRecordError, match="memory_id"):
        store.get_run_memory("missing")
    with pytest.raises(UnknownRecordError, match="fact_id"):
        store.get_fact("missing")
    with pytest.raises(UnknownRecordError, match="fact_id"):
        store.supersede_fact("missing", _fact("fact-x"))


def _fact_rows(path) -> dict[str, dict]:
    connection = sqlite3.connect(str(path))
    try:
        return {
            row[0]: json.loads(row[1])
            for row in connection.execute(
                "SELECT fact_id,payload_json FROM facts"
            ).fetchall()
        }
    finally:
        connection.close()


def _status_events(path) -> list[tuple[str, str]]:
    connection = sqlite3.connect(str(path))
    try:
        return [
            (row[0], row[1])
            for row in connection.execute(
                "SELECT fact_id,superseded_by_id FROM fact_status_events"
            ).fetchall()
        ]
    finally:
        connection.close()


def test_fact_supersede_single_winner_across_processes(tmp_path) -> None:
    path = tmp_path / "memory.sqlite"
    store = ArticleMemoryStore(path)
    store.add_fact(_fact("fact-1"))

    processes = []
    for tag in ("2a", "2b"):
        ready = tmp_path / f"ready_{tag}"
        processes.append(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    _SUPERSEDE_SCRIPT,
                    str(path),
                    "fact-1",
                    f"fact-{tag}",
                    str(ready),
                    str(tmp_path / "start"),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        )

    deadline = 0.0
    while not (
        (tmp_path / "ready_2a").exists() and (tmp_path / "ready_2b").exists()
    ):
        assert deadline < 10.0, "subprocesses did not become ready"
        time.sleep(0.01)
        deadline += 0.01
    (tmp_path / "start").write_text("go", encoding="utf-8")

    outcomes = [
        process.communicate(timeout=60)[0].strip().splitlines()[-1]
        for process in processes
    ]
    assert sorted(outcomes) == ["DENIED:FactMutationError", "SUCCESS"]

    rows = _fact_rows(path)
    events = _status_events(path)
    winner = next(
        tag for tag in ("fact-2a", "fact-2b") if tag in rows
    )
    loser = "fact-2b" if winner == "fact-2a" else "fact-2a"
    assert set(rows) == {"fact-1", winner}
    assert loser not in rows
    assert len(events) == 1
    assert events[0] == ("fact-1", winner)
    assert rows["fact-1"]["supersedes_id"] is None


def test_fact_supersede_single_winner_threads(tmp_path) -> None:
    path = tmp_path / "memory.sqlite"
    store = ArticleMemoryStore(path)
    store.add_fact(_fact("fact-1"))
    barrier = threading.Barrier(2)
    results: list[tuple[str, str]] = []
    result_lock = threading.Lock()

    def attempt(new_id: str) -> None:
        worker_store = ArticleMemoryStore(path)
        barrier.wait()
        try:
            worker_store.supersede_fact(
                "fact-1", _fact(new_id, statement="thread correction")
            )
            with result_lock:
                results.append(("SUCCESS", new_id))
        except Exception as exc:
            with result_lock:
                results.append((type(exc).__name__, new_id))

    threads = [
        threading.Thread(target=attempt, args=("fact-2a",)),
        threading.Thread(target=attempt, args=("fact-2b",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [
        ("FactMutationError", "fact-2a"),
        ("SUCCESS", "fact-2b"),
    ] or sorted(results) == [
        ("FactMutationError", "fact-2b"),
        ("SUCCESS", "fact-2a"),
    ]
    winner = next(tag for kind, tag in results if kind == "SUCCESS")
    loser = "fact-2b" if winner == "fact-2a" else "fact-2a"
    rows = _fact_rows(path)
    assert loser not in rows
    assert _status_events(path) == [("fact-1", winner)]


def test_snapshot_uses_single_connection(tmp_path, monkeypatch) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    store.add_evidence(_evidence())
    store.add_run_memory(_memory())
    store.add_fact(_fact())

    original = store._connect
    calls = {"count": 0}

    def counting_connect():
        calls["count"] += 1
        return original()

    monkeypatch.setattr(store, "_connect", counting_connect)
    snapshot = store.snapshot()
    assert calls["count"] == 1
    assert snapshot["evidence"] == [_evidence().model_dump(mode="json")]
    assert snapshot["run_memory"] == [_memory().model_dump(mode="json")]
    assert snapshot["facts"] == [_fact().model_dump(mode="json")]

    calls["count"] = 0
    facts = store.fact_records()
    assert calls["count"] == 1
    assert facts == [store.get_fact("fact-1")]


def test_snapshot_consistent_under_writer_barrier(tmp_path) -> None:
    store = ArticleMemoryStore(tmp_path / "memory.sqlite")
    store.add_evidence(_evidence("ev-0"))
    ready = threading.Event()
    go = threading.Event()

    def writer() -> None:
        connection = store._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            pending = _evidence("ev-pending").model_dump(mode="json")
            connection.execute(
                "INSERT INTO method_evidence(evidence_id,payload_json,recorded_at) "
                "VALUES(?,?,?)",
                ("ev-pending", store._canonical_json(pending), time.time() + 1000.0),
            )
            ready.set()
            assert go.wait(timeout=10)
            connection.execute("COMMIT")
        finally:
            connection.close()

    thread = threading.Thread(target=writer)
    thread.start()
    assert ready.wait(timeout=10)

    before = store.snapshot()
    assert [item["evidence_id"] for item in before["evidence"]] == ["ev-0"]

    go.set()
    thread.join(timeout=10)
    after = store.snapshot()
    assert [item["evidence_id"] for item in after["evidence"]] == [
        "ev-0",
        "ev-pending",
    ]
