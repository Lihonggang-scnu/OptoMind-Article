from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from optomind_optics.harness import TMMFailureDiagnoser
from optomind_optics.harness.material_service import MaterialResolutionService
from tmm_engine import (
    FailureCode,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    OptimizationTask,
    SimulationTask,
    SpectralGrid,
    SpectralTarget,
    StackSpec,
)


def _simulation(
    layer: LayerSpec,
    *,
    start_nm: float = 400.0,
    stop_nm: float = 700.0,
    allow_extrapolation: bool = False,
) -> SimulationTask:
    return SimulationTask(
        stack=StackSpec(
            layers=(layer,),
            incident=MediumSpec(constant_n=1.0),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=start_nm, stop_nm=stop_nm, points=3),
        allow_material_extrapolation=allow_extrapolation,
    )


def test_local_named_material_is_resolved_for_simulation_and_optimization() -> None:
    simulation = _simulation(LayerSpec(material="sio2", thickness_nm=100.0))
    task = OptimizationTask(
        simulation=simulation,
        targets=(SpectralTarget("R", 0.5, 400.0, 700.0),),
    )

    manifest = MaterialResolutionService(MaterialRegistry()).resolve(task)

    assert manifest.resolved
    assert manifest.task_kind == "optimization"
    assert [snapshot.position for snapshot in manifest.positions] == [
        "incident",
        "layer[0]",
        "exit",
    ]
    layer = manifest.positions[1]
    assert layer.material_kind == "named"
    assert layer.provenance["provider"] == "local_csv"
    assert layer.provenance["dataset_id"] == "sio2"
    assert layer.wavelengths_um == pytest.approx((0.4, 0.55, 0.7))
    assert not layer.extrapolated


def test_explicit_rii_dataset_is_preserved_in_the_registry_snapshot() -> None:
    task = _simulation(
        LayerSpec(
            material="sio2",
            thickness_nm=100.0,
            provider="rii",
            dataset_id="410",
        )
    )

    manifest = MaterialResolutionService(MaterialRegistry()).resolve(task)

    assert manifest.resolved
    provenance = manifest.positions[1].provenance
    assert provenance["provider"] == "rii_sqlite"
    assert provenance["pageid"] == 410
    assert provenance["dataset_id"] == 410
    assert manifest.positions[1].dataset_id == 410


def test_constant_n_positions_are_manifested_without_registry_lookup() -> None:
    class NoLookupRegistry:
        def resolve(self, *args, **kwargs):
            raise AssertionError("constant-n positions must not resolve a registry material")

        def sample(self, *args, **kwargs):
            raise AssertionError("constant-n positions must not sample a registry material")

        def search(self, *args, **kwargs):
            raise AssertionError("constant-n positions must not search the registry")

    task = SimulationTask(
        stack=StackSpec(
            incident=MediumSpec(constant_n=1.0, constant_k=0.01),
            layers=(LayerSpec(material=None, constant_n=2.2, constant_k=0.03, thickness_nm=80.0),),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=400.0, stop_nm=700.0, points=3),
    )

    manifest = MaterialResolutionService(NoLookupRegistry()).resolve(task)

    assert manifest.resolved
    assert all(snapshot.material_kind == "constant_n" for snapshot in manifest.positions)
    assert manifest.positions[0].constant_k == pytest.approx(0.01)
    assert manifest.positions[1].constant_n == pytest.approx(2.2)
    assert manifest.positions[1].k == (0.03, 0.03, 0.03)
    assert all(not snapshot.provenance for snapshot in manifest.positions)


def test_range_failure_is_fail_closed_and_diagnosable() -> None:
    task = _simulation(
        LayerSpec(material="sio2", thickness_nm=100.0),
        start_nm=100.0,
        stop_nm=150.0,
    )

    manifest = MaterialResolutionService(MaterialRegistry()).resolve(task)

    assert not manifest.resolved
    assert manifest.failures[0].code == FailureCode.MATERIAL_RANGE_ERROR
    assert manifest.positions[1].resolved is False
    diagnosis = TMMFailureDiagnoser().diagnose(manifest.failures[0])
    assert diagnosis.category == "material_data"
    assert diagnosis.context["failure"]["code"] == FailureCode.MATERIAL_RANGE_ERROR.value


def test_ambiguity_is_not_silently_selected_and_branch_choices_do_not_execute() -> None:
    class TrackingRegistry:
        def __init__(self) -> None:
            self.inner = MaterialRegistry()
            self.resolve_calls = 0
            self.sample_calls = 0
            self.search_calls = 0

        def resolve(self, *args, **kwargs):
            self.resolve_calls += 1
            return self.inner.resolve(*args, **kwargs)

        def sample(self, *args, **kwargs):
            self.sample_calls += 1
            return self.inner.sample(*args, **kwargs)

        def search(self, *args, **kwargs):
            self.search_calls += 1
            return self.inner.search(*args, **kwargs)

    registry = TrackingRegistry()
    task = _simulation(
        LayerSpec(material="alumina", thickness_nm=100.0, provider="rii"),
    )
    service = MaterialResolutionService(registry)

    manifest = service.resolve(task)
    resolve_calls = registry.resolve_calls
    sample_calls = registry.sample_calls
    choices = service.list_eligible_dataset_choices(task)

    assert not manifest.resolved
    assert manifest.failures[0].code == FailureCode.MATERIAL_AMBIGUITY
    assert len(manifest.ambiguities) == 1
    assert {choice.dataset_id for choice in manifest.ambiguities[0].choices} == {355, 356}
    assert {choice.dataset_id for choice in choices} == {355, 356}
    assert registry.resolve_calls == resolve_calls
    assert registry.sample_calls == sample_calls
    assert registry.search_calls > 0


def test_manifest_hash_is_stable_and_models_are_frozen() -> None:
    task = _simulation(LayerSpec(material="sio2", thickness_nm=100.0))
    service = MaterialResolutionService(MaterialRegistry())

    first = service.resolve(task)
    second = service.resolve(task)

    assert first.manifest_hash == second.manifest_hash
    assert first.stable_hash == first.manifest_hash
    canonical = json.dumps(
        first.canonical_payload(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert first.manifest_hash == hashlib.sha256(canonical).hexdigest()
    with pytest.raises(ValidationError):
        first.resolved = False


def test_manifest_is_atomically_persisted_when_work_dir_is_supplied(tmp_path: Path) -> None:
    task = _simulation(LayerSpec(material="sio2", thickness_nm=100.0))

    manifest = MaterialResolutionService(MaterialRegistry()).resolve(task, work_dir=tmp_path)

    path = tmp_path / "MATERIAL_MANIFEST.json"
    assert path.exists()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["manifest_hash"] == manifest.manifest_hash
    assert persisted["positions"][1]["provenance"]["provider"] == "local_csv"
    assert not list(tmp_path.glob(".MATERIAL_MANIFEST.*.tmp"))
