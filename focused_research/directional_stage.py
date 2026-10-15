"""Execute model-expanded concrete branch seeds with bounded process search.

Semantic expansion belongs to direction_expansion and its injected planner.
This module never calls a model. Each branch searches from its provided seed,
then a single shared certification dispatch updates the retained design archive.
"""
from __future__ import annotations

import argparse
import copy
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
import os
from pathlib import Path
import time
import uuid

try:
    from . import search_stage as st, family_optimization as fo
except ImportError:
    import search_stage as st, family_optimization as fo

SCHEMA = 'focused_research.directional_stage.v1'


def _expansion_module():
    try:
        from . import direction_expansion
    except ImportError:
        import direction_expansion
    return direction_expansion


def read_expansion(directory, previous):
    directory = Path(directory)
    manifest = st.load(directory / 'manifest.json')
    required = {'expansion_parent.json','coarse_plans.json','expansion_response.json','summary.json'}
    if not required.issubset(manifest):
        raise ValueError('expansion is incomplete or has no executable response')
    for name, expected in manifest.items():
        if Path(name).name != name or st.digest(st.load(directory / name)) != expected:
            raise ValueError('expansion artifact binding mismatch: '+name)
    parent = st.load(directory / 'expansion_parent.json')
    if parent.get('manifest_sha256') != previous['manifest_sha256']:
        raise ValueError('expansion belongs to a different previous search snapshot')
    return st.load(directory / 'expansion_response.json'), st.load(directory / 'coarse_plans.json')


def _search_worker(payload):
    """Top-level spawn-safe worker. Failed reservations are never silently retried."""
    job, quota, seed, backend = payload
    started_wall, started_cpu = time.perf_counter(), time.process_time()
    result = {'branch_id':job['branch_id'],'worker_pid':os.getpid(),
              'reserved_forward_design_rows':quota}
    try:
        if backend in ('auto','torch'):
            try:
                import torch
                torch.set_num_threads(1)
            except ImportError:
                if backend == 'torch':
                    raise
        search = fo.optimize_family(job['route'],job['seed_envelopes'],
            max_forward_evaluations=quota, seed=seed, backend=backend,
            parameterization=job['parameterization'],
            search_bounds_nm=job.get('search_bounds_nm'), initialization_policy='provided_only')
        used = search['metering']['forward_design_rows']
        if type(used) is not int or used < 0 or used > quota:
            raise ValueError('optimizer violated its reserved forward-design-row budget')
        result.update(status='completed',search=search)
    except Exception as exc:
        result.update(status='search_failed',error=str(exc),metering_complete=False)
    result.update(worker_cpu_seconds=time.process_time()-started_cpu,
                  worker_wall_seconds=time.perf_counter()-started_wall)
    return result


def _run_workers(jobs, quotas, *, seed, backend, search_workers):
    payloads = [(job,quotas[i],seed+i,backend) for i,job in enumerate(jobs)]
    if search_workers == 1:
        return [_search_worker(payload) for payload in payloads]
    results = [None]*len(payloads)
    # Explicit spawn avoids inheriting numerical libraries' process/thread state.
    with ProcessPoolExecutor(max_workers=search_workers,
                             mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = {pool.submit(_search_worker,payload):i for i,payload in enumerate(payloads)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                results[i] = future.result()
            except Exception as exc:
                results[i] = {'branch_id':jobs[i]['branch_id'],'status':'worker_failed',
                    'error':str(exc),'metering_complete':False,'reserved_forward_design_rows':quotas[i]}
    return results


def build_branch_feedback(jobs, workers, results, archive, coarse_plans, failures):
    """Compact descriptive comparisons of real certified designs, never a composite point."""
    plans = coarse_plans.get('plans', []) if isinstance(coarse_plans, dict) else coarse_plans
    goals = archive['objectives']
    senses = {g['objective_id']: 1 if g['sense']=='maximize' else -1 for g in goals}
    events = {e['instance_id']: e for e in archive['evaluations']}
    def metrics(design_id):
        return {g: archive['designs'][design_id]['metrics'][g]['value'] for g in senses}
    branches = []
    for job, worker in zip(jobs, workers):
        rows = [r for r in results if r['directional_attribution']['branch_id']==job['branch_id']]
        associated = {}
        for row in rows:
            event = events.get(row['instance_id'])
            if row.get('physics_accepted') and event:
                associated.setdefault(event['design_id'], []).append(event)
        parent = metrics(job['parent_design_id'])
        outcomes = dict(all_improve=0, tradeoff=0, all_worse=0, unchanged=0)
        details = {}
        for design_id, evidence in associated.items():
            values = metrics(design_id)
            deltas = {g: values[g]-parent[g] for g in senses}
            oriented = [deltas[g]*senses[g] for g in senses]
            outcome = ('unchanged' if all(d==0 for d in oriented) else
                       'all_improve' if all(d>=0 for d in oriented) else
                       'all_worse' if all(d<=0 for d in oriented) else 'tradeoff')
            outcomes[outcome] += 1
            details[design_id] = {'design_id':design_id, 'metrics':values,
                'deltas_from_parent':deltas, 'outcome':outcome,
                'cache_hit_count':sum(bool(e['cache_hit']) for e in evidence),
                'new_evidence_count':sum(bool(e['new_evidence']) for e in evidence),
                'extreme_for_goals':[]}
        selected = {}
        for goal, sense in senses.items():
            if details:
                ident = max(sorted(details), key=lambda d: details[d]['metrics'][goal]*sense)
                selected[ident] = details[ident]
                selected[ident]['extreme_for_goals'].append(goal)
        search_meter = worker.get('search',{}).get('metering',{})
        branch_failures = [f for f in failures if f.get('branch_id')==job['branch_id']]
        branches.append({
            **{k:copy.deepcopy(job[k]) for k in ('branch_id','plan_index','hypothesis_id',
                'parent_design_id','parameterization','rationale','parameters_nm','search_bounds_nm')},
            'parent_metrics':parent, 'search_status':worker['status'],
            'counts':{'reserved_forward_design_rows':worker['reserved_forward_design_rows'],
                'search_forward_design_rows':search_meter.get('forward_design_rows'),
                'search_solver_channel_rows':search_meter.get('solver_channel_rows'),
                'search_metering_complete':worker['status']=='completed',
                'candidate_count':len(rows),
                'certified_candidate_count':sum(bool(r.get('physics_accepted')) for r in rows),
                'unique_associated_certified_design_count':len(details),
                'new_certification_evaluations':sum(r.get('metering',{}).get('new_evaluations',0) for r in rows),
                'cache_hit_count':sum(bool(r.get('cache_hit')) for r in rows),
                'new_evidence_count':sum(d['new_evidence_count'] for d in details.values()),
                'deferred_unvalidated_count':sum(r.get('status') in ('deferred_unvalidated','budget_exhausted') for r in rows),
                'certification_failure_count':sum(not r.get('physics_accepted') and
                    r.get('status') not in ('deferred_unvalidated','budget_exhausted') for r in rows),
                'search_input_archive_failure_count':len(branch_failures)},
            'outcome_counts_unique_certified_designs':outcomes,
            'selected_certified_designs':list(selected.values()),
            'failure_summaries':branch_failures})
    return {'schema_version':'focused_research.branch_feedback.v1',
        'scope':'Descriptive sampled-grid outcomes only; cached reuse is not new evidence. No mechanism proof.',
        'outcome_definition':'Exact metric deltas oriented by objective sense: all_improve means no goal worsens and at least one improves; all_worse is the reverse; tradeoff has both signs; unchanged has all zero deltas.',
        'selection_scope':'At most one actual associated certified design per goal extreme, deduplicated. Every selected point retains its complete goal vector. Counts cover all associated unique certified designs, not only selected extremes.',
        'objectives':[{'objective_id':g['objective_id'],'sense':g['sense']} for g in goals],
        'plans':[{'plan_index':i,'original_coarse_plan':copy.deepcopy(plans[i])}
                 for i in sorted({j['plan_index'] for j in jobs})],
        'branches':branches, 'raw_results_ref':'branch_search_results.json'}


def run_directional_search(previous_search, expansion_payload, coarse_plans, *, output,
                           cache_dir, max_forward_evaluations, max_new_evals,
                           search_workers=2, max_workers=2, backend='auto', seed=42):
    """Validate concrete seeds, reserve globally, parallel-search, certify, retain."""
    st._budget(max_forward_evaluations,'max_forward_evaluations')
    st._budget(max_new_evals,'max_new_evals')
    if type(search_workers) is not int or not 1 <= search_workers <= 2:
        raise ValueError('search_workers must be 1 or 2')
    if type(max_workers) is not int or not 1 <= max_workers <= 2:
        raise ValueError('certification max_workers must be 1 or 2')
    if backend not in ('auto','numpy','torch'):
        raise ValueError('unsupported numerical backend')
    previous = st.read_search(previous_search)
    validated = _expansion_module().validate_expansion(expansion_payload,previous,coarse_plans)
    jobs, report = validated['jobs'],validated['report']
    output = st._new_output(output,[previous_search])
    started_wall, started_cpu = time.perf_counter(),time.process_time()
    st._persist_context(output,previous['context'])
    st.save(output/'accepted_contract.json',previous['accepted'])
    st.save(output/'candidate_report.json',{'source':'previous_search','expansion_validation':report})
    st.save(output/'expansion_input.json',expansion_payload)
    st.save(output/'coarse_plans.json',coarse_plans)
    st.save(output/'expansion_report.json',report)
    st.save(output/'expanded_branch_jobs.json',jobs)
    st.save(output/'search_parent.json',{'previous_search':str(Path(previous_search).resolve()),
        'manifest_sha256':previous['manifest_sha256'],
        'model_context_scope':'Expansion input is recorded separately; no claim that earlier coarse plans saw later results.'})
    quotas = [max_forward_evaluations//len(jobs)+int(i<max_forward_evaluations%len(jobs))
              for i in range(len(jobs))]
    st.save(output/'budget_reservation.json',{'max_forward_design_rows':max_forward_evaluations,
        'max_new_certification_evaluations':max_new_evals,'search_workers':search_workers,
        'certification_workers':max_workers,'phases':'Search processes finish before certification dispatch.',
        'initialization_policy':'provided_only','implicit_retries':0})
    st.save(output/'branch_reservations.json',[{'branch_id':job['branch_id'],
        'hypothesis_id':job['hypothesis_id'],'plan_index':job['plan_index'],
        'parent_design_id':job['parent_design_id'],'reserved_forward_design_rows':quotas[i],
        'seed':seed+i} for i,job in enumerate(jobs)])
    # All reservations are persisted before the first optimizer is started.
    workers = _run_workers(jobs,quotas,seed=seed,backend=backend,search_workers=search_workers) if jobs else []
    st.save(output/'branch_search_results.json',workers)
    archive = copy.deepcopy(previous['archive'])
    envelopes = copy.deepcopy(previous['envelopes'])
    diagnostics = copy.deepcopy(previous['diagnostics'])
    candidates,ancestry,failures = [],{},[]
    nonce = uuid.uuid4().hex
    for job,result in zip(jobs,workers):
        if result['status'] != 'completed':
            failures.append({'stage':'branch_search','branch_id':job['branch_id'],
                'error':result.get('error'),'reserved_forward_design_rows':result['reserved_forward_design_rows']})
            continue
        seed_ids = {e['instance_id'] for e in job['seed_envelopes']}
        for proposal in result['search']['candidates']:
            try:
                envelope = copy.deepcopy(proposal)
                envelope.pop('directional_observations',None)
                st._validate_candidate(envelope,job['seed_envelopes'][0],job['route'],job['parameterization'])
                st._vector(envelope)  # Every original goal must have a finite search metric.
                for index,(spec,physical) in enumerate(zip(job['route']['family']['layers'],envelope['tmm_task']['stack']['layers'])):
                    variable = spec['thickness_nm']
                    if isinstance(variable,str):
                        name = variable if job['parameterization']=='family' else 'layer_%d:%s' % (index,variable)
                        bounds = job.get('search_bounds_nm',{}).get(name,job['route']['family']['thickness_variables_nm'][variable])
                        if not bounds[0] <= physical['thickness_nm'] <= bounds[1]:
                            raise ValueError('candidate violates model-declared branch search bounds')
                source_id = proposal.get('parent_instance_id')
                if source_id not in seed_ids:
                    raise ValueError('provided-only optimizer candidate lacks an actual expanded seed ancestor')
                envelope['instance_id'] = 'branch_'+nonce+'_'+str(len(candidates))
                envelope['tmm_task']['stack']['name'] = envelope['instance_id']
                attribution = {'branch_id':job['branch_id'],'hypothesis_id':job['hypothesis_id'],
                    'plan_index':job['plan_index'],'parent_design_id':job['parent_design_id'],
                    'expanded_seed_instance_id':source_id,'parameterization':job['parameterization'],
                    'search_bounds_nm':copy.deepcopy(job.get('search_bounds_nm')),
                    'scope':'Parent-derived model seed followed by bounded numerical optimization; not a mechanism proof.'}
                envelope['directional_attribution'] = attribution
                ancestry[envelope['instance_id']] = attribution
                candidates.append(envelope)
            except (ValueError,TypeError,KeyError) as exc:
                failures.append({'stage':'candidate_input','branch_id':job['branch_id'],'error':str(exc)})
    archived_physics = {st.digest(st.da._physical(d['physical_input'])) for d in archive['designs'].values()}
    rediscoveries = [e for e in candidates if st.digest(st.da._physical(e['tmm_task'])) in archived_physics]
    probe = st.ex.execute_prepared_batch(rediscoveries,cache_dir=cache_dir,max_new_evals=0,max_workers=max_workers)
    st.save(output/'rediscovery_cache_probe.json',probe)
    cached = {r['instance_id']:r for r in probe['results'] if r.get('physics_accepted') and r.get('cache_hit')}
    selected,selection = st.select_certification_candidates(candidates,archive,max_new_evals=max_new_evals,
                                                           verified_cached_instance_ids=cached)
    st.save(output/'certification_selection.json',selection)
    # Exactly one positive-budget certification batch across all branches.
    batch = st.ex.execute_prepared_batch([e for e in selected if e['instance_id'] not in cached],
        cache_dir=cache_dir,max_new_evals=max_new_evals,max_workers=max_workers)
    by_id = {**cached,**{r['instance_id']:r for r in batch['results']}}
    results = []
    decision_by_id = {d['instance_id']:d for d in selection['decisions']}
    for envelope in candidates:
        ident = envelope['instance_id']
        row = by_id.get(ident)
        if row is None:
            row = {'instance_id':ident,'route_id':envelope['route_id'],'status':'deferred_unvalidated',
                'physics_accepted':False,'certification_attempted':False,
                'reason':decision_by_id[ident]['reason'],'metering':{'new_evaluations':0}}
        row = {**row,'directional_attribution':copy.deepcopy(ancestry[ident])}
        results.append(row)
        if not row.get('physics_accepted'):
            continue
        relation = ancestry[ident]
        job = next(j for j in jobs if j['branch_id']==relation['branch_id'])
        try:
            archive,design_id = st._record(archive,envelopes,row,envelope,
                parent=relation['parent_design_id'],family=st._family(job['route'],job['parameterization']),role='candidate')
            # Rediscovery keeps original physical envelope; append attribution separately.
            envelopes[design_id].setdefault('directional_observations',[]).append(copy.deepcopy(relation))
            diagnostics.append(st._diagnostic(archive,relation['parent_design_id'],design_id,
                                              previous['context']['sheet']['subgoals']))
        except (ValueError,TypeError,KeyError) as exc:
            failures.append({'stage':'archive','branch_id':relation['branch_id'],'instance_id':ident,'error':str(exc)})
    diagnostics = list({d['diagnosis_id']:d for d in diagnostics}.values())
    completed = [r for r in workers if r['status']=='completed']
    failed = [r for r in workers if r['status']!='completed']
    meters = [r['search']['metering'] for r in completed]
    rejected_count = report.get('rejected_count',len(report.get('rejected_branches',[])))
    summary = {'schema_version':SCHEMA,'stage':'directional_search',
        'status':'search_complete' if jobs and not failures and not rejected_count and all(r.get('physics_accepted') for r in batch['results']) else 'partial',
        'live_model_called':False,'branch_count':len(jobs),'candidate_count':len(candidates),
        'certified_design_count':len(archive['designs']),'pareto_design_ids':archive['pareto_design_ids'],
        'deferred_unvalidated_candidate_count':selection['deferred_unvalidated_count'],
        'uncertified_candidate_count':sum(not r.get('physics_accepted') for r in results),
        'certification_coverage':'partial' if failures or any(not r.get('physics_accepted') for r in results) else 'complete',
        'scientific_outcome':'Execution status is not a claim of objective improvement or correct scientific interpretation.',
        'metering':{'search_forward_design_rows':sum(m['forward_design_rows'] for m in meters),
            'search_solver_channel_rows':sum(m['solver_channel_rows'] for m in meters),
            'search_backward_passes':sum(m.get('backward_passes',0) for m in meters),
            'search_metering_complete':not failed,
            'failed_branch_reserved_forward_design_rows':sum(r['reserved_forward_design_rows'] for r in failed),
            'max_search_forward_design_rows':max_forward_evaluations,
            'certification_new_evaluations':batch['metering']['new_evaluations'],
            'max_new_certification_evaluations':max_new_evals,
            'certification_dispatch':batch['metering'],'rediscovery_cache_probe':probe['metering'],
            'search_worker_cpu_seconds':sum(r.get('worker_cpu_seconds',0) for r in workers),
            'parent_process_cpu_seconds':time.process_time()-started_cpu,
            'stage_wall_seconds':time.perf_counter()-started_wall,
            'cpu_scope':'Search worker CPU excludes certification workers. With search_workers=1 it is included in parent CPU; do not sum those overlapping values.',
            'search_executor':'process_pool_spawn' if search_workers>1 else 'serial',
            'search_workers':search_workers,'certification_workers':max_workers},
        'mechanism_proven':False,'claim_scope':'certified_sampled_grid_only'}
    for name,value in [('execution_inputs',candidates),('execution_results',results),
        ('candidate_ancestry',ancestry),('design_archive',archive),('design_envelopes',envelopes),
        ('physics_diagnostics',diagnostics),('input_failures',failures),('summary',summary)]:
        st.save(output/(name+'.json'),value)
    st.save(output/'branch_feedback.json',build_branch_feedback(
        jobs,workers,results,archive,coarse_plans,failures))
    st._seal(output)
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=['execute'])
    for name in ('previous-search','expansion-dir','cache-dir','output'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--max-forward-evaluations',type=int,required=True)
    p.add_argument('--max-new-evals',type=int,required=True)
    p.add_argument('--search-workers',type=int,default=2)
    p.add_argument('--max-workers',type=int,default=2)
    p.add_argument('--backend',choices=['auto','numpy','torch'],default='auto')
    p.add_argument('--seed',type=int,default=42)
    p.add_argument('--execute',action='store_true')
    args = p.parse_args(argv)
    try:
        if not args.execute:
            raise ValueError('explicit --execute required')
        previous = st.read_search(args.previous_search)
        response,plans = read_expansion(args.expansion_dir,previous)
        result = run_directional_search(args.previous_search,response,plans,output=args.output,
            cache_dir=args.cache_dir,max_forward_evaluations=args.max_forward_evaluations,
            max_new_evals=args.max_new_evals,search_workers=args.search_workers,max_workers=args.max_workers,
            backend=args.backend,seed=args.seed)
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['status']=='search_complete' else 2
    except (ValueError,TypeError,KeyError,OSError) as exc:
        print('Directional search failed: '+str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
