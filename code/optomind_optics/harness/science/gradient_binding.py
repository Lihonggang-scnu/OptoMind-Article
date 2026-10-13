"""Provenance-bound nominal multilayer derivatives using the existing VeriTMM core.

The native optimizer loss is an explicitly named objective, not target attainment,
robust yield, or physics acceptance. Unsupported objectives remain unavailable.
"""
from __future__ import annotations

import hashlib
import inspect
import math
from pathlib import Path
from .contracts import ModelCandidate, ResearchTask, canonical_hash


def _engine():
    from ..veritmm_adapter import _ensure_real_veritmm_import
    _ensure_real_veritmm_import()
    import tmm_engine
    from tmm_engine import gradient_api, optimization, differentiable, task_io
    from tmm_engine.material_registry import MaterialRegistry
    return tmm_engine, gradient_api, optimization, differentiable, task_io, MaterialRegistry


def _resolve(simulation, objective):
    engine, api, opt, diff, task_io, registry_cls = _engine()
    if not isinstance(objective, dict) or objective.get("kind") not in {"veritmm_nominal_loss", "optomind_soft_score"}:
        raise ValueError("objective_unsupported: requires an explicit supported loss/score")
    if set(objective) - {"kind", "targets", "robustness"}:
        raise ValueError("objective_unsupported: unrecognized objective fields")
    if objective.get("robustness"):
        raise ValueError("objective_unsupported: robust objective is not nominal loss")
    if not objective.get("targets"):
        raise ValueError("objective_unsupported: explicit targets required")
    from dataclasses import fields
    from tmm_engine.schemas import SpectralTarget
    allowed_target_fields={field.name for field in fields(SpectralTarget)}
    if any(not isinstance(target,dict) or set(target)-allowed_target_fields for target in objective["targets"]):
        raise ValueError("objective_unsupported: unrecognized target formula fields")
    task = task_io.optimization_task_from_dict({"simulation":simulation,"targets":objective["targets"]})
    if task.simulation.solver != "smatrix":
        raise ValueError("solver_unsupported: differentiable binding requires smatrix")
    indices = api._validate_task(task.simulation, None)
    api._require_torch_backend()
    registry = registry_cls()
    optimizer = opt.DifferentiableThicknessOptimizer(registry, device="cpu")
    nk, provenance = optimizer._build_nk_stack(task.simulation)
    from .. import objectives as scoring
    source = {module.__name__:hashlib.sha256(Path(inspect.getfile(module)).read_bytes()).hexdigest()
              for module in (api,opt,diff,task_io,scoring)}
    source[__name__]=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    runtime = {"veritmm":engine.__version__,"torch":optimizer.torch.__version__,"sources":source,
               "solver":"smatrix","dtype":"float64/complex128","device":"cpu"}
    material = {"real":nk.detach().real.tolist(),"imag":nk.detach().imag.tolist(),"provenance":provenance}
    identities = {"grid":task.simulation.spectrum.wavelengths_nm().tolist(),
                  "polarization":{"illumination":simulation.get("illumination"),
                                  "targets":[[t.angle_deg,t.polarization] for t in task.targets]},
                  "materials":material,"tool_version":runtime}
    return task, optimizer, nk, indices, identities



def _meter_gradient_solver(optimizer, counts):
    from .compute_ledger import computational_event, compute_scope
    base_solver = optimizer._solver_class

    class MeteredGradientSolver(base_solver):
        def forward(self, thicknesses, nk_stack, wavelengths, *args, **kwargs):
            batch = int(thicknesses.shape[0])
            points = batch * int(wavelengths.numel())
            with compute_scope("bound_gradient"):
                with computational_event("model_predictions", batch):
                    with computational_event("solver_points", points):
                        counts["differentiable_forward_calls"] += batch
                        counts["solver_points"] += points
                        return super().forward(thicknesses, nk_stack, wavelengths, *args, **kwargs)

    optimizer._solver_class = MeteredGradientSolver


def _objective_value(task, optimizer, nk, objective):
    if objective["kind"] == "veritmm_nominal_loss":
        return lambda row: optimizer._loss_for_thicknesses(task,row.reshape(1,-1),nk)[0][0]
    # The simulator and differentiation remain VeriTMM's. Only the explicitly
    # version-bound harness score is lifted to torch; independent tests compare
    # this to the existing scalar evaluate_objectives implementation.
    from tmm_engine.gradient_api import _make_objective_value, ObjectiveSpec
    torch=optimizer.torch
    functions=[]
    for target in task.targets:
        def score(obs, target=target):
            mask=(obs["wavelengths_nm"]>=target.wavelength_min_nm)&(obs["wavelengths_nm"]<=target.wavelength_max_nm)
            values=obs[target.observable][torch.tensor(mask,dtype=torch.bool)]
            if not values.numel(): raise ValueError("target band outside grid")
            if target.aggregation == "mean": observed=values.mean()
            elif target.constraint == "at_least": observed=values.min()
            elif target.constraint == "at_most": observed=values.max()
            else: observed=values[torch.argmax(torch.abs(values-float(target.target)))]
            scale=max(abs(float(target.target)),abs(float(target.tolerance or 0)),.05)
            if target.constraint == "at_least": return (observed/(observed+scale)).clamp(0,1)
            if target.constraint == "at_most": return (scale/(observed.clamp_min(0)+scale)).clamp(0,1)
            return torch.exp(-torch.abs(observed-float(target.target))/scale)
        functions.append((float(target.weight),_make_objective_value(optimizer,task.simulation,nk,
             ObjectiveSpec("custom",None,"custom",True),target.angle_deg,target.polarization,score)))
    return lambda row: sum(weight*function(row) for weight,function in functions)/sum(weight for weight,_ in functions)


def _check_smoothness(task, optimizer, nk, objective, x):
    from tmm_engine.gradient_api import _make_objective_value, ObjectiveSpec
    torch=optimizer.torch
    for target in task.targets:
        def check(obs, target=target):
            mask=(obs["wavelengths_nm"]>=target.wavelength_min_nm)&(obs["wavelengths_nm"]<=target.wavelength_max_nm)
            values=obs[target.observable][torch.tensor(mask,dtype=torch.bool)]
            if not values.numel(): raise ValueError("target band outside grid")
            if objective["kind"] == "veritmm_nominal_loss":
                criteria=(values-target.target)**2 if target.constraint=="match" else (1-values)**2 if target.constraint=="at_least" else values**2
            else:
                criteria=-values if target.constraint=="at_least" else values if target.constraint=="at_most" else torch.abs(values-target.target)
            if target.aggregation=="worst_case" and values.numel()>1:
                if int(torch.sum(torch.abs(criteria-criteria.max())<=1e-12))>1:
                    raise ValueError("nondifferentiable_objective: tied worst-case wavelengths")
            if objective["kind"]=="optomind_soft_score":
                observed=values.mean() if target.aggregation=="mean" else values[torch.argmax(criteria)]
                if target.constraint=="match" and abs(float(observed.detach())-target.target)<=1e-10:
                    raise ValueError("nondifferentiable_objective: absolute-error score at exact target")
                if target.constraint!="match" and abs(float(observed.detach()))<=1e-12:
                    raise ValueError("nondifferentiable_objective: score clipping boundary")
            return values.mean()
        value=_make_objective_value(optimizer,task.simulation,nk,ObjectiveSpec("custom",None,"custom",True),
                                    target.angle_deg,target.polarization,check)
        with torch.no_grad(): value(x)


def make_gradient_binding(simulation, *, candidate, objective_task, sample):
    """Freeze exact adopted geometry and declared objective; never choose a stack.

    The canonical candidate's model_ir must contain the exact simulation. A caller
    adapting a richer ModelIR must explicitly construct that projection first.
    """
    candidate = ModelCandidate.model_validate(candidate)
    objective_task = ResearchTask.model_validate(objective_task)
    if canonical_hash(candidate.model_ir.get("simulation")) != canonical_hash(simulation):
        raise ValueError("adopted candidate does not bind this simulation")
    if not sample:
        raise ValueError("sample provenance required")
    _, _, _, _, identities = _resolve(simulation, objective_task.objective)
    from .observation_adapter import evidence_binding
    binding = evidence_binding(candidate=simulation, sample=sample, objective=objective_task.objective,
                               objective_version=objective_task.ref().model_dump(), **identities)
    return {"simulation":simulation,"candidate_ref":candidate.ref().model_dump(),
            "objective_ref":objective_task.ref().model_dump(),"objective":objective_task.objective,
            "binding":binding,"provenance":{"candidate":candidate.model_dump(mode="json"),
            "objective_task":objective_task.model_dump(mode="json"),"sample":sample,**identities}}


def compute_bound_gradient(simulation, *, candidate_ref, objective_ref, objective, binding, provenance=None):
    from .observation_adapter import BINDING_FIELDS, evidence_binding
    if set(binding) != set(BINDING_FIELDS):
        raise ValueError("gradient requires complete identity binding")
    if binding["candidate"] != canonical_hash(simulation) or binding["objective"] != canonical_hash(objective):
        raise ValueError("gradient simulation/objective identity mismatch")
    base = {"candidate_ref":candidate_ref.model_dump(),"objective_ref":objective_ref.model_dump(),
            "binding":binding,"variable_definition":"optimizable layer thickness in nm",
            "grid_ref":binding["grid"],"check_ref":None,"objective":objective,
            "compute_cost":{"differentiable_forward_calls":0,"solver_points":0,"paid_calls":0}}
    if not provenance:
        return {**base,"status":"unavailable","reason":"missing candidate/objective/material/runtime provenance"}
    try:
        candidate = ModelCandidate.model_validate(provenance["candidate"])
        task_object = ResearchTask.model_validate(provenance["objective_task"])
        if candidate.ref() != candidate_ref or task_object.ref() != objective_ref:
            raise ValueError("canonical candidate/objective reference mismatch")
        if canonical_hash(candidate.model_ir.get("simulation")) != canonical_hash(simulation) or canonical_hash(task_object.objective) != canonical_hash(objective):
            raise ValueError("canonical candidate/objective payload mismatch")
        task, optimizer, nk, indices, identities = _resolve(simulation, objective)
        expected = evidence_binding(candidate=simulation,sample=provenance["sample"],objective=objective,
                                    objective_version=task_object.ref().model_dump(),**identities)
        if expected != binding or any(canonical_hash(provenance.get(k)) != canonical_hash(v) for k,v in identities.items()):
            raise ValueError("material/grid/polarization/solver/runtime identity mismatch")
        _meter_gradient_solver(optimizer, base["compute_cost"])
        torch = optimizer.torch
        x = torch.tensor([[float(layer.thickness_nm) for layer in task.simulation.stack.layers]],
                         dtype=optimizer.real_dtype,requires_grad=True)
        _check_smoothness(task, optimizer, nk, objective, x)
        value = _objective_value(task, optimizer, nk, objective)
        scalar = value(x)
        scalar.backward()
        from tmm_engine.gradient_api import _finite_difference_cross_check
        checks, max_relative = _finite_difference_cross_check(value,x,indices,torch)
        rows=[]
        for index, check in zip(indices, checks):
            gradient=float(x.grad.reshape(-1)[index])
            fd=float(check["finite_difference_per_nm"])
            passed=math.isfinite(gradient) and math.isfinite(fd) and abs(gradient-fd)<=1e-7+1e-3*max(abs(gradient),abs(fd))
            rows.append({**check,"gradient_per_nm":gradient,"thickness_nm":float(x.detach().reshape(-1)[index]),
                         "sensitivity_per_10nm":gradient*10.,"fd_passed":passed})
        verification={"method":"central_difference_same_torch_backend","independent_physics_check":False,
                      "absolute_tolerance":1e-7,"relative_tolerance":1e-3,"rows":rows,
                      "max_relative_deviation":max_relative,"passed":all(row["fd_passed"] for row in rows)}
        if not math.isfinite(float(scalar.detach())) or not verification["passed"]:
            return {**base,"status":"unavailable","reason":"finite_difference_verification_failed","verification":verification}
        return {**base,"status":"available","method":"autodiff","backend":"torch",
                "metric_value":float(scalar.detach()),"objective_semantics":("native weighted nominal optimizer loss; minimize" if objective["kind"] == "veritmm_nominal_loss" else "harness aggregate_soft_score; maximize"),
                "constraints":task_object.constraints,"constraint_semantics":"preserved separately; derivative is not feasibility/attainment proof",
                "layer_gradients":rows,"verification":verification,"check_ref":canonical_hash(verification),
                "top_influential_layers":sorted(rows,key=lambda r:-abs(r["gradient_per_nm"])),
                "notes":["Proposal evidence only; does not establish physics acceptance or robust yield.",
                         "at_least/at_most follow the native continuous directional loss, not a hard constraint plateau."]}
    except Exception as exc:
        return {**base,"status":"unavailable","reason":f"{type(exc).__name__}: {exc}"}


def adopted_gradient_summary(compiled_payload, run_payload, execution_dir):
    """Legacy producer: bind the explicitly selected best-target candidate receipt.

    A certificate task hash must prove reconstruction at the adopted thicknesses;
    a same-length seed stack or a first experiment is never sufficient evidence.
    """
    import copy
    import json
    unavailable=lambda reason:{"status":"unavailable","reason":reason}
    try:
        choices=[]
        for experiment in run_payload.get("experiment_results",[]):
            portfolio=experiment.get("portfolio") or {}
            selected=(portfolio.get("selected_roles") or {}).get("best_target_score")
            for row in portfolio.get("candidates",[]):
                if row.get("candidate_id")==selected and row.get("physically_admissible") and row.get("target_score") is not None:
                    choices.append((float(row["target_score"]),experiment["experiment_id"],row))
        if not choices: return unavailable("no adopted best-target candidate receipt")
        choices.sort(key=lambda row:row[0],reverse=True)
        if len(choices)>1 and choices[0][0]==choices[1][0]:
            return unavailable("ambiguous adopted best-target candidate; explicit tie resolution required")
        score,experiment_id,row=choices[0]
        experiments=[e for e in compiled_payload.get("experiments",[]) if e.get("experiment_id")==experiment_id]
        if len(experiments)!=1 or experiments[0].get("mode")!="optimize":
            return unavailable("selected experiment lacks exact native optimization objective")
        native=experiments[0]["tmm_task"]
        if "optimization" in native: native=native["optimization"]
        simulation=copy.deepcopy(native["simulation"])
        thicknesses=(row.get("metadata") or {}).get("thicknesses_nm")
        if not thicknesses or len(thicknesses)!=len(simulation["stack"]["layers"]):
            return unavailable("adopted candidate thicknesses are missing or incomplete")
        for layer,thickness in zip(simulation["stack"]["layers"],thicknesses): layer["thickness_nm"]=thickness
        certificates=[a for a in row.get("artifact_ids",[]) if a.endswith("/PHYSICS_ACCEPTANCE_CERTIFICATE.json")]
        if len(certificates)!=1: return unavailable("unique adopted candidate certificate not recorded")
        root=Path(execution_dir).resolve(); path=(root/certificates[0]).resolve()
        if not path.is_relative_to(root): return unavailable("candidate certificate path escapes execution directory")
        certificate=json.loads(path.read_text())
        engine,api,opt,diff,task_io,_=_engine()
        from tmm_engine.hashing import stable_sha256
        from tmm_engine.schemas import dataclass_to_dict
        simulation=dataclass_to_dict(task_io.simulation_task_from_dict(simulation))
        certificate_body={k:v for k,v in certificate.items() if k!="certificate_id"}
        if (certificate.get("accepted") is not True or certificate.get("certificate_id")!=stable_sha256(certificate_body)
                or certificate.get("certificate_id")!=row.get("certificate_id") or certificate.get("task_sha256")!=stable_sha256(simulation)):
            return unavailable("adopted candidate certificate does not bind reconstructed simulation")
        if certificate.get("veritmm_version") != engine.__version__:
            return unavailable("adopted candidate engine version missing or changed")
        from tmm_engine.workbench import TMMWorkbench
        from tmm_engine.material_registry import MaterialRegistry
        _,_,current_materials=TMMWorkbench(MaterialRegistry())._resolve_stack(task_io.simulation_task_from_dict(simulation))
        if certificate.get("material_provenance_sha256") != stable_sha256(current_materials):
            return unavailable("adopted candidate material provenance missing or changed")
        # This differentiates exactly the nominal score used by this portfolio;
        # robustness and fabrication scores are intentionally separate outputs.
        objective={"kind":"optomind_soft_score","targets":copy.deepcopy(native["targets"])}
        run_id=str(run_payload.get("run_id") or compiled_payload.get("task_id") or "legacy")
        candidate=ModelCandidate(object_id=str(row["candidate_id"]),source_run_id=run_id,
            model_ir={"simulation":simulation},parameter_values={"thicknesses_nm":thicknesses})
        task=ResearchTask(object_id=str(compiled_payload["task_id"])+":nominal_score",source_run_id=run_id,
            question="Exact nominal aggregate soft score of the adopted candidate",objective=objective,
            constraints={"source_task":compiled_payload,"robustness":native.get("robustness"),"scope":"nominal score only"})
        request=make_gradient_binding(simulation,candidate=candidate,objective_task=task,
            sample={"candidate_id":row["candidate_id"],"certificate_id":row["certificate_id"],"task_sha256":certificate["task_sha256"]})
        from .contracts import ObjectRef
        result=compute_bound_gradient(simulation,candidate_ref=ObjectRef.model_validate(request["candidate_ref"]),
            objective_ref=ObjectRef.model_validate(request["objective_ref"]),objective=objective,
            binding=request["binding"],provenance=request["provenance"])
        if result["status"]=="available" and not math.isclose(result["metric_value"],score,rel_tol=1e-7,abs_tol=1e-9):
            return unavailable("recomputed objective differs from adopted score; material/runtime/objective provenance cannot be reused")
        return {**result,"selection_role":"best_target_score","candidate_id":row["candidate_id"],
                "experiment_id":experiment_id,"frozen_request":request}
    except Exception as exc:
        return unavailable(f"{type(exc).__name__}: {exc}")
