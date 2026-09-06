"""Certificate energy ledger, verifier dependency graph, and cross-solver split.

V-02 promotes the V-01 independent absorption accounting into the acceptance
decision and the certificate: the energy verdict consumes two recorded
residuals (closure and independent) and the certificate carries an
``energy_accounting`` block, so "which quantities the verifier used — and
which it is forbidden to use" is a machine-readable contract.  The tests also
pin the failure that the cross-check exists to catch: a multi-layer internal
absorption integral that disagrees with an independent implementation.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import numpy as np
import pytest

from tmm_engine import (
    AcceptanceSettings,
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
    TMMWorkbench,
    certify_simulation,
)
from tmm_engine.acceptance import (
    ENERGY_FALLBACK_REASON_PRE_LEDGER,
    ENERGY_FALLBACK_REASON_UNAVAILABLE,
    VERIFIER_DEPENDENCY_GRAPH,
    collect_verification_evidence,
    evaluate_evidence,
)

ENGINE_ROOT = Path(__file__).resolve().parents[1] / "tmm_engine"
ACCEPTANCE_MODULE = ENGINE_ROOT / "acceptance.py"


def _absorbing_task() -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec("tio2", 30.0),
                LayerSpec(None, 60.0, constant_n=1.8, constant_k=0.4),
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec("sio2"),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 21),
        illumination=IlluminationSpec((0.0,), ("s", "p")),
    )


def _energy_failure(certificate: dict) -> dict:
    return next(
        item
        for item in certificate["failures"]
        if item["code"] == "energy_conservation_failure"
    )


def test_certificate_carries_energy_accounting_block() -> None:
    certificate = certify_simulation(
        TMMWorkbench(MaterialRegistry()), _absorbing_task()
    ).certificate

    block = certificate["energy_accounting"]
    assert block["method"] == "layer_absorption_integral"
    assert block["independence_class"] == "layer_absorption_integral"
    assert "fallback_reason" not in block
    assert block["residual_closure"] < 1e-7
    assert block["residual_independent"] < 1e-7
    for name in ("R", "T", "A_closure", "A_independent"):
        summary = block[name]
        assert 0.0 <= summary["maximum"] <= 1.0
        assert summary["channel"] in {"angle=0|pol=s", "angle=0|pol=p"}
        assert 500.0 <= summary["wavelength_nm"] <= 700.0
    assert set(block["worst_case"]) == {
        "closure_channel",
        "closure_wavelength_nm",
        "independent_channel",
        "independent_wavelength_nm",
    }


def test_injected_absorption_is_rejected_by_the_energy_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = TMMWorkbench._layer_absorption_integrals

    def inflated(*args, **kwargs):
        return original(*args, **kwargs) * 1.05

    monkeypatch.setattr(TMMWorkbench, "_layer_absorption_integrals", staticmethod(inflated))
    certificate = certify_simulation(
        TMMWorkbench(MaterialRegistry()), _absorbing_task()
    ).certificate

    # The V-01 audit metric alone only flagged the deviation; the acceptance
    # decision must now actually reject on the independent residual while the
    # closure ledger stays blind (it is algebraically closed by construction).
    assert certificate["accepted"] is False
    assert certificate["status"] == "rejected_physics"
    failure = _energy_failure(certificate)
    assert failure["context"]["triggered_by"] == "independent"
    assert failure["context"]["tolerance"] == AcceptanceSettings().energy_tolerance
    assert (
        certificate["physics_audit"]["energy_conservation_max_abs_error"] < 1e-12
    )


def test_unavailable_independence_degrades_explicitly() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    evidence, _ = collect_verification_evidence(
        workbench, _absorbing_task(), AcceptanceSettings()
    )
    degraded = dataclasses.replace(
        evidence,
        energy_accounting={
            "independence": {
                "energy_independent_max_abs_error": None,
                "energy_independent_worst_channel": None,
                "energy_independent_worst_wavelength_nm": None,
                "absorption_independence_class": "unavailable",
            },
            "observables": {},
        },
    )
    certificate = evaluate_evidence(degraded, AcceptanceSettings())

    block = certificate["energy_accounting"]
    assert block["independence_class"] == "derived_closure_fallback"
    assert block["fallback_reason"] == ENERGY_FALLBACK_REASON_UNAVAILABLE
    assert block["residual_independent"] is None
    # Closure-only judgement of the healthy run still accepts.
    assert certificate["accepted"] is True


def test_pre_ledger_evidence_is_judged_on_the_closure_alone() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    evidence, _ = collect_verification_evidence(
        workbench, _absorbing_task(), AcceptanceSettings()
    )
    legacy = dataclasses.replace(evidence, energy_accounting=None)
    certificate = evaluate_evidence(legacy, AcceptanceSettings())

    block = certificate["energy_accounting"]
    assert block["independence_class"] == "derived_closure_fallback"
    assert block["fallback_reason"] == ENERGY_FALLBACK_REASON_PRE_LEDGER
    assert certificate["accepted"] is True


def test_pre_ledger_energy_failure_keeps_the_historical_verdict() -> None:
    workbench = TMMWorkbench(MaterialRegistry())
    evidence, _ = collect_verification_evidence(
        workbench, _absorbing_task(), AcceptanceSettings(energy_tolerance=0.0)
    )
    legacy = dataclasses.replace(evidence, energy_accounting=None)
    certificate = evaluate_evidence(legacy, AcceptanceSettings(energy_tolerance=0.0))

    assert certificate["accepted"] is False
    assert _energy_failure(certificate)["context"]["ledger"] == "closure_only"


def test_injected_absorption_cannot_hide_behind_a_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong integral with a *passing* closure is caught; with an
    *unavailable* integral the fallback must not silently re-accept a bad
    closure either — the closure verdict is what it is, but the fallback is
    always recorded."""

    original = TMMWorkbench._layer_absorption_integrals

    def inverted(*args, **kwargs):
        return -original(*args, **kwargs)

    monkeypatch.setattr(TMMWorkbench, "_layer_absorption_integrals", staticmethod(inverted))
    certificate = certify_simulation(
        TMMWorkbench(MaterialRegistry()), _absorbing_task()
    ).certificate
    assert certificate["accepted"] is False
    assert _energy_failure(certificate)["context"]["triggered_by"] == "independent"


def test_verifier_dependency_graph_declares_the_energy_check() -> None:
    assert VERIFIER_DEPENDENCY_GRAPH["energy_conservation"] == {
        "depends_on": [
            "solver.R",
            "solver.T",
            "independent_absorption.layer_integral",
        ],
        "forbidden_dependency": ["A_closure"],
    }
    assert (
        "A_closure" in VERIFIER_DEPENDENCY_GRAPH["cross_solver_agreement"]["forbidden_dependency"]
    )


def test_energy_decision_path_never_rebuilds_absorption_from_r_and_t() -> None:
    """AST gate: ``evaluate_evidence`` must consume recorded residuals only.

    The check depends on the recorded ledger (``evidence.energy_accounting``)
    and never performs subtraction arithmetic — rebuilding ``A`` from ``R`` and
    ``T`` inside the decision would make the energy identity tautological,
    which is exactly the defect this verification layer removes.
    """

    tree = ast.parse(ACCEPTANCE_MODULE.read_text(encoding="utf-8"))
    evaluate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "evaluate_evidence"
    )
    subtractions = [
        node.lineno
        for node in ast.walk(evaluate)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub)
    ]
    assert not subtractions, (
        "evaluate_evidence performs subtraction arithmetic; the energy verdict "
        f"must judge recorded residuals, not rebuild them: lines {subtractions}"
    )
    consumed = any(
        isinstance(node, ast.Attribute) and node.attr == "energy_accounting"
        for node in ast.walk(evaluate)
    )
    assert consumed, "evaluate_evidence must consume the energy_accounting ledger"


def test_cross_solver_report_splits_the_ledgers() -> None:
    certificate = certify_simulation(
        TMMWorkbench(MaterialRegistry()), _absorbing_task()
    ).certificate
    check = certificate["independent_solver_check"]

    assert check["status"] == "passed"
    assert set(check["metrics"]) == {"R", "T", "A_independent", "energy_accounting"}
    for name in ("R", "T", "A_independent", "energy_accounting"):
        metric = check["metrics"][name]
        assert metric["status"] == "passed"
        assert metric["maximum_absolute_difference"] <= metric["tolerance"]
    assert check["offending_observable"] in {"R", "T", "A_independent", "energy_accounting"}


def test_reference_side_absorption_fault_raises_solver_disagreement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The primary (internal) ledger is correct; inflating the Byrnes
    reference's independent absorption must fail the *A_independent* agreement
    and reject the run through SOLVER_DISAGREEMENT — not through energy."""

    import tmm as byrnes_tmm

    original = byrnes_tmm.absorp_in_each_layer

    def inflated(data: dict) -> np.ndarray:
        values = np.asarray(original(data), dtype=np.float64).copy()
        values[1:-1] *= 1.05
        return values

    monkeypatch.setattr(byrnes_tmm, "absorp_in_each_layer", inflated)
    certificate = certify_simulation(
        TMMWorkbench(MaterialRegistry()), _absorbing_task()
    ).certificate

    check = certificate["independent_solver_check"]
    assert check["status"] == "failed"
    assert check["metrics"]["A_independent"]["status"] == "failed"
    assert check["metrics"]["R"]["status"] == "passed"
    assert certificate["accepted"] is False
    assert [item["code"] for item in certificate["failures"]] == ["solver_disagreement"]
    # The primary's own energy ledger is healthy.
    assert certificate["energy_accounting"]["residual_independent"] < 1e-7


def test_multi_layer_internal_independence_closes_energy() -> None:
    """Regression: the internal flux integration must propagate fields forward
    through every layer — a two-layer stack with a buried absorber closes the
    independent ledger to numerical noise, and the cross-implementation
    agreement holds."""

    task = SimulationTask(
        stack=StackSpec(
            layers=(
                LayerSpec("tio2", 30.0),
                LayerSpec(None, 60.0, constant_n=1.8, constant_k=0.4),
            ),
            incident=MediumSpec.air(),
            exit=MediumSpec("sio2"),
        ),
        spectrum=SpectralGrid(500.0, 700.0, 21),
        illumination=IlluminationSpec((0.0, 45.0), ("s", "p")),
    )
    result = TMMWorkbench(MaterialRegistry()).simulate(task)

    assert (
        result.independence_audit["absorption_independence_class"]
        == "layer_absorption_integral"
    )
    assert result.independence_audit["energy_independent_max_abs_error"] < 1e-9
