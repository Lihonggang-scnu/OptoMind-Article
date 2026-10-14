"""Existing reversal diagnostic through the actual Article entry, offline only."""
from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import numpy as np
import pytest

from optomind_eval.diagnostic_world import reversal_family
from optomind_eval.durable_diagnostic_world import DurableSyntheticDiagnosticProvider, offline_diagnostic_provider_factory
from optomind_optics.harness.article_pipeline_integration import (
    AUTHORITY_ENVIRONMENT_VARIABLE, ArticleIntegrationError, ArticleIntegrationOptions,
    execute_article_pipeline_integration,
)
from optomind_optics.harness.problem_analyzer import OpticalProblemAnalysis
from optomind_optics.harness.science.contracts import canonical_hash
from optomind_optics.harness.science.diagnostic_design import (
    DiagnosticAction, DiagnosticConfig, DiagnosticFrontEnd, DiagnosticSearchSpace,
)
from optomind_optics.harness.science.entry_spec import DiagnosticEntrySpec


QUESTION = 'Compare the forward and reversed planar sample layer order using bounded reflectance measurements.'
WORLD = {'family_id': 'reverse', 'parameters': {'sample.d0': 87., 'sample.d1': 102., 'sample.d2': 55.}, 'seed': 9127}


def entry_spec(run_id='article-diagnostic'):
    # Exactly the existing blind reversal fixture; no new scientific case.
    analysis = OpticalProblemAnalysis(problem_id='diagnostic-order', original_request=QUESTION,
        normalized_request_english=QUESTION, primary_intent='compare', compatibility='compatible',
        compatibility_reason='Known bounded competing planar layer-order hypotheses.',
        wavelengths_nm=[(400., 900.)], angles_deg=[0., 60.], polarizations=['s'],
        target_observables=['R'], needs_method_research=False)
    cfg = DiagnosticConfig(fit_starts=2, max_nfev=130, adversarial_shortlist=1,
        adversarial_starts=1, adversarial_max_nfev=80)
    space = DiagnosticSearchSpace(thicknesses_nm=(90.,), gaps_nm=(0.,), angles_deg=(0., 60.),
        polarizations=('s',), bandwidth_nm=0., angular_spread_deg=0.)
    initial = DiagnosticAction('initial', DiagnosticFrontEnd('bare'),
        tuple(np.linspace(440., 810., 15)), session_id='initial')
    return DiagnosticEntrySpec(kind='mechanism_diagnosis', run_id=run_id, original_question=QUESTION,
        analysis=analysis, analysis_hash=canonical_hash(analysis.model_dump(mode='json')),
        objective={'kind': 'mechanism_diagnosis'}, source_refs=('existing-reversal-fixture:v1',),
        families=tuple(asdict(f) for f in (reversal_family('forward'), reversal_family('reverse', True))),
        config=asdict(cfg), search_space=asdict(space), initial_action=asdict(initial),
        provider={'provider_id': 'existing-reversal-offline', 'evidence_tier': 'V1_synthetic',
                  'channels': ('R',), 'supports_status': True, 'noise_validated': True})


def options(tmp_path, spec=None, *, resume=False):
    spec = spec or entry_spec()
    world = tmp_path / 'sealed-world.json'
    world.write_text(json.dumps(WORLD))
    return ArticleIntegrationOptions(question=spec.original_question, run_id=spec.run_id,
        work_dir=str(tmp_path / 'run'), execution_root=str(tmp_path / 'execution'),
        science_entry_spec=spec.model_dump(mode='json'), science_offline_world=str(world), resume=resume)


def forbidden(*args, **kwargs):
    raise AssertionError('explicit offline scientific entry reached a model/literature provider')


def run_entry(opts, **kwargs):
    return execute_article_pipeline_integration(opts,
        environment={AUTHORITY_ENVIRONMENT_VARIABLE: 'offline-science-entry-test-authority'},
        synthesizer_factory=forbidden, online_client_factory=forbidden, **kwargs)


def test_spec_rejects_task_objective_analysis_and_domain_substitution():
    base = entry_spec().model_dump(mode='json')
    for update, message in (
        ({'original_question': 'A different task'}, 'question'),
        ({'objective': {'kind': 'design'}}, 'objective'),
        ({'analysis_hash': '0' * 64}, 'analysis hash'),
        ({'config': {}}, 'complete frozen'),
        ({'config': {**base['config'], 'noise': {}}}, 'complete frozen DiagnosticNoise'),
        ({'initial_action': {k: v for k, v in base['initial_action'].items() if k != 'polarization'}}, 'complete frozen DiagnosticAction'),
    ):
        with pytest.raises(ValueError, match=message):
            DiagnosticEntrySpec.model_validate({**base, **update})
    analysis = {**base['analysis'], 'primary_intent': 'design'}
    with pytest.raises(ValueError, match='unsupported task'):
        DiagnosticEntrySpec.model_validate({**base, 'analysis': analysis, 'analysis_hash': canonical_hash(analysis)})
    with pytest.raises(ValueError, match='wavelength domain'):
        DiagnosticEntrySpec.model_validate({**base, 'initial_action': {**base['initial_action'], 'wavelengths_nm': [350., 500.]}})


def test_unbound_entry_rejected_before_any_provider_or_run_directory(tmp_path):
    opts = options(tmp_path)
    with pytest.raises(ArticleIntegrationError, match='question/run'):
        run_entry(opts.model_copy(update={'question': 'Unrelated objective'}))
    assert not (tmp_path / 'run').exists()
    with pytest.raises(ArticleIntegrationError, match='explicit observation provider'):
        run_entry(opts.model_copy(update={'science_offline_world': None}))
    assert not (tmp_path / 'run').exists()


def test_actual_entry_completes_without_legacy_planning_or_paid_calls(tmp_path, monkeypatch):
    result = run_entry(options(tmp_path))
    assert result.result.status == 'completed', result.result.validation_errors
    assert result.result.execution_count == 0
    assert [r.status for r in result.result.receipts] == ['completed', *['skipped'] * 5, 'completed', 'completed']
    for number, stage in enumerate(('method_research', 'strategy_planning', 'article_director', 'route_task_binding', 'experiment_planning'), 2):
        assert json.loads((tmp_path / 'run' / f'{number:02d}-{stage}.json').read_text())['status'] == 'not_applicable'
    report = json.loads((tmp_path / 'run' / 'DIAGNOSTIC_RESULT.json').read_text())
    assert report['competition']['retained_families'] == ['reverse']
    assert report['result']['candidate_roles']['research_outcome']['local_prediction_state'] == 'supported'
    assert report['method_lifecycle']['status'] == 'registered_bounded_trial'
    assert report['compute_ledger']['paid_model_calls'] == 0
    assert len(report['acquisition_index']) == 3
    assert all(max(row['prediction_sequences'].values()) < row['acquisition_sequence'] for row in report['acquisition_index'])
    handoff = json.loads((tmp_path / 'run' / 'SCIENCE_OPEN_PIPELINE_HANDOFF.json').read_text())
    assert handoff['selected_result_ref'] == {k: report['result'][k] for k in ('object_id', 'revision', 'content_hash')}
    assert handoff['source_kind'] == 'scientific_result'
    assert not handoff['legacy_optimizer_certificates']
    from optomind_optics.harness.science.repository import ScienceRepository
    repository = ScienceRepository(tmp_path / 'run' / 'science', read_only=True)
    try:
        task = next(repository.load(r) for r in repository.latest_snapshot().object_refs if r.object_id == 'article-diagnostic:task')
        assert task.question == QUESTION
        assert task.objective == {'kind': 'mechanism_diagnosis'}
        assert 'parameters' not in task.constraints
        before = repository.latest_snapshot().snapshot_id
    finally:
        repository.close()
    monkeypatch.setattr(DurableSyntheticDiagnosticProvider, 'acquire', forbidden)
    resumed = run_entry(options(tmp_path, resume=True))
    assert resumed.result.result_id == result.result.result_id
    repository = ScienceRepository(tmp_path / 'run' / 'science', read_only=True)
    try:
        assert repository.latest_snapshot().snapshot_id == before
    finally:
        repository.close()
    changed = entry_spec().model_dump(mode='json')
    changed['config']['max_nfev'] += 1
    with pytest.raises(ValueError, match='changed on resume'):
        run_entry(options(tmp_path, DiagnosticEntrySpec.model_validate(changed), resume=True))
    # A fully cached Article result must still verify the observation provider
    # and authoritative gate receipts, without a new acquisition.
    opts = options(tmp_path, resume=True)
    Path(opts.science_offline_world).write_text(json.dumps({**WORLD, 'seed': 42}))
    with pytest.raises(ValueError, match='provider changed on resume'):
        run_entry(opts)
    Path(opts.science_offline_world).write_text(json.dumps(WORLD))
    with sqlite3.connect(tmp_path / 'run' / 'science' / 'diagnostic_gate.sqlite') as db:
        db.execute('DELETE FROM jobs WHERE rowid=(SELECT MIN(rowid) FROM jobs)')
    with pytest.raises(ValueError, match='authoritative receipt'):
        run_entry(opts)
    with sqlite3.connect(tmp_path / 'run' / 'offline_evaluator' / 'diagnostic.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM acquisitions').fetchone()[0] == 3


def test_durable_synthetic_provider_restores_rng_sessions_and_receipts(tmp_path):
    spec = entry_spec()
    families, cfg, _, initial = spec.components()
    args = dict(provider_id=spec.provider.provider_id, families=families, config=cfg, sealed_world=WORLD)
    continuous = DurableSyntheticDiagnosticProvider(tmp_path / 'continuous.sqlite', **args)
    restarted = DurableSyntheticDiagnosticProvider(tmp_path / 'restart.sqlite', **args)
    try:
        for index, session in enumerate(('initial', 'diagnostic', 'independent-confirmation')):
            action = replace(initial, action_id='action-' + str(index), session_id=session)
            expected = continuous.acquire(action, request_id=str(index))
            actual = restarted.acquire(action, request_id=str(index))
            assert actual == expected
            restarted.close()
            restarted = DurableSyntheticDiagnosticProvider(tmp_path / 'restart.sqlite', **args)
            assert restarted.status(str(index)) == expected
        assert restarted.db.execute('SELECT COUNT(*) FROM acquisitions').fetchone()[0] == 3
    finally:
        continuous.close()
        restarted.close()
    with pytest.raises(ValueError, match='changed on resume'):
        DurableSyntheticDiagnosticProvider(tmp_path / 'restart.sqlite', **{**args, 'sealed_world': {**WORLD, 'seed': 42}})


def test_cli_uses_explicit_spec_and_separate_world(tmp_path):
    path = Path(__file__).parents[1] / 'scripts' / 'run_article_pipeline_integration.py'
    spec = entry_spec('diagnostic-cli')
    public = tmp_path / 'public-spec.json'; public.write_text(spec.model_dump_json())
    world = tmp_path / 'sealed.json'; world.write_text(json.dumps(WORLD))
    args = ['--question', QUESTION, '--run-id', spec.run_id, '--work-dir', str(tmp_path / 'run'),
        '--execution-root', str(tmp_path / 'execution'), '--science-entry-spec', str(public),
        '--science-offline-world', str(world)]
    # Two actual processes, with only the test authority and import path. No
    # model credentials or inherited provider configuration are passed through.
    env = {'PYTHONPATH': os.environ.get('PYTHONPATH', ''), 'PYTHONDONTWRITEBYTECODE': '1',
           AUTHORITY_ENVIRONMENT_VARIABLE: 'offline-science-entry-test-authority'}
    outputs = []
    for invocation in (args, [*args, '--resume']):
        completed = subprocess.run([sys.executable, str(path), *invocation],
            env=env, capture_output=True, text=True, timeout=120)
        assert completed.returncode == 0, completed.stderr
        outputs.append(json.loads(completed.stdout))
    assert outputs[0]['result_id'] == outputs[1]['result_id']
    assert all(row['qwen_billable_total_tokens'] == 0 for row in outputs)
    with sqlite3.connect(tmp_path / 'run' / 'offline_evaluator' / 'diagnostic.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM acquisitions').fetchone()[0] == 3
    public_saved = json.loads((tmp_path / 'run' / 'SCIENCE_ENTRY_SPEC.json').read_text())
    assert public_saved == spec.model_dump(mode='json')
    assert 'sealed_world' not in public_saved and 'parameters' not in public_saved


@pytest.mark.parametrize('interrupt_type', [KeyboardInterrupt, RuntimeError])
def test_entry_resume_recovers_provider_return_before_adapter_receipt(tmp_path, monkeypatch, interrupt_type):
    from optomind_optics.harness.science.diagnostic_research import DiagnosticExecutionPending, DiagnosticProviderAdapter
    original = DiagnosticProviderAdapter._record
    interrupted = []
    def crash_once(self, *args, **kwargs):
        if not interrupted:
            interrupted.append(True)
            raise interrupt_type('provider returned before adapter persisted receipt')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(DiagnosticProviderAdapter, '_record', crash_once)
    expected = KeyboardInterrupt if interrupt_type is KeyboardInterrupt else DiagnosticExecutionPending
    with pytest.raises(expected):
        run_entry(options(tmp_path))
    with sqlite3.connect(tmp_path / 'run' / 'offline_evaluator' / 'diagnostic.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM acquisitions').fetchone()[0] == 1
        first = db.execute('SELECT request_id,datum FROM acquisitions ORDER BY sequence LIMIT 1').fetchone()
    resumed = run_entry(options(tmp_path, resume=True))
    assert resumed.result.status == 'completed', resumed.result.validation_errors
    with sqlite3.connect(tmp_path / 'run' / 'offline_evaluator' / 'diagnostic.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM acquisitions').fetchone()[0] == 3
        assert db.execute('SELECT request_id,datum FROM acquisitions ORDER BY sequence LIMIT 1').fetchone() == first
    with sqlite3.connect(tmp_path / 'run' / 'science' / 'diagnostic_gate.sqlite') as db:
        receipts = [json.loads(row[0]) for row in db.execute('SELECT receipt FROM jobs')]
        assert len(receipts) == 3 and all(len(row['attempts']) == 1 for row in receipts)


def test_unknown_provider_outcome_stays_blocked_without_resubmit(tmp_path, monkeypatch):
    from optomind_optics.harness.science.diagnostic_research import DiagnosticExecutionPending, DiagnosticProviderAdapter
    original = DiagnosticProviderAdapter._record
    def interrupt(self, *args, **kwargs):
        raise KeyboardInterrupt('lost adapter response')
    monkeypatch.setattr(DiagnosticProviderAdapter, '_record', interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_entry(options(tmp_path))
    monkeypatch.setattr(DiagnosticProviderAdapter, '_record', original)
    original_status = DurableSyntheticDiagnosticProvider.status
    monkeypatch.setattr(DurableSyntheticDiagnosticProvider, 'status', lambda self, request_id: None)
    with pytest.raises(DiagnosticExecutionPending):
        run_entry(options(tmp_path, resume=True))
    assert not (tmp_path / 'run' / '07-execution.json').exists()
    assert not (tmp_path / 'run' / 'SCIENCE_OPEN_PIPELINE_HANDOFF.json').exists()
    with sqlite3.connect(tmp_path / 'run' / 'offline_evaluator' / 'diagnostic.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM acquisitions').fetchone()[0] == 1
    with sqlite3.connect(tmp_path / 'run' / 'science' / 'diagnostic_gate.sqlite') as db:
        receipt = json.loads(db.execute('SELECT receipt FROM jobs').fetchone()[0])
        assert receipt['state'] == 'unknown_outcome' and len(receipt['attempts']) == 1
    monkeypatch.setattr(DurableSyntheticDiagnosticProvider, 'status', original_status)
    recovered = run_entry(options(tmp_path, resume=True))
    assert recovered.result.status == 'completed'
    with sqlite3.connect(tmp_path / 'run' / 'offline_evaluator' / 'diagnostic.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM acquisitions').fetchone()[0] == 3
    with sqlite3.connect(tmp_path / 'run' / 'science' / 'diagnostic_gate.sqlite') as db:
        receipts = [json.loads(row[0]) for row in db.execute('SELECT receipt FROM jobs')]
        assert all(len(receipt['attempts']) == 1 for receipt in receipts)


@pytest.mark.parametrize('mode', ['legacy', 'shadow'])
def test_explicit_diagnosis_never_falls_back_to_legacy_release_mode(tmp_path, monkeypatch, mode):
    from types import SimpleNamespace
    from optomind_optics.harness.science import release_manifest
    import optomind_optics.harness.article_pipeline_factory as factory
    monkeypatch.setattr(release_manifest, 'ProductionReleaseBinding',
        lambda *args, **kwargs: SimpleNamespace(selection={'mode': mode}))
    for name in ('QwenTMMTaskCompiler', 'QwenTMMStrategyPlanner', 'ArticleDirector', 'QwenArticleExperimentPlanner'):
        monkeypatch.setattr(factory, name, forbidden)
    opts = options(tmp_path).model_copy(update={'release_state_root': str(tmp_path / 'release'),
        'release_runtime_versions': {'Article': 'test', 'Review': 'test', 'VeriTMM': 'test'}})
    with pytest.raises(ValueError, match='requires an active release'):
        run_entry(opts)
    assert not (tmp_path / 'run').exists()


def _legacy_factory(calls):
    from optomind_optics.harness.article_pipeline_factory import ProductionArticlePipelineFactory
    from test_science_c22_release_archive import assemble_real
    class OfflineLegacyFactory(ProductionArticlePipelineFactory):
        def assemble(self, **kwargs):
            actual = ProductionArticlePipelineFactory(request=self.request, authority=self.authority, config=self.config)
            return assemble_real(actual, calls, **kwargs)
    return OfflineLegacyFactory


def test_default_entry_initializes_memory_after_admission_before_first_consumer(tmp_path, monkeypatch):
    import test_article_pipeline_factory as legacy
    import optomind_optics.harness.article_pipeline_factory as factory_module
    original = factory_module.analyze_optical_problem
    seen = []
    def analyze(*args, **kwargs):
        assert (tmp_path / 'run' / 'REQUEST.json').is_file()
        assert (tmp_path / 'run' / 'ARTICLE_MEMORY.sqlite').is_file()
        seen.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(factory_module, 'analyze_optical_problem', analyze)
    calls = []
    opts = ArticleIntegrationOptions(question=legacy.QUESTION, run_id='run-integration',
        work_dir=str(tmp_path / 'run'), execution_root=str(tmp_path / 'execution'))
    kwargs = dict(environment={AUTHORITY_ENVIRONMENT_VARIABLE: 'default-entry-test-authority'},
        factory_type=_legacy_factory(calls), synthesizer_factory=lambda **kwargs: None)
    result = execute_article_pipeline_integration(opts, **kwargs)
    # This existing legacy fixture has no literature evidence or robustness
    # report. Keep its honest partial result rather than promote it to complete.
    assert result.result.status == 'partial', result.result.validation_errors
    assert [r.status for r in result.result.receipts] == [
        'completed', 'partial', 'completed', 'completed', 'completed',
        'completed', 'completed', 'partial']
    assert result.result.execution_count == 1 and seen == [True]
    resumed = execute_article_pipeline_integration(opts.model_copy(update={'resume': True}), **kwargs)
    assert resumed.result.result_id == result.result.result_id
    assert seen == [True] and len(calls) == 1


def test_default_entry_never_bootstraps_foreign_nonempty_directory(tmp_path):
    import test_article_pipeline_factory as legacy
    root = tmp_path / 'run'; root.mkdir()
    sentinel = root / 'foreign.txt'; sentinel.write_text('must stay untouched')
    calls = []
    opts = ArticleIntegrationOptions(question=legacy.QUESTION, run_id='run-integration',
        work_dir=str(root), execution_root=str(tmp_path / 'execution'))
    with pytest.raises(ArticleIntegrationError, match='not empty'):
        execute_article_pipeline_integration(opts,
            environment={AUTHORITY_ENVIRONMENT_VARIABLE: 'default-entry-test-authority'},
            factory_type=_legacy_factory(calls), synthesizer_factory=lambda **kwargs: None)
    assert sentinel.read_text() == 'must stay untouched'
    assert sorted(p.name for p in root.iterdir()) == ['foreign.txt']
    assert calls == []
