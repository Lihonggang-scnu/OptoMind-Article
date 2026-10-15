"""Synthetic offline recipe diagnostics; no model or optical calculation."""
import copy
import json
import sys
from pathlib import Path

import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from route_quality import quality_report, contract_change_audit
import research_routes as rr


def fixture():
    return json.loads((ROOT / 'examples/research_routes_example.json').read_text())


def pair():
    data = fixture()
    other = copy.deepcopy(data['routes'][0])
    other['id'] = 'R2'
    data['routes'].append(other)
    return data, other


def test_exact_comparison_despite_names_sources_and_prose():
    data, other = pair()
    other.update(basis_mode='model_knowledge', claim='Another narrative', clue_ids=[], evidence_ids=[])
    other['instances'][0]['id'] = 'renamed'
    for e in other['expectations']:
        e['baseline_instance_id'] = 'renamed'
    before = copy.deepcopy(data)
    report = quality_report(data)
    assert data == before
    assert report['similar_route_pairs'][0]['shared_comparisons'][0]['classification'] == 'identical_concrete_comparison'
    assert report['routes_removed'] == [] and not report['blocking']
    assert report['scientific_review']['status'] == 'not_performed'


def test_parameter_variants_ignore_variable_names():
    data, other = pair()
    for layer in other['family']['layers']:
        if layer['thickness_nm'] == 'dH':
            layer['thickness_nm'] = 'high'
    other['family']['thickness_variables_nm']['high'] = other['family']['thickness_variables_nm'].pop('dH')
    for instance in other['instances']:
        instance['parameters_nm']['high'] = instance['parameters_nm'].pop('dH') + 2
    for expectation in other['expectations']:
        expectation['changed_variables'] = ['high']
    report = quality_report(data)
    assert report['similar_route_pairs'][0]['shared_comparisons'][0]['classification'] == 'same_structure_intervention_variant'


@pytest.mark.parametrize('change', ['termination', 'layer_roles', 'metric'])
def test_different_executable_intervention_not_collapsed(change):
    data, other = pair()
    if change == 'termination':
        other['family']['layers'][-1]['material'] = 'TiO2'
    elif change == 'layer_roles':
        other['family']['layers'][0]['thickness_nm'] = 60
    else:
        for e in other['expectations']:
            e['metric']['region']['angle_deg'] = 30
    assert quality_report(data)['similar_route_pairs'] == []


def test_incomplete_routes_are_retained():
    data = fixture()
    data['routes'][0].update(family=None, instances=[], expectations=[])
    assert quality_report(data)['comparison_signatures'] == {'R1': []}


def test_changed_variable_empty_error_identifies_actual_problem():
    data = fixture()
    route = data['routes'][0]
    route['instances'][1]['parameters_nm'] = copy.deepcopy(route['instances'][0]['parameters_nm'])
    read = lambda name: json.loads((ROOT / 'examples' / name).read_text())
    with pytest.raises(ValueError, match='actual changed variables are empty'):
        rr.validate(data, read('rgb_nir_task_sheet.json'), read('research_routes_evidence_fixture.json'),
                    design_constraints=read('research_routes_constraints.json'))


def test_audit_records_content_changes_without_copying_text():
    before = fixture()
    after = copy.deepcopy(before)
    after['routes'][0]['claim'] = 'Replacement scientific claim'
    after['routes'][0]['instances'][1]['parameters_nm']['dH'] += 1
    audit = contract_change_audit(before, after)
    assert audit['classification'] == 'contract_repair_not_format_only'
    assert '/routes/0/claim' in audit['changed_json_paths']
    assert '/routes/0/instances/1/parameters_nm/dH' in audit['changed_json_paths']
    assert 'Replacement scientific claim' not in json.dumps(audit)
    assert not contract_change_audit(before, before)['parsed_contract_changed']


def test_multi_variable_control_remains_allowed():
    data = fixture()
    route = data['routes'][0]
    route['instances'][1]['parameters_nm']['dL'] += 1
    for e in route['expectations']:
        e['changed_variables'] = ['dH', 'dL']
    read = lambda name: json.loads((ROOT / 'examples' / name).read_text())
    assert rr.validate(data, read('rgb_nir_task_sheet.json'), read('research_routes_evidence_fixture.json'),
                       design_constraints=read('research_routes_constraints.json')) is data


def test_prompt_explains_deduplicated_evidence_without_new_citation_ids():
    prompt = (ROOT / 'research_routes_prompt.txt').read_text()
    assert 'source_ref resolves into the supplied sources table' in prompt
    assert 'snapshot_ref resolves into the snapshots table' in prompt
    assert "Cite the item's evidence_id" in prompt
    assert 'does not remove those restrictions' in prompt
