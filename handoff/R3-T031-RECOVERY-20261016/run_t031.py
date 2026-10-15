"""Audited local T031 continuation. No new task sheets or scientific edits."""
from __future__ import annotations
import argparse
import contextlib
import copy
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import types

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
SOURCE=Path(r'D:\OptoMind-Article-3\r3t031a')
IMPL='550165c23fe3d8a1338ec4ab47033133f8fdcb3d'
SOURCE_RESULT='35c60b9f016eee6ef53a1b266b48e6366e1ec52d'
T029=ROOT.parent/'R3-T029'
T030=ROOT.parent/'R3-T030'
D20=ROOT.parent/'R3-T019-DEDICATED20'
OLD=Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite')
POOL=D20/'private'/'budget.sqlite'
TOOLS=ROOT.parent/'R3-T001-INTEGRATE-OFFLINE'/'release'/'local_tools'
CASES=['e2e-09-si-photonics-ar-dualband','e2e-08-advanced-packaging-uv','e2e-02-space-qkd-cband']
PLANNING_SHA=dict(zip(CASES,['e6e3dff45597fc5c05d1dcbce5719130202e1ffc441f688af2a36d627c09e57e','00ebda66a45873229b0b80f754c7fcf1c745ff8c2dfbb7f95e860af494c8aaaf','1f6f22289fdb0fafb14bf519b1bd350cb0a980637aef72e36692220b773adfc7']))
MAX_TOKENS=16384
sys.path.insert(0,str(ROOT/'local_tools'))

def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def digest(o):return hashlib.sha256(json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()
def save(p,o,*,new=False):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);data=(json.dumps(o,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    if new:
        with p.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    else:
        q=p.with_suffix(p.suffix+'.next')
        with q.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(q,p)
def module(p,n):
    spec=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(spec);sys.modules[n]=m;spec.loader.exec_module(m);return m


def bind():
    assert subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()==IMPL
    assert not subprocess.check_output(['git','-C',str(SOURCE),'status','--porcelain','--untracked-files=no'],text=True).strip()
    for n in list(sys.modules):
        if n.split('.')[0] in {'config','llm','optomind_research','focused_research','tmm_engine','task_sheet','research_routes','route_stage'}:del sys.modules[n]
    focus=SOURCE/'focused_research';vendor=focus/'vendor'/'review';sys.path[:0]=[str(vendor),str(focus),str(SOURCE/'veritmm'),str(SOURCE)]
    pkg=types.ModuleType('focused_research');pkg.__path__=[str(focus)];pkg.__package__='focused_research';sys.modules['focused_research']=pkg
    runner=module(focus/'run.py','t031_fixed_runner')
    names=['optomind_research.query_planner','focused_research.search_stage','focused_research.local_campaign_guard','focused_research.task_sheet_recovery','task_sheet','tmm_engine.material_registry']
    origins={n:str(Path(importlib.import_module(n).__file__).resolve()) for n in names};assert all(Path(p).is_relative_to(SOURCE) for p in origins.values()),'stale source import'
    return runner,origins


def reconcile(case):
    from aggregate_budget import checked_totals
    with sqlite3.connect(OLD.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as b:
        a.row_factory=b.row_factory=sqlite3.Row;r=checked_totals(a,b,Decimal(0),load(ROOT/'CASE_HISTORY_FOR_GUARD.json'),'R3-T031:'+case,admission=False)
    r.update(checked_at=datetime.now(timezone.utc).isoformat(),read_only=True,both_ledgers_locked_across_check_and_reservation=False)
    return r


def verify_recovery(case):
    st=importlib.import_module('focused_research.search_stage');d=ROOT/'cases'/case/'recovered'
    for name,h in load(d/'manifest.json').items():assert st.digest(st.load(d/name))==h,'recovery manifest mismatch'
    binding=load(d/'recovery.json');sheet=load(d/'task_sheet.json');planning=T029/'cases'/case/'plan/planning.json';assert sha(planning)==PLANNING_SHA[case]
    rows=[r for r in load(planning)['rounds'] if r['round']==2];assert len(rows)==1
    assert binding['case_id']==case and binding['source_commit']==SOURCE_RESULT and binding['source_round']==2
    assert binding['source_planning_sha256']==PLANNING_SHA[case] and binding['source_response_sha256']==st.digest(rows[0]['raw_response'])
    assert binding['task_sheet_sha256']==st.digest(sheet) and binding['original_question_sha256']==hashlib.sha256(sheet['original_question'].encode()).hexdigest()
    assert binding['new_model_calls']==0 and binding['physics_executed'] is False
    approved=load(ROOT/'preflight/T031_RECOVERY_VERIFICATION.json');assert approved['global_pass'] is True
    assert next(r for r in approved['cases'] if r['case_id']==case)['status']=='pass'
    return sheet,binding


def preflight(case):
    runner,origins=bind();sheet,binding=verify_recovery(case)
    tests=load(ROOT/'preflight/T031_FOCUSED_TEST_RESULT.json');assert tests['passed']==100 and tests['failed']==0 and tests['skipped']==0
    # Reuse the exact, already verified T029 configuration routine, never its plan/case dispatcher.
    old=module(T029/'run_t029.py','t031_original_preparation_helpers');old.faithful(sheet,sheet['original_question'],case)
    billing=module(T030/'run_t030.py','t031_preserved_billing_helpers');cfg=billing.original_billing()
    assert (ROOT/'local_tools/task_budget_guard_220.py').read_bytes()==(T030/'local_tools/task_budget_guard_220.py').read_bytes()
    # Material bytes are the same source identities verified by the local offline stage.
    oldmat=load(T029/'preflight/T029_MATERIAL_INPUT_PREFLIGHT.json')['materials']['canonical_sources']
    assert sha(SOURCE/'veritmm/tmm_engine/rii_cache.db')==oldmat['rii_sqlite']['sha256']
    assert sha(SOURCE/'veritmm/tmm_engine/materials/hfo2.csv')==oldmat['local_csv']['hfo2.csv']['sha256']
    return runner,old,cfg,{'case_id':case,'source_sha':IMPL,'origins':origins,'interpreter':sys.executable,'recovery_binding':binding,'budget':reconcile(case),'wrapper_sha256':sha(__file__),'aggregate_guard_sha256':sha(ROOT/'local_tools/aggregate_budget.py')}


def audited_guard(case,case_root,cfg,binding):
    am=module(D20/'transport/dedicated_qwen_adapter.py','t031_original_qwen_adapter');native=am.load_native_runtime(Path(cfg['paths']['review_runtime']))
    entry,base=am._load_budget_factory(TOOLS/'budgeted_entry.py',ROOT/'local_tools/task_budget_guard_220.py')
    from aggregate_budget import guard_class
    cls=guard_class(base.TaskBudgetGuard,OLD,load(ROOT/'CASE_HISTORY_FOR_GUARD.json'),case_root/'atomic_reservations')
    ledger=native.GlobalBudgetLedger(limit_cny=220,path=POOL);taskid='R3-T031:'+case
    money=cls(ledger,task_id=taskid,lane='recovered_continuation',task_limit_cny=8,max_calls=3,global_limit_cny=220)
    config=am.Dedicated20Config(ledger_path=POOL,audit_root=case_root/'private/audit',key_file=Path(cfg['paths']['key_file']),task_id=taskid,model='qwen3.5-plus',repair_model='qwen3.7-flash',task_limit_cny=8,global_limit_cny=220,max_calls=3,max_retries=0,max_keys=1,max_tokens=MAX_TOKENS)
    pm=importlib.import_module('optomind_research.query_planner');resolver,_=am._load_vendored_model_resolver(Path(pm.__file__))
    adapter=am.FocusedQwenCallAdapter(audited_client_factory=entry.make_audited_client(native,money,config.factory_config()),runtime=native,config=config,guard=money,model_resolver=resolver)
    calls=0;admission={}
    def protected(agent,messages,**kwargs):
        nonlocal calls
        if 'guard' not in admission or admission['guard'].audit['dispatch_attempts']!=calls+1:raise RuntimeError('paid call must pass the live recovered campaign guard first')
        if calls>=3:raise RuntimeError('T031 three actual HTTP attempts exhausted')
        if agent!='QueryPlannerAgent' or kwargs.get('model_tier') in {'standard_model','QueryPlannerFormatRepairAgent'}:raise RuntimeError('automatic repair/fallback HTTP forbidden')
        if kwargs.get('max_tokens')!=MAX_TOKENS or kwargs.get('force_mock') is not False:raise RuntimeError('original real planner wire contract changed')
        fresh=reconcile(case);body,_=native.build_qwen_wire_body(messages,model='qwen3.5-plus',max_output_tokens=MAX_TOKENS,thinking=False,thinking_budget=0,json_mode=True,stream=False,temperature=0.1);estimate=native.estimate_prompt_tokens(body,messages,prompt_token_counter=None)
        amount=Decimal(str(native.estimated_cost_cny({'prompt_tokens':estimate['prompt_tokens'],'completion_tokens':MAX_TOKENS},model='qwen3.5-plus',conservative=True)))
        if Decimal(fresh['case_exposure_cny'])+amount>8 or Decimal(fresh['project_exposure_cny'])+amount>220 or Decimal(fresh['three_case_exposure_cny'])+amount>24:raise RuntimeError('conservative reserve exceeds cumulative monetary gate')
        calls+=1;save(case_root/f'CALL_{calls}_PREFLIGHT.json',{'model':'qwen3.5-plus','wire_body_sha256':hashlib.sha256(body).hexdigest(),'wire_body_bytes':len(body),'native_estimator':estimate,'conservative_reservation_cny':str(amount),'fresh_budget':fresh,'transport_retries':0},new=True)
        answer=adapter(agent,messages,**kwargs);save(case_root/'BUDGET_STATUS.json',money.as_dict());return answer
    pm.call_qwen_chat=protected;planner=pm.QueryPlannerAgent(max_tokens=MAX_TOKENS)
    cg=importlib.import_module('focused_research.local_campaign_guard')
    def persist(snapshot):
        p=case_root/'guard_audit'/f"{snapshot['dispatch_attempts']:02d}_{digest(snapshot)}.json"
        if not p.exists():save(p,snapshot,new=True)
        save(case_root/'GUARD_CURRENT.json',snapshot)
    guard=cg.RecoveredCampaignPlannerGuard(planner,case_id=case,audit_sink=persist,recovered_task_sheet_binding=binding);admission['guard']=guard
    return guard,money,persist


def step(case_root,name,fn):
    start=time.monotonic()
    with (case_root/(name+'.stdout.log')).open('x',encoding='utf-8') as out,(case_root/(name+'.stderr.log')).open('x',encoding='utf-8') as err:
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):result=fn()
    save(case_root/(name+'.EXECUTION.json'),{'stage':name,'exit_code':result if isinstance(result,int) else None,'wall_seconds':time.monotonic()-start,'source_sha':IMPL},new=True)
    return result


def run_case(case):
    runner,old,cfg,prepared=preflight(case);cr=ROOT/'cases'/case;sheet,binding=verify_recovery(case)
    assert not (cr/'CASE_STARTED.json').exists(),'already admitted; no automatic resume/replay'
    with sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as db:assert not db.execute('SELECT 1 FROM r3_task_calls WHERE task_id=?',('R3-T031:'+case,)).fetchone(),'prior persisted attempt exists'
    save(cr/'CASE_STARTED.json',prepared,new=True);guard,money,persist=audited_guard(case,cr,cfg,binding)
    status={'case_id':case,'source_sha':IMPL,'engineering_status':'running','scientific_status':'unassessed','stages':{}}
    try:
        constraints,media,grid=old.configuration(sheet,case)
        for name,value in [('design_constraints.json',constraints),('optical_media.json',media),('wavelengths.json',grid)]:save(cr/name,value,new=True)
        os.environ['SEMANTIC_SCHOLAR_API_KEYS_FILE']=r'F:\OptoMind-Review-2\api_keys\semantic-scholar-api-key.txt'
        rc=step(cr,'01_retrieval',lambda:runner.main(['retrieve','--plan-file',str(cr/'recovered/query_plan.json'),'--output',str(cr/'retrieval'),'--per-query-limit','10','--retrieval-seconds','60']));status['stages']['retrieval_exit']=rc
        ep=cr/'retrieval/evidence_items.json';evidence=load(ep) if ep.exists() else {'schema_version':'focused_evidence_items_v1','items':[]};save(cr/'evidence_items.json',evidence,new=True)
        if not evidence['items']:save(cr/'EMPTY_EVIDENCE_REASON.json',{'retrieval_exit':rc,'reason':'No usable evidence from this bounded retrieval; not proof of literature absence.','model_knowledge_routes_allowed':True},new=True)
        guard.set_stage('route_generation');route=importlib.import_module('route_stage');rr=importlib.import_module('research_routes')
        try:rc=step(cr,'02_routes',lambda:route.main(['generate','--task-sheet',str(cr/'recovered/task_sheet.json'),'--evidence-file',str(cr/'evidence_items.json'),'--constraints-file',str(cr/'design_constraints.json'),'--output',str(cr/'routes')],planner=guard))
        finally:persist(guard.audit)
        status['stages']['route_exit']=rc;p=cr/'routes/research_routes.json'
        if not p.exists():raise ValueError('route contract not produced; no further paid step')
        routes=load(p);cg=importlib.import_module('focused_research.local_campaign_guard');bounds=cg.check_route_thickness_bounds(routes,case_id=case);save(cr/'THICKNESS_PREFLIGHT.json',bounds,new=True)
        indices={r['route_index'] for r in bounds['route_results'] if r['accepted']};selected=copy.deepcopy(routes);selected['routes']=[r for i,r in enumerate(routes['routes']) if i in indices and r['readiness']=='ready']
        rr.validate(selected,sheet,evidence,design_constraints=constraints,candidate_mode=True);save(cr/'routes_for_search.json',selected,new=True)
        if not selected['routes']:raise ValueError('no executable original-bound-compliant route')
        if case==CASES[2]:save(cr/'QKD_THICKNESS_DOMAIN_POLICY.json',{'user_global_thickness_bounds':None,'route_domains':'model-proposed finite positive family design hypotheses; not user manufacturing limits','source':'C2L-R3-0046 HANDOFF'},new=True)
        st=importlib.import_module('focused_research.search_stage')
        rc=step(cr,'03_search',lambda:st.main(['search','--task-sheet',str(cr/'recovered/task_sheet.json'),'--evidence-file',str(cr/'evidence_items.json'),'--constraints-file',str(cr/'design_constraints.json'),'--routes-file',str(cr/'routes_for_search.json'),'--optical-media',str(cr/'optical_media.json'),'--wavelengths',str(cr/'wavelengths.json'),'--cache-dir',str(cr/'cache'),'--max-forward-evaluations','2048','--max-new-evals','32','--max-workers','2','--backend','torch','--parameterization','family','--seed','42','--execute','--output',str(cr/'search')]))
        status['stages']['search_exit']=rc;previous=st.read_search(cr/'search')
        if not previous['archive']['designs']:raise ValueError('no certified observation; skip paid revision')
        guard.set_stage('search_revision')
        try:rc=step(cr,'04_revision',lambda:st.main(['revise','--previous-search',str(cr/'search'),'--execute','--output',str(cr/'revision')],planner=guard))
        finally:persist(guard.audit)
        status['stages']['revision_exit']=rc;plans=load(cr/'revision/refinement_plans.json')
        if plans['plans']:
            rc=step(cr,'05_refine',lambda:st.main(['refine','--previous-search',str(cr/'search'),'--revision-dir',str(cr/'revision'),'--cache-dir',str(cr/'cache'),'--max-forward-evaluations','2048','--max-new-evals','32','--max-workers','2','--backend','torch','--seed','43','--execute','--output',str(cr/'refined')]))
            status['stages']['refine_exit']=rc
        else:status['stages']['refine']='empty_plan_stop'
        status['engineering_status']='completed_bounded_cycle';status['scientific_status']='inspect_certified_results_and_predictions_separately';return 0
    except Exception as exc:
        status.update(engineering_status='blocked',failure_type=type(exc).__name__,reason=str(exc),scientific_status='preserve_any_actual_results; no acceptance inferred');return 2
    finally:
        persist(guard.audit);save(cr/'BUDGET_STATUS.json',money.as_dict());status['budget_after']=reconcile(case);save(cr/'CASE_RESULT.json',status,new=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['preflight','case']);p.add_argument('--case',choices=CASES,required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.action=='preflight':print(json.dumps(preflight(a.case)[3],ensure_ascii=False));return 0
    if not a.execute:raise RuntimeError('explicit admitted case execution required')
    return run_case(a.case)

if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
