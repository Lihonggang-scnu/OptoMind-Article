"""Bounded declarative context selection; does not execute untrusted Python.

This is not an OS sandbox: arbitrary MCE code requires a separately verified
network/process/time/memory-isolated executor and is deliberately unsupported.
"""
from dataclasses import dataclass
from .learning_runner import ActionInput

@dataclass(frozen=True)
class ContextBundle:
    sources: tuple[str,...]
    omissions: tuple[str,...]
    cutoff: int
    cost: int
    view: ActionInput

def mce_trigger(report):
    return bool(report.context_bottleneck and report.forward_verified and report.gain>0 and report.evidence and report.comparison=="legal_history_reaccess")

def build_context(view,*,source_records,cutoff,max_records,report):
    if not mce_trigger(report): raise ValueError("context bottleneck not demonstrated")
    if not isinstance(view,ActionInput): raise TypeError("typed action view required")
    if max_records<0 or max_records>256: raise ValueError("bounded record limit required")
    chosen=[];omitted=[]
    for record in source_records:
        if set(record)!={"source_id","sequence","observations"}: raise ValueError("context source field not whitelisted")
        if record["sequence"]>cutoff or len(chosen)>=max_records: omitted.append(record["source_id"])
        else: chosen.append(record)
    observations=tuple(x for r in chosen for x in r["observations"])
    if len(observations)>4096: raise ValueError("context memory bound")
    data=view.model_dump();data["observations"]=observations
    return ContextBundle(tuple(r["source_id"] for r in chosen),tuple(omitted),cutoff,len(observations),ActionInput.model_validate(data))
