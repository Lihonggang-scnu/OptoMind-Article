"""Exploratory admission preserves open questions without bypassing hard gates."""
import copy
import pytest
from test_research_routes import fixture
import research_routes as rr


def test_candidate_materializes_original_structure_with_unresolved_risk_and_strict_still_blocks():
    raw, sheet, evidence, constraints = fixture()
    route = raw['routes'][0]
    route.update(open_requirements=['Verify absorption before execution.', 'Define a better comparison.'],
                 readiness='needs_revision')
    before = copy.deepcopy(raw)
    rr.validate(raw, sheet, evidence, design_constraints=constraints)
    with pytest.raises(ValueError, match='materialization blocked'):
        rr.materialize(raw, 'R1', sheet, evidence, design_constraints=constraints)
    compiled = rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)
    assert compiled['candidate_report']['executable_route_ids'] == ['R1']
    accepted = compiled['accepted_contract']
    assert accepted['routes'][0]['open_requirements'] == route['open_requirements']
    for source in [raw, accepted]:
        prepared = rr.materialize(source, 'R1', sheet, evidence,
                                  design_constraints=constraints, candidate_mode=True)
        assert len(prepared) == len(route['instances'])
        for item, instance in zip(prepared, route['instances']):
            assert item['objectives'] == [g['executable_metric'] for g in sheet['subgoals']]
            assert item['layers'] == [{'material': row['material'], 'thickness_nm':
                instance['parameters_nm'][row['thickness_nm']] if isinstance(row['thickness_nm'], str)
                else row['thickness_nm']} for row in route['family']['layers']]
            warnings = [w for w in item['candidate_metadata']['warnings']
                        if w['code'] == 'open_requirement_unresolved']
            assert [w['original'] for w in warnings] == route['open_requirements']
            assert all(w['status'] == 'unresolved' for w in warnings)
            assert item['execution_authorized'] is False
    assert raw == before
    with pytest.raises(ValueError, match='must be needs_revision'):
        rr.validate(accepted, sheet, evidence, design_constraints=constraints)


@pytest.mark.parametrize('problem', ['missing_family', 'missing_instances', 'bad_range',
    'unknown_material', 'unknown_goal', 'wrong_metric', 'unknown_source', 'conflicting_condition'])
def test_open_questions_cannot_hide_execution_defects(problem):
    raw, sheet, evidence, constraints = fixture()
    r = raw['routes'][0]
    r['open_requirements'] = ['Everything can be tested later.']
    if problem == 'missing_family':
        r['family'] = None
    elif problem == 'missing_instances':
        r['instances'] = []
    elif problem == 'bad_range':
        for i in r['instances']:
            i['parameters_nm']['dH'] = 999
    elif problem == 'unknown_material':
        r['family']['layers'][0]['material'] = 'invented'
    elif problem == 'unknown_goal':
        r['goal_ids'] = ['invented']
    elif problem == 'wrong_metric':
        r['expectations'][0]['metric']['sense'] = 'minimize'
    elif problem == 'unknown_source':
        r['clue_ids'] = ['invented']
    else:
        r['compatibility_conditions'][0]['required_value'] = None
    result = rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)
    assert result['candidate_report']['executable_route_ids'] == []


def test_identical_instances_remain_reference_even_when_open_comparison_unblocks():
    raw, sheet, evidence, constraints = fixture()
    r = raw['routes'][0]
    r['open_requirements'] = ['Need a distinct material-swap comparison.']
    r['instances'][1]['parameters_nm'] = copy.deepcopy(r['instances'][0]['parameters_nm'])
    compiled = rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)
    info = compiled['candidate_report']['routes']['R1']
    assert info['readiness'] == 'ready'
    assert info['candidate_role'] == 'reference_seed_only'
    assert all(c['classification'] == 'baseline_reference' and
               c['physical_prediction_status'] == 'untested' and
               not c['material_order_changed'] and not c['layer_count_changed']
               for c in info['comparisons'])
    assert len(rr.materialize(raw, 'R1', sheet, evidence,
               design_constraints=constraints, candidate_mode=True)) == 2
