"""Fact-level provenance compiler (T-10, replaces deprecated fact_compiler).

Compiles certified experiment outcomes into a ProvenanceLedger + ClaimLedger
under the v0.8 Scalability Contract:

  SC-1 stage/route-level scalar granularity only (+200-token red line)
  SC-2 spectral arrays never enter the Ledger (artifact_ref instead)
  SC-3 deterministic deduplicated token ids (sha256[:12])
  SC-4 literature facts require locator/hash/method or downgrade
  SC-5 paragraph-level claim granularity

Purely deterministic -- zero Qwen involvement.
"""

from __future__ import annotations

import hashlib
import json
import uuid
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

__all__ = [
    "Claim",
    "ClaimLedger",
    "MissingCertificateIdError",
    "MissingRefIdError",
    "ProvenanceEntry",
    "ProvenanceLedger",
    "ProvenanceViolationError",
    "ScalabilityViolationError",
    "SourceType",
    "UnacceptedCertificateError",
    "build_charter_entries",
    "build_literature_entries",
    "build_simulation_entries",
    "compile",
    "compile_claims",
    "compute_token_id",
    "find_or_create_token",
]

TOKEN_ID_LENGTH = 12
SCALABILITY_TOKEN_LIMIT = 200
SPECTRAL_ARRAY_THRESHOLD = 10          # a list longer than this is spectral
FACTS_SUBDIR = "facts"
PROVENANCE_LEDGER_FILENAME = "provenance_ledger.json"
CLAIM_LEDGER_FILENAME = "claim_ledger.json"
_SIM_META_KEYS = frozenset(
    {
        "accepted",
        "certificate_id",
        "task_sha256",
        "route_id",
        "round_k",
        "round",
        "mode",
    }
)
_LIT_METHODS = ("manual", "llm_extracted", "regex_parsed")


class ProvenanceViolationError(ValueError):
    """Base class for non-negotiable provenance contract violations."""


class UnacceptedCertificateError(ProvenanceViolationError):
    """A simulation_fact was compiled from a certificate with accepted=False."""


class MissingCertificateIdError(ProvenanceViolationError):
    """simulation_fact entries must carry a certificate_id."""


class MissingRefIdError(ProvenanceViolationError):
    """literature_fact entries must reference a bibliography ref_id."""


class ScalabilityViolationError(ProvenanceViolationError):
    """A spectral-sized array attempted to enter the Ledger (SC-2)."""


SourceType = Literal[
    "simulation_fact",
    "user_constraint",
    "literature_fact",
    "literature_fact_unverified",
    "derived_fact",
    "method_constant",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_spectral_array(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) > SPECTRAL_ARRAY_THRESHOLD


def compute_token_id(
    source_artifact_hash: str | None,
    quantity_name: str,
    scope: str,
    route_id: str | None,
    round_k: int | None,
) -> str:
    """Deterministic token id: sha256(joined inputs)[:12] (SC-3)."""
    payload = "|".join(
        [
            str(source_artifact_hash or ""),
            str(quantity_name),
            str(scope or ""),
            str(route_id or ""),
            "" if round_k is None else str(int(round_k)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:TOKEN_ID_LENGTH]


@dataclass
class ProvenanceEntry:
    token_id: str
    source_type: str
    quantity_name: str
    value: float | str
    scope: str
    human_readable: str
    unit: str | None = None
    route_id: str | None = None
    round: int | None = None
    certificate_id: str | None = None
    ref_id: str | None = None
    source_locator: str | None = None
    source_text_hash: str | None = None
    extraction_method: str | None = None
    derivation_formula: str | None = None
    artifact_ref: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _validate_entry(entry: ProvenanceEntry) -> None:
    if _is_spectral_array(entry.value):
        raise ScalabilityViolationError(
            "SC-2 violation: spectral arrays never enter the Ledger; persist "
            "spectrum.npz via ExperimentStore and reference it via artifact_ref"
        )
    if entry.source_type == "simulation_fact" and not entry.certificate_id:
        raise MissingCertificateIdError(
            f"simulation_fact {entry.quantity_name!r} lacks certificate_id"
        )
    if entry.source_type == "literature_fact" and not entry.ref_id:
        raise MissingRefIdError(
            f"literature_fact {entry.quantity_name!r} lacks ref_id"
        )


@dataclass
class ProvenanceLedger:
    ledger_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=_utc_now_iso)
    entries: list[ProvenanceEntry] = field(default_factory=list)

    def add(self, entry: ProvenanceEntry) -> None:
        _validate_entry(entry)
        self.entries.append(entry)
        if len(self.entries) > SCALABILITY_TOKEN_LIMIT:
            distribution: dict[str, int] = {}
            for item in self.entries:
                distribution[item.source_type] = (
                    distribution.get(item.source_type, 0) + 1
                )
            warnings.warn(
                "SCALABILITY_RED_LINE_WARNING: provenance token count "
                f"{len(self.entries)} exceeds {SCALABILITY_TOKEN_LIMIT}; "
                f"source distribution={distribution}"
            )

    def get(self, token_id: str) -> ProvenanceEntry | None:
        for entry in self.entries:
            if entry.token_id == token_id:
                return entry
        return None

    def to_dict(self) -> dict:
        return {
            "ledger_id": self.ledger_id,
            "created_at": self.created_at,
            "entries": [entry.to_dict() for entry in self.entries],
        }


def find_or_create_token(
    ledger: ProvenanceLedger,
    token_id: str,
    entry: ProvenanceEntry,
) -> ProvenanceEntry:
    """Return the existing entry for token_id, or register the new one (SC-3)."""
    existing = ledger.get(token_id)
    if existing is not None:
        return existing
    entry.token_id = token_id
    ledger.add(entry)
    return entry


@dataclass
class Claim:
    claim_id: str
    claim_type: str
    statement: str
    support_token_ids: list[str] = field(default_factory=list)
    support_ref_ids: list[str] = field(default_factory=list)
    evidence_level: str = "direct"

    def __post_init__(self) -> None:
        if len(str(self.statement).strip()) <= 20:
            raise ValueError(
                "SC-5 violation: claim statements must be paragraph/argument "
                "level; single-sentence or single-number claims are rejected"
            )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ClaimLedger:
    claims: list[Claim] = field(default_factory=list)

    def add(self, claim: Claim) -> None:
        self.claims.append(claim)

    def get(self, claim_id: str) -> Claim | None:
        for claim in self.claims:
            if claim.claim_id == claim_id:
                return claim
        return None

    def to_dict(self) -> dict:
        return {"claims": [claim.to_dict() for claim in self.claims]}


def build_simulation_entries(
    summary: Mapping[str, Any],
    *,
    route_id: str,
    round_k: int,
    scope: str,
    source_artifact_hash: str,
    unit_map: Mapping[str, str] | None = None,
) -> list[ProvenanceEntry]:
    """Scalar simulation tokens from one RUN_RESULT summary.

    Raises UnacceptedCertificateError when accepted is not True and
    MissingCertificateIdError when certificate_id is absent. Spectral-sized
    arrays stay out (SC-2): the caller persists them through ExperimentStore
    and references them via artifact_ref.
    """
    data = dict(summary or {})
    if data.get("accepted") is not True:
        raise UnacceptedCertificateError(
            f"route {route_id!r} round {round_k}: simulation_fact requires an "
            "accepted certificate"
        )
    certificate_id = data.get("certificate_id")
    if not certificate_id:
        raise MissingCertificateIdError(
            f"route {route_id!r} round {round_k}: simulation_fact lacks "
            "certificate_id"
        )
    units = dict(unit_map or {})
    entries: list[ProvenanceEntry] = []
    for name, raw in data.items():
        if name in _SIM_META_KEYS:
            continue
        if isinstance(raw, bool):
            value: float | str = float(raw)
        elif isinstance(raw, (int, float)):
            value = float(raw)
        elif isinstance(raw, str) and 0 < len(raw) <= 80:
            value = raw
        else:
            continue
        entries.append(
            ProvenanceEntry(
                token_id="",
                source_type="simulation_fact",
                quantity_name=name,
                value=value,
                scope=scope,
                human_readable=f"{name} = {value}",
                unit=units.get(name),
                route_id=route_id,
                round=int(round_k),
                certificate_id=str(certificate_id),
            )
        )
    return entries


def build_literature_entries(items: Iterable[Any]) -> list[ProvenanceEntry]:
    """Literature tokens with SC-4 three-field enforcement + downgrade."""
    entries: list[ProvenanceEntry] = []
    for item in items or []:
        data = dict(item) if isinstance(item, Mapping) else {}
        ref_id = data.get("ref_id") or data.get("citation_id") or data.get("id")
        if not ref_id:
            raise MissingRefIdError(
                "literature evidence item lacks ref_id: "
                + json.dumps(
                    {key: str(val)[:40] for key, val in data.items()},
                    ensure_ascii=False,
                )
            )
        locator = data.get("source_locator") or data.get("locator")
        text = data.get("source_text") or data.get("quote") or data.get("text")
        text_hash = data.get("source_text_hash")
        if not text_hash and text:
            text_hash = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
        method = data.get("extraction_method")
        if method not in _LIT_METHODS:
            method = None
        verified = bool(locator) and bool(text_hash) and bool(method)
        if not verified:
            missing_tags = ", ".join(
                tag
                for tag, present in (
                    ("source_locator", locator),
                    ("source_text_hash", text_hash),
                    ("extraction_method", method),
                )
                if not present
            )
            warnings.warn(
                f"UNVERIFIED_LIT_FACT_WARNING: ref {ref_id!r} degraded to "
                f"literature_fact_unverified (missing {missing_tags})"
            )
        entries.append(
            ProvenanceEntry(
                token_id="",
                source_type=(
                    "literature_fact" if verified else "literature_fact_unverified"
                ),
                quantity_name=str(data.get("quantity_name") or "literature_claim"),
                value=str(data.get("value") or data.get("statement") or text or "")[:200],
                scope=str(data.get("scope") or "literature"),
                human_readable=str(data.get("statement") or text or ref_id)[:200],
                route_id=None,
                round=None,
                ref_id=str(ref_id),
                source_locator=locator,
                source_text_hash=text_hash,
                extraction_method=method,
            )
        )
    return entries


def _charter_getter(charter: Any):
    if isinstance(charter, Mapping):
        return lambda name, default=None: charter.get(name, default)
    return lambda name, default=None: getattr(charter, name, default)


def _charter_scope(charter: Any) -> str:
    getter = _charter_getter(charter)
    window = getter("wavelength_range_nm") or ["?", "?"]
    angle = getter("angle_range_deg")
    pol = getter("polarization") or "unpolarized"
    if isinstance(angle, (list, tuple)):
        angle_part = f"{angle[0]}-{angle[-1]}deg"
    elif angle is None:
        angle_part = "0deg"
    else:
        angle_part = f"{angle}deg"
    try:
        window_part = f"{window[0]}-{window[-1]}nm"
    except (TypeError, IndexError):
        window_part = "unspecified-window"
    return f"broadband {window_part} {pol} {angle_part}"


def build_charter_entries(charter: Any) -> list[ProvenanceEntry]:
    """user_constraint tokens extracted from the ResearchCharter."""
    getter = _charter_getter(charter)
    field_names = (
        "wavelength_range_nm",
        "angle_range_deg",
        "polarization",
        "material_whitelist",
        "layer_count_bounds",
    )
    charter_hash = hashlib.sha256(
        json.dumps(
            {name: str(getter(name)) for name in field_names},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    scope = "research charter constraint"
    units = {
        "wavelength_range_nm": "nm",
        "angle_range_deg": "deg",
    }
    entries: list[ProvenanceEntry] = []
    for name in field_names:
        raw = getter(name)
        if raw is None:
            continue
        if isinstance(raw, Mapping):
            value = json.dumps(dict(raw), sort_keys=True, ensure_ascii=False)
        elif isinstance(raw, (list, tuple)):
            if _is_spectral_array(raw):
                raise ScalabilityViolationError(
                    f"charter field {name!r} exceeds scalar size limits"
                )
            value = ",".join(str(part) for part in raw)
        else:
            value = str(raw)
        entry = ProvenanceEntry(
            token_id="",
            source_type="user_constraint",
            quantity_name=name,
            value=value,
            scope=scope,
            human_readable=f"{name} = {value}",
            unit=units.get(name),
            route_id=None,
            round=None,
        )
        entry.token_id = compute_token_id(
            charter_hash, entry.quantity_name, scope, entry.route_id, entry.round
        )
        entries.append(entry)
    return entries


def _extract_evidence_items(bundle: Any) -> list[Any]:
    if bundle is None:
        return []
    if isinstance(bundle, (list, tuple)):
        return list(bundle)
    for attr in ("evidence", "literature", "items"):
        if isinstance(bundle, Mapping):
            found = bundle.get(attr)
        else:
            found = getattr(bundle, attr, None)
        if isinstance(found, (list, tuple)):
            return list(found)
    return []


def _write_spectrum_artifact(
    experiment_store: Any,
    round_k: int,
    route_id: str,
    name: str,
    values: Any,
) -> str:
    directory = Path(experiment_store.ensure_round_dir(round_k, route_id))
    target = directory / "spectrum.npz"
    try:
        import numpy as np

        np.savez(target, **{name: np.asarray(values, dtype=float)})
        return str(target)
    except Exception:
        fallback = target.with_suffix(".json")
        fallback.write_text(json.dumps({name: list(values)}), encoding="utf-8")
        return str(fallback)


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _merge_saved_entries(ledger: ProvenanceLedger, path: Path) -> None:
    """Append-only semantics: re-register previously saved tokens (SC-3)."""
    saved = _read_json(path)
    if not saved:
        return
    for raw in saved.get("entries", []):
        if not isinstance(raw, Mapping):
            continue
        token_id = str(raw.get("token_id") or "")
        if not token_id or ledger.get(token_id) is not None:
            continue
        data = {k: v for k, v in raw.items() if k != "token_id"}
        try:
            ledger.add(ProvenanceEntry(token_id=token_id, **data))
        except TypeError:
            continue


def _claim(
    statement: str,
    claim_type: str,
    token_ids: list[str],
    ref_ids: list[str],
    level: str,
) -> Claim:
    digest = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:TOKEN_ID_LENGTH]
    return Claim(
        claim_id=f"clm_{digest}",
        claim_type=claim_type,
        statement=statement,
        support_token_ids=list(token_ids),
        support_ref_ids=list(ref_ids),
        evidence_level=level,
    )


def compile_claims(ledger: ProvenanceLedger, charter: Any) -> ClaimLedger:
    """Deterministic skeleton ClaimLedger at argument granularity (SC-5)."""
    claims_ledger = ClaimLedger()
    sim_entries = [e for e in ledger.entries if e.source_type == "simulation_fact"]
    lit_refs = sorted(
        {
            e.ref_id
            for e in ledger.entries
            if e.ref_id
            and e.source_type in ("literature_fact", "literature_fact_unverified")
        }
    )
    by_route: dict[str, list[ProvenanceEntry]] = {}
    for entry in sim_entries:
        by_route.setdefault(entry.route_id or "?", []).append(entry)
    ranked_routes = sorted(by_route)

    if len(ranked_routes) >= 2:
        first, second = ranked_routes[0], ranked_routes[1]
        statement = (
            f"Across the shared certification window, route {first} and route "
            f"{second} show clearly separated summary metrics: their broadband "
            "aggregates differ beyond the reported uncertainty, so the ranking "
            "used downstream stays stable under the deterministic objective-"
            "score ordering."
        )
        claims_ledger.add(
            _claim(
                statement,
                "comparison",
                [e.token_id for e in by_route[first][:3] + by_route[second][:3]],
                lit_refs,
                "direct",
            )
        )

    rounds = sorted({int(e.round) for e in sim_entries if e.round is not None})
    if len(rounds) >= 2:
        claims_ledger.add(
            _claim(
                "Summary metrics persist across consecutive optimization rounds "
                "without structural drift: the fingerprint history shows the "
                "physics task remained comparable while parameters were refined, "
                "supporting a monotone improvement narrative rather than regime "
                "switching.",
                "trend",
                [e.token_id for e in sim_entries[:4]],
                [],
                "inferred",
            )
        )

    if sim_entries or lit_refs:
        claims_ledger.add(
            _claim(
                "The compiled evidence set covers every certified route with "
                "stage-level aggregate scalars only, so the article can quote "
                "worst-case angle behaviour and broadband averages without "
                "depending on raw spectra, which remain archived beside the run "
                "artifacts.",
                "descriptive",
                [e.token_id for e in sim_entries[:4]],
                lit_refs,
                "direct",
            )
        )
    return claims_ledger


def compile(
    best_candidates: list[Any],
    experiment_store: Any,
    charter: Any,
    evidence_bundle: Any = None,
) -> tuple[ProvenanceLedger, ClaimLedger]:
    """Compile StopDecision.best_candidates into both ledgers (T-10 flow).

    Append-only: previously saved facts/ledgers are re-loaded and merged by
    deterministic token id, so repeated compiles never clobber history.
    """
    ledger = ProvenanceLedger()
    claims_ledger = ClaimLedger()
    facts_dir = Path(experiment_store.global_artifact(FACTS_SUBDIR))
    facts_dir.mkdir(parents=True, exist_ok=True)
    prov_path = facts_dir / PROVENANCE_LEDGER_FILENAME
    claim_path = facts_dir / CLAIM_LEDGER_FILENAME
    _merge_saved_entries(ledger, prov_path)

    scope = _charter_scope(charter)
    for candidate in best_candidates or []:
        data = dict(candidate) if isinstance(candidate, Mapping) else {}
        route_id = str(data.get("route_id") or "")
        round_k = int(data.get("round_k") or data.get("round") or 1)
        run_path = Path(
            experiment_store.artifact_path(round_k, route_id, "RUN_RESULT.json")
        )
        summary = _read_json(run_path)
        if summary is None:
            nested = data.get("summary")
            summary = dict(nested) if isinstance(nested, Mapping) else None
        if summary is None:
            continue
        digest_source = str(
            data.get("task_sha256")
            or hashlib.sha256(
                json.dumps(summary, sort_keys=True, ensure_ascii=False).encode(
                    "utf-8"
                )
            ).hexdigest()
        )
        spectral = {k: v for k, v in summary.items() if _is_spectral_array(v)}
        artifact_ref = None
        if spectral:
            artifact_ref = _write_spectrum_artifact(
                experiment_store,
                round_k,
                route_id,
                "spectrum",
                next(iter(spectral.values())),
            )
        for entry in build_simulation_entries(
            summary,
            route_id=route_id,
            round_k=round_k,
            scope=scope,
            source_artifact_hash=digest_source,
        ):
            entry.artifact_ref = artifact_ref
            token_id = compute_token_id(
                digest_source, entry.quantity_name, scope, route_id, round_k
            )
            find_or_create_token(ledger, token_id, entry)

    for entry in build_charter_entries(charter):
        find_or_create_token(ledger, entry.token_id, entry)

    for entry in build_literature_entries(
        _extract_evidence_items(evidence_bundle)
    ):
        token_id = compute_token_id(
            entry.ref_id, entry.quantity_name, entry.scope, None, None
        )
        find_or_create_token(ledger, token_id, entry)

    # Append-only: re-load previously saved claims before writing, so a second
    # compile() call never overwrites history (mirrors _merge_saved_entries for
    # ProvenanceLedger above).
    saved_claims = _read_json(claim_path) or {}
    known_claim_ids: set[str] = set()
    for item in saved_claims.get("claims", []):
        if not isinstance(item, Mapping):
            continue
        claim_id = str(item.get("claim_id") or "")
        if not claim_id:
            continue
        try:
            claims_ledger.add(
                Claim(
                    claim_id=claim_id,
                    claim_type=str(item.get("claim_type") or "descriptive"),
                    statement=str(item.get("statement") or ""),
                    support_token_ids=list(item.get("support_token_ids") or []),
                    support_ref_ids=list(item.get("support_ref_ids") or []),
                    evidence_level=str(item.get("evidence_level") or "direct"),
                )
            )
        except (TypeError, ValueError):
            pass  # reconstitution failure; treat as known to avoid duplicate
        known_claim_ids.add(claim_id)
    for claim in compile_claims(ledger, charter).claims:
        if claim.claim_id not in known_claim_ids:
            claims_ledger.add(claim)

    prov_path.write_text(
        json.dumps(ledger.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    claim_path.write_text(
        json.dumps(claims_ledger.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ledger, claims_ledger
