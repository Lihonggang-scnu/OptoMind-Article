"""Explicit local science execution and evidence-fed revision; no implicit paid client.

All model calls require the caller's existing audited planner. Execution requires
explicit media, grid and numerical budget. Original proposals are never rewritten.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def _modules():
    try:
        from . import research_routes as rr, route_stage, route_consumer as rc
        from . import research_execution as ex, physical_observations as po, iteration_state as state
    except ImportError:
        import research_routes as rr, route_stage, route_consumer as rc
        import research_execution as ex, physical_observations as po, iteration_state as state
    return rr, route_stage, rc, ex, po, state


def _new_output(output, inputs):
    output = Path(output).resolve()
    for source in inputs:
        source = Path(source).resolve()
        if output == source or output in source.parents:
            raise ValueError('output must not contain any input')
    if output.exists():
        raise ValueError('new output directory required')
    output.mkdir(parents=True)
    return output


class ObservationPlanner:
    """Add actual physical observations to the existing generation call boundary."""
    def __init__(self, planner, context):
        self.planner, self.context = planner, context

    def _call_query_plan_model(self, prompt):
        prompt = copy.deepcopy(prompt)
        prompt['user_payload']['previous_physical_iteration'] = self.context
        prompt['system_prompt'] += (
            '\nRevise executable research routes using the supplied actual observations. '
            'Preserve the original user objectives and constraints. Distinguish numerical '
            'results from explanations. Explain which observation motivates each proposed '
            'edit and state an untested next prediction. A useful structure can be kept '
            'even when its earlier explanation was challenged. Do not claim a proposed '
            'next design has already been simulated. No route-count ceiling. '
            'Do not change material identities or fabricate quantitative outcomes.')
        return self.planner._call_query_plan_model(prompt)

    @property
    def last_usage(self):
        return getattr(self.planner, 'last_usage', {})

    @property
    def last_transport(self):
        return getattr(self.planner, 'last_transport', {})


def run_iteration(routes, sheet, evidence, constraints, *, optical_media,
                  wavelengths_nm, output, cache_dir, max_new_evals, max_workers=1):
    rr, _, rc, ex, po, state = _modules()
    compiled = rr.compile_candidates(routes, sheet, evidence, design_constraints=constraints)
    accepted = compiled['accepted_contract']
    output = Path(output)
    save(output / 'raw_proposals.json', routes)
    save(output / 'accepted_contract.json', accepted)
    save(output / 'candidate_report.json', compiled['candidate_report'])
    bindings = {'task_sheet': digest(sheet), 'constraints': digest(constraints),
        'evidence': digest(evidence), 'optical_media': digest(optical_media),
        'wavelengths_nm': digest(wavelengths_nm)}
    save(output / 'input_bindings.json', bindings)
    envelopes, prepared, pair_specs, failures = [], [], [], []
    for route in accepted['routes']:
        if route['readiness'] != 'ready':
            continue
        instances = rr.materialize(accepted, route['id'], sheet, evidence,
            design_constraints=constraints, candidate_mode=True, candidate_report=compiled['candidate_report'])
        instance_map = {x['instance_id']: x for x in instances}
        for instance in instances:
            try:
                envelope = rc.prepare_tmm_inputs(instance, optical_media=optical_media,
                    wavelengths_nm=wavelengths_nm)
                envelopes.append(envelope)
                prepared.append(instance)
            except (ValueError, TypeError, KeyError) as exc:
                failures.append({'route_id': route['id'], 'instance_id': instance['instance_id'],
                    'status': 'input_rejected', 'error': str(exc)})
        pairs = {}
        for expectation in route['expectations']:
            key = (expectation['baseline_instance_id'], expectation['candidate_instance_id'])
            pairs.setdefault(key, []).append(expectation)
        for (baseline_id, candidate_id), predictions in pairs.items():
            if baseline_id not in instance_map or candidate_id not in instance_map:
                continue
            proposal = {'proposal_id': route['id'] + ':' + baseline_id + ':' + candidate_id,
                'proposal_version': '1', 'route_id': route['id'], 'claim': route['claim'],
                'rationale': route['claim'],
                'baseline_instance_id': baseline_id, 'candidate_instance_id': candidate_id,
                'predictions': copy.deepcopy(predictions), 'input_hashes': bindings,
                'baseline_design_hash': digest(instance_map[baseline_id]),
                'candidate_design_hash': digest(instance_map[candidate_id])}
            # Directory names are content-derived; model IDs are never file paths.
            iteration_dir = output / 'iterations' / digest(proposal)[:24]
            try:
                state.create_prediction_record(iteration_dir, proposal)
                pair_specs.append((iteration_dir, proposal, instance_map))
            except (ValueError, TypeError, KeyError) as exc:
                failures.append({'proposal_id': proposal['proposal_id'], 'status': 'comparison_rejected', 'error': str(exc)})
    save(output / 'prepared_instances.json', prepared)
    save(output / 'execution_inputs.json', envelopes)
    for iteration_dir, _, _ in pair_specs:
        state.begin_execution(iteration_dir)
    batch = ex.execute_prepared_batch(envelopes, cache_dir=cache_dir,
        max_new_evals=max_new_evals, max_workers=max_workers)
    save(output / 'execution_results.json', batch)
    by_id = {(x['route_id'], x['instance_id']): x for x in batch['results']}
    observations = []
    for iteration_dir, proposal, instance_map in pair_specs:
        baseline = by_id.get((proposal['route_id'], proposal['baseline_instance_id']))
        candidate = by_id.get((proposal['route_id'], proposal['candidate_instance_id']))
        if baseline is None or candidate is None:
            failures.append({'proposal_id': proposal['proposal_id'], 'status': 'missing_execution'})
            continue
        observation = po.build_physical_observations(baseline, candidate, sheet['subgoals'])
        state.record_observation(iteration_dir, observation)
        prediction_result = po.compare_predictions(proposal['predictions'], observation)
        entry = {'proposal': proposal, 'observation': observation,
            'prediction_result': prediction_result, 'iteration_path': str(iteration_dir)}
        observations.append(entry)
    save(output / 'physical_observations.json', observations)
    save(output / 'input_failures.json', failures)
    observed_ok = observations and all(all(g['status'] == 'observed' for g in x['observation']['goals']) for x in observations)
    summary = {'status': 'observations_recorded' if observed_ok and not failures else 'partial',
        'live_model_called': False, 'proposal_pairs': len(pair_specs),
        'observation_pairs': len(observations), 'input_failure_count': len(failures),
        'metering': batch['metering'], 'mechanism_proven': False,
        'next_action_executed': False}
    save(output / 'summary.json', summary)
    return summary


def main(argv=None, *, planner=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=['run', 'revise'])
    p.add_argument('--task-sheet', type=Path, required=True)
    p.add_argument('--evidence-file', type=Path, required=True)
    p.add_argument('--constraints-file', type=Path, required=True)
    p.add_argument('--routes-file', type=Path)
    p.add_argument('--optical-media', type=Path)
    p.add_argument('--wavelengths', type=Path)
    p.add_argument('--cache-dir', type=Path)
    p.add_argument('--max-new-evals', type=int)
    p.add_argument('--max-workers', type=int, default=1)
    p.add_argument('--previous-run', type=Path)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if not args.execute:
            raise ValueError('explicit --execute required')
        inputs = [args.task_sheet, args.evidence_file, args.constraints_file]
        sheet, evidence, constraints = map(load, inputs)
        if args.stage == 'run':
            if any(x is None for x in (args.routes_file, args.optical_media,
                    args.wavelengths, args.cache_dir, args.max_new_evals)):
                raise ValueError('run requires routes, explicit media/grid, cache and numerical budget')
            if args.max_new_evals < 0 or args.max_workers < 1:
                raise ValueError('invalid numerical budget or worker count')
            inputs += [args.routes_file, args.optical_media, args.wavelengths]
            output = _new_output(args.output, inputs)
            summary = run_iteration(load(args.routes_file), sheet, evidence, constraints,
                optical_media=load(args.optical_media), wavelengths_nm=load(args.wavelengths),
                output=output, cache_dir=args.cache_dir, max_new_evals=args.max_new_evals,
                max_workers=args.max_workers)
            print(summary['status'])
            return 0 if summary['status'] == 'observations_recorded' else 2
        if planner is None or args.previous_run is None:
            raise ValueError('revise requires existing audited planner injection and previous run')
        context = load(args.previous_run / 'physical_observations.json')
        old_bindings = load(args.previous_run / 'input_bindings.json')
        if any(old_bindings[k] != digest(v) for k, v in
                [('task_sheet', sheet), ('constraints', constraints), ('evidence', evidence)]):
            raise ValueError('revision inputs must match previous iteration')
        if not context:
            raise ValueError('no physical observation available for revision')
        _, route_stage, _, _, _, state = _modules()
        for entry in context:
            directory = Path(entry['iteration_path']).resolve()
            if directory.parent != (args.previous_run / 'iterations').resolve():
                raise ValueError('iteration path outside previous run')
            if (directory / 'next_action.json').exists():
                raise ValueError('previous iteration already has a next action')
            chain = state._chain(directory, 'observation')
            saved = chain[-1]['payload']['observations']
            frozen = chain[0]['payload']
            if frozen.get('input_hashes') != old_bindings:
                raise ValueError('frozen prediction input binding mismatch')
            if saved != entry['observation'] or frozen != entry['proposal']:
                raise ValueError('previous observation or prediction binding mismatch')
        if args.output.exists():
            raise ValueError('new output directory required')
        result = route_stage.main(['generate', '--task-sheet', str(args.task_sheet),
            '--evidence-file', str(args.evidence_file), '--constraints-file', str(args.constraints_file),
            '--output', str(args.output)], planner=ObservationPlanner(planner, context))
        action_errors = []
        # Route generation may select a whole-item subset. Bind its actual saved
        # evidence for the next run rather than silently restoring the archive.
        selected_path = args.output / 'evidence_items.json'
        selected_binding = None
        if selected_path.is_file():
            selected_binding = {'path': str(selected_path.resolve()),
                'sha256': digest(load(selected_path)),
                'selection_path': str((args.output / 'evidence_selection.json').resolve())}
        next_contract = load(args.output / 'research_routes.json') if (args.output / 'research_routes.json').is_file() else None
        if next_contract and next_contract.get('routes'):
            for entry in context:
                proposed = {'proposal_version': str(entry['proposal']['proposal_version']) + '.next',
                    'status': 'proposed_not_executed',
                    'physical_basis_refs': [g['observation_id'] for g in entry['observation']['goals'] if g['status'] == 'observed'],
                    'basis_refs_source': 'provided_context_not_verified_model_use',
                    'proposed_edit': {'contract_sha256': digest(next_contract), 'contract_path': str((args.output / 'research_routes.json').resolve()),
                        'route_ids': [r['id'] for r in next_contract['routes']],
                        'evidence_binding': selected_binding},
                    'predictions': [{**p, 'route_id': r['id']} for r in next_contract['routes'] for p in r['expectations']]}
                if proposed['physical_basis_refs'] and proposed['predictions']:
                    try:
                        state.record_next_action(entry['iteration_path'], proposed)
                    except (ValueError, TypeError, KeyError, OSError) as exc:
                        action_errors.append({'proposal_id': entry['proposal']['proposal_id'], 'error': str(exc)})
        save(args.output / 'revision_parent.json', {'previous_run': str(args.previous_run.resolve()),
            'observation_sha256': digest(context), 'input_bindings': old_bindings,
            'next_evidence_binding': selected_binding,
            'next_action_executed': False, 'action_record_errors': action_errors})
        return 2 if action_errors else result
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print('Scientific stage failed: ' + str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
