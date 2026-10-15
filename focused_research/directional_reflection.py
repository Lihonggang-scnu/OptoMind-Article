"""One injected feedback call after directional search; never runs another search."""
from __future__ import annotations
from copy import deepcopy
from pathlib import Path
from . import search_stage as st


def reflect_on_branches(previous_search, *, output, planner):
    if planner is None:
        raise ValueError('an explicitly injected audited planner is required')
    previous_search = Path(previous_search)
    # Validate the complete saved search before reading its bound feedback file.
    st.read_search(previous_search)
    manifest = st.load(previous_search / 'manifest.json')
    if 'branch_feedback.json' not in manifest:
        raise ValueError('previous search has no bound directional branch feedback')
    feedback = st.load(previous_search / 'branch_feedback.json')
    output = Path(output)

    class FeedbackPlanner:
        @property
        def last_usage(self):
            return getattr(planner, 'last_usage', {})

        @property
        def last_transport(self):
            return getattr(planner, 'last_transport', {})

        def _call_query_plan_model(self, prompt):
            forwarded = deepcopy(prompt)
            forwarded['user_payload']['directional_branch_feedback'] = deepcopy(feedback)
            forwarded['system_prompt'] += (
                '\nReview the actual branch feedback before proposing the next coarse plans. '
                'Explain which observations support continuing, broadening, or changing a hypothesis. '
                'Different opinions and speculative interpretations are allowed. '
                'Separate the model-proposed seed from subsequent numerical refinement. '
                'Cached rediscoveries are not independent new experiments. '
                'Failure within a finite search budget does not disprove a hypothesis. '
                'These next plans remain unexecuted; do not claim new outcomes.')
            # st.revise has created output before dispatch; this is the actual
            # forwarded request, while revision_prompt.json is its base view.
            st.save(output / 'directional_reflection_prompt.json', forwarded)
            st.save(output / 'directional_reflection_binding.json', {
                'feedback_sha256': st.digest(feedback),
                'actual_forwarded_prompt_sha256': st.digest(forwarded),
                'base_prompt_file': 'revision_prompt.json',
                'actual_prompt_file': 'directional_reflection_prompt.json',
                'next_action_executed': False})
            return planner._call_query_plan_model(forwarded)

    return st.revise(previous_search, output=output, planner=FeedbackPlanner())
