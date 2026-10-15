"""Synthetic T025 failure-class replay. No historical acceptance is rewritten.

Run with Python and optionally --output DIR. Uses shipped public synthetic
fixtures only: no network, model, solver, private PDF or source upload.
"""
import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import research_routes as rr


def synthetic_fixture():
    def read(name):
        return json.loads((ROOT / 'examples' / name).read_text())
    raw, sheet, evidence, constraints = [read(name) for name in [
        'research_routes_example.json', 'rgb_nir_task_sheet.json',
        'research_routes_evidence_fixture.json', 'research_routes_constraints.json']]
    template, clue = copy.deepcopy(raw['routes'][0]), copy.deepcopy(raw['decision_clues'][0])
    raw['routes'] = []
    for number in range(1, 13):
        route = copy.deepcopy(template)
        route['id'] = 'candidate_' + str(number)
        raw['routes'].append(route)
    paraphrase = copy.deepcopy(clue)
    paraphrase.update(id='paraphrase', source_quote='An editorial ... paraphrase not present verbatim.')
    invalid = copy.deepcopy(clue)
    invalid.update(id='unknown_source', evidence_id='not_a_source')
    raw['decision_clues'] += [paraphrase, invalid]
    raw['routes'][1]['clue_ids'] = ['paraphrase']
    raw['routes'][2]['clue_ids'] = ['unknown_source']
    raw['routes'][3]['evidence_ids'] = ['wrong_declared_evidence']
    raw['routes'][4]['expectations'][0]['changed_variables'] = ['dH', 'dL']
    raw['routes'][5]['instances'][1]['parameters_nm'] = copy.deepcopy(template['instances'][0]['parameters_nm'])
    raw['routes'][6]['claim'] = 'An untested but questionable claim: larger p propagation phase causes improved performance.'
    raw['routes'][7]['family']['layers'][0]['material'] = 'not_allowed'
    raw['routes'][8]['family']['layers'] *= constraints['max_layers']
    raw['routes'][9]['expectations'][0]['candidate_instance_id'] = 'missing'
    raw['routes'][10].update(basis_mode='model_knowledge', clue_ids=[], evidence_ids=[],
                            model_knowledge_rationale='Unverified remembered interference intuition.')
    return raw, sheet, evidence, constraints


def replay():
    raw, sheet, evidence, constraints = synthetic_fixture()
    compiled = rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)
    instances = []
    for route in compiled['accepted_contract']['routes']:
        if route['readiness'] == 'ready':
            instances += rr.materialize(compiled['accepted_contract'], route['id'], sheet, evidence,
                design_constraints=constraints, candidate_mode=True,
                candidate_report=compiled['candidate_report'])
    return {'fixture_kind': 'synthetic_T025_failure_classes_not_historical_data',
        'historical_T025_acceptance': 'unchanged: zero accepted routes and zero prepared instances',
        'network_calls': 0, 'model_calls': 0, 'solver_calls': 0,
        **compiled, 'prepared_instances': instances}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = replay()
    if args.output:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / 'synthetic_replay.json').write_text(json.dumps(result, indent=2) + '\n')
    report = result['candidate_report']
    print(json.dumps({key: report[key] for key in [
        'generated_route_count', 'accepted_route_ids', 'rejected_route_ids', 'warnings', 'failure_semantics']}, indent=2))


if __name__ == '__main__':
    main()
