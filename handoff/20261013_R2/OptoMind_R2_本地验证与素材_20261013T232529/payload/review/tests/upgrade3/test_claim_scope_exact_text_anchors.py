"""Supply exact paths to the checker without accepting inexact evidence."""
import copy
import json

from optomind_research.runtime.upgrade3.progressive_review_plan import (
    _claim_pointer, _claim_scope_text_anchors, _messages_for, validate_claim_scope_review,
)


def packet():
    return {'chapter_id': 'CH1', 'chapter_plan': {'units': [
        {'point': 'Only the reported setup supports this result.'}]},
        'source_materials': [{'source_handle': 'P0001', 'finding/a~b': 'Reported setup: value <= 5.',
                              'possible_uses': ['Unverified proposal.']}],
        'tool_materials': [{'usable_content': 'The limit applies to the measured setup.'}]}


def issue(payload):
    anchors = _claim_scope_text_anchors(payload)
    return {'category': 'numeric_bound', **{k:v for k,v in
        [('claim_path', anchors['claims'][0]['path']), ('claim_quote', anchors['claims'][0]['quote'])]},
        'problem': 'Fixture mismatch', 'requested_change': 'Owner must inspect the bound.',
        'evidence': [next(x for x in anchors['evidence'] if 'finding~1a~0b' in x['path'])]}


def test_anchors_resolve_to_exact_existing_strings_without_mutation():
    payload = packet()
    before = copy.deepcopy(payload)
    anchors = _claim_scope_text_anchors(payload)
    for rows in anchors.values():
        for row in rows:
            assert _claim_pointer(payload, row['path']) == row['quote']
    assert payload == before
    assert '/source_materials/0/finding~1a~0b' in [x['path'] for x in anchors['evidence']]
    assert 'exact_text_anchors' in _messages_for('claim_scope_review', payload)[1]['content']
    assert 'exact_text_anchors' not in _messages_for('chapter_details', payload)[1]['content']


def test_exact_indexed_anchor_accepted_but_paraphrase_and_bad_path_stay_blocked():
    payload = packet()
    response = {'status': 'issues', 'checks': ['claim_scope', 'numeric_bounds'], 'issues': [issue(payload)]}
    assert validate_claim_scope_review(payload, response)['status'] == 'issues'
    for change in ('paraphrase', 'missing_root', 'wrong_index'):
        broken = copy.deepcopy(response)
        evidence = broken['issues'][0]['evidence'][0]
        if change == 'paraphrase':
            evidence['quote'] = 'This is not a source quote.'
        elif change == 'missing_root':
            evidence['path'] = evidence['path'].lstrip('/')
        else:
            evidence['path'] = evidence['path'].replace('/0/', '/1/')
        assert validate_claim_scope_review(payload, broken)['status'] == 'unresolved'


def test_index_does_not_make_proposed_uses_independent_evidence():
    payload = packet()
    row = issue(payload)
    row['evidence'] = [next(x for x in _claim_scope_text_anchors(payload)['evidence']
                            if '/possible_uses/' in x['path'])]
    response = {'status': 'issues', 'checks': ['claim_scope', 'numeric_bounds'], 'issues': [row]}
    assert validate_claim_scope_review(payload, response)['status'] == 'unresolved'


def id_response(payload):
    anchors = _claim_scope_text_anchors(payload)
    return {'status': 'issues', 'checks': ['claim_scope', 'numeric_bounds'], 'issues': [{
        'category': 'numeric_bound', 'claim_anchor_id': anchors['claims'][0]['anchor_id'],
        'evidence_anchor_ids': [next(x['anchor_id'] for x in anchors['evidence'] if 'finding~1a~0b' in x['path'])],
        'problem': 'Fixture bound mismatch', 'requested_change': 'Owner must inspect the original condition.'}]}


def test_ids_restore_exact_bound_text_without_model_authored_quotes():
    payload = packet()
    result = validate_claim_scope_review(payload, id_response(payload))
    assert result['status'] == 'issues'
    row = result['issues'][0]
    assert row['claim_quote'] == payload['chapter_plan']['units'][0]['point']
    assert row['evidence'][0]['quote'] == payload['source_materials'][0]['finding/a~b']
    assert row['source_handles'] == ['P0001']


def test_unknown_stale_or_contradictory_anchor_ids_stay_blocked():
    payload = packet()
    original = id_response(payload)
    unknown = copy.deepcopy(original)
    unknown['issues'][0]['evidence_anchor_ids'] = ['E-not-provided']
    assert validate_claim_scope_review(payload, unknown)['status'] == 'unresolved'
    changed = copy.deepcopy(payload)
    changed['source_materials'][0]['usage_limits'] = ['new source restriction']
    assert validate_claim_scope_review(changed, original)['status'] == 'unresolved'
    contradictory = copy.deepcopy(original)
    contradictory['issues'][0]['claim_quote'] = 'invented quotation'
    assert validate_claim_scope_review(payload, contradictory)['status'] == 'unresolved'


def test_proposal_ids_are_not_independent_evidence():
    payload = packet()
    response = id_response(payload)
    response['issues'][0]['evidence_anchor_ids'] = [next(x['anchor_id'] for x in
        _claim_scope_text_anchors(payload)['evidence'] if '/possible_uses/' in x['path'])]
    assert validate_claim_scope_review(payload, response)['status'] == 'unresolved'
