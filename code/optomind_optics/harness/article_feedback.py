"""Stage 7: deterministic observation-driven hypothesis updates and route
scheduling for the Article Scientific Harness.

The controller consumes a validated ``ArticleDirectorPlan`` (hypotheses +
coverage) and an ordered history of trusted ``ObservationCard`` records
(Stage 6).  It is fully deterministic and requires no LLM: hypothesis status
updates and stop decisions come only from explicit program rules over
observation status, declared evidence kind, and trusted metrics.

Scientific rules:
- A ``physically_valid`` observation only proves that a declared task ran.  It
  may support ``active``/``under_test``/``partially_supported`` transitions
  only when explicit evidence is present.  Confirmation requires a declared
  ``discriminator_confirmed`` evidence kind whose discriminator is actually
  represented in the observation metrics (``discriminator_match`` + metric
  key presence).  Refutation requires an explicit disconfirming observation
  (declared ``disconfirming`` kind with ``discriminator_match.matched ==
  False``).  There is no hidden semantic inference.
- ``rejected_physics``/``failed``/``needs_higher_fidelity``/``cancelled`` are
  execution outcomes, not scientific refutations: with explicit
  ``execution_failure`` evidence they map to ``under_test``/``active``, never
  ``confirmed``/``refuted``.
- Transitions follow ``HypothesisStatus`` with a documented forward-only map;
  terminal states (``confirmed``/``refuted``/``superseded``/``retired``)
  cannot move backward.

Route scheduling is bounded (default one next route) in coverage order and
never reschedules completed/superseded routes.  Stop decisions are explicit
``ArticleDecision`` values.  Unknown IDs, illegal transitions, inconsistent
observation order, or persistence failures fail closed before any partial
persistence; optional ``ArticleMemoryStore``/``ExperimentGraph`` writes are
append-only with stable IDs and duplicate detection (idempotent retry).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from optomind_optics.harness.article_contracts import (
    ARTICLE_EVENT_SCHEMA_VERSION,
    ArticleDecision,
    ArticleNodePayload,
    ArticleStage,
    CoverageStatus,
    ExperimentCard,
    HypothesisCard,
    HypothesisStatus,
    ObservationCard,
    validate_article_event,
)
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_memory import (
    ArticleMemoryStore,
    DuplicateRecordError,
    RunMemoryRecord,
)
from optomind_optics.harness.contracts import ExperimentStatus
from optomind_optics.harness.experiment_graph import ExperimentGraph
from optomind_research.runtime.artifact_store import atomic_write_json


FEEDBACK_SCHEMA_VERSION = "article-feedback-result.v1"
FEEDBACK_CONTROLLER_VERSION = "article-feedback-controller.v1"


class ArticleFeedbackError(ValueError):
    """Raised when persistence fails or inputs are irrecoverably inconsistent."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HypothesisUpdateDecision(_StrictModel):
    schema_version: Literal["hypothesis-update-decision.v1"] = (
        "hypothesis-update-decision.v1"
    )
    hypothesis_id: str
    from_status: HypothesisStatus
    to_status: HypothesisStatus
    reason: str
    observation_ids: List[str] = Field(default_factory=list)
    experiment_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    route_ids: List[str] = Field(default_factory=list)
    evidence_summary: str = ""


class CoverageUpdate(_StrictModel):
    schema_version: Literal["coverage-update.v1"] = "coverage-update.v1"
    route_id: str
    from_status: CoverageStatus
    to_status: CoverageStatus
    reason: str
    observation_ids: List[str] = Field(default_factory=list)


class RouteSchedule(_StrictModel):
    schema_version: Literal["route-schedule.v1"] = "route-schedule.v1"
    route_id: str
    stage: ArticleStage
    priority: int
    reason: str


class ArticleFeedbackResult(_StrictModel):
    schema_version: Literal["article-feedback-result.v1"] = (
        "article-feedback-result.v1"
    )
    controller_id: str
    hypothesis_updates: List[HypothesisUpdateDecision] = Field(default_factory=list)
    coverage_updates: List[CoverageUpdate] = Field(default_factory=list)
    next_routes: List[RouteSchedule] = Field(default_factory=list)
    stop_decision: ArticleDecision
    stop_reason: str
    provenance_observation_ids: List[str] = Field(default_factory=list)
    normalization_warnings: List[str] = Field(default_factory=list)
    validation_errors: List[str] = Field(default_factory=list)
    progress_state: Dict[str, int] = Field(default_factory=dict)


class ObservationContext(_StrictModel):
    """Trusted experiment context binding one experiment to hypotheses/route.

    Accepts an ``ExperimentCard`` or an equivalent mapping carrying
    ``experiment_id``, ``hypothesis_ids``, ``route_id``, and the expected
    discriminator contract.  The controller validates observation identity and
    evidence against this context instead of trusting arbitrary upstream
    ``to_status`` values.
    """

    schema_version: Literal["observation-context.v1"] = "observation-context.v1"
    experiment_id: str
    hypothesis_ids: List[str] = Field(min_length=1)
    route_id: str
    expected_discriminator: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("hypothesis_ids")
    @classmethod
    def _unique_hypotheses(cls, values: List[str]) -> List[str]:
        cleaned = [str(item).strip() for item in values if str(item).strip()]
        if not cleaned:
            raise ValueError("hypothesis_ids must not be empty")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("hypothesis_ids must be unique")
        return cleaned


_TERMINAL_HYPOTHESIS_STATUSES = frozenset(
    {
        HypothesisStatus.confirmed,
        HypothesisStatus.refuted,
        HypothesisStatus.superseded,
        HypothesisStatus.retired,
    }
)

_LEGAL_TRANSITIONS: Dict[HypothesisStatus, frozenset[HypothesisStatus]] = {
    HypothesisStatus.proposed: frozenset(
        {HypothesisStatus.active, HypothesisStatus.under_test, HypothesisStatus.partially_supported}
    ),
    HypothesisStatus.active: frozenset(
        {
            HypothesisStatus.under_test,
            HypothesisStatus.partially_supported,
            HypothesisStatus.confirmed,
            HypothesisStatus.refuted,
        }
    ),
    HypothesisStatus.under_test: frozenset(
        {
            HypothesisStatus.partially_supported,
            HypothesisStatus.confirmed,
            HypothesisStatus.refuted,
        }
    ),
    HypothesisStatus.partially_supported: frozenset(
        {HypothesisStatus.confirmed, HypothesisStatus.refuted, HypothesisStatus.under_test}
    ),
    HypothesisStatus.confirmed: frozenset(),
    HypothesisStatus.refuted: frozenset(),
    HypothesisStatus.superseded: frozenset(),
    HypothesisStatus.retired: frozenset(),
}

_ROUTE_STAGE: Dict[str, ArticleStage] = {
    "baseline": ArticleStage.baseline_experiments,
    "exploration": ArticleStage.exploration,
    "controlled_improvement": ArticleStage.controlled_improvement,
    "discriminative_experiments": ArticleStage.discriminative_experiments,
    "robustness_ablation": ArticleStage.robustness_ablation,
    "fresh_replay": ArticleStage.fresh_replay,
}

_ROUTE_FROM_STAGE: Dict[ArticleStage, str] = {
    stage: route for route, stage in _ROUTE_STAGE.items()
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _scoped_progress(
    plan_id: str, counters: Mapping[str, int]
) -> Dict[str, int]:
    """Current-plan progress counters only (ignore stale other-plan keys)."""

    prefix = f"{plan_id}:"
    return {
        key: int(value)
        for key, value in counters.items()
        if str(key).startswith(prefix)
    }


def _coverage_target(status: ExperimentStatus) -> CoverageStatus:
    if status == ExperimentStatus.physically_valid:
        return CoverageStatus.completed
    if status in {ExperimentStatus.rejected_physics, ExperimentStatus.failed}:
        return CoverageStatus.failed
    return CoverageStatus.not_run  # needs_higher_fidelity / cancelled


def _coverage_reason(observation: ObservationCard) -> str:
    if observation.status == ExperimentStatus.physically_valid:
        return "route executed with physically valid candidates"
    if observation.status == ExperimentStatus.rejected_physics:
        return "route completed without physically valid candidates"
    if observation.status == ExperimentStatus.failed:
        return "route run failed"
    if observation.status == ExperimentStatus.cancelled:
        return "route run cancelled"
    return f"route outcome: {observation.status.value}"


class ArticleFeedbackController:
    """Deterministic hypothesis/coverage/route feedback from observations."""

    def __init__(
        self,
        *,
        max_next_routes: int = 1,
        max_no_progress: int = 3,
    ) -> None:
        if int(max_next_routes) < 1:
            raise ValueError("max_next_routes must be at least 1")
        if int(max_no_progress) < 1:
            raise ValueError("max_no_progress must be at least 1")
        self.max_next_routes = int(max_next_routes)
        self.max_no_progress = int(max_no_progress)
        # Per-hypothesis consecutive no-progress round counters (keyed by
        # plan_id + hypothesis_id) so progress is tracked across update calls.
        self._no_progress_counters: Dict[str, int] = {}

    def update(
        self,
        plan: ArticleDirectorPlan | Mapping[str, Any],
        observations: Sequence[ObservationCard | Mapping[str, Any]] = (),
        *,
        experiment_context: ExperimentCard | Mapping[str, Any] | ObservationContext | None = None,
        existing_hypotheses: Sequence[HypothesisCard | Mapping[str, Any]] = (),
        budget_exhausted: bool = False,
        memory_store: ArticleMemoryStore | None = None,
        graph: ExperimentGraph | None = None,
        run_id: Optional[str] = None,
        journal_path: str | Path | None = None,
        progress_state: Optional[Mapping[str, int]] = None,
    ) -> ArticleFeedbackResult:
        errors: List[str] = []
        warnings: List[str] = []
        if progress_state is not None:
            self._no_progress_counters = {
                str(key): int(value) for key, value in progress_state.items()
            }
        elif journal_path is not None:
            journal = _read_journal(journal_path)
            stored = journal.get("progress_state")
            if isinstance(stored, Mapping):
                self._no_progress_counters = {
                    str(key): int(value) for key, value in stored.items()
                }
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
            return self._hard_blocker(errors, warnings, controller_id="invalid-plan")

        observation_models: List[ObservationCard] = []
        for index, raw in enumerate(observations):
            try:
                observation = (
                    raw
                    if isinstance(raw, ObservationCard)
                    else ObservationCard.model_validate(raw)
                )
            except ValidationError as exc:
                errors.append(f"observations[{index}] is invalid: {exc}")
                continue
            observation_models.append(observation)

        if not errors:
            timestamps = [item.created_at for item in observation_models if item.created_at]
            if any(
                left is not None and right is not None and left > right
                for left, right in zip(timestamps, timestamps[1:])
            ):
                errors.append("observation order is inconsistent (created_at not non-decreasing)")

        if errors:
            controller_id = self._controller_id(plan_model, observation_models, {}, bool(budget_exhausted))
            return self._hard_blocker(errors, warnings, controller_id=controller_id)

        context = self._normalize_context(plan_model, experiment_context, errors)
        if errors:
            controller_id = self._controller_id(plan_model, observation_models, {}, bool(budget_exhausted))
            return self._hard_blocker(errors, warnings, controller_id=controller_id)

        current = self._current_hypothesis_statuses(plan_model, existing_hypotheses, errors)
        progress_before = _scoped_progress(
            plan_model.plan_id, self._no_progress_counters
        )
        if errors:
            controller_id = self._controller_id(
                plan_model,
                observation_models,
                {},
                bool(budget_exhausted),
                progress_before=progress_before,
            )
            return self._hard_blocker(errors, warnings, controller_id=controller_id)

        hypothesis_decisions: List[HypothesisUpdateDecision] = []
        coverage_decisions: List[CoverageUpdate] = []
        coverage_state: Dict[str, CoverageStatus] = {
            row.route_id: row.coverage_status for row in plan_model.coverage_matrix.rows
        }
        provenance_ids: List[str] = []

        for observation in observation_models:
            provenance_ids.append(observation.observation_id)
            route_id = self._observation_route(observation, context)
            if context is not None and observation.experiment_id != context.experiment_id:
                errors.append(
                    f"observation {observation.observation_id} experiment_id "
                    f"{observation.experiment_id!r} does not match context "
                    f"{context.experiment_id!r}"
                )
                continue
            metrics_route = (
                str(observation.metrics.get("route_id") or "").strip()
                if isinstance(observation.metrics, Mapping)
                else ""
            )
            if (
                context is not None
                and metrics_route
                and metrics_route != context.route_id
            ):
                errors.append(
                    f"observation {observation.observation_id} route_id "
                    f"{metrics_route!r} does not match experiment context "
                    f"route {context.route_id!r}"
                )
                continue
            if route_id:
                if route_id not in coverage_state:
                    errors.append(
                        f"observation {observation.observation_id} references "
                        f"unknown route {route_id!r}"
                    )
                else:
                    from_status = coverage_state[route_id]
                    if from_status not in {
                        CoverageStatus.completed,
                        CoverageStatus.superseded,
                    }:
                        to_status = _coverage_target(observation.status)
                        coverage_state[route_id] = to_status
                        coverage_decisions.append(
                            CoverageUpdate(
                                route_id=route_id,
                                from_status=from_status,
                                to_status=to_status,
                                reason=_coverage_reason(observation),
                                observation_ids=[observation.observation_id],
                            )
                        )
            else:
                warnings.append(
                    f"observation {observation.observation_id} has no route_id; "
                    "coverage not updated"
                )

            entries = list(observation.hypothesis_updates)
            if not entries and context is not None:
                entries = self._auto_evidence_entries(plan_model, observation, context)
            for entry in entries:
                decision = self._evaluate_hypothesis_evidence(
                    plan_model,
                    observation,
                    entry,
                    current,
                    route_id,
                    context,
                )
                if isinstance(decision, str):
                    errors.append(decision)
                    continue
                hypothesis_decisions.append(decision)
                current[decision.hypothesis_id] = decision.to_status

        if errors:
            controller_id = self._controller_id(
                plan_model,
                observation_models,
                current,
                bool(budget_exhausted),
                progress_before=progress_before,
            )
            return self._hard_blocker(errors, warnings, controller_id=controller_id)

        counters_before_round = dict(self._no_progress_counters)
        no_progress_count = self._advance_no_progress_counters(
            plan_model, hypothesis_decisions
        )
        progress_after = _scoped_progress(
            plan_model.plan_id, self._no_progress_counters
        )
        next_routes = self._schedule_routes(
            plan_model, coverage_state, current, observation_models, context
        )
        stop_decision, stop_reason = self._decide_stop(
            budget_exhausted=bool(budget_exhausted),
            next_routes=next_routes,
            coverage_state=coverage_state,
            no_progress_count=no_progress_count,
            plan=plan_model,
        )
        controller_id = self._controller_id(
            plan_model,
            observation_models,
            current,
            bool(budget_exhausted),
            progress_before=progress_before,
            progress_after=progress_after,
        )
        result = ArticleFeedbackResult(
            controller_id=controller_id,
            hypothesis_updates=hypothesis_decisions,
            coverage_updates=coverage_decisions,
            next_routes=next_routes,
            stop_decision=stop_decision,
            stop_reason=stop_reason,
            provenance_observation_ids=provenance_ids,
            normalization_warnings=warnings,
            progress_state=progress_after,
        )
        if memory_store is not None or graph is not None or journal_path is not None:
            try:
                self._persist(
                    controller_id=controller_id,
                    result=result,
                    memory_store=memory_store,
                    graph=graph,
                    run_id=str(run_id or ""),
                    journal_path=journal_path,
                    progress_before=progress_before,
                    progress_after=progress_after,
                )
            except ArticleFeedbackError:
                self._no_progress_counters = counters_before_round
                raise
            except Exception as exc:
                self._no_progress_counters = counters_before_round
                raise ArticleFeedbackError(
                    f"persistence failed: {exc}"
                ) from exc
        return result

    # -- deterministic decision helpers --------------------------------------

    @staticmethod
    def _hard_blocker(
        errors: Sequence[str],
        warnings: Sequence[str],
        *,
        controller_id: str,
    ) -> ArticleFeedbackResult:
        return ArticleFeedbackResult(
            controller_id=controller_id,
            stop_decision=ArticleDecision.stop_hard_blocker,
            stop_reason="; ".join(str(item) for item in errors) or "invalid input",
            normalization_warnings=[str(item) for item in warnings],
            validation_errors=[str(item) for item in errors],
        )

    @staticmethod
    def _controller_id(
        plan: ArticleDirectorPlan,
        observations: Sequence[ObservationCard],
        current: Mapping[str, HypothesisStatus],
        budget_exhausted: bool,
        progress_before: Optional[Mapping[str, int]] = None,
        progress_after: Optional[Mapping[str, int]] = None,
    ) -> str:
        payload = {
            "plan_id": plan.plan_id,
            "observations": [
                {
                    "observation_id": item.observation_id,
                    "status": item.status.value,
                    "metrics": item.metrics,
                }
                for item in observations
            ],
            "hypothesis_statuses": {
                key: value.value for key, value in sorted(current.items())
            },
            "budget_exhausted": bool(budget_exhausted),
            "progress_before": _scoped_progress(plan.plan_id, progress_before or {}),
            "progress_after": _scoped_progress(plan.plan_id, progress_after or {}),
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _current_hypothesis_statuses(
        plan: ArticleDirectorPlan,
        existing_hypotheses: Sequence[Any],
        errors: List[str],
    ) -> Dict[str, HypothesisStatus]:
        current = {
            item.hypothesis_id: HypothesisStatus.proposed
            for item in plan.hypotheses
        }
        for raw in existing_hypotheses:
            try:
                card = (
                    raw
                    if isinstance(raw, HypothesisCard)
                    else HypothesisCard.model_validate(raw)
                )
            except ValidationError as exc:
                errors.append(f"existing hypothesis is invalid: {exc}")
                continue
            if card.hypothesis_id not in current:
                errors.append(
                    f"existing hypothesis {card.hypothesis_id!r} is unknown to the plan"
                )
                continue
            current[card.hypothesis_id] = card.status
        return current

    @staticmethod
    def _observation_route(
        observation: ObservationCard,
        context: Optional[ObservationContext],
    ) -> str:
        metrics = observation.metrics if isinstance(observation.metrics, Mapping) else {}
        metrics_route = str(metrics.get("route_id") or "").strip()
        if context is not None:
            if metrics_route and metrics_route != context.route_id:
                return ""  # caller reports the binding error via context validation
            return context.route_id
        return metrics_route

    @staticmethod
    def _normalize_context(
        plan: ArticleDirectorPlan,
        raw: ExperimentCard | Mapping[str, Any] | ObservationContext | None,
        errors: List[str],
    ) -> Optional[ObservationContext]:
        if raw is None:
            return None
        if isinstance(raw, ExperimentCard) or (
            isinstance(raw, Mapping) and ("task_hash" in raw or "action_type" in raw)
        ):
            try:
                card = (
                    raw
                    if isinstance(raw, ExperimentCard)
                    else ExperimentCard.model_validate(raw)
                )
            except ValidationError as exc:
                errors.append(f"experiment_context is invalid: {exc}")
                return None
            route = _ROUTE_FROM_STAGE.get(card.stage)
            if route is None:
                errors.append(
                    "experiment_context ExperimentCard has no experimental stage"
                )
                return None
            context = ObservationContext(
                experiment_id=card.experiment_id,
                hypothesis_ids=list(card.hypothesis_ids),
                route_id=route,
                expected_discriminator=dict(card.expected_discriminator),
            )
        elif isinstance(raw, ObservationContext):
            context = raw
        else:
            try:
                context = ObservationContext.model_validate(raw)
            except ValidationError as exc:
                errors.append(f"experiment_context is invalid: {exc}")
                return None
        known = {item.hypothesis_id for item in plan.hypotheses}
        unknown = sorted(set(context.hypothesis_ids) - known)
        if unknown:
            errors.append(
                f"experiment_context references unknown hypotheses: {unknown}"
            )
        coverage_ids = {row.route_id for row in plan.coverage_matrix.rows}
        if context.route_id not in coverage_ids:
            errors.append(
                f"experiment_context references unknown route {context.route_id!r}"
            )
        return context

    @staticmethod
    def _is_legal_transition(
        from_status: HypothesisStatus, to_status: HypothesisStatus
    ) -> bool:
        if to_status == from_status:
            return True
        return to_status in _LEGAL_TRANSITIONS.get(from_status, frozenset())

    def _evaluate_hypothesis_evidence(
        self,
        plan: ArticleDirectorPlan,
        observation: ObservationCard,
        entry: Any,
        current: Mapping[str, HypothesisStatus],
        route_id: str,
        context: Optional[ObservationContext],
    ) -> HypothesisUpdateDecision | str:
        if not isinstance(entry, Mapping):
            return f"observation {observation.observation_id}: hypothesis evidence must be an object"
        hypothesis_id = str(entry.get("hypothesis_id") or "").strip()
        if hypothesis_id not in current:
            return (
                f"observation {observation.observation_id} references unknown "
                f"hypothesis {hypothesis_id!r}"
            )
        if context is not None and hypothesis_id not in context.hypothesis_ids:
            return (
                f"observation {observation.observation_id}: hypothesis "
                f"{hypothesis_id!r} is not bound to experiment context "
                f"{context.experiment_id!r}"
            )
        from_status = current[hypothesis_id]
        to_raw = str(entry.get("to_status") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        kind = str(entry.get("evidence_kind") or "").strip()
        try:
            to_status = HypothesisStatus(to_raw)
        except ValueError:
            return (
                f"observation {observation.observation_id}: unknown to_status {to_raw!r}"
            )
        declared_from = entry.get("from_status")
        if declared_from is not None:
            try:
                declared = HypothesisStatus(str(declared_from))
            except ValueError:
                return (
                    f"observation {observation.observation_id}: unknown from_status "
                    f"{declared_from!r}"
                )
            if declared != from_status:
                return (
                    f"observation {observation.observation_id}: from_status mismatch "
                    f"declared {declared.value!r} != current {from_status.value!r}"
                )
        if not reason:
            return f"observation {observation.observation_id}: missing reason"
        if from_status in _TERMINAL_HYPOTHESIS_STATUSES:
            return (
                f"observation {observation.observation_id}: hypothesis "
                f"{hypothesis_id!r} is terminal ({from_status.value}) and cannot change"
            )
        if not self._is_legal_transition(from_status, to_status):
            return (
                f"observation {observation.observation_id}: illegal transition "
                f"{from_status.value} -> {to_status.value}"
            )

        metrics = observation.metrics if isinstance(observation.metrics, Mapping) else {}
        discriminator = None
        discriminator_map = metrics.get("discriminator_match")
        if isinstance(discriminator_map, Mapping):
            discriminator = discriminator_map.get(hypothesis_id)

        if kind == "partial_support":
            if observation.status != ExperimentStatus.physically_valid:
                return (
                    f"observation {observation.observation_id}: partial support "
                    "requires a physically valid observation"
                )
            if to_status not in {
                HypothesisStatus.active,
                HypothesisStatus.under_test,
                HypothesisStatus.partially_supported,
            }:
                return (
                    f"observation {observation.observation_id}: invalid target "
                    f"{to_status.value} for partial support"
                )
        elif kind == "discriminator_confirmed":
            if observation.status != ExperimentStatus.physically_valid:
                return (
                    f"observation {observation.observation_id}: confirmation "
                    "requires a physically valid observation"
                )
            if to_status != HypothesisStatus.confirmed:
                return (
                    f"observation {observation.observation_id}: discriminator "
                    "confirmation must target confirmed"
                )
            if not isinstance(discriminator, Mapping) or discriminator.get("matched") is not True:
                return (
                    f"observation {observation.observation_id}: discriminator "
                    "for the hypothesis is not represented as matched in metrics"
                )
            metric_keys = discriminator.get("metric_keys") or []
            if not metric_keys or not all(key in metrics for key in metric_keys):
                return (
                    f"observation {observation.observation_id}: discriminator "
                    "metric keys are not present in the observation metrics"
                )
            if context is None or not context.expected_discriminator:
                return (
                    f"observation {observation.observation_id}: confirmation "
                    "requires a non-empty expected discriminator in the experiment context"
                )
            expected_keys = context.expected_discriminator.get("metric_keys")
            if isinstance(expected_keys, list) and expected_keys:
                if not set(expected_keys) <= set(metric_keys):
                    return (
                        f"observation {observation.observation_id}: discriminator "
                        "metric keys do not cover the expected discriminator"
                    )
        elif kind == "disconfirming":
            if observation.status != ExperimentStatus.physically_valid:
                return (
                    f"observation {observation.observation_id}: disconfirmation "
                    "requires a physically valid observation"
                )
            if to_status != HypothesisStatus.refuted:
                return (
                    f"observation {observation.observation_id}: disconfirming "
                    "evidence must target refuted"
                )
            if not isinstance(discriminator, Mapping) or discriminator.get("matched") is not False:
                return (
                    f"observation {observation.observation_id}: no explicit "
                    "disconfirming observation is represented in metrics"
                )
        elif kind == "execution_failure":
            if observation.status not in {
                ExperimentStatus.rejected_physics,
                ExperimentStatus.failed,
                ExperimentStatus.needs_higher_fidelity,
                ExperimentStatus.cancelled,
            }:
                return (
                    f"observation {observation.observation_id}: execution_failure "
                    "evidence requires a non-success observation"
                )
            if to_status not in {
                HypothesisStatus.under_test,
                HypothesisStatus.active,
            }:
                return (
                    f"observation {observation.observation_id}: execution "
                    "failure cannot confirm or refute; target must be under_test/active"
                )
        else:
            return (
                f"observation {observation.observation_id}: unknown evidence_kind {kind!r}"
            )

        return HypothesisUpdateDecision(
            hypothesis_id=hypothesis_id,
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            observation_ids=[observation.observation_id],
            experiment_ids=[observation.experiment_id] if observation.experiment_id else [],
            artifact_ids=list(observation.artifact_ids),
            route_ids=[route_id] if route_id else [],
            evidence_summary=f"evidence_kind={kind}",
        )

    def _auto_evidence_entries(
        self,
        plan: ArticleDirectorPlan,
        observation: ObservationCard,
        context: ObservationContext,
    ) -> List[Dict[str, Any]]:
        """Derive evidence entries from trusted metrics + experiment context."""

        metrics = observation.metrics if isinstance(observation.metrics, Mapping) else {}
        discriminator_map = metrics.get("discriminator_match")
        discriminator_map = (
            discriminator_map if isinstance(discriminator_map, Mapping) else {}
        )
        expected_metric_keys = context.expected_discriminator.get("metric_keys") or []
        entries: List[Dict[str, Any]] = []
        for hypothesis_id in context.hypothesis_ids:
            discriminator = discriminator_map.get(hypothesis_id)
            if isinstance(discriminator, Mapping):
                matched = discriminator.get("matched")
                metric_keys = discriminator.get("metric_keys") or []
                if (
                    matched is True
                    and metric_keys
                    and all(key in metrics for key in metric_keys)
                    and context.expected_discriminator
                ):
                    entries.append(
                        {
                            "hypothesis_id": hypothesis_id,
                            "to_status": "confirmed",
                            "evidence_kind": "discriminator_confirmed",
                            "reason": "declared discriminator matched in trusted metrics",
                        }
                    )
                    continue
                if (
                    matched is False
                    and observation.status == ExperimentStatus.physically_valid
                ):
                    entries.append(
                        {
                            "hypothesis_id": hypothesis_id,
                            "to_status": "refuted",
                            "evidence_kind": "disconfirming",
                            "reason": "declared discriminator did not match",
                        }
                    )
                    continue
            if observation.status == ExperimentStatus.physically_valid:
                matched_key = any(key in metrics for key in expected_metric_keys)
                hypothesis = next(
                    (
                        item
                        for item in plan.hypotheses
                        if item.hypothesis_id == hypothesis_id
                    ),
                    None,
                )
                hypothesis_key = bool(
                    hypothesis
                    and any(key in metrics for key in hypothesis.expected_observations)
                )
                if matched_key or hypothesis_key:
                    entries.append(
                        {
                            "hypothesis_id": hypothesis_id,
                            "to_status": "partially_supported",
                            "evidence_kind": "partial_support",
                            "reason": "declared observable keys present in trusted metrics",
                        }
                    )
                    continue
            else:
                entries.append(
                    {
                        "hypothesis_id": hypothesis_id,
                        "to_status": "under_test",
                        "evidence_kind": "execution_failure",
                        "reason": f"execution outcome: {observation.status.value}",
                    }
                )
        return entries

    def _advance_no_progress_counters(
        self,
        plan: ArticleDirectorPlan,
        decisions: Sequence[HypothesisUpdateDecision],
    ) -> int:
        """Per-hypothesis consecutive no-progress rounds (across update calls)."""

        by_hypothesis: Dict[str, List[HypothesisUpdateDecision]] = {}
        for decision in decisions:
            by_hypothesis.setdefault(decision.hypothesis_id, []).append(decision)
        for hypothesis_id, items in by_hypothesis.items():
            key = f"{plan.plan_id}:{hypothesis_id}"
            if any(item.to_status != item.from_status for item in items):
                self._no_progress_counters[key] = 0
            else:
                self._no_progress_counters[key] = (
                    self._no_progress_counters.get(key, 0) + 1
                )
        scoped = _scoped_progress(plan.plan_id, self._no_progress_counters)
        return max(scoped.values(), default=0)

    def _schedule_routes(
        self,
        plan: ArticleDirectorPlan,
        coverage_state: Mapping[str, CoverageStatus],
        current: Mapping[str, HypothesisStatus],
        observations: Sequence[ObservationCard],
        context: Optional[ObservationContext],
    ) -> List[RouteSchedule]:
        order = [row.route_id for row in plan.coverage_matrix.rows]
        candidates: List[Tuple[str, str]] = []

        baseline_observed = any(
            self._observation_route(item, context) == "baseline"
            for item in observations
        )
        baseline_status = coverage_state.get("baseline")
        if not baseline_observed and baseline_status == CoverageStatus.planned:
            candidates.append(("baseline", "no observation exists for the baseline route"))
        if baseline_status in {CoverageStatus.failed, CoverageStatus.not_run}:
            candidates.append(
                ("exploration", "baseline failed or produced no physically valid candidates")
            )
        if baseline_status == CoverageStatus.completed:
            candidates.append(
                ("controlled_improvement", "baseline produced physically valid candidates")
            )
        competing = sum(
            1
            for status in current.values()
            if status
            in {
                HypothesisStatus.active,
                HypothesisStatus.under_test,
                HypothesisStatus.partially_supported,
            }
        )
        if competing >= 2:
            candidates.append(
                ("discriminative_experiments", "competing hypotheses remain")
            )
        if any(
            status in {HypothesisStatus.partially_supported, HypothesisStatus.confirmed}
            for status in current.values()
        ):
            candidates.append(
                ("robustness_ablation", "a candidate hypothesis is supported")
            )

        picked: List[Tuple[str, str]] = []
        for route_id in order:
            if route_id == "fresh_replay":
                continue
            if coverage_state.get(route_id) in {
                CoverageStatus.completed,
                CoverageStatus.superseded,
            }:
                continue
            for candidate in candidates:
                if candidate[0] == route_id:
                    picked.append(candidate)
                    break
            if len(picked) >= self.max_next_routes:
                break
        return [
            RouteSchedule(
                route_id=route_id,
                stage=_ROUTE_STAGE[route_id],
                priority=index + 1,
                reason=reason,
            )
            for index, (route_id, reason) in enumerate(picked)
        ]

    def _decide_stop(
        self,
        *,
        budget_exhausted: bool,
        next_routes: Sequence[RouteSchedule],
        coverage_state: Mapping[str, CoverageStatus],
        no_progress_count: int,
        plan: ArticleDirectorPlan,
    ) -> Tuple[ArticleDecision, str]:
        if budget_exhausted:
            return ArticleDecision.stop_budget_exhausted, "caller reported budget exhaustion"
        if no_progress_count >= self.max_no_progress:
            return (
                ArticleDecision.stop_no_progress,
                f"{no_progress_count} consecutive observations without hypothesis progress",
            )
        required = [
            row.route_id
            for row in plan.coverage_matrix.rows
            if row.coverage_status == CoverageStatus.planned
            and row.route_id != "fresh_replay"
        ]
        all_done = all(
            coverage_state.get(route_id) == CoverageStatus.completed
            for route_id in required
        )
        if not next_routes:
            if all_done:
                return ArticleDecision.stop_completed, "all required routes are complete"
            return ArticleDecision.stop_route_exhausted, "no legal route remains"
        return ArticleDecision.continue_run, "proceeding to the next scheduled route"

    # -- optional persistence -------------------------------------------------

    def _persist(
        self,
        *,
        controller_id: str,
        result: ArticleFeedbackResult,
        memory_store: Optional[ArticleMemoryStore],
        graph: Optional[ExperimentGraph],
        run_id: str,
        journal_path: Optional[str | Path],
        progress_before: Mapping[str, int],
        progress_after: Mapping[str, int],
    ) -> None:
        if journal_path is None:
            # Legacy path without a recovery journal: graph then memory.  A
            # failure after the graph write leaves graph events without memory
            # records; callers that need recovery must supply a journal.
            if graph is not None:
                self._persist_graph(graph, controller_id, result)
            if memory_store is not None:
                self._persist_memory(memory_store, controller_id, result, run_id)
            return
        journal = _read_journal(journal_path)
        state = journal.get(controller_id)
        if state is not None and state.get("status") == "completed":
            return
        if state is None:
            state = {
                "status": "in_progress",
                "graph_written": graph is None,
                "memory_written": memory_store is None,
                "pending_progress_state": dict(progress_after),
            }
        try:
            if graph is not None and not state.get("graph_written"):
                self._persist_graph(graph, controller_id, result)
                state["graph_written"] = True
                _write_journal(
                    journal_path,
                    journal,
                    controller_id,
                    state,
                    progress_state=progress_before,
                )
            if memory_store is not None and not state.get("memory_written"):
                self._persist_memory(memory_store, controller_id, result, run_id)
                state["memory_written"] = True
                _write_journal(
                    journal_path,
                    journal,
                    controller_id,
                    state,
                    progress_state=progress_before,
                )
            state["status"] = "completed"
            _write_journal(
                journal_path,
                journal,
                controller_id,
                state,
                progress_state=progress_after,
            )
        except Exception as exc:
            _write_journal(
                journal_path,
                journal,
                controller_id,
                state,
                progress_state=progress_before,
            )
            raise ArticleFeedbackError(f"persistence failed: {exc}") from exc

    @staticmethod
    def _persist_graph(
        graph: ExperimentGraph,
        controller_id: str,
        result: ArticleFeedbackResult,
    ) -> None:
        node_id = f"feedback-{controller_id}"
        summary = f"feedback-{controller_id}"
        payload = ArticleNodePayload(
            stage=ArticleStage.hypothesis_update,
            hypothesis_ids=[item.hypothesis_id for item in result.hypothesis_updates],
            summary=summary,
        )
        expected_events = ArticleFeedbackController._expected_graph_events(result)
        created = False
        try:
            graph.create_article_node(payload, node_id=node_id)
            created = True
        except sqlite3.IntegrityError:
            existing = graph.article_node(node_id)
            if existing.get("payload", {}).get("summary") != summary:
                raise ArticleFeedbackError(
                    f"feedback node {node_id!r} already exists with different content"
                )
        if created:
            for event_type, event_payload in expected_events:
                ArticleFeedbackController._append_graph_event(
                    graph, node_id, event_type, event_payload
                )
            return
        # Completeness-aware replay: an existing node may be missing events
        # from a previously interrupted write.  Append only missing equivalent
        # events and reject conflicting content before graph_written is set.
        existing = graph.article_node(node_id)
        seen = {
            (item["event_type"], _canonical_json(item["payload"]))
            for item in existing["history"]
        }
        by_identity: Dict[str, Tuple[str, str]] = {}
        for item in existing["history"]:
            key = ArticleFeedbackController._graph_event_identity(
                item["event_type"], item["payload"]
            )
            if key is not None:
                by_identity[key] = (
                    item["event_type"],
                    _canonical_json(item["payload"]),
                )
        for event_type, event_payload in expected_events:
            key = ArticleFeedbackController._graph_event_identity(
                event_type, event_payload
            )
            canonical = _canonical_json(event_payload)
            if key in by_identity and by_identity[key] != (event_type, canonical):
                raise ArticleFeedbackError(
                    f"feedback node {node_id!r} has conflicting {event_type} "
                    f"event for {key}"
                )
            if (event_type, canonical) in seen:
                continue
            ArticleFeedbackController._append_graph_event(
                graph, node_id, event_type, event_payload
            )
            seen.add((event_type, canonical))
            if key is not None:
                by_identity[key] = (event_type, canonical)

    @staticmethod
    def _expected_graph_events(
        result: ArticleFeedbackResult,
    ) -> List[Tuple[str, Dict[str, Any]]]:
        events: List[Tuple[str, Dict[str, Any]]] = []
        for update in result.hypothesis_updates:
            events.append(
                (
                    "article.hypothesis_update",
                    validate_article_event("article.hypothesis_update", {
                        "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                        "hypothesis_id": update.hypothesis_id,
                        "from_status": update.from_status.value,
                        "to_status": update.to_status.value,
                        "reason": update.reason,
                    }),
                )
            )
        for update in result.coverage_updates:
            events.append(
                (
                    "article.coverage",
                    validate_article_event("article.coverage", {
                        "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                        "route_id": update.route_id,
                        "coverage_status": update.to_status.value,
                        "reason": update.reason,
                    }),
                )
            )
        for observation_id in result.provenance_observation_ids:
            events.append(
                (
                    "article.observation",
                    validate_article_event("article.observation", {
                        "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                        "observation_id": observation_id,
                    }),
                )
            )
        events.append(
            (
                "article.decision",
                validate_article_event("article.decision", {
                    "schema_version": ARTICLE_EVENT_SCHEMA_VERSION,
                    "decision": result.stop_decision.value,
                    "reason": result.stop_reason,
                }),
            )
        )
        return events

    @staticmethod
    def _graph_event_identity(
        event_type: str, payload: Mapping[str, Any]
    ) -> Optional[str]:
        if event_type == "article.hypothesis_update":
            return f"hypothesis:{payload.get('hypothesis_id')}"
        if event_type == "article.coverage":
            return f"route:{payload.get('route_id')}"
        if event_type == "article.observation":
            return f"observation:{payload.get('observation_id')}"
        if event_type == "article.decision":
            return "decision"
        return None

    @staticmethod
    def _append_graph_event(
        graph: ExperimentGraph,
        node_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type == "article.hypothesis_update":
            graph.record_hypothesis_update(
                node_id,
                str(payload["hypothesis_id"]),
                HypothesisStatus(str(payload["from_status"])),
                HypothesisStatus(str(payload["to_status"])),
                reason=str(payload["reason"]),
            )
        elif event_type == "article.coverage":
            graph.record_coverage(
                node_id,
                str(payload["route_id"]),
                CoverageStatus(str(payload["coverage_status"])),
                reason=str(payload["reason"]),
            )
        elif event_type == "article.observation":
            graph.record_observation(node_id, str(payload["observation_id"]))
        elif event_type == "article.decision":
            graph.set_article_decision(
                node_id,
                ArticleDecision(str(payload["decision"])),
                reason=str(payload["reason"]),
            )
        else:
            raise ArticleFeedbackError(
                f"unsupported graph event type {event_type!r}"
            )

    @staticmethod
    def _persist_memory(
        memory_store: ArticleMemoryStore,
        controller_id: str,
        result: ArticleFeedbackResult,
        run_id: str,
    ) -> None:
        decision_record = RunMemoryRecord(
            memory_id=f"feedback-decision-{controller_id}",
            run_id=run_id,
            event_type="article_feedback_decision",
            graph_node_id=f"feedback-{controller_id}",
            artifact_ids=[],
            operational_note=_canonical_json(
                {
                    "stop_decision": result.stop_decision.value,
                    "stop_reason": result.stop_reason,
                    "next_routes": [
                        {"route_id": item.route_id, "reason": item.reason}
                        for item in result.next_routes
                    ],
                    "controller_id": controller_id,
                }
            ),
        )
        ArticleFeedbackController._add_memory_idempotent(memory_store, decision_record)
        for index, observation_id in enumerate(result.provenance_observation_ids):
            record = RunMemoryRecord(
                memory_id=f"feedback-observation-{controller_id}-{index:03d}",
                run_id=run_id,
                event_type="article_feedback_observation",
                graph_node_id=f"feedback-{controller_id}",
                artifact_ids=[],
                operational_note=_canonical_json(
                    {
                        "observation_id": observation_id,
                        "hypothesis_updates": [
                            {
                                "hypothesis_id": item.hypothesis_id,
                                "from_status": item.from_status.value,
                                "to_status": item.to_status.value,
                            }
                            for item in result.hypothesis_updates
                            if observation_id in item.observation_ids
                        ],
                    }
                ),
            )
            ArticleFeedbackController._add_memory_idempotent(memory_store, record)

    @staticmethod
    def _add_memory_idempotent(
        memory_store: ArticleMemoryStore, record: RunMemoryRecord
    ) -> None:
        try:
            memory_store.add_run_memory(record)
        except DuplicateRecordError:
            existing = memory_store.get_run_memory(record.memory_id)
            if existing.operational_note != record.operational_note:
                raise ArticleFeedbackError(
                    f"memory record {record.memory_id!r} already exists with "
                    "different content"
                ) from None


def _read_journal(path: str | Path) -> Dict[str, Any]:
    journal_path = Path(path)
    if not journal_path.exists():
        return {}
    try:
        payload = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArticleFeedbackError(f"feedback journal is unreadable: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ArticleFeedbackError("feedback journal must be a JSON object")
    return {str(key): dict(value) for key, value in payload.items() if isinstance(value, Mapping)}


def _write_journal(
    path: str | Path,
    journal: Mapping[str, Any],
    controller_id: str,
    state: Mapping[str, Any],
    *,
    progress_state: Optional[Mapping[str, int]] = None,
) -> None:
    payload = dict(journal)
    payload[str(controller_id)] = dict(state)
    if progress_state is not None:
        payload["progress_state"] = dict(progress_state)
    atomic_write_json(Path(path), payload)


__all__ = [
    "ArticleFeedbackController",
    "ArticleFeedbackError",
    "ArticleFeedbackResult",
    "CoverageUpdate",
    "FEEDBACK_CONTROLLER_VERSION",
    "FEEDBACK_SCHEMA_VERSION",
    "HypothesisUpdateDecision",
    "ObservationContext",
    "RouteSchedule",
]
