"""Offline metadata-tolerance regression; no provider calls or physical claims."""
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor' / 'review'))
import research_routes as rr
import task_sheet as ts


def example(name='rgb_nir_task_sheet.json'):
    return json.loads((ROOT / 'examples' / name).read_text(encoding='utf-8'))


class OfflinePlanner:
    def __init__(self, analysis):
        self.raw = json.dumps(analysis, ensure_ascii=False)
        self.last_usage = {}
        self.last_transport = {}
        self.calls = []

    def _call_query_plan_model(self, prompt):
        self.calls.append(copy.deepcopy(prompt))
        return self.raw


def unverified_example():
    analysis = example()
    analysis['known_data'][0]['original_span'] = '多光谱…相机'
    return analysis


def test_all_goals_can_have_descriptive_physical_tradeoff_without_repair():
    analysis = example()
    analysis['goal_relationships']['tradeoff'] = (
        'Layer changes may improve one spectral band while worsening the other; '
        'the user has not specified weights or an acceptable compromise.')
    before = copy.deepcopy(analysis)
    assert ts.validate(analysis) == before
    planner = OfflinePlanner(analysis)
    outcome = ts.generate(analysis['original_question'], planner)
    assert outcome['status'] == 'ok' and outcome['first_hit']
    assert len(planner.calls) == 1 and outcome['revision_rounds'] == 0
    expected = ts.prepare_analysis(analysis['original_question'], before)[0]
    assert outcome['task_sheet'] == expected
    assert outcome['task_sheet']['goal_relationships']['priority_order'] is None
    assert outcome['rounds'][0]['normalization_audit'] == []
    assert outcome['rounds'][0]['raw_response'] == planner.raw
    assert outcome['rounds'][0]['effective_task_sheet'] == expected
    assert analysis == before


@pytest.mark.parametrize('mode', ['all', 'any'])
def test_all_any_still_reject_executable_priority_order(mode):
    analysis = example()
    analysis['goal_relationships'].update(mode=mode, priority_order=['G1'])
    with pytest.raises(ValueError, match='cannot declare priorities'):
        ts.validate(analysis)


def test_optional_span_demotion_is_lossless_explicit_and_idempotent():
    analysis = unverified_example()
    before = copy.deepcopy(analysis)
    question = '\t\r\n' + analysis['original_question'] + '\n🙂  '
    prepared = ts.prepare_analysis(question, analysis)
    sheet = prepared[0]
    audit = prepared[1]
    assert ts.validate(sheet, question=question) is sheet
    assert sheet['original_question'].encode('utf-8') == question.encode('utf-8')
    assert analysis == before
    assert sheet['known_data'] == []
    assert sheet['unknowns_assumptions'][:-1] == analysis['unknowns_assumptions']
    demoted = sheet['unknowns_assumptions'][-1]
    assert demoted == {'kind': 'assumption',
        'statement': ts.UNVERIFIED_BACKGROUND + analysis['known_data'][0]['statement'],
        'assumed_value': None, 'needs_user_input': False}
    assert 'not user-provided or accepted truth' in demoted['statement']
    assert audit == [{'operation': 'demote_unverified_known_data',
        'source_path': 'known_data[0]', 'destination_path': 'unknowns_assumptions[2]',
        'reason': 'original_span_not_exact_native_question',
        'raw_row': before['known_data'][0], 'effective_row': demoted}]
    for key in set(before) - {'original_question', 'known_data', 'unknowns_assumptions', 'subgoals'}:
        assert sheet[key] == before[key]
    for actual, original in zip(sheet['subgoals'], before['subgoals']):
        assert actual == {**original, 'quote_status': ts.EXACT_GOAL_QUOTE}
    repeated = ts.prepare_analysis(question, sheet)
    assert repeated[0] == sheet
    assert repeated[1] == []
    # Audit and prepared output cannot mutate the source or one another.
    audit[0]['raw_row']['statement'] = 'audit changed'
    audit[0]['effective_row']['statement'] = 'audit changed'
    assert analysis == before
    assert demoted['statement'].startswith(ts.UNVERIFIED_BACKGROUND)


def test_known_data_mismatch_uses_native_input_not_model_echo():
    analysis = example()
    question = analysis['original_question']
    unsupported_quote = 'this text exists only in the model echo'
    analysis['original_question'] += unsupported_quote
    analysis['known_data'][0]['original_span'] = unsupported_quote
    planner = OfflinePlanner(analysis)
    outcome = ts.generate(question, planner)
    assert outcome['status'] == 'ok' and outcome['first_hit']
    assert outcome['revision_rounds'] == 0 and len(planner.calls) == 1
    record = outcome['rounds'][0]
    assert record['raw_response'] == planner.raw
    assert record['raw_task_sheet'] == analysis
    assert record['ignored_model_fields'] == ['original_question']
    assert record['normalization_audit'][0]['raw_row'] == analysis['known_data'][0]
    assert record['effective_task_sheet'] == outcome['task_sheet']
    assert outcome['task_sheet']['original_question'] == question
    assert outcome['task_sheet']['known_data'] == []
    assert outcome['plan']['task_sheet'] == outcome['task_sheet']
    assert ts.validate_adapter(outcome['plan']) == outcome['plan']


def test_direct_validation_never_silently_accepts_false_known_data_provenance():
    analysis = unverified_example()
    before = copy.deepcopy(analysis)
    with pytest.raises(ValueError, match='known_data.original_span: must be an exact span'):
        ts.validate(analysis)
    with pytest.raises(ValueError, match='known_data.original_span: must be an exact span'):
        ts.adapt(analysis)
    assert analysis == before


def test_exact_known_data_is_preserved_while_unverified_rows_keep_source_indices():
    analysis = example()
    verified = copy.deepcopy(analysis['known_data'][0])
    first = {'statement': 'Speculative first background', 'original_span': 'not a native quote'}
    last = {'statement': 'Speculative last background', 'original_span': 'another nonquote'}
    analysis['known_data'] = [first, verified, last]
    prepared = ts.prepare_analysis(analysis['original_question'], analysis)
    assert prepared[0]['known_data'] == [verified]
    assert [row['source_path'] for row in prepared[1]] == ['known_data[0]', 'known_data[2]']
    assert [row['destination_path'] for row in prepared[1]] == ['unknowns_assumptions[2]', 'unknowns_assumptions[3]']
    assert [row['raw_row'] for row in prepared[1]] == [first, last]
    ts.validate(prepared[0])


@pytest.mark.parametrize('row', [
    {'statement': 'Background', 'original_span': None},
    {'statement': 'Background', 'original_span': ''},
    {'statement': 'Background', 'original_span': ' padded nonquote '},
    {'statement': '', 'original_span': 'nonquote'},
    {'statement': None, 'original_span': 'nonquote'},
    {'statement': 'Background'},
    {'statement': 'Background', 'original_span': 'nonquote', 'extra': True},
    'nonobject',
])
def test_demotion_does_not_hide_structurally_invalid_metadata(row):
    analysis = example()
    analysis['known_data'] = [row]
    prepared = ts.prepare_analysis(analysis['original_question'], analysis)
    assert prepared[1] == []
    assert prepared[0]['known_data'] == [row]
    with pytest.raises(ValueError):
        ts.validate(prepared[0])


@pytest.mark.parametrize('field', ['explicit_constraints', 'unsupported_requirements'])
def test_metadata_demotion_cannot_repair_goal_or_requirement_provenance(field):
    analysis = unverified_example()
    if field == 'unsupported_requirements':
        analysis[field] = [{'id': 'U1', 'requirement': 'Unmapped outcome',
            'original_span': 'not in native input', 'reason': 'Unsupported metric'}]
        analysis['readiness'] = 'needs_revision'
    else:
        analysis[field][0]['original_span'] = 'not in native input'
    prepared = ts.prepare_analysis(analysis['original_question'], analysis)
    assert prepared[1]
    assert prepared[0][field] == analysis[field]
    with pytest.raises(ValueError, match='must be an exact span'):
        ts.validate(prepared[0])


@pytest.mark.parametrize('mutation', [
    lambda s: s['subgoals'][0]['executable_metric'].update(metric='invented_score'),
    lambda s: s['subgoals'][0]['executable_metric'].update(objective_id='G2'),
    lambda s: s['subgoals'][0]['executable_metric'].update(mean_reduction='invented_mean'),
    lambda s: s['subgoals'][0]['executable_metric']['region'].update(wavelength_nm=[650, 450]),
    lambda s: s['subgoals'][0]['executable_metric']['region'].update(wavelength_nm=[450, float('inf')]),
    lambda s: s['subgoals'][0]['executable_metric']['region'].update(angle_deg=90),
    lambda s: s['subgoals'][0]['executable_metric']['region'].update(polarization='unspecified'),
    lambda s: s.update(unknowns_assumptions={}),
])
def test_metadata_tolerance_does_not_relax_executable_guards(mutation):
    analysis = unverified_example()
    mutation(analysis)
    planner = OfflinePlanner(analysis)
    outcome = ts.generate(analysis['original_question'], planner)
    assert outcome['status'] == 'invalid'
    assert outcome['task_sheet'] == {} and outcome['plan'] == {}
    assert len(planner.calls) == 2
    assert all(row['raw_task_sheet'] == analysis for row in outcome['rounds'])


def test_unverified_background_reaches_routes_only_as_labeled_assumption():
    analysis = unverified_example()
    sheet = ts.prepare_analysis(analysis['original_question'], analysis)[0]
    prompt = rr.build_prompt(sheet, example('research_routes_evidence_fixture.json'),
        design_constraints=example('research_routes_constraints.json'))
    downstream = prompt['user_payload']['task_sheet']
    assert downstream['known_data'] == []
    assert downstream['unknowns_assumptions'][-1]['statement'].startswith(ts.UNVERIFIED_BACKGROUND)
    routes = example('research_routes_example.json')
    stacks = rr.materialize(routes, 'R1', sheet,
        example('research_routes_evidence_fixture.json'),
        design_constraints=example('research_routes_constraints.json'))
    assert stacks[0]['objectives'] == [g['executable_metric'] for g in analysis['subgoals']]
    assert sheet['explicit_constraints'] == analysis['explicit_constraints']


def test_prompt_explains_descriptive_tradeoff_and_unverified_background():
    prompt = ts.render_prompt('Native question')['system_prompt']
    assert 'Descriptive physical tension in tradeoff is compatible with mode all' in prompt
    assert 'never a user priority, weight, compromise rule or additional objective' in prompt
    assert 'Never reconstruct a quote from an ellipsis or paraphrase' in prompt
    assert 'Supported report-only reflection, transmission and absorption outputs' in prompt
    assert 'not additional optimization objectives' in prompt


@pytest.mark.parametrize('links', [[], ['R1']])
def test_partial_indirect_research_links_are_preserved_and_advisory(links):
    analysis = example()
    analysis['research_questions'][0]['subgoal_ids'] = ['G1']
    analysis['search_queries'][0]['research_question_ids'] = links
    before = copy.deepcopy(analysis)
    sheet, audit = ts.prepare_analysis(analysis['original_question'], analysis)
    expected = copy.deepcopy(before)
    for goal in expected['subgoals']:
        goal['quote_status'] = ts.EXACT_GOAL_QUOTE
    assert sheet == expected and analysis == before
    assert ts.validate(sheet) == expected
    assert ts.validate_adapter(ts.adapt(sheet))['task_sheet'] == expected
    first = audit[0]
    assert first['operation'] == 'retain_optional_query_links'
    assert first['source_path'] == 'search_queries[0].research_question_ids'
    assert first['raw_value'] == links and first['effective_value'] == links
    assert first['uncovered_subgoal_ids'] == (['G1', 'G2'] if not links else ['G2'])
    assert first['status'] == 'advisory_not_evidence_of_relevance'
    assert ts.prepare_analysis(analysis['original_question'], sheet) == (sheet, audit)
    planner = OfflinePlanner(analysis)
    result = ts.generate(analysis['original_question'], planner)
    assert result['status'] == 'ok' and result['first_hit']
    assert result['rounds'][0]['normalization_audit'] == audit
    assert len(planner.calls) == 1


@pytest.mark.parametrize('mutation', [
    lambda s: s['search_queries'][0].update(research_question_ids=['MISSING']),
    lambda s: s['search_queries'][0].update(research_question_ids=['R1', 'R1']),
    lambda s: s['search_queries'][0].update(subgoal_ids=[]),
    lambda s: s['search_queries'][0].update(subgoal_ids=['MISSING']),
    lambda s: s['search_queries'][0].update(research_question_ids=['R1'], subgoal_ids=['G1'])
        or s['research_questions'][0].update(subgoal_ids=['G2']),
    lambda s: [q.update(subgoal_ids=['G1']) for q in s['search_queries']],
])
def test_optional_links_do_not_remove_reference_or_direct_coverage_guards(mutation):
    analysis = example()
    mutation(analysis)
    sheet, _ = ts.prepare_analysis(analysis['original_question'], analysis)
    with pytest.raises(ValueError):
        ts.validate(sheet)
