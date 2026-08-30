"""Semantic Scholar online gateway used by the S2-first pipeline."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable

from optomind_research.s2_cache import (
    DEFAULT_S2_CACHE,
    S2PersistentCache,
    canonical_request_key,
)
from optomind_research.s2_schemas import S2PaperRecord, parse_paper_record
from tools.academic_backends.semantic_scholar_backend import _api_keys


GRAPH_BASE = "https://api.semanticscholar.org/graph/v1"
RECOMMEND_BASE = "https://api.semanticscholar.org/recommendations/v1"

RICH_FIELDS = ",".join(
    [
        "paperId",
        "corpusId",
        "title",
        "abstract",
        "year",
        "authors",
        "venue",
        "publicationVenue",
        "externalIds",
        "referenceCount",
        "citationCount",
        "influentialCitationCount",
        "isOpenAccess",
        "openAccessPdf",
        "publicationTypes",
        "publicationDate",
        "s2FieldsOfStudy",
        "citationStyles",
        "tldr",
        "embedding.specter_v2",
        "textAvailability",
    ]
)

# Discovery is intentionally a cheap identity/abstract lookup.  In
# particular, tldr, embeddings, citation styles, and text-availability
# expansions are not requested for every broad query.  They have triggered
# provider-side 429s even when the same query succeeds with this field set.
DISCOVERY_FIELDS = ",".join(
    [
        "paperId",
        "title",
        "abstract",
        "year",
        "externalIds",
        "isOpenAccess",
        "openAccessPdf",
    ]
)

# A bounded identity enrichment used only after discovery has shortlisted
# paper IDs.  Keep this separate from RICH_FIELDS so a broad search cannot
# accidentally regress to the restricted field set.
ENRICHMENT_FIELDS = ",".join(
    [
        "paperId",
        "corpusId",
        "title",
        "abstract",
        "year",
        "authors",
        "venue",
        "publicationVenue",
        "externalIds",
        "referenceCount",
        "citationCount",
        "influentialCitationCount",
        "isOpenAccess",
        "openAccessPdf",
        "publicationTypes",
        "publicationDate",
        "s2FieldsOfStudy",
        "textAvailability",
    ]
)

EDGE_FIELDS = ",".join(
    [
        "contexts",
        "intents",
        "contextsWithIntent",
        "isInfluential",
        "paperId",
        "corpusId",
        "title",
        "abstract",
        "year",
        "authors",
        "venue",
        "externalIds",
        "citationCount",
        "influentialCitationCount",
        "isOpenAccess",
        "openAccessPdf",
        "publicationTypes",
        "publicationDate",
    ]
)

RECOMMENDATION_FIELDS = ",".join(
    [
        "paperId",
        "corpusId",
        "title",
        "abstract",
        "year",
        "authors",
        "venue",
        "publicationVenue",
        "externalIds",
        "referenceCount",
        "citationCount",
        "influentialCitationCount",
        "isOpenAccess",
        "openAccessPdf",
        "publicationTypes",
        "publicationDate",
        "s2FieldsOfStudy",
        "citationStyles",
    ]
)


@dataclass(slots=True)
class S2GatewayResponse:
    ok: bool
    payload: Any = None
    status_code: int = 0
    status_category: str = ""
    error: str = ""
    cache_hit: bool = False
    elapsed_seconds: float = 0.0
    wait_seconds: float = 0.0
    retry_count: int = 0
    key_slot: int | None = None
    endpoint: str = ""
    audit: dict[str, Any] = field(default_factory=dict)


class S2AvailabilityError(RuntimeError):
    """A transport/provider availability failure, not a scientific zero."""

    def __init__(self, message: str, response: S2GatewayResponse | None = None) -> None:
        super().__init__(message)
        self.response = response


class S2RequestContractError(RuntimeError):
    """A deterministic request-shape/provider contract failure."""

    def __init__(self, message: str, response: S2GatewayResponse | None = None) -> None:
        super().__init__(message)
        self.response = response


def _retry_after_seconds(headers: Any, attempt: int) -> float:
    value = ""
    try:
        value = str(headers.get("Retry-After") or "")
    except Exception:
        value = ""
    if value:
        try:
            return max(0.5, min(120.0, float(value)))
        except ValueError:
            try:
                dt = parsedate_to_datetime(value)
                return max(0.5, min(120.0, dt.timestamp() - time.time()))
            except Exception:
                pass
    return min(60.0, (1.5 * (2**attempt)) + random.uniform(0.1, 0.8))


class S2Transport:
    """Cached transport with patient 429 handling and secret-safe metrics."""

    def __init__(
        self,
        *,
        keys: list[str] | None = None,
        cache: S2PersistentCache | None = None,
        cache_path: str | Path = DEFAULT_S2_CACHE,
        timeout_seconds: float = 40.0,
        min_interval_seconds: float = 1.1,
        max_attempts: int | None = None,
        max_elapsed_seconds: float | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self.keys = list(keys if keys is not None else _api_keys())
        self.cache = cache or S2PersistentCache(cache_path)
        self.timeout_seconds = timeout_seconds
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.max_attempts = max_attempts or max(3, len(self.keys) * 2 or 3)
        self.max_elapsed_seconds = (
            None
            if max_elapsed_seconds is None
            else max(1.0, float(max_elapsed_seconds))
        )
        self.sleep_fn = sleep_fn
        self.opener = opener
        self._cursor = 0

    def _next_key(self) -> tuple[int | None, str]:
        if not self.keys:
            return None, ""
        slot = self._cursor % len(self.keys)
        self._cursor += 1
        return slot, self.keys[slot]

    def _pace(self) -> float:
        wait = self.cache.reserve_request_slot(
            min_interval_seconds=self.min_interval_seconds,
        )
        if wait:
            self.sleep_fn(wait)
        return wait

    def request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        ttl_seconds: float = 7 * 86400,
        negative_ttl_seconds: float = 86400,
        schema_version: str = "v1",
    ) -> S2GatewayResponse:
        endpoint = urllib.parse.urlparse(url).path
        cache_key = canonical_request_key(
            method, endpoint, params, body, schema_version=schema_version
        )
        cached = self.cache.get(
            method, endpoint, params, body, schema_version=schema_version
        )
        if cached.hit:
            response = S2GatewayResponse(
                ok=not cached.negative,
                payload=cached.payload,
                status_code=cached.status_code,
                status_category="cache_hit",
                cache_hit=True,
                endpoint=endpoint,
            )
            self.cache.record_metric(
                cache_key=cache_key,
                endpoint=endpoint,
                status_category=response.status_category,
                status_code=response.status_code,
                elapsed_seconds=0.0,
                wait_seconds=0.0,
                retry_count=0,
                key_slot=None,
                cache_hit=True,
            )
            return response

        final_url = url
        if params:
            final_url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
                params, doseq=True
            )
        encoded_body = None
        if body is not None:
            encoded_body = json.dumps(body).encode("utf-8")

        started = time.monotonic()
        total_wait = 0.0
        last_error = ""
        last_status = 0
        last_category = "availability_delay"
        last_slot: int | None = None
        # One logical request keeps one credential across rate-limit and
        # transient-server retries.  A different key is selected only after an
        # explicit authentication/authorization failure.
        slot, key = self._next_key()

        for attempt in range(self.max_attempts):
            if (
                self.max_elapsed_seconds is not None
                and time.monotonic() - started >= self.max_elapsed_seconds
            ):
                last_category = "availability_budget_exhausted"
                last_error = (
                    "Semantic Scholar request budget exhausted after "
                    f"{self.max_elapsed_seconds:.1f} seconds"
                )
                break
            total_wait += self._pace()
            last_slot = slot
            headers = {
                "Accept": "application/json",
                "User-Agent": "OptoMind-S2-First/2.0",
            }
            if key:
                headers["x-api-key"] = key
            if body is not None:
                headers["Content-Type"] = "application/json"
            request = urllib.request.Request(
                final_url,
                data=encoded_body,
                headers=headers,
                method=method.upper(),
            )
            try:
                with self.opener(request, timeout=self.timeout_seconds) as resp:
                    raw = resp.read()
                    payload = (
                        json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
                    )
                    status = int(getattr(resp, "status", 200))
                    self.cache.put(
                        method,
                        endpoint,
                        params,
                        body,
                        status_code=status,
                        payload=payload,
                        ttl_seconds=ttl_seconds,
                        schema_version=schema_version,
                    )
                    result = S2GatewayResponse(
                        ok=True,
                        payload=payload,
                        status_code=status,
                        status_category="ok",
                        elapsed_seconds=round(time.monotonic() - started, 3),
                        wait_seconds=round(total_wait, 3),
                        retry_count=attempt,
                        key_slot=slot,
                        endpoint=endpoint,
                    )
                    self.cache.record_metric(
                        cache_key=cache_key,
                        endpoint=endpoint,
                        status_category=result.status_category,
                        status_code=status,
                        elapsed_seconds=result.elapsed_seconds,
                        wait_seconds=result.wait_seconds,
                        retry_count=result.retry_count,
                        key_slot=slot,
                        cache_hit=False,
                    )
                    return result
            except urllib.error.HTTPError as exc:
                last_status = int(exc.code)
                try:
                    detail = exc.read().decode("utf-8", errors="replace")[:500]
                except Exception:
                    detail = str(exc.reason)
                last_error = f"HTTP {exc.code}: {detail}"
                if exc.code == 429 or 500 <= exc.code <= 599:
                    last_category = "availability_delay"
                    if attempt + 1 < self.max_attempts:
                        wait = _retry_after_seconds(exc.headers, attempt)
                        if (
                            self.max_elapsed_seconds is not None
                            and time.monotonic() - started + wait
                            >= self.max_elapsed_seconds
                        ):
                            last_category = "availability_budget_exhausted"
                            last_error = (
                                f"{last_error}; retry would exceed the "
                                f"{self.max_elapsed_seconds:.1f}-second request budget"
                            )
                            break
                        self.sleep_fn(wait)
                        total_wait += wait
                        continue
                elif exc.code in {401, 403}:
                    last_category = "authentication_failure"
                    if attempt + 1 < self.max_attempts and len(self.keys) > 1:
                        slot, key = self._next_key()
                        continue
                elif exc.code in {400, 404, 422}:
                    last_category = "request_contract_failure"
                else:
                    last_category = "availability_delay"
                break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                last_category = "availability_delay"
                if attempt + 1 < self.max_attempts:
                    wait = min(8.0, 0.5 * (2**attempt))
                    if (
                        self.max_elapsed_seconds is not None
                        and time.monotonic() - started + wait
                        >= self.max_elapsed_seconds
                    ):
                        last_category = "availability_budget_exhausted"
                        last_error = (
                            f"{last_error}; retry would exceed the "
                            f"{self.max_elapsed_seconds:.1f}-second request budget"
                        )
                        break
                    self.sleep_fn(wait)
                    total_wait += wait
                    continue
                break

        negative = last_status in {400, 404, 422}
        if negative:
            self.cache.put(
                method,
                endpoint,
                params,
                body,
                status_code=last_status,
                payload={"error": last_error},
                ttl_seconds=negative_ttl_seconds,
                schema_version=schema_version,
                negative=True,
            )
        result = S2GatewayResponse(
            ok=False,
            status_code=last_status,
            status_category=last_category,
            error=last_error,
            elapsed_seconds=round(time.monotonic() - started, 3),
            wait_seconds=round(total_wait, 3),
            retry_count=max(0, self.max_attempts - 1),
            key_slot=last_slot,
            endpoint=endpoint,
        )
        self.cache.record_metric(
            cache_key=cache_key,
            endpoint=endpoint,
            status_category=result.status_category,
            status_code=result.status_code,
            elapsed_seconds=result.elapsed_seconds,
            wait_seconds=result.wait_seconds,
            retry_count=result.retry_count,
            key_slot=last_slot,
            cache_hit=False,
        )
        return result


class S2IntelligenceGateway:
    """Typed access to S2 search, snippets, graph and recommendation APIs."""

    def __init__(self, transport: S2Transport | None = None) -> None:
        self.transport = transport or S2Transport()

    def search_papers(
        self,
        query: str,
        *,
        limit: int = 20,
        open_access_pdf: bool = False,
        enrich_limit: int = 0,
    ) -> tuple[list[S2PaperRecord], S2GatewayResponse]:
        """Run lightweight discovery, then optionally enrich a tiny shortlist."""

        params: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(int(limit), 100)),
            "fields": DISCOVERY_FIELDS,
        }
        if open_access_pdf:
            params["openAccessPdf"] = ""
        response = self.transport.request_json(
            "GET", f"{GRAPH_BASE}/paper/search", params=params, ttl_seconds=7 * 86400
        )
        papers = [
            parse_paper_record(item)
            for item in ((response.payload or {}).get("data") or [])
            if isinstance(item, dict)
        ]
        response.audit.update(
            {
                "discovery_fields": DISCOVERY_FIELDS,
                "enrichment_requested": max(0, int(enrich_limit)),
                "enrichment_calls": 0,
            }
        )
        if response.ok and papers and enrich_limit > 0:
            shortlist = [paper.paper_id for paper in papers if paper.paper_id][
                : max(1, min(int(enrich_limit), 20))
            ]
            if shortlist:
                enriched, enrichment_response = self.batch_papers(shortlist)
                response.audit.update(
                    {
                        "enrichment_calls": 1,
                        "enrichment_status_category": enrichment_response.status_category,
                        "enrichment_status_code": enrichment_response.status_code,
                        "enrichment_result_count": len(enriched),
                    }
                )
                if enrichment_response.ok:
                    by_id = {paper.paper_id: paper for paper in enriched}
                    papers = [by_id.get(paper.paper_id, paper) for paper in papers]
        return papers, response

    def title_match(
        self, title: str
    ) -> tuple[S2PaperRecord | None, S2GatewayResponse]:
        response = self.transport.request_json(
            "GET",
            f"{GRAPH_BASE}/paper/search/match",
            params={"query": title, "fields": ENRICHMENT_FIELDS},
            ttl_seconds=30 * 86400,
        )
        response.audit.setdefault("field_set", ENRICHMENT_FIELDS)
        data = (response.payload or {}).get("data") if response.ok else None
        return (parse_paper_record(data) if isinstance(data, dict) else None), response

    def get_paper(
        self, paper_id: str
    ) -> tuple[S2PaperRecord | None, S2GatewayResponse]:
        response = self.transport.request_json(
            "GET",
            f"{GRAPH_BASE}/paper/{urllib.parse.quote(paper_id, safe=':')}",
            params={"fields": ENRICHMENT_FIELDS},
            ttl_seconds=30 * 86400,
        )
        response.audit.setdefault("field_set", ENRICHMENT_FIELDS)
        return (
            parse_paper_record(response.payload)
            if response.ok and isinstance(response.payload, dict)
            else None
        ), response

    def batch_papers(
        self, paper_ids: list[str]
    ) -> tuple[list[S2PaperRecord], S2GatewayResponse]:
        ids = [item for item in dict.fromkeys(paper_ids) if item][:500]
        response = self.transport.request_json(
            "POST",
            f"{GRAPH_BASE}/paper/batch",
            params={"fields": ENRICHMENT_FIELDS},
            body={"ids": ids},
            ttl_seconds=30 * 86400,
        )
        response.audit.setdefault("field_set", ENRICHMENT_FIELDS)
        records = [
            parse_paper_record(item)
            for item in (response.payload or [])
            if isinstance(item, dict) and item.get("paperId")
        ]
        return records, response

    def search_snippets(
        self,
        query: str,
        *,
        limit: int = 20,
        paper_ids: list[str] | None = None,
        min_citation_count: int | None = None,
        publication_date_or_year: str = "",
    ) -> tuple[list[dict[str, Any]], S2GatewayResponse]:
        params: dict[str, Any] = {
            "query": query,
            "limit": max(1, min(int(limit), 1000)),
            "fields": (
                "snippet.text,snippet.snippetKind,snippet.section,"
                "snippet.snippetOffset,snippet.annotations.refMentions,"
                "snippet.annotations.sentences"
            ),
        }
        if paper_ids:
            params["paperIds"] = ",".join(paper_ids[:100])
        if min_citation_count is not None:
            params["minCitationCount"] = str(max(0, int(min_citation_count)))
        if publication_date_or_year:
            params["publicationDateOrYear"] = publication_date_or_year
        response = self.transport.request_json(
            "GET",
            f"{GRAPH_BASE}/snippet/search",
            params=params,
            ttl_seconds=30 * 86400,
        )
        data = (response.payload or {}).get("data") or []
        return [item for item in data if isinstance(item, dict)], response

    def _paper_edges(
        self, paper_id: str, *, relation: str, limit: int = 100
    ) -> tuple[list[dict[str, Any]], S2GatewayResponse]:
        response = self.transport.request_json(
            "GET",
            f"{GRAPH_BASE}/paper/{urllib.parse.quote(paper_id, safe=':')}/{relation}",
            params={
                "limit": max(1, min(int(limit), 1000)),
                "fields": EDGE_FIELDS,
            },
            ttl_seconds=14 * 86400,
        )
        data = (response.payload or {}).get("data") or []
        return [item for item in data if isinstance(item, dict)], response

    def references(
        self, paper_id: str, *, limit: int = 100
    ) -> tuple[list[dict[str, Any]], S2GatewayResponse]:
        return self._paper_edges(paper_id, relation="references", limit=limit)

    def citations(
        self, paper_id: str, *, limit: int = 100
    ) -> tuple[list[dict[str, Any]], S2GatewayResponse]:
        return self._paper_edges(paper_id, relation="citations", limit=limit)

    def recommendations_for_paper(
        self, paper_id: str, *, limit: int = 50, pool: str = "recent"
    ) -> tuple[list[S2PaperRecord], S2GatewayResponse]:
        response = self.transport.request_json(
            "GET",
            f"{RECOMMEND_BASE}/papers/forpaper/{urllib.parse.quote(paper_id, safe=':')}",
            params={
                "limit": max(1, min(int(limit), 500)),
                "from": pool,
                "fields": RECOMMENDATION_FIELDS,
            },
            ttl_seconds=7 * 86400,
        )
        records = [
            parse_paper_record(item)
            for item in ((response.payload or {}).get("recommendedPapers") or [])
            if isinstance(item, dict)
        ]
        return records, response

    def recommendations_from_seeds(
        self,
        positive_paper_ids: list[str],
        *,
        negative_paper_ids: list[str] | None = None,
        limit: int = 50,
    ) -> tuple[list[S2PaperRecord], S2GatewayResponse]:
        response = self.transport.request_json(
            "POST",
            f"{RECOMMEND_BASE}/papers",
            params={
                "limit": max(1, min(int(limit), 500)),
                "fields": RECOMMENDATION_FIELDS,
            },
            body={
                "positivePaperIds": positive_paper_ids[:100],
                "negativePaperIds": (negative_paper_ids or [])[:100],
            },
            ttl_seconds=7 * 86400,
        )
        records = [
            parse_paper_record(item)
            for item in ((response.payload or {}).get("recommendedPapers") or [])
            if isinstance(item, dict)
        ]
        return records, response


def classify_oa_candidate_from_headers(
    *,
    url: str,
    content_type: str = "",
    first_bytes: bytes = b"",
    final_url: str = "",
) -> dict[str, Any]:
    """Classify an OA candidate without assuming that an S2 URL is a PDF."""

    normalized_type = content_type.casefold()
    effective_url = final_url or url
    lower_url = effective_url.casefold()
    if first_bytes.startswith(b"%PDF") or "application/pdf" in normalized_type:
        kind = "pdf"
    elif "text/html" in normalized_type or lower_url.endswith((".html", ".htm")):
        kind = "html_requires_structure_check"
    elif "xml" in normalized_type or lower_url.endswith(".xml"):
        kind = "xml"
    else:
        kind = "unknown"
    return {
        "url": url,
        "final_url": effective_url,
        "content_type": content_type,
        "detected_kind": kind,
        "is_direct_fulltext_candidate": kind in {"pdf", "xml"},
    }
