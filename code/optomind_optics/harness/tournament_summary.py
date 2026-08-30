"""Cross-route Pareto summary for the tournament scheduler (R-07).

Deterministic, LLM-free aggregation of every racing chain's outcome:

* ONE Pareto frontier over all physically admissible candidates of all
  routes. Dominance reuses portfolio._dominates verbatim -- no second Pareto
  implementation. Dominance needs no cross-route score comparability, which
  makes it the only red-line-6-safe aggregation over heterogeneous routes.
* A single primary recommendation chosen INSIDE the frontier by target
  score, carrying the mandatory cross-route comparability disclaimer.
* A per-route comparison table and an explicit negative-results section
  (P19): refuted hypotheses, stagnation readings, score regressions and
  zero-valid-candidate rounds are all recorded, never cleaned away.

Every output list carries an explicit deterministic sort key; nothing
depends on dict iteration order or thread completion order.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping

from .portfolio import _dominates
from .stop_controller import evaluate_stagnation

# Red line 6 disclosure: MANDATORY on the primary recommendation. Target
# scores are normalized inside each route's own objective contract, so their
# cross-route ordering is indicative only. The frontier itself never uses it.
COMPARABILITY_DISCLAIMER = (
    "Cross-route target-score ordering is INDICATIVE ONLY. Each route "
    "normalizes its soft score against its own executable objective "
    "contract; the normalized values are not directly comparable between "
    "routes (red line 6). This ranking is used solely to pick the primary "
    "recommendation INSIDE the Pareto frontier -- it never removes any "
    "solution from the frontier, and every frontier member remains part of "
    "the delivered portfolio."
)

# Stagnation window used when RE-READING a stopped route's history. This must
# match the window the orchestrator's gate used, but importing it from
# research_orchestrator would be circular (that module imports this one), so
# the value is restated here and locked to the orchestrator's constant by a
# test rather than by an import.
STAGNATION_WINDOW_ROUNDS: int = 3


def _field(row: Any, name: str, default: Any = None) -> Any:
    """Read one field from an observation, object- or mapping-shaped.

    Observations arrive as ResearchIterationObservation from the orchestrator
    and as plain dicts from replay/tests. getattr-then-get (rather than
    try/except AttributeError around a whole block) keeps a missing field on
    one shape from silently rerouting the reads of every other field.
    """
    if isinstance(row, Mapping):
        value = row.get(name, default)
    else:
        value = getattr(row, name, default)
    return default if value is None and default is not None else value


def _solution_key(entry: Mapping[str, Any]) -> str:
    """Composite identity of one solution across the whole tournament.

    ``candidate_id`` alone is NOT unique. The optimizer names its candidates
    ``candidate_01..N`` per RUN (optimizer_registry), so every round of every
    route re-emits the same ids, and forward baselines reuse
    ``<experiment_id>__baseline``. This summary aggregates ALL rounds of ALL
    routes into one table, so on any multi-round route the bare id collides by
    construction: two physically different stacks arrive labelled identically,
    and ``primary_recommendation`` stops resolving to a single artifact
    directory -- which is exactly what R-09 has to open. Identity is therefore
    the full route/round/experiment path to the solution.
    """
    return "::".join(
        (
            str(entry.get("route_id") or ""),
            str(entry.get("iteration_id") or ""),
            str(entry.get("experiment_id") or ""),
            str(entry.get("candidate_id") or ""),
        )
    )


def _frontier_sort_key(entry: Mapping[str, Any]):
    # Ties break on the COMPOSITE key: candidate_id is not unique, so using it
    # alone leaves the published order underdetermined between colliding ids.
    return (-float(entry.get("target_score") or 0.0), _solution_key(entry))


def _candidate_entry(route_id: str, source: str, raw: Mapping[str, Any]) -> Dict[str, Any]:
    entry = {
        "candidate_id": str(raw.get("candidate_id") or ""),
        "route_id": route_id,
        "route_source": source,
        # Round + experiment provenance. Without these the candidate_id is
        # ambiguous across rounds; see _solution_key.
        "iteration_id": str(raw.get("iteration_id") or ""),
        "experiment_id": str(raw.get("experiment_id") or ""),
        "target_score": float(raw["target_score"]) if raw.get("target_score") is not None else None,
        "robustness_score": float(raw["robustness_score"]) if raw.get("robustness_score") is not None else None,
        "simplicity_score": float(raw["simplicity_score"]) if raw.get("simplicity_score") is not None else None,
        "physically_admissible": bool(raw.get("physically_admissible")),
        "certificate_id": raw.get("certificate_id"),
    }
    entry["solution_key"] = _solution_key(entry)
    return entry


def _pareto_frontier(admissible: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split admissible scored candidates into (frontier, dominated).

    Pairwise dominance via the EXISTING portfolio._dominates; iteration runs
    over composite-key-sorted lists so the split cannot depend on input order.
    Identity comparison is `is`, never the candidate_id, because that id is not
    unique across rounds (see _solution_key).
    """
    ordered = sorted(admissible, key=_solution_key)
    frontier: List[Dict[str, Any]] = []
    dominated: List[Dict[str, Any]] = []
    for candidate in ordered:
        dominated_by_any = False
        for other in ordered:
            if other is candidate:
                continue
            # _dominates requires a strict improvement on some axis, so two
            # entries with identical scores never dominate each other: equal
            # solutions from different rounds both stay on the frontier.
            if _dominates(other, candidate):
                dominated_by_any = True
                break
        (frontier if not dominated_by_any else dominated).append(candidate)
    return (
        sorted(frontier, key=_frontier_sort_key),
        sorted(dominated, key=_frontier_sort_key),
    )


def _scored_rounds_by_route(
    observations: Iterable[Any],
) -> Dict[str, List[str]]:
    """route_id -> iteration_ids of the rounds that actually produced a score.

    ``score_history`` grows ONLY when an observation carried a non-None
    ``best_target_score`` (research_orchestrator), while ``rounds_used``
    increments on every executed round. A round whose candidates were all
    physically rejected therefore appends nothing, and the Nth score is no
    longer the Nth round. This mapping recovers which round each score
    belongs to so regressions can be attributed to a real iteration_id.
    """
    scored: Dict[str, List[str]] = {}
    for row in observations:
        route_id = str(_field(row, "route_id", ""))
        iteration_id = str(_field(row, "iteration_id", ""))
        if _field(row, "best_target_score", None) is not None:
            scored.setdefault(route_id, []).append(iteration_id)
    return scored


def _planning_source_comparison(
    tracks: Iterable[Any],
    observations: Iterable[Any],
    candidates_by_route: Mapping[str, Iterable[Mapping[str, Any]]],
    scoring_ranking: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    """Summarize the literature arm and memory-only control arm separately.

    Route-local soft scores are intentionally never averaged across routes:
    their objective contracts may differ.  The only cross-arm performance
    comparison here is made from the frozen standard's representative scores,
    and only when both arms actually have scoreable representatives.  Progress
    is retained per route as a within-chain diagnostic, which is the valid way
    to answer whether ordinary starting points improved during iteration.
    """

    ordered_tracks = sorted(
        list(tracks), key=lambda track: str(getattr(track, "route_id", ""))
    )
    observations = list(observations)
    source_by_route = {
        str(getattr(track, "route_id", "")): str(
            getattr(track, "source", "planned")
        )
        for track in ordered_tracks
    }
    observation_by_route: Dict[str, list[Any]] = {}
    for row in observations:
        observation_by_route.setdefault(str(_field(row, "route_id", "")), []).append(row)

    frozen_by_route: Dict[str, Dict[str, Any]] = {}
    if isinstance(scoring_ranking, Mapping):
        for entry in scoring_ranking.get("routes", []) or []:
            if not isinstance(entry, Mapping):
                continue
            representative = entry.get("representative")
            if isinstance(representative, Mapping) and representative.get("score") is not None:
                frozen_by_route[str(entry.get("route_id") or "")] = dict(representative)

    progress_by_route: Dict[str, Dict[str, Any]] = {}
    for route_id, rows in observation_by_route.items():
        scores = [
            float(_field(row, "best_target_score"))
            for row in rows
            if _field(row, "best_target_score", None) is not None
        ]
        initial = scores[0] if scores else None
        best = max(scores) if scores else None
        progress_by_route[route_id] = {
            "route_id": route_id,
            "source": source_by_route.get(route_id, "planned"),
            "initial_best_target_score": initial,
            "best_target_score": best,
            "within_route_delta": (
                best - initial if initial is not None and best is not None else None
            ),
            "scored_rounds": len(scores),
            "executed_rounds": sum(
                1
                for row in rows
                if str(_field(row, "run_status", ""))
                or _field(row, "compilation_status", None) is not None
            ),
        }

    groups: Dict[str, Dict[str, Any]] = {}
    for source in sorted(set(source_by_route.values())):
        route_ids = sorted(
            route_id for route_id, route_source in source_by_route.items()
            if route_source == source
        )
        route_progress = [progress_by_route[route_id] for route_id in route_ids if route_id in progress_by_route]
        representatives = [
            (route_id, frozen_by_route[route_id])
            for route_id in route_ids
            if route_id in frozen_by_route
        ]
        representatives.sort(
            key=lambda item: (-float(item[1].get("score") or 0.0), item[0])
        )
        groups[source] = {
            "route_ids": route_ids,
            "route_count": len(route_ids),
            "routes_with_verified_candidates": sum(
                any(
                    int(_field(row, "physically_valid_candidate_count", 0) or 0) > 0
                    for row in observation_by_route.get(route_id, [])
                )
                for route_id in route_ids
            ),
            "executed_rounds": sum(
                int(getattr(track, "rounds_used", 0) or 0)
                for track in ordered_tracks
                if str(getattr(track, "route_id", "")) in route_ids
            ),
            "route_progress": route_progress,
            "scoreable_routes_by_frozen_standard": len(representatives),
            "best_frozen_standard_result": (
                {
                    "route_id": representatives[0][0],
                    **representatives[0][1],
                }
                if representatives
                else None
            ),
        }

    literature = groups.get("literature_planned")
    control = groups.get("llm_memory_control")
    literature_score = (
        (literature or {}).get("best_frozen_standard_result") or {}
    ).get("score")
    control_score = (
        (control or {}).get("best_frozen_standard_result") or {}
    ).get("score")
    valid_cross_source = bool(
        literature
        and control
        and literature_score is not None
        and control_score is not None
    )
    if valid_cross_source:
        verdict = (
            "memory_control_higher"
            if float(control_score) > float(literature_score)
            else "literature_higher"
            if float(literature_score) > float(control_score)
            else "tie"
        )
    elif literature and control:
        verdict = "not_scoreable_by_frozen_standard"
    else:
        verdict = "control_or_literature_arm_missing"
    return {
        "schema_version": "planning-source-comparison.v1",
        "comparison_basis": (
            "Cross-arm performance uses frozen-standard representative scores only. "
            "Route-local target-score deltas are within-route diagnostics and are "
            "not cross-route rankings."
        ),
        "frozen_standard_formula": (
            str(scoring_ranking.get("formula"))
            if isinstance(scoring_ranking, Mapping)
            and scoring_ranking.get("formula")
            else None
        ),
        "cross_source_comparison_valid": valid_cross_source,
        "control_vs_literature_verdict": verdict,
        "frozen_score_delta_control_minus_literature": (
            float(control_score) - float(literature_score)
            if valid_cross_source
            else None
        ),
        "groups": groups,
    }


def _negative_results(
    tracks: Iterable[Any], observations: Iterable[Any]
) -> List[Dict[str, Any]]:
    """P19: refutations, stagnation, regressions, empty rounds -- all kept."""
    entries: List[Dict[str, Any]] = []
    observations = list(observations)
    scored_rounds = _scored_rounds_by_route(observations)

    for track in tracks:
        route_id = str(getattr(track, "route_id", ""))
        status = str(getattr(track, "status", ""))
        history = [float(v) for v in (getattr(track, "score_history", None) or [])]

        if status == "eliminated_physics":
            current = getattr(track, "current_route", None) or {}
            entries.append(
                {
                    "kind": "physics_refuted",
                    "route_id": route_id,
                    "scientific_hypothesis": str(current.get("scientific_hypothesis") or ""),
                    "verdict": str(getattr(track, "termination_reason", "")),
                }
            )
        elif status == "stopped_stagnant":
            # Recompute with the SAME window the orchestrator's gate used.
            # Passing evaluate_stagnation's defaults instead would silently
            # disagree with the verdict that stopped this route the moment
            # STAGNATION_WINDOW_ROUNDS is retuned -- and that constant exists
            # precisely to be retuned.
            stalled, observed_gain = evaluate_stagnation(
                history, patience_rounds=STAGNATION_WINDOW_ROUNDS
            )
            entry = {
                "kind": "stagnation",
                "route_id": route_id,
                "score_history": history,
                "stagnation_window_rounds": STAGNATION_WINDOW_ROUNDS,
                "observed_gain": observed_gain,
                "stalled": bool(stalled),
                "detail": str(getattr(track, "termination_reason", "")),
            }
            if not stalled:
                # The gate stopped this route on the window it saw AT that
                # moment; an approved grace round can then append an improving
                # score, so the final history no longer reads as stagnant.
                # Publishing stalled=False under kind="stagnation" with no
                # explanation makes the artifact contradict itself. State which
                # reading is the verdict and which is the recomputation.
                entry["verdict_basis"] = (
                    "The deterministic gate stopped this route on the window "
                    "observed at the stop decision. This recomputation runs "
                    "over the FINAL score history, which includes any later "
                    "grace round; a disagreement here means a post-verdict "
                    "score arrived, not that the verdict was wrong."
                )
                entry["recomputation_disagrees_with_verdict"] = True
            entries.append(entry)

        # Score regressions, independent of the terminal status. Attribute each
        # to the iteration that produced the score rather than to its position
        # in score_history: unscored rounds leave no entry, so position N is
        # not round N (see _scored_rounds_by_route).
        route_scored_rounds = scored_rounds.get(route_id, [])
        for index in range(1, len(history)):
            previous, current_value = history[index - 1], history[index]
            if current_value < previous:
                entry = {
                    "kind": "score_regression",
                    "route_id": route_id,
                    # Position within score_history, 1-based. NOT the executed
                    # round number when some round scored nothing.
                    "score_index": index + 1,
                    "from_score": previous,
                    "to_score": current_value,
                    "drop": round(previous - current_value, 12),
                }
                if index < len(route_scored_rounds):
                    entry["iteration_id"] = route_scored_rounds[index]
                    entry["from_iteration_id"] = route_scored_rounds[index - 1]
                else:
                    # Observations were not supplied (or are incomplete): the
                    # drop is real, its round is simply unattributable here.
                    entry["iteration_id"] = None
                    entry["from_iteration_id"] = None
                entries.append(entry)

    for row in observations:
        valid_count = int(_field(row, "physically_valid_candidate_count", 0) or 0)
        if valid_count == 0:
            entries.append(
                {
                    "kind": "zero_valid_candidates",
                    "route_id": str(_field(row, "route_id", "")),
                    "iteration_id": str(_field(row, "iteration_id", "")),
                    "run_status": str(_field(row, "run_status", "")),
                }
            )

    return sorted(
        entries,
        key=lambda e: (
            str(e.get("kind")),
            str(e.get("route_id")),
            # score_index orders regressions; iteration_id orders empty rounds.
            str(e.get("score_index", "")),
            str(e.get("iteration_id") or ""),
        ),
    )


def summarize_tournament(
    tracks: Iterable[Any],
    observations: Iterable[Any],
    candidates_by_route: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    recorded_at_utc: str | None = None,
    scoring_ranking: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Aggregate finished tournament state into TOURNAMENT_SUMMARY.json content.

    Deterministic: given equal inputs (any order), every list comes back in
    the same order -- the frontier by (-target_score, solution key), routes
    by route_id, negative results by (kind, route_id, score_index,
    iteration_id). The solution key is route/round/experiment/candidate
    because candidate_id repeats across rounds (see _solution_key).
    """
    observations = list(observations)
    ordered_tracks = sorted(tracks, key=lambda t: str(getattr(t, "route_id", "")))
    source_of = {
        str(getattr(t, "route_id", "")): str(getattr(t, "source", "planned"))
        for t in ordered_tracks
    }

    # ---- (1) Pareto frontier over ALL routes' admissible candidates -------
    admissible: List[Dict[str, Any]] = []
    inadmissible_excluded: List[Dict[str, Any]] = []
    for route_id in sorted(candidates_by_route):
        for raw in candidates_by_route[route_id]:
            entry = _candidate_entry(
                route_id, source_of.get(route_id, "planned"), raw
            )
            if entry["physically_admissible"]:
                admissible.append(entry)
            else:
                inadmissible_excluded.append(entry)
    inadmissible_excluded.sort(key=_frontier_sort_key)
    frontier, dominated_but_retained = _pareto_frontier(admissible)

    # ---- (2) primary recommendation, chosen INSIDE the frontier only ------
    primary: Dict[str, Any] | None = None
    if frontier:
        # The frontier is already sorted by (-target_score, composite key):
        # the primary recommendation is simply its head.
        best = frontier[0]
        primary = {
            **best,
            # Unambiguous pointer for R-09, which has to open this exact
            # artifact directory. candidate_id alone cannot locate it.
            "solution_key": _solution_key(best),
            "selection_basis": (
                "Highest target score WITHIN the Pareto frontier (ties broken "
                "by the route/round/experiment/candidate composite key "
                "ascending, because candidate_id repeats across rounds). No "
                "frontier member was removed by this choice."
            ),
            "cross_route_comparability_disclaimer": COMPARABILITY_DISCLAIMER,
        }

    # ---- (3) route comparison table ---------------------------------------
    frontier_route_ids = {str(e["route_id"]) for e in frontier}
    route_comparison: List[Dict[str, Any]] = []
    for track in ordered_tracks:
        route_id = str(getattr(track, "route_id", ""))
        current = getattr(track, "current_route", None) or {}
        own_admissible = [
            e for e in admissible if str(e["route_id"]) == route_id
        ]
        best_solution = (
            max(own_admissible, key=lambda e: float(e.get("target_score") or 0.0))
            if own_admissible
            else None
        )
        route_comparison.append(
            {
                "route_id": route_id,
                "source": source_of.get(route_id, "planned"),
                "status": str(getattr(track, "status", "")),
                "termination_reason": str(getattr(track, "termination_reason", "")),
                "rounds_used": int(getattr(track, "rounds_used", 0) or 0),
                "best_solution": best_solution,
                "entered_pareto_frontier": route_id in frontier_route_ids,
                "design_orientation": {
                    "scientific_hypothesis": str(current.get("scientific_hypothesis") or ""),
                    "proposed_materials": list(current.get("proposed_materials") or []),
                    "proposed_topology": str(current.get("proposed_topology") or ""),
                },
            }
        )
    route_comparison.sort(key=lambda row: str(row["route_id"]))

    eliminated_routes = [
        {
            "route_id": str(getattr(t, "route_id", "")),
            "source": source_of.get(str(getattr(t, "route_id", "")), "planned"),
            "termination_reason": str(getattr(t, "termination_reason", "")),
            "scientific_hypothesis": str(
                (getattr(t, "current_route", None) or {}).get("scientific_hypothesis") or ""
            ),
        }
        for t in ordered_tracks
        if str(getattr(t, "status", "")) == "eliminated_physics"
    ]
    planning_source_comparison = _planning_source_comparison(
        ordered_tracks,
        observations,
        candidates_by_route,
        scoring_ranking,
    )

    return {
        "schema_version": "tournament-summary.v1",
        "recorded_at_utc": recorded_at_utc
        or datetime.now(timezone.utc).isoformat(),
        "pareto_frontier": frontier,
        "primary_recommendation": primary,
        "route_comparison": route_comparison,
        "dominated_but_retained": dominated_but_retained,
        "eliminated_routes": eliminated_routes,
        "negative_results": _negative_results(ordered_tracks, observations),
        # Extra disclosure beyond the mandated keys.
        "inadmissible_excluded": inadmissible_excluded,
        "comparability_note": COMPARABILITY_DISCLAIMER,
        "planning_source_comparison": planning_source_comparison,
    }


__all__ = ["COMPARABILITY_DISCLAIMER", "summarize_tournament"]
