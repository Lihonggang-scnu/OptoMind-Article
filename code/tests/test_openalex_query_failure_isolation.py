"""Offline fake-wire regressions; no keys, credentials or network are read."""
import io
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

import unittest
from contextlib import ExitStack
from tools.academic_backends import openalex_backend as oa
from optomind_optics.harness.method_research import DefaultMethodResearchOnlineClient

class Reply:
    status = 200
    def __init__(self, data): self.data=data
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return json.dumps(self.data).encode()

def failure(code,headers=None):
    return urllib.error.HTTPError('https://example.invalid',code,'fake failure',headers or {},io.BytesIO(b'{}'))

class Tests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for p in [patch.object(oa,"_openalex_api_keys",return_value=[]),patch.object(oa,"_openalex_email",return_value=None),patch.object(oa.OpenAlexBackend,"_respect_rate_limit"),patch.object(oa.time,"sleep")]:self.stack.enter_context(p)
        self.backend=oa.OpenAlexBackend();oa.OpenAlexBackend._global_disabled_until=0
    def tearDown(self):
        oa.OpenAlexBackend._global_disabled_until=0

    def test_query400_does_not_suppress_next_independent_query(self):
        backend=self.backend
        item={'id':'https://openalex.org/W123','title':'Thin-film optical filter'}
        with patch.object(oa.urllib.request,'urlopen',side_effect=[failure(400),Reply({'results':[item]})]) as wire:
            assert backend.search('first malformed query')==[]
            assert backend.last_error.startswith('HTTP 400')
            assert not backend.adapter_only
            records=backend.search('next independent query')
            assert len(records)==1 and backend.last_error==''
            assert wire.call_count==2 and backend.stats['requests']==2

    def test_auth_and_exhaustion_still_fail_closed(self):
        for code in [401,402,403,429]:
            self.backend.adapter_only=False
            self.backend.last_error=""
            self._check_fail_closed(code)

    def _check_fail_closed(self,code):
        backend=self.backend
        with patch.object(oa.urllib.request,'urlopen',side_effect=failure(code,{'Retry-After':'3600'})) as wire:
            assert backend.search('one')==[]
            assert backend.adapter_only
            assert backend.search('two')==[]
            assert wire.call_count==1

    def test_transient_failure_is_not_mislabeled_as_zero_success(self):
        backend=self.backend
        with patch.object(oa.urllib.request,'urlopen',side_effect=[TimeoutError('fake timeout'),Reply({'results':[]})]):
            client=DefaultMethodResearchOnlineClient(s2_gateway=SimpleNamespace(),openalex_backend=backend)
            failed=client.search_openalex('one',limit=2)
            assert failed.status=='error' and failed.error
            empty=client.search_openalex('two',limit=2)
            assert empty.status=='empty_success' and not empty.error
            assert empty.provider_calls['openalex_http_requests']==1

    def test_skipped_adapter_has_zero_requests_not_six_http_queries(self):
        backend=self.backend
        client=DefaultMethodResearchOnlineClient(s2_gateway=SimpleNamespace(),openalex_backend=backend)
        with patch.object(oa.urllib.request,'urlopen',side_effect=failure(403)) as wire:
            results=[client.search_openalex(str(i),limit=2) for i in range(6)]
            assert wire.call_count==1
            assert sum(x.provider_calls['openalex_http_requests'] for x in results)==1
            assert sum(x.provider_calls['openalex_skipped_adapter_calls'] for x in results)==5
            assert all(x.status=='adapter_skipped' for x in results[1:])

    def test_existing429_retry_count_is_actual_wire_count(self):
        backend=self.backend
        with patch.object(oa.urllib.request,'urlopen',side_effect=[failure(429,{'Retry-After':'3'}),Reply({'results':[]})]):
            client=DefaultMethodResearchOnlineClient(s2_gateway=SimpleNamespace(),openalex_backend=backend)
            result=client.search_openalex('one',limit=2)
            assert result.status=='empty_success'
            assert result.provider_calls['openalex_http_requests']==2

    def test_generated_prose_compacts_structured_fields_and_retains_original(self):
        from optomind_optics.harness.method_research import generate_method_research_queries
        original='Design a very detailed optical device with many wavelength constraints ' * 8
        queries=generate_method_research_queries({'normalized_request_english':original,'method_research_questions':['How to optimize thickness and sensitivity?'],'design_family':'planar dielectric multilayer'})
        first=queries[0]
        assert 'Design a very detailed' in first.query_text
        assert first.openalex_query_text=='planar dielectric multilayer sensitivity robustness'
        assert len(first.openalex_query_text.split())<=9
        explicit=generate_method_research_queries({},explicit_queries=['"optical filter" AND tolerance'],max_queries=1)[0]
        assert explicit.query_text=='"optical filter" AND tolerance'
        assert not explicit.openalex_query_text

    def test_report_records_effective_query_and_preserves_zero_evidence(self):
        import tempfile
        from pathlib import Path
        from optomind_optics.harness.method_research import TMMMethodResearchAdapter, MethodOnlineSearchResult
        class Fake:
            def search_s2(self,query,*,limit):return MethodOnlineSearchResult(records=[],error='s2_key_pool_empty',provider_calls={})
            def search_openalex(self,query,*,limit):
                self.query=query
                return MethodOnlineSearchResult(records=[],status='empty_success',provider_calls={'openalex_http_requests':1})
        fake=Fake()
        with tempfile.TemporaryDirectory() as tmp:
            report=TMMMethodResearchAdapter(review_kb_paths=[Path(tmp)/'absent.sqlite'],online_client=fake).research({'problem_id':'test','normalized_request_english':'Design an optical filter using many constraints','method_research_questions':['How to optimize thickness?']})
        log=[row for row in report.telemetry.online_query_log if row['route']=='openalex_search']
        assert log and log[0]['original_query']!=log[0]['effective_query']
        assert log[0]['status']=='empty_success'
        assert report.telemetry.openalex_http_requests==len(log)
        assert report.telemetry.records_accepted==0 and not report.evidence

    def test400_body_diagnostic_is_bounded_and_redacted(self):
        body={'error':'invalid_search','message':'Wildcard rejected api_key=SECRET bearer HIDDEN https://example.invalid/?private=query','headers':{'Authorization':'DO_NOT_LOG'}}
        exc=urllib.error.HTTPError('https://example.invalid',400,'Bad Request',{},io.BytesIO(json.dumps(body).encode()))
        with patch.object(oa.urllib.request,'urlopen',side_effect=exc):
            assert self.backend._fetch_json('https://example.invalid',key='SECRET') is None
        error=self.backend.last_error
        assert 'invalid_search' in error and 'Wildcard rejected' in error
        assert all(x not in error for x in ['SECRET','HIDDEN','DO_NOT_LOG','private=query'])
        assert len(error)<1100
        assert oa._safe_bad_request_detail(SimpleNamespace(read=lambda n:b'x'*n))==''

    def test400_authorization_schemes_never_leave_echoed_tokens(self):
        examples=[
            'Authorization: Bearer HIDDEN',
            'Authorization: Basic dXNlcjpwYXNz',
            'authorization = "Bearer HIDDEN VALUE"',
            'Bearer HIDDEN',
            'Basic dXNlcjpwYXNz',
            'Authorization: Bearer HIDDEN; extra secret VALUE',
            'Authorization: Bearer\nHIDDEN',
            'api_key: "HIDDEN VALUE"',
            'access_token=HIDDEN VALUE',
        ]
        for message in examples:
            with self.subTest(message=message):
                body=json.dumps({'error':'invalid_search','message':'Query rejected. '+message}).encode()
                detail=oa._safe_bad_request_detail(io.BytesIO(body))
                assert 'invalid_search' in detail
                assert '[credential-bearing detail omitted]' in detail
                assert all(secret not in detail for secret in ['HIDDEN','VALUE','dXNlcjpwYXNz'])

    def test_explicit_analysis_ids_never_trigger_automatic_compaction(self):
        from optomind_optics.harness.method_research import generate_method_research_queries, MethodResearchQuery
        for alternate in ['', 'authorized alternate literal query']:
            payload={'query_id':'analysis_question_01','query_text':'"optical filter" AND tolerance','purpose':'fabrication_constraint','openalex_query_text':alternate}
            for supplied in [payload,MethodResearchQuery.model_validate(payload)]:
                with self.subTest(alternate=alternate, supplied_type=type(supplied).__name__):
                    result=generate_method_research_queries({'design_family':'different family'},explicit_queries=[supplied],max_queries=1)[0]
                    assert result.query_text==payload['query_text']
                    assert result.openalex_query_text==alternate

if __name__=="__main__":unittest.main()
