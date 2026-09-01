"""Read-only catalog and HTTP service for completed TMM research runs.

The replay service never invokes an LLM, a literature provider, an optimizer, or
the numerical engine.  It derives an easy-to-read view from immutable artifacts
that already exist under ``outputs/tmm_research_harness``.
"""

from __future__ import annotations

import ast
import json
import mimetypes
import threading
import webbrowser
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


JsonObject = dict[str, Any]

_REQUIRED_RUN_FILES = (
    "REQUEST.json",
    "SCORING_STANDARD.json",
    "ITERATION_HISTORY.json",
    "SCORING_RANKING.json",
)

_DISPLAY_META: dict[str, dict[str, Any]] = {
    "e2e-methane-swir-window-20260828-default4-w10800": {
        "title": "甲烷泄漏巡检短波红外窗口",
        "group": 1,
        "tags": ["环境监测", "短波红外", "甲烷巡检"],
    },
    "e2e-uav-swir-window-20260829-default4-w10800": {
        "title": "高空无人机短波红外遥感窗口",
        "group": 2,
        "tags": ["无人机遥感", "短波红外", "紫外抑制"],
    },
    "e2e-space-qkd-cband-window-20260829-default4-w10800": {
        "title": "星载量子密钥分发接收窗口",
        "group": 3,
        "tags": ["量子通信", "星载光学", "C波段"],
    },
    "e2e-solarblind-uv-window-20260829-default4-w10800": {
        "title": "低轨太阳盲紫外探测滤光膜",
        "group": 4,
        "tags": ["空间探测", "太阳盲紫外", "背景抑制"],
    },
    "e2e-fifth-dualgas-mwir-20260829-default4-w10800": {
        "title": "小卫星双气体中波红外滤光膜",
        "group": 5,
        "tags": ["温室气体", "小卫星", "中波红外"],
    },
    "e2e-sixth-combustion-co-20260829-default4-w10800": {
        "title": "工业烟气一氧化碳监测滤光膜",
        "group": 6,
        "tags": ["工业监测", "燃气轮机", "中波红外"],
    },
}

_TOP_LEVEL_EVIDENCE = (
    (
        "problem",
        "研究问题与约束",
        "核对原始工程需求以及系统识别的波段、材料和制造约束。",
        ("REQUEST.json", "PROBLEM_ANALYSIS.json"),
    ),
    (
        "scoring",
        "评价标准",
        "查看由仿真能力目录约束、并在实验开始前固定的指标和公式。",
        ("SCORING_STANDARD.json", "SCORING_STANDARD.ATTESTATION.json"),
    ),
    (
        "planning",
        "研究路线",
        "比较文献启发路线与不接收文献的独立记忆对照路线。",
        (
            "METHOD_RESEARCH.json",
            "ROUTE_PLANNING.json",
            "CONTROL_ROUTE_PLANNING.json",
            "STRATEGY_PLAN.json",
        ),
    ),
    (
        "execution",
        "仿真执行",
        "逐轮核对可执行任务、候选结构、真实光谱和物理验证。",
        ("ITERATION_HISTORY.json", "RESEARCH_EVENTS.jsonl"),
    ),
    (
        "feedback",
        "结果反馈与调整",
        "查看每轮结果如何改变后续材料、结构、厚度范围和优化方法。",
        ("FEEDBACK_HISTORY.json", "ROUTE_TERMINATION_AUDIT.json"),
    ),
    (
        "result",
        "最终比较与交付",
        "依据同一冻结标准查看路线排名、冠军候选和完整研究结论。",
        (
            "SCORING_RANKING.json",
            "TOURNAMENT_SUMMARY.json",
            "RESEARCH_RESULT.json",
            "FINAL_ANSWER.md",
        ),
    ),
)

_SAFE_ARTIFACT_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".csv"}


def _load_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _source_label(source: str | None) -> str:
    if source == "llm_memory_control":
        return "独立记忆对照路线"
    if source == "literature_planned":
        return "文献启发路线"
    return "研究路线"


def _iteration_number(iteration_id: str) -> int:
    try:
        return int(iteration_id.rsplit("_", 1)[-1])
    except (TypeError, ValueError):
        return 0


def _safe_formula_value(formula: str, values: Mapping[str, float]) -> float | None:
    """Evaluate the frozen arithmetic formula without using ``eval``."""

    if not formula.strip():
        return None
    try:
        root = ast.parse(formula, mode="eval")
    except SyntaxError:
        return None

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name) and node.id in values:
            return float(values[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = visit(node.left)
            right = visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
        raise ValueError("unsupported frozen scoring expression")

    try:
        result = visit(root)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return _float(result)


def _candidate_fixed_values(
    candidate: Mapping[str, Any], variables: Sequence[str]
) -> dict[str, float]:
    report = _mapping(candidate.get("objective_report"))
    attainment = _mapping(report.get("target_attainment"))
    values: dict[str, float] = {}
    for variable in variables:
        row = _mapping(attainment.get(f"fixedscore.{variable}"))
        observed = _float(row.get("observed"))
        if observed is not None:
            values[str(variable)] = observed
    return values


def _candidate_certificate_path(candidate: Mapping[str, Any]) -> str | None:
    for artifact_id in _list(candidate.get("artifact_ids")):
        value = str(artifact_id).replace("\\", "/").lstrip("/")
        if value.endswith("PHYSICS_ACCEPTANCE_CERTIFICATE.json"):
            return value
    return None


def _compact_candidate(
    candidate: Mapping[str, Any],
    *,
    variables: Sequence[str],
    formula: str,
) -> JsonObject:
    values = _candidate_fixed_values(candidate, variables)
    score = _safe_formula_value(formula, values)
    materials = [str(item) for item in _list(candidate.get("layer_materials"))]
    thicknesses = [
        number
        for item in _list(candidate.get("thicknesses_nm"))
        if (number := _float(item)) is not None
    ]
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "experiment_id": str(candidate.get("experiment_id") or ""),
        "frozen_score": score,
        "metric_values": values,
        "route_local_score": _float(candidate.get("target_score")),
        "robustness_score": _float(candidate.get("robustness_score")),
        "simplicity_score": _float(candidate.get("simplicity_score")),
        "certificate_id": str(candidate.get("certificate_id") or ""),
        "certificate_relative_path": _candidate_certificate_path(candidate),
        "layer_count": len(thicknesses) or len(materials),
        "layer_materials": materials,
        "thicknesses_nm": thicknesses,
        "optimizer_id": candidate.get("optimizer_id"),
    }


def _best_iteration_candidate(
    row: Mapping[str, Any], *, variables: Sequence[str], formula: str
) -> JsonObject | None:
    candidates = [
        _compact_candidate(_mapping(item), variables=variables, formula=formula)
        for item in _list(row.get("candidate_summaries"))
    ]
    scoreable = [item for item in candidates if item["frozen_score"] is not None]
    if scoreable:
        return max(scoreable, key=lambda item: float(item["frozen_score"]))
    return candidates[0] if candidates else None


class ReplayCatalog:
    """Build a public, read-only projection of completed research artifacts."""

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root.resolve()
        self._cache: dict[str, JsonObject] = {}

    def discover_run_ids(self) -> list[str]:
        if not self.output_root.is_dir():
            return []
        discovered: list[tuple[int, str]] = []
        for child in self.output_root.iterdir():
            if not child.is_dir():
                continue
            if not all((child / name).is_file() for name in _REQUIRED_RUN_FILES):
                continue
            group = _int(_DISPLAY_META.get(child.name, {}).get("group"), 999)
            discovered.append((group, child.name))
        return [name for _, name in sorted(discovered, key=lambda item: item)]

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or "\\" in run_id:
            raise KeyError(run_id)
        run_dir = (self.output_root / run_id).resolve()
        if run_dir.parent != self.output_root or not run_dir.is_dir():
            raise KeyError(run_id)
        if not all((run_dir / name).is_file() for name in _REQUIRED_RUN_FILES):
            raise KeyError(run_id)
        return run_dir

    def catalog(self) -> JsonObject:
        runs = [self.get_run(run_id)["summary"] for run_id in self.discover_run_ids()]
        totals = {
            "runs": len(runs),
            "routes": sum(_int(item.get("route_count")) for item in runs),
            "iterations": sum(_int(item.get("iteration_count")) for item in runs),
            "completed_executions": sum(
                _int(item.get("completed_execution_count")) for item in runs
            ),
            "valid_candidates": sum(
                _int(item.get("physically_valid_candidate_count")) for item in runs
            ),
            "forward_evaluations": sum(
                _int(item.get("forward_evaluations")) for item in runs
            ),
        }
        return {
            "schema_version": "optomind-static-replay.v1",
            "mode": "read_only_artifact_replay",
            "notice": "本页面只读取已固化产物，不调用模型、文献服务或仿真引擎。",
            "totals": totals,
            "runs": runs,
        }

    def get_run(self, run_id: str) -> JsonObject:
        if run_id not in self._cache:
            self._cache[run_id] = self._build_run(self._run_dir(run_id))
        return self._cache[run_id]

    def resolve_artifact(self, run_id: str, relative_path: str) -> Path:
        run_dir = self._run_dir(run_id)
        normalized = relative_path.replace("\\", "/").lstrip("/")
        candidate = (run_dir / normalized).resolve()
        try:
            candidate.relative_to(run_dir)
        except ValueError as exc:
            raise FileNotFoundError(relative_path) from exc
        if not candidate.is_file() or candidate.suffix.lower() not in _SAFE_ARTIFACT_SUFFIXES:
            raise FileNotFoundError(relative_path)
        return candidate

    def _build_run(self, run_dir: Path) -> JsonObject:
        request = _mapping(_load_json(run_dir / "REQUEST.json", {}))
        problem_payload = _mapping(_load_json(run_dir / "PROBLEM_ANALYSIS.json", {}))
        problem = _mapping(problem_payload.get("analysis"))
        scoring_payload = _mapping(_load_json(run_dir / "SCORING_STANDARD.json", {}))
        standard = _mapping(scoring_payload.get("standard"))
        ranking = _mapping(_load_json(run_dir / "SCORING_RANKING.json", {}))
        tournament = _mapping(_load_json(run_dir / "TOURNAMENT_SUMMARY.json", {}))
        research_result = _mapping(_load_json(run_dir / "RESEARCH_RESULT.json", {}))
        telemetry = _mapping(research_result.get("telemetry"))
        route_plan = _mapping(_load_json(run_dir / "ROUTE_PLANNING.json", {}))
        control_plan = _mapping(
            _load_json(run_dir / "CONTROL_ROUTE_PLANNING.json", {})
        )
        history = [
            _mapping(item)
            for item in _list(_load_json(run_dir / "ITERATION_HISTORY.json", []))
        ]
        feedback_rows = [
            _mapping(item)
            for item in _list(_load_json(run_dir / "FEEDBACK_HISTORY.json", []))
        ]

        formula = str(standard.get("formula") or ranking.get("formula") or "")
        variables = [str(item) for item in _list(standard.get("formula_variables"))]
        metrics: list[JsonObject] = []
        for item in _list(standard.get("metrics")):
            metric = _mapping(item)
            variable = str(metric.get("variable") or "")
            if variable and variable not in variables:
                variables.append(variable)
            metrics.append(
                {
                    "variable": variable,
                    "metric": str(metric.get("metric") or ""),
                    "sense": str(metric.get("sense") or ""),
                    "region": dict(_mapping(metric.get("region"))),
                    "rationale": str(metric.get("rationale") or ""),
                }
            )

        route_definitions: dict[str, Mapping[str, Any]] = {}
        for planning_payload in (route_plan, control_plan):
            plan = _mapping(planning_payload.get("plan"))
            for route in _list(plan.get("routes")):
                route_row = _mapping(route)
                route_id = str(route_row.get("route_id") or "")
                if route_id:
                    route_definitions[route_id] = route_row

        route_comparison = {
            str(_mapping(item).get("route_id") or ""): _mapping(item)
            for item in _list(tournament.get("route_comparison"))
        }
        ranking_routes = {
            str(_mapping(item).get("route_id") or ""): _mapping(item)
            for item in _list(ranking.get("routes"))
        }
        feedback_by_iteration: dict[str, Mapping[str, Any]] = {}
        for index, row in enumerate(history):
            if index < len(feedback_rows):
                feedback_by_iteration[str(row.get("iteration_id") or "")] = feedback_rows[
                    index
                ]

        iteration_rows: list[JsonObject] = []
        for row in sorted(
            history, key=lambda item: _iteration_number(str(item.get("iteration_id") or ""))
        ):
            iteration_id = str(row.get("iteration_id") or "")
            route_id = str(row.get("route_id") or "")
            best = _best_iteration_candidate(row, variables=variables, formula=formula)
            feedback = feedback_by_iteration.get(iteration_id, {})
            raw_files: list[JsonObject] = []
            iteration_dir = run_dir / "iterations" / iteration_id
            for relative in (
                "ROUTE.json",
                "COMPILED_TASK.json",
                "TASK_COMPILATION.json",
                "ITERATION_OBSERVATION.json",
                "FEEDBACK_DECISION.json",
                "ROUTE.REFLECTION.json",
                "tmm_run/FINAL_RESULT.json",
            ):
                if (iteration_dir / relative).is_file():
                    raw_files.append(
                        {
                            "label": relative.rsplit("/", 1)[-1],
                            "path": f"iterations/{iteration_id}/{relative}",
                        }
                    )
            if best and best.get("certificate_relative_path"):
                certificate_relative = str(best["certificate_relative_path"])
                certificate_path = (
                    iteration_dir / "tmm_run" / certificate_relative
                )
                if certificate_path.is_file():
                    raw_files.append(
                        {
                            "label": "物理验证证书",
                            "path": (
                                f"iterations/{iteration_id}/tmm_run/"
                                f"{certificate_relative}"
                            ),
                        }
                    )
            iteration_rows.append(
                {
                    "iteration_id": iteration_id,
                    "global_iteration": _iteration_number(iteration_id),
                    "route_id": route_id,
                    "route_title": str(row.get("route_title") or ""),
                    "compilation_status": str(row.get("compilation_status") or ""),
                    "run_status": str(row.get("run_status") or ""),
                    "physically_valid_candidate_count": _int(
                        row.get("physically_valid_candidate_count")
                    ),
                    "candidate_count": len(_list(row.get("candidate_summaries"))),
                    "failure_categories": [
                        str(item) for item in _list(row.get("failure_categories"))
                    ],
                    "compilation_errors": [
                        str(item) for item in _list(row.get("compilation_errors"))
                    ],
                    "best_candidate": best,
                    "feedback": {
                        "action": str(feedback.get("action") or ""),
                        "reason": str(feedback.get("reason") or ""),
                        "observed_improvement": feedback.get("observed_improvement"),
                        "remaining_headroom": feedback.get("remaining_headroom"),
                        "preserve_candidate_ids": [
                            str(item)
                            for item in _list(feedback.get("preserve_candidate_ids"))
                        ],
                        "planner_guidance": [
                            str(item)
                            for item in _list(feedback.get("feedback_for_planner"))
                        ],
                    },
                    "raw_files": raw_files,
                }
            )

        all_route_ids: list[str] = []
        for route_id in route_definitions:
            if route_id not in all_route_ids:
                all_route_ids.append(route_id)
        for row in iteration_rows:
            route_id = str(row["route_id"])
            if route_id and route_id not in all_route_ids:
                all_route_ids.append(route_id)

        routes: list[JsonObject] = []
        winner = str(ranking.get("winner") or "")
        for route_id in all_route_ids:
            definition = route_definitions.get(route_id, {})
            comparison = route_comparison.get(route_id, {})
            ranked = ranking_routes.get(route_id, {})
            source = str(comparison.get("source") or "")
            if not source:
                source = (
                    "llm_memory_control"
                    if route_id.startswith("control_")
                    else "literature_planned"
                )
            points = [row for row in iteration_rows if row["route_id"] == route_id]
            points.sort(key=lambda item: _int(item.get("global_iteration")))
            previous_score: float | None = None
            trajectory: list[JsonObject] = []
            for route_round, point in enumerate(points, start=1):
                candidate = _mapping(point.get("best_candidate"))
                score = _float(candidate.get("frozen_score"))
                delta = (
                    score - previous_score
                    if score is not None and previous_score is not None
                    else None
                )
                point["route_round"] = route_round
                point["delta_from_previous"] = delta
                if score is not None:
                    trajectory.append(
                        {
                            "route_round": route_round,
                            "iteration_id": point["iteration_id"],
                            "score": score,
                        }
                    )
                    previous_score = score

            scores = [float(item["score"]) for item in trajectory]
            strict_increase = len(scores) >= 2 and all(
                scores[index] > scores[index - 1] for index in range(1, len(scores))
            )
            representative = _mapping(ranked.get("representative"))
            routes.append(
                {
                    "route_id": route_id,
                    "title": str(
                        definition.get("title")
                        or (points[0].get("route_title") if points else route_id)
                    ),
                    "route_kind": str(definition.get("route_kind") or ""),
                    "source": source,
                    "source_label": _source_label(source),
                    "winner": route_id == winner,
                    "rank": _int(ranked.get("rank"), 0) or None,
                    "status": str(comparison.get("status") or ""),
                    "termination_reason": str(
                        comparison.get("termination_reason") or ""
                    ),
                    "scientific_hypothesis": str(
                        definition.get("scientific_hypothesis") or ""
                    ),
                    "design_principle": str(definition.get("design_principle") or ""),
                    "materials": [
                        str(item) for item in _list(definition.get("proposed_materials"))
                    ],
                    "topology": str(definition.get("proposed_topology") or ""),
                    "evidence_ids": [
                        str(item) for item in _list(definition.get("evidence_ids"))
                    ],
                    "known_risks": [
                        str(item) for item in _list(definition.get("known_risks"))
                    ],
                    "rounds": points,
                    "trajectory": trajectory,
                    "progress": {
                        "scored_rounds": len(scores),
                        "initial_score": scores[0] if scores else None,
                        "final_score": scores[-1] if scores else None,
                        "peak_score": max(scores) if scores else None,
                        "net_change": scores[-1] - scores[0] if len(scores) >= 2 else None,
                        "net_change_percent": (
                            100.0 * (scores[-1] - scores[0]) / scores[0]
                            if len(scores) >= 2 and scores[0]
                            else None
                        ),
                        "strictly_increasing": strict_increase,
                    },
                    "representative": {
                        "candidate_id": str(representative.get("candidate_id") or ""),
                        "iteration_id": str(representative.get("iteration_id") or ""),
                        "score": _float(representative.get("score")),
                        "inputs": dict(_mapping(representative.get("inputs"))),
                    },
                    "candidates_scored": _int(ranked.get("candidates_scored")),
                    "candidates_scoreable": _int(ranked.get("candidates_scoreable")),
                }
            )

        leaderboard: list[JsonObject] = []
        for item in _list(ranking.get("leaderboard")):
            row = _mapping(item)
            route_id = str(row.get("route_id") or "")
            source = next(
                (route["source"] for route in routes if route["route_id"] == route_id),
                "",
            )
            leaderboard.append(
                {
                    "rank": _int(row.get("rank")),
                    "route_id": route_id,
                    "source": source,
                    "source_label": _source_label(source),
                    "candidate_id": str(row.get("candidate_id") or ""),
                    "score": _float(row.get("score")),
                }
            )

        champion: JsonObject | None = None
        winner_route = next((route for route in routes if route["winner"]), None)
        if winner_route:
            representative = _mapping(winner_route.get("representative"))
            champion_iteration = str(representative.get("iteration_id") or "")
            champion_id = str(representative.get("candidate_id") or "")
            champion_candidate: JsonObject | None = None
            for history_row in history:
                if str(history_row.get("iteration_id") or "") != champion_iteration:
                    continue
                for candidate in _list(history_row.get("candidate_summaries")):
                    candidate_row = _mapping(candidate)
                    if str(candidate_row.get("candidate_id") or "") == champion_id:
                        champion_candidate = _compact_candidate(
                            candidate_row, variables=variables, formula=formula
                        )
                        break
            champion = {
                "route_id": winner_route["route_id"],
                "source": winner_route["source"],
                "source_label": winner_route["source_label"],
                "candidate_id": champion_id,
                "iteration_id": champion_iteration,
                "score": _float(representative.get("score")),
                "metric_values": dict(_mapping(representative.get("inputs"))),
                "candidate": champion_candidate,
            }

        evidence: list[JsonObject] = []
        for key, label, description, file_names in _TOP_LEVEL_EVIDENCE:
            files = [
                {"label": name, "path": name}
                for name in file_names
                if (run_dir / name).is_file()
            ]
            evidence.append(
                {
                    "key": key,
                    "label": label,
                    "description": description,
                    "files": files,
                }
            )

        comparison_payload = _mapping(tournament.get("planning_source_comparison"))
        groups = _mapping(comparison_payload.get("groups"))
        source_comparison: JsonObject = {
            "valid": bool(comparison_payload.get("cross_source_comparison_valid")),
            "verdict": str(comparison_payload.get("control_vs_literature_verdict") or ""),
            "delta_control_minus_literature": _float(
                comparison_payload.get("frozen_score_delta_control_minus_literature")
            ),
            "literature_best": _mapping(
                _mapping(groups.get("literature_planned")).get(
                    "best_frozen_standard_result"
                )
            ),
            "control_best": _mapping(
                _mapping(groups.get("llm_memory_control")).get(
                    "best_frozen_standard_result"
                )
            ),
        }

        meta = _DISPLAY_META.get(run_dir.name, {})
        question = str(request.get("question") or research_result.get("question") or "")
        title = str(meta.get("title") or question[:38] or run_dir.name)
        completed_execution_count = sum(
            1 for row in iteration_rows if row["run_status"] == "completed"
        )
        valid_candidate_count = sum(
            _int(row.get("physically_valid_candidate_count")) for row in iteration_rows
        )
        summary: JsonObject = {
            "run_id": run_dir.name,
            "group": meta.get("group"),
            "title": title,
            "tags": list(meta.get("tags") or []),
            "question": question,
            "status": str(research_result.get("status") or "completed"),
            "stage": str(research_result.get("stage") or ""),
            "formula": formula,
            "metric_count": len(metrics),
            "route_count": len(routes),
            "iteration_count": len(iteration_rows),
            "completed_execution_count": completed_execution_count,
            "physically_valid_candidate_count": valid_candidate_count,
            "scoreable_candidate_count": sum(
                _int(route.get("candidates_scoreable")) for route in routes
            ),
            "forward_evaluations": _int(telemetry.get("forward_evaluations")),
            "optimizer_runs": _int(telemetry.get("optimizer_runs")),
            "qwen_calls": _int(telemetry.get("qwen_calls")),
            "wall_seconds": _float(telemetry.get("wall_seconds")),
            "estimated_qwen_cost_cny": _float(
                telemetry.get("estimated_qwen_cost_cny")
            ),
            "winner": champion,
            "source_comparison": source_comparison,
        }

        return {
            "schema_version": "optomind-static-replay-run.v1",
            "read_only": True,
            "summary": summary,
            "problem": {
                "compatibility": str(problem.get("compatibility") or ""),
                "compatibility_reason": str(problem.get("compatibility_reason") or ""),
                "wavelengths_nm": _list(problem.get("wavelengths_nm")),
                "target_observables": _list(problem.get("target_observables")),
                "materials": _list(problem.get("known_stack_materials")),
                "manufacturing_constraints": _list(
                    problem.get("manufacturing_constraints")
                ),
                "assumptions": _list(problem.get("assumptions")),
                "ambiguities": _list(problem.get("ambiguities")),
            },
            "scoring": {
                "locked": bool(standard.get("locked")),
                "formula": formula,
                "metrics": metrics,
                "metric_rationale": str(standard.get("metric_rationale") or ""),
                "formula_rationale": str(standard.get("formula_rationale") or ""),
            },
            "routes": routes,
            "leaderboard": leaderboard,
            "champion": champion,
            "source_comparison": source_comparison,
            "telemetry": dict(telemetry),
            "evidence": evidence,
        }


class ReplayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        catalog: ReplayCatalog,
        ui_root: Path,
    ) -> None:
        self.catalog = catalog
        self.ui_root = ui_root.resolve()
        super().__init__(server_address, ReplayRequestHandler)


class ReplayRequestHandler(BaseHTTPRequestHandler):
    server: ReplayHTTPServer

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(
        self, payload: bytes, *, content_type: str, status: int = HTTPStatus.OK
    ) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send_bytes(raw, content_type="application/json; charset=utf-8", status=status)

    def _send_error_json(self, status: int, message: str) -> None:
        self._send_json({"error": message, "status": status}, status=status)

    def _serve_ui_file(self, relative: str) -> None:
        root = self.server.ui_root
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "页面资源不存在")
            return
        if not candidate.is_file():
            self._send_error_json(HTTPStatus.NOT_FOUND, "页面资源不存在")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self._send_bytes(candidate.read_bytes(), content_type=content_type)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/healthz":
            self._send_json({"status": "ok", "mode": "read_only"})
            return
        if path == "/api/catalog":
            self._send_json(self.server.catalog.catalog())
            return
        if path.startswith("/api/runs/"):
            run_id = path[len("/api/runs/") :].strip("/")
            try:
                payload = self.server.catalog.get_run(run_id)
            except KeyError:
                self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该运行记录")
                return
            self._send_json(payload)
            return
        if path.startswith("/artifacts/"):
            remainder = path[len("/artifacts/") :]
            run_id, separator, relative = remainder.partition("/")
            if not separator or not relative:
                self._send_error_json(HTTPStatus.NOT_FOUND, "产物路径不完整")
                return
            try:
                artifact = self.server.catalog.resolve_artifact(run_id, relative)
            except (KeyError, FileNotFoundError):
                self._send_error_json(HTTPStatus.NOT_FOUND, "未找到该只读产物")
                return
            content_type = mimetypes.guess_type(artifact.name)[0] or "text/plain"
            if content_type.startswith("text/") or content_type == "application/json":
                content_type += "; charset=utf-8"
            self._send_bytes(artifact.read_bytes(), content_type=content_type)
            return
        if path in {"/", "/index.html"}:
            self._serve_ui_file("index.html")
            return
        if path.startswith("/assets/"):
            self._serve_ui_file(path.lstrip("/"))
            return
        self._send_error_json(HTTPStatus.NOT_FOUND, "页面不存在")

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the local server quiet except for errors printed by the runtime.
        return


def serve_static_replay(
    *,
    output_root: Path,
    ui_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    catalog = ReplayCatalog(output_root)
    run_count = len(catalog.discover_run_ids())
    if run_count == 0:
        raise RuntimeError(f"未在 {output_root} 发现可回放的完整运行")
    if not (ui_root / "index.html").is_file():
        raise RuntimeError(f"未找到静态回放页面：{ui_root}")
    try:
        server = ReplayHTTPServer((host, port), catalog, ui_root)
    except OSError:
        if port == 0:
            raise
        server = ReplayHTTPServer((host, 0), catalog, ui_root)
        print(f"端口 {port} 不可用，已自动选择本机可用端口。")
    url = f"http://{host}:{server.server_port}/"
    print(f"OptoMind 静态回放台已启动：{url}")
    print(f"只读加载 {run_count} 组运行；不会调用模型或重新执行仿真。")
    if open_browser:
        threading.Timer(0.35, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = [
    "ReplayCatalog",
    "ReplayHTTPServer",
    "ReplayRequestHandler",
    "serve_static_replay",
]
