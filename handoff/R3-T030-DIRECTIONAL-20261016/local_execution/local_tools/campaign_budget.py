"""Authorized campaign accounting wiring; preserve native reservation/settlement."""
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import sqlite3
import time
import uuid

PROJECT_CAP = Decimal('220')
CASE_CAP = Decimal('8')


def digest_rows(db):
    # All historical reservation columns, including provider usage, are checked.
    rows = [list(r) for r in db.execute('SELECT * FROM reservations ORDER BY reservation_id')]
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def authorize_existing_pool(path, authorization):
    """Change only authorized metadata, appending the original-native audit schema."""
    path = Path(path).resolve(strict=True)
    if authorization.get('project_cap_cny') != 220 or authorization.get('per_original_case_aggregate_cap_cny') != 8:
        raise RuntimeError('explicit campaign220/case8 authorization required')
    if authorization.get('direct_human_confirmation') != '确认220元，并解除本批原15/20元限额':
        raise RuntimeError('direct human confirmation missing')
    with closing(sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=30)) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT COUNT(*) FROM reservations WHERE status IS NULL OR status!='settled'").fetchone()[0]:
            raise RuntimeError('unresolved spend prevents monetary revision')
        old = Decimal(db.execute("SELECT value FROM budget_meta WHERE key='limit_cny'").fetchone()[0])
        if old not in (Decimal(20), PROJECT_CAP):
            raise RuntimeError('unexpected existing pool metadata')
        before = digest_rows(db)
        encoded = json.dumps(authorization, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(encoded.encode()).hexdigest()
        db.execute('CREATE TABLE IF NOT EXISTS r3_budget_authorizations (authorization_sha256 TEXT PRIMARY KEY, previous_limit_cny REAL NOT NULL, limit_cny REAL NOT NULL, audit_json TEXT NOT NULL, created_at REAL NOT NULL)')
        db.execute('INSERT OR IGNORE INTO r3_budget_authorizations VALUES (?,?,?,?,?)',
                   (key, float(old), 220.0, encoded, time.time()))
        db.execute("UPDATE budget_meta SET value='220' WHERE key='limit_cny'")
        after = digest_rows(db)
        if before != after:
            raise RuntimeError('historical reservation mutation refused')
        db.commit()
    return {'authorization_sha256': key, 'previous_native_limit_cny': str(old),
            'current_native_allocation_ceiling_cny': '220', 'shared_project_cap_cny': '220',
            'case_cumulative_cap_cny': '8', 'reservation_rows_before_after_sha256': before,
            'historical_reservation_rows_unchanged': True, 'new_pool': False}


def cost(rows):
    value = Decimal(0)
    for row in rows:
        amount = Decimal(str((row['actual_cny'] if row['status'] == 'settled' else row['amount_cny']) or 0))
        if not amount.is_finite() or amount < 0:
            raise RuntimeError('invalid ledger amount')
        value += amount
    return value


def checked_totals(original, pool, amount, history, task_id):
    if history.get('case_id') != 'e2e-11-rgb-nir-splitters' or not history.get('attribution_complete'):
        raise RuntimeError('complete source-supported original case attribution required')
    original_rows = original.execute('SELECT * FROM reservations').fetchall()
    pool_rows = pool.execute('SELECT * FROM reservations').fetchall()
    if any(r['status'] != 'settled' for r in original_rows + pool_rows):
        raise RuntimeError('unresolved aggregate spend')
    unique = {}
    for row in original_rows + pool_rows:
        identity = row['reservation_id']
        if identity in unique and dict(row) != dict(unique[identity]):
            raise RuntimeError('conflicting duplicate reservation identity')
        unique[identity] = row
    selected = set(history['case_reservation_ids'])
    known = set(history['all_reservation_ids_at_audit'])
    task_rows = pool.execute('SELECT r.* FROM r3_task_calls c JOIN reservations r USING(reservation_id) WHERE c.task_id=?', (task_id,)).fetchall()
    current = {r['reservation_id'] for r in task_rows}
    if not selected.issubset(unique) or not known.issubset(unique):
        raise RuntimeError('historical reservation disappeared')
    if set(unique) - known - current:
        raise RuntimeError('new unattributed activity requires fresh history audit')
    exposure = cost(list(unique.values()))
    case_used = cost([unique[k] for k in selected | current])
    task_used = cost(task_rows)
    if exposure + amount > PROJECT_CAP:
        raise RuntimeError('project cumulative220 exhausted')
    if case_used + amount > CASE_CAP:
        raise RuntimeError('original case cumulative8 exhausted')
    if task_used + amount > CASE_CAP:
        raise RuntimeError('T030 task8 exhausted')
    return {'project_exposure_cny': str(exposure), 'case_exposure_cny': str(case_used),
            'task_exposure_cny': str(task_used), 'reservation_cny': str(amount),
            'project_cap_cny': '220', 'original_case_cap_cny': '8',
            'both_ledgers_locked_across_check_and_native_reservation': True,
            'history_attribution_sha256': hashlib.sha256(json.dumps(history, sort_keys=True).encode()).hexdigest()}


def guard_class(base, original_path, history, audit_dir):
    original_path = Path(original_path).resolve(strict=True)
    audit_dir = Path(audit_dir)

    class CampaignGuard(base):
        _aggregate_amount = None

        @contextmanager
        def _transaction(self):
            if self._aggregate_amount is None:
                with super()._transaction() as pool:
                    yield pool
                return
            with closing(sqlite3.connect(original_path.as_uri() + '?mode=rw', uri=True, timeout=30)) as original:
                original.row_factory = sqlite3.Row
                original.execute('BEGIN IMMEDIATE')
                try:
                    with super()._transaction() as pool:
                        snapshot = checked_totals(original, pool, self._aggregate_amount, history, self.task_id)
                        audit_dir.mkdir(parents=True, exist_ok=True)
                        with (audit_dir / (uuid.uuid4().hex + '.json')).open('x', encoding='utf-8') as f:
                            json.dump(snapshot, f, indent=2)
                            f.write('\n')
                        yield pool
                    # Native pool reservation committed before releasing original lock.
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

    return CampaignGuard
