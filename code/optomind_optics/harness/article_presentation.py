"""Stage 12C: citation restoration, figure/table rendering and placement, and
provenance-bound front matter.

Builds the reader-facing presentation from accepted Stages 9-12B outputs.
Stage 12C itself makes no model or network call in the deterministic core;
Qwen (``qwen3.7-flash``) is advisory only for citation placement and
front-matter form filling.  Advisory-model mistakes reject that response and
fall back deterministically (fail-open); unsafe content that survives into
the final package is fail-closed.  Production visual rendering reuses the
existing local figure processor for trusted raster/PDF assets and renders
numeric assets only from declared fields.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
import warnings
import xml.sax.saxutils as saxutils
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
    model_validator,
)

from optomind_optics.harness.article_architecture import (
    ArtifactFieldBinding,
    ArticleArchitectureResult,
    StoryCandidate,
)
from optomind_optics.harness.article_citation_audit import (
    CitationAuditProvider,
    apply_citation_audit,
    write_citation_audit,
)
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_director import (
    ArticleDirectorPlan,
    EvidenceIdentityManifest,
)
from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptPackage,
    validate_manuscript_package,
)
from optomind_optics.harness.article_literature import LiteratureSupplement
from optomind_optics.harness.article_reproducibility import (
    ArticleReproducibilityPackage,
    validate_reproducibility_package,
)
from optomind_optics.harness.article_review import (
    ArticleReviewResult,
    validate_review_result,
)
from optomind_optics.harness.article_writing import (
    TrustedValueRecord,
    _VALUE_TOKEN_RE,
    _usage_with_cost,
)
from optomind_optics.harness.method_research import (
    MethodAllowedUse,
    MethodEvidence,
)
from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient
from optomind_research.runtime.artifact_store import (
    atomic_write_json,
    atomic_write_text,
)
from optomind_research.runtime.publication_figure_processor import (
    prepare_publication_figure,
)


PRESENTATION_SCHEMA_VERSION = "article-presentation-package.v1"
CITATION_SCHEMA_VERSION = "citation-record.v1"
REFERENCE_SCHEMA_VERSION = "reference-record.v1"
PLACEMENT_SCHEMA_VERSION = "citation-placement.v1"
FRONT_MATTER_SCHEMA_VERSION = "front-matter.v1"
VISUAL_SCHEMA_VERSION = "rendered-visual.v1"

MODEL_NAME = "qwen3.7-flash"
DEFAULT_CITATION_MAX_TOKENS = 6000
DEFAULT_FRONT_MATTER_MAX_TOKENS = 12000
CITATION_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Citation Placer.txt"
)
FRONT_MATTER_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Front Matter Writer.txt"
)
_CITATION_MARKER_RE = re.compile(r"\[REF:[A-Za-z0-9_]+\]")
_INTERNAL_SOURCE_MARKER_RE = re.compile(r"\[C\d+_[A-Za-z0-9_]+\]")
_PLAIN_INTEGER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?![\d.])")
_MEASUREMENT_NUMBER_RE = re.compile(
    r"(?:"
    r"\d+\.\d+(?:[eE][+-]?\d+)?"
    r"|\d+[eE][+-]?\d+"
    r"|\d+(?:\.\d+)?\s*%"
    r"|\d+(?:\.\d+)?\s*percent\b"
    r"|(?:[<>]=?|[=])\s*\d+(?:\.\d+)?"
    r"|\b(?:exceeds?|below|under|above|greater than|less than|at least|"
    r"at most|up to|no more than|no less than)\s+\d+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s*(?:nm|um|mm|cm|km|kg|g|mg|s|ms|us|ns|"
    r"Hz|kHz|MHz|GHz|THz|W|mW|uW|kW|V|mV|uV|kV|A|mA|uA|kA|"
    r"K|deg|degC|J|kJ|mol|dB|eV|keV|MeV)\b"
    r")"
)


class ArticlePresentationError(ValueError):
    """Base error for presentation-package failures."""


class ArticlePresentationIntegrityError(ArticlePresentationError):
    """Conflicting persisted presentation content."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CitationRecord(_StrictModel):
    schema_version: Literal["citation-record.v1"] = "citation-record.v1"
    citation_id: str
    reference_alias: str
    paragraph_id: str
    claim_id: str
    hypothesis_id: str
    evidence_id: str
    paper_id: str
    doi: str = ""
    title: str
    year: Optional[int] = None
    support_semantics: Literal["background", "method_guidance", "direct_fact"]
    content_depth: str
    metadata_complete: bool


class ReferenceRecord(_StrictModel):
    schema_version: Literal["reference-record.v1"] = "reference-record.v1"
    reference_id: str
    reference_alias: str
    paper_ids: List[str] = Field(default_factory=list)
    doi: str = ""
    title: str
    year: Optional[int] = None
    authors: List[str] = Field(default_factory=list)
    venue: str = ""
    url: str = ""
    evidence_ids: List[str] = Field(default_factory=list)
    paragraph_ids: List[str] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)
    hypothesis_ids: List[str] = Field(default_factory=list)
    support_semantics: List[str] = Field(default_factory=list)
    content_depth: List[str] = Field(default_factory=list)
    metadata_incomplete_fields: List[str] = Field(default_factory=list)
    metadata_complete: bool = False


class CitationPlacement(_StrictModel):
    schema_version: Literal["citation-placement.v1"] = "citation-placement.v1"
    placement_id: str
    paragraph_id: str
    reference_alias: str
    sentence_position: int
    fallback: bool = False
    marker: str


class FrontMatter(_StrictModel):
    schema_version: Literal["front-matter.v1"] = "front-matter.v1"
    title: str
    abstract_sentences: List[Dict[str, Any]] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    fallback: bool = False


class PanelAsset(_StrictModel):
    schema_version: Literal["panel-asset.v1"] = "panel-asset.v1"
    label: str
    asset_path: str
    encoding: Literal["utf-8", "base64"] = "utf-8"
    media_type: str = "text/plain"
    asset_content: str = ""
    asset_bytes_b64: str = ""
    sha256: str

    @field_validator("asset_path")
    @classmethod
    def _safe_asset_path(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text or "\x00" in text:
            raise ValueError("asset_path must be non-empty without NUL")
        path = Path(text)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("asset_path must be relative and safe")
        return text

    @field_validator("sha256")
    @classmethod
    def _full_hex_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", str(value or "")):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        return value

    @field_validator("asset_bytes_b64")
    @classmethod
    def _valid_base64(cls, value: str) -> str:
        if value:
            try:
                base64.b64decode(value, validate=True)
            except Exception as exc:
                raise ValueError("asset_bytes_b64 must be valid base64") from exc
        return value

    @model_validator(mode="after")
    def _content_exclusive(self) -> "PanelAsset":
        if self.encoding == "utf-8":
            if not self.asset_content or self.asset_bytes_b64:
                raise ValueError("utf-8 panels must carry asset_content and no base64")
        elif not self.asset_bytes_b64 or self.asset_content:
            raise ValueError(
                "base64 panels must carry asset_bytes_b64 and no asset_content"
            )
        return self


class RenderedVisual(_StrictModel):
    schema_version: Literal["rendered-visual.v1"] = "rendered-visual.v1"
    visual_id: str
    asset_kind: Literal["figure", "table"]
    contract_figure_id: str
    section_id: str
    figure_number: int = 0
    after_paragraph_id: str = ""
    panels: List[PanelAsset] = Field(default_factory=list)
    source_mode: Literal["trusted_artifact", "synthesized_claims"]
    provenance: Literal["verified", "synthesized"]
    caption: str
    claim_ids: List[str] = Field(default_factory=list)
    fact_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    sha256: str
    block_markdown: str

    @field_validator("sha256")
    @classmethod
    def _full_hex_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", str(value or "")):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        return value

    @field_validator("panels")
    @classmethod
    def _panel_content_exclusive(cls, value: List[PanelAsset]) -> List[PanelAsset]:
        for panel in value:
            if panel.encoding == "utf-8":
                if not panel.asset_content or panel.asset_bytes_b64:
                    raise ValueError(
                        "utf-8 panels must carry asset_content and no base64"
                    )
            else:
                if not panel.asset_bytes_b64 or panel.asset_content:
                    raise ValueError(
                        "base64 panels must carry asset_bytes_b64 and no "
                        "asset_content"
                    )
        return value


class PublicationBlocker(_StrictModel):
    schema_version: Literal["presentation-blocker.v1"] = "presentation-blocker.v1"
    blocker_id: str
    kind: str
    message: str
    paragraph_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    citation_ids: List[str] = Field(default_factory=list)


class ArticlePresentationPackage(_StrictModel):
    schema_version: Literal["article-presentation-package.v1"] = (
        "article-presentation-package.v1"
    )
    package_id: str
    plan_id: str
    ledger_id: str
    architecture_id: str
    review_id: str
    result_id: str
    manuscript_body_id: str
    reproducibility_package_id: str
    story_id: str
    status: Literal["ready", "ready_with_findings", "blocked"]
    citations: List[CitationRecord] = Field(default_factory=list)
    references: List[ReferenceRecord] = Field(default_factory=list)
    placements: List[CitationPlacement] = Field(default_factory=list)
    front_matter: Optional[FrontMatter] = None
    visuals: List[RenderedVisual] = Field(default_factory=list)
    reader_markdown: str = ""
    blockers: List[PublicationBlocker] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    model_name: Literal["qwen3.7-flash", "none", "mixed"] = "none"
    usage: Dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0
    literature_supplement_id: str = ""


CitationPlacerProvider = Callable[[Mapping[str, Any]], "ProviderResult"]
FrontMatterProvider = Callable[[Mapping[str, Any]], "ProviderResult"]


class ProviderResult(_StrictModel):
    schema_version: Literal["presentation-provider-result.v1"] = (
        "presentation-provider-result.v1"
    )
    response: Dict[str, Any]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


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
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_json(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}


def _json_equal(left_text: str, right_text: str) -> bool:
    try:
        return json.loads(left_text) == json.loads(right_text)
    except json.JSONDecodeError:
        return False


def _safe_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _slugify(text: str, limit: int = 24) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return (cleaned[:limit].strip("_")) or "item"


def _sanitize_asset_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("._")
    return cleaned or "asset"


def _xml_escape(value: Any) -> str:
    return saxutils.escape(str(value))


def _md_escape_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _wrap_text(text: str, width: int = 60) -> List[str]:
    words = str(text).split()
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _sentence_offsets(text: str) -> List[int]:
    offsets: List[int] = []
    abbreviations = (
        "e.g.",
        "i.e.",
        "et al.",
        "fig.",
        "figs.",
        "eq.",
        "eqs.",
        "dr.",
        "mr.",
        "ms.",
        "prof.",
        "vs.",
        "no.",
    )
    for index, char in enumerate(text):
        if char not in ".!?":
            continue
        if (
            char == "."
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        ):
            continue
        if char == "." and index + 1 < len(text) and not text[index + 1].isspace():
            continue
        prefix = text[: index + 1].rstrip().casefold()
        if char == "." and prefix.endswith(abbreviations):
            continue
        if char == "." and re.search(r"(?:\b[a-z]\.){2,}$", prefix):
            continue
        offsets.append(index + 1)
    tail_start = offsets[-1] if offsets else 0
    if text[tail_start:].strip() and (not offsets or offsets[-1] != len(text)):
        offsets.append(len(text))
    return offsets


def _sentence_table(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    start = 0
    for position, end in enumerate(_sentence_offsets(text)):
        sentence = text[start:end].strip()
        if sentence:
            rows.append(
                {"sentence_position": position, "sentence_text": sentence}
            )
        start = end
    return rows


def _strip_citation_markers(text: str) -> str:
    return _CITATION_MARKER_RE.sub("", text)


def _strip_internal_source_markers(text: str) -> str:
    """Remove writer-only claim aliases already preserved by the source map."""

    return _INTERNAL_SOURCE_MARKER_RE.sub("", str(text))


def _marker_to_offset(paragraph_text: str, sentence_position: int) -> int:
    offsets = _sentence_offsets(paragraph_text)
    if not offsets:
        return len(paragraph_text)
    index = max(0, min(sentence_position, len(offsets) - 1))
    return offsets[index]


def _insert_markers(text: str, placements: Sequence[CitationPlacement]) -> str:
    positions: Dict[int, List[str]] = {}
    for placement in placements:
        offset = _marker_to_offset(text, placement.sentence_position)
        positions.setdefault(offset, []).append(placement.marker)
    result = str(text)
    for offset in sorted(positions, reverse=True):
        markers = "".join(sorted(positions[offset]))
        result = result[:offset] + markers + result[offset:]
    return result


def _render_reader_paragraph(
    paragraph_id: str,
    text: str,
    placements: Sequence[CitationPlacement],
) -> str:
    paragraph_placements = [
        item for item in placements if item.paragraph_id == paragraph_id
    ]
    public_text = _strip_internal_source_markers(text)
    return _insert_markers(public_text, paragraph_placements)


def _normalize_doi(doi: str) -> str:
    return str(doi or "").strip().lower().rstrip(".")


def _normalize_title(title: str) -> str:
    return " ".join(str(title or "").lower().split())


def _md_escape(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("#", "\\#")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _valid_url(url: str) -> bool:
    text = str(url or "").strip()
    if not text:
        return True
    if any(char in text for char in ("\n", "\r", " ", "\x00")):
        return False
    return text.startswith("https://") or text.startswith("http://")


def _front_matter_unsafe(text: str) -> bool:
    value = str(text or "")
    if any(ord(char) < 32 for char in value):
        return True
    if "[REF:" in value or "[VALUE:" in value:
        return True
    if "\n" in value or "\r" in value:
        return True
    stripped = value.lstrip()
    if stripped.startswith("#"):
        return True
    if "](" in value and ("http://" in value or "https://" in value):
        return True
    return False


def _build_citations(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    manuscript: ArticleManuscriptPackage,
    evidence_by_id: Mapping[str, MethodEvidence],
    bibliographic_metadata: Mapping[str, Mapping[str, Any]],
    blockers: List[PublicationBlocker],
    warnings: List[str],
) -> Tuple[List[CitationRecord], List[ReferenceRecord], Dict[str, List[str]]]:
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    hypotheses_by_id = {item.hypothesis_id: item for item in plan.hypotheses}
    manifest_by_id = {item.evidence_id: item for item in plan.evidence_identity}
    _validate_bibliographic_metadata(bibliographic_metadata, evidence_by_id, blockers)
    citations: List[CitationRecord] = []
    references_by_doi: Dict[str, Dict[str, Any]] = {}
    alias_by_key: Dict[str, str] = {}
    cited_paragraph_evidence: set[Tuple[str, str]] = set()
    seen_evidence: Dict[str, str] = {}
    for evidence in evidence_by_id.values():
        if evidence.paper_id in seen_evidence:
            prior_title, prior_doi = seen_evidence[evidence.paper_id].split("\x1f", 1)
            if _normalize_title(evidence.title) != _normalize_title(prior_title):
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('conflicting_paper', evidence.paper_id)}",
                        kind="conflicting_paper_identity",
                        message=(
                            f"evidence records for paper {evidence.paper_id!r} "
                            "conflict on title"
                        ),
                    )
                )
            if _normalize_doi(evidence.doi) != _normalize_doi(prior_doi):
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('paper_doi_conflict', evidence.paper_id)}",
                        kind="conflicting_paper_identity",
                        message=(
                            f"evidence records for paper {evidence.paper_id!r} "
                            "conflict on DOI"
                        ),
                    )
                )
        else:
            seen_evidence[evidence.paper_id] = f"{evidence.title}\x1f{evidence.doi}"
        normalized_doi = _normalize_doi(evidence.doi)
        if normalized_doi:
            for other in evidence_by_id.values():
                other_doi = _normalize_doi(other.doi)
                if (
                    other.evidence_id != evidence.evidence_id
                    and other_doi == normalized_doi
                    and _normalize_title(other.title)
                    != _normalize_title(evidence.title)
                ):
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest('doi_title_conflict', normalized_doi)}",
                            kind="conflicting_paper_identity",
                            message=(
                                f"DOI {normalized_doi} maps to incompatible " "titles"
                            ),
                        )
                    )
    has_citations_requested = any(
        hypothesis.evidence_ids for hypothesis in plan.hypotheses
    ) or any(paragraph.literature_evidence_ids for paragraph in manuscript.source_map)
    if has_citations_requested and not plan.evidence_identity:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest('legacy_plan_no_evidence_identity')}",
                kind="missing_evidence_identity",
                message=(
                    "plan lacks a bound evidence identity manifest; "
                    "literature citation is not permitted for legacy plans"
                ),
            )
        )

    def _append_citation(
        paragraph_id: str,
        evidence: MethodEvidence,
        *,
        claim_id: str = "",
        hypothesis_id: str = "",
    ) -> None:
        evidence_id = evidence.evidence_id
        citation_key = (paragraph_id, evidence_id)
        if citation_key in cited_paragraph_evidence:
            return
        cited_paragraph_evidence.add(citation_key)
        support = evidence.allowed_use.value
        metadata = dict(bibliographic_metadata.get(evidence.paper_id, {}) or {})
        effective_year = evidence.year
        if effective_year is None and metadata.get("year") not in (None, ""):
            try:
                effective_year = int(metadata["year"])
            except (TypeError, ValueError):
                # Metadata validation owns conflicts.  A malformed optional
                # year remains incomplete instead of creating a citation fact.
                effective_year = None
        doi_key = _normalize_doi(evidence.doi) or evidence.paper_id
        alias = alias_by_key.get(doi_key)
        if alias is None:
            index = len(alias_by_key) + 1
            alias = f"REF{index:02d}_{_slugify(evidence.title)}"
            alias_by_key[doi_key] = alias
            references_by_doi[doi_key] = {
                "reference_alias": alias,
                "paper_ids": set(),
                "doi": evidence.doi,
                "title": evidence.title,
                "year": effective_year,
                "authors": list(metadata.get("authors", [])),
                "venue": str(metadata.get("venue", "")),
                "url": str(metadata.get("url", "")),
                "evidence_ids": set(),
                "paragraph_ids": set(),
                "claim_ids": set(),
                "hypothesis_ids": set(),
                "support_semantics": set(),
                "content_depth": set(),
                "metadata_incomplete_fields": set(),
            }
        record = references_by_doi[doi_key]
        incomplete = []
        if not evidence.title:
            incomplete.append("title")
        if not effective_year:
            incomplete.append("year")
        if (
            not evidence.doi
            and not record["venue"]
            and not (record["url"] and _valid_url(record["url"]))
        ):
            incomplete.append("doi_or_venue_or_url")
        if not record["authors"]:
            incomplete.append("authors")
        citations.append(
            CitationRecord(
                citation_id=(
                    f"cite-{_digest(paragraph_id, claim_id, evidence_id)}"
                    if claim_id
                    else f"cite-{_digest(paragraph_id, evidence_id)}"
                ),
                reference_alias=alias,
                paragraph_id=paragraph_id,
                claim_id=claim_id,
                hypothesis_id=hypothesis_id,
                evidence_id=evidence_id,
                paper_id=evidence.paper_id,
                doi=evidence.doi,
                title=evidence.title,
                year=effective_year,
                support_semantics=support,
                content_depth=evidence.content_depth.value,
                metadata_complete=not incomplete,
            )
        )
        record["paper_ids"].add(evidence.paper_id)
        record["evidence_ids"].add(evidence_id)
        record["paragraph_ids"].add(paragraph_id)
        if claim_id:
            record["claim_ids"].add(claim_id)
        if hypothesis_id:
            record["hypothesis_ids"].add(hypothesis_id)
        record["support_semantics"].add(support)
        record["content_depth"].add(evidence.content_depth.value)
        record["metadata_incomplete_fields"].update(incomplete)
        if incomplete:
            warnings.append(
                f"reference for paper {evidence.paper_id!r} has incomplete "
                f"bibliographic metadata ({sorted(incomplete)})"
            )

    for paragraph in manuscript.source_map:
        for claim_id in paragraph.claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('unknown_claim', paragraph.paragraph_id, claim_id)}",
                        kind="unknown_claim",
                        message=(
                            f"paragraph {paragraph.paragraph_id!r} references "
                            f"unknown claim {claim_id!r}"
                        ),
                        paragraph_ids=[paragraph.paragraph_id],
                    )
                )
                continue
            hypothesis_id = claim.metadata.get("hypothesis_id")
            hypothesis = hypotheses_by_id.get(hypothesis_id)
            if hypothesis is None:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('unknown_hypothesis', paragraph.paragraph_id, hypothesis_id)}",
                        kind="unknown_hypothesis",
                        message=(
                            f"claim {claim_id!r} references unknown hypothesis "
                            f"{hypothesis_id!r}"
                        ),
                        paragraph_ids=[paragraph.paragraph_id],
                    )
                )
                continue
            for evidence_id in hypothesis.evidence_ids:
                evidence = evidence_by_id.get(evidence_id)
                if evidence is None:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest('missing_evidence', hypothesis_id, evidence_id)}",
                            kind="missing_expected_evidence",
                            message=(
                                f"hypothesis {hypothesis_id!r} references "
                                f"missing method evidence {evidence_id!r}"
                            ),
                            paragraph_ids=[paragraph.paragraph_id],
                        )
                    )
                    continue
                manifest = manifest_by_id.get(evidence_id)
                if manifest is None:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest('unbound_evidence', evidence_id)}",
                            kind="missing_evidence_identity",
                            message=(
                                f"evidence {evidence_id!r} is not bound in "
                                "the plan evidence identity manifest"
                            ),
                            paragraph_ids=[paragraph.paragraph_id],
                        )
                    )
                    continue
                if not _evidence_matches_manifest(evidence, manifest):
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest('evidence_identity_mismatch', evidence_id)}",
                            kind="evidence_identity_mismatch",
                            message=(
                                f"evidence {evidence_id!r} does not match the "
                                "plan-bound identity manifest"
                            ),
                            paragraph_ids=[paragraph.paragraph_id],
                        )
                    )
                    continue
                if evidence.allowed_use == MethodAllowedUse.discovery:
                    warnings.append(
                        f"evidence {evidence_id!r} is discovery-only and is "
                        "not citeable as support"
                    )
                    continue
                _append_citation(
                    paragraph.paragraph_id,
                    evidence,
                    claim_id=claim_id,
                    hypothesis_id=hypothesis_id,
                )
        for evidence_id in paragraph.literature_evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('missing_evidence', paragraph.paragraph_id, evidence_id)}",
                        kind="missing_expected_evidence",
                        message=(
                            f"paragraph {paragraph.paragraph_id!r} references "
                            f"missing method evidence {evidence_id!r}"
                        ),
                        paragraph_ids=[paragraph.paragraph_id],
                    )
                )
                continue
            manifest = manifest_by_id.get(evidence_id)
            if manifest is None:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('unbound_evidence', evidence_id)}",
                        kind="missing_evidence_identity",
                        message=(
                            f"evidence {evidence_id!r} is not bound in the plan "
                            "evidence identity manifest"
                        ),
                        paragraph_ids=[paragraph.paragraph_id],
                    )
                )
                continue
            if not _evidence_matches_manifest(evidence, manifest):
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('evidence_identity_mismatch', evidence_id)}",
                        kind="evidence_identity_mismatch",
                        message=(
                            f"evidence {evidence_id!r} does not match the "
                            "plan-bound identity manifest"
                        ),
                        paragraph_ids=[paragraph.paragraph_id],
                    )
                )
                continue
            if evidence.allowed_use == MethodAllowedUse.discovery:
                warnings.append(
                    f"evidence {evidence_id!r} is discovery-only and is not "
                    "citeable as support"
                )
                continue
            _append_citation(paragraph.paragraph_id, evidence)
    references: List[ReferenceRecord] = []
    for doi_key in sorted(references_by_doi):
        record = references_by_doi[doi_key]
        incomplete = sorted(record["metadata_incomplete_fields"])
        references.append(
            ReferenceRecord(
                reference_id=f"ref-{_digest(doi_key)}",
                reference_alias=record["reference_alias"],
                paper_ids=sorted(record["paper_ids"]),
                doi=record["doi"],
                title=record["title"],
                year=record["year"],
                authors=list(record["authors"]),
                venue=record["venue"],
                url=record["url"],
                evidence_ids=sorted(record["evidence_ids"]),
                paragraph_ids=sorted(record["paragraph_ids"]),
                claim_ids=sorted(record["claim_ids"]),
                hypothesis_ids=sorted(record["hypothesis_ids"]),
                support_semantics=sorted(record["support_semantics"]),
                content_depth=sorted(record["content_depth"]),
                metadata_incomplete_fields=incomplete,
                metadata_complete=not incomplete,
            )
        )
    citation_alias_by_paragraph: Dict[str, List[str]] = {}
    for citation in citations:
        citation_alias_by_paragraph.setdefault(citation.paragraph_id, []).append(
            citation.reference_alias
        )
    return (
        citations,
        references,
        {
            paragraph_id: sorted(set(aliases))
            for paragraph_id, aliases in citation_alias_by_paragraph.items()
        },
    )


def _evidence_matches_manifest(
    evidence: MethodEvidence, manifest: EvidenceIdentityManifest
) -> bool:
    return (
        evidence.paper_id == manifest.paper_id
        and _normalize_doi(evidence.doi) == _normalize_doi(manifest.doi)
        and _normalize_title(evidence.title) == _normalize_title(manifest.title)
        and evidence.year == manifest.year
        and evidence.source_route == manifest.source_route
        and evidence.content_depth.value == manifest.content_depth
        and evidence.allowed_use.value == manifest.allowed_use
        and _sha256_bytes(evidence.text.encode("utf-8")) == manifest.text_sha256
    )


def _supplement_evidence_manifests(
    supplement: LiteratureSupplement | None,
    *,
    plan_id: str,
    errors: List[str],
) -> Tuple[List[EvidenceIdentityManifest], str]:
    if supplement is None:
        return [], ""
    if not isinstance(supplement, LiteratureSupplement):
        errors.append(
            "literature_supplement must be loaded through "
            "load_literature_supplement"
        )
        return [], ""
    if supplement.old_director_plan_id != plan_id:
        errors.append(
            "literature supplement old_director_plan_id does not match "
            "the presentation plan"
        )
    if not supplement.new_plan_id or supplement.new_plan_id == plan_id:
        errors.append(
            "literature supplement must identify a distinct derived plan"
        )
    hash_fields = {
        "report_sha256": supplement.report_sha256,
        "supplement_sha256": supplement.supplement_sha256,
        "metadata_sha256": supplement.metadata_sha256,
    }
    for label, value in hash_fields.items():
        if not re.fullmatch(r"[0-9a-f]{64}", str(value or "")):
            errors.append(
                f"literature supplement {label} must be a bound 64-hex digest"
            )
    manifests: List[EvidenceIdentityManifest] = []
    seen: set[str] = set()
    for entry in supplement.evidence_identity:
        try:
            manifest = EvidenceIdentityManifest.model_validate(
                entry.model_dump(mode="json")
            )
        except ValidationError as exc:
            errors.append(
                f"literature supplement evidence identity is invalid: {exc}"
            )
            continue
        if manifest.evidence_id in seen:
            errors.append(
                "literature supplement repeats evidence identity "
                f"{manifest.evidence_id!r}"
            )
            continue
        seen.add(manifest.evidence_id)
        manifests.append(manifest)
    aliased_ids = set(supplement.evidence_aliases.values())
    unknown_alias_ids = sorted(aliased_ids - seen)
    if unknown_alias_ids:
        errors.append(
            "literature supplement aliases reference unknown evidence "
            f"identities: {unknown_alias_ids}"
        )
    supplement_id = "literature-" + _sha256_bytes(
        _canonical_json(
            {
                "old_plan_id": supplement.old_director_plan_id,
                "new_plan_id": supplement.new_plan_id,
                **hash_fields,
            }
        ).encode("utf-8")
    )[:16]
    return manifests, supplement_id


def _validate_bibliographic_metadata(
    biblio: Mapping[str, Mapping[str, Any]],
    evidence_by_id: Mapping[str, MethodEvidence],
    blockers: List[PublicationBlocker],
) -> None:
    evidence_by_paper: Dict[str, MethodEvidence] = {}
    evidence_by_doi: Dict[str, MethodEvidence] = {}
    for evidence in evidence_by_id.values():
        evidence_by_paper.setdefault(evidence.paper_id, evidence)
        doi = _normalize_doi(evidence.doi)
        if doi:
            existing = evidence_by_doi.get(doi)
            if existing is not None and _normalize_title(
                existing.title
            ) != _normalize_title(evidence.title):
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('doi_metadata_conflict', doi)}",
                        kind="conflicting_paper_identity",
                        message=f"DOI {doi} maps to incompatible titles",
                    )
                )
            evidence_by_doi.setdefault(doi, evidence)
    for key, entry in biblio.items():
        entry_dict = dict(entry or {})
        authors = entry_dict.get("authors") or []
        if (
            not isinstance(authors, list)
            or not authors
            or any(not str(author).strip() for author in authors)
        ):
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('bad_metadata', key)}",
                    kind="conflicting_paper_identity",
                    message=(
                        f"bibliographic metadata for {key!r} has invalid " "authors"
                    ),
                )
            )
            continue
        evidence = evidence_by_paper.get(key) or evidence_by_doi.get(
            _normalize_doi(key)
        )
        if evidence is None:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('unknown_metadata_key', key)}",
                    kind="conflicting_paper_identity",
                    message=(
                        f"bibliographic metadata key {key!r} matches no "
                        "evidence identity"
                    ),
                )
            )
            continue
        if "year" in entry_dict:
            year = entry_dict["year"]
            if evidence.year is not None and int(year) != evidence.year:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('metadata_year', key)}",
                        kind="conflicting_paper_identity",
                        message=(
                            f"bibliographic metadata year for {key!r} "
                            "conflicts with evidence identity"
                        ),
                    )
                )
        if "doi" in entry_dict and _normalize_doi(
            str(entry_dict["doi"])
        ) != _normalize_doi(evidence.doi):
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('metadata_doi', key)}",
                    kind="conflicting_paper_identity",
                    message=(
                        f"bibliographic metadata DOI for {key!r} conflicts "
                        "with evidence identity"
                    ),
                )
            )
        if "title" in entry_dict and _normalize_title(
            str(entry_dict["title"])
        ) != _normalize_title(evidence.title):
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('metadata_title', key)}",
                    kind="conflicting_paper_identity",
                    message=(
                        f"bibliographic metadata title for {key!r} conflicts "
                        "with evidence identity"
                    ),
                )
            )


def _build_citation_section_requests(
    *,
    manuscript: ArticleManuscriptPackage,
    references: Sequence[ReferenceRecord],
    allowed_aliases: Mapping[str, List[str]],
    plan: ArticleDirectorPlan,
    evidence_by_id: Mapping[str, MethodEvidence],
) -> List[Dict[str, Any]]:
    reference_by_alias = {item.reference_alias: item for item in references}
    paragraphs_by_section: Dict[str, List[Dict[str, Any]]] = {}
    for paragraph in manuscript.source_map:
        paragraphs_by_section.setdefault(paragraph.section_id, []).append(
            {
                "paragraph_id": paragraph.paragraph_id,
                "rendered_text": paragraph.rendered_text,
                "sentences": _sentence_table(paragraph.rendered_text),
            }
        )
    requests: List[Dict[str, Any]] = []
    for section_id in sorted(paragraphs_by_section):
        paragraphs = paragraphs_by_section[section_id]
        references_for_section = []
        for alias in sorted(
            {
                alias
                for paragraph in paragraphs
                for alias in allowed_aliases.get(paragraph["paragraph_id"], [])
            }
        ):
            reference = reference_by_alias[alias]
            references_for_section.append(
                {
                    "reference_alias": alias,
                    "title": reference.title,
                    "year": reference.year,
                    "doi": reference.doi,
                    "use": sorted(reference.support_semantics),
                    "evidence_excerpts": [
                        {
                            "evidence_id": evidence_id,
                            "allowed_use": evidence_by_id[evidence_id].allowed_use.value,
                            "content_depth": evidence_by_id[evidence_id].content_depth.value,
                            "excerpt": evidence_by_id[evidence_id].text[:1200],
                        }
                        for evidence_id in reference.evidence_ids
                        if evidence_id in evidence_by_id
                    ],
                }
            )
        requests.append(
            {
                "task": "Place citation markers for one article section.",
                "question": plan.charter.question,
                "section_id": section_id,
                "paragraphs": paragraphs,
                "references": references_for_section,
                "response_contract": {
                    "placements": [
                        {
                            "paragraph_id": "story-01-section-01-p01",
                            "reference_alias": "REF01_title",
                            "sentence_position": 1,
                        }
                    ],
                    "advice": ["optional"],
                },
            }
        )
    return requests


def _apply_placements(
    *,
    manuscript: ArticleManuscriptPackage,
    allowed_aliases: Mapping[str, List[str]],
    raw_placements: Sequence[Mapping[str, Any]],
    providers_available: bool,
    blockers: List[PublicationBlocker],
    warnings: List[str],
) -> List[CitationPlacement]:
    paragraphs = {
        paragraph.paragraph_id: paragraph.rendered_text
        for paragraph in manuscript.source_map
    }
    placements: List[CitationPlacement] = []
    if not providers_available:
        warnings.append(
            "citation placement advice rejected/unavailable; deterministic "
            "end-of-paragraph fallback used"
        )
    for paragraph_id in sorted(paragraphs):
        aliases = sorted(allowed_aliases.get(paragraph_id, []))
        if not aliases:
            continue
        if providers_available:
            matched = [
                item
                for item in raw_placements
                if item.get("paragraph_id") == paragraph_id
            ]
            used: set[str] = set()
            response_ok = True
            for item in matched:
                alias = str(item.get("reference_alias") or "")
                position = item.get("sentence_position")
                if alias not in aliases:
                    warnings.append(
                        f"placement for {paragraph_id!r} references unknown "
                        f"alias {alias!r}; advisory response rejected"
                    )
                    response_ok = False
                    continue
                if not isinstance(position, int) or position < 0:
                    warnings.append(
                        f"placement for {paragraph_id!r} alias {alias!r} has "
                        "an invalid sentence position; advisory response "
                        "rejected"
                    )
                    response_ok = False
                    continue
                if alias in used:
                    continue
                used.add(alias)
                placements.append(
                    CitationPlacement(
                        placement_id=f"place-{_digest(paragraph_id, alias)}",
                        paragraph_id=paragraph_id,
                        reference_alias=alias,
                        sentence_position=position,
                        fallback=False,
                        marker=f"[REF:{alias}]",
                    )
                )
            if not response_ok:
                for alias in aliases:
                    placements.append(
                        CitationPlacement(
                            placement_id=f"place-{_digest(paragraph_id, alias)}",
                            paragraph_id=paragraph_id,
                            reference_alias=alias,
                            sentence_position=len(
                                _sentence_offsets(paragraphs[paragraph_id])
                            ),
                            fallback=True,
                            marker=f"[REF:{alias}]",
                        )
                    )
                continue
            for alias in aliases:
                if alias not in used:
                    placements.append(
                        CitationPlacement(
                            placement_id=f"place-{_digest(paragraph_id, alias)}",
                            paragraph_id=paragraph_id,
                            reference_alias=alias,
                            sentence_position=len(
                                _sentence_offsets(paragraphs[paragraph_id])
                            ),
                            fallback=True,
                            marker=f"[REF:{alias}]",
                        )
                    )
        else:
            for alias in aliases:
                placements.append(
                    CitationPlacement(
                        placement_id=f"place-{_digest(paragraph_id, alias)}",
                        paragraph_id=paragraph_id,
                        reference_alias=alias,
                        sentence_position=len(
                            _sentence_offsets(paragraphs[paragraph_id])
                        ),
                        fallback=True,
                        marker=f"[REF:{alias}]",
                    )
                )
    return placements


def _build_front_matter_request(
    *,
    plan: ArticleDirectorPlan,
    manuscript: ArticleManuscriptPackage,
    references: Sequence[ReferenceRecord],
) -> Dict[str, Any]:
    return {
        "task": "Write article front matter. Organization-only.",
        "question": plan.charter.question,
        "charter_scope": plan.charter.scope,
        "goals": list(plan.charter.goals),
        "story": {
            "story_shape": plan.charter.scope,
            "purpose": "whole-article presentation",
        },
        "sections": [
            {
                "section_id": section.section_id,
                "heading": section.heading,
                "purpose": "section body",
                "paragraphs": [
                    {
                        "paragraph_id": paragraph.paragraph_id,
                        "rendered_text": paragraph.rendered_text,
                    }
                    for paragraph in section.paragraphs
                ],
            }
            for section in manuscript.body.sections
        ],
        "review_findings": [
            {
                "paragraph_id": finding.paragraph_id,
                "severity": finding.severity.value,
                "kind": finding.kind,
                "reason": finding.reason,
                "suggested_action": finding.suggested_action,
            }
            for finding in manuscript.findings
        ],
        "references": [
            {
                "reference_alias": item.reference_alias,
                "title": item.title,
                "year": item.year,
            }
            for item in references
        ],
        "response_contract": {
            "title": "string",
            "abstract_sentences": [
                {
                    "sentence": "string",
                    "paragraph_aliases": ["story-01-section-01-p01"],
                }
            ],
            "keywords": ["keyword"],
        },
    }


def _validate_front_matter(
    raw: Mapping[str, Any],
    *,
    manuscript: ArticleManuscriptPackage,
    warnings: List[str],
) -> Optional[FrontMatter]:
    title = str(raw.get("title") or "").strip()
    keywords = [str(item).strip() for item in raw.get("keywords") or []]
    if not title or _front_matter_unsafe(title):
        warnings.append(
            "front-matter title contains unsafe structure; advisory "
            "response rejected"
        )
        return None
    safe_keywords = []
    for keyword in keywords:
        if not keyword or _front_matter_unsafe(keyword):
            warnings.append(
                f"front-matter keyword {keyword!r} was dropped as unsafe"
            )
            continue
        safe_keywords.append(keyword)
    sentences = raw.get("abstract_sentences") or []
    if not sentences:
        return None
    paragraph_texts = {
        paragraph.paragraph_id: paragraph.rendered_text
        for paragraph in manuscript.source_map
    }
    normalized_sentences: List[Dict[str, Any]] = []
    for index, item in enumerate(sentences):
        if not isinstance(item, dict):
            warnings.append(
                f"front-matter sentence {index} was dropped because it is "
                "not an object"
            )
            continue
        sentence = str(item.get("sentence") or "").strip()
        aliases = [str(alias) for alias in (item.get("paragraph_aliases") or [])]
        if not sentence or not aliases:
            warnings.append(
                f"front-matter sentence {index} was dropped because text or "
                "paragraph aliases are missing"
            )
            continue
        unknown = [alias for alias in aliases if alias not in paragraph_texts]
        if unknown:
            warnings.append(
                f"front-matter sentence {index} references unknown paragraph "
                f"aliases {unknown}; sentence dropped"
            )
            continue
        if "[REF:" in sentence or "[VALUE:" in sentence:
            warnings.append(
                f"front-matter sentence {index} contains a forbidden marker; "
                "sentence dropped"
            )
            continue
        cited_text = " ".join(paragraph_texts[alias] for alias in aliases)
        unsupported = []
        for token in _MEASUREMENT_NUMBER_RE.findall(sentence):
            if token not in cited_text:
                unsupported.append(token)
        if unsupported:
            warnings.append(
                f"front-matter sentence {index} contains unsupported "
                f"number(s) {unsupported}; sentence dropped"
            )
            continue
        normalized_sentences.append(
            {"sentence": sentence, "paragraph_aliases": sorted(set(aliases))}
        )
    if not normalized_sentences:
        return None
    return FrontMatter(
        title=title,
        abstract_sentences=normalized_sentences,
        keywords=sorted(set(safe_keywords)),
        fallback=False,
    )


def _deterministic_front_matter(
    *,
    plan: ArticleDirectorPlan,
    manuscript: ArticleManuscriptPackage,
    warnings: List[str],
) -> FrontMatter:
    warnings.append(
        "front-matter provider unavailable/malformed/unsafe; deterministic "
        "fallback used"
    )
    abstract_sentences = []
    for section in manuscript.body.sections:
        if not section.paragraphs:
            continue
        paragraph = section.paragraphs[0]
        offsets = _sentence_offsets(paragraph.rendered_text)
        first_sentence = (
            paragraph.rendered_text[: offsets[0]]
            if offsets
            else paragraph.rendered_text
        )
        if first_sentence.strip():
            abstract_sentences.append(
                {
                    "sentence": first_sentence.strip(),
                    "paragraph_aliases": [paragraph.paragraph_id],
                }
            )
    title = plan.charter.question.strip().rstrip(".")
    return FrontMatter(
        title=title or "Article",
        abstract_sentences=abstract_sentences,
        keywords=list(plan.charter.goals),
        fallback=True,
    )


def _verify_quantitative_artifact(
    *,
    descriptor: Any,
    roots: Sequence[Path],
    reproducibility: ArticleReproducibilityPackage,
    blockers: List[PublicationBlocker],
    contract_figure_id: str,
) -> Tuple[Optional[Path], Optional[str]]:
    if not descriptor.sha256:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest('missing_descriptor_sha', descriptor.artifact_id)}",
                kind="missing_artifact_hash",
                message=(
                    f"figure {contract_figure_id!r} artifact "
                    f"{descriptor.artifact_id!r} has no SHA-256 in the "
                    "artifact inventory"
                ),
                artifact_ids=[descriptor.artifact_id],
            )
        )
        return None, None
    candidates: List[Path] = []
    for root in roots:
        root_path = Path(root)
        for candidate in (
            root_path / descriptor.path,
            root_path / descriptor.artifact_id,
        ):
            if _safe_within(candidate, root_path):
                resolved = candidate.resolve()
                if resolved not in candidates:
                    candidates.append(resolved)
    artifact_path = next((item for item in candidates if item.is_file()), None)
    if artifact_path is None:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest('missing_artifact', descriptor.artifact_id)}",
                kind="missing_quantitative_artifact",
                message=(
                    f"figure {contract_figure_id!r} artifact "
                    f"{descriptor.artifact_id!r} is not inside the trusted "
                    "artifact roots"
                ),
                artifact_ids=[descriptor.artifact_id],
            )
        )
        return None, None
    actual_sha = _sha256_file(artifact_path)
    if actual_sha != descriptor.sha256:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest('hash_mismatch', descriptor.artifact_id)}",
                kind="artifact_hash_mismatch",
                message=(
                    f"figure {contract_figure_id!r} artifact "
                    f"{descriptor.artifact_id!r} sha256 does not match the "
                    "artifact inventory"
                ),
                artifact_ids=[descriptor.artifact_id],
            )
        )
        return None, None
    lineage_root = next(
        (Path(root) for root in roots if _safe_within(artifact_path, Path(root))),
        None,
    )
    canonical_digest = (
        _canonical_lineage_digest(artifact_path, lineage_root)
        if lineage_root is not None
        else None
    )
    lineage_match = None
    for item in reproducibility.lineage:
        if item.artifact_id != descriptor.artifact_id:
            continue
        raw_replay_match = False
        if (
            item.matched
            and item.identity_kind == "byte_identical"
            and lineage_root is not None
        ):
            source_candidate = (lineage_root / item.relative_path).resolve()
            replay_candidate = (
                lineage_root / "fresh_replay" / item.relative_path
            ).resolve()
            raw_replay_match = (
                _safe_within(source_candidate, lineage_root)
                and _safe_within(replay_candidate, lineage_root)
                and source_candidate == artifact_path.resolve()
                and source_candidate.is_file()
                and replay_candidate.is_file()
                and _sha256_file(source_candidate) == actual_sha
                and _sha256_file(replay_candidate) == actual_sha
            )
        if item.source_sha256:
            if item.source_sha256 == actual_sha:
                pass
            elif (
                canonical_digest is not None and item.source_sha256 == canonical_digest
            ):
                pass
            elif raw_replay_match:
                pass
            else:
                continue
        if not item.matched or item.identity_kind not in {
            "byte_identical",
            "canonical_scientific_identity",
        }:
            continue
        compatible_article_ids = _lineage_article_ids_for_descriptor(
            descriptor, reproducibility
        )
        if compatible_article_ids is not None:
            if item.experiment_id not in compatible_article_ids:
                continue
        elif (
            descriptor.source_experiment_ids
            and item.experiment_id not in descriptor.source_experiment_ids
        ):
            continue
        lineage_match = item
        break
    if lineage_match is None:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest('lineage_missing', descriptor.artifact_id)}",
                kind="artifact_lineage_missing",
                message=(
                    f"figure {contract_figure_id!r} artifact "
                    f"{descriptor.artifact_id!r} lacks a matched Stage 12B "
                    "lineage entry with the exact source hash and compatible "
                    "experiment"
                ),
                artifact_ids=[descriptor.artifact_id],
            )
        )
        return None, None
    return artifact_path, actual_sha


def _physical_to_article_experiment_ids(
    reproducibility: ArticleReproducibilityPackage,
) -> Dict[str, str]:
    """Unambiguous physical -> Article experiment mapping from 12B records."""

    mapping: Dict[str, set[str]] = {}
    for record in reproducibility.critical_experiments:
        for physical_id in record.physical_experiment_ids:
            mapping.setdefault(physical_id, set()).add(record.experiment_id)
    return {
        physical_id: next(iter(article_ids))
        for physical_id, article_ids in mapping.items()
        if len(article_ids) == 1
    }


def _lineage_article_ids_for_descriptor(
    descriptor: Any,
    reproducibility: ArticleReproducibilityPackage,
) -> Optional[set[str]]:
    """Article experiment IDs compatible with a descriptor's physical IDs.

    Returns ``None`` for the legacy same-ID case (caller falls back to the
    old direct comparison); returns a set of Article IDs when the descriptor's
    physical IDs resolve unambiguously through Stage 12B critical records.
    """

    if not descriptor.source_experiment_ids:
        return None
    physical_to_article = _physical_to_article_experiment_ids(reproducibility)
    resolved: set[str] = set()
    resolved_all = True
    for physical_id in descriptor.source_experiment_ids:
        article_id = physical_to_article.get(physical_id)
        if article_id is None:
            resolved_all = False
            continue
        resolved.add(article_id)
    if not resolved_all or not resolved:
        return set()
    return resolved


def _canonical_lineage_digest(
    path: Path,
    root: Path,
) -> Optional[str]:
    """Recompute the Stage 12B canonical scientific JSON digest of a file.

    Stage 12B replay manifests store canonical scientific JSON digests
    (volatile fields scrubbed, path references canonicalized), whereas Stage 9
    descriptors and filesystem checks use raw byte SHA-256.  This helper
    reproduces the replay canonical digest so the presentation adapter can
    compare the correct hash semantics without weakening either check.
    """

    from optomind_optics.harness.replay import (
        _load_experiment_path_index,
        _scientific_digest,
    )

    try:
        path_index = _load_experiment_path_index(root)
    except Exception:
        path_index = None
    try:
        return _scientific_digest(
            path,
            root=root,
            path_index=path_index,
        )
    except Exception:
        return None


def _load_spectrum_rows(
    payload: Mapping[str, Any], selected_fields: Sequence[str]
) -> List[Dict[str, Any]]:
    """Load aligned rows from a genuine columnar SIMULATION_RESULT.json.

    Supports ``wavelengths_nm`` and nested/flat channels such as
    ``channels.angle=45|pol=s.R`` (and p equivalents).  All selected series
    must share the wavelength grid and be finite numbers; malformed or
    unequal columns are rejected instead of silently shifting data.  Scalar
    metadata fields are refused here so metadata cannot be confused with a
    spectrum series.
    """

    wavelengths = payload.get("wavelengths_nm")
    channels = payload.get("channels")
    if not isinstance(wavelengths, list) or not wavelengths:
        raise ValueError(
            "SIMULATION_RESULT.json wavelengths_nm must be a non-empty list"
        )
    try:
        wavelength_values = [float(value) for value in wavelengths]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "SIMULATION_RESULT.json wavelengths_nm contains non-numeric values"
        ) from exc
    if any(not math.isfinite(value) for value in wavelength_values):
        raise ValueError(
            "SIMULATION_RESULT.json wavelengths_nm contains non-finite values"
        )
    if not isinstance(channels, Mapping):
        raise ValueError("SIMULATION_RESULT.json channels must be a mapping")

    columns: Dict[str, List[float]] = {}
    for field in selected_fields:
        if field == "wavelengths_nm":
            columns[field] = wavelength_values
            continue
        if not isinstance(field, str) or not field.startswith("channels."):
            raise ValueError(
                f"field {field!r} is not a spectrum series; scalar metadata "
                "cannot be mixed with spectrum columns"
            )
        parts = field.split(".")
        if len(parts) < 3:
            raise ValueError(f"malformed spectrum field {field!r}")
        observable = parts[-1]
        channel_key = ".".join(parts[1:-1])
        channel = channels.get(channel_key)
        if not isinstance(channel, Mapping):
            raise ValueError(
                f"spectrum field {field!r} references unknown channel "
                f"{channel_key!r}"
            )
        series = channel.get(observable)
        if not isinstance(series, list):
            raise ValueError(
                f"spectrum field {field!r} has no series under "
                f"channels.{channel_key}.{observable}"
            )
        try:
            values = [float(value) for value in series]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"spectrum field {field!r} contains non-numeric values"
            ) from exc
        if any(not math.isfinite(value) for value in values):
            raise ValueError(f"spectrum field {field!r} contains non-finite values")
        if len(values) != len(wavelength_values):
            raise ValueError(
                f"spectrum field {field!r} length {len(values)} does not "
                f"match wavelengths_nm length {len(wavelength_values)}"
            )
        columns[field] = values
    if not columns:
        return []
    return [
        {field: columns[field][index] for field in columns}
        for index in range(len(wavelength_values))
    ]


def _finite_scalar(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"field {field!r} must be a numeric scalar")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"field {field!r} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"field {field!r} is non-finite")
    return number


def _strip_field_prefix(field: str, prefix: str) -> str:
    if field == prefix:
        raise ValueError(
            f"field {field!r} is only a semantic wrapper, not a data field"
        )
    if not field.startswith(prefix + "."):
        raise ValueError(
            f"field {field!r} has a wrong semantic wrapper for the "
            f"artifact schema (expected {prefix!r})"
        )
    return field[len(prefix) + 1 :]


def _resolve_schema_numeric_rows(
    payload: Mapping[str, Any],
    schema: str,
    selected_fields: Sequence[str],
) -> Optional[List[Dict[str, Any]]]:
    """Resolve selected fields against known TMM artifact schemas."""

    if schema == "tmm-robustness-report.v1":
        row: Dict[str, Any] = {}
        for field in selected_fields:
            key = _strip_field_prefix(field, "robustness_report")
            row[field] = _finite_scalar(payload.get(key), field)
        return [row]
    if schema == "tmm-objective-report.v1":
        attainment = payload.get("target_attainment")
        if not isinstance(attainment, Mapping):
            raise ValueError("tmm-objective-report.v1 has no target_attainment mapping")
        row = {}
        for field in selected_fields:
            rest = _strip_field_prefix(field, "objective_report")
            if rest in {"aggregate_soft_score", "weighted_directional_loss"}:
                row[field] = _finite_scalar(payload.get(rest), field)
                continue
            parts = rest.split(".")
            if len(parts) != 3 or parts[0] != "target_attainment":
                raise ValueError(
                    f"field {field!r} is not an objective_report "
                    "target_attainment scalar path"
                )
            _, objective_id, attribute = parts
            entry = attainment.get(objective_id)
            if not isinstance(entry, Mapping):
                raise ValueError(
                    f"objective {objective_id!r} not found in " "target_attainment"
                )
            if attribute not in entry:
                raise ValueError(
                    f"objective {objective_id!r} has no field " f"{attribute!r}"
                )
            row[field] = _finite_scalar(entry[attribute], field)
        return [row]
    if schema == "physics-acceptance-certificate-v1":
        permitted_paths = {
            "physics_audit.energy_conservation_max_abs_error",
            "physics_audit.minimum_observable",
            "physics_audit.maximum_observable",
            "physics_audit.nonfinite_value_count",
            "spectral_convergence.final_points",
            "independent_solver_check.maximum_absolute_difference",
            "independent_solver_check.tolerance",
        }
        row = {}
        for field in selected_fields:
            rest = _strip_field_prefix(field, "physics_certificate")
            if rest not in permitted_paths:
                raise ValueError(
                    f"field {field!r} is not a permitted physics certificate "
                    "audit scalar path"
                )
            value: Any = payload
            for segment in rest.split("."):
                if not isinstance(value, Mapping) or segment not in value:
                    raise ValueError(
                        f"physics certificate has no field {rest!r}"
                    )
                value = value[segment]
            row[field] = _finite_scalar(value, field)
        return [row]
    if schema == "optical-design-portfolio.v1":
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError("optical-design-portfolio.v1 has no candidates list")
        by_id: Dict[str, Mapping[str, Any]] = {}
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                raise ValueError("portfolio candidate must be an object")
            candidate_id = candidate.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                raise ValueError("portfolio candidate has no candidate_id")
            if candidate_id in by_id:
                raise ValueError(
                    f"duplicate candidate_id {candidate_id!r} in portfolio"
                )
            by_id[candidate_id] = candidate
        rows_by_candidate: Dict[str, Dict[str, Any]] = {}
        for field in selected_fields:
            candidate_token, separator, attribute = field.rpartition(".")
            if not separator or not candidate_token or not attribute:
                raise ValueError(f"field {field!r} is not a candidate.attribute path")
            candidate = by_id.get(candidate_token)
            if candidate is None:
                raise ValueError(
                    f"unknown portfolio candidate {candidate_token!r} "
                    f"for field {field!r}"
                )
            if attribute not in candidate:
                raise ValueError(
                    f"portfolio candidate {candidate_token!r} has no "
                    f"field {attribute!r}"
                )
            rows_by_candidate.setdefault(candidate_token, {})[field] = _finite_scalar(
                candidate[attribute], field
            )
        # A table FigureContract may intentionally compare several candidates.
        # Keep one sparse row per candidate; the markdown renderer leaves
        # fields that do not apply to that candidate blank. A scalar chart
        # still receives one candidate because its figure contract selects one.
        if len(rows_by_candidate) == 1:
            return [next(iter(rows_by_candidate.values()))]
        return [
            {"candidate_id": candidate_id, **row}
            for candidate_id, row in sorted(rows_by_candidate.items())
        ]
    return None


def _validate_numeric_rows(
    rows: Sequence[Mapping[str, Any]],
    selected_fields: Sequence[str],
    *,
    allow_sparse: bool = False,
) -> List[Dict[str, Any]]:
    if not rows:
        raise ValueError("no numeric response series available")
    for field in selected_fields:
        for row in rows:
            if field not in row and allow_sparse:
                continue
            if field not in row:
                raise ValueError(f"field {field!r} is missing from a numeric data row")
            _finite_scalar(row[field], field)
    return [dict(row) for row in rows]


def _load_numeric_rows(
    path: Path, selected_fields: Sequence[str]
) -> List[Dict[str, Any]]:
    suffix = path.suffix.lower()
    rows: List[Dict[str, Any]] = []
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            isinstance(payload, dict)
            and isinstance(payload.get("wavelengths_nm"), list)
            and isinstance(payload.get("channels"), dict)
        ):
            return _load_spectrum_rows(payload, selected_fields)
        schema = (
            str(payload.get("schema_version") or "")
            if isinstance(payload, dict)
            else ""
        )
        resolved = _resolve_schema_numeric_rows(
            payload if isinstance(payload, Mapping) else {},
            schema,
            selected_fields,
        )
        if resolved is not None:
            return _validate_numeric_rows(
                resolved,
                selected_fields,
                allow_sparse=schema == "optical-design-portfolio.v1",
            )
        records = payload if isinstance(payload, list) else payload.get("data", [])
        if not isinstance(records, list):
            records = [payload]
        for record in records:
            if isinstance(record, dict):
                rows.append(
                    {
                        field: record.get(field)
                        for field in selected_fields
                        if field in record
                    }
                )
    else:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = (
                csv.DictReader(handle, delimiter="\t")
                if suffix == ".tsv"
                else csv.DictReader(handle)
            )
            for record in reader:
                rows.append(
                    {
                        field: record.get(field)
                        for field in selected_fields
                        if field in record
                    }
                )
    if not rows:
        raise ValueError("no numeric response series available")
    return rows


def _render_svg_plot(
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
    label: str,
) -> str:
    width, height = 640, 420
    margin = 56
    plot_width = width - 2 * margin
    plot_height = height - 2 * margin
    has_wavelengths = "wavelengths_nm" in fields
    response_fields = [field for field in fields if field != "wavelengths_nm"]
    series = []
    for field in response_fields:
        values = []
        for row in rows:
            try:
                values.append(float(row.get(field)))
            except (TypeError, ValueError):
                continue
        if values:
            series.append((field, values))
    if not series:
        raise ValueError("no numeric response series available")
    if not has_wavelengths and all(len(values) == 1 for _, values in series):
        return _render_scalar_bar_svg(series, label)
    if has_wavelengths:
        x_values: List[float] = []
        for row in rows:
            try:
                x_values.append(float(row.get("wavelengths_nm")))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "spectrum wavelengths_nm must be numeric for every row"
                ) from exc
        if any(len(values) != len(x_values) for _, values in series):
            raise ValueError(
                "spectrum response series length does not match " "wavelengths_nm"
            )
        x_low, x_high = min(x_values), max(x_values)
        x_span = (x_high - x_low) or 1.0
    else:
        x_values = None
        x_low = 0.0
        x_span = 1.0
    all_values = [value for _, values in series for value in values]
    low, high = min(all_values), max(all_values)
    span = (high - low) or 1.0
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    palette = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]
    for index, (field, values) in enumerate(series):
        points = []
        for point_index, value in enumerate(values):
            if x_values is None:
                x = margin + (point_index / max(len(values) - 1, 1) * plot_width)
            else:
                x = margin + ((x_values[point_index] - x_low) / x_span * plot_width)
            y = margin + plot_height - ((value - low) / span * plot_height)
            points.append(f"{x:.1f},{y:.1f}")
        color = palette[index % len(palette)]
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="2" '
            f'points="{" ".join(points)}"/>'
        )
        for line_index, line in enumerate(_wrap_text(field, 30)):
            parts.append(
                f'<text x="{margin}" y="{margin + index * 48 + line_index * 14}" '
                f'font-family="sans-serif" font-size="12" fill="{color}">'
                f"{_xml_escape(line)}</text>"
            )
    for line_index, line in enumerate(_wrap_text(label, 90)):
        parts.append(
            f"<text x='{margin}' y='{height - 10 + line_index * 14}' "
            f"font-family='sans-serif' font-size='12'>"
            f"{_xml_escape(line)}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def _scalar_domain(values: Sequence[float]) -> Tuple[float, float]:
    """Truthful visual domain with an explicit zero baseline."""

    if not values:
        raise ValueError("no scalar values for domain")
    low, high = min(values), max(values)
    if all(value >= 0.0 for value in values):
        if all(value <= 1.0 for value in values):
            return 0.0, 1.0
        return 0.0, high
    if all(value <= 0.0 for value in values):
        return low, 0.0
    return low, high


def _compact_scalar_label(field: str) -> str:
    """Human-readable compact label; exact path stays in SVG metadata."""

    if field == "robustness_report.mean_soft_score":
        return "Mean soft score"
    if field == "robustness_report.nominal_soft_score":
        return "Nominal soft score"
    if field == "robustness_report.robustness_score":
        return "Robustness score"
    if field == "robustness_report.worst_soft_score":
        return "Worst soft score"
    objective = re.match(
        r"^objective_report\.target_attainment\."
        r"(canonical_a_\d+_\d+_at_(?:least|most)_(mean|worst_case)_(\d+)_(s|p)_\d+_\d+)"
        r"\.observed$",
        field,
    )
    if objective is not None:
        aggregation = "Mean" if objective.group(2) == "mean" else "Worst-case"
        angle = objective.group(3)
        polarization = objective.group(4).upper()
        return f"{aggregation} A, {angle} deg, {polarization}"
    if "." in field:
        candidate_token, separator, attribute = field.rpartition(".")
        if separator and attribute and candidate_token:
            human = attribute.replace("_", " ").strip().title()
            if human and len(human) <= 40 and " " in human:
                return human
            if human and len(human) <= 40:
                return human
    fallback = field.rsplit(".", 1)[-1].replace("_", " ").strip()
    if fallback and len(fallback) <= 40 and " " in fallback:
        return fallback.title()
    return "Metric value"


def _compact_candidate_display(value: Any) -> str:
    raw = str(value or "").strip()
    parts = raw.split("__")
    if len(parts) >= 2:
        method = (parts[-2] if len(parts) >= 3 else parts[0]).replace("_", "-").strip("-") or "candidate"
        token = parts[-1].replace("_", "-").strip("-")
        if token:
            if token.casefold() == "baseline":
                return "baseline"
            if method.casefold() in {"different", "differen"}:
                method = "variant"
            return f"{method} {token[:10]}"
    return raw.replace("_", " ")


def _render_scalar_bar_svg(
    series: Sequence[Tuple[str, List[float]]],
    label: str,
) -> str:
    """Truthful horizontal bar/category SVG for scalar metric summaries."""

    width, height = 640, 420
    margin = 24
    label_width = 210
    plot_left = margin + label_width
    plot_right = width - margin
    plot_width = plot_right - plot_left
    top = margin
    bottom = height - 36
    plot_height = bottom - top
    values = [value for _, field_values in series for value in field_values]
    domain_low, domain_high = _scalar_domain(values)
    span = (domain_high - domain_low) or 1.0

    def x_position(value: float) -> float:
        return plot_left + (value - domain_low) / span * plot_width

    zero_x = x_position(0.0)
    row_height = plot_height / max(len(series), 1)
    palette = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]
    metadata = [
        f"artifact: {_xml_escape(label)}",
        f"domain: [{domain_low:.6g}, {domain_high:.6g}]",
    ]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for index, (field, field_values) in enumerate(series):
        value = field_values[0]
        compact = _compact_scalar_label(field)
        metadata.append(f"field: {_xml_escape(field)} = {value:.9g}")
        parts.append(f"<title>{_xml_escape(field)}</title>")
        parts.append(
            f"<desc>{_xml_escape(f'{compact} = {value:.9g} ({field})')}" "</desc>"
        )
        bar_y = top + index * row_height + row_height * 0.30
        bar_height = max(row_height * 0.40, 8.0)
        start_x = zero_x
        end_x = x_position(value)
        left = min(start_x, end_x)
        bar_width = max(abs(end_x - start_x), 1.0)
        color = palette[index % len(palette)]
        parts.append(
            f'<rect x="{left:.1f}" y="{bar_y:.1f}" '
            f'width="{bar_width:.1f}" height="{bar_height:.1f}" '
            f'fill="{color}"/>'
        )
        rendered_value = f"{value:.4g}"
        estimated_text_width = max(24.0, len(rendered_value) * 7.0)
        value_x = max(left + bar_width + 5, end_x + 5)
        text_anchor = "start"
        text_fill = color
        if value_x + estimated_text_width > width - margin:
            value_x = max(left + 5, end_x - 5)
            text_anchor = "end"
            text_fill = "#ffffff" if bar_width >= estimated_text_width + 12 else "#333333"
        parts.append(
            f'<text x="{value_x:.1f}" y="{bar_y + bar_height / 2 + 4:.1f}" '
            f'font-family="sans-serif" font-size="11" fill="{text_fill}" '
            f'text-anchor="{text_anchor}">{_xml_escape(rendered_value)}</text>'
        )
        for line_index, line in enumerate(_wrap_text(compact, 30)):
            parts.append(
                f'<text x="{margin}" y="{bar_y + 14 + line_index * 13:.1f}" '
                f'font-family="sans-serif" font-size="11" fill="#333333">'
                f"{_xml_escape(line)}</text>"
            )
    parts.append(
        f'<line x1="{zero_x:.1f}" y1="{top}" x2="{zero_x:.1f}" '
        f'y2="{bottom}" stroke="#999999" stroke-dasharray="4 4"/>'
    )
    visible_footer = Path(label).name if label else "Scalar metrics"
    for line_index, line in enumerate(_wrap_text(visible_footer, 90)):
        parts.append(
            f"<text x='{margin}' y='{height - 12 + line_index * 14}' "
            f"font-family='sans-serif' font-size='11' fill='#666666'>"
            f"{_xml_escape(line)}</text>"
        )
    parts.append(f"<metadata>{_xml_escape('; '.join(metadata))}</metadata>")
    parts.append("</svg>")
    return "\n".join(parts)


def _render_markdown_table(
    rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
) -> str:
    if not rows:
        raise ValueError("no numeric data rows for table")
    normalized_rows = [dict(row) for row in rows]
    if any("candidate_id" in row for row in normalized_rows):
        metric_fields: List[str] = []
        compact_rows: List[Dict[str, Any]] = []
        for row in normalized_rows:
            compact = {
                "candidate_id": _compact_candidate_display(
                    row.get("candidate_id")
                )
            }
            for field, value in row.items():
                if field == "candidate_id":
                    continue
                metric = field.rsplit(".", 1)[-1]
                compact[metric] = value
                if metric not in metric_fields:
                    metric_fields.append(metric)
            compact_rows.append(compact)
        normalized_rows = compact_rows
        fields = ["candidate_id", *metric_fields]
    display_labels = [
        "Candidate" if field == "candidate_id" else _compact_scalar_label(field)
        for field in fields
    ]
    header = (
        "| " + " | ".join(_md_escape_cell(label) for label in display_labels) + " |"
    )
    separator = "| " + " | ".join(["---"] * len(fields)) + " |"

    def display_cell(value: Any) -> Any:
        if isinstance(value, bool) or value in (None, ""):
            return value
        if isinstance(value, (int, float)):
            number = float(value)
            return f"{number:.6g}" if math.isfinite(number) else value
        return value

    body = []
    for row in normalized_rows:
        body.append(
            "| "
            + " | ".join(
                _md_escape_cell(display_cell(row.get(field, ""))) for field in fields
            )
            + " |"
        )
    return "\n".join([header, separator, *body])


def _group_artifact_bindings(
    bindings: Sequence[ArtifactFieldBinding],
    *,
    figure_id: str,
    warnings: List[str],
) -> List[ArtifactFieldBinding]:
    grouped: Dict[str, List[str]] = {}
    duplicate_artifacts: set[str] = set()
    for binding in bindings:
        fields = grouped.setdefault(binding.artifact_id, [])
        if fields:
            duplicate_artifacts.add(binding.artifact_id)
        fields.extend(binding.selected_fields)
    for artifact_id in sorted(duplicate_artifacts):
        warnings.append(
            f"figure {figure_id!r} merged duplicate artifact binding "
            f"{artifact_id!r}"
        )
    return [
        ArtifactFieldBinding(
            artifact_id=artifact_id,
            selected_fields=sorted(set(fields)),
        )
        for artifact_id, fields in grouped.items()
    ]


def _render_synthesized_diagram(
    panel_intents: Sequence[str],
    claim_statements: Sequence[str],
) -> str:
    entries: List[tuple[str, str, str]] = []
    stage_index = 1
    for intent in panel_intents:
        text = str(intent).strip()
        flow_text = re.sub(r"^illustrate\s+the\s+flow\s+from\s+", "", text, flags=re.I)
        stages = [
            part.strip(" .")
            for part in re.split(r"\s+(?:to|and\s+final)\s+", flow_text, flags=re.I)
            if part.strip(" .")
        ]
        if len(stages) < 2:
            stages = [text]
        for stage in stages:
            entries.append((f"STAGE {stage_index}", stage, "#2563eb"))
            stage_index += 1
    for index, statement in enumerate(claim_statements, 1):
        entries.append((f"SUPPORTED CLAIM {index}", str(statement), "#15803d"))
    if not entries:
        entries.append(("CONCEPT", "No conceptual stages were supplied.", "#4b5563"))

    wrapped_entries: List[tuple[str, List[str], str, int]] = []
    for label, text, color in entries:
        wrapped = _wrap_text(text, 68) or [""]
        height = max(68, 42 + 18 * len(wrapped))
        wrapped_entries.append((label, wrapped, color, height))
    content_height = sum(item[3] for item in wrapped_entries)
    connector_height = 28 * max(0, len(wrapped_entries) - 1)
    svg_height = max(420, 76 + content_height + connector_height + 28)

    lines: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="{svg_height}" '
        f'viewBox="0 0 640 {svg_height}">',
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" '
        'refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" '
        'fill="#6b7280"/></marker></defs>',
        '<rect width="100%" height="100%" fill="white"/>',
        '<text x="24" y="32" font-family="sans-serif" font-size="14" '
        'font-weight="bold">Synthesized conceptual diagram (not measured '
        "data)</text>",
    ]
    y = 58
    for index, (label, wrapped, color, height) in enumerate(wrapped_entries):
        lines.append(
            f'<rect x="36" y="{y}" width="568" height="{height}" rx="4" '
            f'fill="#f8fafc" stroke="{color}" stroke-width="2"/>'
        )
        lines.append(
            f'<text x="54" y="{y + 24}" font-family="sans-serif" font-size="11" '
            f'font-weight="bold" fill="{color}">{_xml_escape(label)}</text>'
        )
        text_y = y + 46
        for wrapped_line in wrapped:
            lines.append(
                f'<text x="54" y="{text_y}" font-family="sans-serif" '
                f'font-size="12" fill="#111827">{_xml_escape(wrapped_line)}</text>'
            )
            text_y += 18
        if index < len(wrapped_entries) - 1:
            arrow_y = y + height
            lines.append(
                f'<line x1="320" y1="{arrow_y + 4}" x2="320" '
                f'y2="{arrow_y + 24}" stroke="#6b7280" stroke-width="2" '
                'marker-end="url(#arrow)"/>'
            )
        y += height + 28
    lines.append("</svg>")
    return "\n".join(lines)


def _final_safety_check(
    *,
    reader_markdown: str,
    front_matter: FrontMatter,
    manuscript: ArticleManuscriptPackage,
    plan: ArticleDirectorPlan,
    blockers: List[PublicationBlocker],
) -> None:
    if front_matter is not None:
        text = " ".join(
            [front_matter.title]
            + [item["sentence"] for item in front_matter.abstract_sentences]
        )
        if "[REF:" in text or "[VALUE:" in text:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('front_matter_marker')}",
                    kind="forbidden_marker",
                    message="front matter contains an internal marker",
                )
            )
        allowed_text = " ".join(
            [plan.charter.question]
            + [paragraph.rendered_text for paragraph in manuscript.source_map]
        )
        for token in _MEASUREMENT_NUMBER_RE.findall(text):
            if token not in allowed_text:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('front_matter_number', token)}",
                        kind="unsupported_numeric",
                        message=(
                            f"front matter contains unsupported exact number "
                            f"{token!r}"
                        ),
                    )
                )


def compute_presentation_package_id(
    *,
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    review_id: str,
    result_id: str,
    manuscript_body_id: str,
    reproducibility_package_id: str,
    story_id: str,
    status: str,
    citations: Sequence[CitationRecord | Mapping[str, Any]],
    references: Sequence[ReferenceRecord | Mapping[str, Any]],
    placements: Sequence[CitationPlacement | Mapping[str, Any]],
    front_matter: Optional[FrontMatter | Mapping[str, Any]],
    visuals: Sequence[RenderedVisual | Mapping[str, Any]],
    reader_markdown: str,
    blockers: Sequence[PublicationBlocker | Mapping[str, Any]],
    warnings: Sequence[str],
    errors: Sequence[str],
    attempts: int,
    literature_supplement_id: str = "",
) -> str:
    def _models(values: Sequence[Any], model_type: Any) -> List[Any]:
        return [
            item if isinstance(item, model_type) else model_type.model_validate(item)
            for item in values
        ]

    front = (
        front_matter
        if isinstance(front_matter, FrontMatter)
        else (
            FrontMatter.model_validate(front_matter)
            if front_matter is not None
            else None
        )
    )
    return _digest(
        str(plan_id),
        str(ledger_id),
        str(architecture_id),
        str(review_id),
        str(result_id),
        str(manuscript_body_id),
        str(reproducibility_package_id),
        str(story_id),
        str(status),
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(citations, CitationRecord)
        ],
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(references, ReferenceRecord)
        ],
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(placements, CitationPlacement)
        ],
        _canonical_json(front.model_dump(mode="json")) if front is not None else "",
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(visuals, RenderedVisual)
        ],
        str(reader_markdown),
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(blockers, PublicationBlocker)
        ],
        [str(item) for item in warnings],
        [str(item) for item in errors],
        int(attempts),
        str(literature_supplement_id),
    )


def _render_reader_manuscript(
    *,
    front_matter: FrontMatter,
    manuscript: ArticleManuscriptPackage,
    citations: Sequence[CitationRecord],
    references: Sequence[ReferenceRecord],
    placements: Sequence[CitationPlacement],
    visuals: Sequence[RenderedVisual],
) -> str:
    blocks: List[str] = []
    blocks.append(f"# {_md_escape(front_matter.title)}\n")
    if front_matter.abstract_sentences:
        abstract = " ".join(
            item["sentence"] for item in front_matter.abstract_sentences
        )
        blocks.append(f"**Abstract.** {abstract}\n")
    if front_matter.keywords:
        blocks.append(
            "**Keywords:** "
            + ", ".join(_md_escape(item) for item in front_matter.keywords)
            + "\n"
        )
    paragraphs_by_section: Dict[str, List[Any]] = {}
    for paragraph in manuscript.source_map:
        paragraphs_by_section.setdefault(paragraph.section_id, []).append(paragraph)
    for section in manuscript.body.sections:
        section_paragraphs = paragraphs_by_section.get(section.section_id, [])
        section_visuals = [
            visual for visual in visuals if visual.section_id == section.section_id
        ]
        body = ""
        placed: set[str] = set()
        for paragraph in section_paragraphs:
            rendered = _render_reader_paragraph(
                paragraph.paragraph_id,
                paragraph.rendered_text,
                placements,
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
    references_section = []
    for reference in references:
        authors = (
            ", ".join(reference.authors) if reference.authors else "Unknown author"
        )
        parts = [
            f"[{reference.reference_alias}] {_md_escape(authors)}",
            f"({_md_escape(str(reference.year))})" if reference.year else "",
            _md_escape(reference.title),
        ]
        if reference.doi:
            parts.append(f"https://doi.org/{_md_escape(reference.doi)}")
        elif reference.venue:
            parts.append(_md_escape(reference.venue))
        references_section.append(" ".join(part for part in parts if part) + ".")
    if references_section:
        blocks.append("## References\n\n" + "\n\n".join(references_section))
    return "\n\n".join(blocks)


def _normalize_duplicate_citations(
    package: ArticlePresentationPackage,
    manuscript: ArticleManuscriptPackage,
) -> ArticlePresentationPackage:
    """Merge duplicate paragraph/reference placements before final validation."""

    seen: set[tuple[str, str]] = set()
    placements: List[CitationPlacement] = []
    for placement in package.placements:
        key = (placement.paragraph_id, placement.reference_alias)
        if key in seen:
            continue
        seen.add(key)
        placements.append(placement)
    if len(placements) == len(package.placements):
        return package
    citations: List[CitationRecord] = []
    seen_citations: set[tuple[str, str]] = set()
    for citation in package.citations:
        key = (citation.paragraph_id, citation.reference_alias)
        if key not in seen or key in seen_citations:
            continue
        seen_citations.add(key)
        citations.append(citation)
    citations_by_alias: Dict[str, List[CitationRecord]] = {}
    for citation in citations:
        citations_by_alias.setdefault(citation.reference_alias, []).append(citation)
    references: List[ReferenceRecord] = []
    for reference in package.references:
        rows = citations_by_alias.get(reference.reference_alias, [])
        if not rows:
            continue
        references.append(
            reference.model_copy(
                update={
                    "paragraph_ids": sorted({item.paragraph_id for item in rows}),
                    "claim_ids": sorted({item.claim_id for item in rows if item.claim_id}),
                    "hypothesis_ids": sorted({item.hypothesis_id for item in rows if item.hypothesis_id}),
                    "evidence_ids": sorted({item.evidence_id for item in rows}),
                    "support_semantics": sorted({item.support_semantics for item in rows}),
                    "content_depth": sorted({item.content_depth for item in rows}),
                }
            )
        )
    reader = _render_reader_manuscript(
        front_matter=package.front_matter,
        manuscript=manuscript,
        citations=citations,
        references=references,
        placements=placements,
        visuals=package.visuals,
    )
    warnings = list(package.warnings)
    warning = "duplicate citation placements merged deterministically"
    if warning not in warnings:
        warnings.append(warning)
    status: Literal["ready", "ready_with_findings", "blocked"] = (
        "ready_with_findings" if warnings else "ready"
    )
    package_id = compute_presentation_package_id(
        plan_id=package.plan_id,
        ledger_id=package.ledger_id,
        architecture_id=package.architecture_id,
        review_id=package.review_id,
        result_id=package.result_id,
        manuscript_body_id=package.manuscript_body_id,
        reproducibility_package_id=package.reproducibility_package_id,
        story_id=package.story_id,
        status=status,
        citations=citations,
        references=references,
        placements=placements,
        front_matter=package.front_matter,
        visuals=package.visuals,
        reader_markdown=reader,
        blockers=[],
        warnings=warnings,
        errors=[],
        attempts=package.attempts,
        literature_supplement_id=package.literature_supplement_id,
    )
    return package.model_copy(
        update={
            "package_id": package_id,
            "status": status,
            "citations": citations,
            "references": references,
            "placements": placements,
            "reader_markdown": reader,
            "blockers": [],
            "errors": [],
            "warnings": warnings,
        }
    )


def _verify_body_invariant(
    *,
    manuscript: ArticleManuscriptPackage,
    reader_markdown: str,
    placements: Sequence[CitationPlacement],
    references: Sequence[ReferenceRecord],
    blockers: List[PublicationBlocker],
) -> None:
    """Exact paragraph restoration and placement/reference resolution."""

    placement_markers = [item.marker for item in placements]
    placement_keys = {(item.paragraph_id, item.reference_alias) for item in placements}
    if len(placement_keys) != len(placements):
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest('duplicate_paragraph_placement')}",
                kind="duplicate_placement",
                message=(
                    "duplicate (paragraph_id, reference_alias) citation " "placements"
                ),
            )
        )
    reference_aliases = {item.reference_alias for item in references}
    for paragraph in manuscript.source_map:
        rendered = _render_reader_paragraph(
            paragraph.paragraph_id,
            paragraph.rendered_text,
            placements,
        )
        expected_public_text = _strip_internal_source_markers(
            paragraph.rendered_text
        )
        if _strip_citation_markers(rendered) != expected_public_text:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('body_invariant', paragraph.paragraph_id)}",
                    kind="paragraph_mutation",
                    message=(
                        f"removing markers from paragraph "
                        f"{paragraph.paragraph_id!r} does not reproduce the "
                        "accepted text"
                    ),
                    paragraph_ids=[paragraph.paragraph_id],
                )
            )
        if rendered not in reader_markdown:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('reader_missing_paragraph', paragraph.paragraph_id)}",
                    kind="reader_paragraph_missing",
                    message=(
                        f"reader manuscript is missing paragraph "
                        f"{paragraph.paragraph_id!r} with its markers"
                    ),
                    paragraph_ids=[paragraph.paragraph_id],
                )
            )
        paragraph_placements = [
            item for item in placements if item.paragraph_id == paragraph.paragraph_id
        ]
        for item in paragraph_placements:
            if rendered.count(item.marker) != 1:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest('paragraph_placement_count', item.marker, paragraph.paragraph_id)}",
                        kind="duplicate_placement",
                        message=(
                            f"marker {item.marker!r} must appear exactly once "
                            f"in paragraph {paragraph.paragraph_id!r}"
                        ),
                        paragraph_ids=[paragraph.paragraph_id],
                    )
                )
    if _INTERNAL_SOURCE_MARKER_RE.search(reader_markdown):
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest('internal_source_marker_leak')}",
                kind="internal_marker_leak",
                message="reader manuscript contains a writer-only claim alias",
            )
        )
    if re.search(r"\d\.\[REF:[A-Za-z0-9_]+\]\d", reader_markdown):
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest('citation_splits_decimal')}",
                kind="citation_splits_numeric_literal",
                message="citation marker splits a decimal numeric literal",
            )
        )
    for marker in _CITATION_MARKER_RE.findall(reader_markdown):
        if marker not in placement_markers:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('unknown_reader_marker', marker)}",
                    kind="unknown_citation",
                    message=f"reader manuscript contains unregistered marker {marker!r}",
                )
            )
        alias = marker[len("[REF:") : -1]
        if alias not in reference_aliases:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('unresolved_reference', alias)}",
                    kind="unknown_citation",
                    message=f"reader marker {marker!r} has no reference record",
                )
            )
    marker_counts: Dict[str, int] = {}
    for item in placements:
        marker_counts[item.marker] = marker_counts.get(item.marker, 0) + 1
    for marker, expected in marker_counts.items():
        if reader_markdown.count(marker) != expected:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('placement_count', marker)}",
                    kind="duplicate_placement",
                    message=(
                        f"placement marker {marker!r} must appear exactly "
                        f"{expected} times in the reader manuscript"
                    ),
                )
            )


def _default_citation_provider():
    raise RuntimeError("citation provider not supplied")


def _default_front_matter_provider():
    raise RuntimeError("front-matter provider not supplied")


def build_article_presentation(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    reproducibility: ArticleReproducibilityPackage | Mapping[str, Any],
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    method_evidence: Sequence[MethodEvidence | Mapping[str, Any]],
    artifact_roots: Sequence[str | Path],
    *,
    bibliographic_metadata: Optional[Mapping[str, Mapping[str, Any]]] = None,
    citation_provider: Optional[CitationPlacerProvider] = None,
    front_matter_provider: Optional[FrontMatterProvider] = None,
    literature_supplement: LiteratureSupplement | None = None,
    citation_auditor: CitationAuditProvider | None = None,
    output_dir: str | Path | None = None,
) -> ArticlePresentationPackage:
    errors: List[str] = []
    warnings: List[str] = []
    blockers: List[PublicationBlocker] = []
    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
    except ValidationError as exc:
        errors.append(f"plan is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    supplement_manifests, literature_supplement_id = (
        _supplement_evidence_manifests(
            literature_supplement,
            plan_id=plan_model.plan_id,
            errors=errors,
        )
    )
    try:
        ledger_model = (
            ledger
            if isinstance(ledger, ClaimLedgerResult)
            else ClaimLedgerResult.model_validate(ledger)
        )
    except ValidationError as exc:
        errors.append(f"ledger is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        architecture_model = (
            architecture
            if isinstance(architecture, ArticleArchitectureResult)
            else ArticleArchitectureResult.model_validate(architecture)
        )
    except ValidationError as exc:
        errors.append(f"architecture is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        review_model = (
            review
            if isinstance(review, ArticleReviewResult)
            else ArticleReviewResult.model_validate(review)
        )
    except ValidationError as exc:
        errors.append(f"review is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        manuscript_model = (
            manuscript
            if isinstance(manuscript, ArticleManuscriptPackage)
            else ArticleManuscriptPackage.model_validate(manuscript)
        )
    except ValidationError as exc:
        errors.append(f"manuscript is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        reproducibility_model = (
            reproducibility
            if isinstance(reproducibility, ArticleReproducibilityPackage)
            else ArticleReproducibilityPackage.model_validate(reproducibility)
        )
    except ValidationError as exc:
        errors.append(f"reproducibility is invalid: {exc}")
        return _hard_blocker(errors, warnings)
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
    evidence: List[MethodEvidence] = []
    evidence_by_id: Dict[str, MethodEvidence] = {}
    for index, raw in enumerate(method_evidence):
        try:
            item = (
                raw
                if isinstance(raw, MethodEvidence)
                else MethodEvidence.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"method_evidence[{index}] is invalid: {exc}")
            continue
        if item.evidence_id in evidence_by_id:
            errors.append(f"duplicate method evidence id {item.evidence_id!r}")
        evidence.append(item)
        evidence_by_id[item.evidence_id] = item
    combined_identity: Dict[str, EvidenceIdentityManifest] = {
        item.evidence_id: item for item in plan_model.evidence_identity
    }
    for manifest in supplement_manifests:
        existing = combined_identity.get(manifest.evidence_id)
        if existing is not None and existing != manifest:
            errors.append(
                "literature supplement conflicts with plan evidence identity "
                f"{manifest.evidence_id!r}"
            )
            continue
        combined_identity[manifest.evidence_id] = manifest
    citation_plan = plan_model.model_copy(
        update={"evidence_identity": list(combined_identity.values())}
    )
    roots = [Path(item) for item in artifact_roots]
    if errors:
        return _hard_blocker(errors, warnings)
    story = validate_manuscript_package(
        manuscript_model,
        plan_model,
        ledger_model,
        architecture_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if errors or story is None:
        return _hard_blocker(errors, warnings)
    validate_review_result(
        plan_model,
        ledger_model,
        architecture_model,
        review_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if errors:
        return _hard_blocker(errors, warnings)
    validate_reproducibility_package(
        reproducibility_model,
        plan_model,
        ledger_model,
        architecture_model,
        review_model,
        manuscript_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if errors:
        return _hard_blocker(errors, warnings)
    biblio = bibliographic_metadata or {}
    citations, references, allowed_aliases = _build_citations(
        plan=citation_plan,
        ledger=ledger_model,
        manuscript=manuscript_model,
        evidence_by_id=evidence_by_id,
        bibliographic_metadata=biblio,
        blockers=blockers,
        warnings=warnings,
    )
    if errors or blockers:
        return _hard_blocker(errors, warnings, blockers)
    usage_parts: List[Dict[str, Any]] = []
    models: List[str] = []
    attempts = 0
    citation_placements: List[CitationPlacement] = []
    citation_provider_available = citation_provider is not None
    raw_placements: List[Mapping[str, Any]] = []
    if citation_provider is not None:
        requests = _build_citation_section_requests(
            manuscript=manuscript_model,
        references=references,
        allowed_aliases=allowed_aliases,
        plan=plan_model,
        evidence_by_id=evidence_by_id,
    )
        try:
            for request in requests:
                attempts += 1
                envelope = citation_provider(request)
                if not isinstance(envelope, ProviderResult):
                    raise TypeError("citation provider must return ProviderResult")
                usage_parts.append(dict(envelope.usage or {}))
                models.append(envelope.provider_model)
                response = envelope.response or {}
                placements_raw = response.get("placements") or []
                if not isinstance(placements_raw, list):
                    citation_provider_available = False
                    warnings.append(
                        "citation placement response is malformed; "
                        "deterministic fallback used"
                    )
                    break
                raw_placements.extend(placements_raw)
        except Exception as exc:
            citation_provider_available = False
            warnings.append(f"citation placement provider unavailable: {exc}")
    citation_placements = _apply_placements(
        manuscript=manuscript_model,
        allowed_aliases=allowed_aliases,
        raw_placements=raw_placements,
        providers_available=citation_provider_available,
        blockers=blockers,
        warnings=warnings,
    )
    front_matter_model: Optional[FrontMatter] = None
    front_fallback = True
    if front_matter_provider is not None:
        request = _build_front_matter_request(
            plan=plan_model,
            manuscript=manuscript_model,
            references=references,
        )
        try:
            attempts += 1
            envelope = front_matter_provider(request)
            if not isinstance(envelope, ProviderResult):
                raise TypeError("front-matter provider must return ProviderResult")
            usage_parts.append(dict(envelope.usage or {}))
            models.append(envelope.provider_model)
            validated = _validate_front_matter(
                envelope.response or {},
                manuscript=manuscript_model,
                warnings=warnings,
            )
            if validated is not None:
                front_matter_model = validated
                front_fallback = False
            else:
                warnings.append(
                    "front-matter response rejected; deterministic fallback " "used"
                )
        except Exception as exc:
            warnings.append(f"front-matter provider unavailable: {exc}")
    if front_matter_model is None:
        front_matter_model = _deterministic_front_matter(
            plan=plan_model,
            manuscript=manuscript_model,
            warnings=warnings,
        )

    figure_by_id = {figure.figure_id: figure for figure in story.figure_contracts}
    inventory_by_id = {
        item.artifact_id: item for item in architecture_model.artifact_inventory
    }
    used_figure_ids = sorted(
        {
            figure_id
            for paragraph in manuscript_model.source_map
            for figure_id in paragraph.figure_ids
        }
    )
    visuals: List[RenderedVisual] = []
    figure_number = 0
    for figure_id in used_figure_ids:
        figure = figure_by_id.get(figure_id)
        if figure is None:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest('unknown_figure', figure_id)}",
                    kind="unknown_figure",
                    message=f"manuscript references unknown figure {figure_id!r}",
                )
            )
            continue
        citing_paragraphs = [
            paragraph.paragraph_id
            for paragraph in manuscript_model.source_map
            if figure_id in paragraph.figure_ids
        ]
        after_paragraph_id = citing_paragraphs[0] if citing_paragraphs else ""
        section_id = figure.section_target or next(
            (
                section.section_id
                for section in manuscript_model.body.sections
                if any(
                    figure_id in paragraph.figure_ids
                    for paragraph in section.paragraphs
                )
            ),
            "",
        )
        if not section_id:
            warnings.append(f"figure {figure_id!r} has no target section; skipped")
            continue
        caption = figure.caption_intent or f"Figure for {figure.role_key}"
        figure_number += 1
        if figure.source_mode == "trusted_artifact":
            panels: List[PanelAsset] = []
            artifact_ids: List[str] = []
            render_bindings = _group_artifact_bindings(
                figure.artifact_bindings,
                figure_id=figure_id,
                warnings=warnings,
            )
            for binding in render_bindings:
                descriptor = inventory_by_id.get(binding.artifact_id)
                if descriptor is None:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest('unknown_artifact', figure_id, binding.artifact_id)}",
                            kind="unknown_artifact",
                            message=(
                                f"figure {figure_id!r} references unknown "
                                f"artifact {binding.artifact_id!r}"
                            ),
                            artifact_ids=[binding.artifact_id],
                        )
                    )
                    continue
                artifact_path, artifact_sha = _verify_quantitative_artifact(
                    descriptor=descriptor,
                    roots=roots,
                    reproducibility=reproducibility_model,
                    blockers=blockers,
                    contract_figure_id=figure_id,
                )
                if artifact_path is None:
                    continue
                artifact_ids.append(descriptor.artifact_id)
                suffix = artifact_path.suffix.lower()
                safe_artifact = _sanitize_asset_name(descriptor.artifact_id)
                if suffix in {".png", ".jpg", ".jpeg", ".pdf"}:
                    final_bytes = _prepare_raster_bytes(artifact_path, caption, suffix)
                    final_sha = _sha256_bytes(final_bytes)
                    asset_name = _sanitize_asset_name(
                        f"{figure_id}-{descriptor.artifact_id}-{final_sha[:8]}{suffix}"
                    )
                    asset_path = Path("figures") / asset_name
                    panels.append(
                        PanelAsset(
                            label=descriptor.artifact_id,
                            asset_path=asset_path.as_posix(),
                            encoding="base64",
                            media_type=(
                                "application/pdf"
                                if suffix == ".pdf"
                                else "image/png" if suffix == ".png" else "image/jpeg"
                            ),
                            asset_bytes_b64=base64.b64encode(final_bytes).decode(
                                "ascii"
                            ),
                            sha256=final_sha,
                        )
                    )
                else:
                    selected_fields = [
                        field
                        for item_binding in figure.artifact_bindings
                        if item_binding.artifact_id == descriptor.artifact_id
                        for field in item_binding.selected_fields
                    ]
                    try:
                        rows = _load_numeric_rows(artifact_path, selected_fields)
                        if figure.kind == "table":
                            content = _render_markdown_table(rows, selected_fields)
                            content_sha = _sha256_bytes(content.encode("utf-8"))
                            asset_name = _sanitize_asset_name(
                                f"{figure_id}-{safe_artifact}-{content_sha[:8]}.md"
                            )
                            asset_path = Path("tables") / asset_name
                            panels.append(
                                PanelAsset(
                                    label=descriptor.artifact_id,
                                    asset_path=asset_path.as_posix(),
                                    encoding="utf-8",
                                    media_type="text/markdown",
                                    asset_content=content,
                                    sha256=content_sha,
                                )
                            )
                        else:
                            content = _render_svg_plot(
                                rows, selected_fields, descriptor.artifact_id
                            )
                            content_sha = _sha256_bytes(content.encode("utf-8"))
                            asset_name = _sanitize_asset_name(
                                f"{figure_id}-{safe_artifact}-{content_sha[:8]}.svg"
                            )
                            asset_path = Path("figures") / asset_name
                            panels.append(
                                PanelAsset(
                                    label=descriptor.artifact_id,
                                    asset_path=asset_path.as_posix(),
                                    encoding="utf-8",
                                    media_type="image/svg+xml",
                                    asset_content=content,
                                    sha256=content_sha,
                                )
                            )
                    except Exception as exc:
                        blockers.append(
                            PublicationBlocker(
                                blocker_id=f"blocker-{_digest('render_failed', figure_id, descriptor.artifact_id)}",
                                kind="numeric_render_failed",
                                message=(
                                    f"numeric rendering failed for figure "
                                    f"{figure_id!r}: {exc}"
                                ),
                                artifact_ids=[descriptor.artifact_id],
                            )
                        )
            if not panels:
                warnings.append(f"figure {figure_id!r} produced no trusted panels")
                continue
            block_markdown = _visual_block_markdown(
                figure_number, caption, panels, table=(figure.kind == "table")
            )
            combined_sha = hashlib.sha256(
                _canonical_json(
                    [panel.model_dump(mode="json") for panel in panels]
                ).encode("utf-8")
            ).hexdigest()
            visuals.append(
                RenderedVisual(
                    visual_id=f"fig-{combined_sha}",
                    asset_kind="table" if figure.kind == "table" else "figure",
                    contract_figure_id=figure_id,
                    section_id=section_id,
                    figure_number=figure_number,
                    after_paragraph_id=after_paragraph_id,
                    panels=panels,
                    source_mode="trusted_artifact",
                    provenance="verified",
                    caption=caption,
                    claim_ids=list(figure.claim_ids),
                    fact_ids=list(figure.fact_ids),
                    artifact_ids=artifact_ids,
                    limitations=list(figure.limitations),
                    sha256=combined_sha,
                    block_markdown=block_markdown,
                )
            )
        else:
            try:
                claim_statements = [
                    next(
                        claim.statement
                        for claim in ledger_model.claims
                        if claim.claim_id == claim_id
                    )
                    for claim_id in figure.claim_ids
                ]
                content = _render_synthesized_diagram(
                    figure.panel_intents, claim_statements
                )
            except Exception as exc:
                warnings.append(
                    f"conceptual figure {figure_id!r} failed to render: {exc}"
                )
                continue
            content_sha = _sha256_bytes(content.encode("utf-8"))
            asset_name = _sanitize_asset_name(
                f"{figure_id}-synthesized-{content_sha[:8]}.svg"
            )
            asset_path = Path("figures") / asset_name
            panels = [
                PanelAsset(
                    label="synthesized",
                    asset_path=asset_path.as_posix(),
                    encoding="utf-8",
                    media_type="image/svg+xml",
                    asset_content=content,
                    sha256=content_sha,
                )
            ]
            block_markdown = (
                f"![{caption} (synthesized)]({asset_path.as_posix()})\n\n"
                f"**{caption}** (synthesized conceptual diagram; not measured "
                "data)"
            )
            combined_sha = hashlib.sha256(
                _canonical_json(
                    [panel.model_dump(mode="json") for panel in panels]
                ).encode("utf-8")
            ).hexdigest()
            visuals.append(
                RenderedVisual(
                    visual_id=f"fig-{combined_sha}",
                    asset_kind="figure",
                    contract_figure_id=figure_id,
                    section_id=section_id,
                    figure_number=figure_number,
                    after_paragraph_id=after_paragraph_id,
                    panels=panels,
                    source_mode="synthesized_claims",
                    provenance="synthesized",
                    caption=caption,
                    claim_ids=list(figure.claim_ids),
                    fact_ids=list(figure.fact_ids),
                    artifact_ids=[],
                    limitations=list(figure.limitations),
                    sha256=combined_sha,
                    block_markdown=block_markdown,
                )
            )
    usage: Dict[str, Any] = {}
    for part in usage_parts:
        for key, value in part.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[key] = usage.get(key, 0) + value
            elif key not in usage and value is not None:
                usage[key] = value
    semantic_model = (
        models[0] if len(set(models)) == 1 else ("mixed" if models else "none")
    )
    model_name = (
        MODEL_NAME
        if semantic_model == MODEL_NAME
        else ("none" if not models else "mixed")
    )
    if blockers:
        partial_reader = _render_reader_manuscript(
            front_matter=front_matter_model,
            manuscript=manuscript_model,
            citations=citations,
            references=references,
            placements=citation_placements,
            visuals=visuals,
        )
        return _blocked_with_partial(
            errors=errors,
            warnings=warnings,
            blockers=blockers,
            plan_id=plan_model.plan_id,
            ledger_id=ledger_model.ledger_id,
            architecture_id=architecture_model.architecture_id,
            review_id=review_model.review_id,
            result_id=review_model.result_id,
            manuscript_body_id=manuscript_model.body_id,
            reproducibility_package_id=reproducibility_model.package_id,
            story_id=selected_story_id,
            citations=citations,
            references=references,
            placements=citation_placements,
            front_matter=front_matter_model,
            visuals=visuals,
            reader_markdown=partial_reader,
            attempts=attempts,
            usage=usage,
            model_name=model_name,
            literature_supplement_id=literature_supplement_id,
        )
    reader_markdown = _render_reader_manuscript(
        front_matter=front_matter_model,
        manuscript=manuscript_model,
        citations=citations,
        references=references,
        placements=citation_placements,
        visuals=visuals,
    )
    _final_safety_check(
        reader_markdown=reader_markdown,
        front_matter=front_matter_model,
        manuscript=manuscript_model,
        plan=plan_model,
        blockers=blockers,
    )
    _verify_body_invariant(
        manuscript=manuscript_model,
        reader_markdown=reader_markdown,
        placements=citation_placements,
        references=references,
        blockers=blockers,
    )
    if blockers:
        return _blocked_with_partial(
            errors=errors,
            warnings=warnings,
            blockers=blockers,
            plan_id=plan_model.plan_id,
            ledger_id=ledger_model.ledger_id,
            architecture_id=architecture_model.architecture_id,
            review_id=review_model.review_id,
            result_id=review_model.result_id,
            manuscript_body_id=manuscript_model.body_id,
            reproducibility_package_id=reproducibility_model.package_id,
            story_id=selected_story_id,
            citations=citations,
            references=references,
            placements=citation_placements,
            front_matter=front_matter_model,
            visuals=visuals,
            reader_markdown=reader_markdown,
            attempts=attempts,
            usage=usage,
            model_name=model_name,
            literature_supplement_id=literature_supplement_id,
        )
    status: Literal["ready", "ready_with_findings", "blocked"] = (
        "ready_with_findings" if warnings else "ready"
    )
    package_id = compute_presentation_package_id(
        plan_id=plan_model.plan_id,
        ledger_id=ledger_model.ledger_id,
        architecture_id=architecture_model.architecture_id,
        review_id=review_model.review_id,
        result_id=review_model.result_id,
        manuscript_body_id=manuscript_model.body_id,
        reproducibility_package_id=reproducibility_model.package_id,
        story_id=selected_story_id,
        status=status,
        citations=citations,
        references=references,
        placements=citation_placements,
        front_matter=front_matter_model,
        visuals=visuals,
        reader_markdown=reader_markdown,
        blockers=blockers,
        warnings=warnings,
        errors=errors,
        attempts=attempts,
        literature_supplement_id=literature_supplement_id,
    )
    result = ArticlePresentationPackage(
        package_id=package_id,
        plan_id=plan_model.plan_id,
        ledger_id=ledger_model.ledger_id,
        architecture_id=architecture_model.architecture_id,
        review_id=review_model.review_id,
        result_id=review_model.result_id,
        manuscript_body_id=manuscript_model.body_id,
        reproducibility_package_id=reproducibility_model.package_id,
        story_id=selected_story_id,
        status=status,
        citations=citations,
        references=references,
        placements=citation_placements,
        front_matter=front_matter_model,
        visuals=visuals,
        reader_markdown=reader_markdown,
        blockers=blockers,
        warnings=warnings,
        errors=errors,
        model_name=(
            MODEL_NAME
            if semantic_model == MODEL_NAME
            else ("none" if not models else "mixed")
        ),
        usage=usage,
        attempts=attempts,
        literature_supplement_id=literature_supplement_id,
    )
    audit_result = None
    if citation_auditor is not None:
        try:
            audit_result = citation_auditor(
                result,
                manuscript_model,
                evidence_by_id,
            )
            result = apply_citation_audit(result, manuscript_model, audit_result)
        except Exception as exc:
            retained_warnings = list(result.warnings)
            retained_warnings.append(
                f"citation auditor unavailable or rejected; original "
                f"placements retained: {exc}"
            )
            retained_attempts = result.attempts + 1
            retained_package_id = compute_presentation_package_id(
                plan_id=result.plan_id,
                ledger_id=result.ledger_id,
                architecture_id=result.architecture_id,
                review_id=result.review_id,
                result_id=result.result_id,
                manuscript_body_id=result.manuscript_body_id,
                reproducibility_package_id=result.reproducibility_package_id,
                story_id=result.story_id,
                status="ready_with_findings",
                citations=result.citations,
                references=result.references,
                placements=result.placements,
                front_matter=result.front_matter,
                visuals=result.visuals,
                reader_markdown=result.reader_markdown,
                blockers=result.blockers,
                warnings=retained_warnings,
                errors=result.errors,
                attempts=retained_attempts,
                literature_supplement_id=result.literature_supplement_id,
            )
            result = result.model_copy(
                update={
                    "package_id": retained_package_id,
                    "status": "ready_with_findings",
                    "warnings": retained_warnings,
                    "attempts": retained_attempts,
                }
            )
    result = _normalize_duplicate_citations(result, manuscript_model)
    if output_dir is not None:
        write_presentation_package(
            result,
            output_dir,
            plan=plan_model,
            ledger=ledger_model,
            architecture=architecture_model,
            review=review_model,
            manuscript=manuscript_model,
            reproducibility=reproducibility_model,
            selected_story_id=selected_story_id,
            value_records=records,
        )
        if audit_result is not None:
            write_citation_audit(
                Path(output_dir) / "ARTICLE_CITATION_AUDIT.json",
                audit_result,
            )
    return result


def _prepare_raster_bytes(source: Path, caption: str, suffix: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        destination = Path(tmp) / f"figure{suffix}"
        prepare_publication_figure(source, destination, {"caption_en": caption})
        return destination.read_bytes()


def _visual_block_markdown(
    figure_number: int,
    caption: str,
    panels: Sequence[PanelAsset],
    *,
    table: bool,
) -> str:
    label = "Table" if table else "Figure"
    escaped_caption = _md_escape(caption)
    if table:
        parts = []
        for index, panel in enumerate(panels):
            panel_suffix = (
                f" (panel {chr(ord('a') + index)})" if len(panels) > 1 else ""
            )
            parts.append(f"{panel.asset_content}\n\n: {escaped_caption}{panel_suffix}")
        return "\n\n".join(parts)
    parts = [f"**{label} {figure_number}.** {escaped_caption}\n"]
    for panel in panels:
        parts.append(f"![{_md_escape(caption)} panel]({panel.asset_path})")
    return "\n\n".join(parts)


def _validate_package_relationships(
    package: ArticlePresentationPackage,
    manuscript: Optional[ArticleManuscriptPackage],
    errors: List[str],
) -> None:
    reference_aliases = {item.reference_alias for item in package.references}
    placement_markers = {item.marker for item in package.placements}
    placement_keys = {
        (item.paragraph_id, item.reference_alias) for item in package.placements
    }
    if len(placement_keys) != len(package.placements):
        errors.append("duplicate (paragraph_id, reference_alias) citation placements")
    citation_keys = {
        (item.paragraph_id, item.reference_alias) for item in package.citations
    }
    for placement in package.placements:
        if (placement.paragraph_id, placement.reference_alias) not in citation_keys:
            errors.append(
                f"placement {placement.placement_id!r} has no matching "
                "citation in the same paragraph"
            )
    for citation in package.citations:
        if (citation.paragraph_id, citation.reference_alias) not in placement_keys:
            errors.append(
                f"citation {citation.citation_id!r} has no matching placement "
                "in the same paragraph"
            )
    paragraph_ids = (
        {paragraph.paragraph_id for paragraph in manuscript.source_map}
        if manuscript is not None
        else set()
    )
    for placement in package.placements:
        if manuscript is not None and placement.paragraph_id not in paragraph_ids:
            errors.append(
                f"placement {placement.placement_id!r} targets unknown "
                f"paragraph {placement.paragraph_id!r}"
            )
        if placement.reference_alias not in reference_aliases:
            errors.append(
                f"placement {placement.placement_id!r} references unknown "
                f"alias {placement.reference_alias!r}"
            )
    for citation in package.citations:
        if citation.reference_alias not in reference_aliases:
            errors.append(
                f"citation {citation.citation_id!r} references unknown "
                f"alias {citation.reference_alias!r}"
            )
    for marker in _CITATION_MARKER_RE.findall(package.reader_markdown):
        if marker not in placement_markers:
            errors.append(f"reader manuscript contains unregistered marker {marker!r}")
    marker_counts: Dict[str, int] = {}
    for placement in package.placements:
        marker_counts[placement.marker] = marker_counts.get(placement.marker, 0) + 1
    for marker, expected in marker_counts.items():
        if package.reader_markdown.count(marker) != expected:
            errors.append(
                f"marker {marker!r} must appear exactly {expected} times in "
                "the reader manuscript"
            )
    if manuscript is not None:
        for paragraph in manuscript.source_map:
            paragraph_text = paragraph.rendered_text
            paragraph_placements = [
                item
                for item in package.placements
                if item.paragraph_id == paragraph.paragraph_id
            ]
            local = _render_reader_paragraph(
                paragraph.paragraph_id, paragraph_text, paragraph_placements
            )
            for item in paragraph_placements:
                if local.count(item.marker) != 1:
                    errors.append(
                        f"marker {item.marker!r} must appear exactly once in "
                        f"paragraph {paragraph.paragraph_id!r}"
                    )
        blockers: List[PublicationBlocker] = []
        _verify_body_invariant(
            manuscript=manuscript,
            reader_markdown=package.reader_markdown,
            placements=package.placements,
            references=package.references,
            blockers=blockers,
        )
        errors.extend(item.message for item in blockers)
    for visual in package.visuals:
        seen_paths: set[str] = set()
        for panel in visual.panels:
            if panel.asset_path in seen_paths:
                errors.append(f"visual {visual.visual_id!r} repeats panel path")
            seen_paths.add(panel.asset_path)
            path = Path(panel.asset_path)
            if path.is_absolute() or ".." in path.parts:
                errors.append(f"panel path {panel.asset_path!r} is unsafe")
            expected_bytes = (
                panel.asset_content.encode("utf-8")
                if panel.encoding == "utf-8"
                else base64.b64decode(panel.asset_bytes_b64)
            )
            if hashlib.sha256(expected_bytes).hexdigest() != panel.sha256:
                errors.append(
                    f"panel {panel.asset_path!r} hash does not match its " "manifest"
                )
        expected_visual_sha = hashlib.sha256(
            _canonical_json(
                [panel.model_dump(mode="json") for panel in visual.panels]
            ).encode("utf-8")
        ).hexdigest()
        if expected_visual_sha != visual.sha256:
            errors.append(
                f"visual {visual.visual_id!r} sha256 does not match its "
                "ordered panel manifest"
            )
    if package.front_matter is not None and manuscript is not None:
        for index, item in enumerate(package.front_matter.abstract_sentences):
            for alias in item.get("paragraph_aliases", []):
                if alias not in paragraph_ids:
                    errors.append(
                        f"front-matter sentence {index} references unknown "
                        f"paragraph alias {alias!r}"
                    )


def validate_presentation_package(
    package: ArticlePresentationPackage | Mapping[str, Any],
    *,
    plan: Optional[ArticleDirectorPlan | Mapping[str, Any]] = None,
    ledger: Optional[ClaimLedgerResult | Mapping[str, Any]] = None,
    architecture: Optional[ArticleArchitectureResult | Mapping[str, Any]] = None,
    review: Optional[ArticleReviewResult | Mapping[str, Any]] = None,
    manuscript: Optional[ArticleManuscriptPackage | Mapping[str, Any]] = None,
    reproducibility: Optional[ArticleReproducibilityPackage | Mapping[str, Any]] = None,
    selected_story_id: str = "",
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]] = (),
    require_body_provenance: bool = False,
    errors: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
) -> bool:
    """Public deterministic Stage 12C validator (no network/model calls).

    Package-only validation never claims to revalidate the exact Stage 12A
    body.  Callers that require body provenance (for example the fixed-name
    writer) pass ``require_body_provenance=True``; if no manuscript is then
    supplied, a warning is reported instead of implying full provenance.

    When the complete upstream set (plan, ledger, architecture, review,
    manuscript, reproducibility, selected story, value records) is supplied,
    the same deterministic chain integrity checks used by
    ``build_article_presentation`` are re-run: ``validate_manuscript_package``,
    ``validate_review_result``, and ``validate_reproducibility_package``.
    Content tampering with stale top-level IDs therefore fails closed.
    Partial optional-object calls keep their ID-check behavior only and never
    pretend to perform a full chain audit.
    """

    if errors is None:
        errors = []
    if warnings is None:
        warnings = []
    try:
        package_model = (
            package
            if isinstance(package, ArticlePresentationPackage)
            else ArticlePresentationPackage.model_validate(package)
        )
    except ValidationError as exc:
        errors.append(f"presentation package is invalid: {exc}")
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
        reproducibility,
        ArticleReproducibilityPackage,
        "reproducibility",
        errors,
    )
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
    if plan_model is not None and package_model.plan_id != plan_model.plan_id:
        errors.append("presentation plan_id does not match the plan")
    if ledger_model is not None and package_model.ledger_id != ledger_model.ledger_id:
        errors.append("presentation ledger_id does not match the ledger")
    if architecture_model is not None and (
        package_model.architecture_id != architecture_model.architecture_id
    ):
        errors.append("presentation architecture_id does not match the architecture")
    if review_model is not None and (
        package_model.review_id != review_model.review_id
        or package_model.result_id != review_model.result_id
    ):
        errors.append("presentation review/result identity does not match the review")
    if manuscript_model is not None and (
        package_model.manuscript_body_id != manuscript_model.body_id
    ):
        errors.append("presentation manuscript_body_id does not match the manuscript")
    if reproducibility_model is not None and (
        package_model.reproducibility_package_id != reproducibility_model.package_id
    ):
        errors.append(
            "presentation reproducibility package id does not match the "
            "supplied package"
        )
    if selected_story_id and package_model.story_id != selected_story_id:
        errors.append("presentation story_id does not match the story")
    recomputed = compute_presentation_package_id(
        plan_id=package_model.plan_id,
        ledger_id=package_model.ledger_id,
        architecture_id=package_model.architecture_id,
        review_id=package_model.review_id,
        result_id=package_model.result_id,
        manuscript_body_id=package_model.manuscript_body_id,
        reproducibility_package_id=package_model.reproducibility_package_id,
        story_id=package_model.story_id,
        status=package_model.status,
        citations=package_model.citations,
        references=package_model.references,
        placements=package_model.placements,
        front_matter=package_model.front_matter,
        visuals=package_model.visuals,
        reader_markdown=package_model.reader_markdown,
        blockers=package_model.blockers,
        warnings=package_model.warnings,
        errors=package_model.errors,
        attempts=package_model.attempts,
        literature_supplement_id=package_model.literature_supplement_id,
    )
    if recomputed != package_model.package_id:
        errors.append("presentation package_id does not match recomputed identity")
    if package_model.errors or package_model.blockers:
        derived_status = "blocked"
    elif package_model.warnings:
        derived_status = "ready_with_findings"
    else:
        derived_status = "ready"
    if derived_status != package_model.status:
        errors.append(
            f"presentation status {package_model.status!r} does not match "
            f"derived status {derived_status!r}"
        )
    if require_body_provenance and manuscript_model is None:
        warnings.append(
            "exact Stage 12A body provenance was not revalidated (no "
            "manuscript supplied)"
        )
    complete_chain = (
        plan_model is not None
        and ledger_model is not None
        and architecture_model is not None
        and review_model is not None
        and manuscript_model is not None
        and reproducibility_model is not None
        and not errors
    )
    if complete_chain:
        validate_manuscript_package(
            manuscript_model,
            plan_model,
            ledger_model,
            architecture_model,
            selected_story_id,
            records,
            errors,
            warnings,
        )
        validate_review_result(
            plan_model,
            ledger_model,
            architecture_model,
            review_model,
            selected_story_id,
            records,
            errors,
            warnings,
        )
        validate_reproducibility_package(
            reproducibility_model,
            plan_model,
            ledger_model,
            architecture_model,
            review_model,
            manuscript_model,
            selected_story_id,
            records,
            errors,
            warnings,
        )
    _validate_package_relationships(package_model, manuscript_model, errors)
    return not errors


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


def _hard_blocker(
    errors: Sequence[str],
    warnings: Sequence[str],
    blockers: Optional[Sequence[PublicationBlocker]] = None,
) -> ArticlePresentationPackage:
    blocker_models = [
        PublicationBlocker(
            blocker_id=f"blocker-{_digest('invalid', str(item))}",
            kind="upstream_identity",
            message=str(item),
        )
        for item in errors
    ]
    blocker_models.extend(blockers or [])
    warning_values = [str(item) for item in warnings]
    error_values = [str(item) for item in errors]
    package_id = compute_presentation_package_id(
        plan_id="",
        ledger_id="",
        architecture_id="",
        review_id="",
        result_id="",
        manuscript_body_id="",
        reproducibility_package_id="",
        story_id="",
        status="blocked",
        citations=[],
        references=[],
        placements=[],
        front_matter=None,
        visuals=[],
        reader_markdown="",
        blockers=blocker_models,
        warnings=warning_values,
        errors=error_values,
        attempts=0,
    )
    return ArticlePresentationPackage(
        package_id=package_id,
        plan_id="",
        ledger_id="",
        architecture_id="",
        review_id="",
        result_id="",
        manuscript_body_id="",
        reproducibility_package_id="",
        story_id="",
        status="blocked",
        citations=[],
        references=[],
        placements=[],
        front_matter=None,
        visuals=[],
        reader_markdown="",
        blockers=blocker_models,
        warnings=warning_values,
        errors=error_values,
        model_name="none",
        usage={},
        attempts=0,
    )


def _blocked_with_partial(
    *,
    errors: Sequence[str],
    warnings: Sequence[str],
    blockers: Sequence[PublicationBlocker],
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    review_id: str,
    result_id: str,
    manuscript_body_id: str,
    reproducibility_package_id: str,
    story_id: str,
    citations: Sequence[CitationRecord],
    references: Sequence[ReferenceRecord],
    placements: Sequence[CitationPlacement],
    front_matter: Optional[FrontMatter],
    visuals: Sequence[RenderedVisual],
    reader_markdown: str,
    attempts: int,
    usage: Optional[Mapping[str, Any]] = None,
    model_name: str = "none",
    literature_supplement_id: str = "",
) -> ArticlePresentationPackage:
    blocker_models = list(blockers)
    warning_values = [str(item) for item in warnings]
    error_values = [str(item) for item in errors]
    package_id = compute_presentation_package_id(
        plan_id=plan_id,
        ledger_id=ledger_id,
        architecture_id=architecture_id,
        review_id=review_id,
        result_id=result_id,
        manuscript_body_id=manuscript_body_id,
        reproducibility_package_id=reproducibility_package_id,
        story_id=story_id,
        status="blocked",
        citations=citations,
        references=references,
        placements=placements,
        front_matter=front_matter,
        visuals=visuals,
        reader_markdown=reader_markdown,
        blockers=blocker_models,
        warnings=warning_values,
        errors=error_values,
        attempts=attempts,
        literature_supplement_id=literature_supplement_id,
    )
    return ArticlePresentationPackage(
        package_id=package_id,
        plan_id=plan_id,
        ledger_id=ledger_id,
        architecture_id=architecture_id,
        review_id=review_id,
        result_id=result_id,
        manuscript_body_id=manuscript_body_id,
        reproducibility_package_id=reproducibility_package_id,
        story_id=story_id,
        status="blocked",
        citations=list(citations),
        references=list(references),
        placements=list(placements),
        front_matter=front_matter,
        visuals=list(visuals),
        reader_markdown=reader_markdown,
        blockers=blocker_models,
        warnings=warning_values,
        errors=error_values,
        model_name=model_name,
        usage=dict(usage or {}),
        attempts=attempts,
        literature_supplement_id=literature_supplement_id,
    )


class _QwenAdvisoryBase:
    """Shared concrete qwen3.7-flash advisory adapter behavior."""

    def __init__(
        self,
        *,
        prompt_path: str | Path,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int,
        agent_name: str,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.max_tokens = int(max_tokens)
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        self.client = client or QwenFlashOnlyClient(agent_name=agent_name)

    def _call(self, request: Mapping[str, Any]) -> ProviderResult:
        messages = [
            {
                "role": "system",
                "content": self.prompt_path.read_text(encoding="utf-8"),
            },
            {
                "role": "user",
                "content": json.dumps(dict(request), ensure_ascii=False),
            },
        ]
        response = self.client.call(
            messages, max_tokens=self.max_tokens, force_mock=False
        )
        parsed = _safe_json(str(response.get("content") or ""))
        usage = _usage_with_cost(response.get("_llm_usage") or {})
        return ProviderResult(
            response=parsed,
            usage=usage,
            provider_model=MODEL_NAME,
            mock_llm=bool(usage.get("mock_llm")),
        )


class QwenCitationPlacer(_QwenAdvisoryBase):
    def __init__(
        self,
        *,
        prompt_path: str | Path = CITATION_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_CITATION_MAX_TOKENS,
    ) -> None:
        super().__init__(
            prompt_path=prompt_path,
            client=client,
            max_tokens=max_tokens,
            agent_name="ArticleCitationPlacer",
        )

    def __call__(self, request: Mapping[str, Any]) -> ProviderResult:
        return self._call(request)


class QwenFrontMatterWriter(_QwenAdvisoryBase):
    def __init__(
        self,
        *,
        prompt_path: str | Path = FRONT_MATTER_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_FRONT_MATTER_MAX_TOKENS,
    ) -> None:
        super().__init__(
            prompt_path=prompt_path,
            client=client,
            max_tokens=max_tokens,
            agent_name="ArticleFrontMatterWriter",
        )

    def __call__(self, request: Mapping[str, Any]) -> ProviderResult:
        return self._call(request)


def write_presentation_package(
    package: ArticlePresentationPackage,
    output_dir: str | Path,
    *,
    plan: Optional[ArticleDirectorPlan | Mapping[str, Any]] = None,
    ledger: Optional[ClaimLedgerResult | Mapping[str, Any]] = None,
    architecture: Optional[ArticleArchitectureResult | Mapping[str, Any]] = None,
    review: Optional[ArticleReviewResult | Mapping[str, Any]] = None,
    manuscript: Optional[ArticleManuscriptPackage | Mapping[str, Any]] = None,
    reproducibility: Optional[ArticleReproducibilityPackage | Mapping[str, Any]] = None,
    selected_story_id: str = "",
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]] = (),
) -> Dict[str, Path]:
    """Atomic fixed-name writer; refuses to overwrite conflicting content."""

    validation_errors: List[str] = []
    validation_warnings: List[str] = []
    if not validate_presentation_package(
        package,
        plan=plan,
        ledger=ledger,
        architecture=architecture,
        review=review,
        manuscript=manuscript,
        reproducibility=reproducibility,
        selected_story_id=selected_story_id,
        value_records=value_records,
        require_body_provenance=True,
        errors=validation_errors,
        warnings=validation_warnings,
    ):
        raise ArticlePresentationIntegrityError(
            "refusing to write a package that fails validation: "
            + "; ".join(validation_errors[:5])
        )
    for warning in validation_warnings:
        warnings.warn(warning, UserWarning, stacklevel=2)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    reader_path = output_dir / "ARTICLE_READER_MANUSCRIPT.md"
    package_path = output_dir / "ARTICLE_PRESENTATION_PACKAGE.json"
    citation_map_path = output_dir / "ARTICLE_CITATION_MAP.json"
    references_path = output_dir / "ARTICLE_REFERENCES.json"
    manifest_path = output_dir / "ARTICLE_FIGURE_TABLE_MANIFEST.json"
    expected_reader = package.reader_markdown
    expected_package = _canonical_json(package.model_dump(mode="json"))
    expected_citations = _canonical_json(
        [item.model_dump(mode="json") for item in package.citations]
    )
    expected_references = _canonical_json(
        [item.model_dump(mode="json") for item in package.references]
    )
    expected_manifest = _canonical_json(
        [item.model_dump(mode="json") for item in package.visuals]
    )
    for path, expected in (
        (reader_path, expected_reader),
        (package_path, expected_package),
        (citation_map_path, expected_citations),
        (references_path, expected_references),
        (manifest_path, expected_manifest),
    ):
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                identical = _json_equal(existing, expected)
            else:
                identical = existing == expected
            if not identical:
                raise ArticlePresentationIntegrityError(
                    f"refusing to overwrite conflicting {path.name} under "
                    f"package {package.package_id!r}"
                )
    panel_payloads: List[Tuple[Path, bytes]] = []
    for visual in package.visuals:
        for panel in visual.panels:
            asset_path = output_dir / panel.asset_path
            if not _safe_within(asset_path, output_dir):
                raise ArticlePresentationIntegrityError(
                    f"asset path {panel.asset_path!r} escapes output_dir"
                )
            expected_bytes = (
                panel.asset_content.encode("utf-8")
                if panel.encoding == "utf-8"
                else base64.b64decode(panel.asset_bytes_b64)
            )
            if _sha256_bytes(expected_bytes) != panel.sha256:
                raise ArticlePresentationIntegrityError(
                    f"asset {panel.asset_path!r} hash does not match its " "manifest"
                )
            if asset_path.exists() and asset_path.read_bytes() != expected_bytes:
                raise ArticlePresentationIntegrityError(
                    f"refusing to overwrite conflicting asset "
                    f"{panel.asset_path} under package {package.package_id!r}"
                )
            panel_payloads.append((asset_path, expected_bytes))
    atomic_write_text(reader_path, expected_reader)
    atomic_write_json(package_path, package.model_dump(mode="json"))
    atomic_write_json(
        citation_map_path,
        [item.model_dump(mode="json") for item in package.citations],
    )
    atomic_write_json(
        references_path,
        [item.model_dump(mode="json") for item in package.references],
    )
    atomic_write_json(
        manifest_path,
        [item.model_dump(mode="json") for item in package.visuals],
    )
    for asset_path, expected_bytes in panel_payloads:
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(asset_path.parent), prefix=".tmp_asset_")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(expected_bytes)
            os.replace(tmp, asset_path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return {
        "reader": reader_path,
        "package": package_path,
        "citation_map": citation_map_path,
        "references": references_path,
        "manifest": manifest_path,
    }


__all__ = [
    "ArticlePresentationError",
    "ArticlePresentationIntegrityError",
    "ArticlePresentationPackage",
    "CitationPlacement",
    "CitationPlacerProvider",
    "CitationRecord",
    "DEFAULT_CITATION_MAX_TOKENS",
    "DEFAULT_FRONT_MATTER_MAX_TOKENS",
    "FrontMatter",
    "FrontMatterProvider",
    "MODEL_NAME",
    "PanelAsset",
    "ProviderResult",
    "PublicationBlocker",
    "QwenCitationPlacer",
    "QwenFrontMatterWriter",
    "ReferenceRecord",
    "RenderedVisual",
    "build_article_presentation",
    "compute_presentation_package_id",
    "validate_presentation_package",
    "write_presentation_package",
]
