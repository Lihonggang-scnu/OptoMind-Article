import pytest

from optomind_optics.harness.science.contracts import Observation, PredictionSpec, ResearchTask
from optomind_optics.harness.science.repository import ScienceRepository
from optomind_optics.harness.science.prediction import commit_prediction, evaluate_prediction
from optomind_optics.harness.science.evidence_use import EvidenceUseGraph, EvidenceUseRecord
from optomind_optics.harness.science.adequacy import calibrate_adaptive_null, evaluate_adequacy


def setup_prediction(tmp_path):
    repo = ScienceRepository(tmp_path)
    task = ResearchTask(object_id='task', source_run_id='test', question='synthetic envelope')
    snapshot = repo.commit([task], task_ref=task.ref(), evidence_cutoff='0')
    spec = PredictionSpec(object_id='prediction', source_run_id='test', observable='R', snapshot_id=snapshot.snapshot_id,
                          raw_channel='R', units='dimensionless', statistic='simultaneous_envelope', tolerance_source='synthetic preregistration',
                          lower=(.4, .5), upper=(.6, .7), coordinate=(500, 600))
    commit = commit_prediction(repo, spec, task_ref=task.ref(), evidence_cutoff='0')
    return repo, task, spec, commit


def observation(name='obs', values=(.5, .6), status='completed', noise=True):
    return Observation(object_id=name, source_run_id='test', acquisition_id=name,
                       raw_blocks=({'block_id':'raw','channel':'R','units':'dimensionless','coordinate':[500,600],'values':list(values)},),
                       quality={'execution_status':status,'numerical_valid':True,'noise_validated':noise})


@pytest.mark.parametrize('values,status,noise,expected', [((.5,.6),'completed',True,'supported'), ((.8,.6),'completed',True,'violated'),
                                                       ((.8,.6),'failed',True,'not_evaluable'), ((.5,.6),'completed',False,'inconclusive')])
def test_four_verdicts(tmp_path, values, status, noise, expected):
    repo, task, spec, commit = setup_prediction(tmp_path)
    obs = observation(values=values, status=status, noise=noise)
    repo.commit([obs], task_ref=task.ref(), evidence_cutoff='1')
    assert evaluate_prediction(repo, commit, obs.ref()).state == expected
    repo.close()


def test_posthoc_and_wrong_context_rejected(tmp_path):
    repo, task, spec, commit = setup_prediction(tmp_path)
    obs = observation()
    repo.commit([obs], task_ref=task.ref(), evidence_cutoff='1')
    later_spec = spec.model_copy(update={'object_id':'prediction2', 'content_hash':'', 'snapshot_id':repo.latest_snapshot().snapshot_id})
    later_spec = PredictionSpec.model_validate(later_spec.model_dump())
    later = commit_prediction(repo, later_spec, task_ref=task.ref(), evidence_cutoff='1')
    with pytest.raises(ValueError, match='before prediction'):
        evaluate_prediction(repo, later, obs.ref())
    with pytest.raises(ValueError, match='current scientific snapshot'):
        commit_prediction(repo, spec, task_ref=task.ref(), evidence_cutoff='1')
    repo.close()


def test_evidence_renaming_does_not_reset_roles():
    graph = EvidenceUseGraph()
    graph.add(EvidenceUseRecord('a'*64, 'parent', 'fit', 1))
    graph.inherit_candidate('parent', 'child')
    with pytest.raises(ValueError, match='cannot independently'):
        graph.add(EvidenceUseRecord('a'*64, 'child', 'validate', 3))
    graph.add(EvidenceUseRecord('b'*64, 'child', 'validate', 3))


def test_adaptive_null_scope_and_audit_order():
    calls=[]
    def whole(seed):
        calls.append(seed)
        return seed / 100
    calibration=calibrate_adaptive_null(whole, range(40), 'frozen-protocol', 'synthetic complete adaptive task')
    assert len(calls)==40
    args=dict(identity_valid=True,numerical_valid=True,noise_validated=True,residual_statistic=2,refit_completed=True,
              identifiable=True,decision_equivalent=None,calibration=calibration)
    assert evaluate_adequacy(**args).state=='model_family_inadequate'
    assert evaluate_adequacy(**dict(args, numerical_valid=False)).state=='numerical_failure'
    assert evaluate_adequacy(**dict(args, refit_completed=False)).state=='parameter_fit_required'
    assert evaluate_adequacy(**dict(args, residual_statistic=.2, identifiable=False, decision_equivalent=True)).state=='nonidentifiable_decision_equivalent'


def test_old_acquisition_cannot_be_rewrapped_after_prediction(tmp_path):
    repo=ScienceRepository(tmp_path)
    task=ResearchTask(object_id='task',source_run_id='test',question='alias')
    old=observation()
    repo.commit([task,old],task_ref=task.ref(),evidence_cutoff='0')
    spec=PredictionSpec(object_id='prediction',source_run_id='test',observable='R',snapshot_id=repo.latest_snapshot().snapshot_id,
                        raw_channel='R',units='dimensionless',statistic='simultaneous_envelope',tolerance_source='test',
                        lower=(.4,.5),upper=(.6,.7),coordinate=(500,600))
    commit=commit_prediction(repo,spec,task_ref=task.ref(),evidence_cutoff='0')
    alias=Observation.model_validate(dict(old.model_dump(),object_id='alias',content_hash=''))
    repo.commit([alias],task_ref=task.ref(),evidence_cutoff='1')
    with pytest.raises(ValueError,match='before prediction'):
        evaluate_prediction(repo,commit,alias.ref())
    repo.close()
