"""Synthetic SQLite fixtures only; never open project ledgers or call models."""
import hashlib
import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import pytest
import aggregate_budget as agg

GUARD = Path(r'D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T001-INTEGRATE-OFFLINE\release\local_tools\task_budget_guard.py')
spec = importlib.util.spec_from_file_location('t029_fixture_base', GUARD)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def database(path, cap, initial):
    cols = ','.join(name + (' REAL' if name in {'amount_cny','actual_cny','created_at'} else ' TEXT')
                    for name in sorted(mod._REQUIRED_NATIVE_COLUMNS))
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE budget_meta (key TEXT,value TEXT)')
        db.execute('INSERT INTO budget_meta VALUES (?,?)', ('limit_cny', str(cap)))
        db.execute('CREATE TABLE reservations (' + cols + ', UNIQUE(reservation_id))')
        if initial:
            db.execute('INSERT INTO reservations(reservation_id,call_id,status,amount_cny,actual_cny) '
                       "VALUES ('old','old','settled',?,?)", (initial,initial))


def fixture(tmp, old=17, pool_used=0):
    original, pool = tmp/'original.fixture.sqlite', tmp/'pool.fixture.sqlite'
    database(original,140,old);database(pool,20,0)
    native = SimpleNamespace(path=pool,limit_cny=20,settle=lambda *a,**k:None,
                             as_dict=lambda:{},reservations=[])
    cls=agg.guard_class(mod.TaskBudgetGuard,original,tmp/'audit')
    g=cls(native,task_id='R3-T029:fixture',lane='fixture',task_limit_cny=8,max_calls=4,global_limit_cny=20)
    if pool_used:
        with sqlite3.connect(pool) as db:
            db.execute("INSERT INTO r3_task_budgets VALUES ('R3-T028','fixture','2',1,NULL,'20',1,0)")
            db.execute("INSERT INTO reservations(reservation_id,call_id,status,amount_cny,actual_cny) VALUES ('past','past','settled',?,?)",(pool_used,pool_used))
            db.execute("INSERT INTO r3_task_calls VALUES ('R3-T028','past',?,'past','past',0)",('a'*64,))
    return g,original,pool


def reserve(g,n=1):return g.for_request('request','a'*64).reserve(n,'call')


def test_native_reservation_original_bytes_preserved(tmp_path):
    g,original,pool=fixture(tmp_path);before=hashlib.sha256(original.read_bytes()).hexdigest()
    r=reserve(g);assert r['status']=='reserved'
    assert hashlib.sha256(original.read_bytes()).hexdigest()==before
    with sqlite3.connect(pool) as db:assert db.execute('SELECT count(*) FROM r3_task_calls').fetchone()[0]==1


def test_project_120_blocks_native_140_allowance(tmp_path):
    g,original,pool=fixture(tmp_path,old=119)
    with pytest.raises(RuntimeError,match='project'):reserve(g,2)
    with sqlite3.connect(pool) as db:assert db.execute('SELECT count(*) FROM reservations').fetchone()[0]==0


def test_existing_chain15_blocks(tmp_path):
    g,original,pool=fixture(tmp_path,pool_used=14.9)
    with pytest.raises(RuntimeError,match='chain'):reserve(g,.2)


def test_original_zero_unknown_hold_blocks(tmp_path):
    g,original,pool=fixture(tmp_path)
    with sqlite3.connect(original) as db:db.execute("INSERT INTO reservations(reservation_id,call_id,status,amount_cny) VALUES ('unknown','unknown','uncertain',0)")
    with pytest.raises(RuntimeError,match='unresolved'):reserve(g)


def test_case8_native_guard_remains_effective(tmp_path):
    g,original,pool=fixture(tmp_path)
    with pytest.raises(mod.TaskBudgetError,match='task_budget_exceeded'):reserve(g,8.1)


def test_original_writer_locked_through_check_and_reservation(tmp_path,monkeypatch):
    g,original,pool=fixture(tmp_path);prior=agg.checked_totals;observed=[]
    def check(old,pooldb,amount):
        with sqlite3.connect(original,timeout=.01) as other:
            with pytest.raises(sqlite3.OperationalError,match='locked'):other.execute('BEGIN IMMEDIATE')
        observed.append(True);return prior(old,pooldb,amount)
    monkeypatch.setattr(agg,'checked_totals',check);reserve(g);assert observed==[True]
