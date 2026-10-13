from dataclasses import replace
import hashlib
import json
import numpy as np
import pytest
from optomind_eval.worlds import make_world
from optomind_eval.world_audits import audit_physical_splits, audit_world, write_sealed_audit


def test_physical_near_duplicate_rejects_changed_lineage_and_tiny_perturbation():
    a = make_world("F0", task_id="a", split="T", seed=1)
    b = replace(a, task=replace(a.task, task_id="b", split="V", group_id="different"),
                seed=9, provenance=(), thickness_nm=a.thickness_nm + .05)
    with pytest.raises(ValueError, match="physical near-duplicate"):
        audit_physical_splits([a, b])
    separated = make_world("F0", task_id="c", split="V", seed=100)
    assert audit_physical_splits([a, separated])["passed"]


def test_spectral_alias_rejected_even_if_physical_parameters_far():
    a = make_world("F6", task_id="a", split="T", seed=4)
    b = replace(a, task=replace(a.task, task_id="b", split="A", group_id="different"),
                seed=90, provenance=(), thickness_nm=a.thickness_nm + 120.)
    with pytest.raises(ValueError, match="physical near-duplicate"):
        audit_physical_splits([a, b])


@pytest.mark.parametrize("family", ["F0", "F3", "F6", "F8"])
def test_independent_reference_and_honest_expressibility(family):
    record = audit_world(make_world(family, task_id="t", split="V", seed=12))
    assert record["independent_reference"]["maximum_abs_error"] < 1e-12
    assert not record["initial_grammar"]["mechanism_impossibility_proven"]
    if family == "F0":
        assert record["initial_grammar"]["heldout_max_residual"] < 1e-7
    if family == "F6":
        assert record["identifiability"]["thickness_invariance_witness"]
        assert record["identifiability"]["thickness_100nm_change_max"] < 1e-12
    if family == "F8":
        refinement = record["independent_reference"]["refinement"]
        assert refinement["error_bound"] is None
        assert all(np.isfinite(refinement["successive_max_differences"]))
    seal = record.pop("seal")
    assert hashlib.sha256(json.dumps(record, sort_keys=True, allow_nan=False).encode()).hexdigest() == seal


def test_audit_write_is_exclusive_and_sealed(tmp_path):
    output = tmp_path / "audit.json"
    payload = write_sealed_audit([make_world("F0", task_id="t", split="V", seed=2)], output)
    assert not payload["policy_export"]
    with pytest.raises(FileExistsError):
        write_sealed_audit([], output)
