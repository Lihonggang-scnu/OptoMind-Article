"""Offline evidence retention fixtures; no paid or online requests."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('evidence_runner', ROOT / 'run.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
from evidence_store import build_evidence_items, serialize_record


def build(rows, goals=None):
    specs = [NS(query_id=f'Q{i}', facet_id=f'G{i}', query_text=f'query {i}', retrieval_source='snippet_search') for i in range(len(rows))]
    return build_evidence_items([{'call_index': i, 'rows': batch} for i, batch in enumerate(rows)], specs, goals)['items']


def test_fields_missing_metadata_and_exact_original_text():
    rows = [{'paper': {'corpusId': 1}, 'snippet': {'text': '  original\ntext ', 'snippetKind': 'body'}},
            {'source_url': 'https://example.org/a', 'snippet': 'flat text'},
            {'paper_id': 'P2', 'snippet_text': 'alternate text'},
            {'paperId': 'P3', 'abstract': 'Abstract', 'title': 'Title'},
            {'paperId': 'P4', 'snippet': {'content': 'nested content'}},
            {'paperId': 'P5', 'snippetText': 'camel text'}]
    items = build([rows])
    assert len(items) == 7
    assert items[0]['text'] == '  original\ntext ' and items[0]['snippet_type'] == 'body'
    assert [x['snippet_type'] for x in items[1:]] == ['unknown', 'unknown', 'abstract', 'title', 'unknown', 'unknown']
    assert all(x['eligible_as_evidence'] for x in items)
    assert items[0]['year'] is None and items[0]['authors'] == [] and items[0]['doi'] == ''


def test_missing_source_or_text_retained_but_not_evidence():
    items = build([[{'text': 'source missing'}, {'paperId': 'P'}, {'unrecognized': 'retain raw'}]])
    assert len(items) == 3
    assert not any(x['eligible_as_evidence'] for x in items)
    assert items[0]['missing_reasons'] == ['missing_source']
    assert items[1]['missing_reasons'] == ['missing_original_text']


def test_dedup_merges_all_goals_queries_and_preserves_distinct_passages():
    same = {'paper': {'corpusId': 1}, 'snippet': {'text': 'one'}}
    items = build([[same], [same, {'paper': {'corpusId': 1}, 'snippet': {'text': 'two'}}]],
                  {'Q0': {'id': 'T0', 'subgoal_ids': ['G1', 'G2']}, 'Q1': {'id': 'T1', 'subgoal_ids': ['G2', 'G3']}})
    assert len(items) == 2
    assert items[0]['query_ids'] == ['T0', 'T1']
    assert items[0]['goal_ids'] == ['G1', 'G2', 'G3']
    assert len(items[0]['source_mappings']) == 2


def test_source_aliases_join_without_merging_different_snippet_kinds():
    items = build([[{'paper_id': 'P1', 'corpus_id': 1, 'abstract': 'same'},
                    {'paper': {'corpusId': 1}, 'snippet': {'text': 'same', 'snippetKind': 'abstract'}},
                    {'paper': {'corpusId': 1}, 'snippet': {'text': 'same'}}]])
    assert len(items) == 2 and len(items[0]['source_mappings']) == 2
    assert items[0]['source_id'] == items[1]['source_id']


def test_partial_failures_keep_raw_rows_and_successes(tmp_path):
    # Reuse only the established offline plan fixture, never a live gateway.
    spec = importlib.util.spec_from_file_location('fixtures', ROOT / 'tests/test_focused.py')
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    gateway = fixture.Gateway(fail=True)
    summary = r.retrieve(fixture.plan(), tmp_path, gateway=gateway)
    assert summary['status'] == 'partial'
    assert all(c[2]['limit'] == 10 for c in gateway.calls)
    evidence = json.loads((tmp_path / 'evidence_items.json').read_text())
    assert evidence['schema_version'] == 'focused_evidence_items_v1'
    assert {x['snippet_type'] for x in evidence['items']} == {'title', 'abstract'}
    assert summary['eligible_evidence_items'] == 2 and summary['ineligible_evidence_items'] == 0
    raw = json.loads((tmp_path / 'raw_retrieval_records.json').read_text())
    assert raw[0]['rows'][0]['abstract'] == 'Fixture abstract' and raw[1]['rows'] == []
    assert 'NEVER-PERSIST-SECRET' not in json.dumps(raw)


def test_exact_query_cache_and_raw_record_serialization():
    class Gateway:
        def __init__(self): self.queries = []
        def search_snippets(self, query, **kwargs):
            self.queries.append(query)
            return [{'source_id': 's', 'text': 'original'}], NS(ok=True, audit={})
    gateway = Gateway()
    a = r.AuditedGateway(gateway, deadline=float('inf'))
    for query in ['Query  A', 'query A', 'Query  A']:
        a.search_snippets(query, limit=10)
    assert gateway.queries == ['Query  A', 'query A']
    assert len(a.raw_records) == 3 and a.calls[-1]['deduplicated_query']
    assert serialize_record(NS(paper_id='P', authors=[])) == {'paper_id': 'P', 'authors': []}


def test_task_query_bytes_and_configurable_depth(tmp_path):
    spec = importlib.util.spec_from_file_location('fixtures', ROOT / 'tests/test_focused.py')
    fixture = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixture)
    sheet = fixture.task_sheet()
    sheet['search_queries'][0]['query'] = sheet['search_queries'][0]['query'].replace(' ', '  ', 1)
    gateway = fixture.Gateway()
    r.retrieve(sheet, tmp_path, gateway=gateway, per_query_limit=15)
    assert gateway.calls[0][1] == sheet['search_queries'][0]['query']
    assert all(c[2]['limit'] == 15 for c in gateway.calls)
    evidence = json.loads((tmp_path / 'evidence_items.json').read_text())
    assert set(evidence['items'][0]['query_ids']) == {q['id'] for q in sheet['search_queries'] if q['endpoint'] == 'paper_search'}
    assert evidence['items'][0]['source_mappings'][0]['query_text'] == sheet['search_queries'][0]['query']


def test_partial_provider_rows_are_kept():
    class Gateway:
        def search_snippets(self, query, **kwargs):
            return [{'paperId': 'P1', 'text': 'retained partial'}], NS(ok=False, status_category='partial', audit={})
    adapter = r.AuditedGateway(Gateway(), deadline=float('inf'))
    rows, _ = adapter.search_snippets('query', limit=20)
    assert rows and adapter.calls[0]['partial']
    items = build([adapter.raw_records[0]['rows']])
    assert items[0]['text'] == 'retained partial' and items[0]['eligible_as_evidence']


def test_raw_metadata_quotes_original_and_cap_bounds_evidence():
    rows = [{'paper_id': 'P', 'abstract': 'normalized', 'raw_metadata':
             {'paperId': 'P', 'abstract': '  original\ntext  '}},
            {'paper_id': 'extra', 'abstract': 'outside budget'}]
    specs = [NS(query_id='Q', facet_id='G', query_text='query', retrieval_source='paper_search')]
    payload = build_evidence_items([{'call_index': 0, 'rows': rows, 'accepted_limit': 1}], specs)
    assert len(payload['items']) == 1
    item = payload['items'][0]
    assert item['text'] == '  original\ntext  '
    assert item['source_mappings'][0]['text_field'] == 'raw_metadata.abstract'


def test_query_sequence_mismatch_fails_closed():
    import pytest
    specs = [NS(query_text='exact query', retrieval_source='snippet_search')]
    adapter = r.AuditedGateway(object(), deadline=float('inf'), query_specs=specs)
    with pytest.raises(ValueError, match='sequence'):
        adapter.search_papers('exact query', limit=10)
    with pytest.raises(ValueError, match='sequence'):
        adapter.search_snippets('different query', limit=10)
    assert not adapter.calls
