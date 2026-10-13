"""Narrow executable literature implications with typed targets and provenance.

Unsupported implications remain reported, never silently treated as executable.
The caller explicitly supplies adopted IDs. This is not a natural-language
inference engine and never changes the scientific objective behind a frozen run.
"""
from dataclasses import dataclass, replace
import math
import numpy as np
from .literature_bridge import ResearchBrief
from .claim_compiler import compile_claim_implication
from .model_compiler import compile_model


@dataclass(frozen=True)
class ExecutableImplications:
    model_ir: object
    program: object
    receipts: tuple[dict, ...]


def consume_implications(brief, *, model_ir, program, task_conditions, adopted_claim_ids):
    if not isinstance(brief, ResearchBrief): brief = ResearchBrief.model_validate(brief)
    if set(adopted_claim_ids) - set(brief.claim_refs): raise ValueError("unknown adopted claim")
    ir = model_ir; out_program = program; receipts = []
    for claim in brief.claims:
        if claim.claim_id not in adopted_claim_ids: continue
        for specification in claim.implications:
            compiled = compile_claim_implication(claim, specification, task_conditions=task_conditions)
            receipt = {**compiled.model_dump(mode="json"), "claim_id": claim.claim_id,
                       "source_spans": list(claim.source_spans), "consumed": False}
            if compiled.status != "compiled":
                receipts.append(receipt); continue
            target = compiled.target_path; value = compiled.value_or_expression
            if compiled.kind == "model_seed" and target.startswith("ModelCandidate.parameter_values."):
                name = target.removeprefix("ModelCandidate.parameter_values.")
                specs = {p.name: p for p in ir.parameters}
                if name not in specs: receipt.update(status="uncompilable", reason="unknown declared model parameter")
                elif compiled.units != specs[name].unit: receipt.update(status="uncompilable", reason="parameter unit mismatch")
                elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not specs[name].lower <= value <= specs[name].upper:
                    receipt.update(status="uncompilable", reason="seed outside declared parameter domain")
                else:
                    ir = replace(ir, parameters=tuple(replace(p, initial=float(value)) if p.name == name else p for p in ir.parameters))
                    compile_model(ir)
                    receipt.update(consumed=True, consumer="ModelIR.ParameterSpec.initial", before=specs[name].initial, after=float(value))
            elif compiled.kind in {"probe", "control"} and target.startswith("ExperimentProgram.protocol.nodes."):
                # Explicit node addressing prevents an arbitrary dictionary path
                # from masquerading as an executable instruction.
                parts = target.removeprefix("ExperimentProgram.protocol.nodes.").split(".")
                if len(parts) != 2 or out_program is None:
                    receipt.update(status="uncompilable", reason="requires declared program node and field")
                else:
                    node_id, field = parts; nodes = {n.node_id: n for n in out_program.nodes}
                    node = nodes.get(node_id)
                    valid = False
                    if node is not None and field == "wavelengths_nm" and node.kind == "new_evidence" and compiled.units == "nm":
                        grid = np.asarray(value, dtype=float)
                        valid = grid.ndim == 1 and 2 <= len(grid) <= 4096 and np.isfinite(grid).all() and np.all(np.diff(grid)>0) and grid[0] >= ir.domain_nm[0] and grid[-1] <= ir.domain_nm[1]
                        if valid: value = tuple(map(float, grid))
                    elif node is not None and field == "threshold" and node.kind == "branch" and compiled.units == "1":
                        valid = not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value)
                    if not valid: receipt.update(status="uncompilable", reason="unsupported node field, units, or domain")
                    else:
                        arguments = dict(node.arguments); before = arguments.get(field); arguments[field] = value
                        out_program = replace(out_program, nodes=tuple(replace(n, arguments=tuple(arguments.items())) if n.node_id == node_id else n for n in out_program.nodes))
                        receipt.update(consumed=True, consumer="ProgramIR.node.arguments", before=before, after=value)
            else:
                receipt.update(status="uncompilable", reason="no typed runtime consumer; frozen objectives and constraints cannot be edited implicitly")
            receipts.append(receipt)
    return ExecutableImplications(ir, out_program, tuple(receipts))
