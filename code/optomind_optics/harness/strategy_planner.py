"""Literature-grounded strategy planning for the TMM research harness.

This module never runs a solver and never certifies physics.  Its only job is
to turn a normalized optical problem plus traceable method evidence into a
small portfolio of executable research routes.  The routes are subsequently
compiled into immutable :class:`OpticalDesignTask` contracts and judged by the
deterministic TMM runtime.
"""

from __future__ import annotations

import json
import difflib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Mapping, Protocol, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

from config.qwen_config import get_cost_tracker

from .problem_analyzer import ArticlePlusQwenClient, validate_research_charter
from .qwen_policy import QWEN_POLICY_MODEL
from .text_safety import (
    normalize_text_sequence,
    normalize_text_sequence_list,
    repair_scientific_payload,
)


DEFAULT_STRATEGY_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "TMM Research Strategy Planner.txt"
)

# ---------------------------------------------------------------------------
# Article branch additions (T-05)
# ---------------------------------------------------------------------------

# Strategy planning is a planning-class task: route through the plus tier.
ARTICLE_STRATEGY_PLANNER_MODEL = "qwen3.5-plus"

# R-01: the repair attempt now carries method_research and prior_iterations, so
# the prompt is materially longer than the first attempt's. A 5000-token ceiling
# truncated multi-route plans mid-JSON, which surfaced as a validation error and
# burned the only retry on a defect the model never actually made.
PLANNER_MAX_TOKENS = 8000

_ALLOWED_PLANNER_MODELS = frozenset(
    {QWEN_POLICY_MODEL, ARTICLE_STRATEGY_PLANNER_MODEL}
)


class StrategyPlannerClient(Protocol):
    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> Dict[str, Any]: ...


# Every DesignRoute field declared as Tuple[str, ...]. A model that writes one
# of these as a bare string instead of an array used to be rejected outright
# (pydantic: tuple_type), which is how R-09 stage 1 burned two real API calls on
# `manufacturing_considerations` and `theory_basis`. The shape is repaired at the
# contract boundary instead, so a well-formed sentence in the wrong container no
# longer costs a run. Restated here rather than derived at import time because
# the class cannot introspect itself inside its own body; test_strategy_planner
# locks this set against the live annotations, so adding a Tuple[str, ...] field
# without adding it here fails the suite.
_ROUTE_TEXT_SEQUENCE_FIELDS: frozenset[str] = frozenset(
    {
        "proposed_materials",
        "design_variables",
        "soft_objectives",
        "manufacturing_considerations",
        "evidence_ids",
        "theory_basis",
        "expected_advantages",
        "known_risks",
    }
)


class DesignRoute(BaseModel):
    """One independently executable TMM research route."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str
    title: str
    route_kind: Literal[
        "analyze_known_stack",
        "optimize_existing_stack",
        "periodic_stack",
        "defect_cavity",
        "chirped_stack",
        "absorber_emitter",
        "mixed_coherence_stack",
        "custom_layered_stack",
    ]
    scientific_hypothesis: str
    design_principle: str
    proposed_materials: Tuple[str, ...] = ()
    proposed_topology: str
    design_variables: Tuple[str, ...] = ()
    soft_objectives: Tuple[str, ...] = ()
    manufacturing_considerations: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()
    theory_basis: Tuple[str, ...] = ()
    expected_advantages: Tuple[str, ...] = ()
    known_risks: Tuple[str, ...] = ()
    execution_request_english: str
    priority: int = 1
    parent_route_id: str | None = None
    revision_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _repair_scientific_controls(cls, value: Any) -> Any:
        value = repair_scientific_payload(value)
        # Shape repair for the "list of statements" fields. Runs BEFORE tuple
        # coercion so a bare string becomes one statement instead of a
        # tuple_type rejection; see _ROUTE_TEXT_SEQUENCE_FIELDS.
        if isinstance(value, Mapping):
            repaired = dict(value)
            for field_name in _ROUTE_TEXT_SEQUENCE_FIELDS:
                if field_name in repaired:
                    repaired[field_name] = normalize_text_sequence(
                        repaired[field_name]
                    )
            return repaired
        return value

    @field_validator("route_id")
    @classmethod
    def _safe_route_id(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value or not value.replace("_", "").replace("-", "").isalnum():
            raise ValueError("route_id must be a stable alphanumeric identifier")
        return value

    @field_validator("priority")
    @classmethod
    def _bounded_priority(cls, value: int) -> int:
        if not 1 <= int(value) <= 100:
            raise ValueError("priority must be in [1, 100]")
        return int(value)

    @model_validator(mode="after")
    def _complete_route(self) -> "DesignRoute":
        for field_name in (
            "title",
            "scientific_hypothesis",
            "design_principle",
            "proposed_topology",
            "execution_request_english",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"{field_name} must not be empty")
        encoded = self.model_dump_json()
        if any("\u4e00" <= char <= "\u9fff" for char in encoded):
            raise ValueError("strategy routes and intermediate messages must be English")
        return self


class StrategyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    problem_id: str
    planning_summary: str
    routes: Tuple[DesignRoute, ...]
    research_influence: Tuple[str, ...] = ()
    unresolved_decisions: Tuple[str, ...] = ()
    stop_if_all_routes_fail: str

    @model_validator(mode="before")
    @classmethod
    def _repair_scientific_controls(cls, value: Any) -> Any:
        return repair_scientific_payload(value)

    @model_validator(mode="after")
    def _bounded_routes(self) -> "StrategyPlan":
        if not 1 <= len(self.routes) <= 4:
            raise ValueError("a strategy plan must contain one to four routes")
        ids = [route.route_id for route in self.routes]
        if len(ids) != len(set(ids)):
            raise ValueError("route_id values must be unique")
        return self


class StrategyPlanningResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["planned", "invalid", "unavailable"]
    plan: StrategyPlan | None = None
    attempts: int = 0
    validation_errors: Tuple[str, ...] = ()
    normalization_warnings: Tuple[str, ...] = ()
    usage: Tuple[Dict[str, Any], ...] = ()
    model_name: Literal["qwen3.5-plus", "qwen3.7-flash"] = ARTICLE_STRATEGY_PLANNER_MODEL

    # R-04: sidecar mapping of route_id -> {expected_observations, stop_conditions}
    # populated before DesignRoute validation to avoid extra="forbid"
    # Use PrivateAttr to keep it out of model_dump() and hash contract
    _pre_declarations: Dict[str, Dict[str, List[str]]] = PrivateAttr(default_factory=dict)

    # The rejected plan text, kept on the invalid path only.  Without it a
    # failed replan leaves nothing but the validator's one-line message, which
    # cannot distinguish a model that broke a rule from a guard that misread
    # compliant prose -- and those two call for opposite repairs.  It is a
    # PrivateAttr for the same reason ``_pre_declarations`` is: this result
    # participates in a content-addressed identity chain, so a new dumped
    # field would invalidate every persisted pipeline digest.  The caller that
    # writes the failure artifact records it explicitly instead.
    _rejected_plan: str = PrivateAttr(default="")

    @property
    def rejected_plan(self) -> str:
        return self._rejected_plan

    @property
    def pre_declarations(self) -> Mapping[str, Mapping[str, Sequence[str]]]:
        return self._pre_declarations


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
                pass
    return {}


def _as_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError("planner inputs must be mappings or Pydantic models")


def _evidence_ids(report: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("evidence_id") or "")
        for item in report.get("evidence", [])
        if isinstance(item, Mapping) and item.get("evidence_id")
    }


def _compact_method_research(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep planning evidence useful without replaying an entire KB payload."""

    findings = [
        dict(item)
        for item in report.get("method_findings", []) or []
        if isinstance(item, Mapping)
    ][:8]
    linked_ids = {
        str(evidence_id)
        for finding in findings
        for evidence_id in finding.get("evidence_ids", []) or []
        if str(evidence_id).strip()
    }
    evidence_rows = [
        dict(item)
        for item in report.get("evidence", []) or []
        if isinstance(item, Mapping) and item.get("evidence_id")
    ]
    evidence_rows.sort(
        key=lambda item: (
            0 if str(item.get("evidence_id")) in linked_ids else 1,
            {
                "direct_fact": 0,
                "method_guidance": 1,
                "background": 2,
                "discovery": 3,
            }.get(str(item.get("allowed_use") or ""), 4),
            str(item.get("evidence_id") or ""),
        )
    )
    compact_evidence: list[dict[str, Any]] = []
    for item in evidence_rows[:16]:
        compact_evidence.append(
            {
                key: item.get(key)
                for key in (
                    "evidence_id",
                    "paper_id",
                    "title",
                    "doi",
                    "year",
                    "source_route",
                    "content_depth",
                    "allowed_use",
                    "query_ids",
                )
            }
            | {"text": str(item.get("text") or "")[:1200]}
        )
    return {
        "status": report.get("status"),
        "queries": [
            dict(item)
            for item in report.get("queries", []) or []
            if isinstance(item, Mapping)
        ][:8],
        "evidence": compact_evidence,
        "method_findings": findings,
        "unresolved_questions": [
            str(item) for item in report.get("unresolved_questions", []) or []
        ][:8],
    }


def _compact_prior_iterations(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for raw in list(rows)[-6:]:
        item = dict(raw)
        compact.append(
            {
                "iteration_id": item.get("iteration_id"),
                "route_id": item.get("route_id"),
                "route_title": item.get("route_title"),
                "compilation_status": item.get("compilation_status"),
                "compilation_rationale": item.get("compilation_rationale"),
                "compilation_errors": list(item.get("compilation_errors") or [])[:6],
                "run_status": item.get("run_status"),
                "physically_valid_candidate_count": item.get(
                    "physically_valid_candidate_count", 0
                ),
                "best_target_score": item.get("best_target_score"),
                "best_robustness_score": item.get("best_robustness_score"),
                "failure_categories": list(item.get("failure_categories") or []),
                "candidate_summaries": [
                    {
                        key: candidate.get(key)
                        for key in (
                            "candidate_id",
                            "target_score",
                            "robustness_score",
                            "simplicity_score",
                            "thicknesses_nm",
                            "optimizer_id",
                        )
                    }
                    for candidate in item.get("candidate_summaries", []) or []
                    if isinstance(candidate, Mapping)
                ][:4],
            }
        )
    return compact


def _validate_evidence_links(plan: StrategyPlan, allowed_ids: set[str]) -> tuple[str, ...]:
    errors: list[str] = []
    for route in plan.routes:
        unknown = sorted(set(route.evidence_ids) - allowed_ids)
        if unknown:
            errors.append(
                f"route {route.route_id} references unknown evidence ids: {', '.join(unknown)}"
            )
        if not route.evidence_ids and not route.theory_basis:
            errors.append(
                f"route {route.route_id} requires traceable evidence or an explicit theory basis"
            )
    return tuple(errors)


def _repair_near_match_evidence_ids(
    plan: StrategyPlan,
    allowed_ids: set[str],
) -> tuple[StrategyPlan, tuple[str, ...]]:
    """Correct only unique, near-exact copies of already supplied IDs."""

    warnings: list[str] = []
    routes: list[DesignRoute] = []
    ordered_allowed = sorted(allowed_ids)
    for route in plan.routes:
        repaired: list[str] = []
        for evidence_id in route.evidence_ids:
            if evidence_id in allowed_ids:
                repaired.append(evidence_id)
                continue
            matches = difflib.get_close_matches(
                evidence_id,
                ordered_allowed,
                n=2,
                cutoff=0.97,
            )
            if len(matches) == 1:
                repaired.append(matches[0])
                warnings.append(
                    f"route {route.route_id} evidence id was corrected to its unique "
                    "near-exact allowlist match"
                )
            else:
                repaired.append(evidence_id)
        routes.append(
            route.model_copy(update={"evidence_ids": tuple(dict.fromkeys(repaired))})
        )
    return plan.model_copy(update={"routes": tuple(routes)}), tuple(warnings)


_EXPLICIT_TARGET_CLAUSE_RE = re.compile(
    r"\b((?:mean|average|worst[- ]case|peak|maximum|minimum)?\s*"
    r"(?:reflectance|reflection|transmittance|transmission|absorptance|"
    r"absorption)\b[^.;\n]{0,80}?"
    r"(?:at\s+or\s+below|at\s+most|no\s+more\s+than|below|under|"
    r"at\s+or\s+above|at\s+least|no\s+less\s+than|above|>=|<=|≥|≤)\s*"
    r"\d+(?:\.\d+)?\s*(?:%|percent)\b)",
    re.IGNORECASE,
)
_UNCERTAINTY_CLAUSE_RE = re.compile(
    r"\b(?:tolerance|uncertainty|error|perturbation|offset|deviation|"
    r"one[- ]sigma)\b",
    re.IGNORECASE,
)
_THICKNESS_UNCERTAINTY_WORDS_RE = re.compile(
    r"\b(?:thickness|layer[- ]thickness|film[- ]thickness|coating[- ]thickness|"
    r"fabricat\w*|manufactur\w*)\b",
    re.IGNORECASE,
)
_THICKNESS_UNCERTAINTY_MARKERS_RE = re.compile(
    r"\b(?:error|uncertainty|tolerance|sigma|standard deviation|deviation|"
    r"perturbation|variation|one[- ]sigma)\b|\+/-|±",
    re.IGNORECASE,
)
_THICKNESS_UNCERTAINTY_VALUE_RE = re.compile(
    r"\b(?:normal|gaussian|uniform|bounded|sigma|standard deviation)\b|"
    r"\d+(?:\.\d+)?\s*(?:%|percent\b|nm\b)|\+/-|±|plus\s+or\s+minus",
    re.IGNORECASE,
)
_ANGLE_UNCERTAINTY_PHRASE_RE = re.compile(
    r"\b(?:a\s+)?common\s+incidence[- ]angle\b|\bincidence[- ]angle\b|"
    r"\bangle\s+(?:offset|perturbation|error|uncertainty|tolerance|deviation)\b",
    re.IGNORECASE,
)
_ANGLE_UNCERTAINTY_VALUE_RE = re.compile(
    r"\b(?:\+/-|±|plus\s+or\s+minus|bounded\s+by)\b|"
    r"\d+(?:\.\d+)?\s*(?:degrees?|deg\b|°)",
    re.IGNORECASE,
)
_THICKNESS_UNCERTAINTY_START_RE = re.compile(
    r"\b(?:independent\s+)?(?:normally\s+distributed|"
    r"uniform(?:ly)?\s+(?:distributed|bounded)|bounded\s+by|"
    r"one[- ]sigma|1[- ]sigma|sigma|standard\s+deviation|"
    r"(?:layer[- ]thickness|thickness|film[- ]thickness|fabricat\w*|manufactur\w*)\s+"
    r"(?:errors?|error|uncertainty|tolerance|deviation|perturbation|variation))\b",
    re.IGNORECASE,
)
_MAX_EXPLICIT_TARGETS = 12
_MAX_EXPLICIT_TARGET_CLAUSE_CHARS = 160
_MAX_EXPLICIT_UNCERTAINTY_CLAUSES = 6
_MAX_EXPLICIT_UNCERTAINTY_CLAUSE_CHARS = 260
_MAX_EXPLICIT_UNCERTAINTY_SPAN_CHARS = 170


def _explicit_performance_target_clauses(
    problem: Mapping[str, Any],
) -> list[str]:
    """Preserve exact explicit user optical percentage-target clauses.

    ``original_request`` is the immutable source of explicit user controls;
    ``normalized_request_english`` is used only when the original carries no
    percentage targets.  The extracted clauses are the verbatim source text
    (whitespace-normalized), deduplicated and bounded.  They are soft ranking
    objectives for the task compiler, never physical admission gates.
    """

    candidates: list[str] = []
    for source_name in ("original_request", "normalized_request_english"):
        source = problem.get(source_name)
        if not isinstance(source, str) or not source.strip():
            continue
        for sentence in re.split(
            r"[;\n]+|(?<=[^\d])\.(?=\s|$)", source
        ):
            for match in _EXPLICIT_TARGET_CLAUSE_RE.finditer(sentence):
                clause = match.group(1)
                # Match-local filtering only: an uncertainty/tolerance phrase
                # elsewhere in the sentence must not erase a later explicit
                # optical target, while a clause that itself is about a
                # tolerance/error is not a nominal optical target.
                if _UNCERTAINTY_CLAUSE_RE.search(clause):
                    continue
                if len(clause) > _MAX_EXPLICIT_TARGET_CLAUSE_CHARS:
                    continue
                candidates.append(clause)
        if candidates:
            break
    seen: set[str] = set()
    result: list[str] = []
    for clause in candidates:
        key = re.sub(r"\s+", " ", clause).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(re.sub(r"\s+", " ", clause).strip())
        if len(result) >= _MAX_EXPLICIT_TARGETS:
            break
    return result


def _explicit_uncertainty_clauses(problem: Mapping[str, Any]) -> list[str]:
    """Return verbatim user-authored uncertainty clauses from the request.

    ``original_request`` is the immutable source of deterministic uncertainty
    controls.  Model-authored assumptions are advisory prose and are never
    treated as scientific authority here.
    """

    source = problem.get("original_request")
    if not isinstance(source, str) or not source.strip():
        return []
    candidates: list[str] = []
    for sentence in re.split(
        r"[;\n]+|(?<=[^\d])\.(?=\s|$)", source
    ):
        sentence = re.sub(r"\s+", " ", str(sentence or "")).strip()
        if not sentence:
            continue
        thickness_uncertainty = bool(
            _THICKNESS_UNCERTAINTY_WORDS_RE.search(sentence)
            and _THICKNESS_UNCERTAINTY_MARKERS_RE.search(sentence)
            and _THICKNESS_UNCERTAINTY_VALUE_RE.search(sentence)
        )
        angle_uncertainty = bool(
            _ANGLE_UNCERTAINTY_PHRASE_RE.search(sentence)
            and _ANGLE_UNCERTAINTY_VALUE_RE.search(sentence)
        )
        if not (thickness_uncertainty or angle_uncertainty):
            continue
        if len(sentence) > _MAX_EXPLICIT_UNCERTAINTY_CLAUSE_CHARS:
            candidates.extend(_bounded_uncertainty_spans(sentence))
        else:
            candidates.append(sentence)
    seen: set[str] = set()
    result: list[str] = []
    for clause in candidates:
        key = re.sub(r"\s+", " ", clause).casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(clause)
        if len(result) >= _MAX_EXPLICIT_UNCERTAINTY_CLAUSES:
            break
    return result


def _trim_uncertainty_span(span: str) -> str:
    """Whitespace-normalize and strip connectors from one uncertainty span."""

    cleaned = re.sub(r"\s+", " ", span).strip()
    cleaned = re.sub(
        r"^(?:evaluate|compute|analyze|perform|assess|consider|verify|apply|run)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"^a\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(
        r"\s+(?:together\s+with|and|but|while|if|then)\b.*$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip(" ,;:-")


def _bounded_uncertainty_spans(sentence: str) -> list[str]:
    """Slice one long sentence into bounded local uncertainty spans.

    A compact canonical contract must never copy an entire long prose
    sentence.  Each span starts at the uncertainty expression and ends at its
    magnitude/unit (or at the angle boundary), preserving distribution
    semantics, thickness magnitude/unit, and angle magnitude when present.
    """

    spans: list[str] = []
    angle_match = _ANGLE_UNCERTAINTY_PHRASE_RE.search(sentence)
    angle_boundary = angle_match.start() if angle_match is not None else None
    start_match = _THICKNESS_UNCERTAINTY_START_RE.search(sentence)
    if start_match is not None:
        start = start_match.start()
        end = (
            angle_boundary
            if angle_boundary is not None
            else len(sentence)
        )
        end = min(end, start + _MAX_EXPLICIT_UNCERTAINTY_SPAN_CHARS)
        span = sentence[start:end]
        value_end = max(
            (
                match.end()
                for match in _THICKNESS_UNCERTAINTY_VALUE_RE.finditer(span)
            ),
            default=None,
        )
        if value_end is not None:
            span = span[:value_end]
        span = _trim_uncertainty_span(span)
        if (
            span
            and _THICKNESS_UNCERTAINTY_WORDS_RE.search(span)
            and _THICKNESS_UNCERTAINTY_VALUE_RE.search(span)
        ):
            spans.append(span)
    if angle_boundary is not None:
        tail = sentence[angle_boundary:]
        degree_match = re.search(
            r"\d+(?:\.\d+)?\s*(?:degrees?|deg\b|°)",
            tail,
            re.IGNORECASE,
        )
        end = (
            degree_match.end()
            if degree_match is not None
            else min(len(tail), 120)
        )
        span = _trim_uncertainty_span(tail[:end])
        if span and _ANGLE_UNCERTAINTY_VALUE_RE.search(span):
            spans.append(span)
    return spans


def _attach_canonical_execution_controls(
    plan: StrategyPlan,
    problem: Mapping[str, Any],
) -> tuple[StrategyPlan, tuple[str, ...]]:
    """Make every route standalone using only controls from the user contract."""

    controls: list[str] = []
    angles = [float(item) for item in problem.get("angles_deg", []) or []]
    if angles:
        rendered_angles = ", ".join(f"{item:g} degrees" for item in angles)
        controls.append(f"incidence angles {rendered_angles}")
    polarizations = [
        str(item).strip()
        for item in problem.get("polarizations", []) or []
        if str(item).strip()
    ]
    if polarizations:
        controls.append("polarization " + ", ".join(polarizations))
    intervals = [
        tuple(float(value) for value in item)
        for item in problem.get("wavelengths_nm", []) or []
        if isinstance(item, (list, tuple)) and len(item) == 2
    ]
    if intervals:
        controls.append(
            "wavelength intervals "
            + ", ".join(f"{lo:g}-{hi:g} nm" for lo, hi in intervals)
        )
    preferred_behaviors = [
        str(item).strip()
        for item in problem.get("preferred_behaviors", []) or []
        if str(item).strip()
    ]
    explicit_targets = _explicit_performance_target_clauses(problem)
    soft_objectives: list[str] = []
    seen_soft: set[str] = set()
    for clause in explicit_targets + preferred_behaviors:
        key = re.sub(r"\s+", " ", clause).casefold()
        if key in seen_soft:
            continue
        seen_soft.add(key)
        soft_objectives.append(re.sub(r"\s+", " ", clause).strip())
    if soft_objectives:
        controls.append("soft objectives: " + "; ".join(soft_objectives))
    suppressed_behaviors = [
        str(item).strip()
        for item in problem.get("suppressed_behaviors", []) or []
        if str(item).strip()
    ]
    if suppressed_behaviors:
        # A suppressed behaviour is a direction to avoid, not another desired
        # objective.  Keeping the two lists separate prevents phrases such as
        # ``high reflectance`` from being appended as a positive target.
        controls.append(
            "behaviors to suppress or avoid: " + "; ".join(suppressed_behaviors)
        )
    manufacturing = [
        str(item).strip()
        for item in problem.get("manufacturing_constraints", []) or []
        if str(item).strip()
    ]
    uncertainty = _explicit_uncertainty_clauses(problem)
    uncertainty_keys = {
        re.sub(r"\s+", " ", clause).casefold() for clause in uncertainty
    }
    manufacturing = [
        item
        for item in manufacturing
        if re.sub(r"\s+", " ", item).casefold() not in uncertainty_keys
    ]
    if manufacturing:
        controls.append("manufacturing constraints: " + "; ".join(manufacturing))
    if uncertainty:
        controls.append("uncertainty conditions: " + "; ".join(uncertainty))
    if not controls:
        return plan, ()

    canonical = "Canonical user controls: " + ". ".join(controls) + "."
    routes: list[DesignRoute] = []
    warnings: list[str] = []
    for route in plan.routes:
        request = route.execution_request_english.rstrip()
        if canonical.casefold() not in request.casefold():
            request = f"{request} {canonical}"
            warnings.append(
                f"route {route.route_id} received canonical user controls from the "
                "problem contract"
            )
        routes.append(route.model_copy(update={"execution_request_english": request}))
    return plan.model_copy(update={"routes": tuple(routes)}), tuple(warnings)


_SYMMETRIC_PAIR_RANGE_RE = re.compile(
    r"\bN[_ ]?H\s*=\s*N[_ ]?L\s*=\s*(\d+)\s*[-–]\s*(\d+)\s*(?:pairs?)?\b",
    re.IGNORECASE,
)
_GENERIC_PAIR_RANGE_RE = re.compile(
    r"\b(\d+)\s*[-–]\s*(\d+)\s+pairs?\b",
    re.IGNORECASE,
)


def _fix_discrete_pair_ranges(
    plan: StrategyPlan,
) -> tuple[StrategyPlan, tuple[str, ...]]:
    """Instantiate one deterministic topology per route before compilation."""

    routes: list[DesignRoute] = []
    warnings: list[str] = []
    for route in plan.routes:
        request = route.execution_request_english

        def replace_symmetric(match: re.Match[str]) -> str:
            lo, hi = sorted((int(match.group(1)), int(match.group(2))))
            return f"N_H=N_L={(lo + hi) // 2} pairs"

        def replace_generic(match: re.Match[str]) -> str:
            lo, hi = sorted((int(match.group(1)), int(match.group(2))))
            return f"{(lo + hi) // 2} pairs"

        fixed = _SYMMETRIC_PAIR_RANGE_RE.sub(replace_symmetric, request)
        fixed = _GENERIC_PAIR_RANGE_RE.sub(replace_generic, fixed)
        if fixed != request:
            fixed += (
                " This route uses the fixed pair count above; do not optimize or "
                "vary the integer layer count inside this task."
            )
            warnings.append(
                f"route {route.route_id} pair-count range was instantiated as one fixed topology"
            )
        routes.append(route.model_copy(update={"execution_request_english": fixed}))
    return plan.model_copy(update={"routes": tuple(routes)}), tuple(warnings)


_DEFAULT_ROUTE_PAIR_COUNTS = {
    "periodic_stack": 8,
    "defect_cavity": 5,
    "chirped_stack": 8,
    "optimize_existing_stack": 6,
    "custom_layered_stack": 6,
}

_LAYER_COUNT_VARIABLE_RE = re.compile(
    r"\blayer[_ ]count\b[^\d]{0,40}(?:between\s+)?(\d{1,3})\s*"
    r"(?:to|-|\u2013|\u2014|and)\s*(\d{1,3})",
    re.IGNORECASE,
)


def _declared_layer_count_range(
    problem: Mapping[str, Any],
) -> tuple[int, int] | None:
    for value in problem.get("design_variables", []) or []:
        match = _LAYER_COUNT_VARIABLE_RE.search(str(value))
        if match:
            lo, hi = sorted((int(match.group(1)), int(match.group(2))))
            if 1 <= lo <= hi <= 200:
                return lo, hi
    return None


def _portfolio_layer_counts(lo: int, hi: int, count: int) -> list[int]:
    """Return deterministic, spread-out fixed counts within a requested range."""

    if count <= 1 or lo == hi:
        return [lo]
    # The runtime normally executes at most the first three initial routes.
    # Put both boundaries and one midpoint in those first slots, then use any
    # remaining routes for additional interior alternatives.
    primary_count = min(count, 3)
    values = [
        round(lo + index * (hi - lo) / (primary_count - 1))
        for index in range(primary_count)
    ]
    if count > primary_count:
        unused = [value for value in range(lo, hi + 1) if value not in values]
        unused.sort(key=lambda value: (abs(value - (lo + hi) / 2), value))
        values.extend((unused or values)[index % max(len(unused), 1)] for index in range(count - primary_count))
    # Rounding can collapse adjacent values in narrow ranges.  Keep every
    # value legal and monotonic; duplicate counts are allowed only when the
    # user supplied fewer distinct integers than requested routes.
    return [max(lo, min(hi, int(value))) for value in values]


_LAYER_WORD_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"\d{1,3})[-\s]layers?\b",
    re.IGNORECASE,
)


def _synchronize_layer_count_language(value: str, layer_count: int) -> str:
    """Remove stale model-authored layer counts after topology assignment."""

    text = str(value or "")
    text = _LAYER_WORD_RE.sub(f"{layer_count}-layer", text)
    text = re.sub(
        r"\bbilayer\b",
        f"{layer_count}-layer stack",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bfixed\s+at\s+\d{1,3}\b",
        f"fixed at {layer_count}",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\(\s*\d{1,3}\s+layers?\s*\)",
        f"({layer_count} layers)",
        text,
        flags=re.IGNORECASE,
    )
    return text


def _enforce_requested_layer_count_portfolio(
    plan: StrategyPlan,
    problem: Mapping[str, Any],
) -> tuple[StrategyPlan, tuple[str, ...]]:
    """Turn a user layer-count range into several fixed executable routes.

    The language model chooses scientific families and materials.  This
    deterministic bridge owns the integer experiment design so the compiler
    receives one immutable topology per route and cannot silently move outside
    the user's search space.
    """

    requested = _declared_layer_count_range(problem)
    if requested is None:
        return plan, ()
    lo, hi = requested
    counts = _portfolio_layer_counts(lo, hi, len(plan.routes))
    known_media = "; ".join(
        str(item).strip()
        for item in problem.get("known_stack_materials", []) or []
        if str(item).strip()
    )
    routes: list[DesignRoute] = []
    warnings: list[str] = []
    for route, layer_count in zip(plan.routes, counts):
        materials = [
            str(item).strip()
            for item in route.proposed_materials
            if str(item).strip()
        ]
        if len(materials) >= 2:
            sequence = " / ".join(
                materials[index % len(materials)] for index in range(layer_count)
            )
            topology = (
                f"exactly {layer_count} finite layers from the incident side: "
                f"{sequence}"
            )
        else:
            topology = (
                f"exactly {layer_count} explicitly expanded finite layers using "
                "the proposed material family"
            )
        material_label = "/".join(materials) if materials else "declared materials"
        fixed_title = (
            f"Fixed {layer_count}-layer {route.route_kind.replace('_', ' ')} "
            f"route ({material_label})"
        )
        request_parts = [
            f"Perform one bounded TMM thickness-optimization route for {fixed_title}.",
            f"Use {topology}.",
            "Optimize every finite layer physical thickness with finite positive bounds.",
            "Evaluate the wavelength, incidence-angle, polarization, soft-objective, and uncertainty controls supplied below.",
            "Report verified optical metrics, total thickness, and manufacturing robustness; targets rank candidates and never gate physical validity.",
        ]
        if known_media:
            request_parts.insert(1, f"Use these declared media constraints: {known_media}.")
        routes.append(
            route.model_copy(
                update={
                    "title": fixed_title,
                    "scientific_hypothesis": (
                        f"A fixed {layer_count}-layer {material_label} stack tests "
                        "whether the proposed optical mechanism can balance the "
                        "shared spectral targets, total thickness, and manufacturing "
                        "robustness."
                    ),
                    "design_principle": _synchronize_layer_count_language(
                        route.design_principle, layer_count
                    ),
                    "proposed_topology": topology,
                    "design_variables": tuple(
                        f"Physical thickness of {material} layer {index + 1}"
                        for index, material in enumerate(
                            [
                                materials[index % len(materials)]
                                if materials
                                else "declared material"
                                for index in range(layer_count)
                            ]
                        )
                    ),
                    "soft_objectives": tuple(
                        _synchronize_layer_count_language(value, layer_count)
                        for value in route.soft_objectives
                    ),
                    "theory_basis": tuple(
                        _synchronize_layer_count_language(value, layer_count)
                        for value in route.theory_basis
                    ),
                    "expected_advantages": tuple(
                        _synchronize_layer_count_language(value, layer_count)
                        for value in route.expected_advantages
                    ),
                    "known_risks": tuple(
                        _synchronize_layer_count_language(value, layer_count)
                        for value in route.known_risks
                    ),
                    "execution_request_english": " ".join(request_parts),
                }
            )
        )
        warnings.append(
            f"route {route.route_id} was assigned the fixed {layer_count}-layer "
            f"topology from the requested {lo}-{hi} layer portfolio"
        )
    return plan.model_copy(update={"routes": tuple(routes)}), tuple(warnings)


def _has_symbolic_topology(text: str) -> bool:
    return bool(
        re.search(r"\.\.\.|\u2026", text)
        or re.search(r"\[[^\]]+\]\s*[_^]?\s*[Nn]\b", text)
        or re.search(r"\bN\b(?!\s*=\s*\d+)", text)
    )


def _realize_symbolic_topologies(
    plan: StrategyPlan,
) -> tuple[StrategyPlan, tuple[str, ...]]:
    """Instantiate route-family placeholders before the task compiler.

    The strategy model selects a scientific family.  Integer topology is a
    reproducible program decision because the downstream optimizer only owns
    continuous thickness variables.  These defaults are starting hypotheses,
    never performance gates, and alternative families remain separate routes.
    """

    routes: list[DesignRoute] = []
    warnings: list[str] = []
    for route in plan.routes:
        combined = "\n".join(
            (
                route.title,
                route.scientific_hypothesis,
                route.design_principle,
                route.proposed_topology,
                route.execution_request_english,
            )
        )
        if not _has_symbolic_topology(combined):
            routes.append(route)
            continue

        materials = [item.strip() for item in route.proposed_materials if item.strip()]
        material_pair = (
            f"{materials[0]}/{materials[1]}"
            if len(materials) >= 2
            else "the two proposed alternating materials"
        )
        is_cavity = route.route_kind == "defect_cavity" or bool(
            re.search(r"\b(?:cavity|defect)\b", combined, re.IGNORECASE)
        )
        if is_cavity:
            pairs = 5
            layer_count = 4 * pairs + 1
            realized_topology = (
                f"exactly {pairs} alternating {material_pair} pairs, one cavity layer, "
                f"and exactly {pairs} reverse-order pairs ({layer_count} finite layers)"
            )
        else:
            pairs = _DEFAULT_ROUTE_PAIR_COUNTS.get(route.route_kind, 6)
            layer_count = 2 * pairs
            qualifier = "chirped " if route.route_kind == "chirped_stack" else ""
            realized_topology = (
                f"exactly {pairs} {qualifier}alternating {material_pair} pairs "
                f"({layer_count} finite layers)"
            )
        override = (
            "Topology realization override: disregard any symbolic repeat count, "
            f"range, or ellipsis above and use {realized_topology}. Optimize only "
            "continuous physical thicknesses; the task compiler must fully expand "
            "every finite layer."
        )
        routes.append(
            route.model_copy(
                update={
                    "proposed_topology": realized_topology,
                    "execution_request_english": (
                        f"{route.execution_request_english.rstrip()} {override}"
                    ),
                }
            )
        )
        warnings.append(
            f"route {route.route_id} symbolic topology was instantiated as a fixed "
            f"{layer_count}-layer starting hypothesis"
        )
    return plan.model_copy(update={"routes": tuple(routes)}), tuple(warnings)


def _route_layer_count(route: DesignRoute) -> int | None:
    """Recover one fixed finite-layer count from an otherwise valid route."""

    text = "\n".join(
        (
            route.title,
            route.proposed_topology,
            route.execution_request_english,
        )
    )
    for pattern in (
        r"\b(?:exactly\s+)?(\d{1,3})\s*[- ]layers?\b",
        r"\bfor\s+(?:an?\s+)?(\d{1,3})\s*[- ]layer\b",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    labels = [int(item) for item in re.findall(r"\bLayer\s*(\d{1,3})\b", text, re.I)]
    return max(labels) if labels else None


def _material_pair_portfolio(
    materials: list[str],
    *,
    center_wavelength_nm: float,
) -> list[tuple[str, str]]:
    """Rank allowed dielectrics and return bounded low/high-index hypotheses."""

    ranked: list[tuple[float, str]] = []
    try:
        from tmm_engine import MaterialRegistry

        registry = MaterialRegistry()
        for material in materials:
            try:
                sampled = registry.sample(
                    material,
                    [float(center_wavelength_nm) / 1000.0],
                    allow_extrapolation=False,
                )
                ranked.append((float(sampled.n[0]), material))
            except Exception:
                continue
    except Exception:
        ranked = []
    if len(ranked) < 2:
        ranked = [(float(index), material) for index, material in enumerate(materials)]
    ranked.sort(key=lambda item: (item[0], item[1].casefold()))
    ordered = [item[1] for item in ranked]
    hypotheses = [(ordered[0], ordered[-1])]
    if len(ordered) >= 3:
        hypotheses.extend(
            [
                (ordered[1], ordered[-1]),
                (ordered[0], ordered[-2]),
            ]
        )
    return list(dict.fromkeys(hypotheses))


def _realize_material_choice_topologies(
    plan: StrategyPlan,
    problem: Mapping[str, Any],
) -> tuple[StrategyPlan, tuple[str, ...]]:
    """Compile material-choice language into fixed, auditable route hypotheses.

    The optimizer owns continuous thicknesses only.  When a user delegates
    material choice, the strategy layer therefore enumerates a small portfolio
    of refractive-index-ordered pairs rather than passing an impossible
    discrete variable to the task compiler.
    """

    intervals = [
        tuple(float(value) for value in item)
        for item in problem.get("wavelengths_nm", []) or []
        if isinstance(item, (list, tuple)) and len(item) == 2
    ]
    center_nm = (
        sum((lo + hi) / 2.0 for lo, hi in intervals) / len(intervals)
        if intervals
        else 550.0
    )
    routes: list[DesignRoute] = []
    warnings: list[str] = []
    for route_index, route in enumerate(plan.routes):
        text = "\n".join(
            (
                route.proposed_topology,
                route.execution_request_english,
                " ".join(route.design_variables),
            )
        )
        delegated_choice = bool(
            re.search(
                r"(?:material\s+(?:choice|selection|sequence)|choose\s+(?:the\s+)?materials?|"
                r"materials?\s+available|from\s+the\s+\d+\s+options)",
                text,
                re.IGNORECASE,
            )
            or re.search(
                r"\bmaterial\s+(?:choice|selection|sequence)\b",
                " ".join(route.design_variables),
                re.IGNORECASE,
            )
        )
        layer_count = _route_layer_count(route)
        if layer_count is None:
            word_counts = {
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
                "fifteen": 15, "sixteen": 16, "seventeen": 17,
                "eighteen": 18, "nineteen": 19, "twenty": 20,
            }
            word_match = re.search(
                r"\b(" + "|".join(word_counts) + r")\s+layers?\b",
                text,
                re.IGNORECASE,
            )
            if word_match:
                layer_count = word_counts[word_match.group(1).casefold()]
        materials = list(dict.fromkeys(
            item.strip() for item in route.proposed_materials if item.strip()
        ))
        if not delegated_choice or layer_count is None or len(materials) < 2:
            routes.append(route)
            continue
        pairs = _material_pair_portfolio(
            materials,
            center_wavelength_nm=center_nm,
        )
        low_index, high_index = pairs[route_index % len(pairs)]
        # Start with the high-index member at the incident side.  Reversing the
        # pair is represented by another route only when route capacity exists.
        sequence = [
            high_index if index % 2 == 0 else low_index
            for index in range(layer_count)
        ]
        sequence_text = " / ".join(sequence)
        fixed_topology = (
            f"exactly {layer_count} finite layers from the incident side: "
            f"{sequence_text}"
        )
        fixed_request = " ".join(
            (
                f"Perform one bounded TMM thickness optimization for an exactly "
                f"{layer_count}-layer planar stack.",
                f"Use the fixed material sequence from the incident side: {sequence_text}.",
                "Optimize only the physical thickness of each finite layer within the user-declared bounds.",
                "Do not optimize material identity or layer count in this route.",
                route.execution_request_english,
            )
        )
        fixed_request = re.sub(
            r"Optimize\s+(?:the\s+)?material\s+(?:selection|choice)[^.]*\.",
            "",
            fixed_request,
            flags=re.IGNORECASE,
        )
        fixed_request = re.sub(
            r"Optimize\s+(?:the\s+)?material\s+sequence[^.]*\.",
            "",
            fixed_request,
            flags=re.IGNORECASE,
        )
        fixed_request = re.sub(
            r"Design\s+Variables\s*:\s*\d+\.\s*Material\s+ID[^.]*\.\s*",
            "Design Variables: ",
            fixed_request,
            flags=re.IGNORECASE,
        )
        fixed_request = re.sub(
            r"The\s+material\s+registry\s+should\s+resolve[^.]*\.",
            "",
            fixed_request,
            flags=re.IGNORECASE,
        )
        fixed_request = re.sub(
            r"Stack\s+structure\s*:[^.]*\.",
            "",
            fixed_request,
            flags=re.IGNORECASE,
        )
        routes.append(
            route.model_copy(
                update={
                    "proposed_topology": fixed_topology,
                    "design_variables": tuple(
                        f"Physical thickness of {material} layer {index + 1}"
                        for index, material in enumerate(sequence)
                    ),
                    "execution_request_english": re.sub(r"\s+", " ", fixed_request).strip(),
                }
            )
        )
        warnings.append(
            f"route {route.route_id} delegated material choice was realized as "
            f"the fixed {high_index}/{low_index} index-contrast hypothesis at "
            f"{center_nm:g} nm"
        )
    return plan.model_copy(update={"routes": tuple(routes)}), tuple(warnings)


def _validate_explicit_constraint_preservation(
    plan: StrategyPlan,
    problem: Mapping[str, Any],
) -> tuple[str, ...]:
    """Ensure every standalone route preserves user-facing optical controls."""

    errors: list[str] = []
    expected_polarizations = {
        str(item).strip().casefold()
        for item in problem.get("polarizations", []) or []
        if str(item).strip()
    }
    expected_angles = [float(item) for item in problem.get("angles_deg", []) or []]
    expected_wavelengths = [
        tuple(float(value) for value in interval)
        for interval in problem.get("wavelengths_nm", []) or []
        if isinstance(interval, (list, tuple)) and len(interval) == 2
    ]
    manufacturing_text = " ".join(
        str(item) for item in problem.get("manufacturing_constraints", []) or []
    )
    expected_percentages = re.findall(
        r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:%|percent)\b",
        manufacturing_text,
        re.IGNORECASE,
    )

    for route in plan.routes:
        text = route.execution_request_english.casefold()
        if re.search(r"\b(?:compare(?:d|ison)?\s+(?:with|to)\s+)?route\s*[_-]?\d+\b", text):
            errors.append(
                f"route {route.route_id} is not standalone because it references another route"
            )
        for polarization in expected_polarizations:
            aliases = {
                "te": (r"\bte\b", r"\bs[- ]polar(?:ized|ization)\b"),
                "tm": (r"\btm\b", r"\bp[- ]polar(?:ized|ization)\b"),
                "s": (r"\bs[- ]polar(?:ized|ization)\b", r"\bte\b"),
                "p": (r"\bp[- ]polar(?:ized|ization)\b", r"\btm\b"),
            }.get(polarization, (rf"\b{re.escape(polarization)}\b",))
            if not any(re.search(pattern, text) for pattern in aliases):
                errors.append(
                    f"route {route.route_id} omits explicit polarization {polarization}"
                )
        for angle in expected_angles:
            if abs(angle) <= 1e-9:
                present = bool(
                    re.search(r"\bnormal[- ]incidence\b", text)
                    or re.search(r"\b0(?:\.0+)?\s*(?:deg(?:ree)?s?|\u00b0)\b", text)
                )
            else:
                present = bool(
                    re.search(
                        rf"\b{re.escape(f'{angle:g}')}\s*(?:deg(?:ree)?s?|\u00b0)\b",
                        text,
                    )
                )
            if not present:
                errors.append(
                    f"route {route.route_id} omits explicit incidence angle {angle:g} degrees"
                )
        for lo, hi in expected_wavelengths:
            required = {f"{lo:g}", f"{hi:g}"}
            if any(not re.search(rf"(?<!\d){re.escape(item)}(?!\d)", text) for item in required):
                errors.append(
                    f"route {route.route_id} omits wavelength interval {lo:g}-{hi:g} nm"
                )
        for percentage in expected_percentages:
            if not re.search(
                rf"(?<!\d){re.escape(percentage)}\s*(?:%|percent)\b",
                text,
            ) or not re.search(r"\b(?:thickness|manufactur|tolerance|error)", text):
                errors.append(
                    f"route {route.route_id} omits the explicit {percentage}% manufacturing tolerance"
                )
    return tuple(dict.fromkeys(errors))


_REPEAT_COUNT_BOUND = re.compile(
    r"\bN\b\s*(?:=|:|\bis\b(?:\s+fixed\s+at)?|\bequals\b|\bof\b)\s*\d+"
)


def _validate_executable_topologies(plan: StrategyPlan) -> tuple[str, ...]:
    """Reject route prose that cannot be expanded into an immutable TMM stack."""

    errors: list[str] = []
    # Each entry is (pattern, flags, reason, requires_unbound_repeat_count).
    unresolved_patterns = (
        (
            r"\.\.\.|\u2026",
            re.IGNORECASE,
            "contains an ellipsis instead of explicit repeated layers",
            False,
        ),
        (
            r"\[[^\]]+\]\s*[_^]?\s*[Nn]\b",
            re.IGNORECASE,
            "contains an unresolved symbolic repeat count",
            False,
        ),
        # Case-sensitive, and only when nothing binds N to a literal. Lowercase
        # ``n`` is the refractive index -- the most common symbol in thin-film
        # prose -- so matching it case-insensitively rejected every route that
        # described its own materials ("tabulated n and k data for Ge"). A
        # capital N that the prose does fix ("the pair count N is 8") likewise
        # yields a determinate stack, which is all this guard exists to require.
        (
            r"\bN\b(?!\s*=\s*\d+)",
            0,
            "contains an unresolved symbolic repeat count",
            True,
        ),
        (
            r"\b(?:optimi[sz]e|vary|search)\s+(?:the\s+)?(?:integer\s+)?(?:layer|pair)\s+count\b",
            re.IGNORECASE,
            "asks the continuous task compiler to search an integer topology",
            False,
        ),
    )
    for route in plan.routes:
        combined = f"{route.proposed_topology}\n{route.execution_request_english}"
        has_realization_override = (
            "topology realization override:" in combined.casefold()
        )
        repeat_count_is_bound = bool(_REPEAT_COUNT_BOUND.search(combined))
        for pattern, flags, reason, requires_unbound in unresolved_patterns:
            if requires_unbound and repeat_count_is_bound:
                continue
            if re.search(pattern, combined, flags):
                if has_realization_override and reason != (
                    "asks the continuous task compiler to search an integer topology"
                ):
                    continue
                if (
                    reason.startswith("asks the continuous task compiler")
                    and (
                        "this route uses the fixed pair count above" in combined.casefold()
                        or has_realization_override
                    )
                ):
                    continue
                errors.append(
                    f"route {route.route_id} is not executable because it {reason}; "
                    "choose one fixed pair/layer count and fully specify the standalone topology"
                )
    return tuple(dict.fromkeys(errors))


def _charter_value(charter: Any, name: str) -> Any:
    if isinstance(charter, Mapping):
        return charter.get(name)
    return getattr(charter, name, None)


def _coerce_bound_pair(value: Any) -> Tuple[float, float] | None:
    """Normalize {"min","max"} mappings or [lo, hi] sequences into a pair."""

    if value is None:
        return None
    if isinstance(value, Mapping):
        low = value.get("min", value.get("lo"))
        high = value.get("max", value.get("hi"))
        if low is None or high is None:
            return None
        return (float(low), float(high))
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return (float(value[0]), float(value[1]))
    return None


def _route_wavelength_range(route: Mapping[str, Any]) -> Tuple[float, float] | None:
    for key in ("wavelength_range_nm", "wavelength_range", "wavelength_nm"):
        if key in route:
            return _coerce_bound_pair(route[key])
    return None


def _route_materials(route: Mapping[str, Any]) -> list[str]:
    for key in ("proposed_materials", "materials"):
        raw = route.get(key)
        if isinstance(raw, (list, tuple)):
            return [str(item).strip().casefold() for item in raw if str(item).strip()]
        if isinstance(raw, str) and raw.strip():
            return [raw.strip().casefold()]
    return []


def _route_layer_counts(route: Mapping[str, Any]) -> list[int]:
    counts: list[int] = []
    for key in ("layer_count", "num_layers"):
        if key in route:
            try:
                counts.append(int(route[key]))
            except (TypeError, ValueError):
                continue
    raw_counts = route.get("layer_counts")
    if isinstance(raw_counts, (list, tuple)):
        for item in raw_counts:
            try:
                counts.append(int(item))
            except (TypeError, ValueError):
                continue
    return counts


def _check_charter_drift(strategy: Mapping[str, Any], charter: Any) -> None:
    """Raise ValueError("CHARTER_DRIFT_ERROR: ...") when any planned route
    violates the immutable ResearchCharter boundaries.

    v0.8 field names: layer_count_bounds (legacy layer_count_hard_bounds is
    accepted as a read-only input alias only). Checks performed per route:
      - structured wavelength range must sit inside the charter range;
      - every proposed material must appear in the charter material_whitelist;
      - declared layer counts must stay within [bounds.min, bounds.max].
    Routes without the corresponding structured keys are not diffed for that
    aspect; prose-level constraints are enforced by the existing validators.
    """
    if charter is None:
        raise ValueError("CHARTER_DRIFT_ERROR: no ResearchCharter provided")
    routes: list[Any] = []
    if isinstance(strategy, Mapping):
        candidate = strategy.get("routes")
        if isinstance(candidate, (list, tuple)):
            routes = list(candidate)
    charter_wavelength = _coerce_bound_pair(
        _charter_value(charter, "wavelength_range_nm")
    )
    raw_whitelist = _charter_value(charter, "material_whitelist") or []
    whitelist_items = (
        raw_whitelist
        if isinstance(raw_whitelist, (list, tuple))
        else [raw_whitelist]
    )
    whitelist = {
        str(item).strip().casefold()
        for item in whitelist_items
        if str(item).strip()
    }
    layer_bounds = _coerce_bound_pair(
        _charter_value(charter, "layer_count_bounds")
        or _charter_value(charter, "layer_count_hard_bounds")
    )
    for index, route_raw in enumerate(routes):
        route = route_raw if isinstance(route_raw, Mapping) else _as_mapping(route_raw)
        label = str(route.get("route_id") or f"route_{index}")
        wavelength = _route_wavelength_range(route)
        if (
            wavelength is not None
            and charter_wavelength is not None
            and (
                wavelength[0] < charter_wavelength[0] - 1e-9
                or wavelength[1] > charter_wavelength[1] + 1e-9
            )
        ):
            raise ValueError(
                f"CHARTER_DRIFT_ERROR: route {label} proposes wavelength range "
                f"{list(wavelength)} nm outside the charter range "
                f"{list(charter_wavelength)} nm"
            )
        materials = _route_materials(route)
        if whitelist and materials:
            outside = [item for item in materials if item not in whitelist]
            if outside:
                raise ValueError(
                    f"CHARTER_DRIFT_ERROR: route {label} proposes material(s) "
                    f"{outside} outside the charter material_whitelist"
                )
        layer_counts = _route_layer_counts(route)
        if layer_bounds is not None and layer_counts:
            low, high = layer_bounds
            bad = [count for count in layer_counts if not low <= count <= high]
            if bad:
                raise ValueError(
                    f"CHARTER_DRIFT_ERROR: route {label} layer count(s) {bad} "
                    f"outside the charter layer_count_bounds "
                    f"[{int(low)}, {int(high)}]"
                )


class QwenTMMStrategyPlanner:
    """Plan or revise a bounded route portfolio using the article plus tier."""

    def __init__(
        self,
        *,
        client: StrategyPlannerClient | None = None,
        prompt_path: str | Path = DEFAULT_STRATEGY_PROMPT,
        maximum_attempts: int = 2,
    ) -> None:
        # T-05: strategy planning routes through the plus tier by default.
        self.client = client or ArticlePlusQwenClient(role="plus")
        self.prompt_path = Path(prompt_path)
        self.maximum_attempts = max(1, min(int(maximum_attempts), 2))
        declared_model = getattr(self.client, "model_name", None)
        declared_label = str(declared_model).strip() if declared_model is not None else ""
        if declared_label and declared_label not in _ALLOWED_PLANNER_MODELS:
            raise ValueError(
                f"strategy planner model lock violation: client declared {declared_model!r}"
            )
        # Results echo the client's declared model; the article default is plus.
        self._model_label = declared_label or ARTICLE_STRATEGY_PLANNER_MODEL

    def plan(
        self,
        problem_analysis: Mapping[str, Any] | BaseModel,
        method_research: Mapping[str, Any] | BaseModel,
        *,
        prior_iterations: Iterable[Mapping[str, Any]] = (),
        feedback_directives: Iterable[str] = (),
        charter: Any | None = None,
        force_mock: bool | None = None,
        chain_id: str | None = None,
    ) -> StrategyPlanningResult:
        # T-05 Charter immutability gate (field presence) runs first.
        if charter is not None:
            validate_research_charter(charter)
        problem = _as_mapping(problem_analysis)
        research = _compact_method_research(_as_mapping(method_research))
        allowed_evidence_ids = _evidence_ids(research)
        system_prompt = self.prompt_path.read_text(encoding="utf-8")
        base_payload: dict[str, Any] = {
            "problem_analysis": problem,
            "method_research": research,
            "prior_iterations": _compact_prior_iterations(prior_iterations),
            "feedback_directives": [
                str(item).strip()
                for item in feedback_directives
                if str(item).strip()
            ][:6],
            "fixed_rules": {
                "solver_scope": "planar linear isotropic TMM only",
                "performance_targets": "soft ranking objectives, never admission gates",
                "physics_acceptance": "deterministic verifier only",
                "evidence_ids_allowed": sorted(allowed_evidence_ids),
                "model": self._model_label,
            },
        }
        # Fix A: when refining a single chain, inject its stable id so the
        # planner knows to set parent_route_id on every continuation route.
        if chain_id:
            base_payload["refinement_chain"] = {
                "current_route_id": chain_id,
                "instruction": (
                    f"You are refining route '{chain_id}'. "
                    f"Every route in your response MUST have parent_route_id "
                    f"set to '{chain_id}'. Do NOT use parent_route_id: null."
                ),
            }
        usages: list[dict[str, Any]] = []
        validation_errors: tuple[str, ...] = ()
        validation_history: list[str] = []
        previous = ""
        for attempt in range(1, self.maximum_attempts + 1):
            if attempt == 1:
                payload = dict(base_payload)
            else:
                previous_plan = _safe_json(previous)
                # R-01: the repair attempt keeps method_research and
                # prior_iterations. Without them the model loses the evidence
                # pool and the feedback history, so a repair degenerates into
                # regenerating an unrelated plan instead of iterating.
                payload = {
                    "problem_analysis": problem,
                    "method_research": base_payload["method_research"],
                    "prior_iterations": base_payload["prior_iterations"],
                    "existing_plan": previous_plan,
                    "feedback_directives": base_payload["feedback_directives"],
                    "fixed_rules": base_payload["fixed_rules"],
                    "repair_request": {
                        "validation_errors": list(validation_errors),
                        "instruction": (
                            "Repair only the listed defects in existing_plan. Return "
                            "the corrected complete StrategyPlan JSON object only. "
                            "Preserve its evidence IDs and scientific content. "
                            "Cite evidence IDs only from fixed_rules."
                            "evidence_ids_allowed, which method_research supports. "
                            "Keep every route that prior_iterations shows as an "
                            "improvement, and do not discard the accumulated "
                            "iteration history while repairing."
                        ),
                    },
                }
            try:
                response = self.client.call(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                    ],
                    max_tokens=PLANNER_MAX_TOKENS,
                    force_mock=force_mock,
                )
            except Exception as exc:
                return StrategyPlanningResult(
                    status="unavailable",
                    attempts=attempt,
                    validation_errors=(f"{type(exc).__name__}: {exc}",),
                    usage=tuple(usages),
                )
            usage_row = dict(response.get("_llm_usage") or {})
            usages.append(usage_row)
            # T-05: meter every planning Qwen call on the run-level CostTracker.
            total_tokens = usage_row.get("total_tokens")
            if total_tokens is None:
                total_tokens = (
                    int(usage_row.get("input_tokens") or 0)
                    + int(usage_row.get("output_tokens") or 0)
                ) or (
                    int(usage_row.get("prompt_tokens") or 0)
                    + int(usage_row.get("completion_tokens") or 0)
                )
            get_cost_tracker().record_qwen_usage("plus", int(total_tokens or 0))
            previous = str(response.get("content") or "")
            try:
                raw_plan = _safe_json(previous)
                # R-04: extract expected_observations and stop_conditions before validation
                # to avoid extra="forbid" on DesignRoute. Store in pre_declarations field.
                # Shape-normalized: these are popped out BEFORE contract
                # validation, so no contract ever rejects a wrong shape here. A
                # bare string used to reach list() and char-split into dozens of
                # one-character "declarations" that the reflection prompt then
                # reflected against (R-09 audit; fails open, unlike the tuple
                # fields which fail loudly).
                pre_declarations: dict[str, dict[str, list[str]]] = {}
                routes_raw = raw_plan.get("routes", [])
                if isinstance(routes_raw, list):
                    for route_raw in routes_raw:
                        if isinstance(route_raw, dict):
                            route_id = str(route_raw.get("route_id") or "")
                            if route_id:
                                pre_declarations[route_id] = {
                                    "expected_observations": (
                                        normalize_text_sequence_list(
                                            route_raw.pop("expected_observations", [])
                                        )
                                    ),
                                    "stop_conditions": normalize_text_sequence_list(
                                        route_raw.pop("stop_conditions", [])
                                    ),
                                }
                plan = StrategyPlan.model_validate(raw_plan)
                warnings: list[str] = []
                plan, id_warnings = _repair_near_match_evidence_ids(
                    plan, allowed_evidence_ids
                )
                warnings.extend(id_warnings)
                plan, topology_warnings = _fix_discrete_pair_ranges(plan)
                warnings.extend(topology_warnings)
                plan, realization_warnings = _realize_symbolic_topologies(plan)
                warnings.extend(realization_warnings)
                plan, layer_count_warnings = _enforce_requested_layer_count_portfolio(
                    plan, problem
                )
                warnings.extend(layer_count_warnings)
                plan, material_warnings = _realize_material_choice_topologies(
                    plan, problem
                )
                warnings.extend(material_warnings)
                plan, control_warnings = _attach_canonical_execution_controls(
                    plan, problem
                )
                warnings.extend(control_warnings)
                link_errors = _validate_evidence_links(plan, allowed_evidence_ids)
                constraint_errors = _validate_explicit_constraint_preservation(
                    plan, problem
                )
                topology_errors = _validate_executable_topologies(plan)
                if link_errors or constraint_errors or topology_errors:
                    raise ValueError(
                        "; ".join((*link_errors, *constraint_errors, *topology_errors))
                    )
                # T-05: enforce immutable ResearchCharter boundaries on the
                # fully normalized plan; violations feed the repair loop.
                if charter is not None:
                    _check_charter_drift(plan.model_dump(mode="json"), charter)
                safe_stop = (
                    "Stop after the bounded route portfolio is exhausted or further search "
                    "stagnates. Return every physically verified candidate and report soft "
                    "objective trade-offs without a performance admission threshold."
                )
                if plan.stop_if_all_routes_fail != safe_stop:
                    plan = plan.model_copy(update={"stop_if_all_routes_fail": safe_stop})
                    warnings.append(
                        "the model-authored stopping sentence was replaced by the fixed soft-objective policy"
                    )
                result = StrategyPlanningResult(
                    status="planned",
                    plan=plan,
                    attempts=attempt,
                    normalization_warnings=tuple(warnings),
                    usage=tuple(usages),
                )
                object.__setattr__(result, "_pre_declarations", pre_declarations)
                return result
            except (ValidationError, ValueError) as exc:
                validation_errors = (str(exc),)
                validation_history.extend(validation_errors)
        result = StrategyPlanningResult(
            status="invalid",
            attempts=self.maximum_attempts,
            validation_errors=tuple(dict.fromkeys(validation_history or validation_errors)),
            usage=tuple(usages),
        )
        object.__setattr__(result, "_rejected_plan", str(previous or ""))
        return result


__all__ = [
    "DEFAULT_STRATEGY_PROMPT",
    "PLANNER_MAX_TOKENS",
    "DesignRoute",
    "QwenTMMStrategyPlanner",
    "StrategyPlan",
    "StrategyPlannerClient",
    "StrategyPlanningResult",
]
