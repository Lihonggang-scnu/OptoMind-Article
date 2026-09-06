"""ScientificIntentSpec compiler: semantic regression case set.

The case set (``tmm_engine/intent/cases.json``) pairs reference natural-
language clauses (Chinese and English) with human-annotated IntentSpec key
fields and expected compilation assertions.  NL → IntentSpec extraction is
consumed downstream (OptoMind O-10); this suite pins the compiler's
IntentSpec → task direction, including the direction-reversal class whose
silent errors must be exactly zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tmm_engine import (
    LayerSpec,
    MediumSpec,
    StackSpec,
)
from tmm_engine.intent import (
    IntentSpec,
    compile_intent,
)

CASES_PATH = (
    Path(__file__).resolve().parents[1] / "tmm_engine" / "intent" / "cases.json"
)

CATEGORY_MINIMUMS = {
    "unit_conversion": 8,
    "direction": 8,
    "material_index": 6,
    "hard_soft": 6,
    "multi_objective": 6,
    "angle_pol": 6,
    "structural": 5,
    "ambiguity": 5,
}

MINIMUM_CASE_COUNT = 50


def _load_cases() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _default_stack(case: dict) -> StackSpec:
    stack = case.get("stack")
    if stack is not None:
        layers = tuple(
            LayerSpec(
                layer.get("material"),
                layer["thickness_nm"],
                constant_n=layer.get("constant_n"),
                constant_k=layer.get("constant_k", 0.0),
            )
            for layer in stack["layers"]
        )
        return StackSpec(
            layers=layers,
            incident=MediumSpec(**stack["incident"]),
            exit=MediumSpec(**stack["exit"]),
        )
    # default four-layer lossless stack (91/137 nm, n 2.15/1.43, exit 1.52)
    return StackSpec(
        layers=tuple(
            LayerSpec(None, thickness, constant_n=n)
            for thickness, n in ((91.0, 2.15), (137.0, 1.43)) * 2
        ),
        incident=MediumSpec.air(),
        exit=MediumSpec(constant_n=1.52),
    )


def _resolve(compiled, path: str):
    if path == "task_kind":
        return compiled.task_kind
    if path == "status":
        return compiled.certificate.semantic_status
    if path == "rejection_code":
        rejection = compiled.certificate.rejection
        return rejection["code"] if rejection else None
    if path == "conversion_count":
        return len(compiled.certificate.unit_conversions)
    if path.startswith("conversions["):
        index = int(path[len("conversions[") : path.index("]")])
        field = path.split("].", 1)[1]
        return compiled.certificate.unit_conversions[index][field]
    if path == "ambiguity_fields":
        return sorted({item["field"] for item in compiled.certificate.ambiguities})
    if path == "notes":
        return "\n".join(compiled.certificate.notes)
    if path == "defaults_fields":
        return sorted({item["field"] for item in compiled.certificate.defaults_inserted})
    if path == "target_count":
        return len(compiled.targets)
    if path.startswith("targets["):
        index = int(path[len("targets[") : path.index("]")])
        field = path.split("].", 1)[1]
        return getattr(compiled.targets[index], field)
    if path == "angles":
        task = compiled.optimization or compiled.simulation
        return sorted({float(angle) for angle in task.illumination.angles_deg})
    if path == "polarizations":
        task = compiled.optimization or compiled.simulation
        return sorted({str(pol) for pol in task.illumination.polarizations})
    if path.startswith("bounds["):
        index = int(path[len("bounds[") : path.index("]")])
        field = path.split("].", 1)[1]
        mapping = {"min_nm": "min_thickness_nm", "max_nm": "max_thickness_nm"}
        layer = compiled.optimization.simulation.stack.layers[index]
        return getattr(layer, mapping[field])
    if path == "bounds":
        key = path[len("bounds.") :]

        def _bounds(layer):
            mapping = {"min_nm": layer.min_thickness_nm, "max_nm": layer.max_thickness_nm}
            return mapping[key]

        return [
            _bounds(layer)
            for layer in compiled.optimization.simulation.stack.layers
            if layer.optimizable
        ]
    if path == "stack_constants":
        return [
            layer.constant_n
            for layer in compiled.simulation.stack.layers
            if layer.constant_n is not None
        ]
    raise AssertionError(f"unresolvable assertion path {path!r}")


def _compile_case(case: dict):
    intent_fields = dict(case["intent"])
    intent_fields.setdefault("intent_id", case["case_id"])
    intent_fields.setdefault("source_clause", case["nl_en"])
    spec = IntentSpec.model_validate(intent_fields)
    return compile_intent(spec, _default_stack(case))


def test_case_set_meets_coverage_minimums() -> None:
    payload = _load_cases()
    cases = payload["cases"]
    assert len(cases) >= MINIMUM_CASE_COUNT
    counts: dict = {}
    for case in cases:
        counts[case["category"]] = counts.get(case["category"], 0) + 1
    for category, minimum in CATEGORY_MINIMUMS.items():
        assert counts.get(category, 0) >= minimum, category


@pytest.mark.parametrize("case_index", range(len(_load_cases()["cases"])))
def test_semantic_case_compiles_as_annotated(case_index: int) -> None:
    payload = _load_cases()
    case = payload["cases"][case_index]
    compiled = _compile_case(case)
    certificate = compiled.certificate

    expect = case["expect"]
    assert certificate.semantic_status == expect["semantic_status"]
    if expect["task_kind"] != "any":
        assert compiled.task_kind == expect["task_kind"]

    for assertion in expect["assertions"]:
        actual = _resolve(compiled, assertion["path"])
        if "contains" in assertion:
            assert assertion["contains"] in actual, (
                f"{case['case_id']}: {assertion['path']} does not contain "
                f"{assertion['contains']!r} (got {actual!r})"
            )
        else:
            assert actual == assertion["value"], (
                f"{case['case_id']}: {assertion['path']} expected "
                f"{assertion['value']!r}, got {actual!r}"
            )


def test_direction_reversal_cases_have_zero_silent_errors() -> None:
    """The direction-reversal horror class: every case either compiles with
    the exact expected constraint/target pair or is explicitly ambiguous —
    never silently inverted."""

    payload = _load_cases()
    silent = []
    for case in payload["cases"]:
        if case["category"] != "direction":
            continue
        compiled = _compile_case(case)
        certificate = compiled.certificate
        if certificate.semantic_status == "rejected":
            silent.append((case["case_id"], "rejected"))
            continue
        expected = {
            item["path"]: item["value"]
            for item in case["expect"]["assertions"]
            if item["path"].startswith("targets[")
        }
        for path, value in expected.items():
            index = int(path[len("targets[") : path.index("]")])
            field = path.split("].", 1)[1]
            actual = getattr(compiled.targets[index], field)
            if actual != value:
                silent.append((case["case_id"], f"{path} = {actual!r}"))
    assert silent == []


def test_fail_closed_on_unknown_fields() -> None:
    with pytest.raises(Exception):
        IntentSpec.model_validate(
            {
                "intent_id": "bad",
                "observables": [
                    {
                        "quantity": "R",
                        "domain": {"wavelength_min_nm": 500.0},
                        "colour": "red",
                    }
                ],
            }
        )


def test_compile_equivalence_certificate_round_trips(tmp_path: Path) -> None:
    case = _load_cases()["cases"][0]
    compiled = _compile_case(case)
    certificate = compiled.certificate
    payload = certificate.model_dump(mode="json")
    assert payload["schema_version"] == "compilation-equivalence.v1"
    restored = type(certificate).model_validate(payload)
    assert restored.model_dump(mode="json") == payload
    assert restored.source_clause_hash == certificate.source_clause_hash


def test_schema_export_includes_intent_kinds() -> None:
    from tmm_engine.protocol.schema_export import export_schema

    intent_schema = export_schema("intent")
    assert "IntentSpec" in json.dumps(intent_schema)
    equivalence_schema = export_schema("compilation-equivalence")
    assert "compilation-equivalence.v1" in json.dumps(equivalence_schema)
