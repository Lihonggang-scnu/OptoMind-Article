"""Fail-closed compiler for measured channels, bounded resources, and branch dependencies."""
import math
from .program_ir import CompiledExecutionPlan


def compile_program(program,*,capabilities,limits):
    if not program.program_id or not program.nodes or len(program.nodes)>256: raise ValueError("program identity and bounded graph required")
    known={}; totals={}; sample_states=dict(program.initial_samples); channels=set()
    for node in program.nodes:
        if node.node_id in known or any(i not in known for i in node.inputs): raise ValueError("program DAG input missing or duplicate")
        if node.kind not in ("new_evidence","state_transition","derived_view","computation","branch","terminate"): raise ValueError("unsupported program node kind")
        if node.condition:
            branch,expected=node.condition
            if branch not in known or known[branch].kind!="branch" or type(expected) is not bool: raise ValueError("branch must consume a completed earlier predicate")
        if node.kind in ("new_evidence","state_transition"):
            if node.channel not in capabilities: raise ValueError("unregistered measurement or preparation channel")
            cap=capabilities[node.channel]
            if node.channel not in cap.channels: raise ValueError("provider does not declare channel")
            if node.kind=="state_transition" and cap.effect_class=="read_only":raise ValueError("read-only provider cannot prepare a sample")
            if node.kind=="new_evidence" and cap.effect_class!="external_measurement": raise ValueError("computation cannot be relabeled as new evidence")
            if node.sample_id and node.sample_id not in sample_states: raise ValueError("unknown sample")
            if not node.condition and node.sample_id:
                if sample_states[node.sample_id]!=node.required_state: raise ValueError("sample state precondition fails")
                if node.kind=="state_transition": sample_states[node.sample_id]=node.resulting_state
            if not node.estimate: raise ValueError("external action requires explicit resource estimate")
            channels.add(node.channel)
        elif node.channel: raise ValueError("local computation cannot acquire a channel")
        if node.kind in ("derived_view","branch") and len(node.inputs)!=(2 if node.kind=="derived_view" and (dict(node.arguments).get("calibration")=="reference_offset" or dict(node.arguments).get("statistic")=="mean_absolute_difference") else 1): raise ValueError("statistic/predicate requires exactly one dependency")
        if node.kind=="derived_view" and dict(node.arguments).get("calibration") not in (None,"reference_offset"): raise ValueError("unknown calibration operation")
        if node.kind=="branch":
            args=dict(node.arguments)
            if args.get("comparison") not in ("gt","lt","ge","le") or not math.isfinite(args.get("threshold",float("nan"))): raise ValueError("bounded scalar branch predicate required")
        if node.kind=="derived_view" and dict(node.arguments).get("statistic","mean") not in ("mean","max","min","range","mean_absolute_difference"): raise ValueError("unknown derived statistic")
        for resource,cost in node.estimate:
            if resource not in limits or not math.isfinite(cost) or cost<0: raise ValueError("unbounded or unknown resource")
            totals[resource]=totals.get(resource,0.)+cost
        known[node.node_id]=node
    # Explore all abstract branch assignments before any side effect. Correlated
    # predicates may make some paths unreachable; including them is conservative.
    from itertools import product
    branches=[n.node_id for n in program.nodes if n.kind=="branch"]
    if len(branches)>12: raise ValueError("branch verification resource limit")
    for assignment in product((False,True),repeat=len(branches)):
        predicates=dict(zip(branches,assignment)); states=dict(program.initial_samples); active={}; stopped=False
        for node in program.nodes:
            enabled=not stopped and all(active.get(i,False) for i in node.inputs)
            if node.condition: enabled=enabled and active.get(node.condition[0],False) and predicates[node.condition[0]]==node.condition[1]
            active[node.node_id]=enabled
            if not enabled: continue
            if node.kind in ("new_evidence","state_transition") and node.sample_id:
                if states[node.sample_id]!=node.required_state: raise ValueError("sample state precondition fails on a legal branch")
                if node.kind=="state_transition":states[node.sample_id]=node.resulting_state
            if node.kind=="terminate":stopped=True
    if any(value>limits[key] for key,value in totals.items()): raise ValueError("worst-case program resource bound exceeds budget")
    return CompiledExecutionPlan(program,tuple(sorted(totals.items())),tuple(sorted(channels)))
