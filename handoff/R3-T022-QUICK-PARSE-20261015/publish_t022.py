"""Fast publish of the completed T022 code/evidence; credentials-only scan."""
import datetime,json,os,re,shutil,subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
G=Path(r'D:\OptoMind-Article-3\r3t022a')
REL=Path('handoff/R3-T022-QUICK-PARSE-20261015')
BRANCH='codex/r3-t022-quick-chain-20261015'
REMOTE='https://github.com/Lihonggang-scnu/OptoMind-Article.git'
env={**os.environ,'GIT_TERMINAL_PROMPT':'0'}
def git(*args):
    p=subprocess.run(['git','-C',str(G),*args],env=env,capture_output=True,timeout=120)
    with (ROOT/'github-upload.log.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps({'argv':list(args),'exit_code':p.returncode,
            'stdout':p.stdout.decode('utf-8',errors='replace'),
            'stderr':p.stderr.decode('utf-8',errors='replace')})+'\n')
    assert p.returncode==0,'Git operation failed; preserve results and do not repeat execution'
    return p.stdout.decode('utf-8').strip()
assert git('rev-parse','HEAD')=='5ef850832c221e7f6444195ac8b948117ec1c8aa'
assert git('branch','--show-current')==BRANCH
assert not git('status','--porcelain=v1')
result=json.loads((ROOT/'RESULT_SUMMARY.json').read_text(encoding='utf-8'))
assert result['new_model_http_attempts']==3 and not result['simulation_performed']
out=G/REL;out.mkdir(parents=True,exist_ok=False)

def copy(source,dest):
    source=Path(source);dest=out/dest
    assert source.is_file(),str(source)
    assert source.suffix.lower() not in {'.sqlite','.db','.pdf','.env','.pt','.pth','.safetensors','.zip','.bundle','.log'}
    dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)

files=['REPORT.md','DETAILED_REPORT.md','SEARCH_QUALITY.md','RUN_COMMANDS.md',
       'RESULT_SUMMARY.json','SOURCE_PREFLIGHT.json','LAUNCHER_PREFLIGHT.json',
       'BUDGET_BEFORE.json','BUDGET_AFTER.json','COST_ESTIMATE.json','MODEL_CALLS.json',
       'PLAN_ACCOUNTING.json','ROUTES_ACCOUNTING.json','PARSER_PROFILE.json',
       'PARSER_SUMMARY.json','ACTUAL_TEXT_CONSUMPTION.json','CORE_BRIDGE_EXCLUSION.json',
       'ACQUISITION_OBSERVED_SOURCES.json','FULLTEXT_SELECTION.json',
       'SELECTED_FULLTEXT_RECORDS.json','CHECKED_CONSTRAINTS.json',
       'INPUT_PREPARATION_CONFIG.json','S2_SELECTION_MANIFEST.json','S2_SELECTED_VIEW.json',
       'MATERIAL_SELECTION.json','EVIDENCE_VIEW_PREFLIGHT.json','ROUTE_PROMPT_PREFLIGHT.json',
       'launch_t022.py','discover_fulltext_sources.py','run_fulltext.py',
       'prepare_inputs.py','finalize_evidence.py','publish_t022.py']
for name in files:copy(ROOT/name,Path(name))
for p in (ROOT/'inbox').iterdir():
    if p.suffix in ['.json','.md']:copy(p,Path('task_messages')/p.name)
for p in (ROOT/'receipts').glob('*.json'):copy(p,Path('receipts')/p.name)
for stage in ['plan','s2','routes','inputs','fulltext-discovery','material-archive','material-view']:
    for p in (ROOT/'runs'/stage).glob('*.json'):
        copy(p,Path('runs')/stage/p.name)
for name in ['FULLTEXT_REQUEST.json','FULLTEXT_RESULT.json','CANDIDATES.json','PDF_MANIFEST.json']:
    copy(ROOT/'runs/fulltext'/name,Path('runs/fulltext')/name)
for p in (ROOT/'runs/fulltext/exports').glob('*.json'):copy(p,Path('runs/fulltext/exports')/p.name)
copy(ROOT/'runs/fulltext/docling_batch/BATCH_RESULT.json',Path('runs/fulltext/docling_batch/BATCH_RESULT.json'))
copy(ROOT/'runs/fulltext/docling_batch/WORKER_HISTORY.json',Path('runs/fulltext/docling_batch/WORKER_HISTORY.json'))
for p in (ROOT/'private/audit').glob('*/calls/**/*'):
    if p.is_file() and p.suffix in ['.json','.raw']:
        copy(p,Path('model_raw')/p.relative_to(ROOT/'private/audit'))
(out/'README.md').write_text('# T022 本地联动证据\n\n先读 [REPORT.md](REPORT.md)。本轮真实任务单、四份全文、两条候选与四组输入已完成；验收为partial，未仿真。原始模型输出在model_raw，解析与选中文本在runs，费用见BUDGET_AFTER.json。核心缺口保留给云端处理。\n\n源码基准5ef850832c221e7f6444195ac8b948117ec1c8aa，固定Review d2deb4dbe18b23837d949822ad731afbaba9414b。非新案例启动指令；停止追加执行。\n',encoding='utf-8',newline='\n')
(out/'.gitattributes').write_text('* -text\n',encoding='utf-8',newline='\n')
events=[]
for event,evidence in [('claimed','receipts/20261015T101906Z_RCP-LOCAL-T022-CLAIM-20261015T101906Z.json'),
    ('source_and_budget_preflight','LAUNCHER_PREFLIGHT.json'),('plan','PLAN_STARTED.json'),
    ('retrieval','runs/s2/summary.json'),('independent_discovery','runs/fulltext-discovery/DISCOVERY_RESULT.json'),
    ('fulltext_basic','runs/fulltext/FULLTEXT_RESULT.json'),('routes','ROUTES_STARTED.json'),
    ('inputs','runs/inputs/CONSUMER_CHECK.json'),('finished','RESULT_SUMMARY.json')]:
    p=ROOT/evidence
    events.append({'event':event,'artifact':evidence,
        'artifact_written_at_utc':datetime.datetime.fromtimestamp(p.stat().st_mtime,datetime.timezone.utc).isoformat()})
events.append({'event':'operator_metadata_correction','reason':'material_block_index is a list; summary-only .get error corrected after prompt validation. No provider/model/parser repeated.'})
(out/'WORK_LOG.jsonl').write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in events),encoding='utf-8',newline='\n')

# Read existing credential files solely to detect accidental exact-value disclosure.
# Never print values, headers or authentication material.
secret_values=set()
for name in ['qwen-api-key.txt','semantic-scholar-api-key.txt','openalex.txt','core_api.txt']:
    p=Path(r'F:\OptoMind-Review-2\api_keys')/name
    if not p.is_file():continue
    for line in p.read_text(encoding='utf-8-sig').splitlines():
        value=line.strip().strip('"\',; ')
        if not value or value.startswith(('#','//')):continue
        if '=' in value and re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',value.split('=',1)[0]):
            value=value.split('=',1)[1].strip().strip('"\'')
        if len(value)>=16 and re.fullmatch(r'[A-Za-z0-9_.+/=\-]+',value):
            secret_values.add(value.encode())
pattern=re.compile(rb'sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9_]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----')
paths=[p for p in out.rglob('*') if p.is_file()]
for p in paths:
    data=p.read_bytes()
    assert not pattern.search(data) and not any(x in data for x in secret_values),'Credential-like value detected; file retained locally, no push'
git('add','--',REL.as_posix())
git('-c','user.name=OptoMind Local Agent','-c','user.email=optomind-local@localhost',
    'commit','-m','Record T022 real fulltext-to-route integration and partial quality findings')
commit=git('rev-parse','HEAD')
net=['-c','http.proxy=http://127.0.0.1:7897','-c','http.version=HTTP/1.1',
     '-c','http.lowSpeedLimit=1','-c','http.lowSpeedTime=15']
git(*net,'push',REMOTE,'HEAD:refs/heads/'+BRANCH)
assert git(*net,'ls-remote',REMOTE,'refs/heads/'+BRANCH).split()[0]==commit
refs={'commit':commit,'branch':BRANCH,'remote_verified':True,'files':len(paths),
    'bytes':sum(p.stat().st_size for p in paths),
    'reading_url':'https://github.com/Lihonggang-scnu/OptoMind-Article/tree/'+commit+'/'+REL.as_posix(),
    'report_url':'https://github.com/Lihonggang-scnu/OptoMind-Article/blob/'+commit+'/'+REL.as_posix()+'/REPORT.md',
    'source_sha':result['article_source_sha'],'cost_cny':result['new_cost_cny'],
    'execution_status':result['execution_status'],'acceptance_status':result['acceptance_status']}
(ROOT/'GITHUB_RESULT.json').write_text(json.dumps(refs,indent=2)+'\n',encoding='utf-8')
print(json.dumps(refs,ensure_ascii=False,indent=2))
