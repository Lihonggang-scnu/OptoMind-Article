"""Read-only C13 → existing Article authoring projection.

These are source-import records, not optimizer executions, compiled requests or
physics certificates. Every evidence file is the exact canonical repository
object. Only explicitly qualified finite source scalars become trusted values.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import math
from typing import Any, Literal
from pydantic import Field
from .contracts import StrictModel, ObjectRef, Observation, ResearchTask, canonical_hash
from .article_bridge import load_result_bundle
from .authoring_selection import AuthoringSelection, AuthoringValueBinding, resolve_authoring_selection
from .repository import ScienceRepository
from ..article_assets import ArticleAssetCompilationResult, compute_asset_compilation_result_id
from ..article_architecture import ArtifactDescriptor
from ..article_writing import TrustedValueRecord
from ..article_contracts import ObservationCard, ExperimentStatus, ResearchCharter, CoverageMatrix, CoverageRow
from ..article_director import ArticleDirectorPlan, CapabilityDecision
from ..problem_analyzer import TMMCompatibility


class ScientificSourceCandidate(StrictModel):
    """An adopted model identity; intentionally contains no certificate fields."""
    candidate_id: str
    experiment_id: str
    source_ref: ObjectRef
    role_keys: list[str] = Field(default_factory=lambda: ['adopted_result'])
    artifact_ids: list[str] = Field(default_factory=list)
    is_pareto: bool = False
    is_baseline: bool = False
    target_score: None = None
    robustness_score: None = None
    simplicity_score: None = None
    distinctiveness_score: None = None


class ScientificSourceAssets(ArticleAssetCompilationResult):
    source_kind: Literal['scientific_result'] = 'scientific_result'
    candidates: list[ScientificSourceCandidate] = Field(default_factory=list)


class ScientificSourceRequest(StrictModel):
    """A non-executable source join key, never a CompiledExperimentRequest."""
    source_kind: Literal['scientific_result'] = 'scientific_result'
    request_id: str
    task_hash: str
    task_digest: str
    run_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class ScientificSourceRow(StrictModel):
    route_id: str = 'scientific_result'
    route_alias: str = 'R01'
    status: Literal['ready'] = 'ready'
    coverage_route: str = 'scientific_result'
    request: ScientificSourceRequest
    proposal: None = None
    cells: None = None


class ScientificAuthoringPlanning(StrictModel):
    source_kind: Literal['scientific_result'] = 'scientific_result'
    plan_id: str
    rows: tuple[ScientificSourceRow, ...]


@dataclass(frozen=True)
class ScientificSourceIdentity:
    result_id: str
    selected_result_ref: ObjectRef
    problem_analysis: None = None


def _bytes(obj):
    return (json.dumps(obj.model_dump(mode='json'), sort_keys=True, ensure_ascii=False,
                       allow_nan=False, separators=(',', ':')) + '\n').encode()


def _qualified_scalars(value, path=()):
    if isinstance(value, dict):
        kind = value.get('data_kind')
        units = value.get('units', value.get('unit'))
        number = value.get('value')
        if (kind in {'measurement', 'simulation', 'fit', 'held_out_prediction', 'schematic'}
                and isinstance(units, str) and isinstance(number, (int, float))
                and not isinstance(number, bool) and math.isfinite(number)):
            yield path + ('value',), number, units, kind
        for key, child in value.items():
            yield from _qualified_scalars(child, path + (key,))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _qualified_scalars(child, path + (index,))


def _project(repository_root, selected_ref, root, *, write):
    from ..article_continuation import SourcePipelineBundle
    root = Path(root).resolve()
    repo = ScienceRepository(repository_root, read_only=True)
    try:
        bundle = load_result_bundle(repo, selected_ref)
        selected = bundle.selected_result
        task_refs = {canonical_hash(c.scope['task_ref']): ObjectRef.model_validate(c.scope['task_ref'])
                     for c in bundle.claim_ledger.claims if c.scope.get('task_ref')}
        if len(task_refs) != 1:
            raise ValueError('scientific source requires one immutable task_ref in adopted claim scope')
        task = repo.load(next(iter(task_refs.values())))
        if not isinstance(task, ResearchTask):
            raise ValueError('adopted task_ref must resolve to ResearchTask')
        candidate = repo.load(selected.candidate_ref)
        refs = {}
        for ref in (*selected.asset_refs, *selected.negative_result_refs,
                    *(ref for claim in bundle.claim_ledger.claims for ref in claim.evidence_refs)):
            refs[canonical_hash(ref)] = ref
        objects = [(ref, repo.load(ref)) for _, ref in sorted(refs.items())]
        observations = [obj for _, obj in objects if isinstance(obj, Observation)]
        if not observations:
            raise ValueError('scientific authoring requires a real adopted source Observation')
        source_observation = observations[0]
        source_id = 'scientific-source:' + canonical_hash(selected.ref())
        descriptors, values, bindings, artifact_bindings = [], [], [], {}
        for ref, obj in objects:
            filename = 'objects/' + canonical_hash(ref) + '.json'
            target = root / filename
            content = _bytes(obj)
            if write:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    if target.read_bytes() != content:
                        raise ValueError('existing scientific source artifact differs from immutable evidence')
                else:
                    target.write_bytes(content)
            elif not target.is_file() or target.read_bytes() != content:
                raise ValueError('scientific source artifact missing or modified: ' + filename)
            digest = hashlib.sha256(content).hexdigest()
            artifact_id = 'scientific:' + canonical_hash(ref)
            scalars = list(_qualified_scalars(obj.model_dump(mode='json')))
            fields = ['.'.join(map(str, path)) for path, *_ in scalars] or list(obj.model_dump())
            descriptors.append(ArtifactDescriptor(artifact_id=artifact_id, path=filename,
                fields=fields, artifact_type='scientific_repository_object', media_type='application/json',
                content_summary=f'Immutable {obj.schema_version}: {obj.object_id}; scope and status retained in source.',
                sha256=digest, source_observation_ids=[obj.object_id] if isinstance(obj, Observation) else []))
            artifact_bindings[artifact_id] = ref
            for path, number, units, kind in scalars:
                field = '.'.join(map(str, path))
                values.append(TrustedValueRecord(artifact_id=artifact_id, field=field,
                    rendered_value=str(number), unit=units, source_hash=digest,
                    derivation='Exact source scalar at ' + field, label=field,
                    prose_safe=kind != 'schematic'))
                bindings.append(AuthoringValueBinding(artifact_id=artifact_id, field=field,
                    source_ref=ref, source_path=path, units=units, data_kind=kind))
        selection = AuthoringSelection(repository_root=str(Path(repository_root).resolve()),
            selected_result_ref=selected.ref(), candidate_bindings={candidate.object_id:candidate.ref()},
            artifact_bindings=artifact_bindings, value_bindings=tuple(bindings))
        obs = ObservationCard(observation_id=source_observation.object_id,
            experiment_id=source_observation.acquisition_id, status=ExperimentStatus.candidate,
            artifact_ids=list(artifact_bindings),
            metrics={'source_kind':'scientific_result','source_ref':source_observation.ref().model_dump(mode='json'),
                     'provenance_role':'primary source join only; not universal artifact origin'},
            summary='Primary acquisition join for read-only source import, not universal evidence origin. Scientific status is exclusively governed by the adopted claim ledger; no optimizer execution or physics certificate is asserted.')
        asset = ScientificSourceAssets(status='ready', request_id=source_id, task_hash=task.content_hash,
            task_digest=task.content_hash, run_id=selected.source_run_id,
            experiment_id=source_observation.acquisition_id, observation_id=source_observation.object_id,
            descriptors=descriptors, trusted_values=values, observation=obs,
            candidates=[ScientificSourceCandidate(candidate_id=candidate.object_id,
                experiment_id=source_observation.acquisition_id, source_ref=candidate.ref(),
                artifact_ids=[aid for aid, ref in artifact_bindings.items() if ref == selected.candidate_ref])])
        asset = asset.model_copy(update={'result_id':compute_asset_compilation_result_id(asset)})
        scope = 'Only the immutable adopted scientific claims, with their original status and scope. No manufactured-device validation is inferred.'
        plan = ArticleDirectorPlan(plan_id=source_id, question=task.question,
            charter=ResearchCharter(charter_id=source_id,question=task.question,scope=scope,
                goals=['Report the adopted scientific result and all negative or unresolved outcomes.']),
            capability=CapabilityDecision(capability_id=source_id,status=TMMCompatibility.ambiguous,
                supported_scope=scope,recommended_next_action='Author within the adopted evidence scope.'),
            hypotheses=[],coverage_matrix=CoverageMatrix(matrix_id=source_id,
                rows=[CoverageRow(route_id='scientific_result',title='Immutable scientific source import',coverage_status='completed')]),
            stage_plan=[], research_influence=['Deterministic source projection; not a generated research plan.'])
        planning = ScientificAuthoringPlanning(plan_id=source_id,rows=(ScientificSourceRow(
            request=ScientificSourceRequest(request_id=source_id,task_hash=task.content_hash,
                task_digest=task.content_hash,run_id=selected.source_run_id)),))
        context = resolve_authoring_selection(selection, [asset])
        identity = canonical_hash({'selected_result_ref':selected.ref().model_dump(mode='json'), 'selection':selection.model_dump(mode='json'),
                                   'plan':plan.model_dump(mode='json'),'planning':planning.model_dump(mode='json'),'assets':[asset.model_dump(mode='json')]})
        result = SourcePipelineBundle(request=None, result=ScientificSourceIdentity(identity,selected.ref()),
            plan=plan, planning=planning, executions=(), assets=(asset,), execution_by_request={},
            asset_by_request={source_id:asset},source_digest=identity,scientific_selection=context,
            source_kind='scientific_result')
        return result, selection
    finally:
        repo.close()


def prepare_scientific_authoring_source(repository_root, selected_result_ref, out_dir):
    """Export immutable evidence and return the generated authoritative bindings."""
    _, selection = _project(repository_root, selected_result_ref, out_dir, write=True)
    path = Path(out_dir) / 'SCIENTIFIC_AUTHORING_SELECTION.json'
    content = _bytes(selection)
    if path.exists() and path.read_bytes() != content:
        raise ValueError('scientific authoring source is already bound to another selection')
    path.write_bytes(content)
    return selection


def load_scientific_authoring_source(source_dir, selection):
    persisted = AuthoringSelection.model_validate_json((Path(source_dir)/'SCIENTIFIC_AUTHORING_SELECTION.json').read_text())
    if persisted != selection:
        raise ValueError('scientific source selection differs from persisted authority')
    bundle, expected = _project(selection.repository_root, selection.selected_result_ref, source_dir, write=False)
    if expected != selection:
        raise ValueError('scientific source bindings differ from canonical projection')
    return bundle


def scientific_source_feedback(plan, observations, findings, manifest, context):
    """Import canonical adjudication; never assert a legacy physics verdict."""
    from ..article_feedback import ArticleFeedbackResult, HypothesisUpdateDecision
    from ..article_result_synthesis import _resolve_tokens, _local_manifest, _route_alias_by_route_id
    by_id = {o.observation_id:o for o in observations}
    routes = _route_alias_by_route_id(manifest)
    claims = {c['statement']:c for c in context['scientific_claims']}
    updates = []
    for finding in findings:
        local = _local_manifest(manifest, routes[finding.route_id])
        claim = claims[_resolve_tokens(finding.statement_with_value_tokens, local)]
        observation = by_id[finding.observation_id]
        artifacts = sorted({local[a].artifact_id for a in finding.source_value_aliases if local[a].artifact_id})
        common = dict(hypothesis_id=finding.synthesized_hypothesis_id,
            reason='Immutable scientific claim import: ' + claim['object_id'] + '; status=' + claim['status'],
            observation_ids=[observation.observation_id], experiment_ids=[observation.experiment_id],
            artifact_ids=artifacts, route_ids=['scientific_result'],
            evidence_summary='Canonical scientific claim status; not a legacy physics acceptance.')
        target = 'partially_supported' if claim['status']=='supported' else 'under_test'
        if claim['status']=='refuted':
            updates.append(HypothesisUpdateDecision(from_status='proposed',to_status='active',**common))
            updates.append(HypothesisUpdateDecision(from_status='active',to_status='refuted',**common))
        else:
            updates.append(HypothesisUpdateDecision(from_status='proposed',to_status=target,**common))
    return ArticleFeedbackResult(controller_id='scientific-source-import:' + canonical_hash(context),
        hypothesis_updates=updates, stop_decision='stop_completed',
        stop_reason='Source import completed; scientific research acceptance is unchanged.',
        provenance_observation_ids=sorted(by_id))
