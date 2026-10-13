from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys

from .contracts import canonical_hash
from .method_assets import MethodRegistry
from .method_lifecycle import finalize_completed_research
from .method_policy import BoundedMethodPolicySearch,MethodGenerationPolicy,MethodPolicyTask
from .repository import ScienceRepository
from optomind_research.runtime.artifact_store import atomic_write_json


def _source(output, diagnostic_config=None, diagnostic_world=None):
    if diagnostic_config:
        from .diagnostic_cli import parse_public_config
        from .diagnostic_research import DiagnosticResearchLoop
        from optomind_eval.diagnostic_world import SyntheticDiagnosticProvider
        public=json.loads(Path(diagnostic_config).read_text())
        world=json.loads(Path(diagnostic_world).read_text())
        families,cfg,space,initial=parse_public_config(public)
        provider=SyntheticDiagnosticProvider(family=next(f for f in families if f.family_id==world['family_id']),
            parameters=world['parameters'],config=cfg,seed=world.get('seed',9127),mismatch_layer=world.get('mismatch_layer'))
        repository=ScienceRepository(output/'science')
        try:
            loop=DiagnosticResearchLoop(families=families,config=cfg,search_space=space,repository=repository,
                run_id=public['run_id'],provider=provider)
            loop.run(initial,output)
            result=next(repository.load(ref) for ref in repository.latest_snapshot().object_refs if ref.object_id==public['run_id']+':selected')
            receipt=finalize_completed_research(repository,task_ref=loop.task.ref(),result_ref=result.ref())
        finally:
            repository.close()
    else:
        from .open_research_cli import build_offline_loop
        payload={'family':'F0','world_seed':13,'config':{'run_id':'automatic-method-source','measurement_std':.004,
            'max_iterations':1,'unknown_exploration_limit':0,'fit_starts':8,'max_nfev':160}}
        output.mkdir(parents=True,exist_ok=True)
        atomic_write_json(output/'SOURCE_PROTOCOL.json',payload)
        loop,resources=build_offline_loop(output,payload)
        try:
            result=loop.run(output)
            receipt=finalize_completed_research(loop.repository,task_ref=loop.task.ref(),result_ref=result.ref())
        finally:
            for resource in resources:
                resource.close()
    atomic_write_json(output/'METHOD_LIFECYCLE.json',receipt.model_dump(mode='json'))
    if receipt.status!='registered_bounded_trial':
        raise ValueError('source did not qualify; no replay/transfer claims: '+','.join(receipt.reasons))
    return output/'science'


def run_suite(output, *, library=None, policy_trials=2, diagnostic_config=None, diagnostic_world=None):
    from optomind_eval.method_transfer import TransferCase,execute_transfer_case,forward_policy_row
    output=Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('suite requires a fresh output directory')
    output.mkdir(parents=True,exist_ok=True)
    transfer_cases=[TransferCase('transfer-'+str(i),indices,layers,8200+i) for i,(indices,layers) in enumerate(
        (((1.65,2.2),1),((1.95,2.65),1),((1.7,2.35),2),((1.8,2.45),3),((2.0,2.7),3)))]
    replay_cases=[TransferCase('replay-'+str(i),(1.8,2.4),1,8100+i) for i in range(5)]
    baseline=MethodGenerationPolicy(grid_rule='uniform',points=2)
    candidates=(MethodGenerationPolicy(grid_rule='mean_pair_greedy',points=2,nuisance_rule='mean_prediction'),
                MethodGenerationPolicy(grid_rule='worst_pair_greedy',points=2,nuisance_rule='closest_pair'))
    frozen={'schema_version':'method-capability-diagnostic.v1','replay_cases':[asdict(c) for c in replay_cases],
            'transfer_cases':[asdict(c) for c in transfer_cases],'policy_trials':policy_trials,
            'baseline':baseline.model_dump(mode='json'),'candidates':[p.model_dump(mode='json') for p in candidates],
            'primary_transfer_target':'at least four of five correct singleton retained mechanisms',
            'primary_policy_score':'correct singleton mechanism fraction, equal two-point acquisition and compute caps',
            'replay_target':'five fresh-process executions without a conversation or reused execution journal',
            'scope':'small synthetic diagnostics, no population performance or RSI claim',
            'source_hashes':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                for name in ('method_assets.py','method_runtime.py','method_lifecycle.py','method_policy.py','method_cli.py','diagnostic_design.py')}}
    atomic_write_json(output/'FROZEN_METHOD_PROTOCOL.json',frozen)
    library=Path(library).resolve() if library else _source(output/'source',diagnostic_config,diagnostic_world)
    replays=[]
    for case in replay_cases:
        spec=output/(case.case_id+'.json');atomic_write_json(spec,asdict(case))
        destination=output/case.case_id
        command=[sys.executable,'-m','optomind_optics.harness.science.method_cli','--offline','--case',str(spec),
                 '--library',str(library),'--output',str(destination)]
        completed=subprocess.run(command,text=True,capture_output=True,check=False)
        (output/(case.case_id+'.log')).write_text(completed.stdout+completed.stderr)
        if completed.returncode:
            replays.append({'case_id':case.case_id,'executed':False,'returncode':completed.returncode})
        else:
            report=json.loads((destination/'METHOD_TRANSFER_RESULT.json').read_text())
            replays.append({'case_id':case.case_id,'executed':report['method_receipt']['status']=='executed',
                'program_hash':report['executed_program_hash'],'result':str(destination/'METHOD_TRANSFER_RESULT.json')})
    transfers=[]
    for case in transfer_cases:
        report=execute_transfer_case(library,case,output/case.case_id)
        transfers.append({'case_id':case.case_id,'passed':report['evaluation']['passed'],'layers':case.layers,
            'indices':case.indices,'result':str(output/case.case_id/'METHOD_TRANSFER_RESULT.json')})
    policy_reports=[]
    for trial in range(policy_trials):
        trial_dir=output/('policy-trial-'+str(trial));trial_dir.mkdir()
        specs=[];cases={}
        for split,count,offset in (('T',3,0),('V',2,100),('P',5,200),('A',2,300)):
            for i in range(count):
                task_id=f'{split}-{trial}-{i}'
                case=TransferCase(task_id,(1.6+.09*i,2.05+.12*i),1+i%3,9100+trial*1000+offset+i)
                cases[task_id]=case
                specs.append(MethodPolicyTask(task_id=task_id,provenance_group=task_id,split=split,budget=2.))
        atomic_write_json(trial_dir/'FROZEN_TASKS.json',[asdict(case) for case in cases.values()])
        repository=ScienceRepository(library,read_only=True)
        snapshot=MethodRegistry(repository).snapshot()
        library_snapshot=canonical_hash([ref.model_dump(mode='json') for ref in snapshot.asset_refs]);repository.close()
        counter=[0]
        def forward(task,policy):
            counter[0]+=1
            return forward_policy_row(task,policy,library=library,case=cases[task.task_id],
                output=trial_dir/(f'run-{counter[0]:03}-'+task.task_id))
        search=BoundedMethodPolicySearch(specs,forward,library_snapshot=library_snapshot,total_budget=80.,max_forward_calls=40)
        ids=lambda split:[t.task_id for t in specs if t.split==split]
        evolved=search.select(baseline,candidates,train_ids=ids('T'),validation_ids=ids('V'),shortlist=2)
        atomic_write_json(trial_dir/'FROZEN_SELECTED_POLICY.json',evolved.model_dump(mode='json'))
        heldout=search.compare_holdout(baseline,evolved,task_ids=ids('P'))
        audit=search.compare_holdout(baseline,evolved,task_ids=ids('A'),split='A')
        manifest=search.manifest()
        means={arm:sum(row.score for row in rows)/len(rows) for arm,rows in heldout.items()}
        audit_means={arm:sum(row.score for row in rows)/len(rows) for arm,rows in audit.items()}
        compute={arm:{key:sum(row.output['compute'][key] for row in rows)
                      for key in ('prediction_calls','solver_points')} for arm,rows in heldout.items()}
        for arm,rows in heldout.items():
            compute[arm]['planning_subset_evaluations']=sum(row.output['planning_compute']['subset_evaluations'] for row in rows)
        manifest.update(heldout_scores=means,audit_scores=audit_means,selected_policy=evolved.model_dump(mode='json'),
                        heldout_gain=means['evolved']-means['fixed'],heldout_compute=compute)
        atomic_write_json(trial_dir/'POLICY_COMPARISON.json',manifest)
        policy_reports.append({'trial':trial,'heldout_scores':means,'audit_scores':audit_means,
            'heldout_gain':means['evolved']-means['fixed'],'selected_policy_hash':evolved.version_hash,
            'baseline_policy_hash':baseline.version_hash,'library_snapshot':library_snapshot,
            'actual_cost':manifest['actual_cost'],'forward_calls':manifest['forward_calls'],
            'heldout_compute':compute,
            'result':str(trial_dir/'POLICY_COMPARISON.json')})
    result={'library':str(library),'replays':replays,'transfers':transfers,'policy_comparisons':policy_reports,
            'replay_pass_count':sum(r['executed'] for r in replays),'transfer_pass_count':sum(r['passed'] for r in transfers),
            'policy_gain_status':'small_diagnostic_gain' if policy_reports and all(r['heldout_gain']>0 for r in policy_reports) else 'gain_not_established',
            'paid_calls':0,'limitations':['Five transfer cases do not estimate population accuracy',
                'Bounded generation-policy mutation is not recursive self-improvement',
                'Same library and resource caps do not imply equal actual compute use',
                'Assets remain task_validated; this diagnostic never auto-promotes production use']}
    atomic_write_json(output/'METHOD_CAPABILITY_RESULT.json',result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--offline',action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--library',type=Path)
    parser.add_argument('--case',type=Path)
    parser.add_argument('--policy-trials',type=int,default=2,choices=range(0,4))
    parser.add_argument('--diagnostic-config',type=Path)
    parser.add_argument('--diagnostic-world',type=Path)
    args=parser.parse_args(argv)
    if bool(args.diagnostic_config)!=bool(args.diagnostic_world):
        parser.error('diagnostic source requires both public config and separate evaluator world')
    if args.case:
        if not args.library:
            parser.error('--case requires --library')
        from optomind_eval.method_transfer import TransferCase,execute_transfer_case
        raw=json.loads(args.case.read_text());raw['indices']=tuple(raw['indices'])
        result=execute_transfer_case(args.library,TransferCase(**raw),args.output)
        print(json.dumps({'executed':result['method_receipt']['status'],'passed':result['evaluation']['passed']}))
    else:
        result=run_suite(args.output,library=args.library,policy_trials=args.policy_trials,
            diagnostic_config=args.diagnostic_config,diagnostic_world=args.diagnostic_world)
        print(json.dumps({key:result[key] for key in ('replay_pass_count','transfer_pass_count','policy_gain_status','paid_calls')}))


if __name__=='__main__':
    main()
