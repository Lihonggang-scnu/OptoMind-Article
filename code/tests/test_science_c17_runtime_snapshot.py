import json
from pathlib import Path
import pytest
from optomind_eval.runtime_snapshot import create_runtime_snapshot


def test_snapshot_whitelist_hashes_and_no_overwrite(tmp_path):
    root=tmp_path/'source'
    for name in ('repos/OptoMind-Article/code/optomind_optics','repos/OptoMind-Article/code/config',
                 'repos/OptoMind-Article/veritmm','repos/OptoMInd-Review/optomind_research'):
        path=root/name;path.mkdir(parents=True);(path/'module.py').write_text('VALUE=1\n')
    (root/'repos/OptoMind-Article/code/config/.env').write_text('do not copy')
    keys=root/'repos/OptoMind-Article/code/config/api_keys';keys.mkdir();(keys/'secret').write_text('do not copy')
    target=tmp_path/'snapshot'
    result=create_runtime_snapshot(root,target)
    assert result['valid_for_execution'] and len(result['files'])==4
    assert all('api_keys' not in key and '.env' not in key for key in result['files'])
    assert all(len(value)==64 for value in result['files'].values())
    with pytest.raises(FileExistsError):create_runtime_snapshot(root,target)
    assert json.loads((target/'RUNTIME_SNAPSHOT_MANIFEST.json').read_text())==result
