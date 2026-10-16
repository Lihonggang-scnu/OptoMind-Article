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
# T032 extends the cold-start protocol inventory, not the numerical engine.
# Unspecified physical conditions are labeled execution assumptions; none of
# these three original questions supplies a global layer-thickness interval.
CASE_PROTOCOLS.update({
    'e2e-01-methane-swir-window': {
        'assumptions': ['Use 0 degree incidence, equal-s/p unpolarized light and wavelength-integral band means as separately labeled execution assumptions, not original user facts.'],
        'requirements': ['Preserve transmission maximization in 1000–1700 nm and reflection maximization in 300–450 nm, air incidence, fused silica, HfO2/SiO2 and at most 30 layers.', 'The original question provides no global thickness bounds or chirping requirement. Finite positive family-variable domains and structure families are model design hypotheses.'],
    },
    'e2e-04-solarblind-uv-triple': {
        'assumptions': ['Use 0 degree incidence, equal-s/p unpolarized light and wavelength-integral band means as separately labeled execution assumptions, not original user facts.', 'Evaluate suppression as transmission minimization in each original rejected band. This is an explicit execution interpretation, not an added reflection objective.'],
        'requirements': ['Maximize transmission in 255–280 nm; minimize transmission in 300–700 nm and 700–1100 nm. Preserve air incidence, fused silica, at most 30 layers and the original prohibition on metal layers.', 'The user permits ordinary depositable inorganic dielectric materials, not a named two-material set. Actual available optical-media records are an execution inventory and must be disclosed separately. Do not invent a narrower original material requirement.', 'No global user thickness bounds were specified. Use finite positive model-proposed family domains without inventing manufacturing constraints.'],
    },
    'e2e-05-visual-absorber-cr': {
        'assumptions': ['Use 0 degree incidence, equal-s/p unpolarized light and wavelength-integral band means as separately labeled execution assumptions, not original user facts.', 'Treat the stated aluminum substrate as a semi-infinite absorbing exit medium for this planar-surface calculation; disclose this boundary assumption.'],
        'requirements': ['Use the original explicit low-reflectance parenthetical to minimize mean R in 400–700 nm, and maximize mean R in 700–2000 nm. Preserve the user absorption motivation, HfO2/SiO2/Cr film allowance, aluminum substrate, air incidence and at most 20 layers.', 'A_finite=1-R-T_into_exit measures finite-film absorption only. Do not label it coating-plus-substrate absorption. For the stated absorbing semi-infinite surface system, 1-R is system absorptance; this does not add a third optimization goal or a heat-power objective.', 'No global user thickness bounds were specified. Use finite positive model-proposed family domains without inventing manufacturing constraints.'],
    },
})
# T042 admits the original LWIR Ge-window question to the existing cold-start
# flow. This protocol supplies no solver, new material data, or thermal model.
CASE_PROTOCOLS['e2e-12-lwir-ge-window'] = {
    'assumptions': [
        'Use 0 degree incidence, equal-s/p unpolarized light and wavelength-integral band means as separately labeled execution assumptions, not original user facts.',
        'Treat the Ge substrate as a semi-infinite exit medium for the planar-stack calculation; disclose this boundary assumption.',
    ],
    'requirements': [
        'Maximize mean transmittance in 8000–11000 nm and minimize mean transmittance in 3000–5000 nm. Preserve air incidence, Ge substrate, only ZnS/ZnSe film materials and at most 16 layers. Do not replace either transmission goal with reflection or absorption objectives.',
        'T_into_exit is optical flux entering the semi-infinite Ge substrate, not transmission through an entire finite-thickness window including its rear surface. Keep this scope explicit in results.',
        'For any optical-constant source without extinction coefficient k, disclose the existing provider k=0 convention. Missing source k does not establish physically absent absorption.',
        'Temperature, substrate thickness and rear-surface conditions were not supplied; do not invent them. Preserve thermal-background and focal-plane heat-load reduction as application motivation, without adding heat-load, blackbody-power or temperature-weighted objectives or claiming those quantities were calculated.',
        'No global user thickness bounds were specified. Use finite positive model-proposed family domains without inventing manufacturing constraints.',
    ],
}
CASE_THICKNESS_BOUNDS = {
    'e2e-09-si-photonics-ar-dualband': (20.0, 400.0),
    'e2e-08-advanced-packaging-uv': (20.0, 350.0),
    'e2e-02-space-qkd-cband': None,
    'e2e-01-methane-swir-window': None,
    'e2e-04-solarblind-uv-triple': None,
    'e2e-05-visual-absorber-cr': None,
    'e2e-12-lwir-ge-window': None,
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
    _stage_dispatch_limits = {'search_revision': 1}

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
        if count >= self._stage_dispatch_limits.get(stage, 2):
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

    For a continuation sharing an earlier case-attempt ceiling, the caller must
    supply both prior_dispatch_attempts and cumulative_dispatch_limit after
    reconciling the original history. The continuation allowance is the smaller
    of three and the remaining cumulative allowance. Both base stages are
    reserved; two prior attempts under a four-attempt ceiling permit one route
    call and one revision, with no route repair. Omitting both parameters keeps
    the original three-attempt continuation contract and audit shape.

    The caller must verify the recovered sheet and its recovery manifest, and
    supply that manifest as recovered_task_sheet_binding. This constructor only
    checks a matching case ID, a canonical SHA-256 string, and JSON audit data;
    it does not read or independently verify the artifact. The full copied
    binding accompanies every pre-dispatch durable audit without changing the
    forwarded prompt beyond the inherited CASE_PROTOCOLS.

    Stage counts cover this continuation only. When explicitly supplied, the
    prior-attempt limits and conservative cumulative count are separately
    audited; they do not establish actual HTTP counts. Prior attempts and costs
    remain in the original durable ledger: admission must reconcile and reserve
    against the cumulative per-case/project limits there. Never recreate this
    object to reset attempts after a continuation failure or crash. The caller
    must also persist the final audit in finally, as with CampaignPlannerGuard.
    """
    _stage_order = ('route_generation', 'search_revision')
    _max_dispatch_attempts = 3

    def __init__(self, planner, *, case_id, audit_sink, recovered_task_sheet_binding,
                 prior_dispatch_attempts=None, cumulative_dispatch_limit=None):
        self._recovered_attempt_budget = None
        if prior_dispatch_attempts is not None or cumulative_dispatch_limit is not None:
            if prior_dispatch_attempts is None or cumulative_dispatch_limit is None:
                raise ValueError('prior_dispatch_attempts and cumulative_dispatch_limit must be supplied together')
            if type(prior_dispatch_attempts) is not int or prior_dispatch_attempts < 0:
                raise ValueError('prior_dispatch_attempts must be a nonnegative integer')
            if type(cumulative_dispatch_limit) is not int or cumulative_dispatch_limit < 1:
                raise ValueError('cumulative_dispatch_limit must be a positive integer')
            remaining = cumulative_dispatch_limit - prior_dispatch_attempts
            if remaining < len(self._stage_order):
                raise ValueError('cumulative dispatch allowance must reserve route generation and revision')
            self._max_dispatch_attempts = min(self._max_dispatch_attempts, remaining)
            self._recovered_attempt_budget = {
                'prior_dispatch_attempts': prior_dispatch_attempts,
                'cumulative_dispatch_limit': cumulative_dispatch_limit,
                'continuation_dispatch_limit': self._max_dispatch_attempts,
                'actual_http_and_costs_source': 'original_audited_transport_and_ledger',
            }
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
        if self._recovered_attempt_budget is not None:
            budget = copy.deepcopy(self._recovered_attempt_budget)
            budget['cumulative_conservative_dispatch_attempts'] = (
                budget['prior_dispatch_attempts'] + snapshot['dispatch_attempts'])
            snapshot['recovered_attempt_budget'] = budget
        return snapshot


class RecoveredRevisionPlannerGuard(RecoveredCampaignPlannerGuard):
    """Explicit new-work-order continuation using already recovered routes.

    Only search_revision may dispatch, at most twice and at most once per
    distinct physical-search snapshot. No route call or format/transport repair
    is admitted. A single revision is sufficient; another is allowed only after
    the caller obtains new physical feedback and verifies its new manifest.

    The continuation task ID, reconciled prior attempts, and cumulative ceiling
    are mandatory. For T040 the launcher must verify and pass prior=4, limit=6;
    this generic class does not infer, reset, or independently verify history.
    The recovered binding must also identify the unchanged original question by
    its UTF-8 SHA-256. Existing search_stage.revise prompts carry that question
    and identity_scope.snapshot_manifest_sha256; no model text or opinion is
    judged here. The caller verifies manifests, recovered routes, physical
    feedback, and task-sheet identity before admission. This class checks only
    their explicit bindings and dispatch allowance, never scientific quality.

    Start search_revision once, then use the same object for the permitted
    calls. Persist every pre-dispatch audit and the final audit in finally.
    Audit/transport failure closes the guard; do not recreate it to retry.
    Actual HTTP, prior/current money, pending/held/uncertain liabilities and
    cumulative case/project caps remain the original transport/ledger's facts.
    """
    _stage_order = ('search_revision',)
    _max_dispatch_attempts = 2
    _stage_dispatch_limits = {'search_revision': 2}

    def __init__(self, planner, *, case_id, audit_sink, recovered_task_sheet_binding,
                 continuation_task_id, prior_dispatch_attempts, cumulative_dispatch_limit):
        if not isinstance(continuation_task_id, str) or not continuation_task_id.strip():
            raise ValueError('explicit continuation_task_id is required')
        if type(prior_dispatch_attempts) is not int or prior_dispatch_attempts < 0:
            raise ValueError('explicit reconciled prior_dispatch_attempts must be a nonnegative integer')
        if type(cumulative_dispatch_limit) is not int or cumulative_dispatch_limit < 1:
            raise ValueError('explicit cumulative_dispatch_limit must be a positive integer')
        question_digest = (recovered_task_sheet_binding.get('original_question_sha256')
                           if isinstance(recovered_task_sheet_binding, dict) else None)
        if not isinstance(question_digest, str) or len(question_digest) != 64 or any(
                char not in '0123456789abcdef' for char in question_digest):
            raise ValueError('revision continuation requires a canonical original_question_sha256')
        self._continuation_task_id = continuation_task_id
        self._revision_snapshot_manifest_hashes = []
        super().__init__(planner, case_id=case_id, audit_sink=audit_sink,
                         recovered_task_sheet_binding=recovered_task_sheet_binding,
                         prior_dispatch_attempts=prior_dispatch_attempts,
                         cumulative_dispatch_limit=cumulative_dispatch_limit)

    @property
    def audit(self):
        snapshot = super().audit
        snapshot['revision_continuation'] = {
            'task_id': self._continuation_task_id,
            'allowed_stage': 'search_revision',
            'snapshot_manifest_sha256': list(self._revision_snapshot_manifest_hashes),
            'new_physical_feedback_verified_by': 'caller_before_each_revision',
        }
        return snapshot

    def _call_query_plan_model(self, prompt):
        if self._failed or self._stage_index < 0:
            raise ValueError('active admitted stage required; failed guards cannot retry')
        if not isinstance(prompt, dict) or not isinstance(prompt.get('user_payload'), dict):
            raise ValueError('prompt requires an object user_payload')
        payload = prompt['user_payload']
        if any(key in row for row in (prompt, payload)
               for key in ('previous_response', 'validation_error')):
            raise ValueError('revision continuation does not admit contract repair requests')
        identity = payload.get('identity_scope')
        snapshot_digest = identity.get('snapshot_manifest_sha256') if isinstance(identity, dict) else None
        if not isinstance(snapshot_digest, str) or len(snapshot_digest) != 64 or any(
                char not in '0123456789abcdef' for char in snapshot_digest):
            raise ValueError('revision requires an explicit search snapshot_manifest_sha256')
        sheet = payload.get('task_sheet')
        question = sheet.get('original_question') if isinstance(sheet, dict) else None
        if (not isinstance(question, str) or hashlib.sha256(question.encode('utf-8')).hexdigest()
                != self._recovered_task_sheet_binding['original_question_sha256']):
            raise ValueError('revision original_question does not match the recovered binding')
        if snapshot_digest in self._revision_snapshot_manifest_hashes:
            raise ValueError('revision snapshot already dispatched; new physical feedback required')
        # Record the identity in the same durable pre-dispatch audit as the
        # forwarded prompt. Preflight rejections must not consume an identity.
        previous_count = sum(self._counts.values())
        self._revision_snapshot_manifest_hashes.append(snapshot_digest)
        try:
            return super()._call_query_plan_model(prompt)
        finally:
            if sum(self._counts.values()) == previous_count:
                self._revision_snapshot_manifest_hashes.pop()


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
