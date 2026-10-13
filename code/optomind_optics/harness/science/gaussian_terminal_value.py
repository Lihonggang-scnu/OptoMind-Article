"""Continuous scalar Gaussian observations on a frozen finite world/design pool.

The outer evaluation samples worlds; the deployed policy receives only a scalar
observation. Independent confirmation and every refinement stage are fixed in a
hashed protocol before outcomes. Hoeffding bounds cover this finite model only,
not prior/model correctness or missing continuous designs.
"""
from dataclasses import asdict, dataclass
import numpy as np
from scipy.special import logsumexp
from .terminal_problem import canonical_hash, probability_vector
from .action_value import confirm_policy_value
from .compute_ledger import ComputeBudgetExceeded


@dataclass(frozen=True)
class GaussianAction:
    action_id: str
    means: tuple[float, ...]
    standard_deviation: float
    loss_cost: float = 0.
    resource_cost: float = 0.
    source_ref: str = 'declared-gaussian-model'

    def __post_init__(self):
        object.__setattr__(self,'means',tuple(float(v) for v in self.means))
        if not self.action_id or not self.source_ref or not self.means or not np.isfinite(self.means).all():
            raise ValueError('Gaussian action requires identified finite means and source')
        if not np.isfinite([self.standard_deviation,self.loss_cost,self.resource_cost]).all() or self.standard_deviation<=0 or min(self.loss_cost,self.resource_cost)<0:
            raise ValueError('Gaussian noise must be positive; costs nonnegative')


@dataclass(frozen=True)
class GaussianValueProtocol:
    proposal_samples: tuple[int, ...] = (256,1024)
    confirmation_samples: tuple[int, ...] = (1024,4096)
    proposal_seed: int = 1001
    confirmation_seed: int = 1000001
    proposal_split_id: str = 'proposal'
    confirmation_split_id: str = 'confirmation'
    family_alpha: float = .05
    ranking_tolerance: float = 0.
    min_world_ess: float = 1.
    support_semantics: str = 'declared_finite_worlds'
    evaluation_loss_cost_per_sample: float = 0.
    precomputation_loss_cost: float = 0.
    cost_ref: str = 'declared-zero-compute-loss'

    def __post_init__(self):
        for name in ('proposal_samples','confirmation_samples'):
            object.__setattr__(self,name,tuple(getattr(self,name)))
        n=len(self.proposal_samples)
        if not n or n!=len(self.confirmation_samples) or any(type(v) is not int or v<2 for v in self.proposal_samples+self.confirmation_samples):
            raise ValueError('fixed proposal and confirmation sample schedules required')
        if any(b<=a for values in (self.proposal_samples,self.confirmation_samples) for a,b in zip(values,values[1:])):
            raise ValueError('refinement sample counts must increase')
        if type(self.proposal_seed) is not int or type(self.confirmation_seed) is not int or min(self.proposal_seed,self.confirmation_seed)<0:
            raise ValueError('nonnegative integer independent seeds required')
        if set(range(self.proposal_seed,self.proposal_seed+n)) & set(range(self.confirmation_seed,self.confirmation_seed+n)):
            raise ValueError('proposal and confirmation random streams overlap')
        if not self.proposal_split_id or not self.confirmation_split_id or self.proposal_split_id==self.confirmation_split_id:
            raise ValueError('proposal and confirmation need distinct split identities')
        if not 0<self.family_alpha<1 or not np.isfinite([self.ranking_tolerance,self.min_world_ess,self.evaluation_loss_cost_per_sample,self.precomputation_loss_cost]).all() or min(self.ranking_tolerance,self.evaluation_loss_cost_per_sample,self.precomputation_loss_cost)<0 or self.min_world_ess<1:
            raise ValueError('invalid preregistered precision, ESS or compute cost')
        if self.support_semantics not in ('declared_finite_worlds','posterior_sample_approximation') or not self.cost_ref:
            raise ValueError('declared support semantics and compute conversion source required')

    @property
    def protocol_hash(self):return canonical_hash(asdict(self))


@dataclass(frozen=True)
class GaussianContinuationPolicy:
    problem_hash: str
    action_id: str
    world_probabilities: tuple[float, ...]
    means: tuple[float, ...]
    standard_deviation: float
    design_ids: tuple[str, ...]
    losses: tuple[tuple[float, ...], ...]
    commitment_mode: str = 'bayesian_precommit_continuous_scalar_gaussian'

    def __post_init__(self):
        for key in ('world_probabilities','means','design_ids'):
            object.__setattr__(self,key,tuple(getattr(self,key)))
        object.__setattr__(self,'losses',tuple(tuple(row) for row in self.losses))
        probability_vector(self.world_probabilities,len(self.means))
        if (not self.problem_hash or not self.action_id or not self.design_ids
                or len(set(self.design_ids))!=len(self.design_ids)
                or np.asarray(self.losses).shape!=(len(self.means),len(self.design_ids))
                or not np.isfinite(self.losses).all() or not np.isfinite(self.means).all()
                or not np.isfinite(self.standard_deviation) or self.standard_deviation<=0
                or self.commitment_mode!='bayesian_precommit_continuous_scalar_gaussian'):
            raise ValueError('invalid frozen Gaussian continuation')

    @property
    def policy_hash(self):return canonical_hash(asdict(self))

    def posterior(self, observation):
        if not np.isscalar(observation) or not np.isfinite(observation):
            raise ValueError('policy receives only one finite scalar observation')
        q=np.asarray(self.world_probabilities)
        with np.errstate(divide='ignore'):
            logp=np.log(q)-.5*((float(observation)-np.asarray(self.means))/self.standard_deviation)**2
        normalizer=logsumexp(logp)
        if not np.isfinite(normalizer):raise ValueError('observation posterior numerically unsupported')
        return np.exp(logp-normalizer)

    def choose(self, observation, *, problem_hash, policy_hash=None):
        if problem_hash!=self.problem_hash or (policy_hash is not None and policy_hash!=self.policy_hash):
            raise ValueError('continuous continuation binding changed')
        posterior=self.posterior(observation)
        return self.design_ids[int(np.argmin(posterior @ np.asarray(self.losses)))]


@dataclass(frozen=True)
class GaussianValueReport:
    estimator: str
    problem_hash: str
    policy: GaussianContinuationPolicy
    policy_hash: str
    selected_action_id: str | None
    confirmed_positive: bool
    value_interval: tuple[float,float]
    net_value_interval: tuple[float,float]
    status: str
    protocol_hash: str
    proposal_split_id: str
    confirmation_split_id: str
    cost_vector: dict
    approximation_scope: str
    diagnostics: dict


def gaussian_value_report_from_dict(raw):
    data=dict(raw);policy=dict(data['policy'])
    for key in ('world_probabilities','means','design_ids'):policy[key]=tuple(policy[key])
    policy['losses']=tuple(tuple(row) for row in policy['losses'])
    data['policy']=GaussianContinuationPolicy(**policy)
    if data['policy'].policy_hash!=data['policy_hash'] or data['policy'].problem_hash!=data['problem_hash']:
        raise ValueError('frozen policy receipt binding changed')
    if GaussianValueProtocol(**data['diagnostics']['protocol']).protocol_hash!=data['protocol_hash']:
        raise ValueError('frozen valuation protocol changed')
    for key in ('value_interval','net_value_interval'):data[key]=tuple(data[key])
    return GaussianValueReport(**data)


def evaluate_gaussian_actions(problem, probabilities, actions, protocol, *, used_split_ids=()):
    """Screen by paired utility, refine fixed stages, independently confirm winner.

    The family error budget covers all predeclared stage/action comparisons,
    including data-dependent winner selection and early stopping. Shared random
    numbers are allowed for proposal ranking; hidden world indices are passed
    only to the observation generator, never to the continuation.
    """
    q=probability_vector(probabilities,len(problem.world_ids));actions=tuple(actions)
    if not actions or len({a.action_id for a in actions})!=len(actions):raise ValueError('unique nonempty action menu required')
    if any(len(a.means)!=len(q) for a in actions):raise ValueError('Gaussian means differ from world support')
    if protocol.proposal_split_id in used_split_ids or protocol.confirmation_split_id in used_split_ids:
        raise ValueError('valuation and independent confirmation require fresh splits')
    policies=[GaussianContinuationPolicy(problem.cache_key,a.action_id,tuple(q),a.means,a.standard_deviation,
                                        problem.design_ids,tuple(map(tuple,problem.losses))) for a in actions]
    span=float(problem.losses.max()-problem.losses.min())
    total_attempted=0;stage_records=[];min_ess=float(len(q));last_interval=(-span,span)
    winner=0;status='unknown_insufficient_precision';confirmed=False
    comparisons=2*len(actions)*len(protocol.proposal_samples)
    def evaluate(index,n,seed,split):
        nonlocal total_attempted,min_ess
        action,policy=actions[index],policies[index]
        def sample(world,rng):
            nonlocal total_attempted
            total_attempted+=1
            return float(rng.normal(action.means[world],action.standard_deviation))
        def choose(observation):
            nonlocal min_ess
            posterior=policy.posterior(observation)
            min_ess=min(min_ess,float(1./np.dot(posterior,posterior)))
            return policy.design_ids[int(np.argmin(posterior @ problem.losses))]
        return confirm_policy_value(problem,q,sample,choose,policy_hash=policy.policy_hash,
            confirmation_split_id=split,used_split_ids=used_split_ids,n_samples=n,seed=seed,
            comparison_count=comparisons,family_alpha=protocol.family_alpha,loss_cost=action.loss_cost,
            evaluation_cost_per_sample=protocol.evaluation_loss_cost_per_sample,meter_resource='valuation_samples')
    try:
        for stage,(n,m) in enumerate(zip(protocol.proposal_samples,protocol.confirmation_samples)):
            proposals=[evaluate(i,n,protocol.proposal_seed+stage,f'{protocol.proposal_split_id}:{stage}:{a.action_id}') for i,a in enumerate(actions)]
            winner=int(np.argmax([r.estimate-a.loss_cost for r,a in zip(proposals,actions)]))
            # Independently evaluate the frozen winner, without optimizing on confirmation outcomes.
            confirmation=evaluate(winner,m,protocol.confirmation_seed+stage,f'{protocol.confirmation_split_id}:{stage}')
            last_interval=confirmation.value_interval
            internal=total_attempted*protocol.evaluation_loss_cost_per_sample+protocol.precomputation_loss_cost
            lower=confirmation.value_interval[0]-actions[winner].loss_cost-internal
            upper=confirmation.value_interval[1]-actions[winner].loss_cost-internal
            other_upper=max([proposals[i].value_interval[1]-actions[i].loss_cost for i in range(len(actions)) if i!=winner]+[0.])
            stable=proposals[winner].value_interval[0]-actions[winner].loss_cost>other_upper+protocol.ranking_tolerance
            ess_ok=protocol.support_semantics=='declared_finite_worlds' or min_ess>=protocol.min_world_ess
            stage_records.append({'stage':stage,'proposal_reports':[asdict(r) for r in proposals],
                                  'confirmation':asdict(confirmation),'winner':actions[winner].action_id,
                                  'ranking_separated':stable,'min_posterior_ess':min_ess,'net_interval':(lower,upper)})
            if not ess_ok:
                status='unknown_refine_posterior_world_support';break
            if lower>0 and stable:
                status='positive_net_value_independently_confirmed';confirmed=True;break
            if max(r.value_interval[1]-a.loss_cost-internal for r,a in zip(proposals,actions))<=0:
                status='no_positive_value_confirmed';break
    except ComputeBudgetExceeded:
        status='unknown_compute_budget_exhausted'
    internal=total_attempted*protocol.evaluation_loss_cost_per_sample+protocol.precomputation_loss_cost
    costs={'valuation_samples':total_attempted,'internal_loss_cost':internal,
           'measurement_loss_cost':actions[winner].loss_cost,'measurement_resource_cost':actions[winner].resource_cost,
           'cost_ref':protocol.cost_ref,'units':problem.units,
           'precomputation_loss_cost':protocol.precomputation_loss_cost,
           'precomputation_cost_scope':'caller supplies actual charged model/solver usage under frozen loss-unit conversion; default zero is an explicit unpriced assumption, not empirical free compute'}
    return GaussianValueReport('independent_paired_mc_scalar_gaussian',problem.cache_key,policies[winner],policies[winner].policy_hash,
        actions[winner].action_id if confirmed else None,confirmed,last_interval,
        (last_interval[0]-actions[winner].loss_cost-internal,last_interval[1]-actions[winner].loss_cost-internal),
        status,protocol.protocol_hash,protocol.proposal_split_id,protocol.confirmation_split_id,costs,
        'continuous scalar Gaussian known-noise likelihood; finite declared posterior worlds and common designs; conditional utility only',
        {'stages':stage_records,'min_posterior_ess':min_ess,'family_alpha':protocol.family_alpha,
         'declared_comparisons':comparisons,'continuous_design_error':'unknown',
         'protocol':asdict(protocol),'action_refs':[canonical_hash(asdict(a)) for a in actions],
         'no_world_index_to_continuation':True})
