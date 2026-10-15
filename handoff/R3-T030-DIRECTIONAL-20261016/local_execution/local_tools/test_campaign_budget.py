import importlib.util
from pathlib import Path
import sqlite3
import sys
from decimal import Decimal
import pytest
from campaign_budget import authorize_existing_pool, guard_class, digest_rows

spec = importlib.util.spec_from_file_location('t030_guard220_fixture', Path(__file__).with_name('task_budget_guard_220.py'))
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


class NativeFixture:
    def __init__(self, path):
        self.path = path
        self.limit_cny = 220
        self.reservations = []

    def settle(self, *args, **kwargs):
        raise AssertionError('no billing settlement in fixture')

    def as_dict(self):
        return {}


def ledger(path, cap, amount, identity):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE budget_meta(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('INSERT INTO budget_meta VALUES (?,?)', ('limit_cny', str(cap)))
        columns = sorted(base._REQUIRED_NATIVE_COLUMNS)
        db.execute('CREATE TABLE reservations (' + ','.join(x+' '+('REAL' if x in {'amount_cny','actual_cny','created_at'} else 'TEXT')+(' PRIMARY KEY' if x=='reservation_id' else '') for x in columns) + ')')
        db.execute('INSERT INTO reservations(reservation_id,call_id,status,amount_cny,actual_cny) VALUES (?,?,?,?,?)', (identity,identity,'settled',amount,amount))


def setup_guard(tmp_path, oldcost=0, poolcost=0, case_ids=None):
    old, pool = tmp_path/'original.sqlite.fixture', tmp_path/'pool.sqlite.fixture'
    ledger(old,140,oldcost,'old');ledger(pool,220,poolcost,'pool')
    history={'case_id':'e2e-11-rgb-nir-splitters','attribution_complete':True,
             'case_reservation_ids':case_ids or [],'all_reservation_ids_at_audit':['old','pool']}
    cls=guard_class(base.TaskBudgetGuard,old,history,tmp_path/'audit')
    money=cls(NativeFixture(pool),task_id='R3-T030',lane='directional_continuation',task_limit_cny=8,max_calls=2,global_limit_cny=220)
    return old,pool,money


def reserve(money,n=1,amount=0.1):
    return money._reserve('request'+str(n),str(n)*64,amount,'call'+str(n))


def test_project220_combines_both_original_ledgers(tmp_path):
    old,pool,m=setup_guard(tmp_path,219.9,0.05)
    with pytest.raises(RuntimeError,match='project cumulative220'):reserve(m)
    with sqlite3.connect(pool) as db:assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0]==1


def test_original_case8_includes_historical_other_ledger(tmp_path):
    old,pool,m=setup_guard(tmp_path,7.9,0,['old'])
    with pytest.raises(RuntimeError,match='original case cumulative8'):reserve(m,amount=0.2)


def test_unknown_zero_hold_blocks_paid_request(tmp_path):
    old,pool,m=setup_guard(tmp_path)
    with sqlite3.connect(old) as db:db.execute("UPDATE reservations SET status='uncertain',amount_cny=0")
    with pytest.raises(RuntimeError,match='unresolved'):reserve(m)


def test_new_unattributed_activity_blocks_paid_request(tmp_path):
    old,pool,m=setup_guard(tmp_path)
    with sqlite3.connect(old) as db:db.execute("INSERT INTO reservations(reservation_id,call_id,status,amount_cny,actual_cny) VALUES('new','new','settled',0,0)")
    with pytest.raises(RuntimeError,match='unattributed'):reserve(m)


def test_native_max2_and_request_identity_survive_220_revision(tmp_path):
    old,pool,m=setup_guard(tmp_path)
    before=old.read_bytes()
    for n in (1,2):
        reserve(m,n)
        with sqlite3.connect(pool) as db:db.execute("UPDATE reservations SET status='settled',actual_cny=0.05 WHERE call_id=?",('call'+str(n),))
    with pytest.raises(base.TaskBudgetError,match='task_max_calls_exceeded'):reserve(m,3)
    assert old.read_bytes()==before


def test_authorized_metadata_change_preserves_native_history(tmp_path):
    p=tmp_path/'pool.sqlite.fixture';ledger(p,20,0.3,'historic')
    with sqlite3.connect(p) as db:before=digest_rows(db)
    r=authorize_existing_pool(p,{'project_cap_cny':220,'per_original_case_aggregate_cap_cny':8,'direct_human_confirmation':'确认220元，并解除本批原15/20元限额'})
    with sqlite3.connect(p) as db:
        assert digest_rows(db)==before
        assert db.execute("SELECT value FROM budget_meta WHERE key='limit_cny'").fetchone()[0]=='220'
        assert db.execute('SELECT COUNT(*) FROM r3_budget_authorizations').fetchone()[0]==1
    assert r['new_pool'] is False


def test_original_writer_locked_through_native_pool_reservation(tmp_path):
    old,pool,m=setup_guard(tmp_path)
    m._aggregate_amount=Decimal('0.1')
    try:
        with m._transaction():
            with sqlite3.connect(old,timeout=0.01) as contender:
                with pytest.raises(sqlite3.OperationalError,match='locked'):
                    contender.execute("UPDATE budget_meta SET value='999'")
    finally:m._aggregate_amount=None
