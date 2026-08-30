"""Deterministic material resolution for the TMM-only harness.

This module is intentionally a thin audit boundary around the public
``tmm_engine.MaterialRegistry`` facade.  It resolves every optical position in
a task, records the registry response without adding provenance keys, and
returns a frozen manifest that can be persisted and replayed.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import math
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, Optional, Sequence, Tuple

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from tmm_engine import (
    FailureCode,
    FailureRecord,
    MaterialAmbiguityError,
    MaterialNotFoundError,
    MaterialRangeError,
    MaterialRegistry,
    OptimizationTask,
    SimulationTask,
)


PositionKind = Literal["constant_n", "named"]


class _FrozenModel(BaseModel):
    """Shared immutable Pydantic configuration for manifest contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self.model_dump(mode="python"))


class MaterialDatasetChoice(_FrozenModel):
    """One public ``MaterialRegistry.search`` candidate.

    The fields mirror public ``MaterialCandidate`` fields.  ``position`` and
    ``material`` identify where the candidate may be used; they are service
    context, not registry provenance.
    """

    position: str
    material: str
    provider: Optional[str] = None
    dataset_id: Optional[Any] = None
    shelf: Optional[str] = None
    book: Optional[str] = None
    page: Optional[str] = None
    filepath: Optional[str] = None
    score: float = 0.0
    exact_book: bool = False
    full_coverage: bool = False
    has_n: bool = False
    has_k: bool = False
    points: int = 0
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    rank_key: Tuple[int, ...] = ()

    @property
    def pageid(self) -> Any:
        """Compatibility view of the public registry dataset identifier."""

        return self.dataset_id

    @property
    def range_um(self) -> Optional[Tuple[float, float]]:
        if self.range_min is None or self.range_max is None:
            return None
        return float(self.range_min), float(self.range_max)


class MaterialSnapshot(_FrozenModel):
    """Resolved optical constants and audit state for one stack position."""

    position: str
    material_kind: PositionKind
    material: Optional[str] = None
    constant_n: Optional[float] = None
    constant_k: Optional[float] = None
    wavelengths_um: Tuple[float, ...] = ()
    n: Tuple[float, ...] = ()
    k: Tuple[float, ...] = ()
    extrapolated_mask: Tuple[bool, ...] = ()
    extrapolated: bool = False
    warnings: Tuple[str, ...] = ()
    provenance: dict[str, Any] = Field(default_factory=dict)
    resolved: bool = True

    @property
    def material_type(self) -> PositionKind:
        """Alias useful to callers that call the model a material type."""

        return self.material_kind

    @property
    def provider(self) -> Optional[str]:
        value = self.provenance.get("provider")
        return None if value is None else str(value)

    @property
    def dataset_id(self) -> Any:
        if "dataset_id" in self.provenance:
            return self.provenance["dataset_id"]
        return self.provenance.get("pageid")

    @property
    def extrapolation_state(self) -> Literal["used", "not_used"]:
        return "used" if self.extrapolated else "not_used"


class MaterialAmbiguity(_FrozenModel):
    """An unresolved equal-ranked material selection."""

    position: str
    material: str
    choices: Tuple[MaterialDatasetChoice, ...] = ()

    @property
    def candidates(self) -> Tuple[MaterialDatasetChoice, ...]:
        return self.choices

    @property
    def eligible_datasets(self) -> Tuple[MaterialDatasetChoice, ...]:
        return self.choices


class MaterialManifest(_FrozenModel):
    """Frozen, hashable material-resolution output for a TMM task."""

    schema_version: str = "tmm-material-manifest.v1"
    task_kind: Literal["simulation", "optimization"]
    wavelengths_um: Tuple[float, ...] = ()
    wavelength_interval_um: Optional[Tuple[float, float]] = None
    allow_extrapolation: bool = False
    positions: Tuple[MaterialSnapshot, ...] = ()
    warnings: Tuple[str, ...] = ()
    ambiguities: Tuple[MaterialAmbiguity, ...] = ()
    extrapolation_used: bool = False
    resolved: bool = False
    failures: Tuple[FailureRecord, ...] = ()
    manifest_hash: str

    @property
    def stable_hash(self) -> str:
        return self.manifest_hash

    @property
    def stable_manifest_hash(self) -> str:
        return self.manifest_hash

    @property
    def hash(self) -> str:
        return self.manifest_hash

    @property
    def success(self) -> bool:
        return self.resolved

    @property
    def ready(self) -> bool:
        return self.resolved

    @property
    def failure_records(self) -> Tuple[FailureRecord, ...]:
        return self.failures

    @property
    def material_snapshots(self) -> Tuple[MaterialSnapshot, ...]:
        return self.positions

    @property
    def snapshots(self) -> Tuple[MaterialSnapshot, ...]:
        return self.positions

    @property
    def extrapolated(self) -> bool:
        return self.extrapolation_used

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation including the hash."""

        return _jsonable(self.model_dump(mode="python"))

    def canonical_payload(self) -> dict[str, Any]:
        """Return the hash input, excluding the self-referential hash field."""

        payload = self.to_dict()
        payload.pop("manifest_hash", None)
        return payload

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            separators=(",", ": "),
        )


class MaterialResolutionService:
    """Resolve task materials through the public ``MaterialRegistry`` API."""

    def __init__(
        self,
        registry: Optional[MaterialRegistry] = None,
        *,
        material_registry: Optional[MaterialRegistry] = None,
    ) -> None:
        if registry is not None and material_registry is not None:
            raise ValueError("supply registry or material_registry, not both")
        selected = material_registry if material_registry is not None else registry
        self.registry = selected if selected is not None else MaterialRegistry()
        self.material_registry = self.registry

    def resolve(
        self,
        task: SimulationTask | OptimizationTask,
        work_dir: str | Path | None = None,
    ) -> MaterialManifest:
        """Resolve all incident, layer, and exit positions in ``task``.

        Registry failures are represented in the returned manifest and leave
        ``resolved`` false.  This is deliberately fail-closed: a caller must
        inspect ``failures`` before allowing a TMM execution.
        """

        task_kind, simulation, task_failure = self._task_context(task)
        if task_failure is not None:
            manifest = self._build_manifest(
                task_kind=task_kind,
                wavelengths_um=(),
                wavelength_interval_um=None,
                allow_extrapolation=False,
                positions=(),
                warnings=(),
                ambiguities=(),
                failures=(task_failure,),
            )
            self._persist_if_requested(manifest, work_dir)
            return manifest

        wavelengths_um = _wavelengths_um(simulation)
        interval = (float(wavelengths_um[0]), float(wavelengths_um[-1]))
        allow_extrapolation = bool(simulation.allow_material_extrapolation)

        snapshots: list[MaterialSnapshot] = []
        warnings: list[str] = []
        ambiguities: list[MaterialAmbiguity] = []
        failures: list[FailureRecord] = []

        for position, spec in _material_positions(simulation):
            if spec.constant_n is not None:
                snapshot = self._constant_snapshot(position, spec, wavelengths_um)
                snapshots.append(snapshot)
                continue

            snapshot, failure, ambiguity = self._named_snapshot(
                position,
                spec,
                wavelengths_um,
                interval,
                allow_extrapolation,
            )
            snapshots.append(snapshot)
            warnings.extend(snapshot.warnings)
            if ambiguity is not None:
                ambiguities.append(ambiguity)
            if failure is not None:
                failures.append(failure)

        manifest = self._build_manifest(
            task_kind=task_kind,
            wavelengths_um=wavelengths_um,
            wavelength_interval_um=interval,
            allow_extrapolation=allow_extrapolation,
            positions=tuple(snapshots),
            warnings=tuple(warnings),
            ambiguities=tuple(ambiguities),
            failures=tuple(failures),
        )
        self._persist_if_requested(manifest, work_dir)
        return manifest

    def resolve_task(
        self,
        task: SimulationTask | OptimizationTask,
        work_dir: str | Path | None = None,
    ) -> MaterialManifest:
        """Explicit task-named alias for :meth:`resolve`."""

        return self.resolve(task, work_dir=work_dir)

    def resolve_materials(
        self,
        task: SimulationTask | OptimizationTask,
        work_dir: str | Path | None = None,
    ) -> MaterialManifest:
        """Compatibility alias used by harness action dispatchers."""

        return self.resolve(task, work_dir=work_dir)

    def resolve_task_materials(
        self,
        task: SimulationTask | OptimizationTask,
        work_dir: str | Path | None = None,
    ) -> MaterialManifest:
        """Alias for :meth:`resolve` with the full action-oriented name."""

        return self.resolve(task, work_dir=work_dir)

    def list_eligible_dataset_choices(
        self,
        task: SimulationTask | OptimizationTask,
        *,
        position: str | None = None,
    ) -> Tuple[MaterialDatasetChoice, ...]:
        """List top-ranked eligible choices without resolving or sampling.

        If a material is ambiguous, all equal-ranked top candidates are
        returned.  No branch is executed and this method never calls
        ``MaterialRegistry.resolve`` or ``MaterialRegistry.sample``.
        """

        _, simulation, task_failure = self._task_context(task)
        if task_failure is not None:
            raise ValueError(task_failure.message)

        wavelengths_um = _wavelengths_um(simulation)
        interval = (float(wavelengths_um[0]), float(wavelengths_um[-1]))
        choices: list[MaterialDatasetChoice] = []
        for item_position, spec in _material_positions(simulation):
            if position is not None and item_position != str(position):
                continue
            if spec.constant_n is not None:
                continue

            candidates = self.registry.search(
                spec.material,
                wavelength_range=interval,
                provider=spec.provider,
                dataset_id=spec.dataset_id,
            )
            if not candidates:
                continue
            top_rank = max(
                tuple(int(value) for value in candidate.rank_key)
                for candidate in candidates
            )
            eligible = [
                candidate
                for candidate in candidates
                if tuple(int(value) for value in candidate.rank_key) == top_rank
            ]
            choices.extend(
                _candidate_choice(item_position, candidate) for candidate in eligible
            )
        choices.sort(
            key=lambda choice: (
                choice.position,
                choice.material,
                choice.provider or "",
                str(choice.dataset_id),
                choice.book or "",
                choice.page or "",
            )
        )
        return tuple(choices)

    def eligible_dataset_choices(
        self,
        task: SimulationTask | OptimizationTask,
        *,
        position: str | None = None,
    ) -> Tuple[MaterialDatasetChoice, ...]:
        """Alias for :meth:`list_eligible_dataset_choices`."""

        return self.list_eligible_dataset_choices(task, position=position)

    def list_branch_choices(
        self,
        task: SimulationTask | OptimizationTask,
        *,
        position: str | None = None,
    ) -> Tuple[MaterialDatasetChoice, ...]:
        """Alias for :meth:`list_eligible_dataset_choices`."""

        return self.list_eligible_dataset_choices(task, position=position)

    def branch_choices(
        self,
        task: SimulationTask | OptimizationTask,
        *,
        position: str | None = None,
    ) -> Tuple[MaterialDatasetChoice, ...]:
        """Explicit branch-planning alias; it does not execute branches."""

        return self.list_eligible_dataset_choices(task, position=position)

    def eligible_branches(
        self,
        task: SimulationTask | OptimizationTask,
        *,
        position: str | None = None,
    ) -> Tuple[MaterialDatasetChoice, ...]:
        """Alias for deterministic ambiguity branch planning."""

        return self.list_eligible_dataset_choices(task, position=position)

    def _task_context(
        self,
        task: SimulationTask | OptimizationTask,
    ) -> tuple[Literal["simulation", "optimization"], SimulationTask, FailureRecord | None]:
        if isinstance(task, OptimizationTask):
            task_kind: Literal["simulation", "optimization"] = "optimization"
            simulation = task.simulation
        elif isinstance(task, SimulationTask):
            task_kind = "simulation"
            simulation = task
        else:
            raise TypeError("material resolution requires SimulationTask or OptimizationTask")

        try:
            task.validate()
        except Exception as exc:
            return (
                task_kind,
                simulation,
                FailureRecord(
                    FailureCode.INVALID_TASK,
                    f"{type(exc).__name__}: {exc}",
                    False,
                    context={"task_kind": task_kind},
                ),
            )
        return task_kind, simulation, None

    def _constant_snapshot(
        self,
        position: str,
        spec: Any,
        wavelengths_um: Tuple[float, ...],
    ) -> MaterialSnapshot:
        n_value = float(spec.constant_n)
        k_value = float(spec.constant_k)
        return MaterialSnapshot(
            position=position,
            material_kind="constant_n",
            constant_n=n_value,
            constant_k=k_value,
            wavelengths_um=wavelengths_um,
            n=tuple(n_value for _ in wavelengths_um),
            k=tuple(k_value for _ in wavelengths_um),
            extrapolated_mask=tuple(False for _ in wavelengths_um),
            extrapolated=False,
            warnings=(),
            provenance={},
            resolved=True,
        )

    def _named_snapshot(
        self,
        position: str,
        spec: Any,
        wavelengths_um: Tuple[float, ...],
        interval: Tuple[float, float],
        allow_extrapolation: bool,
    ) -> tuple[MaterialSnapshot, FailureRecord | None, MaterialAmbiguity | None]:
        material = str(spec.material)
        context = {
            "position": position,
            "material": material,
            "wavelength_interval_um": interval,
            "allow_extrapolation": allow_extrapolation,
        }
        if spec.provider is not None:
            context["provider"] = spec.provider
        if spec.dataset_id is not None:
            context["dataset_id"] = spec.dataset_id

        try:
            # Resolve first so equal-ranked candidates remain a hard failure.
            reference = self.registry.resolve(
                material,
                provider=spec.provider,
                dataset_id=spec.dataset_id,
                wavelength_range=interval,
            )
            sampled = self.registry.sample(
                reference,
                wavelengths_um,
                allow_extrapolation=allow_extrapolation,
            )
            sample_mask = tuple(bool(value) for value in sampled.extrapolated_mask)
            sample_extrapolated = bool(sampled.extrapolated or any(sample_mask))
            if sample_extrapolated and not allow_extrapolation:
                failure = FailureRecord(
                    FailureCode.MATERIAL_RANGE_ERROR,
                    "MaterialRegistry returned extrapolated constants while extrapolation is disabled.",
                    True,
                    context=context,
                )
                return self._failed_named_snapshot(position, material), failure, None

            snapshot = MaterialSnapshot(
                position=position,
                material_kind="named",
                material=material,
                wavelengths_um=_float_tuple(sampled.wavelengths_um),
                n=_float_tuple(sampled.n),
                k=_float_tuple(sampled.k),
                extrapolated_mask=sample_mask,
                extrapolated=sample_extrapolated,
                warnings=tuple(str(value) for value in sampled.warnings),
                provenance=copy.deepcopy(dict(sampled.provenance)),
                resolved=True,
            )
            return snapshot, None, None
        except MaterialAmbiguityError as exc:
            ambiguity_choices = [
                _candidate_choice(position, candidate)
                for candidate in getattr(exc, "candidates", ())
            ]
            ambiguity_choices.sort(
                key=lambda choice: (
                    choice.provider or "",
                    str(choice.dataset_id),
                    choice.book or "",
                    choice.page or "",
                )
            )
            choices = tuple(ambiguity_choices)
            ambiguity = MaterialAmbiguity(
                position=position,
                material=material,
                choices=choices,
            )
            failure = FailureRecord(
                FailureCode.MATERIAL_AMBIGUITY,
                str(exc),
                True,
                context={**context, "candidates": [choice.to_dict() for choice in choices]},
            )
            return self._failed_named_snapshot(position, material), failure, ambiguity
        except MaterialRangeError as exc:
            range_context = dict(context)
            if exc.requested_range is not None:
                range_context["requested_range_um"] = exc.requested_range
            if exc.available_range is not None:
                range_context["available_range_um"] = exc.available_range
            failure = FailureRecord(
                FailureCode.MATERIAL_RANGE_ERROR,
                str(exc),
                True,
                context=range_context,
            )
            return self._failed_named_snapshot(position, material), failure, None
        except MaterialNotFoundError as exc:
            failure = FailureRecord(
                FailureCode.MATERIAL_NOT_FOUND,
                str(exc),
                True,
                context=context,
            )
            return self._failed_named_snapshot(position, material), failure, None
        except (TypeError, ValueError) as exc:
            failure = FailureRecord(
                FailureCode.INVALID_TASK,
                f"{type(exc).__name__}: {exc}",
                False,
                context=context,
            )
            return self._failed_named_snapshot(position, material), failure, None
        except Exception as exc:
            # A registry/provider failure must never become an apparently
            # resolved material.  Keep the failure structured and fail closed.
            failure = FailureRecord(
                FailureCode.INVALID_TASK,
                f"{type(exc).__name__}: {exc}",
                False,
                context=context,
            )
            return self._failed_named_snapshot(position, material), failure, None

    @staticmethod
    def _failed_named_snapshot(position: str, material: str) -> MaterialSnapshot:
        return MaterialSnapshot(
            position=position,
            material_kind="named",
            material=material,
            provenance={},
            resolved=False,
        )

    @staticmethod
    def _build_manifest(
        *,
        task_kind: Literal["simulation", "optimization"],
        wavelengths_um: Sequence[float],
        wavelength_interval_um: Optional[Tuple[float, float]],
        allow_extrapolation: bool,
        positions: Tuple[MaterialSnapshot, ...],
        warnings: Tuple[str, ...],
        ambiguities: Tuple[MaterialAmbiguity, ...],
        failures: Tuple[FailureRecord, ...],
    ) -> MaterialManifest:
        extrapolation_used = any(snapshot.extrapolated for snapshot in positions)
        pending = MaterialManifest(
            task_kind=task_kind,
            wavelengths_um=tuple(float(value) for value in wavelengths_um),
            wavelength_interval_um=wavelength_interval_um,
            allow_extrapolation=allow_extrapolation,
            positions=positions,
            warnings=warnings,
            ambiguities=ambiguities,
            extrapolation_used=extrapolation_used,
            resolved=not failures and all(snapshot.resolved for snapshot in positions),
            failures=failures,
            manifest_hash="pending",
        )
        canonical = json.dumps(
            pending.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        return MaterialManifest(
            **{
                **pending.model_dump(mode="python"),
                "manifest_hash": digest,
            }
        )

    @staticmethod
    def _persist_if_requested(
        manifest: MaterialManifest,
        work_dir: str | Path | None,
    ) -> None:
        if work_dir is None:
            return
        directory = Path(work_dir)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "MATERIAL_MANIFEST.json"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".MATERIAL_MANIFEST.",
            suffix=".tmp",
            dir=str(directory),
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(manifest.to_json())
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)


def resolve_materials(
    task: SimulationTask | OptimizationTask,
    registry: Optional[MaterialRegistry] = None,
    work_dir: str | Path | None = None,
) -> MaterialManifest:
    """Functional entry point for deterministic task material resolution."""

    return MaterialResolutionService(registry).resolve(task, work_dir=work_dir)


def resolve_material_manifest(
    task: SimulationTask | OptimizationTask,
    registry: Optional[MaterialRegistry] = None,
    work_dir: str | Path | None = None,
) -> MaterialManifest:
    """Explicit manifest-named alias for :func:`resolve_materials`."""

    return resolve_materials(task, registry=registry, work_dir=work_dir)


def _material_positions(simulation: SimulationTask) -> Iterable[tuple[str, Any]]:
    yield "incident", simulation.stack.incident
    for index, layer in enumerate(simulation.stack.layers):
        yield f"layer[{index}]", layer
    yield "exit", simulation.stack.exit


def _wavelengths_um(simulation: SimulationTask) -> Tuple[float, ...]:
    wavelengths_nm = simulation.spectrum.wavelengths_nm()
    return tuple(float(value) * 1.0e-3 for value in wavelengths_nm)


def _float_tuple(values: Any) -> Tuple[float, ...]:
    array = np.asarray(values, dtype=np.float64).reshape(-1)
    if not np.all(np.isfinite(array)):
        raise ValueError("MaterialRegistry returned non-finite optical constants")
    return tuple(float(value) for value in array)


def _candidate_choice(position: str, candidate: Any) -> MaterialDatasetChoice:
    material = getattr(candidate, "material", None)
    if material is None:
        reference = getattr(candidate, "ref", None)
        material = getattr(reference, "normalized_name", None) or getattr(reference, "name", "")
    rank_key = tuple(int(value) for value in getattr(candidate, "rank_key", ()))
    dataset_id = getattr(candidate, "dataset_id", None)
    if dataset_id is None:
        dataset_id = getattr(candidate, "pageid", None)
    return MaterialDatasetChoice(
        position=position,
        material=str(material),
        provider=_optional_str(getattr(candidate, "provider", None)),
        dataset_id=_jsonable(dataset_id),
        shelf=_optional_str(getattr(candidate, "shelf", None)),
        book=_optional_str(getattr(candidate, "book", None)),
        page=_optional_str(getattr(candidate, "page", None)),
        filepath=_optional_str(getattr(candidate, "filepath", None)),
        score=float(getattr(candidate, "score", 0.0)),
        exact_book=bool(getattr(candidate, "exact_book", False)),
        full_coverage=bool(getattr(candidate, "full_coverage", False)),
        has_n=bool(getattr(candidate, "has_n", False)),
        has_k=bool(getattr(candidate, "has_k", False)),
        points=int(getattr(candidate, "points", 0)),
        range_min=_optional_float(getattr(candidate, "range_min", None)),
        range_max=_optional_float(getattr(candidate, "range_max", None)),
        rank_key=rank_key,
    )


def _optional_str(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _jsonable(value: Any) -> Any:
    """Convert public registry values and Pydantic models to JSON values."""

    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="python"))
    if isinstance(value, FailureRecord):
        return _jsonable(value.to_dict())
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_jsonable(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return _jsonable(value.item())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("manifest values must be finite")
    return value


# Small aliases keep the contract discoverable for existing harness naming.
TMMMaterialResolutionService = MaterialResolutionService
TMMMaterialService = MaterialResolutionService
MaterialService = MaterialResolutionService
MaterialBranchChoice = MaterialDatasetChoice
MaterialPositionSnapshot = MaterialSnapshot
MaterialResolutionManifest = MaterialManifest


__all__ = [
    "MaterialAmbiguity",
    "MaterialBranchChoice",
    "MaterialDatasetChoice",
    "MaterialManifest",
    "MaterialPositionSnapshot",
    "MaterialResolutionManifest",
    "MaterialResolutionService",
    "MaterialService",
    "MaterialSnapshot",
    "TMMMaterialService",
    "TMMMaterialResolutionService",
    "resolve_material_manifest",
    "resolve_materials",
]
