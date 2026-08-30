"""Literature-driven route planning: decide what to try, before anything runs.

A run's routes are the strategy axes it will explore in parallel -- one route
tunes thicknesses, another swaps the material pair, a third changes the
topology -- and each route then iterates only along its own axis.  Which axes
exist therefore decides the ceiling of the whole study: an axis nobody proposed
cannot be reached by any amount of iteration inside the axes that were.

Before this module existed the portfolio width was a configuration number.
Three routes were requested because ``maximum_initial_routes`` said three, and
the plan was cut to that length regardless of how many distinct axes the
problem actually had.  A problem with two real axes got a padded third, and a
problem with five got two of them silently discarded.

Here the count is the model's answer, bounded but not fixed.  The stage runs in
three steps:

1. A model turns the user's request into English literature queries.  This step
   degrades to a locally derived query rather than failing, because a request
   written in Chinese leaves no usable search terms on its own and an empty
   query returns nothing.
2. Those queries go to Semantic Scholar and the results are normalised,
   de-duplicated, ranked and cut to a bounded number of papers -- bounded
   because everything retained is spent from the planning model's context
   window, so an unbounded harvest would push the user's own request out of it.
3. A model reads the request together with those papers and proposes one to
   five routes, each stating explicitly what it tunes.  Fewer than one is a
   regeneration; more than five is truncated.  A route that does not say what
   it tunes is rejected, because a route without a declared axis cannot be kept
   from wandering into another route's axis and the two stop being comparable.

Literature is motivation, never proof.  A harvest that comes back empty is a
recorded condition and not a failure: the stage proceeds and the routes are
identified as theory-based, which is the same rule the strategy planner has
always followed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Protocol, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, Field

from config.qwen_config import get_cost_tracker

from .material_catalog import MEASURED_NOT_TUNABLE, RouteMaterialCatalog
from .strategy_planner import DesignRoute
from .text_safety import normalize_text_sequence_list


_PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts" / "optical_harness"
DEFAULT_SEARCH_QUERY_PROMPT = _PROMPT_ROOT / "TMM Route Search Query.txt"
DEFAULT_ROUTE_PLANNING_PROMPT = _PROMPT_ROOT / "TMM Literature Route Planning.txt"
DEFAULT_CONTROL_ROUTE_PROMPT = _PROMPT_ROOT / "TMM Control Route Planning.txt"

ROUTE_PLANNING_SCHEMA_VERSION = "tmm-route-planning.v1"

# Both stages read a research request and answer with structure, which is the
# plus tier's job in this harness; the label is recorded in the artifact so a
# reader can tell which tier produced a plan.
ROUTE_PLANNING_MODEL = "qwen3.5-plus"

# A five-route plan carries five execution requests and five sets of
# pre-declarations, which is several times the length of a scoring answer.
ROUTE_PLANNING_MAX_TOKENS = 12000
SEARCH_QUERY_MAX_TOKENS = 1500

# The user's stated starting point.  Adjustable, and deliberately checked
# against a character budget below rather than trusted on its own: forty short
# abstracts and forty long ones differ by an order of magnitude in context.
DEFAULT_LITERATURE_LIMIT = 40

# One to five routes, the model choosing where in that range to land.  The
# lower bound exists because a study needs at least one axis; the upper bound
# because each route costs its own iteration budget for the whole run.
DEFAULT_MINIMUM_ROUTES = 1
DEFAULT_MAXIMUM_ROUTES = 5

DEFAULT_MAXIMUM_ATTEMPTS = 3

# More than a handful of queries multiplies provider calls without widening
# coverage much, since the results overlap heavily.
DEFAULT_MAXIMUM_QUERIES = 3

# Per-paper and total limits on what reaches the planning prompt.  The total is
# the real guard: it holds regardless of how many papers came back or how long
# each abstract is.
DEFAULT_SUMMARY_CHARACTERS = 700
DEFAULT_LITERATURE_CHARACTER_BUDGET = 48000

# The material vocabulary handed to the planning stage.  Unlike the literature
# client this is a local, free, deterministic lookup, so it is built by default
# instead of being injected by the caller: a planner that silently skipped it
# would propose names the engine cannot resolve and burn a route's whole round
# quota discovering that.  Passing ``material_catalog=None`` disables the
# check, which is what keeps the offline tests free of an engine import.
AUTO_MATERIAL_CATALOG = "auto"

# The control arm's identifiers.  Fixed rather than derived so the tournament,
# the summary and the report all name the same route without agreeing on a
# convention out of band, and so a reader scanning artifacts can find it by
# grep.  The source marker is what every downstream comparison splits on.
CONTROL_ROUTE_ID = "control_route_01"
CONTROL_ROUTE_SOURCE = "llm_memory_control"
CONTROL_PLANNING_SCHEMA_VERSION = "tmm-control-route-planning.v1"
CONTROL_ROUTE_PLANNING_ARTIFACT = "CONTROL_ROUTE_PLANNING.json"
# Stated in the artifact rather than left implicit: an empty literature block
# reads identically whether the search returned nothing or was never run, and
# those are opposite facts about this route.
CONTROL_NO_LITERATURE_DISCLOSURE = (
    "This route was planned from the model's own prior knowledge alone. No "
    "Semantic Scholar query was issued, no retrieved paper reached the prompt, "
    "and no method-research report was supplied -- neither for the initial plan "
    "nor for any continuation round. It is the control arm of a controlled "
    "comparison against the literature-grounded routes, and its evidence-id "
    "allowlist is empty by construction."
)


class RoutePlanningClient(Protocol):
    """The harness LLM adapter protocol, as the other planners use it."""

    def call(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        max_tokens: int = 4000,
        force_mock: bool | None = None,
    ) -> Mapping[str, Any]:
        ...


class RouteLiteratureClient(Protocol):
    """Paper-level literature search.

    Route planning wants breadth of papers, whereas method research wants depth
    inside a few of them, so this stage does not reuse the method-research
    client's snippet path -- it asks for titles and abstracts directly.  A
    client exposing ``search_s2`` instead is also accepted, so an existing
    online client can be injected without an adapter.
    """

    def search_papers(self, query: str, *, limit: int) -> Any:
        ...


class LiteraturePaper(BaseModel):
    """One harvested paper, trimmed to what a planning model can use."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    paper_id: str = ""
    doi: str = ""
    title: str
    authors: Tuple[str, ...] = ()
    year: int | None = None
    venue: str = ""
    citation_count: int = 0
    summary: str = ""
    queries: Tuple[str, ...] = ()

    def prompt_row(self) -> Dict[str, Any]:
        """The form the planning prompt sees: label first, so it can be cited."""

        row: Dict[str, Any] = {"label": self.label, "title": self.title}
        if self.year is not None:
            row["year"] = self.year
        if self.venue:
            row["venue"] = self.venue
        if self.citation_count:
            row["citation_count"] = self.citation_count
        if self.summary:
            row["summary"] = self.summary
        return row


class DefaultRouteLiteratureClient:
    """Paper-level Semantic Scholar search over the repository's own gateway.

    Method research reaches Semantic Scholar through its snippet endpoint,
    which returns passages drawn from a handful of papers -- the right shape
    for method guidance, and the wrong one here.  Route planning needs breadth:
    it is deciding which axes exist, so it wants to see many papers' titles and
    abstracts rather than a few papers' methods sections.  This client calls
    the paper-search endpoint directly, so a limit of forty means forty papers.

    The gateway is built lazily and can be injected, which is what keeps the
    tests for this stage off the network.
    """

    def __init__(
        self,
        *,
        s2_gateway: Any | None = None,
        request_budget_seconds: float = 75.0,
    ) -> None:
        if s2_gateway is None:
            from optomind_research.s2_intelligence_gateway import (
                S2IntelligenceGateway,
                S2Transport,
            )
            from tools.academic_backends.semantic_scholar_backend import _api_keys

            from .method_research import _resolve_shared_s2_key_pool

            keys = list(_api_keys() or ()) or _resolve_shared_s2_key_pool()
            budget = max(5.0, float(request_budget_seconds))
            s2_gateway = S2IntelligenceGateway(
                transport=S2Transport(
                    keys=keys,
                    timeout_seconds=min(30.0, budget),
                    max_attempts=4,
                    max_elapsed_seconds=budget,
                )
            )
        self.s2_gateway = s2_gateway

    def search_papers(self, query: str, *, limit: int) -> Dict[str, Any]:
        records, response = self.s2_gateway.search_papers(query, limit=limit)
        return {
            "records": list(records or ()),
            "status": str(_field(response, "status_category", "") or ""),
            "error": str(_field(response, "error", "") or ""),
        }


class LiteratureHarvest(BaseModel):
    """What the search step retained, and what it dropped on the way."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = "empty"
    papers: Tuple[LiteraturePaper, ...] = ()
    queries: Tuple[str, ...] = ()
    requested_limit: int = DEFAULT_LITERATURE_LIMIT
    returned: int = 0
    duplicates_dropped: int = 0
    dropped_for_context: int = 0
    character_count: int = 0
    errors: Tuple[str, ...] = ()

    @property
    def allowed_labels(self) -> Tuple[str, ...]:
        return tuple(paper.label for paper in self.papers)


class SearchQueryResult(BaseModel):
    """The queries this run will search with, and how they were obtained."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = "unavailable"
    queries: Tuple[str, ...] = ()
    rationale: str = ""
    attempts: int = 0
    validation_errors: Tuple[str, ...] = ()
    usage: Tuple[Dict[str, Any], ...] = ()


class RoutePlanResult(BaseModel):
    """The planned portfolio plus every step's provenance."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    plan: Dict[str, Any] | None = None
    pre_declarations: Dict[str, Dict[str, List[str]]] = Field(default_factory=dict)
    attempts: int = 0
    route_count: int = 0
    warnings: Tuple[str, ...] = ()
    validation_errors: Tuple[str, ...] = ()
    planning_usage: Tuple[Dict[str, Any], ...] = ()
    query_result: SearchQueryResult = Field(default_factory=SearchQueryResult)
    literature: LiteratureHarvest = Field(default_factory=LiteratureHarvest)
    model_name: str = ROUTE_PLANNING_MODEL
    question_digest: str = ""
    material_verification: Dict[str, Any] = Field(default_factory=dict)

    @property
    def usage(self) -> Tuple[Dict[str, Any], ...]:
        """Both stages' usage, so a caller meters the stage in one read.

        Derived rather than stored for the same reason the scoring standard
        derives it: a stored copy drifts from the two lists it summarises.
        Being a property, it is absent from ``model_dump``; a caller that
        meters from the serialised form would silently record nothing, so the
        sidecar below writes the rows explicitly.
        """

        return tuple(self.query_result.usage) + tuple(self.planning_usage)

    def sidecar(self) -> Dict[str, Any]:
        """The artifact: enough to re-derive the plan's justification."""

        return {
            "schema_version": ROUTE_PLANNING_SCHEMA_VERSION,
            "status": self.status,
            "model_name": self.model_name,
            "question_digest": self.question_digest,
            "route_count": self.route_count,
            "attempts": self.attempts,
            "warnings": list(self.warnings),
            "validation_errors": list(self.validation_errors),
            "queries": {
                "status": self.query_result.status,
                "queries": list(self.query_result.queries),
                "rationale": self.query_result.rationale,
                "attempts": self.query_result.attempts,
                "validation_errors": list(self.query_result.validation_errors),
                "usage": [dict(row) for row in self.query_result.usage],
            },
            "literature": {
                "status": self.literature.status,
                "requested_limit": self.literature.requested_limit,
                "returned": self.literature.returned,
                "duplicates_dropped": self.literature.duplicates_dropped,
                "dropped_for_context": self.literature.dropped_for_context,
                "character_count": self.literature.character_count,
                "errors": list(self.literature.errors),
                "papers": [
                    paper.model_dump(mode="json") for paper in self.literature.papers
                ],
            },
            "planning_usage": [dict(row) for row in self.planning_usage],
            "material_verification": self.material_verification,
            "plan": self.plan,
            "pre_declarations": {
                route_id: {key: list(value) for key, value in declarations.items()}
                for route_id, declarations in self.pre_declarations.items()
            },
        }


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


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
    """Identical to the scoring standard's digest, so artifacts cross-reference."""

    return hashlib.sha256(" ".join(str(question).split()).encode("utf-8")).hexdigest()[:16]


def _compact_control_iterations(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep control-arm feedback useful without importing research evidence.

    A control continuation needs to see what its own earlier experiment did,
    but it must not receive the method-research payload that the literature
    routes use.  The compact record is deliberately limited to measurements,
    failures and the candidate geometries produced by this same route.
    """

    compact: list[dict[str, Any]] = []
    for raw in list(rows)[-6:]:
        item = dict(raw)
        compact.append(
            {
                "iteration_id": item.get("iteration_id"),
                "compilation_status": item.get("compilation_status"),
                "compilation_rationale": item.get("compilation_rationale"),
                "compilation_errors": list(item.get("compilation_errors") or [])[:6],
                "run_status": item.get("run_status"),
                "physically_valid_candidate_count": item.get(
                    "physically_valid_candidate_count", 0
                ),
                "best_target_score": item.get("best_target_score"),
                "best_robustness_score": item.get("best_robustness_score"),
                "failure_categories": list(item.get("failure_categories") or [])[:8],
                "candidate_summaries": [
                    {
                        key: candidate.get(key)
                        for key in (
                            "candidate_id",
                            "target_score",
                            "robustness_score",
                            "simplicity_score",
                            "thicknesses_nm",
                            "optimizer_id",
                        )
                    }
                    for candidate in item.get("candidate_summaries", []) or []
                    if isinstance(candidate, Mapping)
                ][:4],
            }
        )
    return compact


def _safe_error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}".replace("\n", " ").strip()[:300]


def _field(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, Mapping):
        return record.get(name, default)
    if hasattr(record, name):
        return getattr(record, name)
    if hasattr(record, "model_dump"):
        try:
            dumped = record.model_dump(mode="python")
            if isinstance(dumped, Mapping):
                return dumped.get(name, default)
        except Exception:
            pass
    return default


def _first_field(record: Any, names: Sequence[str], default: Any = None) -> Any:
    for name in names:
        value = _field(record, name, None)
        if value not in (None, "", [], {}):
            return value
    return default


def _clean_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if limit > 0 and len(text) > limit:
        # Cut on a word boundary when one is near the limit, so the trimmed
        # summary does not end mid-token and read as a different word.
        head = text[:limit]
        pivot = head.rfind(" ")
        head = head[:pivot] if pivot > limit * 0.6 else head
        return head.rstrip(" ,;:") + " ..."
    return text


def _records_of(result: Any) -> Sequence[Any]:
    """Accept a records sequence, a ``(records, response)`` pair, or a result."""

    if result is None:
        return ()
    records = _field(result, "records", None)
    if records is not None:
        return list(records)
    if isinstance(result, tuple) and result:
        first = result[0]
        return list(first) if isinstance(first, (list, tuple)) else ()
    if isinstance(result, (list, tuple)):
        return list(result)
    return ()


_DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def _normalize_doi(value: Any) -> str:
    return _DOI_PREFIX.sub("", str(value or "").strip()).strip().casefold()


def _identity(paper_id: str, doi: str, title: str) -> str:
    """De-duplication key, most reliable identifier first.

    Titles are the last resort because two records of the same paper can carry
    different identifier sets from different providers while their titles
    agree, and because a title match across providers is the case this
    de-duplication exists for.
    """

    if doi:
        return f"doi:{doi}"
    if paper_id:
        return f"id:{paper_id.casefold()}"
    return "title:" + re.sub(r"[^a-z0-9]+", "", title.casefold())


_QUERY_UNIT = r"(?:nm|um|µm|mm|THz|GHz|eV|K)"
# Ranges come first, because a request states its band as one ("300-800nm") and
# matching the single-number branch there would keep the upper bound and drop
# the lower one, which is half the band the user asked about.
_QUERY_TOKEN = re.compile(
    rf"\d+(?:\.\d+)?\s*(?:-|–|~|to)\s*\d+(?:\.\d+)?\s*{_QUERY_UNIT}\b"
    rf"|\d+(?:\.\d+)?\s*{_QUERY_UNIT}\b"
    r"|[A-Za-z][A-Za-z0-9\-+/]{1,}"
)

# Attached to a locally derived query so it still reaches the right corpus when
# the request itself contributed no usable Latin terms.
_QUERY_ANCHOR = "optical multilayer thin-film coating design transfer matrix"


def _fallback_queries(question: str) -> Tuple[str, ...]:
    """Derive one query locally, for when the query model is unavailable.

    A request written entirely in Chinese leaves no Latin search terms, and an
    empty query returns nothing, so the anchor is always appended rather than
    used only as a last resort.
    """

    tokens: List[str] = []
    for match in _QUERY_TOKEN.findall(str(question or "")):
        token = " ".join(match.split())
        if len(token) <= 2 and not token[0].isdigit():
            continue
        if token.casefold() not in {item.casefold() for item in tokens}:
            tokens.append(token)
        if len(tokens) >= 12:
            break
    return (" ".join([*tokens, _QUERY_ANCHOR]).strip(),)


def _axis_signature(route: Mapping[str, Any]) -> str:
    """What this route says it tunes, normalised for comparison.

    Two routes that tune the same variables over the same materials and the
    same topology are one route described twice; keeping both would spend two
    iteration budgets to explore one axis.
    """

    def _normal(values: Any) -> Tuple[str, ...]:
        items = values if isinstance(values, (list, tuple)) else [values]
        return tuple(
            sorted(
                {
                    re.sub(r"[^a-z0-9]+", " ", str(item).casefold()).strip()
                    for item in items
                    if str(item).strip()
                }
            )
        )

    return json.dumps(
        {
            "variables": _normal(route.get("design_variables")),
            "materials": _normal(route.get("proposed_materials")),
            "kind": str(route.get("route_kind") or "").casefold(),
            "topology": re.sub(
                r"[^a-z0-9]+", " ", str(route.get("proposed_topology") or "").casefold()
            ).strip(),
        },
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------


class QwenLiteratureRoutePlanner:
    """Turn a request plus retrieved literature into one to five routes."""

    def __init__(
        self,
        client: RoutePlanningClient,
        *,
        literature_client: RouteLiteratureClient | Any | None = None,
        query_prompt_path: Path | str = DEFAULT_SEARCH_QUERY_PROMPT,
        planning_prompt_path: Path | str = DEFAULT_ROUTE_PLANNING_PROMPT,
        literature_limit: int = DEFAULT_LITERATURE_LIMIT,
        minimum_routes: int = DEFAULT_MINIMUM_ROUTES,
        maximum_routes: int = DEFAULT_MAXIMUM_ROUTES,
        maximum_attempts: int = DEFAULT_MAXIMUM_ATTEMPTS,
        maximum_queries: int = DEFAULT_MAXIMUM_QUERIES,
        summary_characters: int = DEFAULT_SUMMARY_CHARACTERS,
        character_budget: int = DEFAULT_LITERATURE_CHARACTER_BUDGET,
        material_catalog: Any = AUTO_MATERIAL_CATALOG,
    ) -> None:
        self.client = client
        self.literature_client = literature_client
        self.query_prompt_path = Path(query_prompt_path)
        self.planning_prompt_path = Path(planning_prompt_path)
        self.literature_limit = max(1, int(literature_limit))
        self.maximum_routes = max(1, int(maximum_routes))
        self.minimum_routes = max(1, min(int(minimum_routes), self.maximum_routes))
        self.maximum_attempts = max(1, int(maximum_attempts))
        self.maximum_queries = max(1, int(maximum_queries))
        self.summary_characters = max(0, int(summary_characters))
        self.character_budget = max(1000, int(character_budget))
        self.material_catalog = material_catalog
        self._material_catalog_error: str | None = None
        self._model_label = str(getattr(client, "model_name", ROUTE_PLANNING_MODEL))

    def _resolved_material_catalog(self) -> Any:
        """The material vocabulary, built once on first use.

        A registry that cannot be reached is recorded rather than retried: the
        stage still plans, but the artifact then says the material check did
        not run instead of implying every name was verified.
        """

        catalog = self.material_catalog
        if catalog is not AUTO_MATERIAL_CATALOG:
            return catalog
        try:
            # The route planner remains the outer model call.  The same client
            # is injected only as the internal material-dataset selector after
            # a route has been proposed, so the outer prompt/contract stays
            # unchanged while the chosen dataset is auditable.
            catalog = RouteMaterialCatalog(selector_client=self.client)
        except Exception as exc:
            self._material_catalog_error = _safe_error(exc)
            catalog = None
        self.material_catalog = catalog
        return catalog

    # -- step 1: queries -------------------------------------------------

    def derive_queries(
        self,
        question: str,
        *,
        problem_analysis: Any = None,
        force_mock: bool | None = None,
    ) -> SearchQueryResult:
        """Ask for English literature queries; fall back locally on failure."""

        payload: Dict[str, Any] = {
            "user_question": str(question or "").strip(),
            "fixed_rules": {
                "maximum_queries": self.maximum_queries,
                "language": "English only; the corpus is English",
                "results_per_query": self.literature_limit,
                "model": self._model_label,
            },
        }
        if problem_analysis is not None:
            payload["problem_analysis"] = _as_plain(problem_analysis)

        usages: List[Dict[str, Any]] = []
        try:
            system_prompt = self.query_prompt_path.read_text(encoding="utf-8")
            response = self.client.call(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                max_tokens=SEARCH_QUERY_MAX_TOKENS,
                force_mock=force_mock,
            )
        except Exception as exc:
            return SearchQueryResult(
                status="fallback",
                queries=_fallback_queries(question),
                rationale="derived locally because the query model was unavailable",
                attempts=1,
                validation_errors=(_safe_error(exc),),
                usage=tuple(usages),
            )
        _record_usage(response, usages)
        raw = _safe_json(str(response.get("content") or ""))
        proposed = raw.get("queries")
        queries: List[str] = []
        for item in proposed if isinstance(proposed, list) else []:
            text = " ".join(str(item or "").split())
            if len(text) < 3:
                continue
            if text.casefold() not in {existing.casefold() for existing in queries}:
                queries.append(text)
            if len(queries) >= self.maximum_queries:
                break
        if not queries:
            return SearchQueryResult(
                status="fallback",
                queries=_fallback_queries(question),
                rationale="derived locally because the model returned no usable query",
                attempts=1,
                validation_errors=("the response carried no non-empty 'queries' array",),
                usage=tuple(usages),
            )
        return SearchQueryResult(
            status="derived",
            queries=tuple(queries),
            rationale=str(raw.get("rationale") or "").strip(),
            attempts=1,
            usage=tuple(usages),
        )

    # -- step 2: literature ----------------------------------------------

    def harvest_literature(
        self, queries: Sequence[str], *, limit: int | None = None
    ) -> LiteratureHarvest:
        """Search each query, then merge into one bounded ranked list."""

        wanted = max(1, int(limit if limit is not None else self.literature_limit))
        queries = tuple(dict.fromkeys(str(item).strip() for item in queries if str(item).strip()))
        if not queries:
            return LiteratureHarvest(
                status="empty",
                requested_limit=wanted,
                errors=("no search query was available",),
            )
        search = self._search_callable()
        if search is None:
            return LiteratureHarvest(
                status="unavailable",
                queries=queries,
                requested_limit=wanted,
                errors=(
                    "no literature client was supplied; route planning proceeded "
                    "from theory alone",
                ),
            )

        errors: List[str] = []
        per_query: List[List[Any]] = []
        for query in queries:
            try:
                result = search(query, limit=wanted)
            except Exception as exc:
                errors.append(f"{query}: {_safe_error(exc)}")
                per_query.append([])
                continue
            provider_error = str(_field(result, "error", "") or "").strip()
            if provider_error:
                errors.append(f"{query}: {provider_error}")
            per_query.append(list(_records_of(result)))

        # Round-robin over the queries rather than concatenating them: a
        # concatenation spends the whole budget on the first query and a second
        # query that found the one relevant paper never reaches the model.
        merged: List[LiteraturePaper] = []
        seen: Dict[str, int] = {}
        duplicates = 0
        depth = max((len(records) for records in per_query), default=0)
        for rank in range(depth):
            for query, records in zip(queries, per_query):
                if rank >= len(records):
                    continue
                paper = self._normalize(records[rank], query=query, label_index=len(merged) + 1)
                if paper is None:
                    continue
                key = _identity(paper.paper_id, paper.doi, paper.title)
                if key in seen:
                    duplicates += 1
                    index = seen[key]
                    existing = merged[index]
                    if query not in existing.queries:
                        merged[index] = existing.model_copy(
                            update={"queries": (*existing.queries, query)}
                        )
                    continue
                seen[key] = len(merged)
                merged.append(paper)

        kept: List[LiteraturePaper] = []
        characters = 0
        dropped_for_context = 0
        for paper in merged:
            if len(kept) >= wanted:
                dropped_for_context += 1
                continue
            size = len(json.dumps(paper.prompt_row(), ensure_ascii=False))
            if kept and characters + size > self.character_budget:
                # Keep at least one paper regardless, so a single very long
                # abstract does not turn a successful search into an empty one.
                dropped_for_context += 1
                continue
            characters += size
            kept.append(paper.model_copy(update={"label": f"L{len(kept) + 1:02d}"}))

        return LiteratureHarvest(
            status="harvested" if kept else ("unavailable" if errors else "empty"),
            papers=tuple(kept),
            queries=queries,
            requested_limit=wanted,
            returned=len(kept),
            duplicates_dropped=duplicates,
            dropped_for_context=dropped_for_context,
            character_count=characters,
            errors=tuple(dict.fromkeys(errors)),
        )

    def _search_callable(self) -> Any:
        client = self.literature_client
        if client is None:
            return None
        for name in ("search_papers", "search_s2"):
            candidate = getattr(client, name, None)
            if callable(candidate):
                return candidate
        return None

    def _normalize(
        self, record: Any, *, query: str, label_index: int
    ) -> LiteraturePaper | None:
        title = _clean_text(
            _first_field(record, ("title", "paper_title", "name")), 300
        )
        if not title:
            return None
        raw = _first_field(record, ("raw", "raw_metadata"), {})
        raw = raw if isinstance(raw, Mapping) else {}
        paper_id = str(
            _first_field(
                record,
                (
                    "paper_id",
                    "paperId",
                    "semantic_scholar_paper_id",
                    "s2_paper_id",
                    "openalex_id",
                    "source_id",
                    "id",
                ),
                _first_field(raw, ("paper_id", "paperId", "id"), ""),
            )
            or ""
        ).strip()
        summary = _clean_text(
            _first_field(
                record,
                ("tldr", "abstract", "abstract_or_snippet", "snippet_text", "snippet", "text"),
                _first_field(raw, ("tldr", "abstract", "snippet_text"), ""),
            ),
            self.summary_characters,
        )
        authors_raw = _first_field(record, ("authors", "author_names"), ()) or ()
        authors: List[str] = []
        for author in authors_raw if isinstance(authors_raw, (list, tuple)) else ():
            name = _clean_text(
                author if isinstance(author, str) else _first_field(author, ("name",), ""),
                120,
            )
            if name:
                authors.append(name)
            if len(authors) >= 4:
                break
        year_raw = _first_field(record, ("year", "publication_year"), None)
        try:
            year = int(year_raw) if year_raw not in (None, "") else None
        except (TypeError, ValueError):
            year = None
        try:
            citations = int(_first_field(record, ("citation_count", "citationCount"), 0) or 0)
        except (TypeError, ValueError):
            citations = 0
        return LiteraturePaper(
            label=f"L{label_index:02d}",
            paper_id=paper_id,
            doi=_normalize_doi(_first_field(record, ("doi", "DOI"), "")),
            title=title,
            authors=tuple(authors),
            year=year,
            venue=_clean_text(_first_field(record, ("venue", "journal"), ""), 160),
            citation_count=max(0, citations),
            summary=summary,
            queries=(query,),
        )

    # -- step 3: routes --------------------------------------------------

    def propose_routes(
        self,
        question: str,
        harvest: LiteratureHarvest,
        *,
        problem_analysis: Any = None,
        force_mock: bool | None = None,
    ) -> RoutePlanResult:
        """Ask for the portfolio; regenerate while nothing usable comes back."""

        digest = _question_digest(question)
        try:
            system_prompt = self.planning_prompt_path.read_text(encoding="utf-8")
        except Exception as exc:
            return RoutePlanResult(
                status="unavailable",
                validation_errors=(_safe_error(exc),),
                literature=harvest,
                model_name=self._model_label,
                question_digest=digest,
            )

        allowed = list(harvest.allowed_labels)
        catalog = self._resolved_material_catalog()
        base_payload: Dict[str, Any] = {
            "user_question": str(question or "").strip(),
            "literature": [paper.prompt_row() for paper in harvest.papers],
            "fixed_rules": {
                "minimum_routes": self.minimum_routes,
                "maximum_routes": self.maximum_routes,
                "evidence_ids_allowed": allowed,
                "literature_status": harvest.status,
                "every_route_must_declare": "design_variables",
                "verification": (
                    "a local check validates every route against the execution "
                    "contract and rejects any route that does not state what it "
                    "tunes; rejected routes are returned for regeneration"
                ),
                "model": self._model_label,
            },
        }
        warnings: List[str] = []
        if catalog is not None:
            base_payload["material_catalog"] = catalog.prompt_payload()
            base_payload["fixed_rules"]["materials_must_resolve"] = (
                "every material named in proposed_materials and in "
                "execution_request_english is resolved against material_catalog "
                "by a local check; a name the registry cannot resolve to exactly "
                "one dataset sends the route back for repair"
            )
            base_payload["fixed_rules"]["measured_not_tunable"] = MEASURED_NOT_TUNABLE
            if not catalog.names:
                warnings.append(
                    "the material catalogue exported no names; proposals are "
                    "still resolved, but no guaranteed list was offered"
                )
        else:
            warnings.append(
                "material_catalog_unavailable: proposed materials were not "
                "verified against the engine registry"
                + (
                    f" ({self._material_catalog_error})"
                    if self._material_catalog_error
                    else ""
                )
            )
        if problem_analysis is not None:
            base_payload["problem_analysis"] = _as_plain(problem_analysis)
        if harvest.status != "harvested":
            base_payload["fixed_rules"]["theory_only"] = (
                "no literature was retrieved; identify every route as "
                "theory-based and populate theory_basis instead of evidence_ids"
            )

        usages: List[Dict[str, Any]] = []
        errors: Tuple[str, ...] = ()
        materials: Dict[str, Any] = {}
        repaired: List[Dict[str, Any]] = []
        previous = ""
        for attempt in range(1, self.maximum_attempts + 1):
            payload = dict(base_payload)
            if attempt > 1:
                payload["rejected_plan"] = _safe_json(previous)
                payload["repair_request"] = {
                    "validation_errors": list(errors),
                    "instruction": (
                        "Repair only the listed defects and return the corrected "
                        "complete JSON object. Every route must state what it "
                        "tunes in design_variables, no two routes may tune the "
                        "same variables over the same materials, and every "
                        "material must be a name the local registry resolves."
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
                    max_tokens=ROUTE_PLANNING_MAX_TOKENS,
                    force_mock=force_mock,
                )
            except Exception as exc:
                return RoutePlanResult(
                    status="unavailable",
                    attempts=attempt,
                    warnings=tuple(warnings),
                    validation_errors=(_safe_error(exc),),
                    planning_usage=tuple(usages),
                    literature=harvest,
                    model_name=self._model_label,
                    question_digest=digest,
                )
            _record_usage(response, usages)
            previous = str(response.get("content") or "")
            raw = _safe_json(previous)
            routes, declarations, errors, attempt_warnings, materials = (
                self._verify_routes(
                    raw,
                    allowed_labels=allowed,
                    catalog=catalog,
                    user_question=question,
                    problem_analysis=problem_analysis,
                    force_mock=force_mock,
                )
            )
            if catalog is not None:
                drain_usage = getattr(catalog, "drain_selector_usage", None)
                if callable(drain_usage):
                    for row in drain_usage():
                        _record_usage({"_llm_usage": row}, usages)
            warnings.extend(attempt_warnings)
            # Kept across attempts: a repaired plan overwrites this attempt's
            # errors, and without this the artifact would show the clean final
            # names with no trace of the name that had to be repaired.
            for entry in materials.get("rejected") or ():
                repaired.append({"attempt": attempt, **entry})
            if len(routes) >= self.minimum_routes:
                plan = {
                    "problem_id": str(raw.get("problem_id") or "").strip()
                    or f"question-{digest}",
                    "planning_summary": _clean_text(raw.get("planning_summary"), 1200),
                    "routes": routes,
                    "research_influence": normalize_text_sequence_list(
                        raw.get("research_influence") or []
                    ),
                    "unresolved_decisions": normalize_text_sequence_list(
                        raw.get("unresolved_decisions") or []
                    ),
                    "stop_if_all_routes_fail": _clean_text(
                        raw.get("stop_if_all_routes_fail"), 600
                    ),
                }
                return RoutePlanResult(
                    status="planned",
                    plan=plan,
                    pre_declarations=declarations,
                    attempts=attempt,
                    route_count=len(routes),
                    warnings=tuple(dict.fromkeys(warnings)),
                    validation_errors=tuple(dict.fromkeys(errors)),
                    planning_usage=tuple(usages),
                    literature=harvest,
                    model_name=self._model_label,
                    question_digest=digest,
                    material_verification=self._material_report(
                        catalog, materials, repaired
                    ),
                )
        return RoutePlanResult(
            status="invalid",
            attempts=self.maximum_attempts,
            warnings=tuple(dict.fromkeys(warnings)),
            validation_errors=tuple(dict.fromkeys(errors))
            or ("the model produced no valid route",),
            planning_usage=tuple(usages),
            literature=harvest,
            model_name=self._model_label,
            question_digest=digest,
            material_verification=self._material_report(catalog, materials, repaired),
        )

    def _material_report(
        self,
        catalog: Any,
        verified: Mapping[str, Any],
        repaired: Sequence[Mapping[str, Any]] = (),
    ) -> Dict[str, Any]:
        """What the material check saw, for the artifact.

        ``checked`` is recorded explicitly so a reader can tell a run where
        every name resolved from a run where the check never ran, and
        ``repaired`` keeps the names that had to be sent back rather than
        showing only the clean ones that survived.
        """

        report: Dict[str, Any] = {"checked": catalog is not None}
        if catalog is not None:
            report["catalog"] = catalog.provenance()
        elif self._material_catalog_error:
            report["error"] = self._material_catalog_error
        report["routes"] = {
            key: value for key, value in (verified.get("routes") or {}).items()
        }
        report["repaired"] = [dict(entry) for entry in repaired]
        return report

    def _verify_routes(
        self,
        raw: Mapping[str, Any],
        *,
        allowed_labels: Sequence[str],
        catalog: Any = None,
        user_question: str = "",
        problem_analysis: Any = None,
        force_mock: bool | None = None,
    ) -> tuple[
        List[Dict[str, Any]],
        Dict[str, Dict[str, List[str]]],
        Tuple[str, ...],
        List[str],
        Dict[str, Any],
    ]:
        """Validate, de-duplicate, order and bound the proposed routes."""

        proposed = raw.get("routes")
        if not isinstance(proposed, list) or not proposed:
            return (
                [],
                {},
                (
                    "the response carries no 'routes' array; return at least one "
                    "route describing exactly what it tunes",
                ),
                [],
                {"routes": {}, "rejected": []},
            )

        allowed = {str(label).strip().casefold() for label in allowed_labels}
        errors: List[str] = []
        warnings: List[str] = []
        # Rejected names are collected separately from the accepted ones: a route
        # that fails the material check is dropped, so its verdict has nowhere to
        # live in the per-route report the surviving routes build.
        rejected_materials: List[Dict[str, Any]] = []
        accepted: List[
            tuple[int, int, Dict[str, Any], Dict[str, List[str]], Dict[str, Any]]
        ] = []
        signatures: Dict[str, str] = {}

        for index, item in enumerate(proposed):
            if not isinstance(item, Mapping):
                errors.append(f"route #{index + 1} is not an object")
                continue
            candidate = dict(item)
            stated_id = str(candidate.get("route_id") or f"route #{index + 1}")
            declarations = {
                "expected_observations": normalize_text_sequence_list(
                    candidate.pop("expected_observations", [])
                ),
                "stop_conditions": normalize_text_sequence_list(
                    candidate.pop("stop_conditions", [])
                ),
            }

            # The axis declaration is the one field this stage exists to
            # guarantee, so it is checked before the contract: a route without
            # it is not a strategy axis, whatever else it gets right.
            variables = candidate.get("design_variables")
            variables = (
                [variables] if isinstance(variables, str) else list(variables or ())
            )
            variables = [str(value).strip() for value in variables if str(value).strip()]
            if not variables:
                errors.append(
                    f"{stated_id} does not say what it tunes; every route must list "
                    "the variables it varies in design_variables"
                )
                continue
            candidate["design_variables"] = variables

            # Materials are checked here, before a round is spent on the route,
            # because the engine resolves a name and refuses to guess: an
            # ambiguous name is not a slightly worse design, it is a route that
            # cannot run at all.  Both places a name can reach the compiler are
            # covered -- the declared list, and the request text the compiler
            # actually reads -- so a route cannot pass by keeping an
            # unresolvable name out of the list it declares.
            material_report: Dict[str, Any] = {}
            if catalog is not None:
                raw_materials = candidate.get("proposed_materials") or ()
                proposed_materials = (
                    [raw_materials]
                    if isinstance(raw_materials, str)
                    else list(raw_materials)
                )
                unique_materials = list(
                    dict.fromkeys(
                        str(item).strip()
                        for item in proposed_materials
                        if str(item).strip()
                    )
                )
                selection_report: Dict[str, Any] | None = None
                select_materials = getattr(catalog, "select_materials_for_route", None)
                rewrite_materials = getattr(catalog, "rewrite_execution_materials", None)
                if unique_materials and callable(select_materials) and user_question:
                    try:
                        selected_materials, selection_report = select_materials(
                            unique_materials,
                            user_question=user_question,
                            problem_analysis=problem_analysis,
                            force_mock=force_mock,
                        )
                    except Exception as exc:
                        selected_materials = []
                        selection_report = {
                            "schema_version": "route-material-selector.v1",
                            "status": "failed",
                            "selection_method": "ranked_candidates_then_internal_llm",
                            "error": _safe_error(exc),
                        }
                    if (
                        not selected_materials
                        or len(selected_materials) != len(unique_materials)
                    ):
                        rejected_materials.append(
                            {
                                "route": stated_id,
                                "material_selection": selection_report
                                or {
                                    "status": "failed",
                                    "error": "material selector returned no complete selection",
                                },
                            }
                        )
                        selection_errors = (selection_report or {}).get("errors") or ()
                        errors.append(
                            f"{stated_id}: material dataset selection failed"
                            + (
                                ": " + "; ".join(str(item) for item in selection_errors[:4])
                                if selection_errors
                                else ""
                            )
                        )
                        continue
                    candidate["proposed_materials"] = list(selected_materials)
                    if callable(rewrite_materials):
                        candidate["execution_request_english"] = rewrite_materials(
                            candidate.get("execution_request_english"),
                            unique_materials,
                            selected_materials,
                        )
                declared = catalog.verify_all(
                    candidate.get("proposed_materials") or (),
                    where="proposed_materials",
                )
                mentioned = catalog.scan_text(
                    candidate.get("execution_request_english"),
                    where="execution_request_english",
                )
                unusable = [
                    verdict
                    for verdict in tuple(declared) + tuple(mentioned)
                    if not verdict.ok
                ]
                material_report = {
                    "selection": selection_report,
                    "resolved": [
                        verdict.as_dict() for verdict in declared if verdict.ok
                    ]
                }
                if unusable:
                    rejected_materials.append(
                        {
                            "route": stated_id,
                            "materials": [
                                verdict.as_dict() for verdict in unusable
                            ],
                        }
                    )
                    errors.append(
                        f"{stated_id}: "
                        + "; ".join(verdict.message() for verdict in unusable[:4])
                    )
                    continue

            # Unknown citation labels are dropped rather than fatal: a route's
            # physics does not become wrong because it mislabelled a reference,
            # but an unverifiable label must not enter the provenance chain.
            citations = candidate.get("evidence_ids")
            citations = (
                [citations] if isinstance(citations, str) else list(citations or ())
            )
            verified = [
                str(value).strip()
                for value in citations
                if str(value).strip().casefold() in allowed
            ]
            unknown = [
                str(value).strip()
                for value in citations
                if str(value).strip() and str(value).strip().casefold() not in allowed
            ]
            if unknown:
                warnings.append(
                    f"{stated_id} cited {len(unknown)} label(s) absent from the "
                    f"harvest ({', '.join(sorted(set(unknown))[:5])}); dropped"
                )
            candidate["evidence_ids"] = verified

            try:
                route = DesignRoute.model_validate(candidate).model_dump(mode="json")
            except Exception as exc:
                errors.append(f"{stated_id}: {_safe_error(exc)}")
                continue

            signature = _axis_signature(route)
            if signature in signatures:
                warnings.append(
                    f"{stated_id} tunes the same axis as {signatures[signature]}; "
                    "kept the earlier one"
                )
                continue
            signatures[signature] = stated_id
            try:
                priority = int(route.get("priority") or 1)
            except (TypeError, ValueError):
                priority = 1
            accepted.append((priority, index, route, declarations, material_report))

        # Highest priority first, the model's own order breaking ties, so a
        # truncation drops what the model itself ranked last.
        accepted.sort(key=lambda entry: (entry[0], entry[1]))
        if len(accepted) > self.maximum_routes:
            warnings.append(
                f"{len(accepted)} routes were proposed; kept the {self.maximum_routes} "
                "highest-priority ones and dropped "
                + ", ".join(
                    str(entry[2].get("route_id") or "")
                    for entry in accepted[self.maximum_routes :]
                )
            )
            accepted = accepted[: self.maximum_routes]

        routes: List[Dict[str, Any]] = []
        declarations_by_id: Dict[str, Dict[str, List[str]]] = {}
        materials_by_id: Dict[str, Dict[str, Any]] = {}
        for position, (_, _, route, declarations, material_report) in enumerate(
            accepted, 1
        ):
            # Renumbered so the identifiers the tournament keys on are unique
            # and ordered, whatever the model named them.
            route_id = f"route_{position:02d}"
            route["route_id"] = route_id
            route["priority"] = position
            routes.append(route)
            declarations_by_id[route_id] = declarations
            if material_report:
                materials_by_id[route_id] = material_report

        return (
            routes,
            declarations_by_id,
            tuple(dict.fromkeys(errors)),
            warnings,
            {"routes": materials_by_id, "rejected": rejected_materials},
        )

    # -- the whole stage --------------------------------------------------

    def plan(
        self,
        question: str,
        *,
        problem_analysis: Any = None,
        force_mock: bool | None = None,
        literature_limit: int | None = None,
    ) -> RoutePlanResult:
        """Run the three steps and return one result carrying all provenance."""

        query_result = self.derive_queries(
            question, problem_analysis=problem_analysis, force_mock=force_mock
        )
        harvest = self.harvest_literature(query_result.queries, limit=literature_limit)
        result = self.propose_routes(
            question,
            harvest,
            problem_analysis=problem_analysis,
            force_mock=force_mock,
        )
        return result.model_copy(update={"query_result": query_result})


class QwenMemoryControlRoutePlanner:
    """Plan ONE route from the model's prior knowledge, with no literature.

    A distinct path rather than the literature planner with its evidence
    stripped, and the distinction is the experiment.  Removing citations from a
    plan that was written while reading papers still yields a plan shaped by
    those papers; what the control arm has to measure is what the model proposes
    when it never saw them.  So this class carries its own prompt, never
    constructs or accepts a literature client, and hands the model a payload
    that contains the user's request and nothing retrieved.

    Route verification is deliberately shared with the literature planner --
    same DesignRoute contract, same material resolution, same design_variables
    requirement -- because the control route enters the same tournament,
    compiler and executor as every other route.  Only the planning input
    differs.  That is what makes the comparison a comparison.
    """

    def __init__(
        self,
        client: RoutePlanningClient,
        *,
        planning_prompt_path: Path | str = DEFAULT_CONTROL_ROUTE_PROMPT,
        maximum_attempts: int = DEFAULT_MAXIMUM_ATTEMPTS,
        material_catalog: Any = AUTO_MATERIAL_CATALOG,
        route_id: str = CONTROL_ROUTE_ID,
    ) -> None:
        self.client = client
        self.planning_prompt_path = Path(planning_prompt_path)
        self.maximum_attempts = max(1, int(maximum_attempts))
        self.material_catalog = material_catalog
        self.route_id = str(route_id)
        self._material_catalog_error: str | None = None
        self._model_label = str(getattr(client, "model_name", ROUTE_PLANNING_MODEL))
        # One route, always.  Reusing the literature planner's verifier means
        # reusing its bounds, and the control arm's whole contract is that it
        # adds exactly one route to the portfolio.
        self.minimum_routes = 1
        self.maximum_routes = 1

    # The verifier, the catalogue resolution and the per-route material report
    # are identical requirements, so they are borrowed rather than copied: a
    # divergence between how a control route and a literature route are
    # validated would be a difference between the arms that is not the
    # independent variable.
    _resolved_material_catalog = QwenLiteratureRoutePlanner._resolved_material_catalog
    _verify_routes = QwenLiteratureRoutePlanner._verify_routes
    _material_report = QwenLiteratureRoutePlanner._material_report

    @staticmethod
    def _control_problem_analysis(problem_analysis: Any) -> Any:
        """The problem contract, with every research-derived field removed.

        The control arm gets the same user/problem contract as every other
        route: same request, same bands, same constraints.  What it must not get
        is anything that came out of reading -- and the analyzer's own
        ``method_research_questions`` is exactly that kind of field, a list of
        what the literature stage was told to go find out.  Passing it would
        leak the research agenda into the arm defined by not having one, which
        is a small leak that invalidates the comparison completely.
        """

        payload = _as_plain(problem_analysis)
        if not isinstance(payload, Mapping):
            return payload
        return {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "method_research_questions",
                "needs_method_research",
                "method_research",
                "method_findings",
                "queries",
                "unresolved_questions",
                "literature",
                "evidence",
            }
        }

    def propose_route(
        self,
        question: str,
        *,
        problem_analysis: Any = None,
        force_mock: bool | None = None,
        prior_iterations: Iterable[Mapping[str, Any]] = (),
        feedback_directives: Iterable[str] = (),
        chain_id: str | None = None,
    ) -> RoutePlanResult:
        """Ask for the one control route; regenerate while nothing usable comes back."""

        digest = _question_digest(question)
        # An empty harvest that was never attempted, recorded as such: the
        # status distinguishes "no literature was retrieved" from "no literature
        # was sought", and only the second one describes this route.
        harvest = LiteratureHarvest(
            status="not_consulted",
            requested_limit=0,
            errors=(CONTROL_NO_LITERATURE_DISCLOSURE,),
        )
        try:
            system_prompt = self.planning_prompt_path.read_text(encoding="utf-8")
        except Exception as exc:
            return RoutePlanResult(
                status="unavailable",
                validation_errors=(_safe_error(exc),),
                literature=harvest,
                model_name=self._model_label,
                question_digest=digest,
            )

        catalog = self._resolved_material_catalog()
        base_payload: Dict[str, Any] = {
            "user_question": str(question or "").strip(),
            "fixed_rules": {
                "minimum_routes": 1,
                "maximum_routes": 1,
                # Empty, not absent: the model is told there is no allowlist to
                # cite from, so an invented label is a rule it broke rather than
                # a field it guessed at.
                "evidence_ids_allowed": [],
                "literature_status": "not_consulted",
                "knowledge_source": "model_prior_knowledge_only",
                "no_literature_disclosure": CONTROL_NO_LITERATURE_DISCLOSURE,
                "every_route_must_declare": "design_variables",
                "verification": (
                    "a local check validates the route against the same execution "
                    "contract every other route in this study is held to, and "
                    "rejects any route that does not state what it tunes; a "
                    "rejected route is returned for regeneration"
                ),
                "model": self._model_label,
            },
        }
        warnings: List[str] = []
        if catalog is not None:
            base_payload["material_catalog"] = catalog.prompt_payload()
            base_payload["fixed_rules"]["materials_must_resolve"] = (
                "every material named in proposed_materials and in "
                "execution_request_english is resolved against material_catalog "
                "by a local check; a name the registry cannot resolve to exactly "
                "one dataset sends the route back for repair"
            )
            base_payload["fixed_rules"]["measured_not_tunable"] = MEASURED_NOT_TUNABLE
        else:
            warnings.append(
                "material_catalog_unavailable: proposed materials were not "
                "verified against the engine registry"
                + (
                    f" ({self._material_catalog_error})"
                    if self._material_catalog_error
                    else ""
                )
            )
        if problem_analysis is not None:
            base_payload["problem_analysis"] = self._control_problem_analysis(
                problem_analysis
            )
        compact_prior = _compact_control_iterations(prior_iterations)
        compact_feedback = [
            str(item).strip()
            for item in feedback_directives
            if str(item).strip()
        ][:6]
        if compact_prior or compact_feedback or chain_id:
            # This is the only continuation context the control arm receives.
            # In particular, there is deliberately no method_research key and
            # no evidence pool, even when the normal route has one.
            base_payload["prior_iterations"] = compact_prior
            base_payload["feedback_directives"] = compact_feedback
            if chain_id:
                base_payload["refinement_chain"] = {
                    "current_route_id": str(chain_id),
                    "instruction": (
                        f"Every continuation route MUST keep route_id '{self.route_id}' "
                        "and set parent_route_id to that same stable control id."
                    ),
                }

        usages: List[Dict[str, Any]] = []
        errors: Tuple[str, ...] = ()
        materials: Dict[str, Any] = {}
        repaired: List[Dict[str, Any]] = []
        previous = ""
        for attempt in range(1, self.maximum_attempts + 1):
            payload = dict(base_payload)
            if attempt > 1:
                payload["rejected_plan"] = _safe_json(previous)
                payload["repair_request"] = {
                    "validation_errors": list(errors),
                    "instruction": (
                        "Repair only the listed defects and return the corrected "
                        "complete JSON object. Return exactly one route, state "
                        "what it tunes in design_variables, keep evidence_ids "
                        "empty, and name only materials the local registry "
                        "resolves."
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
                    max_tokens=ROUTE_PLANNING_MAX_TOKENS,
                    force_mock=force_mock,
                )
            except Exception as exc:
                return RoutePlanResult(
                    status="unavailable",
                    attempts=attempt,
                    warnings=tuple(warnings),
                    validation_errors=(_safe_error(exc),),
                    planning_usage=tuple(usages),
                    literature=harvest,
                    model_name=self._model_label,
                    question_digest=digest,
                )
            _record_usage(response, usages)
            previous = str(response.get("content") or "")
            raw = _safe_json(previous)
            # An empty allowlist means every citation the model wrote is
            # dropped, which is the intended outcome: the control route enters
            # the provenance chain carrying no evidence at all.
            routes, declarations, errors, attempt_warnings, materials = (
                self._verify_routes(
                    raw,
                    allowed_labels=(),
                    catalog=catalog,
                    user_question=question,
                    problem_analysis=problem_analysis,
                    force_mock=force_mock,
                )
            )
            if catalog is not None:
                drain_usage = getattr(catalog, "drain_selector_usage", None)
                if callable(drain_usage):
                    for row in drain_usage():
                        _record_usage({"_llm_usage": row}, usages)
            warnings.extend(attempt_warnings)
            for entry in materials.get("rejected") or ():
                repaired.append({"attempt": attempt, **entry})
            if not routes:
                continue
            routes, declarations, materials = self._rekey_to_control_id(
                routes,
                declarations,
                materials,
                parent_route_id=chain_id,
            )
            plan = {
                "problem_id": str(raw.get("problem_id") or "").strip()
                or f"question-{digest}",
                "planning_summary": _clean_text(raw.get("planning_summary"), 1200),
                "routes": routes,
                "knowledge_source": "model_prior_knowledge_only",
                "knowledge_source_disclosure": _clean_text(
                    raw.get("knowledge_source_disclosure")
                    or CONTROL_NO_LITERATURE_DISCLOSURE,
                    800,
                ),
                "research_influence": [],
                "unresolved_decisions": normalize_text_sequence_list(
                    raw.get("unresolved_decisions") or []
                ),
                "stop_if_all_routes_fail": _clean_text(
                    raw.get("stop_if_all_routes_fail"), 600
                ),
            }
            return RoutePlanResult(
                status="planned",
                plan=plan,
                pre_declarations=declarations,
                attempts=attempt,
                route_count=len(routes),
                warnings=tuple(dict.fromkeys(warnings)),
                validation_errors=tuple(dict.fromkeys(errors)),
                planning_usage=tuple(usages),
                literature=harvest,
                model_name=self._model_label,
                question_digest=digest,
                material_verification=self._material_report(
                    catalog, materials, repaired
                ),
            )
        return RoutePlanResult(
            status="invalid",
            attempts=self.maximum_attempts,
            warnings=tuple(dict.fromkeys(warnings)),
            validation_errors=tuple(dict.fromkeys(errors))
            or ("the model produced no valid control route",),
            planning_usage=tuple(usages),
            literature=harvest,
            model_name=self._model_label,
            question_digest=digest,
            material_verification=self._material_report(catalog, materials, repaired),
        )

    def _rekey_to_control_id(
        self,
        routes: List[Dict[str, Any]],
        declarations: Dict[str, Dict[str, List[str]]],
        materials: Dict[str, Any],
        *,
        parent_route_id: str | None = None,
    ) -> tuple[
        List[Dict[str, Any]], Dict[str, Dict[str, List[str]]], Dict[str, Any]
    ]:
        """Give the route its stable id, carrying every ledger keyed on it.

        The shared verifier renumbers to ``route_01``, which is correct for the
        literature portfolio and wrong here: appending a route called route_01
        to a portfolio that already has one collides, and the collision is
        silent because both are valid ids.  Renaming it afterwards means the
        declarations and the material report have to move with it, or the
        attestation records pre-declarations under a route id no track will ever
        look up.
        """

        route = dict(routes[0])
        planned_id = str(route.get("route_id") or "")
        route["route_id"] = self.route_id
        route["priority"] = 1
        # The initial control route is a lineage root. A continuation keeps the
        # stable control id as its parent so the scheduler can audit that the
        # feedback round stayed on the same planning arm.
        route["parent_route_id"] = parent_route_id
        route["evidence_ids"] = []
        materials_by_id = dict(materials.get("routes") or {})
        return (
            [route],
            {
                self.route_id: declarations.get(planned_id)
                or declarations.get(self.route_id)
                or {"expected_observations": [], "stop_conditions": []}
            },
            {
                "routes": (
                    {self.route_id: materials_by_id[planned_id]}
                    if planned_id in materials_by_id
                    else {}
                ),
                "rejected": list(materials.get("rejected") or ()),
            },
        )

    def sidecar(self, result: RoutePlanResult) -> Dict[str, Any]:
        """The artifact, marked so it can never be read as a literature plan."""

        envelope = result.sidecar()
        envelope["schema_version"] = CONTROL_PLANNING_SCHEMA_VERSION
        envelope["planning_mechanism"] = CONTROL_ROUTE_SOURCE
        envelope["knowledge_source"] = "model_prior_knowledge_only"
        envelope["no_literature_disclosure"] = CONTROL_NO_LITERATURE_DISCLOSURE
        envelope["evidence_ids_allowed"] = []
        envelope["literature_client_invoked"] = False
        envelope["method_research_supplied"] = False
        return envelope

    def plan(
        self,
        question: str,
        *,
        problem_analysis: Any = None,
        force_mock: bool | None = None,
        prior_iterations: Iterable[Mapping[str, Any]] = (),
        feedback_directives: Iterable[str] = (),
        chain_id: str | None = None,
    ) -> RoutePlanResult:
        """The whole stage. One step, because there is nothing to retrieve."""

        return self.propose_route(
            question,
            problem_analysis=problem_analysis,
            force_mock=force_mock,
            prior_iterations=prior_iterations,
            feedback_directives=feedback_directives,
            chain_id=chain_id,
        )


def _as_plain(value: Any) -> Any:
    """Reduce a model or mapping to JSON-safe primitives for the prompt."""

    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except Exception:
            pass
    if isinstance(value, Mapping):
        return {str(key): _as_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_plain(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
