import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('focused_runner', ROOT / 'run.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
from optomind_research.s2_intelligence_gateway import S2GatewayResponse, S2IntelligenceGateway


def plan():
    raw = {
        'schema_version': r.qp.QUERY_PLAN_SCHEMA,
        'question_en': 'How does cycling degradation reduce capacity in sodium ion battery cathodes?',
        'research_object': 'sodium ion battery cathodes',
        'ambiguity': {'is_ambiguous': False, 'default_reading': '', 'needs_user_input': []},
        'facets': [{'id': 'F1', 'ask': 'Which structural changes affect capacity retention?',
            'keyword_queries': ['sodium ion battery cathodes cycling degradation'],
            'question_queries': ['How do structural changes affect capacity retention in sodium ion battery cathodes?'],
            'filters': {'publication_type': [], 'fields_of_study': [], 'text_availability': []},
            'must_exclude': []}],
        'seeds': [], 'criteria': {'must_include_topic': ['sodium ion battery cathodes'],
            'must_exclude_domain': [], 'synonyms': {}}, 'additional_constraints': [],
    }
    report = r.qp.validate_plan(raw)
    assert report['ok'], report['errors']
    return report['plan']


class Gateway:
    def __init__(self, fail=False, empty=False):
        self.calls = []; self.fail = fail; self.empty = empty

    def search_papers(self, query, **kwargs):
        self.calls.append(('paper', query, kwargs))
        papers = [] if self.empty else [SimpleNamespace(paper_id='P1', doi='10.1/test', corpus_id=42,
            title='A real fixture title', authors=[], year=2024, venue='Fixture', abstract='Fixture abstract')]
        return papers, S2GatewayResponse(ok=True, status_code=200, status_category='ok')

    def search_snippets(self, query, **kwargs):
        self.calls.append(('snippet', query, kwargs))
        if self.fail:
            raise RuntimeError('NEVER-PERSIST-SECRET')
        rows = [] if self.empty else [
            {'paper': {'corpusId':42, 'title':'A real fixture title'},
             'snippet': {'text': text}, 'score': .9} for text in ('First passage.', 'Second passage.')]
        return rows, S2GatewayResponse(ok=True, status_code=200, status_category='ok')


def test_profile_and_prompt_numeric_changes():
    fields = r.qp.load_field_dictionary()
    assert fields['limits'] == dict(facets_min=1, facets_max=2, keyword_queries_per_facet_max=1,
        question_queries_per_facet_max=2, seeds_max=3)
    prompt = r.qp.render_prompt(fields)['system_prompt']
    assert 'Use up to two only' in prompt
    assert 'Use two complementary forms' in prompt


def test_retrieval_depth_mapping_materials_and_dedup(tmp_path):
    gateway = Gateway()
    result = r.retrieve(plan(), tmp_path, gateway=gateway)
    assert result['status'] == 'complete'
    assert result['papers'] == 1
    assert result['hits'] == 3
    assert all(c[2]['limit'] == 200 for c in gateway.calls)
    assert len(gateway.calls) == 2
    assert all(c[2]['deadline'] > time.monotonic() for c in gateway.calls)
    material = json.loads((tmp_path/'materials.json').read_text())
    assert [m['material_type'] for m in material] == ['abstract', 'snippet', 'snippet']
    assert all(m['paper_id'] == 'P1' and m['facet_id'] == 'F1' for m in material)
    assert all(not m['fulltext_retrieved'] for m in material)
    assert material[1]['text'] == 'First passage.'
    assert material[1]['text_hash'] == hashlib.sha256(b'First passage.').hexdigest()
    store = r.SnippetTextStore(tmp_path/'snippets.sqlite')
    assert store.count() == 2
    store.close()


def test_failure_keeps_successful_material_and_marks_partial(tmp_path):
    result = r.retrieve(plan(), tmp_path, gateway=Gateway(fail=True))
    assert result['status'] == 'partial' and result['hits'] == 1
    calls = json.loads((tmp_path/'provider_calls.json').read_text())
    assert calls[1]['status_category'] == 'exception:RuntimeError'
    assert 'NEVER-PERSIST-SECRET' not in (tmp_path/'provider_calls.json').read_text()


def test_no_results_is_not_success(tmp_path):
    assert r.retrieve(plan(), tmp_path, gateway=Gateway(empty=True))['status'] == 'no_results'


def test_deadline_fails_closed_without_provider_call():
    gateway = Gateway()
    a = r.AuditedGateway(gateway, deadline=time.monotonic()-1)
    assert a.search_papers('query',limit=200)[0] == []
    assert a.calls[0]['partial'] and not gateway.calls


@pytest.mark.parametrize('limit,seconds', [(0,300),(201,300),(100,0),(100,1801)])
def test_invalid_budgets(limit,seconds):
    with pytest.raises(ValueError): r.validate_limits(limit,seconds)


def test_unapplied_explicit_filter_marks_partial(tmp_path):
    p=plan();p['facets'][0]['filters']['publication_type']=['JournalArticle']
    assert r.retrieve(p,tmp_path,gateway=Gateway())['status']=='partial'


def test_overbudget_external_plan_is_rejected_before_network(tmp_path):
    p=plan();p['facets'][0]['keyword_queries'] *= 2
    gateway=Gateway()
    with pytest.raises(ValueError): r.retrieve(p,tmp_path,gateway=gateway)
    assert not gateway.calls


class Transport:
    def __init__(self, fail_second=False): self.calls=[];self.fail_second=fail_second
    def request_json(self, method, endpoint, **kwargs):
        self.calls.append((method,endpoint,kwargs))
        params=kwargs['params']
        if endpoint.endswith('/snippet/search'):
            return S2GatewayResponse(ok=True,status_code=200,status_category='ok',payload={'data':[]})
        offset=params['offset']
        if self.fail_second and offset:
            return S2GatewayResponse(ok=False,status_code=429,status_category='rate_limited',payload={})
        rows=[{'paperId':f'P{i}','corpusId':i,'title':f'Paper {i}'} for i in range(offset,offset+params['limit'])]
        return S2GatewayResponse(ok=True,status_code=200,status_category='ok',payload={'data':rows,'total':300})


def test_real_gateway_paper_200_is_two_pages_and_snippet_200_single_call():
    t=Transport();g=S2IntelligenceGateway(t)
    papers,response=g.search_papers('focused query',limit=200)
    assert len(papers)==200 and response.audit['page_count']==2
    assert [c[2]['params']['offset'] for c in t.calls]==[0,100]
    assert all(c[2]['params']['limit']==100 for c in t.calls)
    assert all('corpusId' in c[2]['params']['fields'] for c in t.calls)
    g.search_snippets('question',limit=200)
    assert t.calls[-1][2]['params']['limit']==200


def test_real_gateway_later_page_failure_is_partial(tmp_path):
    g=S2IntelligenceGateway(Transport(fail_second=True))
    out=r.retrieve(plan(),tmp_path,gateway=g)
    assert out['status']=='partial' and out['hits']==100
    calls=json.loads((tmp_path/'provider_calls.json').read_text())
    assert calls[0]['truncated_reason']=='later_page_unavailable'


def test_mock_planning_rejected(tmp_path):
    fake=SimpleNamespace(query_plan_v2=lambda q: {'status':'ok','plan':plan(),'round_usages':[{'mock_llm':True}]})
    assert r.generate_plan('Question',tmp_path,planner=fake)['status']=='mock_rejected'


def test_output_reuse_refused(tmp_path):
    (tmp_path/'summary.json').write_text('{}')
    with pytest.raises(ValueError): r.prepare_output(tmp_path)


def test_vendor_manifest_hashes():
    manifest=json.loads((ROOT/'PROVENANCE.json').read_text())
    for row in manifest['files']:
        assert hashlib.sha256((r.VENDOR/row['source']).read_bytes()).hexdigest()==row['vendored_sha256']


def test_duplicate_queries_reuse_provider_result_and_preserve_mappings(tmp_path):
    p=plan();p['facets'].append({**p['facets'][0], 'id':'F2',
        'ask':'How does capacity loss evolve across cycling conditions?'})
    g=Gateway();out=r.retrieve(p,tmp_path,gateway=g)
    assert len(g.calls)==2
    assert out['queries']==4 and out['hits']==6 and out['papers']==1
    assert out['unique_materials']==3
    unique=json.loads((tmp_path/'unique_materials.json').read_text())
    assert all({m['facet_id'] for m in u['source_mappings']}=={'F1','F2'} for u in unique)
    calls=json.loads((tmp_path/'provider_calls.json').read_text())
    assert [c['deduplicated_query'] for c in calls]==[False,False,True,True]


def test_original_planner_uses_tuned_prompt_and_real_provider_contract(monkeypatch,tmp_path):
    import optomind_research.query_planner as planner_module
    calls=[]
    def offline_call(agent,messages,**kwargs):
        calls.append((agent,messages,kwargs))
        return {'content':json.dumps(plan()),'_llm_usage':{'mock_llm':False},'fallback_used':False}
    monkeypatch.setattr(planner_module,'call_qwen_chat',offline_call)
    out=r.generate_plan('How does cycling degradation affect sodium ion battery cathodes?',tmp_path)
    assert out['status']=='ok' and len(calls)==1
    assert calls[0][2]['force_mock'] is False
    assert calls[0][2]['response_format']=={'type':'json_object'}
    assert 'STEP 1 --' in calls[0][1][0]['content']
    assert 'STEP 2 --' in calls[0][1][0]['content']
    assert json.loads((tmp_path/'subproblems.json').read_text())[0]['facet_id']=='F1'
    assert len(json.loads((tmp_path/'queries.json').read_text()))==2


@pytest.mark.parametrize('location', ['facet', 'criteria'])
def test_external_exclusions_never_claim_applied(tmp_path,location):
    p=plan()
    if location=='facet': p['facets'][0]['must_exclude']=['excluded material']
    else: p['criteria']['must_exclude_domain']=['excluded domain']
    assert r.retrieve(p,tmp_path,gateway=Gateway())['status']=='partial'


def test_real_transport_builds_bounded_provider_urls_without_network(tmp_path):
    from urllib.parse import urlparse, parse_qs
    from optomind_research.s2_intelligence_gateway import S2Transport
    calls=[]
    class Response:
        status=200
        def __enter__(self): return self
        def __exit__(self,*args): return False
        def read(self): return json.dumps({'data': [], 'total': 0}).encode()
    def opener(request,timeout):
        calls.append((request.full_url,timeout))
        return Response()
    gateway=S2IntelligenceGateway(S2Transport(keys=[],cache_path=tmp_path/'cache.sqlite',
        opener=opener,min_interval_seconds=0,max_attempts=2))
    adapter=r.AuditedGateway(gateway,deadline=time.monotonic()+5)
    adapter.search_papers('precise phrase',limit=200)
    adapter.search_snippets('precise question?',limit=200)
    assert len(calls)==2
    first=parse_qs(urlparse(calls[0][0]).query)
    second=parse_qs(urlparse(calls[1][0]).query)
    assert first['limit']==['100'] and first['offset']==['0']
    assert second['limit']==['200'] and second['query']==['precise question?']
    assert all(timeout<=5 for _,timeout in calls)


def test_transport_failure_prevents_retrieval_even_with_valid_shape(monkeypatch,tmp_path):
    import optomind_research.query_planner as planner_module
    monkeypatch.setattr(planner_module,'call_qwen_chat',lambda *a,**kw:{
        'content':'[fallback] provider unavailable','fallback_used':True,
        'transport_error':'HTTPError_429','_llm_usage':{'mock_llm':False}})
    outcome=r.generate_plan('Question',tmp_path)
    assert outcome['status']=='transport_rejected'
    assert outcome['errors']==['api_error']


def test_valid_looking_transport_fallback_is_rejected(tmp_path):
    fake=SimpleNamespace(query_plan_v2=lambda q: {'status':'ok','plan':plan(),
        'round_usages':[], 'transport':{'fallback_used':True}})
    assert r.generate_plan('Question',tmp_path,planner=fake)['status']=='transport_rejected'
