"""O-08: Bradley-Terry final ranking over the champion circle.

Robin-style: the frozen metric stays the scientific floor; the LLM acts only
as a pairwise judge whose verdicts feed ``choix.ilsr_pairwise`` for a global
strength score. Both scales are reported side by side and the ranking
mechanism (including any fallback) is recorded in the artifact. A judge
failure never crashes the run -- the fallback discipline is explicit.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from tmm_engine.hashing import stable_sha256

BT_SCHEMA_VERSION = "optomind-bt-ranking.v1"
BT_RANKING_FILENAME = "RANKED_CANDIDATES.json"
CHAMPION_CIRCLE_MAX = 8

JUDGE_SYSTEM_PROMPT = (
    "You are a pairwise scientific judge. Compare the two candidates using "
    "ONLY the frozen ranking metric values and the physics-certificate "
    "evidence provided. Do not judge by wording, style, or verbosity. "
    "Answer with STRICT JSON: {\"Winner\": \"<candidate_id>\", "
    "\"Loser\": \"<candidate_id>\", \"Reasoning\": \"<one sentence citing "
    "the numbers>\"} and nothing else."
)


def _candidate_row(entry: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "candidate_id": str(entry.get("candidate_id") or ""),
        "frozen_score": entry.get("frozen_score"),
        "route_id": str(entry.get("route_id") or ""),
        "certificate_id": str(entry.get("certificate_id") or ""),
        "summary": str(entry.get("summary") or "")[:200],
    }


def deterministic_judge(a: Mapping[str, Any], b: Mapping[str, Any]) -> Dict[str, Any]:
    """Mock-mode proxy judge: frozen score decides; ties break by id."""

    score_a = float(a.get("frozen_score") or 0.0)
    score_b = float(b.get("frozen_score") or 0.0)
    if score_a >= score_b:
        winner, loser = a, b
        margin = score_a - score_b
    else:
        winner, loser = b, a
        margin = score_b - score_a
    return {
        "Winner": str(winner.get("candidate_id")),
        "Loser": str(loser.get("candidate_id")),
        "Reasoning": (
            f"frozen metric {max(score_a, score_b):.4f} vs "
            f"{min(score_a, score_b):.4f} (deterministic proxy judge)"
        ),
        "_margin": margin,
    }


def llm_judge(
    client: Any,
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    force_mock: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """One pairwise LLM judgement; None on any failure (fallback discipline)."""

    user_payload = {
        "candidates": [_candidate_row(a), _candidate_row(b)],
        "frozen_metric": "best_target_score (frozen standard, higher is better)",
    }
    try:
        response = client.call(
            [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            max_tokens=300,
            force_mock=force_mock,
        )
        raw = str(response.get("content") or "")
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        parsed = json.loads(match.group(0))
        winner = str(parsed.get("Winner") or "")
        loser = str(parsed.get("Loser") or "")
        if not winner or not loser or winner == loser:
            return None
        ids = {str(a.get("candidate_id")), str(b.get("candidate_id"))}
        if winner not in ids or loser not in ids:
            return None
        return {
            "Winner": winner,
            "Loser": loser,
            "Reasoning": str(parsed.get("Reasoning") or "")[:300],
        }
    except Exception:
        return None


def rank_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    client: Any = None,
    force_mock: Optional[bool] = None,
) -> Dict[str, Any]:
    """Champion-circle pairwise judging + Bradley-Terry strength fit.

    Falls back to frozen-score ordering whenever choix is unavailable or a
    judgement is unparseable; the mechanism is recorded either way.
    """

    rows = sorted(
        (dict(row) for row in candidates),
        key=lambda row: float(row.get("frozen_score") or 0.0),
        reverse=True,
    )[:CHAMPION_CIRCLE_MAX]
    circle = [_candidate_row(row) for row in rows]

    pairwise: List[Dict[str, Any]] = []
    wins: List[List[int]] = []
    mechanism = "bradley_terry_choix"
    notes: List[str] = []

    try:
        import choix
    except Exception as exc:  # pragma: no cover - environment-dependent
        choix = None
        notes.append(f"choix unavailable: {type(exc).__name__}")

    for i in range(len(circle)):
        for j in range(i + 1, len(circle)):
            if client is None or force_mock:
                verdict = deterministic_judge(circle[i], circle[j])
                judge_source = "deterministic_proxy"
            else:
                verdict = llm_judge(client, circle[i], circle[j], force_mock=force_mock)
                if verdict is None:
                    verdict = deterministic_judge(circle[i], circle[j])
                    judge_source = "deterministic_fallback_after_llm_failure"
                else:
                    judge_source = "llm"
            verdict["judge"] = judge_source
            pairwise.append(verdict)
            winner_idx = next(
                idx
                for idx, row in enumerate(circle)
                if row["candidate_id"] == verdict["Winner"]
            )
            loser_idx = next(
                idx
                for idx, row in enumerate(circle)
                if row["candidate_id"] == verdict["Loser"]
            )
            wins.append([winner_idx, loser_idx])

    strengths: Optional[List[float]] = None
    if choix is not None and wins:
        try:
            import numpy

            params = choix.ilsr_pairwise(len(circle), wins, alpha=0.01)
            strengths = [float(value) for value in params]
        except Exception as exc:
            strengths = None
            notes.append(f"choix fit failed: {type(exc).__name__}: {exc}")
    elif choix is None:
        notes.append("fell back to frozen-score ordering")

    if strengths is not None and len(set(round(v, 12) for v in strengths)) > 1:
        ranked = sorted(
            zip(circle, strengths), key=lambda pair: -pair[1]
        )
        mechanism = "bradley_terry_choix"
    else:
        ranked = [(row, float(row.get("frozen_score") or 0.0)) for row in circle]
        mechanism = "frozen_score_fallback"

    return {
        "schema_version": BT_SCHEMA_VERSION,
        "ranking_mechanism": mechanism,
        "notes": notes,
        "pairwise_judgements": pairwise,
        "ranked_candidates": [
            {
                "rank": position,
                "candidate_id": row["candidate_id"],
                "strength_score": (
                    round(float(strength), 6) if strength is not None else None
                ),
                # Both scales, side by side and labelled (O-08 contract).
                "frozen_score": row.get("frozen_score"),
                "route_id": row["route_id"],
                "certificate_id": row["certificate_id"],
            }
            for position, (row, strength) in enumerate(ranked, 1)
        ],
    }
