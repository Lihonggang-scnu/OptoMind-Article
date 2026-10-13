import pytest
from optomind_research.runtime.upgrade3.article_evidence import EvidenceRequest, build_evidence_bundle
from optomind_optics.harness.science.contracts import ResearchTask, ModelCandidate, ExperimentProgram
from optomind_optics.harness.science.literature_bridge import build_research_brief, adopt_evidence_delta
from optomind_optics.harness.science.claim_compiler import compile_claim_implication, apply_research_brief


def fixture(depth='full_text', **changes):
    request = EvidenceRequest(question='bounded source fixture', required_outputs=('constraint',))
    unit = dict(canonical_paper_id='P0001', snapshot_id='version1', source_hash='a'*64,
        material_depth=depth, roles=['problem_boundary'], anchors=[{'source_block_id':'source-1','quote':'Thickness below 20 nm at normal incidence.'}],
        conditions={'angle':0}, implications=[{'kind':'constraint','target_path':'ResearchTask.constraints.thickness_max_nm','value_or_expression':20,'units':'nm'}])
    unit.update(changes)
    return build_evidence_bundle(request, [unit])


def task():
    return ResearchTask(object_id='task',source_run_id='test',question='fixture')


def test_identity_versions_and_namespaced_fallback():
    t=task()
    first=build_research_brief(fixture(doi='10.1/a'),task_ref=t.ref(),citation_namespace='run1')
    second=build_research_brief(fixture(doi='10.1/a',snapshot_id='version2'),task_ref=t.ref(),citation_namespace='run2')
    assert first.claims[0].document.work == second.claims[0].document.work
    assert first.claims[0].document != second.claims[0].document
    a=build_research_brief(fixture(),task_ref=t.ref(),citation_namespace='run1')
    b=build_research_brief(fixture(),task_ref=t.ref(),citation_namespace='run2')
    assert a.claims[0].document.work != b.claims[0].document.work


def test_real_task_consumer_and_abstract_restriction():
    t=task(); brief=build_research_brief(fixture(),task_ref=t.ref(),citation_namespace='run')
    new, proof=apply_research_brief(brief,t,task_conditions={'angle':0},adopted_claim_ids=brief.claim_refs)
    assert new.constraints['thickness_max_nm']==20 and new.revision==2 and proof
    assert t.constraints=={}
    abstract=build_research_brief(fixture('abstract_only'),task_ref=t.ref(),citation_namespace='run')
    unchanged, proof=apply_research_brief(abstract,t,task_conditions={'angle':0},adopted_claim_ids=abstract.claim_refs)
    assert unchanged==t and not proof
    c=brief.claims[0]
    assert compile_claim_implication(c,c.implications[0],task_conditions={'angle':45}).status=='inapplicable'
    assert compile_claim_implication(c,{'kind':'model_seed','target_path':'ModelCandidate.parameter_values.prior_distribution','value_or_expression':[0,1],'units':'1'},task_conditions={'angle':0}).status=='uncompilable'
    with pytest.raises(ValueError,match='explicit adoption'):
        adopt_evidence_delta(brief,fixture(),task_ref=t.ref(),citation_namespace='run',adopted=False)


@pytest.mark.parametrize('kind,path,target',[
    ('model_seed','ModelCandidate.parameter_values.thickness',ModelCandidate(object_id='m',source_run_id='test',model_ir={})),
    ('probe','ExperimentProgram.protocol.angle',ExperimentProgram(object_id='p',source_run_id='test',protocol={}))])
def test_actual_model_program_consumers(kind,path,target):
    t=task(); b=build_research_brief(fixture(implications=[{'kind':kind,'target_path':path,'value_or_expression':20,'units':'nm'}]),task_ref=t.ref(),citation_namespace='run')
    new, proof=apply_research_brief(b,target,task_conditions={'angle':0},adopted_claim_ids=b.claim_refs)
    assert new.model_dump()[path.split('.')[1]][path.split('.')[2]]==20 and proof


def test_strategy_consumer_retains_brief():
    from optomind_optics.harness.strategy_planner import _compact_method_research
    brief=build_research_brief(fixture(),task_ref=task().ref(),citation_namespace='run')
    assert _compact_method_research({'research_brief':brief.model_dump(mode='json')})['research_brief']['read_receipts']


def test_method_entry_compiles_bundle_and_preserves_ordinary_report(monkeypatch):
    from optomind_optics.harness.method_research import TMMMethodResearchAdapter, MethodResearchReport, research_tmm_methods
    monkeypatch.setattr(TMMMethodResearchAdapter,'research',lambda self,*args:MethodResearchReport(problem_id='p'))
    ordinary=research_tmm_methods({'problem_id':'p'},online=False)
    compiled=research_tmm_methods({'problem_id':'p'},online=False,evidence_bundle=fixture(),research_task_ref=task().ref(),citation_namespace='test')
    assert ordinary.research_brief is None
    assert "research_brief" not in ordinary.model_dump(mode="json")
    assert compiled.research_brief.claim_refs and compiled.problem_id==ordinary.problem_id
