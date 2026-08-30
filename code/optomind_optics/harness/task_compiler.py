"""Constrained natural-language compiler for the TMM Harness task contract.

Qwen may only translate a question into a bounded JSON draft.  Deterministic
Pydantic and TMM validators construct the immutable :class:`OpticalDesignTask`.
It never simulates a spectrum, certifies physics, or defines admission gates.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .benchmarks import BenchmarkTask
from .design_task import (
    HarnessBudgetPolicy,
    ObjectivePreference,
    OpticalDesignTask,
    PhysicsVerificationPolicy,
    PortfolioPolicy,
    TMMExperimentSpec,
    UncertaintyPolicy,
)
from config.qwen_config import get_cost_tracker

from .experiment_store import ExperimentStore
from .problem_analyzer import (
    ARTICLE_PROBLEM_ANALYZER_MODEL,
    ArticlePlusQwenClient,
    validate_research_charter,
)
from .qwen_policy import QWEN_POLICY_MODEL
from .scoring_standard import ScoringStandard, widen_spectral_grid
from .strategy_planner import _charter_value, _coerce_bound_pair


DEFAULT_TASK_COMPILER_PROMPT = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "TMM Task Compiler.txt"
)
_SAFE_ID_FRAGMENT = re.compile(r"[^a-z0-9]+")
_BAND_RANGE_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:-|–|—|to)\s*"
    r"(\d+(?:\.\d+)?)\s*(nm|um|µm|μm)",
    re.IGNORECASE,
)
_PREFERRED_TERMS = re.compile(
    r"\b(high|higher|maximi[sz]e|preferred|enhanced|passband)\b|高|增强|优选|偏好",
    re.IGNORECASE,
)
_SUPPRESSED_TERMS = re.compile(
    r"\b(low|lower|minimi[sz]e|suppressed|rejected|stopband)\b|低|抑制|阻带",
    re.IGNORECASE,
)
# "high-index / low-index" names two materials, not two opposing spectral
# preferences.  Every alternating stack in the literature is described this way,
# so scanning the raw text for "high" and "low" reads the material system as a
# maximize/minimize pair and rejects a perfectly well-formed dual-maximize task.
# These forms are struck out before the preference scan; a genuine preference
# word elsewhere in the text still matches.
_MATERIAL_INDEX_ADJECTIVE_RE = re.compile(
    r"\b(?:high|higher|low|lower)[\s\-_]*(?:refractive[\s\-_]*)?"
    r"(?:index|indices|n)\b"
    r"|(?:高|低)折射率",
    re.IGNORECASE,
)


def _without_material_adjectives(text: str) -> str:
    """Strip material index adjectives so they cannot read as preferences."""

    return _MATERIAL_INDEX_ADJECTIVE_RE.sub(" ", str(text or ""))

_DISCRETE_COUNT_RANGE_RE = re.compile(
    r"\b([NMP])\s*\(\s*(\d+)\s*(?:-|–|—|to)\s*(\d+)\s*\)",
    re.IGNORECASE,
)
_PAIR_COUNT_EACH_SIDE_RE = re.compile(
    r"\b(?:p\s*=\s*)?(\d+)\s+(?:dbr\s+)?pairs?\s+on\s+each\s+side\b",
    re.IGNORECASE,
)
_DECLARED_LAYER_COUNT_RE = re.compile(
    r"\b(\d{1,3})(?:\s*[- ]layer(?:ed)?|\s+finite\s+layers?)\b",
    re.IGNORECASE,
)
_INCIDENT_MEDIUM_RE = re.compile(
    r"\bincident\s+(?:medium|media)\b|\bsuperstrate\b", re.IGNORECASE
)
_EXIT_MEDIUM_RE = re.compile(
    r"\bsubstrate\b|\bexit\s+(?:medium|media)\b", re.IGNORECASE
)
_SEMI_INFINITE_RE = re.compile(r"\bsemi-?infinite\b", re.IGNORECASE)
_MIRROR_PAIR_FIXED_RE = re.compile(
    r"\bN[_ ]?H\s*=\s*N[_ ]?L\s*=\s*(\d+)(?!\s*(?:-|–|—))\s*(?:pairs?)?\b",
    re.IGNORECASE,
)
_MIRROR_PAIR_RANGE_RE = re.compile(
    r"\bN[_ ]?H\s*=\s*N[_ ]?L\s*=\s*(\d+)\s*(?:-|–|—|to)\s*"
    r"(\d+)\s*(?:pairs?)?\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE
)
_POINT_NM_RE = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*nm\b", re.IGNORECASE)
# Multi-element chemical formulas such as TiO2 or Si3N4.  All-caps acronyms
# (TE, TM, DBR) are excluded by requiring a lowercase letter or a digit, so
# this never mistakes polarization labels for materials.
_MATERIAL_FORMULA_RE = re.compile(r"\b(?:[A-Z][a-z]?\d*){2,}\b")


# A sentence ends at a period, semicolon or newline that is not a decimal
# point, matching the clause splitter used for explicit target thresholds.
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<!\d)[.;\n](?!\d)")
# Requests write these words in the plural far more often than the singular
# ("all 30 layer thicknesses", "design variables: 20 layer thicknesses"), and
# a \b-anchored singular does not match a plural.
_GEOMETRIC_RANGE_RE = re.compile(
    r"\b(?:thickness(?:es)?|thick|layer\s+(?:sizes?|depths?)|"
    r"fabrication\s+bounds?)\b",
    re.IGNORECASE,
)
_SPECTRAL_RANGE_RE = re.compile(
    r"\b(?:wavelength|spectr(?:al|um)|band|passband|stopband)\b",
    re.IGNORECASE,
)


def _enclosing_sentence(text: str, start: int, end: int) -> tuple[str, int]:
    """Return the sentence around ``text[start:end]`` and ``start`` within it.

    A numeric range belongs to whatever its own sentence is about, and a fixed
    character window cannot express that.  A request that enumerates per-layer
    bounds names "thicknesses" once and then lists a dozen ranges, the last of
    which sits far outside any window anchored on that word.
    """

    left = 0
    for boundary in _SENTENCE_BOUNDARY_RE.finditer(text, 0, start):
        left = boundary.end()
    closing = _SENTENCE_BOUNDARY_RE.search(text, end)
    right = closing.start() if closing else len(text)
    return text[left:right], start - left


def _range_is_geometric(sentence: str, start: int) -> bool:
    """Decide whether the range at ``start`` measures a thickness or a spectrum.

    Asking only whether the sentence *contains* a geometric or a spectral word
    mislabels ranges in both directions, because one sentence routinely holds
    both kinds of word.  "all 24 layer thicknesses uniformly within 140-190 nm
    band" carries "thicknesses" and "band" at once, and a request closing with
    "... from 500-900 nm ... and thickness uncertainty" carries them in the
    opposite order; a containment test reads the first as spectral and the
    second as geometric, and both readings are wrong.  What settles it is the
    noun the range modifies, which in English is the nearest one before it.
    """

    nearest: tuple[int, bool] | None = None
    for pattern, geometric in (
        (_GEOMETRIC_RANGE_RE, True),
        (_SPECTRAL_RANGE_RE, False),
    ):
        for match in pattern.finditer(sentence):
            if match.end() > start:
                break
            distance = start - match.end()
            if nearest is None or distance < nearest[0]:
                nearest = (distance, geometric)
    return bool(nearest and nearest[1])


def _wavelength_ranges(text: str) -> set[tuple[float, float, str]]:
    """Return spectral intervals while excluding geometric thickness bounds."""

    ranges: set[tuple[float, float, str]] = set()
    for match in _BAND_RANGE_RE.finditer(text):
        sentence, offset = _enclosing_sentence(text, match.start(), match.end())
        if _range_is_geometric(sentence, offset):
            continue
        ranges.add(
            (
                float(match.group(1)),
                float(match.group(2)),
                match.group(3).casefold(),
            )
        )
    return ranges


def _bounding_media_named(sentence: str) -> int:
    """Count the semi-infinite bounding media an enumeration names explicitly.

    Prose calls "Air incident medium, SiC, SiO2, Al semi-infinite substrate" a
    three-layer stack, counting the substrate among the layers, while the
    compiler policy directive -- and the engine -- count only the coating
    layers between the two semi-infinite media.  Both conventions are current,
    so a draft that falls short of the prose count by no more than the number
    of bounding media the request itself names has resolved an ambiguity in the
    request rather than changed the topology.  Without this, the layer-count
    check rejects the very reading the policy directive asks for.
    """

    incident = bool(_INCIDENT_MEDIUM_RE.search(sentence))
    exit_side = bool(_EXIT_MEDIUM_RE.search(sentence))
    if not (incident or exit_side):
        return 1 if _SEMI_INFINITE_RE.search(sentence) else 0
    return int(incident) + int(exit_side)


class TaskCompilerClient(Protocol):
    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> dict[str, Any]: ...


class TMMTaskCompilationError(RuntimeError):
    pass


class CompileFailure(TMMTaskCompilationError):
    """Raised when a compiled task violates the immutable ResearchCharter."""


# ---------------------------------------------------------------------------
# Article branch additions (T-06)
# ---------------------------------------------------------------------------

# Compilation / structured tasks route through the turbo tier (role="turbo"
# resolves to the advanced_model tier of model_policy.yaml).
ARTICLE_TASK_COMPILER_MODEL = "qwen3.7-flash"

# The compiler emits the largest artifact in the harness: the prompt forbids
# ellipses and symbolic repeat counts, so a chirped mirror has to be written
# out layer by layer, roughly 80 tokens each.  Measured against R-09 runs, the
# largest accepted draft was a 30-layer stack at ~3.5k tokens -- inside the
# former 4k ceiling with only 14% to spare, and a longer stack in the same run
# came back truncated.  Truncation is indistinguishable from garbage at the
# parse boundary: the JSON never closes, the draft reads as ``{}``, and the
# route dies reporting the two required fields as missing.  Sized like
# ``PLANNER_MAX_TOKENS`` so the ceiling is set by the contract rather than by
# the largest stack anyone has happened to try.
COMPILER_MAX_TOKENS = 8000

# The planning tier is admitted alongside the compilation tier because the
# result schema already declares both, and because the turbo tier has been
# observed to invert a direction: asked to maximize band reflectance it wrote
# ``at_most`` with target 0.0, which ``sense_by_constraint`` then faithfully
# derived as ``minimize``.  Which tier a run uses stays the caller's choice --
# the lock exists to keep an unvetted model out, not to pin one tier.
_ALLOWED_COMPILER_MODELS = frozenset(
    {
        QWEN_POLICY_MODEL,
        ARTICLE_TASK_COMPILER_MODEL,
        ARTICLE_PROBLEM_ANALYZER_MODEL,
    }
)


class ArticleTurboQwenClient(ArticlePlusQwenClient):
    """Compilation-tier client routed through qwen_config.get_qwen_client('turbo')."""

    def __init__(self) -> None:
        super().__init__(role="turbo")


class TMMTaskDraft(BaseModel):
    """Only fields that a language model is allowed to propose."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["compiled", "needs_clarification", "needs_higher_fidelity"]
    rationale: str
    normalized_request_english: str = ""
    experiments: tuple[TMMExperimentSpec, ...] = ()
    uncertainty: UncertaintyPolicy = Field(default_factory=UncertaintyPolicy)

    @field_validator("normalized_request_english")
    @classmethod
    def _english_only(cls, value: str) -> str:
        text = str(value or "").strip()
        if any("\u4e00" <= char <= "\u9fff" for char in text):
            raise ValueError("normalized_request_english must contain English only")
        return text

    @field_validator("experiments")
    @classmethod
    def _bounded_experiments(
        cls,
        value: tuple[TMMExperimentSpec, ...],
    ) -> tuple[TMMExperimentSpec, ...]:
        if len(value) > 3:
            raise ValueError("the compiler may emit at most three experiments")
        return value


class TaskCompilationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["tmm-task-compilation.v1"] = "tmm-task-compilation.v1"
    status: Literal["compiled", "needs_clarification", "needs_higher_fidelity", "invalid"]
    benchmark_id: str | None = None
    model_name: Literal["qwen3.7-flash", "qwen3.5-plus"] = ARTICLE_TASK_COMPILER_MODEL
    attempts: int
    task: OpticalDesignTask | None = None
    rationale: str = ""
    validation_errors: tuple[str, ...] = ()
    usage: tuple[dict[str, Any], ...] = ()
    raw_response_sha256: tuple[str, ...] = ()
    # A digest identifies a rejected draft but does not describe it. Keeping
    # the last rejected text is what makes a compile failure diagnosable after
    # the run: without it the only evidence of why a route died is the
    # validator's one-line message, which cannot distinguish a model defect
    # from a normalizer defect.  Populated on the invalid path only.
    rejected_draft: str = ""


def _safe_json(text: str) -> dict[str, Any]:
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
                return {}
    return {}


def _is_retryable_model_call_error(exc: Exception) -> bool:
    """Recognize transient provider failures without hiding compiler bugs."""

    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    name = type(exc).__name__.casefold()
    return name in {
        "apiconnectionerror",
        "apitimeouterror",
        "connecterror",
        "readtimeout",
        "connecttimeout",
    }


def _unwrap_draft_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap common JSON-object envelopes without guessing scientific fields.

    Some otherwise valid model responses place the requested object under a
    single ``result``/``draft``/``output`` key.  Accepting those mechanical
    wrappers is safe because the inner object still passes the complete
    Pydantic and deterministic physics validation path.
    """

    if "status" in payload:
        return payload
    for key in ("draft", "result", "output", "task_draft"):
        nested = payload.get(key)
        if isinstance(nested, dict) and (
            "status" in nested
            or "experiments" in nested
            or "normalized_request_english" in nested
        ):
            return dict(nested)
    return payload


_ANGLE_UNIT_TOKEN_RE = re.compile(r"(?:degrees?|deg\b|°)", re.IGNORECASE)
_ANGLE_VALUE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg\b|°)", re.IGNORECASE
)
_NON_ANGLE_UNIT_RE = re.compile(
    r"\b(?:nm|um|µm|mm|cm|percent|%|layer|l|layers|pair|pairs)\b",
    re.IGNORECASE,
)
_LIST_SEPARATOR_RE = re.compile(r"[\s,]*(?:and\s+)?[\s,]*")


def _angle_values_from_clause(clause: str) -> list[float]:
    """Extract only values grammatically attached to an angular unit.

    Handles both repeated-unit lists (``0 degrees, 30 degrees, 45 degrees``)
    and one-terminal-unit lists (``0, 30, and 45 degrees``).  Unrelated small
    integers such as ``6-layer`` or ``450-700 nm`` are excluded because they
    are attached to a different unit or separated by a non-list boundary.
    """

    if not clause:
        return []
    candidates: list[tuple[int, float]] = []
    # Repeated-unit form: every number directly followed by an angle unit.
    for match in _ANGLE_VALUE_RE.finditer(clause):
        value = float(match.group(1))
        if 0.0 <= value < 90.0:
            candidates.append((match.start(), value))
    # One-terminal-unit form: walk backward from the last angle unit over
    # list punctuation only, stopping at a boundary or another unit.
    last_unit = None
    for match in _ANGLE_UNIT_TOKEN_RE.finditer(clause):
        last_unit = match
    if last_unit is not None:
        prefix = clause[: last_unit.start()]
        numbers = list(re.finditer(r"\d+(?:\.\d+)?", prefix))
        if numbers:
            run: list[Any] = [numbers[-1]]
            for index in range(len(numbers) - 2, -1, -1):
                between = prefix[
                    numbers[index].end() : numbers[index + 1].start()
                ]
                if (
                    _LIST_SEPARATOR_RE.fullmatch(between)
                    and not _ANGLE_UNIT_TOKEN_RE.search(between)
                    and not _NON_ANGLE_UNIT_RE.search(between)
                ):
                    run.append(numbers[index])
                else:
                    break
            for match in run:
                value = float(match.group())
                if 0.0 <= value < 90.0:
                    candidates.append((match.start(), value))
    seen: set[float] = set()
    ordered: list[float] = []
    for _, value in sorted(candidates):
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


# A comma/"and"-separated number list whose unit appears once, at the end.
# Letters other than a separating "and" are deliberately excluded: the list is
# matched immediately before its unit, so admitting words would let an earlier
# wavelength phrase bleed into the captured angle list.
_ANGLE_VALUE_LIST = (
    r"(?:[-+]?\d+(?:\.\d+)?)"
    r"(?:\s*(?:,|and|,\s*and)\s*(?:[-+]?\d+(?:\.\d+)?))*"
)

# The same list, but requiring at least one separator, so it matches only where
# two or more angles were named.  "for" introduces a requested set often enough
# ("for 0, 30 and 60 degrees") to be worth reading, but it also introduces
# uncertainty clauses ("for 2 degrees of misalignment").  A single number after
# a bare preposition is too ambiguous to enforce a channel on; two or more is
# not a tolerance.
_ANGLE_VALUE_LIST_MULTI = (
    r"(?:[-+]?\d+(?:\.\d+)?)"
    r"(?:\s*(?:,|and|,\s*and)\s*(?:[-+]?\d+(?:\.\d+)?))+"
)

# A unit has to be consumed whole.  "degrees?" happily matches "degree" and
# leaves the "s" behind, which is enough to satisfy a following negative
# lookahead and let "for 1, 2 degrees of misalignment" through as a request.
_ANGLE_UNIT = r"(?:degrees|degree|deg|°)(?![A-Za-z])"

# Nouns that turn a number into a tolerance rather than a requested channel.
_ANGLE_UNCERTAINTY_NOUNS = (
    r"offset|error|uncertainty|tolerance|perturbation|deviation"
    r"|misalignment|misalignments|jitter|drift|spread|margin"
)
_ANGLE_NOT_AN_UNCERTAINTY = r"(?!\s*(?:of\s+)?(?:" + _ANGLE_UNCERTAINTY_NOUNS + r")\b)"


def _requested_illumination(source_question: str) -> tuple[list[float], list[str]]:
    """Extract explicit user illumination while excluding uncertainty clauses."""

    canonical_source = (
        source_question.rsplit("Canonical user controls:", 1)[-1]
        if "Canonical user controls:" in source_question
        else ""
    )
    candidate_sources = [canonical_source, source_question] if canonical_source else [source_question]
    requested_angles: list[float] = []
    for angle_source in candidate_sources:
        for pattern in (
            r"(?:incidence\s+angles?|angles?\s+of\b)\s*(?:of|:|=)?\s*([^.;\n]{1,120})",
            r"evaluate\s+(?:performance\s+)?at\s+([^.;\n]{1,120})",
            # A colon/equals list ("Angles: 0, 30, 60 degrees") and a list whose
            # unit trails the values ("at 0, 30, and 60 degrees incidence") are
            # the shapes route requests actually use.  Without them the
            # requested angle set came back empty, so nothing downstream
            # enforced the illumination channels or checked that the objective
            # scored them.  The capture admits only numbers and "and" so a
            # preceding wavelength phrase ("500-900nm ... at 30 degrees")
            # cannot be absorbed into the angle list.
            r"\bangles?\s*[:=]\s*("
            + _ANGLE_VALUE_LIST
            + r"\s*" + _ANGLE_UNIT + r")",
            r"\bat\s+(" + _ANGLE_VALUE_LIST + r"\s*" + _ANGLE_UNIT + r")",
            r"\bfor\s+(" + _ANGLE_VALUE_LIST_MULTI + r"\s*" + _ANGLE_UNIT + r")"
            # The unit ends the capture, so an uncertainty noun trailing it is
            # invisible to the clause split below and has to be excluded here.
            + _ANGLE_NOT_AN_UNCERTAINTY,
        ):
            match = re.search(pattern, angle_source, re.IGNORECASE)
            if not match:
                continue
            clause = re.split(
                r"\b(?:" + _ANGLE_UNCERTAINTY_NOUNS + r")\b|\+/-|±",
                match.group(1),
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            # The broad patterns capture up to 120 characters, so a robustness
            # clause hanging off the angle list ("0, 30 and 60 degrees with 2
            # degrees of misalignment") lands inside the capture.  The
            # uncertainty split above cannot reach it when the tolerance noun
            # trails its own unit, and the leftover "2 degrees" then reads as a
            # fourth requested angle.  An angle list never uses these words as
            # separators, so cutting at the first one is safe.
            clause = re.split(
                r"\b(?:with|within|under|assuming|allowing|including|given|plus"
                r"|despite|subject\s+to)\b",
                clause,
                maxsplit=1,
                flags=re.IGNORECASE,
            )[0]
            requested_angles.extend(_angle_values_from_clause(clause))
            if requested_angles:
                break
        if requested_angles:
            break
    polarization_source = canonical_source if re.search(
        r"\b(?:polarization|TE|TM|s[- ]?pol|p[- ]?pol)\b",
        canonical_source,
        re.IGNORECASE,
    ) else source_question
    requested_polarizations: list[str] = []
    if re.search(r"\b(?:TE|s[- ]?pol(?:ari[sz](?:ed|ation))?|polarization\s+s\b)\b", polarization_source, re.IGNORECASE):
        requested_polarizations.append("s")
    if re.search(r"\b(?:TM|p[- ]?pol(?:ari[sz](?:ed|ation))?|(?:polarization\s+)?s\s*,\s*p\b)\b", polarization_source, re.IGNORECASE):
        requested_polarizations.append("p")
    return (
        list(dict.fromkeys(requested_angles)),
        list(dict.fromkeys(requested_polarizations)),
    )


def _normalize_draft_envelope(
    payload: dict[str, Any], source_question: str = ""
) -> dict[str, Any]:
    """Recover a complete draft envelope without weakening inner validators."""

    normalized = _unwrap_draft_payload(dict(payload or {}))
    if "status" not in normalized and normalized.get("experiments"):
        normalized["status"] = "compiled"
    if normalized.get("status") and not str(normalized.get("rationale") or "").strip():
        normalized["rationale"] = (
            "The model response envelope was normalized; all experiment and physics "
            "fields remain subject to deterministic validation."
        )
    # A frequent model shorthand is one target with null angle/polarization to
    # mean "all simulated channels".  Lower-level TMM validators correctly
    # reject nulls, so expand this shorthand *before* Pydantic constructs the
    # validated experiment.  The operation cannot invent channels: it uses
    # only those already declared in the simulation illumination.
    requested_angles, requested_polarizations = _requested_illumination(
        source_question
    )
    experiments = normalized.get("experiments")
    if isinstance(experiments, list):
        repaired_experiments: list[Any] = []
        for raw_experiment in experiments:
            if not isinstance(raw_experiment, dict):
                repaired_experiments.append(raw_experiment)
                continue
            experiment = dict(raw_experiment)
            is_optimize = str(experiment.get("mode") or "").casefold() == "optimize"
            if not is_optimize:
                task = dict(experiment.get("tmm_task") or {})
                illumination = dict(task.get("illumination") or {})
                if requested_angles:
                    illumination["angles_deg"] = requested_angles
                if requested_polarizations:
                    illumination["polarizations"] = requested_polarizations
                if illumination:
                    task["illumination"] = illumination
                    experiment["tmm_task"] = task
                repaired_experiments.append(experiment)
                continue
            task = dict(experiment.get("tmm_task") or {})
            simulation = dict(task.get("simulation") or {})
            illumination = dict(simulation.get("illumination") or {})
            angles = requested_angles or list(illumination.get("angles_deg") or [0.0])
            polarizations = list(
                requested_polarizations
                or illumination.get("polarizations")
                or ["unpolarized"]
            )
            illumination["angles_deg"] = angles
            illumination["polarizations"] = polarizations
            simulation["illumination"] = illumination
            task["simulation"] = simulation
            targets = task.get("targets")
            if not isinstance(targets, list):
                repaired_experiments.append(experiment)
                continue
            expanded: list[Any] = []
            for raw_target in targets:
                if not isinstance(raw_target, dict):
                    expanded.append(raw_target)
                    continue
                target = dict(raw_target)
                observable_aliases = {
                    "reflectance": "R",
                    "reflection": "R",
                    "mean_reflectance": "R",
                    "band_reflectance": "R",
                    "transmittance": "T",
                    "transmission": "T",
                    "mean_transmittance": "T",
                    "absorptance": "A",
                    "absorption": "A",
                    "mean_absorption": "A",
                    "worst_case_reflectance": "R",
                    "worst_case_transmittance": "T",
                    "worst_case_absorption": "A",
                }
                raw_observable = str(target.get("observable") or "").strip()
                target["observable"] = observable_aliases.get(
                    raw_observable.casefold(), raw_observable.upper()
                )
                raw_angle = target.get("angle_deg")
                raw_polarization = target.get("polarization")
                target_angles = (
                    angles
                    if raw_angle is None
                    or not any(abs(float(raw_angle) - float(item)) <= 1e-9 for item in angles)
                    else [raw_angle]
                )
                target_polarizations = (
                    polarizations
                    if raw_polarization is None
                    or str(raw_polarization) not in polarizations
                    else [raw_polarization]
                )
                for angle in target_angles:
                    for polarization in target_polarizations:
                        item = {
                            **target,
                            "angle_deg": angle,
                            "polarization": polarization,
                        }
                        if len(target_angles) * len(target_polarizations) > 1:
                            base_name = str(item.get("name") or "target")
                            suffix = _SAFE_ID_FRAGMENT.sub(
                                "_", f"{float(angle):g}_{polarization}"
                            ).strip("_")
                            item["name"] = f"{base_name}_{suffix}"[:96]
                        expanded.append(item)
            task["targets"] = expanded
            experiment["tmm_task"] = task
            repaired_objectives: list[Any] = []
            for raw_objective in experiment.get("objectives") or []:
                if not isinstance(raw_objective, dict):
                    repaired_objectives.append(raw_objective)
                    continue
                objective = dict(raw_objective)
                if str(objective.get("metric") or "") in {
                    "resonance_q_phase",
                    "mixed_coherence_RTA",
                    "emissivity_spectrum",
                    "polarization_splitting",
                    "phase_group_delay_gdd",
                    "layer_absorption",
                    "opaque_stack_rta",
                }:
                    objective["sense"] = "report"
                    objective["target"] = None
                region = dict(objective.get("region") or {})
                if region.get("polarization") not in (None, *polarizations):
                    region.pop("polarization", None)
                if region.get("angle_deg") is not None and not any(
                    abs(float(region["angle_deg"]) - float(item)) <= 1e-9
                    for item in angles
                ):
                    region.pop("angle_deg", None)
                objective["region"] = region
                repaired_objectives.append(objective)
            experiment["objectives"] = repaired_objectives
            repaired_experiments.append(experiment)
        normalized["experiments"] = repaired_experiments
    return normalized


def _repair_unrequested_uncertainty(
    payload: dict[str, Any],
    source_question: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Stop an unrequested, self-inconsistent uncertainty block killing a route.

    A relative error model without a fraction in (0, 1] is rejected by
    ``UncertaintyPolicy``.  When the request never asked for uncertainty at all
    the block is the compiler's own invention, so recasting it as the absolute
    counterpart preserves the requested contract instead of losing the route.
    User-authored uncertainty semantics stay authoritative and still validate
    strictly.
    """

    uncertainty = payload.get("uncertainty")
    if not isinstance(uncertainty, Mapping):
        return payload, ()
    model = str(uncertainty.get("thickness_error_model") or "")
    if not model.startswith("relative"):
        return payload, ()
    try:
        fraction = float(uncertainty.get("thickness_relative_fraction") or 0.0)
    except (TypeError, ValueError):
        fraction = 0.0
    if 0.0 < fraction <= 1.0:
        return payload, ()
    if re.search(
        r"\b(?:uncertaint\w*|tolerance|sigma|deviation)\b|\+/-|±|percent|%",
        source_question,
        re.IGNORECASE,
    ):
        return payload, ()
    recast = "absolute_normal" if model.endswith("normal") else "absolute_uniform"
    repaired = dict(payload)
    block = dict(uncertainty)
    block["thickness_error_model"] = recast
    block["thickness_relative_fraction"] = 0.0
    repaired["uncertainty"] = block
    return repaired, (
        f"unrequested relative thickness uncertainty '{model}' carried no valid "
        f"fraction and was recast as '{recast}'",
    )


def _bounded_compiler_question(question: str) -> str:
    """Turn a discrete count range into one executable fixed-topology draft.

    The outer research loop may compare other counts in separate routes. A
    single TMM optimization contract is intentionally continuous-only.
    """

    matches = list(_DISCRETE_COUNT_RANGE_RE.finditer(question))
    directives: list[str] = []
    for match in matches:
        lo, hi = int(match.group(2)), int(match.group(3))
        if lo > hi:
            lo, hi = hi, lo
        fixed = (lo + hi) // 2
        directives.append(
            f"For this bounded executable route, fix {match.group(1).upper()}={fixed}; "
            "do not optimize the integer layer count inside the TMM task."
        )
    for match in _MIRROR_PAIR_RANGE_RE.finditer(question):
        lo, hi = sorted((int(match.group(1)), int(match.group(2))))
        fixed = (lo + hi) // 2
        directives.append(
            f"For this bounded route, fix N_H=N_L={fixed} mirror pairs, expand "
            f"both mirrors and the cavity into exactly {4 * fixed + 1} layers, "
            "and optimize thicknesses only."
        )
    directives.extend(
        [
            "Resolve every named material through the local material registry; ignore any request to manually supply Cauchy or Sellmeier coefficients.",
            "A request to report simulated or optimized results is a downstream output requirement, not a request for the compiler to invent results.",
            "If this bounded route declares an explicit material for every finite layer, that fixed sequence is authoritative. Do not request discrete material-choice optimization even if earlier background text lists additional allowed materials.",
            "Compile independent bounded relative thickness tolerance into the uncertainty policy; downstream deterministic code computes center transmittance and passband-width statistics.",
            "Map TE to s polarization and TM to p polarization for planar incidence.",
            "Count only coating layers between the incident and exit media as finite layers. Incident air and the named substrate are semi-infinite media, never coating layers. If an explicit sequence contains exactly the declared number of materials between those media, compile it without requesting clarification.",
            "Executable target thresholds are dimensionless fractions in [0, 1]: write 99% reflectance as 0.99, never as 99.",
            "For mode=\"optimize\", the objective lives at tmm_task.targets and the search settings at tmm_task.optimizer; neither is a key of the experiment itself. tmm_task.targets must hold at least one entry, and every entry needs a positive weight. A target that carries no weight, or an objective that scores every candidate alike, is not a scoreboard and will be rejected.",
        ]
    )
    return question.rstrip() + "\n\nCompiler policy directives:\n- " + "\n- ".join(directives)


def _clarification_is_policy_resolvable(rationale: str) -> bool:
    text = str(rationale or "").casefold()
    fixed_policy_issue = any(
        marker in text
        for marker in (
            "sellmeier",
            "cauchy",
            "report the optimal",
            "claiming achieved",
            "monte carlo",
            "sensitivity analysis",
            "integer number",
            "integer layer count",
            "discrete combinatorial",
            "not directly supported by the defined objective",
            "fixed topology",
            "finite layer",
            "layer sequence",
            "incident medium",
            "exit medium",
            "optimal p",
            "final design",
        )
    )
    bounded_thickness_issue = "thickness" in text and any(
        marker in text
        for marker in (
            "bound",
            "minimum",
            "maximum",
            "range",
            "non-physical",
        )
    )
    return fixed_policy_issue or bounded_thickness_issue


def _stable_task_id(benchmark_id: str | None, question: str) -> str:
    if benchmark_id:
        return f"tmm_{benchmark_id.casefold()}"
    prefix = _SAFE_ID_FRAGMENT.sub("_", question.casefold()).strip("_")[:48]
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()[:10]
    return f"tmm_{prefix or 'question'}_{digest}"


def _normalize_explicit_target_thresholds(
    draft: TMMTaskDraft,
    source_question: str,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Make explicit user percentages authoritative in executable targets.

    The model is allowed to propose a task, but it is not allowed to silently
    drop one member of a multi-band request.  Existing targets are corrected
    in place and missing targets are reconstructed from the user's explicit
    wavelength/observable/constraint clauses.  This is intentionally generic:
    it applies to R, T and A, point targets and wavelength bands.
    """

    def wavelength_nm(value: str, unit: str) -> float:
        number = float(value)
        return number * 1000.0 if unit.casefold() in {"um", "µm", "μm"} else number

    clauses: list[dict[str, Any]] = []
    target_source = (
        source_question.rsplit("Canonical user controls:", 1)[-1]
        if "Canonical user controls:" in source_question
        else source_question
    )
    global_ranges = [
        (wavelength_nm(lo, unit), wavelength_nm(hi, unit))
        for lo, hi, unit in _BAND_RANGE_RE.findall(target_source)
    ]
    for raw_clause in re.split(
        r"[;\n]|(?<!\d)\.(?!\d)|,\s*(?:while|and)\b",
        target_source,
    ):
        text = raw_clause.strip()
        percentages = list(_PERCENT_RE.finditer(text))
        for index, percentage in enumerate(percentages):
            left = 0
            if index:
                between = text[percentages[index - 1].end() : percentage.start()]
                separator = list(re.finditer(r"\band\b", between, re.IGNORECASE))
                left = (
                    percentages[index - 1].end() + separator[-1].end()
                    if separator
                    else (percentages[index - 1].end() + percentage.start()) // 2
                )
            right = len(text)
            if index + 1 < len(percentages):
                between = text[percentage.end() : percentages[index + 1].start()]
                separator = re.search(r"\band\b", between, re.IGNORECASE)
                right = (
                    percentage.end() + separator.start()
                    if separator
                    else (percentage.end() + percentages[index + 1].start()) // 2
                )
            local = text[left:right].strip()
            lower = local.casefold()
            if re.search(
                r"(?:<=|at most|no more than|less than or equal|at or below)",
                lower,
            ):
                constraint = "at_most"
            elif re.search(
                r"(?:>=|at least|no less than|greater than or equal|at or above)",
                lower,
            ):
                constraint = "at_least"
            else:
                continue
            observable = None
            for key, patterns in {
                "T": ("transmittance", "transmission"),
                "R": ("reflectance", "reflection"),
                "A": ("absorptance", "absorption"),
            }.items():
                if any(pattern in lower for pattern in patterns):
                    observable = key
                    break
            if observable is None:
                continue
            aggregation = "worst_case" if re.search(
                r"\bworst(?:\s*[- ]\s*case)?\b|maximum", lower
            ) else "mean"
            ranges = [
                (wavelength_nm(lo, unit), wavelength_nm(hi, unit))
                for lo, hi, unit in _BAND_RANGE_RE.findall(local)
            ]
            # A common request shape declares one shared evaluation band in
            # the preceding sentence and then lists several targets.  Bind
            # those targets to that sole explicit band instead of accepting a
            # model-invented simulation interval.
            if not ranges and len(set(global_ranges)) == 1:
                ranges = list(dict.fromkeys(global_ranges))
            points = [
                float(match.group(1)) for match in _POINT_NM_RE.finditer(local)
            ]
            clauses.append(
                {
                    "target": float(percentage.group(1)) / 100.0,
                    "constraint": constraint,
                    "observable": observable,
                    "aggregation": aggregation,
                    "ranges": ranges,
                    "points": points,
                    "source_clause": local,
                }
            )
    clauses = list(
        {
            (
                item["target"],
                item["constraint"],
                item["observable"],
                item["aggregation"],
                tuple(item["ranges"]),
                tuple(item["points"]),
                item["source_clause"].casefold(),
            ): item
            for item in clauses
        }.values()
    )
    if not clauses:
        return draft, ()

    corrections: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        if experiment.mode.value != "optimize":
            experiments.append(experiment)
            continue
        task_payload = dict(experiment.tmm_task)
        simulation = dict(task_payload.get("simulation") or {})
        spectrum = dict(simulation.get("spectrum") or {})
        spectral_lo = float(spectrum.get("start_nm", 0.0) or 0.0)
        spectral_hi = float(spectrum.get("stop_nm", 0.0) or 0.0)
        declared_values = [
            value
            for clause in clauses
            for interval in clause["ranges"]
            for value in interval
        ] + [
            point for clause in clauses for point in clause["points"]
        ]
        if declared_values:
            requested_lo = min(declared_values)
            requested_hi = max(declared_values)
            if requested_lo < spectral_lo or requested_hi > spectral_hi:
                spectrum["start_nm"] = min(spectral_lo, requested_lo) if spectral_lo else requested_lo
                spectrum["stop_nm"] = max(spectral_hi, requested_hi) if spectral_hi else requested_hi
                simulation["spectrum"] = spectrum
                task_payload["simulation"] = simulation
                spectral_lo = float(spectrum["start_nm"])
                spectral_hi = float(spectrum["stop_nm"])
                corrections.append(
                    f"{experiment.experiment_id}: expanded simulation spectrum to "
                    f"{spectral_lo:g}-{spectral_hi:g} nm for explicit user targets"
                )
        illumination = dict(simulation.get("illumination") or {})
        angles = list(illumination.get("angles_deg") or [0.0])
        polarizations = list(illumination.get("polarizations") or ["unpolarized"])
        # Explicit numerical requirements are the canonical route-comparison
        # contract.  Do not retain model-authored R/T/A targets alongside
        # them: duplicate targets, invented weights, or omitted channels would
        # otherwise make two routes answer the same user request with
        # different rulers.  Compound/report-only objectives live in the
        # separate objective list and are preserved below.
        targets: list[dict[str, Any]] = []
        skipped_unqualified = 0
        for clause_index, clause in enumerate(clauses, start=1):
            intervals = list(clause["ranges"]) or [
                (point, point) for point in clause["points"]
            ]
            if not intervals:
                # An unqualified threshold is unambiguous only when the request
                # declares no band or exactly one band.  With two or more
                # distinct bands, binding it to the min-to-max envelope would
                # invent a combined band the user never requested.
                if len(set(global_ranges)) >= 2:
                    skipped_unqualified += 1
                    continue
                intervals = [(spectral_lo, spectral_hi)]
            for interval_index, (lo, hi) in enumerate(intervals, start=1):
                if spectral_lo and spectral_hi and not (
                    spectral_lo <= lo <= hi <= spectral_hi
                ):
                    continue
                for angle in angles:
                    target_polarizations = _target_channels_from_clause(
                        str(clause.get("source_clause") or ""),
                        observable=str(clause["observable"]),
                        available_polarizations=polarizations,
                    )
                    for polarization in target_polarizations:
                        observable = str(clause["observable"]).casefold()
                        name = (
                            f"canonical_{observable}_{lo:g}_{hi:g}_"
                            f"{clause['constraint']}_{clause['aggregation']}_"
                            f"{float(angle):g}_{polarization}_{clause_index}_{interval_index}"
                        )
                        targets.append(
                            {
                                "observable": clause["observable"],
                                "target": clause["target"],
                                "wavelength_min_nm": lo,
                                "wavelength_max_nm": hi,
                                "weight": 1.0,
                                "angle_deg": float(angle),
                                "polarization": str(polarization),
                                "constraint": clause["constraint"],
                                "aggregation": clause["aggregation"],
                                "name": _SAFE_ID_FRAGMENT.sub("_", name).strip("_")[:96],
                            }
                        )
                        corrections.append(
                            f"{experiment.experiment_id}.{name}: rebuilt canonical executable "
                            "target from explicit user clause"
                        )
        if skipped_unqualified:
            corrections.append(
                f"{experiment.experiment_id}: skipped {skipped_unqualified} ambiguous "
                "unqualified target clause(s) because multiple distinct wavelength "
                "bands were declared"
            )
        if targets:
            task_payload["targets"] = targets
        elif skipped_unqualified:
            corrections.append(
                f"{experiment.experiment_id}: retained model draft targets because no "
                "canonical target clause was unambiguously band-qualified"
            )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {**experiment.model_dump(mode="json"), "tmm_task": task_payload}
            )
        )
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(corrections)


def _normalize_explicit_illumination(
    draft: TMMTaskDraft,
    source_question: str,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Make explicitly requested angle/polarization channels authoritative."""

    requested_angles, requested_polarizations = _requested_illumination(source_question)
    if not requested_angles and not requested_polarizations:
        return draft, ()

    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload = dict(experiment.tmm_task)
        if experiment.mode.value == "optimize":
            simulation = dict(payload.get("simulation") or {})
        else:
            simulation = dict(payload)
        illumination = dict(simulation.get("illumination") or {})
        existing_angles = [float(value) for value in illumination.get("angles_deg") or [0.0]]
        existing_polarizations = [
            str(value) for value in illumination.get("polarizations") or ["unpolarized"]
        ]
        final_angles = requested_angles or existing_angles
        final_polarizations = requested_polarizations or existing_polarizations
        if existing_angles != final_angles or existing_polarizations != final_polarizations:
            illumination["angles_deg"] = final_angles
            illumination["polarizations"] = final_polarizations
            simulation["illumination"] = illumination
            if experiment.mode.value == "optimize":
                payload["simulation"] = simulation
            else:
                payload = simulation
            changes.append(
                f"{experiment.experiment_id}: synchronized explicit illumination "
                f"angles={final_angles}, polarizations={final_polarizations}"
            )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {**experiment.model_dump(mode="json"), "tmm_task": payload}
            )
        )
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


def _normalize_target_angle_coverage(
    draft: TMMTaskDraft,
    source_question: str,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Score every incidence angle the request names, not just one of them.

    ``_normalize_explicit_illumination`` makes the requested angles authoritative
    for what gets *simulated*.  Nothing constrained what gets *scored*, so a
    request naming three angles routinely compiled to an objective covering a
    single angle -- and a different one after each revision.  Two harms follow:
    the optimizer never sees the binding channel, so it trades that channel away
    freely; and two revisions of one route report scores computed against
    different objectives, which makes their difference meaningless as evidence
    of improvement.  Replicating each angle-bearing target across the missing
    requested angles completes the contract the request already stated, and the
    spectra for those angles are already computed because the illumination block
    carries them.
    """

    requested_angles, _ = _requested_illumination(source_question)
    if len(requested_angles) < 2:
        return draft, ()

    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload = dict(experiment.tmm_task)
        targets = payload.get("targets")
        if not isinstance(targets, list) or not targets:
            experiments.append(experiment)
            continue
        angled = [
            target
            for target in targets
            if isinstance(target, Mapping) and target.get("angle_deg") is not None
        ]
        covered = {float(target["angle_deg"]) for target in angled}
        # The null-angle shorthand is already expanded upstream, so an objective
        # whose angles collectively span the request needs nothing further.
        # Only an explicitly-but-partially angled objective is repaired here.
        if not angled or all(
            any(abs(angle - value) <= 1e-6 for value in covered)
            for angle in requested_angles
        ):
            experiments.append(experiment)
            continue
        # Group by every field except the angle and the angle-derived name, so
        # "R >= 0.92, s-polarized" is recognised as one objective that happens
        # to be scored at some of the requested angles and not others.
        groups: dict[str, list[Mapping[str, Any]]] = {}
        for target in angled:
            key = json.dumps(
                {
                    name: value
                    for name, value in target.items()
                    if name not in ("angle_deg", "name")
                },
                sort_keys=True,
                default=str,
            )
            groups.setdefault(key, []).append(target)
        additions: list[dict[str, Any]] = []
        for members in groups.values():
            group_angles = {float(member["angle_deg"]) for member in members}
            template = dict(members[0])
            for angle in requested_angles:
                if any(abs(angle - value) <= 1e-6 for value in group_angles):
                    continue
                addition = {**template, "angle_deg": angle}
                addition["name"] = _retarget_angle_name(
                    template.get("name"),
                    float(template["angle_deg"]),
                    angle,
                )
                additions.append(addition)
        if not additions:
            experiments.append(experiment)
            continue
        payload["targets"] = [*targets, *additions]
        changes.append(
            f"{experiment.experiment_id}: objective scored {sorted(covered)} of "
            f"requested angles {requested_angles}; added {len(additions)} "
            f"target(s) so every requested angle is scored"
        )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {**experiment.model_dump(mode="json"), "tmm_task": payload}
            )
        )
    if not changes:
        return draft, ()
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


_OBJECTIVE_IDENTITY_FIELDS = (
    "observable",
    "constraint",
    "target",
    "wavelength_min_nm",
    "wavelength_max_nm",
    "angle_deg",
    "polarization",
    "weight",
    "aggregation",
)


def _objective_targets(draft: TMMTaskDraft) -> list[dict[str, Any]]:
    """Collect every scored target in the draft, across all experiments."""

    collected: list[dict[str, Any]] = []
    for experiment in draft.experiments:
        for target in experiment.tmm_task.get("targets") or []:
            if isinstance(target, Mapping):
                collected.append(dict(target))
    return collected


def _objective_signature(targets: Sequence[Mapping[str, Any]]) -> str:
    """Identify a scoreboard by what it measures, not by how it is written.

    Only the fields that change a score participate, so a renamed or reordered
    target set keeps its identity while a changed threshold, angle, or
    polarization does not.  Two revisions sharing a signature are comparable;
    two that do not, are not, whatever their reported scores suggest.
    """

    canonical = sorted(
        json.dumps(
            {field: target.get(field) for field in _OBJECTIVE_IDENTITY_FIELDS},
            sort_keys=True,
            default=str,
        )
        for target in targets
    )
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()[:16]


def _objective_sanity_problems(
    targets: Sequence[Mapping[str, Any]],
    requested_angles: Sequence[float],
    requested_polarizations: Sequence[str],
) -> tuple[str, ...]:
    """Reject a scoreboard that cannot rank designs or that ignores a channel.

    Freezing removes the run's ability to repair its objective later, so what
    gets frozen has to be usable.  Two shapes are not, and both are checked
    against how the objective is actually consumed rather than against how it
    reads.

    Deliberately *not* checked: a threshold sitting at a numeric extreme.
    ``at_least 0.0`` and ``at_least 1.0`` both look broken and neither is.  The
    optimizer's loss ignores the threshold entirely -- ``at_least`` drives the
    band toward 1 and ``at_most`` toward 0 whatever the number says -- and the
    reported soft score, ``observed / (observed + scale)``, stays monotone in
    the observation for every threshold because ``scale`` floors at 0.05.  An
    extreme threshold therefore still ranks candidates correctly *within* one
    revision; what it destroys is comparability *across* revisions, since it
    rescales the score.  That is the freeze's job, not a gate's, and rejecting
    such objectives here would refuse legitimate minimize-R and maximize-R
    formulations that the fixtures rely on.
    """

    if not targets:
        return ("objective has no targets, so every candidate scores alike",)

    problems: list[str] = []
    if not any(
        float(target.get("weight") or 0.0) > 0.0
        for target in targets
        if isinstance(target.get("weight"), (int, float))
        and not isinstance(target.get("weight"), bool)
    ):
        problems.append(
            "every target carries zero weight, so the aggregate cannot rank designs"
        )

    # A channel absent from the objective is absent from the optimizer's loss,
    # which sums only over the targets present.  The optimizer is then free to
    # trade that channel away at no cost -- and for a wide-angle stack the
    # channel that binds is exactly the one a partial objective tends to omit.
    scored = {
        (float(target["angle_deg"]), str(target["polarization"]))
        for target in targets
        if target.get("angle_deg") is not None
        and target.get("polarization") is not None
    }
    if requested_angles and requested_polarizations and scored:
        missing = [
            f"{angle:g}|{polarization}"
            for angle in requested_angles
            for polarization in requested_polarizations
            if not any(
                abs(angle - scored_angle) <= 1e-6
                and polarization == scored_polarization
                for scored_angle, scored_polarization in scored
            )
        ]
        if missing:
            problems.append(
                "objective never scores requested channel(s) " + ", ".join(missing)
            )
    return tuple(dict.fromkeys(problems))


def _apply_frozen_objective(
    draft: TMMTaskDraft,
    frozen_targets: Sequence[Mapping[str, Any]],
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Score every revision against the objective the run started with.

    A revision that rewrites its own targets makes its score incomparable with
    the score it is meant to improve on -- the one number a feedback loop
    exists to produce.  Two distinct harms were observed, with different
    mechanisms.  Rewriting a threshold (a reflectance floor moved to 1.0 in one
    revision and to 0.0 in the next) leaves the optimizer's loss untouched but
    rescales the reported soft score, so three near-identical designs reported
    0.65, 0.48 and 0.95 on physics that barely moved.  Dropping targets (nine
    down to three, losing the transmittance and absorptance terms) changes the
    loss itself, because the loss sums only over the targets present.

    Freezing the scoreboard leaves a route free to change what it tries --
    layer count, bounds, optimizer, initialization -- and removes only its
    ability to change what it is graded on.  A route that believes the
    objective is unattainable now has to report that as a finding instead of
    lowering the bar and reporting success.
    """

    frozen = [dict(target) for target in frozen_targets]
    frozen_signature = _objective_signature(frozen)
    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload = dict(experiment.tmm_task)
        current = payload.get("targets")
        if not isinstance(current, list):
            experiments.append(experiment)
            continue
        present = [target for target in current if isinstance(target, Mapping)]
        if _objective_signature(present) == frozen_signature:
            experiments.append(experiment)
            continue
        payload["targets"] = [dict(target) for target in frozen]
        # The revision that produced this draft may have narrowed the sweep --
        # or widened it past the frozen channel -- so the objective has to be
        # re-admitted before the payload is validated, not after.
        payload, added_angles, added_polarizations = _declare_target_channels(payload)
        changes.append(
            f"{experiment.experiment_id}: replaced a revised objective "
            f"({len(present)} target(s), signature {_objective_signature(present)}) "
            f"with the objective frozen at run start ({len(frozen)} target(s), "
            f"signature {frozen_signature})"
        )
        if added_angles or added_polarizations:
            changes.append(
                f"{experiment.experiment_id}: "
                f"{_channel_declaration_note(added_angles, added_polarizations)}"
            )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {**experiment.model_dump(mode="json"), "tmm_task": payload}
            )
        )
    if not changes:
        return draft, ()
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


def _target_is_gradable(target: Any) -> bool:
    """Mirror the engine's admission rule for a single objective term.

    ``SpectralTarget.validate`` admits a term only with a weight above zero and
    a threshold inside [0, 1].  Checking the same two conditions here lets the
    compiler tell "this route revised its objective" apart from "this route
    emitted something that is not an objective at all".
    """

    if not isinstance(target, Mapping):
        return False
    try:
        weight = float(target.get("weight", 1.0))
        threshold = float(target["target"])
    except (KeyError, TypeError, ValueError):
        return False
    return weight > 0.0 and 0.0 <= threshold <= 1.0


def _restore_frozen_objective_payload(
    payload: Any,
    frozen_targets: Sequence[Mapping[str, Any]],
) -> tuple[Any, tuple[str, ...]]:
    """Put the run's frozen objective back before the schema sees the draft.

    ``_apply_frozen_objective`` is the authoritative pass, but it runs on a
    validated ``TMMTaskDraft``.  A revision that drops its targets entirely, or
    zeroes their weights, never reaches it: ``OptimizationTask`` rejects an
    objective with no target and a target without positive weight, so the draft
    dies at ``model_validate`` and the freeze -- the mechanism that exists to
    stop a route rewriting its own scoreboard -- is bypassed by the most
    complete rewrite of all.  This applies the same rule one stage earlier and
    only to objectives that could not be graded as written; a merely revised
    objective is still left to the authoritative pass.
    """

    if not frozen_targets or not isinstance(payload, Mapping):
        return payload, ()
    experiments = payload.get("experiments")
    if not isinstance(experiments, list):
        return payload, ()
    frozen = [dict(target) for target in frozen_targets]
    frozen_signature = _objective_signature(frozen)
    changes: list[str] = []
    rebuilt: list[Any] = []
    for experiment in experiments:
        task = (
            experiment.get("tmm_task") if isinstance(experiment, Mapping) else None
        )
        if (
            not isinstance(experiment, Mapping)
            or experiment.get("mode") != "optimize"
            or not isinstance(task, Mapping)
        ):
            rebuilt.append(experiment)
            continue
        declared = task.get("targets")
        declared = declared if isinstance(declared, list) else []
        if declared and all(_target_is_gradable(item) for item in declared):
            rebuilt.append(experiment)
            continue
        task_payload = dict(task)
        task_payload["targets"] = [dict(target) for target in frozen]
        task_payload, added_angles, added_polarizations = _declare_target_channels(
            task_payload
        )
        experiment_id = str(experiment.get("experiment_id") or "experiment")
        changes.append(
            f"{experiment_id}: restored the objective frozen at run start "
            f"({len(frozen)} target(s), signature {frozen_signature}) before "
            f"validation because the revision "
            + (
                "declared no target"
                if not declared
                else f"declared {len(declared)} ungradable target(s)"
            )
        )
        if added_angles or added_polarizations:
            changes.append(
                f"{experiment_id}: "
                f"{_channel_declaration_note(added_angles, added_polarizations)}"
            )
        rebuilt.append({**experiment, "tmm_task": task_payload})
    if not changes:
        return payload, ()
    return {**payload, "experiments": rebuilt}, tuple(changes)


def _channel_declaration_note(
    added_angles: tuple[float, ...],
    added_polarizations: tuple[str, ...],
) -> str:
    detail: list[str] = []
    if added_angles:
        detail.append("angles " + ", ".join(f"{value:g}" for value in added_angles))
    if added_polarizations:
        detail.append("polarizations " + ", ".join(added_polarizations))
    return (
        f"declared {' and '.join(detail)} in the simulation illumination because "
        "the objective scores that channel"
    )


def _declare_target_channels(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], tuple[float, ...], tuple[str, ...]]:
    """Widen one task payload's sweep to cover the channels its targets score.

    ``OptimizationTask.validate`` admits a target only when its
    ``(angle_deg, polarization)`` pair is a member of the cross product of the
    simulation illumination.  Several normalizations rewrite one side of that
    pair without the other: the explicit-illumination repair and the two
    coverage completions re-derive the channel set from the *revised* request
    text, while the objective freeze re-imposes the objective the run started
    with.  A revision that widens the request ("evaluate at 0, 30, 45 and 60
    degrees", "TE/TM average") therefore lands a frozen normal-incidence,
    unpolarized target in a stack that no longer declares that channel, and
    the route dies reporting that the model failed to produce a valid bounded
    task -- a compiler fault attributed to the model, and one no retry can
    clear because nothing about it is stochastic.

    Widening the sweep is the only repair that leaves the scoreboard intact.
    Dropping or rewriting a frozen target is precisely what the freeze exists
    to prevent, so the channel set moves instead: simulating an additional
    channel costs forward evaluations and changes no score.
    """

    targets = payload.get("targets")
    simulation = payload.get("simulation")
    if not isinstance(targets, list) or not isinstance(simulation, Mapping):
        return payload, (), ()
    illumination = dict((simulation.get("illumination") or {}))
    angles = [float(item) for item in (illumination.get("angles_deg") or ())]
    polarizations = [str(item) for item in (illumination.get("polarizations") or ())]
    added_angles: list[float] = []
    added_polarizations: list[str] = []
    for target in targets:
        if not isinstance(target, Mapping):
            continue
        raw_angle = target.get("angle_deg")
        if raw_angle is not None:
            angle = float(raw_angle)
            # An out-of-range angle is a different contract breach and stays
            # the engine validator's call; admitting one here would only move
            # the failure downstream.
            if 0.0 <= angle < 90.0 and not any(
                abs(angle - item) <= 1e-9 for item in angles
            ):
                angles.append(angle)
                added_angles.append(angle)
        raw_polarization = target.get("polarization")
        if raw_polarization is not None:
            polarization = str(raw_polarization)
            if (
                polarization in ("s", "p", "unpolarized")
                and polarization not in polarizations
            ):
                polarizations.append(polarization)
                added_polarizations.append(polarization)
    if not added_angles and not added_polarizations:
        return payload, (), ()
    illumination["angles_deg"] = angles
    illumination["polarizations"] = polarizations
    payload = {
        **payload,
        "simulation": {**dict(simulation), "illumination": illumination},
    }
    return payload, tuple(added_angles), tuple(added_polarizations)


def _reconcile_illumination_with_targets(
    draft: TMMTaskDraft,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Apply :func:`_declare_target_channels` to every experiment in a draft.

    The freeze repairs its own payload inline, before it validates.  This pass
    is the backstop for the normalizations that narrow the sweep without
    touching the objective at all, and it runs last so one enforcement covers
    all of them.
    """

    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload, added_angles, added_polarizations = _declare_target_channels(
            dict(experiment.tmm_task)
        )
        if not added_angles and not added_polarizations:
            experiments.append(experiment)
            continue
        changes.append(
            f"{experiment.experiment_id}: "
            f"{_channel_declaration_note(added_angles, added_polarizations)}"
        )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {**experiment.model_dump(mode="json"), "tmm_task": payload}
            )
        )
    if not changes:
        return draft, ()
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


def _normalize_target_polarization_coverage(
    draft: TMMTaskDraft,
    source_question: str,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Score every polarization the request names, not only one of them.

    ``_normalize_target_angle_coverage`` completes the angle axis but groups
    targets by every field except the angle -- polarization included -- so an
    objective naming a single polarization stays single-polarization however
    many angles it acquires.  Both axes then read "complete" while the channel
    that actually binds the design is never scored at all: for a quarter-wave
    stack evaluated near Brewster incidence the binding channel is ``60 deg |
    p``, and an all-``s`` objective leaves the optimizer free to trade it away.

    Completion is guarded by the experiment-wide union of scored polarizations
    rather than applied per group.  Dropping polarization from a grouping key
    and taking the Cartesian product would invent targets contradicting an
    author's deliberate per-polarization thresholds -- an objective stating
    ``R >= 0.95 (s)`` and ``R >= 0.80 (p)`` would acquire ``R >= 0.95 (p)``,
    a requirement nobody asked for.  Replicating only when the union misses a
    requested polarization leaves every such objective untouched and repairs
    only the case where a polarization is absent outright.  Angle completion
    runs afterwards and fills the remaining grid cells per group.
    """

    _, requested_polarizations = _requested_illumination(source_question)
    if len(requested_polarizations) < 2:
        return draft, ()

    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload = dict(experiment.tmm_task)
        targets = payload.get("targets")
        if not isinstance(targets, list) or not targets:
            experiments.append(experiment)
            continue
        polarized = [
            target
            for target in targets
            if isinstance(target, Mapping) and target.get("polarization") is not None
        ]
        # An unpolarized objective is expanded upstream; only an explicitly but
        # partially polarized one is repaired here.
        if not polarized:
            experiments.append(experiment)
            continue
        covered = {str(target["polarization"]) for target in polarized}
        missing = [
            polarization
            for polarization in requested_polarizations
            if polarization not in covered
        ]
        if not missing:
            experiments.append(experiment)
            continue
        used_names = {
            str(target.get("name"))
            for target in targets
            if isinstance(target, Mapping) and target.get("name") is not None
        }
        additions: list[dict[str, Any]] = []
        for polarization in missing:
            for target in polarized:
                addition = {**target, "polarization": polarization}
                name = _retarget_polarization_name(
                    target.get("name"),
                    str(target["polarization"]),
                    polarization,
                )
                if isinstance(name, str):
                    if name in used_names:
                        name = f"{name}_{polarization}pol"
                    used_names.add(name)
                addition["name"] = name
                additions.append(addition)
        payload["targets"] = [*targets, *additions]
        changes.append(
            f"{experiment.experiment_id}: objective scored polarizations "
            f"{sorted(covered)} of requested {requested_polarizations}; added "
            f"{len(additions)} target(s) so every requested polarization is scored"
        )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {**experiment.model_dump(mode="json"), "tmm_task": payload}
            )
        )
    if not changes:
        return draft, ()
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


def _retarget_polarization_name(
    name: Any, old_polarization: str, new_polarization: str
) -> Any:
    """Keep target names unique when a target is replicated to another polarization.

    Generated names embed the polarization as a standalone token
    (``global_reflectance_30_s``), so substituting that token yields the name
    the upstream expansion would have produced.  The lookaround keeps the
    substitution off letters inside a word, and the caller still checks the
    result for collisions because a name may carry the token more than once.
    """

    if not isinstance(name, str) or not name:
        return name
    substituted, count = re.subn(
        rf"(?<![0-9A-Za-z]){re.escape(old_polarization)}(?![0-9A-Za-z])",
        new_polarization,
        name,
        count=1,
    )
    if count:
        return substituted
    return f"{name}_{new_polarization}pol"


def _retarget_angle_name(name: Any, old_angle: float, new_angle: float) -> Any:
    """Keep target names unique when a target is replicated to another angle.

    Names generated for angled targets embed the angle
    (``global_reflectance_30_s``), so substituting the angle token yields the
    same name the upstream expansion would have produced.  When no angle token
    is present a deterministic suffix preserves uniqueness instead.
    """

    if not isinstance(name, str) or not name:
        return name
    old_token = f"{old_angle:g}"
    new_token = f"{new_angle:g}"
    substituted, count = re.subn(
        rf"(?<![0-9.]){re.escape(old_token)}(?![0-9.])", new_token, name, count=1
    )
    if count:
        return substituted
    return f"{name}_a{new_token}"


def _target_channels_from_clause(
    local_clause: str,
    *,
    observable: str,
    available_polarizations: list[str],
) -> list[str]:
    """Resolve an explicitly paired polarization objective conservatively.

    ``TE reflectance`` and ``TM transmittance`` are selective objectives, not
    shorthand for evaluating both observables on every polarization.  When
    no pairing is explicit, all simulated polarizations remain applicable.
    """

    lower = local_clause.casefold()
    observable_words = {
        "R": r"reflectance|reflection",
        "T": r"transmittance|transmission",
        "A": r"absorptance|absorption",
    }[observable]
    te_near = bool(
        re.search(
            rf"\b(?:te|s[- ]?pol(?:ari[sz](?:ed|ation))?)\b[^.;,]{{0,48}}"
            rf"\b(?:{observable_words})\b",
            lower,
        )
    )
    tm_near = bool(
        re.search(
            rf"\b(?:tm|p[- ]?pol(?:ari[sz](?:ed|ation))?)\b[^.;,]{{0,48}}"
            rf"\b(?:{observable_words})\b",
            lower,
        )
    )
    # Also support the natural order "reflectance for TE".
    if re.search(rf"\b(?:{observable_words})\b[^.;,]{{0,32}}\b(?:TE|s[- ]?pol)\b", local_clause, re.IGNORECASE):
        te_near = True
    if re.search(rf"\b(?:{observable_words})\b[^.;,]{{0,32}}\b(?:TM|p[- ]?pol)\b", local_clause, re.IGNORECASE):
        tm_near = True
    selected = []
    if te_near and "s" in available_polarizations:
        selected.append("s")
    if tm_near and "p" in available_polarizations:
        selected.append("p")
    return selected or available_polarizations


def _normalize_named_substrate(
    draft: TMMTaskDraft,
    source_question: str,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Preserve explicitly named substrate dispersion in every experiment."""

    text = source_question.casefold()
    substrate_material: str | None = None
    if re.search(r"\b(?:fused[ -]?silica|silica)\s+substrate\b", text):
        substrate_material = "sio2"
    if substrate_material is None:
        return draft, ()

    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload = dict(experiment.tmm_task)
        simulation = (
            dict(payload.get("simulation") or {})
            if experiment.mode.value == "optimize"
            else dict(payload)
        )
        stack = dict(simulation.get("stack") or {})
        exit_medium = dict(stack.get("exit") or {})
        if str(exit_medium.get("material") or "").casefold() != substrate_material:
            stack["exit"] = {"material": substrate_material, "constant_k": 0.0}
            simulation["stack"] = stack
            if experiment.mode.value == "optimize":
                payload["simulation"] = simulation
            else:
                payload = simulation
            changes.append(
                f"{experiment.experiment_id}: explicit fused-silica substrate "
                "uses local sio2 dispersion instead of a constant index"
            )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {**experiment.model_dump(mode="json"), "tmm_task": payload}
            )
        )
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


_PRODUCTION_SOLVERS: tuple[str, ...] = ("smatrix", "byrnes")
_DIAGNOSTIC_SOLVERS: frozenset[str] = frozenset({"characteristic"})


def _normalize_diagnostic_solver(
    draft: TMMTaskDraft,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Keep production tasks off the engine's diagnostic-only solver.

    The engine accepts three solver names but only two of them are meant to
    run experiments.  The characteristic-matrix path is kept as a comparison
    reference -- its own docstring says so -- and it chooses the opposite sign
    convention from the branch it selects for the propagation constant, so an
    absorbing layer amplifies instead of attenuating.  ``R + T + A`` then
    leaves 1 by far more than the acceptance tolerance and the physics gate
    correctly rejects every candidate, which costs the round its whole budget
    and reports a solver disagreement instead of anything actionable.  The
    prompt no longer offers the name; rewrite it here too, because a prompt is
    a soft constraint and this failure consumes an entire route.
    """

    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload = dict(experiment.tmm_task)
        simulation = (
            dict(payload.get("simulation") or {})
            if experiment.mode.value == "optimize"
            else dict(payload)
        )
        requested = str(simulation.get("solver") or "").casefold()
        if requested in _DIAGNOSTIC_SOLVERS:
            layers = (simulation.get("stack") or {}).get("layers") or ()
            incoherent = any(
                isinstance(layer, dict)
                and str(layer.get("coherence") or "").casefold() == "incoherent"
                for layer in layers
            )
            replacement = "byrnes" if incoherent else "smatrix"
            simulation["solver"] = replacement
            if experiment.mode.value == "optimize":
                payload["simulation"] = simulation
            else:
                payload = simulation
            changes.append(
                f"{experiment.experiment_id}: solver {requested} is a diagnostic "
                f"reference implementation, not a production solver; compiled "
                f"with {replacement} instead"
            )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {**experiment.model_dump(mode="json"), "tmm_task": payload}
            )
        )
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


def _target_band_within(
    target: Mapping[str, Any],
    band: tuple[float, float],
) -> bool:
    """Whether the target's interval sits inside the scored band.

    Containment rather than overlap, because a route that aims at a different
    interval is expressing a design trade-off, not a contradiction.  Wanting
    low reflectance across the visible is a legitimate goal even while the
    study ranks by high reflectance in the ultraviolet; only a target confined
    to the scored band can be said to disagree with the standard about it.
    """

    try:
        low = float(target["wavelength_min_nm"])
        high = float(target["wavelength_max_nm"])
    except (KeyError, TypeError, ValueError):
        return False
    if high < low:
        low, high = high, low
    return low >= band[0] and high <= band[1]


def _align_targets_with_scoring_standard(
    draft: TMMTaskDraft,
    standard: ScoringStandard,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Stop a route from optimizing against the direction it is ranked by.

    ``_synchronize_objectives_from_targets`` derives each objective's sense
    from its target's constraint, so a constraint written the wrong way round
    silently reverses what the optimizer pursues.  That is not hypothetical:
    asked to maximize mean reflectance in a band, a compiler wrote
    ``at_most`` with target 0.0 and explained in its own rationale that
    "maximization maps to mean R<=0.0".  The draft satisfied every schema, so
    nothing objected, and the route spent its whole round budget driving one
    of the two scored numbers toward zero.

    The frozen standard already states which direction is better for that
    measurement, so the disagreement is decidable without asking the model
    again -- repaired here rather than sent back as a validation error for the
    same reason the other normalizers in this module repair in place: a paid
    retry buys nothing when the correct value is known.

    The standard's per-metric ``sense`` is taken at face value.  A formula that
    subtracted a metric it declares as ``maximize`` would make that reading
    wrong, but such a standard contradicts itself and is a problem where it is
    authored, not here.
    """

    metric_by_observable = {
        "R": "mean_reflectance",
        "T": "mean_transmittance",
        "A": "mean_absorption",
    }
    worst_case_metric_by_observable = {
        "R": "worst_case_reflectance",
        "T": "worst_case_transmittance",
        "A": "worst_case_absorption",
    }
    # Both aggregations of one observable answer to the same scored metric: a
    # standard that ranks by mean reflectance still means "more" when a route
    # writes its target as a worst case.
    scored_sense: dict[str, str] = {}
    scored_bands: dict[str, list[tuple[float, float]]] = {}
    for metric in standard.metrics:
        sense = str(metric.sense or "").casefold()
        if sense not in {"maximize", "minimize"}:
            continue
        bands = [band for band in metric.bands_nm()]
        if not bands:
            continue
        scored_sense[metric.metric] = sense
        scored_bands.setdefault(metric.metric, []).extend(bands)
    if not scored_sense:
        return draft, ()

    agreeing = {"maximize": ("at_least", 1.0), "minimize": ("at_most", 0.0)}
    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload = dict(experiment.tmm_task)
        targets = list(payload.get("targets") or [])
        rewritten: list[dict[str, Any]] = []
        touched = False
        for target in targets:
            if not isinstance(target, Mapping):
                rewritten.append(target)
                continue
            row = dict(target)
            observable = str(row.get("observable") or "").upper()
            aggregation = str(row.get("aggregation") or "mean")
            metric_name = (
                worst_case_metric_by_observable.get(observable)
                if aggregation == "worst_case"
                else metric_by_observable.get(observable)
            )
            canonical = metric_name
            if canonical not in scored_sense:
                # A worst-case target still disagrees with a mean-aggregated
                # standard, so fall back to the mean name for the lookup.
                canonical = metric_by_observable.get(observable)
            wanted = scored_sense.get(canonical or "")
            if wanted is None:
                rewritten.append(row)
                continue
            constraint = str(row.get("constraint") or "").casefold()
            implied = {"at_least": "maximize", "at_most": "minimize"}.get(constraint)
            if implied is None or implied == wanted:
                rewritten.append(row)
                continue
            bands = scored_bands.get(canonical or "") or ()
            if not any(_target_band_within(row, band) for band in bands):
                rewritten.append(row)
                continue
            new_constraint, new_target = agreeing[wanted]
            was_constraint = row.get("constraint")
            was_target = row.get("target")
            row["constraint"] = new_constraint
            row["target"] = new_target
            rewritten.append(row)
            touched = True
            changes.append(
                f"{experiment.experiment_id}: target "
                f"{row.get('name') or observable} pushed {observable} the wrong "
                f"way for the frozen standard, which ranks {canonical} as "
                f"{wanted} over "
                f"{row.get('wavelength_min_nm')}-{row.get('wavelength_max_nm')}nm; "
                f"rewrote {was_constraint} {was_target} as "
                f"{new_constraint} {new_target}"
            )
        if touched:
            payload["targets"] = rewritten
            experiments.append(
                TMMExperimentSpec.model_validate(
                    {**experiment.model_dump(mode="json"), "tmm_task": payload}
                )
            )
        else:
            experiments.append(experiment)
    if not changes:
        return draft, ()
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


def _apply_scoring_standard(
    draft: TMMTaskDraft,
    standard: ScoringStandard,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Make every experiment measure the numbers the frozen standard ranks by.

    Runs last, after the objective list has been rebuilt from the executable
    targets, so nothing can strip the standard's own objectives afterwards.
    Two things are needed for a route to be comparable at all: the simulation
    grid has to span every scored band, because the task contract rejects an
    objective whose interval falls outside it, and the objectives themselves
    have to be present so the runtime measures them.

    The injected objectives are report-only.  They are how a route gets
    measured, not what it is told to pursue -- routes still optimize their own
    targets, which is the whole reason for running more than one.
    """

    requirement = standard.spectral_requirement()
    injected = [
        preference.model_dump(mode="json")
        for preference in standard.objective_preferences()
    ]
    injected_ids = {str(item["objective_id"]) for item in injected}
    changes: list[str] = []
    experiments: list[TMMExperimentSpec] = []
    for experiment in draft.experiments:
        payload = dict(experiment.tmm_task)
        optimize = experiment.mode.value == "optimize"
        simulation = dict(payload.get("simulation") or {}) if optimize else dict(payload)
        grid, warnings = widen_spectral_grid(simulation.get("spectrum") or {}, requirement)
        if grid:
            simulation["spectrum"] = grid
        if optimize:
            payload["simulation"] = simulation
        else:
            payload = simulation
        for warning in warnings:
            changes.append(f"{experiment.experiment_id}: {warning}")
        kept = [
            item.model_dump(mode="json")
            for item in experiment.objectives
            if item.objective_id not in injected_ids
        ]
        objectives = tuple(
            ObjectivePreference.model_validate(item) for item in [*kept, *injected]
        )
        if objectives != experiment.objectives:
            changes.append(
                f"{experiment.experiment_id}: attached the {len(injected)} frozen "
                f"scoring objective(s) so this route is ranked by the same "
                f"standard as every other"
            )
        experiments.append(
            TMMExperimentSpec.model_validate(
                {
                    **experiment.model_dump(mode="json"),
                    "tmm_task": payload,
                    "objectives": objectives,
                }
            )
        )
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


def _synchronize_objectives_from_targets(
    draft: TMMTaskDraft,
) -> tuple[TMMTaskDraft, tuple[str, ...]]:
    """Keep ranking objectives consistent with the executable optimizer targets.

    Optimizer targets are the executable source of truth.  Simple R/T/A band
    objectives are rebuilt from them, while report-only and compound physics
    objectives are retained.  This prevents a valid two-stopband task from
    being rejected merely because a language model omitted one parallel
    ``objectives`` entry.
    """

    metric_by_observable = {
        "R": "mean_reflectance",
        "T": "mean_transmittance",
        "A": "mean_absorption",
    }
    worst_case_metric_by_observable = {
        "R": "worst_case_reflectance",
        "T": "worst_case_transmittance",
        "A": "worst_case_absorption",
    }
    sense_by_constraint = {
        "at_least": "maximize",
        "at_most": "minimize",
        "match": "match",
    }
    simple_metrics = set(metric_by_observable.values()) | set(
        worst_case_metric_by_observable.values()
    )
    experiments: list[TMMExperimentSpec] = []
    changes: list[str] = []
    for experiment in draft.experiments:
        if experiment.mode.value != "optimize":
            experiments.append(experiment)
            continue
        targets = list((experiment.tmm_task or {}).get("targets") or [])
        derived: list[dict[str, Any]] = []
        for index, target in enumerate(targets, start=1):
            observable = str(target.get("observable") or "").upper()
            constraint = str(target.get("constraint") or "")
            aggregation = str(target.get("aggregation") or "mean")
            metric = (
                worst_case_metric_by_observable.get(observable)
                if aggregation == "worst_case"
                else metric_by_observable.get(observable)
            )
            sense = sense_by_constraint.get(constraint)
            if metric is None or sense is None:
                continue
            raw_name = str(target.get("name") or f"target_{index}")
            objective_id = _SAFE_ID_FRAGMENT.sub("_", raw_name.casefold()).strip("_")
            objective_id = (objective_id or f"target_{index}")[:96]
            region: dict[str, Any] = {
                "wavelength_nm": [
                    float(target["wavelength_min_nm"]),
                    float(target["wavelength_max_nm"]),
                ]
            }
            if target.get("angle_deg") is not None:
                region["angle_deg"] = float(target["angle_deg"])
            if target.get("polarization") is not None:
                region["polarization"] = str(target["polarization"])
            derived.append(
                {
                    "objective_id": objective_id,
                    "metric": metric,
                    "sense": sense,
                    "weight": float(target.get("weight", 1.0) or 1.0),
                    "target": float(target["target"]),
                    "region": region,
                    "admission_role": "score_only",
                }
            )
        if not derived:
            experiments.append(experiment)
            continue
        retained = [
            item.model_dump(mode="json")
            for item in experiment.objectives
            if item.sense == "report" or item.metric not in simple_metrics
        ]
        synchronized = tuple(
            ObjectivePreference.model_validate(item) for item in [*retained, *derived]
        )
        if synchronized != experiment.objectives:
            changes.append(
                f"{experiment.experiment_id}: synchronized {len(derived)} simple "
                "objectives from executable targets"
            )
        experiments.append(experiment.model_copy(update={"objectives": synchronized}))
    return draft.model_copy(update={"experiments": tuple(experiments)}), tuple(changes)


_UNCERTAINTY_ANCHOR_RE = re.compile(
    r"\b(?:one[- ]sigma|1[- ]sigma|sigma|standard deviation|std\.?|"
    r"error|uncertainty|tolerance|deviation|perturbation|variation|"
    r"bounded\s+by|plus\s+or\s+minus|\+/-|±)\b",
    re.IGNORECASE,
)


def _bound_uncertainty_value_match(
    window: str,
) -> re.Match[str] | None:
    """Pick the percent or nm value semantically bound to the uncertainty phrase.

    All percent and nm candidates compete in one selection using their
    semantic relation to the uncertainty expression (sigma, standard
    deviation, error, tolerance, bounded-by, +/- ...).  Prepositional binding
    ("error of 2 nm", "standard deviation of 2 percent") is stronger than raw
    character proximity, and the unit of the winning candidate decides whether
    the uncertainty is relative or absolute.  An earlier optical goal
    percentage or a manufacturing bound is never selected merely because it
    appears first.
    """

    percent_pattern = re.compile(
        r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)",
        re.IGNORECASE,
    )
    nm_pattern = re.compile(
        r"(\d+(?:\.\d+)?)\s*nm\b",
        re.IGNORECASE,
    )
    anchors = [
        match.start() for match in _UNCERTAINTY_ANCHOR_RE.finditer(window)
    ]
    if not anchors:
        percent = re.search(percent_pattern, window)
        return (
            percent
            if percent is not None
            else re.search(nm_pattern, window)
        )
    best: re.Match[str] | None = None
    best_key: tuple[float, int, int] | None = None
    for pattern_order, pattern in enumerate((percent_pattern, nm_pattern)):
        for match in pattern.finditer(window):
            value_start = match.start()
            score: float | None = None
            for anchor in anchors:
                distance = float(abs(value_start - anchor))
                if anchor <= value_start:
                    between = window[anchor:value_start]
                    if (
                        len(between) <= 16
                        and re.search(
                            r"\b(?:of|at|to|by|:|=|plus\s+or\s+minus)\s*$",
                            between,
                            re.IGNORECASE,
                        )
                    ):
                        distance = min(distance, float(len(between)))
                if score is None or distance < score:
                    score = distance
            if score is None:
                continue
            key = (score, pattern_order, value_start)
            if best_key is None or key < best_key:
                best = match
                best_key = key
    return best


def _normalize_uncertainty_budget(
    policy: UncertaintyPolicy,
    source_question: str,
) -> tuple[UncertaintyPolicy, tuple[str, ...]]:
    """Bound model-invented Monte Carlo counts without hiding user requests."""

    constraint_source = (
        source_question.rsplit("Canonical user controls:", 1)[-1]
        if "Canonical user controls:" in source_question
        else source_question
    )
    explicit = re.findall(
        r"\b(\d{1,5})\s+(?:(?:monte\s+carlo|random)\s+)?"
        r"(?:samples?|draws?|reali[sz]ations?)\b",
        constraint_source,
        re.IGNORECASE,
    )
    if explicit:
        requested = max(int(item) for item in explicit)
        samples = min(requested, 10_000)
        reason = (
            f"explicit uncertainty sample count {requested} was preserved"
            if requested <= 10_000
            else "explicit uncertainty sample count was capped at the schema maximum 10000"
        )
    else:
        samples = min(int(policy.thickness_samples), 32)
        reason = (
            "model-invented uncertainty sample count was capped at 32"
            if samples != int(policy.thickness_samples)
            else ""
        )
    updates: dict[str, Any] = {
        "thickness_samples": samples,
        "material_dataset_policy": "evaluate_all_eligible",
    }
    reasons = [reason] if reason else []

    # User-authored uncertainty semantics are authoritative.  The model may
    # translate prose, but it must not turn an absolute one-sigma error into a
    # relative bounded error (or vice versa) on different routes.
    fragments = [
        item.strip()
        for item in re.split(r"[;\n]|(?<!\d)\.(?!\d)", constraint_source)
        if item.strip()
    ]
    assembled: list[str] = []
    index = 0
    while index < len(fragments):
        fragment = fragments[index]
        if not (
            re.search(
                r"\b(?:thickness|manufactur|fabricat)\w*\b",
                fragment,
                re.IGNORECASE,
            )
            and re.search(
                r"\b(?:error|uncertainty|tolerance|sigma|deviation)\b|\+/-|±|percent|%",
                fragment,
                re.IGNORECASE,
            )
        ):
            index += 1
            continue
        # A semicolon commonly separates the distribution clause from the
        # percentage value ("normal errors; sigma = 1.5%"). Reattach the
        # adjacent fragment before interpreting the contract.
        clause = fragment
        if index + 1 < len(fragments) and re.search(
            r"\b(?:sigma|standard deviation|std\.?)\b|\d+(?:\.\d+)?\s*(?:%|percent\b|nm\b)",
            fragments[index + 1],
            re.IGNORECASE,
        ):
            clause = f"{clause}; {fragments[index + 1]}"
            index += 1
        assembled.append(clause)
        index += 1

    # A combined sentence may carry normal thickness uncertainty and a bounded
    # angle offset ("together with a common incidence-angle offset bounded by
    # plus or minus 2 degrees").  Evaluate each observable in its own local
    # semantic window so the angle's bounded phrase cannot turn the thickness
    # distribution into uniform.
    angle_phrase_re = re.compile(
        r"\b(?:a\s+)?common\s+incidence[- ]angle\b|\bincidence[- ]angle\b|"
        r"\bangle\s+(?:offset|perturbation|error|uncertainty|tolerance|deviation)\b",
        re.IGNORECASE,
    )
    thickness_windows: list[str] = []
    angle_windows: list[str] = []
    for fragment in fragments:
        angle_match = angle_phrase_re.search(fragment)
        if angle_match is not None:
            angle_windows.append(fragment[angle_match.start() :])
    for clause in assembled:
        angle_match = angle_phrase_re.search(clause)
        if angle_match is None:
            thickness_windows.append(clause)
            continue
        prefix = clause[: angle_match.start()].strip(" ,;:-")
        if prefix and re.search(
            r"\b(?:thickness|manufactur|fabricat)\w*\b",
            prefix,
            re.IGNORECASE,
        ):
            thickness_windows.append(prefix)

    for window in thickness_windows:
        is_sigma = bool(
            re.search(
                r"\b(?:one[- ]sigma|1[- ]sigma|sigma|standard deviation|std\.?|"
                r"normal(?:ly)?\s+distributed)(?:\b|\s)",
                window,
                re.IGNORECASE,
            )
        )
        is_bounded = bool(
            re.search(
                r"\+/-|±|plus\s+or\s+minus|bounded\s+by|uniform",
                window,
                re.IGNORECASE,
            )
        )
        value_match = _bound_uncertainty_value_match(window)
        if value_match is None:
            continue
        if re.search(r"(?:%|percent\b)", value_match.group(0), re.IGNORECASE):
            fraction = float(value_match.group(1)) / 100.0
            updates.update(
                {
                    "thickness_error_model": (
                        "relative_normal" if is_sigma and not is_bounded else "relative_uniform"
                    ),
                    "thickness_relative_fraction": fraction,
                    "thickness_sigma_nm": 0.0,
                }
            )
            reasons.append(
                "explicit relative thickness uncertainty was normalized from the user request"
            )
            break
        updates.update(
            {
                "thickness_error_model": (
                    "absolute_normal" if is_sigma and not is_bounded else "absolute_uniform"
                ),
                "thickness_sigma_nm": float(value_match.group(1)),
                "thickness_relative_fraction": 0.0,
            }
        )
        reasons.append(
            "explicit absolute thickness uncertainty was normalized from the user request"
        )
        break

    for window in dict.fromkeys(angle_windows):
        angle_value = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg\b|°)",
            window,
            re.IGNORECASE,
        )
        if angle_value:
            updates["angle_perturbation_deg"] = float(angle_value.group(1))
            reasons.append(
                "explicit common incidence-angle uncertainty was normalized "
                "from the user request"
            )
            break

    normalized = policy.model_copy(update=updates)
    return normalized, tuple(dict.fromkeys(item for item in reasons if item))


def _validation_messages(exc: Exception) -> tuple[str, ...]:
    if isinstance(exc, ValidationError):
        rows = []
        for item in exc.errors(include_url=False):
            location = ".".join(str(part) for part in item.get("loc", ()))
            rows.append(f"{location}: {item.get('msg', 'invalid value')}")
        return tuple(rows[:24])
    return (str(exc),)


def _has_paired_band_objectives(task: OpticalDesignTask) -> bool:
    objectives = [
        objective
        for experiment in task.experiments
        for objective in experiment.objectives
    ]
    explicit_contrast = any(
        objective.metric == "band_emissivity_contrast"
        for objective in objectives
    )
    directional_bands = {
        (
            objective.sense,
            tuple(float(value) for value in objective.region.get("wavelength_nm", ())),
        )
        for objective in objectives
        if objective.metric
        in {
            "mean_reflectance",
            "band_reflectance",
            "mean_transmittance",
            "mean_absorption",
            "mean_emissivity",
            "worst_case_reflectance",
            "worst_case_transmittance",
            "worst_case_absorption",
        }
        and objective.sense in {"maximize", "minimize"}
    }
    senses = {sense for sense, _ in directional_bands}
    bands = {band for _, band in directional_bands}
    return explicit_contrast or (senses >= {"maximize", "minimize"} and len(bands) >= 2)


def _preserves_multiple_bands(task: OpticalDesignTask) -> bool:
    """Whether the contract still carries every band the request asked for.

    Distinct from ``_has_paired_band_objectives``, which answers a narrower
    question: the ``band_preference`` capability axis genuinely requires
    opposing objectives, because that is what the axis measures.  Semantic
    coverage asks only whether both bands survived compilation, and two bands
    driven the SAME direction -- "transmit here and reflect there", both
    maximized -- preserve both bands exactly as faithfully as an opposing pair.
    """

    directional_bands = {
        tuple(float(value) for value in objective.region.get("wavelength_nm", ()))
        for experiment in task.experiments
        for objective in experiment.objectives
        if objective.sense in {"maximize", "minimize"}
        and objective.region.get("wavelength_nm")
    }
    return _has_paired_band_objectives(task) or len(directional_bands) >= 2


def _requested_materials(text: str) -> set[str]:
    """Collect chemical formulas the request names explicitly."""

    return {
        match.group(0).casefold()
        for match in _MATERIAL_FORMULA_RE.finditer(text)
        if any(character.islower() or character.isdigit() for character in match.group(0))
    }


def _semantic_coverage_errors(
    task: OpticalDesignTask,
    benchmark: BenchmarkTask | None,
    source_question: str,
) -> tuple[str, ...]:
    errors: list[str] = []
    if benchmark is not None and "band_preference" in set(benchmark.capability_axes):
        if not _has_paired_band_objectives(task):
            errors.append(
                "band_preference requires either band_emissivity_contrast with two "
                "declared bands or separate maximize/minimize mean objectives"
            )
    # Only the immutable user/route request defines semantic coverage.  A
    # model-authored normalized paraphrase must never introduce an extra band
    # or preference and then invalidate its own otherwise correct contract.
    semantic_text = source_question
    ranges = _wavelength_ranges(semantic_text)
    preference_text = _without_material_adjectives(semantic_text)
    explicit_opposing_preference = (
        len(ranges) >= 2
        and bool(_PREFERRED_TERMS.search(preference_text))
        and bool(_SUPPRESSED_TERMS.search(preference_text))
    )
    if explicit_opposing_preference and not _preserves_multiple_bands(task):
        errors.append(
            "the natural-language request contains opposing multi-band preferences, "
            "but the task contract does not preserve both bands"
        )
    requested_polarizations: set[str] = set()
    if re.search(r"\bTE\b", source_question, re.IGNORECASE):
        requested_polarizations.add("s")
    if re.search(r"\bTM\b", source_question, re.IGNORECASE):
        requested_polarizations.add("p")
    if requested_polarizations:
        for experiment in task.experiments:
            raw_task = experiment.tmm_task
            simulation = (
                raw_task.get("simulation", {})
                if experiment.mode.value == "optimize"
                else raw_task
            )
            actual = set(
                ((simulation.get("illumination") or {}).get("polarizations") or [])
            )
            for requested_polarization in sorted(requested_polarizations):
                if requested_polarization not in actual:
                    errors.append(
                        f"explicit {'TE' if requested_polarization == 's' else 'TM'} "
                        f"polarization requires '{requested_polarization}' in experiment "
                        f"{experiment.experiment_id}"
                    )
    requested_materials = _requested_materials(source_question)
    if requested_materials:
        for experiment in task.experiments:
            raw_task = experiment.tmm_task
            simulation = (
                raw_task.get("simulation", {})
                if experiment.mode.value == "optimize"
                else raw_task
            )
            layers = (simulation.get("stack") or {}).get("layers") or []
            for layer in layers:
                if not isinstance(layer, Mapping):
                    continue
                used = str(layer.get("material") or "").casefold()
                if not used or used in requested_materials:
                    continue
                # A layer material that is a proper prefix of a requested
                # formula is a truncation ('ti' for TiO2), not a substitution:
                # both resolve in the registry, so only this check catches it.
                truncated = sorted(
                    name
                    for name in requested_materials
                    if name != used and name.startswith(used)
                )
                if truncated:
                    errors.append(
                        f"experiment {experiment.experiment_id} layer material "
                        f"'{used}' truncates explicitly requested "
                        f"'{truncated[0]}'"
                    )
    pair_match = _PAIR_COUNT_EACH_SIDE_RE.search(source_question)
    if pair_match and re.search(r"\bcavit(?:y|ies)\b", source_question, re.IGNORECASE):
        pairs = int(pair_match.group(1))
        expected_layers = 4 * pairs + 1
        for experiment in task.experiments:
            raw_task = experiment.tmm_task
            simulation = (
                raw_task.get("simulation", {})
                if experiment.mode.value == "optimize"
                else raw_task
            )
            layer_count = len(
                ((simulation.get("stack") or {}).get("layers") or [])
            )
            if layer_count != expected_layers:
                errors.append(
                    f"{pairs} DBR pairs on each side of one explicit cavity require "
                    f"{expected_layers} expanded layers, but experiment "
                    f"{experiment.experiment_id} contains {layer_count}"
                )
    declared_matches = list(_DECLARED_LAYER_COUNT_RE.finditer(source_question))
    declared_counts = {int(match.group(1)) for match in declared_matches}
    if len(declared_counts) == 1:
        declared_count = next(iter(declared_counts))
        # The prose count may or may not include the semi-infinite media that
        # the same sentence enumerates; accept either reading rather than
        # rejecting the one the policy directive asks the model to produce.
        slack = max(
            (
                _bounding_media_named(
                    _enclosing_sentence(
                        source_question, match.start(), match.end()
                    )[0]
                )
                for match in declared_matches
            ),
            default=0,
        )
        admissible = {
            declared_count - offset
            for offset in range(slack + 1)
            if declared_count - offset >= 1
        }
        for experiment in task.experiments:
            raw_task = experiment.tmm_task
            simulation = (
                raw_task.get("simulation", {})
                if experiment.mode.value == "optimize"
                else raw_task
            )
            layer_count = len(
                ((simulation.get("stack") or {}).get("layers") or [])
            )
            if layer_count not in admissible:
                errors.append(
                    f"the route declares {declared_count} layers, but experiment "
                    f"{experiment.experiment_id} contains {layer_count} explicit layers"
                )
    fixed_pairs = _MIRROR_PAIR_FIXED_RE.search(source_question)
    range_pairs = _MIRROR_PAIR_RANGE_RE.search(source_question)
    if fixed_pairs or range_pairs:
        if range_pairs:
            lo, hi = sorted((int(range_pairs.group(1)), int(range_pairs.group(2))))
            mirror_pairs = (lo + hi) // 2
        else:
            assert fixed_pairs is not None
            mirror_pairs = int(fixed_pairs.group(1))
        if re.search(r"\bcavit(?:y|ies)\b", source_question, re.IGNORECASE):
            expected_layers = 4 * mirror_pairs + 1
            for experiment in task.experiments:
                raw_task = experiment.tmm_task
                simulation = (
                    raw_task.get("simulation", {})
                    if experiment.mode.value == "optimize"
                    else raw_task
                )
                layer_count = len(
                    ((simulation.get("stack") or {}).get("layers") or [])
                )
                if layer_count != expected_layers:
                    errors.append(
                        f"N_H=N_L={mirror_pairs} mirror pairs plus one cavity require "
                        f"{expected_layers} explicit layers, but experiment "
                        f"{experiment.experiment_id} contains {layer_count}"
                    )
    return tuple(errors)


def _experiment_payloads(task_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    experiments = task_payload.get("experiments")
    if not isinstance(experiments, (list, tuple)):
        return []
    return [item for item in experiments if isinstance(item, Mapping)]


def _stack_layers(experiment: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    tmm_task = experiment.get("tmm_task")
    simulation = tmm_task.get("simulation") if isinstance(tmm_task, Mapping) else None
    holder = simulation if isinstance(simulation, Mapping) else tmm_task
    stack = holder.get("stack") if isinstance(holder, Mapping) else None
    layers = stack.get("layers") if isinstance(stack, Mapping) else None
    if not isinstance(layers, (list, tuple)):
        return []
    return [layer for layer in layers if isinstance(layer, Mapping)]


def _spec_wavelength_ranges(experiment: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Collect explicit design-band ranges from targets and objective regions.

    Named distinctly from the module's prose scanner _wavelength_ranges(text)
    which predates the article branch.
    """

    ranges: list[tuple[float, float]] = []
    tmm_task = experiment.get("tmm_task")
    targets = tmm_task.get("targets") if isinstance(tmm_task, Mapping) else None
    for target in targets if isinstance(targets, (list, tuple)) else []:
        if not isinstance(target, Mapping):
            continue
        low, high = target.get("wavelength_min_nm"), target.get("wavelength_max_nm")
        if low is not None and high is not None:
            ranges.append((float(low), float(high)))
    for objective in experiment.get("objectives") or []:
        if not isinstance(objective, Mapping):
            continue
        region = objective.get("region")
        pair = _coerce_bound_pair(region.get("wavelength_nm")) if isinstance(region, Mapping) else None
        if pair is not None:
            ranges.append(pair)
    return ranges


def _check_task_spec_charter_drift(task_spec: Mapping[str, Any], charter: Any) -> None:
    """T-06 Charter immutable diff on a VeriTMM task_spec (fail-closed).

    v0.8 field names: material_whitelist and layer_count_bounds (the legacy
    layer_count_hard_bounds is accepted as a read-only input alias only).
    """

    if charter is None:
        raise CompileFailure("CHARTER_DRIFT_ERROR: no ResearchCharter provided")
    payload = task_spec.get("task") if isinstance(task_spec, Mapping) else None
    label = (
        str(task_spec.get("route_id") or "task")
        if isinstance(task_spec, Mapping)
        else "task"
    )
    materials: list[str] = []
    layer_counts: list[int] = []
    ranges: list[tuple[float, float]] = []
    for experiment in _experiment_payloads(
        payload if isinstance(payload, Mapping) else {}
    ):
        layers = _stack_layers(experiment)
        materials.extend(
            str(layer.get("material")).strip()
            for layer in layers
            if str(layer.get("material") or "").strip()
        )
        layer_counts.append(len(layers))
        ranges.extend(_spec_wavelength_ranges(experiment))

    raw_whitelist = _charter_value(charter, "material_whitelist") or []
    whitelist_items = (
        raw_whitelist if isinstance(raw_whitelist, (list, tuple)) else [raw_whitelist]
    )
    whitelist = {
        str(item).strip().casefold() for item in whitelist_items if str(item).strip()
    }
    if whitelist and materials:
        outside = sorted(
            {item.casefold() for item in materials} - whitelist
        )
        if outside:
            raise CompileFailure(
                f"CHARTER_DRIFT_ERROR: task proposes material(s) {outside} "
                "outside the charter material_whitelist"
            )

    bounds = _coerce_bound_pair(
        _charter_value(charter, "layer_count_bounds")
        or _charter_value(charter, "layer_count_hard_bounds")
    )
    if bounds is not None and layer_counts:
        low, high = bounds
        bad = [count for count in layer_counts if not low <= count <= high]
        if bad:
            raise CompileFailure(
                f"CHARTER_DRIFT_ERROR: task layer count(s) {bad} outside the "
                f"charter layer_count_bounds [{int(low)}, {int(high)}]"
            )

    charter_wavelength = _coerce_bound_pair(
        _charter_value(charter, "wavelength_range_nm")
    )
    if charter_wavelength is not None:
        low_limit, high_limit = charter_wavelength
        for lo, hi in ranges:
            if lo < low_limit - 1e-9 or hi > high_limit + 1e-9:
                raise CompileFailure(
                    f"CHARTER_DRIFT_ERROR: task wavelength range "
                    f"[{lo}, {hi}] nm outside the charter range "
                    f"{[low_limit, high_limit]} nm"
                )


def _canonical_task_sha256(task_spec: Mapping[str, Any]) -> str:
    """Canonical UTF-8 JSON sha256 of the physics task content only.

    Only 'mode' and 'task' are included in the fingerprint.  Metadata fields
    (round_k, route_id, output_dir, schema_version) are intentionally excluded
    so that the sha256 is stable across rounds for an unchanged physics task.
    This is required for T-08 detect_stagnation to work correctly: if round_k
    or output_dir were included, the hash would always differ between rounds
    even for an identical task, making stagnation detection permanently blind.

    Never hashlib.sha256(json.dumps(...)): ASCII escaping diverges from the
    engine's canonical_json_dumps convention for non-ASCII material names.
    """
    from .veritmm_adapter import _ensure_real_veritmm_import

    _ensure_real_veritmm_import()
    from tmm_engine.hashing import stable_sha256

    content = {
        "mode": task_spec.get("mode"),
        "task": task_spec.get("task"),
    }
    return stable_sha256(content)


def build_veritmm_task_spec(
    compiled: TaskCompilationResult,
    *,
    route_id: str,
    round_k: int,
    experiment_store: ExperimentStore,
    charter: Any | None = None,
    mode: Literal["simulate", "optimize"] = "optimize",
) -> dict[str, Any]:
    """Assemble the VeriTMM-compatible task_spec for one route (T-06).

    Output paths come exclusively from ExperimentStore; when a charter is
    supplied the immutable diff runs BEFORE the spec is finalized, and the
    canonical task_sha256 is attached last so failed specs never carry a
    fingerprint. charter=None skips the gate (T-16 must always supply one).
    """
    if compiled.status != "compiled" or compiled.task is None:
        raise CompileFailure(
            f"cannot build a task_spec from a compilation with status={compiled.status!r}"
        )
    if charter is not None:
        validate_research_charter(charter)
    output_dir = experiment_store.ensure_round_dir(round_k, route_id)
    task_spec: dict[str, Any] = {
        "schema_version": "veritmm-task-spec.v1",
        "route_id": str(route_id),
        "round_k": int(round_k),
        "mode": mode,
        "task": compiled.task.model_dump(mode="json"),
        "output_dir": str(output_dir),
    }
    if charter is not None:
        _check_task_spec_charter_drift(task_spec, charter)
    task_spec["task_sha256"] = _canonical_task_sha256(
        {key: value for key, value in task_spec.items() if key != "task_sha256"}
    )
    return task_spec


class QwenTMMTaskCompiler:
    """Compile a natural-language question with one repair attempt at most."""

    def __init__(
        self,
        *,
        client: TaskCompilerClient | None = None,
        prompt_path: str | Path = DEFAULT_TASK_COMPILER_PROMPT,
        maximum_attempts: int = 2,
        transport_retries: int = 2,
        scoring_standard: ScoringStandard | None = None,
    ) -> None:
        # T-06: compilation / structured tasks use the turbo tier.
        self.client = client or ArticleTurboQwenClient()
        self.prompt_path = Path(prompt_path)
        self.maximum_attempts = max(1, min(int(maximum_attempts), 2))
        # A connection blip is not a scientific or compilation verdict. Keep
        # this retry budget separate from the two semantic-draft attempts so a
        # transient failure cannot consume the route's only repair attempt.
        self.transport_retries = max(0, min(int(transport_retries), 3))
        declared_model = getattr(self.client, "model_name", None)
        declared_label = str(declared_model).strip() if declared_model is not None else ""
        if declared_label and declared_label not in _ALLOWED_COMPILER_MODELS:
            raise ValueError(
                f"task compiler model lock violation: client declared {declared_model!r}"
            )
        # Results echo the client's declared model; the article default is turbo.
        self._model_label = declared_label or ARTICLE_TASK_COMPILER_MODEL
        # The scoreboard is frozen once per compiler instance, and the factory
        # builds one instance per run, so every route and every revision of a
        # run is graded against the same objective.  Held here rather than
        # passed per call because the caller that drives the waves is not the
        # caller that knows what a comparable objective is.
        #
        # Two mechanisms can supply that comparability, and only one runs.  A
        # scoring standard, when the run built one, states the criteria up front
        # from the user's question and measures them as report-only objectives,
        # which leaves each route free to optimize its own targets.  Without
        # one, the fallback copies the first route that compiled onto every
        # later route -- comparable, but it takes the criteria from whichever
        # route happened to be first and forces them all down one path.
        self._scoring_standard = scoring_standard
        self._frozen_objective: tuple[dict[str, Any], ...] | None = None
        self._frozen_signature: str | None = None

    def adopt_scoring_standard(self, standard: ScoringStandard) -> None:
        """Attach the run's frozen standard once, before the first compile.

        A run learns the user's question after this object exists, so the
        standard cannot always arrive through the constructor.  Adoption is
        deliberately one-way: replacing a standard mid-run would rank the early
        routes by one rule and the later ones by another, which is precisely
        the comparison this mechanism exists to make possible.  Re-adopting the
        identical standard is allowed so a caller need not track whether it
        already did so.
        """

        if self._scoring_standard is not None and self._scoring_standard != standard:
            raise TMMTaskCompilationError(
                "the scoring standard is fixed for the whole run; it cannot be "
                "replaced after a route has been compiled against it"
            )
        self._scoring_standard = standard

    def compile(
        self,
        question: str,
        *,
        benchmark: BenchmarkTask | None = None,
        force_mock: bool | None = None,
    ) -> TaskCompilationResult:
        source_question = str(question or "").strip()
        if not source_question:
            raise TMMTaskCompilationError("a non-empty question is required")
        prompt = self.prompt_path.read_text(encoding="utf-8")
        benchmark_id = benchmark.id if benchmark is not None else None
        input_payload: dict[str, Any] = {
            "question": _bounded_compiler_question(source_question),
            "benchmark": None
            if benchmark is None
            else {
                "id": benchmark.id,
                "title": benchmark.title,
                "task_family": benchmark.task_family,
                "capability_axes": list(benchmark.capability_axes),
                "expected_artifacts": list(benchmark.expected_artifacts),
            },
            "fixed_rules": {
                "solver_family": "TMM only",
                "model": self._model_label,
                "performance_targets": "soft ranking scores only",
                "physics_admission": "deterministic validators only",
                "diversity": "optional bonus, never required",
            },
        }
        usages: list[dict[str, Any]] = []
        hashes: list[str] = []
        errors: tuple[str, ...] = ()
        previous = ""
        for attempt in range(1, self.maximum_attempts + 1):
            existing_draft = _safe_json(previous)
            if attempt == 1:
                user_payload = input_payload
            elif not existing_draft:
                # A response that did not parse leaves nothing to repair, and
                # asking the model to "repair only the listed defects in
                # existing_draft" against an empty object reliably reproduces
                # the same empty output.  Retry the original request instead,
                # and say what went wrong with the last one.
                user_payload = {
                    **input_payload,
                    "repair_request": {
                        "validation_errors": list(errors),
                        "instruction": (
                            "The previous response was not parseable as a single "
                            "JSON object, most often because it was cut off before "
                            "the object closed. Compile the task again from the "
                            "question above and return one complete TMMTaskDraft "
                            "JSON object only."
                        ),
                    },
                }
            else:
                user_payload = {
                    "question": input_payload["question"],
                    "benchmark": input_payload["benchmark"],
                    "fixed_rules": input_payload["fixed_rules"],
                    "existing_draft": existing_draft,
                    "repair_request": {
                        "validation_errors": list(errors),
                        "instruction": (
                            "Repair only the listed defects in existing_draft. Return the "
                            "corrected complete TMMTaskDraft JSON object only."
                        ),
                    },
                }
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ]
            response: Mapping[str, Any] | None = None
            transport_errors: list[str] = []
            for transport_attempt in range(self.transport_retries + 1):
                try:
                    response = self.client.call(
                        messages,
                        max_tokens=COMPILER_MAX_TOKENS,
                        force_mock=force_mock,
                    )
                    break
                except Exception as exc:
                    transport_errors.append(
                        f"{type(exc).__name__}: {exc}".replace("\n", " ")[:300]
                    )
                    if (
                        not _is_retryable_model_call_error(exc)
                        or transport_attempt >= self.transport_retries
                    ):
                        break
            if response is None:
                errors = (
                    "task compiler model call was unavailable after "
                    f"{len(transport_errors)} transport attempt(s): "
                    + "; ".join(transport_errors[-3:]),
                )
                if attempt < self.maximum_attempts:
                    continue
                return TaskCompilationResult(
                    status="invalid",
                    benchmark_id=benchmark_id,
                    attempts=attempt,
                    rationale=(
                        f"{self._model_label} could not reach the compiler provider"
                    ),
                    validation_errors=errors,
                    usage=tuple(usages),
                    raw_response_sha256=tuple(hashes),
                    rejected_draft=str(previous or ""),
                )
            previous = str(response.get("content") or "")
            hashes.append(hashlib.sha256(previous.encode("utf-8")).hexdigest())
            usage_row = dict(response.get("_llm_usage") or {})
            usages.append(usage_row)
            # T-06: meter every compiler Qwen call under the turbo role.
            total_tokens = usage_row.get("total_tokens")
            if total_tokens is None:
                total_tokens = (
                    int(usage_row.get("input_tokens") or 0)
                    + int(usage_row.get("output_tokens") or 0)
                ) or (
                    int(usage_row.get("prompt_tokens") or 0)
                    + int(usage_row.get("completion_tokens") or 0)
                )
            get_cost_tracker().record_qwen_usage("turbo", int(total_tokens or 0))
            raw_parsed = _safe_json(previous)
            parsed = _normalize_draft_envelope(raw_parsed, source_question)
            parsed, uncertainty_repairs = _repair_unrequested_uncertainty(
                parsed, source_question
            )
            # Runs before validation on purpose: an objective the model has
            # emptied or zeroed cannot survive the schema, so the freeze has to
            # reach it here or not at all.
            parsed, frozen_payload_restorations = _restore_frozen_objective_payload(
                parsed, self._frozen_objective or ()
            )
            requested_illumination = any(_requested_illumination(source_question))
            envelope_illumination_repaired = (
                requested_illumination and raw_parsed != parsed
            )
            try:
                draft = TMMTaskDraft.model_validate(parsed)
                if draft.status != "compiled":
                    if (
                        draft.status == "needs_clarification"
                        and attempt < self.maximum_attempts
                        and _clarification_is_policy_resolvable(draft.rationale)
                    ):
                        errors = (
                            "The clarification rationale conflicts with fixed compiler policy. "
                            "Apply the supplied registry, result-reporting, fixed-topology, "
                            "and uncertainty directives and return an executable draft.",
                        )
                        continue
                    return TaskCompilationResult(
                        status=draft.status,
                        benchmark_id=benchmark_id,
                        attempts=attempt,
                        rationale=draft.rationale,
                        usage=tuple(usages),
                        raw_response_sha256=tuple(hashes),
                    )
                if not draft.normalized_request_english or not draft.experiments:
                    raise ValueError("compiled drafts require an English request and experiments")
                draft, illumination_corrections = _normalize_explicit_illumination(
                    draft, source_question
                )
                draft, target_polarization_corrections = (
                    _normalize_target_polarization_coverage(draft, source_question)
                )
                draft, target_angle_corrections = _normalize_target_angle_coverage(
                    draft, source_question
                )
                illumination_corrections = (
                    *illumination_corrections,
                    *target_polarization_corrections,
                    *target_angle_corrections,
                )
                if envelope_illumination_repaired:
                    illumination_corrections = (
                        "pre-validation normalization synchronized explicit user "
                        "illumination and target channels",
                        *illumination_corrections,
                    )
                draft, substrate_corrections = _normalize_named_substrate(
                    draft, source_question
                )
                draft, solver_corrections = _normalize_diagnostic_solver(draft)
                draft, target_corrections = _normalize_explicit_target_thresholds(
                    draft, source_question
                )
                standard_alignments: tuple[str, ...] = ()
                if self._scoring_standard is not None:
                    # Ahead of the objective rebuild, which reads each target's
                    # constraint to decide what the route pursues: a target
                    # corrected afterwards would leave the objective still
                    # facing the wrong way.
                    draft, standard_alignments = _align_targets_with_scoring_standard(
                        draft, self._scoring_standard
                    )
                requested_angles, requested_polarizations = _requested_illumination(
                    source_question
                )
                # Gate the objective this compilation produced on its own, before
                # any freeze is applied.  Recorded either way: when it passes it
                # says the model needed no help, which is what tells us later
                # whether a prompt change did anything.
                objective_gate = _objective_sanity_problems(
                    _objective_targets(draft),
                    requested_angles,
                    requested_polarizations,
                )
                objective_freeze: list[str] = list(frozen_payload_restorations)
                if self._scoring_standard is not None:
                    # Copying one route's targets onto the others would defeat
                    # the standard: the routes would all chase the same thing,
                    # and the ranking they were built to compare would have
                    # nothing left to compare.
                    objective_freeze.append(
                        "optimizer targets left as this route declared them; the "
                        "run is ranked by the frozen scoring standard "
                        f"({self._scoring_standard.formula})"
                    )
                    if objective_gate:
                        objective_freeze.append(
                            "this route's own targets look unrankable, which does "
                            "not affect the frozen standard but is worth reading: "
                            + "; ".join(objective_gate)
                        )
                elif self._frozen_objective is None:
                    if objective_gate:
                        objective_freeze.append(
                            "objective not frozen, it would lock a scoreboard that "
                            "cannot rank designs: " + "; ".join(objective_gate)
                        )
                    else:
                        self._frozen_objective = tuple(_objective_targets(draft))
                        self._frozen_signature = _objective_signature(
                            self._frozen_objective
                        )
                        objective_freeze.append(
                            f"objective frozen for this run at signature "
                            f"{self._frozen_signature} "
                            f"({len(self._frozen_objective)} target(s))"
                        )
                else:
                    draft, frozen_changes = _apply_frozen_objective(
                        draft, self._frozen_objective
                    )
                    objective_freeze.extend(
                        frozen_changes
                        or (
                            f"objective already matches the frozen signature "
                            f"{self._frozen_signature}",
                        )
                    )
                draft, objective_corrections = _synchronize_objectives_from_targets(
                    draft
                )
                if self._scoring_standard is not None:
                    # After the rebuild, so the standard's own objectives cannot
                    # be discarded by it.
                    draft, standard_corrections = _apply_scoring_standard(
                        draft, self._scoring_standard
                    )
                    objective_freeze.extend(standard_corrections)
                # Last writer on the draft: every upstream pass that can widen
                # the objective or narrow the sweep has run, so this is the
                # only point where the target/illumination invariant can be
                # restored once for all of them.
                draft, illumination_reconciliations = (
                    _reconcile_illumination_with_targets(draft)
                )
                illumination_corrections = (
                    *illumination_corrections,
                    *illumination_reconciliations,
                )
                uncertainty, uncertainty_warnings = _normalize_uncertainty_budget(
                    draft.uncertainty, source_question
                )
                uncertainty_warnings = (*uncertainty_repairs, *uncertainty_warnings)
                rationale = draft.rationale
                if uncertainty_warnings:
                    # The validated contract is authoritative. A model may
                    # describe its original uncertainty proposal even after
                    # deterministic normalization corrected it; do not retain
                    # that stale explanation in the audit trail.
                    rationale = (
                        "The bounded planar TMM route was compiled with explicit "
                        "finite layers, shared spectral targets, and the "
                        "deterministically normalized user uncertainty contract."
                    )
                task = OpticalDesignTask(
                    task_id=_stable_task_id(benchmark_id, source_question),
                    benchmark_id=benchmark_id,
                    user_request_original=source_question,
                    normalized_request_english=draft.normalized_request_english,
                    experiments=draft.experiments,
                    verification=PhysicsVerificationPolicy(),
                    portfolio=PortfolioPolicy(),
                    uncertainty=uncertainty,
                    budget=HarnessBudgetPolicy(),
                    metadata={
                        "compiler": "QwenTMMTaskCompiler",
                        "compiler_schema": "tmm-task-compilation.v1",
                        "compiler_model": self._model_label,
                        "performance_targets_are_soft": True,
                        "diversity_required": False,
                        "target_threshold_corrections": list(target_corrections),
                        "scoring_standard_target_alignments": list(
                            standard_alignments
                        ),
                        "illumination_corrections": list(illumination_corrections),
                        "substrate_corrections": list(substrate_corrections),
                        "solver_corrections": list(solver_corrections),
                        "objective_synchronization": list(objective_corrections),
                        "objective_freeze": list(objective_freeze),
                        "objective_gate": list(objective_gate),
                        "objective_signature": _objective_signature(
                            _objective_targets(draft)
                        ),
                        "uncertainty_normalization": list(uncertainty_warnings),
                    },
                )
                semantic_errors = _semantic_coverage_errors(
                    task,
                    benchmark,
                    source_question,
                )
                if semantic_errors:
                    raise ValueError("; ".join(semantic_errors))
                return TaskCompilationResult(
                    status="compiled",
                    benchmark_id=benchmark_id,
                    attempts=attempt,
                    task=task,
                    rationale=rationale,
                    usage=tuple(usages),
                    raw_response_sha256=tuple(hashes),
                )
            # Some lower-level dataclass validators call ``float`` directly;
            # malformed model nulls therefore surface as TypeError rather than
            # Pydantic ValidationError.  Treat both as repairable draft defects.
            except (
                ValidationError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
            ) as exc:
                errors = _validation_messages(exc)
        return TaskCompilationResult(
            status="invalid",
            benchmark_id=benchmark_id,
            attempts=self.maximum_attempts,
            rationale=f"{self._model_label} did not produce a valid bounded TMM task",
            validation_errors=errors,
            usage=tuple(usages),
            raw_response_sha256=tuple(hashes),
            rejected_draft=str(previous or ""),
        )


__all__ = [
    "DEFAULT_TASK_COMPILER_PROMPT",
    "ArticleTurboQwenClient",
    "CompileFailure",
    "QwenTMMTaskCompiler",
    "TMMTaskCompilationError",
    "build_veritmm_task_spec",
    "TMMTaskDraft",
    "TaskCompilationResult",
    "TaskCompilerClient",
]
