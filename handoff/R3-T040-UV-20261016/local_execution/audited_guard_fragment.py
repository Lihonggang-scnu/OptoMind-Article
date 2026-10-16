def audited_guard(case,cr,cfg):
 am=module(D20/'transport/dedicated_qwen_adapter.py','t040_original_qwen_adapter');native=am.load_native_runtime(Path(cfg['paths']['review_runtime']))
 entry,base=am._load_budget_factory(reused.TOOLS/'budgeted_entry.py',R/'local_tools/task_budget_guard_220.py')
 cls=AGG.guard_class(base.TaskBudgetGuard,OLD,load(R/'CASE_HISTORY_FOR_GUARD.json'),cr/'atomic_reservations')
 money=cls(native.GlobalBudgetLedger(limit_cny=220,path=POOL),task_id='R3-T040:'+case,lane='recovered_continuation',task_limit_cny=8,max_calls=2,global_limit_cny=220)
 config=am.Dedicated20Config(ledger_path=POOL,audit_root=cr/'private/audit',key_file=Path(cfg['paths']['key_file']),task_id='R3-T040:'+case,model='qwen3.5-plus',repair_model='qwen3.7-flash',task_limit_cny=8,global_limit_cny=220,max_calls=2,max_retries=0,max_keys=1,max_tokens=MAX_TOKENS)
 pm=importlib.import_module('optomind_research.query_planner');resolver,_=am._load_vendored_model_resolver(Path(pm.__file__))
 adapter=am.FocusedQwenCallAdapter(audited_client_factory=entry.make_audited_client(native,money,config.factory_config()),runtime=native,config=config,guard=money,model_resolver=resolver)
 calls=0;admitted={}
 def protected(agent,messages,**kw):
  nonlocal calls
  if 'guard' not in admitted or admitted['guard'].audit['dispatch_attempts']!=calls+1:raise RuntimeError('no live campaign guard admission')
  if calls>=2:raise RuntimeError('T040 two revision HTTP ceiling')
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
 cg=importlib.import_module('focused_research.local_campaign_guard');guard=cg.RecoveredRevisionPlannerGuard(planner,case_id=case,audit_sink=persist,recovered_task_sheet_binding=verify_recovery(case)[1],continuation_task_id='R3-T040',prior_dispatch_attempts=4,cumulative_dispatch_limit=6);admitted['guard']=guard
 return guard,money,persist
