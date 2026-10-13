"""Repository-backed opt-in evidence scope for the existing Article author chain."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from pydantic import Field
from .contracts import StrictModel, ObjectRef, canonical_hash
from .article_bridge import load_result_bundle


class AuthoringValueBinding(StrictModel):
    artifact_id: str
    field: str
    source_ref: ObjectRef
    source_path: tuple[str | int, ...]
    units: str
    data_kind: Literal['measurement', 'simulation', 'fit', 'held_out_prediction', 'schematic', 'unknown']


class AuthoringSelection(StrictModel):
    repository_root: str
    selected_result_ref: ObjectRef
    candidate_bindings: dict[str, ObjectRef]
    artifact_bindings: dict[str, ObjectRef]
    value_bindings: tuple[AuthoringValueBinding, ...] = ()


def _same_value(raw, rendered):
    if str(raw) == rendered:
        return True
    if isinstance(raw, bool):
        return False
    try:
        return Decimal(str(raw)).is_finite() and Decimal(str(raw)) == Decimal(rendered)
    except (InvalidOperation, ValueError):
        return False


def resolve_authoring_selection(selection: AuthoringSelection, assets) -> dict[str, Any]:
    from .repository import ScienceRepository
    import sqlite3
    try:
        repo = ScienceRepository(selection.repository_root, read_only=True)
    except sqlite3.Error as exc:
        raise ValueError('scientific repository is unavailable') from exc
    try:
        bundle = load_result_bundle(repo, selection.selected_result_ref)
        selected = bundle.selected_result
        if not selection.candidate_bindings or any(ref != selected.candidate_ref for ref in selection.candidate_bindings.values()):
            raise ValueError('authoring candidates must bind exactly the adopted scientific candidate')
        candidates = _unique_records((c.candidate_id,c) for a in assets for c in a.candidates)
        if set(selection.candidate_bindings) - set(candidates):
            raise ValueError('authoring legacy candidate binding absent from verified assets')
        allowed_refs = {canonical_hash(r) for r in (*selected.asset_refs, *selected.negative_result_refs,
            *(ref for claim in bundle.claim_ledger.claims for ref in claim.evidence_refs))}
        if any(canonical_hash(ref) not in allowed_refs for ref in selection.artifact_bindings.values()):
            raise ValueError('authoring artifact is not frozen scientific evidence')
        descriptors = _unique_records((d.artifact_id,d) for a in assets for d in a.descriptors)
        if set(selection.artifact_bindings) - set(descriptors):
            raise ValueError('authoring legacy artifact absent from verified inventory')
        for candidate in candidates.values():
            if candidate.candidate_id not in selection.candidate_bindings and set(candidate.artifact_ids) & set(selection.artifact_bindings):
                raise ValueError('authoring asset belongs to unselected candidate')
        records = _unique_records(((v.artifact_id,v.field),v) for a in assets for v in a.trusted_values)
        verified_values = []
        keys = set()
        for binding in selection.value_bindings:
            key=(binding.artifact_id,binding.field)
            if key in keys or key not in records:
                raise ValueError('duplicate or unknown authoring value')
            keys.add(key)
            if selection.artifact_bindings.get(binding.artifact_id) != binding.source_ref:
                raise ValueError('authoring value source differs from artifact binding')
            raw = repo.load(binding.source_ref).model_dump(mode='json')
            parent = None
            try:
                for part in binding.source_path:
                    parent = raw
                    raw = raw[part]
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError('scientific value source path does not resolve') from exc
            value = records[key]
            if not _same_value(raw, value.rendered_value) or binding.units != value.unit:
                raise ValueError('authoring number or units disagree with immutable scientific source')
            # Qualification must come from the same immutable value record, not
            # mutually agreeing caller labels. Bare scalars remain unqualified.
            metadata = parent if isinstance(parent, dict) else {}
            source_units = metadata.get('units', metadata.get('unit', 'unknown'))
            source_kind = metadata.get('data_kind', 'unknown')
            if binding.units != source_units or binding.data_kind != source_kind:
                raise ValueError('authoring units or data kind lack matching immutable source metadata')
            if binding.data_kind == 'schematic' and value.prose_safe:
                raise ValueError('schematic data cannot be a trusted quantitative prose value')
            verified_values.append(binding.model_dump(mode='json'))
        claims=[c.model_dump(mode='json') for c in bundle.claim_ledger.claims]
        if not claims:
            raise ValueError('authoring requires explicit adopted ScientificClaims')
        if len({c['statement'] for c in claims}) != len(claims):
            raise ValueError('ambiguous adopted statements require distinct source-qualified claims')
        represented = {canonical_hash(r) for c in bundle.claim_ledger.claims for r in (c.ref(), *c.evidence_refs)}
        if any(canonical_hash(ref) not in represented for ref in selected.negative_result_refs):
            raise ValueError('every adopted negative outcome requires a source-bound scientific claim')
        return {'adopted_result_ref':selected.ref().model_dump(mode='json'),
            'selected_candidate_ids':sorted(selection.candidate_bindings),
            'candidate_roles':selected.candidate_roles,
            'allowed_artifact_ids':sorted(selection.artifact_bindings),
            'verified_value_bindings':verified_values,
            'scientific_claims':claims,
            'negative_results':[repo.load(ref).model_dump(mode='json') for ref in selected.negative_result_refs],
            'equivalent_explanations':list(selected.equivalent_explanations),
            'claim_strength':selected.claim_strength,
            'literature_claims_are_separate':True,
            'scope':'adopted_research_result_only', 'binding_hash':canonical_hash(selection),
            'synthesis_contract': {'required_finding_field':'scientific_claim_id',
                'statement_rule':'Resolved value-token statement must equal its adopted ScientificClaim statement.',
                'unknown_or_refuted_rule':'Use limitation role; do not promote to supported result.'}}
    finally:
        repo.close()


def scope_synthesis_payloads(manifest, payloads, context):
    """Remove non-adopted candidate/value aliases before any provider sees them."""
    allowed_candidates=set(context['selected_candidate_ids'])
    allowed_artifacts=set(context['allowed_artifact_ids'])
    allowed_values={(v['artifact_id'],v['field']) for v in context['verified_value_bindings']}
    retained={alias:record for alias,record in manifest.items()
              if (record.kind == 'route' or
                  record.kind == 'candidate' and record.candidate_id in allowed_candidates or
                  record.kind == 'artifact' and record.artifact_id in allowed_artifacts or
                  record.kind == 'trusted_value' and (record.artifact_id,record.field) in allowed_values)}
    for alias,record in list(retained.items()):
        local_aliases={r.alias for k,r in retained.items() if k.startswith(alias.split('.')[0]+'.')}
        if record.kind=='candidate':
            retained[alias]=record.model_copy(update={'role_keys':['adopted_result'], 'route_role_keys':['adopted_result'],
                'global_role_keys':[], 'artifact_aliases':[a for a in record.artifact_aliases if a in local_aliases],
                'value_aliases':[a for a in record.value_aliases if a in local_aliases]})
    scoped={}
    for key,payload in payloads.items():
        local_aliases={r.alias for k,r in retained.items() if k.startswith(payload.route_alias+'.')}
        candidates=[c.model_copy(update={'role_keys':['adopted_result'], 'route_role_keys':['adopted_result'],
            'global_role_keys':[], 'is_baseline':False,'is_pareto':False,
            'target_score':None,'robustness_score':None,'simplicity_score':None,'distinctiveness_score':None,
            'artifact_aliases':[a for a in c.artifact_aliases if a in local_aliases],
            'value_aliases':[a for a in c.value_aliases if a in local_aliases]})
            for c in payload.candidates if c.candidate_id in allowed_candidates]
        if not candidates:
            continue
        scoped[key]=payload.model_copy(update={'candidates':candidates,
            'artifacts':[v for v in payload.artifacts if v.alias in local_aliases],
            'trusted_values':[v for v in payload.trusted_values if v.alias in local_aliases],
            'observation_summary':'Use only the adopted scientific claims and bound source values.',
            'scientific_selection':context})
    return retained,scoped


def filter_adopted_findings(findings, manifest, context, warnings):
    """Structured claims may only restate exact adopted scientific statements.

    Prose authors can paraphrase downstream under the existing evidence checker.
    Unknown/refuted outcomes must be represented as limitations, never support.
    """
    from ..article_result_synthesis import _resolve_tokens
    claims={c['object_id']:c for c in context['scientific_claims']}
    result=[]
    for finding in findings:
        claim=claims.get(finding.get('scientific_claim_id'))
        text=_resolve_tokens(str(finding.get('statement_with_value_tokens') or ''),manifest)
        if claim is None or text != claim['statement']:
            warnings.append('rejected synthesis claim outside adopted scientific ledger')
            continue
        if claim['status'] != 'supported' and finding.get('role') != 'limitation':
            warnings.append('rejected promotion of unknown/refuted scientific outcome')
            continue
        result.append({k:v for k,v in finding.items() if k!='scientific_claim_id'})
    return result


def required_negative_claim_ids(ledger):
    return {c.claim_id for c in ledger.claims if c.metadata.get('scientific_status') in {'unknown', 'refuted', 'proposed'}}


def missing_negative_claims(ledger, represented):
    return sorted(required_negative_claim_ids(ledger) - set(represented))


def optional_authoring_identity(value):
    """Do not invalidate ordinary-mode identities merely by adding optional fields."""
    if isinstance(value, dict):
        return {k:optional_authoring_identity(v) for k,v in value.items()
                if not ((k in {'scientific_selection', 'adopted_result_ref'} and v is None)
                        or (k == 'source_kind' and v == 'legacy_pipeline'))}
    if isinstance(value, (tuple,list)):
        return [optional_authoring_identity(v) for v in value]
    return value


def _unique_records(rows):
    result = {}
    for key,value in rows:
        if key in result and result[key] != value:
            raise ValueError('conflicting verified authoring identity: ' + str(key))
        result[key] = value
    return result
