"""Deterministic, sampled-grid observations; no mechanism inference or probes.

Consumes research_executor result items. Complete spectra remain in the immutable
full-result references; the brief view retains every goal and names its omissions.
"""
from copy import deepcopy
import math


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _channel(result, region):
    raw = result.get('raw_result', {})
    grid = raw.get('wavelengths_nm', [])
    if (len(grid) < 3 or any(not _finite(v) or v <= 0 for v in grid)
            or any(a >= b for a, b in zip(grid, grid[1:]))):
        raise ValueError('invalid wavelength grid')
    target_angle = region['angle_deg']
    pol = region['polarization']
    found = []
    for key, channel in raw.get('channels', {}).items():
        try:
            parts = dict(part.split('=', 1) for part in key.split('|'))
            matches = float(parts['angle']) == target_angle and parts['pol'] == pol
        except (KeyError, ValueError):
            matches = False
        if matches:
            found.append(channel)
    if len(found) != 1:
        raise ValueError('missing or ambiguous condition channel')
    return grid, found[0]


def _metric_spec(goal):
    metric = goal['metric']
    aliases = {'reflectance': 'R', 'transmittance': 'T', 'absorptance': 'A', 'absorption': 'A'}
    parts = metric.split('_')
    if len(parts) != 2:
        raise ValueError('unsupported metric')
    if parts[0] in ('mean', 'min', 'max'):
        stat, quantity = parts[0], aliases.get(parts[1], parts[1])
    else:
        quantity, stat = parts
    if quantity not in ('R', 'T', 'A') or stat not in ('mean', 'min', 'max'):
        raise ValueError('unsupported metric')
    return quantity, stat


def _values(channel, quantity, size):
    values = channel.get(quantity, [])
    if len(values) != size or any(not _finite(v) for v in values):
        raise ValueError('missing or nonfinite spectral values')
    return values


def _summary(grid, values):
    lo, hi = min(values), max(values)
    return {'sample_count': len(grid), 'wavelength_range_nm': [grid[0], grid[-1]],
            'min': {'value': lo, 'wavelengths_nm': [x for x,y in zip(grid,values) if y == lo]},
            'max': {'value': hi, 'wavelengths_nm': [x for x,y in zip(grid,values) if y == hi]}}


def _metric(grid, values, goal, stat):
    lo, hi = goal['region']['wavelength_nm']
    indices = [i for i, x in enumerate(grid) if lo <= x <= hi]
    xs, ys = [grid[i] for i in indices], [values[i] for i in indices]
    if len(xs) < 3 or xs[0] != lo or xs[-1] != hi:
        raise ValueError('band requires exact endpoints and interior samples')
    if stat == 'mean':
        reduction = goal.get('mean_reduction')
        if reduction == 'wavelength_integral_mean':
            value = sum((b-a)*(y+z)/2 for a,b,y,z in zip(xs,xs[1:],ys,ys[1:]))/(hi-lo)
        elif reduction == 'sample_mean':
            value = sum(ys)/len(ys)
        else:
            raise ValueError('unsupported or missing mean reduction')
    else:
        value = min(ys) if stat == 'min' else max(ys)
    return value, _summary(xs, ys)


def _parameter_diff(baseline, candidate):
    before, after = baseline.get('parameters'), candidate.get('parameters')
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {'status': 'unknown', 'reason': 'missing actual parameters'}
    diffs = []
    for key in sorted(set(before) | set(after)):
        a, b = before.get(key), after.get(key)
        if a == b:
            continue
        row = {'parameter': key, 'baseline': deepcopy(a), 'candidate': deepcopy(b)}
        if key == 'thicknesses_nm' and isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
            row['changed_layers'] = [{'layer_index': i, 'baseline_nm': x, 'candidate_nm': y,
                'delta_nm': y-x} for i,(x,y) in enumerate(zip(a,b)) if x != y]
        diffs.append(row)
    return {'status': 'observed', 'differences': diffs}


def build_physical_observations(baseline, candidate, goals=None, *, change_tolerance=1e-12):
    """Compare all inherited objectives without interpolating or hiding failures.

    Tolerance is a numerical change threshold, not a physical confidence interval.
    A supplied by closure is descriptive only, never independent conservation proof.
    """
    if not _finite(change_tolerance) or change_tolerance < 0:
        raise ValueError('invalid change tolerance')
    goals = deepcopy(candidate.get('objectives', []) if goals is None else goals)
    if not goals:
        raise ValueError('explicit objectives required')
    goals = [g.get('executable_metric', g) for g in goals]
    ids = [g['objective_id'] for g in goals]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate objective ids')
    out = {'schema_version': 'focused_research.physical_observations.v1',
        'route_id': candidate.get('route_id'),
        'baseline_instance_id': baseline.get('instance_id'), 'candidate_instance_id': candidate.get('instance_id'),
        'claim_scope': 'sampled_grid_only', 'units': {'wavelength': 'nm', 'optical_quantity': 'dimensionless_fraction', 'angle': 'deg'},
        'parameter_diff': _parameter_diff(baseline,candidate), 'goals': [],
        'full_result_refs': {'baseline': deepcopy(baseline.get('full_result_ref')), 'candidate': deepcopy(candidate.get('full_result_ref'))},
        'limits': ['No continuous-band guarantee.', 'No unique physical mechanism inferred.',
            'Absorptance from R/T closure is not independent energy validation.',
            'No gradient calculation or extra forward probe performed.']}
    for goal in goals:
        row = {'observation_id': 'goal:' + goal['objective_id'], 'goal_id': goal['objective_id'], 'metric': deepcopy(goal), 'status': 'unknown'}
        out['goals'].append(row)
        try:
            if baseline.get('route_id') != candidate.get('route_id'):
                raise ValueError('comparison route identity mismatch')
            if baseline.get('physics_accepted') is not True or candidate.get('physics_accepted') is not True:
                raise ValueError('both results require accepted physics certificates')
            quantity, stat = _metric_spec(goal)
            gx, bc = _channel(baseline,goal['region'])
            cx, cc = _channel(candidate,goal['region'])
            if gx != cx:
                raise ValueError('different sample grids; no implicit interpolation')
            by, cy = _values(bc,quantity,len(gx)), _values(cc,quantity,len(cx))
            b, bs = _metric(gx,by,goal,stat)
            c, cs = _metric(cx,cy,goal,stat)
            delta = [y-x for x,y in zip(by,cy)]
            changed = [i for i,d in enumerate(delta) if abs(d)>change_tolerance]
            runs = []
            for i in changed:
                if runs and runs[-1][-1]+1 == i:
                    runs[-1].append(i)
                else:
                    runs.append([i])
            regions = [{'sample_index_range': [r[0],r[-1]], 'wavelength_range_nm': [gx[r[0]],gx[r[-1]]],
                        'sample_count': len(r), 'min_delta': min(delta[i] for i in r),
                        'max_delta': max(delta[i] for i in r)} for r in runs]
            sense = goal.get('sense')
            if sense not in ('maximize', 'minimize'):
                raise ValueError('unsupported objective sense')
            row.update(status='observed', baseline=b, candidate=c, delta=c-b,
                improvement=(c-b)*(1 if sense=='maximize' else -1),
                band_samples={'baseline':bs, 'candidate':cs},
                spectral_change={'quantity':quantity, 'coverage':_summary(gx,delta),
                    'change_tolerance':change_tolerance, 'changed_sample_count':len(changed),
                    'changed_regions':regions, 'all_sample_count':len(gx)},
                expand_handles={'baseline':{'result':'baseline','channel':goal['region'],'quantity':quantity},
                    'candidate':{'result':'candidate','channel':goal['region'],'quantity':quantity}})
        except (KeyError, TypeError, ValueError) as error:
            row['reason'] = str(error)
    brief_rows=[]
    for row in out['goals']:
        brief={k:deepcopy(row[k]) for k in ('observation_id','goal_id','status','baseline','candidate','delta','improvement','reason') if k in row}
        brief['conditions']=deepcopy(row['metric']['region'])
        brief['metric']=row['metric']['metric']
        brief['units']='dimensionless_fraction'
        if row['status']=='observed':
            brief['band_extrema']={}
            for role, band in row['band_samples'].items():
                brief['band_extrema'][role]={kind:{'value':band[kind]['value'],
                    'first_wavelength_nm':band[kind]['wavelengths_nm'][0],
                    'tied_sample_count':len(band[kind]['wavelengths_nm'])} for kind in ('min','max')}
            change=row['spectral_change']
            brief['sample_grid_coverage']={k:deepcopy(change['coverage'][k]) for k in ('sample_count','wavelength_range_nm')}
            brief['changed_sample_count']=change['changed_sample_count']
            regions=change['changed_regions']
            brief['largest_change_region']=deepcopy(max(regions,key=lambda r:max(abs(r['min_delta']),abs(r['max_delta'])))) if regions else None
            brief['other_changed_region_count']=max(0,len(regions)-1)
            brief['expand_handles']=deepcopy(row['expand_handles'])
        brief_rows.append(brief)
    out['brief_view'] = {'goals':brief_rows, 'all_goals_retained':True,
        'omitted_from_brief':['full spectra','sample-level differences','additional tied extrema locations','other changed regions (counts shown)'],
        'expand':'goals and full_result_refs', 'claim_scope':'sampled_grid_only'}
    return out


def compare_predictions(predictions, observations, *, tolerance=1e-12):
    """Test frozen directional claims only; unknown is not supporting evidence."""
    if not _finite(tolerance) or tolerance < 0:
        raise ValueError('invalid tolerance')
    rows = {row['goal_id']:row for row in observations['goals']}
    output=[]
    for prediction in predictions:
        gid = prediction.get('goal_id')
        obs = rows.get(gid,{})
        direction=prediction.get('direction')
        status='unknown'
        identity_matches = all(key not in prediction or prediction[key] == observations.get(key)
            for key in ('route_id','baseline_instance_id','candidate_instance_id'))
        if 'route_id' in prediction:
            identity_matches = identity_matches and all(isinstance(prediction.get(key),str) and prediction[key]
                for key in ('route_id','baseline_instance_id','candidate_instance_id'))
        metric_matches = 'metric' not in prediction or prediction['metric'] == obs.get('metric')
        if identity_matches and metric_matches and obs.get('status')=='observed' and direction in ('increase','decrease','unchanged'):
            d=obs['delta']
            matches={'increase':d>tolerance,'decrease':d < -tolerance,'unchanged':abs(d)<=tolerance}[direction]
            status='supports' if matches else 'challenged'
        output.append({'goal_id':gid, 'status':status, 'prediction':deepcopy(prediction),
            'observed_delta':obs.get('delta'), 'scope':'direction_on_sampled_grid',
            'mechanism_status':'unknown', 'numerical_performance':{'improvement':obs.get('improvement')}})
    return output


def costed_probe_plan(*, kind, fractional_change, layer_indices, points_per_evaluation):
    """Describe two explicit +/- probes; this function never executes a probe."""
    if kind not in ('whole_thickness','terminal_group') or not _finite(fractional_change) or not 0 < fractional_change < 1:
        raise ValueError('invalid explicit probe')
    if not layer_indices or any(type(i) is not int or i < 0 for i in layer_indices) or len(set(layer_indices))!=len(layer_indices):
        raise ValueError('explicit unique layer indices required')
    if type(points_per_evaluation) is not int or points_per_evaluation <= 0:
        raise ValueError('positive point count required')
    return {'kind':kind,'status':'proposed_not_executed','requires_explicit_execution':True,
        'layer_indices':list(layer_indices),'relative_changes':[-fractional_change,fractional_change],
        'cost':{'forward_evaluations':2,'spectral_points':2*points_per_evaluation},
        'scope':'local finite perturbation; not a unique mechanism proof'}
