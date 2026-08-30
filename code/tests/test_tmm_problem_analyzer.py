from __future__ import annotations

import json

import pytest

from optomind_optics.harness.problem_analyzer import (
    QwenTMMProblemAnalyzer,
    ResearchIntent,
    TMMCompatibility,
    stable_problem_id,
)


class _FakeQwenClient:
    model_name = "qwen3.7-flash"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        self.calls.append(messages)
        value = self.responses.pop(0)
        return value if isinstance(value, dict) else {"content": value, "_llm_usage": {"model_name": "qwen3.7-flash"}}


def _analysis_payload(**overrides):
    payload = {
        "problem_id": "model-id-is-replaced",
        "original_request": "model-copy-is-replaced",
        "normalized_request_english": "Analyze a known planar optical stack.",
        "primary_intent": "analyze",
        "secondary_intents": [],
        "compatibility": "compatible",
        "compatibility_reason": "The request fits planar isotropic frequency-domain TMM.",
        "wavelengths_nm": [],
        "angles_deg": [],
        "polarizations": [],
        "target_observables": ["reflectance"],
        "preferred_behaviors": [],
        "suppressed_behaviors": [],
        "known_stack_materials": [],
        "design_variables": [],
        "manufacturing_constraints": [],
        "assumptions": [],
        "ambiguities": [],
        "method_research_questions": [],
        "needs_method_research": False,
    }
    payload.update(overrides)
    return {"content": json.dumps(payload), "_llm_usage": {"model_name": "qwen3.7-flash", "input_tokens": 20}}


@pytest.mark.parametrize(
    ("intent", "query"),
    [
        ("analyze", "Analyze the known stack and report reflectance."),
        ("design", "Design a planar multilayer coating."),
        ("optimize", "Optimize the layer thicknesses."),
        ("reproduce", "Reproduce the stated thin-film method."),
        ("compare", "Compare two explicitly named planar stacks."),
        ("robustness", "Assess robustness to thickness tolerance."),
    ],
)
def test_all_research_intents_are_preserved(intent, query):
    response = _analysis_payload(
        normalized_request_english=f"{intent.capitalize()} the requested planar optical problem.",
        primary_intent=intent,
    )
    client = _FakeQwenClient([response])
    result = QwenTMMProblemAnalyzer(client=client).analyze(query)

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.primary_intent is ResearchIntent(intent)
    assert result.model_name == "qwen3.7-flash"
    assert len(client.calls) == 1


def test_scope_rejection_overrides_a_model_compatible_label():
    response = _analysis_payload(
        normalized_request_english="Analyze a planar grating.",
        compatibility="compatible",
        wavelengths_nm=[[500, 600]],
    )
    client = _FakeQwenClient([response])
    result = QwenTMMProblemAnalyzer(client=client).analyze(
        "Analyze a grating and its diffraction orders from 500-600 nm."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.compatibility is TMMCompatibility.incompatible
    assert "grating" in result.analysis.compatibility_reason


def test_multiple_bands_are_not_collapsed():
    response = _analysis_payload(
        normalized_request_english="Design separate transmission bands and a middle reflection band.",
        primary_intent="design",
        wavelengths_nm=[[500, 550], [700, 750], [600, 650]],
        preferred_behaviors=["high transmission from 500 to 550 nm", "high transmission from 700 to 750 nm"],
        suppressed_behaviors=["high reflection from 600 to 650 nm"],
        ambiguities=["Material identities and thickness bounds are not specified."],
        method_research_questions=["Which materials and bounds should be evaluated?"],
        needs_method_research=True,
    )
    result = QwenTMMProblemAnalyzer(client=_FakeQwenClient([response])).analyze(
        "Design high transmission from 500-550 nm and 700-750 nm, with high reflection from 600-650 nm."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.wavelengths_nm == [(500.0, 550.0), (700.0, 750.0), (600.0, 650.0)]
    assert len(result.analysis.preferred_behaviors) == 2
    assert len(result.analysis.suppressed_behaviors) == 1


def test_unknown_design_choices_become_ambiguities_without_invented_materials():
    response = _analysis_payload(
        normalized_request_english="Design an optical coating for low reflectance.",
        primary_intent="design",
        target_observables=["reflectance"],
        needs_method_research=False,
    )
    result = QwenTMMProblemAnalyzer(client=_FakeQwenClient([response])).analyze(
        "Design an optical coating for low reflectance."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.known_stack_materials == []
    assert result.analysis.needs_method_research is True
    assert result.analysis.ambiguities
    assert all("TiO2" not in item for item in result.analysis.known_stack_materials)


def test_unrequested_material_defaults_are_removed_without_a_repair_call():
    response = _analysis_payload(
        normalized_request_english=(
            "Design an air-to-glass multilayer filter and choose the coating materials."
        ),
        primary_intent="design",
        known_stack_materials=["incident medium: air", "substrate: glass"],
        assumptions=["Assume an air incident medium and a glass substrate."],
        method_research_questions=["Which coating materials should be evaluated?"],
        needs_method_research=True,
    )
    client = _FakeQwenClient([response])

    result = QwenTMMProblemAnalyzer(client=client).analyze(
        "Design a multilayer filter and determine reasonable materials."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.known_stack_materials == []
    assert "air" not in result.analysis.normalized_request_english.casefold()
    assert "glass" not in result.analysis.normalized_request_english.casefold()
    assert "unspecified material" in result.analysis.normalized_request_english
    assert len(client.calls) == 1
    assert any("material assumptions" in item for item in result.validation_warnings)


def test_design_request_is_not_downgraded_to_forward_analysis():
    response = _analysis_payload(
        normalized_request_english="Analyze the requested coating.",
        primary_intent="analyze",
    )
    result = QwenTMMProblemAnalyzer(client=_FakeQwenClient([response])).analyze(
        "Design a multilayer coating with unresolved materials."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.primary_intent is ResearchIntent.design
    assert "design" in result.analysis.normalized_request_english.casefold()


def test_invalid_json_gets_one_bounded_repair_call():
    client = _FakeQwenClient(
        [
            {"content": "not json", "_llm_usage": {"model_name": "qwen3.7-flash"}},
            _analysis_payload(),
        ]
    )
    result = QwenTMMProblemAnalyzer(client=client).analyze(
        "Analyze a known planar stack."
    )

    assert result.status == "analyzed"
    assert result.attempts == 2
    assert len(client.calls) == 2
    repair_payload = json.loads(client.calls[1][1]["content"])
    assert repair_payload["repair_request"]["validation_errors"]
    assert result.analysis is not None
    assert result.analysis.problem_id == stable_problem_id("Analyze a known planar stack.")


def test_redundant_primary_intent_is_normalized_without_second_model_call():
    response = _analysis_payload(
        normalized_request_english="Design and optimize a single-layer coating.",
        primary_intent="design",
        secondary_intents=["design", "optimize", "optimize"],
        design_variables=["layer thickness"],
        manufacturing_constraints=["bounded positive thickness"],
    )
    client = _FakeQwenClient([response])

    result = QwenTMMProblemAnalyzer(client=client).analyze(
        "Design and optimize a single-layer coating with bounded thickness."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.primary_intent is ResearchIntent.optimize
    assert result.analysis.secondary_intents == [ResearchIntent.design]
    assert len(client.calls) == 1
    assert any("normalized deterministically" in item for item in result.validation_warnings)


def test_unrequested_normal_incidence_is_removed_without_repair_call():
    response = _analysis_payload(
        normalized_request_english=(
            "Optimize a dielectric coating at normal incidence for low reflectance."
        ),
        primary_intent="optimize",
        angles_deg=[0.0],
        known_stack_materials=["dielectric", "glass"],
        design_variables=["coating thickness"],
        manufacturing_constraints=["bounded positive thickness"],
        assumptions=["Assume normal incidence.", "Use coating index n=1.2."],
    )
    client = _FakeQwenClient([response])

    result = QwenTMMProblemAnalyzer(client=client).analyze(
        "Optimize a coating on glass for low reflectance with bounded thickness."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.angles_deg == []
    assert "normal incidence" not in result.analysis.normalized_request_english.casefold()
    assert "1.2" not in result.model_dump_json()
    assert any("numeric assumptions" in item for item in result.validation_warnings)
    assert len(client.calls) == 1
    assert any("normal-incidence" in item for item in result.validation_warnings)


def test_hyphenated_normal_incidence_is_preserved_as_zero_degrees():
    payload = _analysis_payload(
        normalized_request_english=(
            "Optimize a dielectric filter at normal-incidence with TE polarization."
        ),
        angles_deg=[0.0],
        polarizations=["TE"],
    )
    result = QwenTMMProblemAnalyzer(client=_FakeQwenClient([payload])).analyze(
        "Optimize a dielectric filter at normal-incidence with TE polarization."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.angles_deg == [0.0]


def test_angle_uncertainty_is_not_misread_as_an_incidence_working_point():
    payload = _analysis_payload(
        normalized_request_english=(
            "Optimize a dielectric coating at 0, 30, and 45 degree incidence "
            "with a plus or minus 1 degree common angular offset."
        ),
        primary_intent="optimize",
        angles_deg=[0.0, 30.0, 45.0],
        design_variables=["layer thicknesses"],
        manufacturing_constraints=[
            "common incidence-angle offset bounded by plus or minus 1 degree"
        ],
    )
    result = QwenTMMProblemAnalyzer(client=_FakeQwenClient([payload])).analyze(
        "Optimize a coating at incidence angles of 0, 30, and 45 degrees; analyze "
        "a common incidence-angle offset bounded by plus or minus 1 degree."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.angles_deg == [0.0, 30.0, 45.0]


def test_extract_angles_handles_malformed_or_empty_angle_list_wording():
    from optomind_optics.harness.problem_analyzer import _extract_angles

    for text in ("", "degrees", "incidence degrees", "evaluate degrees", "angle  degrees"):
        assert _extract_angles(text) == []
    assert set(_extract_angles("evaluate 0, 30, and 60 degrees incidence")) == {
        0.0,
        30.0,
        60.0,
    }
    assert set(_extract_angles("incidence angles of 0, 30, and 60 degrees")) == {
        0.0,
        30.0,
        60.0,
    }


def test_malformed_angle_wording_in_source_is_analyzed_without_crash():
    response = _analysis_payload(
        normalized_request_english=(
            "Evaluate degrees incidence for both TE and TM polarization."
        ),
        primary_intent="design",
        angles_deg=[],
        polarizations=["TE", "TM"],
        design_variables=["layer thickness"],
        manufacturing_constraints=["bounded thickness"],
    )
    result = QwenTMMProblemAnalyzer(
        client=_FakeQwenClient([response]), maximum_attempts=1
    ).analyze(
        "Design a planar coating; evaluate degrees incidence for both TE and TM "
        "polarization."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.angles_deg == []


def test_explicit_rejection_of_hard_threshold_is_normalized_to_positive_soft_scoring_language():
    payload = _analysis_payload(
        normalized_request_english=(
            "Optimize TE reflectance and TM transmittance as soft goals without a hard gate."
        ),
        primary_intent="optimize",
        angles_deg=[45.0],
        polarizations=["TE", "TM"],
        preferred_behaviors=[
            "TE reflectance and TM transmittance are soft scoring preferences"
        ],
        assumptions=["No hard gate is imposed."],
    )
    result = QwenTMMProblemAnalyzer(client=_FakeQwenClient([payload, payload])).analyze(
        "At 45 degrees, optimize TE reflectance and TM transmittance as soft goals rather than imposing a hard threshold."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    encoded = result.analysis.model_dump_json(exclude={"original_request"}).casefold()
    assert "hard gate" not in encoded
    assert "hard threshold" not in encoded
    assert "soft scoring preferences" in encoded


def test_selective_thermal_emitter_grammar_keeps_only_spectral_wavelengths():
    source = (
        "Design an isotropic planar selective thermal emitter on an optically opaque "
        "aluminum substrate, illuminated from air. Use only locally available SiC, SiO2, "
        "Al2O3, HfO2, ZnS, and Al, and compare several fixed coating routes between 3 and "
        "8 finite layers with each coating layer bounded between 30 and 1500 nm. Evaluate "
        "0, 30, and 60 degrees incidence for both TE and TM polarization. Use soft goals "
        "of mean absorptance at or above 85 percent and worst-case absorptance at or above "
        "60 percent across the 8-13 micrometer atmospheric window, while keeping mean "
        "absorptance at or below 20 percent across 3-5 micrometers. None of these "
        "performance goals is a physics admission gate."
    )
    response = _analysis_payload(
        normalized_request_english=(
            "Design an isotropic planar selective thermal emitter on an opaque aluminum "
            "substrate. Compare fixed coating routes with each coating layer bounded "
            "between 30 and 1500 nm. Evaluate 0, 30, and 60 degrees incidence for TE and "
            "TM. Soft goals: mean absorptance at or above 85 percent and worst-case "
            "absorptance at or above 60 percent across 8-13 um; mean absorptance at or "
            "below 20 percent across 3-5 um. These performance goals are soft scoring "
            "preferences and are not physics admission gates."
        ),
        primary_intent="design",
        wavelengths_nm=[[8000, 13000], [3000, 5000]],
        angles_deg=[0, 30, 60],
        polarizations=["TE", "TM"],
        target_observables=["absorptance"],
        preferred_behaviors=[
            "mean absorptance at or above 85 percent",
            "worst-case absorptance at or above 60 percent",
        ],
        suppressed_behaviors=["mean absorptance at or below 20 percent across 3-5 um"],
        known_stack_materials=["SiC", "SiO2", "Al2O3", "HfO2", "ZnS", "Al"],
        design_variables=["layer thicknesses"],
        manufacturing_constraints=["coating layer bounded between 30 and 1500 nm"],
    )
    client = _FakeQwenClient([response])

    result = QwenTMMProblemAnalyzer(client=client).analyze(source)

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.wavelengths_nm == [(8000.0, 13000.0), (3000.0, 5000.0)]
    assert result.analysis.angles_deg == [0.0, 30.0, 60.0]
    assert result.analysis.manufacturing_constraints == [
        "coating layer bounded between 30 and 1500 nm"
    ]
    assert not any(
        term in item
        for item in result.validation_warnings
        for term in ("invented", "not preserved", "not explicit")
    )
    encoded = result.analysis.model_dump_json(exclude={"original_request"}).casefold()
    assert "admission gate" not in encoded
    assert "hard gate" not in encoded
    assert "soft scoring preferences" in encoded
    assert len(client.calls) == 1


def test_thickness_nm_bound_is_a_manufacturing_constraint_not_a_wavelength():
    response = _analysis_payload(
        normalized_request_english=(
            "Optimize a coating with layer thickness bounded 30-1500 nm for low "
            "reflectance across 8-13 um."
        ),
        primary_intent="optimize",
        wavelengths_nm=[[8000, 13000]],
        target_observables=["reflectance"],
        design_variables=["layer thicknesses"],
        manufacturing_constraints=["layer thickness bounded 30-1500 nm"],
    )
    result = QwenTMMProblemAnalyzer(
        client=_FakeQwenClient([response]), maximum_attempts=1
    ).analyze(
        "Optimize a coating with layer thickness bounded 30-1500 nm for low reflectance "
        "across 8-13 um."
    )

    assert result.status == "analyzed"
    assert result.analysis is not None
    assert result.analysis.wavelengths_nm == [(8000.0, 13000.0)]
    assert "30-1500 nm" in result.analysis.manufacturing_constraints[0]
    assert all(30.0 not in interval and 1500.0 not in interval for interval in result.analysis.wavelengths_nm)


def test_invented_wavelength_interval_fails_closed():
    response = _analysis_payload(
        normalized_request_english=(
            "Design a planar antireflection coating for low reflectance from 500-600 nm "
            "and a second 900-1100 nm band."
        ),
        primary_intent="design",
        wavelengths_nm=[[500, 600], [900, 1100]],
        design_variables=["layer thickness"],
        manufacturing_constraints=["bounded thickness"],
    )
    result = QwenTMMProblemAnalyzer(
        client=_FakeQwenClient([response]), maximum_attempts=1
    ).analyze(
        "Design a planar antireflection coating for low reflectance from 500-600 nm."
    )

    assert result.status == "invalid"
    assert result.analysis is None
    assert any("900" in item and "not explicit" in item for item in result.validation_warnings)


def test_invented_incidence_angle_fails_closed():
    response = _analysis_payload(
        normalized_request_english=(
            "Design a planar coating at 0, 30, 60, and 75 degrees incidence."
        ),
        primary_intent="design",
        angles_deg=[0, 30, 60, 75],
        design_variables=["layer thickness"],
        manufacturing_constraints=["bounded thickness"],
    )
    result = QwenTMMProblemAnalyzer(
        client=_FakeQwenClient([response]), maximum_attempts=1
    ).analyze(
        "Design a planar coating for low reflectance at incidence angles of 0, 30, and "
        "60 degrees."
    )

    assert result.status == "invalid"
    assert result.analysis is None
    assert any("75" in item and "not explicit" in item for item in result.validation_warnings)


def test_invented_hard_gate_fails_closed():
    response = _analysis_payload(
        normalized_request_english=(
            "Design a planar coating whose reflectance must pass a hard gate of 1 percent."
        ),
        primary_intent="design",
        target_observables=["reflectance"],
        design_variables=["layer thickness"],
        manufacturing_constraints=["bounded thickness"],
    )
    result = QwenTMMProblemAnalyzer(
        client=_FakeQwenClient([response]), maximum_attempts=1
    ).analyze("Design a planar coating for low reflectance below 1 percent.")

    assert result.status == "invalid"
    assert result.analysis is None
    assert any("hard performance gate was invented" in item for item in result.validation_warnings)
