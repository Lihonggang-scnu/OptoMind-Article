"""T040 dispatch-only fixtures: no live provider calls or money accounting."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from focused_research.local_campaign_guard import (
    CampaignPlannerGuard, RecoveredCampaignPlannerGuard, RecoveredRevisionPlannerGuard,
)


CASE = 'e2e-04-solarblind-uv-triple'
QUESTION = '原题 fixture\n  retain exact whitespace'


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class Planner:
    last_usage = {'source': 'fixture original transport'}
    last_transport = {'finish_reason': 'stop'}

    def __init__(self):
        self.calls = []
        self.fail = False

    def _call_query_plan_model(self, prompt):
        self.calls.append(copy.deepcopy(prompt))
        if self.fail:
            raise RuntimeError('fixture transport failure')
        return {'plans': []}


def binding():
    return {
        'case_id': CASE,
        'task_sheet_sha256': sha('offline recovered sheet fixture'),
        'original_question_sha256': sha(QUESTION),
        'original_history': {'planning_attempts': 2, 'route_attempts': 2},
    }


def prompt(snapshot='first computed snapshot'):
    return {
        'system_prompt': 'fixture revision request',
        'user_payload': {
            'task_sheet': {'original_question': QUESTION},
            'identity_scope': {'snapshot_manifest_sha256': sha(snapshot)},
            'certified_designs': [{'design_id': 'fixture-parent', 'available_actions': ['refine_family']}],
        },
    }


def make_guard(**overrides):
    planner, audits = Planner(), []
    options = {
        'case_id': CASE, 'audit_sink': audits.append,
        'recovered_task_sheet_binding': binding(),
        'continuation_task_id': 'R3-T040',
        'prior_dispatch_attempts': 4, 'cumulative_dispatch_limit': 6,
    }
    options.update(overrides)
    return RecoveredRevisionPlannerGuard(planner, **options), planner, audits


def test_two_distinct_revision_snapshots_preserve_historical_four_and_cap_at_six():
    guard, planner, audits = make_guard()
    assert guard.audit['stage_counts'] == {'search_revision': 0}
    assert guard.audit['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 4
    guard.set_stage('search_revision')
    for snapshot in ['first computed snapshot', 'new physical feedback snapshot']:
        original = prompt(snapshot)
        before = copy.deepcopy(original)
        assert guard._call_query_plan_model(original) == {'plans': []}
        assert original == before
    assert len(planner.calls) == len(audits) == 2
    assert guard.audit['dispatch_attempts'] == 2
    assert guard.audit['stage_counts'] == {'search_revision': 2}
    assert not guard.audit['shared_contract_repair_used']
    assert all(not event['contract_repair'] for event in guard.audit['events'])
    budget = guard.audit['recovered_attempt_budget']
    assert budget['prior_dispatch_attempts'] == 4
    assert budget['cumulative_dispatch_limit'] == 6
    assert budget['continuation_dispatch_limit'] == 2
    assert budget['cumulative_conservative_dispatch_attempts'] == 6
    before_third = guard.audit
    with pytest.raises(ValueError, match='stage dispatch limit'):
        guard._call_query_plan_model(prompt('third snapshot'))
    assert guard.audit == before_third
    assert len(planner.calls) == len(audits) == 2
    assert [row['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts']
            for row in audits] == [5, 6]


def test_revision_binding_and_forwarded_prompt_are_durable_before_delegation():
    planner, audits = Planner(), []
    recovery = binding()
    original_recovery = copy.deepcopy(recovery)

    def persist(snapshot):
        assert len(planner.calls) == len(audits)
        assert snapshot['recovered_task_sheet_binding'] == original_recovery
        assert snapshot['revision_continuation']['task_id'] == 'R3-T040'
        assert snapshot['revision_continuation']['allowed_stage'] == 'search_revision'
        assert snapshot['revision_continuation']['snapshot_manifest_sha256'] == [sha('first computed snapshot')]
        assert snapshot['events'][-1]['status'] == 'dispatch_attempt_recorded'
        audits.append(snapshot)

    guard = RecoveredRevisionPlannerGuard(
        planner, case_id=CASE, audit_sink=persist, recovered_task_sheet_binding=recovery,
        continuation_task_id='R3-T040', prior_dispatch_attempts=4, cumulative_dispatch_limit=6)
    recovery['original_history'].clear()
    guard.set_stage('search_revision')
    original = prompt()
    guard._call_query_plan_model(original)
    event = audits[0]['events'][0]
    assert event['original_prompt'] == original
    assert event['forwarded_prompt'] == planner.calls[0]
    assert event['forwarded_prompt_sha256'] == sha(json.dumps(
        planner.calls[0], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
    assert planner.calls[0]['user_payload']['task_sheet'] == original['user_payload']['task_sheet']
    assert planner.calls[0]['user_payload']['execution_protocol']['stage'] == 'search_revision'
    assert guard.last_usage is planner.last_usage
    assert guard.last_transport is planner.last_transport
    audits[0]['revision_continuation']['snapshot_manifest_sha256'].clear()
    guard.audit['revision_continuation']['snapshot_manifest_sha256'].clear()
    assert guard.audit['revision_continuation']['snapshot_manifest_sha256'] == [sha('first computed snapshot')]
    assert guard.audit['recovered_task_sheet_binding'] == original_recovery


@pytest.mark.parametrize('stage', ['task_sheet', 'route_generation', 'repair', 'unknown', None])
def test_non_revision_stage_is_blocked_before_dispatch(stage):
    guard, planner, audits = make_guard()
    with pytest.raises(ValueError):
        guard.set_stage(stage)
    assert not planner.calls and not audits
    assert guard.audit['dispatch_attempts'] == 0


def test_stage_must_be_started_once_and_cannot_reset_after_one_revision():
    guard, planner, audits = make_guard()
    with pytest.raises(ValueError, match='active admitted stage'):
        guard._call_query_plan_model(prompt())
    guard.set_stage('search_revision')
    with pytest.raises(ValueError, match='cannot reset, repeat, reverse, or skip'):
        guard.set_stage('search_revision')
    guard._call_query_plan_model(prompt())
    with pytest.raises(ValueError, match='cannot reset, repeat, reverse, or skip'):
        guard.set_stage('search_revision')
    assert len(planner.calls) == len(audits) == 1
    assert not guard.audit['failed_closed']  # Stopping after one call is permitted.


@pytest.mark.parametrize('location', ['top', 'payload'])
@pytest.mark.parametrize('key', ['previous_response', 'validation_error'])
def test_contract_repair_markers_are_rejected_even_on_first_call(location, key):
    guard, planner, audits = make_guard()
    guard.set_stage('search_revision')
    request = prompt()
    target = request if location == 'top' else request['user_payload']
    target[key] = 'fixture repair input'
    with pytest.raises(ValueError, match='does not admit contract repair'):
        guard._call_query_plan_model(request)
    assert not planner.calls and not audits
    assert guard.audit['revision_continuation']['snapshot_manifest_sha256'] == []


def test_same_snapshot_cannot_use_second_allowance_as_retry():
    guard, planner, audits = make_guard()
    guard.set_stage('search_revision')
    guard._call_query_plan_model(prompt())
    another_wording = prompt()
    another_wording['system_prompt'] = 'Changed wording does not constitute new physical feedback.'
    with pytest.raises(ValueError, match='snapshot already dispatched'):
        guard._call_query_plan_model(another_wording)
    assert len(planner.calls) == len(audits) == 1
    guard._call_query_plan_model(prompt('new measured snapshot'))
    assert len(planner.calls) == 2


@pytest.mark.parametrize('identity', [None, [], {}, {'snapshot_manifest_sha256': 'unknown'},
                                      {'snapshot_manifest_sha256': 'A' * 64}])
def test_unknown_request_without_revision_snapshot_identity_is_rejected(identity):
    guard, planner, audits = make_guard()
    guard.set_stage('search_revision')
    request = prompt()
    request['user_payload']['identity_scope'] = identity
    with pytest.raises(ValueError, match='snapshot_manifest_sha256'):
        guard._call_query_plan_model(request)
    assert not planner.calls and not audits


@pytest.mark.parametrize('sheet', [None, {}, {'original_question': QUESTION.replace('\n  ', '\n ')},
                                 {'original_question': QUESTION + ' changed'}, {'original_question': 4}])
def test_changed_or_missing_original_question_is_rejected(sheet):
    guard, planner, audits = make_guard()
    guard.set_stage('search_revision')
    request = prompt()
    request['user_payload']['task_sheet'] = sheet
    with pytest.raises(ValueError, match='original_question does not match'):
        guard._call_query_plan_model(request)
    assert not planner.calls and not audits


@pytest.mark.parametrize('missing', ['continuation_task_id', 'prior_dispatch_attempts', 'cumulative_dispatch_limit'])
def test_new_work_order_and_history_cannot_be_omitted(missing):
    options = dict(case_id=CASE, audit_sink=[].append, recovered_task_sheet_binding=binding(),
                   continuation_task_id='R3-T040', prior_dispatch_attempts=4, cumulative_dispatch_limit=6)
    del options[missing]
    with pytest.raises(TypeError):
        RecoveredRevisionPlannerGuard(Planner(), **options)


@pytest.mark.parametrize('overrides', [
    {'continuation_task_id': None}, {'continuation_task_id': '  '},
    {'prior_dispatch_attempts': None}, {'prior_dispatch_attempts': True},
    {'prior_dispatch_attempts': -1}, {'prior_dispatch_attempts': 4.0},
    {'cumulative_dispatch_limit': None}, {'cumulative_dispatch_limit': True},
    {'cumulative_dispatch_limit': 6.0}, {'cumulative_dispatch_limit': 4},
    {'recovered_task_sheet_binding': {**binding(), 'original_question_sha256': None}},
    {'recovered_task_sheet_binding': {**binding(), 'original_question_sha256': 'A' * 64}},
])
def test_invalid_history_or_question_binding_fails_at_construction(overrides):
    with pytest.raises(ValueError):
        make_guard(**overrides)


@pytest.mark.parametrize('ceiling, allowed', [(5, 1), (6, 2), (100, 2)])
def test_explicit_cumulative_ceiling_only_tightens_the_two_new_call_cap(ceiling, allowed):
    guard, planner, audits = make_guard(cumulative_dispatch_limit=ceiling)
    guard.set_stage('search_revision')
    for number in range(allowed):
        guard._call_query_plan_model(prompt(str(number)))
    with pytest.raises(ValueError):
        guard._call_query_plan_model(prompt('one too many'))
    assert len(planner.calls) == len(audits) == allowed
    assert guard.audit['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 4 + allowed


@pytest.mark.parametrize('failure_call', [1, 2])
def test_transport_failure_preserves_attempt_and_snapshot_history_and_blocks_all_retry(failure_call):
    guard, planner, audits = make_guard()
    guard.set_stage('search_revision')
    if failure_call == 2:
        guard._call_query_plan_model(prompt())
    planner.fail = True
    with pytest.raises(RuntimeError):
        guard._call_query_plan_model(prompt('failing snapshot'))
    assert guard.audit['failed_closed']
    assert guard.audit['events'][-1]['status'] == 'failed_closed'
    assert guard.audit['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 4 + failure_call
    with pytest.raises(ValueError, match='failed guards cannot retry'):
        guard._call_query_plan_model(prompt('different snapshot cannot reopen failed guard'))
    with pytest.raises(ValueError, match='failed closed'):
        guard.set_stage('search_revision')
    assert len(planner.calls) == len(audits) == failure_call


def test_audit_failure_blocks_delegation_and_keeps_conservative_fifth_attempt():
    def fail(snapshot):
        assert snapshot['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 5
        assert snapshot['revision_continuation']['snapshot_manifest_sha256'] == [sha('first computed snapshot')]
        raise OSError('fixture audit failure')

    guard, planner, _ = make_guard(audit_sink=fail)
    guard.set_stage('search_revision')
    with pytest.raises(OSError):
        guard._call_query_plan_model(prompt())
    assert not planner.calls
    assert guard.audit['failed_closed']
    assert guard.audit['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 5
    with pytest.raises(ValueError):
        guard._call_query_plan_model(prompt('new snapshot'))


def test_prompt_preflight_rejection_does_not_consume_a_snapshot_or_attempt():
    guard, planner, audits = make_guard()
    guard.set_stage('search_revision')
    request = prompt()
    request['user_payload']['execution_protocol'] = {}
    with pytest.raises(ValueError, match='execution_protocol collision'):
        guard._call_query_plan_model(request)
    assert guard.audit['revision_continuation']['snapshot_manifest_sha256'] == []
    assert guard.audit['dispatch_attempts'] == 0
    assert not audits and not planner.calls
    guard._call_query_plan_model(prompt())
    assert len(planner.calls) == 1


def test_original_and_t038_guards_still_require_their_original_stage_orders():
    for cls, options in [
        (CampaignPlannerGuard, {}),
        (RecoveredCampaignPlannerGuard, {'recovered_task_sheet_binding': binding(),
                                        'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': 4}),
    ]:
        planner = Planner()
        guard = cls(planner, case_id=CASE, audit_sink=[].append, **options)
        with pytest.raises(ValueError, match='cannot reset, repeat, reverse, or skip'):
            guard.set_stage('search_revision')
        assert not planner.calls


def test_pinned_case04_recovery_manifest_uses_native_question_hash_without_newline_rewriting(tmp_path):
    from focused_research import task_sheet_recovery as recovery, search_stage as st

    saved = Path(__file__).parent / 'fixtures/t037_case04_planning.json'
    source = json.loads(saved.read_text(encoding='utf-8'))
    question = source['rounds'][1]['effective_task_sheet']['original_question']
    recovered = recovery.recover_planning(
        CASE, question, saved, expected_sha256=recovery.UV_PLANNING_SHA256,
        output=tmp_path / 'recovered', round_number=2)
    sheet = st.load(tmp_path / 'recovered/task_sheet.json')
    assert st.digest(sheet) == recovered['task_sheet_sha256']
    assert sha(question) == recovered['original_question_sha256'] == recovery.UV_QUESTION_SHA256
    guard, planner, audits = make_guard(recovered_task_sheet_binding=recovered)
    guard.set_stage('search_revision')
    request = prompt()
    request['user_payload']['task_sheet'] = {'original_question': sheet['original_question']}
    guard._call_query_plan_model(request)
    assert len(planner.calls) == 1
    assert audits[0]['recovered_task_sheet_binding'] == recovered
    assert audits[0]['events'][0]['forwarded_prompt']['user_payload']['task_sheet']['original_question'] == question
