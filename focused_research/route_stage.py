#!/usr/bin/env python3
"""Route preparation with optional explicitly injected, audited local generation.

Shell stages are offline. The generate stage requires main(..., planner=...) from
an existing local billing wrapper. No client, credentials, fallback, or billing
allowance is created here. The caller must enforce the authorized aggregate
budget, at most two route-stage HTTP attempts and zero transport retries before dispatch
(and any separate whole-chain cap, including the planning stage).
This module never retrieves evidence or runs a simulation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


class _RoutePlanner:
    """Add a soft route-count request without replacing the caller's transport."""
    def __init__(self, planner):
        self.planner = planner
        self.calls = 0

    @property
    def last_usage(self):
        return self.planner.last_usage

    @property
    def last_transport(self):
        return self.planner.last_transport

    def _call_query_plan_model(self, prompt):
        if self.calls >= 2:
            raise ValueError('route stage model-call limit exceeded')
        self.calls += 1
        prompt = {**prompt, 'user_payload': {**prompt['user_payload'],
            'route_count_request': 'Aim for 2–3 distinct evidence-grounded routes; return at most 3. This is a soft target: return fewer or none when supported clues are insufficient. Keep each route compact: a few explicit thickness variables, one baseline and one single-variable-change candidate. Never fabricate evidence or duplicate a route just to meet the count.'}}
        return self.planner._call_query_plan_model(prompt)


def main(argv=None, *, planner=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prompt', 'validate', 'materialize', 'generate'])
    parser.add_argument('--task-sheet', type=Path, required=True)
    parser.add_argument('--evidence-file', type=Path, required=True)
    parser.add_argument('--constraints-file', type=Path, required=True)
    parser.add_argument('--routes-file', type=Path)
    parser.add_argument('--route-id')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    # No vendor client or credentials are constructed, even for injected generation.
    import research_routes as rr
    try:
        if args.stage == 'generate' and planner is None:
            raise ValueError('generate requires an explicitly injected original audited local planner; no bare paid fallback is available')
        sheet, evidence, constraints = map(load, [args.task_sheet, args.evidence_file, args.constraints_file])
        if args.output.exists() and any(args.output.iterdir()):
            raise ValueError('output directory must be new or empty')
        if args.stage in ('validate', 'materialize') and args.routes_file is None:
            raise ValueError('--routes-file is required for validate/materialize')
        if args.stage == 'materialize' and not args.route_id:
            raise ValueError('--route-id is required for materialize')
        args.output.mkdir(parents=True, exist_ok=True)
        for name, value in [('task_sheet', sheet), ('evidence_items', evidence), ('design_constraints', constraints)]:
            save(args.output / (name + '.json'), value)
        identity = {name: fingerprint(value) for name, value in
            [('task_sheet', sheet), ('evidence_items', evidence), ('design_constraints', constraints)]}
        called = False
        generation = None
        if args.stage == 'generate':
            # Validate input contracts before dispatch, and never pay to invent evidence.
            rr.build_prompt(sheet, evidence, design_constraints=constraints)
            if not any(item.get('eligible_as_evidence') is True and
                       isinstance(item.get('text'), str) and item['text'].strip() and
                       isinstance(item.get('source_id'), str) and item['source_id'].strip()
                       for item in evidence['items']):
                raise ValueError('no eligible source-linked evidence; generation blocked')
            injected = _RoutePlanner(planner)
            generation = rr.generate(sheet, evidence, injected, design_constraints=constraints, max_repairs=1)
            called = injected.calls > 0
            save(args.output / 'generation_outcome.json', generation)
            routes = generation.get('research_routes')
            instances = []
            if routes:
                save(args.output / 'research_routes.json', routes)
                identity['research_routes'] = fingerprint(routes)
                for route in routes['routes']:
                    if route['readiness'] == 'ready':
                        instances.extend(rr.materialize(routes, route['id'], sheet, evidence, design_constraints=constraints))
            save(args.output / 'prepared_instances.json', instances)
            state = 'instances_prepared' if generation['status'] == 'ok' else generation['status']
        elif args.stage == 'prompt':
            save(args.output / 'route_prompt.json', rr.build_prompt(sheet, evidence, design_constraints=constraints))
            state = 'prompt_prepared'
        else:
            routes = load(args.routes_file)
            rr.validate(routes, sheet, evidence, design_constraints=constraints)
            save(args.output / 'research_routes.json', routes)
            identity['research_routes'] = fingerprint(routes)
            if args.stage == 'materialize':
                instances = rr.materialize(routes, args.route_id, sheet, evidence, design_constraints=constraints)
                save(args.output / 'prepared_instances.json', instances)
                state = 'instances_prepared'
            else:
                state = 'contract_validated'
        save(args.output / 'input_identity.json', identity)
        save(args.output / 'summary.json', {'status': state, 'live_model_called': called,
            'model_call_attempts': injected.calls if args.stage == 'generate' else 0,
            'generation_status': generation['status'] if generation else None,
            'simulation_performed': False, 'scientific_validity_established': False})
        print(state)
        return 0 if generation is None or generation['status'] in ('ok', 'needs_revision') else 2
    except (ValueError, TypeError, KeyError, OSError) as exc:
        # Do not print arbitrary raw model responses, provider headers or secrets.
        print('Route preparation failed: ' + str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
