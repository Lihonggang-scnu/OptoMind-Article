"""Read-only decision views and arithmetic on saved, uncertified derivatives.

No solver, model call, new physical observation, or scientific acceptance gate.
"""
from copy import deepcopy
import json
import math


LIMITS = [
    'Saved local derivatives are not independently certified.',
    'Directional rates are first-order hypotheses, not finite-step outcomes.',
    'Shared-variable derivatives do not identify individual layer effects.',
    'Failure to find a common direction does not prove none exists or require untying.',
]


def _finite(x):
    return type(x) in (int, float) and math.isfinite(x)


def local_direction_facts(summary, objectives):
    """Keep saved goal vectors for top-k coordinates; never fill omissions with zero."""
    result = {'status': 'unknown', 'limits': LIMITS[:], 'variables': [],
              'same_direction_opportunities': [], 'joint_direction': {'status': 'unknown', 'reason': 'insufficient_saved_derivatives'},
              'additional_forward_design_rows': 0, 'warnings': []}
    if isinstance(summary, dict):
        result['direction_feasibility_scope'] = summary.get('bounds_role', 'recorded_bounds')
        if 'original_family_bounds_nm' in summary:
            result['original_family_bounds_nm'] = deepcopy(summary['original_family_bounds_nm'])
        if summary.get('bounds_role') == 'search_policy_bounds':
            result['limits'].append('Feasible directions and at-bound flags refer to the recorded search window, not a new user constraint; original family bounds are supplied separately.')
    if not isinstance(summary, dict) or summary.get('status') != 'local_derivative_uncertified':
        result['reason'] = 'no usable saved local derivative'
        return result
    goals = {g['objective_id']: g['sense'] for g in objectives}
    entries = summary.get('goal_sensitivities', [])
    if not isinstance(entries, list):
        result['reason'] = 'unrecognized saved derivative format'
        return result
    variables, invalid = {}, set()
    def add_coordinate(v, gid, *, saved_vector=False):
        name, bounds, value = v.get('name'), v.get('bounds_nm'), v.get('current_value_nm')
        d = v.get('derivative_per_nm')
        if (not isinstance(name, str) or not isinstance(bounds, (list, tuple)) or len(bounds) != 2
                or not all(_finite(x) for x in [*bounds, value, d])
                or not bounds[0] <= value <= bounds[1] or bounds[0] >= bounds[1]):
            result['warnings'].append('invalid derivative coordinate omitted')
            return
        row = variables.setdefault(name, {'name': name, 'current_value_nm': value,
            'bounds_nm': list(bounds), 'derivative_per_nm': {}, 'improvement_rate_normalized': {}})
        if row['bounds_nm'] != list(bounds) or row['current_value_nm'] != value:
            invalid.add(name)
            return
        if gid in row['derivative_per_nm']:
            if not saved_vector or row['derivative_per_nm'][gid] != d:
                invalid.add(name)
            return
        rate = d * (bounds[1]-bounds[0]) * (1 if goals[gid] == 'maximize' else -1)
        if not math.isfinite(rate):
            invalid.add(name)
            result['warnings'].append('nonfinite normalized derivative omitted: ' + name)
            return
        row['derivative_per_nm'][gid] = d
        row['improvement_rate_normalized'][gid] = rate

    for item in entries:
        gid = item.get('objective_id')
        if gid in goals:
            for v in item.get('top_variables', []):
                add_coordinate(v, gid)
    # New summaries retain all already-computed goal derivatives for the same
    # top-k union. Legacy summaries lack this field and remain honestly partial.
    saved_vectors = summary.get('selected_variable_derivatives', [])
    if not isinstance(saved_vectors, list):
        result['warnings'].append('unrecognized saved derivative vector format omitted')
        saved_vectors = []
    for v in saved_vectors:
        if not isinstance(v, dict) or not isinstance(v.get('derivative_per_nm'), dict):
            result['warnings'].append('unrecognized saved derivative vector format omitted')
            continue
        if v.get('name') not in variables:
            continue
        for gid, derivative in v['derivative_per_nm'].items():
            if gid in goals:
                add_coordinate({**v, 'derivative_per_nm': derivative}, gid, saved_vector=True)
    tol = 1e-10
    complete = []
    for name, row in sorted(variables.items()):
        if name in invalid:
            result['warnings'].append('inconsistent saved coordinate omitted: ' + name)
            continue
        lo, hi = row['bounds_nm']; x = row['current_value_nm']
        row['feasible_directions'] = (['decrease'] if x > lo else []) + (['increase'] if x < hi else [])
        row['missing_goal_derivatives'] = sorted(set(goals)-set(row['derivative_per_nm']))
        result['variables'].append(row)
        if row['missing_goal_derivatives']:
            continue
        complete.append(row)
        for direction, sign in [('decrease', -1), ('increase', 1)]:
            rates = {g: sign*d for g, d in row['improvement_rate_normalized'].items()}
            if direction in row['feasible_directions'] and all(v >= -tol for v in rates.values()) and any(v > tol for v in rates.values()):
                result['same_direction_opportunities'].append({'variable': name, 'direction': direction,
                    'improvement_rates_normalized': rates, 'scope': 'local_first_order_only'})
    result['status'] = 'local_derivative_uncertified'
    result['derivative_coverage'] = {'available_complete_coordinates': len(complete),
        'declared_variable_count': summary.get('goal_gradient_conflicts', {}).get('variable_count'),
        'omitted_coordinates': 'unknown, not zero; joint direction uses complete coordinates only'}
    # For two objectives, the minimum-norm point on the segment between their
    # normalized gradients is a useful candidate common direction. Verify actual
    # oriented dot products after enforcing the feasible tangent directions.
    if not complete:
        result['joint_direction'] = {'status': 'unknown', 'reason': 'insufficient_saved_derivatives',
            'detail': 'No coordinate has saved derivatives for every objective; no joint-direction assessment was performed.'}
        return result
    if len(goals) != 2:
        result['joint_direction'] = {'status': 'unsupported', 'reason': 'objective_count_not_supported',
            'detail': 'This joint-direction helper supports exactly two objectives; all original objectives are retained.'}
        return result
    gids = list(goals)
    raw = [[r['improvement_rate_normalized'][g] for r in complete] for g in gids]
    norms = [math.hypot(*row) for row in raw]
    if any(n <= tol or not math.isfinite(n) for n in norms):
        result['joint_direction'] = {'status': 'unknown', 'reason': 'zero_or_invalid_available_gradient',
            'detail': 'The normalized-gradient candidate method was not applicable; no impossibility conclusion follows.'}
        return result
    result['joint_direction'] = {'status': 'not_found'}
    a, b = [[v/n for v in row] for row, n in zip(raw, norms)]
    diff = [x-y for x,y in zip(a,b)]
    denom = sum(x*x for x in diff)
    alpha = max(0., min(1., -sum(x*y for x,y in zip(b,diff))/denom)) if denom > tol else .5
    direction = [alpha*x+(1-alpha)*y for x,y in zip(a,b)]
    for i, row in enumerate(complete):
        needed = 'increase' if direction[i] > 0 else 'decrease'
        if needed not in row['feasible_directions']:
            direction[i] = 0.
    scale = max(abs(v) for v in direction)
    if scale <= tol:
        result['joint_direction']['reason'] = 'candidate direction vanishes at bounds or gradient conflict'
        return result
    direction = [v/scale for v in direction]
    rates = {g: sum(v*d for v,d in zip(row,direction)) for g,row in zip(gids,raw)}
    if not all(_finite(v) and v > tol for v in rates.values()):
        if not all(_finite(v) for v in rates.values()):
            result['joint_direction'] = {'status': 'unknown', 'reason': 'nonfinite_directional_rates'}
        else:
            result['joint_direction']['reason'] = 'candidate did not improve every objective locally after bound projection'
        return result
    steps = []
    for row, d in zip(complete, direction):
        lo,hi = row['bounds_nm']; x = row['current_value_nm']; nm = d*(hi-lo)
        if nm: steps.append((hi-x)/nm if nm > 0 else (lo-x)/nm)
    result['joint_direction'] = {'status': 'local_common_ascent_candidate',
        'normalized_coordinate_direction': {r['name']: d for r,d in zip(complete,direction)},
        'nm_per_unit_epsilon': {r['name']: d*(r['bounds_nm'][1]-r['bounds_nm'][0]) for r,d in zip(complete,direction)},
        'improvement_rates_per_epsilon': rates,
        'maximum_epsilon_from_bounds_only': min(steps),
        'warning': 'The bound limit is not a recommended step or a validity radius for the linear model.'}
    return result


def _representatives(rows, objectives, budget):
    """Objective extremes then farthest-point coverage; no performance weighting."""
    if len(rows) <= budget:
        return [r['design_id'] for r in rows]
    gids = [g['objective_id'] for g in objectives]
    low = {g: min(r['metrics'][g] for r in rows) for g in gids}
    span = {g: max(r['metrics'][g] for r in rows)-low[g] for g in gids}
    vectors = {r['design_id']: [(r['metrics'][g]-low[g])/span[g] if span[g] else 0 for g in gids] for r in rows}
    chosen = []
    for g in objectives:
        for sign in (1, -1):
            r = max(rows, key=lambda r: sign*r['metrics'][g['objective_id']])
            if r['design_id'] not in chosen and len(chosen) < budget: chosen.append(r['design_id'])
    # After extremes, cover distinct families before filling by spacing.
    covered = {r['family_id'] for r in rows if r['design_id'] in chosen}
    for family in sorted({r['family_id'] for r in rows}-covered):
        r = next(r for r in rows if r['family_id'] == family)
        if len(chosen) < budget: chosen.append(r['design_id'])
    while len(chosen) < budget:
        left = [r['design_id'] for r in rows if r['design_id'] not in chosen]
        chosen.append(max(left, key=lambda k: min(sum((x-y)**2 for x,y in zip(vectors[k],vectors[c])) for c in chosen)))
    return chosen


def _diagnostic(row, pa, da):
    goals = []
    for g in row['diagnostics']['goals']:
        out = {k: deepcopy(g[k]) for k in ('observation_id','goal_id','status','baseline','candidate','delta','improvement','objective_direction','reason','red_gain_blue_loss') if k in g}
        out['subband_sample_means'] = [{k: deepcopy(s[k]) for k in ('subband','status','sampled_range_nm','baseline','candidate','delta','direction') if k in s} for s in g.get('subbands', [])]
        p = g.get('polarization', {})
        out['polarization_sample_means'] = {k: deepcopy(p[k]) for k in ('status','baseline','candidate','s_delta','p_delta','reason') if k in p}
        goals.append(out)
    return {'diagnosis_alias': da[row['diagnosis_id']], 'diagnosis_id': row['diagnosis_id'],
        'comparison_baseline_alias': pa[row['comparison_baseline_design_id']],
        'design_alias': pa[row['design_id']], 'goals': goals}


def build_decision_context(previous, aliases, derivative_getter, *, detail_budget=8):
    """All parents remain selectable; representatives get rich diagnostic detail.

    detail_budget limits prompt detail only, never eligible designs or plans.
    """
    if type(detail_budget) is not int or detail_budget < 1:
        raise ValueError('detail_budget must be a positive integer')
    pa = {v:k for k,v in aliases['parents'].items()}
    da = {v:k for k,v in aliases['diagnostics'].items()}
    archive = previous['archive']; objectives = archive['objectives']
    families, rows = {}, []
    family_aliases = {k: 'F%03d' % (i+1) for i,k in enumerate(sorted({d['family']['family_key'] for d in archive['designs'].values()}))}
    for did, design in sorted(archive['designs'].items()):
        fid = design['family']['family_key']
        fa = family_aliases[fid]
        families.setdefault(fa, {'family_key': fid, 'specification': deepcopy(design['family']['specification'])})
        rows.append({'parent_alias': pa[did], 'design_id': did,
            'metrics': {g:v['value'] for g,v in design['metrics'].items()},
            'thickness_reference': 'design_envelopes.json indexed by this design_id',
            'family_id': fa, 'lineage_parent_alias': pa.get(design['parent_design_id']),
            'parameterization': design['family']['specification'].get('parameterization'),
            'pareto': did in archive['pareto_design_ids']})
    selected = _representatives(rows, objectives, detail_budget)
    details = []
    for did in selected:
        summary = derivative_getter(previous['envelopes'][did], did)
        details.append({'parent_alias': pa[did], 'design_id': did,
            'thicknesses_nm': [l['thickness_nm'] for l in archive['designs'][did]['physical_input']['stack']['layers']],
            'local_direction_facts': local_direction_facts(summary, objectives),
            'derivative_source': {k: deepcopy(summary[k]) for k in ('status','observation_design_id','source_instance_id','parameterization') if k in summary}})
    # Diagnosis index for every parent, but avoid repeating every full comparison.
    diagnostic_index = [{'diagnosis_alias': da[d['diagnosis_id']], 'diagnosis_id': d['diagnosis_id'],
        'design_alias': pa[d['design_id']], 'comparison_baseline_alias': pa[d['comparison_baseline_design_id']],
        'observation_ids': [g['observation_id'] for g in d['diagnostics']['goals'] if g.get('status') == 'observed']}
        for d in previous['diagnostics']]
    by_design = {}
    for d in previous['diagnostics']: by_design[d['design_id']] = d
    diagnostic_details = [_diagnostic(by_design[d],pa,da) for d in selected if d in by_design]
    sheet = previous['context']['sheet']
    payload = {'task_sheet': {k:deepcopy(sheet[k]) for k in ('original_question','user_objective','goal_relationships','explicit_constraints','unknowns_assumptions','unsupported_requirements','candidate_mechanisms') if k in sheet},
        'constraints': deepcopy(previous['context']['constraints']), 'objectives': deepcopy(objectives),
        'route_rationales': [{k:r.get(k) for k in ('id','claim','basis_mode','model_knowledge_rationale','clue_ids','evidence_ids')} for r in previous['accepted']['routes']],
        'decision_clues': [{k:c.get(k) for k in ('id','evidence_id','physical_effect','inference_bridge','conditions_and_unknowns')} for c in previous['accepted'].get('decision_clues',[])],
        'certified_designs': rows, 'family_catalog': families,
        'pareto_parent_aliases': [pa[k] for k in archive['pareto_design_ids']],
        'representative_decisions': details, 'diagnostic_index': diagnostic_index,
        'physics_diagnostics': diagnostic_details,
        'diagnostic_scope': 'Certified sampled-grid objective values. Subband/s/p summaries are descriptive sample means, not objective reductions. Local derivatives remain uncertified; no mechanism or finite-step guarantee.',
        'selection_scope': 'Prefer detailed parents for informed choices; other parents remain selectable but omitted diagnostics are unknown. Every indexed parent remains selectable. Detailed views are representatives, not ranked recommendations. Missing detail is unknown; original full diagnostics and envelopes remain saved.',
        'decision_guidance': ['Check each variable sign and feasible bound direction before choosing an action.',
            'A common local direction can exist despite coordinate-wise conflicts. Untying is an exploration option, never established as necessary by shared gradients.',
            'Use actual subband/polarization values when making spectral claims; otherwise label the mechanism untested.',
            'Explain why selected near-neighbor parents warrant separate searches; no family or action quota.',
            'Predict all goals honestly, including possible tradeoffs or unknown changes; unchanged is not a synonym for a small acceptable loss.']}
    audit = {'schema_version': 'focused_research.decision_context.v1', 'archive_design_count': len(rows),
        'detailed_design_count': len(selected), 'detail_budget': detail_budget,
        'representative_design_ids': selected, 'full_diagnostic_count': len(previous['diagnostics']),
        'prompt_diagnostic_count': len(diagnostic_details), 'all_parent_identities_retained': True,
        'selection_method': 'objective extremes, family representatives, normalized objective-space coverage',
        'omitted_detail_location': 'physics_diagnostics.json and design_envelopes.json in previous_search',
        'payload_characters': len(json.dumps(payload, ensure_ascii=False, separators=(',',':'))),
        'payload_utf8_bytes': len(json.dumps(payload, ensure_ascii=False, separators=(',',':')).encode('utf-8')),
        'additional_forward_design_rows': 0}
    return {'user_payload': payload, 'selection_report': audit}
