"""Task-local T029 wiring. No core algorithms or model answers are modified."""
from __future__ import annotations
import argparse
import contextlib
import copy
from datetime import datetime, timezone
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

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t029a')
IMPL = 'e1eba28e5350632ed7cf387c643f8d16ede1ef72'
D20 = ROOT.parent/'R3-T019-DEDICATED20'
OLD = Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite')
POOL = D20/'private'/'budget.sqlite'
TOOLS = ROOT.parent/'R3-T001-INTEGRATE-OFFLINE'/'release'/'local_tools'
CASES = ['e2e-09-si-photonics-ar-dualband', 'e2e-08-advanced-packaging-uv', 'e2e-02-space-qkd-cband']
EXPECTED = {
    CASES[0]: [('mean_reflectance','minimize',1260,1360), ('mean_reflectance','minimize',1530,1565)],
    CASES[1]: [('mean_transmittance','minimize',355,365), ('mean_transmittance','minimize',1030,1080), ('mean_transmittance','maximize',420,700)],
    CASES[2]: [('mean_transmittance','maximize',1530,1565), ('mean_reflectance','maximize',400,700)],
}
MAX_TOKENS = 16384


def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def canonical(o):return hashlib.sha256(json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def save(p,o,*,new=False):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(o,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    if new:
        with p.open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    else:
        tmp=p.with_suffix(p.suffix+'.tmp')
        with tmp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)


def module(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    result=importlib.util.module_from_spec(spec);sys.modules[name]=result;spec.loader.exec_module(result);return result


def bind():
    head=subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()
    assert head==IMPL,'fixed source SHA mismatch'
    assert not subprocess.check_output(['git','-C',str(SOURCE),'status','--porcelain','--untracked-files=no'],text=True).strip(),'tracked changes'
    for name in list(sys.modules):
        if name.split('.')[0] in {'config','llm','optomind_research','focused_research','tmm_engine'}:
            del sys.modules[name]
    focus=SOURCE/'focused_research';vendor=focus/'vendor'/'review'
    sys.path[:0]=[str(vendor),str(focus),str(SOURCE/'veritmm'),str(SOURCE)]
    pkg=types.ModuleType('focused_research');pkg.__path__=[str(focus)];pkg.__package__='focused_research';sys.modules['focused_research']=pkg
    runner=module(focus/'run.py','t029_fixed_runner')
    names=['config.qwen_config','llm.qwen_chat_client','optomind_research.query_planner','focused_research.search_stage','focused_research.local_campaign_guard','tmm_engine.material_registry']
    origins={name:str(Path(importlib.import_module(name).__file__).resolve()) for name in names}
    assert all(Path(p).is_relative_to(SOURCE) for p in origins.values()),'stale import origin'
    return runner,origins


def reconcile():
    result=[]
    for path in (OLD,POOL):
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory=sqlite3.Row;rows=db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
        used=sum((Decimal(str(r['actual_cny'] or 0)) for r in rows if r['status']=='settled'),Decimal(0))
        held=sum((Decimal(str(r['amount_cny'] or 0)) for r in rows if r['status']!='settled'),Decimal(0))
        result.append({'path':str(path),'settled_cny':str(used),'held_cny':str(held),'unresolved_count':sum(r['status']!='settled' for r in rows),'sha256':sha(path)})
    if any(r['unresolved_count'] for r in result):raise RuntimeError('unresolved project spend blocks all paid work')
    total=sum((Decimal(r['settled_cny'])+Decimal(r['held_cny']) for r in result),Decimal(0))
    assert total<=120,'project cap exhausted'
    return {'at':datetime.now(timezone.utc).isoformat(),'project_cap_cny':120,'aggregate_exposure_cny':str(total),'remaining_cny':str(Decimal(120)-total),'ledgers':result}


def source_checks():
    paths={D20/'transport'/'dedicated_qwen_adapter.py':'b2a52f685d6d0c85abd56a5cdada9fd7eb787c7d2e53f2383e9a86431aee811d',TOOLS/'budgeted_entry.py':'1a6832ade3f4b05d24a31102d106894d97e8e1f4a7af24883349783c4f88d512',TOOLS/'task_budget_guard.py':'d09c24cb6895790dc1c53a6807e743b09a8737135d65ec081c9b7c206060c461'}
    cfg=load(D20/'RUN_CONFIG.local-only.json');native=Path(cfg['paths']['review_runtime']);paths[native]='7fa510a1b9da12a4403868259bb2bd124e36b0d2b28718226af485d74c00c005'
    for p,h in paths.items():assert sha(p)==h,'billing source hash mismatch'
    return cfg


def preflight():
    runner,origins=bind();source_checks();budget=reconcile()
    checks=load(ROOT/'preflight'/'T029_MATERIAL_INPUT_PREFLIGHT.json')
    assert checks['input_provenance']['all_exact']
    sources=checks['materials']['canonical_sources'];assert sha(sources['rii_sqlite']['path'])==sources['rii_sqlite']['sha256']
    assert sha(sources['local_csv']['hfo2.csv']['path'])==sources['local_csv']['hfo2.csv']['sha256']
    override=load(ROOT/'USER_BUDGET_OVERRIDE.json');assert override['per_case_cap_cny']==8 and override['batch_cap_cny']==24
    report={'source_sha':IMPL,'python':sys.executable,'origins':origins,'wrapper_sha256':sha(__file__),'aggregate_guard_sha256':sha(ROOT/'aggregate_budget.py'),'budget':budget,'per_case_cap_cny':8,'batch_cap_cny':24,'project_cap_cny':120,'existing_chain_cap_cny':15,'existing_pool_cap_cny':20,'per_case_http_max':4,'shared_contract_repair_max':1,'transport_retries':0,'model':'qwen3.5-plus','max_output_tokens':MAX_TOKENS,'paid_calls':0,'new_pools':0,'guard_atomicity':'Original-ledger BEGIN IMMEDIATE lock-only plus unchanged pool TaskBudgetGuard transaction; aggregate checks before native reservation.'}
    save(ROOT/'LAUNCHER_PREFLIGHT.json',report)
    return report


def audited_planner(case,case_root):
    cfg=source_checks();adapter_module=module(D20/'transport'/'dedicated_qwen_adapter.py','t029_original_adapter')
    native=adapter_module.load_native_runtime(Path(cfg['paths']['review_runtime']))
    entry,base=adapter_module._load_budget_factory(TOOLS/'budgeted_entry.py',TOOLS/'task_budget_guard.py')
    from aggregate_budget import guard_class
    cls=guard_class(base.TaskBudgetGuard,OLD,case_root/'atomic_reservations')
    ledger=native.GlobalBudgetLedger(limit_cny=20,path=POOL)
    taskid='R3-T029:'+case
    money=cls(ledger,task_id=taskid,lane='t029_initial_campaign',task_limit_cny=8,max_calls=4,global_limit_cny=20)
    config=adapter_module.Dedicated20Config(ledger_path=POOL,audit_root=case_root/'private'/'audit',key_file=Path(cfg['paths']['key_file']),task_id=taskid,model='qwen3.5-plus',repair_model='qwen3.7-flash',task_limit_cny=8,global_limit_cny=20,max_calls=4,max_retries=0,max_keys=1,max_tokens=MAX_TOKENS)
    planner_module=importlib.import_module('optomind_research.query_planner')
    resolver,_=adapter_module._load_vendored_model_resolver(Path(planner_module.__file__))
    adapter=adapter_module.FocusedQwenCallAdapter(audited_client_factory=entry.make_audited_client(native,money,config.factory_config()),runtime=native,config=config,guard=money,model_resolver=resolver)
    calls=0
    def protected(*args,**kwargs):
        nonlocal calls
        reconcile()
        if calls>=4:raise RuntimeError('four model dispatches exhausted')
        calls+=1;save(case_root/'TRANSPORT_CALL_MARKERS.json',{'attempted_delegations':calls,'max':4,'no_transport_retries':True})
        answer=adapter(*args,**kwargs)
        save(case_root/'BUDGET_STATUS.json',money.as_dict())
        return answer
    planner_module.call_qwen_chat=protected
    planner=planner_module.QueryPlannerAgent(max_tokens=MAX_TOKENS)
    cg=importlib.import_module('focused_research.local_campaign_guard')
    def persist(snapshot):
        ident=canonical(snapshot);save(case_root/'guard_audit'/f"{snapshot['dispatch_attempts']:02d}_{ident}.json",snapshot) if not (case_root/'guard_audit'/f"{snapshot['dispatch_attempts']:02d}_{ident}.json").exists() else None
        save(case_root/'GUARD_CURRENT.json',snapshot)
    return cg.CampaignPlannerGuard(planner,case_id=case,audit_sink=persist),money,persist


def faithful(sheet,question,case):
    import task_sheet as ts
    ts.validate(sheet,question=question)
    actual=[]
    for goal in sheet['subgoals']:
        m=goal['executable_metric'];r=m['region'];actual.append((m['metric'],m['sense'],*r['wavelength_nm']))
        if r['angle_deg']!=0 or r['polarization']!='unpolarized' or m['mean_reduction']!='wavelength_integral_mean':raise ValueError('generated evaluation protocol differs from disclosed original/approved conditions')
    if sorted(actual)!=sorted(EXPECTED[case]):raise ValueError('original goal set differs; do not replace, drop or add goals')
    if case==CASES[2] and not any(x['kind']=='assumption' for x in sheet['unknowns_assumptions']):raise ValueError('QKD execution assumptions not separately labeled')


def configuration(sheet,case):
    films=['HfO2','SiO2'] if case==CASES[2] else ['Ta2O5','SiO2'];substrate='silicon' if case==CASES[0] else 'fused_silica';maxlayers={CASES[0]:12,CASES[1]:36,CASES[2]:30}[case]
    rows=sheet['explicit_constraints']
    def refs(words):return [r['id'] for r in rows if any(w.lower() in r['original_span'].lower() for w in words)]
    ids={'allowed_materials':refs(films),'max_layers':refs([str(maxlayers)]),'substrate':refs(['硅作为','硅基底','硅元件'] if case==CASES[0] else ['熔融石英']),'incident_medium':refs(['空气','air'])}
    if any(not v for v in ids.values()):raise ValueError('generated task lacks source IDs for original material/layer/substrate/air constraints')
    constraints={'allowed_materials':films,'max_layers':maxlayers,'substrate':substrate,'incident_medium':'air','constraint_source_ids':ids,'assumptions':{}}
    media={'air':{'constant_n':1.0,'constant_k':0.0},'SiO2':{'material':'SiO2','provider':'rii_sqlite','dataset_id':'411'}}
    if case==CASES[2]:media['HfO2']={'material':'hfo2','provider':'local_csv'}
    else:media['Ta2O5']={'material':'Ta2O5','provider':'rii_sqlite','dataset_id':'415'}
    if case==CASES[0]:media['silicon']={'material':'Si','provider':'rii_sqlite','dataset_id':'608'}
    else:media['fused_silica']={'material':'fused_silica','provider':'rii_sqlite','dataset_id':'835'}
    start,end={CASES[0]:(1260,1565),CASES[1]:(355,1080),CASES[2]:(400,1565)}[case]
    return constraints,media,list(range(start,end+1))


def step(case_root,name,fn):
    started=time.monotonic()
    with (case_root/(name+'.stdout.log')).open('x',encoding='utf-8') as out,(case_root/(name+'.stderr.log')).open('x',encoding='utf-8') as err:
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):result=fn()
    save(case_root/(name+'.EXECUTION.json'),{'step':name,'wall_seconds':time.monotonic()-started,'return_code':result if isinstance(result,int) else None,'source_sha':IMPL},new=True)
    return result


def run_case(case):
    preflight();runner,_=bind();case_root=ROOT/'cases'/case
    case_root.mkdir(parents=True,exist_ok=True)
    if (case_root/'CASE_STARTED.json').exists():raise RuntimeError('initial-run guard has no resume; case already admitted')
    with sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as db:
        assert not db.execute('SELECT 1 FROM r3_task_calls WHERE task_id=?',('R3-T029:'+case,)).fetchone(),'prior persisted attempt exists'
    save(case_root/'CASE_STARTED.json',{'at':datetime.now(timezone.utc).isoformat(),'case_id':case,'source_sha':IMPL,'budget_before':reconcile(),'wrapper_sha256':sha(__file__)},new=True)
    question=load(SOURCE/'focused_research'/'campaigns'/'t029'/'inputs'/case/'FRESH_REQUEST.json')['question']
    save(case_root/'FRESH_REQUEST.json',{'question':question},new=True)
    guard,money,persist=audited_planner(case,case_root)
    status={'case_id':case,'source_sha':IMPL,'engineering_status':'in_progress','scientific_status':'unassessed','stages':{}}
    try:
        guard.set_stage('task_sheet')
        try:outcome=step(case_root,'01_plan',lambda:runner.generate_plan(question,runner.prepare_output(case_root/'plan'),planner=guard))
        finally:persist(guard.audit)
        status['stages']['plan']=outcome['status']
        if outcome['status']!='ok':raise ValueError('task-sheet contract not accepted: '+outcome['status'])
        sheet=outcome['task_sheet'];faithful(sheet,question,case)
        constraints,media,grid=configuration(sheet,case)
        save(case_root/'design_constraints.json',constraints,new=True);save(case_root/'optical_media.json',media,new=True);save(case_root/'wavelengths.json',grid,new=True)
        # Existing free retrieval only. Native loader reads the original key
        # file in memory; no credential value is copied or printed.
        os.environ['SEMANTIC_SCHOLAR_API_KEYS_FILE']=r'F:\OptoMind-Review-2\api_keys\semantic-scholar-api-key.txt'
        rc=step(case_root,'02_retrieval',lambda:runner.main(['retrieve','--plan-file',str(case_root/'plan'/'query_plan.json'),'--output',str(case_root/'retrieval'),'--per-query-limit','10','--retrieval-seconds','60']))
        status['stages']['retrieval_exit']=rc
        evidence_path=case_root/'retrieval'/'evidence_items.json'
        evidence=load(evidence_path) if evidence_path.exists() else {'schema_version':'focused_evidence_items_v1','items':[]}
        save(case_root/'evidence_items.json',evidence,new=True)
        if not evidence['items']:save(case_root/'EMPTY_EVIDENCE_REASON.json',{'reason':'Existing free bounded retrieval did not provide usable evidence; this does not establish literature absence.','retrieval_exit':rc,'model_knowledge_routes_allowed':True},new=True)
        guard.set_stage('route_generation');route=importlib.import_module('route_stage');rr=importlib.import_module('research_routes')
        try:rc=step(case_root,'03_routes',lambda:route.main(['generate','--task-sheet',str(case_root/'plan'/'task_sheet.json'),'--evidence-file',str(case_root/'evidence_items.json'),'--constraints-file',str(case_root/'design_constraints.json'),'--output',str(case_root/'routes')],planner=guard))
        finally:persist(guard.audit)
        status['stages']['routes_exit']=rc
        routes_file=case_root/'routes'/'research_routes.json'
        if not routes_file.exists():raise ValueError('route contract not produced')
        routes=load(routes_file);cg=importlib.import_module('focused_research.local_campaign_guard');bounds=cg.check_route_thickness_bounds(routes,case_id=case);save(case_root/'THICKNESS_PREFLIGHT.json',bounds,new=True)
        indices={r['route_index'] for r in bounds['route_results'] if r['accepted']};selected=copy.deepcopy(routes);selected['routes']=[r for i,r in enumerate(routes['routes']) if i in indices and r['readiness']=='ready']
        rr.validate(selected,sheet,evidence,design_constraints=constraints,candidate_mode=True)
        save(case_root/'routes_for_search.json',selected,new=True)
        if not selected['routes']:raise ValueError('no executable original-bound-compliant routes')
        if case==CASES[2]:save(case_root/'QKD_THICKNESS_DOMAIN_POLICY.json',{'user_global_thickness_bounds':None,'route_domains':'model-proposed finite per-family design hypotheses; not manufacturing constraints','source':'C2L-R3-0043 HANDOFF'},new=True)
        search=importlib.import_module('focused_research.search_stage')
        rc=step(case_root,'04_search',lambda:search.main(['search','--task-sheet',str(case_root/'plan'/'task_sheet.json'),'--evidence-file',str(case_root/'evidence_items.json'),'--constraints-file',str(case_root/'design_constraints.json'),'--routes-file',str(case_root/'routes_for_search.json'),'--optical-media',str(case_root/'optical_media.json'),'--wavelengths',str(case_root/'wavelengths.json'),'--cache-dir',str(case_root/'cache'),'--max-forward-evaluations','2048','--max-new-evals','32','--max-workers','2','--backend','torch','--parameterization','family','--seed','42','--execute','--output',str(case_root/'search')]))
        status['stages']['search_exit']=rc
        previous=search.read_search(case_root/'search')
        if not previous['archive']['designs']:raise ValueError('no certified search incumbent; skip revision')
        guard.set_stage('search_revision')
        try:rc=step(case_root,'05_revision',lambda:search.main(['revise','--previous-search',str(case_root/'search'),'--execute','--output',str(case_root/'revision')],planner=guard))
        finally:persist(guard.audit)
        status['stages']['revision_exit']=rc
        plans=load(case_root/'revision'/'refinement_plans.json')
        if plans['plans']:
            rc=step(case_root,'06_refine',lambda:search.main(['refine','--previous-search',str(case_root/'search'),'--revision-dir',str(case_root/'revision'),'--cache-dir',str(case_root/'cache'),'--max-forward-evaluations','2048','--max-new-evals','32','--max-workers','2','--backend','torch','--seed','43','--execute','--output',str(case_root/'refined')]))
            status['stages']['refine_exit']=rc
        else:status['stages']['refine']='empty_plan_stop'
        status['engineering_status']='completed_bounded_cycle';status['scientific_status']='inspect_certified_results_and_predictions_separately'
        return 0
    except Exception as exc:
        status.update(engineering_status='blocked',failure_type=type(exc).__name__,reason=str(exc),scientific_status='preserve_any_measured_results; no acceptance inferred')
        return 2
    finally:
        persist(guard.audit);save(case_root/'BUDGET_STATUS.json',money.as_dict());status['budget_after']=reconcile();save(case_root/'CASE_RESULT.json',status,new=True)


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['preflight','case']);p.add_argument('--case',choices=CASES);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if a.stage=='preflight':print(json.dumps(preflight(),ensure_ascii=False));return 0
    if not a.execute or not a.case:raise RuntimeError('explicit admitted case execution required')
    return run_case(a.case)


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
