"""One injected feedback call after directional search; never runs another search."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
import re
from . import search_stage as st


_SHORT_ALIAS = re.compile(r'(?<![\w:.])([PD][0-9]{3,})(?![\w:]|\.[\w])')


def _historical_text(value, labels):
    """Namespace display labels in known historical prose, never structured IDs."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    def replace(match):
        label = match.group(1)
        labels.add(label)
        return 'historical[' + label + ']'
    return _SHORT_ALIAS.sub(replace, text)


def _feedback_decision_view(feedback, previous, aliases):
    """Separate saved model interpretations from current measured identities.

    P/D labels in historical prose have no recoverable source alias map here.
    Do not guess their identity from the current archive or rewrite hypotheses.
    The original feedback stays unchanged and is separately hash-bound.
    """
    view = deepcopy(feedback)
    pa = {identity: alias for alias, identity in aliases['parents'].items()}
    da = {identity: alias for alias, identity in aliases['diagnostics'].items()}
    diagnostics = {row['diagnosis_id']: row for row in previous['diagnostics']}
    labels = set()

    def diagnosis_reference(identity):
        row = diagnostics.get(identity)
        if row is None:
            return None
        return {'diagnosis_id': identity, 'diagnosis_alias': da[identity],
                'design_id': row['design_id'], 'design_alias': pa[row['design_id']],
                'comparison_baseline_design_id': row['comparison_baseline_design_id'],
                'comparison_baseline_alias': pa[row['comparison_baseline_design_id']],
                'observation_ids': [g['observation_id'] for g in row['diagnostics']['goals']
                                    if g.get('status') == 'observed']}

    parents_by_plan = {}
    for branch in view.get('branches', []):
        parent = branch['parent_design_id']
        branch['current_parent_alias'] = pa[parent]
        parents_by_plan.setdefault(branch['plan_index'], set()).add(parent)
        if 'rationale' in branch:
            branch['historical_model_rationale'] = _historical_text(branch.pop('rationale'), labels)
        for outcome in branch.get('selected_certified_designs', []):
            identity = outcome['design_id']
            outcome['current_design_alias'] = pa[identity]
            outcome['comparison_baseline_design_id'] = parent
            outcome['comparison_baseline_alias'] = pa[parent]
            outcome['current_diagnostic_references'] = [diagnosis_reference(d['diagnosis_id'])
                for d in previous['diagnostics'] if d['design_id'] == identity
                and d['comparison_baseline_design_id'] == parent]
    for plan in view.get('plans', []):
        source = plan.pop('original_coarse_plan')
        historical = deepcopy(source)
        # Only these schema-defined prose fields contain historical short labels.
        # Structured canonical IDs, parameters and observations are not text-replaced.
        if isinstance(historical, dict):
            parent = historical.get('parent_design_id')
            if isinstance(parent, str) and _SHORT_ALIAS.fullmatch(parent):
                historical['parent_design_id'] = _historical_text(parent, labels)
            diagnosis = historical.get('diagnosis')
            if isinstance(diagnosis, dict):
                if 'explanation' in diagnosis:
                    diagnosis['explanation'] = _historical_text(diagnosis['explanation'], labels)
                identity = diagnosis.get('diagnosis_id')
                if isinstance(identity, str) and _SHORT_ALIAS.fullmatch(identity):
                    diagnosis['diagnosis_id'] = _historical_text(identity, labels)
                elif isinstance(identity, str):
                    plan['current_diagnostic_reference'] = diagnosis_reference(identity)
            elif diagnosis is not None:
                historical['diagnosis'] = _historical_text(diagnosis, labels)
        else:
            historical = _historical_text(historical, labels)
        plan['historical_model_plan'] = historical
        plan['current_parent_bindings'] = [
            {'design_id': parent, 'parent_alias': pa[parent]}
            for parent in sorted(parents_by_plan.get(plan['plan_index'], set()))]
    view['identity_scope'] = {
        'snapshot_manifest_sha256': previous['manifest_sha256'],
        'current_aliases': 'Only current_parent_alias/current_design_alias and explicit current references use this snapshot.',
        'historical_labels': sorted(labels),
        'historical_label_resolution': 'Unresolved historical display labels; never look them up in the current alias map.',
        'historical_statement_scope': 'Historical model plans and rationales are interpretations, not certified facts. '
                                      'verified_references validates cited identities only, not prose or predictions.',
        'measured_comparison_scope': 'Branch parent_metrics, selected metrics, deltas_from_parent and outcomes '
                                     'are saved measured comparisons bound to their stable design IDs. '
                                     'Do not substitute a different baseline or combine values from different designs.'}
    return view


def reflect_on_branches(previous_search, *, output, planner):
    if planner is None:
        raise ValueError('an explicitly injected audited planner is required')
    previous_search = Path(previous_search)
    # Validate the complete saved search before reading its bound feedback file.
    previous = st.read_search(previous_search)
    manifest = st.load(previous_search / 'manifest.json')
    if 'branch_feedback.json' not in manifest:
        raise ValueError('previous search has no bound directional branch feedback')
    feedback = st.load(previous_search / 'branch_feedback.json')
    feedback_view = _feedback_decision_view(feedback, previous, st._aliases(previous))
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
            forwarded['user_payload']['directional_branch_feedback'] = deepcopy(feedback_view)
            forwarded['system_prompt'] += (
                '\nReview the actual branch feedback before proposing the next coarse plans. '
                'Explain which observations support continuing, broadening, or changing a hypothesis. '
                'Different opinions and speculative interpretations are allowed. '
                'Historical model statements are not measured facts; their historical[Pnnn]/historical[Dnnn] '
                'labels must not be resolved with the current P/D aliases. Use explicit stable IDs and '
                'current bindings to compare each full measured metric vector against its actual baseline. '
                'Separate the model-proposed seed from subsequent numerical refinement. '
                'Cached rediscoveries are not independent new experiments. '
                'Failure within a finite search budget does not disprove a hypothesis. '
                'These next plans remain unexecuted; do not claim new outcomes.')
            # st.revise has created output before dispatch; this is the actual
            # forwarded request, while revision_prompt.json is its base view.
            st.save(output / 'directional_reflection_prompt.json', forwarded)
            st.save(output / 'directional_reflection_feedback_source.json', feedback)
            st.save(output / 'directional_reflection_binding.json', {
                'feedback_sha256': st.digest(feedback),
                'feedback_view_sha256': st.digest(feedback_view),
                'feedback_source_file': 'directional_reflection_feedback_source.json',
                'snapshot_manifest_sha256': previous['manifest_sha256'],
                'actual_forwarded_prompt_sha256': st.digest(forwarded),
                'base_prompt_file': 'revision_prompt.json',
                'actual_prompt_file': 'directional_reflection_prompt.json',
                'next_action_executed': False})
            return planner._call_query_plan_model(forwarded)

    return st.revise(previous_search, output=output, planner=FeedbackPlanner())
