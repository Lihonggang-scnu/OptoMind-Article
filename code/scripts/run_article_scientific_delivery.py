#!/usr/bin/env python3
"""Preview or deliver repository-selected Article manuscripts. Default: zero paid calls."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys


def build_parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository-root',type=Path)
    p.add_argument('--selected-result-ref',type=Path,help='ObjectRef JSON for an already complete manuscript')
    p.add_argument('--manuscript',type=Path,help='Already complete Article HANDLE manuscript')
    p.add_argument('--manuscript-sha256')
    p.add_argument('--continuation-result',type=Path,help='Alternatively finish a complete accepted author BODY result')
    p.add_argument('--scientific-selection',type=Path,help='Required with --continuation-result')
    p.add_argument('--front-matter-response',type=Path,help='Request-hash-bound recorded Article front/back response')
    p.add_argument('--structure-response',type=Path,help='Request-hash-bound recorded Article full-structure responses')
    p.add_argument('--citation-namespace',required=True)
    p.add_argument('--source-identity-map',type=Path,required=True)
    p.add_argument('--material-records',type=Path)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--out-dir',type=Path,required=True)
    p.add_argument('--review-root',type=Path,help='Explicit Review repository, or OPTOMIND_REVIEW_ROOT/PYTHONPATH')
    mode=p.add_mutually_exclusive_group()
    mode.add_argument('--preview',action='store_true',help='Read-only validation, no model clients (default)')
    mode.add_argument('--replay',action='store_true',help='Execute local author response replay and checker fixtures/recordings only')
    mode.add_argument('--run',action='store_true',help='Explicit paid shared checker under dual budgets; Article front/back remains bound replay')
    p.add_argument('--resume',action='store_true',help='Require an existing output; existing immutable bindings and cached calls are reused')
    p.add_argument('--retry-failed',action='store_true',help='Explicitly allow failed shared checker attempts to retry')
    p.add_argument('--ledger',type=Path)
    p.add_argument('--budget-limit',type=float)
    p.add_argument('--account-ledger',type=Path)
    p.add_argument('--account-budget-limit',type=float)
    p.add_argument('--account-mapping-dir',type=Path)
    p.add_argument('--incremental-budget',type=float)
    p.add_argument('--key-file',type=Path,help='Local credential file, read only by an explicitly running live provider')
    p.add_argument('--key-index',type=int)
    p.add_argument('--tokenizer',type=Path,help='Existing local tokenizer only; no download')
    p.add_argument('--allow-max',action='store_true')
    return p


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _configure(review_root):
    root=Path(__file__).resolve().parents[1]
    for path in (root,root.parent/'veritmm'):
        if str(path) not in sys.path: sys.path.insert(0,str(path))
    value=review_root or os.environ.get('OPTOMIND_REVIEW_ROOT')
    if value:
        path=Path(value).resolve()
        if not (path/'optomind_research/runtime/upgrade3/article_delivery.py').is_file():
            raise ValueError('Review public Article delivery adapter is absent from --review-root')
        sys.path.append(str(path))
        from pkgutil import extend_path
        import optomind_research, optomind_research.runtime
        optomind_research.__path__=extend_path(optomind_research.__path__,optomind_research.__name__)
        optomind_research.runtime.__path__=extend_path(optomind_research.runtime.__path__,optomind_research.runtime.__name__)


def _recorded(path,kind):
    from optomind_optics.harness.science.contracts import canonical_hash
    raw=_read(path)
    def provider(request):
        if raw.get('request_sha256') != canonical_hash(request):
            raise ValueError(kind+' recorded request hash mismatch')
        if kind=='structure':
            from optomind_optics.harness.article_full_structure import FullStructureProviderResult
            return [FullStructureProviderResult.model_validate(row) for row in raw['responses']]
        from optomind_optics.harness.article_presentation import ProviderResult
        return ProviderResult.model_validate(raw['response'])
    return provider


def main(argv=None):
    args=build_parser().parse_args(argv)
    repo=None
    try:
        _configure(args.review_root)
        from optomind_optics.harness.science.contracts import ObjectRef, canonical_hash
        from optomind_optics.harness.science.repository import ScienceRepository
        from optomind_optics.harness.science.article_bridge import load_result_bundle,deliver_repository_article
        identities=_read(args.source_identity_map);materials=_read(args.material_records) if args.material_records else []
        config=_read(args.config)
        full=args.continuation_result is not None
        if full:
            if args.manuscript or not args.scientific_selection:
                raise ValueError('full author mode requires --scientific-selection and excludes --manuscript')
            from optomind_optics.harness.article_continuation import ContinuationResult,compute_continuation_result_id,load_source_pipeline
            from optomind_optics.harness.science.authoring_selection import AuthoringSelection,resolve_authoring_selection
            result=ContinuationResult.model_validate(_read(args.continuation_result))
            selection=AuthoringSelection.model_validate(_read(args.scientific_selection))
            if result.result_id!=compute_continuation_result_id(result): raise ValueError('continuation result hash mismatch')
            source=load_source_pipeline(Path(result.source_pipeline_dir))
            context=resolve_authoring_selection(selection,source.assets)
            if not result.stage_payloads.result_synthesis or result.stage_payloads.result_synthesis.scientific_selection!=context:
                raise ValueError('continuation scientific selection mismatch')
            ref=selection.selected_result_ref
            if result.stage_payloads.manuscript is None or result.stage_payloads.manuscript.errors or result.stage_payloads.manuscript.blocked_handoff:
                raise ValueError('full mode requires accepted complete BODY package')
            source_text=result.stage_payloads.manuscript.body_markdown
        else:
            if not all((args.repository_root,args.selected_result_ref,args.manuscript,args.manuscript_sha256)):
                raise ValueError('complete-input mode requires repository, selected-result-ref, manuscript and its SHA256')
            repo=ScienceRepository(args.repository_root,read_only=True)
            ref=ObjectRef.model_validate(_read(args.selected_result_ref));load_result_bundle(repo,ref)
            if hashlib.sha256(args.manuscript.read_bytes()).hexdigest()!=args.manuscript_sha256:
                raise ValueError('selected manuscript hash mismatch')
            source_text=args.manuscript.read_text(encoding='utf-8')
        from optomind_research.runtime.upgrade3.article_delivery import _catalogs
        _catalogs({'citation_namespace':args.citation_namespace,'source_identity_map':identities},config,source_text)
        if args.resume and not (args.out_dir/('COMPLETE_ARTICLE_INPUT.json' if full else 'ARTICLE_SCIENCE_DELIVERY.json')).is_file():
            raise ValueError('--resume requires an existing bound output checkpoint')
        if not args.run and not args.replay:
            print(json.dumps({'status':'preview_ready','mode':'complete_author_chain' if full else 'complete_manuscript',
                'adopted_result_ref':ref.model_dump(mode='json'),'provider_calls':0,'output_writes':0,
                'front_back_requires_bound_responses':full},ensure_ascii=False))
            return 0
        checker=config.get('manuscript_checker') or {}
        if args.replay and checker.get('enabled',True) and not (checker.get('fixture') is not None or checker.get('recordings') is not None):
            raise ValueError('--replay requires checker fixture/recordings; no live fallback')
        options={'retry_failed':args.retry_failed}
        if args.run:
            required=(args.ledger,args.account_ledger,args.account_mapping_dir)
            numbers=(args.budget_limit,args.account_budget_limit,args.incremental_budget)
            if not all(required) or not all(v is not None and math.isfinite(v) and v>0 for v in numbers):
                raise ValueError('--run requires distinct project/account ledgers, mapping directory, finite positive total caps and incremental budget')
            from scripts.upgrade3.writer_candidates import tokenizer_counter
            counter,info=tokenizer_counter(args.tokenizer)
            args.output=args.out_dir/'delivery' if full else args.out_dir
            options.update(live_args=args,token_counter=counter,tokenizer_info=info,run=True)
        if full:
            if not args.front_matter_response or not args.structure_response:
                raise ValueError('full mode requires --front-matter-response and --structure-response; no unbudgeted provider fallback')
            from optomind_optics.harness.science.complete_article import complete_and_deliver_article
            report=complete_and_deliver_article(continuation_result=result,selection=selection,
                source_identity_map=identities,citation_namespace=args.citation_namespace,material_records=materials,
                config=config,out_dir=args.out_dir,structure_provider=_recorded(args.structure_response,'structure'),
                front_matter_provider=_recorded(args.front_matter_response,'front_matter'),**options)
        else:
            report=deliver_repository_article(repository=repo,selected_result_ref=ref,manuscript_path=args.manuscript,
                manuscript_sha256=args.manuscript_sha256,source_identity_map=identities,citation_namespace=args.citation_namespace,
                material_records=materials,config=config,out_dir=args.out_dir,**options)
        print(json.dumps({'status':report.get('status'),'selected_manuscript':report.get('selected_manuscript'),
            'adopted_result_ref':report.get('adopted_result_ref'),'stages':{k:v.get('status') for k,v in report.get('stages',{}).items()},
            'scientific_acceptance':False},ensure_ascii=False,indent=2))
        return 0 if '05_publication' in report.get('stages',{}) else 2
    except Exception as exc:
        print(json.dumps({'status':'blocked','error_type':type(exc).__name__,'message':str(exc)[:1000]}),file=sys.stderr)
        return 1
    finally:
        if repo is not None: repo.close()


if __name__=='__main__':
    raise SystemExit(main())
