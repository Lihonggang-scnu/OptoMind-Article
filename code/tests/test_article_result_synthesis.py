from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

from optomind_optics.harness.article_assets import (
    ArticleAssetCompilationResult,
    VerifiedCandidateRecord,
)
from optomind_optics.harness.article_architecture import ArtifactDescriptor
from optomind_optics.harness.article_contracts import (
    ExperimentStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_experiment_planning import (
    PlanningProviderResult,
    RouteTaskBinding,
    plan_article_experiments,
)
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    compute_optical_design_task_digest,
)
from optomind_optics.harness.article_result_synthesis import (
    QwenArticleResultClaimSynthesizer,
    ResultSynthesisProviderInput,
    ResultSynthesisProviderResult,
    SynthesisAliasRecord,
    SynthesisFinding,
    TrustedValueView,
    _synthesis_value_lineage,
    synthesize_article_results,
)
from optomind_optics.harness.article_writing import TrustedValueRecord
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
from optomind_optics.harness.strategy_planner import DesignRoute


QUESTION = (
    "Design an isotropic planar selective thermal emitter on an opaque "
    "aluminum substrate. Evaluate 0, 30, and 60 degrees incidence for both "
    "TE and TM polarization over the 8-13 um and 3-5 um windows with soft "
    "absorptance goals of 85 and 20 percent."
)


def _analysis() -> OpticalProblemAnalysis:
    return OpticalProblemAnalysis(
        problem_id="problem-synthesis",
        original_request=QUESTION,
        normalized_request_english=QUESTION,
        primary_intent=ResearchIntent.design,
        secondary_intents=[ResearchIntent.robustness],
        compatibility=TMMCompatibility.compatible,
        compatibility_reason="Planar layered TMM.",
        wavelengths_nm=[(8000.0, 13000.0), (3000.0, 5000.0)],
        angles_deg=[0.0, 30.0, 60.0],
        polarizations=["s", "p"],
        needs_method_research=False,
    )


def _report() -> MethodResearchReport:
    return MethodResearchReport(
        problem_id="problem-synthesis",
        status=MethodResearchStatus.completed,
    )


def _director_plan():
    result = ArticleDirector().plan(QUESTION, _analysis(), _report(), force_mock=True)
    assert result.status == "planned" and result.plan is not None
    return result.plan


def _route(route_id: str, priority: int) -> DesignRoute:
    return DesignRoute(
        route_id=route_id,
        title=f"Route {route_id}",
        route_kind="optimize_existing_stack",
        scientific_hypothesis="A planar stack tests the emitter mechanism.",
        design_principle="Alternating index layers.",
        proposed_topology="four finite layers",
        design_variables=("thickness_1",),
        theory_basis=("Transfer matrix methods.",),
        execution_request_english=(
            f"Optimize the selective emitter stack for route {route_id}."
        ),
        priority=priority,
    )


def _authority() -> ArticleCompilationAuthority:
    return ArticleCompilationAuthority(b"synthesis-test-key")


class FakePlanner:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def __call__(self, request_table: Any) -> PlanningProviderResult:
        return PlanningProviderResult(
            response={"rows": self.rows},
            usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
            provider_model="fake-planner",
        )


def _planning(plan) -> Any:
    tasks = [
        build_dev_optical_design_task("DEV04"),
        build_dev_optical_design_task("DEV02"),
    ]
    bindings = [
        RouteTaskBinding(
            route_id=route.route_id,
            route=route,
            compiler_status="compiled",
            task=tasks[index],
            task_digest=compute_optical_design_task_digest(tasks[index]),
        )
        for index, route in enumerate((_route("route_01", 1), _route("route_02", 2)))
    ]
    rows = [
        {
            "route_alias": "R01",
            "hypothesis_aliases": ["H01"],
            "stage": "baseline_experiments",
            "atomic_change": {"variable": "thickness_1", "delta_nm": 2.0},
            "expected_discriminator": {
                "metric": "A_mean",
                "direction": "higher",
            },
            "rationale": "Test the mechanism.",
            "uncertainty": "Solver tolerance only.",
        },
        {
            "route_alias": "R02",
            "hypothesis_aliases": ["H01"],
            "stage": "exploration",
            "atomic_change": {"variable": "thickness_2", "delta_nm": 3.0},
            "expected_discriminator": {
                "metric": "A_worst",
                "direction": "higher",
            },
            "rationale": "Test a second mechanism.",
            "uncertainty": "Solver tolerance only.",
        },
    ]
    return plan_article_experiments(
        plan,
        bindings,
        run_id="run-synthesis-1",
        branch_id="root",
        authority=_authority(),
        provider=FakePlanner(rows),
    )


def _asset_for(
    row,
    *,
    status: str = "ready",
    warnings: Sequence[str] = (),
    observation_id: Optional[str] = None,
    value_count: int = 2,
) -> ArticleAssetCompilationResult:
    request = row.request
    physical = str(
        request.parameters.get("experiment_id") or request.experiment.experiment_id
    )
    obs_id = observation_id or f"observation-{row.route_id}"
    descriptor = ArtifactDescriptor(
        artifact_id=f"{physical}/SIMULATION_RESULT.json",
        path=f"{physical}/SIMULATION_RESULT.json",
        fields=["wavelengths_nm", "channels"],
        artifact_type="simulation_result",
        content_summary="Verified absorptance spectrum.",
        sha256="ab" * 32,
        source_experiment_ids=[physical],
        source_observation_ids=[obs_id],
    )
    value_specs = [
        ("channels.angle=60|pol=s.A.max", "0.85", "Worst-case absorptance"),
        ("channels.angle=0|pol=s.A.mean", "0.62", "Mean absorptance"),
        ("channels.angle=0|pol=s.A.min", "0.70", "Minimum absorptance"),
    ]
    values = [
        TrustedValueRecord(
            artifact_id=descriptor.artifact_id,
            field=field,
            rendered_value=rendered,
            unit="",
            source_hash=descriptor.sha256,
            derivation=f"{descriptor.artifact_id} {label.casefold()}",
            label=label,
            prose_safe=True,
        )
        for field, rendered, label in value_specs[:value_count]
    ]
    candidate = VerifiedCandidateRecord(
        candidate_id=f"{physical}__baseline",
        experiment_id=physical,
        role_keys=["simplest_fabrication"],
        is_pareto=True,
        is_baseline=True,
        physics_status="physically_valid",
        certificate_id="cd" * 32,
        certificate_artifact_id=f"{physical}/PHYSICS_ACCEPTANCE_CERTIFICATE.json",
        objective_artifact_id=f"{physical}/OBJECTIVE_REPORT.json",
        simulation_artifact_id=descriptor.artifact_id,
        artifact_ids=[descriptor.artifact_id],
        target_score=0.4,
    )
    observation = ObservationCard(
        observation_id=obs_id,
        experiment_id=request.experiment.experiment_id,
        status=ExperimentStatus.physically_valid,
        metrics={
            "run_status": "completed",
            "verified_candidate_ids": [candidate.candidate_id],
            "selected_roles": {"simplest_fabrication": candidate.candidate_id},
        },
        artifact_ids=[descriptor.artifact_id, "EXECUTION_MARKER.json"],
        failure_records=[],
        failure_diagnosis={},
        hypothesis_updates=[],
        summary="TMM run completed with a physically valid candidate.",
    )
    return ArticleAssetCompilationResult(
        status=status,
        result_id="",
        request_id=request.request_id,
        task_hash=request.task_hash,
        task_digest=request.task_digest,
        run_id=request.run_id,
        experiment_id=physical,
        observation_id=obs_id,
        manifest_head_hash="",
        manifest_sha256="",
        validation_errors=[],
        warnings=list(warnings),
        descriptors=[descriptor],
        trusted_values=values,
        candidates=[candidate],
        observation=observation,
    )


class FakeSynthesizer:
    def __init__(self, responses: Optional[List[Any]] = None) -> None:
        self.responses = list(responses or [])
        self.calls: List[Any] = []

    def __call__(self, payload: Any) -> ResultSynthesisProviderResult:
        self.calls.append(payload)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            if response is None:
                raise RuntimeError("provider unavailable")
            return response
        tv = payload.trusted_values[0].alias
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Mean absorptance reached {{{tv}}} in the 8-13 um " "window."
                    ),
                    "source_value_aliases": [tv],
                    "role": "result",
                    "rationale": "Verified trusted scalar from the run.",
                    "scope_limits": "Nominal design only.",
                }
            ],
            usage={
                "model_name": "fake-provider",
                "estimated_input_tokens": 10,
                "estimated_output_tokens": 5,
            },
            provider_model="fake-provider",
        )


class _FakeQwenClient:
    model_name = "qwen3.7-flash"

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: List[Any] = []

    def call(
        self,
        messages: Any,
        *,
        max_tokens: int = 900,
        force_mock: bool | None = None,
    ) -> Dict[str, Any]:
        self.calls.append(messages)
        return {
            "content": self.content,
            "_llm_usage": {
                "model_name": "qwen3.7-flash",
                "input_tokens": 10,
                "output_tokens": 5,
            },
        }


def _stack():
    plan = _director_plan()
    planning = _planning(plan)
    assets = [
        _asset_for(row)
        for row in planning.rows
        if row.status == "ready" and row.request is not None
    ]
    return plan, planning, assets


def _with_candidates(
    asset: ArticleAssetCompilationResult,
    candidate_ids: Sequence[str],
) -> ArticleAssetCompilationResult:
    template = asset.candidates[0]
    candidates = [
        template.model_copy(
            update={
                "candidate_id": candidate_id,
                "is_baseline": index == 0,
                "role_keys": [f"role_{index + 1}"],
                "artifact_ids": (
                    list(template.artifact_ids)
                    if index == len(candidate_ids) - 1
                    else []
                ),
            }
        )
        for index, candidate_id in enumerate(candidate_ids)
    ]
    observation = asset.observation.model_copy(
        update={
            "metrics": {
                **asset.observation.metrics,
                "verified_candidate_ids": sorted(candidate_ids),
                "selected_roles": {
                    f"role_{index + 1}": candidate_id
                    for index, candidate_id in enumerate(candidate_ids)
                },
            }
        }
    )
    return asset.model_copy(
        update={"candidates": candidates, "observation": observation}
    )


def _with_two_owned_candidates(
    asset: ArticleAssetCompilationResult,
) -> ArticleAssetCompilationResult:
    first_descriptor = asset.descriptors[0]
    second_descriptor = first_descriptor.model_copy(
        update={
            "artifact_id": f"{asset.experiment_id}/SECOND_RESULT.json",
            "path": f"{asset.experiment_id}/SECOND_RESULT.json",
            "sha256": "ef" * 32,
        }
    )
    first_value = asset.trusted_values[0]
    second_value = first_value.model_copy(
        update={
            "artifact_id": second_descriptor.artifact_id,
            "field": "candidate_b.target_score",
            "rendered_value": "0.55",
            "source_hash": second_descriptor.sha256,
            "label": "Candidate B score",
        }
    )
    first_candidate = asset.candidates[0].model_copy(
        update={
            "candidate_id": "candidate-a",
            "artifact_ids": [first_descriptor.artifact_id],
            "target_score": 0.85,
            "simplicity_score": 0.4,
            "role_keys": ["best_target_score", "structurally_distinctive"],
        }
    )
    second_candidate = asset.candidates[0].model_copy(
        update={
            "candidate_id": "candidate-b",
            "artifact_ids": [second_descriptor.artifact_id],
            "simulation_artifact_id": second_descriptor.artifact_id,
            "target_score": 0.55,
            "simplicity_score": 0.9,
            "role_keys": ["simplest_fabrication"],
            "is_baseline": False,
        }
    )
    observation = asset.observation.model_copy(
        update={
            "artifact_ids": [
                first_descriptor.artifact_id,
                second_descriptor.artifact_id,
            ],
            "metrics": {
                **asset.observation.metrics,
                "verified_candidate_ids": ["candidate-a", "candidate-b"],
                "selected_roles": {
                    "best_target_score": "candidate-a",
                    "simplest_fabrication": "candidate-b",
                },
            },
        }
    )
    return asset.model_copy(
        update={
            "descriptors": [first_descriptor, second_descriptor],
            "trusted_values": [first_value, second_value],
            "candidates": [first_candidate, second_candidate],
            "observation": observation,
        }
    )


def _with_shared_portfolio_candidates(
    asset: ArticleAssetCompilationResult,
) -> ArticleAssetCompilationResult:
    descriptor = asset.descriptors[0].model_copy(
        update={
            "artifact_id": f"{asset.experiment_id}/DESIGN_PORTFOLIO.json",
            "path": f"{asset.experiment_id}/DESIGN_PORTFOLIO.json",
            "fields": [
                "candidate-alpha.target_score",
                "candidate-beta.target_score",
            ],
            "artifact_type": "design_portfolio",
            "content_summary": "Verified shared candidate portfolio.",
        }
    )
    values = [
        asset.trusted_values[0].model_copy(
            update={
                "artifact_id": descriptor.artifact_id,
                "field": f"{candidate_id}.target_score",
                "rendered_value": rendered_value,
                "source_hash": descriptor.sha256,
                "label": f"{candidate_id} target score",
            }
        )
        for candidate_id, rendered_value in (
            ("candidate-alpha", "0.85"),
            ("candidate-beta", "0.55"),
        )
    ]
    candidates = [
        asset.candidates[0].model_copy(
            update={
                "candidate_id": candidate_id,
                "artifact_ids": [descriptor.artifact_id],
                "target_score": target_score,
                "role_keys": [role_key],
                "is_baseline": index == 0,
            }
        )
        for index, (candidate_id, target_score, role_key) in enumerate(
            (
                ("candidate-alpha", 0.85, "best_target_score"),
                ("candidate-beta", 0.55, "simplest_fabrication"),
            )
        )
    ]
    observation = asset.observation.model_copy(
        update={
            "artifact_ids": [descriptor.artifact_id],
            "metrics": {
                **asset.observation.metrics,
                "verified_candidate_ids": [
                    "candidate-alpha",
                    "candidate-beta",
                ],
                "selected_roles": {
                    "best_target_score": "candidate-alpha",
                    "simplest_fabrication": "candidate-beta",
                },
            },
        }
    )
    return asset.model_copy(
        update={
            "descriptors": [descriptor],
            "trusted_values": values,
            "candidates": candidates,
            "observation": observation,
        }
    )


def test_valid_multi_route_synthesis_creates_writable_source_bound_facts() -> None:
    plan, planning, assets = _stack()
    provider = FakeSynthesizer()

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "ready"
    assert result.derived_plan is not None
    assert len(result.findings) == 2
    assert len(result.derived_plan.hypotheses) == len(plan.hypotheses) + 2
    assert result.ledger is not None
    assert result.ledger.claims
    assert result.ledger.facts
    assert len(result.feedback_results) == 1
    assert result.feedback_results[0].hypothesis_updates
    assert len(result.observations) == 2
    for observation in result.observations:
        assert observation.hypothesis_updates
        assert observation.hypothesis_updates[0]["evidence_kind"] == "partial_support"
        assert observation.metrics["route_id"] in {
            "baseline",
            "exploration",
        }
    assert result.alias_manifest
    for finding in result.findings:
        assert finding.source_value_aliases
        assert finding.synthesized_hypothesis_id
    assert len(provider.calls) == 2


def test_global_candidate_aliases_disambiguate_route_local_positions() -> None:
    plan, planning, assets = _stack()
    assets = [
        _with_candidates(assets[0], ["candidate-alpha", "candidate-delta"]),
        _with_candidates(assets[1], ["candidate-beta", "candidate-gamma"]),
    ]

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        candidate_alias = payload.candidates[1].alias
        value_alias = payload.trusted_values[0].alias
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Candidate {{{candidate_alias}}} reached "
                        f"{{{value_alias}}}."
                    ),
                    "source_value_aliases": [candidate_alias, value_alias],
                    "subject_aliases": [candidate_alias],
                    "comparison_scope": "none",
                    "role": "result",
                    "rationale": "Candidate-bound verified scalar.",
                    "scope_limits": "Nominal design only.",
                }
            ],
            provider_model="fake-provider",
        )

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="global-candidates"
    )

    assert result.status == "ready"
    candidate_records = [
        record
        for record in result.alias_manifest.values()
        if record.kind == "candidate"
    ]
    alias_by_id = {record.candidate_id: record.alias for record in candidate_records}
    assert alias_by_id == {
        "candidate-alpha": "GC01",
        "candidate-beta": "GC02",
        "candidate-delta": "GC03",
        "candidate-gamma": "GC04",
    }
    cited_candidates = [
        next(alias for alias in finding.source_value_aliases if alias.startswith("GC"))
        for finding in result.findings
    ]
    assert cited_candidates == ["GC03", "GC04"]
    assert len(set(cited_candidates)) == 2
    assert result.derived_plan is not None
    derived_statements = [
        item.statement
        for item in result.derived_plan.hypotheses
        if item.route_kind == "result_synthesis"
    ]
    assert any("Candidate GC03" in statement for statement in derived_statements)
    assert any("Candidate GC04" in statement for statement in derived_statements)


def test_same_candidate_id_reuses_global_alias_across_routes() -> None:
    plan, planning, assets = _stack()
    assets = [
        _with_candidates(assets[0], ["candidate-alpha", "candidate-shared"]),
        _with_candidates(assets[1], ["candidate-beta", "candidate-shared"]),
    ]
    provider = FakeSynthesizer()

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="shared-candidate"
    )

    assert result.status == "ready"
    shared_records = [
        record
        for record in result.alias_manifest.values()
        if record.kind == "candidate" and record.candidate_id == "candidate-shared"
    ]
    assert len(shared_records) == 2
    assert {record.route_id for record in shared_records} == {
        "route_01",
        "route_02",
    }
    assert {record.alias for record in shared_records} == {"GC03"}
    assert all(
        "GC03" in {candidate.alias for candidate in call.candidates}
        for call in provider.calls
    )


def test_global_candidate_aliases_are_stable_under_input_reordering() -> None:
    plan, planning, assets = _stack()
    prepared = [
        _with_candidates(assets[0], ["candidate-zeta", "candidate-alpha"]),
        _with_candidates(assets[1], ["candidate-gamma", "candidate-beta"]),
    ]

    first = synthesize_article_results(
        plan,
        planning,
        prepared,
        provider=FakeSynthesizer(),
        run_id="stable-global-candidates",
    )
    reordered = [
        asset.model_copy(update={"candidates": list(reversed(asset.candidates))})
        for asset in reversed(prepared)
    ]
    second = synthesize_article_results(
        plan,
        planning,
        reordered,
        provider=FakeSynthesizer(),
        run_id="stable-global-candidates",
    )

    def aliases_by_candidate(result: Any) -> Dict[str, str]:
        return {
            record.candidate_id: record.alias
            for record in result.alias_manifest.values()
            if record.kind == "candidate"
        }

    assert (
        aliases_by_candidate(first)
        == aliases_by_candidate(second)
        == {
            "candidate-alpha": "GC01",
            "candidate-beta": "GC02",
            "candidate-gamma": "GC03",
            "candidate-zeta": "GC04",
        }
    )


def test_candidate_role_scopes_are_recomputed_without_promoting_local_roles() -> None:
    plan, planning, assets = _stack()
    first = (
        assets[0]
        .candidates[0]
        .model_copy(
            update={
                "candidate_id": "candidate-a",
                "role_keys": ["best_target_score", "structurally_distinctive"],
                "target_score": 0.9,
                "simplicity_score": 0.2,
                "robustness_score": 0.8,
            }
        )
    )
    second = (
        assets[1]
        .candidates[0]
        .model_copy(
            update={
                "candidate_id": "candidate-b",
                "role_keys": ["simplest_fabrication"],
                "target_score": 0.5,
                "simplicity_score": 0.8,
                "robustness_score": None,
            }
        )
    )
    prepared = [
        assets[0].model_copy(update={"candidates": [first]}),
        assets[1].model_copy(update={"candidates": [second]}),
    ]
    provider = FakeSynthesizer()

    result = synthesize_article_results(
        plan, planning, prepared, provider=provider, run_id="role-scopes"
    )

    assert result.status == "ready"
    views = {
        candidate.candidate_id: candidate
        for call in provider.calls
        for candidate in call.candidates
    }
    assert views["candidate-a"].route_role_keys == [
        "best_target_score",
        "structurally_distinctive",
    ]
    assert views["candidate-a"].global_role_keys == ["best_target_score"]
    assert views["candidate-b"].route_role_keys == ["simplest_fabrication"]
    assert views["candidate-b"].global_role_keys == ["simplest_fabrication"]
    assert all(
        "most_robust" not in view.global_role_keys
        and "structurally_distinctive" not in view.global_role_keys
        for view in views.values()
    )


def test_global_role_ties_and_mapping_are_input_order_stable() -> None:
    plan, planning, assets = _stack()
    prepared = []
    for asset, candidate_id in zip(assets, ("candidate-z", "candidate-a")):
        candidate = asset.candidates[0].model_copy(
            update={
                "candidate_id": candidate_id,
                "target_score": 0.9,
                "simplicity_score": 0.7,
                "robustness_score": 0.6,
            }
        )
        prepared.append(asset.model_copy(update={"candidates": [candidate]}))

    def role_map(items: Sequence[ArticleAssetCompilationResult]) -> Dict[str, Any]:
        provider = FakeSynthesizer()
        synthesize_article_results(
            plan, planning, items, provider=provider, run_id="stable-role-ties"
        )
        return {
            candidate.candidate_id: (
                candidate.alias,
                candidate.global_role_keys,
            )
            for call in provider.calls
            for candidate in call.candidates
        }

    expected_roles = [
        "best_target_score",
        "most_robust",
        "simplest_fabrication",
    ]
    assert (
        role_map(prepared)
        == role_map(list(reversed(prepared)))
        == {
            "candidate-a": ("GC01", expected_roles),
            "candidate-z": ("GC02", expected_roles),
        }
    )


def test_candidate_evidence_views_expose_exact_owner_lineage() -> None:
    plan, planning, assets = _stack()
    prepared = _with_two_owned_candidates(assets[0])
    provider = FakeSynthesizer()

    synthesize_article_results(
        plan, planning, [prepared], provider=provider, run_id="owner-views"
    )

    payload = provider.calls[0]
    candidates = {item.alias: item for item in payload.candidates}
    values = {item.candidate_alias: item for item in payload.trusted_values}
    assert set(values) == set(candidates)
    for alias, candidate in candidates.items():
        value = values[alias]
        assert value.field
        assert value.artifact_alias in candidate.artifact_aliases
        assert value.alias in candidate.value_aliases
        assert value.candidate_alias == alias


def test_candidate_value_owner_correct_passes_and_mismatch_is_dropped() -> None:
    plan, planning, assets = _stack()
    prepared = _with_two_owned_candidates(assets[0])

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        candidates = {item.candidate_id: item.alias for item in payload.candidates}
        values = {item.candidate_alias: item.alias for item in payload.trusted_values}
        subject = candidates["candidate-a"]
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": f"{{{subject}}} produced {{{values[subject]}}}.",
                    "source_value_aliases": [subject, values[subject]],
                    "subject_aliases": [subject],
                    "comparison_scope": "none",
                    "role": "result",
                },
                {
                    "statement_with_value_tokens": f"{{{subject}}} produced {{{values[candidates['candidate-b']]}}}.",
                    "source_value_aliases": [
                        subject,
                        values[candidates["candidate-b"]],
                    ],
                    "subject_aliases": [subject],
                    "comparison_scope": "none",
                    "role": "result",
                },
            ]
        )

    result = synthesize_article_results(
        plan, planning, [prepared], provider=provider, run_id="owner-check"
    )

    assert len(result.findings) == 1
    assert result.findings[0].subject_aliases == ["GC01"]
    assert any("non-subject candidates" in item for item in result.warnings)


def test_shared_portfolio_value_key_prefix_binds_candidate_evidence() -> None:
    plan, planning, assets = _stack()
    prepared = _with_shared_portfolio_candidates(assets[0])

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        assert len(payload.artifacts) == 1
        assert payload.artifacts[0].candidate_alias == ""
        candidates = {item.candidate_id: item.alias for item in payload.candidates}
        values = {item.field: item for item in payload.trusted_values}
        alpha = candidates["candidate-alpha"]
        beta = candidates["candidate-beta"]
        alpha_value = values["candidate-alpha.target_score"]
        beta_value = values["candidate-beta.target_score"]
        assert alpha_value.candidate_alias == alpha
        assert beta_value.candidate_alias == beta
        assert alpha_value.alias in next(
            item.value_aliases for item in payload.candidates if item.alias == alpha
        )
        assert beta_value.alias in next(
            item.value_aliases for item in payload.candidates if item.alias == beta
        )
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"{{{alpha}}} produced {{{alpha_value.alias}}}."
                    ),
                    "source_value_aliases": [alpha, alpha_value.alias],
                    "subject_aliases": [alpha],
                    "comparison_scope": "none",
                    "role": "result",
                },
                {
                    "statement_with_value_tokens": (
                        f"{{{alpha}}} produced {{{beta_value.alias}}}."
                    ),
                    "source_value_aliases": [alpha, beta_value.alias],
                    "subject_aliases": [alpha],
                    "comparison_scope": "none",
                    "role": "result",
                },
            ]
        )

    result = synthesize_article_results(
        plan,
        planning,
        [prepared],
        provider=provider,
        run_id="shared-portfolio-owner",
    )

    assert len(result.findings) == 1
    assert result.findings[0].subject_aliases == ["GC01"]
    assert any("non-subject candidates ['GC02']" in item for item in result.warnings)


def test_global_score_role_requires_verified_global_candidate_role() -> None:
    plan, planning, assets = _stack()
    prepared = _with_shared_portfolio_candidates(assets[0])

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        candidates = {item.candidate_id: item for item in payload.candidates}
        values = {item.candidate_alias: item for item in payload.trusted_values}
        winner = candidates["candidate-alpha"]
        route_only = candidates["candidate-beta"]

        def row(candidate: Any) -> Dict[str, Any]:
            value = values[candidate.alias]
            return {
                "statement_with_value_tokens": (
                    f"{{{candidate.alias}}} is globally best by target score "
                    f"{{{value.alias}}}."
                ),
                "source_value_aliases": [candidate.alias, value.alias],
                "subject_aliases": [candidate.alias],
                "comparison_scope": "global",
                "role": "method",
                "scope_limits": "Run-global target-score ranking only.",
            }

        return ResultSynthesisProviderResult(findings=[row(winner), row(route_only)])

    result = synthesize_article_results(
        plan,
        planning,
        [prepared],
        provider=provider,
        run_id="global-role-validation",
    )

    assert len(result.findings) == 1
    assert result.findings[0].subject_aliases == ["GC01"]
    assert result.findings[0].comparison_scope == "global"
    assert any("global score role not granted" in item for item in result.warnings)


def test_each_comparison_subject_requires_own_evidence() -> None:
    plan, planning, assets = _stack()
    prepared = _with_two_owned_candidates(assets[0])

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        subjects = [item.alias for item in payload.candidates]
        values = {item.candidate_alias: item.alias for item in payload.trusted_values}
        statement = (
            f"{{{subjects[0]}}} and {{{subjects[1]}}} produced verified outputs."
        )
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": statement,
                    "source_value_aliases": [
                        *subjects,
                        values[subjects[0]],
                        values[subjects[1]],
                    ],
                    "subject_aliases": subjects,
                    "comparison_scope": "route",
                    "role": "result",
                },
                {
                    "statement_with_value_tokens": statement,
                    "source_value_aliases": [*subjects, values[subjects[0]]],
                    "subject_aliases": subjects,
                    "comparison_scope": "route",
                    "role": "result",
                },
            ]
        )

    result = synthesize_article_results(
        plan, planning, [prepared], provider=provider, run_id="two-subjects"
    )

    assert len(result.findings) == 1
    assert result.findings[0].comparison_scope == "route"
    assert any("lack owner-bound" in item for item in result.warnings)


def test_unselected_candidate_artifact_and_unlisted_subjects_are_rejected() -> None:
    plan, planning, assets = _stack()
    prepared = _with_two_owned_candidates(assets[0])

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        candidates = [item.alias for item in payload.candidates]
        artifacts = {item.candidate_alias: item.alias for item in payload.artifacts}
        values = {item.candidate_alias: item.alias for item in payload.trusted_values}
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": f"{{{candidates[0]}}} produced an output.",
                    "source_value_aliases": [candidates[0], artifacts[candidates[1]]],
                    "subject_aliases": [candidates[0]],
                    "comparison_scope": "none",
                    "role": "result",
                },
                {
                    "statement_with_value_tokens": "Both candidates produced verified outputs.",
                    "source_value_aliases": [
                        values[candidates[0]],
                        values[candidates[1]],
                    ],
                    "role": "result",
                },
            ]
        )

    result = synthesize_article_results(
        plan, planning, [prepared], provider=provider, run_id="unselected-owner"
    )

    assert result.findings == ()
    assert any("non-subject candidates" in item for item in result.warnings)
    assert any("requires explicit subject_aliases" in item for item in result.warnings)


def test_candidate_independent_method_finding_remains_usable() -> None:
    plan, planning, assets = _stack()

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        artifact = payload.artifacts[0].alias
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"The method recorded its output in {{{artifact}}}."
                    ),
                    "source_value_aliases": [artifact],
                    "role": "method",
                }
            ]
        )

    result = synthesize_article_results(
        plan, planning, [assets[0]], provider=provider, run_id="generic-method"
    )

    assert len(result.findings) == 1
    assert result.findings[0].subject_aliases == []
    assert result.findings[0].comparison_scope == "none"


def test_cross_route_global_candidate_alias_is_rejected() -> None:
    plan, planning, assets = _stack()
    assets = [
        _with_candidates(assets[0], ["candidate-alpha"]),
        _with_candidates(assets[1], ["candidate-beta"]),
    ]

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        value_alias = payload.trusted_values[0].alias
        candidate_alias = (
            "GC02" if payload.route_alias == "R01" else payload.candidates[0].alias
        )
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Candidate {{{candidate_alias}}} reached "
                        f"{{{value_alias}}}."
                    ),
                    "source_value_aliases": [candidate_alias, value_alias],
                    "subject_aliases": [candidate_alias],
                    "comparison_scope": "none",
                    "role": "result",
                    "rationale": "Candidate-bound verified scalar.",
                    "scope_limits": "Nominal design only.",
                }
            ],
            provider_model="fake-provider",
        )

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="cross-route-candidate"
    )

    assert result.status == "partial"
    assert len(result.findings) == 1
    assert result.findings[0].route_id == "route_02"
    assert any("GC02" in warning for warning in result.warnings)


def test_exact_rerun_stable_ids() -> None:
    plan, planning, assets = _stack()
    first = synthesize_article_results(
        plan, planning, assets, provider=FakeSynthesizer(), run_id="run-synthesis-1"
    )
    second = synthesize_article_results(
        plan, planning, assets, provider=FakeSynthesizer(), run_id="run-synthesis-1"
    )

    assert first == second
    assert first.result_id == second.result_id
    assert [item.finding_id for item in first.findings] == [
        item.finding_id for item in second.findings
    ]


def test_malformed_sibling_fail_open() -> None:
    plan, planning, assets = _stack()

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        tv = payload.trusted_values[0].alias
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Mean absorptance reached {{{tv}}} in the 8-13 um " "window."
                    ),
                    "source_value_aliases": [tv],
                    "role": "result",
                    "rationale": "Valid row.",
                    "scope_limits": "None.",
                },
                {
                    "statement_with_value_tokens": "Invented statement.",
                    "source_value_aliases": [],
                    "role": "not_a_role",
                    "rationale": "",
                    "scope_limits": "",
                },
            ],
            usage={"model_name": "fake-provider"},
            provider_model="fake-provider",
        )

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "partial"
    assert len(result.findings) == 2
    assert any("unknown role" in item for item in result.warnings)
    assert result.ledger is not None


def test_statement_tokens_recover_omitted_redundant_alias_fields() -> None:
    plan, planning, assets = _stack()

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        candidate = payload.candidates[0]
        value = next(
            item
            for item in payload.trusted_values
            if item.candidate_alias == candidate.alias
        )
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"{{{candidate.alias}}} produced {{{value.alias}}}."
                    ),
                    "source_value_aliases": [],
                    "subject_aliases": [],
                    "comparison_scope": "none",
                    "role": "result",
                }
            ]
        )

    prepared = _with_shared_portfolio_candidates(assets[0])
    result = synthesize_article_results(
        plan,
        planning,
        [prepared],
        provider=provider,
        run_id="recover-redundant-alias-fields",
    )

    assert len(result.findings) == 1
    assert result.findings[0].subject_aliases == ["GC01"]
    assert set(result.findings[0].source_value_aliases) == {"GC01", "TV01"}
    assert any(
        "recovered explicit statement-token aliases" in item for item in result.warnings
    )


def test_unknown_and_cross_route_alias_rejected() -> None:
    plan, planning, assets = _stack()
    assets[1] = _asset_for(
        next(row for row in planning.rows if row.route_id == "route_02"),
        value_count=3,
    )

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        tv = payload.trusted_values[0].alias
        if payload.route_alias == "R01":
            findings = [
                {
                    "statement_with_value_tokens": (
                        "Mean absorptance reached {TV99} in the 8-13 um window."
                    ),
                    "source_value_aliases": ["TV99"],
                    "role": "result",
                    "rationale": "Bad alias.",
                    "scope_limits": "None.",
                },
                {
                    "statement_with_value_tokens": (
                        "Mean absorptance reached {TV03} in the 8-13 um window."
                    ),
                    "source_value_aliases": ["TV03"],
                    "role": "result",
                    "rationale": "Cross-route alias.",
                    "scope_limits": "None.",
                },
                {
                    "statement_with_value_tokens": (
                        f"Mean absorptance reached {{{tv}}} in the 8-13 um " "window."
                    ),
                    "source_value_aliases": [tv],
                    "role": "result",
                    "rationale": "Valid row.",
                    "scope_limits": "None.",
                },
            ]
        else:
            findings = [
                {
                    "statement_with_value_tokens": (
                        f"Mean absorptance reached {{{tv}}} in the 8-13 um " "window."
                    ),
                    "source_value_aliases": [tv],
                    "role": "result",
                    "rationale": "Valid row.",
                    "scope_limits": "None.",
                }
            ]
        return ResultSynthesisProviderResult(
            findings=findings,
            usage={"model_name": "fake-provider"},
            provider_model="fake-provider",
        )

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "partial"
    assert len(result.findings) == 2
    assert any("TV99" in item for item in result.warnings)
    assert any("TV03" in item for item in result.warnings)


def test_invented_numeric_value_rejected() -> None:
    plan, planning, assets = _stack()

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        tv = payload.trusted_values[0].alias
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        "Mean absorptance reached 89.5 percent and "
                        f"{{{tv}}} in the 8-13 um window."
                    ),
                    "source_value_aliases": [tv],
                    "role": "result",
                    "rationale": "Invented number.",
                    "scope_limits": "None.",
                }
            ],
            usage={"model_name": "fake-provider"},
            provider_model="fake-provider",
        )

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "unavailable"
    assert result.findings == ()
    assert result.ledger is None
    assert any("invented numeric value 89.5" in item for item in result.warnings)


def test_target_attainment_is_rendered_from_structured_inequality() -> None:
    plan, planning, assets = _stack()
    asset = assets[0]
    descriptor = asset.descriptors[0].model_copy(
        update={
            "fields": [
                (
                    "objective_report.target_attainment."
                    "canonical_a_8000_13000_at_least_worst_case_0_p_1_1."
                    "observed"
                ),
                (
                    "objective_report.target_attainment."
                    "canonical_a_8000_13000_at_least_worst_case_0_p_1_1."
                    "target"
                ),
            ]
        }
    )
    observed = asset.trusted_values[0].model_copy(
        update={
            "artifact_id": descriptor.artifact_id,
            "field": descriptor.fields[0],
            "rendered_value": "0.7018069771877161",
            "source_hash": descriptor.sha256,
            "label": "Observed worst-case absorptance at normal incidence",
        }
    )
    target = asset.trusted_values[0].model_copy(
        update={
            "artifact_id": descriptor.artifact_id,
            "field": descriptor.fields[1],
            "rendered_value": "0.6",
            "source_hash": descriptor.sha256,
            "label": "Target worst-case absorptance at normal incidence",
        }
    )
    prepared = asset.model_copy(
        update={
            "descriptors": [descriptor],
            "trusted_values": [observed, target],
        }
    )

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        candidate = payload.candidates[0].alias
        observed_alias = next(
            item.alias
            for item in payload.trusted_values
            if item.field.endswith(".observed")
        )
        target_alias = next(
            item.alias
            for item in payload.trusted_values
            if item.field.endswith(".target")
        )
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Candidate {{{candidate}}} failed because "
                        f"{{{observed_alias}}} fell short of "
                        f"{{{target_alias}}}."
                    ),
                    "source_value_aliases": [
                        candidate,
                        observed_alias,
                        target_alias,
                    ],
                    "subject_aliases": [candidate],
                    "comparison_scope": "none",
                    "role": "limitation",
                    "rationale": "Model-authored direction is intentionally wrong.",
                    "scope_limits": "Normal incidence only.",
                }
            ]
        )

    result = synthesize_article_results(
        plan,
        planning,
        [prepared],
        provider=provider,
        run_id="structured-target-attainment",
    )

    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.role == "result"
    assert finding.target_comparison["met"] is True
    assert finding.target_comparison["constraint"] == "at_least"
    assert "met the at-least target" in finding.statement_with_value_tokens
    assert "did not meet" not in finding.statement_with_value_tokens
    assert result.ledger is not None
    assert "falling short" not in result.ledger.claims[0].statement
    assert any(
        "normalized from verified structured values" in warning
        for warning in result.warnings
    )


def test_join_mismatch_fail_closed() -> None:
    plan, planning, _ = _stack()
    wrong = _asset_for(next(row for row in planning.rows if row.status == "ready"))
    wrong = wrong.model_copy(
        update={"request_id": "request-other", "task_hash": "0" * 64}
    )

    result = synthesize_article_results(
        plan, planning, [wrong], provider=FakeSynthesizer()
    )

    assert result.status == "invalid"
    assert result.findings == ()
    assert any(
        "no matching ready planning row" in item for item in result.validation_errors
    )


def test_original_hypotheses_retained_but_not_writable() -> None:
    plan, planning, assets = _stack()
    original_ids = [item.hypothesis_id for item in plan.hypotheses]
    original_statements = {
        item.hypothesis_id: item.statement for item in plan.hypotheses
    }

    result = synthesize_article_results(
        plan, planning, assets, provider=FakeSynthesizer(), run_id="run-synthesis-1"
    )

    assert result.derived_plan is not None
    for hypothesis_id in original_ids:
        derived = next(
            item
            for item in result.derived_plan.hypotheses
            if item.hypothesis_id == hypothesis_id
        )
        assert derived.statement == original_statements[hypothesis_id]
    synthesized_ids = {item.synthesized_hypothesis_id for item in result.findings}
    assert synthesized_ids
    assert not (synthesized_ids & set(original_ids))
    assert all(
        decision.hypothesis_id in synthesized_ids
        for decision in result.feedback_results[0].hypothesis_updates
    )


def test_provider_unavailable_no_fabricated_facts() -> None:
    plan, planning, assets = _stack()
    result = synthesize_article_results(
        plan, planning, assets, provider=None, run_id="run-synthesis-1"
    )

    assert result.status == "unavailable"
    assert result.findings == ()
    assert result.ledger is None
    assert any("no synthesis provider" in item for item in result.warnings)


def test_006_shape_two_routes_with_partial_asset() -> None:
    plan, planning, assets = _stack()
    assets[1] = _asset_for(
        next(row for row in planning.rows if row.route_id == "route_02"),
        status="partial",
        warnings=[
            "candidate 'x/baseline' has no robustness_report; robustness "
            "metrics are omitted (partial coverage)"
        ],
    )

    result = synthesize_article_results(
        plan, planning, assets, provider=FakeSynthesizer(), run_id="run-synthesis-1"
    )

    assert result.status == "partial"
    assert len(result.findings) == 2
    assert any("robustness_report" in item for item in result.warnings)
    assert result.ledger is not None


def test_no_input_mutation() -> None:
    plan, planning, assets = _stack()
    plan_before = plan.model_dump(mode="json")
    planning_before = planning.model_dump(mode="json")
    assets_before = [item.model_dump(mode="json") for item in assets]

    synthesize_article_results(
        plan, planning, assets, provider=FakeSynthesizer(), run_id="run-synthesis-1"
    )

    assert plan.model_dump(mode="json") == plan_before
    assert planning.model_dump(mode="json") == planning_before
    assert [item.model_dump(mode="json") for item in assets] == assets_before


def test_qwen_synthesizer_parses_findings_and_usage() -> None:
    client = _FakeQwenClient(
        json.dumps(
            {
                "findings": [
                    {
                        "statement_with_value_tokens": (
                            "Mean absorptance reached {TV01} in the 8-13 um " "window."
                        ),
                        "source_value_aliases": ["TV01"],
                        "role": "result",
                        "rationale": "Verified scalar.",
                        "scope_limits": "Nominal design.",
                    }
                ]
            }
        )
    )
    provider = QwenArticleResultClaimSynthesizer(client=client)
    assert provider.max_tokens == 6000
    result = provider(
        ResultSynthesisProviderInput(
            route_alias="R01",
            question=QUESTION,
            trusted_values=[
                TrustedValueView(
                    alias="TV01",
                    label="Mean absorptance",
                    rendered_value="0.85",
                    prose_safe=True,
                )
            ],
        )
    )

    assert len(result.findings) == 1
    assert result.findings[0]["role"] == "result"
    assert result.provider_model == "qwen3.7-flash"
    assert result.usage["input_tokens"] == 10
    assert result.usage["output_tokens"] == 5
    assert "estimated_list_price_cost_cny" in result.usage
    assert len(client.calls) == 1
    assert "findings" in client.calls[0][0]["content"]
    assert "TV01" in client.calls[0][1]["content"]


def test_cloned_evidence_observation_binds_only_cited_artifacts() -> None:
    plan, planning, assets = _stack()
    provider = FakeSynthesizer()

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "ready"
    for observation, finding in zip(result.observations, result.findings):
        assert observation.observation_id == finding.observation_id
        assert observation.experiment_id
        assert "EXECUTION_MARKER.json" not in observation.artifact_ids
        assert len(observation.artifact_ids) == 1
        assert observation.artifact_ids[0].endswith("SIMULATION_RESULT.json")
        assert (
            "EXECUTION_MARKER.json"
            in observation.metrics["synthesis_omitted_artifact_ids"]
        )


def test_two_routes_shared_root_ids_do_not_collide_in_facts() -> None:
    plan, planning, assets = _stack()
    assert all(
        "EXECUTION_MARKER.json" in asset.observation.artifact_ids for asset in assets
    )

    result = synthesize_article_results(
        plan, planning, assets, provider=FakeSynthesizer(), run_id="run-synthesis-1"
    )

    assert result.status == "ready"
    assert result.ledger is not None
    fact_artifact_sets = [set(fact.source_artifact_ids) for fact in result.ledger.facts]
    assert len(fact_artifact_sets) == 2
    assert all(len(items) == 1 for items in fact_artifact_sets)
    assert fact_artifact_sets[0] != fact_artifact_sets[1]
    assert all("EXECUTION_MARKER.json" not in items for items in fact_artifact_sets)


def test_explicit_artifact_alias_is_bound_to_backing_artifact() -> None:
    plan, planning, assets = _stack()

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        tv = payload.trusted_values[0].alias
        av = payload.artifacts[0].alias
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Mean absorptance reached {{{tv}}} in the 8-13 um " "window."
                    ),
                    "source_value_aliases": [tv, av],
                    "role": "result",
                    "rationale": "Cites a trusted value and an artifact.",
                    "scope_limits": "None.",
                }
            ],
            usage={"model_name": "fake-provider"},
            provider_model="fake-provider",
        )

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "ready"
    observation = result.observations[0]
    descriptor_id = assets[0].descriptors[0].artifact_id
    assert set(observation.artifact_ids) == {descriptor_id}


def test_invalid_backing_artifact_fails_closed() -> None:
    plan, planning, _ = _stack()
    row = next(row for row in planning.rows if row.status == "ready")
    asset = _asset_for(row)
    descriptor_ids = {item.artifact_id for item in asset.descriptors}
    invalid_values = [
        (
            item.model_copy(update={"artifact_id": "missing/UNTRUSTED.json"})
            if item.artifact_id in descriptor_ids
            else item
        )
        for item in asset.trusted_values
    ]
    asset = asset.model_copy(update={"trusted_values": invalid_values})

    result = synthesize_article_results(
        plan, planning, [asset], provider=FakeSynthesizer()
    )

    assert result.status == "invalid"
    assert result.findings == ()
    assert any(
        "verified descriptor inventory" in item for item in result.validation_errors
    )


def test_finding_without_artifact_bound_alias_rejected() -> None:
    plan, planning, assets = _stack()

    def provider(payload: Any) -> ResultSynthesisProviderResult:
        cv = payload.candidates[0].alias
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Mean absorptance improved for {{{cv}}}."
                    ),
                    "source_value_aliases": [cv],
                    "subject_aliases": [cv],
                    "role": "result",
                    "rationale": "Candidate-only provenance.",
                    "scope_limits": "None.",
                }
            ],
            usage={"model_name": "fake-provider"},
            provider_model="fake-provider",
        )

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "unavailable"
    assert result.findings == ()
    assert result.ledger is None
    assert any("no artifact-bound source alias" in item for item in result.warnings)


def test_synthesis_value_lineage_survives_into_claim_metadata() -> None:
    """Probe 031: trusted value lineage must bind claims to fields."""

    plan, planning, assets = _stack()
    provider = FakeSynthesizer()

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "ready"
    assert result.ledger is not None
    finding_by_hypothesis = {
        finding.synthesized_hypothesis_id: finding for finding in result.findings
    }
    route_alias_by_route = {
        record.route_id: record.alias
        for record in result.alias_manifest.values()
        if record.kind == "route"
    }
    enriched = [
        claim
        for claim in result.ledger.claims
        if claim.metadata.get("hypothesis_id") in finding_by_hypothesis
        and claim.metadata.get("value_lineage")
    ]
    assert enriched
    for claim in enriched:
        finding = finding_by_hypothesis[claim.metadata["hypothesis_id"]]
        alias = finding.source_value_aliases[0]
        route_alias = route_alias_by_route[finding.route_id]
        record = result.alias_manifest[f"{route_alias}.{alias}"]
        assert record.kind == "trusted_value"
        assert record.prose_safe
        refs = claim.metadata["value_lineage"]
        assert refs
        ref = refs[0]
        assert ref["artifact_id"] == record.artifact_id
        assert ref["field"] == record.field
        assert ref["label"] == record.label
        assert ref["unit"] == record.unit
        assert ref["source_alias"] == alias
        assert "rendered_value" not in ref
        assert "value" not in ref
        contract = claim.metadata["synthesis_contract"]
        assert contract["kind"] == "result_synthesis"
        assert contract["route_id"] == finding.route_id
        assert contract["comparison_scope"] == finding.comparison_scope
        assert contract["subject_aliases"] == finding.subject_aliases
        assert contract["scope_limits"] == finding.scope_limits
        assert contract["metric_bindings"]
        assert contract["metric_bindings"][0]["field"] == record.field
        for subject in contract["subject_candidates"]:
            assert set(subject) == {
                "alias",
                "candidate_id",
                "route_role_keys",
                "global_role_keys",
            }
        fact_id = claim.metadata.get("fact_id")
        if fact_id:
            fact = next(item for item in result.ledger.facts if item.fact_id == fact_id)
            assert fact.metadata["synthesis_contract"] == contract


def test_value_lineage_skips_non_prose_safe_records() -> None:
    """Non-prose-safe trusted values never become claim value lineage."""

    manifest = {
        "R01": SynthesisAliasRecord(alias="R01", kind="route", route_id="route_01"),
        "R01.TV01": SynthesisAliasRecord(
            alias="TV01",
            kind="trusted_value",
            route_id="route_01",
            artifact_id="dev04_absorber/SIMULATION_RESULT.json",
            field="channels.angle=0|pol=s.A.mean",
            label="Mean absorptance",
            prose_safe=False,
        ),
        "R01.TV02": SynthesisAliasRecord(
            alias="TV02",
            kind="trusted_value",
            route_id="route_01",
            artifact_id="dev04_absorber/SIMULATION_RESULT.json",
            field="channels.angle=60|pol=s.A.max",
            label="Worst-case absorptance",
            unit="%",
            prose_safe=True,
        ),
    }
    findings = [
        SynthesisFinding(
            finding_id="finding-d0e859",
            observation_id="observation-route_01",
            route_id="route_01",
            role="result",
            statement_with_value_tokens="Mean absorptance reached {TV02}.",
            source_value_aliases=["TV01", "TV02"],
            rationale="Verified trusted scalar.",
            scope_limits="Nominal design only.",
            synthesized_hypothesis_id="hyp-1",
        )
    ]

    lineage = _synthesis_value_lineage(findings, manifest)

    assert lineage == {
        "hyp-1": [
            {
                "artifact_id": "dev04_absorber/SIMULATION_RESULT.json",
                "field": "channels.angle=60|pol=s.A.max",
                "label": "Worst-case absorptance",
                "unit": "%",
                "source_alias": "TV02",
            }
        ]
    }


def test_value_lineage_is_durable_on_claim_and_fact() -> None:
    """Claim and matching FactRecord carry identical lineage refs."""

    plan, planning, assets = _stack()
    provider = FakeSynthesizer()

    result = synthesize_article_results(
        plan, planning, assets, provider=provider, run_id="run-synthesis-1"
    )

    assert result.status == "ready"
    assert result.ledger is not None
    fact_by_claim = {
        fact.metadata.get("claim_id"): fact for fact in result.ledger.facts
    }
    lineage_claims = [
        claim for claim in result.ledger.claims if claim.metadata.get("value_lineage")
    ]
    assert lineage_claims
    for claim in lineage_claims:
        refs = claim.metadata["value_lineage"]
        fact = fact_by_claim.get(claim.claim_id)
        assert fact is not None
        assert fact.metadata.get("claim_id") == claim.claim_id
        assert fact.metadata.get("hypothesis_id") == claim.metadata.get("hypothesis_id")
        assert fact.metadata.get("value_lineage") == refs
        for ref in refs:
            assert "rendered_value" not in ref
            assert "value" not in ref
