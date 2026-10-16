"""Task-local wiring adaptation of the already audited T034 launcher."""
from pathlib import Path
import shutil
R=Path(__file__).resolve().parent;old=R.parent/'R3-T034';L=R/'local_tools';L.mkdir(exist_ok=True)
shutil.copyfile(old/'local_tools/task_budget_guard_220.py',L/'task_budget_guard_220.py')
s=(old/'local_tools/aggregate_budget.py').read_text(encoding='utf-8')
s=s.replace("['e2e-09-si-photonics-ar-dualband','e2e-08-advanced-packaging-uv','e2e-02-space-qkd-cband']","['e2e-01-methane-swir-window','e2e-04-solarblind-uv-triple','e2e-05-visual-absorber-cr']")
s=s.replace("('R3-T031','R3-T034')","('R3-T032',)")
s=s.replace("|task_ids['R3-T031:'+c]|task_ids['R3-T034:'+c]","|task_ids['R3-T032:'+c]")
s=s.replace('>=9:','>=12:').replace('batch9','batch12')
s=s.replace("len(task_ids['R3-T031:'+TASK_IDS[task_id]]|task_ids['R3-T034:'+TASK_IDS[task_id]])>=3","len(task_ids['R3-T032:'+TASK_IDS[task_id]])>=4")
s=s.replace('shared T031/T034 original case3','T032 original case4').replace('shared T031/T034 batch','T032 batch').replace("'native_task_http_cap':3,'batch_http_cap':9","'native_task_http_cap':4,'batch_http_cap':12")
(L/'aggregate_budget.py').write_text(s,encoding='utf-8')
print('Task-local native220 and aggregate gates prepared; no real ledger writes')
