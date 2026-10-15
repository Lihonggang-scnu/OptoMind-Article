#!/usr/bin/env python3
"""Independent Review-v2 planning/retrieval entrypoint. No downstream stages."""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import math
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.request
import json
import sys
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / 'vendor' / 'review'
# This is a standalone process, deliberately not an in-process Article plugin.
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(VENDOR))
import task_sheet as ts
from evidence_store import build_evidence_items, serialize_record
from optomind_research.runtime.upgrade3 import query_plan as qp
from optomind_research.s2_key_router import S2KeyRouter
from optomind_research.runtime.upgrade3.candidate_corpus import (
    SnippetTextStore, build_candidate_corpus, plan_queries as _vendor_plan_queries,
)


def plan_queries(plan):
    mappings = plan.get('task_query_mappings', {})
    return [replace(q, query_text=mappings.get(q.query_id, {}).get('query', q.query_text))
            for q in _vendor_plan_queries(plan)]


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def runtime_source_provenance(source_file=None):
    """Report this runner's checkout, not the vendored Review origin revision.

    Detached exports may have no Git metadata. In that case commit/dirty are
    unknown, while the actual runner bytes remain fingerprinted. Never borrow a
    containing unrelated repository's HEAD or guess a release commit.
    """
    source = Path(source_file or __file__).resolve()
    result = {'source_commit': None, 'source_worktree_dirty': None,
              'runner_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'vendor_review_origin_commit': '775329cee02d4dacd108bf41969b1d21d26750e0'}
    def git(*args):
        return subprocess.run(['git', '-C', str(source.parent), *args],
            check=True, capture_output=True, text=True, timeout=3).stdout.strip()
    try:
        top = Path(git('rev-parse', '--show-toplevel')).resolve()
        source.relative_to(top)
        git('ls-files', '--error-unmatch', '--', source.name)
        commit = git('rev-parse', 'HEAD')
        if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
            return result
        # No pathspec: report the whole checkout, including nonignored untracked
        # modules, rather than silently excluding configuration outside focused/.
        dirty = git('status', '--porcelain', '--untracked-files=normal')
        result.update(source_commit=commit, source_worktree_dirty=bool(dirty))
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return result


def validate_limits(limit, seconds):
    if not 1 <= limit <= 200:
        raise ValueError('per-query limit must be 1..200')
    if not 1 <= seconds <= 1800:
        raise ValueError('retrieval seconds must be 1..1800')


def prepare_output(output):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('output directory must be empty; use a new directory for each stage/run')
    output.mkdir(parents=True, exist_ok=True)
    return output


def generate_plan(question, output, planner=None):
    if not question.strip():
        raise ValueError('question must not be empty')
    if planner is None:
        from optomind_research.query_planner import QueryPlannerAgent
        planner = QueryPlannerAgent(max_tokens=7000)
    outcome = ts.generate(question, planner)
    if any(u.get('mock_llm') or u.get('mock') for u in outcome.get('round_usages', [])):
        outcome['status'] = 'mock_rejected'
    transport = outcome.get('transport') or {}
    if transport.get('fallback_used') or transport.get('transport_error'):
        outcome['status'] = 'transport_rejected'
    plan = outcome.get('plan') or {}
    write_json(output / 'planning.json', outcome)
    write_json(output / 'query_plan.json', plan)
    write_json(output / 'task_sheet.json', outcome.get('task_sheet', {}))
    write_json(output / 'subproblems.json', [
        {'facet_id': f['id'], 'question': f['ask']} for f in plan.get('facets', [])
    ])
    write_json(output / 'queries.json', [dict(vars(q), task_query=plan.get('task_query_mappings', {}).get(q.query_id)) for q in plan_queries(plan)])
    return outcome


S2_MAX_RECONNECTS = 15


def retry_after_seconds(value, *, wall_time=None):
    """Parse the server's full delay, without the vendored transport's 120s cap."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(str(value)).timestamp() - (
                time.time() if wall_time is None else wall_time)
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class RetryAfterOpener:
    """Capture only safe rate-limit metadata; never retain headers or credentials."""
    def __init__(self, opener=urllib.request.urlopen):
        self.opener = opener
        self.retry_after = None
        self.request_count = 0

    def __call__(self, *args, **kwargs):
        self.request_count += 1
        try:
            return self.opener(*args, **kwargs)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                delay = retry_after_seconds(exc.headers.get('Retry-After'))
                if delay is not None:
                    self.retry_after = max(self.retry_after or 0.0, delay)
            raise


class FocusedS2KeyRouter(S2KeyRouter):
    """The outer focused-query loop is the sole owner of 429 cooldowns.

    Keep vendor lane acquisition, pacing, and authentication quarantine intact.
    Its additional exponential 429 penalty would otherwise double-wait and
    consume the active deadline after our full Retry-After wait.
    """
    def rate_limit_cool_lane(self, lane, base_seconds):
        pass


def make_s2_gateway(output, *, opener=None, sleep=None, clock=None):
    from optomind_research.s2_intelligence_gateway import S2IntelligenceGateway, S2Transport
    capture = RetryAfterOpener(opener or urllib.request.urlopen)
    transport = S2Transport(cache_path=output / 'provider_cache.sqlite',
        timeout_seconds=30, max_attempts=1, opener=capture, sleep_fn=sleep or time.sleep)
    transport.router = FocusedS2KeyRouter(transport.keys,
        min_interval_seconds=transport.min_interval_seconds, max_attempts=1,
        sleep_fn=sleep or time.sleep, now_fn=clock or time.monotonic,
        reserve_fn=transport._reserve_lane)
    gateway = S2IntelligenceGateway(transport)
    gateway._focused_retry_capture = capture
    return gateway


class AuditedGateway:
    """Bound and audit the real gateway; one failed query cannot erase other hits."""
    def __init__(self, gateway, *, deadline, query_specs=None,
                 clock=None, sleep=None, checkpoint=None):
        self.gateway = gateway
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.checkpoint = checkpoint
        self.retry_progress = []
        self.query_specs = query_specs
        self.deadline = deadline
        self.calls = []
        self.query_cache = {}
        self.raw_records = []

    def _search(self, method, query, *, limit, **kwargs):
        if self.query_specs is not None:
            if len(self.calls) >= len(self.query_specs):
                raise ValueError('unexpected extra provider call')
            spec = self.query_specs[len(self.calls)]
            expected = 'search_snippets' if spec.retrieval_source == 'snippet_search' else 'search_papers'
            if method != expected or query != spec.query_text.strip():
                raise ValueError('provider call differs from validated query sequence')
            query = spec.query_text
        started = self.clock()
        identity = (method, query, limit)
        if identity in self.query_cache:
            rows, response, original_index = self.query_cache[identity]
            self.calls.append({**self.calls[original_index], 'query': query,
                'deduplicated_query': True, 'reused_call_index': original_index,
                'elapsed_seconds': 0.0, 'attempt_count': 0, 'reconnect_count': 0,
                'cooldown_seconds': 0.0, 'http_requests': 0, 'attempts': []})
            self.raw_records.append({'call_index': len(self.calls) - 1, 'rows':
                self.raw_records[original_index]['rows'], 'reused_call_index': original_index,
                'accepted_limit': limit, 'returned': len(self.raw_records[original_index]['rows'])})
            return rows, response
        attempts = []
        cooldown_total = 0.0
        retained_rows = []
        exhausted = False
        capture = getattr(self.gateway, '_focused_retry_capture', None)
        for retry_index in range(S2_MAX_RECONNECTS + 1):
            attempt_started = self.clock()
            if capture is not None:
                capture.retry_after = None
                capture.request_count = 0
            if attempt_started >= self.deadline:
                rows, response = [], SimpleNamespace(ok=False, status_code=0,
                    status_category='deadline_exceeded', cache_hit=False, audit={})
            else:
                try:
                    rows, response = getattr(self.gateway, method)(
                        query, limit=limit, deadline=self.deadline, **kwargs)
                except Exception as exc:
                    # Exceptions may contain request headers/secrets; persist only class.
                    response = getattr(exc, 'response', None)
                    if response is None:
                        response = SimpleNamespace(ok=False,
                            status_code=getattr(exc, 'code', 0),
                            status_category='exception:' + type(exc).__name__,
                            cache_hit=False, audit={})
                        if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
                            response.audit['retry_after_seconds'] = retry_after_seconds(
                                exc.headers.get('Retry-After'))
                    rows = []
            attempt_audit = getattr(response, 'audit', {}) or {}
            status = getattr(response, 'status_code', 0)
            rate_limited = status == 429 or any(
                page.get('status_code') == 429 for key in ('page_runs', 'batch_runs')
                for page in attempt_audit.get(key, []))
            # Preserve partial pages if a later attempt fails before returning them.
            if len(rows) > len(retained_rows):
                retained_rows = rows
            attempt = {'attempt': retry_index + 1, 'reconnect': retry_index,
                'status_code': status, 'ok': bool(getattr(response, 'ok', False)),
                'rate_limited': rate_limited, 'returned': len(rows),
                'status_category': getattr(response, 'status_category', ''),
                'page_runs': attempt_audit.get('page_runs', []),
                'batch_runs': attempt_audit.get('batch_runs', []),
                'http_requests': capture.request_count if capture is not None else None,
                'elapsed_seconds': round(self.clock() - attempt_started, 3),
                'cooldown_seconds': 0.0}
            attempts.append(attempt)
            delay = None
            if rate_limited:
                delay = capture.retry_after if capture is not None else None
                if delay is None:
                    delay = retry_after_seconds(attempt_audit.get('retry_after_seconds'))
                if delay is None:
                    delay = retry_after_seconds(attempt_audit.get('retry_after'))
                attempt['cooldown_source'] = 'retry_after' if delay is not None else 'exponential'
                if delay is None:
                    delay = min(60.0, 5.0 * (2 ** retry_index))
                exhausted = retry_index == S2_MAX_RECONNECTS
                attempt['retry_exhausted'] = exhausted
                if not exhausted:
                    attempt['cooldown_seconds'] = delay
            progress = {'call_index': len(self.calls), 'method': method, 'query': query,
                'requested_limit': limit, 'attempts': attempts,
                'retry_exhausted': exhausted,
                'complete': bool(getattr(response, 'ok', False)) and not rate_limited
                    and not attempt_audit.get('truncated_reason'),
                'rows': [serialize_record(r) for r in
                    (rows if getattr(response, 'ok', False) and not rate_limited else retained_rows)]}
            if self.checkpoint is not None:
                self.checkpoint(self.retry_progress + [progress])
            if not rate_limited or exhausted:
                if rate_limited:
                    self.deadline += self.clock() - attempt_started
                break
            # A 429 is recovery time, not active retrieval work. Neither its request
            # latency nor the full server-mandated cooldown spends the active budget.
            self.sleep(delay)
            cooldown_total += delay
            self.deadline += self.clock() - attempt_started
        self.retry_progress.append(progress)
        if rate_limited:
            rows = retained_rows
            response.ok = False
            response.status_category = 'rate_limit_retry_exhausted'
        elif not getattr(response, 'ok', False) and retained_rows:
            rows = retained_rows
        audit = getattr(response, 'audit', {}) or {}
        reason = str(getattr(response, 'status_category', '') or '')
        ok = bool(getattr(response, 'ok', False))
        truncated = bool(audit.get('truncated_reason'))
        self.calls.append({
            'provider': 'semantic_scholar', 'method': method, 'query': query,
            'requested_limit': limit, 'returned': min(len(rows), limit),
            'provider_returned': len(rows), 'ok': ok,
            'status_code': getattr(response, 'status_code', 0),
            'status_category': reason,
            'cache_hit': bool(getattr(response, 'cache_hit', False)),
            'deduplicated_query': False,
            'partial': not ok or 'partial' in reason or truncated or len(rows) > limit,
            'missing_reason': reason if not ok or 'partial' in reason or truncated else
                ('no_results' if not rows else ''),
            'page_count': audit.get('page_count'),
            'page_runs': audit.get('page_runs', []),
            'provider_total': audit.get('provider_total'),
            'truncated_reason': audit.get('truncated_reason', ''),
            'elapsed_seconds': round(self.clock() - started, 3),
            'attempt_count': len(attempts), 'reconnect_count': len(attempts) - 1,
            'max_reconnects': S2_MAX_RECONNECTS, 'retry_exhausted': exhausted,
            'cooldown_seconds': cooldown_total, 'attempts': attempts,
            'http_requests': sum(a['http_requests'] for a in attempts) if capture is not None else None,
        })
        self.raw_records.append({'call_index': len(self.calls) - 1,
            'rows': [serialize_record(row) for row in rows],
            'accepted_limit': limit, 'returned': len(rows)})
        bounded = rows[:limit]
        if ok and not self.calls[-1]['partial']:
            self.query_cache[identity] = (bounded, response, len(self.calls) - 1)
        return bounded, response

    def search_papers(self, query, **kwargs):
        return self._search('search_papers', query, **kwargs)

    def search_snippets(self, query, **kwargs):
        return self._search('search_snippets', query, **kwargs)


def retrieve(plan, output, *, gateway=None, per_query_limit=10, seconds=300):
    # 未来方向（尚未实现）：先检查本地文献原文/解析缓存与科研经验，再按缺口外检。
    # 现有 provider/parser 缓存不等于科研经验库；复用须匹配条件，不能照搬历史结论。
    # 仅记录讨论入口，不改变本次 T022 范围；见 FUTURE_LOCAL_EXPERIENCE_MEMO.md。
    validate_limits(per_query_limit, seconds)
    if plan.get('schema_version') == ts.SCHEMA:
        plan = ts.adapt(plan)
    if plan.get('schema_version') == ts.ADAPTER_SCHEMA:
        ts.validate_adapter(plan)
    else:
        report = qp.validate_plan(plan)
        # Legacy replay only; reject any truncation or silent normalization.
        if not report['ok'] or report['plan'] != plan:
            raise ValueError('retrieval requires the exact validated focused query_plan.json')
    mappings = plan.get('task_query_mappings', {})
    if 'task_sheet' in plan:
        write_json(output / 'task_sheet.json', plan['task_sheet'])
    write_json(output / 'query_plan.json', plan)
    write_json(output / 'queries.json', [dict(vars(q), task_query=mappings.get(q.query_id)) for q in plan_queries(plan)])
    if gateway is None:
        gateway = make_s2_gateway(output)
    adapter = AuditedGateway(gateway, deadline=time.monotonic() + seconds,
        query_specs=plan_queries(plan),
        checkpoint=lambda progress: write_json(output / 'provider_retry_checkpoint.json', progress))
    store = SnippetTextStore(output / 'snippets.sqlite')
    try:
        corpus = build_candidate_corpus(plan, gateway=adapter, per_query_limit=per_query_limit,
            enrich=False, snippet_store=store, snippet_discovery_target=0,
            allow_degraded=plan.get('schema_version') == ts.ADAPTER_SCHEMA)
        if plan.get('schema_version') == ts.ADAPTER_SCHEMA:
            corpus['legacy_channel_diagnostic'] = corpus['input_state']
            corpus['input_state'] = {
                'planner_status': 'validated_task_sheet', 'plan_state': 'complete',
                'degraded': False, 'channel_selection': 'intentional_per_query',
                'note': 'Task sheets do not require both endpoints; legacy dual-channel diagnostic retained separately.'}
        evidence = build_evidence_items(adapter.raw_records, plan_queries(plan), mappings)
        write_json(output / 'evidence_items.json', evidence)
        write_json(output / 'raw_retrieval_records.json', adapter.raw_records)
        # Preserve task provenance on hits/audits as well as the executable query list.
        for row in corpus['retrieval_hits'] + corpus['query_audit']:
            if row['query_id'] in mappings:
                row['task_query'] = mappings[row['query_id']]
                row['query_text'] = row['task_query']['query']
        # Produce readable retrieved materials in addition to the original hash/locator store.
        papers = {p['paper_id'] or ('corpus:' + str(p['corpus_id'])): p for p in corpus['papers']}
        materials = []
        for hit in corpus['retrieval_hits']:
            paper = papers.get(hit['paper_id'], {})
            snippet = store.get(hit['hit_id'])
            abstract = paper.get('abstract', '')
            materials.append({**hit, 'provider': 'semantic_scholar',
                'material_type': 'snippet' if snippet else ('abstract' if abstract else 'metadata'),
                'text': snippet or abstract or '', 'title': paper.get('title', ''),
                'doi': paper.get('doi', ''), 'fulltext_retrieved': False,
                'missing_text_reason': '' if snippet or abstract else 'provider_returned_metadata_only'})
        # A unique material can support multiple query/facet mappings without repeating its text.
        unique_materials = {}
        for material in materials:
            identity = (material['paper_id'], material['material_type'], material['text'])
            material_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            material['material_id'] = material_id
            if material_id not in unique_materials:
                unique_materials[material_id] = {'material_id': material_id,
                    'paper_id': material['paper_id'], 'material_type': material['material_type'],
                    'text': material['text'], 'title': material['title'], 'doi': material['doi'],
                    'fulltext_retrieved': False, 'source_mappings': []}
            unique_materials[material_id]['source_mappings'].append({key: material[key] for key in
                ('hit_id', 'facet_id', 'query_id', 'query_text', 'retrieval_source', 'provider',
                 'snippet_locator', 'text_hash')})
            if 'task_query' in material:
                unique_materials[material_id]['source_mappings'][-1]['task_query'] = material['task_query']
        write_json(output / 'unique_materials.json', list(unique_materials.values()))
        # Query occurrences and evidence passages remain separate; paper identity is deduped upstream.
        for audit, call in zip(corpus['query_audit'], adapter.calls):
            audit['provider_response'] = call
            if audit['query_id'] in mappings:
                call['task_query'] = mappings[audit['query_id']]
        unsupported_filters = [f['id'] for f in plan['facets'] if any(f['filters'].values())]
        constraints = list(plan.get('additional_constraints') or [])
        constraints.extend(plan.get('criteria', {}).get('must_exclude_domain') or [])
        for facet in plan['facets']:
            constraints.extend(facet.get('must_exclude') or [])
        partial = any(c['partial'] for c in adapter.calls) or bool(unsupported_filters or constraints)
        missing_facets = sorted(set(f['id'] for f in plan['facets']) -
                                set(h['facet_id'] for h in corpus['retrieval_hits']))
        status = 'partial' if partial or missing_facets else 'complete'
        if not corpus['retrieval_hits']:
            status = 'failed' if partial else 'no_results'
        corpus['focused_status'] = status
        corpus['filter_execution'] = {'applied': False, 'facets_with_unapplied_filters': unsupported_filters,
            'unapplied_additional_constraints': constraints,
            'task_sheet_constraints_for_later_evaluation': plan.get('task_sheet', {}).get('explicit_constraints', []),
            'note': 'Candidate retrieval only; explicit constraints preserved for later acceptance, not asserted satisfied.'}
        corpus['configuration']['corpus_target'] = None
        corpus['configuration']['focused_maximum_hits'] = len(plan_queries(plan)) * per_query_limit
        write_json(output / 'candidate_corpus.json', corpus)
        write_json(output / 'materials.json', materials)
        write_json(output / 'provider_calls.json', adapter.calls)
        summary = {'status': status, 'stage': 'retrieval', 'plan_identity': qp.plan_identity(plan),
            'subproblems': len(plan['facets']), 'queries': len(plan_queries(plan)),
            'papers': len(corpus['papers']), 'hits': len(materials),
            'unique_materials': len(unique_materials),
            'evidence_items': len(evidence['items']),
            'eligible_evidence_items': sum(i['eligible_as_evidence'] for i in evidence['items']),
            'ineligible_evidence_items': sum(not i['eligible_as_evidence'] for i in evidence['items']),
            'missing_facets': missing_facets, 'fulltext_retrieved': False,
            'per_query_limit': per_query_limit, 'retrieval_seconds': seconds,
            'retrieval_budget_scope': 'active work excluding 429 recovery requests and cooldown',
            'max_s2_reconnects_per_query': S2_MAX_RECONNECTS,
            'rate_limit_exhausted_queries': sum(c.get('retry_exhausted', False) for c in adapter.calls),
            'task_sheet_contract': plan.get('task_sheet', {}).get('schema_version'),
            'relevance_validated': False, 'constraints_evaluated': False,
            **runtime_source_provenance()}
        write_json(output / 'summary.json', summary)
        return summary
    finally:
        write_json(output / 'raw_retrieval_records.json', adapter.raw_records)
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['plan', 'retrieve', 'run'])
    parser.add_argument('--question')
    parser.add_argument('--plan-file', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--per-query-limit', type=int, default=10)
    parser.add_argument('--retrieval-seconds', type=float, default=300)
    args = parser.parse_args(argv)
    try:
        validate_limits(args.per_query_limit, args.retrieval_seconds)
        if args.stage in ('plan', 'run') and not args.question:
            parser.error('--question is required for plan/run')
        if args.stage == 'retrieve' and not args.plan_file:
            parser.error('--plan-file is required for retrieve')
        output = prepare_output(args.output)
        write_json(output / 'provenance.json', json.loads((ROOT / 'PROVENANCE.json').read_text()))
        if args.stage in ('plan', 'run'):
            outcome = generate_plan(args.question, output)
            if outcome['status'] != 'ok':
                write_json(output / 'summary.json', {'status': 'failed', 'stage': 'planning',
                    'reason': outcome['status']})
                print('Planning failed; inspect planning.json. Retrieval was not started.')
                return 2
            plan = outcome['plan']
        else:
            plan = json.loads(args.plan_file.read_text(encoding='utf-8'))
            write_json(output / 'query_plan.json', plan)
        if args.stage == 'plan':
            summary = {'status': 'planned', 'stage': 'planning', 'plan_identity': qp.plan_identity(plan)}
            write_json(output / 'summary.json', summary)
        else:
            summary = retrieve(plan, output, per_query_limit=args.per_query_limit, seconds=args.retrieval_seconds)
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary['status'] in ('complete', 'planned') else 2
    except (ValueError, OSError, ImportError) as exc:
        print('Focused retrieval stopped: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
