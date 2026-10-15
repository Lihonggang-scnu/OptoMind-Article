"""Whole-item prompt budgeting and active-citation binding, entirely offline."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor' / 'review'))
from material_evidence import prompt_evidence_view, select_prompt_evidence
import research_routes as rr


def item(eid, text='A useful complete original passage.', goals=('G1',), queries=('Q1',), **extra):
    return {'evidence_id': eid, 'text': text, 'source_id': 'source:' + eid,
            'snippet_type': 'unknown', 'source_mappings': [],
            'goal_ids': list(goals), 'query_ids': list(queries),
            'eligible_as_evidence': True, 'missing_reasons': [], **extra}


def payload(*items):
    return {'schema_version': 'focused_evidence_items_v1', 'items': list(items)}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def test_small_input_preserves_whole_items_order_and_strict_projection():
    evidence = payload(item('E2'), item('E1', '  原始公式：η=A/B\n'))
    evidence['material_exports'] = [{'full': 'unmodified source archive'}]
    before = deepcopy(evidence)
    result = select_prompt_evidence(evidence)
    assert evidence == before
    assert result['evidence_items']['items'] == evidence['items']
    assert result['prompt_view'] == prompt_evidence_view(evidence)
    audit = result['selection_audit']
    assert audit['strategy'] == 'all_items_fit'
    assert audit['included_evidence_ids'] == ['E2', 'E1']
    assert audit['deferred_items'] == []
    assert audit['archive_sha256'] == fingerprint(evidence)
    assert audit['selected_evidence_sha256'] == fingerprint(result['evidence_items'])
    result['evidence_items']['items'][0]['text'] = 'changed copy'
    assert evidence == before


def test_round_robin_coverage_determinism_and_no_false_empty_query_coverage():
    evidence = payload(*(item('E%d' % n, str(n) * 100,
        goals=('G1' if n < 6 else 'G2',), queries=('Q1' if n < 6 else 'Q2',))
        for n in range(12)))
    result = select_prompt_evidence(evidence, goal_ids=['G1', 'G2'],
        query_ids=['Q1', 'Q2', 'Q_missing'], max_text_chars=600)
    assert result == select_prompt_evidence(evidence, goal_ids=['G1', 'G2'],
        query_ids=['Q1', 'Q2', 'Q_missing'], max_text_chars=600)
    audit = result['selection_audit']
    assert audit['included_text_chars'] <= 600
    assert audit['queries']['Q1']['included_substantive_item_count'] == 3
    assert audit['queries']['Q2']['included_substantive_item_count'] == 3
    assert audit['queries']['Q_missing']['available_item_count'] == 0
    assert audit['queries']['Q_missing']['included_item_count'] == 0
    assert all(c['included_substantive_item_count'] for c in audit['goals'].values())
    active = result['evidence_items']['items']
    assert active == [i for i in evidence['items'] if i['evidence_id'] in audit['included_evidence_ids']]
    assert result['prompt_view'] == prompt_evidence_view(result['evidence_items'], max_text_chars=600)


def test_unknown_snippet_without_bibliographic_fields_is_kept_before_title():
    evidence = payload(item('title', 'T' * 10, snippet_type='title'), item('unknown', 'U' * 90))
    result = select_prompt_evidence(evidence, max_text_chars=90)
    assert result['selection_audit']['included_evidence_ids'] == ['unknown']
    assert result['evidence_items']['items'][0] == evidence['items'][1]
    assert result['selection_audit']['queries']['Q1']['included_substantive_item_count'] == 1


def test_oversized_item_is_deferred_intact_and_does_not_kill_case():
    evidence = payload(item('oversized', 'x' * 48001), item('small', 'safe full source'))
    before = deepcopy(evidence)
    result = select_prompt_evidence(evidence)
    assert result['selection_audit']['included_evidence_ids'] == ['small']
    assert result['selection_audit']['deferred_items'] == [
        {'evidence_id': 'oversized', 'reasons': ['text_budget'], 'text_chars': 48001}]
    assert evidence == before
    with pytest.raises(ValueError, match='explicit smaller block selection'):
        prompt_evidence_view(evidence)


def test_actual_projected_metadata_counts_toward_payload_budget():
    evidence = payload(item('metadata_heavy', source_mappings=[{'provider': 'original', 'legal_terms': 'x' * 12000}]),
                       item('small', 'whole short text'))
    result = select_prompt_evidence(evidence, max_payload_chars=3000)
    assert result['selection_audit']['included_evidence_ids'] == ['small']
    assert result['selection_audit']['deferred_items'][0]['reasons'] == ['payload_budget']
    assert len(json.dumps(result['prompt_view'], ensure_ascii=False, allow_nan=False)) <= 3000
    assert len(evidence['items'][0]['source_mappings'][0]['legal_terms']) == 12000


def test_material_assets_restrictions_and_provenance_survive_selection():
    from test_material_evidence import export
    from material_evidence import import_material_export
    original = import_material_export(export(), goal_ids=['G1'], query_ids=['Q1'])
    original['items'].append(item('oversized', 'x' * 48001))
    result = select_prompt_evidence(original)
    assert result['evidence_items']['items'] == original['items'][:2]
    assert result['prompt_view'] == prompt_evidence_view(result['evidence_items'])
    assert result['evidence_items']['items'][0]['material_assets'][0]['structure']['cells'] == [['10^{-3}']]
    assert result['evidence_items']['items'][0]['source_mappings'] == original['items'][0]['source_mappings']
    assert result['prompt_view']['snapshots']
    assert 'material_exports' not in result['evidence_items']
    assert original['material_exports'] == [export()]


def test_empty_all_ineligible_and_all_too_large_are_honest():
    empty = select_prompt_evidence(payload(), query_ids=['Q1'])
    assert empty['evidence_items']['items'] == []
    assert empty['selection_audit']['queries']['Q1']['available_item_count'] == 0
    weak = select_prompt_evidence(payload(item('weak', eligible_as_evidence=False, source_id='',
        missing_reasons=['missing_source'])))
    assert weak['evidence_items']['items'][0]['eligible_as_evidence'] is False
    assert weak['selection_audit']['queries']['Q1']['included_substantive_item_count'] == 0
    large = select_prompt_evidence(payload(item('large', 'x' * 48001)))
    assert large['evidence_items']['items'] == []
    assert large['selection_audit']['queries']['Q1']['available_substantive_item_count'] == 1
    assert large['selection_audit']['queries']['Q1']['included_substantive_item_count'] == 0


@pytest.mark.parametrize('values', [dict(max_text_chars=0), dict(max_text_chars=True),
    dict(max_payload_chars=-1), dict(max_payload_chars=1)])
def test_invalid_or_impossible_budgets_fail_clearly(values):
    with pytest.raises(ValueError, match='budget'):
        select_prompt_evidence(payload(item('E')), **values)


def test_duplicate_ids_cannot_alias_different_scientific_content():
    with pytest.raises(ValueError, match='unique nonempty evidence IDs'):
        select_prompt_evidence(payload(item('same'), item('same', 'different')))


def examples():
    read = lambda name: json.loads((ROOT / 'examples' / name).read_text())
    return (read('research_routes_example.json'), read('rgb_nir_task_sheet.json'),
        read('research_routes_evidence_fixture.json'), read('research_routes_constraints.json'))


class OfflinePlanner:
    last_usage = {'input_tokens': 1}
    last_transport = {'provider': 'saved_response_fixture', 'fallback_used': False}

    def __init__(self, response):
        self.response = response
        self.calls = []

    def _call_query_plan_model(self, prompt):
        self.calls.append(deepcopy(prompt))
        return json.dumps(self.response)


def test_generate_rejects_real_but_unseen_archive_id():
    routes, sheet, evidence, constraints = examples()
    evidence['items'][0]['text'] += 'x' * 48001
    evidence['items'].append(item('visible', 'readable full passage'))
    planner = OfflinePlanner(routes)
    result = rr.generate(sheet, evidence, planner, design_constraints=constraints,
        candidate_mode=True, max_repairs=0)
    assert len(planner.calls) == 1
    assert result['selected_evidence_items']['items'] == evidence['items'][1:]
    assert result['candidate_report']['rejected_route_ids'] == ['R1']
    assert result['candidate_report']['clues'][routes['decision_clues'][0]['id']]['status'] == 'rejected'
    assert result['research_routes']['routes'] == []
    assert 'unknown evidence ID' in json.dumps(result['candidate_report'])
    assert [i['evidence_id'] for i in planner.calls[0]['user_payload']['evidence_items']['items']] == ['visible']
    assert 'deferred' in planner.calls[0]['system_prompt']
    assert result['evidence_selection']['archive_sha256'] == fingerprint(evidence)


def test_generate_under_budget_keeps_lenient_paraphrase_admission():
    routes, sheet, evidence, constraints = examples()
    routes['decision_clues'][0]['source_quote'] = 'A tentative inference, not an exact source quote.'
    planner = OfflinePlanner(routes)
    result = rr.generate(sheet, evidence, planner, design_constraints=constraints,
        candidate_mode=True, max_repairs=0)
    assert result['status'] == 'ok'
    assert result['selected_evidence_items'] == evidence
    assert result['candidate_report']['clues'][routes['decision_clues'][0]['id']]['source_confidence'] == 'unverified_paraphrase_or_inference'
