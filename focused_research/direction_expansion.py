"""One injected model call expands hypotheses into concrete, untested seeds.

No semantic direction interpreter, implicit client, solver, or automatic retry lives
here. Validation enforces execution identity and the inherited physical contract;
it does not grade hypotheses or require agreement with a model's explanation.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from .scientific_stage import _new_output, digest, load, save
from . import search_stage as ss

SCHEMA = 'focused_research.direction_expansion.v1'


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _plan_list(plans):
    rows = plans.get('plans') if isinstance(plans, dict) else plans
    if not isinstance(rows, list):
        raise ValueError('coarse plans must contain a plans list')
    return rows


def _parent_view(previous, plan, index):
    if not isinstance(plan, dict):
        raise ValueError('coarse plan must be an object')
    parent = plan.get('parent_design_id')
    parent = ss._aliases(previous)['parents'].get(parent, parent)
    if parent not in previous['archive']['designs']:
        raise ValueError('plan parent must be a certified design')
    action = plan.get('action')
    if action not in ('refine_family', 'refine_layers'):
        raise ValueError('unsupported coarse plan action')
    mode = 'family' if action == 'refine_family' else 'layerwise_refinement'
    envelope = previous['envelopes'][parent]
    route = next(r for r in previous['accepted']['routes'] if r['id'] == envelope['route_id'])
    # Also verify actual archived parent's ties and fixed physical inputs.
    ss._validate_candidate(envelope, envelope, route, mode)
    variables = {}
    for i, (spec, physical) in enumerate(zip(route['family']['layers'], envelope['tmm_task']['stack']['layers'])):
        variable = spec['thickness_nm']
        if isinstance(variable, str):
            name = variable if mode == 'family' else 'layer_%d:%s' % (i, variable)
            variables[name] = {'value_nm': physical['thickness_nm'],
                               'bounds_nm': copy.deepcopy(route['family']['thickness_variables_nm'][variable])}
    hypothesis = plan.get('hypothesis_id', 'plan_%04d' % index)
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError('hypothesis_id must be nonempty text')
    return {'plan_index': index, 'hypothesis_id': hypothesis, 'parent_design_id': parent,
            'parameterization': mode, 'variables': variables,
            'coarse_plan': copy.deepcopy(plan), 'route': copy.deepcopy(route),
            'parent_envelope': copy.deepcopy(envelope)}


def validate_expansion(payload, previous, coarse_plans):
    """Compile each branch independently; retain rejected proposals for audit.

    Concrete values must come from the model response. Duplicate physical seeds
    remain separate hypothesis branches; downstream execution can deduplicate work.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('branches'), list):
        raise ValueError('expansion response must contain a branches list')
    plans = _plan_list(coarse_plans)
    jobs, rejected, seen, audits = [], [], set(), []
    allowed = {'branch_id', 'plan_index', 'parent_design_id', 'hypothesis_id',
               'parameterization', 'parameters_nm', 'search_bounds_nm', 'rationale'}
    for index, branch in enumerate(payload['branches']):
        try:
            if not isinstance(branch, dict):
                raise ValueError('branch must be an object')
            ignored = sorted(str(key) for key in set(branch) - allowed)
            bindings = {}
            branch_id = branch.get('branch_id')
            if not isinstance(branch_id, str) or not branch_id.strip() or branch_id in seen:
                raise ValueError('branch_id must be unique nonempty text')
            pi = branch.get('plan_index')
            if type(pi) is not int or not 0 <= pi < len(plans):
                raise ValueError('invalid plan_index')
            view = _parent_view(previous, plans[pi], pi)
            for key in ('parent_design_id', 'hypothesis_id', 'parameterization'):
                if key not in branch:
                    bindings[key] = view[key]
                elif branch[key] != view[key]:
                    raise ValueError('branch ' + key + ' does not match its selected plan')
            params = branch.get('parameters_nm')
            variables = view['variables']
            if not isinstance(params, dict) or set(params) != set(variables):
                raise ValueError('parameters_nm must assign every executable variable exactly once')
            overrides = branch.get('search_bounds_nm', {})
            if not isinstance(overrides, dict) or set(overrides) - set(variables):
                raise ValueError('search_bounds_nm contains unknown variables')
            for name, bounds in overrides.items():
                original = variables[name]['bounds_nm']
                if (not isinstance(bounds, list) or len(bounds) != 2 or
                        not all(_finite(x) for x in bounds) or
                        not original[0] <= bounds[0] <= bounds[1] <= original[1]):
                    raise ValueError('branch bounds must be finite ordered subsets of original bounds')
            for name, value in params.items():
                bounds = overrides.get(name, variables[name]['bounds_nm'])
                if not _finite(value) or not bounds[0] <= value <= bounds[1]:
                    raise ValueError('seed parameter must be finite and inside effective bounds')
            envelope = copy.deepcopy(view['parent_envelope'])
            for i, (spec, physical) in enumerate(zip(view['route']['family']['layers'], envelope['tmm_task']['stack']['layers'])):
                variable = spec['thickness_nm']
                if isinstance(variable, str):
                    name = variable if view['parameterization'] == 'family' else 'layer_%d:%s' % (i, variable)
                    physical['thickness_nm'] = params[name]
            ss._validate_candidate(envelope, view['parent_envelope'], view['route'], view['parameterization'])
            envelope['instance_id'] = 'direction_seed_' + digest({'branch': branch, 'parent': view['parent_design_id']})[:24]
            envelope['tmm_task']['stack']['name'] = envelope['instance_id']
            # Old derivative/search observations concern the parent, not this seed.
            for key in ('numerical_search', 'numeric_search_observations', 'source_instance_id', 'parent_instance_id'):
                envelope.pop(key, None)
            lineage = {key: view[key] for key in ('plan_index', 'hypothesis_id', 'parent_design_id', 'parameterization')}
            lineage['branch_id'] = branch_id
            envelope['direction_expansion'] = {**lineage, 'status': 'untested_model_seed'}
            jobs.append({**lineage, 'route': view['route'], 'seed_envelopes': [envelope],
                         'parameters_nm': copy.deepcopy(params), 'search_bounds_nm': copy.deepcopy(overrides),
                         'rationale': copy.deepcopy(branch.get('rationale', ''))})
            audits.append({'source_index': index, 'branch_id': branch_id,
                           'ignored_fields': ignored, 'ignored_fields_executed': False,
                           'bound_from_selected_plan': bindings})
            seen.add(branch_id)
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            rejected.append({'source_index': index, 'error': str(exc), 'raw_branch': copy.deepcopy(branch)})
    return {'jobs': jobs, 'report': {'accepted_count': len(jobs), 'rejected_count': len(rejected),
            'rejected_branches': rejected, 'normalization_audit': audits, 'physics_executed': False,
            'limits': ['Accepted means executable proposal, not a verified hypothesis.',
                       'No scientific-opinion gate or fixed candidate-count ceiling.',
                       'Execution must reserve shared search and certification budgets before dispatch.']}}


def _prompt_payload(previous, views, invalid_plans, max_forward_evaluations):
    """Lossless task/selected-design projection; no unrelated archive or papers."""
    catalog, projected = {}, []
    for view in views:
        task = copy.deepcopy(view['parent_envelope']['tmm_task'])
        stack = task.pop('stack')
        stack.pop('name', None)
        layers = stack.pop('layers')
        mapping = []
        for i, (layer, spec) in enumerate(zip(layers, view['route']['family']['layers'])):
            row = {k: v for k, v in layer.items() if k != 'thickness_nm'}
            variable = spec['thickness_nm']
            if isinstance(variable, str):
                row['variable_name'] = variable if view['parameterization'] == 'family' else 'layer_%d:%s' % (i, variable)
            else:
                row['fixed_thickness_nm'] = layer['thickness_nm']
            mapping.append(row)
        definition = {'layers': mapping, 'stack_conditions': stack, 'physical_conditions': task}
        fid = 'family_' + digest(definition)[:16]
        catalog.setdefault(fid, definition)
        projected.append({k: copy.deepcopy(view[k]) for k in
                          ('plan_index', 'hypothesis_id', 'parent_design_id', 'parameterization', 'variables', 'coarse_plan')})
        projected[-1].update(family_id=fid, parent_metrics=copy.deepcopy(
            previous['archive']['designs'][view['parent_design_id']].get('metrics', {})))
    context = previous['context']
    sheet = context.get('sheet', {})
    return {'original_question': copy.deepcopy(sheet.get('original_question')),
            'original_objectives': copy.deepcopy(previous['archive']['objectives']),
            'subgoals': copy.deepcopy(sheet.get('subgoals', [])),
            'goal_relationships': copy.deepcopy(sheet.get('goal_relationships')),
            'explicit_constraints': copy.deepcopy(sheet.get('explicit_constraints', [])),
            'design_constraints': copy.deepcopy(context.get('constraints')),
            'plans': projected, 'family_catalog': catalog, 'invalid_plan_inputs': invalid_plans,
            'max_forward_evaluations': max_forward_evaluations}


def expand_directions(previous_search, plans, *, output, planner, max_forward_evaluations):
    """Persist one audited injected Qwen-planner call, without numerical execution."""
    if planner is None:
        raise ValueError('an explicitly injected audited planner is required')
    ss._budget(max_forward_evaluations, 'max_forward_evaluations')
    previous = ss.read_search(previous_search)
    rows = _plan_list(plans)
    views, invalid_plans = [], []
    for i, plan in enumerate(rows):
        try:
            views.append(_parent_view(previous, plan, i))
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            invalid_plans.append({'plan_index': i, 'error': str(exc)})
    output = _new_output(output, [previous_search])
    prompt = {'system_prompt':
        'Expand the supplied coarse scientific hypotheses into concrete numerical search branches. '
        'Return JSON only: {"branches":[...]}. Each branch has unique branch_id, plan_index, '
        'parent_design_id, hypothesis_id, parameterization, parameters_nm, optional search_bounds_nm, '
        'and rationale. Copy plan_index, parent_design_id, hypothesis_id and parameterization from '
        'one supplied plan. parameters_nm assigns every listed variable a finite thickness in nm. '
        'Optional search_bounds_nm maps existing variables to [lower_nm,upper_nm] inside their original '
        'bounds; the seed must lie inside them. Preserve fixed layers, materials, layer count, all '
        'original goals and conditions. Do not emit layer JSON or new objective definitions. '
        'You perform the semantic interpretation: choose concrete diverse starts, step sizes and '
        'coupled/independent changes that test each hypothesis, including alternatives or counterdirections '
        'where informative. Do not merely repeat the parent. Ten or more branches are welcome when '
        'the shared numerical budget makes them useful; there is no fixed branch-count ceiling. '
        'All branches share the supplied total search budget, including refinement. Weak, conflicting '
        'or speculative hypotheses are permitted. Rationale and predicted improvements are untested, '
        'not established mechanisms or outcomes. An empty branches list is permitted.',
        'user_payload': _prompt_payload(previous, views, invalid_plans, max_forward_evaluations)}
    save(output / 'expansion_parent.json', {'previous_search': str(Path(previous_search).resolve()),
         'manifest_sha256': previous['manifest_sha256'], 'next_action_executed': False})
    save(output / 'context_bindings.json', {k: digest(v) for k, v in previous['context'].items()})
    save(output / 'coarse_plans.json', plans)
    save(output / 'expansion_prompt.json', prompt)
    status, error, normalization = 'expansion_failed', None, None
    if not views:
        summary = {'schema_version': SCHEMA, 'stage': 'direction_expansion',
                   'status': 'no_executable_plan', 'planner_calls': 0, 'implicit_retries': 0,
                   'next_action_executed': False, 'generation_complete': None,
                   'invalid_plan_inputs': invalid_plans}
        save(output / 'summary.json', summary)
        ss._seal(output)
        return summary
    try:
        response = planner._call_query_plan_model(copy.deepcopy(prompt))
        try:
            json.dumps(response, allow_nan=False)
        except (ValueError, TypeError) as exc:
            save(output / 'raw_response.json', {'response': None, 'response_repr': repr(response),
                 'serialization_error': str(exc)})
            raise ValueError('model response is not finite JSON serializable') from exc
        save(output / 'raw_response.json', {'response': response})
        parsed = response.strip() if isinstance(response, str) else response
        if isinstance(parsed, str) and parsed.startswith(('```json\n', '```\n')) and parsed.endswith('```'):
            parsed = '\n'.join(parsed.splitlines()[1:-1])
            normalization = 'removed_single_outer_json_code_fence'
        def reject_constant(value):
            raise ValueError('nonfinite JSON constant: ' + value)
        payload = json.loads(parsed, parse_constant=reject_constant) if isinstance(parsed, str) else parsed
        compiled = validate_expansion(payload, previous, plans)
        save(output / 'expansion_response.json', payload)
        save(output / 'expansion_jobs.json', {'jobs': compiled['jobs']})
        save(output / 'expansion_report.json', compiled['report'])
        status = 'branches_partial' if compiled['report']['rejected_count'] else 'branches_recorded'
    except Exception as exc:
        error = str(exc)
        if not (output / 'raw_response.json').exists():
            save(output / 'raw_response.json', {'response': None, 'transport_error': error})
    finish_reason = getattr(planner, 'last_transport', {}).get('finish_reason')
    complete = None if finish_reason is None else finish_reason == 'stop'
    if complete is False and status == 'branches_recorded':
        status = 'branches_partial'
    summary = {'schema_version': SCHEMA, 'stage': 'direction_expansion', 'status': status,
               'error': error, 'planner_calls': 1, 'implicit_retries': 0,
               'generation_complete': complete, 'next_action_executed': False,
               'response_normalization': normalization,
               'usage': getattr(planner, 'last_usage', {}),
               'transport': getattr(planner, 'last_transport', {})}
    save(output / 'summary.json', summary)
    ss._seal(output)
    return summary
