import json
import hashlib
import threading
from types import SimpleNamespace

from optomind_optics.harness.research_orchestrator import TMMResearchHarness


def bare(tmp_path):
    harness = object.__new__(TMMResearchHarness)
    harness.work_dir = tmp_path
    harness._state_lock = threading.RLock()
    harness._mechanism_states = {}
    harness._followup_queries_seen = set()
    harness._followup_research_reports = []
    harness._followup_research_calls = 0
    harness._usage = []
    harness._service_telemetry = []
    harness.config = SimpleNamespace(maximum_method_research_rounds=2, online_method_research=False)
    harness._write = lambda name, content: (tmp_path / name).write_text(json.dumps(content))
    return harness


def test_need_based_retrieval_enters_next_report_once(tmp_path):
    harness = bare(tmp_path)
    calls = []
    def research(problem, queries):
        calls.append(queries)
        return {'report': {'status': 'completed', 'evidence': [{'evidence_id': 'fresh', 'source': 'saved_offline_response'}]}}
    harness._research = research
    observation = SimpleNamespace(failure_categories=['objective_shortfall'], iteration_id='r1', compilation_status='compiled')
    updated = harness._research_for_decision({'original_request': 'optical filter'}, observation, {'route_id': 'a'}, {'evidence': []})
    assert updated['evidence'][0]['evidence_id'] == 'fresh'
    harness._research_for_decision({'original_request': 'optical filter'}, observation, {'route_id': 'a'}, updated)
    assert len(calls) == 1
    observation.failure_categories = ['runtime_environment']
    harness._research_for_decision({}, observation, {}, updated)
    assert len(calls) == 1


class PhysicalPredictionFixture:
    def predict(self, **kwargs):
        return {'belief_id': 'mechanisms', 'hypothesis_ids': ['h1', 'h2'], 'prior': [.5, .5],
                'intervention_id': 'angle_test', 'condition_id': 'frozen_conditions',
                'outcome_ids': ['shift', 'no_shift'], 'kernel': [[.9, .1], [.1, .9]]}

    def observe(self, **kwargs):
        return {'observation_id': kwargs['iteration_id'], 'intervention_id': 'angle_test',
                'condition_id': 'frozen_conditions', 'outcome_id': kwargs['run_payload']['outcome'],
                'execution_signature': kwargs['execution_signature'],
                'source_artifact': {'path': 'observation.json', 'sha256': hashlib.sha256((kwargs['execution_dir'] / 'observation.json').read_bytes()).hexdigest()}}


def test_prediction_is_frozen_before_observation_and_updates_main_adapter(tmp_path):
    harness = bare(tmp_path)
    harness.scientific_prediction_provider = PhysicalPredictionFixture()
    frozen = harness._freeze_scientific_prediction({}, {}, tmp_path, 'r1')
    assert json.loads((tmp_path / 'SCIENTIFIC_PREDICTION.json').read_text())['stage'] == 'before_execution'
    (tmp_path / 'tmm_run').mkdir()
    (tmp_path / 'tmm_run' / 'observation.json').write_text('{"outcome":"shift"}')
    context = {'scientific_prediction': frozen, 'task': {}, 'iteration_id': 'r1', 'iteration_dir': tmp_path,
               'execution_signature': 'exec1', 'execution_facts': {'physics_executed': True}}
    result = harness._update_scientific_prediction(context, {'outcome': 'shift'})
    assert result['status'] == 'updated'
    assert abs(result['posterior'][0] - .9) < 1e-12
    assert harness._update_scientific_prediction(context, {'outcome': 'shift'})['status'] == 'duplicate_observation'


def test_missing_physical_prediction_never_uses_route_score_as_likelihood(tmp_path):
    harness = bare(tmp_path)
    harness.scientific_prediction_provider = None
    assert harness._freeze_scientific_prediction({}, {}, tmp_path, 'r1') is None
    result = harness._update_scientific_prediction({}, {'score': 100})
    assert result['status'] == 'unresolved'
    assert result['posterior'] is None


def test_mutated_prediction_is_rejected(tmp_path):
    harness = bare(tmp_path)
    harness.scientific_prediction_provider = PhysicalPredictionFixture()
    frozen = harness._freeze_scientific_prediction({}, {}, tmp_path, 'r1')
    frozen['prediction']['prior'] = [.99, .01]
    result = harness._update_scientific_prediction({'scientific_prediction': frozen, 'execution_signature': 'exec1', 'execution_facts': {'physics_executed': True}}, {'outcome': 'shift'})
    assert result['reason'] == 'prediction_changed_after_freeze'


def test_failed_execution_cannot_create_mechanistic_evidence(tmp_path):
    harness = bare(tmp_path)
    harness.scientific_prediction_provider = PhysicalPredictionFixture()
    frozen = harness._freeze_scientific_prediction({}, {}, tmp_path, 'r1')
    result = harness._update_scientific_prediction({'scientific_prediction': frozen}, {'status': 'failed'})
    assert result['reason'] == 'no_bound_physical_execution'


def test_failed_followup_consumes_durable_limit(tmp_path):
    harness = bare(tmp_path)
    def research(*args):
        raise RuntimeError('offline backend unavailable')
    harness._research = research
    observation = SimpleNamespace(failure_categories=['objective_shortfall'], iteration_id='r1', compilation_status='compiled')
    harness._research_for_decision({'original_request': 'filter'}, observation, {}, {})
    state = json.loads((tmp_path / 'FOLLOWUP_RESEARCH_STATE.json').read_text())
    assert state['calls'] == 1
    assert state['queries']


def test_search_tree_does_not_treat_negative_objective_as_refutation():
    from optomind_optics.harness.hypothesis_tree import HypothesisTree
    tree = HypothesisTree()
    node = tree.create_node(parent_id=None, route_id='route', stage='draft', plan_pointer='plan')
    for _ in range(3):
        tree.update_node(node.node_id, metric=-.1, is_buggy=False, rotated=True)
    assert node.status == 'open'
    assert node.falsification.refuted_discards == 0
    for _ in range(3):
        tree.update_node(node.node_id, is_buggy=True, rotated=True)
    assert node.status != 'falsified'


def test_next_planner_keeps_mechanistic_evidence_and_actionable_incumbent():
    from optomind_optics.harness.strategy_planner import _compact_prior_iterations
    result = _compact_prior_iterations([{'iteration_id': 'r1',
        'mechanism_update': {'status': 'updated', 'posterior': [.9, .1], 'next_action_value': {'action_id': 'angle-test'}},
        'candidate_summaries': [{'candidate_id': 'c1', 'thicknesses_nm': [70., 90.],
                                'band_metrics': {'R': .9}, 'frozen_metric_values': {'m0': .9}}]}])[0]
    assert result['mechanism_update']['posterior'] == [.9, .1]
    assert result['candidate_summaries'][0]['thicknesses_nm'] == [70., 90.]
    assert result['candidate_summaries'][0]['band_metrics']['R'] == .9
