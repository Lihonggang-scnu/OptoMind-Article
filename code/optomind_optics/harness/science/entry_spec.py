"""Explicit, bounded Article entry for the existing mechanism diagnostic loop.

This is a binding/assembly boundary, not a natural-language scientific compiler.
The caller supplies the public hypotheses and frozen protocol. Provider secrets
and synthetic world answers are supplied separately to a host factory.
"""
from __future__ import annotations

from dataclasses import asdict, fields
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from pydantic import Field, model_validator

from .contracts import ResearchTask, SelectedResearchResult, StrictModel, canonical_hash
from .diagnostic_design import (DiagnosticAction, DiagnosticConfig, DiagnosticFrontEnd,
                               DiagnosticNoise, DiagnosticSearchSpace, ReferenceLayer,
                               action_from_dict, config_from_dict, family_from_dict)
from .model_ir import IRNode, ModelIR, ParameterSpec
from ..problem_analyzer import OpticalProblemAnalysis, ProblemAnalysisResult

ENTRY_SPEC_FILENAME = 'SCIENCE_ENTRY_SPEC.json'


def _complete(payload, kind):
    if not isinstance(payload, dict) or set(payload) != {f.name for f in fields(kind)}:
        raise ValueError(f'complete frozen {kind.__name__} required; omitted defaults are not accepted')


def _complete_frontend(payload):
    _complete(payload, DiagnosticFrontEnd)
    for layer in payload['layers']:
        _complete(layer, ReferenceLayer)


class DiagnosticProviderSpec(StrictModel):
    provider_id: str = Field(min_length=1)
    evidence_tier: str = Field(min_length=1)
    channels: tuple[Literal['R', 'T', 'A'], ...] = Field(min_length=1)
    supports_status: Literal[True]
    noise_validated: bool


class DiagnosticEntrySpec(StrictModel):
    schema_version: Literal['article-diagnostic-entry.v1'] = 'article-diagnostic-entry.v1'
    kind: Literal['mechanism_diagnosis']
    run_id: str = Field(min_length=1)
    original_question: str = Field(min_length=1)
    analysis: OpticalProblemAnalysis
    analysis_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    objective: dict
    source_refs: tuple[str, ...] = Field(min_length=1)
    families: tuple[dict, ...] = Field(min_length=2, max_length=12)
    config: dict
    search_space: dict
    initial_action: dict
    provider: DiagnosticProviderSpec

    @model_validator(mode='after')
    def validate_protocol(self):
        if self.original_question != self.analysis.original_request:
            raise ValueError('scientific spec question differs from its structured analysis')
        if self.analysis_hash != canonical_hash(self.analysis.model_dump(mode='json')):
            raise ValueError('scientific spec analysis hash differs')
        if self.analysis.primary_intent.value not in {'analyze', 'compare', 'reproduce'}:
            raise ValueError('unsupported task: mechanism diagnosis cannot replace a design/optimization objective')
        if self.analysis.compatibility.value != 'compatible':
            raise ValueError('unsupported task: compatible planar analysis is required')
        if self.objective != {'kind': 'mechanism_diagnosis'}:
            raise ValueError('unsupported scientific objective; no default objective substitution')
        if any(not isinstance(ref, str) or not ref.strip() for ref in self.source_refs):
            raise ValueError('explicit nonempty scientific source references required')
        _complete(self.config, DiagnosticConfig)
        _complete(self.config['noise'], DiagnosticNoise)
        _complete(self.search_space, DiagnosticSearchSpace)
        _complete_frontend(self.search_space['fixed_frontend'])
        _complete(self.initial_action, DiagnosticAction)
        _complete_frontend(self.initial_action['frontend'])
        for family in self.families:
            model = family.get('model')
            _complete(model, ModelIR)
            for node in model['nodes']:
                _complete(node, IRNode)
            for parameter in model['parameters']:
                _complete(parameter, ParameterSpec)
        families, cfg, space, initial = self.components()
        if len({f.family_id for f in families}) != len(families):
            raise ValueError('distinct mechanism families required')
        if set(self.provider.channels) != {initial.channel, 'R'}:
            raise ValueError('provider channels must match the initial and diagnostic acquisition channels')
        bands = self.analysis.wavelengths_nm
        if not bands or not self.analysis.target_observables:
            raise ValueError('explicit analysis wavelength domain and observables required')
        allowed = {'R': {'r', 'reflectance'}, 'T': {'t', 'transmittance'}, 'A': {'a', 'absorptance', 'absorption'}}
        declared = {x.casefold().strip() for x in self.analysis.target_observables}
        if not all(declared & allowed[channel] for channel in self.provider.channels):
            raise ValueError('scientific measurement channel differs from the structured analysis')
        actions = (initial, *space.actions(sample_id=initial.sample_id, session_id='spec-validation', limit=cfg.candidate_limit))
        for action in actions:
            low = action.wavelengths_nm[0] - action.bandwidth_nm / 2
            high = action.wavelengths_nm[-1] + action.bandwidth_nm / 2
            if not any(a <= low <= high <= b for a, b in bands):
                raise ValueError('scientific acquisition exceeds the analyzed wavelength domain')
            if any(not family.model.domain_nm[0] <= low <= high <= family.model.domain_nm[1] for family in families):
                raise ValueError('scientific acquisition exceeds a declared mechanism model domain')
            if self.analysis.angles_deg and action.angle_deg not in self.analysis.angles_deg:
                raise ValueError('scientific acquisition angle differs from structured analysis')
            if self.analysis.polarizations and action.polarization not in self.analysis.polarizations:
                raise ValueError('scientific acquisition polarization differs from structured analysis')
        return self

    def components(self):
        return (tuple(family_from_dict(row) for row in self.families),
                config_from_dict(self.config), DiagnosticSearchSpace(**self.search_space),
                action_from_dict(self.initial_action))

    def bind_request(self, request):
        # Revalidate nested mutable values before any runtime is created.
        spec = type(self).model_validate_json(self.model_dump_json())
        if request.run_id != spec.run_id or request.question != spec.original_question:
            raise ValueError('Article request and scientific spec question/run identity differ')
        return spec

    def bind_committed_analysis(self, root):
        saved = json.loads((Path(root) / ENTRY_SPEC_FILENAME).read_text(encoding='utf-8'))
        if canonical_hash(saved) != canonical_hash(self.model_dump(mode='json')):
            raise ValueError('frozen scientific entry changed on resume')
        result = ProblemAnalysisResult.model_validate_json((Path(root) / '01-problem_analysis.json').read_text(encoding='utf-8'))
        if result.status != 'analyzed' or result.analysis is None:
            raise ValueError('scientific entry requires completed committed problem analysis')
        if canonical_hash(result.analysis.model_dump(mode='json')) != self.analysis_hash:
            raise ValueError('committed Article analysis differs from the frozen scientific spec')

    def freeze(self, root):
        from optomind_research.runtime.artifact_store import atomic_write_json
        path = Path(root) / ENTRY_SPEC_FILENAME
        payload = self.model_dump(mode='json')
        if path.exists():
            if canonical_hash(json.loads(path.read_text(encoding='utf-8'))) != canonical_hash(payload):
                raise ValueError('frozen scientific entry changed')
        else:
            atomic_write_json(path, payload)


class DiagnosticPipelineLoopAdapter:
    """Exact typed bridge; never duck-type an arbitrary object as a science loop."""
    def __init__(self, loop, spec, request):
        from .diagnostic_research import DiagnosticResearchLoop
        if not isinstance(loop, DiagnosticResearchLoop) or not isinstance(spec, DiagnosticEntrySpec):
            raise ValueError('actual diagnostic loop and validated entry spec required')
        self.loop = loop
        self.spec = spec.bind_request(request)
        self.repository, self.task, self.runner = loop.repository, loop.task, loop.runner
        self.config = SimpleNamespace(run_id=spec.run_id)
        self.compute_ledger = loop.compute_ledger
        self.selected_result = None
        self.verify_contract(request)
        self._context('protocol', {'kind': spec.kind, 'scientific_spec': spec.model_dump(mode='json'),
                                 'scientific_spec_hash': canonical_hash(spec.model_dump(mode='json')),
                                 'scope': 'existing bounded mechanism diagnosis; no autonomous task invention'})

    def verify_contract(self, request):
        spec = self.spec.bind_request(request)
        if (self.task.question != spec.original_question or self.task.source_run_id != spec.run_id
                or self.task.objective != spec.objective
                or self.task.constraints.get('source_task', {}).get('article_entry_spec_hash') != canonical_hash(spec.model_dump(mode='json'))):
            raise ValueError('diagnostic task does not preserve the exact Article scientific specification')
        if self.repository is not self.loop.repository or self.runner is not self.loop.runner:
            raise ValueError('diagnostic adapter runtime authority changed')
        if not self.runner.gates or not set(spec.provider.channels) <= set(self.runner.gates):
            raise ValueError('diagnostic adapter is missing required durable acquisition gates')

    def _find(self, object_id):
        snapshot = self.repository.latest_snapshot()
        ref = next((r for r in snapshot.object_refs if r.object_id == object_id), None) if snapshot else None
        return self.repository.load(ref) if ref else None

    def _context(self, suffix, summary, refs=()):
        return self.loop._context(suffix, summary, refs)

    def run(self, work_dir):
        self.spec.bind_committed_analysis(work_dir)
        report = self.loop.run(self.spec.components()[3], work_dir)
        result = SelectedResearchResult.model_validate(report['result'])
        self.selected_result = self.repository.load(result.ref())
        return self.selected_result

    def verify_completed(self, work_dir):
        # Cached Article stages must not bypass the diagnostic receipt checks.
        # Require a terminal checkpoint first, so validation cannot restart work.
        if self._find(self.config.run_id + ':terminal-state') is None:
            raise ValueError('completed Article diagnosis lacks its terminal scientific checkpoint')
        selected_path = Path(work_dir) / 'SELECTED_RESEARCH_RESULT.json'
        expected = SelectedResearchResult.model_validate_json(selected_path.read_text(encoding='utf-8'))
        if self.run(work_dir).ref() != expected.ref():
            raise ValueError('completed Article diagnosis changed its selected result')


def verify_completed_diagnostic_entry(policy, request, receipts):
    """Called under Article's runtime lock before returning committed stages."""
    if getattr(policy, 'explicit_diagnostic_entry', None) is None:
        return
    from .pipeline_adapter import OpenResearchPipelinePolicy
    if not isinstance(policy, OpenResearchPipelinePolicy):
        raise ValueError('explicit diagnosis requires the typed bound scientific policy')
    if not any(r.stage == 'execution' and r.status == 'completed' for r in receipts):
        return
    loop = policy._ensure_loop(request.work_dir)
    if not isinstance(loop, DiagnosticPipelineLoopAdapter):
        raise ValueError('completed diagnosis requires the actual diagnostic adapter')
    loop.verify_completed(request.work_dir)
    policy.authoring_source(request.work_dir)


def verify_entry_selection(policy, request, *, resume):
    """Completed Article resumes must not silently switch scientific modes."""
    path = Path(request.work_dir) / ENTRY_SPEC_FILENAME
    spec = getattr(policy, 'explicit_diagnostic_entry', None)
    if path.exists():
        if not isinstance(spec, DiagnosticEntrySpec):
            raise ValueError('resuming a diagnostic Article run requires its explicit scientific spec')
        if canonical_hash(json.loads(path.read_text(encoding='utf-8'))) != canonical_hash(spec.model_dump(mode='json')):
            raise ValueError('frozen scientific entry changed on resume')
    elif resume and spec is not None:
        raise ValueError('diagnostic resume requires the existing frozen scientific entry')


def diagnostic_entry_builder(spec, request, provider_factory):
    """Create no resources at assembly; acquire only after committed analysis."""
    spec = DiagnosticEntrySpec.model_validate(spec) if isinstance(spec, dict) else spec
    if not isinstance(spec, DiagnosticEntrySpec) or not callable(provider_factory):
        raise ValueError('scientific entry requires an explicit typed spec and host observation provider factory')
    spec = spec.bind_request(request)

    def build(root):
        from .diagnostic_research import DiagnosticResearchLoop
        from .repository import ScienceRepository
        spec.bind_committed_analysis(root)
        families, config, space, _ = spec.components()
        provider = provider_factory(Path(root), spec.provider)
        resources = []
        if callable(getattr(provider, 'close', None)):
            resources.append(provider)
        try:
            if (getattr(provider, 'provider_id', None) != spec.provider.provider_id
                    or getattr(provider, 'evidence_tier', None) != spec.provider.evidence_tier
                    or getattr(provider, 'noise_validated', None) is not spec.provider.noise_validated
                    or not callable(getattr(provider, 'status', None))
                    or not getattr(provider, 'recovery_identity', None)):
                raise ValueError('observation provider does not match the frozen public contract')
            repository = ScienceRepository(Path(root) / 'science')
            resources.append(repository)
            task = ResearchTask(object_id=spec.run_id + ':task', source_run_id=spec.run_id,
                question=spec.original_question, objective=spec.objective,
                constraints={'source_task': {'article_entry_spec_hash': canonical_hash(spec.model_dump(mode='json')),
                    'article_analysis_hash': spec.analysis_hash, 'source_refs': spec.source_refs},
                    'scope': {'config': asdict(config), 'search_space': asdict(space), 'families': [asdict(f) for f in families]},
                    'evidence_tier': spec.provider.evidence_tier})
            loop = DiagnosticResearchLoop(families=families, config=config, search_space=space,
                repository=repository, run_id=spec.run_id, provider=provider, task=task,
                provider_identity=provider.recovery_identity)
            resources.append(loop)
            adapter = DiagnosticPipelineLoopAdapter(loop, spec, request)
            return adapter, tuple(reversed(resources))
        except BaseException:
            for resource in reversed(resources):
                resource.close()
            raise
    return build
