"""Capture coordinator input before its API call, under the shared budget.

Upstream cache misses may execute. The first diagnostic attempt also changed
the retrieval client identity; those bounded upstream calls are preserved and
included in the ledger, never claimed to be free diagnostic operations.
"""
from pathlib import Path
import json
import shutil
import sys

ROOT=Path(__file__).resolve().parent
src=ROOT/'review/real-01'
snapshot=ROOT/'review/attempt-01-context-failure'
if not snapshot.exists():
    shutil.copytree(src,snapshot,copy_function=shutil.copy2)
from optomind_research.runtime.upgrade3 import progressive_review_plan as mod

original_messages=mod._messages_for
def capture(stage,payload):
    if stage != 'whole_plan_improvement':
        return original_messages(stage,payload)
    path=ROOT/'review/CONTEXT_FAILURE_INPUT.json'
    path.write_text(json.dumps({'stage':stage,'payload':payload},ensure_ascii=False,indent=2),encoding='utf-8')
    sizes=[]
    def visit(value,path='payload'):
        if isinstance(value,dict):
            for key,child in value.items():
                size=len(json.dumps(child,ensure_ascii=False))
                if size>20000:
                    sizes.append((path+'/'+key,size))
                    visit(child,path+'/'+key)
        elif isinstance(value,list):
            for i,child in enumerate(value):
                if isinstance(child,(dict,list)):
                    visit(child,path+'/'+str(i))
    visit(payload)
    (ROOT/'review/CONTEXT_FAILURE_FIELD_SIZES.json').write_text(json.dumps(sorted(sizes,key=lambda x:x[1],reverse=True),indent=2),encoding='utf-8')
    print(json.dumps({'capture_only':True,'stage':stage,'payload_chars':len(json.dumps(payload,ensure_ascii=False)),
        'large_fields':sorted(sizes,key=lambda x:x[1],reverse=True)[:35]},ensure_ascii=False,indent=2))
    raise mod.ProgressivePlanError('read_only_pending_input_capture_no_api')

mod._messages_for=capture
from budgeted_entry import main
args=json.loads((ROOT/'review/RUN_ARGV.json').read_text(encoding='utf-8'))
sys.argv=[str(ROOT/'budgeted_entry.py'),'review',*args]
raise SystemExit(main())
