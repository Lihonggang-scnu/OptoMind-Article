"""Structural and finite-domain prediction equivalence, without posterior weight inflation."""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class EquivalenceClass:
    representative: str
    members: tuple[str,...]
    criterion: str
    scope: str


def deduplicate_models(models,*,wavelengths_nm,parameters=None,tolerance=1e-8):
    groups=[]; predictions=[]; hashes=set()
    for model in models:
        if model.model_id in hashes: continue
        hashes.add(model.model_id)
        pars=(parameters or {}).get(model.model_id,{p.name:p.initial for p in model.parameter_specs})
        prediction=model.predict(pars,wavelengths_nm)
        for i,old in enumerate(predictions):
            if np.allclose(prediction,old,atol=tolerance,rtol=0): groups[i].append(model)
            else: continue
            break
        else:
            groups.append([model]); predictions.append(prediction)
    return tuple(g[0] for g in groups),tuple(EquivalenceClass(g[0].model_id,tuple(m.model_id for m in g),"finite-domain prediction","specified grid and parameter values only") for g in groups)


def algebraic_expression_key(expression):
    """Conservative commutative normalization; no division/cancellation identities.

    Parenthesization is preserved to avoid changing IEEE floating-point order.
    Only binary operand exchange in +/* is admitted on the same finite domain.
    """
    import ast
    from .operator_sandbox import validate_expression
    validate_expression(expression)
    def key(node):
        if isinstance(node,ast.BinOp):
            operands=(key(node.left),key(node.right))
            if isinstance(node.op,(ast.Add,ast.Mult)):operands=tuple(sorted(operands,key=repr))
            return (type(node.op).__name__,operands)
        if isinstance(node,ast.UnaryOp):return(type(node.op).__name__,key(node.operand))
        if isinstance(node,ast.Call):return('call',node.func.id,tuple(key(a) for a in node.args))
        if isinstance(node,ast.Name):return('name',node.id)
        if isinstance(node,ast.Constant):return('constant',node.value)
        raise ValueError('unsupported expression normalization')
    return key(ast.parse(expression,mode='eval').body)


def algebraic_model_key(ir):
    """Same typed DAG, domains and parameter-sharing, with commutative AST keys."""
    from dataclasses import replace
    from .contracts import canonical_hash
    nodes=[]
    for node in ir.nodes:
        normalized=repr(algebraic_expression_key(node.attrs['expression'])) if node.operator=='expression' else None
        attributes=tuple((k,normalized if normalized is not None and k=='expression' else canonical_hash(normalized) if normalized is not None and k=='operator_id' else v) for k,v in node.attributes)
        nodes.append(replace(node,attributes=attributes))
    return canonical_hash({'normalized_structure':replace(ir,nodes=tuple(nodes)).structural_hash,
                           'criterion':'binary-commutative-only/1'})
