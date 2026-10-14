"""Offline tests: execute only the pure projection functions, never a model/solver."""
import ast
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

SOURCE = Path(__file__).resolve().parents[1] / 'code/optomind_optics/harness/strategy_planner.py'
module = ast.parse(SOURCE.read_text())
names = {'_compact_execution_evidence', '_compact_prior_iterations'}
namespace = dict(Any=Any, Iterable=Iterable, Mapping=Mapping, hashlib=hashlib, json=json)
exec(compile(ast.Module(body=[n for n in module.body if isinstance(n, ast.FunctionDef) and n.name in names], type_ignores=[]), str(SOURCE), 'exec'), namespace)
compact = namespace['_compact_prior_iterations']


def row():
    return {
        'iteration_id': 'round-2', 'route_id': 'r',
        'route_snapshot': {'proposed_materials': ['proposal-A', 'proposal-B']},
        'selected_candidate_ids': ['incumbent-round-1'],
        'best_frozen_candidate_ids': ['new-round-2'],
        'best_frozen_score': -0.1, 'score_basis': 'frozen_standard',
        'score_basis_fingerprint': 'frozen-fingerprint', 'score_available': True,
        'score_direction': 'maximize',
        'executed_record': {
            'iteration_id': 'round-2', 'route_id': 'r', 'execution_signature': 'execution-hash',
            'decision_score': -0.1,
            'execution': {'experiments': [{'mode': 'optimize', 'tmm_task': {
                'simulation': {'stack': {'layers': [{'material': 'actual-B'}, {'material': 'actual-A'}]},
                               'spectrum': {'values_nm': list(range(1000))}},
                'optimizer': {'initialization': 'random', 'seed': 7}}}],
                'frozen_objective': {'metrics': [['metric', {'sense': 'minimize', 'target': 0.03}]],
                                     'formula': '-metric'}},
            'evidence': {'optimizers': [{'status': 'completed', 'stop_reason': 'budget',
                            'budget_exhausted': True, 'evaluation_count': 34}],
                         'forward_evaluation_accounting': {'run_forward_evaluations': 60},
                         'budget_limits': {'forward_evaluations': 64}}},
        'budget_usage': {'forward_evaluations': 60},
        'gradient_summary': {'status': 'unavailable', 'reason': 'insufficient remaining budget',
                            'required_forward_evaluations': 5, 'remaining_forward_evaluations': 4},
        'insight_for_next': 'Try a different order.',
        'insight_grounding': ['The current measured score missed the target.'],
        'candidate_summaries': [{'candidate_id': 'new-round-2', 'target_score': 0.8,
             'frozen_score': -0.1, 'frozen_score_outcome': {'ok': True, 'value': -0.1, 'errors': []},
             'score_basis_fingerprint': 'frozen-fingerprint', 'objective_report': {'metric': 0.1}}]
    }


def test_preserves_executed_not_proposed_decision_and_metric_identity():
    source = row()
    before = copy.deepcopy(source)
    out = compact([source])[0]
    assert source == before
    execution = out['executed_record']['execution']
    assert execution['experiments'][0]['tmm_task']['simulation']['stack']['layers'][0]['material'] == 'actual-B'
    assert execution['frozen_objective']['metrics'][0][1]['sense'] == 'minimize'
    for key in ('selected_candidate_ids', 'best_frozen_candidate_ids', 'best_frozen_score',
                'score_direction', 'score_basis_fingerprint', 'score_available', 'insight_for_next', 'insight_grounding'):
        assert out[key] == source[key]
    assert out['executed_record']['execution_signature'] == 'execution-hash'
    assert out['candidate_summaries'][0]['frozen_score_outcome'] == {'ok': True, 'value': -0.1, 'errors': []}
    assert 'proposal-A' not in json.dumps(out)


def test_no_hindsight_incumbent_replacement_or_missing_diagnostic_inference():
    out = compact([row()])[0]
    assert out['selected_candidate_ids'] == ['incumbent-round-1']
    assert out['best_frozen_candidate_ids'] == ['new-round-2']
    assert out['gradient_summary']['status'] == 'unavailable'
    assert out['gradient_summary']['reason'] == 'insufficient remaining budget'
    assert out['executed_record']['evidence']['optimizers'][0]['stop_reason'] == 'budget'
    assert 'gradient_summary' not in compact([{}])[0]
    assert 'selected_candidate_ids' not in compact([{}])[0]


def test_error_zero_false_and_null_survive():
    source = {'score_available': False, 'best_frozen_score': 0.0,
              'gradient_summary': {'status': 'unavailable', 'errors': ['binding mismatch'], 'metric_value': None},
              'candidate_summaries': [{'candidate_id': 'bad', 'frozen_score': None,
                  'frozen_score_outcome': {'ok': False, 'errors': ['missing metric']}}]}
    out = compact([source])[0]
    assert out['score_available'] is False and out['best_frozen_score'] == 0.0
    assert out['gradient_summary'] == source['gradient_summary']
    assert out['candidate_summaries'][0]['frozen_score_outcome']['ok'] is False


def test_spectra_omitted_with_source_fingerprint_and_size_is_bounded():
    out = compact([row()])[0]
    spectrum = out['executed_record']['execution']['experiments'][0]['tmm_task']['simulation']['spectrum']
    assert spectrum['compaction_omitted'] == 'spectral_data'
    assert len(spectrum['source_sha256']) == 64
    huge = {'executed_record': {'wide': [{'blob': 'x' * 10000} for _ in range(10000)]}}
    bounded = compact([huge])[0]
    assert len(json.dumps(bounded)) < 15000
    assert 'sequence_limit' in json.dumps(bounded) and 'text_limit' in json.dumps(bounded)


def test_legacy_projection_and_history_bound_remain():
    out = compact({'iteration_id': str(i), 'candidate_summaries': [{'candidate_id': str(n)} for n in range(8)]} for i in range(9))
    assert [r['iteration_id'] for r in out] == ['3', '4', '5', '6', '7', '8']
    assert len(out[0]['candidate_summaries']) == 4
    assert out[0]['physically_valid_candidate_count'] == 0


def test_provenance_rule_does_not_force_specific_strategy():
    source = SOURCE.read_text()
    rule = next(n for n in ast.walk(module) if isinstance(n, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == 'prior_evidence' for k in n.keys))
    value = next(v.value for k, v in zip(rule.keys, rule.values) if isinstance(k, ast.Constant) and k.value == 'prior_evidence')
    assert 'hypotheses' in value and 'not measured improvements' in value
    assert 'budget stop is not evidence of convergence' in value
    assert 'SiO2' not in value and 'TiO2' not in value


def test_fake_client_receives_evidence_with_unchanged_hypothesis_text():
    import os
    import runpy
    root = SOURCE.parents[3]
    default_repo = root / 'repo' if (root / 'repo').is_dir() else root
    repo = Path(os.environ.get('PLANNER_REPO_ROOT', default_repo)).resolve()
    assert (repo / 'code/optomind_optics/harness/strategy_planner.py').read_bytes() == SOURCE.read_bytes()
    # Reuse the exact-base canned plan; the injected client has no network path.
    fixtures = runpy.run_path(str(repo / 'code/tests/test_tmm_strategy_planner.py'))
    client = fixtures['_FakeClient']([fixtures['_plan'](fixtures['_route']())])
    result = fixtures['QwenTMMStrategyPlanner'](client=client).plan(
        {'problem_id': 'p1', 'primary_intent': 'design'}, {'evidence': [{'evidence_id': 'ev_1'}]},
        prior_iterations=[row()], feedback_directives=['Test the proposed mechanism.'])
    assert result.status == 'planned'
    payload = json.loads(client.calls[0][1]['content'])
    prior = payload['prior_iterations'][0]
    assert prior['executed_record']['execution_signature'] == 'execution-hash'
    assert prior['gradient_summary']['status'] == 'unavailable'
    assert prior['insight_grounding'] == row()['insight_grounding']
    assert payload['feedback_directives'] == ['Test the proposed mechanism.']
    assert 'not measured improvements' in payload['fixed_rules']['prior_evidence']
