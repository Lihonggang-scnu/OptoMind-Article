"""Task-local audited T030 entry wiring; no scientific/core edits."""
from __future__ import annotations
import argparse
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
import types

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t030a')
IMPL = '13b238f99c2ead4c2c126908cd2914d8429d037f'
PREVIOUS = ROOT.parent/'R3-T028'/'refined'
PLANS = ROOT.parent/'R3-T028'/'revision'/'refinement_plans.json'
CACHE = ROOT.parent/'R3-T027'/'cache'
D20 = ROOT.parent/'R3-T019-DEDICATED20'
OLD = Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite')
POOL = D20/'private'/'budget.sqlite'
TOOLS = ROOT.parent/'R3-T001-INTEGRATE-OFFLINE'/'release'/'local_tools'
MODEL = 'qwen3.5-plus'
MAX_TOKENS = 16384
TASK_ID = 'R3-T030'
HISTORY = ROOT/'accounting-history'/'CASE_HISTORY_FOR_GUARD.json'
sys.path.insert(0, str(ROOT/'local_tools'))


def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save_new(p, obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('x',encoding='utf-8') as f:
        json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())


def module(p,n):
    spec=importlib.util.spec_from_file_location(n,p);m=importlib.util.module_from_spec(spec);sys.modules[n]=m;spec.loader.exec_module(m);return m


def bind():
    assert subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()==IMPL
    assert not subprocess.check_output(['git','-C',str(SOURCE),'status','--porcelain','--untracked-files=no'],text=True).strip()
    for name in list(sys.modules):
        if name.split('.')[0] in {'config','llm','optomind_research','focused_research','tmm_engine'}:del sys.modules[name]
    focus=SOURCE/'focused_research';vendor=focus/'vendor'/'review'
    sys.path[:0]=[str(vendor),str(focus),str(SOURCE/'veritmm'),str(SOURCE)]
    pkg=types.ModuleType('focused_research');pkg.__path__=[str(focus)];pkg.__package__='focused_research';sys.modules['focused_research']=pkg
    module(focus/'run.py','t030_fixed_runner')
    names=['optomind_research.query_planner','focused_research.search_stage','focused_research.direction_expansion','focused_research.directional_reflection','tmm_engine.material_registry']
    origins={n:str(Path(importlib.import_module(n).__file__).resolve()) for n in names}
    assert all(Path(p).is_relative_to(SOURCE) for p in origins.values()),'wrong import source'
    return origins


def original_billing():
    expected={D20/'transport'/'dedicated_qwen_adapter.py':'b2a52f685d6d0c85abd56a5cdada9fd7eb787c7d2e53f2383e9a86431aee811d',TOOLS/'budgeted_entry.py':'1a6832ade3f4b05d24a31102d106894d97e8e1f4a7af24883349783c4f88d512',TOOLS/'task_budget_guard.py':'d09c24cb6895790dc1c53a6807e743b09a8737135d65ec081c9b7c206060c461'}
    cfg=load(D20/'RUN_CONFIG.local-only.json');native_path=Path(cfg['paths']['review_runtime']);expected[native_path]='7fa510a1b9da12a4403868259bb2bd124e36b0d2b28718226af485d74c00c005'
    for p,h in expected.items():assert sha(p)==h,'original billing source changed'
    original=(TOOLS/'task_budget_guard.py').read_bytes();revised=(ROOT/'local_tools'/'task_budget_guard_220.py').read_bytes()
    assert revised==original.replace(b'MAX_AUTHORIZED_GLOBAL_CNY = Decimal("140")',b'MAX_AUTHORIZED_GLOBAL_CNY = Decimal("220")')
    return cfg


def reconcile():
    from campaign_budget import checked_totals
    history=load(HISTORY)
    with sqlite3.connect(OLD.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as b:
        a.row_factory=b.row_factory=sqlite3.Row
        totals=checked_totals(a,b,Decimal(0),history,TASK_ID)
    totals.update(checked_at=datetime.now(timezone.utc).isoformat(),read_only=True,
                  both_ledgers_locked_across_check_and_native_reservation=False,
                  original_ledger_sha256=sha(OLD),pool_ledger_sha256=sha(POOL))
    return totals


def preflight():
    origins=bind();original_billing()
    evidence=load(ROOT/'preflight'/'T030_PREFLIGHT_REPORT_FINAL.json')
    assert evidence['targeted_tests']['passed']==69 and evidence['targeted_tests']['skipped']==0
    st=importlib.import_module('focused_research.search_stage');previous=st.read_search(PREVIOUS)
    b=load(ROOT/'preflight'/'T030_ARCHIVE_BINDINGS_FINAL.json')
    assert previous['manifest_sha256']==b['t028_current']['read_search_manifest_digest']
    assert sha(PLANS)==b['t028_original_revision']['refinement_plans_file_sha256']
    return {'source_sha':IMPL,'interpreter':sys.executable,'origins':origins,'budget':reconcile(),
            'previous_search':str(PREVIOUS),'previous_manifest_sha256':previous['manifest_sha256'],
            'plans':str(PLANS),'plans_sha256':sha(PLANS),'cache':str(CACHE),
            'model':MODEL,'max_model_http':2,'implicit_retries':0,'max_forward_rows':8192,'max_new_certifications':128,
            'wrapper_sha256':sha(__file__),'new_model_calls':0}


def planner(stage):
    cfg=original_billing();am=module(D20/'transport'/'dedicated_qwen_adapter.py','t030_preserved_qwen_adapter')
    native=am.load_native_runtime(Path(cfg['paths']['review_runtime']))
    entry,base=am._load_budget_factory(TOOLS/'budgeted_entry.py',ROOT/'local_tools'/'task_budget_guard_220.py')
    from campaign_budget import guard_class
    cls=guard_class(base.TaskBudgetGuard,OLD,load(HISTORY),ROOT/'atomic_reservations')
    ledger=native.GlobalBudgetLedger(limit_cny=220,path=POOL)
    money=cls(ledger,task_id=TASK_ID,lane='directional_continuation',task_limit_cny=8,max_calls=2,global_limit_cny=220)
    config=am.Dedicated20Config(ledger_path=POOL,audit_root=ROOT/'private'/'audit',key_file=Path(cfg['paths']['key_file']),task_id=TASK_ID,model=MODEL,repair_model='qwen3.7-flash',task_limit_cny=8,global_limit_cny=220,max_calls=2,max_retries=0,max_keys=1,max_tokens=MAX_TOKENS)
    pm=importlib.import_module('optomind_research.query_planner');resolver,_=am._load_vendored_model_resolver(Path(pm.__file__))
    adapter=am.FocusedQwenCallAdapter(audited_client_factory=entry.make_audited_client(native,money,config.factory_config()),runtime=native,config=config,guard=money,model_resolver=resolver)
    delegated=False
    def protected(agent,messages,**kwargs):
        nonlocal delegated
        if delegated:raise RuntimeError('one HTTP request per T030 stage; no repair/fallback')
        if agent!='QueryPlannerAgent' or kwargs.get('model_tier') in {'standard_model','QueryPlannerFormatRepairAgent'}:raise RuntimeError('alternate model/repair forbidden')
        if kwargs.get('force_mock') is not False or kwargs.get('max_tokens')!=MAX_TOKENS or kwargs.get('temperature')!=0.1:raise RuntimeError('unexpected original planner transport settings')
        if kwargs.get('response_format')!={'type':'json_object'} or kwargs.get('stream') not in (None,False):raise RuntimeError('unexpected planner wire settings')
        fresh=reconcile()
        body,_=native.build_qwen_wire_body(messages,model=MODEL,max_output_tokens=MAX_TOKENS,thinking=False,thinking_budget=0,json_mode=True,stream=False,temperature=0.1)
        estimate=native.estimate_prompt_tokens(body,messages,prompt_token_counter=None)
        amount=Decimal(str(native.estimated_cost_cny({'prompt_tokens':estimate['prompt_tokens'],'completion_tokens':MAX_TOKENS},model=MODEL,conservative=True)))
        if Decimal(fresh['case_exposure_cny'])+amount>8 or Decimal(fresh['project_exposure_cny'])+amount>220:raise RuntimeError('conservative estimate exceeds cumulative authorization')
        save_new(ROOT/(stage+'_CALL_PREFLIGHT.json'),{'source_sha':IMPL,'model':MODEL,'wire_body_sha256':hashlib.sha256(body).hexdigest(),'wire_body_bytes':len(body),'native_prompt_estimate':estimate,'conservative_reservation_cny':str(amount),'budget_before':fresh,'transport_retries':0,'stage_http_max':1,'task_http_max':2})
        delegated=True
        result=adapter(agent,messages,**kwargs)
        save_new(ROOT/(stage+'_BUDGET_STATUS.json'),money.as_dict())
        return result
    pm.call_qwen_chat=protected
    return pm.QueryPlannerAgent(max_tokens=MAX_TOKENS)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['preflight','authorize','expand','reflect']);parser.add_argument('--execute',action='store_true');args=parser.parse_args()
    if args.action=='preflight':print(json.dumps(preflight(),ensure_ascii=False));return
    if not args.execute:raise RuntimeError('explicit --execute required')
    preflight()
    if args.action=='authorize':
        from campaign_budget import authorize_existing_pool
        result=authorize_existing_pool(POOL,load(ROOT/'AUTH_CONFIRMATION.json'));save_new(ROOT/'MONETARY_MIGRATION.json',result);print(json.dumps(result));return
    bind()
    if args.action=='expand':
        output=ROOT/'expansion'
        if output.exists():raise RuntimeError('expansion already exists; no duplicate call')
        save_new(ROOT/'EXPANSION_STARTED.json',{'at':datetime.now(timezone.utc).isoformat(),'source_sha':IMPL,'budget':reconcile()})
        p=planner('expansion');fn=importlib.import_module('focused_research.direction_expansion').expand_directions
        result=fn(PREVIOUS,load(PLANS),output=output,planner=p,max_forward_evaluations=8192)
    else:
        output=ROOT/'reflection';search=ROOT/'search'
        if output.exists():raise RuntimeError('reflection already exists; no duplicate call')
        st=importlib.import_module('focused_research.search_stage');st.read_search(search)
        summary=load(search/'summary.json');assert summary['metering']['search_forward_design_rows']<=8192 and summary['metering']['certification_new_evaluations']<=128
        save_new(ROOT/'REFLECTION_STARTED.json',{'at':datetime.now(timezone.utc).isoformat(),'source_sha':IMPL,'budget':reconcile()})
        p=planner('reflection');fn=importlib.import_module('focused_research.directional_reflection').reflect_on_branches
        result=fn(search,output=output,planner=p)
    save_new(ROOT/(args.action.upper()+'_ACCOUNTING_AFTER.json'),reconcile())
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
