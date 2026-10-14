"""The existing reversal fixture through shared durable execution and Article data."""
from dataclasses import replace
import json

import numpy as np
import pytest

from optomind_eval.diagnostic_world import SyntheticDiagnosticProvider, reversal_family
from optomind_optics.harness.science.contracts import (
    DecisionContext, Observation, ResearchTask, ScientificClaim, SelectedResearchResult,
)
from optomind_optics.harness.science.diagnostic_design import (
    DiagnosticAction, DiagnosticConfig, DiagnosticFrontEnd, DiagnosticSearchSpace,
)
from optomind_optics.harness.science.diagnostic_research import (
    DiagnosticExecutionPending, DiagnosticResearchLoop,
)
from optomind_optics.harness.science.repository import ScienceRepository


class CountedProvider:
    evidence_tier = 'V1_synthetic'
    noise_validated = True
    recovery_identity = 'existing-reversal-fixture-v1'

    def __init__(self, families, config):
        self.inner = SyntheticDiagnosticProvider(family=families[1],
            parameters={'sample.d0': 87., 'sample.d1': 102., 'sample.d2': 55.}, config=config)
        self.calls = 0

    def acquire(self, action, *, request_id):
        self.calls += 1
        return self.inner.acquire(action, request_id=request_id)


def fixture():
    families = (reversal_family('forward'), reversal_family('reverse', True))
    config = DiagnosticConfig(fit_starts=2, max_nfev=130, adversarial_shortlist=1,
        adversarial_starts=1, adversarial_max_nfev=80)
    space = DiagnosticSearchSpace(thicknesses_nm=(90.,), gaps_nm=(0.,), angles_deg=(0., 60.),
        polarizations=('s',), bandwidth_nm=0., angular_spread_deg=0.)
    action = DiagnosticAction('initial', DiagnosticFrontEnd('bare'),
        tuple(np.linspace(440., 810., 15)), session_id='initial')
    return families, config, space, action


def make_loop(root, provider, task=None):
    families, config, space, _ = fixture()
    repository = ScienceRepository(root / 'science')
    try:
        loop = DiagnosticResearchLoop(families=families, config=config, search_space=space,
            repository=repository, run_id='durable-reversal', provider=provider, task=task)
        return loop, repository
    except BaseException:
        repository.close()
        raise


def test_reversal_recovers_every_gate_receipt_without_reacquisition_and_binds_article(tmp_path):
    families, config, _, action = fixture()
    provider = CountedProvider(families, config)
    task = ResearchTask(object_id='article-task', source_run_id='durable-reversal',
        question='Which declared layer-order mechanism explains this same specimen?',
        objective={'kind': 'mechanism_diagnosis'}, constraints={'scope': {'source': 'original-article-request'}})
    # A pre-existing Article task/context is authoritative and must be preserved.
    repository = ScienceRepository(tmp_path / 'science')
    repository.commit([task, DecisionContext(object_id='article-source', source_run_id=task.source_run_id)],
                      task_ref=task.ref(), evidence_cutoff='article-entry')
    repository.close()
    for acquisition in range(3):
        loop, repository = make_loop(tmp_path, provider, task)
        gate = loop.runner.gates['R']
        def crash(stage):
            if stage == 'after_provider_return':
                raise RuntimeError('interrupt after durable provider return')
        gate.fault_hook = crash
        try:
            with pytest.raises(DiagnosticExecutionPending, match='unknown_outcome'):
                loop.run(action, tmp_path)
            assert provider.calls == acquisition + 1
            unknown = [json.loads(row[0]) for row in gate.db.execute('SELECT receipt FROM jobs')]
            assert sum(row['state'] == 'unknown_outcome' for row in unknown) == 1
        finally:
            loop.close(); repository.close()
    # A fresh provider may not be used for any completed acquisition on restart.
    fresh = CountedProvider(families, config)
    loop, repository = make_loop(tmp_path, fresh, task)
    try:
        report = loop.run(action, tmp_path)
        assert fresh.calls == 0
        assert report['competition']['selected_family'] == 'reverse'
        assert report['result']['candidate_roles']['research_outcome']['mechanism_state'] == 'distinguished'
        assert len(report['acquisition_index']) == 3
        assert report['measurement_cost'] == pytest.approx(loop.runner.gates['R'].usage()['cost'])
        assert all(max(row['prediction_sequences'].values()) < row['acquisition_sequence']
                   for row in report['acquisition_index'])
        result = SelectedResearchResult.model_validate(report['result'])
        assert repository.load(task.ref()) == task
        claims = [repository.load(ref) for ref in result.claim_refs]
        assert claims and all(isinstance(claim, ScientificClaim) for claim in claims)
        assert all(claim.scope['task_ref'] == task.ref().model_dump(mode='json') for claim in claims)
        assert all(any(ref in claim.evidence_refs or ref == claim.ref() for claim in claims)
                   for ref in result.negative_result_refs)
        objects = [repository.load(ref) for ref in repository.latest_snapshot().object_refs]
        assert sum(isinstance(obj, Observation) for obj in objects) == 3
        assert all(isinstance(repository.load(ref), (Observation,)) or ref in loop.confirmation_refs
                   for ref in result.asset_refs)
        from optomind_optics.harness.science.authoring_source import prepare_scientific_authoring_source
        source = prepare_scientific_authoring_source(repository.root, result.ref(), tmp_path / 'authoring-source')
        assert source.selected_result_ref == result.ref()
        snapshot = repository.latest_snapshot()
        usage = loop.compute_ledger.snapshot()['usage']
        again = loop.run(action, tmp_path)
        assert again['result'] == report['result']
        assert repository.latest_snapshot() == snapshot
        assert loop.compute_ledger.snapshot()['usage'] == usage
        assert fresh.calls == 0
    finally:
        loop.close(); repository.close()


def test_ambiguous_provider_without_status_keeps_reservation_and_never_resubmits(tmp_path):
    families, config, _, action = fixture()
    class Ambiguous(CountedProvider):
        def acquire(self, action, *, request_id):
            super().acquire(action, request_id=request_id)
            raise RuntimeError('provider connection lost after effect')
    provider = Ambiguous(families, config)
    loop, repository = make_loop(tmp_path, provider)
    try:
        with pytest.raises(DiagnosticExecutionPending, match='unknown_outcome'):
            loop.run(action, tmp_path)
        assert provider.calls == 1
    finally:
        loop.close(); repository.close()
    loop, repository = make_loop(tmp_path, provider)
    try:
        with pytest.raises(DiagnosticExecutionPending, match='unknown_outcome'):
            loop.run(action, tmp_path)
        assert provider.calls == 1
        assert loop.runner.gates['R'].usage() == {'calls': 1., 'cost': action.cost}
        assert not any(isinstance(repository.load(ref), Observation)
                       for ref in repository.latest_snapshot().object_refs)
    finally:
        loop.close(); repository.close()


@pytest.mark.parametrize('changed', ['task', 'family', 'config', 'provider', 'initial_action'])
def test_recovery_rejects_changed_frozen_identity_before_acquisition(tmp_path, changed):
    families, config, space, action = fixture()
    provider = CountedProvider(families, config)
    task = ResearchTask(object_id='article-task', source_run_id='durable-reversal',
        question='Original question', objective={'kind': 'mechanism_diagnosis'})
    loop, repository = make_loop(tmp_path, provider, task)
    loop._context('initial-action', {'action': __import__('dataclasses').asdict(action)})
    loop.close(); repository.close()
    if changed == 'task':
        task = ResearchTask(**{**task.model_dump(exclude={'content_hash'}), 'question': 'Replacement question'})
    elif changed == 'family':
        families = (reversal_family('forward', thickness_scale=1.1), families[1])
    elif changed == 'config':
        config = replace(config, seed=config.seed + 1)
    elif changed == 'provider':
        provider.recovery_identity = 'different-provider-session'
    elif changed == 'initial_action':
        action = replace(action, angle_deg=15.)
    repository = ScienceRepository(tmp_path / 'science')
    loop = None
    try:
        with pytest.raises(ValueError, match='identity changed'):
            loop = DiagnosticResearchLoop(families=families, config=config, search_space=space,
                repository=repository, run_id='durable-reversal', provider=provider, task=task)
            loop.run(action, tmp_path)
        assert provider.calls == 0
    finally:
        if loop:
            loop.close()
        repository.close()


@pytest.mark.parametrize('kind', ['slots', 'frozen'])
def test_anonymous_immutable_provider_uses_external_instance_identity(tmp_path, kind):
    from dataclasses import dataclass
    class SlotProvider:
        __slots__ = ()
        def acquire(self, action, *, request_id):
            raise AssertionError('identity-only test must not acquire')
    @dataclass(frozen=True)
    class FrozenProvider:
        def acquire(self, action, *, request_id):
            raise AssertionError('identity-only test must not acquire')
    provider_type = SlotProvider if kind == 'slots' else FrozenProvider
    provider = provider_type()
    loop, repository = make_loop(tmp_path, provider)
    first = loop.provider_identity
    loop.close(); repository.close()
    loop, repository = make_loop(tmp_path, provider)
    assert loop.provider_identity == first
    loop.close(); repository.close()
    with pytest.raises(ValueError, match='identity changed'):
        make_loop(tmp_path, provider_type())


def test_compute_bundle_rejection_is_atomic_and_failed_work_stays_consumed(tmp_path):
    from optomind_optics.harness.science.compute_ledger import ComputeBudgetExceeded, ComputeLedger
    ledger = ComputeLedger(tmp_path / 'compute.sqlite', {'predictions': 3, 'points': 6})
    try:
        events = ledger.reserve_many({'predictions': 1, 'points': 6})
        assert list(events) == ['predictions', 'points']
        for event in events.values():
            ledger.settle(event, 'failed_after_work_started')
        before = ledger.snapshot()
        for _ in range(8):
            with pytest.raises(ComputeBudgetExceeded, match='points'):
                ledger.reserve_many({'predictions': 1, 'points': 1})
        assert ledger.snapshot() == before
        assert before['usage'] == {'points': 6, 'predictions': 1}
        assert before['events_by_status'] == {'failed_consumed': 2}
        event = ledger.reserve('predictions')
        assert isinstance(event, int)
        assert ledger.snapshot()['usage']['predictions'] == 2
    finally:
        ledger.close()


def test_compute_bundle_storage_failure_rolls_back_every_resource(tmp_path):
    import sqlite3
    from optomind_optics.harness.science.compute_ledger import ComputeLedger
    ledger = ComputeLedger(tmp_path / 'compute.sqlite', {'predictions': 3, 'points': 6})
    try:
        ledger.db.execute("CREATE TRIGGER fail_second BEFORE INSERT ON events WHEN NEW.resource='points' "
                          "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END")
        ledger.db.commit()
        with pytest.raises(sqlite3.IntegrityError, match='injected storage'):
            ledger.reserve_many({'predictions': 1, 'points': 3})
        assert ledger.snapshot()['usage'] == {}
        assert dict(ledger.db.execute('SELECT resource,quantity FROM totals')) == {'predictions': 0, 'points': 0}
    finally:
        ledger.close()


def test_budget_exhausted_terminal_result_replays_without_new_compute_or_effects(tmp_path):
    from optomind_optics.harness.science.diagnostic_design import DiagnosticNoise
    families, config, space, _ = fixture()
    # The existing reversal mechanism, with its declared sample values fixed,
    # makes both initial fits fit in six points; any diagnostic design exceeds it.
    fixed = []
    for family in families:
        values = {parameter.name: parameter.initial for parameter in family.model.parameters}
        nodes = tuple(replace(node, attributes=tuple((key, values[value[1:]]
            if isinstance(value, str) and value.startswith('$') else value)
            for key, value in node.attributes)) for node in family.model.nodes)
        fixed.append(replace(family, model=replace(family.model, nodes=nodes, parameters=())))
    families = tuple(fixed)
    config = replace(config, max_prediction_calls=8, max_solver_points=6, fit_starts=1,
                     noise=DiagnosticNoise(offset_std=0., gain_std=0.))
    action = DiagnosticAction('initial', DiagnosticFrontEnd('bare'), (450., 600., 750.), session_id='initial')
    provider = CountedProvider(families, config)
    provider.inner = SyntheticDiagnosticProvider(family=families[1], parameters={}, config=config)
    expected_result = expected_usage = expected_snapshot = None
    for attempt in range(10):
        repository = ScienceRepository(tmp_path / 'science')
        loop = DiagnosticResearchLoop(families=families, config=config, search_space=space,
            repository=repository, run_id='budget-terminal', provider=provider)
        try:
            if attempt == 0:
                def interrupt_terminal_commit(stage):
                    if stage == 'after_commit' and repository.latest_snapshot().evidence_cutoff.endswith(':terminal-state'):
                        raise RuntimeError('interrupt after terminal checkpoint before selected result')
                repository.fault_hook = interrupt_terminal_commit
                with pytest.raises(RuntimeError, match='terminal checkpoint'):
                    loop.run(action, tmp_path)
                assert provider.calls == 1
                continue
            report = loop.run(action, tmp_path)
            usage = loop.compute_ledger.snapshot()['usage']
            snapshot = repository.latest_snapshot()
            assert usage == {'diagnostic_predictions': 2, 'diagnostic_solver_points': 6}
            assert report['result']['candidate_roles']['research_outcome']['unknown_exploration_state'] == 'budget_exhausted'
            if expected_result is None:
                expected_result, expected_usage, expected_snapshot = report['result'], usage, snapshot
            else:
                assert report['result'] == expected_result
                assert usage == expected_usage and snapshot == expected_snapshot
            assert provider.calls == 1
        finally:
            loop.close(); repository.close()


@pytest.mark.parametrize('fault_site', ['provider', 'adapter_record'])
def test_connection_loss_recovers_provider_status_without_reacquiring(tmp_path, fault_site):
    families, config, _, action = fixture()
    class Recoverable(CountedProvider):
        def __init__(self, families, config):
            super().__init__(families, config)
            self.receipts = {}
            self.status_calls = 0
        def acquire(self, action, *, request_id):
            datum = super().acquire(action, request_id=request_id)
            self.receipts[request_id] = datum
            if self.calls == 1 and fault_site == 'provider':
                raise RuntimeError('connection lost after durable provider effect')
            return datum
        def status(self, request_id):
            self.status_calls += 1
            return self.receipts.get(request_id)
    provider = Recoverable(families, config)
    loop, repository = make_loop(tmp_path, provider)
    if fault_site == 'adapter_record':
        def record_lost(key, binding, datum):
            raise RuntimeError('connection lost before adapter record commit')
        loop.provider_adapter._record = record_lost
    try:
        with pytest.raises(DiagnosticExecutionPending, match='unknown_outcome') as error:
            loop.run(action, tmp_path)
        assert isinstance(error.value.__cause__, RuntimeError)
        assert provider.calls == 1
        assert loop.runner.gates['R'].usage() == {'calls': 1., 'cost': action.cost}
    finally:
        loop.close(); repository.close()
    loop, repository = make_loop(tmp_path, provider)
    try:
        datum, verdicts = loop._acquire(action, stage='initial')
        assert provider.calls == 1 and provider.status_calls == 1
        assert datum.action == action and verdicts['prior']['state'] == 'supported'
        assert loop.runner.gates['R'].usage() == {'calls': 1., 'cost': action.cost}
    finally:
        loop.close(); repository.close()


def test_pre_dispatch_error_is_not_relabelled_as_pending_effect(tmp_path):
    families, config, _, action = fixture()
    provider = CountedProvider(families, config)
    loop, repository = make_loop(tmp_path, provider)
    def before_submit(stage):
        if stage == 'before_submit':
            raise RuntimeError('pre-dispatch execution bug')
    loop.runner.gates['R'].fault_hook = before_submit
    try:
        with pytest.raises(RuntimeError, match='pre-dispatch execution bug') as error:
            loop.run(action, tmp_path)
        assert not isinstance(error.value, DiagnosticExecutionPending)
        assert provider.calls == 0
    finally:
        loop.close(); repository.close()


@pytest.mark.parametrize('terminal_state', ['failed', 'cancelled'])
def test_definitive_gate_failure_is_terminal_execution_error_not_scientific_result(tmp_path, terminal_state):
    families, config, _, action = fixture()
    provider = CountedProvider(families, config)
    loop, repository = make_loop(tmp_path, provider)
    submitted = []
    def terminal_submit(key, payload):
        submitted.append(key)
        return {'state': terminal_state, 'cost': {'calls': 0., 'cost': 0.}}
    loop.provider_adapter.submit = terminal_submit
    try:
        for _ in range(2):
            with pytest.raises(RuntimeError, match='acquisition execution ' + terminal_state) as error:
                loop.run(action, tmp_path)
            assert not isinstance(error.value, DiagnosticExecutionPending)
        assert len(submitted) == 1 and provider.calls == 0
        assert loop.runner.gates['R'].receipt('durable-reversal:initial:1:measure').state == terminal_state
        assert not any(isinstance(repository.load(ref), SelectedResearchResult)
                       for ref in repository.latest_snapshot().object_refs)
    finally:
        loop.close(); repository.close()
