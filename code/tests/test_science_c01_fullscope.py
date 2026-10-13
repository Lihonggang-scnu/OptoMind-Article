import hashlib
import json
from pathlib import Path
import pytest
from optomind_optics.harness.science.contracts import ResearchTask, Observation, ModelCandidate, ExperimentProgram, canonical_hash
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.migration import migrate_observation_v1, legacy_observation_projection
from optomind_optics.harness.science.trajectory_migration import migrate_trajectory, plan_trajectory_migration, rebuild_historical_files

SOURCE=Path(__file__).resolve().parents[2]/'accepted_examples/research_broadband_ar_source'

@pytest.mark.parametrize('factory,fields',[
 (ResearchTask,dict(question='?',objective={'target_R':True})),
 (ResearchTask,dict(question='?',constraints={'silent_science':3})),
 (ModelCandidate,dict(model_ir={'nodes':[],'output':'x','unvalidated':1})),
 (ExperimentProgram,dict(protocol={'program_id':'p','nodes':[{'node_id':'x','kind':'computation','silent_science':3}]})),
 (Observation,dict(acquisition_id='a',raw_blocks=({'block_id':'r','values':[1], 'wavelengths_nm':[1,2]},))),
 (Observation,dict(acquisition_id='a',quality={'noise_validated':'false'})),
])
def test_strict_nested_consumed_contract(factory,fields):
 with pytest.raises(ValueError):factory(object_id='x',source_run_id='r',**fields)

def test_actual_likelihood_consumer_revalidates_mutation():
 from optomind_optics.harness.science.likelihood_blocks import blocks_from_observations
 obs=Observation(object_id='o',source_run_id='r',acquisition_id='a',raw_blocks=({'block_id':'r','values':[1.],'wavelengths_nm':[500.],'covariance':[[.01]]},))
 obs.raw_blocks[0]['unvalidated_science']=1
 with pytest.raises(ValueError):blocks_from_observations([obs])

def test_enhanced_explicit_observation_and_projection(tmp_path):
 old={'observation_id':'o','binding':{'task_id':'t'},'raw_blocks':[{'block_id':'r','values':[1.]}],'quality':{'numerical_valid':True},'derived_features':{'gradient_summary':{'signed':-3}},'legacy_belief':.9}
 obs,report=migrate_observation_v1(old,source_run_id='r')
 assert obs.raw_blocks and obs.binding==old['binding'] and obs.derived_features['gradient_summary']==old['derived_features']['gradient_summary']
 task=ResearchTask(object_id='t',source_run_id='r',question='?')
 repo=ScienceRepository(tmp_path)
 try:
  repo.commit([task,obs],task_ref=task.ref(),evidence_cutoff='historical')
  assert legacy_observation_projection(repo,obs.ref())==old
 finally:repo.close()

def test_whole_real_trajectory_rebuilds_actual_reflection_and_original_bytes(tmp_path):
 from optomind_optics.harness.route_reflection import _build_reflection_payload
 before={p.relative_to(SOURCE).as_posix():p.read_bytes() for p in SOURCE.rglob('*.json')}
 repo=ScienceRepository(tmp_path)
 try:
  snap,reports=migrate_trajectory(SOURCE,repo)
  objects=[repo.load(ref) for ref in snap.object_refs]
  kinds={o.schema_version for o in objects}
  assert {'research-task.v1','model-candidate.v1','experiment-program.v1','observation.v2','inference-state.v1','decision-context.v1'}<=kinds
  result=next(o for o in objects if o.object_id.endswith(':result'))
  assert rebuild_historical_files(repo,result.ref())==before
  for obs in [o for o in objects if isinstance(o,Observation)]:
   projection=legacy_observation_projection(repo,obs.ref())
   reflected=_build_reflection_payload({},projection,[],1e-4)
   assert reflected['scientific_evidence']['candidate_summaries']==projection['candidate_summaries']
   assert not obs.raw_blocks and 'raw_blocks' in obs.not_recorded_fields
  assert all(o.representation=='not_recorded' for o in objects if o.schema_version=='inference-state.v1')
  assert 'canonical_selected_winner' in reports[result.object_id]['not_recorded_fields']
  assert migrate_trajectory(SOURCE,repo)[0]==snap
 finally:repo.close()
 assert before=={p.relative_to(SOURCE).as_posix():p.read_bytes() for p in SOURCE.rglob('*.json')}

@pytest.mark.parametrize('stage',['before_artifacts','after_artifacts','before_commit','after_commit'])
def test_whole_migration_crash_recovery(tmp_path,stage):
 def fault(point):
  if point==stage:raise RuntimeError('interrupted')
 repo=ScienceRepository(tmp_path,fault_hook=fault)
 try:
  with pytest.raises(RuntimeError):migrate_trajectory(SOURCE,repo)
 finally:repo.close()
 repo=ScienceRepository(tmp_path)
 try:
  snap,_=migrate_trajectory(SOURCE,repo)
  assert snap.committed_sequence==1
  assert len(snap.object_refs)==len(plan_trajectory_migration(SOURCE)[0])
 finally:repo.close()

def test_nested_refs_and_snapshot_integrity(tmp_path):
 task=ResearchTask(object_id='t',source_run_id='r',question='?')
 missing=ResearchTask(object_id='missing',source_run_id='r',question='?')
 obs=Observation(object_id='o',source_run_id='r',acquisition_id='a',binding={'program_ref':missing.ref().model_dump(mode='json')})
 repo=ScienceRepository(tmp_path)
 try:
  with pytest.raises(ValueError):repo.commit([task,obs],task_ref=task.ref(),evidence_cutoff='x')
  snap=repo.commit([task],task_ref=task.ref(),evidence_cutoff='x')
  with pytest.raises(ValueError):type(snap).model_validate({**snap.model_dump(mode='json'),'evidence_cutoff':'tampered'})
 finally:repo.close()

def test_article_execution_return_reads_committed_authority(tmp_path,monkeypatch):
 from test_article_execution import _request,_stack,_task,FakeHarness,_run_result
 from optomind_optics.harness.article_contracts import ObservationCard
 from optomind_optics.harness.science import migration
 request=_request();stack=_stack(tmp_path,harness=FakeHarness(_run_result()))
 stack['registry'].register(request.task_hash,_task())
 repo=ScienceRepository(tmp_path/'science')
 task=ResearchTask(object_id='t',source_run_id='r',question='?')
 repo.commit([task],task_ref=task.ref(),evidence_cutoff='before')
 coordinator=stack['coordinator'];coordinator.science_repository=repo;coordinator.science_task_ref=task.ref()
 original=migration.legacy_observation_projection
 reads=[]
 def projection(repository,ref):
  reads.append(ref)
  payload=original(repository,ref)
  assert repository.object_commit_sequence(ref)==2
  return payload
 monkeypatch.setattr(migration,'legacy_observation_projection',projection)
 try:
  result=coordinator.execute(request)
  assert len(reads)==1
  assert result.observation==ObservationCard.model_validate(original(repo,reads[0]))
 finally:repo.close()

def test_live_legacy_reflection_uses_committed_observation_and_messages(tmp_path,monkeypatch):
 from test_tmm_research_orchestrator import (_Analyzer,_Researcher,_Planner,_route,_Compiler,_Harness,_run_payload,
      TMMResearchHarness,TMMResearchHarnessConfig)
 from optomind_optics.harness import research_orchestrator as module
 observed=[];original=module.reflect_on_route
 def reflect(*args,**kwargs):
  repo=ScienceRepository(tmp_path/'run/science',read_only=True)
  try:
   snapshots=repo.latest_snapshot()
   canonical=[repo.load(r) for r in snapshots.object_refs if isinstance(repo.load(r),Observation)]
   assert any(legacy_observation_projection(repo,o.ref())==kwargs['observation'] for o in canonical)
   observed.append(kwargs['observation'])
  finally:repo.close()
  return original(*args,**kwargs)
 monkeypatch.setattr(module,'reflect_on_route',reflect)
 result=TMMResearchHarness(tmp_path/'run',problem_analyzer=_Analyzer(),method_researcher=_Researcher(),
  strategy_planner=_Planner([[_route('r1','request one')]]),task_compiler=_Compiler(),
  tmm_harness_factory=lambda path,run_id:_Harness(_run_payload(True,.7)),
  config=TMMResearchHarnessConfig(maximum_refinement_rounds=0,max_rounds_per_route=1,
   scoring_standard_enabled=False,route_planning_enabled=False,qwen_force_mock=True,confirmation_gates=[],online_method_research=False)).run('Design a reflector')
 assert result.status=='completed' and observed
 repo=ScienceRepository(tmp_path/'run/science',read_only=True)
 try:
  contexts=[repo.load(r) for r in repo.latest_snapshot().object_refs if repo.load(r).schema_version=='decision-context.v1']
  assert contexts and contexts[0].context_hash==canonical_hash(contexts[0].actual_messages)
  saved=json.loads(next((tmp_path/'run/iterations').glob('*/DECISION_CONTEXT.json')).read_text())
  assert saved['actual_messages']==list(contexts[0].actual_messages)
 finally:repo.close()

def test_qualified_external_reference_is_explicit_and_shape_checked(tmp_path):
 from optomind_optics.harness.science.contracts import DecisionContext
 task=ResearchTask(object_id='t',source_run_id='r',question='?')
 external=ResearchTask(object_id='asset',source_run_id='library',question='external')
 envelope={'external_scientific_ref':{'repository_id':'library:bound','snapshot_id':'a'*64,'object_ref':external.ref().model_dump(mode='json')}}
 context=DecisionContext(object_id='context',source_run_id='r',summary={'source':envelope})
 repo=ScienceRepository(tmp_path)
 try:
  repo.commit([task,context],task_ref=task.ref(),evidence_cutoff='x')
  assert repo.load(context.ref()).summary['source']==envelope
  malformed=DecisionContext(object_id='bad',source_run_id='r',summary={'external_scientific_ref':{'object_ref':external.ref().model_dump(mode='json')}})
  with pytest.raises(ValueError):repo.commit([malformed],task_ref=task.ref(),evidence_cutoff='x')
 finally:repo.close()

def test_json_schema_exposes_strict_nested_consumer_fields():
 for cls,field in [(ResearchTask,'objective'),(Observation,'binding'),(Observation,'quality')]:
  assert cls.model_json_schema()['properties'][field]['additionalProperties'] is False
 for cls,field in [(ModelCandidate,'model_ir'),(ExperimentProgram,'protocol')]:
  assert all(variant['additionalProperties'] is False for variant in cls.model_json_schema()['properties'][field]['anyOf'])

def test_snapshot_v2_binds_task_and_reads_explicit_legacy_v1(tmp_path):
 from optomind_optics.harness.science.contracts import ScienceSnapshot
 a=ResearchTask(object_id='a',source_run_id='r',question='A?')
 b=ResearchTask(object_id='b',source_run_id='r',question='B?')
 repo=ScienceRepository(tmp_path)
 try:
  snapshot=repo.commit([a,b],task_ref=a.ref(),evidence_cutoff='x')
  assert snapshot.schema_version=='science-snapshot.v2' and snapshot.task_identity_bound
  changed={**snapshot.model_dump(mode='json'),'task_ref':b.ref().model_dump(mode='json')}
  with pytest.raises(ValueError,match='snapshot identity mismatch'):ScienceSnapshot.model_validate(changed)
  legacy={**snapshot.model_dump(mode='json'),'schema_version':'science-snapshot.v1'}
  legacy['snapshot_id']=canonical_hash({'sequence':snapshot.committed_sequence,'refs':[ref.model_dump() for ref in snapshot.object_refs],'cutoff':snapshot.evidence_cutoff})
  historical=ScienceSnapshot.model_validate(legacy)
  assert not historical.task_identity_bound
  assert ScienceSnapshot.model_validate_json(historical.model_dump_json())==historical
 finally:repo.close()

def test_direct_candidate_compilation_revalidates_content_hash():
 from dataclasses import asdict
 from optomind_optics.harness.science.open_research_loop import initial_planar_model
 from optomind_optics.harness.science.model_compiler import compile_candidate
 model=ModelCandidate(object_id='m',source_run_id='r',model_ir=asdict(initial_planar_model()))
 model.parameter_values['film.n']=2.1
 with pytest.raises(ValueError,match='content_hash mismatch'):compile_candidate(model)
