import numpy as np
import pytest
from optomind_eval.worlds import make_world, airy_intensity, audit_splits
from optomind_eval.service import MeasurementService, assert_production_isolation
from optomind_optics.harness.science.world_client import WorldClient


def test_analytic_world_matches_fresnel_zero_thickness():
    from dataclasses import replace
    world=replace(make_world("F0",task_id="one",split="V",seed=20),thickness_nm=0)
    assert np.allclose(airy_intensity(world,[500,600],"R"),.04)
    assert np.allclose(airy_intensity(world,[500,600],"T"),.96)


def test_purchase_idempotency_authorization_budget_and_terminal_sealing():
    world=make_world("F0",task_id="one",split="V",seed=2)
    client=WorldClient(MeasurementService([world],budget=2).handle)
    a=client.purchase("one","req","R",[500,600]); b=client.purchase("one","req","R",[500,600]); assert a==b
    with pytest.raises(PermissionError): client.purchase("one","req","T",[500,600])
    with pytest.raises(PermissionError): client.purchase("one","bad","A",[500,600])
    c=client.purchase("one","new","R",[500,600]); assert a.values!=c.values and a.calibration_group==c.calibration_group
    with pytest.raises(PermissionError): client.purchase("one","over","R",[500,600])
    sealed=client.submit_terminal("one","decision",{"film_n":2.,"thickness_nm":100.})
    assert set(sealed)=={"submission_id","status","sealed_digest"}
    assert sealed==client.submit_terminal("one","decision",{"film_n":2.,"thickness_nm":100.})


def test_split_conflicts_and_isolation_fail_closed():
    a=make_world("F0",task_id="a",split="T",seed=2); b=make_world("F1",task_id="b",split="V",seed=2)
    with pytest.raises(ValueError,match="split provenance"): audit_splits([a,b])
    with pytest.raises(PermissionError): assert_production_isolation(policy_uid=1000,evaluator_uid=1000,policy_can_read_world_store=False,evaluator_package_in_policy=False)


def test_families_and_nonidentifiable_world():
    from dataclasses import replace
    worlds=[make_world(f"F{i}",task_id=str(i),split="V",seed=i) for i in range(9)]
    assert audit_splits(worlds)["passed"]
    w=worlds[6]
    assert np.allclose(airy_intensity(w,[500,600],"R"),airy_intensity(replace(w,thickness_nm=800),[500,600],"R"))
    with pytest.raises(NotImplementedError): make_world("F9",task_id="x",split="V",seed=1)
