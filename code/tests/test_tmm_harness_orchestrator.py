from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from optomind_optics.harness.article_proposals import (
    compute_optical_design_task_digest,
)
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.orchestrator import (
    TMMHarnessConfig,
    TMMHarnessOrchestrator,
    _compact_candidate_id,
    _stable_hash,
    _summarize_diagnoses,
)
from optomind_optics.harness.replay import replay_completed_run
from tmm_engine.capabilities import CapabilityAssessment, FailureCode, FailureRecord


def test_long_candidate_ids_are_compacted_stably_for_windows_artifact_paths() -> None:
    original = "very_long_experiment_identifier__differential_evolution_thickness__01"
    compact = _compact_candidate_id(original)

    assert len(compact) <= 48
    assert compact == _compact_candidate_id(original)
    assert compact != original


def test_deep_run_root_uses_short_reversible_artifact_directories(tmp_path) -> None:
    deep_root = tmp_path / ("audit_segment_" + "x" * 44)
    orchestrator = TMMHarnessOrchestrator(
        deep_root,
        run_id="deep_path_policy",
    )
    experiment_id = "long_multicondition_experiment_identifier"
    candidate_id = "long_optimizer_candidate_identifier_with_audit_suffix"

    experiment_dir = orchestrator._experiment_directory(experiment_id)
    candidate_dir = orchestrator._candidate_directory(
        experiment_id,
        candidate_id,
    )

    assert experiment_dir.parent.name == "x"
    assert experiment_dir.name.startswith("e_")
    assert candidate_dir.name.startswith("c_")
    assert len(str((candidate_dir / "MATERIAL_DATASET_UNCERTAINTY.json").resolve())) < 240


def test_forward_dev_task_runs_without_qwen_and_writes_verified_portfolio(tmp_path) -> None:
    task = build_dev_optical_design_task("DEV02")
    config = TMMHarnessConfig(enable_global_optimizer=False, use_qwen_policy=False)
    result = TMMHarnessOrchestrator(tmp_path, run_id="dev02_run", config=config).run(task)
    assert result.status == "completed"
    assert result.state_stage == "completed"
    assert result.qwen_usage == ()
    assert result.experiment_results[0]["physically_valid_candidate_count"] == 1
    assert (tmp_path / "experiments" / "dev02_forward_dbr" / "DESIGN_PORTFOLIO.json").exists()
    objective_path = (
        tmp_path
        / "experiments"
        / "dev02_forward_dbr"
        / "baseline"
        / "OBJECTIVE_REPORT.json"
    )
    assert objective_path.exists()
    objective = json.loads(objective_path.read_text(encoding="utf-8"))
    assert "report_stopband" in objective["target_attainment"]
    assert json.loads((tmp_path / "FINAL_RESULT.json").read_text(encoding="utf-8"))["status"] == "completed"
    cost = json.loads((tmp_path / "COST.json").read_text(encoding="utf-8"))
    assert cost["forward_evaluations"] == 5
    assert cost["qwen_calls"] == 0
    assert cost["qwen_model_constraint"] == "qwen3.7-flash"
    events = [
        json.loads(line)
        for line in (tmp_path / "EVENTS.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [item["sequence"] for item in events] == list(range(1, len(events) + 1))
    assert events[-1]["stage"] == "completed"
    runtime_lock = json.loads(
        (tmp_path / "RUNTIME_LOCK.json").read_text(encoding="utf-8")
    )
    assert runtime_lock["qwen_policy"]["model_fallback_allowed"] is False


def test_unicode_task_manifest_digest_matches_canonical_utf8_digest(tmp_path) -> None:
    task = build_dev_optical_design_task("DEV02")
    task = task.model_copy(
        update={
            "user_request_original": (
                "计算在 8–13 µm 波段的反射率，θ 角 45°，中文注释。 "
                + task.user_request_original
            ),
            "normalized_request_english": (
                "Evaluate the 8–13 µm window with θ incidence and λ-dependent "
                "dispersion. " + task.normalized_request_english
            ),
            "metadata": {
                **task.metadata,
                "unicode_probe": "µm θ λ 中文",
            },
        }
    )
    config = TMMHarnessConfig(enable_global_optimizer=False, use_qwen_policy=False)
    result = TMMHarnessOrchestrator(
        tmp_path, run_id="unicode_digest_run", config=config
    ).run(task)

    assert result.status == "completed"
    manifest = json.loads(
        (tmp_path / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8")
    )
    task_record = next(
        item
        for item in manifest["artifacts"]
        if item["artifact_id"] == "TASK.json"
    )
    expected = compute_optical_design_task_digest(task)
    assert task_record["scientific_provenance"]["task_sha256"] == expected
    assert _stable_hash(task.model_dump(mode="json")) == expected


def test_ascii_task_digest_matches_legacy_ascii_serialization() -> None:
    task = build_dev_optical_design_task("DEV02")
    payload = task.model_dump(mode="json")
    legacy = hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()

    assert _stable_hash(payload) == legacy
    assert _stable_hash(payload) == compute_optical_design_task_digest(task)


def test_completed_run_can_resume_without_repeating_physics(tmp_path) -> None:
    task = build_dev_optical_design_task("DEV05")
    config = TMMHarnessConfig(enable_global_optimizer=False)
    first = TMMHarnessOrchestrator(tmp_path, run_id="resume_run", config=config).run(task)
    budget_before = json.loads((tmp_path / "BUDGET.json").read_text(encoding="utf-8"))
    second = TMMHarnessOrchestrator(tmp_path, run_id="resume_run", resume=True, config=config).run(task)
    budget_after = json.loads((tmp_path / "BUDGET.json").read_text(encoding="utf-8"))
    assert second == first
    assert budget_after == budget_before


def test_candidate_clustering_only_removes_numerical_clones() -> None:
    rows = [
        {"candidate_id": "best", "objective_loss": 0.1, "thicknesses_nm": [100.0]},
        {"candidate_id": "clone", "objective_loss": 0.100001, "thicknesses_nm": [100.01]},
        {"candidate_id": "distinct", "objective_loss": 0.2, "thicknesses_nm": [110.0]},
    ]
    selected = TMMHarnessOrchestrator._deduplicate_candidates(
        rows,
        8,
        spans_nm=[100.0],
        minimum_normalized_separation=0.002,
    )
    assert [item["candidate_id"] for item in selected] == ["best", "distinct"]


def test_relative_work_directory_registers_lineage_without_double_prefix(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    task = build_dev_optical_design_task("DEV02")
    relative_work_dir = Path("relative_outputs") / "dev02"
    result = TMMHarnessOrchestrator(
        relative_work_dir,
        run_id="relative_dev02",
        config=TMMHarnessConfig(enable_global_optimizer=False),
    ).run(task)
    assert result.status == "completed"
    manifest = json.loads(
        (relative_work_dir / "ARTIFACT_MANIFEST.json").read_text(encoding="utf-8")
    )
    paths = {item["relative_path"] for item in manifest["artifacts"]}
    assert "TASK.json" in paths
    assert not any("relative_outputs/dev02/relative_outputs" in path for path in paths)


def test_optimizer_baseline_duplicates_are_not_reverified(tmp_path) -> None:
    task = build_dev_optical_design_task("DEV01")
    result = TMMHarnessOrchestrator(
        tmp_path,
        run_id="dedupe_baseline",
        config=TMMHarnessConfig(enable_global_optimizer=False),
    ).run(task)
    candidates = result.experiment_results[0]["portfolio"]["candidates"]
    thicknesses = [tuple(item["metadata"]["thicknesses_nm"]) for item in candidates]
    assert thicknesses.count((110.0,)) == 1


def test_budget_exhaustion_fails_closed_without_crashing_or_losing_diagnosis(
    tmp_path,
) -> None:
    task = build_dev_optical_design_task("DEV02")
    task = task.model_copy(
        update={
            "budget": task.budget.model_copy(
                update={"maximum_forward_evaluations": 1}
            )
        }
    )

    result = TMMHarnessOrchestrator(
        tmp_path,
        run_id="budget_stop",
        config=TMMHarnessConfig(enable_global_optimizer=False),
    ).run(task)

    assert result.status == "budget_exhausted"
    assert result.state_stage == "failed"
    diagnoses = json.loads(
        (tmp_path / "FAILURE_DIAGNOSES.json").read_text(encoding="utf-8")
    )
    assert diagnoses["records"][0]["failure"]["code"] == "budget_exhausted"
    assert json.loads(
        (tmp_path / "FINAL_RESULT.json").read_text(encoding="utf-8")
    )["status"] == "budget_exhausted"


def test_unsupported_physics_stops_with_machine_readable_handoff(
    tmp_path, monkeypatch
) -> None:
    task = build_dev_optical_design_task("DEV02")
    orchestrator = TMMHarnessOrchestrator(
        tmp_path,
        run_id="unsupported_geometry",
        config=TMMHarnessConfig(enable_global_optimizer=False),
    )
    monkeypatch.setattr(
        orchestrator.solver_adapter,
        "assess",
        lambda _task: CapabilityAssessment(
            engine_id="optomind_tmm",
            supported=False,
            resolved_solver=None,
            failures=(
                FailureRecord(
                    FailureCode.UNSUPPORTED_GEOMETRY,
                    "Lateral periodic geometry is outside scalar TMM.",
                    True,
                    "rcwa",
                ),
            ),
        ),
    )

    result = orchestrator.run(task)

    assert result.status == "needs_higher_fidelity"
    assert result.state_stage == "needs_higher_fidelity"
    diagnoses = json.loads(
        (tmp_path / "FAILURE_DIAGNOSES.json").read_text(encoding="utf-8")
    )
    assert diagnoses["records"][0]["failure"]["code"] == "unsupported_geometry"
    assert diagnoses["records"][0]["diagnosis"]["allowed_actions"] == ["stop"]
    assert not (tmp_path / "experiments" / "dev02_forward_dbr" / "baseline").exists()


def test_primary_optimizer_failure_recovers_with_registered_global_optimizer(
    tmp_path, monkeypatch
) -> None:
    task = build_dev_optical_design_task("DEV01")
    orchestrator = TMMHarnessOrchestrator(
        tmp_path,
        run_id="optimizer_recovery",
        config=TMMHarnessConfig(enable_global_optimizer=True),
    )
    original_select = orchestrator.optimizers.select

    class BrokenPrimary:
        descriptor = SimpleNamespace(optimizer_id="broken_primary")

        @staticmethod
        def optimize(_task):
            raise RuntimeError("synthetic optimizer failure")

    def select_with_failure(optimization, *, purpose="primary"):
        if purpose == "primary":
            return BrokenPrimary()
        return original_select(optimization, purpose=purpose)

    monkeypatch.setattr(orchestrator.optimizers, "select", select_with_failure)

    result = orchestrator.run(task)

    assert result.status == "completed"
    diagnoses = json.loads(
        (tmp_path / "FAILURE_DIAGNOSES.json").read_text(encoding="utf-8")
    )
    assert any(
        item["failure"]["code"] == "optimizer_failure"
        for item in diagnoses["records"]
    )
    sources = {
        item["metadata"]["optimizer_id"]
        for item in result.experiment_results[0]["portfolio"]["candidates"]
        if item["metadata"].get("optimizer_id")
    }
    assert sources
    assert "broken_primary" not in sources


def test_abrupt_interruption_recovers_in_isolated_attempt_with_remaining_budget(
    tmp_path, monkeypatch
) -> None:
    task = build_dev_optical_design_task("DEV02")
    first = TMMHarnessOrchestrator(
        tmp_path,
        run_id="interrupted_run",
        config=TMMHarnessConfig(enable_global_optimizer=False),
    )
    original_advance = first._advance

    def interrupt_after_materials(stage, reason, details):
        original_advance(stage, reason, details)
        if stage.value == "materials_resolved":
            raise KeyboardInterrupt("synthetic abrupt process interruption")

    monkeypatch.setattr(first, "_advance", interrupt_after_materials)
    with pytest.raises(KeyboardInterrupt):
        first.run(task)

    resumed = TMMHarnessOrchestrator(
        tmp_path,
        run_id="interrupted_run",
        resume=True,
        config=TMMHarnessConfig(enable_global_optimizer=False),
    ).run(task)

    assert resumed.status == "completed_after_interruption_recovery"
    assert resumed.state_stage == "completed"
    report = json.loads((tmp_path / "RECOVERY_REPORT.json").read_text(encoding="utf-8"))
    assert report["source_stage"] == "materials_resolved"
    assert report["strategy"] == "isolated_deterministic_restart_with_remaining_budget"
    assert report["qwen_disabled_in_recovery"] is True
    assert report["child_status"] == "completed"
    assert (tmp_path / "recovery_attempts" / "attempt_001" / "FINAL_RESULT.json").exists()
    assert resumed.budget["usage"]["forward_evaluations"] == 5
    assert resumed.budget["overrun"] is False
    replay = replay_completed_run(tmp_path)
    assert replay.success is True
    assert replay.scientific_source_relative == "recovery_attempts/attempt_001"


def test_run_result_carries_the_engine_diagnoses_the_research_loop_reads() -> None:
    # FAILURE_DIAGNOSES.json keeps every record, but the outer research loop
    # reads the run result and branches purely on category.  Leaving this
    # empty hid nine passivity failures and nine solver disagreements behind
    # the stop reason, so the loop classified a physics outcome as an
    # environment fault and never took its physics branch.
    records = [
        {
            "sequence": index + 1,
            "stage": "baseline_evaluated" if index == 0 else "candidate_verification",
            "failure": {"code": code},
            "diagnosis": {
                "category": category,
                "recoverable_with_tmm": True,
                "explanation": "explanation",
            },
        }
        for index, (code, category) in enumerate(
            [("passivity_violation", "physics_violation")] * 9
            + [("solver_disagreement", "solver_disagreement")] * 9
        )
    ]

    summary = {entry["category"]: entry for entry in _summarize_diagnoses(records)}

    assert set(summary) == {"physics_violation", "solver_disagreement"}
    assert summary["physics_violation"]["occurrences"] == 9
    assert summary["solver_disagreement"]["occurrences"] == 9
    assert summary["physics_violation"]["first_failure_code"] == "passivity_violation"
    assert summary["physics_violation"]["first_stage"] == "baseline_evaluated"


def test_summarized_diagnoses_skip_records_without_a_category() -> None:
    assert _summarize_diagnoses([{"sequence": 1, "failure": {"code": "x"}}]) == ()
