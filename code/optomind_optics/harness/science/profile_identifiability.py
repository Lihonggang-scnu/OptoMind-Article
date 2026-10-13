"""Bounded nuisance-refitted likelihood profiles and worst examined pair.

The profile grid and restart/budget contract are frozen. Pair search is exhaustive
only over admitted fitted profile points; no global identifiability certificate
or universal chi-square coverage is inferred from a finite grid.
"""
from dataclasses import asdict,dataclass
import itertools
import numpy as np
from scipy.optimize import least_squares
from .terminal_problem import canonical_hash


@dataclass(frozen=True)
class ProfileIdentifiabilityConfig:
    grid_points: int = 9
    starts: int = 2
    max_nfev: int = 40
    max_residual_evaluations: int = 2000
    loss_delta: float = 1.920729410347062
    equivalence_radius: float = 1.
    pair_budget: int = 10000
    max_action_samples: int = 16
    seed: int = 31415

    def __post_init__(self):
        if any(type(v) is not int or v<1 for v in (self.grid_points,self.starts,self.max_nfev,self.max_residual_evaluations,self.pair_budget,self.max_action_samples)) or self.grid_points<3:
            raise ValueError('positive fixed profile/search budgets and grid>=3 required')
        if not np.isfinite([self.loss_delta,self.equivalence_radius]).all() or min(self.loss_delta,self.equivalence_radius)<0:
            raise ValueError('finite nonnegative support/equivalence tolerances required')
        if type(self.seed) is not int or self.seed<0:raise ValueError('fixed nonnegative profile seed required')

    @property
    def config_hash(self):return canonical_hash(asdict(self))


class _ProfileBudgetReached(RuntimeError):pass


def profile_identifiability(*,residual,unpack,best_x,best_residual,parameter_names,config,terminal_action=None,source_ref='',best_converged=True):
    best_x=np.asarray(best_x,float);best_residual=np.asarray(best_residual,float)
    names=tuple(parameter_names);n=len(names)
    if best_x.shape!=(n,) or not np.isfinite(best_x).all() or np.any((best_x<0)|(best_x>1)):
        raise ValueError('normalized profile center differs from parameter bounds')
    if best_residual.ndim!=1 or not np.isfinite(best_residual).all():raise ValueError('finite whitened residual required')
    rng=np.random.default_rng(config.seed);calls=solves=0;rows=[];candidates=[];incomplete=not best_converged
    best_loss=float(best_residual@best_residual/2)
    def evaluate(x):
        nonlocal calls
        if calls>=config.max_residual_evaluations:raise _ProfileBudgetReached()
        calls+=1
        r=np.asarray(residual(x),float)
        if r.shape!=best_residual.shape or not np.isfinite(r).all():raise ValueError('profile residual shape or finiteness changed')
        return r
    def admit(x,r,converged):
        if not converged or float(r@r/2)>best_loss+config.loss_delta+1e-10:return
        if any(np.allclose(x,old[0],rtol=0,atol=1e-9) for old in candidates):return
        parameters=unpack(x)
        action=terminal_action(parameters) if terminal_action else None
        candidates.append((np.array(x),r,parameters,action))
    admit(best_x,best_residual,best_converged)
    try:
        for j,name in enumerate(names):
            others=np.array([k for k in range(n) if k!=j],dtype=int)
            for fixed in sorted(set(np.linspace(0.,1.,config.grid_points).tolist()+[float(best_x[j])])):
                def expand(z):
                    x=best_x.copy();x[j]=fixed;x[others]=z;return x
                results=[];errors=[]
                if len(others)==0:
                    try:
                        x=expand(np.array([]));r=evaluate(x);results.append((float(r@r/2),True,x,r))
                    except (ValueError,FloatingPointError) as exc:
                        errors.append(str(exc))
                else:
                    starts=[best_x[others]]+list(rng.uniform(0.,1.,(config.starts-1,len(others))))
                    for start in starts:
                        solves+=1
                        try:
                            opt=least_squares(lambda z:evaluate(expand(z)),start,bounds=(np.zeros(len(others)),np.ones(len(others))),
                                              max_nfev=config.max_nfev,ftol=1e-10,xtol=1e-10,gtol=1e-10)
                            results.append((float(opt.cost),bool(opt.success),expand(opt.x),np.asarray(opt.fun)))
                        except (ValueError,FloatingPointError) as exc:
                            errors.append(str(exc))
                if not results:
                    incomplete=True;x=expand(best_x[others])
                    rows.append({'parameter':name,'fixed_value':unpack(x)[name],'normalized_fixed_value':fixed,
                                 'parameters':unpack(x),'loss':None,'converged':False,
                                 'within_support_threshold':False,'errors':errors})
                    continue
                loss,converged,x,r=min(results,key=lambda item:item[0])
                incomplete=incomplete or not converged
                rows.append({'parameter':name,'fixed_value':unpack(x)[name],'normalized_fixed_value':fixed,
                             'parameters':unpack(x),'loss':loss,'converged':converged,
                             'within_support_threshold':converged and loss<=best_loss+config.loss_delta+1e-10})
                admit(x,r,converged)
    except _ProfileBudgetReached:
        incomplete=True
    supports={}
    for name in names:
        kept=[row['fixed_value'] for row in rows if row['parameter']==name and row['within_support_threshold']]
        kept.append(unpack(best_x)[name])
        supports[name]=(float(min(kept)),float(max(kept)))
    worst=None;checks=0;ambiguous=False
    for left,right in itertools.combinations(candidates,2):
        if checks>=config.pair_budget:
            incomplete=True;break
        checks+=1
        distance=float(np.linalg.norm(left[1]-right[1]))
        if distance>config.equivalence_radius:continue
        separation=float(np.linalg.norm(left[0]-right[0]))
        differs=terminal_action is not None and left[3]!=right[3]
        ambiguous=ambiguous or differs
        if worst is None or (differs,separation)>(worst['actions_differ'],worst['normalized_parameter_separation']):
            worst={'left':left[2],'right':right[2],'left_action':left[3],'right_action':right[3],
                   'actions_differ':differs,'whitened_prediction_distance':distance,'normalized_parameter_separation':separation,
                   'left_loss':float(left[1]@left[1]/2),'right_loss':float(right[1]@right[1]/2)}
    impact='action_ambiguous' if ambiguous else ('unknown' if incomplete or terminal_action is None else 'stable_on_examined_profile_set')
    return {'config':asdict(config),'config_hash':config.config_hash,'source_ref':source_ref,'profile_points':rows,
            'support_intervals':supports,'support_threshold_loss':best_loss+config.loss_delta,'worst_pair':worst,
            'candidate_count':len(candidates),'pair_checks':checks,'reference_point_converged':bool(best_converged),
            'pair_selection_objective':'action disagreement, then normalized parameter separation','residual_evaluations':calls,'optimizer_solves':solves,
            'task_impact':impact,'status':'unknown_search_incomplete' if incomplete else 'completed_declared_profile_search',
            'limits':['Profiles reoptimize nuisance parameters using bounded multistart least squares, not a global optimizer.',
                      'Support intervals are envelopes of sampled accepted profile points; disconnected support and gaps are retained in profile_points.',
                      'Likelihood threshold is user-declared; nominal coverage needs separate model/noise/regularity validation.',
                      'Worst pair is exact only over the finite admitted profile candidates; no global identifiability or action-equivalence proof.']}
