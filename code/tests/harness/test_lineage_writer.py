"""T-07 tests: round-k lineage writing (any round depth, JSON round-trip)."""

from __future__ import annotations

import json

import pytest

from optomind_optics.harness.lineage_writer import (
    LINEAGE_FILENAME,
    LineageRecord,
    write_lineage,
)


def test_write_lineage_round1(tmp_path):
    record = LineageRecord(
        round=1,
        parent_round=None,
        parent_task_sha256=None,
        task_sha256="a" * 64,
        adjustment_reason="initial plan",
    )
    path = write_lineage(record, tmp_path / "round_1" / "route_A")

    assert path == tmp_path / "round_1" / "route_A" / LINEAGE_FILENAME
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["round"] == 1
    assert data["parent_round"] is None
    assert data["parent_task_sha256"] is None
    assert data["task_sha256"] == "a" * 64
    assert data["adjustment_reason"] == "initial plan"


def test_write_lineage_round2(tmp_path):
    record = LineageRecord(
        round=2,
        parent_round=1,
        parent_task_sha256="a" * 64,
        task_sha256="b" * 64,
        adjustment_reason="tighten thickness after failed margin",
    )
    path = write_lineage(record, tmp_path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["round"] == 2
    assert data["parent_round"] == 1
    assert data["parent_task_sha256"] == "a" * 64
    assert data["task_sha256"] == "b" * 64


def test_write_lineage_round3(tmp_path):
    record = LineageRecord(
        round=3,
        parent_round=2,
        parent_task_sha256="b" * 64,
        task_sha256="c" * 64,
        adjustment_reason="widen band after round-2 spectrum drift",
    )
    path = write_lineage(record, tmp_path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["round"] == 3
    assert data["parent_round"] == 2
    assert data["parent_task_sha256"] == "b" * 64
    assert data["task_sha256"] == "c" * 64


def test_lineage_json_deserializable(tmp_path):
    record = LineageRecord(
        round=2,
        parent_round=1,
        parent_task_sha256="a" * 64,
        task_sha256="b" * 64,
        adjustment_reason="adjust angles",
    )
    path = write_lineage(record, tmp_path)
    restored = json.loads(path.read_text(encoding="utf-8"))

    assert restored == record.to_dict()
    for key in (
        "round",
        "parent_round",
        "parent_task_sha256",
        "task_sha256",
        "adjustment_reason",
    ):
        assert key in restored


def test_invalid_records_rejected():
    with pytest.raises(ValueError):
        LineageRecord(
            round=0, parent_round=None, parent_task_sha256=None,
            task_sha256="a", adjustment_reason="x",
        )
    # round 1 must not declare a parent
    with pytest.raises(ValueError):
        LineageRecord(
            round=1, parent_round=None, parent_task_sha256="a" * 64,
            task_sha256="b" * 64, adjustment_reason="x",
        )
    # later rounds need the immediate parent link
    with pytest.raises(ValueError):
        LineageRecord(
            round=3, parent_round=1, parent_task_sha256="a" * 64,
            task_sha256="c" * 64, adjustment_reason="x",
        )
