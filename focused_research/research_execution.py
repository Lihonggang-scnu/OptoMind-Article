"""Bounded execution of explicit route-consumer tasks using the existing VeriTMM.

No candidates, material mappings, grids, scores or extra analyses are invented.
The budget counts newly dispatched candidate evaluations (including failures),
not verifier-internal forward solves. Workbench calls are reported separately.
Workbench counters exclude direct high-precision referee solves and per-channel
internal solver operations; they are not a total physical-solver-work counter. Parallelism
is process isolation; this module makes no general speedup claim.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import multiprocessing
import platform
from pathlib import Path
import sys
import threading
import time
import uuid

_SCHEMA = 'focused-research-execution-v1'
_LOCK = threading.Lock()


def _engine():
    root = Path(__file__).resolve().parents[1] / 'veritmm'
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import tmm_engine
    if Path(tmm_engine.__file__).resolve().parent != root / 'tmm_engine':
        raise RuntimeError('a different tmm_engine is loaded; use the bundled VeriTMM')
    return tmm_engine


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    with path.open('x', encoding='utf-8') as handle:
        handle.write(_json(value))


def _identity(settings):
    engine = _engine()
    from tmm_engine.managed_execution import material_catalog_identity
    root = Path(engine.__file__).parent
    dependencies = {}
    for package in ('numpy', 'scipy', 'tmm', 'pydantic', 'torch', 'mpmath'):
        try:
            dependencies[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            dependencies[package] = None
    return {'schema': _SCHEMA, 'engine_version': engine.__version__,
            'engine_sources': _hash([(p.relative_to(root).as_posix(), _file_hash(p))
                                     for p in sorted(root.rglob('*.py'))]),
            'material_catalog': material_catalog_identity(),
            'dependencies': dependencies, 'python': platform.python_version(),
            'platform': platform.platform(), 'verifier_settings': settings}


def _read_archive(root, key):
    """Reject partial, redirected, corrupted, or stale cached artifacts."""
    index = root / 'index' / (key + '.json')
    if not index.exists():
        return None
    try:
        if index.is_symlink():
            return None
        entry = json.loads(index.read_text(encoding='utf-8'))
        archive = root / 'runs' / entry['run_directory']
        if archive.parent.resolve() != (root / 'runs').resolve() or archive.is_symlink():
            return None
        if entry['simulation_key'] != key:
            return None
        for relative, digest in entry['files'].items():
            path = archive / relative
            if path.is_symlink() or not path.resolve().is_relative_to(archive.resolve()):
                return None
            if _file_hash(path) != digest:
                return None
        from tmm_engine.run_artifacts import validate_run_artifact_integrity
        validate_run_artifact_integrity(archive)
        result = json.loads((archive / 'EXECUTION_RECORD.json').read_text(encoding='utf-8'))
        if not result['envelope'].get('ok') or not result['certificate'].get('accepted'):
            return None
        result['archive_root'] = str(archive)
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _execute(payload):
    """One isolated engine invocation, with measured workbench-call accounting."""
    task_data, settings_data, archive = payload
    _engine()
    from tmm_engine import execution
    from tmm_engine.task_io import simulation_task_from_dict
    from tmm_engine.workbench import TMMWorkbench
    archive = Path(archive)
    calls, durations = [], []
    certify_seconds = 0.0
    original_simulate = TMMWorkbench.simulate
    original_certify = execution.certify_simulation

    def simulate(self, task):
        started = time.perf_counter()
        calls.append(1)
        try:
            return original_simulate(self, task)
        finally:
            durations.append(time.perf_counter() - started)

    def certify(*args, **kwargs):
        nonlocal certify_seconds
        started = time.perf_counter()
        try:
            return original_certify(*args, **kwargs)
        finally:
            certify_seconds += time.perf_counter() - started

    started = time.perf_counter()
    TMMWorkbench.simulate, execution.certify_simulation = simulate, certify
    try:
        envelope = execution.execute_task('simulate', simulation_task_from_dict(task_data), archive,
                                         settings=execution.ExecutionSettings(**settings_data))
        def read(name):
            path = archive / name
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        record = {'envelope': envelope, 'raw_result': read('SIMULATION_RESULT.json'),
                  'certificate': read('PHYSICS_ACCEPTANCE_CERTIFICATE.json'),
                  'archive_root': str(archive)}
    except Exception as exc:
        record = {'envelope': {'ok': False, 'status': 'execution_failed',
                              'failures': [{'message': str(exc)}]},
                  'raw_result': None, 'certificate': None, 'archive_root': str(archive)}
    finally:
        TMMWorkbench.simulate, execution.certify_simulation = original_simulate, original_certify
    record['metering'] = {'new_evaluations': 1, 'cache_hits': 0,
                          'primary_forward_calls': min(1, len(calls)),
                          'verification_forward_calls': max(0, len(calls) - 1),
                          'workbench_forward_calls': len(calls),
                          'primary_forward_seconds': durations[0] if durations else 0.0,
                          'verification_seconds': max(0.0, certify_seconds - (durations[0] if durations else 0.0)),
                          'worker_wall_seconds': time.perf_counter() - started}
    archive.mkdir(parents=True, exist_ok=True)
    _write(archive / 'EXECUTION_RECORD.json', record)
    return record


def _zero_meter():
    return {key: 0 for key in ('new_evaluations', 'cache_hits', 'primary_forward_calls',
                              'verification_forward_calls', 'workbench_forward_calls',
                              'primary_forward_seconds', 'verification_seconds', 'worker_wall_seconds')}


def execute_prepared_batch(prepared_instances, *, cache_dir, max_new_evals,
                           max_workers=1, execution_settings=None):
    """Execute route_consumer.prepare_tmm_inputs envelopes, preserving input order.

    All pending unique physics tasks reserve budget before dispatch. Duplicate
    tasks share one execution. Objective changes generate a new evaluation key,
    while exact spectra/certificate reuse is allowed only under identical physics
    and verifier identities. No cached objective score is ever returned.
    """
    if type(max_new_evals) is not int or max_new_evals < 0:
        raise ValueError('max_new_evals must be a nonnegative integer')
    if type(max_workers) is not int or not 1 <= max_workers <= 32:
        raise ValueError('max_workers must be an integer from 1 to 32')
    if not isinstance(prepared_instances, (list, tuple)):
        raise ValueError('prepared_instances must be an explicit list of envelopes')
    _engine()
    from tmm_engine.execution import ExecutionSettings
    from tmm_engine.schemas import dataclass_to_dict
    from tmm_engine.task_io import simulation_task_from_dict
    settings = asdict(execution_settings or ExecutionSettings(write_plot=False))
    if settings['skip_certificate'] or settings['device'] != 'cpu' or settings['workers'] != 1:
        raise ValueError('strict CPU certificate execution with one worker per process is required')
    root = Path(cache_dir).absolute()
    # Reject symlink redirects before any write, including missing descendants.
    from tmm_engine.experiment_store import ExperimentStore
    ExperimentStore.assert_no_path_redirection(root)
    root.mkdir(parents=True, exist_ok=True)
    for directory in ('runs', 'index', 'evaluations'):
        (root / directory).mkdir(exist_ok=True)
        ExperimentStore.assert_no_path_redirection(root / directory)
    with _LOCK:
        lock = root / '.batch-lock'
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise RuntimeError('cache is busy; another batch holds its reservation lock') from exc
        try:
            started = time.perf_counter()
            identity = _identity(settings)
            rows, pending, records = [], {}, {}
            for instance in prepared_instances:
                row = {'instance_id': instance.get('instance_id') if isinstance(instance, dict) else None,
                       'route_id': instance.get('route_id') if isinstance(instance, dict) else None,
                       'status': 'invalid_input', 'physics_accepted': False, 'cache_hit': False,
                       'metering': _zero_meter()}
                rows.append(row)
                try:
                    if instance.get('status') != 'prepared_requires_material_and_numerical_preflight':
                        raise ValueError('requires an explicit prepare_tmm_inputs envelope')
                    if not instance.get('objectives'):
                        raise ValueError('explicit inherited objectives required')
                    task = dataclass_to_dict(simulation_task_from_dict(deepcopy(instance['tmm_task'])))
                    task['stack']['name'] = 'focused_candidate'  # display IDs are not physics
                    key = _hash({'identity': identity, 'task': task})
                    evaluation_key = _hash({'simulation_key': key, 'objectives': instance['objectives']})
                    row.update(simulation_key=key, evaluation_key=evaluation_key,
                               objectives=deepcopy(instance['objectives']),
                               parameters={'thicknesses_nm': [x['thickness_nm'] for x in task['stack']['layers']],
                                           'layer_materials': [x.get('material') or {'constant_n': x.get('constant_n'), 'constant_k': x.get('constant_k')} for x in task['stack']['layers']]})
                    if key not in records and key not in pending:
                        cached = _read_archive(root, key)
                        if cached is not None:
                            records[key] = (cached, True)
                        elif len(pending) < max_new_evals:
                            archive = root / 'runs' / uuid.uuid4().hex
                            pending[key] = (task, settings, str(archive))
                        else:
                            row['status'] = 'budget_exhausted'
                            continue
                    row['status'] = 'pending'
                except Exception as exc:
                    row['error'] = str(exc)
            # Reservation is durable before any worker receives a task.
            batch_id = uuid.uuid4().hex
            _write(root / 'evaluations' / (batch_id + '.reservation.json'),
                   {'schema': _SCHEMA, 'max_new_evals': max_new_evals,
                    'reserved_simulation_keys': list(pending), 'identity': identity})
            if max_workers == 1:
                executed = [_execute(payload) for payload in pending.values()]
            else:
                with ProcessPoolExecutor(max_workers=max_workers,
                                         mp_context=multiprocessing.get_context('spawn')) as pool:
                    futures = [pool.submit(_execute, payload) for payload in pending.values()]
                    executed = []
                    for future in futures:
                        try:
                            executed.append(future.result())
                        except Exception as exc:
                            executed.append({'envelope': {'ok': False, 'status': 'worker_failed', 'failures': [{'message': str(exc)}]}, 'raw_result': None, 'certificate': None, 'metering': {**_zero_meter(), 'new_evaluations': 1}, 'metering_incomplete': True})
            total = _zero_meter()
            for (key, payload), record in zip(pending.items(), executed):
                records[key] = (record, False)
                for metric in total:
                    total[metric] += record['metering'][metric]
                if record['envelope'].get('ok'):
                    archive = Path(payload[2])
                    entry = {'simulation_key': key, 'run_directory': archive.name,
                             'files': {p.relative_to(archive).as_posix(): _file_hash(p) for p in archive.rglob('*') if p.is_file()}}
                    temporary = root / 'index' / (key + '.' + uuid.uuid4().hex + '.tmp')
                    _write(temporary, entry)
                    temporary.replace(root / 'index' / (key + '.json'))
            charged = set()
            for row in rows:
                if row['status'] != 'pending':
                    output = root / 'evaluations' / (uuid.uuid4().hex + '.json')
                    _write(output, row)
                    row['full_result_ref'] = {'path': str(output), 'sha256': _file_hash(output)}
                    continue
                key = row['simulation_key']
                record, persisted_hit = records[key]
                reused = persisted_hit or key in charged
                row.update(status='completed' if record['envelope'].get('ok') else 'failed',
                           physics_accepted=bool((record.get('certificate') or {}).get('accepted') is True and record['envelope'].get('ok')),
                           cache_hit=reused, raw_result=record['raw_result'], certificate=record['certificate'],
                           envelope=record['envelope'], archive_root=record.get('archive_root'),
                           metering={**_zero_meter(), 'cache_hits': 1} if reused else record['metering'])
                if record.get('metering_incomplete'):
                    row['metering_incomplete'] = True
                if reused:
                    total['cache_hits'] += 1
                charged.add(key)
                output = root / 'evaluations' / (uuid.uuid4().hex + '.json')
                _write(output, row)
                row['full_result_ref'] = {'path': str(output), 'sha256': _file_hash(output)}
            total.update(batch_wall_seconds=time.perf_counter() - started,
                         call_count_scope='TMMWorkbench.simulate invocations only; excludes direct high-precision referee and internal channel solves',
                         reserved_new_evaluations=len(pending), max_new_evals=max_new_evals,
                         workbench_metering_complete=not any(r.get('metering_incomplete') for r in executed),
                         executor='serial' if max_workers == 1 else 'process_pool', max_workers=max_workers)
            return {'schema_version': _SCHEMA, 'results': rows, 'metering': total}
        finally:
            lock.rmdir()
