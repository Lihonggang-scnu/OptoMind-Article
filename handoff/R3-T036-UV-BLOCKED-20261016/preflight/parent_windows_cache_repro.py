"""One isolated toy-file cache diagnosis; no material data, provider or solver calls."""
from pathlib import Path
import sys,json,hashlib,os,platform
R=Path(__file__).resolve().parent;S=Path(r'D:\OptoMind-Article-3\r3t036a')
sys.path.insert(0,str(S/'focused_research'))
import route_consumer as c
assert Path(c.__file__).resolve()==S/'focused_research/route_consumer.py'
p=R/'windows-cache-repro'/'toy_source.txt';p.parent.mkdir(exist_ok=True);assert not p.exists()
p.write_bytes(b'first');before=p.stat();sig1=c._source_signature(p);first=c._source_sha256(p,sig1)
p.write_bytes(b'other');os.utime(p,ns=(before.st_atime_ns,before.st_mtime_ns));after=p.stat();sig2=c._source_signature(p)
second=c._source_sha256(p,sig2);actual=hashlib.sha256(p.read_bytes()).hexdigest()
x={'source_sha':'408fef514d4ed156ffde03c868a26cea3aba9dfa','source_file':str(Path(c.__file__)),'scope':'isolated toy text file; no production material modification','python':sys.version,'platform':platform.platform(),'signature_fields':['device','inode','size','mtime_ns','ctime_ns'],'signature_before':sig1,'signature_after':sig2,'signature_equal':sig1==sig2,'ctime_ns_before':before.st_ctime_ns,'ctime_ns_after':after.st_ctime_ns,'first_sha256':first,'cached_second_sha256':second,'actual_mutated_sha256':actual,'stale_hash_returned':second!=actual,'cache_info':c._source_sha256.cache_info()._asdict(),'new_model_http':0,'new_physics':0,'new_fee_cny':0,'ledger_writes':0,'production_source_modified':False}
with (R/'MINIMAL_WINDOWS_CACHE_REPRO.json').open('x',encoding='utf-8') as f:json.dump(x,f,ensure_ascii=False,indent=2);f.write('\n')
assert sig1==sig2 and first==second and second!=actual,'ExpectedWindowscachefailure did not reproduce'
print(json.dumps({'signature_equal':x['signature_equal'],'stale_hash_returned':x['stale_hash_returned'],'cache_info':x['cache_info']}))
