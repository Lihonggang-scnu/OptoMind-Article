"""Small deterministic kernel used before any Qwen policy is connected."""

from __future__ import annotations

import hashlib
import json
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict

from optomind_research.runtime.artifact_store import atomic_write_json
from tmm_engine import SimulationTask
from tmm_engine.schemas import dataclass_to_dict

from .contracts import ActionProposal, ActionType, ExperimentStatus
from .experiment_graph import ExperimentGraph
from .portfolio import DesignCandidate, PortfolioSelector
from .solver_registry import SolverRegistry, TMMAdapter


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class OpticalExperimentRuntime:
    def __init__(
        self,
        work_dir: str | Path,
        *,
        run_id: str | None = None,
        solver_registry: SolverRegistry | None = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.solvers = solver_registry or SolverRegistry((TMMAdapter(),))
        self.graph = ExperimentGraph(self.work_dir / "EXPERIMENT_GRAPH.sqlite", self.run_id)

    def build_portfolio(
        self, candidates: list[DesignCandidate], *, max_pareto_candidates: int = 8
    ) -> Dict[str, Any]:
        """Persist several verified trade-off designs; target scores never gate admission."""

        portfolio = PortfolioSelector().select(
            candidates, max_pareto_candidates=max_pareto_candidates
        )
        payload = portfolio.model_dump(mode="json")
        atomic_write_json(self.work_dir / "DESIGN_PORTFOLIO.json", payload)
        return payload

    def run_simulation(self, task: SimulationTask) -> Dict[str, Any]:
        started = time.perf_counter()
        task_payload = dataclass_to_dict(task)
        task_hash = _hash(task_payload)
        atomic_write_json(
            self.work_dir / "TASK.json",
            {
                "schema_version": "optical-experiment-task.v1",
                "run_id": self.run_id,
                "mode": "simulate",
                "simulation": task_payload,
            },
        )
        self._write_state("capability_classification", "running", task_hash=task_hash)
        solver = self.solvers.select(task)
        action = ActionProposal(
            action_type=ActionType.run_solver,
            parameters={"solver_id": solver.descriptor.solver_id if solver else None},
            rationale="Deterministic lowest-fidelity capable solver selection.",
        )
        node_id = self.graph.create_node(task_hash, action)
        node_dir = self.work_dir / "nodes" / node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(node_dir / "ACTION.json", action.model_dump(mode="json"))
        self.graph.set_status(node_id, ExperimentStatus.admitted)
        if solver is None:
            assessment = TMMAdapter().assess(task)
            status = ExperimentStatus.needs_higher_fidelity
            payload = {
                "run_id": self.run_id,
                "status": status.value,
                "node_id": node_id,
                "capability_assessment": assessment.to_dict(),
                "wall_seconds": time.perf_counter() - started,
            }
            self.graph.set_status(node_id, status, failures=assessment.to_dict()["failures"])
            atomic_write_json(node_dir / "OBSERVATION.json", payload)
            return self._finalize(payload)

        self.graph.set_status(node_id, ExperimentStatus.running)
        self._write_state("physics_verification", "running", node_id=node_id, solver_id=solver.descriptor.solver_id)
        try:
            certified = solver.run(task)
        except Exception as exc:  # future third-party adapters may fail outside their verifier
            payload = {
                "run_id": self.run_id,
                "status": ExperimentStatus.failed.value,
                "node_id": node_id,
                "solver_id": solver.descriptor.solver_id,
                "failure_records": [
                    {
                        "code": "solver_runtime_error",
                        "message": f"{type(exc).__name__}: {exc}",
                        "recoverable": True,
                        "suggested_action": "retry_or_switch_solver",
                    }
                ],
                "wall_seconds": time.perf_counter() - started,
            }
            self.graph.record_event(
                node_id,
                "diagnostic",
                {"exception_type": type(exc).__name__, "traceback": traceback.format_exc()},
            )
            self.graph.set_status(node_id, ExperimentStatus.failed)
            atomic_write_json(node_dir / "OBSERVATION.json", payload)
            return self._finalize(payload)
        atomic_write_json(node_dir / "PHYSICS_ACCEPTANCE_CERTIFICATE.json", certified.certificate)
        if certified.result is not None:
            atomic_write_json(node_dir / "SIMULATION_RESULT.json", certified.result.to_dict())
        if certified.certificate["accepted"]:
            status = (
                ExperimentStatus.physically_valid_with_limits
                if certified.certificate["status"] == "physically_valid_with_limits"
                else ExperimentStatus.physically_valid
            )
        else:
            status = ExperimentStatus.rejected_physics
        payload = {
            "run_id": self.run_id,
            "status": status.value,
            "node_id": node_id,
            "solver_id": solver.descriptor.solver_id,
            "certificate_id": certified.certificate["certificate_id"],
            "failure_records": certified.certificate.get("failures", []),
            "wall_seconds": time.perf_counter() - started,
        }
        self.graph.set_status(node_id, status, certificate_id=payload["certificate_id"])
        atomic_write_json(node_dir / "OBSERVATION.json", payload)
        return self._finalize(payload)

    def _finalize(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        graph_payload = self.graph.export()
        atomic_write_json(self.work_dir / "EXPERIMENT_GRAPH.json", graph_payload)
        atomic_write_json(self.work_dir / "FINAL_RESULT.json", payload)
        self._write_state("finished", str(payload.get("status") or "unknown"), node_id=payload.get("node_id"))
        return payload

    def _write_state(self, stage: str, status: str, **extra: Any) -> None:
        atomic_write_json(
            self.work_dir / "RUN_STATE.json",
            {
                "schema_version": "optical-experiment-run-state.v1",
                "run_id": self.run_id,
                "stage": stage,
                "status": status,
                "updated_at_unix": time.time(),
                **extra,
            },
        )


__all__ = ["OpticalExperimentRuntime"]
