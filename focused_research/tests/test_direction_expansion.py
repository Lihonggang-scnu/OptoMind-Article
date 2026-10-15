"""Offline semantic-expansion contract tests: no paid clients or physics calls."""
import copy
import json
from unittest import mock

import pytest

from focused_research import direction_expansion as de


def fixture():
    route = {'id': 'r', 'family': {'layers': [
        {'material': 'A', 'thickness_nm': 'd'},
        {'material': 'A', 'thickness_nm': 'd'},
        {'material': 'B', 'thickness_nm': 25.}],
        'thickness_variables_nm': {'d': [10., 100.]}}}
    envelope = {'route_id': 'r', 'instance_id': 'old', 'status': 'prepared_requires_material_and_numerical_preflight',
        'objectives': [{'objective_id': 'g1', 'sense': 'maximize'}, {'objective_id': 'g2', 'sense': 'minimize'}],
        'tmm_task': {'stack': {'name': 'old', 'layers': [
            {'material': 'A', 'thickness_nm': 50.}, {'material': 'A', 'thickness_nm': 50.},
            {'material': 'B', 'thickness_nm': 25.}]}, 'illumination': {'angle': 0}},
        'numerical_search': {'uncertified_metrics': {'g1': 1}},
        'numeric_search_observations': [{'old_derivative': 1}]}
    previous = {'archive': {'designs': {'p': {}}, 'objectives': envelope['objectives']},
        'envelopes': {'p': envelope}, 'accepted': {'routes': [route]}, 'diagnostics': [],
        'context': {'constraints': {'allowed_materials': ['A', 'B']}, 'sheet': {'all': 'goals'}},
        'manifest_sha256': 'binding'}
    plans = {'plans': [{'parent_design_id': 'p', 'action': 'refine_family',
                        'diagnosis': {'explanation': 'weak or contradictory hypothesis'}}]}
    branch = {'branch_id': 'b', 'plan_index': 0, 'parent_design_id': 'p',
              'hypothesis_id': 'plan_0000', 'parameterization': 'family', 'parameters_nm': {'d': 40.}}
    return previous, plans, branch


def test_model_values_control_seed_and_preserve_contract():
    previous, plans, branch = fixture()
    before = copy.deepcopy(previous)
    branch['search_bounds_nm'] = {'d': [30., 45.]}
    result = de.validate_expansion({'branches': [branch]}, previous, plans)
    assert result['report']['accepted_count'] == 1
    job = result['jobs'][0]
    seed = job['seed_envelopes'][0]
    assert [l['thickness_nm'] for l in seed['tmm_task']['stack']['layers']] == [40., 40., 25.]
    assert seed['objectives'] == before['envelopes']['p']['objectives']
    assert seed['tmm_task']['illumination'] == {'angle': 0}
    assert 'numerical_search' not in seed and 'numeric_search_observations' not in seed
    assert job['route'] == before['accepted']['routes'][0]
    assert seed['direction_expansion']['parent_design_id'] == 'p'
    assert previous == before


@pytest.mark.parametrize('change', [
    {'parameters_nm': {'d': float('nan')}}, {'parameters_nm': {'d': True}},
    {'parameters_nm': {'d': 101.}}, {'parameters_nm': {'wrong': 30.}},
    {'search_bounds_nm': {'d': [0., 40.]}}, {'search_bounds_nm': {'d': [45., 60.]}},
    {'search_bounds_nm': {'wrong': [30., 50.]}}, {'parent_design_id': 'other'},
    {'hypothesis_id': 'other'}, {'parameterization': 'layerwise_refinement'},
    {'plan_index': True},
])
def test_reject_one_branch_only(change):
    previous, plans, good = fixture()
    bad = {**copy.deepcopy(good), 'branch_id': 'bad', **change}
    result = de.validate_expansion({'branches': [bad, good]}, previous, plans)
    assert result['report']['rejected_count'] == 1
    assert [j['branch_id'] for j in result['jobs']] == ['b']


def test_layerwise_uses_actual_parent_and_keeps_fixed_layer():
    previous, plans, branch = fixture()
    previous['envelopes']['p']['tmm_task']['stack']['layers'][1]['thickness_nm'] = 60.
    plans['plans'][0]['action'] = 'refine_layers'
    branch.update(parameterization='layerwise_refinement', parameters_nm={'layer_0:d': 42., 'layer_1:d': 65.})
    view = de._parent_view(previous, plans['plans'][0], 0)
    assert view['variables']['layer_1:d']['value_nm'] == 60.
    result = de.validate_expansion({'branches': [branch]}, previous, plans)
    assert result['report']['accepted_count'] == 1
    assert [l['thickness_nm'] for l in result['jobs'][0]['seed_envelopes'][0]['tmm_task']['stack']['layers']] == [42., 65., 25.]
    plans['plans'][0]['action'] = 'refine_family'
    assert de.validate_expansion({'branches': [branch]}, previous, plans)['report']['rejected_count'] == 1


def test_no_branch_ceiling_or_opinion_gate_and_duplicate_identity_rejected():
    previous, plans, branch = fixture()
    branches = [{**branch, 'branch_id': 'b%d' % i, 'rationale': 'possibly wrong'} for i in range(25)]
    result = de.validate_expansion({'branches': branches + [branches[0]]}, previous, plans)
    assert result['report']['accepted_count'] == 25
    assert result['report']['rejected_count'] == 1


class FakePlanner:
    def __init__(self, response, finish='stop'):
        self.response, self.calls = response, 0
        self.last_usage = {'test_only': True}
        self.last_transport = {'finish_reason': finish, 'model': 'offline_fake'}

    def _call_query_plan_model(self, prompt):
        self.calls += 1
        self.prompt = prompt
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.parametrize('finish,status', [('stop', 'branches_recorded'), ('length', 'branches_partial')])
def test_one_audited_call_with_full_context_and_partial_finish(tmp_path, finish, status):
    previous, plans, branch = fixture()
    planner = FakePlanner(json.dumps({'branches': [branch]}), finish)
    out = tmp_path / 'expansion'
    with mock.patch.object(de.ss, 'read_search', return_value=previous):
        result = de.expand_directions(tmp_path / 'previous', plans, output=out, planner=planner, max_forward_evaluations=100)
    assert planner.calls == 1 and result['status'] == status
    assert result['next_action_executed'] is False
    assert planner.prompt['user_payload']['design_constraints'] == previous['context']['constraints']
    assert 'original_context' not in planner.prompt['user_payload']
    assert planner.prompt['user_payload']['original_objectives'] == previous['archive']['objectives']
    assert json.loads((out / 'raw_response.json').read_text())['response'] == planner.response
    manifest = json.loads((out / 'manifest.json').read_text())
    for name, value in manifest.items():
        assert de.digest(json.loads((out / name).read_text())) == value


@pytest.mark.parametrize('response', ['{"branches": [', '{"branches":[NaN]}', {'branches': [float('nan')]}, RuntimeError('offline failure')])
def test_failure_persisted_without_retry(tmp_path, response):
    previous, plans, _ = fixture()
    planner = FakePlanner(response)
    out = tmp_path / 'expansion'
    with mock.patch.object(de.ss, 'read_search', return_value=previous):
        result = de.expand_directions(tmp_path / 'previous', plans, output=out, planner=planner, max_forward_evaluations=0)
    assert planner.calls == 1 and result['status'] == 'expansion_failed'
    assert (out / 'raw_response.json').exists()
    assert not (out / 'expansion_jobs.json').exists()
    assert not (out / 'expansion_response.json').exists()


def test_no_valid_plans_skips_paid_boundary(tmp_path):
    previous, plans, branch = fixture()
    plans['plans'][0]['parent_design_id'] = 'missing'
    planner = FakePlanner({'branches': [branch]})
    with mock.patch.object(de.ss, 'read_search', return_value=previous):
        result = de.expand_directions(tmp_path / 'previous', plans, output=tmp_path / 'expansion',
                                     planner=planner, max_forward_evaluations=100)
    assert planner.calls == result['planner_calls'] == 0
    assert result['status'] == 'no_executable_plan'


def test_large_archive_five_plan_prompt_projection_preserves_task(tmp_path):
    previous, plans, branch = fixture()
    previous['context']['sheet'] = {'original_question': 'Exact original question',
        'subgoals': [{'id': 'g1', 'meaning': 'all original goals'}, {'id': 'g2'}],
        'explicit_constraints': [{'id': 'c1', 'constraint': 'fixed layer stays'}],
        'goal_relationships': {'mode': 'all', 'priority_order': None}}
    previous['context']['evidence'] = {'items': [{'paper': 'UNRELATED_PAPER' * 10000}]}
    previous['context']['routes'] = {'routes': [{'unused': 'UNRELATED_ROUTE' * 10000}]}
    template = copy.deepcopy(previous['envelopes']['p'])
    template['numeric_search_observations'] = [{'large': 'OLD_NUMERICAL_HISTORY' * 1000}]
    # A 24-layer, 126-design archive; only five selected parents go to the model.
    template['tmm_task']['stack']['layers'] = [copy.deepcopy(layer) for _ in range(8) for layer in template['tmm_task']['stack']['layers']]
    previous['accepted']['routes'][0]['family']['layers'] *= 8
    for i in range(126):
        parent = 'parent_%03d' % i
        previous['envelopes'][parent] = copy.deepcopy(template)
        previous['archive']['designs'][parent] = {'metrics': {'g1': {'value': i / 126}, 'g2': {'value': .2}}}
    previous['archive']['designs'].pop('p')
    previous['envelopes'].pop('p')
    plans['plans'] = [{**plans['plans'][0], 'parent_design_id': 'parent_%03d' % i} for i in range(5)]
    planner = FakePlanner({'branches': []})
    out = tmp_path / 'expansion'
    with mock.patch.object(de.ss, 'read_search', return_value=previous):
        de.expand_directions(tmp_path / 'previous', plans, output=out, planner=planner, max_forward_evaluations=200)
    text = json.dumps(planner.prompt)
    payload = planner.prompt['user_payload']
    assert len(text) < 15000
    assert not any(marker in text for marker in ('UNRELATED_PAPER', 'UNRELATED_ROUTE', 'OLD_NUMERICAL_HISTORY'))
    assert len(payload['plans']) == 5 and len(payload['family_catalog']) == 1
    assert payload['original_question'] == 'Exact original question'
    assert payload['original_objectives'] == previous['archive']['objectives']
    for key in ('subgoals', 'explicit_constraints', 'goal_relationships'):
        assert payload[key] == previous['context']['sheet'][key]
    family = next(iter(payload['family_catalog'].values()))
    assert len(family['layers']) == 24
    assert family['layers'][2]['fixed_thickness_nm'] == 25.
    assert family['physical_conditions']['illumination'] == {'angle': 0}
    assert payload['plans'][4]['parent_metrics']['g1']['value'] == 4 / 126
    assert json.loads((out / 'context_bindings.json').read_text())['evidence'] == de.digest(previous['context']['evidence'])


def test_explanatory_and_unknown_fields_are_preserved_but_not_executed():
    previous, plans, branch = fixture()
    branch.update(expected_goal_changes={'g1': 'increase'}, notes='Speculative explanation',
                  layers=[{'material': 'NOT_ALLOWED'}], objectives=[])
    result = de.validate_expansion({'branches': [branch]}, previous, plans)
    assert result['report']['accepted_count'] == 1
    audit = result['report']['normalization_audit'][0]
    assert audit['ignored_fields'] == ['expected_goal_changes', 'layers', 'notes', 'objectives']
    assert audit['ignored_fields_executed'] is False
    seed = result['jobs'][0]['seed_envelopes'][0]
    assert seed['objectives'] == previous['envelopes']['p']['objectives']
    assert seed['tmm_task']['stack']['layers'][0]['material'] == 'A'
    assert branch['notes'] == 'Speculative explanation'


def test_missing_redundant_identity_binds_from_plan_with_audit():
    previous, plans, branch = fixture()
    for key in ('parent_design_id', 'hypothesis_id', 'parameterization'):
        branch.pop(key)
    result = de.validate_expansion({'branches': [branch]}, previous, plans)
    assert result['report']['accepted_count'] == 1
    assert result['report']['normalization_audit'][0]['bound_from_selected_plan'] == {
        'parent_design_id': 'p', 'hypothesis_id': 'plan_0000', 'parameterization': 'family'}
    branch['parent_design_id'] = 'other'
    assert de.validate_expansion({'branches': [branch]}, previous, plans)['report']['rejected_count'] == 1
    branch.pop('parent_design_id')
    branch.pop('parameters_nm')
    assert de.validate_expansion({'branches': [branch]}, previous, plans)['report']['rejected_count'] == 1
