"""Two-stage T027 command witness and cumulative numerical-budget gate."""
import ctypes, datetime, hashlib, json, os, subprocess, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent
SOURCE = Path(r'D:\OptoMind-Article-3\r3t027a')
SHA = 'b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b'
def load(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def save(p, v):
    with Path(p).open('x',encoding='utf-8',newline='\n') as f:
        json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n')
def used():
    reservations=[]
    for p in sorted((ROOT/'cache/evaluations').glob('*.reservation.json')):
        reservations.append({'path':str(p),'count':len(load(p)['reserved_simulation_keys']),
                             'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
    row_charge=0
    for stage in ('search','refined'):
        p=ROOT/stage/'summary.json'
        if p.exists():
            m=load(p)['metering'];row_charge+=m['search_forward_design_rows']+m['search_failed_job_reserved_rows']
        elif (ROOT/(stage.upper()+'_STARTED.json')).exists():
            row_charge+=4096
    return {'search_row_charge_including_failed_job_reservations':row_charge,
            'certification_reserved_count':sum(x['count'] for x in reservations),'certification_reservations':reservations}
def process_cpu_seconds(process):
    class FILETIME(ctypes.Structure):
        _fields_=[('low',ctypes.c_uint32),('high',ctypes.c_uint32)]
    times=[FILETIME() for _ in range(4)]
    ok=ctypes.windll.kernel32.GetProcessTimes(ctypes.c_void_p(int(process._handle)),*[ctypes.byref(t) for t in times])
    if not ok:return {'available':False,'scope':'main CLI process only'}
    seconds=lambda t:((t.high<<32)+t.low)/1e7
    return {'available':True,'kernel_seconds':seconds(times[2]),'user_seconds':seconds(times[3]),
            'total_seconds':seconds(times[2])+seconds(times[3]),'scope':'main CLI process; excludes spawned certification worker CPU'}
def main():
    stage=sys.argv[1];assert stage in ('search','refined')
    assert subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()==SHA
    assert not subprocess.check_output(['git','-C',str(SOURCE),'status','--porcelain=v1'],text=True).strip()
    gate=load(ROOT/'TORCH_GATE.json');assert gate['exit_code']==0 and gate['pytest_summary']['skipped']==0
    paths=load(ROOT/'INPUT_PATHS.json')
    for path,expected in paths['sha256'].items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==expected
    before=used();assert before['search_row_charge_including_failed_job_reservations']<=8192 and before['certification_reserved_count']<=128
    if stage=='search':
        assert before['search_row_charge_including_failed_job_reservations']==before['certification_reserved_count']==0
        command=[sys.executable,'-B','-X','utf8','-m','focused_research.search_stage','search']
        for option,key in (('task-sheet','task_sheet'),('evidence-file','evidence'),('constraints-file','constraints'),
                           ('routes-file','routes'),('optical-media','optical_media'),('wavelengths','wavelengths')):
            command+=['--'+option,paths[key]]
        command+=['--parameterization','family','--seed','42']
    else:
        assert (ROOT/'SEARCH_EXIT.json').is_file()
        assert load(ROOT/'revision/refinement_plans.json')['plans']
        assert before['search_row_charge_including_failed_job_reservations']<=4096 and before['certification_reserved_count']<=64
        command=[sys.executable,'-B','-X','utf8','-m','focused_research.search_stage','refine',
                 '--previous-search',str(ROOT/'search'),'--revision-dir',str(ROOT/'revision'),'--seed','43']
    command+=['--cache-dir',str(ROOT/'cache'),'--max-forward-evaluations','4096','--max-new-evals','64',
              '--max-workers','2','--backend','torch','--execute','--output',str(ROOT/stage)]
    assert not (ROOT/stage).exists()
    save(ROOT/(stage.upper()+'_STARTED.json'),{'source_sha':SHA,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'command':command,'budget_before':before,'reserved_search_rows':4096,'reserved_new_certifications':64,
        'model_calls_in_this_stage':0,'backend':'torch','retries':0})
    state_path=ROOT.parent.parent/'state.json';state=load(state_path)
    assert state['active_task']['task_id']=='R3-T027'
    state['active_task'].update(phase=stage+'_running',paid_execution_allowed=False)
    state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    start=time.monotonic()
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1'}
    with (ROOT/(stage.upper()+'.stdout.log')).open('x',encoding='utf-8') as out,(ROOT/(stage.upper()+'.stderr.log')).open('x',encoding='utf-8') as err:
        process=subprocess.Popen(command,cwd=SOURCE,env=env,stdout=out,stderr=err)
        save(ROOT/(stage.upper()+'_PROCESS.json'),{'pid':process.pid,'command':command,'workdir':str(SOURCE)})
        print('T027 '+stage+' started; owned CLI PID '+str(process.pid),flush=True)
        code=process.wait();cpu=process_cpu_seconds(process)
    after=used()
    assert after['search_row_charge_including_failed_job_reservations']<=8192 and after['certification_reserved_count']<=128
    save(ROOT/(stage.upper()+'_EXIT.json'),{'exit_code':code,'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'wall_seconds':time.monotonic()-start,'cpu':cpu,'budget_after':after})
    print(json.dumps({'stage':stage,'exit_code':code,'search_row_charge':after['search_row_charge_including_failed_job_reservations'],
                      'certification_reservations':after['certification_reserved_count']}),flush=True)
    return code
if __name__=='__main__':raise SystemExit(main())
