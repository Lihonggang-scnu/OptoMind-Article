"""Stage 10: evidence-token section writing.

Converts one explicitly selected Stage 9 ``StoryCandidate`` into traceable
section drafts.  Each section is written independently against a bounded
local payload: the original question/charter scope, the global story
shape/thesis, the section's own contract, the relevant full claim
statements/scopes/limits/FactRecord linkage, its figures, and compact
outlines of the other sections.

Trust boundary: local code owns IDs, semantic aliases, value tokens, schema,
statuses, source ledgers, validation, usage, and persistence.  Qwen
(``qwen3.7-flash``, via a concrete adapter) fills only high-information
content and never sees exact scalar values: it receives value token aliases
plus semantic labels/units, and exact numbers enter prose only through local
``[VALUE:...]`` tokens replaced after validation.

Fail-open workflow: input provenance/integrity errors fail the bundle before
any provider call.  A provider failure or one malformed/unsafe section never
erases valid sibling sections or Stage 8/9 assets; the affected section is
marked ``blocked``/``needs_revision`` with exact errors and any safe draft is
retained as non-publishable.  At most two compact format/source repair rounds
are attempted for failed sections; no-progress stops and retains findings.

Fail-closed rules: unknown/misspelled claim/figure aliases or value tokens,
measurement-like raw numbers outside allowed ``[VALUE:...]`` tokens,
figure-only values used in prose, and persistence conflicts are hard errors
for that section/bundle.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from enum import Enum
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

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from optomind_optics.harness.article_architecture import (
    ArticleArchitectureResult,
    ClaimPlacement,
    FigureContract,
    SectionContract,
    StoryCandidate,
    compute_architecture_id,
)
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_contracts import (
    ARTICLE_EVENT_SCHEMA_VERSION,
    ArticleNodePayload,
    ArticleStage,
    ClaimCard,
    ClaimStatus,
    validate_article_event,
)
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_memory import (
    ArticleMemoryStore,
    DuplicateRecordError,
    RunMemoryRecord,
)
from optomind_optics.harness.experiment_graph import ExperimentGraph
from optomind_optics.harness.qwen_policy import QwenFlashOnlyClient
from optomind_research.runtime.artifact_store import atomic_write_json
from optomind_research.runtime.cost_ledger import estimate_call_cost_cny


WRITING_SCHEMA_VERSION = "article-draft-bundle.v1"
SECTION_DRAFT_SCHEMA_VERSION = "article-section-draft.v1"
PARAGRAPH_SOURCE_LEDGER_SCHEMA_VERSION = "paragraph-source-ledger.v1"
TRUSTED_VALUE_SCHEMA_VERSION = "trusted-value-record.v1"
WRITER_MODEL_NAME = "qwen3.7-flash"
DEFAULT_WRITER_MAX_TOKENS = 6000
DEFAULT_REPAIR_MAX_TOKENS = 4000
WRITER_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Section Writer.txt"
)
REPAIR_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "Article Section Format Repair.txt"
)

POSITIVE_CLAIM_STATUSES = frozenset(
    {ClaimStatus.partially_supported, ClaimStatus.supported}
)
QUANTITATIVE_KINDS = frozenset({"quantitative", "table"})

_PLAIN_INTEGER_RE = re.compile(r"(?<![A-Za-z0-9])\d+(?![\d.])")
_VALUE_TOKEN_RE = re.compile(r"\[VALUE:([^\[\]]*)\]")
_MEASUREMENT_UNIT_SUFFIX = (
    r"%|percent\b|nm|um|mm|cm|km|kg|g|mg|s|ms|us|ns|Hz|kHz|MHz|GHz|THz|"
    r"W|mW|uW|kW|V|mV|uV|kV|A|mA|uA|kA|K|deg|degC|J|kJ|mol|dB|eV|keV|MeV"
)
_MEASUREMENT_COMPARATOR = (
    r"at or above|at or below|greater than|less than|at least|at most|"
    r"no more than|no less than|exceeds?|above|below|under|up to"
)
_MEASUREMENT_NUMBER = r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_MEASUREMENT_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:"
    r"(?:(?:" + _MEASUREMENT_COMPARATOR + r")\s+|[<>]=?|=)?"
    r"(" + _MEASUREMENT_NUMBER + r")\s*"
    r"(" + _MEASUREMENT_UNIT_SUFFIX + r")(?!\w)"
    r"|"
    r"(?:(?:" + _MEASUREMENT_COMPARATOR + r")\s+|[<>]=?|=)"
    r"(" + _MEASUREMENT_NUMBER + r")(?!\d)"
    r"|"
    r"(\d+\.\d+(?:[eE][+-]?\d+)?)"
    r"|"
    r"(\d+(?:\.\d+)?[eE][+-]?\d+)"
    r")"
)
_RANGE_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*[-\u2013]\s*"
    r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*"
    r"(" + _MEASUREMENT_UNIT_SUFFIX + r")"
    r"(?!\w)"
)
_COMPARATOR_PREFIX_RE = re.compile(
    r"^(?:at or above|at or below|exceeds?|below|under|above|greater than|"
    r"less than|at least|at most|up to|no more than|no less than)\s+"
)


class ArticleWritingError(ValueError):
    """Base error for section-writing failures."""


class ArticleWritingIntegrityError(ArticleWritingError):
    """Unknown/cross-wired provenance or conflicting persistence content."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ParagraphRole(str, Enum):
    background = "background"
    method = "method"
    result = "result"
    limitation = "limitation"
    transition = "transition"
    discussion = "discussion"
    conclusion = "conclusion"


class InferenceKind(str, Enum):
    none_required = "none_required"
    bounded_inference = "bounded_inference"
    unsupported = "unsupported"


class TrustedValueRecord(_StrictModel):
    """Caller-asserted verified scalar value for one artifact field.

    The exact ``rendered_value`` is never sent to the model; prose may use it
    only through a locally issued ``[VALUE:...]`` token.  Arrays/curves must be
    marked ``prose_safe=False`` and remain figure-only.
    """

    schema_version: Literal["trusted-value-record.v1"] = "trusted-value-record.v1"
    artifact_id: str
    field: str
    rendered_value: str
    unit: str = ""
    source_hash: str = ""
    derivation: str = ""
    label: str = ""
    prose_safe: bool

    @field_validator("artifact_id", "field", "rendered_value")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("artifact_id/field/rendered_value must be non-empty")
        return value


class WriterProviderResult(_StrictModel):
    """Envelope returned by any section writer / repair provider."""

    schema_version: Literal["section-writer-result.v1"] = "section-writer-result.v1"
    response: Dict[str, Any]
    usage: Dict[str, Any] = Field(default_factory=dict)
    provider_model: str = "unknown"
    mock_llm: bool = False


SectionWriterProvider = Callable[[Mapping[str, Any]], WriterProviderResult]
FormatRepairProvider = Callable[[Mapping[str, Any]], WriterProviderResult]


class _ModelParagraph(_StrictModel):
    text_with_value_tokens: str
    claim_aliases: List[str] = Field(default_factory=list)
    figure_aliases: List[str] = Field(default_factory=list)
    literature_evidence_aliases: List[str] = Field(default_factory=list)
    paragraph_role: ParagraphRole
    inference_kind: InferenceKind
    inference_note: str = ""

    @field_validator("text_with_value_tokens")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("paragraph text must be non-empty")
        return value


class _ModelSectionResponse(_StrictModel):
    paragraphs: List[_ModelParagraph] = Field(min_length=1)
    deferred_claim_aliases: List[str] = Field(default_factory=list)
    author_notes: List[str] = Field(default_factory=list)


class _ModelTargetedParagraph(_ModelParagraph):
    paragraph_id: str


class _ModelTargetedResponse(_StrictModel):
    targeted_paragraphs: List[_ModelTargetedParagraph] = Field(min_length=1)


class ParagraphDraft(_StrictModel):
    schema_version: Literal["paragraph-draft.v1"] = "paragraph-draft.v1"
    paragraph_id: str
    role: ParagraphRole
    inference_kind: InferenceKind
    inference_note: str
    text_with_value_tokens: str
    rendered_text: str
    claim_ids: List[str] = Field(default_factory=list)
    figure_ids: List[str] = Field(default_factory=list)
    value_token_ids: List[str] = Field(default_factory=list)
    literature_evidence_ids: List[str] = Field(default_factory=list)
    word_count: int
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ParagraphSourceLedger(_StrictModel):
    schema_version: Literal["paragraph-source-ledger.v1"] = "paragraph-source-ledger.v1"
    paragraph_id: str
    section_id: str
    story_id: str
    claim_ids: List[str] = Field(default_factory=list)
    fact_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    value_token_ids: List[str] = Field(default_factory=list)
    figure_ids: List[str] = Field(default_factory=list)
    literature_evidence_ids: List[str] = Field(default_factory=list)
    inference_kind: InferenceKind
    inference_note: str
    scope: str
    scopes: List[str] = Field(default_factory=list)
    limits: List[str] = Field(default_factory=list)
    roles: List[str] = Field(default_factory=list)


class ArticleSectionDraft(_StrictModel):
    schema_version: Literal["article-section-draft.v1"] = "article-section-draft.v1"
    section_id: str
    title: str
    story_id: str
    architecture_id: str
    status: Literal["publishable", "needs_revision", "blocked"]
    tokenized_prose: str
    rendered_prose: str
    paragraphs: List[ParagraphDraft] = Field(default_factory=list)
    source_ledger: List[ParagraphSourceLedger] = Field(default_factory=list)
    figure_ids: List[str] = Field(default_factory=list)
    deferred_claim_aliases: List[str] = Field(default_factory=list)
    deferred_claim_ids: List[str] = Field(default_factory=list)
    author_notes: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    word_count: int
    target_word_range: List[int]
    model_status: Literal["available", "unavailable"]
    semantic_model: str = "none"
    usage: Dict[str, Any] = Field(default_factory=dict)
    repair_rounds: int = 0
    attempts: int = 0


class ArticleDraftBundle(_StrictModel):
    schema_version: Literal["article-draft-bundle.v1"] = "article-draft-bundle.v1"
    bundle_id: str
    plan_id: str
    ledger_id: str
    architecture_id: str
    story_id: str
    sections: List[ArticleSectionDraft]
    source_ledger: List[ParagraphSourceLedger] = Field(default_factory=list)
    deferred_claims: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    publishable: bool
    publishable_section_ids: List[str] = Field(default_factory=list)
    usage: Dict[str, Any] = Field(default_factory=dict)
    semantic_model: str = "none"
    model_status: Literal["available", "partial", "unavailable"]
    attempts: int = 0
    claim_alias_map: Dict[str, str] = Field(default_factory=dict)
    fact_alias_map: Dict[str, str] = Field(default_factory=dict)
    value_alias_map: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    figure_alias_map: Dict[str, str] = Field(default_factory=dict)


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


def _identity_dump(value: Any) -> Any:
    """Project models onto the stable identity view.

    The optional paragraph-level literature bridge was added after persisted
    Stage 10 assets already existed.  Missing values are parsed as ``[]`` by
    Pydantic, so only that empty compatibility field is omitted from hashes.
    Non-empty bindings remain content-addressed.
    """

    if isinstance(value, Mapping):
        return {
            str(key): _identity_dump(item)
            for key, item in value.items()
            if not (str(key) == "literature_evidence_ids" and item == [])
        }
    if isinstance(value, list):
        return [_identity_dump(item) for item in value]
    if isinstance(value, tuple):
        return [_identity_dump(item) for item in value]
    return value


def _identity_model_json(model: BaseModel) -> str:
    return _canonical_json(_identity_dump(model.model_dump(mode="json")))


def compute_bundle_id(
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    story_id: str,
    sections: Sequence[ArticleSectionDraft | Mapping[str, Any]],
) -> str:
    """Content-addressed Stage 10 bundle identity (public and deterministic)."""

    section_models = [
        (
            item
            if isinstance(item, ArticleSectionDraft)
            else ArticleSectionDraft.model_validate(item)
        )
        for item in sections
    ]
    return _digest(
        str(plan_id),
        str(ledger_id),
        str(architecture_id),
        str(story_id),
        [_identity_model_json(item) for item in section_models],
    )


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


def _slugify(text: str, limit: int = 24) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return (cleaned[:limit].strip("_")) or "item"


def _humanize_field(field: str) -> str:
    return str(field or "").replace("_", " ").replace("-", " ").strip()


def _target_word_range(purpose: str) -> List[int]:
    text = str(purpose or "").lower()
    if "background" in text or "intro" in text:
        return [700, 1200]
    if "method" in text:
        return [800, 1500]
    if "result" in text:
        return [800, 1500]
    if "discussion" in text:
        return [800, 1500]
    if "limitation" in text:
        return [300, 700]
    if "conclusion" in text:
        return [250, 500]
    return [500, 1000]


def _is_scalar_numeric_literal(text: str) -> bool:
    """True for a finite scalar numeric literal (no units/prose/markup)."""

    value = str(text or "").strip()
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value) is None:
        return False
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _aggregate_usage(usages: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    numeric: Dict[str, float] = {}
    non_numeric: Dict[str, Any] = {}
    for usage in usages:
        for key, value in dict(usage or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                numeric[key] = numeric.get(key, 0.0) + float(value)
            elif key not in non_numeric and value is not None:
                non_numeric[key] = value
    result: Dict[str, Any] = {}
    for key, value in numeric.items():
        result[key] = int(value) if float(value).is_integer() else round(value, 6)
    result.update(non_numeric)
    return result


def _usage_with_cost(usage: Mapping[str, Any]) -> Dict[str, Any]:
    """Add local cost-ledger pricing for qwen calls when tokens lack cost."""

    result = dict(usage or {})
    input_tokens = result.get("estimated_input_tokens")
    output_tokens = result.get("estimated_output_tokens")
    if (
        not result.get("estimated_cost_cny")
        and isinstance(input_tokens, (int, float))
        and isinstance(output_tokens, (int, float))
    ):
        result["estimated_cost_cny"] = round(
            estimate_call_cost_cny(
                WRITER_MODEL_NAME,
                max(0, int(input_tokens)),
                max(0, int(output_tokens)),
            ),
            6,
        )
    return result


def _positive_claims(ledger: ClaimLedgerResult) -> List[ClaimCard]:
    return [
        claim
        for claim in ledger.claims
        if claim.status in POSITIVE_CLAIM_STATUSES and claim.source_artifact_ids
    ]


def _limitation_claims(ledger: ClaimLedgerResult) -> List[ClaimCard]:
    return [
        claim
        for claim in ledger.claims
        if claim.status
        in {ClaimStatus.refuted, ClaimStatus.withdrawn, ClaimStatus.draft}
    ]


def _fact_by_claim(ledger: ClaimLedgerResult, errors: List[str]) -> Dict[str, Any]:
    fact_by_claim: Dict[str, Any] = {}
    for fact in ledger.facts:
        owners = [
            item
            for item in ledger.claims
            if item.metadata.get("fact_id") == fact.fact_id
            and item.metadata.get("claim_id") == item.claim_id
        ]
        if len(owners) != 1:
            if not owners:
                errors.append(f"fact {fact.fact_id!r} has no matching Stage 8 claim")
            else:
                errors.append(
                    f"fact {fact.fact_id!r} has ambiguous claim ownership "
                    "(multiple claims)"
                )
            continue
        claim = owners[0]
        if claim.claim_id in fact_by_claim:
            errors.append(f"claim {claim.claim_id!r} owns multiple Stage 8 facts")
            continue
        if fact.source_artifact_ids != claim.source_artifact_ids:
            errors.append(
                f"fact {fact.fact_id!r} source artifacts do not match claim "
                f"{claim.claim_id!r}"
            )
        fact_by_claim[claim.claim_id] = fact
    return fact_by_claim


def _claim_authorized_artifacts(
    claim: ClaimCard, fact_by_claim: Mapping[str, Any]
) -> List[str]:
    fact = fact_by_claim.get(claim.claim_id)
    if fact is not None:
        return sorted(set(fact.source_artifact_ids))
    return sorted(set(claim.source_artifact_ids))


def _claim_authorizes_value_field(
    claim: ClaimCard,
    fact_by_claim: Mapping[str, Any],
    artifact_id: str,
    field: str,
) -> bool:
    """Exact value-field authorization for one claim.

    A claim with value lineage authorizes only its exact (artifact_id, field)
    pairs.  Legacy claims without lineage keep artifact-level authorization.
    """

    lineage = claim.metadata.get("value_lineage") or []
    if lineage:
        return any(
            str(ref.get("artifact_id") or "") == artifact_id
            and str(ref.get("field") or "") == field
            for ref in lineage
        )
    return artifact_id in _claim_authorized_artifacts(claim, fact_by_claim)


def _inventory(plan: ArticleDirectorPlan, ledger: ClaimLedgerResult) -> Dict[str, int]:
    return {
        "positive_claim_count": len(_positive_claims(ledger)),
        "fact_count": len(ledger.facts),
        "limitation_claim_count": len(_limitation_claims(ledger)),
        "charter_goal_count": len(plan.charter.goals),
        "success_criterion_count": len(plan.charter.success_criteria),
    }


def _story_claims(
    story: StoryCandidate,
) -> List[str]:
    claims = set()
    for section in story.section_contracts:
        claims.update(item.claim_id for item in section.claim_bindings)
    for figure in story.figure_contracts:
        claims.update(item.claim_id for item in figure.claim_bindings)
    return sorted(claims)


def _validate_story_figure(
    story_id: str,
    figure: FigureContract,
    claims_by_id: Mapping[str, ClaimCard],
    fact_by_claim: Mapping[str, Any],
    errors: List[str],
) -> None:
    all_claim_ids = set(claims_by_id)
    unknown = sorted(set(figure.claim_ids) - all_claim_ids)
    if unknown:
        errors.append(
            f"{story_id} figure {figure.role_key!r} references unknown "
            f"claims: {unknown}"
        )
    for fact_id in figure.fact_ids:
        owner = next(
            (
                claim_id
                for claim_id, fact in fact_by_claim.items()
                if fact.fact_id == fact_id
            ),
            None,
        )
        if owner is None or owner not in set(figure.claim_ids):
            errors.append(
                f"{story_id} figure {figure.role_key!r} fact {fact_id!r} "
                "does not correspond to a bound claim in the figure"
            )
    authorized = {
        artifact
        for claim_id in figure.claim_ids
        if claim_id in claims_by_id
        for artifact in _claim_authorized_artifacts(
            claims_by_id[claim_id], fact_by_claim
        )
    }
    selected = {binding.artifact_id for binding in figure.artifact_bindings}
    if selected and not selected <= authorized:
        errors.append(
            f"{story_id} figure {figure.role_key!r} attaches unrelated "
            f"artifacts {sorted(selected - authorized)} not authorized by "
            "the bound facts"
        )


def _validate_story_contract(
    story: StoryCandidate,
    ledger: ClaimLedgerResult,
    fact_by_claim: Mapping[str, Any],
    errors: List[str],
    warnings: List[str],
) -> None:
    """Revalidate the selected story contract as immutable input."""

    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    positive_ids = {claim.claim_id for claim in _positive_claims(ledger)}
    limitation_ids = {claim.claim_id for claim in _limitation_claims(ledger)}
    section_ids = [item.section_id for item in story.section_contracts]
    if len(set(section_ids)) != len(section_ids):
        errors.append(f"{story.story_id} has duplicate section IDs")
    figure_ids = [item.figure_id for item in story.figure_contracts]
    if len(set(figure_ids)) != len(figure_ids):
        errors.append(f"{story.story_id} has duplicate figure IDs")
    figure_by_id = {item.figure_id: item for item in story.figure_contracts}
    section_refs: Dict[str, List[str]] = {figure_id: [] for figure_id in figure_ids}

    for section in story.section_contracts:
        seen: Dict[str, str] = {}
        for binding in section.claim_bindings:
            claim_id = binding.claim_id
            if claim_id not in claims_by_id:
                errors.append(
                    f"{story.story_id} section {section.section_id!r} "
                    f"references unknown claim {claim_id!r}"
                )
                continue
            if binding.role == "positive" and claim_id not in positive_ids:
                errors.append(
                    f"{story.story_id} section {section.section_id!r} uses "
                    f"non-positive claim {claim_id!r} as positive support"
                )
            if binding.role in {"limitation", "counterevidence"}:
                if claim_id in positive_ids:
                    warnings.append(
                        f"{story.story_id} section {section.section_id!r} uses "
                        f"positive claim {claim_id!r} in a "
                        "limitation/counterevidence role; role retained as "
                        "organizational framing"
                    )
                elif claim_id not in limitation_ids:
                    errors.append(
                        f"{story.story_id} section {section.section_id!r} uses "
                        f"claim {claim_id!r} in a limitation/counterevidence "
                        "role but the claim is not a limitation claim"
                    )
            if claim_id in seen and seen[claim_id] != binding.role:
                errors.append(
                    f"{story.story_id} section {section.section_id!r} binds "
                    f"claim {claim_id!r} with conflicting roles"
                )
            elif claim_id in seen:
                warnings.append(
                    f"{story.story_id} section {section.section_id!r} repeats "
                    f"claim binding {claim_id!r}"
                )
            seen[claim_id] = binding.role
        for figure_id in section.figure_ids:
            if figure_id not in figure_by_id:
                errors.append(
                    f"{story.story_id} section {section.section_id!r} "
                    f"references unknown figure {figure_id!r}"
                )
                continue
            section_refs[figure_id].append(section.section_id)

    for figure in story.figure_contracts:
        binding_ids = {item.claim_id for item in figure.claim_bindings}
        if set(figure.claim_ids) != binding_ids:
            errors.append(
                f"{story.story_id} figure {figure.role_key!r} claim_ids do "
                "not match its claim_bindings"
            )
        seen: Dict[str, str] = {}
        for binding in figure.claim_bindings:
            claim_id = binding.claim_id
            if binding.role == "positive" and claim_id not in positive_ids:
                errors.append(
                    f"{story.story_id} figure {figure.role_key!r} uses "
                    f"non-positive claim {claim_id!r} as positive support"
                )
            if binding.role in {"limitation", "counterevidence"}:
                if claim_id in positive_ids:
                    warnings.append(
                        f"{story.story_id} figure {figure.role_key!r} uses "
                        f"positive claim {claim_id!r} in a "
                        "limitation/counterevidence role; role retained as "
                        "organizational framing"
                    )
                elif claim_id not in limitation_ids:
                    errors.append(
                        f"{story.story_id} figure {figure.role_key!r} uses "
                        f"claim {claim_id!r} in a limitation/counterevidence "
                        "role but the claim is not a limitation claim"
                    )
            if claim_id in seen and seen[claim_id] != binding.role:
                errors.append(
                    f"{story.story_id} figure {figure.role_key!r} binds "
                    f"claim {claim_id!r} with conflicting roles"
                )
            elif claim_id in seen:
                warnings.append(
                    f"{story.story_id} figure {figure.role_key!r} repeats "
                    f"claim binding {claim_id!r}"
                )
            seen[claim_id] = binding.role
        _validate_story_figure(
            story.story_id, figure, claims_by_id, fact_by_claim, errors
        )
        if figure.kind in QUANTITATIVE_KINDS:
            for binding in figure.claim_bindings:
                if binding.role != "positive":
                    continue
                fact = fact_by_claim.get(binding.claim_id)
                if fact is None:
                    errors.append(
                        f"{story.story_id} figure {figure.role_key!r} "
                        f"positive claim {binding.claim_id!r} has no Stage 8 "
                        "FactRecord"
                    )
                elif fact.fact_id not in set(figure.fact_ids):
                    errors.append(
                        f"{story.story_id} figure {figure.role_key!r} "
                        f"positive claim {binding.claim_id!r} is missing its "
                        f"fact_id {fact.fact_id!r} in fact_ids"
                    )
        refs = section_refs.get(figure.figure_id, [])
        if not refs:
            errors.append(
                f"{story.story_id} figure {figure.role_key!r} is not assigned "
                "to any section"
            )
        elif figure.section_target and figure.section_target not in refs:
            errors.append(
                f"{story.story_id} figure {figure.role_key!r} section_target "
                f"{figure.section_target!r} is not one of its assigned "
                f"sections {refs}"
            )


def _validate_inputs(
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord],
    errors: List[str],
    warnings: List[str],
) -> Tuple[Optional[StoryCandidate], Dict[str, Any]]:
    """Validate plan/ledger/architecture identity and selected story links."""

    if ledger.validation_errors:
        errors.append(
            "ledger is not valid writing input (it carries validation errors)"
        )
    if architecture.validation_errors:
        errors.append(
            "architecture is not valid writing input (it carries validation " "errors)"
        )
    if ledger.source_plan_id is not None and ledger.source_plan_id != plan.plan_id:
        errors.append(
            f"ledger source_plan_id {ledger.source_plan_id!r} does not match "
            f"plan {plan.plan_id!r}"
        )
    if architecture.source_plan_id is None:
        errors.append(
            "architecture lacks Stage 9 source_plan_id provenance; legacy "
            "data is not trusted for writing"
        )
    elif architecture.source_plan_id != plan.plan_id:
        errors.append(
            f"architecture source_plan_id {architecture.source_plan_id!r} "
            f"does not match plan {plan.plan_id!r}"
        )
    if architecture.source_ledger_id is None:
        errors.append(
            "architecture lacks Stage 9 source_ledger_id provenance; legacy "
            "data is not trusted for writing"
        )
    elif architecture.source_ledger_id != ledger.ledger_id:
        errors.append(
            f"architecture source_ledger_id {architecture.source_ledger_id!r} "
            f"does not match ledger {ledger.ledger_id!r}"
        )
    if not architecture.artifact_inventory:
        errors.append(
            "architecture carries no Stage 9 artifact inventory; legacy data "
            "is not trusted for writing"
        )
    else:
        try:
            recomputed = compute_architecture_id(
                plan.plan_id,
                ledger.ledger_id,
                architecture.artifact_inventory,
                architecture.missing_work_handoffs,
                architecture.stories,
            )
        except ValidationError as exc:
            errors.append(f"architecture content cannot be re-identified: {exc}")
            recomputed = None
        if recomputed is not None and recomputed != architecture.architecture_id:
            errors.append(
                f"architecture_id {architecture.architecture_id!r} does not "
                f"match recomputed identity {recomputed!r} (content changed)"
            )
    expected_inventory = _inventory(plan, ledger)
    actual_inventory = dict(architecture.deterministic_inventory or {})
    for key, expected in expected_inventory.items():
        if key in actual_inventory and actual_inventory[key] != expected:
            errors.append(
                f"architecture deterministic_inventory[{key}]={actual_inventory[key]} "
                f"does not match plan/ledger ({expected})"
            )
    if not selected_story_id.strip():
        errors.append("selected_story_id is required")
    story = None
    if selected_story_id.strip():
        story = next(
            (
                item
                for item in architecture.stories
                if item.story_id == selected_story_id
            ),
            None,
        )
        if story is None:
            errors.append(
                f"selected_story_id {selected_story_id!r} is not present in "
                "the architecture result"
            )
            return None, {}

    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    if len(claims_by_id) != len(ledger.claims):
        errors.append("ledger contains duplicate claim IDs")
    fact_ids = {fact.fact_id for fact in ledger.facts}
    if len(fact_ids) != len(ledger.facts):
        errors.append("ledger contains duplicate fact IDs")
    fact_by_claim = _fact_by_claim(ledger, errors)
    if story is not None:
        for claim_id in _story_claims(story):
            if claim_id not in claims_by_id:
                errors.append(f"selected story references unknown claim {claim_id!r}")
        _validate_story_contract(story, ledger, fact_by_claim, errors, warnings)

    binding_fields: Dict[Tuple[str, str], bool] = {}
    if story is not None:
        authorized = {
            artifact
            for figure in story.figure_contracts
            for claim_id in figure.claim_ids
            if claim_id in claims_by_id
            for artifact in _claim_authorized_artifacts(
                claims_by_id[claim_id], fact_by_claim
            )
        }
        for figure in story.figure_contracts:
            for binding in figure.artifact_bindings:
                if binding.artifact_id not in authorized:
                    errors.append(
                        f"{story.story_id} figure {figure.role_key!r} binds "
                        f"artifact {binding.artifact_id!r} not authorized by "
                        "bound claims"
                    )
                for field in binding.selected_fields:
                    binding_fields[(binding.artifact_id, field)] = True
        for claim_id in _story_claims(story):
            claim = claims_by_id.get(claim_id)
            if claim is None:
                continue
            for ref in claim.metadata.get("value_lineage") or []:
                artifact_id = str(ref.get("artifact_id") or "")
                field = str(ref.get("field") or "")
                if artifact_id and field:
                    binding_fields[(artifact_id, field)] = True
    inventory_by_id = {
        item.artifact_id: item for item in architecture.artifact_inventory
    }
    seen_values: set[Tuple[str, str]] = set()
    for record in value_records:
        key = (record.artifact_id, record.field)
        if key in seen_values:
            errors.append(
                f"duplicate trusted value record for {record.artifact_id!r}:"
                f"{record.field!r}"
            )
            continue
        seen_values.add(key)
        descriptor = inventory_by_id.get(record.artifact_id)
        if descriptor is None:
            errors.append(
                f"trusted value record artifact {record.artifact_id!r} is not "
                "present in the Stage 9 artifact inventory"
            )
            continue
        if record.field not in set(descriptor.fields):
            errors.append(
                f"trusted value record field {record.field!r} is not declared "
                f"for artifact {record.artifact_id!r}"
            )
        if record.prose_safe:
            if not descriptor.sha256:
                errors.append(
                    f"artifact {record.artifact_id!r} has no sha256 in the "
                    "Stage 9 artifact inventory; prose-safe values require a "
                    "hash"
                )
            elif record.source_hash != descriptor.sha256:
                errors.append(
                    f"trusted value record source_hash does not match the "
                    f"Stage 9 artifact inventory sha256 for "
                    f"{record.artifact_id!r}:{record.field!r}"
                )
            if not _is_scalar_numeric_literal(record.rendered_value):
                errors.append(
                    f"prose-safe rendered_value for "
                    f"{record.artifact_id!r}:{record.field!r} must be a "
                    "finite scalar numeric literal"
                )
        if key not in binding_fields:
            errors.append(
                f"trusted value record {record.artifact_id!r}:{record.field!r} "
                "does not correspond to any Stage 9 artifact-field binding"
            )
    return story, fact_by_claim


def _build_alias_maps(
    story: StoryCandidate,
    ledger: ClaimLedgerResult,
    value_records: Sequence[TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    claim_ids = _story_claims(story)
    claim_alias_to_id: Dict[str, str] = {}
    claim_id_to_alias: Dict[str, str] = {}
    for index, claim_id in enumerate(claim_ids, start=1):
        claim = next(item for item in ledger.claims if item.claim_id == claim_id)
        alias = f"C{index:02d}_{_slugify(claim.statement)}"
        claim_alias_to_id[alias] = claim_id
        claim_id_to_alias[claim_id] = alias
    fact_alias_map: Dict[str, str] = {}
    for alias, claim_id in claim_alias_to_id.items():
        fact = fact_by_claim.get(claim_id)
        if fact is not None:
            fact_alias_map[alias] = fact.fact_id
    figure_ids = sorted(item.figure_id for item in story.figure_contracts)
    figure_alias_to_id: Dict[str, str] = {}
    figure_id_to_alias: Dict[str, str] = {}
    for index, figure_id in enumerate(figure_ids, start=1):
        figure = next(
            item for item in story.figure_contracts if item.figure_id == figure_id
        )
        alias = f"FIG{index:02d}_{_slugify(figure.role_key)}"
        figure_alias_to_id[alias] = figure_id
        figure_id_to_alias[figure_id] = alias
    value_alias_map: Dict[str, Dict[str, Any]] = {}
    records = sorted(
        value_records,
        key=lambda item: (item.artifact_id, item.field),
    )
    for index, record in enumerate(records, start=1):
        alias = f"V{index:02d}_{_slugify(record.field, limit=16).upper()}"
        value_alias_map[alias] = {
            "artifact_id": record.artifact_id,
            "field": record.field,
            "label": record.label or _humanize_field(record.field),
            "unit": record.unit,
            "prose_safe": bool(record.prose_safe),
        }
    claim_value_aliases: Dict[str, List[str]] = {}
    for claim in ledger.claims:
        refs = claim.metadata.get("value_lineage") or []
        if not refs:
            continue
        aliases_for_claim: List[str] = []
        for ref in refs:
            artifact_id = str(ref.get("artifact_id") or "")
            field = str(ref.get("field") or "")
            for alias, info in value_alias_map.items():
                if (
                    info["artifact_id"] == artifact_id
                    and info["field"] == field
                    and info["prose_safe"]
                    and alias not in aliases_for_claim
                ):
                    aliases_for_claim.append(alias)
        if aliases_for_claim:
            claim_value_aliases[claim.claim_id] = aliases_for_claim
    return {
        "claim_alias_to_id": claim_alias_to_id,
        "claim_id_to_alias": claim_id_to_alias,
        "fact_alias_map": fact_alias_map,
        "figure_alias_to_id": figure_alias_to_id,
        "figure_id_to_alias": figure_id_to_alias,
        "value_alias_map": value_alias_map,
        "claim_value_aliases": claim_value_aliases,
    }


def _build_section_request(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    story: StoryCandidate,
    section: SectionContract,
    all_sections: Sequence[SectionContract],
    aliases: Mapping[str, Any],
    value_records_by_key: Mapping[Tuple[str, str], TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    literature_context: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    section_claim_ids = sorted({item.claim_id for item in section.claim_bindings})
    figure_aliases = [
        aliases["figure_id_to_alias"][figure_id] for figure_id in section.figure_ids
    ]
    figure_by_id = {figure.figure_id: figure for figure in story.figure_contracts}
    section_figures = [figure_by_id[figure_id] for figure_id in section.figure_ids]
    value_tokens: List[Dict[str, Any]] = []
    for figure in section_figures:
        for binding in figure.artifact_bindings:
            for field in binding.selected_fields:
                record = value_records_by_key.get((binding.artifact_id, field))
                if record is None or not record.prose_safe:
                    continue
                alias = next(
                    alias
                    for alias, info in aliases["value_alias_map"].items()
                    if info["artifact_id"] == binding.artifact_id
                    and info["field"] == field
                )
                info = aliases["value_alias_map"][alias]
                authorizing_aliases = sorted(
                    {
                        aliases["claim_id_to_alias"][item.claim_id]
                        for item in figure.claim_bindings
                        if item.claim_id in claims_by_id
                        and _claim_authorizes_value_field(
                            claims_by_id[item.claim_id],
                            fact_by_claim,
                            binding.artifact_id,
                            field,
                        )
                    }
                )
                value_tokens.append(
                    {
                        "token": f"[VALUE:{alias}]",
                        "alias": alias,
                        "label": info["label"],
                        "unit": info["unit"],
                        "authorized_claim_aliases": authorizing_aliases,
                        "meaning": (f"{info['label']} from {binding.artifact_id}"),
                    }
                )
    existing_tokens = {item["token"] for item in value_tokens}
    claim_value_aliases = aliases.get("claim_value_aliases") or {}
    for binding in section.claim_bindings:
        for alias in claim_value_aliases.get(binding.claim_id, []):
            token = f"[VALUE:{alias}]"
            if token in existing_tokens:
                continue
            info = aliases["value_alias_map"].get(alias)
            if info is None or not info["prose_safe"]:
                continue
            existing_tokens.add(token)
            claim_alias = aliases["claim_id_to_alias"][binding.claim_id]
            value_tokens.append(
                {
                    "token": token,
                    "alias": alias,
                    "label": info["label"],
                    "unit": info["unit"],
                    "authorized_claim_aliases": [claim_alias],
                    "meaning": "claim-authorizing value",
                }
            )
    claims: List[Dict[str, Any]] = []
    for claim_id in section_claim_ids:
        claim = claims_by_id[claim_id]
        alias = aliases["claim_id_to_alias"][claim_id]
        fact = fact_by_claim.get(claim_id)
        roles = [
            item.role for item in section.claim_bindings if item.claim_id == claim_id
        ]
        claims.append(
            {
                "claim_alias": alias,
                "statement": claim.statement,
                "scope": claim.scope,
                "strength": claim.strength.value,
                "status": claim.status.value,
                "roles": sorted(set(roles)),
                "limits": list(claim.metadata.get("limits") or []),
                "synthesis_contract": dict(
                    claim.metadata.get("synthesis_contract") or {}
                ),
                "fact_statement": fact.statement if fact is not None else None,
                "source_artifact_ids": list(claim.source_artifact_ids),
            }
        )
    figures: List[Dict[str, Any]] = []
    for figure in section_figures:
        figures.append(
            {
                "figure_alias": aliases["figure_id_to_alias"][figure.figure_id],
                "role_key": figure.role_key,
                "kind": figure.kind,
                "story_role": figure.story_role,
                "caption_intent": figure.caption_intent,
                "claim_aliases": [
                    aliases["claim_id_to_alias"][claim_id]
                    for claim_id in figure.claim_ids
                ],
                "artifact_bindings": [
                    {
                        "artifact_id": binding.artifact_id,
                        "selected_fields": list(binding.selected_fields),
                    }
                    for binding in figure.artifact_bindings
                ],
                "source_mode": figure.source_mode,
            }
        )
    target_range = _target_word_range(section.purpose)
    other_sections = [
        {
            "section_id": item.section_id,
            "heading": item.heading,
            "purpose": item.purpose,
            "figure_roles": [
                aliases["figure_id_to_alias"][figure_id]
                for figure_id in item.figure_ids
                if figure_id in aliases["figure_id_to_alias"]
            ],
        }
        for item in all_sections
        if item.section_id != section.section_id
    ]
    return {
        "task": (
            "Write one article section with evidence tokens. "
            "Organization-only; all IDs/aliases are local."
        ),
        "section_roles_allowed": (
            "background|transition|discussion"
            if not section.claim_bindings
            else (
                "background|method|result|limitation|transition|discussion|"
                "conclusion"
            )
        ),
        "question": plan.charter.question,
        "charter_scope": plan.charter.scope,
        "story": {
            "story_id": story.story_id,
            "story_shape": story.story_shape,
            "central_thesis": story.central_thesis,
        },
        "section": {
            "section_id": section.section_id,
            "heading": section.heading,
            "purpose": section.purpose,
            "target_word_range": target_range,
            "claim_bindings": [
                {
                    "claim_alias": aliases["claim_id_to_alias"][item.claim_id],
                    "role": item.role,
                }
                for item in section.claim_bindings
            ],
            "figure_aliases": figure_aliases,
        },
        "claims": claims,
        "figures": figures,
        "values": value_tokens,
        "other_sections_outline": other_sections,
        "response_contract": {
            "paragraphs": [
                {
                    "text_with_value_tokens": "string; [VALUE:...] tokens allowed",
                    "claim_aliases": ["C01_..."],
                    "figure_aliases": ["FIG01_..."],
                    "literature_evidence_aliases": ["E01_..."],
                    "paragraph_role": (
                        (
                            "background|transition|discussion"
                            if not section.claim_bindings
                            else (
                                "background|method|result|limitation|"
                                "transition|discussion|conclusion"
                            )
                        )
                    ),
                    "inference_kind": ("none_required|bounded_inference|unsupported"),
                    "inference_note": "string",
                }
            ],
            "deferred_claim_aliases": ["C02_..."],
            "author_notes": ["string"],
        },
        **(
            {"literature_context": dict(literature_context)}
            if literature_context is not None
            else {}
        ),
    }


def _canonical_literal(literal: str) -> str:
    text = str(literal or "").strip().lower()
    text = _COMPARATOR_PREFIX_RE.sub("", text)
    text = re.sub(r"^[<>]=?|=+", "", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"percent$", "%", text)
    return text


def _iter_measurement_literals(text: str):
    for match in _MEASUREMENT_LITERAL_RE.finditer(text):
        if match.group(1) is not None:
            canonical = _canonical_literal(f"{match.group(1)} {match.group(2)}")
        elif match.group(3) is not None:
            canonical = _canonical_literal(match.group(3))
        elif match.group(4) is not None:
            canonical = _canonical_literal(match.group(4))
        else:
            canonical = _canonical_literal(match.group(5))
        yield match.group(0), canonical


def _literal_allowlist_from_texts(texts: Sequence[str]) -> frozenset[str]:
    literals: set[str] = set()
    for text in texts:
        source = str(text or "")
        for _, canonical in _iter_measurement_literals(source):
            literals.add(canonical)
        for match in _RANGE_LITERAL_RE.finditer(source):
            unit = match.group(3)
            literals.add(_canonical_literal(f"{match.group(1)} {unit}"))
            literals.add(_canonical_literal(f"{match.group(2)} {unit}"))
    return frozenset(literals)


def _source_literal_allowlist(plan: ArticleDirectorPlan) -> frozenset[str]:
    """Measurement-like literals bound by accepted immutable plan fields.

    Canonicalization proves only source identity of the literal (for example
    ``2 percent`` and ``2%`` are the same source-bound constant).  It never
    proves that a comparator/achievement statement is true; whether a target
    was met remains Stage 11 scientific-review responsibility.  Allowing a
    source constant in prose is intentional fail-open semantic handling, not
    permission to treat a target as achieved.
    """

    return _literal_allowlist_from_texts(
        [
            plan.question,
            plan.charter.question,
            plan.charter.scope,
            *plan.charter.goals,
            *plan.charter.success_criteria,
            *plan.charter.constraints,
            plan.capability.supported_scope,
            *plan.capability.accepted_assumptions,
        ]
    )


def _claim_literal_allowlist(
    claims_by_id: Mapping[str, ClaimCard],
    claim_ids: Sequence[str],
) -> frozenset[str]:
    return _literal_allowlist_from_texts(
        [
            text
            for claim_id in claim_ids
            if claim_id in claims_by_id
            for text in (
                claims_by_id[claim_id].statement,
                claims_by_id[claim_id].scope,
            )
        ]
    )


def _verify_no_invented_numbers(
    text: str,
    errors: List[str],
    warnings: List[str],
    label: str,
    source_literals: frozenset[str] = frozenset(),
) -> None:
    stripped = _VALUE_TOKEN_RE.sub("", text)
    for token, canonical in _iter_measurement_literals(stripped):
        if canonical not in source_literals:
            errors.append(
                f"{label} contains invented numeric content {token!r} outside "
                "an allowed [VALUE:...] token or source-bound charter literal"
            )
    for token in _PLAIN_INTEGER_RE.findall(stripped):
        warnings.append(
            f"{label} contains structural integer {token!r} not tied to a "
            "trusted value token"
        )


def _assemble_section(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture_id: str,
    story: StoryCandidate,
    section: SectionContract,
    aliases: Mapping[str, Any],
    value_records_by_key: Mapping[Tuple[str, str], TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    raw_response: Mapping[str, Any],
    semantic_model: str,
    usage: Mapping[str, Any],
    model_status: Literal["available", "unavailable"],
    repair_rounds: int,
    attempts: int,
    preserved_literature_evidence_ids: Optional[Mapping[str, Sequence[str]]] = None,
) -> ArticleSectionDraft:
    errors: List[str] = []
    warnings: List[str] = []
    try:
        model_response = _ModelSectionResponse.model_validate(dict(raw_response))
    except ValidationError as exc:
        return ArticleSectionDraft(
            section_id=section.section_id,
            title=section.heading,
            story_id=story.story_id,
            architecture_id=architecture_id,
            status="needs_revision",
            tokenized_prose="",
            rendered_prose="",
            paragraphs=[],
            source_ledger=[],
            figure_ids=list(section.figure_ids),
            author_notes=[],
            warnings=warnings,
            errors=[f"malformed section response: {exc}"],
            word_count=0,
            target_word_range=_target_word_range(section.purpose),
            model_status=model_status,
            semantic_model=semantic_model,
            usage=dict(usage),
            repair_rounds=repair_rounds,
            attempts=attempts,
        )

    source_literals = _source_literal_allowlist(plan)
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    claim_alias_to_id = aliases["claim_alias_to_id"]
    figure_alias_to_id = aliases["figure_alias_to_id"]
    value_alias_map = aliases["value_alias_map"]
    literature_alias_to_id = aliases.get("literature_alias_to_id") or {}
    section_roles: Dict[str, str] = {
        item.claim_id: item.role for item in section.claim_bindings
    }
    assigned_claims = {item.claim_id for item in section.claim_bindings}
    allowed_claims = set(assigned_claims)
    allowed_figures = set(section.figure_ids)
    section_figures = [
        figure
        for figure in story.figure_contracts
        if figure.figure_id in allowed_figures
    ]
    allowed_value_pairs: set[Tuple[str, str]] = {
        (binding.artifact_id, field)
        for figure in section_figures
        for binding in figure.artifact_bindings
        for field in binding.selected_fields
    }
    value_authorizing_claims: Dict[Tuple[str, str], set[str]] = {}
    for figure in section_figures:
        for binding in figure.artifact_bindings:
            for field in binding.selected_fields:
                authorized_claims = {
                    item.claim_id
                    for item in figure.claim_bindings
                    if item.claim_id in claims_by_id
                    and _claim_authorizes_value_field(
                        claims_by_id[item.claim_id],
                        fact_by_claim,
                        binding.artifact_id,
                        field,
                    )
                }
                value_authorizing_claims.setdefault(
                    (binding.artifact_id, field), set()
                ).update(authorized_claims)
    claim_value_aliases = aliases.get("claim_value_aliases") or {}
    for claim_id in assigned_claims:
        for alias in claim_value_aliases.get(claim_id, []):
            info = value_alias_map.get(alias)
            if info is None or not info["prose_safe"]:
                continue
            key = (info["artifact_id"], info["field"])
            allowed_value_pairs.add(key)
            value_authorizing_claims.setdefault(key, set()).add(claim_id)
    cited_claims: set[str] = set()
    cited_figures: set[str] = set()
    paragraphs: List[ParagraphDraft] = []
    source_ledger: List[ParagraphSourceLedger] = []
    for index, model_paragraph in enumerate(model_response.paragraphs, start=1):
        paragraph_id = f"{section.section_id}-p{index:02d}"
        paragraph_errors: List[str] = []
        paragraph_warnings: List[str] = []
        claim_ids: List[str] = []
        for alias in model_paragraph.claim_aliases:
            claim_id = claim_alias_to_id.get(alias)
            if claim_id is None:
                paragraph_errors.append(
                    f"{paragraph_id} references unknown claim alias {alias!r}"
                )
                continue
            if claim_id not in allowed_claims:
                paragraph_errors.append(
                    f"{paragraph_id} references claim alias {alias!r} that is "
                    "not assigned to this section"
                )
                continue
            claim_ids.append(claim_id)
            cited_claims.add(claim_id)
        figure_ids: List[str] = []
        for alias in model_paragraph.figure_aliases:
            figure_id = figure_alias_to_id.get(alias)
            if figure_id is None:
                paragraph_errors.append(
                    f"{paragraph_id} references unknown figure alias {alias!r}"
                )
                continue
            if figure_id not in allowed_figures:
                paragraph_errors.append(
                    f"{paragraph_id} references figure alias {alias!r} that "
                    "is not assigned to this section"
                )
                continue
            figure_ids.append(figure_id)
            cited_figures.add(figure_id)
        if (
            preserved_literature_evidence_ids is not None
            and paragraph_id in preserved_literature_evidence_ids
        ):
            literature_evidence_ids = list(
                preserved_literature_evidence_ids[paragraph_id]
            )
        else:
            literature_evidence_ids = []
            for alias in model_paragraph.literature_evidence_aliases:
                evidence_id = literature_alias_to_id.get(alias)
                if evidence_id is None:
                    paragraph_warnings.append(
                        f"{paragraph_id} references unknown literature evidence "
                        f"alias {alias!r}; binding ignored"
                    )
                    continue
                literature_evidence_ids.append(evidence_id)
        value_token_ids: List[str] = []
        for alias in _VALUE_TOKEN_RE.findall(model_paragraph.text_with_value_tokens):
            info = value_alias_map.get(alias)
            if info is None:
                paragraph_errors.append(
                    f"{paragraph_id} references unknown value token alias " f"{alias!r}"
                )
                continue
            if not info["prose_safe"]:
                paragraph_errors.append(
                    f"{paragraph_id} uses figure-only value token "
                    f"[VALUE:{alias}] in prose"
                )
                continue
            value_key = (info["artifact_id"], info["field"])
            if value_key not in allowed_value_pairs:
                paragraph_errors.append(
                    f"{paragraph_id} value token [VALUE:{alias}] is not "
                    "authorized by any figure binding or claim-value lineage "
                    "in this section"
                )
                continue
            value_token_ids.append(alias)
        cited_claim_set = set(claim_ids)
        for alias in value_token_ids:
            info = value_alias_map[alias]
            value_key = (info["artifact_id"], info["field"])
            authorizers = value_authorizing_claims.get(value_key, set())
            if not (cited_claim_set & authorizers):
                paragraph_errors.append(
                    f"{paragraph_id} value token [VALUE:{alias}] is not "
                    "authorized by any cited claim (figure binding or "
                    "claim-value lineage)"
                )

        def _render(text: str) -> str:
            rendered = str(text)
            for alias in _VALUE_TOKEN_RE.findall(rendered):
                info = value_alias_map.get(alias)
                if info is None or not info["prose_safe"]:
                    continue
                record = value_records_by_key.get((info["artifact_id"], info["field"]))
                if record is None:
                    continue
                replacement = record.rendered_value
                if record.unit:
                    replacement = f"{replacement} {record.unit}"
                rendered = rendered.replace(f"[VALUE:{alias}]", replacement)
            return rendered

        rendered_text = _render(model_paragraph.text_with_value_tokens)
        _verify_no_invented_numbers(
            model_paragraph.text_with_value_tokens,
            paragraph_errors,
            paragraph_warnings,
            paragraph_id,
            source_literals | _claim_literal_allowlist(claims_by_id, claim_ids),
        )
        if model_paragraph.paragraph_role.value == "result" and not claim_ids:
            paragraph_errors.append(
                f"{paragraph_id} is a {model_paragraph.paragraph_role.value} "
                "paragraph but cites no claim aliases"
            )
        elif (
            model_paragraph.paragraph_role.value in {"method", "limitation"}
            and not claim_ids
        ):
            if literature_evidence_ids:
                paragraph_warnings.append(
                    f"{paragraph_id} is a literature-supported "
                    f"{model_paragraph.paragraph_role.value} paragraph with "
                    "no experimental Claim binding"
                )
            else:
                paragraph_errors.append(
                    f"{paragraph_id} is a "
                    f"{model_paragraph.paragraph_role.value} paragraph but "
                    "cites neither claim aliases nor literature evidence"
                )
        if model_paragraph.inference_kind == InferenceKind.bounded_inference and (
            not claim_ids or not model_paragraph.inference_note.strip()
        ):
            paragraph_errors.append(
                f"{paragraph_id} bounded_inference requires at least one "
                "claim alias and a non-empty inference_note"
            )
        if model_paragraph.inference_kind == InferenceKind.unsupported:
            paragraph_warnings.append(
                f"{paragraph_id} declares unsupported inference; no source "
                "claim can be bound"
            )
        fact_ids = sorted(
            {
                fact_by_claim[claim_id].fact_id
                for claim_id in claim_ids
                if claim_id in fact_by_claim
            }
        )
        artifact_ids = sorted(
            {value_alias_map[alias]["artifact_id"] for alias in value_token_ids}
            | {
                binding.artifact_id
                for figure_id in figure_ids
                for figure in story.figure_contracts
                if figure.figure_id == figure_id
                for binding in figure.artifact_bindings
            }
            | {
                artifact
                for claim_id in claim_ids
                if claim_id in claims_by_id
                for artifact in claims_by_id[claim_id].source_artifact_ids
            }
            | {
                artifact
                for fact in ledger.facts
                if fact.fact_id in fact_ids
                for artifact in fact.source_artifact_ids
            }
        )
        scopes = sorted(
            {
                claims_by_id[claim_id].scope
                for claim_id in claim_ids
                if claim_id in claims_by_id and claims_by_id[claim_id].scope.strip()
            }
        )
        limits = sorted(
            {
                limit
                for claim_id in claim_ids
                if claim_id in claims_by_id
                for limit in (claims_by_id[claim_id].metadata.get("limits") or [])
            }
        )
        roles = sorted(
            {
                section_roles[claim_id]
                for claim_id in claim_ids
                if claim_id in section_roles
            }
        )
        word_count = len(re.findall(r"\S+", model_paragraph.text_with_value_tokens))
        paragraphs.append(
            ParagraphDraft(
                paragraph_id=paragraph_id,
                role=model_paragraph.paragraph_role,
                inference_kind=model_paragraph.inference_kind,
                inference_note=model_paragraph.inference_note,
                text_with_value_tokens=model_paragraph.text_with_value_tokens,
                rendered_text=rendered_text,
                claim_ids=sorted(set(claim_ids)),
                figure_ids=sorted(set(figure_ids)),
                value_token_ids=sorted(set(value_token_ids)),
                literature_evidence_ids=sorted(set(literature_evidence_ids)),
                word_count=word_count,
                warnings=paragraph_warnings,
                errors=paragraph_errors,
            )
        )
        source_ledger.append(
            ParagraphSourceLedger(
                paragraph_id=paragraph_id,
                section_id=section.section_id,
                story_id=story.story_id,
                claim_ids=sorted(set(claim_ids)),
                fact_ids=fact_ids,
                artifact_ids=artifact_ids,
                value_token_ids=sorted(set(value_token_ids)),
                figure_ids=sorted(set(figure_ids)),
                literature_evidence_ids=sorted(set(literature_evidence_ids)),
                inference_kind=model_paragraph.inference_kind,
                inference_note=model_paragraph.inference_note,
                scope=plan.charter.scope,
                scopes=scopes,
                limits=limits,
                roles=roles,
            )
        )
        errors.extend(paragraph_errors)
        warnings.extend(paragraph_warnings)

    deferred_aliases: List[str] = []
    deferred_ids: List[str] = []
    for alias in model_response.deferred_claim_aliases:
        claim_id = claim_alias_to_id.get(alias)
        if claim_id is None:
            errors.append(f"unknown deferred claim alias {alias!r}")
            continue
        if claim_id not in allowed_claims:
            errors.append(
                f"deferred claim alias {alias!r} is not assigned to this " "section"
            )
            continue
        deferred_aliases.append(alias)
        deferred_ids.append(claim_id)
        cited_claims.add(claim_id)
    unused = sorted(assigned_claims - cited_claims)
    for claim_id in unused:
        warnings.append(f"assigned claim {claim_id!r} is neither cited nor deferred")
    target_range = _target_word_range(section.purpose)
    safe_paragraphs = [item for item in paragraphs if not item.errors]
    tokenized_prose = "\n\n".join(
        item.text_with_value_tokens for item in safe_paragraphs
    )
    rendered_prose = "\n\n".join(item.rendered_text for item in safe_paragraphs)
    word_count = sum(item.word_count for item in paragraphs)
    if word_count < target_range[0] * 0.5:
        warnings.append(
            f"section word count {word_count} is unusually short for target "
            f"range {target_range}"
        )
    status = "needs_revision" if errors else "publishable"
    return ArticleSectionDraft(
        section_id=section.section_id,
        title=section.heading,
        story_id=story.story_id,
        architecture_id=architecture_id,
        status=status,
        tokenized_prose=tokenized_prose,
        rendered_prose=rendered_prose,
        paragraphs=paragraphs,
        source_ledger=source_ledger,
        figure_ids=list(section.figure_ids),
        deferred_claim_aliases=deferred_aliases,
        deferred_claim_ids=deferred_ids,
        author_notes=list(model_response.author_notes),
        warnings=warnings,
        errors=errors,
        word_count=word_count,
        target_word_range=target_range,
        model_status=model_status,
        semantic_model=semantic_model,
        usage=dict(usage),
        repair_rounds=repair_rounds,
        attempts=attempts,
    )


def _blocked_section(
    *,
    architecture_id: str,
    story: StoryCandidate,
    section: SectionContract,
    errors: Sequence[str],
    warnings: Sequence[str],
    attempts: int,
) -> ArticleSectionDraft:
    return ArticleSectionDraft(
        section_id=section.section_id,
        title=section.heading,
        story_id=story.story_id,
        architecture_id=architecture_id,
        status="blocked",
        tokenized_prose="",
        rendered_prose="",
        paragraphs=[],
        source_ledger=[],
        figure_ids=list(section.figure_ids),
        warnings=[str(item) for item in warnings],
        errors=[str(item) for item in errors],
        word_count=0,
        target_word_range=_target_word_range(section.purpose),
        model_status="unavailable",
        semantic_model="none",
        usage={},
        repair_rounds=0,
        attempts=attempts,
    )


def _repair_made_progress(
    original: ArticleSectionDraft, repaired: ArticleSectionDraft
) -> bool:
    return repaired.status == "publishable" or len(repaired.errors) < len(
        original.errors
    )


def _paragraph_id_for_error(
    error: str,
    paragraph_ids: set[str],
) -> Optional[str]:
    match = re.match(r"^([A-Za-z0-9_-]+)\s", str(error))
    if match is None:
        return None
    paragraph_id = match.group(1)
    return paragraph_id if paragraph_id in paragraph_ids else None


def _targeted_repair_plan(
    draft: ArticleSectionDraft,
) -> Optional[List[str]]:
    if not draft.paragraphs or not draft.errors:
        return None
    paragraph_ids = {item.paragraph_id for item in draft.paragraphs}
    targeted: List[str] = []
    for error in draft.errors:
        paragraph_id = _paragraph_id_for_error(error, paragraph_ids)
        if paragraph_id is None:
            return None
        targeted.append(paragraph_id)
    return sorted(set(targeted))


def _paragraph_ids_for_response(
    section: SectionContract,
    response: Mapping[str, Any],
) -> List[str]:
    paragraphs = response.get("paragraphs")
    if not isinstance(paragraphs, list):
        return []
    return [
        f"{section.section_id}-p{index:02d}" for index in range(1, len(paragraphs) + 1)
    ]


def _build_targeted_repair_payload(
    request: Mapping[str, Any],
    current_response: Mapping[str, Any],
    current_draft: ArticleSectionDraft,
    section: SectionContract,
) -> Dict[str, Any]:
    paragraphs = current_response.get("paragraphs")
    if not isinstance(paragraphs, list):
        raise ValueError("targeted repair requires a paragraph list in failed_response")
    paragraph_ids = _paragraph_ids_for_response(section, current_response)
    targeted_ids = _targeted_repair_plan(current_draft)
    if targeted_ids is None:
        raise ValueError("no paragraph-local errors are available for targeting")
    targeted_set = set(targeted_ids)
    rows = [
        {**dict(row), "paragraph_id": paragraph_id}
        for paragraph_id, row in zip(paragraph_ids, paragraphs)
        if paragraph_id in targeted_set
    ]
    return {
        **request,
        "repair_mode": "targeted_paragraphs",
        "task": (
            "Repair ONLY the targeted paragraph rows below. "
            "Organization-only; all IDs/aliases are local."
        ),
        "response_contract": {
            "targeted_paragraphs": [
                {
                    "paragraph_id": ("string; the local paragraph id, required first"),
                    "text_with_value_tokens": ("string; [VALUE:...] tokens allowed"),
                    "claim_aliases": ["C01_..."],
                    "figure_aliases": ["FIG01_..."],
                    "literature_evidence_aliases": ["E01_..."],
                    "paragraph_role": (
                        "background|method|result|limitation|transition|"
                        "discussion|conclusion"
                    ),
                    "inference_kind": ("none_required|bounded_inference|unsupported"),
                    "inference_note": "string",
                }
            ]
        },
        "errors": list(current_draft.errors),
        "targeted_paragraph_ids": targeted_ids,
        "targeted_paragraphs": rows,
    }


def _merge_targeted_response(
    current_response: Mapping[str, Any],
    targeted_rows: Sequence[_ModelTargetedParagraph],
    section: SectionContract,
    required_ids: Sequence[str],
) -> Dict[str, Any]:
    paragraphs = current_response.get("paragraphs")
    if not isinstance(paragraphs, list):
        raise ValueError(
            "targeted repair response cannot merge into a malformed response"
        )
    paragraph_ids = _paragraph_ids_for_response(section, current_response)
    id_to_index = {
        paragraph_id: index for index, paragraph_id in enumerate(paragraph_ids)
    }
    provided_ids = {row.paragraph_id for row in targeted_rows}
    required_set = set(required_ids)
    if provided_ids != required_set:
        missing = sorted(required_set - provided_ids)
        extra = sorted(provided_ids - required_set)
        raise ValueError(
            "targeted paragraph ids do not match required ids exactly "
            f"(missing={missing}, extra={extra})"
        )
    merged = list(paragraphs)
    seen: set[str] = set()
    for row in targeted_rows:
        paragraph_id = row.paragraph_id
        if paragraph_id in seen:
            raise ValueError(f"duplicate targeted paragraph id {paragraph_id!r}")
        seen.add(paragraph_id)
        if paragraph_id not in id_to_index:
            raise ValueError(f"unknown targeted paragraph id {paragraph_id!r}")
        merged[id_to_index[paragraph_id]] = row.model_dump(exclude={"paragraph_id"})
    return {**current_response, "paragraphs": merged}


def _process_section(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture_id: str,
    story: StoryCandidate,
    section: SectionContract,
    all_sections: Sequence[SectionContract],
    aliases: Mapping[str, Any],
    value_records_by_key: Mapping[Tuple[str, str], TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    writer_provider: Optional[SectionWriterProvider],
    repair_provider: Optional[FormatRepairProvider],
    literature_context: Optional[Mapping[str, Any]] = None,
) -> ArticleSectionDraft:
    request = _build_section_request(
        plan=plan,
        ledger=ledger,
        story=story,
        section=section,
        all_sections=all_sections,
        aliases=aliases,
        value_records_by_key=value_records_by_key,
        fact_by_claim=fact_by_claim,
        literature_context=literature_context,
    )
    if writer_provider is None:
        return _blocked_section(
            architecture_id=architecture_id,
            story=story,
            section=section,
            errors=["no section writer provider supplied"],
            warnings=[],
            attempts=0,
        )
    try:
        envelope = writer_provider(request)
        if not isinstance(envelope, WriterProviderResult):
            raise TypeError("section writer provider must return WriterProviderResult")
        raw_response = dict(envelope.response or {})
        draft = _assemble_section(
            plan=plan,
            ledger=ledger,
            architecture_id=architecture_id,
            story=story,
            section=section,
            aliases=aliases,
            value_records_by_key=value_records_by_key,
            fact_by_claim=fact_by_claim,
            raw_response=raw_response,
            semantic_model=envelope.provider_model,
            usage=envelope.usage,
            model_status="available",
            repair_rounds=0,
            attempts=1,
        )
    except Exception as exc:
        return _blocked_section(
            architecture_id=architecture_id,
            story=story,
            section=section,
            errors=[f"section writer provider unavailable: {exc}"],
            warnings=[],
            attempts=1,
        )
    if draft.status == "publishable" or repair_provider is None:
        return draft
    current_draft = draft
    current_response = raw_response
    usage_rows: List[Dict[str, Any]] = [dict(draft.usage or {})]
    attempts = 1
    for repair_round in (1, 2):
        targeted_ids = _targeted_repair_plan(current_draft)
        if targeted_ids is not None:
            repair_payload = _build_targeted_repair_payload(
                request,
                current_response,
                current_draft,
                section,
            )
        else:
            repair_payload = {
                **request,
                "repair_mode": "full_response",
                "failed_response": current_response,
                "errors": list(current_draft.errors),
            }
        attempts += 1
        try:
            envelope = repair_provider(repair_payload)
            if not isinstance(envelope, WriterProviderResult):
                raise TypeError(
                    "format repair provider must return WriterProviderResult"
                )
            raw_repaired = dict(envelope.response or {})
        except Exception as exc:
            return current_draft.model_copy(
                update={
                    "warnings": current_draft.warnings
                    + [f"format repair unavailable: {exc}"],
                    "usage": _aggregate_usage(usage_rows),
                    "attempts": attempts,
                }
            )
        usage_rows.append(dict(envelope.usage or {}))
        try:
            if targeted_ids is not None and "targeted_paragraphs" in raw_repaired:
                raw_rows = raw_repaired.get("targeted_paragraphs")
                if isinstance(raw_rows, list) and raw_rows:
                    present_ids = [
                        row.get("paragraph_id")
                        for row in raw_rows
                        if isinstance(row, Mapping)
                        and row.get("paragraph_id") not in (None, "")
                    ]
                    if not present_ids and len(raw_rows) == len(targeted_ids):
                        injected_rows = []
                        for row, paragraph_id in zip(raw_rows, targeted_ids):
                            if not isinstance(row, Mapping):
                                raise ValueError(
                                    "targeted paragraph rows must be objects"
                                )
                            injected_rows.append(
                                {
                                    **dict(row),
                                    "paragraph_id": paragraph_id,
                                }
                            )
                        raw_repaired = {
                            **raw_repaired,
                            "targeted_paragraphs": injected_rows,
                        }
                targeted_model = _ModelTargetedResponse.model_validate(raw_repaired)
                merged_response = _merge_targeted_response(
                    current_response,
                    targeted_model.targeted_paragraphs,
                    section,
                    targeted_ids,
                )
                repair_response = merged_response
            elif "paragraphs" in raw_repaired:
                repair_response = raw_repaired
            else:
                raise ValueError(
                    "format repair provider returned an unsupported response "
                    "contract"
                )
        except (ValidationError, ValueError) as exc:
            return current_draft.model_copy(
                update={
                    "warnings": current_draft.warnings
                    + [f"targeted repair response invalid: {exc}"],
                    "usage": _aggregate_usage(usage_rows),
                    "attempts": attempts,
                }
            )
        repaired = _assemble_section(
            plan=plan,
            ledger=ledger,
            architecture_id=architecture_id,
            story=story,
            section=section,
            aliases=aliases,
            value_records_by_key=value_records_by_key,
            fact_by_claim=fact_by_claim,
            raw_response=repair_response,
            semantic_model=envelope.provider_model,
            usage=_aggregate_usage(usage_rows),
            model_status="available",
            repair_rounds=repair_round,
            attempts=attempts,
            preserved_literature_evidence_ids={
                paragraph.paragraph_id: paragraph.literature_evidence_ids
                for paragraph in current_draft.paragraphs
            },
        )
        if _repair_made_progress(current_draft, repaired):
            current_draft = repaired
            current_response = repair_response
            if repaired.status == "publishable" or repair_round == 2:
                return current_draft
            continue
        return current_draft.model_copy(
            update={
                "warnings": current_draft.warnings
                + ["repair round made no progress; retaining previous " "findings"],
                "usage": _aggregate_usage(usage_rows),
                "attempts": attempts,
            }
        )
    return current_draft


def build_article_draft_bundle(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    *,
    section_writer: Optional[SectionWriterProvider] = None,
    format_repair: Optional[FormatRepairProvider] = None,
    memory_store: ArticleMemoryStore | None = None,
    graph: ExperimentGraph | None = None,
    run_id: Optional[str] = None,
    journal_path: str | Path | None = None,
    literature_context: Optional[Mapping[str, Any]] = None,
    literature_evidence_alias_map: Optional[Mapping[str, str]] = None,
) -> ArticleDraftBundle:
    errors: List[str] = []
    warnings: List[str] = []
    if (memory_store is not None or graph is not None) and not run_id:
        errors.append("run_id is required when memory_store or graph is provided")
    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
    except ValidationError as exc:
        errors.append(f"plan is invalid: {exc}")
        return _hard_blocker(errors, warnings)
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
    if errors:
        return _hard_blocker(errors, warnings)

    story, fact_by_claim = _validate_inputs(
        plan_model,
        ledger_model,
        architecture_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if errors:
        return _hard_blocker(errors, warnings)
    assert story is not None
    aliases = _build_alias_maps(story, ledger_model, records, fact_by_claim)
    aliases["literature_alias_to_id"] = {
        str(alias): str(evidence_id)
        for alias, evidence_id in (literature_evidence_alias_map or {}).items()
        if str(alias).strip() and str(evidence_id).strip()
    }
    value_records_by_key = {
        (record.artifact_id, record.field): record for record in records
    }

    sections: List[ArticleSectionDraft] = []
    all_sections = list(story.section_contracts)
    for section in all_sections:
        sections.append(
            _process_section(
                plan=plan_model,
                ledger=ledger_model,
                architecture_id=architecture_model.architecture_id,
                story=story,
                section=section,
                all_sections=all_sections,
                aliases=aliases,
                value_records_by_key=value_records_by_key,
                fact_by_claim=fact_by_claim,
                writer_provider=section_writer,
                repair_provider=format_repair,
                literature_context=literature_context,
            )
        )

    bundle_warnings = list(warnings)
    for section in sections:
        bundle_warnings.extend(section.warnings)
    source_ledger = [entry for section in sections for entry in section.source_ledger]
    deferred_claims = sorted(
        {claim_id for section in sections for claim_id in section.deferred_claim_ids}
    )
    publishable_section_ids = [
        section.section_id for section in sections if section.status == "publishable"
    ]
    publishable = (
        not errors
        and len(sections) > 0
        and len(publishable_section_ids) == len(sections)
    )
    usage = _aggregate_usage([section.usage for section in sections])
    attempts = sum(section.attempts for section in sections)
    provider_models = [
        section.semantic_model for section in sections if section.semantic_model
    ]
    if not provider_models:
        semantic_model = "none"
    elif len(set(provider_models)) == 1:
        semantic_model = provider_models[0]
    else:
        semantic_model = "mixed"
    if not sections:
        model_status: Literal["available", "partial", "unavailable"] = "unavailable"
    elif all(section.model_status == "available" for section in sections):
        model_status = "available"
    elif any(section.model_status == "available" for section in sections):
        model_status = "partial"
    else:
        model_status = "unavailable"
    bundle_id = compute_bundle_id(
        plan_model.plan_id,
        ledger_model.ledger_id,
        architecture_model.architecture_id,
        story.story_id,
        sections,
    )
    result = ArticleDraftBundle(
        bundle_id=bundle_id,
        plan_id=plan_model.plan_id,
        ledger_id=ledger_model.ledger_id,
        architecture_id=architecture_model.architecture_id,
        story_id=story.story_id,
        sections=sections,
        source_ledger=source_ledger,
        deferred_claims=deferred_claims,
        warnings=bundle_warnings,
        errors=list(errors),
        publishable=publishable,
        publishable_section_ids=publishable_section_ids,
        usage=usage,
        semantic_model=semantic_model,
        model_status=model_status,
        attempts=attempts,
        claim_alias_map=dict(aliases["claim_alias_to_id"]),
        fact_alias_map=dict(aliases["fact_alias_map"]),
        value_alias_map={
            alias: dict(info) for alias, info in aliases["value_alias_map"].items()
        },
        figure_alias_map=dict(aliases["figure_alias_to_id"]),
    )
    if memory_store is not None or graph is not None or journal_path is not None:
        _persist(
            bundle_id=bundle_id,
            result=result,
            memory_store=memory_store,
            graph=graph,
            run_id=str(run_id or ""),
            journal_path=journal_path,
        )
    return result


def _hard_blocker(errors: Sequence[str], warnings: Sequence[str]) -> ArticleDraftBundle:
    return ArticleDraftBundle(
        bundle_id=f"bundle-{_digest('invalid')}",
        plan_id="",
        ledger_id="",
        architecture_id="",
        story_id="",
        sections=[],
        source_ledger=[],
        deferred_claims=[],
        warnings=[str(item) for item in warnings],
        errors=[str(item) for item in errors],
        publishable=False,
        publishable_section_ids=[],
        usage={},
        semantic_model="none",
        model_status="unavailable",
        claim_alias_map={},
        fact_alias_map={},
        value_alias_map={},
        figure_alias_map={},
    )


def validate_writing_inputs(
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord],
    errors: List[str],
    warnings: List[str],
) -> Tuple[Optional[StoryCandidate], Dict[str, Any]]:
    """Public wrapper around the Stage 10 input revalidation authority."""

    return _validate_inputs(
        plan,
        ledger,
        architecture,
        selected_story_id,
        value_records,
        errors,
        warnings,
    )


def build_writing_alias_maps(
    story: StoryCandidate,
    ledger: ClaimLedgerResult,
    value_records: Sequence[TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    """Public wrapper around the deterministic alias-map builder."""

    return _build_alias_maps(story, ledger, value_records, fact_by_claim)


def revalidate_section_draft(
    *,
    plan: ArticleDirectorPlan,
    ledger: ClaimLedgerResult,
    architecture_id: str,
    story: StoryCandidate,
    section: SectionContract,
    aliases: Mapping[str, Any],
    value_records_by_key: Mapping[Tuple[str, str], TrustedValueRecord],
    fact_by_claim: Mapping[str, Any],
    raw_response: Mapping[str, Any],
    semantic_model: str,
    usage: Mapping[str, Any],
    model_status: Literal["available", "unavailable"],
    repair_rounds: int,
    attempts: int,
    preserved_literature_evidence_ids: Optional[Mapping[str, Sequence[str]]] = None,
) -> ArticleSectionDraft:
    """Public wrapper around the Stage 10 section assembler/validator."""

    return _assemble_section(
        plan=plan,
        ledger=ledger,
        architecture_id=architecture_id,
        story=story,
        section=section,
        aliases=aliases,
        value_records_by_key=value_records_by_key,
        fact_by_claim=fact_by_claim,
        raw_response=raw_response,
        semantic_model=semantic_model,
        usage=usage,
        model_status=model_status,
        repair_rounds=repair_rounds,
        attempts=attempts,
        preserved_literature_evidence_ids=preserved_literature_evidence_ids,
    )


class QwenSectionWriter:
    """Concrete qwen3.7-flash section writer adapter (one request per section)."""

    def __init__(
        self,
        *,
        prompt_path: str | Path = WRITER_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_WRITER_MAX_TOKENS,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.max_tokens = int(max_tokens)
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        self.client = client or QwenFlashOnlyClient(agent_name="ArticleSectionWriter")

    def __call__(self, request: Mapping[str, Any]) -> WriterProviderResult:
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
        return WriterProviderResult(
            response=parsed,
            usage=usage,
            provider_model=WRITER_MODEL_NAME,
            mock_llm=bool(usage.get("mock_llm")),
        )


class QwenFormatRepair:
    """Concrete qwen3.7-flash compact format/source repair adapter."""

    def __init__(
        self,
        *,
        prompt_path: str | Path = REPAIR_PROMPT_PATH,
        client: QwenFlashOnlyClient | None = None,
        max_tokens: int = DEFAULT_REPAIR_MAX_TOKENS,
    ) -> None:
        self.prompt_path = Path(prompt_path)
        self.max_tokens = int(max_tokens)
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        self.client = client or QwenFlashOnlyClient(
            agent_name="ArticleSectionFormatRepair"
        )

    def __call__(self, request: Mapping[str, Any]) -> WriterProviderResult:
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
        return WriterProviderResult(
            response=parsed,
            usage=usage,
            provider_model=WRITER_MODEL_NAME,
            mock_llm=bool(usage.get("mock_llm")),
        )


def _read_journal(path: str | Path) -> Dict[str, Any]:
    journal_path = Path(path)
    if not journal_path.exists():
        return {}
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArticleWritingError(f"writing journal is unreadable: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ArticleWritingError("writing journal must be a JSON object")
    return {
        str(key): dict(value)
        for key, value in payload.items()
        if isinstance(value, Mapping)
    }


def _write_journal(
    path: str | Path,
    journal: Mapping[str, Any],
    bundle_id: str,
    state: Mapping[str, Any],
) -> None:
    payload = dict(journal)
    payload[str(bundle_id)] = dict(state)
    atomic_write_json(Path(path), payload)


def _expected_section_events(
    result: ArticleDraftBundle,
) -> List[Tuple[str, Mapping[str, Any]]]:
    events: List[Tuple[str, Mapping[str, Any]]] = []
    for section in result.sections:
        events.append(
            (
                "article.section",
                validate_article_event(
                    "article.section",
                    {
                        "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                        "section_id": section.section_id,
                        "status": section.status,
                        "story_id": result.story_id,
                    },
                ),
            )
        )
    return events


def _persist(
    *,
    bundle_id: str,
    result: ArticleDraftBundle,
    memory_store: Optional[ArticleMemoryStore],
    graph: Optional[ExperimentGraph],
    run_id: str,
    journal_path: Optional[str | Path],
) -> None:
    if journal_path is None:
        if graph is not None:
            _persist_graph(graph, bundle_id, result)
        if memory_store is not None:
            _persist_memory(memory_store, bundle_id, result, run_id)
        return
    journal = _read_journal(journal_path)
    state = journal.get(bundle_id)
    if state is not None and state.get("status") == "completed":
        return
    if state is None:
        state = {
            "status": "in_progress",
            "graph_written": graph is None,
            "memory_written": memory_store is None,
        }
    try:
        if graph is not None and not state.get("graph_written"):
            _persist_graph(graph, bundle_id, result)
            state["graph_written"] = True
            _write_journal(journal_path, journal, bundle_id, state)
        if memory_store is not None and not state.get("memory_written"):
            _persist_memory(memory_store, bundle_id, result, run_id)
            state["memory_written"] = True
            _write_journal(journal_path, journal, bundle_id, state)
        state["status"] = "completed"
        _write_journal(journal_path, journal, bundle_id, state)
    except Exception as exc:
        _write_journal(journal_path, journal, bundle_id, state)
        raise ArticleWritingError(f"section writing persistence failed: {exc}") from exc


def _persist_graph(
    graph: ExperimentGraph,
    bundle_id: str,
    result: ArticleDraftBundle,
) -> None:
    node_id = f"bundle-{bundle_id}"
    summary = f"bundle-{bundle_id}"
    payload = ArticleNodePayload(
        stage=ArticleStage.section_writing,
        hypothesis_ids=[],
        card_refs={
            "story_ids": [result.story_id],
            "section_ids": [section.section_id for section in result.sections],
        },
        summary=summary,
    )
    expected_events = _expected_section_events(result)
    created = False
    try:
        graph.create_article_node(payload, node_id=node_id)
        created = True
    except sqlite3.IntegrityError:
        existing = graph.article_node(node_id)
        if existing.get("payload", {}).get("summary") != summary:
            raise ArticleWritingIntegrityError(
                f"writing bundle node {node_id!r} already exists with "
                "different content"
            )
    if created:
        for event_type, event_payload in expected_events:
            graph.record_article_event(node_id, event_type, event_payload)
        return
    existing = graph.article_node(node_id)
    seen = {
        (item["event_type"], _canonical_json(item["payload"]))
        for item in existing["history"]
    }
    by_identity: Dict[str, Tuple[str, str]] = {}
    for item in existing["history"]:
        if item["event_type"] == "article.section":
            identity = f"section:{item['payload'].get('section_id')}"
            by_identity[identity] = (
                item["event_type"],
                _canonical_json(item["payload"]),
            )
    for event_type, event_payload in expected_events:
        canonical = _canonical_json(event_payload)
        identity = f"section:{event_payload.get('section_id')}"
        if identity in by_identity and by_identity[identity] != (event_type, canonical):
            raise ArticleWritingIntegrityError(
                f"writing bundle node {node_id!r} has conflicting section "
                f"event for {identity}"
            )
        if (event_type, canonical) in seen:
            continue
        graph.record_article_event(node_id, event_type, event_payload)
        seen.add((event_type, canonical))
        by_identity[identity] = (event_type, canonical)


def _persist_memory(
    memory_store: ArticleMemoryStore,
    bundle_id: str,
    result: ArticleDraftBundle,
    run_id: str,
) -> None:
    bundle_artifacts = sorted(
        {
            artifact
            for section in result.sections
            for entry in section.source_ledger
            for artifact in entry.artifact_ids
        }
    )
    records: List[RunMemoryRecord] = [
        RunMemoryRecord(
            memory_id=f"bundle-{bundle_id}",
            run_id=run_id,
            event_type="article_draft_bundle",
            graph_node_id=f"bundle-{bundle_id}",
            artifact_ids=bundle_artifacts,
            operational_note=_canonical_json(result.model_dump(mode="json")),
        ),
        RunMemoryRecord(
            memory_id=f"source-ledger-{bundle_id}",
            run_id=run_id,
            event_type="article_source_ledger",
            graph_node_id=f"bundle-{bundle_id}",
            artifact_ids=bundle_artifacts,
            operational_note=_canonical_json(
                [item.model_dump(mode="json") for item in result.source_ledger]
            ),
        ),
    ]
    for section in result.sections:
        section_artifacts = sorted(
            {
                artifact
                for entry in section.source_ledger
                for artifact in entry.artifact_ids
            }
        )
        records.append(
            RunMemoryRecord(
                memory_id=f"section-{bundle_id}-{section.section_id}",
                run_id=run_id,
                event_type="article_section",
                graph_node_id=f"bundle-{bundle_id}",
                artifact_ids=section_artifacts,
                operational_note=_canonical_json(section.model_dump(mode="json")),
            )
        )
    for record in records:
        try:
            memory_store.add_run_memory(record)
        except DuplicateRecordError:
            existing = memory_store.get_run_memory(record.memory_id)
            if existing.model_dump(mode="json") != record.model_dump(mode="json"):
                raise ArticleWritingIntegrityError(
                    f"memory record {record.memory_id!r} already exists with "
                    "different content"
                ) from None


__all__ = [
    "ArticleDraftBundle",
    "ArticleSectionDraft",
    "ArticleWritingError",
    "ArticleWritingIntegrityError",
    "DEFAULT_REPAIR_MAX_TOKENS",
    "DEFAULT_WRITER_MAX_TOKENS",
    "FormatRepairProvider",
    "InferenceKind",
    "ParagraphDraft",
    "ParagraphRole",
    "ParagraphSourceLedger",
    "QwenFormatRepair",
    "QwenSectionWriter",
    "SectionWriterProvider",
    "TrustedValueRecord",
    "WRITER_MODEL_NAME",
    "WriterProviderResult",
    "build_article_draft_bundle",
    "build_writing_alias_maps",
    "compute_bundle_id",
    "revalidate_section_draft",
    "validate_writing_inputs",
]
