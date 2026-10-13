from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

import numpy as np

from .terminal_problem import TerminalDecisionProblem


class LossMatrixCache:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def store(self, problem: TerminalDecisionProblem) -> str:
        record = {"schema": "loss_matrix.v1", "key": problem.cache_key, "world_ids": problem.world_ids,
                  "design_ids": problem.design_ids, "losses": problem.losses.tolist(),
                  "context": dict(problem.context), "units": problem.units}
        handle, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w") as stream:
                json.dump(record, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / f"{problem.cache_key}.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return problem.cache_key

    def load(self, key: str) -> TerminalDecisionProblem:
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("cache key must be SHA256")
        record = json.loads((self.directory / f"{key}.json").read_text())
        if record.get("schema") != "loss_matrix.v1" or record.get("key") != key:
            raise ValueError("cache identity mismatch")
        problem = TerminalDecisionProblem(tuple(record["world_ids"]), tuple(record["design_ids"]),
                                           np.asarray(record["losses"]), record["context"], record["units"])
        if problem.cache_key != key:
            raise ValueError("cached scientific context or values changed")
        return problem
