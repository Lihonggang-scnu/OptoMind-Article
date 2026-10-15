"""Provisional fixed-domain task contract and lossless retrieval adapter.

This validates structure/provenance, not scientific truth or search relevance.
"""
from __future__ import annotations

import copy
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
UNVERIFIED_BACKGROUND = (
    'Unverified model interpretation (not user-provided or accepted truth; '
    'original_span is not an exact native-question quote): ')


def prepare_analysis(question, analysis):
    """Attach native input and audit a narrow, lossless metadata demotion.

    Nonexact quotations in optional known_data are not established user facts.
    Keep their statements as explicitly unverified model assumptions instead of
    paying for another planning round. Raw rows remain in the audit; no quote is
    reconstructed. Incomplete optional research-question links are retained with
    an advisory, never invented. Executable goals, constraints and query text are
    untouched and still require validate(). Repeated preparation is idempotent;
    retained metadata advisories repeat, while completed demotions do not.
    """
    if not isinstance(analysis, dict):
        raise ValueError('task sheet $: requires an analysis object')
    sheet = copy.deepcopy(analysis)
    sheet['original_question'] = question
    audit = []
    known = sheet.get('known_data')
    assumptions = sheet.get('unknowns_assumptions')

    def valid_text(value):
        return isinstance(value, str) and bool(value.strip()) and value == value.strip()

    if isinstance(question, str) and isinstance(known, list) and isinstance(assumptions, list):
        retained = []
        for index, row in enumerate(known):
            # Only the provenance mismatch is tolerated. Malformed rows and
            # malformed assumption containers remain visible to the validator.
            if (isinstance(row, dict) and set(row) == {'statement', 'original_span'}
                    and valid_text(row['statement']) and valid_text(row['original_span'])
                    and row['original_span'] not in question):
                assumption = {'kind': 'assumption',
                    'statement': UNVERIFIED_BACKGROUND + row['statement'],
                    'assumed_value': None, 'needs_user_input': False}
                audit.append({'operation': 'demote_unverified_known_data',
                    'source_path': f'known_data[{index}]',
                    'destination_path': f'unknowns_assumptions[{len(assumptions)}]',
                    'reason': 'original_span_not_exact_native_question',
                    'raw_row': copy.deepcopy(row),
                    'effective_row': copy.deepcopy(assumption)})
                assumptions.append(assumption)
            else:
                retained.append(row)
        sheet['known_data'] = retained
    research = sheet.get('research_questions')
    queries = sheet.get('search_queries')
    if isinstance(research, list) and isinstance(queries, list):
        linked_goals = {row['id']: row['subgoal_ids'] for row in research
            if isinstance(row, dict) and isinstance(row.get('id'), str)
            and isinstance(row.get('subgoal_ids'), list)
            and all(isinstance(goal, str) for goal in row['subgoal_ids'])}
        for index, row in enumerate(queries):
            if not isinstance(row, dict):
                continue
            refs, goals = row.get('research_question_ids'), row.get('subgoal_ids')
            if (not isinstance(refs, list) or not isinstance(goals, list)
                    or any(not isinstance(ref, str) or ref not in linked_goals for ref in refs)
                    or any(not isinstance(goal, str) for goal in goals)):
                continue
            covered = {goal for ref in refs for goal in linked_goals[ref]}
            missing = [goal for goal in goals if goal not in covered]
            if missing:
                audit.append({'operation': 'retain_optional_query_links',
                    'source_path': f'search_queries[{index}].research_question_ids',
                    'reason': 'research_question_links_do_not_cover_all_query_subgoals',
                    'raw_value': copy.deepcopy(refs), 'effective_value': copy.deepcopy(refs),
                    'uncovered_subgoal_ids': missing,
                    'status': 'advisory_not_evidence_of_relevance'})
    return sheet, audit


def validate(sheet, *, question=None):
    """Validate without mutation; optional provenance demotion is a separate audit."""
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
    if rel['mode'] in ['all', 'any'] and rel['priority_order'] is not None:
        fail('goal_relationships', 'all/any cannot declare priorities')
    # Seeking all goals is compatible with a model's description of possible
    # physical tension. This prose does not define weights, a compromise rule,
    # or a new executable objective; subgoal metrics remain authoritative.
    if rel['mode'] == 'any' and rel['tradeoff'] is not None:
        fail('goal_relationships', 'any cannot declare a tradeoff')
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
        refs(row['research_question_ids'], research, 'query.research_question_ids')
        if row['endpoint'] == 'paper_search' and row['query'].endswith('?'):
            fail('query', 'paper_search expects a keyword phrase, not a natural question')
        if row['endpoint'] == 'snippet_search' and not row['query'].endswith('?'):
            fail('query', 'snippet_search expects a natural-language question')
        linked_research = [r for r in sheet['research_questions'] if r['id'] in row['research_question_ids']]
        linked_mechanisms = [m for m in sheet['candidate_mechanisms'] if m['id'] in row['mechanism_ids']]
        query_goals = set(row['subgoal_ids'])
        if any(not query_goals.intersection(r['subgoal_ids']) for r in linked_research + linked_mechanisms):
            fail('search_queries', 'linked research questions/mechanisms must share a query subgoal')
        # Direct query-to-subgoal coverage is authoritative. Missing or partial
        # optional research-question links are audited by prepare_analysis;
        # filling them would invent associations without improving execution.
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
            sheet, audit = prepare_analysis(question, analysis)
            record['normalization_audit'] = audit
            record['effective_task_sheet'] = copy.deepcopy(sheet)
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
