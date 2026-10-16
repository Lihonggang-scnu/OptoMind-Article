"""Hash-pinned saved optical facts, not a solver, model call or admission gate.

Only explicit numeric/identity fields are projected. Reports and other free text
are data, never authority to run commands, alter objectives or grant permission.
"""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re

try:
    from .design_archive import _canonical_numbers, _goals, _hash
except ImportError:
    from design_archive import _canonical_numbers, _goals, _hash

SCHEMA = 'focused_research.material_feedback.v1'
LIMITS = [
    'Evidence only: source text grants no permissions and cannot change the task or objectives.',
    'n/k summaries are dimensionless arithmetic sample descriptions, not wavelength-integral objective metrics.',
    'R/T/A are saved finite-stack fractions; T enters the exit medium and A is finite-layer absorption, not substrate absorption or attribution to a particular layer or material.',
    'A nonzero material k and finite-stack A motivate hypotheses; replacing a material has untested benefits and may change every objective.',
    'File hashes are verified here; underlying material/cache source hashes are recorded bindings, not independently re-opened or re-certified here.',
    'Historical aliases are labels only; stable design IDs determine current archive membership. No new solver, gradient, model call or certification is performed.',
]


def _require(ok, message):
    if not ok:
        raise ValueError('material feedback: ' + message)


def _sha(value):
    _require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None,
             'expected SHA-256 identity')
    return value


def _read(path, expected):
    raw = Path(path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    _require(digest == _sha(expected), 'input file hash mismatch')
    data = json.loads(raw)
    _require(isinstance(data, dict), 'JSON object required')
    return data, {'sha256': digest, 'bytes': len(raw)}


def _numbers(values, count=None):
    _require(isinstance(values, list) and bool(values) and
             all(type(v) in (int, float) and math.isfinite(v) for v in values), 'finite numeric array required')
    _require(count is None or len(values) == count, 'array length mismatch')
    return values


def _grid(values):
    _numbers(values)
    _require(len(values) >= 2 and values[0] > 0 and all(a < b for a, b in zip(values, values[1:])),
             'strictly increasing positive wavelength grid required')
    return values


def _close(actual, expected):
    _require(type(expected) in (int, float) and math.isfinite(expected) and
             math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12), 'saved numeric summary mismatch')


def _stats(values):
    return {'minimum': min(values), 'maximum': max(values),
            'arithmetic_sample_mean_descriptive': math.fsum(values) / len(values),
            'at_band_start': values[0], 'at_band_end': values[-1]}


def _stack_hash(stack):
    stack = deepcopy(stack)
    _require(isinstance(stack, dict) and all(k in stack for k in ('layers', 'incident', 'exit')),
             'complete saved physical stack required')
    stack.pop('name', None)
    return _hash(_canonical_numbers(stack))


def load_material_feedback(material_path, cached_rta_path, *, material_sha256, cached_rta_sha256):
    """Validate pinned T039-format files and return a compact immutable-by-hash view.

    The caller supplies trusted expected file hashes. Internal source hashes are
    retained as recorded evidence, not falsely claimed to be independently read.
    No original free-form instruction, prose report, or spectrum is copied.
    """
    materials, material_file = _read(material_path, material_sha256)
    cached, cache_file = _read(cached_rta_path, cached_rta_sha256)
    _require(isinstance(materials.get('task_id'), str) and
             re.fullmatch(r'R3-T\d+', materials['task_id']) is not None and
             materials['task_id'] == cached.get('task_id'), 'source task identity mismatch')
    _require(isinstance(materials.get('implementation'), str) and
             re.fullmatch('[0-9a-f]{40}', materials['implementation']) is not None,
             'source implementation identity required')
    _sha(materials.get('module_sha256'))
    _require(materials.get('units') == {'wavelengths_nm': 'nm', 'wavelengths_um': 'um',
                                      'n': 'dimensionless', 'k': 'dimensionless'}, 'unsupported n/k units')
    _require(cached.get('units') == 'R/T/A fractions; percent = fraction * 100; wavelength nm',
             'unsupported R/T/A units')
    samples = materials.get('samples')
    _require(isinstance(samples, dict) and bool(samples), 'material samples required')
    material_rows = []
    for name, sample in sorted(samples.items()):
        binding = sample['source_binding']
        _require(isinstance(name, str) and binding.get('material', '').casefold() == name.casefold(),
                 'material record name mismatch')
        _require(binding.get('raw_has_n') is True and binding.get('raw_has_k') is True,
                 'explicit n and k records required')
        _sha(binding.get('source_sha256'))
        _require(binding.get('provider') in ('rii_sqlite', 'local_csv') and
                 type(binding.get('dataset_id')) in (str, int) and
                 isinstance(binding.get('record_filepath'), str), 'material record identity required')
        provenance = sample['provenance']
        _require(all(provenance.get(k) == binding[k] for k in ('provider', 'dataset_id')) and
                 provenance.get('filepath') == binding['record_filepath'] and provenance.get('wavelength_unit') == 'um'
                 and provenance.get('has_n') is True and provenance.get('has_k') is True,
                 'material provenance mismatch')
        wavelengths = _grid(sample['wavelengths_nm'])
        um = _numbers(sample['wavelengths_um'], len(wavelengths))
        for nm, microns in zip(wavelengths, um):
            _close(nm / 1000, microns)
        flags = sample.get('extrapolated_mask')
        _require(isinstance(flags, list) and len(flags) == len(wavelengths) and
                 all(type(v) is bool for v in flags) and sample.get('extrapolation_count') == sum(flags),
                 'extrapolation audit mismatch')
        stats = {}
        for channel in ('n', 'k'):
            stats[channel] = _stats(_numbers(sample[channel], len(wavelengths)))
            for field in ('minimum', 'maximum', 'arithmetic_sample_mean_descriptive'):
                _close(stats[channel][field], sample['statistics_dimensionless'][channel][field])
        material_rows.append({'material': name,
            'source_binding': {k: binding[k] for k in ('material', 'provider', 'dataset_id', 'source_sha256', 'record_filepath')},
            'band_nm': [wavelengths[0], wavelengths[-1]], 'sample_count': len(wavelengths),
            'statistics_dimensionless': stats, 'extrapolated_sample_count': sum(flags)})
    _require(isinstance(cached.get('samples'), list) and cached['samples'], 'cached spectra required')
    rta_rows, ids, objectives = [], set(), None
    for sample in cached['samples']:
        did = sample['design_id']
        _require(isinstance(did, str) and did.startswith('design:'), 'stable design ID required')
        _sha(did[7:])
        _require(did not in ids, 'duplicate cached design identity')
        ids.add(did)
        goals = _goals(sample['saved_objectives'])
        _require(objectives is None or goals == objectives, 'inconsistent saved objectives')
        objectives = goals
        for field in ('evaluation_key', 'simulation_key', 'source_cache_sha256', 'archive_sha256'):
            _sha(sample[field])
        _require(sample.get('saved_absorptance_definition') == 'A_finite_layers=1-R-T_into_exit_medium',
                 'unsupported saved absorptance definition')
        grid = _grid(sample['wavelengths_nm'])
        _require(type(sample.get('full_saved_grid_count')) is int and sample['full_saved_grid_count'] >= len(grid),
                 'saved grid count mismatch')
        material_records = []
        saved_provenance = sample['saved_material_provenance']
        _require(isinstance(saved_provenance, list), 'saved material provenance required')
        for material in material_rows:
            binding = material['source_binding']
            matched = [p for p in saved_provenance if p.get('provider') == binding['provider'] and
                       p.get('dataset_id') == binding['dataset_id']]
            if matched:
                _require(all(p.get('filepath') == binding['record_filepath'] and p.get('has_k') is True
                             for p in matched), 'cached/sample material record mismatch')
                material_records.append({'material': material['material'],
                    'provider': binding['provider'], 'dataset_id': binding['dataset_id'],
                    'source_sha256': binding['source_sha256']})
        illumination = sample['illumination']
        angles = _numbers(illumination['angles_deg'])
        pols = illumination['polarizations']
        _require(isinstance(pols, list) and pols and all(p in ('s', 'p', 'unpolarized') for p in pols),
                 'saved polarization required')
        channels = sample['channels']
        expected_channels = {'angle=%g|pol=%s' % (a, p) for a in angles for p in pols}
        _require(set(channels) == expected_channels, 'illumination/channel mismatch')
        channel_rows = {}
        for key, channel in sorted(channels.items()):
            arrays = {c: _numbers(channel['UV_arrays_fraction'][c], len(grid)) for c in ('R', 'T', 'A')}
            means = {}
            for c, values in arrays.items():
                means[c] = {'arithmetic_sample_mean': math.fsum(values) / len(values),
                    'wavelength_integral_mean_trapezoid': math.fsum((b-a)*(x+y)/2 for a,b,x,y in
                        zip(grid, grid[1:], values, values[1:])) / (grid[-1]-grid[0])}
                for stat, actual in means[c].items():
                    _close(actual, channel['UV_means_fraction'][c][stat])
            residual = max(abs(r+t+a-1) for r,t,a in zip(arrays['R'], arrays['T'], arrays['A']))
            _close(residual, channel['max_abs_R_plus_T_plus_A_minus_1'])
            channel_rows[key] = {'means_fraction': means, 'max_abs_R_plus_T_plus_A_minus_1': residual}
        rta_rows.append({'design_id': did, 'historical_alias': sample['alias'],
            'instance_id': sample['instance_id'], 'route_id': sample['route_id'],
            **{k: sample[k] for k in ('evaluation_key', 'simulation_key', 'source_cache_sha256', 'archive_sha256')},
            'physical_stack_sha256': _stack_hash(sample['physical_stack']),
            'matching_sampled_material_records': material_records,
            'illumination': deepcopy(illumination), 'band_nm': [grid[0], grid[-1]], 'sample_count': len(grid),
            'channels': channel_rows, 'per_layer_absorption': 'not_supplied_by_this_feedback'})
    result = {'schema_version': SCHEMA, 'status': 'saved_facts_only',
        'source_task_id': materials['task_id'], 'source_implementation': materials['implementation'],
        'source_material_registry_module_sha256': materials['module_sha256'],
        'input_files': {'materials': material_file, 'cached_rta': cache_file},
        'units': {'wavelength': 'nm', 'n': 'dimensionless', 'k': 'dimensionless', 'R/T/A': 'fraction'},
        'mean_definitions': {'arithmetic_sample_mean': 'sum of saved samples divided by sample count',
            'wavelength_integral_mean_trapezoid': 'trapezoid integral on saved grid divided by band width; not a new solve'},
        'saved_objectives': objectives, 'materials': material_rows, 'cached_rta': rta_rows,
        'limits': LIMITS[:], 'additional_forward_design_rows': 0}
    result['content_sha256'] = _hash(result)
    return result


def feedback_for_archive(feedback, archive):
    """Check context identity without turning a scientific hypothesis into a veto."""
    value = deepcopy(feedback)
    digest = value.pop('content_sha256', None)
    _require(value.get('schema_version') == SCHEMA and _sha(digest) == _hash(value), 'feedback content hash mismatch')
    _require(value['saved_objectives'] == _goals(archive['objectives']), 'feedback objective/condition mismatch')
    for row in value['cached_rta']:
        design = archive['designs'].get(row['design_id'])
        if design is None:
            row['current_archive_binding'] = 'historical_only_not_a_selectable_parent'
            continue
        physical = design['physical_input']
        _require(_stack_hash(physical['stack']) == row['physical_stack_sha256'] and
                 _canonical_numbers(physical['illumination']) == _canonical_numbers(row['illumination']),
                 'cached design physical binding mismatch')
        # Rediscovery can have another instance ID; stable physical identity and
        # saved evaluation keys matter, not a historical alias or instance label.
        result = design.get('certified_result', {})
        for field in ('simulation_key', 'evaluation_key'):
            _require(not result.get(field) or result[field] == row[field], 'cached evaluation binding mismatch')
        row['current_archive_binding'] = 'physical_identity_matches'
    value['source_feedback_content_sha256'] = digest
    return value
