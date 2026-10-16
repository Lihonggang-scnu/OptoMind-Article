"""Offline permission/capability contracts. No model, network or solver calls."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import research_routes as rr
from material_capabilities import CATEGORY, validate_material_capabilities


def fixture(category=True):
    def read(name):
        return json.loads((ROOT / 'examples' / name).read_text())
    routes = read('research_routes_example.json')
    sheet = read('rgb_nir_task_sheet.json')
    evidence = read('research_routes_evidence_fixture.json')
    constraints = read('research_routes_constraints.json')
    if category:
        old, new = '仅使用 TiO2 和 SiO2', '允许普通可沉积无机介质材料，禁止金属层'
        sheet['original_question'] = sheet['original_question'].replace(old, new)
        material_constraint = next(c for c in sheet['explicit_constraints'] if c['id'] == 'C5')
        material_constraint.update(constraint='Ordinary depositable inorganic dielectrics; no metal layers', original_span=new)
        constraints.update(allowed_materials=None, allowed_material_category=CATEGORY,
            execution_materials={name: {'material': name.lower(), 'provider': 'local_csv',
                                       'dataset_id': name.lower()} for name in ('TiO2', 'SiO2', 'Al2O3')})
        constraints['constraint_source_ids'].update(allowed_materials=[], allowed_material_category=['C5'])
        constraints['assumptions']['execution_materials'] = (
            'Current local execution inventory only; not an exhaustive user material whitelist. '
            'Record provenance and full-grid n/k coverage require separate local preflight.')
    return routes, sheet, evidence, constraints


def check(values):
    routes, sheet, evidence, constraints = values
    return rr.validate(routes, sheet, evidence, design_constraints=constraints)


def test_category_inventory_is_ready_without_narrowing_original_permission():
    values = fixture()
    before = copy.deepcopy(values)
    routes, sheet, evidence, constraints = values
    check(values)
    assert rr.revision_reasons(routes['routes'][0], sheet, constraints) == []
    instances = rr.materialize(routes, 'R1', sheet, evidence, design_constraints=constraints)
    assert instances[0]['design_constraints']['allowed_materials'] is None
    assert instances[0]['design_constraints']['execution_materials'] == constraints['execution_materials']
    assert instances[0]['task_explicit_constraints'] == sheet['explicit_constraints']
    assert values == before


def test_supported_additional_material_is_not_excluded_by_old_example_whitelist():
    values = fixture()
    values[0]['routes'][0]['family']['layers'][0]['material'] = 'Al2O3'
    check(values)


def test_unavailable_dielectric_is_an_inventory_limit_not_user_prohibition():
    values = fixture()
    values[0]['routes'][0]['family']['layers'][0]['material'] = 'HfO2'
    with pytest.raises(ValueError, match='execution inventory; broad user category is unchanged'):
        check(values)


def test_expanding_execution_support_never_rewrites_user_permission():
    values = fixture()
    original_sheet = copy.deepcopy(values[1])
    values[0]['routes'][0]['family']['layers'][0]['material'] = 'HfO2'
    values[3]['execution_materials']['HfO2'] = {
        'material': 'hfo2', 'provider': 'local_csv', 'dataset_id': 'hfo2'}
    check(values)
    assert values[1] == original_sheet
    assert values[3]['allowed_materials'] is None
    assert values[3]['allowed_material_category'] == CATEGORY


@pytest.mark.parametrize('name', ['Au', 'Al', 'Cr', 'PMMA', 'invented_material'])
def test_category_membership_does_not_depend_on_model_assertion(name):
    values = fixture()
    values[3]['execution_materials'][name] = {
        'material': name, 'provider': 'local_csv', 'dataset_id': name.lower()}
    with pytest.raises(ValueError, match='reviewed canonical dielectric'):
        check(values)


@pytest.mark.parametrize('change', [
    lambda c: c.update(allowed_materials=['TiO2', 'SiO2']),
    lambda c: c.update(allowed_material_category='any_dielectric'),
    lambda c: c.update(execution_materials=[]),
    lambda c: c.update(execution_materials={}),
    lambda c: c['execution_materials']['TiO2'].update(material='Au'),
    lambda c: c['execution_materials']['TiO2'].update(dataset_id='ag'),
    lambda c: c['execution_materials']['TiO2'].update(provider='invented'),
    lambda c: c['execution_materials']['TiO2'].update(dataset_id=True),
    lambda c: c['execution_materials']['TiO2'].update(constant_n=2.4),
    lambda c: c['constraint_source_ids'].update(allowed_material_category=[]),
    lambda c: c['constraint_source_ids'].update(allowed_material_category=['C999']),
    lambda c: c['constraint_source_ids'].update(allowed_materials=['C5']),
    lambda c: c['constraint_source_ids'].update(execution_materials=['C5']),
    lambda c: c['assumptions'].pop('execution_materials'),
    lambda c: c.pop('execution_materials'),
])
def test_invalid_or_misattributed_inventory_fails_closed(change):
    values = fixture()
    change(values[3])
    with pytest.raises(ValueError):
        check(values)


def test_missing_inventory_is_preserved_as_unexecutable_not_missing_permission():
    routes, sheet, evidence, constraints = values = fixture()
    constraints['execution_materials'] = None
    routes['routes'][0]['readiness'] = 'needs_revision'
    check(values)
    assert rr.revision_reasons(routes['routes'][0], sheet, constraints) == ['missing configuration: execution_materials']
    with pytest.raises(ValueError, match='materialization blocked'):
        rr.materialize(routes, 'R1', sheet, evidence, design_constraints=constraints)


def test_missing_inventory_does_not_disable_no_metal_gate():
    values = fixture()
    values[3]['execution_materials'] = None
    values[0]['routes'][0]['readiness'] = 'needs_revision'
    values[0]['routes'][0]['family']['layers'][0]['material'] = 'Au'
    with pytest.raises(ValueError, match='reviewed dielectric category membership'):
        check(values)


def test_legacy_finite_contract_and_missing_permission_are_unchanged():
    routes, sheet, evidence, constraints = values = fixture(category=False)
    check(values)
    assert validate_material_capabilities(constraints) is None
    constraints['allowed_materials'] = None
    routes['routes'][0]['readiness'] = 'needs_revision'
    check(values)
    assert rr.revision_reasons(routes['routes'][0], sheet, constraints) == ['missing configuration: allowed_materials']


def test_prompt_exposes_inventory_and_distinction_instead_of_assuming_model_knows():
    _, sheet, evidence, constraints = fixture()
    prompt = rr.build_prompt(sheet, evidence, design_constraints=constraints)
    assert prompt['user_payload']['design_constraints'] == constraints
    assert 'never infer missing local inventory' in prompt['system_prompt']
    assert 'This finite inventory is not an exhaustive description of what the user permits' in prompt['system_prompt']


def test_candidate_mode_also_preserves_permission_and_enforces_inventory():
    routes, sheet, evidence, constraints = fixture()
    compiled = rr.compile_candidates(routes, sheet, evidence, design_constraints=constraints)
    assert compiled['candidate_report']['executable_route_ids'] == ['R1']
    routes['routes'][0]['family']['layers'][0]['material'] = 'HfO2'
    compiled = rr.compile_candidates(routes, sheet, evidence, design_constraints=constraints)
    assert compiled['candidate_report']['executable_route_ids'] == []
    assert compiled['candidate_report']['rejected_route_ids'] == ['R1']
