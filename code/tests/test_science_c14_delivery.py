import hashlib
from pathlib import Path
import pytest
from optomind_optics.harness.science.contracts import ModelCandidate, SelectedResearchResult, ResearchTask
from optomind_optics.harness.science.article_bridge import build_result_bundle, ResultValue, load_result_bundle
from optomind_optics.harness.article_continuation import ArticleContinuation
from optomind_optics.harness.science.repository import ScienceRepository


def selected():
    model=ModelCandidate(object_id='model',source_run_id='fixture',model_ir={})
    return SelectedResearchResult(object_id='selected',source_run_id='fixture',candidate_ref=model.ref()),model


def test_repository_selected_authority(tmp_path):
    selected_result, model=selected(); task=ResearchTask(object_id='t',source_run_id='fixture',question='test')
    repo=ScienceRepository(tmp_path)
    repo.commit([task,model,selected_result],task_ref=task.ref(),evidence_cutoff='fixture')
    assert load_result_bundle(repo,selected_result.ref()).selected_result==selected_result
    with pytest.raises(TypeError): load_result_bundle(repo,model.ref())
    repo.close()


def test_cross_candidate_and_negative_omission_rejected():
    result,model=selected()
    other=ModelCandidate(object_id='other',source_run_id='fixture',model_ir={})
    value=ResultValue(value_id='v',candidate_ref=other.ref(),source_ref=model.ref(),value=1,units='nm',data_kind='simulation')
    with pytest.raises(ValueError,match='cross-contamination'): build_result_bundle(result,trusted_values=[value])
    payload=result.model_dump(); payload.update(content_hash='',negative_result_refs=[model.ref()])
    with pytest.raises(ValueError,match='negative results'): build_result_bundle(SelectedResearchResult.model_validate(payload))


def test_actual_checker_numbering_publication_consumes_new_hash(tmp_path):
    result,_=selected(); path=tmp_path/'complete.md'
    path.write_text('# Fixture title\n\n## Abstract\n\nAlpha prose.\n\n## Conclusion\n\nBounded fixture only.\n')
    original=hashlib.sha256(path.read_bytes()).hexdigest()
    def fixture(payload,number):
        block=next(x for x in payload['blocks'] if 'Alpha prose.' in x['text'])
        return {'snapshot_id':payload['snapshot_id'],'changes':[{'target_block_id':block['block_id'],'operation':'replace','original_text':'Alpha prose.','replacement_text':'Clear prose.','reason':'Local expression correction.'}],'unresolved_questions':[]}
    report=ArticleContinuation().deliver_complete_manuscript(result_bundle=build_result_bundle(result),manuscript_path=path,
        manuscript_sha256=original,source_identity_map={},citation_namespace='fixture',material_records=[],
        config={'language':'en','compile_pdf':False,'manuscript_checker':{'fixture':fixture}},out_dir=tmp_path/'delivery')
    assert '03_front_back' not in report['stages']
    chosen=report['selected_manuscript']
    assert chosen['sha256']!=original and 'Clear prose.' in Path(chosen['path']).read_text()
    for stage in ['04_figures_citations','05_publication']:
        assert report['stages'][stage]['selected_manuscript']['sha256']==chosen['sha256']
        assert report['stages'][stage]['adopted_result_ref']==result.ref().model_dump(mode='json')
    assert report['front_back_generated'] is False
    assert report['manuscript_checker']['adopted_result_ref']==result.ref().model_dump(mode='json')


def test_unknown_citations_block_real_publication(tmp_path):
    result,_=selected(); path=tmp_path/'complete.md'; path.write_text('A claim [P9999].\n')
    report=ArticleContinuation().deliver_complete_manuscript(result_bundle=build_result_bundle(result),manuscript_path=path,
        manuscript_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_identity_map={},citation_namespace='fixture',material_records=[],
        config={'language':'en','compile_pdf':False,'manuscript_checker':{'fixture':lambda payload,number:{'snapshot_id':payload['snapshot_id'],'changes':[],'unresolved_questions':[]}}},out_dir=tmp_path/'delivery')
    assert '05_publication' not in report['stages']
    assert report['stages']['04_figures_citations']['unknown_citation_tokens']


def test_body_and_hash_integrity_rejected(tmp_path):
    result,_=selected(); path=tmp_path/'complete.md'; path.write_text('Fixture.\n')
    kwargs=dict(result_bundle=build_result_bundle(result),manuscript_path=path,manuscript_sha256='0'*64,
        source_identity_map={},citation_namespace='fixture',material_records=[],config={},out_dir=tmp_path/'delivery')
    with pytest.raises(ValueError,match='hash mismatch'): ArticleContinuation().deliver_complete_manuscript(**kwargs)
    with pytest.raises(ValueError,match='BODY'): ArticleContinuation().deliver_complete_manuscript(**kwargs,manuscript_kind='body')


def test_repository_delivery_feeds_scientific_materials_to_shared_checker(tmp_path):
    result,model=selected(); task=ResearchTask(object_id='t',source_run_id='fixture',question='test')
    repo=ScienceRepository(tmp_path/'science');repo.commit([task,model,result],task_ref=task.ref(),evidence_cutoff='fixture')
    path=tmp_path/'complete.md';path.write_text('A bounded fixture.\n'); seen=[]
    def checker(payload,number):
        seen.append(payload)
        return {'snapshot_id':payload['snapshot_id'],'changes':[],'unresolved_questions':[]}
    report=ArticleContinuation().deliver_complete_manuscript(repository=repo,selected_result_ref=result.ref(),manuscript_path=path,
        manuscript_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_identity_map={},citation_namespace='fixture',material_records=[],
        config={'language':'en','compile_pdf':False,'manuscript_checker':{'fixture':checker}},out_dir=tmp_path/'delivery')
    assert '05_publication' in report['stages']
    assert seen and any(k.startswith('scientific:selected:') for k in seen[0]['materials'])
    assert any(k.startswith('scientific:model:') for k in seen[0]['materials'])
    repo.close()
