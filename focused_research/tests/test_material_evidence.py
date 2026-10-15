"""Offline native Review-export fixtures: no parsing, provider, or model calls."""
from copy import deepcopy
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from material_evidence import (import_material_export, select_material_evidence,
                              merge_evidence_items, material_block_index)


def export(snapshot='S1'):
    return {'schema_version': 'review_material_export_v1', 'verified': True,
            'review_commit': 'd2deb4dbe18b23837d949822ad731afbaba9414b',
            'snapshot_path': '/prepared/' + snapshot,
            'source_record': {'paperId': 'P1', 'title': 'Paper'},
            'material': {'snapshot_id': snapshot,
                'manifest': {'snapshot_id': snapshot, 'source_documents': [
                    {'source_document_id': 'D1', 'raw_sha256': 'a' * 64, 'raw_path': 'sources/original.pdf'}],
                    'known_gaps': [{'kind': 'formula', 'status': 'unenhanced'}],
                    'identity_check': {'status': 'provisional'}},
                'content_depth': 'structured_partial', 'reading_policy': {'material_scope': 'structured_partial'},
                'reading_view': 'Full source retained, never default prompt input.',
                'references': [{'reference_id': 'R1', 'text': 'Original reference'}],
                'assets': [{'asset_id': 'T1', 'type': 'table', 'structure': {
                    'columns': ['value'], 'cells': [['10^{-3}']], 'footnotes': ['at 300 K']}}],
                'blocks': [{'block_id': 'B1', 'source_document_id': 'D1', 'block_type': 'table_cell',
                    'text_raw': '  10^{-3}\n m ', 'text_normalized': '10^{-3} m',
                    'locator': {'page': 2, 'bbox': [1, 2, 3, 4], 'row_index': 0, 'col_index': 1},
                    'asset_refs': ['T1'], 'section_path': ['Results']},
                    {'block_id': 'B2', 'source_document_id': 'D1', 'block_type': 'formula',
                     'text_raw': r'\eta = A/B', 'text_normalized': r'\eta = A/B',
                     'locator': {'page': 2}, 'normalization_warnings': ['unenhanced']}]}}


def test_native_export_lossless_raw_text_assets_and_provenance():
    source = export()
    result = import_material_export(source, query_ids=['Q1'], goal_ids=['G1'])
    assert result['material_exports'] == [source]
    item = result['items'][0]
    assert item['text'] == '  10^{-3}\n m '
    assert item['snippet_type'] == 'body' and item['source_material_type'] == 'table_cell'
    assert item['source_mappings'][0]['source_document']['raw_sha256'] == 'a' * 64
    assert item['material_assets'][0]['structure']['footnotes'] == ['at 300 K']
    assert item['query_ids'] == ['Q1'] and item['goal_ids'] == ['G1']
    assert item['identity_check']['status'] == 'provisional'
    assert item['relevance_status'] == 'not_assessed'
    assert result['items'][1]['material_block']['normalization_warnings'] == ['unenhanced']
    source['material']['blocks'][0]['text_raw'] = 'modified later'
    assert result['items'][0]['text'] != 'modified later'


def test_verified_boundary_missing_source_and_text_stay_explicit():
    source = export()
    source['verified'] = False
    with pytest.raises(ValueError, match='verified'):
        import_material_export(source)
    source['verified'] = True
    source['source_record'] = {}
    # Native source hashes establish a source even when publication IDs are absent.
    assert import_material_export(source)['items'][0]['source_id'] == 'sha256:' + 'a' * 64
    source['material']['manifest']['source_documents'] = []
    source['material']['blocks'][1]['text_raw'] = ''
    source['material']['blocks'][1]['text_normalized'] = ''
    items = import_material_export(source)['items']
    assert items[0]['missing_reasons'] == ['missing_source']
    assert items[1]['missing_reasons'] == ['missing_source', 'missing_original_text']
    assert not any(i['eligible_as_evidence'] for i in items)


def test_explicit_selection_is_whole_block_bounded_and_archive_free():
    payload = import_material_export(export())
    selection = [{'snapshot_id': 'S1', 'block_id': 'B1'}]
    view = select_material_evidence(payload, selection)
    assert view['items'] == payload['items'][:1] and 'material_exports' not in view
    assert select_material_evidence(payload, [])['items'] == []
    with pytest.raises(ValueError, match='explicit'):
        select_material_evidence(payload, None)
    with pytest.raises(ValueError, match='budget'):
        select_material_evidence(payload, selection, max_text_chars=1)
    with pytest.raises(ValueError, match='payload budget'):
        select_material_evidence(payload, selection, max_payload_chars=50)
    with pytest.raises(ValueError, match='unknown'):
        select_material_evidence(payload, [{'snapshot_id': 'OTHER', 'block_id': 'B1'}])
    assert material_block_index(payload)[0]['text_chars'] == len(payload['items'][0]['text'])
    assert 'text' not in material_block_index(payload)[0]


def test_snapshot_scoping_merge_and_associations():
    a = import_material_export(export(), query_ids=['Q1'], goal_ids=['G1'])
    b = import_material_export(export(), query_ids=['Q2'], goal_ids=['G2'])
    c = import_material_export(export('S2'))
    merged = merge_evidence_items({'schema_version': a['schema_version'], 'items': []}, [a, b, c])
    assert len(merged['items']) == 4 and len(merged['material_exports']) == 2
    assert merged['items'][0]['query_ids'] == ['Q1', 'Q2']
    assert len(merged['items'][0]['source_mappings']) == 2
    assert a['items'][0]['evidence_id'] != c['items'][0]['evidence_id']
    broken = deepcopy(a)
    broken['items'][0]['text'] = 'conflict'
    with pytest.raises(ValueError, match='conflicting'):
        merge_evidence_items(a, [broken])


def test_duplicates_and_snapshot_mismatch_fail_closed():
    source = export()
    source['material']['blocks'].append(deepcopy(source['material']['blocks'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        import_material_export(source)
    source = export()
    source['material']['manifest']['snapshot_id'] = 'wrong'
    with pytest.raises(ValueError, match='disagree'):
        import_material_export(source)


def test_normalized_fallback_is_labelled_and_never_synthesizes_table_text():
    source = export()
    source['material']['blocks'][0]['text_raw'] = ''
    item = import_material_export(source)['items'][0]
    assert item['text'] == '10^{-3} m'
    assert item['source_mappings'][0]['text_field'] == 'text_normalized'
    source['material']['blocks'][0]['text_normalized'] = ''
    item = import_material_export(source)['items'][0]
    assert item['text'] == '' and not item['eligible_as_evidence']
    assert item['material_assets'][0]['structure']['cells'] == [['10^{-3}']]


def test_cli_archives_all_and_emits_only_explicit_selection(tmp_path):
    import json
    from material_evidence import main
    source = tmp_path / 'export.json'
    source.write_text(json.dumps(export()), encoding='utf-8')
    selection = tmp_path / 'selection.json'
    selection.write_text(json.dumps([{'snapshot_id': 'S1', 'block_id': 'B2'}]), encoding='utf-8')
    output = tmp_path / 'out'
    assert main(['--export', str(source), '--output-dir', str(output),
                 '--selections', str(selection), '--query-id', 'Q1']) == 0
    combined = json.loads((output / 'combined_evidence_items.json').read_text())
    selected = json.loads((output / 'selected_evidence_items.json').read_text())
    assert combined['material_exports'][0] == export()
    assert len(combined['items']) == 2 and len(selected['items']) == 1
    assert selected['items'][0]['block_id'] == 'B2'
    assert 'material_exports' not in selected
    assert json.loads((output / 'material_block_index.json').read_text())[0]['block_id'] == 'B1'


def test_cli_empty_valid_and_max_five_exports(tmp_path):
    import json
    from material_evidence import main
    output = tmp_path / 'out'
    assert main(['--output-dir', str(output)]) == 0
    assert json.loads((output / 'combined_evidence_items.json').read_text())['items'] == []
    with pytest.raises(SystemExit):
        main(['--output-dir', str(output)] + ['--export', 'unused.json'] * 6)


def test_prompt_view_strips_archive_duplicate_text_and_fails_on_overflow():
    from material_evidence import prompt_evidence_view
    payload = import_material_export(export())
    view = prompt_evidence_view(payload)
    assert 'material_exports' not in view
    assert view['items'][0]['text'] == payload['items'][0]['text']
    assert 'text_raw' not in view['items'][0]['material_block']
    assert 'text_normalized' not in view['items'][0]['material_block']
    assert view['items'][0]['material_assets'] == payload['items'][0]['material_assets']
    with pytest.raises(ValueError, match='explicit smaller block selection'):
        prompt_evidence_view(payload, max_text_chars=2)
    with pytest.raises(ValueError, match='explicit smaller block selection'):
        prompt_evidence_view(payload, max_payload_chars=2)
    assert prompt_evidence_view({'schema_version': payload['schema_version'], 'items': []})['items'] == []


def test_local_formula_enhancement_and_inherited_associations_reach_prompt():
    from material_evidence import prompt_evidence_view
    source = export()
    source['source_record'].update(query_ids=['Q0'], goal_ids=['G0'])
    source['material']['blocks'][1]['locator'].update(region_id='R1', docling_self_ref='#/texts/3')
    source['local_formula_materials'] = {'schema_version': 'optomind.local_formula_materials.v1',
        'binding': {'pdf_sha256': 'b' * 64}, 'content_sha256': 'c' * 64,
        'model': {'name': 'CodeFormulaV2'}, 'regions': [
            {'region_id': 'R1', 'source_ref': '#/texts/3', 'status': 'recognized', 'latex': r'\frac{A}{B}'},
            {'region_id': 'R2', 'source_ref': '#/texts/4', 'status': 'missing', 'latex': 'unreliable'}]}
    source['region_index'] = {'regions': [{'region_id': 'R2', 'source_ref': '#/texts/4',
        'locations': [{'page': 3, 'bbox': [1, 2, 3, 4]}]}]}
    payload = import_material_export(source, query_ids=['Q1'], goal_ids=['G1'])
    enhanced, missing = payload['items'][-2:]
    assert enhanced['source_material_type'] == 'formula_enhancement'
    assert enhanced['eligible_as_evidence'] and not missing['eligible_as_evidence']
    assert enhanced['source_mappings'][0]['original_block_ids'] == ['B2']
    assert enhanced['material_block']['locator']['page'] == 2
    assert missing['material_block']['locator']['native_region']['locations'][0]['page'] == 3
    assert enhanced['query_ids'] == ['Q1', 'Q0'] and enhanced['goal_ids'] == ['G1', 'G0']
    assert payload['items'][1]['text'] == r'\eta = A/B'
    view = prompt_evidence_view(payload)
    assert view['items'][-2]['text'] == r'\frac{A}{B}'
    assert view['items'][-2]['formula_provenance']['model']['name'] == 'CodeFormulaV2'
    assert view['items'][-2]['fidelity_status'] == 'local_transcription_candidate_not_scientific_verification'
