"""Task-local adaptation of the verified T031 launcher; production source unchanged."""
from pathlib import Path
import shutil

r=Path(__file__).resolve().parent
prior=r.parent/'R3-T031'
(r/'local_tools').mkdir(exist_ok=True)
for name in ['aggregate_budget.py','task_budget_guard_220.py','test_aggregate_budget.py']:
    shutil.copyfile(prior/'local_tools'/name,r/'local_tools'/name)
shutil.copyfile(prior/'CASE_HISTORY_FOR_GUARD.json',r/'CASE_HISTORY_FOR_GUARD.json')
p=r/'local_tools/aggregate_budget.py';s=p.read_text(encoding='utf-8')
s=s.replace("TASK_IDS = {'R3-T031:'+c:c for c in CASES}","TASK_IDS = {task+':'+c:c for task in ('R3-T031','R3-T034') for c in CASES}")
s=s.replace("selected={c:set(history['case_reservation_ids'][c])|task_ids['R3-T031:'+c] for c in CASES}","selected={c:set(history['case_reservation_ids'][c])|task_ids['R3-T031:'+c]|task_ids['R3-T034:'+c] for c in CASES}")
s=s.replace("if admission and sum(len(x) for x in task_ids.values())>=9:raise RuntimeError('T031 batch9 actual reservation attempts exhausted')", "if admission and sum(len(x) for x in task_ids.values())>=9:raise RuntimeError('shared T031/T034 batch9 actual reservation attempts exhausted')\n    if admission and len(task_ids['R3-T031:'+TASK_IDS[task_id]]|task_ids['R3-T034:'+TASK_IDS[task_id]])>=3:raise RuntimeError('shared T031/T034 original case3 actual reservation attempts exhausted')")
p.write_text(s,encoding='utf-8')
s=(prior/'run_t031.py').read_text(encoding='utf-8').replace('Audited local T031 continuation','Audited local T034 cached continuation')
s=s.replace("r'D:\\OptoMind-Article-3\\r3t031a'","r'D:\\OptoMind-Article-3\\r3t034a'")
s=s.replace("IMPL='550165c23fe3d8a1338ec4ab47033133f8fdcb3d'","IMPL='8a38b2d0d7b18c4308c4f5978691a234f6ab3732'")
s=s.replace("T029=ROOT.parent/'R3-T029'","T031=ROOT.parent/'R3-T031'\nT029=ROOT.parent/'R3-T029'")
s=s.replace("'R3-T031:'+case","'R3-T034:'+case")
s=s.replace("d=ROOT/'cases'/case/'recovered'","d=T031/'cases'/case/'recovered'")
s=s.replace("load(ROOT/'preflight/T031_RECOVERY_VERIFICATION.json')","load(T031/'preflight/T031_RECOVERY_VERIFICATION.json')")
s=s.replace("tests=load(ROOT/'preflight/T031_FOCUSED_TEST_RESULT.json');assert tests['passed']==100 and tests['failed']==0 and tests['skipped']==0", "tests=load(ROOT/'preflight/T034_OFFLINE_ADMISSION.json');assert tests['global_pass'] is True\n    inputs=load(ROOT/'INPUT_PROVENANCE.json');assert inputs['source_result_sha']=='2d2338cc2dea5929b6b05ea2c7b781652e619f62'\n    for relative,expected in inputs['files'].items():assert sha(T031/relative)==expected['sha256'],'original T031 input bytes changed: '+relative\n    prior_guard=load(T031/'cases'/case/'GUARD_CURRENT.json');assert prior_guard['dispatch_attempts']==0,'original continuation attempts must be reconciled, never reset'\n    import aggregate_budget\n    with sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as db:assert not db.execute('SELECT 1 FROM r3_task_calls WHERE task_id=?',('R3-T031:'+case,)).fetchone(),'intervening original T031 dispatch; new admission prohibited'")
start=s.index("        constraints,media,grid=old.configuration(sheet,case)")
end=s.index("        guard.set_stage('route_generation')",start)
s=s[:start]+"""        olddir=T031/'cases'/case
        constraints=load(olddir/'design_constraints.json')
        for name in ['design_constraints.json','optical_media.json','wavelengths.json']:
            target=cr/name;assert not target.exists();target.write_bytes((olddir/name).read_bytes())
        status['stages']['retrieval']='reused verified immutable T031 cache; no new retrieval'
"""+s[end:]
s=s.replace("str(cr/'recovered/task_sheet.json')","str(T031/'cases'/case/'recovered/task_sheet.json')")
s=s.replace("str(cr/'evidence_items.json')","str(T031/'cases'/case/'evidence_items.json')",1)
needle="        routes=load(p);cg="
s=s.replace(needle,"""        evidence=load(cr/'routes/evidence_items.json')
        full=load(T031/'cases'/case/'evidence_items.json');byid={x['evidence_id']:x for x in full['items']}
        assert all(x==byid[x['evidence_id']] for x in evidence['items']),'selected original evidence changed'
        assert (cr/'routes/evidence_items.source.json').read_bytes()==(T031/'cases'/case/'evidence_items.json').read_bytes(),'source evidence bytes changed'
        expected=load(ROOT/'offline-selection'/case/'evidence_items.json')
        assert evidence==expected,'actual selected compiler set differs from verified prompt preflight'
        save(cr/'SELECTED_EVIDENCE_DOWNSTREAM_BINDING.json',{'source_sha':IMPL,'source_byte_sha256':sha(T031/'cases'/case/'evidence_items.json'),'selected_evidence_sha256':digest(evidence),'active_file':str(cr/'routes/evidence_items.json'),'selected_ids':[x['evidence_id'] for x in evidence['items']],'route_validation_and_materialization':'production route_stage.generate using selected original items','search_evidence_argument':str(cr/'routes/evidence_items.json')},new=True)
        routes=load(p);cg=""")
s=s.replace("str(cr/'evidence_items.json')","str(cr/'routes/evidence_items.json')")
(r/'run_t034.py').write_text(s,encoding='utf-8')
print('new task-local launcher and shared original-case budget gate prepared; no execution')
