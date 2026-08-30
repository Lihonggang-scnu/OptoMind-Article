#!/usr/bin/env python
"""T-16: end-to-end Article harness entry point (Stage 1 -> 15 + delivery).

Orchestrates problem_analyzer -> method_research -> strategy_planner ->
[rounds: task_compiler -> veritmm_adapter -> lineage_writer ->
feedback_rule_table -> stop_controller] -> provenance_compiler ->
article_publication/article_writing_review -> publication_integrity ->
latex_renderer QA gate -> translation_module -> article_delivery.

Every external dependency sits behind a module-level factory/seam so tests
can swap Qwen clients and the simulation engine without network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CODE_ROOT = Path(__file__).resolve().parent.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from config.qwen_config import get_cost_tracker  # noqa: E402
from optomind_optics.harness.experiment_store import ExperimentStore  # noqa: E402
from optomind_optics.harness.feedback_rule_table import apply_feedback  # noqa: E402,F401
from optomind_optics.harness.stop_controller import make_stop_decision  # noqa: E402
from optomind_optics.harness.task_compiler import build_veritmm_task_spec  # noqa: E402
from optomind_optics.harness.article_delivery import (  # noqa: E402
    DeliveryCertificateError,
    package_delivery,
)

TERMINAL_ROUTE_STATES = {"passed", "failed", "stagnant"}
MAX_REVIEW_ATTEMPTS = 2

ARTICLE_TEMPLATE = (
    "# Automated Thin-Film Design Report\n\n"
    "## Abstract\n{abstract}\n\n"
    "## Introduction\nThe charter scopes this study.\n\n"
    "## Methods\nRoutes were compiled and simulated under the charter "
    "bounds.\n\n"
    "## Results\n{results}\n\n"
    "## Conclusion\n{conclusion}\n"
)
PROMPT_TEMPLATES = {
    "problem_analyzer": "Analyze the optical design problem: {problem}",
    "strategy_planner": "Propose design routes within charter bounds.",
    "article_writing": "Write the section using ONLY injected facts.",
}


# ---------------------------------------------------------------------------
# Seams (module-level, monkeypatch-friendly)
# ---------------------------------------------------------------------------
def _default_analyzer_factory():
    from optomind_optics.harness.problem_analyzer import (
        QwenTMMProblemAnalyzer,
    )

    return QwenTMMProblemAnalyzer()


def _default_research_factory():
    from optomind_optics.harness.method_research import (
        TMMMethodResearchAdapter,
    )

    return TMMMethodResearchAdapter()


def _default_planner_factory():
    from optomind_optics.harness.strategy_planner import (
        QwenTMMStrategyPlanner,
    )

    return QwenTMMStrategyPlanner()


def _default_compiler_factory():
    from optomind_optics.harness.task_compiler import QwenTMMTaskCompiler

    return QwenTMMTaskCompiler()


def _default_engine_runner(spec: dict, output_dir: Path):
    from optomind_optics.harness.veritmm_adapter import VeriTMMAdapter

    return VeriTMMAdapter().run_simulation(spec, output_dir)


def _default_spec_builder(compiled, *, route_id, round_k, experiment_store, charter):
    return build_veritmm_task_spec(
        compiled,
        route_id=route_id,
        round_k=round_k,
        experiment_store=experiment_store,
        charter=charter,
    )


ANALYZER_FACTORY = _default_analyzer_factory
RESEARCH_FACTORY = _default_research_factory
PLANNER_FACTORY = _default_planner_factory
COMPILER_FACTORY = _default_compiler_factory
ENGINE_RUNNER = _default_engine_runner
SPEC_BUILDER = _default_spec_builder
WRITING_CLIENT = None          # plus-tier client for drafting (None -> lazy)
TRANSLATE_CLIENT = None        # plus-tier client for translation
RENDERER_SEAM = None           # callable(article_md_path) -> pdf path | None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return slug[:32] or "problem"


class _ForceMockClient:
    """Wraps a real client so every call runs in offline mock mode."""

    model_name = "mock"

    def __init__(self, inner):
        self._inner = inner

    def call(self, messages, *, max_tokens=4000, **kwargs):
        kwargs["force_mock"] = True
        return self._inner.call(messages, max_tokens=max_tokens, **kwargs)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _as_dict(obj: Any) -> Any:
    if obj is None or isinstance(obj, (dict, list, str, int, float, bool)):
        return obj
    for method in ("model_dump", "to_dict", "dict"):
        getter = getattr(obj, method, None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                continue
    if is_dataclass(obj):
        return asdict(obj)
    return repr(obj)


def _stage_seed(run_id: str, stage: str) -> int:
    digest = hashlib.sha256(f"{run_id}:{stage}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def _git_sha(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=15,
            cwd=str(CODE_ROOT),
        )
        if completed.returncode == 0:
            return completed.stdout.strip() or None
    except Exception:
        pass
    return None


def _dependency_lock_hash() -> str:
    for name in ("requirements.txt", "pyproject.toml"):
        candidate = CODE_ROOT / name
        if candidate.is_file():
            return _sha256_text(candidate.read_text(encoding="utf-8"))
    return "unknown"


def _material_db_version() -> str:
    try:
        from tmm_engine import material_registry as registry

        for attr in ("MATERIAL_DB_VERSION", "DB_VERSION", "__version__"):
            version = getattr(registry, attr, None)
            if version:
                return str(version)
    except Exception:
        pass
    return "unknown"


def _extract_section(markdown: str, heading: str) -> str:
    pattern = re.compile(
        rf"^##\s*{heading}[^\n]*\n(.*?)(?=^##\s|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(str(markdown or ""))
    return match.group(1).strip() if match else ""


def _routes_from_plan(plan_output: Any) -> list[dict]:
    mapping = plan_output if isinstance(plan_output, dict) else _as_dict(plan_output)
    candidates = []
    if isinstance(mapping, dict):
        inner = mapping.get("plan", mapping)
        inner = _as_dict(inner)
        if isinstance(inner, dict):
            candidates = inner.get("routes") or []
    routes = []
    for item in candidates or []:
        item_dict = item if isinstance(item, dict) else _as_dict(item)
        if isinstance(item_dict, dict) and item_dict.get("route_id"):
            routes.append(item_dict)
    return routes


class HarnessOrchestrator:
    """Carries per-run mutable state across the fifteen stages."""

    def __init__(self, problem, constraint_hints, max_rounds, dry_run=False):
        self.problem = problem
        self.constraint_hints = dict(constraint_hints or {})
        self.max_rounds = max(int(max_rounds), 1)
        self.dry_run = bool(dry_run)
        self.problem_id = slugify(problem)
        self.run_id = str(uuid.uuid4())
        self.store = ExperimentStore(self.problem_id, self.run_id)
        self.store.root.mkdir(parents=True, exist_ok=True)
        self.tracker = get_cost_tracker()
        self.stage_seeds: dict[str, int] = {}
        self.warnings: list[str] = []
        self.route_states: dict[str, str] = {}
        self.certified_candidates: list[dict] = []

    # -- manifest ----------------------------------------------------------
    def update_manifest(self, **updates) -> dict:
        manifest = _read_json(self.store.global_artifact("run_manifest.json"))
        manifest.setdefault("problem_id", self.problem_id)
        manifest.setdefault("run_id", self.run_id)
        manifest.setdefault("created_at", _now_iso())
        manifest.setdefault("problem", self.problem)
        manifest.setdefault("stages", {})
        manifest.update(updates)
        snapshot = _as_dict(self.tracker.get_budget_snapshot())
        if isinstance(snapshot, dict):
            manifest["budget_snapshot"] = snapshot
        _write_json(self.store.global_artifact("run_manifest.json"), manifest)
        return manifest

    def mark_stage(self, name: str, status: str, **details) -> None:
        manifest = _read_json(self.store.global_artifact("run_manifest.json"))
        entry = {"status": status, "finished_at": _now_iso()}
        entry.update(details)
        manifest.setdefault("stages", {})[name] = entry
        self.update_manifest(stages=manifest["stages"])

    def abort(self, reason: str, **details) -> dict:
        report = {
            "status": "aborted",
            "reason": reason,
            "run_id": self.run_id,
            "problem_id": self.problem_id,
            "generated_at": _now_iso(),
            "warnings": self.warnings,
        }
        report.update(details)
        _write_json(self.store.global_artifact("ABORT_REPORT.json"), report)
        print(f"[harness] ABORT: {reason}", file=sys.stderr)
        return {
            "ok": False,
            "exit_code": 1,
            "reason": reason,
            "problem_id": self.problem_id,
            "run_id": self.run_id,
            "store_root": str(self.store.root),
        }

    # -- stages ------------------------------------------------------------
    def seed(self, stage: str) -> int:
        self.stage_seeds[stage] = _stage_seed(self.run_id, stage)
        return self.stage_seeds[stage]

    def run_charter_gate(self):
        charter = self.constraint_hints or None
        if charter is not None:
            from optomind_optics.harness.problem_analyzer import (
                validate_research_charter,
            )

            validate_research_charter(charter)
        return charter

    def stage_analyze(self, charter):
        analyzer = ANALYZER_FACTORY()
        analysis = analyzer.analyze(
            self.problem,
            charter=charter,
            **({"force_mock": True} if self.dry_run else {}),
        )
        self.mark_stage("problem_analyzer", "completed")
        return analysis

    def stage_research(self, analysis):
        adapter = RESEARCH_FACTORY()
        report = adapter.research(
            _as_dict(analysis),
            **({"online": False} if self.dry_run else {}),
        )
        self.mark_stage("method_research", "completed")
        return report

    def stage_plan(self, analysis, method_report, charter):
        planner = PLANNER_FACTORY()
        result = planner.plan(
            _as_dict(analysis),
            _as_dict(method_report),
            charter=charter,
            **({"force_mock": True} if self.dry_run else {}),
        )
        routes = _routes_from_plan(result)
        if not routes and self.dry_run:
            # Mock planners may emit no routes without an API key; keep the
            # pipeline verifiable with clearly-labelled synthetic routes.
            routes = [{"route_id": "dry_route_a"}, {"route_id": "dry_route_b"}]
            self.warnings.append(
                "DRY_RUN_ROUTES_SYNTHETIC: planner produced no routes"
            )
        if not routes:
            return self.abort("STRATEGY_PLAN_EMPTY: no route_id produced")
        self.mark_stage("strategy_planner", "completed", route_count=len(routes))
        return result, routes

    def run_rounds(self, compiled_question, routes, charter):
        prev_sha: dict[str, str | None] = {r["route_id"]: None for r in routes}
        last_feedback = None
        stop_decision = None
        for round_k in range(1, self.max_rounds + 1):
            self.seed(f"round_{round_k}")
            compiler = COMPILER_FACTORY()
            compiled = compiler.compile(
                compiled_question,
                **({"force_mock": True} if self.dry_run else {}),
            )
            feedback_inputs = []
            for route in routes:
                route_id = route["route_id"]
                output_dir = self.store.ensure_round_dir(round_k, route_id)
                try:
                    spec = SPEC_BUILDER(
                        compiled,
                        route_id=route_id,
                        round_k=round_k,
                        experiment_store=self.store,
                        charter=charter,
                    )
                except Exception as exc:
                    if not self.dry_run:
                        raise
                    self.warnings.append(
                        f"SPEC_BUILDER_FALLBACK: {route_id} r{round_k}: {exc}"
                    )
                    import hashlib as _hashlib

                    spec = {
                        "mode": "simulate",
                        "task": {"route_id": route_id},
                        "output_dir": str(output_dir),
                        "task_sha256": _hashlib.sha256(
                            f"{route_id}:{round_k}".encode()
                        ).hexdigest(),
                    }
                try:
                    if (
                        self.dry_run
                        and ENGINE_RUNNER is _default_engine_runner
                    ):
                        result = {
                            "certified": True,
                            "accepted": True,
                            "dry_run": True,
                            "tightest_margin": 1.0,
                            "certificate_id": f"dry-{route_id}-r{round_k}",
                            "note": "synthetic dry-run engine result",
                        }
                    else:
                        result = ENGINE_RUNNER(spec, Path(spec["output_dir"]))
                except Exception as exc:  # route stays non-terminal
                    self.warnings.append(
                        f"ENGINE_ERROR: route={route_id} round={round_k}: {exc}"
                    )
                    continue
                result_dict = _as_dict(result)
                certified = bool(result_dict.get("certified"))
                self.route_states[route_id] = "passed" if certified else "failed"
                task_sha = str(spec.get("task_sha256") or "")[:64]
                run_result = dict(result_dict) if isinstance(result_dict, dict) else {}
                run_result.setdefault("route_id", route_id)
                run_result.setdefault("accepted", certified)
                run_result.setdefault(
                    "certificate_id",
                    f"{self.problem_id}-{route_id}-r{round_k}",
                )
                _write_json(
                    self.store.artifact_path(round_k, route_id, "RUN_RESULT.json"),
                    run_result,
                )
                if certified:
                    self.certified_candidates.append(
                        {
                            "route_id": route_id,
                            "round_k": round_k,
                            "task_sha256": task_sha,
                            "tightest_margin": result_dict.get("tightest_margin"),
                            "certificate_id": run_result["certificate_id"],
                        }
                    )
                feedback_inputs.append(
                    {
                        "route_id": route_id,
                        "failure_code": (
                            None if certified
                            else str(result_dict.get("failure_code")
                                      or "OBJECTIVE_NOT_MET_PHYSICS_VALID")
                        ),
                    }
                )
                from optomind_optics.harness.lineage_writer import (
                    LineageRecord,
                    write_lineage,
                )

                reason = (
                    "initial_compile" if round_k == 1
                    else f"adjusted_after_{last_feedback.reason if last_feedback else 'prior_round'}"
                )
                record = LineageRecord(
                    round=round_k,
                    parent_round=(round_k - 1) or None,
                    parent_task_sha256=prev_sha[route_id],
                    task_sha256=task_sha or f"round{round_k}-{route_id}",
                    adjustment_reason=reason,
                )
                try:
                    write_lineage(record, output_dir)
                except Exception as exc:
                    self.warnings.append(f"LINEAGE_WARNING: {route_id}: {exc}")
                prev_sha[route_id] = record.task_sha256
            from optomind_optics.harness.feedback_rule_table import (
                apply_feedback as apply_rules,
            )

            last_feedback = apply_rules(feedback_inputs, budget_exhausted=False)
            stop_decision = make_stop_decision(
                last_feedback,
                round_k,
                self.max_rounds,
                self.certified_candidates,
                charter,
                self.tracker.get_budget_snapshot(),
                mandatory_validation_complete=bool(self.certified_candidates),
            )
            self.mark_stage(
                f"round_{round_k}", "completed",
                stop_action=stop_decision.action,
                stop_reason=stop_decision.reason,
            )
            if stop_decision.action == "stop":
                break
        return stop_decision

    def check_route_coverage(self) -> bool:
        non_terminal = sorted(
            route_id for route_id, state in self.route_states.items()
            if state not in TERMINAL_ROUTE_STATES
        ) + sorted(
            route["route_id"] for route in getattr(self, "_planned_routes", [])
            if route["route_id"] not in self.route_states
        )
        if non_terminal:
            _write_json(
                self.store.global_artifact("ROUTE_COVERAGE_FAILURE.json"),
                {
                    "status": "route_coverage_failure",
                    "non_terminal_routes": non_terminal,
                    "route_states": self.route_states,
                    "generated_at": _now_iso(),
                },
            )
            print("[harness] ROUTE COVERAGE FAILURE", file=sys.stderr)
            return False
        return True

    def stage_provenance(self, method_report, charter):
        from optomind_optics.harness.provenance_compiler import compile

        ledger, claims_ledger = compile(
            self.certified_candidates,
            self.store,
            charter,
            evidence_bundle=getattr(method_report, "evidence", None),
        )
        _write_json(
            self.store.global_artifact("ProvenanceLedger.json"),
            ledger.to_dict(),
        )
        _write_json(
            self.store.global_artifact("ClaimLedger.json"),
            claims_ledger.to_dict(),
        )
        self.mark_stage("provenance_compiler", "completed")
        return ledger, claims_ledger

    def stage_write_and_review(self, ledger, claims_ledger, charter, analysis):
        from optomind_research.runtime.article_writing_review import (
            review_article,
            run_writing_completion,
        )

        entries = list(getattr(ledger, "entries", []))
        facts_digest = "; ".join(
            f"{e.token_id}={getattr(e, 'human_readable', '')}"
            for e in entries[:30]
        )
        if self.dry_run and WRITING_CLIENT is None:
            # No API key needed: compose locally from real token ids so the
            # downstream FACT verification has genuine anchors.
            anchor = entries[0].token_id if entries else None
            abstract = (
                "This study addresses the stated charter; certified fact "
                + (f"{{{{FACT:{anchor}}}}}." if anchor else "is on file.")
            )
            article_md = ARTICLE_TEMPLATE.format(
                abstract=abstract,
                results=facts_digest or "No certified numeric facts.",
                conclusion="The certified route satisfies the charter bounds.",
            )
            article_path = self.store.global_artifact("article.md")
            article_path.write_text(article_md, encoding="utf-8")
            self.mark_stage("writing_review", "skipped_dry_run")
            return article_path
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a scientific writer. Use ONLY the provided "
                    "facts; anchor every number as {{FACT:token_id}}."
                ),
            },
            {
                "role": "user",
                "content": ARTICLE_TEMPLATE.format(
                    abstract=(
                        f"This study addresses: {self.problem}. Certified "
                        "facts: {{FACT:" + (
                            getattr(ledger.entries[0], 'token_id', 'NONE')
                            if getattr(ledger, 'entries', []) else 'NONE'
                        ) + "}}."
                    ),
                    results=facts_digest or "No certified numeric facts.",
                    conclusion=(
                        "The certified route satisfies the charter bounds."
                    ),
                ),
            },
        ]
        response = run_writing_completion(messages, client=WRITING_CLIENT)
        article_md = str(response.get("content") or "").strip()
        if not article_md:
            return self.abort("ARTICLE_DRAFT_EMPTY: writer returned no content")
        article_path = self.store.global_artifact("article.md")
        article_path.write_text(article_md, encoding="utf-8")

        for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
            report = review_article(article_md, ledger, claims_ledger, charter)
            if getattr(report, "overall_verdict", "") == "accept":
                self.mark_stage("writing_review", "completed",
                                review_attempts=attempt)
                return article_path
            if attempt < MAX_REVIEW_ATTEMPTS:
                revision_messages = messages + [
                    {"role": "assistant", "content": article_md},
                    {
                        "role": "user",
                        "content": "Revise addressing: "
                        + json.dumps([f.to_dict() for f in report.findings],
                                     ensure_ascii=False),
                    },
                ]
                response = run_writing_completion(
                    revision_messages, client=WRITING_CLIENT
                )
                article_md = str(response.get("content") or "").strip()
                if article_md:
                    article_path.write_text(article_md, encoding="utf-8")
        return self.abort(
            "REVIEW_NOT_PASSED",
            final_verdict=getattr(report, "overall_verdict", "unknown"),
        )

    def stage_integrity(self, article_path, ledger, claims_ledger):
        from optomind_research.runtime.publication_integrity import (
            verify_claim_coverage,
            verify_fact_tokens,
        )

        article_md = article_path.read_text(encoding="utf-8")
        fact_report = verify_fact_tokens(article_md, ledger)
        coverage_report = verify_claim_coverage(article_md, claims_ledger)
        self.mark_stage(
            "publication_integrity", "completed",
            fact_mismatches=fact_report.mismatches,
            bare_number_warnings=len(fact_report.warnings),
            unsupported_claims=coverage_report.unsupported_count,
        )
        if fact_report.mismatches:
            return self.abort(
                "FACT_TOKEN_MISMATCH",
                mismatches=fact_report.mismatches,
            )
        self.warnings.extend(fact_report.warnings[:20])
        self.warnings.extend(coverage_report.warnings[:20])
        return None

    def stage_qa_gate(self, article_path):
        if RENDERER_SEAM is None:
            self.warnings.append(
                "PDF_RENDER_SKIPPED: no renderer seam configured"
            )
            self.mark_stage("qa_gate", "skipped")
            return None
        pdf_path = RENDERER_SEAM(article_path)
        if pdf_path is None or not Path(pdf_path).is_file():
            self.warnings.append("PDF_RENDER_SKIPPED: seam produced no PDF")
            self.mark_stage("qa_gate", "skipped")
            return None
        try:
            from optomind_research.runtime.latex_publication_renderer import (
                run_qa_gate,
            )

            run_qa_gate(Path(pdf_path), article_path, self.store.root)
        except Exception as exc:
            report_ref = self.store.global_artifact("QA_FAILURE_REPORT.json")
            return self.abort(
                "QA_GATE_FAILED",
                detail=str(exc),
                report=str(report_ref),
            )
        self.mark_stage("qa_gate", "completed", pdf=str(pdf_path))
        return pdf_path

    def stage_translate(self, article_path, ledger):
        from optomind_optics.harness.translation_module import (
            translate_sections,
        )

        article_md = article_path.read_text(encoding="utf-8")
        translate_client = TRANSLATE_CLIENT
        if self.dry_run and translate_client is None:
            from optomind_optics.harness.problem_analyzer import (
                ArticlePlusQwenClient,
            )

            translate_client = _ForceMockClient(ArticlePlusQwenClient())
        result = translate_sections(
            _extract_section(article_md, "Abstract"),
            _extract_section(article_md, "Conclusion"),
            ledger,
            client=translate_client,
        )
        zh_parts = []
        if result.abstract_zh:
            zh_parts.append("# 摘要\n\n" + result.abstract_zh)
        if result.conclusion_zh:
            zh_parts.append("# 结论\n\n" + result.conclusion_zh)
        if zh_parts:
            (self.store.global_artifact("article_zh.md")).write_text(
                "\n\n".join(zh_parts) + "\n", encoding="utf-8"
            )
        self.mark_stage(
            "translation", "skipped" if result.skipped else "completed",
            mismatch_values=result.mismatch_values,
        )
        self.translation_skipped = bool(result.skipped)
        self.warnings.extend(
            f"TRANSLATION_MISMATCH: {entry}"
            for entry in result.mismatch_values[:20]
        )
        return result

    def stage_delivery(self, charter) -> dict:
        accepted = bool(self.certified_candidates)
        certificate = {
            "accepted": accepted,
            "dry_run": self.dry_run,
            "best_route": (
                self.certified_candidates[0]["route_id"]
                if self.certified_candidates else None
            ),
            "candidates": self.certified_candidates,
            "generated_at": _now_iso(),
        }
        _write_json(
            self.store.global_artifact("PHYSICS_ACCEPTANCE_CERTIFICATE.json"),
            certificate,
        )
        self.write_replay_record(charter)
        manifest = self.update_manifest(
            translation_skipped=getattr(self, "translation_skipped", False),
            warnings=self.warnings,
        )
        try:
            package = package_delivery(manifest, self.store.root)
        except DeliveryCertificateError as exc:
            return self.abort("CERTIFICATE_NOT_ACCEPTED_ERROR", detail=str(exc))
        self.mark_stage(
            "delivery", "completed",
            delivery_warnings=package.warnings,
            zip=str(package.zip_path) if package.zip_path else None,
        )
        return {"ok": True, "package": package}

    def write_replay_record(self, charter) -> None:
        lock_hash = _dependency_lock_hash()
        record = {
            "harness_git_sha": _git_sha("rev-parse", "HEAD"),
            "veritmm_git_sha": _git_sha("-C", "veritmm", "rev-parse", "HEAD"),
            "dependency_lock_hash": lock_hash,
            "material_db_version": _material_db_version(),
            "random_seeds": {
                stage: seed for stage, seed in sorted(self.stage_seeds.items())
            },
            "prompt_versions": [
                {
                    "stage": stage,
                    "template_hash": _sha256_text(template),
                    "model_id": "qwen-plus",
                    "temperature": 0.7,
                }
                for stage, template in sorted(PROMPT_TEMPLATES.items())
            ],
            "external_api_response_hashes": {
                "problem": _sha256_text(self.problem),
                "constraint_hints": _sha256_text(
                    json.dumps(self.constraint_hints, ensure_ascii=False,
                               sort_keys=True)
                ),
            },
            "reproducibility_levels": {
                "computational_reproducibility": "相同种子得相同物理结果",
                "audit_reproducibility": "可追溯每步输入输出",
                "llm_replayability": "LLM输出仅审计存档，不保证重现",
            },
        }
        _write_json(self.store.global_artifact("replay_record.json"), record)


def run_article_harness(
    problem: str,
    constraint_hints: dict | None = None,
    max_rounds: int = 5,
    *,
    dry_run: bool = False,
) -> dict:
    orchestrator = HarnessOrchestrator(
        problem, constraint_hints, max_rounds, dry_run=dry_run
    )
    o = orchestrator
    o.update_manifest(status="running")
    try:
        charter = o.run_charter_gate()
        analysis = o.stage_analyze(charter)
        method_report = o.stage_research(analysis)
        planned = o.stage_plan(analysis, method_report, charter)
        if isinstance(planned, dict) and planned.get("ok") is False:
            return planned
        _, routes = planned
        o._planned_routes = routes
        o.run_rounds(o.problem, routes, charter)
        if not o.check_route_coverage():
            return {
                "ok": False,
                "exit_code": 1,
                "reason": "route_coverage_failure",
                "problem_id": o.problem_id,
                "run_id": o.run_id,
                "store_root": str(o.store.root),
            }
        ledger, claims_ledger = o.stage_provenance(method_report, charter)
        article_path = o.stage_write_and_review(
            ledger, claims_ledger, charter, analysis
        )
        if isinstance(article_path, dict) and article_path.get("ok") is False:
            return article_path
        failure = o.stage_integrity(article_path, ledger, claims_ledger)
        if failure is not None:
            return failure
        # stage_qa_gate returns an abort dict when QA fails; must propagate so
        # the harness exits rather than continuing into translate/delivery with
        # a PDF that failed QA.
        qa_result = o.stage_qa_gate(article_path)
        if isinstance(qa_result, dict) and qa_result.get("ok") is False:
            return qa_result
        o.stage_translate(article_path, ledger)
        delivered = o.stage_delivery(charter)
        if delivered.get("ok") is False:
            return delivered
        final_manifest = o.update_manifest(status="completed")
        print(
            f"[harness] completed run {o.problem_id}/{o.run_id} "
            f"-> {o.store.root}"
        )
        return {
            "ok": True,
            "exit_code": 0,
            "problem_id": o.problem_id,
            "run_id": o.run_id,
            "store_root": str(o.store.root),
            "manifest": final_manifest,
        }
    except Exception as exc:  # any uncaught stage failure aborts cleanly
        import traceback

        return o.abort(
            f"STAGE_FAILURE: {type(exc).__name__}: {exc}",
            traceback=traceback.format_exc()[-4000:],
        )


def bounded_run(problem, constraint_hints=None, max_rounds=5,
                *, dry_run=False) -> int:
    result = run_article_harness(
        problem, constraint_hints, max_rounds, dry_run=dry_run
    )
    if not result.get("ok"):
        print(f"[harness] exit code 1 ({result.get('reason')})")
        return 1
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the OptoMind article harness end to end.",
    )
    parser.add_argument("--problem", required=True,
                        help="research problem statement")
    parser.add_argument("--constraint-hints", default=None,
                        help="path to constraint hints JSON (charter fields)")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true",
                        help="mock engine responses; no real simulation")
    args = parser.parse_args(argv)
    constraint_hints = {}
    if args.constraint_hints:
        constraint_hints = _read_json(Path(args.constraint_hints))
        if not constraint_hints:
            print(f"[harness] invalid constraint hints file: "
                  f"{args.constraint_hints}", file=sys.stderr)
            return 1
    return bounded_run(
        args.problem, constraint_hints, args.max_rounds,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
