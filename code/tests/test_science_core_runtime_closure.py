import numpy as np
import pytest
from optomind_optics.harness.science.compute_ledger import ComputeLedger, active_compute_ledger, ComputeBudgetExceeded
from optomind_optics.harness.science.open_research_loop import initial_planar_model
from optomind_optics.harness.science.model_compiler import compile_model
from optomind_optics.harness.science.reference_calibration import correct_reference

def test_forward_budget_failure_and_recovery(tmp_path):
    path=tmp_path/'compute.sqlite';limits={'model_predictions':3,'solver_points':8}
    ledger=ComputeLedger(path,limits);model=compile_model(initial_planar_model())
    with active_compute_ledger(ledger):
        model.predict({},[500.,600.])
        assert ledger.snapshot()['usage']=={'model_predictions':1,'solver_points':4}
        with pytest.raises(ValueError):model.predict({},[600.,500.])
        model.predict({},[500.,600.])
        with pytest.raises(ComputeBudgetExceeded):model.predict({},[500.,600.])
    assert ledger.snapshot()['events_by_status']['failed_consumed']==1
    ledger.close();ledger=ComputeLedger(path,limits)
    assert ledger.snapshot()['usage']['solver_points']==8
    ledger.close()

def test_unknown_compute_reservation_retained(tmp_path):
    path=tmp_path/'c.sqlite';ledger=ComputeLedger(path,{'model_predictions':1})
    ledger.reserve('model_predictions');ledger.close();ledger=ComputeLedger(path,{'model_predictions':1})
    assert ledger.snapshot()['unknown_reserved_events']==1
    with pytest.raises(ComputeBudgetExceeded):ledger.reserve('model_predictions')
    ledger.close()

def raw(key,group):
    return {'acquisition_id':key,'provider_receipt':{'measurement_receipt':{'wavelengths_nm':[500.,600.], 'values':[.5,.6] if key=='s' else [.1,.1], 'covariance':[[.01,0],[0,.01]],'calibration_group':group,'calibration_std':.2}}}

def test_reference_covariance_and_no_new_evidence():
    same=correct_reference(raw('s','shared'),raw('r','shared'),expected_reference=.04)
    assert np.allclose(same['values'],[.44,.54]) and np.allclose(same['covariance'],np.eye(2)*.02)
    other=correct_reference(raw('s','s'),raw('r','r'),expected_reference=.04)
    assert np.allclose(other['covariance'],np.eye(2)*.02+.08)
    assert not same['new_evidence'] and same['cross_view_covariance_required']

def test_literature_seed_changes_real_prediction_and_units_reject():
    from optomind_optics.harness.science.literature_bridge import build_research_brief
    from optomind_optics.harness.science.contracts import ResearchTask
    from optomind_research.runtime.upgrade3.article_evidence import EvidenceRequest,build_evidence_bundle
    from optomind_optics.harness.science.typed_implication_runtime import consume_implications
    unit=dict(canonical_paper_id='P0001',snapshot_id='v1',source_hash='a'*64,material_depth='full_text',roles=['model_mechanism'],anchors=[{'source_block_id':'s','quote':'Seed thickness is 150 nm.'}],conditions={'angle_deg':0},implications=[{'kind':'model_seed','target_path':'ModelCandidate.parameter_values.film.thickness_nm','value_or_expression':150.,'units':'nm'}])
    task=ResearchTask(object_id='task',source_run_id='test',question='test')
    brief=build_research_brief(build_evidence_bundle(EvidenceRequest(question='test',required_outputs=('model_seed',)),[unit]),task_ref=task.ref(),citation_namespace='run')
    parent=initial_planar_model()
    result=consume_implications(brief,model_ir=parent,program=None,task_conditions={'angle_deg':0},adopted_claim_ids=brief.claim_refs)
    assert result.receipts[0]['consumed']
    assert not np.allclose(compile_model(parent).predict({},[500.,600.]),compile_model(result.model_ir).predict({},[500.,600.]))
    altered=brief.model_dump(mode='json');altered['claims'][0]['implications'][0]['units']='deg'
    rejected=consume_implications(altered,model_ir=parent,program=None,task_conditions={'angle_deg':0},adopted_claim_ids=brief.claim_refs)
    assert not rejected.receipts[0]['consumed']

def test_compute_totals_are_transactional_and_recovery_checks_tampering(tmp_path):
    path=tmp_path/'aggregate.sqlite';ledger=ComputeLedger(path,{'model_predictions':2})
    ledger.reserve('model_predictions');ledger.close()
    import sqlite3
    db=sqlite3.connect(path)
    with db:db.execute("UPDATE totals SET quantity=0 WHERE resource='model_predictions'")
    db.close()
    with pytest.raises(ValueError,match='reconcile'):ComputeLedger(path,{'model_predictions':2})


def test_reference_corrected_statistic_drives_actual_program_branch(tmp_path):
    from types import SimpleNamespace
    from optomind_optics.harness.science.program_ir import ProgramIR,ProgramNode
    from optomind_optics.harness.science.program_compiler import compile_program
    from optomind_optics.harness.science.program_runner import ProgramRunner
    from optomind_optics.harness.science.jobs import ToolCapability,ExecutionGate
    cap=ToolCapability(effect_class='external_measurement',domain='fixture',channels=('R',),supports_status=True)
    gate=ExecutionGate(tmp_path/'jobs.sqlite',limits={'calls':2.,'cost':2.},capability=cap)
    class Provider:
        def submit(self,key,payload):
            specimen=payload['sample_id']=='s';data=raw('s' if specimen else 'r','shared')['provider_receipt']
            return {'state':'completed','cost':{'calls':1.,'cost':1.},'values':data['measurement_receipt']['values'],**data}
        def status(self,key):return {'state':'unknown_outcome'}
    nodes=(ProgramNode('reference','new_evidence',channel='R',sample_id='r',estimate=(('calls',1.),('cost',1.))),
        ProgramNode('sample','new_evidence',channel='R',sample_id='s',estimate=(('calls',1.),('cost',1.))),
        ProgramNode('corrected','derived_view',('sample','reference'),arguments=(('statistic','mean'),('calibration','reference_offset'),('expected_reference',.04))),
        ProgramNode('branch','branch',('corrected',),arguments=(('comparison','gt'),('threshold',.52))))
    program=ProgramIR('calibration',nodes,(('s','ready'),('r','ready')))
    plan=compile_program(program,capabilities={'R':cap},limits={'calls':2.,'cost':2.})
    runner=ProgramRunner(tmp_path/'program.sqlite',gates={'R':gate},providers={'R':Provider()})
    receipt=runner.run(plan,prediction_commits={'reference':{'frozen':True},'sample':{'frozen':True}})
    assert np.mean([.5,.6])>.52
    assert receipt.nodes['branch']['value'] is False
    assert receipt.nodes['corrected']['new_evidence'] is False
    assert gate.usage()['calls']==2
    runner.close();gate.close()

@pytest.mark.parametrize('estimator',['gaussian_mc','robust_gaussian_bins'])
def test_real_open_loop_value_consumer_metered_and_bound(tmp_path,estimator):
    from types import SimpleNamespace
    from dataclasses import asdict
    from optomind_optics.harness.science.open_research_cli import build_offline_loop
    from optomind_optics.harness.science.contracts import ModelCandidate
    options={'max_iterations':1,'unknown_exploration_limit':0,'value_estimator':estimator,
        'design_thicknesses_nm':[50.,90.,130.,170.,210.],'wavelengths_nm':[430.,550.,850.],
        'gaussian_value_protocol':{'proposal_samples':[16], 'confirmation_samples':[32]},
        'compute_utility_rates':{'model_predictions':1e-9,'solver_points':1e-10,'valuation_samples':1e-9,'milp_solves':1e-8},
        'compute_utility_source':'test_resource_conversion/1'}
    loop,resources=build_offline_loop(tmp_path,{'family':'F0','config':options})
    try:
        ir=initial_planar_model();parameters={'film.n':1.6,'film.thickness_nm':90.}
        candidate=loop._put(ModelCandidate(object_id='consumer:model',source_run_id=loop.config.run_id,model_ir=asdict(ir),parameter_values=parameters))
        fit=SimpleNamespace(parameters=parameters,samples=(parameters,{'film.n':2.4,'film.thickness_nm':180.}),weights=(.5,.5) if estimator=='gaussian_mc' else None,weights_semantics='test declared finite law' if estimator=='gaussian_mc' else 'unweighted scenarios')
        with active_compute_ledger(loop.compute_ledger):report=loop._value(candidate,fit,8.)
        usage=loop.compute_ledger.snapshot()['usage']
        assert usage['model_predictions']>0 and usage['solver_points']>0
        if estimator=='gaussian_mc':
            assert usage['valuation_samples']>0
            assert report['gaussian_report']['cost_vector']['precomputation_loss_cost']>0
        else:
            assert usage['milp_solves']==1
            assert report['status']=='finite_declared_joint_ambiguity'
    finally:
        for r in resources:r.close()

def test_open_loop_wires_real_smc_and_keeps_inconclusive_semantics(tmp_path):
    from optomind_optics.harness.science.open_research_cli import build_offline_loop
    loop,resources=build_offline_loop(tmp_path,{'family':'F3','config':{'max_iterations':1,'unknown_exploration_limit':0,
        'fit_starts':1,'max_nfev':1,'smc_config':{'particles':32,'max_likelihood_calls':1}}})
    try:
        with active_compute_ledger(loop.compute_ledger):
            observations,_,_=loop._acquire(loop._program('initial',loop.config.training_session),reserve=loop.config.measurement_cost)
            candidate,fit,state=loop._fit(loop.initial_model,[observations['measure']])
        assert 'tempered_smc' in fit.diagnostics['backend']
        assert fit.diagnostics['escalation_required']
        assert fit.weights is None and state.representation=='scenario_set'
        assert loop.compute_ledger.snapshot()['usage']['model_predictions']>0
    finally:
        for resource in resources:resource.close()

def test_open_loop_profile_uses_actual_constrained_optical_actions(tmp_path):
    from optomind_optics.harness.science.open_research_cli import build_offline_loop
    loop,resources=build_offline_loop(tmp_path,{'family':'F0','config':{'max_iterations':1,'unknown_exploration_limit':0,
        'fit_starts':1,'max_nfev':8,'design_thicknesses_nm':[50.,90.,130.],
        'terminal_lower_nm':60.,'profile_config':{'grid_points':3,'starts':1,'max_nfev':2,'max_residual_evaluations':50,'max_action_samples':2}}})
    try:
        with active_compute_ledger(loop.compute_ledger):
            observations,_,_=loop._acquire(loop._program('initial',loop.config.training_session),reserve=loop.config.measurement_cost)
            _,fit,_=loop._fit(loop.initial_model,[observations['measure']])
        assert fit.diagnostics['action_sensitivity']['terminal_action_calls']>0
        assert 'profile_identifiability' in fit.diagnostics
        assert loop.compute_ledger.snapshot()['usage']['solver_points']>0
    finally:
        for r in resources:r.close()
