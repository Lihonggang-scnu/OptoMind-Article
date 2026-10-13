"""Genuine offline production-loop factorial evaluator, independent Airy scoring.

No live models and no fabricated M/E benefit. Native feature gates are required.
"""
from dataclasses import asdict, replace
import hashlib
import inspect
import json
from pathlib import Path
import time
import traceback
import numpy as np
from .factorial_protocol import (FACTORIAL_ARMS,FACTOR_FLAGS,BaselineParityManifest,FactorialProtocol,FactorialResult,seal_factorial_results,_new)
from .factorial_statistics import report_factorial
from .protocols import digest
from .worlds import make_world,airy_intensity


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _dependencies():
    from optomind_optics.harness.science import open_research_loop
    from tmm_engine import tmm_solver
    science=Path(inspect.getsourcefile(open_research_loop)).parent
    names=('open_research_loop','open_research_cli','controller','rollout','evsi','terminal_problem','model_ir','model_compiler',
           'inference','tempered_smc','likelihood_blocks','model_search','program_ir','program_runner','program_compiler',
           'program_search','jobs','repository','contracts','adequacy','calibration','world_client',
           'joint_proposer','literature_request','literature_bridge','typed_implication_runtime','compute_ledger',
           'reference_calibration','evidence_use','prediction','identifiability','method_runtime')
    paths={f'science/{name}.py':science/f'{name}.py' for name in names if (science/f'{name}.py').exists()}
    paths['engine/tmm_solver.py']=Path(inspect.getsourcefile(tmm_solver))
    for name in ('factorial_protocol','factorial_statistics','factorial_runner','worlds','service'):
        paths[f'evaluation/{name}.py']=Path(__file__).parent/f'{name}.py'
    return paths


def independent_task_scores(family,seed,thickness,pool,parameters=None,model=None):
    world=make_world(family,task_id='source',split='V',seed=seed)
    def loss(d):
        actual=replace(world,thickness_nm=float(d))
        return float((airy_intensity(actual,np.array([550.]),'R')[0]-.1)**2)
    terminal=loss(thickness)
    oracle=min(loss(d) for d in pool)
    prediction=None;max_error=None
    if model is not None:
        grid=np.linspace(433.,847.,19)
        residual=model.predict(parameters,grid)-airy_intensity(world,grid,'R')
        if not np.isfinite(residual).all():raise ValueError('nonfinite independent predictive comparison')
        prediction=float(np.mean(residual**2));max_error=float(np.max(np.abs(residual)))
    if not np.isfinite([terminal,oracle]).all():raise ValueError('nonfinite independent terminal score')
    return terminal,oracle,prediction,max_error


def validate_factorial_plan(plan):
    """This physical runner is scoped to its declared optical score, not arbitrary prose."""
    if plan.get('version')!='c17-full-scope-offline-v1' or plan.get('fixture')!='single_film':
        raise ValueError('unsupported factorial physical protocol')
    if not set(plan.get('families',()))<=set(('F0','F3','F4','F5','F6')) or not plan.get('families'):
        raise ValueError('unsupported independent-score family')
    if plan.get('channels')!=['R'] or plan.get('arms')!={name:list(flags) for name,flags in FACTOR_FLAGS.items()}:
        raise ValueError('factor or channel permissions differ from supported physical protocol')
    common=plan['common_config']
    required={'objective_wavelength_nm':550.,'objective_target':.1,'measurement_std':.002,'calibration_std':.001}
    if any(common.get(key)!=value for key,value in required.items()):
        raise ValueError('objective/noise changes require a new independent-score protocol; not silently reused')


def run_factorial_evaluation(output_dir,plan_path,*,phase='audit',fixture_limit=None):
    from optomind_optics.harness.science.open_research_cli import build_offline_loop
    from optomind_optics.harness.science.open_research_loop import OpenResearchConfig
    from optomind_optics.harness.science.model_ir import model_ir_from_dict
    from optomind_optics.harness.science.model_compiler import compile_model
    required={'model_expansion_enabled','program_composition_enabled'}
    if not required<=set(OpenResearchConfig.__dataclass_fields__):
        raise RuntimeError('production native factorial feature flags unavailable; no pseudo-policy substitution')
    plan_path=Path(plan_path);plan=json.loads(plan_path.read_text());validate_factorial_plan(plan)
    if phase not in ('pilot','audit'):raise ValueError('phase must be pilot or audit')
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    settings=plan[phase];count=settings['tasks_per_family']
    if fixture_limit is not None:
        if not isinstance(fixture_limit,int) or not 1<=fixture_limit<=count:raise ValueError('invalid engineering fixture limit')
        count=fixture_limit
    tasks=[{'task_id':f'{phase}-{family}-{i:03}','family':family,'seed':settings['seed_base']+j*10000+i}
           for j,family in enumerate(plan['families']) for i in range(count)]
    pool=tuple(np.linspace(**dict(start=plan['terminal_pool_nm']['start'],stop=plan['terminal_pool_nm']['stop'],num=plan['terminal_pool_nm']['count'])))
    parity=BaselineParityManifest(plan['common_config']['budget'],tuple(plan['channels']),tuple(plan['common_config']['grammar']),pool,
        ('initial model','grammar','prior parameter bounds','budget','noise model','terminal pool','channel permissions','task seed','heldout session contract'),
        tuple((name,'pending_unavailable' if name=='same_base_free_agent' else 'evaluator_oracle' if name=='informed_oracle'
                    else 'executed_factorial' if name=='same_grammar_search' else 'available_separate_audit',evidence)
              for name,evidence in plan['baselines'].items()))
    dependencies=_dependencies();hashes=tuple((name,_sha(path)) for name,path in dependencies.items())
    stats=plan['statistics']
    protocol=FactorialProtocol(f"{plan['version']}:{phase}",tuple(t['task_id'] for t in tasks),tuple(t['family'] for t in tasks),parity,hashes,
        tuple(tuple(pair) for pair in plan['comparisons']),plan['failure_loss'],stats['minimum_important_difference'],stats['alpha'],
        stats['replicates'],stats['seed'],stats['tail_fraction'],plan['resource_loss_cost'],
        phase='engineering_fixture' if fixture_limit else ('independent_pilot' if phase=='pilot' else 'fixed_size_exploratory_audit'),preregistration_hash=_sha(plan_path))
    protocol.freeze(out/'protocol.json')
    _new(out/'BASELINE_PARITY_MANIFEST.json',{'protocol_seal':protocol.seal,'manifest':asdict(parity)})
    _new(out/'frozen_config.json',{'plan':plan,'plan_sha256':_sha(plan_path),'tasks':tasks,'phase':protocol.phase,
         'protocol_seal':protocol.seal,'source_hashes':dict(hashes),'oracle_is_policy_input':False})
    snap=out/'source_snapshot';snap.mkdir()
    for name,path in dependencies.items():
        destination=snap/name;destination.parent.mkdir(parents=True,exist_ok=True);destination.write_bytes(path.read_bytes())
    rows=[]
    for task in tasks:
        for arm in FACTORIAL_ARMS:
            flags=FACTOR_FLAGS[arm];path=out/task['task_id']/arm
            trace={'task':task,'arm':arm,'factor_flags':flags,'protocol_seal':protocol.seal,'config':plan['common_config'],
                   'paid_calls':0,'input_tokens':0,'output_tokens':0,'timing_scope':'combined policy/fitting/acquisition and evaluator; not equal speed claim'}
            resources=();loop=None;terminal=prediction=false_acceptance=constraint=oracle=None;status='failed';cost=0.;start=time.perf_counter()
            try:
                if any(_sha(dependencies[name])!=expected for name,expected in hashes):
                    raise RuntimeError('runtime source changed after protocol freeze; retain failure, do not mix versions')
                public_config={**plan['common_config'],'design_thicknesses_nm':pool,
                    'run_id':f'factorial-{task["task_id"]}', # Same within paired arms; directories isolate stores.
                    'model_expansion_enabled':flags[0],'program_composition_enabled':flags[1]}
                payload={'family':task['family'],'fixture':plan['fixture'],'world_seed':task['seed'],'config':public_config}
                loop,resources=build_offline_loop(path,payload)
                result=loop.run(path)
                candidate=loop.repository.load(result.candidate_ref)
                inference=loop.repository.load(result.inference_ref)
                fitted_parameters=inference.posterior['parameters']
                model=compile_model(model_ir_from_dict(candidate.model_ir))
                design=float(result.candidate_roles['terminal_choice'].split(':',1)[1])
                # Production IDs use eight significant digits; recover the unique frozen numeric design.
                matches=[d for d in pool if f'thickness:{d:.8g}'==result.candidate_roles['terminal_choice']]
                if len(matches)!=1:raise ValueError('terminal design outside exact common pool')
                design=matches[0]
                terminal,oracle,prediction,max_error=independent_task_scores(task['family'],task['seed'],design,pool,fitted_parameters,model)
                adopted=result.candidate_roles['adoption_status']=='proposed_design'
                false_acceptance=bool(adopted and max_error>3*np.hypot(.002,.001))
                status='completed' if adopted else 'abstained'
                constraint=False
                trajectory=json.loads((path/'SCIENCE_OPEN_TRAJECTORY.json').read_text())
                trace.update(selected_result=result.model_dump(mode='json'),selected_design_nm=design,
                    production_trajectory_sha256=_sha(path/'SCIENCE_OPEN_TRAJECTORY.json'),
                    production_result_sha256=_sha(path/'SELECTED_RESEARCH_RESULT.json'),
                    production_compute=trajectory.get('compute_budget'),
                    independent_prediction_max_abs_error=max_error,terminal_loss=terminal,prediction_loss=prediction,
                    oracle_terminal_loss=oracle,false_acceptance=false_acceptance,
                    independent_score_semantics=plan['terminal_loss'])
            except Exception:
                trace['failure']=traceback.format_exc()
            finally:
                if loop is not None:
                    cost=sum(float(g.usage().get('cost',0.)) for g in set(loop.runner.gates.values()))
                    # A purchased receipt remains a real cost even if downstream processing failed.
                    provider=resources[-1]
                    if hasattr(provider,'db'):
                        purchased=sum(float(json.loads(row[0]).get('cost',{}).get('cost',0.)) for row in provider.db.execute('SELECT result FROM receipts'))
                        cost=max(cost,purchased)
                for resource in resources:resource.close()
                if loop is not None and getattr(loop,'compute_ledger',None) is not None and loop.compute_ledger not in resources:
                    loop.compute_ledger.close()
            if cost>parity.resource_budget+1e-10:constraint=True;status='budget_exceeded'
            elapsed=time.perf_counter()-start
            trace.update(status=status,resource_cost=cost,declared_loss_cost=cost*protocol.loss_cost_per_resource,wall_seconds=elapsed)
            trace_path=out/f'{task["task_id"]}-{arm}-evaluation-trace.json';_new(trace_path,trace)
            rows.append(FactorialResult(protocol.seal,task['task_id'],arm,*flags,terminal,prediction,false_acceptance,constraint,
                cost,cost*protocol.loss_cost_per_resource,status,parity.observation_permissions,digest(trace),oracle,wall_seconds=elapsed))
    seal_factorial_results(protocol,tuple(rows),out/'results.json')
    report=report_factorial(out/'results.json');_new(out/'report.json',report)
    return report
