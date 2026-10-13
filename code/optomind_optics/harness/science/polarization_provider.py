"""Durable explicitly synthetic polarized intensity channels using existing TMM."""
from dataclasses import replace,asdict
import json,sqlite3
from types import MappingProxyType
import numpy as np
from .model_compiler import compile_model
from .contracts import canonical_hash


class SyntheticPolarizationProvider:
    def __init__(self,path,*,samples,angle_deg,detector_offset=0.,cost=1.):
        if not np.isfinite([angle_deg,detector_offset,cost]).all() or not 0<=angle_deg<90 or cost<0:raise ValueError('finite synthetic instrument declaration required')
        self.samples=MappingProxyType({k:(ir,MappingProxyType(dict(parameters))) for k,(ir,parameters) in samples.items()});self.angle=angle_deg;self.offset=detector_offset;self.cost=cost;self.models={}
        for sample,(ir,parameters) in self.samples.items():
            if not sample or not any(n.operator=='tmm' for n in ir.nodes):raise ValueError('registered optical specimen required')
            for pol,fraction in (('R_s',1.),('R_p',0.)):
                nodes=tuple(replace(n,attributes=tuple((k,v) for k,v in n.attributes if k!='s_fraction')+(('s_fraction',fraction),)) if n.operator=='tmm' else n for n in ir.nodes)
                self.models[sample,pol]=compile_model(replace(ir,nodes=nodes))
        identity=canonical_hash({'samples':{k:{'ir':asdict(v[0]),'parameters':dict(v[1])} for k,v in self.samples.items()},'angle':angle_deg,'offset':detector_offset,'cost':cost})
        self.models=MappingProxyType(self.models)
        self._instrument_binding=(self.angle,self.offset,self.cost)
        self.db=sqlite3.connect(str(path));self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS binding(identity TEXT); CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload_hash TEXT,receipt TEXT);')
        row=self.db.execute('SELECT identity FROM binding').fetchone()
        if row and row[0]!=identity:self.db.close();raise ValueError('polarization provider recovery conflict')
        if not row:
            with self.db:self.db.execute('INSERT INTO binding VALUES(?)',(identity,))

    def close(self):self.db.close()
    def status(self,key):
        row=self.db.execute('SELECT receipt FROM jobs WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else {'state':'unknown_outcome'}

    def submit(self,key,payload):
        if (self.angle,self.offset,self.cost)!=self._instrument_binding:raise ValueError("synthetic instrument declaration changed")
        digest=canonical_hash(payload)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row=self.db.execute('SELECT payload_hash,receipt FROM jobs WHERE id=?',(key,)).fetchone()
            if row:
                if row[0]!=digest:raise ValueError('polarization request identity conflict')
                return json.loads(row[1])
            sample,channel=payload.get('sample_id'),payload.get('channel');args=payload.get('arguments',{})
            if (sample,channel) not in self.models or set(args)!={'wavelengths_nm'} or payload.get('required_state','ready')!='ready':raise ValueError('unregistered polarized channel/sample/arguments')
            wl=tuple(args['wavelengths_nm']);parameters=self.samples[sample][1]
            values=self.models[sample,channel].predict(parameters,wl,angle_deg=self.angle)+self.offset
            raw={'wavelengths_nm':wl,'values':values.tolist(),'covariance':np.zeros((len(wl),len(wl))).tolist(),
                'calibration_group':payload.get('correlation_group',''),'calibration_std':0.,'channel':channel,'angle_deg':self.angle,
                'noise_semantics':'deterministic fixture with declared shared additive offset'}
            receipt={'state':'completed','cost':{'calls':1.,'cost':self.cost},'values':values.tolist(),'measurement_receipt':raw,
                'provider_kind':'synthetic_polarized_VeriTMM','device_capability_claim':False}
            self.db.execute('INSERT INTO jobs VALUES(?,?,?)',(key,digest,json.dumps(receipt,allow_nan=False)))
            return receipt
