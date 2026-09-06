"""O-07: ScientificProposalGate -- a typed, auditable "spend this round?"
decision taken BEFORE an expensive execution (task compile / optimizer run).

Seven criteria must all pass. A failure emits a typed rejection carrying the
failed criteria and repair suggestions, and the route falls into the existing
refine/repair semantics instead of a blind rerun. Cheap operations (queries,
meta-tools, observations) never pass through the gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional

from tmm_engine.hashing import stable_sha256

from .noise_floor import DEFAULT_BAND, NoiseFloor

GATE_SCHEMA_VERSION = "optomind-proposal-gate.v1"
GATE_EVENT_TYPE = "proposal_gate_decided"

CRITERIA = (
    "novel_information_expected",
    "duplicate_of_previous",
    "lower_fidelity_exhausted",
    "prediction_recorded",
    "falsification_condition_recorded",
    "budget_approved",
    "delta_exceeds_noise_floor",
)

#: Repair suggestions, keyed by the failed criterion (typed rejections teach
#: the repair instead of just refusing).
REPAIR_SUGGESTIONS = {
    "novel_information_expected": (
        "state a design variable or topology change that differs from every "
        "prior round of this chain"
    ),
    "duplicate_of_previous": (
        "the proposed stack already ran: pick a materially different "
        "initialization, material pair or topology family"
    ),
    "lower_fidelity_exhausted": (
        "cheap exploration is exhausted: the reflection directives must name "
        "one concrete untried adjustment before another expensive round"
    ),
    "prediction_recorded": (
        "write expected_observations for this round before it runs"
    ),
    "falsification_condition_recorded": (
        "write stop_conditions naming what result would falsify the route"
    ),
    "budget_approved": (
        "the run budget is exhausted; the scheduler owns this decision"
    ),
    "delta_exceeds_noise_floor": (
        "expected improvement sits inside the noise band: probe the far side "
        "of the range or reverse the direction instead of re-rolling"
    ),
}


@dataclass(frozen=True)
class GateDecision:
    approved: bool
    failed: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_payload(self, proposal_id: str, hypothesis_id: str) -> Dict[str, Any]:
        return {
            "schema_version": GATE_SCHEMA_VERSION,
            "proposal_id": proposal_id,
            "hypothesis_id": hypothesis_id,
            "approved": self.approved,
            "failed_criteria": list(self.failed),
            "repair_suggestions": list(self.suggestions),
        }


class DeadEndLedger:
    """Cross-route failure memory keyed by (materials, family, failure code).

    It influences ONLY the gate and queue ordering -- never a route's frozen
    scoring or its recorded history (scoring isolation preserved).
    """

    def __init__(self, entries: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        self._entries: Dict[str, Dict[str, Any]] = dict(entries or {})

    @staticmethod
    def _key(material_system: str, structure_family: str, failure_code: str) -> str:
        return stable_sha256(
            {
                "material_system": str(material_system),
                "structure_family": str(structure_family),
                "failure_code": str(failure_code),
            }
        )

    def record(
        self,
        material_system: str,
        structure_family: str,
        failure_code: str,
        *,
        axis: str = "",
        direction: str = "",
        value_range: str = "",
        route_id: str = "",
    ) -> str:
        key = self._key(material_system, structure_family, failure_code)
        entry = self._entries.setdefault(
            key,
            {
                "material_system": str(material_system),
                "structure_family": str(structure_family),
                "failure_code": str(failure_code),
                "attempts": 0,
                "routes": [],
                "axes": [],
            },
        )
        entry["attempts"] += 1
        if route_id and route_id not in entry["routes"]:
            entry["routes"].append(str(route_id))
        summary = {"axis": axis, "direction": direction, "value_range": value_range}
        if any(summary.values()) and summary not in entry["axes"]:
            entry["axes"].append(summary)
        return key

    def matches(self, material_system: str, structure_family: str, failure_code: str) -> bool:
        return self._key(
            material_system, structure_family, failure_code
        ) in self._entries

    def attempts(self, material_system: str, structure_family: str) -> int:
        return sum(
            entry["attempts"]
            for entry in self._entries.values()
            if entry["material_system"] == str(material_system)
            and entry["structure_family"] == str(structure_family)
        )

    def to_dict(self) -> Dict[str, Dict[str, Any]]:
        return {key: dict(entry) for key, entry in self._entries.items()}


def build_dead_end_index(events: Iterable[Mapping[str, Any]]) -> DeadEndLedger:
    """Project the shared failure memory from the event stream.

    Physics eliminations and hard failures feed the ledger; budget stops and
    round-limit exits do not (they are resources, not refuted hypotheses).
    """

    ledger = DeadEndLedger()
    route_info: Dict[str, Dict[str, Any]] = {}
    for row in events:
        event_type = str(row.get("event_type") or "")
        payload = row.get("payload") or {}
        if event_type in {"route_planned", "strategy_planned"}:
            for route in payload.get("routes") or ():
                if isinstance(route, Mapping) and route.get("route_id"):
                    route_info[str(route["route_id"])] = {
                        "materials": ", ".join(
                            str(m) for m in route.get("proposed_materials") or ()
                        ),
                        "family": str(route.get("route_kind") or ""),
                    }
    for row in events:
        event_type = str(row.get("event_type") or "")
        payload = row.get("payload") or {}
        route_id = str(payload.get("route_id") or "")
        if not route_id:
            continue
        failure_codes: List[str] = []
        if event_type == "track_status" and payload.get("status") in {
            "eliminated_physics",
            "error_unrecoverable",
        }:
            reason = str(payload.get("reason") or "")
            failure_codes = [category for category in (
                "physics_rejected" if payload.get("status") == "eliminated_physics" else "unrecoverable_error",
            ) if category] or [reason[:60] or "unknown"]
        elif event_type == "round_observed":
            for category in payload.get("failure_categories") or ():
                failure_codes.append(str(category))
        if not failure_codes:
            continue
        info = route_info.get(route_id, {})
        material_system = str(info.get("materials") or "unknown")
        structure_family = str(info.get("family") or "unknown")
        for code in failure_codes:
            ledger.record(
                material_system,
                structure_family,
                code,
                route_id=route_id,
                axis="design_variables",
                direction="as-planned",
                value_range="",
            )
    return ledger


class ProposalGate:
    """Seven typed criteria; all must pass. An explicit override flag is
    recorded in the event so a human/upper-layer bypass is always visible."""

    def __init__(
        self,
        *,
        noise_floor: Optional[NoiseFloor] = None,
        dead_ends: Optional[DeadEndLedger] = None,
        config_hash: str = "default",
        rank_metric: str = "best_target_score",
    ) -> None:
        self.noise_floor = noise_floor or NoiseFloor()
        self.dead_ends = dead_ends or DeadEndLedger()
        self.config_hash = str(config_hash)
        self.rank_metric = str(rank_metric)

    # -- criterion helpers ------------------------------------------------

    @staticmethod
    def route_fingerprint(route: Mapping[str, Any]) -> str:
        """A route-level stack fingerprint: materials + family + variables."""

        return stable_sha256(
            {
                "materials": sorted(
                    str(m).casefold() for m in route.get("proposed_materials") or ()
                ),
                "family": str(route.get("route_kind") or ""),
                "variables": sorted(
                    str(v).casefold() for v in route.get("design_variables") or ()
                ),
                "request": str(route.get("execution_request_english") or ""),
            }
        )[:16]

    def evaluate(
        self,
        *,
        proposal_id: str,
        hypothesis_id: str,
        route: Mapping[str, Any],
        fingerprint: str,
        chain_version_hashes: Iterable[str],
        rounds_used: int,
        directives: Iterable[str],
        budget_approved: bool,
        expected_delta: Optional[float] = None,
        override: bool = False,
        has_attestation_channel: bool = True,
    ) -> GateDecision:
        failed: List[str] = []
        directives = [str(item) for item in directives if str(item).strip()]
        materials = ", ".join(
            str(m) for m in route.get("proposed_materials") or ()
        )
        family = str(route.get("route_kind") or "")

        chain_hashes = set(chain_version_hashes)
        if fingerprint in chain_hashes:
            failed.append("duplicate_of_previous")
            failed.append("novel_information_expected")
        elif self.dead_ends.matches(materials, family, "physics_rejected"):
            # The exact material-system x family combination was already
            # physics-refuted on another route of this run.
            failed.append("duplicate_of_previous")

        # O-07 criterion 3: cheap paths are exhausted when the proposal adds
        # NOTHING new -- a rerun of an already-accepted version with no cheap
        # alternative directive. A fresh revision or any directive means an
        # untried adjustment exists, so this criterion stays open.
        if fingerprint in chain_hashes and not directives:
            failed.append("lower_fidelity_exhausted")
        del rounds_used  # retained in the signature for queue ordering use

        # O-07 conservatism: the prediction/falsification criteria bind only
        # when the run actually operates the R-04 attestation channel. With a
        # legacy/injected planner that supplied no sidecar at all, the gate
        # records the criteria as unavailable instead of manufacturing a
        # rejection a first round could never satisfy.
        if has_attestation_channel:
            if not (route.get("expected_observations") or ()):
                failed.append("prediction_recorded")
            if not (route.get("stop_conditions") or ()):
                failed.append("falsification_condition_recorded")

        if not budget_approved:
            failed.append("budget_approved")

        if expected_delta is not None:
            judgement = self.noise_floor.judge(
                self.config_hash, self.rank_metric, expected_delta
            )
            if judgement["verdict"] == "in_band":
                failed.append("delta_exceeds_noise_floor")

        if override:
            return GateDecision(approved=True, failed=[], suggestions=[
                f"OVERRIDE recorded for failed criteria: {failed}"
            ]) if failed else GateDecision(approved=True)

        suggestions = [REPAIR_SUGGESTIONS[name] for name in failed if name in REPAIR_SUGGESTIONS]
        return GateDecision(
            approved=not failed,
            failed=failed,
            suggestions=suggestions,
        )

    # -- queue ordering -----------------------------------------------------

    def queue_priority(
        self,
        *,
        hypothesis_id: str,
        rounds_used: int,
        avg_abs_delta: Optional[float],
        expected_delta: Optional[float],
    ) -> Tuple[int, int, float, int]:
        """Sort key: anti-consensus first, then cold directions (<3 tries),
        then average |delta| descending; in-band proposals last.

        Lower tuple sorts first. anti_consensus is a placeholder hook (a
        future planner may vote against the majority); cold directions get
        priority over hot ones; bigger historical deltas are worth spending
        on; in-band expected deltas rank last.
        """

        in_band = 0
        if expected_delta is not None:
            judgement = self.noise_floor.judge(
                self.config_hash, self.rank_metric, expected_delta
            )
            in_band = 1 if judgement["verdict"] == "in_band" else 0
        cold = 0 if rounds_used < 3 else 1
        anti_consensus = 0  # hook: filled by planner votes in O-08
        avg = float(avg_abs_delta) if avg_abs_delta is not None else 0.0
        return (in_band, cold, -avg, anti_consensus)
