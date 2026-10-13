"""Repeated evidence must not multiply coordinator context or lose identity."""
import copy
import json

from optomind_research.runtime.upgrade3.progressive_review_plan import (
    ProgressivePlannerConfig, ProgressiveReviewPlanner,
    _messages_for, _shared_whole_plan_materials,
)


def materials():
    return {'source_handle': 'P0001', 'paper_id': 'same-paper',
            'usage_limits': ['synthetic fixture only'],
            'deep_read_material': {'question_material': [
                {'finding': 'bounded source-specific evidence ' * 150,
                 'conditions': ['current condition'], 'source_pointer': '/sections/2'}],
                'prior_question_material': [{'finding': 'prior finding'}]}}


def payload():
    source = materials()
    change = {'source_handle': 'P0001', 'source_materials': [source],
              'previous_material': {'finding': 'older bounded evidence'}}
    return {'research_question': 'fixture', 'planning_revision_mode': True,
            'chapters': [{'chapter_id': cid, 'scope': cid, 'late_material_changes': [change]}
                         for cid in ('CH1', 'CH2', 'CH3')],
            'late_material_changes': {cid: [change] for cid in ('CH1', 'CH2', 'CH3')},
            'tool_results_summary': {'materials': [source]}}


def expand(value, catalog):
    if isinstance(value, dict):
        if set(value) == {'$optomind_shared_material'}:
            return copy.deepcopy(catalog[value['$optomind_shared_material']])
        return {key: expand(child, catalog) for key, child in value.items()
                if key not in ('__shared_materials__', '__shared_materials_contract__')}
    if isinstance(value, list):
        return [expand(child, catalog) for child in value]
    return value


def test_repeated_late_readings_are_lossless_and_keep_chapter_roles():
    original = payload()
    snapshot = copy.deepcopy(original)
    rendered = _shared_whole_plan_materials(original)
    assert original == snapshot
    assert expand(rendered, rendered['__shared_materials__']) == original
    assert len(json.dumps(rendered)) < len(json.dumps(original)) / 2
    assert [row['scope'] for row in rendered['chapters']] == ['CH1', 'CH2', 'CH3']


def test_conflicting_content_for_one_identity_is_never_folded():
    original = payload()
    other = materials()
    other['deep_read_material']['question_material'][0]['finding'] = 'conflicting evidence ' * 150
    original['tool_results_summary']['materials'].append(other)
    rendered = _shared_whole_plan_materials(original)
    restored = expand(rendered, rendered['__shared_materials__'])
    assert restored == original
    assert restored['tool_results_summary']['materials'][0] != restored['tool_results_summary']['materials'][1]


def test_only_coordinator_prompt_changes_and_existing_catalog_is_preserved():
    original = payload()
    whole = _messages_for('whole_plan_improvement', original)[1]['content']
    owner = _messages_for('affected_chapter_revision', original)[1]['content']
    checker = _messages_for('claim_scope_review', original)[1]['content']
    assert '__shared_materials__' in whole
    assert '__shared_materials__' not in owner and '__shared_materials__' not in checker
    original['__shared_materials__'] = {'existing': 'unrelated input'}
    assert _shared_whole_plan_materials(original) == original


def test_scientific_change_invalidates_coordinator_cache(tmp_path):
    config = ProgressivePlannerConfig(topic_id='fixture', pool_path=tmp_path/'pool',
        plan_path=tmp_path/'plan', output_dir=tmp_path/'out')
    flow = ProgressiveReviewPlanner(config, planner=lambda *_: {})
    original = payload()
    first = flow._cache_contract('whole_plan_improvement', original)
    assert first == flow._cache_contract('whole_plan_improvement', copy.deepcopy(original))
    changed = copy.deepcopy(original)
    changed['late_material_changes']['CH1'][0]['source_materials'][0]['usage_limits'].append('new restriction')
    assert first != flow._cache_contract('whole_plan_improvement', changed)
