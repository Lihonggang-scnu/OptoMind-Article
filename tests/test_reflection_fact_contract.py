"""Offline reflection-contract tests without optional solver imports or model calls."""
import ast
import copy
import hashlib
import json
import re
import runpy
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

import pytest
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'code/optomind_optics/harness/route_reflection.py'
PROMPT = ROOT / 'code/prompts/optical_harness/TMM Route Reflection.txt'


class PureImports(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        return None


module = ast.parse(SOURCE.read_text())
nodes = [PureImports().visit(node) for node in module.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name != 'write_reflection_sidecar']
usage = []
namespace = dict(Any=Any, Literal=Literal, Mapping=Mapping, Protocol=Protocol, BaseModel=BaseModel,
                 ConfigDict=ConfigDict, Field=Field, model_validator=model_validator,
                 hashlib=hashlib, json=json, re=re, Path=Path,
                 DEFAULT_REFLECTION_PROMPT=PROMPT,
                 project_execution_history=runpy.run_path(str(ROOT / "code/optomind_optics/harness/execution_facts.py"))["project_execution_history"],
                 decision_score=lambda row: row.get('best_frozen_score') if row.get('score_basis') == 'frozen_standard' else row.get('best_target_score'),
                 reflection_evidence=lambda row: {k: row[k] for k in ('candidate_summaries', 'gradient_summary', 'run_status', 'failure_categories') if k in row},
                 get_cost_tracker=lambda: type('Tracker', (), {'record_qwen_usage': lambda self, *args: usage.append(args)})())
exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(SOURCE), 'exec'), namespace)
build = namespace['_build_reflection_payload']
reflect = namespace['reflect_on_route']


def observation():
    return {'iteration_id': 'iteration_02', 'run_status': 'completed', 'attempted_round_count': 2,
            'physics_executed_round_count': 1, 'score_basis': 'frozen_standard', 'best_frozen_score': -0.03100000000001,
            'execution_facts': {'iteration_id': 'iteration_02', 'optimizer_performed': True,
                'optimizer_committed_runs': 1, 'optimizers': [{'evaluation_count': 24, 'stop_reason': 'budget'}]},
            'candidate_summaries': [{'candidate_id': 'optimized', 'thicknesses_nm': [1.234567890123e-12]}]}


def response(**changes):
    return {'fact_contract_version': 1,
            'factual_assertions': [{'fact_id': '/semantic_facts/' + name, 'value': value} for name, value in
                                   [('optimization_occurred', True), ('physical_hypothesis_refuted', False), ('local_optimum_established', False), ('prior_optimization_occurred', None)]],
            'observed_vs_expected': 'A finite optimizer search missed the target.',
            'deviation_mechanism': 'The physical explanation is unresolved.', 'continue_recommended': True,
            'continue_rationale': 'Test a new initialization.', 'insight_for_next': 'Change seed to 8.',
            'insight_grounding': 'The optimizer exhausted its allocated budget.', **changes}


class Client:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def call(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return {'content': json.dumps(self.content), '_llm_usage': {'input_tokens': 10, 'output_tokens': 5}}


def test_bound_projection_preserves_critical_numbers_and_records_raw_refs():
    row = observation()
    row['execution_evidence'] = {'optimizers': [{'evaluation_count': 24}], 'huge': ['x' * 10000] * 200}
    row['candidate_summaries'][0]['detail'] = 'not converged ' + 'x' * 20000 + ' -1.23456789e-123'
    before = copy.deepcopy(row)
    payload = build({}, row, [-0.03100000000001], 1e-4, executed_history=[{'iteration_id': 'iteration_01', 'huge': row['execution_evidence']}] * 12)
    assert row == before
    assert len(json.dumps(payload)) < 50000
    assert payload['observed_metrics']['decision_score'] == -0.03100000000001
    assert payload['observed_metrics']['candidate_thicknesses_nm'][0][0] == 1.234567890123e-12
    assert 'source_sha256' in json.dumps(payload) and 'raw_ref' in json.dumps(payload)
    assert 'text_head' not in json.dumps(payload)


def test_current_identity_and_attempted_executed_scored_counts_are_separate():
    row = observation()
    payload = build({}, row, [], 1e-4, executed_history=[{'iteration_id': 'iteration_01'}, {'iteration_id': 'iteration_02'}])
    assert [row['iteration_id'] for row in payload['executed_history']] == ['iteration_01']
    assert payload['execution_identity'] == {'current_iteration_id': 'iteration_02', 'current_run_status': 'completed', 'attempted_round_count': 2, 'executed_round_count': 1, 'scored_round_count': 0}
    assert build({}, {}, [0.5], 1e-4)['observed_metrics']['rounds_executed'] is None


@pytest.mark.parametrize('name,value', [('optimization_occurred', False), ('physical_hypothesis_refuted', True), ('local_optimum_established', True)])
def test_contradictory_assertions_are_rejected_with_exactly_one_metered_call(name, value):
    reply = response()
    next(item for item in reply['factual_assertions'] if item['fact_id'].endswith('/' + name))['value'] = value
    client = Client(reply)
    result = reflect(client, pre_declarations={}, observation=observation(), score_history=[])
    assert 'contradicts visible evidence' in result.degraded_reason
    assert len(client.calls) == 1 and usage[-1] == ('turbo', 15)


def test_failure_is_not_a_physical_refutation_and_previous_optimizer_is_not_current():
    row = {'iteration_id': 'iteration_02', 'run_status': 'not_run', 'execution_facts': {'optimizer_performed': None}}
    payload = build({}, row, [], 1e-4, executed_history=[{'iteration_id': 'iteration_01', 'execution_facts': {'optimizer_performed': True}}])
    assert payload['semantic_facts']['optimization_occurred'] is None
    assert payload['semantic_facts']['physical_hypothesis_refuted'] is False


def test_legacy_response_preserved_with_explicit_unavailable_validation():
    reply = response()
    del reply['fact_contract_version']
    del reply['factual_assertions']
    reply['observed_vs_expected'] = 'The old model claimed no optimization happened.'
    result = reflect(Client(reply), pre_declarations={}, observation=observation(), score_history=[])
    assert result.observed_vs_expected == reply['observed_vs_expected']
    assert result.factual_validation == 'unavailable_legacy'
    assert 'not semantically verified' in result.factual_limitations
    assert result.next_decision is None and not result.degraded_reason


def test_visible_fact_binding_and_optional_next_decision():
    decision = {'observation_ids': ['/execution_facts/optimizers/0/stop_reason'], 'unresolved_question': 'Initialization or topology?',
                'intervention': 'Change seed to 8', 'fixed_conditions': ['objective', 'layer order'],
                'predicted_observable_change': 'A different score', 'basis': 'Search ended at budget',
                'alternative': 'Topology limits performance', 'outcome_changing_decision': 'If unchanged, test topology'}
    result = reflect(Client(response(next_decision=decision)), pre_declarations={}, observation=observation(), score_history=[])
    assert result.factual_validation == 'validated_structured' and not result.degraded_reason
    decision['observation_ids'] = ['/execution_facts/candidates/999/score']
    result = reflect(Client(response(next_decision=decision)), pre_declarations={}, observation=observation(), score_history=[])
    assert 'unavailable observation' in result.degraded_reason


def test_missing_required_assertions_and_wrong_types_fail():
    result = reflect(Client(response(factual_assertions=[])), pre_declarations={}, observation=observation(), score_history=[])
    assert 'missing required' in result.degraded_reason
    reply = response()
    reply['factual_assertions'][0]['value'] = 1
    result = reflect(Client(reply), pre_declarations={}, observation=observation(), score_history=[])
    assert 'contradicts' in result.degraded_reason


def test_versioned_new_execution_cannot_silently_accept_unstructured_response():
    row = observation()
    row['execution_facts']['schema_version'] = 'execution-facts.v1'
    reply = response()
    del reply['fact_contract_version']
    del reply['factual_assertions']
    result = reflect(Client(reply), pre_declarations={}, observation=row, score_history=[])
    assert 'require fact_contract_version=1' in result.degraded_reason


def test_rgb_prior_commit_survives_large_execution_grid_and_refutes_no_optimizer_claim():
    history = [{'iteration_id': 'iteration_02', 'decision_score': 1.94599113067998,
                'execution': {'grid': list(range(10000))},
                'evidence': {'run_status': 'completed', 'allocation_events': [
                    {'event_type': 'commit', 'action_id': 'optimizer:gradient_thickness', 'actual': 205}]}}]
    row = {'iteration_id': 'iteration_03', 'run_status': 'not_run'}
    payload = build({}, row, [1.94599113067998], 1e-4, executed_history=history)
    assert payload['semantic_facts']['optimization_occurred'] is None
    assert payload['semantic_facts']['prior_optimization_occurred'] is True
    assert payload['executed_history'][0]['decision_score'] == 1.94599113067998
    assert payload['executed_history'][0]['optimizer_commits'][0]['actual'] == 205
    reply = response()
    for fact in reply['factual_assertions']:
        if fact['fact_id'].endswith('/optimization_occurred'):
            fact['value'] = None
        if fact['fact_id'].endswith('/prior_optimization_occurred'):
            fact['value'] = False
    result = reflect(Client(reply), pre_declarations={}, observation=row, score_history=[], executed_history=history)
    assert 'contradicts visible evidence' in result.degraded_reason


def test_conditional_mechanism_update_reaches_payload_and_next_decision():
    row = observation()
    row['mechanism_update'] = {'status': 'updated', 'updated': True, 'belief_id': 'b1',
        'belief_scope': 'conditional_on_declared_finite_hypotheses_prior_and_observation_model',
        'belief_binding': 'bound', 'intervention_id': 'seed-change', 'condition_id': 'fixed-topology',
        'hypothesis_ids': ['initialization', 'topology'], 'prior': [0.5, 0.5], 'posterior': [0.8, 0.2],
        'predictive_support': {'posterior_predictive': {'improvement': 0.7, 'flat': 0.3}},
        'remaining_uncertainty': {'entropy_nats': 0.5004024235381879, 'outside_declared_hypotheses': 'unresolved'},
        'consumed_observation_ids': ['obs-2'], 'observation_bindings': {'obs-2': {'observation_hash': 'o', 'prediction_hash': 'p'}},
        'mechanism_claim': 'not_established_by_conditional_posterior'}
    payload = build({}, row, [], 1e-4)
    evidence = payload['mechanism_evidence']
    for key, value in row['mechanism_update'].items():
        assert evidence[key] == value
    assert evidence['raw_ref'] == 'observation.mechanism_update'
    assert len(evidence['source_sha256']) == 64
    reply = response(next_decision={'observation_ids': ['/mechanism_evidence/posterior/0', '/mechanism_evidence/condition_id'],
        'unresolved_question': 'Initialization or topology?', 'intervention': 'Change seed',
        'fixed_conditions': ['fixed-topology'], 'predicted_observable_change': 'Score improvement',
        'basis': 'Conditional posterior favors initialization', 'alternative': 'Topology',
        'outcome_changing_decision': 'A flat result motivates topology comparison'})
    reply['factual_assertions'].append({'fact_id': '/mechanism_evidence/posterior/0', 'value': 0.8})
    client = Client(reply)
    result = reflect(client, pre_declarations={}, observation=row, score_history=[])
    assert result.factual_validation == 'validated_structured'
    assert len(client.calls) == 1
    sent = json.loads(client.calls[0][0][1]['content'])
    assert sent['mechanism_evidence']['posterior'] == [0.8, 0.2]
    reply['factual_assertions'][-1]['value'] = 0.9
    assert 'contradicts' in reflect(Client(reply), pre_declarations={}, observation=row, score_history=[]).degraded_reason


def test_mechanism_projection_is_bounded_and_unknown_is_explicit():
    assert build({}, {}, [], 1e-4)['mechanism_evidence']['status'] == 'unavailable'
    row = observation()
    row['mechanism_update'] = {'status': 'unresolved', 'updated': False, 'posterior': None,
        'reason': 'No validated noise model', 'observation_bindings': {str(i): {'blob': 'x' * 10000} for i in range(100)},
        'irrelevant_blob': 'x' * 100000}
    evidence = build({}, row, [], 1e-4)['mechanism_evidence']
    assert evidence['status'] == 'unresolved' and evidence['posterior'] is None
    assert evidence['reason'] == 'No validated noise model'
    assert len(json.dumps(evidence)) < 10000
    assert 'irrelevant_blob' not in evidence
