"""Dual-source portfolio seeding for the research tournament (R-05).

Two epistemically independent sources propose initial routes:

* evidence_derived   -- grounded in retrieved literature evidence; every route
  carries non-empty evidence_ids referencing real retrieval hits.
* experience_derived -- grounded in the model's own physics knowledge; every
  route carries empty evidence_ids and a concrete theory_basis statement.

The two groups are merged, deduplicated with the SAME hash function used by
the research orchestrator's queue, and truncated to the configured maximum.
Every exclusion (hash conflicts, truncation, invalid contracts) is recorded in
the PORTFOLIO_SEEDING.json sidecar instead of being silently discarded.

Red-line notes:

* red line 5: the source marker never enters DesignRoute. It lives in a
  parallel mapping (SeededPortfolio.sources, route_id -> marker), the same
  side-channel pattern as R-04's PrivateAttr pre-declarations.
* red line 1: the model proposes; this module deterministically validates,
  deduplicates and truncates. Nothing here calls a solver or scores physics.

This module owns ONLY initial seeding. Feedback-driven replanning still uses
strategy_planner (and becomes the in-loop improver under R-06).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from pydantic import ValidationError

from config.qwen_config import get_cost_tracker

from .strategy_planner import (
    ARTICLE_STRATEGY_PLANNER_MODEL,
    DesignRoute,
    PLANNER_MAX_TOKENS,
    _ROUTE_TEXT_SEQUENCE_FIELDS,
)
from .text_safety import normalize_text_sequence, normalize_text_sequence_list

# The DesignRoute tuple fields, plus the two pre-declaration keys that are
# popped out before validation. Sourced from the contract's own set so this
# cannot drift when a field is added there.
_SEED_TEXT_SEQUENCE_FIELDS: frozenset[str] = _ROUTE_TEXT_SEQUENCE_FIELDS | frozenset(
    {"expected_observations", "stop_conditions"}
)

DEFAULT_SEEDING_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "TMM Portfolio Seeding.txt"
)

# Seeding is a planning-class heavy task: plus tier, same tier as strategy
# planning. It runs once per tournament but decides the quality ceiling of the
# whole race. This is deliberately DIFFERENT from R-04 reflection, which uses
# the flash tier ("turbo" bucket) because it fires on every route per round.
# The first argument of record_qwen_usage() below is therefore the BUDGET BUCKET
# KEY "plus" -- never this model-name constant (R-04-FIX D-2 lesson).
PORTFOLIO_SEEDING_MODEL = ARTICLE_STRATEGY_PLANNER_MODEL

MINIMUM_SEED_ROUTES = 2


class SeedPortfolioClient(Protocol):
    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 8000,
        force_mock: bool | None = None,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class SeededPortfolio:
    """Deterministic outcome of one seeding call."""

    routes: list[dict[str, Any]]
    sources: dict[str, str]
    pre_declarations: dict[str, dict[str, list[str]]]
    usage_rows: list[dict[str, Any]]
    plan: dict[str, Any]
    sidecar: dict[str, Any]

    @property
    def insufficient(self) -> bool:
        return bool(self.sidecar.get("insufficient"))


def _safe_json(text: str) -> dict[str, Any]:
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


def _usage_row_total(usage_row: Mapping[str, Any]) -> int:
    """Accept both usage spellings (R-04 requirement)."""
    total = usage_row.get("total_tokens")
    if total is None:
        total = (
            int(usage_row.get("input_tokens") or 0)
            + int(usage_row.get("output_tokens") or 0)
        ) or (
            int(usage_row.get("prompt_tokens") or 0)
            + int(usage_row.get("completion_tokens") or 0)
        )
    return int(total or 0)


def _build_user_payload(
    problem_analysis: Mapping[str, Any],
    method_research: Mapping[str, Any],
    *,
    max_routes: int,
) -> tuple[dict[str, Any], list[str]]:
    evidence_rows: list[dict[str, Any]] = []
    for item in (method_research or {}).get("evidence", []) or []:
        if not isinstance(item, Mapping):
            continue
        evidence_id = str(item.get("evidence_id") or "").strip()
        if not evidence_id:
            continue
        evidence_rows.append(
            {
                "evidence_id": evidence_id,
                "title": str(item.get("title") or "")[:200],
                "text": str(item.get("text") or item.get("summary") or "")[:600],
            }
        )
    findings = [
        str(item.get("finding") or "")
        for item in (method_research or {}).get("method_findings", []) or []
        if isinstance(item, Mapping) and str(item.get("finding") or "").strip()
    ][:8]
    payload = {
        "problem_analysis": {
            "problem_id": str((problem_analysis or {}).get("problem_id") or "unknown"),
            "primary_intent": str((problem_analysis or {}).get("primary_intent") or ""),
            "normalized_request_english": str(
                (problem_analysis or {}).get("normalized_request_english") or ""
            ),
            "compatibility": str((problem_analysis or {}).get("compatibility") or ""),
        },
        "retrieved_evidence": evidence_rows,
        "method_findings": findings,
        "fixed_rules": {
            "solver_family": "TMM only",
            "solver_boundary": (
                "planar linear isotropic transfer-matrix optics only"
            ),
            "performance_targets": "soft ranking scores only",
            "evidence_ids_allowed": [row["evidence_id"] for row in evidence_rows],
            "maximum_routes_per_source": int(max_routes),
        },
    }
    return payload, findings


def _sort_key(route: Mapping[str, Any]) -> tuple[int, str]:
    """Same ordering convention as the orchestrator's queue sort."""
    return (int(route.get("priority") or 100), str(route.get("route_id") or ""))


def seed_portfolio(
    *,
    problem_analysis: Mapping[str, Any],
    method_research: Mapping[str, Any],
    client: SeedPortfolioClient,
    max_routes: int = 5,
    force_mock: bool | None = None,
    prompt_path: Path | None = None,
) -> SeededPortfolio:
    """Generate, validate, merge and rank the initial route portfolio.

    Exactly ONE LLM call. No automatic retry: an insufficient portfolio is
    reported through SeededPortfolio.insufficient so the caller can decide
    between regeneration and reporting.
    """
    # Single source of truth for duplicate detection: import at call time from
    # the orchestrator module so the two dedup sites cannot drift apart
    # (function-local import also avoids the module-level cycle).
    from .research_orchestrator import _route_hash

    max_routes = max(int(max_routes), 1)
    system_prompt = (prompt_path or DEFAULT_SEEDING_PROMPT).read_text(encoding="utf-8")
    user_payload, findings = _build_user_payload(
        problem_analysis, method_research, max_routes=max_routes
    )
    response = client.call(
        [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False),
            },
        ],
        max_tokens=PLANNER_MAX_TOKENS,
        force_mock=force_mock,
    )
    usage_row = dict(response.get("_llm_usage") or {})
    tokens = _usage_row_total(usage_row)
    # Budget bucket key "plus" (existing vocabulary), NOT the pricing-model name.
    get_cost_tracker().record_qwen_usage("plus", tokens)
    usage_rows = [usage_row]

    parsed = _safe_json(str(response.get("content") or ""))
    raw_evidence_group = parsed.get("evidence_derived_routes")
    raw_experience_group = parsed.get("experience_derived_routes")

    allowed_ids = set(user_payload["fixed_rules"]["evidence_ids_allowed"])
    invalid_records: list[dict[str, Any]] = []
    pre_declarations: dict[str, dict[str, list[str]]] = {}
    groups: dict[str, list[dict[str, Any]]] = {}
    # route_id -> source that claimed it. route_id is the primary key of every
    # downstream ledger (all_routes, pre_declarations, sources, the lineage map
    # and the per-route round cap), and NOTHING downstream tolerates a
    # collision: the orchestrator files routes into a dict keyed on route_id, so
    # a second route reusing an id silently disappears from the tournament while
    # inheriting the other one's declarations. _route_hash cannot catch this —
    # it hashes execution_request_english, so two routes with the same id and
    # different requests are "distinct" to dedup and identical to every dict.
    # Uniqueness is therefore enforced here, spanning BOTH source groups.
    claimed_ids: dict[str, str] = {}

    def collect(raw_items: Any, source: str) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        if not isinstance(raw_items, list):
            invalid_records.append(
                {
                    "route_id": "<" + source + "_payload>",
                    "reason": source + " group must be an array",
                }
            )
            return collected
        for index, raw in enumerate(raw_items):
            if not isinstance(raw, Mapping):
                invalid_records.append(
                    {
                        "route_id": "<" + source + "_" + str(index) + ">",
                        "reason": source + " entry is not an object",
                    }
                )
                continue
            route_label = str(raw.get("route_id") or ("<" + source + "_" + str(index) + ">"))
            # Shape-normalize the "list of statements" fields BEFORE the group
            # gates read them. A bare string char-splits under
            # `tuple(str(v) for v in ...)`, which made both gates below
            # ineffective: a single-sentence theory_basis satisfied
            # `any(t.strip() for t in theory_basis)` on its first letter, and a
            # scalar evidence_ids reported every CHARACTER as an unknown id. The
            # rejection then came from DesignRoute one step later with a
            # tuple_type error that named the field but not the real cause
            # (R-09 audit). Normalizing here fixes the diagnosis and the gates
            # together; `working` is built from the normalized mapping so the
            # repair actually reaches the contract and everything downstream.
            raw = {
                key: (
                    normalize_text_sequence(value)
                    if key in _SEED_TEXT_SEQUENCE_FIELDS
                    else value
                )
                for key, value in raw.items()
            }
            evidence_ids = tuple(str(v) for v in (raw.get("evidence_ids") or []))
            theory_basis = tuple(str(v) for v in (raw.get("theory_basis") or []))
            if source == "evidence_derived":
                if not any(e.strip() for e in evidence_ids):
                    invalid_records.append(
                        {
                            "route_id": route_label,
                            "reason": "evidence_derived route has empty evidence_ids",
                        }
                    )
                    continue
                unknown = [e for e in evidence_ids if e not in allowed_ids]
                if unknown:
                    invalid_records.append(
                        {
                            "route_id": route_label,
                            "reason": "evidence_ids outside allowed whitelist: " + str(unknown),
                        }
                    )
                    continue
            else:
                if any(e.strip() for e in evidence_ids):
                    invalid_records.append(
                        {
                            "route_id": route_label,
                            "reason": (
                                "experience_derived route must keep evidence_ids empty"
                            ),
                        }
                    )
                    continue
                if not any(t.strip() for t in theory_basis):
                    invalid_records.append(
                        {
                            "route_id": route_label,
                            "reason": (
                                "experience_derived route requires a concrete "
                                "theory_basis statement"
                            ),
                        }
                    )
                    continue
            working = dict(raw)
            pre_decl = {
                # Same fail-open hazard as the replan path: these are popped out
                # to survive extra="forbid", so NO contract validates them. A
                # bare string reaching list() becomes dozens of one-character
                # "declarations" and the route is still admitted -- carrying
                # per-character noise as the grounding the reflection prompt
                # reflects against (R-09 audit).
                "expected_observations": normalize_text_sequence_list(
                    working.pop("expected_observations", None)
                ),
                "stop_conditions": normalize_text_sequence_list(
                    working.pop("stop_conditions", None)
                ),
            }
            try:
                DesignRoute.model_validate(dict(working))
            except ValidationError as exc:
                invalid_records.append(
                    {
                        "route_id": route_label,
                        "reason": "DesignRoute contract validation failed",
                        "error": str(exc)[:300],
                    }
                )
                continue
            validated_id = str(working.get("route_id"))
            if validated_id in claimed_ids:
                invalid_records.append(
                    {
                        "route_id": validated_id,
                        "reason": (
                            "duplicate route_id already claimed by "
                            + claimed_ids[validated_id]
                            + "; route_id is the primary key of every "
                            "downstream ledger and must be unique across "
                            "both sources"
                        ),
                    }
                )
                continue
            claimed_ids[validated_id] = source
            pre_declarations[validated_id] = pre_decl
            collected.append(working)
        return sorted(collected, key=_sort_key)

    groups["evidence_derived"] = collect(raw_evidence_group, "evidence_derived")
    groups["experience_derived"] = collect(raw_experience_group, "experience_derived")

    # Merge with the canonical hash; on conflict keep the evidence-derived
    # route (P3/P4: literature-backed claims outrank project inference).
    seen: dict[str, str] = {}
    dedup_events: list[dict[str, Any]] = []
    merged: list[tuple[str, dict[str, Any]]] = []
    for source in ("evidence_derived", "experience_derived"):
        for route in groups[source]:
            digest = _route_hash(route)
            route_id = str(route.get("route_id"))
            if digest in seen:
                reason = (
                    "duplicate_hash_kept_evidence_derived"
                    if source == "experience_derived"
                    else "duplicate_hash_within_" + source
                )
                dedup_events.append(
                    {
                        "dropped_route_id": route_id,
                        "kept_route_id": seen[digest],
                        "reason": reason,
                    }
                )
                continue
            seen[digest] = route_id
            merged.append((source, route))

    merged.sort(key=lambda item: _sort_key(item[1]))
    selected_pairs = merged[:max_routes]

    # Source floor. Truncating on priority alone can delete an ENTIRE source:
    # the model assigns priorities within each group independently, so a group
    # that happens to self-rate lower loses every slot. That silently collapses
    # the tournament to a single epistemic source — exactly what this work order
    # exists to prevent — while `insufficient` stays False because the route
    # COUNT is still fine. Reserve one slot per source that actually produced
    # valid routes, displacing the worst-priority route of an over-represented
    # source. Every swap is recorded rather than applied silently.
    source_floor_events: list[dict[str, Any]] = []
    if len(merged) > max_routes:
        for starved in ("evidence_derived", "experience_derived"):
            if not groups[starved]:
                continue  # this source produced nothing valid; nothing to floor
            if any(source == starved for source, _ in selected_pairs):
                continue
            promoted = next(
                (pair for pair in merged[max_routes:] if pair[0] == starved),
                None,
            )
            if promoted is None:
                continue
            counts = Counter(source for source, _ in selected_pairs)
            donor = max(counts, key=lambda source: (counts[source], source))
            if counts[donor] < 2:
                # Cannot honour both sources without emptying the donor.
                continue
            victim_index = max(
                (
                    index
                    for index, (source, _) in enumerate(selected_pairs)
                    if source == donor
                ),
                key=lambda index: _sort_key(selected_pairs[index][1]),
            )
            demoted_route = selected_pairs[victim_index][1]
            selected_pairs[victim_index] = promoted
            source_floor_events.append(
                {
                    "promoted_route_id": str(promoted[1].get("route_id")),
                    "promoted_source": starved,
                    "demoted_route_id": str(demoted_route.get("route_id")),
                    "demoted_source": donor,
                    "reason": "source_floor_reserved_one_slot_per_source",
                }
            )
        selected_pairs.sort(key=lambda item: _sort_key(item[1]))

    selected_ids = [str(route.get("route_id")) for _, route in selected_pairs]
    _selected_id_set = set(selected_ids)
    truncated_ids = [
        str(route.get("route_id"))
        for _, route in merged
        if str(route.get("route_id")) not in _selected_id_set
    ]
    insufficient = len(selected_pairs) < MINIMUM_SEED_ROUTES

    sidecar: dict[str, Any] = {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "evidence_derived": [
            str(route.get("route_id")) for route in groups["evidence_derived"]
        ],
        "experience_derived": [
            str(route.get("route_id")) for route in groups["experience_derived"]
        ],
        "deduplicated": dedup_events,
        "selected": selected_ids,
        "truncated": truncated_ids,
        "insufficient": insufficient,
        # Slot swaps performed to keep both epistemic sources represented in
        # the portfolio. Empty on the common path.
        "source_floor": source_floor_events,
        # Extra disclosure beyond the required keys: entries rejected by the
        # deterministic contract checks above.
        "invalid": invalid_records,
        "model_name": PORTFOLIO_SEEDING_MODEL,
    }

    routes = [dict(route) for _, route in selected_pairs]
    sources = {str(route.get("route_id")): source for source, route in selected_pairs}
    plan = {
        "problem_id": str((problem_analysis or {}).get("problem_id") or "unknown"),
        "planning_summary": (
            "Dual-source portfolio seeding (R-05): literature-evidence-derived "
            "and experience-derived routes racing independently."
        ),
        "routes": routes,
        "research_influence": findings,
        "unresolved_decisions": [],
        "stop_if_all_routes_fail": SAFE_STOP_IF_ALL_ROUTES_FAIL,
    }
    return SeededPortfolio(
        routes=routes,
        sources=sources,
        pre_declarations=pre_declarations,
        usage_rows=usage_rows,
        plan=plan,
        sidecar=sidecar,
    )


class QwenTMPPortfolioSeeder:
    """Injectable seeding component mirroring the other harness adapters."""

    model_name = PORTFOLIO_SEEDING_MODEL

    def __init__(
        self,
        client: SeedPortfolioClient,
        *,
        prompt_path: Path | None = None,
    ) -> None:
        self.client = client
        self.prompt_path = prompt_path

    def seed(
        self,
        *,
        problem_analysis: Mapping[str, Any],
        method_research: Mapping[str, Any],
        max_routes: int = 5,
        force_mock: bool | None = None,
    ) -> SeededPortfolio:
        return seed_portfolio(
            problem_analysis=problem_analysis,
            method_research=method_research,
            client=self.client,
            max_routes=max_routes,
            force_mock=force_mock,
            prompt_path=self.prompt_path,
        )


SAFE_STOP_IF_ALL_ROUTES_FAIL = (
    "Stop after the bounded route portfolio is exhausted or further search "
    "stagnates. Return every physically verified candidate and report soft "
    "objective trade-offs without a performance admission threshold."
)


__all__ = [
    "DEFAULT_SEEDING_PROMPT",
    "MINIMUM_SEED_ROUTES",
    "PORTFOLIO_SEEDING_MODEL",
    "QwenTMPPortfolioSeeder",
    "SAFE_STOP_IF_ALL_ROUTES_FAIL",
    "SeedPortfolioClient",
    "SeededPortfolio",
    "seed_portfolio",
]

