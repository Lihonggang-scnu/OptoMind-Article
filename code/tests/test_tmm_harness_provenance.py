from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from optomind_optics.harness.provenance import (
    ARTIFACT_MANIFEST_FILENAME,
    ArtifactLineageStore,
    ArtifactPathError,
    ArtifactTamperedError,
    HistoryRewriteError,
    LineageCycleError,
    UnknownArtifactError,
)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_task_material_result_certificate_chain_is_persisted_atomically(
    tmp_path: Path,
) -> None:
    _write_json(tmp_path / "TASK.json", {"task_id": "TMM-001", "mode": "tmm"})
    _write_json(
        tmp_path / "MATERIAL_MANIFEST.json",
        {"material": "demo", "dataset_id": "local-001"},
    )
    _write_json(tmp_path / "RESULT.json", {"status": "computed", "values": [1, 2, 3]})
    _write_json(tmp_path / "CERTIFICATE.json", {"status": "verified", "result": "RESULT.json"})

    store = ArtifactLineageStore(tmp_path)
    task = store.register_artifact(
        "task-001",
        "TASK.json",
        artifact_type="task",
        producing_action="create_task",
        producing_node="task-node",
        created_at="2026-08-09T00:00:00Z",
    )
    material = store.register_artifact(
        "material-001",
        "MATERIAL_MANIFEST.json",
        artifact_type="material_manifest",
        producing_action="resolve_materials",
        producing_node="material-node",
        input_artifact_ids=[task.artifact_id],
        scientific_provenance={"provider": "local_fixture", "dataset_id": "local-001"},
        created_at="2026-08-09T00:00:01Z",
    )
    result = store.register_artifact(
        "result-001",
        "RESULT.json",
        artifact_type="result",
        producing_action="run_tmm",
        producing_node="result-node",
        input_artifact_ids=[material.artifact_id],
        created_at="2026-08-09T00:00:02Z",
    )
    certificate = store.register_artifact(
        "certificate-001",
        "CERTIFICATE.json",
        artifact_type="certificate",
        producing_action="verify_result",
        producing_node="certificate-node",
        input_artifact_ids=[result.artifact_id],
        created_at="2026-08-09T00:00:03Z",
    )

    manifest_path = tmp_path / ARTIFACT_MANIFEST_FILENAME
    assert manifest_path.exists()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "tmm-artifact-manifest.v1"
    assert [item["artifact_id"] for item in payload["artifacts"]] == [
        "task-001",
        "material-001",
        "result-001",
        "certificate-001",
    ]
    assert payload["artifacts"][0]["relative_path"] == "TASK.json"
    assert payload["artifacts"][0]["path"] == "TASK.json"
    assert payload["artifacts"][1]["scientific_provenance"]["dataset_id"] == "local-001"
    assert payload["artifacts"][-1]["input_artifact_ids"] == [result.artifact_id]
    assert store.lineage(certificate.artifact_id) == (
        task,
        material,
        result,
        certificate,
    )
    assert store.verify() is True


def test_resume_from_disk_reloads_and_verifies_all_lineage(tmp_path: Path) -> None:
    (tmp_path / "TASK.json").write_bytes(b"task\n")
    first = ArtifactLineageStore(tmp_path)
    first.register_artifact(
        "task-001",
        "TASK.json",
        artifact_type="task",
        producing_action="create_task",
    )

    resumed = ArtifactLineageStore(tmp_path, resume=True)
    assert resumed.artifact_ids == ("task-001",)
    assert resumed.get_artifact("task-001")["bytes"] == 5
    assert resumed.verify()


def test_tampered_file_is_rejected_on_resume(tmp_path: Path) -> None:
    artifact_path = tmp_path / "RESULT.json"
    artifact_path.write_text("original", encoding="utf-8")
    store = ArtifactLineageStore(tmp_path)
    store.register_artifact(
        "result-001",
        artifact_path,
        artifact_type="result",
        producing_action="run_tmm",
    )
    artifact_path.write_text("tampered", encoding="utf-8")

    with pytest.raises(ArtifactTamperedError, match="hash/bytes|tampered"):
        ArtifactLineageStore(tmp_path, resume=True)


def test_path_traversal_is_rejected_before_registration(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-lineage-artifact.txt"
    outside.write_text("outside", encoding="utf-8")
    store = ArtifactLineageStore(tmp_path)

    with pytest.raises(ArtifactPathError, match="traversal|outside"):
        store.register_artifact(
            "bad-001",
            "../outside-lineage-artifact.txt",
            artifact_type="result",
            producing_action="bad_action",
        )
    assert store.artifact_ids == ()


def test_unknown_input_artifact_is_rejected_without_appending(tmp_path: Path) -> None:
    (tmp_path / "RESULT.json").write_text("result", encoding="utf-8")
    store = ArtifactLineageStore(tmp_path)

    with pytest.raises(UnknownArtifactError, match="Unknown input artifact ID"):
        store.register_artifact(
            "result-001",
            "RESULT.json",
            artifact_type="result",
            producing_action="run_tmm",
            input_artifact_ids=["missing-001"],
        )
    assert store.artifact_ids == ()
    assert json.loads(store.manifest_path.read_text(encoding="utf-8"))["artifacts"] == []


def test_idempotent_registration_and_immutable_history(tmp_path: Path) -> None:
    path = tmp_path / "TASK.json"
    path.write_text("same content", encoding="utf-8")
    store = ArtifactLineageStore(tmp_path)
    first = store.register_artifact(
        "task-001",
        path,
        artifact_type="task",
        producing_action="create_task",
        producing_node="node-1",
        created_at="2026-08-09T00:00:00Z",
    )
    assert store.register_artifact(
        "task-001",
        path,
        artifact_type="task",
        producing_action="create_task",
        producing_node="node-1",
        created_at="2026-08-09T00:00:00Z",
    ) == first

    with pytest.raises(HistoryRewriteError, match="Immutable artifact history"):
        store.register_artifact(
            "task-001",
            path,
            artifact_type="task",
            producing_action="different_action",
            producing_node="node-1",
            created_at="2026-08-09T00:00:00Z",
        )

    with pytest.raises(LineageCycleError, match="cycle"):
        store.register_artifact(
            "cycle-001",
            path,
            artifact_type="diagnostic",
            producing_action="diagnose",
            input_artifact_ids=["cycle-001"],
        )


def test_registration_is_thread_safe_and_manifest_updates_are_atomic(tmp_path: Path) -> None:
    paths = []
    for index in range(8):
        path = tmp_path / f"artifact-{index}.txt"
        path.write_text(f"artifact-{index}", encoding="utf-8")
        paths.append(path)
    store = ArtifactLineageStore(tmp_path)

    def register(index: int):
        return store.register_artifact(
            f"artifact-{index}",
            paths[index],
            artifact_type="fixture",
            producing_action="write_fixture",
            producing_node=f"node-{index}",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(register, range(8)))

    assert {record.artifact_id for record in records} == {
        f"artifact-{index}" for index in range(8)
    }
    assert len(store.records) == 8
    assert store.verify()
