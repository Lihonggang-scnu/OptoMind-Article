"""Summarize the one completed T022 chain; no network/model/solver calls."""
import datetime
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p,v): Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
plan=read(ROOT/'runs/plan/task_sheet.json')
question=read(Path(r'D:\OptoMind-Article-3\r3t022a\focused_research\examples\rgb_nir_question.v2.json'))['original_question']
assert plan['original_question']==question
selected=read(ROOT/'runs/material-view/selected_evidence_items.json')
byid={x['evidence_id']:x for x in selected['items']}
routes=read(ROOT/'runs/routes/research_routes.json')
generation=read(ROOT/'runs/routes/generation_outcome.json')
proof=[]
calls=[]
for folder in sorted((ROOT/'private/audit').glob('*/calls/*')):
    request=read(folder/'REQUEST.json'); response=read(folder/'RESPONSE.json')
    payload=json.loads(request['messages'][1]['content'])
    usage=response['usage']
    calls.append({'call_id':folder.name,'model':request['model'],
        'stage':'routes' if 'task_sheet' in payload else 'plan',
        'format_repair':'validation_error' in payload,
        'prompt_tokens':usage['prompt_tokens'],'completion_tokens':usage['completion_tokens'],
        'cost_cny':usage['prompt_tokens']*0.0000008+usage['completion_tokens']*0.0000048,
        'finish_reason':response.get('finish_reason'),
        'raw_response_files':[p.name for p in (folder/'raw').glob('*.raw')],
        'transport_retries':request['transport_retries']})
    if 'evidence_items' not in payload: continue
    items=payload['evidence_items']['items']
    assert payload['task_sheet']==plan
    assert {x['evidence_id'] for x in items}==set(byid)
    assert all(x['text']==byid[x['evidence_id']]['text'] for x in items)
    proof.append({'call_id':folder.name,'actual_wire_request':str(folder/'REQUEST.json'),
        'original_question_exact':payload['task_sheet']['original_question']==question,
        'evidence_ids':[x['evidence_id'] for x in items],
        'snapshot_ids':list(dict.fromkeys(x['snapshot_id'] for x in items if x.get('snapshot_id'))),
        'selected_text_exact':True,'text_chars':sum(len(x['text']) for x in items),
        'request_sha256':request['request_sha256'],
        'format_repair':'validation_error' in payload})
quotes=[]
for clue in routes['decision_clues']:
    item=byid[clue['evidence_id']]
    assert clue['source_quote'] in item['text']
    quotes.append({'clue_id':clue['id'],'evidence_id':clue['evidence_id'],
        'snapshot_id':item.get('snapshot_id'),'block_id':item.get('block_id'),
        'title':item['title'],'source_quote':clue['source_quote'],'exact_substring':True})
save(ROOT/'ACTUAL_TEXT_CONSUMPTION.json',{'actual_route_requests':proof,
    'final_clues':quotes,'note':'All four accepted fulltext snapshots entered both route HTTP requests. Final external-evidence route cites two real Docling GaAs body blocks; other supplied blocks are context, not proof they influenced the route.'})
save(ROOT/'MODEL_CALLS.json',calls)
full=read(ROOT/'runs/fulltext/FULLTEXT_RESULT.json')
batch=read(ROOT/'runs/fulltext/docling_batch/BATCH_RESULT.json')
exports=[]
for path in full['exports']:
    v=read(path); material=v['material'];types={}
    for block in material['blocks']:
        kind=block['block_type'];types[kind]=types.get(kind,0)+1
    exports.append({'title':v['source_record']['title'],'snapshot_id':material['snapshot_id'],
        'verified':v['verified'],'review_commit':v['review_commit'],
        'scope':material.get('reading_policy',{}).get('material_scope'),
        'identity_check':material.get('identity_check'), 'block_types':types,
        'export_file':Path(path).name})
save(ROOT/'PARSER_SUMMARY.json',{'fulltext_status':full['status'],'selected_papers':5,
    'verified_exports':exports,'docling_configured_workers':batch['workers'],
    'docling_actual_nonempty_shards':len(batch.get('records',[])),
    'docling_status':batch['status'],'docling_elapsed_seconds':batch['elapsed_seconds'],
    'docling_peak_gpu_memory_mib':batch['peak_gpu_memory_mib'],
    'formula_enhancement':'skipped by explicit user scope',
    'pdf_reports':'not_run','cached_weights_reused':True,
    'failed_or_excluded':[{'title':x['record']['title'],'status':x['status'],
        'parser':x.get('acquisition',{}).get('parser'),'identity':x.get('acquisition',{}).get('identity',{}).get('status')}
        for x in full['records'] if x['status']!='verified_material'],
    'note':'Intermediate fitz snapshots were not accepted as Docling; the two PDF exports come from verified native Docling outputs. XML sources are provider-supplied OpenAlex TEI, not claimed to be publisher-authored XML.'})
consumer=read(ROOT/'runs/inputs/CONSUMER_CHECK.json')
budget=read(ROOT/'BUDGET_AFTER.json')
summary={'task_id':'R3-T022','in_reply_to':'C2L-R3-0035',
    'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'execution_status':'completed_with_findings','acceptance_status':'partial',
    'article_source_sha':'5ef850832c221e7f6444195ac8b948117ec1c8aa',
    'article_runtime_logic_sha':'df3a423357657242bddc65bece4b7d5ada6d54ae',
    'review_source_sha':'d2deb4dbe18b23837d949822ad731afbaba9414b',
    'native_question_exact':True,'planning_first_hit':True,
    's2_counts':[10,10,4,10,10],'independent_source_records':20,
    'fulltext_selected':5,'fulltext_accepted':4,'docling_successes':2,
    'provider_xml_successes':2,'routes':len(routes['routes']),
    'route_basis_modes':[x['basis_mode'] for x in routes['routes']],
    'route_generation_rounds':len(generation['rounds']),
    'prepared_inputs':consumer['prepared_count'],'native_parser_accepted':consumer['native_parser_accepted_count'],
    'new_model_http_attempts':budget['task']['attempted_calls'],
    'new_cost_cny':round(budget['task']['actual_cny'],7),
    'cumulative_cost_cny':round(float(budget['aggregate_settled_cny']),7),
    'held_cny':float(budget['aggregate_held_cny']),'unresolved_count':0,
    'core_production_changes':False,'simulation_performed':False,
    'broad_regression_repeated':False,'new_model_downloads':False,
    'automatic_followup_allowed':False,
    'findings':['CORE fullText snapshot dropped by bridge core_text_jats allowlist mismatch',
        'R2 polarization phase rationale conflicts with isotropic engine phase/admittance separation',
        'Both proposed routes use the same 12-layer alternating stack and increase only dH; route diversity is limited',
        'No numerical evidence establishes the predicted band-mean changes or target attainment',
        'Retrieval summary source_commit is stale; actual tested Git SHA recorded separately']}
save(ROOT/'RESULT_SUMMARY.json',summary)
print(json.dumps(summary,ensure_ascii=False,indent=2))
