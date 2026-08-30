from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from optomind_optics.harness.article_contracts import ArticleStage, ExperimentCard
from optomind_optics.harness.article_director import ArticleDirector, CapabilityDecision
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    BUDGET_CAPS,
    CompiledExperimentRequest,
    EXPERIMENTAL_STAGES,
    ExperimentProposal,
    ProposalCompileError,
    TMM_WORK_ACTIONS,
    compile_proposal,
    compute_optical_design_task_digest,
    compute_task_hash,
)
from optomind_optics.harness.contracts import ActionType, ExperimentStatus
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ResearchIntent,
    TMMCompatibility,
)


def _analysis(compatibility: str = "compatible") -> OpticalProblemAnalysis:
    return OpticalProblemAnalysis(
        problem_id="problem-1",
        original_request="Design a broadband AR coating over 450-700 nm.",
        normalized_request_english=(
            "Design a broadband one-dimensional antireflection coating for "
            "fused silica in air over 450-700 nm."
        ),
        primary_intent=ResearchIntent.design,
        compatibility=TMMCompatibility(compatibility),
        compatibility_reason="planar multilayer stack within the TMM domain",
        needs_method_research=True,
        wavelengths_nm=[(450.0, 700.0)],
        target_observables=["mean reflectance"],
    )


def _report() -> MethodResearchReport:
    return MethodResearchReport(
        problem_id="problem-1", status=MethodResearchStatus.completed
    )


def _plan():
    result = ArticleDirector().plan(
        "Design a broadband AR coating over 450-700 nm.",
        _analysis(),
        _report(),
        force_mock=True,
    )
    assert result.status == "planned" and result.plan is not None
    return result.plan


def _proposal(**overrides) -> ExperimentProposal:
    fields = dict(
        proposal_id="proposal-1",
        hypothesis_ids=["hyp-01"],
        stage=ArticleStage.baseline_experiments,
        action_type=ActionType.run_solver,
        parameters={"experiment_id": "exp-1", "solver": "smatrix"},
        atomic_change={"variable": "thickness_layer_3", "delta_nm": 2.0},
        expected_discriminator={"metric": "R_mean", "direction": "lower"},
        rationale="Baseline solver run to verify the candidate stack.",
        uncertainty="Solver tolerance only.",
        requested_budget={"forward_evaluations": 100},
    )
    fields.update(overrides)
    return ExperimentProposal(**fields)


def _authority(key: bytes = b"stage5-test-key") -> ArticleCompilationAuthority:
    return ArticleCompilationAuthority(key)


def test_valid_proposal_compiles_deterministically() -> None:
    plan = _plan()
    first = compile_proposal(
        _proposal(),
        plan=plan,
        run_id="run-1",
        branch_id="root",
        authority=_authority(),
    )
    second = compile_proposal(
        _proposal(),
        plan=plan,
        run_id="run-1",
        branch_id="root",
        authority=_authority(),
    )
    assert isinstance(first, CompiledExperimentRequest)
    assert first.status == "compiled"
    assert first.source == "article_compiler"
    assert first.allowed_action == ActionType.run_solver
    assert first.experiment.action_type == ActionType.run_solver
    assert first.experiment.status == ExperimentStatus.proposed
    assert first.experiment.artifact_ids == []
    assert first.experiment.budget_lease_id is None
    assert first.request_id == second.request_id
    assert first.task_hash == second.task_hash
    assert first.compiler_attestation == second.compiler_attestation
    assert first.authority_id == second.authority_id
    assert first.parameters == {
        "experiment_id": "exp-1",
        "solver": "smatrix",
    }
    assert first.requested_budget == {"forward_evaluations": 100}
    assert first.experiment.experiment_id == second.experiment.experiment_id
    assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), sort_keys=True
    )
    recomputed = compute_task_hash(first)
    assert recomputed == first.task_hash


def test_compile_with_task_binds_canonical_digest() -> None:
    plan = _plan()
    task = build_dev_optical_design_task("DEV02")
    authority = _authority()
    request = compile_proposal(
        _proposal(),
        plan=plan,
        run_id="run-1",
        branch_id="root",
        authority=authority,
        task=task,
    )
    expected = compute_optical_design_task_digest(task)
    assert request.task_digest == expected
    assert compute_task_hash(request) == request.task_hash
    assert authority.verify(request)
    tampered = request.model_copy(update={"task_digest": "1" * 64})
    assert compute_task_hash(tampered) != request.task_hash
    assert not authority.verify(tampered)
    legacy = compile_proposal(
        _proposal(),
        plan=plan,
        run_id="run-1",
        branch_id="root",
        authority=authority,
    )
    assert legacy.task_digest == ""


def test_task_digest_deterministic_and_content_sensitive() -> None:
    task = build_dev_optical_design_task("DEV02")
    assert compute_optical_design_task_digest(task) == (
        compute_optical_design_task_digest(task.model_dump(mode="json"))
    )
    other = build_dev_optical_design_task("DEV03")
    assert compute_optical_design_task_digest(task) != (
        compute_optical_design_task_digest(other)
    )


def test_proposal_rejects_forged_fields() -> None:
    for forged in (
        "certificate",
        "result",
        "metrics",
        "observation",
        "permissions",
        "execute",
        "artifact_ids",
    ):
        with pytest.raises(ValidationError, match="Extra inputs"):
            ExperimentProposal.model_validate(
                {**_proposal().model_dump(mode="json"), forged: {"x": 1}}
            )


def test_proposal_rejects_unknown_action_and_invalid_stage() -> None:
    with pytest.raises(ValidationError):
        _proposal(action_type=ActionType.switch_optimizer)
    with pytest.raises(ValidationError):
        _proposal(action_type="run_code")
    with pytest.raises(ValidationError):
        _proposal(stage="not_a_stage")


def test_proposal_rejects_empty_or_duplicate_hypothesis_refs() -> None:
    with pytest.raises(ValidationError):
        _proposal(hypothesis_ids=[])
    with pytest.raises(ValidationError):
        _proposal(hypothesis_ids=["hyp-01", "hyp-01"])


def test_proposal_rejects_non_bounded_parameters() -> None:
    with pytest.raises(ValidationError, match="non-bounded parameter keys"):
        _proposal(parameters={"power_level": 9})
    with pytest.raises(ValidationError, match="exceeds 500 characters"):
        _proposal(parameters={"notes": "x" * 501})
    with pytest.raises(ValidationError, match="scalar or list"):
        _proposal(parameters={"notes": {"nested": True}})
    with pytest.raises(ValidationError, match="exceeds 64 items"):
        _proposal(parameters={"requested_outputs": ["R"] * 65})


def test_proposal_rejects_unknown_or_negative_budget_resources() -> None:
    with pytest.raises(ValidationError, match="unknown budget resources"):
        _proposal(requested_budget={"unlimited_power": 1})
    with pytest.raises(ValidationError, match="non-negative"):
        _proposal(requested_budget={"forward_evaluations": -1})
    with pytest.raises(ValidationError, match="numeric"):
        _proposal(requested_budget={"forward_evaluations": "many"})


def test_compile_rejects_budget_overflow() -> None:
    plan = _plan()
    overflow = _proposal(requested_budget={"forward_evaluations": 10_000_000})
    with pytest.raises(ProposalCompileError, match="exceeds documented cap"):
        compile_proposal(
            overflow, plan=plan, run_id="run-1", branch_id="root", authority=_authority()
        )


def test_compile_rejects_incompatible_and_ambiguous_capability() -> None:
    plan = _plan()
    incompatible_capability = CapabilityDecision(
        capability_id="cap-incompatible",
        status=TMMCompatibility.incompatible,
        supported_scope="",
        unsupported_requirements=["grating"],
        recommended_next_action="stop_capability_boundary",
    )
    incompatible_plan = plan.model_copy(
        update={"capability": incompatible_capability}
    )
    with pytest.raises(ProposalCompileError, match="capability is not compatible"):
        compile_proposal(
            _proposal(),
            plan=incompatible_plan,
            run_id="run-1",
            branch_id="root",
            authority=_authority(),
        )

    ambiguous_capability = incompatible_capability.model_copy(
        update={
            "status": TMMCompatibility.ambiguous,
            "clarification_questions": ["layer count"],
            "recommended_next_action": "clarify_before_experiments",
        }
    )
    ambiguous_plan = plan.model_copy(update={"capability": ambiguous_capability})
    with pytest.raises(ProposalCompileError, match="capability is not compatible"):
        compile_proposal(
            _proposal(),
            plan=ambiguous_plan,
            run_id="run-1",
            branch_id="root",
            authority=_authority(),
        )


def test_compile_rejects_unknown_hypothesis_id() -> None:
    plan = _plan()
    with pytest.raises(ProposalCompileError, match="unknown hypothesis IDs"):
        compile_proposal(
            _proposal(hypothesis_ids=["hyp-99"]),
            plan=plan,
            run_id="run-1",
            branch_id="root",
            authority=_authority(),
        )


def test_compile_rejects_non_whitelisted_action() -> None:
    plan = _plan()
    stop_mapping = {
        **_proposal().model_dump(mode="json"),
        "action_type": "stop",
        "parameters": {},
    }
    with pytest.raises(ProposalCompileError, match="not proposable"):
        compile_proposal(
            stop_mapping,
            plan=plan,
            run_id="run-1",
            branch_id="root",
            authority=_authority(),
        )


def test_compile_accepts_mappings_and_reuses_experiment_card() -> None:
    plan = _plan()
    request = compile_proposal(
        _proposal().model_dump(mode="json"),
        plan=plan.model_dump(mode="json"),
        run_id="run-1",
        branch_id="root",
        authority=_authority(),
    )
    assert isinstance(request.experiment, ExperimentCard)
    assert request.experiment.hypothesis_ids == ["hyp-01"]
    assert request.experiment.stage == ArticleStage.baseline_experiments
    assert request.experiment.atomic_change == {
        "variable": "thickness_layer_3",
        "delta_nm": 2.0,
    }
    assert request.experiment.expected_discriminator == {
        "metric": "R_mean",
        "direction": "lower",
    }


def test_tmm_work_actions_are_the_expected_bounded_set() -> None:
    assert TMM_WORK_ACTIONS == {
        ActionType.generate_baseline,
        ActionType.run_solver,
        ActionType.run_optimizer,
        ActionType.run_convergence_audit,
        ActionType.run_reference_solver,
        ActionType.run_robustness_audit,
    }
    assert ActionType.stop not in TMM_WORK_ACTIONS
    assert "forward_evaluations" in BUDGET_CAPS


@pytest.mark.parametrize(
    "stage",
    [
        ArticleStage.baseline_experiments,
        ArticleStage.exploration,
        ArticleStage.controlled_improvement,
        ArticleStage.discriminative_experiments,
        ArticleStage.robustness_ablation,
    ],
)
def test_experimental_stages_are_accepted(stage: ArticleStage) -> None:
    proposal = _proposal(stage=stage)
    assert proposal.stage == stage


@pytest.mark.parametrize(
    "stage",
    [
        ArticleStage.publication_package,
        ArticleStage.section_writing,
        ArticleStage.fresh_replay,
        ArticleStage.claim_ledger,
        ArticleStage.figure_first_planning,
    ],
)
def test_non_experimental_stages_are_rejected(stage: ArticleStage) -> None:
    with pytest.raises(ValidationError, match="not an experimental stage"):
        _proposal(stage=stage)


def test_compile_rejects_non_experimental_stage_via_mapping() -> None:
    plan = _plan()
    mapping = {
        **_proposal().model_dump(mode="json"),
        "stage": "publication_package",
    }
    with pytest.raises(ProposalCompileError, match="not an experimental stage"):
        compile_proposal(
            mapping,
            plan=plan,
            run_id="run-1",
            branch_id="root",
            authority=_authority(),
        )


def test_compile_requires_authority_and_non_empty_identity() -> None:
    plan = _plan()
    with pytest.raises(TypeError):
        compile_proposal(
            _proposal(), plan=plan, run_id="run-1", branch_id="root"
        )
    with pytest.raises(ProposalCompileError, match="ArticleCompilationAuthority"):
        compile_proposal(
            _proposal(),
            plan=plan,
            run_id="run-1",
            branch_id="root",
            authority="not-an-authority",
        )
    with pytest.raises(ProposalCompileError, match="branch_id"):
        compile_proposal(
            _proposal(),
            plan=plan,
            run_id="run-1",
            branch_id="   ",
            authority=_authority(),
        )
    with pytest.raises(ProposalCompileError, match="run_id"):
        compile_proposal(
            _proposal(),
            plan=plan,
            run_id="",
            branch_id="root",
            authority=_authority(),
        )
    with pytest.raises(ValidationError, match="non-empty"):
        _proposal(proposal_id="   ")


def test_compile_preserves_parameters_budget_and_lease() -> None:
    plan = _plan()
    request = compile_proposal(
        _proposal(),
        plan=plan,
        run_id="run-1",
        branch_id="root",
        authority=_authority(),
        budget_lease_id="lease-local-7",
        available_budget={"forward_evaluations": 500},
    )
    assert request.parameters == {"experiment_id": "exp-1", "solver": "smatrix"}
    assert request.requested_budget == {"forward_evaluations": 100}
    assert request.budget_lease_id == "lease-local-7"
    assert request.experiment.budget_lease_id == "lease-local-7"
    tampered_budget = request.model_copy(
        update={"requested_budget": {"forward_evaluations": 200}}
    )
    assert compute_task_hash(tampered_budget) != request.task_hash
    tampered_parameters = request.model_copy(
        update={"parameters": {"solver": "byrnes"}}
    )
    assert compute_task_hash(tampered_parameters) != request.task_hash


def test_compile_rejects_available_budget_overflow() -> None:
    plan = _plan()
    with pytest.raises(ProposalCompileError, match="exceeds available budget"):
        compile_proposal(
            _proposal(),
            plan=plan,
            run_id="run-1",
            branch_id="root",
            authority=_authority(),
            available_budget={"forward_evaluations": 50},
        )


def test_compile_attestation_is_deterministic_per_key_and_key_binding() -> None:
    plan = _plan()
    authority_a = _authority(b"key-a")
    authority_b = _authority(b"key-b")
    first = compile_proposal(
        _proposal(),
        plan=plan,
        run_id="run-1",
        branch_id="root",
        authority=authority_a,
    )
    second = compile_proposal(
        _proposal(),
        plan=plan,
        run_id="run-1",
        branch_id="root",
        authority=authority_a,
    )
    assert first.compiler_attestation == second.compiler_attestation
    assert first.request_id == second.request_id
    assert first.task_hash == second.task_hash

    other = compile_proposal(
        _proposal(),
        plan=plan,
        run_id="run-1",
        branch_id="root",
        authority=authority_b,
    )
    assert other.authority_id != first.authority_id
    assert other.compiler_attestation != first.compiler_attestation
    assert authority_a.verify(other) is False
    assert authority_a.verify(first) is True
