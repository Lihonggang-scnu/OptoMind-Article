"""Algorithm quality regression on explicit constant-index synthetic optics.

This is NOT the user's dispersive-material case or proof of general performance.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from focused_research import research_routes as rr, route_consumer as rc
from focused_research.family_optimization import optimize_family
from focused_research.research_execution import execute_prepared_batch
from focused_research.physical_observations import build_physical_observations


def test_layerwise_fixture_improves_both_goals_and_certifies(tmp_path):
    root=Path(__file__).resolve().parents[2]
    p=root/'focused_research/examples';load=lambda n:json.loads((p/n).read_text())
    routes=load('research_routes_example.json');sheet=load('rgb_nir_task_sheet.json');ev=load('research_routes_evidence_fixture.json');con=load('research_routes_constraints.json')
    r=routes['routes'][0];r['family']['layers']*=3
    compiled=rr.compile_candidates(routes,sheet,ev,design_constraints=con)
    assert len(compiled['accepted_contract']['routes'])==1,compiled['candidate_report']
    instances=rr.materialize(compiled['accepted_contract'],'R1',sheet,ev,design_constraints=con,candidate_mode=True,candidate_report=compiled['candidate_report'])
    media={'air':{'constant_n':1},'fused_silica':{'constant_n':1.46},'TiO2':{'constant_n':2.3},'SiO2':{'constant_n':1.46}}
    env=[rc.prepare_tmm_inputs(x,optical_media=media,wavelengths_nm=list(range(450,1001,10))) for x in instances]
    res=optimize_family(r,env,max_forward_evaluations=512,parameterization='layerwise_refinement',backend='numpy',seed=42)

    assert res['metering']['forward_design_rows'] <= 512
    assert res['metering']['failed_forward_design_rows'] == 0
    assert res['metering']['backward_passes'] == 0
    choice=max(res['candidates'],key=lambda x:min(x['numerical_search']['uncertified_metrics'].values()))
    certified=execute_prepared_batch([choice],cache_dir=tmp_path/'cache',max_new_evals=1)
    result=certified['results'][0]
    assert result['physics_accepted'] is True
    goals=build_physical_observations(result,result)['goals']
    measured={g['goal_id']:g['candidate'] for g in goals}
    assert measured['G1'] > .94 and measured['G2'] > .94
    assert all(measured['G1'] > row['metrics'][0] and measured['G2'] > row['metrics'][1]
               for row in res['evaluations'] if row['source']=='original_seed')
