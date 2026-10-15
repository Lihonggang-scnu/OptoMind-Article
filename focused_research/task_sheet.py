"""Provisional fixed-domain task contract and lossless retrieval adapter.

This validates structure/provenance, not scientific truth or search relevance.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

SCHEMA = 'focused_research.task_sheet.v2'
ADAPTER_SCHEMA = 'focused_research.task_queries.v1'
TASK_TYPES = ['design/optimization', 'explanation', 'diagnosis', 'comparison', 'feasibility', 'mixed/unresolved']
ENDPOINTS = {'paper_search': 'keyword_queries', 'snippet_search': 'question_queries'}
METRICS = {'mean_reflectance', 'mean_transmittance', 'mean_absorption'}
MEAN_REDUCTIONS = {'sample_mean', 'wavelength_integral_mean'}
PURPOSES = ['analogous_case', 'material_structure', 'method_transfer', 'mechanism']
ROOT = Path(__file__).resolve().parent


def validate(sheet, *, question=None):
    """Reject rather than normalize, truncate, invent, or silently repair values."""
    def fail(path, reason):
        raise ValueError(f'task sheet {path}: {reason}')
    def obj(value, keys, path):
        if not isinstance(value, dict) or set(value) != set(keys):
            fail(path, 'requires exactly ' + ', '.join(keys))
    def text(value, path, nullable=False):
        if nullable and value is None:
            return
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            fail(path, 'requires nonempty unpadded text')
    def rows(value, path, minimum=0):
        if not isinstance(value, list) or len(value) < minimum:
            fail(path, 'requires a list with at least ' + str(minimum) + ' entries')
    def identifiers(items, keys, path):
        rows(items, path)
        ids = set()
        for row in items:
            obj(row, keys, path)
            ident = row['id']
            if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', ident) or ident in ids:
                fail(path, 'IDs must be unique simple identifiers')
            ids.add(ident)
        return ids
    def refs(values, allowed, path, minimum=0):
        rows(values, path, minimum)
        if any(not isinstance(v, str) or v not in allowed for v in values) or len(values) != len(set(values)):
            fail(path, 'requires unique references to existing IDs')
    def span(value, path):
        text(value, path)
        if value not in sheet['original_question']:
            fail(path, 'must be an exact span of original_question')

    obj(sheet, ['schema_version', 'original_question', 'task_type', 'user_objective', 'subgoals', 'application_intents', 'unsupported_requirements', 'readiness',
        'goal_relationships', 'explicit_constraints', 'known_data', 'unknowns_assumptions',
        'research_questions', 'candidate_mechanisms', 'search_queries'], '$')
    if sheet['schema_version'] != SCHEMA:
        fail('schema_version', 'unsupported version')
    if not isinstance(sheet['original_question'], str) or not sheet['original_question'].strip():
        fail('original_question', 'requires the complete nonempty input text')
    if question is not None and sheet['original_question'] != question:
        fail('original_question', 'must preserve the full input exactly')
    if sheet['task_type'] not in TASK_TYPES:
        fail('task_type', 'unsupported task type')
    text(sheet['user_objective'], 'user_objective')
    goals = identifiers(sheet['subgoals'], ['id', 'objective', 'original_span', 'operational_definition', 'executable_metric'], 'subgoals')
    for row in sheet['subgoals']:
        text(row['objective'], 'subgoal.objective'); span(row['original_span'], 'subgoal.original_span')
        text(row['operational_definition'], 'subgoal.operational_definition', nullable=True)
        metric = row['executable_metric']
        obj(metric, ['objective_id', 'metric', 'sense', 'mean_reduction', 'region'], 'executable_metric')
        if metric['objective_id'] != row['id']:
            fail('executable_metric.objective_id', 'must equal the subgoal ID')
        if not isinstance(metric['metric'], str) or metric['metric'] not in METRICS:
            fail('executable_metric.metric', 'unsupported metric; preserve requirement under unsupported_requirements')
        if metric['sense'] not in ['minimize', 'maximize']:
            fail('executable_metric.sense', 'requires minimize or maximize')
        if metric['mean_reduction'] not in list(MEAN_REDUCTIONS):
            fail('executable_metric.mean_reduction', 'requires an explicit supported reduction')
        region = metric['region']
        obj(region, ['wavelength_nm', 'angle_deg', 'polarization'], 'executable_metric.region')
        band = region['wavelength_nm']
        def finite_number(value):
            return type(value) in (int, float) and math.isfinite(value)
        if (not isinstance(band, list) or len(band) != 2
                or not all(finite_number(v) for v in band) or not 0 < band[0] < band[1]):
            fail('executable_metric.region.wavelength_nm', 'requires positive finite lo < hi in nm')
        if not finite_number(region['angle_deg']) or not 0 <= region['angle_deg'] < 90:
            fail('executable_metric.region.angle_deg', 'requires an explicit finite angle in [0, 90)')
        if region['polarization'] not in ['s', 'p', 'unpolarized']:
            fail('executable_metric.region.polarization', 'requires explicit s, p or unpolarized')
    # Only obvious textual duplicates can be rejected mechanically; semantic
    # redundancy and whether a definition is justified still require review.
    objectives = [' '.join(row['objective'].casefold().split()) for row in sheet['subgoals']]
    if len(objectives) != len(set(objectives)):
        fail('subgoals', 'duplicate objectives are not separate score targets')
    metric_signatures = []
    for row in sheet['subgoals']:
        metric = row['executable_metric']
        region = metric['region']
        metric_signatures.append((metric['metric'], metric['sense'], metric['mean_reduction'],
            tuple(float(v) for v in region['wavelength_nm']), float(region['angle_deg']),
            region['polarization']))
    if len(metric_signatures) != len(set(metric_signatures)):
        fail('subgoals', 'duplicate executable metrics are not separate score targets')
    identifiers(sheet['application_intents'], ['id', 'intent', 'original_span',
        'subgoal_ids', 'operational_definition'], 'application_intents')
    for row in sheet['application_intents']:
        text(row['intent'], 'application_intent.intent')
        span(row['original_span'], 'application_intent.original_span')
        refs(row['subgoal_ids'], goals, 'application_intent.subgoal_ids', 1)
        text(row['operational_definition'], 'application_intent.operational_definition', nullable=True)
    identifiers(sheet['unsupported_requirements'], ['id', 'requirement', 'original_span', 'reason'],
        'unsupported_requirements')
    for row in sheet['unsupported_requirements']:
        text(row['requirement'], 'unsupported.requirement')
        span(row['original_span'], 'unsupported.original_span')
        text(row['reason'], 'unsupported.reason')
    expected_readiness = 'needs_revision' if sheet['unsupported_requirements'] or not goals else 'ready'
    if sheet['readiness'] != expected_readiness:
        fail('readiness', 'must be ' + expected_readiness)
    rel = sheet['goal_relationships']
    obj(rel, ['mode', 'priority_order', 'tradeoff', 'coupling'], 'goal_relationships')
    if rel['mode'] not in ['all', 'any', 'priorities', 'tradeoff', 'mixed/unresolved']:
        fail('goal_relationships.mode', 'unsupported mode')
    if rel['priority_order'] is not None:
        refs(rel['priority_order'], goals, 'priority_order', 1)
    text(rel['tradeoff'], 'tradeoff', nullable=True)
    text(rel['coupling'], 'coupling', nullable=True)
    if rel['mode'] in ['all', 'any'] and (rel['priority_order'] is not None or rel['tradeoff'] is not None):
        fail('goal_relationships', 'all/any cannot declare priorities or a tradeoff')
    if rel['mode'] == 'priorities' and rel['priority_order'] is None:
        fail('priority_order', 'required for priorities')
    if rel['mode'] == 'tradeoff' and rel['tradeoff'] is None:
        fail('tradeoff', 'required for tradeoff')
    identifiers(sheet['explicit_constraints'], ['id', 'constraint', 'original_span'], 'explicit_constraints')
    for row in sheet['explicit_constraints']:
        text(row['constraint'], 'constraint'); span(row['original_span'], 'constraint.original_span')
    rows(sheet['known_data'], 'known_data')
    for row in sheet['known_data']:
        obj(row, ['statement', 'original_span'], 'known_data')
        text(row['statement'], 'known_data.statement'); span(row['original_span'], 'known_data.original_span')
    rows(sheet['unknowns_assumptions'], 'unknowns_assumptions')
    for row in sheet['unknowns_assumptions']:
        obj(row, ['kind', 'statement', 'assumed_value', 'needs_user_input'], 'unknowns_assumptions')
        if row['kind'] not in ['unknown', 'assumption'] or type(row['needs_user_input']) is not bool:
            fail('unknowns_assumptions', 'invalid kind or boolean')
        text(row['statement'], 'unknown.statement')
        text(row['assumed_value'], 'assumed_value', nullable=True)
        if row['kind'] == 'unknown' and row['assumed_value'] is not None:
            fail('assumed_value', 'unknowns cannot assert a value')
    research = identifiers(sheet['research_questions'], ['id', 'question', 'subgoal_ids'], 'research_questions')
    if goals and not research:
        fail('research_questions', 'at least one research question required for supported goals')
    for row in sheet['research_questions']:
        text(row['question'], 'research_question'); refs(row['subgoal_ids'], goals, 'research.subgoal_ids', 1)
    mechanisms = identifiers(sheet['candidate_mechanisms'], ['id', 'mechanism', 'subgoal_ids', 'status'], 'candidate_mechanisms')
    for row in sheet['candidate_mechanisms']:
        text(row['mechanism'], 'mechanism'); refs(row['subgoal_ids'], goals, 'mechanism.subgoal_ids', 1)
        if row['status'] != 'candidate':
            fail('mechanism.status', 'mechanisms are candidates, not established conclusions')
    queries = sheet['search_queries']
    identifiers(queries, ['id', 'endpoint', 'query', 'subgoal_ids', 'mechanism_ids',
        'research_question_ids', 'purpose', 'rationale'], 'search_queries')
    if not (1 if goals else 0) <= len(queries) <= 6:
        fail('search_queries', 'requires 1..6 queries for supported goals; zero allowed only without goals')
    covered = set()
    for row in queries:
        if row['endpoint'] not in list(ENDPOINTS) or row['purpose'] not in PURPOSES:
            fail('search_queries', 'unsupported endpoint or purpose')
        text(row['query'], 'query'); text(row['rationale'], 'rationale')
        refs(row['subgoal_ids'], goals, 'query.subgoal_ids', 1)
        refs(row['mechanism_ids'], mechanisms, 'query.mechanism_ids')
        refs(row['research_question_ids'], research, 'query.research_question_ids', 1)
        if row['endpoint'] == 'paper_search' and row['query'].endswith('?'):
            fail('query', 'paper_search expects a keyword phrase, not a natural question')
        if row['endpoint'] == 'snippet_search' and not row['query'].endswith('?'):
            fail('query', 'snippet_search expects a natural-language question')
        linked_research = [r for r in sheet['research_questions'] if r['id'] in row['research_question_ids']]
        linked_mechanisms = [m for m in sheet['candidate_mechanisms'] if m['id'] in row['mechanism_ids']]
        query_goals = set(row['subgoal_ids'])
        if any(not query_goals.intersection(r['subgoal_ids']) for r in linked_research + linked_mechanisms):
            fail('search_queries', 'linked research questions/mechanisms must share a query subgoal')
        if not query_goals.issubset({g for r in linked_research for g in r['subgoal_ids']}):
            fail('search_queries', 'research question links must cover query subgoals')
        covered.update(row['subgoal_ids'])
    if covered != goals:
        fail('search_queries', 'every user subgoal needs retrieval coverage')
    return sheet


def adapt(sheet):
    """One execution facet per query; no query rewriting or hidden fan-out."""
    validate(sheet)
    if sheet['readiness'] != 'ready':
        raise ValueError('task sheet needs_revision: unsupported or insufficiently specified requirements')
    facets = []
    mapping = {}
    for row in sheet['search_queries']:
        key = ENDPOINTS[row['endpoint']]
        facet = {'id': row['id'], 'ask': row['rationale'], 'keyword_queries': [],
            'question_queries': [], 'filters': {'publication_type': [], 'fields_of_study': [],
            'text_availability': []}, 'must_exclude': []}
        facet[key] = [row['query']]
        facets.append(facet)
        suffix = 'KQ' if row['endpoint'] == 'paper_search' else 'QQ'
        mapping[f"{row['id']}_{suffix}_01"] = dict(row)
    return {'schema_version': ADAPTER_SCHEMA, 'question_en': sheet['user_objective'],
        'facets': facets, 'additional_constraints': [],
        'criteria': {'must_include_topic': [], 'must_exclude_domain': [], 'synonyms': {}},
        'task_sheet': sheet, 'task_query_mappings': mapping}


def validate_adapter(plan):
    if plan != adapt(plan.get('task_sheet')):
        raise ValueError('retrieval adapter differs from validated task-sheet queries')
    return plan


def render_prompt(question):
    system = (ROOT / 'task_sheet_prompt.txt').read_text(encoding='utf-8')
    example = json.loads((ROOT / 'examples' / 'rgb_nir_task_sheet.json').read_text(encoding='utf-8'))
    example_question = example.pop('original_question')
    system += '\nWORKED EXAMPLE INPUT:\n' + example_question
    system += '\nWORKED EXAMPLE ANALYSIS OUTPUT (all shown fields required):\n' + json.dumps(
        example, ensure_ascii=False, indent=2)
    return {'system_prompt': system, 'user_payload': {'original_question': question},
        'prompt_hash': hashlib.sha256(system.encode()).hexdigest()}


def generate(question, planner):
    """Use existing Qwen transport/policy; at most one explicit contract repair."""
    from optomind_research.query_planner import _extract_json_object
    prompt = render_prompt(question)
    outcome = {'status': 'invalid', 'plan': {}, 'task_sheet': {}, 'rounds': [],
        'round_usages': [], 'transport': {}, 'errors': [], 'prompt_hash': prompt['prompt_hash']}
    for number in (1, 2):
        raw = planner._call_query_plan_model(prompt)
        usage = dict(planner.last_usage or {})
        transport = dict(planner.last_transport or {})
        record = {'round': number, 'raw_response': raw, 'usage': usage, 'transport': transport}
        outcome['rounds'].append(record); outcome['round_usages'].append(usage)
        outcome['transport'] = transport
        if usage.get('mock_llm') or usage.get('mock'):
            outcome['status'] = 'mock_rejected'; break
        if transport.get('fallback_used') or transport.get('transport_error'):
            outcome['status'] = 'transport_rejected'; outcome['errors'] = ['api_error']; break
        try:
            analysis = _extract_json_object(raw)
            record['raw_task_sheet'] = analysis
            # Preserve model output for audit; the input is program-owned.
            record['ignored_model_fields'] = ['original_question'] if 'original_question' in analysis else []
            sheet = {**analysis, 'original_question': question}
            validate(sheet, question=question)
            if sheet['readiness'] == 'needs_revision':
                outcome.update(status='needs_revision', task_sheet=sheet, plan={}, errors=[])
            else:
                outcome.update(status='ok', task_sheet=sheet, plan=adapt(sheet), errors=[])
            record['ok'] = True
            break
        except (ValueError, TypeError) as exc:
            record.update(ok=False, error=str(exc))
            outcome['errors'] = [str(exc)]
            prompt = {**prompt, 'user_payload': {'original_question': question,
                'previous_response': raw, 'validation_error': str(exc),
                'instruction': 'Repair this contract without changing user constraints or inventing facts. Return the full analysis JSON without original_question.'}}
    outcome['revision_rounds'] = len(outcome['rounds']) - 1
    outcome['first_hit'] = outcome['status'] == 'ok' and len(outcome['rounds']) == 1
    return outcome
