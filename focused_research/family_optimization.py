"""Budgeted numerical search within an explicit, immutable material family.

Search scalarizations explore tradeoffs; they are not inferred user preferences.
Every solver design row, including finite-difference probes and failed attempts,
is charged. Returned designs are proposals and require independent certification.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
import time
import numpy as np


class _BudgetExhausted(Exception):
    pass


def _modules():
    try:
        from .research_execution import _engine
        from .physical_observations import _metric_spec, _metric
    except ImportError:
        from research_execution import _engine
        from physical_observations import _metric_spec, _metric
    _engine()
    from tmm_engine.task_io import simulation_task_from_dict
    from tmm_engine.material_registry import MaterialRegistry
    from tmm_engine.workbench import TMMWorkbench
    return simulation_task_from_dict, MaterialRegistry, TMMWorkbench, _metric_spec, _metric


def _identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _nondominated(rows, signs):
    valid = [r for r in rows if r['status'] == 'evaluated']
    unique = {tuple(r['thicknesses_nm']): r for r in valid}
    valid = list(unique.values())
    if not valid:
        return []
    values = np.asarray([r['metrics'] for r in valid]) * signs
    return [r for i, r in enumerate(valid) if not any(
        np.all(values[j] >= values[i]) and np.any(values[j] > values[i])
        for j in range(len(valid)) if j != i)]



def summarize_local_derivatives(row, variable_names, bounds_nm, goals, *, top_k=3,
                                original_family_bounds_nm=None, bounds_role='original_family_bounds'):
    """Summarize already-computed Jacobian; never dispatch a solver or probe.

    Sensitivity order uses normalized-coordinate magnitude (the entire declared
    variable range). Sign conflicts use improvement-oriented derivatives, so a
    minimizing goal is correctly reversed. A shared-variable derivative is the
    aggregate for its tied layers and cannot diagnose individual layer effects.
    """
    output = {'status': 'unknown', 'additional_forward_design_rows': 0,
        'bounds_role': bounds_role,
        'original_family_bounds_nm': deepcopy(original_family_bounds_nm) if original_family_bounds_nm is not None else dict(zip(variable_names, np.asarray(bounds_nm).tolist())),
        'at_bound_scope': 'effective search window only; not a new user physical constraint',
        'evaluation_index': row.get('evaluation_index'),
        'limits': ['Local sampled-grid derivatives only; not independently certified.',
                   'No finite-step improvement, mechanism, or global optimum guarantee.',
                   'Shared-variable derivatives aggregate tied layers; individual layer effects are unknown.']}
    raw = row.get('metric_jacobian_wrt_normalized_parameters')
    if raw is None:
        output['reason'] = 'analytic_jacobian_unavailable; no extra finite-difference probes performed'
        return output
    bounds = np.asarray(bounds_nm, dtype=float).reshape(-1, 2)
    jac = np.asarray(raw, dtype=float)
    if jac.shape != (len(goals), len(variable_names)) or not np.isfinite(jac).all():
        output['reason'] = 'invalid_or_nonfinite_saved_jacobian'
        return output
    if bounds.shape != (len(variable_names), 2) or not np.isfinite(bounds).all():
        output['reason'] = 'invalid_saved_variable_bounds'
        return output
    spans = bounds[:, 1]-bounds[:, 0]
    variable_rows = []
    for j, name in enumerate(variable_names):
        value = float(row['parameters_nm'][name])
        lower, upper = bounds[j].tolist()
        tolerance = max(1e-10, abs(upper-lower)*1e-8)
        at_lower, at_upper = abs(value-lower) <= tolerance, abs(value-upper) <= tolerance
        variable_rows.append({'name': name, 'current_value_nm': value, 'bounds_nm': [lower, upper],
            'at_bound': 'both_fixed' if at_lower and at_upper else 'lower' if at_lower else 'upper' if at_upper else None})
    sensitivities, selected = [], set()
    for i, goal in enumerate(goals):
        ranked = sorted((j for j in range(len(variable_names)) if spans[j] > 0),
                        key=lambda j: (-abs(jac[i, j]), j))[:top_k]
        selected.update(ranked)
        sensitivities.append({'objective_id': goal['objective_id'], 'sense': goal['sense'],
            'top_variables': [{**variable_rows[j], 'derivative_per_nm': float(jac[i, j]/spans[j]),
                               'derivative_per_normalized_variable': float(jac[i, j])} for j in ranked]})
    # Top-k chooses coordinates, not which known goals may be shown for them.
    # Preserve each selected coordinate's complete saved vector without adding
    # parameters, evaluating the solver, or changing the per-goal rankings.
    selected_derivatives = [{**variable_rows[j],
        'derivative_per_nm': {g['objective_id']: float(jac[i, j]/spans[j])
                              for i, g in enumerate(goals)}} for j in sorted(selected)]
    signs = np.asarray([1. if g['sense'] == 'maximize' else -1. for g in goals])
    improvement_jac = jac * signs[:, None]
    zero_tolerance = 1e-10
    conflicts = []
    for j in range(len(variable_names)):
        if spans[j] <= 0:
            continue
        positive = [g['objective_id'] for i, g in enumerate(goals) if improvement_jac[i, j] > zero_tolerance]
        negative = [g['objective_id'] for i, g in enumerate(goals) if improvement_jac[i, j] < -zero_tolerance]
        if positive and negative:
            conflicts.append({**variable_rows[j], 'increasing_variable_improves_goals': positive,
                'increasing_variable_worsens_goals': negative})
    output.update(status='local_derivative_uncertified', sensitivity_ranking='absolute_normalized_coordinate_derivative',
        goal_sensitivities=sensitivities,
        selected_variable_derivatives=selected_derivatives,
        goal_gradient_conflicts={'status': 'observed' if conflicts else 'none_above_threshold',
            'improvement_orientation': 'maximize:+derivative; minimize:-derivative',
            'normalized_zero_tolerance': zero_tolerance, 'conflicting_variable_count': len(conflicts),
            'variable_count': len(variable_names), 'variables': conflicts,
            'interpretation': 'Opposite local goal directions for the same variable; no claim that a joint multivariable improving step is impossible.'})
    return output


def optimize_family(route, prepared_envelopes, *, max_forward_evaluations,
                    seed=42, search_weights=None, parameterization='family',
                    backend='auto', candidate_limit=None, search_bounds_nm=None,
                    initialization_policy='space_filling'):
    """Return uncertified prepared candidates, full search records, and metering.

    ``family`` preserves repeated variable ties and literal fixed thicknesses.
    ``layerwise_refinement`` explicitly creates independent variables for each
    originally variable layer, using that variable's original bounds. Literal
    fixed layers remain fixed. This is a declared new parameterization branch.
    ``search_bounds_nm`` optionally narrows named variables to subsets of the
    immutable family bounds. These are search-policy bounds, not new physical
    constraints. Every provided seed must lie in the effective search bounds.
    ``provided_only`` disables the global Latin-hypercube screen: local starts
    come only from provided seeds or their numerical continuation. Continuation
    can use the entire original family range unless explicitly narrowed.
    No optimizer/search dependency is installed automatically.
    """
    if type(max_forward_evaluations) is not int or max_forward_evaluations < 0:
        raise ValueError('max_forward_evaluations must be a nonnegative integer')
    if parameterization not in ('family', 'layerwise_refinement'):
        raise ValueError('unsupported parameterization')
    if initialization_policy not in ('space_filling', 'provided_only'):
        raise ValueError('unsupported initialization_policy')
    if backend not in ('auto', 'torch', 'numpy'):
        raise ValueError('unsupported numerical backend')
    if candidate_limit is not None and (type(candidate_limit) is not int or candidate_limit < 1):
        raise ValueError('positive candidate_limit required')
    parse, Registry, Workbench, metric_spec, metric_numpy = _modules()
    envelopes = [deepcopy(e) for e in prepared_envelopes if e.get('route_id') == route['id']]
    if not envelopes:
        raise ValueError('prepared route envelopes required')
    template = envelopes[0]
    task = parse(template['tmm_task'])
    task.validate()
    family = route['family']
    if len(family['layers']) != len(task.stack.layers):
        raise ValueError('family and prepared layer counts differ')
    goals = template['objectives']
    specs = [metric_spec(g) for g in goals]
    if not goals or len({g['objective_id'] for g in goals}) != len(goals):
        raise ValueError('unique inherited objectives required')
    for g in goals:
        if g.get('sense') not in ('maximize', 'minimize'):
            raise ValueError('unsupported objective sense')
        if metric_spec(g)[1] == 'mean' and g.get('mean_reduction') not in ('sample_mean', 'wavelength_integral_mean'):
            raise ValueError('explicit mean reduction required')
    signs = np.asarray([1. if g['sense'] == 'maximize' else -1. for g in goals])
    names, bounds, indices = [], [], []
    for i, layer in enumerate(family['layers']):
        variable = layer['thickness_nm']
        if isinstance(variable, str):
            name = variable if parameterization == 'family' else 'layer_%d:%s' % (i, variable)
            if name not in names:
                names.append(name)
                bounds.append(family['thickness_variables_nm'][variable])
            indices.append(names.index(name))
        else:
            indices.append(None)
            if float(variable) != task.stack.layers[i].thickness_nm:
                raise ValueError('prepared fixed thickness differs from family')
    bounds = np.asarray(bounds, dtype=float).reshape(-1, 2)
    if not np.isfinite(bounds).all() or np.any(bounds <= 0) or np.any(bounds[:, 0] > bounds[:, 1]):
        raise ValueError('invalid family bounds')
    original_bounds = bounds.copy()
    if search_bounds_nm is not None:
        if not isinstance(search_bounds_nm, dict) or set(search_bounds_nm) - set(names):
            raise ValueError('search_bounds_nm must map existing variable names to bounds')
        for name, narrowed in search_bounds_nm.items():
            if (not isinstance(narrowed, (list, tuple)) or len(narrowed) != 2 or
                    any(type(v) not in (int, float) or not math.isfinite(v) for v in narrowed)):
                raise ValueError('search bounds must be finite numeric pairs')
            j = names.index(name)
            lower, upper = narrowed
            if lower > upper or lower < original_bounds[j, 0] or upper > original_bounds[j, 1]:
                raise ValueError('search bounds must be an ordered subset of original family bounds')
            bounds[j] = narrowed
    search_policy = {'initialization_policy': initialization_policy,
        'provided_seed_instance_ids': [e['instance_id'] for e in envelopes],
        'search_bounds_nm': dict(zip(names, bounds.tolist())),
        'original_family_bounds_nm': dict(zip(names, original_bounds.tolist())),
        'requested_search_bounds_nm': deepcopy(search_bounds_nm),
        'bounds_role': 'numerical_search_policy_not_new_user_physical_constraints',
        'continuation_scope': 'all effective search bounds; coarse hint sectors do not constrain numerical continuation',
        'global_space_filling_initialization_enabled': initialization_policy == 'space_filling'}
    lo, span = bounds[:, 0], bounds[:, 1] - bounds[:, 0]
    seeds = []
    for envelope in envelopes:
        candidate = parse(envelope['tmm_task'])
        if envelope['objectives'] != goals:
            raise ValueError('all family seeds must inherit identical objectives')
        # Only thickness and display name may vary between seeds.
        a, b = deepcopy(template['tmm_task']), deepcopy(envelope['tmm_task'])
        for t in (a, b):
            t['stack'].pop('name', None)
            for layer in t['stack']['layers']:
                layer.pop('thickness_nm', None)
        if a != b:
            raise ValueError('seed material/conditions mismatch')
        values = np.full(len(names), np.nan)
        for i, index in enumerate(indices):
            value = candidate.stack.layers[i].thickness_nm
            if index is None:
                if value != task.stack.layers[i].thickness_nm:
                    raise ValueError('fixed layer changed in seed')
            elif np.isnan(values[index]):
                values[index] = value
            elif values[index] != value:
                raise ValueError('seed violates shared variable binding')
        if not np.isfinite(values).all():
            raise ValueError('seed parameters must be finite')
        if np.any(values < original_bounds[:, 0]) or np.any(values > original_bounds[:, 1]):
            raise ValueError('seed outside declared family bounds')
        if np.any(values < lo) or np.any(values > bounds[:, 1]):
            raise ValueError('seed outside effective search bounds')
        seeds.append(np.divide(values-lo, span, out=np.zeros_like(values), where=span != 0))
    weights = search_weights
    if weights is None:
        weights = [np.ones(len(goals)).tolist()]
        if len(goals) > 1:
            weights += [[4. if i == j else 1. for i in range(len(goals))] for j in range(len(goals))]
    weights = np.asarray(weights, dtype=float)
    if weights.ndim != 2 or weights.shape[1] != len(goals) or not len(weights) or not np.isfinite(weights).all() or np.any(weights < 0) or np.any(weights.sum(axis=1) <= 0):
        raise ValueError('invalid search scalarizations')
    weights = weights / weights.sum(axis=1, keepdims=True)
    registry = Registry()
    workbench = Workbench(registry)
    torch_engine = None
    if backend != 'numpy':
        try:
            from tmm_engine.optimization import DifferentiableThicknessOptimizer
            torch_engine = DifferentiableThicknessOptimizer(registry)
        except ImportError:
            if backend == 'torch':
                raise
    selected_backend = 'torch' if torch_engine else 'numpy'
    grid = task.spectrum.wavelengths_nm()
    # Validate sampled metric coverage before spending numerical budget.
    for g, (_, stat) in zip(goals, specs):
        metric_numpy(grid.tolist(), np.zeros(len(grid)).tolist(), g, stat)
    nk = None
    provenance = []
    if torch_engine:
        if task.stack.has_incoherent_layers:
            raise ValueError('torch search only supports coherent layers')
        nk, provenance = torch_engine._build_nk_stack(task)
    records, cache, stops = [], {}, []
    parent_evaluations = {}
    meter = {'forward_design_rows': 0, 'solver_channel_rows': 0,
             'cache_hits': 0, 'failed_forward_design_rows': 0, 'backward_passes': 0}
    started = time.perf_counter()
    channel_count = len(task.illumination.angles_deg) * sum(2 if p == 'unpolarized' else 1 for p in task.illumination.polarizations)
    group_keys = list(dict.fromkeys((g['region']['angle_deg'], g['region']['polarization']) for g in goals))
    torch_channel_count = sum(2 if p == 'unpolarized' else 1 for _, p in group_keys)
    stage_limit = max_forward_evaluations

    def evaluate(z, label):
        nonlocal provenance
        z = np.clip(np.asarray(z, dtype=float), 0., 1.)
        values = lo + span * z
        thicknesses = [float(task.stack.layers[i].thickness_nm if index is None else values[index]) for i, index in enumerate(indices)]
        key = tuple(thicknesses)
        if key in cache:
            meter['cache_hits'] += 1
            return cache[key]
        if meter['forward_design_rows'] >= min(stage_limit, max_forward_evaluations):
            raise _BudgetExhausted
        meter['forward_design_rows'] += 1
        row = {'evaluation_index': meter['forward_design_rows'], 'source': label,
               'parameters_nm': dict(zip(names, values.tolist())), 'normalized_parameters': z.tolist(),
               'thicknesses_nm': thicknesses, 'status': 'failed', 'metrics': None,
               'parent_evaluation_index': parent_evaluations.get(label)}
        records.append(row)
        try:
            if torch_engine:
                torch = torch_engine.torch
                tz = torch.tensor(z, dtype=torch.float64, requires_grad=True)
                tv = torch.tensor(lo) + torch.tensor(span) * tz
                td = torch.stack([torch.tensor(task.stack.layers[i].thickness_nm, dtype=torch.float64) if index is None else tv[index] for i, index in enumerate(indices)])
                channels = {}
                for angle, pol in group_keys:
                    meter['solver_channel_rows'] += 2 if pol == 'unpolarized' else 1
                    solver = torch_engine._solver_class(polarization=pol, dtype_real=torch.float64, dtype_complex=torch.complex128)
                    channels[(angle, pol)] = solver(td.reshape(1, -1)*1e-3, nk.unsqueeze(0), torch.tensor(grid*1e-3), theta_rad=math.radians(angle))
                metrics = []
                for g, (quantity, stat) in zip(goals, specs):
                    region = g['region']; mask = (grid >= region['wavelength_nm'][0]) & (grid <= region['wavelength_nm'][1])
                    ys = getattr(channels[(region['angle_deg'], region['polarization'])], quantity)[0, mask]
                    if stat == 'mean':
                        xs = torch.tensor(grid[mask], dtype=torch.float64)
                        m = torch.trapezoid(ys, xs)/(xs[-1]-xs[0]) if g['mean_reduction'] == 'wavelength_integral_mean' else ys.mean()
                    else:
                        m = ys.min() if stat == 'min' else ys.max()
                    metrics.append(m)
                gradients = []
                for metric in metrics:
                    if metric.requires_grad:
                        meter['backward_passes'] += 1
                        gradients.append(torch.autograd.grad(metric, tz, retain_graph=True)[0].detach().numpy())
                    else:
                        gradients.append(np.zeros_like(z))
                jac = np.asarray(gradients)
                observed = np.asarray([m.detach().item() for m in metrics])
            else:
                meter['solver_channel_rows'] += channel_count
                simulation = replace(task, stack=replace(task.stack, layers=tuple(replace(layer, thickness_nm=d) for layer, d in zip(task.stack.layers, thicknesses))))
                forward = workbench.simulate(simulation)
                provenance = forward.material_provenance
                observed = np.asarray([metric_numpy(grid.tolist(), forward.channel(g['region']['angle_deg'], g['region']['polarization'])[q].tolist(), g, stat)[0] for g, (q, stat) in zip(goals, specs)])
                jac = None
            if not np.isfinite(observed).all() or (jac is not None and not np.isfinite(jac).all()):
                raise ValueError('nonfinite physics or gradient')
            row.update(status='evaluated', metrics=observed.tolist())
            if jac is not None:
                row['metric_jacobian_wrt_normalized_parameters'] = jac.tolist()
            cache[key] = (observed, jac, row)
            return cache[key]
        except Exception as exc:
            meter['failed_forward_design_rows'] += 1
            row['error'] = str(exc)
            raise

    def objective(z, weight, label):
        observed, jac, _ = evaluate(z, label)
        value = float(-np.dot(weight * signs, observed))
        return (value, -np.dot(weight * signs, jac)) if jac is not None else value

    try:
        for i, z in enumerate(seeds):
            try:
                _, _, seed_row = evaluate(z, 'original_seed')
                seed_row.setdefault('source_instance_ids', []).append(envelopes[i]['instance_id'])
            except _BudgetExhausted:
                raise
            except Exception as exc:
                stops.append({'status': 'seed_failed', 'instance_id': envelopes[i]['instance_id'], 'error': str(exc)})
        if names and np.any(span > 0) and meter['forward_design_rows'] < max_forward_evaluations:
            from scipy.optimize import minimize
            # The opt-in provided-only branch never constructs a global screen.
            screen = []
            if initialization_policy == 'space_filling':
                from scipy.stats import qmc
                count = min(max(4, 2*len(names)), max(0, (max_forward_evaluations-meter['forward_design_rows'])//8))
                screen = qmc.LatinHypercube(d=len(names), seed=seed).random(count)
            for z in screen:
                try:
                    evaluate(z, 'space_filling_start')
                except _BudgetExhausted:
                    raise
                except Exception:
                    continue
            for wi, weight in enumerate(weights):
                valid = [r for r in records if r['status'] == 'evaluated']
                if not valid:
                    break
                available = max_forward_evaluations-meter['forward_design_rows']
                stage_limit = meter['forward_design_rows'] + max(0, available//(len(weights)-wi))
                ranked = sorted(valid, key=lambda r: -np.dot(weight*signs, r['metrics']))
                # Two independent starts per direction, budget split explicitly.
                local_starts = ranked[:min(2, len(ranked))]
                direction_end = stage_limit
                for si, row in enumerate(local_starts):
                    stage_limit = meter['forward_design_rows'] + max(0, (direction_end-meter['forward_design_rows'])//(len(local_starts)-si))
                    if stage_limit <= meter['forward_design_rows']:
                        break
                    try:
                        label = 'local_weight_%d_start_%d' % (wi, si)
                        parent_evaluations[label] = row['evaluation_index']
                        result = minimize(objective, row['normalized_parameters'], args=(weight, 'local_weight_%d_start_%d' % (wi, si)), method='L-BFGS-B', jac=bool(torch_engine), bounds=[(0., 1.) if s else (0., 0.) for s in span], options={'maxiter': max_forward_evaluations, 'maxfun': max_forward_evaluations, 'ftol': 1e-12, 'gtol': 1e-7, 'maxls': 20})
                        stops.append({'weight_index': wi, 'start_index': si, 'status': str(result.message)})
                    except _BudgetExhausted:
                        stops.append({'weight_index': wi, 'start_index': si, 'status': 'allocated_budget_exhausted'})
                    except Exception as exc:
                        stops.append({'weight_index': wi, 'start_index': si, 'status': 'failed', 'error': str(exc)})
    except _BudgetExhausted:
        stops.append({'status': 'global_budget_exhausted'})
    except Exception as exc:
        stops.append({'status': 'failed', 'error': str(exc)})
    frontier = _nondominated(records, signs)
    # Preserve all evaluated/frontier rows in the record; certification shortlist
    # spans individual goal extrema then search directions without a user ranking.
    chosen = []
    for weight in list(np.eye(len(goals))) + list(weights):
        if frontier:
            best = max(frontier, key=lambda r: np.dot(weight*signs, r['metrics']))
            if best not in chosen:
                chosen.append(best)
    for row in frontier:
        if candidate_limit is not None and len(chosen) >= candidate_limit:
            break
        if row not in chosen:
            chosen.append(row)
    candidates = []
    for row in chosen[:candidate_limit]:
        envelope = deepcopy(template)
        cid = 'numeric_' + _identity({'route': route['id'], 'parameterization': parameterization, 'thicknesses': row['thicknesses_nm']})[:20]
        envelope['instance_id'] = cid
        envelope['source_instance_id'] = template['instance_id']
        ancestor = row
        while ancestor.get('parent_evaluation_index'):
            ancestor = records[ancestor['parent_evaluation_index']-1]
        source_ids = ancestor.get('source_instance_ids', [])
        envelope['parent_instance_id'] = source_ids[0] if len(source_ids) == 1 else None
        envelope['parent_reference_role'] = 'original_seed_ancestor' if envelope['parent_instance_id'] else 'no_unique_original_seed_ancestor'
        envelope['material_template_instance_id'] = template['instance_id']
        envelope['source_instance_id'] = envelope['parent_instance_id']
        envelope['search_seed_instance_ids'] = [e['instance_id'] for e in envelopes]
        envelope['tmm_task']['stack']['name'] = cid
        for layer, d in zip(envelope['tmm_task']['stack']['layers'], row['thicknesses_nm']):
            layer['thickness_nm'] = d
        envelope['numerical_search'] = {'parameterization': parameterization, 'evaluation_index': row['evaluation_index'], 'parent_evaluation_index': row['parent_evaluation_index'], 'uncertified_metrics': dict(zip([g['objective_id'] for g in goals], row['metrics'])), 'physics_accepted': False}
        envelope['numerical_search']['search_policy'] = deepcopy(search_policy)
        envelope['numerical_search']['local_derivative_summary'] = summarize_local_derivatives(
            row, names, bounds, goals,
            original_family_bounds_nm=search_policy['original_family_bounds_nm'],
            bounds_role='search_policy_bounds' if search_bounds_nm is not None else 'original_family_bounds')
        candidates.append(envelope)
    meter['wall_seconds'] = time.perf_counter()-started
    return {'schema_version': 'focused_research.family_optimization.v1', 'route_id': route['id'],
        'status': 'candidates_proposed' if candidates else 'no_candidate',
        'backend': selected_backend, 'parameterization': parameterization,
        'variable_names': names, 'bounds_nm': bounds.tolist(), 'layer_variable_indices': indices,
        'original_family_bounds_nm': original_bounds.tolist(), 'search_policy': search_policy,
        'search_weights': weights.tolist(), 'search_weights_are_user_preferences': False,
        'max_forward_evaluations': max_forward_evaluations, 'metering': meter,
        'objective_ids': [g['objective_id'] for g in goals], 'evaluations': records,
        'pareto_evaluation_indices': [r['evaluation_index'] for r in frontier],
        'candidates': candidates, 'optimizer_stops': stops, 'material_provenance': provenance,
        'limits': ['Search metrics are uncertified; certify candidates independently.',
                   'No continuous-band, manufacturing, mechanism, or global optimum guarantee.',
                   'All attempted search design rows charged; certification is additional work.',
                   'solver_channel_rows counts requested polarized channel attempts, not verifier internal solves.']}
