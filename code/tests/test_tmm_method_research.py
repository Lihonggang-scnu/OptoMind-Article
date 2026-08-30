from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from pydantic import ValidationError

import optomind_optics.harness.method_research as method_research
from optomind_optics.harness.method_research import (
    MethodAllowedUse,
    MethodContentDepth,
    MethodEvidence,
    MethodFinding,
    MethodResearchQuery,
    MethodResearchStatus,
    QwenMethodFindingSynthesizer,
    TMMMethodResearchAdapter,
    discover_review_kb_paths,
    generate_method_research_queries,
)


def _problem() -> dict[str, Any]:
    return {
        "problem_id": "tmm-method-test",
        "design_family": "one-dimensional dielectric multilayer",
        "materials": ["high-index dielectric", "low-index dielectric"],
        "objectives": {"metric": "reflectance", "wavelength_nm": [900, 1100]},
        "optimization_strategy": "bounded thickness search",
    }


def test_local_kb_discovery_prefers_hqvisual_and_is_narrow(tmp_path: Path) -> None:
    base = tmp_path / "outputs" / "review_knowledge_base"
    ordinary = base / "ordinary" / "review_knowledge_base.sqlite"
    preferred = base / "core-hqvisual-v1" / "review_knowledge_base.sqlite"
    ordinary.parent.mkdir(parents=True)
    preferred.parent.mkdir(parents=True)
    ordinary.write_bytes(b"")
    preferred.write_bytes(b"")

    assert discover_review_kb_paths(tmp_path) == (preferred,)


def test_local_kb_discovery_gracefully_returns_empty(tmp_path: Path) -> None:
    assert discover_review_kb_paths(tmp_path) == ()


def test_local_kb_discovery_rejects_cross_topic_pollution(tmp_path: Path) -> None:
    root = tmp_path / "outputs" / "review_knowledge_base" / "radiative-hqvisual"
    root.mkdir(parents=True)
    sqlite_path = root / "review_knowledge_base.sqlite"
    sqlite_path.write_bytes(b"")
    (root / "source_query_plan.current_english.json").write_text(
        method_research.json.dumps(
            {
                "question": "Transparent daytime radiative cooling for buildings",
                "scope": ["atmospheric-window emission", "photovoltaic thermal management"],
            }
        ),
        encoding="utf-8",
    )

    assert discover_review_kb_paths(
        tmp_path,
        question="Design a 1550 nm Fabry-Perot bandpass filter with stopbands",
    ) == ()
    assert discover_review_kb_paths(
        tmp_path,
        question="Review transparent radiative cooling for photovoltaic thermal management",
    ) == (sqlite_path,)


def test_realistic_provenance_boilerplate_cannot_admit_cross_topic_kb(tmp_path: Path) -> None:
    root = tmp_path / "outputs" / "review_knowledge_base" / "radiative-hqvisual"
    root.mkdir(parents=True)
    sqlite_path = root / "review_knowledge_base.sqlite"
    sqlite_path.write_bytes(b"")
    (root / "source_query_plan.current_english.json").write_text(
        method_research.json.dumps(
            {
                "question": "Transparent daytime radiative cooling materials",
                "result": {
                    "output": {
                        "problem_understanding": "Review atmospheric-window emission for passive radiative cooling.",
                        "scope_definition": {
                            "main_scope": "Thermal emission and photovoltaic cooling",
                            "scope_items": ["radiative cooling materials and outdoor validation"],
                        },
                        "keyword_decomposition": {
                            "keywords": ["radiative cooling", "atmospheric window emissivity"]
                        },
                    }
                },
                # These generic words used to create a false 10% overlap.
                "primary": {
                    "raw_response": "compare valid physical trade offs errors failure domain results " * 20
                },
                "final_validation": {"ok": True, "warnings": [], "errors": []},
            }
        ),
        encoding="utf-8",
    )

    ar_question = (
        "Design a broadband antireflection coating on fused silica over 450-700 nm; "
        "compare valid physical trade-offs and report errors, failure, domain results."
    )
    assert discover_review_kb_paths(tmp_path, question=ar_question) == ()


def test_local_results_receive_record_level_topic_and_scope_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_query_kb(path: Path, query: str, **_: Any) -> dict[str, Any]:
        return {
            "text_chunks": [
                {
                    "chunk_id": "bad-radiative",
                    "paper_id": "paper-radiative",
                    "title": "Multilayer photonic structures in radiative cooling",
                    "text_preview": (
                        "A multilayer coating uses reflectance and thermal emission in the "
                        "atmospheric window for radiative cooling."
                    ),
                },
                {
                    "chunk_id": "good-ar",
                    "paper_id": "paper-ar",
                    "title": "Broadband antireflection multilayer coating",
                    "text_preview": (
                        "Transfer-matrix optimization selects dielectric layer thicknesses "
                        "to suppress broadband reflectance in an antireflection coating."
                    ),
                },
            ]
        }

    monkeypatch.setattr(method_research, "query_kb", fake_query_kb)
    sqlite_path = tmp_path / "explicit.sqlite"
    sqlite_path.touch()
    report = TMMMethodResearchAdapter(
        review_kb_paths=[sqlite_path], online_enabled=False
    ).research(
        {"problem_id": "ar-local-gate"},
        explicit_queries=[
            "broadband antireflection multilayer coating transfer matrix optimization"
        ],
    )

    assert [item.paper_id for item in report.evidence] == ["paper-ar"]
    assert report.telemetry["records_returned"] == 2
    assert report.telemetry["records_accepted"] == 1
    assert report.telemetry["records_rejected_scope"] == 1


def test_query_generation_is_bounded_deterministic_and_scientific() -> None:
    first = generate_method_research_queries(_problem(), max_queries=3)
    second = generate_method_research_queries(_problem(), max_queries=3)

    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]
    assert len(first) == 3
    assert all("foundation" not in item.query_text.casefold() for item in first)
    assert all("frontier" not in item.query_text.casefold() for item in first)
    assert all(item.purpose.value in {"design_family", "material_choice", "objective_formulation"} for item in first)


def test_evidence_permission_boundaries_are_enforced() -> None:
    common = {
        "evidence_id": "e1",
        "paper_id": "p1",
        "title": "A paper",
        "source_route": "s2_search",
        "text": "A bounded passage.",
        "query_ids": ["q1"],
    }
    metadata = MethodEvidence(
        **common,
        content_depth=MethodContentDepth.metadata,
        allowed_use=MethodAllowedUse.discovery,
    )
    assert metadata.allowed_use == MethodAllowedUse.discovery

    with pytest.raises(ValidationError):
        MethodEvidence(
            **common,
            content_depth=MethodContentDepth.metadata,
            allowed_use=MethodAllowedUse.method_guidance,
        )
    with pytest.raises(ValidationError):
        MethodEvidence(
            **common,
            content_depth=MethodContentDepth.abstract,
            allowed_use=MethodAllowedUse.direct_fact,
        )
    snippet = MethodEvidence(
        **{**common, "evidence_id": "e2"},
        content_depth=MethodContentDepth.s2_snippet,
        allowed_use=MethodAllowedUse.method_guidance,
    )
    assert snippet.allowed_use == MethodAllowedUse.method_guidance


def test_local_first_deduplicates_papers_and_chunks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_calls: list[tuple[Path, str]] = []

    def fake_query_kb(path: Path, query: str, *, top_k: int = 8, include_raw: bool = False) -> dict[str, Any]:
        del top_k, include_raw
        local_calls.append((path, query))
        return {
            "papers": [
                {"paper_id": "paper-1", "title": "Bragg multilayer method", "year": 2024},
            ],
            "text_chunks": [
                {
                    "chunk_id": "chunk-1",
                    "paper_id": "paper-1",
                    "title": "Bragg multilayer method",
                    "text_preview": "A distributed Bragg reflector uses alternating layers.",
                },
            ],
        }

    class NeverOnline:
        def search_s2(self, query: str, *, limit: int) -> list[Any]:
            raise AssertionError(f"online search should not run: {query}, {limit}")

        def search_openalex(self, query: str, *, limit: int) -> list[Any]:
            raise AssertionError("OpenAlex should not run after sufficient local evidence")

    monkeypatch.setattr(method_research, "query_kb", fake_query_kb)
    (tmp_path / "review.sqlite").touch()
    report = TMMMethodResearchAdapter(
        review_kb_paths=[tmp_path / "review.sqlite"],
        online_client=NeverOnline(),
    ).research(
        {"problem_id": "local-first"},
        explicit_queries=["distributed Bragg reflector multilayer"],
    )

    assert len(local_calls) == 1
    assert report.status == MethodResearchStatus.completed
    assert len(report.evidence) == 2
    assert report.evidence[1].content_depth == MethodContentDepth.fulltext
    assert report.evidence[1].allowed_use == MethodAllowedUse.method_guidance
    assert report.method_findings
    assert set(report.method_findings[0].evidence_ids).issubset({item.evidence_id for item in report.evidence})
    assert report.telemetry["local_queries"] == 1
    assert report.telemetry["records_returned"] == 2
    assert report.telemetry["records_accepted"] == 2


def test_one_local_method_paper_does_not_suppress_online_complement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_query_kb(path: Path, query: str, **_: Any) -> dict[str, Any]:
        del path, query
        return {
            "text_chunks": [
                {
                    "chunk_id": "local-method",
                    "paper_id": "local-one-paper",
                    "title": "A local multilayer method",
                    "section": "Methods",
                    "text_preview": (
                        "A transfer matrix method optimizes bounded multilayer "
                        "thicknesses under tolerance perturbations."
                    ),
                }
            ]
        }

    class OnlineComplement:
        def __init__(self) -> None:
            self.calls = 0

        def search_s2(self, query: str, *, limit: int) -> list[dict[str, Any]]:
            del query, limit
            self.calls += 1
            return [
                {
                    "chunk_id": "online-method",
                    "paper_id": "online-paper",
                    "title": "An independent multilayer method",
                    "section": "Methods",
                    "text": (
                        "An independent transfer matrix method evaluates "
                        "polarization and thickness tolerance."
                    ),
                    "content_depth": "s2_snippet",
                }
            ]

        def search_openalex(self, query: str, *, limit: int) -> list[Any]:
            del query, limit
            return []

    monkeypatch.setattr(method_research, "query_kb", fake_query_kb)
    (tmp_path / "review.sqlite").touch()
    online = OnlineComplement()
    report = TMMMethodResearchAdapter(
        review_kb_paths=[tmp_path / "review.sqlite"],
        online_client=online,
        require_method_guidance=True,
    ).research(
        {"problem_id": "local-one-paper"},
        explicit_queries=["multilayer transfer matrix tolerance"],
    )
    assert online.calls == 1
    assert report.telemetry["s2_calls"] == 1


def test_dedupe_merges_query_links_and_source_provenance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_query_kb(path: Path, query: str, **_: Any) -> dict[str, Any]:
        return {
            "papers": [
                {
                    "paper_id": "shared-paper",
                    "title": "Shared optical method",
                    "abstract": "An abstract lead.",
                }
            ],
            "text_chunks": [
                {
                    "chunk_id": "shared-chunk",
                    "paper_id": "shared-paper",
                    "title": "Shared optical method",
                    "text_preview": "Transfer matrix parameterization and bounded layers.",
                }
            ],
        }

    monkeypatch.setattr(method_research, "query_kb", fake_query_kb)
    (tmp_path / "review.sqlite").touch()
    report = TMMMethodResearchAdapter(
        review_kb_paths=[tmp_path / "review.sqlite"],
    ).research(
        {"problem_id": "dedupe"},
        explicit_queries=[
            "transfer matrix multilayer method",
            "bounded transfer matrix layers",
        ],
    )

    # The generic metadata title is not allowed to ride along merely because
    # a relevant full-text chunk from the same response exists.
    assert len(report.evidence) == 1
    chunk = next(item for item in report.evidence if item.evidence_id.endswith("shared-chunk"))
    assert chunk.query_ids == ["explicit_01", "explicit_02"]
    assert chunk.source_route == "local_review_kb"
    assert chunk.local_path == str(tmp_path / "review.sqlite")
    assert report.telemetry["local_queries"] == 2


def test_s2_first_and_openalex_complement_only_when_s2_is_empty(tmp_path: Path) -> None:
    class FakeOnline:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def search_s2(self, query: str, *, limit: int) -> dict[str, Any]:
            self.calls.append(("s2", query))
            return {"records": [], "cache_hit": True}

        def search_openalex(self, query: str, *, limit: int) -> list[dict[str, Any]]:
            self.calls.append(("openalex", query))
            return [
                {
                    "openalex_id": "W123",
                    "title": "A multilayer optimization method",
                    "abstract_or_snippet": "A transfer matrix method guides bounded layer optimization.",
                    "year": 2025,
                    "doi": "10.1000/example",
                }
            ]

    fake = FakeOnline()
    report = TMMMethodResearchAdapter(
        review_kb_paths=[tmp_path / "missing.sqlite"],
        online_client=fake,
    ).research(
        {"problem_id": "online-complement"},
        explicit_queries=["multilayer optimization method"],
    )

    assert [route for route, _ in fake.calls] == ["s2", "openalex"]
    assert report.telemetry["s2_calls"] == 1
    assert report.telemetry["openalex_calls"] == 1
    assert report.telemetry["cache_hits"] == 1
    assert report.telemetry["cache_source_routes"]["s2_search:hit"] == 1
    assert report.evidence[0].source_route == "openalex_search"


def test_s2_sufficient_suppresses_openalex_call(tmp_path: Path) -> None:
    class FakeOnline:
        def __init__(self) -> None:
            self.openalex_calls = 0

        def search_s2(self, query: str, *, limit: int) -> list[dict[str, Any]]:
            return [
                {
                    "paper_id": "s2-1",
                    "title": "S2 abstract method lead",
                    "abstract": "A transfer matrix method for multilayer optimization.",
                }
            ]

        def search_openalex(self, query: str, *, limit: int) -> list[Any]:
            self.openalex_calls += 1
            return []

    fake = FakeOnline()
    report = TMMMethodResearchAdapter(online_client=fake).research(
        {"problem_id": "s2-sufficient"},
        explicit_queries=["transfer matrix optimization"],
    )
    assert fake.openalex_calls == 0
    assert report.telemetry["s2_calls"] == 1
    assert report.telemetry["openalex_calls"] == 0
    assert report.evidence[0].content_depth == MethodContentDepth.abstract


def test_synthesis_discards_fabricated_evidence_ids(tmp_path: Path) -> None:
    def fake_query_kb(path: Path, query: str, **_: Any) -> dict[str, Any]:
        return {
            "text_chunks": [
                {
                    "chunk_id": "real-chunk",
                    "paper_id": "real-paper",
                    "title": "A real method passage",
                    "text_preview": "A transfer matrix method keeps layer order explicit.",
                }
            ]
        }

    def fabricated_callback(evidence: list[MethodEvidence], queries: list[MethodResearchQuery]) -> list[MethodFinding]:
        del evidence, queries
        return [
            MethodFinding(
                method="invented method",
                reusable_principle="Unsupported principle.",
                evidence_ids=["fabricated-evidence-id"],
                applicability="Unknown.",
                limitations="Must be checked.",
                confidence=0.1,
            )
        ]

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(method_research, "query_kb", fake_query_kb)
        (tmp_path / "review.sqlite").touch()
        report = TMMMethodResearchAdapter(
            review_kb_paths=[tmp_path / "review.sqlite"],
            synthesis_callback=fabricated_callback,
        ).research(
            {"problem_id": "fabricated-id"},
            explicit_queries=["transfer matrix method"],
        )
    finally:
        monkeypatch.undo()

    assert report.method_findings == []
    assert any("fabricated-evidence-id" in reason for reason in report.reasons)
    assert all(
        evidence_id in {item.evidence_id for item in report.evidence}
        for finding in report.method_findings
        for evidence_id in finding.evidence_ids
    )


def test_report_rejects_unknown_finding_evidence_id() -> None:
    finding = MethodFinding(
        method="transfer matrix method",
        reusable_principle="Keep layer parameters explicit.",
        evidence_ids=["not-in-report"],
        applicability="Bounded multilayer tasks.",
        limitations="Requires TMM verification.",
    )
    with pytest.raises(ValidationError):
        method_research.MethodResearchReport(
            problem_id="p1",
            method_findings=[finding],
            status=MethodResearchStatus.partial,
        )


def test_analyzer_contract_drives_scientific_queries() -> None:
    payload = {
        "problem_id": "analyzer-contract",
        "normalized_request_english": (
            "Design an angle-tolerant dielectric reflector from 900 to 1100 nm."
        ),
        "primary_intent": "design",
        "known_stack_materials": ["TiO2", "SiO2"],
        "target_observables": ["reflectance"],
        "preferred_behaviors": ["high reflectance from 900 to 1100 nm"],
        "suppressed_behaviors": ["angular degradation"],
        "design_variables": ["layer thicknesses", "layer count"],
        "manufacturing_constraints": ["minimum layer thickness 20 nm"],
        "method_research_questions": [
            "Which aperiodic dielectric multilayer families preserve broadband reflectance at oblique incidence?"
        ],
    }

    queries = generate_method_research_queries(payload, max_queries=6)

    assert queries[0].query_id == "analysis_question_01"
    assert "aperiodic dielectric multilayer" in queries[0].query_text
    assert "angle-tolerant dielectric reflector" in queries[0].query_text
    assert "transfer-matrix design" in queries[0].query_text
    assert any(item.purpose == method_research.MethodPurpose.material_choice for item in queries)
    assert any("TiO2" in item.query_text and "SiO2" in item.query_text for item in queries)
    assert any("minimum layer thickness" in item.query_text for item in queries)
    assert all("workflow" not in item.query_text.casefold() for item in queries)


def test_missing_local_kb_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "must-not-be-created.sqlite"

    report = TMMMethodResearchAdapter(review_kb_paths=[missing]).research(
        {"problem_id": "missing-kb"},
        explicit_queries=["transfer matrix multilayer design"],
    )

    assert not missing.exists()
    assert report.telemetry.local_queries == 0
    assert any(reason.startswith("local_kb_missing:") for reason in report.reasons)


def test_default_s2_client_promotes_quality_gated_body_snippet() -> None:
    class FakeGateway:
        def batch_papers(self, paper_ids: list[str]):
            assert paper_ids == ["paper-s2"]
            return [
                {
                    "paper_id": "paper-s2",
                    "title": "Aperiodic dielectric multilayer optimization",
                    "abstract": "A broad abstract about optical coating optimization.",
                }
            ], SimpleNamespace(
                cache_hit=False,
                status_category="ok",
                error="",
            )

        def search_snippets(self, query: str, *, limit: int, paper_ids: list[str] | None = None):
            del query, limit
            assert paper_ids is None
            text = (
                "A chirped multilayer varies the optical thickness of successive high- and low-index "
                "layers to distribute Bragg conditions across a broad spectral interval. The design "
                "was optimized with transfer-matrix calculations under oblique incidence, and bounded "
                "thickness perturbations were included to avoid fragile nominal solutions. This method "
                "provides a reusable route for broadband dielectric reflector design while retaining "
                "explicit layer order and material constraints."
            )
            item = {
                "score": 0.9,
                "paper": {"paperId": "paper-s2", "title": "Aperiodic dielectric multilayer optimization"},
                "snippet": {
                    "text": text,
                    "snippetKind": "body",
                    "section": "Methods",
                    "snippetOffset": {"start": 10, "end": 10 + len(text)},
                    "annotations": {"refMentions": [], "sentences": []},
                },
            }
            return [item], SimpleNamespace(
                status_code=200,
                status_category="ok",
                cache_hit=False,
                wait_seconds=0.0,
                payload={},
            )

    class NeverOpenAlex:
        last_error = ""

        def search(self, query: str, *, max_results: int):
            raise AssertionError(f"OpenAlex should not run: {query}, {max_results}")

    client = method_research.DefaultMethodResearchOnlineClient(
        s2_gateway=FakeGateway(),
        openalex_backend=NeverOpenAlex(),
        enrich_snippet_metadata=True,
    )
    report = TMMMethodResearchAdapter(
        online_client=client,
        require_method_guidance=True,
    ).research(
        {"problem_id": "snippet-route"},
        explicit_queries=["chirped multilayer broadband reflector"],
    )

    snippet = next(item for item in report.evidence if item.content_depth == MethodContentDepth.s2_snippet)
    assert snippet.allowed_use == MethodAllowedUse.method_guidance
    assert snippet.source_route == "s2_snippet_search"
    assert report.telemetry.s2_snippet_calls == 1
    assert report.telemetry.s2_batch_calls == 1
    assert report.telemetry.s2_calls == 2
    assert report.method_findings


class _EmptyOpenAlex:
    last_error = ""

    def search(self, query: str, *, max_results: int):
        del query, max_results
        return []


def test_default_s2_client_resolves_repo_key_pool_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import optomind_research.s2_intelligence_gateway as s2_gateway_module
    import tools.academic_backends.semantic_scholar_backend as s2_backend

    keys = ["key-a", "key-b"]
    monkeypatch.setattr(s2_backend, "_api_keys", lambda: list(keys))
    client = method_research.DefaultMethodResearchOnlineClient(
        openalex_backend=_EmptyOpenAlex()
    )
    assert isinstance(
        client.s2_gateway,
        s2_gateway_module.S2IntelligenceGateway,
    )
    assert client.s2_gateway.transport.keys == keys


def test_injected_s2_gateway_is_used() -> None:
    class FakeGateway:
        pass

    gateway = FakeGateway()
    client = method_research.DefaultMethodResearchOnlineClient(
        s2_gateway=gateway,
        openalex_backend=_EmptyOpenAlex(),
    )
    assert client.s2_gateway is gateway


def _s2_response(
    status_category: str = "rate_limited",
    status_code: int = 429,
) -> SimpleNamespace:
    return SimpleNamespace(
        status_category=status_category,
        status_code=status_code,
        ok=False,
        cache_hit=False,
        error="rate limited",
        payload={},
    )


def test_s2_rate_limited_reports_partial_telemetry_and_no_evidence() -> None:
    class RateLimitedGateway:
        def search_snippets(
            self,
            query: str,
            *,
            limit: int,
            paper_ids: list[str] | None = None,
        ):
            del query, limit, paper_ids
            return [], _s2_response()

        def batch_papers(self, paper_ids: list[str]):
            del paper_ids
            return [], _s2_response()

        def search_papers(self, query: str, *, limit: int):
            del query, limit
            return [], _s2_response()

    client = method_research.DefaultMethodResearchOnlineClient(
        s2_gateway=RateLimitedGateway(),
        openalex_backend=_EmptyOpenAlex(),
    )
    result = client.search_s2("multilayer design", limit=4)
    assert result.records == ()
    assert result.status == "rate_limited"
    assert "s2_rate_limited" in result.error

    report = TMMMethodResearchAdapter(
        online_client=client,
        require_method_guidance=True,
    ).research(
        _problem(),
        explicit_queries=["multilayer design"],
    )
    assert report.status in {
        MethodResearchStatus.partial,
        MethodResearchStatus.unavailable,
    }
    assert report.evidence == []
    assert any("online_s2_search_notice" in reason for reason in report.reasons)


def test_empty_s2_key_pool_reports_partial_telemetry_and_no_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.academic_backends.semantic_scholar_backend as s2_backend

    monkeypatch.setattr(s2_backend, "_api_keys", lambda: [])
    monkeypatch.setattr(
        method_research,
        "_resolve_shared_s2_key_pool",
        lambda *args, **kwargs: [],
    )
    client = method_research.DefaultMethodResearchOnlineClient(
        openalex_backend=_EmptyOpenAlex()
    )
    assert client.s2_gateway.transport.keys == []
    result = client.search_s2("multilayer design", limit=4)
    assert result.records == ()
    assert "s2_key_pool_empty" in result.error

    report = TMMMethodResearchAdapter(
        online_client=client,
        require_method_guidance=True,
    ).research(
        _problem(),
        explicit_queries=["multilayer design"],
    )
    assert report.status in {
        MethodResearchStatus.partial,
        MethodResearchStatus.unavailable,
    }
    assert report.evidence == []
    assert any("s2_key_pool_empty" in reason for reason in report.reasons)


def test_shared_s2_key_pool_bridge_resolves_files(tmp_path: Path) -> None:
    pool = tmp_path / "api_keys"
    pool.mkdir()
    (pool / "semantic-scholar-api-key.txt").write_text(
        "  key-a  \n# comment\n\nkey-b\nkey-a\n",
        encoding="utf-8",
    )
    (pool / "unrelated.txt").write_text("not-an-s2-key", encoding="utf-8")
    keys = method_research._resolve_shared_s2_key_pool([pool])
    assert keys == ["key-a", "key-b"]


def test_default_s2_client_uses_shared_pool_when_repo_pool_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.academic_backends.semantic_scholar_backend as s2_backend

    monkeypatch.setattr(s2_backend, "_api_keys", lambda: [])
    monkeypatch.setattr(
        method_research,
        "_resolve_shared_s2_key_pool",
        lambda *args, **kwargs: ["shared-key"],
    )
    client = method_research.DefaultMethodResearchOnlineClient(
        openalex_backend=_EmptyOpenAlex()
    )
    assert client.s2_gateway.transport.keys == ["shared-key"]


def test_default_s2_client_prefers_env_pool_over_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.academic_backends.semantic_scholar_backend as s2_backend

    monkeypatch.setattr(s2_backend, "_api_keys", lambda: ["env-key"])
    monkeypatch.setattr(
        method_research,
        "_resolve_shared_s2_key_pool",
        lambda *args, **kwargs: ["shared-key"],
    )
    client = method_research.DefaultMethodResearchOnlineClient(
        openalex_backend=_EmptyOpenAlex()
    )
    assert client.s2_gateway.transport.keys == ["env-key"]


def test_s2_snippet_failure_preserves_abstract_as_partial_result() -> None:
    class FakeGateway:
        def search_papers(self, query: str, *, limit: int):
            del query, limit
            return [
                {
                    "paper_id": "paper-s2",
                    "title": "Thin-film design study",
                    "abstract": "A transfer matrix study of multilayer design.",
                }
            ], SimpleNamespace(cache_hit=False, status_category="ok", error="")

        def search_snippets(self, *args: Any, **kwargs: Any):
            raise RuntimeError("temporary snippet outage")

    class NeverOpenAlex:
        last_error = ""

        def search(self, query: str, *, max_results: int):
            return []

    client = method_research.DefaultMethodResearchOnlineClient(
        s2_gateway=FakeGateway(),
        openalex_backend=NeverOpenAlex(),
    )
    report = TMMMethodResearchAdapter(online_client=client).research(
        {"problem_id": "snippet-fallback"},
        explicit_queries=["thin film design"],
    )

    assert any(item.content_depth == MethodContentDepth.abstract for item in report.evidence)
    assert report.status == MethodResearchStatus.partial
    assert any("s2_snippet_unavailable" in reason for reason in report.reasons)


def test_online_method_research_stops_at_its_own_stage_budget() -> None:
    now = [0.0]

    class SlowClient:
        def search_s2(self, query: str, *, limit: int) -> list[Any]:
            del query, limit
            now[0] = 6.0
            return []

        def search_openalex(self, query: str, *, limit: int) -> list[Any]:
            del query, limit
            return []

    report = TMMMethodResearchAdapter(
        online_client=SlowClient(),
        online_wall_time_seconds=5.0,
        clock=lambda: now[0],
    ).research(
        {"problem_id": "stage-budget"},
        explicit_queries=["query one multilayer", "query two multilayer"],
    )
    assert report.telemetry.online_budget_exhausted is True
    assert report.telemetry.online_queries_skipped_budget == 1
    assert "online_method_research_time_budget_exhausted" in report.reasons


def test_evidence_budget_satisfied_keeps_completed_status() -> None:
    def guidance_record() -> dict[str, Any]:
        return {
            "chunk_id": "chunk-s2-1",
            "paper_id": "paper-s2",
            "title": "Chirped multilayer broadband reflector design",
            "text": (
                "A chirped multilayer varies the optical thickness of "
                "successive high- and low-index layers to distribute Bragg "
                "conditions across a broad spectral interval. The design was "
                "optimized with transfer-matrix calculations under oblique "
                "incidence, and bounded thickness perturbations were "
                "included. This method provides a reusable route for "
                "broadband dielectric reflector design."
            ),
            "content_depth": "s2_snippet",
            "section": "Methods",
        }

    class RichS2Client:
        def search_s2(self, query: str, *, limit: int) -> list[Any]:
            del query, limit
            return [guidance_record()]

        def search_openalex(self, query: str, *, limit: int) -> list[Any]:
            del query, limit
            return []

    def synthesis_callback(evidence: list[Any], queries: list[Any]):
        del queries

        def evidence_id(item: Any) -> str:
            if isinstance(item, Mapping):
                return str(item.get("evidence_id") or "")
            return str(getattr(item, "evidence_id", "") or "")

        return [
            {
                "method_name": "Bounded multilayer optimization",
                "reusable_principle": (
                    "Bounded multilayer optimization is a reusable method."
                ),
                "applicability": "TMM thin-film design.",
                "limitations": "Nominal design only.",
                "evidence_ids": [
                    evidence_id(item) for item in evidence
                ],
            }
        ]

    report = TMMMethodResearchAdapter(
        online_client=RichS2Client(),
        minimum_online_queries=1,
        maximum_method_guidance_evidence=1,
        synthesis_callback=synthesis_callback,
    ).research(
        {"problem_id": "budget-satisfied"},
        explicit_queries=[
            "q1 chirped multilayer reflector",
            "q2 transfer matrix optimization",
            "q3 oblique incidence multilayer",
        ],
    )
    assert "online_method_evidence_budget_satisfied" in report.reasons
    assert report.telemetry["online_queries_skipped_budget"] == 2
    assert report.status == MethodResearchStatus.completed
    assert report.evidence
    assert report.method_findings


def test_qwen_method_synthesizer_is_bounded_and_tracks_usage() -> None:
    class FakeClient:
        def call(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
            assert len(messages) == 2
            assert kwargs["max_tokens"] == 3000
            payload = method_research.json.loads(messages[1]["content"])
            assert payload["allowed_evidence_ids"] == ["ev-snippet"]
            return {
                "content": method_research.json.dumps(
                    {
                        "method_findings": [
                            {
                                "design_family": "chirped dielectric multilayer",
                                "method_name": "chirped dielectric multilayer",
                                "reusable_principle": "Distribute optical thicknesses across the stack.",
                                "evidence_ids": ["ev-snippet"],
                                "applicability": "Broadband planar reflectors.",
                                "limitations": "Material dispersion and tolerance require TMM verification.",
                                "confidence": 0.8,
                            }
                        ]
                    }
                ),
                "_llm_usage": {
                    "model_name": "qwen3.7-flash",
                    "input_tokens": 120,
                    "output_tokens": 60,
                },
            }

    evidence = MethodEvidence(
        evidence_id="ev-snippet",
        paper_id="paper-1",
        title="Chirped multilayer",
        source_route="s2_snippet_search",
        content_depth=MethodContentDepth.s2_snippet,
        text="A chirped multilayer distributes optical thickness across a stack.",
        query_ids=["q1"],
        allowed_use=MethodAllowedUse.method_guidance,
    )
    synthesizer = QwenMethodFindingSynthesizer(client=FakeClient())
    findings = synthesizer(
        [evidence],
        [MethodResearchQuery(query_id="q1", query_text="chirped multilayer", purpose="design_family")],
    )

    assert findings[0].evidence_ids == ["ev-snippet"]
    assert synthesizer.drain_usage()[0]["model_name"] == "qwen3.7-flash"
    assert synthesizer.drain_usage() == []


def test_tmm_scope_gate_rejects_semantic_neighbors_outside_layered_domain() -> None:
    query = "dielectric multilayer reflector oblique incidence robust optimization"
    assert method_research._tmm_method_scope_match(
        query=query,
        title="Robust optimization of dielectric multilayer coatings",
        section="Methods",
        text=(
            "A transfer matrix model optimizes layer thicknesses while comparing "
            "reflectance at normal and oblique incidence."
        ),
    )
    assert not method_research._tmm_method_scope_match(
        query=query,
        title="Cylindrical cloaking at oblique incidence with optimized finite multilayers",
        section="Optimization",
        text="A cylindrical cloak is optimized for oblique incidence.",
    )
    assert not method_research._tmm_method_scope_match(
        query=query,
        title="Antenna array optimization",
        section="Methods",
        text="A convex optimizer controls a directional antenna array.",
    )
    assert not method_research._tmm_method_scope_match(
        query=query,
        title="Full-wave inverse design",
        section="Discussion",
        text=(
            "Future work includes considering multilayer problems and transfer "
            "matrix optimization with particle swarm methods."
        ),
    )
    assert not method_research._tmm_method_scope_match(
        query="1550 nm multilayer bandpass filter thickness optimization",
        title="Simulation-based optimization of a multilayer neutron detector",
        section="Methods",
        text="The layer thickness and detector count were optimized for neutrons.",
    )
    assert not method_research._tmm_method_scope_match(
        query="multilayer material selection for an optical filter",
        title="Network-based model of eco-tourism",
        section="Results",
        text="A multilayer network topology was optimized for tourism services.",
    )


def test_method_finding_repairs_json_damaged_latex_controls() -> None:
    finding = MethodFinding(
        method="quarter-wave coating",
        reusable_principle="Use $d = \theta/(4n)$ and $n = \text{sqrt}(n_s)$.",
        evidence_ids=["ev1"],
        applicability="Single-layer baselines.",
        limitations="Requires independent verification.",
    )

    assert "\t" not in finding.reusable_principle
    assert r"\theta" in finding.reusable_principle
    assert r"\text" in finding.reusable_principle
