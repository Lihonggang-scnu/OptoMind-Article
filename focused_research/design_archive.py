"""JSON design history and Pareto selection for certified sampled-grid results.

This is a pure archive, not a solver or certificate issuer. Pass the actual task
paired with the trusted research_execution result; do not pass proposal values.
Example::

    archive = create_design_archive(prepared['objectives'], prepared['tmm_task'])
    archive = add_certified_design(archive, result,
        physical_input=prepared['tmm_task'], role='baseline_reference')
    parent_id = archive['latest_event']['design_id']
    archive = add_certified_design(archive, next_result,
        physical_input=next_prepared['tmm_task'], parent_design_id=parent_id,
        family={'family_id': 'chirped', 'parameterization': 'per_layer_free'})

No truncation, aggregate objective weights, performance guarantees, or new
physical evaluations are introduced here. All objectives remain visible.
"""
from copy import deepcopy
import hashlib
import json
import math

try:
    from .physical_observations import _channel, _metric, _metric_spec, _values
except ImportError:
    from physical_observations import _channel, _metric, _metric_spec, _values


SCHEMA = 'focused_research.design_archive.v1'


def _canonical_numbers(value):
    """JSON numeric spelling is not physical identity; booleans stay booleans."""
    if type(value) in (int, float):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('finite JSON numbers required')
        return 0.0 if number == 0 else number
    if isinstance(value, dict):
        return {key: _canonical_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonical_numbers(item) for item in value]
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _goals(objectives):
    if not isinstance(objectives, list) or not objectives:
        raise ValueError('explicit objectives required')
    goals = [deepcopy(g.get('executable_metric', g)) for g in objectives]
    ids = [g.get('objective_id') for g in goals]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('unique nonempty objective ids required')
    for goal in goals:
        _, stat = _metric_spec(goal)
        if goal.get('sense') not in ('maximize', 'minimize'):
            raise ValueError('explicit objective direction required')
        region = goal.get('region', {})
        band = region.get('wavelength_nm', [])
        if (len(band) != 2 or any(type(x) not in (int, float) or not math.isfinite(x) for x in band)
                or not 0 < band[0] < band[1]):
            raise ValueError('explicit finite wavelength band required')
        angle = region.get('angle_deg')
        if type(angle) not in (int, float) or not math.isfinite(angle) or not 0 <= angle < 90:
            raise ValueError('explicit incidence angle required')
        if region.get('polarization') not in ('s', 'p', 'unpolarized'):
            raise ValueError('explicit polarization required')
        if stat == 'mean' and goal.get('mean_reduction') not in ('sample_mean', 'wavelength_integral_mean'):
            raise ValueError('explicit supported mean reduction required')
    return _canonical_numbers(sorted(goals, key=lambda g: g['objective_id']))


def _physical(task):
    """Keep the entire supplied task, except its nonphysical display name."""
    if not isinstance(task, dict):
        raise ValueError('complete physical input required')
    task = deepcopy(task)
    stack = task.get('stack', {})
    if not all(key in stack for key in ('layers', 'incident', 'exit')):
        raise ValueError('explicit layers, incident medium and exit medium required')
    if not stack['layers'] or not task.get('spectrum') or not task.get('illumination'):
        raise ValueError('complete physical input and conditions required')
    for layer in stack['layers']:
        thickness = layer.get('thickness_nm')
        if type(thickness) not in (int, float) or not math.isfinite(thickness) or thickness <= 0:
            raise ValueError('finite positive actual layer thickness required')
        if not layer.get('material') and layer.get('constant_n') is None:
            raise ValueError('actual optical medium required for each layer')
    stack.pop('name', None)
    _hash(task)  # Reject non-JSON/nonfinite provenance as well as metrics.
    return _canonical_numbers(task)


def _conditions(task):
    condition = deepcopy(task)
    condition['stack'].pop('layers')
    return condition


def create_design_archive(objectives, reference_physical_input):
    """Freeze every objective and condition; only the layer design may vary."""
    goals = _goals(objectives)
    conditions = _conditions(_physical(reference_physical_input))
    return {'schema_version': SCHEMA, 'objectives': goals, 'conditions': conditions,
            'context_id': _hash({'objectives': goals, 'conditions': conditions}),
            'designs': {}, 'evaluations': [], 'pareto_design_ids': [],
            'new_evidence_count': 0, 'effective_intervention_count': 0,
            'latest_event': None, 'claim_scope': 'certified_sampled_grid_only'}


def _layer_diff(parent, child):
    before = parent['stack']['layers'] if parent else []
    after = child['stack']['layers']
    rows = []
    for i in range(max(len(before), len(after))):
        a = before[i] if i < len(before) else None
        b = after[i] if i < len(after) else None
        if a == b:
            continue
        x, y = (a or {}).get('thickness_nm'), (b or {}).get('thickness_nm')
        rows.append({'layer_index': i, 'change': 'added' if a is None else 'removed' if b is None else 'changed',
                     'before': deepcopy(a), 'after': deepcopy(b), 'before_nm': x, 'after_nm': y,
                     'delta_nm': y - x if x is not None and y is not None else None})
    return {'status': 'observed' if parent else 'no_parent', 'changed_layers': rows}


def _family(task, family):
    topology = [{k: deepcopy(v) for k, v in layer.items() if k != 'thickness_nm'}
                for layer in task['stack']['layers']]
    if family is not None and (not isinstance(family, dict) or not family):
        raise ValueError('family must be a nonempty JSON object')
    specification = deepcopy(family) if family is not None else {'parameterization': 'unspecified'}
    return {'family_key': _hash({'topology': topology, 'specification': specification}),
            'topology': topology, 'specification': specification}


def pareto_design_ids(archive):
    """Exact nondominance across ALL goals, without weighted scalar collapse."""
    designs, goals = archive['designs'], archive['objectives']
    def dominates(a, b):
        deltas = [(a['metrics'][g['objective_id']]['value'] - b['metrics'][g['objective_id']]['value'])
                  * (1 if g['sense'] == 'maximize' else -1) for g in goals]
        return all(d >= 0 for d in deltas) and any(d > 0 for d in deltas)
    return sorted(key for key, design in designs.items()
                  if not any(other != key and dominates(candidate, design)
                             for other, candidate in designs.items()))


def add_certified_design(archive, result, *, physical_input, parent_design_id=None,
                         family=None, role='candidate'):
    """Return a new archive; rejected results never mutate the supplied archive.

    Cache hits may import certified historical designs but are never new evidence.
    Every ingestion is retained as an evaluation event, including repeated cache
    references and alternate ancestry. A design's first provenance stays intact.
    """
    if archive.get('schema_version') != SCHEMA:
        raise ValueError('unsupported archive schema')
    if role not in ('candidate', 'baseline_reference'):
        raise ValueError('unsupported design role')
    if (result.get('status') != 'completed' or result.get('physics_accepted') is not True
            or result.get('certificate', {}).get('accepted') is not True
            or result.get('envelope', {}).get('ok') is not True):
        raise ValueError('only completed certified physical results may enter the archive')
    if type(result.get('cache_hit')) is not bool:
        raise ValueError('explicit cache provenance required')
    if not result.get('simulation_key') or not result.get('full_result_ref'):
        raise ValueError('simulation identity and full result reference required')
    goals = _goals(result.get('objectives'))
    task = _physical(physical_input)
    binding = 'caller_contract_partial'
    if 'physical_input' in result:
        if _physical(result['physical_input']) != task:
            raise ValueError('certified result physical_input mismatch')
        binding = 'executor_physical_input_matched'
    if goals != archive['objectives']:
        raise ValueError('objective mismatch; create a separate archive')
    if _conditions(task) != archive['conditions']:
        raise ValueError('condition mismatch; create a separate archive')
    # Bind the supplied actual task to executor-exposed dimensions. The complete
    # task/certificate pairing remains the trusted executor caller's obligation.
    layers = task['stack']['layers']
    parameters = result.get('parameters', {})
    if parameters.get('thicknesses_nm') != [l['thickness_nm'] for l in layers]:
        raise ValueError('result thicknesses do not match actual physical input')
    materials = [l.get('material') or {'constant_n': l.get('constant_n'), 'constant_k': l.get('constant_k', 0.0)} for l in layers]
    if parameters.get('layer_materials') != materials:
        raise ValueError('result materials do not match actual physical input')
    explicit_grid = task['spectrum'].get('values_nm')
    if explicit_grid is not None and result.get('raw_result', {}).get('wavelengths_nm') != explicit_grid:
        raise ValueError('result grid does not match actual physical input')
    metrics = {}
    for goal in goals:
        quantity, stat = _metric_spec(goal)
        grid, channel = _channel(result, goal['region'])
        value, summary = _metric(grid, _values(channel, quantity, len(grid)), goal, stat)
        if not math.isfinite(value):
            raise ValueError('nonfinite objective metric')
        metrics[goal['objective_id']] = {'value': value, 'objective': deepcopy(goal),
                                        'sample_summary': summary}
    design_id = 'design:' + _hash({'context_id': archive['context_id'], 'physical_input': task})
    if parent_design_id is not None and parent_design_id not in archive['designs']:
        raise ValueError('parent design must exist in this archive')
    parent = archive['designs'].get(parent_design_id)
    current_family = _family(task, family)
    previous = archive['designs'].get(design_id)
    if previous and previous['metrics'] != metrics:
        raise ValueError('conflicting certified metrics for identical physical input')
    matching_simulations = [e for e in archive['evaluations'] if e['simulation_key'] == result['simulation_key']]
    if any(e['design_id'] != design_id for e in matching_simulations):
        raise ValueError('simulation identity was already bound to a different physical input')
    seen_simulation = bool(matching_simulations)
    new_evidence = not result['cache_hit'] and not seen_simulation and previous is None
    diff = _layer_diff(parent['physical_input'] if parent else None, task)
    effective = bool(new_evidence and role != 'baseline_reference' and parent and
                     parent_design_id != design_id and diff['changed_layers'])
    seen_family = any(e['family']['family_key'] == current_family['family_key'] for e in archive['evaluations'])
    family_state = ('new' if not seen_family else 'existing')
    parent_family_changed = bool(parent and parent['family']['family_key'] != current_family['family_key'])
    event = {'design_id': design_id, 'parent_design_id': parent_design_id,
             'instance_id': result.get('instance_id'), 'route_id': result.get('route_id'),
             'simulation_key': result['simulation_key'], 'evaluation_key': result.get('evaluation_key'),
             'full_result_ref': deepcopy(result['full_result_ref']), 'role': role,
             'cache_hit': result['cache_hit'], 'input_binding': binding, 'new_design': previous is None,
             'new_evidence': new_evidence, 'effective_intervention': effective,
             'layer_diff': diff, 'family': current_family, 'family_state': family_state,
             'family_changed_from_parent': parent_family_changed,
             'parameterization_changed_from_parent': bool(parent and parent['family']['specification'] != current_family['specification'])}
    out = deepcopy(archive)
    if previous is None:
        out['designs'][design_id] = {'design_id': design_id, 'context_id': archive['context_id'],
            'physical_input': task, 'metrics': metrics, 'parent_design_id': parent_design_id,
            'layer_diff': deepcopy(diff), 'family': deepcopy(current_family),
            'first_evaluation': deepcopy(event), 'certified_result': deepcopy(result)}
    out['evaluations'].append(event)
    out['latest_event'] = deepcopy(event)
    out['new_evidence_count'] += int(new_evidence)
    out['effective_intervention_count'] += int(effective)
    if previous is None:
        # An existing nondominated frontier is sufficient: historical dominated
        # points cannot dominate a new point without a frontier point doing so.
        frontier = {key: out['designs'][key] for key in archive['pareto_design_ids']}
        frontier[design_id] = out['designs'][design_id]
        out['pareto_design_ids'] = pareto_design_ids({'designs': frontier, 'objectives': goals})
    _hash(out)
    return out
