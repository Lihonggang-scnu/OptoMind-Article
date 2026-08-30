"""The run-wide scoring standard: choose the metrics once, then freeze a formula.

Before this module existed, the number that decided which design won was
whatever the first route that compiled happened to declare, frozen afterwards
for everyone else.  Two consequences followed.  The winning route was ranked by
its own objectives while later routes were ranked by objectives they never
chose, and the number itself was not comparable: the harness's soft score
divides by a scale derived from each objective's own target, so two candidates
carrying different objective sets produce scores that cannot be ordered.

The standard is built in two stages before any experiment runs.  A model reads
the user's request together with the machine-readable capability catalogue and
proposes the metrics; a local check verifies every proposal against that
catalogue and sends the failures back for regeneration, so no invented or
report-only metric can enter.  A second model then writes one arithmetic
expression over the verified metrics, and a local check parses it under a
whitelist and probes it for reversed directions.  What comes out is frozen for
the whole study.

Three properties are deliberate:

* The expression is evaluated here, by :meth:`ScoringStandard.score`, from the
  ``observed`` values the deterministic runtime measured.  No route reports its
  own score, and no route can restate the criteria it is judged by.
* The injected objectives carry ``sense="report"``.  They exist only so the
  runtime measures the numbers the formula needs; they contribute nothing to
  the harness's own aggregate and nothing to what any optimizer chases, so
  freezing the ranking does not quietly make every route pursue the same thing.
* Model text is never executed.  The expression is parsed with :mod:`ast` and
  evaluated node by node against a whitelist of operators and three functions.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from config.qwen_config import get_cost_tracker

from .design_task import ObjectivePreference
from .metric_catalog import (
    CATALOG_SCHEMA_VERSION,
    METRIC_CATALOG,
    SCOREABLE_METRICS,
    catalog_document,
    fixed_score_objective_id,
    verify_metric_selection,
)


_PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts" / "optical_harness"
DEFAULT_METRIC_SELECTION_PROMPT = _PROMPT_ROOT / "TMM Metric Selection.txt"
DEFAULT_SCORING_FORMULA_PROMPT = _PROMPT_ROOT / "TMM Scoring Formula.txt"

SCORING_STANDARD_SCHEMA_VERSION = "tmm-scoring-standard.v1"

# Both stages are planning-class judgement over a short payload; route them to
# the same planning tier the other pre-execution stages use.
SCORING_STANDARD_MODEL = "qwen3.5-plus"

# The payloads are the user's question plus the catalogue, and the answers are a
# handful of metric rows or a single expression.  Neither stage needs the wide
# ceiling that route planning does.
SCORING_STANDARD_MAX_TOKENS = 4000

# Fewer metrics than routes, deliberately.  Every extra metric dilutes the ones
# that matter and adds an objective the runtime must measure on every
# candidate.  Adjustable, like the route count, because a request that names
# four competing bands genuinely needs four.
DEFAULT_MAXIMUM_METRICS = 4

# Metric selection is cheap, and getting it wrong poisons the comparability of
# every route in the run, so it is worth one more retry than route planning.
DEFAULT_MAXIMUM_ATTEMPTS = 3

# The widest uniform grid the standard will ask for.  Scoring two bands that sit
# decades apart forces the simulation grid to span both; without a ceiling, a
# request covering the visible and the long-wave infrared would multiply the
# sample count by the span ratio and the forward solve with it.
DEFAULT_MAXIMUM_SPECTRAL_POINTS = 2001

# Below this many samples a band mean stops describing the band.  One sample is
# enough for the reduction to be defined, which is why this is a warning rather
# than a rejection: refusing would abandon the run over a resolution choice the
# user never made.
MINIMUM_SAMPLES_PER_BAND = 5


class ScoringStandardClient(Protocol):
    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# The whitelisted expression language
# ---------------------------------------------------------------------------


class FormulaError(ValueError):
    """A scoring expression the whitelist refuses, with the reason to send back."""


_ALLOWED_CALLS: Dict[str, Any] = {"min": min, "max": max, "abs": abs}

_ALLOWED_BINARY_OPERATORS: Dict[type, Any] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a**b,
}

# An exponent is the one operator that can turn a short expression into an
# unbounded amount of work, so it must be a small literal rather than anything
# computed.
_MAXIMUM_EXPONENT = 8

# A scoring expression over at most a handful of metrics has no legitimate
# reason to be large; a ceiling keeps a degenerate response from becoming a
# deep recursion.
_MAXIMUM_NODES = 400


class CompiledFormula(BaseModel):
    """A scoring expression that parsed under the whitelist."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    declared: tuple[str, ...]
    used: tuple[str, ...]

    def evaluate(self, values: Mapping[str, float]) -> float:
        """Evaluate against one candidate's measurements."""

        missing = [name for name in self.used if name not in values]
        if missing:
            raise FormulaError(
                f"no measurement for {', '.join(sorted(missing))}"
            )
        environment = {name: float(values[name]) for name in self.used}
        tree = ast.parse(self.text, mode="eval")
        result = _evaluate_node(tree.body, environment)
        if not math.isfinite(result):
            raise FormulaError(
                f"the expression evaluated to {result}, which cannot be ranked"
            )
        return float(result)


def _describe_node(node: ast.AST) -> str:
    """Name a rejected construct the way the prompt names it."""

    return {
        ast.Attribute: "attribute access",
        ast.Subscript: "indexing",
        ast.Compare: "a comparison",
        ast.IfExp: "a conditional expression",
        ast.BoolOp: "a boolean operator",
        ast.Lambda: "a lambda",
        ast.ListComp: "a comprehension",
        ast.GeneratorExp: "a comprehension",
        ast.Dict: "a dict literal",
        ast.List: "a list literal",
        ast.JoinedStr: "an f-string",
        ast.NamedExpr: "an assignment expression",
    }.get(type(node), f"{type(node).__name__} syntax")


def _validate_node(node: ast.AST, declared: set[str], used: set[str]) -> None:
    if isinstance(node, ast.Expression):
        _validate_node(node.body, declared, used)
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise FormulaError(
                f"{node.value!r} is not a numeric literal; the expression may "
                "contain only numbers, the given variable names, arithmetic, "
                "and min/max/abs"
            )
        return
    if isinstance(node, ast.Name):
        if node.id in _ALLOWED_CALLS:
            raise FormulaError(
                f"{node.id} may be called but not used as a value"
            )
        if node.id not in declared:
            raise FormulaError(
                f"{node.id!r} is not one of the metrics chosen for this study; "
                f"use only: {', '.join(sorted(declared))}"
            )
        used.add(node.id)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.UAdd, ast.USub)):
            raise FormulaError(f"{_describe_node(node.op)} is not allowed")
        _validate_node(node.operand, declared, used)
        return
    if isinstance(node, ast.BinOp):
        if type(node.op) not in _ALLOWED_BINARY_OPERATORS:
            raise FormulaError(
                f"the operator {type(node.op).__name__} is not allowed; use "
                "only + - * / ** and parentheses"
            )
        if isinstance(node.op, ast.Pow):
            exponent = node.right
            if isinstance(exponent, ast.UnaryOp) and isinstance(
                exponent.op, (ast.UAdd, ast.USub)
            ):
                exponent = exponent.operand
            if not isinstance(exponent, ast.Constant) or isinstance(
                exponent.value, bool
            ):
                raise FormulaError(
                    "an exponent must be a plain number, not an expression"
                )
            if abs(float(exponent.value)) > _MAXIMUM_EXPONENT:
                raise FormulaError(
                    f"the exponent {exponent.value} exceeds the permitted "
                    f"magnitude of {_MAXIMUM_EXPONENT}"
                )
        _validate_node(node.left, declared, used)
        _validate_node(node.right, declared, used)
        return
    if isinstance(node, ast.Call):
        function = node.func
        if not isinstance(function, ast.Name):
            raise FormulaError(
                f"a function reached through {_describe_node(function)} cannot be "
                f"called; only {', '.join(sorted(_ALLOWED_CALLS))} may be called"
            )
        if function.id not in _ALLOWED_CALLS:
            raise FormulaError(
                f"{function.id} is not an available function; only "
                f"{', '.join(sorted(_ALLOWED_CALLS))} may be called"
            )
        if node.keywords:
            raise FormulaError(
                f"{function.id} takes plain arguments, not keyword arguments"
            )
        if any(isinstance(argument, ast.Starred) for argument in node.args):
            raise FormulaError(f"{function.id} does not accept argument unpacking")
        expected = 1 if function.id == "abs" else 2
        if function.id == "abs" and len(node.args) != 1:
            raise FormulaError(f"abs takes exactly one argument, got {len(node.args)}")
        if function.id != "abs" and len(node.args) < expected:
            raise FormulaError(
                f"{function.id} needs at least {expected} arguments, "
                f"got {len(node.args)}"
            )
        for argument in node.args:
            _validate_node(argument, declared, used)
        return
    raise FormulaError(f"{_describe_node(node)} is not allowed in a scoring expression")


def _evaluate_node(node: ast.AST, environment: Mapping[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return float(environment[node.id])
    if isinstance(node, ast.UnaryOp):
        value = _evaluate_node(node.operand, environment)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, environment)
        right = _evaluate_node(node.right, environment)
        try:
            return float(_ALLOWED_BINARY_OPERATORS[type(node.op)](left, right))
        except ZeroDivisionError:
            raise FormulaError(
                "the expression divides by zero on this candidate's measurements"
            ) from None
        except (OverflowError, ValueError) as exc:
            raise FormulaError(f"the expression could not be evaluated: {exc}") from None
    if isinstance(node, ast.Call):
        arguments = [_evaluate_node(argument, environment) for argument in node.args]
        return float(_ALLOWED_CALLS[node.func.id](*arguments))  # type: ignore[union-attr]
    raise FormulaError(f"{_describe_node(node)} is not allowed in a scoring expression")


def compile_formula(text: Any, *, variables: Sequence[str]) -> CompiledFormula:
    """Parse a scoring expression, or explain why it cannot be used.

    Rejection is by whitelist rather than by blacklist: a construct is refused
    unless it is one of numbers, the declared variable names, ``+ - * / **``,
    parentheses, and ``min``/``max``/``abs``.  Model text therefore never
    reaches an evaluator that could resolve a name, read an attribute, or call
    anything else.
    """

    expression = str(text or "").strip()
    if not expression:
        raise FormulaError("the formula is empty")
    declared = {str(name) for name in variables}
    if not declared:
        raise FormulaError("no verified metrics were supplied to write a formula over")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"the formula does not parse: {exc.msg}") from None
    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > _MAXIMUM_NODES:
        raise FormulaError(
            f"the formula has {node_count} elements, above the permitted "
            f"{_MAXIMUM_NODES}; a scoring expression should be readable"
        )
    used: set[str] = set()
    _validate_node(tree, declared, used)
    unused = sorted(declared - used)
    if unused:
        raise FormulaError(
            f"{', '.join(unused)} never appears in the formula, so "
            f"{'those requirements' if len(unused) > 1 else 'that requirement'} "
            "would silently stop counting; either use it or do not select it"
        )
    return CompiledFormula(
        text=expression,
        declared=tuple(sorted(declared)),
        used=tuple(sorted(used)),
    )


# ---------------------------------------------------------------------------
# Orientation: higher must mean better
# ---------------------------------------------------------------------------

# The measurements are fractions, so probing inside the unit interval covers the
# whole range a real candidate can occupy.  Several probe points rather than one,
# because min(...) and max(...) are flat in one argument at any single point and
# a sign error hiding on a plateau would pass a single-point check.
_ORIENTATION_PROBES = (0.25, 0.5, 0.75)
_ORIENTATION_DELTA = 0.1
_ORIENTATION_TOLERANCE = 1e-9


def _orientation_errors(
    formula: CompiledFormula,
    metrics: Sequence["FixedScoreMetric"],
) -> tuple[str, ...]:
    """Check that each metric moves the score the way its direction says.

    A sign error is the one defect in a scoring formula that cannot be noticed
    afterwards: every route is ranked consistently, just backwards, and the
    study concludes that the worst design won.  Probing the expression costs
    nothing and catches it before any experiment runs.
    """

    errors: list[str] = []
    by_variable = {metric.variable: metric for metric in metrics}
    for variable in formula.used:
        metric = by_variable.get(variable)
        if metric is None or metric.sense == "report":
            continue
        for probe in _ORIENTATION_PROBES:
            base_values = {
                name: (
                    _match_probe(by_variable.get(name), probe)
                    if name != variable
                    else probe
                )
                for name in formula.used
            }
            try:
                base = formula.evaluate(base_values)
                higher = formula.evaluate({**base_values, variable: probe + _ORIENTATION_DELTA})
                lower = formula.evaluate({**base_values, variable: probe - _ORIENTATION_DELTA})
            except FormulaError:
                # An expression that cannot be evaluated at a probe point is
                # reported by the caller's own evaluation, not misattributed to
                # this metric's direction.
                break
            if metric.sense == "maximize":
                wrong = (
                    higher < base - _ORIENTATION_TOLERANCE
                    or lower > base + _ORIENTATION_TOLERANCE
                )
                expectation = "raise"
            elif metric.sense == "minimize":
                wrong = (
                    higher > base + _ORIENTATION_TOLERANCE
                    or lower < base - _ORIENTATION_TOLERANCE
                )
                expectation = "lower"
            else:
                target = float(metric.target if metric.target is not None else probe)
                try:
                    at_target = formula.evaluate({**base_values, variable: target})
                    above = formula.evaluate(
                        {**base_values, variable: target + _ORIENTATION_DELTA}
                    )
                    below = formula.evaluate(
                        {**base_values, variable: target - _ORIENTATION_DELTA}
                    )
                except FormulaError:
                    break
                if (
                    above > at_target + _ORIENTATION_TOLERANCE
                    or below > at_target + _ORIENTATION_TOLERANCE
                ):
                    errors.append(
                        f"{variable} should be best at its target of {target:g}, but "
                        "moving away from the target raises the score; subtract the "
                        f"distance instead, for example - abs({variable} - {target:g})"
                    )
                break
            if wrong:
                errors.append(
                    f"{variable} is a '{metric.sense}' metric, so a larger value must "
                    f"{expectation} the score, but the formula moves the other way "
                    f"(score {base:.6g} at {probe:g}, {higher:.6g} at "
                    f"{probe + _ORIENTATION_DELTA:g}); "
                    f"{'add' if metric.sense == 'maximize' else 'subtract'} the term"
                )
                break
    return tuple(dict.fromkeys(errors))


def _match_probe(metric: "FixedScoreMetric | None", probe: float) -> float:
    if metric is not None and metric.sense == "match" and metric.target is not None:
        return float(metric.target)
    return probe


# ---------------------------------------------------------------------------
# The standard
# ---------------------------------------------------------------------------


class FixedScoreMetric(BaseModel):
    """One verified measurement the whole study is ranked by."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    variable: str
    canonical_id: str
    metric: str
    sense: str
    region: Dict[str, Any] = Field(default_factory=dict)
    target: float | None = None
    rationale: str = ""

    @property
    def objective_id(self) -> str:
        return fixed_score_objective_id(self.variable)

    @property
    def interval_keys(self) -> tuple[str, ...]:
        spec = METRIC_CATALOG.get(self.metric)
        return spec.required_region_keys if spec else ("wavelength_nm",)

    def bands_nm(self) -> tuple[tuple[float, float], ...]:
        bands: list[tuple[float, float]] = []
        for key in self.interval_keys:
            interval = self.region.get(key)
            if isinstance(interval, (list, tuple)) and len(interval) == 2:
                bands.append((float(interval[0]), float(interval[1])))
        return tuple(bands)

    def objective_preference(self) -> ObjectivePreference:
        """The objective injected so the runtime measures this number.

        ``sense="report"`` on purpose.  The direction that matters lives in the
        frozen formula; declaring it here as well would feed this metric into
        the harness's own aggregate score and, through it, into what the search
        pursues, which would make every route chase the same thing.
        """

        return ObjectivePreference(
            objective_id=self.objective_id,
            metric=self.metric,
            sense="report",
            weight=1.0,
            target=self.target,
            region=dict(self.region),
            admission_role="score_only",
        )


class SpectralRequirement(BaseModel):
    """The wavelength coverage the frozen metrics oblige every run to simulate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lower_nm: float
    upper_nm: float
    bands_nm: tuple[tuple[float, float], ...] = ()


class ScoreOutcome(BaseModel):
    """The frozen formula's verdict on one candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    value: float | None = None
    values: Dict[str, float] = Field(default_factory=dict)
    missing: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()


class ScoringStandard(BaseModel):
    """The metrics and the one expression every route in a study is ranked by.

    Frozen in both senses: the model is immutable, and by contract nothing
    downstream may add a metric, change a coefficient, or substitute a score of
    its own.  Persisting it alongside the run is what makes a reported ranking
    checkable after the fact.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = SCORING_STANDARD_SCHEMA_VERSION
    catalog_schema_version: str = CATALOG_SCHEMA_VERSION
    locked: bool = True
    question_digest: str = ""
    metrics: tuple[FixedScoreMetric, ...]
    formula: str
    formula_variables: tuple[str, ...] = ()
    metric_rationale: str = ""
    formula_rationale: str = ""
    provenance: Dict[str, Any] = Field(default_factory=dict)

    def compiled(self) -> CompiledFormula:
        return compile_formula(
            self.formula, variables=[metric.variable for metric in self.metrics]
        )

    def objective_preferences(self) -> tuple[ObjectivePreference, ...]:
        return tuple(metric.objective_preference() for metric in self.metrics)

    def spectral_requirement(self) -> SpectralRequirement:
        bands = [band for metric in self.metrics for band in metric.bands_nm()]
        if not bands:  # pragma: no cover - a verified metric always has a band
            return SpectralRequirement(lower_nm=0.0, upper_nm=0.0)
        return SpectralRequirement(
            lower_nm=min(band[0] for band in bands),
            upper_nm=max(band[1] for band in bands),
            bands_nm=tuple(sorted(set(bands))),
        )

    def score(self, objective_report: Any) -> ScoreOutcome:
        """Rank one candidate from what the deterministic runtime measured.

        Reads ``observed``, never a reported score.  Every row is re-checked
        against the metric and band the standard declares before its number is
        used, so a row that merely carries a matching identifier cannot feed the
        formula a different measurement.  A candidate missing any required
        measurement is reported as unscoreable rather than ranked on a partial
        sum, because a partial sum is silently smaller and would look like a
        worse design.
        """

        attainment = _attainment_rows(objective_report)
        values: Dict[str, float] = {}
        missing: list[str] = []
        errors: list[str] = []
        for metric in self.metrics:
            row = _locate_row(attainment, metric, errors)
            if row is None:
                missing.append(metric.variable)
                continue
            observed = row.get("observed")
            try:
                number = float(observed)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                missing.append(metric.variable)
                errors.append(
                    f"{metric.canonical_id} was measured as {observed!r}, "
                    "which is not a number"
                )
                continue
            if not math.isfinite(number):
                missing.append(metric.variable)
                errors.append(f"{metric.canonical_id} was measured as {number}")
                continue
            values[metric.variable] = number
        if missing:
            return ScoreOutcome(
                ok=False,
                values=values,
                missing=tuple(missing),
                errors=tuple(errors)
                or (
                    f"no measurement for {', '.join(missing)}; this candidate "
                    "cannot be compared under the frozen standard",
                ),
            )
        try:
            value = self.compiled().evaluate(values)
        except FormulaError as exc:
            return ScoreOutcome(
                ok=False, values=values, errors=(*errors, str(exc))
            )
        return ScoreOutcome(ok=True, value=value, values=values, errors=tuple(errors))

    def rank(self, candidates: Sequence[Any]) -> tuple[tuple[int, ScoreOutcome], ...]:
        """Order candidates best first, keeping the unscoreable ones visible.

        Unscoreable candidates sort last rather than being dropped, so a route
        whose measurements went missing shows up as a gap to explain instead of
        vanishing from the comparison.
        """

        outcomes = [
            (index, self.score(_objective_report_of(candidate)))
            for index, candidate in enumerate(candidates)
        ]
        return tuple(
            sorted(
                outcomes,
                key=lambda item: (
                    0 if item[1].ok else 1,
                    -(item[1].value if item[1].value is not None else 0.0),
                    item[0],
                ),
            )
        )


def _objective_report_of(candidate: Any) -> Any:
    if isinstance(candidate, Mapping) and "objective_report" in candidate:
        return candidate.get("objective_report")
    return candidate


def _attainment_rows(objective_report: Any) -> Dict[str, Any]:
    report = objective_report
    if isinstance(report, BaseModel):
        report = report.model_dump(mode="json")
    if isinstance(report, Mapping) and "target_attainment" in report:
        report = report.get("target_attainment")
    if isinstance(report, Mapping):
        return {str(key): value for key, value in report.items()}
    return {}


def _intervals_agree(left: Any, right: Any) -> bool:
    if not isinstance(left, (list, tuple)) or not isinstance(right, (list, tuple)):
        return False
    if len(left) != 2 or len(right) != 2:
        return False
    try:
        return all(
            abs(float(a) - float(b)) <= 1e-6 for a, b in zip(left, right, strict=False)
        )
    except (TypeError, ValueError):
        return False


def _row_matches(row: Any, metric: FixedScoreMetric) -> bool:
    """Whether a measured row really carries this metric over this band."""

    if not isinstance(row, Mapping):
        return False
    if str(row.get("metric") or "") != metric.metric:
        return False
    region = row.get("region")
    if not isinstance(region, Mapping):
        return False
    for key in metric.interval_keys:
        if not _intervals_agree(region.get(key), metric.region.get(key)):
            return False
    for selector in ("angle_deg", "polarization"):
        if metric.region.get(selector) != region.get(selector):
            return False
    return True


def _locate_row(
    attainment: Mapping[str, Any],
    metric: FixedScoreMetric,
    errors: list[str],
) -> Mapping[str, Any] | None:
    """Find the measurement for one frozen metric, by identifier then by content.

    The content fallback matters: it keeps the standard working when a run
    measured the right metric over the right band under a different identifier,
    which is the difference between a comparable study and a route that drops
    out of the ranking for a naming reason.
    """

    row = attainment.get(metric.objective_id)
    if row is not None:
        if _row_matches(row, metric):
            return row
        errors.append(
            f"objective {metric.objective_id} does not carry "
            f"{metric.canonical_id}; its measurement was ignored"
        )
    for candidate in attainment.values():
        if _row_matches(candidate, metric):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Making the run measure what the standard needs
# ---------------------------------------------------------------------------


def widen_spectral_grid(
    grid: Any,
    requirement: SpectralRequirement,
    *,
    maximum_points: int = DEFAULT_MAXIMUM_SPECTRAL_POINTS,
) -> tuple[Dict[str, Any], tuple[str, ...]]:
    """Extend a uniform wavelength grid to cover every frozen scoring band.

    A metric over a band the run never simulates is not a scoring choice, it is
    a compilation failure: the task contract rejects an objective whose interval
    falls outside the grid.  Widening keeps the sample spacing rather than the
    sample count, so a band added far from the original range is still resolved,
    and stops at a ceiling because a request spanning the visible and the
    long-wave infrared would otherwise multiply the forward solve by the span
    ratio.  Explicit sample lists are left alone; there is no defensible way to
    guess which samples such a caller wanted added.
    """

    warnings: list[str] = []
    if not isinstance(grid, Mapping):
        return {}, ("the spectral grid is not an object; left unchanged",)
    updated = dict(grid)
    if updated.get("values_nm"):
        covered = [float(value) for value in updated["values_nm"]]
        for lower, upper in requirement.bands_nm:
            if not any(lower <= value <= upper for value in covered):
                warnings.append(
                    f"the explicit wavelength list does not sample "
                    f"{lower:g}-{upper:g} nm, so that metric cannot be measured"
                )
        return updated, tuple(warnings)
    try:
        start = float(updated["start_nm"])
        stop = float(updated["stop_nm"])
        points = int(updated["points"])
    except (KeyError, TypeError, ValueError):
        return updated, (
            "the spectral grid states neither an explicit sample list nor "
            "start_nm/stop_nm/points; left unchanged",
        )
    if points < 2 or stop <= start:
        return updated, ("the spectral grid is degenerate; left unchanged",)
    new_start = min(start, requirement.lower_nm) if requirement.lower_nm > 0 else start
    new_stop = max(stop, requirement.upper_nm)
    if new_start >= new_stop:  # pragma: no cover - guarded by verified bands
        return updated, ("the frozen scoring bands are degenerate; left unchanged",)
    if new_start == start and new_stop == stop:
        new_points = points
    else:
        spacing = (stop - start) / (points - 1)
        new_points = min(
            maximum_points, max(points, int(math.ceil((new_stop - new_start) / spacing)) + 1)
        )
        warnings.append(
            f"widened the simulation grid from {start:g}-{stop:g} nm to "
            f"{new_start:g}-{new_stop:g} nm with {new_points} samples so the "
            "frozen scoring bands are measured"
        )
    updated["start_nm"] = new_start
    updated["stop_nm"] = new_stop
    updated["points"] = new_points
    spacing = (new_stop - new_start) / max(1, new_points - 1)
    for lower, upper in requirement.bands_nm:
        samples = int((min(upper, new_stop) - max(lower, new_start)) / spacing) + 1
        if samples < MINIMUM_SAMPLES_PER_BAND:
            warnings.append(
                f"{lower:g}-{upper:g} nm receives about {samples} "
                f"sample{'' if samples == 1 else 's'} at {spacing:.3g} nm spacing, "
                f"below the {MINIMUM_SAMPLES_PER_BAND} a band mean needs to be "
                "representative"
            )
    return updated, tuple(warnings)


# ---------------------------------------------------------------------------
# Stage results
# ---------------------------------------------------------------------------


class MetricSelectionResult(BaseModel):
    """What the first stage produced, verified against the catalogue."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    metrics: tuple[FixedScoreMetric, ...] = ()
    rationale: str = ""
    attempts: int = 0
    validation_errors: tuple[str, ...] = ()
    usage: tuple[Dict[str, Any], ...] = ()


class ScoringFormulaResult(BaseModel):
    """What the second stage produced, parsed and probed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    formula: str = ""
    variables: tuple[str, ...] = ()
    rationale: str = ""
    attempts: int = 0
    validation_errors: tuple[str, ...] = ()
    usage: tuple[Dict[str, Any], ...] = ()


class ScoringStandardResult(BaseModel):
    """The two stages together."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    standard: ScoringStandard | None = None
    selection: MetricSelectionResult | None = None
    formula: ScoringFormulaResult | None = None
    validation_errors: tuple[str, ...] = ()

    @property
    def usage(self) -> tuple[Dict[str, Any], ...]:
        rows: list[Dict[str, Any]] = []
        for stage in (self.selection, self.formula):
            if stage is not None:
                rows.extend(stage.usage)
        return tuple(rows)


def _safe_json(text: str) -> Dict[str, Any]:
    text = str(text or "").strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(text[start : end + 1])
                return value if isinstance(value, dict) else {}
            except json.JSONDecodeError:
                pass
    return {}


def _record_usage(response: Mapping[str, Any], usages: List[Dict[str, Any]]) -> None:
    row = dict(response.get("_llm_usage") or {})
    usages.append(row)
    total = row.get("total_tokens")
    if total is None:
        total = (
            int(row.get("input_tokens") or 0) + int(row.get("output_tokens") or 0)
        ) or (
            int(row.get("prompt_tokens") or 0) + int(row.get("completion_tokens") or 0)
        )
    get_cost_tracker().record_qwen_usage("plus", int(total or 0))


def _question_digest(question: str) -> str:
    return hashlib.sha256(" ".join(str(question).split()).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


class QwenScoringStandardBuilder:
    """Run the two stages and hand back one frozen standard.

    Each stage regenerates on local rejection rather than accepting a defective
    answer, because both defects this catches -- a metric the simulator cannot
    compute and a formula with a direction reversed -- are silent afterwards.
    """

    def __init__(
        self,
        client: ScoringStandardClient,
        *,
        metric_prompt_path: Path | str = DEFAULT_METRIC_SELECTION_PROMPT,
        formula_prompt_path: Path | str = DEFAULT_SCORING_FORMULA_PROMPT,
        maximum_metrics: int = DEFAULT_MAXIMUM_METRICS,
        maximum_attempts: int = DEFAULT_MAXIMUM_ATTEMPTS,
    ) -> None:
        self.client = client
        self.metric_prompt_path = Path(metric_prompt_path)
        self.formula_prompt_path = Path(formula_prompt_path)
        self.maximum_metrics = max(1, int(maximum_metrics))
        self.maximum_attempts = max(1, int(maximum_attempts))
        self._model_label = str(getattr(client, "model_name", SCORING_STANDARD_MODEL))

    # -- stage 1 ---------------------------------------------------------

    def select_metrics(
        self,
        question: str,
        *,
        problem_analysis: Any = None,
        force_mock: bool | None = None,
    ) -> MetricSelectionResult:
        system_prompt = self.metric_prompt_path.read_text(encoding="utf-8")
        base_payload: Dict[str, Any] = {
            "user_question": str(question or "").strip(),
            "capability_catalog": catalog_document(),
            "fixed_rules": {
                "maximum_metrics": self.maximum_metrics,
                "scoreable_metrics": list(SCOREABLE_METRICS),
                "verification": (
                    "a local check verifies every metric against the catalogue "
                    "before anything runs; rejected proposals are returned for "
                    "regeneration"
                ),
                "scoring_role": (
                    "these metrics rank designs and never decide physical validity"
                ),
                "model": self._model_label,
            },
        }
        if problem_analysis is not None:
            base_payload["problem_analysis"] = _as_plain(problem_analysis)
        usages: List[Dict[str, Any]] = []
        history: List[str] = []
        errors: tuple[str, ...] = ()
        previous = ""
        for attempt in range(1, self.maximum_attempts + 1):
            payload = dict(base_payload)
            if attempt > 1:
                payload["rejected_selection"] = _safe_json(previous)
                payload["repair_request"] = {
                    "validation_errors": list(errors),
                    "instruction": (
                        "Repair only the listed defects. Return the corrected "
                        "complete JSON object. Copy metric names character for "
                        "character from capability_catalog.scoreable_metrics, and "
                        "keep the bands and directions the user's request implies."
                    ),
                }
            try:
                response = self.client.call(
                    [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    ],
                    max_tokens=SCORING_STANDARD_MAX_TOKENS,
                    force_mock=force_mock,
                )
            except Exception as exc:
                return MetricSelectionResult(
                    status="unavailable",
                    attempts=attempt,
                    validation_errors=(f"{type(exc).__name__}: {exc}",),
                    usage=tuple(usages),
                )
            _record_usage(response, usages)
            previous = str(response.get("content") or "")
            metrics, errors = self._verify_selection(_safe_json(previous))
            if metrics:
                return MetricSelectionResult(
                    status="selected",
                    metrics=metrics,
                    rationale=str(_safe_json(previous).get("rationale") or "").strip(),
                    attempts=attempt,
                    usage=tuple(usages),
                )
            history.extend(errors)
        return MetricSelectionResult(
            status="invalid",
            attempts=self.maximum_attempts,
            validation_errors=tuple(dict.fromkeys(history or errors)),
            usage=tuple(usages),
        )

    def _verify_selection(
        self, raw: Mapping[str, Any]
    ) -> tuple[tuple[FixedScoreMetric, ...], tuple[str, ...]]:
        proposed = raw.get("metrics")
        if not isinstance(proposed, list) or not proposed:
            return (), (
                "the response carries no 'metrics' array; return at least one "
                "metric chosen from the catalogue",
            )
        errors: list[str] = []
        if len(proposed) > self.maximum_metrics:
            errors.append(
                f"{len(proposed)} metrics were proposed, above the maximum of "
                f"{self.maximum_metrics}; keep only the ones the request names"
            )
        verdicts = verify_metric_selection(proposed[: self.maximum_metrics])
        metrics: list[FixedScoreMetric] = []
        seen: set[str] = set()
        for index, verdict in enumerate(verdicts, start=1):
            if not verdict.ok:
                errors.append(f"metric {index}: {verdict.repair_hint}")
                continue
            if verdict.variable in seen:
                errors.append(
                    f"metric {index}: {verdict.canonical_id} is selected twice; "
                    "each metric and band may appear once"
                )
                continue
            seen.add(verdict.variable)
            reference = proposed[index - 1]
            metrics.append(
                FixedScoreMetric(
                    variable=verdict.variable,
                    canonical_id=verdict.canonical_id,
                    metric=str(verdict.normalized["metric"]),
                    sense=str(verdict.normalized["sense"]),
                    region=dict(verdict.normalized["region"]),
                    target=verdict.normalized.get("target"),
                    rationale=str(
                        (reference or {}).get("rationale")
                        if isinstance(reference, Mapping)
                        else ""
                    ).strip(),
                )
            )
        if errors:
            return (), tuple(errors)
        return tuple(metrics), ()

    # -- stage 2 ---------------------------------------------------------

    def author_formula(
        self,
        question: str,
        metrics: Sequence[FixedScoreMetric],
        *,
        force_mock: bool | None = None,
    ) -> ScoringFormulaResult:
        if not metrics:
            return ScoringFormulaResult(
                status="invalid",
                validation_errors=("no verified metrics were supplied",),
            )
        system_prompt = self.formula_prompt_path.read_text(encoding="utf-8")
        base_payload: Dict[str, Any] = {
            "user_question": str(question or "").strip(),
            "verified_metrics": [
                {
                    "variable": metric.variable,
                    "canonical_id": metric.canonical_id,
                    "metric": metric.metric,
                    "sense": metric.sense,
                    "region": metric.region,
                    "target": metric.target,
                    "why_selected": metric.rationale,
                    "summary": (
                        METRIC_CATALOG[metric.metric].summary
                        if metric.metric in METRIC_CATALOG
                        else ""
                    ),
                }
                for metric in metrics
            ],
            "fixed_rules": {
                "grammar": (
                    "the given variable names, numeric literals, + - * / ** and "
                    "parentheses, and min/max/abs"
                ),
                "orientation": "higher must always mean better",
                "every_variable_used": True,
                "value_range": "each measurement is a fraction between 0 and 1",
                "frozen": (
                    "the expression is used for every route in the study and is "
                    "never rewritten afterwards"
                ),
                "model": self._model_label,
            },
        }
        usages: List[Dict[str, Any]] = []
        history: List[str] = []
        errors: tuple[str, ...] = ()
        previous = ""
        variables = [metric.variable for metric in metrics]
        for attempt in range(1, self.maximum_attempts + 1):
            payload = dict(base_payload)
            if attempt > 1:
                payload["rejected_formula"] = _safe_json(previous).get("formula", "")
                payload["repair_request"] = {
                    "validation_errors": list(errors),
                    "instruction": (
                        "Repair only the listed defects and return the corrected "
                        "complete JSON object. Use every variable exactly as "
                        "spelled in verified_metrics."
                    ),
                }
            try:
                response = self.client.call(
                    [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": json.dumps(payload, ensure_ascii=False),
                        },
                    ],
                    max_tokens=SCORING_STANDARD_MAX_TOKENS,
                    force_mock=force_mock,
                )
            except Exception as exc:
                return ScoringFormulaResult(
                    status="unavailable",
                    attempts=attempt,
                    validation_errors=(f"{type(exc).__name__}: {exc}",),
                    usage=tuple(usages),
                )
            _record_usage(response, usages)
            previous = str(response.get("content") or "")
            raw = _safe_json(previous)
            try:
                compiled = compile_formula(raw.get("formula"), variables=variables)
            except FormulaError as exc:
                errors = (str(exc),)
                history.extend(errors)
                continue
            errors = _orientation_errors(compiled, metrics)
            if errors:
                history.extend(errors)
                continue
            return ScoringFormulaResult(
                status="authored",
                formula=compiled.text,
                variables=compiled.used,
                rationale=str(raw.get("rationale") or "").strip(),
                attempts=attempt,
                usage=tuple(usages),
            )
        return ScoringFormulaResult(
            status="invalid",
            attempts=self.maximum_attempts,
            validation_errors=tuple(dict.fromkeys(history or errors)),
            usage=tuple(usages),
        )

    # -- both ------------------------------------------------------------

    def build(
        self,
        question: str,
        *,
        problem_analysis: Any = None,
        force_mock: bool | None = None,
    ) -> ScoringStandardResult:
        selection = self.select_metrics(
            question, problem_analysis=problem_analysis, force_mock=force_mock
        )
        if selection.status != "selected" or not selection.metrics:
            return ScoringStandardResult(
                status=(
                    "unavailable" if selection.status == "unavailable" else "invalid"
                ),
                selection=selection,
                validation_errors=selection.validation_errors,
            )
        formula = self.author_formula(
            question, selection.metrics, force_mock=force_mock
        )
        if formula.status != "authored":
            return ScoringStandardResult(
                status="unavailable" if formula.status == "unavailable" else "invalid",
                selection=selection,
                formula=formula,
                validation_errors=formula.validation_errors,
            )
        standard = ScoringStandard(
            question_digest=_question_digest(question),
            metrics=selection.metrics,
            formula=formula.formula,
            formula_variables=formula.variables,
            metric_rationale=selection.rationale,
            formula_rationale=formula.rationale,
            provenance={
                "model": self._model_label,
                "metric_selection_attempts": selection.attempts,
                "formula_attempts": formula.attempts,
                "maximum_metrics": self.maximum_metrics,
            },
        )
        return ScoringStandardResult(
            status="standardized",
            standard=standard,
            selection=selection,
            formula=formula,
        )


def _as_plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _as_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain(item) for item in value]
    return value


__all__ = [
    "DEFAULT_MAXIMUM_ATTEMPTS",
    "DEFAULT_MAXIMUM_METRICS",
    "DEFAULT_MAXIMUM_SPECTRAL_POINTS",
    "DEFAULT_METRIC_SELECTION_PROMPT",
    "DEFAULT_SCORING_FORMULA_PROMPT",
    "MINIMUM_SAMPLES_PER_BAND",
    "SCORING_STANDARD_MAX_TOKENS",
    "SCORING_STANDARD_MODEL",
    "SCORING_STANDARD_SCHEMA_VERSION",
    "CompiledFormula",
    "FixedScoreMetric",
    "FormulaError",
    "MetricSelectionResult",
    "QwenScoringStandardBuilder",
    "ScoreOutcome",
    "ScoringFormulaResult",
    "ScoringStandard",
    "ScoringStandardClient",
    "ScoringStandardResult",
    "SpectralRequirement",
    "compile_formula",
    "widen_spectral_grid",
]
