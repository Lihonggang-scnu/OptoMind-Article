"""Offline regression coverage: no network, real clocks, or real cooldowns."""
import importlib.util
import io
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import urllib.error
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('focused_cooldown_run', ROOT / 'run.py')
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


class Clock:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def response(status=200, **audit):
    return SimpleNamespace(ok=status == 200, status_code=status,
        status_category='ok' if status == 200 else 'availability_delay',
        cache_hit=False, audit=audit)


class Gateway:
    def __init__(self, outcomes, clock=None, latency=0):
        self.outcomes = iter(outcomes)
        self.calls = []
        self.clock, self.latency = clock, latency

    def search_papers(self, query, **kwargs):
        self.calls.append(('search_papers', query, kwargs))
        if self.clock:
            self.clock.now += self.latency
        return next(self.outcomes)

    def search_snippets(self, query, **kwargs):
        self.calls.append(('search_snippets', query, kwargs))
        return next(self.outcomes)


class CooldownTests(unittest.TestCase):
    def adapter(self, outcomes, **kwargs):
        clock = Clock()
        gateway = Gateway(outcomes)
        return run.AuditedGateway(gateway, deadline=300, clock=clock.monotonic,
            sleep=clock.sleep, **kwargs), gateway, clock

    def test_retry_after_preserved_and_success_is_never_rerun(self):
        rows = [{'paperId': 'p1'}]
        adapter, gateway, clock = self.adapter([
            ([], response(429, retry_after_seconds=900)), (rows, response())])
        self.assertEqual(adapter.search_papers(' exact query ', limit=7)[0], rows)
        self.assertEqual(adapter.search_papers(' exact query ', limit=7)[0], rows)
        self.assertEqual(clock.waits, [900])
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(gateway.calls[0][1], gateway.calls[1][1])
        self.assertEqual(gateway.calls[1][2]['limit'], 7)
        self.assertEqual(gateway.calls[1][2]['deadline'], 1200)
        self.assertTrue(adapter.calls[1]['deduplicated_query'])
        self.assertEqual(adapter.calls[0]['reconnect_count'], 1)

    def test_exactly_fifteen_reconnects_not_fifteen_total_attempts(self):
        adapter, gateway, clock = self.adapter([( [], response(429)) for _ in range(16)])
        rows, result = adapter.search_papers('q', limit=4)
        self.assertFalse(result.ok)
        self.assertEqual(result.status_category, 'rate_limit_retry_exhausted')
        self.assertEqual(len(gateway.calls), 16)
        self.assertEqual(len(clock.waits), 15)
        self.assertEqual(clock.waits[:5], [5, 10, 20, 40, 60])
        self.assertTrue(all(wait <= 60 for wait in clock.waits))
        self.assertTrue(adapter.calls[0]['partial'])
        self.assertTrue(adapter.calls[0]['retry_exhausted'])
        self.assertEqual(adapter.query_cache, {})

    def test_success_on_sixteenth_attempt(self):
        adapter, gateway, clock = self.adapter(
            [([], response(429)) for _ in range(15)] + [([], response())])
        _, result = adapter.search_snippets('why?', limit=10)
        self.assertTrue(result.ok)
        self.assertEqual(len(gateway.calls), 16)
        self.assertFalse(adapter.calls[0]['retry_exhausted'])
        self.assertFalse(adapter.calls[0]['partial'])
        self.assertEqual(adapter.calls[0]['missing_reason'], 'no_results')
        self.assertTrue(all(c[0] == 'search_snippets' for c in gateway.calls))

    def test_configuration_failures_are_not_retried(self):
        for code in (400, 401, 403, 404, 422, 500):
            with self.subTest(code=code):
                adapter, gateway, clock = self.adapter([([], response(code))])
                adapter.search_papers('q', limit=1)
                self.assertEqual(len(gateway.calls), 1)
                self.assertEqual(clock.waits, [])

    def test_later_page_429_preserves_partial_rows_on_exhaustion(self):
        rows = [{'paperId': 'p1'}]
        partial = response(200, page_runs=[{'status_code': 200}, {'status_code': 429}],
            truncated_reason='later_page_unavailable')
        adapter, gateway, clock = self.adapter([(rows, partial)] +
            [([], response(429)) for _ in range(15)])
        kept, result = adapter.search_papers('q', limit=200)
        self.assertEqual(kept, rows)
        self.assertFalse(result.ok)
        self.assertEqual(adapter.raw_records[0]['returned'], 1)
        self.assertEqual(adapter.query_cache, {})

    def test_prior_success_survives_other_query_failure(self):
        adapter, gateway, clock = self.adapter([([{'paperId': 'p1'}], response())] +
            [([], response(429)) for _ in range(16)])
        adapter.search_papers('good', limit=2)
        adapter.search_papers('limited', limit=2)
        self.assertEqual(adapter.search_papers('good', limit=2)[0], [{'paperId': 'p1'}])
        self.assertEqual(len(gateway.calls), 17)

    def test_failed_query_is_not_cached_as_empty_success(self):
        adapter, gateway, clock = self.adapter([([], response(401)), ([{'paperId': 'p'}], response())])
        adapter.search_papers('q', limit=1)
        self.assertEqual(adapter.search_papers('q', limit=1)[0], [{'paperId': 'p'}])
        self.assertEqual(len(gateway.calls), 2)

    def test_active_time_budget_excludes_429_request_latency(self):
        clock = Clock()
        gateway = Gateway([([], response(429)), ([], response())], clock, latency=30)
        adapter = run.AuditedGateway(gateway, deadline=10, clock=clock.monotonic, sleep=clock.sleep)
        _, result = adapter.search_papers('q', limit=1)
        self.assertTrue(result.ok)
        self.assertEqual(len(gateway.calls), 2)
        self.assertEqual(gateway.calls[1][2]['deadline'], 45)

    def test_normal_deadline_still_blocks_unstarted_work(self):
        adapter, gateway, clock = self.adapter([])
        clock.now = 301
        _, result = adapter.search_papers('q', limit=1)
        self.assertEqual(result.status_category, 'deadline_exceeded')
        self.assertEqual(len(gateway.calls), 0)

    def test_checkpoint_before_cooldown_retains_success_and_attempts(self):
        checkpoints = []
        adapter, gateway, clock = self.adapter(
            [([], response(429)), ([{'paperId': 'p'}], response())],
            checkpoint=lambda progress: checkpoints.append(progress[-1]['attempts'][-1].copy()))
        adapter.search_papers('q', limit=1)
        self.assertEqual([p['attempt'] for p in checkpoints], [1, 2])
        self.assertEqual(checkpoints[0]['cooldown_seconds'], 5)

    def test_retry_after_seconds_and_http_date_without_120_second_cap(self):
        self.assertEqual(run.retry_after_seconds('7200'), 7200)
        self.assertEqual(run.retry_after_seconds('Thu, 01 Jan 1970 01:00:00 GMT', wall_time=0), 3600)
        self.assertEqual(run.retry_after_seconds('-3'), 0)
        for value in (None, 'nan', 'inf', 'broken'):
            self.assertIsNone(run.retry_after_seconds(value))

    def test_real_vendor_transport_and_gateway_429_to_success(self):
        from optomind_research.s2_intelligence_gateway import S2GatewayResponse
        clock = Clock()
        sent = []
        class Reply(io.BytesIO):
            status = 200
        def opener(request, **kwargs):
            sent.append(request.full_url)
            if len(sent) <= 3:
                raise urllib.error.HTTPError(request.full_url, 429, 'limited',
                    {'Retry-After': '400'}, io.BytesIO(b'limited'))
            return Reply(json.dumps({'data': [{'paperId': 'p1', 'title': 'Result'}]}).encode())
        with tempfile.TemporaryDirectory() as tmp, patch.object(run.time, 'monotonic', clock.monotonic):
            gateway = run.make_s2_gateway(Path(tmp), opener=opener,
                sleep=clock.sleep, clock=clock.monotonic)
            # Isolate test process-global lanes and cache pacing, not request/retry logic.
            gateway.transport.router.reset_process_lanes()
            gateway.transport.router.reserve_fn = lambda *args: 0
            gateway.transport.router.min_interval_seconds = 0
            adapter = run.AuditedGateway(gateway, deadline=300,
                clock=clock.monotonic, sleep=clock.sleep)
            rows, result = adapter.search_papers('precise query', limit=7)
            self.assertIsInstance(result, S2GatewayResponse)
            self.assertTrue(result.ok)
            self.assertEqual(rows[0].paper_id, 'p1')
            self.assertEqual(clock.waits, [400, 400, 400])
            self.assertEqual(len(set(sent)), 1)
            self.assertEqual(gateway.transport.max_attempts, 1)
            self.assertEqual([a['http_requests'] for a in adapter.calls[0]['attempts']], [1, 1, 1, 1])
            self.assertEqual([a['cooldown_source'] for a in adapter.calls[0]['attempts'][:3]],
                ['retry_after'] * 3)
            adapter.search_papers('precise query', limit=7)
            self.assertEqual(len(sent), 4)

    def test_real_gateway_reuses_successful_page_during_retry(self):
        from urllib.parse import parse_qs, urlparse
        clock = Clock()
        offsets = []
        class Reply(io.BytesIO):
            status = 200
        def opener(request, **kwargs):
            offset = int(parse_qs(urlparse(request.full_url).query)['offset'][0])
            offsets.append(offset)
            if offsets == [0, 100]:
                raise urllib.error.HTTPError(request.full_url, 429, 'limited',
                    {'Retry-After': '400'}, io.BytesIO(b'limited'))
            return Reply(json.dumps({'total': 200, 'data': [
                {'paperId': str(i), 'title': str(i)} for i in range(offset, offset + 100)
            ]}).encode())
        with tempfile.TemporaryDirectory() as tmp, patch.object(run.time, 'monotonic', clock.monotonic):
            gateway = run.make_s2_gateway(Path(tmp), opener=opener,
                sleep=clock.sleep, clock=clock.monotonic)
            gateway.transport.router.reset_process_lanes()
            gateway.transport.router.reserve_fn = lambda *args: 0
            gateway.transport.router.min_interval_seconds = 0
            adapter = run.AuditedGateway(gateway, deadline=300,
                clock=clock.monotonic, sleep=clock.sleep)
            rows, result = adapter.search_papers('q', limit=200)
            self.assertTrue(result.ok)
            self.assertEqual(len(rows), 200)
            self.assertEqual(offsets, [0, 100, 100])
            self.assertEqual([a['http_requests'] for a in adapter.calls[0]['attempts']], [2, 1])
            self.assertTrue(adapter.calls[0]['attempts'][0]['rate_limited'])
            self.assertFalse(adapter.calls[0]['partial'])

    def test_opener_captures_only_retry_after_and_counts_wire_requests(self):
        error = urllib.error.HTTPError('https://example.test', 429, 'limited',
            {'Retry-After': '800', 'Secret': 'not retained'}, io.BytesIO())
        def failing(*args, **kwargs):
            raise error
        capture = run.RetryAfterOpener(failing)
        with self.assertRaises(urllib.error.HTTPError):
            capture('url')
        self.assertEqual(capture.retry_after, 800)
        self.assertEqual(capture.request_count, 1)
        self.assertNotIn('headers', vars(capture))

    def test_s2_cache_closes_connections_and_rolls_back_failed_write(self):
        from optomind_research.s2_cache import (
            S2PersistentCache,
            canonical_request_key,
        )

        class TrackingConnection:
            def __init__(self, real, *, fail_after_cache_insert=False):
                self.real = real
                self.fail_after_cache_insert = fail_after_cache_insert
                self.closed = False

            def __enter__(self):
                self.real.__enter__()
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return self.real.__exit__(exc_type, exc_value, traceback)

            def execute(self, sql, *args, **kwargs):
                result = self.real.execute(sql, *args, **kwargs)
                if (self.fail_after_cache_insert and
                        sql.lstrip().startswith('INSERT INTO s2_cache')):
                    self.fail_after_cache_insert = False
                    raise RuntimeError('forced write failure after sqlite insert')
                return result

            def close(self):
                self.closed = True
                return self.real.close()

            def __getattr__(self, name):
                return getattr(self.real, name)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'provider_cache.sqlite'
            returned = []
            original_connect = S2PersistentCache._connect
            fail_next = [False]

            def tracked_connect(cache):
                real = original_connect(cache)
                wrapper = TrackingConnection(real,
                    fail_after_cache_insert=fail_next[0])
                fail_next[0] = False
                returned.append(wrapper)
                return wrapper

            with patch.object(S2PersistentCache, '_connect', tracked_connect):
                cache = S2PersistentCache(db_path)
                ok_key = cache.put('GET', '/ok', {}, {}, status_code=200,
                    payload={'ok': True}, ttl_seconds=60)
                self.assertTrue(returned[-1].closed)
                with self.assertRaises(sqlite3.ProgrammingError):
                    returned[-1].execute('SELECT 1')

                with closing(sqlite3.connect(db_path)) as conn, conn:
                    self.assertIsNotNone(conn.execute(
                        'SELECT cache_key FROM s2_cache WHERE cache_key=?',
                        (ok_key,)).fetchone())

                fail_next[0] = True
                with self.assertRaises(RuntimeError):
                    cache.put('GET', '/error', {}, {}, status_code=200,
                        payload={'error': True}, ttl_seconds=60)
                self.assertTrue(returned[-1].closed)
                with self.assertRaises(sqlite3.ProgrammingError):
                    returned[-1].execute('SELECT 1')

                error_key = canonical_request_key('GET', '/error', {}, {})
                with closing(sqlite3.connect(db_path)) as conn, conn:
                    self.assertIsNone(conn.execute(
                        'SELECT cache_key FROM s2_cache WHERE cache_key=?',
                        (error_key,)).fetchone())

            self.assertTrue(returned)
            self.assertTrue(all(conn.closed for conn in returned))


if __name__ == '__main__':
    unittest.main()
