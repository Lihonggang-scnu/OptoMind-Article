"""Machine-readable catalogue of the executable metric vocabulary.

Two consumers need this file.  A language model has to be told which quantities
it may select as a scoring standard, and a local checker has to confirm the
selection before anything is executed.  Both were previously served by prose:
the metric names were written out by hand in six separate places, the shape of
the wavelength interval each metric needs was written in a prompt, and nothing
compared the two.  A name that existed in the code but not in the prompt was
therefore unreachable, and a name in the prompt that the code had renamed would
only fail after a run had been spent.

The catalogue is the single table.  It derives the legal name set from the task
contract that actually validates it, borrows the band-reduction rules from the
engine's own capability declaration, and adds the one thing neither of them
records: for each metric, which quantity it reduces, how it reduces it, which
interval fields it requires, and whether its number can carry a score at all.

Nothing here ranks or accepts a design.  A verified reference only means the
quantity is computable and correctly named.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field
from tmm_engine.protocol import describe_capabilities

from .design_task import SUPPORTED_OBJECTIVE_METRICS


CATALOG_SCHEMA_VERSION = "tmm-metric-catalog.v1"

# Wavelength units a caller may declare.  The engine contract reads nanometres,
# but infrared requirements are almost always stated in micrometres, so an
# unlabelled "5-13" is the single most likely way for a legal-looking request to
# mean something 1000x off.  Accept the unit explicitly and normalize once.
_WAVELENGTH_UNIT_TO_NM: Dict[str, float] = {
    "nm": 1.0,
    "um": 1000.0,
    "µm": 1000.0,  # micro sign
    "μm": 1000.0,  # greek small letter mu
    "micron": 1000.0,
    "microns": 1000.0,
}

_SCOREABLE_SENSES: tuple[str, ...] = ("maximize", "minimize", "match")
_REPORT_SENSES: tuple[str, ...] = ("report",)

_ORDINARY_REGION_KEYS: tuple[str, ...] = ("wavelength_nm",)
_CONTRAST_REGION_KEYS: tuple[str, ...] = (
    "preferred_wavelength_nm",
    "suppressed_wavelength_nm",
)


class MetricSpec(BaseModel):
    """One executable metric: what it measures and what it needs to be told."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    observables: tuple[str, ...]
    reduction: Literal[
        "band_mean",
        "band_worst_case",
        "band_mean_pair_difference",
        "channel_inventory",
    ]
    required_region_keys: tuple[str, ...]
    allowed_senses: tuple[str, ...]
    scoreable: bool
    summary: str

    @property
    def is_report_only(self) -> bool:
        return not self.scoreable


_ROWS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name="mean_reflectance",
        observables=("R",),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary="Average reflectance across one wavelength band.",
    ),
    MetricSpec(
        name="band_reflectance",
        observables=("R",),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary=(
            "Average reflectance across one wavelength band; an alias kept for "
            "requests phrased as a band specification rather than an average."
        ),
    ),
    MetricSpec(
        name="mean_transmittance",
        observables=("T",),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary="Average transmittance across one wavelength band.",
    ),
    MetricSpec(
        name="mean_absorption",
        observables=("A",),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary="Average absorptance across one wavelength band.",
    ),
    MetricSpec(
        name="mean_emissivity",
        observables=("A",),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary=(
            "Average emissivity across one wavelength band.  Numerically the "
            "band-mean absorptance, by Kirchhoff's law at thermal equilibrium; "
            "prefer this name when the requirement is thermal."
        ),
    ),
    MetricSpec(
        name="reflectance_stopband",
        observables=("R",),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary=(
            "Average reflectance across a band that is meant to be blocked; "
            "prefer this name for a rejection or stopband requirement."
        ),
    ),
    MetricSpec(
        name="worst_case_reflectance",
        observables=("R",),
        reduction="band_worst_case",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary=(
            "The least favourable single reflectance sample in the band, not "
            "its average.  Use this when the requirement must hold everywhere "
            "in the band rather than on average."
        ),
    ),
    MetricSpec(
        name="worst_case_transmittance",
        observables=("T",),
        reduction="band_worst_case",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary=(
            "The least favourable single transmittance sample in the band, for "
            "a requirement that must hold across the whole band."
        ),
    ),
    MetricSpec(
        name="worst_case_absorption",
        observables=("A",),
        reduction="band_worst_case",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary=(
            "The least favourable single absorptance sample in the band, for a "
            "requirement that must hold across the whole band."
        ),
    ),
    MetricSpec(
        name="band_emissivity_contrast",
        observables=("A",),
        reduction="band_mean_pair_difference",
        required_region_keys=_CONTRAST_REGION_KEYS,
        allowed_senses=_SCOREABLE_SENSES,
        scoreable=True,
        summary=(
            "Mean absorptance of a preferred band minus that of a suppressed "
            "band.  The only metric taking two intervals; use it when the "
            "requirement is explicitly the gap between two bands."
        ),
    ),
    MetricSpec(
        name="emissivity_spectrum",
        observables=("A",),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_REPORT_SENSES,
        scoreable=False,
        summary="Reports the emissivity band summary without scoring it.",
    ),
    MetricSpec(
        name="mixed_coherence_RTA",
        observables=("R", "T", "A"),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_REPORT_SENSES,
        scoreable=False,
        summary=(
            "Reports band-mean R, T and A together for a stack that mixes "
            "coherent and incoherent layers.  Requires all three outputs."
        ),
    ),
    MetricSpec(
        name="opaque_stack_rta",
        observables=("R", "T", "A"),
        reduction="band_mean",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_REPORT_SENSES,
        scoreable=False,
        summary=(
            "Reports band-mean R, T and A together for an opaque stack. "
            "Requires all three outputs."
        ),
    ),
    MetricSpec(
        name="resonance_q_phase",
        observables=(),
        reduction="channel_inventory",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_REPORT_SENSES,
        scoreable=False,
        summary=(
            "Reports the available channel data for resonance and phase "
            "analysis; the detailed extraction happens in the analysis report."
        ),
    ),
    MetricSpec(
        name="polarization_splitting",
        observables=(),
        reduction="channel_inventory",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_REPORT_SENSES,
        scoreable=False,
        summary=(
            "Reports the available channel data for a polarization-splitting "
            "analysis; the detailed extraction happens in the analysis report."
        ),
    ),
    MetricSpec(
        name="phase_group_delay_gdd",
        observables=(),
        reduction="channel_inventory",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_REPORT_SENSES,
        scoreable=False,
        summary=(
            "Reports the available channel data for group delay and dispersion "
            "analysis; the detailed extraction happens in the analysis report."
        ),
    ),
    MetricSpec(
        name="layer_absorption",
        observables=(),
        reduction="channel_inventory",
        required_region_keys=_ORDINARY_REGION_KEYS,
        allowed_senses=_REPORT_SENSES,
        scoreable=False,
        summary=(
            "Reports the available per-layer absorption channel data; the "
            "detailed extraction happens in the analysis report."
        ),
    ),
)

METRIC_CATALOG: Dict[str, MetricSpec] = {row.name: row for row in _ROWS}

# Fail at import rather than at scoring time.  If the task contract accepts a
# name this table does not describe, every answer the checker gives about that
# name is unfounded -- it would report "legal" for something it cannot describe,
# or "illegal" for something the contract will happily accept.  A vocabulary
# checker that is quietly out of step with the validator is worse than none.
_missing = sorted(set(SUPPORTED_OBJECTIVE_METRICS) - set(METRIC_CATALOG))
_extra = sorted(set(METRIC_CATALOG) - set(SUPPORTED_OBJECTIVE_METRICS))
if _missing or _extra:  # pragma: no cover - guarded by test_metric_catalog
    raise RuntimeError(
        "metric catalogue is out of step with the task contract; "
        f"undescribed={_missing} not_accepted={_extra}"
    )

SCOREABLE_METRICS: tuple[str, ...] = tuple(
    sorted(name for name, row in METRIC_CATALOG.items() if row.scoreable)
)
REPORT_ONLY_METRICS: tuple[str, ...] = tuple(
    sorted(name for name, row in METRIC_CATALOG.items() if not row.scoreable)
)


class MetricVerification(BaseModel):
    """The checker's answer about one proposed metric reference."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CATALOG_SCHEMA_VERSION
    ok: bool
    canonical_id: str = ""
    variable: str = ""
    normalized: Dict[str, Any] = Field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def repair_hint(self) -> str:
        """A single line safe to hand back to a model for regeneration."""

        return "; ".join(self.errors)


def _format_band(lower: float, upper: float) -> str:
    return f"{lower:g}-{upper:g}nm"


def canonical_metric_id(metric: str, region: Mapping[str, Any]) -> str:
    """Build the stable name a scoring formula refers to this metric by.

    ``mean_reflectance@300-800nm`` rather than a positional index, so a formula
    stays readable and stays valid if the objective list is reordered.
    """

    spec = METRIC_CATALOG.get(metric)
    keys = spec.required_region_keys if spec else _ORDINARY_REGION_KEYS
    parts: list[str] = []
    for key in keys:
        interval = region.get(key)
        if isinstance(interval, (list, tuple)) and len(interval) == 2:
            parts.append(_format_band(float(interval[0]), float(interval[1])))
    return f"{metric}@{'_vs_'.join(parts)}" if parts else metric


FIXED_SCORE_OBJECTIVE_PREFIX = "fixedscore."
"""Marks an objective as belonging to the run-wide frozen scoring standard.

The prefix is a label, not a trust boundary.  It makes the standard's own
objectives identifiable in a persisted report and gives
:meth:`ScoringStandard.score` a direct lookup, but nothing relies on it for a
number: that method re-checks each row's metric and band before reading it, and
falls back to matching on content when the identifier differs.

Survival through the compiler's objective rebuild is handled by the sense, not
by this prefix.  A frozen scoring objective is declared ``sense="report"``,
which is exactly what that pass already retains, and which also keeps it out of
the harness's own aggregate score so freezing the ranking cannot steer what any
route searches for.

The dot keeps the prefix clear of anything the compiler generates: derived
identifiers are case-folded and stripped to ``[a-z0-9_]``, so they can never
contain one.
"""

_VARIABLE_UNSAFE = re.compile(r"[^A-Za-z0-9]+")


def formula_variable_name(metric: str, region: Mapping[str, Any]) -> str:
    """Build the name a scoring formula spells this metric with.

    ``mean_reflectance_300_800nm``.  This is the same reference as
    :func:`canonical_metric_id` in a second spelling, needed because that one
    carries ``@`` and so is neither a Python identifier nor an acceptable
    objective identifier.  Deriving both from the metric and band keeps them in
    step: two references agree on one name exactly when they agree on the other.
    """

    canonical = canonical_metric_id(metric, region)
    return _VARIABLE_UNSAFE.sub("_", canonical).strip("_")


def fixed_score_objective_id(variable: str) -> str:
    """Name the injected objective that carries a frozen scoring metric."""

    return f"{FIXED_SCORE_OBJECTIVE_PREFIX}{variable}"


def is_fixed_score_objective_id(objective_id: Any) -> bool:
    """Whether an objective identifier claims membership of the standard."""

    return isinstance(objective_id, str) and objective_id.startswith(
        FIXED_SCORE_OBJECTIVE_PREFIX
    )


def _normalize_interval(
    raw: Any,
    *,
    field_name: str,
    unit: str,
    errors: list[str],
) -> tuple[float, float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        errors.append(
            f"{field_name} must be a two-value wavelength interval "
            f"like [300, 800]"
        )
        return None
    try:
        lower, upper = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        errors.append(f"{field_name} bounds must be numbers")
        return None
    factor = _WAVELENGTH_UNIT_TO_NM[unit]
    lower, upper = lower * factor, upper * factor
    if lower > upper:
        # A reversed pair is a transcription slip, not a different requirement.
        lower, upper = upper, lower
    if lower <= 0.0:
        errors.append(f"{field_name} must satisfy 0 < lower <= upper in nm")
        return None
    return lower, upper


def verify_metric_reference(reference: Any) -> MetricVerification:
    """Check one proposed metric reference against the catalogue.

    Returns the normalized reference on success and actionable messages on
    failure, so a caller can either build an objective from it directly or quote
    the reasons back when asking for a corrected selection.  Every message names
    the offending field and what a legal value looks like; "invalid metric" on
    its own gives a regeneration attempt nothing to correct.
    """

    errors: list[str] = []
    if not isinstance(reference, Mapping):
        return MetricVerification(
            ok=False,
            errors=("metric reference must be an object with a 'metric' field",),
        )

    metric = str(reference.get("metric") or "").strip()
    if not metric:
        errors.append("metric name is missing")
    elif metric not in METRIC_CATALOG:
        # Offer only the names that can satisfy what this reference is asking
        # for.  A reference that carries a scoring direction and is answered
        # with the full vocabulary invites a second attempt that picks a
        # report-only name and is rejected again for a different reason.
        wants_score = str(reference.get("sense") or "").strip() in _SCOREABLE_SENSES
        offered = SCOREABLE_METRICS if wants_score else tuple(sorted(METRIC_CATALOG))
        near = sorted(
            name
            for name in offered
            if metric.casefold() in name or name in metric.casefold()
        )
        suggestion = f"; closest legal names: {', '.join(near)}" if near else ""
        errors.append(
            f"metric {metric!r} is not computable; "
            f"{'scoreable names are' if wants_score else 'legal names are'}: "
            f"{', '.join(offered)}{suggestion}"
        )
    if errors:
        return MetricVerification(ok=False, errors=tuple(errors))

    spec = METRIC_CATALOG[metric]

    unit = str(reference.get("wavelength_unit") or "nm").strip().casefold()
    if unit not in _WAVELENGTH_UNIT_TO_NM:
        errors.append(
            f"wavelength_unit {unit!r} is unknown; use one of: "
            f"{', '.join(sorted(_WAVELENGTH_UNIT_TO_NM))}"
        )
        unit = "nm"

    raw_region = reference.get("region")
    region: Dict[str, Any] = dict(raw_region) if isinstance(raw_region, Mapping) else {}
    if not isinstance(raw_region, Mapping) and raw_region is not None:
        errors.append("region must be an object keyed by interval field name")

    normalized_region: Dict[str, Any] = {}
    for key in spec.required_region_keys:
        interval = _normalize_interval(
            region.get(key), field_name=key, unit=unit, errors=errors
        )
        if interval is not None:
            normalized_region[key] = [interval[0], interval[1]]
    if metric == "band_emissivity_contrast" and len(normalized_region) == 2:
        preferred = normalized_region["preferred_wavelength_nm"]
        suppressed = normalized_region["suppressed_wavelength_nm"]
        if preferred == suppressed:
            errors.append(
                "band_emissivity_contrast needs two different bands; the "
                "preferred and suppressed intervals are identical"
            )

    unexpected = sorted(
        set(region) - set(spec.required_region_keys) - {"angle_deg", "polarization"}
    )
    if unexpected:
        errors.append(
            f"{metric} does not read region {'fields' if len(unexpected) > 1 else 'field'} "
            f"{', '.join(unexpected)}; it requires exactly "
            f"{', '.join(spec.required_region_keys)}"
        )
    for selector in ("angle_deg", "polarization"):
        if selector in region:
            normalized_region[selector] = region[selector]

    sense = str(reference.get("sense") or "").strip()
    if not sense:
        errors.append(
            f"sense is missing; {metric} accepts: {', '.join(spec.allowed_senses)}"
        )
    elif sense not in spec.allowed_senses:
        reason = (
            f"{metric} is a report-only metric and cannot carry a score"
            if not spec.scoreable
            else f"{metric} accepts: {', '.join(spec.allowed_senses)}"
        )
        errors.append(f"sense {sense!r} is not allowed; {reason}")

    target = reference.get("target")
    if sense == "match" and target is None:
        errors.append("a 'match' sense requires a numeric target")

    if errors:
        return MetricVerification(ok=False, errors=tuple(errors))

    normalized: Dict[str, Any] = {
        "metric": metric,
        "sense": sense,
        "region": normalized_region,
    }
    if target is not None:
        normalized["target"] = float(target)
    return MetricVerification(
        ok=True,
        canonical_id=canonical_metric_id(metric, normalized_region),
        variable=formula_variable_name(metric, normalized_region),
        normalized=normalized,
    )


def verify_metric_selection(references: Sequence[Any]) -> tuple[MetricVerification, ...]:
    """Check a whole proposed selection, keeping one verdict per reference."""

    return tuple(verify_metric_reference(reference) for reference in references)


def catalog_document(*, scoreable_only: bool = True) -> Dict[str, Any]:
    """Render the catalogue as the payload a metric-selection prompt receives.

    ``scoreable_only`` is the default because a selection destined for a scoring
    formula must not contain a metric whose number is never scored; offering
    those names invites exactly that mistake.  Pass ``False`` to document the
    full executable vocabulary, including the report-only compounds.
    """

    engine = describe_capabilities()
    rows = [
        row
        for row in _ROWS
        if row.scoreable or not scoreable_only
    ]
    return {
        "schema_version": CATALOG_SCHEMA_VERSION,
        "engine_id": engine.engine_id,
        "engine_capability_version": engine.capability_version,
        "band_reduction": engine.spectral_metrics.model_dump(mode="json"),
        "reference_shape": {
            "metric": "one of the names below",
            "sense": "maximize | minimize | match (match also needs 'target')",
            "wavelength_unit": "nm by default; declare um explicitly if used",
            "region": {
                "wavelength_nm": "[lower, upper] for ordinary metrics",
                "preferred_wavelength_nm": "[lower, upper] for the contrast metric",
                "suppressed_wavelength_nm": "[lower, upper] for the contrast metric",
                "angle_deg": "optional channel selector",
                "polarization": "optional channel selector, 's' or 'p'",
            },
            "canonical_id_example": "mean_reflectance@300-800nm",
        },
        "metrics": [row.model_dump(mode="json") for row in rows],
        "scoreable_metrics": list(SCOREABLE_METRICS),
        "report_only_metrics": list(REPORT_ONLY_METRICS),
        "scoring_role": (
            "A verified metric is computable and correctly named.  It says "
            "nothing about whether a design is physically accepted."
        ),
    }


__all__ = [
    "CATALOG_SCHEMA_VERSION",
    "FIXED_SCORE_OBJECTIVE_PREFIX",
    "METRIC_CATALOG",
    "MetricSpec",
    "MetricVerification",
    "REPORT_ONLY_METRICS",
    "SCOREABLE_METRICS",
    "canonical_metric_id",
    "catalog_document",
    "fixed_score_objective_id",
    "formula_variable_name",
    "is_fixed_score_objective_id",
    "verify_metric_reference",
    "verify_metric_selection",
]
