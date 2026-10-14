"""Offline replay of T010 topology and authoritative-objective boundaries."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest

from optomind_optics.harness.route_planning import QwenLiteratureRoutePlanner, LiteratureHarvest
from optomind_optics.harness.scoring_standard import ScoringStandard
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler, _original_request_source
from optomind_optics.harness.research_orchestrator import TMMResearchHarness
from optomind_optics.harness.route_planning import RoutePlanResult


FIXTURE = Path(__file__).parent / 'fixtures/t010_route_contract/lidar.json'
MARKER = 'Immutable original user request (overrides conflicting route proposals):'


class ReplayClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.payloads = []

    def call(self, messages, **kwargs):
        self.payloads.append(json.loads(messages[-1]['content']))
        return {'content': next(self.replies)}


def captured(case='lidar'):
    return json.loads(FIXTURE.with_name(case + '.json').read_text(encoding='utf-8'))


def wrapped(route, original):
    return route + '\n\n' + MARKER + '\n' + original


def test_captured_literature_route_gets_existing_topology_normalization(monkeypatch):
    data = captured()
    proposal = {'routes': [data['iterations']['iteration_01']['route']]}
    client = ReplayClient([json.dumps(proposal)])
    planner = QwenLiteratureRoutePlanner(client, minimum_routes=1, maximum_routes=1)
    monkeypatch.setattr(planner, '_resolved_material_catalog', lambda: None)
    result = planner.propose_routes(data['question'], LiteratureHarvest(),
                                    problem_analysis=data['problem_analysis'])
    assert result.status == 'planned', result.validation_errors
    assert result.attempts == 1
    route = result.plan['routes'][0]
    assert '_N' not in route['proposed_topology']
    assert 'Topology realization override:' in route['execution_request_english']
    assert any('symbolic topology' in warning for warning in result.warnings)
    assert result.sidecar()['plan'] == result.plan
    assert result.sidecar()['warnings'] == list(result.warnings)
    assert len(client.payloads) == 1


def test_requested_layer_count_range_stays_distributed_across_five_routes():
    data = captured()
    routes = []
    for index in range(5):
        route = copy.deepcopy(data['iterations']['iteration_01']['route'])
        route.update(route_id=f'route_{index}', priority=index + 1,
                     design_variables=[f'layer thickness axis {index}'])
        routes.append(route)
    planner = QwenLiteratureRoutePlanner(ReplayClient([]), maximum_routes=5)
    problem = {**data['problem_analysis'], 'design_variables': ['layer count between 10 and 20']}
    accepted, _, errors, warnings, _ = planner._verify_routes(
        {'routes': routes}, allowed_labels=(), catalog=None, problem_analysis=problem)
    assert len(accepted) == 5, errors
    assert len({route['proposed_topology'] for route in accepted}) == 5
    assert 'exactly 10 finite layers' in accepted[0]['proposed_topology']
    assert 'exactly 20 finite layers' in accepted[2]['proposed_topology']


@pytest.mark.parametrize('case,iteration', [
    ('lidar', 'iteration_02'), ('lidar', 'iteration_03'), ('rgb', 'iteration_03')])
def test_captured_draft_compiles_without_changing_its_targets(case, iteration):
    data = captured(case)
    saved = data['iterations'][iteration]
    standard = ScoringStandard.model_validate(data['standard'])
    before = standard.model_dump(mode='json')
    client = ReplayClient([saved['rejected_draft']])
    compiler = QwenTMMTaskCompiler(client=client, scoring_standard=standard,
                                   maximum_attempts=1, transport_retries=0)
    result = compiler.compile(wrapped(saved['route']['execution_request_english'], data['question']))
    assert saved['baseline_status'] == 'invalid'
    assert any('frozen scoring standard contradicts request' in x
               for x in saved['baseline_validation_errors'])
    assert result.status == 'compiled', result.validation_errors
    raw = json.loads(saved['rejected_draft'])
    for experiment, original in zip(result.task.experiments, raw['experiments']):
        # No T->R substitution or loss of route-local optimizer objectives.
        assert experiment.tmm_task['targets'] == original['tmm_task']['targets']
        fixed_ids = {p.objective_id for p in standard.objective_preferences()}
        assert fixed_ids <= {p.objective_id for p in experiment.objectives}
    assert standard.model_dump(mode='json') == before
    assert compiler._frozen_objective is None
    assert client.payloads[0]['frozen_scoring_standard'] == before


def test_captured_missing_optimizer_target_stays_invalid_and_repair_sees_contract():
    data = captured('rgb')
    bad = data['iterations']['iteration_01']
    standard = ScoringStandard.model_validate(data['standard'])
    client = ReplayClient([bad['rejected_draft']] * 2)
    result = QwenTMMTaskCompiler(client=client, scoring_standard=standard,
                                transport_retries=0).compile(
        wrapped(bad['route']['execution_request_english'], data['question']))
    assert result.status == 'invalid'
    assert any('requires at least one target' in e for e in result.validation_errors)
    assert len(client.payloads) == 2
    for payload in client.payloads:
        assert payload['frozen_scoring_standard'] == data['standard']
        assert 'nonempty executable targets' in payload['fixed_rules']['frozen_scoring_contract']


def test_frozen_contract_survives_compiler_repair_and_real_user_conflict_still_fails():
    data = captured()
    saved = data['iterations']['iteration_03']
    standard = ScoringStandard.model_validate(data['standard'])
    before = standard.model_dump(mode='json')
    client = ReplayClient([saved['rejected_draft']] * 2)
    compiler = QwenTMMTaskCompiler(client=client, scoring_standard=standard,
                                   maximum_attempts=2, transport_retries=0)
    original = data['question'] + '\nMinimize reflectance over 1400-1500 nm.'
    result = compiler.compile(wrapped(saved['route']['execution_request_english'], original))
    assert result.status == 'invalid'
    assert any('frozen scoring standard contradicts request' in x for x in result.validation_errors)
    assert len(client.payloads) == 2
    assert all(p['frozen_scoring_standard'] == before for p in client.payloads)
    assert 'existing_draft' in client.payloads[1]
    assert standard.model_dump(mode='json') == before


@pytest.mark.parametrize('text', ['', 'Maximize R over 500-600 nm.', 'A normal standalone user request'])
def test_unwrapped_original_request_is_unchanged(text):
    assert _original_request_source(text) == text


def test_route_cannot_override_the_later_authoritative_original_request():
    route = MARKER + '\nMinimize R over 500-600 nm.\nInvented route conditions.'
    original = 'Maximize R over 500-600 nm at normal incidence.'
    assert _original_request_source(wrapped(route, original)).strip() == original


def test_initial_and_refinement_planners_receive_exact_readonly_standard(tmp_path):
    data = captured()
    standard = ScoringStandard.model_validate(data['standard'])
    seen = []

    class Planner:
        def plan(self, problem, *args, **kwargs):
            payload = kwargs.get('problem_analysis', problem)
            seen.append(copy.deepcopy(payload['frozen_scoring_standard']))
            # A recipient can edit its own payload but cannot mutate the standard.
            payload['frozen_scoring_standard']['metrics'][0]['sense'] = 'report'
            return RoutePlanResult(status='unavailable')

    planner = Planner()
    harness = SimpleNamespace(
        scoring_standard=standard, route_planner=planner, strategy_planner=planner,
        config=SimpleNamespace(route_planning_enabled=True, qwen_force_mock=True),
        research_charter=None, _state_lock=threading.RLock(), _usage=[],
        _write=lambda *args: tmp_path / 'unused.json', _event=lambda *args, **kwargs: None)
    problem = copy.deepcopy(data['problem_analysis'])
    TMMResearchHarness._plan_routes_from_literature(harness, data['question'], problem)
    TMMResearchHarness._plan(harness, problem, {})
    assert seen == [data['standard'], data['standard']]
    assert standard.model_dump(mode='json') == data['standard']
    assert 'frozen_scoring_standard' not in problem
