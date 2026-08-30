"""Natural-language problem analysis for the TMM-only optical Harness.

The analyzer is deliberately a boundary component.  Qwen translates a natural
language request into a small English analysis object; deterministic checks
then preserve explicit numerical content, enforce the TMM scope, and reject
invented details.  It does not choose a solver, run a simulation, or invent a
design.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Literal, Mapping, Protocol, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    ValidationError,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from config.qwen_config import (
    ARTICLE_ROLE_MODEL_TIERS,
    get_cost_tracker,
    get_model_name,
    get_qwen_client,
    get_qwen_client_config,
)

from .qwen_policy import QWEN_POLICY_MODEL, QwenFlashOnlyClient

# ---------------------------------------------------------------------------
# Article branch additions (T-03)
# ---------------------------------------------------------------------------

# Planning-class analysis routes to the plus tier (qwen3.5-plus).
ARTICLE_PROBLEM_ANALYZER_MODEL = "qwen3.5-plus"

# Both tiers remain acceptable so legacy flash-declaring clients keep working;
# the article default is plus.
_ALLOWED_ANALYZER_MODELS = frozenset({QWEN_POLICY_MODEL, ARTICLE_PROBLEM_ANALYZER_MODEL})

# T-03 Charter immutability gate: every required field must be present.
REQUIRED_CHARTER_FIELDS: tuple[str, ...] = (
    "wavelength_range_nm",
    "angle_range_deg",
    "polarization",
    "objectives",
    "material_whitelist",
    "layer_count_bounds",
)


def validate_research_charter(charter: Any) -> None:
    """Validate required ResearchCharter fields (Charter immutability gate).

    Raises ValueError("CHARTER_FIELD_MISSING: <field>") on the first missing
    required field. Accepts mappings or attribute-bearing objects.
    """
    if isinstance(charter, Mapping):
        missing = [name for name in REQUIRED_CHARTER_FIELDS if name not in charter]
    else:
        missing = [name for name in REQUIRED_CHARTER_FIELDS if not hasattr(charter, name)]
    if missing:
        raise ValueError(f"CHARTER_FIELD_MISSING: {missing[0]}")


DEFAULT_PROMPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "prompts"
    / "optical_harness"
    / "TMM Research Problem Analyzer.txt"
)


class ResearchIntent(str, Enum):
    analyze = "analyze"
    design = "design"
    optimize = "optimize"
    reproduce = "reproduce"
    compare = "compare"
    robustness = "robustness"


class TMMCompatibility(str, Enum):
    compatible = "compatible"
    incompatible = "incompatible"
    ambiguous = "ambiguous"


class ProblemAnalyzerClient(Protocol):
    """The narrow interface shared by the locked optical Qwen client and fakes."""

    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> dict[str, Any]: ...


def _is_retryable_qwen_transport_error(exc: Exception) -> bool:
    """Retry only provider transport failures, not malformed model output."""

    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    return type(exc).__name__.casefold() in {
        "apiconnectionerror",
        "apitimeouterror",
        "connecterror",
        "connecttimeout",
        "readtimeout",
    }


class ArticlePlusQwenClient:
    """Article planning-tier client routed through qwen_config.get_qwen_client.

    role == "plus" (default) targets the c_model tier (qwen3.5-plus) per the
    T-00 role mapping. Implements the ProblemAnalyzerClient protocol so it
    drops into QwenTMMProblemAnalyzer unchanged.
    """

    def __init__(self, *, role: str = "plus") -> None:
        self.role = role
        self._client = get_qwen_client(role)

    @property
    def model_name(self) -> str:
        return get_model_name(ARTICLE_ROLE_MODEL_TIERS[self.role])

    def call(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> dict[str, Any]:
        cfg = get_qwen_client_config(ARTICLE_ROLE_MODEL_TIERS[self.role])
        model = self.model_name
        if force_mock or bool(cfg.get("mock_llm")):
            return {
                "content": "",
                "_llm_usage": {
                    "model_name": model,
                    "mock_llm": True,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                },
            }
        request_messages = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        response = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=model,
                    messages=request_messages,
                    max_tokens=max_tokens,
                )
                break
            except Exception as exc:
                last_error = exc
                if not _is_retryable_qwen_transport_error(exc) or attempt >= 2:
                    raise
        if response is None:
            # The loop either returned or raised; this is a defensive guard for
            # an unusual SDK that returns without assigning a response.
            raise RuntimeError("Qwen provider returned no response") from last_error
        usage_obj = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
        total_tokens = int(
            getattr(usage_obj, "total_tokens", 0) or 0
        ) or (prompt_tokens + completion_tokens)
        choice = getattr(response, "choices", None)
        message = getattr(choice[0], "message", None) if choice else None
        content = str(getattr(message, "content", "") or "") if message is not None else ""
        return {
            "content": content,
            "_llm_usage": {
                "model_name": model,
                "mock_llm": False,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
        }


_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
)
_RANGE_RE = re.compile(
    r"(?P<lo>[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*"
    r"(?:-|\u2013|\u2014|to)\s*"
    r"(?P<hi>[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*"
    r"(?P<unit>nm|nanometers?|um|\u00b5m|\u03bcm|micrometers?|microns?)\b",
    re.IGNORECASE,
)
_RANGE_AND_RE = re.compile(
    r"(?:between\s+)?(?P<lo>[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s+"
    r"(?:and|to)\s+"
    r"(?P<hi>[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*"
    r"(?P<unit>nm|nanometers?|um|\u00b5m|\u03bcm|micrometers?|microns?)\b",
    re.IGNORECASE,
)
_POINT_WAVELENGTH_RE = re.compile(
    r"(?<![\d.])(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*"
    r"(?P<unit>nm|nanometers?|um|\u00b5m|\u03bcm|micrometers?|microns?)\b",
    re.IGNORECASE,
)
_ANGLE_WITH_UNIT_RE = re.compile(
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*(?:deg(?:ree)?s?|\u00b0)",
    re.IGNORECASE,
)
_ANGLE_UNIT_RE = re.compile(r"(?:\bdegrees?\b|\bdeg\b|\u00b0)", re.IGNORECASE)
_POLARIZATION_RE = re.compile(
    r"\b(?:TE|TM|s|p|unpolarized|unpolarised)\b", re.IGNORECASE
)
_FORMULA_RE = re.compile(r"\b(?=[A-Za-z0-9]*\d)[A-Z][A-Za-z0-9]*\b")
_MATERIAL_WORDS = {
    "air",
    "glass",
    "silica",
    "silicon",
    "titania",
    "alumina",
    "aluminum",
    "aluminium",
    "silver",
    "gold",
    "sapphire",
    "polymer",
    "quartz",
}
# A material the user named in their own language is still a material the user
# named.  _MATERIAL_WORDS and _FORMULA_RE read English words and formulas only,
# so a Chinese request for 熔融石英 registered no token at all, the allowlist came
# back empty, and the model's faithful English "fused silica substrate" was
# scrubbed to "fused unspecified material substrate" -- the analyzer deleting the
# one substrate constraint the request actually stated.  Each entry maps the ways
# a request can name one material onto the tokens a faithful English rendering of
# it will contain, so the rendering survives the unrequested-material scrub.
#
# This grants nothing the request did not say: the tokens are released only when
# the source text matches, and only for the material it matched.
_MATERIAL_SOURCE_ALIASES: tuple[tuple[str, re.Pattern[str], frozenset[str]], ...] = (
    (
        "fused silica",
        re.compile(
            r"熔融石英|熔石英|石英玻璃|石英基?[片板底]|"
            r"\bfused[\s\-_]*(?:silica|quartz)\b|\bquartz\s+glass\b",
            re.IGNORECASE,
        ),
        frozenset({"silica", "quartz", "sio2", "glass"}),
    ),
    (
        "silicon",
        re.compile(r"硅片|单晶硅|\bsilicon\s+wafer\b", re.IGNORECASE),
        frozenset({"silicon", "si"}),
    ),
    (
        "sapphire",
        re.compile(r"蓝宝石|\bsapphire\b", re.IGNORECASE),
        frozenset({"sapphire", "al2o3", "alumina"}),
    ),
    (
        "BK7",
        re.compile(r"\bBK[\s\-_]*7\b", re.IGNORECASE),
        frozenset({"bk7", "glass"}),
    ),
)
_UNIT_FACTORS_NM = {
    "nm": 1.0,
    "nanometer": 1.0,
    "nanometers": 1.0,
    "um": 1000.0,
    "\u00b5m": 1000.0,
    "\u03bcm": 1000.0,
    "micrometer": 1000.0,
    "micrometers": 1000.0,
    "micron": 1000.0,
    "microns": 1000.0,
    "mm": 1_000_000.0,
}
_POLARIZATION_CANONICAL = {
    "te": "s",
    "s": "s",
    "tm": "p",
    "p": "p",
    "unpolarized": "unpolarized",
    "unpolarised": "unpolarized",
}

_WAVELENGTH_EVIDENCE_RE = re.compile(
    r"\b(?:wavelength|spectr(?:al|um)|band(?:s)?|window|spectrum)\b",
    re.IGNORECASE,
)
_EXCLUDED_WAVELENGTH_TERMS = (
    "thickness",
    "thick",
    "bounded",
    "bound",
    "bounds",
    "quantiz",
    "roughness",
    "tolerance",
    "uncertainty",
    "layer",
)

_GATE_TERMINOLOGY = (
    r"(?:hard\s+(?:(?:performance|feasibility)\s+)?(?:gates?|thresholds?)|"
    r"admission\s+(?:gates?|thresholds?)|performance\s+gates?|feasibility\s+gates?|"
    r"pass/fail|pass\s+fails?)"
)
_GATE_NEGATION = (
    r"(?:without|rather\s+than|do\s+not|does\s+not|not|no\b|none|neither|never)"
)
_REJECTS_HARD_GATE_RE = re.compile(
    rf"\b{_GATE_NEGATION}\b[^.;\n]{{0,65}}\b{_GATE_TERMINOLOGY}\b",
    re.IGNORECASE,
)
_ASSERTS_HARD_GATE_RE = re.compile(
    rf"\b(?:must\s+achieve|required\s+hard\s+gate|enforce\s+a\s+hard\s+threshold|"
    rf"impose\s+(?:a\s+)?(?:hard\s+)?(?:gates?|thresholds?)|"
    rf"hard\s+(?:(?:performance|feasibility)\s+)?gates?|{_GATE_TERMINOLOGY})\b",
    re.IGNORECASE,
)
_STRIP_GATE_LANGUAGE_RE = re.compile(
    rf"\b{_GATE_NEGATION}\b[^.;]{{0,65}}\b{_GATE_TERMINOLOGY}\b[^.;]*[.;]?",
    re.IGNORECASE,
)
_REPLACE_GATE_TERM_RE = re.compile(
    rf"\b(?:hard\s+(?:(?:performance|feasibility)\s+)?(?:gates?|thresholds?)|"
    rf"admission\s+(?:gates?|thresholds?)|performance\s+gates?|feasibility\s+gates?|"
    rf"pass/fail|pass\s+fails?|must\s+achieve)\b",
    re.IGNORECASE,
)

_INCOMPATIBLE_SCOPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "grating or diffraction",
        re.compile(r"\b(?:grating|diffraction|diffraction\s+order(?:s)?)\b", re.I),
    ),
    (
        "metasurface or lateral pattern",
        re.compile(
            r"\b(?:metasurface|meta-?surface|lateral\s+(?:pattern|structure|variation)|surface[- ]relief)\b",
            re.I,
        ),
    ),
    (
        "anisotropic or chiral media",
        re.compile(r"\b(?:anisotrop(?:ic|y)|chiral)\b", re.I),
    ),
    (
        "magnetic or magneto-optic media",
        re.compile(r"\b(?<!non)magnetic\b|\bmagneto[- ]optic", re.I),
    ),
    (
        "nonlinear optics",
        re.compile(r"\bnonlinear(?:ity)?\b|\bsecond[- ]harmonic\b|\bthird[- ]harmonic\b", re.I),
    ),
    (
        "near-field modeling",
        re.compile(r"\bnear[- ]field\b|\bnearfield\b", re.I),
    ),
    (
        "time-domain modeling",
        re.compile(r"\btime[- ]domain\b|\btransient\b|\bpulse propagation\b", re.I),
    ),
    (
        "RCWA, FDTD, or FEM",
        re.compile(r"\b(?:RCWA|FDTD|FEM|finite[- ]difference|finite[- ]element)\b", re.I),
    ),
)
_AMBIGUOUS_SCOPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "unspecified spatial pattern",
        re.compile(r"\b(?:patterned|patterning|nanostructure|lateral)\b", re.I),
    ),
    (
        "unspecified field or geometry model",
        re.compile(r"\b(?:waveguide|finite\s+aperture|roughness|nonlocal)\b", re.I),
    ),
)


def _contains_cjk(value: Any) -> bool:
    return bool(_CJK_RE.search(str(value or "")))


def _english_string(value: str, field_name: str) -> str:
    text = str(value or "").strip()
    if _contains_cjk(text):
        raise ValueError(f"{field_name} must contain English only")
    return text


class OpticalProblemAnalysis(BaseModel):
    """Stable, English intermediate representation of one optical request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    problem_id: StrictStr
    original_request: StrictStr
    normalized_request_english: StrictStr
    primary_intent: ResearchIntent
    secondary_intents: list[ResearchIntent] = Field(default_factory=list)
    compatibility: TMMCompatibility
    compatibility_reason: StrictStr
    wavelengths_nm: list[tuple[StrictFloat, StrictFloat]] = Field(default_factory=list)
    angles_deg: list[StrictFloat] = Field(default_factory=list)
    polarizations: list[StrictStr] = Field(default_factory=list)
    target_observables: list[StrictStr] = Field(default_factory=list)
    preferred_behaviors: list[StrictStr] = Field(default_factory=list)
    suppressed_behaviors: list[StrictStr] = Field(default_factory=list)
    known_stack_materials: list[StrictStr] = Field(default_factory=list)
    design_variables: list[StrictStr] = Field(default_factory=list)
    manufacturing_constraints: list[StrictStr] = Field(default_factory=list)
    assumptions: list[StrictStr] = Field(default_factory=list)
    ambiguities: list[StrictStr] = Field(default_factory=list)
    method_research_questions: list[StrictStr] = Field(default_factory=list)
    needs_method_research: StrictBool

    @model_validator(mode="before")
    @classmethod
    def _accept_stack_material_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "known_stack_materials" not in data:
            combined: list[Any] = []
            for key in ("known_stack", "materials", "known_stack/materials"):
                item = data.pop(key, None)
                if item is None:
                    continue
                if isinstance(item, (list, tuple)):
                    combined.extend(item)
                else:
                    combined.append(item)
            if combined:
                data["known_stack_materials"] = combined
        else:
            data.pop("known_stack", None)
            data.pop("materials", None)
            data.pop("known_stack/materials", None)
        return data

    @field_validator("problem_id", "original_request", "normalized_request_english", "compatibility_reason")
    @classmethod
    def _required_text(cls, value: str, info: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        if info.field_name != "original_request" and _contains_cjk(text):
            raise ValueError(f"{info.field_name} must contain English only")
        return text

    @field_validator(
        "polarizations",
        "target_observables",
        "preferred_behaviors",
        "suppressed_behaviors",
        "known_stack_materials",
        "design_variables",
        "manufacturing_constraints",
        "assumptions",
        "ambiguities",
        "method_research_questions",
    )
    @classmethod
    def _english_lists(cls, value: list[str], info: Any) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                raise ValueError(f"{info.field_name} cannot contain empty strings")
            if _contains_cjk(text):
                raise ValueError(f"{info.field_name} must contain English only")
            cleaned.append(text)
        return cleaned

    @field_validator("wavelengths_nm")
    @classmethod
    def _valid_wavelength_intervals(cls, value: list[tuple[float, float]]) -> list[tuple[float, float]]:
        checked: list[tuple[float, float]] = []
        for interval in value:
            if len(interval) != 2:
                raise ValueError("each wavelengths_nm item must be a two-value interval")
            start, stop = float(interval[0]), float(interval[1])
            if not math.isfinite(start) or not math.isfinite(stop):
                raise ValueError("wavelength intervals must be finite")
            if start < 0 or stop < start:
                raise ValueError("wavelength intervals must be ordered and non-negative")
            checked.append((start, stop))
        return checked

    @field_validator("angles_deg")
    @classmethod
    def _valid_angles(cls, value: list[float]) -> list[float]:
        checked = [float(item) for item in value]
        if not all(math.isfinite(item) for item in checked):
            raise ValueError("angles_deg must contain finite numbers")
        return checked

    @field_validator("secondary_intents")
    @classmethod
    def _unique_secondary_intents(cls, value: list[ResearchIntent]) -> list[ResearchIntent]:
        if len(value) != len(set(value)):
            raise ValueError("secondary_intents must be unique")
        return value

    @model_validator(mode="after")
    def _secondary_must_differ(self) -> "OpticalProblemAnalysis":
        if self.primary_intent in self.secondary_intents:
            raise ValueError("primary_intent must not be repeated in secondary_intents")
        return self

    @property
    def known_stack(self) -> list[str]:
        """Compatibility view for callers that name the field ``known_stack``."""

        return list(self.known_stack_materials)

    @property
    def materials(self) -> list[str]:
        """Compatibility view for callers that name the field ``materials``."""

        return list(self.known_stack_materials)


class ProblemAnalysisResult(BaseModel):
    """Analyzer outcome, including the locked model telemetry and warnings."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis: OpticalProblemAnalysis | None = None
    model_name: Literal["qwen3.5-plus", "qwen3.7-flash"] = ARTICLE_PROBLEM_ANALYZER_MODEL
    usage: list[dict[str, Any]] = Field(default_factory=list)
    validation_warnings: list[StrictStr] = Field(default_factory=list)
    status: Literal["analyzed", "invalid", "unavailable"]
    attempts: StrictInt = 0

    @field_validator("validation_warnings")
    @classmethod
    def _warnings_are_english(cls, value: list[str]) -> list[str]:
        for item in value:
            if _contains_cjk(item):
                raise ValueError("validation_warnings must contain English only")
        return value


class ProblemAnalysisError(RuntimeError):
    """Raised for an invalid analyzer configuration rather than a bad model draft."""


def stable_problem_id(original_request: str) -> str:
    """Return a deterministic identifier without exposing request text."""

    digest = hashlib.sha256(str(original_request).strip().encode("utf-8")).hexdigest()[:16]
    return f"tmm_problem_{digest}"


def _format_validation_errors(exc: Exception) -> tuple[str, ...]:
    if isinstance(exc, ValidationError):
        messages: list[str] = []
        for item in exc.errors(include_url=False):
            location = ".".join(str(part) for part in item.get("loc", ())) or "analysis"
            messages.append(f"{location}: {item.get('msg', 'invalid value')}")
        return tuple(messages[:32]) or ("invalid analysis schema",)
    return (str(exc),)


def _safe_json_object(content: Any) -> dict[str, Any] | None:
    if isinstance(content, Mapping):
        return dict(content)
    if not isinstance(content, str):
        return None
    try:
        value = json.loads(content.strip())
    except (TypeError, json.JSONDecodeError):
        return None
    return dict(value) if isinstance(value, Mapping) else None


def _canonical_item(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        pieces: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list, tuple)):
                rendered = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            else:
                rendered = str(item)
            pieces.append(f"{key}={rendered}")
        return "; ".join(pieces).strip()
    return str(value).strip()


def _normalise_interval(value: Any) -> Any:
    if isinstance(value, Mapping):
        for low_key, high_key in (
            ("start_nm", "end_nm"),
            ("min_nm", "max_nm"),
            ("lower_nm", "upper_nm"),
            ("start", "end"),
            ("min", "max"),
        ):
            if low_key in value and high_key in value:
                return [value[low_key], value[high_key]]
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [value[0], value[1]]
    return value


_LIST_FIELDS = (
    "polarizations",
    "target_observables",
    "preferred_behaviors",
    "suppressed_behaviors",
    "known_stack_materials",
    "design_variables",
    "manufacturing_constraints",
    "assumptions",
    "ambiguities",
    "method_research_questions",
)


def _strip_unrequested_normal_incidence(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = re.sub(
        r"\b(?:(?:at|under|for|assuming)\s+)?normal[- ]incidence\b",
        "",
        value,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned).strip(" ,;:-")
    return cleaned


def _strip_unrequested_numbers(value: Any, allowed_numbers: set[float]) -> Any:
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        number = float(match.group(0))
        if any(_same_number(number, allowed) for allowed in allowed_numbers):
            return match.group(0)
        return "unspecified"

    return _NUMBER_RE.sub(replace, value)


def _strip_unrequested_materials(value: Any, allowed_materials: set[str]) -> Any:
    """Replace material choices that were not supplied by the user.

    Problem analysis is a constraint-preservation boundary, not a material
    selector.  Qwen occasionally inserts plausible defaults such as air or
    glass even when the request deliberately leaves the material system open.
    Keeping those defaults would turn a model guess into a user constraint;
    rejecting the entire request wastes a repair call and can still fail.  A
    deterministic replacement preserves the sentence while returning the
    choice to the downstream method-research and strategy stages.
    """

    if not isinstance(value, str):
        return value
    generated = sorted(
        _explicit_material_tokens(value) - allowed_materials,
        key=len,
        reverse=True,
    )
    cleaned = value
    for material in generated:
        cleaned = re.sub(
            rf"\b{re.escape(material)}\b",
            "unspecified material",
            cleaned,
            flags=re.IGNORECASE,
        )
    cleaned = re.sub(
        r"\bunspecified material(?:\s*(?:,|/|and)\s*unspecified material)+\b",
        "unspecified material",
        cleaned,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _normalise_analysis_payload(
    payload: Mapping[str, Any],
    *,
    source_request: str,
    expected_problem_id: str,
) -> dict[str, Any]:
    data = dict(payload)
    if isinstance(data.get("analysis"), Mapping):
        data = dict(data["analysis"])

    source_rejects_hard_gate = bool(_REJECTS_HARD_GATE_RE.search(source_request.casefold()))
    if source_rejects_hard_gate:
        def remove_negated_gate_language(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            cleaned = _STRIP_GATE_LANGUAGE_RE.sub(
                "The numerical performance targets are soft scoring preferences. ",
                value,
            )
            cleaned = _REPLACE_GATE_TERM_RE.sub(
                "soft scoring preference",
                cleaned,
            )
            return re.sub(r"\s+", " ", cleaned).strip()

        for field_name in (
            "normalized_request_english",
            "compatibility_reason",
            "preferred_behaviors",
            "suppressed_behaviors",
            "design_variables",
            "manufacturing_constraints",
            "assumptions",
            "ambiguities",
            "method_research_questions",
        ):
            value = data.get(field_name)
            if isinstance(value, list):
                data[field_name] = [remove_negated_gate_language(item) for item in value]
            elif isinstance(value, str):
                data[field_name] = remove_negated_gate_language(value)

    if "known_stack_materials" not in data:
        combined: list[Any] = []
        for key in ("known_stack", "materials", "known_stack/materials"):
            item = data.pop(key, None)
            if item is None:
                continue
            if isinstance(item, (list, tuple)):
                combined.extend(item)
            else:
                combined.append(item)
        if combined:
            data["known_stack_materials"] = combined
    else:
        data.pop("known_stack", None)
        data.pop("materials", None)
        data.pop("known_stack/materials", None)

    if "wavelengths_nm" in data and isinstance(data["wavelengths_nm"], (list, tuple)):
        data["wavelengths_nm"] = [_normalise_interval(item) for item in data["wavelengths_nm"]]
    for field_name in _LIST_FIELDS:
        if field_name in data and data[field_name] is not None:
            item = data[field_name]
            if not isinstance(item, (list, tuple)):
                item = [item]
            data[field_name] = [_canonical_item(value) for value in item]

    if not _extract_angles(source_request):
        data["angles_deg"] = []
        for field_name in (
            "normalized_request_english",
            "preferred_behaviors",
            "suppressed_behaviors",
            "assumptions",
            "ambiguities",
            "method_research_questions",
        ):
            value = data.get(field_name)
            if isinstance(value, list):
                cleaned_values: list[Any] = []
                for item in value:
                    original = str(item or "")
                    if "normal incidence" in original.casefold():
                        if field_name == "assumptions":
                            continue
                        if field_name == "ambiguities":
                            cleaned_values.append("Angle of incidence is unspecified.")
                            continue
                    cleaned = _strip_unrequested_normal_incidence(item)
                    if cleaned:
                        cleaned_values.append(cleaned)
                data[field_name] = list(dict.fromkeys(cleaned_values))
            elif isinstance(value, str):
                data[field_name] = _strip_unrequested_normal_incidence(value)

    # The analyzer may identify only materials explicitly supplied by the
    # caller.  Material discovery and environmental boundary choices belong to
    # later research/strategy stages.  Remove model-invented material entries
    # before semantic validation rather than accepting them or spending a
    # second model call on a deterministic provenance issue.
    allowed_materials = _explicit_material_tokens(source_request)
    known_materials = data.get("known_stack_materials") or []
    if not isinstance(known_materials, (list, tuple)):
        known_materials = [known_materials]
    data["known_stack_materials"] = [
        item
        for item in known_materials
        if _explicit_material_tokens(str(item or "")) <= allowed_materials
    ]
    for field_name in (
        "normalized_request_english",
        "compatibility_reason",
        "preferred_behaviors",
        "suppressed_behaviors",
        "design_variables",
        "manufacturing_constraints",
        "assumptions",
        "ambiguities",
        "method_research_questions",
    ):
        value = data.get(field_name)
        if isinstance(value, list):
            data[field_name] = [
                _strip_unrequested_materials(item, allowed_materials) for item in value
            ]
        elif isinstance(value, str):
            data[field_name] = _strip_unrequested_materials(value, allowed_materials)

    allowed_numbers = _source_number_values(source_request)
    for field_name in (
        "normalized_request_english",
        "compatibility_reason",
        *_LIST_FIELDS,
    ):
        value = data.get(field_name)
        if isinstance(value, list):
            data[field_name] = [
                _strip_unrequested_numbers(item, allowed_numbers) for item in value
            ]
        elif isinstance(value, str):
            data[field_name] = _strip_unrequested_numbers(value, allowed_numbers)

    # Repeating the primary intent in secondary_intents is harmless model
    # redundancy, not a scientific ambiguity. Normalize it at the program
    # boundary instead of spending another LLM repair call.
    primary_intent = str(data.get("primary_intent") or "").strip().casefold()
    secondary = data.get("secondary_intents") or []
    if not isinstance(secondary, (list, tuple)):
        secondary = [secondary]
    cleaned_secondary: list[Any] = []
    seen_secondary: set[str] = set()
    for item in secondary:
        normalized = str(item or "").strip().casefold()
        if not normalized or normalized == primary_intent or normalized in seen_secondary:
            continue
        seen_secondary.add(normalized)
        cleaned_secondary.append(item)
    data["secondary_intents"] = cleaned_secondary

    # These are provenance fields owned by the deterministic boundary, not by
    # the model.  Replacing a model-supplied ID also prevents unstable IDs from
    # leaking into cache keys or downstream research artifacts.
    data["problem_id"] = expected_problem_id
    data["original_request"] = source_request
    return data


def _source_text_for_structured_input(value: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    data = dict(value)
    if isinstance(data.get("analysis"), Mapping):
        nested = dict(data["analysis"])
        if data.get("original_request") is not None:
            nested["original_request"] = data["original_request"]
        data = nested
    source = str(data.get("original_request") or data.get("source_request") or "").strip()
    if not source:
        source = str(data.get("normalized_request_english") or "").strip()
    if not source:
        source = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return source, data


def _looks_like_structured_input(value: Any) -> bool:
    if isinstance(value, BaseModel):
        return True
    if isinstance(value, Mapping):
        return True
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return False
        return isinstance(parsed, Mapping) and (
            "normalized_request_english" in parsed
            or "primary_intent" in parsed
            or isinstance(parsed.get("analysis"), Mapping)
        )
    return False


def _as_structured_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, Mapping):
            return parsed
    raise TypeError("structured analyzer input must be a mapping or Pydantic model")


def _unit_factor(unit: str) -> float:
    return _UNIT_FACTORS_NM.get(unit.casefold(), 1.0)


def _clause_around(text: str, start: int, end: int) -> str:
    """Return the sentence/clause containing a span without splitting decimals."""

    left = max(
        text.rfind(". ", 0, start),
        text.rfind(";", 0, start),
        text.rfind("\n", 0, start),
    )
    right = min(
        [
            position
            for delimiter in (". ", ";", "\n")
            if (position := text.find(delimiter, end)) >= 0
        ]
        or [len(text)]
    )
    return text[left + 1 : right]


def _is_wavelength_context(text: str, start: int, end: int) -> bool:
    # An excluded term immediately after the unit means the number quantifies
    # that noun ("2 nm quantization"), so it outranks clause-level evidence.
    trailing = text[end : end + 20].casefold().lstrip(" -")
    if trailing.startswith(_EXCLUDED_WAVELENGTH_TERMS):
        return False
    if _WAVELENGTH_EVIDENCE_RE.search(_clause_around(text, start, end)):
        return True
    before = text[max(0, start - 40) : start].casefold()
    return not any(term in before for term in _EXCLUDED_WAVELENGTH_TERMS)


def _extract_wavelength_intervals(text: str) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    range_spans: list[tuple[int, int]] = []
    for range_pattern in (_RANGE_RE, _RANGE_AND_RE):
        for match in range_pattern.finditer(text):
            if not _is_wavelength_context(text, match.start(), match.end()):
                continue
            factor = _unit_factor(match.group("unit"))
            low = float(match.group("lo")) * factor
            high = float(match.group("hi")) * factor
            intervals.append((low, high))
            range_spans.append((match.start(), match.end()))

    for match in _POINT_WAVELENGTH_RE.finditer(text):
        if any(start <= match.start() < end for start, end in range_spans):
            continue
        if not _is_wavelength_context(text, match.start(), match.end()):
            continue
        factor = _unit_factor(match.group("unit"))
        value = float(match.group("value")) * factor
        intervals.append((value, value))

    unique: list[tuple[float, float]] = []
    for item in intervals:
        if item not in unique:
            unique.append(item)
    return unique


def _extract_angles(text: str) -> list[float]:
    def is_uncertainty_angle(start: int, end: int) -> bool:
        left = max(
            text.rfind(";", 0, start),
            text.rfind("\n", 0, start),
            text.rfind(".", 0, start),
        )
        right_candidates = [
            position
            for delimiter in (";", "\n", ".")
            if (position := text.find(delimiter, end)) >= 0
        ]
        right = min(right_candidates) if right_candidates else len(text)
        context = text[left + 1 : right].casefold()
        return bool(
            re.search(
                r"\b(?:offset|error|uncertainty|tolerance|perturb(?:ation|ed)?|"
                r"variation|deviation|misalignment|plus\s+or\s+minus|bounded)\b|"
                r"[+±]\s*/?\s*-",
                context,
            )
        )

    values: list[float] = []
    if re.search(r"\bnormal[- ]incidence\b", text, re.IGNORECASE):
        values.append(0.0)
    for match in _ANGLE_WITH_UNIT_RE.finditer(text):
        if is_uncertainty_angle(match.start(), match.end()):
            continue
        values.append(float(match.group("value")))

    # A list such as "0, 30, and 60 degrees" carries the unit only on the
    # final value, and the unit may be followed by "incidence".  Collect the
    # comma/"and"-separated number list immediately preceding the unit.
    for match in _ANGLE_UNIT_RE.finditer(text):
        if is_uncertainty_angle(match.start(), match.end()):
            continue
        head = text[: match.start()]
        list_match = re.search(
            r"(?:^|[^.\d])(?P<list>"
            r"(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:,\s*|\s+and\s+)*)+"
            r"[-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*$",
            head,
        )
        if list_match:
            values.extend(
                float(item) for item in _NUMBER_RE.findall(list_match.group("list"))
            )

    # Preserve a list such as "at 0, 30, and 60 degrees", where only the
    # final value carries the unit.
    list_pattern = re.compile(
        r"\b(?:at|for|angle(?:s)?|incidence|evaluate|compute|simulate|measure|run|consider)\b([^.;\n]{0,100}?)\b(?:degrees?|deg)\b",
        re.IGNORECASE,
    )
    for match in list_pattern.finditer(text):
        fragment = match.group(1) or ""
        if is_uncertainty_angle(match.start(), match.end()):
            continue
        for number in _NUMBER_RE.findall(fragment):
            values.append(float(number))

    unique: list[float] = []
    for item in values:
        if item not in unique:
            unique.append(item)
    return unique


def _extract_polarizations(text: str) -> list[str]:
    values: list[str] = []
    for match in _POLARIZATION_RE.finditer(text):
        token = match.group(0).casefold()
        if token in {"s", "p"}:
            before = text[max(0, match.start() - 32) : match.start()].casefold()
            after = text[match.end() : min(len(text), match.end() + 32)].casefold()
            if "polar" not in before + after and not re.search(r"\b(?:te|tm)\b", before + after):
                continue
        canonical = _POLARIZATION_CANONICAL.get(token)
        if canonical and canonical not in values:
            values.append(canonical)
    return values


def _scope_findings(text: str) -> tuple[list[str], list[str]]:
    incompatible: list[str] = []
    ambiguous: list[str] = []
    for label, pattern in _INCOMPATIBLE_SCOPE_PATTERNS:
        if pattern.search(text):
            incompatible.append(label)
    for label, pattern in _AMBIGUOUS_SCOPE_PATTERNS:
        if pattern.search(text) and label not in ambiguous:
            ambiguous.append(label)
    return incompatible, ambiguous


def _infer_explicit_intents(text: str) -> list[ResearchIntent]:
    lower = text.casefold()
    matches: list[ResearchIntent] = []
    patterns: tuple[tuple[ResearchIntent, str], ...] = (
        (ResearchIntent.optimize, r"\b(?:optim(?:ize|ise|ization|isation)|tune|parameter sweep)\b"),
        (ResearchIntent.design, r"\b(?:design|synthesi[sz]e|create|develop|inverse design)\b"),
        (ResearchIntent.reproduce, r"\b(?:reproduce|replicate|reproduction|replication)\b"),
        (ResearchIntent.compare, r"\b(?:compare|comparison|versus|vs\.?|trade[- ]?off)\b"),
        (ResearchIntent.robustness, r"\b(?:robust|robustness|tolerance|uncertainty|fabrication variation)\b"),
        (ResearchIntent.analyze, r"\b(?:analy[sz]e|simulation|simulate|compute|characteri[sz]e|evaluate|known stack)\b"),
    )
    for intent, pattern in patterns:
        if re.search(pattern, lower):
            matches.append(intent)
    return matches


def _same_number(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-9)


def _same_interval(left: Sequence[float], right: Sequence[float]) -> bool:
    return len(left) == 2 and len(right) == 2 and _same_number(left[0], right[0]) and _same_number(left[1], right[1])


def _generated_payload_text(analysis: OpticalProblemAnalysis) -> str:
    data = analysis.model_dump(mode="json")
    data.pop("original_request", None)
    data.pop("problem_id", None)
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _source_number_values(text: str) -> set[float]:
    allowed: set[float] = set()
    for _, candidates in _source_number_requirements(text):
        allowed.update(candidates)
    if re.search(r"\bnormal[- ]incidence\b", text, re.IGNORECASE):
        allowed.add(0.0)
    return allowed


def _source_number_requirements(text: str) -> list[tuple[float, set[float]]]:
    requirements: list[tuple[float, set[float]]] = []
    unit_ranges: list[tuple[int, int, float]] = []
    for range_pattern in (_RANGE_RE, _RANGE_AND_RE):
        for match in range_pattern.finditer(text):
            unit_ranges.append(
                (match.start(), match.end(), _unit_factor(match.group("unit")))
            )
    for match in _NUMBER_RE.finditer(text):
        value = float(match.group(0))
        candidates = {value}
        for start, end, factor in unit_ranges:
            if start <= match.start() < end:
                candidates.add(value * factor)
        context = text[match.start() : min(len(text), match.end() + 20)].casefold()
        for unit, factor in _UNIT_FACTORS_NM.items():
            if re.search(rf"\b{re.escape(unit)}\b", context):
                candidates.add(value * factor)
        requirements.append((value, candidates))
    return requirements


def _generated_number_values(text: str) -> list[float]:
    return [float(item) for item in _NUMBER_RE.findall(text)]


def _requested_material_aliases(text: str) -> tuple[str, ...]:
    """Canonical names of the materials this request named, in any language."""

    body = str(text or "")
    return tuple(
        name for name, pattern, _ in _MATERIAL_SOURCE_ALIASES if pattern.search(body)
    )


def _explicit_material_tokens(text: str) -> set[str]:
    tokens = {item.casefold() for item in _FORMULA_RE.findall(text)}
    lower = text.casefold()
    tokens.update(word for word in _MATERIAL_WORDS if re.search(rf"\b{re.escape(word)}\b", lower))
    for _, pattern, implied in _MATERIAL_SOURCE_ALIASES:
        if pattern.search(text):
            tokens.update(implied)
    return tokens


def _unresolved_material_warnings(source_request: str) -> list[str]:
    """Name the materials the request stated that the registry cannot pin.

    The request is preserved either way -- a name the local library does not
    carry is still what the user asked for, and substituting a material that
    happens to resolve would silently answer a different question.  What changes
    is that the run says so, here, instead of a route failing later with a
    registry error nobody can trace back to the request.

    Best effort by construction: the registry import touches the engine, and an
    analysis must not fail because a catalogue could not be built.
    """

    requested = _requested_material_aliases(source_request)
    if not requested:
        return []
    try:
        from .material_catalog import RouteMaterialCatalog

        catalog = RouteMaterialCatalog()
    except Exception:
        return []
    warnings: list[str] = []
    for name in requested:
        try:
            verdict = catalog.verify(name)
        except Exception:
            continue
        if not verdict.ok:
            warnings.append(
                f"the requested material {name!r} was preserved from the request "
                "but the local registry could not resolve it to exactly one "
                "dataset; no substitute was chosen"
            )
    return warnings


def _semantic_validation_errors(
    analysis: OpticalProblemAnalysis,
    source_request: str,
) -> tuple[str, ...]:
    errors: list[str] = []
    expected_wavelengths = _extract_wavelength_intervals(source_request)
    actual_wavelengths = analysis.wavelengths_nm
    for interval in expected_wavelengths:
        if not any(_same_interval(interval, candidate) for candidate in actual_wavelengths):
            errors.append(
                f"explicit wavelength interval {interval[0]:g}-{interval[1]:g} nm was not preserved"
            )
    for interval in actual_wavelengths:
        if not any(_same_interval(interval, expected) for expected in expected_wavelengths):
            errors.append(
                f"wavelength interval {interval[0]:g}-{interval[1]:g} nm was not explicit in the request"
            )

    expected_angles = _extract_angles(source_request)
    actual_angles = analysis.angles_deg
    for angle in expected_angles:
        if not any(_same_number(angle, candidate) for candidate in actual_angles):
            errors.append(f"explicit angle {angle:g} degrees was not preserved")
    for angle in actual_angles:
        if not any(_same_number(angle, expected) for expected in expected_angles):
            errors.append(f"angle {angle:g} degrees was not explicit in the request")

    expected_polarizations = set(_extract_polarizations(source_request))
    actual_polarizations = {
        _POLARIZATION_CANONICAL.get(item.casefold(), item.casefold())
        for item in analysis.polarizations
    }
    if expected_polarizations - actual_polarizations:
        errors.append("explicit polarizations were not all preserved")
    if actual_polarizations - expected_polarizations:
        errors.append("a polarization was supplied without an explicit request")

    allowed_numbers = _source_number_values(source_request)
    generated_numbers = _generated_number_values(_generated_payload_text(analysis))
    for source_value, candidates in _source_number_requirements(source_request):
        if not any(
            _same_number(generated, candidate)
            for generated in generated_numbers
            for candidate in candidates
        ):
            errors.append(f"explicit numeric value {source_value:g} was not preserved")
            break
    for value in generated_numbers:
        if not any(_same_number(value, candidate) for candidate in allowed_numbers):
            errors.append(f"generated numeric value {value:g} was not explicit in the request")
            break

    source_materials = _explicit_material_tokens(source_request)
    generated_materials = _explicit_material_tokens(_generated_payload_text(analysis))
    invented_materials = sorted(generated_materials - source_materials)
    # Generic words such as "substrate" and "layer" are not material names;
    # formulas and named material words are.  This check covers assumptions and
    # normalized text as well as the dedicated stack field.
    if invented_materials:
        errors.append(
            "generated material identifiers were not explicit in the request: "
            + ", ".join(invented_materials[:8])
        )

    lower_source = source_request.casefold()
    generated_text = _generated_payload_text(analysis).casefold()
    if "normal incidence" in generated_text and not re.search(
        r"\b(?:normal[- ]incidence|0\s*(?:deg(?:ree)?s?|\u00b0))\b", lower_source
    ):
        errors.append("normal incidence was assumed without being requested")
    source_rejects_hard_gate = bool(_REJECTS_HARD_GATE_RE.search(lower_source))
    generated_asserts_hard_gate = bool(_ASSERTS_HARD_GATE_RE.search(generated_text))
    source_asserts_hard_gate = bool(
        _ASSERTS_HARD_GATE_RE.search(lower_source)
    ) and not source_rejects_hard_gate
    if generated_asserts_hard_gate and not source_asserts_hard_gate:
        errors.append("a hard performance gate was invented")
    return tuple(dict.fromkeys(errors))


def _append_unique(items: Iterable[str], additions: Iterable[str]) -> list[str]:
    result = list(items)
    for item in additions:
        if item and item not in result:
            result.append(item)
    return result


def _apply_deterministic_guards(
    analysis: OpticalProblemAnalysis,
    source_request: str,
) -> tuple[OpticalProblemAnalysis, list[str]]:
    """Apply only constraints that are explicit in the source or fixed by TMM."""

    updates: dict[str, Any] = {}
    warnings: list[str] = []
    scope_text = f"{source_request}\n{analysis.normalized_request_english}"
    incompatible, ambiguous_scope = _scope_findings(scope_text)
    if incompatible:
        updates["compatibility"] = TMMCompatibility.incompatible
        updates["compatibility_reason"] = (
            "The request requires "
            + ", ".join(incompatible)
            + "; the Harness supports only laterally uniform planar, linear isotropic, "
            "frequency-domain plane-wave layered-media TMM."
        )
        updates["needs_method_research"] = True
        updates["method_research_questions"] = _append_unique(
            analysis.method_research_questions,
            ["What higher-fidelity method is required for the unsupported spatial, material, or temporal behavior?"],
        )
        warnings.append("TMM scope rejection was enforced deterministically")
    elif ambiguous_scope and analysis.compatibility == TMMCompatibility.compatible:
        updates["compatibility"] = TMMCompatibility.ambiguous
        updates["compatibility_reason"] = (
            "The request contains "
            + ", ".join(ambiguous_scope)
            + "; clarify whether the system remains a laterally uniform isotropic layered medium."
        )
        updates["needs_method_research"] = True
        warnings.append("TMM scope ambiguity was flagged deterministically")

    explicit_intents = _infer_explicit_intents(source_request)
    if explicit_intents:
        # Optimization is a stronger operation than generic design, while a
        # literal design request must never be reduced to forward analysis.
        primary = explicit_intents[0]
        if analysis.primary_intent != primary:
            warnings.append(
                f"explicit {primary.value} intent took precedence over the model intent"
            )
            updates["primary_intent"] = primary
        secondary = list(analysis.secondary_intents)
        for intent in explicit_intents[1:]:
            if intent != primary and intent not in secondary:
                secondary.append(intent)
        if secondary != list(analysis.secondary_intents):
            updates["secondary_intents"] = secondary

        normalized_lower = analysis.normalized_request_english.casefold()
        intent_words = {
            ResearchIntent.analyze: "analyze",
            ResearchIntent.design: "design",
            ResearchIntent.optimize: "optimize",
            ResearchIntent.reproduce: "reproduce",
            ResearchIntent.compare: "compare",
            ResearchIntent.robustness: "robustness",
        }
        if intent_words[primary] not in normalized_lower:
            updates["normalized_request_english"] = (
                f"{primary.value.capitalize()} request: {analysis.normalized_request_english}"
            )

    effective_primary = updates.get("primary_intent", analysis.primary_intent)
    effective_secondary: list[ResearchIntent] = []
    for intent in updates.get("secondary_intents", analysis.secondary_intents):
        if intent != effective_primary and intent not in effective_secondary:
            effective_secondary.append(intent)
    if effective_secondary != list(analysis.secondary_intents):
        updates["secondary_intents"] = effective_secondary
        warnings.append(
            "secondary intents were reconciled after deterministic primary-intent selection"
        )
    known_materials = list(analysis.known_stack_materials)
    ambiguities = list(analysis.ambiguities)
    research_questions = list(analysis.method_research_questions)
    needs_research = bool(updates.get("needs_method_research", analysis.needs_method_research))
    if effective_primary in {ResearchIntent.design, ResearchIntent.optimize}:
        if not known_materials:
            ambiguities = _append_unique(
                ambiguities,
                ["Material identities and layer topology are unspecified."],
            )
            research_questions = _append_unique(
                research_questions,
                ["Which material system and layer topology should be evaluated?"],
            )
            needs_research = True
        if not analysis.design_variables:
            ambiguities = _append_unique(
                ambiguities,
                ["Design variables, thickness values, and numerical bounds are unspecified."],
            )
            research_questions = _append_unique(
                research_questions,
                ["Which layer parameters may vary, and what explicit bounds should be used?"],
            )
            needs_research = True
        if not analysis.manufacturing_constraints:
            ambiguities = _append_unique(
                ambiguities,
                ["Manufacturing limits and quantization are unspecified."],
            )
            research_questions = _append_unique(
                research_questions,
                ["What manufacturing limits, tolerances, or quantization rules apply?"],
            )
            needs_research = True
        if not analysis.target_observables and not analysis.preferred_behaviors and not analysis.suppressed_behaviors:
            ambiguities = _append_unique(
                ambiguities,
                ["The design objective is unspecified."],
            )
            research_questions = _append_unique(
                research_questions,
                ["Which observable and preference should define the design objective?"],
            )
            needs_research = True

    updates["ambiguities"] = ambiguities
    updates["method_research_questions"] = research_questions
    updates["needs_method_research"] = needs_research
    if updates:
        analysis = analysis.model_copy(update=updates)
    return analysis, warnings


class QwenTMMProblemAnalyzer:
    """Analyze one request with one locked Qwen call plus one bounded repair."""

    def __init__(
        self,
        *,
        client: ProblemAnalyzerClient | None = None,
        prompt_path: str | Path = DEFAULT_PROMPT_PATH,
        maximum_attempts: int = 2,
    ) -> None:
        # T-03: article planning analysis defaults to the plus-tier client.
        self.client = client or ArticlePlusQwenClient(role="plus")
        self.prompt_path = Path(prompt_path)
        self.maximum_attempts = max(1, min(int(maximum_attempts), 2))
        declared_model = getattr(self.client, "model_name", None)
        declared_label = str(declared_model).strip() if declared_model is not None else ""
        if declared_label and declared_label not in _ALLOWED_ANALYZER_MODELS:
            raise ProblemAnalyzerError(
                f"Optical Harness model lock violation: client declared {declared_model!r}"
            )
        # Results echo the client's declared model; the article default is plus.
        self._model_label = declared_label or ARTICLE_PROBLEM_ANALYZER_MODEL

    def _result(
        self,
        *,
        status: Literal["analyzed", "invalid", "unavailable"],
        analysis: OpticalProblemAnalysis | None,
        usages: list[dict[str, Any]],
        warnings: list[str],
        attempts: int,
    ) -> ProblemAnalysisResult:
        return ProblemAnalysisResult(
            status=status,
            analysis=analysis,
            model_name=self._model_label,
            usage=usages,
            validation_warnings=warnings,
            attempts=attempts,
        )

    def _analyze_structured(self, request: Any) -> ProblemAnalysisResult:
        try:
            mapping = _as_structured_mapping(request)
            source_request, data = _source_text_for_structured_input(mapping)
            expected_id = stable_problem_id(source_request)
            candidate = OpticalProblemAnalysis.model_validate(
                _normalise_analysis_payload(
                    data,
                    source_request=source_request,
                    expected_problem_id=expected_id,
                )
            )
            semantic_errors = _semantic_validation_errors(candidate, source_request)
            if semantic_errors:
                return self._result(
                    status="invalid",
                    analysis=None,
                    usages=[],
                    warnings=list(semantic_errors),
                    attempts=0,
                )
            candidate, warnings = _apply_deterministic_guards(candidate, source_request)
            return self._result(
                status="analyzed",
                analysis=candidate,
                usages=[],
                warnings=warnings,
                attempts=0,
            )
        except (ValidationError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return self._result(
                status="invalid",
                analysis=None,
                usages=[],
                warnings=list(_format_validation_errors(exc)),
                attempts=0,
            )

    def analyze(
        self,
        request: str | Mapping[str, Any] | BaseModel,
        *,
        charter: Any | None = None,
        force_mock: bool | None = None,
    ) -> ProblemAnalysisResult:
        # T-03 Charter immutability gate runs before any other work.
        if charter is not None:
            validate_research_charter(charter)
        if _looks_like_structured_input(request):
            return self._analyze_structured(request)

        source_request = str(request or "").strip()
        if not source_request:
            raise ValueError("a non-empty optical problem request is required")
        try:
            system_prompt = self.prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ProblemAnalyzerError(f"problem analyzer prompt is unavailable: {exc}") from exc

        expected_id = stable_problem_id(source_request)
        base_payload: dict[str, Any] = {
            "original_request": source_request,
            "expected_problem_id": expected_id,
            "fixed_rules": {
                "solver_scope": "planar laterally uniform linear isotropic layered media under frequency-domain plane-wave illumination",
                "unsupported": [
                    "gratings",
                    "metasurface lateral patterns",
                    "anisotropic media",
                    "chiral media",
                    "magnetic or magneto-optic media",
                    "nonlinear optics",
                    "near-field tasks",
                    "time-domain tasks",
                ],
                "model": self._model_label,
                "performance_targets": "preferences and research questions only; never invented hard gates",
            },
        }
        usages: list[dict[str, Any]] = []
        validation_warnings: list[str] = []
        previous_response = ""
        validation_errors: tuple[str, ...] = ()

        for attempt in range(1, self.maximum_attempts + 1):
            user_payload = dict(base_payload)
            if attempt > 1:
                user_payload["repair_request"] = {
                    "validation_errors": list(validation_errors),
                    "previous_response": previous_response,
                    "instruction": "Return one corrected complete English JSON object only. Preserve every explicit value and do not add assumptions.",
                }
            try:
                response = self.client.call(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                    ],
                    max_tokens=4000,
                    force_mock=force_mock,
                )
            except Exception as exc:
                return self._result(
                    status="unavailable",
                    analysis=None,
                    usages=usages,
                    warnings=[f"locked Qwen client call failed: {type(exc).__name__}"],
                    attempts=attempt,
                )

            usage = dict(response.get("_llm_usage") or {}) if isinstance(response, Mapping) else {}
            usages.append(usage)
            # T-03: meter every analyzer Qwen call on the run-level CostTracker.
            total_tokens = usage.get("total_tokens")
            if total_tokens is None:
                for first_key, second_key in (
                    ("input_tokens", "output_tokens"),
                    ("prompt_tokens", "completion_tokens"),
                ):
                    first_value = usage.get(first_key)
                    second_value = usage.get(second_key)
                    if first_value is not None or second_value is not None:
                        total_tokens = int(first_value or 0) + int(second_value or 0)
                        break
            get_cost_tracker().record_qwen_usage("plus", int(total_tokens or 0))
            declared_model = str(usage.get("model_name") or "").strip()
            if declared_model and declared_model not in _ALLOWED_ANALYZER_MODELS:
                return self._result(
                    status="invalid",
                    analysis=None,
                    usages=usages,
                    warnings=[f"locked Qwen client returned disallowed model {declared_model!r}"],
                    attempts=attempt,
                )
            if usage.get("model_fallback_used") or usage.get("fallback_chain"):
                return self._result(
                    status="invalid",
                    analysis=None,
                    usages=usages,
                    warnings=["locked Qwen client reported model fallback usage"],
                    attempts=attempt,
                )
            previous_response = str(response.get("content") or "") if isinstance(response, Mapping) else ""
            parsed = _safe_json_object(response.get("content") if isinstance(response, Mapping) else None)
            if parsed is None:
                validation_errors = ("response content is not one JSON object",)
                continue
            try:
                raw_analysis = (
                    parsed.get("analysis")
                    if isinstance(parsed.get("analysis"), Mapping)
                    else parsed
                )
                raw_primary = str(
                    raw_analysis.get("primary_intent") or ""
                ).strip().casefold()
                raw_secondary = raw_analysis.get("secondary_intents") or []
                if not isinstance(raw_secondary, (list, tuple)):
                    raw_secondary = [raw_secondary]
                secondary_keys = [
                    str(item or "").strip().casefold() for item in raw_secondary
                ]
                normalized_intent_warning = (
                    bool(raw_primary and raw_primary in secondary_keys)
                    or len([item for item in secondary_keys if item])
                    != len(set(item for item in secondary_keys if item))
                )
                raw_generated_json = json.dumps(
                    raw_analysis, ensure_ascii=False, sort_keys=True
                )
                raw_generated_text = raw_generated_json.casefold()
                normalized_default_warning = (
                    "normal incidence" in raw_generated_text
                    and not re.search(
                        r"\b(?:normal[- ]incidence|0\s*(?:deg(?:ree)?s?|\u00b0))\b",
                        source_request.casefold(),
                    )
                )
                allowed_source_numbers = _source_number_values(source_request)
                normalized_number_warning = any(
                    not any(
                        _same_number(value, allowed)
                        for allowed in allowed_source_numbers
                    )
                    for value in _generated_number_values(raw_generated_text)
                )
                normalized_material_warning = bool(
                    _explicit_material_tokens(raw_generated_json)
                    - _explicit_material_tokens(source_request)
                )
                normalized_payload = _normalise_analysis_payload(
                    parsed,
                    source_request=source_request,
                    expected_problem_id=expected_id,
                )
                candidate = OpticalProblemAnalysis.model_validate(normalized_payload)
                validation_errors = _semantic_validation_errors(candidate, source_request)
                if validation_errors:
                    raise ValueError("; ".join(validation_errors))
                candidate, guard_warnings = _apply_deterministic_guards(candidate, source_request)
                if normalized_intent_warning:
                    guard_warnings = [
                        "redundant secondary intents were normalized deterministically",
                        *guard_warnings,
                    ]
                if normalized_default_warning:
                    guard_warnings = [
                        "an unrequested normal-incidence default was removed deterministically",
                        *guard_warnings,
                    ]
                if normalized_number_warning:
                    guard_warnings = [
                        "unrequested numeric assumptions were replaced with unspecified",
                        *guard_warnings,
                    ]
                if normalized_material_warning:
                    guard_warnings = [
                        "unrequested material assumptions were replaced with unspecified",
                        *guard_warnings,
                    ]
                # Reported after the scrub, not instead of it: the two warnings
                # answer different questions -- what the analyzer removed because
                # the request never asked for it, and what the request did ask for
                # that the engine may not be able to supply.
                guard_warnings = [
                    *guard_warnings,
                    *_unresolved_material_warnings(source_request),
                ]
                if attempt > 1:
                    guard_warnings = ["one bounded model-output repair was used", *guard_warnings]
                return self._result(
                    status="analyzed",
                    analysis=candidate,
                    usages=usages,
                    warnings=guard_warnings,
                    attempts=attempt,
                )
            except (ValidationError, ValueError, TypeError) as exc:
                validation_errors = _format_validation_errors(exc)

        if not validation_errors:
            validation_errors = (f"{self._model_label} did not produce a valid analysis",)
        validation_warnings.extend(validation_errors)
        return self._result(
            status="invalid",
            analysis=None,
            usages=usages,
            warnings=validation_warnings,
            attempts=self.maximum_attempts,
        )

    __call__ = analyze


TMMProblemAnalyzer = QwenTMMProblemAnalyzer


def analyze_optical_problem(
    request: str | Mapping[str, Any] | BaseModel,
    *,
    client: ProblemAnalyzerClient | None = None,
    charter: Any | None = None,
    force_mock: bool | None = None,
) -> ProblemAnalysisResult:
    """Convenience entry point that retains the injectable client boundary."""

    return QwenTMMProblemAnalyzer(client=client).analyze(
        request, charter=charter, force_mock=force_mock
    )


__all__ = [
    "DEFAULT_PROMPT_PATH",
    "ArticlePlusQwenClient",
    "OpticalProblemAnalysis",
    "ProblemAnalysisError",
    "ProblemAnalysisResult",
    "ProblemAnalyzerClient",
    "QwenTMMProblemAnalyzer",
    "REQUIRED_CHARTER_FIELDS",
    "ResearchIntent",
    "TMMCompatibility",
    "TMMProblemAnalyzer",
    "analyze_optical_problem",
    "stable_problem_id",
    "validate_research_charter",
]
