"""Strict local-only activation for the official offline research CLI.

A library is an existing canonical repository pinned by its exact asset-version
snapshot. Preview reads/validates it without creating a task, registry, provider,
job, or model client. Actual task-bound calibration is checked at consumption.
"""
import re
import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .contracts import ObjectRef, canonical_hash
from .method_assets import MethodRegistry, PolicyPayload, compile_payload
from .method_retriever import retrieve
from .method_runtime import FrozenMethodProvider, _public_facts
from .repository import ScienceRepository


def policy_from_config(payload):
    if 'experiment_policy' not in payload:
        return None
    return PolicyPayload.model_validate(payload['experiment_policy'])


@contextmanager
def _inspection_repository(root):
    # SQLite mode=ro can still create source -wal/-shm sidecars. Inspect a
    # short-lived local database copy instead, retaining source object reads.
    # A concurrent database/WAL change is refused rather than silently ignored.
    paths=[root/'science.sqlite3',root/'science.sqlite3-wal']
    def stamps():
        return tuple((p.exists(),p.stat().st_size,p.stat().st_mtime_ns) if p.exists() else (False,0,0) for p in paths)
    before=stamps()
    with tempfile.TemporaryDirectory(prefix='optomind-method-preview-') as directory:
        temporary=Path(directory)
        for path in paths:
            if path.exists():shutil.copyfile(path,temporary/path.name)
        if stamps()!=before:raise ValueError('library changed during read-only snapshot inspection; retry from a stable snapshot')
        (temporary/'objects').symlink_to((root/'objects').resolve(),target_is_directory=True)
        repo=ScienceRepository(temporary,read_only=True)
        try:yield repo
        finally:repo.close()


def inspect_library_config(config):
    if not isinstance(config,dict):
        raise ValueError('method_library must be a local repository configuration object')
    required={'repository_root','snapshot_id','facts'}
    optional={'allow_task_validated','calibration_refs'}
    if not required.issubset(config) or set(config)-required-optional:
        raise ValueError('method_library requires exact repository_root, snapshot_id and facts fields')
    root=Path(config['repository_root']).expanduser().resolve()
    if not (root/'science.sqlite3').is_file():
        raise ValueError('method library must be an existing local canonical repository')
    if not isinstance(config['snapshot_id'],str) or re.fullmatch('[0-9a-f]{64}',config['snapshot_id']) is None:
        raise ValueError('method library snapshot_id must be an exact asset-version hash')
    if type(config.get('allow_task_validated',False)) is not bool:
        raise ValueError('bounded trial opt-in must be an explicit boolean')
    facts=_public_facts(config['facts'])
    calibration=config.get('calibration_refs',{})
    if not isinstance(calibration,dict):
        raise ValueError('calibration_refs must map exact asset hashes to ObjectRefs')
    refs={key:ObjectRef.model_validate(value) for key,value in calibration.items()}
    with _inspection_repository(root) as repo:
        registry=MethodRegistry(repo)
        snapshot=registry.snapshot()
        actual=canonical_hash([ref.model_dump(mode='json') for ref in snapshot.asset_refs])
        if actual!=config['snapshot_id']:
            raise ValueError('method library differs from its frozen asset-version snapshot')
        receipt=retrieve(registry,facts)
        for ref in snapshot.asset_refs:
            asset=repo.load(ref)
            compile_payload(asset.kind,asset.payload)
        for key,ref in refs.items():
            if key not in {r.content_hash for r in snapshot.asset_refs}:
                raise ValueError('calibration map names an asset outside the frozen library')
            repo.load(ref)
        return {'repository_root':str(root),'snapshot_id':actual,'asset_count':len(snapshot.asset_refs),
                'applicable_refs':[r.model_dump(mode='json') for r in receipt.selected],
                'bounded_trial_opt_in':config.get('allow_task_validated',False),
                'task_bound_validation':'promotion/calibration checked again on actual task construction and use',
                'mode':'local_read_only_preview','network_calls':0,'paid_calls':0}


def inspect_activation(payload):
    policy=policy_from_config(payload)
    return {'experiment_policy':policy.model_dump(mode='json') if policy is not None else None,
            'method_library':inspect_library_config(payload['method_library']) if 'method_library' in payload else None}


def library_factory(config):
    """Return an actual-task provider factory and resources the host must close."""
    frozen=json.loads(json.dumps(config,allow_nan=False))
    preview=inspect_library_config(frozen)
    resources=[]
    def create(task):
        # Recheck the pin before any canonical task commit, including between
        # preview and run. The snapshot itself excludes unrelated task receipts.
        inspect_library_config(frozen)
        repo=ScienceRepository(preview['repository_root'])
        resources.append(repo)
        snapshot=repo.latest_snapshot()
        existing={r.object_id:r for r in snapshot.object_refs} if snapshot else {}
        if task.object_id in existing:
            if existing[task.object_id]!=task.ref():
                raise ValueError('library already contains a different target task version')
            repo.load(task.ref())
        else:
            repo.commit([task],task_ref=task.ref(),evidence_cutoff='offline-method-target-before-acquisition')
        return FrozenMethodProvider(MethodRegistry(repo),task_ref=task.ref(),facts=frozen['facts'],
            allow_task_validated=frozen.get('allow_task_validated',False),
            calibration_refs={key:ObjectRef.model_validate(value) for key,value in frozen.get('calibration_refs',{}).items()})
    return create,resources


def main(argv=None):
    """Read-only manifest command for obtaining an exact CLI activation pin."""
    import argparse
    parser=argparse.ArgumentParser(description='Inspect an existing local method library without changing it.')
    parser.add_argument('--repository',required=True)
    args=parser.parse_args(argv)
    root=Path(args.repository).expanduser().resolve()
    if not (root/'science.sqlite3').is_file():
        raise ValueError('existing canonical method repository required')
    with _inspection_repository(root) as repo:
        snapshot=MethodRegistry(repo).snapshot()
        refs=[ref.model_dump(mode='json') for ref in snapshot.asset_refs]
        result={'schema_version':'offline-method-library-manifest.v1','repository_root':str(root),
                'snapshot_id':canonical_hash(refs),'asset_refs':refs,'network_calls':0,'paid_calls':0,
                'activation':'pass this exact snapshot_id in method_library config; task applicability and promotion remain required'}
    print(json.dumps(result,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
