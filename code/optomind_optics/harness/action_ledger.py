"""O-05: ActionEffectLedger -- action -> observation -> belief update.

Every feedback decision point records one structured entry: the pre-registered
expectation, the execution trace, the measured effect (program-computed), a
rule-table belief update (the LLM may explain, never write numbers), and the
decision. Entries chain by stable state hashes.

The Robin-style INSIGHT_APPENDAGE re-flows verified results into the next
round's candidate generation as template-generated text: no LLM-authored
numbers, an exclusion list of already-tested stack fingerprints, and the
feedback directives verbatim.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from tmm_engine.hashing import stable_sha256

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

    before: float = 0.5
    after: float = 0.5
    rule: str = "no_signal"


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


# ---------------------------------------------------------------------------
# Belief rule table -- numbers come ONLY from here, never from an LLM.
# ---------------------------------------------------------------------------

#: (rule name, delta/expected_delta relationship) -> confidence delta.
BELIEF_RULES: Tuple[Tuple[str, str], ...] = (
    ("confirmed", "same_sign_over_threshold"),
    ("contradicted", "opposite_sign"),
    ("flat", "below_threshold"),
)
CONFIRM_THRESHOLD = 0.01
CONFIRM_STEP = 0.1
CONTRADICT_STEP = -0.2
FLAT_STEP = -0.05
CONFIDENCE_FLOOR = 0.0
CONFIDENCE_CEILING = 1.0


def _rule_for(delta: Optional[float], expected: Optional[float]) -> Tuple[str, float]:
    """The mechanical belief rule. Same-sign & over threshold confirms."""

    if delta is None:
        return ("no_signal", 0.0)
    if expected is None or expected == 0:
        return ("flat", FLAT_STEP if abs(delta) < CONFIRM_THRESHOLD else CONFIRM_STEP)
    same_sign = (delta > 0) == (expected > 0)
    magnitude_over = abs(delta) >= CONFIRM_THRESHOLD
    if same_sign and magnitude_over:
        return ("confirmed", CONFIRM_STEP)
    if not same_sign:
        return ("contradicted", CONTRADICT_STEP)
    return ("flat", FLAT_STEP)


def update_belief(before: float, delta: Optional[float], expected: Optional[float]) -> BeliefUpdate:
    rule, step = _rule_for(delta, expected)
    after = max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, before + step))
    return BeliefUpdate(before=round(before, 4), after=round(after, 4), rule=rule)


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
    layers = summary.get("top_influential_layers")
    if not layers:
        return ""
    try:
        rows = [
            f"layer {item.get('layer_index')} (sensitivity {float(item.get('sensitivity')):.4g})"
            for item in layers[:3]
            if isinstance(item, Mapping) and item.get("layer_index") is not None
        ]
    except (TypeError, ValueError):
        return ""
    if not rows:
        return ""
    return "gradient sensitivity: " + ", ".join(rows) + " most influence the objective"


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
    belief = update_belief(confidence_before, delta, expected_delta)
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
        score = row.get("best_target_score")
        if score is not None:
            value = float(score)
            best = value if best is None else max(best, value)
            if index > 1:
                previous = rows[index - 2].get("best_target_score")
                if previous is not None:
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
    if best is not None and best < 1.0:
        insights.append(
            f"remaining headroom to 1.0: {1.0 - best:.4f} on the frozen metric"
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
