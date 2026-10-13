from dataclasses import replace
import json
import numpy as np
import pytest
from optomind_eval.factorial_protocol import *
from optomind_eval.factorial_statistics import report_factorial
from optomind_eval.factorial_runner import independent_task_scores


def protocol():
    parity=BaselineParityManifest(8.,('R',),('cauchy','affine'),(45.,100.,230.),('same model','same evidence'),
        (('classical','executed_factorial','actual production typed search'),('free_agent','pending_unavailable','paid resources not authorized')))
    return FactorialProtocol('fixture',('t0','t1','t2'),('A','A','A'),parity,(('code','a'*64),),bootstrap_replicates=500)


def make_rows(p):
    losses={'M0E0':.5,'M1E0':.3,'M0E1':.4,'M1E1':.1}
    return tuple(FactorialResult(p.seal,task,arm,*FACTOR_FLAGS[arm],losses[arm],.01,False,None,
                 2.,.0002,'completed',('R',),'a'*64,oracle_terminal_loss=.05) for task in p.task_ids for arm in FACTORIAL_ARMS)


def test_hand_computed_pairs_interaction_tail_and_missing_metrics(tmp_path):
    p=protocol();rows=make_rows(p);path=tmp_path/'results.json';seal_factorial_results(p,rows,path)
    r=report_factorial(path)
    assert r['comparisons']['M1E1 - M0E0']['mean']==pytest.approx(-.4)
    assert r['interaction']['mean']==pytest.approx(-.1)
    assert r['interaction']['interval']==pytest.approx([-.1,-.1])
    assert r['arms']['M0E0']['total_loss']['cvar_upper_tail']==pytest.approx(.5002)
    assert r['arms']['M0E0']['metrics']['memory_bytes']['unavailable_count']==3
    assert r['arms']['M0E0']['indicators']['constraint_violation']['rate'] is None
    assert r['arms']['M0E0']['metrics']['same_pool_oracle_regret']['mean']==pytest.approx(.45)


def test_failures_abstentions_retain_actual_cost(tmp_path):
    p=protocol();rows=list(make_rows(p));idx=next(i for i,r in enumerate(rows) if r.task_id=='t0' and r.arm=='M1E1')
    rows[idx]=replace(rows[idx],status='failed',terminal_loss=None,resource_cost=8.,declared_loss_cost=.0008)
    path=tmp_path/'results.json';seal_factorial_results(p,rows,path);r=report_factorial(path)
    assert r['arms']['M1E1']['status_counts']['failed']==1
    assert r['arms']['M1E1']['total_loss']['mean']==pytest.approx((1.0008+.1002+.1002)/3)
    assert r['arms']['M1E1']['metrics']['resource_cost']['mean']==4


def test_seal_mutation_parity_flags_and_missing_task_rejected(tmp_path):
    p=protocol();rows=make_rows(p);path=tmp_path/'results.json';seal_factorial_results(p,rows,path)
    data=json.loads(path.read_text());data['rows'][0]['terminal_loss']=0;path.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='modified'):report_factorial(path)
    with pytest.raises(ValueError,match='all independent'):seal_factorial_results(p,rows[:-1],tmp_path/'bad')
    with pytest.raises(ValueError,match='parity'):seal_factorial_results(p,(replace(rows[0],observation_permissions=('R','T')),)+rows[1:],tmp_path/'bad')
    with pytest.raises(ValueError,match='factor flags'):seal_factorial_results(p,(replace(rows[0],model_expansion_enabled=True),)+rows[1:],tmp_path/'bad')
    with pytest.raises(ValueError,match='cost conversion'):seal_factorial_results(p,(replace(rows[0],declared_loss_cost=.1),)+rows[1:],tmp_path/'bad')


def test_offline_forbids_paid_and_nonfinite_metrics(tmp_path):
    p=protocol();rows=make_rows(p)
    with pytest.raises(ValueError,match='paid'):seal_factorial_results(p,(replace(rows[0],paid_calls=1),)+rows[1:],tmp_path/'bad')
    with pytest.raises(ValueError,match='finite'):seal_factorial_results(p,(replace(rows[0],prediction_loss=float('nan')),)+rows[1:],tmp_path/'bad')
    with pytest.raises(ValueError,match='wavelengths'):replace(p,statistical_unit='wavelength')


def test_independent_same_pool_oracle_bound():
    pool=np.linspace(45.,230.,75)
    terminal,oracle,_,_=independent_task_scores('F3',8262001,pool[15],pool)
    assert 0<=oracle<=terminal<=1
    assert independent_task_scores('F6',8262001,pool[15],pool)[0]==pytest.approx(independent_task_scores('F6',8262001,pool[40],pool)[0])


def test_missing_live_baseline_is_not_presented_as_executed(tmp_path):
    p=protocol();path=tmp_path/'results.json';seal_factorial_results(p,make_rows(p),path)
    report=report_factorial(path)
    entry=next(row for row in report['baseline_parity_manifest']['baseline_registry'] if row[0]=='free_agent')
    assert entry[1]=='pending_unavailable'
    assert report['claims']['llm_attribution'].startswith('offline classical')


def test_independent_byrnes_reproduces_airy_terminal_oracle():
    from optomind_eval.factorial_audit import _byrnes_loss
    pool=np.linspace(45.,230.,75)
    for family in ('F0','F3','F4','F5','F6'):
        terminal,oracle,_,_=independent_task_scores(family,8262001,pool[15],pool)
        assert _byrnes_loss(family,8262001,pool[15])==pytest.approx(terminal,abs=1e-12)
        assert min(_byrnes_loss(family,8262001,d) for d in pool)==pytest.approx(oracle,abs=1e-12)


def test_physical_runner_rejects_silent_objective_or_channel_change():
    from optomind_eval.factorial_runner import validate_factorial_plan
    plan={'version':'c17-full-scope-offline-v1','fixture':'single_film','families':['F0'],
          'channels':['R'],'arms':{a:list(f) for a,f in FACTOR_FLAGS.items()},
          'common_config':{'objective_wavelength_nm':550.,'objective_target':.1,'measurement_std':.002,'calibration_std':.001}}
    validate_factorial_plan(plan)
    plan['common_config']['objective_target']=.2
    with pytest.raises(ValueError,match='new independent-score'):validate_factorial_plan(plan)
