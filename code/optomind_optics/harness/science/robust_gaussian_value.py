"""Finite joint ambiguity consumer of the existing deterministic robust MILP.

Gaussian probabilities are integrated exactly over declared bins. Uncertainty is
only the convex hull of explicit (prior, global-noise) joint-mass vertices. This
is not robustness to a continuous variance interval or arbitrary likelihoods.
"""
from contextlib import nullcontext
from dataclasses import asdict,dataclass
import numpy as np
from scipy.special import ndtr
from .terminal_problem import canonical_hash,probability_vector
from .robust_voi import robust_voi
from .evsi import ContinuationPolicy
from .compute_ledger import computational_event,ComputeBudgetExceeded


@dataclass(frozen=True)
class RobustGaussianPolicy:
    base_policy: ContinuationPolicy
    thresholds: tuple[float,...]
    joint_ambiguity_ref: str

    @property
    def policy_hash(self):return canonical_hash(asdict(self))

    def choose(self,observation,*,problem_hash,policy_hash=None):
        if policy_hash is not None and policy_hash!=self.policy_hash:raise ValueError('robust scalar policy binding changed')
        if not np.isscalar(observation) or not np.isfinite(observation):raise ValueError('finite scalar observation required')
        index=int(np.searchsorted(self.thresholds,float(observation),side='left'))
        return self.base_policy.choose(self.base_policy.observation_ids[index],problem_hash=problem_hash,
                                       policy_hash=self.base_policy.policy_hash)


@dataclass(frozen=True)
class RobustGaussianReport:
    estimator: str
    problem_hash: str
    policy: RobustGaussianPolicy
    policy_hash: str
    confirmed_positive: bool
    selected_action_id: str | None
    value_interval: tuple[float,float]
    net_value_interval: tuple[float,float]
    status: str
    prior_risk: float
    posterior_risk: float | None
    commitment_mode: str
    cost_vector: dict
    solver_receipt: dict
    approximation_scope: str
    diagnostics: dict


def robust_gaussian_report_from_dict(raw):
    data=dict(raw);p=dict(data['policy']);base=dict(p['base_policy'])
    base['observation_ids']=tuple(base['observation_ids']);base['design_ids']=tuple(base['design_ids'])
    p['base_policy']=ContinuationPolicy(**base);p['thresholds']=tuple(p['thresholds'])
    data['policy']=RobustGaussianPolicy(**p)
    if data['policy'].policy_hash!=data['policy_hash'] or data['policy'].base_policy.problem_hash!=data['problem_hash']:
        raise ValueError('robust policy receipt binding changed')
    if data['policy'].joint_ambiguity_ref!=canonical_hash(data['diagnostics']['joint_scenarios']):
        raise ValueError('joint ambiguity receipt changed')
    for name in ('value_interval','net_value_interval'):data[name]=tuple(data[name])
    return RobustGaussianReport(**data)


def evaluate_robust_gaussian(problem,prior_vertices,means,noise_std_vertices,thresholds,*,commitment_mode='precommit',
                             cost=0.,cost_units=None,source_ref, time_limit=30.,internal_loss_cost=0.,
                             action_id='new_independent_session'):
    means=np.asarray(means,float);sigmas=tuple(float(s) for s in noise_std_vertices);thresholds=tuple(float(t) for t in thresholds)
    if means.shape!=(len(problem.world_ids),) or not np.isfinite(means).all() or not sigmas or not np.isfinite(sigmas).all() or min(sigmas)<=0:
        raise ValueError('finite Gaussian world means and positive noise vertices required')
    if not thresholds or not np.isfinite(thresholds).all() or any(b<=a for a,b in zip(thresholds,thresholds[1:])):
        raise ValueError('strictly increasing finite observation thresholds required')
    priors=tuple(probability_vector(q,len(means)) for q in prior_vertices)
    if not priors or not source_ref or not action_id or commitment_mode not in ('precommit','replanning'):
        raise ValueError('explicit prior ambiguity vertices, source and commitment mode required')
    if not np.isfinite([cost,internal_loss_cost,time_limit]).all() or min(cost,internal_loss_cost)<0 or time_limit<=0 or (cost_units is not None and cost_units!=problem.units):
        raise ValueError('invalid cost units, conversion or solver limit')
    edges=np.array((-np.inf,*thresholds,np.inf));joints=[]
    for prior in priors:
        for sigma in sigmas:
            # One shared noise parameter defines every conditional cell of a vertex.
            cdf=ndtr((edges[None,:]-means[:,None])/sigma)
            kernel=np.diff(cdf,axis=1)
            joints.append(prior[:,None]*kernel)
    joint=np.asarray(joints);joint_ref=canonical_hash(joint.tolist())
    ids=tuple(f'gaussian_bin:{i}' for i in range(len(thresholds)+1))
    baseline=problem.solve_robust(priors)
    span=float(problem.losses.max()-problem.losses.min());interval=(-span,span)
    fallback=ContinuationPolicy(problem.cache_key,ids,(baseline.decision_id,)*len(ids),commitment_mode,
                                canonical_hash({'ignore_observation':True,'joint':joint_ref}))
    policy=RobustGaussianPolicy(fallback,thresholds,joint_ref)
    receipt={};attempted=0;posterior=None;positive=False;status='unknown_solver_not_completed'
    try:
        with computational_event('milp_solves') if commitment_mode=='precommit' else nullcontext():
            attempted=int(commitment_mode=='precommit')
            report=robust_voi(problem,joint,ids,mode=commitment_mode,cost=cost,cost_units=problem.units,
                              time_limit=time_limit,solver_receipt=receipt)
        policy=RobustGaussianPolicy(report.policy,thresholds,joint_ref)
        interval=report.value_interval;posterior=report.posterior_risk
        status=report.status
        positive=status=='supported_on_declared_finite_problem' and interval[0]>cost+internal_loss_cost
        if status!='supported_on_declared_finite_problem':status='unknown_incomplete_milp'
    except ComputeBudgetExceeded:
        status='unknown_compute_budget_exhausted'
    except RuntimeError as exc:
        status='unknown_no_certified_deterministic_policy';receipt['error']=str(exc)
    return RobustGaussianReport('finite_joint_gaussian_bin_minimax',problem.cache_key,policy,policy.policy_hash,
        positive,action_id if positive else None,interval,(interval[0]-cost-internal_loss_cost,interval[1]-cost-internal_loss_cost),
        status,baseline.risk,posterior,commitment_mode,
        {'milp_solves':attempted,'measurement_loss_cost':cost,'internal_loss_cost':internal_loss_cost,
         'units':problem.units,'cost_source_ref':source_ref},receipt,
        'finite Gaussian bins and convex hull of declared prior/global-noise joint vertices; no continuum-noise or continuous-design guarantee',
        {'prior_vertices':[q.tolist() for q in priors],'noise_std_vertices':sigmas,'thresholds':thresholds,
         'means':means.tolist(),'joint_scenarios':joint.tolist(),'joint_ambiguity_ref':joint_ref,'source_ref':source_ref,
         'unweighted_semantics':'explicit admissible priors, never an invented posterior',
         'ignore_observation_design':baseline.decision_id,'continuous_design_error':'unknown'})
