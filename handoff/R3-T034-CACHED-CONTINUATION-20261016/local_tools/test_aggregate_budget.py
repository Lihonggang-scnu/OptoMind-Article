from pathlib import Path
from decimal import Decimal
import importlib.util
import sqlite3
import sys
import pytest
from aggregate_budget import CASES,guard_class,checked_totals

spec=importlib.util.spec_from_file_location('t031_budget220_fixture',Path(__file__).with_name('task_budget_guard_220.py'));base=importlib.util.module_from_spec(spec);sys.modules[spec.name]=base;spec.loader.exec_module(base)

class Native:
    def __init__(self,path):self.path=path;self.limit_cny=220;self.reservations=[]
    def settle(self,*args,**kwargs):raise AssertionError('fixture only')
    def as_dict(self):return {}

def make(tmp_path,oldcost=0,poolcost=0,case=CASES[0],historical=False):
    a,b=tmp_path/'old.sqlite.fixture',tmp_path/'pool.sqlite.fixture'
    for p,cap,cost,ident in [(a,140,oldcost,'old'),(b,220,poolcost,'pool')]:
        with sqlite3.connect(p) as db:
            db.execute('CREATE TABLE budget_meta(key TEXT PRIMARY KEY,value TEXT)');db.execute('INSERT INTO budget_meta VALUES(?,?)',('limit_cny',str(cap)))
            db.execute('CREATE TABLE reservations('+','.join(k+' '+('REAL' if k in {'amount_cny','actual_cny','created_at'} else 'TEXT')+(' PRIMARY KEY' if k=='reservation_id' else '') for k in sorted(base._REQUIRED_NATIVE_COLUMNS))+')')
            db.execute('INSERT INTO reservations(reservation_id,call_id,status,amount_cny,actual_cny) VALUES(?,?,?,?,?)',(ident,ident,'settled',cost,cost))
    h={'attribution_complete':True,'all_reservation_ids_at_audit':['old','pool'],'case_reservation_ids':{c:(['old','pool'] if c==case and historical else []) for c in CASES}}
    cls=guard_class(base.TaskBudgetGuard,a,h,tmp_path/'audit');m=cls(Native(b),task_id='R3-T031:'+case,lane='recovered_continuation',task_limit_cny=8,max_calls=3,global_limit_cny=220)
    return a,b,h,m

def test_same_original_case_includes_both_historical_ledgers(tmp_path):
    a,b,h,m=make(tmp_path,7.96,0.04,CASES[1],True)
    with pytest.raises(RuntimeError,match='original case cumulative8'):m._reserve('request','a'*64,0.01,'call')

def test_native_three_attempts_cannot_be_reset_by_reconstruction(tmp_path):
    a,b,h,m=make(tmp_path)
    for i in range(3):
        m._reserve(str(i),str(i)*64,0.1,'call'+str(i))
        with sqlite3.connect(b) as db:db.execute("UPDATE reservations SET status='settled',actual_cny=.01 WHERE call_id=?",('call'+str(i),))
    cls=guard_class(base.TaskBudgetGuard,a,h,tmp_path/'audit2');again=cls(Native(b),task_id='R3-T031:'+CASES[0],lane='recovered_continuation',task_limit_cny=8,max_calls=3,global_limit_cny=220)
    with pytest.raises((RuntimeError,base.TaskBudgetError),match='task_max_calls_exceeded|shared T031/T034 original case3'):again._reserve('fourth','f'*64,.1,'fourth')

def test_zero_unknown_hold_blocks_another_independent_case(tmp_path):
    a,b,h,m=make(tmp_path,case=CASES[2])
    with sqlite3.connect(a) as db:db.execute("UPDATE reservations SET status='uncertain',amount_cny=0")
    with pytest.raises(RuntimeError,match='unresolved'):m._reserve('request','a'*64,.1,'call')

def test_final_readonly_reconciliation_valid_at_nine_attempt_limit(tmp_path):
    a,b,h,m=make(tmp_path)
    with sqlite3.connect(b) as db:
        for j,c in enumerate(CASES):
            for i in range(3):
                ident=f'new{j}{i}';db.execute('INSERT INTO reservations(reservation_id,call_id,status,amount_cny,actual_cny) VALUES(?,?,?,?,?)',(ident,ident,'settled',.01,.01));db.execute('INSERT INTO r3_task_budgets(task_id,lane,task_limit_cny,max_calls,checkpoint_cny,global_limit_cny,schema_version,created_at) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(task_id) DO NOTHING',('R3-T031:'+c,'recovered_continuation','8',3,None,'220',1,0));db.execute('INSERT INTO r3_task_calls(task_id,request_key,request_hash,reservation_id,call_id,created_at) VALUES(?,?,?,?,?,?)',('R3-T031:'+c,ident,'a'*64,ident,ident,0))
    with sqlite3.connect(a) as old,sqlite3.connect(b) as pool:
        old.row_factory=pool.row_factory=sqlite3.Row
        assert checked_totals(old,pool,Decimal(0),h,m.task_id,admission=False)['batch_attempts']==9
        with pytest.raises(RuntimeError,match='batch9'):checked_totals(old,pool,Decimal('.1'),h,m.task_id)

def settled_attempt(m, path, number):
    ident='shared'+str(number)
    m._reserve(ident,str(number)*64,.1,ident)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE reservations SET status='settled',actual_cny=.01 WHERE call_id=?",(ident,))

def resumed_guard(a,b,h,tmp_path):
    cls=guard_class(base.TaskBudgetGuard,a,h,tmp_path/'resumed-audit')
    return cls(Native(b),task_id='R3-T034:'+CASES[0],lane='recovered_continuation',task_limit_cny=8,max_calls=3,global_limit_cny=220)

def test_new_task_id_does_not_reset_three_old_continuation_attempts(tmp_path):
    a,b,h,m=make(tmp_path)
    for i in range(3):settled_attempt(m,b,i)
    resumed=resumed_guard(a,b,h,tmp_path)
    with pytest.raises(RuntimeError,match='shared T031/T034 original case3'):
        resumed._reserve('extra','e'*64,.1,'extra')

def test_two_old_one_new_attempt_exhaust_shared_case_limit(tmp_path):
    a,b,h,m=make(tmp_path)
    for i in range(2):settled_attempt(m,b,i)
    resumed=resumed_guard(a,b,h,tmp_path)
    settled_attempt(resumed,b,2)
    with sqlite3.connect(a) as old,sqlite3.connect(b) as pool:
        old.row_factory=pool.row_factory=sqlite3.Row
        summary=checked_totals(old,pool,Decimal(0),h,resumed.task_id,admission=False)
        assert summary['batch_attempts']==3
        assert Decimal(summary['case_exposure_cny'])==Decimal('.03')
    with pytest.raises(RuntimeError,match='shared T031/T034 original case3'):
        resumed._reserve('extra','e'*64,.1,'extra')
