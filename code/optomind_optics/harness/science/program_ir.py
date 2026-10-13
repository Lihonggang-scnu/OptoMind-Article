"""Experiment topology and sample state are distinct from optical model parameters."""
from dataclasses import dataclass, asdict
import hashlib
import json


@dataclass(frozen=True)
class ProgramNode:
    node_id: str
    kind: str
    inputs: tuple[str,...] = ()
    channel: str = ""
    sample_id: str = ""
    required_state: str = "ready"
    resulting_state: str = "ready"
    arguments: tuple[tuple[str,object],...] = ()
    condition: tuple[str,bool] | None = None
    estimate: tuple[tuple[str,float],...] = ()
    correlation_group: str = ""


@dataclass(frozen=True)
class ProgramIR:
    program_id: str
    nodes: tuple[ProgramNode,...]
    initial_samples: tuple[tuple[str,str],...] = ()
    revision: int = 1

    @property
    def content_hash(self):
        return hashlib.sha256(json.dumps(asdict(self),sort_keys=True,allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class CompiledExecutionPlan:
    program: ProgramIR
    resource_bounds: tuple[tuple[str,float],...]
    channels: tuple[str,...]
    bound_semantics: str = "conservative sum over all branches"
