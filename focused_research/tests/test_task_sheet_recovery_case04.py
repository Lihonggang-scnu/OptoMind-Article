"""Offline replay of both byte-pinned T037 case04 planning responses."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from focused_research import task_sheet as ts, task_sheet_recovery as rec, search_stage as st

PLANNING = Path(__file__).parent / 'fixtures/t037_case04_planning.json'
SAVED = json.loads(PLANNING.read_text())
QUESTION = SAVED['rounds'][1]['effective_task_sheet']['original_question']


def analysis(round_number=2):
    return json.loads(SAVED['rounds'][round_number - 1]['raw_response'])


def prepare(value, **kwargs):
    return rec.prepare_recovered_analysis(rec.UV, kwargs.pop('question', QUESTION), value,
        source_commit=kwargs.pop('source_commit', rec.UV_SOURCE_COMMIT),
        source_planning_sha256=kwargs.pop('source_planning_sha256', rec.UV_PLANNING_SHA256))


@pytest.mark.parametrize('round_number', [1, 2])
def test_exact_saved_round_recovers_and_adapter_roundtrips(tmp_path, round_number):
    before = PLANNING.read_bytes()
    assert hashlib.sha256(before).hexdigest() == rec.UV_PLANNING_SHA256
    raw = analysis(round_number)
    unchanged = copy.deepcopy(raw)
    output = tmp_path / f'round{round_number}'
    binding = rec.recover_planning(rec.UV, QUESTION, PLANNING,
        expected_sha256=rec.UV_PLANNING_SHA256, output=output, round_number=round_number)
    sheet = st.load(output / 'task_sheet.json')
    assert sheet['readiness'] == 'ready'
    assert binding['source_commit'] == rec.UV_SOURCE_COMMIT != rec.SOURCE_COMMIT
    assert binding['original_question_sha256'] == rec.UV_QUESTION_SHA256
    assert binding['new_model_calls'] == 0 and binding['physics_executed'] is False
    assert binding['source_round'] == round_number
    assert st.load(output / 'raw_response.json') == SAVED['rounds'][round_number - 1]['raw_response']
    assert st.load(output / 'raw_task_sheet.json') == unchanged
    assert PLANNING.read_bytes() == before
    expected = copy.deepcopy(raw)
    expected['original_question'] = QUESTION
    for goal, status in zip(expected['subgoals'], ['exact_user_quote', 'exact_user_quote',
                                                 'unverified_model_paraphrase']):
        goal['quote_status'] = status
    assert sheet == expected  # Every other field, including queries and constraints, is unchanged.
    plan = st.load(output / 'query_plan.json')
    assert ts.validate_adapter(plan) == ts.adapt(sheet)
    assert prepare(plan['task_sheet'])[0] == sheet
    assert st.digest(sheet) == binding['task_sheet_sha256']
    audit = st.load(output / 'normalization_audit.json')
    quote_audit = [r for r in audit['analysis_normalization'] if r['operation'] == 'classify_subgoal_quote']
    assert len(quote_audit) == 1
    assert quote_audit[0]['raw_original_span'] == raw['subgoals'][2]['original_span']


@pytest.mark.parametrize('field,value', [('sense', 'maximize'), ('metric', 'mean_reflectance'),
                                       ('mean_reduction', 'sample_mean')])
def test_semantic_metric_changes_rejected(field, value):
    raw = analysis()
    raw['subgoals'][2]['executable_metric'][field] = value
    with pytest.raises(ValueError, match='three-band contract'):
        prepare(raw)


def test_wrong_band_rejected():
    raw = analysis()
    raw['subgoals'][2]['executable_metric']['region']['wavelength_nm'] = [701, 1100]
    with pytest.raises(ValueError, match='three-band contract'):
        prepare(raw)


@pytest.mark.parametrize('index,value', [(3, 'Incident medium: water'),
    (4, 'Substrate: silicon'), (5, 'Metal layers allowed'),
    (6, 'Only SiO2 and HfO2'), (8, 'Total layer count not exceeding 31')])
def test_constraint_changes_and_narrowed_material_permission_rejected(index, value):
    raw = analysis()
    raw['explicit_constraints'][index]['constraint'] = value
    with pytest.raises(ValueError, match='broad-material contract'):
        prepare(raw)


@pytest.mark.parametrize('kwargs,match', [
    ({'question': QUESTION + ' '}, 'pinned T037 question'),
    ({'source_commit': rec.SOURCE_COMMIT}, 'pinned T037 source commit'),
    ({'source_planning_sha256': '0' * 64}, 'pinned T037 planning hash')])
def test_wrong_source_binding_rejected(kwargs, match):
    with pytest.raises(ValueError, match=match):
        prepare(analysis(), **kwargs)


def test_case04_cannot_use_unbound_prepare():
    with pytest.raises(ValueError, match='pinned T037 source commit'):
        rec.prepare_recovered_analysis(rec.UV, QUESTION, analysis())


def test_recovery_rejects_unpinned_file_even_when_callers_hash_matches(tmp_path):
    changed = tmp_path / 'changed.json'
    changed.write_bytes(PLANNING.read_bytes() + b'\n')
    output = tmp_path / 'bad'
    with pytest.raises(ValueError, match='pinned T037 planning hash'):
        rec.recover_planning(rec.UV, QUESTION, changed,
            expected_sha256=hashlib.sha256(changed.read_bytes()).hexdigest(), output=output)
    assert not output.exists()


@pytest.mark.parametrize('kwargs,match', [
    ({'source_commit': '0' * 40}, 'pinned T037 source commit'),
    ({'expected_sha256': '0' * 64}, 'hash mismatch'),
    ({'question': QUESTION + ' changed'}, 'pinned T037 question'),
    ({'round_number': 3}, 'exactly one selected saved round')])
def test_recover_rejects_wrong_binding_before_output(tmp_path, kwargs, match):
    args = dict(expected_sha256=rec.UV_PLANNING_SHA256, question=QUESTION)
    args.update(kwargs)
    with pytest.raises(ValueError, match=match):
        rec.recover_planning(rec.UV, planning_file=PLANNING, output=tmp_path / 'bad', **args)
    assert not (tmp_path / 'bad').exists()
