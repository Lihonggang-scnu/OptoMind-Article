"""Case12 admission and prompt preservation only; no model, solver, or billing."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from focused_research.local_campaign_guard import (
    CASE_PROTOCOLS, CASE_THICKNESS_BOUNDS, CampaignPlannerGuard,
    check_route_thickness_bounds,
)


CASE = 'e2e-12-lwir-ge-window'
REPLAY = Path(__file__).resolve().parents[2] / 'replay_data/runs/e2e-12-lwir-ge-window.json'
# Computed from summary.question at original source commit 032bd651. This is
# the native string hash, not a question text file with a newly added newline.
QUESTION_SHA256 = '9471a83b6c3ab5490fcb8f3ca8c414aba698f961a69b2ea05b3376df5ac66aea'


def original_question():
    return json.loads(REPLAY.read_text(encoding='utf-8'))['summary']['question']


def objectives():
    return [
        {'objective_id': 'G1', 'metric': 'mean_transmittance', 'sense': 'maximize',
         'mean_reduction': 'wavelength_integral_mean',
         'region': {'wavelength_nm': [8000, 11000], 'angle_deg': 0, 'polarization': 'unpolarized'}},
        {'objective_id': 'G2', 'metric': 'mean_transmittance', 'sense': 'minimize',
         'mean_reduction': 'wavelength_integral_mean',
         'region': {'wavelength_nm': [3000, 5000], 'angle_deg': 0, 'polarization': 'unpolarized'}},
    ]


def prompt(repair=False):
    # The explicit objectives are an offline execution fixture. They are not
    # imported from the replay's winner, scoring formula, or historical routes.
    payload = {'original_question': original_question(), 'objectives': objectives()}
    if repair:
        payload.update(previous_response='fixture invalid contract', validation_error='fixture validation failure')
    return {'system_prompt': 'fixture original system prompt', 'user_payload': payload}


class Planner:
    last_usage = {'source': 'original transport fixture'}
    last_transport = {'finish_reason': 'stop'}

    def __init__(self, audits):
        self.calls = []
        self.audits = audits

    def _call_query_plan_model(self, request):
        # A durable sink must have received the current full request first.
        assert len(self.audits) == len(self.calls) + 1
        assert self.audits[-1]['events'][-1]['forwarded_prompt'] == request
        self.calls.append(copy.deepcopy(request))
        return {'fixture_response': 'unchanged'}


def make_guard():
    audits = []
    planner = Planner(audits)
    guard = CampaignPlannerGuard(planner, case_id=CASE, audit_sink=audits.append)
    return guard, planner, audits


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def test_original_case12_question_is_byte_pinned_and_keeps_thermal_motivation():
    question = original_question()
    assert hashlib.sha256(question.encode('utf-8')).hexdigest() == QUESTION_SHA256
    assert '8000-11000 nm' in question and '3000-5000 nm' in question
    assert '以降低焦平面的热负载' in question
    assert '空气入射、锗（Ge）基底' in question
    assert '仅使用 ZnS 和 ZnSe' in question
    assert '不超过 16 层' in question


def test_each_original_stage_forwards_question_and_two_transmission_goals_without_rewriting():
    guard, planner, audits = make_guard()
    for stage in ('task_sheet', 'route_generation', 'search_revision'):
        guard.set_stage(stage)
        request = prompt()
        before = copy.deepcopy(request)
        assert guard._call_query_plan_model(request) == {'fixture_response': 'unchanged'}
        assert request == before
        actual = planner.calls[-1]
        expected = copy.deepcopy(before)
        expected['user_payload']['execution_protocol'] = {
            'case_id': CASE, 'stage': stage,
            'original_question_policy': 'Preserve original_question verbatim; these assumptions are separate from original user constraints.',
            **copy.deepcopy(CASE_PROTOCOLS[CASE]),
        }
        assert actual == expected
        assert actual['user_payload']['objectives'] == objectives()
        assert hashlib.sha256(actual['user_payload']['original_question'].encode()).hexdigest() == QUESTION_SHA256
        event = audits[-1]['events'][-1]
        assert event['original_prompt'] == before
        assert event['forwarded_prompt'] == actual
        assert event['original_prompt_sha256'] == digest(before)
        assert event['forwarded_prompt_sha256'] == digest(actual)
        assert event['status'] == 'dispatch_attempt_recorded'
    assert guard.audit['dispatch_attempts'] == len(planner.calls) == 3
    assert guard.last_usage is planner.last_usage
    assert guard.last_transport is planner.last_transport


def test_protocol_keeps_physical_assumptions_separate_from_original_user_facts():
    assumptions = CASE_PROTOCOLS[CASE]['assumptions']
    assert assumptions == [
        'Use 0 degree incidence, equal-s/p unpolarized light and wavelength-integral band means as separately labeled execution assumptions, not original user facts.',
        'Treat the Ge substrate as a semi-infinite exit medium for the planar-stack calculation; disclose this boundary assumption.',
    ]


def test_protocol_preserves_goals_materials_layer_limit_and_numerical_scope():
    requirements = CASE_PROTOCOLS[CASE]['requirements']
    assert 'Maximize mean transmittance in 8000–11000 nm and minimize mean transmittance in 3000–5000 nm. Preserve air incidence, Ge substrate, only ZnS/ZnSe film materials and at most 16 layers. Do not replace either transmission goal with reflection or absorption objectives.' in requirements
    assert 'T_into_exit is optical flux entering the semi-infinite Ge substrate, not transmission through an entire finite-thickness window including its rear surface. Keep this scope explicit in results.' in requirements
    assert 'For any optical-constant source without extinction coefficient k, disclose the existing provider k=0 convention. Missing source k does not establish physically absent absorption.' in requirements
    assert 'Temperature, substrate thickness and rear-surface conditions were not supplied; do not invent them. Preserve thermal-background and focal-plane heat-load reduction as application motivation, without adding heat-load, blackbody-power or temperature-weighted objectives or claiming those quantities were calculated.' in requirements


@pytest.mark.parametrize('repair_stage', ['task_sheet', 'route_generation'])
def test_cold_start_keeps_four_attempts_with_one_shared_repair_and_reserved_revision(repair_stage):
    guard, planner, audits = make_guard()
    for stage in ('task_sheet', 'route_generation', 'search_revision'):
        guard.set_stage(stage)
        guard._call_query_plan_model(prompt())
        if stage == repair_stage:
            guard._call_query_plan_model(prompt(repair=True))
        elif stage == 'route_generation' and repair_stage == 'task_sheet':
            with pytest.raises(ValueError, match='shared contract repair already used'):
                guard._call_query_plan_model(prompt(repair=True))
    with pytest.raises(ValueError, match='stage dispatch limit'):
        guard._call_query_plan_model(prompt(repair=True))
    assert guard.audit['dispatch_attempts'] == len(planner.calls) == len(audits) == 4
    assert sum(event['contract_repair'] for event in guard.audit['events']) == 1
    assert guard.audit['stage_counts']['search_revision'] == 1
    for request in planner.calls:
        assert request['user_payload']['objectives'] == objectives()
        assert request['user_payload']['original_question'] == original_question()


def test_case12_cannot_skip_task_sheet_or_restart_stage_order():
    guard, planner, audits = make_guard()
    for stage in ['route_generation', 'search_revision', 'unknown']:
        with pytest.raises(ValueError):
            guard.set_stage(stage)
    guard.set_stage('task_sheet')
    with pytest.raises(ValueError):
        guard.set_stage('route_generation')
    guard._call_query_plan_model(prompt())
    with pytest.raises(ValueError):
        guard.set_stage('task_sheet')
    assert len(planner.calls) == len(audits) == 1


def route(bounds, fixed):
    return {'id': 'fixture_route', 'family': {
        'thickness_variables_nm': {'d': bounds},
        'layers': [{'material': 'ZnS', 'thickness_nm': 'd'},
                   {'material': 'ZnSe', 'thickness_nm': fixed}],
    }}


@pytest.mark.parametrize('bounds,fixed', [([1, 100000], 12000), ([0.01, 0.02], 0.01), ([400, 400], 400)])
def test_case12_has_no_invented_global_thickness_bounds_or_clipping(bounds, fixed):
    assert CASE_THICKNESS_BOUNDS[CASE] is None
    rows = [route(bounds, fixed)]
    before = copy.deepcopy(rows)
    report = check_route_thickness_bounds(rows, case_id=CASE)
    assert report['explicit_user_bounds_nm'] is None
    assert report['route_results'][0]['accepted']
    assert not report['mutated']
    assert rows == before
    assert 'No global user thickness bounds were specified. Use finite positive model-proposed family domains without inventing manufacturing constraints.' in CASE_PROTOCOLS[CASE]['requirements']


@pytest.mark.parametrize('bounds,fixed', [([0, 100], 10), ([-1, 100], 10), ([20, 10], 10),
                                       ([1, float('inf')], 10), ([True, 10], 10),
                                       ([1, 10], 0), ([1, 10], float('nan'))])
def test_absence_of_user_bounds_does_not_relax_positive_finite_domain_validation(bounds, fixed):
    report = check_route_thickness_bounds([route(bounds, fixed)], case_id=CASE)
    assert not report['route_results'][0]['accepted']


def test_other_cases_keep_existing_thickness_policy():
    assert CASE_THICKNESS_BOUNDS['e2e-09-si-photonics-ar-dualband'] == (20.0, 400.0)
    assert CASE_THICKNESS_BOUNDS['e2e-08-advanced-packaging-uv'] == (20.0, 350.0)
    for case in ('e2e-02-space-qkd-cband', 'e2e-01-methane-swir-window',
                 'e2e-04-solarblind-uv-triple', 'e2e-05-visual-absorber-cr'):
        assert CASE_THICKNESS_BOUNDS[case] is None
