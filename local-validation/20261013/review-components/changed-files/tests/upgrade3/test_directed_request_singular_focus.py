"""Regression for real planning output using singular focus_area."""
import pytest

from optomind_research.runtime.upgrade3 import progressive_review_plan as p


def test_singular_focus_and_integration_goal_reach_reader_and_cache():
    request = {
        'paper_id': 'source-one', 'source_handle': 'P0001',
        'focus_area': 'Extract the assumptions and stated measurement conditions.',
        'integration_goal': 'Support the chapter comparison with attributed conditions.',
        'usage_constraints': 'Do not infer missing numerical measurements.',
    }
    merged = p._merge_directed_tasks([request])[0]
    assert merged['questions'][0]['question'] == request['focus_area']
    assert request['integration_goal'] in merged['questions'][0]['purpose']
    assert request['usage_constraints'] in merged['questions'][0]['purpose']
    assert merged['required_outputs'][0]['description'] == request['integration_goal']
    assert p._directed_task_signature(merged) == p._directed_task_signature(request)
    assert p._merge_directed_tasks([merged]) == [merged]
    changed = {**request, 'integration_goal': 'Use only the explicitly compared conditions.'}
    assert p._directed_task_signature(changed) != p._directed_task_signature(request)


@pytest.mark.parametrize('focus', ['', {}, []])
def test_empty_singular_focus_still_fails_without_invented_question(focus):
    with pytest.raises(p.ProgressivePlanError, match='question_missing'):
        p._merge_directed_tasks([{'paper_id': 'source-one', 'focus_area': focus,
                                 'integration_goal': 'Chapter purpose alone is not a reading question.'}])
