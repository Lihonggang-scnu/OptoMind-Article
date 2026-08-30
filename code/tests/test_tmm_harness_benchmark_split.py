from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from optomind_optics.harness.benchmarks import (
    BenchmarkIntegrityError,
    holdout_access_events,
    load_benchmark_task,
    load_benchmark_tasks,
)


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = ROOT / "benchmarks" / "tmm_harness_v1"
HOLDOUT_IDS = ("HOLDOUT06", "HOLDOUT07", "HOLDOUT08", "HOLDOUT09", "HOLDOUT10")


def _evaluation_contract() -> dict[str, object]:
    return {
        "performance_targets": "soft_scores",
        "admission_gate": "deterministic_physics_validity_only",
        "hard_gates": [],
        "statement": (
            "Performance targets are soft scores; deterministic physics validity "
            "is the only admission gate."
        ),
    }


def _synthetic_holdout_dir(tmp_path: Path) -> Path:
    root = tmp_path / "synthetic_tmm_harness"
    root.mkdir()
    dev_rows = [
        {
            "id": f"DEV0{index}",
            "split": "dev",
            "domain": "TMM",
            "title": f"Synthetic development task {index}",
            "natural_language_question": "Synthetic development question.",
            "task_family": "forward_analysis",
            "capability_axes": ["synthetic"],
            "expected_artifacts": ["FINAL_RESULT.json"],
            "evaluation_contract": _evaluation_contract(),
        }
        for index in range(1, 6)
    ]
    holdout_rows = [
        {
            "id": task_id,
            "split": "holdout",
            "domain": "TMM",
            "title": f"Synthetic sealed task {index}",
            "natural_language_question": "Synthetic sealed question.",
            "task_family": "forward_analysis",
            "capability_axes": ["synthetic_holdout"],
            "expected_artifacts": ["FINAL_RESULT.json"],
            "evaluation_contract": _evaluation_contract(),
        }
        for index, task_id in enumerate(HOLDOUT_IDS, start=6)
    ]
    files = {"dev": ("dev_tasks.json", dev_rows), "holdout": ("holdout_tasks.json", holdout_rows)}
    splits: dict[str, object] = {}
    for split, (filename, rows) in files.items():
        data = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
        (root / filename).write_bytes(data)
        splits[split] = {
            "file": filename,
            "ids": [row["id"] for row in rows],
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    (root / "split_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "tmm_harness_v1.split_manifest.v1",
                "benchmark_id": "tmm_harness_v1",
                "splits": splits,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return root


def test_default_loader_returns_only_the_dev_split() -> None:
    tasks = load_benchmark_tasks()

    assert tuple(task.id for task in tasks) == ("DEV01", "DEV02", "DEV03", "DEV04", "DEV05")
    assert all(task.split == "dev" for task in tasks)


def test_real_manifest_freezes_five_plus_five_ids_without_opening_holdout() -> None:
    manifest = json.loads((BENCHMARK_DIR / "split_manifest.json").read_text(encoding="utf-8"))
    assert tuple(manifest["splits"]["dev"]["ids"]) == (
        "DEV01", "DEV02", "DEV03", "DEV04", "DEV05",
    )
    assert tuple(manifest["splits"]["holdout"]["ids"]) == HOLDOUT_IDS


def test_holdout_requires_both_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPTOMIND_ALLOW_TMM_HOLDOUT", raising=False)
    with pytest.raises(PermissionError):
        load_benchmark_tasks("holdout", allow_holdout=True)

    monkeypatch.setenv("OPTOMIND_ALLOW_TMM_HOLDOUT", "1")
    with pytest.raises(PermissionError):
        load_benchmark_tasks("holdout")


def test_authorized_synthetic_holdout_read_is_audited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _synthetic_holdout_dir(tmp_path)
    audit = tmp_path / "audit" / "HOLDOUT_ACCESS.jsonl"
    monkeypatch.setenv("OPTOMIND_ALLOW_TMM_HOLDOUT", "1")

    task = load_benchmark_task(
        "HOLDOUT08",
        allow_holdout=True,
        benchmark_dir=root,
        holdout_audit_log=audit,
    )

    assert task.id == "HOLDOUT08"
    events = holdout_access_events(audit)
    assert [event["event"] for event in events] == [
        "holdout_read_started",
        "holdout_read_completed",
    ]
    assert all(event["requested_holdout_id"] == "HOLDOUT08" for event in events)
    assert events[0]["access_scope"] == "entire_holdout_file"


def test_synthetic_holdout_requires_audit_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _synthetic_holdout_dir(tmp_path)
    monkeypatch.setenv("OPTOMIND_ALLOW_TMM_HOLDOUT", "1")
    with pytest.raises(PermissionError, match="holdout_audit_log"):
        load_benchmark_tasks("holdout", allow_holdout=True, benchmark_dir=root)


def test_digest_tampering_is_rejected_from_a_copied_development_split(tmp_path: Path) -> None:
    copied = tmp_path / "tmm_harness_v1"
    shutil.copytree(BENCHMARK_DIR, copied)
    dev_path = copied / "dev_tasks.json"
    dev_path.write_bytes(dev_path.read_bytes() + b"\n")

    with pytest.raises(BenchmarkIntegrityError, match="SHA-256 digest mismatch"):
        load_benchmark_tasks(benchmark_dir=copied)


def test_development_tasks_use_soft_scores_and_no_performance_hard_gates() -> None:
    for task in load_benchmark_tasks():
        contract = task.evaluation_contract
        assert contract.performance_targets == "soft_scores"
        assert contract.admission_gate == "deterministic_physics_validity_only"
        assert contract.hard_gates == ()
        assert "Performance targets are soft scores" in contract.statement
        assert "deterministic physics validity is the only admission gate" in contract.statement
