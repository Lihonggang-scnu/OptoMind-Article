import copy
import pytest
from focused_research import directional_reflection as dr, search_stage as st
from focused_research.tests.test_directional_stage import setup, execute
from focused_research.tests.test_search_stage import Planner


def test_real_saved_branch_feedback_is_forwarded_once_and_bound(tmp_path):
    _, plans, payload = setup(tmp_path)
    execute(tmp_path, plans, payload)
    original = st.load(tmp_path/'directional/branch_feedback.json')
    planner = Planner('{"plans":[]}')
    result = dr.reflect_on_branches(tmp_path/'directional',output=tmp_path/'reflection',planner=planner)
    assert result['status'] == 'plans_recorded'
    assert len(planner.calls) == 1
    actual = st.load(tmp_path/'reflection/directional_reflection_prompt.json')
    assert planner.calls[0] == actual
    projected = actual['user_payload']['directional_branch_feedback']
    assert projected == dr._feedback_decision_view(original,
        st.read_search(tmp_path/'directional'), st._aliases(st.read_search(tmp_path/'directional')))
    assert st.load(tmp_path/'reflection/directional_reflection_feedback_source.json') == original
    assert st.load(tmp_path/'directional/branch_feedback.json') == original
    binding = st.load(tmp_path/'reflection/directional_reflection_binding.json')
    assert binding['actual_forwarded_prompt_sha256'] == st.digest(actual)
    assert binding['feedback_sha256'] == st.digest(original)
    assert binding['feedback_view_sha256'] == st.digest(projected)
    assert binding['next_action_executed'] is False
    manifest = st.load(tmp_path/'reflection/manifest.json')
    assert 'directional_reflection_prompt.json' in manifest
    assert manifest['directional_reflection_feedback_source.json'] == st.digest(original)


def test_missing_or_tampered_feedback_stops_before_model_dispatch(tmp_path):
    _, plans, payload = setup(tmp_path)
    planner = Planner('{"plans":[]}')
    with pytest.raises(ValueError,match='no bound'):
        dr.reflect_on_branches(tmp_path/'original',output=tmp_path/'missing',planner=planner)
    execute(tmp_path,plans,payload)
    p=tmp_path/'directional/branch_feedback.json'
    before=st.load(p)
    changed=copy.deepcopy(before);changed['tampered']=True
    p.write_text(__import__('json').dumps(changed))
    with pytest.raises(ValueError,match='binding mismatch'):
        dr.reflect_on_branches(tmp_path/'directional',output=tmp_path/'bad',planner=planner)
    assert not planner.calls
