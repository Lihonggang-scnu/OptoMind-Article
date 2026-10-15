import copy
import hashlib
import json

import pytest

from focused_research.local_campaign_guard import (
    CASE_PROTOCOLS, CampaignPlannerGuard, RecoveredCampaignPlannerGuard,
    check_route_thickness_bounds,
)

AR = 'e2e-09-si-photonics-ar-dualband'
UV = 'e2e-08-advanced-packaging-uv'
QKD = 'e2e-02-space-qkd-cband'


class Planner:
    last_usage = {'actual': 'delegated'}
    last_transport = {'transport': 'original'}
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail
    def _call_query_plan_model(self, prompt):
        self.calls.append(copy.deepcopy(prompt))
        if self.fail:
            raise RuntimeError('transport failed')
        return 'unchanged response'


def setup_guard(case_id=AR, fail=False):
    planner, snapshots = Planner(fail), []
    return CampaignPlannerGuard(planner, case_id=case_id, audit_sink=snapshots.append), planner, snapshots


def prompt(repair=False):
    payload = {'original_question': '原题\n  preserve spaces'}
    if repair:
        payload.update(previous_response='bad JSON', validation_error='invalid contract')
    return {'system_prompt': 'system', 'user_payload': payload, 'prompt_hash': 'upstream-system-only'}


def test_preserves_prompt_and_forwards_protocol_with_actual_hash_before_call():
    guard, planner, snapshots = setup_guard(QKD)
    original = prompt(); before = copy.deepcopy(original)
    guard.set_stage('task_sheet')
    assert guard._call_query_plan_model(original) == 'unchanged response'
    assert original == before
    actual = planner.calls[0]
    assert actual['user_payload']['original_question'] == before['user_payload']['original_question']
    assert actual['user_payload']['execution_protocol']['case_id'] == QKD
    event = snapshots[0]['events'][0]
    assert event['status'] == 'dispatch_attempt_recorded'
    assert event['forwarded_prompt'] == actual
    expected = hashlib.sha256(json.dumps(actual, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert event['forwarded_prompt_sha256'] == expected
    assert event['original_prompt'] == before
    assert event['original_prompt_sha256'] != expected
    assert guard.last_usage is planner.last_usage
    assert guard.last_transport is planner.last_transport
    guard.audit['events'].clear()
    assert len(guard.audit['events']) == 1


@pytest.mark.parametrize('repair_stage', ['task_sheet', 'route_generation'])
def test_four_dispatches_one_shared_repair_preserves_all_base_calls(repair_stage):
    guard, planner, snapshots = setup_guard()
    for stage in ['task_sheet', 'route_generation', 'search_revision']:
        guard.set_stage(stage)
        guard._call_query_plan_model(prompt())
        if stage == repair_stage:
            guard._call_query_plan_model(prompt(True))
        elif stage == 'route_generation' and repair_stage == 'task_sheet':
            with pytest.raises(ValueError, match='shared contract repair'):
                guard._call_query_plan_model(prompt(True))
    assert guard.audit['dispatch_attempts'] == len(planner.calls) == 4
    with pytest.raises(ValueError, match='stage dispatch limit'):
        guard._call_query_plan_model(prompt(True))
    assert guard.audit['dispatch_attempts'] == 4


def test_stage_reset_skip_unknown_and_unstarted_rejected():
    guard, planner, _ = setup_guard()
    with pytest.raises(ValueError): guard._call_query_plan_model(prompt())
    for invalid in ['route_generation', 'search_revision', 'unknown']:
        with pytest.raises(ValueError): guard.set_stage(invalid)
    guard.set_stage('task_sheet')
    for invalid in ['task_sheet', 'search_revision', 'route_generation']:
        with pytest.raises(ValueError): guard.set_stage(invalid)
    guard._call_query_plan_model(prompt())
    guard.set_stage('route_generation')
    with pytest.raises(ValueError): guard.set_stage('task_sheet')
    assert len(planner.calls) == 1


def test_second_dispatch_requires_validation_failure_and_no_third_attempt():
    guard, planner, _ = setup_guard()
    guard.set_stage('task_sheet'); guard._call_query_plan_model(prompt())
    with pytest.raises(ValueError, match='explicit contract'):
        guard._call_query_plan_model(prompt())
    guard._call_query_plan_model(prompt(True))
    with pytest.raises(ValueError, match='stage dispatch limit'):
        guard._call_query_plan_model(prompt(True))
    assert len(planner.calls) == 2


def test_transport_failure_charged_and_no_retry_or_stage_advance():
    guard, planner, snapshots = setup_guard(fail=True)
    guard.set_stage('task_sheet')
    with pytest.raises(RuntimeError): guard._call_query_plan_model(prompt())
    assert guard.audit['dispatch_attempts'] == 1
    assert snapshots[0]['dispatch_attempts'] == 1
    assert guard.audit['failed_closed']
    with pytest.raises(ValueError): guard._call_query_plan_model(prompt(True))
    with pytest.raises(ValueError): guard.set_stage('route_generation')
    assert len(planner.calls) == 1


def test_audit_failure_prevents_transport_and_consumes_conservative_attempt():
    planner = Planner()
    def fail(_): raise OSError('disk unavailable')
    guard = CampaignPlannerGuard(planner, case_id=AR, audit_sink=fail)
    guard.set_stage('task_sheet')
    with pytest.raises(OSError): guard._call_query_plan_model(prompt())
    assert not planner.calls
    assert guard.audit['dispatch_attempts'] == 1
    assert guard.audit['failed_closed']


@pytest.mark.parametrize('payload', [None, [], {'execution_protocol': {}}])
def test_malformed_or_colliding_prompt_does_not_dispatch(payload):
    guard, planner, _ = setup_guard(); guard.set_stage('task_sheet')
    with pytest.raises(ValueError): guard._call_query_plan_model({'user_payload': payload})
    assert not planner.calls
    assert guard.audit['dispatch_attempts'] == 0


def route(rid='R1', bounds=None, fixed=20):
    return {'id': rid, 'family': {'layers': [{'thickness_nm': 'd'}, {'thickness_nm': fixed}],
                                'thickness_variables_nm': {'d': [20, 400] if bounds is None else bounds}}}


def test_per_route_bounds_do_not_clip_or_mutate():
    rows = [route('ok'), route('low', [19, 100]), route('high', [20, 401]), route('fixed', fixed=401)]
    original = copy.deepcopy(rows)
    report = check_route_thickness_bounds({'routes': rows}, case_id=AR)
    assert [x['accepted'] for x in report['route_results']] == [True, False, False, False]
    assert rows == original
    assert not report['mutated']
    assert check_route_thickness_bounds([route(bounds=[20, 350], fixed=350)], case_id=UV)['route_results'][0]['accepted']
    assert not check_route_thickness_bounds([route(bounds=[20, 351])], case_id=UV)['route_results'][0]['accepted']


def test_qkd_has_no_invented_manufacturing_bounds():
    report = check_route_thickness_bounds([route(bounds=[1, 10000], fixed=9000)], case_id=QKD)
    assert report['explicit_user_bounds_nm'] is None
    assert report['route_results'][0]['accepted']


@pytest.mark.parametrize('bounds', [[0, 20], [30, 20], [20, float('inf')], [True, 30], ['20', 30], None])
def test_invalid_intervals_rejected(bounds):
    row = route(); row['family']['thickness_variables_nm']['d'] = bounds
    assert not check_route_thickness_bounds([row], case_id=AR)['route_results'][0]['accepted']


def test_missing_family_variable_and_invalid_fixed_values_rejected():
    rows = [None, {'id': 'missing'}, route(fixed=False), route(fixed=float('nan')), route(fixed=-1)]
    row = route(); row['family']['layers'][0]['thickness_nm'] = 'unknown'; rows.append(row)
    assert all(not x['accepted'] for x in check_route_thickness_bounds(rows, case_id=AR)['route_results'])


def test_unknown_case_and_missing_sink_rejected():
    with pytest.raises(ValueError): CampaignPlannerGuard(Planner(), case_id='unknown', audit_sink=list().append)
    with pytest.raises(ValueError): CampaignPlannerGuard(Planner(), case_id=AR, audit_sink=None)
    with pytest.raises(ValueError): check_route_thickness_bounds([], case_id='unknown')


def test_degenerate_fixed_variable_interval_matches_existing_route_contract():
    assert check_route_thickness_bounds([route(bounds=[20, 20])], case_id=AR)["route_results"][0]["accepted"]


def recovery_binding(case_id=AR):
    return {
        'case_id': case_id,
        'task_sheet_sha256': hashlib.sha256(b'offline recovered sheet fixture').hexdigest(),
        'source_planning_sha256': hashlib.sha256(b'saved planning fixture').hexdigest(),
        'source_round': 2,
        'new_model_calls': 0,
        'physics_executed': False,
        'status': 'recovered_for_continuation_not_scientific_acceptance',
        'provenance': {'source': ['saved response fixture']},
    }


def setup_recovered_guard(case_id=AR, fail=False):
    planner, snapshots = Planner(fail), []
    guard = RecoveredCampaignPlannerGuard(
        planner, case_id=case_id, audit_sink=snapshots.append,
        recovered_task_sheet_binding=recovery_binding(case_id))
    return guard, planner, snapshots


@pytest.mark.parametrize('case_id', [AR, UV, QKD])
def test_recovered_binding_and_exact_forwarded_prompt_persist_before_dispatch(case_id):
    binding = recovery_binding(case_id)
    original_binding = copy.deepcopy(binding)
    planner, snapshots = Planner(), []

    def persist_before_dispatch(snapshot):
        assert len(planner.calls) == 0
        assert snapshot['recovered_task_sheet_binding'] == original_binding
        assert snapshot['dispatch_attempts'] == 1
        snapshots.append(snapshot)

    guard = RecoveredCampaignPlannerGuard(
        planner, case_id=case_id, audit_sink=persist_before_dispatch,
        recovered_task_sheet_binding=binding)
    binding['task_sheet_sha256'] = '0' * 64
    binding['provenance']['source'].clear()
    assert guard.audit['recovered_task_sheet_binding'] == original_binding
    original = prompt()
    before = copy.deepcopy(original)
    guard.set_stage('route_generation')
    assert guard._call_query_plan_model(original) == 'unchanged response'
    assert original == before
    actual = planner.calls[0]
    expected = copy.deepcopy(before)
    expected['user_payload']['execution_protocol'] = {
        'case_id': case_id, 'stage': 'route_generation',
        'original_question_policy': 'Preserve original_question verbatim; these assumptions are separate from original user constraints.',
        **copy.deepcopy(CASE_PROTOCOLS[case_id]),
    }
    assert actual == expected
    event = snapshots[0]['events'][0]
    assert event['status'] == 'dispatch_attempt_recorded'
    assert event['original_prompt'] == before
    assert event['forwarded_prompt'] == actual
    for key, value in [('original_prompt_sha256', before), ('forwarded_prompt_sha256', actual)]:
        assert event[key] == hashlib.sha256(json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            allow_nan=False).encode()).hexdigest()
    snapshots[0]['recovered_task_sheet_binding']['provenance']['source'].clear()
    guard.audit['recovered_task_sheet_binding'].clear()
    assert guard.audit['recovered_task_sheet_binding'] == original_binding
    assert guard.last_usage is planner.last_usage
    assert guard.last_transport is planner.last_transport


def test_recovered_stage_order_excludes_task_sheet_and_cannot_restart_or_skip():
    guard, planner, _ = setup_recovered_guard()
    assert guard.audit['stage_counts'] == {'route_generation': 0, 'search_revision': 0}
    with pytest.raises(ValueError): guard._call_query_plan_model(prompt())
    for stage in ['task_sheet', 'search_revision', 'unknown']:
        with pytest.raises(ValueError): guard.set_stage(stage)
    guard.set_stage('route_generation')
    for stage in ['task_sheet', 'route_generation', 'search_revision']:
        with pytest.raises(ValueError): guard.set_stage(stage)
    guard._call_query_plan_model(prompt())
    guard.set_stage('search_revision')
    guard._call_query_plan_model(prompt())
    for stage in ['task_sheet', 'route_generation', 'search_revision']:
        with pytest.raises(ValueError): guard.set_stage(stage)
    assert len(planner.calls) == guard.audit['dispatch_attempts'] == 2


def test_recovered_max_three_attempts_reserves_revision_after_one_contract_repair():
    guard, planner, snapshots = setup_recovered_guard()
    guard.set_stage('route_generation')
    guard._call_query_plan_model(prompt())
    with pytest.raises(ValueError, match='explicit contract'):
        guard._call_query_plan_model(prompt())
    guard._call_query_plan_model(prompt(True))
    with pytest.raises(ValueError, match='stage dispatch limit'):
        guard._call_query_plan_model(prompt(True))
    guard.set_stage('search_revision')
    guard._call_query_plan_model(prompt())
    with pytest.raises(ValueError, match='stage dispatch limit'):
        guard._call_query_plan_model(prompt(True))
    audit = guard.audit
    assert len(planner.calls) == audit['dispatch_attempts'] == 3
    assert audit['stage_counts'] == {'route_generation': 2, 'search_revision': 1}
    assert audit['shared_contract_repair_used']
    assert [event['contract_repair'] for event in audit['events']] == [False, True, False]
    assert [snapshot['dispatch_attempts'] for snapshot in snapshots] == [1, 2, 3]
    assert all(snapshot['recovered_task_sheet_binding'] == recovery_binding() for snapshot in snapshots)


@pytest.mark.parametrize('failure_stage, repair', [
    ('route_generation', False), ('route_generation', True), ('search_revision', False),
])
def test_recovered_transport_failure_is_counted_and_failed_closed(failure_stage, repair):
    guard, planner, snapshots = setup_recovered_guard()
    guard.set_stage('route_generation')
    if failure_stage == 'search_revision' or repair:
        guard._call_query_plan_model(prompt())
    if failure_stage == 'search_revision':
        guard.set_stage('search_revision')
    planner.fail = True
    with pytest.raises(RuntimeError, match='transport failed'):
        guard._call_query_plan_model(prompt(repair))
    audit = guard.audit
    count = 2 if failure_stage == 'search_revision' or repair else 1
    assert audit['dispatch_attempts'] == len(planner.calls) == len(snapshots) == count
    assert audit['failed_closed']
    assert audit['events'][-1]['status'] == 'failed_closed'
    assert audit['events'][-1]['exception_type'] == 'RuntimeError'
    with pytest.raises(ValueError): guard._call_query_plan_model(prompt(True))
    for stage in ['route_generation', 'search_revision']:
        with pytest.raises(ValueError): guard.set_stage(stage)
    assert len(planner.calls) == count


def test_recovered_audit_failure_blocks_delegation_and_all_further_work():
    planner = Planner()
    def fail(snapshot):
        assert snapshot['recovered_task_sheet_binding'] == recovery_binding()
        raise OSError('disk unavailable')
    guard = RecoveredCampaignPlannerGuard(
        planner, case_id=AR, audit_sink=fail,
        recovered_task_sheet_binding=recovery_binding())
    guard.set_stage('route_generation')
    with pytest.raises(OSError): guard._call_query_plan_model(prompt())
    assert not planner.calls
    assert guard.audit['dispatch_attempts'] == 1
    assert guard.audit['failed_closed']
    with pytest.raises(ValueError): guard._call_query_plan_model(prompt(True))
    with pytest.raises(ValueError): guard.set_stage('search_revision')


@pytest.mark.parametrize('binding', [
    None, [], {}, {'case_id': AR}, recovery_binding(UV),
    {'case_id': AR, 'task_sheet_sha256': 'unverified'},
    {'case_id': AR, 'task_sheet_sha256': 'a' * 63},
    {'case_id': AR, 'task_sheet_sha256': 'g' * 64},
    {'case_id': AR, 'task_sheet_sha256': 'A' * 64},
    {'case_id': AR, 'task_sheet_sha256': 123},
    {**recovery_binding(), 'invalid': float('nan')},
    {**recovery_binding(), 'invalid': float('inf')},
    {**recovery_binding(), 'invalid': object()},
])
def test_recovered_invalid_binding_rejected_without_audit_or_dispatch(binding):
    planner, snapshots = Planner(), []
    with pytest.raises(ValueError):
        RecoveredCampaignPlannerGuard(
            planner, case_id=AR, audit_sink=snapshots.append,
            recovered_task_sheet_binding=binding)
    assert not snapshots
    assert not planner.calls


def test_recovered_requires_explicit_binding_known_case_and_durable_sink():
    with pytest.raises(TypeError):
        RecoveredCampaignPlannerGuard(Planner(), case_id=AR, audit_sink=list().append)
    with pytest.raises(ValueError, match='unknown campaign case'):
        RecoveredCampaignPlannerGuard(
            Planner(), case_id='unknown', audit_sink=list().append,
            recovered_task_sheet_binding=recovery_binding('unknown'))
    with pytest.raises(ValueError, match='durable audit_sink'):
        RecoveredCampaignPlannerGuard(
            Planner(), case_id=AR, audit_sink=None,
            recovered_task_sheet_binding=recovery_binding())


def test_original_campaign_keeps_three_stages_four_attempt_limit_and_audit_shape():
    guard, _, _ = setup_guard()
    assert guard.audit == {
        'case_id': AR, 'dispatch_attempts': 0,
        'stage_counts': {'task_sheet': 0, 'route_generation': 0, 'search_revision': 0},
        'shared_contract_repair_used': False, 'failed_closed': False, 'events': [],
    }
