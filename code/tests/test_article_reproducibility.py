from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from optomind_optics.harness.article_architecture import (
    ArchitectureProviderResult,
    ArtifactDescriptor,
    build_article_architecture,
)
from optomind_optics.harness.article_claims import build_claim_ledger
from optomind_optics.harness.article_contracts import (
    ClaimStatus,
    ExperimentStatus,
    ObservationCard,
)
from optomind_optics.harness.article_director import ArticleDirector
from optomind_optics.harness.article_execution import ArticleExecutionResult
from optomind_optics.harness.article_feedback import ArticleFeedbackController
from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptBody,
    ArticleManuscriptPackage,
    ParagraphManuscriptSource,
    build_article_manuscript,
    compute_manuscript_body_id,
    compute_manuscript_package_id,
)
from optomind_optics.harness.article_reproducibility import (
    ArticleReproducibilityIntegrityError,
    build_article_reproducibility,
    compute_reproducibility_package_id,
    validate_reproducibility_package,
    write_reproducibility_package,
    _discover_critical_experiments,
)
from optomind_optics.harness.article_review import (
    ReviewerProviderResult,
    build_article_review,
)
from optomind_optics.harness.article_writing import (
    TrustedValueRecord,
    WriterProviderResult,
    build_article_draft_bundle,
)
from optomind_optics.harness.method_research import (
    MethodResearchReport,
    MethodResearchStatus,
)
from optomind_optics.harness.problem_analyzer import (
    OpticalProblemAnalysis,
    ResearchIntent,
    TMMCompatibility,
)
from optomind_optics.harness.replay import ReplayArtifactCheck, ReplayManifest


TASK_BYTES = json.dumps({"task_hash": "task-1"}).encode()
TASK_SHA = hashlib.sha256(TASK_BYTES).hexdigest()
RUN_BYTES = json.dumps({"run_id": "run-1"}).encode()


def _analysis() -> OpticalProblemAnalysis:
    return OpticalProblemAnalysis(
        problem_id="problem-1",
        original_request="Design a broadband AR coating over 450-700 nm.",
        normalized_request_english=(
            "Design a broadband one-dimensional antireflection coating for "
            "fused silica in air over 450-700 nm."
        ),
        primary_intent=ResearchIntent.design,
        compatibility=TMMCompatibility.compatible,
        compatibility_reason="planar multilayer stack within the TMM domain",
        needs_method_research=True,
        wavelengths_nm=[(450.0, 700.0)],
        target_observables=["mean reflectance"],
        preferred_behaviors=["reflectance below target"],
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


def _ledger():
    plan = _plan()
    observation = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004},
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[
            {
                "hypothesis_id": "hyp-01",
                "to_status": "partially_supported",
                "evidence_kind": "partial_support",
                "reason": "improved",
            }
        ],
        summary="observation",
    )
    feedback = ArticleFeedbackController().update(plan, [observation])
    ledger = build_claim_ledger(plan, [feedback], [observation])
    assert ledger.validation_errors == []
    return plan, ledger


def _value_records(sha256: str) -> list[TrustedValueRecord]:
    return [
        TrustedValueRecord(
            artifact_id="FINAL_RESULT.json",
            field="R_mean",
            rendered_value="0.004",
            source_hash=sha256,
            label="mean reflectance",
            prose_safe=True,
        )
    ]


def _manifest(sha256: str) -> list[ArtifactDescriptor]:
    return [
        ArtifactDescriptor(
            artifact_id="FINAL_RESULT.json",
            path="FINAL_RESULT.json",
            fields=["R_mean", "worst_case"],
            artifact_type="simulation",
            media_type="application/json",
            content_summary="Verified solver spectrum for the baseline route.",
            field_descriptions={
                "R_mean": "mean reflectance over the declared band",
                "worst_case": "worst-case reflectance",
            },
            sha256=sha256,
            source_experiment_ids=["exp-1"],
            source_observation_ids=["obs-1"],
        )
    ]


def _upstream_shape_manifest(sha256: str) -> list[ArtifactDescriptor]:
    descriptor = _manifest(sha256)[0]
    return [descriptor.model_copy(update={"path": "runs/example/FINAL_RESULT.json"})]


def _story_draft(ledger) -> dict:
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    fact = next(
        f for f in ledger.facts if f.metadata.get("claim_id") == positive.claim_id
    )
    figure = {
        "role_key": "spectrum",
        "kind": "quantitative",
        "story_role": "spectral response",
        "panel_intents": ["panel"],
        "caption_intent": "verified spectrum",
        "claim_bindings": [{"claim_id": positive.claim_id, "role": "positive"}],
        "fact_ids": [fact.fact_id],
        "artifact_bindings": [
            {
                "artifact_id": "FINAL_RESULT.json",
                "selected_fields": ["R_mean", "worst_case"],
            }
        ],
        "limitations": ["solver only"],
    }
    binding = {"claim_id": positive.claim_id, "role": "positive"}
    return {
        "story_shape": "shape-a",
        "central_thesis": "An evidence-bound AR design story.",
        "sections": [
            {
                "heading": "Results",
                "purpose": "present the verified result evidence",
                "key_messages": ["key"],
                "transitions": ["next"],
                "claim_bindings": [binding],
                "figure_roles": ["spectrum"],
            }
        ],
        "figures": [figure],
        "omitted_claims": [],
        "exclusions": ["excluded"],
        "strengths": ["strength"],
        "risks": ["risk"],
        "recommendation_rationale": "rationale",
        "recommendation_score": 0.6,
    }


def _architecture_provider(story_draft: dict):
    def provider(requests):
        return [
            ArchitectureProviderResult(
                stories=[story_draft],
                provider_model="fake-architecture-provider",
                usage={"estimated_input_tokens": 5, "estimated_output_tokens": 5},
            )
        ]

    return provider


def _architecture(plan, ledger, *, manifest):
    result = build_article_architecture(
        plan,
        ledger,
        manifest,
        architecture_provider=_architecture_provider(_story_draft(ledger)),
    )
    assert result.validation_errors == []
    return result, result.stories[0].story_id


def _writer_response(request, *, with_value: bool = True) -> dict:
    claim_aliases = [b["claim_alias"] for b in request["section"]["claim_bindings"]]
    figure_aliases = list(request["section"]["figure_aliases"])
    value_aliases = [item["alias"] for item in request["values"]]
    p1_text = (
        "The verified evidence supports the design claim within the declared " "scope."
    )
    if with_value and value_aliases:
        p1_text = f"{p1_text} [VALUE:{value_aliases[0]}]"
    return {
        "paragraphs": [
            {
                "text_with_value_tokens": p1_text,
                "claim_aliases": claim_aliases,
                "figure_aliases": figure_aliases,
                "paragraph_role": "result",
                "inference_kind": "bounded_inference",
                "inference_note": "local inference from the cited claim",
            }
        ],
        "deferred_claim_aliases": [],
        "author_notes": [],
    }


def _writer(builder: Callable[[dict], dict]):
    def provider(request):
        return WriterProviderResult(
            response=builder(request),
            usage={"estimated_input_tokens": 10, "estimated_output_tokens": 20},
            provider_model="fake-writer",
        )

    return provider


def _reviewer(response: dict):
    def provider(request):
        return ReviewerProviderResult(
            response=response,
            usage={"estimated_input_tokens": 7, "estimated_output_tokens": 9},
            provider_model="fake-reviewer",
        )

    return provider


def _empty_response(request) -> dict:
    return {"findings": [], "advice": []}


def _execution_result(
    observation: ObservationCard,
    run_dir: Path,
    task_hash: str = "task-1",
    receipt: dict | None = None,
) -> ArticleExecutionResult:
    return ArticleExecutionResult(
        request_id=f"req-{observation.observation_id}",
        task_hash=task_hash,
        run_dir=str(run_dir),
        observation=observation,
        receipt=receipt or {},
        outcome=observation.status.value,
    )


def _make_run(
    tmp_path: Path,
    name: str = "run-1",
    content: bytes = RUN_BYTES,
    extra_files: dict[str, bytes] | None = None,
) -> Path:
    run_dir = tmp_path / "runs" / name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "FINAL_RESULT.json").write_bytes(content)
    (run_dir / "TASK.json").write_bytes(TASK_BYTES)
    for filename, payload in (extra_files or {}).items():
        (run_dir / filename).write_bytes(payload)
    return run_dir


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _replay_manifest(
    source_sha: str,
    *,
    task_sha: str = TASK_SHA,
    success: bool = True,
    replay_sha: str | None = None,
    matched: bool = True,
    task_mismatch: bool = False,
    source_run_id: str = "run-1",
    checks: list[ReplayArtifactCheck] | None = None,
    total: int | None = None,
    matched_total: int | None = None,
) -> ReplayManifest:
    check_list = (
        checks
        if checks is not None
        else [
            ReplayArtifactCheck(
                relative_path="FINAL_RESULT.json",
                source_sha256=source_sha,
                replay_sha256=replay_sha if replay_sha is not None else source_sha,
                matched=matched,
                reason="ok" if matched else "content differs",
            )
        ]
    )
    return ReplayManifest(
        source_run_id=source_run_id,
        replay_run_id="run-1-replay",
        source_task_sha256=task_sha,
        replay_task_sha256="task-b" if task_mismatch else task_sha,
        checks=tuple(check_list),
        matched_artifacts=(
            matched_total
            if matched_total is not None
            else sum(1 for item in check_list if item.matched)
        ),
        total_artifacts=total if total is not None else len(check_list),
        success=success,
    )


def _chain(
    tmp_path: Path,
    *,
    file_sha: str | None = None,
    content: bytes = RUN_BYTES,
    manifest_maker: Callable[[str], list[ArtifactDescriptor]] | None = None,
    observation: ObservationCard | None = None,
    receipt: dict | None = None,
    with_value: bool = True,
) -> dict:
    plan, ledger = _ledger()
    sha = file_sha or _sha256_bytes(content)
    run_dir = _make_run(tmp_path, content=content)
    manifest = (manifest_maker or _manifest)(sha)
    architecture, story_id = _architecture(plan, ledger, manifest=manifest)
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(sha) if with_value else [],
        section_writer=_writer(
            lambda request: _writer_response(request, with_value=with_value)
        ),
    )
    review = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(sha) if with_value else [],
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    manuscript = build_article_manuscript(
        plan,
        ledger,
        architecture,
        review,
        story_id,
        _value_records(sha) if with_value else [],
    )
    obs = observation
    if obs is None:
        obs = ObservationCard(
            observation_id="obs-1",
            experiment_id="exp-1",
            status=ExperimentStatus.physically_valid,
            metrics={"route_id": "baseline", "R_mean": 0.004},
            artifact_ids=["FINAL_RESULT.json"],
            hypothesis_updates=[],
            summary="observation",
        )
    execution = _execution_result(obs, run_dir, receipt=receipt)
    return {
        "plan": plan,
        "ledger": ledger,
        "architecture": architecture,
        "review": review,
        "manuscript": manuscript,
        "story_id": story_id,
        "execution": execution,
        "run_dir": run_dir,
        "sha": sha,
    }


def _minimal_manuscript(source_map: list[ParagraphManuscriptSource]):
    body = ArticleManuscriptBody(
        body_id="body-x",
        plan_id="p",
        ledger_id="l",
        architecture_id="a",
        review_id="r",
        result_id="rr",
        story_id="story-01",
        status="assembled",
        sections=[],
        blocked_handoff=[],
        source_map=source_map,
        findings=[],
        warnings=[],
        errors=[],
    )
    return ArticleManuscriptPackage(
        package_id="pkg-x",
        body_id="body-x",
        plan_id="p",
        ledger_id="l",
        architecture_id="a",
        review_id="r",
        result_id="rr",
        story_id="story-01",
        body_markdown="",
        body=body,
        source_map=source_map,
        findings=[],
        blocked_handoff=[],
        warnings=[],
        errors=[],
    )


def test_critical_discovery_and_idempotent_replay(tmp_path) -> None:
    ctx = _chain(tmp_path)
    calls: list[str] = []

    def provider(source_run_dir):
        calls.append(str(source_run_dir))
        return _replay_manifest(ctx["sha"])

    first = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert first.status == "ready"
    assert [item.experiment_id for item in first.critical_experiments] == ["exp-1"]
    assert first.critical_experiments[0].source_run_dir == str(ctx["run_dir"])
    assert len(calls) == 1
    assert first.replay_records[0].status == "completed"
    assert first.lineage[0].identity_kind == "canonical_scientific_identity"

    second = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert second.package_id == first.package_id

    replay_dir = ctx["run_dir"] / "fresh_replay"
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / "FINAL_RESULT.json").write_bytes(RUN_BYTES)
    byte_identical = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert byte_identical.lineage[0].identity_kind == "byte_identical"


def _two_identity_chain(
    tmp_path: Path,
    *,
    descriptor_experiment_ids: list[str],
    source_observation_id: str = "obs-1",
) -> dict:
    plan, ledger = _ledger()
    sha = _sha256_bytes(RUN_BYTES)
    run_dir = _make_run(tmp_path, name="run-two-identity")
    manifest = [
        ArtifactDescriptor(
            artifact_id="FINAL_RESULT.json",
            path="FINAL_RESULT.json",
            fields=["R_mean", "worst_case"],
            sha256=sha,
            source_experiment_ids=list(descriptor_experiment_ids),
            source_observation_ids=[source_observation_id],
        )
    ]
    architecture, story_id = _architecture(plan, ledger, manifest=manifest)
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records(sha),
        section_writer=_writer(
            lambda request: _writer_response(request, with_value=True)
        ),
    )
    review = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records(sha),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    manuscript = build_article_manuscript(
        plan,
        ledger,
        architecture,
        review,
        story_id,
        _value_records(sha),
    )
    observation = ObservationCard(
        observation_id="obs-1",
        experiment_id="experiment-373109e3ae10c387",
        status=ExperimentStatus.physically_valid,
        metrics={
            "route_id": "baseline",
            "R_mean": 0.004,
            "experiments": [
                {
                    "experiment_id": "optimize_absorber_5layer",
                    "baseline_status": "physically_valid",
                }
            ],
        },
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[],
        summary="observation",
    )
    execution = _execution_result(observation, run_dir)
    return {
        "plan": plan,
        "ledger": ledger,
        "architecture": architecture,
        "review": review,
        "manuscript": manuscript,
        "story_id": story_id,
        "execution": execution,
        "run_dir": run_dir,
        "sha": sha,
    }


def test_two_identity_mapping_reaches_replay_and_preserves_both_ids(
    tmp_path,
) -> None:
    ctx = _two_identity_chain(
        tmp_path,
        descriptor_experiment_ids=["optimize_absorber_5layer"],
    )
    calls: list[str] = []

    def provider(source_run_dir):
        calls.append(str(source_run_dir))
        return _replay_manifest(ctx["sha"])

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert package.status == "ready"
    assert [item.experiment_id for item in package.critical_experiments] == [
        "experiment-373109e3ae10c387"
    ]
    assert package.critical_experiments[0].physical_experiment_ids == [
        "optimize_absorber_5layer"
    ]
    assert len(calls) == 1
    assert package.replay_records[0].status == "completed"
    assert package.replay_records[0].experiment_id == ("experiment-373109e3ae10c387")
    assert package.replay_records[0].physical_experiment_ids == [
        "optimize_absorber_5layer"
    ]
    assert not any(
        item.kind in {"source_provenance_mismatch", "missing_execution"}
        for item in package.blockers
    )


def test_artifact_source_can_be_subset_of_nested_physical_experiments(
    tmp_path,
) -> None:
    ctx = _two_identity_chain(
        tmp_path,
        descriptor_experiment_ids=["optimize_absorber_5layer"],
    )
    observation = ctx["execution"].observation
    metrics = dict(observation.metrics)
    metrics["experiments"] = [
        {"experiment_id": "optimize_absorber_5layer"},
        {"experiment_id": "robustness_check_5layer"},
    ]
    execution = ctx["execution"].model_copy(
        update={"observation": observation.model_copy(update={"metrics": metrics})}
    )
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [execution],
        tmp_path / "runs",
        replay_provider=lambda _: _replay_manifest(ctx["sha"]),
    )
    assert package.status == "ready"
    assert not any(
        item.kind == "source_provenance_mismatch" for item in package.blockers
    )


def test_two_identity_forged_or_unknown_source_mapping_blocks(
    tmp_path,
) -> None:
    cases = [
        (
            ["optimize_opaque_absorber"],
            "obs-1",
            "source_provenance_mismatch",
        ),
        (
            ["optimize_absorber_5layer"],
            "obs-ghost",
            "source_provenance_mismatch",
        ),
    ]
    for descriptor_ids, source_obs_id, expected_kind in cases:
        ctx = _two_identity_chain(
            tmp_path,
            descriptor_experiment_ids=descriptor_ids,
            source_observation_id=source_obs_id,
        )
        calls: list[str] = []

        def provider(source_run_dir):
            calls.append(str(source_run_dir))
            return _replay_manifest(ctx["sha"])

        package = build_article_reproducibility(
            ctx["plan"],
            ctx["ledger"],
            ctx["architecture"],
            ctx["review"],
            ctx["manuscript"],
            ctx["story_id"],
            _value_records(ctx["sha"]),
            [ctx["execution"]],
            tmp_path / "runs",
            replay_provider=provider,
        )
        assert any(
            item.kind == expected_kind for item in package.blockers
        ), descriptor_ids
        assert calls == []


def test_volatile_runtime_lock_mismatch_does_not_block(tmp_path) -> None:
    ctx = _chain(tmp_path)
    checks = [
        ReplayArtifactCheck(
            relative_path="FINAL_RESULT.json",
            source_sha256=ctx["sha"],
            replay_sha256=ctx["sha"],
            matched=True,
            reason="ok",
        ),
        ReplayArtifactCheck(
            relative_path="RUNTIME_LOCK.json",
            source_sha256="a" * 64,
            replay_sha256="b" * 64,
            matched=False,
            reason="ephemeral runtime lock changed",
        ),
    ]
    calls: list[str] = []

    def provider(source_run_dir):
        calls.append(str(source_run_dir))
        return _replay_manifest(ctx["sha"], success=False, checks=checks)

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert package.status in {"ready", "ready_with_findings"}
    assert len(calls) == 1
    assert not any(item.kind == "replay_mismatch" for item in package.blockers)
    assert not any("RUNTIME_LOCK" in item.message for item in package.blockers)
    assert any("volatile runtime metadata" in item for item in package.warnings)
    assert all(item.relative_path != "RUNTIME_LOCK.json" for item in package.lineage)
    assert package.replay_records[0].manifest["total_artifacts"] == 2
    assert package.replay_records[0].manifest["matched_artifacts"] == 1

    validation_errors: list[str] = []
    validation_warnings: list[str] = []
    validate_reproducibility_package(
        package,
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        validation_errors,
        validation_warnings,
    )
    assert validation_errors == []


def test_success_false_with_scientific_mismatch_blocks(tmp_path) -> None:
    ctx = _chain(tmp_path)
    checks = [
        ReplayArtifactCheck(
            relative_path="FINAL_RESULT.json",
            source_sha256="a" * 64,
            replay_sha256="b" * 64,
            matched=False,
            reason="content changed",
        )
    ]

    def provider(source_run_dir):
        return _replay_manifest(ctx["sha"], success=False, checks=checks)

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert package.status == "blocked"
    assert any(
        item.kind == "replay_mismatch" and "FINAL_RESULT.json" in item.message
        for item in package.blockers
    )
    assert package.replay_records[0].status == "failed"


def test_scientific_artifact_mismatch_still_blocks(tmp_path) -> None:
    ctx = _chain(tmp_path)
    for relative_path in (
        "FINAL_RESULT.json",
        "TASK.json",
        "x/b/PHYSICS_ACCEPTANCE_CERTIFICATE.json",
        "x/b/OBJECTIVE_REPORT.json",
        "x/c/c_1af3d89019f7/ROBUSTNESS.json",
    ):
        checks = [
            ReplayArtifactCheck(
                relative_path=relative_path,
                source_sha256="a" * 64,
                replay_sha256="b" * 64,
                matched=False,
                reason="content changed",
            )
        ]

        def provider(source_run_dir, path=relative_path):
            return _replay_manifest(ctx["sha"], checks=checks)

        package = build_article_reproducibility(
            ctx["plan"],
            ctx["ledger"],
            ctx["architecture"],
            ctx["review"],
            ctx["manuscript"],
            ctx["story_id"],
            _value_records(ctx["sha"]),
            [ctx["execution"]],
            tmp_path / "runs",
            replay_provider=provider,
        )
        assert any(
            item.kind == "replay_mismatch" and relative_path in item.message
            for item in package.blockers
        ), relative_path


def test_only_manuscript_critical_experiments_replayed(tmp_path) -> None:
    ctx = _chain(tmp_path)
    unrelated_run = _make_run(tmp_path, name="run-unrelated")
    unrelated_observation = ObservationCard(
        observation_id="obs-99",
        experiment_id="exp-99",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "exploration"},
        artifact_ids=[],
        summary="unrelated",
    )
    unrelated = _execution_result(unrelated_observation, unrelated_run)
    calls: list[str] = []

    def provider(source_run_dir):
        calls.append(str(source_run_dir))
        return _replay_manifest(ctx["sha"])

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"], unrelated],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert package.status == "ready"
    assert len(calls) == 1
    assert [item.experiment_id for item in package.critical_experiments] == ["exp-1"]


def test_claim_artifact_fact_and_figure_paths_discover_experiments() -> None:
    plan, ledger = _ledger()
    positive = [
        c for c in ledger.claims if c.status == ClaimStatus.partially_supported
    ][0]
    fact = next(
        f for f in ledger.facts if f.metadata.get("claim_id") == positive.claim_id
    )
    other_descriptor = ArtifactDescriptor(
        artifact_id="OTHER.json",
        path="OTHER.json",
        fields=["x"],
        sha256="b" * 64,
        source_experiment_ids=["exp-2"],
        source_observation_ids=["obs-2"],
    )
    architecture, story_id = _architecture(
        plan,
        ledger,
        manifest=_manifest("a" * 64) + [other_descriptor],
    )
    story = architecture.stories[0]
    claim_only = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p01",
        section_id="story-01-section-01",
        rendered_text="claim path",
        claim_ids=[positive.claim_id],
    )
    fact_only = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p02",
        section_id="story-01-section-01",
        rendered_text="fact path",
        fact_ids=[fact.fact_id],
    )
    artifact_only = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p03",
        section_id="story-01-section-01",
        rendered_text="artifact path",
        artifact_ids=["OTHER.json"],
    )
    figure_only = ParagraphManuscriptSource(
        paragraph_id="story-01-section-01-p04",
        section_id="story-01-section-01",
        rendered_text="figure path",
        figure_ids=[story.figure_contracts[0].figure_id],
    )
    manuscript = _minimal_manuscript(
        [claim_only, fact_only, artifact_only, figure_only]
    )
    observation_by_id = {
        "obs-1": ObservationCard(
            observation_id="obs-1",
            experiment_id="exp-1",
            status=ExperimentStatus.physically_valid,
            metrics={},
            artifact_ids=["FINAL_RESULT.json"],
            summary="",
        ),
        "obs-2": ObservationCard(
            observation_id="obs-2",
            experiment_id="exp-2",
            status=ExperimentStatus.physically_valid,
            metrics={},
            artifact_ids=["OTHER.json"],
            summary="",
        ),
    }
    errors: list[str] = []
    critical = _discover_critical_experiments(
        manuscript=manuscript,
        ledger=ledger,
        architecture=architecture,
        story=story,
        observation_by_id=observation_by_id,
        errors=errors,
    )
    assert errors == []
    by_experiment = {item.experiment_id: item for item in critical}
    assert set(by_experiment) == {"exp-1", "exp-2"}
    assert by_experiment["exp-1"].claim_ids == [positive.claim_id]
    assert by_experiment["exp-1"].fact_ids == [fact.fact_id]
    assert by_experiment["exp-1"].figure_ids == [story.figure_contracts[0].figure_id]
    assert by_experiment["exp-2"].artifact_ids == ["OTHER.json"]


def test_missing_execution_and_non_physical_source_block(tmp_path) -> None:
    ctx = _chain(tmp_path)
    failed_observation = ObservationCard(
        observation_id="obs-failed",
        experiment_id="exp-failed",
        status=ExperimentStatus.failed,
        metrics={"route_id": "exploration"},
        artifact_ids=[],
        failure_records=[{"stage": "solver"}],
        summary="failed run",
    )
    failed_execution = _execution_result(
        failed_observation, _make_run(tmp_path, "run-failed")
    )
    claim = ctx["ledger"].claims[0]
    tampered_claim = claim.model_copy(
        update={"evidence_ids": ["obs-missing", "obs-failed"]}
    )
    tampered_ledger = ctx["ledger"].model_copy(
        update={"claims": [tampered_claim, *ctx["ledger"].claims[1:]]}
    )
    calls: list[str] = []

    def provider(source_run_dir):
        calls.append(str(source_run_dir))
        return _replay_manifest(ctx["sha"])

    package = build_article_reproducibility(
        ctx["plan"],
        tampered_ledger,
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"], failed_execution],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert package.status == "blocked"
    assert any(item.kind == "missing_execution" for item in package.blockers)
    assert any(item.kind == "non_physical_source" for item in package.blockers)
    assert len(calls) == 1


def test_replay_failure_and_mismatch_block(tmp_path) -> None:
    ctx = _chain(tmp_path)
    calls: list[str] = []

    def failing_provider(source_run_dir):
        calls.append(str(source_run_dir))
        raise RuntimeError("replay crashed")

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=failing_provider,
    )
    assert any(item.kind == "replay_failed" for item in package.blockers)

    def mismatched_provider(source_run_dir):
        return _replay_manifest(ctx["sha"], success=False, matched=False)

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=mismatched_provider,
    )
    assert any(item.kind == "replay_mismatch" for item in package.blockers)

    def task_mismatch_provider(source_run_dir):
        return _replay_manifest(ctx["sha"], task_mismatch=True)

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=task_mismatch_provider,
    )
    assert any(item.kind == "task_identity_mismatch" for item in package.blockers)


def test_forged_success_manifests_rejected(tmp_path) -> None:
    ctx = _chain(tmp_path)

    def empty_checks(source_run_dir):
        return _replay_manifest(ctx["sha"], checks=[], total=0, matched_total=0)

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=empty_checks,
    )
    assert any("no artifact checks" in item.message for item in package.blockers)

    def wrong_task_sha(source_run_dir):
        return _replay_manifest(ctx["sha"], task_sha="deadbeef" * 8)

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=wrong_task_sha,
    )
    assert any(item.kind == "source_task_hash_mismatch" for item in package.blockers)

    def wrong_total(source_run_dir):
        return _replay_manifest(ctx["sha"], total=2, matched_total=1)

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=wrong_total,
    )
    assert any("total_artifacts" in item.message for item in package.blockers)

    run_id_ctx = _chain(
        tmp_path,
        content=RUN_BYTES,
        receipt={"run_id": "run-1"},
    )

    def wrong_run_id(source_run_dir):
        return _replay_manifest(run_id_ctx["sha"], source_run_id="run-2")

    package = build_article_reproducibility(
        run_id_ctx["plan"],
        run_id_ctx["ledger"],
        run_id_ctx["architecture"],
        run_id_ctx["review"],
        run_id_ctx["manuscript"],
        run_id_ctx["story_id"],
        _value_records(run_id_ctx["sha"]),
        [run_id_ctx["execution"]],
        tmp_path / "runs",
        replay_provider=wrong_run_id,
    )
    assert any(item.kind == "source_run_id_mismatch" for item in package.blockers)
    assert package.replay_records[0].status == "failed"


def test_path_traversal_and_hash_mismatch_block(tmp_path) -> None:
    ctx = _chain(tmp_path)
    traversal_execution = ctx["execution"].model_copy(
        update={"run_dir": str((tmp_path / ".." / "outside-run").resolve())}
    )
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [traversal_execution],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert any(item.kind == "path_traversal" for item in package.blockers)

    ctx2 = _chain(tmp_path)
    (ctx2["run_dir"] / "FINAL_RESULT.json").write_bytes(b"other-content")
    package = build_article_reproducibility(
        ctx2["plan"],
        ctx2["ledger"],
        ctx2["architecture"],
        ctx2["review"],
        ctx2["manuscript"],
        ctx2["story_id"],
        _value_records(ctx2["sha"]),
        [ctx2["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx2["sha"]),
    )
    assert any(item.kind == "hash_mismatch" for item in package.blockers)


def test_symlink_escape_blocked(tmp_path) -> None:
    ctx = _chain(tmp_path)
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir(parents=True, exist_ok=True)
    (outside_dir / "FINAL_RESULT.json").write_bytes(RUN_BYTES)
    junction = ctx["run_dir"] / "linked"
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside_dir)],
            check=True,
            capture_output=True,
        )
    except OSError:
        pytest.skip("junction creation not permitted in this environment")
    (ctx["run_dir"] / "FINAL_RESULT.json").unlink()
    (junction / "FINAL_RESULT.json").write_bytes(b"outside-bytes")
    descriptor = ctx["architecture"].artifact_inventory[0]
    tampered_descriptor = descriptor.model_copy(
        update={"path": "linked/FINAL_RESULT.json"}
    )
    tampered_manifest = [
        (
            item.model_copy()
            if item.artifact_id != "FINAL_RESULT.json"
            else tampered_descriptor
        )
        for item in ctx["architecture"].artifact_inventory
    ]
    architecture2, story_id2 = _architecture(
        ctx["plan"], ctx["ledger"], manifest=tampered_manifest
    )
    bundle2 = build_article_draft_bundle(
        ctx["plan"],
        ctx["ledger"],
        architecture2,
        story_id2,
        _value_records(ctx["sha"]),
        section_writer=_writer(
            lambda request: _writer_response(request, with_value=True)
        ),
    )
    review2 = build_article_review(
        ctx["plan"],
        ctx["ledger"],
        architecture2,
        bundle2,
        story_id2,
        _value_records(ctx["sha"]),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    manuscript2 = build_article_manuscript(
        ctx["plan"],
        ctx["ledger"],
        architecture2,
        review2,
        story_id2,
        _value_records(ctx["sha"]),
    )
    execution2 = ctx["execution"].model_copy()
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        architecture2,
        review2,
        manuscript2,
        story_id2,
        _value_records(ctx["sha"]),
        [execution2],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(_sha256_bytes(b"outside-bytes")),
    )
    assert package.status == "blocked"
    assert any(
        item.kind in {"missing_source_artifact", "path_traversal", "hash_mismatch"}
        for item in package.blockers
    )


def test_appendix_includes_negative_and_not_run_routes(tmp_path) -> None:
    ctx = _chain(tmp_path)
    rejected = ObservationCard(
        observation_id="obs-neg-1",
        experiment_id="exp-neg-1",
        status=ExperimentStatus.rejected_physics,
        metrics={"route_id": "exploration"},
        artifact_ids=["NEG.json"],
        failure_records=[{"reason": "physics rejected"}],
        summary="rejected by physics",
    )
    cancelled = ObservationCard(
        observation_id="obs-neg-2",
        experiment_id="exp-neg-2",
        status=ExperimentStatus.cancelled,
        metrics={"route_id": "robustness_ablation"},
        artifact_ids=[],
        summary="cancelled",
    )
    negative_executions = [
        _execution_result(rejected, _make_run(tmp_path, "run-neg-1")),
        _execution_result(cancelled, _make_run(tmp_path, "run-neg-2")),
    ]
    plan = ctx["plan"]
    rows = list(plan.coverage_matrix.rows)
    from optomind_optics.harness.article_contracts import CoverageStatus

    rows[1] = rows[1].model_copy(
        update={
            "coverage_status": CoverageStatus.not_run,
            "not_run_reason": "deferred",
        }
    )
    rows[2] = rows[2].model_copy(update={"coverage_status": CoverageStatus.failed})
    tampered_plan = plan.model_copy(
        update={
            "coverage_matrix": plan.coverage_matrix.model_copy(update={"rows": rows})
        }
    )
    package = build_article_reproducibility(
        tampered_plan,
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]] + negative_executions,
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert package.status == "ready"
    kinds = {
        (item.kind, item.observation_id or item.route_id) for item in package.appendix
    }
    assert ("observation", "obs-neg-1") in kinds
    assert ("observation", "obs-neg-2") in kinds
    assert ("coverage_row", "exploration") in kinds
    assert ("coverage_row", "controlled_improvement") in kinds
    observation_count = sum(
        1 for item in package.appendix if item.kind == "observation"
    )
    assert observation_count == 2


def test_forged_manuscript_with_recomputed_ids_rejected(tmp_path) -> None:
    ctx = _chain(tmp_path)
    body = ctx["manuscript"].body
    section = body.sections[0]
    paragraphs = [
        item.model_copy(update={"claim_ids": ["claim-forged", *item.claim_ids]})
        for item in section.paragraphs
    ]
    sections = [section.model_copy(update={"paragraphs": paragraphs})]
    source_map = [
        item.model_copy(update={"claim_ids": ["claim-forged", *item.claim_ids]})
        for item in body.source_map
    ]
    new_body_id = compute_manuscript_body_id(
        ctx["manuscript"].plan_id,
        ctx["manuscript"].ledger_id,
        ctx["manuscript"].architecture_id,
        ctx["manuscript"].review_id,
        ctx["manuscript"].result_id,
        ctx["manuscript"].story_id,
        sections,
        source_map,
        body.findings,
        body.blocked_handoff,
    )
    body2 = body.model_copy(
        update={
            "body_id": new_body_id,
            "sections": sections,
            "source_map": source_map,
        }
    )
    new_package_id = compute_manuscript_package_id(
        new_body_id,
        ctx["manuscript"].body_markdown,
        source_map,
        ctx["manuscript"].findings,
        ctx["manuscript"].blocked_handoff,
        ctx["manuscript"].plan_id,
        ctx["manuscript"].ledger_id,
        ctx["manuscript"].architecture_id,
        ctx["manuscript"].review_id,
        ctx["manuscript"].result_id,
        ctx["manuscript"].story_id,
    )
    forged = ctx["manuscript"].model_copy(
        update={
            "body_id": new_body_id,
            "body": body2,
            "source_map": source_map,
            "package_id": new_package_id,
        }
    )
    calls: list[str] = []

    def provider(source_run_dir):
        calls.append(str(source_run_dir))
        return _replay_manifest(ctx["sha"])

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        forged,
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert package.status == "blocked"
    assert any(
        "does not equal the deterministic rebuild" in item for item in package.errors
    )
    assert calls == []


def test_real_stage6_observation_refs_do_not_block(tmp_path) -> None:
    extra_files = {
        "TASK.json": TASK_BYTES,
        "EXPERIMENT_GRAPH.json": b"{}",
        "RUN_STATE.json": b"{}",
        "EXECUTION_MARKER.json": json.dumps(
            {
                "task_hash": "task-1",
                "request_id": "req-obs-1",
                "run_id": "run-1",
                "status": "completed",
            }
        ).encode(),
        "PHYSICS_ACCEPTANCE_CERTIFICATE.json": b"{}",
    }
    ctx = _chain(
        tmp_path,
        observation=ObservationCard(
            observation_id="obs-1",
            experiment_id="exp-1",
            status=ExperimentStatus.physically_valid,
            metrics={"route_id": "baseline", "R_mean": 0.004},
            artifact_ids=[
                "FINAL_RESULT.json",
                "TASK.json",
                "EXPERIMENT_GRAPH.json",
                "RUN_STATE.json",
                "EXECUTION_MARKER.json",
                "PHYSICS_ACCEPTANCE_CERTIFICATE.json",
            ],
            hypothesis_updates=[],
            summary="observation",
        ),
    )
    run_dir = ctx["run_dir"]
    for filename, payload in extra_files.items():
        (run_dir / filename).write_bytes(payload)
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert package.status == "ready"


def test_upstream_descriptor_path_shape_maps_to_run_local_file(tmp_path) -> None:
    ctx = _chain(
        tmp_path,
        manifest_maker=_upstream_shape_manifest,
    )
    assert ctx["architecture"].artifact_inventory[0].path == (
        "runs/example/FINAL_RESULT.json"
    )
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert package.status == "ready"
    assert package.replay_records[0].status == "completed"


def test_with_limits_replays_and_warns(tmp_path) -> None:
    ctx = _chain(
        tmp_path,
        observation=ObservationCard(
            observation_id="obs-1",
            experiment_id="exp-1",
            status=ExperimentStatus.physically_valid_with_limits,
            metrics={"route_id": "baseline", "R_mean": 0.004},
            artifact_ids=["FINAL_RESULT.json"],
            hypothesis_updates=[],
            summary="observation with limits",
        ),
    )
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert package.status == "ready_with_findings"
    assert any("with limits" in item for item in package.warnings)
    assert package.replay_records[0].status == "completed"


def test_missing_sha_on_manuscript_artifact_is_blocker(tmp_path) -> None:
    plan, ledger = _ledger()
    no_sha_descriptor = ArtifactDescriptor(
        artifact_id="FINAL_RESULT.json",
        path="FINAL_RESULT.json",
        fields=["R_mean", "worst_case"],
        content_summary="no hash",
        source_experiment_ids=["exp-1"],
        source_observation_ids=["obs-1"],
    )
    architecture, story_id = _architecture(plan, ledger, manifest=[no_sha_descriptor])
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        [],
        section_writer=_writer(
            lambda request: _writer_response(request, with_value=False)
        ),
    )
    review = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        [],
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    manuscript = build_article_manuscript(
        plan, ledger, architecture, review, story_id, []
    )
    run_dir = _make_run(tmp_path, content=RUN_BYTES)
    observation = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004},
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[],
        summary="observation",
    )
    execution = _execution_result(observation, run_dir)
    calls: list[str] = []

    def provider(source_run_dir):
        calls.append(str(source_run_dir))
        return _replay_manifest(_sha256_bytes(RUN_BYTES))

    package = build_article_reproducibility(
        plan,
        ledger,
        architecture,
        review,
        manuscript,
        story_id,
        [],
        [execution],
        tmp_path / "runs",
        replay_provider=provider,
    )
    assert package.status == "blocked"
    assert any(item.kind == "missing_hash" for item in package.blockers)
    assert calls == []


def test_nested_telemetry_and_marker_identity(tmp_path) -> None:
    ctx = _chain(
        tmp_path,
        receipt={
            "request_id": "req-obs-1",
            "telemetry": {
                "task_hash": "task-1",
                "request_id": "req-obs-1",
                "run_id": "run-1",
                "run_dir": str(tmp_path / "runs" / "run-1"),
            },
        },
    )
    marker = {
        "task_hash": "task-1",
        "request_id": "req-obs-1",
        "run_id": "run-1",
        "status": "completed",
    }
    (ctx["run_dir"] / "EXECUTION_MARKER.json").write_bytes(json.dumps(marker).encode())
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert package.status == "ready"

    conflicting = ctx["execution"].model_copy(
        update={
            "receipt": {
                "task_hash": "task-2",
                "telemetry": {"task_hash": "task-1"},
            }
        }
    )
    calls: list[str] = []
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [conflicting],
        tmp_path / "runs",
        replay_provider=lambda p: (
            calls.append(str(p)) or _replay_manifest(ctx["sha"])
        ),
    )
    assert any(
        item.kind == "execution_identity_mismatch" and "conflicting" in item.message
        for item in package.blockers
    )
    assert calls == []

    for marker_payload in (
        {
            "task_hash": "task-1",
            "request_id": "req-wrong",
            "run_id": "run-1",
            "status": "completed",
        },
        {
            "task_hash": "task-1",
            "request_id": "req-obs-1",
            "run_id": "run-1",
            "status": "running",
        },
        {
            "task_hash": "task-1",
            "request_id": "req-obs-1",
            "run_id": "run-1",
        },
    ):
        (ctx["run_dir"] / "EXECUTION_MARKER.json").write_bytes(
            json.dumps(marker_payload).encode()
        )
        package = build_article_reproducibility(
            ctx["plan"],
            ctx["ledger"],
            ctx["architecture"],
            ctx["review"],
            ctx["manuscript"],
            ctx["story_id"],
            _value_records(ctx["sha"]),
            [ctx["execution"]],
            tmp_path / "runs",
            replay_provider=lambda p: _replay_manifest(ctx["sha"]),
        )
        assert package.status == "blocked"

    (ctx["run_dir"] / "EXECUTION_MARKER.json").write_bytes(b"not json")
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert any("malformed" in item.message for item in package.blockers)


def test_empty_invalid_manifest_hashes_block(tmp_path) -> None:
    ctx = _chain(tmp_path)

    def empty_replay_run_id(source_run_dir):
        manifest = _replay_manifest(ctx["sha"])
        return manifest.model_copy(update={"replay_run_id": ""})

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=empty_replay_run_id,
    )
    assert any("empty replay_run_id" in item.message for item in package.blockers)

    bad_check = ReplayArtifactCheck(
        relative_path="FINAL_RESULT.json",
        source_sha256="not-hex",
        replay_sha256=ctx["sha"],
        matched=True,
        reason="ok",
    )

    def bad_check_hashes(source_run_dir):
        return _replay_manifest(ctx["sha"], checks=[bad_check])

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=bad_check_hashes,
    )
    assert any("non-64-hex" in item.message for item in package.blockers)

    def bad_task_hash(source_run_dir):
        manifest = _replay_manifest(ctx["sha"])
        return manifest.model_copy(update={"source_task_sha256": "xyz"})

    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=bad_task_hash,
    )
    assert any(
        "source_task_sha256" in item.message and "64-character" in item.message
        for item in package.blockers
    )


def test_provider_not_called_for_cross_wired_critical_source(tmp_path) -> None:
    plan, ledger = _ledger()
    cross_wired_descriptor = ArtifactDescriptor(
        artifact_id="FINAL_RESULT.json",
        path="FINAL_RESULT.json",
        fields=["R_mean", "worst_case"],
        sha256="a" * 64,
        source_experiment_ids=["exp-1"],
        source_observation_ids=["obs-2"],
    )
    architecture, story_id = _architecture(
        plan, ledger, manifest=[cross_wired_descriptor]
    )
    bundle = build_article_draft_bundle(
        plan,
        ledger,
        architecture,
        story_id,
        _value_records("a" * 64),
        section_writer=_writer(
            lambda request: _writer_response(request, with_value=True)
        ),
    )
    review = build_article_review(
        plan,
        ledger,
        architecture,
        bundle,
        story_id,
        _value_records("a" * 64),
        scientific_reviewer=_reviewer(_empty_response),
        expression_reviewer=_reviewer(_empty_response),
    )
    manuscript = build_article_manuscript(
        plan,
        ledger,
        architecture,
        review,
        story_id,
        _value_records("a" * 64),
    )
    run_dir = _make_run(tmp_path, content=RUN_BYTES)
    observation = ObservationCard(
        observation_id="obs-1",
        experiment_id="exp-1",
        status=ExperimentStatus.physically_valid,
        metrics={"route_id": "baseline", "R_mean": 0.004},
        artifact_ids=["FINAL_RESULT.json"],
        hypothesis_updates=[],
        summary="observation",
    )
    obs_2 = ObservationCard(
        observation_id="obs-2",
        experiment_id="exp-2",
        status=ExperimentStatus.physically_valid,
        metrics={},
        artifact_ids=[],
        summary="other",
    )
    execution = _execution_result(observation, run_dir)
    execution_2 = _execution_result(obs_2, _make_run(tmp_path, "run-2"))
    calls: list[str] = []
    package = build_article_reproducibility(
        plan,
        ledger,
        architecture,
        review,
        manuscript,
        story_id,
        _value_records("a" * 64),
        [execution, execution_2],
        tmp_path / "runs",
        replay_provider=lambda p: (calls.append(str(p)) or _replay_manifest("a" * 64)),
    )
    assert any(item.kind == "source_provenance_mismatch" for item in package.blockers)
    assert calls == []


def test_hard_blocker_ids_are_content_addressed(tmp_path) -> None:
    ctx = _chain(tmp_path)
    forged_markdown = ctx["manuscript"].model_copy(
        update={"body_markdown": ctx["manuscript"].body_markdown + " x"}
    )
    first = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        forged_markdown,
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    forged_review = ctx["review"].model_copy(update={"review_id": "review-forged"})
    second = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        forged_review,
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert first.status == "blocked" and second.status == "blocked"
    assert first.package_id != second.package_id


def test_real_relative_telemetry_run_dir_resolves_against_runs_root(
    tmp_path,
) -> None:
    ctx = _chain(tmp_path)
    real_run_dir = tmp_path / "runs" / "branch-a" / "run-abc"
    real_run_dir.mkdir(parents=True, exist_ok=True)
    (real_run_dir / "FINAL_RESULT.json").write_bytes(RUN_BYTES)
    (real_run_dir / "TASK.json").write_bytes(TASK_BYTES)
    (real_run_dir / "EXECUTION_MARKER.json").write_bytes(
        json.dumps(
            {
                "task_hash": "task-1",
                "request_id": "req-obs-1",
                "run_id": "run-1",
                "status": "completed",
            }
        ).encode()
    )
    execution = ctx["execution"].model_copy(
        update={
            "run_dir": str(real_run_dir),
            "receipt": {
                "request_id": "req-obs-1",
                "telemetry": {
                    "task_hash": "task-1",
                    "request_id": "req-obs-1",
                    "run_id": "run-1",
                    "run_dir": "branch-a/run-abc",
                },
            },
        }
    )
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [execution],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert package.status == "ready"
    assert package.replay_records[0].status == "completed"

    escaping = ctx["execution"].model_copy(
        update={
            "run_dir": str(real_run_dir),
            "receipt": {
                "request_id": "req-obs-1",
                "telemetry": {
                    "task_hash": "task-1",
                    "request_id": "req-obs-1",
                    "run_id": "run-1",
                    "run_dir": "../escape",
                },
            },
        }
    )
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [escaping],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert package.status == "blocked"
    assert any(
        item.kind == "execution_identity_mismatch"
        and "resolves outside runs_root" in item.message
        for item in package.blockers
    )

    mismatched = ctx["execution"].model_copy(
        update={
            "run_dir": str(real_run_dir),
            "receipt": {
                "request_id": "req-obs-1",
                "telemetry": {
                    "task_hash": "task-1",
                    "request_id": "req-obs-1",
                    "run_id": "run-1",
                    "run_dir": "branch-a/run-other",
                },
            },
        }
    )
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [mismatched],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    assert any(
        item.kind == "execution_identity_mismatch" and "receipt run_dir" in item.message
        for item in package.blockers
    )


def test_package_id_covers_status_warnings_attempts() -> None:
    base = dict(
        plan_id="p",
        ledger_id="l",
        architecture_id="a",
        review_id="r",
        result_id="rr",
        manuscript_body_id="b",
        story_id="s",
        status="ready",
        critical_experiments=[],
        replay_records=[],
        lineage=[],
        appendix=[],
        blockers=[],
        warnings=[],
        errors=[],
        attempts=0,
    )
    first = compute_reproducibility_package_id(**base)
    assert compute_reproducibility_package_id(**{**base, "warnings": ["w"]}) != first
    assert compute_reproducibility_package_id(**{**base, "status": "blocked"}) != first
    assert compute_reproducibility_package_id(**{**base, "attempts": 1}) != first


def test_atomic_writing_idempotent_and_conflict_rejected(tmp_path) -> None:
    ctx = _chain(tmp_path)
    package = build_article_reproducibility(
        ctx["plan"],
        ctx["ledger"],
        ctx["architecture"],
        ctx["review"],
        ctx["manuscript"],
        ctx["story_id"],
        _value_records(ctx["sha"]),
        [ctx["execution"]],
        tmp_path / "runs",
        replay_provider=lambda p: _replay_manifest(ctx["sha"]),
    )
    out = tmp_path / "out"
    paths = write_reproducibility_package(package, out)
    assert paths["package"].exists()
    assert paths["lineage"].exists()
    assert paths["appendix"].exists()
    write_reproducibility_package(package, out)
    paths["appendix"].write_text(
        paths["appendix"].read_text(encoding="utf-8") + " tampered",
        encoding="utf-8",
    )
    with pytest.raises(ArticleReproducibilityIntegrityError, match="conflicting"):
        write_reproducibility_package(package, out)
    assert paths["package"].exists()
    assert list(out.iterdir())
