"""Canonical numerical research through actual author consumers and shared delivery."""
from pathlib import Path
import os
import json
import hashlib
import subprocess
from optomind_optics.harness.science.repository import ScienceRepository
from test_science_authoring_source import numerical_source, ScientificReplayClient
from test_article_continuation import _continuation, _request
from optomind_optics.harness.article_result_synthesis import QwenArticleResultClaimSynthesizer
from optomind_optics.harness.article_architecture import QwenArticleArchitecturePlanner
from optomind_optics.harness.article_writing import QwenSectionWriter
from optomind_optics.harness.article_full_structure import FullStructureProviderResult
from optomind_optics.harness.article_presentation import ProviderResult
from optomind_optics.harness.science.complete_article import complete_and_deliver_article


class SourceClaimReplayClient(ScientificReplayClient):
    """Replay prose is derived from current payload evidence, never canned answers."""
    def call(self,messages,**kwargs):
        result=super().call(messages,**kwargs)
        if self.stage=='writing':
            payload=json.loads(messages[1]['content'])
            response=json.loads(result['content'])
            canonical={c['statement']:c for c in payload['scientific_selection']['scientific_claims']}
            for paragraph,claim in zip(response['paragraphs'],payload['claims']):
                source=canonical[claim['statement']]
                conditions=[key.replace('_',' ') for key,value in source['scope'].items() if value is True]
                conditions += [key.replace('_',' ')+': '+value for key,value in source['scope'].items()
                               if isinstance(value,str) and value=='not established']
                paragraph['text_with_value_tokens']=source['statement'] + (' Scope: '+ '; '.join(conditions)+'.' if conditions else '')
            result['content']=json.dumps(response)
        return result


def test_numerical_selected_result_enters_complete_shared_delivery(numerical_source,tmp_path):
    root,selection,selected=numerical_source
    seen={stage:[] for stage in ('synthesis','architecture','writing')}
    continuation,_=_continuation(
        synthesis=QwenArticleResultClaimSynthesizer(client=ScientificReplayClient('synthesis',seen)),
        architecture=QwenArticleArchitecturePlanner(client=ScientificReplayClient('architecture',seen)),
        writer=QwenSectionWriter(client=SourceClaimReplayClient('writing',seen)))
    result=continuation.run(_request(root/'source',tmp_path/'body',source_kind='scientific_result',scientific_selection=selection))
    assert len(result.receipts)==5,(result.errors,result.warnings)
    def structure(payloads):
        assert payloads[0]['scientific_selection']['adopted_result_ref']==selected.ref().model_dump(mode='json')
        return [FullStructureProviderResult(response={'section_order':[
            {'source_section_id':s['section_id'],'order':i,'whole_article_role':'source-bound numerical results'}
            for i,s in enumerate(payloads[0]['sections'],1)]},provider_model='offline-replay')]
    def front(payload):
        paragraphs=[p for s in payload['sections'] for p in s['paragraphs']]
        return ProviderResult(response={'title':'Bounded numerical multilayer research',
            'abstract_sentences':[{'sentence':'The adopted numerical result is reported within its recorded model scope.',
                                   'paragraph_aliases':[paragraphs[0]['paragraph_id']]}],
            'keywords':['multilayer','numerical'],
            'conclusion_sentences':[{'sentence':'The recorded scope and limitations bound interpretation.',
                                    'paragraph_aliases':[paragraphs[-1]['paragraph_id']]}]},provider_model='offline-replay')
    checked=[]
    def checker(payload,number):
        checked.append(payload)
        return {'snapshot_id':payload['snapshot_id'],'changes':[],'unresolved_questions':[]}
    report=complete_and_deliver_article(continuation_result=result,selection=selection,
        source_identity_map={},citation_namespace='numerical-selected',material_records=[],
        config={'language':'en','compile_pdf':os.environ.get('OPTOMIND_C14_NUMERICAL_PDF')=='1','manuscript_checker':{'fixture':checker}},
        out_dir=tmp_path/'complete',structure_provider=structure,front_matter_provider=front)
    assert checked and any(k.startswith('scientific:') for k in checked[0]['materials'])
    assert '03_front_back' not in report['stages']
    final=report['selected_manuscript']
    for stage in ('04_figures_citations','05_publication'):
        assert report['stages'][stage]['selected_manuscript']['sha256']==final['sha256']
    assert all(r['adopted_result_ref']==selected.ref().model_dump(mode='json') for r in report['author_chain_receipts'])
    published=Path(report['stages']['05_publication']['published_markdown']).read_text()
    canonical_claims=result.stage_payloads.result_synthesis.scientific_selection['scientific_claims']
    assert all(c['statement'] in published for c in canonical_claims)
    repo=ScienceRepository(selection.repository_root,read_only=True)
    try:
        candidate=repo.load(selected.candidate_ref)
    finally:
        repo.close()
    qualified_values=[f"{candidate.parameter_values[p['name']]:g} {p['unit']}" for p in candidate.model_ir['parameters']
                      if p['name'] in candidate.parameter_values]
    visible_values=[value for value in qualified_values if value in published]
    assert visible_values, 'a concrete adopted scalar with source units must survive the writer and checker'
    assert 'model conditional' in published and 'manufactured design validation' not in published

    assert {c.metadata['scientific_claim_ref']['object_id'] for c in result.stage_payloads.result_synthesis.ledger.claims}=={r.object_id for r in selected.claim_refs}
    canonical_scopes={c['object_id']:c['scope'] for c in result.stage_payloads.result_synthesis.scientific_selection['scientific_claims']}
    assert all(c.metadata['scientific_scope']==canonical_scopes[c.metadata['scientific_claim_ref']['object_id']]
               for c in result.stage_payloads.result_synthesis.ledger.claims)

    if os.environ.get('OPTOMIND_C14_NUMERICAL_PDF')=='1':
        publication=report['stages']['05_publication']
        tex=Path(publication['tex_result']['tex_path'])
        pdf=Path(publication['tex_result']['pdf_path'])
        assert pdf.read_bytes().startswith(b'%PDF-') and pdf.stat().st_size>1000
        extracted=' '.join(subprocess.run(['pdftotext',str(pdf),'-'],check=True,capture_output=True,text=True).stdout.split())
        assert all(value in extracted for value in visible_values)
        assert 'model conditional' in extracted
        proof={'visible_source_values':visible_values,'scope':'offline numerical research and replayed authors through actual publication engine',
               'selected_result_ref':selected.ref().model_dump(mode='json'),
               'author_receipts':report['author_chain_receipts'],
               'selected_manuscript':final,
               'numbering_input':report['manuscript_checker']['consumer_artifacts']['numbering_input'],
               'tex':{'path':str(tex),'sha256':hashlib.sha256(tex.read_bytes()).hexdigest()},
               'pdf':{'path':str(pdf),'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'bytes':pdf.stat().st_size},
               'scientific_acceptance':False,'visual_quality_acceptance':False}
        (tmp_path/'NUMERICAL_PUBLICATION_ENGINE_PROOF.json').write_text(json.dumps(proof,indent=2))
