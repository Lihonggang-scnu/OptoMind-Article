from copy import deepcopy
import pytest
from optomind_optics.harness.research_report import _assess_target, DeterministicTMMResearchReporter
from optomind_optics.harness.scoring_standard import ScoringStandard, FixedScoreMetric
from optomind_optics.harness.research_feedback import ResearchFeedbackDecision, ResearchIterationObservation


def fixture(observed=.0353624086564206, threshold=.03, sense='minimize'):
    metric=FixedScoreMetric(variable='r',canonical_id='r',metric='mean_reflectance',sense=sense,
        region={'wavelength_nm':[450.,700.], 'angle_deg':0., 'polarization':'s'},
        target=threshold,mean_reduction='sample_mean')
    standard=ScoringStandard(metrics=(metric,),formula='-r')
    candidate={'candidate_id':'old','iteration_id':'i1','frozen_score':None,
        'objective_report':{'target_attainment':{'r':{
        'metric':'mean_reflectance','region':dict(metric.region),'mean_reduction':'sample_mean','observed':observed}}}}
    return candidate,standard


@pytest.mark.parametrize('value,expected',[(.0353624086564206,False),(.018,True),(.03,True),(0,True),(None,None),(float('nan'),None)])
def test_frozen_target_status(value,expected):
    candidate,standard=fixture(value)
    result=_assess_target(candidate,standard)
    assert result['target_met'] is expected
    assert result['candidate_id']=='old'
    assert result['score_basis_fingerprint']
    if expected is not None:
        assert result['clauses'][0]['target_margin']==pytest.approx(.03-value)


@pytest.mark.parametrize('field,value',[('polarization','p'),('angle_deg',30.),('wavelength_nm',[400.,700.])])
def test_changed_region_is_unknown(field,value):
    candidate,standard=fixture()
    candidate['objective_report']['target_attainment']['r']['region'][field]=value
    assert _assess_target(candidate,standard)['target_met'] is None


def test_changed_reduction_missing_threshold_and_native_target_are_unknown():
    candidate,standard=fixture()
    candidate['objective_report']['target_attainment']['r']['mean_reduction']='wavelength_integral_mean'
    assert _assess_target(candidate,standard)['target_met'] is None
    candidate,standard=fixture(threshold=None)
    candidate['objective_report']['target_attainment']['native']={'observed':.035,'target':.03,'constraint':'at_most'}
    result=_assess_target(candidate,standard)
    assert result['target_met'] is None
    assert 'native_target_binding_unavailable' in result['reason']
    assert _assess_target(candidate)['target_met'] is None


def test_match_without_explicit_frozen_tolerance_is_unknown():
    candidate,standard=fixture(.03,sense='match')
    assert _assess_target(candidate,standard)['target_met'] is None


def test_maximize_margin_and_failed_clause_dominates_missing_clause():
    candidate,standard=fixture(.02,sense='maximize')
    result=_assess_target(candidate,standard)
    assert result['target_met'] is False
    assert result['clauses'][0]['target_margin']==pytest.approx(-.01)
    other=standard.metrics[0].model_copy(update={'variable':'other','region':{'wavelength_nm':[700.,800.],'angle_deg':0.,'polarization':'s'}})
    standard=standard.model_copy(update={'metrics':(*standard.metrics,other)})
    assert _assess_target(candidate,standard)['target_met'] is False


def test_report_accepted_and_adopted_old_candidate_target_missed():
    old,standard=fixture()
    new=deepcopy(old);new.update(candidate_id='new',iteration_id='i2')
    new['objective_report']['target_attainment']['r']['observed']=.09994
    rows=[]
    for candidate,score in [(old,.8),(new,.2)]:
        candidate['target_score']=score
        rows.append(ResearchIterationObservation(iteration_id=candidate['iteration_id'],route_id='r',route_title='route',
            compilation_status='compiled',run_status='completed',physically_valid_candidate_count=1,
            selected_candidate_ids=(candidate['candidate_id'],),candidate_summaries=(candidate,),work_dir='x'))
    answer=DeterministicTMMResearchReporter().build(problem_analysis={'problem_id':'p'},method_research={},
        strategy_plan={},iterations=rows,stop_decision=ResearchFeedbackDecision(action='stop_completed',reason='round limit'),
        status='stopped_round_limit',scoring_standard=standard)
    assert answer.acceptance_status=='accepted'
    assert answer.physics_accepted
    assert answer.target_met is False
    assert answer.target_assessment['candidate_id']=='old'
    assert answer.target_assessment['objective_value']==pytest.approx(-.0353624086564206)
    assert 'Scientific target: not met.' in answer.markdown
    assert answer.model_dump(mode='json')['target_met'] is False


def test_no_adopted_candidate_is_unknown():
    assert _assess_target(None)['target_met'] is None


@pytest.mark.parametrize('malformed', [[], {}, True, float('nan'), float('inf')])
def test_malformed_duplicate_measurement_returns_unknown(malformed):
    candidate, standard = fixture(.02)
    row = deepcopy(candidate['objective_report']['target_attainment']['r'])
    row['observed'] = malformed
    candidate['objective_report']['target_attainment']['duplicate'] = row
    result = _assess_target(candidate, standard)
    assert result['target_met'] is None
    assert result['clauses'][0]['target_margin'] is None
