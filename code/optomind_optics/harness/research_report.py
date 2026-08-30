"""Traceable final reporting for the TMM research-design harness."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .research_feedback import ResearchFeedbackDecision, ResearchIterationObservation
from .text_safety import repair_scientific_payload


class TMMResearchAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = "tmm-research-answer.v1"
    status: str
    problem_id: str
    problem_interpretation: str
    method_findings: tuple[dict[str, Any], ...] = ()
    route_summaries: tuple[dict[str, Any], ...] = ()
    recommended_candidates: tuple[dict[str, Any], ...] = ()
    failed_or_limited_routes: tuple[dict[str, Any], ...] = ()
    stop_decision: dict[str, Any] = Field(default_factory=dict)
    references: tuple[dict[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    artifact_index: tuple[str, ...] = ()
    markdown: str


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value or {})


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def _reported_metrics(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    report = candidate.get("objective_report") or {}
    attainment = report.get("target_attainment") or {}
    metrics: list[dict[str, Any]] = []
    if not isinstance(attainment, Mapping):
        return metrics
    for name, raw in attainment.items():
        if not isinstance(raw, Mapping):
            continue
        metrics.append(
            {
                "name": str(name),
                "observable": raw.get("observable") or raw.get("metric"),
                "observed": raw.get("observed"),
                "target": raw.get("target"),
                "constraint": raw.get("constraint"),
                "aggregation": raw.get("aggregation"),
                "weight": raw.get("weight"),
                "role": raw.get("role"),
            }
        )
    return metrics


def _robustness_metrics(candidate: Mapping[str, Any]) -> dict[str, Any]:
    report = candidate.get("robustness_report") or {}
    if not isinstance(report, Mapping):
        return {}
    return {
        "perturbation_model": dict(report.get("perturbation_model") or {}),
        "nominal_spectral_metrics": dict(
            report.get("nominal_spectral_metrics") or {}
        ),
        "spectral_metric_summary": dict(report.get("spectral_metric_summary") or {}),
        "failed_simulations": int(report.get("failed_simulations") or 0),
    }


def _objective_signature(candidate: Mapping[str, Any]) -> tuple[Any, ...] | None:
    metrics = _reported_metrics(candidate)
    if not metrics:
        return None
    signature: list[tuple[Any, ...]] = []
    for metric in metrics:
        if (
            metric.get("target") is None
            or metric.get("constraint") not in {"at_least", "at_most", "match"}
            or not metric.get("aggregation")
        ):
            return None
        signature.append(
            (
                metric.get("name"),
                metric.get("observable"),
                float(metric["target"]),
                metric.get("constraint"),
                metric.get("aggregation"),
                float(metric.get("weight") or 1.0),
            )
        )
    return tuple(sorted(signature, key=str))


def _frozen_rank_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    """Order candidates by the run's frozen score, unscoreable ones last.

    A candidate whose measurements went missing sorts to the bottom rather than
    being treated as a zero, because zero is a legitimate score and the two
    situations call for different reading.
    """

    value = candidate.get("frozen_score")
    return (
        0 if value is not None else 1,
        -float(value) if value is not None else 0.0,
        -float(candidate.get("robustness_score") or 0.0),
        -float(candidate.get("simplicity_score") or 0.0),
        str(candidate.get("candidate_key") or candidate.get("candidate_id") or ""),
    )


def _shared_contract_summary(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metrics = _reported_metrics(candidate)
    mean_r = [
        float(item["observed"])
        for item in metrics
        if item.get("observable") == "R"
        and item.get("aggregation") == "mean"
        and item.get("observed") is not None
    ]
    worst_r = [
        float(item["observed"])
        for item in metrics
        if item.get("observable") == "R"
        and item.get("aggregation") == "worst_case"
        and item.get("observed") is not None
    ]
    met = 0
    assessed = 0
    for item in metrics:
        if item.get("observed") is None or item.get("target") is None:
            continue
        assessed += 1
        observed = float(item["observed"])
        target = float(item["target"])
        constraint = str(item.get("constraint") or "")
        met += int(
            (constraint == "at_most" and observed <= target)
            or (constraint == "at_least" and observed >= target)
            or (constraint == "match" and abs(observed - target) <= 1e-9)
        )
    return {
        "mean_reflectance_across_channels": (
            sum(mean_r) / len(mean_r) if mean_r else None
        ),
        "worst_channel_mean_reflectance": max(mean_r) if mean_r else None,
        "worst_point_reflectance": max(worst_r) if worst_r else None,
        "target_clauses_met": met,
        "target_clauses_assessed": assessed,
    }


def _percent(value: Any, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{100.0 * float(value):.{digits}f}%"


class DeterministicTMMResearchReporter:
    """Build a factual report without inventing literature or solver results."""

    @staticmethod
    def _metric_text(metric: Mapping[str, Any]) -> str:
        observed = metric.get("observed")
        target = metric.get("target")
        constraint = str(metric.get("constraint") or "")
        attainment = ""
        if observed is not None and target is not None and constraint in {
            "at_least",
            "at_most",
            "match",
        }:
            value = float(observed)
            threshold = float(target)
            met = (
                value >= threshold
                if constraint == "at_least"
                else value <= threshold
                if constraint == "at_most"
                else abs(value - threshold) <= 1e-9
            )
            attainment = (
                f" ({constraint} {_fmt(target, 6)}; "
                f"{'met' if met else 'trade-off'})"
            )
        return f"{metric.get('name')}={_fmt(observed, 6)}{attainment}"

    def build(
        self,
        *,
        problem_analysis: Mapping[str, Any] | BaseModel,
        method_research: Mapping[str, Any] | BaseModel,
        strategy_plan: Mapping[str, Any] | BaseModel,
        iterations: Iterable[ResearchIterationObservation],
        stop_decision: ResearchFeedbackDecision,
        status: str,
        scoring_standard: Any | None = None,
    ) -> TMMResearchAnswer:
        # The reporter is also a trust boundary: replayed or migrated runs can
        # arrive as raw dictionaries and therefore bypass the Pydantic repair
        # hooks used by current model-facing contracts.
        problem = dict(repair_scientific_payload(_mapping(problem_analysis)))
        research = dict(repair_scientific_payload(_mapping(method_research)))
        plan = dict(repair_scientific_payload(_mapping(strategy_plan)))
        observations = list(iterations)
        routes_by_id = {
            str(route.get("route_id") or ""): dict(route)
            for route in plan.get("routes", []) or []
            if isinstance(route, Mapping)
        }
        raw_route_sources = plan.get("route_sources")
        route_sources = (
            {
                str(route_id): str(source)
                for route_id, source in raw_route_sources.items()
            }
            if isinstance(raw_route_sources, Mapping)
            else {}
        )
        candidates: list[dict[str, Any]] = []
        route_summaries: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        artifact_index: list[str] = []
        for row in observations:
            route = routes_by_id.get(row.route_id, {})
            route_summary = {
                "iteration_id": row.iteration_id,
                "route_id": row.route_id,
                "route_title": row.route_title,
                "route_source": route_sources.get(row.route_id, "planned"),
                "scientific_hypothesis": route.get("scientific_hypothesis", ""),
                "run_status": row.run_status,
                "physically_valid_candidate_count": row.physically_valid_candidate_count,
                "best_target_score": row.best_target_score,
                "best_robustness_score": row.best_robustness_score,
                "failure_categories": list(row.failure_categories),
            }
            route_summaries.append(route_summary)
            if row.physically_valid_candidate_count <= 0:
                failed.append(route_summary)
            for candidate in row.candidate_summaries:
                item = dict(candidate)
                item["route_id"] = row.route_id
                item["route_title"] = row.route_title
                item["iteration_id"] = row.iteration_id
                item["reported_metrics"] = _reported_metrics(item)
                item["reported_robustness"] = _robustness_metrics(item)
                if scoring_standard is not None:
                    # The run's own criteria, applied to the measurements this
                    # candidate actually produced.  Recorded per candidate so a
                    # reader can recompute the leaderboard from the artifact.
                    outcome = scoring_standard.score(item.get("objective_report") or {})
                    item["frozen_score"] = outcome.value if outcome.ok else None
                    item["frozen_score_inputs"] = dict(outcome.values)
                    item["frozen_score_missing"] = list(outcome.missing)
                candidates.append(item)
            for path in (row.task_path, row.result_path):
                if path:
                    artifact_index.append(path)

        # Deduplicate candidates generated by repeated reporting while retaining
        # the strongest verified observation for each stable candidate id.
        by_candidate: dict[str, dict[str, Any]] = {}
        for item in candidates:
            candidate_key = (
                f"{item.get('iteration_id') or 'iteration'}::"
                f"{item.get('candidate_id') or 'candidate'}"
            )
            item["candidate_key"] = candidate_key
            previous = by_candidate.get(candidate_key)
            if previous is None:
                by_candidate[candidate_key] = item
            elif scoring_standard is not None:
                if _frozen_rank_key(item) < _frozen_rank_key(previous):
                    by_candidate[candidate_key] = item
            elif float(item.get("target_score") or 0.0) > float(
                previous.get("target_score") or 0.0
            ):
                by_candidate[candidate_key] = item
        selected_pool: list[dict[str, Any]] = []
        for row in observations:
            route_items = [
                item
                for item in by_candidate.values()
                if item.get("iteration_id") == row.iteration_id
            ]
            selected_ids = set(row.selected_candidate_ids)
            selected_items = [
                item
                for item in route_items
                if str(item.get("candidate_id") or "") in selected_ids
            ]
            if not selected_items:
                selected_items = route_items
            if scoring_standard is not None:
                # Each route sends its best representative under the run's own
                # criteria, not under the targets that route happened to declare.
                selected_items.sort(key=_frozen_rank_key)
            else:
                selected_items.sort(
                    key=lambda item: (
                        -float(item.get("target_score") or 0.0),
                        -float(item.get("robustness_score") or 0.0),
                        -float(item.get("simplicity_score") or 0.0),
                        str(item.get("candidate_id") or ""),
                    )
                )
            selected_pool.extend(selected_items[:3])

        signatures = {
            signature
            for item in selected_pool
            for signature in [_objective_signature(item)]
            if signature is not None
        }
        # Two different grounds for a cross-route leaderboard, and only one
        # applies.  A frozen standard makes the rows comparable by construction:
        # every route was measured on the same quantities before any of them
        # ran.  Without one, the rows are comparable only if the routes happen
        # to share an identical target contract.
        #
        # Having a standard is not the same as having applied it.  When no
        # candidate produced a row the standard could locate, every frozen_score
        # is None, the "ranking" is the tie-break order with nothing to break
        # ties on, and the report would still tell the reader the rows were
        # measured on one fixed expression -- a claim about measurements that
        # were never made.  At least one real score is required before the run
        # says the standard ranked anything.
        scoreable = [
            item for item in selected_pool if item.get("frozen_score") is not None
        ]
        frozen_ranking = (
            scoring_standard is not None and bool(selected_pool) and bool(scoreable)
        )
        # A standard that was established, applied, and located nothing.  Kept
        # apart from "no standard at all", because the honest sentence differs:
        # one run never had criteria, the other had them and could not measure
        # them, and only the second one points at a defect worth chasing.
        frozen_unscoreable = (
            scoring_standard is not None and bool(selected_pool) and not scoreable
        )
        shared_contract = (
            scoring_standard is None
            and
            not frozen_ranking
            and bool(selected_pool)
            and len(signatures) == 1
            and all(_objective_signature(item) is not None for item in selected_pool)
        )
        comparable = frozen_ranking or shared_contract
        for item in selected_pool:
            item["ranking_scope"] = (
                "frozen_scoring_standard"
                if frozen_ranking
                else "shared_user_contract"
                if shared_contract
                else "frozen_standard_unscoreable"
                if frozen_unscoreable
                else "within_route_only"
            )
            item["shared_contract_summary"] = _shared_contract_summary(item)
        if frozen_ranking:
            ranked = sorted(selected_pool, key=_frozen_rank_key)
            for index, item in enumerate(ranked, 1):
                item["cross_route_rank"] = index
        elif shared_contract:
            ranked = sorted(
                selected_pool,
                key=lambda item: (
                    -float(item.get("target_score") or 0.0),
                    -float(item.get("robustness_score") or 0.0),
                    -float(item.get("simplicity_score") or 0.0),
                    str(item.get("candidate_key") or ""),
                ),
            )
            for index, item in enumerate(ranked, 1):
                item["cross_route_rank"] = index
        else:
            ranked = selected_pool

        recommendation_roles: dict[str, dict[str, Any]] = {}
        if comparable and ranked:
            performance_field = "frozen_score" if frozen_ranking else "target_score"
            role_specs = (
                ("best_performance", performance_field),
                ("most_robust", "robustness_score"),
                ("simplest", "simplicity_score"),
            )
            for role, field_name in role_specs:
                eligible = [item for item in ranked if item.get(field_name) is not None]
                if not eligible:
                    continue
                chosen = max(
                    eligible,
                    key=lambda item: (
                        float(item.get(field_name) or 0.0),
                        float(item.get(performance_field) or 0.0),
                        str(item.get("candidate_key") or ""),
                    ),
                )
                recommendation_roles[role] = chosen
                chosen.setdefault("recommendation_roles", []).append(role)

        used_evidence = {
            str(evidence_id)
            for route in routes_by_id.values()
            for evidence_id in route.get("evidence_ids", []) or []
        }
        references = []
        for item in research.get("evidence", []) or []:
            if not isinstance(item, Mapping):
                continue
            if str(item.get("evidence_id") or "") not in used_evidence:
                continue
            references.append(
                {
                    key: item.get(key)
                    for key in (
                        "evidence_id",
                        "paper_id",
                        "title",
                        "doi",
                        "year",
                        "source_route",
                        "content_depth",
                        "allowed_use",
                    )
                }
            )

        limitations = list(problem.get("ambiguities") or [])
        limitations.extend(plan.get("unresolved_decisions") or [])
        if any(row.failure_categories for row in observations):
            limitations.append("Some planned routes produced recorded failures or capability limits; see the iteration artifacts.")
        if not references:
            limitations.append("No traceable literature item directly influenced the executed route; the result is theory-guided.")
        if status == "needs_higher_fidelity":
            limitations.append("The requested physics exceeds the declared TMM-only capability boundary.")

        normalized = str(
            problem.get("normalized_request_english")
            or problem.get("original_request")
            or ""
        )
        lines = [
            "# TMM research and design result",
            "",
            "## Problem interpretation",
            "",
            normalized,
            "",
            "## Method research",
            "",
        ]
        findings = [dict(item) for item in research.get("method_findings", []) or [] if isinstance(item, Mapping)]
        if findings:
            for item in findings:
                lines.append(
                    f"- **{item.get('name') or item.get('method_name') or 'Method'}**: "
                    f"{item.get('reusable_principle') or item.get('principle') or item.get('summary') or ''}"
                )
        else:
            lines.append("- No reusable literature method was available; planning used explicit optical theory assumptions.")
        lines.extend(["", "## Routes executed", ""])
        for item in route_summaries:
            lines.append(
                f"- **{item['route_title']}** — status `{item['run_status']}`, "
                f"source `{item['route_source']}`, "
                f"verified candidates {item['physically_valid_candidate_count']}, "
                f"best soft score {_fmt(item['best_target_score'])}."
            )
        source_comparison = plan.get("planning_source_comparison")
        if isinstance(source_comparison, Mapping):
            lines.extend(["", "## Planning-source comparison", ""])
            lines.append(
                "The memory-only control and literature-planned routes are "
                "separated by source. Frozen-standard scores are used for a "
                "cross-source verdict only when both arms have scoreable "
                "representatives; score deltas inside a route are diagnostics."
            )
            verdict = str(
                source_comparison.get("control_vs_literature_verdict")
                or "unavailable"
            )
            lines.append(f"- Frozen-standard comparison: `{verdict}`.")
            delta = source_comparison.get(
                "frozen_score_delta_control_minus_literature"
            )
            if delta is not None:
                lines.append(
                    "- Control minus best literature frozen score: "
                    f"`{_fmt(delta, 6)}`."
                )
            for source_name, group in sorted(
                (source_comparison.get("groups") or {}).items()
            ):
                if not isinstance(group, Mapping):
                    continue
                lines.append(
                    f"- `{source_name}`: {int(group.get('route_count') or 0)} "
                    "route(s), "
                    f"{int(group.get('routes_with_verified_candidates') or 0)} "
                    "with verified candidates, "
                    f"{int(group.get('executed_rounds') or 0)} executed round(s)."
                )
        lines.extend(["", "## Recommended candidate portfolio", ""])
        if ranked:
            if comparable:
                if frozen_ranking:
                    lines.append(
                        "Every route was measured on the same quantities, chosen "
                        "from the request before any route ran, and ranked by the "
                        f"one fixed expression `{scoring_standard.formula}`, so "
                        "these rows are directly comparable."
                    )
                else:
                    lines.append(
                        "All listed routes use the same canonical user target contract, "
                        "so their soft scores and reported spectral metrics are directly comparable."
                    )
                role_labels = {
                    "best_performance": "Best performance",
                    "most_robust": "Most robust",
                    "simplest": "Simplest verified",
                }
                for role, item in recommendation_roles.items():
                    lines.append(
                        f"- **{role_labels[role]}**: `{item.get('candidate_id')}` "
                        f"from {item.get('route_title')}."
                    )
            else:
                if frozen_unscoreable:
                    missing = sorted(
                        {
                            str(name)
                            for item in selected_pool
                            for name in (item.get("frozen_score_missing") or ())
                        }
                    )
                    lines.append(
                        "A frozen scoring standard was established before any route "
                        f"ran (`{scoring_standard.formula}`), but no verified "
                        "candidate produced the measurements it needs, so no row "
                        "was scored against it and the rows below are NOT directly "
                        "comparable across routes."
                        + (
                            " Unmeasured quantities: " + ", ".join(missing[:6]) + "."
                            if missing
                            else ""
                        )
                    )
                else:
                    lines.append("Scores are used only within each route; rows from different contracts are not a global leaderboard.")
            lines.append("")
            for item in ranked[:12]:
                materials = " / ".join(item.get("layer_materials", []) or [])
                if materials:
                    lines.append(
                        f"- `{item.get('candidate_id')}` stack ({len(item.get('layer_materials', []))} layers): "
                        f"{materials}."
                    )
            lines.append("")
            score_label = (
                "Frozen standard score"
                if frozen_ranking
                else "Shared-contract soft score"
                if shared_contract
                else "Within-route soft score (unscoreable by the frozen standard)"
                if frozen_unscoreable
                else "Within-route soft score"
            )
            lines.append(f"| Candidate | Route | {score_label} | Comparable spectral summary | Robustness | Thicknesses (nm) |")
            lines.append("|---|---|---:|---|---:|---|")
            for item in ranked[:12]:
                thicknesses = ", ".join(_fmt(v, 2) for v in item.get("thicknesses_nm", [])) or "n/a"
                if frozen_ranking:
                    metric_text = "; ".join(
                        f"{name}={_fmt(value)}"
                        for name, value in (item.get("frozen_score_inputs") or {}).items()
                    ) or "; ".join(
                        f"missing {name}"
                        for name in (item.get("frozen_score_missing") or [])
                    ) or "n/a"
                elif shared_contract:
                    summary = item.get("shared_contract_summary") or {}
                    metric_text = (
                        f"mean R={_percent(summary.get('mean_reflectance_across_channels'))}; "
                        f"worst-channel mean R={_percent(summary.get('worst_channel_mean_reflectance'))}; "
                        f"worst-point R={_percent(summary.get('worst_point_reflectance'))}; "
                        f"targets={int(summary.get('target_clauses_met') or 0)}/"
                        f"{int(summary.get('target_clauses_assessed') or 0)}"
                    )
                else:
                    metric_text = "; ".join(
                        self._metric_text(metric)
                        for metric in item.get("reported_metrics", [])
                    ) or "n/a"
                score_text = _fmt(
                    item.get("frozen_score") if frozen_ranking else item.get("target_score")
                )
                lines.append(
                    f"| `{item.get('candidate_id')}` | {item.get('route_title')} | "
                    f"{score_text} | {metric_text} | "
                    f"{_fmt(item.get('robustness_score'))} | {thicknesses} |"
                )
        else:
            lines.append("No candidate passed deterministic physics verification.")
        robustness_rows = [
            item for item in ranked if item.get("reported_robustness", {}).get("spectral_metric_summary")
        ]
        if robustness_rows:
            lines.extend(["", "## Manufacturing uncertainty", ""])
            for item in robustness_rows:
                robustness = item["reported_robustness"]
                model = robustness.get("perturbation_model") or {}
                metric_summary = robustness.get("spectral_metric_summary") or {}
                compact_metrics = []
                for metric_name in sorted(metric_summary):
                    values = metric_summary.get(metric_name) or {}
                    compact_metrics.append(
                        f"{metric_name} mean={_fmt(values.get('mean'), 6)} "
                        f"± {_fmt(values.get('standard_deviation'), 6)}"
                    )
                lines.append(
                    f"- `{item.get('candidate_id')}`: model={model.get('distribution')}, "
                    f"sigma_nm={_fmt(model.get('sigma_nm'), 3)}, "
                    f"relative_fraction={_fmt(model.get('relative_fraction'), 4)}, "
                    f"common_angle_bound_deg={_fmt(model.get('angle_perturbation_deg'), 3)}, "
                    f"samples={int(model.get('samples') or 0)}, "
                    f"failed={int(robustness.get('failed_simulations') or 0)}; "
                    + "; ".join(compact_metrics)
                    + "."
                )
        lines.extend(
            [
                "",
                "## Feedback and stopping decision",
                "",
                f"The loop stopped with `{stop_decision.action}`: {stop_decision.reason}",
                "",
                "## Limitations",
                "",
            ]
        )
        for limitation in dict.fromkeys(str(value) for value in limitations if str(value).strip()):
            lines.append(f"- {limitation}")
        lines.extend(["", "## Literature provenance", ""])
        if references:
            for item in references:
                identity = item.get("doi") or item.get("paper_id") or "unknown id"
                lines.append(
                    f"- [{item.get('evidence_id')}] {item.get('title') or 'Untitled'} "
                    f"({item.get('year') or 'n.d.'}; {identity}); "
                    f"use={item.get('allowed_use') or 'unspecified'}, "
                    f"source={item.get('source_route') or 'unspecified'}, "
                    f"depth={item.get('content_depth')}."
                )
        else:
            lines.append("- No literature reference was used by the executed route.")

        return TMMResearchAnswer(
            status=status,
            problem_id=str(problem.get("problem_id") or "unknown"),
            problem_interpretation=normalized,
            method_findings=tuple(findings),
            route_summaries=tuple(route_summaries),
            recommended_candidates=tuple(ranked[:12]),
            failed_or_limited_routes=tuple(failed),
            stop_decision=stop_decision.model_dump(mode="json"),
            references=tuple(references),
            limitations=tuple(dict.fromkeys(str(v) for v in limitations if str(v).strip())),
            artifact_index=tuple(dict.fromkeys(artifact_index)),
            markdown="\n".join(lines).strip() + "\n",
        )


__all__ = ["DeterministicTMMResearchReporter", "TMMResearchAnswer"]
