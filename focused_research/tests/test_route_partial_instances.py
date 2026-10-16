"""Candidate-only tolerance never changes physical inputs or scientific claims."""
import copy
import pytest
from test_research_routes import fixture
import research_routes as rr


def compile_values(values):
    return rr.compile_candidates(*values[:3], design_constraints=values[3])


def test_empty_compatibility_and_open_questions_do_not_block_candidates():
    values = fixture()
    route = values[0]['routes'][0]
    route['compatibility_conditions'] = []
    assert compile_values(values)['candidate_report']['executable_route_ids'] == ['R1']
    route['open_requirements'] = ['Verify material absorption before execution.']
    assert compile_values(values)['candidate_report']['executable_route_ids'] == ['R1']
    route['open_requirements'] = []
    route['compatibility_conditions'] = copy.deepcopy(fixture()[0]['routes'][0]['compatibility_conditions'])
    route['compatibility_conditions'][0]['required_value'] = None
    assert compile_values(values)['candidate_report']['needs_revision_route_ids'] == ['R1']


@pytest.mark.parametrize('mode', ['model_knowledge', 'external_evidence'])
def test_basis_is_typed_audited_not_scientifically_verified(mode):
    values = fixture()
    route = values[0]['routes'][0]
    route.update(basis_mode=mode, model_knowledge_rationale='Unverified model hypothesis.')
    before = copy.deepcopy(values)
    result = compile_values(values)
    assert values == before
    assert result['raw_research_routes'] == before[0]
    accepted = result['accepted_contract']['routes'][0]
    assert accepted['basis_mode'] == 'hybrid'
    assert accepted['model_knowledge_rationale'] == route['model_knowledge_rationale']
    info = result['candidate_report']['routes']['R1']
    assert info['model_knowledge_status'] == 'unverified'
    assert any(w['code'] == 'basis_mode_derived' for w in info['warnings'])
    assert all(c['semantic_support'] == 'not_verified' for c in result['candidate_report']['clues'].values())
    route['model_knowledge_rationale'] = {'not': 'text'}
    assert compile_values(values)['candidate_report']['rejected_route_ids'] == ['R1']


def test_bad_instance_is_quarantined_not_clipped_and_valid_seed_materializes():
    values = fixture()
    raw, sheet, evidence, constraints = values
    route = raw['routes'][0]
    route['instances'][1]['parameters_nm']['dH'] = 999
    before = copy.deepcopy(values)
    result = compile_values(values)
    assert values == before
    report = result['candidate_report']
    assert report['executable_route_ids'] == ['R1']
    info = report['routes']['R1']
    assert info['candidate_role'] == 'reference_seed_only'
    assert len(info['rejected_instances']) == 1
    assert info['rejected_instances'][0]['original'] == route['instances'][1]
    assert 'outside family range' in info['rejected_instances'][0]['reason']
    assert len(info['deferred_expectations']) == len(route['expectations'])
    accepted = result['accepted_contract']['routes'][0]
    assert accepted['family'] == route['family']
    assert accepted['instances'] == [route['instances'][0]]
    assert accepted['expectations'] == []
    prepared = rr.materialize(result['accepted_contract'], 'R1', sheet, evidence,
        design_constraints=constraints, candidate_mode=True, candidate_report=report)
    assert len(prepared) == 1
    assert prepared[0]['objectives'] == [g['executable_metric'] for g in sheet['subgoals']]
    assert prepared[0]['candidate_metadata']['candidate_role'] == 'reference_seed_only'
    assert prepared[0]['proposal_derivation_audit']['rejected_instances'] == info['rejected_instances']
    assert compile_values((result['accepted_contract'], sheet, evidence, constraints))['accepted_contract'] == result['accepted_contract']


@pytest.mark.parametrize('mutation', ['metric', 'unknown_instance', 'duplicate_instance', 'bad_family', 'unknown_clue'])
def test_quarantine_cannot_hide_hard_errors(mutation):
    values = fixture()
    route = values[0]['routes'][0]
    route['instances'][1]['parameters_nm']['dH'] = 999
    if mutation == 'metric':
        route['expectations'][0]['metric']['sense'] = 'minimize'
    elif mutation == 'unknown_instance':
        route['expectations'][0]['candidate_instance_id'] = 'invented'
    elif mutation == 'duplicate_instance':
        route['instances'][1]['id'] = route['instances'][0]['id']
    elif mutation == 'bad_family':
        route['family']['layers'][0]['material'] = 'Au'
    else:
        route['clue_ids'] = ['invented']
    assert compile_values(values)['candidate_report']['rejected_route_ids'] == ['R1']


def test_identical_names_do_not_establish_material_or_layer_count_intervention():
    values = fixture()
    route = values[0]['routes'][0]
    route['instances'][1]['parameters_nm'] = copy.deepcopy(route['instances'][0]['parameters_nm'])
    result = compile_values(values)
    info = result['candidate_report']['routes']['R1']
    assert info['candidate_role'] == 'reference_seed_only'
    assert all(c['classification'] == 'baseline_reference' and not c['layer_count_changed']
               and not c['material_order_changed'] for c in info['comparisons'])


def test_no_legal_instance_remains_nonexecutable():
    values = fixture()
    for instance in values[0]['routes'][0]['instances']:
        instance['parameters_nm']['dH'] = 999
    result = compile_values(values)
    assert result['candidate_report']['executable_route_ids'] == []
    assert len(result['candidate_report']['routes']['R1']['rejected_instances']) == 2


def test_candidate_clue_goal_relevance_is_warning_not_fabricated_support():
    values = fixture()
    route = values[0]['routes'][0]
    clue = values[0]['decision_clues'][0]
    route['goal_ids'] = [route['expectations'][0]['goal_id']]
    route['expectations'] = [e for e in route['expectations'] if e['goal_id'] in route['goal_ids']]
    route['compatibility_conditions'] = [c for c in route['compatibility_conditions'] if c['goal_id'] in route['goal_ids']]
    clue['goal_ids'] = [g['id'] for g in values[1]['subgoals'] if g['id'] not in route['goal_ids']]
    before = copy.deepcopy(values)
    result = compile_values(values)
    assert result['candidate_report']['executable_route_ids'] == ['R1']
    assert values == before
    assert result['accepted_contract']['routes'][0]['goal_ids'] == route['goal_ids']
    assert result['accepted_contract']['decision_clues'][0]['goal_ids'] == clue['goal_ids']
    warnings = result['candidate_report']['routes']['R1']['warnings']
    assert any(w['code'] == 'clue_goal_relevance_unverified' and w['semantic_support'] == 'not_verified' for w in warnings)
    with pytest.raises(ValueError, match='each clue must relate'):
        rr.validate(*values[:3], design_constraints=values[3])
    clue['evidence_id'] = 'unknown_source'
    assert compile_values(values)['candidate_report']['rejected_route_ids'] == ['R1']


def test_saved_t038_replay_when_supplied():
    import os
    from pathlib import Path
    from replay_t038_route_admission import replay
    directory = os.environ.get('T038_ROUTE_INPUT_DIR')
    if not directory:
        pytest.skip('Set T038_ROUTE_INPUT_DIR to immutable saved T038 route exports.')
    result = replay(Path(directory))
    assert result['input_byte_sha256'] == {
        'research_routes.raw.json': '728354d9f919dba53a997bf6f5df1dd30c56eb1b00f2d9bb5f674f28aa11fd75',
        'task_sheet.json': 'e1859acb6ac1b936f47519171de48fa60817f47a259ff78a5d3a7f8c17581395',
        'evidence_items.json': '6e1a08aa10774c32aaff14a02da05dcecf1bcbf4c11e2bf43d1f93b4a6997606',
        'design_constraints.json': '67a602f85043ab7ab75f7c860482d55e203edfaf9bd564ac8a4396b3129c474e'}
    report = result['candidate_report']
    assert report['accepted_route_ids'] == ['R' + str(i) for i in range(1, 11)]
    assert report['executable_route_ids'] == ['R' + str(i) for i in range(1, 11)]
    assert report['needs_revision_route_ids'] == []
    assert len(result['prepared_instances']) == 18
    assert sum(len(r['instances']) for r in result['accepted_contract']['routes']) == 18
    assert sum(len(r['rejected_instances']) for r in report['routes'].values()) == 2
    assert report['routes']['R10']['candidate_role'] == 'reference_seed_only'
    assert any(w['code'] == 'clue_goal_relevance_unverified' for w in report['routes']['R10']['warnings'])
    assert result['model_calls'] == result['solver_calls'] == result['network_calls'] == 0
