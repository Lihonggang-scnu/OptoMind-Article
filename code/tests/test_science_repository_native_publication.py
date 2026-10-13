"""Native artifact publication must fail before scientific authority commits."""
import pytest
from optomind_optics.harness.science import repository as storage
from optomind_optics.harness.science.contracts import ResearchTask


def test_native_publish_error_rolls_back_all_scientific_authority(tmp_path, monkeypatch):
    task = ResearchTask(object_id="task", source_run_id="native-publication", question="bounded source")
    repo = storage.ScienceRepository(tmp_path)
    def fail_publish(*args):
        raise PermissionError("native rename rejected")
    monkeypatch.setattr(storage, "_publish_artifact", fail_publish)
    try:
        with pytest.raises(PermissionError, match="native rename rejected"):
            repo.commit([task], task_ref=task.ref(), evidence_cutoff="before")
        assert repo.latest_snapshot() is None
        assert repo.db.execute("SELECT COUNT(*) FROM objects").fetchone()[0] == 0
        assert not list((tmp_path / "objects").iterdir())
    finally:
        repo.close()


def test_native_publish_roundtrips_unicode_in_long_output_path(tmp_path):
    root = tmp_path / ("long-" + "a" * 90) / ("output-" + "b" * 90)
    task = ResearchTask(object_id="unicode-task", source_run_id="native-publication", question="偏振与波长 λ")
    repo = storage.ScienceRepository(root)
    try:
        committed = repo.commit([task], task_ref=task.ref(), evidence_cutoff="before")
    finally:
        repo.close()
    recovered = storage.ScienceRepository(root, read_only=True)
    try:
        assert recovered.latest_snapshot() == committed
        assert recovered.load(task.ref()) == task
        assert not list((root / "objects").glob(".pending-*"))
    finally:
        recovered.close()
