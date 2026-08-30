"""Stage 16: deterministic Article research-strategy tournament.

Replays four named research policies over immutable historical real TMM
trace banks under identical revealed-information and route-count budget
contracts.  This is retrospective policy replay, not fresh physics
execution: no Qwen, no network, no solver, no TMM, and no fabricated or
recomputed physical result.  Strategies see only public route descriptors
plus outcomes revealed by their own prior selections; the evaluator may
compare a completed trace with the full-pool oracle afterward.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

EVALUATOR_CONTRACT_VERSION = "article-tournament-evaluator.v2"
CHECKPOINT_SCHEMA_VERSION = "article-tournament-checkpoint.v2"
RESULT_SCHEMA_VERSION = "article-tournament-result.v2"

STRATEGY_LEGACY_TEMPLATE = "legacy_template"
STRATEGY_STAGED_TREE = "staged_tree"
STRATEGY_ATOMIC_IMPROVEMENT = "atomic_improvement"
STRATEGY_OPTOMIND_HYBRID = "optomind_hybrid"

REQUIRED_SOURCE_FILES = (
    "RESEARCH_RESULT.json",
    "ITERATION_HISTORY.json",
    "STRATEGY_PLAN.json",
    "FEEDBACK_HISTORY.json",
)

STOP_BUDGET_EXHAUSTED = "budget_exhausted"
STOP_POLICY_STOP = "policy_stop"
STOP_POOL_EXHAUSTED = "pool_exhausted"
STOP_INVALID_STRATEGY = "invalid_strategy"
VALID_STOP_REASONS = {
    STOP_BUDGET_EXHAUSTED,
    STOP_POLICY_STOP,
    STOP_POOL_EXHAUSTED,
    STOP_INVALID_STRATEGY,
}

# Stable public composite weights; must sum to 1.0.  Metrics marked
# not-applicable are excluded and the remaining weights are renormalized.
COMPOSITE_WEIGHTS = {
    "coverage": 0.10,
    "experimental_gain": 0.15,
    "discrimination": 0.09,
    "fact_yield": 0.12,
    "figure_readiness": 0.08,
    "robustness_coverage": 0.09,
    "optimizer_ablation_coverage": 0.05,
    "validity_ratio": 0.10,
    "efficiency": 0.08,
    "stop_quality": 0.10,
    "checkpoint_resume_equivalence": 0.02,
    "provenance_preservation": 0.02,
}

_VISUAL_TABLE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".pdf",
    ".csv",
    ".tsv",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(*parts: Any) -> str:
    return hashlib.sha256(
        _canonical_json([str(part) for part in parts]).encode("utf-8")
    ).hexdigest()[:16]


def _deep_json_copy(value: Any) -> Any:
    """Detached deep copy via canonical JSON (allow_nan=False, sorted)."""

    return json.loads(_canonical_json(value))


def _deep_copy_model(model: Any) -> Any:
    """Detached model copy via canonical JSON round-trip."""

    payload = _canonical_json(model.model_dump(mode="json"))
    return type(model).model_validate(json.loads(payload))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _optional_finite(value: Any, label: str) -> Optional[float]:
    if value is None:
        return None
    return _finite_number(value, label)


class SourceFileBinding(_StrictModel):
    schema_version: Literal["tournament-source-binding.v1"] = (
        "tournament-source-binding.v1"
    )
    relative_path: str
    sha256: str
    size_bytes: int
    run_id: str
    question: str

    @field_validator("sha256")
    @classmethod
    def _hex_sha256(cls, value: str) -> str:
        if len(value) != 64 or any(
            char not in "0123456789abcdef" for char in value
        ):
            raise ValueError("sha256 must be a 64-character lowercase hex digest")
        return value


class PublicRouteDescriptor(_StrictModel):
    """Route features visible before selection (public contract only)."""

    schema_version: Literal["tournament-public-route.v1"] = (
        "tournament-public-route.v1"
    )
    route_id: str
    title: str
    priority: int
    route_kind: str
    materials: Tuple[str, ...] = Field(default_factory=tuple)
    topology: str = ""
    layer_count: int = 0
    design_principle: str = ""
    hypothesis: str = ""
    soft_objectives: Tuple[str, ...] = Field(default_factory=tuple)
    expected_advantages: Tuple[str, ...] = Field(default_factory=tuple)
    known_risks: Tuple[str, ...] = Field(default_factory=tuple)
    parent_route_id: str = ""
    revision_reason: str = ""

    @field_validator("route_id", "title", "route_kind")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not str(value or "").strip():
            raise ValueError("route_id/title/route_kind must be non-empty")
        return value


class VerifiedCandidateRecord(_StrictModel):
    schema_version: Literal["tournament-candidate-record.v1"] = (
        "tournament-candidate-record.v1"
    )
    candidate_id: str
    experiment_id: str = ""
    optimizer_id: str = ""
    certificate_id: str = ""
    artifact_ids: Tuple[str, ...] = Field(default_factory=tuple)
    objective_report_present: bool = False
    robustness_report_present: bool = False
    target_score: Optional[float] = None
    robustness_score: Optional[float] = None
    candidate_hash: str

    @field_validator("candidate_hash")
    @classmethod
    def _hex_hash(cls, value: str) -> str:
        if len(value) != 64 or any(
            char not in "0123456789abcdef" for char in value
        ):
            raise ValueError("candidate_hash must be a 64-character hex digest")
        return value


class RevealedOutcome(_StrictModel):
    """Hidden route outcome revealed only after the route is selected."""

    schema_version: Literal["tournament-revealed-outcome.v1"] = (
        "tournament-revealed-outcome.v1"
    )
    route_id: str
    iteration_id: str
    run_status: str = ""
    compilation_status: str = ""
    physically_valid_candidate_count: int = 0
    best_target_score: Optional[float] = None
    best_robustness_score: Optional[float] = None
    selected_candidate_ids: Tuple[str, ...] = Field(default_factory=tuple)
    failure_categories: Tuple[str, ...] = Field(default_factory=tuple)
    experiment_ids: Tuple[str, ...] = Field(default_factory=tuple)
    selected_roles: Dict[str, str] = Field(default_factory=dict)
    result_path: str = ""
    task_path: str = ""
    work_dir: str = ""
    budget_usage: Dict[str, Any] = Field(default_factory=dict)
    candidates: Tuple[VerifiedCandidateRecord, ...] = Field(default_factory=tuple)
    outcome_hash: str

    @field_validator("outcome_hash")
    @classmethod
    def _hex_hash(cls, value: str) -> str:
        if len(value) != 64 or any(
            char not in "0123456789abcdef" for char in value
        ):
            raise ValueError("outcome_hash must be a 64-character hex digest")
        return value


class StrategySnapshot(_StrictModel):
    """Narrow view handed to a strategy: public pool + revealed outcomes only."""

    schema_version: Literal["tournament-strategy-snapshot.v1"] = (
        "tournament-strategy-snapshot.v1"
    )
    trace_id: str
    strategy_id: str
    strategy_version: int
    route_budget: int
    remaining_budget: int
    public_pool: Tuple[PublicRouteDescriptor, ...] = Field(
        default_factory=tuple
    )
    revealed: Tuple[RevealedOutcome, ...] = Field(default_factory=tuple)
    selected_order: Tuple[str, ...] = Field(default_factory=tuple)
    next_decision_state: Dict[str, Any] = Field(default_factory=dict)

    def selected(self) -> set[str]:
        return set(self.selected_order)

    def revealed_by_route(self) -> Dict[str, RevealedOutcome]:
        return {item.route_id: item for item in self.revealed}


class StrategyChoice(_StrictModel):
    schema_version: Literal["tournament-strategy-choice.v1"] = (
        "tournament-strategy-choice.v1"
    )
    kind: Literal["select", "stop"]
    route_id: str = ""
    reason: str = ""
    next_state: Dict[str, Any] = Field(default_factory=dict)


class TournamentStrategy:
    strategy_id: str = "abstract"
    strategy_version: int = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        raise NotImplementedError

    def clone(self) -> "TournamentStrategy":
        try:
            return type(self)()
        except TypeError as exc:
            raise ValueError(
                f"strategy {self.strategy_id} must support a no-argument clone"
            ) from exc


class _TraceBank:
    def __init__(
        self,
        *,
        trace_id: str,
        run_id: str,
        question: str,
        source_bindings: Dict[str, SourceFileBinding],
        public_pool: Tuple[PublicRouteDescriptor, ...],
        hidden_outcomes: Dict[str, RevealedOutcome],
        planned_not_run: Tuple[PublicRouteDescriptor, ...],
        problem_metadata: Dict[str, Any],
    ) -> None:
        self.trace_id = trace_id
        self.run_id = run_id
        self.question = question
        self.source_bindings = source_bindings
        self.public_pool = public_pool
        self.hidden_outcomes = hidden_outcomes
        self.planned_not_run = planned_not_run
        self.problem_metadata = problem_metadata

    @property
    def route_count(self) -> int:
        return len(self.public_pool)


def _load_json_file(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"{label} is not readable JSON: {exc}") from exc


def _route_public_descriptor(route: Mapping[str, Any]) -> PublicRouteDescriptor:
    route_id = str(route.get("route_id") or "")
    title = str(route.get("title") or "")
    priority = route.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ValueError(f"route {route_id!r} priority must be an integer")
    if priority < 0:
        raise ValueError(f"route {route_id!r} priority must be non-negative")
    materials = tuple(
        str(item)
        for item in (route.get("proposed_materials") or [])
        if str(item).strip()
    )
    design_variables = route.get("design_variables") or []
    if not isinstance(design_variables, list):
        raise ValueError(f"route {route_id!r} design_variables must be a list")
    return PublicRouteDescriptor(
        route_id=route_id,
        title=title,
        priority=int(priority),
        route_kind=str(route.get("route_kind") or ""),
        materials=materials,
        topology=str(route.get("proposed_topology") or ""),
        layer_count=len(design_variables),
        design_principle=str(route.get("design_principle") or ""),
        hypothesis=str(route.get("scientific_hypothesis") or ""),
        soft_objectives=tuple(
            str(item)
            for item in (route.get("soft_objectives") or [])
            if str(item).strip()
        ),
        expected_advantages=tuple(
            str(item)
            for item in (route.get("expected_advantages") or [])
            if str(item).strip()
        ),
        known_risks=tuple(
            str(item)
            for item in (route.get("known_risks") or [])
            if str(item).strip()
        ),
        parent_route_id=str(route.get("parent_route_id") or ""),
        revision_reason=str(route.get("revision_reason") or ""),
    )


def _candidate_record(row: Mapping[str, Any], label: str) -> VerifiedCandidateRecord:
    candidate_id = str(row.get("candidate_id") or "")
    if not candidate_id.strip():
        raise ValueError(f"{label} candidate_id must be non-empty")
    artifact_ids = tuple(
        sorted(
            str(item)
            for item in (row.get("artifact_ids") or [])
            if str(item).strip()
        )
    )
    objective_present = isinstance(row.get("objective_report"), dict)
    robustness_present = isinstance(row.get("robustness_report"), dict)
    target = _optional_finite(row.get("target_score"), f"{label}.{candidate_id} target_score")
    robustness = _optional_finite(
        row.get("robustness_score"), f"{label}.{candidate_id} robustness_score"
    )
    candidate_hash = hashlib.sha256(
        _canonical_json(
            {
                "candidate_id": candidate_id,
                "experiment_id": str(row.get("experiment_id") or ""),
                "optimizer_id": str(row.get("optimizer_id") or ""),
                "certificate_id": str(row.get("certificate_id") or ""),
                "artifact_ids": list(artifact_ids),
                "objective_report_present": objective_present,
                "robustness_report_present": robustness_present,
                "target_score": target,
                "robustness_score": robustness,
            }
        ).encode("utf-8")
    ).hexdigest()
    return VerifiedCandidateRecord(
        candidate_id=candidate_id,
        experiment_id=str(row.get("experiment_id") or ""),
        optimizer_id=str(row.get("optimizer_id") or ""),
        certificate_id=str(row.get("certificate_id") or ""),
        artifact_ids=artifact_ids,
        objective_report_present=objective_present,
        robustness_report_present=robustness_present,
        target_score=target,
        robustness_score=robustness,
        candidate_hash=candidate_hash,
    )


def _outcome_payload(
    row: Mapping[str, Any],
    label: str,
    *,
    include_telemetry: bool,
) -> str:
    route_id = str(row.get("route_id") or "")
    iteration_id = str(row.get("iteration_id") or "")
    target = _optional_finite(row.get("best_target_score"), f"{label} best_target_score")
    robustness = _optional_finite(
        row.get("best_robustness_score"), f"{label} best_robustness_score"
    )
    candidate_count = row.get("physically_valid_candidate_count")
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count < 0
    ):
        raise ValueError(
            f"{label} physically_valid_candidate_count must be a "
            "non-negative integer"
        )
    summaries = row.get("experiment_summaries") or []
    experiment_ids = tuple(
        str(item.get("experiment_id"))
        for item in summaries
        if isinstance(item, dict) and str(item.get("experiment_id") or "").strip()
    )
    selected_roles: Dict[str, str] = {}
    for item in summaries:
        if not isinstance(item, dict):
            continue
        roles = item.get("selected_roles")
        if isinstance(roles, dict):
            for role, candidate in roles.items():
                selected_roles[f"{item.get('experiment_id')}:{role}"] = str(candidate)
    budget_usage = row.get("budget_usage")
    if budget_usage is not None and not isinstance(budget_usage, dict):
        raise ValueError(f"{label} budget_usage must be a mapping or absent")
    budget_usage = dict(budget_usage or {})
    if not include_telemetry:
        budget_usage = {
            key: value
            for key, value in budget_usage.items()
            if key != "wall_time_seconds"
        }
    for key, value in budget_usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            _finite_number(value, f"{label} budget_usage.{key}")
    candidate_rows = row.get("candidate_summaries") or []
    if not isinstance(candidate_rows, list):
        raise ValueError(f"{label} candidate_summaries must be a list")
    candidates = [
        _candidate_record(item, label).model_dump(mode="json")
        for item in candidate_rows
    ]
    return _canonical_json(
        {
            "route_id": route_id,
            "iteration_id": iteration_id,
            "run_status": str(row.get("run_status") or ""),
            "compilation_status": str(row.get("compilation_status") or ""),
            "physically_valid_candidate_count": int(candidate_count),
            "best_target_score": target,
            "best_robustness_score": robustness,
            "selected_candidate_ids": sorted(
                str(item)
                for item in (row.get("selected_candidate_ids") or [])
                if str(item).strip()
            ),
            "failure_categories": sorted(
                str(item)
                for item in (row.get("failure_categories") or [])
                if str(item).strip()
            ),
            "experiment_ids": list(experiment_ids),
            "selected_roles": selected_roles,
            "result_path": str(row.get("result_path") or ""),
            "task_path": str(row.get("task_path") or ""),
            "work_dir": str(row.get("work_dir") or ""),
            "budget_usage": budget_usage,
            "candidates": candidates,
        }
    )


def _route_hidden_outcome(
    row: Mapping[str, Any],
    *,
    label: str,
) -> RevealedOutcome:
    target = _optional_finite(row.get("best_target_score"), f"{label} best_target_score")
    robustness = _optional_finite(
        row.get("best_robustness_score"), f"{label} best_robustness_score"
    )
    candidate_count = row.get("physically_valid_candidate_count")
    summaries = row.get("experiment_summaries") or []
    experiment_ids = tuple(
        str(item.get("experiment_id"))
        for item in summaries
        if isinstance(item, dict) and str(item.get("experiment_id") or "").strip()
    )
    selected_roles: Dict[str, str] = {}
    for item in summaries:
        if not isinstance(item, dict):
            continue
        roles = item.get("selected_roles")
        if isinstance(roles, dict):
            for role, candidate in roles.items():
                selected_roles[f"{item.get('experiment_id')}:{role}"] = str(candidate)
    candidate_rows = row.get("candidate_summaries") or []
    candidates = tuple(
        _candidate_record(item, label)
        for item in candidate_rows
    )
    outcome = RevealedOutcome(
        route_id=str(row.get("route_id") or ""),
        iteration_id=str(row.get("iteration_id") or ""),
        run_status=str(row.get("run_status") or ""),
        compilation_status=str(row.get("compilation_status") or ""),
        physically_valid_candidate_count=int(candidate_count),
        best_target_score=target,
        best_robustness_score=robustness,
        selected_candidate_ids=tuple(
            str(item)
            for item in (row.get("selected_candidate_ids") or [])
            if str(item).strip()
        ),
        failure_categories=tuple(
            str(item)
            for item in (row.get("failure_categories") or [])
            if str(item).strip()
        ),
        experiment_ids=experiment_ids,
        selected_roles=selected_roles,
        result_path=str(row.get("result_path") or ""),
        task_path=str(row.get("task_path") or ""),
        work_dir=str(row.get("work_dir") or ""),
        budget_usage=dict(row.get("budget_usage") or {}),
        candidates=candidates,
        outcome_hash="0" * 64,
    )
    return outcome.model_copy(
        update={"outcome_hash": _outcome_content_hash(outcome)}
    )


def _outcome_content_hash(outcome: RevealedOutcome) -> str:
    return hashlib.sha256(
        _canonical_json(
            outcome.model_dump(exclude={"outcome_hash"}, mode="json")
        ).encode("utf-8")
    ).hexdigest()


def load_trace_bank(run_dir: str | Path) -> _TraceBank:
    """Load and bind one immutable historical trace bank (read-only)."""

    directory = Path(run_dir)
    if not directory.is_dir():
        raise ValueError(f"trace bank directory does not exist: {directory}")
    source: Dict[str, SourceFileBinding] = {}
    raw_files: Dict[str, Any] = {}
    for name in REQUIRED_SOURCE_FILES:
        path = directory / name
        if not path.is_file():
            raise ValueError(f"trace bank is missing required file {name!r}")
        payload = path.read_bytes()
        raw_files[name] = _load_json_file(path, name)
        source[name] = SourceFileBinding(
            relative_path=name,
            sha256=_sha256_bytes(payload),
            size_bytes=len(payload),
            run_id="",
            question="",
        )
    result = raw_files["RESEARCH_RESULT.json"]
    if not isinstance(result, dict):
        raise ValueError("RESEARCH_RESULT.json must be a JSON object")
    run_id = str(result.get("run_id") or "")
    question = str(result.get("question") or "")
    if not run_id.strip() or not question.strip():
        raise ValueError("RESEARCH_RESULT.json requires run_id and question")
    for name in REQUIRED_SOURCE_FILES:
        source[name] = source[name].model_copy(
            update={"run_id": run_id, "question": question}
        )
    trace_id = hashlib.sha256(
        _canonical_json(
            {
                "run_id": run_id,
                "question": question,
                "source_hashes": {
                    name: source[name].sha256
                    for name in REQUIRED_SOURCE_FILES
                },
            }
        ).encode("utf-8")
    ).hexdigest()

    plan = raw_files["STRATEGY_PLAN.json"]
    if not isinstance(plan, dict) or not isinstance(plan.get("plan"), dict):
        raise ValueError("STRATEGY_PLAN.json must contain a plan object")
    plan_routes = plan["plan"].get("routes")
    if not isinstance(plan_routes, list) or not plan_routes:
        raise ValueError("STRATEGY_PLAN.json plan.routes must be a non-empty list")
    descriptors_by_id: Dict[str, PublicRouteDescriptor] = {}
    for index, route in enumerate(plan_routes):
        if not isinstance(route, dict):
            raise ValueError(f"plan route {index} must be an object")
        descriptor = _route_public_descriptor(route)
        if descriptor.route_id in descriptors_by_id:
            raise ValueError(
                f"duplicate route_id {descriptor.route_id!r} in STRATEGY_PLAN"
            )
        descriptors_by_id[descriptor.route_id] = descriptor

    embedded_plan = result.get("strategy_plan")
    if not isinstance(embedded_plan, dict):
        raise ValueError("RESEARCH_RESULT.strategy_plan must be an object")
    embedded_routes = embedded_plan.get("routes")
    if not isinstance(embedded_routes, list):
        raise ValueError("RESEARCH_RESULT.strategy_plan.routes must be a list")
    for index, route in enumerate(embedded_routes):
        if not isinstance(route, dict):
            raise ValueError(f"embedded plan route {index} must be an object")
        route_id = str(route.get("route_id") or "")
        if route_id not in descriptors_by_id:
            raise ValueError(
                f"embedded strategy_plan route {route_id!r} is missing from "
                "STRATEGY_PLAN.json"
            )
        embedded_descriptor = _route_public_descriptor(route)
        if embedded_descriptor != descriptors_by_id[route_id]:
            raise ValueError(
                f"embedded strategy_plan route {route_id!r} disagrees with "
                "STRATEGY_PLAN.json"
            )

    feedback = raw_files["FEEDBACK_HISTORY.json"]
    embedded_feedback = result.get("feedback_history")
    if not isinstance(feedback, list) or feedback != embedded_feedback:
        raise ValueError(
            "FEEDBACK_HISTORY.json disagrees with embedded feedback_history"
        )
    for index, row in enumerate(feedback):
        if not isinstance(row, dict):
            raise ValueError(f"feedback {index} must be an object")
        for key in ("observed_improvement", "remaining_headroom"):
            value = row.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                _finite_number(value, f"FEEDBACK_HISTORY[{index}].{key}")

    iteration_history = raw_files["ITERATION_HISTORY.json"]
    result_iterations = result.get("iterations")
    if not isinstance(iteration_history, list) or not iteration_history:
        raise ValueError("ITERATION_HISTORY.json must be a non-empty list")
    if not isinstance(result_iterations, list):
        raise ValueError("RESEARCH_RESULT.json iterations must be a list")

    history_by_id: Dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(iteration_history):
        if not isinstance(row, dict):
            raise ValueError(f"iteration {index} must be an object")
        iteration_id = str(row.get("iteration_id") or "")
        if not iteration_id or iteration_id in history_by_id:
            raise ValueError(
                f"duplicate or missing iteration_id at index {index}"
            )
        history_by_id[iteration_id] = row
    result_by_id: Dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(result_iterations):
        if not isinstance(row, dict):
            raise ValueError(f"result iteration {index} must be an object")
        iteration_id = str(row.get("iteration_id") or "")
        if not iteration_id or iteration_id in result_by_id:
            raise ValueError(
                f"duplicate or missing result iteration_id at index {index}"
            )
        result_by_id[iteration_id] = row
    if set(history_by_id) != set(result_by_id):
        raise ValueError(
            "ITERATION_HISTORY and RESEARCH_RESULT iterations disagree"
        )

    hidden: Dict[str, RevealedOutcome] = {}
    executed_route_ids: set[str] = set()
    for iteration_id in sorted(history_by_id):
        history_row = history_by_id[iteration_id]
        result_row = result_by_id[iteration_id]
        history_canonical = _outcome_payload(
            history_row,
            f"ITERATION_HISTORY.{iteration_id}",
            include_telemetry=False,
        )
        result_canonical = _outcome_payload(
            result_row,
            f"RESEARCH_RESULT.{iteration_id}",
            include_telemetry=False,
        )
        if history_canonical != result_canonical:
            raise ValueError(
                f"ITERATION_HISTORY and RESEARCH_RESULT disagree on "
                f"iteration {iteration_id}"
            )
        outcome = _route_hidden_outcome(
            history_row,
            label=f"ITERATION_HISTORY.{iteration_id}",
        )
        route_id = outcome.route_id
        if route_id in hidden:
            raise ValueError(f"duplicate executed route_id {route_id!r}")
        if route_id not in descriptors_by_id:
            raise ValueError(
                f"executed route {route_id!r} is missing from STRATEGY_PLAN"
            )
        hidden[route_id] = outcome
        executed_route_ids.add(route_id)

    public_pool = tuple(
        descriptors_by_id[route_id]
        for route_id in sorted(executed_route_ids)
    )
    planned_not_run = tuple(
        descriptors_by_id[route_id]
        for route_id in sorted(set(descriptors_by_id) - executed_route_ids)
    )
    if len(public_pool) != len(hidden):
        raise ValueError("public pool and hidden outcomes disagree")
    problem = result.get("problem_analysis")
    problem_metadata = {
        "target_observables": (
            list(problem.get("target_observables") or [])
            if isinstance(problem, dict)
            else []
        ),
        "preferred_behaviors": (
            list(problem.get("preferred_behaviors") or [])
            if isinstance(problem, dict)
            else []
        ),
        "wavelengths_nm": (
            list(problem.get("wavelengths_nm") or [])
            if isinstance(problem, dict)
            else []
        ),
    }
    return _TraceBank(
        trace_id=trace_id,
        run_id=run_id,
        question=question,
        source_bindings=source,
        public_pool=public_pool,
        hidden_outcomes=hidden,
        planned_not_run=planned_not_run,
        problem_metadata=problem_metadata,
    )


def _public_by_id(
    pool: Sequence[PublicRouteDescriptor],
) -> Dict[str, PublicRouteDescriptor]:
    return {item.route_id: item for item in pool}


class LegacyTemplateStrategy(TournamentStrategy):
    """Planned priority/source-order replay, bounded by the route budget."""

    strategy_id = STRATEGY_LEGACY_TEMPLATE
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        selected = snapshot.selected()
        ordered = sorted(
            snapshot.public_pool,
            key=lambda item: (item.priority, item.route_id),
        )
        for route in ordered:
            if route.route_id not in selected:
                return StrategyChoice(
                    kind="select",
                    route_id=route.route_id,
                    reason="planned priority/source order",
                )
        return StrategyChoice(
            kind="stop",
            reason="no unselected route remains",
        )


class StagedTreeStrategy(TournamentStrategy):
    """AI-Scientist-inspired staged exploration over public descriptors only.

    This is a clean deterministic reimplementation from the architecture
    contract; it does not copy or call any upstream implementation.
    """

    strategy_id = STRATEGY_STAGED_TREE
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        selected = snapshot.selected()
        by_kind: Dict[str, List[PublicRouteDescriptor]] = {}
        for route in snapshot.public_pool:
            by_kind.setdefault(route.route_kind, []).append(route)
        stage_order = sorted(
            by_kind.keys(),
            key=lambda kind: min(
                item.priority for item in by_kind[kind]
            ),
        )
        for kind in stage_order:
            candidates = sorted(
                by_kind[kind],
                key=lambda item: (item.priority, item.route_id),
            )
            for route in candidates:
                if route.route_id not in selected:
                    return StrategyChoice(
                        kind="select",
                        route_id=route.route_id,
                        reason=f"staged exploration of kind {kind!r}",
                    )
        return StrategyChoice(
            kind="stop",
            reason="all staged kinds exhausted",
        )


def _design_delta(
    current: PublicRouteDescriptor,
    candidate: PublicRouteDescriptor,
) -> int:
    layer_delta = abs(current.layer_count - candidate.layer_count)
    material_delta = len(
        set(current.materials) ^ set(candidate.materials)
    )
    kind_delta = 2 if current.route_kind != candidate.route_kind else 0
    return layer_delta + material_delta + kind_delta


class AtomicImprovementStrategy(TournamentStrategy):
    """AIDE-inspired smallest public design delta with emulated lineage.

    Historical traces have no real parent/child genealogy in the source, so
    lineage is labeled tournament-emulated and the source is never rewritten.
    """

    strategy_id = STRATEGY_ATOMIC_IMPROVEMENT
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        selected_order = list(snapshot.selected_order)
        state = dict(snapshot.next_decision_state)
        lineage = dict(state.get("lineage") or {})
        pool = _public_by_id(snapshot.public_pool)
        if not selected_order:
            current = min(
                snapshot.public_pool,
                key=lambda item: (item.priority, item.route_id),
            )
            return StrategyChoice(
                kind="select",
                route_id=current.route_id,
                reason="start from highest-priority public route",
                next_state={"lineage": {}},
            )
        current = pool[selected_order[-1]]
        unselected = [
            item
            for item in snapshot.public_pool
            if item.route_id not in snapshot.selected()
        ]
        if not unselected:
            return StrategyChoice(
                kind="stop",
                reason="no unselected route remains",
            )
        best = min(
            unselected,
            key=lambda item: (
                _design_delta(current, item),
                item.priority,
                item.route_id,
            ),
        )
        lineage[best.route_id] = current.route_id
        return StrategyChoice(
            kind="select",
            route_id=best.route_id,
            reason="smallest public design delta from current route",
            next_state={"lineage": lineage},
        )


class OptoMindHybridStrategy(TournamentStrategy):
    """Diversity/central-complexity first, then revealed marginal gain/stop."""

    strategy_id = STRATEGY_OPTOMIND_HYBRID
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        state = dict(snapshot.next_decision_state)
        pool = list(snapshot.public_pool)
        selected = snapshot.selected()
        revealed = snapshot.revealed_by_route()
        layer_counts = [item.layer_count for item in pool]
        median_layer = float(
            sorted(layer_counts)[len(layer_counts) // 2]
        )
        layer_range = max(layer_counts) - min(layer_counts)

        def diversity_score(route: PublicRouteDescriptor) -> Tuple[float, int, str]:
            seen_kinds = {
                pool_item.route_kind
                for pool_item in pool
                if pool_item.route_id in selected
            }
            seen_materials = {
                material
                for pool_item in pool
                if pool_item.route_id in selected
                for material in pool_item.materials
            }
            kind_bonus = 2.0 if route.route_kind not in seen_kinds else 0.0
            material_bonus = sum(
                1.0
                for material in route.materials
                if material not in seen_materials
            )
            centrality = (
                1.0
                - abs(route.layer_count - median_layer) / max(1, layer_range)
            )
            return (
                kind_bonus + material_bonus + centrality,
                route.priority,
                route.route_id,
            )

        unselected = [item for item in pool if item.route_id not in selected]
        if not unselected:
            return StrategyChoice(
                kind="stop",
                reason="no unselected route remains",
            )
        if len(selected) >= 2 and snapshot.remaining_budget > 0:
            revealed_targets = [
                revealed[route_id].best_target_score
                for route_id in snapshot.selected_order
                if route_id in revealed
                and revealed[route_id].best_target_score is not None
            ]
            if len(revealed_targets) >= 2 and state.get("phase_two"):
                marginal = revealed_targets[-1] - revealed_targets[-2]
                if marginal <= 0:
                    return StrategyChoice(
                        kind="stop",
                        reason=(
                            "revealed marginal gain is not positive and "
                            "diversity phase completed"
                        ),
                    )
        def diversity_key(route: PublicRouteDescriptor) -> Tuple[float, int, str]:
            score, priority, route_id = diversity_score(route)
            return (score, -priority, route_id)

        best = max(unselected, key=diversity_key)
        next_state = dict(state)
        if (
            len(selected) >= 1
            and not next_state.get("phase_two")
            and len(
                {
                    item.route_kind
                    for item in pool
                    if item.route_id in selected
                }
            )
            >= len({item.route_kind for item in pool})
        ):
            next_state["phase_two"] = True
        return StrategyChoice(
            kind="select",
            route_id=best.route_id,
            reason="public diversity/central complexity selection",
            next_state=next_state,
        )


def default_strategies() -> Sequence[TournamentStrategy]:
    return (
        LegacyTemplateStrategy(),
        StagedTreeStrategy(),
        AtomicImprovementStrategy(),
        OptoMindHybridStrategy(),
    )


REGISTERED_BUILTIN_PAIRS = frozenset(
    (item.strategy_id, item.strategy_version)
    for item in default_strategies()
)


class RouteTrace(_StrictModel):
    schema_version: Literal["tournament-route-trace.v1"] = (
        "tournament-route-trace.v1"
    )
    trace_id: str
    strategy_id: str
    strategy_version: int
    route_budget: int
    selected_order: Tuple[str, ...] = Field(default_factory=tuple)
    revealed: Tuple[RevealedOutcome, ...] = Field(default_factory=tuple)
    invalid_attempts: Tuple[Dict[str, str], ...] = Field(default_factory=tuple)
    stop_reason: str
    stop_detail: str = ""
    next_decision_state: Dict[str, Any] = Field(default_factory=dict)
    trace_hash: str = ""


class _TraceState:
    def __init__(self) -> None:
        self.selected: List[str] = []
        self.revealed: Dict[str, RevealedOutcome] = {}
        self.invalid: List[Dict[str, str]] = []
        self.stop_reason: Optional[str] = None
        self.stop_detail: str = ""
        self.next_state: Dict[str, Any] = {}


def _build_snapshot(
    bank: _TraceBank,
    strategy: TournamentStrategy,
    budget: int,
    state: _TraceState,
) -> StrategySnapshot:
    return StrategySnapshot(
        trace_id=bank.trace_id,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        route_budget=budget,
        remaining_budget=max(0, budget - len(state.selected)),
        public_pool=bank.public_pool,
        revealed=tuple(
            _deep_copy_model(state.revealed[route_id])
            for route_id in state.selected
            if route_id in state.revealed
        ),
        selected_order=tuple(state.selected),
        next_decision_state=_deep_json_copy(state.next_state),
    )


def _advance_trace(
    bank: _TraceBank,
    strategy: TournamentStrategy,
    budget: int,
    state: _TraceState,
) -> bool:
    """Advance one decision step; returns False when the trace is finished."""

    if state.stop_reason is not None:
        return False
    if len(state.selected) >= budget:
        state.stop_reason = STOP_BUDGET_EXHAUSTED
        state.stop_detail = f"route budget {budget} reached"
        return False
    unselected = [
        item.route_id
        for item in bank.public_pool
        if item.route_id not in set(state.selected)
    ]
    if not unselected:
        state.stop_reason = STOP_POOL_EXHAUSTED
        state.stop_detail = "all historical routes selected"
        return False
    snapshot = _build_snapshot(bank, strategy, budget, state)
    before = _canonical_json(snapshot.model_dump(mode="json"))
    try:
        choice = strategy.select(snapshot)
    except Exception as exc:
        state.stop_reason = STOP_INVALID_STRATEGY
        state.stop_detail = f"strategy raised: {exc}"
        return False
    after = _canonical_json(snapshot.model_dump(mode="json"))
    if before != after:
        state.stop_reason = STOP_INVALID_STRATEGY
        state.stop_detail = "strategy mutated its snapshot"
        return False
    if not isinstance(choice, StrategyChoice):
        state.stop_reason = STOP_INVALID_STRATEGY
        state.stop_detail = "strategy returned a non-StrategyChoice value"
        return False
    try:
        _deep_json_copy(choice.model_dump(mode="json"))
    except ValueError:
        state.stop_reason = STOP_INVALID_STRATEGY
        state.stop_detail = (
            "strategy returned a non-finite/non-serializable choice"
        )
        return False
    if choice.kind == "stop":
        state.stop_reason = STOP_POLICY_STOP
        state.stop_detail = choice.reason or "policy stopped"
        try:
            state.next_state = _deep_json_copy(choice.next_state or {})
        except ValueError as exc:
            state.stop_reason = STOP_INVALID_STRATEGY
            state.stop_detail = (
                "strategy returned non-finite/non-serializable stop state"
            )
            return False
        return False
    route_id = choice.route_id
    if route_id not in unselected:
        state.invalid.append(
            {
                "route_id": route_id,
                "reason": choice.reason or "invalid or repeated selection",
            }
        )
        state.stop_reason = STOP_INVALID_STRATEGY
        state.stop_detail = f"invalid or repeated selection {route_id!r}"
        return False
    state.selected.append(route_id)
    state.revealed[route_id] = _deep_copy_model(
        bank.hidden_outcomes[route_id]
    )
    try:
        state.next_state = _deep_json_copy(choice.next_state or {})
    except ValueError as exc:
        state.stop_reason = STOP_INVALID_STRATEGY
        state.stop_detail = (
            "strategy returned non-finite/non-serializable decision state"
        )
        return False
    return True


def run_policy_trace(
    bank: _TraceBank,
    strategy: TournamentStrategy,
    budget: int,
) -> RouteTrace:
    """Run one policy trace with a fresh strategy clone (no state leakage)."""

    cloned = strategy.clone()
    state = _TraceState()
    while _advance_trace(bank, cloned, budget, state):
        pass
    return _trace_from_state(bank, cloned, budget, state)


def _trace_from_state(
    bank: _TraceBank,
    strategy: TournamentStrategy,
    budget: int,
    state: _TraceState,
) -> RouteTrace:
    trace = RouteTrace(
        trace_id=bank.trace_id,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        route_budget=budget,
        selected_order=tuple(state.selected),
        revealed=tuple(
            state.revealed[route_id]
            for route_id in state.selected
            if route_id in state.revealed
        ),
        invalid_attempts=tuple(state.invalid),
        stop_reason=state.stop_reason or STOP_BUDGET_EXHAUSTED,
        stop_detail=state.stop_detail,
        next_decision_state=dict(state.next_state),
        trace_hash="",
    )
    return trace.model_copy(
        update={
            "trace_hash": hashlib.sha256(
                _canonical_json(
                    trace.model_dump(exclude={"trace_hash"}, mode="json")
                ).encode("utf-8")
            ).hexdigest()
        }
    )


class TournamentCheckpoint(_StrictModel):
    schema_version: Literal["article-tournament-checkpoint.v2"] = (
        "article-tournament-checkpoint.v2"
    )
    checkpoint_id: str
    trace_id: str
    strategy_id: str
    strategy_version: int
    route_budget: int
    evaluator_contract_version: str
    selected_order: Tuple[str, ...] = Field(default_factory=tuple)
    revealed: Tuple[RevealedOutcome, ...] = Field(default_factory=tuple)
    invalid_attempts: Tuple[Dict[str, str], ...] = Field(default_factory=tuple)
    stop_reason: Optional[str] = None
    stop_detail: str = ""
    next_decision_state: Dict[str, Any] = Field(default_factory=dict)
    source_hashes: Dict[str, str] = Field(default_factory=dict)


def make_checkpoint(
    bank: _TraceBank,
    strategy: TournamentStrategy,
    budget: int,
    state: _TraceState,
) -> TournamentCheckpoint:
    checkpoint = TournamentCheckpoint(
        checkpoint_id="",
        trace_id=bank.trace_id,
        strategy_id=strategy.strategy_id,
        strategy_version=strategy.strategy_version,
        route_budget=budget,
        evaluator_contract_version=EVALUATOR_CONTRACT_VERSION,
        selected_order=tuple(state.selected),
        revealed=tuple(
            state.revealed[route_id]
            for route_id in state.selected
            if route_id in state.revealed
        ),
        invalid_attempts=tuple(state.invalid),
        stop_reason=state.stop_reason,
        stop_detail=state.stop_detail,
        next_decision_state=dict(state.next_state),
        source_hashes={
            name: binding.sha256
            for name, binding in bank.source_bindings.items()
        },
    )
    checkpoint_id = hashlib.sha256(
        _canonical_json(
            checkpoint.model_dump(exclude={"checkpoint_id"}, mode="json")
        ).encode("utf-8")
    ).hexdigest()
    return checkpoint.model_copy(update={"checkpoint_id": checkpoint_id})


def _validate_checkpoint_content(
    bank: _TraceBank,
    strategy: TournamentStrategy,
    budget: int,
    checkpoint: TournamentCheckpoint,
) -> None:
    pool_ids = {item.route_id for item in bank.public_pool}
    selected = list(checkpoint.selected_order)
    if len(selected) != len(set(selected)):
        raise ValueError("checkpoint selected_order contains duplicates")
    if any(route_id not in pool_ids for route_id in selected):
        raise ValueError("checkpoint selected_order references unknown routes")
    if len(selected) > budget:
        raise ValueError("checkpoint selected_order exceeds the route budget")
    revealed = list(checkpoint.revealed)
    revealed_ids = [item.route_id for item in revealed]
    if revealed_ids != selected:
        raise ValueError(
            "checkpoint revealed order/keys do not match selected_order"
        )
    for outcome in revealed:
        if outcome.route_id not in bank.hidden_outcomes:
            raise ValueError("checkpoint reveals an unknown route")
        expected = bank.hidden_outcomes[outcome.route_id]
        if (
            outcome.model_dump(exclude={"outcome_hash"}, mode="json")
            != expected.model_dump(exclude={"outcome_hash"}, mode="json")
            or outcome.outcome_hash != expected.outcome_hash
        ):
            raise ValueError(
                "checkpoint revealed outcome does not equal the bank outcome"
            )
    if checkpoint.stop_reason is not None and (
        checkpoint.stop_reason not in VALID_STOP_REASONS
    ):
        raise ValueError("checkpoint has an illegal stop reason")
    if (
        checkpoint.stop_reason == STOP_INVALID_STRATEGY
        and not checkpoint.invalid_attempts
    ):
        raise ValueError(
            "checkpoint invalid_strategy stop has no invalid attempts"
        )


def resume_policy_trace(
    bank: _TraceBank,
    strategy: TournamentStrategy,
    budget: int,
    checkpoint: TournamentCheckpoint | Mapping[str, Any],
) -> RouteTrace:
    """Resume a trace from a validated checkpoint (byte-equivalent result)."""

    model = (
        checkpoint
        if isinstance(checkpoint, TournamentCheckpoint)
        else TournamentCheckpoint.model_validate(checkpoint)
    )
    recomputed = make_checkpoint(
        bank,
        strategy,
        budget,
        _state_from_checkpoint(model),
    )
    if recomputed.checkpoint_id != model.checkpoint_id:
        raise ValueError("checkpoint identity does not match its content")
    if model.trace_id != bank.trace_id:
        raise ValueError("checkpoint trace_id does not match the trace bank")
    if (
        model.strategy_id != strategy.strategy_id
        or model.strategy_version != strategy.strategy_version
    ):
        raise ValueError("checkpoint strategy does not match the strategy")
    if model.route_budget != budget:
        raise ValueError("checkpoint route budget does not match the budget")
    if model.evaluator_contract_version != EVALUATOR_CONTRACT_VERSION:
        raise ValueError("checkpoint evaluator contract version is incompatible")
    expected_source_hashes = {
        name: binding.sha256
        for name, binding in bank.source_bindings.items()
    }
    if model.source_hashes != expected_source_hashes:
        raise ValueError("checkpoint source hashes do not match the trace bank")
    _validate_checkpoint_content(bank, strategy, budget, model)
    cloned = strategy.clone()
    state = _state_from_checkpoint(model)
    while _advance_trace(bank, cloned, budget, state):
        pass
    return _trace_from_state(bank, cloned, budget, state)


def _state_from_checkpoint(checkpoint: TournamentCheckpoint) -> _TraceState:
    state = _TraceState()
    state.selected = list(checkpoint.selected_order)
    state.revealed = {
        item.route_id: item for item in checkpoint.revealed
    }
    state.invalid = [dict(item) for item in checkpoint.invalid_attempts]
    state.stop_reason = checkpoint.stop_reason
    state.stop_detail = checkpoint.stop_detail
    state.next_state = dict(checkpoint.next_decision_state)
    return state


class MetricValue(_StrictModel):
    schema_version: Literal["tournament-metric-value.v1"] = (
        "tournament-metric-value.v1"
    )
    name: str
    raw: Optional[Any] = None
    normalized: Optional[float] = None
    not_applicable: bool = False
    reason: str = ""


class MetricVector(_StrictModel):
    schema_version: Literal["tournament-metric-vector.v1"] = (
        "tournament-metric-vector.v1"
    )
    trace_id: str
    strategy_id: str
    strategy_version: int
    route_budget: int
    metrics: Dict[str, MetricValue] = Field(default_factory=dict)
    composite_score: Optional[float] = None
    composite_weights_applied: Dict[str, float] = Field(default_factory=dict)
    pareto_member: bool = False
    vector_hash: str = ""


def _normalized(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _route_visual_artifacts(outcome: RevealedOutcome) -> Tuple[str, ...]:
    return tuple(
        sorted(
            {
                artifact
                for candidate in outcome.candidates
                for artifact in candidate.artifact_ids
                if Path(artifact).suffix.lower() in _VISUAL_TABLE_EXTENSIONS
            }
        )
    )


def _candidate_backed(candidate: VerifiedCandidateRecord) -> bool:
    return bool(
        candidate.certificate_id
        and candidate.artifact_ids
        and candidate.objective_report_present
        and candidate.robustness_report_present
    )


def _pool_metadata(
    bank: _TraceBank,
) -> Dict[str, Any]:
    outcomes = list(bank.hidden_outcomes.values())
    targets = [
        item.best_target_score
        for item in outcomes
        if item.best_target_score is not None
    ]
    robustness = [
        item.best_robustness_score
        for item in outcomes
        if item.best_robustness_score is not None
    ]
    backed = [
        candidate
        for outcome in outcomes
        for candidate in outcome.candidates
        if _candidate_backed(candidate)
    ]
    all_candidates = [
        candidate
        for outcome in outcomes
        for candidate in outcome.candidates
    ]
    return {
        "route_count": len(outcomes),
        "oracle_best_target": max(targets) if targets else None,
        "oracle_best_robustness": max(robustness) if robustness else None,
        "target_std": (
            float(
                (
                    sum((value - sum(targets) / len(targets)) ** 2
                        for value in targets)
                    / len(targets)
                )
                ** 0.5
            )
            if len(targets) > 1
            else 0.0
        ),
        "kinds": sorted({item.route_kind for item in bank.public_pool}),
        "materials": sorted(
            {
                material
                for item in bank.public_pool
                for material in item.materials
            }
        ),
        "layer_counts": sorted(
            {item.layer_count for item in bank.public_pool}
        ),
        "pool_candidates": len(all_candidates),
        "pool_backed_candidates": len(backed),
        "optimizers": sorted(
            {
                candidate.optimizer_id
                for candidate in all_candidates
                if candidate.optimizer_id
            }
        ),
        "pool_cost": sum(
            float(
                item.budget_usage.get("forward_evaluations")
                if isinstance(
                    item.budget_usage.get("forward_evaluations"), (int, float)
                )
                else 1.0
            )
            for item in outcomes
        ),
        "robustness_routes": sum(
            1
            for item in outcomes
            if item.best_robustness_score is not None
            and item.physically_valid_candidate_count > 0
        ),
    }


def _resume_equivalence_check(
    bank: _TraceBank,
    strategy: TournamentStrategy,
    budget: int,
) -> Tuple[bool, str]:
    full = run_policy_trace(bank, strategy, budget)
    if not full.selected_order:
        return True, "no selections to resume"
    partial_strategy = strategy.clone()
    state = _TraceState()
    _advance_trace(bank, partial_strategy, budget, state)
    checkpoint = make_checkpoint(bank, partial_strategy, budget, state)
    try:
        resumed = resume_policy_trace(bank, strategy, budget, checkpoint)
    except Exception as exc:
        return False, f"resume raised: {exc}"
    equal = _canonical_json(
        resumed.model_dump(mode="json")
    ) == _canonical_json(full.model_dump(mode="json"))
    return equal, "resume byte-equivalence verified" if equal else "resume differs"


def _build_trace_ledger(
    bank: _TraceBank,
    trace: RouteTrace,
) -> Tuple[AuditLedgerEntry, ...]:
    entries: List[AuditLedgerEntry] = []
    selected = set(trace.selected_order)
    for route in bank.public_pool:
        outcome = bank.hidden_outcomes[route.route_id]
        if route.route_id in selected:
            status = "selected"
            reason = "selected by this strategy at this budget"
        elif outcome.failure_categories:
            status = "failed_negative"
            reason = "historical failure/negative route preserved"
        else:
            status = "unselected"
            reason = "not selected by this strategy at this budget"
        entries.append(
            AuditLedgerEntry(
                strategy_id=trace.strategy_id,
                strategy_version=trace.strategy_version,
                route_budget=trace.route_budget,
                route_id=route.route_id,
                iteration_id=outcome.iteration_id,
                status=status,
                public_hash=_route_public_hash(route),
                outcome_hash=outcome.outcome_hash,
                candidate_ids=tuple(
                    candidate.candidate_id
                    for candidate in outcome.candidates
                ),
                failure_categories=outcome.failure_categories,
                reason=reason,
            )
        )
    for item in trace.invalid_attempts:
        entries.append(
            AuditLedgerEntry(
                strategy_id=trace.strategy_id,
                strategy_version=trace.strategy_version,
                route_budget=trace.route_budget,
                route_id=item.get("route_id", ""),
                iteration_id="",
                status="rejected_invalid",
                public_hash="0" * 64,
                outcome_hash="0" * 64,
                candidate_ids=(),
                failure_categories=(),
                reason=item.get("reason", "invalid or repeated selection"),
            )
        )
    return tuple(entries)


class AuditLedgerEntry(_StrictModel):
    schema_version: Literal["tournament-audit-entry.v2"] = (
        "tournament-audit-entry.v2"
    )
    strategy_id: str
    strategy_version: int
    route_budget: int
    route_id: str
    iteration_id: str = ""
    status: Literal[
        "selected",
        "unselected",
        "failed_negative",
        "rejected_invalid",
        "not_run",
    ]
    public_hash: str
    outcome_hash: str
    candidate_ids: Tuple[str, ...] = Field(default_factory=tuple)
    failure_categories: Tuple[str, ...] = Field(default_factory=tuple)
    reason: str = ""

    @field_validator("public_hash", "outcome_hash")
    @classmethod
    def _hex_hash(cls, value: str) -> str:
        if len(value) != 64 or any(
            char not in "0123456789abcdef" for char in value
        ):
            raise ValueError("hashes must be 64-character hex digests")
        return value


def _route_public_hash(route: PublicRouteDescriptor) -> str:
    return hashlib.sha256(
        _canonical_json(route.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


def evaluate_trace(
    bank: _TraceBank,
    trace: RouteTrace,
    pool: Dict[str, Any],
    trace_ledger: Sequence[AuditLedgerEntry],
    resume_equivalence: Tuple[bool, str],
    *,
    checkpoint_audited: bool = True,
) -> MetricVector:
    """Post-hoc deterministic evaluation of a completed trace."""

    selected = list(trace.selected_order)
    revealed = {item.route_id: item for item in trace.revealed}
    selected_outcomes = [
        revealed[route_id]
        for route_id in selected
        if route_id in revealed
    ]
    metrics: Dict[str, MetricValue] = {}

    def add(
        name: str,
        *,
        raw: Any = None,
        normalized: Optional[float] = None,
        not_applicable: bool = False,
        reason: str = "",
    ) -> None:
        metrics[name] = MetricValue(
            name=name,
            raw=raw,
            normalized=normalized,
            not_applicable=not_applicable,
            reason=reason,
        )

    selected_ids = set(selected)
    selected_kinds = {
        item.route_kind
        for item in bank.public_pool
        if item.route_id in selected_ids
    }
    selected_materials = {
        material
        for item in bank.public_pool
        if item.route_id in selected_ids
        for material in item.materials
    }
    selected_layers = {
        item.layer_count
        for item in bank.public_pool
        if item.route_id in selected_ids
    }
    pool_kinds = set(pool["kinds"])
    pool_materials = set(pool["materials"])
    pool_layers = set(pool["layer_counts"])
    coverage_parts: List[float] = []
    coverage_raw: Dict[str, Any] = {}
    if pool_kinds:
        value = len(selected_kinds & pool_kinds) / len(pool_kinds)
        coverage_parts.append(value)
        coverage_raw["kind_coverage"] = round(value, 6)
    if pool_materials:
        value = len(selected_materials & pool_materials) / len(pool_materials)
        coverage_parts.append(value)
        coverage_raw["material_coverage"] = round(value, 6)
    if pool_layers:
        value = len(selected_layers & pool_layers) / len(pool_layers)
        coverage_parts.append(value)
        coverage_raw["layer_coverage"] = round(value, 6)
    if coverage_parts:
        add(
            "coverage",
            raw=coverage_raw,
            normalized=_normalized(sum(coverage_parts) / len(coverage_parts)),
        )
    else:
        add(
            "coverage",
            not_applicable=True,
            reason="no public design dimensions available in the pool",
        )

    oracle_target = pool["oracle_best_target"]
    oracle_robustness = pool["oracle_best_robustness"]
    best_target = max(
        (
            item.best_target_score
            for item in selected_outcomes
            if item.best_target_score is not None
        ),
        default=None,
    )
    best_robustness = max(
        (
            item.best_robustness_score
            for item in selected_outcomes
            if item.best_robustness_score is not None
        ),
        default=None,
    )
    gain_parts: List[float] = []
    gain_raw: Dict[str, Any] = {}
    if oracle_target and best_target is not None:
        gain_parts.append(_normalized(best_target / oracle_target))
        gain_raw["best_target"] = best_target
        gain_raw["oracle_best_target"] = oracle_target
        gain_raw["oracle_regret_target"] = round(oracle_target - best_target, 6)
    if oracle_robustness and best_robustness is not None:
        gain_parts.append(_normalized(best_robustness / oracle_robustness))
        gain_raw["best_robustness"] = best_robustness
        gain_raw["oracle_best_robustness"] = oracle_robustness
        gain_raw["oracle_regret_robustness"] = round(
            oracle_robustness - best_robustness, 6
        )
    if gain_parts:
        add(
            "experimental_gain",
            raw=gain_raw,
            normalized=_normalized(sum(gain_parts) / len(gain_parts)),
        )
    else:
        add(
            "experimental_gain",
            not_applicable=True,
            reason="no revealed scores available for gain",
        )

    selected_count = len(selected)
    if selected_count < 2:
        add(
            "discrimination",
            not_applicable=True,
            reason="fewer than two selected routes",
        )
    else:
        comparisons = selected_count * (selected_count - 1) // 2
        max_comparisons = max(
            1,
            pool["route_count"] * (pool["route_count"] - 1) // 2,
        )
        comparison_ratio = min(1.0, comparisons / max_comparisons)
        selected_targets = sorted(
            item.best_target_score
            for item in selected_outcomes
            if item.best_target_score is not None
        )
        if len(selected_targets) >= 2:
            mean = sum(selected_targets) / len(selected_targets)
            target_std = float(
                (
                    sum((value - mean) ** 2 for value in selected_targets)
                    / len(selected_targets)
                )
                ** 0.5
            )
        else:
            target_std = 0.0
        separation_ratio = (
            _normalized(target_std / pool["target_std"])
            if pool["target_std"] > 0
            else 0.0
        )
        add(
            "discrimination",
            raw={
                "route_comparisons": comparisons,
                "target_std": round(target_std, 6),
            },
            normalized=_normalized(
                0.5 * comparison_ratio + 0.5 * separation_ratio
            ),
        )

    selected_backed = sum(
        1
        for outcome in selected_outcomes
        for candidate in outcome.candidates
        if _candidate_backed(candidate)
    )
    if pool["pool_backed_candidates"] > 0:
        add(
            "fact_yield",
            raw={
                "backed_candidate_records": selected_backed,
                "certificate_ids": sorted(
                    {
                        candidate.certificate_id
                        for outcome in selected_outcomes
                        for candidate in outcome.candidates
                        if candidate.certificate_id
                    }
                ),
                "artifact_ids": sorted(
                    {
                        artifact
                        for outcome in selected_outcomes
                        for candidate in outcome.candidates
                        for artifact in candidate.artifact_ids
                    }
                ),
            },
            normalized=_normalized(
                selected_backed / pool["pool_backed_candidates"]
            ),
        )
    else:
        add(
            "fact_yield",
            not_applicable=True,
            reason="pool carries no certificate/artifact/objective/robustness "
            "backed candidate records",
        )

    if selected_count == 0:
        add(
            "figure_readiness",
            not_applicable=True,
            reason="no routes selected",
        )
    else:
        visual_artifacts = {
            artifact
            for outcome in selected_outcomes
            for artifact in _route_visual_artifacts(outcome)
        }
        evidence_ready = sum(
            1
            for outcome in selected_outcomes
            if _route_visual_artifacts(outcome)
        )
        add(
            "figure_readiness",
            raw={
                "visual_artifact_identities": sorted(visual_artifacts),
                "evidence_ready_routes": evidence_ready,
            },
            normalized=_normalized(evidence_ready / selected_count),
            reason=(
                "derived from explicit per-candidate visual/table artifact "
                "identities only"
                if evidence_ready == 0
                else ""
            ),
        )

    if selected_count == 0:
        add(
            "robustness_coverage",
            not_applicable=True,
            reason="no routes selected",
        )
    else:
        present = sum(
            1
            for item in selected_outcomes
            if item.best_robustness_score is not None
            and item.physically_valid_candidate_count > 0
        )
        robustness_gain = (
            _normalized(best_robustness / oracle_robustness)
            if oracle_robustness and best_robustness is not None
            else None
        )
        parts = [present / selected_count]
        if robustness_gain is not None:
            parts.append(robustness_gain)
        add(
            "robustness_coverage",
            raw={
                "robustness_present_routes": present,
                "best_robustness": best_robustness,
            },
            normalized=_normalized(sum(parts) / len(parts)),
        )

    selected_optimizers = {
        candidate.optimizer_id
        for outcome in selected_outcomes
        for candidate in outcome.candidates
        if candidate.optimizer_id
    }
    pool_optimizers = set(pool["optimizers"])
    selected_candidate_total = sum(
        len(outcome.candidates) for outcome in selected_outcomes
    )
    selected_robust_candidates = sum(
        1
        for outcome in selected_outcomes
        for candidate in outcome.candidates
        if candidate.robustness_report_present
    )
    if pool_optimizers and selected_candidate_total > 0:
        optimizer_ratio = len(selected_optimizers & pool_optimizers) / len(
            pool_optimizers
        )
        ablation_ratio = selected_robust_candidates / selected_candidate_total
        add(
            "optimizer_ablation_coverage",
            raw={
                "selected_optimizers": sorted(selected_optimizers),
                "robustness_ablation_candidates": selected_robust_candidates,
            },
            normalized=_normalized(
                0.6 * optimizer_ratio + 0.4 * ablation_ratio
            ),
        )
    else:
        add(
            "optimizer_ablation_coverage",
            not_applicable=True,
            reason="no optimizer or candidate records available",
        )

    invalid_count = len(trace.invalid_attempts)
    total_attempts = selected_count + invalid_count
    add(
        "validity_ratio",
        raw={"selected": selected_count, "invalid": invalid_count},
        normalized=(
            _normalized(selected_count / total_attempts)
            if total_attempts > 0
            else 0.0
        ),
    )

    selected_cost = sum(
        float(
            item.budget_usage.get("forward_evaluations")
            if isinstance(
                item.budget_usage.get("forward_evaluations"), (int, float)
            )
            else 1.0
        )
        for item in selected_outcomes
    )
    pool_cost = float(pool["pool_cost"])
    selected_efficiency = selected_count / max(1e-9, selected_cost)
    pool_efficiency = pool["route_count"] / max(1e-9, pool_cost)
    if selected_cost > 0 and math.isfinite(selected_efficiency):
        add(
            "efficiency",
            raw={
                "selected_cost": round(selected_cost, 6),
                "pool_cost": round(pool_cost, 6),
                "selected_efficiency": round(selected_efficiency, 6),
            },
            normalized=_normalized(
                selected_efficiency / max(1e-9, pool_efficiency)
            ),
        )
    else:
        add(
            "efficiency",
            not_applicable=True,
            reason="no finite route cost available",
        )

    saved_cost = 0.0
    if trace.stop_reason in {STOP_BUDGET_EXHAUSTED, STOP_POLICY_STOP}:
        saved_cost = _normalized(
            (pool_cost - selected_cost) / max(1e-9, pool_cost)
        )
    regret_norm: Optional[float] = None
    if oracle_target and best_target is not None:
        regret = oracle_target - best_target
        all_targets = [
            item.best_target_score
            for item in bank.hidden_outcomes.values()
            if item.best_target_score is not None
        ]
        regret_range = (
            oracle_target - min(all_targets)
            if all_targets
            else 0.0
        )
        regret_norm = (
            _normalized(1.0 - regret / max(1e-9, regret_range))
            if regret_range > 0
            else (1.0 if regret <= 1e-9 else 0.0)
        )
    frontier: List[float] = []
    best_so_far: Optional[float] = None
    for outcome in selected_outcomes:
        score = outcome.best_target_score
        if score is None:
            continue
        best_so_far = (
            score if best_so_far is None else max(best_so_far, score)
        )
        frontier.append(best_so_far)
    last_frontier_gain = 0.0
    if len(frontier) >= 2:
        last_frontier_gain = frontier[-1] - frontier[-2]
    diminishing_return_quality: Optional[float] = None
    if trace.stop_reason == STOP_POLICY_STOP:
        # Stopping right after a large best-so-far improvement is weak
        # evidence of diminishing returns; zero/no improvement supports it.
        diminishing_return_quality = 1.0 - _normalized(
            max(0.0, last_frontier_gain) / max(1e-9, pool["target_std"])
        )
    parts = [saved_cost]
    if regret_norm is not None:
        parts.append(regret_norm)
    if diminishing_return_quality is not None:
        parts.append(diminishing_return_quality)
    add(
        "stop_quality",
        raw={
            "stop_reason": trace.stop_reason,
            "saved_cost": round(saved_cost, 6),
            "best_so_far_frontier": frontier,
            "last_frontier_gain": round(last_frontier_gain, 6),
            "diminishing_return_quality": (
                round(diminishing_return_quality, 6)
                if diminishing_return_quality is not None
                else None
            ),
            "interpretation": (
                "diminishing-return evidence from the last best-so-far "
                "frontier step (policy_stop only); budget-exhausted, "
                "pool-exhausted, and invalid stops get no last-step "
                "marginal-gain bonus"
            ),
            "oracle_regret": (
                round(oracle_target - best_target, 6)
                if oracle_target and best_target is not None
                else None
            ),
        },
        normalized=_normalized(sum(parts) / len(parts)),
    )

    equivalence_ok, equivalence_detail = resume_equivalence
    if checkpoint_audited:
        add(
            "checkpoint_resume_equivalence",
            raw={"verified": equivalence_ok, "detail": equivalence_detail},
            normalized=1.0 if equivalence_ok else 0.0,
            reason=equivalence_detail,
        )
    else:
        add(
            "checkpoint_resume_equivalence",
            not_applicable=True,
            reason=(
                "unregistered strategy has no registered checkpoint "
                "validator; equivalence is not audited"
            ),
        )

    trace_route_ids = {
        entry.route_id
        for entry in trace_ledger
        if entry.status != "rejected_invalid"
    }
    pool_route_ids = {item.route_id for item in bank.public_pool}
    candidate_ids_ok = all(
        outcome.candidates
        == bank.hidden_outcomes[outcome.route_id].candidates
        for outcome in trace.revealed
    )
    provenance_ok = (
        trace_route_ids == pool_route_ids
        and len({entry.route_id for entry in trace_ledger}) == len(trace_ledger)
        and candidate_ids_ok
    )
    add(
        "provenance_preservation",
        raw={
            "ledger_route_count": len(trace_route_ids),
            "pool_route_count": len(pool_route_ids),
            "candidate_hashes_preserved": candidate_ids_ok,
        },
        normalized=1.0 if provenance_ok else 0.0,
        reason=(
            "per-trace audit ledger covers every pool route with preserved "
            "candidate identities and hashes"
            if provenance_ok
            else "audit ledger or candidate identities are incomplete"
        ),
    )
    vector = MetricVector(
        trace_id=trace.trace_id,
        strategy_id=trace.strategy_id,
        strategy_version=trace.strategy_version,
        route_budget=trace.route_budget,
        metrics=metrics,
        vector_hash="",
    )
    vector = _apply_composite(vector)
    return _rehash_vector(vector)


def _apply_composite(vector: MetricVector) -> MetricVector:
    weights = dict(COMPOSITE_WEIGHTS)
    total_weight = sum(weights.values())
    if abs(total_weight - 1.0) > 1e-9:
        raise ValueError("composite weights must sum to 1.0")
    available = {
        name: metric
        for name, metric in vector.metrics.items()
        if metric.normalized is not None and not metric.not_applicable
    }
    applied: Dict[str, float] = {}
    composite: Optional[float] = None
    if available:
        weight_sum = sum(weights[name] for name in available)
        if weight_sum > 0:
            applied = {
                name: round(weights[name] / weight_sum, 6)
                for name in available
            }
            composite = round(
                sum(
                    applied[name] * float(available[name].normalized)
                    for name in available
                ),
                6,
            )
    return vector.model_copy(
        update={
            "composite_score": composite,
            "composite_weights_applied": applied,
        }
    )


def _rehash_vector(vector: MetricVector) -> MetricVector:
    vector_hash = hashlib.sha256(
        _canonical_json(
            vector.model_dump(exclude={"vector_hash"}, mode="json")
        ).encode("utf-8")
    ).hexdigest()
    return vector.model_copy(update={"vector_hash": vector_hash})


class PolicyTournamentResult(_StrictModel):
    schema_version: Literal["tournament-policy-result.v1"] = (
        "tournament-policy-result.v1"
    )
    trace_id: str
    strategy_id: str
    strategy_version: int
    traces: Tuple[RouteTrace, ...] = Field(default_factory=tuple)
    metric_vectors: Tuple[MetricVector, ...] = Field(default_factory=tuple)


class BankTournamentResult(_StrictModel):
    schema_version: Literal["tournament-bank-result.v1"] = (
        "tournament-bank-result.v1"
    )
    trace_id: str
    run_id: str
    question: str
    source_bindings: Dict[str, SourceFileBinding] = Field(default_factory=dict)
    public_pool: Tuple[PublicRouteDescriptor, ...] = Field(default_factory=tuple)
    planned_not_run: Tuple[PublicRouteDescriptor, ...] = Field(
        default_factory=tuple
    )
    budgets_used: Tuple[int, ...] = Field(default_factory=tuple)
    executed_route_count: int = 0
    planned_route_count: int = 0
    outcome_inventory: Dict[str, RevealedOutcome] = Field(default_factory=dict)
    full_pool_oracle: Dict[str, Any] = Field(default_factory=dict)
    policies: Tuple[PolicyTournamentResult, ...] = Field(default_factory=tuple)
    audit_ledger: Tuple[AuditLedgerEntry, ...] = Field(default_factory=tuple)


class TournamentResult(_StrictModel):
    schema_version: Literal["article-tournament-result.v2"] = (
        "article-tournament-result.v2"
    )
    evaluator_contract_version: str
    result_id: str = ""
    banks: Tuple[BankTournamentResult, ...] = Field(default_factory=tuple)
    limitations: Tuple[str, ...] = Field(default_factory=tuple)


def _normalize_budgets(
    budgets: Optional[Sequence[int]],
    executed_count: int,
) -> Tuple[int, ...]:
    if budgets is None:
        values = list(range(1, executed_count + 1))
    else:
        values = list(budgets)
    normalized: List[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("route budgets must be non-bool integers")
        if value < 1 or value > executed_count:
            raise ValueError(
                f"route budget {value} outside 1..{executed_count}"
            )
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError("route budgets must be unique")
    return tuple(sorted(normalized))


def _validate_strategies(strategies: Sequence[TournamentStrategy]) -> None:
    if not strategies:
        raise ValueError("at least one strategy is required")
    pairs = [(item.strategy_id, item.strategy_version) for item in strategies]
    if len(set(pairs)) != len(pairs):
        raise ValueError("strategy id/version pairs must be unique")


def _pareto_members(
    vectors: Sequence[MetricVector],
) -> Dict[str, bool]:
    members: Dict[str, bool] = {}
    for candidate in vectors:
        dominated = False
        for other in vectors:
            if other is candidate:
                continue
            common = [
                name
                for name in candidate.metrics
                if name in other.metrics
                and candidate.metrics[name].normalized is not None
                and other.metrics[name].normalized is not None
            ]
            if not common:
                continue
            strictly_better = any(
                other.metrics[name].normalized
                > candidate.metrics[name].normalized
                for name in common
            )
            no_worse = all(
                other.metrics[name].normalized
                >= candidate.metrics[name].normalized
                for name in common
            )
            if strictly_better and no_worse:
                dominated = True
                break
        members[candidate.vector_hash] = not dominated
    return members


def run_bank_tournament(
    bank: _TraceBank,
    *,
    budgets: Optional[Sequence[int]] = None,
    strategies: Optional[Sequence[TournamentStrategy]] = None,
) -> BankTournamentResult:
    strategy_list = list(strategies or default_strategies())
    _validate_strategies(strategy_list)
    budget_list = _normalize_budgets(budgets, bank.route_count)
    pool = _pool_metadata(bank)
    policy_results: List[PolicyTournamentResult] = []
    for strategy in strategy_list:
        traces: List[RouteTrace] = []
        vectors: List[MetricVector] = []
        checkpoint_audited = (
            strategy.strategy_id,
            strategy.strategy_version,
        ) in REGISTERED_BUILTIN_PAIRS
        for budget in budget_list:
            trace = run_policy_trace(bank, strategy, budget)
            traces.append(trace)
            trace_ledger = _build_trace_ledger(bank, trace)
            equivalence = (
                _resume_equivalence_check(bank, strategy, budget)
                if checkpoint_audited
                else (False, "unregistered strategy; not audited")
            )
            vectors.append(
                evaluate_trace(
                    bank,
                    trace,
                    pool,
                    trace_ledger,
                    equivalence,
                    checkpoint_audited=checkpoint_audited,
                )
            )
        policy_results.append(
            PolicyTournamentResult(
                trace_id=bank.trace_id,
                strategy_id=strategy.strategy_id,
                strategy_version=strategy.strategy_version,
                traces=tuple(traces),
                metric_vectors=tuple(vectors),
            )
        )
    for budget in budget_list:
        budget_vectors = [
            vector
            for policy in policy_results
            for vector in policy.metric_vectors
            if vector.route_budget == budget
        ]
        members = _pareto_members(budget_vectors)
        for policy_index, policy in enumerate(policy_results):
            updated = []
            for vector in policy.metric_vectors:
                if vector.route_budget != budget:
                    updated.append(vector)
                    continue
                member = members.get(vector.vector_hash, False)
                updated.append(_rehash_vector(vector.model_copy(
                    update={"pareto_member": member}
                )))
            policy_results[policy_index] = policy.model_copy(
                update={"metric_vectors": tuple(updated)}
            )
    ledger: List[AuditLedgerEntry] = []
    for policy in policy_results:
        for trace in policy.traces:
            ledger.extend(_build_trace_ledger(bank, trace))
    for route in bank.planned_not_run:
        ledger.append(
            AuditLedgerEntry(
                strategy_id="",
                strategy_version=0,
                route_budget=0,
                route_id=route.route_id,
                iteration_id="",
                status="not_run",
                public_hash=_route_public_hash(route),
                outcome_hash="0" * 64,
                candidate_ids=(),
                failure_categories=(),
                reason="planned in STRATEGY_PLAN but never executed",
            )
        )
    return BankTournamentResult(
        trace_id=bank.trace_id,
        run_id=bank.run_id,
        question=bank.question,
        source_bindings=bank.source_bindings,
        public_pool=bank.public_pool,
        planned_not_run=bank.planned_not_run,
        budgets_used=budget_list,
        executed_route_count=bank.route_count,
        planned_route_count=len(bank.planned_not_run) + bank.route_count,
        outcome_inventory={
            route_id: _deep_copy_model(outcome)
            for route_id, outcome in bank.hidden_outcomes.items()
        },
        full_pool_oracle=pool,
        policies=tuple(policy_results),
        audit_ledger=tuple(ledger),
    )


def compute_tournament_result_id(result: TournamentResult) -> str:
    return hashlib.sha256(
        _canonical_json(
            result.model_dump(exclude={"result_id"}, mode="json")
        ).encode("utf-8")
    ).hexdigest()


def run_tournament(
    trace_dirs: Sequence[str | Path],
    *,
    budgets: Optional[Sequence[int]] = None,
    strategies: Optional[Sequence[TournamentStrategy]] = None,
) -> TournamentResult:
    """Run all policies across one or more trace banks and budget curves."""

    banks = [load_trace_bank(item) for item in trace_dirs]
    if not banks:
        raise ValueError("at least one trace bank directory is required")
    bank_results = [
        run_bank_tournament(bank, budgets=budgets, strategies=strategies)
        for bank in banks
    ]
    executed_counts = [bank.route_count for bank in banks]
    result = TournamentResult(
        evaluator_contract_version=EVALUATOR_CONTRACT_VERSION,
        banks=tuple(bank_results),
        limitations=(
            (
                f"Retrospective strategy replay over {len(banks)} trace "
                f"bank(s) with executed route counts {executed_counts}; this "
                "is policy replay, not fresh solver performance and not a "
                "claim of general scientific superiority."
            ),
            "Only public route descriptors and outcomes revealed by prior "
            "selections were visible to strategies; the evaluator used the "
            "full-pool oracle after each trace completed.",
            "Composite ranking is one documented view; Pareto membership is "
            "reported alongside and no absolute single winner is forced.",
        ),
    )
    return result.model_copy(update={"result_id": compute_tournament_result_id(result)})


class TournamentIntegrityError(ValueError):
    pass


def validate_tournament_result(
    result: TournamentResult | Mapping[str, Any],
    *,
    trace_dirs: Optional[Sequence[str | Path]] = None,
    errors: Optional[List[str]] = None,
) -> bool:
    """Public deterministic semantic validator for a tournament result.

    Recomputes every deterministically derivable field from the public pool,
    outcome inventory, source bindings, planned-not-run descriptors, traces,
    and registered built-in strategies.  A rehashed forged metric, composite,
    Pareto flag, ledger row, or inventory entry is rejected.
    """

    if errors is None:
        errors = []
    model = (
        result
        if isinstance(result, TournamentResult)
        else TournamentResult.model_validate(result)
    )
    recomputed_id = compute_tournament_result_id(model)
    if recomputed_id != model.result_id:
        errors.append("tournament result_id does not match recomputed identity")
    if model.evaluator_contract_version != EVALUATOR_CONTRACT_VERSION:
        errors.append("tournament evaluator contract version is incompatible")
    strategies_by_pair = {
        (item.strategy_id, item.strategy_version): item
        for item in default_strategies()
    }
    for bank in model.banks:
        expected_trace_id = hashlib.sha256(
            _canonical_json(
                {
                    "run_id": bank.run_id,
                    "question": bank.question,
                    "source_hashes": {
                        name: binding.sha256
                        for name, binding in bank.source_bindings.items()
                    },
                }
            ).encode("utf-8")
        ).hexdigest()
        if expected_trace_id != bank.trace_id:
            errors.append(
                f"bank {bank.trace_id} trace_id does not match its sources"
            )
        if len(bank.public_pool) != bank.executed_route_count:
            errors.append(
                f"bank {bank.trace_id} executed_route_count mismatch"
            )
        if len(bank.planned_not_run) + bank.executed_route_count != (
            bank.planned_route_count
        ):
            errors.append(
                f"bank {bank.trace_id} planned_route_count mismatch"
            )
        pool_ids = {item.route_id for item in bank.public_pool}
        not_run_ids = {item.route_id for item in bank.planned_not_run}
        if pool_ids & not_run_ids:
            errors.append(
                f"bank {bank.trace_id} not_run routes overlap the pool"
            )
        inventory_ids = set(bank.outcome_inventory)
        if inventory_ids != pool_ids:
            errors.append(
                f"bank {bank.trace_id} outcome inventory does not match "
                "the public pool"
            )
        for route_id, outcome in bank.outcome_inventory.items():
            if _outcome_content_hash(outcome) != outcome.outcome_hash:
                errors.append(
                    f"bank {bank.trace_id} inventory outcome {route_id} "
                    "hash does not match its content"
                )
            for candidate in outcome.candidates:
                recomputed_candidate = _candidate_record(
                    {
                        "candidate_id": candidate.candidate_id,
                        "experiment_id": candidate.experiment_id,
                        "optimizer_id": candidate.optimizer_id,
                        "certificate_id": candidate.certificate_id,
                        "artifact_ids": list(candidate.artifact_ids),
                        "objective_report": (
                            {} if candidate.objective_report_present else None
                        ),
                        "robustness_report": (
                            {} if candidate.robustness_report_present else None
                        ),
                        "target_score": candidate.target_score,
                        "robustness_score": candidate.robustness_score,
                    },
                    "validate",
                )
                if recomputed_candidate.candidate_hash != candidate.candidate_hash:
                    errors.append(
                        f"bank {bank.trace_id} candidate hash does not "
                        "match its content"
                    )
        reconstructed = _TraceBank(
            trace_id=bank.trace_id,
            run_id=bank.run_id,
            question=bank.question,
            source_bindings={
                name: binding
                for name, binding in bank.source_bindings.items()
            },
            public_pool=bank.public_pool,
            hidden_outcomes={
                route_id: _deep_copy_model(outcome)
                for route_id, outcome in bank.outcome_inventory.items()
            },
            planned_not_run=bank.planned_not_run,
            problem_metadata={},
        )
        recomputed_oracle = _pool_metadata(reconstructed)
        if recomputed_oracle != bank.full_pool_oracle:
            errors.append(
                f"bank {bank.trace_id} full_pool_oracle does not match "
                "recomputation"
            )
        try:
            canonical_budgets = _normalize_budgets(
                bank.budgets_used, bank.executed_route_count
            )
        except ValueError:
            canonical_budgets = ()
            errors.append(
                f"bank {bank.trace_id} budgets are not valid integers "
                "within 1..executed route count"
            )
        if tuple(bank.budgets_used) != canonical_budgets:
            errors.append(
                f"bank {bank.trace_id} budgets are not canonical"
            )
        expected_not_run = [
            AuditLedgerEntry(
                strategy_id="",
                strategy_version=0,
                route_budget=0,
                route_id=route.route_id,
                iteration_id="",
                status="not_run",
                public_hash=_route_public_hash(route),
                outcome_hash="0" * 64,
                candidate_ids=(),
                failure_categories=(),
                reason="planned in STRATEGY_PLAN but never executed",
            )
            for route in bank.planned_not_run
        ]
        stored_not_run = [
            entry
            for entry in bank.audit_ledger
            if entry.status == "not_run"
        ]
        if stored_not_run != expected_not_run:
            errors.append(
                f"bank {bank.trace_id} not_run ledger entries do not match "
                "planned-not-run descriptors"
            )
        policy_pairs = [
            (policy.strategy_id, policy.strategy_version)
            for policy in bank.policies
        ]
        if len(set(policy_pairs)) != len(policy_pairs):
            errors.append(f"bank {bank.trace_id} duplicate policy identities")
        expected_ledger: List[AuditLedgerEntry] = []
        budget_vector_lists: Dict[int, List[MetricVector]] = {}
        for policy in bank.policies:
            if len(policy.traces) != len(bank.budgets_used):
                errors.append(
                    f"bank {bank.trace_id} policy {policy.strategy_id} trace "
                    "count mismatch"
                )
            if len(policy.metric_vectors) != len(bank.budgets_used):
                errors.append(
                    f"bank {bank.trace_id} policy {policy.strategy_id} vector "
                    "count mismatch"
                )
            for trace, vector in zip(policy.traces, policy.metric_vectors):
                if trace.route_budget not in bank.budgets_used:
                    errors.append("trace budget not in the bank budget set")
                if vector.route_budget != trace.route_budget:
                    errors.append("vector budget does not match its trace")
                if trace.trace_id != bank.trace_id:
                    errors.append("trace trace_id does not match its bank")
                if (
                    trace.strategy_id != policy.strategy_id
                    or trace.strategy_version != policy.strategy_version
                ):
                    errors.append("trace strategy identity does not match policy")
                if (
                    vector.trace_id != bank.trace_id
                    or vector.strategy_id != policy.strategy_id
                    or vector.strategy_version != policy.strategy_version
                ):
                    errors.append("vector identity does not match policy/trace")
                recomputed_trace_hash = hashlib.sha256(
                    _canonical_json(
                        trace.model_dump(
                            exclude={"trace_hash"}, mode="json"
                        )
                    ).encode("utf-8")
                ).hexdigest()
                if recomputed_trace_hash != trace.trace_hash:
                    errors.append("trace_hash does not match its content")
                if len(trace.selected_order) != len(set(trace.selected_order)):
                    errors.append("trace selected_order contains duplicates")
                if any(
                    route_id not in pool_ids
                    for route_id in trace.selected_order
                ):
                    errors.append("trace selects unknown routes")
                revealed_ids = [
                    item.route_id for item in trace.revealed
                ]
                if revealed_ids != list(trace.selected_order):
                    errors.append(
                        "trace revealed order does not match selected_order"
                    )
                for outcome in trace.revealed:
                    expected_outcome = bank.outcome_inventory.get(
                        outcome.route_id
                    )
                    if expected_outcome is None:
                        errors.append("trace reveals an unknown route outcome")
                    elif outcome.model_dump(mode="json") != expected_outcome.model_dump(
                        mode="json"
                    ):
                        errors.append(
                            "trace revealed outcome does not equal the "
                            "inventory outcome"
                        )
                trace_ledger = _build_trace_ledger(reconstructed, trace)
                expected_ledger.extend(trace_ledger)
                strategy = strategies_by_pair.get(
                    (trace.strategy_id, trace.strategy_version)
                )
                checkpoint_audited = strategy is not None
                equivalence = (
                    _resume_equivalence_check(
                        reconstructed, strategy, trace.route_budget
                    )
                    if strategy is not None
                    else (False, "unregistered strategy; not audited")
                )
                recomputed_vector = evaluate_trace(
                    reconstructed,
                    trace,
                    recomputed_oracle,
                    trace_ledger,
                    equivalence,
                    checkpoint_audited=checkpoint_audited,
                )
                budget_vector_lists.setdefault(
                    trace.route_budget, []
                ).append(recomputed_vector)
        for budget, vectors in budget_vector_lists.items():
            members = _pareto_members(vectors)
            for recomputed_base in vectors:
                member = members.get(recomputed_base.vector_hash, False)
                recomputed_final = _rehash_vector(
                    recomputed_base.model_copy(
                        update={"pareto_member": member}
                    )
                )
                stored = next(
                    (
                        item
                        for policy in bank.policies
                        for item in policy.metric_vectors
                        if item.route_budget == budget
                        and item.strategy_id
                        == recomputed_final.strategy_id
                        and item.strategy_version
                        == recomputed_final.strategy_version
                    ),
                    None,
                )
                if stored is None:
                    errors.append("stored metric vector is missing")
                elif stored.pareto_member != recomputed_final.pareto_member:
                    errors.append(
                        "pareto_member does not match recomputation"
                    )
                elif recomputed_final.model_dump(mode="json") != stored.model_dump(
                    mode="json"
                ):
                    errors.append(
                        "metric vector does not match deterministic "
                        "recomputation"
                    )
        expected_ledger.extend(expected_not_run)
        if list(bank.audit_ledger) != expected_ledger:
            errors.append(
                f"bank {bank.trace_id} audit ledger does not match the "
                "exact per-trace recomputation"
            )
    if trace_dirs:
        for path in trace_dirs:
            bank = load_trace_bank(path)
            matches = [
                item
                for item in model.banks
                if item.trace_id == bank.trace_id
            ]
            if not matches:
                errors.append(
                    f"no result bank matches trace {bank.trace_id!r}"
                )
                continue
            match = matches[0]
            expected_bindings = {
                name: binding.sha256
                for name, binding in bank.source_bindings.items()
            }
            actual_bindings = {
                name: binding.sha256
                for name, binding in match.source_bindings.items()
            }
            if expected_bindings != actual_bindings:
                errors.append("result source hashes do not match the bank")
    return not errors


def write_tournament_result(
    result: TournamentResult,
    output_dir: str | Path,
    *,
    trace_dirs: Optional[Sequence[str | Path]] = None,
) -> Path:
    """Atomic fixed-name writer; idempotent reuse, conflicting content rejected."""

    validation_errors: List[str] = []
    if not validate_tournament_result(
        result,
        trace_dirs=trace_dirs,
        errors=validation_errors,
    ):
        raise TournamentIntegrityError(
            "refusing to write an invalid tournament result: "
            + "; ".join(validation_errors[:5])
        )
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "ARTICLE_TOURNAMENT_RESULT.json"
    payload = _canonical_json(result.model_dump(mode="json")).encode("utf-8")
    if destination.exists():
        if destination.read_bytes() != payload:
            raise TournamentIntegrityError(
                "existing ARTICLE_TOURNAMENT_RESULT.json conflicts with the "
                "new result"
            )
        return destination
    temporary = destination.with_name(
        destination.name + f".tmp{_digest(payload)}"
    )
    temporary.write_bytes(payload)
    temporary.replace(destination)
    return destination
