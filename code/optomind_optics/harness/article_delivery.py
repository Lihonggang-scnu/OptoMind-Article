"""Stage 12D: Article-specific publication/delivery adapter.

Consumes the accepted Stage 12C presentation package and the complete
upstream chain, then invokes the existing read-only
``optomind_research.runtime.latex_publication_renderer.build_latex_publication``
through dependency injection.  Stage 12D makes no Qwen/model/network call:
all fixed contracts, renderer inputs, statuses, hashes, costs, and audit
records are built locally.  Ordinary advisory issues fail open; source,
citation, numeric, identity, hash, path, and submission-integrity violations
fail closed.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from optomind_optics.harness.article_architecture import ArticleArchitectureResult
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_manuscript import ArticleManuscriptPackage
from optomind_optics.harness.article_presentation import (
    ArticlePresentationPackage,
    CitationPlacement,
    PanelAsset,
    ReferenceRecord,
    RenderedVisual,
    _render_reader_paragraph,
    validate_presentation_package,
)
from optomind_optics.harness.article_reproducibility import (
    ArticleReproducibilityPackage,
)
from optomind_optics.harness.article_review import ArticleReviewResult
from optomind_optics.harness.article_writing import TrustedValueRecord
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny

MODEL_NAME = "qwen3.7-flash"
DELIVERY_MODEL_NAME = "none"
SAFE_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf"}
_HEX64_RE = re.compile(r"[0-9a-f]{64}")
_MAX_PANEL_PAYLOAD = 100 * 1024 * 1024
_MAX_SIDE = 4096
_MAX_COMPOSITE_PIXELS = 40_000_000
_MAX_PANELS_PER_FIGURE = 100
_SVG_RENDER_SCALE = 2.0


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArticleDeliveryIntegrityError(ValueError):
    """Upstream identity, renderer integrity, or persistence conflict."""


class PublicationAuthor(_StrictModel):
    schema_version: Literal["publication-author.v1"] = "publication-author.v1"
    name: str
    affiliations: List[str] = Field(default_factory=list)
    email: str = ""
    orcid: str = ""
    corresponding: bool = False

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("author name must be non-empty")
        return value


class PublicationMetadata(_StrictModel):
    """Caller-supplied author/delivery metadata; title/abstract/keywords are
    authoritative from the Stage 12C front matter and are never replaced."""

    schema_version: Literal["publication-metadata.v1"] = "publication-metadata.v1"
    authors: List[PublicationAuthor] = Field(default_factory=list)
    date: str = ""
    acknowledgements: str = ""
    draft: bool = False


class AdditionalUsageRow(_StrictModel):
    schema_version: Literal["additional-usage-row.v1"] = "additional-usage-row.v1"
    label: str
    usage: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("label")
    @classmethod
    def _non_empty_label(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("usage row label must be non-empty")
        return value


class DeliveryCostRow(_StrictModel):
    schema_version: Literal["delivery-cost-row.v1"] = "delivery-cost-row.v1"
    row_id: str
    stage_label: str
    source: Literal["upstream", "caller"]
    model_name: str = ""
    mock_llm: bool = False
    call_count: int = 0
    attempts: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_cny: float = 0.0
    cost_estimated_locally: bool = False
    notes: str = ""


class DeliveryCostTotals(_StrictModel):
    schema_version: Literal["delivery-cost-totals.v1"] = "delivery-cost-totals.v1"
    call_count: int = 0
    attempts: int = 0
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    estimated_cost_cny: float = 0.0


class DeliveryCostLedger(_StrictModel):
    schema_version: Literal["delivery-cost-ledger.v1"] = "delivery-cost-ledger.v1"
    ledger_id: str
    rows: List[DeliveryCostRow] = Field(default_factory=list)
    totals: DeliveryCostTotals
    coverage_missing: List[str] = Field(default_factory=list)
    total_cost_complete: bool = False


class DeliveryArtifactRecord(_StrictModel):
    schema_version: Literal["delivery-artifact-record.v1"] = (
        "delivery-artifact-record.v1"
    )
    artifact_id: str
    relative_path: str
    kind: str
    role: Literal["final", "audit"]
    bytes_count: int
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _full_hex_sha256(cls, value: str) -> str:
        if not _HEX64_RE.fullmatch(str(value or "")):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        return value

    @field_validator("relative_path")
    @classmethod
    def _safe_relative_path(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text or "\x00" in text:
            raise ValueError("relative_path must be non-empty without NUL")
        path = Path(text)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("relative_path must be relative and safe")
        return text


class DeliveryBlocker(_StrictModel):
    schema_version: Literal["delivery-blocker.v1"] = "delivery-blocker.v1"
    blocker_id: str
    kind: str
    message: str
    upstream_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)


class DeliveryFinding(_StrictModel):
    schema_version: Literal["delivery-finding.v1"] = "delivery-finding.v1"
    finding_id: str
    kind: str
    message: str


class DeliveryReferenceRecord(_StrictModel):
    schema_version: Literal["delivery-reference-record.v1"] = (
        "delivery-reference-record.v1"
    )
    reference_alias: str
    citation_key: str
    paper_ids: List[str] = Field(default_factory=list)
    doi: str = ""
    title: str
    year: Optional[int] = None
    authors: List[str] = Field(default_factory=list)
    venue: str = ""
    url: str = ""
    evidence_ids: List[str] = Field(default_factory=list)
    metadata_complete: bool
    metadata_incomplete_fields: List[str] = Field(default_factory=list)


class DeliveryPanelRecord(_StrictModel):
    schema_version: Literal["delivery-panel-record.v1"] = "delivery-panel-record.v1"
    label: str
    relative_path: str
    media_type: str
    encoding: str
    bytes_count: int
    sha256: str

    @field_validator("sha256")
    @classmethod
    def _full_hex_sha256(cls, value: str) -> str:
        if not _HEX64_RE.fullmatch(str(value or "")):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        return value


class DeliveryVisualRecord(_StrictModel):
    schema_version: Literal["delivery-visual-record.v1"] = "delivery-visual-record.v1"
    visual_id: str
    contract_figure_id: str
    kind: Literal["figure", "table"]
    section_id: str
    figure_number: int
    after_paragraph_id: str
    panels: List[DeliveryPanelRecord] = Field(default_factory=list)
    caption: str
    source_mode: str
    provenance: str
    representable: bool
    block_reason: str = ""
    renderer_asset_path: str = ""
    renderer_media_type: str = ""
    renderer_bytes: int = 0
    renderer_sha256: str = ""
    composition: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("renderer_sha256")
    @classmethod
    def _renderer_sha256(cls, value: str) -> str:
        if value and not _HEX64_RE.fullmatch(str(value)):
            raise ValueError(
                "renderer_sha256 must be empty or a 64-character hex digest"
            )
        return value

    @field_validator("renderer_asset_path")
    @classmethod
    def _safe_renderer_asset_path(cls, value: str) -> str:
        if not value:
            return value
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or "\x00" in value:
            raise ValueError("renderer_asset_path must be relative and safe")
        return value


class ArticleDeliveryPackage(_StrictModel):
    schema_version: Literal["article-delivery-package.v1"] = (
        "article-delivery-package.v1"
    )
    package_id: str
    plan_id: str
    ledger_id: str
    architecture_id: str
    review_id: str
    result_id: str
    manuscript_body_id: str
    reproducibility_package_id: str
    presentation_package_id: str
    story_id: str
    status: Literal[
        "submission_ready",
        "compiled_awaiting_metadata",
        "failed",
        "blocked",
    ]
    renderer_name: str
    renderer_status: str = ""
    renderer_invoked: bool = False
    renderer_report_digest: str = ""
    renderer_attempts: int = 0
    compile_pdf: bool
    publication_metadata: PublicationMetadata
    author_metadata_complete: bool
    reference_metadata_complete: bool
    references: List[DeliveryReferenceRecord] = Field(default_factory=list)
    visuals: List[DeliveryVisualRecord] = Field(default_factory=list)
    artifacts: List[DeliveryArtifactRecord] = Field(default_factory=list)
    blockers: List[DeliveryBlocker] = Field(default_factory=list)
    findings: List[DeliveryFinding] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    cost: DeliveryCostLedger
    body_sha256: str
    citation_count: int
    reference_count: int
    figure_count: int
    table_count: int
    tool_availability: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("package_id", "body_sha256")
    @classmethod
    def _hex_fields(cls, value: str) -> str:
        if not _HEX64_RE.fullmatch(str(value or "")):
            raise ValueError("expected a 64-character lowercase hex digest")
        return value


LatexRenderer = Callable[..., Dict[str, Any]]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(*parts: Any) -> str:
    return hashlib.sha256(
        _canonical_json([str(part) for part in parts]).encode("utf-8")
    ).hexdigest()[:16]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_optional_model(
    value: Any,
    model_type: Any,
    label: str,
    errors: List[str],
) -> Optional[Any]:
    if value is None:
        return None
    if isinstance(value, model_type):
        return value
    try:
        return model_type.model_validate(value)
    except ValidationError as exc:
        errors.append(f"{label} is invalid: {exc}")
        return None


def _normalize_records(
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    errors: List[str],
) -> List[TrustedValueRecord]:
    records: List[TrustedValueRecord] = []
    for index, raw in enumerate(value_records):
        try:
            records.append(
                raw
                if isinstance(raw, TrustedValueRecord)
                else TrustedValueRecord.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"value_records[{index}] is invalid: {exc}")
    return records


def _citation_key(alias: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(alias or "").lower()).strip("_")
    digest = hashlib.sha1(str(alias).encode("utf-8")).hexdigest()[:8]
    return f"{(value[:64] or 'reference')}_{digest}"


def _validate_usage_numbers(
    usage: Mapping[str, Any],
    label: str,
    errors: List[str],
) -> Dict[str, Any]:
    numeric_keys = (
        "estimated_input_tokens",
        "estimated_output_tokens",
        "call_count",
        "attempts",
        "estimated_cost_cny",
    )
    values: Dict[str, Any] = {}
    for key in numeric_keys:
        raw = usage.get(key)
        if raw is None:
            values[key] = 0
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            errors.append(f"usage row {label!r} has non-numeric {key!r}")
            values[key] = 0
            continue
        number = float(raw)
        if not math.isfinite(number) or number < 0:
            errors.append(f"usage row {label!r} has negative or non-finite {key!r}")
            values[key] = 0
            continue
        values[key] = int(raw) if key != "estimated_cost_cny" else round(number, 6)
    return values


def _estimate_cost(usage: Dict[str, Any], model_name: str) -> Tuple[float, bool]:
    cost = float(usage.get("estimated_cost_cny") or 0.0)
    if cost > 0:
        return round(cost, 6), False
    input_tokens = int(usage.get("estimated_input_tokens") or 0)
    output_tokens = int(usage.get("estimated_output_tokens") or 0)
    if input_tokens or output_tokens:
        return (
            round(
                estimate_call_cost_cny(
                    model_name or MODEL_NAME,
                    max(0, input_tokens),
                    max(0, output_tokens),
                ),
                6,
            ),
            True,
        )
    return 0.0, False


def _cost_row_from_usage(
    label: str,
    usage: Mapping[str, Any],
    *,
    source: Literal["upstream", "caller"],
    errors: List[str],
    fallback_model: str = "",
) -> DeliveryCostRow:
    values = _validate_usage_numbers(usage, label, errors)
    model_name = str(usage.get("model_name") or fallback_model or "")
    cost, estimated = _estimate_cost(values, model_name)
    notes = "cost estimated locally from token telemetry" if estimated else ""
    return DeliveryCostRow(
        row_id=f"cost-{_digest(label, _canonical_json(dict(usage)))}",
        stage_label=label,
        source=source,
        model_name=model_name,
        mock_llm=bool(usage.get("mock_llm")),
        call_count=int(values["call_count"]),
        attempts=int(values["attempts"]),
        estimated_input_tokens=int(values["estimated_input_tokens"]),
        estimated_output_tokens=int(values["estimated_output_tokens"]),
        estimated_cost_cny=round(float(values["estimated_cost_cny"]) or cost, 6),
        cost_estimated_locally=estimated,
        notes=notes,
    )


def _cost_ledger_id(
    rows: Sequence[DeliveryCostRow],
    totals: DeliveryCostTotals,
    coverage_missing: Sequence[str],
) -> str:
    return "cost-" + _digest(
        _canonical_json([row.model_dump(mode="json") for row in rows]),
        totals.model_dump(mode="json"),
        list(coverage_missing),
    )


def _build_cost_ledger(
    *,
    plan: ArticleDirectorPlan,
    architecture: ArticleArchitectureResult,
    review: ArticleReviewResult,
    presentation: ArticlePresentationPackage,
    reproducibility: ArticleReproducibilityPackage,
    additional_usage: Sequence[AdditionalUsageRow],
    errors: List[str],
    findings: List[DeliveryFinding],
) -> Optional[DeliveryCostLedger]:
    rows: List[DeliveryCostRow] = []
    used_labels: set[str] = set()

    def add(
        label: str,
        usage: Mapping[str, Any],
        source: str,
        fallback_model: str = "",
    ) -> None:
        if label in used_labels:
            errors.append(f"duplicate cost row label {label!r}")
            return
        if usage:
            rows.append(
                _cost_row_from_usage(
                    label,
                    usage,
                    source=("upstream" if source == "upstream" else "caller"),
                    errors=errors,
                    fallback_model=fallback_model,
                )
            )
            used_labels.add(label)

    builtin_labels = [
        "director_plan",
        "architecture",
        "review",
        "presentation",
    ]
    builtin_labels.extend(
        f"writing_section_{section.section_id}" for section in review.sections
    )

    add(
        "director_plan",
        dict(getattr(plan, "usage", None) or {}),
        "upstream",
        fallback_model=str(getattr(plan, "model_name", "") or ""),
    )
    add(
        "architecture",
        architecture.usage,
        "upstream",
        fallback_model=str(architecture.semantic_model or ""),
    )
    for section in review.sections:
        add(
            f"writing_section_{section.section_id}",
            section.original_section_draft.usage,
            "upstream",
            fallback_model=str(section.original_section_draft.semantic_model or ""),
        )
    add(
        "review",
        review.usage,
        "upstream",
        fallback_model=str(review.semantic_model or ""),
    )
    add(
        "presentation",
        presentation.usage,
        "upstream",
        fallback_model=str(presentation.model_name or ""),
    )
    add(
        "reproducibility",
        reproducibility.usage,
        "upstream",
        fallback_model=str(reproducibility.model_name or ""),
    )

    caller_labels: set[str] = set()
    for index, row in enumerate(additional_usage):
        if row.label in used_labels:
            errors.append(f"additional usage row {index} duplicates {row.label!r}")
            continue
        if row.label in caller_labels:
            errors.append(f"duplicate additional usage row {row.label!r}")
            continue
        caller_labels.add(row.label)
        used_labels.add(row.label)
        rows.append(
            _cost_row_from_usage(
                row.label,
                row.usage,
                source="caller",
                errors=errors,
            )
        )

    coverage_missing = [
        label
        for label in builtin_labels
        if label not in {row.stage_label for row in rows}
    ]
    if "director_plan" in coverage_missing:
        findings.append(
            DeliveryFinding(
                finding_id=f"finding-{_digest('cost_coverage', 'director_plan')}",
                kind="cost_coverage_gap",
                message=(
                    "director plan usage telemetry is unavailable on the "
                    "supplied plan; full cost coverage requires a plan that "
                    "carries usage or one caller row labeled 'director_plan'"
                ),
            )
        )
    totals = DeliveryCostTotals(
        call_count=sum(row.call_count for row in rows),
        attempts=sum(row.attempts for row in rows),
        estimated_input_tokens=sum(row.estimated_input_tokens for row in rows),
        estimated_output_tokens=sum(row.estimated_output_tokens for row in rows),
        estimated_cost_cny=round(sum(row.estimated_cost_cny for row in rows), 6),
    )
    ledger = DeliveryCostLedger(
        ledger_id=_cost_ledger_id(rows, totals, coverage_missing),
        rows=rows,
        totals=totals,
        coverage_missing=coverage_missing,
        total_cost_complete=not coverage_missing,
    )
    return ledger


def _reference_completeness(
    reference: ReferenceRecord,
) -> Tuple[bool, List[str]]:
    missing: List[str] = []
    if not str(reference.title or "").strip():
        missing.append("title")
    if not reference.authors:
        missing.append("authors")
    if not reference.year:
        missing.append("year")
    url = str(reference.url or "").strip()
    stable_url = (
        bool(url)
        and not any(char in url for char in ("\n", "\r", " ", "\x00"))
        and (url.startswith("https://") or url.startswith("http://"))
    )
    if (
        not str(reference.doi or "").strip()
        and not str(reference.venue or "").strip()
        and not stable_url
    ):
        missing.append("doi_or_venue_or_url")
    return not missing, missing


def _build_reference_records(
    references: Sequence[ReferenceRecord],
    errors: List[str],
    findings: List[DeliveryFinding],
    blockers: List[DeliveryBlocker],
) -> Tuple[List[DeliveryReferenceRecord], bool]:
    records: List[DeliveryReferenceRecord] = []
    aliases: set[str] = set()
    complete = True
    for reference in references:
        if reference.reference_alias in aliases:
            errors.append(f"duplicate reference alias {reference.reference_alias!r}")
            continue
        aliases.add(reference.reference_alias)
        ok, missing = _reference_completeness(reference)
        if not ok:
            complete = False
            blockers.append(
                DeliveryBlocker(
                    blocker_id=f"blocker-{_digest('incomplete_reference', reference.reference_alias)}",
                    kind="incomplete_bibliographic_metadata",
                    message=(
                        f"reference {reference.reference_alias!r} is missing "
                        + ", ".join(missing)
                    ),
                    upstream_ids=[reference.reference_alias],
                )
            )
        records.append(
            DeliveryReferenceRecord(
                reference_alias=reference.reference_alias,
                citation_key=_citation_key(reference.reference_alias),
                paper_ids=list(reference.paper_ids),
                doi=reference.doi,
                title=reference.title,
                year=reference.year,
                authors=list(reference.authors),
                venue=reference.venue,
                url=reference.url,
                evidence_ids=list(reference.evidence_ids),
                metadata_complete=ok,
                metadata_incomplete_fields=missing,
            )
        )
        if not ok:
            findings.append(
                DeliveryFinding(
                    finding_id=f"finding-{_digest('incomplete_reference', reference.reference_alias)}",
                    kind="incomplete_bibliographic_metadata",
                    message=(
                        f"reference {reference.reference_alias!r} has "
                        "incomplete bibliographic metadata"
                    ),
                )
            )
    return records, complete


def _panel_bytes(panel: PanelAsset) -> bytes:
    if panel.encoding == "utf-8":
        return panel.asset_content.encode("utf-8")
    return base64.b64decode(panel.asset_bytes_b64)


def _load_fitz():
    """Optional PyMuPDF import; returns None when unavailable."""

    try:
        import fitz  # type: ignore

        return fitz
    except Exception:
        return None


def _load_pil():
    """Optional Pillow import; returns None when unavailable."""

    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore

        return Image, ImageDraw, ImageFont
    except Exception:
        return None


def _decode_panel_image(
    panel: PanelAsset,
    errors: List[str],
) -> Optional[Tuple[Any, str]]:
    """Decode one panel into a white-background RGB Pillow image.

    Returns ``(image, original_suffix)`` or None after recording an error.
    Bounds: payload <= 100 MiB, rasterized side <= 4096 px, pixel count
    <= 40 million.  Decompression-bomb and parse failures fail closed.
    """

    payload = _panel_bytes(panel)
    if _sha256_bytes(payload) != panel.sha256:
        errors.append(f"panel {panel.asset_path!r} hash mismatch")
        return None
    if len(payload) == 0:
        errors.append(f"panel {panel.asset_path!r} is empty")
        return None
    if len(payload) > _MAX_PANEL_PAYLOAD:
        errors.append(f"panel {panel.asset_path!r} payload exceeds 100 MiB bound")
        return None
    suffix = Path(panel.asset_path).suffix.lower()
    pil = _load_pil()
    if pil is None:
        errors.append(
            f"panel {panel.asset_path!r} requires Pillow which is unavailable"
        )
        return None
    Image, ImageDraw, ImageFont = pil  # noqa: F841

    def geometry_ok(width: float, height: float) -> bool:
        if (
            not math.isfinite(width)
            or not math.isfinite(height)
            or width <= 0
            or height <= 0
        ):
            errors.append(
                f"panel {panel.asset_path!r} has zero or non-finite " "dimensions"
            )
            return False
        raster_width = math.ceil(width)
        raster_height = math.ceil(height)
        if raster_width > _MAX_SIDE or raster_height > _MAX_SIDE:
            errors.append(
                f"panel {panel.asset_path!r} rasterized side exceeds " "4096 px bound"
            )
            return False
        if raster_width * raster_height > _MAX_COMPOSITE_PIXELS:
            errors.append(
                f"panel {panel.asset_path!r} rasterized pixel count exceeds "
                "40M bound"
            )
            return False
        return True

    image: Optional[Any] = None
    if suffix == ".svg":
        fitz = _load_fitz()
        if fitz is None:
            errors.append(
                f"panel {panel.asset_path!r} requires PyMuPDF for SVG " "conversion"
            )
            return None
        document = None
        try:
            document = fitz.open(stream=payload, filetype="svg")
            page = document.load_page(0)
            rect = page.rect
            if not geometry_ok(
                rect.width * _SVG_RENDER_SCALE,
                rect.height * _SVG_RENDER_SCALE,
            ):
                return None
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(_SVG_RENDER_SCALE, _SVG_RENDER_SCALE),
                alpha=False,
            )
            image = Image.frombytes(
                "RGB",
                (pixmap.width, pixmap.height),
                pixmap.samples,
            )
        except MemoryError:
            raise
        except Exception as exc:
            errors.append(f"panel {panel.asset_path!r} SVG conversion failed: {exc}")
            return None
        finally:
            if document is not None:
                document.close()
    elif suffix == ".pdf":
        fitz = _load_fitz()
        if fitz is None:
            errors.append(
                f"panel {panel.asset_path!r} requires PyMuPDF for PDF " "rendering"
            )
            return None
        document = None
        try:
            document = fitz.open(stream=payload, filetype="pdf")
            page = document.load_page(0)
            rect = page.rect
            if not geometry_ok(
                rect.width * _SVG_RENDER_SCALE,
                rect.height * _SVG_RENDER_SCALE,
            ):
                return None
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(_SVG_RENDER_SCALE, _SVG_RENDER_SCALE),
                alpha=False,
            )
            image = Image.frombytes(
                "RGB",
                (pixmap.width, pixmap.height),
                pixmap.samples,
            )
        except MemoryError:
            raise
        except Exception as exc:
            errors.append(f"panel {panel.asset_path!r} PDF rendering failed: {exc}")
            return None
        finally:
            if document is not None:
                document.close()
    elif suffix in {".png", ".jpg", ".jpeg"}:
        try:
            with Image.open(io.BytesIO(payload)) as handle:
                if not geometry_ok(handle.width, handle.height):
                    return None
                with warnings.catch_warnings():
                    warnings.simplefilter(
                        "error",
                        Image.DecompressionBombWarning,
                    )
                    handle.load()
                image = handle.convert("RGBA")
        except MemoryError:
            raise
        except Exception as exc:
            errors.append(f"panel {panel.asset_path!r} raster decode failed: {exc}")
            return None
    else:
        errors.append(f"panel {panel.asset_path!r} unsupported suffix {suffix!r}")
        return None

    if image is None or image.width <= 0 or image.height <= 0:
        errors.append(f"panel {panel.asset_path!r} has zero dimensions")
        return None
    background = Image.new("RGB", (image.width, image.height), (255, 255, 255))
    try:
        if image.mode == "RGBA":
            background.paste(image, (0, 0), image)
        else:
            background.paste(image, (0, 0))
    finally:
        if image is not None and image is not background:
            image.close()
    return background, suffix


def _encode_png(image: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=6)
    return buffer.getvalue()


def _shrink_to_pixel_budget(image: Any, pixel_budget: int) -> Any:
    """Deterministically shrink an image to a pixel budget (aspect preserved)."""

    pixels = image.width * image.height
    if pixels <= pixel_budget:
        return image
    pil = _load_pil()
    if pil is None:
        raise ArticleDeliveryIntegrityError("Pillow is required to bound panel memory")
    Image, ImageDraw, ImageFont = pil
    scale = math.sqrt(pixel_budget / float(pixels))
    return image.resize(
        (
            max(1, int(image.width * scale)),
            max(1, int(image.height * scale)),
        ),
        Image.LANCZOS,
    )


def _panel_label(index: int) -> str:
    """Deterministic panel label: (a)..(z), (aa)..(az), (ba).."""

    letters = "abcdefghijklmnopqrstuvwxyz"
    result = ""
    value = int(index)
    while True:
        result = letters[value % 26] + result
        value = value // 26 - 1
        if value < 0:
            break
    return f"({result})"


def _compose_grid(
    images: Sequence[Any],
    labels: Sequence[str],
) -> Tuple[Any, int, int]:
    """Compose panels onto a deterministic white grid without cropping.

    The composite-pixel bound is enforced before canvas allocation: panels
    are pre-scaled deterministically so the grid never exceeds
    ``_MAX_COMPOSITE_PIXELS`` pixels.
    """

    pil = _load_pil()
    if pil is None:
        raise ArticleDeliveryIntegrityError("Pillow is required for panel composition")
    Image, ImageDraw, ImageFont = pil
    if not images:
        raise ArticleDeliveryIntegrityError("cannot compose an empty panel set")
    count = len(images)
    if count <= 3:
        cols, rows = count, 1
    else:
        cols = math.ceil(math.sqrt(count))
        rows = math.ceil(count / cols)
        while rows > 1 and (rows - 1) * cols >= count:
            rows -= 1
    cell_width = max(item.width for item in images)
    cell_height = max(item.height for item in images)
    natural_pixels = cols * cell_width * rows * cell_height
    temporaries: List[Any] = []
    canvas: Optional[Any] = None
    try:
        if natural_pixels > _MAX_COMPOSITE_PIXELS:
            scale = math.sqrt(_MAX_COMPOSITE_PIXELS / float(natural_pixels))
            pre_scaled = []
            for item in images:
                pre_scaled.append(
                    item.resize(
                        (
                            max(1, int(item.width * scale)),
                            max(1, int(item.height * scale)),
                        ),
                        Image.LANCZOS,
                    )
                )
            temporaries = pre_scaled
            images = pre_scaled
            cell_width = max(item.width for item in images)
            cell_height = max(item.height for item in images)
        canvas = Image.new(
            "RGB",
            (cols * cell_width, rows * cell_height),
            (255, 255, 255),
        )
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default()
        for index, image in enumerate(images):
            row, col = divmod(index, cols)
            x = col * cell_width + (cell_width - image.width) // 2
            y = row * cell_height + (cell_height - image.height) // 2
            canvas.paste(image, (x, y))
            draw.text((x + 2, y + 2), labels[index], fill=(0, 0, 0), font=font)
    except BaseException:
        if canvas is not None:
            canvas.close()
        raise
    finally:
        for temporary in temporaries:
            temporary.close()
    return canvas, rows, cols


def _write_figure_asset(
    staging_figures_dir: Path,
    contract_figure_id: str,
    payload: bytes,
    suffix: str,
    errors: List[str],
) -> Optional[Path]:
    digest = _sha256_bytes(payload)
    slug = re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        str(contract_figure_id or "figure"),
    ).strip("_")
    name = f"{slug}_{digest[:12]}{suffix}"
    destination = staging_figures_dir / name
    destination.write_bytes(payload)
    return destination


def _delivery_body_markdown(
    manuscript: ArticleManuscriptPackage,
    placements: Sequence[CitationPlacement],
    visuals: Sequence[RenderedVisual],
    figure_alias_numbers: Optional[Mapping[str, int]] = None,
    warnings: Optional[List[str]] = None,
) -> str:
    paragraphs_by_section: Dict[str, List[Any]] = {}
    for paragraph in manuscript.source_map:
        paragraphs_by_section.setdefault(paragraph.section_id, []).append(paragraph)
    blocks: List[str] = []
    for section in manuscript.body.sections:
        section_paragraphs = paragraphs_by_section.get(section.section_id, [])
        section_visuals = [
            visual
            for visual in visuals
            if visual.section_id == section.section_id and visual.asset_kind == "table"
        ]
        body = ""
        placed: set[str] = set()
        for paragraph in section_paragraphs:
            rendered = _render_reader_paragraph(
                paragraph.paragraph_id,
                paragraph.rendered_text,
                placements,
            )
            for alias, figure_number in (figure_alias_numbers or {}).items():
                rendered = re.sub(
                    rf"\bFigure\s+{re.escape(alias)}\b",
                    f"Figure {figure_number}",
                    rendered,
                )
                rendered = re.sub(
                    rf"\b{re.escape(alias)}\b",
                    f"Figure {figure_number}",
                    rendered,
                )
            unresolved = sorted(
                set(re.findall(r"\bFIG\d{2}_[A-Za-z0-9_]+\b", rendered))
            )
            if unresolved and warnings is not None:
                warnings.append(
                    f"paragraph {paragraph.paragraph_id!r} retains unresolved "
                    f"figure aliases {unresolved}"
                )
            body = f"{body}\n\n{rendered}".strip()
            for visual in section_visuals:
                if visual.visual_id in placed:
                    continue
                if visual.after_paragraph_id == paragraph.paragraph_id:
                    body = f"{body}\n\n{visual.block_markdown}"
                    placed.add(visual.visual_id)
        for visual in section_visuals:
            if visual.visual_id not in placed:
                body = f"{body}\n\n{visual.block_markdown}"
        blocks.append(f"## {section.heading}\n\n{body}".rstrip())
    return "\n\n".join(blocks)


def _figure_alias_numbers(
    architecture: ArticleArchitectureResult,
    selected_story_id: str,
    visuals: Sequence[RenderedVisual],
) -> Dict[str, int]:
    story = next(
        (item for item in architecture.stories if item.story_id == selected_story_id),
        None,
    )
    if story is None:
        return {}
    number_by_contract = {
        item.contract_figure_id: item.figure_number
        for item in visuals
        if item.figure_number > 0
    }
    aliases: Dict[str, int] = {}
    for index, figure in enumerate(
        sorted(story.figure_contracts, key=lambda item: item.figure_id),
        start=1,
    ):
        number = number_by_contract.get(figure.figure_id)
        if number is None:
            continue
        role = re.sub(r"[^a-z0-9]+", "_", str(figure.role_key or "").lower()).strip("_")
        role = (role[:24].strip("_")) or "item"
        aliases[f"FIG{index:02d}_{role}"] = number
    return aliases


def _build_visual_records(
    visuals: Sequence[RenderedVisual],
    *,
    staging_figures_dir: Path,
    blockers: List[DeliveryBlocker],
    errors: List[str],
) -> Tuple[List[DeliveryVisualRecord], List[Dict[str, Any]]]:
    records: List[DeliveryVisualRecord] = []
    plan_figures: List[Dict[str, Any]] = []
    seen_visual_ids: set[str] = set()
    for visual in visuals:
        if visual.visual_id in seen_visual_ids:
            errors.append(f"duplicate visual id {visual.visual_id!r}")
            continue
        seen_visual_ids.add(visual.visual_id)
        panels: List[DeliveryPanelRecord] = []
        representable = True
        block_reason = ""
        renderer_asset_path = ""
        renderer_media_type = ""
        renderer_bytes = 0
        renderer_sha256 = ""
        composition: Dict[str, Any] = {}
        if visual.asset_kind == "table":
            for panel in visual.panels:
                payload = _panel_bytes(panel)
                if _sha256_bytes(payload) != panel.sha256:
                    errors.append(f"table panel {panel.asset_path!r} hash mismatch")
                    representable = False
                    block_reason = "table panel hash mismatch"
                    continue
                if panel.asset_content not in visual.block_markdown:
                    errors.append(
                        f"table panel {panel.asset_path!r} content missing "
                        "from its block markdown"
                    )
                    representable = False
                    block_reason = "table panel content missing from block"
                    continue
                panels.append(
                    DeliveryPanelRecord(
                        label=panel.label,
                        relative_path=panel.asset_path,
                        media_type=panel.media_type,
                        encoding=panel.encoding,
                        bytes_count=len(payload),
                        sha256=panel.sha256,
                    )
                )
        else:
            decoded: List[Tuple[Any, str]] = []
            try:
                if not visual.panels:
                    representable = False
                    block_reason = "figure has no panels"
                elif len(visual.panels) > _MAX_PANELS_PER_FIGURE:
                    representable = False
                    block_reason = (
                        f"figure panel count {len(visual.panels)} exceeds "
                        f"the {_MAX_PANELS_PER_FIGURE} panel bound"
                    )
                if representable:
                    for panel in visual.panels:
                        payload = _panel_bytes(panel)
                        if _sha256_bytes(payload) != panel.sha256:
                            errors.append(
                                f"figure panel {panel.asset_path!r} hash mismatch"
                            )
                            representable = False
                            block_reason = f"panel {panel.asset_path!r} hash mismatch"
                            break
                        panels.append(
                            DeliveryPanelRecord(
                                label=panel.label,
                                relative_path=panel.asset_path,
                                media_type=panel.media_type,
                                encoding=panel.encoding,
                                bytes_count=len(payload),
                                sha256=panel.sha256,
                            )
                        )
                        decoded_panel = _decode_panel_image(panel, errors)
                        if decoded_panel is None:
                            representable = False
                            block_reason = (
                                f"panel {panel.asset_path!r} cannot be decoded or "
                                "rendered safely"
                            )
                            break
                        decoded_image, decoded_suffix = decoded_panel
                        share = max(
                            1,
                            _MAX_COMPOSITE_PIXELS // len(visual.panels),
                        )
                        if decoded_image.width * decoded_image.height > share:
                            bounded = _shrink_to_pixel_budget(decoded_image, share)
                            decoded_image.close()
                            decoded_image = bounded
                        decoded.append((decoded_image, decoded_suffix))
                if representable:
                    labels = [_panel_label(index) for index in range(len(decoded))]
                    original_hashes = [item.sha256 for item in panels]
                    rows = cols = 1
                    if len(decoded) == 1:
                        image, suffix = decoded[0]
                        original_suffix = Path(
                            visual.panels[0].asset_path
                        ).suffix.lower()
                        if original_suffix in {
                            ".png",
                            ".jpg",
                            ".jpeg",
                            ".pdf",
                        }:
                            payload = _panel_bytes(visual.panels[0])
                            mode = "direct"
                            asset_suffix = original_suffix
                            if original_suffix == ".png":
                                renderer_media_type = "image/png"
                            elif original_suffix == ".pdf":
                                renderer_media_type = "application/pdf"
                            else:
                                renderer_media_type = "image/jpeg"
                        else:
                            payload = _encode_png(image)
                            mode = "converted"
                            asset_suffix = ".png"
                            renderer_media_type = "image/png"
                        asset_width = image.width
                        asset_height = image.height
                    else:
                        images = [item[0] for item in decoded]
                        composed, rows, cols = _compose_grid(images, labels)
                        try:
                            payload = _encode_png(composed)
                            mode = "composite"
                            asset_suffix = ".png"
                            renderer_media_type = "image/png"
                            asset_width = composed.width
                            asset_height = composed.height
                        finally:
                            composed.close()
                    destination = _write_figure_asset(
                        staging_figures_dir,
                        visual.contract_figure_id,
                        payload,
                        asset_suffix,
                        errors,
                    )
                    if destination is None:
                        representable = False
                        block_reason = "generated figure asset could not be written"
                    else:
                        renderer_asset_path = destination.relative_to(
                            staging_figures_dir.parent.parent
                        ).as_posix()
                        renderer_bytes = len(payload)
                        renderer_sha256 = _sha256_bytes(payload)
                        composition = {
                            "mode": mode,
                            "grid_rows": rows,
                            "grid_cols": cols,
                            "panel_labels": labels,
                            "original_panel_hashes": original_hashes,
                            "composite_width": asset_width,
                            "composite_height": asset_height,
                            "total_pixels": asset_width * asset_height,
                        }
                        plan_figures.append(
                            {
                                "figure_id": visual.contract_figure_id,
                                "visual_chunk_id": visual.visual_id,
                                "local_path": str(destination),
                                "review_decision": ("system_approved_test_mode"),
                                "render_status": "ready",
                                "caption_crop_policy": (
                                    "preserve_preprocessed_asset"
                                ),
                                "caption_en": visual.caption,
                                "section_id": visual.section_id,
                                "figure_number": visual.figure_number,
                                "publication_asset_audit": {
                                    "schema_version": (
                                        "article_delivery.visual_asset_audit.v1"
                                    ),
                                    "asset_path": renderer_asset_path,
                                    "sha256": renderer_sha256,
                                    "media_type": renderer_media_type,
                                    "caption": visual.caption,
                                    "source_mode": visual.source_mode,
                                    "provenance": visual.provenance,
                                    "contract_figure_id": (visual.contract_figure_id),
                                    "visual_id": visual.visual_id,
                                    "composition": composition,
                                },
                            }
                        )
            except MemoryError:
                raise
            except Exception as exc:
                errors.append(
                    f"figure {visual.visual_id!r} visual conversion " f"failed: {exc}"
                )
                representable = False
                block_reason = "visual conversion/composition/encoding failed"
            finally:
                for retained_image, _suffix in decoded:
                    retained_image.close()
        if not representable:
            blockers.append(
                DeliveryBlocker(
                    blocker_id=f"blocker-{_digest('visual_unrepresentable', visual.visual_id)}",
                    kind="renderer_representation_limit",
                    message=(
                        block_reason
                        or f"visual {visual.visual_id!r} cannot be represented"
                    ),
                    upstream_ids=[visual.contract_figure_id],
                    artifact_ids=list(visual.artifact_ids),
                )
            )
        records.append(
            DeliveryVisualRecord(
                visual_id=visual.visual_id,
                contract_figure_id=visual.contract_figure_id,
                kind=visual.asset_kind,
                section_id=visual.section_id,
                figure_number=visual.figure_number,
                after_paragraph_id=visual.after_paragraph_id,
                panels=panels,
                caption=visual.caption,
                source_mode=visual.source_mode,
                provenance=visual.provenance,
                representable=representable,
                block_reason=block_reason,
                renderer_asset_path=renderer_asset_path,
                renderer_media_type=renderer_media_type,
                renderer_bytes=renderer_bytes,
                renderer_sha256=renderer_sha256,
                composition=composition,
            )
        )
    return records, plan_figures


def _renderer_metadata(
    presentation: ArticlePresentationPackage,
    metadata: PublicationMetadata,
) -> Dict[str, Any]:
    front = presentation.front_matter
    abstract = (
        " ".join(item["sentence"] for item in front.abstract_sentences)
        if front and front.abstract_sentences
        else ""
    )
    return {
        "title": front.title if front else "",
        "abstract": abstract,
        "keywords": list(front.keywords) if front else [],
        "authors": [
            {
                "name": author.name,
                "affiliation": "; ".join(author.affiliations),
                "email": author.email,
                "orcid": author.orcid,
            }
            for author in metadata.authors
        ],
        "date": metadata.date,
        "draft_only": metadata.draft,
        "acknowledgements": metadata.acknowledgements,
    }


def _normalize_renderer_report(
    report: Dict[str, Any],
    roots: Sequence[Path],
) -> str:
    """Digest the renderer report with absolute staging paths normalized away."""

    ordered_roots = [Path(item).resolve() for item in roots]

    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: walk(item) for key, item in value.items()}
        if isinstance(value, list):
            return [walk(item) for item in value]
        if isinstance(value, str):
            text = value
            try:
                candidate = Path(text)
                if candidate.is_absolute():
                    for root in ordered_roots:
                        try:
                            relative = candidate.resolve().relative_to(root)
                            text = relative.as_posix()
                            break
                        except ValueError:
                            continue
            except (ValueError, OSError):
                pass
            return text
        return value

    return _canonical_json(walk(report))


def _default_renderer(**kwargs: Any) -> Dict[str, Any]:
    from optomind_research.runtime.latex_publication_renderer import (
        build_latex_publication,
    )

    return build_latex_publication(**kwargs)


def compute_delivery_package_id(
    *,
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    review_id: str,
    result_id: str,
    manuscript_body_id: str,
    reproducibility_package_id: str,
    presentation_package_id: str,
    story_id: str,
    status: str,
    renderer_name: str,
    renderer_status: str,
    renderer_report_digest: str,
    renderer_invoked: bool,
    renderer_attempts: int,
    compile_pdf: bool,
    publication_metadata: PublicationMetadata,
    author_metadata_complete: bool,
    reference_metadata_complete: bool,
    references: Sequence[DeliveryReferenceRecord],
    visuals: Sequence[DeliveryVisualRecord],
    artifacts: Sequence[DeliveryArtifactRecord],
    blockers: Sequence[DeliveryBlocker],
    findings: Sequence[DeliveryFinding],
    warnings: Sequence[str],
    errors: Sequence[str],
    cost: DeliveryCostLedger,
    body_sha256: str,
    citation_count: int,
    reference_count: int,
    figure_count: int,
    table_count: int,
    tool_availability: Mapping[str, Any],
) -> str:
    payload = {
        "plan_id": plan_id,
        "ledger_id": ledger_id,
        "architecture_id": architecture_id,
        "review_id": review_id,
        "result_id": result_id,
        "manuscript_body_id": manuscript_body_id,
        "reproducibility_package_id": reproducibility_package_id,
        "presentation_package_id": presentation_package_id,
        "story_id": story_id,
        "status": status,
        "renderer_name": renderer_name,
        "renderer_status": renderer_status,
        "renderer_report_digest": renderer_report_digest,
        "renderer_invoked": renderer_invoked,
        "renderer_attempts": int(renderer_attempts),
        "compile_pdf": compile_pdf,
        "publication_metadata": publication_metadata.model_dump(mode="json"),
        "author_metadata_complete": author_metadata_complete,
        "reference_metadata_complete": reference_metadata_complete,
        "references": [item.model_dump(mode="json") for item in references],
        "visuals": [item.model_dump(mode="json") for item in visuals],
        "artifacts": [item.model_dump(mode="json") for item in artifacts],
        "blockers": [item.model_dump(mode="json") for item in blockers],
        "findings": [item.model_dump(mode="json") for item in findings],
        "warnings": list(warnings),
        "errors": list(errors),
        "cost": cost.model_dump(mode="json"),
        "body_sha256": body_sha256,
        "citation_count": citation_count,
        "reference_count": reference_count,
        "figure_count": figure_count,
        "table_count": table_count,
        "tool_availability": _canonical_json(tool_availability),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _derive_status(
    *,
    blockers: Sequence[DeliveryBlocker],
    errors: Sequence[str],
    renderer_invoked: bool,
    renderer_status: str,
    compile_pdf: bool,
    author_complete: bool,
    reference_complete: bool,
    draft: bool,
    pdf_present: bool,
) -> str:
    if blockers:
        return "blocked"
    if errors or not renderer_invoked:
        return "failed"
    if renderer_status not in {"submission_ready", "compiled_awaiting_metadata"}:
        return "failed"
    if not reference_complete:
        return "failed"
    if renderer_status == "compiled_awaiting_metadata":
        return "compiled_awaiting_metadata"
    if not author_complete or draft or not compile_pdf or not pdf_present:
        return "compiled_awaiting_metadata"
    return "submission_ready"


def _artifact_kind(relative: str) -> str:
    name = Path(relative).name
    if name == "main.tex":
        return "latex"
    if name == "main.pdf":
        return "pdf"
    if name == "arxiv-source.zip":
        return "arxiv_zip"
    if name in {
        "LATEX_BUILD_REPORT.json",
        "REFERENCE_METADATA_AUDIT.json",
        "FIGURE_ASSET_AUDIT.json",
        "PUBLICATION_INTEGRITY_AUDIT.json",
        "ARXIV_MANIFEST.json",
        "BIBLIOGRAPHY_METADATA.json",
    }:
        return "renderer_audit"
    if relative.startswith("renderer_inputs/figures/"):
        return "figure_asset"
    if relative.startswith("figures/"):
        return "figure_asset"
    if relative.startswith("renderer_inputs/"):
        return "renderer_input"
    return "latex"


def _collect_renderer_artifacts(
    renderer_output_dir: Path,
    *,
    final_prefix: str,
    role: Literal["final", "audit"],
) -> List[DeliveryArtifactRecord]:
    records: List[DeliveryArtifactRecord] = []
    for path in sorted(renderer_output_dir.rglob("*")):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        relative_in_latex = path.relative_to(renderer_output_dir).as_posix()
        records.append(
            DeliveryArtifactRecord(
                artifact_id=f"artifact-{_digest(relative_in_latex, _sha256_file(path))}",
                relative_path=f"{final_prefix}/{relative_in_latex}",
                kind=_artifact_kind(relative_in_latex),
                role=role,
                bytes_count=path.stat().st_size,
                sha256=_sha256_file(path),
            )
        )
    return records


def _collect_renderer_input_artifacts(
    staging_root: Path,
    renderer_inputs_dir: Path,
    *,
    role: Literal["final", "audit"],
) -> List[DeliveryArtifactRecord]:
    records: List[DeliveryArtifactRecord] = []
    for path in sorted(renderer_inputs_dir.rglob("*")):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        relative = path.relative_to(staging_root).as_posix()
        records.append(
            DeliveryArtifactRecord(
                artifact_id=f"artifact-{_digest(relative, _sha256_file(path))}",
                relative_path=relative,
                kind=_artifact_kind(relative),
                role=role,
                bytes_count=path.stat().st_size,
                sha256=_sha256_file(path),
            )
        )
    return records


def build_article_delivery(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    reproducibility: ArticleReproducibilityPackage | Mapping[str, Any],
    presentation: ArticlePresentationPackage | Mapping[str, Any],
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    publication_metadata: PublicationMetadata | Mapping[str, Any],
    *,
    additional_usage: Sequence[AdditionalUsageRow | Mapping[str, Any]] = (),
    renderer: Optional[LatexRenderer] = None,
    compile_pdf: bool = True,
    output_dir: Optional[str | Path] = None,
) -> ArticleDeliveryPackage:
    """Deterministic Stage 12D delivery build (no model/network calls).

    When ``output_dir`` is provided, the final delivery bundle is persisted
    conflict-aware; otherwise only the in-memory package is returned.
    """

    errors: List[str] = []
    warnings: List[str] = []
    findings: List[DeliveryFinding] = []
    blockers: List[DeliveryBlocker] = []

    plan_model = _normalize_optional_model(plan, ArticleDirectorPlan, "plan", errors)
    ledger_model = _normalize_optional_model(
        ledger, ClaimLedgerResult, "ledger", errors
    )
    architecture_model = _normalize_optional_model(
        architecture, ArticleArchitectureResult, "architecture", errors
    )
    review_model = _normalize_optional_model(
        review, ArticleReviewResult, "review", errors
    )
    manuscript_model = _normalize_optional_model(
        manuscript, ArticleManuscriptPackage, "manuscript", errors
    )
    reproducibility_model = _normalize_optional_model(
        reproducibility, ArticleReproducibilityPackage, "reproducibility", errors
    )
    presentation_model = _normalize_optional_model(
        presentation, ArticlePresentationPackage, "presentation", errors
    )
    try:
        metadata_model = (
            publication_metadata
            if isinstance(publication_metadata, PublicationMetadata)
            else PublicationMetadata.model_validate(publication_metadata)
        )
    except ValidationError as exc:
        errors.append(f"publication metadata is invalid: {exc}")
        metadata_model = PublicationMetadata(authors=[])
    rows: List[AdditionalUsageRow] = []
    for index, raw in enumerate(additional_usage):
        try:
            rows.append(
                raw
                if isinstance(raw, AdditionalUsageRow)
                else AdditionalUsageRow.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"additional_usage[{index}] is invalid: {exc}")
    records = _normalize_records(value_records, errors)
    if (
        plan_model is None
        or ledger_model is None
        or architecture_model is None
        or review_model is None
        or manuscript_model is None
        or reproducibility_model is None
        or presentation_model is None
        or errors
    ):
        return _blocked_package(
            plan_id=plan_model.plan_id if plan_model else "",
            ledger_id=ledger_model.ledger_id if ledger_model else "",
            architecture_id=(
                architecture_model.architecture_id if architecture_model else ""
            ),
            review_id=review_model.review_id if review_model else "",
            result_id=review_model.result_id if review_model else "",
            manuscript_body_id=(manuscript_model.body_id if manuscript_model else ""),
            reproducibility_package_id=(
                reproducibility_model.package_id if reproducibility_model else ""
            ),
            presentation_package_id=(
                presentation_model.package_id if presentation_model else ""
            ),
            story_id=selected_story_id,
            errors=errors,
            warnings=warnings,
            blockers=blockers,
            compile_pdf=compile_pdf,
            metadata=metadata_model,
        )

    validation_errors: List[str] = []
    validation_warnings: List[str] = []
    chain_ok = validate_presentation_package(
        presentation_model,
        plan=plan_model,
        ledger=ledger_model,
        architecture=architecture_model,
        review=review_model,
        manuscript=manuscript_model,
        reproducibility=reproducibility_model,
        selected_story_id=selected_story_id,
        value_records=records,
        require_body_provenance=True,
        errors=validation_errors,
        warnings=validation_warnings,
    )
    if not chain_ok:
        for item in validation_errors:
            blockers.append(
                DeliveryBlocker(
                    blocker_id=f"blocker-{_digest('upstream', item)}",
                    kind="upstream_validation",
                    message=item,
                )
            )
        warnings.extend(validation_warnings)
        return _blocked_package(
            plan_id=plan_model.plan_id,
            ledger_id=ledger_model.ledger_id,
            architecture_id=architecture_model.architecture_id,
            review_id=review_model.review_id,
            result_id=review_model.result_id,
            manuscript_body_id=manuscript_model.body_id,
            reproducibility_package_id=reproducibility_model.package_id,
            presentation_package_id=presentation_model.package_id,
            story_id=selected_story_id,
            errors=errors,
            warnings=warnings,
            blockers=blockers,
            compile_pdf=compile_pdf,
            metadata=metadata_model,
        )
    warnings.extend(validation_warnings)

    if presentation_model.status == "blocked":
        blockers.append(
            DeliveryBlocker(
                blocker_id=f"blocker-{_digest('presentation_blocked')}",
                kind="upstream_blocked",
                message="presentation package status is blocked",
                upstream_ids=[presentation_model.package_id],
            )
        )
    if review_model.status == "blocked":
        blockers.append(
            DeliveryBlocker(
                blocker_id=f"blocker-{_digest('review_blocked')}",
                kind="upstream_blocked",
                message="review result status is blocked",
                upstream_ids=[review_model.result_id],
            )
        )
    if manuscript_model.body.status == "blocked":
        blockers.append(
            DeliveryBlocker(
                blocker_id=f"blocker-{_digest('manuscript_blocked')}",
                kind="upstream_blocked",
                message="manuscript body status is blocked",
                upstream_ids=[manuscript_model.body_id],
            )
        )
    if reproducibility_model.status == "blocked":
        blockers.append(
            DeliveryBlocker(
                blocker_id=f"blocker-{_digest('reproducibility_blocked')}",
                kind="upstream_blocked",
                message="reproducibility package status is blocked",
                upstream_ids=[reproducibility_model.package_id],
            )
        )
    front = presentation_model.front_matter
    if front is None or not str(front.title or "").strip():
        blockers.append(
            DeliveryBlocker(
                blocker_id=f"blocker-{_digest('missing_title')}",
                kind="missing_front_matter",
                message="presentation front matter has no title",
                upstream_ids=[presentation_model.package_id],
            )
        )
    if front is None or not front.abstract_sentences:
        blockers.append(
            DeliveryBlocker(
                blocker_id=f"blocker-{_digest('missing_abstract')}",
                kind="missing_front_matter",
                message="presentation front matter has no abstract sentences",
                upstream_ids=[presentation_model.package_id],
            )
        )
    if front is None or not front.keywords:
        blockers.append(
            DeliveryBlocker(
                blocker_id=f"blocker-{_digest('missing_keywords')}",
                kind="missing_front_matter",
                message="presentation front matter has no keywords",
                upstream_ids=[presentation_model.package_id],
            )
        )

    body_bytes = manuscript_model.body_markdown.encode("utf-8")
    body_sha256 = _sha256_bytes(body_bytes)
    for paragraph in manuscript_model.source_map:
        rendered = _render_reader_paragraph(
            paragraph.paragraph_id,
            paragraph.rendered_text,
            presentation_model.placements,
        )
        if rendered not in presentation_model.reader_markdown:
            blockers.append(
                DeliveryBlocker(
                    blocker_id=f"blocker-{_digest('body_invariant', paragraph.paragraph_id)}",
                    kind="body_mutation",
                    message=(
                        f"paragraph {paragraph.paragraph_id!r} is not "
                        "byte-identical in the presentation reader"
                    ),
                    upstream_ids=[paragraph.paragraph_id],
                )
            )

    references, references_complete = _build_reference_records(
        presentation_model.references,
        errors,
        findings,
        blockers,
    )
    if not references_complete:
        warnings.append("incomplete bibliographic metadata blocks renderer input")

    author_complete = bool(metadata_model.authors) and all(
        str(author.name or "").strip() and bool(author.affiliations)
        for author in metadata_model.authors
    )
    if not author_complete:
        warnings.append("author metadata incomplete; delivery is not submission-ready")
        findings.append(
            DeliveryFinding(
                finding_id=f"finding-{_digest('author_metadata_pending')}",
                kind="author_metadata_pending",
                message="author metadata is missing or incomplete",
            )
        )

    if output_dir is not None:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        staging_root = output_dir / ".delivery_audit"
    else:
        staging_root = (
            Path(tempfile.gettempdir())
            / f"article_delivery_{_digest(plan_model.plan_id, selected_story_id, os.getpid())}"
        )
        staging_root.mkdir(parents=True, exist_ok=True)
    renderer_output_dir = staging_root / "latex"
    renderer_inputs_dir = staging_root / "renderer_inputs"
    staging_figures_dir = renderer_inputs_dir / "figures"
    renderer_output_dir.mkdir(parents=True, exist_ok=True)
    staging_figures_dir.mkdir(parents=True, exist_ok=True)

    visuals, plan_figures = _build_visual_records(
        presentation_model.visuals,
        staging_figures_dir=staging_figures_dir,
        blockers=blockers,
        errors=errors,
    )
    cost_ledger = _build_cost_ledger(
        plan=plan_model,
        architecture=architecture_model,
        review=review_model,
        presentation=presentation_model,
        reproducibility=reproducibility_model,
        additional_usage=rows,
        errors=errors,
        findings=findings,
    )
    if cost_ledger is None:
        return _blocked_package(
            plan_id=plan_model.plan_id,
            ledger_id=ledger_model.ledger_id,
            architecture_id=architecture_model.architecture_id,
            review_id=review_model.review_id,
            result_id=review_model.result_id,
            manuscript_body_id=manuscript_model.body_id,
            reproducibility_package_id=reproducibility_model.package_id,
            presentation_package_id=presentation_model.package_id,
            story_id=selected_story_id,
            errors=errors,
            warnings=warnings,
            blockers=blockers,
            compile_pdf=compile_pdf,
            metadata=metadata_model,
        )
    if errors:
        for item in errors:
            if not any(blocker.message == item for blocker in blockers):
                blockers.append(
                    DeliveryBlocker(
                        blocker_id=f"blocker-{_digest('blocked_input', item)}",
                        kind="blocked_input",
                        message=item,
                    )
                )
    if blockers:
        blocked_artifacts = _collect_renderer_input_artifacts(
            staging_root,
            renderer_inputs_dir,
            role="final" if output_dir is not None else "audit",
        )
        return _finalize_blocked(
            plan=plan_model,
            ledger=ledger_model,
            architecture=architecture_model,
            review=review_model,
            manuscript=manuscript_model,
            reproducibility=reproducibility_model,
            presentation=presentation_model,
            selected_story_id=selected_story_id,
            metadata=metadata_model,
            blockers=blockers,
            findings=findings,
            warnings=warnings,
            errors=errors,
            cost_ledger=cost_ledger,
            references=references,
            visuals=visuals,
            artifacts=blocked_artifacts,
            body_sha256=body_sha256,
            compile_pdf=compile_pdf,
            output_dir=output_dir,
        )

    renderer_name = (
        "build_latex_publication" if renderer is None else "injected_renderer"
    )
    if output_dir is not None:
        output_path = Path(output_dir)
        persisted_path = output_path / "ARTICLE_DELIVERY_PACKAGE.json"
        if persisted_path.is_file():
            try:
                persisted = ArticleDeliveryPackage.model_validate(
                    json.loads(persisted_path.read_text(encoding="utf-8"))
                )
            except Exception as exc:
                raise ArticleDeliveryIntegrityError(
                    "existing delivery package is unreadable: " + str(exc)
                ) from exc
            persisted_errors: List[str] = []
            persisted_warnings: List[str] = []
            valid = validate_delivery_package(
                persisted,
                plan=plan_model,
                ledger=ledger_model,
                architecture=architecture_model,
                review=review_model,
                manuscript=manuscript_model,
                reproducibility=reproducibility_model,
                presentation=presentation_model,
                selected_story_id=selected_story_id,
                value_records=records,
                output_dir=output_path,
                allow_pending_artifacts=False,
                errors=persisted_errors,
                warnings=persisted_warnings,
            )
            request_matches = bool(
                valid
                and persisted.renderer_invoked
                and persisted.plan_id == plan_model.plan_id
                and persisted.ledger_id == ledger_model.ledger_id
                and persisted.architecture_id == architecture_model.architecture_id
                and persisted.review_id == review_model.review_id
                and persisted.result_id == review_model.result_id
                and persisted.manuscript_body_id == manuscript_model.body_id
                and persisted.reproducibility_package_id
                == reproducibility_model.package_id
                and persisted.presentation_package_id == presentation_model.package_id
                and persisted.story_id == selected_story_id
                and persisted.compile_pdf == compile_pdf
                and persisted.renderer_name == renderer_name
                and persisted.publication_metadata == metadata_model
                and _canonical_json(persisted.cost.model_dump(mode="json"))
                == _canonical_json(cost_ledger.model_dump(mode="json"))
            )
            if request_matches:
                return persisted
            raise ArticleDeliveryIntegrityError(
                "existing delivery package conflicts with the current "
                "request; refusing to overwrite: " + "; ".join(persisted_errors[:5])
            )

    source_markdown = _delivery_body_markdown(
        manuscript_model,
        presentation_model.placements,
        presentation_model.visuals,
        _figure_alias_numbers(
            architecture_model,
            selected_story_id,
            presentation_model.visuals,
        ),
        warnings,
    )
    source_markdown_path = renderer_inputs_dir / "source_markdown.md"
    source_markdown_path.write_text(source_markdown, encoding="utf-8", newline="\n")
    metadata_path = renderer_inputs_dir / "metadata.json"
    metadata_path.write_text(
        _canonical_json(_renderer_metadata(presentation_model, metadata_model)),
        encoding="utf-8",
    )
    blueprint_path = renderer_inputs_dir / "blueprint.json"
    blueprint_path.write_text(
        _canonical_json(
            {
                "schema_version": "article_delivery.blueprint.v1",
                "review_thesis": "",
                "full_review_argument": "",
                "sections": [
                    {
                        "section_id": section.section_id,
                        "section_title": section.heading,
                    }
                    for section in manuscript_model.body.sections
                ],
                "topic_identity": {},
                "input_context": {},
            }
        ),
        encoding="utf-8",
    )
    visual_plan_path = renderer_inputs_dir / "visual_plan.json"
    visual_plan_path.write_text(
        _canonical_json(
            {
                "schema_version": "article_delivery.visual_plan.v1",
                "figures": plan_figures,
            }
        ),
        encoding="utf-8",
    )
    content_package_path = renderer_inputs_dir / "content_package.json"
    content_package_path.write_text(
        _canonical_json(
            {
                "schema_version": "article_delivery.content_package.v1",
                "source_run_dir": str(staging_root),
                "final_review_path": str(source_markdown_path),
                "final_visual_package_path": str(visual_plan_path),
                "artifacts": {
                    "review_blueprint": str(blueprint_path),
                },
                "base_kb_sqlite": "",
            }
        ),
        encoding="utf-8",
    )
    bibliography_seed_path = renderer_inputs_dir / "bibliography_seed.json"
    seed_records = {}
    for reference in references:
        seed_records[reference.reference_alias] = {
            "title": reference.title,
            "authors": list(reference.authors),
            "year": reference.year,
            "venue": reference.venue,
            "doi": reference.doi,
            "url": reference.url,
            "metadata_source": "stage12c_delivery",
        }
    bibliography_seed_path.write_text(
        _canonical_json(
            {
                "schema_version": "article_delivery.bibliography_seed.v1",
                "records": seed_records,
            }
        ),
        encoding="utf-8",
    )
    renderer_output_dir.joinpath("BIBLIOGRAPHY_METADATA.json").write_text(
        _canonical_json(
            {
                "schema_version": "research_harness.bibliography_metadata_cache.v1",
                "records": seed_records,
                "updated_at": "",
            }
        ),
        encoding="utf-8",
    )

    artifacts = _collect_renderer_input_artifacts(
        staging_root,
        renderer_inputs_dir,
        role="final" if output_dir is not None else "audit",
    )

    renderer_attempts = 1
    renderer_report_digest = ""
    renderer_status = ""
    renderer_invoked = True
    report: Dict[str, Any] = {}
    try:
        actual_renderer = renderer or _default_renderer
        report = dict(
            actual_renderer(
                content_package_path=content_package_path,
                output_dir=renderer_output_dir,
                metadata_path=metadata_path,
                source_markdown_path=source_markdown_path,
                language="en",
                document_type="article",
                enrich_crossref=False,
                compile_pdf=compile_pdf,
                render_previews=False,
            )
            or {}
        )
        if not isinstance(report, dict):
            raise ArticleDeliveryIntegrityError("renderer returned a non-dict report")
    except MemoryError:
        raise
    except Exception as exc:
        renderer_invoked = True
        errors.append(f"renderer invocation failed: {exc}")
        renderer_status = "failed"

    if renderer_invoked and not errors:
        renderer_status = str(report.get("status") or "")
        if renderer_status in {"", "failed"}:
            errors.append(f"renderer reported non-success status {renderer_status!r}")
        renderer_report_digest = _normalize_renderer_report(
            report,
            [renderer_output_dir, renderer_inputs_dir],
        )
        if not errors:
            declared = report.get("artifacts") or {}
            required = ["main_tex", "arxiv_source_zip"]
            if compile_pdf:
                required.append("compiled_pdf")
            for key in required:
                raw = declared.get(key)
                if not raw:
                    errors.append(f"renderer report declares no {key!r}")
                    continue
                path = Path(str(raw))
                try:
                    path.resolve().relative_to(renderer_output_dir.resolve())
                except ValueError:
                    errors.append(f"renderer artifact {key!r} escapes output dir")
                    continue
                if not path.is_file():
                    errors.append(f"renderer artifact {key!r} is missing: {path}")
        for path in sorted(renderer_output_dir.rglob("*")):
            if not path.is_file() or path.name.endswith(".tmp"):
                continue
            try:
                path.resolve().relative_to(renderer_output_dir.resolve())
            except ValueError:
                errors.append(f"unsafe renderer output path {path}")
                continue
        if not errors:
            artifacts.extend(
                _collect_renderer_artifacts(
                    renderer_output_dir,
                    final_prefix=(
                        "latex" if output_dir is not None else ".delivery_audit/latex"
                    ),
                    role="final" if output_dir is not None else "audit",
                )
            )
            artifact_paths = {item.relative_path for item in artifacts}
            if len(artifact_paths) != len(artifacts):
                errors.append("duplicate delivery artifact relative paths")

    pdf_present = any(
        item.relative_path.endswith("/main.pdf") or item.relative_path == "main.pdf"
        for item in artifacts
    )
    if compile_pdf and not pdf_present:
        errors.append("main.pdf is missing after a compile-enabled renderer run")
    if not compile_pdf:
        warnings.append("pdf compilation disabled; delivery is not submission-ready")
        findings.append(
            DeliveryFinding(
                finding_id=f"finding-{_digest('pdf_not_compiled')}",
                kind="pdf_not_compiled",
                message="compile_pdf is False; no PDF was produced",
            )
        )

    reference_metadata_complete = references_complete
    status = _derive_status(
        blockers=blockers,
        errors=errors,
        renderer_invoked=renderer_invoked,
        renderer_status=renderer_status,
        compile_pdf=compile_pdf,
        author_complete=author_complete,
        reference_complete=references_complete,
        draft=metadata_model.draft,
        pdf_present=pdf_present,
    )
    tool_availability = {
        "compile_pdf": compile_pdf,
        "pandoc_found": shutil.which("pandoc") is not None,
        "latexmk_found": shutil.which("latexmk") is not None,
    }
    package = ArticleDeliveryPackage(
        package_id="0" * 64,
        plan_id=plan_model.plan_id,
        ledger_id=ledger_model.ledger_id,
        architecture_id=architecture_model.architecture_id,
        review_id=review_model.review_id,
        result_id=review_model.result_id,
        manuscript_body_id=manuscript_model.body_id,
        reproducibility_package_id=reproducibility_model.package_id,
        presentation_package_id=presentation_model.package_id,
        story_id=selected_story_id,
        status=status,
        renderer_name=renderer_name,
        renderer_status=renderer_status,
        renderer_invoked=renderer_invoked,
        renderer_report_digest=renderer_report_digest,
        renderer_attempts=renderer_attempts,
        compile_pdf=compile_pdf,
        publication_metadata=metadata_model,
        author_metadata_complete=author_complete,
        reference_metadata_complete=reference_metadata_complete,
        references=references,
        visuals=visuals,
        artifacts=artifacts,
        blockers=blockers,
        findings=findings,
        warnings=warnings,
        errors=errors,
        cost=cost_ledger,
        body_sha256=body_sha256,
        citation_count=len(presentation_model.placements),
        reference_count=len(references),
        figure_count=sum(1 for item in visuals if item.kind == "figure"),
        table_count=sum(1 for item in visuals if item.kind == "table"),
        tool_availability=tool_availability,
    )
    package = package.model_copy(
        update={
            "package_id": compute_delivery_package_id(
                plan_id=package.plan_id,
                ledger_id=package.ledger_id,
                architecture_id=package.architecture_id,
                review_id=package.review_id,
                result_id=package.result_id,
                manuscript_body_id=package.manuscript_body_id,
                reproducibility_package_id=package.reproducibility_package_id,
                presentation_package_id=package.presentation_package_id,
                story_id=package.story_id,
                status=package.status,
                renderer_name=package.renderer_name,
                renderer_status=package.renderer_status,
                renderer_report_digest=package.renderer_report_digest,
                renderer_invoked=package.renderer_invoked,
                renderer_attempts=package.renderer_attempts,
                compile_pdf=package.compile_pdf,
                publication_metadata=package.publication_metadata,
                author_metadata_complete=package.author_metadata_complete,
                reference_metadata_complete=package.reference_metadata_complete,
                references=package.references,
                visuals=package.visuals,
                artifacts=package.artifacts,
                blockers=package.blockers,
                findings=package.findings,
                warnings=package.warnings,
                errors=package.errors,
                cost=package.cost,
                body_sha256=package.body_sha256,
                citation_count=package.citation_count,
                reference_count=package.reference_count,
                figure_count=package.figure_count,
                table_count=package.table_count,
                tool_availability=package.tool_availability,
            )
        }
    )
    if output_dir is not None:
        write_delivery_package(
            package,
            Path(output_dir),
            staging_dir=staging_root,
        )
    return package


def _blocked_package(
    *,
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    review_id: str,
    result_id: str,
    manuscript_body_id: str,
    reproducibility_package_id: str,
    presentation_package_id: str,
    story_id: str,
    errors: Sequence[str],
    warnings: Sequence[str],
    blockers: Sequence[DeliveryBlocker],
    compile_pdf: bool,
    metadata: PublicationMetadata,
) -> ArticleDeliveryPackage:
    empty_totals = DeliveryCostTotals()
    empty_coverage = ["telemetry_not_evaluated"]
    empty_cost = DeliveryCostLedger(
        ledger_id=_cost_ledger_id([], empty_totals, empty_coverage),
        rows=[],
        totals=empty_totals,
        coverage_missing=empty_coverage,
        total_cost_complete=False,
    )
    blocker_list = list(blockers)
    for item in errors:
        if not any(blocker.message == item for blocker in blocker_list):
            blocker_list.append(
                DeliveryBlocker(
                    blocker_id=f"blocker-{_digest('blocked_input', item)}",
                    kind="blocked_input",
                    message=item,
                )
            )
    package = ArticleDeliveryPackage(
        package_id="0" * 64,
        plan_id=plan_id,
        ledger_id=ledger_id,
        architecture_id=architecture_id,
        review_id=review_id,
        result_id=result_id,
        manuscript_body_id=manuscript_body_id,
        reproducibility_package_id=reproducibility_package_id,
        presentation_package_id=presentation_package_id,
        story_id=story_id,
        status="blocked",
        renderer_name="",
        renderer_invoked=False,
        renderer_attempts=0,
        compile_pdf=compile_pdf,
        publication_metadata=metadata,
        author_metadata_complete=bool(metadata.authors)
        and all(
            str(author.name or "").strip() and bool(author.affiliations)
            for author in metadata.authors
        ),
        reference_metadata_complete=False,
        references=[],
        visuals=[],
        artifacts=[],
        blockers=blocker_list,
        findings=[],
        warnings=list(warnings),
        errors=list(errors),
        cost=empty_cost,
        body_sha256="0" * 64,
        citation_count=0,
        reference_count=0,
        figure_count=0,
        table_count=0,
        tool_availability={"compile_pdf": compile_pdf},
    )
    return package.model_copy(
        update={
            "package_id": compute_delivery_package_id(
                plan_id=package.plan_id,
                ledger_id=package.ledger_id,
                architecture_id=package.architecture_id,
                review_id=package.review_id,
                result_id=package.result_id,
                manuscript_body_id=package.manuscript_body_id,
                reproducibility_package_id=package.reproducibility_package_id,
                presentation_package_id=package.presentation_package_id,
                story_id=package.story_id,
                status=package.status,
                renderer_name=package.renderer_name,
                renderer_status=package.renderer_status,
                renderer_report_digest=package.renderer_report_digest,
                renderer_invoked=package.renderer_invoked,
                renderer_attempts=package.renderer_attempts,
                compile_pdf=package.compile_pdf,
                publication_metadata=package.publication_metadata,
                author_metadata_complete=package.author_metadata_complete,
                reference_metadata_complete=package.reference_metadata_complete,
                references=package.references,
                visuals=package.visuals,
                artifacts=package.artifacts,
                blockers=package.blockers,
                findings=package.findings,
                warnings=package.warnings,
                errors=package.errors,
                cost=package.cost,
                body_sha256=package.body_sha256,
                citation_count=package.citation_count,
                reference_count=package.reference_count,
                figure_count=package.figure_count,
                table_count=package.table_count,
                tool_availability=package.tool_availability,
            )
        }
    )


def _finalize_blocked(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    review: ArticleReviewResult,
    manuscript: ArticleManuscriptPackage,
    reproducibility: ArticleReproducibilityPackage,
    presentation: ArticlePresentationPackage,
    selected_story_id: str,
    metadata: PublicationMetadata,
    blockers: Sequence[DeliveryBlocker],
    findings: Sequence[DeliveryFinding],
    warnings: Sequence[str],
    errors: Sequence[str],
    cost_ledger: DeliveryCostLedger,
    references: Sequence[DeliveryReferenceRecord],
    visuals: Sequence[DeliveryVisualRecord],
    artifacts: Sequence[DeliveryArtifactRecord],
    body_sha256: str,
    compile_pdf: bool,
    output_dir: Optional[Path],
) -> ArticleDeliveryPackage:
    package = ArticleDeliveryPackage(
        package_id="0" * 64,
        plan_id=plan.plan_id,
        ledger_id=ledger.ledger_id,
        architecture_id=architecture.architecture_id,
        review_id=review.review_id,
        result_id=review.result_id,
        manuscript_body_id=manuscript.body_id,
        reproducibility_package_id=reproducibility.package_id,
        presentation_package_id=presentation.package_id,
        story_id=selected_story_id,
        status="blocked",
        renderer_name="",
        renderer_invoked=False,
        renderer_attempts=0,
        compile_pdf=compile_pdf,
        publication_metadata=metadata,
        author_metadata_complete=bool(metadata.authors)
        and all(
            str(author.name or "").strip() and bool(author.affiliations)
            for author in metadata.authors
        ),
        reference_metadata_complete=False,
        references=list(references),
        visuals=list(visuals),
        artifacts=list(artifacts),
        blockers=list(blockers),
        findings=list(findings),
        warnings=list(warnings),
        errors=list(errors),
        cost=cost_ledger,
        body_sha256=body_sha256,
        citation_count=len(presentation.placements),
        reference_count=len(references),
        figure_count=sum(1 for item in visuals if item.kind == "figure"),
        table_count=sum(1 for item in visuals if item.kind == "table"),
        tool_availability={"compile_pdf": compile_pdf},
    )
    package = package.model_copy(
        update={
            "package_id": compute_delivery_package_id(
                plan_id=package.plan_id,
                ledger_id=package.ledger_id,
                architecture_id=package.architecture_id,
                review_id=package.review_id,
                result_id=package.result_id,
                manuscript_body_id=package.manuscript_body_id,
                reproducibility_package_id=package.reproducibility_package_id,
                presentation_package_id=package.presentation_package_id,
                story_id=package.story_id,
                status=package.status,
                renderer_name=package.renderer_name,
                renderer_status=package.renderer_status,
                renderer_report_digest=package.renderer_report_digest,
                renderer_invoked=package.renderer_invoked,
                renderer_attempts=package.renderer_attempts,
                compile_pdf=package.compile_pdf,
                publication_metadata=package.publication_metadata,
                author_metadata_complete=package.author_metadata_complete,
                reference_metadata_complete=package.reference_metadata_complete,
                references=package.references,
                visuals=package.visuals,
                artifacts=package.artifacts,
                blockers=package.blockers,
                findings=package.findings,
                warnings=package.warnings,
                errors=package.errors,
                cost=package.cost,
                body_sha256=package.body_sha256,
                citation_count=package.citation_count,
                reference_count=package.reference_count,
                figure_count=package.figure_count,
                table_count=package.table_count,
                tool_availability=package.tool_availability,
            )
        }
    )
    if output_dir is not None:
        write_delivery_package(
            package,
            output_dir,
            staging_dir=output_dir / ".delivery_audit",
        )
    return package


def _expected_core_files(package: ArticleDeliveryPackage) -> Dict[str, bytes]:
    cost_bytes = _canonical_json(package.cost.model_dump(mode="json")).encode("utf-8")
    checklist = _render_checklist(package).encode("utf-8")
    package_bytes = _canonical_json(package.model_dump(mode="json")).encode("utf-8")
    audit = _render_audit(package, package_bytes, cost_bytes, checklist)
    manifest = _render_input_manifest(package)
    return {
        "ARTICLE_DELIVERY_PACKAGE.json": package_bytes,
        "ARTICLE_PUBLICATION_AUDIT.json": _canonical_json(audit).encode("utf-8"),
        "ARTICLE_COST_LEDGER.json": cost_bytes,
        "ARTICLE_SUBMISSION_CHECKLIST.md": checklist,
        "ARTICLE_RENDERER_INPUT_MANIFEST.json": _canonical_json(manifest).encode(
            "utf-8"
        ),
    }


def _render_checklist(package: ArticleDeliveryPackage) -> str:
    lines = [
        "# Article Submission Checklist",
        "",
        f"- Delivery package ID: `{package.package_id}`",
        f"- Status: `{package.status}`",
        "",
        "## Upstream identity",
        f"- Plan: `{package.plan_id}`",
        f"- Ledger: `{package.ledger_id}`",
        f"- Architecture: `{package.architecture_id}`",
        f"- Review result: `{package.result_id}`",
        f"- Manuscript body: `{package.manuscript_body_id}`",
        f"- Reproducibility: `{package.reproducibility_package_id}`",
        f"- Presentation: `{package.presentation_package_id}`",
        f"- Story: `{package.story_id}`",
        "",
        "## Content",
        f"- Body SHA256: `{package.body_sha256}`",
        f"- Citations: {package.citation_count}",
        f"- References: {package.reference_count}",
        f"- Figures: {package.figure_count}",
        f"- Tables: {package.table_count}",
        f"- Author metadata complete: {'yes' if package.author_metadata_complete else 'no'}",
        f"- Reference metadata complete: {'yes' if package.reference_metadata_complete else 'no'}",
        "",
        "## Renderer",
        f"- Renderer: `{package.renderer_name}`",
        f"- Invoked: {'yes' if package.renderer_invoked else 'no'}",
        f"- Status: `{package.renderer_status}`",
        f"- Report digest: `{package.renderer_report_digest}`",
        "",
        "## Artifacts",
    ]
    has_main_tex = False
    has_pdf = False
    has_arxiv = False
    for artifact in package.artifacts:
        name = Path(artifact.relative_path).name
        if name == "main.tex":
            has_main_tex = True
        elif name == "main.pdf":
            has_pdf = True
        elif name == "arxiv-source.zip":
            has_arxiv = True
        lines.append(
            f"- [x] {artifact.relative_path} "
            f"({artifact.bytes_count} bytes, sha256 {artifact.sha256[:12]}...)"
        )
    if not package.artifacts:
        lines.append("- (no renderer artifacts produced)")
    lines.extend(
        [
            "",
            "## Submission gates",
            f"- main.tex present: {'yes' if has_main_tex else 'no'}",
            f"- main.pdf present: {'yes' if has_pdf else 'no'}",
            f"- arXiv source zip present: {'yes' if has_arxiv else 'no'}",
            f"- Hard blockers: {len(package.blockers)}",
        ]
    )
    for blocker in package.blockers:
        lines.append(f"- BLOCKED: {blocker.message}")
    for error in package.errors:
        lines.append(f"- ERROR: {error}")
    lines.append("")
    lines.append("## Cost coverage")
    lines.append(
        f"- Total estimated cost CNY: {package.cost.totals.estimated_cost_cny}"
    )
    if package.cost.coverage_missing:
        lines.append(
            "- Missing telemetry stages: " + ", ".join(package.cost.coverage_missing)
        )
    else:
        lines.append("- Missing telemetry stages: none")
    return "\n".join(lines)


def _render_audit(
    package: ArticleDeliveryPackage,
    package_bytes: bytes,
    cost_bytes: bytes,
    checklist_bytes: bytes,
) -> Dict[str, Any]:
    core_artifacts = [
        {
            "relative_path": "ARTICLE_DELIVERY_PACKAGE.json",
            "bytes": len(package_bytes),
            "sha256": _sha256_bytes(package_bytes),
        },
        {
            "relative_path": "ARTICLE_COST_LEDGER.json",
            "bytes": len(cost_bytes),
            "sha256": _sha256_bytes(cost_bytes),
        },
        {
            "relative_path": "ARTICLE_SUBMISSION_CHECKLIST.md",
            "bytes": len(checklist_bytes),
            "sha256": _sha256_bytes(checklist_bytes),
        },
    ]
    return {
        "schema_version": "article-delivery-audit.v1",
        "package_id": package.package_id,
        "status": package.status,
        "upstream": {
            "plan_id": package.plan_id,
            "ledger_id": package.ledger_id,
            "architecture_id": package.architecture_id,
            "review_id": package.review_id,
            "result_id": package.result_id,
            "manuscript_body_id": package.manuscript_body_id,
            "reproducibility_package_id": package.reproducibility_package_id,
            "presentation_package_id": package.presentation_package_id,
            "story_id": package.story_id,
        },
        "body_sha256": package.body_sha256,
        "citation_count": package.citation_count,
        "reference_count": package.reference_count,
        "figure_count": package.figure_count,
        "table_count": package.table_count,
        "renderer": {
            "name": package.renderer_name,
            "invoked": package.renderer_invoked,
            "status": package.renderer_status,
            "report_digest": package.renderer_report_digest,
            "attempts": package.renderer_attempts,
        },
        "tool_availability": package.tool_availability,
        "cost_coverage": {
            "coverage_missing": package.cost.coverage_missing,
            "total_cost_complete": package.cost.total_cost_complete,
        },
        "blockers": [item.model_dump(mode="json") for item in package.blockers],
        "findings": [item.model_dump(mode="json") for item in package.findings],
        "artifacts": [item.model_dump(mode="json") for item in package.artifacts]
        + core_artifacts,
    }


def _render_input_manifest(package: ArticleDeliveryPackage) -> Dict[str, Any]:
    inputs = [item for item in package.artifacts if item.kind == "renderer_input"]
    return {
        "schema_version": "article-renderer-input-manifest.v1",
        "package_id": package.package_id,
        "inputs": [item.model_dump(mode="json") for item in inputs],
    }


def validate_delivery_package(
    package: ArticleDeliveryPackage | Mapping[str, Any],
    *,
    plan: Optional[ArticleDirectorPlan | Mapping[str, Any]] = None,
    ledger: Optional[ClaimLedgerResult | Mapping[str, Any]] = None,
    architecture: Optional[ArticleArchitectureResult | Mapping[str, Any]] = None,
    review: Optional[ArticleReviewResult | Mapping[str, Any]] = None,
    manuscript: Optional[ArticleManuscriptPackage | Mapping[str, Any]] = None,
    reproducibility: Optional[ArticleReproducibilityPackage | Mapping[str, Any]] = None,
    presentation: Optional[ArticlePresentationPackage | Mapping[str, Any]] = None,
    selected_story_id: str = "",
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]] = (),
    output_dir: Optional[str | Path] = None,
    allow_pending_artifacts: bool = False,
    errors: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
) -> bool:
    """Public deterministic Stage 12D validator (no network/model calls).

    When ``output_dir`` is supplied, every recorded artifact must exist on
    disk and match its SHA256 unless ``allow_pending_artifacts`` is True.
    Only the fixed-name writer passes True during its preflight, after it
    independently proves the staging sources; public validation therefore
    rejects a persisted package with deleted artifacts.
    """

    if errors is None:
        errors = []
    if warnings is None:
        warnings = []
    try:
        package_model = (
            package
            if isinstance(package, ArticleDeliveryPackage)
            else ArticleDeliveryPackage.model_validate(package)
        )
    except ValidationError as exc:
        errors.append(f"delivery package is invalid: {exc}")
        return False
    plan_model = _normalize_optional_model(plan, ArticleDirectorPlan, "plan", errors)
    ledger_model = _normalize_optional_model(
        ledger, ClaimLedgerResult, "ledger", errors
    )
    architecture_model = _normalize_optional_model(
        architecture, ArticleArchitectureResult, "architecture", errors
    )
    review_model = _normalize_optional_model(
        review, ArticleReviewResult, "review", errors
    )
    manuscript_model = _normalize_optional_model(
        manuscript, ArticleManuscriptPackage, "manuscript", errors
    )
    reproducibility_model = _normalize_optional_model(
        reproducibility, ArticleReproducibilityPackage, "reproducibility", errors
    )
    presentation_model = _normalize_optional_model(
        presentation, ArticlePresentationPackage, "presentation", errors
    )
    records = _normalize_records(value_records, errors)

    if plan_model is not None and package_model.plan_id != plan_model.plan_id:
        errors.append("delivery plan_id does not match the plan")
    if ledger_model is not None and package_model.ledger_id != ledger_model.ledger_id:
        errors.append("delivery ledger_id does not match the ledger")
    if architecture_model is not None and (
        package_model.architecture_id != architecture_model.architecture_id
    ):
        errors.append("delivery architecture_id does not match the architecture")
    if review_model is not None and (
        package_model.review_id != review_model.review_id
        or package_model.result_id != review_model.result_id
    ):
        errors.append("delivery review/result identity does not match the review")
    if manuscript_model is not None and (
        package_model.manuscript_body_id != manuscript_model.body_id
    ):
        errors.append("delivery manuscript_body_id does not match the manuscript")
    if reproducibility_model is not None and (
        package_model.reproducibility_package_id != reproducibility_model.package_id
    ):
        errors.append(
            "delivery reproducibility package id does not match the supplied " "package"
        )
    if presentation_model is not None and (
        package_model.presentation_package_id != presentation_model.package_id
    ):
        errors.append(
            "delivery presentation package id does not match the supplied " "package"
        )
    if selected_story_id and package_model.story_id != selected_story_id:
        errors.append("delivery story_id does not match the story")

    if (
        plan_model is not None
        and ledger_model is not None
        and architecture_model is not None
        and review_model is not None
        and manuscript_model is not None
        and reproducibility_model is not None
        and presentation_model is not None
        and not errors
    ):
        chain_errors: List[str] = []
        chain_warnings: List[str] = []
        if not validate_presentation_package(
            presentation_model,
            plan=plan_model,
            ledger=ledger_model,
            architecture=architecture_model,
            review=review_model,
            manuscript=manuscript_model,
            reproducibility=reproducibility_model,
            selected_story_id=selected_story_id,
            value_records=records,
            require_body_provenance=True,
            errors=chain_errors,
            warnings=chain_warnings,
        ):
            errors.extend(chain_errors)
        warnings.extend(chain_warnings)

    recomputed = compute_delivery_package_id(
        plan_id=package_model.plan_id,
        ledger_id=package_model.ledger_id,
        architecture_id=package_model.architecture_id,
        review_id=package_model.review_id,
        result_id=package_model.result_id,
        manuscript_body_id=package_model.manuscript_body_id,
        reproducibility_package_id=package_model.reproducibility_package_id,
        presentation_package_id=package_model.presentation_package_id,
        story_id=package_model.story_id,
        status=package_model.status,
        renderer_name=package_model.renderer_name,
        renderer_status=package_model.renderer_status,
        renderer_report_digest=package_model.renderer_report_digest,
        renderer_invoked=package_model.renderer_invoked,
        renderer_attempts=package_model.renderer_attempts,
        compile_pdf=package_model.compile_pdf,
        publication_metadata=package_model.publication_metadata,
        author_metadata_complete=package_model.author_metadata_complete,
        reference_metadata_complete=package_model.reference_metadata_complete,
        references=package_model.references,
        visuals=package_model.visuals,
        artifacts=package_model.artifacts,
        blockers=package_model.blockers,
        findings=package_model.findings,
        warnings=package_model.warnings,
        errors=package_model.errors,
        cost=package_model.cost,
        body_sha256=package_model.body_sha256,
        citation_count=package_model.citation_count,
        reference_count=package_model.reference_count,
        figure_count=package_model.figure_count,
        table_count=package_model.table_count,
        tool_availability=package_model.tool_availability,
    )
    if recomputed != package_model.package_id:
        errors.append("delivery package_id does not match recomputed identity")

    pdf_present = any(
        Path(item.relative_path).name == "main.pdf" for item in package_model.artifacts
    )
    recomputed_author_complete = bool(
        package_model.publication_metadata.authors
    ) and all(
        str(author.name or "").strip() and bool(author.affiliations)
        for author in package_model.publication_metadata.authors
    )
    if recomputed_author_complete != package_model.author_metadata_complete:
        errors.append("delivery author_metadata_complete does not match its metadata")
    derived = _derive_status(
        blockers=package_model.blockers,
        errors=package_model.errors,
        renderer_invoked=package_model.renderer_invoked,
        renderer_status=package_model.renderer_status,
        compile_pdf=package_model.compile_pdf,
        author_complete=recomputed_author_complete,
        reference_complete=package_model.reference_metadata_complete,
        draft=package_model.publication_metadata.draft,
        pdf_present=pdf_present,
    )
    if derived != package_model.status:
        errors.append(
            f"delivery status {package_model.status!r} does not match "
            f"derived status {derived!r}"
        )
    if package_model.status != "blocked" and not package_model.renderer_invoked:
        errors.append("non-blocked delivery claims renderer was not invoked")
    if package_model.blockers and package_model.renderer_invoked:
        errors.append("blocked delivery must not invoke the renderer")

    finding_ids = [item.finding_id for item in package_model.findings]
    if len(finding_ids) != len(set(finding_ids)):
        errors.append("delivery findings have duplicate IDs")

    artifact_ids = [item.artifact_id for item in package_model.artifacts]
    if len(artifact_ids) != len(set(artifact_ids)):
        errors.append("delivery artifacts have duplicate IDs")
    artifact_paths = [item.relative_path for item in package_model.artifacts]
    if len(artifact_paths) != len(set(artifact_paths)):
        errors.append("delivery artifacts have duplicate relative paths")
    for item in package_model.artifacts:
        if not _HEX64_RE.fullmatch(str(item.sha256 or "")):
            errors.append(f"artifact {item.relative_path!r} has invalid sha256")
        if output_dir is not None:
            path = Path(output_dir) / item.relative_path
            try:
                resolved = path.resolve()
                resolved.relative_to(Path(output_dir).resolve())
            except ValueError:
                errors.append(f"artifact {item.relative_path!r} escapes output_dir")
                continue
            if not resolved.is_file():
                if item.relative_path.startswith(".delivery_audit/"):
                    errors.append(
                        f"staging artifact {item.relative_path!r} is missing " "on disk"
                    )
                elif not allow_pending_artifacts:
                    errors.append(f"artifact {item.relative_path!r} is missing on disk")
            elif _sha256_file(resolved) != item.sha256:
                errors.append(
                    f"artifact {item.relative_path!r} hash does not match disk"
                )

    visual_ids = [item.visual_id for item in package_model.visuals]
    if len(visual_ids) != len(set(visual_ids)):
        errors.append("delivery visuals have duplicate IDs")
    if package_model.figure_count != sum(
        1 for item in package_model.visuals if item.kind == "figure"
    ):
        errors.append("delivery figure_count does not match visuals")
    if package_model.table_count != sum(
        1 for item in package_model.visuals if item.kind == "table"
    ):
        errors.append("delivery table_count does not match visuals")
    for visual in package_model.visuals:
        if visual.kind != "figure":
            continue
        if visual.representable:
            if (
                not visual.renderer_asset_path
                or not visual.renderer_sha256
                or visual.renderer_bytes <= 0
            ):
                errors.append(
                    f"representable figure {visual.visual_id!r} lacks a "
                    "renderer asset"
                )
            asset_path = Path(visual.renderer_asset_path)
            if visual.renderer_asset_path and (
                asset_path.is_absolute() or ".." in asset_path.parts
            ):
                errors.append(
                    f"figure {visual.visual_id!r} renderer asset path is " "unsafe"
                )
            if visual.composition.get("mode") not in {
                "direct",
                "converted",
                "composite",
            }:
                errors.append(
                    f"figure {visual.visual_id!r} has invalid composition mode"
                )
            expected_labels = [
                _panel_label(index) for index in range(len(visual.panels))
            ]
            if visual.composition.get("panel_labels") != expected_labels:
                errors.append(
                    f"figure {visual.visual_id!r} panel labels do not match "
                    "its ordered panels"
                )
            if visual.composition.get("original_panel_hashes") != [
                item.sha256 for item in visual.panels
            ]:
                errors.append(
                    f"figure {visual.visual_id!r} original panel hashes do "
                    "not match its ordered panels"
                )
            grid_rows = int(visual.composition.get("grid_rows") or 0)
            grid_cols = int(visual.composition.get("grid_cols") or 0)
            if grid_rows <= 0 or grid_cols <= 0:
                errors.append(f"figure {visual.visual_id!r} has invalid grid metadata")
            elif grid_rows * grid_cols < len(visual.panels):
                errors.append(
                    f"figure {visual.visual_id!r} grid is smaller than its "
                    "panel count"
                )
            if visual.renderer_asset_path:
                artifact = next(
                    (
                        item
                        for item in package_model.artifacts
                        if item.relative_path == visual.renderer_asset_path
                    ),
                    None,
                )
                if (
                    artifact is None
                    or artifact.sha256 != visual.renderer_sha256
                    or artifact.bytes_count != visual.renderer_bytes
                ):
                    errors.append(
                        f"figure {visual.visual_id!r} renderer asset is "
                        "missing or inconsistent in the artifact inventory"
                    )
        else:
            if (
                visual.renderer_asset_path
                or visual.renderer_sha256
                or visual.renderer_bytes
                or visual.composition
            ):
                errors.append(
                    f"non-representable figure {visual.visual_id!r} must not "
                    "carry renderer asset metadata"
                )
    if package_model.reference_count != len(package_model.references):
        errors.append("delivery reference_count does not match references")
    reference_aliases = [item.reference_alias for item in package_model.references]
    if len(reference_aliases) != len(set(reference_aliases)):
        errors.append("delivery references have duplicate aliases")
    citation_keys = [item.citation_key for item in package_model.references]
    if len(citation_keys) != len(set(citation_keys)):
        errors.append("delivery references have duplicate citation keys")

    labels = [row.stage_label for row in package_model.cost.rows]
    if len(labels) != len(set(labels)):
        errors.append("delivery cost ledger has duplicate row labels")
    totals = package_model.cost.totals
    if totals.call_count != sum(row.call_count for row in package_model.cost.rows):
        errors.append("delivery cost call_count total mismatch")
    if totals.attempts != sum(row.attempts for row in package_model.cost.rows):
        errors.append("delivery cost attempts total mismatch")
    if totals.estimated_input_tokens != sum(
        row.estimated_input_tokens for row in package_model.cost.rows
    ):
        errors.append("delivery cost input token total mismatch")
    if totals.estimated_output_tokens != sum(
        row.estimated_output_tokens for row in package_model.cost.rows
    ):
        errors.append("delivery cost output token total mismatch")
    if (
        abs(
            totals.estimated_cost_cny
            - sum(row.estimated_cost_cny for row in package_model.cost.rows)
        )
        > 1e-6
    ):
        errors.append("delivery cost CNY total mismatch")
    for row in package_model.cost.rows:
        if row.estimated_cost_cny < 0 or not math.isfinite(row.estimated_cost_cny):
            errors.append(f"cost row {row.stage_label!r} has invalid cost")
    expected_ledger_id = _cost_ledger_id(
        package_model.cost.rows,
        package_model.cost.totals,
        package_model.cost.coverage_missing,
    )
    if expected_ledger_id != package_model.cost.ledger_id:
        errors.append("delivery cost ledger_id does not match its content")
    if package_model.cost.total_cost_complete and (package_model.cost.coverage_missing):
        errors.append("delivery cost claims completeness despite missing coverage")

    if manuscript_model is not None:
        expected_body_sha = _sha256_bytes(
            manuscript_model.body_markdown.encode("utf-8")
        )
        if expected_body_sha != package_model.body_sha256:
            errors.append("delivery body_sha256 does not match the manuscript")

    return not errors


def write_delivery_package(
    package: ArticleDeliveryPackage,
    output_dir: str | Path,
    *,
    staging_dir: Optional[str | Path] = None,
    plan: Optional[ArticleDirectorPlan | Mapping[str, Any]] = None,
    ledger: Optional[ClaimLedgerResult | Mapping[str, Any]] = None,
    architecture: Optional[ArticleArchitectureResult | Mapping[str, Any]] = None,
    review: Optional[ArticleReviewResult | Mapping[str, Any]] = None,
    manuscript: Optional[ArticleManuscriptPackage | Mapping[str, Any]] = None,
    reproducibility: Optional[ArticleReproducibilityPackage | Mapping[str, Any]] = None,
    presentation: Optional[ArticlePresentationPackage | Mapping[str, Any]] = None,
    selected_story_id: str = "",
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]] = (),
) -> Dict[str, Path]:
    """Atomic fixed-name writer; refuses to overwrite conflicting content."""

    output_dir = Path(output_dir)
    validation_errors: List[str] = []
    validation_warnings: List[str] = []
    if not validate_delivery_package(
        package,
        plan=plan,
        ledger=ledger,
        architecture=architecture,
        review=review,
        manuscript=manuscript,
        reproducibility=reproducibility,
        presentation=presentation,
        selected_story_id=selected_story_id,
        value_records=value_records,
        output_dir=output_dir,
        allow_pending_artifacts=True,
        errors=validation_errors,
        warnings=validation_warnings,
    ):
        raise ArticleDeliveryIntegrityError(
            "refusing to write a delivery package that fails validation: "
            + "; ".join(validation_errors[:5])
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    artifact_sources: Dict[str, Path] = {}
    for artifact in package.artifacts:
        relative = Path(artifact.relative_path)
        if artifact.relative_path.startswith(".delivery_audit/"):
            source = output_dir / artifact.relative_path
            if not source.is_file():
                raise ArticleDeliveryIntegrityError(
                    f"staging artifact missing: {artifact.relative_path}"
                )
            if _sha256_file(source) != artifact.sha256:
                raise ArticleDeliveryIntegrityError(
                    f"staging artifact hash mismatch: {artifact.relative_path}"
                )
            artifact_sources[artifact.relative_path] = source
        else:
            staging = Path(staging_dir) if staging_dir else None
            source = (
                (staging / artifact.relative_path)
                if staging is not None
                else (output_dir / artifact.relative_path)
            )
            artifact_sources[artifact.relative_path] = source

    pending_writes: List[Tuple[Path, bytes]] = []
    for relative, source in artifact_sources.items():
        destination = output_dir / relative
        if source.is_file():
            payload = source.read_bytes()
            if _sha256_bytes(payload) != artifact_sha_by_path(relative, package):
                raise ArticleDeliveryIntegrityError(
                    f"staging artifact hash mismatch: {relative}"
                )
        else:
            if destination.is_file():
                payload = destination.read_bytes()
                if _sha256_bytes(payload) != artifact_sha_by_path(relative, package):
                    raise ArticleDeliveryIntegrityError(
                        f"existing artifact conflicts: {relative}"
                    )
                continue
            raise ArticleDeliveryIntegrityError(
                f"artifact source missing for {relative}"
            )
        if destination.exists():
            existing = destination.read_bytes()
            if existing != payload:
                raise ArticleDeliveryIntegrityError(
                    f"existing artifact conflicts: {relative}"
                )
            continue
        pending_writes.append((destination, payload))

    core_files = _expected_core_files(package)
    for name, payload in core_files.items():
        destination = output_dir / name
        if destination.exists():
            if destination.read_bytes() != payload:
                raise ArticleDeliveryIntegrityError(
                    f"existing core file conflicts: {name}"
                )
            continue
        pending_writes.append((destination, payload))

    written: Dict[str, Path] = {}
    for destination, payload in pending_writes:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + f".tmp{_digest(payload)}")
        temporary.write_bytes(payload)
        temporary.replace(destination)
        written[destination.name] = destination
    for name, payload in core_files.items():
        written.setdefault(name, output_dir / name)
    return written


def artifact_sha_by_path(relative: str, package: ArticleDeliveryPackage) -> str:
    for artifact in package.artifacts:
        if artifact.relative_path == relative:
            return artifact.sha256
    raise ArticleDeliveryIntegrityError(f"no artifact record for {relative!r}")


# ===========================================================================
# Article branch additions (T-17): final delivery packaging (P2-03).
# Distinct names from the legacy ArticleDeliveryPackage pipeline above;
# both layers coexist in this module.
# ===========================================================================

import json as _json  # noqa: E402
import shutil as _shutil  # noqa: E402
import zipfile as _zipfile  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

DELIVERY_EXPECTED_ARTIFACTS: tuple = (
    "article.pdf",
    "article_zh.md",
    "PHYSICS_ACCEPTANCE_CERTIFICATE.json",
    "ProvenanceLedger.json",
    "ClaimLedger.json",
    "replay_record.json",
    "run_manifest.json",
)
DELIVERY_CERTIFICATE_FILENAME = "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
DELIVERY_MANIFEST_FILENAME = "delivery_manifest.json"


class DeliveryCertificateError(Exception):
    """Delivery refused: the physics certificate is missing or not accepted."""


@dataclass
class DeliveryPackage:
    manifest_path: _Path
    zip_path: _Path | None = None
    warnings: list = field(default_factory=list)


def _delivery_sha256(path: _Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_delivery(
    run_manifest: dict,
    output_dir,
    zip_output: bool = False,
) -> DeliveryPackage:
    """Package + verify the final run artifacts; refuse uncertified runs.

    Integrity gaps become DELIVERY_INCOMPLETE_WARNING entries (no raise);
    a missing or unaccepted PHYSICS_ACCEPTANCE_CERTIFICATE.json raises
    DeliveryCertificateError before any manifest is written.
    """
    output_dir = _Path(output_dir)
    run_manifest = dict(run_manifest or {})
    warnings_out: list = []
    translation_skipped = bool(run_manifest.get("translation_skipped"))

    expected = [
        name
        for name in DELIVERY_EXPECTED_ARTIFACTS
        if not (name == "article_zh.md" and translation_skipped)
    ]
    artifacts_meta: list = []
    for name in expected:
        candidate = output_dir / name
        if candidate.is_file() and candidate.stat().st_size > 0:
            artifacts_meta.append(
                {
                    "filename": name,
                    "sha256": _delivery_sha256(candidate),
                    "size_bytes": candidate.stat().st_size,
                }
            )
        else:
            warnings_out.append(f"DELIVERY_INCOMPLETE_WARNING: {name}")

    certificate_path = output_dir / DELIVERY_CERTIFICATE_FILENAME
    accepted = None
    if certificate_path.is_file() and certificate_path.stat().st_size > 0:
        try:
            certificate = _json.loads(
                certificate_path.read_text(encoding="utf-8")
            )
            if isinstance(certificate, dict):
                accepted = certificate.get("accepted")
        except ValueError:
            accepted = None
    if accepted is not True:
        raise DeliveryCertificateError("CERTIFICATE_NOT_ACCEPTED_ERROR")

    manifest = {
        "problem_id": str(run_manifest.get("problem_id") or ""),
        "run_id": str(run_manifest.get("run_id") or ""),
        "generated_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "artifacts": artifacts_meta,
        "warnings": warnings_out,
    }
    manifest_path = output_dir / DELIVERY_MANIFEST_FILENAME
    manifest_path.write_text(
        _json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    zip_path = None
    if zip_output:
        problem_id = manifest["problem_id"] or "problem"
        run_id = manifest["run_id"] or "run"
        zip_path = output_dir / f"{problem_id}_{run_id}_delivery.zip"
        with _zipfile.ZipFile(zip_path, "w", _zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(output_dir.rglob("*")):
                if not file_path.is_file() or file_path == zip_path:
                    continue
                if file_path.suffix == ".zip":
                    continue
                archive.write(file_path, file_path.relative_to(output_dir))
    return DeliveryPackage(
        manifest_path=manifest_path,
        zip_path=zip_path,
        warnings=warnings_out,
    )
