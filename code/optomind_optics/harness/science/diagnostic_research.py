from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import json
import hashlib
import numpy as np
from scipy.stats import norm

from .contracts import AcquisitionIdentity, DecisionContext, ExperimentProgram, ModelCandidate, Observation, PredictionEvaluation, PredictionSpec, ResearchOutcome, ResearchTask, SelectedResearchResult, canonical_hash
from .diagnostic_design import DiagnosticBudgetExceeded, DiagnosticForward, DiagnosticDatum, design_diagnostic_action, refit_competition, tolerance_vertices
from .prediction import commit_prediction, evaluate_prediction
from .program_ir import ProgramIR, ProgramNode
from .repository import ScienceRepository
from optomind_research.runtime.artifact_store import atomic_write_json


class DiagnosticResearchLoop:
    def __init__(self, *, families, config, search_space, repository, run_id, provider):
        self.families = tuple(families)
        if len({f.family_id for f in families}) != len(families) or not 2 <= len(families) <= 12:
            raise ValueError("diagnostic research requires 2..12 distinct mechanism families")
        self.config = config
        self.search_space = search_space
        self.repository = repository
        self.run_id = run_id
        self.provider = provider
        self.forward = DiagnosticForward(config)
        self.measurement_spent = 0.
        self.acquisition_index = []
        self.prediction_refs = []
        self.observation_refs = []
        self.negative_refs = []
        self.last_acquisition_evaluations = {}
        self.last_competition_ref = None
        self.confirmation_refs = ()
        self.task = ResearchTask(object_id=run_id + ":task", source_run_id=run_id,
            question="Discriminate bounded competing sample mechanisms by automatically designing a removable diagnostic front end",
            objective={"kind": "mechanism_diagnosis"},
            constraints={"scope": {"config": asdict(config), "search_space": asdict(search_space), "families": [asdict(f) for f in families]}, "paid_network_calls": 0, "evidence_tier": "provider_declared"})
        if repository.latest_snapshot() is not None:
            raise ValueError("use an empty repository; acquisition recovery requires provider-specific durable orchestration")
        self._put(self.task)
        runtime_files=("diagnostic_design.py","diagnostic_research.py","diagnostic_cli.py","model_ir.py","model_compiler.py","contracts.py","nested_contracts.py","prediction.py")
        self.runtime_fingerprints={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in runtime_files}
        self._context("runtime-protocol",{"runtime_fingerprints":self.runtime_fingerprints,"provider_evidence_tier":getattr(provider,"evidence_tier","unvalidated_provider"),"model_api_calls":0})

    def _put(self, obj):
        self.repository.commit([obj], task_ref=self.task.ref(), evidence_cutoff="diagnostic:" + obj.object_id)
        return obj

    def _context(self, suffix, summary, refs=()):
        return self._put(DecisionContext(object_id=self.run_id + ":" + suffix, source_run_id=self.run_id, input_refs=tuple(refs), summary=summary))

    def _candidates(self, competition, stage):
        candidates = {}
        for family, fit in zip(self.families, competition.fits):
            if fit.parameters or not family.model.parameters:
                candidates[family.family_id] = self._put(ModelCandidate(object_id=self.run_id + ":" + stage + ":" + family.family_id, source_run_id=self.run_id,
                    model_ir=asdict(family.model), parameter_values=fit.parameters,
                    domain={"interpretation": "alternative model version of the same original sample; front end excluded", "support": fit.state}))
        return candidates

    def _acquire(self, action, *, stage, competition=None, candidates=None, readout=None):
        if self.measurement_spent + action.cost > self.config.measurement_budget + 1e-12:
            raise DiagnosticBudgetExceeded("diagnostic measurement budget exhausted")
        frontend = self._context(stage + ":frontend", {"frontend": asdict(action.frontend), "parameter_domain": "known removable reference operator; original sample unknowns unchanged"})
        node = ProgramNode("measure", "new_evidence", channel=action.channel, sample_id=action.sample_id,
            arguments=(("wavelengths_nm", action.wavelengths_nm), ("angle_deg", action.angle_deg), ("polarization", action.polarization), ("frontend_hash", action.frontend.content_hash)),
            estimate=(("calls", 1.), ("cost", action.cost)), correlation_group=action.session_id)
        program_ir = ProgramIR(self.run_id + ":" + stage, (node,), ((action.sample_id, "ready"),))
        program = self._put(ExperimentProgram(object_id=self.run_id + ":" + stage + ":program", source_run_id=self.run_id,
            objective_ref=self.task.ref(), protocol=asdict(program_ir)))
        identity = AcquisitionIdentity(program_ref=program.ref(), node_id="measure", sample_id=action.sample_id, session_id=action.session_id,
            frontend_ref=frontend.ref(), conditions={"angle_deg": action.angle_deg, "polarization": action.polarization, "coordinate_units": "nm", "bandwidth_nm": action.bandwidth_nm, "angular_spread_deg": action.angular_spread_deg})
        models = {f.family_id: f for f in self.families}
        fits = {f.family_id: f for f in competition.fits} if competition else {}
        commitments = []; frozen_means = {}
        for family_id, candidate in (candidates or {"prior": None}).items():
            if candidate is None:
                lower = np.full(len(action.wavelengths_nm), -.1); upper = np.full(len(lower), 1.1); mean = (lower + upper) / 2
            else:
                mean = self.forward.predict(models[family_id], fits[family_id].parameters, action)
                scenarios = [self.forward.predict(models[family_id], b["parameters"], action, vertex) for b in fits[family_id].basins for vertex in tolerance_vertices(action.frontend)]
                if not scenarios:
                    scenarios = [mean]
                radius = norm.ppf(1 - self.config.alpha / (2 * len(mean))) * np.sqrt(np.diag(self.config.noise.covariance(mean)))
                lower = np.min(scenarios, axis=0) - radius; upper = np.max(scenarios, axis=0) + radius
            spec = PredictionSpec(object_id=self.run_id + ":" + stage + ":prediction:" + family_id, source_run_id=self.run_id,
                candidate_ref=candidate.ref() if candidate else None, program_ref=program.ref(), snapshot_id=self.repository.latest_snapshot().snapshot_id,
                input_refs=(candidate.ref(),) if candidate else (), raw_channel=action.channel, units="1", statistic="simultaneous_envelope", observable="diagnostic_intensity",
                tolerance_source="declared Gaussian noise plus finite fitted-basin and front-end-corner envelope; not global parameter coverage",
                lower=tuple(map(float, lower)), upper=tuple(map(float, upper)), coordinate=action.wavelengths_nm,
                grid={"acquisition_identity": identity.model_dump(mode="json"), "angle_deg": action.angle_deg, "polarization": action.polarization, "coordinate_units": "nm"},
                distribution={"mean": list(map(float, mean)), "candidate_id": family_id, "parameter_uncertainty": "finite fitted basin and tolerance corner envelope"})
            commitment = commit_prediction(self.repository, spec, task_ref=self.task.ref(), evidence_cutoff="before:" + program_ir.program_id)
            commitments.append((family_id, commitment)); self.prediction_refs.append(spec.ref()); frozen_means[family_id] = mean
        frozen_readout = self._context(stage + ":frozen-readout", {"readout": asdict(readout) if readout else None, "likelihood_count": 0, "program_ref": program.ref().model_dump(mode="json")})
        request_id = self.run_id + ":" + stage + ":" + action.content_hash
        self._context(stage + ":acquisition-dispatch", {"request_id": request_id, "action": asdict(action), "state": "submitted", "unknown_outcome_policy": "do not resubmit without durable provider receipt"}, (program.ref(), frozen_readout.ref()))
        datum = self.provider.acquire(action, request_id=request_id)
        if not isinstance(datum, DiagnosticDatum) or datum.action != action:
            raise ValueError("provider acquisition action identity differs")
        if any(row["acquisition_id"] == datum.acquisition_id for row in self.acquisition_index):
            raise ValueError("provider reused a raw acquisition")
        expected_cov = np.eye(len(datum.values)) * self.config.noise.measurement_std ** 2
        if not np.allclose(datum.covariance, expected_cov, rtol=1e-9, atol=1e-15):
            raise ValueError("provider raw covariance differs from frozen protocol")
        self.measurement_spent += action.cost
        evidence_tier = getattr(self.provider, "evidence_tier", "unvalidated_provider")
        validated = getattr(self.provider, "noise_validated", False) is True
        observation = self._put(Observation(object_id=self.run_id + ":" + stage + ":observation", source_run_id=self.run_id, acquisition_id=datum.acquisition_id,
            objective_ref=self.task.ref(), binding={"program_ref": program.ref().model_dump(mode="json"), "acquisition_identity": identity.model_dump(mode="json"), "conditions": identity.conditions},
            raw_blocks=({"block_id": action.channel, "channel": action.channel, "wavelengths_nm": action.wavelengths_nm, "coordinate": action.wavelengths_nm,
                "coordinate_units": "nm", "units": "1", "angle_deg": action.angle_deg, "polarization": action.polarization, "values": datum.values, "covariance": datum.covariance,
                "calibration_group": action.session_id, "calibration_std": self.config.noise.offset_std, "gaussian_validated": validated},),
            quality={"execution_status": "completed", "numerical_valid": True, "noise_validated": validated, "evidence_tier": evidence_tier, "validation_scope": "provider-declared Gaussian noise; bounded explicit session gain/offset nuisance"}, cost_refs=(request_id,)))
        self.observation_refs.append(observation.ref())
        verdicts = {}
        self.last_acquisition_evaluations = {}
        for family_id, commitment in commitments:
            verdict = evaluate_prediction(self.repository, commitment, observation.ref())
            evaluation = self._put(PredictionEvaluation(object_id=self.run_id + ":" + stage + ":evaluation:" + family_id, source_run_id=self.run_id,
                prediction_ref=commitment.prediction_ref, observation_ref=observation.ref(), metrics=asdict(verdict)))
            verdicts[family_id] = evaluation.model_dump(mode="json")["metrics"]
            self.last_acquisition_evaluations[family_id] = evaluation.ref()
            if verdict.state != "supported":
                self.negative_refs.append(evaluation.ref())
        row = {"acquisition_id": datum.acquisition_id, "observation_ref": observation.ref().model_dump(mode="json"), "acquisition_sequence": self.repository.acquisition_commit_sequence(datum.acquisition_id),
            "prediction_sequences": {family_id: commitment.committed_sequence for family_id, commitment in commitments}, "program_ref": program.ref().model_dump(mode="json"), "acquisition_identity": identity.model_dump(mode="json"), "cost": action.cost}
        if readout:
            weights = np.asarray(readout.weights)
            row["derived_readout"] = {"value": float(weights @ datum.values), "frozen_predictions": {key: float(weights @ value) for key, value in frozen_means.items()}, "raw_noise_variance": float(weights @ np.asarray(datum.covariance) @ weights), "frozen_design_marginal_variance": readout.variance, "likelihood_count": 0}
        self.acquisition_index.append(row)
        return datum, verdicts

    def run(self, initial_action, output):
        output = Path(output); output.mkdir(parents=True, exist_ok=True)
        initial, _ = self._acquire(initial_action, stage="initial")
        data = [initial]; trajectory = []; competition = None; last_candidates = {}; last_verdicts = {}; confirmation = "not_performed"
        stop_reason = "round_budget_exhausted"
        try:
            competition = refit_competition(self.families, data, self.forward, self.config)
            self.last_competition_ref = self._context("initial-competition", asdict(competition), tuple(self.observation_refs)).ref()
            if competition.state in {"model_family_inadequate", "numerical_failure"}:
                self.negative_refs.append(self.last_competition_ref)
            last_candidates = self._candidates(competition, "initial-fit")
            for iteration in range(self.config.max_rounds):
                if competition.state != "mechanisms_confounded":
                    stop_reason = competition.state; break
                selection = design_diagnostic_action(self.families, data, competition, self.forward, self.config, self.search_space,
                    session_id="diagnostic-" + str(iteration), remaining_budget=self.config.measurement_budget - self.measurement_spent - self.search_space.acquisition_cost - self.search_space.mounting_cost)
                selected_context = self._context("design:" + str(iteration), asdict(selection), tuple(c.ref() for c in last_candidates.values()))
                atomic_write_json(output / f"CANDIDATES_{iteration}.json", asdict(selection))
                if selection.selected is None:
                    stop_reason = selection.state; break
                before = competition
                datum, last_verdicts = self._acquire(selection.selected, stage="diagnostic-" + str(iteration), competition=competition, candidates=last_candidates, readout=selection.readout)
                data.append(datum)
                competition = refit_competition(self.families, data, self.forward, self.config, previous=competition)
                record = {"iteration": iteration, "selection_ref": selected_context.ref().model_dump(mode="json"), "before": asdict(before), "prequential_verdicts": last_verdicts, "after_all_family_refits": asdict(competition), "new_observation_reused_as_training": True, "post_refit_is_independent_validation": False}
                trajectory.append(record)
                context = self._context("competition:" + str(iteration), record, tuple(self.observation_refs))
                self.last_competition_ref = context.ref()
                if competition.state in {"model_family_inadequate", "numerical_failure"}:
                    self.negative_refs.append(context.ref())
                last_candidates = self._candidates(competition, "fit-" + str(iteration))
                if len(competition.retained_families) == 1:
                    stop_reason = competition.state
                    confirm_action = replace(selection.selected, action_id=selection.selected.action_id + ":confirmation", session_id="independent-confirmation")
                    if self.measurement_spent + confirm_action.cost <= self.config.measurement_budget:
                        _, final_verdicts = self._acquire(confirm_action, stage="confirmation", competition=competition, candidates=last_candidates, readout=selection.readout)
                        confirmation = final_verdicts[competition.selected_family]["state"]
                        self.confirmation_refs = (self.last_acquisition_evaluations[competition.selected_family],)
                        self._context("confirmation-result", {"verdicts": final_verdicts, "used_for_refit": False, "selected_before_confirmation": competition.selected_family}, (self.observation_refs[-1], *self.last_acquisition_evaluations.values()))
                    break
        except DiagnosticBudgetExceeded as exc:
            stop_reason = str(exc)
        if competition is None or not last_candidates:
            self._context("blocked", {"state": "budget_exhausted_before_competition", "stop_reason": stop_reason, "ledger": self.forward.ledger()})
            raise DiagnosticBudgetExceeded(stop_reason)
        selected_id = competition.selected_family or min(last_candidates)
        outcome = ResearchOutcome(model_adequacy_state=competition.state, local_prediction_state=confirmation,
            identity_state="bound", numerical_state="failure" if competition.state == "numerical_failure" else "valid",
            noise_state="calibrated" if getattr(self.provider, "noise_validated", False) else "not_calibrated",
            mechanism_state="model_set_inadequate" if competition.state == "model_family_inadequate" else "distinguished" if len(competition.retained_families) == 1 and confirmation == "supported" else "confounded",
            unknown_exploration_state="budget_exhausted" if "budget" in stop_reason else "not_authorized" if competition.state == "model_family_inadequate" else "not_needed",
            unresolved_failure_refs=tuple(self.negative_refs) if competition.state == "model_family_inadequate" else ())
        result = self._put(SelectedResearchResult(object_id=self.run_id + ":selected", source_run_id=self.run_id,
            input_refs=(self.last_competition_ref,), asset_refs=self.confirmation_refs, candidate_ref=last_candidates[selected_id].ref(), prediction_refs=tuple(self.prediction_refs), negative_result_refs=tuple(self.negative_refs),
            equivalent_explanations=competition.retained_families, claim_strength="prediction", candidate_roles={"research_outcome": outcome.model_dump(mode="json"),
                "selected_family": competition.selected_family, "candidate_role": "conditional representative; no forced winner when all families fail", "stop_reason": stop_reason,
                "adoption_status": "supported_within_bounded_mechanism_set" if confirmation == "supported" and len(competition.retained_families) == 1 else "withheld_pending_further_evidence",
                "classification_scope": "finite candidate families and optimizer search; thresholds not yet calibrated for population accuracy"}))
        from .method_lifecycle import finalize_completed_research
        lifecycle = finalize_completed_research(self.repository, task_ref=self.task.ref(), result_ref=result.ref())
        atomic_write_json(output / "METHOD_LIFECYCLE.json", lifecycle.model_dump(mode="json"))
        report = {"method_lifecycle": lifecycle.model_dump(mode="json"), "schema_version": "diagnostic-research-run.v1", "run_id": self.run_id, "result": result.model_dump(mode="json"), "competition": asdict(competition), "trajectory": trajectory,
            "acquisition_index": self.acquisition_index, "raw_data": [asdict(d) for d in data], "measurement_cost": self.measurement_spent, "compute_ledger": self.forward.ledger(),
            "evidence_tier": getattr(self.provider, "evidence_tier", "unvalidated_provider"), "hidden_answer_consumed_by_design": False, "runtime_fingerprints":self.runtime_fingerprints,
            "limitations": ["Finite optimizer failures do not establish global mechanism impossibility", "No E01 accuracy, E02 cost reduction or E04 improvement threshold is established by one run", "Independent confirmation is same specimen and new session, not transfer or fabricated-device validation", "Front-end tolerance corners and finite bandwidth/angular quadrature are bounded approximations", "No empirical hardware noise calibration or complete complex-response baseline is claimed", "Chi-square discrepancy threshold uses frozen raw-noise covariance and n data degrees; no search-wide false-rejection or population calibration claim"]}
        atomic_write_json(output / "DIAGNOSTIC_RESULT.json", report)
        atomic_write_json(output / "SELECTED_RESEARCH_RESULT.json", result.model_dump(mode="json"))
        atomic_write_json(output / "ACQUISITION_INDEX.json", self.acquisition_index)
        snapshots = [json.loads(row[0]) for row in self.repository.db.execute("SELECT payload FROM snapshots ORDER BY sequence")]
        atomic_write_json(output / "SCIENCE_SNAPSHOTS.json", snapshots)
        return report
