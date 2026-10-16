"""Offline reclassification of saved T038 exports. No model, retrieval or solver."""
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import research_routes as rr


def replay(input_dir):
    names = ['research_routes.raw.json', 'task_sheet.json', 'evidence_items.json', 'design_constraints.json']
    blobs = {name: (input_dir / name).read_bytes() for name in names}
    raw, sheet, evidence, constraints = [json.loads(blobs[name]) for name in names]
    before = copy.deepcopy([raw, sheet, evidence, constraints])
    compiled = rr.compile_candidates(raw, sheet, evidence, design_constraints=constraints)
    assert [raw, sheet, evidence, constraints] == before
    assert compiled['raw_research_routes'] == raw
    prepared = []
    originals = {r['id']: r for r in raw['routes']}
    for route in compiled['accepted_contract']['routes']:
        original = originals[route['id']]
        assert route['family'] == original['family']
        for instance in route['instances']:
            assert instance == next(i for i in original['instances'] if i['id'] == instance['id'])
        for field in ['claim', 'model_knowledge_rationale', 'transfer_assumptions', 'open_requirements']:
            assert route[field] == original[field]
        if route['readiness'] == 'ready':
            prepared += rr.materialize(compiled['accepted_contract'], route['id'], sheet, evidence,
                design_constraints=constraints, candidate_mode=True, candidate_report=compiled['candidate_report'])
    for item in prepared:
        assert item['objectives'] == [g['executable_metric'] for g in sheet['subgoals']]
        original = originals[item['route_id']]
        params = next(i['parameters_nm'] for i in original['instances'] if i['id'] == item['instance_id'])
        assert item['layers'] == [{'material': l['material'], 'thickness_nm': params[l['thickness_nm']]
            if isinstance(l['thickness_nm'], str) else l['thickness_nm']} for l in original['family']['layers']]
    assert all((input_dir / name).read_bytes() == blob for name, blob in blobs.items())
    return {'scope': 'offline_reclassification_only; historical T038 executed only R1',
        'model_calls': 0, 'solver_calls': 0, 'network_calls': 0,
        'input_byte_sha256': {name: hashlib.sha256(blob).hexdigest() for name, blob in blobs.items()},
        'raw_input_bytes_unchanged': True, 'legal_structures_unchanged': True,
        'all_execution_objectives_unchanged': True,
        **compiled, 'prepared_instances': prepared}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = replay(args.input_dir)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'replay.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    report = result['candidate_report']
    print(json.dumps({**{k: report[k] for k in ['accepted_route_ids', 'rejected_route_ids',
        'executable_route_ids', 'needs_revision_route_ids']},
        'prepared_instance_count': len(result['prepared_instances']),
        'retained_instance_count': sum(len(r['instances']) for r in result['accepted_contract']['routes']),
        'rejected_instance_count': sum(len(r['rejected_instances']) for r in report['routes'].values()),
        'input_byte_sha256': result['input_byte_sha256']}, indent=2))


if __name__ == '__main__':
    main()
