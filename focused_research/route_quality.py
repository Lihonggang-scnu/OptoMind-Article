"""Offline descriptive diagnostics, never scientific validation or route selection.

Input is a contract already checked by research_routes.validate. Signatures describe
executable comparisons, not physical mechanisms. No numerical optics is evaluated.
"""
from __future__ import annotations

import json
from itertools import combinations


def _key(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _comparisons(route):
    family = route['family']
    if family is None:
        return []
    layers = family['layers']
    groups = {}
    roles = []
    for layer in layers:
        value = layer['thickness_nm']
        if isinstance(value, str):
            if value not in groups:
                groups[value] = len(groups)
            roles.append(['variable', groups[value]])
        else:
            roles.append(['fixed', value])
    instances = {row['id']: row['parameters_nm'] for row in route['instances']}
    result = []
    for expectation in route['expectations']:
        def expand(instance_id):
            params = instances[instance_id]
            return [params[layer['thickness_nm']] if isinstance(layer['thickness_nm'], str)
                    else layer['thickness_nm'] for layer in layers]
        baseline = expand(expectation['baseline_instance_id'])
        candidate = expand(expectation['candidate_instance_id'])
        result.append({'classification': 'thickness_intervention' if baseline != candidate else 'baseline_reference',
            'actual_changed_variables': [key for key in instances[expectation['baseline_instance_id']]
                if instances[expectation['baseline_instance_id']][key] != instances[expectation['candidate_instance_id']][key]],
            'physical_prediction_status': 'untested', 'metric': expectation['metric'],
            'media': [family['incident_medium'], family['substrate']],
            'materials': [layer['material'] for layer in layers],
            'parameter_roles': roles,
            'baseline_nm': baseline, 'candidate_nm': candidate,
            'layer_change_signs': [(b > a) - (b < a) for a, b in zip(baseline, candidate)]})
    return result


def quality_report(routes, candidate_report=None):
    """Keep all routes; flag shared comparisons without claiming mechanism identity."""
    signatures = {route['id']: _comparisons(route) for route in routes['routes']}
    pairs = []
    for left, right in combinations(routes['routes'], 2):
        matches = []
        for a in signatures[left['id']]:
            for b in signatures[right['id']]:
                context = ('metric', 'media', 'materials')
                if any(a[key] != b[key] for key in context):
                    continue
                exact = a['baseline_nm'] == b['baseline_nm'] and a['candidate_nm'] == b['candidate_nm']
                same_intervention = (a['parameter_roles'] == b['parameter_roles'] and
                                     a['layer_change_signs'] == b['layer_change_signs'])
                if exact or same_intervention:
                    matches.append({'metric': a['metric'], 'classification':
                        'identical_concrete_comparison' if exact else 'same_structure_intervention_variant',
                        'left_baseline_nm': a['baseline_nm'], 'left_candidate_nm': a['candidate_nm'],
                        'right_baseline_nm': b['baseline_nm'], 'right_candidate_nm': b['candidate_nm'],
                        'layer_change_signs': a['layer_change_signs']})
        if matches:
            pairs.append({'route_ids': [left['id'], right['id']],
                          'shared_comparisons': list({_key(m): m for m in matches}.values()),
                          'interpretation': 'Shared executable comparison or intervention pattern; different source labels do not establish different mechanisms. Parameter variants may still be useful.'})
    return {'schema_version': 'focused_research.route_quality.v1',
            'blocking': False, 'routes_removed': [], 'simulation_performed': False,
            'candidate_admission': candidate_report,
            'scientific_review': {'status': 'not_performed',
                'scope': 'Descriptive recipe comparisons only; no mechanism, directional expectation, optical performance or scientific diversity is verified.'},
            'comparison_signatures': signatures, 'similar_route_pairs': pairs,
            'limitations': ['No pair match is not proof of scientific diversity.',
                           'No prose keyword matching or source-quality threshold is used.']}


def contract_change_audit(before, after):
    """Record changed JSON paths, not interpretations or duplicated source text."""
    paths = []
    def visit(a, b, path):
        if type(a) is not type(b):
            paths.append(path)
        elif isinstance(a, dict):
            for key in sorted(set(a) | set(b)):
                child = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
                if key not in a or key not in b:
                    paths.append(child)
                else:
                    visit(a[key], b[key], child)
        elif isinstance(a, list):
            for i in range(max(len(a), len(b))):
                child = path + '/' + str(i)
                if i >= len(a) or i >= len(b):
                    paths.append(child)
                else:
                    visit(a[i], b[i], child)
        elif a != b:
            paths.append(path)
    visit(before, after, '')
    return {'classification': 'contract_repair_not_format_only',
            'parsed_contract_changed': bool(paths), 'changed_json_paths': paths,
            'scientific_review': 'not_performed',
            'note': 'Paths report syntactic contract differences; they do not establish scientific improvement. Original responses remain authoritative.'}
