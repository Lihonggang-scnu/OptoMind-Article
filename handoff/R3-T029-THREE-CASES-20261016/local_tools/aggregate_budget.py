"""T029 local wiring: extend the existing reservation guard, never replace billing.

The original ledger receives a lock only and SELECTs. The existing pool ledger
receives the unchanged native guard's reservation and append-only task linkage.
Both ledgers are locked across aggregate checks and that single reservation.
"""
from contextlib import contextmanager, closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import uuid

CHAIN_TASKS = {'R3-T022', 'R3-T025', 'R3-T026', 'R3-T027', 'R3-T028'}
PREFIX = 'R3-T029:'


def cost(rows):
    total = Decimal(0)
    for r in rows:
        value = Decimal(str((r['actual_cny'] if r['status'] == 'settled'
                             else r['amount_cny']) or 0))
        if not value.is_finite() or value < 0:
            raise RuntimeError('invalid historical cost; no new dispatch')
        total += value
    return total


def checked_totals(original, pool, amount):
    if Decimal(original.execute("SELECT value FROM budget_meta WHERE key='limit_cny'").fetchone()[0]) != 140:
        raise RuntimeError('original ledger metadata changed')
    if Decimal(pool.execute("SELECT value FROM budget_meta WHERE key='limit_cny'").fetchone()[0]) != 20:
        raise RuntimeError('existing pool metadata changed')
    old = original.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
    all_pool = pool.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
    if any(r['status'] != 'settled' for r in old + all_pool):
        raise RuntimeError('unresolved original/pool spend; no new dispatch')
    linked = pool.execute('SELECT r.status,r.amount_cny,r.actual_cny,c.task_id '
                          'FROM reservations r JOIN r3_task_calls c USING(reservation_id)').fetchall()
    batch = [r for r in linked if r['task_id'].startswith(PREFIX)]
    chain = [r for r in linked if r['task_id'] in CHAIN_TASKS or r['task_id'].startswith(PREFIX)]
    values = {'project': cost(old) + cost(all_pool), 'pool': cost(all_pool),
              'chain': cost(chain), 'batch': cost(batch)}
    caps = {'project': Decimal(120), 'pool': Decimal(20),
            'chain': Decimal(15), 'batch': Decimal(24)}
    for name, used in values.items():
        if used + amount > caps[name]:
            raise RuntimeError('aggregate budget refused at ' + name)
    return {'exposure_cny': {k: str(v) for k, v in values.items()},
            'caps_cny': {k: str(v) for k, v in caps.items()},
            'reservation_cny': str(amount), 'unresolved_count': 0,
            'both_ledger_write_reservations_held': True,
            'original_ledger_SQL_writes': 0, 'native_pool_reservation_used': True}


def guard_class(base, original_path, audit_dir):
    original_path = Path(original_path).resolve(strict=True)
    audit_dir = Path(audit_dir)

    class AggregateGuard(base):
        _aggregate_amount = None

        @contextmanager
        def _transaction(self):
            amount = self._aggregate_amount
            if amount is None:
                with super()._transaction() as pool:
                    yield pool
                return
            # mode=rw never creates a database. BEGIN IMMEDIATE excludes an
            # original-ledger writer while checking/reserving against the pool.
            # No INSERT/UPDATE/DELETE/DDL is issued against the original ledger.
            with closing(sqlite3.connect(original_path.as_uri() + '?mode=rw',
                                         uri=True, timeout=30)) as original:
                original.row_factory = sqlite3.Row
                original.execute('PRAGMA busy_timeout=30000')
                original.execute('BEGIN IMMEDIATE')
                try:
                    with super()._transaction() as pool:
                        snapshot = checked_totals(original, pool, amount)
                        audit_dir.mkdir(parents=True, exist_ok=True)
                        path = audit_dir / (uuid.uuid4().hex + '.json')
                        with path.open('x', encoding='utf-8') as f:
                            json.dump(snapshot, f, indent=2)
                            f.write('\n')
                            f.flush()
                            import os
                            os.fsync(f.fileno())
                        yield pool
                    # Pool commit precedes release of the original lock; the
                    # recorded reservation is now visible as aggregate exposure.
                finally:
                    original.rollback()

        def _reserve(self, request_key, request_hash, amount_cny, call_id):
            if self._aggregate_amount is not None:
                raise RuntimeError('nested reservation refused')
            self._aggregate_amount = Decimal(str(amount_cny))
            try:
                return super()._reserve(request_key, request_hash, amount_cny, call_id)
            finally:
                self._aggregate_amount = None

    return AggregateGuard
