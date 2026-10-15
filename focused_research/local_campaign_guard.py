"""Initial-admission-only campaign adapters around an audited local planner.

This is not a transport, HTTP counter, or money ledger. The caller must reconcile
and reserve in its original ledger, disable transport retries, and persist the
provided audit snapshots before dispatch. Never automatically recreate this
object after a crash: reconcile original persisted accounting first. No resume
or automatic retry is implemented here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math

STAGES = ('task_sheet', 'route_generation', 'search_revision')
CASE_PROTOCOLS = {
    'e2e-09-si-photonics-ar-dualband': {
        'assumptions': ['Use wavelength-integral band means as an evaluation protocol; the original question says average but does not define the reduction.'],
        'requirements': ['Preserve the two original reflection-minimization goals. Report transmission into semi-infinite silicon without adding a transmission-maximization objective.', 'Original layer thickness limits are 20–400 nm.'],
    },
    'e2e-08-advanced-packaging-uv': {
        'assumptions': ['Use wavelength-integral band means as an evaluation protocol; the original question says average but does not define the reduction.'],
        'requirements': ['Preserve transmission suppression in 355–365 and 1030–1080 nm and transmission maximization in 420–700 nm. Contextual excitation near 355 nm is not an additional point goal.', 'Original layer thickness limits are 20–350 nm.'],
    },
    'e2e-02-space-qkd-cband': {
        'assumptions': ['Use 0 degree incidence and equal-s/p unpolarized light as new execution assumptions, not original user statements.', 'Use wavelength-integral band means as an evaluation protocol, not an original user definition.'],
        'requirements': ['Preserve transmission maximization in 1530–1565 nm and reflection maximization in 400–700 nm.', 'The original question provides no global thickness bounds. Do not invent user manufacturing limits. Finite route-family variable domains are model design hypotheses and must be labeled accordingly.'],
    },
}
CASE_THICKNESS_BOUNDS = {
    'e2e-09-si-photonics-ar-dualband': (20.0, 400.0),
    'e2e-08-advanced-packaging-uv': (20.0, 350.0),
    'e2e-02-space-qkd-cband': None,
}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class CampaignPlannerGuard:
    """One case, three ordered stages, at most four conservative dispatch attempts.

    audit_sink(snapshot) must durably save the snapshot or raise. It receives
    both original and actual forwarded prompts before the underlying planner is
    called. Actual HTTP and costs remain the original audited transport's facts.
    The constructor supports only a freshly admitted initial run, never resume.
    """
    _stage_order = STAGES
    _max_dispatch_attempts = 4

    def __init__(self, planner, *, case_id, audit_sink):
        if case_id not in CASE_PROTOCOLS:
            raise ValueError('unknown campaign case')
        if not callable(audit_sink):
            raise ValueError('a durable audit_sink is required')
        self._planner, self.case_id, self._audit_sink = planner, case_id, audit_sink
        self._stage_index = -1
        self._counts = dict.fromkeys(self._stage_order, 0)
        self._repair_used = False
        self._events = []
        self._failed = False

    @property
    def last_usage(self):
        return self._planner.last_usage

    @property
    def last_transport(self):
        return self._planner.last_transport

    @property
    def audit(self):
        return copy.deepcopy({'case_id': self.case_id,
            'dispatch_attempts': sum(self._counts.values()), 'stage_counts': self._counts,
            'shared_contract_repair_used': self._repair_used, 'failed_closed': self._failed,
            'events': self._events})

    def set_stage(self, stage):
        if self._failed:
            raise ValueError('guard failed closed; reconcile original accounting, no automatic restart')
        if stage not in self._stage_order:
            raise ValueError('unknown stage')
        index = self._stage_order.index(stage)
        if index != self._stage_index + 1:
            raise ValueError('stages cannot reset, repeat, reverse, or skip')
        if self._stage_index >= 0 and not self._counts[self._stage_order[self._stage_index]]:
            raise ValueError('previous stage has no dispatch')
        self._stage_index = index

    def _call_query_plan_model(self, prompt):
        if self._failed or self._stage_index < 0:
            raise ValueError('active admitted stage required; failed guards cannot retry')
        stage = self._stage_order[self._stage_index]
        if not isinstance(prompt, dict) or not isinstance(prompt.get('user_payload'), dict):
            raise ValueError('prompt requires an object user_payload')
        count = self._counts[stage]
        repair = count == 1 and stage != 'search_revision'
        if count >= (1 if stage == 'search_revision' else 2):
            raise ValueError('stage dispatch limit reached')
        if repair:
            payload = prompt['user_payload']
            if self._repair_used:
                raise ValueError('shared contract repair already used; preserve revision allowance')
            if 'previous_response' not in payload or not isinstance(payload.get('validation_error'), str) or not payload['validation_error'].strip():
                raise ValueError('second attempt requires explicit contract validation failure')
        remaining_base_calls = len(self._stage_order) - self._stage_index - 1
        if sum(self._counts.values()) + 1 + remaining_base_calls > self._max_dispatch_attempts:
            raise ValueError('aggregate dispatch limit would consume reserved base calls')
        forwarded = copy.deepcopy(prompt)
        if 'execution_protocol' in forwarded['user_payload']:
            raise ValueError('execution_protocol collision; refusing to overwrite')
        forwarded['user_payload']['execution_protocol'] = {
            'case_id': self.case_id, 'stage': stage,
            'original_question_policy': 'Preserve original_question verbatim; these assumptions are separate from original user constraints.',
            **copy.deepcopy(CASE_PROTOCOLS[self.case_id])}
        event = {'stage': stage, 'stage_attempt': count + 1, 'contract_repair': repair,
                 'original_prompt': copy.deepcopy(prompt), 'original_prompt_sha256': _hash(prompt),
                 'forwarded_prompt': copy.deepcopy(forwarded), 'forwarded_prompt_sha256': _hash(forwarded),
                 'status': 'dispatch_attempt_recorded'}
        self._counts[stage] += 1
        self._repair_used |= repair
        self._events.append(event)
        try:
            self._audit_sink(self.audit)  # Persist actual forwarded request before delegation.
            response = self._planner._call_query_plan_model(forwarded)
        except Exception as exc:
            self._failed = True
            event.update(status='failed_closed', exception_type=type(exc).__name__)
            # The pre-dispatch snapshot already exists if transport failed. Caller
            # persists final audit in finally; never mask the original exception.
            raise
        event['status'] = 'delegate_returned'
        return response


class RecoveredCampaignPlannerGuard(CampaignPlannerGuard):
    """One newly admitted continuation after offline task-sheet recovery.

    Only route_generation (plus at most one explicit contract repair) and then
    search_revision may dispatch: at most three conservative attempts total.
    No task-sheet call, transport retry, automatic restart, or resume is added.

    The caller must verify the recovered sheet and its recovery manifest, and
    supply that manifest as recovered_task_sheet_binding. This constructor only
    checks a matching case ID, a canonical SHA-256 string, and JSON audit data;
    it does not read or independently verify the artifact. The full copied
    binding accompanies every pre-dispatch durable audit without changing the
    forwarded prompt beyond the inherited CASE_PROTOCOLS.

    Counts cover this continuation only. Prior task-sheet attempts and costs
    remain in the original durable ledger: admission must reconcile and reserve
    against the cumulative per-case/project limits there. Never recreate this
    object to reset attempts after a continuation failure or crash. The caller
    must also persist the final audit in finally, as with CampaignPlannerGuard.
    """
    _stage_order = ('route_generation', 'search_revision')
    _max_dispatch_attempts = 3

    def __init__(self, planner, *, case_id, audit_sink, recovered_task_sheet_binding):
        if not isinstance(recovered_task_sheet_binding, dict):
            raise ValueError('explicit recovered_task_sheet_binding object is required')
        if recovered_task_sheet_binding.get('case_id') != case_id:
            raise ValueError('recovered task-sheet binding case_id must match the admitted case')
        digest = recovered_task_sheet_binding.get('task_sheet_sha256')
        if not isinstance(digest, str) or len(digest) != 64 or any(
                char not in '0123456789abcdef' for char in digest):
            raise ValueError('recovered task-sheet binding requires a canonical task_sheet_sha256')
        try:
            binding = copy.deepcopy(recovered_task_sheet_binding)
            _hash(binding)  # Reject data that cannot be preserved in a JSON audit.
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError('recovered task-sheet binding must contain finite JSON audit data') from exc
        self._recovered_task_sheet_binding = binding
        super().__init__(planner, case_id=case_id, audit_sink=audit_sink)

    @property
    def audit(self):
        snapshot = super().audit
        snapshot['recovered_task_sheet_binding'] = copy.deepcopy(self._recovered_task_sheet_binding)
        return snapshot


def check_route_thickness_bounds(routes, *, case_id):
    """Pure additional per-route range check, not full route-schema validation.

    No clipping, edits, synthesized domains, or new QKD manufacturing bounds.
    Existing contract validation must still validate all other route fields.
    """
    if case_id not in CASE_THICKNESS_BOUNDS:
        raise ValueError('unknown campaign case')
    rows = routes.get('routes') if isinstance(routes, dict) else routes
    if not isinstance(rows, list):
        raise ValueError('routes must be a list or a contract containing routes')
    limits = CASE_THICKNESS_BOUNDS[case_id]
    results = []
    def finite(v):
        return type(v) in (int, float) and math.isfinite(v)
    for index, route in enumerate(rows):
        errors = []
        rid = route.get('id') if isinstance(route, dict) else None
        family = route.get('family') if isinstance(route, dict) else None
        if not isinstance(family, dict):
            errors.append('family must be an object')
        else:
            variables = family.get('thickness_variables_nm')
            layers = family.get('layers')
            if not isinstance(variables, dict):
                errors.append('thickness_variables_nm must be an object')
                variables = {}
            for name, bounds in variables.items():
                if not isinstance(bounds, list) or len(bounds) != 2 or not all(finite(v) for v in bounds) or not 0 < bounds[0] <= bounds[1]:
                    errors.append(f'variable {name}: invalid positive finite interval')
                elif limits and not limits[0] <= bounds[0] <= bounds[1] <= limits[1]:
                    errors.append(f'variable {name}: interval exceeds original user thickness bounds {limits}')
            if not isinstance(layers, list) or not layers:
                errors.append('layers must be a nonempty list')
            else:
                for pos, layer in enumerate(layers):
                    value = layer.get('thickness_nm') if isinstance(layer, dict) else None
                    if isinstance(value, str):
                        if value not in variables:
                            errors.append(f'layer {pos}: undeclared thickness variable')
                    elif not finite(value) or value <= 0:
                        errors.append(f'layer {pos}: invalid positive finite thickness')
                    elif limits and not limits[0] <= value <= limits[1]:
                        errors.append(f'layer {pos}: fixed thickness exceeds original user bounds {limits}')
        results.append({'route_index': index, 'route_id': rid, 'accepted': not errors, 'errors': errors})
    return {'case_id': case_id, 'explicit_user_bounds_nm': list(limits) if limits else None,
            'route_results': results, 'mutated': False}
