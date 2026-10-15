"""Offline contract and transport fixtures; never contacts a model or runs TMM."""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor' / 'review'))
import research_routes as rr


def fixture():
    def read(name):
        return json.loads((ROOT / 'examples' / name).read_text())
    return (read('research_routes_example.json'), read('rgb_nir_task_sheet.json'),
            read('research_routes_evidence_fixture.json'), read('research_routes_constraints.json'))


def check(values):
    routes, sheet, evidence, constraints = values
    return rr.validate(routes, sheet, evidence, design_constraints=constraints)


def test_weak_evidence_is_still_a_prepared_hypothesis():
    values = fixture()
    routes, sheet, evidence, constraints = values
    assert check(values) is routes
    before = copy.deepcopy(values)
    stacks = rr.materialize(routes, 'R1', sheet, evidence, design_constraints=constraints)
    assert stacks == rr.materialize(routes, 'R1', sheet, evidence, design_constraints=constraints)
    assert stacks[0]['status'] == 'prepared'
    assert len(stacks[0]['layers']) == 8
    assert stacks[0]['layers'][0] == {'material': 'TiO2', 'thickness_nm': 55}
    assert stacks[1]['layers'][0]['thickness_nm'] == 65
    assert stacks[0]['objectives'] == [g['executable_metric'] for g in sheet['subgoals']]
    assert values == before
    assert 'untested' in routes['routes'][0]['claim']
    assert routes['decision_clues'][0]['source_quote'] != routes['decision_clues'][0]['inference_bridge']


@pytest.mark.parametrize('mutate', [
    lambda r,s,e,c: r['decision_clues'][0].update(evidence_id='invented'),
    lambda r,s,e,c: r['routes'][0].update(evidence_ids=['invented']),
    lambda r,s,e,c: r['routes'][0].update(clue_ids=['invented']),
    lambda r,s,e,c: r['routes'][0].update(goal_ids=['G999']),
    lambda r,s,e,c: r['decision_clues'][0].update(source_quote='This was never in the source.'),
    lambda r,s,e,c: e['items'][0].update(text=''),
    lambda r,s,e,c: e['items'][0].update(source_id=None),
    lambda r,s,e,c: e['items'][0].update(eligible_as_evidence=False),
    lambda r,s,e,c: r['routes'][0]['family']['layers'][0].update(material='Au'),
    lambda r,s,e,c: r['routes'][0]['family'].update(layers=r['routes'][0]['family']['layers'] * 4),
    lambda r,s,e,c: r['routes'][0]['family'].update(kind='python_exec'),
    lambda r,s,e,c: r['routes'][0]['family']['layers'][0].update(thickness_nm='eval("10")'),
    lambda r,s,e,c: r['routes'][0]['instances'][0]['parameters_nm'].update(dH=500),
    lambda r,s,e,c: r['routes'][0]['instances'][0]['parameters_nm'].update(dH=float('nan')),
    lambda r,s,e,c: r['routes'][0]['family'].update(substrate='silicon'),
    lambda r,s,e,c: r['routes'][0]['expectations'][0]['metric'].update(metric='color_rendering_index'),
    lambda r,s,e,c: r['routes'][0]['expectations'][0]['metric'].update(mean_reduction='sample_mean'),
    lambda r,s,e,c: r['routes'][0]['expectations'][0]['metric']['region'].update(angle_deg=0),
    lambda r,s,e,c: r['routes'][0]['expectations'][0].update(reason=''),
    lambda r,s,e,c: r['routes'][0]['expectations'][0].update(changed_variables=[]),
    lambda r,s,e,c: r['routes'][0]['expectations'][0].update(candidate_instance_id='missing'),
    lambda r,s,e,c: r['routes'][0].update(executed_result={'mean_reflectance':.99}),
    lambda r,s,e,c: r['routes'][0].update(confidence_probability=.99),
    lambda r,s,e,c: c['constraint_source_ids'].update(max_layers=['C999']),
    lambda r,s,e,c: c.update(assumptions={}),
])
def test_rejects_fabricated_unexecutable_or_result_fields(mutate):
    values = fixture()
    mutate(*values)
    with pytest.raises(ValueError):
        check(values)


@pytest.mark.parametrize('field', ['expectations', 'transfer_assumptions', 'compatibility_conditions'])
def test_missing_required_content_needs_revision(field):
    values = fixture()
    route = values[0]['routes'][0]
    route[field] = []
    with pytest.raises(ValueError, match='must be needs_revision'):
        check(values)
    route['readiness'] = 'needs_revision'
    check(values)
    with pytest.raises(ValueError, match='materialization blocked'):
        rr.materialize(*values[:1], 'R1', *values[1:3], design_constraints=values[3])


def test_incomplete_recipe_is_preserved_but_never_materialized():
    values = fixture()
    route = values[0]['routes'][0]
    route.update(family=None, instances=[], expectations=[], readiness='needs_revision',
                 open_requirements=['Thickness parameterization still needs to be specified.'])
    check(values)


def test_contradictory_condition_is_retained_for_revision():
    values = fixture()
    route = values[0]['routes'][0]
    route['compatibility_conditions'][0]['required_value'] = 0
    with pytest.raises(ValueError, match='must be needs_revision'):
        check(values)
    route['readiness'] = 'needs_revision'
    check(values)
    assert any('conflicts' in reason for reason in rr.revision_reasons(route, values[1], values[3]))


def test_unknown_required_condition_blocks_ready():
    values = fixture()
    values[0]['routes'][0]['compatibility_conditions'][0]['required_value'] = None
    values[0]['routes'][0]['readiness'] = 'needs_revision'
    check(values)


@pytest.mark.parametrize('field', ['allowed_materials', 'max_layers', 'substrate', 'incident_medium'])
def test_missing_configuration_blocks_ready(field):
    values = fixture()
    values[3][field] = None
    values[0]['routes'][0]['readiness'] = 'needs_revision'
    check(values)


def test_nonhardcoded_layer_limit():
    values = fixture()
    values[3]['max_layers'] = 7
    with pytest.raises(ValueError, match='layer limit'):
        check(values)


class Planner:
    def __init__(self, responses, usage=None, transport=None):
        self.responses = iter(responses)
        self.last_usage = usage or {'input_tokens': 10}
        self.last_transport = transport or {'provider': 'offline_fixture', 'fallback_used': False}
        self.calls = []

    def _call_query_plan_model(self, prompt):
        self.calls.append(copy.deepcopy(prompt))
        return next(self.responses)


def run_generation(planner, **kwargs):
    _, sheet, evidence, constraints = fixture()
    return rr.generate(sheet, evidence, planner, design_constraints=constraints, **kwargs)


def test_generation_one_repair_preserves_both_raw_responses():
    routes, _, _, _ = fixture()
    raw = json.dumps(routes)
    planner = Planner(['{"invalid":true}', raw])
    result = run_generation(planner)
    assert result['status'] == 'ok'
    assert result['revision_rounds'] == 1
    assert result['rounds'][0]['raw_response'] == '{"invalid":true}'
    assert result['rounds'][0]['effective_response'] is None
    assert result['rounds'][0]['error']
    assert result['rounds'][1]['effective_response'] == routes
    assert planner.calls[1]['user_payload']['evidence_items'] == fixture()[2]


def test_generation_repair_limit_and_invalid_preservation():
    planner = Planner(['{}', '{}', '{}'])
    result = run_generation(planner)
    assert result['status'] == 'invalid'
    assert len(planner.calls) == 2
    assert len(result['rounds']) == 2
    assert result['errors']
    assert run_generation(Planner(['{}']), max_repairs=0)['revision_rounds'] == 0
    with pytest.raises(ValueError):
        run_generation(Planner([]), max_repairs=2)


@pytest.mark.parametrize('usage,transport,status', [
    ({'mock_llm':True}, {}, 'mock_rejected'),
    ({}, {'fallback_used':True}, 'transport_rejected'),
    ({}, {'transport_error':'offline'}, 'transport_rejected'),
])
def test_mock_and_transport_fail_closed(usage, transport, status):
    result = run_generation(Planner([json.dumps(fixture()[0])], usage, transport))
    assert result['status'] == status
    assert result['research_routes'] == {}
    assert len(result['rounds']) == 1


def test_no_evidence_does_not_require_invented_route():
    routes = {'schema_version':rr.SCHEMA, 'decision_clues':[], 'routes':[]}
    result = run_generation(Planner([json.dumps(routes)]))
    assert result['status'] == 'needs_revision'
    assert result['research_routes'] == routes


def test_build_prompt_is_local_and_includes_exact_inputs():
    _, sheet, evidence, constraints = fixture()
    prompt = rr.build_prompt(sheet, evidence, design_constraints=constraints)
    assert prompt['user_payload']['task_sheet'] == sheet
    assert prompt['user_payload']['evidence_items'] == evidence
    assert 'not computed designs' in prompt['system_prompt']
    assert len(prompt['prompt_hash']) == 64


def test_unknown_direction_for_other_goal_does_not_block_ready():
    values = fixture()
    route = values[0]['routes'][0]
    route['expectations'] = route['expectations'][:1]
    route['transfer_assumptions'].append('The NIR transmission response is scientifically unknown; no direction is asserted for G2.')
    check(values)
    assert route['readiness'] == 'ready'


def test_focused_route_materializes_all_user_goals():
    routes, sheet, evidence, constraints = fixture()
    route = routes['routes'][0]
    route['goal_ids'] = ['G1']
    route['compatibility_conditions'] = route['compatibility_conditions'][:1]
    route['expectations'] = route['expectations'][:1]
    stacks = rr.materialize(routes, 'R1', sheet, evidence, design_constraints=constraints)
    for stack in stacks:
        assert stack['targeted_goal_ids'] == ['G1']
        assert stack['objectives'] == [goal['executable_metric'] for goal in sheet['subgoals']]
        assert [metric['objective_id'] for metric in stack['objectives']] == ['G1', 'G2']
