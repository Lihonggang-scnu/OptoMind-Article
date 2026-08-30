from __future__ import annotations

import json

from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.strategy_planner import QwenTMMStrategyPlanner
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler


class _FakeClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        self.calls.append(messages)
        return {
            "content": json.dumps(self.payloads.pop(0)),
            "_llm_usage": {"model_name": "qwen3.7-flash", "input_tokens": 10},
        }


class _ScriptedTaskCompilerClient:
    """Deterministic canned task-compiler provider for integration checks."""

    model_name = "qwen3.7-flash"

    def __init__(self) -> None:
        source = build_dev_optical_design_task("DEV04")
        payload = {
            "status": "compiled",
            "rationale": "Planar multilayer supported by TMM.",
            "normalized_request_english": source.normalized_request_english,
            "experiments": [
                item.model_dump(mode="json") for item in source.experiments
            ],
            "uncertainty": source.uncertainty.model_dump(mode="json"),
        }
        experiment = payload["experiments"][0]
        simulation = experiment["tmm_task"]["simulation"]
        simulation["spectrum"] = {
            "start_nm": 3000.0,
            "stop_nm": 13000.0,
            "points": 501,
        }
        simulation["illumination"] = {
            "angles_deg": [0.0, 30.0, 60.0],
            "polarizations": ["s", "p"],
        }
        experiment["tmm_task"]["targets"] = [
            {
                **experiment["tmm_task"]["targets"][0],
                "observable": "A",
                "target": 0.85,
                "constraint": "at_least",
                "wavelength_min_nm": 8000.0,
                "wavelength_max_nm": 13000.0,
                "angle_deg": 0.0,
                "polarization": "s",
                "name": "draft_high_abs",
            }
        ]
        experiment["objectives"] = [
            {
                "objective_id": "draft_high_abs",
                "metric": "mean_absorption",
                "sense": "maximize",
                "target": 0.85,
                "region": {"wavelength_nm": [8000.0, 13000.0]},
                "admission_role": "score_only",
            }
        ]
        self.payload = payload

    def call(
        self,
        messages,
        *,
        max_tokens=4000,
        force_mock=None,
    ):
        return {
            "content": json.dumps(self.payload),
            "_llm_usage": {
                "model_name": "qwen3.7-flash",
                "input_tokens": 1,
            },
        }


def _route(**overrides):
    value = {
        "route_id": "route_01",
        "title": "Quarter-wave starting point",
        "route_kind": "periodic_stack",
        "scientific_hypothesis": "A periodic impedance contrast can create the requested stop band.",
        "design_principle": "Start near quarter-wave optical thickness and optimize physical thicknesses.",
        "proposed_materials": ["SiO2", "TiO2"],
        "proposed_topology": "Five alternating dielectric pairs on glass.",
        "design_variables": ["all layer thicknesses"],
        "soft_objectives": ["maximize mean reflectance from 500 to 600 nm"],
        "manufacturing_considerations": ["positive bounded thicknesses"],
        "evidence_ids": ["ev_1"],
        "theory_basis": [],
        "expected_advantages": ["interpretable initialization"],
        "known_risks": ["angular blue shift"],
        "execution_request_english": "Design and optimize a five-pair TiO2/SiO2 reflector on glass from 450 to 900 nm, maximizing mean reflectance from 500 to 600 nm as a soft objective.",
        "priority": 1,
        "parent_route_id": None,
        "revision_reason": None,
    }
    value.update(overrides)
    return value


def _plan(route=None):
    return {
        "problem_id": "p1",
        "planning_summary": "Use a physically interpretable starting family.",
        "routes": [route or _route()],
        "research_influence": ["ev_1 motivated the periodic starting family."],
        "unresolved_decisions": [],
        "stop_if_all_routes_fail": "Return the best physically valid result and limitations.",
    }


def test_planner_accepts_only_traceable_evidence_ids():
    client = _FakeClient([_plan()])
    result = QwenTMMStrategyPlanner(client=client).plan(
        {"problem_id": "p1", "primary_intent": "design"},
        {"evidence": [{"evidence_id": "ev_1"}]},
    )
    assert result.status == "planned"
    assert result.plan.routes[0].evidence_ids == ("ev_1",)


def test_planner_realizes_delegated_material_choice_as_fixed_sequence():
    route = _route(
        route_kind="optimize_existing_stack",
        proposed_materials=["MgF2", "SiO2", "Ta2O5", "TiO2"],
        proposed_topology="Air | Layer1 | Layer2 | Layer3 | Layer4 | Layer5 | Layer6 | FusedSilica",
        design_variables=["material selection and thickness of six layers"],
        execution_request_english=(
            "Perform optimization for a 6-layer coating from 500-650 nm. "
            "Materials available: MgF2, SiO2, Ta2O5, TiO2. "
            "Optimize the material selection and thickness for every layer."
        ),
    )
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {
            "problem_id": "p1",
            "primary_intent": "design",
            "wavelengths_nm": [[500.0, 650.0]],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )
    assert result.status == "planned"
    realized = result.plan.routes[0]
    assert "exactly 6 finite layers" in realized.proposed_topology
    assert "TiO2 / MgF2 / TiO2 / MgF2 / TiO2 / MgF2" in realized.proposed_topology
    assert "Optimize the material selection" not in realized.execution_request_english
    assert "Do not optimize material identity" in realized.execution_request_english
    assert any("delegated material choice" in item for item in result.normalization_warnings)


def test_planner_removes_conflicting_material_sequence_instruction_after_realization():
    route = _route(
        route_kind="optimize_existing_stack",
        proposed_materials=["MgF2", "SiO2", "Ta2O5", "TiO2"],
        proposed_topology="exactly 10 finite layers: TiO2 / SiO2 repeated",
        design_variables=["material choice per layer", "physical thickness per layer"],
        execution_request_english=(
            "Use the fixed sequence TiO2 / SiO2 repeated for ten layers. "
            "Optimize the material sequence (one of MgF2, SiO2, Ta2O5, TiO2 per layer) "
            "and physical thicknesses from 25-220 nm."
        ),
    )
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {"problem_id": "p1", "wavelengths_nm": [[500.0, 650.0]]},
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    request = result.plan.routes[0].execution_request_english
    assert "Optimize the material sequence" not in request
    assert "Do not optimize material identity" in request


def test_unknown_evidence_id_triggers_one_repair():
    invalid = _plan(_route(evidence_ids=["invented"]))
    client = _FakeClient([invalid, _plan()])
    result = QwenTMMStrategyPlanner(client=client).plan(
        {"problem_id": "p1", "primary_intent": "design"},
        {"evidence": [{"evidence_id": "ev_1"}]},
    )
    assert result.status == "planned"
    assert result.attempts == 2
    assert "repair_request" in client.calls[1][1]["content"]


def test_theory_based_route_is_allowed_without_literature():
    route = _route(evidence_ids=[], theory_basis=["quarter-wave interference theory"])
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {"problem_id": "p1", "primary_intent": "design"}, {"evidence": []}
    )
    assert result.status == "planned"


def test_route_with_no_evidence_or_theory_fails_closed():
    route = _route(evidence_ids=[], theory_basis=[])
    result = QwenTMMStrategyPlanner(
        client=_FakeClient([_plan(route)]), maximum_attempts=1
    ).plan({"problem_id": "p1"}, {"evidence": []})
    assert result.status == "invalid"


def test_intermediate_plan_rejects_cjk():
    route = _route(title="中文标题")
    result = QwenTMMStrategyPlanner(
        client=_FakeClient([_plan(route)]), maximum_attempts=1
    ).plan({"problem_id": "p1"}, {"evidence": [{"evidence_id": "ev_1"}]})
    assert result.status == "invalid"


def test_planner_compacts_large_research_and_feedback_payloads():
    route = _route(evidence_ids=["ev_29"])
    client = _FakeClient([_plan(route)])
    evidence = [
        {
            "evidence_id": f"ev_{index}",
            "paper_id": f"paper_{index}",
            "title": f"Paper {index}",
            "allowed_use": "method_guidance",
            "content_depth": "fulltext",
            "text": "x" * 5000,
        }
        for index in range(30)
    ]
    result = QwenTMMStrategyPlanner(client=client).plan(
        {"problem_id": "p1", "primary_intent": "design"},
        {
            "evidence": evidence,
            "method_findings": [
                {
                    "method_name": "linked method",
                    "evidence_ids": ["ev_29"],
                }
            ],
            "telemetry": {"large_internal_payload": "y" * 10000},
        },
        prior_iterations=[
            {
                "iteration_id": "i1",
                "candidate_summaries": [
                    {"candidate_id": "c1", "objective_report": {"large": "z" * 5000}}
                ],
            }
        ],
        feedback_directives=["Preserve the verified principle."],
    )

    assert result.status == "planned"
    sent = json.loads(client.calls[0][1]["content"])
    assert len(sent["method_research"]["evidence"]) <= 16
    assert "ev_29" in {
        item["evidence_id"] for item in sent["method_research"]["evidence"]
    }
    assert max(len(item["text"]) for item in sent["method_research"]["evidence"]) <= 1200
    assert "telemetry" not in sent["method_research"]
    assert "objective_report" not in sent["prior_iterations"][0]["candidate_summaries"][0]
    assert sent["feedback_directives"] == ["Preserve the verified principle."]


def test_planner_repairs_latex_controls_and_replaces_performance_stop_gate():
    plan = _plan(
        _route(
            design_principle="Use $d = \theta/(4n)$ and $n = \text{sqrt}(n_s)$.",
        )
    )
    plan["stop_if_all_routes_fail"] = (
        "If reflectance exceeds 5%, reject the design as a failure."
    )
    result = QwenTMMStrategyPlanner(client=_FakeClient([plan])).plan(
        {"problem_id": "p1"}, {"evidence": [{"evidence_id": "ev_1"}]}
    )

    assert result.status == "planned"
    assert "\t" not in result.plan.routes[0].design_principle
    assert r"\theta" in result.plan.routes[0].design_principle
    assert "5%" not in result.plan.stop_if_all_routes_fail
    assert result.normalization_warnings


def test_planner_repairs_route_that_drops_explicit_user_constraints():
    client = _FakeClient([_plan()])
    result = QwenTMMStrategyPlanner(client=client).plan(
        {
            "problem_id": "p1",
            "polarizations": ["TE"],
            "angles_deg": [0.0],
            "wavelengths_nm": [[1200.0, 1350.0], [1550.0, 1550.0], [1750.0, 1900.0]],
            "manufacturing_constraints": ["independent plus or minus 2 percent thickness errors"],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    assert result.attempts == 1
    request = result.plan.routes[0].execution_request_english
    assert "incidence angles 0 degrees" in request
    assert "polarization TE" in request
    assert "1200-1350 nm" in request
    assert "2 percent thickness errors" in request
    assert result.normalization_warnings


def test_planner_repairs_only_unique_near_exact_evidence_id_copy_error():
    allowed = "s2-chunk:CorpusId:267006865:s2chunk:267006865:19858:20847:72742046f68f098f"
    typo = allowed.replace("46f68f098f", "46f688f098f")
    route = _route(evidence_ids=[typo])
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {"problem_id": "p1"},
        {"evidence": [{"evidence_id": allowed}]},
    )

    assert result.status == "planned"
    assert result.plan.routes[0].evidence_ids == (allowed,)
    assert any("near-exact" in item for item in result.normalization_warnings)


def test_planner_rejects_cross_route_reference_in_standalone_request():
    dependent = _route(
        execution_request_english="Optimize this stack and compare it to Route 1."
    )
    result = QwenTMMStrategyPlanner(
        client=_FakeClient([_plan(dependent)]), maximum_attempts=1
    ).plan(
        {"problem_id": "p1"},
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "invalid"
    assert "not standalone" in " ".join(result.validation_errors)


def test_planner_instantiates_pair_count_range_before_compilation():
    route = _route(
        execution_request_english=(
            "Use N_H=N_L=4-6 pairs around one cavity and optimize thicknesses."
        )
    )
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {"problem_id": "p1"},
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    request = result.plan.routes[0].execution_request_english
    assert "N_H=N_L=5 pairs" in request
    assert "4-6 pairs" not in request
    assert any("fixed topology" in item for item in result.normalization_warnings)


def test_planner_realizes_unresolved_ellipsis_topology_before_compilation():
    unresolved = _plan(
        _route(
            proposed_topology="Air | [H/L]_N | cavity | [L/H]_N | substrate",
            execution_request_english=(
                "Use Mirror-Low | ... | Mirror-High | cavity | Mirror-Low | ... | "
                "Mirror-High and optimize all physical thicknesses."
            ),
        )
    )
    client = _FakeClient([unresolved])

    result = QwenTMMStrategyPlanner(client=client).plan(
        {"problem_id": "p1"},
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    assert result.attempts == 1
    route = result.plan.routes[0]
    assert "exactly 5 alternating" in route.proposed_topology
    assert "21 finite layers" in route.proposed_topology
    assert "Topology realization override" in route.execution_request_english
    assert any("starting hypothesis" in item for item in result.normalization_warnings)


def test_planner_accepts_a_route_that_names_the_refractive_index():
    """``n`` is the refractive index, not an unresolved repeat count.

    The executability guard matched its symbolic-repeat pattern case
    insensitively, so a route that described its own dispersion -- the most
    ordinary sentence in thin-film prose -- was rejected as inexpandable and
    the replan died after exhausting its attempts.  A capital ``N`` the prose
    itself fixes is equally determinate and must survive too.
    """

    accepted = _plan(
        _route(
            proposed_topology=(
                "exactly 16 finite layers: 8 fixed Ge / SiO2 pairs on a silicon "
                "substrate. The pair count N is fixed at 8."
            ),
            execution_request_english=(
                "Optimize all 16 physical thicknesses. Dispersion is taken from "
                "tabulated n and k data for Ge and SiO2."
            ),
        )
    )
    client = _FakeClient([accepted])

    result = QwenTMMStrategyPlanner(client=client).plan(
        {"problem_id": "p1"},
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    assert result.attempts == 1
    assert not any("not executable" in item for item in result.validation_errors)


def test_planner_still_resolves_a_genuinely_unresolved_repeat_count():
    """The guard keeps its job: no unbound N reaches the compiler.

    Loosening the pattern must not let a symbolic count through. It does not:
    an unbound N is still recognized and instantiated as a fixed starting
    topology, exactly as an ellipsis is.
    """

    unresolved = _plan(
        _route(
            proposed_topology="A Bragg stack of N Ge/SiO2 pairs on silicon.",
            execution_request_english=(
                "Choose N later and optimize all physical thicknesses."
            ),
        )
    )
    client = _FakeClient([unresolved, unresolved])

    result = QwenTMMStrategyPlanner(client=client).plan(
        {"problem_id": "p1"},
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    route = result.plan.routes[0]
    assert "finite layers" in route.proposed_topology
    assert "Topology realization override" in route.execution_request_english
    assert any(
        "symbolic topology was instantiated" in item
        for item in result.normalization_warnings
    )


def test_planner_assigns_fixed_routes_across_requested_layer_count_range():
    routes = [
        _route(
            route_id=f"route_{index + 1:02d}",
            title=f"Candidate family {index + 1}",
            proposed_topology="Two-layer starting suggestion.",
            execution_request_english="Optimize a 2-layer coating.",
        )
        for index in range(3)
    ]
    plan = _plan(routes[0])
    plan["routes"] = routes
    result = QwenTMMStrategyPlanner(client=_FakeClient([plan])).plan(
        {
            "problem_id": "p1",
            "primary_intent": "design",
            "design_variables": ["layer_count (integer, 4 to 10)"],
            "known_stack_materials": ["substrate: fused-silica", "ambient: air"],
            "wavelengths_nm": [[450.0, 700.0]],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    requests = [route.execution_request_english for route in result.plan.routes]
    assert [
        int(__import__("re").search(r"exactly (\d+) finite layers", item).group(1))
        for item in requests
    ] == [4, 7, 10]
    assert all("2-layer" not in item for item in requests)
    assert all("fused-silica" in item for item in requests)


def test_planner_understands_between_layer_range_and_keeps_suppression_direction():
    routes = [
        _route(route_id=f"route_{index + 1:02d}", title=f"Family {index + 1}")
        for index in range(3)
    ]
    plan = _plan(routes[0])
    plan["routes"] = routes

    result = QwenTMMStrategyPlanner(client=_FakeClient([plan])).plan(
        {
            "problem_id": "visible_ar",
            "design_variables": ["layer count (integer between 4 and 10)"],
            "preferred_behaviors": ["mean reflectance <= 0.8 percent"],
            "suppressed_behaviors": ["high reflectance"],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    requests = [route.execution_request_english for route in result.plan.routes]
    counts = [
        int(__import__("re").search(r"exactly (\d+) finite layers", item).group(1))
        for item in requests
    ]
    assert counts == [4, 7, 10]
    assert all("behaviors to suppress or avoid: high reflectance" in item for item in requests)
    assert all("soft objectives: mean reflectance <= 0.8 percent" in item for item in requests)
    assert all("soft objectives: mean reflectance <= 0.8 percent; high reflectance" not in item for item in requests)


def test_planner_preserves_exact_explicit_targets_over_lossy_preferred():
    route = _route(
        route_kind="optimize_existing_stack",
        proposed_topology="exactly 6 finite layers",
        execution_request_english=(
            "Optimize thicknesses of a six-layer coating on fused silica."
        ),
    )
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {
            "problem_id": "broadband_ar",
            "primary_intent": "design",
            "original_request": (
                "Design a broadband one-dimensional antireflection coating for "
                "a fused-silica substrate in air over 450-700 nm. "
                "Use soft goals of mean reflectance at or below 0.8 percent "
                "and worst-case reflectance at or below 3 percent over the "
                "full wavelength-angle-polarization domain. "
                "Analyze independent one-sigma thickness errors of 2 nm "
                "together with a common incidence-angle offset bounded by "
                "plus or minus 1 degree."
            ),
            "normalized_request_english": (
                "low mean reflectance; low worst-case reflectance"
            ),
            "wavelengths_nm": [[450.0, 700.0]],
            "preferred_behaviors": [
                "low mean reflectance",
                "low worst-case reflectance",
            ],
                "suppressed_behaviors": ["high reflectance"],
                "manufacturing_constraints": [
                    "layer thickness bounds: 30 nm to 1500 nm",
                ],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )
    assert result.status == "planned"
    request = result.plan.routes[0].execution_request_english
    assert "mean reflectance at or below 0.8 percent" in request
    assert "worst-case reflectance at or below 3 percent" in request
    assert "soft objectives:" in request
    assert "low mean reflectance" in request
    assert "low worst-case reflectance" in request
    assert "behaviors to suppress or avoid: high reflectance" in request
    assert (
        "manufacturing constraints: layer thickness bounds: 30 nm to 1500 nm"
        in request
    )
    assert "uncertainty conditions:" in request
    assert (
        "independent one-sigma thickness errors of 2 nm together with a "
        "common incidence-angle offset bounded by plus or minus 1 degree"
        in request
    )


def test_planner_propagates_authoritative_uncertainty_with_separate_labels():
    route = _route(
        route_kind="optimize_existing_stack",
        proposed_topology="exactly 3 finite layers",
        execution_request_english=(
            "Optimize thicknesses of a three-layer selective emitter."
        ),
    )
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {
            "problem_id": "selective_emitter",
            "primary_intent": "design",
            "original_request": (
                "Design a selective thermal emitter. "
                "Evaluate independent normally distributed layer-thickness "
                "errors with a standard deviation of 2 percent of each "
                "nominal thickness together with a common incidence-angle "
                "offset bounded by plus or minus 2 degrees."
            ),
            "normalized_request_english": "Optimize a selective emitter.",
            "manufacturing_constraints": [
                "layer thickness bounds: 30 nm to 1500 nm"
            ],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    request = result.plan.routes[0].execution_request_english
    assert (
        "manufacturing constraints: layer thickness bounds: 30 nm to 1500 nm"
        in request
    )
    assert (
        "uncertainty conditions: Evaluate independent normally distributed "
        "layer-thickness errors with a standard deviation of 2 percent of "
        "each nominal thickness together with a common incidence-angle "
        "offset bounded by plus or minus 2 degrees"
        in request
    )
    assert "robustness conditions:" not in request


def test_planner_does_not_invent_uncertainty_when_absent():
    route = _route()
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {
            "problem_id": "p1",
            "primary_intent": "design",
            "original_request": (
                "Design a planar antireflection coating with layer thickness "
                "bounds 30-1500 nm and soft mean reflectance goals."
            ),
            "normalized_request_english": "Design an antireflection coating.",
            "manufacturing_constraints": ["layer thickness bounds: 30-1500 nm"],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    request = result.plan.routes[0].execution_request_english
    assert "uncertainty conditions:" not in request
    assert "robustness conditions:" not in request
    assert "manufacturing constraints: layer thickness bounds: 30-1500 nm" in request


def test_planner_preserves_long_005_uncertainty_sentence_and_compiles():
    question = (
        "Design an isotropic planar selective thermal emitter on an optically "
        "opaque aluminum substrate, illuminated from air. Use only locally "
        "available SiC, SiO2, Al2O3, HfO2, ZnS, and Al, and compare several "
        "fixed coating routes between 3 and 8 finite layers with each coating "
        "layer bounded between 30 and 1500 nm. Evaluate 0, 30, and 60 degrees "
        "incidence for both TE and TM polarization. Use soft goals of mean "
        "absorptance at or above 85 percent and worst-case absorptance at or "
        "above 60 percent across the 8-13 um atmospheric window, while keeping "
        "mean absorptance at or below 20 percent across 3-5 um. Also reward "
        "fewer layers and lower total coating thickness; none of these "
        "performance goals is a physics admission gate. Verify candidates with "
        "TMM, report best-performance, most-robust, simplest, and structurally "
        "distinctive physically valid designs, and evaluate independent "
        "normally distributed layer-thickness errors with a standard deviation "
        "of 2 percent of each nominal thickness together with a common "
        "incidence-angle offset bounded by plus or minus 2 degrees. If the "
        "goals conflict, preserve the best verified trade-offs and explain the "
        "limitation rather than declaring failure."
    )
    route = _route(
        route_kind="optimize_existing_stack",
        execution_request_english="Optimize a selective emitter stack.",
    )
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {
            "problem_id": "selective_emitter_005",
            "primary_intent": "design",
            "original_request": question,
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    assert result.status == "planned"
    request = result.plan.routes[0].execution_request_english
    canonical = request.split("Canonical user controls:", 1)[-1]
    assert "uncertainty conditions:" in canonical
    assert (
        "independent normally distributed layer-thickness errors with a "
        "standard deviation of 2 percent"
        in canonical
    )
    assert (
        "common incidence-angle offset bounded by plus or minus 2 degrees"
        in canonical
    )
    assert len(canonical) < 1200

    compiled = QwenTMMTaskCompiler(
        client=_ScriptedTaskCompilerClient()
    ).compile(request)
    assert compiled.status == "compiled"
    assert compiled.task.uncertainty.thickness_error_model == "relative_normal"
    assert compiled.task.uncertainty.thickness_relative_fraction == 0.02
    assert compiled.task.uncertainty.thickness_sigma_nm == 0.0
    assert compiled.task.uncertainty.angle_perturbation_deg == 2.0


def test_planner_long_sentence_without_uncertainty_yields_no_clauses():
    from optomind_optics.harness.strategy_planner import (
        _explicit_uncertainty_clauses,
    )

    long = (
        "Use only locally available SiC, SiO2, Al2O3, HfO2, ZnS, and Al, and "
        "compare several fixed coating routes between 3 and 8 finite layers "
        "with each coating layer bounded between 30 and 1500 nm, while keeping "
        "mean absorptance at or below 20 percent across 3-5 um and worst-case "
        "absorptance at or above 60 percent across 8-13 um, with fewer layers "
        "and lower total coating thickness preferred across all candidates."
    )
    assert len(long) > 260
    assert _explicit_uncertainty_clauses({"original_request": long}) == []


def test_planner_extracts_multiple_observable_explicit_targets():
    from optomind_optics.harness.strategy_planner import (
        _explicit_performance_target_clauses,
    )

    problem = {
        "original_request": (
            "Use soft goals of mean reflectance at or below 0.8 percent, "
            "worst-case reflectance at or below 3 percent, mean transmittance "
            "at least 85 percent, and absorptance no more than 5 percent."
        ),
    }
    clauses = _explicit_performance_target_clauses(problem)
    assert clauses == [
        "mean reflectance at or below 0.8 percent",
        "worst-case reflectance at or below 3 percent",
        "mean transmittance at least 85 percent",
        "absorptance no more than 5 percent",
    ]


def test_planner_ignores_unrelated_percentages_and_tolerances():
    route = _route()
    problem = {
        "problem_id": "p1",
        "primary_intent": "design",
        "original_request": (
            "Use soft goals of mean reflectance at or below 0.8 percent for a "
            "6-layer stack with a 2% manufacturing tolerance and 1.5% "
            "thickness error and a common angle offset of plus or minus 1 "
            "degree."
        ),
        "preferred_behaviors": ["low reflectance"],
    }
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        problem,
        {"evidence": [{"evidence_id": "ev_1"}]},
    )
    request = result.plan.routes[0].execution_request_english
    canonical = request.split("Canonical user controls:", 1)[-1]
    assert "mean reflectance at or below 0.8 percent" in canonical
    assert "uncertainty conditions:" in canonical
    assert "1.5% thickness error" in canonical
    assert "common angle offset of plus or minus 1 degree" in canonical
    soft_section = canonical.split("soft objectives:", 1)[-1].split(
        "uncertainty conditions:", 1
    )[0]
    assert "1.5%" not in soft_section


def test_planner_no_explicit_targets_keeps_preferred_only():
    route = _route()
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {
            "problem_id": "p1",
            "primary_intent": "design",
            "original_request": "Design a coating over 450-700 nm.",
            "preferred_behaviors": ["low mean reflectance"],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )
    request = result.plan.routes[0].execution_request_english
    assert "soft objectives: low mean reflectance" in request
    assert "0.8" not in request


def test_planner_preserves_target_after_earlier_tolerance_phrase():
    from optomind_optics.harness.strategy_planner import (
        _explicit_performance_target_clauses,
    )

    clauses = _explicit_performance_target_clauses(
        {
            "original_request": (
                "With a 2% thickness tolerance, require mean reflectance "
                "at or below 0.8 percent"
            )
        }
    )
    assert clauses == ["mean reflectance at or below 0.8 percent"]

    route = _route()
    result = QwenTMMStrategyPlanner(client=_FakeClient([_plan(route)])).plan(
        {
            "problem_id": "p1",
            "primary_intent": "design",
            "original_request": (
                "With a 2% manufacturing tolerance and a 1.5% thickness "
                "error, require mean reflectance at or below 0.8 percent"
            ),
            "preferred_behaviors": ["low reflectance"],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )
    request = result.plan.routes[0].execution_request_english
    canonical = request.split("Canonical user controls:", 1)[-1]
    assert "mean reflectance at or below 0.8 percent" in canonical
    assert "uncertainty conditions:" in canonical
    assert "2% manufacturing tolerance" in canonical
    assert "1.5% thickness error" in canonical
    soft_section = canonical.split("soft objectives:", 1)[-1].split(
        "uncertainty conditions:", 1
    )[0]
    assert "2%" not in soft_section


def test_planner_does_not_treat_uncertainty_clause_as_optical_target():
    from optomind_optics.harness.strategy_planner import (
        _explicit_performance_target_clauses,
    )

    assert (
        _explicit_performance_target_clauses(
            {
                "original_request": (
                    "reflectance tolerance at or below 2%"
                )
            }
        )
        == []
    )
    assert (
        _explicit_performance_target_clauses(
            {
                "original_request": (
                    "one-sigma thickness error of 1.5% of nominal thickness"
                )
            }
        )
        == []
    )


def test_first_three_routes_span_boundaries_when_planner_returns_four():
    routes = [
        _route(route_id=f"route_{index + 1:02d}", title=f"Family {index + 1}")
        for index in range(4)
    ]
    plan = _plan(routes[0])
    plan["routes"] = routes
    result = QwenTMMStrategyPlanner(client=_FakeClient([plan])).plan(
        {
            "problem_id": "p1",
            "design_variables": ["layer_count (integer, 4 to 10)"],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    first_three = [
        int(__import__("re").search(r"exactly (\d+) finite layers", route.execution_request_english).group(1))
        for route in result.plan.routes[:3]
    ]
    assert first_three == [4, 7, 10]


def test_fixed_layer_portfolio_removes_stale_model_layer_counts():
    route = _route(route_id="route_01", title="Six-layer bilayer proposal")
    route.update(
        {
            "scientific_hypothesis": "A six-layer bilayer can broaden the response.",
            "design_principle": "Optimize the six-layer structure.",
            "design_variables": ["Thickness of each of the 6 layers"],
            "soft_objectives": ["Minimize layer count (fixed at 6)"],
            "known_risks": ["The six layers may accumulate error."],
        }
    )
    plan = _plan(route)
    result = QwenTMMStrategyPlanner(client=_FakeClient([plan])).plan(
        {
            "problem_id": "p1",
            "design_variables": ["layer_count (integer, 4 to 10)"],
        },
        {"evidence": [{"evidence_id": "ev_1"}]},
    )

    normalized = result.plan.routes[0]
    combined = " ".join(
        [
            normalized.title,
            normalized.scientific_hypothesis,
            normalized.design_principle,
            *normalized.design_variables,
            *normalized.soft_objectives,
            *normalized.known_risks,
            normalized.execution_request_english,
        ]
    ).casefold()
    assert "4-layer" in combined
    assert "six-layer" not in combined
    assert "fixed at 6" not in combined
    assert len(normalized.design_variables) == 4
