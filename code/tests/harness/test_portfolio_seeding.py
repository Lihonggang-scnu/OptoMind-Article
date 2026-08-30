"""R-05: dual-source portfolio seeding. All tests use fake clients."""

from __future__ import annotations

import json
from unittest.mock import Mock

from optomind_optics.harness.portfolio_seeding import (
    PORTFOLIO_SEEDING_MODEL,
    seed_portfolio,
)
from optomind_optics.harness.strategy_planner import DesignRoute

ALLOWED_EVIDENCE = ["ev_1", "ev_2", "ev_3"]


def _problem():
    return {
        "problem_id": "p_seed",
        "primary_intent": "design",
        "normalized_request_english": (
            "Design a broadband reflector from 500 to 600 nm."
        ),
        "compatibility": "compatible",
    }


def _research():
    return {
        "status": "completed",
        "evidence": [
            {"evidence_id": "ev_" + str(i), "title": "Evidence " + str(i), "text": "..."}
            for i in (1, 2, 3)
        ],
        "method_findings": [
            {"finding_id": "f1", "finding": "Alumina stacks show high reflectance."}
        ],
    }


def _route(rid, *, priority=1, request=None, evidence=("ev_1",), theory=(), kind="periodic_stack"):
    return {
        "route_id": rid,
        "title": "Route " + rid.replace("_", " "),
        "route_kind": kind,
        "scientific_hypothesis": "Hypothesis statement for route " + rid + ".",
        "design_principle": "Design principle for route " + rid + ".",
        "proposed_materials": ["SiO2", "TiO2"],
        "proposed_topology": "Alternating quarter-wave dielectric pairs on glass.",
        "design_variables": ["Physical thickness of SiO2 layer 1"],
        "soft_objectives": ["maximize mean reflectance from 500 to 600 nm"],
        "evidence_ids": list(evidence),
        "theory_basis": list(theory),
        "execution_request_english": request
        or ("Simulate and optimize reflector variant " + rid + " across 450 to 900 nm."),
        "priority": priority,
        "expected_observations": [
            "best_target_score should increase over the first two rounds"
        ],
        "stop_conditions": [
            "Stop when best_target_score gains stay below the reference epsilon for two rounds"
        ],
    }


class SeedClient:
    model_name = PORTFOLIO_SEEDING_MODEL

    def __init__(self, payload, *, spelling="input_tokens"):
        self.payload = payload
        self.spelling = spelling
        self.calls = 0

    def call(self, messages, *, max_tokens=8000, force_mock=None):
        self.calls += 1
        usage = {"model_name": self.model_name}
        if self.spelling == "dashscope":
            usage.update({"prompt_tokens": 12, "completion_tokens": 5})
        else:
            usage.update({"input_tokens": 12, "output_tokens": 5})
        return {"content": json.dumps(self.payload), "_llm_usage": usage}


def _payload(evidence_routes, experience_routes):
    return {
        "evidence_derived_routes": evidence_routes,
        "experience_derived_routes": experience_routes,
    }


def _two_source_payload():
    return _payload(
        [
            _route("r_e1"),
            _route("r_e2", evidence=("ev_2",), request=(
                "Optimize an eight-pair TiO2 SiO2 chirped stack on fused silica."
            )),
        ],
        [
            _route(
                "r_x1",
                evidence=[],
                theory=(
                    "At quarter-wave optical thickness, reflections from successive "
                    "interfaces interfere coherently and widen the stop band."
                ),
                kind="chirped_stack",
            ),
            _route(
                "r_x2",
                evidence=[],
                theory=(
                    "A symmetric defect cavity between two dielectric mirrors "
                    "produces a narrow transmission resonance."
                ),
                kind="defect_cavity",
                priority=4,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# 1-3: source production and fill patterns
# ---------------------------------------------------------------------------


def test_two_sources_both_produced():
    client = SeedClient(_two_source_payload())
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=client,
        max_routes=5,
        force_mock=True,
    )
    assert set(seeded.sources.values()) == {"evidence_derived", "experience_derived"}
    assert len(seeded.sidecar["evidence_derived"]) == 2
    assert len(seeded.sidecar["experience_derived"]) == 2
    assert seeded.sidecar["insufficient"] is False
    # Exactly ONE LLM call -- no hidden retries.
    assert client.calls == 1


def test_evidence_derived_have_evidence_ids():
    client = SeedClient(_two_source_payload())
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=client,
        max_routes=5,
        force_mock=True,
    )
    for raw in seeded.routes:
        if seeded.sources[str(raw["route_id"])] != "evidence_derived":
            continue
        ids = tuple(str(v) for v in raw["evidence_ids"])
        assert ids, "evidence-derived route must cite evidence"
        assert set(ids) <= set(ALLOWED_EVIDENCE)


def test_experience_derived_have_theory_basis():
    client = SeedClient(_two_source_payload())
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=client,
        max_routes=5,
        force_mock=True,
    )
    for raw in seeded.routes:
        if seeded.sources[str(raw["route_id"])] != "experience_derived":
            continue
        assert tuple(raw["theory_basis"]), "experience-derived route needs theory"
        assert tuple(raw["evidence_ids"]) == (), "experience route keeps evidence empty"


# ---------------------------------------------------------------------------
# 4-5: deduplication semantics
# ---------------------------------------------------------------------------


def test_duplicate_across_sources_keeps_evidence_derived():
    same_request = "Optimize a ten-pair Ta2O5 SiO2 stack on fused silica."
    payload = _payload(
        [_route("dup_e", request=same_request)],
        [
            _route(
                "dup_x",
                evidence=[],
                theory=("Concrete physical principle statement.",),
                request=same_request,
            )
        ],
    )
    client = SeedClient(payload)
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=client,
        max_routes=5,
        force_mock=True,
    )
    assert seeded.sources.get("dup_e") == "evidence_derived"
    assert "dup_x" not in seeded.sources
    events = seeded.sidecar["deduplicated"]
    match = [
        e
        for e in events
        if e["dropped_route_id"] == "dup_x" and e["kept_route_id"] == "dup_e"
    ]
    assert match, "hash conflict must be recorded with kept/dropped ids"


def test_dedup_reuses_route_hash(monkeypatch):
    import optomind_optics.harness.research_orchestrator as ro_module

    calls = {"count": 0}
    real_hash = ro_module._route_hash

    def spy(route):
        calls["count"] += 1
        return real_hash(route)

    monkeypatch.setattr(ro_module, "_route_hash", spy)

    req_a = "Optimize   a TEN-pair stack on fused silica."
    req_b = "optimize a ten-pair STACK on fused silica."
    payload = _payload(
        [_route("case_e", request=req_a)],
        [
            _route(
                "case_x",
                evidence=[],
                theory=("Concrete physical principle statement.",),
                request=req_b,
            )
        ],
    )
    client = SeedClient(payload)
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=client,
        max_routes=5,
        force_mock=True,
    )
    assert calls["count"] >= 2, "dedup must go through orchestrator._route_hash"
    # Case and whitespace differences collapse under _route_hash normalization.
    assert seeded.sources.get("case_e") == "evidence_derived"
    assert "case_x" not in seeded.sources


# ---------------------------------------------------------------------------
# 6-7: cap and floor
# ---------------------------------------------------------------------------


def test_truncation_respects_max_routes():
    priorities = [3, 1, 4, 1, 5, 9, 2, 6]
    routes = [
        _route("r_" + chr(ord("a") + i), priority=priorities[i])
        for i in range(8)
    ]
    client = SeedClient(_payload(routes, []))
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=client,
        max_routes=5,
        force_mock=True,
    )
    assert seeded.sidecar["selected"] == ["r_b", "r_d", "r_g", "r_a", "r_c"]
    assert seeded.sidecar["truncated"] == ["r_e", "r_h", "r_f"]
    assert len(seeded.routes) == 5
    ordered_priorities = [int(r["priority"]) for r in seeded.routes]
    assert ordered_priorities == sorted(ordered_priorities)


def test_insufficient_flagged_when_under_two():
    payload = _payload(
        [],
        [
            _route(
                "only_one",
                evidence=[],
                theory=("Concrete physical principle statement.",),
            )
        ],
    )
    client = SeedClient(payload)
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=client,
        max_routes=5,
        force_mock=True,
    )
    assert seeded.insufficient is True
    assert seeded.sidecar["insufficient"] is True
    # The floor guard must NOT auto-retry the LLM.
    assert client.calls == 1


# ---------------------------------------------------------------------------
# 8: sidecar written through the harness
# ---------------------------------------------------------------------------


def test_seeding_sidecar_written(tmp_path):
    from optomind_optics.harness import (
        EngineMode,
        HarnessBudgetPolicy,
        OpticalDesignTask,
        TMMExperimentSpec,
    )
    from optomind_optics.harness.research_orchestrator import (
        TMMResearchHarness,
        TMMResearchHarnessConfig,
    )
    from optomind_optics.harness.strategy_planner import QwenTMMStrategyPlanner
    from tmm_engine import LayerSpec, MediumSpec, SimulationTask, SpectralGrid, StackSpec
    from tmm_engine.schemas import dataclass_to_dict

    class _Analyzer:
        def analyze(self, question, force_mock=None):
            return {
                "status": "completed",
                "analysis": dict(_problem()),
                "usage": [],
            }

    class _Researcher:
        def research(self, problem, **kwargs):
            return {"status": "completed", "report": _research()}

    class _UnusedPlannerClient:
        def call(self, messages, *, max_tokens=4000, force_mock=None):
            raise AssertionError(
                "strategy planner must not be consulted while seeding is enabled"
            )

    class _StubSeeder:
        def __init__(self, prepared):
            self.prepared = prepared

        def seed(self, **kwargs):
            return self.prepared

    simulation = SimulationTask(
        stack=StackSpec(
            layers=(LayerSpec(material="alumina", provider="rii", thickness_nm=100.0),),
            incident=MediumSpec(constant_n=1.0),
            exit=MediumSpec(constant_n=1.5),
        ),
        spectrum=SpectralGrid(start_nm=450.0, stop_nm=700.0, points=31),
    )
    design_task = OpticalDesignTask(
        task_id="seeding_flow_task",
        user_request_original="Design a broadband reflector from 500 to 600 nm.",
        normalized_request_english="Design a broadband reflector from 500 to 600 nm.",
        experiments=(
            TMMExperimentSpec(
                experiment_id="seed_forward",
                mode=EngineMode.simulate,
                tmm_task=dataclass_to_dict(simulation),
            ),
        ),
        budget=HarnessBudgetPolicy(maximum_forward_evaluations=100),
    )

    class _StubCompilation:
        def __init__(self, task):
            self.task = task

        def model_dump(self, mode="json"):
            return {
                "status": "compiled",
                "task": None,
                "rationale": "stub compiler produced a concrete task",
                "validation_errors": [],
                "usage": [],
            }

    class _StubCompiler:
        def compile(self, question, *, benchmark=None, force_mock=None):
            return _StubCompilation(design_task)

    prepared = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=SeedClient(_two_source_payload()),
        max_routes=3,
        force_mock=True,
    )

    planner = QwenTMMStrategyPlanner(client=_UnusedPlannerClient(), maximum_attempts=1)
    config = TMMResearchHarnessConfig(
        qwen_force_mock=True,
        maximum_initial_routes=3,
        maximum_iterations=1,
        portfolio_seeding_enabled=True,
    )
    harness = TMMResearchHarness(
        work_dir=tmp_path,
        problem_analyzer=_Analyzer(),
        method_researcher=_Researcher(),
        strategy_planner=planner,
        task_compiler=_StubCompiler(),
        portfolio_seeder=_StubSeeder(prepared),
        config=config,
    )

    def mock_tmm_factory(directory, run_id):
        mock_harness = Mock()
        mock_harness.run = Mock(return_value=Mock(
            status="completed",
            experiment_results=[],
            diagnoses=[],
            budget={},
            model_dump=Mock(return_value={"status": "completed", "experiment_results": []}),
        ))
        return mock_harness

    harness.tmm_harness_factory = mock_tmm_factory
    harness.run("Design a broadband reflector from 500 to 600 nm")

    sidecar_path = tmp_path / "PORTFOLIO_SEEDING.json"
    assert sidecar_path.exists(), "seeding sidecar must be written at run root"
    data = json.loads(sidecar_path.read_text(encoding="utf-8"))
    for key in (
        "recorded_at_utc",
        "evidence_derived",
        "experience_derived",
        "deduplicated",
        "selected",
        "truncated",
        "insufficient",
    ):
        assert key in data, key
    assert data["selected"] == prepared.sidecar["selected"]

    # The sidecar path must be registered among the run's artifacts.
    assert "PORTFOLIO_SEEDING.json" in harness._artifacts


# ---------------------------------------------------------------------------
# 9-10: red-line regression guards and metering
# ---------------------------------------------------------------------------


def test_no_field_added_to_design_route():
    expected_fields = {
        "route_id",
        "title",
        "route_kind",
        "scientific_hypothesis",
        "design_principle",
        "proposed_materials",
        "proposed_topology",
        "design_variables",
        "soft_objectives",
        "manufacturing_considerations",
        "evidence_ids",
        "theory_basis",
        "expected_advantages",
        "known_risks",
        "execution_request_english",
        "priority",
        "parent_route_id",
        "revision_reason",
    }
    assert set(DesignRoute.model_fields.keys()) == expected_fields


def test_usage_metered_both_spellings(monkeypatch):
    from config.qwen_config import CostTracker
    from optomind_optics.harness import portfolio_seeding as ps_module

    for spelling in ("openai", "dashscope"):
        tracker = CostTracker()
        monkeypatch.setattr(ps_module, "get_cost_tracker", lambda t=tracker: t)
        client = SeedClient(_two_source_payload(), spelling=spelling)
        seed_portfolio(
            problem_analysis=_problem(),
            method_research=_research(),
            client=client,
            max_routes=5,
            force_mock=True,
        )
        snapshot = tracker.get_budget_snapshot()
        assert snapshot.qwen_tokens.get("plus") == 17, spelling  # 12 input + 5 output
        assert set(snapshot.qwen_tokens.keys()) <= {"plus", "turbo"}
        assert not any(
            "qwen" in key.lower() or "flash" in key.lower() for key in snapshot.qwen_tokens
        )



# ---------------------------------------------------------------------------
# 11-12: human audit (R-05 acceptance) — defects invisible to tests 1-10
# ---------------------------------------------------------------------------


def test_duplicate_route_id_across_sources_is_rejected():
    """route_id is the primary key of every downstream ledger.

    _route_hash cannot catch an id collision: it hashes
    execution_request_english, so two routes sharing an id but differing in
    request are "distinct" to dedup and identical to every dict keyed on
    route_id. Downstream, the orchestrator files routes into all_routes by
    route_id -- so the second route silently vanished from the tournament while
    inheriting the first one's pre-declarations and losing its source marker.
    """
    ev = _route(
        "route_01",
        evidence=("ev_1",),
        request="Simulate evidence-side variant across 450 to 900 nm.",
    )
    ev["expected_observations"] = ["EVIDENCE-SIDE expectation"]
    xp = _route(
        "route_01",
        evidence=[],
        theory=("A defect cavity produces a narrow transmission resonance.",),
        request="Simulate a different experience-side variant across 450 to 900 nm.",
    )
    xp["expected_observations"] = ["EXPERIENCE-SIDE expectation"]
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=SeedClient(_payload([ev], [xp])),
        max_routes=5,
        force_mock=True,
    )
    ids = [str(route["route_id"]) for route in seeded.routes]
    assert len(ids) == len(set(ids)), f"duplicate route_id survived seeding: {ids}"
    # Every surviving route must carry its own source marker and declarations.
    assert set(seeded.sources) == set(ids), (
        f"source markers do not cover the portfolio: {seeded.sources} vs {ids}")
    for route_id in ids:
        assert route_id in seeded.pre_declarations
    # First claimant (evidence-derived) keeps the id and its OWN declarations.
    assert seeded.sources["route_01"] == "evidence_derived"
    assert seeded.pre_declarations["route_01"]["expected_observations"] == [
        "EVIDENCE-SIDE expectation"
    ], "the surviving route inherited the other route's declarations"
    # The collision is disclosed, not silently dropped.
    reasons = " ".join(str(row.get("reason", "")) for row in seeded.sidecar["invalid"])
    assert "duplicate route_id" in reasons, seeded.sidecar["invalid"]
    # Only one route left -> the floor guard must fire rather than race alone.
    assert seeded.insufficient is True


def test_truncation_never_starves_a_source():
    """Priority-only truncation could delete an entire epistemic source.

    Priorities are assigned within each group independently, so a group that
    self-rates lower lost every slot -- collapsing the dual-source tournament to
    a single source while `insufficient` stayed False because the route COUNT
    was still fine.
    """
    evidence_routes = [
        _route(
            "r_e%d" % i,
            priority=1,
            evidence=("ev_1",),
            request="Evidence variant %d across 450 to 900 nm." % i,
        )
        for i in range(5)
    ]
    experience_routes = [
        _route(
            "r_x%d" % i,
            priority=9,
            evidence=[],
            theory=("Distinct physical mechanism %d." % i,),
            request="Experience variant %d across 450 to 900 nm." % i,
        )
        for i in range(2)
    ]
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=SeedClient(_payload(evidence_routes, experience_routes)),
        max_routes=5,
        force_mock=True,
    )
    assert len(seeded.routes) == 5
    assert set(seeded.sources.values()) == {
        "evidence_derived",
        "experience_derived",
    }, f"a source was starved out of the portfolio: {seeded.sources}"
    # The swap is disclosed with both ends named.
    assert seeded.sidecar["source_floor"], "slot swap was applied silently"
    event = seeded.sidecar["source_floor"][0]
    assert event["promoted_source"] == "experience_derived"
    assert event["demoted_source"] == "evidence_derived"
    # Nothing is lost or double-counted between selected and truncated.
    assert set(seeded.sidecar["selected"]) | set(seeded.sidecar["truncated"]) == {
        "r_e0", "r_e1", "r_e2", "r_e3", "r_e4", "r_x0", "r_x1",
    }
    assert not set(seeded.sidecar["selected"]) & set(seeded.sidecar["truncated"])
    assert seeded.sidecar["selected"] == [
        str(route["route_id"]) for route in seeded.routes
    ]


def _scalar_shaped_payload():
    """The exact malformation that stopped R-09 stage 1, as bare strings."""
    evidence = _route("r_e1")
    evidence["manufacturing_considerations"] = "sputtering tolerance is +/-2 nm"
    evidence["expected_observations"] = "best_target_score should rise by ~0.05"
    evidence["stop_conditions"] = "gains at or below 1e-4 over three rounds"
    experience = _route(
        "r_x1",
        evidence=[],
        kind="defect_cavity",
        request="Simulate a half-wave defect cavity across 450 to 900 nm.",
    )
    experience["theory_basis"] = (
        "at quarter-wave optical thickness reflections from successive "
        "interfaces interfere coherently"
    )
    experience["manufacturing_considerations"] = "cavity thickness control"
    experience["expected_observations"] = "valid candidate count should exceed 3"
    experience["stop_conditions"] = "stop when marginal gain stays under 1e-4"
    return _payload([evidence], [experience])


def test_scalar_list_fields_are_repaired_instead_of_rejected():
    """Locks the R-09 stage-1 fix: a one-item field written as a bare string.

    The model wrote manufacturing_considerations and theory_basis as single
    sentences rather than arrays. DesignRoute declares both Tuple[str, ...], so
    pydantic rejected every route with tuple_type and the portfolio came back
    insufficient -- two real API calls spent on a container shape, not on any
    physics. A single well-formed sentence is now admitted as one statement.
    """
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=SeedClient(_scalar_shaped_payload()),
        max_routes=5,
        force_mock=True,
    )
    assert not seeded.insufficient, seeded.sidecar["invalid"]
    assert seeded.sidecar["invalid"] == []
    assert [route["route_id"] for route in seeded.routes] == ["r_e1", "r_x1"]
    by_id = {route["route_id"]: route for route in seeded.routes}
    # Repaired into ONE statement, never char-split into letters.
    assert by_id["r_e1"]["manufacturing_considerations"] == [
        "sputtering tolerance is +/-2 nm"
    ]
    assert by_id["r_x1"]["theory_basis"] == [
        "at quarter-wave optical thickness reflections from successive "
        "interfaces interfere coherently"
    ]
    # Every repaired route still satisfies the real contract.
    for route in seeded.routes:
        DesignRoute.model_validate(dict(route))


def test_scalar_pre_declarations_do_not_char_split():
    """Locks the fail-open half of the R-09 audit.

    expected_observations and stop_conditions are popped out before DesignRoute
    validation to survive extra="forbid", so NO contract rejects a wrong shape
    here. A bare string reaching list() became dozens of one-character
    "declarations" and the route was still ADMITTED, carrying per-character
    noise as the grounding that the reflection prompt reflects against. This is
    strictly worse than the tuple fields, which at least failed loudly.
    """
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=SeedClient(_scalar_shaped_payload()),
        max_routes=5,
        force_mock=True,
    )
    declarations = seeded.pre_declarations
    assert declarations["r_e1"]["expected_observations"] == [
        "best_target_score should rise by ~0.05"
    ]
    assert declarations["r_e1"]["stop_conditions"] == [
        "gains at or below 1e-4 over three rounds"
    ]
    for route_id in ("r_e1", "r_x1"):
        for key in ("expected_observations", "stop_conditions"):
            entries = declarations[route_id][key]
            assert len(entries) == 1, (route_id, key, entries)
            assert len(entries[0]) > 1, "declaration was split per character"


def test_well_formed_arrays_pass_through_untouched():
    """The shape repair must be a no-op on payloads that were already correct.

    Guards against the fix quietly rewriting good input: every historical
    payload in this suite uses proper arrays, and none of them may change.
    """
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=SeedClient(_two_source_payload()),
        max_routes=5,
        force_mock=True,
    )
    assert not seeded.insufficient
    for route in seeded.routes:
        assert route["proposed_materials"] == ["SiO2", "TiO2"]
        assert route["design_variables"] == [
            "Physical thickness of SiO2 layer 1"
        ]
        declarations = seeded.pre_declarations[route["route_id"]]
        assert declarations["expected_observations"] == [
            "best_target_score should increase over the first two rounds"
        ]


def test_vague_scalar_theory_basis_is_still_rejected():
    """Repairing the container must not weaken the content gate.

    An empty or whitespace-only theory_basis is still a missing physics
    statement, and an experience-derived route without one must not race.
    """
    blank = _route("r_x9", evidence=[], kind="chirped_stack")
    blank["theory_basis"] = "   "
    seeded = seed_portfolio(
        problem_analysis=_problem(),
        method_research=_research(),
        client=SeedClient(_payload([], [blank])),
        max_routes=5,
        force_mock=True,
    )
    assert seeded.routes == []
    assert seeded.insufficient
    assert any(
        "theory_basis" in record["reason"] for record in seeded.sidecar["invalid"]
    ), seeded.sidecar["invalid"]
