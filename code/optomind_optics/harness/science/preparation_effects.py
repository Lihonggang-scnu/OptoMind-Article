"""Host-registered preparation laws for explicitly synthetic optical trajectories."""
from dataclasses import dataclass,asdict
import json
import math
import sqlite3
from .contracts import canonical_hash


@dataclass(frozen=True)
class PreparationEffect:
    effect_id: str
    version: str
    source_refs: tuple[str,...]
    required_state: str
    resulting_state: str
    parameter: str
    unit: str
    offset: float
    input_bounds: tuple[float,float]
    output_bounds: tuple[float,float]

    def __post_init__(self):
        for field in ('source_refs','input_bounds','output_bounds'):object.__setattr__(self,field,tuple(getattr(self,field)))
        if not all((self.effect_id,self.version,self.source_refs,self.required_state,self.resulting_state,self.parameter,self.unit)):
            raise ValueError('named versioned source-bound preparation contract required')
        if any(not ref for ref in self.source_refs) or self.required_state==self.resulting_state:raise ValueError('explicit state transition required')
        if len(self.input_bounds)!=2 or len(self.output_bounds)!=2 or not all(map(math.isfinite,(*self.input_bounds,*self.output_bounds,self.offset))):raise ValueError('finite preparation domain required')
        if self.input_bounds[0]>=self.input_bounds[1] or self.output_bounds[0]>=self.output_bounds[1]:raise ValueError('ordered preparation bounds required')

    @property
    def identity(self):return canonical_hash(asdict(self))


class SimulationPreparationProvider:
    """Idempotent transaction binds sample state to actual optical parameters.

    No arbitrary code, network, hardware, or inferred physical preparation law.
    A fresh simulation database must declare each sample's initial parameters.
    """
    def __init__(self,path,*,model,initial_samples,effects,wavelengths_nm):
        self.model=model;self.effects={e.effect_id:e for e in effects};self.wavelengths=tuple(wavelengths_nm)
        if len(self.effects)!=len(effects):raise ValueError('duplicate preparation effect')
        specs={p.name:p for p in model.parameter_specs}
        for effect in effects:
            if effect.parameter not in specs or specs[effect.parameter].unit!=effect.unit:raise ValueError('preparation parameter/unit mismatch')
            spec=specs[effect.parameter]
            if effect.output_bounds[0]<spec.lower or effect.output_bounds[1]>spec.upper:raise ValueError('preparation output exceeds model domain')
        normalized={}
        for sample,(state,parameters) in initial_samples.items():
            if not sample or not state or set(parameters)!=set(specs):raise ValueError('exact sample parameter binding required')
            if any(not math.isfinite(parameters[k]) or not specs[k].lower<=parameters[k]<=specs[k].upper for k in specs):raise ValueError('initial parameter outside model domain')
            normalized[sample]=(state,dict(parameters))
        self.identity=canonical_hash({'model':model.model_id,'samples':normalized,'effects':[asdict(e) for e in effects],'wavelengths':self.wavelengths})
        self.db=sqlite3.connect(str(path));self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS binding(identity TEXT); CREATE TABLE IF NOT EXISTS samples(id TEXT PRIMARY KEY,state TEXT,parameters TEXT); CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,payload_hash TEXT,receipt TEXT);')
        row=self.db.execute('SELECT identity FROM binding').fetchone()
        if row and row[0]!=self.identity:self.db.close();raise ValueError('preparation simulation recovery identity conflict')
        if not row:
            with self.db:
                self.db.execute('INSERT INTO binding VALUES(?)',(self.identity,))
                self.db.executemany('INSERT INTO samples VALUES(?,?,?)',[(k,s,json.dumps(p,sort_keys=True)) for k,(s,p) in normalized.items()])

    def close(self):self.db.close()
    def status(self,key):
        row=self.db.execute('SELECT receipt FROM jobs WHERE id=?',(key,)).fetchone()
        return json.loads(row[0]) if row else {'state':'unknown_outcome'}

    def submit(self,key,payload):
        payload_hash=canonical_hash(payload)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            previous=self.db.execute('SELECT payload_hash,receipt FROM jobs WHERE id=?',(key,)).fetchone()
            if previous:
                if previous[0]!=payload_hash:raise ValueError('preparation idempotency payload conflict')
                return json.loads(previous[1])
            sample=payload.get('sample_id');row=self.db.execute('SELECT state,parameters FROM samples WHERE id=?',(sample,)).fetchone()
            if row is None:raise ValueError('unknown preparation sample')
            state,parameters=row[0],json.loads(row[1]);args=payload.get('arguments',{})
            if payload.get('channel')=='prepare':
                if set(args)!={'effect_id'} or args['effect_id'] not in self.effects:raise ValueError('unregistered preparation effect')
                effect=self.effects[args['effect_id']]
                if state!=effect.required_state or payload.get('required_state')!=effect.required_state or payload.get('resulting_state')!=effect.resulting_state:raise ValueError('preparation state binding mismatch')
                old=parameters[effect.parameter];new=old+effect.offset
                if not effect.input_bounds[0]<=old<=effect.input_bounds[1] or not effect.output_bounds[0]<=new<=effect.output_bounds[1]:raise ValueError('preparation law outside registered domain')
                parameters[effect.parameter]=new
                self.db.execute('UPDATE samples SET state=?,parameters=? WHERE id=?',(effect.resulting_state,json.dumps(parameters,sort_keys=True),sample))
                receipt={'state':'completed','cost':{'calls':1.,'cost':0.},'effect_ref':effect.identity,'sample_id':sample,'before':{effect.parameter:old},'after':{effect.parameter:new},'unit':effect.unit,'resulting_state':effect.resulting_state,'new_evidence':False,'scope':'declared synthetic preparation law'}
            elif payload.get('channel')=='R':
                if args or payload.get('required_state')!=state:raise ValueError('measurement sample state/arguments mismatch')
                values=self.model.predict(parameters,self.wavelengths).tolist()
                receipt={'state':'completed','cost':{'calls':1.,'cost':0.},'values':values,'sample_id':sample,'sample_state':state,'parameter_hash':canonical_hash(parameters),'provider_kind':'deterministic_simulation','physical_validation':False}
            else:raise ValueError('unregistered simulation channel')
            self.db.execute('INSERT INTO jobs VALUES(?,?,?)',(key,payload_hash,json.dumps(receipt,allow_nan=False)))
            return receipt
