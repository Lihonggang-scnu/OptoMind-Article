"""Optional, pinned upstream GEPA entry point with an explicit free proposer.

No LLM defaults, dependency download, eval cache, production write, or resume.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
from .gepa_adapter import GEPAAdapter, PINNED_COMMIT
from .learning_runner import PolicyVersion


def verify_upstream_pin():
    spec=importlib.util.find_spec('gepa')
    if spec is None or spec.origin is None: raise ImportError('optional pinned GEPA is not installed')
    origin=Path(spec.origin).resolve()
    checkout=origin.parent.parent.parent
    if (checkout/'.git').exists():
        def git(*args):
            return subprocess.check_output(['git','-C',str(checkout),*args],text=True).strip()
        if git('rev-parse','HEAD')!=PINNED_COMMIT: raise ValueError('GEPA checkout does not match mother pin')
        if git('status','--porcelain','--untracked-files=no'): raise ValueError('GEPA checkout has modified tracked source')
        return PINNED_COMMIT
    from importlib.metadata import distribution
    metadata=distribution('gepa').read_text('direct_url.json')
    data=json.loads(metadata or '{}')
    if data.get('vcs_info',{}).get('commit_id')!=PINNED_COMMIT:
        raise ValueError('GEPA install lacks pinned VCS provenance')
    return PINNED_COMMIT


def optimize_with_gepa(runner,seed,propose,*,train_ids,validation_ids,max_metric_calls,proposal_cost=0.):
    """Run the actual upstream engine; callback receives sanitized training rows."""
    verify_upstream_pin()
    from gepa import optimize
    if not train_ids or not validation_ids or max_metric_calls<4: raise ValueError('separate train/validation and sufficient bounded calls required')
    if any(runner.tasks[t].split!='T' for t in train_ids) or any(runner.tasks[t].split!='V' for t in validation_ids): raise ValueError('GEPA search only receives T and V')
    adapter=GEPAAdapter(runner)
    def charged_proposer(candidate,reflective_dataset,components_to_update,*,metadata=None):
        runner.cost.charge('gepa_proposal',proposal_cost)
        proposed=propose(candidate,reflective_dataset,components_to_update)
        if set(proposed)!={'experiment_policy'}: raise ValueError('proposal cannot mutate scientific kernel')
        return {'experiment_policy':PolicyVersion(**proposed).experiment_policy}
    result=optimize(seed_candidate={'experiment_policy':seed.experiment_policy},trainset=list(train_ids),valset=list(validation_ids),adapter=adapter,
        custom_candidate_proposer=charged_proposer,max_metric_calls=max_metric_calls,
        reflection_minibatch_size=1,skip_perfect_score=False,candidate_selection_strategy='current_best',
        cache_evaluation=False,use_merge=False,seed=0,raise_on_exception=True,
        use_wandb=False,use_mlflow=False,display_progress_bar=False)
    winner=PolicyVersion(**result.best_candidate)
    runner.freeze(winner)
    return winner,runner.manifest(),result
