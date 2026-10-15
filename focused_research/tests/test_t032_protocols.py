import copy
import pytest
from focused_research.local_campaign_guard import CampaignPlannerGuard, CASE_PROTOCOLS, CASE_THICKNESS_BOUNDS
CASES=['e2e-01-methane-swir-window','e2e-04-solarblind-uv-triple','e2e-05-visual-absorber-cr']
class Planner:
    last_usage={};last_transport={}
    def __init__(self):self.calls=[]
    def _call_query_plan_model(self,p):self.calls.append(copy.deepcopy(p));return '{}'
@pytest.mark.parametrize('case',CASES)
def test_new_original_case_profiles_use_existing_four_attempt_guard(case):
    planner=Planner();audit=[]
    guard=CampaignPlannerGuard(planner,case_id=case,audit_sink=audit.append)
    p={'system_prompt':'s','user_payload':{'original_question':'native input unchanged'}}
    before=copy.deepcopy(p)
    for stage in ['task_sheet','route_generation','search_revision']:
        guard.set_stage(stage);guard._call_query_plan_model(p)
    assert p==before
    assert len(planner.calls)==3
    for call in planner.calls:
        assert call['user_payload']['original_question']==p['user_payload']['original_question']
        assert call['user_payload']['execution_protocol']['assumptions']
    assert CASE_THICKNESS_BOUNDS[case] is None
    assert guard.audit['dispatch_attempts']==3

def test_no_duplicate_application_or_material_narrowing_hidden_in_profiles():
    assert 'e2e-03-methane-swir-chirped' not in CASE_PROTOCOLS
    uv=' '.join(CASE_PROTOCOLS[CASES[1]]['requirements'])
    assert 'not a named two-material set' in uv
    absorber=' '.join(CASE_PROTOCOLS[CASES[2]]['requirements'])
    assert 'finite-film absorption only' in absorber
    assert 'minimize mean R in 400–700' in absorber
