"""Publish necessary ordinary T027 evidence on the authorized independent branch."""
import json, os, re, shutil, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SOURCE=Path(r'D:\OptoMind-Article-3\r3t027a')
SHA='b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b'
REL='handoff/R3-T027-NUMERICAL-SEARCH-20261016'
DEST=SOURCE/REL
BRANCH='codex/r3-t027-physics-guided-search-20261016'
REMOTE='https://github.com/Lihonggang-scnu/OptoMind-Article.git'
def load(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def git(*args):
    r=subprocess.run(['git','-C',str(SOURCE),*args],capture_output=True,text=True,env={**os.environ,'GIT_TERMINAL_PROMPT':'0'})
    if r.returncode:raise RuntimeError(r.stdout+r.stderr)
    return r.stdout.strip()
assert git('rev-parse','HEAD')==SHA and git('branch','--show-current')==BRANCH
assert not git('status','--porcelain=v1') and not DEST.exists()
top=['REPORT.md','QUALITY_REPORT.md','RESULT_SUMMARY.json','TORCH_GATE.json','TORCH_GATE.stdout.txt','TORCH_GATE.stderr.txt',
    'BUDGET_INITIAL.json','BUDGET_FINAL.json','REVISION_PREFLIGHT.json','REVISION_STARTED.json','REVISION_ACCOUNTING.json',
    'CALL_1_PREFLIGHT.json','MODEL_CALLS.json','ACTUAL_MODEL_CONTEXT.json','PRESERVATION_CHECK.json','NUMERICAL_ACCOUNTING.json',
    'MATERIAL_PREFLIGHT.json','DELIVERY_HASHES.json','INPUT_PATHS.json','SOURCE_RECEIVE.json',
    'MODEL_DECISION_FACT_AUDIT.json','PARENT_CHILD_COMPARISONS.json','TRAJECTORY.json','PHYSICS_FAILURES.json',
    'SELECTED_NATIVE_ARCHIVES.json','CPU_TIMER_SCOPE_CHECK.json',
    'SEARCH_STARTED.json','SEARCH_EXIT.json','SEARCH_PROCESS.json','REFINED_STARTED.json','REFINED_EXIT.json','REFINED_PROCESS.json',
    'prepare_claim.py','prepare_inputs.py','run_numerical_stage.py','launch_decision.py','analyze_and_export.py','publish.py']
mapping=[(ROOT/p,Path(p)) for p in top]
commands=['# T027 actual commands','', 'Source: `'+SHA+'`. Existing Python 3.12 Windows tensor environment; all runs already completed. These commands record evidence, not authorization to repeat.', '',
          '## Required Torch gate (exit 0; 102 passed, no skips)', '', '```text',load(ROOT/'TORCH_GATE.json')['command'],'```','']
for stage in ('search','refined'):
    witness=load(ROOT/(stage.upper()+'_STARTED.json'))
    text='& '+' '.join("'"+str(a).replace("'","''")+"'" for a in witness['command'])
    commands+=['## '+stage+' (exit 2; saved partial coverage, not retried)','', '```powershell',text,'```','']
commands+=['## One native-metered model decision (exit 0; one actual HTTP)','', '```powershell',
    "& 'D:\\OptoMind-Article-3\\local-integration\\20261013\\envs\\tensor\\Scripts\\python.exe' -B -X utf8 '"+str(ROOT/'launch_decision.py')+"' preflight",
    "& 'D:\\OptoMind-Article-3\\local-integration\\20261013\\envs\\tensor\\Scripts\\python.exe' -B -X utf8 '"+str(ROOT/'launch_decision.py')+"' revise --execute",'```','',
    'All stages used PYTHONDONTWRITEBYTECODE=1 and PYTHONUTF8=1. Search seeds42 and43 were fixed by HANDOFF; production allocates per-job seeds from those values. No retry, backend fallback, installation or additional stage.', '']
with (ROOT/'RUN_COMMANDS.md').open('x',encoding='utf-8',newline='\n') as f:f.write('\n'.join(commands))
mapping.append((ROOT/'RUN_COMMANDS.md',Path('RUN_COMMANDS.md')))
for p in sorted((ROOT/'export').glob('*.json')):mapping.append((p,p.relative_to(ROOT)))
for p in sorted((ROOT/'revision').glob('*.json')):mapping.append((p,p.relative_to(ROOT)))
for directory in ('search','refined'):
    for filename in ('task_sheet.json','evidence_items.json','design_constraints.json','optical_media.json','wavelengths_nm.json',
                     'accepted_contract.json','candidate_report.json','summary.json','input_failures.json','input_bindings.json',
                     'search_reservations.json','budget_reservation.json'):
        mapping.append((ROOT/directory/filename,Path(directory)/filename))
for filename in ('READY.json','T027_HANDOFF.md','T027_DESIGN.md','T027_OFFLINE_VALIDATION.json','SHA256SUMS.txt','CLOUD_PUBLISHED.json'):
    mapping.append((ROOT/'delivery'/filename,Path('delivery')/filename))
for p in sorted((ROOT/'private/audit').rglob('*')):
    if p.is_file() and (p.name in ('REQUEST.json','RESPONSE.json') or p.suffix=='.raw'):
        mapping.append((p,Path('native-audit')/p.relative_to(ROOT/'private/audit')))
for p in sorted(ROOT.glob('*.log')):mapping.append((p,Path('process-logs')/p.name))
selected=load(ROOT/'SELECTED_NATIVE_ARCHIVES.json')
archive_roots={x['archive_root'] for x in selected['certified_designs']+selected['failed_controls']}
# Retain full native evidence for all three genuine phase1 double-improving children.
extra=[]
first=load(ROOT/'search/design_archive.json')
for comparison in load(ROOT/'PARENT_CHILD_COMPARISONS.json')['comparisons']:
    if comparison['stage']=='search' and comparison['category']=='both_increase':
        design=first['designs'][comparison['child_design_id']]
        root=design['certified_result']['archive_root'];archive_roots.add(root)
        extra.append({'design_id':design['design_id'],'archive_root':root,'reason':'actual phase1 parent-child double improvement'})
with (ROOT/'ADDITIONAL_SELECTED_ARCHIVES.json').open('x',encoding='utf-8',newline='\n') as f:json.dump(extra,f,indent=2);f.write('\n')
mapping.append((ROOT/'ADDITIONAL_SELECTED_ARCHIVES.json',Path('ADDITIONAL_SELECTED_ARCHIVES.json')))
for root in sorted(archive_roots):
    folder=Path(root)
    assert folder.is_relative_to(ROOT/'cache/runs')
    for filename in ('NORMALIZED_TASK.json','PHYSICS_ACCEPTANCE_CERTIFICATE.json','SPECTRA.csv','VERIFICATION_EVIDENCE.json',
                     'VERIFICATION_POLICY.json','RUN_RESULT.json','RESULT_SUMMARY.json'):
        file=folder/filename
        if file.exists():mapping.append((file,Path('selected-native')/folder.name/filename))
mapping.append((Path(load(ROOT/'INPUT_PATHS.json')['routes']),Path('original-inputs/T025-round-2.research_routes.json')))
assert len({str(rel) for _,rel in mapping})==len(mapping)
patterns=[r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',r'\bsk-[A-Za-z0-9_-]{20,}',
          r'\bgh[pousr]_[A-Za-z0-9_]{20,}',r'\bgithub_pat_[A-Za-z0-9_]{20,}',
          r'\bAIza[A-Za-z0-9_-]{30,}',r'\bBearer\s+[A-Za-z0-9_.=-]{20,}']
for p,_ in mapping:
    assert p.is_file() and p.suffix.lower() not in ('.sqlite','.db','.pdf','.zip','.env','.bin','.safetensors')
    assert not any(re.search(pattern,p.read_bytes().decode('utf-8-sig')) for pattern in patterns),'Credential pattern: '+p.name
for p,relative in mapping:
    dest=DEST/relative;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
(DEST/'.gitattributes').write_text('* -text\n',encoding='utf-8')
git('-c','core.autocrlf=false','add','--',REL)
git('-c','core.whitespace=cr-at-eol','diff','--cached','--check')
git('-c','core.autocrlf=false','-c','user.name=OptoMind Local Agent','-c','user.email=optomind-local@localhost',
    'commit','-m','Record T027 real numerical search, explicit parent refinement and decision limits')
commit=git('rev-parse','HEAD')
network=['-c','http.proxy=http://127.0.0.1:7897','-c','http.version=HTTP/1.1','-c','http.lowSpeedLimit=1','-c','http.lowSpeedTime=15']
git(*network,'push',REMOTE,'HEAD:refs/heads/'+BRANCH)
assert git(*network,'ls-remote',REMOTE,'refs/heads/'+BRANCH).split()[0]==commit
result={'task_id':'R3-T027','commit':commit,'source_sha':SHA,'branch':BRANCH,'path':REL,
    'remote_verified':True,'files':len(mapping)+1,'bytes':sum(p.stat().st_size for p,_ in mapping),
    'selected_native_archive_count':len(archive_roots),'credential_scan':'passed','zip_or_databases_uploaded':False,
    'reading_url':f'https://github.com/Lihonggang-scnu/OptoMind-Article/tree/{commit}/{REL}',
    'report_url':f'https://github.com/Lihonggang-scnu/OptoMind-Article/blob/{commit}/{REL}/REPORT.md',
    'quality_report_url':f'https://github.com/Lihonggang-scnu/OptoMind-Article/blob/{commit}/{REL}/QUALITY_REPORT.md'}
with (ROOT/'GITHUB_RESULT.json').open('x',encoding='utf-8',newline='\n') as f:json.dump(result,f,indent=2);f.write('\n')
print(json.dumps(result))
