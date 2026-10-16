"""T040 local entry wiring; released production CLI, original audited transport/ledgers."""
from __future__ import annotations
import argparse,hashlib,importlib,json,multiprocessing,os,sqlite3,subprocess,sys,traceback
from pathlib import Path
from decimal import Decimal
R=Path(__file__).resolve().parent;T=R.parent;SOURCE=Path(r'D:\OptoMind-Article-3\r3t040a')
IMPL='0437ba26003d7920c6d37b3d9437351eecf42392';CASE='e2e-04-solarblind-uv-triple'
sys.dont_write_bytecode=True;sys.path.insert(0,str(T/'R3-T038'));import run_t038 as legacy
reused=legacy.reused;reused.SOURCE=SOURCE;reused.IMPL=IMPL
save,load,sha,digest,module=reused.save,reused.load,reused.sha,reused.digest,reused.module
sys.path.insert(0,str(R/'local_tools'));AGG=module(R/'local_tools/aggregate_budget.py','t040_aggregate')
OLD,POOL,D20,MAX_TOKENS=legacy.OLD,legacy.POOL,legacy.D20,legacy.MAX_TOKENS
CR=R/'cases'/CASE;OLDCASE=T/'R3-T038/cases'/CASE;PREVIOUS=OLDCASE/'refined';CACHE=OLDCASE/'cache'
REPLAY=T/'R3-T039/replay-input';FACTS=Path(r'D:\OptoMind-Article-3\r3t039a\handoff\R3-T039-OFFLINE-20261016')
MATERIAL_SHA='d4852aaa920125d7f217a3b60f291f48ab05d67f79ae65c5231f1477c0a4c2ae'
RTA_SHA='9506bb389742590a8249b60e97a0a0131403d9bc636d43f412b7dce48e119842'

def reconcile(case=CASE):
 with sqlite3.connect(OLD.as_uri()+'?mode=ro',uri=True) as a,sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as b:
  a.row_factory=b.row_factory=sqlite3.Row
  result=AGG.checked_totals(a,b,Decimal(0),load(R/'CASE_HISTORY_FOR_GUARD.json'),'R3-T040:'+case,admission=False)
  links=b.execute('SELECT task_id,reservation_id FROM r3_task_calls').fetchall()
  result['case_actual_HTTP']=sum(x['task_id'] in AGG.TASK_IDS and AGG.TASK_IDS[x['task_id']]==case for x in links)
  result['task_actual_HTTP']=sum(x['task_id']=='R3-T040:'+case for x in links)
 result.update(read_only=True,both_ledgers_locked_across_check_and_reservation=False)
 return result

def verify_recovery(case=CASE):return legacy.verify_recovery(case)

def preflight():
 _,origins=reused.bind();st=importlib.import_module('focused_research.search_stage')
 state=load(R.parents[1]/'state.json');assert state['processed']['R3-T040::C2L-R3-0055']['claim_readback_verified'] is True
 accepted=load(R/'preflight/T040_LOCAL_ACCEPTANCE.json');assert accepted['global_pass'] is True
 for item in load(R/'OFFLINE_VALIDATION.json')['implementation_hashes']:assert sha(SOURCE/item['path'])==item['sha256']
 for name,h in load(R/'INPUT_PROVENANCE.json')['T038_saved_route_inputs'].items():assert sha(REPLAY/name)==h
 assert sha(FACTS/'FIVE_MATERIAL_UV.json')==MATERIAL_SHA and sha(FACTS/'CACHED_UV_RTA.json')==RTA_SHA
 assert (R/'local_tools/task_budget_guard_220.py').read_bytes()==(T/'R3-T030/local_tools/task_budget_guard_220.py').read_bytes()
 sheet,binding=verify_recovery();assert sheet==load(REPLAY/'task_sheet.json')
 assert hashlib.sha256(sheet['original_question'].encode()).hexdigest()=='a072e2f7732cc73738382bb04f916bcd0a1f4814c526cb9c022290cd6bd73d12'
 old=st.read_search(PREVIOUS);assert len(old['archive']['designs'])==51
 assert len(old['context']['evidence']['items'])==46
 mat=load(FACTS/'FIVE_MATERIAL_UV.json')
 for sample in mat['samples'].values():
  b=sample['source_binding'];assert sha(Path(b['source_path']))==b['source_sha256']
 for sample in load(FACTS/'CACHED_UV_RTA.json')['samples']:assert sha(Path(sample['source_cache_path']))==sample['source_cache_sha256']
 assert sha(SOURCE/'veritmm/tmm_engine/rii_cache.db')==sha(Path(r'D:\OptoMind-Article-3\r3t038a\veritmm\tmm_engine\rii_cache.db'))
 cfg=module(T/'R3-T030/run_t030.py','t040_original_billing').original_billing()
 budget=reconcile();assert budget['case_actual_HTTP'] in (4,5,6)
 return st,cfg,{'source_sha':IMPL,'origins':origins,'interpreter':sys.executable,'budget':budget,
               'original_recovery_manifest':binding,'old_manifest':old['manifest_sha256'],'launcher_sha256':sha(__file__)}

exec(compile((R/'audited_guard_fragment.py').read_text(encoding='utf-8'),str(R/'audited_guard_fragment.py'),'exec'))

def stage(name,args):
 save(CR/'commands'/f'{name}.json',{'argv':[sys.executable,'-B','-X','utf8','-m','focused_research.search_stage']+args,
      'actual_entry':'focused_research.search_stage.main; planner injected only for revise','source_sha':IMPL,'cwd':str(SOURCE)},new=True)
 st=importlib.import_module('focused_research.search_stage')
 return reused.step(CR,name,lambda:st.main(args))

def numerical_state(st,path,rows,certs,name):
 current=st.read_search(path);summary=load(path/'summary.json');m=summary['metering']
 charged=m['search_forward_design_rows']+m.get('search_failed_job_reserved_rows',0)
 assert charged<=rows and m['certification_new_evaluations']<=certs
 old=st.read_search(PREVIOUS)
 for did,design in old['archive']['designs'].items():
  assert did in current['archive']['designs'],'historical stable design dropped'
  now=current['archive']['designs'][did]
  for k in ['physical_input','metrics','parent_design_id']:assert now[k]==design[k],'historical design changed: '+did+'/'+k
 assert current['context']['sheet']==old['context']['sheet']
 assert current['context']['evidence']==old['context']['evidence']
 assert current['context']['constraints']==old['context']['constraints']
 assert current['context']['wavelengths_nm']==old['context']['wavelengths_nm']
 assert current['context']['optical_media']==old['context']['optical_media']
 report={'stage':name,'manifest_sha256':current['manifest_sha256'],'rows_charged':charged,
  'new_certifications':m['certification_new_evaluations'],'historical_designs_preserved':51,
  'archive_count':len(current['archive']['designs']),'new_stable_design_ids':sorted(set(current['archive']['designs'])-set(old['archive']['designs'])),
  'metering':m,'budget':reconcile(),'summary_status':summary['status']}
 save(CR/f'{name}_AUDIT.json',report,new=True)
 return current,report

def initial():
 st,cfg,p=preflight();assert p['budget']['case_actual_HTTP']==4 and p['budget']['task_actual_HTTP']==0
 assert not (CR/'CASE_STARTED.json').exists(),'no automatic rerun of numerical work'
 save(CR/'CASE_STARTED.json',p,new=True)
 save(CR/'NUMERICAL_PHASE_RESERVATIONS.json',{'total_rows':4096,'total_certifications':128,'no_implicit_transfer':True,
    'phases':{'initial':[2048,64],'refinement1':[1024,32],'optional_refinement2':[1024,32]}},new=True)
 out=CR/'initial_search';args=['search','--previous-search',str(PREVIOUS),
 '--routes-file',str(REPLAY/'research_routes.raw.json'),'--task-sheet',str(REPLAY/'task_sheet.json'),
 '--evidence-file',str(REPLAY/'evidence_items.json'),'--constraints-file',str(REPLAY/'design_constraints.json'),
 '--optical-media',str(PREVIOUS/'optical_media.json'),'--wavelengths',str(PREVIOUS/'wavelengths_nm.json'),
 '--cache-dir',str(CACHE),'--output',str(out),'--max-forward-evaluations','2048','--max-new-evals','64',
 '--max-workers','2','--seed','42','--backend','auto','--execute']
 rc=stage('01_initial',args)
 if not (out/'manifest.json').exists():raise RuntimeError('initial search failed before sealed snapshot')
 current,audit=numerical_state(st,out,2048,64,'initial')
 continuation=load(out/'continuation_audit.json');assert len(continuation['retained_seed_references'])==2
 assert all(x['route_id']=='R1' and not x['cache_hit_claimed'] and x['new_evaluations']==0 for x in continuation['retained_seed_references'])
 assert 'R1' not in continuation['new_search_route_ids']
 save(CR/'INITIAL_RESULT.json',{'CLI_exit':rc,'audit':audit,'continuation':continuation,'paid_HTTP':0,
       'stop_before_parent_review':True,'valid_new_feedback':bool(audit['new_stable_design_ids'])},new=True)
 return 0

def revisions():
 st,cfg,p=preflight();assert p['budget']['case_actual_HTTP']==4 and p['budget']['task_actual_HTTP']==0
 assert not (CR/'REVISIONS_STARTED.json').exists(),'guard may not be recreated after failure'
 first=load(CR/'INITIAL_RESULT.json');assert first['valid_new_feedback'] is True
 assert load(CR/'PARENT_INITIAL_ADMISSION.json')['revision_allowed'] is True
 save(CR/'REVISIONS_STARTED.json',p,new=True)
 guard,money,persist=audited_guard(CASE,CR,cfg);guard.set_stage('search_revision')
 status={'case_id':CASE,'source_sha':IMPL,'engineering_status':'running','scientific_status':'unassessed','stages':{},'new_HTTP_max':2}
 previous=CR/'initial_search';numeric=[load(CR/'initial_AUDIT.json')]
 try:
  for i in [1,2]:
   current=st.read_search(previous)
   if i==2:
    prior=st.read_search(CR/'initial_search')
    new_ids=set(current['archive']['designs'])-set(prior['archive']['designs'])
    if not new_ids:status['stop_reason']='no genuine new measured physical feedback after refinement1';break
    save(CR/'SECOND_REVISION_NEW_FEEDBACK.json',{'new_stable_design_ids':sorted(new_ids),
        'old_manifest':prior['manifest_sha256'],'current_manifest':current['manifest_sha256']},new=True)
   b=reconcile();assert b['case_actual_HTTP']==4+(i-1) and b['task_actual_HTTP']==i-1
   assert sum(x['rows_charged'] for x in numeric)<=4096 and sum(x['new_certifications'] for x in numeric)<=128
   rev=CR/f'revision{i}';args=['revise','--previous-search',str(previous),
    '--material-feedback',str(FACTS/'FIVE_MATERIAL_UV.json'),'--cached-rta-feedback',str(FACTS/'CACHED_UV_RTA.json'),
    '--material-feedback-sha256',MATERIAL_SHA,'--cached-rta-feedback-sha256',RTA_SHA,'--output',str(rev),'--execute']
   save(CR/'commands'/f'0{i*2}_revision{i}.json',{'argv':[sys.executable,'-B','-X','utf8','-m','focused_research.search_stage']+args,
       'actual_entry':'search_stage.main(args,planner=same_RecoveredRevisionPlannerGuard)','source_sha':IMPL},new=True)
   try:rc=reused.step(CR,f'0{i*2}_revision{i}',lambda:st.main(args,planner=guard))
   finally:persist(guard.audit)
   status['stages'][f'revision{i}_exit']=rc
   summary=load(rev/'summary.json');status['stages'][f'revision{i}_status']=summary['status']
   if summary['error'] is not None or not (rev/'refinement_plans.json').exists():
    status['engineering_status']='blocked';status['stop_reason']=summary['error'] or 'no valid plans';break
   plans=load(rev/'refinement_plans.json')
   if not plans['plans']:status['stop_reason']='valid empty plan';break
   out=CR/f'refined{i}';args=['refine','--previous-search',str(previous),'--revision-dir',str(rev),
    '--cache-dir',str(CACHE),'--output',str(out),'--max-forward-evaluations','1024','--max-new-evals','32',
    '--max-workers','2','--seed','42','--backend','auto','--execute']
   rc=stage(f'0{i*2+1}_refine{i}',args);status['stages'][f'refine{i}_exit']=rc
   if not (out/'manifest.json').exists():raise RuntimeError('refinement failed before sealed snapshot')
   current,audit=numerical_state(st,out,1024,32,f'refinement{i}');numeric.append(audit)
   assert sum(x['rows_charged'] for x in numeric)<=4096 and sum(x['new_certifications'] for x in numeric)<=128
   previous=out
  if status['engineering_status']=='running':status['engineering_status']='completed_bounded_continuation'
  status['scientific_status']='inspect actual certified tradeoffs and model reasoning separately'
  return 0 if status['engineering_status']!='blocked' else 2
 except Exception as exc:
  save(CR/'REVISION_FAILURE.json',{'type':type(exc).__name__,'reason':str(exc),'traceback':traceback.format_exc()},new=True)
  status.update(engineering_status='blocked',stop_reason=str(exc));return 2
 finally:
  persist(guard.audit);save(CR/'BUDGET_STATUS.json',money.as_dict())
  try:status['budget_after']=reconcile()
  except Exception as exc:status['budget_reconciliation_error']=str(exc);status['engineering_status']='blocked'
  status['numerical_stages']=numeric;status['final_snapshot']=str(previous)
  save(CR/'CASE_RESULT.json',status,new=True)

def main():
 p=argparse.ArgumentParser();p.add_argument('action',choices=['preflight','initial','revisions']);p.add_argument('--execute',action='store_true');a=p.parse_args()
 if a.action=='preflight':print(json.dumps(preflight()[2],ensure_ascii=False));return 0
 if not a.execute:raise RuntimeError('explicit execution required')
 try:return initial() if a.action=='initial' else revisions()
 except Exception as exc:
  save(CR/f'{a.action.upper()}_FAILURE.json',{'type':type(exc).__name__,'reason':str(exc),'traceback':traceback.format_exc()},new=True)
  traceback.print_exc();return 2
if __name__=='__main__':
 multiprocessing.freeze_support();raise SystemExit(main())
