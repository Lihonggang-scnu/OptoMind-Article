"""Recoverable, opt-in C13 adapter over the existing C07/C10/C11/C12 engines.

The policy sees only purchased receipts and public session manifests. Fixed typed
classical proposals are grammar search, never a claim of scientific discovery.
Selection sessions may influence later proposals; the final validation session is
sealed until a single candidate and terminal design have been committed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from pathlib import Path
import json
import hashlib
import numpy as np
from scipy.stats import norm

from .adequacy import calibrate_adaptive_null, evaluate_adequacy
from .contracts import (ResearchTask, ModelCandidate, DecisionContext, ExperimentProgram,
                        PredictionSpec, PredictionEvaluation, EvidenceUse,
                        SelectedResearchResult, ScientificClaim, AcquisitionIdentity, ResearchOutcome, canonical_hash)
from .controller import MeasurementAction, decide_next, execute_round, route_research_state
from .evidence_use import EvidenceUseGraph, EvidenceUseRecord
from .identifiability import IdentifiabilityReport
from .inference import FitResult, fit_model, fit_result_to_state
from .likelihood_blocks import JointLikelihood, blocks_from_observations
from .model_compiler import compile_model
from .model_ir import ModelIR, IRNode, ParameterSpec
from .model_search import propose_extensions
from .prediction import PredictionCommit, commit_prediction, evaluate_prediction
from .program_ir import ProgramIR, ProgramNode
from .program_compiler import compile_program
from .program_runner import ProgramRunner
from .program_search import adaptive_measurement_program
from .terminal_problem import TerminalDecisionProblem, TerminalSolution
from .world_client import MeasurementReceipt, receipt_to_observation
from .compute_ledger import metered_scope
from optomind_research.runtime.artifact_store import atomic_write_json


@dataclass(frozen=True)
class OpenResearchConfig:
    run_id: str
    training_session: str
    selection_sessions: tuple[str, ...]
    validation_session: str
    reference_session: str
    session_groups: tuple[tuple[str, str], ...]
    budget: float = 10.
    max_iterations: int = 2
    unknown_exploration_limit: int = 2
    wavelengths_nm: tuple[float, ...] = tuple(np.linspace(420., 860., 17))
    validation_wavelengths_nm: tuple[float, ...] = tuple(np.linspace(433., 847., 19))
    design_thicknesses_nm: tuple[float, ...] = tuple(np.linspace(45., 230., 75))
    design_parameter: str = "film.thickness_nm"
    objective_wavelength_nm: float = 550.
    objective_target: float = 0.
    reference_reflectance: float = .04
    measurement_std: float = .002
    calibration_std: float = .001
    measurement_cost: float = 1.
    action_loss_cost: float = 1e-6
    fit_starts: int = 3
    max_nfev: int = 160
    null_replicates: int = 19
    alpha: float = .05
    seed: int = 2718
    grammar: tuple[str, ...] = ("cauchy", "affine", "resolution")
    max_candidates: int = 6
    model_expansion_enabled: bool = True
    program_composition_enabled: bool = True
    compute_prediction_limit: int = 2000000
    compute_solver_point_limit: int = 200000000
    compute_valuation_sample_limit: int = 1000000
    compute_milp_limit: int = 1000
    robust_noise_std_multipliers: tuple[float, ...] = (1.,)
    robust_thresholds: tuple[float, ...] = ()
    robust_prior_vertices: tuple[tuple[float, ...], ...] = ()
    value_estimator: str = "binary"
    gaussian_value_protocol: tuple[tuple[str, object], ...] = ()
    compute_utility_rates: tuple[tuple[str, float], ...] = ()
    compute_utility_source: str = "unpriced_compute_zero_utility_assumption"
    smc_config: tuple[tuple[str, object], ...] = ()
    profile_config: tuple[tuple[str, object], ...] = ()
    terminal_tolerance_nm: float = 0.
    terminal_lower_nm: float | None = None
    terminal_upper_nm: float | None = None
    terminal_loss_transform: str = "identity"
    terminal_risk_aversion: float = 1.
    terminal_optimizer_budget: int = 0
    adopt_source_qualified_implications: bool = False
    literature_request_limit: int = 0
    literature_max_papers: int = 4
    literature_max_supplementary_calls: int = 0

    def __post_init__(self):
        if type(self.model_expansion_enabled) is not bool or type(self.program_composition_enabled) is not bool:
            raise ValueError("factorial intervention flags must be booleans")
        if any(type(v) is not int or v < 1 for v in (self.compute_prediction_limit,self.compute_solver_point_limit,self.compute_valuation_sample_limit,self.compute_milp_limit)):
            raise ValueError("positive integer compute limits required")
        rates=dict(self.compute_utility_rates)
        if set(rates)-{"model_predictions","solver_points","valuation_samples","milp_solves"} or any(not np.isfinite(v) or v<0 for v in rates.values()) or not self.compute_utility_source:
            raise ValueError("explicit nonnegative compute utility conversions required")
        if self.value_estimator not in {"binary","gaussian_mc","robust_gaussian_bins"}:raise ValueError("unsupported value estimator")
        if not self.robust_noise_std_multipliers or not np.isfinite(self.robust_noise_std_multipliers).all() or min(self.robust_noise_std_multipliers)<=0:
            raise ValueError("positive declared robust noise vertices required")
        if self.robust_thresholds and (not np.isfinite(self.robust_thresholds).all() or np.any(np.diff(self.robust_thresholds)<=0)):
            raise ValueError("ordered finite robust bin thresholds required")
        if self.gaussian_value_protocol:
            from .gaussian_terminal_value import GaussianValueProtocol
            GaussianValueProtocol(**dict(self.gaussian_value_protocol))
        if self.terminal_loss_transform not in {"identity","exponential"} or not np.isfinite([self.terminal_tolerance_nm,self.terminal_risk_aversion]).all() or self.terminal_tolerance_nm<0 or self.terminal_risk_aversion<=0:
            raise ValueError("invalid terminal constraints or utility")
        if type(self.terminal_optimizer_budget) is not int or self.terminal_optimizer_budget<0:
            raise ValueError("nonnegative explicit optimizer budget required")
        if self.profile_config:
            from .profile_identifiability import ProfileIdentifiabilityConfig
            ProfileIdentifiabilityConfig(**dict(self.profile_config))
        if self.smc_config:
            from .tempered_smc import SMCConfig
            SMCConfig(**dict(self.smc_config))
        roles=(self.training_session, *self.selection_sessions, self.validation_session, self.reference_session)
        groups=dict(self.session_groups)
        if (not self.run_id or len(set(roles)) != len(roles) or set(groups) != set(roles)
                or len(set(groups.values())) != len(roles)):
            raise ValueError("fit, selection, reference and final-validation sessions need distinct frozen groups")
        if not 1 <= self.max_iterations <= min(8,len(self.selection_sessions)) or not 0 <= self.unknown_exploration_limit <= self.max_iterations:
            raise ValueError("bounded iterations and explicit unknown exploration allowance required")
        if not 0<=self.literature_request_limit<=self.max_iterations or not 0<=self.literature_max_papers<=32 or not 0<=self.literature_max_supplementary_calls<=4:
            raise ValueError("literature requests require a separate finite exploration cap")
        numeric=[self.budget,self.measurement_std,self.calibration_std,self.measurement_cost,self.action_loss_cost,self.objective_target,self.reference_reflectance,self.objective_wavelength_nm]
        if not np.isfinite(numeric).all() or min(self.budget,self.measurement_std,self.measurement_cost) <= 0 or min(self.calibration_std,self.action_loss_cost)<0:
            raise ValueError("finite positive acquisition bounds required")
        if self.budget < 2*self.measurement_cost or self.null_replicates < 19 or self.null_replicates > 99 or not 0 < self.alpha < 1:
            raise ValueError("reserve initial and final acquisition; require bounded scoped null calibration")
        if not 1 <= self.fit_starts <= 32 or not 1 <= self.max_nfev <= 2000 or not 1 <= self.max_candidates <= 32:
            raise ValueError("finite fitting/search budget required")
        if len({f"thickness:{d:.8g}" for d in self.design_thicknesses_nm})!=len(self.design_thicknesses_nm):
            raise ValueError("terminal design IDs must remain unique at the declared precision")
        for grid in (self.wavelengths_nm,self.validation_wavelengths_nm,self.design_thicknesses_nm):
            if not 2<=len(grid)<=4096 or not np.isfinite(grid).all() or np.any(np.diff(grid)<=0):
                raise ValueError("ordered finite grids required")


def initial_planar_model():
    """Public starting grammar; unknown film parameters, known substrate contract."""
    return ModelIR((IRNode("film","constant_nk",attributes=(("n","$film.n"),)),
                    IRNode("layer","layer",("film",),(("thickness","$film.thickness_nm"),),unit="nm"),
                    IRNode("substrate","constant_nk",attributes=(("n",1.5),)),
                    IRNode("stack","stack",("layer","substrate"),unit="nm"),IRNode("R","tmm",("stack",))),
                   "R",(ParameterSpec("film.n",1.2,2.8,1.8),ParameterSpec("film.thickness_nm",40.,240.,110.,"nm")),(400.,900.))


class OpenResearchLoop:
    """A policy adapter, not another solver, evaluator, optimizer or job runner.

    ``runner`` must use the same durable C03 execution boundary as its host.
    A provider's public groups/costs must agree with the frozen config; observations
    are checked before they can enter a fit. No evaluator world is accepted here.
    """
    def __init__(self, *, repository, runner: ProgramRunner, config: OpenResearchConfig,
                 initial_model: ModelIR | None = None, task: ResearchTask | None = None, proposal_provider=None, evidence_provider=None, experiment_policy=None, method_provider=None, terminal_optimizer_task=None, method_provider_factory=None, operator_admission=None):
        self.repository=repository; self.runner=runner; self.config=config;self.proposal_provider=proposal_provider;self.evidence_provider=evidence_provider
        self.experiment_policy=experiment_policy;self.method_provider=method_provider;self.terminal_optimizer_task=terminal_optimizer_task
        self.operator_admission=operator_admission
        self._terminal_designs={}
        if config.terminal_optimizer_budget and terminal_optimizer_task is None:raise ValueError("terminal optimizer requires an explicit valid optimization task")
        self.initial_model=initial_model or initial_planar_model()
        expected_objective={"wavelength_nm":config.objective_wavelength_nm,"target_R":config.objective_target,
                            "loss":"squared_reflectance_error","design_pool_nm":config.design_thicknesses_nm}
        if config.terminal_loss_transform!="identity":expected_objective.update(loss_transform=config.terminal_loss_transform,risk_aversion=config.terminal_risk_aversion)
        if task is not None and canonical_hash(task.objective)!=canonical_hash(expected_objective):
            raise ValueError("Article research task objective differs from the frozen open-loop objective")
        self.task=task or ResearchTask(object_id=f"{config.run_id}:task",source_run_id=config.run_id,
            question="Choose a coating thickness using purchased optical evidence",
            objective=expected_objective,
            constraints={"open_research_config":asdict(config),"initial_model_hash":canonical_hash(asdict(self.initial_model)),
                         "evidence_tier":"V1_synthetic","paid_network_calls":0})
        if method_provider_factory is not None:
            if method_provider is not None:raise ValueError("supply method provider or factory, not both")
            method_provider=method_provider_factory(self.task)
            self.method_provider=method_provider
        self._prepared_methods={"model_fragment":(),"program":(),"policy":()}
        if method_provider is not None:
            if method_provider.task_ref!=self.task.ref():raise ValueError("method library target must bind the exact frozen research task")
            self._prepared_methods={kind:method_provider.prepare(kind) for kind in self._prepared_methods}
            policy_assets=self._prepared_methods["policy"]
            if policy_assets:
                selected_policy=policy_assets[0].compiled
                if self.experiment_policy is not None and self.experiment_policy!=selected_policy:raise ValueError("explicit and retrieved policy conflict")
                self.experiment_policy=selected_policy
        compile_model(self.initial_model)
        specs={p.name:p for p in self.initial_model.parameters}
        if config.design_parameter not in specs:
            raise ValueError("terminal design parameter must bind a declared model parameter")
        design_spec=specs[config.design_parameter]
        if design_spec.unit!="nm":raise ValueError("terminal thickness grid requires an nm-valued parameter")
        if min(config.design_thicknesses_nm)<design_spec.lower or max(config.design_thicknesses_nm)>design_spec.upper:
            raise ValueError("terminal design grid exceeds declared model parameter domain")
        if not self.initial_model.domain_nm[0] <= config.objective_wavelength_nm <= self.initial_model.domain_nm[1]:
            raise ValueError("terminal objective exceeds model wavelength domain")
        from .compute_ledger import ComputeLedger
        self.compute_ledger=ComputeLedger(repository.root/"compute.sqlite", {"model_predictions":config.compute_prediction_limit,"solver_points":config.compute_solver_point_limit,"valuation_samples":config.compute_valuation_sample_limit,"milp_solves":config.compute_milp_limit})
        self.selected_result=None
        self._graph=EvidenceUseGraph()
        self._put(self.task)
        runtime_files=("open_research_loop.py","contracts.py","nested_contracts.py","prediction.py","world_client.py","method_lifecycle.py","open_research_cli.py","prefreeze_task_adoption.py","offline_method_config.py","joint_proposer.py","literature_request.py","literature_bridge.py","controller.py","model_search.py","model_compiler.py",
                       "inference.py","profile_identifiability.py","model_ir.py","operator_admission.py","operator_registry.py","operator_sandbox.py","model_equivalence.py","trace_extractor.py","method_runtime.py","method_assets.py","method_retriever.py","applicability.py","rollout.py","gaussian_terminal_value.py","robust_gaussian_value.py","robust_voi.py","action_value.py","terminal_optimization.py","typed_implication_runtime.py","compute_ledger.py","tempered_smc.py","likelihood_blocks.py","program_runner.py","program_search.py","polarization_provider.py","reference_calibration.py","program_compiler.py","terminal_problem.py","evsi.py")
        runtime_fingerprints={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in runtime_files}
        protocol=DecisionContext(object_id=f"{config.run_id}:protocol",source_run_id=config.run_id,
            input_refs=(self.task.ref(),),summary={"config":asdict(config),"initial_model":asdict(self.initial_model),
            "evidence_roles":"fit and selection may influence proposals; final validation never fits or selects",
            "support_scope":"bounded typed proposals; independent calibration sessions on same specimen, not transfer",
            "proposal_protocol":proposal_provider.protocol_id if proposal_provider is not None else "typed_classical_beam/1",
            "runtime_fingerprints":runtime_fingerprints,
            "evidence_provider_protocol":evidence_provider.protocol_id if evidence_provider is not None else None,
            "experiment_policy":self.experiment_policy.model_dump(mode="json") if self.experiment_policy else None,
            "method_library_snapshot":method_provider.library_snapshot if method_provider else None,
            "method_library_repository":method_provider.repository_id if method_provider else None,
            "terminal_optimizer_task":asdict(terminal_optimizer_task) if terminal_optimizer_task is not None else None,
            "operator_admission_protocol":operator_admission.protocol_id if operator_admission else None})
        self._put(protocol)
        for gate in set(runner.gates.values()):
            if gate.limits.get("cost",0.) != config.budget:
                raise ValueError("C03 gate budget must equal frozen total research budget")
        for node in self.initial_model.nodes:
            if node.operator=="tmm" and node.attrs.get("aperture_deg",0.):
                raise ValueError("this optical adapter requires the declared normal-incidence purchase protocol")
        self._frozen_contract=canonical_hash(self._binding())

    def _binding(self):
        def provider_binding(provider):
            if provider is None:return None
            result={"protocol_id":provider.protocol_id}
            for key in ("responses","source_units","allow_live","max_tokens"):
                if hasattr(provider,key):result[key]=getattr(provider,key)
            return result
        return {"config":asdict(self.config),"initial_model":asdict(self.initial_model),
                "task":self.task.model_dump(mode="json"),"terminal_optimizer_task":asdict(self.terminal_optimizer_task) if self.terminal_optimizer_task is not None else None,"experiment_policy":self.experiment_policy.model_dump(mode="json") if self.experiment_policy else None,
                "method_library_snapshot":self.method_provider.library_snapshot if self.method_provider else None,
                "method_library_repository":self.method_provider.repository_id if self.method_provider else None,
                "proposal_provider":provider_binding(self.proposal_provider),
                "evidence_provider":provider_binding(self.evidence_provider),
                "operator_admission":self.operator_admission.public_protocols() if self.operator_admission else None,
                "gates":{channel:{"limits":gate.limits,"capability":gate.capability.model_dump(mode="json")} for channel,gate in self.runner.gates.items()}}

    def _check_contract(self):
        if canonical_hash(self._binding())!=self._frozen_contract:
            raise ValueError("open research config, task, provider or gate changed after protocol commitment")

    def _find(self, oid):
        latest=self.repository.latest_snapshot()
        ref=next((r for r in latest.object_refs if r.object_id==oid),None) if latest else None
        return self.repository.load(ref) if ref else None

    def _put(self, obj):
        old=self._find(obj.object_id)
        if old is not None:
            if old.ref()!=obj.ref(): raise ValueError("open-loop checkpoint binding changed: "+obj.object_id)
            return old
        latest=self.repository.latest_snapshot()
        self.repository.commit([obj],task_ref=self.task.ref(),evidence_cutoff=obj.object_id,
                               expected_sequence=latest.committed_sequence if latest else 0)
        return obj

    def _context(self, key, summary, refs=()):
        return self._put(DecisionContext(object_id=f"{self.config.run_id}:{key}",source_run_id=self.config.run_id,
                                        input_refs=tuple(refs),summary=summary))

    def _use(self, observation, candidate, purpose):
        oid=f"use:{canonical_hash([observation.ref().model_dump(),candidate.object_id,purpose])}"
        record=EvidenceUseRecord(observation.acquisition_id,candidate.object_id,purpose,len(self._graph.records)+1,("R",))
        self._graph.add(record)
        return self._put(EvidenceUse(object_id=oid,source_run_id=self.config.run_id,
            input_refs=(candidate.ref(),),observation_ref=observation.ref(),purpose=purpose,
            raw_block_ids=("R",),likelihood_count=1 if purpose=="fit" else 0))

    @metered_scope("fit")
    def _fit(self, ir, observations):
        self._check_contract()
        cfg=self.config
        key=canonical_hash({"ir":asdict(ir),"observations":[o.ref().model_dump() for o in observations],
                            "starts":cfg.fit_starts,"max_nfev":cfg.max_nfev,"seed":cfg.seed,"smc_config":cfg.smc_config,"profile_config":cfg.profile_config,
                            "terminal_action_contract":asdict(cfg) if cfg.profile_config else None})
        cache=self._find("fit-cache:"+key)
        if cache:
            data=dict(cache.summary["fit"])
            data["identifiability"]=IdentifiabilityReport(**data["identifiability"])
            fit=FitResult(**data)
            candidate=self.repository.load(cache.input_refs[0]);state=self.repository.load(cache.input_refs[1])
        else:
            model=compile_model(ir)
            from .model_ir import model_contract_report
            self._context("model-contract:"+ir.structural_hash,model_contract_report(ir))
            from .tempered_smc import SMCConfig
            from .profile_identifiability import ProfileIdentifiabilityConfig
            terminal_action=None
            if cfg.profile_config:
                spec=next(p for p in model.parameter_specs if p.name==cfg.design_parameter)
                lower=spec.lower if cfg.terminal_lower_nm is None else cfg.terminal_lower_nm
                upper=spec.upper if cfg.terminal_upper_nm is None else cfg.terminal_upper_nm
                feasible=tuple(d for d in cfg.design_thicknesses_nm if lower<=d-cfg.terminal_tolerance_nm and d+cfg.terminal_tolerance_nm<=upper)
                if not feasible:raise ValueError("profile action diagnostic has no feasible frozen-grid design")
                grid=tuple(sorted(set(cfg.wavelengths_nm+(cfg.objective_wavelength_nm,))))
                objective_index=grid.index(cfg.objective_wavelength_nm)
                def terminal_action(parameters):
                    # Both supported utility transforms are strictly increasing, so pointwise argmin is identical.
                    losses=[(float(model.predict({**parameters,cfg.design_parameter:d},grid)[objective_index])-cfg.objective_target)**2 for d in feasible]
                    return f"thickness:{feasible[int(np.argmin(losses))]:.8g}"
            fit=fit_model(model,blocks_from_observations(observations),starts=cfg.fit_starts,seed=cfg.seed,
                          max_nfev=cfg.max_nfev,laplace_samples=24,
                          bounded_uniform_prior={p.name:(p.lower,p.upper) for p in ir.parameters},
                          smc_config=SMCConfig(**dict(cfg.smc_config)) if cfg.smc_config else None,
                          terminal_action=terminal_action,
                          profile_config=ProfileIdentifiabilityConfig(**dict(cfg.profile_config)) if cfg.profile_config else None)
            candidate=self._put(ModelCandidate(object_id="candidate:"+key,source_run_id=cfg.run_id,
                input_refs=tuple(o.ref() for o in observations),model_ir=asdict(ir),parameter_values=fit.parameters,
                domain={"wavelength_nm":ir.domain_nm,"provenance":"typed_classical_grammar","support":"fitted_only"}))
            state=self._put(fit_result_to_state(fit,candidate,observations,object_id="inference:"+key,source_run_id=cfg.run_id))
            self._put(DecisionContext(object_id="fit-cache:"+key,source_run_id=cfg.run_id,
                input_refs=(candidate.ref(),state.ref()),summary={"fit":asdict(fit)}))
        for observation in observations:self._use(observation,candidate,"fit")
        return candidate,fit,state

    @metered_scope("assess")
    def _assess(self, candidate, fit, observations):
        """Full refit bootstrap for this frozen family's current acquisition schedule.

        This is a *plug-in, model-conditional* calibration, not a global repeated-
        search false-discovery guarantee. Independent final validation remains
        mandatory and never upgrades a mechanism claim.
        """
        from .model_ir import model_ir_from_dict
        cfg=self.config;key="adequacy:"+candidate.content_hash
        saved=self._find(key)
        if saved:return saved.summary
        model=compile_model(model_ir_from_dict(candidate.model_ir));blocks=blocks_from_observations(observations)
        likelihood=JointLikelihood(blocks)
        if not fit.success:
            result=evaluate_adequacy(identity_valid=True,numerical_valid=False,noise_validated=likelihood.gaussian_validated,
                residual_statistic=None,refit_completed=False,identifiable=False,decision_equivalent=None)
        else:
            means=likelihood.predict(model,fit.parameters)
            null_nfev=[];null_model_calls=[];null_unknown_counts=[]
            def null_task(seed):
                values=means+likelihood.cholesky@np.random.default_rng(seed).normal(size=len(means))
                simulated=tuple(replace(block,values=tuple(map(float,values[sl]))) for block,sl in zip(blocks,likelihood.slices))
                try:
                    trial=fit_model(model,simulated,starts=cfg.fit_starts,seed=cfg.seed,max_nfev=cfg.max_nfev,laplace_samples=1)
                except (ValueError,RuntimeError):
                    null_unknown_counts.append(True)
                    raise
                null_nfev.append(trial.diagnostics.get("nfev",0))
                null_model_calls.append(trial.diagnostics.get("model_predict_calls"))
                if not trial.success:raise ValueError("null refit did not converge; adequacy uncalibrated")
                return 2*trial.loss
            try:
                calibration=calibrate_adaptive_null(null_task,tuple(range(cfg.seed+100,cfg.seed+100+cfg.null_replicates)),
                    "frozen-family-refit-bootstrap/1","Plug-in fixed family, current raw grids and joint covariance; not global adaptive search calibration",cfg.alpha)
            except (ValueError,RuntimeError):calibration=None
            result=evaluate_adequacy(identity_valid=True,numerical_valid=True,noise_validated=likelihood.gaussian_validated,
                residual_statistic=2*fit.loss,refit_completed=True,
                identifiable=fit.identifiability.rank==fit.identifiability.parameter_count,decision_equivalent=None,
                calibration=calibration,task_impact="unknown")
        summary={**asdict(result),"route":route_research_state(result.state),
            "audits":["frozen_family_refit_bootstrap_calibrated" if a=="whole_adaptive_task_calibrated" else a for a in result.audits],
            "null_refit_count":len(null_nfev) if fit.success else 0,"null_nfev":sum(null_nfev) if fit.success else 0,
            "null_model_predict_calls_known":sum(v for v in null_model_calls if v is not None) if fit.success else 0,
            "null_predict_call_count_incomplete":bool(null_unknown_counts or any(v is None for v in null_model_calls)) if fit.success else False,
            "cost_scope":"nfev counts optimizer residual steps only; model_predict_calls includes inference numerical derivative probes; non-inference forwards are separate",
            "calibration_scope":"plug-in frozen-family refit bootstrap only; search-wide false-rejection risk remains unknown",
            "discovery_claim":False,"training_discrepancy":2*fit.loss}
        self._put(DecisionContext(object_id=key,source_run_id=cfg.run_id,input_refs=(candidate.ref(),),summary=summary))
        return summary

    def _literature(self, iteration, candidate, assessment):
        cfg=self.config
        if self.evidence_provider is None or cfg.literature_request_limit==0:return None
        oid=f"{cfg.run_id}:literature:{iteration}"
        old=self._find(oid+":result")
        if old is not None:return old.summary.get("research_brief")
        attempted=self._find(oid+":request")
        if attempted is not None:
            self._context(f"literature:{iteration}:result",{"status":"outcome_unknown_after_interruption",
                "research_brief":None,"retry":"not automatic; request already consumed bounded exploration capacity"},(attempted.ref(),))
            return None
        requests=[ref for ref in self.repository.latest_snapshot().object_refs
                  if ref.object_id.startswith(f"{cfg.run_id}:literature:") and ref.object_id.endswith(":request")]
        if len(requests)>=cfg.literature_request_limit:return None
        from optomind_research.runtime.upgrade3.article_evidence import EvidenceRequest,EvidenceBundle
        from .literature_bridge import build_research_brief
        request=EvidenceRequest(question=f"Find source-anchored model assumptions or experimental controls relevant to this task: {self.task.question}. Current empirical adequacy state: {assessment['state']}.",
            object_conditions={"wavelength_domain_nm":list(self.initial_model.domain_nm),"angle_deg":0.,
                "candidate_hash":candidate.content_hash,"objective":self.task.objective},
            required_outputs=("model_seed_or_experiment_control",),material_roles=("model_mechanism","experiment_control","counterexample_mismatch"),
            current_gaps=(assessment["state"],),budget={"max_papers":cfg.literature_max_papers,
                "max_supplementary_calls":cfg.literature_max_supplementary_calls})
        frozen=request.to_dict()
        reservation=self._context(f"literature:{iteration}:request",{"request":frozen,"provider_protocol":self.evidence_provider.protocol_id,
            "value_status":"unknown","bounded_exploration_slot":len(requests)+1,"slot_consumed_before_request":True,
            "likelihood_weight":None,"claims_remain":"LiteratureClaim"},(candidate.ref(),))
        try:
            bundle=self.evidence_provider.request(request)
            raw=bundle.to_dict() if hasattr(bundle,"to_dict") else bundle
            verified=EvidenceBundle.from_mapping(raw)
            if canonical_hash(verified.request)!=canonical_hash(frozen) or canonical_hash(request.to_dict())!=canonical_hash(frozen):
                raise ValueError("evidence request binding changed")
            if len(verified.source_units)>cfg.literature_max_papers:raise ValueError("evidence provider exceeded frozen source cap")
            brief=build_research_brief(verified,task_ref=self.task.ref(),citation_namespace=cfg.run_id.replace("::","-"))
        except (OSError,ValueError,TypeError,KeyError) as exc:
            self._context(f"literature:{iteration}:result",{"status":"failed_or_rejected","error":str(exc),
                "research_brief":None,"request_slot_consumed":True,"value_status":"unknown"},(reservation.ref(),))
            return None
        self._context(f"literature:{iteration}:result",{"status":verified.status,"research_brief":brief.model_dump(mode="json"),
            "evidence_bundle":verified.to_dict(),"value_status":"unknown","likelihood_weight":None,
            "scope":"source-attributed proposal context only; never an observation or current scientific conclusion"},(reservation.ref(),))
        return brief.model_dump(mode="json")

    def _program(self, key, session, *, adaptive=False, final=False):
        cfg=self.config;grid=cfg.validation_wavelengths_nm if final else cfg.wavelengths_nm
        if adaptive:
            return adaptive_measurement_program(program_id=f"{cfg.run_id}:{key}",channel="R",reference_channel="R",
                coarse_grid=grid[::2],fine_grid=grid,threshold=5*cfg.measurement_std,
                sample_id=session,reference_id=cfg.reference_session,cost=cfg.measurement_cost,expected_reference=cfg.reference_reflectance)
        node=ProgramNode("measure","new_evidence",channel="R",sample_id=session,
            arguments=(("wavelengths_nm",tuple(grid)),),estimate=(("calls",1.),("cost",cfg.measurement_cost)),
            correlation_group=dict(cfg.session_groups)[session])
        return ProgramIR(f"{cfg.run_id}:{key}",(node,),((session,"ready"),))

    def _required_new_cost(self, program):
        total=0.
        for node in program.nodes:
            if node.kind not in ("new_evidence","state_transition"):continue
            key=f"{program.program_id}:{program.revision}:{node.node_id}"
            saved=self.runner.db.execute("SELECT receipt FROM program_nodes WHERE program=? AND node=?",(program.program_id,node.node_id)).fetchone()
            if saved and json.loads(saved[0])["state"] in {"completed","skipped"}:continue
            if self.runner.gates[node.channel].receipt(key) is None:total+=dict(node.estimate).get("cost",0.)
        return total

    def _can_execute(self, program, *, reserve):
        # Existing reservations count too, including ambiguous effects after a crash.
        gates=set(self.runner.gates.values());used=sum(g.usage().get("cost",0.) for g in gates)
        # An already-reserved final effect is included in durable gate usage.
        # Replaying earlier completed nodes must not reserve it a second time.
        final_key=f"{self.config.run_id}:final-validation:1:measure"
        if self.runner.gates["R"].receipt(final_key) is not None:reserve=0.
        return used+self._required_new_cost(program)+reserve <= self.config.budget+1e-12

    @metered_scope("predictions")
    def _predictions(self, program, candidates):
        from .model_ir import model_ir_from_dict
        cfg=self.config;commits={};specs=[]
        artifact=self._put(ExperimentProgram(object_id="program:"+program.content_hash,source_run_id=cfg.run_id,
            objective_ref=self.task.ref(),protocol=asdict(program)))
        frontend=self._context("frontend:bare",{"frontend_id":"bare","removable_layers":[],"role":"acquisition operator, independent of proposed sample model"})
        for node in program.nodes:
            if node.kind!="new_evidence":continue
            identity=AcquisitionIdentity(program_ref=artifact.ref(),node_id=node.node_id,
                sample_id=cfg.run_id+(":reference" if node.sample_id==cfg.reference_session else ":specimen"),
                session_id=node.sample_id,frontend_ref=frontend.ref(),
                conditions={"angle_deg":0.,"polarization":"unpolarized","coordinate_units":"nm"})
            coordinates=tuple(dict(node.arguments)["wavelengths_nm"]);predictions=[]
            for candidate in candidates or (None,):
                name=candidate.content_hash if candidate else "prior"
                oid=f"prediction:{program.content_hash}:{node.node_id}:{name}"
                spec=self._find(oid)
                if spec is None:
                    if node.sample_id==cfg.reference_session:
                        mean=np.full(len(coordinates),cfg.reference_reflectance)
                        radius=np.full(len(mean),norm.ppf(1-cfg.alpha/(2*len(mean)))*np.hypot(cfg.measurement_std,cfg.calibration_std))
                    elif candidate is None:
                        mean=np.full(len(coordinates),.5);radius=np.full(len(coordinates),.6)
                    else:
                        model=compile_model(model_ir_from_dict(candidate.model_ir))
                        mean=model.predict(candidate.parameter_values,coordinates)
                        # A transparent conditional plug-in tolerance, not a claim
                        # of posterior predictive coverage or parameter certainty.
                        radius=np.full(len(mean),norm.ppf(1-cfg.alpha/(2*len(mean)))*np.hypot(cfg.measurement_std,cfg.calibration_std))
                    spec=PredictionSpec(object_id=oid,source_run_id=cfg.run_id,
                        input_refs=(candidate.ref(),) if candidate else (),snapshot_id=self.repository.latest_snapshot().snapshot_id,
                        candidate_ref=candidate.ref() if candidate else None,program_ref=artifact.ref(),
                        raw_channel="R",units="1",statistic="simultaneous_envelope",observable="reflectance",
                        tolerance_source="declared Gaussian acquisition noise; plug-in parameters; no global coverage guarantee",
                        lower=tuple(map(float,mean-radius)),upper=tuple(map(float,mean+radius)),coordinate=coordinates,
                        grid={"session":node.sample_id,"program":program.content_hash,"acquisition_identity":identity.model_dump(mode="json")},
                        distribution={"mean":list(map(float,mean)),"candidate_id":candidate.object_id if candidate else None,
                                      "parameter_uncertainty":"not included; final test is empirical scoped validation"})
                    commit_prediction(self.repository,spec,task_ref=self.task.ref(),evidence_cutoff="before:"+program.program_id)
                predictions.append(spec.ref().model_dump(mode="json"));specs.append((node.node_id,spec))
            commits[node.node_id]={"predictions":predictions,"program_ref":artifact.ref().model_dump(mode="json"),
                                    "protocol_ref":self._find(f"{cfg.run_id}:protocol").ref().model_dump(mode="json")}
        return commits,specs

    def _acquire(self, program, candidates=(), *, reserve=0.):
        self._check_contract()
        if not self._can_execute(program,reserve=reserve):raise ValueError("budget preserves final validation reserve")
        cfg=self.config
        plan=compile_program(program,capabilities={channel:gate.capability for channel,gate in self.runner.gates.items()},
                             limits={"calls":float(4*cfg.max_iterations+3),"cost":cfg.budget-reserve})
        commits,specs=self._predictions(program,candidates)
        receipt=self.runner.run(plan,prediction_commits=commits)
        if receipt.state!="completed":
            self._context("blocked:"+program.content_hash,{"state":receipt.state,"blocked_node":receipt.blocked_node,
                "resume":"recover provider status with the existing idempotency key; do not resubmit ambiguous acquisitions"})
            raise RuntimeError("research acquisition unresolved: "+receipt.state)
        calibrated={key:value for key,value in receipt.nodes.items() if value.get("method")=="linear_additive_reference_offset"}
        if calibrated:self._context("calibration:"+program.content_hash,{"derived_views":calibrated,"likelihood_count":0,"scope":"program branch consumer; raw joint likelihood remains authoritative"})
        observations={}
        for node in program.nodes:
            item=receipt.nodes.get(node.node_id,{})
            if not item.get("new_evidence"):continue
            raw=MeasurementReceipt(**item["provider_receipt"]["measurement_receipt"])
            expected_group=dict(cfg.session_groups)[node.sample_id]
            expected_key=f"{program.program_id}:{program.revision}:{node.node_id}"
            if raw.request_id!=expected_key or raw.channel!=node.channel or tuple(raw.wavelengths_nm)!=tuple(dict(node.arguments)["wavelengths_nm"]):
                raise ValueError("purchased receipt request/channel/coordinates differ from committed program")
            if raw.task_id!=node.sample_id or raw.calibration_group!=expected_group or raw.cost!=cfg.measurement_cost:
                raise ValueError("purchased receipt differs from frozen session/correlation/cost contract")
            expected_cov=np.eye(len(raw.values))*cfg.measurement_std**2
            if not np.allclose(raw.covariance,expected_cov,rtol=1e-10,atol=1e-15) or raw.calibration_std!=cfg.calibration_std:
                raise ValueError("purchased noise covariance differs from frozen predictive protocol")
            obs=self._put(receipt_to_observation(raw,object_id="observation:"+raw.acquisition_id,source_run_id=cfg.run_id,
                                                objective_ref=self.task.ref(),acquisition_identity=next(spec.grid.get("acquisition_identity") for nodeid,spec in specs if nodeid==node.node_id)))
            observations[node.node_id]=obs
        evaluations=[]
        for nodeid,spec in specs:
            if nodeid not in observations:continue
            seq=self.repository.object_commit_sequence(spec.ref())
            snapshot=next(self.repository.snapshot(json.loads(row[0])["snapshot_id"]) for row in self.repository.db.execute("SELECT payload FROM snapshots WHERE sequence=?",(seq,)))
            commit=PredictionCommit(spec.ref(),spec.snapshot_id,snapshot.snapshot_id,seq)
            verdict=evaluate_prediction(self.repository,commit,observations[nodeid].ref())
            evaluation=self._put(PredictionEvaluation(object_id="evaluation:"+canonical_hash([spec.content_hash,observations[nodeid].content_hash]),
                source_run_id=cfg.run_id,prediction_ref=spec.ref(),observation_ref=observations[nodeid].ref(),metrics=asdict(verdict)))
            evaluations.append(evaluation)
            if observations[nodeid].binding["task_id"]==cfg.reference_session and verdict.state!="supported":
                self._context("control-failure:"+program.content_hash,{"route":"calibrate_noise","state":"reference_prediction_failed",
                    "resume":"check instrument/noise model before reusing specimen data"},(evaluation.ref(),))
                raise ValueError("reference control failed; noise calibration required")
        return observations,tuple(spec for _,spec in specs),tuple(evaluations)

    @metered_scope("terminal")
    def _terminal(self, candidate, fit):
        from .model_ir import model_ir_from_dict
        cfg=self.config;model=compile_model(model_ir_from_dict(candidate.model_ir))
        samples=fit.samples or (fit.parameters,)
        design_ids=tuple(f"thickness:{d:.8g}" for d in cfg.design_thicknesses_nm)
        grid=tuple(sorted(set(cfg.wavelengths_nm+(cfg.objective_wavelength_nm,))))
        target_index=grid.index(cfg.objective_wavelength_nm)
        world_ids=tuple(f"scenario:{i}" for i in range(len(samples)))
        worlds=dict(zip(world_ids,samples))
        def world_loss(world, thickness):
            return (float(model.predict({**worlds[world],cfg.design_parameter:thickness[0]},grid)[target_index])-cfg.objective_target)**2
        context={"task":self.task.content_hash,"objective":canonical_hash(self.task.objective),"model":candidate.content_hash,
                 "sample":canonical_hash({"session":cfg.training_session,"samples":samples}),"material":"public-planar-film-contract","solver":"production-VeriTMM",
                 "fidelity":"normal-incidence-single-film","loss":"squared_R_error","split":"design-not-validation"}
        from .terminal_optimization import TerminalConstraints, TerminalObjective, build_constrained_pool, optimize_common_pool
        spec=next(p for p in model.parameter_specs if p.name==cfg.design_parameter)
        constraints=TerminalConstraints("open-loop-box/1",(self.task.content_hash,),
            (cfg.terminal_lower_nm if cfg.terminal_lower_nm is not None else spec.lower,),
            (cfg.terminal_upper_nm if cfg.terminal_upper_nm is not None else spec.upper,), (cfg.terminal_tolerance_nm,))
        objective=TerminalObjective("open-loop-utility/1",self.task.content_hash,cfg.terminal_loss_transform,cfg.terminal_risk_aversion)
        designs={**self._terminal_designs,**dict(zip(design_ids,((d,) for d in cfg.design_thicknesses_nm)))}
        from .decision_cache import LossMatrixCache
        matrix_cache=LossMatrixCache(self.repository.root/"loss-matrices")
        input_key=canonical_hash({"context":context,"designs":designs,"constraints":constraints.identity,"objective":objective.identity,
            "optimizer_task":asdict(self.terminal_optimizer_task) if self.terminal_optimizer_task is not None else None,
            "optimizer_budget":cfg.terminal_optimizer_budget,"optimization_weights":fit.weights if cfg.terminal_optimizer_budget else None})
        saved=self._find("terminal-cache:"+input_key)
        semantics=fit.weights_semantics if fit.weights is not None else "minimax over fitted scenarios; no invented posterior weights"
        if saved is not None:
            problem=matrix_cache.load(saved.summary["matrix_key"])
            self._terminal_designs.update({key:tuple(value) for key,value in saved.summary["designs"].items()})
            solution=problem.solve(fit.weights) if fit.weights is not None else problem.solve_robust(np.eye(len(samples)))
            self._context("terminal-cache-use:"+canonical_hash({"key":input_key,"weights":fit.weights}),{
                "cache_state":"hit","matrix_key":problem.cache_key,"solution":asdict(solution),"weights":fit.weights,
                "new_forward_cost":0,"feasibility":"all cached designs passed frozen constraints"},(candidate.ref(),saved.ref()))
            return problem,solution,semantics
        if cfg.terminal_optimizer_budget:
            result=optimize_common_pool(task=self.terminal_optimizer_task,world_ids=world_ids,probabilities=fit.weights,
                world_loss=world_loss,context=context,units="squared_R_error",constraints=constraints,objective=objective,
                incumbent_designs=designs,budget=cfg.terminal_optimizer_budget)
        else:
            result=build_constrained_pool(world_ids,designs,world_loss,context,"squared_R_error",constraints,objective,fit.weights)
        if result.problem is None:raise ValueError("no feasible terminal design under frozen manufacturing constraints")
        self._terminal_designs.update(result.designs)
        matrix_cache.store(result.problem)
        self._put(DecisionContext(object_id="terminal-cache:"+input_key,source_run_id=cfg.run_id,input_refs=(candidate.ref(),),
            summary={"input_key":input_key,"matrix_key":result.problem.cache_key,"designs":dict(result.designs),"cache_state":"miss"}))
        self._context("terminal-pool:"+canonical_hash(result.receipt),result.receipt,(candidate.ref(),))
        semantics=fit.weights_semantics if fit.weights is not None else "minimax over fitted scenarios; no invented posterior weights"
        return result.problem,result.solution,semantics

    @metered_scope("value")
    def _value(self, candidate, fit, available):
        from .model_ir import model_ir_from_dict
        before_compute=self.compute_ledger.snapshot()["usage"]
        cfg=self.config;problem,solution,semantics=self._terminal(candidate,fit)
        if fit.weights is None and cfg.value_estimator!="robust_gaussian_bins":
            return {"status":"unknown","reason":"unweighted fitted scenarios do not define an observation probability model",
                    "terminal":asdict(solution),"weights_semantics":semantics}
        model=compile_model(model_ir_from_dict(candidate.model_ir))
        means=np.array([model.predict(p,cfg.wavelengths_nm)[0] for p in fit.samples]);threshold=float(np.median(means))
        if cfg.value_estimator=="robust_gaussian_bins":
            if self.experiment_policy is not None and (self.experiment_policy.depth!=1 or self.experiment_policy.continuation_mode!="fixed_bundle"):
                raise ValueError("robust binned value supports one precommitted acquisition")
            from .robust_gaussian_value import evaluate_robust_gaussian
            vertices=cfg.robust_prior_vertices or (tuple(tuple(row) for row in np.eye(len(means))) if fit.weights is None else (fit.weights,))
            sigma=float(np.hypot(cfg.measurement_std,cfg.calibration_std))
            rates=dict(cfg.compute_utility_rates);after=self.compute_ledger.snapshot()["usage"]
            internal=sum((after.get(r,0)-before_compute.get(r,0))*v for r,v in rates.items())+rates.get("milp_solves",0.)
            report=evaluate_robust_gaussian(problem,vertices,tuple(means),tuple(sigma*m for m in cfg.robust_noise_std_multipliers),
                cfg.robust_thresholds or (threshold,),cost=cfg.action_loss_cost,cost_units=problem.units,
                source_ref=canonical_hash({"candidate":candidate.content_hash,"noise_vertices":cfg.robust_noise_std_multipliers,"compute_price_source":cfg.compute_utility_source}),
                internal_loss_cost=internal)
            return {"status":"finite_declared_joint_ambiguity","robust_report":asdict(report),
                "decision":{"selected_actions":[report.selected_action_id] if report.confirmed_positive else []},
                "terminal":asdict(solution),"weights_semantics":semantics,"scope":"finite fitted worlds and declared prior/noise vertices; no continuum guarantee"}
        if cfg.value_estimator=="gaussian_mc":
            if self.experiment_policy is not None and (self.experiment_policy.depth!=1 or self.experiment_policy.continuation_mode!="fixed_bundle"):
                raise ValueError("continuous Gaussian value currently supports one fixed acquisition, not adaptive policy composition")
            from .gaussian_terminal_value import GaussianAction, GaussianValueProtocol, evaluate_gaussian_actions
            action=GaussianAction("new_independent_session",tuple(means),float(np.hypot(cfg.measurement_std,cfg.calibration_std)),
                cfg.action_loss_cost,cfg.measurement_cost,candidate.content_hash)
            options={"proposal_split_id":"proposal:"+candidate.content_hash,"confirmation_split_id":"confirmation:"+candidate.content_hash,
                "support_semantics":"posterior_sample_approximation","min_world_ess":2.}
            options.update(dict(cfg.gaussian_value_protocol))
            rates=dict(cfg.compute_utility_rates);after_compute=self.compute_ledger.snapshot()["usage"]
            options["precomputation_loss_cost"]=sum((after_compute.get(r,0)-before_compute.get(r,0))*rate for r,rate in rates.items() if r!="valuation_samples")
            options["evaluation_loss_cost_per_sample"]=rates.get("valuation_samples",options.get("evaluation_loss_cost_per_sample",0.))
            options["cost_ref"]=cfg.compute_utility_source
            report=evaluate_gaussian_actions(problem,fit.weights,(action,),GaussianValueProtocol(**options))
            return {"status":"conditional_on_fitted_model","gaussian_report":asdict(report),
                "decision":{"selected_actions":[report.selected_action_id] if report.confirmed_positive else []},
                "terminal":asdict(solution),"weights_semantics":semantics,"scope":report.approximation_scope}
        low=norm.cdf((threshold-means)/np.hypot(cfg.measurement_std,cfg.calibration_std))
        action=MeasurementAction("new_independent_session",("low","high"),tuple((float(p),float(1-p)) for p in low),
                                 cfg.action_loss_cost,cfg.measurement_cost)
        if self.experiment_policy is not None and self.experiment_policy.continuation_mode=="adaptive":
            from .rollout import plan_adaptive
            depth=self.experiment_policy.depth
            if len(cfg.selection_sessions)<depth:raise ValueError("adaptive policy requires distinct declared acquisition sessions")
            actions=tuple(replace(action,action_id=f"new_independent_session_{i+1}",conditional_independence_group="independent_instrument_sessions") for i in range(depth))
            requested=set(self.experiment_policy.action_ids)
            if requested-set(a.action_id for a in actions):raise ValueError("adaptive policy action IDs do not match public acquisition menu")
            if requested:actions=tuple(a for a in actions if a.action_id in requested)
            plan=plan_adaptive(problem,fit.weights,actions,available_budget=available,final_validation_reserve=cfg.measurement_cost,depth=depth)
            if self.method_provider is not None and self._prepared_methods["policy"]:
                method=self._prepared_methods["policy"][0]
                reused,receipt=self.method_provider.consume(method.asset_ref,problem=problem,probabilities=fit.weights,actions=actions,
                    available_budget=available,final_validation_reserve=cfg.measurement_cost)
                if reused.policy_hash!=plan.policy_hash:raise ValueError("retrieved policy execution differs from planned adaptive tree")
                self._context("policy-consumption:"+plan.policy_hash,{"receipt":self.method_provider.receipt_payload(receipt),"policy_hash":plan.policy_hash})
            return {"status":"conditional_on_fitted_model","adaptive_plan":asdict(plan),"actions":[asdict(a) for a in actions],
                    "threshold":threshold,"terminal":asdict(solution),"weights_semantics":semantics,
                    "scope":"finite adaptive conditional policy; independent instrument sessions"}
        if self.experiment_policy is not None and (self.experiment_policy.depth!=1 or set(self.experiment_policy.action_ids)-{action.action_id}):
            raise ValueError("fixed open-loop policy currently requires one declared single-session action")
        decision=decide_next(problem,fit.weights,(action,),available_budget=available,
                             final_validation_reserve=cfg.measurement_cost,depth=1)
        if self.method_provider is not None and self._prepared_methods["policy"]:
            method=self._prepared_methods["policy"][0]
            reused,receipt=self.method_provider.consume(method.asset_ref,problem=problem,probabilities=fit.weights,actions=(action,),
                available_budget=available,final_validation_reserve=cfg.measurement_cost)
            if reused.context_hash!=decision.context_hash:raise ValueError("retrieved fixed policy differs from executable decision")
            self._context("policy-consumption:"+decision.context_hash,{"receipt":self.method_provider.receipt_payload(receipt),"decision_context_hash":decision.context_hash})
        return {"status":"conditional_on_fitted_model","decision":asdict(decision),"threshold":threshold,
                "terminal":asdict(solution),"weights_semantics":semantics,"action":asdict(action),
                "scope":"one scalar first-grid-point Gaussian acquisition statistic; no unmodeled-structure probability"}

    def _export_sequence_receipts(self, work_dir):
        snapshots=[json.loads(row[0]) for row in self.repository.db.execute("SELECT payload FROM snapshots ORDER BY sequence")]
        first={}
        for snapshot in snapshots:
            for ref in snapshot["object_refs"]:first.setdefault(ref["content_hash"],snapshot["committed_sequence"])
        objects=[self.repository.load(ref) for ref in self.repository.latest_snapshot().object_refs]
        observations=[obj for obj in objects if obj.schema_version=="observation.v2"]
        predictions=[obj for obj in objects if isinstance(obj,PredictionSpec)]
        rows=[]
        for observation in observations:
            identity=observation.binding.get("acquisition_identity")
            bound=[spec for spec in predictions if identity is not None and spec.grid.get("acquisition_identity")==identity]
            sequences={spec.object_id:first[spec.content_hash] for spec in bound}
            rows.append({"acquisition_id":observation.acquisition_id,"observation_ref":observation.ref().model_dump(mode="json"),
                "acquisition_sequence":first[observation.content_hash],"prediction_sequences":sequences,
                "identity_state":observation.identity_state,"acquisition_identity":identity,
                "all_predictions_precede_acquisition":all(seq<first[observation.content_hash] for seq in sequences.values()) if sequences else None})
        atomic_write_json(work_dir/"SCIENCE_SNAPSHOTS.json",snapshots)
        atomic_write_json(work_dir/"ACQUISITION_INDEX.json",rows)

    def run(self, work_dir):
        from .compute_ledger import active_compute_ledger
        with active_compute_ledger(self.compute_ledger):
            return self._run(work_dir)

    def _run(self, work_dir):
        self._check_contract()
        cfg=self.config;work_dir=Path(work_dir);work_dir.mkdir(parents=True,exist_ok=True)
        old=self._find(f"{cfg.run_id}:selected")
        if old:
            self.selected_result=old
            atomic_write_json(work_dir/"SELECTED_RESEARCH_RESULT.json",old.model_dump(mode="json"))
            checkpoint=self._find(f"{cfg.run_id}:trajectory-export")
            if checkpoint is None:raise ValueError("selected result lacks its recoverable trajectory export checkpoint")
            atomic_write_json(work_dir/"SCIENCE_OPEN_TRAJECTORY.json",checkpoint.summary)
            from .method_lifecycle import finalize_completed_research
            lifecycle=finalize_completed_research(self.repository,task_ref=self.task.ref(),result_ref=old.ref())
            atomic_write_json(work_dir/"METHOD_LIFECYCLE.json",lifecycle.model_dump(mode="json"))
            self._export_sequence_receipts(work_dir)
            return old
        trajectory=[];unknown_spent=0;stop_reason="iteration_limit";committed_design=None
        train_program=self._program("initial",cfg.training_session)
        observed,_,_=self._acquire(train_program,reserve=cfg.measurement_cost)
        training=[observed["measure"]]
        current,fit,inference=self._fit(self.initial_model,training)
        initial_design=self._terminal(current,fit)[1].decision_id
        all_predictions=[];negative_refs=[];competition_pool=[(current,fit,inference)]
        for iteration in range(cfg.max_iterations):
            assessment=self._assess(current,fit,training)
            usage=sum(g.usage().get("cost",0.) for g in set(self.runner.gates.values()))
            saved=self._find(f"{cfg.run_id}:iteration:{iteration}:decision")
            if saved:
                decision=saved.summary
            else:
                valuation=self._value(current,fit,max(cfg.measurement_cost,cfg.budget-usage))
                route=assessment["route"]
                structural=route in {"propose_structure","bounded_unknown_exploration"}
                positive=bool(valuation.get("decision",{}).get("selected_actions") or valuation.get("adaptive_plan",{}).get("root",{}).get("action_id"))
                action="stop"
                if structural and unknown_spent<cfg.unknown_exploration_limit:action="bounded_structure_search"
                elif route in {"value_experiment_or_stop","identify_task_relevant_direction"} and positive:action="task_valued_measurement"
                decision={"iteration":iteration,"adequacy":assessment,"valuation":valuation,"action":action,
                          "unknown_exploration_used":unknown_spent,"final_validation_reserve":cfg.measurement_cost,
                          "commitment_mode":"precommitted_fixed_grammar_and_selection_score",
                          "model_expansion_enabled":cfg.model_expansion_enabled,"program_composition_enabled":cfg.program_composition_enabled,
                          "experiment_policy":self.experiment_policy.model_dump(mode="json") if self.experiment_policy else None}
                self._context(f"iteration:{iteration}:decision",decision,(current.ref(),inference.ref()))
            trajectory.append(decision)
            if decision["action"]=="stop":
                stop_reason=("unknown_exploration_not_authorized" if cfg.unknown_exploration_limit==0 else "unknown_exploration_budget_exhausted") if assessment["route"] in {"propose_structure","bounded_unknown_exploration"} else assessment["route"]+":no_supported_positive_value"
                break
            structural=decision["action"]=="bounded_structure_search"
            if structural:
                unknown_spent+=1
                committed_design=None
            if not structural and decision["valuation"].get("adaptive_plan"):
                from .rollout import adaptive_plan_from_dict, execute_adaptive
                valuation=decision["valuation"];plan=adaptive_plan_from_dict(valuation["adaptive_plan"])
                actions=tuple(MeasurementAction(**a) for a in valuation["actions"])
                problem,_,_=self._terminal(current,fit);collected=[]
                def acquire_action(action):
                    index=int(action.action_id.rsplit("_",1)[1])-1
                    session=cfg.selection_sessions[index]
                    p=self._program(f"adaptive:{iteration}:{action.action_id}",session)
                    obs,specs,evals=self._acquire(p,(current,),reserve=cfg.measurement_cost)
                    specimen=obs["measure"];collected.append(specimen)
                    all_predictions.extend(s.ref() for s in specs)
                    return "low" if float(specimen.raw_blocks[0]["values"][0])<=valuation["threshold"] else "high"
                continuation=execute_adaptive(problem,fit.weights,actions,plan,acquire_action)
                committed_design=continuation.selected_design_id
                self._context(f"iteration:{iteration}:adaptive-continuation",asdict(continuation),tuple(o.ref() for o in collected))
                training.extend(collected)
                from .model_ir import model_ir_from_dict
                current,fit,inference=self._fit(model_ir_from_dict(current.model_ir),training)
                stop_reason="adaptive_precommitted_continuation_completed"
                break
            session=cfg.selection_sessions[iteration]
            program=self._program(f"iteration:{iteration}",session,adaptive=structural and cfg.program_composition_enabled)
            if structural and self.method_provider is not None and cfg.program_composition_enabled and self._prepared_methods["program"]:
                method=self._prepared_methods["program"][0]
                slots=dict(method.compiled.initial_samples)
                if set(slots) not in ({"sample"},{"sample","reference"}):raise ValueError("reusable program must declare sample and optional reference slots")
                program,instantiation=self.method_provider.instantiate_program(method.asset_ref,program_id=program.program_id,
                    sample_bindings={key:value for key,value in {"sample":session,"reference":cfg.reference_session}.items() if key in slots})
                for node in program.nodes:
                    if node.kind=="state_transition":raise ValueError("reused program cannot add unsupported preparation")
                    if node.kind=="new_evidence" and (node.channel!="R" or dict(node.estimate)!={"calls":1.,"cost":cfg.measurement_cost}):
                        raise ValueError("reused program changed frozen acquisition channel/cost")
                self._context(f"iteration:{iteration}:program-instantiation",{"receipt":self.method_provider.receipt_payload(instantiation),"program":asdict(program)})
            if self.proposal_provider is None and not self._can_execute(program,reserve=cfg.measurement_cost):
                stop_reason="budget_reserved_for_independent_validation";break
            candidates=list(competition_pool)
            if structural and cfg.model_expansion_enabled:
                from .model_ir import model_ir_from_dict
                parent=model_ir_from_dict(current.model_ir)
                parent=replace(parent,parameters=tuple(replace(p,initial=fit.parameters[p.name]) for p in parent.parameters))
                research_brief=self._literature(iteration,current,assessment)
                if research_brief and cfg.adopt_source_qualified_implications:
                    from .typed_implication_runtime import consume_implications
                    consumed=consume_implications(research_brief,model_ir=parent,program=program,
                        task_conditions={"wavelength_domain_nm":list(self.initial_model.domain_nm),"angle_deg":0.,
                                         "candidate_hash":current.content_hash,"objective":self.task.objective},
                        adopted_claim_ids=tuple(research_brief["claim_refs"]))
                    self._context(f"iteration:{iteration}:literature-adoption",{
                        "explicit_policy":"adopt_source_qualified_implications", "receipts":consumed.receipts,
                        "model_before":asdict(parent),"model_after":asdict(consumed.model_ir),
                        "program_before":asdict(program),"program_after":asdict(consumed.program)},(current.ref(),))
                    parent=consumed.model_ir;program=consumed.program
                proposals=propose_extensions(parent,mismatch=assessment["state"],operators=cfg.grammar,
                                             max_depth=1,max_candidates=cfg.max_candidates,source_refs=(current.content_hash,))
                if self.proposal_provider is not None:
                    from .joint_proposer import parse_joint_proposal
                    context={"iteration":iteration,"parent_model_ir":asdict(parent),"adequacy":assessment,
                        "task_objective":self.task.objective,"observations":[o.model_dump(mode="json") for o in training],
                        "source_refs":[o.content_hash for o in training]+(research_brief["claim_refs"] if research_brief else []),
                        "research_brief":research_brief,"literature_is_likelihood":False,"max_candidates":cfg.max_candidates,
                        "operator_reference_protocols":self.operator_admission.public_protocols() if self.operator_admission else (),
                        "program_id":program.program_id,"available_channels":["R"],
                        "allowed_samples":[session,cfg.reference_session],"remaining_budget":cfg.budget-usage-cfg.measurement_cost,
                        "default_program":asdict(program)}
                    oid=f"{cfg.run_id}:iteration:{iteration}:provider"
                    cached=self._find(oid)
                    if cached:
                        if cached.summary["context_hash"]!=canonical_hash(context):
                            # Live remaining budget changes on recovery, but the
                            # exact originally transmitted context remains bound.
                            stable={k:v for k,v in context.items() if k!="remaining_budget"}
                            if canonical_hash(stable)!=cached.summary["stable_context_hash"]:raise ValueError("proposal recovery context changed")
                        batch=parse_joint_proposal(cached.summary["payload"],context,messages=cached.actual_messages,usage=cached.summary["usage"])
                    else:
                        from .joint_proposer import ReplayJointProposer
                        if self._find(oid+":preview") is not None and not isinstance(self.proposal_provider,ReplayJointProposer):
                            raise RuntimeError("proposal call outcome unknown after interruption; do not automatically repeat a paid call")
                        messages=tuple(self.proposal_provider.preview(context))
                        self._put(DecisionContext(object_id=oid+":preview",source_run_id=cfg.run_id,
                            input_refs=(current.ref(),),actual_messages=messages,summary={"context_hash":canonical_hash(context),"paid_call_status":"not_started_at_preview"}))
                        from .joint_proposer import ProposalResponseError
                        self._context(f"iteration:{iteration}:provider:dispatch",{"state":"submitting","protocol_id":self.proposal_provider.protocol_id,
                            "recovery":"no automatic paid retry if response receipt is absent"},(current.ref(),))
                        try:
                            batch=self.proposal_provider.propose(context)
                        except ProposalResponseError as exc:
                            self._put(DecisionContext(object_id=oid+":failed",source_run_id=cfg.run_id,input_refs=(current.ref(),),actual_messages=messages,
                                summary={"state":"response_received_invalid","usage":exc.usage,"response_hash":exc.response_hash,
                                         "error":str(exc),"proposal_admitted":False}))
                            raise
                        self._check_contract()
                        self._put(DecisionContext(object_id=oid,source_run_id=cfg.run_id,input_refs=(current.ref(),),actual_messages=batch.actual_messages,
                            summary={"payload":batch.payload,"usage":batch.usage,"context_hash":canonical_hash(context),
                                     "stable_context_hash":canonical_hash({k:v for k,v in context.items() if k!="remaining_budget"})}))
                    admitted=[];admission_receipts=[]
                    for request in batch.operator_requests:
                        if self.operator_admission is None:
                            admission_receipts.append({"status":"unavailable","reason":"no host-trusted public reference protocol","request_hash":canonical_hash(request)})
                            continue
                        try:
                            extension,receipt=self.operator_admission.admit(request,parent)
                            admitted.append(extension);admission_receipts.append(receipt)
                        except (ValueError,FloatingPointError) as exc:
                            admission_receipts.append({"status":"rejected","reason":str(exc),"request_hash":canonical_hash(request)})
                    if admission_receipts:self._context(f"iteration:{iteration}:operator-admission",{"receipts":admission_receipts,"scientific_support":"not established by admission"},(current.ref(),))
                    proposals=tuple(admitted)+tuple(batch.models)+tuple(proposals)
                    from .model_equivalence import algebraic_model_key
                    seen=set();deduplicated=[]
                    for proposal in proposals:
                        normalized_key=algebraic_model_key(proposal.model_ir)
                        if normalized_key not in seen:
                            seen.add(normalized_key);deduplicated.append(proposal)
                    proposals=tuple(deduplicated[:cfg.max_candidates])
                    if batch.program is not None and cfg.program_composition_enabled and not self._prepared_methods["program"]:
                        program=batch.program
                        if set(dict(program.initial_samples))-{session,cfg.reference_session}:
                            raise ValueError("proposed program attempted to access a held-out or unapproved sample")
                        for node in program.nodes:
                            if node.kind=="state_transition":raise ValueError("offline purchase protocol does not register preparation effects")
                            if node.kind=="new_evidence":
                                if node.sample_id not in {session,cfg.reference_session} or node.channel!="R":raise ValueError("unapproved proposed acquisition")
                                if dict(node.estimate)!={"calls":1.,"cost":cfg.measurement_cost}:raise ValueError("proposal cannot alter trusted acquisition costs")
                                if set(dict(node.arguments))!={"wavelengths_nm"}:raise ValueError("proposal may only set declared measurement coordinates")
                                grid=np.asarray(dict(node.arguments)["wavelengths_nm"],float)
                                if grid.ndim!=1 or not 2<=len(grid)<=4096 or not np.isfinite(grid).all() or np.any(np.diff(grid)<=0) or grid.min()<self.initial_model.domain_nm[0] or grid.max()>self.initial_model.domain_nm[1]:
                                    raise ValueError("proposed measurement grid exceeds frozen acquisition domain")
                        compile_program(program,capabilities={"R":self.runner.gates["R"].capability},
                            limits={"calls":float(4*cfg.max_iterations+3),"cost":cfg.budget-cfg.measurement_cost})
                if self.method_provider is not None:
                    from .model_search import ModelExtensionProposal
                    additions=tuple(ModelExtensionProposal(parent.structural_hash,(m.asset_ref.content_hash,),"frozen_method_library",
                        (m.asset_ref.content_hash,),"reused typed source model; must refit on current evidence","requires current task validation",m.compiled.ir)
                        for m in self._prepared_methods["model_fragment"])
                    proposals=tuple((additions+tuple(proposals))[:cfg.max_candidates])
                for proposal in proposals:
                    if cfg.design_parameter not in {p.name for p in proposal.model_ir.parameters}:
                        raise ValueError("proposal removed the frozen terminal design parameter")
                    spec=next(p for p in proposal.model_ir.parameters if p.name==cfg.design_parameter)
                    if spec.unit!="nm" or spec.lower>min(cfg.design_thicknesses_nm) or spec.upper<max(cfg.design_thicknesses_nm):
                        raise ValueError("proposal changed the frozen terminal design domain")
                    if proposal.model_ir.domain_nm[0]>min(cfg.wavelengths_nm+cfg.validation_wavelengths_nm+(cfg.objective_wavelength_nm,)) or proposal.model_ir.domain_nm[1]<max(cfg.wavelengths_nm+cfg.validation_wavelengths_nm+(cfg.objective_wavelength_nm,)):
                        raise ValueError("proposal cannot cover frozen acquisition and terminal domains")
                    from .model_ir import CompileError
                    try:
                        child,child_fit,child_inference=self._fit(proposal.model_ir,training)
                    except (CompileError,RuntimeError) as exc:
                        if not isinstance(exc,CompileError) and not str(exc).startswith("all inference starts failed:"):
                            raise  # Includes compute-budget exhaustion and unrelated runtime defects.
                        self._context(f"iteration:{iteration}:proposal-rejected:"+proposal.model_ir.structural_hash,
                            {"model_hash":proposal.model_ir.structural_hash,"status":"unfittable_on_declared_domain",
                             "reason":str(exc),"failed_forward_cost":"retained in common ledger"},(current.ref(),))
                        continue
                    if not child_fit.success:continue
                    self._graph.inherit_candidate(current.object_id,child.object_id)
                    candidates.append((child,child_fit,child_inference))
                    if proposal.origin=="frozen_method_library":
                        method=next(m for m in self._prepared_methods["model_fragment"] if m.asset_ref.content_hash in proposal.source_refs)
                        _,receipt=self.method_provider.consume(method.asset_ref,parameters=child_fit.parameters,wavelengths_nm=cfg.wavelengths_nm)
                        self._context(f"iteration:{iteration}:method:{method.asset_ref.content_hash}",
                            {"receipt":self.method_provider.receipt_payload(receipt),"candidate_ref":child.ref().model_dump(mode="json"),
                             "role":"executed and fitted candidate; not automatic selection"},(child.ref(),))
                # Numerical equivalence is local to fitted values and this grid. Retain
                # distinct families; do not turn duplicated predictions into extra mass.
                from .model_equivalence import deduplicate_models
                compiled=[compile_model(model_ir_from_dict(c.model_ir)) for c,_,_ in candidates]
                _,equivalences=deduplicate_models(compiled,wavelengths_nm=cfg.wavelengths_nm,
                    parameters={m.model_id:f.parameters for m,(_,f,_) in zip(compiled,candidates)})
                self._context(f"iteration:{iteration}:prediction-equivalence",{
                    "classes":[asdict(group) for group in equivalences],
                    "use":"diagnostic only; no cross-model posterior weights or family deletion",
                    "structural_dedup":"canonical DAG and conservative commutative expression normalization"},tuple(c.ref() for c,_,_ in candidates))
                self._context(f"iteration:{iteration}:proposals",{"proposals":[{"parent":p.parent,"edit":p.edit,"origin":p.origin,
                    "expected_difference":p.expected_difference,"model_hash":p.model_ir.structural_hash} for p in proposals],
                    "selection_rule":"lowest fresh predictive Gaussian squared discrepancy plus parameter_count * log(n)",
                    "claim":"classical typed grammar search; not new-operator discovery"},tuple(c.ref() for c,_,_ in candidates))
            if not self._can_execute(program,reserve=cfg.measurement_cost):
                stop_reason="budget_reserved_for_independent_validation";break
            new,specs,evals=self._acquire(program,tuple(c for c,_,_ in candidates),reserve=cfg.measurement_cost)
            all_predictions.extend(s.ref() for s in specs)
            if structural and self.method_provider is not None and cfg.program_composition_enabled and self._prepared_methods["program"]:
                method=self._prepared_methods["program"][0]
                self._context(f"iteration:{iteration}:program-method-executed",{
                    "asset_ref":self.method_provider.external_asset_ref(method.asset_ref),"instantiated_program_hash":program.content_hash,
                    "status":"executed","observation_refs":[o.ref().model_dump(mode="json") for o in new.values()],
                    "prediction_refs":[p.ref().model_dump(mode="json") for p in specs]},tuple(o.ref() for o in new.values()))
            # Reference remains a separately purchased control; it is never fitted
            # as if it were another observation of the unknown specimen.
            selection=[obs for key,obs in new.items() if obs.binding["task_id"]==session]
            if not selection:raise ValueError("proposed program produced no specimen selection evidence")
            if not structural and decision["valuation"].get("robust_report"):
                from .robust_gaussian_value import robust_gaussian_report_from_dict
                report=robust_gaussian_report_from_dict(decision["valuation"]["robust_report"])
                if not report.confirmed_positive:raise ValueError("robust acquisition lacks positive bounded value")
                problem,_,_=self._terminal(current,fit)
                scalar=float(selection[0].raw_blocks[0]["values"][0])
                committed_design=report.policy.choose(scalar,problem_hash=problem.cache_key,policy_hash=report.policy_hash)
                self._context(f"iteration:{iteration}:continuation",{"outcome":"committed_robust_gaussian_continuation_completed",
                    "selected_design_id":committed_design,"observation":scalar,"policy_hash":report.policy_hash,
                    "value_report":asdict(report)},tuple(o.ref() for o in selection))
            elif not structural and decision["valuation"].get("gaussian_report"):
                from .gaussian_terminal_value import gaussian_value_report_from_dict
                report=gaussian_value_report_from_dict(decision["valuation"]["gaussian_report"])
                if not report.confirmed_positive:raise ValueError("continuous acquisition lacks independent positive-value confirmation")
                problem,_,_=self._terminal(current,fit)
                scalar=float(selection[0].raw_blocks[0]["values"][0])
                committed_design=report.policy.choose(scalar,problem_hash=problem.cache_key,policy_hash=report.policy_hash)
                self._context(f"iteration:{iteration}:continuation",{"outcome":"committed_continuous_gaussian_continuation_completed",
                    "selected_design_id":committed_design,"observation":scalar,"policy_hash":report.policy.policy_hash,
                    "value_report":asdict(report)},tuple(o.ref() for o in selection))
            elif not structural:
                valuation=decision["valuation"]
                problem,_,_=self._terminal(current,fit)
                action=MeasurementAction(**valuation["action"])
                frozen_decision=decide_next(problem,fit.weights,(action,),
                    available_budget=cfg.budget-usage,final_validation_reserve=cfg.measurement_cost,depth=1)
                # On replay usage includes completed effects, so reconstruct from
                # the committed context's original budget instead of live usage.
                original=valuation["decision"]
                if frozen_decision.context_hash != original["context_hash"]:
                    from .controller import DecisionCommit
                    from .evsi import ActionValueReport, ContinuationPolicy
                    estimates=[]
                    for ids,report in original["estimates"]:
                        report=dict(report);report["policy"]=ContinuationPolicy(**report["policy"])
                        estimates.append((tuple(ids),ActionValueReport(**report)))
                    frozen_decision=DecisionCommit(**{**original,"estimates":tuple(estimates),"selected_actions":tuple(original["selected_actions"]),"unknown_action_ids":tuple(original["unknown_action_ids"])})
                scalar=float(selection[0].raw_blocks[0]["values"][0])
                outcome="low" if scalar<=valuation["threshold"] else "high"
                continuation=execute_round(problem,fit.weights,(action,),frozen_decision,lambda _:outcome,
                                           current_context_hash=original["context_hash"])
                committed_design=continuation.selected_design_id
                self._context(f"iteration:{iteration}:continuation",asdict(continuation),tuple(o.ref() for o in selection))
            likelihood=JointLikelihood(blocks_from_observations(selection))
            scores=[]
            from .model_ir import model_ir_from_dict
            unique={model_ir_from_dict(c.model_ir).structural_hash:(c,f,state) for c,f,state in candidates}
            candidates=list(unique.values())
            for candidate,candidate_fit,state in candidates:
                for obs in selection:self._use(obs,candidate,"select")
                residual=likelihood.residual(compile_model(model_ir_from_dict(candidate.model_ir)),candidate_fit.parameters)
                scores.append(float(residual@residual+len(candidate_fit.parameters)*np.log(len(residual))))
            training.extend(selection)
            refitted=[];refit_records=[];failed_candidates=[]
            for candidate,candidate_fit,state in candidates:
                try:
                    child,child_fit,child_state=self._fit(model_ir_from_dict(candidate.model_ir),training)
                except RuntimeError as exc:
                    if not str(exc).startswith("all inference starts failed:"):raise
                    refit_records.append({"source_candidate_id":candidate.object_id,"state":"numerical_failure","error":str(exc)})
                    failed_candidates.append((candidate,candidate_fit,state))
                    continue
                n=sum(len(o.raw_blocks[0]["values"]) for o in training)
                score=2*child_fit.loss+len(child_fit.parameters)*np.log(n)
                refit_records.append({"source_candidate_id":candidate.object_id,"candidate_ref":child.ref().model_dump(mode="json"),
                    "state":"converged" if child_fit.success else "numerical_failure","score":score,"fit":asdict(child_fit)})
                refitted.append((child,child_fit,child_state))
            converged=[item for item in refitted if item[1].success]
            if not converged:raise RuntimeError("all competing mechanism refits failed; no mechanism is refuted")
            current,fit,inference=min(converged,key=lambda item:2*item[1].loss+len(item[1].parameters)*np.log(n))
            competition_pool=refitted+failed_candidates
            selection_context=self._context(f"iteration:{iteration}:selection",{"scores":scores,"candidate_ids":[c.object_id for c,_,_ in candidates],
                "prequential_selected_id":candidates[int(np.argmin(scores))][0].object_id,"selected_id":current.object_id,
                "all_competition_refits":refit_records,"refit_score":"joint training+selection discrepancy plus parameter_count*log(n)",
                "frozen_predictions_preserved":True,"selection_evidence_is_not_independent_validation":True,
                "finite_search_not_global_mechanism_refutation":True},
                tuple(c.ref() for c,_,_ in candidates)+tuple(c.ref() for c,_,_ in refitted)+tuple(o.ref() for o in selection))
            negative_refs.append(selection_context.ref())
        final_assessment=self._assess(current,fit,training)
        adequacy_record=self._find("adequacy:"+current.content_hash)
        unresolved_failure=final_assessment["state"]=="model_family_inadequate"
        problem,solution,semantics=self._terminal(current,fit)
        if committed_design is not None:
            index=problem.design_ids.index(committed_design)
            risk=float(np.asarray(fit.weights)@problem.losses[:,index]) if fit.weights is not None else float(problem.losses[:,index].max())
            solution=TerminalSolution(committed_design,risk,"precommitted_continuation; updated risk reported without changing action")
        design_index=problem.design_ids.index(solution.decision_id)
        chosen_thickness=self._terminal_designs[solution.decision_id][0]
        design_parameters={**fit.parameters,cfg.design_parameter:chosen_thickness}
        adopted=self._put(ModelCandidate(object_id=f"{cfg.run_id}:adopted-design",source_run_id=cfg.run_id,
            input_refs=(current.ref(),inference.ref()),model_ir=current.model_ir,parameter_values=design_parameters,
            domain={"task":self.task.content_hash,"selection":"C10 finite common design pool",
                    "interpretation":"proposed new manufactured thickness; heldout validates specimen model, not manufactured device"}))
        gradient_summary={"status":"unavailable","reason":"ModelIR terminal squared-reflectance objective has no loss-preserving VeriTMM gradient projection",
                          "candidate_ref":adopted.ref().model_dump(mode="json"),"objective_ref":self.task.ref().model_dump(mode="json"),
                          "variable_definition":cfg.design_parameter}
        commitment=self._context("terminal-commit",{"gradient_summary":gradient_summary,
            "qualified_values":{"selected_thickness_nm":{"value":chosen_thickness,"units":"nm","data_kind":"simulation",
                "role":"proposed manufactured design parameter; not independently fabricated"},
                "predicted_terminal_risk":{"value":solution.risk,"units":"1","data_kind":"simulation",
                "role":"model-conditional squared reflectance error; not empirical device performance"}},"stop_reason":stop_reason,"solution":asdict(solution),
            "initial_design":initial_design,"final_design":solution.decision_id,"design_changed":initial_design!=solution.decision_id,
            "weights_semantics":semantics,"problem_hash":problem.cache_key,"unknown_exploration_used":unknown_spent,
            "final_validation_session":cfg.validation_session,"claim_strength":"design",
            "manufactured_design_validation":"not performed; model prediction validation only"},(current.ref(),adopted.ref(),inference.ref()))
        final_program=self._program("final-validation",cfg.validation_session,final=True)
        final_observations,specs,evals=self._acquire(final_program,(current,))
        final=final_observations["measure"]
        for training_observation in training:
            if training_observation.raw_blocks[0]["calibration_group"]==final.raw_blocks[0]["calibration_group"]:
                raise ValueError("final validation calibration group leaked into fitting or selection")
        self._use(final,current,"validate")
        independent_supported=all(e.metrics["state"]=="supported" for e in evals)
        adoption_supported=independent_supported and fit.success and not unresolved_failure
        if self.operator_admission is not None:
            for node in current.model_ir["nodes"]:
                if node["operator"]!="expression":continue
                operator_id=dict(node["attributes"])["operator_id"]
                if operator_id not in self.operator_admission.registry._entries:continue
                report=self.operator_admission.registry.support_on_fresh_prediction(operator_id,
                    prediction_frozen_before_observation=all(self.repository.object_commit_sequence(spec.ref())<self.repository.acquisition_commit_sequence(final.acquisition_id) for spec in specs),
                    observation_id=final.acquisition_id,training_observation_ids=[o.acquisition_id for o in training],passed=independent_supported)
                self._context("operator-fresh-validation:"+operator_id,{"report":asdict(report),"prediction_refs":[p.ref().model_dump(mode="json") for p in specs],
                    "support_scope":"same specimen, held-out calibration session and coordinates; not mechanism or transfer"},(current.ref(),final.ref(),*(e.ref() for e in evals)))
        validation=self._context("final-validation",{"state":"supported_within_frozen_noise_envelope" if independent_supported else "not_supported",
            "selection_locked_before_validation":True,"evidence_role":"independent predictive check on same specimen in a new calibration session",
            "never_claims":"global coverage, mechanism discovery, transfer or fabricated device performance",
            "evaluation_refs":[e.ref().model_dump(mode="json") for e in evals],
            "qualified_values":{"observed_reflectance":[{"value":float(v),"units":"1","data_kind":"simulation","wavelength_nm":float(w),
                "origin":"independently purchased synthetic observation, not real measurement"} for w,v in zip(final.raw_blocks[0]["wavelengths_nm"],final.raw_blocks[0]["values"])],
                "predicted_reflectance":[{"value":float(v),"units":"1","data_kind":"held_out_prediction","wavelength_nm":float(w),
                "origin":"frozen before this acquisition"} for w,v in zip(specs[0].coordinate,specs[0].distribution["mean"])]}},(commitment.ref(),final.ref(),*(e.ref() for e in evals)))
        claims=[]
        claims.append(self._put(ScientificClaim(object_id=f"{cfg.run_id}:claim:design",source_run_id=cfg.run_id,
            statement=f"The frozen finite-pool calculation selected a proposed {chosen_thickness:g} nm thickness for {cfg.design_parameter} under the squared-reflectance target at {cfg.objective_wavelength_nm:g} nm.",
            evidence_refs=(commitment.ref(),inference.ref(),adopted.ref()),status="supported",
            scope={"strength":"design","evidence_tier":"V1_synthetic","task_ref":self.task.ref().model_dump(mode="json"),
                "model_conditional":True,"manufactured_design_validation":False,"adoption_withheld":not adoption_supported})))
        claims.append(self._put(ScientificClaim(object_id=f"{cfg.run_id}:claim:validation",source_run_id=cfg.run_id,
            statement="The frozen fitted specimen model's reflectance envelope agreed with all held-out wavelengths in a separate calibration session.",
            evidence_refs=(validation.ref(),final.ref(),*(e.ref() for e in evals)),status="supported" if independent_supported else "refuted",
            scope={"strength":"prediction","evidence_tier":"V1_synthetic","same_specimen_only":True,
                "parameter_uncertainty_coverage":"not established","global_adaptive_coverage":"not established",
                "mechanism_or_transfer_claim":False})))
        for index,ref in enumerate(negative_refs):
            selection_record=self.repository.load(ref)
            rejected=max(0,len(selection_record.summary["candidate_ids"])-1)
            claims.append(self._put(ScientificClaim(object_id=f"{cfg.run_id}:claim:selection:{index}",source_run_id=cfg.run_id,
                statement=f"Iteration {index} retained one candidate and did not select {rejected} alternatives after preserving frozen prediction scores and refitting every competitor on the combined evidence.",
                evidence_refs=(ref,),status="supported",scope={"strength":"design","selection_evidence_only":True,
                "not_independent_validation":True,"not_mechanism_refutation":True})))
        export_payload={"protocol":asdict(cfg),"iterations":trajectory,
            "terminal_commit":commitment.model_dump(mode="json"),"validation":validation.model_dump(mode="json"),
            "cost_usage":{str(i):gate.usage() for i,gate in enumerate(set(self.runner.gates.values()))},
            "literature_budget":{"request_limit":cfg.literature_request_limit,"max_papers_per_request":cfg.literature_max_papers,
                "max_supplementary_calls_per_request":cfg.literature_max_supplementary_calls,
                "consumed_request_slots":sum(ref.object_id.startswith(f"{cfg.run_id}:literature:") and ref.object_id.endswith(":request") for ref in self.repository.latest_snapshot().object_refs),
                "value_status":"unknown","optical_likelihood_contribution":False},
            "evidence_uses":[asdict(r) for r in self._graph.records],
            "compute_budget":{"fit_starts":cfg.fit_starts,"max_nfev_per_start":cfg.max_nfev,
                "max_iterations":cfg.max_iterations,"max_candidates_per_iteration":cfg.max_candidates,
                "null_replicates_per_assessment":cfg.null_replicates,
                "null_nfev":sum(self.repository.load(ref).summary.get("null_nfev",0) for ref in self.repository.latest_snapshot().object_refs if ref.object_id.startswith("adequacy:")),
                "fit_nfev":sum(self.repository.load(ref).summary["fit"]["diagnostics"].get("nfev",0) for ref in self.repository.latest_snapshot().object_refs if ref.object_id.startswith("fit-cache:")),
                "fit_model_predict_calls_known":sum(self.repository.load(ref).summary["fit"]["diagnostics"].get("model_predict_calls",0) for ref in self.repository.latest_snapshot().object_refs if ref.object_id.startswith("fit-cache:")),
                "null_model_predict_calls_known":sum(self.repository.load(ref).summary.get("null_model_predict_calls_known",0) for ref in self.repository.latest_snapshot().object_refs if ref.object_id.startswith("adequacy:")),
                "resource_ledger":self.compute_ledger.snapshot(),
                "non_inference_forward_cost":"all production CompiledModel.predict calls and actual solver wavelength-points charged by durable compute ledger"}}
        self._context("trajectory-export",export_payload,(commitment.ref(),validation.ref()))
        result=self._put(SelectedResearchResult(object_id=f"{cfg.run_id}:selected",source_run_id=cfg.run_id,
            input_refs=(commitment.ref(),validation.ref()),candidate_ref=adopted.ref(),inference_ref=inference.ref(),claim_refs=tuple(c.ref() for c in claims),
            asset_refs=(commitment.ref(),validation.ref(),final.ref(),*(e.ref() for e in evals)),
            prediction_refs=tuple(all_predictions)+tuple(s.ref() for s in specs),negative_result_refs=tuple(negative_refs)+((adequacy_record.ref(),) if unresolved_failure else ())+(() if independent_supported else (validation.ref(),)),
            claim_strength="design",candidate_roles={"fitted_specimen":current.object_id,"terminal_choice":solution.decision_id,
                "adoption_status":"proposed_design" if adoption_supported else ("withheld_after_numerical_failure" if not fit.success else "withheld_after_failed_validation" if not independent_supported else "withheld_after_model_family_failure"),
                "validation_state":validation.summary["state"],"evidence_tier":"V1_synthetic",
                "stop_reason":stop_reason,"design_changed":initial_design!=solution.decision_id,
                "unresolved_model_failure":unresolved_failure,
                "research_outcome":ResearchOutcome(model_adequacy_state=final_assessment["state"],
                    local_prediction_state=validation.summary["state"],identity_state="bound",
                    numerical_state="valid" if fit.success else "failure",noise_state="calibrated",
                    mechanism_state="model_set_inadequate" if unresolved_failure else "not_evaluated",
                    unknown_exploration_state="not_authorized" if cfg.unknown_exploration_limit==0 else "budget_exhausted" if unknown_spent>=cfg.unknown_exploration_limit else "available",
                    unresolved_failure_refs=(adequacy_record.ref(),) if unresolved_failure else ()).model_dump(mode="json"),
                "independent_manufactured_design_validation":False}))
        self.selected_result=result
        atomic_write_json(work_dir/"SELECTED_RESEARCH_RESULT.json",result.model_dump(mode="json"))
        atomic_write_json(work_dir/"SCIENCE_OPEN_TRAJECTORY.json",export_payload)
        from .method_lifecycle import finalize_completed_research
        lifecycle=finalize_completed_research(self.repository,task_ref=self.task.ref(),result_ref=result.ref())
        atomic_write_json(work_dir/"METHOD_LIFECYCLE.json",lifecycle.model_dump(mode="json"))
        self._export_sequence_receipts(work_dir)
        return result
