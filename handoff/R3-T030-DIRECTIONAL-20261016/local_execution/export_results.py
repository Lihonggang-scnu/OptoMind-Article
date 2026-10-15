"""Compact real-evidence export; complete originals remain in task directories."""
from pathlib import Path
from collections import Counter
from decimal import Decimal
import hashlib
import json
import shutil

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t030a')
OUT = SOURCE/'handoff'/'R3-T030-DIRECTIONAL-20261016'
OUT.mkdir(parents=True,exist_ok=True)


def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,obj):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def copy(src,dst):
    dst=Path(dst);dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)


archive=load(ROOT/'search'/'design_archive.json')
previous=load(ROOT.parent/'R3-T028'/'refined'/'design_archive.json')
feedback=load(ROOT/'search'/'branch_feedback.json')
fresh=set(archive['designs'])-set(previous['designs'])
counts=Counter()
for b in feedback['branches']:counts.update(b['outcome_counts_unique_certified_designs'])
summary=load(ROOT/'search'/'summary.json')
flat=[]
for ident,d in archive['designs'].items():
    flat.append({'design_id':ident,'new_in_T030':ident in fresh,'parent_design_id':d.get('parent_design_id'),
                 'metrics':d['metrics'],'layers':d['physical_input']['stack']['layers'],
                 'first_evaluation':d.get('first_evaluation')})
save(OUT/'science'/'CERTIFIED_DESIGN_METRICS.json',{'objectives':archive['objectives'],
      'context_id':archive['context_id'],'claim_scope':archive['claim_scope'],
      'pareto_design_ids':archive['pareto_design_ids'],'designs':flat})
chains=[]
for b in feedback['branches']:
    for child in b['selected_certified_designs']:
        chains.append({'branch_id':b['branch_id'],'plan_index':b['plan_index'],
            'parent_design_id':b['parent_design_id'],'parent_metrics':b['parent_metrics'],
            'hypothesis_rationale':b['rationale'],'model_parameters_nm':b['parameters_nm'],
            'actual_search_bounds_nm':b['search_bounds_nm'],'child':child})
save(OUT/'science'/'REPRESENTATIVE_CHAINS.json',chains)
selected={c['parent_design_id'] for c in chains}|{c['child']['design_id'] for c in chains}
save(OUT/'science'/'REPRESENTATIVE_CERTIFIED_DESIGNS.json',
     {'selection':'Actual per-branch objective extrema and their original parents; no fictitious joint optimum.',
      'designs':{k:archive['designs'][k] for k in sorted(selected)}})

for folder,names in {
    'expansion':None,
    'reflection':None,
    'search':['summary.json','manifest.json','branch_feedback.json','branch_reservations.json',
        'input_failures.json','candidate_report.json','certification_selection.json','input_bindings.json',
        'expansion_report.json','search_parent.json','task_sheet.json','design_constraints.json',
        'optical_media.json','wavelengths_nm.json','accepted_contract.json','budget_reservation.json'],
}.items():
    for p in (ROOT/folder).iterdir():
        if p.is_file() and p.suffix=='.json' and (names is None or p.name in names):copy(p,OUT/folder/p.name)
for name in ['BUDGET_BEFORE.json','AUTH_CONFIRMATION.json','CLAIMED.json','MONETARY_MIGRATION.json',
             'expansion_CALL_PREFLIGHT.json','reflection_CALL_PREFLIGHT.json','EXPAND_ACCOUNTING_AFTER.json',
             'REFLECT_ACCOUNTING_AFTER.json','BUDGET_FIXTURE_INITIAL_FAILURE.json','BUDGET_FIXTURE_RERUN.json',
             'EXPAND.stdout.log','EXPAND.stderr.log','SEARCH.stdout.log','SEARCH.stderr.log','REFLECT.stdout.log','REFLECT.stderr.log',
             'run_t030.py','export_results.py']:
    copy(ROOT/name,OUT/'local_execution'/name)
for name in ['campaign_budget.py','task_budget_guard_220.py','test_campaign_budget.py']:
    copy(ROOT/'local_tools'/name,OUT/'local_execution'/'local_tools'/name)
for name in ['T030_PREFLIGHT_REPORT_FINAL.json','T030_ARCHIVE_BINDINGS_FINAL.json',
             'pytest_four_targets_20261016_045850.stdout.log','pytest_four_targets_20261016_045850.stderr.log']:
    copy(ROOT/'preflight'/name,OUT/'preflight'/name)
for p in (ROOT/'accounting-history').rglob('*.json'):copy(p,OUT/'accounting'/p.relative_to(ROOT/'accounting-history'))
for p in (ROOT/'accounting-history').rglob('*.md'):copy(p,OUT/'accounting'/p.relative_to(ROOT/'accounting-history'))
for p in (ROOT/'atomic_reservations').glob('*.json'):copy(p,OUT/'accounting'/'atomic_reservations'/p.name)
for p in (ROOT/'private'/'audit').rglob('REQUEST.json'):copy(p,OUT/'model'/(p.parent.name+'_REQUEST.json'))
for p in (ROOT/'private'/'audit').rglob('RESPONSE.json'):copy(p,OUT/'model'/(p.parent.name+'_RESPONSE.json'))

full=[]
for folder in ['expansion','search','reflection']:
    for p in sorted((ROOT/folder).iterdir()):
        if p.is_file():full.append({'local_path':str(p),'size_bytes':p.stat().st_size,'sha256':sha(p),
                                    'compact_export_present':(OUT/folder/p.name).exists()})
save(OUT/'FULL_LOCAL_ARCHIVE_REFERENCES.json',full)
totals=load(ROOT/'REFLECT_ACCOUNTING_AFTER.json')
result={'implementation_sha':'13b238f99c2ead4c2c126908cd2914d8429d037f',
    'base_sha':'e1eba28e5350632ed7cf387c643f8d16ede1ef72','previous_result_sha':'cead25388c4fd28468d51ff70361e897214eb911',
    'engineering':'completed_bounded_loop_with_partial_reflection_plan_acceptance',
    'scientific':'certified_sampled_grid_results_with_tradeoffs; no controlled benefit or mechanism proof',
    'targeted_tests':{'passed':69,'skipped':0},'budget_guard_tests':{'passed':7},
    'expansion':{'model_http':1,'branches':11,'ignored_optional_window_fields':11,'actual_windows_applied':0},
    'search':{'exit_code':0,'candidate_count':summary['candidate_count'],'archive_before':len(previous['designs']),
              'archive_after':len(archive['designs']),'fresh_designs':len(fresh),'pareto_before':len(previous['pareto_design_ids']),
              'pareto_after':len(archive['pareto_design_ids']),'outcome_counts_by_associated_certified_designs':dict(counts),
              'deferred_unvalidated':summary['deferred_unvalidated_candidate_count'],'metering':summary['metering']},
    'reflection':{'model_http':1,'accepted_plans':3,'rejected_plans':2,'next_action_executed':False},
    'actual_task_cost_cny':totals['task_exposure_cny'],'budget_final':totals,'retries':0,'model_http_total':2,
    'complete_originals_remain_local':True,'no_retrieval_pdf_new_cases':True}
save(ROOT/'RESULT_SUMMARY.json',result);save(OUT/'RESULT_SUMMARY.json',result)
print(json.dumps({'out':str(OUT),'summary':result,'selected_certified_designs':len(selected)},ensure_ascii=False))

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig,ax=plt.subplots(figsize=(7.5,5.4))
for is_new,label,color in [(False,'T028 certified archive','#8a8a8a'),(True,'T030 newly certified','#e67e22')]:
    rows=[x for x in flat if x['new_in_T030']==is_new]
    ax.scatter([x['metrics']['G1']['value'] for x in rows],[x['metrics']['G2']['value'] for x in rows],
               s=16,alpha=.7,color=color,label=label)
parents=[archive['designs'][k] for k in {c['parent_design_id'] for c in chains}]
ax.scatter([x['metrics']['G1']['value'] for x in parents],[x['metrics']['G2']['value'] for x in parents],
           marker='*',s=100,color='#1f77b4',label='Selected original parents')
obj=flat[0]['metrics']
ax.set_xlabel('G1 mean R, %.0f-%.0f nm (maximize)'%tuple(obj['G1']['objective']['region']['wavelength_nm']))
ax.set_ylabel('G2 mean T, %.0f-%.0f nm (maximize)'%tuple(obj['G2']['objective']['region']['wavelength_nm']))
ax.set_title('Certified sampled grid at 45 degrees; integral means')
ax.grid(alpha=.2);ax.legend(fontsize=8);fig.tight_layout();(OUT/'science').mkdir(exist_ok=True)
fig.savefig(OUT/'science'/'certified_objective_scatter.png',dpi=170);plt.close(fig)
