from dataclasses import asdict
import json

import pytest

from optomind_optics.harness.science.contracts import (
    AcquisitionIdentity, DecisionContext, ExperimentProgram, ModelCandidate,
    ObjectRef, Observation, PredictionSpec, ResearchOutcome, ResearchTask,
    SelectedResearchResult, parse_object,
)
from optomind_optics.harness.science.prediction import (
    commit_prediction, compare_bound_observation, evaluate_prediction,
)
from optomind_optics.harness.science.program_ir import ProgramIR, ProgramNode
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.world_client import MeasurementReceipt, receipt_to_observation


def reference(name, digest='a'):
    return ObjectRef(object_id=name, content_hash=digest * 64)


def acquisition_identity(**updates):
    payload = dict(program_ref=reference('program'), node_id='measure', sample_id='specimen',
                   session_id='session', frontend_ref=reference('frontend', 'b'),
                   conditions={'angle_deg': 0., 'polarization': 's', 'coordinate_units': 'nm'})
    payload.update(updates)
    return AcquisitionIdentity(**payload)


def prediction(identity=None, **updates):
    payload = dict(object_id='prediction', source_run_id='test', observable='R',
                   candidate_ref=reference('model'), raw_channel='R', units='1',
                   statistic='simultaneous_envelope', tolerance_source='frozen synthetic noise',
                   lower=(.4, .5), upper=(.6, .7), coordinate=(500., 600.))
    if identity is not None:
        payload.update(program_ref=identity.program_ref,
                       grid={'acquisition_identity': identity.model_dump(mode='json')})
    payload.update(updates)
    return PredictionSpec(**payload)


def observation(identity=None, **updates):
    payload = dict(object_id='observation', source_run_id='test', acquisition_id='one-acquisition',
                   raw_blocks=({'block_id': 'R', 'channel': 'R', 'units': '1',
                                'coordinate': [500., 600.], 'coordinate_units': 'nm', 'values': [.5, .6]},),
                   quality={'execution_status': 'completed', 'numerical_valid': True, 'noise_validated': True})
    if identity is not None:
        payload['binding'] = {'acquisition_identity': identity.model_dump(mode='json')}
    payload.update(updates)
    return Observation(**payload)


def test_one_committed_acquisition_evaluates_distinct_model_versions(tmp_path):
    repo = ScienceRepository(tmp_path)
    try:
        task = ResearchTask(object_id='task', source_run_id='test', question='two mechanisms, one acquisition')
        frontend = DecisionContext(object_id='frontend', source_run_id='test', summary={'kind': 'bare'})
        program = ExperimentProgram(object_id='program', source_run_id='test', protocol=asdict(ProgramIR(
            'program', (ProgramNode('measure', 'new_evidence', channel='R', sample_id='session',
                                   arguments=(('wavelengths_nm', (500., 600.)),)),))))
        models = tuple(ModelCandidate(object_id='model-' + name, source_run_id='test',
                                      model_ir={'kind': 'fixture', 'name': name}) for name in ('a', 'b'))
        repo.commit([task, frontend, program, *models], task_ref=task.ref(), evidence_cutoff='before')
        identity = acquisition_identity(program_ref=program.ref(), frontend_ref=frontend.ref())
        commits = []
        for model in models:
            spec = prediction(identity, object_id='prediction-' + model.object_id, candidate_ref=model.ref(),
                              snapshot_id=repo.latest_snapshot().snapshot_id)
            assert spec.model_ref == model.ref()
            commits.append(commit_prediction(repo, spec, task_ref=task.ref(), evidence_cutoff='before'))
        obs = observation(identity)
        repo.commit([obs], task_ref=task.ref(), evidence_cutoff='after')
        verdicts = [evaluate_prediction(repo, commit, obs.ref()) for commit in commits]
        assert all(v.state == 'supported' and v.identity_state == 'bound' for v in verdicts)
        assert {v.observation_ref.content_hash for v in verdicts} == {obs.content_hash}
        assert len({v.prediction_ref.content_hash for v in verdicts}) == 2
        stored = [repo.load(ref) for ref in repo.latest_snapshot().object_refs]
        assert sum(isinstance(obj, Observation) for obj in stored) == 1
        assert obs.candidate_ref is None
    finally:
        repo.close()


@pytest.mark.parametrize('field,value', [
    ('program_ref', reference('wrong-program', 'c')),
    ('node_id', 'different-node'),
    ('sample_id', 'other-specimen'),
    ('session_id', 'other-session'),
    ('frontend_ref', reference('wrong-frontend', 'c')),
    ('conditions', {'angle_deg': 15., 'polarization': 's', 'coordinate_units': 'nm'}),
])
def test_wrong_acquisition_identity_is_not_model_refutation(field, value):
    expected = acquisition_identity()
    actual = acquisition_identity(**{field: value})
    verdict = compare_bound_observation(prediction(expected), observation(actual))
    assert verdict.state == 'not_evaluable'
    assert verdict.identity_state == 'identity_failure'
    assert verdict.failure_kind == 'identity_failure'
    assert field in verdict.reason


def test_new_identity_model_reference_does_not_name_measured_candidate():
    identity = acquisition_identity()
    verdict = compare_bound_observation(prediction(identity), observation(identity, candidate_ref=reference('unrelated-model', 'f')))
    assert verdict.state == 'supported'
    assert verdict.identity_state == 'bound'


@pytest.mark.parametrize('bound_prediction', [True, False])
def test_bound_contract_cannot_silently_downgrade_to_legacy(bound_prediction):
    identity = acquisition_identity()
    spec = prediction(identity if bound_prediction else None, candidate_ref=None)
    obs = observation(None if bound_prediction else identity)
    verdict = compare_bound_observation(spec, obs)
    assert verdict.state == 'not_evaluable'
    assert verdict.failure_kind == 'identity_failure'


def test_legacy_candidate_matching_and_support_remain_legacy_unbound():
    spec = prediction()
    assert spec.identity_state == 'legacy_unbound'
    assert spec.model_ref is None
    same = observation(candidate_ref=spec.candidate_ref)
    verdict = compare_bound_observation(spec, same)
    assert verdict.state == 'supported'
    assert verdict.identity_state == 'legacy_unbound'
    assert compare_bound_observation(spec, observation()).state == 'not_evaluable'


def test_declared_raw_condition_cannot_disagree_with_bound_identity():
    identity = acquisition_identity()
    obs = observation(identity)
    raw = dict(obs.raw_blocks[0], angle_deg=15.)
    obs = observation(identity, raw_blocks=(raw,))
    verdict = compare_bound_observation(prediction(identity), obs)
    assert verdict.state == 'not_evaluable'
    assert verdict.failure_kind == 'identity_failure'


def test_raw_condition_cannot_mask_contradictory_binding_conditions():
    identity = acquisition_identity()
    obs = observation(identity)
    obs = observation(identity, raw_blocks=(dict(obs.raw_blocks[0], angle_deg=0.),), binding={
        'acquisition_identity': identity.model_dump(mode='json'), 'conditions': {'angle_deg': 15.}})
    verdict = compare_bound_observation(prediction(identity), obs)
    assert verdict.state == 'not_evaluable'
    assert verdict.failure_kind == 'identity_failure'


@pytest.mark.parametrize('conditions', [
    {'angle_deg': 0., 'polarization': 's'},
    {'angle_deg': float('nan'), 'polarization': 's', 'coordinate_units': 'nm'},
    {'angle_deg': 0., 'polarization': 's', 'coordinate_units': 'nm', 'untracked_condition': True},
])
def test_bound_identity_rejects_incomplete_or_unversioned_conditions(conditions):
    with pytest.raises(ValueError):
        acquisition_identity(conditions=conditions)


def test_bound_prediction_rejects_conflicting_program_alias():
    with pytest.raises(ValueError, match='program_ref conflicts'):
        prediction(acquisition_identity(), program_ref=reference('other-program', 'c'))


def test_nested_identity_requires_explicit_version():
    payload = acquisition_identity().model_dump(mode='json')
    payload.pop('schema_version')
    with pytest.raises(ValueError, match='schema_version required'):
        observation(binding={'acquisition_identity': payload})
    with pytest.raises(ValueError, match='schema_version required'):
        prediction(grid={'acquisition_identity': payload})


def test_prediction_commit_rejects_nonexistent_acquisition_node(tmp_path):
    repo = ScienceRepository(tmp_path)
    try:
        task = ResearchTask(object_id='task', source_run_id='test', question='wrong node')
        program = ExperimentProgram(object_id='program', source_run_id='test', protocol=asdict(ProgramIR(
            'program', (ProgramNode('real-node', 'new_evidence', channel='R'),))))
        frontend = DecisionContext(object_id='frontend', source_run_id='test')
        model = ModelCandidate(object_id='model', source_run_id='test', model_ir={'kind': 'fixture'})
        snapshot = repo.commit([task, program, frontend, model], task_ref=task.ref(), evidence_cutoff='before')
        identity = acquisition_identity(program_ref=program.ref(), frontend_ref=frontend.ref(), node_id='imaginary-node')
        spec = prediction(identity, candidate_ref=model.ref(), snapshot_id=snapshot.snapshot_id)
        with pytest.raises(ValueError, match='new-evidence program node'):
            commit_prediction(repo, spec, task_ref=task.ref(), evidence_cutoff='before')
        assert repo.latest_snapshot() == snapshot
    finally:
        repo.close()


def test_bound_nested_json_schemas_resolve_refs_and_enforce_conditions():
    jsonschema = pytest.importorskip('jsonschema')
    identity = acquisition_identity()
    objects = (prediction(identity), observation(identity), SelectedResearchResult(
        object_id='selected', source_run_id='test', candidate_ref=reference('model'), candidate_roles={
            'research_outcome': ResearchOutcome(unresolved_failure_refs=(reference('failure'),)).model_dump(mode='json')}))
    for obj in objects:
        schema = obj.model_json_schema()
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(obj.model_dump(mode='json'), schema)
    payload = observation(identity).model_dump(mode='json')
    payload['binding']['acquisition_identity']['conditions'].pop('angle_deg')
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, Observation.model_json_schema())


def test_receipt_adapter_binds_identity_without_inventing_model_identity():
    receipt = MeasurementReceipt('receipt', 'session', 'request', 'R', (500., 600.), (.5, .6),
                                 ((.001, 0.), (0., .001)), 'calibration', .01, 1.,
                                 'planar-independent/1', 'one-acquisition')
    identity = acquisition_identity()
    obs = receipt_to_observation(receipt, object_id='obs', source_run_id='test', acquisition_identity=identity)
    assert obs.acquisition_identity == identity
    assert obs.binding['program_ref'] == identity.program_ref.model_dump(mode='json')
    assert obs.candidate_ref is None
    assert compare_bound_observation(prediction(identity), obs).state == 'supported'
    with pytest.raises(ValueError, match='session identity'):
        receipt_to_observation(receipt, object_id='wrong', source_run_id='test',
                               acquisition_identity=acquisition_identity(session_id='other-session'))
    legacy = receipt_to_observation(receipt, object_id='legacy', source_run_id='test')
    assert legacy.identity_state == 'legacy_unbound'
    assert set(legacy.binding) == {'measurement_receipt_id', 'task_id', 'evaluator_version'}


@pytest.mark.parametrize('obj,expected_hash', [
    (Observation(object_id='old-observation', source_run_id='test', acquisition_id='old-acquisition'),
     'caa9fa4b908f2c3f8f5e2523d77c1e5b7659408d0ab6c29b2b608f26d9d4db5e'),
    (PredictionSpec(object_id='old-prediction', source_run_id='test', observable='R'),
     '9f156ff777406577c823c53ff0616e563dd0b5b52b281d96351c84bb9d388374'),
    (SelectedResearchResult(object_id='old-selected', source_run_id='test', candidate_ref=reference('model')),
     'd05bef3b302f961e47b9e0eaa06746a4f89c0206d1d19bab74e09de940072877'),
])
def test_legacy_default_hashes_are_unchanged(obj, expected_hash):
    assert obj.content_hash == expected_hash
    assert parse_object(obj.model_dump(mode='json')).content_hash == expected_hash
    assert 'identity_state' not in obj.model_dump(mode='json')
    assert 'research_outcome' not in obj.model_dump(mode='json')


def test_f3_historical_failure_projects_without_rewriting_saved_evidence():
    result = SelectedResearchResult.model_validate(F3_SELECTED)
    adequacy = DecisionContext.model_validate(F3_ADEQUACY)
    before = result.model_dump_json()
    outcome = result.project_research_outcome(model_adequacy_state=adequacy.summary['state'],
        unknown_exploration_limit=0, unresolved_failure_refs=(adequacy.ref(),))
    assert outcome.state == 'model_family_inadequate'
    assert outcome.local_prediction_state == 'supported_within_frozen_noise_envelope'
    assert outcome.unknown_exploration_state == 'not_authorized'
    assert outcome.identity_state == 'legacy_unbound'
    assert outcome.unresolved_failure_refs == (adequacy.ref(),)
    assert result.model_dump_json() == before
    assert result.model_dump(mode='json') == F3_SELECTED
    assert result.content_hash == 'b3d43a69817e2b64b1f72a5405dbfec60f83d5a32c7b443bf06dbbba55b3e618'
    assert adequacy.content_hash == 'ed744afb34d71dd67f9e8435e48db6796190068a1e0d042e44588ed638ac7b26'


def test_persisted_failure_state_takes_priority_over_local_prediction_support():
    outcome = ResearchOutcome(model_adequacy_state='model_family_inadequate',
        local_prediction_state='supported', identity_state='bound', unknown_exploration_state='budget_exhausted',
        unresolved_failure_refs=(reference('adequacy'),))
    result = SelectedResearchResult(object_id='selected', source_run_id='test', candidate_ref=reference('model'),
        candidate_roles={'research_outcome': outcome.model_dump(mode='json')})
    restored = parse_object(result.model_dump(mode='json'))
    assert restored.outcome_state == 'model_family_inadequate'
    assert restored.research_outcome.unknown_exploration_state == 'budget_exhausted'
    assert restored.research_outcome.unresolved_failure_refs == (reference('adequacy'),)


@pytest.mark.parametrize('kwargs,state', [
    ({'identity_state': 'identity_failure'}, 'identity_failure'),
    ({'numerical_state': 'failure'}, 'numerical_failure'),
    ({'noise_state': 'not_calibrated'}, 'noise_uncalibrated'),
    ({'mechanism_state': 'confounded'}, 'mechanism_confounded'),
    ({'unknown_exploration_state': 'budget_exhausted'}, 'unknown_exploration_budget_exhausted'),
    ({}, 'local_prediction_supported'),
])
def test_projected_outcomes_keep_distinct_failure_semantics(kwargs, state):
    assert ResearchOutcome(local_prediction_state='supported', **kwargs).state == state


F3_SELECTED = json.loads(r'''{
  "object_id": "e03-out-f3-s2718:selected",
  "revision": 1,
  "content_hash": "b3d43a69817e2b64b1f72a5405dbfec60f83d5a32c7b443bf06dbbba55b3e618",
  "source_run_id": "e03-out-f3-s2718",
  "input_refs": [
    {
      "object_id": "e03-out-f3-s2718:terminal-commit",
      "revision": 1,
      "content_hash": "1fb94a2963c3106353eb139272d9670de3d55975162a3a433e0f62460940679c"
    },
    {
      "object_id": "e03-out-f3-s2718:final-validation",
      "revision": 1,
      "content_hash": "617176174180453390cebec9451b4b03e8373cc79c66ed8a2dadf22b3f150588"
    }
  ],
  "unconsumed_metadata": {},
  "schema_version": "selected-research-result.v1",
  "candidate_ref": {
    "object_id": "e03-out-f3-s2718:adopted-design",
    "revision": 1,
    "content_hash": "1c36183a4611a6e0efff06020b92932e9f58a15339cfc51f5f8559b87e7eabf2"
  },
  "inference_ref": {
    "object_id": "inference:44dee7d70ff29173f883d6a35d26f64012a6f0574750fb3b44af258014478e90",
    "revision": 1,
    "content_hash": "7c82a5a742c2a4d20045db561be25e7ea362f975a4605f54a9f6c205eb2a53f4"
  },
  "claim_refs": [
    {
      "object_id": "e03-out-f3-s2718:claim:design",
      "revision": 1,
      "content_hash": "1c8b2e86774f50869a0e9a69a23adaea46452a570dc0775f27943d1e9d0276fc"
    },
    {
      "object_id": "e03-out-f3-s2718:claim:validation",
      "revision": 1,
      "content_hash": "87f9387ec932b175dd051f9964fcaa127afe0c2b0b437d8987484455039c8030"
    }
  ],
  "candidate_roles": {
    "fitted_specimen": "candidate:44dee7d70ff29173f883d6a35d26f64012a6f0574750fb3b44af258014478e90",
    "terminal_choice": "thickness:135",
    "adoption_status": "proposed_design",
    "validation_state": "supported_within_frozen_noise_envelope",
    "evidence_tier": "V1_synthetic",
    "stop_reason": "propose_structure:no_supported_positive_value",
    "design_changed": false,
    "independent_manufactured_design_validation": false
  },
  "prediction_refs": [
    {
      "object_id": "prediction:cb1db463b9b4c72051bed27a28e6f37b0244136b6d7ef2268ebd46d8c05e1b5f:measure:a6d7466c8de53204a4d74695b8753b7da5988e252fba3365148a97b46c4d425f",
      "revision": 1,
      "content_hash": "f96c8d21cbb5c8190528e1e36db1bb17cd7398314c3d35a91af1eb75051b271e"
    }
  ],
  "negative_result_refs": [],
  "equivalent_explanations": [],
  "cost_refs": [],
  "asset_refs": [
    {
      "object_id": "e03-out-f3-s2718:terminal-commit",
      "revision": 1,
      "content_hash": "1fb94a2963c3106353eb139272d9670de3d55975162a3a433e0f62460940679c"
    },
    {
      "object_id": "e03-out-f3-s2718:final-validation",
      "revision": 1,
      "content_hash": "617176174180453390cebec9451b4b03e8373cc79c66ed8a2dadf22b3f150588"
    },
    {
      "object_id": "observation:14b62f0b233a626ed040824920643527e1a335a017a2e49d6d2cb5dc6e06c6dd",
      "revision": 1,
      "content_hash": "af46b40ce1bdfa7cac94c658795d399ccc2125601e46bb6bbfc33f6bcf91eb49"
    },
    {
      "object_id": "evaluation:e8a74190275dea0180b4d0abb6ecedb3e7bea4093f4acdd479509f0a239f7943",
      "revision": 1,
      "content_hash": "17642c469d181880fd85e89fcc12f92fd7f17d5897308c70fea30ec27c3a3ef3"
    }
  ],
  "claim_strength": "design"
}''')


F3_ADEQUACY = json.loads(r'''{"object_id":"adequacy:a6d7466c8de53204a4d74695b8753b7da5988e252fba3365148a97b46c4d425f","revision":1,"content_hash":"ed744afb34d71dd67f9e8435e48db6796190068a1e0d042e44588ed638ac7b26","source_run_id":"e03-out-f3-s2718","input_refs":[{"object_id":"candidate:44dee7d70ff29173f883d6a35d26f64012a6f0574750fb3b44af258014478e90","revision":1,"content_hash":"a6d7466c8de53204a4d74695b8753b7da5988e252fba3365148a97b46c4d425f"}],"unconsumed_metadata":{},"schema_version":"decision-context.v1","observation_refs":[],"summary":{"state":"model_family_inadequate","audits":["identity_passed","numerical_passed","noise_validated","parameter_refit_completed","frozen_family_refit_bootstrap_calibrated"],"calibration_version":"39309f9b0721de66e5639771f8bea7898e5c3a3cae7063065695815946a75c87","p_value":0.05,"unknown_risk":true,"task_impact":"unknown","route":"propose_structure","null_refit_count":19,"null_nfev":206,"null_model_predict_calls_known":619,"null_predict_call_count_incomplete":false,"cost_scope":"nfev counts optimizer residual steps only; model_predict_calls includes inference numerical derivative probes; non-inference forwards are separate","calibration_scope":"plug-in frozen-family refit bootstrap only; search-wide false-rejection risk remains unknown","discovery_claim":false,"training_discrepancy":44.49577944536407},"feature_view":{},"raw_view":{},"actual_messages":[],"context_hash":"4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"}''')


def frozen_standard_payload():
    from optomind_optics.harness.scoring_standard import FixedScoreMetric, ScoringStandard
    return ScoringStandard(metrics=(FixedScoreMetric(variable='r', canonical_id='mean_reflectance@450-700nm',
        metric='mean_reflectance', sense='minimize', mean_reduction='sample_mean',
        region={'wavelength_nm': [450., 700.], 'angle_deg': 0., 'polarization': 's'}),), formula='-r').model_dump(mode='json')


def test_frozen_scoring_objective_is_typed_locked_and_hash_preserving():
    objective = {'kind': 'optomind_frozen_score', 'scoring_standard': frozen_standard_payload()}
    task = ResearchTask(object_id='frozen', source_run_id='test', question='frozen score', objective=objective)
    assert task.objective == objective
    assert parse_object(task.model_dump(mode='json')).content_hash == task.content_hash
    jsonschema = pytest.importorskip('jsonschema')
    jsonschema.validate(task.model_dump(mode='json'), ResearchTask.model_json_schema())


@pytest.mark.parametrize('mutation', ['unlocked', 'unsafe_formula', 'unsupported_metric', 'other_kind', 'extra_targets', 'missing_standard', 'invalid_region'])
def test_frozen_scoring_contract_fails_closed(mutation):
    standard = frozen_standard_payload()
    objective = {'kind': 'optomind_frozen_score', 'scoring_standard': standard}
    if mutation == 'unlocked':
        standard['locked'] = False
    elif mutation == 'unsafe_formula':
        standard['formula'] = '__import__("os")'
    elif mutation == 'unsupported_metric':
        standard['metrics'][0]['metric'] = 'polarization_channels'
        standard['metrics'][0]['mean_reduction'] = None
    elif mutation == 'other_kind':
        objective['kind'] = 'optomind_soft_score'
    elif mutation == 'extra_targets':
        objective['targets'] = []
    elif mutation == 'missing_standard':
        objective.pop('scoring_standard')
    elif mutation == 'invalid_region':
        standard['metrics'][0]['region']['wavelength_nm'] = [700., 450.]
    with pytest.raises(ValueError):
        ResearchTask(object_id='invalid-frozen', source_run_id='test', question='invalid', objective=objective)


def test_frozen_scoring_json_schema_keeps_kind_boundary():
    jsonschema = pytest.importorskip('jsonschema')
    task = ResearchTask(object_id='frozen', source_run_id='test', question='frozen score', objective={
        'kind': 'optomind_frozen_score', 'scoring_standard': frozen_standard_payload()})
    schema = ResearchTask.model_json_schema()
    payload = task.model_dump(mode='json')
    payload['objective']['kind'] = 'optomind_soft_score'
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)
    payload['objective']['kind'] = 'optomind_frozen_score'
    payload['objective']['targets'] = []
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)
