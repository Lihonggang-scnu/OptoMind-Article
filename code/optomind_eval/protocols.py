from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class EvaluationProtocol:
    protocol_id: str
    task_ids: tuple[str, ...]
    arms: tuple[str, ...]
    comparisons: tuple[tuple[str, str], ...]
    resource_budget: float
    observation_permissions: tuple[str, ...]
    failure_loss: float
    alpha: float = .05
    primary_metric: str = 'terminal_loss_plus_declared_cost'
    statistical_unit: str = 'independent_task'

    def __post_init__(self):
        if len(self.task_ids) < 2 or len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError('at least two distinct independent task IDs required')
        if not self.arms or len(set(self.arms)) != len(self.arms) or not 0 < self.alpha < 1 or self.resource_budget < 0:
            raise ValueError('invalid arms, alpha or budget')
        if any(a not in self.arms or b not in self.arms or a == b for a, b in self.comparisons):
            raise ValueError('invalid preregistered comparison')
        if self.statistical_unit != 'independent_task':
            raise ValueError('wavelengths or adaptive rounds are not independent tasks')

    @property
    def seal(self):
        return digest(asdict(self))

    def freeze(self, path: str | Path):
        path = Path(path)
        payload = {'protocol': asdict(self), 'seal': self.seal}
        with path.open('x') as stream:
            json.dump(payload, stream, sort_keys=True, allow_nan=False)


@dataclass(frozen=True)
class TaskResult:
    protocol_seal: str
    task_id: str
    arm: str
    terminal_loss: float | None
    declared_loss_cost: float
    resource_cost: float
    status: str
    observation_permissions: tuple[str, ...]
    trace_hash: str


def seal_results(protocol: EvaluationProtocol, rows: tuple[TaskResult, ...], path: str | Path):
    import math
    expected = {(task, arm) for task in protocol.task_ids for arm in protocol.arms}
    actual = {(r.task_id, r.arm) for r in rows}
    if actual != expected or len(rows) != len(expected):
        raise ValueError('all tasks and failures must be present exactly once for every arm')
    for row in rows:
        if row.protocol_seal != protocol.seal or row.observation_permissions != protocol.observation_permissions:
            raise ValueError('protocol or observation parity differs')
        if row.status not in {'completed', 'failed', 'budget_exceeded'} or len(row.trace_hash) != 64:
            raise ValueError('unknown execution status or missing trace identity')
        if not math.isfinite(row.resource_cost) or row.resource_cost < 0 or not math.isfinite(row.declared_loss_cost) or row.declared_loss_cost < 0:
            raise ValueError('all actual costs including failures must be recorded')
        if row.status == 'completed' and (row.terminal_loss is None or not math.isfinite(row.terminal_loss) or row.resource_cost > protocol.resource_budget):
            raise ValueError('completed result invalid or outside common budget')
    payload = {'protocol': asdict(protocol), 'protocol_seal': protocol.seal, 'rows': [asdict(row) for row in rows]}
    payload['seal'] = digest(payload)
    with Path(path).open('x') as stream:
        json.dump(payload, stream, sort_keys=True, allow_nan=False)
    return payload['seal']
