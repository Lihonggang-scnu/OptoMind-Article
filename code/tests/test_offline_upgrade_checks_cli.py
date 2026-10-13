"""Offline release planning must not install packages, create providers or write runs."""
import importlib.util
import json
from pathlib import Path
import pytest

P=Path(__file__).resolve().parents[1]/'scripts/run_offline_upgrade_checks.py'
spec=importlib.util.spec_from_file_location('offline_upgrade_checks',P)
cli=importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)

def test_preview_is_read_only_and_defers_optional_fullwave(tmp_path,capsys):
    assert cli.main(['--preview','--group','article','--output-dir',str(tmp_path/'not_created')])==0
    plan=json.loads(capsys.readouterr().out)
    command=plan['jobs'][0]['argv']
    assert '--disable-socket' in command
    assert not any('c18_' in arg or 'c19_' in arg for arg in command)
    assert not (tmp_path/'not_created').exists()

def test_missing_review_root_fails_before_writes(tmp_path):
    with pytest.raises(SystemExit) as error:
        cli.main(['--review-root',str(tmp_path/'missing'),'--output-dir',str(tmp_path/'not_created'),'--preview'])
    assert error.value.code==2
    assert not (tmp_path/'not_created').exists()

def test_review_root_can_be_configured_without_distribution_install(tmp_path,capsys):
    review=tmp_path/'separate-review';(review/'optomind_research').mkdir(parents=True)
    assert cli.main(['--review-root',str(review),'--group','cli','--preview','--output-dir',str(tmp_path/'not_created')])==0
    plan=json.loads(capsys.readouterr().out)
    assert any(job['cwd']==str(review) for job in plan['jobs'])
    assert all('disable_socket' in job['argv'][2] for job in plan['jobs'])

def test_existing_run_directory_is_never_overwritten(tmp_path):
    marker=tmp_path/'existing.log'
    marker.write_text('historical evidence')
    with pytest.raises(FileExistsError):
        cli.main(['--group','cli','--output-dir',str(tmp_path)])
    assert marker.read_text()=='historical evidence'
    assert not (tmp_path/'selection.json').exists()


def test_pytest_jobs_have_absolute_distinct_output_scoped_temp_without_preview_writes(tmp_path,capsys,monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(['--preview','--output-dir','relative-run'])==0
    plan=json.loads(capsys.readouterr().out)
    jobs=[job for job in plan['jobs'] if job['argv'][1:3]==['-m','pytest']]
    paths=[]
    for job in jobs:
        command=job['argv']
        temp=Path(command[command.index('--basetemp')+1])
        assert temp.is_absolute()
        assert temp==tmp_path/'relative-run'/job['label']/'pytest_tmp'
        assert job['pytest_basetemp']==str(temp)
        paths.append(temp)
    assert len(paths)==4 and len(set(paths))==4
    assert not (tmp_path/'relative-run').exists()


def test_new_review_consumers_and_scientific_cli_helps_are_selected(tmp_path,capsys):
    cli.main(['--preview','--output-dir',str(tmp_path/'not_created')])
    plan=json.loads(capsys.readouterr().out)
    review=next(job for job in plan['jobs'] if job['label']=='review')
    for test in ('tests/upgrade3/test_review_fullscope_consumers.py',
                 'tests/upgrade3/test_plan_handoff_integrity.py',
                 'tests/upgrade3/test_arrangement_body_handoff.py',
                 'tests/upgrade3/test_fullbody_cli.py',
                 'tests/upgrade3/test_review_material_boundaries.py',
                 'tests/upgrade3/test_directed_reading_adapter_contract.py',
                 'tests/test_review_audit_adversarial.py',
                 'tests/upgrade3/test_review_pool_audit.py'):
        assert test in review['argv']
    for label,module in [('cli_lifetime','lifetime_cli'),('cli_trajectory_migration','trajectory_migration')]:
        job=next(job for job in plan['jobs'] if job['label']==label)
        assert job['argv'][-2:]==['optomind_optics.harness.science.'+module,'--help']
        assert 'disable_socket' in job['argv'][2]
    assert not (tmp_path/'not_created').exists()


def test_actual_execution_creates_only_selected_job_temp_parent(tmp_path,monkeypatch):
    import subprocess
    output=tmp_path/'run'
    called=[]
    def fake_run(argv,**kwargs):
        temp=Path(argv[argv.index('--basetemp')+1])
        assert temp.parent.is_dir()
        assert temp==output/'veritmm'/'pytest_tmp'
        called.append(argv)
        return subprocess.CompletedProcess(argv,0)
    monkeypatch.setattr(cli.subprocess,'run',fake_run)
    assert cli.main(['--group','veritmm','--output-dir',str(output)])==0
    assert len(called)==1 and (output/'results.json').is_file()
    assert not (output/'article').exists()
