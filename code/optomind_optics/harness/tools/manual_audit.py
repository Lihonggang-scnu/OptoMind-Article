from __future__ import annotations

import argparse
import hashlib
from dataclasses import asdict
import json
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from ..science.contracts import StrictModel, Observation, canonical_hash
from ..science.prediction import compare_bound_observation
from ..science.repository import ScienceRepository
from ..science.dataset_contracts import DatasetCard, DatasetSplitManifest
from .external_measurement import ManualReturn, validate_manual_source
from .measured_data_adapter import RecordedMeasurement


class IndependentQualityAudit(StrictModel):
    schema_version: Literal['manual-quality-audit.v1'] = 'manual-quality-audit.v1'
    protocol_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    prediction_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    source_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    sample_id: str = Field(min_length=1)
    batch_id: str = Field(min_length=1)
    calibration: str = Field(min_length=1)
    reviewer_id: str = Field(min_length=1)
    report_source_hash: str = Field(pattern=r'^[0-9a-f]{64}$')
    status: Literal['passed', 'failed', 'inconclusive']
    controls: Literal['passed', 'failed', 'unverified']
    numerical: Literal['passed', 'failed', 'unverified']
    uncertainty: Literal['passed', 'failed', 'unverified']
    source_evidence_refs: tuple[str, ...] = Field(min_length=1)
    independent_scope: Literal['same_sample_new_conditions', 'same_batch_new_sample', 'cross_batch', 'cross_instrument']
    limitations: tuple[str, ...] = ()

    @model_validator(mode='after')
    def consistent(self):
        if self.status == 'passed' and any(v != 'passed' for v in (self.controls,self.numerical,self.uncertainty)):
            raise ValueError('passed quality audit requires all component checks')
        return self


def _validate_audit_binding(protocol, returned, spec, audit):
    if (audit.protocol_hash,audit.prediction_hash,audit.source_hash,audit.sample_id,audit.batch_id,audit.calibration) != (
        protocol.content_hash,spec.content_hash,returned.raw_sha256,returned.sample_id,returned.batch_id,returned.calibration):
        raise ValueError('quality audit does not bind the exact frozen prediction and returned source')
    development_batches = {batch for sample, batch in protocol.samples if sample not in protocol.holdout}
    if audit.independent_scope == 'cross_batch' and returned.batch_id in development_batches:
        raise ValueError('cross-batch scope conflicts with shared development batch')
    if audit.reviewer_id == protocol.operator:
        raise ValueError('independent reviewer cannot be the recorded measurement operator')
    if audit.controls == 'passed' and not returned.control_evidence_refs:
        raise ValueError('controls declared passed without returned control evidence')


def audit_manual_holdout(repository, frozen, returned, card, split, record, source_root, audit, output, *, review_report):
    audit = IndependentQualityAudit.model_validate(audit.model_dump(mode='json'))
    if hashlib.sha256(Path(review_report).read_bytes()).hexdigest() != audit.report_source_hash:
        raise ValueError('independent review report source hash mismatch')
    returned = ManualReturn.model_validate(returned.model_dump(mode='json'))
    protocol, receipt, spec, source = validate_manual_source(repository, frozen, returned, card, split, record, source_root,
                                                            purpose='independent_audit')
    _validate_audit_binding(protocol, returned, spec, audit)
    for ref in repository.latest_snapshot().object_refs:
        existing = repository.load(ref)
        if isinstance(existing, Observation) and any(block.get('source_hash') == returned.raw_sha256 for block in existing.raw_blocks):
            raise ValueError('holdout source was already revealed to the research repository')
    report_path = Path(output)
    if report_path.resolve().is_relative_to(repository.root.resolve()):
        raise ValueError('holdout audit output must remain outside the research repository')
    binding = canonical_hash({'protocol':protocol.content_hash,'return':returned.model_dump(mode='json'),'dataset':card.content_hash,
                              'audit':audit.model_dump(mode='json'),'record':asdict(record),'split':asdict(split)})
    valid = audit.status == 'passed'
    observation = Observation(object_id='independent-holdout:'+binding, source_run_id=protocol.protocol_id,
        acquisition_id='manual-raw:'+returned.raw_sha256,candidate_ref=spec.candidate_ref,
        input_refs=(protocol.task,spec.ref()),raw_blocks=source.raw_blocks,
        binding={**source.binding,'program_ref':spec.program_ref.model_dump(mode='json') if spec.program_ref else None},
        quality={**source.quality,'execution_status':returned.execution_status,
                 'numerical_valid':valid and audit.numerical == 'passed' and source.quality.get('numerical_valid') is True,
                 'noise_validated':valid and audit.uncertainty == 'passed' and source.quality.get('noise_validated') is True,
                 'physical_provenance':'external_auditor_report_not_authenticated',
                 'V4_accepted':False,'V5_accepted':False})
    verdict = compare_bound_observation(spec,observation)
    result = {'schema_version':'independent-holdout-report.v1','binding':binding,
              'protocol_hash':protocol.content_hash,'prediction_ref':spec.ref().model_dump(mode='json'),
              'source_hash':returned.raw_sha256,'audit':audit.model_dump(mode='json'),
              'prediction_verdict':asdict(verdict),'independent_scope':audit.independent_scope,
              'raw_data_released_to_research':False,'hardware_executed':False,
              'V4_accepted':False,'V5_accepted':False,
              'scope':'Deterministic source-bound audit of an externally supplied review; reviewer identity and physical acquisition authenticity are not certified.'}
    result['prediction_verdict']['prediction_ref'] = spec.ref().model_dump(mode='json')
    result['prediction_verdict']['observation_ref'] = observation.ref().model_dump(mode='json')
    result['report_hash'] = canonical_hash(result)
    if report_path.exists():
        prior = json.loads(report_path.read_text())
        if canonical_hash(prior) != canonical_hash(result):
            raise ValueError('holdout audit output identity changed')
        return result
    report_path.parent.mkdir(parents=True,exist_ok=True)
    with report_path.open('x') as stream:
        json.dump(result,stream,sort_keys=True,indent=2)
    return result


def audit_manual_development(repository, frozen, returned, card, split, record, source_root, audit, *, review_report, output=None):
    audit = IndependentQualityAudit.model_validate(audit.model_dump(mode='json'))
    returned = ManualReturn.model_validate(returned.model_dump(mode='json'))
    if hashlib.sha256(Path(review_report).read_bytes()).hexdigest() != audit.report_source_hash:
        raise ValueError('independent review report source hash mismatch')
    protocol, receipt, spec, source = validate_manual_source(repository, frozen, returned, card, split, record, source_root)
    _validate_audit_binding(protocol, returned, spec, audit)
    expected_id = 'manual:' + canonical_hash(returned)
    objects = [repository.load(ref) for ref in repository.latest_snapshot().object_refs if ref.object_id == expected_id]
    if len(objects) != 1 or not isinstance(objects[0], Observation):
        raise ValueError('development observation must be imported before quality review')
    observation = objects[0]
    if observation.raw_blocks != source.raw_blocks or observation.candidate_ref != spec.candidate_ref:
        raise ValueError('quality review cannot alter previously imported measurement values or candidate')
    binding = canonical_hash({'audit':audit.model_dump(mode='json'),'dataset':card.content_hash,'record':asdict(record)})
    previous = observation.quality.get('audit_binding')
    if previous is not None and previous != binding:
        raise ValueError('existing quality audit differs; explicit reviewed revision is required')
    if previous is None:
        passed = audit.status == 'passed'
        payload = observation.model_dump(mode='json')
        payload.update(revision=observation.revision+1,content_hash='')
        payload['quality'].update(audit_binding=binding,quality_audit='source_bound_external_report',
            numerical_valid=passed and source.quality.get('numerical_valid') is True,
            noise_validated=passed and source.quality.get('noise_validated') is True,
            independent_quality_audit=audit.model_dump(mode='json'),
            physical_provenance='external_auditor_report_not_authenticated',V4_accepted=False,V5_accepted=False)
        observation = Observation.model_validate(payload)
    verdict = compare_bound_observation(spec, observation)
    result = {'observation_ref':observation.ref().model_dump(mode='json'),'audit_binding':binding,
            'prediction_verdict':verdict.state,'noise_validated':observation.quality['noise_validated'],
            'acquisition_identity_unchanged':True,'V4_accepted':False,'V5_accepted':False,
            'scope':'Audited canonical development observation; existing raw source and acquisition identity retained.'}
    if output is not None and Path(output).exists():
        if json.loads(Path(output).read_text()) != result:
            raise ValueError('development audit result identity changed')
    from ..science.prediction import PredictionCommit, evaluate_prediction
    if previous is None:
        prior_ref = objects[0].ref()
        evaluate_prediction(repository, PredictionCommit(**protocol.prediction_commit.__dict__), prior_ref)
        repository.commit([observation],task_ref=protocol.task,evidence_cutoff=returned.acquired_at,
                          expected_sequence=repository.latest_snapshot().committed_sequence)
    checked = evaluate_prediction(repository,PredictionCommit(**protocol.prediction_commit.__dict__),observation.ref())
    if checked != verdict:
        raise ValueError('canonical development audit verdict differs from validated projection')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--purpose', choices=('holdout','development'), default='holdout')
    parser.add_argument('--repository',required=True); parser.add_argument('--frozen',required=True)
    parser.add_argument('--bundle',required=True); parser.add_argument('--quality-audit',required=True)
    parser.add_argument('--review-report',required=True)
    parser.add_argument('--source-root',required=True); parser.add_argument('--output',required=True)
    args = parser.parse_args(argv)
    bundle = json.loads(Path(args.bundle).read_text())
    card = DatasetCard(**bundle['card'])
    split = DatasetSplitManifest(**bundle['split'])
    record = RecordedMeasurement(**bundle['record'])
    audit = IndependentQualityAudit.model_validate_json(Path(args.quality_audit).read_text())
    repository = ScienceRepository(args.repository)
    try:
        if args.purpose == 'holdout':
            result = audit_manual_holdout(repository,args.frozen,ManualReturn.model_validate(bundle['return']),card,split,record,args.source_root,audit,args.output,review_report=args.review_report)
        else:
            result = audit_manual_development(repository,args.frozen,ManualReturn.model_validate(bundle['return']),card,split,record,args.source_root,audit,review_report=args.review_report,output=args.output)
            out = Path(args.output)
            if out.exists():
                if json.loads(out.read_text()) != result:
                    raise ValueError('development audit result identity changed')
            else:
                out.parent.mkdir(parents=True,exist_ok=True)
                with out.open('x') as stream: json.dump(result,stream,indent=2)
        print(json.dumps(result,ensure_ascii=False))
    finally:
        repository.close()


if __name__ == '__main__':
    main()
