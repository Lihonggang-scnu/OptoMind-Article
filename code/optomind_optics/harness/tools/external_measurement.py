"""Offline prospective manual handoff. No hardware or network implementation.

A local freeze proves ordering inside this repository, not laboratory authenticity.
All returned measurements remain independently unaudited; approval references are
metadata, never permission to actuate equipment.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path

from pydantic import Field, model_validator

from ..science.contracts import StrictModel, ObjectRef, PredictionSpec, Observation, canonical_hash
from ..science.prediction import PredictionCommit, evaluate_prediction
from ..science.repository import ScienceRepository
from ..science.jobs import ExecutionGate, ToolCapability
from ..science.program_ir import ProgramIR, ProgramNode
from ..science.program_compiler import compile_program
from ..science.dataset_contracts import DatasetCard, DatasetSplitManifest
from .measured_data_adapter import RecordedMeasurement, MeasuredObservationAdapter


class FrozenPrediction(StrictModel):
    prediction_ref: ObjectRef
    prior_snapshot_id: str
    committed_snapshot_id: str
    committed_sequence: int = Field(ge=1)


class ProspectiveProtocol(StrictModel):
    protocol_id: str = Field(min_length=1)
    task: ObjectRef
    prediction_commit: FrozenPrediction
    samples: tuple[tuple[str, str], ...]  # sample, fabrication batch
    calibration: str = Field(min_length=1)
    calibration_expires_at: str
    controls: tuple[str, ...] = ('reference', 'dark')
    randomized_order: tuple[str, ...]
    randomization_basis: str = Field(min_length=1)
    repeats: int = Field(ge=1, le=64)
    repeats_basis: str = Field(min_length=1)
    operator: str = Field(min_length=1)
    operator_approval_ref: str | None = None
    safety_approval_ref: str | None = None
    environment: tuple[tuple[str, str], ...]
    failure_rules: tuple[str, ...]
    budget: tuple[tuple[str, float], ...]
    holdout: tuple[str, ...]
    holdout_basis: str = Field(min_length=1)
    channel: str = Field(min_length=1)

    @model_validator(mode='after')
    def validate_protocol(self):
        samples = dict(self.samples)
        if not samples or len(samples) != len(self.samples) or any(not a or not b for a,b in self.samples):
            raise ValueError('unique sample and batch identities required')
        if len(self.randomized_order) != len(samples) or set(self.randomized_order) != set(samples):
            raise ValueError('randomized order must contain every sample exactly once')
        if len(set(self.holdout)) != len(self.holdout) or not self.holdout or not set(self.holdout) < set(samples):
            raise ValueError('nonempty holdout and development sample sets required')
        if not {'reference', 'dark'}.issubset(self.controls):
            raise ValueError('reference and dark controls required')
        if not self.environment or not self.failure_rules:
            raise ValueError('environment and explicit failure rules required')
        if len(dict(self.budget)) != len(self.budget) or not {'human_minutes', 'fabrication_cost', 'readings'}.issubset(dict(self.budget)):
            raise ValueError('explicit human, fabrication and reading budgets required')
        if any(not math.isfinite(value) or value < 0 for _,value in self.budget):
            raise ValueError('budget values must be finite and nonnegative')
        _time(self.calibration_expires_at)
        compile_human_plan(self)
        return self

    @property
    def content_hash(self):
        return canonical_hash(self)


def _time(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('timezone-aware timestamp required')
    return stamp


def compile_human_plan(protocol):
    # Compilation validates topology/resources only. Never run this with a driver.
    nodes = []
    sequence = [(control, '') for control in protocol.controls]
    sequence += [(f'{sample}:repeat:{repeat}', sample) for sample in protocol.randomized_order for repeat in range(protocol.repeats)]
    for index, (label, sample) in enumerate(sequence):
        nodes.append(ProgramNode(str(index), 'new_evidence', (str(index-1),) if index else (),
                                 channel=protocol.channel, sample_id=sample,
                                 arguments=(('human_instruction', label),), estimate=(('readings', 1.),),
                                 correlation_group=protocol.calibration))
    cap = ToolCapability(effect_class='external_measurement', domain='offline_manual_plan_only', channels=(protocol.channel,))
    plan = compile_program(ProgramIR(protocol.protocol_id, tuple(nodes), tuple((s,'ready') for s,_ in protocol.samples)),
                           capabilities={protocol.channel:cap}, limits=dict(protocol.budget))
    return plan


def validate_binding(repository, protocol):
    repository.load(protocol.task)
    commit = protocol.prediction_commit
    spec = repository.load(commit.prediction_ref)
    snapshot = repository.snapshot(commit.committed_snapshot_id)
    if not isinstance(spec, PredictionSpec) or snapshot.task_ref != protocol.task or spec.raw_channel != protocol.channel:
        raise ValueError('task/prediction/channel identity mismatch')
    if repository.object_commit_sequence(spec.ref()) != commit.committed_sequence or snapshot.committed_sequence != commit.committed_sequence or spec.snapshot_id != commit.prior_snapshot_id:
        raise ValueError('prediction commit receipt mismatch')
    if spec.program_ref is not None:
        program = repository.load(spec.program_ref)
        if getattr(program, 'protocol', {}).get('program_ir_hash') != compile_human_plan(protocol).program.content_hash:
            raise ValueError('prediction program does not bind compiled manual protocol')
    return spec


def freeze_protocol(repository, protocol, output):
    """Write once, reserve only. Offline references cannot self-authorize hardware."""
    protocol = ProspectiveProtocol.model_validate(protocol.model_dump(mode='json'))
    validate_binding(repository, protocol)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    target = output/'protocol.json'
    payload = protocol.model_dump(mode='json')
    if target.exists() and json.loads(target.read_text()) != payload:
        raise ValueError('frozen protocol cannot change prediction or execution identity')
    latest = repository.latest_snapshot()
    if not target.exists() and latest.snapshot_id != protocol.prediction_commit.committed_snapshot_id:
        raise ValueError('freeze must immediately follow committed prediction before new evidence')
    # C03 durable idempotency and resource reservation; no submit/execute call.
    gate = ExecutionGate(output/'reservation.sqlite3', limits=dict(protocol.budget),
                         capability=ToolCapability(effect_class='read_only', domain='offline_protocol_reservation'))
    try:
        gate.reserve(protocol.protocol_id, payload, estimate=dict(protocol.budget),
                     prediction_commit=protocol.prediction_commit.model_dump(mode='json'))
    finally:
        gate.close()
    if not target.exists():
        with target.open('x') as stream:
            json.dump(payload, stream, sort_keys=True, indent=2)
    receipt_path = output/'preexecution.json'
    if not receipt_path.exists():
        receipt = {'protocol_hash':protocol.content_hash, 'frozen_at':datetime.now(timezone.utc).isoformat(),
                   'snapshot_id':latest.snapshot_id, 'hardware_executed':False}
        receipt['receipt_hash'] = canonical_hash(receipt)
        with receipt_path.open('x') as stream:
            json.dump(receipt, stream, sort_keys=True, indent=2)
    receipt = json.loads(receipt_path.read_text())
    gate = ExecutionGate(output/'reservation.sqlite3', limits=dict(protocol.budget),
                         capability=ToolCapability(effect_class='read_only', domain='offline_protocol_reservation'))
    try:
        gate.reserve(protocol.protocol_id+':preexecution', receipt, estimate={},
                     prediction_commit=protocol.prediction_commit.model_dump(mode='json'))
    finally:
        gate.close()
    return load_frozen(output)


def load_frozen(directory):
    directory = Path(directory)
    protocol = ProspectiveProtocol.model_validate_json((directory/'protocol.json').read_text())
    receipt = json.loads((directory/'preexecution.json').read_text())
    if receipt.get('protocol_hash') != protocol.content_hash or receipt.get('receipt_hash') != canonical_hash({k:v for k,v in receipt.items() if k!='receipt_hash'}):
        raise ValueError('preexecution receipt mismatch')
    if receipt['snapshot_id'] != protocol.prediction_commit.committed_snapshot_id or receipt['hardware_executed'] is not False:
        raise ValueError('preexecution identity mismatch')
    # Compare with C03's durable identity, not just a recomputable JSON digest.
    with sqlite3.connect(f'file:{directory.resolve() / "reservation.sqlite3"}?mode=ro', uri=True) as db:
        for key, payload in ((protocol.protocol_id, protocol.model_dump(mode='json')), (protocol.protocol_id+':preexecution', receipt)):
            row = db.execute('SELECT payload_hash,prediction FROM jobs WHERE key=?', (key,)).fetchone()
            if not row or row[0] != canonical_hash(payload) or json.loads(row[1]) != protocol.prediction_commit.model_dump(mode='json'):
                raise ValueError('durable preexecution reservation mismatch')
    return protocol, receipt


def human_protocol(protocol):
    lines = ['OFFLINE MANUAL HANDOFF: laboratory authorization and independent audit are pending.',
             'This document issues no hardware command and certifies no approval.',
             f'Protocol: {protocol.protocol_id} / {protocol.content_hash}',
             f'Prediction: {protocol.prediction_commit.prediction_ref.content_hash}',
             f'Operator: {protocol.operator}; approval reference: {protocol.operator_approval_ref or "MISSING"}',
             f'Safety approval reference: {protocol.safety_approval_ref or "MISSING"}',
             '1. Responsible laboratory operator must verify equipment, samples, calibration, approvals and local safety rules before any physical work.',
             f'2. Verify calibration {protocol.calibration}, expiry {protocol.calibration_expires_at}.',
             f'3. Record environment: {dict(protocol.environment)}.',
             f'4. Acquire controls: {", ".join(protocol.controls)}.',
             f'5. Follow frozen randomized sample order: {", ".join(protocol.randomized_order)}.',
             f'6. Repeat each sample {protocol.repeats} times; basis: {protocol.repeats_basis}. Repeated readings are not independent fabrication batches.',
             f'7. Stop/failure rules: {"; ".join(protocol.failure_rules)}.',
             f'8. Preserve holdout: {", ".join(protocol.holdout)}; {protocol.holdout_basis}.',
             '9. Return unmodified raw files, source hashes, sample/batch/calibration identities, preexecution receipt hash, actual acquisition timestamps, controls and operator evidence.',
             '10. Import is pending independent quality audit; do not claim V4/V5 acceptance or redesign using sealed holdout.',
             f'Budget ceiling (not an expenditure authorization): {dict(protocol.budget)}',
             f'Randomization basis: {protocol.randomization_basis}']
    return '\n'.join(lines)+'\n'


class ManualReturn(StrictModel):
    protocol_hash: str
    preexecution_receipt_hash: str
    prediction_hash: str
    sample_id: str
    batch_id: str
    calibration: str
    acquired_at: str
    operator: str
    raw_sha256: str
    action_id: str
    control_evidence_refs: tuple[str, ...]
    execution_status: str = 'completed'


def validate_manual_source(repository, frozen_directory, returned, card, split, record, source_root, *, purpose="development"):
    returned = ManualReturn.model_validate(returned.model_dump(mode='json'))
    protocol, receipt = load_frozen(frozen_directory)
    spec = validate_binding(repository, protocol)
    if (returned.protocol_hash, returned.preexecution_receipt_hash, returned.prediction_hash, returned.batch_id,
        returned.calibration, returned.operator) != (protocol.content_hash, receipt['receipt_hash'], spec.content_hash,
        dict(protocol.samples).get(returned.sample_id), protocol.calibration, protocol.operator):
        raise ValueError('manual return sample/batch/prediction/preexecution identity mismatch')
    acquired = _time(returned.acquired_at)
    if not _time(receipt['frozen_at']) < acquired <= datetime.now(timezone.utc):
        raise ValueError('acquisition must follow frozen preexecution identity and not be in future')
    if acquired >= _time(protocol.calibration_expires_at):
        raise ValueError('calibration expired at acquisition')
    if returned.sample_id != record.sample_id or returned.action_id != record.action_id or dict(card.sample_groups).get(returned.sample_id) != returned.batch_id or card.calibration != protocol.calibration:
        raise ValueError('source sample/batch/calibration identity mismatch')
    if returned.raw_sha256 != dict(card.files_sha256).get(record.source_file):
        raise ValueError('raw data hash mismatch')
    if purpose not in {'development', 'independent_audit'}:
        raise ValueError('unknown manual source purpose')
    if purpose == 'development' and returned.sample_id in protocol.holdout:
        raise ValueError('holdout is sealed; use the independent audit consumer')
    if purpose == 'independent_audit' and returned.sample_id not in protocol.holdout:
        raise ValueError('independent holdout audit requires a frozen holdout sample')
    required_split = 'train' if purpose == 'development' else 'audit'
    if split.sample_split(card, record.sample_id) != required_split:
        raise ValueError('manual import split differs from declared purpose')
    if returned.execution_status not in {'completed','partial','failed'}:
        raise ValueError('unsupported execution status')
    if record.channel != protocol.channel or record.units != spec.units:
        raise ValueError('returned channel/units differ from frozen prediction')
    adapter = MeasuredObservationAdapter(card, split, (record,), source_root, accessible_split=required_split)
    source = adapter.read(record.action_id, run_id=protocol.protocol_id)
    return protocol, receipt, spec, source


def import_manual(repository, frozen_directory, returned, card, split, record, source_root):
    returned = ManualReturn.model_validate(returned.model_dump(mode="json"))
    protocol, receipt, spec, source = validate_manual_source(repository, frozen_directory, returned, card, split, record, source_root)
    # File identity survives renamed samples/actions; cannot launder existing evidence.
    acquisition_id = 'manual-raw:'+returned.raw_sha256
    try:
        repository.acquisition_commit_sequence(acquisition_id)
    except ValueError:
        pass
    else:
        raise ValueError('duplicate acquisition already imported; no automatic remeasurement')
    for ref in repository.latest_snapshot().object_refs:
        existing = repository.load(ref)
        if isinstance(existing, Observation) and any(block.get('source_hash') == returned.raw_sha256 for block in existing.raw_blocks):
            raise ValueError('raw source already available; historical evidence cannot become prospective')
    raw_bytes = (Path(source_root)/record.source_file).read_bytes()
    if hashlib.sha256(raw_bytes).hexdigest() != returned.raw_sha256:
        raise ValueError('raw data changed during import')
    archive = repository.root/'raw_manual'/returned.raw_sha256
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        if archive.read_bytes() != raw_bytes:
            raise ValueError('archived raw data identity conflict')
    else:
        with archive.open('xb') as stream:
            stream.write(raw_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    obs = Observation(object_id='manual:'+canonical_hash(returned), source_run_id=protocol.protocol_id,
                      acquisition_id=acquisition_id, candidate_ref=spec.candidate_ref,
                      input_refs=(protocol.task, spec.ref()), raw_blocks=source.raw_blocks,
                      artifact_refs=source.artifact_refs+(str(archive.relative_to(repository.root)),),
                      binding={**source.binding, 'protocol_hash':protocol.content_hash,
                               'preexecution_receipt_hash':receipt['receipt_hash'], 'manual_return':returned.model_dump(mode='json'),
                               'program_ref':spec.program_ref.model_dump(mode='json') if spec.program_ref else None},
                      quality={**source.quality, 'execution_status':returned.execution_status,
                               'noise_validated':False, 'quality_audit':'pending_independent_review',
                               'physical_provenance':'operator_reported_unverified', 'new_physical_measurement':False,
                               'controls_audit':'pending' if returned.control_evidence_refs else 'missing',
                               'V4_accepted':False, 'V5_accepted':False})
    repository.commit([obs], task_ref=protocol.task, evidence_cutoff=returned.acquired_at,
                      expected_sequence=repository.latest_snapshot().committed_sequence)
    verdict = evaluate_prediction(repository, PredictionCommit(**protocol.prediction_commit.__dict__), obs.ref())
    return {'observation_ref':obs.ref().model_dump(mode='json'), 'prediction_verdict':verdict.state,
            'quality_audit':'pending_independent_review', 'hardware_executed':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    preview = sub.add_parser('preview'); preview.add_argument('--protocol', required=True); preview.add_argument('--readiness')
    export = sub.add_parser('export'); export.add_argument('--protocol', required=True); export.add_argument('--repository', required=True); export.add_argument('--output', required=True)
    imp = sub.add_parser('import'); imp.add_argument('--frozen', required=True); imp.add_argument('--repository', required=True); imp.add_argument('--bundle', required=True); imp.add_argument('--source-root', required=True)
    args = parser.parse_args(argv)
    if args.command == 'preview':
        print(human_protocol(ProspectiveProtocol.model_validate_json(Path(args.protocol).read_text())), end='')
        if args.readiness:
            from ..science.reality_readiness import RealityReadinessChecklist
            readiness = RealityReadinessChecklist.model_validate_json(Path(args.readiness).read_text())
            print(json.dumps({'readiness_hash':readiness.content_hash, 'pending_resources':readiness.pending(),
                              'hardware_authorized':False}))
        return
    repository = ScienceRepository(args.repository)
    try:
        if args.command == 'export':
            protocol = ProspectiveProtocol.model_validate_json(Path(args.protocol).read_text())
            freeze_protocol(repository, protocol, args.output)
            (Path(args.output)/'operator_protocol.txt').write_text(human_protocol(protocol))
            print(json.dumps({'protocol_hash':protocol.content_hash,'hardware_executed':False,'authorization':'unverified'}))
        else:
            bundle = json.loads(Path(args.bundle).read_text())
            card = DatasetCard(**{k:tuple(tuple(x) if isinstance(x,list) else x for x in v) if isinstance(v,list) else v for k,v in bundle['card'].items()})
            split = DatasetSplitManifest(bundle['split']['dataset_hash'], tuple(map(tuple,bundle['split']['group_splits'])))
            record = RecordedMeasurement(**bundle['record'])
            print(json.dumps(import_manual(repository, args.frozen, ManualReturn.model_validate(bundle['return']), card, split, record, args.source_root)))
    finally:
        repository.close()


if __name__ == '__main__':
    main()
