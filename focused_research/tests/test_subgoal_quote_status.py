"""Native-input quotation audit is not semantic authorization or metric repair."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor' / 'review'))
import task_sheet as ts
from test_task_sheet_tolerance import OfflinePlanner


def example():
    return json.loads((ROOT / 'examples' / 'rgb_nir_task_sheet.json').read_text())


def test_legacy_exact_goals_validate_and_adapt_without_mutation():
    sheet = example()
    before = copy.deepcopy(sheet)
    assert ts.validate(sheet) is sheet
    assert ts.validate_adapter(ts.adapt(sheet))['task_sheet'] == before
    assert sheet == before
    assert all(set(goal) == set(ts.GOAL_KEYS) for goal in sheet['subgoals'])


@pytest.mark.parametrize('quote', [
    '尽可能抑制 700–1100 nm 近红外杂散光',
    'An unrelated interpretation about marine biology',
    '可见光波段…尽可能高的反射率',
])
def test_nonquote_is_labeled_losslessly_and_not_promoted_to_user_fact(quote):
    analysis = example()
    analysis['subgoals'][0]['original_span'] = quote
    before = copy.deepcopy(analysis)
    sheet, audit = ts.prepare_analysis(analysis['original_question'], analysis)
    assert analysis == before
    goal = sheet['subgoals'][0]
    assert goal['original_span'] == quote
    assert goal['quote_status'] == ts.UNVERIFIED_GOAL_QUOTE
    assert {k: v for k, v in goal.items() if k != 'quote_status'} == before['subgoals'][0]
    assert set(goal) == {*ts.GOAL_KEYS, 'quote_status'}  # No invented offsets.
    assert sheet['known_data'] == before['known_data']
    assert sheet['explicit_constraints'] == before['explicit_constraints']
    assert audit[0]['raw_original_span'] == quote
    assert audit[0]['effective_quote_status'] == ts.UNVERIFIED_GOAL_QUOTE
    assert ts.validate(sheet) is sheet
    plan = ts.adapt(sheet)
    assert ts.validate_adapter(plan) is plan
    assert plan['task_sheet']['subgoals'][0]['quote_status'] == ts.UNVERIFIED_GOAL_QUOTE
    assert ts.prepare_analysis(analysis['original_question'], sheet) == (sheet, [])


@pytest.mark.parametrize('exact,status', [
    (False, ts.EXACT_GOAL_QUOTE), (False, None), (False, 'verified'),
    (True, ts.UNVERIFIED_GOAL_QUOTE), (True, None), (True, 'verified'),
])
def test_validator_recomputes_and_rejects_forged_quote_status(exact, status):
    sheet = example()
    if not exact:
        sheet['subgoals'][0]['original_span'] = 'Not native user text'
    sheet['subgoals'][0]['quote_status'] = status
    before = copy.deepcopy(sheet)
    with pytest.raises(ValueError, match='subgoal.quote_status'):
        ts.validate(sheet)
    with pytest.raises(ValueError, match='subgoal.quote_status'):
        ts.adapt(sheet)
    assert sheet == before
    prepared, audit = ts.prepare_analysis(sheet['original_question'], sheet)
    assert prepared['subgoals'][0]['quote_status'] == (
        ts.EXACT_GOAL_QUOTE if exact else ts.UNVERIFIED_GOAL_QUOTE)
    assert audit[0]['raw_quote_status'] == status
    ts.validate(prepared)


def test_nonexact_legacy_goal_requires_explicit_unverified_label():
    sheet = example()
    sheet['subgoals'][0]['original_span'] = 'Not native user text'
    with pytest.raises(ValueError, match='nonexact text requires quote_status'):
        ts.validate(sheet)


def test_prepare_uses_native_question_not_model_echo_and_generation_needs_no_repair():
    sheet = example()
    question = sheet['original_question']
    quote = '尽可能抑制 700–1100 nm 近红外杂散光'
    sheet['subgoals'][0].update(original_span=quote, quote_status=ts.EXACT_GOAL_QUOTE)
    sheet['original_question'] += quote
    planner = OfflinePlanner(sheet)
    outcome = ts.generate(question, planner)
    assert outcome['status'] == 'ok' and outcome['first_hit']
    assert len(planner.calls) == 1 and outcome['revision_rounds'] == 0
    assert outcome['rounds'][0]['raw_task_sheet'] == sheet
    effective = outcome['task_sheet']
    assert effective['original_question'] == question
    assert effective['subgoals'][0]['original_span'] == quote
    assert effective['subgoals'][0]['quote_status'] == ts.UNVERIFIED_GOAL_QUOTE


@pytest.mark.parametrize('mutation', [
    lambda s: s['subgoals'][0]['executable_metric'].update(metric='invented'),
    lambda s: s['subgoals'][0]['executable_metric'].update(sense='suppress'),
    lambda s: s['subgoals'][0]['executable_metric'].update(objective_id='G2'),
    lambda s: s['subgoals'][0]['executable_metric'].update(mean_reduction='invented'),
    lambda s: s['subgoals'][0]['executable_metric']['region'].update(wavelength_nm=[700, 600]),
    lambda s: s['subgoals'][0]['executable_metric']['region'].update(wavelength_nm=[700, float('nan')]),
    lambda s: s['subgoals'][0]['executable_metric']['region'].update(angle_deg=90),
    lambda s: s['subgoals'][0]['executable_metric']['region'].update(polarization='unknown'),
    lambda s: s['subgoals'][1].update(id='G1'),
    lambda s: s['explicit_constraints'][0].update(original_span='not a native quote'),
])
def test_unverified_label_does_not_weaken_executable_or_constraint_guards(mutation):
    sheet = example()
    sheet['subgoals'][0]['original_span'] = 'A reasonable but nonexact interpretation'
    mutation(sheet)
    prepared, _ = ts.prepare_analysis(sheet['original_question'], sheet)
    with pytest.raises(ValueError):
        ts.validate(prepared)


@pytest.mark.parametrize('bad', [None, '', ' padded ', 7])
def test_preparation_does_not_mask_invalid_span_shape(bad):
    sheet = example()
    sheet['subgoals'][0]['original_span'] = bad
    prepared, _ = ts.prepare_analysis(sheet['original_question'], sheet)
    with pytest.raises(ValueError, match='subgoal.original_span'):
        ts.validate(prepared)


def test_exact_is_only_substring_evidence_not_semantic_acceptance():
    sheet = example()
    sheet['subgoals'][0]['original_span'] = '我'
    prepared, _ = ts.prepare_analysis(sheet['original_question'], sheet)
    assert prepared['subgoals'][0]['quote_status'] == ts.EXACT_GOAL_QUOTE
    prompt = ts.render_prompt(sheet['original_question'])['system_prompt']
    assert 'not semantic support, scientific correctness or user acceptance' in prompt
    assert 'Unrelated or inferred explanations remain unverified model interpretation' in prompt
