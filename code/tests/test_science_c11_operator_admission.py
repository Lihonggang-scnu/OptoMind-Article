import numpy as np
import pytest
from test_science_c06_model_compiler import film_model
from optomind_optics.harness.science.operator_admission import OperatorReferenceCase as Case,OperatorReferenceProtocol as Protocol,OperatorAdmissionService
from optomind_optics.harness.science.model_compiler import compile_model
from optomind_optics.harness.science.compute_ledger import ComputeLedger,active_compute_ledger


def protocol():
    # Analytic independently specified saturation law and its exact derivative.
    return Protocol('saturation/1',('analytic-detector-reference',),'a'*64,(400.,900.),(0.,1.),
        (Case((.2,.8),(500.,600.),(1/6,4/9),1e-12),),
        (Case((0.,1.),(500.,600.),(0.,.5),1e-12),),
        (Case((.2,.8),(500.,600.),(1/1.2**2,1/1.8**2),1e-8),),
        (Case((.4,.6),(500.,600.),(2/7,3/8),1e-12),))


def test_host_fixed_checks_admit_and_execute_actual_operator(tmp_path):
    service=OperatorAdmissionService((protocol(),))
    request=dict(expression='x/(1+x)',reference_protocol_id='saturation/1',expected_difference='saturation',task_impact='terminal response')
    ledger=ComputeLedger(tmp_path/'compute.sqlite',{'model_predictions':30,'solver_points':30})
    try:
        with active_compute_ledger(ledger):
            proposal,receipt=service.admit(request,film_model())
            x=compile_model(film_model()).predict({},[500.,600.])
            actual=compile_model(proposal.model_ir).predict({},[500.,600.])
        assert np.allclose(actual,x/(1+x))
        assert {r['kind'] for r in receipt['numerical_results']}=={'reference_cases','limit_cases','gradient_cases','refinement_cases'}
        assert ledger.snapshot()['usage']['model_predictions']>=9
        with pytest.raises(ValueError):service.admit({**request,'expected_answers':[0]},film_model())
        with pytest.raises(ValueError,match='check failed'):service.admit({**request,'expression':'x'},film_model())
    finally:ledger.close()

def test_algebraic_dedup_does_not_collapse_nested_families_or_unsafe_division():
    from optomind_optics.harness.science.model_equivalence import algebraic_expression_key
    assert algebraic_expression_key('x/(1+x)')==algebraic_expression_key('x/(x+1)')
    assert algebraic_expression_key('x/x')!=algebraic_expression_key('1')
    assert algebraic_expression_key('(x+1)+2')!=algebraic_expression_key('x+(1+2)')

def test_real_replay_loop_admits_fits_and_freshly_predicts_operator(tmp_path):
    from optomind_optics.harness.science.open_research_cli import build_offline_loop
    service=OperatorAdmissionService((protocol(),))
    request=dict(expression='x/(1+x)',reference_protocol_id='saturation/1',expected_difference='bounded saturation',task_impact='may change terminal response')
    loop,resources=build_offline_loop(tmp_path,{'family':'F3','config':{'max_iterations':1,'unknown_exploration_limit':1,'fit_starts':2,'max_nfev':40,'max_candidates':2},'proposal_replay':[{'models':[],'operators':[request]}]},operator_admission=service)
    try:
        result=loop.run(tmp_path)
        admission=loop._find('offline-open-research:iteration:0:operator-admission')
        assert admission.summary['receipts'][0]['status']=='tentative_executable'
        proposals=loop._find('offline-open-research:iteration:0:proposals')
        assert any(p['origin']=='restricted_new_expression' for p in proposals.summary['proposals'])
        candidates=[loop.repository.load(ref) for ref in proposals.input_refs]
        assert any(any(n['operator']=='expression' for n in c.model_ir['nodes']) for c in candidates)
        assert result.ref().content_hash
    finally:
        for resource in resources:resource.close()


def test_inherited_operator_cannot_widen_registered_domains():
    from dataclasses import asdict,replace
    from optomind_optics.harness.science.joint_proposer import parse_joint_proposal
    service=OperatorAdmissionService((protocol(),))
    request=dict(expression='x/(1+x)',reference_protocol_id='saturation/1',expected_difference='saturation',task_impact='response')
    proposal,_=service.admit(request,film_model());parent=proposal.model_ir
    context={'max_candidates':2,'parent_model_ir':asdict(parent),'source_refs':(), 'program_id':'x'}
    node=parent.nodes[-1]
    changed=replace(node,attributes=tuple((k,100. if k=='input_upper' else v) for k,v in node.attributes))
    for ir in (replace(parent,nodes=parent.nodes[:-1]+(changed,)),replace(parent,domain_nm=(200.,2000.))):
        with pytest.raises(ValueError,match='contract|widened'):
            parse_joint_proposal({'models':[{'model_ir':asdict(ir),'edit':['change'],'expected_difference':'x','task_impact':'x'}]},context)
    with pytest.raises(ValueError,match='conflicting'):
        service.registry.register_expression('x/(1+x)',source_refs=('other-protocol',),domain=(400.,900.),input_domain=(0.,1.),reference_cases=[((0.,),(500.,),(0.,),0.)])


def test_model_algebraic_key_normalizes_expression_registry_identity():
    from optomind_optics.harness.science.model_equivalence import algebraic_model_key
    service=OperatorAdmissionService((protocol(),))
    base=dict(reference_protocol_id='saturation/1',expected_difference='saturation',task_impact='response')
    first,_=service.admit({**base,'expression':'x/(1+x)'},film_model())
    second,_=service.admit({**base,'expression':'x/(x+1)'},film_model())
    assert first.model_ir.structural_hash!=second.model_ir.structural_hash
    assert algebraic_model_key(first.model_ir)==algebraic_model_key(second.model_ir)


def test_admitted_but_inapplicable_operator_is_rejected_without_losing_loop(tmp_path):
    from dataclasses import replace
    from optomind_optics.harness.science.open_research_cli import build_offline_loop
    tiny=replace(protocol(),input_domain=(0.,.001),
        reference_cases=(Case((0.,.0005),(500.,600.),(0.,.0005/1.0005),1e-12),),
        limit_cases=(Case((0.,.001),(500.,600.),(0.,.001/1.001),1e-12),),
        gradient_cases=(Case((.0002,.0008),(500.,600.),(1/1.0002**2,1/1.0008**2),1e-8),),
        refinement_cases=(Case((.0004,.0006),(500.,600.),(.0004/1.0004,.0006/1.0006),1e-12),))
    service=OperatorAdmissionService((tiny,))
    request=dict(expression='x/(1+x)',reference_protocol_id='saturation/1',expected_difference='saturation',task_impact='response')
    loop,resources=build_offline_loop(tmp_path,{'family':'F3','config':{'max_iterations':1,'unknown_exploration_limit':1,'fit_starts':2,'max_nfev':40,'max_candidates':2},'proposal_replay':[{'models':[],'operators':[request]}]},operator_admission=service)
    try:
        result=loop.run(tmp_path)
        objects=[loop.repository.load(ref) for ref in loop.repository.latest_snapshot().object_refs]
        assert any(getattr(o,'summary',{}).get('status')=='unfittable_on_declared_domain' for o in objects)
        assert loop.compute_ledger.snapshot()['events_by_status'].get('failed_consumed',0)>0
        assert result.ref().content_hash
    finally:
        for resource in resources:resource.close()
