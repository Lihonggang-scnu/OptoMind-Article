"""Prepare existing VeriTMM simulation inputs, without running the solver.

Numerical validity, optical-constant coverage and expectation testing belong to
future execution. This adapter neither creates optimization weights nor claims
that a prepared instance passed physical checks.
"""
from copy import deepcopy
from functools import lru_cache
import hashlib
import math
from pathlib import Path

try:
    from .material_capabilities import canonical_dielectric, validate_material_capabilities
except ImportError:
    from material_capabilities import canonical_dielectric, validate_material_capabilities


def _material_registry():
    # Keep the historical finite/constant adapter free of registry imports.
    try:
        from .research_execution import _engine
    except ImportError:
        from research_execution import _engine
    _engine()
    from tmm_engine.material_registry import MaterialRegistry
    return MaterialRegistry()


def _source_signature(path):
    stat = path.stat()
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


@lru_cache(maxsize=128)
def _source_sha256(path, signature):
    """Reuse unchanged local sources across candidates, never caller hashes."""
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    if _source_signature(path) != signature:
        raise ValueError('material record source changed while hashing; repeat preparation')
    return digest.hexdigest()


def _category_bindings(instance, optical_media):
    """Bind category-layer aliases to real local records, without sampling n/k.

    A record identity and source hash do not establish spectral coverage or
    physical feasibility. Existing execution preflight remains authoritative.
    """
    constraints = instance.get('design_constraints', {})
    if not isinstance(constraints, dict):
        raise ValueError('design_constraints requires an object')
    inventory = validate_material_capabilities(constraints)
    if 'allowed_material_category' not in constraints:
        return None
    if inventory is None:
        raise ValueError('category material binding requires execution_materials inventory')
    selected = {}
    for layer in instance['layers']:
        alias = layer['material']
        if not isinstance(alias, str) or alias not in inventory:
            raise ValueError('category layer material lacks execution inventory record: ' + str(alias))
        if optical_media.get(alias) != inventory[alias]:
            raise ValueError('category optical mapping must exactly match execution inventory: ' + alias)
        selected[alias] = inventory[alias]
    registry, bindings = _material_registry(), {}
    for alias, record in selected.items():
        ref = registry.resolve(record['material'], provider=record['provider'],
                               dataset_id=record['dataset_id'])
        # RII may match a query against a page/path and echo the query as its
        # ref.name. The actual book is the chemical identity, not that echo.
        resolved_material = ref.book if ref.provider == 'rii_sqlite' else ref.name
        if ref.provider != record['provider'] or canonical_dielectric(resolved_material) != alias:
            raise ValueError('resolved material record chemical identity differs from category alias: ' + alias)
        path = Path(registry.rii_provider.db_path if ref.provider == 'rii_sqlite' else ref.filepath)
        path = path.resolve()
        bindings[alias] = {'material': resolved_material, 'provider': ref.provider,
            'dataset_id': ref.dataset_id, 'source_path': str(path),
            'source_sha256': _source_sha256(path, _source_signature(path)),
            'record_filepath': ref.filepath,
            'book': ref.book, 'page': ref.page}
    return {'schema_version': 'focused_research.material_capability_binding.v1',
        'allowed_material_category': constraints['allowed_material_category'],
        'scope': 'selected_layer_record_identity_only',
        'coverage_verified': False, 'numerical_preflight_required': True,
        'bindings': bindings}


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
    binding = _category_bindings(instance, optical_media)
    output = {
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
    if binding is not None:
        output['design_constraints'] = deepcopy(instance['design_constraints'])
        output['material_capability_binding'] = binding
    return output
