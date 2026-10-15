"""Read existing evidence and reproduce the default projection guard; zero network/model/physics."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('t031_launch_diagnostic', ROOT / 'run_t031.py')
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)
launch.bind()
from focused_research.material_evidence import prompt_evidence_view

results = []
for case in launch.CASES:
    path = ROOT / 'cases' / case / 'evidence_items.json'
    if not path.exists():
        continue
    payload = launch.load(path)
    items = payload['items']
    row = {'case_id': case, 'source_sha': launch.IMPL,
           'evidence_file': str(path), 'evidence_file_sha256': launch.sha(path),
           'item_count': len(items), 'text_chars': sum(len(x['text']) for x in items),
           'default_text_limit': 48000, 'default_payload_limit': 192000,
           'raw_payload_chars': len(json.dumps(payload, ensure_ascii=False)),
           'items': [{'evidence_id': x.get('evidence_id'), 'text_chars': len(x['text']),
                      'query_ids': x.get('query_ids'), 'goal_ids': x.get('goal_ids')}
                     for x in items]}
    try:
        view = prompt_evidence_view(payload)
        row.update(default_projection_pass=True,
                   projected_payload_chars=len(json.dumps(view, ensure_ascii=False, allow_nan=False)))
    except ValueError as exc:
        row.update(default_projection_pass=False, error_type=type(exc).__name__, error=str(exc))
    # Measurement only: disabling the limits here never forwards a prompt or modifies evidence.
    measured = prompt_evidence_view(payload, max_text_chars=sys.maxsize, max_payload_chars=sys.maxsize)
    row['unbounded_measurement_payload_chars'] = len(json.dumps(measured, ensure_ascii=False, allow_nan=False))
    results.append(row)
report = {'schema_version': 't031-evidence-length-diagnostic.v1', 'source_sha': launch.IMPL,
          'network_calls': 0, 'model_http': 0, 'physics_evaluations': 0,
          'measurement_only': True, 'source_modified': False, 'evidence_modified': False,
          'source_locations': ['focused_research/material_evidence.py:251',
                               'focused_research/material_evidence.py:340',
                               'focused_research/material_evidence.py:372',
                               'focused_research/research_routes.py:511'],
          'existing_selector_fact': 'material_evidence CLI explicitly retains all S2 items; its material-block selection does not bound this S2 input.',
          'cases': results}
launch.save(ROOT / 'EVIDENCE_LENGTH_DIAGNOSTIC.json', report)
print(json.dumps([{k:v for k,v in row.items() if k != 'items'} for row in results], ensure_ascii=False, indent=2))
