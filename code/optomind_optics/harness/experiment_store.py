"""Run-namespace directory conventions for the Article harness (T-02).

All pipeline artifacts live under a per-run namespace:

    runs/{problem_id}/{run_id}/round_{k}/{route_id}/

Architecture constraint: every path consumed by the pipeline MUST be obtained
through ExperimentStore accessors; hand-built "runs/" path strings are
forbidden. Methods only compute paths -- they never assume files exist --
except ensure_round_dir(), which creates the directory. problem_id and run_id
are supplied by callers; this module never generates them.
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path("runs")


class ExperimentStore:
    """Compute (and optionally create) run-namespaced artifact paths."""

    def __init__(self, problem_id: str, run_id: str):
        self.problem_id = problem_id
        self.run_id = run_id
        self.root = BASE_DIR / problem_id / run_id

    def round_dir(self, round_k: int, route_id: str) -> Path:
        """Return runs/{problem_id}/{run_id}/round_{k}/{route_id}/"""
        return self.root / f"round_{round_k}" / route_id

    def artifact_path(self, round_k: int, route_id: str, filename: str) -> Path:
        """Return round_dir / filename (path only; existence not assumed)."""
        return self.round_dir(round_k, route_id) / filename

    def ensure_round_dir(self, round_k: int, route_id: str) -> Path:
        """Create the round/route directory if missing and return its path."""
        path = self.round_dir(round_k, route_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def global_artifact(self, filename: str) -> Path:
        """Return runs/{problem_id}/{run_id}/{filename} (run-level artifact)."""
        return self.root / filename
