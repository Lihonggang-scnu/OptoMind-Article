from __future__ import annotations

from optomind_optics.harness.benchmark_delivery import audit_benchmark_delivery
from optomind_optics.harness.benchmarks import BenchmarkTask
from optomind_optics.harness.design_task import ObjectivePreference
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.orchestrator import TMMHarnessConfig, TMMHarnessOrchestrator


def _benchmark() -> BenchmarkTask:
    return BenchmarkTask.model_validate(
        {
            "id": "SYNTH_DELIVERY",
            "split": "dev",
            "domain": "TMM",
            "title": "Synthetic semantic delivery",
            "natural_language_question": "Compare two reflectance bands at several angles.",
            "task_family": "forward_analysis",
            "capability_axes": ["angle_sweep", "band_preference", "reflection"],
            "expected_artifacts": [
                "multi_angle_spectra.json",
                "thermal_band_preference_report.json",
                "physics_acceptance_certificate.json",
            ],
            "evaluation_contract": {
                "performance_targets": "soft_scores",
                "admission_gate": "deterministic_physics_validity_only",
                "hard_gates": [],
                "statement": "Performance targets are soft scores; deterministic physics validity is the only admission gate.",
            },
        }
    )


def _task():
    source = build_dev_optical_design_task("DEV02")
    experiment = source.experiments[0].model_copy(
        update={
            "objectives": (
                ObjectivePreference(
                    objective_id="preferred_band",
                    metric="mean_reflectance",
                    sense="maximize",
                    region={"wavelength_nm": [500.0, 650.0]},
                ),
                ObjectivePreference(
                    objective_id="suppressed_band",
                    metric="mean_reflectance",
                    sense="minimize",
                    region={"wavelength_nm": [700.0, 850.0]},
                ),
            )
        }
    )
    return source.model_copy(
        update={"benchmark_id": "SYNTH_DELIVERY", "experiments": (experiment,)}
    )


def test_delivery_audit_requires_materialized_objectives_not_only_valid_physics(
    tmp_path,
) -> None:
    task = _task()
    result = TMMHarnessOrchestrator(
        tmp_path,
        run_id="semantic_delivery",
        config=TMMHarnessConfig(enable_global_optimizer=False, use_qwen_policy=False),
    ).run(task)
    assert result.status == "completed"

    audit = audit_benchmark_delivery(_benchmark(), task, tmp_path)
    assert audit.passed is True
    assert all(item.passed for item in audit.checks)

    objective_path = (
        tmp_path
        / "experiments"
        / "dev02_forward_dbr"
        / "baseline"
        / "OBJECTIVE_REPORT.json"
    )
    objective_path.unlink()
    failed = audit_benchmark_delivery(_benchmark(), task, tmp_path)
    assert failed.passed is False
    assert any(
        item.requirement == "band_preference_report" and not item.passed
        for item in failed.checks
    )
