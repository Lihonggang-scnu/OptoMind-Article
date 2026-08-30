"""Deterministic scoring and portfolio selection for verified optical designs.

User targets are preferences, not physical admission gates. A candidate may miss
an ambitious target and still be valuable if it is physically valid. This module
therefore separates physics validity from ranking and returns several useful
trade-off designs instead of collapsing the run to one nominal optimum.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional

from pydantic import BaseModel, Field, field_validator


PHYSICALLY_VALID_STATUSES = {"physically_valid", "physically_valid_with_limits"}
MIN_DISTINCTIVENESS_FOR_ROLE = 0.05


class DesignCandidate(BaseModel):
    candidate_id: str
    physics_status: str
    target_attainment: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    robustness_score: Optional[float] = None
    simplicity_score: Optional[float] = None
    distinctiveness_score: Optional[float] = None
    certificate_id: Optional[str] = None
    artifact_ids: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("robustness_score", "simplicity_score", "distinctiveness_score")
    @classmethod
    def _unit_interval(cls, value: Optional[float]) -> Optional[float]:
        if value is not None and not 0.0 <= float(value) <= 1.0:
            raise ValueError("candidate trait scores must be in [0, 1]")
        return None if value is None else float(value)


class PortfolioSelection(BaseModel):
    schema_version: str = "optical-design-portfolio.v1"
    selection_policy: str = "soft_objective_multi_candidate"
    candidates: List[Dict[str, Any]] = Field(default_factory=list)
    assessed_candidate_count: int = 0
    maximum_candidates: int = 8
    selected_roles: Dict[str, str] = Field(default_factory=dict)
    pareto_candidate_ids: List[str] = Field(default_factory=list)
    rejected_candidate_ids: List[str] = Field(default_factory=list)
    omitted_admissible_candidate_ids: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


def _target_score(item: Dict[str, Any]) -> float:
    """Map an observed target outcome to [0, 1] without a pass threshold."""

    if item.get("soft_score") is not None:
        return float(max(0.0, min(1.0, float(item["soft_score"]))))
    observed = float(item.get("observed", 0.0))
    target = float(item.get("target") or 0.0)
    tolerance = item.get("tolerance")
    scale = max(abs(target), float(tolerance or 0.0), 0.05)
    constraint = str(item.get("constraint", "match"))
    if constraint == "at_least":
        return float(max(0.0, min(1.0, observed / (observed + scale))))
    if constraint == "at_most":
        return float(max(0.0, min(1.0, scale / (max(observed, 0.0) + scale))))
    return float(math.exp(-abs(observed - target) / scale))


def score_candidate(candidate: DesignCandidate) -> Dict[str, Any]:
    weighted = 0.0
    weight_sum = 0.0
    objective_scores: Dict[str, float] = {}
    for name, item in candidate.target_attainment.items():
        if item.get("role") == "report_only":
            continue
        score = _target_score(item)
        weight = max(float(item.get("weight", 1.0)), 0.0)
        objective_scores[str(name)] = score
        weighted += score * weight
        weight_sum += weight
    target_score = weighted / weight_sum if weight_sum else 0.0
    return {
        "candidate_id": candidate.candidate_id,
        "physics_status": candidate.physics_status,
        "physically_admissible": candidate.physics_status in PHYSICALLY_VALID_STATUSES,
        "target_score": float(target_score),
        "objective_scores": objective_scores,
        "robustness_score": candidate.robustness_score,
        "simplicity_score": candidate.simplicity_score,
        "distinctiveness_score": candidate.distinctiveness_score,
        "certificate_id": candidate.certificate_id,
        "artifact_ids": list(candidate.artifact_ids),
        "metadata": dict(candidate.metadata),
    }


def _dominates(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    axes = ("target_score", "robustness_score", "simplicity_score")
    left_values = [float(left.get(axis) or 0.0) for axis in axes]
    right_values = [float(right.get(axis) or 0.0) for axis in axes]
    return all(a >= b for a, b in zip(left_values, right_values)) and any(
        a > b for a, b in zip(left_values, right_values)
    )


def _best(scored: List[Dict[str, Any]], axis: str) -> Optional[str]:
    eligible = [item for item in scored if item.get(axis) is not None]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (float(item[axis]), item["candidate_id"]))[
        "candidate_id"
    ]


class PortfolioSelector:
    """Select a diverse set from physically valid candidates without target gates."""

    def select(
        self,
        candidates: Iterable[DesignCandidate],
        *,
        max_pareto_candidates: int = 8,
        maximum_candidates: int | None = None,
        include_best_target_score: bool = True,
        include_most_robust: bool = True,
        include_simplest_fabrication: bool = True,
        include_structurally_distinctive: bool = True,
        include_pareto_front: bool = True,
    ) -> PortfolioSelection:
        final_limit = int(
            max_pareto_candidates
            if maximum_candidates is None
            else maximum_candidates
        )
        if final_limit < 1:
            raise ValueError("maximum_candidates must be positive")
        scored_all = [score_candidate(item) for item in candidates]
        admissible = [item for item in scored_all if item["physically_admissible"]]
        rejected = [item["candidate_id"] for item in scored_all if not item["physically_admissible"]]
        if not admissible:
            return PortfolioSelection(
                candidates=scored_all,
                assessed_candidate_count=len(scored_all),
                maximum_candidates=final_limit,
                rejected_candidate_ids=rejected,
                notes=["No candidate passed deterministic physics validation."],
            )

        roles: Dict[str, str] = {}
        for enabled, role, axis in (
            (include_best_target_score, "best_target_score", "target_score"),
            (include_most_robust, "most_robust", "robustness_score"),
            (include_simplest_fabrication, "simplest_fabrication", "simplicity_score"),
            (include_structurally_distinctive, "structurally_distinctive", "distinctiveness_score"),
        ):
            if not enabled:
                continue
            candidate_id = _best(admissible, axis)
            if candidate_id is not None and (
                role != "structurally_distinctive"
                or max(
                    float(item.get("distinctiveness_score") or 0.0)
                    for item in admissible
                )
                >= MIN_DISTINCTIVENESS_FOR_ROLE
            ):
                roles[role] = candidate_id

        pareto: list[Dict[str, Any]] = []
        if include_pareto_front:
            pareto = [
                item
                for item in admissible
                if not any(_dominates(other, item) for other in admissible if other is not item)
            ]
            pareto = sorted(
                pareto,
                key=lambda item: (
                    -float(item["target_score"]),
                    -float(item.get("robustness_score") or 0.0),
                    item["candidate_id"],
                ),
            )[: max(1, int(max_pareto_candidates))]
        ranked_admissible = sorted(
            admissible,
            key=lambda item: (
                -float(item["target_score"]),
                -float(item.get("robustness_score") or 0.0),
                -float(item.get("simplicity_score") or 0.0),
                item["candidate_id"],
            ),
        )
        retained_ids: list[str] = []
        for candidate_id in [*roles.values(), *[item["candidate_id"] for item in pareto]]:
            if candidate_id not in retained_ids and len(retained_ids) < final_limit:
                retained_ids.append(candidate_id)
        for item in ranked_admissible:
            if item["candidate_id"] not in retained_ids and len(retained_ids) < final_limit:
                retained_ids.append(item["candidate_id"])
        retained_set = set(retained_ids)
        retained_candidates = [
            item for item in scored_all if item["candidate_id"] in retained_set
        ]
        roles = {
            role: candidate_id
            for role, candidate_id in roles.items()
            if candidate_id in retained_set
        }
        pareto_ids = [
            item["candidate_id"]
            for item in pareto
            if item["candidate_id"] in retained_set
        ]
        omitted = [
            item["candidate_id"]
            for item in admissible
            if item["candidate_id"] not in retained_set
        ]
        return PortfolioSelection(
            candidates=retained_candidates,
            assessed_candidate_count=len(scored_all),
            maximum_candidates=final_limit,
            selected_roles=roles,
            pareto_candidate_ids=pareto_ids,
            rejected_candidate_ids=rejected,
            omitted_admissible_candidate_ids=omitted,
            notes=[
                "Target attainment affects ranking only; it never decides physics validity.",
                "Roles may point to the same candidate when one design leads several views.",
                "Structural distinctiveness is optional and is omitted when no meaningfully distinct verified design exists.",
            ],
        )


__all__ = [
    "DesignCandidate",
    "MIN_DISTINCTIVENESS_FOR_ROLE",
    "PHYSICALLY_VALID_STATUSES",
    "PortfolioSelection",
    "PortfolioSelector",
    "score_candidate",
]
