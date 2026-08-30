"""T-16 smoke tests: end-to-end harness with every Qwen/engine call mocked."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

CODE_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = CODE_ROOT / "scripts" / "run_article_harness.py"
_spec = importlib.util.spec_from_file_location(
    "run_article_harness_under_test", _MODULE_PATH
)
rah = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_article_harness_under_test", rah)
_spec.loader.exec_module(rah)

CHARTER = {
    "wavelength_range_nm": [400, 700],
    "angle_range_deg": [0, 30],
    "polarization": "unpolarized",
    "objectives": ["broadband high transmission"],
    "material_whitelist": ["SiO2", "TiO2"],
    "layer_count_bounds": {"min": 1, "max": 8},
}

ARTICLE_DRAFT = (
    "# Automated Thin-Film Design Report\n\n"
    "## Abstract\nA certified coating design satisfying the charter is "
    "presented for broadband operation.\n\n"
    "## Introduction\nThe charter scopes this study.\n\n"
    "## Methods\nRoutes were compiled and simulated under the charter "
    "bounds.\n\n"
    "## Results\nBoth compiled routes completed their certified runs.\n\n"
    "## Conclusion\nThe certified route satisfies the charter bounds.\n"
)


class FakeClient:
    model_name = "qwen3.5-plus"

    def __init__(self, replies):
        self.replies = list(replies)

    def call(self, messages, *, max_tokens=4000, force_mock=None):
        reply = self.replies.pop(0) if self.replies else "{}"
        return {"content": reply, "_llm_usage": {"total_tokens": 10}}


class FakeAnalyzer:
    def analyze(self, request, *, charter=None, force_mock=None):
        return {"analysis": "ok", "problem": str(request)}


class FakeResearch:
    def research(self, problem, **kwargs):
        return {"findings": [], "evidence": []}


class FakePlanner:
    def __init__(self, routes=None, fail=False):
        self.routes = routes or [
            {"route_id": "route_a"},
            {"route_id": "route_b"},
        ]
        self.fail = fail

    def plan(self, analysis, method_research, **kwargs):
        if self.fail:
            raise RuntimeError("planner exploded")
        return {"routes": [dict(r) for r in self.routes]}


class FakeCompiler:
    def compile(self, question, *, benchmark=None, force_mock=None):
        return types.SimpleNamespace(task={"question": question})


def fake_spec_builder(compiled, *, route_id, round_k, experiment_store, charter):
    output_dir = experiment_store.ensure_round_dir(round_k, route_id)
    digest = hashlib.sha256(f"{route_id}:{round_k}".encode()).hexdigest()
    return {
        "mode": "simulate",
        "task": {"route_id": route_id},
        "output_dir": str(output_dir),
        "task_sha256": digest,
    }


def make_engine_runner(certified=True):
    def runner(spec, output_dir):
        route_id = spec["task"]["route_id"]
        payload = {
            "accepted": certified,
            "certificate_id": f"cert-{route_id}-{output_dir.name}",
            "peak_transmission": 0.93,
            "certified": certified,
            "tightest_margin": 0.42,
            "cpu_seconds": 0.05,
        }
        (Path(output_dir) / "RUN_RESULT.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        return dict(payload)

    return runner


@pytest.fixture()
def harness_env(tmp_path, monkeypatch):
    from optomind_optics.harness import experiment_store as experiment_store_module

    monkeypatch.setattr(experiment_store_module, "BASE_DIR", tmp_path)
    monkeypatch.setattr(rah, "ANALYZER_FACTORY", lambda: FakeAnalyzer())
    monkeypatch.setattr(rah, "RESEARCH_FACTORY", lambda: FakeResearch())
    monkeypatch.setattr(rah, "COMPILER_FACTORY", lambda: FakeCompiler())
    monkeypatch.setattr(rah, "SPEC_BUILDER", fake_spec_builder)
    monkeypatch.setattr(rah, "ENGINE_RUNNER", make_engine_runner(True))
    monkeypatch.setattr(
        rah, "PLANNER_FACTORY", lambda: FakePlanner()
    )
    monkeypatch.setattr(
        rah,
        "WRITING_CLIENT",
        FakeClient([ARTICLE_DRAFT]),
    )
    monkeypatch.setattr(
        rah, "TRANSLATE_CLIENT",
        FakeClient(["摘要：该涂层设计满足宽带目标。", "结论：认证路线达标。"]),
    )

    from optomind_research.runtime import article_writing_review as awr

    review_client = FakeClient(['{"findings": [], "overall_verdict": "accept"}'])
    monkeypatch.setattr(awr, "REVIEW_CLIENT_FACTORY", lambda: review_client)
    return tmp_path


def test_smoke_end_to_end(harness_env):
    result = rah.run_article_harness("宽带高透射减反膜", CHARTER, max_rounds=2)
    assert result["ok"] is True, result
    manifest = json.loads(
        Path(result["store_root"], "run_manifest.json").read_text(encoding="utf-8")
    )
    stages = manifest["stages"]
    for name in (
        "problem_analyzer", "method_research", "strategy_planner",
        "round_1", "provenance_compiler", "writing_review",
        "publication_integrity", "translation", "delivery",
    ):
        assert name in stages, f"missing stage {name}"
    assert manifest["status"] == "completed"
    assert manifest["translation_skipped"] is False
    delivered = json.loads(
        Path(result["store_root"], "delivery_manifest.json").read_text(encoding="utf-8")
    )
    # delivery_manifest tracks the seven ticket-mandated artifacts;
    # article.md / QA outputs are working files, not delivery artifacts.
    assert {a["filename"] for a in delivered["artifacts"]} >= {
        "ProvenanceLedger.json", "ClaimLedger.json",
        "replay_record.json", "run_manifest.json",
        "PHYSICS_ACCEPTANCE_CERTIFICATE.json",
    }
    assert Path(result["store_root"], "article.md").is_file()


def test_abort_on_stage_failure(harness_env):
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(rah, "PLANNER_FACTORY", lambda: FakePlanner(fail=True))
    try:
        result = rah.run_article_harness("问题", CHARTER, max_rounds=1)
    finally:
        monkeypatch.undo()
    assert result["ok"] is False
    abort_path = Path(result["store_root"], "ABORT_REPORT.json")
    assert abort_path.is_file()
    payload = json.loads(abort_path.read_text(encoding="utf-8"))
    assert payload["status"] == "aborted"
    assert "planner exploded" in payload["reason"]


def test_route_coverage_gate(harness_env, monkeypatch):
    def broken_runner(spec, output_dir):
        raise RuntimeError("engine offline")

    monkeypatch.setattr(rah, "ENGINE_RUNNER", broken_runner)
    result = rah.run_article_harness("问题", CHARTER, max_rounds=1)
    assert result["ok"] is False
    assert result["exit_code"] == 1
    failure_file = Path(result["store_root"], "ROUTE_COVERAGE_FAILURE.json")
    assert failure_file.is_file()
    payload = json.loads(failure_file.read_text(encoding="utf-8"))
    # slugify collapses the CJK problem string; route ids remain authoritative.
    assert set(payload["non_terminal_routes"]) == {"route_a", "route_b"}


def test_replay_record_fields(harness_env):
    result = rah.run_article_harness("宽带高透射减反膜", CHARTER, max_rounds=1)
    assert result["ok"] is True
    record = json.loads(
        Path(result["store_root"], "replay_record.json").read_text(encoding="utf-8")
    )
    for key in (
        "harness_git_sha", "veritmm_git_sha", "dependency_lock_hash",
        "material_db_version", "random_seeds", "prompt_versions",
        "external_api_response_hashes", "reproducibility_levels",
    ):
        assert key in record, key
    assert set(record["reproducibility_levels"]) == {
        "computational_reproducibility",
        "audit_reproducibility",
        "llm_replayability",
    }
    assert record["prompt_versions"] and "template_hash" in record["prompt_versions"][0]


def test_run_manifest_budget_snapshot(harness_env):
    result = rah.run_article_harness("宽带高透射减反膜", CHARTER, max_rounds=1)
    assert result["ok"] is True
    manifest = json.loads(
        Path(result["store_root"], "run_manifest.json").read_text(encoding="utf-8")
    )
    assert "budget_snapshot" in manifest
    assert isinstance(manifest["budget_snapshot"]["qwen_tokens"], dict)
    assert manifest["budget_snapshot"]["timestamp"]
