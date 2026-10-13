"""C22 chronological evaluation with durable, fail-closed execution receipts.

Trusted callbacks see only a task's public input and earlier legal feedback. This
is a data boundary, not a sandbox for hostile Python. A pending callback is never
automatically repeated: an operator must reconcile its external receipt first.
"""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import time
from typing import Literal
from pydantic import Field, model_validator
from .contracts import StrictModel, canonical_hash


class LifetimeTask(StrictModel):
    task_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    provenance_group: str = Field(min_length=1)
    inputs: tuple[float, ...]
    target: float = Field(allow_inf_nan=False)
    legal_feedback: bool = True
    @model_validator(mode="after")
    def finite(self):
        import math
        if not all(math.isfinite(x) for x in self.inputs):
            raise ValueError("nonfinite task input")
        return self


class LifetimeProtocol(StrictModel):
    protocol_id: str
    tasks: tuple[LifetimeTask, ...] = Field(min_length=1)
    frozen_ability: tuple[LifetimeTask, ...] = ()
    representations: tuple[Literal["text", "structure", "executable"], ...] = ("text", "structure", "executable")
    cost_unit: str = Field(min_length=1)
    initial_library_snapshot: str = ""
    initial_library_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    learning_enabled: bool = False
    evidence_scope: Literal["offline_fixture", "observational", "prospective"] = "offline_fixture"
    @model_validator(mode="after")
    def disjoint(self):
        if self.initial_library_snapshot and self.initial_library_cost is None:
            raise ValueError("warm initial library requires explicit construction cost accounting")
        all_tasks = self.tasks + self.frozen_ability
        if len({t.task_id for t in all_tasks}) != len(all_tasks):
            raise ValueError("duplicate task identity")
        if {t.provenance_group for t in self.tasks} & {t.provenance_group for t in self.frozen_ability}:
            raise ValueError("frozen ability provenance overlaps learning stream")
        if not self.representations or len(set(self.representations)) != len(self.representations):
            raise ValueError("representations must be nonempty and unique")
        return self


class PublicTask(StrictModel):
    task_id: str
    family: str
    inputs: tuple[float, ...]


class LegalFeedback(StrictModel):
    task: PublicTask
    target: float
    step: int = Field(ge=0)


class LifetimeView(StrictModel):
    task: PublicTask
    history: tuple[LegalFeedback, ...]
    representation: Literal["cold", "text", "structure", "executable"]
    snapshot_id: str
    library_snapshot: str = ""


class ForwardReceipt(StrictModel):
    prediction: float = Field(allow_inf_nan=False)
    costs: dict[str, float]
    retrieval_seconds: float = Field(default=0.0, ge=0, allow_inf_nan=False)
    external_receipt: str = Field(min_length=1)
    library_snapshot: str = ""
    library_storage_bytes: int | None = Field(default=None, ge=0)
    method_consumption_receipts: tuple[dict, ...] = ()
    @model_validator(mode="after")
    def valid_costs(self):
        import math
        required = {"evaluation", "learning", "maintenance", "revalidation"}
        if not required <= self.costs.keys() or any(not math.isfinite(v) or v < 0 for v in self.costs.values()):
            raise ValueError("finite nonnegative measured cost categories required")
        return self


class LifetimeReport(StrictModel):
    protocol_hash: str
    evidence_scope: str
    cost_unit: str
    complete: bool
    forward_transfer: dict[str, float]
    old_capability: dict[str, float | None]
    negative_transfer: dict[str, int]
    tail_loss: dict[str, float]
    family_loss: dict[str, dict[str, float]]
    worst_family: dict[str, str]
    latency: dict[str, float]
    storage: int
    storage_basis: Literal["completion_closure", "legacy_posthoc_capture", "historical_unsealed"] = "historical_unsealed"
    total_cost: dict[str, float]
    comparison_total_cost: dict[str, float]
    amortization: dict[str, float | None]
    records: tuple[dict, ...]

    @model_validator(mode="after")
    def finite_serializable_report(self):
        canonical_hash(self.model_dump(mode="json"))
        return self


def public(task):
    return PublicTask(task_id=task.task_id, family=task.family, inputs=task.inputs)


class LifetimeEvaluator:
    def __init__(self, root, protocol: LifetimeProtocol, *, evaluator_id: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.protocol = LifetimeProtocol.model_validate_json(protocol.model_dump_json())
        self.db = sqlite3.connect(self.root / "lifetime.sqlite3", timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT); CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, view TEXT NOT NULL, result TEXT, elapsed REAL, state TEXT NOT NULL); CREATE TABLE IF NOT EXISTS storage_samples (key TEXT PRIMARY KEY, bytes INTEGER NOT NULL); CREATE TABLE IF NOT EXISTS operation_integrity (key TEXT PRIMARY KEY, digest TEXT NOT NULL);")
        identity = canonical_hash({"protocol": self.protocol.model_dump(mode="json"), "evaluator_id": evaluator_id})
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('identity',?)", (identity,))
            row = self.db.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
            if row[0] != identity:
                raise ValueError("protocol/evaluator mismatch: resume requires exact frozen identity")
        self.identity = identity
        expected = (len(self.protocol.tasks) + 2 * len(self.protocol.frozen_ability)) * (1 + len(self.protocol.representations))
        if self.protocol.learning_enabled:
            expected += sum(task.legal_feedback for task in self.protocol.tasks)
        done = self.db.execute("SELECT COUNT(*) FROM operations WHERE state='done'").fetchone()[0]
        self._historical_unsealed_completion = (done == expected and not self.db.execute(
            "SELECT 1 FROM metadata WHERE key='report_closure'").fetchone())

    def _storage_bytes(self):
        return sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())

    def _closed_storage(self):
        """Seal a measured closure once; replay must not resample mutable WAL bytes.

        Older completed journals have no original closure measurement. Capture
        their current footprint explicitly as post-hoc, without rewriting any
        operation, storage sample, historical report or execution receipt.
        """
        operation_digest = canonical_hash(list(self.db.execute(
            "SELECT key,digest FROM operation_integrity ORDER BY key")))
        row = self.db.execute("SELECT value FROM metadata WHERE key='report_closure'").fetchone()
        if row is None:
            payload = {"schema": "lifetime-report-closure.v1", "identity": self.identity,
                       "operation_digest": operation_digest, "storage_bytes": self._storage_bytes(),
                       "storage_basis": "legacy_posthoc_capture" if self._historical_unsealed_completion else "completion_closure",
                       "measurement_scope": "journal_root_before_closure_write"}
            value = json.dumps({**payload, "digest": canonical_hash(payload)}, sort_keys=True, allow_nan=False)
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('report_closure',?)", (value,))
            row = self.db.execute("SELECT value FROM metadata WHERE key='report_closure'").fetchone()
        payload = json.loads(row[0])
        digest = payload.pop("digest", None)
        if (digest != canonical_hash(payload) or payload.get("identity") != self.identity
                or payload.get("operation_digest") != operation_digest
                or type(payload.get("storage_bytes")) is not int or payload["storage_bytes"] < 0
                or payload.get("storage_basis") not in {"completion_closure", "legacy_posthoc_capture"}):
            raise ValueError("journal closure integrity mismatch; recover from an authoritative closure receipt")
        return payload["storage_bytes"], payload["storage_basis"]

    def close(self):
        self.db.close()

    def preview(self):
        invalid=[]
        for key,view,result,elapsed,state in self.db.execute("SELECT key,view,result,elapsed,state FROM operations WHERE state='done'"):
            try:
                self._verify_receipt(key,(view,result,elapsed,state))
            except (ValueError,TypeError):
                invalid.append(key)
        return {"corrupt_or_unverified_completed":invalid,"protocol_hash": self.identity, "tasks": len(self.protocol.tasks), "frozen_ability": len(self.protocol.frozen_ability), "representations": self.protocol.representations, "pending": [r[0] for r in self.db.execute("SELECT key FROM operations WHERE state='pending'")]}

    def reconcile(self, key, receipt: ForwardReceipt, *, elapsed_seconds: float):
        """Attach a verified receipt; never infer success or repeat external work."""
        import math
        receipt = ForwardReceipt.model_validate_json(receipt.model_dump_json())
        if key.startswith("learning_update:") and (not receipt.library_snapshot or receipt.library_storage_bytes is None):
            raise ValueError("learning reconciliation requires committed library snapshot and measured storage")
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
            raise ValueError("invalid measured elapsed time")
        with self.db:
            updated = self.db.execute("UPDATE operations SET result=?,elapsed=?,state='done' WHERE key=? AND state='pending'", (receipt.model_dump_json(), elapsed_seconds, key)).rowcount
            if updated != 1:
                raise ValueError("reconciliation requires one pending operation")
            storage=self._storage_bytes()
            self.db.execute("INSERT INTO storage_samples VALUES (?,?)", (key,storage))
            view=self.db.execute("SELECT view FROM operations WHERE key=?",(key,)).fetchone()[0]
            self.db.execute("INSERT INTO operation_integrity VALUES (?,?)",(key,self._receipt_digest(key,view,receipt.model_dump_json(),elapsed_seconds,storage)))

    @staticmethod
    def _receipt_digest(key, view, result, elapsed, storage):
        return canonical_hash({"key":key,"view":json.loads(view),"result":json.loads(result),"elapsed":float(elapsed),"storage":int(storage)})

    def _verify_receipt(self,key,row):
        storage=self.db.execute("SELECT bytes FROM storage_samples WHERE key=?",(key,)).fetchone()
        digest=self.db.execute("SELECT digest FROM operation_integrity WHERE key=?",(key,)).fetchone()
        if not storage or not digest or digest[0] != self._receipt_digest(key,row[0],row[1],row[2],storage[0]):
            raise ValueError("journal receipt integrity mismatch; recover from a verified external receipt")

    def repair_receipt(self,key,receipt,*,elapsed_seconds):
        """Explicit operator recovery of a corrupt done receipt; never execute it.

        Only a corrupted receipt is replaceable. Correct committed results are
        immutable. The caller must obtain the replacement from its authoritative
        external execution/learning journal, not infer or fabricate an outcome.
        """
        row=self.db.execute("SELECT view,result,elapsed,state FROM operations WHERE key=?",(key,)).fetchone()
        if not row or row[3] != 'done':
            raise ValueError('repair requires an existing completed operation')
        try:
            self._verify_receipt(key,row)
        except (ValueError,TypeError):
            pass
        else:
            raise ValueError('valid completed receipts cannot be overwritten')
        # Reconciliation is its own durable transaction. Marking an invalid row
        # pending first preserves unknown-outcome behavior if repair is interrupted.
        with self.db:
            self.db.execute("DELETE FROM metadata WHERE key='report_closure'")
            self.db.execute("UPDATE operations SET state='pending' WHERE key=?",(key,))
            self.db.execute("DELETE FROM storage_samples WHERE key=?",(key,))
            self.db.execute("DELETE FROM operation_integrity WHERE key=?",(key,))
        self.reconcile(key,receipt,elapsed_seconds=elapsed_seconds)

    def _evaluate(self, key, view, forward):
        serialized = view.model_dump_json()
        row = self.db.execute("SELECT view,result,elapsed,state FROM operations WHERE key=?", (key,)).fetchone()
        if row:
            if row[0] != serialized:
                raise ValueError("replay view mismatch")
            if row[3] != "done":
                raise RuntimeError("pending operation requires receipt reconciliation: " + key)
            self._verify_receipt(key,row)
            return ForwardReceipt.model_validate_json(row[1]), row[2]
        with self.db:
            self.db.execute("INSERT INTO operations VALUES (?,?,NULL,NULL,'pending')", (key, serialized))
        started = time.perf_counter()
        result = forward(LifetimeView.model_validate_json(serialized))
        receipt = ForwardReceipt.model_validate(result)
        elapsed = time.perf_counter() - started
        self.reconcile(key, receipt, elapsed_seconds=elapsed)
        return receipt, elapsed

    def run(self, forward, *, learn=None):
        if self.protocol.learning_enabled != (learn is not None):
            raise ValueError("learning callback must match the frozen protocol")
        history = []
        library_snapshot = self.protocol.initial_library_snapshot or canonical_hash([])
        records = []
        arms = ("cold",) + self.protocol.representations
        # Ability outcomes never become feedback; evaluate at K0 and final Kn.
        def evaluate(stage, step, task, representation):
            legal = tuple(history) if representation != "cold" else ()
            library = library_snapshot if representation != "cold" else canonical_hash([])
            snapshot = canonical_hash({"history":[h.model_dump(mode="json") for h in legal],"library":library})
            view = LifetimeView(task=public(task), history=legal, representation=representation, snapshot_id=snapshot,library_snapshot=library)
            key = f"{stage}:{step}:{task.task_id}:{representation}"
            receipt, elapsed = self._evaluate(key, view, forward)
            import math
            try:
                loss=(receipt.prediction-task.target)**2
            except OverflowError as exc:
                raise ValueError("nonfinite evaluation loss") from exc
            if not math.isfinite(loss):
                raise ValueError("nonfinite evaluation loss")
            storage_bytes=self.db.execute("SELECT bytes FROM storage_samples WHERE key=?",(key,)).fetchone()[0]
            records.append({"key": key,"storage_bytes":storage_bytes,"library_storage_bytes":receipt.library_storage_bytes, "stage": stage, "step": step, "family": task.family, "arm": representation, "snapshot_id": snapshot, "library_snapshot":library, "history_steps": [h.step for h in legal], "loss": loss, "latency_seconds": elapsed, "retrieval_seconds": receipt.retrieval_seconds, "costs": receipt.costs, "external_receipt": receipt.external_receipt,"method_consumption_receipts":receipt.method_consumption_receipts})
        for task in self.protocol.frozen_ability:
            for arm in arms:
                evaluate("ability_initial", 0, task, arm)
        for k, task in enumerate(self.protocol.tasks):
            for arm in arms:
                evaluate("forward", k, task, arm)
            # Kk becomes Kk+1 only after all paired evaluations have receipts.
            if task.legal_feedback:
                history.append(LegalFeedback(task=public(task), target=task.target, step=k))
                if learn is not None:
                    # The learner sees task k only after every arm has committed
                    # its forward evaluation. It must return the actual new
                    # immutable MethodRegistry snapshot and measured costs.
                    view=LifetimeView(task=public(task),history=tuple(history),representation="executable",
                                      snapshot_id=canonical_hash([h.model_dump(mode="json") for h in history]),library_snapshot=library_snapshot)
                    def legal_update(update_view):
                        receipt=ForwardReceipt.model_validate(learn(update_view))
                        if not receipt.library_snapshot or receipt.library_storage_bytes is None:
                            raise ValueError("legal feedback update must identify its committed library snapshot and measured library storage")
                        return receipt
                    key=f"learning_update:{k}:{task.task_id}"
                    update,elapsed=self._evaluate(key,view,legal_update)
                    if not update.library_snapshot or update.library_storage_bytes is None:
                        raise ValueError("recovered learning update has no library snapshot/storage")
                    library_snapshot=update.library_snapshot
                    records.append({"key":key,"stage":"learning_update","step":k,"family":task.family,"arm":"shared_learning",
                                    "snapshot_id":view.snapshot_id,"library_snapshot":library_snapshot,"history_steps":[h.step for h in history],
                                    "costs":update.costs,"latency_seconds":elapsed,"retrieval_seconds":update.retrieval_seconds,
                                    "external_receipt":update.external_receipt,"library_storage_bytes":update.library_storage_bytes,"storage_bytes":self.db.execute("SELECT bytes FROM storage_samples WHERE key=?",(key,)).fetchone()[0]})
        for task in self.protocol.frozen_ability:
            for arm in arms:
                evaluate("ability_final", len(self.protocol.tasks), task, arm)
        transfer = {}; negative = {}; tail = {}; old = {}; latency = {}; costs = {}; amortization = {}; families = {}; worst = {}
        for arm in arms:
            selected = [r for r in records if r["arm"] == arm]
            curve = [r for r in selected if r["stage"] == "forward"]
            cold = {r["step"]:r for r in records if r["arm"] == "cold" and r["stage"] == "forward"}
            gains = [cold[r["step"]]["loss"]-r["loss"] for r in curve]
            transfer[arm] = sum(gains)/len(gains)
            negative[arm] = sum(g < 0 for g in gains)
            tail[arm] = max(r["loss"] for r in curve)
            families[arm] = {family:sum(r['loss'] for r in curve if r['family']==family)/sum(r['family']==family for r in curve) for family in sorted({r['family'] for r in curve})}
            worst[arm] = max(families[arm],key=families[arm].get)
            initial = sum(r["loss"] for r in selected if r["stage"] == "ability_initial")
            final = sum(r["loss"] for r in selected if r["stage"] == "ability_final")
            old[arm] = final-initial if self.protocol.frozen_ability else None  # Positive means forgetting; None means unmeasured.
            latency[arm] = sum(r["latency_seconds"] for r in selected)
            costs[arm] = sum(sum(r["costs"].values()) for r in selected)
            amortization[arm] = sum(gains)/costs[arm] if costs[arm] else None
        updates=[r for r in records if r["stage"]=="learning_update"]
        costs["shared_learning"]=(self.protocol.initial_library_cost or 0.)+sum(sum(r["costs"].values()) for r in updates)
        latency["shared_learning"]=sum(r["latency_seconds"] for r in updates)
        # Actual costs keep shared construction/update costs exactly once. Each
        # hypothetical warm deployment must nevertheless pay for that history;
        # use the full cost for paired comparisons and gain amortization.
        comparison_costs={arm:costs[arm]+(costs["shared_learning"] if arm != "cold" else 0.) for arm in arms}
        amortization={arm:transfer[arm]*len(self.protocol.tasks)/comparison_costs[arm] if comparison_costs[arm] else None for arm in arms}
        storage, storage_basis = self._closed_storage()
        return LifetimeReport(protocol_hash=self.identity, evidence_scope=self.protocol.evidence_scope, cost_unit=self.protocol.cost_unit, complete=True, forward_transfer=transfer, old_capability=old, negative_transfer=negative, tail_loss=tail, family_loss=families, worst_family=worst, latency=latency, storage=storage, storage_basis=storage_basis, total_cost=costs, comparison_total_cost=comparison_costs, amortization=amortization, records=tuple(records))

    def replay(self):
        def no_execution(view):
            raise RuntimeError("incomplete replay cannot execute missing operations")
        # Check all expected operation keys before replay to avoid creating pending rows.
        count = (len(self.protocol.tasks)+2*len(self.protocol.frozen_ability))*(1+len(self.protocol.representations))
        if self.protocol.learning_enabled:
            count += sum(task.legal_feedback for task in self.protocol.tasks)
        done = self.db.execute("SELECT COUNT(*) FROM operations WHERE state='done'").fetchone()[0]
        if done != count:
            raise RuntimeError("incomplete journal: use resume or reconcile pending receipts")
        return self.run(no_execution,learn=no_execution if self.protocol.learning_enabled else None)


def nearest_history_forward(view):
    """Executable offline reference, costs in measured CPU seconds, not money.

Representations deliberately use the same predictor: this tests controlled
history equality and accounting, and does not assert representation superiority.
"""
    start = time.process_time()
    retrieval = time.perf_counter()
    eligible = [h for h in view.history if h.task.family == view.task.family and len(h.task.inputs) == len(view.task.inputs)]
    nearest = min(eligible, key=lambda h: sum((x-y)**2 for x,y in zip(h.task.inputs,view.task.inputs)), default=None)
    retrieval = time.perf_counter()-retrieval
    prediction = nearest.target if nearest else 0.0
    return ForwardReceipt(prediction=prediction, costs={"evaluation":time.process_time()-start,"learning":0.0,"maintenance":0.0,"revalidation":0.0}, retrieval_seconds=retrieval, external_receipt="local:"+canonical_hash(view),library_storage_bytes=len(json.dumps([h.model_dump(mode="json") for h in view.history],sort_keys=True).encode()))


class ScientificLifetimeAdapter:
    """Reuse C16 ScientificForwardRunner/C04 controller, with lawful history.

The trusted policy builder receives the same bounded view as the predictor and
must charge its actual proposal/revalidation costs to the supplied CostMeter.
A forward score is compared to the task's evaluator-owned scalar target. No
hidden outcome, frozen-ability score, or future task is passed to the builder.
"""
    def __init__(self, forward, policy_builder, *, available_budget, final_validation_reserve,
                 initial_snapshot, operation_budget, attempt_cost):
        import math
        if not all(math.isfinite(v) and v >= 0 for v in (available_budget, final_validation_reserve, operation_budget, attempt_cost)):
            raise ValueError('finite nonnegative budgets required')
        if final_validation_reserve > available_budget or attempt_cost > operation_budget:
            raise ValueError('budget/reserve mismatch')
        self.forward=forward; self.policy_builder=policy_builder
        self.available_budget=available_budget; self.final_validation_reserve=final_validation_reserve
        self.initial_snapshot=initial_snapshot; self.operation_budget=operation_budget; self.attempt_cost=attempt_cost

    def __call__(self, view):
        import math
        from .learning.learning_runner import ActionInput, CostMeter, ForwardResult
        from .method_assets import PolicyPayload
        meter=CostMeter(self.operation_budget)
        meter.charge('evaluation', self.attempt_cost)
        policy=PolicyPayload.model_validate(self.policy_builder(view,meter))
        action=ActionInput(task_id=view.task.task_id,initial_snapshot=self.initial_snapshot,
                           library_snapshot=view.library_snapshot or view.snapshot_id,observations=view.task.inputs,
                           available_budget=self.available_budget,final_validation_reserve=self.final_validation_reserve)
        result=self.forward(action,policy,meter)
        if not isinstance(result,ForwardResult) or not result.valid or not math.isfinite(result.score):
            raise ValueError('invalid scientific forward result')
        if meter.total > meter.limit:
            raise ValueError('scientific lifetime operation exceeded budget')
        costs={name:0. for name in ('evaluation','learning','maintenance','revalidation')}
        for kind,amount in meter.entries:
            costs[kind]=costs.get(kind,0.)+amount
        return ForwardReceipt(prediction=result.score,costs=costs,
                              external_receipt='scientific:'+canonical_hash({'view':view.model_dump(mode='json'),'trajectory':result.trajectory,'output':result.output,'costs':costs}))


class MethodLibraryLifetimeAdapter:
    """Actual equal-library text/structure/executable consumer boundary.

All warm arms receive the same gated assets from the exact Kk registry snapshot.
Text is canonical JSON, structure is detached typed-asset JSON, and executable
access is the existing FrozenMethodProvider. Callbacks perform task-specific
forward work; their receipts must report actual costs. A declared CPU conversion
charges retrieval/validation/serialization overhead in the protocol cost unit.
"""
    def __init__(self, registry, *, task_ref_for_view, facts_for_view, consumers,
                 preparation_cost_per_cpu_second, allow_task_validated=False):
        import math
        if set(consumers) != {'cold','text','structure','executable'}:
            raise ValueError('all four real representation consumers are required')
        if not math.isfinite(preparation_cost_per_cpu_second) or preparation_cost_per_cpu_second < 0:
            raise ValueError('finite nonnegative explicit preparation cost conversion required')
        self.registry=registry;self.task_ref_for_view=task_ref_for_view;self.facts_for_view=facts_for_view
        self.consumers=dict(consumers);self.rate=preparation_cost_per_cpu_second
        self.allow_task_validated=allow_task_validated

    def __call__(self, view):
        if view.representation=='cold':
            return ForwardReceipt.model_validate(self.consumers['cold'](view,None))
        from .method_runtime import FrozenMethodProvider
        started=time.perf_counter();cpu_started=time.process_time()
        if self.registry.snapshot().snapshot_id != view.library_snapshot:
            raise ValueError('current method registry differs from frozen Kk; future or stale library access refused')
        provider=FrozenMethodProvider(self.registry,task_ref=self.task_ref_for_view(view),
                                      facts=self.facts_for_view(view),allow_task_validated=self.allow_task_validated)
        prepared=tuple(item for kind in ('model_fragment','program','policy') for item in provider.prepare(kind))
        assets=tuple(self.registry.repository.load(item.asset_ref).model_dump(mode='json') for item in prepared)
        if view.representation=='text':
            representation=json.dumps(assets,sort_keys=True,ensure_ascii=False,allow_nan=False)
        elif view.representation=='structure':
            representation=json.loads(json.dumps(assets,allow_nan=False))
        else:
            representation=provider
        retrieval_seconds=time.perf_counter()-started
        preparation_cost=(time.process_time()-cpu_started)*self.rate
        result=ForwardReceipt.model_validate(self.consumers[view.representation](view,representation))
        # Do not let one comparison arm mutate the shared knowledge before its
        # peers run. Only the post-evaluation learner may commit Kk+1.
        if self.registry.snapshot().snapshot_id != view.library_snapshot:
            raise ValueError('forward consumer mutated Kk before the legal feedback phase')
        data=result.model_dump(mode='json')
        data['costs']['library_preparation']=data['costs'].get('library_preparation',0.)+preparation_cost
        data['retrieval_seconds']+=retrieval_seconds
        data['library_storage_bytes']=sum(p.stat().st_size for p in self.registry.repository.root.rglob('*') if p.is_file())
        data['method_consumption_receipts']=[r.model_dump(mode='json') for r in provider.receipts]
        return ForwardReceipt.model_validate(data)
