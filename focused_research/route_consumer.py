"""Prepare existing VeriTMM simulation inputs, without running the solver.

Numerical validity, optical-constant coverage and expectation testing belong to
future execution. This adapter neither creates optimization weights nor claims
that a prepared instance passed physical checks.
"""
from copy import deepcopy
import math


def prepare_tmm_inputs(instance, *, optical_media, wavelengths_nm):
    if instance.get('status') != 'prepared':
        raise ValueError('only a prepared route instance is accepted')
    if not isinstance(optical_media, dict):
        raise ValueError('explicit optical media mapping required')
    grid = wavelengths_nm
    if (not isinstance(grid, list) or len(grid) < 3 or
            any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in grid) or
            any(a >= b for a, b in zip(grid, grid[1:]))):
        raise ValueError('explicit positive, finite, strictly increasing wavelength grid required')
    def medium(alias):
        row = optical_media.get(alias)
        if not isinstance(row, dict) or not row:
            raise ValueError('missing explicit optical mapping for ' + str(alias))
        allowed = {'material', 'provider', 'dataset_id', 'constant_n', 'constant_k'}
        if set(row) - allowed:
            raise ValueError('unsupported optical mapping fields')
        has_material = row.get('material') is not None
        has_constant = row.get('constant_n') is not None
        if has_material == has_constant:
            raise ValueError('supply exactly one material identifier or explicit constant index')
        if has_material and (not isinstance(row['material'], str) or
                not row['material'].strip() or row['material'] != row['material'].strip()):
            raise ValueError('invalid material identifier')
        if has_constant and (type(row['constant_n']) not in (int, float) or
                not math.isfinite(row['constant_n']) or row['constant_n'] <= 0):
            raise ValueError('invalid explicit constant index')
        if 'constant_k' in row and (type(row['constant_k']) not in (int, float) or
                not math.isfinite(row['constant_k']) or row['constant_k'] < 0):
            raise ValueError('invalid extinction coefficient')
        return deepcopy(row)
    objectives = deepcopy(instance['objectives'])
    if not objectives:
        raise ValueError('explicit inherited objectives required')
    angles, polarizations = [], []
    for objective in objectives:
        region = objective['region']
        lo, hi = region['wavelength_nm']
        if lo not in grid or hi not in grid or not any(lo < x < hi for x in grid):
            raise ValueError('each objective band requires exact endpoints and interior samples')
        for target, value in [(angles, region['angle_deg']), (polarizations, region['polarization'])]:
            if value not in target:
                target.append(value)
    layers = []
    for layer in instance['layers']:
        thickness = layer['thickness_nm']
        if type(thickness) not in (int, float) or not math.isfinite(thickness) or thickness <= 0:
            raise ValueError('invalid layer thickness')
        layers.append({**medium(layer['material']), 'thickness_nm': thickness})
    if not layers:
        raise ValueError('at least one layer required')
    return {
        'status': 'prepared_requires_material_and_numerical_preflight',
        'route_id': instance['route_id'], 'instance_id': instance['instance_id'],
        'simulation_performed': False, 'objectives': objectives,
        'basis_mode': instance.get('basis_mode', 'external_evidence'),
        'model_knowledge_rationale': instance.get('model_knowledge_rationale'),
        'candidate_metadata': deepcopy(instance.get('candidate_metadata')),
        'source_confidence': deepcopy(instance.get('source_confidence', {})),
        'proposal_derivation_audit': deepcopy(instance.get('proposal_derivation_audit')),
        'clue_ids': deepcopy(instance.get('clue_ids', [])),
        'evidence_ids': deepcopy(instance.get('evidence_ids', [])),
        'targeted_goal_ids': deepcopy(instance.get('targeted_goal_ids', [])),
        'tmm_task': {
            'stack': {'name': instance['instance_id'], 'layers': layers,
                'incident': medium(instance['incident_medium']), 'exit': medium(instance['substrate'])},
            'spectrum': {'values_nm': list(grid)},
            'illumination': {'angles_deg': angles, 'polarizations': polarizations},
            'allow_material_extrapolation': False, 'requested_outputs': ['R', 'T', 'A']
        }
    }
