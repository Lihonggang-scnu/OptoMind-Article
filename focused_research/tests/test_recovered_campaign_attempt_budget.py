"""Offline continuation limits; these fixtures do not measure HTTP or cost."""
import copy
import hashlib

import pytest

from focused_research.local_campaign_guard import RecoveredCampaignPlannerGuard


UV04 = 'e2e-04-solarblind-uv-triple'
T031_CASES = [
    'e2e-09-si-photonics-ar-dualband',
    'e2e-08-advanced-packaging-uv',
    'e2e-02-space-qkd-cband',
]


class Planner:
    def __init__(self):
        self.calls = []
        self.fail = False

    def _call_query_plan_model(self, prompt):
        self.calls.append(copy.deepcopy(prompt))
        if self.fail:
            raise RuntimeError('fixture transport failure')
        return 'fixture response'


def binding(case_id=UV04):
    return {
        'case_id': case_id,
        'task_sheet_sha256': hashlib.sha256(b'recovered sheet fixture').hexdigest(),
        'provenance': {'original_history': ['planning attempt', 'contract repair attempt']},
    }


def prompt(repair=False):
    payload = {'original_question': 'original question fixture'}
    if repair:
        payload.update(previous_response='invalid contract', validation_error='fixture error')
    return {'system_prompt': 'fixture system prompt', 'user_payload': payload}


def make_guard(*, case_id=UV04, **budget):
    planner, snapshots = Planner(), []
    guard = RecoveredCampaignPlannerGuard(
        planner, case_id=case_id, audit_sink=snapshots.append,
        recovered_task_sheet_binding=binding(case_id), **budget)
    return guard, planner, snapshots


def test_case04_two_prior_attempts_allow_only_route_and_reserved_revision():
    guard, planner, snapshots = make_guard(
        prior_dispatch_attempts=2, cumulative_dispatch_limit=4)
    assert guard.audit['dispatch_attempts'] == 0
    assert guard.audit['stage_counts'] == {'route_generation': 0, 'search_revision': 0}
    assert guard.audit['recovered_attempt_budget'] == {
        'prior_dispatch_attempts': 2,
        'cumulative_dispatch_limit': 4,
        'continuation_dispatch_limit': 2,
        'cumulative_conservative_dispatch_attempts': 2,
        'actual_http_and_costs_source': 'original_audited_transport_and_ledger',
    }
    with pytest.raises(ValueError, match='unknown stage'):
        guard.set_stage('task_sheet')
    with pytest.raises(ValueError, match='cannot reset, repeat, reverse, or skip'):
        guard.set_stage('search_revision')
    guard.set_stage('route_generation')
    original = prompt()
    before = copy.deepcopy(original)
    assert guard._call_query_plan_model(original) == 'fixture response'
    assert original == before
    before_rejected_repair = guard.audit
    with pytest.raises(ValueError, match='consume reserved base calls'):
        guard._call_query_plan_model(prompt(True))
    assert guard.audit == before_rejected_repair
    assert len(planner.calls) == len(snapshots) == 1
    guard.set_stage('search_revision')
    guard._call_query_plan_model(prompt())
    for request in [prompt(), prompt(True)]:
        with pytest.raises(ValueError, match='stage dispatch limit'):
            guard._call_query_plan_model(request)
    for stage in ['task_sheet', 'route_generation', 'search_revision']:
        with pytest.raises(ValueError):
            guard.set_stage(stage)
    assert len(planner.calls) == len(snapshots) == 2
    assert guard.audit['dispatch_attempts'] == 2
    assert not guard.audit['shared_contract_repair_used']
    assert guard.audit['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 4
    assert [row['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts']
            for row in snapshots] == [3, 4]
    assert [row['user_payload']['execution_protocol']['stage']
            for row in planner.calls] == ['route_generation', 'search_revision']


def test_budget_and_recovery_provenance_are_durable_before_delegate_and_isolated():
    planner, snapshots = Planner(), []
    source_binding = binding()
    original_binding = copy.deepcopy(source_binding)

    def persist(snapshot):
        assert len(planner.calls) == len(snapshots)
        assert snapshot['recovered_task_sheet_binding'] == original_binding
        assert snapshot['recovered_attempt_budget']['prior_dispatch_attempts'] == 2
        assert snapshot['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 3 + len(snapshots)
        assert snapshot['events'][-1]['status'] == 'dispatch_attempt_recorded'
        snapshots.append(snapshot)

    guard = RecoveredCampaignPlannerGuard(
        planner, case_id=UV04, audit_sink=persist,
        recovered_task_sheet_binding=source_binding,
        prior_dispatch_attempts=2, cumulative_dispatch_limit=4)
    source_binding['provenance']['original_history'].clear()
    guard.audit['recovered_attempt_budget']['prior_dispatch_attempts'] = 0
    guard.set_stage('route_generation')
    guard._call_query_plan_model(prompt())
    snapshots[0]['recovered_attempt_budget']['prior_dispatch_attempts'] = 0
    snapshots[0]['recovered_task_sheet_binding']['provenance']['original_history'].clear()
    guard.set_stage('search_revision')
    guard._call_query_plan_model(prompt())
    assert guard.audit['recovered_attempt_budget']['prior_dispatch_attempts'] == 2
    assert guard.audit['recovered_task_sheet_binding'] == original_binding
    assert [event['forwarded_prompt'] for event in guard.audit['events']] == planner.calls
    assert all('recovered_attempt_budget' not in request['user_payload'] for request in planner.calls)


@pytest.mark.parametrize('budget', [
    {'prior_dispatch_attempts': 2},
    {'cumulative_dispatch_limit': 4},
    {'prior_dispatch_attempts': True, 'cumulative_dispatch_limit': 4},
    {'prior_dispatch_attempts': False, 'cumulative_dispatch_limit': 4},
    {'prior_dispatch_attempts': -1, 'cumulative_dispatch_limit': 4},
    {'prior_dispatch_attempts': 2.0, 'cumulative_dispatch_limit': 4},
    {'prior_dispatch_attempts': '2', 'cumulative_dispatch_limit': 4},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': True},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': 4.0},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': '4'},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': float('inf')},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': float('nan')},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': -1},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': 0},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': 1},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': 2},
    {'prior_dispatch_attempts': 2, 'cumulative_dispatch_limit': 3},
])
def test_invalid_or_insufficient_cumulative_budget_fails_before_audit_or_delegate(budget):
    planner, snapshots = Planner(), []
    with pytest.raises(ValueError):
        RecoveredCampaignPlannerGuard(
            planner, case_id=UV04, audit_sink=snapshots.append,
            recovered_task_sheet_binding=binding(), **budget)
    assert not planner.calls
    assert not snapshots


@pytest.mark.parametrize('case_id', T031_CASES)
def test_legacy_t031_default_retains_three_attempts_and_original_audit_shape(case_id):
    # A stricter instance must not lower the class/default allowance.
    make_guard(prior_dispatch_attempts=2, cumulative_dispatch_limit=4)
    guard, planner, snapshots = make_guard(case_id=case_id)
    guard.set_stage('route_generation')
    guard._call_query_plan_model(prompt())
    guard._call_query_plan_model(prompt(True))
    guard.set_stage('search_revision')
    guard._call_query_plan_model(prompt())
    assert len(planner.calls) == 3
    assert guard.audit['shared_contract_repair_used']
    assert 'recovered_attempt_budget' not in guard.audit
    assert all('recovered_attempt_budget' not in snapshot for snapshot in snapshots)


@pytest.mark.parametrize('prior, ceiling', [(0, 3), (1, 4), (2, 5), (2, 100)])
def test_explicit_budget_never_expands_original_three_continuation_attempt_limit(prior, ceiling):
    guard, planner, _ = make_guard(
        prior_dispatch_attempts=prior, cumulative_dispatch_limit=ceiling)
    guard.set_stage('route_generation')
    guard._call_query_plan_model(prompt())
    guard._call_query_plan_model(prompt(True))
    with pytest.raises(ValueError, match='stage dispatch limit'):
        guard._call_query_plan_model(prompt(True))
    guard.set_stage('search_revision')
    guard._call_query_plan_model(prompt())
    with pytest.raises(ValueError, match='stage dispatch limit'):
        guard._call_query_plan_model(prompt(True))
    budget = guard.audit['recovered_attempt_budget']
    assert len(planner.calls) == budget['continuation_dispatch_limit'] == 3
    assert budget['cumulative_conservative_dispatch_attempts'] == prior + 3 <= ceiling


@pytest.mark.parametrize('failure_stage', ['route_generation', 'search_revision'])
def test_transport_failure_keeps_prior_attempts_and_blocks_retry_or_advance(failure_stage):
    guard, planner, snapshots = make_guard(
        prior_dispatch_attempts=2, cumulative_dispatch_limit=4)
    guard.set_stage('route_generation')
    if failure_stage == 'search_revision':
        guard._call_query_plan_model(prompt())
        guard.set_stage('search_revision')
    planner.fail = True
    with pytest.raises(RuntimeError, match='fixture transport failure'):
        guard._call_query_plan_model(prompt())
    count = 1 if failure_stage == 'route_generation' else 2
    assert guard.audit['failed_closed']
    assert guard.audit['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 2 + count
    with pytest.raises(ValueError):
        guard._call_query_plan_model(prompt(True))
    with pytest.raises(ValueError):
        guard.set_stage('search_revision')
    assert len(planner.calls) == len(snapshots) == count


def test_failed_audit_consumes_conservative_allowance_without_claiming_actual_http():
    planner = Planner()

    def fail_audit(snapshot):
        assert snapshot['dispatch_attempts'] == 1
        assert snapshot['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 3
        raise OSError('fixture disk failure')

    guard = RecoveredCampaignPlannerGuard(
        planner, case_id=UV04, audit_sink=fail_audit,
        recovered_task_sheet_binding=binding(),
        prior_dispatch_attempts=2, cumulative_dispatch_limit=4)
    guard.set_stage('route_generation')
    with pytest.raises(OSError, match='fixture disk failure'):
        guard._call_query_plan_model(prompt())
    assert not planner.calls
    assert guard.audit['failed_closed']
    assert guard.audit['recovered_attempt_budget']['cumulative_conservative_dispatch_attempts'] == 3
    assert guard.audit['recovered_attempt_budget']['actual_http_and_costs_source'] == 'original_audited_transport_and_ledger'
    with pytest.raises(ValueError):
        guard._call_query_plan_model(prompt(True))
    with pytest.raises(ValueError):
        guard.set_stage('search_revision')
