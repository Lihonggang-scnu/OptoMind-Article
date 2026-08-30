"""Round-k lineage recording for multi-round iteration (T-07).

After each round finishes, one LineageRecord is persisted next to the route
artifacts so the whole iteration chain stays auditable: every adjusted round
names its parent round and carries the parent task fingerprint plus a
human-readable adjustment reason. There is no hardcoded ROUND1/ROUND2 -- any
round depth works.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

LINEAGE_FILENAME = "lineage.json"


@dataclass
class LineageRecord:
    """Provenance of one round's task within a single route."""

    round: int                       # current round (starts at 1)
    parent_round: int | None         # previous round (None for round 1)
    parent_task_sha256: str | None   # previous round's task sha256 (None for round 1)
    task_sha256: str                 # current round's task sha256
    adjustment_reason: str           # human-readable reason from Qwen

    def __post_init__(self) -> None:
        if int(self.round) < 1:
            raise ValueError("lineage round must start at 1")
        self.round = int(self.round)
        if not str(self.task_sha256 or "").strip():
            raise ValueError("task_sha256 must be non-empty")
        if not str(self.adjustment_reason or "").strip():
            raise ValueError("adjustment_reason must be non-empty")
        if self.round == 1:
            if self.parent_round is not None or self.parent_task_sha256 is not None:
                raise ValueError("round 1 must not declare a parent")
        else:
            if self.parent_round != self.round - 1:
                raise ValueError(
                    f"parent_round must be {self.round - 1} for round {self.round}"
                )
            if not str(self.parent_task_sha256 or "").strip():
                raise ValueError(
                    f"round {self.round} requires the parent task sha256"
                )

    def to_dict(self) -> dict:
        """JSON-safe mapping mirroring the dataclass fields."""
        return {
            "round": self.round,
            "parent_round": self.parent_round,
            "parent_task_sha256": self.parent_task_sha256,
            "task_sha256": self.task_sha256,
            "adjustment_reason": self.adjustment_reason,
        }


def write_lineage(record: LineageRecord, output_dir: Path) -> Path:
    """Serialize the record to output_dir/lineage.json and return its path.

    output_dir normally comes from ExperimentStore.round_dir(round_k,
    route_id); the directory is created when missing so callers may hand in a
    freshly computed path.
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    target = output_path / LINEAGE_FILENAME
    target.write_text(
        json.dumps(record.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return target
