"""Lossless provider-row retention and conservative, deterministic evidence items.

No generated prose, retrieval, enrichment, or PDF download happens here. A title
is a title; unlabelled snippet text remains unknown rather than presumed body.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
import hashlib
import json

SCHEMA = 'focused_evidence_items_v1'


def serialize_record(value):
    """Retain provider record fields without stringifying away their structure."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Mapping):
        return {str(k): serialize_record(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serialize_record(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, '__dict__'):
        return serialize_record(vars(value))
    raise TypeError('unsupported provider record type: ' + type(value).__name__)


def _text(value):
    return value if isinstance(value, str) and value.strip() else ''


def _first(row, *keys):
    return next((row[k] for k in keys if row.get(k) is not None and row[k] != ''), None)


def _original(row):
    # The vendor normalizes abstracts/titles; quote original bytes when available.
    raw = row.get('raw_metadata') if isinstance(row, dict) else None
    return raw if isinstance(raw, dict) and raw else row


def _accepted(batch):
    return batch['rows'][:batch.get('accepted_limit', len(batch['rows']))]


def _source(row):
    paper = row.get('paper') if isinstance(row.get('paper'), dict) else row
    external = paper.get('externalIds') or paper.get('external_ids') or {}
    if not isinstance(external, dict):
        external = {}
    doi = _first(paper, 'doi', 'DOI') or external.get('DOI') or ''
    identifiers = []
    for prefix, value in [('s2', _first(paper, 'paperId', 'paper_id')),
                          ('corpus', _first(paper, 'corpusId', 'corpus_id')),
                          ('doi', doi), ('source', _first(paper, 'source_id', 'sourceId'))]:
        if value is not None and str(value).strip():
            identifiers.append(prefix + ':' + str(value).strip())
    url = _text(_first(paper, 'url', 'source_url')) or _text(row.get('url')) or _text(row.get('source_url'))
    if url:
        identifiers.append('url:' + url)
    return paper, identifiers, url, doi


def _passages(row):
    paper = row.get('paper') if isinstance(row.get('paper'), dict) else row
    snippet = row.get('snippet')
    if isinstance(snippet, dict):
        kind = str(_first(snippet, 'snippetKind', 'snippet_kind', 'type') or '').lower()
        kind = {'title': 'title', 'abstract': 'abstract', 'body': 'body', 'body_text': 'body'}.get(kind, 'unknown')
        for key in ('text', 'snippet', 'content'):
            if _text(snippet.get(key)):
                yield snippet[key], kind, 'snippet.' + key
    elif _text(snippet):
        yield snippet, 'unknown', 'snippet'
    for key in ('text', 'snippet_text', 'snippetText', 'content'):
        if _text(row.get(key)):
            yield row[key], 'unknown', key
    for key in ('abstract', 'title'):
        if _text(paper.get(key)):
            yield paper[key], key, ('paper.' if paper is not row else '') + key


def build_evidence_items(raw_records, query_specs, mappings=None):
    """Build the public schema from per-call raw rows and matching QuerySpecs.

    Source aliases are joined before passage deduplication, so a paper ID and a
    corpus ID may identify one source. Text bytes and snippet type stay distinct.
    Every occurrence retains its original raw row and query/goal association.
    """
    if len(raw_records) != len(query_specs):
        raise ValueError('raw retrieval batches do not match executed queries')
    mappings = mappings or {}
    parents = {}
    def root(key):
        parents.setdefault(key, key)
        if parents[key] != key:
            parents[key] = root(parents[key])
        return parents[key]
    for batch in raw_records:
        for row in _accepted(batch):
            row = _original(row)
            if not isinstance(row, dict):
                continue
            aliases = _source(row)[1]
            for alias in aliases[1:]:
                parents[root(alias)] = root(aliases[0])
    items = {}
    for batch, spec in zip(raw_records, query_specs):
        task = mappings.get(spec.query_id, {})
        query_id = task.get('id', spec.query_id)
        goals = list(task.get('subgoal_ids') or [spec.facet_id])
        for rank, row in enumerate(_accepted(batch)):
            original_field = 'raw_metadata.' if isinstance(row, dict) and row.get('raw_metadata') else ''
            row = _original(row)
            raw_id = f"call:{batch['call_index']}:row:{rank}"
            paper, aliases, url, doi = _source(row) if isinstance(row, dict) else ({}, [], '', '')
            source_id = root(aliases[0]) if aliases else ''
            passages = list(_passages(row)) if isinstance(row, dict) else []
            for text, kind, field in passages or [('', 'unknown', '')]:
                identity = [source_id or raw_id, kind, text]
                evidence_id = 'E_' + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
                if evidence_id not in items:
                    missing = ([] if source_id else ['missing_source']) + ([] if text else ['missing_original_text'])
                    items[evidence_id] = dict(evidence_id=evidence_id, source_id=source_id,
                        source_url=url, title=paper.get('title') or '', authors=paper.get('authors') or [],
                        year=paper.get('year'), doi=doi, text=text, snippet_type=kind,
                        query_ids=[], goal_ids=[], source_mappings=[], eligible_as_evidence=not missing,
                        missing_reasons=missing)
                item = items[evidence_id]
                for name, values in [('query_ids', [query_id]), ('goal_ids', goals)]:
                    item[name].extend(v for v in values if v not in item[name])
                item['source_mappings'].append(dict(query_id=query_id, execution_query_id=spec.query_id,
                    query_text=spec.query_text, goal_ids=goals, raw_record_id=raw_id,
                    provider='semantic_scholar', retrieval_source=spec.retrieval_source, text_field=original_field + field,
                    source_ids=aliases, source_url=url))
                # Complement missing metadata without overwriting observed values.
                for key, value in [('source_url', url), ('title', paper.get('title')), ('authors', paper.get('authors')),
                                   ('year', paper.get('year')), ('doi', doi)]:
                    if not item[key] and value:
                        item[key] = value
    return {'schema_version': SCHEMA, 'items': list(items.values())}
