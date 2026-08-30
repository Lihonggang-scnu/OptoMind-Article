"""Cost-aware literature guidance for future TMM research-design loops.

This module is deliberately a research adapter, not a physics validator.  It
turns bounded, generic optical design questions into provenance-bearing
literature evidence and method leads.  Literature output never mutates a TMM
task and never certifies a physical result.

The online boundary delegates to the existing Semantic Scholar and OpenAlex
adapters.  This module owns neither HTTP requests nor provider credentials.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from config.qwen_config import get_cost_tracker

from .problem_analyzer import ArticlePlusQwenClient
from .text_safety import repair_scientific_payload


class MethodPurpose(str, Enum):
    """Scientific purpose of one bounded method-research query."""

    design_family = "design_family"
    material_choice = "material_choice"
    objective_formulation = "objective_formulation"
    optimization_strategy = "optimization_strategy"
    fabrication_constraint = "fabrication_constraint"
    failure_mode = "failure_mode"


class MethodContentDepth(str, Enum):
    """How much source content is represented by an evidence record."""

    metadata = "metadata"
    abstract = "abstract"
    s2_snippet = "s2_snippet"
    fulltext = "fulltext"


class MethodAllowedUse(str, Enum):
    """The strongest use permission granted to one evidence record."""

    discovery = "discovery"
    background = "background"
    method_guidance = "method_guidance"
    direct_fact = "direct_fact"


class MethodResearchStatus(str, Enum):
    completed = "completed"
    partial = "partial"
    unavailable = "unavailable"


# Friendly aliases keep the public API discoverable without multiplying the
# underlying enum definitions.
MethodResearchPurpose = MethodPurpose
MethodEvidenceDepth = MethodContentDepth
MethodEvidenceUse = MethodAllowedUse


_PERMITTED_USES: dict[MethodContentDepth, frozenset[MethodAllowedUse]] = {
    MethodContentDepth.metadata: frozenset({MethodAllowedUse.discovery}),
    MethodContentDepth.abstract: frozenset(
        {MethodAllowedUse.discovery, MethodAllowedUse.background}
    ),
    MethodContentDepth.s2_snippet: frozenset(
        {
            MethodAllowedUse.discovery,
            MethodAllowedUse.background,
            MethodAllowedUse.method_guidance,
        }
    ),
    MethodContentDepth.fulltext: frozenset(
        {
            MethodAllowedUse.discovery,
            MethodAllowedUse.background,
            MethodAllowedUse.method_guidance,
            MethodAllowedUse.direct_fact,
        }
    ),
}


class MethodResearchQuery(BaseModel):
    """One scientific query sent to local or optional online literature."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query_id: str
    query_text: str
    purpose: MethodPurpose
    priority: int = Field(default=3, ge=1, le=5)

    @field_validator("query_id", "query_text")
    @classmethod
    def _nonempty_text(cls, value: str) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("query identifiers and text must be non-empty")
        return value


class MethodEvidence(BaseModel):
    """Bounded literature evidence with explicit use permission.

    ``fulltext`` means that the represented text came from a full-text route;
    it does not mean that the entire paper was read.  A local chunk or a
    selected full-text passage remains bounded evidence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    paper_id: str
    title: str
    doi: str = ""
    year: int | None = None
    source_route: str
    content_depth: MethodContentDepth
    text: str
    query_ids: list[str] = Field(default_factory=list)
    allowed_use: MethodAllowedUse
    local_path: str | None = None
    # T-04: every literature citation carries its provenance type so the
    # output is directly consumable by the future ProvenanceLedger (T-10).
    source_type: Literal["literature_fact"] = "literature_fact"

    @field_validator("evidence_id", "paper_id", "source_route")
    @classmethod
    def _required_identifiers(cls, value: str) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("evidence identifiers and source_route must be non-empty")
        return value

    @field_validator("title", "text")
    @classmethod
    def _text_fields(cls, value: str) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("title and evidence text must be non-empty")
        return value

    @field_validator("query_ids")
    @classmethod
    def _unique_query_ids(cls, values: list[str]) -> list[str]:
        ordered: list[str] = []
        for value in values:
            item = str(value).strip()
            if item and item not in ordered:
                ordered.append(item)
        return ordered

    @model_validator(mode="after")
    def _permission_boundary(self) -> "MethodEvidence":
        permitted = _PERMITTED_USES[self.content_depth]
        if self.allowed_use not in permitted:
            allowed = ", ".join(sorted(item.value for item in permitted))
            raise ValueError(
                f"{self.content_depth.value} evidence cannot be used as "
                f"{self.allowed_use.value}; permitted uses: {allowed}"
            )
        return self


class MethodFinding(BaseModel):
    """A reusable method lead, never a physics certificate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Both names are retained because a design family and a concrete method
    # are useful to different callers.  The compatibility aliases below let a
    # synthesis callback provide either one while keeping the report explicit.
    design_family: str = ""
    method_name: str = ""
    reusable_principle: str
    evidence_ids: list[str] = Field(default_factory=list)
    applicability: str
    limitations: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _normalize_method_name(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        values = dict(repair_scientific_payload(data))
        method = values.pop("method", None)
        if method and not values.get("method_name") and not values.get("design_family"):
            values["method_name"] = method
        if values.get("design_family") and not values.get("method_name"):
            values["method_name"] = values["design_family"]
        elif values.get("method_name") and not values.get("design_family"):
            values["design_family"] = values["method_name"]
        return values

    @field_validator("design_family", "method_name", "reusable_principle", "applicability", "limitations")
    @classmethod
    def _clean_finding_text(cls, value: str) -> str:
        return str(value).strip()

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(cls, values: list[str]) -> list[str]:
        ordered: list[str] = []
        for value in values:
            item = str(value).strip()
            if item and item not in ordered:
                ordered.append(item)
        return ordered

    @model_validator(mode="after")
    def _require_method_name(self) -> "MethodFinding":
        if not (self.design_family or self.method_name):
            raise ValueError("a method finding must name a design family or method")
        if not self.reusable_principle:
            raise ValueError("method findings require a reusable principle")
        if not self.applicability:
            raise ValueError("method findings require applicability")
        if not self.limitations:
            raise ValueError("method findings require limitations")
        return self

    @property
    def method(self) -> str:
        """Compatibility view of the named method."""

        return self.method_name or self.design_family


class MethodResearchTelemetry(BaseModel):
    """Small, serializable cost and provenance ledger for one report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    local_queries: int = 0
    s2_calls: int = 0
    s2_paper_search_calls: int = 0
    s2_batch_calls: int = 0
    s2_snippet_calls: int = 0
    s2_snippets_retrieved: int = 0
    s2_snippets_accepted: int = 0
    s2_snippets_rejected_scope: int = 0
    online_queries_skipped_budget: int = 0
    online_budget_exhausted: bool = False
    openalex_calls: int = 0
    records_returned: int = 0
    records_accepted: int = 0
    records_rejected_scope: int = 0
    unique_evidence: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    source_routes: dict[str, int] = Field(default_factory=dict)
    cache_source_routes: dict[str, int] = Field(default_factory=dict)
    elapsed_seconds: float = Field(default=0.0, ge=0.0)
    errors: list[str] = Field(default_factory=list)
    usage: list[dict[str, Any]] = Field(default_factory=list)

    def __getitem__(self, key: str) -> Any:
        """Allow dictionary-style telemetry access in orchestration code."""

        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    @property
    def cache_routes(self) -> dict[str, int]:
        """Short compatibility view for cache/source route counters."""

        return self.cache_source_routes

    @property
    def elapsed_time_seconds(self) -> float:
        return self.elapsed_seconds


class MethodResearchReport(BaseModel):
    """Method-research output safe to pass to a future design loop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    problem_id: str
    queries: list[MethodResearchQuery] = Field(default_factory=list)
    evidence: list[MethodEvidence] = Field(default_factory=list)
    method_findings: list[MethodFinding] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    telemetry: MethodResearchTelemetry = Field(default_factory=MethodResearchTelemetry)
    status: MethodResearchStatus = MethodResearchStatus.unavailable
    reasons: list[str] = Field(default_factory=list)

    @field_validator("problem_id")
    @classmethod
    def _problem_id(cls, value: str) -> str:
        value = str(value).strip()
        if not value:
            raise ValueError("problem_id must be non-empty")
        return value

    @model_validator(mode="after")
    def _evidence_references_are_grounded(self) -> "MethodResearchReport":
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence_id values must be unique in a report")
        known = set(evidence_ids)
        for finding in self.method_findings:
            unknown = [item for item in finding.evidence_ids if item not in known]
            if unknown:
                raise ValueError(
                    "method finding references evidence that is not in the report: "
                    + ", ".join(unknown)
                )
        return self

    @property
    def status_reasons(self) -> list[str]:
        """Compatibility name for callers that prefer an explicit label."""

        return self.reasons

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class MethodOnlineSearchResult:
    """Optional normalized return value for injectable online clients."""

    records: Sequence[Any]
    cache_hit: bool | None = None
    source_route: str = ""
    status: str = ""
    error: str = ""
    provider_calls: Mapping[str, int] | None = None
    quality_counts: Mapping[str, int] | None = None


@runtime_checkable
class MethodResearchOnlineClient(Protocol):
    """Protocol for injectable online search without transport ownership."""

    def search_s2(self, query: str, *, limit: int) -> Any:
        ...

    def search_openalex(self, query: str, *, limit: int) -> Any:
        ...


OnlineLiteratureClient = MethodResearchOnlineClient


def _is_s2_key_file(name: str) -> bool:
    lower = name.lower()
    return "semantic" in lower and (
        "scholar" in lower or "s2" in lower or "apikey" in lower
    )


def _s2_key_candidates(directory: Path) -> List[Path]:
    if not directory.is_dir():
        return []
    try:
        paths = list(directory.iterdir())
    except OSError:
        return []
    return sorted(
        path
        for path in paths
        if path.is_file() and _is_s2_key_file(path.name)
    )


def _resolve_shared_s2_key_pool(
    candidate_dirs: Sequence[Path] | None = None,
) -> List[str]:
    """Resolve shared Semantic Scholar key files without logging secrets."""

    if candidate_dirs is None:
        article_root = Path(__file__).resolve().parents[3]
        candidate_dirs = [
            article_root.parent / "api_keys",
            article_root / "api_keys",
        ]
    keys: List[str] = []
    seen: set[str] = set()
    for directory in candidate_dirs:
        for path in _s2_key_candidates(directory):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for raw in text.splitlines():
                key = raw.strip()
                if not key or key.startswith("#"):
                    continue
                if key not in seen:
                    seen.add(key)
                    keys.append(key)
    return keys


class DefaultMethodResearchOnlineClient:
    """Delegate online searches to the repository's existing backends.

    When no ``s2_gateway`` is injected, the constructor builds the repository
    ``S2IntelligenceGateway`` with its default ``S2Transport``, which resolves
    the shared Semantic Scholar API key-pool/router.  Injected fake gateways
    remain fully supported for offline tests.
    """

    def __init__(
        self,
        *,
        s2_gateway: Any | None = None,
        openalex_backend: Any | None = None,
        enrich_snippet_metadata: bool = False,
        s2_request_budget_seconds: float = 75.0,
    ) -> None:
        if s2_gateway is None:
            from optomind_research.s2_intelligence_gateway import (
                S2IntelligenceGateway,
                S2Transport,
            )
            from tools.academic_backends.semantic_scholar_backend import (
                _api_keys,
            )

            s2_keys = list(_api_keys() or ())
            if not s2_keys:
                s2_keys = _resolve_shared_s2_key_pool()
            s2_gateway = S2IntelligenceGateway(
                transport=S2Transport(
                    keys=s2_keys,
                    timeout_seconds=min(30.0, max(5.0, float(s2_request_budget_seconds))),
                    max_attempts=4,
                    max_elapsed_seconds=max(5.0, float(s2_request_budget_seconds)),
                )
            )
        if openalex_backend is None:
            from tools.academic_backends.openalex_backend import OpenAlexBackend

            openalex_backend = OpenAlexBackend()
        self.s2_gateway = s2_gateway
        self.openalex_backend = openalex_backend
        self.enrich_snippet_metadata = bool(enrich_snippet_metadata)

    def search_s2(self, query: str, *, limit: int) -> MethodOnlineSearchResult:
        combined: list[Any] = []
        provider_calls = {
            "s2_search": 0,
            "s2_batch": 0,
            "s2_snippet_search": 0,
        }
        quality_counts = {
            "s2_snippets_retrieved": 0,
            "s2_snippets_accepted": 0,
            "s2_snippets_rejected_scope": 0,
        }
        notices: list[str] = []
        cache_hit: bool | None = None
        status = ""

        transport_keys = getattr(
            getattr(self.s2_gateway, "transport", None),
            "keys",
            None,
        )
        if transport_keys is not None and not transport_keys:
            return MethodOnlineSearchResult(
                records=(),
                source_route="s2_search",
                status="",
                error="s2_key_pool_empty",
                provider_calls=provider_calls,
                quality_counts=quality_counts,
            )

        # The snippet endpoint is itself a relevance-ranked search over
        # structured scientific text.  Query it globally first; restricting it
        # to paper-search hits would throw away its main semantic-retrieval
        # advantage.
        try:
            from optomind_research.s2_text_chunk_retriever import S2TextChunkRetriever

            retrieval = S2TextChunkRetriever(
                gateway=self.s2_gateway,
                min_chars=240,
            ).retrieve(
                [query],
                paper_ids=None,
                limit_per_query=max(4, min(int(limit) * 2, 16)),
                requested_roles=["method"],
            )
            quality_counts["s2_snippets_retrieved"] = len(retrieval.accepted_chunks)
            scoped_chunks = [
                chunk
                for chunk in retrieval.accepted_chunks
                if _tmm_method_scope_match(
                    query=query,
                    title=chunk.title,
                    section=chunk.section,
                    text=chunk.text,
                )
            ]
            quality_counts["s2_snippets_accepted"] = len(scoped_chunks)
            quality_counts["s2_snippets_rejected_scope"] = (
                len(retrieval.accepted_chunks) - len(scoped_chunks)
            )
            combined.extend(chunk.to_dict() for chunk in scoped_chunks)
            provider_calls["s2_snippet_search"] = len(retrieval.query_runs)
            if retrieval.query_runs:
                run = retrieval.query_runs[0]
                cache_hit = _optional_bool(run.get("cache_hit"))
                status = str(run.get("status_category") or "")
        except Exception as exc:  # provider failures become partial reports
            notices.append(f"s2_snippet_unavailable:{_safe_error(exc)}")

        # Enrich exactly the papers returned by semantic body retrieval.  This
        # is cheaper and more relevant than a second broad paper search.
        paper_ids = [
            str(_first_value(record, ("paper_id",), "") or "").strip()
            for record in combined
            if str(_first_value(record, ("paper_id",), "") or "").strip()
        ][: max(1, min(int(limit), 8))]
        if (
            self.enrich_snippet_metadata
            and paper_ids
            and callable(getattr(self.s2_gateway, "batch_papers", None))
        ):
            try:
                records, response = self.s2_gateway.batch_papers(paper_ids)
                combined.extend(records or ())
                provider_calls["s2_batch"] = 1
                if cache_hit is None:
                    cache_hit = _optional_bool(_value(response, "cache_hit"))
                status = status or str(_value(response, "status_category") or "")
                response_error = str(_value(response, "error") or "").strip()
                if response_error:
                    notices.append(response_error)
            except Exception as exc:
                notices.append(f"s2_batch_unavailable:{_safe_error(exc)}")

        # If no body passage survived the quality gate, retain S2 paper
        # relevance search as a discovery/background fallback.
        if not combined:
            try:
                records, response = self.s2_gateway.search_papers(query, limit=limit)
                combined.extend(records or ())
                provider_calls["s2_search"] = 1
                cache_hit = _optional_bool(_value(response, "cache_hit"))
                status = str(_value(response, "status_category") or "")
                response_error = str(_value(response, "error") or "").strip()
                if response_error:
                    notices.append(response_error)
            except Exception as exc:
                notices.append(f"s2_search_unavailable:{_safe_error(exc)}")

        if str(status).lower() in {"rate_limited", "429"} or "429" in str(
            status
        ):
            if "s2_rate_limited" not in notices:
                notices.append("s2_rate_limited")

        return MethodOnlineSearchResult(
            records=tuple(combined),
            cache_hit=cache_hit,
            source_route="s2_snippet_search" if paper_ids else "s2_search",
            status=status,
            error=";".join(notices),
            provider_calls=provider_calls,
            quality_counts=quality_counts,
        )

    def search_openalex(self, query: str, *, limit: int) -> MethodOnlineSearchResult:
        try:
            records = self.openalex_backend.search(query, max_results=limit)
        except Exception as exc:  # provider failures become partial reports
            return MethodOnlineSearchResult(
                records=(), source_route="openalex_search", error=_safe_error(exc)
            )
        return MethodOnlineSearchResult(
            records=tuple(records or ()),
            source_route="openalex_search",
            error=str(getattr(self.openalex_backend, "last_error", "") or ""),
        )


def query_kb(
    sqlite_path: Path,
    query: str,
    *,
    top_k: int = 8,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Resolve the existing KB query function at call time.

    The small wrapper preserves a useful monkeypatch seam for tests while
    keeping all SQLite/FTS behavior in ``optomind_research``.
    """

    from optomind_research.review_knowledge_base import query_kb as review_query_kb

    return review_query_kb(
        sqlite_path, query, top_k=top_k, include_raw=include_raw
    )


_QUERY_SPECS: tuple[tuple[MethodPurpose, tuple[str, ...], str], ...] = (
    (
        MethodPurpose.design_family,
        ("design_family", "design_families", "architecture", "geometry"),
        "{value}; optical thin-film and multilayer design families for transfer-matrix modeling",
    ),
    (
        MethodPurpose.material_choice,
        ("material_choice", "materials", "material", "material_system"),
        "optical material selection for {value}, including refractive-index and absorption constraints",
    ),
    (
        MethodPurpose.objective_formulation,
        ("objective_formulation", "objectives", "objective", "targets", "target"),
        "spectral objective formulation for {value} in multilayer optical design",
    ),
    (
        MethodPurpose.optimization_strategy,
        ("optimization_strategy", "optimizer", "optimization", "search_strategy"),
        "optimization methods for {value} in transfer-matrix multilayer design",
    ),
    (
        MethodPurpose.fabrication_constraint,
        ("fabrication_constraint", "fabrication_constraints", "fabrication", "constraints"),
        "fabrication-aware layer parameterization for {value} in optical thin films",
    ),
    (
        MethodPurpose.failure_mode,
        ("failure_mode", "failure_modes", "failures", "risk"),
        "failure modes and mitigation methods for {value} in transfer-matrix optical design",
    ),
)


def _render_problem_value(value: Any, *, limit: int = 180) -> str:
    if isinstance(value, Mapping):
        parts = [
            f"{key} {_render_problem_value(value[key], limit=limit)}"
            for key in sorted(value, key=lambda item: str(item))
            if value[key] not in (None, "", [], {})
        ]
        rendered = "; ".join(parts)
    elif isinstance(value, (list, tuple)):
        rendered = ", ".join(_render_problem_value(item, limit=limit) for item in value)
    elif isinstance(value, set):
        rendered = ", ".join(
            _render_problem_value(item, limit=limit) for item in sorted(value, key=str)
        )
    else:
        rendered = str(value).strip()
    rendered = re.sub(r"\s+", " ", rendered).strip()
    return rendered[:limit].rstrip()


_METHOD_QUERY_STOPWORDS = {
    "which",
    "what",
    "when",
    "where",
    "with",
    "from",
    "into",
    "under",
    "using",
    "method",
    "methods",
    "design",
    "designs",
    "preserve",
    "including",
    "optical",
}
_TMM_SCOPE_ANCHORS = (
    "multilayer",
    "multi-layer",
    "thin film",
    "thin-film",
    "coating",
    "layer stack",
    "bragg reflector",
    "bragg mirror",
    "one-dimensional photonic crystal",
    "1d photonic crystal",
    "transfer matrix",
)
_OUTSIDE_ISOTROPIC_TMM_PATTERNS = (
    "metasurface",
    "metamaterial",
    "moth eye",
    "moth-eye",
    "diffraction grating",
    "cylindrical cloak",
    "cylindrical cloaking",
    "antenna array",
    "rectenna",
    "waveguide mode",
    "photonic integrated circuit",
    "optical waveguide",
    "multilayer waveguide",
    "neutron detector",
    "fuel cell",
    "software-defined networking",
    "bioelectrochemical",
    "thermal imaging",
    "anisotropic",
    "uniaxial",
)
_SPECTRAL_LAYER_ANCHORS = (
    "transfer matrix",
    "characteristic matrix",
    "bragg reflector",
    "bragg mirror",
    "photonic crystal",
    "bandpass filter",
    "band-pass filter",
    "stopband",
    "passband",
    "reflectance",
    "transmittance",
    "transmission spectrum",
    "reflection spectrum",
    "interference filter",
    "quarter-wave",
    "optical thickness",
)


def _method_term(token: str) -> str:
    token = token.casefold()
    if token.startswith(("reflect", "reflact")):
        return "reflect"
    if token.startswith("transmit") or token.startswith("transmiss"):
        return "transmit"
    if token.startswith("optim"):
        return "optim"
    if token.startswith("parameter"):
        return "parameter"
    if token.startswith("robust"):
        return "robust"
    if token.startswith("inciden"):
        return "incidence"
    if token.startswith("multilayer"):
        return "multilayer"
    return token


def _method_terms(text: str) -> set[str]:
    return {
        _method_term(token)
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9]+", str(text or ""))
        if len(token) >= 4 and token.casefold() not in _METHOD_QUERY_STOPWORDS
    }


def _tmm_method_scope_match(*, query: str, title: str, section: str, text: str) -> bool:
    """Reject semantically nearby but physically out-of-scope S2 snippets."""

    combined = f"{title} {section} {text}".casefold()
    passage = str(text or "").strip().casefold()
    # A paper's outlook can mention multilayers and optimizers without having
    # executed or explained either. Such discovery leads are useful for graph
    # traversal, but they are not method guidance for an executable TMM route.
    if passage.startswith("future work") or "future work includes" in passage[:240]:
        return False
    if any(pattern in combined for pattern in _OUTSIDE_ISOTROPIC_TMM_PATTERNS):
        return False
    if not any(anchor in combined for anchor in _TMM_SCOPE_ANCHORS):
        return False
    # Generic uses of "multilayer", "thin film", or "coating" occur in
    # batteries, neutron detectors, waveguides and electronics.  Method
    # guidance must additionally discuss a spectral layered-optics mechanism.
    if not any(anchor in combined for anchor in _SPECTRAL_LAYER_ANCHORS):
        return False
    query_terms = _method_terms(query)
    candidate_terms = _method_terms(combined)
    overlap = query_terms & candidate_terms
    if len(overlap) >= 2:
        return True
    # A named TMM/DBR design family plus one task-specific term is still a
    # useful method passage; a generic optical text is not.
    strong_family = any(
        anchor in combined
        for anchor in (
            "transfer matrix",
            "bragg reflector",
            "bragg mirror",
            "one-dimensional photonic crystal",
            "1d photonic crystal",
        )
    )
    return strong_family and bool(overlap)


def _as_problem_mapping(problem: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    if isinstance(problem, Mapping):
        return problem
    if hasattr(problem, "model_dump"):
        dumped = problem.model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return dumped
    raise TypeError("problem payload must be a mapping or a Pydantic model")


def _infer_purpose(query_text: str) -> MethodPurpose:
    text = query_text.casefold()
    if any(term in text for term in ("material", "refractive index", "absorption")):
        return MethodPurpose.material_choice
    if any(term in text for term in ("objective", "target", "metric", "reflectance", "transmittance")):
        return MethodPurpose.objective_formulation
    if any(term in text for term in ("optimizer", "optimization", "search", "genetic", "particle swarm")):
        return MethodPurpose.optimization_strategy
    if any(term in text for term in ("fabricat", "deposition", "roughness", "tolerance")):
        return MethodPurpose.fabrication_constraint
    if any(term in text for term in ("failure", "unstable", "convergence", "sensitivity")):
        return MethodPurpose.failure_mode
    return MethodPurpose.design_family


def generate_method_research_queries(
    problem: Mapping[str, Any] | Any,
    explicit_queries: Sequence[str | MethodResearchQuery | Mapping[str, Any]] | None = None,
    *,
    max_queries: int = 6,
) -> list[MethodResearchQuery]:
    """Build deterministic, bounded scientific queries from a generic payload."""

    if int(max_queries) < 1:
        raise ValueError("max_queries must be positive")
    max_queries = min(int(max_queries), 12)
    payload = _as_problem_mapping(problem)
    queries: list[MethodResearchQuery] = []
    seen_text: set[str] = set()
    seen_ids: set[str] = set()

    def add_query(query: MethodResearchQuery) -> None:
        normalized = re.sub(r"\s+", " ", query.query_text).strip().casefold()
        if not normalized or normalized in seen_text:
            return
        query_id = query.query_id
        if query_id in seen_ids:
            suffix = 2
            while f"{query_id}_{suffix}" in seen_ids:
                suffix += 1
            query = query.model_copy(update={"query_id": f"{query_id}_{suffix}"})
        seen_text.add(normalized)
        seen_ids.add(query.query_id)
        queries.append(query)

    if isinstance(explicit_queries, (str, MethodResearchQuery, Mapping)):
        explicit_items: Sequence[Any] = [explicit_queries]
    else:
        explicit_items = explicit_queries or ()

    for index, item in enumerate(explicit_items, start=1):
        if isinstance(item, MethodResearchQuery):
            add_query(item)
        elif isinstance(item, Mapping):
            add_query(MethodResearchQuery.model_validate(item))
        else:
            text = _render_problem_value(item, limit=220)
            if text:
                add_query(
                    MethodResearchQuery(
                        query_id=f"explicit_{index:02d}",
                        query_text=text,
                        purpose=_infer_purpose(text),
                        priority=1,
                    )
                )
        if len(queries) >= max_queries:
            return queries[:max_queries]

    anchor = _render_problem_value(
        payload.get("normalized_request_english")
        or payload.get("original_request")
        or payload.get("problem_statement")
        or "planar multilayer optical design",
        limit=240,
    )

    # The problem analyzer is the canonical upstream contract.  Its explicit
    # scientific questions are more informative than generic field aliases
    # and therefore receive first priority.
    research_questions = payload.get("method_research_questions") or []
    if isinstance(research_questions, str):
        research_questions = [research_questions]
    for index, item in enumerate(research_questions, start=1):
        text = _render_problem_value(item, limit=240)
        if text:
            scoped_text = (
                f"{anchor}; {text}; planar optical thin-film interference "
                "and transfer-matrix design"
            )
            add_query(
                MethodResearchQuery(
                    query_id=f"analysis_question_{index:02d}",
                    query_text=scoped_text,
                    purpose=_infer_purpose(text),
                    priority=1,
                )
            )
        if len(queries) >= max_queries:
            return queries[:max_queries]

    analyzer_specs: tuple[tuple[MethodPurpose, str, Any], ...] = (
        (
            MethodPurpose.design_family,
            "{anchor}; compatible planar multilayer design families and physical mechanisms",
            payload.get("design_variables") or payload.get("primary_intent"),
        ),
        (
            MethodPurpose.material_choice,
            "{anchor}; optical material systems and refractive-index or absorption constraints for {value}",
            payload.get("known_stack_materials"),
        ),
        (
            MethodPurpose.objective_formulation,
            "{anchor}; objective formulation for {value}",
            {
                "observables": payload.get("target_observables"),
                "preferred": payload.get("preferred_behaviors"),
                "suppressed": payload.get("suppressed_behaviors"),
                "wavelengths_nm": payload.get("wavelengths_nm"),
                "angles_deg": payload.get("angles_deg"),
                "polarizations": payload.get("polarizations"),
            },
        ),
        (
            MethodPurpose.optimization_strategy,
            "{anchor}; optimization strategy for bounded variables {value}",
            payload.get("design_variables"),
        ),
        (
            MethodPurpose.fabrication_constraint,
            "{anchor}; fabrication-aware parameterization and tolerance treatment for {value}",
            payload.get("manufacturing_constraints"),
        ),
        (
            MethodPurpose.failure_mode,
            "{anchor}; common failure modes, target conflicts, and mitigation under {value}",
            payload.get("ambiguities") or payload.get("assumptions"),
        ),
    )
    analyzer_keys = {
        "normalized_request_english",
        "method_research_questions",
        "known_stack_materials",
        "target_observables",
        "preferred_behaviors",
        "suppressed_behaviors",
        "design_variables",
        "manufacturing_constraints",
    }
    if any(key in payload for key in analyzer_keys):
        for purpose, template, value in analyzer_specs:
            rendered = _render_problem_value(value)
            if not rendered or rendered in {"None", "[]", "{}"}:
                continue
            add_query(
                MethodResearchQuery(
                    query_id=f"analysis_{purpose.value}",
                    query_text=template.format(anchor=anchor, value=rendered),
                    purpose=purpose,
                    priority=min(5, 2 + len(queries)),
                )
            )
            if len(queries) >= max_queries:
                return queries[:max_queries]

    for purpose, aliases, template in _QUERY_SPECS:
        value: Any = None
        for alias in aliases:
            if alias in payload and payload[alias] not in (None, "", [], {}):
                value = payload[alias]
                break
        if value is None:
            continue
        rendered = _render_problem_value(value)
        if not rendered:
            continue
        add_query(
            MethodResearchQuery(
                query_id=f"generated_{purpose.value}",
                query_text=template.format(value=rendered),
                purpose=purpose,
                priority=min(5, 2 + len(queries)),
            )
        )
        if len(queries) >= max_queries:
            break

    if not queries:
        add_query(
            MethodResearchQuery(
                query_id="generated_general",
                query_text="transfer-matrix multilayer optical design methods",
                purpose=MethodPurpose.design_family,
                priority=3,
            )
        )
    return queries[:max_queries]


build_method_research_queries = generate_method_research_queries


_METHOD_PATTERNS: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (
        ("distributed bragg reflector", "bragg mirror", "dbr"),
        "distributed Bragg reflector multilayers",
        "Parameterize alternating high- and low-index layers and tune their thickness sequence against the declared spectral target.",
        "Useful for periodic or cavity-coupled spectral selectivity in isotropic multilayer studies.",
    ),
    (
        ("chirped multilayer", "chirped mirror", "aperiodic multilayer"),
        "chirped or aperiodic multilayer design",
        "Vary layer optical thickness across the stack to distribute or broaden spectral response rather than enforcing one period.",
        "Useful when a bounded spectral band or angular response matters more than a single narrow feature.",
    ),
    (
        ("rugate", "continuous-index", "graded-index"),
        "graded-index or rugate filtering",
        "Represent a continuous or finely discretized index profile and optimize the profile under the available layer parameterization.",
        "Useful for smooth spectral filtering when the fabrication process can realize graded or finely segmented profiles.",
    ),
    (
        ("transfer matrix", "transfer-matrix", "characteristic matrix"),
        "transfer-matrix multilayer parameterization",
        "Keep layer order, thickness, optical constants, incidence angle, and polarization explicit so candidate stacks can be compared consistently.",
        "Useful for plane-wave isotropic multilayer tasks that remain within the declared TMM model.",
    ),
    (
        ("particle swarm", "pso"),
        "particle-swarm thickness optimization",
        "Use a population of bounded thickness vectors to explore nonconvex spectral objectives before deterministic refinement.",
        "Useful when the objective is non-smooth or multimodal; budget and reproducibility controls remain necessary.",
    ),
    (
        ("genetic algorithm", "evolutionary algorithm", "differential evolution"),
        "evolutionary multilayer optimization",
        "Search bounded layer parameters with population variation and selection while retaining explicit material and fabrication constraints.",
        "Useful for discrete or strongly multimodal layer choices; it can spend many evaluations and does not validate physics by itself.",
    ),
    (
        ("bayesian optimization", "gaussian process"),
        "surrogate-assisted optical optimization",
        "Use a surrogate to allocate expensive TMM evaluations toward promising regions while tracking uncertainty and the declared budget.",
        "Useful when each TMM evaluation is costly; surrogate error and acquisition bias require independent verification.",
    ),
    (
        ("fabrication tolerance", "thickness tolerance", "manufacturing tolerance", "roughness"),
        "fabrication-tolerance-aware multilayer design",
        "Include bounded thickness or material perturbations during ranking so the nominal stack is not the only reported design condition.",
        "Useful for process-sensitive stacks; the perturbation model must match the actual fabrication process and still needs TMM checks.",
    ),
)


def _depth_rank(depth: MethodContentDepth) -> int:
    return {
        MethodContentDepth.metadata: 0,
        MethodContentDepth.abstract: 1,
        MethodContentDepth.s2_snippet: 2,
        MethodContentDepth.fulltext: 3,
    }[depth]


def _use_rank(use: MethodAllowedUse) -> int:
    return {
        MethodAllowedUse.discovery: 0,
        MethodAllowedUse.background: 1,
        MethodAllowedUse.method_guidance: 2,
        MethodAllowedUse.direct_fact: 3,
    }[use]


def synthesize_method_findings(
    evidence: Sequence[MethodEvidence | Mapping[str, Any]],
    queries: Sequence[MethodResearchQuery] | None = None,
    *,
    max_findings: int = 8,
) -> list[MethodFinding]:
    """Create bounded deterministic method leads from permitted evidence."""

    del queries  # Query links remain on evidence; no role-specific plan is used.
    typed_evidence = [
        item if isinstance(item, MethodEvidence) else MethodEvidence.model_validate(item)
        for item in evidence
    ]
    usable = [
        item
        for item in typed_evidence
        if item.allowed_use in {MethodAllowedUse.method_guidance, MethodAllowedUse.direct_fact}
    ]
    findings: list[MethodFinding] = []
    for patterns, name, principle, applicability in _METHOD_PATTERNS:
        matched = [
            item
            for item in usable
            if any(pattern in item.text.casefold() for pattern in patterns)
        ]
        if not matched:
            continue
        evidence_ids = [item.evidence_id for item in typed_evidence if item in matched]
        confidence = sum(
            0.9 if item.allowed_use == MethodAllowedUse.direct_fact else 0.78 if item.content_depth == MethodContentDepth.fulltext else 0.64
            for item in matched
        ) / len(matched)
        findings.append(
            MethodFinding(
                design_family=name,
                method_name=name,
                reusable_principle=principle,
                evidence_ids=evidence_ids,
                applicability=applicability,
                limitations=(
                    "This is literature method guidance, not a physics certificate; "
                    "validate material data, objectives, convergence, and energy behavior with TMM."
                ),
                confidence=min(1.0, confidence),
            )
        )
        if len(findings) >= max(1, int(max_findings)):
            break
    return findings


deterministic_method_finding_synthesizer = synthesize_method_findings


DEFAULT_METHOD_SYNTHESIS_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "TMM Literature Method Synthesizer.txt"
)


_KB_TOPIC_GENERIC_TERMS = {
    "analysis",
    "application",
    "design",
    "device",
    "engineering",
    "material",
    "materials",
    "method",
    "multilayer",
    "optical",
    "optimization",
    "performance",
    "photonic",
    "research",
    "structure",
    "system",
    "technology",
    # Request/agent boilerplate must never make two scientific topics look
    # related merely because both plans describe validation and trade-offs.
    "analyze",
    "best",
    "candidate",
    "candidates",
    "compare",
    "conflict",
    "count",
    "degree",
    "degrees",
    "domain",
    "error",
    "errors",
    "evaluate",
    "failure",
    "full",
    "goal",
    "goals",
    "limitation",
    "limitations",
    "physical",
    "rather",
    "report",
    "result",
    "results",
    "route",
    "routes",
    "target",
    "targets",
    "than",
    "trade",
    "valid",
    "validation",
    "verify",
    "verified",
}


def _topic_terms(text: str) -> set[str]:
    return _method_terms(text) - _KB_TOPIC_GENERIC_TERMS


_QUERY_PLAN_TOPIC_KEYS = {
    "question",
    "user_query",
    "problem_understanding",
    "main_scope",
    "scope_items",
    "keywords",
    "keyword_decomposition",
    "scope_definition",
    "extra_notes",
}


def _query_plan_topic_text(payload: Any) -> str:
    """Extract scientific scope only, excluding raw model/audit boilerplate."""

    collected: list[str] = []

    def collect_value(value: Any) -> None:
        if isinstance(value, str):
            clean = re.sub(r"\s+", " ", value).strip()
            if clean:
                collected.append(clean)
        elif isinstance(value, Mapping):
            for child_key, child_value in value.items():
                normalized = str(child_key).strip().casefold()
                if normalized in _QUERY_PLAN_TOPIC_KEYS:
                    collect_value(child_value)
                elif normalized in {"input", "output", "result"}:
                    collect_mapping(child_value)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for child in value:
                collect_value(child)

    def collect_mapping(value: Any) -> None:
        if not isinstance(value, Mapping):
            return
        for key, child in value.items():
            normalized = str(key).strip().casefold()
            if normalized in _QUERY_PLAN_TOPIC_KEYS:
                collect_value(child)
            elif normalized in {"input", "output", "result"}:
                collect_mapping(child)

    collect_mapping(payload)
    return " ".join(dict.fromkeys(collected))


def _kb_topic_matches_question(sqlite_path: Path, question: str) -> bool:
    plan_candidates = (
        sqlite_path.parent / "source_query_plan.current_english.json",
        sqlite_path.parent / "source_query_plan.json",
    )
    plan_path = next((path for path in plan_candidates if path.is_file()), None)
    if plan_path is None:
        return False
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    # Never compare against the full provenance JSON.  It can contain raw LLM
    # responses, validation reports and usage metadata whose generic wording
    # creates false cross-topic overlap.
    source_terms = _topic_terms(_query_plan_topic_text(payload))
    question_terms = _topic_terms(question)
    if not source_terms or not question_terms:
        return False
    overlap = source_terms & question_terms
    return len(overlap) >= 2 and len(overlap) / min(
        len(source_terms), len(question_terms)
    ) >= 0.10


_OPTICAL_TOPIC_FAMILIES: dict[str, tuple[str, ...]] = {
    "antireflection": (
        "antireflection",
        "anti-reflection",
        "anti reflection",
        "reflection suppression",
    ),
    "thermal_radiation": (
        "radiative cooling",
        "thermal emission",
        "thermal emitter",
        "atmospheric window",
        "emissivity",
    ),
    "spectral_filter": (
        "bandpass",
        "band-pass",
        "passband",
        "stopband",
        "notch filter",
    ),
    "high_reflector": (
        "high reflectance",
        "high-reflectance",
        "bragg multilayer",
        "bragg mirror",
        "distributed bragg reflector",
    ),
    "absorber": (
        "perfect absorber",
        "optical absorber",
        "absorptance",
    ),
    "dispersion": (
        "group delay dispersion",
        "dispersion compensation",
        "ultrafast pulse",
    ),
}


def _optical_topic_families(text: str) -> set[str]:
    normalized = str(text or "").casefold()
    return {
        family
        for family, markers in _OPTICAL_TOPIC_FAMILIES.items()
        if any(marker in normalized for marker in markers)
    }


def _local_method_scope_match(
    *, query: str, title: str, section: str, text: str
) -> bool:
    """Apply physical-scope and task-topic gates to local KB records."""

    if not _tmm_method_scope_match(
        query=query, title=title, section=section, text=text
    ):
        return False
    query_families = _optical_topic_families(query)
    record_text = f"{title} {section} {text}"
    record_families = _optical_topic_families(record_text)
    if query_families and record_families and query_families.isdisjoint(record_families):
        return False
    # Require actual scientific overlap beyond generic optics/TMM vocabulary.
    overlap = _topic_terms(query) & _topic_terms(record_text)
    return len(overlap) >= 2


def _local_shallow_topic_match(
    *, query: str, title: str, section: str, text: str
) -> bool:
    """Conservative relevance gate for metadata/abstract discovery leads.

    Sparse records cannot demonstrate an executable method, but they may stay
    as discovery/background material when the title/abstract shares concrete
    scientific terms and does not contradict the task's optical family.
    """

    record_text = f"{title} {section} {text}"
    query_families = _optical_topic_families(query)
    record_families = _optical_topic_families(record_text)
    if query_families and record_families and query_families.isdisjoint(record_families):
        return False
    if not any(anchor in record_text.casefold() for anchor in _TMM_SCOPE_ANCHORS):
        return False
    overlap = _topic_terms(query) & _topic_terms(record_text)
    shared_family = query_families & record_families
    return len(overlap) >= 2 or (bool(shared_family) and bool(overlap))


def discover_review_kb_paths(
    project_root: str | Path | None = None,
    *,
    question: str | None = None,
) -> tuple[Path, ...]:
    """Find the active local ReviewKnowledgeBase without binding to one run.

    Explicit caller paths always remain preferable. Automatic discovery is
    intentionally narrow: it searches only the canonical knowledge-base root,
    prefers the high-quality visual core, and returns one database so unrelated
    historical topic runs cannot flood method retrieval.
    """

    root = (
        Path(project_root)
        if project_root is not None
        else Path(__file__).resolve().parents[2]
    )
    base = root / "outputs" / "review_knowledge_base"
    if not base.is_dir():
        return ()
    candidates = [
        path
        for path in base.glob("*/review_knowledge_base.sqlite")
        if path.is_file()
    ]
    if not candidates:
        return ()
    if question is not None:
        candidates = [
            path for path in candidates if _kb_topic_matches_question(path, question)
        ]
        if not candidates:
            return ()
    candidates.sort(
        key=lambda path: (
            "hqvisual" not in path.parent.name.casefold(),
            -path.stat().st_mtime,
            str(path).casefold(),
        )
    )
    return (candidates[0],)


def _qwen_total_tokens(usage: Mapping[str, Any]) -> int:
    """Cascade total_tokens -> input+output -> prompt+completion token counts."""

    total = usage.get("total_tokens")
    if total is not None:
        return int(total or 0)
    for first_key, second_key in (
        ("input_tokens", "output_tokens"),
        ("prompt_tokens", "completion_tokens"),
    ):
        if usage.get(first_key) is not None or usage.get(second_key) is not None:
            return int(usage.get(first_key) or 0) + int(usage.get(second_key) or 0)
    return 0


class QwenMethodFindingSynthesizer:
    """Compress permitted passages into reusable, evidence-linked methods.

    This is deliberately one bounded Qwen call.  It cannot invent evidence
    identifiers, certify physics, or mutate an executable task.  If the call
    fails, :class:`TMMMethodResearchAdapter` falls back to deterministic
    pattern extraction.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        prompt_path: str | Path = DEFAULT_METHOD_SYNTHESIS_PROMPT,
        force_mock: bool | None = None,
        maximum_evidence_items: int = 14,
        maximum_chars_per_item: int = 1400,
    ) -> None:
        if client is None:
            # T-04: literature analysis / synthesis routes through the plus tier.
            client = ArticlePlusQwenClient(role="plus")
        self.client = client
        self.prompt_path = Path(prompt_path)
        self.force_mock = force_mock
        self.maximum_evidence_items = max(1, min(int(maximum_evidence_items), 20))
        self.maximum_chars_per_item = max(240, min(int(maximum_chars_per_item), 2400))
        self._usage: list[dict[str, Any]] = []

    def drain_usage(self) -> list[dict[str, Any]]:
        rows = list(self._usage)
        self._usage.clear()
        return rows

    def __call__(
        self,
        evidence: Sequence[MethodEvidence],
        queries: Sequence[MethodResearchQuery],
    ) -> list[MethodFinding]:
        usable = [
            item
            for item in evidence
            if item.allowed_use
            in {MethodAllowedUse.method_guidance, MethodAllowedUse.direct_fact}
        ]
        usable.sort(
            key=lambda item: (
                -_use_rank(item.allowed_use),
                -_depth_rank(item.content_depth),
                -len(item.query_ids),
                item.evidence_id,
            )
        )
        compact = [
            {
                "evidence_id": item.evidence_id,
                "title": item.title,
                "year": item.year,
                "content_depth": item.content_depth.value,
                "allowed_use": item.allowed_use.value,
                "query_ids": item.query_ids,
                "text": item.text[: self.maximum_chars_per_item],
            }
            for item in usable[: self.maximum_evidence_items]
        ]
        if not compact:
            return []
        response = self.client.call(
            [
                {
                    "role": "system",
                    "content": self.prompt_path.read_text(encoding="utf-8"),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "queries": [item.model_dump(mode="json") for item in queries],
                            "evidence": compact,
                            "allowed_evidence_ids": [item["evidence_id"] for item in compact],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            max_tokens=3000,
            force_mock=self.force_mock,
        )
        usage_row = dict(response.get("_llm_usage") or {})
        self._usage.append(usage_row)
        # T-04: meter every synthesis Qwen call on the run-level CostTracker.
        get_cost_tracker().record_qwen_usage("plus", _qwen_total_tokens(usage_row))
        raw_text = str(response.get("content") or "").strip()
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            start, end = raw_text.find("{"), raw_text.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("method synthesis did not return a JSON object")
            payload = json.loads(raw_text[start : end + 1])
        raw_findings = payload.get("method_findings", []) if isinstance(payload, Mapping) else []
        allowed_ids = {item["evidence_id"] for item in compact}
        findings: list[MethodFinding] = []
        for raw in raw_findings[:8] if isinstance(raw_findings, list) else []:
            finding = MethodFinding.model_validate(raw)
            if not finding.evidence_ids or any(
                item not in allowed_ids for item in finding.evidence_ids
            ):
                continue
            findings.append(finding)
        return findings


@dataclass(frozen=True, slots=True)
class _EvidenceCandidate:
    key: str
    evidence: MethodEvidence


class _EvidenceAccumulator:
    """Merge paper/chunk duplicates without dropping route or query links."""

    def __init__(self) -> None:
        self._items: dict[str, MethodEvidence] = {}

    @property
    def items(self) -> list[MethodEvidence]:
        return list(self._items.values())

    def add(self, candidate: _EvidenceCandidate) -> None:
        current = self._items.get(candidate.key)
        if current is None:
            self._items[candidate.key] = candidate.evidence
            return

        current_depth = current.content_depth
        candidate_depth = candidate.evidence.content_depth
        selected = candidate.evidence if _depth_rank(candidate_depth) > _depth_rank(current_depth) else current
        if _depth_rank(candidate_depth) == _depth_rank(current_depth) and len(candidate.evidence.text) > len(current.text):
            selected = candidate.evidence
        selected_use = (
            candidate.evidence.allowed_use
            if _use_rank(candidate.evidence.allowed_use) > _use_rank(current.allowed_use)
            else current.allowed_use
        )
        depth = selected.content_depth
        if selected_use not in _PERMITTED_USES[depth]:
            selected_use = max(
                _PERMITTED_USES[depth], key=_use_rank
            )
        routes = _ordered_union(
            current.source_route.split("|"), candidate.evidence.source_route.split("|")
        )
        query_ids = _ordered_union(current.query_ids, candidate.evidence.query_ids)
        local_path = current.local_path or candidate.evidence.local_path
        merged = selected.model_copy(
            update={
                "evidence_id": current.evidence_id,
                "paper_id": current.paper_id or candidate.evidence.paper_id,
                "title": selected.title or current.title,
                "doi": current.doi or candidate.evidence.doi,
                "year": current.year if current.year is not None else candidate.evidence.year,
                "source_route": "|".join(routes),
                "content_depth": depth,
                "query_ids": query_ids,
                "allowed_use": selected_use,
                "local_path": local_path,
            }
        )
        self._items[candidate.key] = merged


def _ordered_union(first: Iterable[str], second: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in list(first) + list(second):
        value = str(value).strip()
        if value and value not in result:
            result.append(value)
    return result


def _safe_error(exc: Exception) -> str:
    text = f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()
    return text[:300]


def _value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    if hasattr(record, name):
        return getattr(record, name)
    if hasattr(record, "model_dump"):
        try:
            dumped = record.model_dump(mode="python")
            if isinstance(dumped, Mapping):
                return dumped.get(name, default)
        except Exception:
            pass
    return default


def _first_value(record: Any, names: Sequence[str], default: Any = None) -> Any:
    for name in names:
        value = _value(record, name, None)
        if value not in (None, "", [], {}):
            return value
    return default


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def _safe_doi(value: Any) -> str:
    doi = str(value or "").strip()
    doi = re.sub(r"^https?://doi\.org/", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^doi:\s*", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def _stable_token(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()[:12]


def _strip_source_prefix(value: str) -> str:
    for prefix in ("s2:", "semantic_scholar:", "openalex:", "doi:"):
        if value.casefold().startswith(prefix):
            return value[len(prefix) :]
    return value


def _canonical_identity(paper_id: str, doi: str) -> str:
    if doi:
        return f"doi:{doi.casefold()}"
    return _strip_source_prefix(paper_id).casefold()


def _normalize_depth(value: Any) -> MethodContentDepth | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().casefold().replace("-", "_")
    aliases = {
        "metadata": MethodContentDepth.metadata,
        "abstract": MethodContentDepth.abstract,
        "tldr": MethodContentDepth.abstract,
        "snippet": MethodContentDepth.s2_snippet,
        "s2_body_snippet": MethodContentDepth.s2_snippet,
        "s2_snippet": MethodContentDepth.s2_snippet,
        "structured_snippet": MethodContentDepth.s2_snippet,
        "text_chunk": MethodContentDepth.fulltext,
        "full_text": MethodContentDepth.fulltext,
        "fulltext": MethodContentDepth.fulltext,
    }
    return aliases.get(normalized)


def _normalize_use(value: Any) -> MethodAllowedUse | None:
    if value in (None, ""):
        return None
    try:
        return MethodAllowedUse(str(value).strip().casefold())
    except ValueError:
        return None


def _raw_mapping(record: Any) -> Mapping[str, Any]:
    raw = _first_value(record, ("raw", "raw_metadata"), {})
    return raw if isinstance(raw, Mapping) else {}


def _make_candidate(
    record: Any,
    *,
    source_route: str,
    query_id: str,
    local_path: str | None = None,
    record_kind: str = "paper",
) -> _EvidenceCandidate | None:
    if isinstance(record, MethodEvidence):
        evidence = record.model_copy(
            update={
                "query_ids": _ordered_union(record.query_ids, [query_id]),
                "local_path": record.local_path or local_path,
            }
        )
        identity = _canonical_identity(evidence.paper_id, _safe_doi(evidence.doi))
        chunk_id = ""
        return _EvidenceCandidate(
            key=f"{identity}|paper",
            evidence=evidence,
        )

    raw = _raw_mapping(record)
    paper_id_value = _first_value(
        record,
        ("paper_id", "semantic_scholar_paper_id", "s2_paper_id", "openalex_id", "source_id", "id"),
        _first_value(raw, ("paper_id", "source_id", "id"), ""),
    )
    source_id = str(paper_id_value or "").strip()
    paper_id = _strip_source_prefix(source_id)
    doi = _safe_doi(_first_value(record, ("doi",), _first_value(raw, ("doi", "DOI"), "")))
    title = str(
        _first_value(
            record,
            ("title",),
            _first_value(raw, ("title",), ""),
        )
        or ""
    ).strip()
    if not paper_id and doi:
        paper_id = f"doi:{doi}"
    if not paper_id and title:
        paper_id = f"title:{_stable_token(title.casefold())}"
    if not paper_id:
        return None

    year_raw = _first_value(record, ("year", "publication_year"), _first_value(raw, ("year",), None))
    try:
        year = int(year_raw) if year_raw not in (None, "") else None
    except (TypeError, ValueError):
        year = None

    explicit_depth = _normalize_depth(
        _first_value(record, ("content_depth",), _first_value(raw, ("content_depth",), None))
    )
    snippet_text = _first_value(
        record,
        ("snippet_text", "snippet", "snippet_text_preview"),
        _first_value(raw, ("snippet_text", "snippet"), None),
    )
    abstract_text = _first_value(
        record,
        ("abstract", "abstract_or_snippet"),
        _first_value(raw, ("abstract", "abstract_or_snippet"), None),
    )
    text_value = _first_value(
        record,
        ("text", "text_preview", "caption", "caption_preview"),
        _first_value(raw, ("text", "text_preview", "caption", "caption_preview"), None),
    )
    if text_value in (None, ""):
        text_value = snippet_text if snippet_text not in (None, "") else abstract_text
    if text_value in (None, ""):
        text_value = title or paper_id
    text = str(text_value).strip()

    if explicit_depth is not None:
        depth = explicit_depth
    elif record_kind in {"text_chunk", "visual_chunk"}:
        depth = MethodContentDepth.fulltext
    elif snippet_text not in (None, ""):
        depth = MethodContentDepth.s2_snippet
    elif abstract_text not in (None, ""):
        depth = MethodContentDepth.abstract
    else:
        depth = MethodContentDepth.metadata

    requested_use = _normalize_use(
        _first_value(record, ("allowed_use",), _first_value(raw, ("allowed_use",), None))
    )
    default_use = {
        MethodContentDepth.metadata: MethodAllowedUse.discovery,
        MethodContentDepth.abstract: MethodAllowedUse.background,
        MethodContentDepth.s2_snippet: MethodAllowedUse.method_guidance,
        MethodContentDepth.fulltext: MethodAllowedUse.method_guidance,
    }[depth]
    allowed_use = requested_use if requested_use in _PERMITTED_USES[depth] else default_use

    chunk_id_value = _first_value(
        record,
        ("chunk_id", "snippet_id", "text_chunk_id", "visual_chunk_id"),
        _first_value(raw, ("chunk_id", "snippet_id"), ""),
    )
    chunk_id = str(chunk_id_value or "").strip()
    if record_kind in {"text_chunk", "visual_chunk"} and not chunk_id:
        return None
    identity = _canonical_identity(paper_id, doi)
    key = f"{identity}|chunk:{chunk_id}" if chunk_id else f"{identity}|paper"

    route = str(_first_value(record, ("source_route",), source_route) or source_route).strip()
    if not route:
        route = source_route
    prefix = (
        "local"
        if route.startswith("local")
        else "s2"
        if route.startswith("s2")
        else "openalex"
        if route.startswith("openalex")
        else "literature"
    )
    evidence_id = (
        f"{prefix}-chunk:{paper_id}:{chunk_id}"
        if chunk_id
        else f"{prefix}-paper:{paper_id}"
    )
    path_value = _first_value(record, ("local_path", "local_text_path"), local_path)
    evidence = MethodEvidence(
        evidence_id=evidence_id,
        paper_id=paper_id,
        title=title or paper_id,
        doi=doi,
        year=year,
        source_route=route,
        content_depth=depth,
        text=text,
        query_ids=[query_id],
        allowed_use=allowed_use,
        local_path=str(path_value) if path_value not in (None, "") else None,
    )
    return _EvidenceCandidate(key=key, evidence=evidence)


def _result_records(result: Any) -> tuple[list[Any], Any]:
    if isinstance(result, MethodOnlineSearchResult):
        return list(result.records), result
    if isinstance(result, tuple) and len(result) == 2:
        records, response = result
        return list(records or ()), response
    if isinstance(result, Mapping):
        records = result.get("records", result.get("results", result.get("data", [])))
        return list(records or ()), result
    return list(result or ()), None


def _invoke_search(client: Any, route: str, query: str, limit: int) -> Any:
    names = (
        ("search_s2", "search_papers", "search")
        if route == "s2_search"
        else ("search_openalex", "search_open_alex", "search")
    )
    for name in names:
        method = getattr(client, name, None)
        if not callable(method):
            continue
        try:
            return method(query, limit=limit)
        except TypeError:
            try:
                return method(query, max_results=limit)
            except TypeError:
                return method(query)
    raise AttributeError(f"online client does not expose a {route} search method")


def _response_value(response: Any, name: str, default: Any = None) -> Any:
    value = _value(response, name, default)
    if value is not default:
        return value
    if isinstance(response, Mapping):
        return response.get(name, default)
    return default


class TMMMethodResearchAdapter:
    """Local-first, S2-first method-research adapter for TMM design loops."""

    def __init__(
        self,
        review_kb_paths: Sequence[str | Path] | str | Path | None = None,
        *,
        review_kb_sqlite_paths: Sequence[str | Path] | str | Path | None = None,
        online_client: MethodResearchOnlineClient | Any | None = None,
        online_enabled: bool | None = None,
        enable_online: bool | None = None,
        max_queries: int = 6,
        local_top_k: int = 8,
        online_limit: int = 6,
        min_local_evidence: int = 1,
        minimum_local_method_papers: int = 2,
        require_method_guidance: bool = False,
        minimum_online_queries: int = 5,
        maximum_method_guidance_evidence: int = 18,
        online_wall_time_seconds: float = 360.0,
        s2_request_budget_seconds: float = 75.0,
        synthesis_callback: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        paths: list[str | Path] = []
        for value in (review_kb_paths, review_kb_sqlite_paths):
            if value is None:
                continue
            if isinstance(value, (str, Path)):
                paths.append(value)
            else:
                paths.extend(value)
        self.review_kb_paths = tuple(dict.fromkeys(Path(item) for item in paths))
        self.online_client = online_client
        if enable_online is not None:
            online_enabled = enable_online
        self.online_enabled = bool(online_client is not None) if online_enabled is None else bool(online_enabled)
        self.max_queries = min(max(1, int(max_queries)), 12)
        self.local_top_k = max(1, int(local_top_k))
        self.online_limit = max(1, int(online_limit))
        self.min_local_evidence = max(1, int(min_local_evidence))
        self.minimum_local_method_papers = max(1, int(minimum_local_method_papers))
        self.require_method_guidance = bool(require_method_guidance)
        self.minimum_online_queries = max(1, int(minimum_online_queries))
        self.maximum_method_guidance_evidence = max(
            1, int(maximum_method_guidance_evidence)
        )
        self.online_wall_time_seconds = max(1.0, float(online_wall_time_seconds))
        self.s2_request_budget_seconds = max(5.0, float(s2_request_budget_seconds))
        self.synthesis_callback = synthesis_callback
        self.clock = clock

    def research(
        self,
        problem: Mapping[str, Any] | Any,
        explicit_queries: Sequence[str | MethodResearchQuery | Mapping[str, Any]] | None = None,
        *,
        problem_id: str | None = None,
        online: bool | None = None,
        queries: Sequence[str | MethodResearchQuery | Mapping[str, Any]] | None = None,
    ) -> MethodResearchReport:
        """Run one bounded research pass and return a graceful report."""

        started = self.clock()
        if explicit_queries is None and queries is not None:
            explicit_queries = queries
        telemetry: dict[str, Any] = {
            "local_queries": 0,
            "s2_calls": 0,
            "s2_paper_search_calls": 0,
            "s2_batch_calls": 0,
            "s2_snippet_calls": 0,
            "s2_snippets_retrieved": 0,
            "s2_snippets_accepted": 0,
            "s2_snippets_rejected_scope": 0,
            "online_queries_skipped_budget": 0,
            "online_budget_exhausted": False,
            "openalex_calls": 0,
            "records_returned": 0,
            "records_accepted": 0,
            "records_rejected_scope": 0,
            "unique_evidence": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "source_routes": {},
            "cache_source_routes": {},
            "elapsed_seconds": 0.0,
            "errors": [],
            "usage": [],
        }
        reasons: list[str] = []
        try:
            queries = generate_method_research_queries(
                problem, explicit_queries, max_queries=self.max_queries
            )
        except Exception as exc:
            reasons.append(f"query_generation_error:{_safe_error(exc)}")
            telemetry["errors"].append(reasons[-1])
            telemetry["elapsed_seconds"] = max(0.0, self.clock() - started)
            return MethodResearchReport(
                problem_id=str(problem_id or _problem_id(problem)),
                queries=[],
                evidence=[],
                method_findings=[],
                unresolved_questions=["The scientific problem could not be converted into bounded queries."],
                telemetry=MethodResearchTelemetry(**telemetry),
                status=MethodResearchStatus.unavailable,
                reasons=reasons,
            )

        accumulator = _EvidenceAccumulator()
        for path in self.review_kb_paths:
            if not path.is_file():
                reason = f"local_kb_missing:{path}"
                _append_once(reasons, reason)
                continue
            for query in queries:
                telemetry["local_queries"] += 1
                try:
                    try:
                        result = query_kb(
                            path,
                            query.query_text,
                            top_k=self.local_top_k,
                            include_raw=True,
                        )
                    except TypeError:
                        # A narrow fake may expose the historical three-argument seam.
                        result = query_kb(path, query.query_text, top_k=self.local_top_k)
                except Exception as exc:
                    reason = f"local_query_error:{path}:{_safe_error(exc)}"
                    reasons.append(reason)
                    telemetry["errors"].append(reason)
                    continue
                self._consume_local_result(
                    result,
                    path=path,
                    query=query,
                    accumulator=accumulator,
                    telemetry=telemetry,
                    reasons=reasons,
                )

        use_online = self.online_enabled if online is None else bool(online)
        if use_online:
            client = self.online_client
            if client is None:
                try:
                    client = DefaultMethodResearchOnlineClient(
                        s2_request_budget_seconds=self.s2_request_budget_seconds
                    )
                    self.online_client = client
                except Exception as exc:
                    reason = f"online_client_unavailable:{_safe_error(exc)}"
                    reasons.append(reason)
                    telemetry["errors"].append(reason)
                    client = None
            if client is not None:
                self._consume_online_results(
                    client,
                    queries=queries,
                    accumulator=accumulator,
                    telemetry=telemetry,
                    reasons=reasons,
                    deadline=self.clock() + self.online_wall_time_seconds,
                )
        elif any(not self._local_query_is_sufficient(accumulator, query.query_id) for query in queries):
            _append_once(reasons, "online_disabled")

        evidence = accumulator.items
        telemetry["unique_evidence"] = len(evidence)
        findings = self._synthesize(
            evidence,
            queries,
            reasons=reasons,
            telemetry=telemetry,
        )

        unresolved: list[str] = []
        for query in queries:
            if not any(query.query_id in item.query_ids for item in evidence):
                unresolved.append(
                    f"No accepted literature evidence was found for {query.query_id}: {query.query_text}."
                )
            elif not any(
                query.query_id in item.query_ids
                and item.allowed_use in {MethodAllowedUse.method_guidance, MethodAllowedUse.direct_fact}
                for item in evidence
            ):
                unresolved.append(
                    f"No method-guidance evidence is available for {query.query_id}; abstract or metadata support is insufficient."
                )
        if evidence and not findings:
            _append_once(reasons, "no_reusable_method_pattern_found")
            unresolved.append(
                "Accepted evidence did not yield a bounded reusable method finding."
            )
        if not evidence:
            _append_once(reasons, "no_accepted_evidence")

        hard_reason = any(
            reason.startswith(
                (
                    "query_generation_error:",
                    "local_query_error:",
                    "synthesis_error:",
                )
            )
            or (
                reason.startswith("online_")
                and reason != "online_method_evidence_budget_satisfied"
            )
            for reason in reasons
        )
        if findings and evidence and not hard_reason:
            status = MethodResearchStatus.completed
        elif evidence or hard_reason:
            status = MethodResearchStatus.partial
        else:
            status = MethodResearchStatus.unavailable

        telemetry["elapsed_seconds"] = max(0.0, self.clock() - started)
        report = MethodResearchReport(
            problem_id=str(problem_id or _problem_id(problem)),
            queries=queries,
            evidence=evidence,
            method_findings=findings,
            unresolved_questions=_ordered_union(unresolved, []),
            telemetry=MethodResearchTelemetry(**telemetry),
            status=status,
            reasons=_ordered_union(reasons, []),
        )
        return report

    def research_methods(self, *args: Any, **kwargs: Any) -> MethodResearchReport:
        return self.research(*args, **kwargs)

    def run(self, *args: Any, **kwargs: Any) -> MethodResearchReport:
        return self.research(*args, **kwargs)

    def _consume_local_result(
        self,
        result: Any,
        *,
        path: Path,
        query: MethodResearchQuery,
        accumulator: _EvidenceAccumulator,
        telemetry: dict[str, Any],
        reasons: list[str],
    ) -> None:
        if not isinstance(result, Mapping):
            reasons.append(f"local_result_invalid:{path}")
            return
        for kind, route_kind in (
            ("papers", "paper"),
            ("text_chunks", "text_chunk"),
            ("visual_chunks", "visual_chunk"),
        ):
            rows = result.get(kind) or []
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                continue
            for row in rows:
                if not isinstance(row, (Mapping, MethodEvidence)) and not hasattr(row, "__dict__"):
                    continue
                if isinstance(row, Mapping) and row.get("error"):
                    reasons.append(f"local_record_error:{kind}:{str(row.get('error'))[:200]}")
                    continue
                telemetry["records_returned"] += 1
                candidate = _make_candidate(
                    row,
                    source_route="local_review_kb",
                    query_id=query.query_id,
                    local_path=str(path),
                    record_kind=route_kind,
                )
                if candidate is None:
                    continue
                section = str(
                    _first_value(
                        row,
                        ("section", "section_path", "section_title", "heading"),
                        "",
                    )
                    or ""
                )
                scope_matcher = (
                    _local_shallow_topic_match
                    if candidate.evidence.content_depth
                    in {MethodContentDepth.metadata, MethodContentDepth.abstract}
                    else _local_method_scope_match
                )
                if not scope_matcher(
                    query=query.query_text,
                    title=candidate.evidence.title,
                    section=section,
                    text=candidate.evidence.text,
                ):
                    telemetry["records_rejected_scope"] += 1
                    continue
                telemetry["records_accepted"] += 1
                _increment_route(telemetry["source_routes"], candidate.evidence.source_route)
                accumulator.add(candidate)

    def _consume_online_results(
        self,
        client: Any,
        *,
        queries: Sequence[MethodResearchQuery],
        accumulator: _EvidenceAccumulator,
        telemetry: dict[str, Any],
        reasons: list[str],
        deadline: float,
    ) -> None:
        for query_index, query in enumerate(queries, start=1):
            if self.clock() >= deadline:
                telemetry["online_queries_skipped_budget"] += len(queries) - query_index + 1
                telemetry["online_budget_exhausted"] = True
                _append_once(reasons, "online_method_research_time_budget_exhausted")
                break
            guidance_count = sum(
                1
                for item in accumulator.items
                if item.allowed_use
                in {MethodAllowedUse.method_guidance, MethodAllowedUse.direct_fact}
            )
            if (
                query_index > self.minimum_online_queries
                and guidance_count >= self.maximum_method_guidance_evidence
            ):
                telemetry["online_queries_skipped_budget"] += len(queries) - query_index + 1
                _append_once(reasons, "online_method_evidence_budget_satisfied")
                break
            if self._local_query_is_sufficient(accumulator, query.query_id):
                continue
            s2_records, s2_response = self._online_call(
                client,
                route="s2_search",
                query=query,
                telemetry=telemetry,
                reasons=reasons,
            )
            accepted_s2 = self._consume_online_records(
                s2_records,
                route="s2_search",
                query=query,
                accumulator=accumulator,
                telemetry=telemetry,
            )
            if (
                accepted_s2 >= self.min_local_evidence
                and self._local_query_is_sufficient(accumulator, query.query_id)
            ):
                continue
            # OpenAlex is complementary only after S2 produced no usable
            # evidence for this query.  It is never the first online route.
            del s2_response
            oa_records, _ = self._online_call(
                client,
                route="openalex_search",
                query=query,
                telemetry=telemetry,
                reasons=reasons,
            )
            self._consume_online_records(
                oa_records,
                route="openalex_search",
                query=query,
                accumulator=accumulator,
                telemetry=telemetry,
            )

    def _online_call(
        self,
        client: Any,
        *,
        route: str,
        query: MethodResearchQuery,
        telemetry: dict[str, Any],
        reasons: list[str],
    ) -> tuple[list[Any], Any]:
        if route == "s2_search":
            pass
        else:
            telemetry["openalex_calls"] += 1
        try:
            result = _invoke_search(client, route, query.query_text, self.online_limit)
            records, response = _result_records(result)
        except Exception as exc:
            reason = f"online_{route}_error:{_safe_error(exc)}"
            reasons.append(reason)
            telemetry["errors"].append(reason)
            return [], None

        telemetry["records_returned"] += len(records)
        provider_calls = _response_value(response, "provider_calls", None)
        if isinstance(provider_calls, Mapping):
            actual_s2_calls = sum(
                max(0, int(provider_calls.get(key, 0) or 0))
                for key in ("s2_search", "s2_batch", "s2_snippet_search")
            )
            telemetry["s2_calls"] += actual_s2_calls
            telemetry["s2_paper_search_calls"] += max(
                0, int(provider_calls.get("s2_search", 0) or 0)
            )
            telemetry["s2_batch_calls"] += max(
                0, int(provider_calls.get("s2_batch", 0) or 0)
            )
            telemetry["s2_snippet_calls"] += max(
                0, int(provider_calls.get("s2_snippet_search", 0) or 0)
            )
        elif route == "s2_search":
            # Injectable legacy clients expose one logical call without
            # provider-level telemetry.
            telemetry["s2_calls"] += 1
            telemetry["s2_paper_search_calls"] += 1
        quality_counts = _response_value(response, "quality_counts", None)
        if isinstance(quality_counts, Mapping):
            for key in (
                "s2_snippets_retrieved",
                "s2_snippets_accepted",
                "s2_snippets_rejected_scope",
            ):
                telemetry[key] += max(0, int(quality_counts.get(key, 0) or 0))
        cache_hit = _optional_bool(_response_value(response, "cache_hit", None))
        if cache_hit is not None:
            telemetry["cache_hits" if cache_hit else "cache_misses"] += 1
            key = f"{route}:{'hit' if cache_hit else 'miss'}"
            _increment_route(telemetry["cache_source_routes"], key)
        error = str(_response_value(response, "error", "") or "").strip()
        if error:
            reason = f"online_{route}_notice:{error[:250]}"
            reasons.append(reason)
            telemetry["errors"].append(reason)
        return records, response

    def _consume_online_records(
        self,
        records: Sequence[Any],
        *,
        route: str,
        query: MethodResearchQuery,
        accumulator: _EvidenceAccumulator,
        telemetry: dict[str, Any],
    ) -> int:
        accepted = 0
        for record in records:
            raw_depth = _first_value(
                record,
                ("content_depth",),
                _first_value(_raw_mapping(record), ("content_depth",), None),
            )
            depth = _normalize_depth(raw_depth)
            is_chunk = bool(_first_value(record, ("chunk_id", "text_chunk_id"), ""))
            record_kind = "text_chunk" if is_chunk or depth == MethodContentDepth.s2_snippet else "paper"
            source_route = "s2_snippet_search" if depth == MethodContentDepth.s2_snippet else route
            candidate = _make_candidate(
                record,
                source_route=source_route,
                query_id=query.query_id,
                record_kind=record_kind,
            )
            if candidate is None:
                continue
            if not _tmm_method_scope_match(
                query=query.query_text,
                title=candidate.evidence.title,
                section="",
                text=candidate.evidence.text,
            ):
                telemetry["records_rejected_scope"] += 1
                continue
            accepted += 1
            telemetry["records_accepted"] += 1
            _increment_route(telemetry["source_routes"], candidate.evidence.source_route)
            accumulator.add(candidate)
        return accepted

    def _local_query_is_sufficient(
        self, accumulator: _EvidenceAccumulator, query_id: str
    ) -> bool:
        matched = [item for item in accumulator.items if query_id in item.query_ids]
        if self.require_method_guidance:
            matched = [
                item
                for item in matched
                if item.allowed_use in {MethodAllowedUse.method_guidance, MethodAllowedUse.direct_fact}
            ]
            local_matched = [
                item
                for item in matched
                if "local_review_kb" in item.source_route.split("|")
            ]
            online_matched = [
                item
                for item in matched
                if "local_review_kb" not in item.source_route.split("|")
            ]
            if (
                local_matched
                and not online_matched
                and len({item.paper_id for item in local_matched})
                < self.minimum_local_method_papers
            ):
                return False
        return len(matched) >= self.min_local_evidence

    def _synthesize(
        self,
        evidence: Sequence[MethodEvidence],
        queries: Sequence[MethodResearchQuery],
        *,
        reasons: list[str],
        telemetry: dict[str, Any],
    ) -> list[MethodFinding]:
        if self.synthesis_callback is None:
            raw_findings: Any = synthesize_method_findings(evidence, queries)
        else:
            try:
                try:
                    raw_findings = self.synthesis_callback(evidence, queries)
                except TypeError:
                    raw_findings = self.synthesis_callback(evidence)
            except Exception as exc:
                reason = f"synthesis_error:{_safe_error(exc)}"
                reasons.append(reason)
                telemetry["errors"].append(reason)
                raw_findings = synthesize_method_findings(evidence, queries)
        drain_usage = getattr(self.synthesis_callback, "drain_usage", None)
        if callable(drain_usage):
            telemetry["usage"].extend(
                dict(item) for item in drain_usage() if isinstance(item, Mapping)
            )
        if isinstance(raw_findings, Mapping):
            raw_findings = raw_findings.get("method_findings", raw_findings.get("findings", []))
        if isinstance(raw_findings, MethodFinding):
            raw_findings = [raw_findings]
        if raw_findings is None or isinstance(raw_findings, (str, bytes)):
            raw_findings = []
        valid_ids = {item.evidence_id for item in evidence}
        findings: list[MethodFinding] = []
        for raw in list(raw_findings):
            try:
                finding = raw if isinstance(raw, MethodFinding) else MethodFinding.model_validate(raw)
            except (ValidationError, TypeError, ValueError) as exc:
                reason = f"synthesis_finding_invalid:{_safe_error(exc)}"
                reasons.append(reason)
                telemetry["errors"].append(reason)
                continue
            unknown = [item for item in finding.evidence_ids if item not in valid_ids]
            if unknown:
                reason = "discarded_fabricated_evidence_ids:" + ",".join(unknown)
                reasons.append(reason)
                if not valid_ids:
                    continue
                finding = finding.model_copy(
                    update={
                        "evidence_ids": [item for item in finding.evidence_ids if item in valid_ids]
                    }
                )
            if not finding.evidence_ids:
                continue
            findings.append(finding)
            if len(findings) >= 8:
                break
        return findings


def _increment_route(routes: dict[str, int], route: str) -> None:
    for item in _ordered_union(str(route).split("|"), []):
        routes[item] = routes.get(item, 0) + 1


def _append_once(values: list[str], item: str) -> None:
    if item not in values:
        values.append(item)


def _problem_id(problem: Any) -> str:
    try:
        payload = _as_problem_mapping(problem)
    except Exception:
        return "tmm-method-problem"
    value = payload.get("problem_id", payload.get("id", "tmm-method-problem"))
    return str(value or "tmm-method-problem").strip() or "tmm-method-problem"


MethodResearchAdapter = TMMMethodResearchAdapter
TMMMethodResearcher = TMMMethodResearchAdapter
MethodResearchClient = MethodResearchOnlineClient


def research_tmm_methods(
    problem: Mapping[str, Any] | Any,
    *,
    explicit_queries: Sequence[str | MethodResearchQuery | Mapping[str, Any]] | None = None,
    review_kb_paths: Sequence[str | Path] | str | Path | None = None,
    online_client: MethodResearchOnlineClient | Any | None = None,
    online: bool | None = None,
    synthesis_callback: Callable[..., Any] | None = None,
    **adapter_options: Any,
) -> MethodResearchReport:
    """Convenience entry point for a future research-design loop."""

    adapter = TMMMethodResearchAdapter(
        review_kb_paths,
        online_client=online_client,
        online_enabled=online,
        synthesis_callback=synthesis_callback,
        **adapter_options,
    )
    return adapter.research(problem, explicit_queries)


__all__ = [
    "DefaultMethodResearchOnlineClient",
    "MethodAllowedUse",
    "MethodContentDepth",
    "MethodEvidence",
    "MethodEvidenceDepth",
    "MethodEvidenceUse",
    "MethodFinding",
    "MethodOnlineSearchResult",
    "MethodPurpose",
    "MethodResearchAdapter",
    "MethodResearchClient",
    "MethodResearchOnlineClient",
    "MethodResearchReport",
    "MethodResearchPurpose",
    "MethodResearchQuery",
    "MethodResearchStatus",
    "MethodResearchTelemetry",
    "OnlineLiteratureClient",
    "TMMMethodResearchAdapter",
    "TMMMethodResearcher",
    "QwenMethodFindingSynthesizer",
    "DEFAULT_METHOD_SYNTHESIS_PROMPT",
    "build_method_research_queries",
    "discover_review_kb_paths",
    "deterministic_method_finding_synthesizer",
    "generate_method_research_queries",
    "query_kb",
    "research_tmm_methods",
    "synthesize_method_findings",
]
