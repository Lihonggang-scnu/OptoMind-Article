"""Requirement-by-requirement Stage 17 acceptance audit for the Article Harness."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StageAcceptanceRow(_StrictModel):
    stage: str
    requirement: str
    status: Literal["passed", "partial", "failed", "missing"]
    evidence_path: str = ""
    detail: str


class ArticleStage17AcceptanceReport(_StrictModel):
    schema_version: Literal["article-stage17-acceptance.v1"] = (
        "article-stage17-acceptance.v1"
    )
    report_id: str
    status: Literal["accepted", "partial", "failed"]
    rows: List[StageAcceptanceRow] = Field(default_factory=list)
    blockers: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def _load(path_value: str) -> tuple[Path | None, Mapping[str, Any] | None, str]:
    if not str(path_value or "").strip():
        return None, None, "path not supplied"
    path = Path(path_value).resolve()
    if not path.is_file():
        return path, None, "file does not exist"
    if path.suffix.lower() != ".json":
        return path, {}, ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return path, None, f"invalid JSON: {exc}"
    return path, payload if isinstance(payload, Mapping) else None, "" if isinstance(payload, Mapping) else "JSON is not an object"


def _row(stage: str, requirement: str, status: str, path: Path | None, detail: str) -> StageAcceptanceRow:
    return StageAcceptanceRow(
        stage=stage,
        requirement=requirement,
        status=status,  # type: ignore[arg-type]
        evidence_path=str(path or ""),
        detail=detail,
    )


def audit_stage17_acceptance(
    artifacts: Mapping[str, str],
) -> ArticleStage17AcceptanceReport:
    rows: List[StageAcceptanceRow] = []
    blockers: List[str] = []
    warnings: List[str] = []

    def require_json(key: str, stage: str, requirement: str) -> tuple[Path | None, Mapping[str, Any] | None]:
        path, payload, error = _load(str(artifacts.get(key) or ""))
        if error:
            rows.append(_row(stage, requirement, "missing" if path is None or not path.is_file() else "failed", path, error))
            blockers.append(f"{stage}: {error}")
            return path, None
        return path, payload

    path, pipeline = require_json("pipeline_result", "1-8", "Natural-language planning through real TMM asset compilation")
    if pipeline is not None:
        executions = int(pipeline.get("execution_count") or 0)
        pipeline_status = str(pipeline.get("status") or "")
        status = "passed" if pipeline_status == "completed" and executions > 0 else "partial" if executions > 0 else "failed"
        rows.append(_row("1-8", "Natural-language planning through real TMM asset compilation", status, path, f"status={pipeline_status}, execution_count={executions}"))
        if status != "passed": warnings.append("Stages 1-8 are not fully completed")

    path, synthesis = require_json("synthesis", "9", "Claims and immutable Facts synthesized from TMM results")
    if synthesis is not None:
        ledger = synthesis.get("ledger") or {}
        claims = len(ledger.get("claims") or []) if isinstance(ledger, Mapping) else 0
        facts = len(ledger.get("facts") or []) if isinstance(ledger, Mapping) else 0
        status = "passed" if claims and facts and not synthesis.get("validation_errors") else "failed"
        rows.append(_row("9", "Claims and immutable Facts synthesized from TMM results", status, path, f"claims={claims}, facts={facts}"))

    path, architecture = require_json("architecture", "9", "Story architecture and Figure Contracts")
    if architecture is not None:
        stories = len(architecture.get("stories") or [])
        status = "passed" if stories and not architecture.get("validation_errors") else "failed"
        rows.append(_row("9", "Story architecture and Figure Contracts", status, path, f"stories={stories}"))

    path, writing = require_json("writing", "10", "Source-bound section writing")
    if writing is not None:
        sections = len(writing.get("sections") or [])
        status = "passed" if sections and writing.get("publishable") and not writing.get("errors") else "partial"
        rows.append(_row("10", "Source-bound section writing", status, path, f"sections={sections}, publishable={bool(writing.get('publishable'))}"))

    path, review = require_json("review", "11", "Scientific/expression review with bounded revision")
    if review is not None:
        hard = len(review.get("hard_blockers") or [])
        status = "passed" if hard == 0 and str(review.get("status") or "") != "blocked" else "failed"
        rows.append(_row("11", "Scientific/expression review with bounded revision", status, path, f"status={review.get('status')}, hard_blockers={hard}"))

    path, manuscript = require_json("manuscript", "12A", "Immutable manuscript body and source map")
    if manuscript is not None:
        body = manuscript.get("body") or {}
        sections = len(body.get("sections") or []) if isinstance(body, Mapping) else 0
        status = "passed" if sections and str(body.get("status") or "") == "assembled" and not manuscript.get("errors") else "failed"
        rows.append(_row("12A", "Immutable manuscript body and source map", status, path, f"sections={sections}, status={body.get('status') if isinstance(body, Mapping) else ''}"))

    path, repro = require_json("reproducibility", "12B", "Critical-experiment replay and artifact lineage")
    if repro is not None:
        critical = len(repro.get("critical_experiments") or [])
        replay = len(repro.get("replay_records") or [])
        hard = len(repro.get("blockers") or [])
        status = "passed" if critical and replay and hard == 0 else "failed"
        rows.append(_row("12B", "Critical-experiment replay and artifact lineage", status, path, f"critical={critical}, replay={replay}, blockers={hard}"))

    path, presentation = require_json("presentation", "12C", "Citations, references, tables, and figures")
    if presentation is not None:
        blockers_count = len(presentation.get("blockers") or [])
        citations = len(presentation.get("citations") or [])
        visuals = len(presentation.get("visuals") or [])
        status = "passed" if blockers_count == 0 and citations and visuals else "partial"
        rows.append(_row("12C", "Citations, references, tables, and figures", status, path, f"citations={citations}, visuals={visuals}, blockers={blockers_count}"))

    path, delivery = require_json("delivery", "12D", "LaTeX/PDF/arXiv delivery audit")
    if delivery is not None:
        hard = len(delivery.get("blockers") or []) + len(delivery.get("errors") or [])
        delivery_status = str(delivery.get("status") or "")
        status = "passed" if delivery_status == "submission_ready" and hard == 0 else "partial" if delivery_status == "compiled_awaiting_metadata" and hard == 0 else "failed"
        rows.append(_row("12D", "LaTeX/PDF/arXiv delivery audit", status, path, f"status={delivery_status}, blockers/errors={hard}"))
        if status == "partial": warnings.append("Delivery awaits final author metadata")

    path, audit = require_json("global_audit", "13", "Whole-Article source/scope/precision audit")
    if audit is not None:
        findings = len(audit.get("findings") or [])
        status = "passed" if findings == 0 and str(audit.get("status") or "") != "blocked" else "partial"
        rows.append(_row("13", "Whole-Article source/scope/precision audit", status, path, f"findings={findings}, status={audit.get('status')}"))

    path, registry = require_json("chapter_registry", "14", "Eight independent chapter packages registered")
    if registry is not None:
        registered = int(registry.get("registered_chapter_count") or 0)
        expected = int(registry.get("expected_chapter_count") or 8)
        missing = int(registry.get("missing_chapter_count") or max(0, expected - registered))
        status = "passed" if str(registry.get("status") or "") == "complete" and missing == 0 else "partial"
        rows.append(_row("14", "Eight independent chapter packages registered", status, path, f"registered={registered}/{expected}, missing={missing}"))
        if status != "passed": blockers.append(f"Stage 14: {missing} chapter packages are missing")

    path, full = require_json("full_structure", "15", "Full commander structure and gap output")
    if full is not None:
        errors_count = len(full.get("validation_errors") or [])
        status = "passed" if errors_count == 0 else "failed"
        rows.append(_row("15", "Full commander structure and gap output", status, path, f"sections={len(full.get('section_order') or [])}, validation_errors={errors_count}"))

    for key, requirement in (
        ("gap_compilation", "Bounded gap task compilation"),
        ("gap_query_plan", "Protocol-compatible gap query planning"),
        ("gap_queue", "Recoverable asynchronous gap queue"),
        ("article_memory_manifest", "Article-only memory boundary"),
    ):
        path, payload = require_json(key, "16", requirement)
        if payload is not None:
            status = "passed"
            if key == "article_memory_manifest" and payload.get("domain") != "article":
                status = "failed"
            rows.append(_row("16", requirement, status, path, f"schema={payload.get('schema_version', '')}"))

    pdf_path, _, pdf_error = _load(str(artifacts.get("final_pdf") or ""))
    if pdf_error:
        rows.append(_row("17", "Final PDF artifact exists", "missing" if pdf_path is None or not pdf_path.is_file() else "failed", pdf_path, pdf_error))
        blockers.append(f"Stage 17 PDF: {pdf_error}")
    else:
        rows.append(_row("17", "Final PDF artifact exists", "passed", pdf_path, f"bytes={pdf_path.stat().st_size}"))

    failed = any(row.status in {"failed", "missing"} for row in rows)
    partial = any(row.status == "partial" for row in rows)
    status: Literal["accepted", "partial", "failed"] = "failed" if failed else "partial" if partial or blockers else "accepted"
    payload = {"rows": [row.model_dump(mode="json") for row in rows], "blockers": blockers, "status": status}
    return ArticleStage17AcceptanceReport(
        report_id="stage17-acceptance-" + _digest(payload),
        status=status,
        rows=rows,
        blockers=list(dict.fromkeys(blockers)),
        warnings=list(dict.fromkeys(warnings)),
    )


__all__ = ["ArticleStage17AcceptanceReport", "StageAcceptanceRow", "audit_stage17_acceptance"]
