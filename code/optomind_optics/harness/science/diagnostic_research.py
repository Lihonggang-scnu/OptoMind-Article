from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path
import json
import hashlib
import sqlite3
import uuid
import numpy as np
from scipy.stats import norm

from .contracts import AcquisitionIdentity, DecisionContext, ExperimentProgram, ModelCandidate, ObjectRef, Observation, PredictionEvaluation, PredictionSpec, ResearchOutcome, ResearchTask, ScientificClaim, SelectedResearchResult, canonical_hash
from .diagnostic_design import (DiagnosticBudgetExceeded, DiagnosticForward, DiagnosticDatum,
    CompetitionResult, FamilyFit, DiagnosticSelection, BoundedReadout, action_from_dict,
    design_diagnostic_action, refit_competition, tolerance_vertices)
from .prediction import PredictionCommit, commit_prediction, evaluate_prediction
from .program_ir import ProgramIR, ProgramNode
from .repository import ScienceRepository
from .jobs import ExecutionGate, ToolCapability
from .program_runner import ProgramExecutionReceipt, ProgramRunner
from .program_compiler import compile_program
from .compute_ledger import ComputeLedger, ComputeBudgetExceeded
from optomind_research.runtime.artifact_store import atomic_write_json


# Keep a strong lifetime reference so CPython object-ID reuse cannot attach a
# different anonymous provider to an earlier run. No provider mutation is needed.
_ANONYMOUS_PROVIDERS = []


def _anonymous_provider_identity(provider):
    for instance, identity in _ANONYMOUS_PROVIDERS:
        if instance is provider:
            return identity
    identity = "process-bound:" + uuid.uuid4().hex
    _ANONYMOUS_PROVIDERS.append((provider, identity))
    return identity


class DiagnosticExecutionPending(RuntimeError):
    """An effect is unresolved. Resume queries receipts; it never purchases again."""

    def __init__(self, receipt):
        self.receipt = receipt
        super().__init__("diagnostic acquisition " + receipt.state + ": " + str(receipt.blocked_node))


def _datum(payload):
    return DiagnosticDatum(payload["acquisition_id"], action_from_dict(payload["action"]),
                           tuple(payload["values"]), tuple(map(tuple, payload["covariance"])))


def _competition(payload):
    return CompetitionResult(**{**payload,
        "fits": tuple(FamilyFit(**{**fit, "basins": tuple(fit["basins"]), "starts": tuple(fit["starts"]),
            "acquisition_ids": tuple(fit["acquisition_ids"]), "parameter_bounds": tuple(fit["parameter_bounds"])}) for fit in payload["fits"]),
        "retained_families": tuple(payload["retained_families"]), "acquisition_ids": tuple(payload["acquisition_ids"])})


def _selection(payload):
    raw = payload["readout"]
    readout = BoundedReadout(**{**raw, "weights": tuple(raw["weights"]),
                              "nonzero_indices": tuple(raw["nonzero_indices"])}) if raw else None
    return DiagnosticSelection(**{**payload, "selected": action_from_dict(payload["selected"]) if payload["selected"] else None,
                                  "readout": readout, "candidates": tuple(payload["candidates"])})


class DiagnosticProviderAdapter:
    """Durable bridge from typed diagnostic acquisitions to the shared C03 gate.

    The underlying provider may expose status(request_id) -> DiagnosticDatum | None.
    An acquire-only provider cannot recover the gap between its external effect and
    this journal commit. That gap remains unknown, with its reservation retained.
    """

    def __init__(self, provider, repository, *, provider_identity):
        self.provider = provider
        self.provider_identity = provider_identity
        self.db = sqlite3.connect(str(repository.root / "diagnostic_provider.sqlite"), timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""CREATE TABLE IF NOT EXISTS diagnostic_requests(
            key TEXT PRIMARY KEY, binding TEXT NOT NULL, state TEXT NOT NULL, receipt TEXT);
            CREATE TABLE IF NOT EXISTS diagnostic_provider_identity(value TEXT NOT NULL);""")
        identity = canonical_hash(provider_identity)
        saved = self.db.execute("SELECT value FROM diagnostic_provider_identity").fetchone()
        if saved and saved[0] != identity:
            self.db.close()
            raise ValueError("diagnostic provider recovery identity changed")
        if not saved:
            with self.db:
                self.db.execute("INSERT INTO diagnostic_provider_identity VALUES(?)", (identity,))

    def close(self):
        self.db.close()

    def bind(self, key, *, action, request_id, payload):
        binding = {"action": asdict(action), "request_id": request_id,
                   "payload_hash": canonical_hash(payload), "provider_identity": self.provider_identity}
        serialized = json.dumps(binding, sort_keys=True, allow_nan=False)
        with self.db:
            old = self.db.execute("SELECT binding FROM diagnostic_requests WHERE key=?", (key,)).fetchone()
            if old and old[0] != serialized:
                raise ValueError("diagnostic acquisition recovery identity changed")
            self.db.execute("INSERT OR IGNORE INTO diagnostic_requests VALUES(?,?,'ready',NULL)", (key, serialized))

    def _row(self, key):
        row = self.db.execute("SELECT binding,state,receipt FROM diagnostic_requests WHERE key=?", (key,)).fetchone()
        if row is None:
            raise ValueError("diagnostic acquisition lacks frozen request binding")
        return json.loads(row[0]), row[1], json.loads(row[2]) if row[2] else None

    def _record(self, key, binding, datum):
        if not isinstance(datum, DiagnosticDatum) or datum.action != action_from_dict(binding["action"]):
            raise ValueError("provider acquisition action identity differs")
        result = {"state": "completed", "values": list(datum.values), "datum": asdict(datum),
                  "cost": {"calls": 1., "cost": datum.action.cost},
                  "measurement_receipt": {"calibration_group": datum.action.session_id}}
        with self.db:
            self.db.execute("UPDATE diagnostic_requests SET state='completed',receipt=? WHERE key=?",
                            (json.dumps(result, sort_keys=True, allow_nan=False), key))
        return result

    def submit(self, key, payload):
        binding, state, receipt = self._row(key)
        if binding["payload_hash"] != canonical_hash(payload):
            raise ValueError("diagnostic provider program payload changed")
        if receipt is not None:
            return receipt
        # A second submit is recovery, even if invoked outside ExecutionGate.
        if state != "ready":
            return self.status(key)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            current = self._row(key)
            if current[1] != "ready":
                self.db.rollback()
                return self.status(key)
            self.db.execute("UPDATE diagnostic_requests SET state='submitted' WHERE key=?", (key,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        datum = self.provider.acquire(action_from_dict(binding["action"]), request_id=binding["request_id"])
        return self._record(key, binding, datum)

    def status(self, key):
        binding, _, receipt = self._row(key)
        if receipt is not None:
            return receipt
        status = getattr(self.provider, "status", None)
        datum = status(binding["request_id"]) if callable(status) else None
        if datum is None:
            return {"state": "unknown_outcome"}
        return self._record(key, binding, datum)


class _DurableDiagnosticForward(DiagnosticForward):
    """Reserve real attempted compute before execution, including crash/retry work."""

    def __init__(self, config, ledger):
        super().__init__(config)
        self.compute_ledger = ledger
        used = ledger.snapshot()["usage"]
        self.prediction_calls = used.get("diagnostic_predictions", 0)
        self.solver_points = used.get("diagnostic_solver_points", 0)

    def ledger(self):
        usage = self.compute_ledger.snapshot()["usage"]
        return {**super().ledger(), "prediction_calls": usage.get("diagnostic_predictions", 0),
                "solver_points": usage.get("diagnostic_solver_points", 0)}

    def predict(self, family, parameters, action, tolerance=()):
        points = (len(action.wavelengths_nm) * (3 if action.bandwidth_nm else 1)
                  * (3 if action.angular_spread_deg else 1) * (2 if action.polarization == "unpolarized" else 1))
        try:
            events = tuple(self.compute_ledger.reserve_many({"diagnostic_predictions": 1,
                "diagnostic_solver_points": points}).values())
        except ComputeBudgetExceeded as exc:
            raise DiagnosticBudgetExceeded(str(exc)) from exc
        try:
            result = super().predict(family, parameters, action, tolerance)
        except BaseException as exc:
            for event in events:
                self.compute_ledger.settle(event, type(exc).__name__)
            raise
        for event in events:
            self.compute_ledger.settle(event)
        return result


class DiagnosticResearchLoop:
    def __init__(self, *, families, config, search_space, repository, run_id, provider,
                 task=None, runner=None, provider_identity=None):
        self.families = tuple(families)
        if len({f.family_id for f in families}) != len(families) or not 2 <= len(families) <= 12:
            raise ValueError("diagnostic research requires 2..12 distinct mechanism families")
        self.config = config
        self.search_space = search_space
        self.repository = repository
        self.run_id = run_id
        self.provider = provider
        self.measurement_spent = 0.
        self.acquisition_index = []
        self.prediction_refs = []
        self.observation_refs = []
        self.negative_refs = []
        self.last_acquisition_evaluations = {}
        self.last_competition_ref = None
        self.confirmation_refs = ()
        self.task = ResearchTask.model_validate(task.model_dump(mode="json")) if task is not None else ResearchTask(object_id=run_id + ":task", source_run_id=run_id,
            question="Discriminate bounded competing sample mechanisms by automatically designing a removable diagnostic front end",
            objective={"kind": "mechanism_diagnosis"},
            constraints={"scope": {"config": asdict(config), "search_space": asdict(search_space), "families": [asdict(f) for f in families]}, "paid_network_calls": 0, "evidence_tier": "provider_declared"})
        if not isinstance(self.task, ResearchTask) or self.task.source_run_id != run_id:
            raise ValueError("diagnostic task must preserve the canonical run identity")
        declared_identity = provider_identity or getattr(provider, "recovery_identity", None)
        if declared_identity is None:
            # Legacy acquire-only providers remain usable, but a fresh anonymous
            # provider instance cannot silently attach to a persisted run.
            declared_identity = _anonymous_provider_identity(provider)
        self.provider_identity = {"identity": declared_identity,
            "type": type(provider).__module__ + "." + type(provider).__qualname__,
            "evidence_tier": getattr(provider, "evidence_tier", "unvalidated_provider"),
            "noise_validated": getattr(provider, "noise_validated", False) is True}
        self._put(self.task)
        runtime_files=("diagnostic_design.py","diagnostic_research.py","diagnostic_cli.py","model_ir.py","model_compiler.py","contracts.py","nested_contracts.py","prediction.py","program_runner.py","program_compiler.py","jobs.py","compute_ledger.py")
        self.runtime_fingerprints={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in runtime_files}
        self._context("runtime-protocol", {"runtime_fingerprints": self.runtime_fingerprints,
            "provider_evidence_tier": getattr(provider, "evidence_tier", "unvalidated_provider"), "model_api_calls": 0,
            "provider_identity": self.provider_identity, "task_ref": self.task.ref().model_dump(mode="json"),
            "scope": {"config": asdict(config), "search_space": asdict(search_space), "families": [asdict(f) for f in families]}})
        self._frozen_inputs = canonical_hash(self._inputs())
        self.compute_ledger = ComputeLedger(repository.root / "diagnostic_compute.sqlite", {
            "diagnostic_predictions": config.max_prediction_calls, "diagnostic_solver_points": config.max_solver_points})
        self.forward = _DurableDiagnosticForward(config, self.compute_ledger)
        self.provider_adapter = DiagnosticProviderAdapter(provider, repository, provider_identity=self.provider_identity)
        self._owns_runner = runner is None
        if runner is None:
            gate = ExecutionGate(repository.root / "diagnostic_gate.sqlite", limits={
                "calls": float(config.max_rounds + 2), "cost": config.measurement_budget},
                capability=ToolCapability(effect_class="external_measurement", domain="bounded_diagnostic",
                    channels=("R", "T", "A"), units=("1",), supports_status=True, supports_resume=True))
            runner = ProgramRunner(repository.root / "diagnostic_programs.sqlite", gates={c: gate for c in ("R", "T", "A")}, providers={})
        self.runner = runner
        for channel, gate in runner.gates.items():
            if channel not in {"R", "T", "A"}:
                continue
            old = runner.providers.get(channel)
            if old is not None and old is not self.provider_adapter:
                raise ValueError("diagnostic runner channel already has another provider")
            runner.providers[channel] = self.provider_adapter

    def _inputs(self):
        return {"task": self.task.model_dump(mode="json"), "families": [asdict(f) for f in self.families],
                "config": asdict(self.config), "search_space": asdict(self.search_space),
                "provider_identity": self.provider_identity,
                "provider_declared_identity": getattr(self.provider, "recovery_identity", None),
                "evidence_tier": getattr(self.provider, "evidence_tier", "unvalidated_provider"),
                "noise_validated": getattr(self.provider, "noise_validated", False) is True}

    def _check_frozen(self):
        if canonical_hash(self._inputs()) != self._frozen_inputs:
            raise ValueError("diagnostic frozen task/model/provider identity changed")

    def close(self):
        self.compute_ledger.close()
        self.provider_adapter.close()
        if self._owns_runner:
            self.runner.close()
            for gate in set(self.runner.gates.values()):
                gate.close()
        else:
            for channel in tuple(self.runner.providers):
                if self.runner.providers[channel] is self.provider_adapter:
                    del self.runner.providers[channel]

    def _current(self, object_id):
        latest = self.repository.latest_snapshot()
        ref = next((r for r in latest.object_refs if r.object_id == object_id), None) if latest else None
        return self.repository.load(ref) if ref else None

    def _put(self, obj):
        old = self._current(obj.object_id)
        if old is not None:
            if old.ref() != obj.ref():
                raise ValueError("diagnostic recovery identity changed: " + obj.object_id)
            return old
        latest = self.repository.latest_snapshot()
        self.repository.commit([obj], task_ref=self.task.ref(), evidence_cutoff="diagnostic:" + obj.object_id,
                               expected_sequence=latest.committed_sequence if latest else 0)
        return obj

    def _prediction_commit(self, spec):
        sequence = self.repository.object_commit_sequence(spec.ref())
        saved = self.repository.db.execute("SELECT payload FROM snapshots WHERE sequence=?", (sequence,)).fetchone()
        return PredictionCommit(spec.ref(), spec.snapshot_id, json.loads(saved[0])["snapshot_id"], sequence)

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
        self._check_frozen()
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
            prediction_id = self.run_id + ":" + stage + ":prediction:" + family_id
            spec = self._current(prediction_id)
            if spec is not None:
                if (not isinstance(spec, PredictionSpec) or spec.acquisition_identity != identity
                        or spec.candidate_ref != (candidate.ref() if candidate else None)
                        or spec.coordinate != action.wavelengths_nm or spec.raw_channel != action.channel):
                    raise ValueError("diagnostic frozen prediction recovery identity changed")
                commitment = self._prediction_commit(spec)
                mean = np.asarray(spec.distribution["mean"])
            else:
                if self._current(self.run_id + ":" + stage + ":acquisition-dispatch") is not None:
                    raise ValueError("diagnostic dispatched acquisition is missing its frozen prediction")
                if candidate is None:
                    lower = np.full(len(action.wavelengths_nm), -.1); upper = np.full(len(lower), 1.1); mean = (lower + upper) / 2
                else:
                    mean = self.forward.predict(models[family_id], fits[family_id].parameters, action)
                    scenarios = [self.forward.predict(models[family_id], b["parameters"], action, vertex) for b in fits[family_id].basins for vertex in tolerance_vertices(action.frontend)]
                    if not scenarios:
                        scenarios = [mean]
                    radius = norm.ppf(1 - self.config.alpha / (2 * len(mean))) * np.sqrt(np.diag(self.config.noise.covariance(mean)))
                    lower = np.min(scenarios, axis=0) - radius; upper = np.max(scenarios, axis=0) + radius
                spec = PredictionSpec(object_id=prediction_id, source_run_id=self.run_id,
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
        key = f"{program_ir.program_id}:{program_ir.revision}:measure"
        self.provider_adapter.bind(key, action=action, request_id=request_id, payload=self.runner._payload(program_ir, node))
        gate = self.runner.gates[action.channel]
        plan = compile_program(program_ir, capabilities={action.channel: gate.capability}, limits=gate.limits)
        try:
            execution = self.runner.run(plan, prediction_commits={"measure": {
                "predictions": {name: {"prediction_ref": commit.prediction_ref.model_dump(mode="json"),
                    "prior_snapshot_id": commit.prior_snapshot_id, "committed_snapshot_id": commit.committed_snapshot_id,
                    "committed_sequence": commit.committed_sequence} for name, commit in commitments},
                "frozen_readout_ref": frozen_readout.ref().model_dump(mode="json")}})
        except (RuntimeError, ConnectionError, TimeoutError) as exc:
            # A transport error after dispatch is an unresolved execution, not a
            # scientific refutation or terminal Article result. The gate alone
            # decides whether an effect may already have happened.
            pending = gate.receipt(key)
            if pending is not None and pending.state in {"submitting", "running", "unknown_outcome"}:
                execution = ProgramExecutionReceipt(program_ir.program_id, program_ir.content_hash,
                    pending.state, {}, dict(program_ir.initial_samples), "measure")
                raise DiagnosticExecutionPending(execution) from exc
            raise
        if execution.state in {"failed", "cancelled"}:
            raise RuntimeError("diagnostic acquisition execution " + execution.state + ": " + str(execution.blocked_node))
        if execution.state != "completed":
            raise DiagnosticExecutionPending(execution)
        datum = _datum(execution.nodes["measure"]["provider_receipt"]["datum"])
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
            quality={"execution_status": "completed", "numerical_valid": True, "noise_validated": validated, "evidence_tier": evidence_tier, "validation_scope": "provider-declared Gaussian noise; bounded explicit session gain/offset nuisance"}, cost_refs=tuple(execution.nodes["measure"]["cost_refs"])))
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
        row = {"stage": stage, "action": asdict(action), "acquisition_id": datum.acquisition_id, "observation_ref": observation.ref().model_dump(mode="json"), "acquisition_sequence": self.repository.acquisition_commit_sequence(datum.acquisition_id),
            "prediction_sequences": {family_id: commitment.committed_sequence for family_id, commitment in commitments}, "program_ref": program.ref().model_dump(mode="json"), "acquisition_identity": identity.model_dump(mode="json"), "cost": action.cost}
        if readout:
            weights = np.asarray(readout.weights)
            row["derived_readout"] = {"value": float(weights @ datum.values), "frozen_predictions": {key: float(weights @ value) for key, value in frozen_means.items()}, "raw_noise_variance": float(weights @ np.asarray(datum.covariance) @ weights), "frozen_design_marginal_variance": readout.variance, "likelihood_count": 0}
        self.acquisition_index.append(row)
        return datum, verdicts

    def _claims(self, competition, outcome):
        scope = {"task_ref": self.task.ref().model_dump(mode="json"),
            "evidence_tier": getattr(self.provider, "evidence_tier", "unvalidated_provider"),
            "domain": "finite declared mechanism families, bounded optimizer search and frozen noise model",
            "transfer": "not established", "population_accuracy": "not established"}
        confirmed = outcome.mechanism_state == "distinguished"
        claims = [self._put(ScientificClaim(object_id=self.run_id + ":claim:outcome", source_run_id=self.run_id,
            statement=("Independent same-specimen confirmation supports " + str(competition.selected_family)
                       + " within the declared finite mechanism set." if confirmed else
                       "Mechanism adoption is withheld; the bounded diagnostic outcome is " + outcome.state + "."),
            status="supported" if confirmed else "unknown", scope=scope,
            evidence_refs=(self.last_competition_ref, *self.observation_refs, *self.confirmation_refs)))]
        for index, ref in enumerate(self.negative_refs):
            evidence = self.repository.load(ref)
            refs = (ref,)
            if isinstance(evidence, PredictionEvaluation):
                state = evidence.metrics.get("state", "not_evaluable")
                statement = "Frozen diagnostic prediction " + evidence.prediction_ref.object_id + " agreed with its independently acquired raw observation within the declared envelope."
                status = "refuted" if state == "violated" else "unknown"
                refs = (ref, evidence.prediction_ref, evidence.observation_ref)
            else:
                state = evidence.summary.get("after_all_family_refits", evidence.summary).get("state", "unknown")
                statement = "At least one declared mechanism passed the bounded all-family refit criterion in " + ref.object_id + "."
                status = "refuted" if state == "model_family_inadequate" else "unknown"
                refs = (ref, *evidence.input_refs)
            claims.append(self._put(ScientificClaim(object_id=self.run_id + ":claim:negative:" + str(index),
                source_run_id=self.run_id, statement=statement, status=status,
                scope={**scope, "recorded_evaluation_state": state, "global_mechanism_impossibility": "not established"}, evidence_refs=refs)))
        return claims

    def run(self, initial_action, output):
        self._check_frozen()
        output = Path(output); output.mkdir(parents=True, exist_ok=True)
        # Replay committed scientific stages, reconstructing projections without
        # repeating measurements or completed fitting/design computation.
        self.measurement_spent = 0.
        self.acquisition_index = []; self.prediction_refs = []; self.observation_refs = []; self.negative_refs = []
        self.last_acquisition_evaluations = {}; self.last_competition_ref = None; self.confirmation_refs = ()
        self._context("initial-action", {"action": asdict(initial_action)})
        terminal = self._current(self.run_id + ":terminal-state")
        if terminal is not None:
            return self._resume_terminal(terminal.summary, output)
        initial, _ = self._acquire(initial_action, stage="initial")
        data = [initial]; trajectory = []; competition = None; last_candidates = {}; last_verdicts = {}; confirmation = "not_performed"
        stop_reason = "round_budget_exhausted"
        try:
            cached = self._current(self.run_id + ":initial-competition")
            competition = _competition(cached.summary) if cached else refit_competition(self.families, data, self.forward, self.config)
            self.last_competition_ref = self._context("initial-competition", asdict(competition), tuple(self.observation_refs)).ref()
            if competition.state in {"model_family_inadequate", "numerical_failure"}:
                self.negative_refs.append(self.last_competition_ref)
            last_candidates = self._candidates(competition, "initial-fit")
            for iteration in range(self.config.max_rounds):
                if competition.state != "mechanisms_confounded":
                    stop_reason = competition.state; break
                cached = self._current(self.run_id + ":design:" + str(iteration))
                selection = _selection(cached.summary) if cached else design_diagnostic_action(self.families, data, competition, self.forward, self.config, self.search_space,
                    session_id="diagnostic-" + str(iteration), remaining_budget=self.config.measurement_budget - self.measurement_spent - self.search_space.acquisition_cost - self.search_space.mounting_cost)
                selected_context = self._context("design:" + str(iteration), asdict(selection), tuple(c.ref() for c in last_candidates.values()))
                atomic_write_json(output / f"CANDIDATES_{iteration}.json", asdict(selection))
                if selection.selected is None:
                    stop_reason = selection.state; break
                before = competition
                datum, last_verdicts = self._acquire(selection.selected, stage="diagnostic-" + str(iteration), competition=competition, candidates=last_candidates, readout=selection.readout)
                data.append(datum)
                cached = self._current(self.run_id + ":competition:" + str(iteration))
                competition = _competition(cached.summary["after_all_family_refits"]) if cached else refit_competition(self.families, data, self.forward, self.config, previous=competition)
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
        self._context("terminal-state", {"competition": asdict(competition), "trajectory": trajectory,
            "data": [asdict(d) for d in data], "confirmation": confirmation, "stop_reason": stop_reason,
            "candidates": {key: value.ref().model_dump(mode="json") for key, value in last_candidates.items()},
            "last_competition_ref": self.last_competition_ref.model_dump(mode="json"),
            "prediction_refs": [r.model_dump(mode="json") for r in self.prediction_refs],
            "negative_refs": [r.model_dump(mode="json") for r in self.negative_refs],
            "confirmation_refs": [r.model_dump(mode="json") for r in self.confirmation_refs],
            "acquisition_index": self.acquisition_index}, tuple(self.observation_refs))
        return self._finish(competition, trajectory, data, last_candidates, confirmation, stop_reason, output)

    def _resume_terminal(self, state, output):
        # Verify every completed effect through ProgramRunner's authoritative
        # receipt checks. Missing receipts are corruption, never a new purchase.
        for row in state["acquisition_index"]:
            stage = row["stage"]
            action = action_from_dict(row["action"])
            key = f"{self.run_id}:{stage}:1:measure"
            receipt = self.runner.gates[action.channel].receipt(key)
            if receipt is None or receipt.state != "completed":
                raise ValueError("terminal diagnostic effect lacks completed authoritative receipt")
            specs = [self.repository.load(ObjectRef.model_validate(ref)) for ref in state["prediction_refs"]
                     if ref["object_id"].startswith(self.run_id + ":" + stage + ":prediction:")]
            if not specs:
                raise ValueError("terminal diagnostic acquisition lacks frozen predictions")
            candidates = {spec.distribution["candidate_id"]:
                          self.repository.load(spec.candidate_ref) if spec.candidate_ref else None for spec in specs}
            frozen = self._current(self.run_id + ":" + stage + ":frozen-readout")
            raw = frozen.summary["readout"]
            readout = BoundedReadout(**raw) if raw else None
            self._acquire(action, stage=stage, candidates=candidates, readout=readout)
        if canonical_hash(self.acquisition_index) != canonical_hash(state["acquisition_index"]):
            raise ValueError("terminal diagnostic acquisition projection differs from authoritative receipts")
        # Include any legitimately frozen predictions from an interrupted stage
        # that exhausted compute before it could acquire another observation.
        self.prediction_refs = [ObjectRef.model_validate(ref) for ref in state["prediction_refs"]]
        self.negative_refs = [ObjectRef.model_validate(ref) for ref in state["negative_refs"]]
        self.confirmation_refs = tuple(ObjectRef.model_validate(ref) for ref in state["confirmation_refs"])
        self.last_competition_ref = ObjectRef.model_validate(state["last_competition_ref"])
        candidates = {key: self.repository.load(ObjectRef.model_validate(ref)) for key, ref in state["candidates"].items()}
        for ref in (*self.prediction_refs, *self.negative_refs, *self.confirmation_refs, self.last_competition_ref):
            self.repository.load(ref)
        return self._finish(_competition(state["competition"]), state["trajectory"], [_datum(d) for d in state["data"]],
                            candidates, state["confirmation"], state["stop_reason"], output)

    def _finish(self, competition, trajectory, data, last_candidates, confirmation, stop_reason, output):
        selected_id = competition.selected_family or min(last_candidates)
        outcome = ResearchOutcome(model_adequacy_state=competition.state, local_prediction_state=confirmation,
            identity_state="bound", numerical_state="failure" if competition.state == "numerical_failure" else "valid",
            noise_state="calibrated" if getattr(self.provider, "noise_validated", False) else "not_calibrated",
            mechanism_state="model_set_inadequate" if competition.state == "model_family_inadequate" else "distinguished" if len(competition.retained_families) == 1 and confirmation == "supported" else "confounded",
            unknown_exploration_state="budget_exhausted" if "budget" in stop_reason else "not_authorized" if competition.state == "model_family_inadequate" else "not_needed",
            unresolved_failure_refs=tuple(self.negative_refs) if competition.state == "model_family_inadequate" else ())
        claims = self._claims(competition, outcome)
        result = self._put(SelectedResearchResult(object_id=self.run_id + ":selected", source_run_id=self.run_id,
            input_refs=(self.last_competition_ref,), asset_refs=(*self.confirmation_refs, *self.observation_refs), claim_refs=tuple(c.ref() for c in claims), candidate_ref=last_candidates[selected_id].ref(), prediction_refs=tuple(self.prediction_refs), negative_result_refs=tuple(self.negative_refs),
            equivalent_explanations=competition.retained_families, claim_strength="prediction", candidate_roles={"research_outcome": outcome.model_dump(mode="json"), "task_ref": self.task.ref().model_dump(mode="json"),
                "selected_family": competition.selected_family, "candidate_role": "conditional representative; no forced winner when all families fail", "stop_reason": stop_reason,
                "adoption_status": "supported_within_bounded_mechanism_set" if confirmation == "supported" and len(competition.retained_families) == 1 else "withheld_pending_further_evidence",
                "classification_scope": "finite candidate families and optimizer search; thresholds not yet calibrated for population accuracy"}))
        from .method_lifecycle import finalize_completed_research
        lifecycle = finalize_completed_research(self.repository, task_ref=self.task.ref(), result_ref=result.ref())
        atomic_write_json(output / "METHOD_LIFECYCLE.json", lifecycle.model_dump(mode="json"))
        report = {"method_lifecycle": lifecycle.model_dump(mode="json"), "schema_version": "diagnostic-research-run.v1", "run_id": self.run_id, "result": result.model_dump(mode="json"), "competition": asdict(competition), "trajectory": trajectory,
            "acquisition_index": self.acquisition_index, "raw_data": [asdict(d) for d in data], "measurement_cost": self.measurement_spent, "compute_ledger": self.forward.ledger(), "durable_compute_ledger": self.compute_ledger.snapshot(),
            "evidence_tier": getattr(self.provider, "evidence_tier", "unvalidated_provider"), "hidden_answer_consumed_by_design": False, "runtime_fingerprints":self.runtime_fingerprints,
            "limitations": ["Finite optimizer failures do not establish global mechanism impossibility", "No E01 accuracy, E02 cost reduction or E04 improvement threshold is established by one run", "Independent confirmation is same specimen and new session, not transfer or fabricated-device validation", "Front-end tolerance corners and finite bandwidth/angular quadrature are bounded approximations", "No empirical hardware noise calibration or complete complex-response baseline is claimed", "Chi-square discrepancy threshold uses frozen raw-noise covariance and n data degrees; no search-wide false-rejection or population calibration claim"]}
        atomic_write_json(output / "DIAGNOSTIC_RESULT.json", report)
        atomic_write_json(output / "SELECTED_RESEARCH_RESULT.json", result.model_dump(mode="json"))
        atomic_write_json(output / "ACQUISITION_INDEX.json", self.acquisition_index)
        snapshots = [json.loads(row[0]) for row in self.repository.db.execute("SELECT payload FROM snapshots ORDER BY sequence")]
        atomic_write_json(output / "SCIENCE_SNAPSHOTS.json", snapshots)
        return report
