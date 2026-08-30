from __future__ import annotations

import json
import re
from typing import Any

from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.benchmarks import BenchmarkTask
from optomind_optics.harness.scoring_standard import FixedScoreMetric, ScoringStandard
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler


class _ScriptedClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> dict[str, Any]:
        self.calls.append(messages)
        return self.responses.pop(0)


def _compiled_response() -> dict[str, Any]:
    source = build_dev_optical_design_task("DEV01")
    payload = {
        "status": "compiled",
        "rationale": "A planar single-layer coating is supported by TMM.",
        "normalized_request_english": source.normalized_request_english,
        "experiments": [item.model_dump(mode="json") for item in source.experiments],
        "uncertainty": source.uncertainty.model_dump(mode="json"),
    }
    return {
        "content": json.dumps(payload),
        "_llm_usage": {
            "model_name": "qwen3.7-flash",
            "input_tokens": 500,
            "output_tokens": 900,
        },
    }


def test_compiler_builds_fixed_safe_top_level_contract() -> None:
    client = _ScriptedClient([_compiled_response()])
    result = QwenTMMTaskCompiler(client=client).compile(
        "Design a single-layer antireflection coating on glass over 500-600 nm."
    )

    assert result.status == "compiled"
    assert result.task is not None
    assert result.task.verification.require_independent_solver is True
    assert result.task.verification.forbid_material_extrapolation is True
    assert result.task.portfolio.include_structurally_distinctive is True
    assert result.task.metadata["diversity_required"] is False
    assert result.task.budget.maximum_qwen_calls == 2
    assert result.usage[0]["model_name"] == "qwen3.7-flash"


def test_compiler_normalizes_missing_outer_envelope_only() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    payload.pop("status")
    payload.pop("rationale")
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Design a bounded planar coating."
    )

    assert result.status == "compiled"
    assert result.attempts == 1
    assert result.task is not None
    assert "normalized" in result.rationale


def test_compiler_converts_integer_count_range_to_fixed_topology_directive() -> None:
    client = _ScriptedClient([_compiled_response()])
    result = QwenTMMTaskCompiler(client=client).compile(
        "Optimize integer N (2-6) and all layer thicknesses."
    )

    assert result.status == "compiled"
    sent = json.loads(client.calls[0][1]["content"])["question"]
    assert "fix N=4" in sent
    assert "do not optimize the integer layer count" in sent


def test_compiler_retries_policy_resolvable_clarification() -> None:
    clarification = {
        "content": json.dumps(
            {
                "status": "needs_clarification",
                "rationale": "Sellmeier coefficients and Monte Carlo sensitivity analysis are not directly supported by the defined objective metrics.",
                "normalized_request_english": "",
                "experiments": [],
                "uncertainty": {},
            }
        ),
        "_llm_usage": {"model_name": "qwen3.7-flash"},
    }
    client = _ScriptedClient([clarification, _compiled_response()])

    result = QwenTMMTaskCompiler(client=client).compile(
        "Use TiO2 and SiO2, report optimized results, and analyze +/-2% errors."
    )

    assert result.status == "compiled"
    assert result.attempts == 2
    repair = json.loads(client.calls[1][1]["content"])["repair_request"]
    assert "fixed compiler policy" in " ".join(repair["validation_errors"])


def test_compiler_retries_missing_thickness_bounds_instead_of_blocking() -> None:
    clarification = {
        "content": json.dumps(
            {
                "status": "needs_clarification",
                "rationale": "Finite minimum and maximum thickness bounds were not supplied.",
                "normalized_request_english": "",
                "experiments": [],
                "uncertainty": {},
            }
        ),
        "_llm_usage": {"model_name": "qwen3.7-flash"},
    }
    client = _ScriptedClient([clarification, _compiled_response()])

    result = QwenTMMTaskCompiler(client=client).compile(
        "Design and optimize a bounded dielectric coating."
    )

    assert result.status == "compiled"
    assert result.attempts == 2


def test_compiler_rejects_incorrectly_expanded_symmetric_dbr_cavity() -> None:
    bad = _compiled_response()
    good = _compiled_response()
    for response, layer_count in ((bad, 7), (good, 13)):
        payload = json.loads(response["content"])
        experiment = payload["experiments"][0]
        simulation = (
            experiment["tmm_task"].get("simulation")
            or experiment["tmm_task"]
        )
        base = simulation["stack"]["layers"][0]
        simulation["stack"]["layers"] = [
            {**base, "label": f"layer_{index + 1}"}
            for index in range(layer_count)
        ]
        response["content"] = json.dumps(payload)
    client = _ScriptedClient([bad, good])

    result = QwenTMMTaskCompiler(client=client).compile(
        "Use 3 DBR pairs on each side of one cavity."
    )

    assert result.status == "compiled"
    assert result.attempts == 2
    repair = json.loads(client.calls[1][1]["content"])["repair_request"]
    assert "require 13 expanded layers" in " ".join(
        repair["validation_errors"]
    )


def test_compiler_enforces_declared_layer_count_even_without_pair_notation() -> None:
    bad = _compiled_response()
    good = _compiled_response()
    for response, layer_count in ((bad, 11), (good, 13)):
        payload = json.loads(response["content"])
        experiment = payload["experiments"][0]
        simulation = experiment["tmm_task"].get("simulation") or experiment["tmm_task"]
        base = simulation["stack"]["layers"][0]
        simulation["stack"]["layers"] = [
            {**base, "label": f"layer_{index + 1}"}
            for index in range(layer_count)
        ]
        response["content"] = json.dumps(payload)
    client = _ScriptedClient([bad, good])

    result = QwenTMMTaskCompiler(client=client).compile(
        "Optimize a 13-layer planar optical filter."
    )

    assert result.status == "compiled"
    assert result.attempts == 2
    repair = json.loads(client.calls[1][1]["content"])["repair_request"]
    assert "declares 13 layers" in " ".join(repair["validation_errors"])


def test_compiler_retries_false_ambiguity_about_incident_and_exit_media() -> None:
    unclear = {
        "content": json.dumps(
            {
                "status": "needs_clarification",
                "rationale": (
                    "The layer sequence may contain five interfaces and it is ambiguous "
                    "whether the incident medium or substrate counts as a finite layer."
                ),
                "normalized_request_english": "",
                "experiments": [],
                "uncertainty": {},
            }
        )
    }
    compiled = _compiled_response()
    payload = json.loads(compiled["content"])
    experiment = payload["experiments"][0]
    simulation = experiment["tmm_task"].get("simulation") or experiment["tmm_task"]
    base = simulation["stack"]["layers"][0]
    simulation["stack"]["layers"] = [
        {**base, "label": f"layer_{index + 1}"} for index in range(4)
    ]
    compiled["content"] = json.dumps(payload)
    client = _ScriptedClient([unclear, compiled])

    result = QwenTMMTaskCompiler(client=client).compile(
        "Optimize exactly 4 finite layers from the incident side: SiO2 / TiO2 / "
        "SiO2 / TiO2. Use air as incident medium and fused silica as substrate."
    )

    assert result.status == "compiled"
    assert result.attempts == 2
    repair = json.loads(client.calls[1][1]["content"])["repair_request"]
    assert "fixed compiler policy" in " ".join(repair["validation_errors"])


def test_compiler_restores_explicit_user_percentage_to_soft_target() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    target = payload["experiments"][0]["tmm_task"]["targets"][0]
    target["target"] = 0.0
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Keep reflectance at most 3 percent from 500-600 nm."
    )

    assert result.status == "compiled"
    assert result.task is not None
    restored = result.task.experiments[0].tmm_task["targets"][0]
    assert restored["target"] == 0.03
    assert result.task.metadata["target_threshold_corrections"]


def test_compiler_expands_global_mean_and_worst_case_targets_across_domain() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    experiment = payload["experiments"][0]
    simulation = experiment["tmm_task"]["simulation"]
    simulation["spectrum"] = {
        "start_nm": 450.0,
        "stop_nm": 700.0,
        "points": 251,
    }
    simulation["illumination"] = {
        "angles_deg": [0.0, 30.0, 45.0],
        "polarizations": ["s", "p"],
    }
    template = dict(experiment["tmm_task"]["targets"][0])
    experiment["tmm_task"]["targets"] = [
        {
            **template,
            "name": f"mean_{angle:g}_{pol}",
            "observable": "R",
            "constraint": "at_most",
            "aggregation": "mean",
            "target": 0.0,
            "wavelength_min_nm": 450.0,
            "wavelength_max_nm": 700.0,
            "angle_deg": angle,
            "polarization": pol,
        }
        for angle in (0.0, 30.0, 45.0)
        for pol in ("s", "p")
    ]
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Evaluate 450-700 nm at 0, 30 and 45 degrees for TE and TM. "
        "Canonical user controls: wavelength intervals 450-700 nm; "
        "soft objectives: mean reflectance at or below 0.8 percent; "
        "worst-case reflectance at or below 3 percent."
    )

    assert result.status == "compiled"
    targets = result.task.experiments[0].tmm_task["targets"]
    assert len(targets) == 12
    mean_targets = [item for item in targets if item["aggregation"] == "mean"]
    worst_targets = [item for item in targets if item["aggregation"] == "worst_case"]
    assert len(mean_targets) == len(worst_targets) == 6
    assert {item["target"] for item in mean_targets} == {0.008}
    assert {item["target"] for item in worst_targets} == {0.03}
    assert {
        (item["angle_deg"], item["polarization"]) for item in worst_targets
    } == {
        (0.0, "s"),
        (0.0, "p"),
        (30.0, "s"),
        (30.0, "p"),
        (45.0, "s"),
        (45.0, "p"),
    }
    objectives = result.task.experiments[0].objectives
    mean_objectives = [
        item for item in objectives if item.metric == "mean_reflectance"
    ]
    worst_objectives = [
        item for item in objectives if item.metric == "worst_case_reflectance"
    ]
    assert len(mean_objectives) == 6
    assert len(worst_objectives) == 6
    assert {item.target for item in mean_objectives} == {0.008}
    assert {item.target for item in worst_objectives} == {0.03}
    assert {item.sense for item in worst_objectives} == {"minimize"}
    assert {
        (item.region["angle_deg"], item.region["polarization"])
        for item in worst_objectives
    } == {
        (0.0, "s"),
        (0.0, "p"),
        (30.0, "s"),
        (30.0, "p"),
        (45.0, "s"),
        (45.0, "p"),
    }


def test_unqualified_thresholds_are_not_expanded_across_multiple_distinct_bands() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
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
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Perform a deterministic optimization of a planar selective thermal emitter. "
        "Canonical user controls: incidence angles 0 degrees, 30 degrees, 60 degrees. "
        "polarization TE, TM. wavelength intervals 3000-5000 nm, 8000-13000 nm. "
        "soft objectives: mean absorptance at or above 85 percent; worst-case "
        "absorptance at or above 60 percent; mean absorptance at or below 20 percent; "
        "high mean absorptance >= 85% in 8-13 um; high worst-case absorptance >= 60% "
        "in 8-13 um; low mean absorptance <= 20% in 3-5 um."
    )

    assert result.status == "compiled"
    assert result.attempts == 1
    experiment = result.task.experiments[0]
    targets = experiment.tmm_task["targets"]
    assert len(targets) == 18
    intervals = {
        (item["wavelength_min_nm"], item["wavelength_max_nm"]) for item in targets
    }
    assert intervals == {(3000.0, 5000.0), (8000.0, 13000.0)}
    assert (3000.0, 13000.0) not in intervals
    expected_channels = {
        (0.0, "s"),
        (0.0, "p"),
        (30.0, "s"),
        (30.0, "p"),
        (60.0, "s"),
        (60.0, "p"),
    }
    high_mean = [
        item
        for item in targets
        if item["constraint"] == "at_least"
        and item["aggregation"] == "mean"
        and item["wavelength_min_nm"] == 8000.0
        and item["wavelength_max_nm"] == 13000.0
    ]
    high_worst = [
        item
        for item in targets
        if item["constraint"] == "at_least"
        and item["aggregation"] == "worst_case"
        and item["wavelength_min_nm"] == 8000.0
        and item["wavelength_max_nm"] == 13000.0
    ]
    low_mean = [
        item
        for item in targets
        if item["constraint"] == "at_most"
        and item["aggregation"] == "mean"
        and item["wavelength_min_nm"] == 3000.0
        and item["wavelength_max_nm"] == 5000.0
    ]
    assert len(high_mean) == len(high_worst) == len(low_mean) == 6
    assert {item["target"] for item in high_mean} == {0.85}
    assert {item["target"] for item in high_worst} == {0.60}
    assert {item["target"] for item in low_mean} == {0.20}
    for group in (high_mean, high_worst, low_mean):
        assert {
            (item["angle_deg"], item["polarization"]) for item in group
        } == expected_channels
    objectives = experiment.objectives
    assert len(objectives) == 18
    assert all(
        tuple(item.region["wavelength_nm"]) != (3000.0, 13000.0)
        for item in objectives
    )
    assert any(
        "ambiguous unqualified target clause" in item
        for item in result.task.metadata["target_threshold_corrections"]
    )


def test_multi_band_unqualified_only_retains_model_draft_targets() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    experiment = payload["experiments"][0]
    simulation = experiment["tmm_task"]["simulation"]
    simulation["spectrum"] = {
        "start_nm": 500.0,
        "stop_nm": 700.0,
        "points": 201,
    }
    draft_targets = list(experiment["tmm_task"]["targets"])
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Optimize a planar coating. Canonical user controls: wavelength intervals "
        "500-600 nm, 650-700 nm. soft objectives: mean absorptance at or above 85 "
        "percent; mean absorptance at or below 20 percent."
    )

    assert result.status == "compiled"
    targets = result.task.experiments[0].tmm_task["targets"]
    assert targets == draft_targets
    assert any(
        "retained model draft targets" in item
        for item in result.task.metadata["target_threshold_corrections"]
    )


def test_compiler_restores_both_te_tm_and_all_explicit_angles() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    experiment = payload["experiments"][0]
    simulation = experiment["tmm_task"]["simulation"]
    simulation["illumination"] = {
        "angles_deg": [0.0, 30.0, 45.0],
        "polarizations": ["p"],
    }
    target = experiment["tmm_task"]["targets"][0]
    target["angle_deg"] = 0.0
    target["polarization"] = "p"
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Evaluate incidence angles 0, 30, and 45 degrees for both TE and TM "
        "polarization; robustness includes a common angle offset of plus or minus 1 degree."
    )

    assert result.status == "compiled"
    assert result.attempts == 1
    illumination = result.task.experiments[0].tmm_task["simulation"]["illumination"]
    assert illumination["angles_deg"] == [0.0, 30.0, 45.0]
    assert illumination["polarizations"] == ["s", "p"]
    assert result.task.metadata["illumination_corrections"]


def test_compiler_uses_dispersion_for_explicit_fused_silica_substrate() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    simulation = payload["experiments"][0]["tmm_task"]["simulation"]
    simulation["stack"]["exit"] = {"constant_n": 1.46, "constant_k": 0.0}
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Design an antireflection coating on a fused-silica substrate."
    )

    assert result.status == "compiled"
    exit_medium = result.task.experiments[0].tmm_task["simulation"]["stack"]["exit"]
    assert exit_medium["material"] == "sio2"
    assert "constant_n" not in exit_medium
    assert result.task.metadata["substrate_corrections"]


def test_compiler_repairs_null_numeric_draft_instead_of_raising_type_error() -> None:
    bad = _compiled_response()
    payload = json.loads(bad["content"])
    payload["experiments"][0]["tmm_task"]["targets"][0]["target"] = None
    bad["content"] = json.dumps(payload)
    client = _ScriptedClient([bad, _compiled_response()])

    result = QwenTMMTaskCompiler(client=client).compile(
        "Design a bounded antireflection coating."
    )

    assert result.status == "compiled"
    assert result.attempts == 2
    repair = json.loads(client.calls[1][1]["content"])["repair_request"]
    assert "NoneType" in " ".join(repair["validation_errors"])


def test_compiler_expands_null_target_channel_shorthand_before_validation() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    experiment = payload["experiments"][0]
    simulation = experiment["tmm_task"]["simulation"]
    simulation["illumination"] = {
        "angles_deg": [0.0, 30.0, 45.0],
        "polarizations": ["s", "p"],
    }
    target = experiment["tmm_task"]["targets"][0]
    target["angle_deg"] = None
    target["polarization"] = None
    target["name"] = "global_reflectance"
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Evaluate incidence angles 0, 30 and 45 degrees for TE and TM."
    )

    assert result.status == "compiled"
    assert result.attempts == 1
    targets = result.task.experiments[0].tmm_task["targets"]
    assert len(targets) == 6
    assert {(item["angle_deg"], item["polarization"]) for item in targets} == {
        (0.0, "s"),
        (0.0, "p"),
        (30.0, "s"),
        (30.0, "p"),
        (45.0, "s"),
        (45.0, "p"),
    }
    assert len({item["name"] for item in targets}) == 6


def test_compiler_repairs_stale_unpolarized_target_before_domain_validation() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    experiment = payload["experiments"][0]
    simulation = experiment["tmm_task"]["simulation"]
    simulation["illumination"] = {
        "angles_deg": [0.0],
        "polarizations": ["unpolarized"],
    }
    target = experiment["tmm_task"]["targets"][0]
    target["angle_deg"] = 0.0
    target["polarization"] = "unpolarized"
    experiment["objectives"][0]["region"].update(
        {"angle_deg": 0.0, "polarization": "unpolarized"}
    )
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Evaluate incidence angles 0, 30 and 45 degrees for TE and TM. "
        "Canonical user controls: wavelength intervals 500-600 nm; "
        "soft objectives: mean reflectance at or below 1 percent."
    )

    assert result.status == "compiled"
    assert result.attempts == 1
    experiment = result.task.experiments[0]
    illumination = experiment.tmm_task["simulation"]["illumination"]
    assert illumination == {
        "angles_deg": [0.0, 30.0, 45.0],
        "polarizations": ["s", "p"],
    }
    assert {
        (item["angle_deg"], item["polarization"])
        for item in experiment.tmm_task["targets"]
    } == {
        (0.0, "s"),
        (0.0, "p"),
        (30.0, "s"),
        (30.0, "p"),
        (45.0, "s"),
        (45.0, "p"),
    }


def test_compiler_returns_invalid_instead_of_raising_on_missing_stack() -> None:
    bad = _compiled_response()
    payload = json.loads(bad["content"])
    payload["experiments"][0]["tmm_task"]["simulation"].pop("stack")
    bad["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(
        client=_ScriptedClient([bad, bad])
    ).compile("Optimize a bounded planar coating.")

    assert result.status == "invalid"
    assert result.task is None
    assert result.validation_errors


def test_compiler_normalizes_observable_alias_and_report_only_objective() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    experiment = payload["experiments"][0]
    experiment["tmm_task"]["targets"][0]["observable"] = "mean_reflectance"
    experiment["objectives"].append(
        {
            "objective_id": "layer_absorption_report",
            "metric": "layer_absorption",
            "sense": "minimize",
            "weight": 1.0,
            "target": 0.0,
            "region": {"wavelength_nm": [500.0, 600.0]},
            "admission_role": "score_only",
        }
    )
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Minimize mean reflectance over 500-600 nm and report layer absorption."
    )

    assert result.status == "compiled"
    experiment = result.task.experiments[0]
    assert experiment.tmm_task["targets"][0]["observable"] == "R"
    report_objective = next(
        item for item in experiment.objectives if item.metric == "layer_absorption"
    )
    assert report_objective.sense == "report"
    assert report_objective.target is None


def test_compiler_restores_percent_symbol_threshold_and_mirror_count() -> None:
    bad = _compiled_response()
    good = _compiled_response()
    for response, layer_count in ((bad, 20), (good, 21)):
        payload = json.loads(response["content"])
        experiment = payload["experiments"][0]
        simulation = experiment["tmm_task"].get("simulation") or experiment["tmm_task"]
        base = simulation["stack"]["layers"][0]
        simulation["stack"]["layers"] = [
            {**base, "label": f"layer_{index + 1}"}
            for index in range(layer_count)
        ]
        experiment["tmm_task"]["targets"][0]["target"] = 1.0
        experiment["tmm_task"]["targets"][0]["observable"] = "R"
        experiment["tmm_task"]["targets"][0]["constraint"] = "at_least"
        experiment["tmm_task"]["targets"][0]["wavelength_min_nm"] = 540.0
        experiment["tmm_task"]["targets"][0]["wavelength_max_nm"] = 560.0
        response["content"] = json.dumps(payload)
    client = _ScriptedClient([bad, good])

    result = QwenTMMTaskCompiler(client=client).compile(
        "Use N_H=N_L=4-6 pairs around one cavity and keep reflectance at least 95% at 550 nm."
    )

    assert result.status == "compiled"
    assert result.attempts == 2
    target = result.task.experiments[0].tmm_task["targets"][0]
    assert target["target"] == 0.95
    assert len(
        result.task.experiments[0].tmm_task["simulation"]["stack"]["layers"]
    ) == 21


def test_compiler_keeps_center_and_two_stopband_percentages_separate() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    experiment = payload["experiments"][0]
    experiment["tmm_task"]["simulation"]["spectrum"] = {
        "start_nm": 1100.0,
        "stop_nm": 2000.0,
        "points": 901,
    }
    experiment["objectives"][0].update(
        {
            "metric": "mean_transmittance",
            "sense": "maximize",
            "region": {"wavelength_nm": [1540.0, 1560.0]},
        }
    )
    template = dict(experiment["tmm_task"]["targets"][0])
    experiment["tmm_task"]["targets"] = [
        {
            **template,
            "name": "center",
            "observable": "T",
            "constraint": "at_least",
            "target": 1.0,
            "wavelength_min_nm": 1540.0,
            "wavelength_max_nm": 1560.0,
        },
        {
            **template,
            "name": "stop_low",
            "observable": "T",
            "constraint": "at_most",
            "target": 0.95,
            "wavelength_min_nm": 1200.0,
            "wavelength_max_nm": 1350.0,
        },
        {
            **template,
            "name": "stop_high",
            "observable": "T",
            "constraint": "at_most",
            "target": 0.95,
            "wavelength_min_nm": 1750.0,
            "wavelength_max_nm": 1900.0,
        },
    ]
    response["content"] = json.dumps(payload)
    question = (
        "Seek transmittance at 1550 nm of at least 95 percent, while keeping "
        "transmittance at or below 5 percent in 1200-1350 nm and 1750-1900 nm. "
        "Canonical user controls: soft objectives: transmittance at 1550 nm of at "
        "least 95 percent; transmittance at or below 5 percent in 1200-1350 nm; "
        "transmittance at or below 5 percent in 1750-1900 nm."
    )

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(question)

    assert result.status == "compiled"
    targets = result.task.experiments[0].tmm_task["targets"]
    assert [item["target"] for item in targets] == [0.95, 0.05, 0.05]
    assert result.task.uncertainty.material_dataset_policy == "evaluate_all_eligible"


def test_compiler_reconstructs_missing_explicit_band_target_and_objective() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    experiment = payload["experiments"][0]
    experiment["tmm_task"]["simulation"]["spectrum"] = {
        "start_nm": 1100.0,
        "stop_nm": 2000.0,
        "points": 901,
    }
    template = dict(experiment["tmm_task"]["targets"][0])
    experiment["tmm_task"]["targets"] = [
        {
            **template,
            "name": "center",
            "observable": "T",
            "constraint": "at_least",
            "target": 0.95,
            "wavelength_min_nm": 1550.0,
            "wavelength_max_nm": 1550.0,
        },
        {
            **template,
            "name": "stop_low",
            "observable": "T",
            "constraint": "at_most",
            "target": 0.05,
            "wavelength_min_nm": 1200.0,
            "wavelength_max_nm": 1350.0,
        },
    ]
    experiment["objectives"] = experiment["objectives"][:1]
    experiment["objectives"][0].update(
        {
            "metric": "mean_transmittance",
            "sense": "maximize",
            "target": 0.95,
            "region": {"wavelength_nm": [1550.0, 1550.0]},
        }
    )
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Canonical user controls: soft objectives: transmittance at 1550 nm of at "
        "least 95 percent; transmittance at or below 5 percent in 1200-1350 nm; "
        "transmittance at or below 5 percent in 1.75-1.90 um."
    )

    assert result.status == "compiled"
    assert result.attempts == 1
    assert result.task is not None
    experiment = result.task.experiments[0]
    assert {
        (
            item["constraint"],
            item["wavelength_min_nm"],
            item["wavelength_max_nm"],
            item["target"],
        )
        for item in experiment.tmm_task["targets"]
    } == {
        ("at_least", 1550.0, 1550.0, 0.95),
        ("at_most", 1200.0, 1350.0, 0.05),
        ("at_most", 1750.0, 1900.0, 0.05),
    }
    assert {item.sense for item in experiment.objectives} == {"maximize", "minimize"}
    assert len(experiment.objectives) == 3
    assert result.task.metadata["target_threshold_corrections"]
    assert result.task.metadata["objective_synchronization"]


def test_compiler_corrects_model_inverted_target_direction_without_conflict() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    target = payload["experiments"][0]["tmm_task"]["targets"][0]
    target.update(
        {
            "observable": "R",
            "constraint": "at_least",
            "target": 0.95,
            "wavelength_min_nm": 500.0,
            "wavelength_max_nm": 600.0,
        }
    )
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Keep reflectance at most 3 percent from 500-600 nm."
    )

    assert result.status == "compiled"
    assert result.task is not None
    targets = result.task.experiments[0].tmm_task["targets"]
    assert len(targets) == 1
    assert targets[0]["constraint"] == "at_most"
    assert targets[0]["target"] == 0.03
    assert result.task.experiments[0].objectives[0].sense == "minimize"


def test_compiler_enforces_declared_finite_layer_count() -> None:
    bad = _compiled_response()
    good = _compiled_response()
    for response, layer_count in ((bad, 13), (good, 12)):
        payload = json.loads(response["content"])
        experiment = payload["experiments"][0]
        simulation = experiment["tmm_task"].get("simulation") or experiment["tmm_task"]
        base = simulation["stack"]["layers"][0]
        simulation["stack"]["layers"] = [
            {**base, "label": f"layer_{index + 1}"}
            for index in range(layer_count)
        ]
        response["content"] = json.dumps(payload)
    client = _ScriptedClient([bad, good])

    result = QwenTMMTaskCompiler(client=client).compile(
        "Use exactly 6 alternating pairs (12 finite layers) and optimize thicknesses."
    )

    assert result.status == "compiled"
    assert result.attempts == 2
    layers = result.task.experiments[0].tmm_task["simulation"]["stack"]["layers"]
    assert len(layers) == 12


def test_compiler_caps_model_invented_uncertainty_samples() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    payload["uncertainty"]["thickness_samples"] = 1000
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Assess independent plus or minus 2 percent thickness errors. "
        "Canonical user controls: robustness conditions: independent plus or minus "
        "2 percent manufacturing thickness errors."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_samples == 32
    assert result.task.uncertainty.material_dataset_policy == "evaluate_all_eligible"
    assert result.task.metadata["uncertainty_normalization"]


def test_compiler_preserves_explicit_uncertainty_sample_count() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    payload["uncertainty"]["thickness_samples"] = 100
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Assess uncertainty using 100 Monte Carlo samples."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_samples == 100


def test_compiler_makes_absolute_one_sigma_uncertainty_authoritative() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    payload["uncertainty"].update(
        {
            "thickness_sigma_nm": 2.0,
            "thickness_error_model": "relative_uniform",
            "thickness_relative_fraction": 0.02,
            "angle_perturbation_deg": 0.0,
        }
    )
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Evaluate 0, 30, and 45 degree incidence for TE and TM. "
        "Robustness conditions: independent one-sigma thickness error of 2 nm; "
        "common incidence-angle offset bounded by plus or minus 1 degree."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_error_model == "absolute_normal"
    assert result.task.uncertainty.thickness_sigma_nm == 2.0
    assert result.task.uncertainty.thickness_relative_fraction == 0.0
    assert result.task.uncertainty.angle_perturbation_deg == 1.0
    assert "relative uniform" not in result.rationale.casefold()
    assert "normalized user uncertainty contract" in result.rationale


def test_compiler_rebuilds_one_common_explicit_target_contract() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    original = payload["experiments"][0]["tmm_task"]["targets"][0]
    payload["experiments"][0]["tmm_task"]["targets"] = [
        {**original, "name": "invented", "weight": 99.0, "target": 0.55}
    ]
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Evaluate incidence angles of 0, 30, and 45 degrees for TE and TM over "
        "450-700 nm. Use soft goals of mean reflectance at or below 0.8 percent "
        "and worst-case reflectance at or below 3 percent."
    )

    assert result.status == "compiled"
    targets = result.task.experiments[0].tmm_task["targets"]
    assert len(targets) == 12
    assert {target["target"] for target in targets} == {0.008, 0.03}
    assert {target["weight"] for target in targets} == {1.0}
    assert {target["angle_deg"] for target in targets} == {0.0, 30.0, 45.0}
    assert {target["polarization"] for target in targets} == {"s", "p"}
    assert all(str(target["name"]).startswith("canonical_r_") for target in targets)


def test_compiler_keeps_layer_counts_out_of_incidence_angles() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    layer = payload["experiments"][0]["tmm_task"]["simulation"]["stack"]["layers"][0]
    payload["experiments"][0]["tmm_task"]["simulation"]["stack"]["layers"] = [
        {**layer, "name": f"layer_{index}"} for index in range(6)
    ]
    response["content"] = json.dumps(payload)
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Perform one bounded TMM optimization for an exactly 6-layer stack. "
        "Incidence angles 45 degrees. polarization s, p. wavelength intervals "
        "500-650 nm."
    )

    assert result.status == "compiled"
    illumination = result.task.experiments[0].tmm_task["simulation"]["illumination"]
    assert illumination["angles_deg"] == [45.0]


def test_compiler_preserves_selective_te_r_and_tm_t_targets() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Incidence angles 45 degrees. polarization s, p. wavelength intervals "
        "500-650 nm. soft objectives: high TE reflectance (mean >= 90% soft goal); "
        "high TM transmittance (mean >= 85% soft goal)."
    )

    assert result.status == "compiled"
    targets = result.task.experiments[0].tmm_task["targets"]
    assert {(target["observable"], target["polarization"]) for target in targets} == {
        ("R", "s"),
        ("T", "p"),
    }


def test_compiler_preserves_decimal_relative_normal_uncertainty() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Incidence angles 45 degrees. polarization s, p. wavelength intervals "
        "500-650 nm. robustness conditions: independent normally distributed "
        "layer-thickness errors with standard deviation of 1.5% of nominal "
        "thickness; common incidence-angle offset bounded by plus or minus 1 degree."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_error_model == "relative_normal"
    assert result.task.uncertainty.thickness_relative_fraction == 0.015
    assert result.task.uncertainty.angle_perturbation_deg == 1.0


def test_compiler_keeps_normal_thickness_and_bounded_angle_windows_separate() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Canonical user controls: manufacturing constraints: layer thickness "
        "bounds: 30 nm to 1500 nm. uncertainty conditions: independent "
        "normally distributed layer-thickness errors with a standard "
        "deviation of 2 percent of each nominal thickness together with a "
        "common incidence-angle offset bounded by plus or minus 2 degrees."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_error_model == "relative_normal"
    assert result.task.uncertainty.thickness_relative_fraction == 0.02
    assert result.task.uncertainty.thickness_sigma_nm == 0.0
    assert result.task.uncertainty.angle_perturbation_deg == 2.0
    assert any(
        "explicit relative thickness uncertainty" in item
        for item in result.task.metadata["uncertainty_normalization"]
    )


def test_compiler_binds_thickness_percent_to_uncertainty_not_optical_goal() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Canonical user controls: soft objectives: mean absorptance at or "
        "above 85 percent and independent normally distributed "
        "layer-thickness errors with a standard deviation of 2 percent of "
        "nominal thickness."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_error_model == "relative_normal"
    assert result.task.uncertainty.thickness_relative_fraction == 0.02
    assert result.task.uncertainty.thickness_sigma_nm == 0.0


def test_compiler_binds_thickness_nm_to_uncertainty_not_bounds() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Canonical user controls: manufacturing constraints: layer thickness "
        "bounds 30 nm to 1500 nm and independent normally distributed "
        "thickness error of 2 nm."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_error_model == "absolute_normal"
    assert result.task.uncertainty.thickness_sigma_nm == 2.0
    assert result.task.uncertainty.thickness_relative_fraction == 0.0


def test_compiler_binds_nm_over_distant_percent_when_closer_to_anchor() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Canonical user controls: soft objectives: mean absorptance at least "
        "85 percent and independent normally distributed layer-thickness "
        "error with standard deviation 2 nm."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_error_model == "absolute_normal"
    assert result.task.uncertainty.thickness_sigma_nm == 2.0
    assert result.task.uncertainty.thickness_relative_fraction == 0.0


def test_compiler_normalizes_bounded_relative_thickness_as_uniform() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Canonical user controls: uncertainty conditions: layer-thickness "
        "errors uniformly bounded by plus or minus 3 percent of nominal "
        "thickness."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_error_model == "relative_uniform"
    assert result.task.uncertainty.thickness_relative_fraction == 0.03
    assert result.task.uncertainty.thickness_sigma_nm == 0.0


def test_compiler_normalizes_absolute_nm_normal_thickness_error() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Canonical user controls: uncertainty conditions: independent "
        "normally distributed layer-thickness errors with a standard "
        "deviation of 2 nm."
    )

    assert result.status == "compiled"
    assert result.task.uncertainty.thickness_error_model == "absolute_normal"
    assert result.task.uncertainty.thickness_sigma_nm == 2.0
    assert result.task.uncertainty.thickness_relative_fraction == 0.0


def test_compiler_unwraps_single_result_envelope() -> None:
    response = _compiled_response()
    response["content"] = json.dumps({"result": json.loads(response["content"])})

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Compile this bounded coating task."
    )

    assert result.status == "compiled"
    assert result.attempts == 1


def test_compiler_does_not_treat_thickness_bounds_as_spectral_band() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Optimize high TE reflectance and high TM transmittance over 500-650 nm. "
        "Constrain every layer thickness to 25-220 nm."
    )

    assert result.status == "compiled"


def test_compiler_retries_from_scratch_when_the_response_did_not_parse() -> None:
    """An unparseable response leaves nothing to repair.

    Truncation is the common cause -- the object never closes -- and it is
    indistinguishable from garbage at the parse boundary. Handing the model an
    empty ``existing_draft`` and asking it to "repair only the listed defects"
    reliably reproduces the same empty output, so the retry has to restate the
    original request instead.
    """

    client = _ScriptedClient(
        [
            {"content": "not json", "_llm_usage": {"model_name": "qwen3.7-flash"}},
            _compiled_response(),
        ]
    )
    result = QwenTMMTaskCompiler(client=client).compile("Compile this bounded coating task.")

    assert result.status == "compiled"
    assert result.attempts == 2
    repair_payload = json.loads(client.calls[1][1]["content"])
    assert repair_payload["repair_request"]["validation_errors"]
    assert "existing_draft" not in repair_payload
    assert repair_payload["question"].startswith("Compile this bounded coating task.")
    # The raw previous response is still never echoed back.
    assert "previous_response" not in repair_payload["repair_request"]


def test_compiler_repairs_a_parseable_draft_in_place() -> None:
    """A draft that parsed is repaired, not recompiled, so its work survives."""

    client = _ScriptedClient(
        [
            {
                "content": json.dumps(
                    {
                        "status": "compiled",
                        "rationale": "A planar coating is supported by TMM.",
                        "experiments": [],
                    }
                ),
                "_llm_usage": {"model_name": "qwen3.7-flash"},
            },
            _compiled_response(),
        ]
    )
    result = QwenTMMTaskCompiler(client=client).compile("Compile this bounded coating task.")

    assert result.status == "compiled"
    assert result.attempts == 2
    repair_payload = json.loads(client.calls[1][1]["content"])
    assert repair_payload["existing_draft"]["status"] == "compiled"
    assert repair_payload["repair_request"]["validation_errors"]


def test_compiler_fails_closed_after_two_invalid_outputs() -> None:
    client = _ScriptedClient(
        [
            {"content": "{}", "_llm_usage": {}},
            {"content": '{"status":"compiled"}', "_llm_usage": {}},
        ]
    )
    result = QwenTMMTaskCompiler(client=client).compile("Invalid draft test.")

    assert result.status == "invalid"
    assert result.task is None
    assert result.attempts == 2
    assert result.validation_errors


def test_compiler_preserves_higher_fidelity_boundary() -> None:
    response = {
        "content": json.dumps(
            {
                "status": "needs_higher_fidelity",
                "rationale": "The requested lateral diffraction requires RCWA.",
                "normalized_request_english": "",
                "experiments": [],
                "uncertainty": {},
            }
        ),
        "_llm_usage": {"model_name": "qwen3.7-flash"},
    }
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Resolve diffraction orders from a two-dimensional grating."
    )

    assert result.status == "needs_higher_fidelity"
    assert result.task is None


def test_compiler_rejects_cjk_in_intermediate_normalized_request() -> None:
    response = _compiled_response()
    payload = json.loads(response["content"])
    payload["normalized_request_english"] = "中文中间消息"
    response["content"] = json.dumps(payload, ensure_ascii=False)
    client = _ScriptedClient([response, response])

    result = QwenTMMTaskCompiler(client=client).compile("中文用户问题可以作为原始输入")

    assert result.status == "invalid"
    assert result.task is None


def test_compiler_repairs_incomplete_two_band_preference_contract() -> None:
    source = build_dev_optical_design_task("DEV02")
    experiment = source.experiments[0].model_dump(mode="json")
    incomplete = dict(experiment)
    incomplete["objectives"] = [
        {
            "objective_id": "preferred_band",
            "metric": "mean_emissivity",
            "sense": "maximize",
            "weight": 1.0,
            "target": None,
            "region": {"wavelength_nm": [650.0, 800.0]},
            "admission_role": "score_only",
        }
    ]
    complete = dict(experiment)
    complete["objectives"] = [
        *incomplete["objectives"],
        {
            "objective_id": "suppressed_band",
            "metric": "mean_emissivity",
            "sense": "minimize",
            "weight": 1.0,
            "target": None,
            "region": {"wavelength_nm": [450.0, 550.0]},
            "admission_role": "score_only",
        },
    ]

    def response(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": json.dumps(
                {
                    "status": "compiled",
                    "rationale": "Synthetic two-band analysis.",
                    "normalized_request_english": "Analyze two emissivity bands.",
                    "experiments": [item],
                    "uncertainty": source.uncertainty.model_dump(mode="json"),
                }
            ),
            "_llm_usage": {"model_name": "qwen3.7-flash"},
        }

    benchmark = BenchmarkTask.model_validate(
        {
            "id": "SYNTH_BAND",
            "split": "dev",
            "domain": "TMM",
            "title": "Synthetic band preference",
            "natural_language_question": "Prefer one emissivity band and suppress another.",
            "task_family": "forward_analysis",
            "capability_axes": ["band_preference"],
            "expected_artifacts": ["thermal_band_preference_report.json"],
            "evaluation_contract": {
                "performance_targets": "soft_scores",
                "admission_gate": "deterministic_physics_validity_only",
                "hard_gates": [],
                "statement": "Performance targets are soft scores; deterministic physics validity is the only admission gate.",
            },
        }
    )
    client = _ScriptedClient([response(incomplete), response(complete)])

    result = QwenTMMTaskCompiler(client=client).compile(
        benchmark.natural_language_question,
        benchmark=benchmark,
    )

    assert result.status == "compiled"
    assert result.attempts == 2
    objectives = result.task.experiments[0].objectives if result.task else ()
    assert {item.sense for item in objectives} == {"maximize", "minimize"}
    repair = json.loads(client.calls[1][1]["content"])["repair_request"]
    assert "band_preference" in " ".join(repair["validation_errors"])

    unlabelled_client = _ScriptedClient([response(incomplete), response(complete)])
    unlabelled = QwenTMMTaskCompiler(client=unlabelled_client).compile(
        "Analyze high reflectance from 650-800 nm and low reflectance from 450-550 nm."
    )
    assert unlabelled.status == "compiled"
    assert unlabelled.attempts == 2
    unlabelled_repair = json.loads(unlabelled_client.calls[1][1]["content"])[
        "repair_request"
    ]
    assert "opposing multi-band preferences" in " ".join(
        unlabelled_repair["validation_errors"]
    )


def test_wavelength_ranges_ignore_enumerated_layer_thickness_bounds() -> None:
    from optomind_optics.harness.task_compiler import _wavelength_ranges

    # Shape taken from a real route request: one spectral band in the opening
    # sentence, then a long enumeration of per-layer bounds that names
    # "thicknesses" only once, at the very start of its own sentence.
    request = (
        "Optimize a 30-layer chirped TiO2/SiO2 mirror stack for high "
        "reflectance across 500-900 nm at incidence angles 0, 30, and 60 "
        "degrees for both s and p polarizations. All 30 layer thicknesses "
        "are optimizable variables with bounds: layers 1-7: 40-250 nm, "
        "layers 8-12: 30-150 nm (expanded from prior 44-118 nm clustering), "
        "layers 13-15: 40-200 nm, layer 30: 200-300 nm (termination layer)."
    )

    assert _wavelength_ranges(request) == {(500.0, 900.0, "nm")}


def test_wavelength_ranges_still_separate_two_genuine_spectral_bands() -> None:
    from optomind_optics.harness.task_compiler import _wavelength_ranges

    request = (
        "Design a thermal emitter with high emissivity across 8-13 um and "
        "low emissivity across 3-5 um. Layer thicknesses are bounded "
        "20-400 nm."
    )

    assert _wavelength_ranges(request) == {(8.0, 13.0, "um"), (3.0, 5.0, "um")}


def test_wavelength_ranges_ignore_a_thickness_bound_called_a_band() -> None:
    from optomind_optics.harness.task_compiler import _wavelength_ranges

    # Verbatim shape from a real route request.  "band" is generic enough that
    # requests use it for a thickness interval, so a sentence can carry both a
    # geometric and a spectral word and only word order says which range is
    # which.
    request = (
        "Optimize a 24-layer TiO2/SiO2 chirped mirror stack for broadband "
        "high reflectance across 500-900 nm at 0, 30, and 60 degrees "
        "incidence. Initialize all 24 layer thicknesses uniformly within "
        "140-190 nm band."
    )

    assert _wavelength_ranges(request) == {(500.0, 900.0, "nm")}


def test_wavelength_ranges_keep_a_band_that_merely_ends_in_a_thickness_word() -> None:
    from optomind_optics.harness.task_compiler import _wavelength_ranges

    # The mirror image of the case above: a genuine spectral range whose
    # sentence happens to mention thickness afterwards must survive.  Asking
    # only whether the sentence contains a geometric word drops it.
    request = (
        "Design a chirped TiO2/SiO2 wide-angle robust mirror from 500-900 nm "
        "for 0, 30, and 60 degrees with TE and TM polarization, using 2 nm "
        "quantization and thickness uncertainty."
    )

    assert _wavelength_ranges(request) == {(500.0, 900.0, "nm")}


def test_declared_layer_count_admits_the_semi_infinite_reading() -> None:
    from optomind_optics.harness.task_compiler import (
        _bounding_media_named,
        _enclosing_sentence,
        _DECLARED_LAYER_COUNT_RE,
    )

    # Verbatim from a real route request.  Prose calls this a "3-layer" stack
    # by counting the Al substrate, while the compiler policy directive tells
    # the model to count only the coating layers -- giving 2.  Both readings
    # have to pass, or the check rejects the reading it asked for.
    request = (
        "Analyze a fixed 3-layer planar stack: Air incident medium, SiC layer "
        "1700 nm thickness, SiO2 layer 500 nm thickness, Al semi-infinite "
        "substrate."
    )
    match = next(_DECLARED_LAYER_COUNT_RE.finditer(request))
    sentence, _ = _enclosing_sentence(request, match.start(), match.end())

    assert _bounding_media_named(sentence) == 2


def test_declared_layer_count_stays_strict_without_bounding_media() -> None:
    from optomind_optics.harness.task_compiler import (
        _bounding_media_named,
        _enclosing_sentence,
        _DECLARED_LAYER_COUNT_RE,
    )

    # A request that names no semi-infinite medium alongside its count gets no
    # slack: a draft that silently emits 20 layers for a 24-layer request must
    # still be rejected.
    request = (
        "Optimize a 24-layer TiO2/SiO2 chirped mirror stack for broadband "
        "high reflectance across 500-900 nm."
    )
    match = next(_DECLARED_LAYER_COUNT_RE.finditer(request))
    sentence, _ = _enclosing_sentence(request, match.start(), match.end())

    assert _bounding_media_named(sentence) == 0


def test_optimize_directive_names_the_path_its_objective_lives_at() -> None:
    from optomind_optics.harness.task_compiler import _bounded_compiler_question

    # Naming the constraint without naming the path ("an optimize experiment
    # must declare at least one target") reads as an instruction to hang
    # targets off the experiment, which the schema rejects.
    directives = _bounded_compiler_question("Optimize a 12-layer mirror.")

    assert "tmm_task.targets" in directives
    assert "tmm_task.optimizer" in directives


def test_angle_values_from_clause_handles_repeated_and_terminal_units() -> None:
    from optomind_optics.harness.task_compiler import _angle_values_from_clause

    assert _angle_values_from_clause(
        "0 degrees, 30 degrees, 45 degrees"
    ) == [0.0, 30.0, 45.0]
    assert _angle_values_from_clause("0, 30, and 45 degrees") == [
        0.0,
        30.0,
        45.0,
    ]
    assert _angle_values_from_clause("0, 30 and 45 degrees") == [
        0.0,
        30.0,
        45.0,
    ]
    assert _angle_values_from_clause("45 degrees") == [45.0]
    assert _angle_values_from_clause("a 6-layer stack") == []
    assert _angle_values_from_clause(
        "0, 30, and 45 degrees for a 6-layer stack"
    ) == [0.0, 30.0, 45.0]
    assert _angle_values_from_clause(
        "6-layer, 0, 30, and 45 degrees"
    ) == [0.0, 30.0, 45.0]
    assert _angle_values_from_clause(
        "450-700 nm at 0, 30, and 45 degrees"
    ) == [0.0, 30.0, 45.0]


def test_requested_illumination_excludes_uncertainty_offsets() -> None:
    from optomind_optics.harness.task_compiler import _requested_illumination

    angles, polarizations = _requested_illumination(
        "Robustness conditions: common incidence-angle offset bounded by "
        "plus or minus 1 degree; incidence angles 0, 30, and 45 degrees "
        "for TE and TM."
    )
    assert angles == [0.0, 30.0, 45.0]
    assert polarizations == ["s", "p"]


def test_compiler_parses_repeated_unit_angles_and_expands_all_channels() -> None:
    response = _compiled_response()
    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Design a broadband one-dimensional antireflection coating for a "
        "fused-silica substrate in air over 450-700 nm. "
        "Evaluate incidence angles of 0, 30, and 45 degrees for both TE and "
        "TM polarization. "
        "Canonical user controls: incidence angles 0 degrees, 30 degrees, "
        "45 degrees. polarization TE, TM. wavelength intervals 450-700 nm. "
        "soft objectives: mean reflectance at or below 0.8 percent; "
        "worst-case reflectance at or below 3 percent. "
        "robustness conditions: independent one-sigma thickness errors of "
        "2 nm together with a common incidence-angle offset bounded by "
        "plus or minus 1 degree."
    )

    assert result.status == "compiled"
    assert result.attempts == 1
    experiment = result.task.experiments[0]
    illumination = experiment.tmm_task["simulation"]["illumination"]
    assert illumination["angles_deg"] == [0.0, 30.0, 45.0]
    assert illumination["polarizations"] == ["s", "p"]
    targets = experiment.tmm_task["targets"]
    assert len(targets) == 12
    mean_targets = [
        item for item in targets if item["aggregation"] == "mean"
    ]
    worst_targets = [
        item for item in targets if item["aggregation"] == "worst_case"
    ]
    assert len(mean_targets) == 6
    assert len(worst_targets) == 6
    expected_channels = {
        (0.0, "s"),
        (0.0, "p"),
        (30.0, "s"),
        (30.0, "p"),
        (45.0, "s"),
        (45.0, "p"),
    }
    assert {
        (item["angle_deg"], item["polarization"]) for item in mean_targets
    } == expected_channels
    assert {
        (item["angle_deg"], item["polarization"]) for item in worst_targets
    } == expected_channels
    assert {item["target"] for item in mean_targets} == {0.008}
    assert {item["target"] for item in worst_targets} == {0.03}
    assert result.task.uncertainty.thickness_sigma_nm == 2.0
    assert result.task.uncertainty.angle_perturbation_deg == 1.0
    objectives = result.task.experiments[0].objectives
    mean_objectives = [
        item for item in objectives if item.metric == "mean_reflectance"
    ]
    worst_objectives = [
        item for item in objectives if item.metric == "worst_case_reflectance"
    ]
    assert len(mean_objectives) == 6
    assert len(worst_objectives) == 6
    assert {item.target for item in mean_objectives} == {0.008}
    assert {item.target for item in worst_objectives} == {0.03}
    assert {item.sense for item in worst_objectives} == {"minimize"}


_WIDE_ANGLE_QUESTION = (
    "Design and optimize a 24-layer planar mirror stack with alternating TiO2 and "
    "SiO2 layers. Evaluate performance across wavelength range 500-900 nm at "
    "incidence angles 0, 30, and 60 degrees for both s and p polarizations. "
    "Canonical user controls: polarization s, p. wavelength intervals 500-900 nm."
)


def _quarter_wave_layers() -> list[dict[str, Any]]:
    """Twenty-four alternating quarter-wave layers centred near 700 nm.

    The layer count has to match the one the question declares, otherwise the
    compiler rejects the draft before the objective is ever examined.  Constant
    indices keep the fixture independent of the material registry.
    """

    layers: list[dict[str, Any]] = []
    for index in range(24):
        high = index % 2 == 0
        layers.append(
            {
                "material": None,
                "constant_n": 2.35 if high else 1.46,
                "constant_k": 0.0,
                "thickness_nm": 74.5 if high else 119.9,
                "coherence": "coherent",
                "optimizable": True,
                "label": f"{'TiO2' if high else 'SiO2'}_{index + 1}",
                "min_thickness_nm": 30.0,
                "max_thickness_nm": 250.0,
            }
        )
    return layers


def _mirror_response(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """A compiled draft whose objective is exactly ``targets``."""

    source = build_dev_optical_design_task("DEV01")
    experiment = source.experiments[0].model_dump(mode="json")
    experiment["tmm_task"] = {
        **experiment["tmm_task"],
        "targets": targets,
        "simulation": {
            **experiment["tmm_task"]["simulation"],
            "spectrum": {"start_nm": 500.0, "stop_nm": 900.0, "points": 101},
            "illumination": {
                "angles_deg": [0.0, 30.0, 60.0],
                "polarizations": ["s", "p"],
            },
            "stack": {
                **experiment["tmm_task"]["simulation"]["stack"],
                "name": "wide_angle_mirror",
                "layers": _quarter_wave_layers(),
            },
        },
    }
    payload = {
        "status": "compiled",
        "rationale": "A planar multilayer mirror is supported by TMM.",
        "normalized_request_english": _WIDE_ANGLE_QUESTION,
        "experiments": [experiment],
        "uncertainty": source.uncertainty.model_dump(mode="json"),
    }
    return {
        "content": json.dumps(payload),
        "_llm_usage": {
            "model_name": "qwen3.7-flash",
            "input_tokens": 500,
            "output_tokens": 900,
        },
    }


def _target(
    observable: str,
    constraint: str,
    value: float,
    *,
    angle: float = 0.0,
    polarization: str = "s",
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "observable": observable,
        "constraint": constraint,
        "target": value,
        "wavelength_min_nm": 500.0,
        "wavelength_max_nm": 900.0,
        "angle_deg": angle,
        "polarization": polarization,
        "weight": 1.0,
        "aggregation": "mean",
        "name": name or f"{observable}_{angle:g}_{polarization}",
    }


def _channels(result: Any) -> set[tuple[float, str]]:
    return {
        (float(item["angle_deg"]), str(item["polarization"]))
        for item in result.task.experiments[0].tmm_task["targets"]
    }


def test_single_polarization_objective_is_completed_to_the_requested_grid() -> None:
    client = _ScriptedClient([_mirror_response([_target("R", "at_least", 0.9)])])
    result = QwenTMMTaskCompiler(client=client).compile(_WIDE_ANGLE_QUESTION)

    assert result.status == "compiled"
    assert _channels(result) == {
        (0.0, "s"), (30.0, "s"), (60.0, "s"),
        (0.0, "p"), (30.0, "p"), (60.0, "p"),
    }
    corrections = result.task.metadata["illumination_corrections"]
    assert any("requested polarization is scored" in item for item in corrections)
    names = [item["name"] for item in result.task.experiments[0].tmm_task["targets"]]
    assert len(set(names)) == len(names)


def test_deliberate_per_polarization_thresholds_are_not_cross_multiplied() -> None:
    client = _ScriptedClient(
        [
            _mirror_response(
                [
                    _target("R", "at_least", 0.95, polarization="s", name="refl_s"),
                    _target("R", "at_least", 0.80, polarization="p", name="refl_p"),
                ]
            )
        ]
    )
    result = QwenTMMTaskCompiler(client=client).compile(_WIDE_ANGLE_QUESTION)

    targets = result.task.experiments[0].tmm_task["targets"]
    # Angle completion still runs; polarization completion must not, because the
    # union already covers both requested polarizations.
    assert _channels(result) == {
        (0.0, "s"), (30.0, "s"), (60.0, "s"),
        (0.0, "p"), (30.0, "p"), (60.0, "p"),
    }
    assert {
        (item["polarization"], item["target"]) for item in targets
    } == {("s", 0.95), ("p", 0.80)}
    corrections = result.task.metadata["illumination_corrections"]
    assert not any("requested polarization is scored" in item for item in corrections)


def test_objective_is_frozen_across_revisions_of_one_run() -> None:
    """One compiler instance serves one run, so its scoreboard must not move."""

    wave_one = [_target("R", "at_least", 0.9)]
    wave_two = [_target("R", "at_least", 1.0)]          # threshold rewritten
    wave_three = [_target("R", "at_least", 0.0), _target("T", "at_most", 0.5)]
    client = _ScriptedClient(
        [
            _mirror_response(wave_one),
            _mirror_response(wave_two),
            _mirror_response(wave_three),
        ]
    )
    compiler = QwenTMMTaskCompiler(client=client)

    first = compiler.compile(_WIDE_ANGLE_QUESTION)
    second = compiler.compile(_WIDE_ANGLE_QUESTION)
    third = compiler.compile(_WIDE_ANGLE_QUESTION)

    signature = first.task.metadata["objective_signature"]
    assert second.task.metadata["objective_signature"] == signature
    assert third.task.metadata["objective_signature"] == signature

    assert any("objective frozen" in item for item in first.task.metadata["objective_freeze"])
    for later in (second, third):
        assert any(
            "with the objective frozen at run start" in item
            for item in later.task.metadata["objective_freeze"]
        )
        assert {item["target"] for item in later.task.experiments[0].tmm_task["targets"]} == {0.9}
        assert {item["observable"] for item in later.task.experiments[0].tmm_task["targets"]} == {"R"}


def test_an_emptied_objective_is_restored_from_the_freeze_before_validation() -> None:
    """The most complete rewrite of a scoreboard must not bypass the freeze.

    ``OptimizationTask`` rejects an objective with no target, so a revision
    that drops its targets dies inside ``model_validate`` -- upstream of the
    pass that re-imposes the frozen objective.  The route then reports that the
    model failed to produce a valid task, when what actually happened is that
    the model deleted the scoreboard it is not allowed to change.
    """

    frozen = [_target("R", "at_least", 0.9)]
    emptied = _mirror_response(frozen)
    payload = json.loads(emptied["content"])
    payload["experiments"][0]["tmm_task"]["targets"] = []
    emptied = {**emptied, "content": json.dumps(payload)}
    client = _ScriptedClient([_mirror_response(frozen), emptied])
    compiler = QwenTMMTaskCompiler(client=client)

    first = compiler.compile(_WIDE_ANGLE_QUESTION)
    second = compiler.compile(_WIDE_ANGLE_QUESTION)

    assert first.status == "compiled"
    assert second.status == "compiled"
    assert (
        second.task.metadata["objective_signature"]
        == first.task.metadata["objective_signature"]
    )
    assert second.task.experiments[0].tmm_task["targets"]
    assert any(
        "restored the objective frozen at run start" in item
        and "declared no target" in item
        for item in second.task.metadata["objective_freeze"]
    )


def test_a_zero_weighted_objective_is_restored_from_the_freeze() -> None:
    """A target carrying no weight cannot rank anything, so it is not a target."""

    frozen = [_target("R", "at_least", 0.9)]
    zeroed = _mirror_response(frozen)
    payload = json.loads(zeroed["content"])
    for target in payload["experiments"][0]["tmm_task"]["targets"]:
        target["weight"] = 0.0
    zeroed = {**zeroed, "content": json.dumps(payload)}
    client = _ScriptedClient([_mirror_response(frozen), zeroed])
    compiler = QwenTMMTaskCompiler(client=client)

    compiler.compile(_WIDE_ANGLE_QUESTION)
    second = compiler.compile(_WIDE_ANGLE_QUESTION)

    assert second.status == "compiled"
    assert {
        float(item["weight"])
        for item in second.task.experiments[0].tmm_task["targets"]
    } == {1.0}
    assert any(
        "ungradable target(s)" in item
        for item in second.task.metadata["objective_freeze"]
    )


def test_a_merely_revised_objective_is_left_to_the_authoritative_pass() -> None:
    """Pre-validation repair is for destroyed objectives, not revised ones.

    A revision that rewrites a threshold still validates, so it must reach the
    post-validation freeze and be replaced there.  Repairing it earlier would
    make the two passes indistinguishable in the record and hide which shape of
    rewrite a run actually saw.
    """

    client = _ScriptedClient(
        [
            _mirror_response([_target("R", "at_least", 0.9)]),
            _mirror_response([_target("R", "at_least", 1.0)]),
        ]
    )
    compiler = QwenTMMTaskCompiler(client=client)

    compiler.compile(_WIDE_ANGLE_QUESTION)
    second = compiler.compile(_WIDE_ANGLE_QUESTION)

    freeze_notes = second.task.metadata["objective_freeze"]
    assert not any("before validation" in item for item in freeze_notes)
    assert any("with the objective frozen at run start" in item for item in freeze_notes)
    assert {
        item["target"] for item in second.task.experiments[0].tmm_task["targets"]
    } == {0.9}


_NARROW_QUESTION = (
    "Design and optimize a 24-layer planar mirror stack with alternating TiO2 and "
    "SiO2 layers. Evaluate performance across wavelength range 500-900 nm at "
    "normal incidence for unpolarized light."
)

_WIDENED_QUESTION = (
    "Design and optimize a 24-layer planar mirror stack with alternating TiO2 and "
    "SiO2 layers. Evaluate performance across wavelength range 500-900 nm at "
    "incidence angles 0, 30, 45, and 60 degrees for both s and p polarizations."
)


def _mirror_response_on(
    question: str,
    angles: list[float],
    polarizations: list[str],
    targets: list[dict[str, Any]],
) -> dict[str, Any]:
    """A compiled draft with an explicitly chosen sweep, for revision tests."""

    source = build_dev_optical_design_task("DEV01")
    experiment = source.experiments[0].model_dump(mode="json")
    experiment["tmm_task"] = {
        **experiment["tmm_task"],
        "targets": targets,
        "simulation": {
            **experiment["tmm_task"]["simulation"],
            "spectrum": {"start_nm": 500.0, "stop_nm": 900.0, "points": 101},
            "illumination": {"angles_deg": angles, "polarizations": polarizations},
            "stack": {
                **experiment["tmm_task"]["simulation"]["stack"],
                "name": "wide_angle_mirror",
                "layers": _quarter_wave_layers(),
            },
        },
    }
    payload = {
        "status": "compiled",
        "rationale": "A planar multilayer mirror is supported by TMM.",
        "normalized_request_english": question,
        "experiments": [experiment],
        "uncertainty": source.uncertainty.model_dump(mode="json"),
    }
    return {
        "content": json.dumps(payload),
        "_llm_usage": {
            "model_name": "qwen3.7-flash",
            "input_tokens": 500,
            "output_tokens": 900,
        },
    }


def test_a_widened_revision_still_declares_the_frozen_objectives_channel() -> None:
    """A route may broaden its own sweep without losing its scoreboard.

    Wave one asks for normal incidence only, so the objective freezes on the
    single ``(0 deg, unpolarized)`` channel.  Wave two revises the request to
    sweep four angles in s and p -- the ordinary way a route explores -- and
    the illumination is re-derived from that revised text while the frozen
    objective is re-imposed unchanged.  Before the two were reconciled the
    engine rejected the frozen target as undeclared, and the route died
    reporting that the model had failed to produce a valid task.
    """

    frozen_target = _target("R", "at_least", 0.9, polarization="unpolarized")
    client = _ScriptedClient(
        [
            _mirror_response_on(
                _NARROW_QUESTION, [0.0], ["unpolarized"], [frozen_target]
            ),
            _mirror_response_on(
                _WIDENED_QUESTION,
                [0.0, 30.0, 45.0, 60.0],
                ["s", "p"],
                [_target("R", "at_least", 0.9, angle=angle, polarization=polarization)
                 for angle in (0.0, 30.0, 45.0, 60.0)
                 for polarization in ("s", "p")],
            ),
        ]
    )
    compiler = QwenTMMTaskCompiler(client=client)

    first = compiler.compile(_NARROW_QUESTION)
    second = compiler.compile(_WIDENED_QUESTION)

    assert first.status == "compiled"
    assert second.status == "compiled"

    # The scoreboard is untouched: same signature, same single channel.
    assert (
        second.task.metadata["objective_signature"]
        == first.task.metadata["objective_signature"]
    )
    assert _channels(second) == {(0.0, "unpolarized")}

    # The sweep moved instead, and it kept everything the revision asked for.
    illumination = second.task.experiments[0].tmm_task["simulation"]["illumination"]
    assert set(illumination["angles_deg"]) == {0.0, 30.0, 45.0, 60.0}
    assert set(illumination["polarizations"]) == {"s", "p", "unpolarized"}
    assert any(
        "objective scores that channel" in item
        for item in (
            *second.task.metadata["objective_freeze"],
            *second.task.metadata["illumination_corrections"],
        )
    )


def test_the_sanity_gate_rejects_the_scoreboards_that_cannot_rank_designs() -> None:
    """The gate is a backstop, so it is tested where it can still be reached.

    Coverage completion now closes every channel gap reachable through
    ``compile`` -- it even widens the illumination when the request names an
    angle the draft omitted -- so a gated objective no longer arrives by the
    ordinary route.  The rules still have to hold for the shapes that bypass
    completion: a draft with no targets at all, and one where completion has
    nothing to key on because the request itself named a channel no target
    covers.
    """

    from optomind_optics.harness.task_compiler import _objective_sanity_problems

    assert _objective_sanity_problems((), (0.0,), ("s",))

    zero_weight = [
        {**_target("R", "at_least", 0.9, angle=0.0, polarization="s"), "weight": 0.0}
    ]
    assert any(
        "zero weight" in problem
        for problem in _objective_sanity_problems(zero_weight, (0.0,), ("s",))
    )

    gapped = [_target("R", "at_least", 0.9, angle=0.0, polarization="s")]
    problems = _objective_sanity_problems(gapped, (0.0, 60.0), ("s", "p"))
    assert any("60|p" in problem and "60|s" in problem for problem in problems)
    assert any("0|p" in problem for problem in problems)

    complete = [
        _target("R", "at_least", 0.9, angle=angle, polarization=polarization)
        for angle in (0.0, 60.0)
        for polarization in ("s", "p")
    ]
    assert _objective_sanity_problems(complete, (0.0, 60.0), ("s", "p")) == ()


def test_an_extreme_threshold_is_not_treated_as_a_broken_objective() -> None:
    """``at_least 0.0`` and ``at_least 1.0`` read as broken and are not.

    The optimizer's loss ignores the threshold, and the reported soft score
    stays monotone in the observation for any threshold because its scale
    floors at 0.05.  Rejecting these would refuse the legitimate minimize-R and
    maximize-R formulations the fixtures use.
    """

    from optomind_optics.harness.task_compiler import _objective_sanity_problems

    for threshold in (0.0, 1.0):
        targets = [
            _target("R", "at_least", threshold, angle=angle, polarization=polarization)
            for angle in (0.0, 60.0)
            for polarization in ("s", "p")
        ]
        assert _objective_sanity_problems(targets, (0.0, 60.0), ("s", "p")) == ()


def test_compiler_prompt_enumerates_the_legal_optimizer_methods() -> None:
    """The template's optimizer method must advertise the engine's closed set.

    Every other closed field in the same JSON block is written ``"a|b|c"``, so
    a single concrete value read as a free-form example and the model supplied
    an optimizer name the engine rejects with "unsupported optimizer method".
    Comparing against the engine's own annotation rather than a hard-coded pair
    means adding a method to the engine fails here until the prompt catches up.
    """

    import typing

    from tmm_engine.schemas import OptimizerSpec

    from optomind_optics.harness.task_compiler import DEFAULT_TASK_COMPILER_PROMPT

    prompt = DEFAULT_TASK_COMPILER_PROMPT.read_text(encoding="utf-8")
    advertised = None
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith('"method"'):
            advertised = set(
                stripped.split(":", 1)[1].strip().rstrip(",").strip('"').split("|")
            )
            break

    assert advertised is not None, "the template no longer declares a method field"

    accepted = set(
        typing.get_args(typing.get_type_hints(OptimizerSpec)["method"])
    )
    assert advertised == accepted

    # The annotation is not enforced at construction time -- OptimizerSpec is a
    # plain dataclass -- so confirm the runtime guard agrees with it too.
    for method in sorted(advertised):
        OptimizerSpec(method=method).validate()


def test_compiler_rewrites_the_diagnostic_solver_before_it_reaches_the_engine() -> None:
    """A diagnostic-only solver must never survive compilation.

    The characteristic-matrix path amplifies absorbing layers instead of
    attenuating them, so the physics gate rejects every candidate it produces.
    A whole route once burned its entire budget this way and reported only a
    solver disagreement, so the rewrite has to happen in the compiler rather
    than being left to the prompt.
    """

    response = _compiled_response()
    payload = json.loads(response["content"])
    payload["experiments"][0]["tmm_task"]["simulation"]["solver"] = "characteristic"
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Design a single-layer antireflection coating on glass over 500-600 nm."
    )

    assert result.status == "compiled"
    simulation = result.task.experiments[0].tmm_task["simulation"]
    assert simulation["solver"] == "smatrix"
    corrections = result.task.metadata["solver_corrections"]
    assert corrections and "characteristic" in corrections[0]


def test_compiler_keeps_mixed_coherence_on_the_incoherent_capable_solver() -> None:
    """The replacement has to respect the stack, not just avoid the bad name.

    ``smatrix`` is the coherent default; a stack carrying an incoherent layer
    needs ``byrnes``, so rewriting everything to the default would trade a
    physics violation for a silently wrong coherence treatment.
    """

    response = _compiled_response()
    payload = json.loads(response["content"])
    simulation = payload["experiments"][0]["tmm_task"]["simulation"]
    simulation["solver"] = "characteristic"
    simulation["stack"]["layers"][0]["coherence"] = "incoherent"
    response["content"] = json.dumps(payload)

    result = QwenTMMTaskCompiler(client=_ScriptedClient([response])).compile(
        "Design a coating on a thick incoherent substrate over 500-600 nm."
    )

    assert result.status == "compiled"
    assert result.task.experiments[0].tmm_task["simulation"]["solver"] == "byrnes"


def test_every_engine_solver_is_classified_as_production_or_diagnostic() -> None:
    """The prompt, the compiler, and the engine must agree on the solver set.

    Three places name solvers: the engine's annotation, the compiler's
    production/diagnostic split, and the template that tells the model what it
    may ask for.  Deriving the assertions from the engine means adding a solver
    there fails here until someone classifies it, instead of the new name
    reaching production unreviewed -- which is exactly how the characteristic
    solver got offered in the first place.
    """

    import typing

    from tmm_engine.schemas import SimulationTask

    from optomind_optics.harness.task_compiler import (
        DEFAULT_TASK_COMPILER_PROMPT,
        _DIAGNOSTIC_SOLVERS,
        _PRODUCTION_SOLVERS,
    )

    engine_solvers = set(
        typing.get_args(typing.get_type_hints(SimulationTask)["solver"])
    )
    assert set(_PRODUCTION_SOLVERS) | _DIAGNOSTIC_SOLVERS == engine_solvers
    assert not set(_PRODUCTION_SOLVERS) & _DIAGNOSTIC_SOLVERS

    prompt = DEFAULT_TASK_COMPILER_PROMPT.read_text(encoding="utf-8")
    offered: set[str] | None = None
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith("- Use only solver"):
            offered = set(re.findall(r'"([a-z]+)"', stripped))
            break

    assert offered is not None, "the template no longer restricts the solver set"
    assert offered == set(_PRODUCTION_SOLVERS)


# ---------------------------------------------------------------------------
# A route may not be pointed against the standard it is ranked by
# ---------------------------------------------------------------------------


def _reflectance_standard(sense: str = "maximize") -> ScoringStandard:
    """A frozen standard that ranks one band of reflectance and nothing else.

    One metric on purpose: what is under test is whether a target that
    disagrees with the standard gets turned around, and a second metric would
    only add channels to reason about.
    """

    return ScoringStandard(
        question_digest="alignment_fixture",
        metrics=(
            FixedScoreMetric(
                variable="r_band",
                canonical_id="mean_reflectance@600-700nm",
                metric="mean_reflectance",
                sense=sense,
                region={"wavelength_nm": [600.0, 700.0]},
            ),
        ),
        formula="r_band",
        formula_variables=("r_band",),
    )


def _band_target(
    observable: str,
    constraint: str,
    value: float,
    low: float,
    high: float,
    *,
    aggregation: str = "mean",
) -> dict[str, Any]:
    """One unpolarized normal-incidence target over an arbitrary band.

    ``_target`` fixes 500-900 nm, and these tests turn on where a target sits
    relative to the scored band, so the interval has to be a parameter here.
    """

    return {
        "observable": observable,
        "constraint": constraint,
        "target": value,
        "wavelength_min_nm": low,
        "wavelength_max_nm": high,
        "angle_deg": 0.0,
        "polarization": "unpolarized",
        "weight": 1.0,
        "aggregation": aggregation,
        "name": f"{observable}_{low:g}_{high:g}",
    }


def _one_target_run(
    target: dict[str, Any],
    standard: ScoringStandard | None,
) -> Any:
    """Compile a single-channel draft carrying exactly ``target``."""

    client = _ScriptedClient(
        [_mirror_response_on(_NARROW_QUESTION, [0.0], ["unpolarized"], [target])]
    )
    compiler = QwenTMMTaskCompiler(client=client, scoring_standard=standard)
    result = compiler.compile(_NARROW_QUESTION)
    assert result.status == "compiled"
    assert len(client.calls) == 1, "a decidable disagreement must not cost a retry"
    return result


def _observable_targets(result: Any, observable: str) -> list[dict[str, Any]]:
    return [
        item
        for item in result.task.experiments[0].tmm_task["targets"]
        if str(item["observable"]).upper() == observable
    ]


def _route_objectives(result: Any, metric: str) -> list[Any]:
    """The route's own objectives for ``metric``, excluding the frozen ones."""

    return [
        item
        for item in result.task.experiments[0].objectives
        if item.metric == metric and not item.objective_id.startswith("fixedscore.")
    ]


def test_a_target_aimed_against_the_frozen_standard_is_turned_around() -> None:
    """The observed failure: "maximize R" compiled as ``at_most`` 0.0.

    Nothing objected, because the draft satisfied every schema.  The sense the
    optimizer pursues is derived from the constraint, so the route spent its
    whole round budget driving the one number it is ranked by toward zero.  The
    frozen standard already states which direction is better, so the
    disagreement is decidable without asking the model again.
    """

    result = _one_target_run(
        _band_target("R", "at_most", 0.0, 600.0, 700.0), _reflectance_standard()
    )

    reflectance = _observable_targets(result, "R")
    assert [item["constraint"] for item in reflectance] == ["at_least"]
    assert [item["target"] for item in reflectance] == [1.0]

    derived = _route_objectives(result, "mean_reflectance")
    assert [item.sense for item in derived] == ["maximize"]

    alignments = result.task.metadata["scoring_standard_target_alignments"]
    assert len(alignments) == 1
    assert "the wrong way for the frozen standard" in alignments[0]
    assert "rewrote at_most 0.0 as at_least 1.0" in alignments[0]


def test_turning_a_target_around_leaves_the_frozen_objectives_report_only() -> None:
    """Correcting a route must not promote the standard into the search.

    The standard measures; it does not tell a route what to pursue.  A fix that
    quietly made the scored metric an optimization target would collapse every
    route onto the same objective, which is the reason for running more than
    one.
    """

    result = _one_target_run(
        _band_target("R", "at_most", 0.0, 600.0, 700.0), _reflectance_standard()
    )

    injected = [
        item
        for item in result.task.experiments[0].objectives
        if item.objective_id.startswith("fixedscore.")
    ]
    assert injected, "the frozen standard must still be measured"
    assert {item.sense for item in injected} == {"report"}


def test_a_sub_band_of_the_scored_band_is_still_turned_around() -> None:
    """Containment, not equality: a narrower target is about the same region."""

    result = _one_target_run(
        _band_target("R", "at_most", 0.0, 620.0, 660.0), _reflectance_standard()
    )

    assert [item["constraint"] for item in _observable_targets(result, "R")] == [
        "at_least"
    ]
    assert len(result.task.metadata["scoring_standard_target_alignments"]) == 1


def test_a_worst_case_target_answers_to_a_mean_aggregated_standard() -> None:
    """Both aggregations of one observable mean the same thing by "more".

    A standard that ranks by mean reflectance still means "higher is better"
    when a route writes its target as a worst case, so the disagreement is the
    same disagreement and is corrected the same way.
    """

    result = _one_target_run(
        _band_target("R", "at_most", 0.0, 600.0, 700.0, aggregation="worst_case"),
        _reflectance_standard(),
    )

    corrected = _observable_targets(result, "R")
    assert [item["constraint"] for item in corrected] == ["at_least"]
    assert [item["aggregation"] for item in corrected] == ["worst_case"]
    derived = _route_objectives(result, "worst_case_reflectance")
    assert [item.sense for item in derived] == ["maximize"]


def test_the_gate_follows_the_standard_rather_than_preferring_more() -> None:
    """A standard that ranks downward turns an upward target around.

    Stated as its own case because a gate that simply rewrote every constraint
    to ``at_least`` would pass all the maximizing tests above while being
    wrong.
    """

    result = _one_target_run(
        _band_target("R", "at_least", 1.0, 600.0, 700.0),
        _reflectance_standard(sense="minimize"),
    )

    reflectance = _observable_targets(result, "R")
    assert [item["constraint"] for item in reflectance] == ["at_most"]
    assert [item["target"] for item in reflectance] == [0.0]
    assert [item.sense for item in _route_objectives(result, "mean_reflectance")] == [
        "minimize"
    ]


def test_a_route_may_aim_the_other_way_outside_the_scored_band() -> None:
    """Wanting low reflectance elsewhere is a trade-off, not a contradiction.

    The gate keys on containment for exactly this reason: a route suppressing
    reflectance in a band the standard does not score is making a design
    choice, and rewriting it would substitute the gate's judgement for the
    route's.
    """

    result = _one_target_run(
        _band_target("R", "at_most", 0.0, 500.0, 560.0), _reflectance_standard()
    )

    reflectance = _observable_targets(result, "R")
    assert [item["constraint"] for item in reflectance] == ["at_most"]
    assert [item["target"] for item in reflectance] == [0.0]
    assert result.task.metadata["scoring_standard_target_alignments"] == []


def test_a_target_merely_overlapping_the_scored_band_is_left_alone() -> None:
    """Partial overlap does not say which of the two intentions is meant.

    600-700 nm is scored and the target runs 680-900 nm: the route could be
    contradicting the standard, or could be shaping a transition just past the
    scored edge.  An undecidable case is left as written rather than guessed
    at.
    """

    result = _one_target_run(
        _band_target("R", "at_most", 0.0, 680.0, 900.0), _reflectance_standard()
    )

    assert [item["constraint"] for item in _observable_targets(result, "R")] == [
        "at_most"
    ]
    assert result.task.metadata["scoring_standard_target_alignments"] == []


def test_a_target_on_an_unscored_observable_is_left_alone() -> None:
    """The standard says nothing about transmittance here, so neither does the gate."""

    result = _one_target_run(
        _band_target("T", "at_most", 0.0, 600.0, 700.0), _reflectance_standard()
    )

    assert [item["constraint"] for item in _observable_targets(result, "T")] == [
        "at_most"
    ]
    assert result.task.metadata["scoring_standard_target_alignments"] == []


def test_an_agreeing_target_records_no_correction() -> None:
    """A silent pass is what tells us afterwards whether the gate ever fired.

    The route's own threshold is also kept: agreeing on direction is not a
    reason to overwrite a deliberate 0.95 with the gate's 1.0.
    """

    result = _one_target_run(
        _band_target("R", "at_least", 0.95, 600.0, 700.0), _reflectance_standard()
    )

    reflectance = _observable_targets(result, "R")
    assert [item["constraint"] for item in reflectance] == ["at_least"]
    assert [item["target"] for item in reflectance] == [0.95]
    assert result.task.metadata["scoring_standard_target_alignments"] == []


def test_a_run_without_a_frozen_standard_reports_no_alignments() -> None:
    """The gate needs a standard to compare against; absent one it is inert.

    Benchmarks and development runs compile without a standard, and they must
    keep compiling exactly as before.
    """

    result = _one_target_run(_band_target("R", "at_most", 0.0, 600.0, 700.0), None)

    assert result.task.metadata["scoring_standard_target_alignments"] == []
    assert [item["constraint"] for item in _observable_targets(result, "R")] == [
        "at_most"
    ]
