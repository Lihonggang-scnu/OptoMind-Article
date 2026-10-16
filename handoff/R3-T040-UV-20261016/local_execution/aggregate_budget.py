"""T031 original-case/batch/project gates around the unchanged native reserve."""
from contextlib import contextmanager, closing
from decimal import Decimal
from pathlib import Path
import json
import sqlite3
import uuid

CASES = ['e2e-01-methane-swir-window','e2e-04-solarblind-uv-triple','e2e-05-visual-absorber-cr']
TASK_IDS = {'R3-T032:'+c:c for c in CASES}
TASK_IDS['R3-T036:'+CASES[1]]=CASES[1]
TASK_IDS['R3-T037:'+CASES[1]]=CASES[1]
TASK_IDS['R3-T038:'+CASES[1]]=CASES[1]
TASK_IDS['R3-T040:'+CASES[1]]=CASES[1]


def cost(rows):
    total=Decimal(0)
    for r in rows:
        x=Decimal(str((r['actual_cny'] if r['status']=='settled' else r['amount_cny']) or 0))
        if not x.is_finite() or x<0:raise RuntimeError('invalid ledger amount')
        total+=x
    return total


def checked_totals(original,pool,amount,history,task_id,*,admission=True):
    if task_id not in TASK_IDS or not history.get('attribution_complete'):raise RuntimeError('original case accounting identity required')
    old=original.execute('SELECT * FROM reservations').fetchall();new=pool.execute('SELECT * FROM reservations').fetchall()
    if any(r['status']!='settled' for r in old+new):raise RuntimeError('unresolved original/pool spend blocks all paid work')
    unique={}
    for row in old+new:
        k=row['reservation_id']
        if k in unique and dict(row)!=dict(unique[k]):raise RuntimeError('conflicting duplicate expense identity')
        unique[k]=row
    links=pool.execute('SELECT c.reservation_id,c.task_id FROM r3_task_calls c').fetchall()
    task_ids={key:{r['reservation_id'] for r in links if r['task_id']==key} for key in TASK_IDS}
    known=set(history['all_reservation_ids_at_audit']);current=set().union(*task_ids.values())
    if not known.issubset(unique):raise RuntimeError('historical reservation disappeared')
    if set(unique)-known-current:raise RuntimeError('new unattributed activity; no paid dispatch')
    selected={c:set(history['case_reservation_ids'][c])|set().union(*(ids for key,ids in task_ids.items() if TASK_IDS[key]==c)) for c in CASES}
    if any(not ids.issubset(unique) for ids in selected.values()):raise RuntimeError('historical case identity missing')
    project=cost(list(unique.values()));case_used=cost([unique[k] for k in selected[TASK_IDS[task_id]]]);batch=cost([unique[k] for k in set().union(*selected.values())])
    if project+amount>220:raise RuntimeError('project cumulative220 exhausted')
    if case_used+amount>8:raise RuntimeError('original case cumulative8 exhausted')
    if batch+amount>24:raise RuntimeError('three original cases cumulative24 exhausted')
    if admission and sum(len(x) for x in task_ids.values())>=12:raise RuntimeError('T032 batch12 actual reservation attempts exhausted')
    case_http_cap=6 if task_id=='R3-T040:'+CASES[1] else 4
    if admission and sum(len(ids) for key,ids in task_ids.items() if TASK_IDS[key]==TASK_IDS[task_id])>=case_http_cap:raise RuntimeError('shared original case HTTP ceiling exhausted')
    return {'project_exposure_cny':str(project),'case_exposure_cny':str(case_used),'three_case_exposure_cny':str(batch),'reservation_cny':str(amount),
            'project_cap_cny':220,'original_case_cap_cny':8,'three_case_cap_cny':24,'case_id':TASK_IDS[task_id],
            'batch_attempts':sum(len(x) for x in task_ids.values()),'native_task_http_cap':case_http_cap,'batch_http_cap':12,
            'both_ledgers_locked_across_check_and_reservation':True}


def guard_class(base,original_path,history,audit_dir):
    original_path=Path(original_path).resolve(strict=True);audit_dir=Path(audit_dir)
    class AggregateGuard(base):
        _aggregate_amount=None
        @contextmanager
        def _transaction(self):
            if self._aggregate_amount is None:
                with super()._transaction() as pool:yield pool
                return
            with closing(sqlite3.connect(original_path.as_uri()+'?mode=rw',uri=True,timeout=30)) as original:
                original.row_factory=sqlite3.Row;original.execute('BEGIN IMMEDIATE')
                try:
                    with super()._transaction() as pool:
                        snapshot=checked_totals(original,pool,self._aggregate_amount,history,self.task_id)
                        audit_dir.mkdir(parents=True,exist_ok=True)
                        with (audit_dir/(uuid.uuid4().hex+'.json')).open('x',encoding='utf-8') as f:json.dump(snapshot,f,indent=2);f.write('\n')
                        yield pool
                finally:original.rollback()
        def _reserve(self,request_key,request_hash,amount_cny,call_id):
            if self._aggregate_amount is not None:raise RuntimeError('nested reservation')
            self._aggregate_amount=Decimal(str(amount_cny))
            try:return super()._reserve(request_key,request_hash,amount_cny,call_id)
            finally:self._aggregate_amount=None
    return AggregateGuard
