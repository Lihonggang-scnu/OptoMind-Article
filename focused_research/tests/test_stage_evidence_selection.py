"""Stage evidence identity tests; no model, retrieval or physical calls."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'vendor/review')]
import route_stage as stage
import research_routes as rr
import route_quality


def test_generation_materializes_selected_and_preserves_archive(tmp_path, monkeypatch):
    full = {'schema_version': 'focused_evidence_items_v1', 'items': [
        {'evidence_id': 'kept', 'text': 'exact scientific content', 'source_id': 'source',
         'eligible_as_evidence': True, 'usage_restriction': 'retain me'},
        {'evidence_id': 'omitted', 'text': 'x' * 50000, 'source_id': 'other',
         'eligible_as_evidence': True}]}
    selected = {**full, 'items': [copy.deepcopy(full['items'][0])]}
    audit = {'selected_ids': ['kept'], 'omitted_ids': ['omitted']}
    paths = {}
    for name, value in [('sheet', {}), ('evidence', full), ('constraints', {})]:
        paths[name] = tmp_path / (name + '.json')
        paths[name].write_text(json.dumps(value, indent=4) + '\n')
    original = paths['evidence'].read_bytes()
    calls = []
    def generate(sheet, evidence, planner, **kwargs):
        assert evidence == full
        return {'status': 'ok', 'selected_evidence_items': copy.deepcopy(selected),
                'evidence_selection': audit,
                'research_routes': {'routes': [{'id': 'R1', 'readiness': 'ready'}]}}
    def materialize(routes, rid, sheet, evidence, **kwargs):
        calls.append(copy.deepcopy(evidence))
        return [{'evidence_ids': ['kept']}]
    monkeypatch.setattr(rr, 'generate', generate)
    monkeypatch.setattr(rr, 'materialize', materialize)
    monkeypatch.setattr(rr, 'build_prompt', lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError('stage must not pre-render the full archive')))
    monkeypatch.setattr(route_quality, 'quality_report', lambda *a, **kw: {})
    out = tmp_path / 'out'
    assert stage.main(['generate', '--task-sheet', str(paths['sheet']),
        '--evidence-file', str(paths['evidence']), '--constraints-file', str(paths['constraints']),
        '--output', str(out)], planner=object()) == 0
    assert calls == [selected]
    assert stage.load(out / 'evidence_items.json') == selected
    assert stage.load(out / 'evidence_items.full.json') == full
    assert (out / 'evidence_items.source.json').read_bytes() == original
    assert paths['evidence'].read_bytes() == original
    identity = stage.load(out / 'input_identity.json')
    assert identity['evidence_items'] == stage.fingerprint(selected)
    assert identity['evidence_items_full'] == stage.fingerprint(full)
    assert identity['evidence_selection'] == stage.fingerprint(audit)
    # This is the file downstream search must load, without selection again.
    assert stage.load(out / 'evidence_items.json')['items'][0] == full['items'][0]


def test_generation_without_selection_contract_fails_closed(tmp_path, monkeypatch):
    for name in ['sheet', 'evidence', 'constraints']:
        (tmp_path / (name + '.json')).write_text('{}')
    monkeypatch.setattr(rr, 'generate', lambda *a, **kw: {'status': 'ok',
        'research_routes': {'routes': []}})
    assert stage.main(['generate', '--task-sheet', str(tmp_path / 'sheet.json'),
        '--evidence-file', str(tmp_path / 'evidence.json'), '--constraints-file',
        str(tmp_path / 'constraints.json'), '--output', str(tmp_path / 'out')],
        planner=object()) == 2
    assert not (tmp_path / 'out/input_identity.json').exists()


def test_scientific_revision_binds_active_evidence(tmp_path, monkeypatch):
    import scientific_stage as scientific
    full = {'schema_version': 'focused_evidence_items_v1', 'items': []}
    sheet, constraints = {}, {}
    old = tmp_path / 'previous'
    old.mkdir()
    iteration = old / 'iterations' / 'one'
    iteration.mkdir(parents=True)
    bindings = {'task_sheet': scientific.digest(sheet), 'constraints': scientific.digest(constraints),
                'evidence': scientific.digest(full)}
    proposal = {'proposal_version': 'v1', 'proposal_id': 'P1', 'input_hashes': bindings}
    observation = {'goals': [{'status': 'observed', 'observation_id': 'O1'}]}
    context = [{'iteration_path': str(iteration), 'proposal': proposal, 'observation': observation}]
    for name, obj in [('physical_observations', context), ('input_bindings', bindings)]:
        (old / (name + '.json')).write_text(json.dumps(obj))
    paths = {}
    for name, obj in [('sheet', sheet), ('constraints', constraints), ('evidence', full)]:
        paths[name] = tmp_path / (name + '.json')
        paths[name].write_text(json.dumps(obj))
    selected = {**full, 'selection_marker': 'active'}
    out = tmp_path / 'revision'
    def fake_route_main(argv, **kwargs):
        out.mkdir()
        for name, obj in [('evidence_items', selected), ('evidence_selection', {'selected_ids': []}),
                          ('research_routes', {'routes': [{'id': 'R1', 'expectations': [{}]}]})]:
            (out / (name + '.json')).write_text(json.dumps(obj))
        return 0
    from types import SimpleNamespace
    actions = []
    state = SimpleNamespace(_chain=lambda *a: [{'payload': proposal},
        {'payload': {'observations': observation}}], record_next_action=lambda path, action: actions.append(action))
    monkeypatch.setattr(scientific, '_modules', lambda: (None,
        SimpleNamespace(main=fake_route_main), None, None, None, state))
    assert scientific.main(['revise', '--task-sheet', str(paths['sheet']), '--constraints-file',
        str(paths['constraints']), '--evidence-file', str(paths['evidence']), '--previous-run',
        str(old), '--output', str(out), '--execute'], planner=object()) == 0
    binding = scientific.load(out / 'revision_parent.json')['next_evidence_binding']
    assert binding['sha256'] == scientific.digest(selected)
    assert binding['path'] == str((out / 'evidence_items.json').resolve())
    assert actions[0]['proposed_edit']['evidence_binding'] == binding


def test_offline_prompt_stage_saves_same_selected_evidence(tmp_path):
    evidence = stage.load(ROOT / 'examples/research_routes_evidence_fixture.json')
    extra = copy.deepcopy(evidence['items'][0])
    extra.update(evidence_id='oversized_unselected', text='x' * 50001)
    evidence['items'].append(extra)
    source = tmp_path / 'source.json'
    source.write_text(json.dumps(evidence))
    out = tmp_path / 'prompt'
    assert stage.main(['prompt', '--task-sheet', str(ROOT / 'examples/rgb_nir_task_sheet.json'),
        '--evidence-file', str(source), '--constraints-file',
        str(ROOT / 'examples/research_routes_constraints.json'), '--output', str(out)]) == 0
    active = stage.load(out / 'evidence_items.json')
    prompt = stage.load(out / 'route_prompt.json')
    assert {i['evidence_id'] for i in active['items']} == {
        i['evidence_id'] for i in prompt['user_payload']['evidence_items']['items']}
    assert 'oversized_unselected' not in {i['evidence_id'] for i in active['items']}
    assert stage.load(out / 'evidence_items.full.json') == evidence
    assert stage.load(out / 'input_identity.json')['evidence_items'] == stage.fingerprint(active)
