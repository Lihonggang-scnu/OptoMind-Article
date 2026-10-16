"""Bind the unchanged T032 bounded flow to T036 source/category and cumulative guard."""
from pathlib import Path
R=Path(__file__).resolve().parent;P=R.parent/'R3-T032'
s=(P/'run_t032.py').read_text(encoding='utf-8')
s=s.replace('r3t032b','r3t036a').replace('8a38b2d0d7b18c4308c4f5978691a234f6ab3732','408fef514d4ed156ffde03c868a26cea3aba9dfa').replace('t032_bound_aggregate','t036_bound_aggregate').replace("'R3-T032:'+case","'R3-T036:'+case")
s=s.replace("CASES=load(R/'BUDGET.json')['case_order']","CASES=['e2e-01-methane-swir-window','e2e-04-solarblind-uv-triple','e2e-05-visual-absorber-cr']")
start=s.index('def preflight(case):');end=s.index('def audited_guard(',start)
pre='''def preflight(case):
 assert case==CASES[1],'Only originalcase04 is authorized forT036'
 runner,origins=reused.bind();state=load(R.parents[1]/'state.json')
 assert state['processed']['R3-T032::C2L-R3-0047']['status']=='blocked'
 assert state['processed']['R3-T035::C2L-R3-0050']['status']=='completed'
 admission=load(R/'preflight/T036_LOCAL_ACCEPTANCE.json');assert admission['global_pass'] is True,'offline material/category admission not passed'
 for rel,h in load(R/'OFFLINE_VALIDATION.json')['implementation_file_sha256'].items():assert sha(SOURCE/rel)==h,'tested source changed'
 for name in ['material_capabilities','route_consumer','research_routes']:
  mod=importlib.import_module('focused_research.'+name);origins[name]=str(Path(mod.__file__).resolve());assert Path(mod.__file__).resolve().is_relative_to(SOURCE)
 assert (R/'local_tools/task_budget_guard_220.py').read_bytes()==(R.parent/'R3-T030/local_tools/task_budget_guard_220.py').read_bytes()
 cfg=module(R.parent/'R3-T030/run_t030.py','t036_original_billing').original_billing()
 oldcase=R.parent/'R3-T032/cases'/case
 assert load(oldcase/'CASE_RESULT.json')['engineering_status']=='blocked_pre_dispatch'
 assert not (oldcase/'plan/task_sheet.json').exists() and not (oldcase/'retrieval').exists(),'unexpected prior paidstage state; no freshguard admission'
 with sqlite3.connect(POOL.as_uri()+'?mode=ro',uri=True) as db:
  assert not db.execute('SELECT 1 FROM r3_task_calls WHERE task_id=?',('R3-T032:'+case,)).fetchone(),'unexpected prior04HTTP; reconcile beforeproceeding'
 question=load(SOURCE/'focused_research/campaigns/t032/inputs'/case/'FRESH_REQUEST.json')['question']
 assert sha(SOURCE/'focused_research/campaigns/t032/inputs'/case/'FRESH_REQUEST.json')=='ef00e448f4c376618e9cdbf451d04b4a6736ff91bd91b6f6af9dd16d7c746638'
 assert sha(R/'SOURCE_BLOCKER.json')==sha(R.parent/'R3-T032/CASE04_BLOCKED_PROGRESS.json'),'original blocker receipt changed'
 assert sha(SOURCE/'veritmm/tmm_engine/rii_cache.db')=='e4c1ec34cc4f765a8400ff87bb7dee8705b6da2cdb11a229cc57ad9bdd274989'
 return runner,cfg,question,{'source_sha':IMPL,'origins':origins,'interpreter':sys.executable,'budget_before':reconcile(case),'launcher_sha256':sha(__file__),'aggregate_gate_sha256':sha(R/'local_tools/aggregate_budget.py'),'same_original_case_continuation_of':'R3-T032','same_original_batch_HTTP_ceiling':12}
'''
s=s[:start]+pre+s[end:]
start=s.index('def configuration(');end=s.index('def step(',start)
config='''def configuration(sheet,case):
 assert case==CASES[1]
 rows=sheet['explicit_constraints']
 def refs(words):return [x['id'] for x in rows if any(w in x['original_span'] for w in words)]
 category_ids=refs(['无机介质','不含金属','常规可沉积'])
 spans=[x['original_span'] for x in rows if x['id'] in category_ids]
 assert any('不含金属' in x for x in spans) and any('无机介质' in x for x in spans),'original broadcategory/nometal constraint source IDs absent'
 ids={'allowed_materials':[],'allowed_material_category':category_ids,'max_layers':refs(['30']),'substrate':refs(['熔融石英']),'incident_medium':refs(['空气'])}
 assert all(ids[k] for k in ['allowed_material_category','max_layers','substrate','incident_medium']),'missing original source IDs'
 inventory=load(R/'material-preflight/execution_materials.json')
 cap=importlib.import_module('focused_research.material_capabilities')
 constraints={'allowed_materials':None,'allowed_material_category':cap.CATEGORY,'execution_materials':inventory,'max_layers':30,'substrate':'fused_silica','incident_medium':'air','constraint_source_ids':ids,'assumptions':{'execution_materials':'The finite available execution inventory was verified against original local registry identities and complete255-1100nm n/k coverage with no extrapolation. It is not the user allowed-material set, an exhaustive dielectric classification, a transparency claim or a manufacturing guarantee. Unsupported identities require capability review; no automatic material substitution.'}}
 assert cap.validate_material_capabilities(constraints)==inventory
 mdir=R/'material-preflight'/case;media=load(mdir/'optical_media.json');grid=load(mdir/'wavelengths.json')
 assert grid==list(range(255,1101)) and media['air']=={'constant_n':1.0,'constant_k':0.0} and media['fused_silica']['dataset_id']=='835'
 assert all(media[k]==v for k,v in inventory.items()),'optical media differs fromverifiedinventory'
 return constraints,media,grid
'''
s=s[:start]+config+s[end:]
s=s.replace("p.add_argument('--case',choices=CASES,required=True)","p.add_argument('--case',choices=[CASES[1]],required=True)")
(R/'run_t036.py').write_text(s,encoding='utf-8')
s=(P/'probe.py').read_text().replace("cases=load(r/'BUDGET.json')['case_order']","cases=['e2e-04-solarblind-uv-triple']").replace('R3-T032:','R3-T036:');(R/'probe.py').write_text(s)
s=(P/'record_case_progress.py').read_text().replace('R3-T032::C2L-R3-0047','R3-T036::C2L-R3-0051').replace('run_t032.py','run_t036.py').replace('r3t032b','r3t036a');(R/'record_case_progress.py').write_text(s)
import ast
ast.parse((R/'run_t036.py').read_text());print('TasklocalT036launcher prepared; only04; no scientificsource edits or execution')
