"""Stage 12B: manuscript-critical reproducibility selection and replay.

Selects only the experiments that materially support the assembled Stage 12A
manuscript, fresh-replays those critical completed runs through the existing
replay authority, produces immutable run/artifact lineage, and generates an
honest negative/failed/not-run appendix.

No model or network call is made.  Production fresh replay does invoke the
existing local TMM execution authority via ``replay.replay_completed_run``
(never ``replace_existing=True``); there is no caller-supplied manifest
bypass.  Critical
replay/provenance/hash/identity failures are hard publication blockers;
ordinary negative/failed routes are scientific assets recorded in the
appendix unless a positive manuscript claim depends on them.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from optomind_optics.harness.article_architecture import (
    ArticleArchitectureResult,
    StoryCandidate,
)
from optomind_optics.harness.article_claims import ClaimLedgerResult
from optomind_optics.harness.article_contracts import ObservationCard
from optomind_optics.harness.article_director import ArticleDirectorPlan
from optomind_optics.harness.article_execution import ArticleExecutionResult
from optomind_optics.harness.article_manuscript import (
    ArticleManuscriptPackage,
    build_article_manuscript,
    validate_manuscript_package,
)
from optomind_optics.harness.article_review import (
    ArticleReviewResult,
    validate_review_result,
)
from optomind_optics.harness.article_writing import TrustedValueRecord
from optomind_research.runtime.artifact_store import (
    atomic_write_json,
    atomic_write_text,
)


REPRODUCIBILITY_SCHEMA_VERSION = "article-reproducibility-package.v1"
CRITICAL_EXPERIMENT_SCHEMA_VERSION = "critical-experiment-record.v1"
REPLAY_RECORD_SCHEMA_VERSION = "replay-record.v1"
ARTIFACT_LINEAGE_SCHEMA_VERSION = "artifact-lineage-record.v1"
APPENDIX_RECORD_SCHEMA_VERSION = "appendix-record.v1"
PUBLICATION_BLOCKER_SCHEMA_VERSION = "publication-blocker.v1"

# Ephemeral runtime-control artifacts are not scientific evidence and are
# excluded from replay equality and lineage.  Only explicitly listed volatile
# files are ignored; every scientific artifact remains strict.
VOLATILE_RUNTIME_ARTIFACTS = frozenset({"RUNTIME_LOCK.json"})

NEGATIVE_OBSERVATION_STATUSES = frozenset(
    {"rejected_physics", "needs_higher_fidelity", "failed", "cancelled"}
)
APPENDIX_COVERAGE_STATUSES = frozenset({"failed", "not_run", "superseded"})
REPLAYABLE_STATUSES = frozenset({"physically_valid", "physically_valid_with_limits"})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ArticleReproducibilityError(ValueError):
    """Base error for reproducibility-package failures."""


class ArticleReproducibilityIntegrityError(ArticleReproducibilityError):
    """Conflicting persisted reproducibility content."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CriticalExperimentRecord(_StrictModel):
    schema_version: Literal["critical-experiment-record.v1"] = (
        "critical-experiment-record.v1"
    )
    experiment_id: str
    physical_experiment_ids: List[str] = Field(default_factory=list)
    source_run_dir: str
    observation_ids: List[str] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)
    fact_ids: List[str] = Field(default_factory=list)
    figure_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    paragraph_ids: List[str] = Field(default_factory=list)
    section_ids: List[str] = Field(default_factory=list)
    rationale: str


class ReplayRecord(_StrictModel):
    schema_version: Literal["replay-record.v1"] = "replay-record.v1"
    replay_id: str
    experiment_id: str
    physical_experiment_ids: List[str] = Field(default_factory=list)
    source_run_dir: str
    status: Literal["completed", "failed"]
    source_task_sha256: str = ""
    replay_task_sha256: str = ""
    source_run_id: str = ""
    manifest: Dict[str, Any] = Field(default_factory=dict)
    error: str = ""


class ArtifactLineageRecord(_StrictModel):
    schema_version: Literal["artifact-lineage-record.v1"] = "artifact-lineage-record.v1"
    lineage_id: str
    artifact_id: str
    experiment_id: str
    relative_path: str
    source_sha256: str = ""
    replay_sha256: str = ""
    identity_kind: Literal[
        "byte_identical",
        "canonical_scientific_identity",
        "mismatch",
        "missing",
    ]
    matched: bool


class AppendixRecord(_StrictModel):
    schema_version: Literal["appendix-record.v1"] = "appendix-record.v1"
    appendix_id: str
    kind: Literal["observation", "coverage_row"]
    observation_id: str = ""
    experiment_id: str = ""
    status: str = ""
    summary: str = ""
    failure_records: List[Dict[str, Any]] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    route_id: str = ""
    coverage_status: str = ""
    reason: str = ""


class PublicationBlocker(_StrictModel):
    schema_version: Literal["publication-blocker.v1"] = "publication-blocker.v1"
    blocker_id: str
    kind: str
    message: str
    experiment_id: str = ""
    observation_ids: List[str] = Field(default_factory=list)
    claim_ids: List[str] = Field(default_factory=list)
    artifact_ids: List[str] = Field(default_factory=list)
    paragraph_ids: List[str] = Field(default_factory=list)
    section_ids: List[str] = Field(default_factory=list)


class ArticleReproducibilityPackage(_StrictModel):
    schema_version: Literal["article-reproducibility-package.v1"] = (
        "article-reproducibility-package.v1"
    )
    package_id: str
    plan_id: str
    ledger_id: str
    architecture_id: str
    review_id: str
    result_id: str
    manuscript_body_id: str
    story_id: str
    status: Literal["ready", "ready_with_findings", "blocked"]
    critical_experiments: List[CriticalExperimentRecord] = Field(default_factory=list)
    replay_records: List[ReplayRecord] = Field(default_factory=list)
    lineage: List[ArtifactLineageRecord] = Field(default_factory=list)
    appendix: List[AppendixRecord] = Field(default_factory=list)
    blockers: List[PublicationBlocker] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    model_name: Literal["none"] = "none"
    usage: Dict[str, Any] = Field(default_factory=dict)
    attempts: int = 0


ReplayProvider = Callable[[str | Path], Any]


def _default_replay_provider(source_run_dir: str | Path) -> Any:
    from optomind_optics.harness.replay import replay_completed_run

    return replay_completed_run(Path(source_run_dir))


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(*parts: Any) -> str:
    return hashlib.sha256(
        _canonical_json([str(part) for part in parts]).encode("utf-8")
    ).hexdigest()[:16]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_equal(left_text: str, right_text: str) -> bool:
    try:
        return json.loads(left_text) == json.loads(right_text)
    except json.JSONDecodeError:
        return False


def _safe_within(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _validate_artifact_path(artifact_id: str, relative_path: str) -> Optional[str]:
    text = str(relative_path or "").strip()
    if not text:
        return f"artifact {artifact_id!r} has an empty path"
    if "\x00" in text:
        return f"artifact {artifact_id!r} path contains NUL"
    path = Path(text)
    if path.is_absolute():
        return f"artifact {artifact_id!r} path is absolute"
    if ".." in path.parts:
        return f"artifact {artifact_id!r} path contains traversal"
    return None


def _hex64(value: str) -> bool:
    return bool(_SHA256_RE.match(str(value or "").strip().lower()))


def _receipt_identity(receipt: Mapping[str, Any]) -> Dict[str, List[str]]:
    """Collect canonical nested telemetry plus legacy top-level aliases."""

    candidates: Dict[str, List[str]] = {}
    telemetry = receipt.get("telemetry") if isinstance(receipt, Mapping) else None
    if not isinstance(telemetry, Mapping):
        telemetry = {}

    def collect(section: Mapping[str, Any], source: str) -> None:
        for key in ("task_hash", "request_id", "run_id", "run_dir"):
            value = section.get(key)
            if value is not None and str(value).strip():
                candidates.setdefault(key, []).append((source, str(value).strip()))

    collect(receipt, "top")
    collect(telemetry, "telemetry")
    return {
        key: sorted({value for _, value in entries})
        for key, entries in candidates.items()
    }


def _resolve_identity_value(
    candidates: Dict[str, List[str]],
    key: str,
    *,
    blockers: List[PublicationBlocker],
    context: str,
    experiment_id: str = "",
) -> str:
    values = candidates.get(key, [])
    if not values:
        return ""
    if len(values) > 1:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(context, key, 'conflicting_receipt')}",
                kind="execution_identity_mismatch",
                message=(
                    f"receipt identity {key} has conflicting values "
                    f"{values} in {context}"
                ),
                experiment_id=experiment_id,
            )
        )
        return ""
    return values[0]


def _resolve_receipt_run_dir(
    receipt_run_dir: str, runs_root: Path
) -> Tuple[Optional[Path], Optional[str]]:
    """Resolve a receipt run_dir: absolute paths directly, relative paths
    against the trusted runs_root; reject escapes after symlink resolution."""

    text = str(receipt_run_dir or "").strip()
    if not text:
        return None, "receipt run_dir is empty"
    if "\x00" in text:
        return None, "receipt run_dir contains NUL"
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            return candidate.resolve(), None
        except OSError as exc:
            return None, f"receipt run_dir cannot be resolved: {exc}"
    try:
        resolved = (runs_root / candidate).resolve()
    except OSError as exc:
        return None, f"receipt run_dir cannot be resolved: {exc}"
    if not _safe_within(resolved, runs_root):
        return None, (f"receipt run_dir {receipt_run_dir!r} resolves outside runs_root")
    return resolved, None


def _resolve_artifact_candidates(
    run_dir: Path,
    runs_root: Path,
    descriptor: Any,
) -> List[Path]:
    """Ordered safe candidates for a manuscript-used artifact's bytes.

    Prefers a matching run-local artifact_id reference, then the descriptor
    path inside the run, then a global-relative descriptor path constrained
    to the trusted runs_root.
    """

    candidates: List[Path] = []

    def add(candidate: Path, root: Path) -> None:
        if _safe_within(candidate, root):
            resolved = candidate.resolve()
            if resolved not in [item.resolve() for item in candidates]:
                candidates.append(resolved)

    add(run_dir / descriptor.artifact_id, run_dir)
    descriptor_path = Path(str(descriptor.path or ""))
    if (
        not descriptor_path.is_absolute()
        and ".." not in descriptor_path.parts
        and "\x00" not in str(descriptor_path)
    ):
        add(run_dir / descriptor_path, run_dir)
        add(runs_root / descriptor_path, runs_root)
    return candidates


def compute_reproducibility_package_id(
    *,
    plan_id: str,
    ledger_id: str,
    architecture_id: str,
    review_id: str,
    result_id: str,
    manuscript_body_id: str,
    story_id: str,
    status: str,
    critical_experiments: Sequence[CriticalExperimentRecord | Mapping[str, Any]],
    replay_records: Sequence[ReplayRecord | Mapping[str, Any]],
    lineage: Sequence[ArtifactLineageRecord | Mapping[str, Any]],
    appendix: Sequence[AppendixRecord | Mapping[str, Any]],
    blockers: Sequence[PublicationBlocker | Mapping[str, Any]],
    warnings: Sequence[str],
    errors: Sequence[str],
    attempts: int,
) -> str:
    """Content-addressed package identity over the complete result."""

    def _models(values: Sequence[Any], model_type: Any) -> List[Any]:
        return [
            item if isinstance(item, model_type) else model_type.model_validate(item)
            for item in values
        ]

    return _digest(
        str(plan_id),
        str(ledger_id),
        str(architecture_id),
        str(review_id),
        str(result_id),
        str(manuscript_body_id),
        str(story_id),
        str(status),
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(critical_experiments, CriticalExperimentRecord)
        ],
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(replay_records, ReplayRecord)
        ],
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(lineage, ArtifactLineageRecord)
        ],
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(appendix, AppendixRecord)
        ],
        [
            _canonical_json(item.model_dump(mode="json"))
            for item in _models(blockers, PublicationBlocker)
        ],
        [str(item) for item in warnings],
        [str(item) for item in errors],
        int(attempts),
    )


def _discover_critical_experiments(
    *,
    manuscript: ArticleManuscriptPackage,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    story: StoryCandidate,
    observation_by_id: Mapping[str, ObservationCard],
    errors: List[str],
) -> List[CriticalExperimentRecord]:
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    facts_by_id = {fact.fact_id: fact for fact in ledger.facts}
    inventory_by_id = {
        item.artifact_id: item for item in architecture.artifact_inventory
    }
    figure_by_id = {item.figure_id: item for item in story.figure_contracts}
    found: Dict[str, Dict[str, set]] = {}

    def add(
        experiment_id: str,
        *,
        physical_experiment_ids: Sequence[str] = (),
        observation_ids: Sequence[str] = (),
        claim_ids: Sequence[str] = (),
        fact_ids: Sequence[str] = (),
        figure_ids: Sequence[str] = (),
        artifact_ids: Sequence[str] = (),
        paragraph_id: str = "",
        section_id: str = "",
    ) -> None:
        record = found.setdefault(
            experiment_id,
            {
                "physical_experiment_ids": set(),
                "observation_ids": set(),
                "claim_ids": set(),
                "fact_ids": set(),
                "figure_ids": set(),
                "artifact_ids": set(),
                "paragraph_ids": set(),
                "section_ids": set(),
            },
        )
        record["physical_experiment_ids"].update(physical_experiment_ids)
        record["observation_ids"].update(observation_ids)
        record["claim_ids"].update(claim_ids)
        record["fact_ids"].update(fact_ids)
        record["figure_ids"].update(figure_ids)
        record["artifact_ids"].update(artifact_ids)
        if paragraph_id:
            record["paragraph_ids"].add(paragraph_id)
        if section_id:
            record["section_ids"].add(section_id)

    def follow_descriptor(
        descriptor: Any,
        *,
        artifact_id: str,
        figure_ids: Sequence[str] = (),
        fact_ids: Sequence[str] = (),
        paragraph_id: str,
        section_id: str,
    ) -> None:
        for obs_id in descriptor.source_observation_ids:
            observation = observation_by_id.get(obs_id)
            if observation is not None:
                add(
                    observation.experiment_id,
                    physical_experiment_ids=descriptor.source_experiment_ids,
                    observation_ids=[obs_id],
                    artifact_ids=[artifact_id],
                    figure_ids=list(figure_ids),
                    fact_ids=list(fact_ids),
                    paragraph_id=paragraph_id,
                    section_id=section_id,
                )

    for paragraph in manuscript.source_map:
        for claim_id in paragraph.claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                errors.append(
                    f"manuscript paragraph {paragraph.paragraph_id!r} "
                    f"references unknown claim {claim_id!r}"
                )
                continue
            fact_id = claim.metadata.get("fact_id")
            for obs_id in claim.evidence_ids:
                observation = observation_by_id.get(obs_id)
                if observation is None:
                    continue
                add(
                    observation.experiment_id,
                    observation_ids=[obs_id],
                    claim_ids=[claim_id],
                    fact_ids=[fact_id] if fact_id else [],
                    paragraph_id=paragraph.paragraph_id,
                    section_id=paragraph.section_id,
                )
        for fact_id in paragraph.fact_ids:
            fact = facts_by_id.get(fact_id)
            if fact is None:
                errors.append(
                    f"manuscript paragraph {paragraph.paragraph_id!r} "
                    f"references unknown fact {fact_id!r}"
                )
                continue
            for artifact_id in fact.source_artifact_ids:
                descriptor = inventory_by_id.get(artifact_id)
                if descriptor is None:
                    errors.append(
                        f"fact {fact_id!r} references unknown artifact "
                        f"{artifact_id!r}"
                    )
                    continue
                follow_descriptor(
                    descriptor,
                    artifact_id=artifact_id,
                    fact_ids=[fact_id],
                    paragraph_id=paragraph.paragraph_id,
                    section_id=paragraph.section_id,
                )
        for artifact_id in paragraph.artifact_ids:
            descriptor = inventory_by_id.get(artifact_id)
            if descriptor is None:
                errors.append(
                    f"manuscript paragraph {paragraph.paragraph_id!r} "
                    f"references unknown artifact {artifact_id!r}"
                )
                continue
            follow_descriptor(
                descriptor,
                artifact_id=artifact_id,
                paragraph_id=paragraph.paragraph_id,
                section_id=paragraph.section_id,
            )
        for figure_id in paragraph.figure_ids:
            figure = figure_by_id.get(figure_id)
            if figure is None:
                errors.append(
                    f"manuscript paragraph {paragraph.paragraph_id!r} "
                    f"references unknown figure {figure_id!r}"
                )
                continue
            for binding in figure.claim_bindings:
                claim = claims_by_id.get(binding.claim_id)
                if claim is None:
                    continue
                fact_id = claim.metadata.get("fact_id")
                for obs_id in claim.evidence_ids:
                    observation = observation_by_id.get(obs_id)
                    if observation is None:
                        continue
                    add(
                        observation.experiment_id,
                        observation_ids=[obs_id],
                        claim_ids=[binding.claim_id],
                        fact_ids=[fact_id] if fact_id else [],
                        figure_ids=[figure_id],
                        paragraph_id=paragraph.paragraph_id,
                        section_id=paragraph.section_id,
                    )
            for binding in figure.artifact_bindings:
                descriptor = inventory_by_id.get(binding.artifact_id)
                if descriptor is None:
                    continue
                follow_descriptor(
                    descriptor,
                    artifact_id=binding.artifact_id,
                    figure_ids=[figure_id],
                    paragraph_id=paragraph.paragraph_id,
                    section_id=paragraph.section_id,
                )

    records: List[CriticalExperimentRecord] = []
    for experiment_id in sorted(found):
        record = found[experiment_id]
        rationale_parts = []
        if record["observation_ids"]:
            rationale_parts.append(f"observations {sorted(record['observation_ids'])}")
        if record["claim_ids"]:
            rationale_parts.append(f"claims {sorted(record['claim_ids'])}")
        if record["fact_ids"]:
            rationale_parts.append(f"facts {sorted(record['fact_ids'])}")
        if record["figure_ids"]:
            rationale_parts.append(f"figures {sorted(record['figure_ids'])}")
        if record["artifact_ids"]:
            rationale_parts.append(f"artifacts {sorted(record['artifact_ids'])}")
        records.append(
            CriticalExperimentRecord(
                experiment_id=experiment_id,
                physical_experiment_ids=sorted(record["physical_experiment_ids"]),
                source_run_dir="",
                observation_ids=sorted(record["observation_ids"]),
                claim_ids=sorted(record["claim_ids"]),
                fact_ids=sorted(record["fact_ids"]),
                figure_ids=sorted(record["figure_ids"]),
                artifact_ids=sorted(record["artifact_ids"]),
                paragraph_ids=sorted(record["paragraph_ids"]),
                section_ids=sorted(record["section_ids"]),
                rationale="; ".join(rationale_parts) or "manuscript-linked",
            )
        )
    return records


def _nested_physical_experiment_ids(
    observation: ObservationCard,
) -> set[str]:
    experiments = (observation.metrics or {}).get("experiments")
    if not isinstance(experiments, list):
        return set()
    physical: set[str] = set()
    for item in experiments:
        if not isinstance(item, Mapping):
            continue
        value = item.get("experiment_id")
        if isinstance(value, str) and value.strip():
            physical.add(value.strip())
    return physical


def _validate_provenance(
    *,
    ledger: ClaimLedgerResult,
    architecture: ArticleArchitectureResult,
    manuscript: ArticleManuscriptPackage,
    execution_results: Sequence[ArticleExecutionResult],
    runs_root: Path,
    critical: Sequence[CriticalExperimentRecord],
    errors: List[str],
    warnings: List[str],
    blockers: List[PublicationBlocker],
) -> Tuple[
    Dict[str, ArticleExecutionResult],
    Dict[str, ObservationCard],
    set[str],
    Dict[str, str],
]:
    claims_by_id = {claim.claim_id: claim for claim in ledger.claims}
    if len(claims_by_id) != len(ledger.claims):
        errors.append("ledger contains duplicate claim IDs")
    fact_ids = {fact.fact_id for fact in ledger.facts}
    if len(fact_ids) != len(ledger.facts):
        errors.append("ledger contains duplicate fact IDs")
    inventory_by_id = {
        item.artifact_id: item for item in architecture.artifact_inventory
    }
    if len(inventory_by_id) != len(architecture.artifact_inventory):
        errors.append("artifact inventory contains duplicate artifact IDs")
    execution_by_experiment: Dict[str, ArticleExecutionResult] = {}
    observation_by_id: Dict[str, ObservationCard] = {}
    for result in execution_results:
        observation = result.observation
        if observation.observation_id in observation_by_id:
            errors.append(
                f"duplicate observation {observation.observation_id!r} in "
                "execution results"
            )
        observation_by_id[observation.observation_id] = observation
        if observation.experiment_id in execution_by_experiment:
            errors.append(
                f"duplicate experiment {observation.experiment_id!r} in "
                "execution results"
            )
        execution_by_experiment[observation.experiment_id] = result
        if result.outcome != observation.status.value:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(observation.experiment_id, 'outcome_mismatch')}",
                    kind="execution_identity_mismatch",
                    message=(
                        f"execution outcome {result.outcome!r} does not match "
                        f"observation status {observation.status.value!r} for "
                        f"experiment {observation.experiment_id!r}"
                    ),
                    experiment_id=observation.experiment_id,
                    observation_ids=[observation.observation_id],
                )
            )
    if errors:
        return execution_by_experiment, observation_by_id, set(), {}

    facts_by_id = {fact.fact_id: fact for fact in ledger.facts}
    manuscript_used_artifacts: set[str] = set()
    for paragraph in manuscript.source_map:
        manuscript_used_artifacts.update(paragraph.artifact_ids)
        for fact_id in paragraph.fact_ids:
            fact = facts_by_id.get(fact_id)
            if fact is not None:
                manuscript_used_artifacts.update(fact.source_artifact_ids)
    for artifact_id in sorted(manuscript_used_artifacts):
        descriptor = inventory_by_id.get(artifact_id)
        if descriptor is None:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(artifact_id, 'unknown_artifact')}",
                    kind="unknown_artifact",
                    message=(
                        f"manuscript artifact {artifact_id!r} has no Stage 9 "
                        "artifact descriptor"
                    ),
                    artifact_ids=[artifact_id],
                )
            )
            continue
        if not descriptor.sha256:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(artifact_id, 'missing_hash')}",
                    kind="missing_hash",
                    message=(
                        f"manuscript-critical artifact {artifact_id!r} has no "
                        "sha256 in the artifact inventory"
                    ),
                    artifact_ids=[artifact_id],
                )
            )
        if descriptor.source_experiment_ids and not descriptor.source_observation_ids:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(artifact_id, 'orphan_physical_source')}",
                    kind="source_provenance_mismatch",
                    message=(
                        f"artifact {artifact_id!r} declares physical source "
                        f"experiments {descriptor.source_experiment_ids} but "
                        "no source observation to bind them to an Article "
                        "execution"
                    ),
                    artifact_ids=[artifact_id],
                )
            )
        for obs_id in descriptor.source_observation_ids:
            observation = observation_by_id.get(obs_id)
            if observation is None:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest(artifact_id, obs_id, 'unknown_source_observation')}",
                        kind="source_provenance_mismatch",
                        message=(
                            f"artifact {artifact_id!r} references unknown "
                            f"source observation {obs_id!r}"
                        ),
                        artifact_ids=[artifact_id],
                        observation_ids=[obs_id],
                    )
                )
            elif observation.experiment_id not in execution_by_experiment:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest(artifact_id, obs_id, 'missing_execution')}",
                        kind="missing_execution",
                        message=(
                            f"artifact {artifact_id!r} source observation "
                            f"{obs_id!r} belongs to experiment "
                            f"{observation.experiment_id!r} with no trusted "
                            "execution result"
                        ),
                        artifact_ids=[artifact_id],
                        observation_ids=[obs_id],
                        experiment_id=observation.experiment_id,
                    )
                )
            else:
                execution = execution_by_experiment[observation.experiment_id]
                nested_physical = _nested_physical_experiment_ids(execution.observation)
                if nested_physical:
                    declared = set(descriptor.source_experiment_ids)
                    if not declared or not declared <= nested_physical:
                        blockers.append(
                            PublicationBlocker(
                                blocker_id=f"blocker-{_digest(artifact_id, obs_id, 'cross_wired_source')}",
                                kind="source_provenance_mismatch",
                                message=(
                                    f"artifact {artifact_id!r} source "
                                    f"observation {obs_id!r} belongs to "
                                    f"Article experiment "
                                    f"{observation.experiment_id!r} whose "
                                    "nested physical experiments "
                                    f"{sorted(nested_physical)} do not contain "
                                    "the declared non-empty source_experiment_ids "
                                    f"{descriptor.source_experiment_ids}"
                                ),
                                artifact_ids=[artifact_id],
                                observation_ids=[obs_id],
                                experiment_id=observation.experiment_id,
                            )
                        )
                elif (
                    descriptor.source_experiment_ids
                    and observation.experiment_id
                    not in descriptor.source_experiment_ids
                ):
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(artifact_id, obs_id, 'cross_wired_source')}",
                            kind="source_provenance_mismatch",
                            message=(
                                f"artifact {artifact_id!r} source observation "
                                f"{obs_id!r} belongs to experiment "
                                f"{observation.experiment_id!r}, which is not "
                                "in its source_experiment_ids "
                                f"{descriptor.source_experiment_ids}"
                            ),
                            artifact_ids=[artifact_id],
                            observation_ids=[obs_id],
                            experiment_id=observation.experiment_id,
                        )
                    )

    for paragraph in manuscript.source_map:
        for claim_id in paragraph.claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is None:
                continue
            for obs_id in claim.evidence_ids:
                if obs_id not in observation_by_id:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(claim_id, obs_id, 'missing_execution')}",
                            kind="missing_execution",
                            message=(
                                f"claim {claim_id!r} in paragraph "
                                f"{paragraph.paragraph_id!r} references "
                                f"observation {obs_id!r} with no trusted "
                                "execution result"
                            ),
                            observation_ids=[obs_id],
                            claim_ids=[claim_id],
                            paragraph_ids=[paragraph.paragraph_id],
                            section_ids=[paragraph.section_id],
                        )
                    )

    replayable: set[str] = set()
    expected_run_ids: Dict[str, str] = {}
    seen_experiments: set[str] = set()
    for record in critical:
        if record.experiment_id in seen_experiments:
            errors.append(f"duplicate critical experiment {record.experiment_id!r}")
        seen_experiments.add(record.experiment_id)
        execution = execution_by_experiment.get(record.experiment_id)
        if execution is None:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, 'missing_execution')}",
                    kind="missing_execution",
                    message=(
                        f"manuscript depends on experiment "
                        f"{record.experiment_id!r} but no trusted execution "
                        "result exists"
                    ),
                    experiment_id=record.experiment_id,
                    observation_ids=list(record.observation_ids),
                    claim_ids=list(record.claim_ids),
                    artifact_ids=list(record.artifact_ids),
                    paragraph_ids=list(record.paragraph_ids),
                    section_ids=list(record.section_ids),
                )
            )
            continue
        observation = execution.observation
        if observation.experiment_id != record.experiment_id:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, 'mismatched_identity')}",
                    kind="mismatched_identity",
                    message=(
                        f"execution observation {observation.observation_id!r} "
                        f"experiment_id does not match critical experiment "
                        f"{record.experiment_id!r}"
                    ),
                    experiment_id=record.experiment_id,
                    observation_ids=[observation.observation_id],
                )
            )
            continue
        if observation.status.value not in REPLAYABLE_STATUSES:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, 'non_physical_source')}",
                    kind="non_physical_source",
                    message=(
                        f"manuscript depends on observation "
                        f"{observation.observation_id!r} with status "
                        f"{observation.status.value!r}; a positive claim "
                        "requires a physically valid source"
                    ),
                    experiment_id=record.experiment_id,
                    observation_ids=[observation.observation_id],
                    claim_ids=list(record.claim_ids),
                    paragraph_ids=list(record.paragraph_ids),
                    section_ids=list(record.section_ids),
                )
            )
            continue
        if observation.status.value == "physically_valid_with_limits":
            warnings.append(
                f"experiment {record.experiment_id!r} source observation "
                f"{observation.observation_id!r} is physically valid with "
                "limits; replay proceeds with limitation semantics preserved"
            )
        if not execution.task_hash.strip():
            errors.append(
                f"execution for experiment {record.experiment_id!r} has an "
                "empty task_hash"
            )
        if not execution.run_dir.strip():
            errors.append(
                f"execution for experiment {record.experiment_id!r} has an "
                "empty run_dir"
            )
        run_dir = Path(execution.run_dir)
        if not _safe_within(run_dir, runs_root):
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, 'traversal')}",
                    kind="path_traversal",
                    message=(
                        f"source run dir {execution.run_dir!r} resolves "
                        f"outside runs_root for experiment "
                        f"{record.experiment_id!r}"
                    ),
                    experiment_id=record.experiment_id,
                    paragraph_ids=list(record.paragraph_ids),
                    section_ids=list(record.section_ids),
                )
            )
            continue
        if not run_dir.is_dir():
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, 'not_a_directory')}",
                    kind="missing_source_run",
                    message=(
                        f"source run path {execution.run_dir!r} is not a "
                        f"directory for experiment {record.experiment_id!r}"
                    ),
                    experiment_id=record.experiment_id,
                    paragraph_ids=list(record.paragraph_ids),
                    section_ids=list(record.section_ids),
                )
            )
            continue
        task_hash = str(execution.task_hash or "").strip()
        request_id = str(execution.request_id or "").strip()
        receipt_identity = _receipt_identity(execution.receipt or {})
        receipt_task = _resolve_identity_value(
            receipt_identity,
            "task_hash",
            blockers=blockers,
            context=f"experiment {record.experiment_id!r}",
            experiment_id=record.experiment_id,
        )
        receipt_request = _resolve_identity_value(
            receipt_identity,
            "request_id",
            blockers=blockers,
            context=f"experiment {record.experiment_id!r}",
            experiment_id=record.experiment_id,
        )
        receipt_run_id = _resolve_identity_value(
            receipt_identity,
            "run_id",
            blockers=blockers,
            context=f"experiment {record.experiment_id!r}",
            experiment_id=record.experiment_id,
        )
        receipt_run_dir = _resolve_identity_value(
            receipt_identity,
            "run_dir",
            blockers=blockers,
            context=f"experiment {record.experiment_id!r}",
            experiment_id=record.experiment_id,
        )
        if receipt_task and receipt_task != task_hash:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, 'receipt_task_mismatch')}",
                    kind="task_identity_mismatch",
                    message=(
                        f"execution receipt task_hash {receipt_task!r} does "
                        f"not match execution task_hash {task_hash!r} for "
                        f"experiment {record.experiment_id!r}"
                    ),
                    experiment_id=record.experiment_id,
                )
            )
        if receipt_request and receipt_request != request_id:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, 'receipt_request_mismatch')}",
                    kind="execution_identity_mismatch",
                    message=(
                        f"execution receipt request_id {receipt_request!r} "
                        f"does not match execution request_id {request_id!r} "
                        f"for experiment {record.experiment_id!r}"
                    ),
                    experiment_id=record.experiment_id,
                )
            )
        if receipt_run_dir:
            resolved_receipt_run_dir, receipt_run_dir_error = _resolve_receipt_run_dir(
                receipt_run_dir, runs_root
            )
            if receipt_run_dir_error is not None:
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest(record.experiment_id, 'receipt_run_dir_escape')}",
                        kind="execution_identity_mismatch",
                        message=(
                            f"execution receipt run_dir {receipt_run_dir!r} "
                            f"is invalid for experiment "
                            f"{record.experiment_id!r}: {receipt_run_dir_error}"
                        ),
                        experiment_id=record.experiment_id,
                    )
                )
            elif resolved_receipt_run_dir is not None and (
                resolved_receipt_run_dir != run_dir.resolve()
            ):
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest(record.experiment_id, 'receipt_run_dir_mismatch')}",
                        kind="execution_identity_mismatch",
                        message=(
                            f"execution receipt run_dir {receipt_run_dir!r} "
                            f"(resolved {resolved_receipt_run_dir}) does not "
                            f"match execution run_dir {execution.run_dir!r} "
                            f"for experiment {record.experiment_id!r}"
                        ),
                        experiment_id=record.experiment_id,
                    )
                )
        marker_path = run_dir / "EXECUTION_MARKER.json"
        marker_run_id = ""
        if marker_path.exists():
            if not _safe_within(marker_path, run_dir):
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest(record.experiment_id, 'marker_escape')}",
                        kind="execution_identity_mismatch",
                        message=(
                            f"execution marker for experiment "
                            f"{record.experiment_id!r} resolves outside the "
                            "source run directory"
                        ),
                        experiment_id=record.experiment_id,
                    )
                )
                marker = None
            else:
                marker: Any = None
                try:
                    marker = json.loads(marker_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(record.experiment_id, 'marker_malformed')}",
                            kind="execution_identity_mismatch",
                            message=(
                                f"execution marker for experiment "
                                f"{record.experiment_id!r} is malformed"
                            ),
                            experiment_id=record.experiment_id,
                        )
                    )
            if marker is not None and not isinstance(marker, dict):
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest(record.experiment_id, 'marker_not_object')}",
                        kind="execution_identity_mismatch",
                        message=(
                            f"execution marker for experiment "
                            f"{record.experiment_id!r} is not a JSON object"
                        ),
                        experiment_id=record.experiment_id,
                    )
                )
                marker = None
            if marker is not None:
                missing_fields = [
                    field
                    for field in ("task_hash", "request_id", "run_id", "status")
                    if not str(marker.get(field) or "").strip()
                ]
                if missing_fields:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(record.experiment_id, 'marker_missing_fields')}",
                            kind="execution_identity_mismatch",
                            message=(
                                f"execution marker for experiment "
                                f"{record.experiment_id!r} is missing fields "
                                f"{missing_fields}"
                            ),
                            experiment_id=record.experiment_id,
                        )
                    )
                marker_task = str(marker.get("task_hash") or "").strip()
                marker_request = str(marker.get("request_id") or "").strip()
                marker_run_id = str(marker.get("run_id") or "").strip()
                marker_status = str(marker.get("status") or "").strip()
                if marker_task and marker_task != task_hash:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(record.experiment_id, 'marker_task_mismatch')}",
                            kind="task_identity_mismatch",
                            message=(
                                f"execution marker task_hash {marker_task!r} "
                                f"does not match execution task_hash "
                                f"{task_hash!r} for experiment "
                                f"{record.experiment_id!r}"
                            ),
                            experiment_id=record.experiment_id,
                        )
                    )
                if marker_request and marker_request != request_id:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(record.experiment_id, 'marker_request_mismatch')}",
                            kind="execution_identity_mismatch",
                            message=(
                                f"execution marker request_id {marker_request!r} "
                                f"does not match execution request_id "
                                f"{request_id!r} for experiment "
                                f"{record.experiment_id!r}"
                            ),
                            experiment_id=record.experiment_id,
                        )
                    )
                if marker_status != "completed":
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(record.experiment_id, 'marker_status_mismatch')}",
                            kind="execution_identity_mismatch",
                            message=(
                                f"execution marker status {marker_status!r} is "
                                f"not 'completed' for experiment "
                                f"{record.experiment_id!r}"
                            ),
                            experiment_id=record.experiment_id,
                        )
                    )
        task_path = run_dir / "TASK.json"
        if task_path.exists():
            try:
                task_payload = json.loads(task_path.read_text(encoding="utf-8"))
                task_task = str(task_payload.get("task_hash") or "").strip()
                if task_task and task_task != task_hash:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(record.experiment_id, 'task_file_mismatch')}",
                            kind="task_identity_mismatch",
                            message=(
                                f"source TASK.json task_hash {task_task!r} "
                                f"does not match execution task_hash "
                                f"{task_hash!r} for experiment "
                                f"{record.experiment_id!r}"
                            ),
                            experiment_id=record.experiment_id,
                        )
                    )
            except (OSError, json.JSONDecodeError):
                warnings.append(
                    f"source TASK.json for experiment {record.experiment_id!r} "
                    "is unreadable; task identity not verified"
                )
        final_result_run_id = ""
        final_result_path = run_dir / "FINAL_RESULT.json"
        if final_result_path.is_file():
            try:
                final_payload = json.loads(
                    final_result_path.read_text(encoding="utf-8")
                )
                final_result_run_id = str(final_payload.get("run_id") or "").strip()
            except (OSError, json.JSONDecodeError):
                warnings.append(
                    f"FINAL_RESULT.json for experiment "
                    f"{record.experiment_id!r} is unreadable; run identity "
                    "not verified"
                )
        run_id_sources = [
            ("receipt", receipt_run_id),
            ("final", final_result_run_id),
            ("marker", marker_run_id),
        ]
        non_empty_run_ids = [
            (source, value) for source, value in run_id_sources if value
        ]
        for left_index, (left_source, left_value) in enumerate(non_empty_run_ids):
            for right_source, right_value in non_empty_run_ids[left_index + 1 :]:
                if left_value != right_value:
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(record.experiment_id, left_source, right_source, 'run_id_conflict')}",
                            kind="source_run_id_mismatch",
                            message=(
                                f"source run identity conflicts for experiment "
                                f"{record.experiment_id!r}: {left_source} "
                                f"{left_value!r} vs {right_source} "
                                f"{right_value!r}"
                            ),
                            experiment_id=record.experiment_id,
                        )
                    )
        expected_run_id = non_empty_run_ids[0][1] if non_empty_run_ids else ""
        if expected_run_id:
            expected_run_ids[record.experiment_id] = expected_run_id

        blocked = any(
            item.kind
            in {
                "task_identity_mismatch",
                "execution_identity_mismatch",
                "source_run_id_mismatch",
                "non_physical_source",
                "mismatched_identity",
            }
            for item in blockers
            if item.experiment_id == record.experiment_id
        )
        for artifact_id in sorted(record.artifact_ids):
            descriptor = inventory_by_id.get(artifact_id)
            if descriptor is None:
                continue
            candidates = _resolve_artifact_candidates(run_dir, runs_root, descriptor)
            artifact_path = next((item for item in candidates if item.exists()), None)
            if artifact_path is None:
                blocked = True
                blockers.append(
                    PublicationBlocker(
                        blocker_id=f"blocker-{_digest(record.experiment_id, artifact_id, 'missing_artifact')}",
                        kind="missing_source_artifact",
                        message=(
                            f"manuscript artifact {artifact_id!r} is missing "
                            f"for experiment {record.experiment_id!r}"
                        ),
                        experiment_id=record.experiment_id,
                        artifact_ids=[artifact_id],
                    )
                )
                continue
            if descriptor.sha256:
                actual_hash = _sha256_file(artifact_path)
                if actual_hash != descriptor.sha256:
                    blocked = True
                    blockers.append(
                        PublicationBlocker(
                            blocker_id=f"blocker-{_digest(record.experiment_id, artifact_id, 'hash_mismatch')}",
                            kind="hash_mismatch",
                            message=(
                                f"source artifact {artifact_id!r} sha256 does "
                                "not match the artifact inventory"
                            ),
                            experiment_id=record.experiment_id,
                            artifact_ids=[artifact_id],
                        )
                    )
        for artifact_id in sorted(
            set(observation.artifact_ids) - set(record.artifact_ids)
        ):
            path_error = _validate_artifact_path(artifact_id, artifact_id)
            if path_error:
                warnings.append(
                    f"observation artifact ref {artifact_id!r} is unsafe: "
                    f"{path_error}"
                )
                continue
            ref_path = (run_dir / artifact_id).resolve()
            if not _safe_within(ref_path, run_dir):
                warnings.append(
                    f"observation artifact ref {artifact_id!r} resolves "
                    "outside the source run"
                )
                continue
            if not ref_path.exists():
                warnings.append(
                    f"observation artifact ref {artifact_id!r} is missing "
                    f"from source run {record.experiment_id!r}"
                )
        if not blocked:
            replayable.add(record.experiment_id)
    poisoned: set[str] = set()
    for item in blockers:
        if item.experiment_id:
            poisoned.add(item.experiment_id)
        for obs_id in item.observation_ids:
            observation = observation_by_id.get(obs_id)
            if observation is not None:
                poisoned.add(observation.experiment_id)
        for artifact_id in item.artifact_ids:
            descriptor = inventory_by_id.get(artifact_id)
            if descriptor is not None:
                physical = set(descriptor.source_experiment_ids)
                poisoned.update(physical)
                for execution in execution_by_experiment.values():
                    nested = _nested_physical_experiment_ids(execution.observation)
                    if nested & physical:
                        poisoned.add(execution.observation.experiment_id)
        for claim_id in item.claim_ids:
            claim = claims_by_id.get(claim_id)
            if claim is not None:
                for obs_id in claim.evidence_ids:
                    observation = observation_by_id.get(obs_id)
                    if observation is not None:
                        poisoned.add(observation.experiment_id)
    replayable -= poisoned
    return (
        execution_by_experiment,
        observation_by_id,
        replayable,
        expected_run_ids,
    )


def _verify_replay_manifest(
    *,
    record: CriticalExperimentRecord,
    run_dir: Path,
    runs_root: Path,
    manifest: Any,
    expected_source_run_id: str,
    blockers: List[PublicationBlocker],
    warnings: List[str],
) -> Tuple[bool, List[ArtifactLineageRecord]]:
    task_path = run_dir / "TASK.json"
    if not task_path.is_file():
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'source_task_missing')}",
                kind="source_task_missing",
                message=(
                    f"source run {record.experiment_id!r} has no TASK.json "
                    "to verify replay task identity"
                ),
                experiment_id=record.experiment_id,
            )
        )
        return False, []
    task_byte_sha = _sha256_file(task_path)
    checks = list(manifest.checks)
    scientific_checks = [
        check
        for check in checks
        if check.relative_path not in VOLATILE_RUNTIME_ARTIFACTS
    ]
    valid = True
    source_run_id = str(manifest.source_run_id or "").strip()
    if expected_source_run_id and source_run_id != expected_source_run_id:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'source_run_id')}",
                kind="source_run_id_mismatch",
                message=(
                    f"replay manifest source_run_id {source_run_id!r} does "
                    f"not match trusted source run identity "
                    f"{expected_source_run_id!r} for experiment "
                    f"{record.experiment_id!r}"
                ),
                experiment_id=record.experiment_id,
            )
        )
        valid = False
    if not str(manifest.replay_run_id or "").strip():
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'empty_replay_run_id')}",
                kind="replay_mismatch",
                message=(
                    f"replay manifest for experiment {record.experiment_id!r} "
                    "has an empty replay_run_id"
                ),
                experiment_id=record.experiment_id,
            )
        )
        valid = False
    for label, value in (
        ("source_task_sha256", manifest.source_task_sha256),
        ("replay_task_sha256", manifest.replay_task_sha256),
    ):
        if not _hex64(str(value or "")):
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, label)}",
                    kind="replay_mismatch",
                    message=(
                        f"replay manifest {label} {value!r} is not a "
                        "64-character hex digest for experiment "
                        f"{record.experiment_id!r}"
                    ),
                    experiment_id=record.experiment_id,
                )
            )
            valid = False
    if not checks:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'empty_checks')}",
                kind="replay_mismatch",
                message=(
                    f"replay manifest for experiment {record.experiment_id!r} "
                    "has no artifact checks"
                ),
                experiment_id=record.experiment_id,
            )
        )
        valid = False
    seen_paths: set[str] = set()
    for check in checks:
        if check.matched and (
            not _hex64(str(check.source_sha256 or ""))
            or not _hex64(str(check.replay_sha256 or ""))
        ):
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, check.relative_path, 'bad_check_hashes')}",
                    kind="replay_mismatch",
                    message=(
                        f"matched replay check {check.relative_path!r} has "
                        "non-64-hex hashes"
                    ),
                    experiment_id=record.experiment_id,
                )
            )
            valid = False
        path_error = _validate_artifact_path(check.relative_path, check.relative_path)
        if path_error:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, check.relative_path, 'unsafe_check_path')}",
                    kind="replay_mismatch",
                    message=(
                        f"replay check path {check.relative_path!r} is unsafe: "
                        f"{path_error}"
                    ),
                    experiment_id=record.experiment_id,
                )
            )
            valid = False
        if check.relative_path in seen_paths:
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, check.relative_path, 'duplicate_check')}",
                    kind="replay_mismatch",
                    message=(
                        f"replay manifest for experiment "
                        f"{record.experiment_id!r} repeats check path "
                        f"{check.relative_path!r}"
                    ),
                    experiment_id=record.experiment_id,
                )
            )
            valid = False
        seen_paths.add(check.relative_path)
    if manifest.total_artifacts != len(checks):
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'total_count')}",
                kind="replay_mismatch",
                message=(
                    f"replay manifest total_artifacts "
                    f"{manifest.total_artifacts} does not match check count "
                    f"{len(checks)} for experiment {record.experiment_id!r}"
                ),
                experiment_id=record.experiment_id,
            )
        )
        valid = False
    matched_count = sum(1 for check in checks if check.matched)
    if manifest.matched_artifacts != matched_count:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'matched_count')}",
                kind="replay_mismatch",
                message=(
                    f"replay manifest matched_artifacts "
                    f"{manifest.matched_artifacts} does not match observed "
                    f"count {matched_count} for experiment "
                    f"{record.experiment_id!r}"
                ),
                experiment_id=record.experiment_id,
            )
        )
        valid = False
    if manifest.source_task_sha256 != task_byte_sha:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'source_task_hash')}",
                kind="source_task_hash_mismatch",
                message=(
                    f"replay manifest source_task_sha256 does not match the "
                    f"actual source TASK.json bytes for experiment "
                    f"{record.experiment_id!r}"
                ),
                experiment_id=record.experiment_id,
            )
        )
        valid = False
    unmatched_checks = [check for check in checks if not check.matched]
    volatile_only_unmatched = bool(unmatched_checks) and all(
        check.relative_path in VOLATILE_RUNTIME_ARTIFACTS for check in unmatched_checks
    )
    if not manifest.success and not volatile_only_unmatched:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'replay_mismatch')}",
                kind="replay_mismatch",
                message=(
                    f"fresh replay did not match for experiment "
                    f"{record.experiment_id!r}"
                ),
                experiment_id=record.experiment_id,
                observation_ids=list(record.observation_ids),
                claim_ids=list(record.claim_ids),
                paragraph_ids=list(record.paragraph_ids),
                section_ids=list(record.section_ids),
            )
        )
        valid = False
    elif not manifest.success:
        warnings.append(
            f"replay comparator for experiment {record.experiment_id!r} "
            "reported failure only on volatile runtime metadata "
            f"({sorted({check.relative_path for check in unmatched_checks})}); "
            "scientific artifacts matched"
        )
    if manifest.source_task_sha256 != manifest.replay_task_sha256:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'task_mismatch')}",
                kind="task_identity_mismatch",
                message=(
                    f"replay task hash mismatch for experiment "
                    f"{record.experiment_id!r}"
                ),
                experiment_id=record.experiment_id,
            )
        )
        valid = False
    lineage: List[ArtifactLineageRecord] = []
    for check in scientific_checks:
        identity_kind: Literal[
            "byte_identical",
            "canonical_scientific_identity",
            "mismatch",
            "missing",
        ] = "canonical_scientific_identity"
        if not check.matched:
            identity_kind = "mismatch"
            blockers.append(
                PublicationBlocker(
                    blocker_id=f"blocker-{_digest(record.experiment_id, check.relative_path, 'lineage_mismatch')}",
                    kind="replay_mismatch",
                    message=(
                        f"replay artifact {check.relative_path!r} did not "
                        f"match for experiment {record.experiment_id!r}: "
                        f"{check.reason}"
                    ),
                    experiment_id=record.experiment_id,
                )
            )
            valid = False
        else:
            source_file = (run_dir / check.relative_path).resolve()
            replay_file = (run_dir / "fresh_replay" / check.relative_path).resolve()
            if (
                _safe_within(source_file, run_dir)
                and _safe_within(replay_file, run_dir)
                and source_file.is_file()
                and replay_file.is_file()
                and _sha256_file(source_file) == _sha256_file(replay_file)
            ):
                identity_kind = "byte_identical"
        lineage.append(
            ArtifactLineageRecord(
                lineage_id=f"lineage-{_digest(record.experiment_id, check.relative_path, check.source_sha256 or '', check.replay_sha256 or '', identity_kind)}",
                artifact_id=check.relative_path,
                experiment_id=record.experiment_id,
                relative_path=check.relative_path,
                source_sha256=check.source_sha256 or "",
                replay_sha256=check.replay_sha256 or "",
                identity_kind=identity_kind,
                matched=check.matched,
            )
        )
    return valid, lineage


def _run_replay(
    *,
    record: CriticalExperimentRecord,
    run_dir: Path,
    runs_root: Path,
    expected_source_run_id: str,
    replay_provider: ReplayProvider,
    blockers: List[PublicationBlocker],
    warnings: List[str],
) -> Tuple[ReplayRecord, List[ArtifactLineageRecord]]:
    from optomind_optics.harness.replay import ReplayManifest

    try:
        manifest_payload = replay_provider(str(run_dir))
    except Exception as exc:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'replay_failed')}",
                kind="replay_failed",
                message=(
                    f"fresh replay failed for experiment "
                    f"{record.experiment_id!r}: {exc}"
                ),
                experiment_id=record.experiment_id,
                observation_ids=list(record.observation_ids),
                claim_ids=list(record.claim_ids),
                paragraph_ids=list(record.paragraph_ids),
                section_ids=list(record.section_ids),
            )
        )
        return (
            ReplayRecord(
                replay_id=f"replay-{_digest(record.experiment_id, 'failed')}",
                experiment_id=record.experiment_id,
                physical_experiment_ids=list(record.physical_experiment_ids),
                source_run_dir=str(run_dir),
                status="failed",
                error=str(exc),
            ),
            [],
        )
    try:
        manifest = (
            manifest_payload
            if isinstance(manifest_payload, ReplayManifest)
            else ReplayManifest.model_validate(manifest_payload)
        )
    except ValidationError as exc:
        blockers.append(
            PublicationBlocker(
                blocker_id=f"blocker-{_digest(record.experiment_id, 'replay_manifest')}",
                kind="replay_mismatch",
                message=(
                    f"malformed replay manifest for experiment "
                    f"{record.experiment_id!r}: {exc}"
                ),
                experiment_id=record.experiment_id,
            )
        )
        return (
            ReplayRecord(
                replay_id=f"replay-{_digest(record.experiment_id, 'malformed')}",
                experiment_id=record.experiment_id,
                physical_experiment_ids=list(record.physical_experiment_ids),
                source_run_dir=str(run_dir),
                status="failed",
                error=f"malformed replay manifest: {exc}",
            ),
            [],
        )
    source_run_id = str(manifest.source_run_id or "").strip()
    valid, lineage = _verify_replay_manifest(
        record=record,
        run_dir=run_dir,
        runs_root=runs_root,
        manifest=manifest,
        expected_source_run_id=expected_source_run_id,
        blockers=blockers,
        warnings=warnings,
    )
    if not valid:
        return (
            ReplayRecord(
                replay_id=f"replay-{_digest(record.experiment_id, 'invalid')}",
                experiment_id=record.experiment_id,
                physical_experiment_ids=list(record.physical_experiment_ids),
                source_run_dir=str(run_dir),
                status="failed",
                error="replay manifest failed integrity verification",
                source_task_sha256=manifest.source_task_sha256,
                replay_task_sha256=manifest.replay_task_sha256,
                source_run_id=source_run_id,
                manifest=manifest.model_dump(mode="json"),
            ),
            lineage,
        )
    replay_id = _digest(
        record.experiment_id,
        str(run_dir),
        _canonical_json(manifest.model_dump(mode="json")),
    )
    return (
        ReplayRecord(
            replay_id=replay_id,
            experiment_id=record.experiment_id,
            physical_experiment_ids=list(record.physical_experiment_ids),
            source_run_dir=str(run_dir),
            status="completed",
            source_task_sha256=manifest.source_task_sha256,
            replay_task_sha256=manifest.replay_task_sha256,
            source_run_id=source_run_id,
            manifest=manifest.model_dump(mode="json"),
        ),
        lineage,
    )


def _build_appendix(
    *,
    plan: ArticleDirectorPlan,
    execution_results: Sequence[ArticleExecutionResult],
) -> List[AppendixRecord]:
    appendix: List[AppendixRecord] = []
    seen: set[str] = set()
    for result in execution_results:
        observation = result.observation
        if observation.status.value not in NEGATIVE_OBSERVATION_STATUSES:
            continue
        if observation.observation_id in seen:
            continue
        seen.add(observation.observation_id)
        route_id = str(observation.metrics.get("route_id") or "")
        appendix.append(
            AppendixRecord(
                appendix_id=f"appendix-{_digest('observation', observation.observation_id)}",
                kind="observation",
                observation_id=observation.observation_id,
                experiment_id=observation.experiment_id,
                status=observation.status.value,
                summary=observation.summary,
                failure_records=[dict(item) for item in observation.failure_records],
                artifact_ids=list(observation.artifact_ids),
                route_id=route_id,
                reason=(
                    f"non-success observation {observation.observation_id}"
                    if observation.status.value != "failed"
                    else f"failed observation {observation.observation_id}"
                ),
            )
        )
    for row in plan.coverage_matrix.rows:
        if row.coverage_status.value not in APPENDIX_COVERAGE_STATUSES:
            continue
        key = f"coverage:{row.route_id}:{row.coverage_status.value}"
        if key in seen:
            continue
        seen.add(key)
        appendix.append(
            AppendixRecord(
                appendix_id=f"appendix-{_digest('coverage', row.route_id, row.coverage_status.value)}",
                kind="coverage_row",
                status=row.coverage_status.value,
                route_id=row.route_id,
                coverage_status=row.coverage_status.value,
                reason=row.not_run_reason
                or f"coverage row {row.route_id} is {row.coverage_status.value}",
            )
        )
    return appendix


def _render_appendix_markdown(appendix: Sequence[AppendixRecord]) -> str:
    blocks: List[str] = ["# Negative / Failed / Not-Run Appendix"]
    for record in appendix:
        if record.kind == "observation":
            heading = f"## Observation {record.observation_id} ({record.status})"
            lines = [
                f"- Experiment: {record.experiment_id}",
                f"- Status: {record.status}",
                f"- Summary: {record.summary or 'no summary'}",
            ]
            if record.route_id:
                lines.append(f"- Route: {record.route_id}")
            if record.artifact_ids:
                lines.append("- Artifacts: " + ", ".join(record.artifact_ids))
            if record.failure_records:
                lines.append(
                    "- Failure records: " + _canonical_json(record.failure_records)
                )
        else:
            heading = f"## Coverage row {record.route_id} ({record.coverage_status})"
            lines = [
                f"- Route: {record.route_id}",
                f"- Status: {record.coverage_status}",
                f"- Reason: {record.reason}",
            ]
        blocks.append(f"{heading}\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def build_article_reproducibility(
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    review: ArticleReviewResult | Mapping[str, Any],
    manuscript: ArticleManuscriptPackage | Mapping[str, Any],
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    execution_results: Sequence[ArticleExecutionResult | Mapping[str, Any]],
    runs_root: str | Path,
    *,
    replay_provider: Optional[ReplayProvider] = None,
    output_dir: str | Path | None = None,
) -> ArticleReproducibilityPackage:
    errors: List[str] = []
    warnings: List[str] = []
    blockers: List[PublicationBlocker] = []
    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
    except ValidationError as exc:
        errors.append(f"plan is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        ledger_model = (
            ledger
            if isinstance(ledger, ClaimLedgerResult)
            else ClaimLedgerResult.model_validate(ledger)
        )
    except ValidationError as exc:
        errors.append(f"ledger is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        architecture_model = (
            architecture
            if isinstance(architecture, ArticleArchitectureResult)
            else ArticleArchitectureResult.model_validate(architecture)
        )
    except ValidationError as exc:
        errors.append(f"architecture is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        review_model = (
            review
            if isinstance(review, ArticleReviewResult)
            else ArticleReviewResult.model_validate(review)
        )
    except ValidationError as exc:
        errors.append(f"review is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    try:
        manuscript_model = (
            manuscript
            if isinstance(manuscript, ArticleManuscriptPackage)
            else ArticleManuscriptPackage.model_validate(manuscript)
        )
    except ValidationError as exc:
        errors.append(f"manuscript is invalid: {exc}")
        return _hard_blocker(errors, warnings)
    records: List[TrustedValueRecord] = []
    for index, raw in enumerate(value_records):
        try:
            records.append(
                raw
                if isinstance(raw, TrustedValueRecord)
                else TrustedValueRecord.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"value_records[{index}] is invalid: {exc}")
    execution_models: List[ArticleExecutionResult] = []
    for index, raw in enumerate(execution_results):
        try:
            execution_models.append(
                raw
                if isinstance(raw, ArticleExecutionResult)
                else ArticleExecutionResult.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"execution_results[{index}] is invalid: {exc}")
    if errors:
        return _hard_blocker(errors, warnings)

    story = validate_manuscript_package(
        manuscript_model,
        plan_model,
        ledger_model,
        architecture_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if errors or story is None:
        return _hard_blocker(errors, warnings)
    validate_review_result(
        plan_model,
        ledger_model,
        architecture_model,
        review_model,
        selected_story_id,
        records,
        errors,
        warnings,
    )
    if errors:
        return _hard_blocker(errors, warnings)
    if (
        review_model.review_id != manuscript_model.review_id
        or review_model.result_id != manuscript_model.result_id
    ):
        errors.append("manuscript review_id/result_id do not match the supplied review")
    expected_manuscript = build_article_manuscript(
        plan_model,
        ledger_model,
        architecture_model,
        review_model,
        selected_story_id,
        records,
    )
    if expected_manuscript.model_dump(mode="json") != manuscript_model.model_dump(
        mode="json"
    ):
        errors.append(
            "supplied manuscript does not equal the deterministic rebuild "
            "from the supplied review"
        )
    if errors:
        return _hard_blocker(errors, warnings)

    observation_by_id = {
        result.observation.observation_id: result.observation
        for result in execution_models
    }
    critical = _discover_critical_experiments(
        manuscript=manuscript_model,
        ledger=ledger_model,
        architecture=architecture_model,
        story=story,
        observation_by_id=observation_by_id,
        errors=errors,
    )
    runs_root_path = Path(runs_root)
    (
        execution_by_experiment,
        _observation_by_id,
        replayable,
        expected_run_ids,
    ) = _validate_provenance(
        ledger=ledger_model,
        architecture=architecture_model,
        manuscript=manuscript_model,
        execution_results=execution_models,
        runs_root=runs_root_path,
        critical=critical,
        errors=errors,
        warnings=warnings,
        blockers=blockers,
    )
    resolved_critical: List[CriticalExperimentRecord] = []
    for record in critical:
        execution = execution_by_experiment.get(record.experiment_id)
        source_run_dir = execution.run_dir if execution is not None else ""
        resolved_critical.append(
            record.model_copy(update={"source_run_dir": str(source_run_dir)})
        )
    provider = replay_provider or _default_replay_provider
    replay_records: List[ReplayRecord] = []
    lineage: List[ArtifactLineageRecord] = []
    attempts = 0
    for record in resolved_critical:
        if record.experiment_id not in replayable:
            continue
        run_dir = Path(record.source_run_dir)
        replay_record, artifact_lineage = _run_replay(
            record=record,
            run_dir=run_dir,
            runs_root=runs_root_path,
            expected_source_run_id=expected_run_ids.get(record.experiment_id, ""),
            replay_provider=provider,
            blockers=blockers,
            warnings=warnings,
        )
        attempts += 1
        replay_records.append(replay_record)
        lineage.extend(artifact_lineage)
    appendix = _build_appendix(
        plan=plan_model,
        execution_results=execution_models,
    )
    if errors or blockers:
        status: Literal["ready", "ready_with_findings", "blocked"] = "blocked"
    elif warnings:
        status = "ready_with_findings"
    else:
        status = "ready"
    package_id = compute_reproducibility_package_id(
        plan_id=plan_model.plan_id,
        ledger_id=ledger_model.ledger_id,
        architecture_id=architecture_model.architecture_id,
        review_id=review_model.review_id,
        result_id=review_model.result_id,
        manuscript_body_id=manuscript_model.body_id,
        story_id=selected_story_id,
        status=status,
        critical_experiments=resolved_critical,
        replay_records=replay_records,
        lineage=lineage,
        appendix=appendix,
        blockers=blockers,
        warnings=warnings,
        errors=errors,
        attempts=attempts,
    )
    result = ArticleReproducibilityPackage(
        package_id=package_id,
        plan_id=plan_model.plan_id,
        ledger_id=ledger_model.ledger_id,
        architecture_id=architecture_model.architecture_id,
        review_id=review_model.review_id,
        result_id=review_model.result_id,
        manuscript_body_id=manuscript_model.body_id,
        story_id=selected_story_id,
        status=status,
        critical_experiments=resolved_critical,
        replay_records=replay_records,
        lineage=lineage,
        appendix=appendix,
        blockers=blockers,
        warnings=warnings,
        errors=errors,
        model_name="none",
        usage={},
        attempts=attempts,
    )
    if output_dir is not None:
        write_reproducibility_package(result, output_dir)
    return result


def validate_reproducibility_package(
    package: ArticleReproducibilityPackage | Mapping[str, Any],
    plan: ArticleDirectorPlan | Mapping[str, Any],
    ledger: ClaimLedgerResult | Mapping[str, Any],
    architecture: ArticleArchitectureResult | Mapping[str, Any],
    review: Any,
    manuscript: Any,
    selected_story_id: str,
    value_records: Sequence[TrustedValueRecord | Mapping[str, Any]],
    errors: List[str],
    warnings: List[str],
) -> bool:
    """Public deterministic validation of a persisted Stage 12B package.

    Does not rerun TMM replay; a self-inconsistent persisted package fails
    closed before downstream rendering.
    """

    try:
        package_model = (
            package
            if isinstance(package, ArticleReproducibilityPackage)
            else ArticleReproducibilityPackage.model_validate(package)
        )
    except ValidationError as exc:
        errors.append(f"reproducibility package is invalid: {exc}")
        return False
    try:
        plan_model = (
            plan
            if isinstance(plan, ArticleDirectorPlan)
            else ArticleDirectorPlan.model_validate(plan)
        )
        ledger_model = (
            ledger
            if isinstance(ledger, ClaimLedgerResult)
            else ClaimLedgerResult.model_validate(ledger)
        )
        architecture_model = (
            architecture
            if isinstance(architecture, ArticleArchitectureResult)
            else ArticleArchitectureResult.model_validate(architecture)
        )
    except ValidationError as exc:
        errors.append(f"reproducibility upstream input is invalid: {exc}")
        return False
    records: List[TrustedValueRecord] = []
    for index, raw in enumerate(value_records):
        try:
            records.append(
                raw
                if isinstance(raw, TrustedValueRecord)
                else TrustedValueRecord.model_validate(raw)
            )
        except ValidationError as exc:
            errors.append(f"value_records[{index}] is invalid: {exc}")
    if errors:
        return False
    if package_model.plan_id != plan_model.plan_id:
        errors.append("reproducibility plan_id does not match the plan")
    if package_model.ledger_id != ledger_model.ledger_id:
        errors.append("reproducibility ledger_id does not match the ledger")
    if package_model.architecture_id != architecture_model.architecture_id:
        errors.append("reproducibility architecture_id does not match the architecture")
    if package_model.story_id != selected_story_id:
        errors.append("reproducibility story_id does not match the story")
    if manuscript is not None:
        manuscript_model = (
            manuscript
            if isinstance(manuscript, ArticleManuscriptPackage)
            else ArticleManuscriptPackage.model_validate(manuscript)
        )
        if package_model.manuscript_body_id != manuscript_model.body_id:
            errors.append(
                "reproducibility manuscript_body_id does not match the " "manuscript"
            )
    if review is not None:
        review_model = (
            review
            if isinstance(review, ArticleReviewResult)
            else ArticleReviewResult.model_validate(review)
        )
        if (
            package_model.review_id != review_model.review_id
            or package_model.result_id != review_model.result_id
        ):
            errors.append(
                "reproducibility review/result identity does not match the " "review"
            )
    recomputed = compute_reproducibility_package_id(
        plan_id=package_model.plan_id,
        ledger_id=package_model.ledger_id,
        architecture_id=package_model.architecture_id,
        review_id=package_model.review_id,
        result_id=package_model.result_id,
        manuscript_body_id=package_model.manuscript_body_id,
        story_id=package_model.story_id,
        status=package_model.status,
        critical_experiments=package_model.critical_experiments,
        replay_records=package_model.replay_records,
        lineage=package_model.lineage,
        appendix=package_model.appendix,
        blockers=package_model.blockers,
        warnings=package_model.warnings,
        errors=package_model.errors,
        attempts=package_model.attempts,
    )
    if recomputed != package_model.package_id:
        errors.append("reproducibility package_id does not match recomputed identity")
    if package_model.errors or package_model.blockers:
        derived_status = "blocked"
    elif package_model.warnings:
        derived_status = "ready_with_findings"
    else:
        derived_status = "ready"
    if derived_status != package_model.status:
        errors.append(
            f"reproducibility status {package_model.status!r} does not match "
            f"derived status {derived_status!r}"
        )
    critical_ids = [item.experiment_id for item in package_model.critical_experiments]
    if len(critical_ids) != len(set(critical_ids)):
        errors.append("reproducibility has duplicate critical experiment IDs")
    replay_by_experiment: Dict[str, ReplayRecord] = {}
    for record in package_model.replay_records:
        if record.experiment_id in replay_by_experiment:
            errors.append(
                f"reproducibility has duplicate replay record for "
                f"{record.experiment_id!r}"
            )
        replay_by_experiment[record.experiment_id] = record
    unexpected_replays = [
        record.experiment_id
        for record in package_model.replay_records
        if record.experiment_id not in set(critical_ids)
    ]
    if unexpected_replays:
        errors.append(
            "reproducibility contains unexpected completed replay records "
            f"{unexpected_replays}"
        )
    for record in package_model.critical_experiments:
        replay = replay_by_experiment.get(record.experiment_id)
        if replay is None:
            errors.append(
                f"critical experiment {record.experiment_id!r} has no replay " "record"
            )
            continue
        if replay.status != "completed":
            errors.append(
                f"critical experiment {record.experiment_id!r} replay is not "
                "completed"
            )
        if not replay.source_run_dir:
            errors.append(
                f"critical experiment {record.experiment_id!r} replay has no "
                "source_run_dir"
            )
        manifest = replay.manifest or {}
        checks = manifest.get("checks") or []
        for label, expected in (
            ("source_task_sha256", replay.source_task_sha256),
            ("replay_task_sha256", replay.replay_task_sha256),
        ):
            if not _hex64(str(expected or "")):
                errors.append(
                    f"replay record for {record.experiment_id!r} has "
                    f"invalid {label}"
                )
            elif manifest.get(label) != expected:
                errors.append(
                    f"replay record for {record.experiment_id!r} {label} "
                    "does not match its manifest"
                )
        if not str(replay.source_run_id or "").strip():
            errors.append(
                f"replay record for {record.experiment_id!r} has empty " "source_run_id"
            )
        elif manifest.get("source_run_id") != replay.source_run_id:
            errors.append(
                f"replay record for {record.experiment_id!r} source_run_id "
                "does not match its manifest"
            )
        if not checks:
            errors.append(
                f"replay manifest for {record.experiment_id!r} has empty checks"
            )
        unmatched_paths = [
            check.get("relative_path") for check in checks if not check.get("matched")
        ]
        volatile_only_unmatched = bool(unmatched_paths) and all(
            path in VOLATILE_RUNTIME_ARTIFACTS for path in unmatched_paths
        )
        if manifest.get("success") is not True and not volatile_only_unmatched:
            errors.append(
                f"replay manifest for {record.experiment_id!r} is not successful"
            )
        for check in checks:
            if check.get("relative_path") in VOLATILE_RUNTIME_ARTIFACTS:
                continue
            if not check.get("matched"):
                errors.append(
                    f"replay manifest for {record.experiment_id!r} has "
                    f"unmatched check {check.get('relative_path')!r}"
                )
        for label, value in (
            ("source_task_sha256", manifest.get("source_task_sha256")),
            ("replay_task_sha256", manifest.get("replay_task_sha256")),
        ):
            if not _hex64(str(value or "")):
                errors.append(
                    f"replay manifest for {record.experiment_id!r} has "
                    f"invalid {label}"
                )
        if not str(manifest.get("source_run_id") or "").strip():
            errors.append(
                f"replay manifest for {record.experiment_id!r} has empty "
                "source_run_id"
            )
        if not str(manifest.get("replay_run_id") or "").strip():
            errors.append(
                f"replay manifest for {record.experiment_id!r} has empty "
                "replay_run_id"
            )
        if replay.error:
            errors.append(
                f"replay record for {record.experiment_id!r} carries an error"
            )
        if manifest.get("total_artifacts") != len(checks):
            errors.append(
                f"replay manifest for {record.experiment_id!r} total count "
                "does not match its checks"
            )
        if manifest.get("matched_artifacts") != sum(
            1 for item in checks if item.get("matched")
        ):
            errors.append(
                f"replay manifest for {record.experiment_id!r} matched count "
                "does not match its checks"
            )
    lineage_ids = [item.lineage_id for item in package_model.lineage]
    if len(lineage_ids) != len(set(lineage_ids)):
        errors.append("reproducibility lineage has duplicate lineage IDs")
    lineage_identities: set[Tuple[str, str, str]] = set()
    for item in package_model.lineage:
        identity = (item.artifact_id, item.experiment_id, item.relative_path)
        if identity in lineage_identities:
            errors.append(f"reproducibility repeats lineage identity {identity}")
        lineage_identities.add(identity)
        if item.matched:
            if not _hex64(str(item.source_sha256 or "")) or not _hex64(
                str(item.replay_sha256 or "")
            ):
                errors.append(
                    f"matched lineage {item.lineage_id!r} has invalid "
                    "source/replay hashes"
                )
    for record in package_model.critical_experiments:
        replay = replay_by_experiment.get(record.experiment_id)
        manifest_checks = (
            (replay.manifest or {}).get("checks") or [] if replay is not None else []
        )
        check_pairs = {
            (check.get("relative_path"), check.get("source_sha256"))
            for check in manifest_checks
            if check.get("matched")
        }
        for artifact in record.artifact_ids:
            items = [
                item
                for item in package_model.lineage
                if item.artifact_id == artifact
                and item.experiment_id == record.experiment_id
            ]
            matched = any(
                item.matched
                and item.identity_kind
                in {
                    "byte_identical",
                    "canonical_scientific_identity",
                }
                and (item.relative_path, item.source_sha256) in check_pairs
                for item in items
            )
            if not matched:
                errors.append(
                    f"critical experiment {record.experiment_id!r} artifact "
                    f"{artifact!r} lacks an exact matched lineage entry"
                )
    return not errors


def _hard_blocker(
    errors: Sequence[str], warnings: Sequence[str]
) -> ArticleReproducibilityPackage:
    blocker_models = [
        PublicationBlocker(
            blocker_id=f"blocker-{_digest('invalid', str(item))}",
            kind="upstream_identity",
            message=str(item),
        )
        for item in errors
    ]
    warning_values = [str(item) for item in warnings]
    error_values = [str(item) for item in errors]
    package_id = compute_reproducibility_package_id(
        plan_id="",
        ledger_id="",
        architecture_id="",
        review_id="",
        result_id="",
        manuscript_body_id="",
        story_id="",
        status="blocked",
        critical_experiments=[],
        replay_records=[],
        lineage=[],
        appendix=[],
        blockers=blocker_models,
        warnings=warning_values,
        errors=error_values,
        attempts=0,
    )
    return ArticleReproducibilityPackage(
        package_id=package_id,
        plan_id="",
        ledger_id="",
        architecture_id="",
        review_id="",
        result_id="",
        manuscript_body_id="",
        story_id="",
        status="blocked",
        critical_experiments=[],
        replay_records=[],
        lineage=[],
        appendix=[],
        blockers=blocker_models,
        warnings=warning_values,
        errors=error_values,
        model_name="none",
        usage={},
        attempts=0,
    )


def write_reproducibility_package(
    package: ArticleReproducibilityPackage,
    output_dir: str | Path,
) -> Dict[str, Path]:
    """Atomic fixed-name writer; refuses to overwrite conflicting content."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    package_path = output_dir / "ARTICLE_REPRODUCIBILITY_PACKAGE.json"
    lineage_path = output_dir / "ARTICLE_RUN_LINEAGE.json"
    appendix_path = output_dir / "ARTICLE_NEGATIVE_RESULTS_APPENDIX.md"
    expected_package = _canonical_json(package.model_dump(mode="json"))
    expected_lineage = _canonical_json(
        [item.model_dump(mode="json") for item in package.lineage]
    )
    expected_appendix = _render_appendix_markdown(package.appendix)
    for path, expected in (
        (package_path, expected_package),
        (lineage_path, expected_lineage),
        (appendix_path, expected_appendix),
    ):
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                identical = _json_equal(existing, expected)
            else:
                identical = existing == expected
            if not identical:
                raise ArticleReproducibilityIntegrityError(
                    f"refusing to overwrite conflicting {path.name} under "
                    f"package {package.package_id!r}"
                )
    atomic_write_json(package_path, package.model_dump(mode="json"))
    atomic_write_json(
        lineage_path,
        [item.model_dump(mode="json") for item in package.lineage],
    )
    atomic_write_text(appendix_path, expected_appendix)
    return {
        "package": package_path,
        "lineage": lineage_path,
        "appendix": appendix_path,
    }


__all__ = [
    "APPENDIX_COVERAGE_STATUSES",
    "AppendixRecord",
    "ArtifactLineageRecord",
    "ArticleReproducibilityError",
    "ArticleReproducibilityIntegrityError",
    "ArticleReproducibilityPackage",
    "CriticalExperimentRecord",
    "NEGATIVE_OBSERVATION_STATUSES",
    "PublicationBlocker",
    "REPLAYABLE_STATUSES",
    "ReplayProvider",
    "ReplayRecord",
    "build_article_reproducibility",
    "compute_reproducibility_package_id",
    "validate_reproducibility_package",
    "write_reproducibility_package",
]
