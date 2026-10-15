"""Reuse cached exports and native whole-block selection only; no model calls."""
import hashlib,json,sys
from pathlib import Path
from launch_t025 import ROOT,SOURCE,OLD,LEDGER,reconcile,write,sha
sys.path.insert(0,str(SOURCE/'focused_research'))
import material_evidence as me
import research_routes as rr
base=ROOT.parent/'R3-T022'
four=ROOT.parent/'R3-T024'
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
original=load(base/'runs/routes/evidence_items.json')
sheet=load(base/'runs/routes/task_sheet.json');constraints=load(base/'runs/routes/design_constraints.json')
accepted=load(four/'reexport/FULLTEXT_RESULT.json')
assert accepted['status']=='complete' and len(accepted['exports'])==5
archives=[me.import_material_export(load(Path(p))) for p in accepted['exports']]
combined=me.merge_evidence_items({'schema_version':original['schema_version'],'items':[]},archives)
index=me.material_block_index(combined)
core_index=[x for x in index if x['snapshot_id']=='snapshot-485b340798303b211e786bc5']
write(ROOT/'inputs/material_block_index.json',index)
usable=[x for x in core_index if x['eligible_as_evidence'] and x['text_chars']>100]
assert len(usable)==1,'Unexpected CORE block inventory; do not guess'
selection=[{'snapshot_id':x['snapshot_id'],'block_id':x['block_id']} for x in usable]
chosen=me.select_material_evidence(combined,selection,max_blocks=24,max_text_chars=48000,max_payload_chars=192000)
merged=me.merge_evidence_items(original,[chosen])
core_status={'actual_core_index':core_index,'whole_block_selection':selection,'original_items':len(original['items']),
    'original_text_chars':sum(len(x['text']) for x in original['items']),
    'core_selected_text_chars':sum(len(x['text']) for x in chosen['items']),
    'combined_text_chars':sum(len(x['text']) for x in merged['items']),
    'body_char_budget':48000,'view_char_budget':192000,'header_only_substitution':False,'truncated':False}
try:
    rr.build_prompt(sheet,merged,design_constraints=constraints)
except ValueError as exc:
    assert 'budget' in str(exc), 'Unexpected input error; do not continue'
    core_status.update(status='not_added_whole_block_exceeds_original_budget',reason=str(exc))
    final=original
else:
    core_status.update(status='added_whole_block');final=merged
assert final['items'][:len(original['items'])]==original['items']
prompt=rr.build_prompt(sheet,final,design_constraints=constraints)
write(ROOT/'inputs/evidence_items.json',final)
write(ROOT/'inputs/CORE_SELECTION_STATUS.json',core_status)
write(ROOT/'PROMPT_PREFLIGHT.json',prompt)
write(ROOT/'BUDGET_BEFORE.json',reconcile())
old={name:{str(p.relative_to(ROOT.parent/name)):sha(p) for p in sorted((ROOT.parent/name).rglob('*')) if p.is_file()} for name in ('R3-T022','R3-T023','R3-T024')}
write(ROOT/'OLD_OUTPUT_HASHES_BEFORE.json',old)
write(ROOT/'INPUT_PREFLIGHT.json',{'source_sha':'e2e3a54aa6bd5b3fd1757b82b718d44c24391417',
    'task_sheet_sha256':sha(base/'runs/routes/task_sheet.json'),'constraints_sha256':sha(base/'runs/routes/design_constraints.json'),
    'original_evidence_sha256':sha(base/'runs/routes/evidence_items.json'),'selected_evidence_sha256':sha(ROOT/'inputs/evidence_items.json'),
    'original_items_retained_exactly':True,'new_evidence_items':len(final['items'])-len(original['items']),
    'evidence_text_chars':sum(len(x['text']) for x in final['items']),
    'prompt_view_chars':len(json.dumps(prompt['user_payload']['evidence_items'],ensure_ascii=False)),
    'cached_materials_available':5,'exports_read_only':True,'no_export_or_parser_invocations':True,
    'old_output_counts':{k:len(v) for k,v in old.items()},'new_model_http_attempts':0})
print(json.dumps({'input':'ready','items':len(final['items']),'core_status':core_status['status'],
    'combined_core_text_chars':core_status['combined_text_chars'],'budget_cny':3},ensure_ascii=False))
