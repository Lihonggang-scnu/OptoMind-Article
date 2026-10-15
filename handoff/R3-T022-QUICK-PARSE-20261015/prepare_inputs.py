"""Prepare and parse actual candidate inputs only; never call a solver."""
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t022a')
sys.path.insert(0, str(SOURCE/'focused_research'))
from route_consumer import prepare_tmm_inputs

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

instances = read(ROOT/'runs/routes/prepared_instances.json')
config = read(ROOT/'INPUT_PREPARATION_CONFIG.json')
sheet = read(ROOT/'runs/plan/task_sheet.json')
metrics = {g['executable_metric']['objective_id']:g['executable_metric'] for g in sheet['subgoals']}
out = ROOT/'runs/inputs'
assert not out.exists(), 'Preserve prior input preparation'
out.mkdir(parents=True)
prepared, pending = [], []
for instance in instances:
    try:
        for metric in instance['objectives']:
            assert metric == metrics[metric['objective_id']], 'Frozen metric changed'
        prepared.append(prepare_tmm_inputs(instance, optical_media=config['optical_media'],
                                          wavelengths_nm=config['wavelengths_nm']))
    except (ValueError, KeyError, AssertionError) as exc:
        pending.append({'instance_id':instance.get('instance_id'),'reason':str(exc)})
(out/'prepared_tmm_inputs.json').write_text(json.dumps(prepared,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
# Load this Article checkout's actual embedded consumer, with no solver invocation.
sys.path.insert(0,str(SOURCE/'code'))
sys.path.insert(0,str(SOURCE/'veritmm'))
task_io = importlib.import_module('tmm_engine.task_io')
design = importlib.import_module('optomind_optics.harness.design_task')
assert Path(task_io.__file__).resolve().is_relative_to(SOURCE/'veritmm')
assert Path(design.__file__).resolve().is_relative_to(SOURCE/'code')
parsed=[]
for value in prepared:
    try:
        task=task_io.simulation_task_from_dict(value['tmm_task'])
        for objective in value['objectives']:
            design.ObjectivePreference(**objective)
        parsed.append({'instance_id':value['instance_id'],'layers':len(task.stack.layers),
            'angle_deg':list(task.illumination.angles_deg),'polarizations':list(task.illumination.polarizations)})
    except (ValueError, TypeError, KeyError) as exc:
        pending.append({'instance_id':value['instance_id'],'reason':str(exc)})
result={'prepared_count':len(prepared),'native_parser_accepted_count':len(parsed),
        'parsed':parsed,'pending':pending,'simulation_performed':False,
        'optical_dataset_and_numerical_preflight':'not_run',
        'actual_imports':{'task_io':task_io.__file__,'objective_consumer':design.__file__},
        'frozen_metrics_preserved':True}
(out/'CONSUMER_CHECK.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False))
