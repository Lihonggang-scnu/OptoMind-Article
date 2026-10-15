"""Compact, evidence-only feedback for the next physical search iteration.

No solver calls, inferred mechanisms, invented polarizations, or success decisions.
The accepted-physics flag is the executor's certificate gate, not an optimizer gate.
"""
from copy import deepcopy
import math

try:
    from .physical_observations import (build_physical_observations, _channel,
                                         _values, _metric_spec, _finite)
except ImportError:
    from physical_observations import (build_physical_observations, _channel,
                                       _values, _metric_spec, _finite)


def _unknown(reason):
    return {'status': 'unknown', 'reason': reason}


def _direction(delta, tolerance):
    return 'gain' if delta > tolerance else 'loss' if delta < -tolerance else 'unchanged'


def _subbands(grid, before, after, goal, tolerance):
    lo, hi = goal['region']['wavelength_nm']
    cuts = [lo, lo + (hi-lo)/3, lo + 2*(hi-lo)/3, hi]
    rows = []
    sign = 1 if goal['sense'] == 'maximize' else -1
    for i, name in enumerate(('blue', 'middle', 'red')):
        a, b = cuts[i:i+2]
        ix = [j for j, x in enumerate(grid) if a <= x and (x < b or i == 2 and x <= b)]
        row = {'subband': name, 'partition_nm': [a, b], 'right_endpoint_inclusive': i == 2,
               'reduction': 'sample_mean', 'sample_count': len(ix)}
        if ix:
            bv = sum(before[j] for j in ix)/len(ix)
            cv = sum(after[j] for j in ix)/len(ix)
            row.update(status='observed', sampled_range_nm=[grid[ix[0]], grid[ix[-1]]],
                       baseline=bv, candidate=cv, delta=cv-bv,
                       sense_adjusted_delta=(cv-bv)*sign,
                       direction=_direction((cv-bv)*sign, tolerance))
        else:
            row.update(_unknown('no samples in this wavelength third'))
        rows.append(row)
    return rows


def _polarization(baseline, candidate, goal, quantity):
    """Only expose s/p when both are actually present, never derive from average."""
    try:
        data = {}
        common = None
        lo, hi = goal['region']['wavelength_nm']
        for role, result in [('baseline', baseline), ('candidate', candidate)]:
            data[role] = {}
            for pol in ('s', 'p'):
                region = dict(goal['region'], polarization=pol)
                grid, channel = _channel(result, region)
                if common is not None and grid != common:
                    raise ValueError('s/p grids differ')
                common = grid
                vals = _values(channel, quantity, len(grid))
                ix = [i for i, x in enumerate(grid) if lo <= x <= hi]
                if not ix:
                    raise ValueError('no in-band polarization samples')
                data[role][pol + '_mean'] = sum(vals[i] for i in ix)/len(ix)
            data[role]['s_minus_p_mean'] = data[role]['s_mean'] - data[role]['p_mean']
        return {'status': 'observed', 'reduction': 'sample_mean', 'sample_count': len(ix),
                'sampled_range_nm': [common[ix[0]], common[ix[-1]]], **data,
                's_delta': data['candidate']['s_mean']-data['baseline']['s_mean'],
                'p_delta': data['candidate']['p_mean']-data['baseline']['p_mean'],
                's_minus_p_delta': data['candidate']['s_minus_p_mean']-data['baseline']['s_minus_p_mean']}
    except (KeyError, TypeError, ValueError) as error:
        return _unknown(str(error))


def _bands(grid, values, threshold):
    runs = []
    for i, y in enumerate(values):
        if y >= threshold:
            if runs and runs[-1][-1] == i-1:
                runs[-1].append(i)
            else:
                runs.append([i])
    return [{'sampled_extent_nm': [grid[r[0]], grid[r[-1]]], 'sample_count': len(r),
             'blue_crossing_bracket_nm': [grid[r[0]-1], grid[r[0]]] if r[0] else None,
             'red_crossing_bracket_nm': [grid[r[-1]], grid[r[-1]+1]] if r[-1] < len(grid)-1 else None,
             'grid_clipped': r[0] == 0 or r[-1] == len(grid)-1} for r in runs]


def _reflection(baseline, candidate, region, threshold):
    if threshold is None:
        return _unknown('no explicit reflection threshold supplied')
    try:
        bg, bc = _channel(baseline, region)
        cg, cc = _channel(candidate, region)
        if bg != cg:
            raise ValueError('reflection grids differ; no interpolation')
        bands = {role: _bands(bg, _values(channel, 'R', len(bg)), threshold)
                 for role, channel in [('baseline', bc), ('candidate', cc)]}
        out = {'status': 'observed', 'quantity': 'R', 'threshold': threshold,
               'predicate': 'R >= threshold', 'coverage_nm': [bg[0], bg[-1]],
               'edge_definition': 'sampled extent with adjacent-sample crossing brackets; no interpolated edge',
               **bands, 'edge_motion': _unknown('requires one non-clipped band in each result')}
        if all(len(bands[r]) == 1 and not bands[r][0]['grid_clipped'] for r in bands):
            a, b = bands['baseline'][0], bands['candidate'][0]
            deltas = [y-x for x, y in zip(a['sampled_extent_nm'], b['sampled_extent_nm'])]
            out['edge_motion'] = {'status': 'observed', 'blue_sample_edge_delta_nm': deltas[0],
                                  'red_sample_edge_delta_nm': deltas[1],
                                  'both_sample_edges_moved_red': all(d > 0 for d in deltas),
                                  'scope': 'sampled edges only; crossing brackets may overlap'}
        return out
    except (KeyError, TypeError, ValueError) as error:
        return _unknown(str(error))


def _scaling(baseline, candidate):
    a, b = baseline.get('parameters'), candidate.get('parameters')
    if not isinstance(a, dict) or not isinstance(b, dict):
        return _unknown('missing actual parameters')
    x, y = a.get('thicknesses_nm'), b.get('thicknesses_nm')
    if (not isinstance(x, list) or not isinstance(y, list) or not x or len(x) != len(y)
            or any(not _finite(v) or v <= 0 for v in x+y)):
        return _unknown('missing valid aligned layer thicknesses')
    if {k:v for k,v in a.items() if k != 'thicknesses_nm'} != {k:v for k,v in b.items() if k != 'thicknesses_nm'}:
        return _unknown('other parameters changed; not isolated whole-stack scaling')
    ratios = [v/u for u,v in zip(x,y)]
    uniform = all(math.isclose(r, ratios[0], rel_tol=1e-9, abs_tol=1e-12) for r in ratios)
    return {'status': 'observed', 'uniform_scaling': uniform,
            'factor': ratios[0] if uniform else None,
            'whole_stack_thickened': uniform and ratios[0] > 1 + 1e-12}


def build_physics_diagnostics(baseline, candidate, goals=None, *, reflection_threshold=None,
                              change_tolerance=1e-12):
    """Describe all objectives and negative directions, never prescribe a mechanism.

    Subbands and s/p use explicitly labeled sample means, even for min/max or
    integral objectives. Only the original objective metric evaluates performance.
    A reflection threshold is an explicit caller choice, never inferred from goals.
    """
    if reflection_threshold is not None and (not _finite(reflection_threshold) or not 0 <= reflection_threshold <= 1):
        raise ValueError('reflection threshold must be a finite fraction in [0, 1]')
    observed = build_physical_observations(baseline, candidate, goals, change_tolerance=change_tolerance)
    out = {'schema_version': 'focused_research.physics_diagnostics.v1',
           'route_id': candidate.get('route_id'), 'baseline_instance_id': baseline.get('instance_id'),
           'candidate_instance_id': candidate.get('instance_id'), 'claim_scope': 'sampled_grid_only',
           'full_result_refs': deepcopy(observed['full_result_refs']), 'all_goals_retained': True,
           'goals': [], 'whole_stack_scaling': _scaling(baseline, candidate),
           'next_iteration_evidence': {'negative_goal_ids': [], 'positive_goal_ids': [], 'unknown_goal_ids': []},
           'limits': ['No mechanism or continuous-band guarantee inferred.',
                      'Subband and polarization sample means are descriptive, not substitutes for objective metrics.',
                      'No objective weights or aggregate success inferred; prediction support is not optimization success.',
                      'No new forward solve, gradient, or paid probe executed.']}
    for source, brief in zip(observed['goals'], observed['brief_view']['goals']):
        row = deepcopy(brief)
        out['goals'].append(row)
        gid = row['goal_id']
        if row['status'] != 'observed':
            out['next_iteration_evidence']['unknown_goal_ids'].append(gid)
            continue
        direction = _direction(row['improvement'], change_tolerance)
        row['objective_direction'] = direction
        if direction != 'unchanged':
            out['next_iteration_evidence']['positive_goal_ids' if direction == 'gain' else 'negative_goal_ids'].append(gid)
        goal = source['metric']
        quantity, _ = _metric_spec(goal)
        grid, bc = _channel(baseline, goal['region'])
        _, cc = _channel(candidate, goal['region'])
        row['subbands'] = _subbands(grid, _values(bc, quantity, len(grid)),
                                    _values(cc, quantity, len(grid)), goal, change_tolerance)
        row['polarization'] = _polarization(baseline, candidate, goal, quantity)
        row['reflection_bands'] = _reflection(baseline, candidate, goal['region'], reflection_threshold)
        blue, _, red = row['subbands']
        row['red_gain_blue_loss'] = (red['direction'] == 'gain' and blue['direction'] == 'loss'
                                    if red['status'] == blue['status'] == 'observed' else None)
        row['whole_thickening_tradeoff'] = {
            'status': 'observed' if out['whole_stack_scaling'].get('whole_stack_thickened') else 'unknown',
            'red_gain_blue_loss': row['red_gain_blue_loss'] if out['whole_stack_scaling'].get('whole_stack_thickened') else None,
            'scope': 'measured subband changes along this actual step; no causal or next-step guarantee'}
    evidence = out['next_iteration_evidence']
    evidence['observed_objective_tradeoff'] = bool(evidence['positive_goal_ids'] and evidence['negative_goal_ids'])
    evidence['guidance'] = 'Retain every goal; inspect measured losses before repeating this parameter direction. New directions require new evaluation.'
    return out
