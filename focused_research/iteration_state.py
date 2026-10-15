"""Append-only, digest-bound one-iteration records.

Exclusive creation prevents accidental rewrites. SHA-256 detects accidental
changes, not malicious changes by someone able to rewrite the entire directory.
No network, model call, solver call, or automatic next-action execution occurs.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _predictions(rows, *, scoped=False):
    if not isinstance(rows,list) or not rows:
        raise ValueError('nonempty predictions required')
    ids=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('goal_id'),str) or not row['goal_id']:
            raise ValueError('prediction requires goal_id')
        if row.get('direction') not in ('increase','decrease','unchanged'):
            raise ValueError('prediction requires an explicit direction')
        if scoped:
            for key in ('route_id','baseline_instance_id','candidate_instance_id'):
                if not isinstance(row.get(key),str) or not row[key].strip():
                    raise ValueError('scoped prediction requires '+key)
            if row['baseline_instance_id']==row['candidate_instance_id']:
                raise ValueError('scoped prediction requires distinct comparison identities')
            ids.append(tuple(row[k] for k in ('route_id','baseline_instance_id','candidate_instance_id','goal_id')))
        else:
            ids.append(row['goal_id'])
    if len(ids)!=len(set(ids)):
        raise ValueError('duplicate prediction goal_id')


def _version(value):
    if not ((isinstance(value,str) and value.strip()) or (type(value) is int and value > 0)):
        raise ValueError('explicit proposal_version required')


def _write(directory, name, payload, previous=None):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    record={'schema_version':'focused_research.iteration_record.v1','kind':name,
        'recorded_at':datetime.now(timezone.utc).isoformat(), 'previous_sha256':previous,
        'payload':deepcopy(payload)}
    record['sha256']=_digest(record)
    path=directory/(name+'.json')
    # O_EXCL deliberately refuses existing files and symlinks; no repair overwrite.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(_canonical(record)+b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    return record


def read_record(directory, name):
    if name not in ('prediction','execution_started','observation','next_action'):
        raise ValueError('unknown record kind')
    path=Path(directory)/(name+'.json')
    if path.is_symlink():
        raise ValueError('record symlinks are not allowed')
    record=json.loads(path.read_text(encoding='utf-8'))
    expected=record.get('sha256')
    unsigned={k:v for k,v in record.items() if k!='sha256'}
    if _digest(unsigned)!=expected or record.get('kind')!=name:
        raise ValueError('record integrity mismatch')
    return record


def _chain(directory, through):
    names=['prediction','execution_started','observation','next_action']
    records=[]
    for name in names[:names.index(through)+1]:
        record=read_record(directory,name)
        if record['previous_sha256'] != (records[-1]['sha256'] if records else None):
            raise ValueError('record chain mismatch')
        if records and record['recorded_at'] < records[-1]['recorded_at']:
            raise ValueError('record order mismatch')
        records.append(record)
    return records


def create_prediction_record(iteration_dir, proposal):
    for key in ('proposal_id','route_id','baseline_instance_id','candidate_instance_id'):
        if not isinstance(proposal.get(key),str) or not proposal[key].strip():
            raise ValueError('proposal requires '+key)
    _version(proposal.get('proposal_version'))
    _predictions(proposal.get('predictions'))
    if proposal['baseline_instance_id']==proposal['candidate_instance_id']:
        raise ValueError('distinct baseline and candidate required')
    for row in proposal['predictions']:
        for key in ('baseline_instance_id','candidate_instance_id'):
            if key in row and row[key]!=proposal[key]:
                raise ValueError('prediction comparison identity mismatch')
    return _write(iteration_dir,'prediction',proposal)


def begin_execution(iteration_dir):
    prediction=_chain(iteration_dir,'prediction')[-1]
    return _write(iteration_dir,'execution_started',{'prediction_sha256':prediction['sha256'],
        'status':'started_not_yet_observed'},prediction['sha256'])


def record_observation(iteration_dir, observations):
    records=_chain(iteration_dir,'execution_started')
    proposal=records[0]['payload']
    for key in ('baseline_instance_id','candidate_instance_id'):
        if observations.get(key)!=proposal[key]:
            raise ValueError('observation comparison identity mismatch')
    if observations.get('route_id') is not None and observations['route_id'] != proposal['route_id']:
        raise ValueError('observation route identity mismatch')
    expected={p['goal_id'] for p in proposal['predictions']}
    observed={g['goal_id'] for g in observations.get('goals',[])}
    if not expected <= observed:
        raise ValueError('predicted goals missing from observation')
    try:
        from .physical_observations import compare_predictions
    except ImportError:
        from physical_observations import compare_predictions
    payload={'observations':deepcopy(observations),
        'prediction_comparison':compare_predictions(proposal['predictions'],observations),
        'prediction_sha256':records[0]['sha256']}
    return _write(iteration_dir,'observation',payload,records[-1]['sha256'])


def record_next_action(iteration_dir, action):
    records=_chain(iteration_dir,'observation')
    proposal=records[0]['payload']
    _version(action.get('proposal_version'))
    if action['proposal_version']==proposal['proposal_version']:
        raise ValueError('next action requires a new proposal version')
    if action.get('status')!='proposed_not_executed':
        raise ValueError('next action must be proposed_not_executed')
    if not isinstance(action.get('proposed_edit'),dict) or not action['proposed_edit']:
        raise ValueError('explicit proposed_edit required')
    scoped = 'route_ids' in action or any('route_id' in row for row in action.get('predictions',[]) if isinstance(row,dict))
    _predictions(action.get('predictions'), scoped=scoped)
    if 'route_ids' in action:
        route_ids=action['route_ids']
        if not isinstance(route_ids,list) or not route_ids or any(not isinstance(r,str) or not r for r in route_ids) or len(set(route_ids))!=len(route_ids):
            raise ValueError('explicit unique route_ids required')
        if any(row['route_id'] not in route_ids for row in action['predictions']):
            raise ValueError('prediction route not in route_ids')
    rows=records[-1]['payload']['observations']['goals']
    allowed={r['observation_id'] for r in rows if r.get('status')=='observed' and 'observation_id' in r}
    refs=action.get('physical_basis_refs')
    if not isinstance(refs,list) or not refs or any(not isinstance(ref,str) or ref not in allowed for ref in refs):
        raise ValueError('physical_basis_refs must reference observed goal observations')
    return _write(iteration_dir,'next_action',action,records[-1]['sha256'])


def build_revision_prompt(proposal, observations, original_task=None, constraints=None):
    """Pure prompt builder for an injected caller; does not call any model."""
    instruction = (
        'Propose one next intervention from the frozen prediction and program-generated observations. '
        'Keep all original objectives and constraints. Separate sampled numerical performance from uncertain physical explanation; '
        'do not claim a unique mechanism, continuous-band verification, independent energy validation from A=1-R-T, '
        'or an unperformed gradient/probe. Reference actual observation_id values in physical_basis_refs. '
        'Return one JSON object with a new proposal_version, status="proposed_not_executed", '
        'nonempty proposed_edit object describing exact parameter changes, physical_basis_refs list, '
        'predictions list using goal_id and direction (increase/decrease/unchanged), and explanation with uncertainties. '
        'If extra whole-thickness or terminal-group +/- probes are needed, propose an explicit costed plan; do not execute it. '
        'Never rewrite the prior prediction or say the proposed action has already run.\n')
    return instruction + json.dumps({'frozen_proposal':proposal,'observations':observations,
        'original_task':original_task,'constraints':constraints},ensure_ascii=False,sort_keys=True,allow_nan=False)
