"""Fresh, serial T032 entrypoint wiring. No scientific source or prompt changes."""
from __future__ import annotations
import argparse,contextlib,copy,hashlib,importlib,json,os,sqlite3,subprocess,sys,time,traceback
from pathlib import Path
from decimal import Decimal
R=Path(__file__).resolve().parent;T=R.parent;SOURCE=Path(r'D:\OptoMind-Article-3\r3t037a')
IMPL='49861e169084976069d9ef1008e4fafc84b86bb8'
sys.dont_write_bytecode=True;sys.path.insert(0,str(R/'local_tools'))
sys.path.insert(0,str(T/'R3-T034'))
import run_t034 as reused
reused.SOURCE=SOURCE;reused.IMPL=IMPL
save,load,sha,digest,module=reused.save,reused.load,reused.sha,reused.digest,reused.module
AGG=module(R/'local_tools/aggregate_budget.py','t037_bound_aggregate')
CASES=['e2e-01-methane-swir-window','e2e-04-solarblind-uv-triple','e2e-05-visual-absorber-cr'];OLD=reused.OLD;POOL=reused.POOL;D20=reused.D20;MAX_TOKENS=16384
EXPECTED={CASES[0]:[('mean_transmittance','maximize',1000,1700),('mean_reflectance','maximize',300,450)],CASES[1]:[('mean_transmittance','maximize',255,280),('mean_transmittance','minimize',300,700),('mean_transmittance','minimize',700,1100)],CASES[2]:[('mean_reflectance','minimize',400,700),('mean_reflectance','maximize',700,2000)]}
def reconcile(case):
 with sqlite3.connect(OLD.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as b:
  a.row_factory=b.row_factory=sqlite3.Row;x=AGG.checked_totals(a,b,Decimal(0),load(R/'CASE_HISTORY_FOR_GUARD.json'),'R3-T037:'+case,admission=False)
 x['read_only']=True;x['both_ledgers_locked_across_check_and_reservation']=False;return x
def preflight(case):
 assert case==CASES[1],'Only originalcase04 is authorized forT037'
 runner,origins=reused.bind();state=load(R.parents[1]/'state.json')
 assert state['processed']['R3-T032::C2L-R3-0047']['status']=='blocked'
 assert state['processed']['R3-T035::C2L-R3-0050']['status']=='completed'
 assert state['processed']['R3-T036::C2L-R3-0051']['status']=='blocked'
 assert state['processed']['R3-T037::C2L-R3-0052']['claim_readback_verified'] is True
 admission=load(R/'preflight/T037_LOCAL_ACCEPTANCE.json');assert admission['global_pass'] is True,'offline material/category admission not passed'
 for rel,h in load(R/'OFFLINE_VALIDATION.json')['implementation_file_sha256'].items():assert sha(SOURCE/rel)==h,'tested source changed'
 for name in ['material_capabilities','route_consumer','research_routes']:
  mod=importlib.import_module('focused_research.'+name);origins[name]=str(Path(mod.__file__).resolve());assert Path(mod.__file__).resolve().is_relative_to(SOURCE)
 assert (R/'local_tools/task_budget_guard_220.py').read_bytes()==(R.parent/'R3-T030/local_tools/task_budget_guard_220.py').read_bytes()
 cfg=module(R.parent/'R3-T030/run_t030.py','t037_original_billing').original_billing()
 oldcase=R.parent/'R3-T032/cases'/case
 assert load(oldcase/'CASE_RESULT.json')['engineering_status']=='blocked_pre_dispatch'
 assert not (oldcase/'plan/task_sheet.json').exists() and not (oldcase/'retrieval').exists(),'unexpected prior paidstage state; no freshguard admission'
 with sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as db:
  assert not db.execute('SELECT 1 FROM r3_task_calls WHERE task_id IN (?,?,?)',tuple(task+':'+case for task in ['R3-T032','R3-T036','R3-T037'])).fetchone(),'unexpected prior04HTTP; reconcile beforeproceeding'
 question=load(SOURCE/'focused_research/campaigns/t032/inputs'/case/'FRESH_REQUEST.json')['question']
 assert sha(SOURCE/'focused_research/campaigns/t032/inputs'/case/'FRESH_REQUEST.json')=='ef00e448f4c376618e9cdbf451d04b4a6736ff91bd91b6f6af9dd16d7c746638'
 assert sha(R.parent/'R3-T036/SOURCE_BLOCKER.json')==sha(R.parent/'R3-T032/CASE04_BLOCKED_PROGRESS.json'),'original blocker receipt changed'
 assert load(R.parent/'R3-T036/BUDGET_AFTER.json')['original_case04_settled_cny']=='0'
 assert load(R.parent/'R3-T036/BUDGET_AFTER.json')['original_case04_http_across_T032_T036']==0
 assert sha(SOURCE/'veritmm/tmm_engine/rii_cache.db')=='e4c1ec34cc4f765a8400ff87bb7dee8705b6da2cdb11a229cc57ad9bdd274989'
 return runner,cfg,question,{'source_sha':IMPL,'origins':origins,'interpreter':sys.executable,'budget_before':reconcile(case),'launcher_sha256':sha(__file__),'aggregate_gate_sha256':sha(R/'local_tools/aggregate_budget.py'),'same_original_case_continuation_of':'R3-T032','same_original_batch_HTTP_ceiling':12}
def audited_guard(case,cr,cfg):
 am=module(D20/'transport/dedicated_qwen_adapter.py','t032_original_qwen_adapter');native=am.load_native_runtime(Path(cfg['paths']['review_runtime']))
 entry,base=am._load_budget_factory(reused.TOOLS/'budgeted_entry.py',R/'local_tools/task_budget_guard_220.py')
 cls=AGG.guard_class(base.TaskBudgetGuard,OLD,load(R/'CASE_HISTORY_FOR_GUARD.json'),cr/'atomic_reservations')
 money=cls(native.GlobalBudgetLedger(limit_cny=220,path=POOL),task_id='R3-T037:'+case,lane='fresh_bounded_cycle',task_limit_cny=8,max_calls=4,global_limit_cny=220)
 config=am.Dedicated20Config(ledger_path=POOL,audit_root=cr/'private/audit',key_file=Path(cfg['paths']['key_file']),task_id='R3-T037:'+case,model='qwen3.5-plus',repair_model='qwen3.7-flash',task_limit_cny=8,global_limit_cny=220,max_calls=4,max_retries=0,max_keys=1,max_tokens=MAX_TOKENS)
 pm=importlib.import_module('optomind_research.query_planner');resolver,_=am._load_vendored_model_resolver(Path(pm.__file__))
 adapter=am.FocusedQwenCallAdapter(audited_client_factory=entry.make_audited_client(native,money,config.factory_config()),runtime=native,config=config,guard=money,model_resolver=resolver)
 calls=0;admitted={}
 def protected(agent,messages,**kw):
  nonlocal calls
  if 'guard' not in admitted or admitted['guard'].audit['dispatch_attempts']!=calls+1:raise RuntimeError('no live campaign guard admission')
  if calls>=4:raise RuntimeError('case4 actualHTTP ceiling')
  if agent!='QueryPlannerAgent' or kw.get('model_tier') in {'standard_model','QueryPlannerFormatRepairAgent'}:raise RuntimeError('automatic transport repair/fallback prohibited')
  if kw.get('max_tokens')!=MAX_TOKENS or kw.get('force_mock') is not False:raise RuntimeError('wire contract changed')
  fresh=reconcile(case);body,_=native.build_qwen_wire_body(messages,model='qwen3.5-plus',max_output_tokens=MAX_TOKENS,thinking=False,thinking_budget=0,json_mode=True,stream=False,temperature=.1)
  estimate=native.estimate_prompt_tokens(body,messages,prompt_token_counter=None)
  amount=Decimal(str(native.estimated_cost_cny({'prompt_tokens':estimate['prompt_tokens'],'completion_tokens':MAX_TOKENS},model='qwen3.5-plus',conservative=True)))
  if Decimal(fresh['case_exposure_cny'])+amount>8 or Decimal(fresh['project_exposure_cny'])+amount>220 or Decimal(fresh['three_case_exposure_cny'])+amount>24:raise RuntimeError('conservative reserve exceeds historical cumulative gate')
  calls+=1;save(cr/f'CALL_{calls}_PREFLIGHT.json',{'model':'qwen3.5-plus','wire_body_sha256':hashlib.sha256(body).hexdigest(),'wire_body_bytes':len(body),'native_estimator':estimate,'conservative_reservation_cny':str(amount),'fresh_budget':fresh,'transport_retries':0},new=True)
  result=adapter(agent,messages,**kw);save(cr/'BUDGET_STATUS.json',money.as_dict());return result
 pm.call_qwen_chat=protected;planner=pm.QueryPlannerAgent(max_tokens=MAX_TOKENS)
 def persist(x):
  p=cr/'guard_audit'/f"{x['dispatch_attempts']:02d}_{digest(x)}.json"
  if not p.exists():save(p,x,new=True)
  save(cr/'GUARD_CURRENT.json',x)
 cg=importlib.import_module('focused_research.local_campaign_guard');guard=cg.CampaignPlannerGuard(planner,case_id=case,audit_sink=persist);admitted['guard']=guard
 return guard,money,persist
def faithful(sheet,question,case):
 import task_sheet as ts
 ts.validate(sheet,question=question);actual=[]
 for goal in sheet['subgoals']:
  m=goal['executable_metric'];reg=m['region'];actual.append((m['metric'],m['sense'],*reg['wavelength_nm']))
  assert reg['angle_deg']==0 and reg['polarization']=='unpolarized' and m['mean_reduction']=='wavelength_integral_mean','execution assumptions changed'
 assert sorted(actual)==sorted(EXPECTED[case]),'original goals changed'
 assert any(x['kind']=='assumption' for x in sheet['unknowns_assumptions']),'execution assumptions missing'
def configuration(sheet,case):
 assert case==CASES[1]
 rows=sheet['explicit_constraints']
 def refs(words):return [x['id'] for x in rows if any(w in x['original_span'] for w in words)]
 category_ids=refs(['无机介质','不含金属','常规可沉积'])
 spans=[x['original_span'] for x in rows if x['id'] in category_ids]
 assert any('不含金属' in x for x in spans) and any('无机介质' in x for x in spans),'original broadcategory/nometal constraint source IDs absent'
 ids={'allowed_materials':[],'allowed_material_category':category_ids,'max_layers':refs(['30']),'substrate':refs(['熔融石英']),'incident_medium':refs(['空气'])}
 assert all(ids[k] for k in ['allowed_material_category','max_layers','substrate','incident_medium']),'missing original source IDs'
 inventory_envelope=load(R/'material-preflight/execution_materials.json')
 assert inventory_envelope['inventory_is_user_permission'] is False and inventory_envelope['allowed_materials'] is None
 assert inventory_envelope['source_commit']==IMPL
 inventory=inventory_envelope['execution_materials']
 cap=importlib.import_module('focused_research.material_capabilities')
 constraints={'allowed_materials':None,'allowed_material_category':cap.CATEGORY,'execution_materials':inventory,'max_layers':30,'substrate':'fused_silica','incident_medium':'air','constraint_source_ids':ids,'assumptions':{'execution_materials':'The finite available execution inventory was verified against original local registry identities and complete255-1100nm n/k coverage with no extrapolation. It is not the user allowed-material set, an exhaustive dielectric classification, a transparency claim or a manufacturing guarantee. Unsupported identities require capability review; no automatic material substitution.'}}
 assert cap.validate_material_capabilities(constraints)==inventory
 mdir=R/'material-preflight'/case;media=load(mdir/'optical_media.json');grid=load(mdir/'wavelengths.json')
 assert grid==list(range(255,1101)) and media['air']=={'constant_n':1.0,'constant_k':0.0} and media['fused_silica']['dataset_id']==835
 assert all(media[k]==v for k,v in inventory.items()),'optical media differs fromverifiedinventory'
 return constraints,media,grid
def step(cr,name,fn):return reused.step(cr,name,fn)
def run_case(case):
 runner,cfg,question,prepared=preflight(case);cr=R/'cases'/case
 assert not (cr/'CASE_STARTED.json').exists(),'no automatic restart'
 with sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as db:assert not db.execute('SELECT 1 FROM r3_task_calls WHERE task_id=?',('R3-T037:'+case,)).fetchone(),'persisted prior attempt; reconcile manually'
 save(cr/'CASE_STARTED.json',prepared,new=True);save(cr/'FRESH_REQUEST.json',load(SOURCE/'focused_research/campaigns/t032/inputs'/case/'FRESH_REQUEST.json'),new=True)
 guard,money,persist=audited_guard(case,cr,cfg);status={'case_id':case,'source_sha':IMPL,'engineering_status':'running','scientific_status':'unassessed','stages':{}}
 try:
  guard.set_stage('task_sheet')
  try:outcome=step(cr,'01_plan',lambda:runner.generate_plan(question,runner.prepare_output(cr/'plan'),planner=guard))
  finally:persist(guard.audit)
  status['stages']['plan']=outcome['status']
  if outcome['status']!='ok':raise ValueError('task-sheet contract not accepted: '+outcome['status'])
  sheet=outcome['task_sheet'];faithful(sheet,question,case);constraints,media,grid=configuration(sheet,case)
  for p,x in [('design_constraints.json',constraints),('optical_media.json',media),('wavelengths.json',grid)]:save(cr/p,x,new=True)
  save(cr/'THICKNESS_DOMAIN_POLICY.json',{'user_global_thickness_bounds':None,'route_domains':'model finite positive family hypotheses only'},new=True)
  os.environ['SEMANTIC_SCHOLAR_API_KEYS_FILE']=r'F:\OptoMind-Review-2\api_keys\semantic-scholar-api-key.txt'
  rc=step(cr,'02_retrieval',lambda:runner.main(['retrieve','--plan-file',str(cr/'plan/query_plan.json'),'--output',str(cr/'retrieval'),'--per-query-limit','10','--retrieval-seconds','60']))
  status['stages']['retrieval_exit']=rc;ep=cr/'retrieval/evidence_items.json';evidence=load(ep) if ep.exists() else {'schema_version':'focused_evidence_items_v1','items':[]};save(cr/'evidence_items.json',evidence,new=True)
  if not evidence['items']:save(cr/'EMPTY_EVIDENCE_REASON.json',{'retrieval_exit':rc,'reason':'No usable result in bounded retrieval; not proof of literature absence','model_knowledge_allowed':True},new=True)
  guard.set_stage('route_generation');route=importlib.import_module('route_stage');rr=importlib.import_module('research_routes')
  try:rc=step(cr,'03_routes',lambda:route.main(['generate','--task-sheet',str(cr/'plan/task_sheet.json'),'--evidence-file',str(cr/'evidence_items.json'),'--constraints-file',str(cr/'design_constraints.json'),'--output',str(cr/'routes')],planner=guard))
  finally:persist(guard.audit)
  status['stages']['route_exit']=rc
  if not (cr/'routes/research_routes.json').exists():raise ValueError('no accepted route contract')
  selected_evidence=load(cr/'routes/evidence_items.json');full={x['evidence_id']:x for x in evidence['items']}
  assert all(x==full[x['evidence_id']] for x in selected_evidence['items'])
  assert (cr/'routes/evidence_items.source.json').read_bytes()==(cr/'evidence_items.json').read_bytes()
  save(cr/'SELECTED_EVIDENCE_DOWNSTREAM_BINDING.json',{'source_sha':IMPL,'source_byte_sha256':sha(cr/'evidence_items.json'),'selected_evidence_sha256':digest(selected_evidence),'active_file':str(cr/'routes/evidence_items.json'),'selected_ids':[x['evidence_id'] for x in selected_evidence['items']]},new=True)
  routes=load(cr/'routes/research_routes.json');cg=importlib.import_module('focused_research.local_campaign_guard');bounds=cg.check_route_thickness_bounds(routes,case_id=case);save(cr/'THICKNESS_PREFLIGHT.json',bounds,new=True)
  idx={x['route_index'] for x in bounds['route_results'] if x['accepted']};selected=copy.deepcopy(routes);selected['routes']=[x for i,x in enumerate(routes['routes']) if i in idx and x['readiness']=='ready']
  rr.validate(selected,sheet,selected_evidence,design_constraints=constraints,candidate_mode=True);save(cr/'routes_for_search.json',selected,new=True)
  if not selected['routes']:raise ValueError('no executable original-constraint-compliant route')
  st=importlib.import_module('focused_research.search_stage')
  rc=step(cr,'04_search',lambda:st.main(['search','--task-sheet',str(cr/'plan/task_sheet.json'),'--evidence-file',str(cr/'routes/evidence_items.json'),'--constraints-file',str(cr/'design_constraints.json'),'--routes-file',str(cr/'routes_for_search.json'),'--optical-media',str(cr/'optical_media.json'),'--wavelengths',str(cr/'wavelengths.json'),'--cache-dir',str(cr/'cache'),'--max-forward-evaluations','2048','--max-new-evals','32','--max-workers','2','--backend','torch','--parameterization','family','--seed','42','--execute','--output',str(cr/'search')]))
  status['stages']['search_exit']=rc;previous=st.read_search(cr/'search')
  if not previous['archive']['designs']:raise ValueError('no certified observation; skip paid revision')
  guard.set_stage('search_revision')
  try:rc=step(cr,'05_revision',lambda:st.main(['revise','--previous-search',str(cr/'search'),'--execute','--output',str(cr/'revision')],planner=guard))
  finally:persist(guard.audit)
  status['stages']['revision_exit']=rc;plans=load(cr/'revision/refinement_plans.json')
  if plans['plans']:status['stages']['refine_exit']=step(cr,'06_refine',lambda:st.main(['refine','--previous-search',str(cr/'search'),'--revision-dir',str(cr/'revision'),'--cache-dir',str(cr/'cache'),'--max-forward-evaluations','2048','--max-new-evals','32','--max-workers','2','--backend','torch','--seed','43','--execute','--output',str(cr/'refined')]))
  else:status['stages']['refine']='empty_plan_stop'
  status['engineering_status']='completed_bounded_cycle';status['scientific_status']='inspect_actual_results_and_predictions';return 0
 except Exception as exc:
  save(cr/'FAILURE.json',{'type':type(exc).__name__,'reason':str(exc),'traceback':traceback.format_exc()},new=True)
  status.update(engineering_status='blocked',failure_type=type(exc).__name__,reason=str(exc),scientific_status='preserve_actual_results_no_acceptance_inferred');return 2
 finally:
  persist(guard.audit);save(cr/'BUDGET_STATUS.json',money.as_dict());status['budget_after']=reconcile(case);save(cr/'CASE_RESULT.json',status,new=True)
def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['preflight','case']);p.add_argument('--case',choices=[CASES[1]],required=True);p.add_argument('--execute',action='store_true');a=p.parse_args()
 if a.action=='preflight':print(json.dumps(preflight(a.case)[3],ensure_ascii=False));return 0
 if not a.execute:raise RuntimeError('explicit execution required')
 return run_case(a.case)
if __name__=='__main__':
 import multiprocessing
 multiprocessing.freeze_support();raise SystemExit(main())
