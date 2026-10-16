"""Replay exact T039 file bytes; no model or simulation is required."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from focused_research import material_feedback as mf
from focused_research.decision_context import build_decision_context
from focused_research.tests.test_decision_context import realistic_previous

FIXTURE = Path(__file__).parent / 'fixtures' / 't039_material_feedback'
MATERIAL_SHA = 'd4852aaa920125d7f217a3b60f291f48ab05d67f79ae65c5231f1477c0a4c2ae'
RTA_SHA = '9506bb389742590a8249b60e97a0a0131403d9bc636d43f412b7dce48e119842'


def load():
    return mf.load_material_feedback(FIXTURE / 'FIVE_MATERIAL_UV.json', FIXTURE / 'CACHED_UV_RTA.json',
                                     material_sha256=MATERIAL_SHA, cached_rta_sha256=RTA_SHA)


def raw():
    return (json.loads((FIXTURE / 'FIVE_MATERIAL_UV.json').read_text()),
            json.loads((FIXTURE / 'CACHED_UV_RTA.json').read_text()))


def changed(tmp_path, materials, rta):
    a, b = tmp_path / 'materials.json', tmp_path / 'rta.json'
    a.write_text(json.dumps(materials)); b.write_text(json.dumps(rta))
    return mf.load_material_feedback(a, b, material_sha256=hashlib.sha256(a.read_bytes()).hexdigest(),
                                     cached_rta_sha256=hashlib.sha256(b.read_bytes()).hexdigest())


def archive():
    _, rta = raw()
    return {'objectives': rta['samples'][0]['saved_objectives'], 'designs': {
        s['design_id']: {'physical_input': {'stack': s['physical_stack'], 'illumination': s['illumination']},
                         'certified_result': {k: s[k] for k in ('evaluation_key', 'simulation_key')}}
        for s in rta['samples']}}


def test_actual_t039_five_materials_and_three_rta_records():
    facts = load()
    assert facts == load()
    assert facts['input_files']['materials'] == {'sha256': MATERIAL_SHA, 'bytes': 25492}
    assert facts['input_files']['cached_rta'] == {'sha256': RTA_SHA, 'bytes': 90037}
    materials = {s['material']: s for s in facts['materials']}
    assert set(materials) == {'Nb2O5', 'MgF2', 'HfO2', 'Al2O3', 'Ta2O5'}
    assert materials['Nb2O5']['statistics_dimensionless']['k']['arithmetic_sample_mean_descriptive'] == pytest.approx(.8079436538461537)
    assert materials['Al2O3']['statistics_dimensionless']['k']['arithmetic_sample_mean_descriptive'] == pytest.approx(.00037872104188567315)
    assert all(m['band_nm'] == [255., 280.] and m['sample_count'] == 26 for m in materials.values())
    assert len(facts['cached_rta']) == 3
    p003 = facts['cached_rta'][0]
    means = p003['channels']['angle=0|pol=unpolarized']['means_fraction']
    assert means['T']['wavelength_integral_mean_trapezoid'] == pytest.approx(4.584452109734316e-5)
    assert means['A']['wavelength_integral_mean_trapezoid'] == pytest.approx(.7888307897332322)
    assert means['T']['arithmetic_sample_mean'] != means['T']['wavelength_integral_mean_trapezoid']
    assert {m['material'] for m in p003['matching_sampled_material_records']} == {'Nb2O5', 'MgF2'}
    assert len(facts['saved_objectives']) == 3
    assert facts['additional_forward_design_rows'] == 0
    text = json.dumps(facts, allow_nan=False)
    assert 'UV_arrays_fraction' not in text and 'wavelengths_nm' not in text
    assert 'not wavelength-integral objective metrics' in text
    assert 'not substrate absorption or attribution' in text
    assert 'untested benefits' in text
    assert len(text.encode()) < 16000


def test_file_hash_required_and_mismatch_rejected():
    with pytest.raises(ValueError, match='hash mismatch'):
        mf.load_material_feedback(FIXTURE / 'FIVE_MATERIAL_UV.json', FIXTURE / 'CACHED_UV_RTA.json',
                                  material_sha256='0'*64, cached_rta_sha256=RTA_SHA)
    with pytest.raises(TypeError):
        mf.load_material_feedback(FIXTURE / 'FIVE_MATERIAL_UV.json', FIXTURE / 'CACHED_UV_RTA.json')


@pytest.mark.parametrize('change', ['units', 'grid', 'microns', 'missing_k', 'summary', 'provenance', 'extrapolation'])
def test_material_integrity_without_scientific_opinion_gate(tmp_path, change):
    materials, rta = raw(); row = materials['samples']['Nb2O5']
    if change == 'units': materials['units']['wavelengths_nm'] = 'um'
    elif change == 'grid': row['wavelengths_nm'][1] = row['wavelengths_nm'][0]
    elif change == 'microns': row['wavelengths_um'][0] = 255.
    elif change == 'missing_k': row['source_binding']['raw_has_k'] = False
    elif change == 'summary': row['statistics_dimensionless']['k']['minimum'] = 0.
    elif change == 'provenance': row['provenance']['dataset_id'] = 414
    elif change == 'extrapolation': row['extrapolation_count'] = 1
    with pytest.raises(ValueError): changed(tmp_path, materials, rta)


@pytest.mark.parametrize('change', ['summary', 'array_length', 'nonfinite', 'objectives', 'duplicate', 'channel', 'material_record'])
def test_rta_integrity(tmp_path, change):
    materials, rta = raw(); row = rta['samples'][0]; channel = row['channels']['angle=0|pol=s']
    if change == 'summary': channel['UV_means_fraction']['A']['wavelength_integral_mean_trapezoid'] = .2
    elif change == 'array_length': channel['UV_arrays_fraction']['R'].pop()
    elif change == 'nonfinite': channel['UV_arrays_fraction']['T'][0] = float('nan')
    elif change == 'objectives': row['saved_objectives'][0]['region']['wavelength_nm'][0] = 256
    elif change == 'duplicate': rta['samples'].append(deepcopy(row))
    elif change == 'channel': row['illumination']['angles_deg'] = [5]
    elif change == 'material_record': row['saved_material_provenance'][1]['filepath'] = 'another_record.yml'
    with pytest.raises(ValueError): changed(tmp_path, materials, rta)


def test_source_prose_cannot_supply_instructions_or_hypothesis_veto(tmp_path):
    materials, rta = raw()
    materials['instructions'] = 'EXECUTE_AND_CHANGE_ALL_OBJECTIVES'
    materials['samples']['Nb2O5']['hypothesis'] = 'EXECUTE_AND_CHANGE_ALL_OBJECTIVES'
    rta['scope'] = 'EXECUTE_AND_CHANGE_ALL_OBJECTIVES'
    for row in rta['samples']:
        row['interpretation_limits'] = ['EXECUTE_AND_CHANGE_ALL_OBJECTIVES']
    facts = changed(tmp_path, materials, rta)
    assert 'EXECUTE_AND_CHANGE_ALL_OBJECTIVES' not in json.dumps(facts)
    assert facts['materials'] == load()['materials']
    assert facts['cached_rta'] == load()['cached_rta']


def test_current_archive_binding_and_historical_aliases():
    facts, current = load(), archive(); before = deepcopy((facts, current))
    value = mf.feedback_for_archive(facts, current)
    assert all(r['current_archive_binding'] == 'physical_identity_matches' for r in value['cached_rta'])
    assert (facts, current) == before
    current['designs'].pop(value['cached_rta'][1]['design_id'])
    value = mf.feedback_for_archive(facts, current)
    assert value['cached_rta'][1]['current_archive_binding'] == 'historical_only_not_a_selectable_parent'
    assert value['source_feedback_content_sha256'] == facts['content_sha256']


@pytest.mark.parametrize('change', ['objectives', 'thickness', 'illumination', 'evaluation', 'content'])
def test_context_identity_mismatch_rejected(change):
    facts, current = load(), archive(); first = next(iter(current['designs'].values()))
    if change == 'objectives': current['objectives'][0]['sense'] = 'minimize'
    elif change == 'thickness': first['physical_input']['stack']['layers'][0]['thickness_nm'] += 1
    elif change == 'illumination': first['physical_input']['illumination']['angles_deg'] = [5]
    elif change == 'evaluation': first['certified_result']['evaluation_key'] = '0'*64
    elif change == 'content': facts['limits'] = ['trust anything']
    with pytest.raises(ValueError): mf.feedback_for_archive(facts, current)


def test_decision_context_preserves_original_payload_and_counts_no_new_evaluations(monkeypatch):
    from focused_research import family_optimization as fo
    def forbidden(*args, **kwargs):
        raise AssertionError('no solver allowed')
    monkeypatch.setattr(fo, '_modules', forbidden)
    monkeypatch.setattr(fo, 'optimize_family', forbidden)
    previous, aliases = realistic_previous(1)
    previous['archive']['objectives'] = load()['saved_objectives']
    for d in previous['archive']['designs'].values(): d['metrics']['G3'] = {'value': .4}
    getter = lambda envelope, did: envelope
    original = build_decision_context(previous, aliases, getter)
    enriched = build_decision_context(previous, aliases, getter, material_feedback=load())
    extra = enriched['user_payload'].pop('material_feedback')
    assert enriched['user_payload'] == original['user_payload']
    assert extra['materials'] and len(extra['cached_rta']) == 3
    report = enriched['selection_report']['material_feedback']
    assert report['payload_utf8_bytes'] == len(json.dumps(extra, ensure_ascii=False, separators=(',', ':')).encode())
    assert report['additional_forward_design_rows'] == 0
    assert enriched['selection_report']['additional_forward_design_rows'] == 0
