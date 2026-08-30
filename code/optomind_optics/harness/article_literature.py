"""Additive Article literature supplement contract.

The supplement is an append-only snapshot of the method-research report and
the derived director plan.  It never overwrites source files, experiment
observations, or trusted values.  Canonical evidence IDs live only in the
local supplement mapping; provider-facing contexts expose short aliases.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError


LITERATURE_SUPPLEMENT_SCHEMA_VERSION = "article-literature-supplement.v1"
LITERATURE_SUPPLEMENT_METADATA_FILENAME = "LITERATURE_SUPPLEMENT_METADATA.json"
MAX_SUPPLEMENT_EVIDENCE = 40
MAX_SUPPLEMENT_FINDINGS = 8
LITERATURE_EXCERPT_CHARS = 600


class _TolerantModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class LiteratureEvidence(_TolerantModel):
    alias: str
    evidence_id: str
    paper_id: str = ""
    title: str = ""
    content_depth: str = ""
    allowed_use: str = ""
    source_route: str = ""
    query_ids: List[str] = Field(default_factory=list)
    excerpt: str = ""


class LiteratureMethodFinding(_TolerantModel):
    design_family: str = ""
    method_name: str = ""
    reusable_principle: str = ""
    applicability: str = ""
    limitations: str = ""
    confidence: float = 0.5
    evidence_ids: List[str] = Field(default_factory=list)


class LiteratureHypothesis(_TolerantModel):
    hypothesis_id: str
    statement: str
    falsifiable_prediction: str = ""
    evidence_ids: List[str] = Field(default_factory=list)
    evidence_aliases: List[str] = Field(default_factory=list)
    route_kind: str = ""
    novelty_rationale: str = ""
    risk_notes: str = ""


class LiteratureEvidenceIdentity(_TolerantModel):
    evidence_id: str
    paper_id: str = ""
    doi: str = ""
    title: str = ""
    year: Optional[int] = None
    source_route: str = ""
    content_depth: str = ""
    allowed_use: str = ""
    text_sha256: str = ""


class LiteratureQueryTelemetry(_TolerantModel):
    status: str = ""
    query_count: int = 0
    evidence_count: int = 0
    method_finding_count: int = 0
    s2_calls: int = 0
    records_returned: int = 0
    online_budget_exhausted: bool = False
    reasons: List[str] = Field(default_factory=list)


class LiteratureQwenUsage(_TolerantModel):
    model_name: str = ""
    mock_llm: bool = False
    call_count: int = 0
    attempt_count: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_cny: float = 0.0


class LiteratureSupplementMetadata(_TolerantModel):
    schema_version: Literal["article-literature-supplement-metadata.v1"] = (
        "article-literature-supplement-metadata.v1"
    )
    source_pipeline_result_id: str
    old_director_plan_id: str
    report_identity: str
    new_plan_id: str
    report_sha256: str
    director_sha256: str


class LiteratureSupplement(_TolerantModel):
    schema_version: Literal["article-literature-supplement.v1"] = (
        "article-literature-supplement.v1"
    )
    source_pipeline_result_id: str
    old_director_plan_id: str = ""
    new_plan_id: str
    report_identity: str
    evidence_count: int
    method_findings: List[LiteratureMethodFinding] = Field(default_factory=list)
    new_plan_hypotheses: List[LiteratureHypothesis] = Field(
        default_factory=list
    )
    evidence_identity: List[LiteratureEvidenceIdentity] = Field(
        default_factory=list
    )
    research_influence: List[str] = Field(default_factory=list)
    unresolved_decisions: List[str] = Field(default_factory=list)
    evidence_aliases: Dict[str, str] = Field(default_factory=dict)
    evidence: List[LiteratureEvidence] = Field(default_factory=list)
    limits: List[str] = Field(default_factory=list)
    query_telemetry: LiteratureQueryTelemetry = Field(
        default_factory=LiteratureQueryTelemetry
    )
    usage: LiteratureQwenUsage = Field(default_factory=LiteratureQwenUsage)
    report_path: str = ""
    supplement_path: str = ""
    report_sha256: str = ""
    supplement_sha256: str = ""
    metadata_sha256: str = ""


class LiteratureSupplementIntegrityError(ValueError):
    """Supplement identity/path/hash validation failed closed."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_doi(doi: str) -> str:
    value = str(doi or "").strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value


def _validate_evidence_identity(
    entry: LiteratureEvidenceIdentity,
    method: Any,
) -> None:
    mismatches: List[str] = []
    if str(entry.paper_id or "") != str(method.paper_id or ""):
        mismatches.append("paper_id")
    if _normalize_doi(entry.doi) != _normalize_doi(method.doi):
        mismatches.append("doi")
    if str(entry.title or "").strip() != str(method.title or "").strip():
        mismatches.append("title")
    if (entry.year or None) != (method.year or None):
        mismatches.append("year")
    if str(entry.source_route or "") != str(method.source_route or ""):
        mismatches.append("source_route")
    if str(entry.content_depth or "") != str(method.content_depth.value):
        mismatches.append("content_depth")
    if str(entry.allowed_use or "") != str(method.allowed_use.value):
        mismatches.append("allowed_use")
    expected_text_sha = hashlib.sha256(
        str(method.text or "").encode("utf-8")
    ).hexdigest()
    if str(entry.text_sha256 or "") != expected_text_sha:
        mismatches.append("text_sha256")
    if mismatches:
        raise LiteratureSupplementIntegrityError(
            "director evidence identity mismatch for "
            f"{entry.evidence_id!r}: {', '.join(mismatches)}"
        )


def load_literature_supplement(
    report_path: str | Path,
    supplement_path: str | Path,
    *,
    sidecar_path: str | Path | None = None,
    expected_source_pipeline_result_id: str = "",
    expected_old_director_plan_id: str = "",
) -> LiteratureSupplement:
    """Load and validate the two persisted supplement assets."""

    report_file = Path(report_path)
    supplement_file = Path(supplement_path)
    for label, path in (
        ("method research report", report_file),
        ("director supplement", supplement_file),
    ):
        if not path.is_file():
            raise LiteratureSupplementIntegrityError(
                f"{label} is missing: {path}"
            )

    bound_validation = bool(
        expected_source_pipeline_result_id or expected_old_director_plan_id
    )
    metadata_file = (
        Path(sidecar_path)
        if sidecar_path is not None
        else supplement_file.parent / LITERATURE_SUPPLEMENT_METADATA_FILENAME
    )
    metadata: Optional[LiteratureSupplementMetadata] = None
    if bound_validation:
        if not metadata_file.is_file():
            raise LiteratureSupplementIntegrityError(
                "literature supplement metadata sidecar is missing: "
                f"{metadata_file}"
            )
        try:
            metadata = LiteratureSupplementMetadata.model_validate(
                json.loads(metadata_file.read_text(encoding="utf-8"))
            )
        except ValidationError as exc:
            raise LiteratureSupplementIntegrityError(
                f"literature supplement metadata is invalid: {exc}"
            ) from exc
    elif metadata_file.is_file():
        try:
            metadata = LiteratureSupplementMetadata.model_validate(
                json.loads(metadata_file.read_text(encoding="utf-8"))
            )
        except ValidationError:
            metadata = None

    from optomind_optics.harness.article_director import ArticleDirectorResult
    from optomind_optics.harness.method_research import MethodResearchReport

    try:
        report = MethodResearchReport.model_validate(
            json.loads(report_file.read_text(encoding="utf-8"))
        )
    except ValidationError as exc:
        raise LiteratureSupplementIntegrityError(
            f"method research report is invalid: {exc}"
        ) from exc
    try:
        director = ArticleDirectorResult.model_validate(
            json.loads(supplement_file.read_text(encoding="utf-8"))
        )
    except ValidationError as exc:
        raise LiteratureSupplementIntegrityError(
            f"director supplement is invalid: {exc}"
        ) from exc

    if director.status != "planned" or director.plan is None:
        raise LiteratureSupplementIntegrityError(
            "director supplement must be a planned result with a plan"
        )
    problem_id = str(report.problem_id or "").strip()
    if not problem_id:
        raise LiteratureSupplementIntegrityError(
            "method research report has no problem_id"
        )
    evidence_ids = [str(item.evidence_id or "") for item in report.evidence]
    if any(not item for item in evidence_ids):
        raise LiteratureSupplementIntegrityError(
            "method research report contains an evidence record without "
            "evidence_id"
        )
    if len(set(evidence_ids)) != len(evidence_ids):
        raise LiteratureSupplementIntegrityError(
            "method research report contains duplicate evidence IDs"
        )
    known_evidence = set(evidence_ids)
    for finding in report.method_findings:
        unknown = sorted(
            set(finding.evidence_ids or ()) - known_evidence
        )
        if unknown:
            raise LiteratureSupplementIntegrityError(
                f"method finding cites unknown evidence ids: {unknown}"
            )

    evidence = []
    aliases: Dict[str, str] = {}
    for index, item in enumerate(
        report.evidence[:MAX_SUPPLEMENT_EVIDENCE],
        start=1,
    ):
        alias = f"E{index:02d}"
        aliases[alias] = item.evidence_id
        evidence.append(
            LiteratureEvidence(
                alias=alias,
                evidence_id=item.evidence_id,
                paper_id=str(item.paper_id or ""),
                title=str(item.title or ""),
                content_depth=str(item.content_depth.value),
                allowed_use=str(item.allowed_use.value),
                source_route=str(item.source_route or ""),
                query_ids=list(item.query_ids or ()),
                excerpt=str(item.text or "")[:LITERATURE_EXCERPT_CHARS],
            )
        )
    findings = [
        LiteratureMethodFinding(
            design_family=str(item.design_family or ""),
            method_name=str(item.method_name or ""),
            reusable_principle=str(item.reusable_principle or ""),
            applicability=str(item.applicability or ""),
            limitations=str(item.limitations or ""),
            confidence=float(item.confidence),
            evidence_ids=list(item.evidence_ids or ()),
        )
        for item in report.method_findings[:MAX_SUPPLEMENT_FINDINGS]
    ]
    evidence_to_alias = {
        evidence_id: alias
        for alias, evidence_id in aliases.items()
    }
    hypotheses = [
        LiteratureHypothesis(
            hypothesis_id=item.hypothesis_id,
            statement=str(item.statement or ""),
            falsifiable_prediction=str(item.falsifiable_prediction or ""),
            evidence_ids=list(item.evidence_ids or ()),
            evidence_aliases=[
                evidence_to_alias[evidence_id]
                for evidence_id in item.evidence_ids
                if evidence_id in evidence_to_alias
            ],
            route_kind=str(item.route_kind or ""),
            novelty_rationale=str(item.novelty_rationale or ""),
            risk_notes=str(item.risk_notes or ""),
        )
        for item in director.plan.hypotheses
    ]
    evidence_identity = [
        LiteratureEvidenceIdentity(
            evidence_id=str(entry.evidence_id or ""),
            paper_id=str(entry.paper_id or ""),
            doi=str(entry.doi or ""),
            title=str(entry.title or ""),
            year=entry.year,
            source_route=str(entry.source_route or ""),
            content_depth=str(entry.content_depth or ""),
            allowed_use=str(entry.allowed_use or ""),
            text_sha256=str(entry.text_sha256 or ""),
        )
        for entry in director.plan.evidence_identity
    ]
    seen_manifest_ids: set[str] = set()
    report_by_id = {item.evidence_id: item for item in report.evidence}
    for entry in evidence_identity:
        if entry.evidence_id in seen_manifest_ids:
            raise LiteratureSupplementIntegrityError(
                "director evidence identity manifest contains duplicate "
                f"evidence_id {entry.evidence_id!r}"
            )
        seen_manifest_ids.add(entry.evidence_id)
        method = report_by_id.get(entry.evidence_id)
        if method is None:
            raise LiteratureSupplementIntegrityError(
                "director evidence identity references evidence missing from "
                f"the method report: {entry.evidence_id!r}"
            )
        _validate_evidence_identity(entry, method)
    usage = dict(director.usage or {})
    telemetry = report.telemetry
    allowed_uses = sorted(
        {item.allowed_use.value for item in report.evidence}
    )
    limits = [
        "literature evidence is supplementary context only and never an "
        "experimental numeric value",
        "canonical evidence IDs are resolved locally; providers see aliases",
    ]
    if allowed_uses:
        limits.append("allowed literature uses: " + ", ".join(allowed_uses))
    supplement = LiteratureSupplement(
        source_pipeline_result_id=str(expected_source_pipeline_result_id or ""),
        old_director_plan_id=str(expected_old_director_plan_id or ""),
        new_plan_id=director.plan.plan_id,
        report_identity=problem_id,
        evidence_count=len(evidence),
        method_findings=findings,
        new_plan_hypotheses=hypotheses,
        evidence_identity=evidence_identity,
        research_influence=list(director.plan.research_influence or ()),
        unresolved_decisions=list(director.plan.unresolved_decisions or ()),
        evidence_aliases=aliases,
        evidence=evidence,
        limits=limits,
        query_telemetry=LiteratureQueryTelemetry(
            status=str(report.status.value),
            query_count=len(report.queries),
            evidence_count=len(report.evidence),
            method_finding_count=len(report.method_findings),
            s2_calls=int(telemetry.s2_calls or 0),
            records_returned=int(telemetry.records_returned or 0),
            online_budget_exhausted=bool(
                getattr(telemetry, "online_budget_exhausted", False)
            ),
            reasons=list(report.reasons or ()),
        ),
        usage=LiteratureQwenUsage(
            model_name=str(usage.get("model_name") or ""),
            mock_llm=bool(usage.get("mock_llm")),
            call_count=int(usage.get("call_count") or 0),
            attempt_count=len(usage.get("attempts") or ()),
            estimated_input_tokens=int(
                usage.get("estimated_input_tokens") or 0
            ),
            estimated_output_tokens=int(
                usage.get("estimated_output_tokens") or 0
            ),
            estimated_cost_cny=float(
                usage.get("estimated_cost_cny") or 0.0
            ),
        ),
        report_path=str(report_file.resolve()),
        supplement_path=str(supplement_file.resolve()),
        report_sha256=_sha256_file(report_file),
        supplement_sha256=_sha256_file(supplement_file),
    )
    report_sha256 = _sha256_file(report_file)
    director_sha256 = _sha256_file(supplement_file)
    if metadata is not None:
        if metadata.report_identity != problem_id:
            raise LiteratureSupplementIntegrityError(
                "literature supplement metadata report_identity does not "
                "match the method research report"
            )
        if metadata.new_plan_id != director.plan.plan_id:
            raise LiteratureSupplementIntegrityError(
                "literature supplement metadata new_plan_id does not match "
                "the director plan"
            )
        if metadata.report_sha256 != report_sha256:
            raise LiteratureSupplementIntegrityError(
                "literature supplement metadata report_sha256 does not match "
                "the on-disk method research report"
            )
        if metadata.director_sha256 != director_sha256:
            raise LiteratureSupplementIntegrityError(
                "literature supplement metadata director_sha256 does not "
                "match the on-disk director supplement"
            )
        if (
            expected_source_pipeline_result_id
            and metadata.source_pipeline_result_id
            != expected_source_pipeline_result_id
        ):
            raise LiteratureSupplementIntegrityError(
                "literature supplement metadata source_pipeline_result_id "
                "does not match the source pipeline"
            )
        if (
            expected_old_director_plan_id
            and metadata.old_director_plan_id
            != expected_old_director_plan_id
        ):
            raise LiteratureSupplementIntegrityError(
                "literature supplement metadata old_director_plan_id does "
                "not match the source director plan"
            )
        supplement = supplement.model_copy(
            update={
                "source_pipeline_result_id": metadata.source_pipeline_result_id,
                "old_director_plan_id": metadata.old_director_plan_id,
                "report_sha256": metadata.report_sha256,
                "supplement_sha256": metadata.director_sha256,
                "metadata_sha256": _sha256_file(metadata_file),
            }
        )
    if (
        expected_old_director_plan_id
        and supplement.new_plan_id == expected_old_director_plan_id
    ):
        raise LiteratureSupplementIntegrityError(
            "supplement plan_id must be a derived plan distinct from the "
            "old director plan"
        )
    return supplement


def build_literature_provider_context(
    supplement: LiteratureSupplement,
    *,
    max_evidence: int = MAX_SUPPLEMENT_EVIDENCE,
    max_findings: int = MAX_SUPPLEMENT_FINDINGS,
) -> Dict[str, Any]:
    """Bounded provider context; canonical IDs stay in the local mapping."""

    evidence_to_alias = {
        evidence_id: alias
        for alias, evidence_id in supplement.evidence_aliases.items()
    }
    findings = []
    for finding in supplement.method_findings[:max_findings]:
        findings.append(
            {
                "design_family": finding.design_family,
                "method_name": finding.method_name,
                "reusable_principle": finding.reusable_principle,
                "applicability": finding.applicability,
                "limitations": finding.limitations,
                "evidence_aliases": [
                    evidence_to_alias[evidence_id]
                    for evidence_id in finding.evidence_ids
                    if evidence_id in evidence_to_alias
                ],
            }
        )
    return {
        "old_director_plan_id": supplement.old_director_plan_id,
        "new_director_plan_id": supplement.new_plan_id,
        "report_identity": supplement.report_identity,
        "method_findings": findings,
        "new_plan_hypotheses": [
            {
                "hypothesis_id": item.hypothesis_id,
                "statement": item.statement,
                "falsifiable_prediction": item.falsifiable_prediction,
                "evidence_aliases": list(item.evidence_aliases),
                "route_kind": item.route_kind,
                "novelty_rationale": item.novelty_rationale,
                "risk_notes": item.risk_notes,
            }
            for item in supplement.new_plan_hypotheses
        ],
        "research_influence": list(supplement.research_influence),
        "unresolved_decisions": list(supplement.unresolved_decisions),
        "evidence_limits": list(supplement.limits),
        "evidence": [
            {
                "alias": item.alias,
                "paper_id": item.paper_id,
                "title": item.title,
                "content_depth": item.content_depth,
                "allowed_use": item.allowed_use,
                "source_route": item.source_route,
                "excerpt": item.excerpt,
            }
            for item in supplement.evidence[:max_evidence]
        ],
    }
