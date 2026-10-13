"""Selected scientific result → complete manuscript → shared Review delivery.

This is an adapter, not another author/checker. BODY input remains the existing
Article author chain's responsibility. No best-score reselection occurs here.
"""
from __future__ import annotations
from pathlib import Path
import hashlib
import json
from typing import Literal, Any
from .contracts import StrictModel, ObjectRef, ScientificClaim, SelectedResearchResult


class ScientificClaimLedger(StrictModel):
    selected_result_ref: ObjectRef
    claims: tuple[ScientificClaim, ...]


class ResultValue(StrictModel):
    value_id: str
    candidate_ref: ObjectRef
    source_ref: ObjectRef
    value: Any
    units: str
    data_kind: Literal['measurement', 'simulation', 'fit', 'held_out_prediction', 'schematic']


class ResultBundle(StrictModel):
    selected_result: SelectedResearchResult
    claim_ledger: ScientificClaimLedger
    trusted_values: tuple[ResultValue, ...] = ()
    negative_result_refs: tuple[ObjectRef, ...] = ()
    consumer_proof: tuple[dict[str, Any], ...] = ()


def build_result_bundle(selected: SelectedResearchResult, *, claims=(), trusted_values=(), negative_result_refs=()) -> ResultBundle:
    if tuple(c.ref() for c in claims) != selected.claim_refs:
        raise ValueError('claim ledger differs from adopted scientific result')
    for value in trusted_values:
        if value.candidate_ref != selected.candidate_ref:
            raise ValueError('candidate cross-contamination')
        if value.data_kind == 'schematic' and value.units != 'schematic':
            raise ValueError('schematic cannot masquerade as measured numeric evidence')
    if tuple(negative_result_refs) != tuple(getattr(selected, 'negative_result_refs', ())):
        raise ValueError('negative results may not be dropped or substituted')
    return ResultBundle(selected_result=selected,
        claim_ledger=ScientificClaimLedger(selected_result_ref=selected.ref(), claims=tuple(claims)),
        trusted_values=tuple(trusted_values), negative_result_refs=tuple(negative_result_refs))


def deliver_selected_article(*, result_bundle: ResultBundle, manuscript_path, manuscript_sha256: str,
                             source_identity_map, citation_namespace: str, material_records,
                             config, out_dir, manuscript_kind='complete', **delivery_options):
    """Carry authoritative adopted result and selected text through actual 04/05."""
    from optomind_research.runtime.upgrade3.article_delivery import run_article_delivery
    if manuscript_kind != 'complete':
        raise ValueError('BODY must explicitly finish the existing author/front-back chain')
    # Revalidate trust boundary even if caller used model_construct/model_copy.
    bundle = ResultBundle.model_validate(result_bundle.model_dump(mode='json'))
    build_result_bundle(bundle.selected_result, claims=bundle.claim_ledger.claims,
                        trusted_values=bundle.trusted_values, negative_result_refs=bundle.negative_result_refs)
    path = Path(manuscript_path).resolve()
    if hashlib.sha256(path.read_bytes()).hexdigest() != manuscript_sha256:
        raise ValueError('selected manuscript hash mismatch')
    report = run_article_delivery(article_report={'status': 'complete',
        'selected_manuscript': {'path': str(path), 'sha256': manuscript_sha256},
        'adopted_result_ref': bundle.selected_result.ref().model_dump(mode='json'),
        'source_identity_map': source_identity_map, 'citation_namespace': citation_namespace,
        'material_records': material_records}, config=config, out_dir=out_dir, **delivery_options)
    proof = []
    for stage, receipt in report.get('stages', {}).items():
        receipt['adopted_result_ref'] = bundle.selected_result.ref().model_dump(mode='json')
        proof.append({'stage': stage, 'scope': 'delivery_manuscript_lineage_only', 'scientific_validation': False, 'adopted_result_ref': receipt['adopted_result_ref'],
                      'selected_manuscript': receipt.get('selected_manuscript', report.get('selected_manuscript'))})
    report['adopted_result_ref'] = bundle.selected_result.ref().model_dump(mode='json')
    report['consumer_proof'] = proof
    report['negative_result_refs'] = [r.model_dump(mode='json') for r in bundle.negative_result_refs]
    Path(out_dir, 'ARTICLE_SCIENCE_DELIVERY.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def load_result_bundle(repository, selected_result_ref: ObjectRef, *, trusted_values=()) -> ResultBundle:
    """Resolve immutable C01 authority, not a score or toy controller receipt."""
    selected = repository.load(selected_result_ref)
    if not isinstance(selected, SelectedResearchResult):
        raise TypeError('selected result reference must resolve to SelectedResearchResult')
    repository.load(selected.candidate_ref)
    claims = tuple(repository.load(ref) for ref in selected.claim_refs)
    if any(not isinstance(c, ScientificClaim) for c in claims):
        raise TypeError('literature cannot become scientific claims')
    for ref in (selected.inference_ref, *selected.prediction_refs, *selected.negative_result_refs,
                *selected.cost_refs, *selected.asset_refs):
        if ref is not None:
            repository.load(ref)
    for value in trusted_values:
        repository.load(value.source_ref)
    return build_result_bundle(selected, claims=claims, trusted_values=trusted_values,
                               negative_result_refs=selected.negative_result_refs)


def deliver_repository_article(*, repository, selected_result_ref: ObjectRef, trusted_values=(), **kwargs):
    """Production entry: resolve authoritative research selection before delivery."""
    bundle = load_result_bundle(repository, selected_result_ref, trusted_values=trusted_values)
    # The shared checker receives immutable scientific sources alongside the
    # separate literature records, not just an opaque adopted-result identifier.
    materials = list(kwargs.pop("material_records", ()))
    if any(str(row.get("material_id", "")).startswith("scientific:") for row in materials):
        raise ValueError("scientific checker material namespace is reserved")
    refs = [bundle.selected_result.ref(), bundle.selected_result.candidate_ref,
            *([bundle.selected_result.inference_ref] if bundle.selected_result.inference_ref else []),
            *bundle.selected_result.prediction_refs, *bundle.selected_result.asset_refs,
            *bundle.selected_result.negative_result_refs,
            *(ref for claim in bundle.claim_ledger.claims for ref in (claim.ref(), *claim.evidence_refs))]
    seen = set()
    for ref in refs:
        if ref.content_hash in seen:
            continue
        seen.add(ref.content_hash)
        obj = repository.load(ref)
        materials.append({"material_id": "scientific:" + ref.object_id + ":" + str(ref.revision) + ":" + ref.content_hash,
                          "title": "Adopted scientific source " + ref.object_id,
                          "text": json.dumps(obj.model_dump(mode="json"), sort_keys=True),
                          "source_handles": []})
    return deliver_selected_article(result_bundle=bundle, material_records=materials, **kwargs)
