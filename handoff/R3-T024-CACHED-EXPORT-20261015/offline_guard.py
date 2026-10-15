"""Test-only execution guard; production sources and cached inputs are untouched."""
import atexit
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
ARTICLE=Path(r'D:\OptoMind-Article-3\r3t024a')
REVIEW_PYTHON=Path(r'D:\OptoMind-Article-3\local-preparation\envs\review\Scripts\python.exe')
target=Path(sys.argv[1]).resolve()
assert target.parent==ARTICLE/'focused_research' or target==ROOT/'check_exports.py'
events=[]
bound_sockets=[]
def audit(event,args):
    # Windows asyncio creates an internal loopback socket pair on import.
    # Permit only a connection to a listener bound by this very process;
    # supplier endpoints and the local HTTP proxy remain forbidden.
    if event=='socket.bind':
        bound_sockets.append(args[0])
    if event=='socket.connect':
        address=args[1]
        for listener in bound_sockets:
            try:
                if listener.getsockname()==address:
                    events.append({'event':'local_socketpair','network_http':False})
                    return
            except OSError:
                pass
    if event in ('socket.connect','socket.getaddrinfo'):
        events.append({'event':event,'blocked':True})
        raise RuntimeError('T023 forbids all network calls')
    if event=='import' and str(args[0]).split('.')[0] in {'docling','torch'}:
        events.append({'event':'forbidden_import','module':str(args[0]),'blocked':True})
        raise RuntimeError('T023 forbids model, acquisition and parser initialization')
sys.addaudithook(audit)
original_popen=subprocess.Popen
class GuardedPopen(original_popen):
    # Windows asyncio subclasses subprocess.Popen during import; preserve its
    # class semantics instead of replacing it with a function.
    def __init__(self,command,*args,**kwargs):
        command=list(command)
        if Path(command[0]).name.lower() in ('git','git.exe'):
            assert any(x in command for x in ('rev-parse','status')), 'Only read-only local git inspection allowed'
        else:
            assert Path(command[0]).resolve()==REVIEW_PYTHON
            assert Path(command[4]).resolve()==ARTICLE/'focused_research/review_fulltext_worker.py'
            command=command[:4]+[str(Path(__file__).resolve())]+command[4:]
        events.append({'event':'subprocess','command':command})
        super().__init__(command,*args,**kwargs)
subprocess.Popen=GuardedPopen
@atexit.register
def finish():
    (ROOT/'guard').mkdir(exist_ok=True)
    (ROOT/'guard'/f'{target.stem}-{os.getpid()}.json').write_text(json.dumps({
        'target':str(target),'python':sys.executable,'events':events,
        'blocked_events':sum(bool(x.get('blocked')) for x in events)},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
sys.argv=sys.argv[1:]
sys.path.insert(0,str(target.parent))
runpy.run_path(str(target),run_name='__main__')
