"""Bounded typed grammar expansion. This is a classical baseline, not discovery evidence."""
from dataclasses import dataclass, replace
from itertools import product
from .model_ir import IRNode, ModelIR, ParameterSpec, CompileError
from .model_compiler import compile_model


@dataclass(frozen=True)
class ModelExtensionProposal:
    parent: str
    edit: tuple[str,...]
    origin: str
    source_refs: tuple[str,...]
    expected_difference: str
    task_impact: str
    model_ir: ModelIR


def propose_extensions(parent,*,mismatch,operators=("drift","resolution"),max_depth=2,max_candidates=16,source_refs=()):
    if max_depth<1 or max_depth>4 or max_candidates<1 or max_candidates>256: raise ValueError("bounded search budget required")
    if not mismatch: raise ValueError("mismatch or task-impact trigger required")
    allowed={"drift","resolution","normalize","cauchy","affine"}
    if not set(operators)<=allowed: raise ValueError("unregistered grammar operator")
    proposals=[]; seen={parent.structural_hash}
    for depth in range(1,max_depth+1):
        for sequence in product(operators,repeat=depth):
            nodes=list(parent.nodes); specs=list(parent.parameters); output=parent.output
            for i,op in enumerate(sequence):
                nodeid=f"extension_{len(nodes)}_{i}"
                pname=f"extension.{len(nodes)}.{op}"
                if op=="cauchy":
                    # Typed constitutive replacement, restricted to media feeding a
                    # layer. Substrate assumptions are not silently rewritten.
                    eligible={n.inputs[0] for n in nodes if n.operator=="layer"}
                    targets=[n for n in nodes if n.operator=="constant_nk" and n.node_id in eligible
                             and isinstance(n.attrs.get("n"),str) and n.attrs["n"].startswith("$")]
                    if not targets: continue
                    for target in targets:
                        name=f"extension.{target.node_id}.B_um2"
                        if any(p.name==name for p in specs): continue
                        attrs=(("A",target.attrs.get("n",1.)),("B_um2","$"+name),("k",target.attrs.get("k",0.)))
                        nodes[nodes.index(target)]=replace(target,operator="cauchy_nk",attributes=attrs)
                        specs.append(ParameterSpec(name,0.,.08,.01,"um2"))
                    continue
                if op=="affine":
                    gain=pname+".gain";offset=pname+".offset"
                    attrs=(("gain","$"+gain),("offset","$"+offset))
                    specs.extend((ParameterSpec(gain,.8,1.2,1.),ParameterSpec(offset,-.05,.05,0.)))
                    nodes.append(IRNode(nodeid,"drift",(output,),attrs));output=nodeid
                    continue
                if op=="drift": attrs=(("offset","$"+pname),); spec=ParameterSpec(pname,-.1,.1,0.)
                elif op=="resolution": attrs=(("sigma_nm","$"+pname),); spec=ParameterSpec(pname,0.,30.,3.,"nm")
                else: attrs=(("reference","$"+pname),); spec=ParameterSpec(pname,.5,1.5,1.)
                nodes.append(IRNode(nodeid,op,(output,),attrs)); specs.append(spec); output=nodeid
            ir=replace(parent,nodes=tuple(nodes),parameters=tuple(specs),output=output)
            compile_model(ir)
            if ir.structural_hash not in seen:
                seen.add(ir.structural_hash)
                proposals.append(ModelExtensionProposal(parent.structural_hash,sequence,"typed_classical_beam",tuple(source_refs),mismatch,"requires held-out prediction and terminal evaluation",ir))
            if len(proposals)>=max_candidates:return tuple(proposals)
    return tuple(proposals)


def append_registered_operator(parent,registry,operator_id,*,source_refs):
    if not source_refs: raise ValueError("operator proposal requires provenance")
    expression=registry.expression(operator_id)
    node=IRNode(f"operator_{operator_id[:12]}","expression",(parent.output,),(("expression",expression),("operator_id",operator_id),("input_lower",registry.report(operator_id).input_domain[0]),("input_upper",registry.report(operator_id).input_domain[1])))
    operator_domain=registry.report(operator_id).semantic_domain
    domain=(max(parent.domain_nm[0],operator_domain[0]),min(parent.domain_nm[1],operator_domain[1]))
    if domain[0]>=domain[1]:raise ValueError("operator and model validity domains do not intersect")
    ir=replace(parent,nodes=parent.nodes+(node,),output=node.node_id,domain_nm=domain)
    compile_model(ir)
    return ModelExtensionProposal(parent.structural_hash,(operator_id,),"restricted_new_expression",tuple(source_refs),"explicit new observation law","not yet independently supported",ir)
