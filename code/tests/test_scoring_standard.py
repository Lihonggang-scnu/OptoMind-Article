"""The frozen scoring standard: verification, whitelist, orientation, ranking.

Four properties are worth a test each, because each one fails silently:

* A metric the simulator cannot compute must be rejected before a run, not
  discovered after one.
* Model-authored expression text must never reach an evaluator that can resolve
  a name or read an attribute.
* A reversed direction must be caught by probing, because afterwards every
  route is ranked consistently and the study simply concludes that the worst
  design won.
* The injected objectives must contribute nothing to the harness's own
  aggregate score.  If they did, freezing the ranking would also steer what
  every route searches for, which is the opposite of running independent routes.
"""

from __future__ import annotations

import json

import pytest

from optomind_optics.harness.metric_catalog import (
    FIXED_SCORE_OBJECTIVE_PREFIX,
    SCOREABLE_METRICS,
    canonical_metric_id,
    formula_variable_name,
    is_fixed_score_objective_id,
)
from optomind_optics.harness.dev_fixtures import build_dev_optical_design_task
from optomind_optics.harness.objectives import evaluate_declared_objectives
from optomind_optics.harness.scoring_standard import (
    DEFAULT_MAXIMUM_METRICS,
    MINIMUM_SAMPLES_PER_BAND,
    FixedScoreMetric,
    FormulaError,
    QwenScoringStandardBuilder,
    ScoringStandard,
    compile_formula,
    widen_spectral_grid,
)
from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler
from tmm_engine import (
    IlluminationSpec,
    LayerSpec,
    MaterialRegistry,
    MediumSpec,
    SimulationTask,
    SpectralGrid,
    StackSpec,
    TMMWorkbench,
)


VISIBLE_REFLECTANCE = {
    "metric": "mean_reflectance",
    "sense": "maximize",
    "wavelength_unit": "nm",
    "region": {"wavelength_nm": [300, 800]},
    "rationale": "the request names 300-800 nm",
}
INFRARED_ABSORPTION_UM = {
    "metric": "mean_absorption",
    "sense": "maximize",
    "wavelength_unit": "um",
    "region": {"wavelength_nm": [5, 13]},
    "rationale": "the request names the 5-13 um window",
}
INFRARED_ABSORPTION_MINIMIZED = {**INFRARED_ABSORPTION_UM, "sense": "minimize"}

VISIBLE_VARIABLE = "mean_reflectance_300_800nm"
INFRARED_VARIABLE = "mean_absorption_5000_13000nm"

QUESTION = "Reflect 300-800 nm and absorb the 5-13 um window."


class _CompilerClient:
    """The task compiler's own client shape: replies are dicts, not strings."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        return self.responses.pop(0)


class ScriptedClient:
    """A client that returns prepared strings and records what it was sent."""

    model_name = "scripted-planner"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.payloads: list[dict] = []

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        self.payloads.append(json.loads(messages[1]["content"]))
        reply = self.replies.pop(0) if self.replies else "{}"
        return {"content": reply, "_llm_usage": {"total_tokens": 11}}


def _selection(*metrics: dict, rationale: str = "chosen from the request") -> str:
    return json.dumps({"metrics": list(metrics), "rationale": rationale})


def _formula(text: str, rationale: str = "equal priority") -> str:
    return json.dumps({"formula": text, "rationale": rationale})


def _standard(*, sense: str = "maximize", formula: str | None = None) -> ScoringStandard:
    infrared = (
        INFRARED_ABSORPTION_UM if sense == "maximize" else INFRARED_ABSORPTION_MINIMIZED
    )
    expression = formula or (
        f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}"
        if sense == "maximize"
        else f"{VISIBLE_VARIABLE} - {INFRARED_VARIABLE}"
    )
    builder = QwenScoringStandardBuilder(
        ScriptedClient(_selection(VISIBLE_REFLECTANCE, infrared), _formula(expression))
    )
    result = builder.build(QUESTION)
    assert result.status == "standardized", result.validation_errors
    assert result.standard is not None
    return result.standard


def _attainment(visible: float, infrared: float) -> dict:
    return {
        "target_attainment": {
            f"{FIXED_SCORE_OBJECTIVE_PREFIX}{VISIBLE_VARIABLE}": {
                "metric": "mean_reflectance",
                "observed": visible,
                "region": {"wavelength_nm": [300.0, 800.0]},
            },
            f"{FIXED_SCORE_OBJECTIVE_PREFIX}{INFRARED_VARIABLE}": {
                "metric": "mean_absorption",
                "observed": infrared,
                "region": {"wavelength_nm": [5000.0, 13000.0]},
            },
        }
    }


# ---------------------------------------------------------------------------
# 1. The expression language is a whitelist
# ---------------------------------------------------------------------------


class TestFormulaWhitelist:
    """Model text is parsed and walked, never executed.

    The rejected cases are grouped by what an attacker or a confused model
    would actually emit: a call into the interpreter, a name that resolves to
    something outside the metric set, and syntax that smuggles control flow into
    an expression that is supposed to be arithmetic.
    """

    VARIABLES = ("a_nm", "b_nm")

    @pytest.mark.parametrize(
        "expression",
        [
            "a_nm + b_nm",
            "a_nm - b_nm",
            "2 * a_nm - 0.5 * b_nm",
            "min(a_nm, b_nm) + 0 * b_nm",
            "max(a_nm, b_nm) * min(a_nm, b_nm)",
            "abs(a_nm - 0.5) * -1 + b_nm",
            "(a_nm + b_nm) / 2",
            "a_nm ** 2 + b_nm",
        ],
    )
    def test_accepts_arithmetic_over_the_declared_metrics(self, expression: str) -> None:
        compiled = compile_formula(expression, variables=self.VARIABLES)
        assert set(compiled.used) == set(self.VARIABLES)

    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os').system('echo hi')",
            "open('secrets.txt').read() + a_nm",
            "os.system('rm -rf /') + a_nm + b_nm",
            "(lambda: a_nm)() + b_nm",
            "a_nm.__class__ + b_nm",
            "eval('a_nm') + b_nm",
        ],
    )
    def test_refuses_anything_that_would_reach_the_interpreter(
        self, expression: str
    ) -> None:
        with pytest.raises(FormulaError):
            compile_formula(expression, variables=self.VARIABLES)

    @pytest.mark.parametrize(
        "expression",
        [
            "a_nm + c_nm",
            "a_nm + b_nm + pi",
            "a_nm + b_nm + e",
        ],
    )
    def test_refuses_a_name_outside_the_chosen_metrics(self, expression: str) -> None:
        with pytest.raises(FormulaError, match="not one of the metrics"):
            compile_formula(expression, variables=self.VARIABLES)

    def test_refuses_a_permitted_function_used_as_a_bare_value(self) -> None:
        with pytest.raises(FormulaError, match="may be called but not used as a value"):
            compile_formula("min + a_nm + b_nm", variables=self.VARIABLES)

    @pytest.mark.parametrize(
        "expression",
        [
            "a_nm if b_nm else 0",
            "a_nm + b_nm > 1",
            "a_nm and b_nm",
            "[a_nm, b_nm][0]",
            "{'a': a_nm}['a'] + b_nm",
            "a_nm % b_nm",
            "a_nm // b_nm",
            "a_nm << 2",
        ],
    )
    def test_refuses_syntax_that_is_not_arithmetic(self, expression: str) -> None:
        with pytest.raises(FormulaError):
            compile_formula(expression, variables=self.VARIABLES)

    def test_refuses_an_exponent_large_enough_to_be_costly(self) -> None:
        with pytest.raises(FormulaError, match="exceeds the permitted magnitude"):
            compile_formula("a_nm ** 4000 + b_nm", variables=self.VARIABLES)

    def test_refuses_a_computed_exponent(self) -> None:
        with pytest.raises(FormulaError, match="must be a plain number"):
            compile_formula("a_nm ** b_nm", variables=self.VARIABLES)

    def test_refuses_a_string_literal(self) -> None:
        with pytest.raises(FormulaError, match="not a numeric literal"):
            compile_formula("a_nm + b_nm + 'x'", variables=self.VARIABLES)

    def test_refuses_a_boolean_literal(self) -> None:
        """``True`` is an ``int`` in Python, so it needs its own rejection."""

        with pytest.raises(FormulaError, match="not a numeric literal"):
            compile_formula("a_nm + b_nm * True", variables=self.VARIABLES)

    def test_refuses_an_unparseable_expression(self) -> None:
        with pytest.raises(FormulaError, match="does not parse"):
            compile_formula("a_nm +", variables=self.VARIABLES)

    def test_refuses_an_empty_expression(self) -> None:
        with pytest.raises(FormulaError, match="empty"):
            compile_formula("   ", variables=self.VARIABLES)

    def test_refuses_a_metric_that_never_appears(self) -> None:
        """A selected metric left out of the formula stops counting silently."""

        with pytest.raises(FormulaError, match="never appears in the formula"):
            compile_formula("a_nm", variables=self.VARIABLES)

    def test_evaluates_from_supplied_measurements(self) -> None:
        compiled = compile_formula("2 * a_nm - b_nm", variables=self.VARIABLES)
        assert compiled.evaluate({"a_nm": 0.5, "b_nm": 0.25}) == pytest.approx(0.75)

    def test_a_missing_measurement_is_an_error_not_a_zero(self) -> None:
        compiled = compile_formula("a_nm + b_nm", variables=self.VARIABLES)
        with pytest.raises(FormulaError, match="no measurement for b_nm"):
            compiled.evaluate({"a_nm": 0.5})

    def test_division_by_zero_is_reported_rather_than_raised(self) -> None:
        compiled = compile_formula("a_nm / b_nm", variables=self.VARIABLES)
        with pytest.raises(FormulaError, match="divides by zero"):
            compiled.evaluate({"a_nm": 0.5, "b_nm": 0.0})


# ---------------------------------------------------------------------------
# 2. Orientation: higher must mean better
# ---------------------------------------------------------------------------


class TestOrientation:
    """A reversed direction is the one scoring defect nothing downstream reveals."""

    def test_adding_a_minimized_metric_is_rejected(self) -> None:
        builder = QwenScoringStandardBuilder(
            ScriptedClient(
                _formula(f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}"),
                _formula(f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}"),
                _formula(f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}"),
            )
        )
        metrics = _verified_metrics(sense="minimize")
        result = builder.author_formula(QUESTION, metrics)
        assert result.status == "invalid"
        assert any(
            "must lower the score" in error for error in result.validation_errors
        ), result.validation_errors

    def test_subtracting_a_maximized_metric_is_rejected(self) -> None:
        builder = QwenScoringStandardBuilder(
            ScriptedClient(*[_formula(f"{VISIBLE_VARIABLE} - {INFRARED_VARIABLE}")] * 3)
        )
        result = builder.author_formula(QUESTION, _verified_metrics(sense="maximize"))
        assert result.status == "invalid"
        assert any("must raise the score" in e for e in result.validation_errors)

    def test_the_correct_sign_is_accepted(self) -> None:
        builder = QwenScoringStandardBuilder(
            ScriptedClient(_formula(f"{VISIBLE_VARIABLE} - {INFRARED_VARIABLE}"))
        )
        result = builder.author_formula(QUESTION, _verified_metrics(sense="minimize"))
        assert result.status == "authored"

    def test_a_plateau_from_min_is_not_mistaken_for_a_reversed_sign(self) -> None:
        """``min(...)`` is flat in one argument, and the prompt recommends it."""

        builder = QwenScoringStandardBuilder(
            ScriptedClient(_formula(f"min({VISIBLE_VARIABLE}, {INFRARED_VARIABLE})"))
        )
        result = builder.author_formula(QUESTION, _verified_metrics(sense="maximize"))
        assert result.status == "authored"

    def test_positive_weights_are_accepted(self) -> None:
        builder = QwenScoringStandardBuilder(
            ScriptedClient(
                _formula(f"3 * {VISIBLE_VARIABLE} - 0.25 * {INFRARED_VARIABLE}")
            )
        )
        result = builder.author_formula(QUESTION, _verified_metrics(sense="minimize"))
        assert result.status == "authored"

    def test_a_match_metric_must_be_penalised_by_distance(self) -> None:
        metrics = (
            FixedScoreMetric(
                variable=VISIBLE_VARIABLE,
                canonical_id="mean_reflectance@300-800nm",
                metric="mean_reflectance",
                sense="match",
                region={"wavelength_nm": [300.0, 800.0]},
                target=0.5,
            ),
        )
        rewarded = QwenScoringStandardBuilder(
            ScriptedClient(*[_formula(f"abs({VISIBLE_VARIABLE} - 0.5)")] * 3)
        ).author_formula(QUESTION, metrics)
        assert rewarded.status == "invalid"
        assert any("best at its target" in e for e in rewarded.validation_errors)

        penalised = QwenScoringStandardBuilder(
            ScriptedClient(_formula(f"-abs({VISIBLE_VARIABLE} - 0.5)"))
        ).author_formula(QUESTION, metrics)
        assert penalised.status == "authored"

    def test_a_repaired_formula_on_the_second_attempt_is_accepted(self) -> None:
        client = ScriptedClient(
            _formula(f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}"),
            _formula(f"{VISIBLE_VARIABLE} - {INFRARED_VARIABLE}"),
        )
        result = QwenScoringStandardBuilder(client).author_formula(
            QUESTION, _verified_metrics(sense="minimize")
        )
        assert result.status == "authored"
        assert result.attempts == 2
        assert "repair_request" in client.payloads[1]
        assert client.payloads[1]["repair_request"]["validation_errors"]


def _verified_metrics(*, sense: str) -> tuple[FixedScoreMetric, ...]:
    infrared = (
        INFRARED_ABSORPTION_UM if sense == "maximize" else INFRARED_ABSORPTION_MINIMIZED
    )
    builder = QwenScoringStandardBuilder(
        ScriptedClient(_selection(VISIBLE_REFLECTANCE, infrared))
    )
    selection = builder.select_metrics(QUESTION)
    assert selection.status == "selected", selection.validation_errors
    return selection.metrics


# ---------------------------------------------------------------------------
# 3. Stage one verifies against the catalogue
# ---------------------------------------------------------------------------


class TestMetricSelection:
    def test_a_valid_selection_is_normalized_to_nanometres(self) -> None:
        selection = QwenScoringStandardBuilder(
            ScriptedClient(_selection(VISIBLE_REFLECTANCE, INFRARED_ABSORPTION_UM))
        ).select_metrics(QUESTION)
        assert selection.status == "selected"
        assert [metric.variable for metric in selection.metrics] == [
            VISIBLE_VARIABLE,
            INFRARED_VARIABLE,
        ]
        infrared = selection.metrics[1]
        assert infrared.region["wavelength_nm"] == [5000.0, 13000.0]
        assert infrared.canonical_id == "mean_absorption@5000-13000nm"

    def test_the_stage_one_payload_carries_the_catalogue(self) -> None:
        client = ScriptedClient(_selection(VISIBLE_REFLECTANCE))
        QwenScoringStandardBuilder(client).select_metrics(QUESTION)
        payload = client.payloads[0]
        assert payload["user_question"] == QUESTION
        assert payload["capability_catalog"]["scoreable_metrics"] == list(
            SCOREABLE_METRICS
        )
        assert payload["fixed_rules"]["maximum_metrics"] == DEFAULT_MAXIMUM_METRICS

    @pytest.mark.parametrize(
        "proposal, expected",
        [
            (
                {"metric": "mean_reflectivity", "sense": "maximize", "region": {"wavelength_nm": [300, 800]}},
                "not computable",
            ),
            (
                {"metric": "layer_absorption", "sense": "maximize", "region": {"wavelength_nm": [300, 800]}},
                "report-only",
            ),
            (
                {"metric": "mean_reflectance", "sense": "biggest", "region": {"wavelength_nm": [300, 800]}},
                "is not allowed",
            ),
            (
                {"metric": "mean_reflectance", "sense": "match", "region": {"wavelength_nm": [300, 800]}},
                "requires a numeric target",
            ),
            (
                {"metric": "mean_reflectance", "sense": "maximize", "region": {"wavelength_nm": [800, 300]}},
                "",
            ),
            (
                {"metric": "band_emissivity_contrast", "sense": "maximize", "region": {"wavelength_nm": [300, 800]}},
                "preferred_wavelength_nm",
            ),
        ],
    )
    def test_an_illegal_proposal_is_rejected_and_regenerated(
        self, proposal: dict, expected: str
    ) -> None:
        client = ScriptedClient(*[_selection(proposal)] * 3)
        selection = QwenScoringStandardBuilder(client).select_metrics(QUESTION)
        if expected:
            assert selection.status == "invalid"
            assert selection.attempts == 3
            assert any(expected in error for error in selection.validation_errors)
            assert len(client.payloads) == 3
        else:
            # A reversed interval is a repairable slip, not an illegal request.
            assert selection.status == "selected"

    def test_a_rejection_sends_the_reason_back_for_repair(self) -> None:
        client = ScriptedClient(
            _selection({"metric": "mean_reflectivity", "sense": "maximize", "region": {"wavelength_nm": [300, 800]}}),
            _selection(VISIBLE_REFLECTANCE),
        )
        selection = QwenScoringStandardBuilder(client).select_metrics(QUESTION)
        assert selection.status == "selected"
        assert selection.attempts == 2
        repair = client.payloads[1]["repair_request"]
        assert any("mean_reflectivity" in e for e in repair["validation_errors"])
        assert client.payloads[1]["rejected_selection"]["metrics"]

    def test_an_unknown_name_is_answered_with_scoreable_names_only(self) -> None:
        """Offering report-only names to a scoring request invites a second failure."""

        client = ScriptedClient(
            *[_selection({"metric": "reflectance_mean", "sense": "maximize", "region": {"wavelength_nm": [300, 800]}})] * 3
        )
        selection = QwenScoringStandardBuilder(client).select_metrics(QUESTION)
        combined = " ".join(selection.validation_errors)
        assert "scoreable names are" in combined
        assert "layer_absorption" not in combined

    def test_more_metrics_than_permitted_are_rejected(self) -> None:
        proposals = [
            {
                "metric": "mean_reflectance",
                "sense": "maximize",
                "region": {"wavelength_nm": [300 + 100 * index, 400 + 100 * index]},
            }
            for index in range(DEFAULT_MAXIMUM_METRICS + 2)
        ]
        selection = QwenScoringStandardBuilder(
            ScriptedClient(*[_selection(*proposals)] * 3)
        ).select_metrics(QUESTION)
        assert selection.status == "invalid"
        assert any("above the maximum" in e for e in selection.validation_errors)

    def test_the_same_metric_and_band_twice_is_rejected(self) -> None:
        selection = QwenScoringStandardBuilder(
            ScriptedClient(*[_selection(VISIBLE_REFLECTANCE, VISIBLE_REFLECTANCE)] * 3)
        ).select_metrics(QUESTION)
        assert selection.status == "invalid"
        assert any("selected twice" in e for e in selection.validation_errors)

    def test_a_response_without_metrics_is_rejected(self) -> None:
        selection = QwenScoringStandardBuilder(
            ScriptedClient(*['{"rationale": "no metrics here"}'] * 3)
        ).select_metrics(QUESTION)
        assert selection.status == "invalid"
        assert any("no 'metrics' array" in e for e in selection.validation_errors)

    def test_an_unreachable_client_is_reported_as_unavailable(self) -> None:
        class Broken:
            model_name = "broken"

            def call(self, messages, *, max_tokens=4000, force_mock=None):
                raise TimeoutError("upstream is down")

        selection = QwenScoringStandardBuilder(Broken()).select_metrics(QUESTION)
        assert selection.status == "unavailable"
        assert selection.validation_errors == ("TimeoutError: upstream is down",)


# ---------------------------------------------------------------------------
# 4. Both stages together
# ---------------------------------------------------------------------------


class TestBuildBothStages:
    def test_a_complete_standard_records_its_provenance(self) -> None:
        standard = _standard()
        assert standard.locked is True
        assert standard.question_digest
        assert standard.formula == f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}"
        assert set(standard.formula_variables) == {VISIBLE_VARIABLE, INFRARED_VARIABLE}
        assert standard.provenance["model"] == "scripted-planner"
        assert standard.provenance["metric_selection_attempts"] == 1
        assert standard.provenance["formula_attempts"] == 1

    def test_the_second_stage_receives_only_verified_metrics(self) -> None:
        client = ScriptedClient(
            _selection(VISIBLE_REFLECTANCE, INFRARED_ABSORPTION_UM),
            _formula(f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}"),
        )
        QwenScoringStandardBuilder(client).build(QUESTION)
        verified = client.payloads[1]["verified_metrics"]
        assert [row["variable"] for row in verified] == [
            VISIBLE_VARIABLE,
            INFRARED_VARIABLE,
        ]
        assert verified[1]["region"]["wavelength_nm"] == [5000.0, 13000.0]
        assert verified[0]["summary"]

    def test_a_failed_first_stage_never_reaches_the_second(self) -> None:
        client = ScriptedClient(*['{"metrics": []}'] * 3)
        result = QwenScoringStandardBuilder(client).build(QUESTION)
        assert result.status == "invalid"
        assert result.standard is None
        assert result.formula is None
        assert len(client.payloads) == 3

    def test_a_failed_second_stage_keeps_the_first_stage_evidence(self) -> None:
        client = ScriptedClient(
            _selection(VISIBLE_REFLECTANCE, INFRARED_ABSORPTION_MINIMIZED),
            *[_formula(f"{VISIBLE_VARIABLE} + {INFRARED_VARIABLE}")] * 3,
        )
        result = QwenScoringStandardBuilder(client).build(QUESTION)
        assert result.status == "invalid"
        assert result.standard is None
        assert result.selection is not None and result.selection.status == "selected"
        assert result.formula is not None and result.formula.status == "invalid"

    def test_usage_is_collected_from_both_stages(self) -> None:
        client = ScriptedClient(
            _selection(VISIBLE_REFLECTANCE),
            _formula(VISIBLE_VARIABLE),
        )
        result = QwenScoringStandardBuilder(client).build(QUESTION)
        assert result.status == "standardized"
        assert len(result.usage) == 2

    def test_the_standard_cannot_be_edited_after_it_is_built(self) -> None:
        standard = _standard()
        with pytest.raises(Exception):
            standard.formula = "0"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 5. Scoring reads what the runtime measured
# ---------------------------------------------------------------------------


class TestScoreReadsObserved:
    def test_a_complete_measurement_set_is_scored(self) -> None:
        outcome = _standard().score(_attainment(0.92, 0.71))
        assert outcome.ok
        assert outcome.value == pytest.approx(1.63)
        assert outcome.values == {
            VISIBLE_VARIABLE: pytest.approx(0.92),
            INFRARED_VARIABLE: pytest.approx(0.71),
        }

    def test_a_report_is_accepted_either_wrapped_or_bare(self) -> None:
        standard = _standard()
        wrapped = _attainment(0.4, 0.6)
        bare = wrapped["target_attainment"]
        assert standard.score(wrapped).value == standard.score(bare).value

    def test_a_missing_measurement_makes_the_candidate_unscoreable(self) -> None:
        """A partial sum would look like a worse design, not a missing one."""

        rows = _attainment(0.9, 0.9)
        rows["target_attainment"].pop(f"{FIXED_SCORE_OBJECTIVE_PREFIX}{INFRARED_VARIABLE}")
        outcome = _standard().score(rows)
        assert not outcome.ok
        assert outcome.value is None
        assert outcome.missing == (INFRARED_VARIABLE,)

    def test_a_measurement_found_under_another_identifier_is_still_used(self) -> None:
        standard = _standard()
        outcome = standard.score(
            {
                "visible_reflect": {
                    "metric": "mean_reflectance",
                    "observed": 0.3,
                    "region": {"wavelength_nm": [300, 800]},
                },
                "infrared_absorb": {
                    "metric": "mean_absorption",
                    "observed": 0.4,
                    "region": {"wavelength_nm": [5000, 13000]},
                },
            }
        )
        assert outcome.ok
        assert outcome.value == pytest.approx(0.7)

    def test_a_row_that_only_borrows_the_identifier_is_refused(self) -> None:
        """The prefix is a label, so the metric and band decide what is read."""

        standard = _standard()
        rows = _attainment(0.9, 0.9)
        rows["target_attainment"][f"{FIXED_SCORE_OBJECTIVE_PREFIX}{VISIBLE_VARIABLE}"] = {
            "metric": "mean_transmittance",
            "observed": 1.0,
            "region": {"wavelength_nm": [300.0, 800.0]},
        }
        outcome = standard.score(rows)
        assert not outcome.ok
        assert VISIBLE_VARIABLE in outcome.missing
        assert any("does not carry" in error for error in outcome.errors)

    def test_a_row_measured_over_a_different_band_is_refused(self) -> None:
        standard = _standard()
        rows = _attainment(0.9, 0.9)
        rows["target_attainment"][f"{FIXED_SCORE_OBJECTIVE_PREFIX}{VISIBLE_VARIABLE}"][
            "region"
        ] = {"wavelength_nm": [400.0, 700.0]}
        outcome = standard.score(rows)
        assert not outcome.ok
        assert VISIBLE_VARIABLE in outcome.missing

    @pytest.mark.parametrize("observed", [None, float("nan"), float("inf"), [0.5], {}])
    def test_a_measurement_that_is_not_a_finite_number_is_refused(
        self, observed
    ) -> None:
        standard = _standard()
        rows = _attainment(0.9, 0.9)
        rows["target_attainment"][f"{FIXED_SCORE_OBJECTIVE_PREFIX}{VISIBLE_VARIABLE}"][
            "observed"
        ] = observed
        outcome = standard.score(rows)
        assert not outcome.ok
        assert VISIBLE_VARIABLE in outcome.missing

    def test_a_numeric_string_is_read_as_its_number(self) -> None:
        """Deliberate leniency: refusing would lose a route over a format, not a value."""

        standard = _standard()
        rows = _attainment(0.9, 0.9)
        rows["target_attainment"][f"{FIXED_SCORE_OBJECTIVE_PREFIX}{VISIBLE_VARIABLE}"][
            "observed"
        ] = "0.5"
        outcome = standard.score(rows)
        assert outcome.ok
        assert outcome.values[VISIBLE_VARIABLE] == pytest.approx(0.5)

    def test_an_empty_report_is_unscoreable_rather_than_zero(self) -> None:
        outcome = _standard().score({})
        assert not outcome.ok
        assert set(outcome.missing) == {VISIBLE_VARIABLE, INFRARED_VARIABLE}

    def test_ranking_puts_the_best_first_and_the_unscoreable_last(self) -> None:
        standard = _standard()
        weak = _attainment(0.1, 0.1)
        strong = _attainment(0.8, 0.8)
        broken = {"target_attainment": {}}
        order = standard.rank([weak, broken, strong])
        assert [index for index, _ in order] == [2, 0, 1]
        assert order[0][1].value == pytest.approx(1.6)
        assert not order[-1][1].ok

    def test_ranking_accepts_candidate_summaries(self) -> None:
        standard = _standard()
        candidates = [
            {"candidate_id": "a", "objective_report": _attainment(0.2, 0.2)},
            {"candidate_id": "b", "objective_report": _attainment(0.7, 0.7)},
        ]
        assert [index for index, _ in standard.rank(candidates)] == [1, 0]


# ---------------------------------------------------------------------------
# 6. The injected objectives must not steer the search
# ---------------------------------------------------------------------------


class TestInjectedObjectivesDoNotSteer:
    """The whole point of separate routes is that they pursue different things.

    A frozen scoring metric declared with its own direction would enter the
    harness's aggregate soft score and, through the feedback controller, push
    every route toward the same objective.  Declaring it ``report`` keeps the
    measurement and drops the pull, and this test pins that: the aggregate must
    stay exactly zero while every ``observed`` value is still present.
    """

    @staticmethod
    def _simulate(start_nm: float, stop_nm: float, points: int):
        task = SimulationTask(
            stack=StackSpec(
                layers=(LayerSpec(None, 120.0, constant_n=2.4, constant_k=0.35),),
                incident=MediumSpec.air(),
                exit=MediumSpec(constant_n=1.52),
            ),
            spectrum=SpectralGrid(start_nm=start_nm, stop_nm=stop_nm, points=points),
            illumination=IlluminationSpec((0.0,), ("unpolarized",)),
        )
        return TMMWorkbench(MaterialRegistry()).simulate(task)

    def test_every_injected_objective_is_declared_report_only(self) -> None:
        standard = _standard()
        preferences = standard.objective_preferences()
        assert [preference.sense for preference in preferences] == ["report", "report"]
        assert all(
            is_fixed_score_objective_id(preference.objective_id)
            for preference in preferences
        )
        assert all(
            preference.admission_role == "score_only" for preference in preferences
        )

    def test_the_runtime_measures_them_without_scoring_them(self) -> None:
        standard = _standard()
        requirement = standard.spectral_requirement()
        grid, _ = widen_spectral_grid(
            {"start_nm": 300.0, "stop_nm": 800.0, "points": 101}, requirement
        )
        result = self._simulate(grid["start_nm"], grid["stop_nm"], grid["points"])
        report = evaluate_declared_objectives(standard.objective_preferences(), result)

        assert report.aggregate_soft_score == 0.0
        assert report.weighted_directional_loss == 0.0
        assert set(report.target_attainment) == {
            preference.objective_id
            for preference in standard.objective_preferences()
        }
        for row in report.target_attainment.values():
            assert row["role"] == "report_only"
            assert row["soft_score"] is None
            assert 0.0 <= float(row["observed"]) <= 1.0

    def test_the_frozen_formula_scores_a_real_simulation(self) -> None:
        standard = _standard()
        grid, _ = widen_spectral_grid(
            {"start_nm": 300.0, "stop_nm": 800.0, "points": 101},
            standard.spectral_requirement(),
        )
        result = self._simulate(grid["start_nm"], grid["stop_nm"], grid["points"])
        report = evaluate_declared_objectives(standard.objective_preferences(), result)
        outcome = standard.score(report)
        assert outcome.ok
        assert outcome.value == pytest.approx(
            sum(outcome.values.values()), rel=1e-12
        )

    def test_the_injected_objectives_pass_the_task_contract(self) -> None:
        """A frozen objective outside the simulated grid is a compile failure."""

        from optomind_optics.harness.design_task import _validate_band

        standard = _standard()
        requirement = standard.spectral_requirement()
        for preference in standard.objective_preferences():
            low, high = _validate_band(
                preference.region["wavelength_nm"], "wavelength_nm"
            )
            assert requirement.lower_nm <= low <= high <= requirement.upper_nm


# ---------------------------------------------------------------------------
# 7. Making the run cover the frozen bands
# ---------------------------------------------------------------------------


class TestSpectralWidening:
    def test_a_grid_that_already_covers_the_bands_is_untouched(self) -> None:
        standard = _standard()
        original = {"start_nm": 200.0, "stop_nm": 14000.0, "points": 501}
        grid, warnings = widen_spectral_grid(
            dict(original), standard.spectral_requirement()
        )
        assert grid == original
        assert not any("widened" in warning for warning in warnings)

    def test_a_narrow_grid_is_widened_to_reach_every_band(self) -> None:
        standard = _standard()
        requirement = standard.spectral_requirement()
        grid, warnings = widen_spectral_grid(
            {"start_nm": 300.0, "stop_nm": 800.0, "points": 101}, requirement
        )
        assert grid["start_nm"] <= requirement.lower_nm
        assert grid["stop_nm"] >= requirement.upper_nm
        assert grid["points"] > 101
        assert any("widened the simulation grid" in w for w in warnings)

    def test_widening_keeps_the_sample_count_bounded(self) -> None:
        """Spanning the visible and the long-wave infrared must not explode."""

        standard = _standard()
        grid, _ = widen_spectral_grid(
            {"start_nm": 300.0, "stop_nm": 800.0, "points": 1001},
            standard.spectral_requirement(),
            maximum_points=801,
        )
        assert grid["points"] == 801

    def test_a_band_left_too_coarse_is_flagged(self) -> None:
        standard = _standard()
        _, warnings = widen_spectral_grid(
            {"start_nm": 300.0, "stop_nm": 800.0, "points": 21},
            standard.spectral_requirement(),
            maximum_points=25,
        )
        assert any(
            f"below the {MINIMUM_SAMPLES_PER_BAND}" in warning for warning in warnings
        )

    def test_an_explicit_sample_list_is_left_alone_but_checked(self) -> None:
        standard = _standard()
        grid, warnings = widen_spectral_grid(
            {"values_nm": [300.0, 500.0, 800.0]}, standard.spectral_requirement()
        )
        assert grid == {"values_nm": [300.0, 500.0, 800.0]}
        assert any("explicit wavelength list" in warning for warning in warnings)

    @pytest.mark.parametrize(
        "grid",
        [
            {},
            {"start_nm": 300.0},
            {"start_nm": 800.0, "stop_nm": 300.0, "points": 11},
            {"start_nm": 300.0, "stop_nm": 800.0, "points": 1},
        ],
    )
    def test_a_grid_that_cannot_be_widened_is_reported_not_corrupted(
        self, grid: dict
    ) -> None:
        standard = _standard()
        updated, warnings = widen_spectral_grid(
            dict(grid), standard.spectral_requirement()
        )
        assert updated == grid
        assert warnings


# ---------------------------------------------------------------------------
# 8. The two spellings of a metric reference stay in step
# ---------------------------------------------------------------------------


class TestMetricNaming:
    @pytest.mark.parametrize("metric", SCOREABLE_METRICS)
    def test_the_formula_variable_is_a_usable_identifier(self, metric: str) -> None:
        region = (
            {
                "preferred_wavelength_nm": [8000.0, 13000.0],
                "suppressed_wavelength_nm": [300.0, 2500.0],
            }
            if metric == "band_emissivity_contrast"
            else {"wavelength_nm": [300.0, 800.0]}
        )
        variable = formula_variable_name(metric, region)
        assert variable.isidentifier()
        compiled = compile_formula(variable, variables=[variable])
        assert compiled.used == (variable,)

    def test_the_two_spellings_agree_exactly_when_the_reference_agrees(self) -> None:
        first = {"wavelength_nm": [300.0, 800.0]}
        second = {"wavelength_nm": [300.0, 801.0]}
        assert canonical_metric_id("mean_reflectance", first) != canonical_metric_id(
            "mean_reflectance", second
        )
        assert formula_variable_name("mean_reflectance", first) != formula_variable_name(
            "mean_reflectance", second
        )
        assert formula_variable_name("mean_reflectance", first) == "mean_reflectance_300_800nm"

    def test_a_fractional_band_still_yields_an_identifier(self) -> None:
        variable = formula_variable_name(
            "mean_absorption", {"wavelength_nm": [1550.5, 1560.25]}
        )
        assert variable.isidentifier()


# ---------------------------------------------------------------------------
# 9. The compiler attaches the standard to every experiment it emits
# ---------------------------------------------------------------------------


def _compiler_reply() -> dict:
    """One accepted draft, reused so the tests differ only in the standard."""

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


def _spectrum(experiment) -> dict:
    """``tmm_task`` stays a plain mapping until the engine validates it."""

    simulation = experiment.tmm_task.get("simulation") or {}
    return simulation.get("spectrum") or {}


def _targets(experiment) -> list:
    optimization = experiment.tmm_task.get("optimization") or {}
    return list(optimization.get("targets") or ())


def _compile_with(standard: ScoringStandard | None):
    compiler = QwenTMMTaskCompiler(
        client=_CompilerClient([_compiler_reply()]), scoring_standard=standard
    )
    result = compiler.compile(
        "Design a single-layer antireflection coating on glass over 500-600 nm."
    )
    assert result.status == "compiled", result.validation_errors
    assert result.task is not None
    return result


class TestCompilerAttachesTheStandard:
    def test_every_experiment_reports_the_frozen_metrics(self) -> None:
        standard = _standard()
        task = _compile_with(standard).task
        wanted = {
            preference.objective_id for preference in standard.objective_preferences()
        }
        for experiment in task.experiments:
            present = {item.objective_id for item in experiment.objectives}
            assert wanted <= present

    def test_the_attached_objectives_only_report(self) -> None:
        """They are how a route is measured, not what it is told to chase."""

        task = _compile_with(_standard()).task
        for experiment in task.experiments:
            for item in experiment.objectives:
                if is_fixed_score_objective_id(item.objective_id):
                    assert item.sense == "report"

    def test_the_route_keeps_the_targets_it_declared(self) -> None:
        without = _compile_with(None).task
        with_standard = _compile_with(_standard()).task
        for plain, ranked in zip(without.experiments, with_standard.experiments):
            assert _targets(plain) == _targets(ranked)

    def test_a_routes_own_objectives_survive_alongside_the_frozen_ones(self) -> None:
        without = _compile_with(None).task
        with_standard = _compile_with(_standard()).task
        for plain, ranked in zip(without.experiments, with_standard.experiments):
            own = {item.objective_id for item in plain.objectives}
            after = {item.objective_id for item in ranked.objectives}
            assert own <= after

    def test_the_grid_is_widened_to_span_every_ranked_band(self) -> None:
        """A band outside the grid is a compilation failure, not a zero."""

        task = _compile_with(_standard()).task
        requirement = _standard().spectral_requirement()
        for experiment in task.experiments:
            spectrum = _spectrum(experiment)
            assert float(spectrum["start_nm"]) <= requirement.lower_nm + 1e-6
            assert float(spectrum["stop_nm"]) >= requirement.upper_nm - 1e-6

    def test_the_grid_is_left_alone_without_a_standard(self) -> None:
        task = _compile_with(None).task
        for experiment in task.experiments:
            assert float(_spectrum(experiment)["stop_nm"]) < 13000.0

    def test_the_freeze_note_says_targets_were_left_to_the_route(self) -> None:
        standard = _standard()
        task = _compile_with(standard).task
        notes = " ".join(task.metadata.get("objective_freeze") or ())
        assert "left as this route declared them" in notes
        assert standard.formula in notes

    def test_the_notes_record_the_widening_and_the_attachment(self) -> None:
        task = _compile_with(_standard()).task
        notes = " ".join(task.metadata.get("objective_freeze") or ())
        assert "13000" in notes
        assert "frozen scoring objective" in notes

    def test_without_a_standard_nothing_is_attached(self) -> None:
        task = _compile_with(None).task
        for experiment in task.experiments:
            for item in experiment.objectives:
                assert not is_fixed_score_objective_id(item.objective_id)

    def test_compiling_twice_attaches_the_same_objectives(self) -> None:
        """The standard is built once, so two routes must be ranked alike."""

        standard = _standard()
        first = _compile_with(standard).task
        second = _compile_with(standard).task
        assert [
            sorted(item.objective_id for item in experiment.objectives)
            for experiment in first.experiments
        ] == [
            sorted(item.objective_id for item in experiment.objectives)
            for experiment in second.experiments
        ]
