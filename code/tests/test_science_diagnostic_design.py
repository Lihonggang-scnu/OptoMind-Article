from dataclasses import asdict, replace
import numpy as np
import pytest

from optomind_eval.diagnostic_world import SyntheticDiagnosticProvider, reversal_family
from optomind_optics.harness.science.diagnostic_design import DiagnosticAction, DiagnosticBudgetExceeded, DiagnosticConfig, DiagnosticDatum, DiagnosticForward, DiagnosticFrontEnd, DiagnosticNoise, DiagnosticSearchSpace, MechanismFamily, ReferenceLayer, optimize_readout, refit_competition
from optomind_optics.harness.science.diagnostic_research import DiagnosticResearchLoop
from optomind_optics.harness.science.repository import ScienceRepository


def datum(family, parameters, action, forward, std=.003, acquisition_id=None):
    values = forward.predict(family, parameters, action)
    return DiagnosticDatum(acquisition_id or action.action_id, action, tuple(values), tuple(map(tuple, np.eye(len(values)) * std ** 2)))


def test_sample_parameters_are_shared_front_end_is_removable_and_index_is_real():
    family = reversal_family("sample", variable_index=True)
    before = asdict(family)
    forward = DiagnosticForward(DiagnosticConfig())
    parameters = {p.name: p.initial for p in family.model.parameters}
    bare = DiagnosticAction("bare", DiagnosticFrontEnd("bare"), (460., 550., 680.))
    front = replace(bare, action_id="front", frontend=DiagnosticFrontEnd("ref", (ReferenceLayer(2., 50.), ReferenceLayer(1., 100.))))
    reference = forward.predict(family, parameters, bare)
    changed = forward.predict(family, parameters, front)
    assert np.max(np.abs(reference - changed)) > .01
    assert np.allclose(forward.predict(family, parameters, bare), reference)
    index_changed = forward.predict(family, {**parameters, "sample.n1": parameters["sample.n1"] * 1.02}, bare)
    assert np.max(np.abs(index_changed - reference)) > .001
    assert asdict(family) == before
    with pytest.raises(ValueError, match="original sample"):
        MechanismFamily("wrong", replace(family.model, parameters=tuple(replace(p, namespace="frontend") for p in family.model.parameters)))


def test_refit_finds_wrong_mechanism_escape_and_preserves_old_prediction():
    config = DiagnosticConfig(fit_starts=5, max_nfev=160)
    forward = DiagnosticForward(config)
    families = (reversal_family("forward"), reversal_family("reverse", True))
    truth = {"sample.d0": 87., "sample.d1": 102., "sample.d2": 55.}
    old_action = DiagnosticAction("old", DiagnosticFrontEnd("bare"), (470., 710.), session_id="old")
    old = datum(families[1], truth, old_action, forward, std=.05)
    before = refit_competition(families, (old,), forward, config)
    new_action = DiagnosticAction("new", DiagnosticFrontEnd("ref", (ReferenceLayer(2., 35.), ReferenceLayer(1., 250.))), (520., 660.), session_id="new")
    new = datum(families[1], truth, new_action, forward)
    frozen = forward.predict(families[0], before.fits[0].parameters, new_action).copy()
    discrepancy = np.sum(((frozen - new.values) / config.noise.measurement_std) ** 2)
    assert discrepancy > 20.
    after = refit_competition(families, (old, new), forward, config, before)
    assert after.state == "mechanisms_confounded"
    assert after.fits[0].discrepancy < after.fits[0].threshold
    assert after.fits[0].parameters != before.fits[0].parameters
    assert np.array_equal(frozen, forward.predict(families[0], before.fits[0].parameters, new_action))
    assert all(f.acquisition_ids == ("old", "new") for f in after.fits)
    with pytest.raises(ValueError, match="unique raw acquisitions"):
        refit_competition(families, (old, old), forward, config)


def test_readout_propagates_covariance_and_cannot_create_information():
    delta = np.array([.1, -.1, .02, .01])
    covariance = np.eye(4) * .001 + np.ones((4, 4)) * .04
    readout = optimize_readout(delta, covariance, max_terms=2)
    weights = np.asarray(readout.weights)
    assert len(readout.nonzero_indices) == 2
    assert np.abs(weights).sum() == pytest.approx(1.)
    assert readout.variance == pytest.approx(weights @ covariance @ weights)
    assert readout.separation <= readout.raw_mahalanobis_separation + 1e-10
    assert readout.separation > max(delta ** 2 / covariance.diagonal())


def test_frontend_candidate_space_and_compute_budgets_are_explicit():
    config = DiagnosticConfig(max_prediction_calls=1)
    forward = DiagnosticForward(config)
    family = reversal_family("sample")
    space = DiagnosticSearchSpace()
    actions = space.actions(sample_id="specimen", session_id="fresh", limit=90)
    assert len({a.angle_deg for a in actions}) == 3
    assert {a.polarization for a in actions} == {"s", "p"}
    assert any(not a.frontend.layers for a in actions)
    assert any(a.frontend.frontend_id == "fixed-reference-baseline" for a in actions)
    assert len(actions) <= config.candidate_limit
    parameters = {p.name: p.initial for p in family.model.parameters}
    forward.predict(family, parameters, actions[0])
    with pytest.raises(DiagnosticBudgetExceeded):
        forward.predict(family, parameters, actions[0])
    assert forward.prediction_calls == 1


def test_out_of_family_can_withhold_all_mechanisms(tmp_path):
    families = (reversal_family("forward"), reversal_family("reverse", True))
    cfg = DiagnosticConfig(fit_starts=3, max_nfev=150)
    provider = SyntheticDiagnosticProvider(family=families[1], parameters={"sample.d0": 87., "sample.d1": 102., "sample.d2": 55.}, config=cfg, mismatch_layer=(2.7, 160.))
    repo = ScienceRepository(tmp_path / "science")
    action = DiagnosticAction("initial", DiagnosticFrontEnd("bare"), tuple(np.linspace(440., 810., 21)), session_id="initial")
    try:
        loop = DiagnosticResearchLoop(families=families, config=cfg, search_space=DiagnosticSearchSpace(), repository=repo, run_id="mismatch", provider=provider)
        result = loop.run(action, tmp_path)
        assert result["competition"]["state"] == "model_family_inadequate"
        assert result["competition"]["selected_family"] is None
        assert result["result"]["candidate_roles"]["adoption_status"] == "withheld_pending_further_evidence"
        assert result["result"]["candidate_roles"]["research_outcome"]["unknown_exploration_state"] == "not_authorized"
    finally:
        repo.close()


def test_blind_end_to_end_search_freezes_models_then_refits_and_confirms(tmp_path):
    from optomind_optics.harness.science.contracts import PredictionSpec, Observation
    families = (reversal_family("forward"), reversal_family("reverse", True))
    cfg = DiagnosticConfig(fit_starts=2, max_nfev=130, adversarial_shortlist=1, adversarial_starts=1, adversarial_max_nfev=80)
    space = DiagnosticSearchSpace(thicknesses_nm=(90.,), gaps_nm=(0.,), angles_deg=(0., 60.), polarizations=("s",), bandwidth_nm=0., angular_spread_deg=0.)
    provider = SyntheticDiagnosticProvider(family=families[1], parameters={"sample.d0": 87., "sample.d1": 102., "sample.d2": 55.}, config=cfg)
    repo = ScienceRepository(tmp_path / "science")
    action = DiagnosticAction("initial", DiagnosticFrontEnd("bare"), tuple(np.linspace(440., 810., 15)), session_id="initial")
    try:
        loop = DiagnosticResearchLoop(families=families, config=cfg, search_space=space, repository=repo, run_id="blind", provider=provider)
        report = loop.run(action, tmp_path)
        assert report["competition"]["selected_family"] == "reverse"
        assert report["competition"]["retained_families"] == ("reverse",)
        assert report["result"]["candidate_roles"]["research_outcome"]["local_prediction_state"] == "supported"
        assert report["trajectory"][0]["before"]["retained_families"] == ("forward", "reverse")
        assert report["trajectory"][0]["prequential_verdicts"]["forward"]["state"] == "violated"
        assert all(len(fit["acquisition_ids"]) == 2 for fit in report["trajectory"][0]["after_all_family_refits"]["fits"])
        assert len(report["raw_data"]) == 2
        assert len(report["acquisition_index"]) == 3
        assert all(max(row["prediction_sequences"].values()) < row["acquisition_sequence"] for row in report["acquisition_index"])
        objects = [repo.load(ref) for ref in repo.latest_snapshot().object_refs]
        assert sum(isinstance(obj, Observation) for obj in objects) == 3
        assert all(obj.identity_state == "bound" for obj in objects if isinstance(obj, (PredictionSpec, Observation)))
        assert (tmp_path / "SCIENCE_SNAPSHOTS.json").exists()
        assert report["measurement_cost"] <= cfg.measurement_budget
        assert report["method_lifecycle"]["status"] == "registered_bounded_trial"
    finally:
        repo.close()
