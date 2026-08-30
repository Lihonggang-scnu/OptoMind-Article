"""T-17 tests: delivery packaging, integrity warnings, certificate gate."""

from __future__ import annotations

import json

import pytest

from optomind_optics.harness.article_delivery import (
    DELIVERY_EXPECTED_ARTIFACTS,
    DeliveryCertificateError,
    package_delivery,
)


CERT_NAME = "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
ALL_FILES = {
    "article.pdf": b"%PDF-1.7 fake pdf payload",
    "article_zh.md": "# 摘要与结论\n\n中文译文内容。".encode("utf-8"),
    CERT_NAME: json.dumps({"accepted": True, "scope": "broadband"}).encode("utf-8"),
    "ProvenanceLedger.json": b'{"entries": []}',
    "ClaimLedger.json": b'{"claims": []}',
    "replay_record.json": b'{"status": "ok"}',
}


def _build_run(tmp_path, *, accepted=True, translation_skipped=False, drop=()):
    output_dir = tmp_path / "delivery"
    output_dir.mkdir()
    for name, payload in ALL_FILES.items():
        (output_dir / name).write_bytes(payload)
    if not accepted:
        (output_dir / CERT_NAME).write_bytes(
            json.dumps({"accepted": False}).encode("utf-8")
        )
    if translation_skipped:
        (output_dir / "article_zh.md").unlink(missing_ok=True)
    for name in drop:
        (output_dir / name).unlink(missing_ok=True)
    run_manifest = {
        "problem_id": "thin-film-broadband",
        "run_id": "run-20260823-01",
        "translation_skipped": translation_skipped,
    }
    # Stage 14 persisted the run manifest before delivery; mirror that here.
    if "run_manifest.json" not in drop:
        (output_dir / "run_manifest.json").write_bytes(
            json.dumps(run_manifest).encode("utf-8")
        )
    return output_dir, run_manifest


def test_package_delivery_all_present(tmp_path):
    output_dir, run_manifest = _build_run(tmp_path)
    package = package_delivery(run_manifest, output_dir)
    assert package.warnings == []
    assert package.zip_path is None
    assert package.manifest_path.is_file()
    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    assert manifest["problem_id"] == "thin-film-broadband"
    assert len(manifest["artifacts"]) == len(DELIVERY_EXPECTED_ARTIFACTS)


def test_delivery_missing_artifact_warning(tmp_path):
    output_dir, run_manifest = _build_run(tmp_path, drop=("replay_record.json",))
    package = package_delivery(run_manifest, output_dir)
    assert package.warnings == [
        "DELIVERY_INCOMPLETE_WARNING: replay_record.json"
    ]


def test_delivery_certificate_not_certified(tmp_path):
    output_dir, run_manifest = _build_run(tmp_path, accepted=False)
    with pytest.raises(DeliveryCertificateError, match="CERTIFICATE_NOT_ACCEPTED_ERROR"):
        package_delivery(run_manifest, output_dir)


def test_delivery_certificate_missing(tmp_path):
    output_dir, run_manifest = _build_run(tmp_path, drop=(CERT_NAME,))
    with pytest.raises(DeliveryCertificateError, match="CERTIFICATE_NOT_ACCEPTED_ERROR"):
        package_delivery(run_manifest, output_dir)


def test_delivery_manifest_sha256(tmp_path):
    output_dir, run_manifest = _build_run(tmp_path)
    package = package_delivery(run_manifest, output_dir)
    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifacts"], "expected artifact entries"
    for entry in manifest["artifacts"]:
        assert set(entry) >= {"filename", "sha256", "size_bytes"}
        assert len(entry["sha256"]) == 64
        int(entry["sha256"], 16)
        assert entry["size_bytes"] > 0
    names = {entry["filename"] for entry in manifest["artifacts"]}
    assert names == set(DELIVERY_EXPECTED_ARTIFACTS)


def test_delivery_translation_skipped(tmp_path):
    # skipped flag + file genuinely absent -> no warning, excluded from manifest
    output_dir, run_manifest = _build_run(tmp_path, translation_skipped=True)
    package = package_delivery(run_manifest, output_dir)
    assert package.warnings == []
    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))
    names = {entry["filename"] for entry in manifest["artifacts"]}
    assert "article_zh.md" not in names
    assert len(names) == len(DELIVERY_EXPECTED_ARTIFACTS) - 1


def test_zip_packaging(tmp_path):
    output_dir, run_manifest = _build_run(tmp_path)
    package = package_delivery(run_manifest, output_dir, zip_output=True)
    assert package.zip_path is not None
    assert package.zip_path.name == "thin-film-broadband_run-20260823-01_delivery.zip"
    assert package.zip_path.is_file() and package.zip_path.stat().st_size > 0
