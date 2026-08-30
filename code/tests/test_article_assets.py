from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import pytest

from optomind_optics.harness.article_assets import (
    ArticleAssetCompilationResult,
    AssetIntegrityError,
    compile_article_assets,
    compute_asset_compilation_result_id,
    validate_asset_compilation_result,
)
from optomind_optics.harness.article_contracts import (
    ExperimentCard,
    ExperimentStatus,
    ObservationCard,
)
from optomind_optics.harness.article_execution import (
    ArticleExecutionResult,
    observation_card_from_tmm_result,
    required_action_for_task,
)
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    CompiledExperimentRequest,
    compute_optical_design_task_digest,
    compute_request_id,
    compute_task_hash,
)
from optomind_optics.harness.contracts import ActionType
from optomind_optics.harness.design_task import OpticalDesignTask
from optomind_optics.harness.provenance import ArtifactLineageStore


REPO_ROOT = Path(__file__).resolve().parents[2]
PBS_RUN = (
    REPO_ROOT
    / "accepted_examples"
    / "research_polarizing_beamsplitter"
    / "iterations"
    / "iteration_02"
    / "tmm_run"
)
EXPERIMENT_ID = "pbs_10layer_opt"
RUN_ID = "generated_polarizing_beamsplitter_cold_v10_20260813.iteration_02"


def _authority(key: bytes = b"assets-test-key") -> ArticleCompilationAuthority:
    return ArticleCompilationAuthority(key)


def _task_payload() -> Dict[str, Any]:
    return json.loads((PBS_RUN / "TASK.json").read_text(encoding="utf-8"))


def _request(
    *,
    authority: ArticleCompilationAuthority,
    run_id: str = RUN_ID,
    experiment_id: str = EXPERIMENT_ID,
    task_payload: Optional[Mapping[str, Any]] = None,
    task_digest: Optional[str] = None,
    proposal_id: str = "proposal-assets-1",
) -> CompiledExperimentRequest:
    task_payload = dict(task_payload or _task_payload())
    task = OpticalDesignTask.model_validate(task_payload)
    action = required_action_for_task(task)
    digest = task_digest or compute_optical_design_task_digest(task)
    card = ExperimentCard(
        experiment_id=experiment_id,
        hypothesis_ids=["hyp-assets-1"],
        action_type=action,
        task_hash="",
    )
    draft = CompiledExperimentRequest(
        request_id="pending",
        task_hash="pending",
        plan_id="plan-assets-1",
        capability_id="cap-assets-1",
        run_id=run_id,
        branch_id="root",
        proposal_id=proposal_id,
        authority_id=authority.authority_id,
        compiler_attestation="pending",
        parameters={"experiment_id": experiment_id, "solver": "smatrix"},
        requested_budget={
            "wall_time_seconds": float(task.budget.wall_time_seconds),
            "forward_evaluations": int(
                task.budget.maximum_forward_evaluations
            ),
            "optimizer_runs": int(task.budget.maximum_optimizer_runs),
        },
        task_digest=digest,
        experiment=card,
        allowed_action=action,
    )
    task_hash = compute_task_hash(draft)
    request_id = compute_request_id(task_hash, proposal_id)
    attested = draft.model_copy(
        update={
            "task_hash": task_hash,
            "request_id": request_id,
            "experiment": card.model_copy(update={"task_hash": task_hash}),
        }
    )
    return attested.model_copy(
        update={"compiler_attestation": authority.attest(attested)}
    )


def _execution_result(
    request: CompiledExperimentRequest,
    run_root: str | Path,
    *,
    experiment_id: str = EXPERIMENT_ID,
    observation: Optional[ObservationCard] = None,
    receipt: Optional[Mapping[str, Any]] = None,
) -> ArticleExecutionResult:
    if observation is None:
        final_payload = json.loads(
            (Path(run_root) / "FINAL_RESULT.json").read_text(encoding="utf-8")
        )
        observation = observation_card_from_tmm_result(
            final_payload,
            run_dir=run_root,
            experiment_id=experiment_id,
        )
    return ArticleExecutionResult(
        request_id=request.request_id,
        task_hash=request.task_hash,
        run_dir=str(run_root),
        observation=observation,
        receipt=dict(
            receipt
            if receipt is not None
            else {"status": "adapter_completed"}
        ),
        outcome=observation.status.value,
    )


def _certificate_payload(certificate_id: str) -> Dict[str, Any]:
    return {
        "schema_version": "physics-acceptance-certificate-v1",
        "certificate_id": certificate_id,
        "accepted": True,
        "status": "physically_valid",
        "task_sha256": "0" * 64,
        "physics_audit": {
            "energy_conservation_max_abs_error": 1.1102230246251565e-16,
            "minimum_observable": 6.05e-06,
            "maximum_observable": 0.970025451795563,
            "nonfinite_value_count": 0,
        },
        "spectral_convergence": {
            "status": "passed",
            "passed": True,
            "final_points": 601,
        },
    }


def _objective_payload() -> Dict[str, Any]:
    return {
        "schema_version": "tmm-objective-report.v1",
        "aggregate_soft_score": 0.55,
        "weighted_directional_loss": 0.21,
        "target_attainment": {
            "canonical_r_500_650_at_least_mean_45_s_1_1": {
                "observable": "R",
                "observed": 0.92,
                "target": 0.9,
                "constraint": "at_least",
                "aggregation": "mean",
                "weight": 1.0,
                "tolerance": None,
                "soft_score": 0.6,
                "role": "soft_scoring_objective",
            }
        },
        "admission_role": "ranking_only",
    }


def _simulation_payload() -> Dict[str, Any]:
    return {
        "wavelengths_nm": [500.0, 575.0, 650.0],
        "channels": {
            "angle=45|pol=s": {
                "R": [0.9, 0.92, 0.88],
                "T": [0.1, 0.08, 0.12],
            },
            "angle=45|pol=p": {
                "R": [0.7, 0.72, 0.68],
                "T": [0.3, 0.28, 0.32],
            },
        },
        "solver": "smatrix",
    }


def _robustness_payload() -> Dict[str, Any]:
    return {
        "schema_version": "tmm-robustness-report.v1",
        "candidate_id": "pbs_10layer_opt__c1",
        "nominal_soft_score": 0.54,
        "mean_soft_score": 0.53,
        "worst_soft_score": 0.51,
        "p10_soft_score": 0.52,
        "robustness_score": 0.9,
        "failed_simulations": 0,
    }


def _identity_payload() -> Dict[str, Any]:
    return {
        "schema_version": "tmm-artifact-identity.v1",
        "experiment_id": EXPERIMENT_ID,
        "candidate_id": "pbs_10layer_opt__c1",
        "physical_directory": "experiments/pbs_10layer_opt/c/c1",
    }


def _candidate_row(
    candidate_id: str,
    *,
    source: str,
    certificate_id: str,
    target_score: float,
    robustness_score: Optional[float],
    artifact_ids: list[str],
    nan_score: bool = False,
) -> Dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "physics_status": "physically_valid",
        "physically_admissible": True,
        "target_score": float("nan") if nan_score else target_score,
        "objective_scores": {"canonical_r_500_650_at_least_mean_45_s_1_1": 0.6},
        "robustness_score": robustness_score,
        "simplicity_score": 0.7,
        "distinctiveness_score": 0.4,
        "certificate_id": certificate_id,
        "artifact_ids": artifact_ids,
        "metadata": {"source": source, "optimizer_id": None},
    }


def _minimal_run(
    tmp_path: Path,
    *,
    final_status: str = "completed",
    duplicate_candidate: bool = False,
    nan_score: bool = False,
    cross_wired_baseline: bool = False,
    cross_wired_objective: bool = False,
    logical_ids: bool = False,
    cross_wired_identity_dir: bool = False,
) -> Path:
    root = tmp_path / "run"
    root.mkdir(parents=True)
    task_payload = _task_payload()
    task_id = str(task_payload["task_id"])
    digest = compute_optical_design_task_digest(task_payload)
    (root / "TASK.json").write_text(
        json.dumps(task_payload, sort_keys=True), encoding="utf-8"
    )
    (root / "ARTIFACT_PATH_INDEX.json").write_text(
        json.dumps(
            {
                "schema_version": "tmm-artifact-path-index.v1",
                "path_policy": "stable_hashed_directories_for_windows_path_safety",
                "experiments": [
                    {
                        "experiment_id": EXPERIMENT_ID,
                        "physical_directory": (
                            f"experiments/{EXPERIMENT_ID}"
                        ),
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    exp_dir = root / "experiments" / EXPERIMENT_ID
    baseline_dir = exp_dir / "baseline"
    c1_dir = exp_dir / "c" / "c1"
    baseline_dir.mkdir(parents=True)
    c1_dir.mkdir(parents=True)

    def _aid(relative: str, artifact_type: str) -> str:
        if not logical_ids:
            return relative
        return (
            f"{artifact_type}:{Path(relative).parent.name}:"
            f"{Path(relative).stem}"
        )

    baseline_cert = "53da6334fbedc0863ed27ecd5cff7ef7fb1dbd0a23de45377e4b8f88902dd1ef"
    c1_cert = "2750f7484c00000000000000000000000000000000000000000000000000000000"
    baseline_cert_rel = (
        f"experiments/{EXPERIMENT_ID}/baseline/"
        "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
    )
    baseline_obj_rel = (
        f"experiments/{EXPERIMENT_ID}/baseline/OBJECTIVE_REPORT.json"
    )
    c1_cert_rel = (
        f"experiments/{EXPERIMENT_ID}/c/c1/"
        "PHYSICS_ACCEPTANCE_CERTIFICATE.json"
    )
    c1_obj_rel = (
        f"experiments/{EXPERIMENT_ID}/c/c1/OBJECTIVE_REPORT.json"
    )
    c1_rob_rel = f"experiments/{EXPERIMENT_ID}/c/c1/ROBUSTNESS.json"
    baseline_artifacts = [
        _aid(baseline_cert_rel, "physics_acceptance_certificate"),
        _aid(baseline_obj_rel, "objective_report"),
    ]
    c1_artifacts = [
        _aid(c1_cert_rel, "physics_acceptance_certificate"),
        _aid(c1_obj_rel, "objective_report"),
        _aid(c1_rob_rel, "robustness_report"),
    ]
    if cross_wired_objective:
        c1_artifacts = [
            _aid(c1_cert_rel, "physics_acceptance_certificate"),
            _aid(baseline_obj_rel, "objective_report"),
            _aid(c1_rob_rel, "robustness_report"),
        ]
    if cross_wired_baseline:
        baseline_artifacts = [
            _aid(c1_cert_rel, "physics_acceptance_certificate"),
            _aid(c1_obj_rel, "objective_report"),
        ]
    candidates = [
        _candidate_row(
            "pbs_10layer_opt__baseline",
            source="initial_baseline",
            certificate_id=(
                c1_cert if cross_wired_baseline else baseline_cert
            ),
            target_score=0.4,
            robustness_score=None,
            artifact_ids=baseline_artifacts,
        ),
        _candidate_row(
            "pbs_10layer_opt__c1",
            source="optimized_best",
            certificate_id=c1_cert,
            target_score=0.65,
            robustness_score=0.9,
            artifact_ids=c1_artifacts,
            nan_score=nan_score,
        ),
    ]
    if duplicate_candidate:
        candidates.append(dict(candidates[1]))
    portfolio = {
        "schema_version": "tmm-design-portfolio.v1",
        "selection_policy": "multi_objective",
        "candidates": candidates,
        "assessed_candidate_count": len(candidates),
        "maximum_candidates": 8,
        "selected_roles": {
            "best_target_score": "pbs_10layer_opt__c1",
            "most_robust": "pbs_10layer_opt__c1",
            "simplest_fabrication": "pbs_10layer_opt__baseline",
        },
        "pareto_candidate_ids": [
            "pbs_10layer_opt__c1",
            "pbs_10layer_opt__baseline",
        ],
        "rejected_candidate_ids": [],
        "omitted_admissible_candidate_ids": [],
        "notes": "minimal fixture",
    }
    (exp_dir / "DESIGN_PORTFOLIO.json").write_text(
        json.dumps(portfolio, sort_keys=True, allow_nan=True), encoding="utf-8"
    )
    files: Dict[str, Dict[str, Any]] = {
        "experiments/{}/baseline/SIMULATION_RESULT.json".format(
            EXPERIMENT_ID
        ): {"artifact_type": "simulation_result", "data": _simulation_payload()},
        "experiments/{}/baseline/PHYSICS_ACCEPTANCE_CERTIFICATE.json".format(
            EXPERIMENT_ID
        ): {
            "artifact_type": "physics_acceptance_certificate",
            "data": _certificate_payload(baseline_cert),
            "inputs": [
                _aid(
                    "experiments/{}/baseline/SIMULATION_RESULT.json".format(
                        EXPERIMENT_ID
                    ),
                    "simulation_result",
                )
            ],
        },
        "experiments/{}/baseline/OBJECTIVE_REPORT.json".format(
            EXPERIMENT_ID
        ): {
            "artifact_type": "objective_report",
            "data": _objective_payload(),
            "inputs": [
                _aid(
                    "experiments/{}/baseline/SIMULATION_RESULT.json".format(
                        EXPERIMENT_ID
                    ),
                    "simulation_result",
                )
            ],
        },
        "experiments/{}/c/c1/SIMULATION_RESULT.json".format(
            EXPERIMENT_ID
        ): {"artifact_type": "simulation_result", "data": _simulation_payload()},
        "experiments/{}/c/c1/PHYSICS_ACCEPTANCE_CERTIFICATE.json".format(
            EXPERIMENT_ID
        ): {
            "artifact_type": "physics_acceptance_certificate",
            "data": _certificate_payload(c1_cert),
            "inputs": [
                _aid(
                    "experiments/{}/c/c1/SIMULATION_RESULT.json".format(
                        EXPERIMENT_ID
                    ),
                    "simulation_result",
                )
            ],
        },
        "experiments/{}/c/c1/OBJECTIVE_REPORT.json".format(
            EXPERIMENT_ID
        ): {
            "artifact_type": "objective_report",
            "data": _objective_payload(),
            "inputs": [
                _aid(
                    "experiments/{}/c/c1/SIMULATION_RESULT.json".format(
                        EXPERIMENT_ID
                    ),
                    "simulation_result",
                )
            ],
        },
        "experiments/{}/c/c1/ROBUSTNESS.json".format(
            EXPERIMENT_ID
        ): {
            "artifact_type": "robustness_report",
            "data": _robustness_payload(),
            "inputs": [
                _aid(
                    "experiments/{}/c/c1/SIMULATION_RESULT.json".format(
                        EXPERIMENT_ID
                    ),
                    "simulation_result",
                )
            ],
        },
        "experiments/{}/c/c1/IDENTITY.json".format(
            EXPERIMENT_ID
        ): {
            "artifact_type": "candidate_identity",
            "data": dict(
                _identity_payload(),
                physical_directory=(
                    "experiments/pbs_10layer_opt/c/other"
                    if cross_wired_identity_dir
                    else _identity_payload()["physical_directory"]
                ),
            ),
        },
    }
    for relative, info in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(info["data"], sort_keys=True), encoding="utf-8"
        )

    final_payload = {
        "schema_version": "tmm-harness-result.v1",
        "run_id": RUN_ID,
        "task_id": task_id,
        "status": final_status,
        "state_stage": final_status,
        "experiment_results": [
            {
                "experiment_id": EXPERIMENT_ID,
                "mode": "optimize",
                "physically_valid_candidate_count": 2,
                "candidate_count": 2,
                "baseline_status": "verified",
                "portfolio_artifact_id": (
                    f"experiments/{EXPERIMENT_ID}/DESIGN_PORTFOLIO.json"
                ),
                "portfolio": {
                    "selected_roles": {
                        "best_target_score": "pbs_10layer_opt__c1",
                        "simplest_fabrication": "pbs_10layer_opt__baseline",
                    }
                },
            }
        ],
        "budget": {
            "usage": {
                "forward_evaluations": 10,
                "optimizer_runs": 1,
                "qwen_calls": 0,
                "qwen_input_tokens": 0,
                "qwen_output_tokens": 0,
                "qwen_cost_cny": 0.0,
                "wall_time_seconds": 1.0,
            }
        },
        "stop_decision": {
            "stop": True,
            "reason": "portfolio_complete",
            "return_best_effort": True,
        },
        "wall_seconds": 1.0,
    }
    (root / "FINAL_RESULT.json").write_text(
        json.dumps(final_payload, sort_keys=True), encoding="utf-8"
    )

    store = ArtifactLineageStore(root)
    store.register_file(
        "TASK.json",
        artifact_id="TASK.json",
        artifact_type="task_contract",
        producing_action="validate_task_contract",
        scientific_provenance={"engine": "tmm", "task_sha256": digest},
    )
    store.register_file(
        "ARTIFACT_PATH_INDEX.json",
        artifact_id="ARTIFACT_PATH_INDEX.json",
        artifact_type="artifact_path_index",
        producing_action="map_logical_ids_to_safe_physical_paths",
        input_artifact_ids=["TASK.json"],
    )
    store.register_file(
        "FINAL_RESULT.json",
        artifact_id="FINAL_RESULT.json",
        artifact_type="final_result",
        producing_action="write_harness_result",
        input_artifact_ids=["ARTIFACT_PATH_INDEX.json"],
    )
    store.register_file(
        f"experiments/{EXPERIMENT_ID}/DESIGN_PORTFOLIO.json",
        artifact_id=f"experiments/{EXPERIMENT_ID}/DESIGN_PORTFOLIO.json",
        artifact_type="design_portfolio",
        producing_action="select_portfolio",
        input_artifact_ids=["FINAL_RESULT.json"],
    )
    for relative, info in files.items():
        store.register_file(
            relative,
            artifact_id=_aid(relative, info["artifact_type"]),
            artifact_type=info["artifact_type"],
            producing_action="materialize_verified_artifact",
            input_artifact_ids=info.get("inputs") or [],
        )
    return root


def _compact_layout_run(tmp_path: Path) -> Path:
    """Convert the minimal fixture to the orchestrator's Windows-safe layout."""

    root = _minimal_run(tmp_path)
    legacy_prefix = f"experiments/{EXPERIMENT_ID}"
    compact_prefix = "x/e_compactfixture"

    def compact_path(value: str) -> str:
        return str(value).replace(legacy_prefix, compact_prefix).replace(
            "/baseline/", "/b/"
        )

    compact_dir = root / compact_prefix
    compact_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(root / legacy_prefix), str(compact_dir))
    (compact_dir / "baseline").rename(compact_dir / "b")

    path_index = json.loads(
        (root / "ARTIFACT_PATH_INDEX.json").read_text(encoding="utf-8")
    )
    path_index["experiments"][0]["physical_directory"] = compact_prefix
    (root / "ARTIFACT_PATH_INDEX.json").write_text(
        json.dumps(path_index, sort_keys=True), encoding="utf-8"
    )

    portfolio_path = compact_dir / "DESIGN_PORTFOLIO.json"
    portfolio = json.loads(portfolio_path.read_text(encoding="utf-8"))
    for candidate in portfolio["candidates"]:
        candidate["artifact_ids"] = [
            compact_path(item) for item in candidate["artifact_ids"]
        ]
    portfolio_path.write_text(
        json.dumps(portfolio, sort_keys=True), encoding="utf-8"
    )

    identity_path = compact_dir / "c" / "c1" / "IDENTITY.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    identity["physical_directory"] = f"{compact_prefix}/c/c1"
    identity_path.write_text(
        json.dumps(identity, sort_keys=True), encoding="utf-8"
    )

    final_path = root / "FINAL_RESULT.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["experiment_results"][0]["portfolio_artifact_id"] = (
        f"{compact_prefix}/DESIGN_PORTFOLIO.json"
    )
    final_path.write_text(json.dumps(final, sort_keys=True), encoding="utf-8")

    (root / "ARTIFACT_MANIFEST.json").unlink()
    task_payload = json.loads((root / "TASK.json").read_text(encoding="utf-8"))
    digest = compute_optical_design_task_digest(task_payload)
    store = ArtifactLineageStore(root)
    store.register_file(
        "TASK.json",
        artifact_id="TASK.json",
        artifact_type="task_contract",
        producing_action="validate_task_contract",
        scientific_provenance={"engine": "tmm", "task_sha256": digest},
    )
    store.register_file(
        "ARTIFACT_PATH_INDEX.json",
        artifact_id="ARTIFACT_PATH_INDEX.json",
        artifact_type="artifact_path_index",
        producing_action="map_logical_ids_to_safe_physical_paths",
        input_artifact_ids=["TASK.json"],
    )
    store.register_file(
        "FINAL_RESULT.json",
        artifact_id="FINAL_RESULT.json",
        artifact_type="final_result",
        producing_action="write_harness_result",
        input_artifact_ids=["ARTIFACT_PATH_INDEX.json"],
    )
    portfolio_relative = f"{compact_prefix}/DESIGN_PORTFOLIO.json"
    store.register_file(
        portfolio_relative,
        artifact_id=portfolio_relative,
        artifact_type="design_portfolio",
        producing_action="select_portfolio",
        input_artifact_ids=["FINAL_RESULT.json"],
    )
    artifact_types = {
        "SIMULATION_RESULT.json": "simulation_result",
        "PHYSICS_ACCEPTANCE_CERTIFICATE.json": "physics_acceptance_certificate",
        "OBJECTIVE_REPORT.json": "objective_report",
        "ROBUSTNESS.json": "robustness_report",
        "IDENTITY.json": "candidate_identity",
    }
    files = sorted(
        (
            path
            for path in compact_dir.rglob("*.json")
            if path.name in artifact_types
        ),
        key=lambda path: (
            0 if path.name == "SIMULATION_RESULT.json" else 1,
            path.as_posix(),
        ),
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        inputs: list[str] = []
        if path.name in {
            "PHYSICS_ACCEPTANCE_CERTIFICATE.json",
            "OBJECTIVE_REPORT.json",
            "ROBUSTNESS.json",
        }:
            inputs = [(path.parent / "SIMULATION_RESULT.json").relative_to(root).as_posix()]
        store.register_file(
            relative,
            artifact_id=relative,
            artifact_type=artifact_types[path.name],
            producing_action="materialize_verified_artifact",
            input_artifact_ids=inputs,
        )
    return root


def _compile(
    run_root: str | Path,
    *,
    authority: Optional[ArticleCompilationAuthority] = None,
    request: Optional[CompiledExperimentRequest] = None,
    observation: Optional[ObservationCard] = None,
) -> ArticleAssetCompilationResult:
    authority = authority or _authority()
    request = request or _request(authority=authority)
    execution = _execution_result(
        request,
        run_root,
        observation=observation,
    )
    return compile_article_assets(
        request,
        execution,
        run_root,
        authority=authority,
        observation=observation,
    )


def test_compact_windows_layout_recognizes_b_as_verified_baseline(
    tmp_path: Path,
) -> None:
    result = _compile(_compact_layout_run(tmp_path))

    assert result.status in {"ready", "partial"}
    assert not result.validation_errors
    baseline = next(
        item for item in result.candidates if item.candidate_id.endswith("__baseline")
    )
    assert baseline.is_baseline is True
    assert baseline.identity_artifact_id == ""


def test_real_pbs_accepted_run_compiles_verified_assets() -> None:
    authority = _authority()
    request = _request(authority=authority)
    execution = _execution_result(request, PBS_RUN)
    result = compile_article_assets(
        request, execution, PBS_RUN, authority=authority
    )
    assert result.status in {"ready", "partial"}
    assert result.request_id == request.request_id
    assert result.task_hash == request.task_hash
    assert result.run_id == RUN_ID
    assert result.experiment_id == EXPERIMENT_ID
    assert result.manifest_head_hash
    assert result.manifest_sha256
    assert not result.validation_errors
    assert len(result.descriptors) >= 40
    assert len(result.trusted_values) >= 10
    assert len(result.candidates) == 3
    candidate_ids = {
        candidate.candidate_id for candidate in result.candidates
    }
    assert candidate_ids == {
        "pbs_10layer_opt__baseline",
        "pbs_10layer_opt__gradient_thickness__01",
        "pbs_10layer_opt__differential_evol__75afeebce2ac",
    }
    by_id = {candidate.candidate_id: candidate for candidate in result.candidates}
    baseline = by_id["pbs_10layer_opt__baseline"]
    gradient = by_id["pbs_10layer_opt__gradient_thickness__01"]
    assert baseline.role_keys == ["simplest_fabrication"]
    assert baseline.is_pareto is True
    assert baseline.is_baseline is True
    assert gradient.role_keys == ["best_target_score", "most_robust"]
    assert gradient.is_pareto is True
    assert gradient.robustness_artifact_id
    assert not baseline.robustness_artifact_id
    assert any("robustness" in warning for warning in result.warnings)

    descriptor_by_id = {
        descriptor.artifact_id: descriptor for descriptor in result.descriptors
    }
    for value in result.trusted_values:
        assert value.prose_safe is True
        assert value.source_hash == descriptor_by_id[value.artifact_id].sha256
    assert any(
        descriptor.artifact_type == "simulation_result"
        and "channels.angle=45|pol=s.R" in descriptor.fields
        for descriptor in result.descriptors
    )
    assert result.observation.observation_id
    assert result.observation.experiment_id == EXPERIMENT_ID
    assert result.observation.status == ExperimentStatus.physically_valid
    assert set(result.observation.metrics["verified_candidate_ids"]) == candidate_ids
    assert result.observation.metrics["pareto_candidate_ids"] == sorted(
        ["pbs_10layer_opt__baseline", "pbs_10layer_opt__gradient_thickness__01"]
    )
    assert "measured_budget" in result.observation.metrics
    assert "FINAL_RESULT.json" in result.observation.artifact_ids

    errors: list[str] = []
    warnings: list[str] = []
    validated = validate_asset_compilation_result(
        result,
        errors,
        warnings,
        run_root=PBS_RUN,
        request=request,
        execution_result=execution,
        authority=authority,
    )
    assert not errors
    assert validated is not None
    assert validated.result_id == compute_asset_compilation_result_id(result)


def test_result_id_is_deterministic_and_tamper_rejected() -> None:
    authority = _authority()
    request = _request(authority=authority)
    execution = _execution_result(request, PBS_RUN)
    result = compile_article_assets(
        request, execution, PBS_RUN, authority=authority
    )
    again = compile_article_assets(
        request, execution, PBS_RUN, authority=authority
    )
    assert again.model_dump(mode="json") == result.model_dump(mode="json")
    assert again.result_id == result.result_id

    tampered = result.model_copy(
        update={
            "trusted_values": [
                value.model_copy(
                    update={"rendered_value": "0.999999"}
                )
                if index == 0
                else value
                for index, value in enumerate(result.trusted_values)
            ]
        }
    )
    forged = tampered.model_copy(
        update={"result_id": compute_asset_compilation_result_id(tampered)}
    )
    errors: list[str] = []
    assert (
        validate_asset_compilation_result(
            tampered, errors, [], run_root=PBS_RUN
        )
        is None
    )
    assert any("result_id" in error for error in errors)
    forged_errors: list[str] = []
    assert (
        validate_asset_compilation_result(
            forged, forged_errors, [], run_root=PBS_RUN
        )
        is None
    )
    assert any("source artifact" in error for error in forged_errors)


def test_wrong_run_id_rejected() -> None:
    authority = _authority()
    request = _request(authority=authority, run_id="wrong-run")
    result = _compile(PBS_RUN, authority=authority, request=request)
    assert result.status == "invalid"
    assert any("run_id" in error for error in result.validation_errors)
    assert result.descriptors == []


def test_task_digest_mismatch_rejected() -> None:
    authority = _authority()
    request = _request(
        authority=authority, task_digest="0" * 64
    )
    result = _compile(PBS_RUN, authority=authority, request=request)
    assert result.status == "invalid"
    assert any("task_digest" in error for error in result.validation_errors)


def test_wrong_experiment_rejected() -> None:
    authority = _authority()
    request = _request(
        authority=authority,
        experiment_id=EXPERIMENT_ID,
    )
    execution = _execution_result(
        request, PBS_RUN, experiment_id="pbs_robustness_check"
    )
    result = compile_article_assets(
        request,
        execution,
        PBS_RUN,
        authority=authority,
    )
    assert result.status == "invalid"
    assert any("experiment_id" in error for error in result.validation_errors)


def test_invalid_result_before_run_context_keeps_source_experiment_identity(
    tmp_path: Path,
) -> None:
    authority = _authority()
    task = OpticalDesignTask.model_validate(_task_payload())
    action = required_action_for_task(task)
    digest = compute_optical_design_task_digest(task)
    article_id = "article_exp_split_identity"
    source_id = "tmm_source_exp_split_identity"
    card = ExperimentCard(
        experiment_id=article_id,
        hypothesis_ids=["hyp-split-identity-1"],
        action_type=action,
        task_hash="",
    )
    draft = CompiledExperimentRequest(
        request_id="pending",
        task_hash="pending",
        plan_id="plan-split-identity-1",
        capability_id="cap-split-identity-1",
        run_id=RUN_ID,
        branch_id="root",
        proposal_id="proposal-split-identity-1",
        authority_id=authority.authority_id,
        compiler_attestation="pending",
        parameters={"experiment_id": source_id, "solver": "smatrix"},
        requested_budget={
            "wall_time_seconds": float(task.budget.wall_time_seconds),
            "forward_evaluations": int(
                task.budget.maximum_forward_evaluations
            ),
            "optimizer_runs": int(task.budget.maximum_optimizer_runs),
        },
        task_digest=digest,
        experiment=card,
        allowed_action=action,
    )
    task_hash = compute_task_hash(draft)
    request_id = compute_request_id(task_hash, "proposal-split-identity-1")
    attested = draft.model_copy(
        update={
            "task_hash": task_hash,
            "request_id": request_id,
            "experiment": card.model_copy(update={"task_hash": task_hash}),
        }
    )
    request = attested.model_copy(
        update={"compiler_attestation": authority.attest(attested)}
    )
    observation = ObservationCard(
        observation_id="observation-split-identity-1",
        experiment_id=article_id,
        status=ExperimentStatus.physically_valid,
        metrics={},
        artifact_ids=[],
        failure_records=[],
        failure_diagnosis={},
        summary="",
    )
    execution = _execution_result(request, tmp_path, observation=observation)
    empty_root = tmp_path / "empty-run"
    empty_root.mkdir()

    result = compile_article_assets(
        request,
        execution,
        empty_root,
        authority=authority,
    )

    assert result.status == "invalid"
    assert result.experiment_id == source_id
    assert result.observation.experiment_id == article_id
    assert any(
        "missing required file TASK.json" in error
        for error in result.validation_errors
    )
    validated_errors: list[str] = []
    validate_asset_compilation_result(
        result,
        validated_errors,
        [],
        run_root=empty_root,
        request=request,
        execution_result=execution,
        authority=authority,
    )
    assert not any(
        "experiment_id does not match the supplied upstream" in error
        for error in validated_errors
    )


def test_tampered_artifact_file_rejected(tmp_path: Path) -> None:
    copy = tmp_path / "tampered"
    shutil.copytree(PBS_RUN, copy)
    target = (
        copy
        / "experiments"
        / EXPERIMENT_ID
        / "baseline"
        / "SIMULATION_RESULT.json"
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["wavelengths_nm"][0] = 999.0
    target.write_text(json.dumps(payload), encoding="utf-8")
    result = _compile(copy)
    assert result.status == "invalid"
    assert any(
        "manifest" in error or "sha256" in error
        for error in result.validation_errors
    )


def test_missing_manifest_never_created(tmp_path: Path) -> None:
    empty = tmp_path / "empty-run"
    empty.mkdir()
    shutil.copy2(PBS_RUN / "TASK.json", empty / "TASK.json")
    shutil.copy2(PBS_RUN / "FINAL_RESULT.json", empty / "FINAL_RESULT.json")
    observation = ObservationCard(
        observation_id="observation-empty",
        experiment_id=EXPERIMENT_ID,
        status=ExperimentStatus.physically_valid,
        metrics={},
        artifact_ids=[],
        failure_records=[],
        failure_diagnosis={},
        summary="",
    )
    result = _compile(empty, observation=observation)
    assert result.status == "invalid"
    assert any("ARTIFACT_MANIFEST.json" in error for error in result.validation_errors)
    assert not (empty / "ARTIFACT_MANIFEST.json").exists()


def test_path_traversal_rejected() -> None:
    from optomind_optics.harness.article_assets import _resolve_artifact_path

    root = Path(".").resolve()
    for unsafe in ("../outside.json", "a/../../outside.json", "C:\\evil.json", "/evil.json"):
        with pytest.raises(AssetIntegrityError):
            _resolve_artifact_path(root, unsafe)


def test_failed_run_cannot_yield_trusted_assets(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path, final_status="failed")
    result = _compile(run_root)
    assert result.status == "invalid"
    assert result.descriptors == []
    assert result.trusted_values == []
    assert any("not completed" in error for error in result.validation_errors)


def test_failed_observation_status_rejected(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path)
    authority = _authority()
    request = _request(authority=authority)
    observation = ObservationCard(
        observation_id="observation-failed",
        experiment_id=EXPERIMENT_ID,
        status=ExperimentStatus.failed,
        metrics={"run_status": "failed"},
        artifact_ids=[],
        failure_records=[{"error": "solver failed"}],
        failure_diagnosis={"run_status": "failed"},
        summary="failed",
    )
    result = _compile(
        run_root,
        authority=authority,
        request=request,
        observation=observation,
    )
    assert result.status == "invalid"
    assert any(
        "FINAL_RESULT-derived" in error
        for error in result.validation_errors
    )


def test_ambiguous_candidate_mapping_rejected(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path, duplicate_candidate=True)
    result = _compile(run_root)
    assert result.status == "invalid"
    assert any("ambiguous" in error for error in result.validation_errors)


def test_non_finite_scalar_rejected(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path, nan_score=True)
    result = _compile(run_root)
    assert result.status == "invalid"
    assert any("non-finite" in error for error in result.validation_errors)


def test_duplicate_selected_roles_deduplicated(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path)
    result = _compile(run_root)
    assert result.status in {"ready", "partial"}
    assert any("robustness" in warning for warning in result.warnings)
    by_id = {candidate.candidate_id: candidate for candidate in result.candidates}
    assert len(result.candidates) == 2
    c1 = by_id["pbs_10layer_opt__c1"]
    assert c1.role_keys == ["best_target_score", "most_robust"]
    baseline = by_id["pbs_10layer_opt__baseline"]
    assert baseline.role_keys == ["simplest_fabrication"]
    value_keys = [
        (value.artifact_id, value.field)
        for value in result.trusted_values
    ]
    assert len(value_keys) == len(set(value_keys))
    assert result.observation.metrics["verified_candidate_ids"] == sorted(
        ["pbs_10layer_opt__baseline", "pbs_10layer_opt__c1"]
    )


def test_mapping_inputs_match_model_inputs() -> None:
    authority = _authority()
    request = _request(authority=authority)
    execution = _execution_result(request, PBS_RUN)
    model_result = compile_article_assets(
        request, execution, PBS_RUN, authority=authority
    )
    mapping_result = compile_article_assets(
        request.model_dump(mode="json"),
        execution.model_dump(mode="json"),
        PBS_RUN,
        authority=authority,
    )
    assert mapping_result.model_dump(mode="json") == model_result.model_dump(
        mode="json"
    )
    errors: list[str] = []
    validated = validate_asset_compilation_result(
        mapping_result.model_dump(mode="json"),
        errors,
        [],
        run_root=PBS_RUN,
        request=request,
        execution_result=execution,
        authority=authority,
    )
    assert not errors
    assert validated is not None


def test_validator_rejects_missing_descriptor_file(tmp_path: Path) -> None:
    copy = tmp_path / "copy"
    shutil.copytree(PBS_RUN, copy)
    authority = _authority()
    request = _request(authority=authority)
    execution = _execution_result(request, copy)
    result = compile_article_assets(
        request, execution, copy, authority=authority
    )
    assert result.status in {"ready", "partial"}
    target = (
        copy
        / "experiments"
        / EXPERIMENT_ID
        / "baseline"
        / "OBJECTIVE_REPORT.json"
    )
    target.unlink()
    errors: list[str] = []
    assert (
        validate_asset_compilation_result(
            result, errors, [], run_root=copy
        )
        is None
    )
    assert any("missing" in error for error in errors)


def test_wrong_authority_rejected() -> None:
    authority = _authority()
    request = _request(authority=authority)
    execution = _execution_result(request, PBS_RUN)
    wrong_authority = _authority(b"other-key")
    result = compile_article_assets(
        request, execution, PBS_RUN, authority=wrong_authority
    )
    assert result.status == "invalid"
    assert any(
        "authority" in error for error in result.validation_errors
    )


def test_rejected_receipt_rejected() -> None:
    authority = _authority()
    request = _request(authority=authority)
    execution = _execution_result(
        request,
        PBS_RUN,
        receipt={"status": "adapter_rejected", "reason": "budget denied"},
    )
    result = compile_article_assets(
        request, execution, PBS_RUN, authority=authority
    )
    assert result.status == "invalid"
    assert any(
        "adapter_completed" in error for error in result.validation_errors
    )


def test_observation_preserves_original_metrics_and_failures(
    tmp_path: Path,
) -> None:
    run_root = _minimal_run(tmp_path)
    authority = _authority()
    request = _request(authority=authority)
    observation = ObservationCard(
        observation_id="observation-keep",
        experiment_id=EXPERIMENT_ID,
        status=ExperimentStatus.physically_valid,
        metrics={
            "run_status": "completed",
            "measured_budget": {"forward_evaluations": 10},
            "candidate_count": 2,
        },
        artifact_ids=["TASK.json"],
        failure_records=[{"error": "one advisory warning"}],
        failure_diagnosis={"note": "kept"},
        summary="original summary",
    )
    execution = _execution_result(
        request, run_root, observation=observation
    )
    result = compile_article_assets(
        request,
        execution,
        run_root,
        authority=authority,
    )
    assert result.status in {"ready", "partial"}
    enriched = result.observation
    assert enriched.observation_id == "observation-keep"
    assert enriched.metrics["measured_budget"] == {
        "forward_evaluations": 10
    }
    assert enriched.metrics["candidate_count"] == 2
    assert enriched.failure_records == [{"error": "one advisory warning"}]
    assert enriched.failure_diagnosis == {"note": "kept"}
    assert enriched.summary == "original summary"
    assert "TASK.json" in enriched.artifact_ids
    assert len(enriched.artifact_ids) > 1


def test_execution_outcome_mismatch_rejected(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path)
    authority = _authority()
    request = _request(authority=authority)
    execution = _execution_result(request, run_root)
    forged = execution.model_copy(update={"outcome": "failed"})
    result = compile_article_assets(
        request, forged, run_root, authority=authority
    )
    assert result.status == "invalid"
    assert any("outcome" in error for error in result.validation_errors)


def test_caller_observation_not_equivalent_rejected(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path)
    authority = _authority()
    request = _request(authority=authority)
    execution = _execution_result(request, run_root)
    promoted = execution.observation.model_copy(
        update={"metrics": {"forged": True, "run_status": "completed"}}
    )
    result = compile_article_assets(
        request,
        execution,
        run_root,
        authority=authority,
        observation=promoted,
    )
    assert result.status == "invalid"
    assert any(
        "canonical-content equivalent" in error
        for error in result.validation_errors
    )


def test_observation_status_must_match_final_result(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path)
    authority = _authority()
    request = _request(authority=authority)
    observation = ObservationCard(
        observation_id="observation-limited",
        experiment_id=EXPERIMENT_ID,
        status=ExperimentStatus.needs_higher_fidelity,
        metrics={},
        artifact_ids=[],
        failure_records=[],
        failure_diagnosis={},
        summary="",
    )
    execution = _execution_result(
        request, run_root, observation=observation
    )
    result = compile_article_assets(
        request, execution, run_root, authority=authority
    )
    assert result.status == "invalid"
    assert any(
        "FINAL_RESULT-derived" in error
        for error in result.validation_errors
    )


def test_needs_higher_fidelity_is_honest_partial(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path, final_status="needs_higher_fidelity")
    result = _compile(run_root)
    assert result.status == "partial"
    assert any(
        "needs_higher_fidelity" in warning for warning in result.warnings
    )
    errors: list[str] = []
    validated = validate_asset_compilation_result(
        result, errors, [], run_root=run_root
    )
    assert not errors
    assert validated is not None


def test_cross_wired_baseline_certificate_rejected(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path, cross_wired_baseline=True)
    result = _compile(run_root)
    assert result.status == "invalid"
    assert any("baseline" in error for error in result.validation_errors)


def test_cross_wired_objective_report_rejected(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path, cross_wired_objective=True)
    result = _compile(run_root)
    assert result.status == "invalid"
    assert any(
        "objective report is cross-wired" in error
        for error in result.validation_errors
    )


def test_cross_wired_identity_directory_rejected(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path, cross_wired_identity_dir=True)
    result = _compile(run_root)
    assert result.status == "invalid"
    assert any(
        "physical_directory" in error
        for error in result.validation_errors
    )


def test_logical_artifact_id_differs_from_relative_path_compiles(
    tmp_path: Path,
) -> None:
    run_root = _minimal_run(tmp_path, logical_ids=True)
    result = _compile(run_root)
    assert result.status in {"ready", "partial"}
    assert any(
        descriptor.artifact_id != descriptor.path
        for descriptor in result.descriptors
    )
    errors: list[str] = []
    validated = validate_asset_compilation_result(
        result, errors, [], run_root=run_root
    )
    assert not errors
    assert validated is not None


def test_validator_rejects_forged_manifest_hash(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path)
    result = _compile(run_root)
    forged = result.model_copy(
        update={
            "manifest_sha256": "0" * 64,
            "manifest_head_hash": "0" * 64,
        }
    )
    forged = forged.model_copy(
        update={"result_id": compute_asset_compilation_result_id(forged)}
    )
    errors: list[str] = []
    assert (
        validate_asset_compilation_result(
            forged, errors, [], run_root=run_root
        )
        is None
    )
    assert any("manifest_sha256" in error for error in errors)
    assert any("manifest_head_hash" in error for error in errors)


def test_validator_rejects_descriptor_id_path_swap(tmp_path: Path) -> None:
    run_root = _minimal_run(tmp_path)
    result = _compile(run_root)
    descriptors = result.descriptors
    certificate = next(
        descriptor
        for descriptor in descriptors
        if descriptor.artifact_type == "physics_acceptance_certificate"
    )
    objective = next(
        descriptor
        for descriptor in descriptors
        if descriptor.artifact_type == "objective_report"
    )
    swapped = []
    for descriptor in descriptors:
        if descriptor.artifact_id == certificate.artifact_id:
            swapped.append(
                descriptor.model_copy(
                    update={"artifact_id": objective.artifact_id}
                )
            )
        elif descriptor.artifact_id == objective.artifact_id:
            swapped.append(
                descriptor.model_copy(
                    update={"artifact_id": certificate.artifact_id}
                )
            )
        else:
            swapped.append(descriptor)
    forged = result.model_copy(update={"descriptors": swapped})
    forged = forged.model_copy(
        update={"result_id": compute_asset_compilation_result_id(forged)}
    )
    errors: list[str] = []
    assert (
        validate_asset_compilation_result(
            forged, errors, [], run_root=run_root
        )
        is None
    )
    assert any(
        "exact matching artifact manifest record" in error
        for error in errors
    )


def test_validator_rejects_forged_candidate_certificate(
    tmp_path: Path,
) -> None:
    run_root = _minimal_run(tmp_path)
    result = _compile(run_root)
    by_id = {candidate.candidate_id: candidate for candidate in result.candidates}
    c1 = by_id["pbs_10layer_opt__c1"]
    baseline_certificate_id = by_id[
        "pbs_10layer_opt__baseline"
    ].certificate_artifact_id
    forged_candidate = c1.model_copy(
        update={"certificate_artifact_id": baseline_certificate_id}
    )
    candidates = [
        forged_candidate
        if candidate.candidate_id == c1.candidate_id
        else candidate
        for candidate in result.candidates
    ]
    forged = result.model_copy(update={"candidates": candidates})
    forged = forged.model_copy(
        update={"result_id": compute_asset_compilation_result_id(forged)}
    )
    errors: list[str] = []
    assert (
        validate_asset_compilation_result(
            forged, errors, [], run_root=run_root
        )
        is None
    )
    assert any(
        "certificate" in error and "baseline" in error
        for error in errors
    )


def test_validator_rejects_post_compile_manifest_tampering(
    tmp_path: Path,
) -> None:
    run_root = _minimal_run(tmp_path)
    result = _compile(run_root)
    manifest = run_root / "ARTIFACT_MANIFEST.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["head_hash"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    errors: list[str] = []
    assert (
        validate_asset_compilation_result(
            result, errors, [], run_root=run_root
        )
        is None
    )
    assert any("manifest" in error for error in errors)
