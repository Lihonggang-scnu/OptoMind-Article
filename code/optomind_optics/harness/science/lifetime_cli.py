"""Offline C22 operations: python -m optomind_optics.harness.science.lifetime_cli.

The built-in forward adapter is a measured nearest-history fixture, not a claim
of scientific benefit. Production evaluation uses LifetimeEvaluator callbacks.
"""
import argparse
import json
from pathlib import Path
from .lifetime_evaluator import LifetimeProtocol, LifetimeEvaluator, ForwardReceipt, nearest_history_forward
from .release_manifest import ReleaseManifest, ReleaseManager, resolve_runtime_identities
from .archive_planner import rebuild_and_verify, plan_archive, apply_archive, ArchivePlan
from .method_assets import MethodRegistry
from .repository import ScienceRepository
from .contracts import ObjectRef


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for action in ('preview', 'run', 'resume', 'replay', 'reconcile', 'repair'):
        p = commands.add_parser(action)
        p.add_argument('--root', required=True)
        p.add_argument('--protocol', required=True)
        if action in ('reconcile','repair'):
            p.add_argument('--operation-key', required=True)
            p.add_argument('--receipt', required=True)
            p.add_argument('--elapsed-seconds', type=float, required=True)
    commands.add_parser('release-identify', help='Resolve actual Article/Review/VeriTMM runtime source identities')
    for action in ('release-preview', 'release-activate', 'release-history', 'release-rollback'):
        p = commands.add_parser(action)
        p.add_argument('--root', required=True)
        if action in ('release-preview', 'release-activate'):
            p.add_argument('--manifest', required=True)
            p.add_argument('--bundle', required=True)
            p.add_argument('--mode', choices=('legacy','shadow','active'), required=True)
        if action in ('release-activate','release-rollback'):
            p.add_argument('--expected-sequence', required=True, type=int)
            p.add_argument('--reason', required=True)
        if action == 'release-rollback':
            p.add_argument('--sequence', required=True, type=int)
    for action in ('archive-preview', 'archive-apply', 'rebuild-index'):
        p = commands.add_parser(action)
        p.add_argument('--repository', required=True)
        if action == 'archive-preview':
            p.add_argument('--retire', action='append', default=[])
            p.add_argument('--quarantine', action='append', default=[])
        elif action == 'rebuild-index':
            p.add_argument('--historical-snapshot', action='append', default=[])
        else:
            p.add_argument('--plan', required=True)
            p.add_argument('--task-ref', required=True, help='JSON ObjectRef file')
            p.add_argument('--evidence-ref', required=True, action='append', help='JSON ObjectRef file')
            p.add_argument('--evidence-cutoff', required=True)
    args = parser.parse_args(argv)
    if args.command == 'release-identify':
        result=resolve_runtime_identities()
    elif args.command.startswith('release-'):
        manager = ReleaseManager(args.root)
        try:
            if args.command == 'release-history':
                result = manager.history()
            elif args.command == 'release-rollback':
                result = manager.rollback(args.sequence, expected_sequence=args.expected_sequence, reason=args.reason)
            else:
                manifest = ReleaseManifest.model_validate_json(Path(args.manifest).read_text())
                if args.command == 'release-preview':
                    result = manager.preview(manifest,args.bundle,args.mode)
                else:
                    result = manager.activate(manifest,args.bundle,args.mode,expected_sequence=args.expected_sequence,reason=args.reason)
        finally:
            manager.close()
    elif args.command.startswith('archive-') or args.command == 'rebuild-index':
        from contextlib import closing
        from .offline_method_config import _inspection_repository
        context = (closing(ScienceRepository(args.repository)) if args.command == 'archive-apply'
                   else _inspection_repository(Path(args.repository).resolve()))
        with context as repo:
            registry = MethodRegistry(repo)
            if args.command == 'archive-preview':
                result = plan_archive(registry, negative_transfer_ids=args.quarantine, retire_ids=args.retire).model_dump(mode='json')
            elif args.command == 'rebuild-index':
                result = rebuild_and_verify(registry,historical_snapshot_ids=args.historical_snapshot)
            else:
                result = apply_archive(registry, ArchivePlan.model_validate_json(Path(args.plan).read_text()), task_ref=ObjectRef.model_validate_json(Path(args.task_ref).read_text()), evidence_refs=[ObjectRef.model_validate_json(Path(p).read_text()) for p in args.evidence_ref], evidence_cutoff=args.evidence_cutoff).model_dump(mode='json')
    else:
        protocol = LifetimeProtocol.model_validate_json(Path(args.protocol).read_text())
        if protocol.cost_unit != 'cpu_seconds' or protocol.evidence_scope != 'offline_fixture' or protocol.learning_enabled:
            raise ValueError('built-in CLI adapter requires offline_fixture scope, cpu_seconds costs and no external learner')
        runner = LifetimeEvaluator(args.root, protocol, evaluator_id='nearest-history-offline.v1')
        try:
            if args.command == 'preview':
                result = runner.preview()
            elif args.command in ('reconcile','repair'):
                operation=runner.reconcile if args.command=='reconcile' else runner.repair_receipt
                operation(args.operation_key,ForwardReceipt.model_validate_json(Path(args.receipt).read_text()),elapsed_seconds=args.elapsed_seconds)
                result = runner.preview()
            elif args.command == 'replay':
                result = runner.replay().model_dump(mode='json')
            else:
                if args.command == 'run' and runner.db.execute('SELECT COUNT(*) FROM operations').fetchone()[0]:
                    raise ValueError('existing execution journal: use resume or replay')
                result = runner.run(nearest_history_forward).model_dump(mode='json')
        finally:
            runner.close()
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
