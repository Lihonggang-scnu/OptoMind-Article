#!/usr/bin/env python3
"""Offline route preparation. No model, retrieval or simulation calls.

Live route generation is a Python API for the original local metered caller;
this command intentionally does not construct an unmetered live client.
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=['prompt', 'validate', 'materialize'])
    parser.add_argument('--task-sheet', type=Path, required=True)
    parser.add_argument('--evidence-file', type=Path, required=True)
    parser.add_argument('--constraints-file', type=Path, required=True)
    parser.add_argument('--routes-file', type=Path)
    parser.add_argument('--route-id')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    # No vendor imports/credentials are needed for these offline operations.
    import research_routes as rr
    try:
        sheet, evidence, constraints = map(load, [args.task_sheet, args.evidence_file, args.constraints_file])
        if args.output.exists() and any(args.output.iterdir()):
            raise ValueError('output directory must be new or empty')
        if args.stage != 'prompt' and args.routes_file is None:
            raise ValueError('--routes-file is required for validate/materialize')
        if args.stage == 'materialize' and not args.route_id:
            raise ValueError('--route-id is required for materialize')
        args.output.mkdir(parents=True, exist_ok=True)
        for name, value in [('task_sheet', sheet), ('evidence_items', evidence), ('design_constraints', constraints)]:
            save(args.output / (name + '.json'), value)
        identity = {name: fingerprint(value) for name, value in
            [('task_sheet', sheet), ('evidence_items', evidence), ('design_constraints', constraints)]}
        if args.stage == 'prompt':
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
        save(args.output / 'summary.json', {'status': state, 'live_model_called': False,
            'simulation_performed': False, 'scientific_validity_established': False})
        print(state)
        return 0
    except (ValueError, TypeError, KeyError, OSError) as exc:
        # Do not print arbitrary raw model responses, provider headers or secrets.
        print('Route preparation failed: ' + str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
