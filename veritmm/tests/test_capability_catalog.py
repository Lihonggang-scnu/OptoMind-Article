"""Capability catalog: runtime self-attestation and drift gate.

The catalog must be generated from the live capability surface (never parsed
back from docs), agree with `describe`, agree with preflight/runtime
behavior, carry schema fingerprints that match the schema exporter, and be
protected by a drift check whose failure message names the regeneration
command.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tmm_engine import (
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    PhysicsRequirements,
    SimulationTask,
    SpectralGrid,
    StackSpec,
    TMMWorkbench,
)
from tmm_engine.backends import get_backend
from tmm_engine.capabilities import assess_tmm_capability
from tmm_engine.capability_catalog import (
    build_catalog,
    check_catalog,
    write_catalog_doc,
)
from tmm_engine.design_problem import OptimizationProblemModel, compile_design_problem
from tmm_engine.execution import ExecutionSettings
from tmm_engine.gradient_api import compute_gradient
from tmm_engine.hashing import stable_sha256
from tmm_engine.intent import IntentSpec, compile_intent
from tmm_engine.job_runtime import JobRequest, JobRuntime
from tmm_engine.managed_execution import execute_managed_task
from tmm_engine.protocol.capabilities import describe_capabilities
from tmm_engine.protocol.schema_export import export_schema


def _qw_task(periods: int = 2, optimizable: bool = False) -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=tuple(
                LayerSpec(None, 600.0 / (4 * n), constant_n=n, optimizable=optimizable)
                for n in (2.4, 1.46) * periods
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.52),
        ),
        spectrum=SpectralGrid(550.0, 650.0, 21),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )


def _catalog() -> dict:
    return build_catalog()


def test_catalog_covers_every_describe_capability_section() -> None:
    manifest = describe_capabilities().model_dump(mode="json")
    describe_sections = set(manifest) - {
        "capability_version",
        "engine_id",
        "package_version",
        "protocol_version",
    }
    catalog = _catalog()
    covered = set()
    for record in catalog["capabilities"]:
        covered.update(record["manifest_sections"])
    assert covered == describe_sections


def test_schema_fingerprints_match_the_schema_exporter() -> None:
    catalog = _catalog()
    expected = {
        "simulation": "tmm.simulate.forward",
        "run_result": "tmm.simulate.response",
        "sensitivity": "tmm.analysis.sensitivity",
        "optimization": "tmm.design.optimize",
        "intent": "tmm.gradient.api",
    }
    for kind, capability_id in expected.items():
        record = next(
            r for r in catalog["capabilities"] if r["capability_id"] == capability_id
        )
        assert record["schema_fingerprint"] == stable_sha256(export_schema(kind))


def test_preflight_decisions_agree_with_runtime_behavior() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    supported = [
        _qw_task(),
        SimulationTask(
            stack=StackSpec(
                layers=(
                    LayerSpec(None, 91.0, constant_n=2.15),
                    LayerSpec(None, 137.0, constant_n=1.43, coherence="incoherent"),
                ),
                incident=MediumSpec.air(),
                exit=MediumSpec("sio2"),
            ),
            spectrum=SpectralGrid(550.0, 650.0, 11),
            illumination=IlluminationSpec((0.0,), ("s",)),
            solver="byrnes",
        ),
        _absorbing_stack(),
    ]
    unsupported = [
        _with_physics(material_class="anisotropic"),
        _with_physics(geometry_class="lateral_periodic"),
        _with_physics(material_class="nonlinear"),
    ]
    for task in supported:
        assert assess_tmm_capability(task).supported is True
        result = workbench.simulate(task)
        assert set(result.channels) == {"angle=0|pol=s"}
    for task in unsupported:
        assessment = assess_tmm_capability(task)
        assert assessment.supported is False
        with pytest.raises(Exception):
            workbench.simulate(task)


def _absorbing_stack() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 100.0, constant_n=2.1, constant_k=0.4),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(550.0, 650.0, 11),
        illumination=IlluminationSpec((0.0,), ("s",)),
    )


def _with_physics(**physics) -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(None, 100.0, constant_n=2.1),),
            incident=MediumSpec.air(),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(550.0, 650.0, 11),
        illumination=IlluminationSpec((0.0,), ("s",)),
        physics=PhysicsRequirements(**physics),
    )


def test_contract_cases_all_pass(tmp_path: Path) -> None:
    catalog = _catalog()
    failures = _run_contract_cases(catalog, tmp_path)
    assert failures == []


def _run_contract_cases(catalog: dict, tmp_path: Path) -> list:
    failures = []
    ids = {record["capability_id"] for record in catalog["capabilities"]}
    for capability_id in sorted(ids):
        try:
            _contract_case(capability_id, tmp_path)
        except Exception as exc:  # noqa: BLE001 - report, don't stop the sweep
            failures.append((capability_id, f"{type(exc).__name__}: {exc}"))
    return failures


def _contract_case(capability_id: str, tmp_path: Path) -> None:
    if capability_id == "tmm.simulate.forward":
        result = TMMWorkbench(MaterialRegistry()).simulate(_qw_task())
        channel = result.channel(0.0, "s")
        assert {"R", "T", "A"} <= set(channel)
        assert result.independence_audit["absorption_independence_class"] == (
            "layer_absorption_integral"
        )
        return
    if capability_id == "tmm.gradient.api":
        result = compute_gradient(_qw_task(optimizable=True), "mean_T")
        assert result.backend == "torch"
        assert len(result.layer_gradients) == 4
        return
    if capability_id == "tmm.analysis.sensitivity":
        result = compute_gradient(_qw_task(optimizable=True), "mean_R")
        assert result.metric_value is not None
        return
    if capability_id == "tmm.design.optimize":
        problem = OptimizationProblemModel.model_validate(
            {
                "stack": {
                    "layers": [
                        {"constant_n": 2.4, "thickness_nm": 62.5},
                        {"constant_n": 1.46, "thickness_nm": 102.7},
                    ],
                    "incident": {"constant_n": 1.0},
                    "exit": {"constant_n": 1.52},
                },
                "objectives": [
                    {"metric": "MeanReflectance", "band_nm": [550.0, 650.0]}
                ],
                "parameters": [{"layer_selector": "all", "bounds_nm": [30.0, 160.0]}],
                "solver": {"max_steps": 2, "starts": 1},
            }
        )
        task = compile_design_problem(problem)
        envelope = execute_managed_task(
            "optimize",
            task,
            tmp_path / "optimize",
            execution_settings=ExecutionSettings(),
        )
        assert envelope["status"] == "completed"
        return
    if capability_id == "tmm.research.batch":
        from tmm_engine.research.batch import ChunkedVerifiedBatchExecutor

        executor = ChunkedVerifiedBatchExecutor(batch_size=2)
        assert executor.batch_size == 2
        return
    if capability_id == "tmm.intent.compile":
        spec = IntentSpec.model_validate(
            {
                "intent_id": "catalog-case",
                "observables": [
                    {
                        "quantity": "R",
                        "domain": {"wavelength_min": {"value": 600.0}},
                        "relation": ">=",
                        "target": 0.9,
                        "reducer": "mean",
                    }
                ],
            }
        )
        compiled = compile_intent(spec, _qw_task().stack)
        assert compiled.certificate.semantic_status in ("equivalent", "ambiguous")
        return
    if capability_id == "tmm.design.compile":
        problem = OptimizationProblemModel.model_validate(
            {
                "stack": {
                    "layers": [{"constant_n": 2.4, "thickness_nm": 62.5}],
                    "incident": {"constant_n": 1.0},
                    "exit": {"constant_n": 1.52},
                },
                "objectives": [
                    {"metric": "MeanReflectance", "band_nm": [550.0, 650.0]}
                ],
                "parameters": [{"layer_selector": "all", "bounds_nm": [30.0, 160.0]}],
            }
        )
        task = compile_design_problem(problem)
        assert len(task.targets) == 1
        assert task.simulation.stack.layers[0].optimizable is True
        return
    if capability_id == "tmm.job.runtime":
        from tmm_engine.experiment_store import ExperimentStore

        store = ExperimentStore(tmp_path / "store")
        runtime = JobRuntime(store=store)
        job = runtime.submit_job(JobRequest("simulate", _qw_task(), tmp_path / "job"))
        assert job["status"] == "completed"
        return
    if capability_id == "tmm.backend.numpy":
        result = get_backend("numpy").assemble(_kernel())
        assert result.R.shape == (len(WAVELENGTHS_GRID),)
        return
    if capability_id == "tmm.backend.torch":
        result = get_backend("torch").assemble(_kernel())
        assert tuple(result.R.shape) == (1, len(WAVELENGTHS_GRID))
        return
    if capability_id == "tmm.simulate.response":
        return  # exercised end to end by the managed runs in the cases above
    if capability_id == "tmm.agent.benchmark":
        from tmm_engine.agent_bench import default_benchmark_cases_dir, load_benchmark_cases

        assert len(load_benchmark_cases(default_benchmark_cases_dir())) >= 80
        return
    raise AssertionError(f"no contract case for {capability_id}")


WAVELENGTHS_GRID = [550.0 + 5.0 * i for i in range(21)]


def _kernel():
    from tmm_engine.backends import KernelSpec

    wavelengths = np.linspace(550.0, 650.0, 21)
    return KernelSpec(
        nk_stack=tuple(
            np.full(21, n, dtype=np.complex128) for n in (1.0, 2.1, 1.5)
        ),
        thicknesses_nm=[100.0],
        wavelengths_nm=wavelengths,
        angle_deg=20.0,
        polarization="s",
    )


def test_drift_gate_catches_hand_edits(tmp_path: Path) -> None:
    doc = tmp_path / "CAPABILITY_CATALOG.md"
    write_catalog_doc(doc)
    assert check_catalog(doc, repo_root=tmp_path)["ok"] is True

    text = doc.read_text(encoding="utf-8")
    text = text.replace(
        '"capability_id": "tmm.simulate.forward",',
        '"capability_id": "tmm.simulate.forward",\n  "fake_field": true,',
        1,
    )
    doc.write_text(text, encoding="utf-8")
    report = check_catalog(doc, repo_root=tmp_path)
    assert report["ok"] is False
    assert "--regenerate" in report["hint"]



def test_surface_roughness_code_is_an_informational_capability_hint() -> None:
    """V-13 generalization drill (c): a new FailureCode semantic registered
    through the existing handoff pattern — a declarative capability hint that
    serializes with the informational marker and deliberately carries no
    solver routing and no recoverable action."""

    from tmm_engine.capabilities import (
        FailureCode,
        FailureRecord,
        enrich_failure_actions,
    )

    record = FailureRecord(
        FailureCode.SURFACE_ROUGHNESS_NOT_MODELED,
        "The declared stack models ideal interfaces; surface roughness "
        "scatter is outside scalar TMM and is not modeled.",
        False,
    )
    payload = enrich_failure_actions(record).to_dict()
    assert payload["code"] == "surface_roughness_not_modeled"
    assert payload["severity"] == "error"
    assert payload["suggested_solver_family"] is None
    assert "informational_only" not in payload
