"""Synthetic-only durable cross-workorder limits, no real ledger/provider."""
import importlib.util,sys,sqlite3
from pathlib import Path
import pytest
from aggregate_budget import CASES,guard_class
spec=importlib.util.spec_from_file_location('t040_money_fixture',Path(__file__).parents[2]/'R3-T032/local_tools/test_aggregate_budget.py')
fixture=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fixture;spec.loader.exec_module(fixture)
def guard(a,b,h,root,task):
 return guard_class(fixture.base.TaskBudgetGuard,a,h,root)(fixture.Native(b),task_id=task+':'+CASES[1],lane='recovered_continuation',task_limit_cny=8,max_calls=2 if task=='R3-T040' else 4,global_limit_cny=220)
def settle(m,b,i):
 m._reserve(i,i*64,.1,i)
 with sqlite3.connect(b) as d:d.execute("UPDATE reservations SET status='settled',actual_cny=.01 WHERE call_id=?",(i,))
def test_four_old_plus_two_new_durable_not_reset(tmp_path):
 a,b,h,_=fixture.make(tmp_path)
 for task,start in [('R3-T037',0),('R3-T038',2)]:
  m=guard(a,b,h,tmp_path/task,task)
  for i in range(start,start+2):settle(m,b,str(i))
 m=guard(a,b,h,tmp_path/'new','R3-T040')
 for i in range(4,6):settle(m,b,str(i))
 again=guard(a,b,h,tmp_path/'restart','R3-T040')
 with pytest.raises((RuntimeError,fixture.base.TaskBudgetError)):again._reserve('x','f'*64,.1,'x')
 with sqlite3.connect(b) as d:assert d.execute('SELECT count(*) FROM r3_task_calls').fetchone()[0]==6
def test_old_case_money_still_counts(tmp_path):
 a,b,h,_=fixture.make(tmp_path,oldcost=7.99);h['case_reservation_ids'][CASES[1]]=['old']
 with pytest.raises(RuntimeError,match='original case cumulative8'):guard(a,b,h,tmp_path/'new','R3-T040')._reserve('x','f'*64,.02,'x')
def test_unknown_liability_blocks_even_zero(tmp_path):
 a,b,h,_=fixture.make(tmp_path)
 with sqlite3.connect(a) as d:d.execute("UPDATE reservations SET status='uncertain',amount_cny=0")
 with pytest.raises(RuntimeError,match='unresolved'):guard(a,b,h,tmp_path/'new','R3-T040')._reserve('x','f'*64,.02,'x')
def test_project_limit_still220(tmp_path):
 a,b,h,_=fixture.make(tmp_path,oldcost=219.99)
 with pytest.raises(RuntimeError,match='project cumulative220'):guard(a,b,h,tmp_path/'new','R3-T040')._reserve('x','f'*64,.02,'x')
