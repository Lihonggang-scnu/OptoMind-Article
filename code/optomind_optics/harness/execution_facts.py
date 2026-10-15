from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path


def _mapping(value):
    return value if isinstance(value, Mapping) else {}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def bounded_projection(value, *, depth=4, items=16, text_limit=500):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value if len(value) <= text_limit else {'omitted': True, 'sha256': _digest(value), 'character_count': len(value)}
    if depth <= 0:
        return {'omitted': True, 'sha256': _digest(value)}
    if isinstance(value, Mapping):
        result = {str(k): bounded_projection(v, depth=depth - 1, items=items, text_limit=text_limit)
                  for k, v in list(value.items())[:items]}
        if len(value) > items:
            result['_omitted_keys'] = len(value) - items
            result['_source_sha256'] = _digest(value)
        return result
    if isinstance(value, (list, tuple)):
        result = [bounded_projection(v, depth=depth - 1, items=items, text_limit=text_limit) for v in value[:items]]
        if len(value) > items:
            result.append({'omitted_count': len(value) - items, 'source_sha256': _digest(value)})
        return result
    return bounded_projection(str(value), depth=depth, items=items, text_limit=text_limit)


def _contained(root, relative):
    raw = str(relative).replace('\\', '/')
    if not raw or raw.startswith('/') or ':' in raw or '..' in Path(raw).parts:
        return None
    path = (root / raw).resolve()
    return path if path.is_relative_to(root) else None


def _read(path):
    try:
        data = path.read_bytes()
        payload = json.loads(data)
        return payload, hashlib.sha256(data).hexdigest()
    except (OSError, ValueError, TypeError):
        return None, None


def discover_execution_artifacts(execution_dir):
    root = Path(execution_dir).resolve()
    manifest_path = _contained(root, 'ARTIFACT_MANIFEST.json')
    manifest, manifest_hash = _read(manifest_path) if manifest_path else (None, None)
    expected = {}
    issues = []
    for entry in _mapping(manifest).get('artifacts', []):
        entry = _mapping(entry)
        rel = str(entry.get('relative_path') or entry.get('artifact_id') or '').replace('\\', '/')
        if _contained(root, rel) is None:
            issues.append({'artifact': rel, 'status': 'unsafe_path'})
            continue
        digest = entry.get('sha256')
        if rel in expected and expected[rel] != digest:
            issues.append({'artifact': rel, 'status': 'conflicting_manifest_hashes'})
            expected[rel] = 'conflict'
        else:
            expected[rel] = digest
    paths = set(expected)
    index_path = _contained(root, 'ARTIFACT_PATH_INDEX.json')
    index, index_hash = _read(index_path) if index_path else (None, None)
    if 'ARTIFACT_PATH_INDEX.json' not in expected or expected['ARTIFACT_PATH_INDEX.json'] == index_hash:
        for entry in _mapping(index).get('experiments', []):
            directory = _contained(root, _mapping(entry).get('physical_directory', ''))
            if directory is not None and directory.is_dir():
                paths.update(p.relative_to(root).as_posix() for p in directory.rglob('*.json'))
    for base in (root, root / 'experiments', root / 'x'):
        if base == root:
            paths.update(p.name for p in base.glob('*.json'))
        elif base.exists():
            paths.update(p.relative_to(root).as_posix() for p in base.rglob('*.json'))
    artifacts = []
    wanted = {'ARTIFACT_PATH_INDEX.json', 'IDENTITY.json', 'BUDGET.json', 'COST.json',
              'SEARCH_ALLOCATION.json', 'PHYSICS_ACCEPTANCE_CERTIFICATE.json', 'OBJECTIVE_REPORT.json',
              'FINAL_RESULT.json', 'ITERATION_OBSERVATION.json'}
    for rel in sorted(paths):
        if Path(rel).name not in wanted and not Path(rel).name.startswith('OPTIMIZER_'):
            continue
        path = _contained(root, rel)
        if path is None:
            issues.append({'artifact': rel, 'status': 'unsafe_path'})
            continue
        payload, digest = _read(path)
        if payload is None:
            issues.append({'artifact': rel, 'status': 'missing_or_invalid'})
            continue
        if rel in expected and (not expected[rel] or expected[rel] != digest):
            issues.append({'artifact': rel, 'status': 'hash_mismatch'})
            continue
        artifacts.append({'artifact': rel, 'artifact_sha256': digest,
                          'integrity': 'manifest_hash_verified' if rel in expected else 'unmanifested',
                          'payload': payload})
    return {'artifacts': artifacts, 'issues': issues,
            'manifest_sha256': manifest_hash}


def _ref(artifact):
    return {k: artifact[k] for k in ('artifact', 'artifact_sha256', 'integrity')}


def selected_candidate_certificate(execution_dir, candidate_id, *, experiment_id=None,
                                   artifact_ids=(), certificate_id=None, discovered=None):
    discovered = discovered or discover_execution_artifacts(execution_dir)
    records = discovered['artifacts']
    identities = {}
    for record in records:
        if Path(record['artifact']).name == 'IDENTITY.json':
            identity = _mapping(record['payload'])
            directory = str(Path(record['artifact']).parent)
            declared = str(identity.get('physical_directory') or directory).replace('\\', '/')
            identities[directory] = {**identity, '_directory_matches': declared == directory}
    explicit = {str(p).replace('\\', '/') for p in artifact_ids}
    matches = []
    conflicts = []
    for record in records:
        if Path(record['artifact']).name != 'PHYSICS_ACCEPTANCE_CERTIFICATE.json':
            continue
        payload = _mapping(record['payload'])
        identity = identities.get(str(Path(record['artifact']).parent), {})
        id_match = identity.get('_directory_matches') is True and identity.get('candidate_id') == candidate_id and (
            experiment_id is None or identity.get('experiment_id') == experiment_id)
        explicit_match = record['artifact'] in explicit
        payload_match = payload.get('candidate_id') == candidate_id and (
            experiment_id is None or payload.get('experiment_id') in (None, experiment_id))
        if not (id_match or explicit_match or payload_match):
            continue
        identity_path = str(Path(record['artifact']).parent / 'IDENTITY.json')
        if any(issue.get('artifact') == identity_path for issue in discovered.get('issues', [])):
            conflicts.append({**_ref(record), 'reason': 'invalid_candidate_identity_artifact'})
            continue
        if (identity and not id_match) or (payload.get('candidate_id') not in (None, candidate_id)) or (
            experiment_id is not None and payload.get('experiment_id') not in (None, experiment_id)) or (
            certificate_id is not None and payload.get('certificate_id') != certificate_id):
            conflicts.append(_ref(record))
            continue
        matches.append(record)
    if not candidate_id:
        matches = []
    if len(matches) != 1 or conflicts:
        return {'status': 'identity_conflict' if conflicts or len(matches) > 1 else 'missing',
                'candidate_id': candidate_id, 'payload': None, 'raw_ref': None,
                'conflicts': conflicts, 'match_count': len(matches)}
    return {'status': 'bound', 'candidate_id': candidate_id,
            'payload': matches[0]['payload'], 'raw_ref': _ref(matches[0])}


def _positive(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and value > 0


def _whole_candidate_value(value, field, *, max_items=64, max_chars=8192):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(encoded) > max_chars or (isinstance(value, (list, tuple)) and len(value) > max_items):
        return {'omitted': True, 'raw_ref': {'source': 'candidate_summary', 'field': field,
                'sha256': _digest(value)}, 'item_count': len(value) if isinstance(value, (list, tuple)) else None}
    return list(value) if isinstance(value, (list, tuple)) else value


def compact_candidate(candidate):
    candidate = _mapping(candidate)
    result = {key: candidate.get(key) for key in ('candidate_id', 'experiment_id', 'optimizer_id',
              'frozen_score', 'target_score', 'score_basis_fingerprint', 'certificate_id',
              'iteration_id', 'parent_iteration_id', 'robustness_score', 'selected_role')}
    result['role'] = ('baseline' if str(candidate.get('candidate_id', '')).endswith('__baseline')
                      else 'optimized' if candidate.get('optimizer_id') else 'unknown')
    for field in ('thicknesses_nm', 'layer_materials', 'layer_labels', 'stack_identity', 'stack_sha256',
                  'incident_medium', 'exit_medium', 'selected_roles'):
        if field in candidate:
            result[field] = _whole_candidate_value(candidate[field], field)
    result['band_metrics'] = bounded_projection(_mapping(candidate.get('objective_report')).get('target_attainment', {}), depth=5, items=12)
    result['frozen_metric_values'] = bounded_projection(_mapping(candidate.get('frozen_score_outcome')).get('values', {}))
    result['raw_ref'] = {'source': 'candidate_summary', 'sha256': _digest(candidate),
                         'artifact_ids': list(candidate.get('artifact_ids') or [])[:12]}
    return result


def build_execution_facts(iteration_dir, run_payload, *, observation=None, gradient_summary=None):
    iteration_dir = Path(iteration_dir)
    run_payload = _mapping(run_payload)
    observation = _mapping(observation)
    discovered = discover_execution_artifacts(iteration_dir / 'tmm_run')
    records = discovered['artifacts']
    optimizers = []
    allocations = []
    budget = {}
    raw_refs = []
    for record in records:
        name = Path(record['artifact']).name
        payload = _mapping(record['payload'])
        if name == 'BUDGET.json' and record['artifact'] == 'BUDGET.json':
            budget = payload
            raw_refs.append(_ref(record))
        elif name.startswith('OPTIMIZER_'):
            audit = _mapping(payload.get('audit'))
            raw_audit = _mapping(audit.get('raw_optimizer_audit')) or audit
            optimizers.append({**{k: payload.get(k) for k in ('optimizer_id', 'status', 'evaluation_count', 'stop_reason')},
                               'audit': bounded_projection({k: raw_audit.get(k) for k in ('maximum_forward_evaluations', 'best_step', 'budget_exhausted', 'local_finish', 'initialization', 'seed')}),
                               'raw_ref': _ref(record)})
        elif name == 'SEARCH_ALLOCATION.json':
            allocations.append({'allocation': bounded_projection(payload, depth=3, items=10), 'raw_ref': _ref(record)})
    commits = []
    for event in budget.get('events', []):
        event = _mapping(event)
        actual = _mapping(event.get('actual_usage'))
        if event.get('event_type', event.get('type')) == 'commit' and _positive(actual.get('optimizer_runs')):
            commits.append({'action_id': event.get('action_id'), 'optimizer_runs': actual.get('optimizer_runs'),
                            'forward_evaluations': actual.get('forward_evaluations')})
    usage = _mapping(run_payload.get('budget_usage') or run_payload.get('budget') or budget.get('usage'))
    usage = _mapping(usage.get('usage')) or usage
    candidates = observation.get('candidate_summaries') or run_payload.get('candidate_summaries') or []
    selected = list(observation.get('best_frozen_candidate_ids') or observation.get('selected_candidate_ids') or [])
    summaries = []
    for candidate in candidates:
        candidate = _mapping(candidate)
        summary = compact_candidate(candidate)
        summary['iteration_id'] = candidate.get('iteration_id') or observation.get('iteration_id') or iteration_dir.name
        selected_roles = [role for experiment in observation.get('experiment_summaries', [])
                          if _mapping(experiment).get('experiment_id') == candidate.get('experiment_id')
                          for role, selected_id in _mapping(_mapping(experiment).get('selected_roles')).items()
                          if selected_id == candidate.get('candidate_id')]
        if selected_roles:
            summary['selected_roles'] = _whole_candidate_value(selected_roles, 'selected_roles')
            summary['selected_role'] = candidate.get('selected_role') or selected_roles[0]
        certificate = selected_candidate_certificate(iteration_dir / 'tmm_run', candidate.get('candidate_id'),
            experiment_id=candidate.get('experiment_id'), artifact_ids=candidate.get('artifact_ids') or (),
            certificate_id=candidate.get('certificate_id'), discovered=discovered)
        summary['verification'] = {'binding_status': certificate['status'],
            'accepted': _mapping(certificate['payload']).get('accepted'),
            'status': _mapping(certificate['payload']).get('status'), 'raw_ref': certificate['raw_ref']}
        if certificate['raw_ref']:
            objective_path = str(Path(certificate['raw_ref']['artifact']).parent / 'OBJECTIVE_REPORT.json')
            objective = next((r for r in records if r['artifact'] == objective_path), None)
            if objective is not None:
                summary['band_metrics'] = bounded_projection(_mapping(objective['payload']).get('target_attainment', {}), depth=5, items=12)
                summary['objective_raw_ref'] = _ref(objective)
            elif any(issue['artifact'] == objective_path for issue in discovered['issues']):
                summary['band_metrics'] = {}
                summary['objective_status'] = 'artifact_invalid'
        summary['selected'] = candidate.get('candidate_id') in selected
        summaries.append(summary)
    summaries.sort(key=lambda c: (not c['selected'], c['role'] != 'baseline', str(c['candidate_id'])))
    status = run_payload.get('status') or observation.get('run_status') or 'unknown'
    performed = bool(commits or _positive(usage.get('optimizer_runs')) or any(_positive(o['evaluation_count']) for o in optimizers))
    physics = bool(performed or _positive(usage.get('forward_evaluations')) or commits or any(c['verification']['binding_status'] == 'bound' for c in summaries))
    return {'schema_version': 'execution-facts.v1', 'iteration_id': observation.get('iteration_id') or iteration_dir.name,
        'run_status': status, 'run_outcome': bounded_projection(run_payload.get('outcome')),
        'physics_executed': physics if physics else False if status in ('not_run', 'not_executed') else None,
        'optimizer_performed': True if performed else None, 'optimizer_committed_runs': sum(c['optimizer_runs'] for c in commits) if commits else None,
        'optimizer_commits': commits[:12], 'optimizers': optimizers[:12], 'search_allocations': allocations[:8],
        'budget_usage': bounded_projection(usage, depth=2), 'gradient_compute_cost': bounded_projection(_mapping(gradient_summary).get('compute_cost')),
        'frozen_objective_hash': observation.get('score_basis_fingerprint'), 'decision_score': observation.get('best_frozen_score', observation.get('decision_score')),
        'candidates': summaries[:12], 'omitted_candidate_count': max(0, len(summaries) - 12),
        'raw_refs': raw_refs, 'artifact_issues': discovered['issues'][:16], 'manifest_sha256': discovered['manifest_sha256'],
        'source_payload_sha256': _digest(run_payload)}


def project_execution_history(history, *, current_iteration_id, current_status, attempted_round_count):
    projected = []
    for record in history:
        if isinstance(record, str):
            try:
                record = json.loads(record)
            except ValueError:
                continue
        record = _mapping(record)
        facts = _mapping(record.get('execution_facts')) or _mapping(_mapping(record.get('evidence')).get('execution_facts'))
        if not facts and record.get('schema_version') == 'execution-facts.v1':
            facts = record
        if not facts:
            evidence = _mapping(record.get('evidence'))
            usage = _mapping(evidence.get('budget_usage'))
            usage = _mapping(usage.get('usage')) or usage
            commits = [e for e in evidence.get('allocation_events', []) if _mapping(e).get('event_type') == 'commit' and str(_mapping(e).get('action_id', '')).startswith('optimizer:') and _positive(_mapping(e).get('actual'))]
            optimizer = bool(commits or _positive(usage.get('optimizer_runs')) or any(_positive(_mapping(o).get('evaluation_count')) for o in evidence.get('optimizers', [])))
            calls = _mapping(evidence.get('forward_evaluation_accounting')).get('run_forward_evaluations')
            facts = {'iteration_id': record.get('iteration_id'), 'run_status': evidence.get('run_status'),
                     'physics_executed': True if optimizer or _positive(calls) or _positive(usage.get('forward_evaluations')) else None,
                     'optimizer_performed': True if optimizer else None, 'decision_score': record.get('decision_score'),
                     'optimizers': bounded_projection(evidence.get('optimizers', [])),
                     'optimizer_commits': bounded_projection(commits), 'raw_record_sha256': _digest(record)}
        projected.append(dict(facts))
    unique = {}
    for row in projected:
        if row.get('iteration_id'):
            unique[row['iteration_id']] = row
    current = unique.pop(current_iteration_id, None)
    prior = list(unique.values())
    executed = sum(row.get('physics_executed') is True for row in prior) + int(bool(current and current.get('physics_executed') is True))
    if current is None:
        current = {'iteration_id': current_iteration_id, 'run_status': current_status,
                   'physics_executed': False if current_status in ('not_run', 'not_executed') else None}
    return {'attempted_round_count': attempted_round_count, 'physics_executed_round_count': executed,
            'current_iteration_id': current_iteration_id, 'current_status': current_status,
            'current_execution': current, 'prior_executions': prior[-8:],
            'omitted_prior_execution_count': max(0, len(prior) - 8), 'raw_history_sha256': _digest(history)}
