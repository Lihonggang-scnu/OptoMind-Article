"""Genuine production-loop integration; no fake M/E policy outcomes."""
import json
from pathlib import Path
import pytest
from optomind_eval.factorial_runner import run_factorial_evaluation
from optomind_eval.factorial_protocol import FACTOR_FLAGS


def test_native_factorial_executes_actual_production_loop(tmp_path):
    plan_path=Path(__file__).resolve().parents[4]/'tracking'/'C17_FULL_SCOPE_PROTOCOL_PLAN.json'
    if not plan_path.exists():
        pytest.skip('delivery-specific frozen full-scope plan not installed in this checkout')
    plan=json.loads(plan_path.read_text())
    plan['families']=['F0','F3']
    plan['pilot']['tasks_per_family']=1
    plan['pilot']['seed_base']=9261000
    fixture=tmp_path/'fixture-plan.json';fixture.write_text(json.dumps(plan))
    out=tmp_path/'result'
    report=run_factorial_evaluation(out,fixture,phase='pilot',fixture_limit=1)
    rows=json.loads((out/'results.json').read_text())['rows']
    assert len(rows)==8 and report['phase']=='engineering_fixture'
    assert not any(row['status']=='failed' for row in rows)
    for row in rows:
        assert (row['model_expansion_enabled'],row['program_composition_enabled'])==FACTOR_FLAGS[row['arm']]
        assert row['resource_cost']<=8 and row['paid_calls']==0
        path=out/row['task_id']/row['arm']
        trajectory=json.loads((path/'SCIENCE_OPEN_TRAJECTORY.json').read_text())
        assert trajectory['protocol']['model_expansion_enabled']==row['model_expansion_enabled']
        assert trajectory['protocol']['program_composition_enabled']==row['program_composition_enabled']
        assert row['oracle_terminal_loss'] is not None
