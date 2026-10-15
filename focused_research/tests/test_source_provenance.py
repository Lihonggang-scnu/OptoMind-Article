import hashlib
import importlib.util
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('focused_provenance_run', ROOT / 'run.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_checkout_commit_dirty_and_byte_hash(tmp_path):
    git(tmp_path, 'init')
    git(tmp_path, 'config', 'user.name', 'Offline Test')
    git(tmp_path, 'config', 'user.email', 'offline@example.invalid')
    folder = tmp_path / 'focused'
    folder.mkdir()
    source = folder / 'run.py'
    source.write_text('pass\n')
    config = tmp_path / 'config.json'
    config.write_text('{}')
    git(tmp_path, 'add', 'focused/run.py', 'config.json')
    git(tmp_path, 'commit', '-m', 'offline fixture')
    expected = git(tmp_path, 'rev-parse', 'HEAD')
    report = runner.runtime_source_provenance(source)
    assert report['source_commit'] == expected
    assert report['source_worktree_dirty'] is False
    config.write_text('{"changed": true}')
    assert runner.runtime_source_provenance(source)['source_worktree_dirty'] is True
    git(tmp_path, 'checkout', '--', 'config.json')
    extra = tmp_path / 'new_module.py'
    extra.write_text('pass\n')
    assert runner.runtime_source_provenance(source)['source_worktree_dirty'] is True
    extra.unlink()
    source.write_text('# changed\npass\n')
    report = runner.runtime_source_provenance(source)
    assert report['source_commit'] == expected
    assert report['source_worktree_dirty'] is True
    assert report['runner_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    untracked = folder / 'copy.py'
    untracked.write_text('pass\n')
    assert runner.runtime_source_provenance(untracked)['source_commit'] is None


def test_missing_git_does_not_invent_revision(tmp_path, monkeypatch):
    source = tmp_path / 'run.py'
    source.write_text('pass\n')
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('git')
    monkeypatch.setattr(runner.subprocess, 'run', unavailable)
    report = runner.runtime_source_provenance(source)
    assert report['source_commit'] is None
    assert report['source_worktree_dirty'] is None
    assert len(report['runner_sha256']) == 64
    assert report['vendor_review_origin_commit'] == '775329cee02d4dacd108bf41969b1d21d26750e0'
