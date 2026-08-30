"""The material vocabulary a planned route may draw on.

Route planning runs before any task is compiled, so it has to know which
material names the engine can actually resolve.  This module exports that
vocabulary from the public ``MaterialRegistry`` facade and checks proposals
against it, so an unresolvable name is caught while the route can still be
regenerated instead of after a round has been spent on it.

Two deliberate limits, both stated rather than hidden:

* The guaranteed-name list remains deliberately small, but route verification
  now has a separate band-aware candidate feed.  It asks the full registry for
  real local/RII datasets, ranks them by measured coverage, n/k completeness
  and sampling density, and records the result.  The name-only payload below
  is kept for compatibility with the outer route planner; it must not be read
  as a claim that the list itself proves spectral coverage.
* The listed names are the ones that are *guaranteed* to resolve, not the only
  legal ones.  The registry resolves many more names than it lists (``Ta2O5``
  and ``Nb2O5`` among them), so treating the list as a whitelist would
  amputate legal high-index materials.  The list is the safe path; the
  registry itself remains the gate.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


MATERIAL_CATALOG_SCHEMA_VERSION = "1.0"
MATERIAL_SELECTOR_SCHEMA_VERSION = "route-material-selector.v1"
MATERIAL_SELECTOR_MAX_TOKENS = 1800

_MATERIAL_SELECTOR_SYSTEM_PROMPT = """
You are the internal material-dataset selector inside a scientific TMM
harness. This is not route planning and you must not redesign the route.
Return exactly one JSON object and no markdown:
{"selections":[{"request_id":"material_01","selected_rank":1,"provider":"rii_sqlite","dataset_id":562,"reason":"..."}]}

The user question is authoritative. Each request contains candidates already
retrieved from the local MaterialRegistry and sorted by three equal physical
metrics: target-band coverage, n/k completeness, and sampling density. Rank 1
is the most physically reliable candidate; lower rank means less reliable.
Choose exactly one candidate from the supplied list for every request. Never
invent, rename, or edit a provider or dataset_id. The selected_rank must point
to the selected array element, and provider/dataset_id must copy that element.

Material identity, stoichiometry, explicit material state/preparation, and an
explicit requested source are hard constraints. A candidate with
formula_exact=false or semantic_match=same_element_family is not an allowed
substitute for a formula request. SiO is not SiO2, even if the SiO2 candidate
has better physical coverage. When several candidates satisfy the hard
constraints, select the highest-ranked one (usually rank 1). If no candidate
satisfies them, return selected_rank:null, provider:null, dataset_id:null and
reason:"needs_confirmation" for that request.
""".strip()

COVERAGE_DEFERRAL = (
    "the guaranteed-name list proves name resolution only; the separate route "
    "candidate ranking reads actual n/k grids and records native coverage or "
    "explicit interpolation/endpoint-extension availability. Execution still keeps the "
    "registry's allow_extrapolation switch fail-closed"
)

MEASURED_NOT_TUNABLE = (
    "refractive index and extinction coefficient are measured data read from "
    "the dataset; they are never design variables. Tune geometry (thickness, "
    "layer count, order) and the choice of material from this list instead"
)

# Chemical element symbols, used to tell a formula-shaped token in free text
# from an optics abbreviation.  General chemistry rather than a project
# stop-list, so it does not drift as the request vocabulary changes.  Stops at
# uranium: nothing heavier occurs naturally or has an optical-constant
# dataset, and carrying the synthetic symbols only creates false readings of
# ordinary words -- "IDs" parses as iodine-darmstadtium, "No" as nobelium.
_ELEMENT_SYMBOLS: frozenset[str] = frozenset(
    """
    H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni
    Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I
    Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt
    Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U
    """.split()
)

_ELEMENTS_BY_FOLD: Dict[str, str] = {
    symbol.casefold(): symbol for symbol in _ELEMENT_SYMBOLS
}

_UNICODE_SUBSCRIPT_TRANSLATION = str.maketrans(
    "₀₁₂₃₄₅₆₇₈₉₊₋₍₎",
    "0123456789+-()",
)

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9₀₁₂₃₄₅₆₇₈₉₊₋₍₎]*")


def _element_groups(
    token: str, *, strict_case: bool = False
) -> Optional[list[tuple[str, str]]]:
    """Split a token into (element, count) groups, or ``None`` if it is not one.

    Two-character symbols are tried first: ``sin`` is silicon nitride written
    loosely, not sulphur-indium.

    ``strict_case`` demands chemistry's own capitalisation -- a capital, then an
    optional lower-case letter.  It is essential when reading free text,
    because case-insensitive matching turns ordinary English into formulas:
    ``layer`` decomposes to La-Y-Er and ``stack`` to S-Ta-C-K.  Comparing
    against the registry's own lower-case names needs the lenient mode.
    """

    token = str(token or "").translate(_UNICODE_SUBSCRIPT_TRANSLATION)
    groups: list[tuple[str, str]] = []
    index = 0
    while index < len(token):
        pair = token[index : index + 2]
        if strict_case:
            pair_ok = (
                len(pair) == 2
                and pair[0].isupper()
                and pair[1].islower()
                and pair in _ELEMENT_SYMBOLS
            )
        else:
            pair_ok = len(pair) == 2 and pair.casefold() in _ELEMENTS_BY_FOLD
        if pair_ok:
            symbol = _ELEMENTS_BY_FOLD[pair.casefold()]
            index += 2
        else:
            single = token[index]
            if strict_case and not single.isupper():
                return None
            if single.casefold() not in _ELEMENTS_BY_FOLD:
                return None
            symbol = _ELEMENTS_BY_FOLD[single.casefold()]
            index += 1
        digits = ""
        while index < len(token) and token[index].isdigit():
            digits += token[index]
            index += 1
        groups.append((symbol, digits))
    return groups or None


def _element_set(token: str) -> frozenset[str]:
    """The elements a name is built from, counts ignored."""

    groups = _element_groups(str(token or "").strip())
    return frozenset(symbol for symbol, _ in groups) if groups else frozenset()


def _looks_like_formula(token: str) -> bool:
    """Whether a bare token in prose is worth resolving as a material.

    Conservative on purpose: this runs over free text, where a false positive
    would fail a physically sound route.  A token must be spelled the way
    chemistry spells a formula, must name at least two elements, and must then
    carry a digit or a two-character symbol.  That keeps ``UV``
    (uranium-vanadium on paper), a sentence-initial ``In``/``As`` and a label
    like ``V01`` out, while keeping ``SiN``, ``Si3N4``, ``Ta2O5`` and ``GaAs``
    in.  The cost is that a bare ``Au`` in prose is not flagged; a route that
    means to use gold names it in ``proposed_materials``, where every entry is
    resolved whatever its shape.
    """

    groups = _element_groups(token, strict_case=True)
    if not groups or len(groups) < 2:
        return False
    return any(digits for _, digits in groups) or any(
        len(symbol) == 2 for symbol, _ in groups
    )


@dataclasses.dataclass(frozen=True)
class MaterialVerdict:
    """One name, and what the registry said about it.

    On success the dataset is identified, not just renamed.  A canonical name
    alone is not provenance: ``au`` is ambiguous among three datasets while the
    synonym ``gold`` resolves to a fourth, so a record holding only ``gold``
    cannot tell a reader which measurement the run actually used.  ``coverage``
    is the dataset's own wavelength span in micrometres, kept here and nowhere
    near the prompt, because it is what a later interpolation step would have
    to reason about -- and because it is the only way to see that ``sio``
    stops at 0.18um and cannot serve a visible-band request at all.
    """

    proposed: str
    ok: bool
    resolved: Optional[str] = None
    code: Optional[str] = None
    detail: Optional[str] = None
    choices: Tuple[str, ...] = ()
    near: Tuple[str, ...] = ()
    where: Optional[str] = None
    dataset_names: Tuple[str, ...] = ()
    provider: Optional[str] = None
    dataset_id: Optional[str] = None
    source: Optional[str] = None
    coverage_um: Optional[Tuple[float, float]] = None

    def dataset(self) -> Optional[str]:
        """``provider:dataset_id``, the form the engine itself prints."""

        if not self.provider or self.dataset_id is None:
            return None
        return f"{self.provider}:{self.dataset_id}"

    def message(self) -> str:
        """The line handed back for regeneration: what is wrong and what to use."""

        if self.ok:
            dataset = self.dataset()
            return (
                f"{self.proposed} resolves to {self.resolved}"
                + (f" ({dataset})" if dataset else "")
            )
        where = f" in {self.where}" if self.where else ""
        if self.code == "ambiguous":
            listed = ", ".join(self.choices[:6])
            head = (
                f"material '{self.proposed}'{where} is ambiguous: the registry "
                f"ranks {len(self.choices)} datasets equally"
                + (f" ({listed})" if listed else "")
                + " and will not choose between them; those identifiers are "
                "diagnostic and are not accepted as material names"
            )
        elif self.code == "not_found":
            head = (
                f"material '{self.proposed}'{where} has no dataset in the "
                "registry"
            )
        else:
            head = (
                f"material '{self.proposed}'{where} cannot be resolved: "
                f"{self.detail or self.code or 'unknown error'}"
            )
        # Guaranteed names come first: they are local, and their coverage is
        # wide.  Naming a single dataset is the fallback for a material the
        # list does not carry at all, where substituting would lose what the
        # question asked for.
        hints = []
        if self.near:
            hints.append(f"closest guaranteed names: {', '.join(self.near)}")
        if self.dataset_names:
            hints.append(
                "to keep this exact material instead, name one dataset "
                f"directly: {', '.join(self.dataset_names)}"
            )
        if hints:
            return f"{head}; " + "; ".join(hints)
        return head

    def as_dict(self) -> Dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["choices"] = list(self.choices)
        payload["near"] = list(self.near)
        payload["dataset_names"] = list(self.dataset_names)
        payload["dataset"] = self.dataset()
        payload["coverage_um"] = (
            list(self.coverage_um) if self.coverage_um else None
        )
        return payload


class RouteMaterialCatalog:
    """The names a route may use, and the check that they resolve.

    Construction touches the registry, so a caller that cannot afford an
    engine import should inject ``names`` or skip the catalogue entirely.
    """

    def __init__(
        self,
        registry: Any = None,
        *,
        names: Optional[Sequence[str]] = None,
        selector_client: Any = None,
        max_candidates: int = 10,
    ) -> None:
        if registry is None:
            from tmm_engine import MaterialRegistry  # local: keeps the import optional

            registry = MaterialRegistry()
        self.registry = registry
        self._names: Tuple[str, ...] = (
            tuple(dict.fromkeys(str(name).strip() for name in names if str(name).strip()))
            if names is not None
            else self._export_names()
        )
        self._elements: Dict[str, frozenset[str]] = {
            name: _element_set(name) for name in self._names
        }
        self._cache: Dict[str, MaterialVerdict] = {}
        self.selector_client = selector_client
        self.max_candidates = max(1, min(10, int(max_candidates)))
        self._selector_usage: List[Dict[str, Any]] = []

    # -- vocabulary ------------------------------------------------------

    def _export_names(self) -> Tuple[str, ...]:
        """Every locally shipped dataset name the registry actually resolves.

        Taken from the local provider because those names are the ones that
        are safe by construction: a local dataset shadows the registry's
        ambiguous remote pages, which is exactly why ``Ag`` resolves while
        ``Au`` -- shipped only remotely, under several pages -- does not.  Each
        candidate is resolved before it is listed, so the list cannot promise a
        name the engine would refuse.
        """

        provider = getattr(self.registry, "local_provider", None)
        directory = getattr(provider, "materials_dir", None)
        if not directory:
            return ()
        found: list[str] = []
        for path in sorted(Path(directory).glob("*.csv")):
            name = path.stem.strip()
            if not name:
                continue
            try:
                self.registry.resolve(name)
            except Exception:
                continue
            found.append(name)
        return tuple(dict.fromkeys(found))

    @property
    def names(self) -> Tuple[str, ...]:
        return self._names

    def __bool__(self) -> bool:
        return bool(self._names)

    def _near(self, proposed: str) -> Tuple[str, ...]:
        """Listed names built from the same elements, else plain substring kin."""

        wanted = _element_set(proposed)
        if wanted:
            same = [
                name for name, elements in self._elements.items() if elements == wanted
            ]
            if same:
                return tuple(sorted(same))
            overlap = [
                name
                for name, elements in self._elements.items()
                if elements and wanted <= elements
            ]
            if overlap:
                return tuple(sorted(overlap))
        folded = str(proposed or "").strip().casefold()
        if not folded:
            return ()
        return tuple(
            sorted(
                name
                for name in self._names
                if folded in name.casefold() or name.casefold() in folded
            )
        )

    # -- verification ----------------------------------------------------

    def verify(self, proposed: Any, *, where: Optional[str] = None) -> MaterialVerdict:
        """Resolve one proposed name through the registry."""

        name = str(proposed or "").strip()
        if not name:
            return MaterialVerdict(
                proposed=name,
                ok=False,
                code="invalid",
                detail="empty material name",
                near=self._names[:6],
                where=where,
            )
        cached = self._cache.get(name.casefold())
        if cached is not None:
            return dataclasses.replace(cached, where=where)
        verdict = self._resolve(name)
        self._cache[name.casefold()] = verdict
        return dataclasses.replace(verdict, where=where)

    def _resolve(self, name: str) -> MaterialVerdict:
        try:
            reference = self.registry.resolve(name)
        except Exception as exc:
            code = _failure_code(exc)
            choices = _ambiguity_choices(exc)
            return MaterialVerdict(
                proposed=name,
                ok=False,
                code=code,
                detail=_first_line(exc),
                choices=choices,
                near=self._near(name),
                dataset_names=(
                    self._dataset_names(name, choices)
                    if code == "ambiguous"
                    else ()
                ),
            )
        resolved = getattr(reference, "material", None) or getattr(
            reference, "name", None
        )
        return MaterialVerdict(
            proposed=name,
            ok=True,
            resolved=str(resolved) if resolved else name,
            provider=_text_or_none(getattr(reference, "provider", None)),
            dataset_id=_text_or_none(getattr(reference, "dataset_id", None)),
            source=_dataset_source(reference),
            coverage_um=_dataset_coverage(reference),
        )

    def _dataset_names(
        self, name: str, choices: Sequence[str]
    ) -> Tuple[str, ...]:
        """Names that address one dataset out of an ambiguous set.

        The ambiguity error lists dataset identifiers, and those identifiers are
        not accepted as material names -- so quoting them alone gives the model
        nothing it can write.  Each dataset does have a page name, though, and a
        page name unique across the library resolves on its own.  Translating
        the identifiers the registry refused to choose between into names the
        registry accepts turns the repair into one round, and keeps the material
        the question asked for instead of pushing the model onto a synonym that
        happens to resolve elsewhere.

        Only pages that resolve back to the dataset they came from are offered:
        a page name shared by several books is itself ambiguous and would send
        the model in a circle.
        """

        search = getattr(self.registry, "search", None)
        if not callable(search):
            return ()
        try:
            candidates = list(search(name) or ())
        except Exception:
            return ()
        wanted = {str(item) for item in choices}
        offers: List[str] = []
        for candidate in candidates:
            label = _candidate_dataset(candidate)
            if wanted and label not in wanted:
                continue
            page = _candidate_page(candidate)
            if not page or page in offers:
                continue
            try:
                reference = self.registry.resolve(page)
            except Exception:
                continue
            if _candidate_dataset(reference) == label:
                offers.append(page)
            if len(offers) >= 6:
                break
        return tuple(offers)

    def dataset_population(self, name: str) -> Optional[int]:
        """How many datasets carry this name at all, or None if unknown.

        The ambiguity error reports only the datasets that tied for best, which
        reads as the whole population and is not: gold ties three ways out of
        twenty-four.
        """

        search = getattr(self.registry, "search", None)
        if not callable(search):
            return None
        try:
            return len(list(search(name) or ()))
        except Exception:
            return None

    def verify_all(
        self, proposals: Iterable[Any], *, where: Optional[str] = None
    ) -> Tuple[MaterialVerdict, ...]:
        seen: Dict[str, MaterialVerdict] = {}
        for proposal in proposals or ():
            verdict = self.verify(proposal, where=where)
            seen.setdefault(verdict.proposed.casefold(), verdict)
        return tuple(seen.values())

    def scan_text(
        self, text: Any, *, where: Optional[str] = None
    ) -> Tuple[MaterialVerdict, ...]:
        """Unresolvable formula-shaped names mentioned in free text.

        The request text is what the task compiler reads, so a name that never
        appears in ``proposed_materials`` still reaches the engine from here.
        Only failures are returned; resolvable mentions are silent.
        """

        body = str(text or "")
        if not body.strip():
            return ()
        failures: Dict[str, MaterialVerdict] = {}
        for match in _TOKEN.finditer(body):
            token = match.group(0)
            if token.casefold() in failures or not _looks_like_formula(token):
                continue
            # A selected route may deliberately spell a dataset as
            # ``ZnSe (Querry)``.  The base formula is not a second, unqualified
            # material mention; checking it separately would reintroduce the
            # ambiguity the page selector just resolved.
            if re.match(r"\s*[\(\[\{]", body[match.end() :]):
                continue
            verdict = self.verify(token, where=where)
            if not verdict.ok:
                failures[token.casefold()] = verdict
        return tuple(failures.values())

    # -- band-aware route selection -------------------------------------

    def drain_selector_usage(self) -> Tuple[Dict[str, Any], ...]:
        """Return and clear usage rows emitted by the internal selector.

        The outer planner owns the run-level meter.  Draining rather than
        charging here keeps a direct catalogue call usable in tests and lets
        the planner record this extra call beside the unchanged route calls.
        """

        rows = tuple(dict(row) for row in self._selector_usage)
        self._selector_usage.clear()
        return rows

    @staticmethod
    def _problem_bands_um(
        user_question: str,
        problem_analysis: Any = None,
    ) -> List[List[float]]:
        """Read the already-normalised target bands, with a conservative fallback."""

        analysis = _plain(problem_analysis)
        raw_intervals: Any = None
        scale = 1.0e-3
        if isinstance(analysis, Mapping):
            sources = (analysis,)
            nested = analysis.get("analysis") or analysis.get("problem_analysis")
            if isinstance(nested, Mapping):
                sources = (analysis, nested)
            for source in sources:
                for key in (
                    "wavelengths_nm",
                    "wavelength_intervals_nm",
                    "spectral_bands_nm",
                ):
                    if source.get(key) is not None:
                        raw_intervals = source.get(key)
                        scale = 1.0e-3
                        break
                if raw_intervals is not None:
                    break
                for key in (
                    "wavelengths_um",
                    "wavelength_intervals_um",
                    "spectral_bands_um",
                ):
                    if source.get(key) is not None:
                        raw_intervals = source.get(key)
                        scale = 1.0
                        break
                if raw_intervals is not None:
                    break
        if (
            isinstance(raw_intervals, (list, tuple))
            and len(raw_intervals) == 2
            and all(not isinstance(item, (list, tuple, Mapping)) for item in raw_intervals)
        ):
            raw_intervals = [raw_intervals]
        bands: List[List[float]] = []
        for interval in raw_intervals or ():
            if not isinstance(interval, (list, tuple)) or len(interval) != 2:
                continue
            try:
                low, high = float(interval[0]) * scale, float(interval[1]) * scale
            except (TypeError, ValueError):
                continue
            if low > high:
                low, high = high, low
            if low >= 0.0 and high > 0.0 and high >= low:
                bands.append([low, high])
        if not bands:
            # Only explicit intervals are inferred from prose.  A lone
            # ``550 nm`` is not promoted to a zero-width target band.
            pattern = re.compile(
                r"(?P<low>\d+(?:\.\d+)?)\s*(?P<low_unit>nm|um|µm)?\s*"
                r"(?:-|–|~|to)\s*"
                r"(?P<high>\d+(?:\.\d+)?)\s*(?P<high_unit>nm|um|µm)\b",
                re.IGNORECASE,
            )
            for match in pattern.finditer(str(user_question or "")):
                low_unit = (match.group("low_unit") or match.group("high_unit") or "nm").casefold()
                high_unit = (match.group("high_unit") or low_unit).casefold()
                try:
                    low = float(match.group("low")) * (1.0e-3 if low_unit == "nm" else 1.0)
                    high = float(match.group("high")) * (1.0e-3 if high_unit == "nm" else 1.0)
                except (TypeError, ValueError):
                    continue
                if low > high:
                    low, high = high, low
                if high > 0.0:
                    item = [low, high]
                    if item not in bands:
                        bands.append(item)
        return bands

    @staticmethod
    def _candidate_display_name(candidate: Mapping[str, Any], proposed: str) -> str:
        provider = str(candidate.get("provider") or "").casefold()
        book = _text_or_none(candidate.get("book"))
        page = _text_or_none(candidate.get("page"))
        if provider == "rii_sqlite" and book and page:
            return f"{book} ({page})"
        material = _text_or_none(candidate.get("material"))
        return material or str(proposed).strip()

    @staticmethod
    def _candidate_is_compatible(candidate: Mapping[str, Any]) -> bool:
        requested_signature = candidate.get("requested_formula_signature") or []
        if requested_signature:
            return bool(candidate.get("formula_exact"))
        if candidate.get("formula_exact") is True:
            return True
        return str(candidate.get("semantic_match") or "") in {
            "exact",
            "exact_name",
            "exact_formula",
        }

    @staticmethod
    def _same_dataset(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        left_provider = str(left.get("provider") or "").casefold()
        right_provider = str(right.get("provider") or "").casefold()
        left_id = str(left.get("dataset_id"))
        right_id = str(right.get("dataset_id"))
        return left_provider == right_provider and left_id == right_id

    @staticmethod
    def _safe_selector_json(text: Any) -> Dict[str, Any]:
        body = str(text or "").strip()
        try:
            value = json.loads(body)
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            start, end = body.find("{"), body.rfind("}")
            if start >= 0 and end > start:
                try:
                    value = json.loads(body[start : end + 1])
                    return value if isinstance(value, dict) else {}
                except json.JSONDecodeError:
                    pass
        return {}

    def select_materials_for_route(
        self,
        proposals: Iterable[Any],
        *,
        user_question: str = "",
        problem_analysis: Any = None,
        selector_client: Any = None,
        force_mock: bool | None = None,
    ) -> Tuple[List[str], Dict[str, Any]]:
        """Resolve route proposals to ranked datasets, then ask one LLM to choose.

        The call is intentionally one batch per route, not one call per
        material.  It is an internal material-binding call: the external route
        planner and its prompt remain unchanged, while the selector receives
        the original user question and the complete ranked evidence table.
        """

        ordered: List[str] = []
        seen: set[str] = set()
        for proposal in proposals or ():
            name = str(proposal or "").strip()
            if name and name.casefold() not in seen:
                seen.add(name.casefold())
                ordered.append(name)
        bands = self._problem_bands_um(user_question, problem_analysis)
        report: Dict[str, Any] = {
            "schema_version": MATERIAL_SELECTOR_SCHEMA_VERSION,
            "ranking_schema_version": "material-candidate-ranking.v1",
            "status": "pending",
            "selection_method": "ranked_candidates_then_internal_llm",
            "max_candidates": int(self.max_candidates),
            "target_wavelength_ranges_um": bands,
            "user_question": str(user_question or ""),
            "requests": [],
            "call_count": 0,
        }
        if not ordered:
            report["status"] = "no_material_requests"
            report["selection_method"] = "no_material_requests"
            return [], report
        ranker = getattr(self.registry, "rank_candidates", None)
        if not callable(ranker):
            # Small injected registries in old callers do not expose the new
            # optional feed.  Preserve their established name-only behavior.
            report["status"] = "legacy_name_verification"
            report["selection_method"] = "legacy_name_verification"
            report["requests"] = [
                {"request_id": f"material_{index:02d}", "proposed_material": name}
                for index, name in enumerate(ordered, 1)
            ]
            return ordered, report

        for index, name in enumerate(ordered, 1):
            request_id = f"material_{index:02d}"
            try:
                candidates = list(
                    ranker(
                        name,
                        wavelength_ranges=bands,
                        limit=self.max_candidates,
                    )
                    or ()
                )
            except Exception as exc:
                report["requests"].append(
                    {
                        "request_id": request_id,
                        "proposed_material": name,
                        "candidates": [],
                        "status": "ranking_failed",
                        "error": _first_line(exc),
                    }
                )
                report["status"] = "failed"
                report.setdefault("errors", []).append(
                    f"{request_id}: candidate ranking failed: {_first_line(exc)}"
                )
                continue
            candidates = [_plain(candidate) for candidate in candidates[: self.max_candidates]]
            compatible = [candidate for candidate in candidates if self._candidate_is_compatible(candidate)]
            report["requests"].append(
                {
                    "request_id": request_id,
                    "proposed_material": name,
                    "target_wavelength_ranges_um": bands,
                    "candidates": candidates,
                    "compatible_candidate_count": len(compatible),
                    "status": "ranked" if candidates else "no_candidates",
                }
            )

        client = selector_client if selector_client is not None else self.selector_client
        payload: Dict[str, Any] = {
            "schema_version": MATERIAL_SELECTOR_SCHEMA_VERSION,
            "user_question": str(user_question or ""),
            "target_wavelength_ranges_um": bands,
            "selection_rule": (
                "Candidates are pre-ranked by three equal physical metrics: target-band "
                "coverage, n/k completeness, and sampling density. Rank 1 is the most "
                "physically reliable candidate. Use the highest-ranked candidate that "
                "also satisfies the user's exact material formula, state and source "
                "constraints. Never replace SiO with SiO2 or another stoichiometry."
            ),
            # Copy before validation adds selected/fallback annotations to the
            # report; the audit payload must remain the exact prompt sent to
            # the internal model.
            "requests": _plain(report["requests"]),
        }
        report["selector_payload"] = payload
        selector_user_content = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        selector_messages = [
            {"role": "system", "content": _MATERIAL_SELECTOR_SYSTEM_PROMPT},
            {"role": "user", "content": selector_user_content},
        ]
        report["selector_prompt"] = {
            "system": _MATERIAL_SELECTOR_SYSTEM_PROMPT,
            "user": selector_user_content,
            "sha256": hashlib.sha256(
                json.dumps(
                    selector_messages,
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
        }

        raw_content = ""
        response_usage: Dict[str, Any] = {}
        if client is not None and callable(getattr(client, "call", None)):
            report["call_count"] = 1
            report["selector_model"] = str(getattr(client, "model_name", "unknown"))
            try:
                response = client.call(
                    selector_messages,
                    max_tokens=MATERIAL_SELECTOR_MAX_TOKENS,
                    force_mock=force_mock,
                )
                raw_content = str(response.get("content") or "")
                response_usage = _plain(response.get("_llm_usage") or {})
                if response_usage:
                    self._selector_usage.append(dict(response_usage))
            except Exception as exc:
                report["selector_error"] = _first_line(exc)
        else:
            report["selector_model"] = None

        report["selector_response"] = {
            "raw_content": raw_content,
            "sha256": hashlib.sha256(raw_content.encode("utf-8")).hexdigest(),
            "usage": response_usage,
        }
        parsed = self._safe_selector_json(raw_content)
        selections = parsed.get("selections")
        selection_by_id = {
            str(item.get("request_id") or ""): item
            for item in selections
            if isinstance(item, Mapping) and str(item.get("request_id") or "").strip()
        } if isinstance(selections, list) else {}

        selected_materials: List[str] = []
        used_fallback = False
        for row in report["requests"]:
            request_id = str(row["request_id"])
            candidates = list(row.get("candidates") or ())
            compatible = [candidate for candidate in candidates if self._candidate_is_compatible(candidate)]
            chosen: Mapping[str, Any] | None = None
            selection = selection_by_id.get(request_id)
            if selection is not None:
                try:
                    selected_rank = int(selection.get("selected_rank"))
                except (TypeError, ValueError):
                    selected_rank = 0
                if 1 <= selected_rank <= len(candidates):
                    candidate = candidates[selected_rank - 1]
                    provider_ok = not selection.get("provider") or str(selection.get("provider")).casefold() == str(candidate.get("provider") or "").casefold()
                    dataset_ok = selection.get("dataset_id") is None or str(selection.get("dataset_id")) == str(candidate.get("dataset_id"))
                    if provider_ok and dataset_ok and self._candidate_is_compatible(candidate):
                        chosen = candidate
                        row["llm_selection"] = {
                            "selected_rank": selected_rank,
                            "provider": candidate.get("provider"),
                            "dataset_id": candidate.get("dataset_id"),
                            "reason": str(selection.get("reason") or "").strip()[:600],
                        }
                if chosen is None:
                    row["selector_rejection"] = {
                        "selected_rank": selection.get("selected_rank"),
                        "provider": selection.get("provider"),
                        "dataset_id": selection.get("dataset_id"),
                        "reason": (
                            "selection did not identify a supplied compatible dataset; "
                            "formula/state/source hard constraints were enforced"
                        ),
                    }
            if chosen is None:
                if not compatible:
                    report["status"] = "failed"
                    report.setdefault("errors", []).append(
                        f"{request_id}: selector did not return a compatible candidate"
                    )
                    return [], report
                chosen = compatible[0]
                used_fallback = True
                row["fallback_selection"] = {
                    "selected_rank": int(chosen.get("rank") or candidates.index(chosen) + 1),
                    "provider": chosen.get("provider"),
                    "dataset_id": chosen.get("dataset_id"),
                    "reason": "internal selector response was unavailable or invalid; selected the highest-ranked compatible candidate",
                }
            row["selected_candidate"] = _plain(chosen)
            row["selected_material"] = self._candidate_display_name(chosen, row["proposed_material"])
            selected_materials.append(str(row["selected_material"]))

        if report.get("status") == "failed":
            return [], report
        if client is None or report.get("selector_error") or not parsed:
            report["status"] = "deterministic_fallback"
        elif used_fallback:
            report["status"] = "deterministic_fallback"
        else:
            report["status"] = "selected_by_llm"
        return selected_materials, report

    @staticmethod
    def rewrite_execution_materials(
        text: Any,
        proposals: Sequence[Any],
        selected: Sequence[Any],
    ) -> str:
        """Replace exact proposed tokens without touching formula prefixes."""

        result = str(text or "")
        pairs = [
            (str(old).strip(), str(new).strip())
            for old, new in zip(proposals, selected)
            if str(old).strip() and str(new).strip()
        ]
        for old, new in sorted(pairs, key=lambda item: len(item[0]), reverse=True):
            if old.casefold() == new.casefold():
                continue
            result = re.sub(
                rf"(?<![A-Za-z0-9]){re.escape(old)}(?![A-Za-z0-9])",
                lambda match, replacement=new: replacement,
                result,
                flags=re.IGNORECASE,
            )
        return result

    # -- reporting -------------------------------------------------------

    def prompt_payload(self) -> Dict[str, Any]:
        """What the planning stage shows the model. Names, no wavelengths."""

        return {
            "schema_version": MATERIAL_CATALOG_SCHEMA_VERSION,
            "guaranteed_names": list(self._names),
            "how_to_use": (
                "copy a name exactly as spelled here into proposed_materials "
                "and into execution_request_english"
            ),
            "other_names_allowed": (
                "a name outside this list is permitted, but it is resolved "
                "locally before the route is accepted and the route is sent "
                "back for repair if the registry cannot resolve it to exactly "
                "one dataset"
            ),
            "measured_not_tunable": MEASURED_NOT_TUNABLE,
            "coverage": COVERAGE_DEFERRAL,
        }

    def provenance(self) -> Dict[str, Any]:
        """Where the list came from, for the artifact."""

        payload: Dict[str, Any] = {
            "schema_version": MATERIAL_CATALOG_SCHEMA_VERSION,
            "guaranteed_names": list(self._names),
            "coverage": COVERAGE_DEFERRAL,
            "source": "tmm_engine.MaterialRegistry local provider, each name resolved before listing",
            "candidate_ranking": {
                "schema_version": "material-candidate-ranking.v1",
                "max_candidates": 10,
                "equal_weight_metrics": [
                    "target_band_coverage",
                    "n_k_completeness",
                    "sampling_density",
                ],
            },
            "internal_selector": {
                "schema_version": MATERIAL_SELECTOR_SCHEMA_VERSION,
                "enabled": self.selector_client is not None,
                "model": (
                    str(getattr(self.selector_client, "model_name", "unknown"))
                    if self.selector_client is not None
                    else None
                ),
                "max_tokens": MATERIAL_SELECTOR_MAX_TOKENS,
            },
        }
        status = getattr(self.registry, "catalog_status", None)
        if callable(status):
            try:
                payload["catalog_status"] = _plain(status())
            except Exception as exc:  # provenance must not break planning
                payload["catalog_status_error"] = _first_line(exc)
        return payload


def _candidate_dataset(candidate: Any) -> str:
    """``provider:id`` for a search hit or a resolved reference alike.

    A search hit carries the fields both on itself and on its nested ``ref``,
    so both shapes are read rather than assuming either one.
    """

    for holder in (candidate, getattr(candidate, "ref", None)):
        if holder is None:
            continue
        provider = _text_or_none(getattr(holder, "provider", None))
        dataset_id = getattr(holder, "dataset_id", None)
        if provider and dataset_id is not None:
            return f"{provider}:{dataset_id}"
    return ""


def _candidate_page(candidate: Any) -> str:
    for holder in (candidate, getattr(candidate, "ref", None)):
        if holder is None:
            continue
        page = _text_or_none(getattr(holder, "page", None))
        if page:
            return page
    return ""


def _text_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dataset_source(reference: Any) -> Optional[str]:
    """Where the numbers physically live.

    A local dataset is a file on this machine, so the path is the honest
    answer.  A remote page has no local file, so the library's own shelf /
    book / page coordinates are recorded instead -- that triple is what a
    reader would need to look the measurement up.
    """

    filepath = _text_or_none(getattr(reference, "filepath", None))
    if filepath:
        return filepath
    parts = [
        _text_or_none(getattr(reference, attr, None))
        for attr in ("shelf", "book", "page")
    ]
    kept = [part for part in parts if part]
    return "/".join(kept) if kept else None


def _dataset_coverage(reference: Any) -> Optional[Tuple[float, float]]:
    """The dataset's own wavelength span, in micrometres, or None.

    Recorded for the artifact only.  Read together with the requested band it
    says whether the run needed data the dataset never had, which is the
    question the deferred interpolation step exists to answer.
    """

    low = getattr(reference, "range_min", None)
    high = getattr(reference, "range_max", None)
    try:
        span = (float(low), float(high))
    except (TypeError, ValueError):
        return None
    return span if span[0] <= span[1] else (span[1], span[0])


def _failure_code(exc: BaseException) -> str:
    name = type(exc).__name__
    if "Ambiguity" in name or "Ambiguous" in name:
        return "ambiguous"
    if "NotFound" in name:
        return "not_found"
    if "Range" in name:
        return "range"
    return "error"


def _ambiguity_choices(exc: BaseException) -> Tuple[str, ...]:
    """Dataset identifiers named by an ambiguity error, in the order given.

    The exception text is preferred over the candidate objects it carries: the
    engine already renders each dataset as a short ``provider:id`` there, while
    the objects stringify to full reprs that would swamp the repair message.
    """

    text = _first_line(exc)
    _, _, tail = text.partition("ambiguous among:")
    if tail.strip():
        return tuple(part.strip() for part in tail.split(",") if part.strip())
    for attribute in ("choices", "candidates", "datasets"):
        raw = getattr(exc, attribute, None)
        if isinstance(raw, (list, tuple)) and raw:
            return tuple(_candidate_label(item) for item in raw)
    return ()


def _candidate_label(candidate: Any) -> str:
    """A short ``provider:id`` for one candidate, falling back to its text."""

    reference = getattr(candidate, "ref", candidate)
    provider = getattr(candidate, "provider", None) or getattr(
        reference, "provider", None
    )
    identifier = None
    for attribute in ("dataset_id", "pageid", "page", "name"):
        identifier = getattr(candidate, attribute, None) or getattr(
            reference, attribute, None
        )
        if identifier:
            break
    if provider and identifier:
        return f"{provider}:{identifier}"
    return " ".join(str(candidate).split())[:80]


def _first_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:400]


def _plain(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _plain(model_dump(mode="python"))
        except TypeError:
            return _plain(model_dump())
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
