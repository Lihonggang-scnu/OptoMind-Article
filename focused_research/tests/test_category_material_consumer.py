"""Category aliases bind to real offline records, without solving spectra."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from focused_research import route_consumer as consumer
from focused_research.material_capabilities import CATEGORY
from focused_research.research_execution import _engine

_engine()
from tmm_engine.material_registry import MaterialNotFoundError, MaterialRegistry
from tmm_engine.preflight import preflight_task
from tmm_engine.task_io import simulation_task_from_dict

GRID = [450, 550, 650]


def category_instance(record=None):
    record = record or {'material': 'sio2', 'provider': 'local_csv', 'dataset_id': 'sio2'}
    return {'status': 'prepared', 'route_id': 'R1', 'instance_id': 'I1',
        'incident_medium': 'air', 'substrate': 'glass',
        'layers': [{'material': 'SiO2', 'thickness_nm': 100}],
        'design_constraints': {'allowed_materials': None,
            'allowed_material_category': CATEGORY, 'execution_materials': {'SiO2': record}},
        'objectives': [{'objective_id': 'G1', 'metric': 'mean_reflectance',
            'sense': 'maximize', 'mean_reduction': 'wavelength_integral_mean',
            'region': {'wavelength_nm': [450, 650], 'angle_deg': 0,
                'polarization': 'unpolarized'}}]}


def optical_media(instance):
    return {'air': {'constant_n': 1.0}, 'glass': {'constant_n': 1.5},
        **deepcopy(instance['design_constraints']['execution_materials'])}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    materials = tmp_path / 'materials'
    materials.mkdir()
    (materials / 'sio2.csv').write_text('wavelength_um,n,k\n0.4,1.5,0\n0.55,1.5,0\n0.7,1.5,0\n')
    database = tmp_path / 'rii.db'
    with sqlite3.connect(database) as connection:
        connection.executescript('''
            CREATE TABLE pages (pageid INTEGER, shelf TEXT, book TEXT, page TEXT,
                filepath TEXT, hasrefractive INTEGER, hasextinction INTEGER,
                rangeMin REAL, rangeMax REAL, points INTEGER);
            CREATE TABLE refractiveindex (pageid INTEGER, wave REAL, refindex REAL);
            CREATE TABLE extcoeff (pageid INTEGER, wave REAL, coeff REAL);
        ''')
        # Page 2 deliberately makes the query match a path/page for SiO2,
        # although the actual record is silver. Query echo is not identity.
        for pageid, book, page, path in [
                (1, 'SiO2', 'fixture', 'main/SiO2/fixture.yml'),
                (2, 'Ag', 'SiO2', 'main/Ag/SiO2.yml'),
                (3, 'SiO2', 'partial', 'main/SiO2/partial.yml')]:
            connection.execute('INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?)',
                (pageid, 'main', book, page, path, 1, 1, .4, .7, 3))
            for wave in [.4, .55, .7]:
                connection.execute('INSERT INTO refractiveindex VALUES (?,?,?)', (pageid, wave, 1.5))
            for wave in ([.5, .55, .6] if pageid == 3 else [.4, .55, .7]):
                connection.execute('INSERT INTO extcoeff VALUES (?,?,?)', (pageid, wave, 0.0))
    result = MaterialRegistry(materials_dir=materials, rii_db_path=database)
    monkeypatch.setattr(consumer, '_material_registry', lambda: result)
    return result


def prepare(instance, media=None):
    return consumer.prepare_tmm_inputs(instance, optical_media=media or optical_media(instance),
        wavelengths_nm=GRID)


def test_category_local_record_identity_hash_and_permission_remain_distinct(registry, monkeypatch):
    row = category_instance()
    before = deepcopy(row)
    # Preparation does not sample optical constants or run any numerical work.
    monkeypatch.setattr(registry, 'sample', lambda *a, **k: pytest.fail('unexpected n/k sampling'))
    output = prepare(row)
    audit = output['material_capability_binding']
    binding = audit['bindings']['SiO2']
    assert binding['material'] == 'sio2'
    assert binding['provider'] == 'local_csv'
    assert binding['dataset_id'] == 'sio2'
    assert binding['source_sha256'] == hashlib.sha256(Path(binding['source_path']).read_bytes()).hexdigest()
    assert output['design_constraints']['allowed_materials'] is None
    assert output['design_constraints']['allowed_material_category'] == CATEGORY
    assert audit['coverage_verified'] is False
    assert audit['numerical_preflight_required'] is True
    assert output['simulation_performed'] is False
    assert output['tmm_task']['allow_material_extrapolation'] is False
    assert row == before
    output['design_constraints']['execution_materials'].clear()
    assert row == before


def test_category_rii_identity_hash_and_actual_preflight(registry):
    row = category_instance({'material': 'SiO2', 'provider': 'rii_sqlite', 'dataset_id': '1'})
    output = prepare(row)
    binding = output['material_capability_binding']['bindings']['SiO2']
    assert binding['book'] == 'SiO2'
    assert binding['dataset_id'] == 1
    assert binding['source_sha256'] == hashlib.sha256(registry.rii_provider.db_path.read_bytes()).hexdigest()
    report = preflight_task('simulate', simulation_task_from_dict(output['tmm_task']), registry=registry)
    assert report['ok'] is True
    assert report['materials'][1]['sampled_wavelength_count'] == len(GRID)


def test_alias_cannot_select_metal_record_through_rii_page_match(registry):
    row = category_instance({'material': 'sio2', 'provider': 'rii_sqlite', 'dataset_id': 2})
    # The existing resolver really does return a misleading query echo here.
    assert registry.resolve('sio2', provider='rii_sqlite', dataset_id=2).name == 'sio2'
    with pytest.raises(ValueError, match='record chemical identity'):
        prepare(row)


def test_rii_partial_k_coverage_is_still_rejected_by_existing_preflight(registry):
    row = category_instance({'material': 'sio2', 'provider': 'rii_sqlite', 'dataset_id': 3})
    output = prepare(row)
    assert output['material_capability_binding']['coverage_verified'] is False
    report = preflight_task('simulate', simulation_task_from_dict(output['tmm_task']), registry=registry)
    assert report['ok'] is False
    assert report['status'] == 'rejected'
    assert any(failure['context'].get('stack_position') == 'layer_0' for failure in report['failures'])


@pytest.mark.parametrize('replacement', [
    {'material': 'ag', 'provider': 'local_csv', 'dataset_id': 'ag'},
    {'material': 'sio2'},
    {'material': 'sio2', 'provider': 'rii_sqlite', 'dataset_id': 1},
    {'material': 'sio2', 'provider': 'local_csv', 'dataset_id': 'sio2.csv'},
    {'constant_n': 1.5},
])
def test_optical_mapping_cannot_change_selected_execution_record(registry, replacement):
    row = category_instance()
    media = optical_media(row)
    media['SiO2'] = replacement
    with pytest.raises(ValueError, match='exactly match execution inventory'):
        prepare(row, media)


@pytest.mark.parametrize('alias', ['Ag', 'UnknownDielectric', 'sio2', 'HfO2'])
def test_category_layer_requires_supported_inventory_alias(registry, alias):
    row = category_instance()
    row['layers'][0]['material'] = alias
    media = optical_media(row)
    media[alias] = {'material': 'sio2'}
    with pytest.raises(ValueError, match='lacks execution inventory'):
        prepare(row, media)


@pytest.mark.parametrize('record', [
    {'material': 'ag', 'provider': 'local_csv', 'dataset_id': 'ag'},
    {'material': 'sio2', 'provider': 'local_csv', 'dataset_id': 'ag'},
    {'material': 'sio2', 'provider': 'invented', 'dataset_id': 'sio2'},
    {'constant_n': 1.5},
])
def test_forged_inventory_cannot_make_mapping_acceptable(registry, record):
    row = category_instance(record)
    with pytest.raises(ValueError):
        prepare(row)


def test_category_requires_inventory_but_legacy_constants_do_not_load_registry(monkeypatch):
    row = category_instance()
    row['design_constraints']['execution_materials'] = None
    media = {'air': {'constant_n': 1.0}, 'glass': {'constant_n': 1.5}, 'SiO2': {'constant_n': 1.5}}
    with pytest.raises(ValueError, match='requires execution_materials inventory'):
        prepare(row, media)
    monkeypatch.setattr(consumer, '_material_registry', lambda: pytest.fail('legacy lookup'))
    row['design_constraints'] = {'allowed_materials': ['SiO2']}
    output = prepare(row, media)
    assert 'material_capability_binding' not in output
    assert output['tmm_task']['stack']['layers'][0]['constant_n'] == 1.5


def test_missing_local_or_rii_record_fails_closed(registry):
    row = category_instance({'material': 'sio2', 'provider': 'rii_sqlite', 'dataset_id': 99})
    with pytest.raises(MaterialNotFoundError):
        prepare(row)
    row = category_instance()
    (registry.local_provider.materials_dir / 'sio2.csv').unlink()
    with pytest.raises(MaterialNotFoundError):
        prepare(row)


def test_unused_inventory_is_not_mislabeled_as_bound_or_user_permission(registry):
    row = category_instance()
    row['design_constraints']['execution_materials']['HfO2'] = {
        'material': 'hfo2', 'provider': 'local_csv', 'dataset_id': 'hfo2'}
    media = optical_media(row)
    del media['HfO2']
    output = prepare(row, media)
    assert set(output['material_capability_binding']['bindings']) == {'SiO2'}
    assert output['design_constraints']['allowed_materials'] is None
    assert 'HfO2' in output['design_constraints']['execution_materials']


def test_binding_hash_is_recomputed_from_record_source(registry):
    row = category_instance()
    row['material_capability_binding'] = {'bindings': {'SiO2': {'source_sha256': '0' * 64}}}
    first = prepare(row)['material_capability_binding']['bindings']['SiO2']['source_sha256']
    path = registry.local_provider.materials_dir / 'sio2.csv'
    path.write_text(path.read_text().replace('1.5', '1.6'))
    second = prepare(row)['material_capability_binding']['bindings']['SiO2']['source_sha256']
    assert first != second
    assert second == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('provider,dataset', [('local_csv', 'sio2'), ('rii_sqlite', 1)])
def test_source_hash_cache_reuses_reads_and_invalidates_changed_source(registry, monkeypatch, provider, dataset):
    row = category_instance({'material': 'sio2', 'provider': provider, 'dataset_id': dataset})
    path = (registry.local_provider.materials_dir / 'sio2.csv'
        if provider == 'local_csv' else registry.rii_provider.db_path).resolve()
    original_open = Path.open
    reads = []

    def counted_open(self, mode='r', *args, **kwargs):
        if self == path and mode == 'rb':
            reads.append(self)
        return original_open(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', counted_open)
    first = prepare(row)['material_capability_binding']['bindings']['SiO2']['source_sha256']
    assert len(reads) == 1
    assert prepare(row)['material_capability_binding']['bindings']['SiO2']['source_sha256'] == first
    assert len(reads) == 1
    previous = path.stat()
    if provider == 'local_csv':
        path.write_text(path.read_text().replace('1.5', '1.6'))
    else:
        with sqlite3.connect(path) as connection:
            connection.execute('UPDATE refractiveindex SET refindex=1.6 WHERE pageid=1')
    # Content changes must invalidate even if size and mtime are preserved.
    assert path.stat().st_size == previous.st_size
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert path.stat().st_mtime_ns == previous.st_mtime_ns
    second = prepare(row)['material_capability_binding']['bindings']['SiO2']['source_sha256']
    assert len(reads) == 2
    assert first != second
    assert second == hashlib.sha256(path.read_bytes()).hexdigest()


def test_source_changed_during_hash_is_not_cached(tmp_path, monkeypatch):
    path = tmp_path / 'record.csv'
    path.write_bytes(b'first')
    signature = consumer._source_signature(path)
    original_open = Path.open

    def changing_open(self, mode='r', *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if self == path and mode == 'rb':
            with original_open(self, 'wb') as output:
                output.write(b'changed source')
        return handle

    monkeypatch.setattr(Path, 'open', changing_open)
    with pytest.raises(ValueError, match='changed while hashing'):
        consumer._source_sha256(path, signature)
