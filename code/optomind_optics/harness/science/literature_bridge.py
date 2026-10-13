"""The single Article compiler of Review's public evidence interface.

Literature remains author-attributed material, never a scientific observation or
an inferred probability. Adoption is explicit and task-local.
"""
from __future__ import annotations
from typing import Any, Literal
import re
from pydantic import Field
from .contracts import StrictModel, ObjectRef, canonical_hash


class WorkIdentity(StrictModel):
    key: str


class DocumentVersionIdentity(StrictModel):
    work: WorkIdentity
    snapshot_id: str
    source_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


class LiteratureClaim(StrictModel):
    claim_id: str
    claim_type: Literal['LiteratureClaim'] = 'LiteratureClaim'
    attribution: Literal['source_author', 'system_bridge', 'current_conclusion'] = 'source_author'
    document: DocumentVersionIdentity
    statement: str
    material_depth: str
    roles: tuple[str, ...]
    conditions: dict[str, Any]
    conflicts: tuple[Any, ...]
    source_spans: tuple[dict[str, Any], ...]
    implications: tuple[dict[str, Any], ...] = ()


class ResearchBrief(StrictModel):
    task_ref: ObjectRef
    evidence_task_id: str
    claim_refs: tuple[str, ...]
    claims: tuple[LiteratureClaim, ...]
    conditions: dict[str, Any]
    conflicts: tuple[Any, ...]
    gaps: tuple[str, ...]
    read_receipts: tuple[dict[str, Any], ...]
    input_hash: str
    diagnostics: tuple[dict[str, Any], ...] = ()


def build_research_brief(bundle, *, task_ref: ObjectRef, citation_namespace: str) -> ResearchBrief:
    from optomind_research.runtime.upgrade3.article_evidence import research_brief_input, EvidenceBundle
    # Always validate even an in-memory dataclass; shallow frozen mappings can mutate.
    raw = bundle.to_dict() if hasattr(bundle, 'to_dict') else bundle
    verified = EvidenceBundle.from_mapping(raw)
    data = research_brief_input(verified)['evidence_bundle']
    if not citation_namespace or '::' in citation_namespace:
        raise ValueError('citation namespace required')
    claims, receipts = [], []
    for unit in data['source_units']:
        canonical = unit['canonical_paper_id']
        doi = str(unit.get('doi') or '').lower().removeprefix('https://doi.org/').removeprefix('doi:').strip()
        key = 'doi:' + doi if doi else (citation_namespace + '::' + canonical if re.fullmatch(r'P[0-9]+', canonical) else 'canonical:' + canonical)
        document = DocumentVersionIdentity(work=WorkIdentity(key=key), snapshot_id=unit['snapshot_id'], source_hash=unit['source_hash'])
        spans = tuple(unit['anchors'])
        claim = LiteratureClaim(claim_id='literature-' + canonical_hash({'document': document.model_dump(), 'spans': spans}),
            document=document, statement=unit.get('statement') or '\n'.join(a['quote'] for a in spans),
            material_depth=unit['material_depth'], roles=tuple(unit['roles']), conditions=unit['conditions'],
            conflicts=tuple(unit['counter_evidence']), source_spans=spans,
            implications=tuple(unit.get('implications') or ()))
        claims.append(claim)
        receipts.append({'document': document.model_dump(), 'source_spans': list(spans), 'material_depth': claim.material_depth})
    return ResearchBrief(task_ref=task_ref, evidence_task_id=data['task_id'], claim_refs=tuple(c.claim_id for c in claims),
        claims=tuple(claims), conditions=dict(data['request']['object_conditions']),
        conflicts=tuple(x for c in claims for x in c.conflicts), gaps=tuple(data['unmet_outputs']),
        read_receipts=tuple(receipts), input_hash=canonical_hash(data), diagnostics=tuple(data.get('diagnostics') or ()))


def adopt_evidence_delta(previous: ResearchBrief, bundle, *, task_ref: ObjectRef, citation_namespace: str, adopted: bool) -> ResearchBrief:
    if not adopted:
        raise ValueError('evidence delta requires explicit adoption')
    if task_ref.object_id != previous.task_ref.object_id:
        raise ValueError('delta task mismatch; recompile raw sources for the new task')
    return build_research_brief(bundle, task_ref=task_ref, citation_namespace=citation_namespace)
