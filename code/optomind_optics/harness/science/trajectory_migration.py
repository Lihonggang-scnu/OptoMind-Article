"""Read-only historical trajectory import into committed canonical projections.

Recorded old contracts retain their bytes and semantics. Missing raw evidence,
posterior or pre-execution predictions are reported, never reconstructed.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
from .contracts import (ResearchTask, ModelCandidate, ExperimentProgram,
                        InferenceState, DecisionContext, canonical_hash)
from .migration import MigrationReport, migrate_observation_v1
from .repository import ScienceRepository


def plan_trajectory_migration(source):
    source = Path(source).resolve()
    paths = sorted(source.rglob('*.json'))
    recorded = {}
    manifest = {}
    for path in paths:
        if path.is_symlink():
            raise ValueError('historical source symlinks are not accepted')
        data = path.read_bytes()
        key = path.relative_to(source).as_posix()
        recorded[key] = json.loads(data)
        manifest[key] = {'sha256': hashlib.sha256(data).hexdigest(),
                         'bytes_base64': base64.b64encode(data).decode('ascii')}
    request = recorded.get('REQUEST.json')
    if not isinstance(request, dict) or not isinstance(request.get('question'), str):
        raise ValueError('recorded REQUEST.json question required')
    run = str(request.get('run_id') or source.name)
    prefix = 'historical:' + canonical_hash({k:v['sha256'] for k,v in manifest.items()})[:20]
    task = ResearchTask(object_id=prefix+':task', source_run_id=run,
                        question=request['question'], constraints={'not_recorded':['canonical_objective']},
                        unconsumed_metadata={'legacy_request': request})
    objects = [task]
    reports = {}
    def add(obj, payload, mapped, missing=()):
        objects.append(obj)
        reports[obj.object_id] = MigrationReport(source_hash=canonical_hash(payload),
            mapped_fields=tuple(mapped), not_recorded_fields=tuple(missing)).model_dump(mode='json')
    observations = []
    for key, payload in recorded.items():
        if not key.endswith('/ITERATION_OBSERVATION.json'):
            continue
        iteration = key.rsplit('/',1)[0]
        obs, report = migrate_observation_v1(payload, source_run_id=run)
        # Namespace ids without rewriting original historical identity.
        obs = type(obs).model_validate({**obs.model_dump(mode='json'), 'object_id':prefix+':'+iteration+':observation','content_hash':''})
        objects.append(obs); observations.append(obs)
        reports[obs.object_id] = report.model_dump(mode='json')
        candidates = []
        for i, candidate in enumerate(payload.get('candidate_summaries') or []):
            geometry = {name:candidate[name] for name in ('candidate_id','layer_materials','layer_labels','incident_medium','exit_medium') if name in candidate}
            geometry['schema_version'] = 'legacy-candidate.v1'
            obj = ModelCandidate(object_id=prefix+':'+iteration+':candidate:'+str(i), source_run_id=run,
                model_ir=geometry, parameter_values={'thicknesses_nm':candidate['thicknesses_nm']} if 'thicknesses_nm' in candidate else {},
                domain={'not_recorded':['executable_optical_ir','material_dispersion']},
                unconsumed_metadata={'legacy_candidate':candidate},input_refs=(obs.ref(),))
            add(obj,candidate,geometry.keys(),('executable_optical_ir','material_dispersion')); candidates.append(obj)
        compiled = recorded.get(iteration+'/COMPILED_TASK.json')
        if compiled:
            for i, experiment in enumerate(compiled.get('experiments') or []):
                program = ExperimentProgram(object_id=prefix+':'+iteration+':program:'+str(i),source_run_id=run,
                    objective_ref=task.ref(), protocol={'schema_version':'legacy-tmm-experiment.v1',
                    'experiment_id':str(experiment.get('experiment_id',i)), 'tmm_task':compiled,
                    'source_task_hash':canonical_hash(compiled)}, unconsumed_metadata={'legacy_experiment':experiment})
                add(program, compiled,('protocol.tmm_task','protocol.source_task_hash'),('program_ir','preexecution_prediction'))
        feedback = recorded.get(iteration+'/FEEDBACK_DECISION.json',{})
        state = InferenceState(object_id=prefix+':'+iteration+':state',source_run_id=run,
            candidate_refs=tuple(c.ref() for c in candidates), observation_refs=(obs.ref(),),
            representation='not_recorded',posterior={'not_recorded':True},
            unconsumed_metadata={'legacy_feedback':feedback,'not_recorded_fields':['posterior','noise_model','historical_belief']})
        add(state,feedback,('unconsumed_metadata.legacy_feedback',),('posterior','noise_model','historical_belief'))
    final = recorded.get('FINAL_ANSWER.json', recorded.get('RESEARCH_RESULT.json',{}))
    context = DecisionContext(object_id=prefix+':result',source_run_id=run,
        observation_refs=tuple(o.ref() for o in observations),
        summary={'historical_result':final,'not_recorded_fields':['canonical_selected_winner','preexecution_predictions']},
        input_refs=tuple(o.ref() for o in objects),
        unconsumed_metadata={'source_manifest':manifest,'migration_reports':dict(reports),
                             'projection_semantics':'recorded historical contracts; no inferred posterior or winner'})
    add(context,final,('summary.historical_result',),('canonical_selected_winner','preexecution_predictions'))
    return objects, task, context, reports


def migrate_trajectory(source, repository):
    objects, task, result, reports = plan_trajectory_migration(source)
    latest = repository.latest_snapshot()
    if latest and result.ref() in latest.object_refs:
        repository.load(result.ref())  # Verify recovered commit rather than trusting its receipt.
        return latest, reports
    snapshot = repository.commit(objects, task_ref=task.ref(), evidence_cutoff='historical-recorded-only',
                                 expected_sequence=latest.committed_sequence if latest else 0)
    return snapshot, reports


def rebuild_historical_files(repository, result_ref):
    result = repository.load(result_ref)
    if not isinstance(result, DecisionContext):raise ValueError('historical result context required')
    files = {}
    for name, entry in result.unconsumed_metadata['source_manifest'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:raise ValueError('unsafe recorded source name')
        data = base64.b64decode(entry['bytes_base64'],validate=True)
        if hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('historical source hash mismatch')
        files[name]=data
    return files


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True);parser.add_argument('--output')
    parser.add_argument('--commit',action='store_true')
    args=parser.parse_args(argv)
    objects,task,result,reports=plan_trajectory_migration(args.source)
    receipt={'mode':'preview','objects':len(objects),'result_ref':result.ref().model_dump(mode='json'),'reports':reports}
    if args.commit:
        if not args.output:parser.error('--commit requires --output')
        source=Path(args.source).resolve();output=Path(args.output).resolve()
        if output==source or source in output.parents:parser.error('output must be outside read-only historical source')
        repo=ScienceRepository(output)
        try: snapshot,_=migrate_trajectory(source,repo)
        finally:repo.close()
        receipt.update(mode='committed',snapshot=snapshot.model_dump(mode='json'))
    print(json.dumps(receipt,sort_keys=True))

if __name__=='__main__':main()
