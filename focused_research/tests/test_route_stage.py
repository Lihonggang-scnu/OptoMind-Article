"""CLI test only uses synthetic evidence, never a project API or solver."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]

@pytest.mark.parametrize('stage,filename',[('prompt','route_prompt.json'),('validate','research_routes.json'),('materialize','prepared_instances.json')])
def test_offline_cli(stage,filename,tmp_path):
    cmd=[sys.executable,str(ROOT/'route_stage.py'),stage,
        '--task-sheet',str(ROOT/'examples/rgb_nir_task_sheet.json'),
        '--evidence-file',str(ROOT/'examples/research_routes_evidence_fixture.json'),
        '--constraints-file',str(ROOT/'examples/rgb_nir_design_constraints.json'),
        '--routes-file',str(ROOT/'examples/research_routes_example.json'),
        '--route-id','R1','--output',str(tmp_path/'out')]
    # Fail any accidental HTTP/socket use in the subprocess too.
    guard=tmp_path/'guard';guard.mkdir()
    (guard/'sitecustomize.py').write_text('import socket\ndef deny(*a,**k): raise RuntimeError("offline-only")\nsocket.socket=deny\nsocket.create_connection=deny\n')
    env=dict(os.environ);env['PYTHONPATH']=str(guard)
    r=subprocess.run(cmd,env=env,capture_output=True,text=True)
    assert r.returncode==0,r.stdout+r.stderr
    assert (tmp_path/'out'/filename).is_file()
    s=json.loads((tmp_path/'out/summary.json').read_text())
    assert not s['simulation_performed'] and not s['live_model_called']
    assert len(json.loads((tmp_path/'out/input_identity.json').read_text())['task_sheet'])==64
    original=(tmp_path/'out'/filename).read_bytes()
    r=subprocess.run(cmd,env=env,capture_output=True,text=True)
    assert r.returncode==2 and (tmp_path/'out'/filename).read_bytes()==original
