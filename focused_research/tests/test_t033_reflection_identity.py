"""Offline identity/action regressions; no planner client or solver is constructed."""
import copy
import json

from focused_research import directional_reflection as dr, search_stage as st
from focused_research.tests.test_decision_context import realistic_previous


def previous_fixture():
    previous, _ = realistic_previous(3)
    previous['manifest_sha256'] = 'current-snapshot'
    first = next(iter(previous['archive']['designs'].values()))
    family = first['family']['specification']['family']
    previous['accepted']['routes'][0]['family'] = copy.deepcopy(family)
    for identity, design in previous['archive']['designs'].items():
        summary = previous['envelopes'][identity]
        previous['envelopes'][identity] = {
            'route_id': 'R1', 'tmm_task': copy.deepcopy(design['physical_input']),
            'numerical_search': {'local_derivative_summary': summary}}
    return previous


def feedback_fixture(previous, old_parent, candidate):
    metrics = {'G1': .912930, 'G2': .793403}
    return {'schema_version': 'focused_research.branch_feedback.v1',
        'objectives': previous['archive']['objectives'],
        'plans': [{'plan_index': 0, 'original_coarse_plan': {
            'parent_design_id': old_parent, 'action': 'refine_layers',
            'diagnosis': {'diagnosis_id': previous['diagnostics'][1]['diagnosis_id'],
                'observation_ids': ['goal:G1', 'goal:G2'],
                'explanation': 'P001 and D002 may support a speculative mechanism; both improve.',
                'verification': 'verified_references'},
            'expected_goal_changes': {'G1': 'increase', 'G2': 'increase'}}}],
        'branches': [{'branch_id': 'branch-a', 'plan_index': 0,
            'parent_design_id': old_parent, 'parameterization': 'layerwise_refinement',
            'parent_metrics': {'G1': .9, 'G2': .8},
            'rationale': 'Explore P001 despite uncertainty; diagnosis:deadbeef must stay intact.',
            'parameters_nm': {'layer_0:dH': 51.}, 'search_bounds_nm': {},
            'outcome_counts_unique_certified_designs': {'all_improve': 0, 'tradeoff': 1},
            'selected_certified_designs': [{'design_id': candidate, 'metrics': metrics,
                'deltas_from_parent': {'G1': .012930, 'G2': -.006597}, 'outcome': 'tradeoff'}]}]}


def test_archive_growth_cannot_rebind_historical_labels_in_feedback():
    previous = previous_fixture()
    old = st._aliases(previous)
    parent, candidate = old['parents']['P001'], old['parents']['P002']
    # A new hash sorting before all older designs shifts every ordinal alias.
    inserted = 'design:-new-earlier-sorting-hash'
    previous['archive']['designs'][inserted] = copy.deepcopy(previous['archive']['designs'][parent])
    previous['envelopes'][inserted] = copy.deepcopy(previous['envelopes'][parent])
    current = st._aliases(previous)
    assert current['parents']['P001'] != old['parents']['P001']
    feedback = feedback_fixture(previous, parent, candidate)
    original = copy.deepcopy(feedback)
    projected = dr._feedback_decision_view(feedback, previous, current)
    assert feedback == original
    plan = projected['plans'][0]
    assert 'original_coarse_plan' not in plan
    assert 'historical[P001]' in plan['historical_model_plan']['diagnosis']['explanation']
    assert 'historical[D002]' in plan['historical_model_plan']['diagnosis']['explanation']
    assert 'speculative mechanism; both improve' in plan['historical_model_plan']['diagnosis']['explanation']
    assert plan['historical_model_plan']['parent_design_id'] == parent
    assert plan['current_parent_bindings'] == [{'design_id': parent, 'parent_alias': 'P002'}]
    branch = projected['branches'][0]
    assert branch['current_parent_alias'] == 'P002'
    assert 'historical[P001]' in branch['historical_model_rationale']
    assert 'diagnosis:deadbeef' in branch['historical_model_rationale']
    outcome = branch['selected_certified_designs'][0]
    assert outcome['current_design_alias'] == 'P003'
    assert outcome['comparison_baseline_design_id'] == parent
    assert outcome['current_diagnostic_references'][0]['design_id'] == candidate
    assert outcome['deltas_from_parent'] == {'G1': .012930, 'G2': -.006597}
    assert outcome['outcome'] == 'tradeoff'
    assert branch['outcome_counts_unique_certified_designs']['all_improve'] == 0
    assert projected['identity_scope']['snapshot_manifest_sha256'] == 'current-snapshot'
    assert projected['identity_scope']['historical_labels'] == ['D002', 'P001']


def test_current_diagnostic_endpoint_ids_and_snapshot_are_explicit():
    previous = previous_fixture()
    before = copy.deepcopy(previous)
    view = st._revision_decision_view(previous, st._aliases(previous))
    payload = view['user_payload']
    assert previous == before
    assert payload['identity_scope']['snapshot_manifest_sha256'] == 'current-snapshot'
    by_id = {row['diagnosis_id']: row for row in previous['diagnostics']}
    for field in ('diagnostic_index', 'physics_diagnostics'):
        for row in payload[field]:
            assert row['design_id'] == by_id[row['diagnosis_id']]['design_id']
            assert row['comparison_baseline_design_id'] == by_id[row['diagnosis_id']]['comparison_baseline_design_id']
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    assert view['selection_report']['payload_utf8_bytes'] == len(encoded.encode('utf-8'))


def test_actual_layer_ties_control_advertised_and_compiled_actions():
    previous = previous_fixture()
    aliases = st._aliases(previous)
    tied, untied = aliases['parents']['P001'], aliases['parents']['P002']
    # Historical creation metadata is not the executable state of this parent.
    previous['archive']['designs'][tied]['family']['specification']['parameterization'] = 'layerwise_refinement'
    previous['archive']['designs'][untied]['family']['specification']['parameterization'] = 'family'
    previous['envelopes'][untied]['tmm_task']['stack']['layers'][2]['thickness_nm'] += 1.
    view = st._revision_decision_view(previous, aliases)['user_payload']
    rows = {row['design_id']: row for row in view['certified_designs']}
    assert rows[tied]['available_actions'] == ['refine_family', 'refine_layers']
    assert rows[untied]['available_actions'] == ['refine_layers']
    blocked_reason = rows[untied]['unavailable_actions']['refine_family']
    plans = {'plans': [{'parent_design_id': untied, 'action': action,
                       'diagnosis': 'This speculative interpretation can be mistaken.'}
                      for action in ('refine_family', 'refine_layers')]}
    before = copy.deepcopy(plans)
    compiled, report = st._compile_plans(plans, previous)
    assert plans == before
    assert report['accepted_count'] == report['rejected_count'] == 1
    assert report['rejected_plans'][0]['error'] == blocked_reason
    assert compiled['plans'][0]['action'] == 'refine_layers'
    assert compiled['plans'][0]['parent_design_id'] == untied
    assert compiled['plans'][0]['diagnosis']['verification'] == 'unverified_reasoning'


def test_namespace_replacement_is_token_bounded_and_never_changes_structured_ids():
    labels = set()
    text = dr._historical_text('P004. D133, P1234 AP004 P004suffix key:P004 layer_P004 design:abc.P004 P004.suffix', labels)
    assert text == ('historical[P004]. historical[D133], historical[P1234] AP004 P004suffix '
                    'key:P004 layer_P004 design:abc.P004 P004.suffix')
    assert labels == {'P004', 'D133', 'P1234'}
    previous = previous_fixture()
    aliases = st._aliases(previous)
    parent, candidate = aliases['parents']['P001'], aliases['parents']['P002']
    source = feedback_fixture(previous, parent, candidate)
    source['plans'][0]['original_coarse_plan']['diagnosis']['observation_ids'] = ['P004']
    source['branches'][0]['parameters_nm']['P004'] = 12.
    view = dr._feedback_decision_view(source, previous, aliases)
    assert view['plans'][0]['historical_model_plan']['diagnosis']['observation_ids'] == ['P004']
    assert view['branches'][0]['parameters_nm']['P004'] == 12.
