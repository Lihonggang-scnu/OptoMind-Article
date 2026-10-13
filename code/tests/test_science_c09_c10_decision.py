import itertools

import numpy as np
import pytest

from optomind_optics.harness.science.terminal_problem import TerminalDecisionProblem
from optomind_optics.harness.science.evsi import exact_evsi
from optomind_optics.harness.science.robust_voi import robust_voi


def problem(losses):
    a = np.asarray(losses)
    return TerminalDecisionProblem(tuple(f'w{i}' for i in range(a.shape[0])), tuple(f'd{i}' for i in range(a.shape[1])), a,
                                   {key: 'v1' for key in ('task', 'objective', 'model', 'sample', 'material', 'solver', 'fidelity', 'loss', 'split')}, 'task_loss')


def test_exact_evsi_and_finite_pool_counterexample():
    p = problem([[0, 100], [100, 0]])
    assert exact_evsi(p, [.5, .5], np.eye(2), ['a', 'b']).value_interval == (50, 50)
    expanded = problem([[0, 100, 1], [100, 0, 1]])
    assert exact_evsi(expanded, [.5, .5], np.eye(2), ['a', 'b']).value_interval == (1, 1)


def test_robust_precommit_and_replanning_are_distinct():
    p = problem([[0, 1, .4], [1, 0, .4]])
    joint = [[[.9, .1], [0, 0]], [[0, 0], [.1, .9]]]
    committed = robust_voi(p, joint, ['a', 'b'])
    replanned = robust_voi(p, joint, ['a', 'b'], mode='replanning')
    assert committed.posterior_risk == pytest.approx(.1)
    assert committed.value_interval == pytest.approx((.3, .3))
    assert replanned.posterior_risk == pytest.approx(.4)
    assert replanned.value_interval == pytest.approx((0, 0))
    assert committed.policy.policy_hash != replanned.policy.policy_hash
    assert committed.policy.choose('a', problem_hash=p.cache_key, policy_hash=committed.policy.policy_hash) == 'd0'
    with pytest.raises(ValueError):
        committed.policy.choose('a', problem_hash='new-context', policy_hash=committed.policy.policy_hash)


def test_milp_matches_deterministic_enumeration():
    rng = np.random.default_rng(128)
    p = problem(rng.uniform(-2, 5, (4, 3)))
    joint = rng.dirichlet(np.ones(12), size=5).reshape(5, 4, 3)
    report = robust_voi(p, joint, ['a', 'b', 'c'])
    expected = min(max(sum(joint[s, w, y] * p.losses[w, policy[y]] for w in range(4) for y in range(3)) for s in range(5))
                   for policy in itertools.product(range(3), repeat=3))
    assert report.posterior_risk == pytest.approx(expected)
    assert report.value_interval[0] == pytest.approx(report.value_interval[1])


def test_cost_unit_and_cache_identity():
    p = problem([[0, 1], [1, 0]])
    report = exact_evsi(p, [.5, .5], np.eye(2), ['a', 'b'], cost=.8, cost_units='task_loss')
    assert report.net_value_interval == pytest.approx((-.3, -.3))
    with pytest.raises(ValueError):
        exact_evsi(p, [.5, .5], np.eye(2), ['a', 'b'], cost=.8, cost_units='dollars')
    changed = TerminalDecisionProblem(p.world_ids, p.design_ids, p.losses, dict(p.context, solver='v2'), p.units)
    assert p.cache_key != changed.cache_key
    with pytest.raises(ValueError):
        p.losses[0, 0] = 5


def test_impossible_observation_and_invalid_kernel():
    p = problem([[0, 1], [1, 0]])
    r = exact_evsi(p, [.5, .5], [[1, 0], [1, 0]], ['a', 'b'])
    assert r.value_interval == pytest.approx((0, 0))
    with pytest.raises(ValueError):
        exact_evsi(p, [.5, .5], [[.9, .9], [0, 1]], ['a', 'b'])


def test_cache_roundtrip_and_tamper_rejection(tmp_path):
    import json
    from optomind_optics.harness.science.decision_cache import LossMatrixCache
    p = problem([[0, 1], [1, 0]])
    cache = LossMatrixCache(tmp_path)
    key = cache.store(p)
    assert cache.load(key).solve([.2, .8]).decision_id == 'd1'
    with pytest.raises(TypeError):
        p.context['solver'] = 'changed'
    target = tmp_path / f'{key}.json'
    value = json.loads(target.read_text())
    value['context']['solver'] = 'changed'
    target.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='changed'):
        cache.load(key)


def test_independent_policy_confirmation_receives_only_observation():
    from optomind_optics.harness.science.action_value import confirm_policy_value
    p=problem([[0,1],[1,0]])
    def sample(world,rng):
        return {'signal':world}
    def policy(observation):
        assert set(observation)=={'signal'}
        return f"d{observation['signal']}"
    r=confirm_policy_value(p,[.5,.5],sample,policy,policy_hash='a'*64,confirmation_split_id='holdout',used_split_ids=['train'],n_samples=1000,seed=123,evaluation_cost_per_sample=.01)
    assert r.estimate==pytest.approx(.5,abs=.04)
    assert r.status=='positive_net_value_confirmed'
    assert r.internal_evaluation_cost==10
    with pytest.raises(ValueError,match='fresh independent'):
        confirm_policy_value(p,[.5,.5],sample,policy,policy_hash='a'*64,confirmation_split_id='train',used_split_ids=['train'],n_samples=10,seed=123)
