import importlib.util,sqlite3,sys
from pathlib import Path
from decimal import Decimal
import pytest
from aggregate_budget import CASES,guard_class,checked_totals
spec=importlib.util.spec_from_file_location('native220_fixture',Path(__file__).with_name('task_budget_guard_220.py'));base=importlib.util.module_from_spec(spec);sys.modules[spec.name]=base;spec.loader.exec_module(base)
class Native:
 def __init__(self,p):self.path=p;self.limit_cny=220;self.reservations=[]
 def as_dict(self):return {}
 def settle(self,*a,**kw):raise AssertionError('synthetic fixture only')
def make(tmp_path,oldcost=0,historical=False):
 a,b=tmp_path/'old.sqlite.fixture',tmp_path/'pool.sqlite.fixture'
 for p,cap,cost,ident in [(a,140,oldcost,'old'),(b,220,0,'pool')]:
  with sqlite3.connect(p) as d:
   d.execute('CREATE TABLE budget_meta(key TEXT PRIMARY KEY,value TEXT)');d.execute('INSERT INTO budget_meta VALUES(?,?)',('limit_cny',str(cap)))
   d.execute('CREATE TABLE reservations('+','.join(k+' '+('REAL' if k in {'amount_cny','actual_cny','created_at'} else 'TEXT')+(' PRIMARY KEY' if k=='reservation_id' else '') for k in sorted(base._REQUIRED_NATIVE_COLUMNS))+')')
   d.execute('INSERT INTO reservations(reservation_id,call_id,status,amount_cny,actual_cny) VALUES(?,?,?,?,?)',(ident,ident,'settled',cost,cost))
 h={'attribution_complete':True,'all_reservation_ids_at_audit':['old','pool'],'case_reservation_ids':{c:(['old'] if c==CASES[0] and historical else []) for c in CASES}}
 cls=guard_class(base.TaskBudgetGuard,a,h,tmp_path/'audit');m=cls(Native(b),task_id='R3-T042:'+CASES[0],lane='fresh_bounded_cycle',task_limit_cny=8,max_calls=4,global_limit_cny=220)
 return a,b,h,m
def test_history_counts_against_same_case(tmp_path):
 a,b,h,m=make(tmp_path,7.99,True)
 with pytest.raises(RuntimeError,match='case cumulative8'):m._reserve('a','a'*64,.02,'a')
def test_unknown_zero_hold_blocks(tmp_path):
 a,b,h,m=make(tmp_path)
 with sqlite3.connect(a) as d:d.execute("UPDATE reservations SET status='uncertain',amount_cny=0")
 with pytest.raises(RuntimeError,match='unresolved'):m._reserve('a','a'*64,.02,'a')
def test_project_gate_preserves_old140_metadata(tmp_path):
 a,b,h,m=make(tmp_path,219.99)
 with pytest.raises(RuntimeError,match='project cumulative220'):m._reserve('a','a'*64,.02,'a')
 with sqlite3.connect(a) as d:assert d.execute('SELECT value FROM budget_meta').fetchone()[0]=='140'
def test_four_attempts_durable_and_atomic(tmp_path):
 a,b,h,m=make(tmp_path)
 for i in range(4):
  m._reserve(str(i),str(i)*64,.1,str(i))
  with sqlite3.connect(b) as d:d.execute("UPDATE reservations SET status='settled',actual_cny=.01 WHERE call_id=?",(str(i),))
 again=guard_class(base.TaskBudgetGuard,a,h,tmp_path/'audit2')(Native(b),task_id=m.task_id,lane='fresh_bounded_cycle',task_limit_cny=8,max_calls=4,global_limit_cny=220)
 with pytest.raises((RuntimeError,base.TaskBudgetError),match='four actual|task_max_calls_exceeded'):again._reserve('x','f'*64,.1,'x')
 audits=list((tmp_path/'audit').glob('*.json'));assert len(audits)==4
 import json
 assert all(json.loads(p.read_text())['both_ledgers_locked_across_check_and_reservation'] for p in audits)


def test_unattributed_new_reservation_blocks_dispatch(tmp_path):
 a,b,h,m=make(tmp_path)
 with sqlite3.connect(a) as d:
  d.execute("INSERT INTO reservations(reservation_id,call_id,status,amount_cny,actual_cny) VALUES('unknown','unknown','settled',0,0)")
 with pytest.raises(RuntimeError,match='new unattributed'):m._reserve('a','a'*64,.02,'a')
 with sqlite3.connect(b) as d:assert d.execute('SELECT count(*) FROM r3_task_calls').fetchone()[0]==0
