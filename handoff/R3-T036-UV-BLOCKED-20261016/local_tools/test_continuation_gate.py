"""Synthetic ledger checks for cross-workorder HTTP/money attribution only."""
import importlib.util,sys,sqlite3
from pathlib import Path
import pytest
from aggregate_budget import CASES,guard_class
spec=importlib.util.spec_from_file_location('t036_existing_fixture',Path(__file__).parents[2]/'R3-T032/local_tools/test_aggregate_budget.py');fixture=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fixture;spec.loader.exec_module(fixture)
def guard(a,b,h,tmp,task):
 return guard_class(fixture.base.TaskBudgetGuard,a,h,tmp)(fixture.Native(b),task_id=task+':'+CASES[1],lane='fresh_bounded_cycle',task_limit_cny=8,max_calls=4,global_limit_cny=220)
def settled(m,b,ident):
 m._reserve(ident,ident*64,.1,ident)
 with sqlite3.connect(b) as d:d.execute("UPDATE reservations SET status='settled',actual_cny=.01 WHERE call_id=?",(ident,))
def test_new_workorder_does_not_reset_original_case_four_http(tmp_path):
 a,b,h,_=fixture.make(tmp_path);old=guard(a,b,h,tmp_path/'old-audit','R3-T032')
 for i in range(2):settled(old,b,str(i))
 new=guard(a,b,h,tmp_path/'new-audit','R3-T036')
 for i in range(2,4):settled(new,b,str(i))
 with pytest.raises(RuntimeError,match='shared T032/T036 original case4'):new._reserve('x','f'*64,.1,'x')
def test_original_case_historical_money_blocks_new_workorder(tmp_path):
 a,b,h,_=fixture.make(tmp_path,oldcost=7.99);h['case_reservation_ids'][CASES[1]]=['old']
 new=guard(a,b,h,tmp_path/'new-audit','R3-T036')
 with pytest.raises(RuntimeError,match='original case cumulative8'):new._reserve('x','f'*64,.02,'x')
