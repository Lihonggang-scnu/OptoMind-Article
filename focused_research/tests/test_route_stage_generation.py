"""Offline tests for the injected local generation boundary; no paid clients."""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'vendor' / 'review'))
import route_stage


class AuditedPlannerFixture:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.last_usage = {'input_tokens': 12}
        self.last_transport = {'provider': 'offline_test', 'fallback_used': False}

    def _call_query_plan_model(self, prompt):
        self.calls.append(copy.deepcopy(prompt))
        return next(self.responses)


def args(tmp_path, evidence=None):
    examples = ROOT / 'examples'
    return ['generate', '--task-sheet', str(examples / 'rgb_nir_task_sheet.json'),
            '--evidence-file', str(evidence or examples / 'research_routes_evidence_fixture.json'),
            '--constraints-file', str(examples / 'research_routes_constraints.json'),
            '--output', str(tmp_path / 'result')]


def response():
    return (ROOT / 'examples' / 'research_routes_example.json').read_text()


def read(tmp_path, name):
    return json.loads((tmp_path / 'result' / name).read_text())


def test_generate_without_injection_is_blocked_before_output(tmp_path):
    assert route_stage.main(args(tmp_path)) == 2
    assert not (tmp_path / 'result').exists()


def test_injected_planner_prepares_all_ready_routes_and_keeps_audit(tmp_path):
    planner = AuditedPlannerFixture([response()])
    assert route_stage.main(args(tmp_path), planner=planner) == 0
    assert len(planner.calls) == 1
    assert 'around 10 or more' in planner.calls[0]['user_payload']['route_count_request']
    assert 'no route-count upper limit or hard minimum' in planner.calls[0]['user_payload']['route_count_request']
    assert 'around 10 or more' in planner.calls[0]['system_prompt']
    outcome = read(tmp_path, 'generation_outcome.json')
    assert outcome['rounds'][0]['raw_response'] == response()
    assert outcome['rounds'][0]['effective_response']
    summary = read(tmp_path, 'summary.json')
    assert summary['live_model_called'] is True
    assert summary['model_call_attempts'] == 1
    assert summary['simulation_performed'] is False
    prepared = read(tmp_path, 'prepared_instances.json')
    assert len(prepared) == 2
    assert all(item['execution_authorized'] is False for item in prepared)
    assert read(tmp_path, 'input_identity.json')['research_routes']


def test_one_repair_only_and_both_failures_saved(tmp_path):
    planner = AuditedPlannerFixture(['{}', '{}', response()])
    assert route_stage.main(args(tmp_path), planner=planner) == 2
    assert len(planner.calls) == 2
    outcome = read(tmp_path, 'generation_outcome.json')
    assert len(outcome['rounds']) == 2
    assert all(round_['error'] for round_ in outcome['rounds'])
    assert read(tmp_path, 'prepared_instances.json') == []


def test_format_repair_keeps_real_evidence_and_soft_route_request(tmp_path):
    planner = AuditedPlannerFixture(['{}', response()])
    assert route_stage.main(args(tmp_path), planner=planner) == 0
    first, second = planner.calls
    assert first['user_payload']['evidence_items'] == second['user_payload']['evidence_items']
    assert first['user_payload']['route_count_request'] == second['user_payload']['route_count_request']


def test_empty_retrieval_generates_explicit_model_knowledge(tmp_path):
    path = tmp_path / 'empty.json'
    path.write_text(json.dumps({'schema_version':'focused_evidence_items_v1', 'items':[]}))
    routes = json.loads(response())
    routes['decision_clues'] = []
    routes['routes'][0].update(basis_mode='model_knowledge', evidence_ids=[], clue_ids=[],
        model_knowledge_rationale='Remembered interference principles suggest a thickness-controlled band shift; this reasoning has no retrieved verification.')
    planner = AuditedPlannerFixture([json.dumps(routes)])
    assert route_stage.main(args(tmp_path, path), planner=planner) == 0
    assert len(planner.calls) == 1
    assert 'Empty retrieval still permits model_knowledge' in planner.calls[0]['user_payload']['route_count_request']
    assert all(row['basis_mode'] == 'model_knowledge' for row in read(tmp_path, 'prepared_instances.json'))


def test_revision_route_is_retained_without_preparation(tmp_path):
    routes = json.loads(response())
    routes['routes'][0]['open_requirements'] = ['Missing an executable requirement.']
    routes['routes'][0]['readiness'] = 'needs_revision'
    planner = AuditedPlannerFixture([json.dumps(routes)])
    assert route_stage.main(args(tmp_path), planner=planner) == 0
    assert read(tmp_path, 'research_routes.json') == routes
    assert read(tmp_path, 'prepared_instances.json') == []
    assert read(tmp_path, 'summary.json')['generation_status'] == 'needs_revision'


def test_transport_fallback_never_prepares_routes(tmp_path):
    planner = AuditedPlannerFixture([response()])
    planner.last_transport = {'fallback_used':True}
    assert route_stage.main(args(tmp_path), planner=planner) == 2
    assert len(planner.calls) == 1
    assert read(tmp_path, 'prepared_instances.json') == []


def test_proxy_enforces_model_invocation_cap():
    planner = AuditedPlannerFixture(['{}', '{}', '{}'])
    proxy = route_stage._RoutePlanner(planner)
    for _ in range(2):
        proxy._call_query_plan_model({'user_payload': {}})
    with pytest.raises(ValueError, match='limit'):
        proxy._call_query_plan_model({'user_payload': {}})
    assert len(planner.calls) == 2


def test_mixed_readiness_keeps_ready_instances(tmp_path):
    routes = json.loads(response())
    incomplete = copy.deepcopy(routes['routes'][0])
    incomplete.update(id='R2', open_requirements=['Needs structure refinement.'], readiness='needs_revision')
    routes['routes'].append(incomplete)
    planner = AuditedPlannerFixture([json.dumps(routes)])
    assert route_stage.main(args(tmp_path), planner=planner) == 0
    assert read(tmp_path, 'generation_outcome.json')['status'] == 'needs_revision'
    assert len(read(tmp_path, 'research_routes.json')['routes']) == 2
    prepared = read(tmp_path, 'prepared_instances.json')
    assert len(prepared) == 2
    assert {item['route_id'] for item in prepared} == {'R1'}


def test_quality_sidecar_and_contract_repair_audit(tmp_path):
    broken = json.loads(response())
    broken['routes'][0]['instances'][1]['parameters_nm'] = copy.deepcopy(broken['routes'][0]['instances'][0]['parameters_nm'])
    planner = AuditedPlannerFixture([json.dumps(broken), response()])
    assert route_stage.main(args(tmp_path), planner=planner) == 0
    outcome = read(tmp_path, 'generation_outcome.json')
    assert 'actual changed variables are empty' in outcome['rounds'][0]['error']
    audit = outcome['rounds'][1]['repair_audit']
    assert audit['classification'] == 'contract_repair_not_format_only'
    assert audit['parsed_contract_changed']
    assert audit['previous_contract_parseable']
    quality = read(tmp_path, 'route_quality_report.json')
    assert quality['scientific_review']['status'] == 'not_performed'
    assert quality['routes_removed'] == []
    assert 'coupled changes are allowed' in planner.calls[0]['user_payload']['route_count_request']
    assert 'shared by s and p' in planner.calls[0]['system_prompt']


def many_routes():
    routes = json.loads(response())
    template = routes['routes'][0]
    routes['routes'] = []
    for index in range(12):
        route = copy.deepcopy(template)
        route['id'] = f'R{index + 1}'
        route['claim'] = f'Untested same-family thickness parameter variant {index + 1}, not an independent mechanism.'
        route['instances'][1]['parameters_nm']['dH'] = 60 + index
        routes['routes'].append(route)
    return routes


def test_more_than_ten_valid_candidates_are_retained_and_materialized(tmp_path):
    routes = many_routes()
    planner = AuditedPlannerFixture([json.dumps(routes)])
    assert route_stage.main(args(tmp_path), planner=planner) == 0
    assert read(tmp_path, 'research_routes.json') == routes
    assert len(read(tmp_path, 'prepared_instances.json')) == 24
    assert {r['route_id'] for r in read(tmp_path, 'prepared_instances.json')} == {r['id'] for r in routes['routes']}
    assert read(tmp_path, 'route_quality_report.json')['routes_removed'] == []


@pytest.mark.parametrize('field,value', [('reason', ''), ('changed_variables', []),
                                        ('candidate_instance_id', 'invented')])
def test_large_route_batch_still_checks_last_candidate(tmp_path, field, value):
    routes = many_routes()
    routes['routes'][-1]['expectations'][0][field] = value
    planner = AuditedPlannerFixture([json.dumps(routes)] * 2)
    assert route_stage.main(args(tmp_path), planner=planner) == 2
    assert read(tmp_path, 'prepared_instances.json') == []
    assert read(tmp_path, 'generation_outcome.json')['status'] == 'invalid'


def test_zero_candidates_does_not_force_a_minimum(tmp_path):
    routes = json.loads(response())
    routes['routes'] = []
    planner = AuditedPlannerFixture([json.dumps(routes)])
    assert route_stage.main(args(tmp_path), planner=planner) == 0
    assert len(planner.calls) == 1
    assert read(tmp_path, 'generation_outcome.json')['status'] == 'needs_revision'
    assert read(tmp_path, 'prepared_instances.json') == []


@pytest.mark.parametrize('metadata', [{'finish_reason': 'length'}, {'finish_reason': 'max_tokens'},
                                      {'partial_stream': True}, {'stream_interrupted_error': 'TimeoutError'}])
@pytest.mark.parametrize('location', ['last_transport', 'last_usage'])
@pytest.mark.parametrize('parseable', [True, False])
def test_truncated_output_is_partial_even_when_json_is_parseable(tmp_path, metadata, location, parseable):
    raw = response() if parseable else response()[:100]
    planner = AuditedPlannerFixture([raw, response()])
    getattr(planner, location).update(metadata)
    assert route_stage.main(args(tmp_path), planner=planner) == 2
    assert len(planner.calls) == 1
    outcome = read(tmp_path, 'generation_outcome.json')
    assert outcome['status'] == 'partial'
    assert outcome['first_hit'] is False
    assert outcome['rounds'][0]['raw_response'] == raw
    assert outcome['rounds'][0]['ok'] is False
    assert read(tmp_path, 'prepared_instances.json') == []
    assert read(tmp_path, 'summary.json')['status'] == 'partial'
