"""O-08: hypothesis-experiment tree for open_research mode.

A journal-style flat store of HypothesisNodes with a derived tree, mechanical
falsification counters (AutoScientists-style), and the per-step control flow
(draft / debug / improve). The tree is a SCHEDULING layer only: node
expansion reuses the existing prepare/execute/observe chain, and every
expensive step still passes the O-07 proposal gate.

bounded_design (the default) never touches any of this -- the six-time
tournament template stays the strong prior when it exists (AI Scientist v2
lesson, encoded as a config default rather than a hope).
"""

from __future__ import annotations

import random
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from tmm_engine.hashing import stable_sha256

TREE_SCHEMA_VERSION = "optomind-hypothesis-tree.v1"
TREE_FILENAME = "HYPOTHESIS_TREE.json"

DEBUG_DEPTH_HARD_LIMIT = 3

TREE_EVENT_NODE_CREATED = "tree_node_created"
TREE_EVENT_NODE_UPDATED = "tree_node_updated"
TREE_EVENT_FALSIFIED = "tree_hypothesis_falsified"

#: Mechanical falsification rule (AutoScientists-style counters). A branch
#: whose hypothesis kept rotating without a single supported keep, across at
#: least three rotations and three refutations, is dead.
FALSIFY_MIN_ROTATIONS = 3
FALSIFY_MIN_REFUTED = 3


class FalsificationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hypothesis: str = ""
    prediction: str = ""
    falsification: str = ""
    supported_keeps: int = 0
    refuted_discards: int = 0
    rotations: int = 0


class HypothesisNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)

    node_id: str
    parent_id: Optional[str] = None
    children_ids: List[str] = Field(default_factory=list)
    route_id: str = ""
    stage: str = "draft"  # draft | debug | improve
    debug_depth: int = 0
    plan_pointer: str = ""  # route description / stack pointer
    metric: Optional[float] = None
    is_buggy: bool = False
    falsification: FalsificationRecord = Field(default_factory=FalsificationRecord)
    status: str = "open"  # open | falsified | improved


class HypothesisTree:
    """Flat journal + derived tree. Nodes are immutable-by-discipline:
    updates go through ``update_node`` which appends, never rewrites
    history -- the journal keeps every version."""

    def __init__(self, *, num_drafts: int = 3, debug_prob: float = 0.5) -> None:
        self.nodes: Dict[str, HypothesisNode] = {}
        self.order: List[str] = []
        self.num_drafts = max(1, int(num_drafts))
        self.debug_prob = min(1.0, max(0.0, float(debug_prob)))
        self.events: List[Dict[str, Any]] = []

    # -- construction / updates -------------------------------------------

    def create_node(
        self,
        *,
        parent_id: Optional[str],
        route_id: str,
        stage: str,
        plan_pointer: str,
        hypothesis: str = "",
        prediction: str = "",
    ) -> HypothesisNode:
        parent = self.nodes.get(parent_id) if parent_id else None
        if stage == "debug" and parent is not None:
            debug_depth = parent.debug_depth + 1
        else:
            debug_depth = 0
        node_id = stable_sha256(
            {
                "parent": parent_id,
                "route": route_id,
                "stage": stage,
                "pointer": plan_pointer,
                "sibling_index": len(self.order),
            }
        )[:16]
        falsification = FalsificationRecord(
            hypothesis=str(hypothesis),
            prediction=str(prediction),
        )
        if parent is not None and stage == "debug":
            # Falsification counters accumulate ALONG THE CHAIN: a debug
            # branch inherits the parent's rotation/refutation history (plus
            # its own rotation) so the mechanical rule judges the hypothesis
            # lineage, not one node.
            falsification = parent.falsification.model_copy(
                update={
                    "rotations": parent.falsification.rotations + 1,
                    "hypothesis": falsification.hypothesis
                    or parent.falsification.hypothesis,
                }
            )
        node = HypothesisNode(
            node_id=node_id,
            parent_id=parent_id,
            route_id=route_id,
            stage=stage,
            debug_depth=debug_depth,
            plan_pointer=str(plan_pointer),
            falsification=falsification,
        )
        self.nodes[node_id] = node
        self.order.append(node_id)
        if parent is not None:
            parent.children_ids.append(node_id)
        self.events.append(
            {
                "event_type": TREE_EVENT_NODE_CREATED,
                "payload": {
                    "node_id": node_id,
                    "parent_id": parent_id,
                    "stage": stage,
                    "debug_depth": debug_depth,
                },
            }
        )
        return node

    def update_node(
        self,
        node_id: str,
        *,
        metric: Optional[float] = None,
        is_buggy: Optional[bool] = None,
        plan_pointer: Optional[str] = None,
        rotated: bool = False,
    ) -> HypothesisNode:
        node = self.nodes[node_id]
        if metric is not None:
            node.metric = float(metric)
        if is_buggy is not None:
            node.is_buggy = bool(is_buggy)
        if plan_pointer:
            node.plan_pointer = str(plan_pointer)
        record = node.falsification
        updates: Dict[str, Any] = {}
        if rotated:
            updates["rotations"] = record.rotations + 1
        if is_buggy or (metric is not None and metric <= 0.0):
            updates["refuted_discards"] = record.refuted_discards + 1
        elif metric is not None and metric > 0.0:
            updates["supported_keeps"] = record.supported_keeps + 1
        if updates:
            node.falsification = record.model_copy(update=updates)
        # Mechanical falsification rule -- no LLM in this decision.
        if (
            node.falsification.rotations >= FALSIFY_MIN_ROTATIONS
            and node.falsification.supported_keeps == 0
            and node.falsification.refuted_discards >= FALSIFY_MIN_REFUTED
        ):
            node.status = "falsified"
            self.events.append(
                {
                    "event_type": TREE_EVENT_FALSIFIED,
                    "payload": {
                        "node_id": node_id,
                        **node.falsification.model_dump(mode="json"),
                    },
                }
            )
        self.events.append(
            {
                "event_type": TREE_EVENT_NODE_UPDATED,
                "payload": {
                    "node_id": node_id,
                    "metric": node.metric,
                    "is_buggy": node.is_buggy,
                    "status": node.status,
                    **node.falsification.model_dump(mode="json"),
                },
            }
        )
        return node

    # -- derived views ------------------------------------------------------

    def roots(self) -> List[HypothesisNode]:
        return [self.nodes[nid] for nid in self.order if self.nodes[nid].parent_id is None]

    def draft_count(self) -> int:
        return len(self.roots())

    def failed_leaves(self) -> List[HypothesisNode]:
        """Buggy leaves whose debug depth still has headroom, freshest first."""

        leaves = []
        for nid in reversed(self.order):
            node = self.nodes[nid]
            if (
                node.is_buggy
                and node.status != "falsified"
                and node.debug_depth < DEBUG_DEPTH_HARD_LIMIT
                and not node.children_ids
            ):
                leaves.append(node)
        return leaves

    def best_good_node(self) -> Optional[HypothesisNode]:
        good = [
            node
            for node in (self.nodes[nid] for nid in self.order)
            if not node.is_buggy
            and node.metric is not None
            and node.status != "falsified"
        ]
        if not good:
            return None
        return max(good, key=lambda node: float(node.metric or 0.0))

    # -- control flow ---------------------------------------------------------

    def next_action(self, rng: Optional[random.Random] = None) -> Dict[str, Any]:
        """One scheduling step: draft if short, else debug a failing leaf
        with probability debug_prob, else improve the current best."""

        rng = rng or random.Random()
        if self.draft_count() < self.num_drafts:
            return {"action": "draft_root"}
        if rng.random() < self.debug_prob:
            leaves = self.failed_leaves()
            if leaves:
                return {
                    "action": "debug",
                    "parent_id": leaves[0].node_id,
                    "debug_depth": leaves[0].debug_depth + 1,
                }
        best = self.best_good_node()
        if best is not None:
            return {"action": "improve", "parent_id": best.node_id}
        leaves = self.failed_leaves()
        if leaves:
            return {
                "action": "debug",
                "parent_id": leaves[0].node_id,
                "debug_depth": leaves[0].debug_depth + 1,
            }
        return {"action": "draft_root"}

    # -- persistence ------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": TREE_SCHEMA_VERSION,
            "num_drafts": self.num_drafts,
            "debug_prob": self.debug_prob,
            "nodes": {
                nid: self.nodes[nid].model_dump(mode="json")
                for nid in self.order
            },
            "order": list(self.order),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HypothesisTree":
        tree = cls(
            num_drafts=int(payload.get("num_drafts") or 3),
            debug_prob=float(payload.get("debug_prob") or 0.5),
        )
        for nid in payload.get("order") or []:
            node = HypothesisNode.model_validate(payload["nodes"][nid])
            tree.nodes[nid] = node
            tree.order.append(nid)
        return tree

    def save(self, out_dir) -> str:
        from pathlib import Path

        path = Path(out_dir) / TREE_FILENAME
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return str(path)


import json  # noqa: E402  (module-level import kept late to mirror usage)


def build_failure_memory_summary(
    observations: List[Mapping[str, Any]],
    *,
    max_rows: int = 8,
) -> str:
    """Two-part tree context block: successful experiments (design+metric),
    failed experiments (design+error+failure code+debug depth). Mechanical;
    reuses the O-05 insight-block discipline (no LLM-authored numbers)."""

    successes: List[str] = []
    failures: List[str] = []
    for index, row in enumerate(observations, 1):
        route_id = str(row.get("route_id") or "")
        score = row.get("best_target_score")
        status = str(row.get("run_status") or "")
        failures_cats = ", ".join(row.get("failure_categories") or [])
        design = str(row.get("iteration_id") or f"round {index}")
        if score is not None and not failures_cats:
            successes.append(
                f"- {route_id}/{design}: frozen metric {float(score):.4f} ({status})"
            )
        else:
            failures.append(
                f"- {route_id}/{design}: status={status or 'unknown'} "
                f"failures=[{failures_cats or 'none recorded'}]"
            )
    lines = ["[TREE_FAILURE_MEMORY]"]
    lines.append("successful experiments:")
    lines.extend(successes[:max_rows] or ["- none yet"])
    lines.append("failed experiments:")
    lines.extend(failures[:max_rows] or ["- none yet"])
    lines.append("[/TREE_FAILURE_MEMORY]")
    return "\n".join(lines)
