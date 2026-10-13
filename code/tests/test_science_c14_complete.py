"""Full selected-science→authoring→Article front/back→shared publication replay."""
import hashlib
import json
import shutil
import subprocess
import pytest
from pathlib import Path
from test_science_c14_authoring import setup_selection, add_negative, NegativeSynthesis, NegativeArchitecture, NegativeWriter
from test_article_continuation import _continuation, _request
from optomind_optics.harness.article_full_structure import FullStructureProviderResult
from optomind_optics.harness.article_presentation import ProviderResult
from optomind_optics.harness.science.complete_article import complete_and_deliver_article


@pytest.mark.parametrize("compile_pdf", [False, True])
def test_complete_selected_article_reuses_author_stages_and_shared_selected_hash(tmp_path, compile_pdf):
    if compile_pdf and not shutil.which("xelatex"):
        pytest.skip("optional installed XeLaTeX engine is absent")
    if compile_pdf and (not shutil.which("kpsewhich") or subprocess.run(
            ["kpsewhich", "xelatex.fmt"], capture_output=True).returncode):
        pytest.skip("optional XeLaTeX format is unavailable; configure a local TEXFORMATS directory")
    source,_,selection,_=setup_selection(tmp_path);selection=add_negative(selection)
    continuation,_=_continuation(synthesis=NegativeSynthesis(),architecture=NegativeArchitecture(),writer=NegativeWriter())
    result=continuation.run(_request(source,tmp_path/'body',scientific_selection=selection))
    calls={'structure':0,'front':0}; seen=[]
    def structure(payloads):
        calls['structure']+=1
        assert payloads[0]['scientific_selection']['adopted_result_ref']==selection.selected_result_ref.model_dump(mode='json')
        return [FullStructureProviderResult(response={'section_order':[
            {'source_section_id':row['section_id'],'order':i,'whole_article_role':'source-bound results'}
            for i,row in enumerate(payloads[0]['sections'],1)]},provider_model='offline-fixture')]
    def front(payload):
        calls['front']+=1
        paragraphs=[p for s in payload['sections'] for p in s['paragraphs']]
        assert payload['scientific_selection']['negative_results']
        return ProviderResult(response={'title':'Source-bound fixture article','abstract_sentences':[
            {'sentence':'A bounded response is documented [fixture::P0001].','paragraph_aliases':[paragraphs[0]['paragraph_id']]}],
            'keywords':['fixture'],'conclusion_sentences':[
                {'sentence':'The mechanism remains unresolved within the reported scope.','paragraph_aliases':[paragraphs[-1]['paragraph_id']]}]},
            provider_model='offline-fixture')
    def checker(payload,number):
        seen.append(payload)
        block=next(b for b in payload['blocks'] if 'A bounded response is documented' in b['text'])
        return {'snapshot_id':payload['snapshot_id'],'changes':[{'target_block_id':block['block_id'],'operation':'replace',
            'original_text':'A bounded response is documented','replacement_text':'A scoped response is documented','reason':'Local expression edit.'}],
            'unresolved_questions':[]}
    kwargs=dict(continuation_result=result,selection=selection,
        source_identity_map={'P0001':{'canonical_paper_id':'fixture-source','title':'Declared offline source fixture',
                                    'authors':['Fixture author'],'year':2020}},citation_namespace='fixture',
        material_records=[{'material_id':'source-fixture','text':'Declared offline source fixture; bounded scope only.','source_handles':['P0001']}],
        config={'language':'en','compile_pdf':compile_pdf,'manuscript_checker':{'fixture':checker}},out_dir=tmp_path/'complete',
        structure_provider=structure,front_matter_provider=front)
    report=complete_and_deliver_article(**kwargs)
    assert '03_front_back' not in report['stages'] and '05_publication' in report['stages']
    original=report['complete_article_input']; selected=report['selected_manuscript']
    assert original['sha256']!=selected['sha256']
    text=Path(original['path']).read_text()
    assert text.startswith('# Source-bound fixture article') and '## Abstract' in text and '## Conclusion' in text
    assert 'mechanism remains unresolved' in text
    for stage in ('04_figures_citations','05_publication'):
        assert report['stages'][stage]['selected_manuscript']['sha256']==selected['sha256']
    metadata=report['stages']['05_publication']['publication_metadata']
    assert metadata['keywords']==['fixture'] and 'Keywords' not in metadata['abstract']
    published=Path(report['stages']['05_publication']['published_markdown']).read_text()
    assert '## References' in published and '参考文献' not in published
    assert 'Declared offline source fixture' in published and ('References' in published or '参考文献' in published)
    assert 'A scoped response is documented' in published
    assert seen and any(k.startswith('scientific:') for k in seen[0]['materials'])
    if compile_pdf:
        publication=report['stages']['05_publication']
        pdf=Path(publication['tex_result']['pdf_path']);tex=Path(publication['tex_result']['tex_path'])
        assert pdf.read_bytes().startswith(b'%PDF-') and pdf.stat().st_size>1000
        assert tex.is_file()
        if shutil.which('pdftotext'):
            extracted=subprocess.run(['pdftotext',str(pdf),'-'],check=True,capture_output=True,text=True).stdout
            assert 'fixture article' in extracted and 'Declared offline source fixture' in extracted
        proof={'scope':'actual publication engine only; synthetic scientific input',
               'selected_manuscript':selected,'numbering_input':report['manuscript_checker']['consumer_artifacts']['numbering_input'],
               'tex':{'path':str(tex),'sha256':hashlib.sha256(tex.read_bytes()).hexdigest()},
               'pdf':{'path':str(pdf),'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'bytes':pdf.stat().st_size},
               'scientific_acceptance':False,'visual_quality_acceptance':False}
        (tmp_path/'PUBLICATION_ENGINE_PROOF.json').write_text(json.dumps(proof,indent=2))
    replay=complete_and_deliver_article(**kwargs)
    assert replay['selected_manuscript']==selected
    assert calls=={'structure':1,'front':1}
    if not compile_pdf:
        from test_science_c14_cli import invoke, REVIEW
        from optomind_optics.harness.science.contracts import canonical_hash
        records=tmp_path/'records';records.mkdir()
        def save(name,value):
            path=records/name;path.write_text(json.dumps(value));return path
        complete=tmp_path/'complete'
        structure_record=save('structure.json',{'request_sha256':canonical_hash(json.loads((complete/'FULL_STRUCTURE_REQUEST.json').read_text())),
            'responses':json.loads((complete/'FULL_STRUCTURE_RESPONSE.json').read_text())})
        front_record=save('front.json',{'request_sha256':canonical_hash(json.loads((complete/'FRONT_MATTER_REQUEST.json').read_text())),
            'response':json.loads((complete/'FRONT_MATTER_RESPONSE.json').read_text())})
        selection_file=save('selection.json',selection.model_dump(mode='json'))
        source_file=save('sources.json',kwargs['source_identity_map']);materials_file=save('materials.json',kwargs['material_records'])
        checker_file=complete/'delivery/03_manuscript_checker/round_1/RAW_RESPONSE.json'
        config_file=save('config.json',{'language':'en','compile_pdf':False,'manuscript_checker':{'fixture':str(checker_file)}})
        args=['--continuation-result',tmp_path/'body/FINAL_CONTINUATION_RESULT.json','--scientific-selection',selection_file,
            '--structure-response',structure_record,'--front-matter-response',front_record,'--citation-namespace','fixture',
            '--source-identity-map',source_file,'--material-records',materials_file,'--config',config_file,
            '--out-dir',tmp_path/'cli-complete','--review-root',REVIEW,'--replay']
        cli=invoke('run_article_scientific_delivery.py',*args)
        assert cli.returncode==0,(cli.stdout,cli.stderr)
        cli_result=json.loads(cli.stdout)
        assert cli_result['selected_manuscript']['sha256']==selected['sha256']
        resumed=invoke('run_article_scientific_delivery.py',*args,'--resume')
        assert resumed.returncode==0,(resumed.stdout,resumed.stderr)
        assert json.loads(resumed.stdout)['selected_manuscript']['sha256']==selected['sha256']
