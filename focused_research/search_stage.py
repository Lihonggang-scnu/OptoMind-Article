"""Explicit certified numerical search, plan-only revision, and parent-preserving refinement.

No paid client is constructed. `revise` accepts only an injected audited planner,
performs one call, saves its original response, and never executes its plans.
Search forward-design rows and certification evaluations have separate budgets.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import time
from pathlib import Path
import uuid

try:
    from .scientific_stage import load, save, digest, _new_output
    from . import research_routes as rr, route_consumer as rc, research_execution as ex
    from . import family_optimization as fo, design_archive as da, physics_diagnostics as pd, decision_context as dc
except ImportError:
    from scientific_stage import load, save, digest, _new_output
    import research_routes as rr, route_consumer as rc, research_execution as ex
    import family_optimization as fo, design_archive as da, physics_diagnostics as pd, decision_context as dc

SCHEMA = 'focused_research.search_stage.v1'
CONTEXT_FILES = {'routes': 'raw_proposals.json', 'sheet': 'task_sheet.json',
                 'evidence': 'evidence_items.json', 'constraints': 'design_constraints.json',
                 'optical_media': 'optical_media.json', 'wavelengths_nm': 'wavelengths_nm.json'}


def _budget(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(name + ' must be a nonnegative integer')


def _persist_context(output, context):
    for key, filename in CONTEXT_FILES.items():
        save(output / filename, context[key])
    save(output / 'input_bindings.json', {k: digest(v) for k, v in context.items()})


def _seal(output):
    # A reproducibility/integrity binding, not an adversarial signature.
    save(output / 'manifest.json', {p.name: digest(load(p)) for p in sorted(output.glob('*.json'))
                                   if p.name != 'manifest.json'})


def read_search(directory):
    directory = Path(directory)
    manifest = load(directory / 'manifest.json')
    required = set(CONTEXT_FILES.values()) | {'input_bindings.json', 'design_archive.json',
        'design_envelopes.json', 'physics_diagnostics.json', 'accepted_contract.json', 'summary.json'}
    if not required.issubset(manifest):
        raise ValueError('previous search is incomplete')
    for filename, expected in manifest.items():
        if Path(filename).name != filename or digest(load(directory / filename)) != expected:
            raise ValueError('previous search artifact binding mismatch: ' + filename)
    context = {key: load(directory / filename) for key, filename in CONTEXT_FILES.items()}
    if load(directory / 'input_bindings.json') != {k: digest(v) for k, v in context.items()}:
        raise ValueError('previous search input binding mismatch')
    return {'context': context, 'archive': load(directory / 'design_archive.json'),
            'envelopes': load(directory / 'design_envelopes.json'),
            'diagnostics': load(directory / 'physics_diagnostics.json'),
            'accepted': load(directory / 'accepted_contract.json'),
            'manifest_sha256': digest(manifest)}


def _vector(envelope):
    metrics = envelope.get('numerical_search', {}).get('uncertified_metrics', {})
    values = []
    for goal in envelope['objectives']:
        value = metrics.get(goal['objective_id'])
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('candidate missing finite metric for every original objective')
        values.append(value * (1 if goal['sense'] == 'maximize' else -1))
    return values


def rank_candidates(candidates):
    """All Pareto fronts, each ordered by objective extremes then maximin spacing.

    This is certification scheduling only. No scalar preference or candidate-count
    cap is introduced, and unselected/budget-exhausted candidates remain recorded.
    """
    if not candidates:
        return []
    goals = candidates[0]['objectives']
    if any(c['objectives'] != goals for c in candidates):
        raise ValueError('candidate objective mismatch')
    vectors = [_vector(c) for c in candidates]
    n = len(goals)
    low = [min(v[j] for v in vectors) for j in range(n)]
    span = [max(v[j] for v in vectors)-low[j] for j in range(n)]
    normalized = [[(v[j]-low[j])/span[j] if span[j] else 0.0 for j in range(n)] for v in vectors]
    pending, ordered = list(range(len(candidates))), []
    while pending:
        front = [i for i in pending if not any(
            all(vectors[k][j] >= vectors[i][j] for j in range(n)) and
            any(vectors[k][j] > vectors[i][j] for j in range(n)) for k in pending if k != i)]
        chosen = []
        for j in range(n):
            i = max(front, key=lambda k: (vectors[k][j], -k))
            if i not in chosen:
                chosen.append(i)
        left = [i for i in front if i not in chosen]
        while left:
            i = max(left, key=lambda k: (min(sum((a-b)**2 for a,b in zip(normalized[k],normalized[c]))
                                            for c in chosen), -k))
            chosen.append(i)
            left.remove(i)
        ordered += chosen
        pending = [i for i in pending if i not in front]
    return [candidates[i] for i in ordered]


def select_certification_candidates(candidates, archive, *, max_new_evals,
                                    objective_tolerance=1e-4, relative_thickness_tolerance=1e-3,
                                    verified_cached_instance_ids=None):
    """Bound certification to meaningful proposals; retain every deferred proposal.

    Tolerances are scheduling heuristics, never physical equivalence, acceptance,
    dominance, or success thresholds. A nearby structure is deferred only when
    ALL objective changes and ALL relative layer-thickness changes are small.
    Exact archived rediscoveries remain eligible for cached derivative ingestion.
    """
    _budget(max_new_evals,'max_new_evals')
    for value in (objective_tolerance,relative_thickness_tolerance):
        if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
            raise ValueError('finite nonnegative shortlist tolerances required')
    verified_cached_instance_ids = set(verified_cached_instance_ids or ())
    ranked = rank_candidates(candidates)
    designs = (archive or {}).get('designs',{})
    def physical(task):
        return da._physical(task)
    def shape(task):
        t = copy.deepcopy(task)
        for layer in t['stack']['layers']:
            layer.pop('thickness_nm')
        return t
    reference = []
    exact = {}
    for design_id,design in designs.items():
        task = physical(design['physical_input'])
        key = digest(task)
        exact[key] = design_id
        reference.append({'id':design_id,'task':task,'shape':shape(task),
                          'metrics':{k:v['value'] for k,v in design['metrics'].items()}})
    selected, deferred, decisions = [], [], []
    new_keys = {}
    for envelope in ranked:
        task = physical(envelope['tmm_task'])
        key = digest(task)
        metrics = envelope['numerical_search']['uncertified_metrics']
        ident = envelope['instance_id']
        if key in exact:
            if ident in verified_cached_instance_ids:
                selected.append(envelope)
                decisions.append({'instance_id':ident,'decision':'selected_verified_cache_rediscovery',
                                  'reference_design_id':exact[key],'reserved_new_evaluations':0})
            else:
                record = {'instance_id':ident,'decision':'deferred_unvalidated',
                    'reason':'archived_rediscovery_cache_unavailable', 'reference_id':exact[key],
                    'reserved_new_evaluations':0}
                deferred.append(record); decisions.append(record)
            continue
        reason, reference_id = None, None
        if key in new_keys:
            reason, reference_id = 'exact_duplicate_proposal',new_keys[key]
        else:
            topology = shape(task)
            for other in reference:
                if topology != other['shape']:
                    continue
                if not all(abs(metrics[g]-other['metrics'][g]) <= objective_tolerance for g in metrics):
                    continue
                near = all(abs(a['thickness_nm']-b['thickness_nm']) / max(1.0,abs(a['thickness_nm']),abs(b['thickness_nm']))
                           <= relative_thickness_tolerance
                           for a,b in zip(task['stack']['layers'],other['task']['stack']['layers']))
                if near:
                    reason,reference_id = 'near_duplicate_structure_and_objectives',other['id']
                    break
        if reason is None and len(new_keys) >= max_new_evals:
            reason = 'certification_budget_reserved_for_more_distinct_candidates'
        if reason is not None:
            record = {'instance_id':ident,'decision':'deferred_unvalidated','reason':reason,
                      'reference_id':reference_id,'reserved_new_evaluations':0}
            deferred.append(record)
            decisions.append(record)
            continue
        selected.append(envelope)
        new_keys[key] = ident
        reference.append({'id':ident,'task':task,'shape':topology,'metrics':metrics})
        decisions.append({'instance_id':ident,'decision':'selected_new_candidate','reserved_new_evaluations':1})
    return selected, {'schema_version':'focused_research.certification_selection.v1',
        'candidate_count':len(candidates),'selected_count':len(selected),
        'reserved_new_evaluations':len(new_keys),'max_new_evals':max_new_evals,
        'deferred_unvalidated_count':len(deferred),
        'objective_tolerance':objective_tolerance,'relative_thickness_tolerance':relative_thickness_tolerance,
        'decisions':decisions,
        'limits':['Scheduling heuristics only; deferred designs remain unvalidated, not scientific failures.',
                  'No objective, certification, physics, or archive dominance threshold is relaxed.',
                  'Additional disk-cache hits may leave some certification budget unused.']}


def _family(route, parameterization):
    return {'route_id': route['id'], 'family': copy.deepcopy(route['family']),
            'parameterization': parameterization}


def _validate_candidate(envelope, template, route, parameterization):
    """Reject changes outside the original physical search space before certification."""
    if envelope.get('route_id') != route['id'] or envelope.get('objectives') != template['objectives']:
        raise ValueError('optimizer changed route or original objectives')
    a, b = copy.deepcopy(envelope['tmm_task']), copy.deepcopy(template['tmm_task'])
    if len(a['stack']['layers']) != len(b['stack']['layers']):
        raise ValueError('optimizer changed original layer count')
    bound_values = {}
    for physical, reference, spec in zip(a['stack']['layers'],b['stack']['layers'],route['family']['layers']):
        value = physical.pop('thickness_nm')
        reference.pop('thickness_nm')
        if type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
            raise ValueError('optimizer returned invalid thickness')
        variable = spec['thickness_nm']
        if isinstance(variable,str):
            lo,hi = route['family']['thickness_variables_nm'][variable]
            if not lo <= value <= hi:
                raise ValueError('optimizer exceeded original family bounds')
            if parameterization == 'family' and variable in bound_values and bound_values[variable] != value:
                raise ValueError('optimizer broke shared family bindings')
            bound_values[variable] = value
        elif value != variable:
            raise ValueError('optimizer changed fixed layer thickness')
    a['stack'].pop('name',None); b['stack'].pop('name',None)
    if a != b:
        raise ValueError('optimizer changed original material identities or physical conditions')


def _record(archive, envelopes, result, envelope, *, parent, family, role):
    if not result.get('physics_accepted'):
        return archive, None
    if archive is None:
        archive = da.create_design_archive(envelope['objectives'], envelope['tmm_task'])
    archive = da.add_certified_design(archive, result, physical_input=envelope['tmm_task'],
        parent_design_id=parent, family=family, role=role)
    design_id = archive['latest_event']['design_id']
    # Preserve the original certified design and original envelope on rediscovery.
    original = copy.deepcopy(envelope)
    # Optimizers copy their seed envelope. Never inherit a different design's
    # derivative-history metadata into a newly certified physical design.
    original.pop('numeric_search_observations', None)
    envelopes.setdefault(design_id, original)
    search = envelope.get('numerical_search', {})
    if 'local_derivative_summary' in search:
        envelopes[design_id].setdefault('numeric_search_observations', []).append({
            'design_id':design_id, 'instance_id':envelope['instance_id'],
            'route_id':envelope['route_id'], 'parameterization':search.get('parameterization'),
            'evaluation_index':search.get('evaluation_index'),
            'parent_evaluation_index':search.get('parent_evaluation_index'),
            'local_derivative_summary':copy.deepcopy(search['local_derivative_summary'])})
    return archive, design_id


def _latest_local_derivative(envelope, design_id):
    observations = [o for o in envelope.get('numeric_search_observations', [])
                    if o.get('design_id') == design_id]
    actual = [o for o in observations if o.get('local_derivative_summary', {}).get('status') not in (None,'unknown')]
    if actual or observations:
        record = (actual or observations)[-1]
        return {**copy.deepcopy(record['local_derivative_summary']),
                'observation_design_id':design_id, 'source_instance_id':record['instance_id'],
                'parameterization':record['parameterization']}
    return copy.deepcopy(envelope.get('numerical_search', {}).get('local_derivative_summary',
        {'status':'unknown','reason':'no stored local derivative for this design'}))


def _diagnostic(archive, baseline_id, design_id, goals):
    baseline = copy.deepcopy(archive['designs'][baseline_id]['certified_result'])
    candidate = copy.deepcopy(archive['designs'][design_id]['certified_result'])
    original_routes = {'baseline':baseline['route_id'],'candidate':candidate['route_id']}
    # Archive ingestion froze the complete objective/condition context. A reused
    # design's first route label must not forbid this certified-context comparison.
    comparison_id = 'comparison:' + digest([archive['context_id'],baseline_id,design_id])
    baseline['route_id'] = candidate['route_id'] = comparison_id
    info = pd.build_physics_diagnostics(baseline,candidate,goals)
    row = {'comparison_baseline_design_id': baseline_id,
           'parent_design_id': archive['designs'][design_id]['parent_design_id'],
           'design_id': design_id, 'original_route_ids': original_routes,
           'comparison_context_id':archive['context_id'], 'diagnostics': info}
    return {'diagnosis_id': 'diagnosis:' + digest(row), **row}


def _aliases(previous):
    return {'parents': {'P%03d' % (i+1): k for i,k in enumerate(sorted(previous['archive']['designs']))},
            'diagnostics': {'D%03d' % (i+1): d['diagnosis_id'] for i,d in enumerate(previous['diagnostics'])}}


def _validate_refinement_action(previous, parent, action):
    """One execution gate shared by the compiler and advertised action choices."""
    if action not in ('refine_family', 'refine_layers'):
        raise ValueError('unsupported plan action')
    if action == 'refine_family':
        envelope = previous['envelopes'][parent]
        route = next(r for r in previous['accepted']['routes'] if r['id'] == envelope['route_id'])
        bindings = {}
        for layer, physical in zip(route['family']['layers'], envelope['tmm_task']['stack']['layers']):
            variable = layer['thickness_nm']
            if isinstance(variable, str):
                value = physical['thickness_nm']
                if variable in bindings and bindings[variable] != value:
                    raise ValueError('parent has independent layer values; use refine_layers to preserve it')
                bindings[variable] = value


def _refinement_action_availability(previous, parent):
    available, unavailable = [], {}
    for action in ('refine_family', 'refine_layers'):
        try:
            _validate_refinement_action(previous, parent, action)
            available.append(action)
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            unavailable[action] = str(exc) or 'missing parent execution context'
    return {'available_actions': available, 'unavailable_actions': unavailable}


def _revision_decision_view(previous, aliases):
    """Bind ephemeral display aliases and executable choices to this snapshot."""
    view = dc.build_decision_context(previous, aliases, _latest_local_derivative)
    payload = view['user_payload']
    payload['identity_scope'] = {
        'snapshot_manifest_sha256': previous['manifest_sha256'],
        'stable_identity_fields': ['design_id', 'diagnosis_id'],
        'parent_alias_index': 'certified_designs',
        'diagnosis_alias_index': 'diagnostic_index',
        'alias_lifetime': 'Current snapshot only. P/D labels can change after archive growth; '
                          'never resolve a historical label using this snapshot.',
        'selection_contract': 'Use a supplied stable design_id/diagnosis_id or its current alias. '
                              'Choose an available action for that exact current parent.'}
    for row in payload['certified_designs']:
        parent = row['design_id']
        row['lineage_parent_design_id'] = previous['archive']['designs'][parent].get('parent_design_id')
        row.update(_refinement_action_availability(previous, parent))
    diagnostics = {row['diagnosis_id']: row for row in previous['diagnostics']}
    for name in ('diagnostic_index', 'physics_diagnostics'):
        for row in payload[name]:
            source = diagnostics[row['diagnosis_id']]
            row.update({key: source[key] for key in ('design_id', 'comparison_baseline_design_id')})
    # The measured size must cover the actual enriched payload, not its base view.
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    view['selection_report'].update(payload_characters=len(encoded),
        payload_utf8_bytes=len(encoded.encode('utf-8')), snapshot_aliases_bound=True,
        parent_action_availability_supplied=True)
    return view


def _compile_plans(payload, previous):
    """Tolerate imperfect reasoning, never imperfect execution identity or bounds."""
    if not isinstance(payload, dict) or not isinstance(payload.get('plans'), list):
        raise ValueError('revision response must contain a plans list')
    if not previous.get('archive') or not previous['archive']['designs']:
        raise ValueError('no certified parents available for refinement')
    aliases = _aliases(previous)
    diagnostics = {r['diagnosis_id']: r for r in previous['diagnostics']}
    goals = {g['objective_id'] for g in (previous['archive'] or {}).get('objectives', [])}
    accepted, rejected, audits = [], [], []
    for index, source in enumerate(payload['plans']):
        warnings = []
        try:
            if not isinstance(source, dict):
                raise ValueError('plan must be an object')
            if set(source) & {'layers','family','bounds','objectives','materials','physical_input','tmm_task'}:
                raise ValueError('plans cannot rewrite physical designs, bounds, or objectives')
            parent = aliases['parents'].get(source.get('parent_design_id'),source.get('parent_design_id'))
            if parent not in (previous['archive'] or {}).get('designs', {}):
                raise ValueError('plan parent must be an already certified design_id or parent alias')
            action = source.get('action')
            _validate_refinement_action(previous, parent, action)
            diagnosis = source.get('diagnosis', {})
            if not isinstance(diagnosis, dict):
                diagnosis = {'explanation': str(diagnosis)}
            diagnosis_id = aliases['diagnostics'].get(diagnosis.get('diagnosis_id'),diagnosis.get('diagnosis_id'))
            ref = diagnostics.get(diagnosis_id)
            valid = {g['observation_id'] for g in ref['diagnostics']['goals'] if g.get('status') == 'observed'} if ref else set()
            refs = diagnosis.get('observation_ids', [])
            refs = refs if isinstance(refs,list) else []
            verified = bool(ref and parent in (ref['comparison_baseline_design_id'],ref['design_id']) and refs and
                            all(isinstance(r,str) and r in valid for r in refs))
            explanation = diagnosis.get('explanation','')
            if not isinstance(explanation,str): explanation = str(explanation)
            if not verified:
                warnings.append('unverified_reasoning: missing, foreign, or invalid observation references')
            changes = source.get('expected_goal_changes', {})
            if not isinstance(changes,dict): changes = {}
            normalized = {}
            for gid in sorted(goals):
                value = changes.get(gid)
                if value not in ('increase','decrease','unchanged','unknown'):
                    value = 'unknown'
                    warnings.append('expected change for '+gid+' filled as unknown')
                normalized[gid] = value
            if set(changes)-goals:
                warnings.append('unknown goal predictions omitted; original goals unchanged')
            accepted.append({'parent_design_id':parent,'action':action,
                'diagnosis':{'diagnosis_id':diagnosis_id if verified else None,
                             'observation_ids':refs if verified else [],'explanation':explanation,
                             'verification':'verified_references' if verified else 'unverified_reasoning'},
                'expected_goal_changes':normalized})
            audits.append({'source_index':index,'accepted_index':len(accepted)-1,'warnings':warnings})
        except (ValueError,TypeError,KeyError,StopIteration) as exc:
            rejected.append({'source_index':index,'error':str(exc),'raw_plan':copy.deepcopy(source)})
    quality_warnings = []
    for i,plan in enumerate(accepted):
        current = previous['archive']['designs'][plan['parent_design_id']]
        if not plan['diagnosis']['explanation'].strip():
            quality_warnings.append({'plan_index':i,'code':'missing_interpretation',
                'message':'No interpretation supplied; execution remains an exploratory numerical test.'})
        for j,other in enumerate(accepted[:i]):
            before = previous['archive']['designs'][other['parent_design_id']]
            exact = plan['parent_design_id'] == other['parent_design_id']
            if exact and plan['action'] == other['action']:
                quality_warnings.append({'plan_indices':[j,i],'code':'repeated_parent_and_action',
                    'message':'Repeated search may be intentional but needs a distinct scientific reason; not rejected.'})
                continue
            a,b = current['physical_input']['stack']['layers'],before['physical_input']['stack']['layers']
            same_topology = len(a)==len(b) and all(
                {k:v for k,v in x.items() if k!='thickness_nm'} == {k:v for k,v in y.items() if k!='thickness_nm'}
                for x,y in zip(a,b))
            if not same_topology:
                continue
            objective_distance = max(abs(current['metrics'][g]['value']-before['metrics'][g]['value']) for g in goals)
            thickness_distance = max(abs(x['thickness_nm']-y['thickness_nm'])/max(1.0,abs(x['thickness_nm']),abs(y['thickness_nm'])) for x,y in zip(a,b))
            if objective_distance <= 1e-3 and thickness_distance <= 5e-3:
                quality_warnings.append({'plan_indices':[j,i],'code':'nearby_parent_searches',
                    'max_absolute_objective_difference':objective_distance,
                    'max_relative_layer_thickness_difference':thickness_distance,
                    'message':'Nearby parents selected; inspect whether their proposed tests add information. No rejection or diversity quota.'})
    return {'plans':accepted}, {'accepted_count':len(accepted),'rejected_count':len(rejected),
        'rejected_plans':rejected,'normalization_audit':audits,'quality_warnings':quality_warnings,
        'quality_scope':'Advisory factual warnings only. Numerical progress and scientific reasoning are assessed separately; negative or exploratory hypotheses remain legitimate.'}


def _validate_plans(payload, previous):
    return _compile_plans(payload, previous)[0]


def _compact_diagnostic(row, parent_alias, diagnosis_alias):
    """Prompt view only: full diagnostics remain persisted, every goal stays visible."""
    source = row['diagnostics']
    goals = []
    for g in source['goals']:
        brief = {k:copy.deepcopy(g[k]) for k in ('observation_id','goal_id','status','baseline','candidate',
                 'delta','improvement','objective_direction','reason','band_extrema','red_gain_blue_loss') if k in g}
        if 'subbands' in g:
            brief['subband_sample_means'] = [{k:b[k] for k in ('subband','status','sample_count','sampled_range_nm',
                                             'delta','sense_adjusted_delta','direction','reason') if k in b}
                                           for b in g['subbands']]
        if 'polarization' in g:
            p = g['polarization']
            brief['polarization_sample_means'] = {k:copy.deepcopy(p[k]) for k in ('status','baseline','candidate',
                's_delta','p_delta','s_minus_p_delta','reason') if k in p}
        reflection = g.get('reflection_bands', {})
        if reflection.get('status') == 'observed':
            brief['reflection_bands'] = copy.deepcopy(reflection)
        goals.append(brief)
    return {'diagnosis_alias':diagnosis_alias[row['diagnosis_id']],
            'comparison_baseline_alias':parent_alias[row['comparison_baseline_design_id']],
            'parent_alias':parent_alias.get(row['parent_design_id']), 'design_alias':parent_alias[row['design_id']],
            'goals':goals,'whole_stack_scaling':copy.deepcopy(source['whole_stack_scaling']),
            'observed_objective_tradeoff':source['next_iteration_evidence']['observed_objective_tradeoff']}


def revise(previous_search, *, output, planner):
    """One audited planner call, raw response retained even when invalid. No retries."""
    if planner is None:
        raise ValueError('revise requires an explicitly injected audited planner')
    previous = read_search(previous_search)
    if not previous['archive'] or not previous['archive']['designs'] or not previous['diagnostics']:
        raise ValueError('certified designs and diagnostics required before revision')
    output = _new_output(output, [previous_search])
    aliases = _aliases(previous)
    decision_view = _revision_decision_view(previous, aliases)
    prompt = {'system_prompt':
        'Return JSON only, exactly {"plans":[...]}. Plan numerical refinement, never layer JSON. '
        'Each plan has parent_design_id, action (refine_family or refine_layers), diagnosis '
        '(diagnosis_id, observation_ids, explanation citing concrete measured changes), '
        'expected_goal_changes (every original objective_id mapped to increase/decrease/unchanged/unknown). '
        'Use supplied stable design_id/diagnosis_id or current parent_alias P001 etc / diagnosis_alias D001 etc. '
        'Short aliases belong only to the current identity_scope snapshot; historical labels are not current identities. '
        'Choose only supplied certified parents. Preserve all original objectives and physical conditions. '
        'Choose only an action listed in that exact parent row available_actions; unavailable_actions explains exclusions. '
        'refine_family preserves shared variables; refine_layers explicitly creates independent layer variables '
        'within original family bounds. Expectations are untested, not outcomes. No route or plan-count ceiling. '
        'Interpret measurements, local derivative arithmetic, and hypotheses separately. '
        'Use program-derived action_effects for local coordinate sign arithmetic, and check bounds feasibility separately. '
        'These effects change one existing coordinate with others fixed; shared-variable effects do not identify individual layer effects. '
        'Missing or zero derivatives leave finite-step effects unknown. An unsupported joint-direction helper means no assessment, not no common feasible direction. '
        'Local directional estimates do not establish finite-step outcomes or a unique mechanism. '
        'Keep alternative physical hypotheses and refinement-search expectations distinct from saved local estimates; they need not match single-coordinate signs. '
        'Explain why the chosen parent and action are informative; nearby parents need a reason, not a diversity quota. '
        'Exploratory actions, predicted tradeoffs, and unsuccessful hypotheses are legitimate; do not promise improvement. '
        'An empty plans list is allowed. Do not fabricate observations, material identities or new bounds.',
        'user_payload':decision_view['user_payload']}
    save(output / 'decision_context_report.json',decision_view['selection_report'])
    save(output / 'revision_aliases.json', aliases)
    save(output / 'revision_prompt.json', prompt)
    save(output / 'revision_parent.json', {'previous_search': str(Path(previous_search).resolve()),
        'manifest_sha256': previous['manifest_sha256'], 'next_action_executed': False})
    status, error, response_normalization = 'revision_failed', None, None
    try:
        response = planner._call_query_plan_model(copy.deepcopy(prompt))
        save(output / 'raw_response.json', {'response': response})
        text_response = response.strip() if isinstance(response,str) else response
        if isinstance(text_response,str) and text_response.startswith(('```json\n','```\n')) and text_response.endswith('```'):
            text_response = '\n'.join(text_response.splitlines()[1:-1])
            response_normalization = 'removed_single_outer_json_code_fence'
        payload = json.loads(text_response) if isinstance(text_response,str) else text_response
        plans, report = _compile_plans(payload, previous)
        save(output / 'refinement_plans.json', plans)
        save(output / 'plan_report.json', report)
        status = 'plans_partial' if report['rejected_count'] else 'plans_recorded'
    except Exception as exc:
        error = str(exc)
        if not (output / 'raw_response.json').exists():
            save(output / 'raw_response.json', {'response': None, 'transport_error': error})
    finish_reason = getattr(planner, 'last_transport', {}).get('finish_reason')
    generation_complete = None if finish_reason is None else finish_reason == 'stop'
    if generation_complete is False and status == 'plans_recorded':
        status = 'plans_partial'
    summary = {'schema_version': SCHEMA, 'stage': 'revise', 'status': status, 'error': error,
        'generation_complete': generation_complete,
        'planner_calls': 1, 'implicit_retries': 0, 'next_action_executed': False,
        'response_normalization':response_normalization,
        'usage': getattr(planner, 'last_usage', {}), 'transport': getattr(planner, 'last_transport', {})}
    save(output / 'summary.json', summary)
    _seal(output)
    return summary


def run_search(routes=None, sheet=None, evidence=None, constraints=None, *, optical_media=None,
               wavelengths_nm=None, output, cache_dir, max_forward_evaluations,
               max_new_evals, max_workers=1, seed=42, backend='auto', previous_search=None,
               plans=None, parameterization='family', include_polarization_channels=None):
    """Run search or refine; failed and un-certified designs remain explicit artifacts."""
    _budget(max_forward_evaluations, 'max_forward_evaluations')
    _budget(max_new_evals, 'max_new_evals')
    started_wall, started_cpu = time.perf_counter(), time.process_time()
    if type(max_workers) is not int or max_workers < 1:
        raise ValueError('positive worker count required')
    if parameterization not in ('family','layerwise_refinement'):
        raise ValueError('unsupported parameterization')
    previous = read_search(previous_search) if previous_search else None
    if previous:
        if include_polarization_channels is not None:
            old_pols = {p for e in previous['envelopes'].values() for p in e['tmm_task']['illumination']['polarizations']}
            if include_polarization_channels != {'s','p'}.issubset(old_pols):
                raise ValueError('refinement must retain original diagnostic polarization channels')
        supplied = dict(routes=routes, sheet=sheet, evidence=evidence, constraints=constraints,
                        optical_media=optical_media, wavelengths_nm=wavelengths_nm)
        if any(v is not None and v != previous['context'][k] for k,v in supplied.items()):
            raise ValueError('refinement must retain original inputs and physical conditions')
        context = previous['context']
        routes, sheet, evidence, constraints, optical_media, wavelengths_nm = (
            context[k] for k in ('routes','sheet','evidence','constraints','optical_media','wavelengths_nm'))
        plans, plan_report = _compile_plans(plans, previous)
    else:
        if plans is not None:
            raise ValueError('plans require previous_search')
        context = dict(routes=routes, sheet=sheet, evidence=evidence, constraints=constraints,
                       optical_media=optical_media, wavelengths_nm=wavelengths_nm)
    compiled = rr.compile_candidates(routes, sheet, evidence, design_constraints=constraints)
    accepted = compiled['accepted_contract']
    output = _new_output(output, [previous_search] if previous_search else [])
    _persist_context(output, context)
    save(output / 'accepted_contract.json', accepted)
    save(output / 'candidate_report.json', compiled['candidate_report'])
    if previous:
        save(output / 'refinement_plans.json', plans)
        save(output / 'plan_report.json', plan_report)
        save(output / 'search_parent.json', {'previous_search': str(Path(previous_search).resolve()),
                                            'manifest_sha256': previous['manifest_sha256']})
    archive = copy.deepcopy(previous['archive']) if previous else None
    design_envelopes = copy.deepcopy(previous['envelopes']) if previous else {}
    diagnostics = copy.deepcopy(previous['diagnostics']) if previous else []
    failures, all_envelopes, all_results, jobs = [], [], [], []
    route_map = {r['id']:r for r in accepted['routes']}
    seed_batch = {'results': [], 'metering': {'new_evaluations': 0}}
    seed_ids, seed_groups = {}, {}
    # Reservation exists before certification or optimizer dispatch.
    save(output / 'budget_reservation.json', {'max_forward_design_rows': max_forward_evaluations,
        'max_new_certification_evaluations': max_new_evals, 'seed': seed,
        'budget_scope': 'Separate search solver design rows and certification tasks; failures consume attempted work.'})
    if previous:
        for plan in plans['plans']:
            parent = plan['parent_design_id']
            envelope = copy.deepcopy(design_envelopes[parent])
            route = route_map[envelope['route_id']]
            mode = 'family' if plan['action'] == 'refine_family' else 'layerwise_refinement'
            jobs.append({'route': route, 'seeds': [envelope], 'parent': parent, 'parameterization': mode})
    else:
        for route in accepted['routes']:
            if route['readiness'] != 'ready':
                continue
            instances = rr.materialize(accepted, route['id'], sheet, evidence,
                design_constraints=constraints, candidate_mode=True, candidate_report=compiled['candidate_report'])
            for instance in instances:
                try:
                    envelope = rc.prepare_tmm_inputs(instance, optical_media=optical_media, wavelengths_nm=wavelengths_nm)
                    if include_polarization_channels is not False:
                        pols = envelope['tmm_task']['illumination']['polarizations']
                        for pol in ('s', 'p'):
                            if pol not in pols:
                                pols.append(pol)
                    all_envelopes.append(envelope)
                except (ValueError, TypeError, KeyError) as exc:
                    failures.append({'stage':'prepare','route_id':route['id'],
                                     'instance_id':instance['instance_id'],'error':str(exc)})
        seed_batch = ex.execute_prepared_batch(all_envelopes, cache_dir=cache_dir,
            max_new_evals=max_new_evals, max_workers=max_workers)
        all_results += seed_batch['results']
        for envelope, result in zip(all_envelopes, seed_batch['results']):
            route = route_map[envelope['route_id']]
            try:
                archive, design_id = _record(archive, design_envelopes, result, envelope, parent=None,
                                             family=_family(route,'family'), role='baseline_reference')
                if design_id:
                    seed_ids[(envelope['route_id'],envelope['instance_id'])] = design_id
                    seed_groups.setdefault(route['id'],[]).append(envelope)
            except (ValueError, TypeError, KeyError) as exc:
                failures.append({'stage':'archive_seed','instance_id':envelope['instance_id'],'error':str(exc)})
        for rid, seeds in seed_groups.items():
            first = seed_ids[(rid,seeds[0]['instance_id'])]
            for envelope in seeds:
                diagnostics.append(_diagnostic(archive, first, seed_ids[(rid,envelope['instance_id'])],sheet['subgoals']))
            jobs.append({'route': route_map[rid], 'seeds':seeds, 'parent':None,'parameterization':parameterization})
    quotas = [max_forward_evaluations // len(jobs) + int(i < max_forward_evaluations % len(jobs))
              for i in range(len(jobs))]
    save(output / 'search_reservations.json', [{'job_index':i, 'route_id':j['route']['id'],
        'parent_design_id':j['parent'], 'parameterization':j['parameterization'],
        'reserved_forward_design_rows':quotas[i]} for i,j in enumerate(jobs)])
    candidates, searches, ancestry = [], [], {}
    nonce = uuid.uuid4().hex
    for i, job in enumerate(jobs):
        try:
            search = fo.optimize_family(job['route'],job['seeds'],max_forward_evaluations=quotas[i],
                seed=seed+i, parameterization=job['parameterization'],backend=backend)
            searches.append({'job_index':i,'route_id':job['route']['id'],
                             'parent_design_id':job['parent'], 'search':search})
            for proposal in search['candidates']:
                envelope = copy.deepcopy(proposal)
                _validate_candidate(envelope,job['seeds'][0],job['route'],job['parameterization'])
                # Fresh execution/display identity even if a design was rediscovered.
                envelope['instance_id'] = 'search_' + nonce + '_' + str(len(candidates))
                envelope['tmm_task']['stack']['name'] = envelope['instance_id']
                parent = job['parent']
                source = proposal.get('parent_instance_id')
                if parent is None and source:
                    parent = seed_ids.get((envelope['route_id'],source))
                reference = parent or seed_ids.get((envelope['route_id'],job['seeds'][0]['instance_id']))
                ancestry[envelope['instance_id']] = {'parent':parent,'reference':reference,
                                                     'family':_family(job['route'],job['parameterization'])}
                candidates.append(envelope)
        except Exception as exc:
            # A failed job still consumes its reservation; no unmetered automatic retry.
            failures.append({'stage':'search','job_index':i,'route_id':job['route']['id'],
                             'reserved_forward_design_rows':quotas[i], 'error':str(exc)})
    remaining = max_new_evals - seed_batch['metering']['new_evaluations']
    archived_physics = {digest(da._physical(d['physical_input'])) for d in (archive or {}).get('designs',{}).values()}
    rediscoveries = [e for e in candidates if digest(da._physical(e['tmm_task'])) in archived_physics]
    # Zero-budget executor probe validates the actual current source/material/
    # verifier identity and cache files. Archive membership alone never proves a hit.
    cache_probe = ex.execute_prepared_batch(rediscoveries,cache_dir=cache_dir,max_new_evals=0,max_workers=max_workers)
    save(output / 'rediscovery_cache_probe.json',cache_probe)
    verified_cached = {r['instance_id']:r for r in cache_probe['results'] if r.get('physics_accepted') and r.get('cache_hit')}
    selected, selection_report = select_certification_candidates(candidates,archive,max_new_evals=remaining,
        verified_cached_instance_ids=verified_cached)
    candidates_by_id = {e['instance_id']:e for e in candidates}
    ranked = [candidates_by_id[d['instance_id']] for d in selection_report['decisions']]
    save(output / 'certification_selection.json',selection_report)
    candidate_batch = ex.execute_prepared_batch([e for e in selected if e['instance_id'] not in verified_cached],
        cache_dir=cache_dir,max_new_evals=remaining,max_workers=max_workers)
    dispatched = {r['instance_id']:r for r in candidate_batch['results']}
    candidate_batch['results'] = [verified_cached[e['instance_id']] if e['instance_id'] in verified_cached
                                  else dispatched[e['instance_id']] for e in selected]
    all_envelopes += ranked
    result_by_id = {r['instance_id']:r for r in candidate_batch['results']}
    deferred_by_id = {r['instance_id']:r for r in selection_report['decisions'] if r['decision']=='deferred_unvalidated'}
    for envelope in ranked:
        ident = envelope['instance_id']
        if ident in result_by_id:
            all_results.append(result_by_id[ident])
        else:
            all_results.append({'instance_id':ident,'route_id':envelope['route_id'],
                'status':'deferred_unvalidated','physics_accepted':False,'certification_attempted':False,
                'reason':deferred_by_id[ident]['reason'],'selection_reference':deferred_by_id[ident].get('reference_id'),
                'metering':{'new_evaluations':0}})
    for envelope,result in zip(selected,candidate_batch['results']):
        relation = ancestry[envelope['instance_id']]
        try:
            archive, design_id = _record(archive,design_envelopes,result,envelope,parent=relation['parent'],
                                        family=relation['family'],role='candidate')
            if design_id and relation['reference']:
                diagnostics.append(_diagnostic(archive,relation['reference'],design_id,sheet['subgoals']))
        except (ValueError,TypeError,KeyError) as exc:
            failures.append({'stage':'archive_candidate','instance_id':envelope['instance_id'],'error':str(exc)})
    diagnostics = list({d['diagnosis_id']:d for d in diagnostics}.values())
    forward_rows = sum(s['search']['metering']['forward_design_rows'] for s in searches)
    solver_rows = sum(s['search']['metering']['solver_channel_rows'] for s in searches)
    search_failures = [f for f in failures if f['stage']=='search']
    summary = {'schema_version':SCHEMA,'stage':'refine' if previous else 'search',
        'status':'search_complete' if archive and not failures and (not previous or not plan_report['rejected_count']) and all(r.get('physics_accepted') for r in seed_batch['results'] + candidate_batch['results']) else 'partial',
        'live_model_called':False,
        'diagnostic_polarization_channels':sorted({p for e in design_envelopes.values() for p in e['tmm_task']['illumination']['polarizations']}),
        'numerical_search_jobs':len(jobs),'candidate_count':len(candidates),
        'certified_design_count':len(archive['designs']) if archive else 0,
        'pareto_design_ids':archive['pareto_design_ids'] if archive else [],
        'uncertified_candidate_count':len(deferred_by_id)+sum(not r.get('physics_accepted') for r in candidate_batch['results']),
        'deferred_unvalidated_candidate_count':len(deferred_by_id),
        'certification_attempted_candidate_count':len(candidate_batch['results']),
        'certification_coverage':'partial' if deferred_by_id or failures or any(not r.get('physics_accepted') for r in seed_batch['results']+candidate_batch['results']) else 'complete',
        'scientific_outcome':'Not inferred from execution status; compare certified metrics and reasoning separately.',
        'metering':{'parent_process_cpu_seconds':time.process_time()-started_cpu,
            'stage_wall_seconds':time.perf_counter()-started_wall,
            'cpu_scope':'Parent process only, including serial optimization; excludes certification child-process CPU. Not total pipeline CPU.',
            'search_forward_design_rows':forward_rows,'search_solver_channel_rows':solver_rows,
            'search_backward_passes':sum(s['search']['metering'].get('backward_passes',0) for s in searches),
            'search_metering_complete':not search_failures,
            'search_failed_job_reserved_rows':sum(f['reserved_forward_design_rows'] for f in search_failures),
            'search_max_forward_design_rows':max_forward_evaluations,
            'certification_new_evaluations':seed_batch['metering']['new_evaluations']+candidate_batch['metering']['new_evaluations'],
            'certification_max_new_evaluations':max_new_evals,
            'certification_cache_hits_total':seed_batch['metering'].get('cache_hits',0)+candidate_batch['metering'].get('cache_hits',0)+cache_probe['metering'].get('cache_hits',0),
            'certification_metering_scope':'Seed execution, new candidate dispatch, and zero-budget rediscovery cache probe are disjoint counters.',
            'seed_certification':seed_batch['metering'],'candidate_certification':candidate_batch['metering'],
            'rediscovery_cache_probe':cache_probe['metering']},
        'mechanism_proven':False,'claim_scope':'certified_sampled_grid_only'}
    for name,value in [('execution_inputs',all_envelopes),('execution_results',all_results),
        ('numerical_search',searches),('candidate_ancestry',ancestry),('design_archive',archive),
        ('design_envelopes',design_envelopes),('physics_diagnostics',diagnostics),('input_failures',failures),('summary',summary)]:
        save(output / (name+'.json'),value)
    _seal(output)
    return summary


def main(argv=None, *, planner=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=['search','revise','refine'])
    for option in ('task-sheet','evidence-file','constraints-file','routes-file','optical-media','wavelengths',
                   'previous-search','plans-file','revision-dir','cache-dir'):
        parser.add_argument('--'+option,type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--max-forward-evaluations',type=int)
    parser.add_argument('--max-new-evals',type=int)
    parser.add_argument('--max-workers',type=int,default=1)
    parser.add_argument('--seed',type=int,default=42)
    parser.add_argument('--backend',choices=['auto','numpy','torch'],default='auto')
    parser.add_argument('--parameterization',choices=['family','layerwise_refinement'],default='family')
    parser.add_argument('--no-polarization-diagnostics',action='store_true')
    parser.add_argument('--execute',action='store_true')
    args = parser.parse_args(argv)
    try:
        if not args.execute:
            raise ValueError('explicit --execute required')
        if args.stage == 'revise':
            if args.previous_search is None:
                raise ValueError('revise requires --previous-search')
            result = revise(args.previous_search,output=args.output,planner=planner)
        else:
            if any(x is None for x in (args.cache_dir,args.max_forward_evaluations,args.max_new_evals)):
                raise ValueError('explicit cache, search-row budget, and certification budget required')
            paths = {'routes':args.routes_file,'sheet':args.task_sheet,'evidence':args.evidence_file,
                     'constraints':args.constraints_file,'optical_media':args.optical_media,'wavelengths_nm':args.wavelengths}
            if args.stage == 'search' and any(p is None for p in paths.values()):
                raise ValueError('search requires original routes/task/evidence/constraints/media/grid')
            plans = None
            if args.stage == 'refine':
                if args.previous_search is None or (args.plans_file is None and args.revision_dir is None):
                    raise ValueError('refine requires --previous-search and --plans-file or --revision-dir')
                if args.plans_file is not None and args.revision_dir is not None:
                    raise ValueError('choose one plan source')
                if args.revision_dir is not None:
                    parent = load(args.revision_dir / 'revision_parent.json')
                    if parent['manifest_sha256'] != read_search(args.previous_search)['manifest_sha256']:
                        raise ValueError('revision belongs to a different previous search')
                    manifest = load(args.revision_dir / 'manifest.json')
                    if manifest.get('refinement_plans.json') != digest(load(args.revision_dir / 'refinement_plans.json')):
                        raise ValueError('refinement plan binding mismatch')
                    plans = load(args.revision_dir / 'refinement_plans.json')
                else:
                    plans = load(args.plans_file)
            elif args.previous_search is not None:
                raise ValueError('use refine to continue a previous search')
            result = run_search(**{k:load(p) if p else None for k,p in paths.items()},output=args.output,
                cache_dir=args.cache_dir,max_forward_evaluations=args.max_forward_evaluations,
                max_new_evals=args.max_new_evals,max_workers=args.max_workers,seed=args.seed,backend=args.backend,
                previous_search=args.previous_search,plans=plans,parameterization=args.parameterization,
                include_polarization_channels=False if args.no_polarization_diagnostics else None)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['status'] in ('search_complete','plans_recorded') else 2
    except (ValueError,TypeError,KeyError,OSError) as exc:
        print('Search stage failed: '+str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
