"""R-01 tests: the planner repair attempt keeps evidence and iteration history.

Before R-01 the second attempt's payload dropped ``method_research`` and
``prior_iterations``, so a validation failure turned the only retry into a
context-free regeneration: the model could no longer see which evidence IDs
were citable, nor which routes had already improved.
"""

from __future__ import annotations

import json
from typing import Any

from optomind_optics.harness.strategy_planner import (
    ARTICLE_STRATEGY_PLANNER_MODEL,
    PLANNER_MAX_TOKENS,
    QwenTMMStrategyPlanner,
)


class RecordingPlusClient:
    """Captures every user payload so the retry contract can be asserted."""

    model_name = ARTICLE_STRATEGY_PLANNER_MODEL

    def __init__(self, payloads: list[dict[str, Any]]):
        self.payloads = list(payloads)
        self.sent_payloads: list[dict[str, Any]] = []
        self.max_tokens_seen: list[int] = []

    def call(self, messages, *, max_tokens: int = 5000, force_mock=None):
        self.max_tokens_seen.append(max_tokens)
        user_message = next(m for m in messages if m["role"] == "user")
        self.sent_payloads.append(json.loads(user_message["content"]))
        return {
            "content": json.dumps(self.payloads.pop(0)),
            "_llm_usage": {
                "model_name": self.model_name,
                "input_tokens": 10,
                "output_tokens": 4,
            },
        }


def _valid_plan() -> dict[str, Any]:
    return {
        "problem_id": "p1",
        "planning_summary": "Use a physically interpretable starting family.",
        "routes": [
            {
                "route_id": "route_01",
                "title": "Quarter-wave starting point",
                "route_kind": "periodic_stack",
                "scientific_hypothesis": "Periodic impedance contrast opens a stop band.",
                "design_principle": "Start near quarter-wave thickness then optimize.",
                "proposed_materials": ["SiO2", "TiO2"],
                "proposed_topology": "Five alternating dielectric pairs on glass.",
                "soft_objectives": ["maximize mean reflectance from 500 to 600 nm"],
                "evidence_ids": ["ev_1"],
                "execution_request_english": (
                    "Design a five-pair TiO2/SiO2 reflector on glass from 450 to "
                    "900 nm, maximizing mean reflectance from 500 to 600 nm."
                ),
                "priority": 1,
            }
        ],
        "research_influence": ["ev_1 motivated the periodic family."],
        "stop_if_all_routes_fail": "Return the best physically valid result.",
    }


def _invalid_plan() -> dict[str, Any]:
    """Missing every required field except problem_id -> ValidationError."""

    return {"problem_id": "p1"}


def _research() -> dict[str, Any]:
    return {
        "status": "completed",
        "evidence": [
            {
                "evidence_id": "ev_1",
                "title": "Dielectric mirror design",
                "allowed_use": "method_guidance",
                "text": "Quarter-wave stacks maximize reflectance at the design wavelength.",
            }
        ],
        "method_findings": [
            {
                "finding": "Quarter-wave periodicity is the canonical starting point.",
                "evidence_ids": ["ev_1"],
            }
        ],
        "unresolved_questions": ["How many pairs before absorption dominates?"],
    }


def _prior_iterations() -> list[dict[str, Any]]:
    return [
        {
            "iteration_id": "iter_1",
            "route_id": "route_01",
            "route_title": "Quarter-wave starting point",
            "compilation_status": "compiled",
            "run_status": "completed",
            "physically_valid_candidate_count": 2,
            "best_target_score": 0.81,
            "failure_categories": [],
        }
    ]


def _run_with_retry() -> RecordingPlusClient:
    client = RecordingPlusClient([_invalid_plan(), _valid_plan()])
    result = QwenTMMStrategyPlanner(client=client, maximum_attempts=2).plan(
        {"problem_id": "p1", "primary_intent": "design"},
        _research(),
        prior_iterations=_prior_iterations(),
        feedback_directives=["Increase the pair count before widening the band."],
    )
    assert result.status == "planned", result.validation_errors
    assert len(client.sent_payloads) == 2, "the retry path must have fired"
    return client


def test_retry_payload_keeps_method_research():
    client = _run_with_retry()
    retry = client.sent_payloads[1]
    assert "method_research" in retry, "R-01: repair attempt lost the evidence pool"
    evidence_ids = [
        row.get("evidence_id") for row in retry["method_research"]["evidence"]
    ]
    assert "ev_1" in evidence_ids
    assert retry["method_research"]["method_findings"], "findings must survive"


def test_retry_payload_keeps_prior_iterations():
    client = _run_with_retry()
    retry = client.sent_payloads[1]
    assert "prior_iterations" in retry, "R-01: repair attempt lost iteration history"
    assert retry["prior_iterations"][0]["route_id"] == "route_01"
    assert retry["prior_iterations"][0]["best_target_score"] == 0.81


def test_retry_payload_matches_first_attempt_context():
    """The repair attempt must not see a *different* evidence pool."""

    client = _run_with_retry()
    first, retry = client.sent_payloads
    assert retry["method_research"] == first["method_research"]
    assert retry["prior_iterations"] == first["prior_iterations"]
    assert retry["fixed_rules"] == first["fixed_rules"]
    assert retry["feedback_directives"] == first["feedback_directives"]


def test_retry_instruction_constrains_evidence_and_history():
    client = _run_with_retry()
    instruction = client.sent_payloads[1]["repair_request"]["instruction"]
    assert "evidence_ids_allowed" in instruction
    assert "prior_iterations" in instruction
    assert "existing_plan" in instruction


def test_retry_payload_still_carries_repair_request():
    """Restoring context must not displace the defect list."""

    client = _run_with_retry()
    first, retry = client.sent_payloads
    assert "repair_request" not in first
    assert "existing_plan" in retry
    errors = retry["repair_request"]["validation_errors"]
    assert errors and any("planning_summary" in str(item) for item in errors)


def test_planner_max_tokens_headroom():
    """The richer repair payload needs a larger completion ceiling."""

    assert PLANNER_MAX_TOKENS == 8000
    client = _run_with_retry()
    assert client.max_tokens_seen == [PLANNER_MAX_TOKENS, PLANNER_MAX_TOKENS]
