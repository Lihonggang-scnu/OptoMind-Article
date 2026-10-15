"""Read-only acceptance audit of an actually generated RGB/NIR-v2 sheet."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t019a')
RUN = ROOT / 'runs/real-plan-01'
def load(p):
    return json.loads(p.read_text(encoding='utf-8-sig'))

question = load(SOURCE / 'focused_research/examples/rgb_nir_question.v2.json')['original_question']
sheet = load(RUN / 'task_sheet.json')
planning = load(RUN / 'planning.json')
query_plan = load(RUN / 'query_plan.json')
queries = load(RUN / 'queries.json')
sys.path[:0] = [str(SOURCE / 'focused_research/vendor/review'), str(SOURCE / 'focused_research')]
import task_sheet

checks = {'actual_planning_status_ok': planning.get('status') == 'ok',
          'original_question_verbatim': sheet.get('original_question') == question,
          'readiness_ready': sheet.get('readiness') == 'ready',
          'unsupported_requirements_empty': sheet.get('unsupported_requirements') == [],
          'only_G1_G2': {s.get('id') for s in sheet.get('subgoals', [])} == {'G1', 'G2'}
                        and len(sheet.get('subgoals', [])) == 2}
validation_error = None
try:
    task_sheet.validate(sheet, question=question)
except (TypeError, ValueError) as exc:
    validation_error = str(exc)
checks['production_validator'] = validation_error is None
expected = {'G1': ('mean_reflectance', [450, 650]),
            'G2': ('mean_transmittance', [700, 1000])}
for subgoal in sheet.get('subgoals', []):
    goal = subgoal.get('id')
    metric = subgoal.get('executable_metric') or {}
    region = metric.get('region') or {}
    if goal in expected:
        name, band = expected[goal]
        checks[goal + '_requested_metric'] = (
            metric.get('objective_id') == goal and metric.get('metric') == name
            and metric.get('sense') == 'maximize' and region.get('wavelength_nm') == band
            and region.get('angle_deg') == 45 and region.get('polarization') == 'unpolarized'
            and metric.get('mean_reduction') == 'wavelength_integral_mean')

spans = [row.get('original_span', '') for row in sheet.get('explicit_constraints', [])]
required = ['450-650 nm', '700-1000 nm', '45 度入射', '熔融石英基底',
            '仅使用 TiO2 和 SiO2', '平面多层介质膜', '膜系总层数不超过 24 层',
            '非偏振光（s、p 偏振等权平均）',
            '各波段目标均定义为按波长积分后除以波段宽度的平均值']
coverage = {span: any(span in original for original in spans) for span in required}
checks['all_explicit_request_spans_retained'] = all(coverage.values())
relationships = sheet.get('goal_relationships') or {}
checks['no_invented_priority_or_tradeoff'] = (
    relationships.get('mode') == 'all' and relationships.get('priority_order') is None
    and relationships.get('tradeoff') is None)
checks['queries_within_contract'] = 1 <= len(sheet.get('search_queries', [])) <= 6
checks['adapted_plan_matches_generated_sheet'] = (
    validation_error is None and task_sheet.adapt(sheet) == query_plan)
actual_query_texts = [row.get('query_text') for row in queries]
declared_query_texts = [row.get('query') for row in sheet.get('search_queries', [])]
checks['exact_query_texts_preserved'] = actual_query_texts == declared_query_texts
checks['query_links_preserved'] = all(
    row.get('task_query') == query_plan.get('task_query_mappings', {}).get(row.get('query_id'))
    for row in queries)
report = {'task_id': 'R3-T019-DEDICATED20',
          'source_sha': '713799fb55899b78427aed56cb643bc72526c19d',
          'checks': checks, 'constraint_span_coverage': coverage,
          'validation_error': validation_error,
          'programmatic_acceptance_passed': all(checks.values()),
          'rounds': len(planning.get('rounds', [])),
          'first_hit': planning.get('first_hit'),
          'model_usages': planning.get('round_usages', []),
          'query_count': len(sheet.get('search_queries', [])),
          'sheet_sha256': hashlib.sha256((RUN / 'task_sheet.json').read_bytes()).hexdigest(),
          'evidence_limits': [
              'Parent semantic review required; source-span containment alone is not semantic proof',
              'The frozen prompt includes a worked example of this exact demonstration question; successful generation is not an unseen-task generalization claim',
              'No retrieval relevance, physical feasibility, s/p forward calculation or coating optimization established']}
with (ROOT / 'TASK_SHEET_AUDIT.json').open('x', encoding='utf-8', newline='\n') as handle:
    json.dump(report, handle, ensure_ascii=False, indent=2)
    handle.write('\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
