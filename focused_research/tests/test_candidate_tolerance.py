"""Offline candidate admission, provenance and partial-failure regressions."""
import copy
import json
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from replay_t025_candidate_semantics import synthetic_fixture, replay
import research_routes as rr


def compile_fixture(values):
    raw, sheet, evidence, constraints = values
    return rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)


def test_synthetic_t025_replay_retains_independent_routes_without_rewriting_history():
    result = replay()
    report = result['candidate_report']
    assert report['accepted_route_ids'] == ['candidate_1', 'candidate_2', 'candidate_4', 'candidate_5',
        'candidate_6', 'candidate_7', 'candidate_11', 'candidate_12']
    assert report['rejected_route_ids'] == ['candidate_3', 'candidate_8', 'candidate_9', 'candidate_10']
    assert len(result['prepared_instances']) == 16
    assert result['historical_T025_acceptance'].startswith('unchanged: zero')
    assert result['model_calls'] == result['solver_calls'] == result['network_calls'] == 0
    assert all(row['scientific_status'] == 'untested' for row in report['routes'].values())


def test_raw_preserved_and_no_physical_design_or_prose_is_repaired():
    values = synthetic_fixture()
    before = copy.deepcopy(values)
    result = compile_fixture(values)
    assert values == before
    assert result['raw_research_routes'] == before[0]
    originals = {r['id']: r for r in before[0]['routes']}
    for route in result['accepted_contract']['routes']:
        original = originals[route['id']]
        for key in ['family', 'instances', 'claim', 'basis_mode', 'model_knowledge_rationale']:
            assert route[key] == original[key]
        for actual, declared in zip(route['expectations'], original['expectations']):
            assert {k: v for k, v in actual.items() if k != 'changed_variables'} == {
                k: v for k, v in declared.items() if k != 'changed_variables'}


def test_quote_mismatch_is_never_verified_and_exact_quote_roundtrips():
    raw, sheet, evidence, constraints = synthetic_fixture()
    evidence['items'][0]['text'] = 'é 中🙂\n' + evidence['items'][0]['text']
    result = compile_fixture((raw, sheet, evidence, constraints))
    report = result['candidate_report']
    assert report['clues']['paraphrase']['source_confidence'] == 'unverified_paraphrase_or_inference'
    assert report['clues']['paraphrase']['quote_verified'] is False
    assert report['clues']['paraphrase']['locator'] is None
    locator = report['clues']['D1']['locator']
    assert evidence['items'][0]['text'][locator['start']:locator['end']] == raw['decision_clues'][0]['source_quote']
    assert locator['normalization'] == 'none'
    assert locator['offset_unit'] == 'unicode_code_points'


def test_actual_diff_and_citation_set_are_derived_with_warnings():
    result = replay()
    report = result['candidate_report']
    assert report['routes']['candidate_4']['warnings'][0]['code'] == 'evidence_ids_derived'
    assert report['routes']['candidate_5']['comparisons'][0]['actual_changed_variables'] == ['dH']
    assert any(w['code'] == 'changed_variables_derived' for w in report['routes']['candidate_5']['warnings'])
    assert all(c['classification'] == 'baseline_reference' for c in report['routes']['candidate_6']['comparisons'])
    assert all(c['layer_count_changed'] is False for r in report['routes'].values() for c in r['comparisons'])
    prepared = next(i for i in result['prepared_instances'] if i['route_id'] == 'candidate_5')
    assert prepared['proposal_derivation_audit']['warnings'] == report['routes']['candidate_5']['warnings']
    assert prepared['proposal_derivation_audit']['status'] == 'caller_supplied_history_not_authoritative'
    assert prepared['candidate_metadata']['comparisons'] == report['routes']['candidate_5']['comparisons']


@pytest.mark.parametrize('entity', ['routes', 'decision_clues'])
def test_duplicate_global_ids_are_global_blockers(entity):
    values = synthetic_fixture()
    values[0][entity].append(copy.deepcopy(values[0][entity][0]))
    with pytest.raises(ValueError, match='duplicate ID'):
        compile_fixture(values)


def test_missing_or_null_quotation_is_allowed_only_in_candidate_api():
    values = synthetic_fixture()
    values[0]['decision_clues'][0]['source_quote'] = None
    result = compile_fixture(values)
    assert 'candidate_1' in result['candidate_report']['accepted_route_ids']
    with pytest.raises(ValueError):
        rr.validate(*values[:3], design_constraints=values[3])


def test_report_must_bind_to_accepted_contract():
    raw, sheet, evidence, constraints = synthetic_fixture()
    result = rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)
    changed = copy.deepcopy(result['accepted_contract'])
    changed['routes'][0]['claim'] += ' Changed.'
    with pytest.raises(ValueError, match='must bind'):
        rr.materialize(changed, 'candidate_1', sheet, evidence, design_constraints=constraints,
            candidate_mode=True, candidate_report=result['candidate_report'])


def test_stale_source_confidence_cannot_be_reused_after_source_text_changes():
    raw, sheet, evidence, constraints = synthetic_fixture()
    result = rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)
    evidence['items'][0]['text'] = 'A changed source no longer containing the original quote.'
    with pytest.raises(ValueError, match='must bind'):
        rr.materialize(result['accepted_contract'], 'candidate_1', sheet, evidence,
            design_constraints=constraints, candidate_mode=True, candidate_report=result['candidate_report'])


@pytest.mark.parametrize('quote', ['', '  unverified wording  ', None])
def test_absent_quote_does_not_become_an_eligibility_gate(quote):
    values = synthetic_fixture()
    values[0]['decision_clues'][0]['source_quote'] = quote
    result = compile_fixture(values)
    assert 'candidate_1' in result['candidate_report']['accepted_route_ids']
    assert result['candidate_report']['clues']['D1']['quote_verified'] is False


def test_validate_and_materialize_stage_use_only_accepted_subset(tmp_path):
    import route_stage
    raw, sheet, evidence, constraints = synthetic_fixture()
    paths = {}
    for name, value in [('routes', raw), ('task-sheet', sheet), ('evidence-file', evidence), ('constraints-file', constraints)]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value))
        paths[name] = path
    arguments = []
    for name in ['task-sheet', 'evidence-file', 'constraints-file']:
        arguments += ['--' + name, str(paths[name])]
    arguments += ['--routes-file', str(paths['routes'])]
    validate_output = tmp_path / 'validate'
    assert route_stage.main(['validate', *arguments, '--output', str(validate_output)]) == 0
    assert json.loads((validate_output / 'summary.json').read_text())['status'] == 'partial_accepted'
    output = tmp_path / 'materialize'
    assert route_stage.main(['materialize', *arguments, '--route-id', 'candidate_2', '--output', str(output)]) == 0
    instances = json.loads((output / 'prepared_instances.json').read_text())
    assert len(instances) == 2
    assert all(row['source_confidence']['paraphrase']['quote_verified'] is False for row in instances)
    assert json.loads((output / 'research_routes.raw.json').read_text()) == raw


def test_tampered_sidecar_cannot_certify_quotes_or_change_actual_comparisons():
    raw, sheet, evidence, constraints = synthetic_fixture()
    result = rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)
    forged = copy.deepcopy(result['candidate_report'])
    forged['clues']['paraphrase'].update(quote_verified=True, source_confidence='directly_locatable_quote')
    forged['routes']['candidate_2']['source_confidence']['paraphrase'] = 'directly_locatable_quote'
    forged['routes']['candidate_2']['comparisons'][0]['actual_changed_variables'] = []
    forged['routes']['candidate_2']['comparisons'][0]['classification'] = 'baseline_reference'
    prepared = rr.materialize(result['accepted_contract'], 'candidate_2', sheet, evidence,
        design_constraints=constraints, candidate_mode=True, candidate_report=forged)
    for instance in prepared:
        assert instance['source_confidence']['paraphrase']['quote_verified'] is False
        assert instance['source_confidence']['paraphrase']['source_confidence'] == 'unverified_paraphrase_or_inference'
        assert instance['candidate_metadata']['source_confidence']['paraphrase'] == 'unverified_paraphrase_or_inference'
        assert instance['candidate_metadata']['comparisons'][0]['actual_changed_variables'] == ['dH']
        assert instance['candidate_metadata']['comparisons'][0]['classification'] == 'thickness_intervention'
        assert instance['proposal_derivation_audit']['status'] == 'caller_supplied_history_not_authoritative'
