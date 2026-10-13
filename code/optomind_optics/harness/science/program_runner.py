"""Durable node receipts; ambiguous acquisitions recover through the shared C03 gate."""
from dataclasses import dataclass
import json
import sqlite3
import numpy as np


@dataclass(frozen=True)
class ProgramExecutionReceipt:
    program_id: str
    program_hash: str
    state: str
    nodes: dict
    sample_states: dict
    blocked_node: str | None = None


class ProgramRunner:
    def __init__(self,path,*,gates,providers):
        self.db=sqlite3.connect(str(path)); self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("CREATE TABLE IF NOT EXISTS programs(id TEXT PRIMARY KEY,hash TEXT,samples TEXT); CREATE TABLE IF NOT EXISTS program_nodes(program TEXT,node TEXT,receipt TEXT,PRIMARY KEY(program,node));")
        self.gates=gates; self.providers=providers

    def close(self):self.db.close()

    @staticmethod
    def _local_receipt(node,dependencies):
        if node.kind=="derived_view":
            source=dependencies[0]; calibration=None
            if dict(node.arguments).get("calibration")=="reference_offset":
                from .reference_calibration import correct_reference
                if "expected_reference" not in dict(node.arguments):raise ValueError("frozen reference expectation required")
                calibration=correct_reference(dependencies[0],dependencies[1],expected_reference=dict(node.arguments)["expected_reference"])
            values=np.asarray(calibration["values"] if calibration else source.get("values",[]),float)
            if not len(values) or not np.all(np.isfinite(values)): raise ValueError("derived view requires finite raw values")
            stat=dict(node.arguments).get("statistic","mean")
            if stat=="mean_absolute_difference":
                other=np.asarray(dependencies[1].get("values",[]),float)
                if other.shape!=values.shape or not np.isfinite(other).all() or source.get("coordinate")!=dependencies[1].get("coordinate"):
                    raise ValueError("polarization contrast requires aligned finite derived spectra")
                value=np.mean(np.abs(values-other))
            else:value={"mean":np.mean,"max":np.max,"min":np.min,"range":np.ptp}[stat](values)
            receipt={"state":"completed","value":float(value),"new_evidence":False,"derived_from":node.inputs,"acquisition_id":source.get("acquisition_id"),"correlation_group":source.get("correlation_group","")}
            if calibration:receipt.update(calibration)
            if stat=="mean_absolute_difference":
                receipt.update(statistic=stat,shared_reference_ids=[r.get("shared_reference_id") for r in dependencies],
                    uncertainty_scope="causal descriptive statistic only; correlated inputs are not independent evidence")
        elif node.kind=="branch":
            value=dependencies[0].get("value")
            if value is None: raise ValueError("branch requires completed scalar statistic")
            args=dict(node.arguments);threshold=args["threshold"]
            test={"gt":value>threshold,"lt":value<threshold,"ge":value>=threshold,"le":value<=threshold}[args["comparison"]]
            receipt={"state":"completed","value":bool(test),"new_evidence":False,"consumed":node.inputs}
        elif node.kind=="terminate":
            receipt={"state":"completed","new_evidence":False}
        else:
            # Computation is explicitly an identity aggregation, never arbitrary code.
            receipt={"state":"completed","new_evidence":False,"inputs":dependencies}
        return receipt

    @staticmethod
    def _payload(program,node):
        return {"program_hash":program.content_hash,"node_id":node.node_id,"channel":node.channel,"sample_id":node.sample_id,
                "required_state":node.required_state,"resulting_state":node.resulting_state,"arguments":dict(node.arguments),"correlation_group":node.correlation_group}

    @staticmethod
    def _effect_receipt(node,key,job):
        receipt={"state":"completed","new_evidence":node.kind=="new_evidence","acquisition_id":key if node.kind=="new_evidence" else None,
                 "provider_receipt":job.provider_receipt,"values":job.provider_receipt.get("values",[]),
                 "correlation_group":job.provider_receipt.get("measurement_receipt",{}).get("calibration_group",node.correlation_group),
                 "declared_correlation_group":node.correlation_group,"cost_refs":job.cost_refs}
        if node.kind=="new_evidence" and not receipt["values"]:raise ValueError("completed measurement lacks raw values")
        if node.kind=="state_transition":
            ack=job.provider_receipt
            if ack.get("sample_id")!=node.sample_id or ack.get("resulting_state")!=node.resulting_state or not ack.get("effect_ref"):
                raise ValueError("preparation receipt lacks matching source-bound postcondition acknowledgment")
        return receipt

    def _verify_cached_prefix(self,program,cached,saved_samples,prediction_commits):
        """Reconstruct local projections from gate-bound receipts before any effect.

        No journal schema migration is needed. Legacy payloads without explicit
        state keys remain admissible only when their original gate hash matches.
        """
        from .contracts import canonical_hash
        verified={};states=dict(program.initial_samples);terminated=False;missing=False
        if set(cached)-{n.node_id for n in program.nodes}:raise ValueError("cached program contains unknown nodes")
        for node in program.nodes:
            if node.node_id not in cached:missing=True;continue
            if missing:raise ValueError("cached program receipts are not a completed prefix")
            dependencies=[verified[i] for i in node.inputs]
            skip=terminated or any(r["state"]=="skipped" for r in dependencies)
            if node.condition:
                source,expected=node.condition
                condition=verified[source]
                if condition["state"]=="skipped":skip=True
                elif condition["state"]!="completed":raise ValueError("cached branch source incomplete")
                else:skip=skip or condition["value"]!=expected
            if skip:expected_receipt={"state":"skipped","new_evidence":False}
            elif node.kind in ("new_evidence","state_transition"):
                if node.sample_id and states[node.sample_id]!=node.required_state:raise ValueError("cached sample precondition mismatch")
                if node.node_id not in prediction_commits:raise ValueError("cached action lacks frozen prediction binding")
                gate=self.gates[node.channel];key=f"{program.program_id}:{program.revision}:{node.node_id}"
                job=gate.receipt(key)
                if job is None or job.state!="completed":raise ValueError("cached effect lacks completed authoritative gate receipt")
                binding=gate.db.execute("SELECT payload_hash,prediction FROM jobs WHERE key=?",(key,)).fetchone()
                payload=self._payload(program,node)
                legacy={k:v for k,v in payload.items() if k not in {"required_state","resulting_state"}}
                if binding[0] not in {canonical_hash(payload),canonical_hash(legacy)} or canonical_hash(json.loads(binding[1]))!=canonical_hash(prediction_commits[node.node_id]):
                    raise ValueError("cached effect payload or prediction binding changed")
                expected_receipt=self._effect_receipt(node,key,job)
                if node.kind=="state_transition" and node.sample_id:states[node.sample_id]=node.resulting_state
            else:
                expected_receipt=self._local_receipt(node,dependencies)
                terminated=terminated or node.kind=="terminate"
            if canonical_hash(cached[node.node_id])!=canonical_hash(expected_receipt):raise ValueError("cached program receipt differs from verified inputs or gate receipt")
            verified[node.node_id]=expected_receipt
        if states!=saved_samples:raise ValueError("cached sample states differ from replayed transitions")
        return verified

    def run(self,plan,*,prediction_commits):
        program=plan.program
        row=self.db.execute("SELECT hash,samples FROM programs WHERE id=?",(program.program_id,)).fetchone()
        if row and row[0]!=program.content_hash: raise ValueError("program recovery identity conflict")
        if not row:
            samples=dict(program.initial_samples)
            self.db.execute("INSERT INTO programs VALUES(?,?,?)",(program.program_id,program.content_hash,json.dumps(samples)));self.db.commit()
        else:samples=json.loads(row[1])
        receipts={n:json.loads(r) for n,r in self.db.execute("SELECT node,receipt FROM program_nodes WHERE program=?",(program.program_id,))}
        receipts=self._verify_cached_prefix(program,receipts,samples,prediction_commits)
        terminated=False
        for node in program.nodes:
            if node.node_id in receipts:
                terminated=terminated or (node.kind=="terminate" and receipts[node.node_id]["state"]=="completed")
                continue
            dependencies=[receipts.get(i) for i in node.inputs]
            if any(x is None for x in dependencies): raise ValueError("dependency was not completed")
            skip=terminated or any(r["state"]=="skipped" for r in dependencies)
            if node.condition:
                source,expected=node.condition
                if source not in receipts: raise ValueError("branch source unavailable")
                if receipts[source]["state"] == "skipped":
                    skip = True
                elif receipts[source]["state"] != "completed":
                    raise ValueError("branch source unavailable")
                else:
                    skip=skip or receipts[source]["value"]!=expected
            if skip:receipt={"state":"skipped","new_evidence":False}
            elif node.kind in ("new_evidence","state_transition"):
                if node.sample_id and samples[node.sample_id]!=node.required_state: raise ValueError("sample was consumed or changed; state mismatch")
                if node.node_id not in prediction_commits: raise ValueError("prediction must be frozen before action")
                gate=self.gates[node.channel]; provider=self.providers[node.channel]
                key=f"{program.program_id}:{program.revision}:{node.node_id}"
                payload=self._payload(program,node)
                job=gate.execute(provider,key,payload,estimate=dict(node.estimate),prediction_commit=prediction_commits[node.node_id])
                if job.state!="completed": return ProgramExecutionReceipt(program.program_id,program.content_hash,job.state,receipts,samples,node.node_id)
                receipt=self._effect_receipt(node,key,job)
                if node.kind=="state_transition" and node.sample_id:samples[node.sample_id]=node.resulting_state
            else:
                receipt=self._local_receipt(node,dependencies)
                terminated=terminated or node.kind=="terminate"
            with self.db:
                self.db.execute("INSERT INTO program_nodes VALUES(?,?,?)",(program.program_id,node.node_id,json.dumps(receipt,allow_nan=False)))
                self.db.execute("UPDATE programs SET samples=? WHERE id=?",(json.dumps(samples),program.program_id))
            receipts[node.node_id]=receipt
        return ProgramExecutionReceipt(program.program_id,program.content_hash,"completed",receipts,samples)
