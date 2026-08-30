"""Semantic Scholar adapter skeleton — disabled by default, requires API key.

Semantic Scholar API: https://api.semanticscholar.org/
Current introductory API-key limit: approximately 1 request/sec across
endpoint families. Provider-specific higher limits must not be assumed.
API key env: SEMANTIC_SCHOLAR_API_KEY

This backend is disabled until the user configures a key.
Once configured, it provides title/abstract search, citation graph,
and paper recommendation endpoints.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

S2_API_BASE = "https://api.semanticscholar.org/graph/v1"
S2_FIELDS = (
    "paperId,title,abstract,year,authors,url,venue,journal,"
    "externalIds,citationCount,publicationTypes,openAccessPdf"
)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_S2_KEYS_FILE = PROJECT_ROOT / "api_keys" / "semantic-scholar-api-key.txt"
NEW_PROJECT_S2_KEYS_FILE = PROJECT_ROOT / "api_keys" / "semanticscholar-apikey.txt"
LEGACY_S2_KEYS_FILE = Path.home() / "Desktop" / "semantic-scholar-api-key.txt"
NEW_DESKTOP_S2_KEYS_FILE = Path.home() / "Desktop" / "semanticscholar-apikey.txt"


def _split_keys(raw: str) -> List[str]:
    keys: List[str] = []
    for part in raw.replace(",", "\n").replace(";", "\n").splitlines():
        key = part.strip()
        if key:
            keys.append(key)
    return keys


def _api_keys() -> List[str]:
    """Load one or more Semantic Scholar API keys without logging secrets."""
    keys: List[str] = []
    for env_name in ("SEMANTIC_SCHOLAR_API_KEYS", "SEMANTIC_SCHOLAR_API_KEY"):
        keys.extend(_split_keys(os.environ.get(env_name) or ""))
    configured = os.environ.get("SEMANTIC_SCHOLAR_API_KEYS_FILE")
    key_files = [Path(configured)] if configured else [
        NEW_PROJECT_S2_KEYS_FILE,
        DEFAULT_S2_KEYS_FILE,
        NEW_DESKTOP_S2_KEYS_FILE,
        LEGACY_S2_KEYS_FILE,
    ]
    for path in key_files:
        if path.exists():
            try:
                keys.extend(_split_keys(path.read_text(encoding="utf-8", errors="replace")))
            except Exception:
                pass
    seen: set[str] = set()
    unique: List[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            unique.append(key)
    return unique


class SemanticScholarBackend:
    """Semantic Scholar paper search — disabled until API key configured.

    When enabled:
    - search(): Search papers by title/abstract keyword query.
    - get_paper(): Fetch a single paper by Semantic Scholar paper ID.
    - get_recommendations(): Get paper recommendations from a seed paper.

    Without a key, falls back to very-low-frequency public access (1 req/s),
    which is unreliable for batch processing.
    """

    def __init__(self) -> None:
        self.enabled = False
        self._api_keys = _api_keys()
        self._key_index = 0
        self._has_key = bool(self._api_keys)
        self._last_request = 0.0
        self.last_error = ""
        self.stats: Dict[str, int] = {
            "requests": 0,
            "errors": 0,
            "keys_loaded": len(self._api_keys),
            "key_rotations": 0,
        }

    def _respect_rate_limit(self) -> None:
        # The documented introductory limit applies to the aggregate stream,
        # not independently to every configured key.
        if self._has_key:
            rate = max(
                1.0,
                float(os.environ.get("S2_MIN_INTERVAL_SEC", "1.05")),
            )
        else:
            rate = max(
                1.0,
                float(os.environ.get("S2_PUBLIC_INTERVAL_SEC", "1.2")),
            )
        elapsed = time.monotonic() - self._last_request
        if elapsed < rate:
            time.sleep(rate - elapsed)
        self._last_request = time.monotonic()

    def _next_key(self) -> str:
        if not self._api_keys:
            return ""
        key = self._api_keys[self._key_index % len(self._api_keys)]
        self._key_index += 1
        if self._api_keys:
            self.stats["key_rotations"] = max(0, self._key_index - 1)
        return key

    def search(
        self,
        query: str,
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """Search Semantic Scholar. Returns empty list if disabled."""
        if not self.enabled and not self._has_key:
            return []

        params: Dict[str, Any] = {
            "query": query,
            "limit": str(min(max_results, 100)),
            "fields": S2_FIELDS,
        }
        url = f"{S2_API_BASE}/paper/search?{urllib.parse.urlencode(params)}"
        data = self._fetch_json(url)
        if data is None:
            return []

        results: List[Dict[str, Any]] = []
        for paper in data.get("data", []):
            source = self._parse_paper(paper, query)
            if source:
                results.append(normalize_s2_result(source))
        return results

    def get_paper(self, paper_id: str) -> Optional[Dict[str, Any]]:
        """Get a single paper by S2 paper ID."""
        if not self.enabled and not self._has_key:
            return None
        url = f"{S2_API_BASE}/paper/{urllib.parse.quote(paper_id, safe='')}?fields={urllib.parse.quote(S2_FIELDS, safe=',')}"
        data = self._fetch_json(url)
        if data is None:
            return None
        source = self._parse_paper(data, paper_id)
        return normalize_s2_result(source) if source else None

    def get_references(self, paper_id: str, max_results: int = 20) -> List[Dict[str, Any]]:
        """Fetch reference papers for a seed paper.

        paper_id may be an S2 paperId or an alternate ID such as DOI:10.xxxx.
        """
        if not self.enabled and not self._has_key:
            return []
        fields = S2_FIELDS
        params = urllib.parse.urlencode({"fields": fields, "limit": str(min(max_results, 100))})
        url = f"{S2_API_BASE}/paper/{urllib.parse.quote(paper_id, safe='')}/references?{params}"
        data = self._fetch_json(url)
        if data is None:
            return []
        out: List[Dict[str, Any]] = []
        for item in data.get("data", []) or []:
            paper = item.get("citedPaper") if isinstance(item, dict) else None
            if not isinstance(paper, dict):
                continue
            source = self._parse_paper(paper, f"references:{paper_id}")
            if source:
                out.append(normalize_s2_result(source))
        return out

    def get_citations(self, paper_id: str, max_results: int = 20) -> List[Dict[str, Any]]:
        """Fetch citing papers (forward citations) for a seed paper.

        paper_id may be an S2 paperId or an alternate ID such as DOI:10.xxxx.
        Returns papers that cite the seed paper (reverse of get_references).
        """
        if not self.enabled and not self._has_key:
            return []
        fields = S2_FIELDS
        params = urllib.parse.urlencode({"fields": fields, "limit": str(min(max_results, 100))})
        url = f"{S2_API_BASE}/paper/{urllib.parse.quote(paper_id, safe='')}/citations?{params}"
        data = self._fetch_json(url)
        if data is None:
            return []
        out: List[Dict[str, Any]] = []
        for item in data.get("data", []) or []:
            paper = item.get("citingPaper") if isinstance(item, dict) else None
            if not isinstance(paper, dict):
                continue
            source = self._parse_paper(paper, f"citations:{paper_id}")
            if source:
                out.append(normalize_s2_result(source))
        return out

    def _fetch_json(self, url: str) -> Optional[Dict[str, Any]]:
        attempts = max(3, len(self._api_keys) * 2 or 3)
        last_error = ""
        key = self._next_key()
        for attempt in range(attempts):
            self._respect_rate_limit()
            self.stats["requests"] += 1
            headers = {"User-Agent": "OptoMind/0.1"}
            if key:
                headers["x-api-key"] = key
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self.last_error = ""
                    return json.loads(resp.read().decode("utf-8", errors="replace"))
            except urllib.error.HTTPError as exc:
                self.stats["errors"] += 1
                last_error = f"HTTP {exc.code}: {exc.reason}"
                self.last_error = last_error
                if exc.code in {429, 500, 502, 503, 504} and attempt < attempts - 1:
                    retry_after = ""
                    try:
                        retry_after = exc.headers.get("Retry-After") if exc.headers else ""
                    except Exception:
                        retry_after = ""
                    try:
                        wait_s = min(8.0, max(1.0, float(retry_after or 1.2)))
                    except Exception:
                        wait_s = 1.2
                    time.sleep(wait_s)
                    continue
                if (
                    exc.code in {401, 403}
                    and attempt < attempts - 1
                    and len(self._api_keys) > 1
                ):
                    key = self._next_key()
                    continue
                return None
            except Exception as exc:
                self.stats["errors"] += 1
                last_error = f"{type(exc).__name__}: {exc}"
                self.last_error = last_error
                if attempt < attempts - 1:
                    time.sleep(0.5)
                    continue
                return None
        self.last_error = last_error
        return None

    def _parse_paper(
        self, paper: Dict[str, Any], query: str
    ) -> Optional[Dict[str, Any]]:
        try:
            s2_id = paper.get("paperId", "")
            title = paper.get("title", "")
            abstract = paper.get("abstract", "")
            year = paper.get("year")
            external_ids = paper.get("externalIds", {}) or {}

            authors: List[str] = []
            for a in paper.get("authors", []):
                name = a.get("name", "")
                if name:
                    authors.append(name)

            doi = external_ids.get("DOI", "")
            arxiv_id = external_ids.get("ArXiv", "")

            has_abstract = bool(abstract and len(abstract) > 50)
            evidence_extraction_ready = has_abstract

            venue_value = paper.get("venue", "")
            if isinstance(venue_value, dict):
                venue_name = venue_value.get("name", "")
            else:
                venue_name = str(venue_value or "")
            journal_value = paper.get("journal", {})
            journal_name = journal_value.get("name", "") if isinstance(journal_value, dict) else ""

            # Extract openAccessPdf.url to top-level pdf_url for direct use in KB ingest
            oa_pdf = paper.get("openAccessPdf") or {}
            s2_pdf_url = oa_pdf.get("url", "") if isinstance(oa_pdf, dict) else ""

            return {
                "source_id": f"s2:{s2_id}" if s2_id else f"s2:{title[:80]}",
                "title": title,
                "authors": authors,
                "year": year,
                "doi": doi,
                "url_or_doi": f"https://doi.org/{doi}" if doi else paper.get("url", ""),
                "arxiv_id": arxiv_id,
                "semantic_scholar_paper_id": s2_id,
                "source_url": paper.get("url", ""),
                "pdf_url": s2_pdf_url,
                "journal_or_venue": venue_name or journal_name,
                "publisher": "",
                "abstract_or_snippet": abstract,
                "query": query,
                "retrieval_method": "semantic_scholar_api",
                "backend": "semantic_scholar",
                "verification_status": "unverified",
                "evidence_extraction_ready": evidence_extraction_ready,
                "relevance_score": 0.5,
                "raw_metadata": {
                    "citation_count": paper.get("citationCount", 0),
                    "publication_types": paper.get("publicationTypes", []),
                    "open_access_pdf": oa_pdf,
                },
            }
        except Exception:
            return None


def normalize_s2_result(source: Dict[str, Any]) -> Dict[str, Any]:
    defaults = {
        "source_id": "", "title": "", "authors": [], "year": None,
        "doi": "", "url_or_doi": "", "arxiv_id": "",
        "semantic_scholar_paper_id": "", "openalex_id": "",
        "source_url": "", "pdf_url": "", "journal_or_venue": "",
        "publisher": "", "abstract_or_snippet": "", "query": "",
        "query_id": "", "retrieval_method": "", "backend": "semantic_scholar",
        "verification_status": "unverified", "evidence_extraction_ready": False,
        "relevance_score": 0.0, "backend_score": 0.0,
        "source_quality_score": 0.0, "raw_metadata": {},
        "local_pdf_path": "", "local_text_path": "",
        "local_chunks_path": "", "notes": "",
    }
    for k, v in defaults.items():
        source.setdefault(k, v)
    return source
