import hashlib
import importlib.util
import json
from pathlib import Path

import unittest
import tempfile
import inspect

SOURCE = Path(__file__).resolve().parents[1] / 'code/optomind_optics/harness/execution_facts.py'
spec = importlib.util.spec_from_file_location('execution_facts', SOURCE)
facts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(facts)


def write(root, path, payload):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(payload))
    return {'relative_path': path, 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}


def test_compact_and_legacy_optimizer_discovery(prefix, tmp_path):
    root = tmp_path / 'iteration_02' / 'tmm_run'
    write(root, prefix + '/OPTIMIZER_gradient_thickness.json', {'optimizer_id': 'gradient_thickness', 'evaluation_count': 205, 'status': 'completed', 'stop_reason': 'budget'})
    write(root, prefix + '/SEARCH_ALLOCATION.json', {'primary_used': 205})
    write(root, 'BUDGET.json', {'events': [{'event_type': 'commit', 'action_id': 'optimizer:experiment-one:primary', 'actual_usage': {'optimizer_runs': 1, 'forward_evaluations': 205}}]})
    result = facts.build_execution_facts(root.parent, {'status': 'completed'})
    assert result['optimizer_performed'] is True
    assert result['optimizer_committed_runs'] == 1
    assert result['optimizers'][0]['evaluation_count'] == 205
    assert result['optimizers'][0]['stop_reason'] == 'budget'
    assert result['search_allocations'][0]['allocation']['primary_used'] == 205


def test_identity_binding_rejects_wrong_first_certificate(tmp_path):
    write(tmp_path, 'x/e/b/PHYSICS_ACCEPTANCE_CERTIFICATE.json', {'certificate_id': 'baseline', 'accepted': True})
    write(tmp_path, 'x/e/c/c_right/IDENTITY.json', {'candidate_id': 'right', 'experiment_id': 'e', 'physical_directory': 'x/e/c/c_right'})
    write(tmp_path, 'x/e/c/c_right/PHYSICS_ACCEPTANCE_CERTIFICATE.json', {'certificate_id': 'selected', 'accepted': True})
    result = facts.selected_candidate_certificate(tmp_path, 'right', experiment_id='e', certificate_id='selected')
    assert result['status'] == 'bound'
    assert result['payload']['certificate_id'] == 'selected'
    rejected = facts.selected_candidate_certificate(tmp_path, 'wrong', artifact_ids=['x/e/c/c_right/PHYSICS_ACCEPTANCE_CERTIFICATE.json'])
    assert rejected['status'] == 'identity_conflict'
    assert rejected['payload'] is None
    assert facts.selected_candidate_certificate(tmp_path, 'missing')['status'] == 'missing'


def test_hash_mismatch_and_path_escape_rejected(tmp_path):
    row = write(tmp_path, 'x/e/OPTIMIZER_gradient.json', {'evaluation_count': 10})
    write(tmp_path, 'ARTIFACT_MANIFEST.json', {'artifacts': [row, {'relative_path': '../outside.json', 'sha256': 'x'}]})
    write(tmp_path, row['relative_path'], {'evaluation_count': 100})
    result = facts.discover_execution_artifacts(tmp_path)
    assert not any(r['artifact'] == row['relative_path'] for r in result['artifacts'])
    assert {i['status'] for i in result['issues']} == {'hash_mismatch', 'unsafe_path'}


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + '_outside.json')
    outside.write_text('{"evaluation_count": 100}')
    (tmp_path / 'OPTIMIZER_escape.json').symlink_to(outside)
    result = facts.discover_execution_artifacts(tmp_path)
    assert not result['artifacts']
    assert result['issues'][0]['status'] == 'unsafe_path'


def test_absent_run_is_unknown_and_compile_failure_not_executed(tmp_path):
    result = facts.build_execution_facts(tmp_path, {'status': 'not_run'})
    assert result['optimizer_performed'] is None
    assert result['physics_executed'] is False
    assert result['candidates'] == []


def test_prior_execution_not_copied_into_current_compile_failure():
    history = [{'iteration_id': 'iteration_02', 'decision_score': 1.94599113067998,
                'evidence': {'run_status': 'completed', 'allocation_events': [
                    {'event_type': 'commit', 'action_id': 'optimizer:rgb:primary', 'actual': 205}]}}]
    result = facts.project_execution_history(history, current_iteration_id='iteration_03', current_status='not_run', attempted_round_count=3)
    assert result['physics_executed_round_count'] == 1
    assert result['attempted_round_count'] == 3
    assert result['current_execution']['physics_executed'] is False
    assert result['prior_executions'][0]['optimizer_performed'] is True
    assert result['prior_executions'][0]['decision_score'] == 1.94599113067998


def test_bounded_candidate_metrics_keep_source_hash():
    candidate = {'candidate_id': 'c', 'optimizer_id': 'g', 'objective_report': {'target_attainment': {str(i): {'observed': 0.5, 'spectra': list(range(10000))} for i in range(1000)}}}
    result = facts.compact_candidate(candidate)
    assert len(json.dumps(result)) < 8000
    assert result['band_metrics']['_omitted_keys'] == 988
    assert len(result['raw_ref']['sha256']) == 64


def test_actual_saved_rgb_selected_candidate(tmp_path):
    saved = Path(__file__).parent / 'fixtures/scientific_core_rgb'
    observation = json.loads((saved / 'OBSERVATION.json').read_text())
    identity = json.loads((saved / 'RGB_SELECTED__IDENTITY.json').read_text())
    root = tmp_path / 'iteration_02/tmm_run'
    entries = []
    for name in ('IDENTITY', 'OBJECTIVE_REPORT', 'PHYSICS_ACCEPTANCE_CERTIFICATE'):
        entries.append(write(root, identity['physical_directory'] + '/' + name + '.json', json.loads((saved / ('RGB_SELECTED__' + name + '.json')).read_text())))
    entries.append(write(root, 'BUDGET.json', json.loads((saved / 'RGB__NATIVE_BUDGET.json').read_text())))
    write(root, 'ARTIFACT_MANIFEST.json', {'artifacts': entries})
    result = facts.build_execution_facts(root.parent, {'status': 'completed'}, observation=observation)
    selected = next(c for c in result['candidates'] if c['candidate_id'] == identity['candidate_id'])
    original = next(c for c in observation['candidate_summaries'] if c['candidate_id'] == identity['candidate_id'])
    assert len(selected['thicknesses_nm']) == 20
    assert selected['thicknesses_nm'] == original['thicknesses_nm']
    assert selected['layer_materials'] == original['layer_materials']
    assert selected['iteration_id'] == 'iteration_02'
    assert selected['selected_role'] == 'best_target_score'
    assert selected['robustness_score'] == original['robustness_score']
    assert selected['role'] == 'optimized'
    assert selected['selected'] is True
    assert selected['frozen_score'] == 1.94599113067998
    assert selected['verification']['binding_status'] == 'bound'
    assert selected['verification']['accepted'] is True
    assert selected['verification']['raw_ref']['integrity'] == 'manifest_hash_verified'
    assert selected['band_metrics']['fixedscore.mean_reflectance_450_650nm_45deg']['observed'] == 0.9655686950438241
    assert result['optimizer_commits'][0]['forward_evaluations'] == 205
    assert result['optimizer_performed'] is True
    assert len(json.dumps(result)) < 30000


def test_path_index_discovers_nonstandard_directory(tmp_path):
    row = write(tmp_path, 'runs/safe/OPTIMIZER_gradient.json', {'evaluation_count': 17})
    write(tmp_path, 'ARTIFACT_PATH_INDEX.json', {'experiments': [{'experiment_id': 'logical', 'physical_directory': 'runs/safe'}]})
    result = facts.discover_execution_artifacts(tmp_path)
    assert any(r['artifact'] == row['relative_path'] for r in result['artifacts'])


def test_duplicate_matching_certificates_do_not_choose_first(tmp_path):
    for directory in ('x/e/c/a', 'experiments/e/c/b'):
        write(tmp_path, directory + '/IDENTITY.json', {'candidate_id': 'candidate', 'experiment_id': 'e', 'physical_directory': directory})
        write(tmp_path, directory + '/PHYSICS_ACCEPTANCE_CERTIFICATE.json', {'accepted': True, 'certificate_id': 'same'})
    result = facts.selected_candidate_certificate(tmp_path, 'candidate', experiment_id='e')
    assert result['status'] == 'identity_conflict'
    assert result['payload'] is None


def test_invalid_objective_cannot_supply_band_metrics(tmp_path):
    root = tmp_path / 'tmm_run'
    write(root, 'x/e/c/a/IDENTITY.json', {'candidate_id': 'candidate', 'experiment_id': 'e', 'physical_directory': 'x/e/c/a'})
    write(root, 'x/e/c/a/PHYSICS_ACCEPTANCE_CERTIFICATE.json', {'accepted': True})
    objective = write(root, 'x/e/c/a/OBJECTIVE_REPORT.json', {'target_attainment': {'x': {'observed': 0.9}}})
    write(root, 'ARTIFACT_MANIFEST.json', {'artifacts': [objective]})
    write(root, objective['relative_path'], {'target_attainment': {'x': {'observed': 1.0}}})
    observation = {'candidate_summaries': [{'candidate_id': 'candidate', 'experiment_id': 'e', 'objective_report': {'target_attainment': {'x': {'observed': 1.0}}}}]}
    result = facts.build_execution_facts(tmp_path, {'status': 'completed'}, observation=observation)
    assert result['candidates'][0]['band_metrics'] == {}
    assert result['candidates'][0]['objective_status'] == 'artifact_invalid'


def test_optimizer_evaluations_prove_execution_without_budget_or_certificate(tmp_path):
    root = tmp_path / 'tmm_run'
    row = write(root, 'x/e/OPTIMIZER_gradient.json', {'evaluation_count': 205, 'status': 'completed'})
    write(root, 'ARTIFACT_MANIFEST.json', {'artifacts': [row]})
    result = facts.build_execution_facts(tmp_path, {'status': 'completed'})
    assert result['optimizer_performed'] is True
    assert result['physics_executed'] is True
    assert result['optimizers'][0]['raw_ref']['integrity'] == 'manifest_hash_verified'
    write(root, row['relative_path'], {'evaluation_count': 999})
    invalid = facts.build_execution_facts(tmp_path, {'status': 'completed'})
    assert invalid['optimizer_performed'] is None
    assert invalid['physics_executed'] is None


def test_explicit_path_cannot_bypass_tampered_identity(tmp_path):
    directory = 'x/e/c/a'
    identity = write(tmp_path, directory + '/IDENTITY.json', {'candidate_id': 'candidate', 'experiment_id': 'e', 'physical_directory': directory})
    certificate = write(tmp_path, directory + '/PHYSICS_ACCEPTANCE_CERTIFICATE.json', {'accepted': True, 'certificate_id': 'cert'})
    write(tmp_path, 'ARTIFACT_MANIFEST.json', {'artifacts': [identity, certificate]})
    write(tmp_path, identity['relative_path'], {'candidate_id': 'other', 'experiment_id': 'e', 'physical_directory': directory})
    result = facts.selected_candidate_certificate(tmp_path, 'candidate', experiment_id='e', artifact_ids=[certificate['relative_path']], certificate_id='cert')
    assert result['status'] == 'identity_conflict'
    assert result['payload'] is None


def test_incumbent_vectors_are_preserved_whole_or_omitted_whole():
    candidate = {'candidate_id': 'candidate', 'thicknesses_nm': [float(i) for i in range(20)],
                 'layer_materials': ['TiO2', 'SiO2'] * 10, 'parent_iteration_id': 'iteration_01',
                 'stack_identity': {'stack_id': 'stack'}, 'selected_role': 'best_target_score', 'robustness_score': 0.8}
    result = facts.compact_candidate(candidate)
    assert result['thicknesses_nm'] == candidate['thicknesses_nm']
    assert result['layer_materials'] == candidate['layer_materials']
    assert result['parent_iteration_id'] == 'iteration_01'
    assert result['stack_identity'] == {'stack_id': 'stack'}
    assert result['robustness_score'] == 0.8
    candidate['thicknesses_nm'] = list(range(65))
    omitted = facts.compact_candidate(candidate)['thicknesses_nm']
    assert omitted['omitted'] is True
    assert omitted['item_count'] == 65
    assert omitted['raw_ref']['field'] == 'thicknesses_nm'
    assert len(omitted['raw_ref']['sha256']) == 64


class ExecutionFactsTests(unittest.TestCase):
    pass


def _wrap(function, prefix=None):
    def run(self):
        with tempfile.TemporaryDirectory() as directory:
            args = {}
            if 'tmp_path' in inspect.signature(function).parameters:
                args['tmp_path'] = Path(directory)
            if prefix is not None:
                args['prefix'] = prefix
            function(**args)
    return run


for _name, _function in list(globals().items()):
    if _name.startswith('test_') and callable(_function):
        if 'prefix' in inspect.signature(_function).parameters:
            for _suffix, _prefix in [('compact', 'x/e_5059ae3185ac'), ('legacy', 'experiments/experiment-one')]:
                setattr(ExecutionFactsTests, _name + '_' + _suffix, _wrap(_function, _prefix))
        else:
            setattr(ExecutionFactsTests, _name, _wrap(_function))
        del globals()[_name]


if __name__ == '__main__':
    unittest.main()
