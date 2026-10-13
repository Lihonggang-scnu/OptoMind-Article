"""Offline regression for adopted limitation protection across actual delivery."""
from test_science_c14_authoring import setup_selection,add_negative,NegativeSynthesis,NegativeArchitecture,NegativeWriter
from test_article_continuation import _continuation,_request
from optomind_optics.harness.article_full_structure import FullStructureProviderResult
from optomind_optics.harness.article_presentation import ProviderResult
from optomind_optics.harness.science.complete_article import complete_and_deliver_article
from pathlib import Path

def test_checker_preserves_unknown_body_and_conclusion_with_independent_edits(tmp_path):
    source,_,selection,_=setup_selection(tmp_path);selection=add_negative(selection)
    continuation,_=_continuation(synthesis=NegativeSynthesis(),architecture=NegativeArchitecture(),writer=NegativeWriter())
    result=continuation.run(_request(source,tmp_path/'body',scientific_selection=selection))
    calls={'structure':0,'front':0,'checker':0}
    def structure(payloads):
        calls['structure']+=1
        return [FullStructureProviderResult(response={'section_order':[{'source_section_id':s['section_id'],'order':i,'whole_article_role':'results'} for i,s in enumerate(payloads[0]['sections'],1)]},provider_model='offline')]
    def front(payload):
        calls['front']+=1
        para=payload['sections'][0]['paragraphs'][0]['paragraph_id']
        negative=payload['sections'][-1]['paragraphs'][-1]['paragraph_id']
        return ProviderResult(response={'title':'Source-bound fixture','abstract_sentences':[{'sentence':'A response is documented.','paragraph_aliases':[para]}],'conclusion_sentences':[{'sentence':'The mechanism remains unresolved.','paragraph_aliases':[negative]}]},provider_model='offline')
    def checker(payload,number):
        calls['checker']+=1
        changes=[{'target_block_id':b['block_id'],'operation':'replace','original_text':'The mechanism remains unresolved within this limited scope.','replacement_text':'The mechanism is established within this limited scope.','reason':'Improve expression.'} for b in payload['blocks'] if 'The mechanism remains unresolved within this limited scope.' in b['text']]
        for b in payload['blocks']:
            if 'The mechanism remains unresolved.' in b['text']:
                changes.append({'target_block_id':b['block_id'],'operation':'replace','original_text':'The mechanism remains unresolved.','replacement_text':'The mechanism is established.','reason':'Improve expression.'})
            if 'A response is documented.' in b['text']:
                changes.append({'target_block_id':b['block_id'],'operation':'replace','original_text':'A response is documented.','replacement_text':'A bounded response is documented.','reason':'Improve expression.'})
        return {'snapshot_id':payload['snapshot_id'],'changes':changes,'unresolved_questions':[]}
    kwargs=dict(continuation_result=result,selection=selection,source_identity_map={},citation_namespace='fixture',material_records=[],config={'language':'en','compile_pdf':False,'manuscript_checker':{'fixture':checker,'max_model_rounds':1}},out_dir=tmp_path/'complete',structure_provider=structure,front_matter_provider=front)
    report=complete_and_deliver_article(**kwargs)
    assert '05_publication' in report['stages']
    published=Path(report['stages']['05_publication']['published_markdown']).read_text()
    assert 'mechanism is established' not in published
    assert 'The mechanism remains unresolved within this limited scope.' in published
    assert 'The mechanism remains unresolved.' in published
    assert 'A bounded response is documented.' in published
    assert any(p['code']=='scientific_negative_unknown_span_protected' for p in report['manuscript_checker']['pending'])
    assert report['selected_manuscript']['sha256'] != report['complete_article_input']['sha256']
    for stage in ('04_figures_citations','05_publication'):
        assert report['stages'][stage]['selected_manuscript']==report['selected_manuscript']

    replay=complete_and_deliver_article(**kwargs)
    assert replay['selected_manuscript']==report['selected_manuscript']
    assert calls=={'structure':1,'front':1,'checker':1}


def test_checker_rejects_insertions_and_empty_anchors_before_span_guard():
    from optomind_research.runtime.upgrade3.manuscript_checker import _material_case, execute_checker_response
    text='The mechanism remains unresolved.'
    case=_material_case(text, [])
    for operation, original, code in [('insert_after',text,'operation_not_allowed'),
                                      ('replace','','missing_original_text')]:
        response={'snapshot_id':case['base_sha256'],'changes':[{
            'target_block_id':case['blocks'][0]['block_id'],'operation':operation,
            'original_text':original,'replacement_text':'Clearly established.',
            'reason':'Attempt an insertion.'}],'unresolved_questions':[]}
        result=execute_checker_response(draft_text=text,response=response,
            protected_paragraphs=[{'text':text,'minimum_occurrences':1}])
        assert result['draft_text']==text
        assert result['pending'][0]['code']==code
