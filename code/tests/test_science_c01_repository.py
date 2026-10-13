from concurrent.futures import ThreadPoolExecutor
import pytest
from pydantic import ValidationError
from optomind_optics.harness.science.contracts import ResearchTask, Observation, EvidenceUse, DecisionContext
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.migration import migrate_observation_v1

def task(): return ResearchTask(object_id="task",source_run_id="run",question="which model?")

def test_roundtrip_hash_unknown_version_and_mutation(tmp_path):
    obj=task(); repo=ScienceRepository(tmp_path)
    snap=repo.commit([obj],task_ref=obj.ref(),evidence_cutoff="before acquisition",expected_sequence=0)
    assert repo.load(obj.ref())==obj
    assert repo.snapshot(snap.snapshot_id)==snap
    assert repo.object_commit_sequence(obj.ref())==1
    with pytest.raises(ValueError): repo.commit([obj],task_ref=obj.ref(),evidence_cutoff="x")
    with pytest.raises(ValidationError): ResearchTask(**{**obj.model_dump(),"extra_science":42})
    with pytest.raises(ValidationError): ResearchTask(**{**obj.model_dump(),"schema_version":"research-task.v99"})
    obj.objective["altered"]=1
    with pytest.raises(ValueError): repo.commit([obj],task_ref=obj.ref(),evidence_cutoff="x")

@pytest.mark.parametrize("stage",["before_artifacts","after_artifacts","before_commit","after_commit"])
def test_fault_atomicity(tmp_path,stage):
    def fault(where):
        if where==stage: raise RuntimeError("crash")
    obj=task(); repo=ScienceRepository(tmp_path,fault_hook=fault)
    with pytest.raises(RuntimeError): repo.commit([obj],task_ref=obj.ref(),evidence_cutoff="x")
    repo.close(); recovered=ScienceRepository(tmp_path)
    assert (recovered.latest_snapshot() is not None)==(stage=="after_commit")
    if stage=="after_commit": assert recovered.load(obj.ref())==obj

def test_concurrent_cas(tmp_path):
    ScienceRepository(tmp_path).close()
    def writer(_):
        repo=ScienceRepository(tmp_path)
        try:
            obj=task();repo.commit([obj],task_ref=obj.ref(),evidence_cutoff="x",expected_sequence=0);return True
        except ValueError:return False
        finally:repo.close()
    with ThreadPoolExecutor(2) as pool: assert sum(pool.map(writer,range(2)))==1

def test_migration_keeps_enhancement_and_no_double_count():
    legacy={"schema_version":"observation-card.v1","observation_id":"o","status":"completed","gradient_summary":{"signed":-3},"legacy_belief":.9}
    obs,report=migrate_observation_v1(legacy,source_run_id="r")
    assert obs.derived_features["legacy_projection"]==legacy
    assert obs.raw_blocks==() and "candidate_ref" in report.not_recorded_fields
    with pytest.raises(ValueError): EvidenceUse(object_id="e",source_run_id="r",observation_ref=obs.ref(),purpose="likelihood",derived_view="smoothed",likelihood_count=1)
    context=DecisionContext(object_id="c",source_run_id="r",actual_messages=({"role":"user","content":"hi"},))
    assert DecisionContext.model_validate_json(context.model_dump_json())==context

def test_foreign_ref_is_checked(tmp_path):
    repo=ScienceRepository(tmp_path); obj=task()
    unknown=ResearchTask(object_id="unknown",source_run_id="r",question="?")
    obs=Observation(object_id="obs",source_run_id="r",acquisition_id="a",candidate_ref=unknown.ref())
    with pytest.raises(ValueError):repo.commit([obj,obs],task_ref=obj.ref(),evidence_cutoff="x")

def test_acquisition_first_sequence_survives_aliases(tmp_path):
    repo=ScienceRepository(tmp_path); obj=task()
    first=Observation(object_id="first",source_run_id="r",acquisition_id="same-physical-acquisition")
    repo.commit([obj,first],task_ref=obj.ref(),evidence_cutoff="old")
    alias=Observation(object_id="alias",source_run_id="r",acquisition_id=first.acquisition_id)
    repo.commit([alias],task_ref=obj.ref(),evidence_cutoff="new")
    assert repo.object_commit_sequence(alias.ref())==2
    assert repo.acquisition_commit_sequence(alias.acquisition_id)==1

def test_existing_accepted_three_round_trajectory_read_only_migration(tmp_path):
    import hashlib, json
    from pathlib import Path
    from optomind_optics.harness.route_reflection import _build_reflection_payload
    trajectory=Path(__file__).resolve().parents[2]/"accepted_examples/research_broadband_ar_source/iterations"
    paths=sorted(trajectory.glob("*/ITERATION_OBSERVATION.json"))
    assert len(paths)==3
    source_hashes={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    repo=ScienceRepository(tmp_path); obj=task()
    repo.commit([obj],task_ref=obj.ref(),evidence_cutoff="prior")
    for path in paths:
        source=json.loads(path.read_text())
        observation,report=migrate_observation_v1(source,source_run_id="accepted_broadband_ar")
        repo.commit([observation],task_ref=obj.ref(),evidence_cutoff=source["iteration_id"])
        recovered=repo.load(observation.ref())
        assert recovered.derived_features["legacy_projection"]==source
        # Actual policy payload reads candidates without treating migration as a new spectrum.
        context=_build_reflection_payload({},source,[],1e-4)
        assert context["scientific_evidence"]["candidate_summaries"]==source["candidate_summaries"]
        assert not recovered.raw_blocks and "objective_ref" in recovered.not_recorded_fields
        assert len(report.mapped_fields)==len(source)
    assert all(hashlib.sha256(path.read_bytes()).hexdigest()==digest for path,digest in source_hashes.items())
