"""R-07: cross-route Pareto summary. Constructed mock tracks, zero LLM."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from optomind_optics.harness.research_orchestrator import (
    STAGNATION_WINDOW_ROUNDS as ORCHESTRATOR_STAGNATION_WINDOW_ROUNDS,
    RouteTrack,
)
from optomind_optics.harness.tournament_summary import (
    COMPARABILITY_DISCLAIMER,
    STAGNATION_WINDOW_ROUNDS,
    summarize_tournament,
)


def _track(
    route_id: str,
    *,
    status: str = "racing",
    termination_reason: str = "",
    rounds_used: int = 2,
    score_history: List[float] | None = None,
    source: str = "experience_derived",
    hypothesis: str = "Alternating indices form a stop band.",
) -> RouteTrack:
    track = RouteTrack(route_id=route_id, source=source)
    track.status = status
    track.termination_reason = termination_reason
    track.rounds_used = rounds_used
    track.score_history = list(score_history or [0.6, 0.7])
    track.current_route = {
        "route_id": route_id,
        "scientific_hypothesis": hypothesis,
        "proposed_materials": ["SiO2", "TiO2"],
        "proposed_topology": "Alternating dielectric pairs on glass.",
    }
    return track


def _cand(cid, target, robust, simple, *, admissible=True, route_source="r1"):
    return {
        "candidate_id": cid,
        "target_score": target,
        "robustness_score": robust,
        "simplicity_score": simple,
        "physically_admissible": admissible,
        "certificate_id": "cert_" + cid,
        "_route": route_source,
    }


def _obs(
    route_id,
    iteration_id,
    valid_count,
    run_status="completed",
    *,
    best_target_score=0.7,
):
    class _Row:
        pass

    row = _Row()
    row.route_id = route_id
    row.iteration_id = iteration_id
    row.physically_valid_candidate_count = valid_count
    row.run_status = run_status
    # Mirrors ResearchIterationObservation: None means this round produced no
    # score, which is what makes score_history positions diverge from rounds.
    row.best_target_score = best_target_score
    row.candidate_summaries = []
    return row


def _candidates(*groups):
    by_route: Dict[str, List[Dict[str, Any]]] = {}
    for route_id, cands in groups:
        by_route[route_id] = list(cands)
    return by_route


# ---------------------------------------------------------------------------
# 1-4: Pareto correctness
# ---------------------------------------------------------------------------


def test_pareto_frontier_excludes_dominated():
    # cand_bad is worse than cand_strong on EVERY axis.
    tracks = [_track("r1")]
    candidates = _candidates(
        (
            "r1",
            [
                _cand("cand_strong", 0.90, 0.80, 0.70),
                _cand("cand_bad", 0.50, 0.40, 0.30),
            ],
        )
    )
    summary = summarize_tournament(tracks, [], candidates)
    frontier_ids = {c["candidate_id"] for c in summary["pareto_frontier"]}
    retained_ids = {c["candidate_id"] for c in summary["dominated_but_retained"]}
    assert "cand_bad" not in frontier_ids
    assert "cand_strong" in frontier_ids
    assert "cand_bad" in retained_ids, "dominated solutions must be RETAINED"


def test_pareto_retains_incomparable_solutions():
    # A wins on target; B wins on robustness: neither dominates the other.
    tracks = [_track("r1"), _track("r2")]
    candidates = _candidates(
        ("r1", [_cand("sol_A", 0.95, 0.30, 0.50)]),
        ("r2", [_cand("sol_B", 0.55, 0.90, 0.50)]),
    )
    summary = summarize_tournament(tracks, [], candidates)
    frontier_ids = {c["candidate_id"] for c in summary["pareto_frontier"]}
    assert {"sol_A", "sol_B"} <= frontier_ids


def test_only_admissible_candidates_enter_frontier():
    tracks = [_track("r1")]
    candidates = _candidates(
        (
            "r1",
            [
                _cand("cand_ok", 0.80, 0.80, 0.80),
                _cand("cand_inadm", 0.99, 0.99, 0.99, admissible=False),
            ],
        )
    )
    summary = summarize_tournament(tracks, [], candidates)
    frontier_ids = {c["candidate_id"] for c in summary["pareto_frontier"]}
    assert "cand_inadm" not in frontier_ids
    assert "cand_inadm" in {
        c["candidate_id"] for c in summary["inadmissible_excluded"]
    }


def test_frontier_spans_multiple_routes():
    tracks = [_track("r1"), _track("r2")]
    candidates = _candidates(
        ("r1", [_cand("sol_A", 0.95, 0.30, 0.50)]),
        ("r2", [_cand("sol_B", 0.55, 0.90, 0.50)]),
    )
    summary = summarize_tournament(tracks, [], candidates)
    provenance = {c["candidate_id"]: c["route_id"] for c in summary["pareto_frontier"]}
    assert provenance["sol_A"] == "r1"
    assert provenance["sol_B"] == "r2"


# ---------------------------------------------------------------------------
# 5-6: primary recommendation and disclosure
# ---------------------------------------------------------------------------


def test_primary_recommendation_from_frontier_only():
    tracks = [_track("r1"), _track("r2")]
    candidates = _candidates(
        ("r1", [_cand("sol_A", 0.95, 0.30, 0.50), _cand("shadow", 0.60, 0.60, 0.60)]),
        ("r2", [_cand("sol_B", 0.55, 0.90, 0.50)]),
    )
    summary = summarize_tournament(tracks, [], candidates)
    primary = summary["primary_recommendation"]
    frontier_ids = {c["candidate_id"] for c in summary["pareto_frontier"]}
    assert primary is not None
    assert primary["candidate_id"] in frontier_ids
    # Highest target inside the frontier wins.
    targets = {c["candidate_id"]: c["target_score"] for c in summary["pareto_frontier"]}
    best_target = max(targets.values())
    assert targets[primary["candidate_id"]] == best_target


def test_comparability_disclaimer_present():
    tracks = [_track("r1"), _track("r2")]
    candidates = _candidates(
        ("r1", [_cand("sol_A", 0.95, 0.30, 0.50)]),
        ("r2", [_cand("sol_B", 0.55, 0.90, 0.50)]),
    )
    summary = summarize_tournament(tracks, [], candidates)
    primary = summary["primary_recommendation"]
    disclaimer = primary["cross_route_comparability_disclaimer"]
    assert COMPARABILITY_DISCLAIMER in disclaimer
    # Red line 6 honesty: normalization bases differ across routes...
    assert "not directly comparable between routes" in disclaimer
    # ...and the ordering never removes frontier members.
    assert "never removes any solution from the frontier" in disclaimer
    assert summary["comparability_note"] == COMPARABILITY_DISCLAIMER


# ---------------------------------------------------------------------------
# 7-10: negative results (P19)
# ---------------------------------------------------------------------------


def test_eliminated_route_recorded_with_hypothesis():
    hypothesis = "A defect cavity yields a narrow transmission resonance."
    tracks = [
        _track(
            "r_dead",
            status="eliminated_physics",
            termination_reason=(
                "VeriTMMResult.outcome == physics_rejected (is_route_eliminable "
                "semantics): refuted by experiment"
            ),
            hypothesis=hypothesis,
        ),
        _track("r_alive"),
    ]
    summary = summarize_tournament(tracks, [], _candidates())
    eliminated = summary["eliminated_routes"]
    assert any(e["route_id"] == "r_dead" for e in eliminated)
    negative = summary["negative_results"]
    refuted = [n for n in negative if n["kind"] == "physics_refuted"]
    assert len(refuted) == 1
    assert refuted[0]["scientific_hypothesis"] == hypothesis
    assert "physics_rejected" in refuted[0]["verdict"]


def test_stagnant_route_records_gain_readings():
    history = [0.62, 0.62, 0.62]
    tracks = [
        _track(
            "r_flat",
            status="stopped_stagnant",
            score_history=history,
            termination_reason="deterministic stagnation criterion triggered",
        )
    ]
    summary = summarize_tournament(tracks, [], _candidates())
    stagnant = [
        n
        for n in summary["negative_results"]
        if n["kind"] == "stagnation" and n["route_id"] == "r_flat"
    ]
    assert len(stagnant) == 1
    entry = stagnant[0]
    assert entry["score_history"] == history
    assert entry["observed_gain"] == 0.0
    assert entry["stalled"] is True


def test_score_regression_recorded():
    tracks = [_track("r_reg", score_history=[0.90, 0.80, 0.85])]
    summary = summarize_tournament(tracks, [], _candidates())
    regressions = [
        n
        for n in summary["negative_results"]
        if n["kind"] == "score_regression"
    ]
    drops = {(r["score_index"], r["from_score"], r["to_score"]) for r in regressions}
    # The second RECORDED SCORE regressed 0.90 -> 0.80. The field is
    # score_index, not a round number: a round whose candidates were all
    # physically rejected appends no score, so position != executed round.
    assert (2, 0.90, 0.80) in drops
    # Position 3 recovered but stays below the peak: recovery is not a drop.
    assert all(r["score_index"] != 3 for r in regressions)
    # No observations were supplied, so the round is honestly unattributable
    # rather than guessed from the position.
    assert all(r["iteration_id"] is None for r in regressions)


def test_regression_attributed_to_the_round_that_actually_regressed():
    """Locks the R-07 audit fix: score_history positions are not round numbers.

    score_history only grows when a round produced a score, while rounds_used
    counts every executed round. Here round 2 yields no admissible candidate,
    so the 0.90 -> 0.80 drop physically happens in round 3 while sitting at
    position 2 of the history. Reporting position 2 as "round 2" points the
    reader at the wrong iteration directory.
    """
    track = _track("r_gap", rounds_used=4, score_history=[0.90, 0.80, 0.85])
    observations = [
        _obs("r_gap", "iteration_01", 2, best_target_score=0.90),
        _obs("r_gap", "iteration_02", 0, best_target_score=None),  # scored nothing
        _obs("r_gap", "iteration_03", 2, best_target_score=0.80),  # the real drop
        _obs("r_gap", "iteration_04", 2, best_target_score=0.85),
    ]
    summary = summarize_tournament([track], observations, _candidates())
    regression = next(
        n for n in summary["negative_results"] if n["kind"] == "score_regression"
    )
    assert regression["from_iteration_id"] == "iteration_01"
    assert regression["iteration_id"] == "iteration_03"
    assert regression["score_index"] == 2  # position, honestly labelled as such


def test_stagnation_recomputation_never_contradicts_itself_silently():
    """Locks the R-07 audit fix: stalled=False under kind='stagnation'.

    The gate stops a route on the window it sees at that moment; the approved
    grace round can then append an improving score. Recomputing over the FINAL
    history then reads "not stagnant" for a route whose status is
    stopped_stagnant. That is publishable only if the artifact says which
    reading is the verdict and which is the recomputation.
    """
    graced = _track(
        "r_grace",
        status="stopped_stagnant",
        termination_reason="no score gain across the stagnation window",
        rounds_used=4,
        score_history=[0.50, 0.50, 0.50, 0.80],  # grace round improved
    )
    entry = next(
        n
        for n in summarize_tournament([graced], [], _candidates())["negative_results"]
        if n["kind"] == "stagnation"
    )
    assert entry["stalled"] is False
    assert entry["recomputation_disagrees_with_verdict"] is True
    assert "grace round" in entry["verdict_basis"]
    # The window used is disclosed, so a reader can reproduce the reading.
    assert entry["stagnation_window_rounds"] == STAGNATION_WINDOW_ROUNDS

    # A genuinely flat route must NOT be flagged as a disagreement.
    flat = _track("r_flat", status="stopped_stagnant", score_history=[0.5, 0.5, 0.5])
    plain = next(
        n
        for n in summarize_tournament([flat], [], _candidates())["negative_results"]
        if n["kind"] == "stagnation"
    )
    assert plain["stalled"] is True
    assert "recomputation_disagrees_with_verdict" not in plain
    assert "verdict_basis" not in plain


def test_stagnation_window_matches_the_orchestrator_constant():
    """The summary restates the window instead of importing it (that import
    would be circular), so nothing but this test keeps the two aligned. If the
    orchestrator retunes its window, the summary must be retuned with it or it
    will publish a reading no gate ever made."""
    assert STAGNATION_WINDOW_ROUNDS == ORCHESTRATOR_STAGNATION_WINDOW_ROUNDS


def test_same_candidate_id_in_two_rounds_stays_two_solutions():
    """Locks the R-07 audit fix: candidate_id is not unique.

    The optimizer restarts its candidate_NN numbering every run, so every
    round of every route re-emits candidate_01. Keyed on the bare id, two
    physically different stacks collapse into one row and
    primary_recommendation no longer resolves to a single artifact directory
    -- which is precisely what R-09 must open.
    """
    track = _track("r1", rounds_used=2, score_history=[0.60, 0.90])
    weak = _cand("candidate_01", 0.60, 0.20, 0.30)
    weak["experiment_id"] = "exp_a"
    weak["iteration_id"] = "iteration_01"
    strong = _cand("candidate_01", 0.90, 0.85, 0.80)
    strong["experiment_id"] = "exp_b"
    strong["iteration_id"] = "iteration_02"

    summary = summarize_tournament(
        [track], [], _candidates(("r1", [weak, strong]))
    )
    frontier = summary["pareto_frontier"]
    dominated = summary["dominated_but_retained"]
    # Both solutions survive as distinct rows: one dominates, none vanishes.
    assert len(frontier) == 1
    assert len(dominated) == 1
    keys = [e["solution_key"] for e in frontier + dominated]
    assert len(set(keys)) == 2, keys
    # The recommendation carries a key that locates exactly one directory.
    primary = summary["primary_recommendation"]
    assert primary["solution_key"] == "r1::iteration_02::exp_b::candidate_01"
    assert primary["iteration_id"] == "iteration_02"


def test_zero_valid_candidate_round_recorded():
    tracks = [_track("r1"), _track("r2")]
    observations = [
        _obs("r1", "iteration_01", 2),
        _obs("r2", "iteration_02", 0, run_status="failed"),
    ]
    summary = summarize_tournament(tracks, observations, _candidates())
    empty_rounds = [
        n
        for n in summary["negative_results"]
        if n["kind"] == "zero_valid_candidates"
    ]
    assert len(empty_rounds) == 1
    assert empty_rounds[0]["iteration_id"] == "iteration_02"
    assert empty_rounds[0]["route_id"] == "r2"


# ---------------------------------------------------------------------------
# 11-12: determinism
# ---------------------------------------------------------------------------


def _fixture():
    tracks = [
        _track("r_b", status="stopped_stagnant", score_history=[0.5, 0.5, 0.5]),
        _track("r_a"),
        _track(
            "r_c",
            status="eliminated_physics",
            termination_reason="physics_rejected",
        ),
    ]
    candidates = _candidates(
        ("r_a", [_cand("sol_A", 0.95, 0.30, 0.50), _cand("weak", 0.40, 0.20, 0.10)]),
        ("r_b", [_cand("sol_B", 0.55, 0.90, 0.50)]),
    )
    observations = [_obs("r_c", "iteration_03", 0)]
    return tracks, observations, candidates


def test_summary_is_deterministic():
    one = summarize_tournament(*_fixture(), recorded_at_utc="2026-08-24T00:00:00+00:00")
    two = summarize_tournament(*_fixture(), recorded_at_utc="2026-08-24T00:00:00+00:00")
    assert json.dumps(one, sort_keys=True) == json.dumps(two, sort_keys=True)


def test_ordering_independent_of_input_order():
    tracks, observations, candidates = _fixture()
    forward = summarize_tournament(tracks, observations, candidates, recorded_at_utc="x")
    reversed_tracks = list(reversed(tracks))
    backward = summarize_tournament(
        reversed_tracks, observations, candidates, recorded_at_utc="x"
    )
    forward_routes = [row["route_id"] for row in forward["route_comparison"]]
    backward_routes = [row["route_id"] for row in backward["route_comparison"]]
    assert forward_routes == sorted(forward_routes)
    assert forward_routes == backward_routes
    forward_frontier = [c["candidate_id"] for c in forward["pareto_frontier"]]
    backward_frontier = [c["candidate_id"] for c in backward["pareto_frontier"]]
    assert forward_frontier == backward_frontier
