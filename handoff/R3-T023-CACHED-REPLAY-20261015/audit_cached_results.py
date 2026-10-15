"""Assertions against real cached T022 objects; no generation or numerical work."""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'R3-T022'
sys.path.insert(0,r'D:\OptoMind-Article-3\r3t023a\focused_research')
import research_routes as rr
from route_quality import contract_change_audit
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def save(name,v):(ROOT/name).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def key(v):return json.dumps(v,ensure_ascii=False,sort_keys=True)

old=read(OLD/'ROUTE_PROMPT_PREFLIGHT.json')['user_payload']['evidence_items']
new=read(ROOT/'prompt/route_prompt.json')['user_payload']['evidence_items']
original=read(OLD/'runs/routes/evidence_items.json')
assert [x['evidence_id'] for x in old['items']]==[x['evidence_id'] for x in new['items']]
assert [x['text'] for x in old['items']]==[x['text'] for x in new['items']]
assert sum(len(x['text']) for x in new['items'])==12487
assert len(new['items'])==12
structures=Counter()
for o,n in zip(original['items'],new['items']):
    assert n['source_ref'] in new['sources']
    source=new['sources'][n['source_ref']]
    for k in ('source_id','source_url','title','authors','year','doi'):
        if k in o:assert source[k]==o[k],k
    if o.get('snapshot_id'):
        assert n['snapshot_ref'] in new['snapshots']
        context=new['snapshots'][n['snapshot_ref']]
        for k in ('snapshot_id','content_depth','identity_check'):
            if k in o:assert context[k]==o[k],k
        for k,v in o.get('reading_policy',{}).items():
            if k=='known_gaps' and k not in context['reading_policy']:
                assert v==o['known_gaps']
            else:assert context['reading_policy'][k]==v,k
        gaps=list(context.get('known_gaps',[]))
        for g in context.get('asset_gap_groups',[]):
            gaps.extend({**g['gap'],'asset_id':asset_id} for asset_id in g['asset_ids'])
        assert Counter(map(key,gaps))==Counter(map(key,o.get('known_gaps',[])))
    for k in ('query_ids','goal_ids','eligible_as_evidence','missing_reasons','material_assets',
              'formula_provenance','fidelity_status'):
        if k in o:assert n[k]==o[k],k
    if 'material_block' in o:
        for k,v in o['material_block'].items():
            if k not in ('text_raw','text_normalized','locator'):
                assert n['material_block'][k]==v,k
        # All scientific substructures present in the selected real blocks are
        # compared above; this does not claim coverage of absent structure types.
        structures[str(o['material_block'].get('block_type',o['material_block'].get('type','unknown')))]+=1
        for k in ('page','page_no','page_number','bbox','region_id','xml_id','locations'):
            if k in o['material_block'].get('locator',{}):
                assert n['material_block']['locator'][k]==o['material_block']['locator'][k],k
chars=lambda x:len(json.dumps(x,ensure_ascii=False))
assert chars(new)<chars(old)
save('PROMPT_COMPARISON.json',{'status':'passed','old_view_chars':chars(old),'new_view_chars':chars(new),
    'character_reduction_percent':round((1-chars(new)/chars(old))*100,2),'text_chars':12487,'items':12,
    'text_and_ids_order_exact':True,'source_refs_resolve':True,'snapshot_refs_resolve':True,
    'source_count':len(new['sources']),'snapshot_context_count':len(new['snapshots']),
    'retained_identity_policy_known_gaps':True,'selected_block_types':dict(structures),
    'selected_scientific_structures_equal':True,'token_or_cost_savings_claimed':False})

sheet=read(OLD/'runs/routes/task_sheet.json')
constraints=read(OLD/'runs/routes/design_constraints.json')
generation=read(OLD/'runs/routes/generation_outcome.json')
first,second=(r['raw_research_routes'] for r in generation['rounds'])
try:
    rr.validate(first,sheet,original,design_constraints=constraints)
except ValueError as exc:
    error=str(exc)
    assert 'actual changed variables are empty' in error
else:
    raise AssertionError('Cached first-round no-change intervention unexpectedly accepted')
rr.validate(second,sheet,original,design_constraints=constraints)
assert second==read(OLD/'runs/routes/research_routes.json')
audit=contract_change_audit(first,second)
cloud=read(ROOT/'delivery/OFFLINE_REPLAY.json')['repair_change_audit']
assert audit==cloud
save('FIRST_ROUND_VALIDATION.json',{'status':'correctly_rejected','error':error,
    'object':'generation_outcome.rounds[0].raw_research_routes','second_round_contract':'accepted',
    'old_raw_response_sha256':[hashlib.sha256(x['raw_response'].encode()).hexdigest() for x in generation['rounds']],
    'original_response_rewritten':False})
save('CONTRACT_CHANGE_AUDIT.json',audit)
quality=read(ROOT/'routes-check/route_quality_report.json')
assert quality['routes_removed']==[]
assert quality['similar_route_pairs'][0]['route_ids']==['R1','R2']
assert {c['classification'] for c in quality['similar_route_pairs'][0]['shared_comparisons']}=={'same_structure_intervention_variant'}
save('ROUTE_DIAGNOSIS.json',{'final_contract':'accepted','first_contract':'correctly_rejected',
    'near_duplicate_classification':'same_structure_intervention_variant','routes_removed':[],
    'contract_changed_json_paths':len(audit['changed_json_paths']),
    'scientific_quality':'T022 remains partial; no new model response or numerical validation.'})

full=read(OLD/'runs/fulltext/FULLTEXT_RESULT.json')
diffs=[]
for row in full['records']:
    if row.get('export_path'):
        export=read(Path(row['export_path']))
        if export['source_record']!=row['record']:
            changes=contract_change_audit(export['source_record'],row['record'])
            differences=[]
            for field in sorted(set(export['source_record'])|set(row['record'])):
                if export['source_record'].get(field)!=row['record'].get(field):
                    differences.append({'field':field,'export':export['source_record'].get(field),
                        'result_record':row['record'].get(field)})
            diffs.append({'paper':row['record']['canonical_paper_id'],
                'blocked_at':'review_fulltext_worker.py reexport_existing existing_export_source_binding_mismatch',
                'changed_json_paths':changes['changed_json_paths'],'field_differences':differences})
save('DOC_REFERENCE_BINDING_DIAGNOSIS.json',{'status':'blocked_production_reexport_binding',
    'rows':diffs,'local_core_fix_attempted':False,'old_exports_or_records_modified':False})
print(json.dumps({'prompt':'passed','final_routes':'accepted','first_routes':'correctly_rejected',
    'contract_change_paths':len(audit['changed_json_paths']),'docling_reexport_binding_failures':len(diffs)}))
