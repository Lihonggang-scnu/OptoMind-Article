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
    fake=SimpleNamespace(_call_query_plan_model=lambda p: '{}', last_usage={'mock_llm':True}, last_transport={})
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
        return {'content':json.dumps(task_sheet()),'_llm_usage':{'mock_llm':False},'fallback_used':False}
    monkeypatch.setattr(planner_module,'call_qwen_chat',offline_call)
    out=r.generate_plan(task_sheet()['original_question'],tmp_path)
    assert out['status']=='ok' and len(calls)==1
    assert calls[0][2]['force_mock'] is False
    assert calls[0][2]['response_format']=={'type':'json_object'}
    assert 'research task sheet' in calls[0][1][0]['content']
    assert task_sheet()['original_question'] in calls[0][1][0]['content']
    assert json.loads((tmp_path/'subproblems.json').read_text())[0]['facet_id']=='Q1'
    assert len(json.loads((tmp_path/'queries.json').read_text()))==5


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
    fake=SimpleNamespace(_call_query_plan_model=lambda p: json.dumps(task_sheet()),
        last_usage={}, last_transport={'fallback_used':True})
    assert r.generate_plan('Question',tmp_path,planner=fake)['status']=='transport_rejected'


def task_sheet():
    return json.loads((ROOT/'examples/rgb_nir_task_sheet.json').read_text())


def test_task_sheet_full_example_contract():
    sheet = task_sheet()
    assert r.ts.validate(sheet) == sheet
    assert '99%' not in str(sheet['explicit_constraints'])
    assert sheet['goal_relationships']['mode'] == 'all'
    assert sheet['goal_relationships']['priority_order'] is None
    assert sheet['goal_relationships']['tradeoff'] is None
    assert 'coupled' in sheet['goal_relationships']['coupling']
    assert all(m['status']=='candidate' for m in sheet['candidate_mechanisms'])
    assert not any('polarization' in str(x) for x in sheet['known_data'])
    assert len(sheet['explicit_constraints']) == 9
    prompt = r.ts.render_prompt('Other question')['system_prompt']
    assert sheet['original_question'] in prompt
    assert '/paper/search' in prompt and '/snippet/search' in prompt


def test_task_adapter_executes_exact_queries_and_links(tmp_path):
    sheet = task_sheet(); gateway = Gateway()
    result = r.retrieve(sheet, tmp_path, gateway=gateway)
    assert result['queries'] == 5 and result['status'] == 'complete'
    assert result['constraints_evaluated'] is False
    assert [call[1] for call in gateway.calls] == [q['query'] for q in sheet['search_queries']]
    assert [call[0] for call in gateway.calls] == ['paper','snippet','paper','snippet','snippet']
    assert all(call[2]['limit']==200 for call in gateway.calls)
    for artifact in ['materials.json','queries.json','provider_calls.json']:
        rows=json.loads((tmp_path/artifact).read_text())
        assert all(row['task_query']['subgoal_ids']==['G1','G2'] for row in rows)
    corpus=json.loads((tmp_path/'candidate_corpus.json').read_text())
    assert corpus['filter_execution']['task_sheet_constraints_for_later_evaluation']==sheet['explicit_constraints']
    unique=json.loads((tmp_path/'unique_materials.json').read_text())
    assert all(m['task_query']['id'] for row in unique for m in row['source_mappings'])


@pytest.mark.parametrize('mutation', ['query','mapping','budget','span','missing','endpoint','subgoal','mechanism','question','padded'])
def test_task_contract_fail_closed_before_calls(tmp_path, mutation):
    sheet=task_sheet(); p=r.ts.adapt(sheet)
    if mutation=='query': p['facets'][0]['keyword_queries'][0]='silently changed'
    elif mutation=='mapping': p['task_query_mappings']['Q1_KQ_01']['purpose']='mechanism'
    elif mutation=='budget':
        for i in range(2):
            sheet['search_queries'].append({**sheet['search_queries'][0], 'id':f'EXTRA{i}'})
        p=sheet
    elif mutation=='span': sheet['explicit_constraints'][0]['original_span']='99%'; p=sheet
    elif mutation=='missing': del sheet['goal_relationships']; p=sheet
    elif mutation=='endpoint': sheet['search_queries'][0]['endpoint']='paper_search_bulk'; p=sheet
    elif mutation=='subgoal': sheet['search_queries'][0]['subgoal_ids']=['BAD']; p=sheet
    elif mutation=='mechanism': sheet['candidate_mechanisms'][0]['status']='proven'; p=sheet
    elif mutation=='question': sheet['search_queries'][0]['query']='Which coating?'; p=sheet
    else: sheet['search_queries'][0]['query']=' padded query '; p=sheet
    gateway=Gateway()
    with pytest.raises(ValueError): r.retrieve(p,tmp_path,gateway=gateway)
    assert not gateway.calls


def test_task_repair_and_raw_history(monkeypatch,tmp_path):
    import optomind_research.query_planner as pm
    good=task_sheet(); bad=task_sheet(); bad['original_question']='short surrogate'
    outputs=iter([bad,good]); calls=[]
    def fake(*args,**kwargs):
        calls.append(args)
        return {'content':json.dumps(next(outputs)), '_llm_usage':{}}
    monkeypatch.setattr(pm,'call_qwen_chat',fake)
    out=r.generate_plan(good['original_question'],tmp_path)
    assert out['status']=='ok' and out['revision_rounds']==1
    assert out['rounds'][0]['raw_task_sheet']['original_question']=='short surrogate'
    assert 'validation_error' in json.loads(calls[1][1][1]['content'])


def test_mixed_unresolved_nullable_contract():
    sheet=task_sheet()
    sheet['task_type']='mixed/unresolved'
    sheet['goal_relationships']={'mode':'mixed/unresolved','priority_order':None,'tradeoff':None,'coupling':None}
    sheet['candidate_mechanisms']=[]
    for q in sheet['search_queries']: q['mechanism_ids']=[]
    assert r.ts.validate(sheet)==sheet
    for mode in ['all','any','priorities','tradeoff']:
        sheet['goal_relationships']={'mode':mode,'priority_order':['G1','G2'] if mode=='priorities' else None,
            'tradeoff':'Priority tradeoff unresolved' if mode=='tradeoff' else None,'coupling':None}
        assert r.ts.validate(sheet)==sheet


def test_repeated_papers_cannot_escape_relevance_window():
    class RepeatedTransport:
        def __init__(self): self.calls=[]
        def request_json(self,*a,**kw):
            self.calls.append(kw['params'])
            return S2GatewayResponse(ok=True,status_code=200,status_category='ok',payload={
                'total':10000, 'data':[{'paperId':'P1','title':'same'}]*kw['params']['limit']})
    t=RepeatedTransport(); g=S2IntelligenceGateway(t)
    papers,response=g.search_papers('coating',limit=200)
    assert len(papers)==1 and len(t.calls)==10
    assert all(c['offset']+c['limit']<=1000 for c in t.calls)
    assert response.audit['truncated_reason']=='relevance_result_window'


@pytest.mark.parametrize('query_index', [0, 1])
def test_single_endpoint_task_needs_no_filler(tmp_path, query_index):
    sheet=task_sheet(); sheet['search_queries']=[sheet['search_queries'][query_index]]
    gateway=Gateway()
    result=r.retrieve(sheet,tmp_path,gateway=gateway)
    assert result['status']=='complete' and result['queries']==1
    assert len(gateway.calls)==1
    corpus=json.loads((tmp_path/'candidate_corpus.json').read_text())
    assert corpus['input_state']['degraded'] is False
    assert corpus['input_state']['channel_selection']=='intentional_per_query'


def test_preserve_original_question_whitespace():
    sheet=task_sheet(); sheet['original_question']=' \n'+sheet['original_question']+'\n'
    assert r.ts.validate(sheet,question=sheet['original_question'])==sheet


def test_contradictory_relationships_and_crosslinks_rejected():
    sheet=task_sheet(); sheet['goal_relationships']['priority_order']=['G1']
    with pytest.raises(ValueError): r.ts.validate(sheet)
    sheet=task_sheet(); sheet['research_questions'][0]['subgoal_ids']=['G1']
    with pytest.raises(ValueError): r.ts.validate(sheet)
    sheet=task_sheet(); sheet['search_queries'][0]['subgoal_ids']=['G3']
    with pytest.raises(ValueError): r.ts.validate(sheet)


def test_distinct_goals_preserve_application_intent_and_original_input():
    sheet=task_sheet()
    assert [g['id'] for g in sheet['subgoals']]==['G1','G2']
    assert all(g['executable_metric']['mean_reduction']=='wavelength_integral_mean' for g in sheet['subgoals'])
    intent=sheet['application_intents'][0]
    assert intent['original_span']=='多光谱成像相机'
    assert '降低通道间串扰' not in sheet['original_question']
    assert intent['subgoal_ids']==['G1','G2'] and intent['operational_definition'] is None
    assert '450-650 nm' in sheet['original_question'] and '455' not in sheet['original_question']
    for section in ['research_questions','candidate_mechanisms','search_queries']:
        assert all(set(row['subgoal_ids']) <= {'G1','G2'} for row in sheet[section])
    adapted=r.ts.adapt(sheet)
    assert adapted['task_sheet']['application_intents']==sheet['application_intents']
    prompt=r.ts.render_prompt(sheet['original_question'])['system_prompt']
    assert 'NOT a promise of physical or statistical independence' in prompt
    assert 'QUERY QUALITY IS PROVISIONAL' in prompt


@pytest.mark.parametrize('mutation', ['intent_link','intent_span','missing_definition','numeric_definition','duplicate_goal','old_schema'])
def test_intent_and_goal_contract_rejects_invalid_shapes_before_calls(tmp_path,mutation):
    sheet=task_sheet()
    if mutation=='intent_link': sheet['application_intents'][0]['subgoal_ids']=['G3']
    elif mutation=='intent_span': sheet['application_intents'][0]['original_span']='invented intent'
    elif mutation=='missing_definition': del sheet['subgoals'][0]['operational_definition']
    elif mutation=='numeric_definition': sheet['subgoals'][0]['operational_definition']=0.99
    elif mutation=='old_schema': sheet['schema_version']='focused_research.task_sheet.v1'
    else:
        sheet['subgoals'][1]['objective']=sheet['subgoals'][0]['objective'].upper()
    gateway=Gateway()
    with pytest.raises(ValueError): r.retrieve(sheet,tmp_path,gateway=gateway)
    assert not gateway.calls


def test_qualitative_requirement_requires_revision_without_proxy():
    sheet=task_sheet()
    sheet['task_type']='explanation'
    sheet['application_intents']=[]
    sheet['subgoals']=[]
    sheet['research_questions']=[]
    sheet['candidate_mechanisms']=[]
    sheet['search_queries']=[]
    sheet['unsupported_requirements']=[{'id':'U1','requirement':'Qualitative explanation requested',
        'original_span':'多光谱成像相机','reason':'No supported executable metric specified'}]
    sheet['readiness']='needs_revision'
    assert r.ts.validate(sheet)==sheet
    with pytest.raises(ValueError,match='needs_revision'): r.ts.adapt(sheet)


@pytest.mark.parametrize('field,value', [
    ('metric','crosstalk'),('metric','1-R'),('metric','max(R,T)'),('metric',{}),
    ('mean_reduction','mean'),('mean_reduction',None),('sense','match'),
    ('weight',1),('target',0.99),('objective_id','not_G1'),
])
def test_unsupported_metric_expressions_rejected_before_calls(tmp_path,field,value):
    sheet=task_sheet(); sheet['subgoals'][0]['executable_metric'][field]=value
    gateway=Gateway()
    with pytest.raises(ValueError): r.retrieve(sheet,tmp_path,gateway=gateway)
    assert not gateway.calls


@pytest.mark.parametrize('field,value', [
    ('wavelength_nm',[450,450]),('wavelength_nm',[650,450]),('wavelength_nm',[0,650]),
    ('wavelength_nm',[float('nan'),650]),('wavelength_nm',[450,float('inf')]),
    ('angle_deg',None),('angle_deg',True),('angle_deg',float('nan')),('angle_deg',90),
    ('polarization',None),('polarization','average'),('formula','R+T'),
])
def test_invalid_or_missing_metric_conditions_rejected(field,value):
    sheet=task_sheet(); sheet['subgoals'][0]['executable_metric']['region'][field]=value
    with pytest.raises(ValueError): r.ts.validate(sheet)


def test_unsupported_original_requirement_blocks_execution_and_is_preserved(tmp_path):
    sheet=task_sheet()
    historical=json.loads((ROOT/'examples/rgb_nir_question.v1.json').read_text())
    sheet['original_question']=historical['original_question']
    sheet['explicit_constraints']=sheet['explicit_constraints'][:7]
    # Original v1 has no specified reducer/polarization, so neither spectral
    # goal is executable without revision; do not keep the v2 metric defaults.
    sheet['subgoals']=[]; sheet['application_intents']=[]
    sheet['research_questions']=[];sheet['candidate_mechanisms']=[];sheet['search_queries']=[]
    sheet['unsupported_requirements']=[
        {'id':'U1','requirement':'Reduce interchannel crosstalk','original_span':'降低通道间串扰',
         'reason':'No supported independent crosstalk metric; no proxy substitution'},
        {'id':'U2','requirement':'Visible reflection and NIR transmission objectives',
         'original_span':'可见光波段 450-650 nm 尽可能高的反射率（用于 RGB 通道成像）',
         'reason':'Polarization and band reduction are unspecified; request revision'}]
    sheet['readiness']='needs_revision'
    assert r.ts.validate(sheet,question=historical['original_question'])==sheet
    gateway=Gateway()
    with pytest.raises(ValueError,match='needs_revision'): r.retrieve(sheet,tmp_path,gateway=gateway)
    assert not gateway.calls
    fake=SimpleNamespace(_call_query_plan_model=lambda p:json.dumps(sheet),last_usage={},last_transport={})
    out=r.generate_plan(sheet['original_question'],tmp_path,planner=fake)
    assert out['status']=='needs_revision' and out['plan']=={} and len(out['rounds'])==1
    assert json.loads((tmp_path/'task_sheet.json').read_text())==sheet
    sheet['readiness']='ready'
    with pytest.raises(ValueError,match='readiness'):r.ts.validate(sheet)


def test_versioned_demo_question_preserves_history_and_states_new_conditions():
    v1=json.loads((ROOT/'examples/rgb_nir_question.v1.json').read_text())
    v2=json.loads((ROOT/'examples/rgb_nir_question.v2.json').read_text())
    assert '，降低通道间串扰。' in v1['original_question']
    assert v2['original_question'].startswith(v1['original_question'].replace('，降低通道间串扰',''))
    assert '非偏振光（s、p 偏振等权平均）' in v2['original_question']
    assert '按波长积分后除以波段宽度' in v2['original_question']
    assert task_sheet()['original_question']==v2['original_question']


def test_existing_objective_consumer_in_isolated_offline_process():
    import os
    import subprocess
    env=dict(os.environ)
    env['PYTHONPATH']=os.pathsep.join([str(ROOT.parent/'code'),str(ROOT.parent/'veritmm'),
        env.get('PYTHONPATH','')])
    result=subprocess.run([sys.executable,'-m','pytest','-q','--disable-socket',
        str(ROOT/'tests/objective_consumer_check.py')],cwd=ROOT.parent,env=env,
        capture_output=True,text=True,timeout=60)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
    assert '4 passed' in result.stdout


def test_duplicate_executable_metric_is_not_a_distinct_goal():
    sheet=task_sheet()
    copied=json.loads(json.dumps(sheet['subgoals'][0]['executable_metric']))
    copied['objective_id']='G2'
    copied['region']['wavelength_nm']=[450.0,650.0]
    copied['region']['angle_deg']=45.0
    sheet['subgoals'][1]['executable_metric']=copied
    with pytest.raises(ValueError,match='duplicate executable metrics'):
        r.ts.validate(sheet)
