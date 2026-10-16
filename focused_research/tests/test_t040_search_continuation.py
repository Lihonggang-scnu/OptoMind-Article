"""Continuation integration with synthetic certified fixtures; no solver/model/network."""
import copy
import json
import pytest
from test_search_stage import inputs
from focused_research import search_stage as st


def synthetic_runtime(monkeypatch):
    cache, calls, jobs = {}, [], []
    def batch(envelopes, *, max_new_evals, **kwargs):
        results, new, hits = [], 0, 0
        calls.append([e['route_id'] for e in envelopes])
        for e in envelopes:
            task = e['tmm_task']; key = st.digest(st.da._physical(task))
            if key not in cache and new >= max_new_evals:
                results.append({'instance_id': e['instance_id'], 'physics_accepted': False, 'status': 'budget_exhausted'})
                continue
            hit = key in cache
            if not hit:
                grid = task['spectrum']['values_nm']
                channels = {'angle='+str(a)+'|pol='+p: {'R': [.4]*len(grid), 'T': [.5]*len(grid), 'A': [.1]*len(grid)}
                            for a in task['illumination']['angles_deg'] for p in task['illumination']['polarizations']}
                # Channel keys use the engine's compact numeric representation.
                channels = {k.replace('45.0|', '45|'): v for k,v in channels.items()}
                cache[key] = {'status':'completed', 'physics_accepted':True,
                    'certificate':{'accepted':True}, 'envelope':{'ok':True}, 'simulation_key':key,
                    'full_result_ref':{'path':'synthetic-fixture.json','sha256':'a'*64},
                    'objectives':copy.deepcopy(e['objectives']), 'physical_input':copy.deepcopy(task),
                    'parameters':{'thicknesses_nm':[l['thickness_nm'] for l in task['stack']['layers']],
                        'layer_materials':[l.get('material') or {'constant_n':l['constant_n'],'constant_k':l.get('constant_k',0)} for l in task['stack']['layers']]},
                    'raw_result':{'wavelengths_nm':grid,'channels':channels}}
                new += 1
            else:
                hits += 1
            result = copy.deepcopy(cache[key]); result.update(instance_id=e['instance_id'],route_id=e['route_id'],cache_hit=hit)
            results.append(result)
        return {'results':results,'metering':{'new_evaluations':new,'cache_hits':hits}}
    def optimize(route, seeds, *, max_forward_evaluations, **kwargs):
        jobs.append((route['id'], max_forward_evaluations))
        return {'candidates':[], 'metering':{'forward_design_rows':0,'solver_channel_rows':0,'backward_passes':0}}
    monkeypatch.setattr(st.ex, 'execute_prepared_batch', batch)
    monkeypatch.setattr(st.fo, 'optimize_family', optimize)
    return calls, jobs


def first_snapshot(tmp_path, monkeypatch):
    calls, jobs = synthetic_runtime(monkeypatch)
    data = inputs()
    st.run_search(**data, output=tmp_path/'first', cache_dir=tmp_path/'cache',
                  max_forward_evaluations=0,max_new_evals=2)
    old = st.read_search(tmp_path/'first')
    assert len(old['archive']['designs']) == 2
    calls.clear(); jobs.clear()
    return data, old, calls, jobs


def extended(data):
    data = copy.deepcopy(data)
    for i in range(2, 6):
        route = copy.deepcopy(data['routes']['routes'][0]); route['id'] = 'R'+str(i)
        # New immutable structural families, not renamed old candidates.
        route['family']['layers'] = route['family']['layers'][:2*i]
        route['instances'][0]['parameters_nm']['dH'] += i
        route['instances'][1]['parameters_nm']['dH'] += i
        data['routes']['routes'].append(route)
    return data


def test_incremental_search_preserves_all_history_skips_old_seeds_and_splits_new_jobs(tmp_path, monkeypatch):
    data, old, calls, jobs = first_snapshot(tmp_path, monkeypatch)
    source_bytes = {p.name:p.read_bytes() for p in (tmp_path/'first').glob('*.json')}
    result = st.run_search(**extended(data), previous_search=tmp_path/'first',output=tmp_path/'next',
        cache_dir=tmp_path/'cache',max_forward_evaluations=17,max_new_evals=8)
    assert result['status'] == 'search_complete', st.load(tmp_path/'next/input_failures.json')
    new = st.read_search(tmp_path/'next')
    assert all(new['archive']['designs'][k] == v for k,v in old['archive']['designs'].items())
    assert new['archive']['evaluations'][:len(old['archive']['evaluations'])] == old['archive']['evaluations']
    assert all(new['envelopes'][k] == v for k,v in old['envelopes'].items())
    assert all(d in new['diagnostics'] for d in old['diagnostics'])
    assert new['archive']['conditions'] == old['archive']['conditions']
    assert result['metering']['certification_new_evaluations'] == 8
    assert result['metering']['certification_cache_hits_total'] == 0
    assert jobs == [('R2',5),('R3',4),('R4',4),('R5',4)]
    assert not any('R1' in call for call in calls)
    audit = st.load(tmp_path/'next/continuation_audit.json')
    assert audit['skipped_route_ids'] == ['R1']
    assert len(audit['retained_seed_references']) == 2
    assert all(not r['cache_hit_claimed'] for r in audit['retained_seed_references'])
    assert {p.name:p.read_bytes() for p in (tmp_path/'first').glob('*.json')} == source_bytes


@pytest.mark.parametrize('key', ['sheet','evidence','constraints','optical_media','wavelengths_nm','family','instance'])
def test_continuation_rejects_changed_inputs_before_execution(tmp_path, monkeypatch, key):
    data, old, calls, jobs = first_snapshot(tmp_path, monkeypatch)
    changed = extended(data)
    if key == 'family': changed['routes']['routes'][0]['family']['layers'][0]['material'] = 'SiO2'
    elif key == 'instance': changed['routes']['routes'][0]['instances'][0]['parameters_nm']['dH'] += 1
    elif key == 'wavelengths_nm': changed[key] = changed[key] + [1025]
    else: changed[key] = {**changed[key], 'changed': True}
    with pytest.raises(ValueError, match='continuation'):
        st.run_search(**changed,previous_search=tmp_path/'first',output=tmp_path/'next',cache_dir=tmp_path/'cache',
                      max_forward_evaluations=12,max_new_evals=8)
    assert calls == jobs == []
    assert not (tmp_path/'next').exists()


def test_zero_new_budget_does_not_relabel_history_as_new_certification(tmp_path, monkeypatch):
    data, old, calls, jobs = first_snapshot(tmp_path, monkeypatch)
    result = st.run_search(**data,previous_search=tmp_path/'first',output=tmp_path/'next',cache_dir=tmp_path/'cache',
                          max_forward_evaluations=0,max_new_evals=0)
    assert result['status'] == 'search_complete'
    assert result['metering']['certification_new_evaluations'] == 0
    assert result['metering']['certification_cache_hits_total'] == 0
    assert result['numerical_search_jobs'] == 0
    assert st.read_search(tmp_path/'next')['archive'] == old['archive']


def test_cli_search_accepts_previous_search_and_preserves_execute_gate(tmp_path, monkeypatch):
    data, old, calls, jobs = first_snapshot(tmp_path, monkeypatch)
    args = ['search','--previous-search',str(tmp_path/'first'),'--output',str(tmp_path/'next'),
            '--cache-dir',str(tmp_path/'cache'),'--max-forward-evaluations','0','--max-new-evals','0']
    for key, option in [('routes','routes-file'),('sheet','task-sheet'),('evidence','evidence-file'),
                        ('constraints','constraints-file'),('optical_media','optical-media'),('wavelengths_nm','wavelengths')]:
        p=tmp_path/(key+'.json');p.write_text(json.dumps(data[key]));args += ['--'+option,str(p)]
    assert st.main(args) == 2
    assert st.main(args+['--execute']) == 0


def test_partial_feedback_flags_fail_before_planner(tmp_path):
    assert st.main(['revise','--execute','--previous-search',str(tmp_path/'unused'),
                    '--output',str(tmp_path/'out'),'--material-feedback',str(tmp_path/'facts.json')]) == 2
    assert not (tmp_path/'out').exists()


def test_complete_feedback_flags_forward_verified_payload_only_to_revision(tmp_path, monkeypatch):
    from focused_research import material_feedback as mf
    facts = {'fixture': 'verified_payload'}
    received = {}
    def loader(a,b,**kwargs):
        received['files'] = (a,b,kwargs)
        return facts
    def revision(previous_search, **kwargs):
        assert kwargs['material_feedback'] is facts
        received['previous'] = previous_search
        return {'status':'plans_recorded'}
    monkeypatch.setattr(mf,'load_material_feedback',loader)
    monkeypatch.setattr(st,'revise',revision)
    args = ['revise','--execute','--previous-search',str(tmp_path/'previous'),
            '--output',str(tmp_path/'revision'),'--material-feedback',str(tmp_path/'nk.json'),
            '--cached-rta-feedback',str(tmp_path/'rta.json'),
            '--material-feedback-sha256','a'*64,'--cached-rta-feedback-sha256','b'*64]
    assert st.main(args,planner=object()) == 0
    assert received['files'][2] == {'material_sha256':'a'*64,'cached_rta_sha256':'b'*64}


def test_authentic_t038_continuation_preflight_when_exports_supplied():
    import os
    from pathlib import Path
    directory = os.environ.get('T040_PREVIOUS_CONTRACT_DIR')
    raw_directory = os.environ.get('T038_ROUTE_INPUT_DIR')
    if not directory or not raw_directory:
        pytest.skip('Supply the authentic T038 refined contract subset and original route exports.')
    p,t = Path(directory),Path(raw_directory)
    manifest = st.load(p/'manifest.json')
    context = {}
    for key,filename in st.CONTEXT_FILES.items():
        source = p/filename if (p/filename).exists() else t/filename
        context[key] = st.load(source)
        assert st.digest(context[key]) == manifest[filename]
    assert st.load(p/'input_bindings.json') == {k:st.digest(v) for k,v in context.items()}
    previous = {'context':context,'accepted':st.load(p/'accepted_contract.json')}
    assert st.digest(previous['accepted']) == manifest['accepted_contract.json']
    assert [r['id'] for r in previous['accepted']['routes']] == ['R1']
    supplied = copy.deepcopy(context)
    supplied['routes'] = st.load(t/'research_routes.raw.json')
    current = st._continuation_context(previous,supplied)
    report = st.rr.compile_candidates(current['routes'],current['sheet'],current['evidence'],
                                     design_constraints=current['constraints'])['candidate_report']
    assert report['executable_route_ids'] == ['R1','R4','R5','R9','R10']
    # This is only a contract-subset check, never a claim to have loaded or
    # executed the unexported 51-design full historical archive.


def test_continuation_inherits_explicitly_disabled_polarization_diagnostics(tmp_path, monkeypatch):
    calls,jobs = synthetic_runtime(monkeypatch)
    data = inputs()
    st.run_search(**data,output=tmp_path/'first',cache_dir=tmp_path/'cache',max_forward_evaluations=0,
                  max_new_evals=2,include_polarization_channels=False)
    calls.clear()
    result=st.run_search(**data,previous_search=tmp_path/'first',output=tmp_path/'next',
                        cache_dir=tmp_path/'cache',max_forward_evaluations=0,max_new_evals=0)
    assert result['status'] == 'search_complete'
    assert result['diagnostic_polarization_channels'] == ['unpolarized']
    assert not any(calls)
