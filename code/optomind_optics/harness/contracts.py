"""Typed actions and states; no model-specific behavior belongs here."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class ExperimentStatus(str, Enum):
    proposed = "proposed"
    admitted = "admitted"
    running = "running"
    candidate = "candidate"
    physically_valid = "physically_valid"
    physically_valid_with_limits = "physically_valid_with_limits"
    rejected_physics = "rejected_physics"
    needs_higher_fidelity = "needs_higher_fidelity"
    failed = "failed"
    cancelled = "cancelled"


class ActionType(str, Enum):
    resolve_materials = "resolve_materials"
    generate_baseline = "generate_baseline"
    run_solver = "run_solver"
    run_optimizer = "run_optimizer"
    run_convergence_audit = "run_convergence_audit"
    run_reference_solver = "run_reference_solver"
    run_robustness_audit = "run_robustness_audit"
    switch_optimizer = "switch_optimizer"
    switch_material_dataset = "switch_material_dataset"
    escalate_solver = "escalate_solver"
    fork_experiment = "fork_experiment"
    stop = "stop"


class ActionProposal(BaseModel):
    action_type: ActionType
    parameters: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    proposed_by: str = "deterministic_policy"


class ExperimentObservation(BaseModel):
    status: ExperimentStatus
    metrics: Dict[str, Any] = Field(default_factory=dict)
    failure_records: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    summary: str = ""


__all__ = ["ActionProposal", "ActionType", "ExperimentObservation", "ExperimentStatus"]

