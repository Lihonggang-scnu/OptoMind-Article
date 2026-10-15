"""Inspect rejected cached model objects; never repair, materialize or validate them as accepted."""
import hashlib,json
from pathlib import Path
from launch_t025 import ROOT,load,write
generation=load(ROOT/'runs/routes/generation_outcome.json')
evidence=load(ROOT/'inputs/evidence_items.json')
items={x['evidence_id']:x for x in evidence['items']}
quote_rows=[]
for round_ in generation['rounds']:
    number=round_['round'];draft=round_['raw_research_routes']
    write(ROOT/f'rejected-drafts/round-{number}.research_routes.json',draft)
    raw=ROOT/f'rejected-drafts/round-{number}.raw.txt'
    with raw.open('x',encoding='utf-8',newline='') as f:f.write(round_['raw_response'])
    for clue in draft['decision_clues']:
        row=items.get(clue['evidence_id'])
        quote=clue['source_quote']
        quote_rows.append({'round':number,'clue_id':clue['id'],'evidence_id':clue['evidence_id'],
            'evidence_exists':row is not None,'quote_exact_contiguous_span':row is not None and quote in row['text'],
            'quote':quote,'actual_evidence_text':row['text'] if row else None,
            'quote_found_in_other_selected_evidence':[x['evidence_id'] for x in evidence['items'] if quote in x['text']]})
write(ROOT/'QUOTE_DIAGNOSTICS.json',{'production_error':generation['errors'],
    'original_quotes_not_modified':True,'rounds':quote_rows})
draft=generation['rounds'][-1]['raw_research_routes']
clues={x['id']:x for x in draft['decision_clues']}
routes=[]
for route in draft['routes']:
    family=route['family'];instances={x['id']:x['parameters_nm'] for x in route['instances']}
    comparisons=[]
    for exp in route['expectations']:
        a=instances[exp['baseline_instance_id']];b=instances[exp['candidate_instance_id']]
        actual={k for k in a if a[k]!=b[k]}
        declared=set(exp['changed_variables'])
        comparisons.append({'goal_id':exp['goal_id'],'direction':exp['direction'],
            'actual_changed_variables':sorted(actual),'declared_changed_variables':exp['changed_variables'],
            'declared_but_unchanged':sorted(declared-actual),'actual_but_undeclared':sorted(actual-declared),
            'changes_nm':{k:[a[k],b[k]] for k in sorted(actual)},'metric':exp['metric']})
    routes.append({'id':route['id'],'basis_mode':route['basis_mode'],'claim':route['claim'],
        'model_declared_readiness':route['readiness'],'contract_accepted':False,
        'layer_count':len(family['layers']),'materials':sorted({x['material'] for x in family['layers']}),
        'media':[family['incident_medium'],family['substrate']],
        'instances':route['instances'],'comparisons':comparisons,
        'clue_evidence_link_mismatches':[c for c in route['clue_ids'] if clues[c]['evidence_id'] not in route['evidence_ids']]})
write(ROOT/'RAW_DRAFT_DIAGNOSTICS.json',{'scope':'Static inspection of rejected raw draft; not production validation, optical computation or accepted input.',
    'actual_first_round_count':len(generation['rounds'][0]['raw_research_routes']['routes']),
    'actual_final_raw_count':len(routes),'accepted_routes':0,'prepared_instances':0,'routes':routes,
    'manual_shared_intervention_note':'R3/R10 share the 12-layer alternating family and increase dH/decrease dL, with different magnitudes. R4/R6 both tune an incident SiO2 layer but have different layer count/termination. No drafts removed.'})
write(ROOT/'ROUTE_QUALITY_STATUS.json',{'production_route_quality_report':'not_produced',
    'reason':'Whole contract rejected at exact source_quote validation; no accepted research_routes object.',
    'prepared_instances_path':'runs/routes/prepared_instances.json','prepared_instance_count':0,
    'manual_diagnostic':'RAW_DRAFT_DIAGNOSTICS.json','manual_quality_report':'QUALITY_REPORT.md',
    'no_production_report_fabricated':True})
assert load(ROOT/'runs/routes/prepared_instances.json')==[]
requests=[]
expected=load(ROOT/'PROMPT_PREFLIGHT.json')['user_payload']
for p in sorted((ROOT/'private/audit').rglob('REQUEST.json')):
    request=load(p);body=request['wire_body'];payload=json.loads(body['messages'][-1]['content'])
    for k in ('task_sheet','evidence_items','design_constraints'):assert payload[k]==expected[k],k
    assert body['max_completion_tokens']==16384
    serial=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':'),default=str).encode()
    digest=hashlib.sha256(serial).hexdigest()
    preflights=[load(x) for x in ROOT.glob('CALL_*_PREFLIGHT.json')]
    match=[x for x in preflights if x['wire_body_sha256']==digest]
    assert len(match)==1 and match[0]['max_output_tokens']==body['max_completion_tokens']
    requests.append({'call_id':p.parent.name,'request_body_hash_matches_native_reservation_preflight':True,
        'selected_12_evidence_items_exact':True,'evidence_text_chars':12487,
        'frozen_task_and_constraints_exact':True,'max_output_tokens_equals_reserved_bound':True})
write(ROOT/'ACTUAL_PROMPT_CONSUMPTION.json',{'actual_model_requests':requests,
    'core_body_in_model_requests':False,'core_exclusion_reason':'Whole block would make total56881 > original48000; old12 preserved without truncation.'})
print(json.dumps({'round_counts':[len(x['raw_research_routes']['routes']) for x in generation['rounds']],
    'invalid_quotes':[(x['round'],x['clue_id']) for x in quote_rows if not x['quote_exact_contiguous_span']],
    'extra_unchanged_variables':[(x['id'],c['declared_but_unchanged']) for x in routes for c in x['comparisons'] if c['declared_but_unchanged']],
    'clue_evidence_mismatches':[(x['id'],x['clue_evidence_link_mismatches']) for x in routes if x['clue_evidence_link_mismatches']],
    'accepted_instances':0},ensure_ascii=False))
