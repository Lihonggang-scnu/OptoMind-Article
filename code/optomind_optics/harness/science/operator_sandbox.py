"""Restricted expression interpreter: no Python eval, imports, attributes, IO or loops."""
import ast
import numpy as np

_FUNCTIONS = {"sin": np.sin, "cos": np.cos, "exp": np.exp, "sqrt": np.sqrt, "log": np.log, "abs": np.abs}


def validate_expression(expression, *, max_nodes=96):
    if not isinstance(expression, str) or len(expression) > 2048:
        raise ValueError("operator source resource limit")
    tree=ast.parse(expression,mode="eval")
    if sum(1 for _ in ast.walk(tree)) > max_nodes:
        raise ValueError("operator AST resource limit")
    allowed=(ast.Expression,ast.BinOp,ast.UnaryOp,ast.Call,ast.Name,ast.Load,ast.Constant,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.Pow,ast.UAdd,ast.USub)
    for node in ast.walk(tree):
        if not isinstance(node,allowed): raise ValueError("operator syntax not permitted")
        if isinstance(node,ast.Name) and node.id not in {"x","wavelength_nm",*_FUNCTIONS}: raise ValueError("operator name not permitted")
        if isinstance(node,ast.Call) and (not isinstance(node.func,ast.Name) or node.func.id not in _FUNCTIONS or len(node.args)!=1 or node.keywords): raise ValueError("operator function not permitted")
        if isinstance(node,ast.Constant) and (type(node.value) not in (int,float) or not np.isfinite(node.value) or abs(node.value)>1e12): raise ValueError("operator constant invalid")
        if isinstance(node,ast.Pow) : continue
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Pow):
            if not isinstance(node.right,ast.Constant) or type(node.right.value) not in (int,float) or abs(node.right.value)>8: raise ValueError("operator exponent limit")
    def dimension(node):
        if isinstance(node,ast.Expression): return dimension(node.body)
        if isinstance(node,ast.Constant): return 0.
        if isinstance(node,ast.Name): return 1. if node.id=="wavelength_nm" else 0.
        if isinstance(node,ast.UnaryOp): return dimension(node.operand)
        if isinstance(node,ast.Call):
            dim=dimension(node.args[0])
            if node.func.id=="sqrt":return dim/2
            if node.func.id=="abs":return dim
            if dim!=0:raise ValueError("transcendental function requires dimensionless argument")
            return 0.
        left,right=dimension(node.left),dimension(node.right)
        if isinstance(node.op,(ast.Add,ast.Sub)):
            if left!=right:raise ValueError("operator dimensional addition mismatch")
            return left
        if isinstance(node.op,ast.Mult):return left+right
        if isinstance(node.op,ast.Div):return left-right
        if isinstance(node.op,ast.Pow):return left*node.right.value
        raise ValueError("untyped expression")
    if dimension(tree)!=0:raise ValueError("intensity operator must return dimensionless output")
    return tree


def evaluate_expression(expression,x,wavelength_nm):
    tree=validate_expression(expression)
    x=np.asarray(x,float); wl=np.asarray(wavelength_nm,float)
    if x.shape!=wl.shape or x.size>100000: raise ValueError("operator array resource limit")
    def evaluate(node):
        if isinstance(node,ast.Expression): return evaluate(node.body)
        if isinstance(node,ast.Constant): return node.value
        if isinstance(node,ast.Name):
            if node.id=="x": return x
            if node.id=="wavelength_nm": return wl
            raise ValueError("function cannot be used as a value")
        if isinstance(node,ast.UnaryOp): return -evaluate(node.operand) if isinstance(node.op,ast.USub) else evaluate(node.operand)
        if isinstance(node,ast.Call): return _FUNCTIONS[node.func.id](evaluate(node.args[0]))
        a,b=evaluate(node.left),evaluate(node.right)
        if isinstance(node.op,ast.Add): return a+b
        if isinstance(node.op,ast.Sub): return a-b
        if isinstance(node.op,ast.Mult): return a*b
        if isinstance(node.op,ast.Div): return a/b
        if isinstance(node.op,ast.Pow): return a**b
        raise ValueError("invalid operator")
    with np.errstate(all="raise"):
        result=np.broadcast_to(evaluate(tree),x.shape).copy()
    if not np.all(np.isfinite(result)): raise ValueError("nonfinite operator result")
    return result
