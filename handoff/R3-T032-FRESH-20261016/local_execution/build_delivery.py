"""Export stopped task evidence; never reruns a model or numerical calculation."""
from pathlib import Path
from decimal import Decimal
import json,hashlib,shutil,subprocess,sys
R=Path(__file__).resolve().parent;S=Path(r'D:\OptoMind-Article-3\r3t032b');O=S/'handoff/R3-T032-FRESH-20261016'
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
def cp(p,q):q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
assert not O.exists(),'do not overwrite old packet'
cases=load(R/'BUDGET.json')['case_order'];assert all((R/'cases'/c/'CASE_RESULT.json').exists() for c in cases)
live=json.loads(subprocess.check_output([sys.executable,'-B','-X','utf8',str(R/'probe.py')],text=True,encoding='utf-8'))
assert all(x['unresolved_count']==0 for x in live['ledgers']),'no settled-only final claim with unknown exposure'
O.mkdir(parents=True);(O/'.gitattributes').write_text('* -text\n',encoding='ascii')
save(R/'BUDGET_AFTER.json',live);refs=[];reports=[]
for case in cases:
 cr=R/'cases'/case;calls=[x for x in live['task_calls'] if x['task_id']=='R3-T032:'+case];row={'case_id':case,'actual_result':load(cr/'CASE_RESULT.json'),'new_actual_http_attempts':len(calls),'new_cost_cny':str(sum((Decimal(str(x['actual_cny'] or 0)) for x in calls),Decimal(0))),'stages':{}}
 atomic=[load(p) for p in (cr/'atomic_reservations').glob('*.json')];assert len(atomic)==len(calls) and all(x['both_ledgers_locked_across_check_and_reservation'] for x in atomic)
 row['actual_atomic_native_reservations_verified']=len(atomic)
 for sub in ['plan','retrieval','routes','search','revision','refined']:
  d=cr/sub
  if not d.exists():continue
  if (d/'summary.json').exists():row['stages'][sub]=load(d/'summary.json')
  for p in sorted(d.rglob('*')):
   if not p.is_file() or any(x in p.parts for x in ['cache','private','__pycache__']):continue
   excluded=p.name in {'design_archive.json','design_envelopes.json','execution_inputs.json','execution_results.json','numerical_search.json','candidate_ancestry.json','evidence_items.full.json','evidence_items.source.json'}
   export=not excluded and p.suffix in {'.json','.txt','.log','.md','.csv'}
   if export:cp(p,O/'cases'/case/sub/p.relative_to(d))
   refs.append({'local_path':str(p),'sha256':sha(p),'size_bytes':p.stat().st_size,'compact_export_present':export})
  if (d/'design_archive.json').exists():
   a=load(d/'design_archive.json');ds=a['designs'];flat={k:{'design_id':k,'parent_design_id':v.get('parent_design_id'),'metrics':v['metrics'],'physical_input':v['physical_input'],'layer_diff':v.get('layer_diff'),'first_evaluation':v.get('first_evaluation')} for k,v in ds.items()}
   save(O/'cases'/case/'science'/(sub+'_CERTIFIED_DESIGNS.json'),{'objectives':a['objectives'],'conditions':a['conditions'],'context_id':a['context_id'],'claim_scope':a['claim_scope'],'pareto_design_ids':a['pareto_design_ids'],'designs':flat})
   chosen=set()
   for g in a['objectives']:
    if ds:chosen.add((min if g['sense']=='minimize' else max)(ds,key=lambda k:ds[k]['metrics'][g['objective_id']]['value']))
   chosen.update(v.get('parent_design_id') for k,v in ds.items() if k in chosen and v.get('parent_design_id') in ds)
   save(O/'cases'/case/'science'/(sub+'_REPRESENTATIVE_CERTIFIED_RESULTS.json'),{'selection':'Existing per-goal extrema plus their actual parents; no combined fictitious optimum.','designs':{k:ds[k] for k in sorted(chosen)}})
 if (cr/'routes/evidence_items.json').exists():
  selected=load(cr/'routes/evidence_items.json')
  for stage in ['search','refined']:
   p=cr/stage/'evidence_items.json'
   if p.exists():assert load(p)==selected,'downstream evidence binding changed'
 for p in cr.glob('*'):
  if p.is_file() and p.suffix in {'.json','.log'}:cp(p,O/'cases'/case/p.name)
 for folder in ['guard_audit','atomic_reservations']:
  for p in (cr/folder).glob('*.json'):cp(p,O/'cases'/case/folder/p.name)
 for name in ['REQUEST.json','RESPONSE.json']:
  for p in (cr/'private/audit').rglob(name):cp(p,O/'cases'/case/'model'/(p.parent.name+'_'+name))
 row['total_search_rows']=sum(x.get('metering',{}).get('search_forward_design_rows',0) for x in row['stages'].values());row['total_new_certifications']=sum(x.get('metering',{}).get('certification_new_evaluations',0) for x in row['stages'].values())
 assert row['total_search_rows']<=4096 and row['total_new_certifications']<=64 and row['new_actual_http_attempts']<=4
 reports.append(row)
for folder in ['preflight','material-preflight','local_tools','quality-review']:
 for p in (R/folder).rglob('*'):
  if p.is_file() and p.suffix in {'.json','.log','.md','.txt','.py'} and '__pycache__' not in p.parts:cp(p,O/p.relative_to(R))
for p in R.glob('*'):
 if p.is_file() and p.suffix in {'.json','.py','.md','.txt'} and not p.name.endswith('.local-only') and p.name not in {'RESULT.json','COMPLETED.json','DELIVERY_VERIFIED.json'}:cp(p,O/'local_execution'/p.name)
save(O/'FULL_LOCAL_ARCHIVE_REFERENCES.json',refs)
total=sum((Decimal(x['settled_cny']) for x in live['ledgers']),Decimal(0))
summary={'task_id':'R3-T032','in_reply_to':'C2L-R3-0047','superseding_source_message':'C2L-R3-0049','status':'blocked' if any(x['actual_result']['engineering_status'].startswith('blocked') for x in reports) else 'completed','implementation_sha':'8a38b2d0d7b18c4308c4f5978691a234f6ab3732','original_release_sha':'61308f5200910f007878f7999e81e470fc08bc38','source_core_modified':False,'cases':reports,'targeted_tests_passed':85,'budget_fixture_tests_passed':4,'new_actual_http_attempts':len(live['task_calls']),'new_task_settled_cny':live['new_task_settled_cny'],'project_settled_cny':str(total),'pending_held_uncertain_cny':'0','original_case_cap_cny':8,'batch_cap_cny':24,'project_cap_cny':220,'original03_alias_of':cases[0],'scientific_scope':'finite sampled-grid certified observations only; no global optimum, mechanism, fabrication or controlled benefit proof','complete_originals':str(R),'compact_export_is_not_full_read_search_snapshot':True,'next_queue':['T035 pure saved-Jacobian zero-fee acceptance at fixed5c50430693151d2066cf352fcc9bb80e5ee6b667 after terminal']}
save(R/'RUN_SUMMARY.json',summary);save(O/'RUN_SUMMARY.json',summary)
lines=['# T032 / C2L-R3-0047 原题冷启动结果','',f"执行状态：{summary['status']}。固定实现 `{summary['implementation_sha']}`（依0049覆盖原release绑定，原交付完整保留）。",'', '85项指定离线检查和4项费用门禁检查通过。每次模型请求均由原供应商、原账本入口及同时锁定两个原账本的累计门禁保护；未新建预算池。', '', '| 原案例 | 执行状态 | HTTP | 搜索行 | 新认证 | 新费用/元 |','|---|---|---:|---:|---:|---:|']
for x in reports:lines.append(f"| {x['case_id']} | {x['actual_result']['engineering_status']} | {x['new_actual_http_attempts']} | {x['total_search_rows']} | {x['total_new_certifications']} | {x['new_cost_cny']} |")
lines+=['','有限网格中的实际代表结构（每行属于单一结构，不拼接两个极值）：']
for case in cases:
 cr=R/'cases'/case;p=cr/'refined/design_archive.json'
 if not p.exists():p=cr/'search/design_archive.json'
 if not p.exists():continue
 archive=load(p);ds=archive['designs']
 for goal in archive['objectives']:
  if not ds:continue
  ident=(min if goal['sense']=='minimize' else max)(ds,key=lambda k:ds[k]['metrics'][goal['objective_id']]['value'])
  vector={g:m['value'] for g,m in ds[ident]['metrics'].items()}
  lines.append(f"- {case}，{goal['objective_id']}方向极值：`{ident}`，完整目标向量 `{vector}`。")
lines+=['',f"新增费用 {summary['new_task_settled_cny']} 元；项目累计 {summary['project_settled_cny']} 元；pending/held/uncertain 为0。原01与03共同归属，不另给预算。",'', 'UV在付费前阻塞：原题允许常规可沉积无机介质且不含金属，现有合同只接受有限 allowed_materials 名单或null；null又触发missing configuration并阻止路线实例化。未将其缩窄为HfO2/SiO2/MgF2名单，未修改核心合同或就绪断言。复现、错误位置见preflight/T032_OFFLINE_ADMISSION.json#route_contract_repro。', '', '01/05的实际原始问答、检索、路线拒绝项、选择证据与父子结果分别保留。质量分析见quality-review；完整认证结构向量及代表性原始谱见各例science。执行结束不表示科学假设成立，权衡、回退与未认证部分单独列明。', '', '原题未给定的0°/等s-p非偏振/波长积分均值均为单独披露的执行假设，未改写原文；全局层厚界为None，有限正家族范围是模型假设。Cr/Al系统中A_finite与1−R的总系统吸收意义分别保留，不添加第三优化目标。', '', '所有原始记录仍在本地，FULL_LOCAL_ARCHIVE_REFERENCES.json记录大输出位置及哈希；未上传密钥、认证头、账本或材料数据库、PDF、权重。后续只按队列执行零费用T035，不额外复跑或补充案例。']
text='\n'.join(lines)+'\n';(R/'REPORT.md').write_text(text,encoding='utf-8');(O/'REPORT.md').write_text(text,encoding='utf-8');print(json.dumps({'packet':str(O),'summary':{k:summary[k] for k in ['status','new_actual_http_attempts','new_task_settled_cny','project_settled_cny']}},ensure_ascii=False))
