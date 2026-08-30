"""Append-only artifact lineage for the TMM optical harness.

The store in this module deliberately has no dependency on a solver, an
optimizer, or a model client.  It records files that already exist in a run
directory and makes their byte content and producer/input relationships
replayable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ARTIFACT_MANIFEST_FILENAME = "ARTIFACT_MANIFEST.json"
MANIFEST_FILENAME = ARTIFACT_MANIFEST_FILENAME
MANIFEST_SCHEMA_VERSION = "tmm-artifact-manifest.v1"

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_LOCKS_GUARD = threading.Lock()
_MANIFEST_LOCKS: dict[str, threading.RLock] = {}


def _native_long_path(path: str | os.PathLike[str]) -> str:
    value = os.path.abspath(os.fspath(path))
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    return "\\\\?\\" + value


def _portable_path_from_native(path: str) -> Path:
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[len("\\\\?\\UNC\\") :]
    elif path.startswith("\\\\?\\"):
        path = path[len("\\\\?\\") :]
    return Path(path)


class ArtifactLineageError(ValueError):
    """Base error for invalid or unverifiable artifact lineage."""


class ManifestError(ArtifactLineageError):
    """The persisted manifest is absent, malformed, or internally invalid."""


class ManifestIntegrityError(ManifestError):
    """The persisted manifest's immutable history or metadata is invalid."""


class ArtifactPathError(ArtifactLineageError):
    """An artifact path is unsafe or cannot be constrained to the run root."""


class ArtifactMissingError(ArtifactLineageError):
    """An artifact path does not name a readable regular file."""


class UnknownArtifactError(ArtifactLineageError):
    """A lineage input refers to an artifact that is not already registered."""


class DuplicateArtifactError(ArtifactLineageError):
    """An artifact ID was used for a different logical or byte record."""


class HistoryRewriteError(DuplicateArtifactError):
    """An operation would rewrite an existing append-only history entry."""


class LineageCycleError(ArtifactLineageError):
    """The input-artifact graph contains a cycle."""


class ArtifactTamperedError(ManifestIntegrityError, HistoryRewriteError):
    """A registered file no longer matches its immutable manifest record."""


# Useful compatibility names for callers that use ``provenance`` terminology.
ProvenanceError = ArtifactLineageError
ArtifactProvenanceError = ArtifactLineageError
PathTraversalError = ArtifactPathError
UnknownInputArtifactError = UnknownArtifactError
ImmutableHistoryError = HistoryRewriteError


def _canonical_json(value: Any) -> str:
    """Return deterministic JSON for hashes and immutable comparisons."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactLineageError(
            "Artifact lineage metadata must be JSON-serializable and finite"
        ) from exc


def _clone_json_mapping(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ArtifactLineageError("scientific_provenance must be a JSON object")
    try:
        cloned = json.loads(_canonical_json(dict(value)))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ArtifactLineageError(
            "scientific_provenance must be JSON-serializable and finite"
        ) from exc
    if not isinstance(cloned, dict):  # pragma: no cover - guarded by Mapping above
        raise ArtifactLineageError("scientific_provenance must be a JSON object")
    return cloned


def _normalise_text(value: Any, field_name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ArtifactLineageError(f"{field_name} must be a string")
    normalised = value.strip()
    if not normalised and not allow_empty:
        raise ArtifactLineageError(f"{field_name} must not be empty")
    return normalised


def _normalise_artifact_id(value: Any) -> str:
    return _normalise_text(value, "artifact_id")


def _normalise_input_ids(value: Iterable[Any] | str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    else:
        try:
            values = tuple(value)
        except TypeError as exc:
            raise ArtifactLineageError(
                "input_artifact_ids must be an iterable of strings"
            ) from exc
    result = tuple(_normalise_artifact_id(item) for item in values)
    if len(result) != len(set(result)):
        raise ArtifactLineageError("input_artifact_ids must not contain duplicates")
    return result


def _created_timestamp(value: str | datetime | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    if isinstance(value, datetime):
        timestamp = value
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
    return _normalise_text(value, "created_at")


def _manifest_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _MANIFEST_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _MANIFEST_LOCKS[key] = lock
        return lock


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a manifest through a same-directory temporary file and replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".artifact-manifest-", suffix=".tmp"
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with open(_native_long_path(path), "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                byte_count += len(chunk)
    except (FileNotFoundError, IsADirectoryError, PermissionError) as exc:
        raise ArtifactMissingError(
            f"Artifact file is missing or unreadable: {path}"
        ) from exc
    return digest.hexdigest(), byte_count


@dataclass(frozen=True, eq=False)
class ArtifactRecord(Mapping[str, Any]):
    """One immutable artifact record in the manifest.

    ``relative_path`` is the canonical path field.  ``path`` is emitted as a
    compatibility alias because existing optical artifacts commonly call this
    field simply ``path``.  Both values are always identical.
    """

    artifact_id: str
    relative_path: str
    sha256: str
    bytes: int
    artifact_type: str
    producing_action: str
    producing_node: str | None
    input_artifact_ids: tuple[str, ...]
    created_at: str
    scientific_provenance: dict[str, Any] | None = None
    record_hash: str = ""
    previous_record_hash: str | None = None

    @property
    def path(self) -> str:
        return self.relative_path

    @property
    def size_bytes(self) -> int:
        return self.bytes

    def logical_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "bytes": self.bytes,
            "artifact_type": self.artifact_type,
            "producing_action": self.producing_action,
            "producing_node": self.producing_node,
            "input_artifact_ids": list(self.input_artifact_ids),
            "created_at": self.created_at,
        }
        if self.scientific_provenance is not None:
            payload["scientific_provenance"] = _clone_json_mapping(
                self.scientific_provenance
            )
        return payload

    def with_integrity(self, previous_record_hash: str | None) -> "ArtifactRecord":
        unsigned = replace(
            self,
            record_hash="",
            previous_record_hash=previous_record_hash,
        )
        record_hash = hashlib.sha256(
            _canonical_json(unsigned.logical_dict()).encode("utf-8")
        ).hexdigest()
        return replace(unsigned, record_hash=record_hash)

    def to_dict(self) -> dict[str, Any]:
        payload = self.logical_dict()
        payload["path"] = self.relative_path
        payload["record_hash"] = self.record_hash or self.with_integrity(
            self.previous_record_hash
        ).record_hash
        payload["previous_record_hash"] = self.previous_record_hash
        return payload

    # Mapping support lets callers use either ``record.artifact_id`` or
    # ``record["artifact_id"]`` without exposing mutable internal state.
    def __getitem__(self, key: str) -> Any:
        if key == "path":
            return self.relative_path
        if key == "size_bytes":
            return self.bytes
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ArtifactRecord):
            return self.to_dict() == other.to_dict()
        if isinstance(other, Mapping):
            return self.to_dict() == dict(other)
        return NotImplemented


class ArtifactLineageStore:
    """Thread-safe, append-only lineage persisted under a TMM run root.

    A store never truncates or replaces a logical record.  Registering an
    already-known ID is idempotent only when every logical field is identical;
    otherwise the operation fails closed as an immutable-history violation.
    """

    def __init__(
        self,
        run_root: str | Path,
        *,
        resume: bool = False,
        manifest_name: str = ARTIFACT_MANIFEST_FILENAME,
    ) -> None:
        if not isinstance(manifest_name, str) or not manifest_name:
            raise ManifestError("manifest_name must be a non-empty string")
        if Path(manifest_name).name != manifest_name or Path(manifest_name).is_absolute():
            raise ManifestError("manifest_name must be a file name, not a path")
        self.run_root = Path(run_root).expanduser().resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.run_root / manifest_name
        self._lock = _manifest_lock(self.manifest_path)
        self._records: tuple[ArtifactRecord, ...] = ()
        self._opened = False
        self.resume = bool(resume)
        with self._lock:
            if self.manifest_path.exists():
                self._load_locked()
            else:
                self._write_manifest_locked(self._records)
            self._opened = True

    @classmethod
    def resume_from(cls, run_root: str | Path) -> "ArtifactLineageStore":
        return cls(run_root, resume=True)

    @classmethod
    def from_disk(cls, run_root: str | Path) -> "ArtifactLineageStore":
        return cls(run_root, resume=True)

    def _normalise_path(self, path: str | Path) -> tuple[str, Path]:
        try:
            raw = os.fspath(path)
        except TypeError as exc:
            raise ArtifactPathError("artifact path must be a string or Path") from exc
        if isinstance(raw, bytes):
            raise ArtifactPathError("artifact path must be text, not bytes")
        if not raw or not str(raw).strip():
            raise ArtifactPathError("artifact path must not be empty")
        raw_text = str(raw)
        if "\x00" in raw_text:
            raise ArtifactPathError("artifact path must not contain NUL")

        # Reject traversal syntax before normalisation.  Resolving ``a/../b``
        # to a safe path would hide the caller's attempt to escape the run
        # root and would make the persisted path less auditable.
        traversal_parts = raw_text.replace("\\", "/").split("/")
        if ".." in traversal_parts:
            raise ArtifactPathError(
                f"path traversal is not allowed for artifact path: {raw_text}"
            )

        candidate = Path(raw_text)
        if candidate.is_absolute():
            lexical_path = Path(os.path.abspath(str(candidate)))
        else:
            lexical_path = Path(os.path.abspath(str(self.run_root / candidate)))
        try:
            relative = lexical_path.relative_to(self.run_root)
        except ValueError as exc:
            raise ArtifactPathError(
                f"artifact path is outside the run root: {raw_text}"
            ) from exc
        if str(relative) in {"", "."}:
            raise ArtifactPathError("artifact path must name a file below the run root")

        try:
            resolved_native = os.path.realpath(
                _native_long_path(lexical_path), strict=True
            )
            resolved_path = _portable_path_from_native(resolved_native)
        except FileNotFoundError as exc:
            raise ArtifactMissingError(
                f"Artifact file is missing: {raw_text}"
            ) from exc
        try:
            resolved_path.relative_to(self.run_root)
        except ValueError as exc:
            raise ArtifactPathError(
                f"artifact path resolves outside the run root: {raw_text}"
            ) from exc
        if not os.path.isfile(_native_long_path(resolved_path)):
            raise ArtifactMissingError(
                f"Artifact path is not a regular file: {raw_text}"
            )

        normalised = Path(os.path.normpath(str(relative))).as_posix()
        if normalised == ARTIFACT_MANIFEST_FILENAME:
            raise ArtifactPathError(
                f"{ARTIFACT_MANIFEST_FILENAME} is reserved for lineage metadata"
            )
        return normalised, resolved_path

    @staticmethod
    def _normalise_action(value: Any, field_name: str) -> str:
        return _normalise_text(value, field_name)

    def _build_record(
        self,
        *,
        artifact_id: str,
        path: str | Path,
        artifact_type: str,
        producing_action: str,
        producing_node: str | None,
        input_artifact_ids: Iterable[Any] | str | None,
        created_at: str | datetime | None,
        scientific_provenance: Mapping[str, Any] | None,
    ) -> ArtifactRecord:
        relative_path, resolved_path = self._normalise_path(path)
        sha256, byte_count = _file_digest(resolved_path)
        node = None
        if producing_node is not None:
            node = _normalise_text(producing_node, "producing_node")
        return ArtifactRecord(
            artifact_id=_normalise_artifact_id(artifact_id),
            relative_path=relative_path,
            sha256=sha256,
            bytes=byte_count,
            artifact_type=_normalise_text(artifact_type, "artifact_type"),
            producing_action=self._normalise_action(
                producing_action, "producing_action"
            ),
            producing_node=node,
            input_artifact_ids=_normalise_input_ids(input_artifact_ids),
            created_at=_created_timestamp(created_at),
            scientific_provenance=_clone_json_mapping(scientific_provenance),
        )

    @staticmethod
    def _validate_input_ids(
        record: ArtifactRecord, records: tuple[ArtifactRecord, ...]
    ) -> None:
        known_ids = {item.artifact_id for item in records}
        for input_id in record.input_artifact_ids:
            if input_id not in known_ids:
                raise UnknownArtifactError(
                    f"Unknown input artifact ID '{input_id}' for '{record.artifact_id}'"
                )

    @staticmethod
    def _validate_cycles(records: tuple[ArtifactRecord, ...]) -> None:
        graph = {
            item.artifact_id: item.input_artifact_ids for item in records
        }
        state: dict[str, int] = {}
        stack: list[str] = []

        def visit(artifact_id: str) -> None:
            current_state = state.get(artifact_id, 0)
            if current_state == 2:
                return
            if current_state == 1:
                try:
                    start = stack.index(artifact_id)
                except ValueError:  # pragma: no cover - defensive only
                    start = 0
                cycle = stack[start:] + [artifact_id]
                raise LineageCycleError(
                    "Artifact lineage cycle detected: " + " -> ".join(cycle)
                )
            state[artifact_id] = 1
            stack.append(artifact_id)
            for parent_id in graph[artifact_id]:
                visit(parent_id)
            stack.pop()
            state[artifact_id] = 2

        for artifact_id in graph:
            visit(artifact_id)

    def _validate_records_locked(
        self,
        records: tuple[ArtifactRecord, ...],
        manifest_payload: Mapping[str, Any] | None = None,
    ) -> None:
        ids: set[str] = set()
        for index, record in enumerate(records):
            if record.artifact_id in ids:
                raise ManifestIntegrityError(
                    f"Duplicate artifact ID in immutable history: {record.artifact_id}"
                )
            ids.add(record.artifact_id)

            expected_record_hash = hashlib.sha256(
                _canonical_json(record.logical_dict()).encode("utf-8")
            ).hexdigest()
            if record.record_hash != expected_record_hash:
                raise ManifestIntegrityError(
                    f"Immutable record hash mismatch for artifact '{record.artifact_id}'"
                )
            expected_previous = records[index - 1].record_hash if index else None
            if record.previous_record_hash != expected_previous:
                raise ManifestIntegrityError(
                    f"Immutable history chain mismatch at artifact '{record.artifact_id}'"
                )

            # Validate existence against the complete persisted ID set first.
            # Append ordering is checked after cycle detection below, so a
            # self-reference is reported as a cycle and a forward reference
            # is reported as an append-only ordering violation.
            self._validate_input_ids(record, records)

        # Check graph shape before ordering so a tampered self-reference is
        # reported as a cycle rather than as a less useful forward-reference.
        self._validate_cycles(records)
        positions = {record.artifact_id: index for index, record in enumerate(records)}
        for record in records:
            for input_id in record.input_artifact_ids:
                if positions[input_id] >= positions[record.artifact_id]:
                    raise ManifestIntegrityError(
                        f"Input artifact '{input_id}' does not precede '{record.artifact_id}'"
                    )

        for record in records:
            relative_path, resolved_path = self._normalise_path(record.relative_path)
            if relative_path != record.relative_path:
                raise ManifestIntegrityError(
                    f"Artifact path is not normalized for '{record.artifact_id}'"
                )
            observed_sha256, observed_bytes = _file_digest(resolved_path)
            if observed_sha256 != record.sha256 or observed_bytes != record.bytes:
                raise ArtifactTamperedError(
                    f"Artifact '{record.artifact_id}' hash/bytes mismatch; "
                    "immutable history has been tampered with"
                )

        if manifest_payload is not None:
            expected_count = len(records)
            if manifest_payload.get("record_count") != expected_count:
                raise ManifestIntegrityError("ARTIFACT_MANIFEST.json record_count is invalid")
            expected_head = records[-1].record_hash if records else None
            if manifest_payload.get("head_hash") != expected_head:
                raise ManifestIntegrityError("ARTIFACT_MANIFEST.json head_hash is invalid")

    @staticmethod
    def _record_from_dict(raw: Mapping[str, Any]) -> ArtifactRecord:
        if not isinstance(raw, Mapping):
            raise ManifestIntegrityError("Every artifact manifest entry must be an object")

        relative_path = raw.get("relative_path")
        path_alias = raw.get("path")
        if relative_path is None:
            relative_path = path_alias
        elif path_alias is not None and path_alias != relative_path:
            raise ManifestIntegrityError("Artifact path and relative_path disagree")
        if relative_path is None:
            raise ManifestIntegrityError("Artifact record is missing relative_path")

        action = raw.get("producing_action")
        node = raw.get("producing_node")
        producer = raw.get("producing")
        if action is None and isinstance(producer, Mapping):
            action = producer.get("action")
            if node is None:
                node = producer.get("node")
        if action is None:
            raise ManifestIntegrityError("Artifact record is missing producing_action")

        required = (
            "artifact_id",
            "sha256",
            "bytes",
            "artifact_type",
            "input_artifact_ids",
            "created_at",
            "record_hash",
            "previous_record_hash",
        )
        missing = [field for field in required if field not in raw]
        if missing:
            raise ManifestIntegrityError(
                "Artifact record is missing required fields: " + ", ".join(missing)
            )

        artifact_id = _normalise_artifact_id(raw["artifact_id"])
        sha256 = raw["sha256"]
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise ManifestIntegrityError(
                f"Invalid SHA-256 for artifact '{artifact_id}'"
            )
        byte_count = raw["bytes"]
        if isinstance(byte_count, bool) or not isinstance(byte_count, int) or byte_count < 0:
            raise ManifestIntegrityError(
                f"Invalid byte count for artifact '{artifact_id}'"
            )
        node_value = None if node is None else _normalise_text(node, "producing_node")
        record = ArtifactRecord(
            artifact_id=artifact_id,
            relative_path=_normalise_text(relative_path, "relative_path"),
            sha256=sha256,
            bytes=byte_count,
            artifact_type=_normalise_text(raw["artifact_type"], "artifact_type"),
            producing_action=_normalise_text(action, "producing_action"),
            producing_node=node_value,
            input_artifact_ids=_normalise_input_ids(raw["input_artifact_ids"]),
            created_at=_normalise_text(raw["created_at"], "created_at"),
            scientific_provenance=_clone_json_mapping(
                raw.get("scientific_provenance", raw.get("provenance"))
            ),
            record_hash=_normalise_text(raw["record_hash"], "record_hash"),
            previous_record_hash=(
                None
                if raw["previous_record_hash"] is None
                else _normalise_text(
                    raw["previous_record_hash"], "previous_record_hash"
                )
            ),
        )
        if not _SHA256_RE.fullmatch(record.record_hash):
            raise ManifestIntegrityError(
                f"Invalid immutable record hash for artifact '{artifact_id}'"
            )
        if record.previous_record_hash is not None and not _SHA256_RE.fullmatch(
            record.previous_record_hash
        ):
            raise ManifestIntegrityError(
                f"Invalid previous record hash for artifact '{artifact_id}'"
            )
        return record

    def _load_locked(self) -> None:
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManifestError(
                f"Unable to read {ARTIFACT_MANIFEST_FILENAME}: {self.manifest_path}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ManifestError("ARTIFACT_MANIFEST.json must contain an object")
        if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise ManifestError("Unsupported ARTIFACT_MANIFEST.json schema_version")
        raw_records = payload.get("artifacts")
        if not isinstance(raw_records, list):
            raise ManifestError("ARTIFACT_MANIFEST.json artifacts must be a list")
        records = tuple(self._record_from_dict(item) for item in raw_records)
        self._validate_records_locked(records, payload)
        self._records = records

    def _reload_locked(self) -> None:
        if not self.manifest_path.exists():
            raise ManifestIntegrityError(
                "ARTIFACT_MANIFEST.json disappeared; immutable history cannot be rewritten"
            )
        self._load_locked()

    @staticmethod
    def _manifest_payload(records: tuple[ArtifactRecord, ...]) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "record_count": len(records),
            "head_hash": records[-1].record_hash if records else None,
            "artifacts": [record.to_dict() for record in records],
        }

    def _write_manifest_locked(self, records: tuple[ArtifactRecord, ...]) -> None:
        _atomic_write_json(self.manifest_path, self._manifest_payload(records))

    @staticmethod
    def _logical_equal(left: ArtifactRecord, right: ArtifactRecord) -> bool:
        return left.logical_dict() == right.logical_dict()

    def register_artifact(
        self,
        artifact_id: str | Path | None = None,
        path: str | Path | None = None,
        *,
        artifact_type: str = "artifact",
        producing_action: str | None = None,
        producing_node: str | None = None,
        input_artifact_ids: Iterable[Any] | str | None = None,
        created_at: str | datetime | None = None,
        scientific_provenance: Mapping[str, Any] | None = None,
        # Friendly aliases for integrations that use shorter producer names.
        action: str | None = None,
        node: str | None = None,
        producer: str | None = None,
        inputs: Iterable[Any] | str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> ArtifactRecord:
        """Register an existing file and append its immutable lineage record."""

        # ``register_artifact(path, artifact_id="...")`` is provided by the
        # path-first ``register_file`` wrapper.  This small convenience also
        # accepts a Path as the first positional argument with an auto ID.
        if path is None and isinstance(artifact_id, Path):
            path = artifact_id
            artifact_id = None
        if path is None and isinstance(artifact_id, str):
            looks_like_path = (
                "/" in artifact_id
                or "\\" in artifact_id
                or bool(Path(artifact_id).suffix)
                or (self.run_root / artifact_id).exists()
            )
            if looks_like_path:
                path = artifact_id
                artifact_id = None
        if path is None:
            raise ArtifactLineageError("path is required when registering an artifact")

        selected_action = producing_action
        if selected_action is None:
            selected_action = action
        if selected_action is None:
            selected_action = producer
        if selected_action is None:
            selected_action = "register_artifact"
        if action is not None and producing_action is not None and action != producing_action:
            raise ArtifactLineageError("action and producing_action disagree")
        if node is not None and producing_node is not None and node != producing_node:
            raise ArtifactLineageError("node and producing_node disagree")
        selected_node = producing_node if producing_node is not None else node
        selected_inputs = input_artifact_ids if input_artifact_ids is not None else inputs
        selected_provenance = (
            scientific_provenance
            if scientific_provenance is not None
            else provenance
        )

        with self._lock:
            self._reload_locked()
            relative_path, _ = self._normalise_path(path)
            if artifact_id is None:
                artifact_id = f"{_normalise_text(artifact_type, 'artifact_type')}:{relative_path}"
            candidate = self._build_record(
                artifact_id=_normalise_artifact_id(artifact_id),
                path=path,
                artifact_type=artifact_type,
                producing_action=selected_action,
                producing_node=selected_node,
                input_artifact_ids=selected_inputs,
                created_at=created_at,
                scientific_provenance=selected_provenance,
            )
            records = self._records
            if candidate.artifact_id in candidate.input_artifact_ids:
                raise LineageCycleError(
                    f"Artifact lineage cycle detected: {candidate.artifact_id} -> "
                    f"{candidate.artifact_id}"
                )
            self._validate_input_ids(candidate, records)
            existing = next(
                (record for record in records if record.artifact_id == candidate.artifact_id),
                None,
            )
            if existing is not None:
                # Omitted timestamps are generated for new entries but are not
                # treated as a history rewrite during an idempotent retry.
                if created_at is None:
                    candidate = replace(candidate, created_at=existing.created_at)
                if self._logical_equal(candidate, existing):
                    return existing
                raise HistoryRewriteError(
                    f"Immutable artifact history forbids rewriting artifact ID "
                    f"'{candidate.artifact_id}'"
                )

            candidate = candidate.with_integrity(
                records[-1].record_hash if records else None
            )
            new_records = records + (candidate,)
            self._validate_records_locked(new_records)
            self._write_manifest_locked(new_records)
            self._records = new_records
            return candidate

    def register_file(
        self,
        path: str | Path,
        *,
        artifact_id: str | None = None,
        artifact_type: str = "artifact",
        producing_action: str | None = None,
        producing_node: str | None = None,
        input_artifact_ids: Iterable[Any] | str | None = None,
        created_at: str | datetime | None = None,
        scientific_provenance: Mapping[str, Any] | None = None,
        **aliases: Any,
    ) -> ArtifactRecord:
        return self.register_artifact(
            artifact_id=artifact_id,
            path=path,
            artifact_type=artifact_type,
            producing_action=producing_action,
            producing_node=producing_node,
            input_artifact_ids=input_artifact_ids,
            created_at=created_at,
            scientific_provenance=scientific_provenance,
            **aliases,
        )

    # Common verb aliases keep the store convenient without creating a second
    # mutation path with different validation rules.
    def register(self, *args: Any, **kwargs: Any) -> ArtifactRecord:
        return self.register_artifact(*args, **kwargs)

    def add_artifact(self, *args: Any, **kwargs: Any) -> ArtifactRecord:
        return self.register_artifact(*args, **kwargs)

    def get_artifact(self, artifact_id: str) -> ArtifactRecord:
        wanted = _normalise_artifact_id(artifact_id)
        with self._lock:
            self._reload_locked()
            for record in self._records:
                if record.artifact_id == wanted:
                    return record
        raise UnknownArtifactError(f"Unknown artifact ID '{wanted}'")

    def get(self, artifact_id: str) -> ArtifactRecord:
        return self.get_artifact(artifact_id)

    @property
    def records(self) -> tuple[ArtifactRecord, ...]:
        with self._lock:
            self._reload_locked()
            return tuple(self._records)

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        return tuple(record.artifact_id for record in self.records)

    @property
    def manifest(self) -> dict[str, Any]:
        with self._lock:
            self._reload_locked()
            return self._manifest_payload(self._records)

    def to_dict(self) -> dict[str, Any]:
        return self.manifest

    def verify(self) -> bool:
        with self._lock:
            self._reload_locked()
        return True

    def verify_all(self) -> bool:
        return self.verify()

    def validate(self) -> bool:
        return self.verify()

    def lineage(self, artifact_id: str) -> tuple[ArtifactRecord, ...]:
        """Return an artifact followed by its transitive inputs, in manifest order."""

        wanted = _normalise_artifact_id(artifact_id)
        with self._lock:
            self._reload_locked()
            by_id = {record.artifact_id: record for record in self._records}
            if wanted not in by_id:
                raise UnknownArtifactError(f"Unknown artifact ID '{wanted}'")
            selected: set[str] = set()

            def collect(current: str) -> None:
                if current in selected:
                    return
                selected.add(current)
                for parent in by_id[current].input_artifact_ids:
                    collect(parent)

            collect(wanted)
            return tuple(record for record in self._records if record.artifact_id in selected)


# Compatibility aliases for callers that name the component by its storage
# role rather than by the lineage behavior.
ArtifactManifestStore = ArtifactLineageStore
ArtifactProvenanceStore = ArtifactLineageStore
ProvenanceStore = ArtifactLineageStore
ArtifactStore = ArtifactLineageStore
ArtifactLineage = ArtifactLineageStore
ArtifactManifest = ArtifactLineageStore
LineageStore = ArtifactLineageStore


__all__ = [
    "ARTIFACT_MANIFEST_FILENAME",
    "MANIFEST_FILENAME",
    "MANIFEST_SCHEMA_VERSION",
    "ArtifactLineageError",
    "ManifestError",
    "ManifestIntegrityError",
    "ArtifactPathError",
    "ArtifactMissingError",
    "UnknownArtifactError",
    "DuplicateArtifactError",
    "HistoryRewriteError",
    "LineageCycleError",
    "ArtifactTamperedError",
    "ProvenanceError",
    "ArtifactProvenanceError",
    "PathTraversalError",
    "UnknownInputArtifactError",
    "ImmutableHistoryError",
    "ArtifactRecord",
    "ArtifactLineageStore",
    "ArtifactManifestStore",
    "ArtifactProvenanceStore",
    "ProvenanceStore",
    "ArtifactStore",
    "ArtifactLineage",
    "ArtifactManifest",
    "LineageStore",
]
