"""Compile explicit, source-qualified implications; never invent numerical priors."""
from __future__ import annotations
from copy import deepcopy
from typing import Any, Literal
from .contracts import StrictModel, ResearchTask, ModelCandidate, ExperimentProgram
from .literature_bridge import LiteratureClaim, ResearchBrief


class CompiledImplication(StrictModel):
    kind: str
    target_path: str
    value_or_expression: Any = None
    units: str = ''
    domain_predicate: dict[str, Any]
    source_refs: tuple[str, ...]
    status: Literal['compiled', 'partial', 'uncompilable', 'inapplicable']
    missing_fields: tuple[str, ...] = ()


ROOTS = {'constraint': ('ResearchTask', 'constraints'), 'model_seed': ('ModelCandidate', 'parameter_values'),
         'probe': ('ExperimentProgram', 'protocol'), 'control': ('ExperimentProgram', 'protocol'),
         'metric_definition': ('ResearchTask', 'objective')}


def compile_claim_implication(claim: LiteratureClaim, specification: dict, *, task_conditions: dict) -> CompiledImplication:
    kind, target = specification.get('kind', ''), specification.get('target_path', '')
    missing = tuple(k for k in ('kind', 'target_path', 'value_or_expression', 'units') if k not in specification)
    status = 'compiled'
    if kind not in ROOTS or not claim.source_spans or claim.attribution != 'source_author':
        status = 'uncompilable'
    elif not target.startswith('.'.join(ROOTS[kind]) + '.') or any(p in {'', '__class__', '__dict__'} for p in target.split('.')):
        status = 'uncompilable'
    elif any(k not in task_conditions for k in claim.conditions):
        status = 'partial'
    elif any(task_conditions[k] != v for k, v in claim.conditions.items()):
        status = 'inapplicable'
    elif missing or claim.material_depth in {'abstract_only', 'metadata_only', 'navigation'}:
        status = 'partial'
    # A source range is a constraint, never an automatic probability distribution.
    if any(word in target.lower() for word in ('probability', 'posterior', 'prior_distribution')):
        status = 'uncompilable'
    return CompiledImplication(kind=kind, target_path=target, value_or_expression=specification.get('value_or_expression'),
        units=specification.get('units', ''), domain_predicate=claim.conditions, source_refs=(claim.claim_id,), status=status, missing_fields=missing)


def apply_research_brief(brief: ResearchBrief, target, *, task_conditions: dict, adopted_claim_ids: tuple[str, ...]):
    """Apply approved compiled implications to real C02/C06/C12 objects, returning a new revision."""
    if not isinstance(target, (ResearchTask, ModelCandidate, ExperimentProgram)):
        raise TypeError('unsupported scientific consumer')
    if set(adopted_claim_ids) - set(brief.claim_refs):
        raise ValueError('unknown adopted literature claim')
    if isinstance(target, ResearchTask) and target.ref() != brief.task_ref:
        raise ValueError('research brief task revision mismatch')
    payload = deepcopy(target.model_dump(mode='json'))
    applied = []
    for claim in brief.claims:
        if claim.claim_id not in adopted_claim_ids:
            continue
        for spec in claim.implications:
            implication = compile_claim_implication(claim, spec, task_conditions=task_conditions)
            parts = implication.target_path.split('.')
            if implication.status != 'compiled' or parts[0] != type(target).__name__:
                continue
            cursor = payload
            for part in parts[1:-1]:
                existing = cursor.setdefault(part, {})
                if not isinstance(existing, dict):
                    raise ValueError('implication target conflicts with scalar field')
                cursor = existing
            cursor[parts[-1]] = implication.value_or_expression
            applied.append(implication.model_dump(mode='json'))
    if not applied:
        return target, ()
    payload['revision'] += 1
    payload['content_hash'] = ''
    payload['input_refs'] = [*payload['input_refs'], brief.task_ref.model_dump(mode='json')]
    return type(target).model_validate(payload), tuple(applied)
