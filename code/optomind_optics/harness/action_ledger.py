"""O-05: ActionEffectLedger -- action -> observation -> belief update.

Every feedback decision point records one structured entry: the pre-registered
expectation, the execution trace, the measured effect (program-computed), a
conditional scientific belief record, kept separate from action utility, and the
decision. Entries chain by stable state hashes.

The Robin-style INSIGHT_APPENDAGE re-flows verified results into the next
round's candidate generation as template-generated text: no LLM-authored
numbers, an exclusion list of already-tested stack fingerprints, and the
feedback directives verbatim.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from tmm_engine.hashing import stable_sha256

from .research_feedback import decision_score

LEDGER_SCHEMA_VERSION = "optomind-action-ledger.v1"
LEDGER_EVENT_TYPE = "action_effect_ledger"


class ExpectedEffect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim: str = ""
    metric: str = "best_target_score"
    expected_delta: Optional[float] = None
    basis: str = "pre_registered_expected_observations"


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str = "not_run"
    tool_call_id: str = ""
    certificate_id: str = ""
    verification_overall: Optional[str] = None


class ObservedEffect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    metric_before: Optional[float] = None
    metric_after: Optional[float] = None
    delta: Optional[float] = None
    uncertainty: Optional[float] = None  # O-07 noise floor plugs in here


class BeliefUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    before: Optional[float] = None
    after: Optional[float] = None
    rule: str = "unresolved_no_probabilistic_evidence"
    status: str = "unresolved"
    mechanism_update: Dict[str, Any] = Field(default_factory=dict)
    semantics: str = "objective_delta_is_action_utility_not_mechanistic_evidence"


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str = "refine_route"
    replan_trigger: bool = False
    reason: str = ""


class ActionEffectLedgerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = LEDGER_SCHEMA_VERSION
    action_id: str
    parent_state_hash: str
    hypothesis_id: str
    expected_effect: ExpectedEffect = Field(default_factory=ExpectedEffect)
    execution: ExecutionRecord = Field(default_factory=ExecutionRecord)
    observed_effect: ObservedEffect = Field(default_factory=ObservedEffect)
    belief_update: BeliefUpdate = Field(default_factory=BeliefUpdate)
    decision: Decision = Field(default_factory=Decision)
    child_state_hash: str = ""

    @property
    def chained(self) -> bool:
        return bool(self.child_state_hash) and self.child_state_hash != self.parent_state_hash


def update_belief(before: float, delta: Optional[float], expected: Optional[float]) -> BeliefUpdate:
    return BeliefUpdate()


def _mechanism_belief_update(payload: Optional[Mapping[str, Any]]) -> BeliefUpdate:
    if not isinstance(payload, Mapping):
        return BeliefUpdate()
    status = str(payload.get("status") or "unresolved")
    if status in ("updated", "duplicate_observation"):
        from .science.terminal_problem import probability_vector
        hypotheses = payload.get("hypothesis_ids") or []
        if not payload.get("belief_binding") or not payload.get("consumed_observation_ids") or len(hypotheses) < 2:
            raise ValueError("scientific belief result lacks explicit hypotheses and evidence binding")
        probability_vector(payload.get("posterior"), len(hypotheses))
    else:
        status = "unresolved"
    return BeliefUpdate(
        rule="finite_model_bayes" if status == "updated" else "duplicate_observation" if status == "duplicate_observation" else "unresolved_no_probabilistic_evidence",
        status=status,
        mechanism_update=dict(payload),
    )


# ---------------------------------------------------------------------------
# Entry construction
# ---------------------------------------------------------------------------


def _state_hash(route_id: str, rounds_used: int, best_score: Optional[float]) -> str:
    return stable_sha256(
        {
            "hypothesis_id": route_id,
            "rounds_used": rounds_used,
            "best_score": best_score,
        }
    )


def _gradient_basis_text(summary: Any) -> str:
    """"Which layer is worth moving", from the computed sensitivity only."""

    if not isinstance(summary, Mapping):
        return ""
    if summary.get("status") != "available":
        return "gradient unavailable: " + str(summary.get("reason") or summary.get("unavailable") or "unverified binding")
    if not summary.get("check_ref") or not (summary.get("verification") or {}).get("passed"):
        return "gradient unavailable: finite-difference verification not recorded"
    layers = summary.get("top_influential_layers") or []
    try:
        rows = [
            f"layer {item['layer_index']} (signed derivative {float(item['gradient_per_nm']):+.4g}/nm)"
            for item in layers[:3]
        ]
    except (KeyError, TypeError, ValueError):
        return "gradient unavailable: signed derivatives missing"
    if not rows:
        return "gradient unavailable: signed derivatives missing"
    return str(summary.get("objective_semantics") or "bound objective") + ": " + ", ".join(rows)


def build_ledger_entry(
    *,
    action_id: str,
    hypothesis_id: str,
    rounds_used: int,
    best_score_before: Optional[float],
    best_score_after: Optional[float],
    expected_claim: str = "",
    expected_metric: str = "best_target_score",
    expected_delta: Optional[float] = None,
    gradient_basis: Any = None,
    run_status: str = "not_run",
    tool_call_id: str = "",
    certificate_id: str = "",
    verification_overall: Optional[str] = None,
    confidence_before: float = 0.5,
    mechanism_update: Optional[Mapping[str, Any]] = None,
    decision_action: str = "refine_route",
    replan_trigger: bool = False,
    decision_reason: str = "",
) -> ActionEffectLedgerEntry:
    """Program-filled ledger entry. Every number is computed, none authored."""

    delta = (
        None
        if best_score_before is None or best_score_after is None
        else round(float(best_score_after) - float(best_score_before), 6)
    )
    belief = _mechanism_belief_update(mechanism_update)
    return ActionEffectLedgerEntry(
        action_id=action_id,
        parent_state_hash=_state_hash(hypothesis_id, rounds_used, best_score_before),
        hypothesis_id=hypothesis_id,
        expected_effect=ExpectedEffect(
            claim=str(expected_claim or ""),
            metric=expected_metric,
            expected_delta=expected_delta,
            basis=(
                _gradient_basis_text(gradient_basis)
                or "pre_registered_expected_observations"
            ),
        ),
        execution=ExecutionRecord(
            status=str(run_status),
            tool_call_id=str(tool_call_id),
            certificate_id=str(certificate_id),
            verification_overall=verification_overall,
        ),
        observed_effect=ObservedEffect(
            metric_before=best_score_before,
            metric_after=best_score_after,
            delta=delta,
        ),
        belief_update=belief,
        decision=Decision(
            action=str(decision_action),
            replan_trigger=bool(replan_trigger),
            reason=str(decision_reason or ""),
        ),
        child_state_hash=_state_hash(
            hypothesis_id, rounds_used + 1, best_score_after
        ),
    )


def entry_to_event_payload(entry: ActionEffectLedgerEntry) -> Dict[str, Any]:
    return {"schema_version": entry.schema_version, **entry.model_dump(mode="json")}


# ---------------------------------------------------------------------------
# Robin-style INSIGHT_APPENDAGE
# ---------------------------------------------------------------------------

_EXCLUSION_MAX = 12


def collect_tested_stack_fingerprints(observations: Iterable[Mapping[str, Any]]) -> List[str]:
    """Already-simulated stack fingerprints, in first-seen order."""

    fingerprints: List[str] = []
    seen: set[str] = set()
    for row in observations:
        for candidate in row.get("candidate_summaries") or []:
            if not isinstance(candidate, Mapping):
                continue
            thicknesses = candidate.get("thicknesses_nm") or []
            fingerprint = stable_sha256(
                [round(float(t), 3) for t in thicknesses]
            )[:16]
            if fingerprint not in seen:
                seen.add(fingerprint)
                fingerprints.append(f"{fingerprint}:{len(thicknesses)}L")
    return fingerprints


def build_insight_appendage(
    *,
    route_id: str,
    observations: Iterable[Mapping[str, Any]],
    directives: Iterable[str],
) -> str:
    """Four-field deterministic text block for the next planning prompt.

    Template-generated from measurements only: every number traces to an
    observation, insights are mechanical reads of margins/failures, and the
    exclusion list names already-tested stacks so the planner cannot resubmit
    them. No LLM-authored content enters this block.
    """

    rows = [dict(row) for row in observations if isinstance(row, Mapping)]
    deltas: List[str] = []
    best: Optional[float] = None
    failure_categories: List[str] = []
    margins: List[float] = []
    for index, row in enumerate(rows, 1):
        score = decision_score(row)
        if score is not None:
            value = float(score)
            if index > 1 and row.get("score_basis_fingerprint") != rows[index - 2].get("score_basis_fingerprint"):
                best = None
            best = value if best is None else max(best, value)
            if index > 1:
                previous_row = rows[index - 2]
                previous = decision_score(previous_row)
                same_basis = bool(row.get("score_basis_fingerprint")) and (
                    row.get("score_basis_fingerprint") == previous_row.get("score_basis_fingerprint")
                    and row.get("score_basis", "legacy_target") == previous_row.get("score_basis", "legacy_target")
                )
                if previous is not None and same_basis:
                    deltas.append(
                        f"round {index}: {value:.4f} (delta {value - float(previous):+.4f})"
                    )
                else:
                    deltas.append(f"round {index}: {value:.4f}")
            else:
                deltas.append(f"round {index}: {value:.4f}")
        failure_categories.extend(row.get("failure_categories") or ())
        for candidate in row.get("candidate_summaries") or []:
            if isinstance(candidate, Mapping):
                report = candidate.get("robustness_report") or {}
                margin = (
                    report.get("tightest_margin")
                    if isinstance(report, Mapping)
                    else None
                )
                if isinstance(margin, (int, float)):
                    margins.append(float(margin))

    analysis = "; ".join(deltas) if deltas else "no scored rounds yet"
    if best is not None:
        analysis += f" | current best {best:.4f}"

    insights: List[str] = []
    if margins:
        insights.append(
            f"tightest physics margin observed: {min(margins):.4f} "
            "(band edges and thickness errors eat margin first)"
        )
    counted: Dict[str, int] = {}
    for category in failure_categories:
        counted[str(category)] = counted.get(str(category), 0) + 1
    for category, count in sorted(counted.items(), key=lambda kv: -kv[1])[:3]:
        insights.append(f"recurring failure category: {category} x{count}")
    if best is not None and best < 1.0 and rows[-1].get("score_basis", "legacy_target") == "legacy_target":
        insights.append(
            f"remaining headroom to 1.0: {1.0 - best:.4f} on the legacy target metric"
        )
    if not insights:
        insights.append("no mechanical signal yet; treat the next round as exploratory")

    questions: List[str] = []
    if margins and min(margins) < 0.05:
        questions.append(
            "which band edge limits the margin -- is the collapse at a band edge "
            "or inside the passband?"
        )
    regressed = [
        index
        for index in range(1, len(deltas))
        if "delta -" in deltas[index]
    ]
    if regressed:
        questions.append(
            "what explains the score regression at " + ", ".join(str(r) for r in regressed) + "?"
        )
    if not questions:
        questions.append("no unexplained regressions recorded this chain")

    followups = [str(item).strip() for item in directives if str(item).strip()][:6]
    if not followups:
        followups = ["no explicit directives; choose one concrete physical change"]

    fingerprints = collect_tested_stack_fingerprints(rows)[-_EXCLUSION_MAX:]

    lines = [
        "[INSIGHT_APPENDAGE]",
        f"route: {route_id}",
        f"analysis_summary: {analysis}",
        "mechanistic_insights:",
        *(f"- {item}" for item in insights[:5]),
        "questions_raised:",
        *(f"- {item}" for item in questions[:3]),
        "followup_suggestions:",
        *(f"- {item}" for item in followups),
        "tested_stack_exclusions (fingerprint:layers, do not resubmit):",
        *(f"- {item}" for item in fingerprints),
        "[/INSIGHT_APPENDAGE]",
    ]
    return "\n".join(lines)
