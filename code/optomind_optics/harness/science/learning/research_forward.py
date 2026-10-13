"""Fresh full OpenResearchLoop evaluation for frozen data-only policy candidates.

Factories and scoring are trusted evaluator infrastructure, never policy code.
Every candidate receives a new durable repository/gates and the same declared
pre-policy scientific state/library. No completed trajectory can substitute for
new candidate execution. Cost rates are explicit accounting conversions, not
claims about monetary provider prices.
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import time
import math

from ..contracts import Observation, SelectedResearchResult, canonical_hash
from ..method_assets import PolicyPayload
from .learning_runner import ForwardResult


@dataclass
class ResearchLoopInstance:
    loop: object
    work_dir: object
    resources: tuple = ()


def initial_state_fingerprint(loop):
    snapshot=loop.repository.latest_snapshot()
    objects=[loop.repository.load(ref) for ref in snapshot.object_refs] if snapshot else []
    if any(isinstance(obj,(Observation,SelectedResearchResult)) for obj in objects):
        raise ValueError('forward candidate requires unexecuted initial state; old observations/results are not allowed')
    return canonical_hash({'task':loop.task.ref().model_dump(mode='json'),
                           'initial_model':asdict(loop.initial_model),
                           'initial_observations':[]})


def library_fingerprint(loop):
    provider=getattr(loop,'method_provider',None)
    return provider.library_snapshot if provider is not None else canonical_hash([])


class ResearchLoopForwardRunner:
    def __init__(self, factory, score, *, compute_cost_rates):
        import math
        if not compute_cost_rates or any(not math.isfinite(v) or v<=0 for v in compute_cost_rates.values()):
            raise ValueError('explicit positive compute accounting rates required')
        self.factory=factory
        self.score=score
        self.compute_cost_rates=dict(compute_cost_rates)
        self.seen_roots=set()
        self.attempts=[]

    def __call__(self, view, policy, meter):
        from ..open_research_loop import OpenResearchLoop
        from optomind_research.runtime.artifact_store import atomic_write_json
        policy=PolicyPayload.model_validate_json(policy.model_dump_json())
        instance=self.factory(view,policy)
        if not isinstance(instance,ResearchLoopInstance) or not isinstance(instance.loop,OpenResearchLoop):
            raise ValueError('factory must return an actual isolated OpenResearchLoop')
        loop=instance.loop
        root=str(loop.repository.root.resolve())
        work_dir=Path(instance.work_dir)
        started=time.perf_counter()
        before={}; gates=(); admitted=False
        record={'task_id':view.task_id,'policy':policy.model_dump(mode='json'),'repository':root,
                'status':'failed','initial_snapshot':view.initial_snapshot,'library_snapshot':view.library_snapshot}
        try:
            if root in self.seen_roots:
                raise ValueError('each candidate requires a fresh repository; cache/replay scoring forbidden')
            self.seen_roots.add(root)
            if initial_state_fingerprint(loop)!=view.initial_snapshot or library_fingerprint(loop)!=view.library_snapshot:
                raise ValueError('candidate initial scientific state or library differs from frozen task')
            actual=getattr(loop,'experiment_policy',None)
            if actual is None or actual != policy:
                raise ValueError('full research runtime must consume the exact candidate policy')
            if loop.config.budget != view.available_budget or loop.config.measurement_cost != view.final_validation_reserve:
                raise ValueError('candidate acquisition budget/reserve differs from evaluation view')
            if view.observations:
                raise ValueError('this cold-loop adapter cannot silently ignore supplied initial observations')
            gates=tuple(set(loop.runner.gates.values()))
            before={id(g):g.usage().get('cost',0.) for g in gates}
            if any(before.values()) or any(loop.compute_ledger.snapshot()['usage'].values()):
                raise ValueError('candidate gate or compute ledger already contains acquired work')
            limits=loop.compute_ledger.snapshot()['limits']
            if set(limits)-set(self.compute_cost_rates):
                raise ValueError('unpriced compute resource in full forward run')
            reservation=loop.config.budget+sum(limits[k]*self.compute_cost_rates[k] for k in limits)
            if meter.total+reservation>meter.limit:
                raise ValueError('remaining outer budget cannot reserve the bounded full forward run')
            record['reserved_outer_cost']=reservation
            admitted=True
            result=loop.run(work_dir)
            from ..compute_ledger import active_compute_ledger, compute_scope
            with active_compute_ledger(loop.compute_ledger), compute_scope('learning_independent_score'):
                score=float(self.score(view,result,loop))
            if not math.isfinite(score):
                raise ValueError('independent score must be finite')
            record.update(status='completed',selected_result_ref=result.ref().model_dump(mode='json'))
        except BaseException as exc:
            record['error_type']=type(exc).__name__
            record['error']=str(exc)
            raise
        finally:
            resources={}
            try:
                resources['measurement']=sum(g.usage().get('cost',0.)-before.get(id(g),0.) for g in gates)
                compute=loop.compute_ledger.snapshot()['usage'] if admitted else {}
                if set(compute)-set(self.compute_cost_rates):
                    raise ValueError('unpriced compute resource in full forward run')
                resources.update({k:float(v) for k,v in compute.items()})
                charged=resources['measurement']+sum(resources.get(k,0.)*rate for k,rate in self.compute_cost_rates.items())
                record.update(resources=resources,compute_cost_rates=self.compute_cost_rates,charged_cost=charged,
                              wall_seconds=time.perf_counter()-started)
                self.attempts.append(record)
                # Persist failed-attempt costs as well, before outer budget
                # enforcement can raise. This is evaluator-side evidence.
                atomic_write_json(work_dir/'LEARNING_FORWARD_RECEIPT.json',record)
                meter.charge('full_research_resources',charged)
            finally:
                for resource in instance.resources:
                    resource.close()
        return ForwardResult({'selected_result_ref':record['selected_result_ref'],
                              'policy':policy.model_dump(mode='json')},score,
                             {'scientific_score':score,'measurement_cost':resources['measurement'],
                              'compute_cost':record['charged_cost']-resources['measurement']},record)
