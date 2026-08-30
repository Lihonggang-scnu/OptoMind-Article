"""T-05 tests: strategy_planner plus routing, Charter drift gate, cost metering."""

from __future__ import annotations

import json
from typing import Any

import pytest

import config.qwen_config as qwen_config
from optomind_optics.harness import problem_analyzer as pmod
from optomind_optics.harness import strategy_planner as spmod
from optomind_optics.harness.strategy_planner import (
    ARTICLE_STRATEGY_PLANNER_MODEL,
    QwenTMMStrategyPlanner,
    _ROUTE_TEXT_SEQUENCE_FIELDS,
    DesignRoute,
    _check_charter_drift,
)

FULL_CHARTER: dict[str, Any] = {
    "wavelength_range_nm": [450.0, 800.0],
    "angle_range_deg": [0.0, 30.0],
    "polarization": "unpolarized",
    "objectives": [{"type": "max_reflectivity", "target": None}],
    "material_whitelist": ["SiO2", "TiO2"],
    "layer_count_bounds": {"min": 2, "max": 8},
}


class _AttrCharter:
    """Attribute-style ResearchCharter stand-in exercising getattr access."""

    def __init__(self) -> None:
        self.wavelength_range_nm = [450.0, 800.0]
        self.angle_range_deg = [0.0, 30.0]
        self.polarization = "unpolarized"
        self.objectives = [{"type": "max_reflectivity"}]
        self.material_whitelist = ["SiO2", "TiO2"]
        self.layer_count_bounds = {"min": 2, "max": 8}


def _strategy(**route_overrides: Any) -> dict[str, Any]:
    route: dict[str, Any] = {"route_id": "route_01"}
    route.update(route_overrides)
    return {"routes": [route]}


class FakePlusClient:
    model_name = ARTICLE_STRATEGY_PLANNER_MODEL

    def __init__(self, payloads: list[dict[str, Any]]):
        self.payloads = list(payloads)
        self.calls = 0

    def call(self, messages, *, max_tokens: int = 5000, force_mock=None):
        self.calls += 1
        return {
            "content": json.dumps(self.payloads.pop(0)),
            "_llm_usage": {
                "model_name": self.model_name,
                "input_tokens": 10,
                "output_tokens": 4,
            },
        }


def _plan_payload() -> dict[str, Any]:
    return {
        "problem_id": "p1",
        "planning_summary": "Use a physically interpretable starting family.",
        "routes": [
            {
                "route_id": "route_01",
                "title": "Quarter-wave starting point",
                "route_kind": "periodic_stack",
                "scientific_hypothesis": "Periodic impedance contrast creates a stop band.",
                "design_principle": "Start near quarter-wave thickness then optimize.",
                "proposed_materials": ["SiO2", "TiO2"],
                "proposed_topology": "Five alternating dielectric pairs on glass.",
                "soft_objectives": ["maximize mean reflectance from 500 to 600 nm"],
                "evidence_ids": ["ev_1"],
                "execution_request_english": (
                    "Design a five-pair TiO2/SiO2 reflector on glass from 450 to "
                    "900 nm, maximizing mean reflectance from 500 to 600 nm."
                ),
                "priority": 1,
            }
        ],
        "research_influence": ["ev_1 motivated the periodic family."],
        "stop_if_all_routes_fail": "Return the best physically valid result.",
    }


def test_strategy_planner_uses_plus_model(monkeypatch):
    captured: dict = {}

    def fake_get_qwen_client(role):
        captured["role"] = role
        return object()

    monkeypatch.setattr(pmod, "get_qwen_client", fake_get_qwen_client)
    planner = QwenTMMStrategyPlanner()  # default construction must route plus
    assert captured["role"] == "plus"
    assert planner._model_label == ARTICLE_STRATEGY_PLANNER_MODEL


def test_charter_drift_wavelength():
    drifting = _strategy(
        wavelength_range_nm=[400.0, 900.0], proposed_materials=["SiO2"]
    )
    with pytest.raises(ValueError, match="CHARTER_DRIFT_ERROR"):
        _check_charter_drift(drifting, FULL_CHARTER)


def test_charter_drift_material():
    drifting = _strategy(proposed_materials=["SiO2", "Au"])
    with pytest.raises(ValueError, match="CHARTER_DRIFT_ERROR"):
        _check_charter_drift(drifting, FULL_CHARTER)


def test_charter_drift_layer_count():
    drifting = _strategy(layer_count=12, proposed_materials=["SiO2"])
    with pytest.raises(ValueError, match="CHARTER_DRIFT_ERROR"):
        _check_charter_drift(drifting, FULL_CHARTER)
    # legacy alias is still readable on the charter side
    legacy_charter = dict(FULL_CHARTER)
    legacy_charter.pop("layer_count_bounds")
    legacy_charter["layer_count_hard_bounds"] = {"min": 2, "max": 8}
    with pytest.raises(ValueError, match="CHARTER_DRIFT_ERROR"):
        _check_charter_drift(_strategy(layer_count=9), legacy_charter)


def test_valid_strategy_passes():
    client = FakePlusClient([_plan_payload()])
    result = QwenTMMStrategyPlanner(client=client).plan(
        {"problem_id": "p1", "primary_intent": "design"},
        {"evidence": [{"evidence_id": "ev_1"}]},
        charter=_AttrCharter(),
    )
    assert result.status == "planned"
    assert result.plan is not None
    assert not any(
        "CHARTER_DRIFT_ERROR" in item for item in result.normalization_warnings
    )
    # pre_declarations field should be present (empty dict if model didn't emit)
    assert hasattr(result, "pre_declarations")


def test_cost_recorded(monkeypatch):
    fresh_tracker = qwen_config.CostTracker()
    monkeypatch.setattr(qwen_config, "_COST_TRACKER", fresh_tracker)
    client = FakePlusClient([_plan_payload(), _plan_payload()])
    result = QwenTMMStrategyPlanner(client=client, maximum_attempts=2).plan(
        {"problem_id": "p1", "primary_intent": "design"},
        {"evidence": [{"evidence_id": "ev_1"}]},
        charter=_AttrCharter(),
    )
    assert result.status == "planned"
    # one successful attempt recorded: input 10 + output 4 tokens
    assert fresh_tracker.get_budget_snapshot().qwen_tokens.get("plus") == 14
    assert hasattr(result, "pre_declarations")


def test_route_text_sequence_fields_match_the_contract_annotations():
    """Locks _ROUTE_TEXT_SEQUENCE_FIELDS against the live DesignRoute fields.

    The shape repair is driven by a restated set (the class cannot introspect
    itself inside its own body). Adding a Tuple[str, ...] field without adding
    it there would silently leave that field unrepaired, so derive the truth
    from the annotations and compare.
    """
    from typing import get_args, get_origin

    derived = {
        name
        for name, field in DesignRoute.model_fields.items()
        if get_origin(field.annotation) is tuple and str in get_args(field.annotation)
    }
    assert derived == set(_ROUTE_TEXT_SEQUENCE_FIELDS), (
        "DesignRoute gained or lost a Tuple[str, ...] field; update "
        "_ROUTE_TEXT_SEQUENCE_FIELDS so the scalar repair still covers it. "
        f"annotations={sorted(derived)} restated={sorted(_ROUTE_TEXT_SEQUENCE_FIELDS)}"
    )


def test_scalar_written_where_an_array_belongs_is_repaired():
    """A one-item field written as a bare string must not be a hard rejection.

    This is the R-09 stage-1 failure at the contract level: the model wrote a
    single sentence instead of a one-element array and pydantic raised
    tuple_type, discarding an otherwise valid route.
    """
    route = DesignRoute.model_validate(
        {
            "route_id": "route_scalar",
            "title": "scalar shaped",
            "route_kind": "periodic_stack",
            "scientific_hypothesis": "Periodic contrast creates a stop band.",
            "design_principle": "Start near quarter-wave thickness.",
            "proposed_topology": "Five alternating dielectric pairs on glass.",
            "execution_request_english": "Design a five-pair reflector.",
            "priority": 3,
            "manufacturing_considerations": "sputtering tolerance is +/-2 nm",
            "theory_basis": "quarter-wave layers interfere coherently",
            "proposed_materials": "SiO2",
        }
    )
    assert route.manufacturing_considerations == ("sputtering tolerance is +/-2 nm",)
    assert route.theory_basis == ("quarter-wave layers interfere coherently",)
    assert route.proposed_materials == ("SiO2",)
    # Whitespace-only stays empty rather than becoming a blank "statement".
    blank = DesignRoute.model_validate(
        {
            "route_id": "route_blank",
            "title": "blank",
            "route_kind": "periodic_stack",
            "scientific_hypothesis": "h",
            "design_principle": "p",
            "proposed_topology": "t",
            "execution_request_english": "r",
            "priority": 3,
            "known_risks": "   ",
        }
    )
    assert blank.known_risks == ()


def test_replan_pre_declarations_do_not_char_split():
    """Same fail-open hazard as seeding, on the R-06 replanning path.

    pre_declarations are popped out before validation to survive
    extra="forbid", so nothing rejects a wrong shape. A bare string reaching
    list() char-split into one-character declarations that the reflection
    prompt then reflected against.
    """
    payload = _plan_payload()
    payload["routes"][0]["expected_observations"] = (
        "best_target_score should rise by about 0.05 in two rounds"
    )
    payload["routes"][0]["stop_conditions"] = (
        "stop when gains stay at or below 1e-4 across recent rounds"
    )
    client = FakePlusClient([payload])
    result = QwenTMMStrategyPlanner(client=client).plan(
        {"problem_id": "p1", "primary_intent": "design"},
        {"evidence": [{"evidence_id": "ev_1"}]},
        charter=_AttrCharter(),
    )
    assert result.status == "planned"
    declarations = result.pre_declarations["route_01"]
    assert declarations["expected_observations"] == [
        "best_target_score should rise by about 0.05 in two rounds"
    ]
    assert declarations["stop_conditions"] == [
        "stop when gains stay at or below 1e-4 across recent rounds"
    ]
    for key in ("expected_observations", "stop_conditions"):
        entries = declarations[key]
        assert len(entries) == 1, (key, entries)
        assert len(entries[0]) > 1, "declaration was split per character"
