"""T042 original-case/task/project gates around the unchanged native reserve."""
from contextlib import contextmanager, closing
from decimal import Decimal
from pathlib import Path
import json
import sqlite3
import uuid

CASES = ['e2e-12-lwir-ge-window']
TASK_IDS = {task+':'+c:c for task in ('R3-T042',) for c in CASES}


def cost(rows):
    total=Decimal(0)
    for r in rows:
        x=Decimal(str((r['actual_cny'] if r['status']=='settled' else r['amount_cny']) or 0))
        if not x.is_finite() or x<0:raise RuntimeError('invalid ledger amount')
        total+=x
    return total


def checked_totals(original,pool,amount,history,task_id,*,admission=True):
    if task_id not in TASK_IDS or not history.get('attribution_complete'):raise RuntimeError('original case accounting identity required')
    rows=original.execute('SELECT * FROM reservations').fetchall()+pool.execute('SELECT * FROM reservations').fetchall()
    if any(r['status']!='settled' for r in rows):raise RuntimeError('unresolved original/pool spend blocks paid work')
    unique={}
    for row in rows:
        k=row['reservation_id']
        if k in unique and dict(row)!=dict(unique[k]):raise RuntimeError('conflicting duplicate expense identity')
        unique[k]=row
    links=pool.execute('SELECT reservation_id,task_id FROM r3_task_calls').fetchall()
    current={r['reservation_id'] for r in links if r['task_id']==task_id}
    known=set(history['all_reservation_ids_at_audit'])
    if not known.issubset(unique):raise RuntimeError('historical reservation disappeared')
    if set(unique)-known-current:raise RuntimeError('new unattributed activity; no paid dispatch')
    selected=set(history['case_reservation_ids'][TASK_IDS[task_id]])|current
    if not selected.issubset(unique):raise RuntimeError('historical case identity missing')
    project=cost(list(unique.values()));case_used=cost([unique[k] for k in selected])
    if project+amount>220:raise RuntimeError('project cumulative220 exhausted')
    if case_used+amount>8:raise RuntimeError('original case cumulative8 exhausted')
    if admission and len(current)>=4:raise RuntimeError('T042 four actual reservation attempts exhausted')
    return {'project_exposure_cny':str(project),'case_exposure_cny':str(case_used),'task_exposure_cny':str(cost([unique[k] for k in current])),
            'reservation_cny':str(amount),'project_cap_cny':220,'original_case_cap_cny':8,'task_cap_cny':8,'case_id':TASK_IDS[task_id],
            'task_attempts':len(current),'native_task_http_cap':4,'not_original_T032_three_case_batch':True,
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
