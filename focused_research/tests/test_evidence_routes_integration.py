"""Synthetic end-to-end contract integration; no real retrieval/model/simulation."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'vendor/review'))
import run
import research_routes as rr
from route_consumer import prepare_tmm_inputs


def test_retrieval_to_route_to_existing_shape_without_network(tmp_path):
    sheet=json.loads((ROOT/'examples/rgb_nir_task_sheet.json').read_text())
    routes=json.loads((ROOT/'examples/research_routes_example.json').read_text())
    config=json.loads((ROOT/'examples/rgb_nir_design_constraints.json').read_text())
    quote=routes['decision_clues'][0]['source_quote']
    class Gateway:
        def search_papers(self,*a,**kw):
            return [],SimpleNamespace(ok=True,status_code=200,status_category='ok',audit={})
        def search_snippets(self,*a,**kw):
            return [{'paper':{'paperId':'synthetic-paper','corpusId':1234},
                'snippet':{'text':quote,'snippetKind':'body'}}],SimpleNamespace(ok=True,status_code=200,status_category='ok',audit={})
    summary=run.retrieve(sheet,tmp_path,gateway=Gateway())
    evidence=json.loads((tmp_path/'evidence_items.json').read_text())
    item=next(e for e in evidence['items'] if e['text']==quote)
    assert item['snippet_type']=='body' and item['eligible_as_evidence']
    assert len(item['query_ids'])==3
    assert set(item['goal_ids'])=={'G1','G2'}
    # Fixture stand-in for a model response referencing this run's real evidence ID.
    routes['decision_clues'][0]['evidence_id']=item['evidence_id']
    routes['routes'][0]['evidence_ids']=[item['evidence_id']]
    class Planner:
        last_usage={};last_transport={}
        def _call_query_plan_model(self,prompt):
            assert item['evidence_id'] in json.dumps(prompt['user_payload'])
            return json.dumps(routes)
    outcome=rr.generate(sheet,evidence,Planner(),design_constraints=config,max_repairs=0)
    assert outcome['status']=='ok'
    prepared=rr.materialize(outcome['research_routes'],'R1',sheet,evidence,design_constraints=config)
    output=prepare_tmm_inputs(prepared[0],optical_media={'air':{'constant_n':1},
        'fused_silica':{'material':'sio2'},'TiO2':{'material':'tio2'},'SiO2':{'material':'sio2'}},
        wavelengths_nm=[450,550,650,700,850,1000])
    assert output['objectives']==[g['executable_metric'] for g in sheet['subgoals']]
    assert output['simulation_performed'] is False
    assert prepared[0]['execution_authorized'] is False
