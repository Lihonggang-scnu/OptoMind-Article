"""Read existing exchanges only; verify criticism against actual transmitted inputs."""
from pathlib import Path
import json,hashlib
R=Path(__file__).resolve().parent;C=R/'cases/e2e-04-solarblind-uv-triple'
g=json.loads((C/'GUARD_CURRENT.json').read_text(encoding='utf-8'));e=next(x for x in g['events'] if x['stage']=='search_revision');payload=e['forwarded_prompt']['user_payload']
req=next((C/'private/audit').rglob('r3-2cc300bce79b4054b73c/REQUEST.json'));raw=json.loads(req.read_text(encoding='utf-8'));messages=json.dumps(raw['messages'],ensure_ascii=False)
rows={x['parent_alias']:x for x in payload['representative_decisions'] if x['parent_alias'] in ['P002','P008']}
for alias,x in rows.items():
 assert x['design_id'] in messages
 for v in x['local_direction_facts']['variables']:
  for goal,value in v['derivative_per_nm'].items():assert str(value) in messages,(alias,v['name'],goal)
out={'scope':'Existing actual transmitted request, no model or physics rerun','request_local_path':str(req),'request_file_sha256':hashlib.sha256(req.read_bytes()).hexdigest(),'source_sha':'366f3faa70217b6209afb1162bac6759765cd628','P002_and_P008_identity_and_derivative_numbers_present_in_actual_messages':True,'forwarded_representative_decisions':rows,'conclusion':'P002 negative raw derivatives for minimized G2/G3 were actually available to the model. Its decreasing-dH1-improves-all statement reverses their local orientation. This does not invalidate finite multivariable child gains or certify the derivative.','joint_direction_capability':'Existing helper explicitly reports unsupported for three objectives; unimplemented capability, not a fabricated zero/common-direction result.'}
(R/'PARENT_ACTUAL_PROMPT_REVIEW.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print('Actual transmitted P002/P008 IDs and per-nm derivative values verified')
