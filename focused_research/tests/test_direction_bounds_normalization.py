"""Offline regression for model-declared direction windows and their known alias."""
import copy
import json
from unittest import mock

import pytest

from focused_research import direction_expansion as de
from focused_research import directional_stage as ds, search_stage as st
from focused_research.tests.test_direction_expansion import FakePlanner, fixture
from focused_research.tests.test_directional_stage import execute, setup


def test_alias_normalizes_with_audit_without_mutating_model_response():
    previous, plans, branch = fixture()
    branch.update(optional_search_bounds_nm={'d': [30., 45.]},
                  notes='This scientific explanation may be wrong.')
    payload = {'branches': [branch]}
    before = copy.deepcopy(payload)
    result = de.validate_expansion(payload, previous, plans)
    assert result['report']['accepted_count'] == 1
    assert payload == before
    assert result['jobs'][0]['search_bounds_nm'] == {'d': [30., 45.]}
    assert 'optional_search_bounds_nm' not in result['jobs'][0]
    audit = result['report']['normalization_audit'][0]
    assert audit['ignored_fields'] == ['notes']
    assert audit['search_bounds_normalization'] == {
        'source_fields': ['optional_search_bounds_nm'],
        'aliases_applied': {'optional_search_bounds_nm': 'search_bounds_nm'},
        'search_bounds_nm': {'d': [30., 45.]}}
    # Compiled jobs, audit, and raw input must not share mutable bounds lists.
    result['jobs'][0]['search_bounds_nm']['d'][0] = 35.
    assert payload == before
    assert audit['search_bounds_normalization']['search_bounds_nm']['d'][0] == 30.


@pytest.mark.parametrize('declarations,expected', [
    ({}, {}),
    ({'search_bounds_nm': {}}, {}),
    ({'optional_search_bounds_nm': {}}, {}),
    ({'search_bounds_nm': {'d': [30, 45]}}, {'d': [30, 45]}),
    ({'search_bounds_nm': {'d': [30, 45]},
      'optional_search_bounds_nm': {'d': [30., 45.]}}, {'d': [30, 45]}),
    ({'search_bounds_nm': {}, 'optional_search_bounds_nm': {}}, {}),
])
def test_canonical_omitted_and_equal_dual_bounds_have_explicit_audit(declarations, expected):
    previous, plans, branch = fixture()
    branch.update(declarations)
    result = de.validate_expansion({'branches': [branch]}, previous, plans)
    assert result['report']['accepted_count'] == 1
    assert result['jobs'][0]['search_bounds_nm'] == expected
    audit = result['report']['normalization_audit'][0]['search_bounds_normalization']
    assert audit['source_fields'] == [key for key in de.SEARCH_BOUNDS_FIELDS if key in declarations]
    assert audit['search_bounds_nm'] == expected
    assert audit['aliases_applied'] == (
        {'optional_search_bounds_nm': 'search_bounds_nm'}
        if 'optional_search_bounds_nm' in declarations else {})


@pytest.mark.parametrize('canonical,alias', [
    ({'d': [30, 45]}, {'d': [35, 45]}),
    ({'d': [30, 45]}, {'d': [30, 50]}),
    ({}, {'d': [30, 45]}),
    ({'d': [30, 45]}, {}),
])
def test_conflicting_dual_bounds_reject_only_that_branch(canonical, alias):
    previous, plans, good = fixture()
    bad = {**copy.deepcopy(good), 'branch_id': 'bad',
           'search_bounds_nm': canonical, 'optional_search_bounds_nm': alias}
    result = de.validate_expansion({'branches': [bad, good]}, previous, plans)
    assert [job['branch_id'] for job in result['jobs']] == ['b']
    assert result['report']['rejected_count'] == 1
    rejected = result['report']['rejected_branches'][0]
    assert rejected['error'] == 'conflicting search_bounds_nm and optional_search_bounds_nm'
    assert rejected['raw_branch'] == bad


@pytest.mark.parametrize('bounds', [
    None, False, [], 'narrow', {'unknown': [30, 45]},
    {'d': [30]}, {'d': [30, 45, 50]}, {'d': ['30', 45]},
    {'d': [True, 45]}, {'d': [30, float('nan')]}, {'d': [30, float('inf')]},
    {'d': [45, 30]}, {'d': [0, 45]}, {'d': [30, 101]},
    {'d': [45, 60]},  # Valid original subset, but it excludes the proposed seed.
])
def test_alias_uses_same_hard_bounds_and_seed_checks_as_canonical(bounds):
    previous, plans, good = fixture()
    for field in de.SEARCH_BOUNDS_FIELDS:
        bad = {**copy.deepcopy(good), 'branch_id': 'bad', field: bounds}
        result = de.validate_expansion({'branches': [bad, good]}, previous, plans)
        assert result['report']['rejected_count'] == 1
        assert [job['branch_id'] for job in result['jobs']] == ['b']


@pytest.mark.parametrize('invalid_field', de.SEARCH_BOUNDS_FIELDS)
@pytest.mark.parametrize('invalid', [None, False, []])
def test_present_invalid_declaration_never_falls_back_to_other_field(invalid_field, invalid):
    previous, plans, branch = fixture()
    branch.update(search_bounds_nm={'d': [30, 45]}, optional_search_bounds_nm={'d': [30, 45]})
    branch[invalid_field] = invalid
    result = de.validate_expansion({'branches': [branch]}, previous, plans)
    assert result['report']['rejected_count'] == 1
    assert result['jobs'] == []


def test_equal_python_values_do_not_hide_boolean_alias_bounds():
    previous, plans, branch = fixture()
    previous['accepted']['routes'][0]['family']['thickness_variables_nm']['d'] = [1, 100]
    branch.update(search_bounds_nm={'d': [1, 45]}, optional_search_bounds_nm={'d': [True, 45]})
    assert branch['search_bounds_nm'] == branch['optional_search_bounds_nm']
    result = de.validate_expansion({'branches': [branch]}, previous, plans)
    assert result['report']['rejected_count'] == 1
    assert 'optional_search_bounds_nm bounds must be finite' in result['report']['rejected_branches'][0]['error']


def test_layerwise_alias_uses_executable_layer_names_and_keeps_partial_overrides():
    previous, plans, branch = fixture()
    plans['plans'][0]['action'] = 'refine_layers'
    branch.update(parameterization='layerwise_refinement',
                  parameters_nm={'layer_0:d': 40, 'layer_1:d': 60},
                  optional_search_bounds_nm={'layer_0:d': [30, 45]})
    result = de.validate_expansion({'branches': [branch]}, previous, plans)
    assert result['report']['accepted_count'] == 1
    assert result['jobs'][0]['search_bounds_nm'] == {'layer_0:d': [30, 45]}
    branch['optional_search_bounds_nm'] = {'d': [30, 45]}
    assert de.validate_expansion({'branches': [branch]}, previous, plans)['report']['rejected_count'] == 1


def test_expansion_prompt_names_exact_json_key_and_persists_raw_alias(tmp_path):
    previous, plans, branch = fixture()
    branch['optional_search_bounds_nm'] = {'d': [30, 45]}
    payload = {'branches': [branch]}
    planner = FakePlanner(json.dumps(payload))
    output = tmp_path / 'expansion'
    with mock.patch.object(de.ss, 'read_search', return_value=previous):
        summary = de.expand_directions(tmp_path / 'previous', plans, output=output,
                                       planner=planner, max_forward_evaluations=16)
    assert summary['status'] == 'branches_recorded'
    assert planner.calls == 1
    prompt = planner.prompt['system_prompt']
    assert '"search_bounds_nm": {"listed_variable": [30.0, 50.0]}' in prompt
    assert '"search_bounds_nm", never "optional_search_bounds_nm"' in prompt
    assert st.load(output / 'expansion_response.json') == payload
    assert st.load(output / 'raw_response.json')['response'] == planner.response
    assert st.load(output / 'expansion_jobs.json')['jobs'][0]['search_bounds_nm'] == {'d': [30, 45]}
    audit = st.load(output / 'expansion_report.json')['normalization_audit'][0]
    assert audit['search_bounds_normalization']['source_fields'] == ['optional_search_bounds_nm']
    for name, expected in st.load(output / 'manifest.json').items():
        assert st.digest(st.load(output / name)) == expected


def test_alias_windows_reach_numerical_search_candidate_ancestry_and_feedback(tmp_path):
    _, plans, payload = setup(tmp_path)
    windows = {}
    for branch in payload['branches']:
        branch['optional_search_bounds_nm'] = branch.pop('search_bounds_nm')
        windows[branch['branch_id']] = copy.deepcopy(branch['optional_search_bounds_nm'])
    before = copy.deepcopy(payload)
    result = execute(tmp_path, plans, payload)
    assert result['status'] == 'search_complete'
    assert payload == before
    root = tmp_path / 'directional'
    assert st.load(root / 'expansion_input.json') == payload
    for worker in st.load(root / 'branch_search_results.json'):
        expected = windows[worker['branch_id']]
        assert worker['search']['search_policy']['requested_search_bounds_nm'] == expected
        assert worker['search']['search_policy']['search_bounds_nm'] == expected
        assert worker['search']['evaluations']
        for evaluation in worker['search']['evaluations']:
            for name, value in evaluation['parameters_nm'].items():
                assert expected[name][0] <= value <= expected[name][1]
    ancestry = st.load(root / 'candidate_ancestry.json')
    assert ancestry
    for attribution in ancestry.values():
        assert attribution['search_bounds_nm'] == windows[attribution['branch_id']]
    for branch in st.load(root / 'branch_feedback.json')['branches']:
        assert branch['search_bounds_nm'] == windows[branch['branch_id']]
    for audit in st.load(root / 'expansion_report.json')['normalization_audit']:
        assert audit['ignored_fields'] == []
        assert audit['search_bounds_normalization']['aliases_applied'] == {
            'optional_search_bounds_nm': 'search_bounds_nm'}


def test_alias_windows_also_guard_candidates_before_certification(tmp_path, monkeypatch):
    _, plans, payload = setup(tmp_path)
    for branch in payload['branches']:
        branch['optional_search_bounds_nm'] = branch.pop('search_bounds_nm')
    actual = ds.fo.optimize_family

    def escape_window(*args, **kwargs):
        search = actual(*args, **kwargs)
        for candidate in search['candidates']:
            for index, layer in enumerate(candidate['tmm_task']['stack']['layers']):
                if index % 2 == 0:
                    layer['thickness_nm'] = 80  # Inside original bounds, outside the alias window.
        return search

    monkeypatch.setattr(ds.fo, 'optimize_family', escape_window)
    result = execute(tmp_path, plans, payload)
    assert result['status'] == 'partial'
    assert result['candidate_count'] == 0
    assert result['metering']['certification_new_evaluations'] == 0
    failures = st.load(tmp_path / 'directional' / 'input_failures.json')
    assert failures and all('branch search bounds' in failure['error'] for failure in failures)
