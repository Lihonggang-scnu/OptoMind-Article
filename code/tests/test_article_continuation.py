from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

from optomind_optics.harness.article_assets import (
    ArticleAssetCompilationResult,
    VerifiedCandidateRecord,
    compute_asset_compilation_result_id,
)
from optomind_optics.harness.article_architecture import (
    ArchitectureProviderResult,
    ArtifactDescriptor,
    build_article_architecture,
)
from optomind_optics.harness.article_contracts import (
    ClaimStatus,
    ExperimentStatus,
    ObservationCard,
)
from optomind_optics.harness.article_continuation import (
    _contracted_inventory,
    _scoped_story_values,
    ArticleContinuation,
    ContinuationAttemptRecord,
    ContinuationIntegrityError,
    ContinuationRequest,
    load_source_pipeline,
)
from optomind_optics.harness.article_director import (
    ArticleDirector,
    ArticleDirectorResult,
)
from optomind_optics.harness.article_execution import ArticleExecutionResult
from optomind_optics.harness.article_experiment_planning import (
    PlanningProviderResult,
    RouteTaskBinding,
    plan_article_experiments,
)
from optomind_optics.harness.article_pipeline import (
    ArticlePipelineRequest,
    ArticlePipelineResult,
    compute_pipeline_result_id,
)
from optomind_optics.harness.article_pipeline_recovery import (
    write_asset_route,
    write_execution_route,
)
from optomind_optics.harness.article_proposals import (
    ArticleCompilationAuthority,
    compute_optical_design_task_digest,
)
from optomind_optics.harness.article_result_synthesis import (
    ArticleResultSynthesisResult,
    ResultSynthesisProviderResult,
    synthesize_article_results,
)
from optomind_optics.harness.article_review import ReviewerProviderResult
from optomind_optics.harness.article_writing import (
    TrustedValueRecord,
    WriterProviderResult,
)
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
        problem_id="problem-continuation",
        original_request=QUESTION,
        normalized_request_english=QUESTION,
        primary_intent=ResearchIntent.design,
        compatibility=TMMCompatibility.compatible,
        compatibility_reason="Planar layered TMM.",
        wavelengths_nm=[(8000.0, 13000.0), (3000.0, 5000.0)],
        angles_deg=[0.0, 30.0, 60.0],
        polarizations=["s", "p"],
        needs_method_research=False,
    )


def _report() -> MethodResearchReport:
    return MethodResearchReport(
        problem_id="problem-continuation",
        status=MethodResearchStatus.completed,
    )


def _director_plan():
    result = ArticleDirector().plan(
        QUESTION, _analysis(), _report(), force_mock=True
    )
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
    return ArticleCompilationAuthority(b"continuation-test-key")


class FakePlanner:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def __call__(self, request_table: Any) -> PlanningProviderResult:
        return PlanningProviderResult(
            response={"rows": self.rows},
            usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
            provider_model="fake-planner",
        )


def _planning(plan, *, same_task: bool = False):
    tasks = (
        [build_dev_optical_design_task("DEV04")] * 2
        if same_task
        else [
            build_dev_optical_design_task("DEV04"),
            build_dev_optical_design_task("DEV02"),
        ]
    )
    bindings = [
        RouteTaskBinding(
            route_id=route.route_id,
            route=route,
            compiler_status="compiled",
            task=tasks[index],
            task_digest=compute_optical_design_task_digest(tasks[index]),
        )
        for index, route in enumerate(
            (_route("route_01", 1), _route("route_02", 2))
        )
    ]
    rows = [
        {
            "route_alias": "R01",
            "hypothesis_aliases": ["H01"],
            "stage": "baseline_experiments",
            "atomic_change": {"variable": "thickness_1", "delta_nm": 2.0},
            "expected_discriminator": {"metric": "A_mean", "direction": "higher"},
            "rationale": "Test the mechanism.",
            "uncertainty": "Solver tolerance only.",
        },
        {
            "route_alias": "R02",
            "hypothesis_aliases": ["H01"],
            "stage": "exploration",
            "atomic_change": {"variable": "thickness_2", "delta_nm": 3.0},
            "expected_discriminator": {"metric": "A_worst", "direction": "higher"},
            "rationale": "Test a second mechanism.",
            "uncertainty": "Solver tolerance only.",
        },
    ]
    return plan_article_experiments(
        plan,
        bindings,
        run_id="run-continuation-1",
        branch_id="root",
        authority=_authority(),
        provider=FakePlanner(rows),
    )


def _asset_for(
    row,
    *,
    generic_artifact: bool = False,
) -> ArticleAssetCompilationResult:
    request = row.request
    physical = str(
        request.parameters.get("experiment_id")
        or request.experiment.experiment_id
    )
    obs_id = f"observation-{row.route_id}"
    descriptor = ArtifactDescriptor(
        artifact_id=f"{physical}/SIMULATION_RESULT.json",
        path=f"{physical}/SIMULATION_RESULT.json",
        fields=[
            "wavelengths_nm",
            "channels",
            "channels.angle=60|pol=s.A.max",
            "channels.angle=0|pol=s.A.mean",
        ],
        artifact_type="simulation_result",
        content_summary="Verified absorptance spectrum.",
        sha256="ab" * 32,
        source_experiment_ids=[physical],
        source_observation_ids=[obs_id],
    )
    values = [
        TrustedValueRecord(
            artifact_id=descriptor.artifact_id,
            field="channels.angle=60|pol=s.A.max",
            rendered_value="0.85",
            unit="",
            source_hash=descriptor.sha256,
            derivation=f"{descriptor.artifact_id} maximum absorptance",
            label="Worst-case absorptance",
            prose_safe=True,
        ),
        TrustedValueRecord(
            artifact_id=descriptor.artifact_id,
            field="channels.angle=0|pol=s.A.mean",
            rendered_value="0.62",
            unit="",
            source_hash=descriptor.sha256,
            derivation=f"{descriptor.artifact_id} mean absorptance",
            label="Mean absorptance",
            prose_safe=True,
        ),
    ]
    descriptors = [descriptor]
    if generic_artifact:
        generic = ArtifactDescriptor(
            artifact_id="DESIGN_PORTFOLIOS.json",
            path=f"{physical}/DESIGN_PORTFOLIOS.json",
            fields=["portfolio_count"],
            artifact_type="design_portfolio",
            content_summary=f"Design portfolio for {row.route_id}.",
            sha256="ef" * 32 if row.route_id == "route_01" else "12" * 32,
            source_experiment_ids=[physical],
            source_observation_ids=[obs_id],
        )
        descriptors.append(generic)
        values.append(
            TrustedValueRecord(
                artifact_id=generic.artifact_id,
                field="portfolio_count",
                rendered_value="3",
                unit="",
                source_hash=generic.sha256,
                derivation=f"{row.route_id} portfolio count",
                label="Design portfolio count",
                prose_safe=True,
            )
        )
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
    model = ArticleAssetCompilationResult(
        status="ready",
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
        warnings=[],
        descriptors=descriptors,
        trusted_values=values,
        candidates=[candidate],
        observation=observation,
    )
    return model.model_copy(
        update={"result_id": compute_asset_compilation_result_id(model)}
    )


def _execution_for(row, asset: ArticleAssetCompilationResult) -> ArticleExecutionResult:
    return ArticleExecutionResult(
        request_id=row.request.request_id,
        task_hash=row.request.task_hash,
        run_dir=f"root/run-{row.route_id}",
        observation=asset.observation,
        receipt={
            "status": "adapter_completed",
            "request_id": row.request.request_id,
            "task_hash": row.request.task_hash,
            "run_id": row.request.run_id,
        },
        outcome=asset.observation.status.value,
    )


def _write_source_pipeline(
    tmp_path: Path,
    *,
    same_task: bool = False,
    generic_artifact: bool = False,
    executed_route_ids: Sequence[str] = ("route_01", "route_02"),
) -> Path:
    plan = _director_plan()
    planning = _planning(plan, same_task=same_task)
    ready = [
        row
        for row in planning.rows
        if row.status == "ready" and row.request is not None
        and row.route_id in executed_route_ids
    ]
    assets = [
        _asset_for(row, generic_artifact=generic_artifact) for row in ready
    ]
    source_dir = tmp_path / "source-pipeline"
    source_dir.mkdir(parents=True, exist_ok=True)
    request = ArticlePipelineRequest(
        question=QUESTION,
        run_id="run-continuation-1",
        branch_id="root",
        work_dir=str(source_dir),
        force_mock=True,
        maximum_routes=2,
    )
    for row, asset in zip(ready, assets):
        execution = _execution_for(row, asset)
        write_execution_route(
            source_dir, row.request, execution, route_id=row.route_id
        )
        write_asset_route(source_dir, row.request, execution, asset)
    (source_dir / "REQUEST.json").write_text(
        json.dumps(request.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    result = ArticlePipelineResult(
        status="partial",
        run_id="run-continuation-1",
        question=QUESTION,
        receipts=(),
        director_plan=ArticleDirectorResult(
            status="planned", plan=plan, attempts=1
        ),
        experiment_planning=planning,
        execution_count=len(assets),
        asset_compilations=tuple(assets),
        result_id="",
    )
    result = result.model_copy(
        update={"result_id": compute_pipeline_result_id(result)}
    )
    (source_dir / "FINAL_PIPELINE_RESULT.json").write_text(
        json.dumps(result.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )
    return source_dir


class FakeSynthesisProvider:
    def __init__(self, responses: Optional[List[Any]] = None) -> None:
        self.responses = list(responses or [])
        self.calls = 0

    def __call__(self, payload: Any) -> ResultSynthesisProviderResult:
        self.calls += 1
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
                        f"Mean absorptance reached {{{tv}}} in the 8-13 um "
                        "window."
                    ),
                    "source_value_aliases": [tv],
                    "role": "result",
                    "rationale": "Verified trusted scalar from the run.",
                    "scope_limits": "Nominal design only.",
                }
            ],
            usage={
                "model_name": "fake-provider",
                "input_tokens": 10,
                "output_tokens": 5,
            },
            provider_model="fake-provider",
        )


class FakeArchitectureProvider:
    def __init__(self) -> None:
        self.calls = 0

    @staticmethod
    def _claim_authorized_value_fields(
        payload: Mapping[str, Any],
    ) -> Dict[str, set[str]]:
        """Collect claim-specific authorized value fields from the payload."""
        by_artifact: Dict[str, set[str]] = {}
        for claim in [
            *payload.get("positive_claims", []),
            *payload.get("limitation_claims", []),
        ]:
            for item in claim.get("authorized_value_fields") or []:
                artifact_id = str(item.get("artifact_id") or "")
                fields = item.get("fields") or []
                if artifact_id and fields:
                    by_artifact.setdefault(artifact_id, set()).update(fields)
        return by_artifact

    def __call__(self, requests: Sequence[Any]) -> List[ArchitectureProviderResult]:
        self.calls += 1
        payload = requests[0]
        positives = list(payload["positive_claims"])
        claim_bindings = [
            {"claim_id": str(item["claim_id"]), "role": "positive"}
            for item in positives
        ]
        fact_ids = [
            str(item["fact_id"])
            for item in positives
            if item.get("fact_id")
        ]
        claim_value_fields = self._claim_authorized_value_fields(payload)
        artifact_bindings = [
            {
                "artifact_id": artifact_id,
                "selected_fields": sorted(fields),
            }
            for artifact_id, fields in sorted(claim_value_fields.items())
        ]
        figure = {
            "role_key": "spectrum",
            "kind": "quantitative",
            "story_role": "spectral response",
            "panel_intents": ["panel"],
            "caption_intent": "verified spectrum",
            "claim_bindings": claim_bindings,
            "fact_ids": fact_ids,
            "artifact_bindings": artifact_bindings,
            "limitations": ["solver only"],
        }
        section = {
            "heading": "Results",
            "purpose": "present the verified result evidence",
            "key_messages": ["key"],
            "transitions": ["next"],
            "claim_bindings": claim_bindings,
            "figure_roles": ["spectrum"],
        }

        def story(score: float, shape: str, heading: str) -> Dict[str, Any]:
            return {
                "story_shape": shape,
                "central_thesis": "An evidence-bound selective emitter story.",
                "sections": [{**section, "heading": heading}],
                "figures": [figure],
                "omitted_claims": [],
                "exclusions": ["excluded"],
                "strengths": ["strength"],
                "risks": ["risk"],
                "recommendation_rationale": "rationale",
                "recommendation_score": score,
            }

        return [
            ArchitectureProviderResult(
                stories=[
                    story(0.6, "shape-a", "Results"),
                    story(0.4, "shape-b", "Discussion"),
                ],
                provider_model="fake-architecture",
                usage={
                    "model_name": "fake-provider",
                    "input_tokens": 5,
                    "output_tokens": 5,
                },
            )
        ]


class InvalidArchitectureProvider(FakeArchitectureProvider):
    """Returns a story that fails local numeric-integrity assembly."""

    def __call__(self, requests: Sequence[Any]) -> List[ArchitectureProviderResult]:
        results = super().__call__(requests)
        source = results[0]
        story = dict(source.stories[0])
        story["central_thesis"] = "Reflectance improves to 999 percent."
        return [
            ArchitectureProviderResult(
                stories=[story],
                provider_model=source.provider_model,
                usage=source.usage,
            )
        ]


class EmptyArchitectureProvider(FakeArchitectureProvider):
    """Returns a provider envelope with no usable story candidates."""

    def __call__(self, requests: Sequence[Any]) -> List[ArchitectureProviderResult]:
        results = super().__call__(requests)
        source = results[0]
        return [
            ArchitectureProviderResult(
                stories=[],
                provider_model=source.provider_model,
                usage=source.usage,
            )
        ]


class UnavailableSynthesisProvider:
    """Provider whose findings are all rejected, leaving an unavailable result."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, payload: Any) -> ResultSynthesisProviderResult:
        self.calls += 1
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        "Mean absorptance reached {ghost-alias}."
                    ),
                    "source_value_aliases": ["ghost-alias"],
                    "role": "result",
                    "rationale": "unverified",
                    "scope_limits": "none",
                }
            ],
            usage={
                "model_name": "fake-provider",
                "input_tokens": 4,
                "output_tokens": 6,
            },
            provider_model="fake-provider",
        )


class MixedArtifactSynthesisProvider:
    """References the generic portfolio artifact for one route only."""

    def __init__(self, generic_route_alias: str = "R01") -> None:
        self.generic_route_alias = generic_route_alias
        self.calls = 0

    def __call__(self, payload: Any) -> ResultSynthesisProviderResult:
        self.calls += 1
        if payload.route_alias == self.generic_route_alias:
            tv = next(
                tv
                for tv in payload.trusted_values
                if "portfolio" in (tv.label or "").lower()
            )
        else:
            tv = next(
                tv
                for tv in payload.trusted_values
                if "portfolio" not in (tv.label or "").lower()
            )
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Design portfolio count reached {{{tv.alias}}} in "
                        f"{payload.route_alias}."
                    ),
                    "source_value_aliases": [tv.alias],
                    "role": "result",
                    "rationale": "Verified trusted scalar from the run.",
                    "scope_limits": "Nominal design only.",
                }
            ],
            usage={
                "model_name": "fake-provider",
                "input_tokens": 10,
                "output_tokens": 5,
            },
            provider_model="fake-provider",
        )


class AllGenericSynthesisProvider:
    """References the generic portfolio artifact for every route."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, payload: Any) -> ResultSynthesisProviderResult:
        self.calls += 1
        tv = next(
            tv
            for tv in payload.trusted_values
            if "portfolio" in (tv.label or "").lower()
        )
        return ResultSynthesisProviderResult(
            findings=[
                {
                    "statement_with_value_tokens": (
                        f"Design portfolio count reached {{{tv.alias}}} in "
                        f"{payload.route_alias}."
                    ),
                    "source_value_aliases": [tv.alias],
                    "role": "result",
                    "rationale": "Verified trusted scalar from the run.",
                    "scope_limits": "Nominal design only.",
                }
            ],
            usage={
                "model_name": "fake-provider",
                "input_tokens": 10,
                "output_tokens": 5,
            },
            provider_model="fake-provider",
        )


class PartialBindingArchitectureProvider(FakeArchitectureProvider):
    """Binds only claim-authorized value fields of each trusted artifact."""

    def __call__(self, requests: Sequence[Any]) -> List[ArchitectureProviderResult]:
        results = super().__call__(requests)
        source = results[0]
        payload = requests[0]
        claim_value_fields = self._claim_authorized_value_fields(payload)
        stories = []
        for story in source.stories:
            story = dict(story)
            figures = []
            for figure in story["figures"]:
                figure = dict(figure)
                bindings = []
                for binding in figure["artifact_bindings"]:
                    binding = dict(binding)
                    authorized = claim_value_fields.get(
                        binding["artifact_id"], set()
                    )
                    if authorized:
                        binding["selected_fields"] = sorted(
                            set(binding["selected_fields"]) & authorized
                        )
                    else:
                        binding["selected_fields"] = list(
                            binding["selected_fields"][:1]
                        )
                    bindings.append(binding)
                figure["artifact_bindings"] = bindings
                figures.append(figure)
            story["figures"] = figures
            stories.append(story)
        return [
            ArchitectureProviderResult(
                stories=stories,
                provider_model=source.provider_model,
                usage=source.usage,
            )
        ]


class MissingValueArchitectureProvider(FakeArchitectureProvider):
    """Binds a figure-only declared field plus the claim-authorized fields."""

    def __call__(self, requests: Sequence[Any]) -> List[ArchitectureProviderResult]:
        results = super().__call__(requests)
        source = results[0]
        payload = requests[0]
        claim_value_fields = self._claim_authorized_value_fields(payload)
        stories = []
        for story in source.stories:
            story = dict(story)
            figures = []
            for figure in story["figures"]:
                figure = dict(figure)
                bindings = []
                for binding in figure["artifact_bindings"]:
                    binding = dict(binding)
                    authorized = claim_value_fields.get(
                        binding["artifact_id"], set()
                    )
                    binding["selected_fields"] = [
                        "wavelengths_nm",
                        *sorted(
                            set(binding["selected_fields"]) | authorized
                        ),
                    ]
                    bindings.append(binding)
                figure["artifact_bindings"] = bindings
                figures.append(figure)
            story["figures"] = figures
            stories.append(story)
        return [
            ArchitectureProviderResult(
                stories=stories,
                provider_model=source.provider_model,
                usage=source.usage,
            )
        ]


class TwoSectionArchitectureProvider(FakeArchitectureProvider):
    """Stories with a second no-figure limitations section."""

    def __call__(self, requests: Sequence[Any]) -> List[ArchitectureProviderResult]:
        results = super().__call__(requests)
        source = results[0]
        stories = []
        for story in source.stories:
            story = dict(story)
            first = dict(story["sections"][0])
            second = {
                **first,
                "heading": "Limitations",
                "purpose": "present the limitations of the study",
                "figure_roles": [],
            }
            story["sections"] = [first, second]
            stories.append(story)
        return [
            ArchitectureProviderResult(
                stories=stories,
                provider_model=source.provider_model,
                usage=source.usage,
            )
        ]


class FakeWriter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: Mapping[str, Any]) -> WriterProviderResult:
        self.calls += 1
        claim_aliases = [
            item["claim_alias"]
            for item in request["section"]["claim_bindings"]
        ]
        figure_aliases = list(request["section"]["figure_aliases"])
        value_aliases = [item["alias"] for item in request["values"]]
        text = "The verified evidence supports the design claim within scope."
        if value_aliases:
            text = f"{text} [VALUE:{value_aliases[0]}]"
        return WriterProviderResult(
            response={
                "paragraphs": [
                    {
                        "text_with_value_tokens": text,
                        "claim_aliases": claim_aliases,
                        "figure_aliases": figure_aliases,
                        "paragraph_role": "result",
                        "inference_kind": "bounded_inference",
                        "inference_note": "local inference from the claim",
                    }
                ],
                "deferred_claim_aliases": [],
                "author_notes": [],
            },
            usage={
                "model_name": "fake-provider",
                "input_tokens": 10,
                "output_tokens": 20,
            },
            provider_model="fake-writer",
        )


class BlockingWriter(FakeWriter):
    """Returns a malformed (blocked) response for one named section."""

    def __init__(self, blocked_heading: str = "Limitations") -> None:
        super().__init__()
        self.blocked_heading = blocked_heading

    def __call__(self, request: Mapping[str, Any]) -> WriterProviderResult:
        if request["section"]["heading"] != self.blocked_heading:
            return super().__call__(request)
        self.calls += 1
        return WriterProviderResult(
            response={
                "paragraphs": [],
                "deferred_claim_aliases": [],
                "author_notes": [],
            },
            usage={
                "model_name": "fake-provider",
                "input_tokens": 1,
                "output_tokens": 1,
            },
            provider_model="fake-writer",
        )


class BlockAllWriter(FakeWriter):
    """Returns a malformed (blocked) response for every section."""

    def __call__(self, request: Mapping[str, Any]) -> WriterProviderResult:
        self.calls += 1
        return WriterProviderResult(
            response={
                "paragraphs": [],
                "deferred_claim_aliases": [],
                "author_notes": [],
            },
            usage={
                "model_name": "fake-provider",
                "input_tokens": 1,
                "output_tokens": 1,
            },
            provider_model="fake-writer",
        )


class FakeReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: Mapping[str, Any]) -> ReviewerProviderResult:
        self.calls += 1
        return ReviewerProviderResult(
            response={"findings": [], "advice": []},
            usage={
                "model_name": "fake-provider",
                "input_tokens": 7,
                "output_tokens": 9,
            },
            provider_model="fake-reviewer",
        )


class FakeFormatRepair:
    def __call__(self, request: Mapping[str, Any]) -> WriterProviderResult:
        return WriterProviderResult(
            response={
                "paragraphs": [
                    {
                        "text_with_value_tokens": "Repaired paragraph.",
                        "claim_aliases": [],
                        "figure_aliases": [],
                        "paragraph_role": "result",
                        "inference_kind": "bounded_inference",
                    }
                ],
                "deferred_claim_aliases": [],
                "author_notes": [],
            },
            usage={"model_name": "fake-provider"},
            provider_model="fake-repair",
        )


def _continuation(
    *,
    synthesis: Optional[FakeSynthesisProvider] = None,
    architecture: Optional[FakeArchitectureProvider] = None,
    writer: Optional[FakeWriter] = None,
    reviewers: Optional[Tuple[FakeReviewer, FakeReviewer, FakeReviewer]] = None,
    fault_hook: Any = None,
) -> Tuple[ArticleContinuation, Dict[str, Any]]:
    reviewers = reviewers or (
        FakeReviewer(),
        FakeReviewer(),
        FakeReviewer(),
    )
    effective_synthesis = synthesis or FakeSynthesisProvider()
    effective_architecture = architecture or FakeArchitectureProvider()
    effective_writer = writer or FakeWriter()
    continuation = ArticleContinuation(
        result_synthesis_provider=effective_synthesis,
        architecture_provider=effective_architecture,
        section_writer=effective_writer,
        format_repair=FakeFormatRepair(),
        scientific_reviewer=reviewers[0],
        expression_reviewer=reviewers[1],
        author_reviser=reviewers[2],
        fault_hook=fault_hook,
    )
    return continuation, {
        "reviewers": reviewers,
        "synthesis": effective_synthesis,
        "architecture": effective_architecture,
        "writer": effective_writer,
    }


def _request(source_dir: Path, work_dir: Path, **overrides: Any) -> ContinuationRequest:
    return ContinuationRequest(
        run_id="run-continuation-1",
        branch_id="root",
        source_pipeline_dir=str(source_dir),
        work_dir=str(work_dir),
        **overrides,
    )


def _provider_calls(continuation: ArticleContinuation, state: Dict[str, Any]) -> int:
    count = 0
    for item in state["reviewers"]:
        count += item.calls
    for key in ("synthesis", "architecture", "writer"):
        provider = state.get(key)
        if provider is not None:
            count += provider.calls
    return count


def test_loader_validates_complete_source_pipeline(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    bundle = load_source_pipeline(source_dir)
    assert bundle.result.result_id
    assert len(bundle.executions) == 2
    assert len(bundle.assets) == 2
    assert bundle.plan is not None


def test_happy_path_through_manuscript_with_fake_providers(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, state = _continuation()

    result = continuation.run(_request(source_dir, work_dir))

    assert result.status in {"completed", "partial"}
    assert len(result.receipts) == 5
    assert [item.stage for item in result.receipts] == [
        "result_synthesis",
        "architecture",
        "writing",
        "review",
        "manuscript",
    ]
    assert result.stage_payloads.manuscript is not None
    assert result.counts["words"] > 0
    assert result.counts["claims"] > 0
    assert result.counts["facts"] > 0
    assert result.selected_story_id
    assert result.story_candidates
    assert result.usage["totals"]["logical_call_count"] >= 5
    assert result.usage["totals"]["estimated_cost_cny"] > 0
    assert (work_dir / "FINAL_CONTINUATION_RESULT.json").is_file()
    assert (work_dir / "manuscript" / "ARTICLE_MANUSCRIPT_BODY.md").is_file()


def test_partial_review_commits_and_manuscript_excludes_blocked_sections(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    writer = BlockingWriter()
    continuation = ArticleContinuation(
        result_synthesis_provider=FakeSynthesisProvider(),
        architecture_provider=TwoSectionArchitectureProvider(),
        section_writer=writer,
        format_repair=None,
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )

    result = continuation.run(_request(source_dir, work_dir))

    assert result.status in {"completed", "partial"}
    review = result.stage_payloads.review
    assert review is not None
    assert review.sections
    assert review.hard_blockers
    assert any(item.status.value == "blocked" for item in review.sections)
    assert result.receipts[3].status == "partial"
    manuscript = result.stage_payloads.manuscript
    assert manuscript is not None
    assert manuscript.body.status == "partial"
    assert [item.heading for item in manuscript.body.sections] == ["Results"]
    assert "Limitations" not in manuscript.body_markdown
    assert len(manuscript.blocked_handoff) == 1
    assert manuscript.blocked_handoff[0].hard_blockers
    assert result.counts["blockers"] >= 1


def test_all_blocked_review_still_produces_manuscript_handoff(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    writer = BlockAllWriter()
    continuation = ArticleContinuation(
        result_synthesis_provider=FakeSynthesisProvider(),
        architecture_provider=TwoSectionArchitectureProvider(),
        section_writer=writer,
        format_repair=None,
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )

    result = continuation.run(_request(source_dir, work_dir))

    assert result.status in {"completed", "partial"}
    assert writer.calls == 2
    review = result.stage_payloads.review
    assert review is not None
    assert review.sections
    assert review.hard_blockers
    manuscript = result.stage_payloads.manuscript
    assert manuscript is not None
    assert manuscript.body.status == "blocked"
    assert manuscript.body.sections == []
    assert manuscript.body_markdown == ""
    assert len(manuscript.blocked_handoff) == 2
    assert all(item.hard_blockers for item in manuscript.blocked_handoff)
    assert result.counts["blockers"] >= 2


def test_extra_unbound_trusted_values_do_not_fail_writing(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, _ = _continuation(
        architecture=PartialBindingArchitectureProvider()
    )

    result = continuation.run(_request(source_dir, work_dir))

    assert result.status in {"completed", "partial"}
    assert result.stage_payloads.writing is not None
    assert result.stage_payloads.writing.errors == []
    assert result.stage_payloads.review is not None
    assert result.stage_payloads.manuscript is not None
    unbound_value_notes = [
        (index, item)
        for index, item in enumerate(result.errors + result.warnings)
        if "does not correspond to any Stage 9 artifact-field binding" in item
    ]
    assert unbound_value_notes == []


def test_story_binds_descriptor_field_without_value_as_figure_only_provenance(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, _ = _continuation(
        architecture=MissingValueArchitectureProvider()
    )

    result = continuation.run(_request(source_dir, work_dir))

    assert result.status in {"completed", "partial"}
    assert result.stage_payloads.writing is not None
    assert result.stage_payloads.writing.errors == []
    assert result.stage_payloads.manuscript is not None
    assert not any(
        "missing from the contracted inventory" in item
        for item in result.errors + result.warnings
    )
    assert result.stage_payloads.writing.value_alias_map
    assert all(
        info.get("field") != "wavelengths_nm"
        for info in result.stage_payloads.writing.value_alias_map.values()
    )
    assert any(
        info.get("field") == "channels.angle=0|pol=s.A.mean"
        for info in result.stage_payloads.writing.value_alias_map.values()
    )


def test_route_local_duplicate_artifact_id_single_retained_observation_passes(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path, generic_artifact=True)
    work_dir = tmp_path / "work"
    continuation, _ = _continuation(
        synthesis=MixedArtifactSynthesisProvider()
    )

    result = continuation.run(_request(source_dir, work_dir))

    assert result.status in {"completed", "partial"}
    assert result.stage_payloads.writing is not None
    assert result.stage_payloads.manuscript is not None
    assert not any(
        "ambiguous duplicate artifact_id across routes" in item
        for item in result.errors + result.warnings
    )


def test_contracted_inventory_uses_observation_matched_asset(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path, generic_artifact=True)
    bundle = load_source_pipeline(source_dir)
    route_one = next(
        asset
        for asset in bundle.assets
        if asset.observation_id == "observation-route_01"
    )
    route_two = next(
        asset
        for asset in bundle.assets
        if asset.observation_id == "observation-route_02"
    )
    generic_one = next(
        item
        for item in route_one.descriptors
        if item.artifact_id == "DESIGN_PORTFOLIOS.json"
    )
    generic_two = next(
        item
        for item in route_two.descriptors
        if item.artifact_id == "DESIGN_PORTFOLIOS.json"
    )
    assert generic_one.sha256 != generic_two.sha256
    observation = route_one.observation.model_copy(
        update={"artifact_ids": ["DESIGN_PORTFOLIOS.json"]}
    )
    synthesis = ArticleResultSynthesisResult(
        status="partial",
        result_id="",
        source_plan_id="",
        observations=(observation,),
    )

    descriptors, values = _contracted_inventory(synthesis, bundle)

    assert len(descriptors) == 1
    assert descriptors[0].artifact_id == "DESIGN_PORTFOLIOS.json"
    assert descriptors[0].sha256 == generic_one.sha256
    assert descriptors[0].path == generic_one.path
    assert len(values) == 1
    assert values[0].artifact_id == "DESIGN_PORTFOLIOS.json"
    assert values[0].source_hash == generic_one.sha256


def test_contracted_inventory_genuine_ambiguity_fails_closed(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path, generic_artifact=True)
    bundle = load_source_pipeline(source_dir)
    observations = tuple(
        asset.observation.model_copy(
            update={"artifact_ids": ["DESIGN_PORTFOLIOS.json"]}
        )
        for asset in bundle.assets
    )
    synthesis = ArticleResultSynthesisResult(
        status="partial",
        result_id="",
        source_plan_id="",
        observations=observations,
    )

    with pytest.raises(
        ContinuationIntegrityError,
        match="ambiguous retained artifact_id",
    ):
        _contracted_inventory(synthesis, bundle)


def test_contracted_inventory_missing_observation_mapping_fails_closed(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path, generic_artifact=True)
    bundle = load_source_pipeline(source_dir)
    ghost = bundle.assets[0].observation.model_copy(
        update={"observation_id": "ghost-observation"}
    )
    synthesis = ArticleResultSynthesisResult(
        status="partial",
        result_id="",
        source_plan_id="",
        observations=(ghost,),
    )

    with pytest.raises(
        ContinuationIntegrityError,
        match="no matching source asset",
    ):
        _contracted_inventory(synthesis, bundle)


def test_contracted_inventory_duplicate_observation_mapping_fails_closed(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    bundle = load_source_pipeline(source_dir)
    duplicated = replace(bundle, assets=(bundle.assets[0], bundle.assets[0]))
    observation = bundle.assets[0].observation.model_copy(
        update={"artifact_ids": [bundle.assets[0].observation.artifact_ids[0]]}
    )
    synthesis = ArticleResultSynthesisResult(
        status="partial",
        result_id="",
        source_plan_id="",
        observations=(observation,),
    )

    with pytest.raises(
        ContinuationIntegrityError,
        match="duplicate source asset observation_id",
    ):
        _contracted_inventory(synthesis, duplicated)


def test_provider_unavailable_fail_open_preserves_earlier_stages(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    synthesis = FakeSynthesisProvider(responses=[RuntimeError("down"), RuntimeError("down")])
    continuation, _ = _continuation(synthesis=synthesis)

    result = continuation.run(_request(source_dir, tmp_path / "work"))

    assert result.status == "unavailable"
    assert result.receipts[0].status == "unavailable"
    assert [item.status for item in result.receipts[1:]] == ["skipped"] * 4
    assert result.stage_payloads.manuscript is None


def test_architecture_provider_unavailable_keeps_synthesis_output(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    synthesis = FakeSynthesisProvider()
    continuation = ArticleContinuation(
        result_synthesis_provider=synthesis,
        architecture_provider=lambda requests: (_ for _ in ()).throw(
            RuntimeError("architecture down")
        ),
        section_writer=FakeWriter(),
        format_repair=FakeFormatRepair(),
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )

    result = continuation.run(_request(source_dir, tmp_path / "work"))

    assert result.status == "unavailable"
    assert result.receipts[0].status == "completed"
    assert result.receipts[1].status == "unavailable"
    assert result.stage_payloads.result_synthesis is not None
    assert result.stage_payloads.architecture is None
    assert (tmp_path / "work" / "01-result_synthesis.json").is_file()

    recovered = ArticleContinuation(
        result_synthesis_provider=synthesis,
        architecture_provider=FakeArchitectureProvider(),
        section_writer=FakeWriter(),
        format_repair=FakeFormatRepair(),
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )
    resumed = recovered.resume(_request(source_dir, tmp_path / "work"))
    assert resumed.status in {"completed", "partial"}
    assert synthesis.calls == 2
    assert resumed.stage_payloads.manuscript is not None
    attempts = json.loads(
        (tmp_path / "work" / "CONTINUATION_ATTEMPTS.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert attempts["stage"] == "architecture"
    assert attempts["status"] == "unavailable"


def test_architecture_failed_hard_block_usage_included_in_totals(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, _ = _continuation(architecture=InvalidArchitectureProvider())

    result = continuation.run(_request(source_dir, work_dir))

    assert result.status == "failed"
    architecture_rows = [
        row for row in result.usage["rows"] if row["stage"] == "architecture"
    ]
    assert len(architecture_rows) == 1
    assert architecture_rows[0]["input_tokens"] == 5
    assert architecture_rows[0]["output_tokens"] == 5
    assert result.usage["totals"]["logical_call_count"] == len(
        result.usage["rows"]
    )
    attempts = (work_dir / "CONTINUATION_ATTEMPTS.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(attempts) == 1
    attempt = json.loads(attempts[0])
    assert attempt["stage"] == "architecture"
    assert attempt["status"] == "failed"
    assert attempt["usage"]["input_tokens"] == 5


def test_unavailable_then_recovery_totals_include_both_calls_once(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    first_continuation = ArticleContinuation(
        result_synthesis_provider=FakeSynthesisProvider(),
        architecture_provider=EmptyArchitectureProvider(),
        section_writer=FakeWriter(),
        format_repair=FakeFormatRepair(),
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )
    first = first_continuation.run(_request(source_dir, work_dir))
    assert first.status == "unavailable"
    first_architecture_rows = [
        row for row in first.usage["rows"] if row["stage"] == "architecture"
    ]
    assert len(first_architecture_rows) == 1
    assert first_architecture_rows[0]["input_tokens"] == 5

    recovered, _ = _continuation()
    resumed = recovered.resume(_request(source_dir, work_dir))

    assert resumed.status in {"completed", "partial"}
    architecture_rows = [
        row for row in resumed.usage["rows"] if row["stage"] == "architecture"
    ]
    assert len(architecture_rows) == 2
    assert {row["input_tokens"] for row in architecture_rows} == {5}
    assert resumed.usage["totals"]["logical_call_count"] == len(
        resumed.usage["rows"]
    )


def test_synthesis_unavailable_attempt_preserves_provider_usage(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    provider = UnavailableSynthesisProvider()
    continuation = ArticleContinuation(
        result_synthesis_provider=provider,
        architecture_provider=FakeArchitectureProvider(),
        section_writer=FakeWriter(),
        format_repair=FakeFormatRepair(),
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )

    result = continuation.run(_request(source_dir, work_dir))

    assert result.status == "unavailable"
    synthesis_rows = [
        row
        for row in result.usage["rows"]
        if row["stage"] == "result_synthesis"
    ]
    assert len(synthesis_rows) == 2
    assert all(row["input_tokens"] == 4 for row in synthesis_rows)
    assert all(row["output_tokens"] == 6 for row in synthesis_rows)
    assert result.usage["totals"]["logical_call_count"] == len(
        result.usage["rows"]
    )
    attempts = (work_dir / "CONTINUATION_ATTEMPTS.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(attempts) == 1
    attempt = json.loads(attempts[0])
    assert attempt["stage"] == "result_synthesis"
    assert attempt["status"] == "unavailable"
    assert len(attempt["usage"]) == 2
    assert all(
        item == {
            "model_name": "fake-provider",
            "input_tokens": 4,
            "output_tokens": 6,
        }
        for item in attempt["usage"]
    )


def test_malformed_attempt_usage_is_ignored_fail_open_for_accounting(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, state = _continuation()
    first = continuation.run(_request(source_dir, work_dir))
    calls_after_run = _provider_calls(continuation, state)
    attempts_path = work_dir / "CONTINUATION_ATTEMPTS.jsonl"
    with attempts_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                ContinuationAttemptRecord(
                    attempt_id="attempt-fake-usage",
                    sequence=9,
                    stage="architecture",
                    status="failed",
                    payload_digest="fake-digest",
                    usage="not-a-usage-mapping",
                ).model_dump(mode="json")
            )
            + "\n"
        )

    resumed = continuation.resume(_request(source_dir, work_dir))

    assert resumed == first
    assert resumed.usage == first.usage
    assert _provider_calls(continuation, state) == calls_after_run


def test_exact_resume_causes_zero_provider_calls(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, state = _continuation()
    first = continuation.run(_request(source_dir, work_dir))
    calls_after_run = _provider_calls(continuation, state)

    resumed = continuation.resume(_request(source_dir, work_dir))

    assert resumed == first
    assert resumed.usage == first.usage
    assert resumed.usage["totals"] == first.usage["totals"]
    assert _provider_calls(continuation, state) == calls_after_run


def test_interrupted_stage_recovery_resumes_without_repeating(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    first_synthesis = FakeSynthesisProvider()
    first_architecture = FakeArchitectureProvider()

    def interrupted_writer(request: Mapping[str, Any]) -> WriterProviderResult:
        raise KeyboardInterrupt()

    interrupted = ArticleContinuation(
        result_synthesis_provider=first_synthesis,
        architecture_provider=first_architecture,
        section_writer=interrupted_writer,  # type: ignore[arg-type]
        format_repair=FakeFormatRepair(),
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )
    try:
        interrupted.run(_request(source_dir, work_dir))
    except KeyboardInterrupt:
        pass
    else:  # pragma: no cover - interruption must propagate
        raise AssertionError("interruption did not propagate")
    synthesis_calls = first_synthesis.calls
    architecture_calls = first_architecture.calls

    resumed, state = _continuation()
    writer = state["writer"]
    reviewers = state["reviewers"]
    final = resumed.resume(_request(source_dir, work_dir))

    assert final.status in {"completed", "partial"}
    assert first_synthesis.calls == synthesis_calls
    assert first_architecture.calls == architecture_calls
    assert writer.calls == 1
    assert reviewers[0].calls == 1
    assert reviewers[1].calls == 1


def test_snapshot_tamper_fails_closed(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, state = _continuation()
    continuation.run(_request(source_dir, work_dir))
    snapshot = work_dir / "02-architecture.json"
    snapshot.write_text(snapshot.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
    calls_before = _provider_calls(continuation, state)

    resumed = continuation.resume(_request(source_dir, work_dir))

    assert resumed.status == "failed"
    assert any("SHA256" in item for item in resumed.errors)
    assert _provider_calls(continuation, state) == calls_before


def test_final_result_id_tamper_fails_closed(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, _ = _continuation()
    continuation.run(_request(source_dir, work_dir))
    final_path = work_dir / "FINAL_CONTINUATION_RESULT.json"
    payload = json.loads(final_path.read_text(encoding="utf-8"))
    payload["result_id"] = "0" * 64
    final_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    resumed = continuation.resume(_request(source_dir, work_dir))

    assert resumed.status == "failed"
    assert any("final continuation result is invalid" in item for item in resumed.errors)


def test_route_progress_sha_mismatch_fails_closed(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    progress_path = source_dir / "ROUTE_PROGRESS.json"
    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    payload["execution"][0]["snapshot_sha256"] = "0" * 64
    progress_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    continuation, _ = _continuation()

    result = continuation.run(
        _request(source_dir, tmp_path / "work")
    )

    assert result.status == "failed"
    assert any("source pipeline validation failed" in item for item in result.errors)


def test_missing_execution_snapshot_fails_closed(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    snapshot = next(source_dir.glob("route-execution-*.json"))
    snapshot.unlink()
    continuation, _ = _continuation()

    result = continuation.run(_request(source_dir, tmp_path / "work"))

    assert result.status == "failed"
    assert any("missing" in item for item in result.errors)


def test_cross_wired_asset_execution_reference_fails_closed(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    progress_path = source_dir / "ROUTE_PROGRESS.json"
    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    payload["asset"][0]["execution_snapshot_filename"], payload["asset"][1][
        "execution_snapshot_filename"
    ] = (
        payload["asset"][1]["execution_snapshot_filename"],
        payload["asset"][0]["execution_snapshot_filename"],
    )
    progress_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    continuation, _ = _continuation()

    result = continuation.run(_request(source_dir, tmp_path / "work"))

    assert result.status == "failed"
    assert any("execution snapshot filename" in item for item in result.errors)


def test_duplicate_descriptor_artifact_id_across_routes_fails_closed(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path, same_task=True)
    continuation, _ = _continuation()

    result = continuation.run(_request(source_dir, tmp_path / "work"))

    assert result.status == "failed"
    assert any(
        "ambiguous retained artifact_id" in item
        for item in result.errors
    )


def test_story_selection_explicit_and_default(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    default, _ = _continuation()
    default_result = default.run(_request(source_dir, tmp_path / "work-default"))
    assert default_result.selected_story_id
    assert "highest recommendation_score" in default_result.story_selection_rationale
    chosen_default = default_result.selected_story_id

    explicit, _ = _continuation()
    explicit_result = explicit.run(
        _request(
            source_dir,
            tmp_path / "work-explicit",
            selected_story_id=chosen_default,
        )
    )
    assert explicit_result.selected_story_id == chosen_default
    assert "explicit selected_story_id" in explicit_result.story_selection_rationale

    unknown, _ = _continuation()
    unknown_result = unknown.run(
        _request(
            source_dir,
            tmp_path / "work-unknown",
            selected_story_id="story-999",
        )
    )
    assert unknown_result.status == "failed"
    assert any("selected story" in item for item in unknown_result.errors)


def test_no_source_mutation(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    before = {
        path.relative_to(source_dir).as_posix(): path.read_bytes()
        for path in source_dir.rglob("*")
        if path.is_file()
    }
    continuation, _ = _continuation()
    continuation.run(_request(source_dir, tmp_path / "work"))
    after = {
        path.relative_to(source_dir).as_posix(): path.read_bytes()
        for path in source_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_cli_help_and_safe_configuration(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_article_continuation.py"
    help_result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
    )
    assert help_result.returncode == 0
    assert "--source-pipeline-dir" in help_result.stdout
    assert "--resume" in help_result.stdout

    missing = tmp_path / "does-not-exist"
    bad_result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--source-pipeline-dir",
            str(missing),
            "--work-dir",
            str(tmp_path / "cli-work"),
            "--run-id",
            "cli-run",
        ],
        capture_output=True,
        text=True,
    )
    assert bad_result.returncode == 1
    assert '"status": "failed"' in bad_result.stdout


def test_source_work_overlap_rejected_with_zero_source_writes(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    before = {
        path.relative_to(source_dir).as_posix(): path.read_bytes()
        for path in source_dir.rglob("*")
        if path.is_file()
    }
    nested_work = source_dir / "nested-work"
    continuation, _ = _continuation()

    result = continuation.run(_request(source_dir, nested_work))

    assert result.status == "failed"
    assert any(
        "work_dir must not be equal to or nested under" in item
        for item in result.errors
    )
    after = {
        path.relative_to(source_dir).as_posix(): path.read_bytes()
        for path in source_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert list(source_dir.glob("nested-work/*")) == []


def test_run_branch_route_and_execution_count_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    progress_path = source_dir / "ROUTE_PROGRESS.json"
    payload = json.loads(progress_path.read_text(encoding="utf-8"))
    payload["execution"][0]["run_id"] = "wrong-run"
    progress_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    continuation, _ = _continuation()
    result = continuation.run(_request(source_dir, tmp_path / "work-run"))
    assert result.status == "failed"
    assert any("run/branch" in item for item in result.errors)

    source_dir2 = _write_source_pipeline(tmp_path / "case2")
    final_path = source_dir2 / "FINAL_PIPELINE_RESULT.json"
    final_payload = json.loads(final_path.read_text(encoding="utf-8"))
    final_payload["execution_count"] = final_payload["execution_count"] + 1
    from optomind_optics.harness.article_pipeline import (
        compute_pipeline_result_id,
    )

    final_payload["result_id"] = compute_pipeline_result_id(final_payload)
    final_path.write_text(
        json.dumps(final_payload, sort_keys=True), encoding="utf-8"
    )
    result2 = continuation.run(_request(source_dir2, tmp_path / "work-count"))
    assert result2.status == "failed"
    assert any("execution_count" in item for item in result2.errors)

    source_dir3 = _write_source_pipeline(tmp_path / "case3")
    progress3 = json.loads(
        (source_dir3 / "ROUTE_PROGRESS.json").read_text(encoding="utf-8")
    )
    progress3["execution"][0]["route_id"] = "route_99"
    (source_dir3 / "ROUTE_PROGRESS.json").write_text(
        json.dumps(progress3, sort_keys=True), encoding="utf-8"
    )
    result3 = continuation.run(_request(source_dir3, tmp_path / "work-route"))
    assert result3.status == "failed"
    assert any("route_id" in item for item in result3.errors)


def test_asset_progress_branch_run_mismatch_fails_closed_before_providers(
    tmp_path: Path,
) -> None:
    for field, value in (("branch_id", "wrong-branch"), ("run_id", "wrong-run")):
        source_dir = _write_source_pipeline(tmp_path / field)
        progress_path = source_dir / "ROUTE_PROGRESS.json"
        payload = json.loads(progress_path.read_text(encoding="utf-8"))
        payload["asset"][0][field] = value
        progress_path.write_text(
            json.dumps(payload, sort_keys=True), encoding="utf-8"
        )
        continuation, state = _continuation()

        result = continuation.run(_request(source_dir, tmp_path / f"work-{field}"))

        assert result.status == "failed"
        assert any(
            "asset route" in item and "run/branch" in item
            for item in result.errors
        )
        assert _provider_calls(continuation, state) == 0


def test_ready_but_unexecuted_route_allowed(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(
        tmp_path, executed_route_ids=("route_01",)
    )
    bundle = load_source_pipeline(source_dir)
    assert len(bundle.executions) == 1
    assert len(bundle.assets) == 1

    continuation, _ = _continuation()
    result = continuation.run(_request(source_dir, tmp_path / "work"))

    assert result.status in {"completed", "partial"}
    assert result.stage_payloads.manuscript is not None
    assert len(result.stage_payloads.result_synthesis.findings) == 1


def test_synthesis_unavailable_then_recovery_retries_only_synthesis(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    failing_synthesis = FakeSynthesisProvider(
        responses=[RuntimeError("down"), RuntimeError("down")]
    )
    first = ArticleContinuation(
        result_synthesis_provider=failing_synthesis,
        architecture_provider=FakeArchitectureProvider(),
        section_writer=FakeWriter(),
        format_repair=FakeFormatRepair(),
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )
    work_dir = tmp_path / "work"
    result = first.run(_request(source_dir, work_dir))
    assert result.status == "unavailable"
    assert result.stage_payloads.manuscript is None
    assert (work_dir / "FINAL_CONTINUATION_RESULT.json").is_file()

    synthesis = FakeSynthesisProvider()
    architecture = FakeArchitectureProvider()
    writer = FakeWriter()
    reviewers = (FakeReviewer(), FakeReviewer(), FakeReviewer())
    recovered = ArticleContinuation(
        result_synthesis_provider=synthesis,
        architecture_provider=architecture,
        section_writer=writer,
        format_repair=FakeFormatRepair(),
        scientific_reviewer=reviewers[0],
        expression_reviewer=reviewers[1],
        author_reviser=reviewers[2],
    )
    resumed = recovered.resume(_request(source_dir, work_dir))

    assert resumed.status in {"completed", "partial"}
    assert resumed.stage_payloads.manuscript is not None
    assert synthesis.calls == 2
    assert architecture.calls == 1
    assert writer.calls == 1
    attempts = (work_dir / "CONTINUATION_ATTEMPTS.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(attempts) == 1
    assert json.loads(attempts[0])["stage"] == "result_synthesis"
    versions = (work_dir / "CONTINUATION_VERSIONS.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(versions) == 2
    assert json.loads(versions[-1])["status"] == resumed.status


def test_partial_final_does_not_block_resume_and_is_replaced(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    failing = ArticleContinuation(
        result_synthesis_provider=FakeSynthesisProvider(
            responses=[RuntimeError("down"), RuntimeError("down")]
        ),
        architecture_provider=FakeArchitectureProvider(),
        section_writer=FakeWriter(),
        format_repair=FakeFormatRepair(),
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
    )
    first = failing.run(_request(source_dir, work_dir))
    assert first.status == "unavailable"

    recovered, _ = _continuation()
    resumed = recovered.resume(_request(source_dir, work_dir))
    final_payload = json.loads(
        (work_dir / "FINAL_CONTINUATION_RESULT.json").read_text(encoding="utf-8")
    )
    assert final_payload["status"] == resumed.status
    assert resumed.status in {"completed", "partial"}


def test_explicit_story_selection_survives_interruption(tmp_path: Path) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    synthesis = FakeSynthesisProvider()
    architecture = FakeArchitectureProvider()

    class FaultAtSecondLedger:
        def __init__(self) -> None:
            self.ledger_calls = 0

        def __call__(self, boundary: str) -> None:
            if boundary == "ledger":
                self.ledger_calls += 1
                if self.ledger_calls == 2:
                    raise KeyboardInterrupt()

    interrupted = ArticleContinuation(
        result_synthesis_provider=synthesis,
        architecture_provider=architecture,
        section_writer=FakeWriter(),
        format_repair=FakeFormatRepair(),
        scientific_reviewer=FakeReviewer(),
        expression_reviewer=FakeReviewer(),
        author_reviser=FakeReviewer(),
        fault_hook=FaultAtSecondLedger(),
    )
    try:
        interrupted.run(
            _request(
                source_dir,
                work_dir,
                selected_story_id="story-02",
            )
        )
    except KeyboardInterrupt:
        pass
    else:  # pragma: no cover
        raise AssertionError("interruption did not propagate")
    synthesis_calls = synthesis.calls
    architecture_calls = architecture.calls

    recovered, _ = _continuation()
    final = recovered.resume(
        _request(source_dir, work_dir, selected_story_id="story-02")
    )

    assert final.status in {"completed", "partial"}
    assert final.selected_story_id == "story-02"
    assert synthesis.calls == synthesis_calls
    assert architecture.calls == architecture_calls
    assert architecture_calls == 1


def _events_lines(work_dir: Path) -> List[str]:
    path = work_dir / "CONTINUATION_EVENTS.jsonl"
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_event_tampering_fails_closed_before_providers(tmp_path: Path) -> None:
    def tamper(lines: List[str], kind: str) -> List[str]:
        if kind == "tampered_id":
            payload = json.loads(lines[-1])
            payload["event_id"] = "0" * 64
            lines[-1] = json.dumps(payload, sort_keys=True)
        elif kind == "missing":
            return lines[1:]
        elif kind == "duplicated":
            return [lines[0], *lines]
        elif kind == "extra_junk":
            return [*lines, json.dumps({"junk": 1}, sort_keys=True)]
        elif kind == "reordered":
            lines = list(lines)
            lines[1], lines[2] = lines[2], lines[1]
        elif kind == "malformed":
            return [*lines[:-1], "not json"]
        return lines

    for kind in (
        "tampered_id",
        "missing",
        "duplicated",
        "extra_junk",
        "reordered",
        "malformed",
    ):
        source_dir = _write_source_pipeline(tmp_path)
        work_dir = tmp_path / f"work-{kind}"
        continuation, state = _continuation()
        continuation.run(_request(source_dir, work_dir))
        calls_before = _provider_calls(continuation, state)
        events = _events_lines(work_dir)
        tampered = tamper(events, kind)
        (work_dir / "CONTINUATION_EVENTS.jsonl").write_text(
            "\n".join(tampered) + "\n", encoding="utf-8"
        )

        resumed = continuation.resume(_request(source_dir, work_dir))

        assert resumed.status == "failed"
        assert _provider_calls(continuation, state) == calls_before


class _OneShotBoundaryFault:
    def __init__(self, boundary: str) -> None:
        self.boundary = boundary
        self.count = 0

    def __call__(self, boundary: str) -> None:
        if boundary == self.boundary and self.count == 0:
            self.count += 1
            raise KeyboardInterrupt()


def test_crash_windows_after_snapshot_events_checkpoint_ledger(
    tmp_path: Path,
) -> None:
    for boundary in ("snapshot", "events", "checkpoint", "ledger"):
        source_dir = _write_source_pipeline(tmp_path)
        work_dir = tmp_path / f"work-{boundary}"
        synthesis = FakeSynthesisProvider()
        architecture = FakeArchitectureProvider()
        interrupted = ArticleContinuation(
            result_synthesis_provider=synthesis,
            architecture_provider=architecture,
            section_writer=FakeWriter(),
            format_repair=FakeFormatRepair(),
            scientific_reviewer=FakeReviewer(),
            expression_reviewer=FakeReviewer(),
            author_reviser=FakeReviewer(),
            fault_hook=_OneShotBoundaryFault(boundary),
        )
        try:
            interrupted.run(_request(source_dir, work_dir))
        except KeyboardInterrupt:
            pass
        else:  # pragma: no cover
            raise AssertionError(
                f"interruption did not propagate after {boundary}"
            )
        calls_before = (synthesis.calls, architecture.calls)

        baseline_cont, _ = _continuation()
        baseline_dir = tmp_path / f"baseline-{boundary}"
        baseline = baseline_cont.run(_request(source_dir, baseline_dir))

        recovered, recovered_state = _continuation()
        final = recovered.resume(_request(source_dir, work_dir))

        assert final.status in {"completed", "partial"}
        assert final.stage_payloads.manuscript is not None
        events = _events_lines(work_dir)
        assert len(events) == 5
        assert len({json.loads(line)["event_id"] for line in events}) == 5
        assert final.result_id == baseline.result_id
        recovered_synthesis_calls = recovered_state["synthesis"].calls
        recovered_architecture_calls = recovered_state["architecture"].calls
        if boundary == "snapshot":
            assert recovered_synthesis_calls == 2
            assert recovered_architecture_calls == 1
        else:
            assert recovered_synthesis_calls == 0
            assert recovered_architecture_calls == 1


def test_final_recomputed_id_tamper_rejected(tmp_path: Path) -> None:
    for field_path in (
        ("counts", "words"),
        ("selected_story_id",),
        ("stage_payloads", "manuscript", "body_markdown"),
    ):
        source_dir = _write_source_pipeline(tmp_path)
        work_dir = tmp_path / ("work-" + "-".join(field_path))
        continuation, _ = _continuation()
        continuation.run(_request(source_dir, work_dir))
        final_path = work_dir / "FINAL_CONTINUATION_RESULT.json"
        payload = json.loads(final_path.read_text(encoding="utf-8"))
        node: Any = payload
        for key in field_path[:-1]:
            node = node[key]
        last = field_path[-1]
        node[last] = "tampered" if node[last] != "tampered" else "tampered2"
        from optomind_optics.harness.article_continuation import (
            compute_continuation_result_id,
        )

        payload["result_id"] = compute_continuation_result_id(payload)
        final_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

        resumed = continuation.resume(_request(source_dir, work_dir))
        assert resumed.status == "failed"
        assert any(
            "final continuation result is invalid" in item
            for item in resumed.errors
        )


def test_typed_checkpoints_reload_through_strict_models(tmp_path: Path) -> None:
    from optomind_optics.harness.article_architecture import (
        ArticleArchitectureResult,
    )
    from optomind_optics.harness.article_manuscript import (
        ArticleManuscriptPackage,
    )
    from optomind_optics.harness.article_result_synthesis import (
        ArticleResultSynthesisResult,
    )
    from optomind_optics.harness.article_review import ArticleReviewResult
    from optomind_optics.harness.article_writing import ArticleDraftBundle

    models = {
        "result_synthesis": ArticleResultSynthesisResult,
        "architecture": ArticleArchitectureResult,
        "writing": ArticleDraftBundle,
        "review": ArticleReviewResult,
        "manuscript": ArticleManuscriptPackage,
    }
    source_dir = _write_source_pipeline(tmp_path)
    work_dir = tmp_path / "work"
    continuation, _ = _continuation()
    result = continuation.run(_request(source_dir, work_dir))
    assert result.status in {"completed", "partial"}

    for checkpoint in sorted(work_dir.glob("checkpoint-*.json")):
        record = json.loads(checkpoint.read_text(encoding="utf-8"))
        assert record["stage"] in models
        snapshot = json.loads(
            (work_dir / record["snapshot_filename"]).read_text(encoding="utf-8")
        )
        models[record["stage"]].model_validate(snapshot)
        assert record["receipt"]["status"] in {"completed", "partial"}


def test_scoped_story_values_fails_closed_on_missing_lineage_value(
    tmp_path: Path,
) -> None:
    """A claim lineage pair absent from the inventory hard-blocks."""

    source_dir = _write_source_pipeline(tmp_path)
    bundle = load_source_pipeline(source_dir)
    plan = bundle.plan
    assert plan is not None
    planning = _planning(plan)
    assets = [
        _asset_for(row)
        for row in planning.rows
        if row.status == "ready" and row.request is not None
    ]
    synthesis = synthesize_article_results(
        plan,
        planning,
        assets,
        provider=FakeSynthesisProvider(),
        run_id="run-continuation-1",
    )
    assert synthesis.status == "ready"
    assert synthesis.ledger is not None
    assert synthesis.derived_plan is not None
    ledger = synthesis.ledger
    positive = next(
        claim
        for claim in ledger.claims
        if claim.status == ClaimStatus.partially_supported
    )
    source_artifact = positive.source_artifact_ids[0]
    enriched = positive.model_copy(
        update={
            "metadata": {
                **positive.metadata,
                "value_lineage": [
                    *(positive.metadata.get("value_lineage") or []),
                    {
                        "artifact_id": source_artifact,
                        "field": "wavelengths_nm",
                        "label": "wavelength axis",
                        "unit": "nm",
                        "source_alias": "TV99",
                    },
                ],
            }
        }
    )
    ledger = ledger.model_copy(
        update={
            "claims": [
                enriched if claim.claim_id == positive.claim_id else claim
                for claim in ledger.claims
            ]
        }
    )
    descriptors, values = _contracted_inventory(synthesis, bundle)
    architecture = build_article_architecture(
        synthesis.derived_plan,
        ledger,
        descriptors,
        architecture_provider=FakeArchitectureProvider(),
    )
    assert architecture.stories
    with pytest.raises(ContinuationIntegrityError) as excinfo:
        _scoped_story_values(
            architecture,
            architecture.stories[0].story_id,
            values,
            ledger,
        )
    assert "has no matching trusted value record" in str(excinfo.value)
    assert "wavelengths_nm" in str(excinfo.value)


def test_scoped_story_values_keeps_old_behavior_without_lineage(
    tmp_path: Path,
) -> None:
    source_dir = _write_source_pipeline(tmp_path)
    bundle = load_source_pipeline(source_dir)
    plan = bundle.plan
    assert plan is not None
    planning = _planning(plan)
    assets = [
        _asset_for(row)
        for row in planning.rows
        if row.status == "ready" and row.request is not None
    ]
    synthesis = synthesize_article_results(
        plan,
        planning,
        assets,
        provider=FakeSynthesisProvider(),
        run_id="run-continuation-1",
    )
    assert synthesis.ledger is not None
    assert synthesis.derived_plan is not None
    descriptors, values = _contracted_inventory(synthesis, bundle)
    architecture = build_article_architecture(
        synthesis.derived_plan,
        synthesis.ledger,
        descriptors,
        architecture_provider=FakeArchitectureProvider(),
    )
    assert architecture.stories
    scoped = _scoped_story_values(
        architecture,
        architecture.stories[0].story_id,
        values,
        None,
    )
    assert len(scoped) == 2
    assert all(value in values for value in scoped)
    assert all(
        value.field == "channels.angle=0|pol=s.A.mean" for value in scoped
    )
