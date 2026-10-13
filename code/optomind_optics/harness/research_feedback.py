"""Deterministic feedback and stopping decisions for TMM research iterations.

Performance is never used as a physics admission gate.  Scores are used only
to decide whether spending more search budget is likely to add information.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, Iterable, Literal, Mapping, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .scoring_standard import ScoringStandard


class ResearchIterationObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    iteration_id: str
    route_id: str
    route_title: str
    route_snapshot: Dict[str, Any] = Field(default_factory=dict)
    compilation_status: str
    compilation_rationale: str = ""
    compilation_errors: Tuple[str, ...] = ()
    run_status: str
    physically_valid_candidate_count: int = 0
    best_target_score: float | None = None
    best_frozen_score: float | None = None
    best_frozen_candidate_ids: Tuple[str, ...] = ()
    score_basis: Literal["legacy_target", "frozen_standard"] = "legacy_target"
    score_basis_fingerprint: str | None = None
    score_available: bool | None = None
    best_robustness_score: float | None = None
    selected_candidate_ids: Tuple[str, ...] = ()
    failure_categories: Tuple[str, ...] = ()
    experiment_summaries: Tuple[Dict[str, Any], ...] = ()
    candidate_summaries: Tuple[Dict[str, Any], ...] = ()
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    work_dir: str
    task_path: str | None = None
    result_path: str | None = None

    @property
    def decision_score(self) -> float | None:
        return decision_score(self)


def _finite_score(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def decision_score(
    observation: ResearchIterationObservation | Mapping[str, Any],
) -> float | None:
    row = (
        {
            field: getattr(observation, field)
            for field in (
                "score_basis", "score_available", "score_basis_fingerprint",
                "best_frozen_score", "best_target_score",
            )
        }
        if isinstance(observation, ResearchIterationObservation) else observation
    )
    basis = row.get("score_basis", "legacy_target")
    if basis == "frozen_standard":
        if not row.get("score_available") or not row.get("score_basis_fingerprint"):
            return None
        return _finite_score(row.get("best_frozen_score"))
    if basis == "legacy_target":
        return _finite_score(row.get("best_target_score"))
    return None


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


def _legacy_objective_fingerprint(compiled_task: Mapping[str, Any] | None) -> str | None:
    contracts = []
    for experiment in (compiled_task or {}).get("experiments", []) or []:
        if not isinstance(experiment, Mapping):
            return None
        raw_task = dict(experiment.get("tmm_task") or {})
        simulation = dict(raw_task.get("simulation") or raw_task)
        if not experiment.get("objectives") and not raw_task.get("targets"):
            return None
        contracts.append(
            {
                "mode": experiment.get("mode"),
                "objectives": experiment.get("objectives"),
                "targets": raw_task.get("targets"),
                "simulation": {key: value for key, value in simulation.items() if key != "stack"},
            }
        )
    return _fingerprint(contracts) if contracts else None


def apply_scoring_standard(
    observation: ResearchIterationObservation,
    standard: ScoringStandard,
) -> ResearchIterationObservation:
    fingerprint = _fingerprint(standard.model_dump(mode="json"))
    candidates = []
    scores = []
    for candidate in observation.candidate_summaries:
        outcome = standard.score(candidate.get("objective_report") or {})
        score = _finite_score(outcome.value) if outcome.ok else None
        candidates.append(
            {
                **candidate,
                "frozen_score": score,
                "frozen_score_outcome": outcome.model_dump(mode="json"),
                "score_basis_fingerprint": fingerprint,
            }
        )
        if score is not None:
            scores.append(score)
    best = max(scores, default=None)
    experiments = tuple(
        {
            **experiment,
            "best_frozen_score": max(
                (
                    candidate["frozen_score"]
                    for candidate in candidates
                    if candidate.get("experiment_id") == experiment.get("experiment_id")
                    and candidate["frozen_score"] is not None
                ),
                default=None,
            ),
        }
        for experiment in observation.experiment_summaries
    )
    return observation.model_copy(
        update={
            "best_frozen_score": best,
            "best_frozen_candidate_ids": tuple(
                dict.fromkeys(
                    str(candidate["candidate_id"])
                    for candidate in candidates
                    if best is not None and candidate["frozen_score"] == best
                    and candidate.get("candidate_id")
                )
            ),
            "score_basis": "frozen_standard",
            "score_basis_fingerprint": fingerprint,
            "score_available": best is not None,
            "candidate_summaries": tuple(candidates),
            "experiment_summaries": experiments,
        }
    )


class ResearchFeedbackDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal[
        "try_next_route",
        "refine_route",
        "research_more",
        "stop_completed",
        "stop_best_effort",
        "needs_higher_fidelity",
    ]
    reason: str
    observed_improvement: float | None = None
    remaining_headroom: float | None = None
    preserve_candidate_ids: Tuple[str, ...] = ()
    feedback_for_planner: Tuple[str, ...] = ()


def observation_from_run_result(
    *,
    iteration_id: str,
    route_id: str,
    route_title: str,
    compilation_status: str,
    compilation_rationale: str = "",
    compilation_errors: Iterable[str] = (),
    run_result: Mapping[str, Any] | None,
    work_dir: str,
    task_path: str | None = None,
    result_path: str | None = None,
    compiled_task: Mapping[str, Any] | None = None,
    scoring_standard: ScoringStandard | None = None,
) -> ResearchIterationObservation:
    payload = dict(run_result or {})
    scores: list[float] = []
    robust: list[float] = []
    selected: list[str] = []
    summaries: list[dict[str, Any]] = []
    candidate_summaries: list[dict[str, Any]] = []
    valid_count = 0
    experiment_stacks: dict[str, dict[str, Any]] = {}
    for experiment in (compiled_task or {}).get("experiments", []) or []:
        if not isinstance(experiment, Mapping):
            continue
        raw_task = dict(experiment.get("tmm_task") or {})
        simulation = dict(raw_task.get("simulation") or raw_task)
        stack = dict(simulation.get("stack") or {})
        layers = [dict(item) for item in stack.get("layers", []) or [] if isinstance(item, Mapping)]
        experiment_stacks[str(experiment.get("experiment_id") or "")] = {
            "layer_materials": [
                str(item.get("material") or f"n={item.get('constant_n')}")
                for item in layers
            ],
            "layer_labels": [str(item.get("label") or "") for item in layers],
            "incident_medium": dict(stack.get("incident") or {}),
            "exit_medium": dict(stack.get("exit") or {}),
        }
    for experiment in payload.get("experiment_results", []) or []:
        if not isinstance(experiment, Mapping):
            continue
        portfolio = dict(experiment.get("portfolio") or {})
        candidates = [
            dict(item)
            for item in portfolio.get("candidates", []) or []
            if isinstance(item, Mapping)
        ]
        valid_count += int(experiment.get("physically_valid_candidate_count", 0) or 0)
        for candidate in candidates:
            if not candidate.get("physically_admissible"):
                continue
            if candidate.get("target_score") is not None:
                scores.append(float(candidate["target_score"]))
            if candidate.get("robustness_score") is not None:
                robust.append(float(candidate["robustness_score"]))
            metadata = dict(candidate.get("metadata") or {})
            stack_summary = experiment_stacks.get(
                str(experiment.get("experiment_id") or ""), {}
            )
            candidate_summaries.append(
                {
                    "candidate_id": str(candidate.get("candidate_id") or ""),
                    "experiment_id": str(experiment.get("experiment_id") or ""),
                    "target_score": candidate.get("target_score"),
                    "robustness_score": candidate.get("robustness_score"),
                    "simplicity_score": candidate.get("simplicity_score"),
                    "distinctiveness_score": candidate.get("distinctiveness_score"),
                    "thicknesses_nm": list(metadata.get("thicknesses_nm") or []),
                    "optimizer_id": metadata.get("optimizer_id"),
                    "objective_report": dict(metadata.get("objective_report") or {}),
                    "robustness_report": dict(metadata.get("robustness_report") or {}),
                    "certificate_id": candidate.get("certificate_id"),
                    "artifact_ids": list(candidate.get("artifact_ids") or []),
                    **stack_summary,
                }
            )
        selected.extend(str(value) for value in (portfolio.get("selected_roles") or {}).values())
        summaries.append(
            {
                "experiment_id": str(experiment.get("experiment_id") or ""),
                "mode": str(experiment.get("mode") or ""),
                "physically_valid_candidate_count": int(
                    experiment.get("physically_valid_candidate_count", 0) or 0
                ),
                "best_target_score": max(
                    (
                        float(item["target_score"])
                        for item in candidates
                        if item.get("physically_admissible")
                        and item.get("target_score") is not None
                    ),
                    default=None,
                ),
                "selected_roles": dict(portfolio.get("selected_roles") or {}),
            }
        )
    categories: list[str] = []
    for diagnosis in payload.get("diagnoses", []) or []:
        if isinstance(diagnosis, Mapping) and diagnosis.get("category"):
            categories.append(str(diagnosis["category"]))
    stop_reason = str((payload.get("stop_decision") or {}).get("reason") or "")
    stop_reason_categories = {
        "material_resolution_failed": "material_data",
        "outside_tmm_only_domain": "outside_tmm_domain",
        "no_candidate_passed_physics_verification": "physics_violation",
        # Running out of evaluation budget is a resource limit, not a broken
        # environment.  Calling it "runtime_environment" made a run that spent
        # its whole wall clock failing physics checks look like an
        # infrastructure fault, and pointed the loop's literature search at
        # stack executability instead of at the physics that actually failed.
        "budget_exhausted": "budget_exhausted",
    }
    if stop_reason in stop_reason_categories:
        categories.append(stop_reason_categories[stop_reason])
    observation = ResearchIterationObservation(
        iteration_id=iteration_id,
        route_id=route_id,
        route_title=route_title,
        compilation_status=compilation_status,
        compilation_rationale=str(compilation_rationale or ""),
        compilation_errors=tuple(str(item) for item in compilation_errors),
        run_status=str(payload.get("status") or "not_run"),
        physically_valid_candidate_count=valid_count,
        best_target_score=max(scores, default=None),
        score_basis_fingerprint=_legacy_objective_fingerprint(compiled_task),
        score_available=any(_finite_score(value) is not None for value in scores),
        best_robustness_score=max(robust, default=None),
        selected_candidate_ids=tuple(dict.fromkeys(selected)),
        failure_categories=tuple(dict.fromkeys(categories)),
        experiment_summaries=tuple(summaries),
        candidate_summaries=tuple(candidate_summaries),
        budget_usage=dict(
            (payload.get("budget") or {}).get("measured_usage")
            or (payload.get("budget") or {}).get("usage")
            or {}
        ),
        work_dir=work_dir,
        task_path=task_path,
        result_path=result_path,
    )
    return (
        apply_scoring_standard(observation, scoring_standard)
        if scoring_standard is not None else observation
    )


def acceptance_summary(
    observations: Iterable[ResearchIterationObservation],
    route_failures: Iterable[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    rows = list(observations)
    terminal_failures = [dict(item) for item in route_failures]
    verified = [row.iteration_id for row in rows if row.physically_valid_candidate_count > 0]
    failed = [row.iteration_id for row in rows if row.compilation_status != "compiled"
              or row.run_status not in {"completed", "completed_best_effort"}
              or row.physically_valid_candidate_count <= 0]
    return {
        "acceptance_status": "partial" if verified and (failed or terminal_failures) else "accepted" if verified else "unverified",
        "verified_iteration_ids": verified,
        "failed_iteration_ids": failed,
        "route_failures": terminal_failures,
    }


class DeterministicResearchFeedbackController:
    """Choose the next legal research action from compact verified outcomes."""

    def __init__(
        self,
        *,
        meaningful_improvement: float = 0.01,
        refinement_headroom: float = 0.05,
        maximum_refinement_rounds: int = 1,
        maximum_research_rounds: int = 2,
    ) -> None:
        self.meaningful_improvement = max(0.0, float(meaningful_improvement))
        self.refinement_headroom = max(0.0, float(refinement_headroom))
        self.maximum_refinement_rounds = max(0, int(maximum_refinement_rounds))
        self.maximum_research_rounds = max(1, int(maximum_research_rounds))

    def decide(
        self,
        history: Iterable[ResearchIterationObservation],
        *,
        untried_route_count: int,
        refinement_rounds_used: int,
        research_rounds_used: int,
        budget_remaining: bool,
        # R-06 tournament scheduling: when the caller supplies the calling
        # route's own consumed rounds and cap, the refine-vs-enumerate
        # priority below keys on the PER-ROUTE round budget instead of the
        # legacy global refinement counter. Omitted (legacy callers/tests)
        # means the historical global semantics apply unchanged.
        route_rounds_used: int | None = None,
        max_rounds_per_route: int | None = None,
    ) -> ResearchFeedbackDecision:
        rows = list(history)
        if not rows:
            return ResearchFeedbackDecision(
                action="research_more",
                reason="No executable route observation exists.",
                feedback_for_planner=("Find at least one TMM-compatible method route.",),
            )
        current = rows[-1]
        preserve = tuple(
            dict.fromkeys(
                candidate
                for row in rows
                for candidate in (*row.selected_candidate_ids, *row.best_frozen_candidate_ids)
            )
        )
        if current.run_status == "needs_higher_fidelity" or "outside_tmm_domain" in current.failure_categories:
            return ResearchFeedbackDecision(
                action="needs_higher_fidelity",
                reason="The requested physics is outside the declared TMM capability boundary.",
                preserve_candidate_ids=preserve,
            )
        if current.compilation_status != "compiled":
            if untried_route_count > 0 and budget_remaining:
                return ResearchFeedbackDecision(
                    action="try_next_route",
                    reason="The current route could not be compiled; another independent route remains.",
                    preserve_candidate_ids=preserve,
                )
            if research_rounds_used < self.maximum_research_rounds and budget_remaining:
                diagnostics = " ".join(
                    [current.compilation_rationale, *current.compilation_errors]
                ).casefold()
                contract_repair = any(
                    marker in diagnostics
                    for marker in (
                        "integer",
                        "discrete",
                        "sellmeier",
                        "cauchy",
                        "material registry",
                        "field required",
                        "layer count",
                        "topology",
                    )
                )
                if (
                    contract_repair
                    and refinement_rounds_used < self.maximum_refinement_rounds
                ):
                    return ResearchFeedbackDecision(
                        action="refine_route",
                        reason=(
                            "Compilation diagnostics identify a contract-shaping problem; "
                            "repair the route before spending another literature round."
                        ),
                        preserve_candidate_ids=preserve,
                        feedback_for_planner=(
                            "Use one fixed explicit layer count per route and optimize thicknesses only.",
                            "Use named material-registry identifiers without requesting dispersion coefficients.",
                            f"Compiler diagnostic: {current.compilation_rationale[:500]}",
                        ),
                    )
                return ResearchFeedbackDecision(
                    action="research_more",
                    reason="No remaining route can be compiled; method research may supply a legal alternative.",
                    preserve_candidate_ids=preserve,
                    feedback_for_planner=("Find a route expressible with the current TMM contract.",),
                )
            return ResearchFeedbackDecision(
                action="stop_best_effort",
                reason="No route compiled within the available research budget.",
                preserve_candidate_ids=preserve,
            )
        if current.physically_valid_candidate_count <= 0:
            if untried_route_count > 0 and budget_remaining:
                return ResearchFeedbackDecision(
                    action="try_next_route",
                    reason="The current route produced no physically valid candidate; try a different structure family.",
                    preserve_candidate_ids=preserve,
                    feedback_for_planner=(
                        "Avoid repeating the failed material/topology combination.",
                    ),
                )
            earlier_valid = any(
                row.physically_valid_candidate_count > 0 for row in rows[:-1]
            )
            if earlier_valid:
                return ResearchFeedbackDecision(
                    action="stop_completed",
                    reason=(
                        "The complementary route failed, but an earlier physically "
                        "verified portfolio is preserved; additional broad research "
                        "is not justified by this route-local failure."
                    ),
                    preserve_candidate_ids=preserve,
                )
            if research_rounds_used < self.maximum_research_rounds and budget_remaining:
                return ResearchFeedbackDecision(
                    action="research_more",
                    reason="All current routes failed physical validation; seek a different method family.",
                    preserve_candidate_ids=preserve,
                    feedback_for_planner=(
                        "Search literature for alternative TMM-compatible structures and known failure modes.",
                    ),
                )
            return ResearchFeedbackDecision(
                action="stop_best_effort",
                reason="No physically valid candidate was found within the bounded search.",
                preserve_candidate_ids=preserve,
            )
        optimization_present = any(
            str(item.get("mode") or "") == "optimize"
            for item in current.experiment_summaries
        )
        if not optimization_present:
            return ResearchFeedbackDecision(
                action="stop_completed",
                reason="The report-only route completed physics verification; no optimizer refinement is applicable.",
                preserve_candidate_ids=preserve,
            )
        same_route_scores = [
            score
            for row in rows[:-1]
            if row.route_id == current.route_id
            and current.score_basis_fingerprint is not None
            and row.score_basis == current.score_basis
            and row.score_basis_fingerprint == current.score_basis_fingerprint
            and row.physically_valid_candidate_count > 0
            if (score := decision_score(row)) is not None
        ]
        current_score = decision_score(current)
        previous_best = max(same_route_scores, default=None)
        improvement = (
            None
            if current_score is None or previous_best is None
            else float(current_score - previous_best)
        )
        frozen_score = current.score_basis == "frozen_standard"
        headroom = (
            None if frozen_score or current_score is None
            else max(0.0, 1.0 - current_score)
        )
        # R-06 reorder: an iterating route with ranking headroom refines
        # BEFORE any untried portfolio member is enumerated. The old order
        # (try_next_route first) reduced every run to exhaustive enumeration
        # with at most one real continuation — the exact behaviour the
        # lineage.json note honestly reported as "not a revision of the
        # parent task".
        if route_rounds_used is not None and max_rounds_per_route is not None:
            rounds_budget_left = int(route_rounds_used) < int(max_rounds_per_route)
        else:
            rounds_budget_left = refinement_rounds_used < self.maximum_refinement_rounds
        if (
            budget_remaining
            and rounds_budget_left
            and current_score is not None
            and (frozen_score or (headroom is not None and headroom >= self.refinement_headroom))
            and (improvement is None or improvement >= self.meaningful_improvement)
        ):
            return ResearchFeedbackDecision(
                action="refine_route",
                reason=(
                    "The verified route has an available frozen score and remaining budget for one bounded refinement."
                    if frozen_score else
                    "The verified route still has ranking headroom and recent search progress justifies one bounded refinement."
                ),
                observed_improvement=improvement,
                remaining_headroom=headroom,
                preserve_candidate_ids=preserve,
                feedback_for_planner=(
                    "Preserve the verified scientific principle.",
                    "Change only design choices that can plausibly improve the frozen score."
                    if frozen_score else
                    "Change only design choices that can plausibly address the remaining objective headroom.",
                ),
            )
        if untried_route_count > 0 and budget_remaining:
            return ResearchFeedbackDecision(
                action="try_next_route",
                reason="A valid candidate exists, but an already planned complementary route remains within budget.",
                preserve_candidate_ids=preserve,
            )
        reason = (
            "The physically verified candidates have no available decision score; score-based refinement cannot be justified."
            if current_score is None
            else (
                "Further bounded search is unlikely to add material information."
                if budget_remaining
                else (
                    "The bounded route portfolio is complete; preserve the best "
                    "verified performance, robustness, and simplicity trade-offs."
                )
            )
        )
        return ResearchFeedbackDecision(
            action="stop_completed",
            reason=reason,
            observed_improvement=improvement,
            remaining_headroom=headroom,
            preserve_candidate_ids=preserve,
        )


__all__ = [
    "DeterministicResearchFeedbackController",
    "ResearchFeedbackDecision",
    "ResearchIterationObservation",
    "apply_scoring_standard",
    "decision_score",
    "observation_from_run_result",
]
