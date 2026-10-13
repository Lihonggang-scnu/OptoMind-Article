#!/usr/bin/env python3
"""Reproduce selected offline upgrade checks without sockets or providers.

No editable installation is needed. Article and Review both publish a distribution
named optomind, so this runner isolates their source paths instead of installing
both distributions. Output directories must be new (historical logs stay intact).
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ARTICLE = Path(__file__).resolve().parents[2]
REVIEW_TESTS = [
    'tests/test_r01_r02_retrieval_contract.py', 'tests/test_r02_skeleton_contract.py',
    'tests/test_r05_coverage_repair.py', 'tests/upgrade3/test_manuscript_checker.py',
    'tests/upgrade3/test_manuscript_delivery.py',
    'tests/upgrade3/test_serial_parts_delivery_integration.py',
    'tests/upgrade3/test_review_delivery_integration.py',
    'tests/upgrade3/test_article_publication_metadata.py',
    'tests/upgrade3/test_review_fullscope_consumers.py',
    'tests/upgrade3/test_plan_handoff_integrity.py',
    'tests/upgrade3/test_arrangement_body_handoff.py',
    'tests/upgrade3/test_fullbody_cli.py',
    'tests/upgrade3/test_review_material_boundaries.py',
    'tests/upgrade3/test_directed_reading_adapter_contract.py',
    'tests/test_review_audit_adversarial.py',
    'tests/upgrade3/test_review_pool_audit.py',
]
VERITMM_TESTS = ['tests/test_protocol_schema.py', 'tests/test_experiment_store.py',
    'tests/test_preflight.py', 'tests/test_run_identity.py', 'tests/test_run_compare.py',
    'tests/test_gradient_channel_contract.py']
ARTICLE_OLD = ['tests/test_article_scientific_contracts.py', 'tests/test_article_claims.py',
    'tests/test_article_experiment_planning.py', 'tests/test_article_result_synthesis.py',
    'tests/test_article_architecture.py', 'tests/test_article_writing.py',
    'tests/test_article_continuation.py', 'tests/test_article_pipeline.py',
    'tests/test_offline_upgrade_checks_cli.py']


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--review-root', type=Path, default=Path(os.environ.get('OPTOMIND_REVIEW_ROOT', str(ARTICLE.parent / 'OptoMInd-Review'))))
    p.add_argument('--veritmm-root', type=Path, default=ARTICLE.parent / 'VeriTMM')
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--group', choices=['all', 'article', 'review', 'veritmm', 'cli'], default='all')
    p.add_argument('--preview', action='store_true', help='Print exact selected commands without running them or writing files')
    a = p.parse_args(argv)
    review, veritmm = a.review_root.resolve(), a.veritmm_root.resolve()
    a.output_dir = a.output_dir.resolve()
    for root, marker in [(review, 'optomind_research'), (veritmm, 'tmm_engine')]:
        if not (root / marker).is_dir():
            p.error(f'invalid source root: {root} (missing {marker})')
    code = ARTICLE / 'code'
    science = sorted(str(f.relative_to(code)) for f in (code/'tests').glob('test_science_*.py')
        if not f.name.startswith(('test_science_c18_', 'test_science_c19_')))
    jobs = []
    for label, cwd, tests in [('article', code, science + ARTICLE_OLD), ('review', review, REVIEW_TESTS), ('veritmm', veritmm, VERITMM_TESTS)]:
        if a.group in ('all', label):
            missing = [f for f in tests if not (cwd/f).is_file()]
            if missing: p.error(f'missing selected tests: {missing}')
            jobs.append(dict(label=label, cwd=str(cwd), argv=[sys.executable, '-m', 'pytest', '--disable-socket', '-q', *tests]))
    if a.group in ('all', 'article'):
        jobs.append(dict(label='article_bundled_gradient', cwd=str(ARTICLE/'veritmm'),
            argv=[sys.executable, '-m', 'pytest', '--disable-socket', '-q', 'tests/test_gradient_api.py']))
    if a.group in ('all','cli'):
        bootstrap = 'import sys,runpy,pytest_socket;pytest_socket.disable_socket();p=sys.argv.pop(1);sys.argv[0]=p;runpy.run_path(p,run_name="__main__")'
        scripts = [(ARTICLE, 'quickstart.py'), (code,'scripts/run_article_pipeline_integration.py'),
            (code,'scripts/run_article_continuation.py'), (code,'scripts/run_tmm_research_harness.py'),
            (review,'quickstart.py'), (review,'scripts/upgrade3/manuscript_delivery.py'),
            (review,'scripts/upgrade3/run_candidate_corpus.py'),
            (review,'scripts/upgrade3/review_material_pipeline.py'),
            (review,'scripts/upgrade3/article_evidence.py'),
            (review,'scripts/upgrade3/evaluate_coverage.py'),
            (review,'scripts/upgrade3/publish_planning_pool.py')]
        if (code/'scripts/run_article_scientific_delivery.py').is_file():
            scripts.append((code,'scripts/run_article_scientific_delivery.py'))
        if (code/'scripts/prepare_scientific_authoring_source.py').is_file():
            scripts.append((code,'scripts/prepare_scientific_authoring_source.py'))
        for i,(cwd, script) in enumerate(scripts):
            jobs.append(dict(label=f'cli_{i}',cwd=str(cwd),argv=[sys.executable,'-c',bootstrap,str(cwd/script),'--help']))
        module_bootstrap = 'import sys,runpy,pytest_socket;pytest_socket.disable_socket();m=sys.argv.pop(1);sys.argv[0]=m;runpy.run_module(m,run_name="__main__")'
        if (code/'optomind_optics/harness/science/open_research_cli.py').is_file():
            jobs.append(dict(label='cli_open_research',cwd=str(code),argv=[sys.executable,'-c',module_bootstrap,'optomind_optics.harness.science.open_research_cli','--help']))
        if (code/'optomind_optics/harness/tools/external_measurement.py').is_file():
            jobs.append(dict(label='cli_manual_measurement',cwd=str(code),argv=[sys.executable,'-c',module_bootstrap,'optomind_optics.harness.tools.external_measurement','--help']))
        for label,module in [('lifetime','lifetime_cli'),('trajectory_migration','trajectory_migration')]:
            if (code/f'optomind_optics/harness/science/{module}.py').is_file():
                jobs.append(dict(label=f'cli_{label}',cwd=str(code),argv=[sys.executable,'-c',module_bootstrap,f'optomind_optics.harness.science.{module}','--help']))
    # Absolute per-job roots avoid global /tmp pytest retention, concurrent cleanup
    # collisions and cross-repository cache growth. Preview only describes paths.
    for job in jobs:
        if job['argv'][1:3] == ['-m', 'pytest']:
            temp = a.output_dir / job['label'] / 'pytest_tmp'
            job['pytest_basetemp'] = str(temp)
            job['argv'][3:3] = ['--basetemp', str(temp)]
    plan = {'scope':'selected offline upgrade checks, not full scientific acceptance', 'optional_rcwa_fdfd':'deferred; excluded',
        'environment': {key: os.environ[key] for key in ('TEXMF', 'TEXFORMATS', 'TEXMFVAR', 'TEXMFCONFIG', 'TEXMFCACHE') if key in os.environ}, 'jobs':jobs}
    if a.preview:
        print(json.dumps(plan,indent=2)); return 0
    a.output_dir.mkdir(parents=True, exist_ok=False)
    (a.output_dir/'selection.json').write_text(json.dumps(plan,indent=2)+'\n')
    results=[]
    for job in jobs:
        env=os.environ.copy()
        roots = [job['cwd']]
        if Path(job['cwd'])==code: roots += [str(ARTICLE/'veritmm'),str(review)]
        env.update(PYTHONPATH=os.pathsep.join(roots), MPLCONFIGDIR='/tmp/optomind-mpl-cache', XDG_CACHE_HOME='/tmp/optomind-xdg-cache')
        if 'pytest_basetemp' in job:
            Path(job['pytest_basetemp']).parent.mkdir(parents=True, exist_ok=True)
        start=time.monotonic()
        with (a.output_dir/(job['label']+'.log')).open('w') as log:
            log.write(json.dumps(job)+'\n'); log.flush()
            result=subprocess.run(job['argv'],cwd=job['cwd'],env=env,stdout=log,stderr=subprocess.STDOUT)
            log.write(f'\nEXIT_CODE={result.returncode}\n')
        results.append(dict(label=job['label'],exit_code=result.returncode,elapsed_seconds=time.monotonic()-start))
        (a.output_dir/'results.json').write_text(json.dumps(results,indent=2)+'\n')
        print(job['label'],result.returncode,flush=True)
    return int(any(r['exit_code'] for r in results))

if __name__=='__main__':
    raise SystemExit(main())
