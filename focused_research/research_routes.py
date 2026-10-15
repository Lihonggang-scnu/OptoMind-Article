"""Source-labeled hypotheses and deterministic stack recipes, never simulation.

Validation establishes provenance and executable syntax, not scientific correctness.
A ready hypothesis may use weak evidence or explicit model knowledge; its predicted
direction remains untested. Legacy v1 contracts retain external-evidence semantics.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

try:
    from . import task_sheet as task_contract
    from .material_evidence import prompt_evidence_view
    from .route_quality import contract_change_audit
except ImportError:
    import task_sheet as task_contract
    from material_evidence import prompt_evidence_view
    from route_quality import contract_change_audit

SCHEMA = 'focused_research.research_routes.v2'
LEGACY_SCHEMA = 'focused_research.research_routes.v1'
ROOT = Path(__file__).resolve().parent


def _fail(path, reason):
    raise ValueError(f'research routes {path}: {reason}')


def _obj(value, keys, path):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(path, 'requires exactly ' + ', '.join(keys))


def _text(value, path, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(path, 'requires nonempty unpadded text')


def _list(value, path):
    if not isinstance(value, list):
        _fail(path, 'requires a list')


def _refs(value, allowed, path, minimum=0):
    _list(value, path)
    if len(value) < minimum or any(not isinstance(v, str) or v not in allowed for v in value) or len(set(value)) != len(value):
        _fail(path, 'requires unique references to existing IDs')


def _index(rows, key, path):
    _list(rows, path)
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            _fail(path, 'requires objects')
        ident = row.get(key)
        _text(ident, path + '.' + key)
        if ident in result:
            _fail(path, 'duplicate ID')
        result[ident] = row
    return result


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _inputs(sheet, evidence, constraints):
    task_contract.validate(sheet)
    fields = ['allowed_materials', 'max_layers', 'substrate', 'incident_medium']
    _obj(constraints, fields + ['constraint_source_ids', 'assumptions'], 'design_constraints')
    _obj(constraints['constraint_source_ids'], fields, 'constraint_source_ids')
    if not isinstance(constraints['assumptions'], dict) or not set(constraints['assumptions']).issubset(fields):
        _fail('assumptions', 'requires explanations keyed by constraint field')
    cids = {c['id'] for c in sheet['explicit_constraints']}
    for field in fields:
        _refs(constraints['constraint_source_ids'][field], cids, 'constraint_source_ids.' + field)
        if field in constraints['assumptions']:
            _text(constraints['assumptions'][field], 'assumptions.' + field)
        if constraints[field] is not None and not constraints['constraint_source_ids'][field] and field not in constraints['assumptions']:
            _fail(field, 'requires existing task constraint IDs or an explicit configuration assumption')
    materials = constraints['allowed_materials']
    if materials is not None:
        _list(materials, 'allowed_materials')
        for material in materials:
            _text(material, 'allowed_materials')
        if not materials or len(set(materials)) != len(materials):
            _fail('allowed_materials', 'requires unique task-permitted materials')
    if constraints['max_layers'] is not None and (type(constraints['max_layers']) is not int or constraints['max_layers'] < 1):
        _fail('max_layers', 'requires positive integer from task constraints')
    for field in ['substrate', 'incident_medium']:
        _text(constraints[field], field, nullable=True)
    if not isinstance(evidence, dict) or evidence.get('schema_version') != 'focused_evidence_items_v1':
        _fail('evidence_items', 'unsupported evidence schema')
    items = _index(evidence.get('items'), 'evidence_id', 'evidence_items.items')
    return {g['id']: g for g in sheet['subgoals']}, items


def _family(family, constraints, path):
    _obj(family, ['kind', 'incident_medium', 'substrate', 'layers', 'thickness_variables_nm'], path)
    if family['kind'] != 'explicit_stack':
        _fail(path + '.kind', 'only declarative explicit_stack is supported')
    _text(family['incident_medium'], path + '.incident_medium')
    _text(family['substrate'], path + '.substrate')
    for field in ['substrate', 'incident_medium']:
        if constraints[field] is not None and family[field] != constraints[field]:
            _fail(path + '.' + field, 'must match task configuration')
    layers = family['layers']
    _list(layers, path + '.layers')
    if not layers or (constraints['max_layers'] is not None and len(layers) > constraints['max_layers']):
        _fail(path + '.layers', 'empty stack or task layer limit exceeded')
    variables = family['thickness_variables_nm']
    if not isinstance(variables, dict):
        _fail(path + '.thickness_variables_nm', 'requires a named range object')
    for name, bounds in variables.items():
        _text(name, path + '.variable')
        if not isinstance(bounds, list) or len(bounds) != 2 or not all(_number(v) for v in bounds) or not 0 < bounds[0] <= bounds[1]:
            _fail(path + '.variable', 'requires finite positive [min_nm,max_nm]')
    used = set()
    for layer in layers:
        _obj(layer, ['material', 'thickness_nm'], path + '.layer')
        if not isinstance(layer['material'], str) or (constraints['allowed_materials'] is not None and layer['material'] not in constraints['allowed_materials']):
            _fail(path + '.material', 'material not permitted by task')
        thickness = layer['thickness_nm']
        if isinstance(thickness, str) and thickness in variables:
            used.add(thickness)
        elif not _number(thickness) or thickness <= 0:
            _fail(path + '.thickness_nm', 'requires positive finite nm or a declared variable')
    if used != set(variables):
        _fail(path + '.thickness_variables_nm', 'unused thickness variable')


def _instances(instances, family, path):
    indexed = _index(instances, 'id', path)
    for instance in instances:
        _obj(instance, ['id', 'parameters_nm'], path)
        params = instance['parameters_nm']
        variables = family['thickness_variables_nm']
        if not isinstance(params, dict) or set(params) != set(variables):
            _fail(path, 'each instance must assign every thickness variable exactly once')
        for name, value in params.items():
            lo, hi = variables[name]
            if not _number(value) or not lo <= value <= hi:
                _fail(path, 'thickness parameter outside family range')
    return indexed


def revision_reasons(route, sheet, design_constraints=None):
    """Structural blockers only; evidence strength is deliberately not scored."""
    reasons = []
    if design_constraints is not None:
        reasons.extend('missing configuration: ' + key for key in ['allowed_materials', 'max_layers', 'substrate', 'incident_medium'] if design_constraints[key] is None)
    if sheet['readiness'] != 'ready':
        reasons.append('task sheet needs revision')
    for field in ('claim', 'family'):
        if route[field] is None:
            reasons.append('missing ' + field)
    mode = route.get('basis_mode', 'external_evidence')
    if mode in ('external_evidence', 'hybrid'):
        for field in ('clue_ids', 'evidence_ids'):
            if not route[field]:
                reasons.append('missing ' + field)
    if mode in ('model_knowledge', 'hybrid') and route['model_knowledge_rationale'] is None:
        reasons.append('missing model_knowledge_rationale')
    for field in ('goal_ids', 'transfer_assumptions', 'compatibility_conditions', 'instances', 'expectations'):
        if not route[field]:
            reasons.append('missing ' + field)
    if route['open_requirements']:
        reasons.extend(route['open_requirements'])
    goals = {g['id']: g for g in sheet['subgoals']}
    for condition in route['compatibility_conditions']:
        if condition['required_value'] is None:
            reasons.append('unresolved compatibility: ' + condition['reason'])
        elif condition['required_value'] != goals[condition['goal_id']]['executable_metric']['region'][condition['field']]:
            reasons.append('task condition conflicts: ' + condition['reason'])
    return reasons


def validate(routes, task_sheet, evidence_items, *, design_constraints):
    """Reject fabricated references or altered task metrics; retain incomplete routes.

    Missing recipe/claim may be null and missing collections empty, but such a route
    must be needs_revision. Explicitly contradictory compatibility also blocks it.
    """
    goals, evidence = _inputs(task_sheet, evidence_items, design_constraints)
    _obj(routes, ['schema_version', 'decision_clues', 'routes'], '$')
    if routes['schema_version'] not in (SCHEMA, LEGACY_SCHEMA):
        _fail('schema_version', 'unsupported version')
    clues = _index(routes['decision_clues'], 'id', 'decision_clues')
    for clue in clues.values():
        _obj(clue, ['id', 'evidence_id', 'source_quote', 'structure_or_method', 'physical_effect',
                    'conditions_and_unknowns', 'goal_ids', 'inference_bridge'], 'decision_clue')
        eid = clue['evidence_id']
        if not isinstance(eid, str) or eid not in evidence:
            _fail('decision_clue.evidence_id', 'unknown evidence ID')
        item = evidence[eid]
        if item.get('eligible_as_evidence') is not True or not isinstance(item.get('text'), str) or not item['text'].strip() or not isinstance(item.get('source_id'), str) or not item['source_id'].strip():
            _fail('decision_clue.evidence_id', 'requires eligible source-linked nonempty evidence text')
        for key in ('source_quote', 'structure_or_method', 'physical_effect', 'inference_bridge'):
            _text(clue[key], 'decision_clue.' + key)
        if clue['source_quote'] not in item['text']:
            _fail('decision_clue.source_quote', 'must be an exact span of evidence text')
        _list(clue['conditions_and_unknowns'], 'conditions_and_unknowns')
        for value in clue['conditions_and_unknowns']:
            _text(value, 'conditions_and_unknowns')
        _refs(clue['goal_ids'], goals, 'decision_clue.goal_ids', 1)
    _index(routes['routes'], 'id', 'routes')
    for route in routes['routes']:
        _obj(route, ['id', 'claim', 'clue_ids', 'evidence_ids', 'goal_ids', 'transfer_assumptions',
            'compatibility_conditions', 'family', 'instances', 'expectations', 'open_requirements', 'readiness']
            + (['basis_mode', 'model_knowledge_rationale'] if routes['schema_version'] == SCHEMA else []), 'route')
        mode = route.get('basis_mode', 'external_evidence')
        if mode not in ('external_evidence', 'model_knowledge', 'hybrid'):
            _fail('route.basis_mode', 'requires external_evidence, model_knowledge or hybrid')
        if routes['schema_version'] == SCHEMA:
            _text(route['model_knowledge_rationale'], 'route.model_knowledge_rationale', nullable=True)
            if mode == 'external_evidence' and route['model_knowledge_rationale'] is not None:
                _fail('route.model_knowledge_rationale', 'external_evidence uses null; select hybrid for a separate model-knowledge basis')
        if mode == 'model_knowledge' and (route['clue_ids'] != [] or route['evidence_ids'] != []):
            _fail('route.basis_mode', 'model_knowledge cannot claim external citations; select hybrid to cite evidence')
        _text(route['claim'], 'route.claim', nullable=True)
        _refs(route['clue_ids'], clues, 'route.clue_ids')
        _refs(route['evidence_ids'], evidence, 'route.evidence_ids')
        _refs(route['goal_ids'], goals, 'route.goal_ids')
        linked = [clues[c] for c in route['clue_ids']]
        if set(route['evidence_ids']) != {c['evidence_id'] for c in linked}:
            _fail('route.evidence_ids', 'must equal the evidence IDs of the cited clues')
        if any(not set(c['goal_ids']).intersection(route['goal_ids']) for c in linked):
            _fail('route.clue_ids', 'each clue must relate to a route goal')
        for key in ('transfer_assumptions', 'open_requirements'):
            _list(route[key], key)
            for value in route[key]:
                _text(value, key)
        _list(route['compatibility_conditions'], 'compatibility_conditions')
        for condition in route['compatibility_conditions']:
            _obj(condition, ['goal_id', 'field', 'required_value', 'reason'], 'compatibility_condition')
            if condition['goal_id'] not in route['goal_ids'] or condition['field'] not in ['wavelength_nm', 'angle_deg', 'polarization']:
                _fail('compatibility_condition', 'requires route goal and a supported region field')
            _text(condition['reason'], 'compatibility_condition.reason')
        if route['family'] is not None:
            _family(route['family'], design_constraints, 'route.family')
            instances = _instances(route['instances'], route['family'], 'route.instances')
        else:
            if route['instances'] != []:
                _fail('route.instances', 'instances require a family')
            instances = {}
        _list(route['expectations'], 'expectations')
        for expectation in route['expectations']:
            _obj(expectation, ['goal_id', 'metric', 'baseline_instance_id', 'candidate_instance_id',
                'controlled_conditions', 'changed_variables', 'direction', 'reason'], 'expectation')
            gid = expectation['goal_id']
            if gid not in route['goal_ids']:
                _fail('expectation.goal_id', 'requires existing route goal')
            if expectation['metric'] != goals[gid]['executable_metric']:
                _fail('expectation.metric', 'must inherit complete task metric, region and reduction exactly')
            for key in ('baseline_instance_id', 'candidate_instance_id'):
                if not isinstance(expectation[key], str) or expectation[key] not in instances:
                    _fail('expectation.' + key, 'requires an instance of this family')
            baseline = instances[expectation['baseline_instance_id']]['parameters_nm']
            candidate = instances[expectation['candidate_instance_id']]['parameters_nm']
            changed = {key for key in baseline if baseline[key] != candidate[key]}
            if not changed:
                _fail('expectation.changed_variables', 'actual changed variables are empty: baseline and candidate parameters are identical; instance names cannot establish an intervention')
            _refs(expectation['changed_variables'], changed, 'expectation.changed_variables', 1)
            if set(expectation['changed_variables']) != changed:
                _fail('expectation.changed_variables', 'must identify all actual parameter changes')
            _text(expectation['controlled_conditions'], 'expectation.controlled_conditions')
            _text(expectation['reason'], 'expectation.reason')
            if expectation['direction'] not in ['increase', 'decrease', 'unchanged']:
                _fail('expectation.direction', 'requires an untested qualitative direction')
        expected = 'needs_revision' if revision_reasons(route, task_sheet, design_constraints) else 'ready'
        if route['readiness'] != expected:
            _fail('route.readiness', 'must be ' + expected)
    return routes


def materialize(routes, route_id, task_sheet, evidence_items, *, design_constraints):
    """Return deterministic concrete stacks with inherited metrics, without execution."""
    validate(routes, task_sheet, evidence_items, design_constraints=design_constraints)
    matches = [r for r in routes['routes'] if r['id'] == route_id]
    if not matches:
        _fail('route_id', 'unknown route')
    route = matches[0]
    if route['readiness'] != 'ready':
        _fail('route.readiness', 'needs_revision; materialization blocked')
    family = route['family']
    goals = {g['id']: g for g in task_sheet['subgoals']}
    output = []
    for instance in route['instances']:
        layers = []
        for layer in family['layers']:
            thickness = layer['thickness_nm']
            if isinstance(thickness, str):
                thickness = instance['parameters_nm'][thickness]
            layers.append({'material': layer['material'], 'thickness_nm': thickness})
        output.append({'status': 'prepared', 'verification_scope': 'syntax_and_provenance_only',
            'execution_authorized': False, 'design_constraints': copy.deepcopy(design_constraints),
            'task_explicit_constraints': copy.deepcopy(task_sheet['explicit_constraints']), 'route_id': route_id, 'instance_id': instance['id'],
            'incident_medium': family['incident_medium'], 'substrate': family['substrate'], 'layers': layers,
            'targeted_goal_ids': list(route['goal_ids']),
            'basis_mode': route.get('basis_mode', 'external_evidence'),
            'model_knowledge_rationale': route.get('model_knowledge_rationale'),
            'clue_ids': list(route['clue_ids']), 'evidence_ids': list(route['evidence_ids']),
            'objectives': [copy.deepcopy(goal['executable_metric']) for goal in task_sheet['subgoals']]})
    return output


def build_prompt(task_sheet, evidence_items, *, design_constraints):
    # 未来经验输入尚未实现：按条件参考真实研究的成功与失败，不盲目复用路线。
    # 模型内部知识不是实测经验；来源、预期与实际结果应明确区分。
    # 数据结构、索引及检索算法仍待讨论，见 FUTURE_LOCAL_EXPERIENCE_MEMO.md。
    _inputs(task_sheet, evidence_items, design_constraints)
    system = (ROOT / 'research_routes_prompt.txt').read_text(encoding='utf-8')
    example = (ROOT / 'examples' / 'research_routes_example.json').read_text(encoding='utf-8')
    system += '\nSCHEMA EXAMPLE (illustrative, not evidence; never copy its evidence IDs):\n' + example
    return {'system_prompt': system, 'user_payload': {'task_sheet': task_sheet,
        'evidence_items': prompt_evidence_view(evidence_items), 'design_constraints': design_constraints},
        'prompt_hash': hashlib.sha256(system.encode()).hexdigest()}


render_prompt = build_prompt


def generate(task_sheet, evidence_items, planner, *, design_constraints, max_repairs=1):
    """Explicit, injected planner call; no implicit model or simulation execution."""
    # 未来可沉淀真实执行的条件、路线、预期、实际结果、原因与修订建议。
    # 此处生成提案不代表取得实测经验；失败也有价值，须保留适用边界。
    # 当前只留备注，不实现经验读写、不扩展本次 T022 联调范围。
    from optomind_research.query_planner import _extract_json_object
    if type(max_repairs) is not int or max_repairs not in (0, 1):
        raise ValueError('max_repairs must be 0 or 1')
    prompt = build_prompt(task_sheet, evidence_items, design_constraints=design_constraints)
    original_payload = copy.deepcopy(prompt['user_payload'])
    outcome = {'status': 'invalid', 'research_routes': {}, 'rounds': [], 'round_usages': [],
        'transport': {}, 'errors': [], 'prompt_hash': prompt['prompt_hash']}
    for number in range(1, max_repairs + 2):
        try:
            raw = planner._call_query_plan_model(prompt)
        except Exception as exc:
            # Preserve failure category without persisting possible credentials from a transport error.
            outcome['errors'] = ['planner call failed: ' + type(exc).__name__]
            outcome['status'] = 'transport_rejected'
            break
        usage = dict(planner.last_usage or {})
        transport = dict(planner.last_transport or {})
        record = {'round': number, 'raw_response': raw, 'effective_response': None,
            'usage': usage, 'transport': transport}
        outcome['rounds'].append(record)
        outcome['round_usages'].append(usage)
        outcome['transport'] = transport
        if usage.get('mock_llm') or usage.get('mock'):
            outcome['status'] = 'mock_rejected'
            break
        if transport.get('fallback_used') or transport.get('transport_error'):
            outcome.update(status='transport_rejected', errors=['api_error'])
            break
        try:
            parsed = _extract_json_object(raw)
            record['raw_research_routes'] = copy.deepcopy(parsed)
            if number > 1:
                previous = outcome['rounds'][-2].get('raw_research_routes')
                record['repair_audit'] = contract_change_audit(previous, parsed)
                record['repair_audit']['previous_contract_parseable'] = previous is not None
            validate(parsed, task_sheet, evidence_items, design_constraints=design_constraints)
            record.update(ok=True, effective_response=copy.deepcopy(parsed))
            status = 'ok' if parsed['routes'] and all(r['readiness'] == 'ready' for r in parsed['routes']) else 'needs_revision'
            outcome.update(status=status, research_routes=parsed, errors=[])
            break
        except (ValueError, TypeError, KeyError) as exc:
            record.update(ok=False, error=str(exc))
            outcome['errors'] = [str(exc)]
            prompt = {**prompt, 'user_payload': {**original_payload, 'previous_response': raw,
                'validation_error': str(exc), 'instruction': 'Repair the full contract with the smallest justified changes. This is a contract repair, not necessarily a format-only repair: preserve valid scientific content, and do not invent a new physical explanation merely to satisfy syntax. Keep claim, actual intervention and expectation consistent. Never invent evidence, task targets or executed results. Keep incomplete routes needs_revision.'}}
    outcome['revision_rounds'] = max(0, len(outcome['rounds']) - 1)
    outcome['first_hit'] = outcome['status'] == 'ok' and len(outcome['rounds']) == 1
    return outcome
