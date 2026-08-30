from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any

import pytest

from optomind_optics.harness.article_tournament import (
    COMPOSITE_WEIGHTS,
    STOP_INVALID_STRATEGY,
    STOP_POLICY_STOP,
    StrategyChoice,
    StrategySnapshot,
    MetricValue,
    RouteTrace,
    TournamentCheckpoint,
    TournamentIntegrityError,
    TournamentResult,
    TournamentStrategy,
    _TraceState,
    _advance_trace,
    _build_trace_ledger,
    _canonical_json,
    _pool_metadata,
    _sha256_file,
    compute_tournament_result_id,
    default_strategies,
    evaluate_trace,
    load_trace_bank,
    make_checkpoint,
    _rehash_vector,
    resume_policy_trace,
    run_bank_tournament,
    run_policy_trace,
    run_tournament,
    validate_tournament_result,
    write_tournament_result,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_BANKS = [
    REPO_ROOT / "accepted_examples" / "research_broadband_ar_source",
    REPO_ROOT / "accepted_examples" / "research_polarizing_beamsplitter",
]


def _compact_routes() -> list[dict[str, Any]]:
    return [
        {
            "route_id": f"route_{index:02d}",
            "title": f"Route {index}",
            "priority": index,
            "route_kind": "kind_a" if index <= 2 else "kind_b",
            "proposed_materials": (
                ["M1"] if index == 1 else ["M2"] if index == 2 else ["M1", "M2"]
            ),
            "proposed_topology": f"topology {index}",
            "design_variables": [
                f"v{offset}" for offset in range(3 + index)
            ],
            "scientific_hypothesis": f"hypothesis {index}",
            "design_principle": f"principle {index}",
            "soft_objectives": ["objective"],
            "expected_advantages": [],
            "known_risks": [],
            "parent_route_id": "",
            "revision_reason": "",
        }
        for index in (1, 2, 3)
    ]


def _candidate_summaries(index: int) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": f"cand_{index}_{offset}",
            "experiment_id": f"exp_{index}",
            "optimizer_id": f"optimizer_{index}",
            "certificate_id": f"{'a' * 64}",
            "artifact_ids": [
                f"experiments/exp_{index}/c/c_{offset}/PHYSICS_ACCEPTANCE_CERTIFICATE.json",
                f"experiments/exp_{index}/c/c_{offset}/OBJECTIVE_REPORT.json",
                f"experiments/exp_{index}/c/c_{offset}/ROBUSTNESS.json",
            ],
            "objective_report": {"schema_version": "tmm-objective-report.v1"},
            "robustness_report": (
                {"schema_version": "tmm-robustness-report.v1"}
                if offset <= 1
                else None
            ),
            "target_score": 0.5 + index * 0.1,
            "robustness_score": 0.4 + index * 0.1,
        }
        for offset in (1, 2, 3)
    ]


def _compact_iterations(
    *,
    missing_scores: bool = False,
) -> list[dict[str, Any]]:
    rows = []
    for index in (1, 2, 3):
        rows.append(
            {
                "iteration_id": f"iteration_{index:02d}",
                "route_id": f"route_{index:02d}",
                "route_title": f"Route {index}",
                "compilation_status": "compiled",
                "compilation_rationale": "ok",
                "compilation_errors": [],
                "run_status": "completed",
                "physically_valid_candidate_count": 3,
                "best_target_score": (
                    None if missing_scores else 0.5 + index * 0.1
                ),
                "best_robustness_score": (
                    None if missing_scores else 0.4 + index * 0.1
                ),
                "selected_candidate_ids": [
                    f"cand_{index}_1",
                    f"cand_{index}_2",
                ],
                "failure_categories": [],
                "experiment_summaries": [
                    {
                        "experiment_id": f"exp_{index}",
                        "mode": "optimize",
                        "physically_valid_candidate_count": 3,
                        "best_target_score": (
                            None if missing_scores else 0.5 + index * 0.1
                        ),
                        "selected_roles": {
                            "best_target_score": f"cand_{index}_1"
                        },
                    }
                ],
                "candidate_summaries": _candidate_summaries(index),
                "budget_usage": {
                    "forward_evaluations": 100 + index * 10,
                    "wall_time_seconds": 10 + index,
                },
                "result_path": (
                    f"iterations/iteration_{index:02d}/tmm_run/FINAL_RESULT.json"
                ),
                "task_path": "",
                "work_dir": "",
            }
        )
    return rows


def _compact_plan() -> dict[str, Any]:
    return {
        "status": "ok",
        "plan": {
            "problem_id": "problem",
            "planning_summary": "summary",
            "routes": _compact_routes(),
            "research_influence": [],
            "unresolved_decisions": [],
            "stop_if_all_routes_fail": "no",
        },
        "attempts": 1,
        "validation_errors": [],
        "normalization_warnings": [],
        "usage": [],
        "model_name": "none",
    }


def write_compact_bank(
    root: Path,
    *,
    seed: str = "base",
    iterations: list[dict[str, Any]] | None = None,
    routes: list[dict[str, Any]] | None = None,
) -> Path:
    iterations = iterations if iterations is not None else _compact_iterations()
    plan = _compact_plan()
    if routes is not None:
        plan["plan"]["routes"] = routes
    result = {
        "schema_version": "v1",
        "run_id": f"run-{seed}",
        "status": "completed",
        "stage": "research",
        "question": f"Question {seed}",
        "problem_analysis": {
            "target_observables": ["reflectance"],
            "preferred_behaviors": ["low reflectance"],
            "wavelengths_nm": [450.0, 700.0],
        },
        "method_research": {},
        "strategy_plan": plan["plan"],
        "iterations": iterations,
        "feedback_history": [],
        "final_answer": {},
    }
    files = {
        "RESEARCH_RESULT.json": result,
        "ITERATION_HISTORY.json": iterations,
        "STRATEGY_PLAN.json": plan,
        "FEEDBACK_HISTORY.json": [],
    }
    run_dir = root / f"bank_{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        (run_dir / name).write_text(json.dumps(data, indent=1), encoding="utf-8")
    return run_dir


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class _SpyStrategy(TournamentStrategy):
    strategy_id = "spy"
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        getattr(snapshot, "hidden_outcomes")  # must raise AttributeError
        return StrategyChoice(kind="select", route_id="route_01")


class _MutatingSnapshotStrategy(TournamentStrategy):
    strategy_id = "snapshot_mutator"
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        snapshot.next_decision_state["evil"] = True
        return StrategyChoice(kind="select", route_id="route_01")


class _InvalidSelectingStrategy(TournamentStrategy):
    strategy_id = "invalid_selector"
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        return StrategyChoice(kind="select", route_id="not-a-route")


class _StatefulStrategy(TournamentStrategy):
    strategy_id = "stateful"
    strategy_version = 1

    def __init__(self) -> None:
        self.seen: dict[str, float] = {}

    def clone(self) -> "_StatefulStrategy":
        return _StatefulStrategy()

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        for route_id, outcome in snapshot.revealed_by_route().items():
            if outcome.best_target_score is not None:
                self.seen[route_id] = outcome.best_target_score
        for route in snapshot.public_pool:
            if route.route_id not in snapshot.selected():
                return StrategyChoice(
                    kind="select",
                    route_id=route.route_id,
                    reason="first unselected",
                )
        return StrategyChoice(kind="stop", reason="pool exhausted")


def test_real_banks_load_with_planned_not_run_and_candidates(tmp_path) -> None:
    broadband_dir = REAL_BANKS[0]
    bank = load_trace_bank(broadband_dir)
    assert bank.route_count == 3
    assert len(bank.planned_not_run) == 1
    assert bank.planned_not_run[0].route_id == "route_04"
    result = run_tournament(REAL_BANKS)
    assert len(result.banks) == 2
    for bank_result in result.banks:
        assert len(bank_result.public_pool) == 3
        assert len(bank_result.source_bindings) == 4
        for binding in bank_result.source_bindings.values():
            assert len(binding.sha256) == 64
            real_path = next(
                path
                for path in REAL_BANKS
                if json.loads(
                    (path / "RESEARCH_RESULT.json").read_text(encoding="utf-8")
                )["run_id"]
                == bank_result.run_id
            )
            assert (
                _sha256_file(real_path / binding.relative_path)
                == binding.sha256
            )
        not_run = [
            entry
            for entry in bank_result.audit_ledger
            if entry.status == "not_run"
        ]
        if bank_result.run_id.startswith("generated-broadband"):
            assert len(not_run) == 1
            assert not_run[0].route_id == "route_04"
        else:
            assert not_run == []
    broadband = result.banks[0]
    route_01_outcome = next(
        outcome
        for policy in broadband.policies
        for trace in policy.traces
        for outcome in trace.revealed
        if outcome.route_id == "route_01"
    )
    assert len(route_01_outcome.candidates) == 8
    for candidate in route_01_outcome.candidates:
        assert candidate.certificate_id
        assert candidate.artifact_ids
        assert candidate.objective_report_present
    assert route_01_outcome.candidates[0].optimizer_id == "gradient_thickness"
    assert route_01_outcome.candidates[0].candidate_id == (
        "opt_4layer_mg_sio_ta_mg__gradient_thickness__01"
    )


def test_figure_readiness_zero_with_reason_on_real_banks() -> None:
    result = run_tournament(REAL_BANKS)
    for bank_result in result.banks:
        vectors = [
            vector
            for policy in bank_result.policies
            for vector in policy.metric_vectors
        ]
        figure_metrics = [vector.metrics["figure_readiness"] for vector in vectors]
        assert all(
            metric.normalized == 0.0 for metric in figure_metrics
        )
        assert all(
            "visual/table artifact" in metric.reason
            for metric in figure_metrics
        )


def test_unrevealed_outcome_isolation_and_decision_invariance(
    tmp_path,
) -> None:
    base = write_compact_bank(tmp_path, seed="base")
    iterations = _compact_iterations()
    iterations[1]["best_target_score"] = 0.99
    tampered = write_compact_bank(
        tmp_path,
        seed="tampered",
        iterations=iterations,
    )
    bank_a = load_trace_bank(base)
    bank_b = load_trace_bank(tampered)
    hybrid = [
        s for s in default_strategies() if s.strategy_id == "optomind_hybrid"
    ][0]
    trace_a = run_policy_trace(bank_a, hybrid, 1)
    trace_b = run_policy_trace(bank_b, hybrid, 1)
    assert trace_a.selected_order == trace_b.selected_order
    pool_a = _pool_metadata(bank_a)
    pool_b = _pool_metadata(bank_b)
    assert pool_a["oracle_best_target"] != pool_b["oracle_best_target"]
    ledger_a = ()
    vector_a = evaluate_trace(
        bank_a, trace_a, pool_a, ledger_a, (True, "test")
    )
    vector_b = evaluate_trace(
        bank_b, trace_b, pool_b, ledger_a, (True, "test")
    )
    raw_a = vector_a.metrics["experimental_gain"].raw
    raw_b = vector_b.metrics["experimental_gain"].raw
    assert raw_a["oracle_best_target"] != raw_b["oracle_best_target"]

    snapshot = StrategySnapshot(
        trace_id=bank_a.trace_id,
        strategy_id="spy",
        strategy_version=1,
        route_budget=3,
        remaining_budget=3,
        public_pool=bank_a.public_pool,
    )
    assert not hasattr(snapshot, "hidden_outcomes")
    assert not hasattr(snapshot, "full_pool_oracle")
    assert snapshot.revealed_by_route().get("route_02") is None
    with pytest.raises(AttributeError):
        _SpyStrategy().select(snapshot)


def test_equal_pools_budgets_no_duplicates_deterministic(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    first = run_bank_tournament(bank, budgets=[1, 2, 3])
    second = run_bank_tournament(bank, budgets=[1, 2, 3])
    for policy_result in first.policies:
        for trace in policy_result.traces:
            assert len(trace.selected_order) == len(set(trace.selected_order))
            assert trace.stop_reason in {
                "budget_exhausted",
                "policy_stop",
                "pool_exhausted",
                "invalid_strategy",
            }
    assert _canonical(first.model_dump(mode="json")) == _canonical(
        second.model_dump(mode="json")
    )
    pairs = {
        (policy.strategy_id, trace.route_budget)
        for policy in first.policies
        for trace in policy.traces
    }
    assert pairs == {
        (strategy_id, budget)
        for strategy_id in {policy.strategy_id for policy in first.policies}
        for budget in (1, 2, 3)
    }


def test_malformed_nonfinite_rejection_and_missing_scores(tmp_path) -> None:
    base = write_compact_bank(tmp_path, seed="bad")
    (base / "ITERATION_HISTORY.json").unlink()
    with pytest.raises(ValueError):
        load_trace_bank(base)

    iterations = _compact_iterations()
    iterations[0]["iteration_id"] = "iteration_02"
    dup = write_compact_bank(tmp_path, seed="dup", iterations=iterations)
    with pytest.raises(ValueError):
        load_trace_bank(dup)

    iterations = _compact_iterations()
    iterations[1]["best_target_score"] = float("nan")
    nonfinite = write_compact_bank(
        tmp_path,
        seed="nan",
        iterations=iterations,
    )
    with pytest.raises(ValueError):
        load_trace_bank(nonfinite)

    iterations = _compact_iterations()
    iterations[0]["run_status"] = "failed"
    cross = write_compact_bank(tmp_path, seed="cross", iterations=iterations)
    result_payload = json.loads(
        (cross / "RESEARCH_RESULT.json").read_text(encoding="utf-8")
    )
    result_payload["iterations"][0]["run_status"] = "completed"
    (cross / "RESEARCH_RESULT.json").write_text(
        json.dumps(result_payload, indent=1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_trace_bank(cross)

    bank = load_trace_bank(
        write_compact_bank(
            tmp_path,
            seed="missing",
            iterations=_compact_iterations(missing_scores=True),
        )
    )
    assert bank.hidden_outcomes["route_01"].best_target_score is None
    pool = _pool_metadata(bank)
    assert pool["oracle_best_target"] is None
    trace = run_policy_trace(bank, default_strategies()[0], 1)
    vector = evaluate_trace(bank, trace, pool, (), (True, "test"))
    assert vector.metrics["experimental_gain"].not_applicable


def test_checkpoint_resume_equivalence_and_rejections(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    hybrid = [
        s for s in default_strategies() if s.strategy_id == "optomind_hybrid"
    ][0]
    state = _TraceState()
    assert _advance_trace(bank, hybrid.clone(), 3, state)
    checkpoint = make_checkpoint(bank, hybrid, 3, state)
    resumed = resume_policy_trace(bank, hybrid, 3, checkpoint)
    uninterrupted = run_policy_trace(bank, hybrid, 3)
    assert _canonical(resumed.model_dump(mode="json")) == _canonical(
        uninterrupted.model_dump(mode="json")
    )

    other_bank = load_trace_bank(write_compact_bank(tmp_path, seed="other"))
    with pytest.raises(ValueError):
        resume_policy_trace(other_bank, hybrid, 3, checkpoint)
    with pytest.raises(ValueError):
        resume_policy_trace(bank, hybrid, 2, checkpoint)
    with pytest.raises(ValueError):
        resume_policy_trace(bank, default_strategies()[0], 3, checkpoint)

    tampered_order = checkpoint.model_copy(
        update={"selected_order": ("route_01", "route_03")}
    )
    with pytest.raises(ValueError):
        resume_policy_trace(bank, hybrid, 3, tampered_order)

    wrong_contract = checkpoint.model_copy(
        update={"evaluator_contract_version": "other-contract"}
    )
    with pytest.raises(ValueError):
        resume_policy_trace(bank, hybrid, 3, wrong_contract)

    checkpoint_from_other = make_checkpoint(other_bank, hybrid, 3, state)
    with pytest.raises(ValueError):
        resume_policy_trace(bank, hybrid, 3, checkpoint_from_other)


def test_checkpoint_revealed_tamper_rejected_even_with_recomputed_id(
    tmp_path,
) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    hybrid = [
        s for s in default_strategies() if s.strategy_id == "optomind_hybrid"
    ][0]
    state = _TraceState()
    _advance_trace(bank, hybrid.clone(), 3, state)
    checkpoint = make_checkpoint(bank, hybrid, 3, state)
    forged_outcome = checkpoint.revealed[0].model_copy(
        update={"best_target_score": 0.999}
    )
    forged = checkpoint.model_copy(
        update={"revealed": (forged_outcome,)}
    )
    # Recompute the unkeyed checkpoint_id with the public canonical hashing.
    payload = forged.model_dump(exclude={"checkpoint_id"}, mode="json")
    import hashlib

    forged = forged.model_copy(
        update={
            "checkpoint_id": hashlib.sha256(
                _canonical_json(payload).encode("utf-8")
            ).hexdigest()
        }
    )
    with pytest.raises(ValueError):
        resume_policy_trace(bank, hybrid, 3, forged)


def test_source_tamper_detected_at_checkpoint(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path, seed="tamper"))
    hybrid = [
        s for s in default_strategies() if s.strategy_id == "optomind_hybrid"
    ][0]
    state = _TraceState()
    _advance_trace(bank, hybrid.clone(), 3, state)
    checkpoint = make_checkpoint(bank, hybrid, 3, state)
    source_path = tmp_path / "bank_tamper" / "ITERATION_HISTORY.json"
    source_path.write_text(
        source_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    tampered_bank = load_trace_bank(tmp_path / "bank_tamper")
    with pytest.raises(ValueError):
        resume_policy_trace(tampered_bank, hybrid, 3, checkpoint)


def test_pareto_across_policies_and_dominated_policy(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    result = run_bank_tournament(bank, budgets=[1, 2, 3])
    for budget in (1, 2, 3):
        vectors = [
            vector
            for policy in result.policies
            for vector in policy.metric_vectors
            if vector.route_budget == budget
        ]
        assert any(vector.pareto_member for vector in vectors)


def test_deliberately_dominated_vector_not_pareto() -> None:
    from optomind_optics.harness.article_tournament import (
        MetricVector,
        _pareto_members,
    )

    def make(strategy_id: str, coverage: float, gain: float, validity: float):
        return MetricVector(
            trace_id="trace-x",
            strategy_id=strategy_id,
            strategy_version=1,
            route_budget=1,
            metrics={
                "coverage": MetricValue(
                    name="coverage", normalized=coverage
                ),
                "experimental_gain": MetricValue(
                    name="experimental_gain", normalized=gain
                ),
                "validity_ratio": MetricValue(
                    name="validity_ratio", normalized=validity
                ),
            },
            vector_hash="",
        )

    reference = make("reference", 0.8, 0.9, 1.0)
    dominated = make("dominated", 0.4, 0.5, 0.6)
    for item in (reference, dominated):
        item = item.model_copy(
            update={
                "vector_hash": hashlib.sha256(
                    _canonical_json(
                        item.model_dump(exclude={"vector_hash"}, mode="json")
                    ).encode("utf-8")
                ).hexdigest()
            }
        )
        if item.strategy_id == "reference":
            reference = item
        else:
            dominated = item
    members = _pareto_members([reference, dominated])
    assert members[reference.vector_hash] is True
    assert members[dominated.vector_hash] is False


def test_per_trace_ledger_complete_and_not_run(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    result = run_bank_tournament(bank, budgets=[1])
    for policy in result.policies:
        for trace in policy.traces:
            entries = [
                entry
                for entry in result.audit_ledger
                if entry.strategy_id == trace.strategy_id
                and entry.strategy_version == trace.strategy_version
                and entry.route_budget == trace.route_budget
            ]
            covered = {
                entry.route_id
                for entry in entries
                if entry.status != "rejected_invalid"
            }
            assert covered == {"route_01", "route_02", "route_03"}
            selected_entries = [
                entry
                for entry in entries
                if entry.status == "selected"
            ]
            assert len(selected_entries) == len(trace.selected_order)
            for entry in selected_entries:
                outcome = next(
                    item
                    for item in trace.revealed
                    if item.route_id == entry.route_id
                )
                assert entry.outcome_hash == outcome.outcome_hash
                assert set(entry.candidate_ids) == {
                    candidate.candidate_id
                    for candidate in outcome.candidates
                }


def test_hybrid_early_stop_scored_not_topped_up(tmp_path) -> None:
    routes = [
        {
            "route_id": f"route_{index:02d}",
            "title": f"Route {index}",
            "priority": index,
            "route_kind": "kind_a",
            "proposed_materials": ["M1"],
            "proposed_topology": f"topology {index}",
            "design_variables": [f"v{offset}" for offset in range(4)],
            "scientific_hypothesis": "h",
            "design_principle": "p",
            "soft_objectives": ["objective"],
            "expected_advantages": [],
            "known_risks": [],
            "parent_route_id": "",
            "revision_reason": "",
        }
        for index in (1, 2, 3)
    ]
    iterations = _compact_iterations()
    for index, row in enumerate(iterations):
        row["best_target_score"] = 0.9 - index * 0.2
        row["best_robustness_score"] = 0.8 - index * 0.1
    bank = load_trace_bank(
        write_compact_bank(
            tmp_path,
            seed="stop",
            iterations=iterations,
            routes=routes,
        )
    )
    hybrid = [
        s for s in default_strategies() if s.strategy_id == "optomind_hybrid"
    ][0]
    trace = run_policy_trace(bank, hybrid, 3)
    assert trace.stop_reason == STOP_POLICY_STOP
    assert len(trace.selected_order) < 3
    pool = _pool_metadata(bank)
    vector = evaluate_trace(bank, trace, pool, (), (True, "test"))
    assert vector.metrics["stop_quality"].normalized is not None
    assert float(vector.metrics["stop_quality"].raw["saved_cost"]) > 0


def test_budget_and_strategy_validation(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    for bad in ([1, 1], [4], [1.5], [True], [0]):
        with pytest.raises(ValueError):
            run_bank_tournament(bank, budgets=bad)
    class _Duplicate(TournamentStrategy):
        strategy_id = "duplicate"
        strategy_version = 1

        def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
            return StrategyChoice(kind="stop", reason="noop")

    class _DuplicateB(_Duplicate):
        strategy_id = "duplicate"
        strategy_version = 1

    with pytest.raises(ValueError):
        run_bank_tournament(
            bank,
            budgets=[1],
            strategies=[_Duplicate(), _DuplicateB()],
        )


def test_stop_quality_invalid_strategy_not_rewarded(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    trace = run_policy_trace(bank, _InvalidSelectingStrategy(), 3)
    assert trace.stop_reason == STOP_INVALID_STRATEGY
    pool = _pool_metadata(bank)
    vector = evaluate_trace(bank, trace, pool, (), (True, "test"))
    assert float(vector.metrics["stop_quality"].raw["saved_cost"]) == 0.0


def test_snapshot_mutation_detected(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    trace = run_policy_trace(bank, _MutatingSnapshotStrategy(), 3)
    assert trace.stop_reason == STOP_INVALID_STRATEGY
    assert "mutated its snapshot" in trace.stop_detail


def test_stateful_strategy_clone_no_cross_trace_leak(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    strategy = _StatefulStrategy()
    trace_one = run_policy_trace(bank, strategy, 1)
    assert len(strategy.seen) == 0
    assert trace_one.selected_order == ("route_01",)
    trace_three = run_policy_trace(bank, strategy, 3)
    assert len(strategy.seen) == 0
    assert len(trace_three.selected_order) == 3


def test_pareto_and_composite_determinism_and_weights(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    result_a = run_bank_tournament(bank, budgets=[1, 2, 3])
    result_b = run_bank_tournament(bank, budgets=[1, 2, 3])
    assert _canonical(result_a.model_dump(mode="json")) == _canonical(
        result_b.model_dump(mode="json")
    )
    assert abs(sum(COMPOSITE_WEIGHTS.values()) - 1.0) < 1e-9
    for vector in (
        item
        for policy in result_a.policies
        for item in policy.metric_vectors
    ):
        if vector.composite_score is None:
            continue
        applied = vector.composite_weights_applied
        assert abs(sum(applied.values()) - 1.0) < 1e-6
        recomputed = sum(
            applied[name] * float(vector.metrics[name].normalized)
            for name in applied
        )
        assert abs(recomputed - vector.composite_score) < 1e-6


def test_validate_tournament_result_and_writer(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    result = run_bank_tournament(bank, budgets=[1, 2, 3])
    tournament = TournamentResult(
        evaluator_contract_version="article-tournament-evaluator.v2",
        banks=(result,),
    )
    tournament = tournament.model_copy(
        update={"result_id": compute_tournament_result_id(tournament)}
    )
    assert validate_tournament_result(tournament)
    out = tmp_path / "out"
    path = write_tournament_result(tournament, out)
    assert path.name == "ARTICLE_TOURNAMENT_RESULT.json"
    write_tournament_result(tournament, out)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(TournamentIntegrityError):
        write_tournament_result(tournament, out)

    stale = tournament.model_copy(update={"result_id": "0" * 64})
    errors: list[str] = []
    assert not validate_tournament_result(stale, errors=errors)
    assert any("result_id" in item for item in errors)
    with pytest.raises(TournamentIntegrityError):
        write_tournament_result(stale, out.parent / "stale_out")


def test_validate_tournament_result_rejects_tampered_pareto_and_ledger(
    tmp_path,
) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    result = run_bank_tournament(bank, budgets=[1])
    tournament = TournamentResult(
        evaluator_contract_version="article-tournament-evaluator.v2",
        banks=(result,),
    )
    tournament = tournament.model_copy(
        update={"result_id": compute_tournament_result_id(tournament)}
    )
    policy = tournament.banks[0].policies[0]
    vector = policy.metric_vectors[0]
    forged_vector = vector.model_copy(
        update={"pareto_member": not vector.pareto_member}
    )
    forged_policy = policy.model_copy(
        update={"metric_vectors": (forged_vector,)}
    )
    forged_bank = tournament.banks[0].model_copy(
        update={"policies": (forged_policy,)}
    )
    forged = tournament.model_copy(
        update={"banks": (forged_bank,)}
    )
    forged = forged.model_copy(
        update={"result_id": compute_tournament_result_id(forged)}
    )
    errors: list[str] = []
    assert not validate_tournament_result(forged, errors=errors)
    assert any("pareto_member" in item for item in errors)

    forged_ledger = tournament.model_copy(
        update={
            "banks": (
                tournament.banks[0].model_copy(
                    update={"audit_ledger": ()}
                ),
            )
        }
    )
    forged_ledger = forged_ledger.model_copy(
        update={"result_id": compute_tournament_result_id(forged_ledger)}
    )
    errors = []
    assert not validate_tournament_result(forged_ledger, errors=errors)
    assert any("audit" in item.lower() for item in errors)


def test_dynamic_limitations_and_real_tournament_writes(tmp_path) -> None:
    result = run_tournament(REAL_BANKS)
    assert len(result.result_id) == 64
    assert any("2 trace bank" in item for item in result.limitations)
    assert any("[3, 3]" in item for item in result.limitations)
    out = tmp_path / "real_out"
    write_tournament_result(result, out)
    assert validate_tournament_result(
        json.loads(
            (out / "ARTICLE_TOURNAMENT_RESULT.json").read_text(encoding="utf-8")
        )
    )


class _NestedMutatingStrategy(TournamentStrategy):
    strategy_id = "nested_mutator"
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        if snapshot.selected_order:
            outcome = snapshot.revealed_by_route().get(
                snapshot.selected_order[-1]
            )
            if outcome is not None:
                outcome.budget_usage["__probe__"] = 1
                outcome.selected_roles["__probe__"] = "x"
                for candidate in outcome.candidates:
                    pass  # candidate records are frozen
            snapshot.next_decision_state["__probe__"] = {"nested": True}
        for route in snapshot.public_pool:
            if route.route_id not in snapshot.selected():
                return StrategyChoice(
                    kind="select",
                    route_id=route.route_id,
                    reason="select then mutate",
                )
        return StrategyChoice(kind="stop", reason="exhausted")


def test_nested_snapshot_mutation_detected_without_bank_corruption(
    tmp_path,
) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path, seed="nested"))
    before = {
        route_id: (
            _canonical_json(outcome.model_dump(mode="json")),
            outcome.outcome_hash,
        )
        for route_id, outcome in bank.hidden_outcomes.items()
    }
    trace = run_policy_trace(bank, _NestedMutatingStrategy(), 3)
    assert trace.stop_reason == STOP_INVALID_STRATEGY
    assert "mutated its snapshot" in trace.stop_detail
    after = {
        route_id: (
            _canonical_json(outcome.model_dump(mode="json")),
            outcome.outcome_hash,
        )
        for route_id, outcome in bank.hidden_outcomes.items()
    }
    assert before == after
    assert "__probe__" not in bank.hidden_outcomes["route_01"].budget_usage


class _NonFiniteStateStrategy(TournamentStrategy):
    strategy_id = "non_finite_state"
    strategy_version = 1

    def select(self, snapshot: StrategySnapshot) -> StrategyChoice:
        return StrategyChoice(
            kind="select",
            route_id="route_01",
            reason="bad",
            next_state={"value": float("nan")},
        )


def test_non_finite_choice_state_rejected(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    trace = run_policy_trace(bank, _NonFiniteStateStrategy(), 3)
    assert trace.stop_reason == STOP_INVALID_STRATEGY
    assert "non-finite" in trace.stop_detail


def _wrap_result(bank_result: Any) -> TournamentResult:
    tournament = TournamentResult(
        evaluator_contract_version="article-tournament-evaluator.v2",
        banks=(bank_result,),
    )
    return tournament.model_copy(
        update={"result_id": compute_tournament_result_id(tournament)}
    )


def test_leader_forged_coverage_metric_rejected(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    bank_result = run_bank_tournament(bank, budgets=[1])
    policy = bank_result.policies[0]
    vector = policy.metric_vectors[0]
    forged_metrics = dict(vector.metrics)
    forged_metrics["coverage"] = forged_metrics["coverage"].model_copy(
        update={"raw": {"forged": True}}
    )
    forged_vector = _rehash_vector(
        vector.model_copy(update={"metrics": forged_metrics})
    )
    forged_policy = policy.model_copy(
        update={"metric_vectors": (forged_vector,)}
    )
    forged_bank = bank_result.model_copy(
        update={"policies": (forged_policy,)}
    )
    forged = _wrap_result(forged_bank)
    errors: list[str] = []
    assert not validate_tournament_result(forged, errors=errors)
    assert any("metric vector does not match" in item for item in errors)


def test_forged_composite_rejected(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    bank_result = run_bank_tournament(bank, budgets=[1])
    policy = bank_result.policies[0]
    vector = policy.metric_vectors[0]
    forged_vector = _rehash_vector(
        vector.model_copy(update={"composite_score": 0.999})
    )
    forged_policy = policy.model_copy(
        update={"metric_vectors": (forged_vector,)}
    )
    forged_bank = bank_result.model_copy(
        update={"policies": (forged_policy,)}
    )
    errors: list[str] = []
    assert not validate_tournament_result(_wrap_result(forged_bank), errors=errors)
    assert any("metric vector does not match" in item for item in errors)


def test_forged_outcome_inventory_rejected(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    bank_result = run_bank_tournament(bank, budgets=[1])
    inventory = dict(bank_result.outcome_inventory)
    forged_outcome = inventory["route_01"].model_copy(
        update={"best_target_score": 0.999}
    )
    forged_outcome = forged_outcome.model_copy(
        update={
            "outcome_hash": hashlib.sha256(
                _canonical_json(
                    forged_outcome.model_dump(
                        exclude={"outcome_hash"}, mode="json"
                    )
                ).encode("utf-8")
            ).hexdigest()
        }
    )
    inventory["route_01"] = forged_outcome
    forged_bank = bank_result.model_copy(
        update={"outcome_inventory": inventory}
    )
    errors: list[str] = []
    assert not validate_tournament_result(_wrap_result(forged_bank), errors=errors)
    assert any(
        "inventory" in item or "does not match" in item
        for item in errors
    )


def test_wrong_not_run_route_or_hash_rejected(tmp_path) -> None:
    bank = load_trace_bank(REAL_BANKS[0])
    bank_result = run_bank_tournament(bank, budgets=[1])
    not_run = list(bank_result.planned_not_run)
    assert not_run
    forged_route = not_run[0].model_copy(update={"route_id": "route_99"})
    forged_bank = bank_result.model_copy(
        update={
            "planned_not_run": (forged_route,),
            "audit_ledger": tuple(
                entry
                for entry in bank_result.audit_ledger
                if entry.status != "not_run"
            ),
        }
    )
    errors: list[str] = []
    assert not validate_tournament_result(_wrap_result(forged_bank), errors=errors)
    assert any("not_run" in item for item in errors)


def test_duplicate_ledger_row_rejected(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    bank_result = run_bank_tournament(bank, budgets=[1])
    ledger = list(bank_result.audit_ledger)
    forged_bank = bank_result.model_copy(
        update={"audit_ledger": tuple(ledger + [ledger[0]])}
    )
    errors: list[str] = []
    assert not validate_tournament_result(_wrap_result(forged_bank), errors=errors)
    assert any("audit ledger" in item for item in errors)


def test_trace_vector_identity_mismatch_rejected(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    bank_result = run_bank_tournament(bank, budgets=[1])
    policy = bank_result.policies[0]
    vector = policy.metric_vectors[0]
    forged_vector = _rehash_vector(
        vector.model_copy(update={"strategy_id": "other_strategy"})
    )
    forged_policy = policy.model_copy(
        update={"metric_vectors": (forged_vector,)}
    )
    forged_bank = bank_result.model_copy(
        update={"policies": (forged_policy,)}
    )
    errors: list[str] = []
    assert not validate_tournament_result(_wrap_result(forged_bank), errors=errors)
    assert any("identity" in item for item in errors)


def test_real_bank_validation_with_trace_dirs(tmp_path) -> None:
    result = run_tournament(REAL_BANKS)
    errors: list[str] = []
    assert validate_tournament_result(
        result,
        trace_dirs=REAL_BANKS,
        errors=errors,
    )
    assert errors == []
    out = tmp_path / "real_validated"
    write_tournament_result(result, out, trace_dirs=REAL_BANKS)


def _manual_trace(
    bank: Any,
    order: list[str],
    *,
    stop_reason: str,
) -> RouteTrace:
    return RouteTrace(
        trace_id=bank.trace_id,
        strategy_id="manual",
        strategy_version=1,
        route_budget=3,
        selected_order=tuple(order),
        revealed=tuple(bank.hidden_outcomes[route] for route in order),
        stop_reason=stop_reason,
        stop_detail="manual trace",
        next_decision_state={},
        trace_hash="",
    )


def _manual_evaluation(bank: Any, trace: RouteTrace) -> Any:
    pool = _pool_metadata(bank)
    ledger = _build_trace_ledger(bank, trace)
    return evaluate_trace(
        bank,
        trace,
        pool,
        ledger,
        (True, "test"),
    )


def test_reversed_order_full_budget_identical_stop_quality(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    forward = _manual_trace(
        bank,
        ["route_01", "route_02", "route_03"],
        stop_reason="budget_exhausted",
    )
    reversed_order = _manual_trace(
        bank,
        ["route_03", "route_02", "route_01"],
        stop_reason="budget_exhausted",
    )
    vector_a = _manual_evaluation(bank, forward)
    vector_b = _manual_evaluation(bank, reversed_order)
    assert (
        vector_a.metrics["stop_quality"].normalized
        == vector_b.metrics["stop_quality"].normalized
    )
    scoring_keys = {"saved_cost", "oracle_regret", "diminishing_return_quality"}
    raw_a = vector_a.metrics["stop_quality"].raw
    raw_b = vector_b.metrics["stop_quality"].raw
    assert {
        key: raw_a[key] for key in scoring_keys
    } == {
        key: raw_b[key] for key in scoring_keys
    }
    assert vector_a.composite_score == vector_b.composite_score
    metrics_a = {
        key: value
        for key, value in vector_a.metrics.items()
        if key != "stop_quality"
    }
    metrics_b = {
        key: value
        for key, value in vector_b.metrics.items()
        if key != "stop_quality"
    }
    assert metrics_a == metrics_b


def test_oracle_early_vs_late_equal_stop_quality(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    oracle_last = _manual_trace(
        bank,
        ["route_01", "route_02", "route_03"],
        stop_reason="budget_exhausted",
    )
    oracle_first = _manual_trace(
        bank,
        ["route_03", "route_02", "route_01"],
        stop_reason="budget_exhausted",
    )
    late = _manual_evaluation(bank, oracle_last)
    early = _manual_evaluation(bank, oracle_first)
    assert (
        early.metrics["stop_quality"].normalized
        >= late.metrics["stop_quality"].normalized
    )
    assert (
        early.metrics["stop_quality"].normalized
        == late.metrics["stop_quality"].normalized
    )


def test_policy_stop_diminishing_return_evidence(tmp_path) -> None:
    bank = load_trace_bank(write_compact_bank(tmp_path))
    after_improvement = _manual_trace(
        bank,
        ["route_01", "route_03"],
        stop_reason="policy_stop",
    )
    after_plateau = _manual_trace(
        bank,
        ["route_03", "route_01"],
        stop_reason="policy_stop",
    )
    improved = _manual_evaluation(bank, after_improvement)
    plateau = _manual_evaluation(bank, after_plateau)
    improved_raw = improved.metrics["stop_quality"].raw
    plateau_raw = plateau.metrics["stop_quality"].raw
    assert plateau_raw["last_frontier_gain"] < improved_raw["last_frontier_gain"]
    assert (
        plateau_raw["diminishing_return_quality"]
        > improved_raw["diminishing_return_quality"]
    )
    assert (
        plateau.metrics["stop_quality"].normalized
        > improved.metrics["stop_quality"].normalized
    )


def test_real_broadband_budget3_order_invariant() -> None:
    result = run_tournament([REAL_BANKS[0]])
    broadband = result.banks[0]
    vectors = [
        vector
        for policy in broadband.policies
        for vector in policy.metric_vectors
        if vector.route_budget == 3
    ]
    assert len(vectors) == 4
    stop_values = {
        vector.metrics["stop_quality"].normalized for vector in vectors
    }
    composite_values = {vector.composite_score for vector in vectors}
    assert len(stop_values) == 1
    assert len(composite_values) == 1
