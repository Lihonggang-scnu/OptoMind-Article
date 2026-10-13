import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from test_science_c14_authoring import setup_selection

ROOT=Path(__file__).resolve().parents[1]
REVIEW=ROOT.parent.parent/'OptoMInd-Review'


def invoke(script,*args):
    code="import runpy,sys,pytest_socket;pytest_socket.disable_socket();p=sys.argv.pop(1);runpy.run_path(p,run_name='__main__')"
    return subprocess.run([sys.executable,'-c',code,str(ROOT/'scripts'/script),*map(str,args)],capture_output=True,text=True)


def test_formal_help_is_offline():
    for name in ('run_article_continuation.py','run_article_scientific_delivery.py'):
        result=invoke(name,'--help')
        assert result.returncode==0,result.stderr
        assert '--preview' in result.stdout and '--resume' in result.stdout and '--run' in result.stdout


def test_author_cli_default_preview_writes_nothing(tmp_path):
    source,_,selection,_=setup_selection(tmp_path)
    selection_path=tmp_path/'selection.json';selection_path.write_text(selection.model_dump_json())
    result=invoke('run_article_continuation.py','--source-pipeline-dir',source,'--work-dir',tmp_path/'preview',
        '--run-id','preview','--scientific-selection',selection_path,'--review-root',REVIEW)
    assert result.returncode==0,(result.stdout,result.stderr)
    payload=json.loads(result.stdout)
    assert payload['status']=='preview_ready' and payload['provider_calls']==payload['output_writes']==0
    assert not (tmp_path/'preview').exists()


def test_complete_cli_default_preview_never_reads_key_or_creates_output(tmp_path):
    _,_,selection,selected=setup_selection(tmp_path)
    ref=tmp_path/'selected.json';ref.write_text(selected.ref().model_dump_json())
    draft=tmp_path/'complete.md';draft.write_text('# Fixture\n\n## Abstract\n\nA bounded fixture.\n')
    identities=tmp_path/'identities.json';identities.write_text('{}')
    config=tmp_path/'config.json';config.write_text('{}')
    result=invoke('run_article_scientific_delivery.py','--repository-root',selection.repository_root,
        '--selected-result-ref',ref,'--manuscript',draft,'--manuscript-sha256',hashlib.sha256(draft.read_bytes()).hexdigest(),
        '--citation-namespace','fixture','--source-identity-map',identities,'--config',config,
        '--out-dir',tmp_path/'preview','--review-root',REVIEW,'--key-file',tmp_path/'nonexistent-secret')
    assert result.returncode==0,(result.stdout,result.stderr)
    payload=json.loads(result.stdout)
    assert payload['status']=='preview_ready' and payload['provider_calls']==payload['output_writes']==0
    assert not (tmp_path/'preview').exists()


def test_scientific_source_never_falls_back_to_legacy_without_selection(tmp_path):
    source,_,_,_=setup_selection(tmp_path)
    result=invoke('run_article_continuation.py','--source-pipeline-dir',source,'--work-dir',tmp_path/'preview',
        '--run-id','preview','--source-kind','scientific_result','--review-root',REVIEW)
    assert result.returncode==1
    payload=json.loads(result.stdout)
    assert payload['status']=='failed' and 'requires an adopted scientific selection' in payload['error']
    assert payload['provider_calls']==0 and not (tmp_path/'preview').exists()
