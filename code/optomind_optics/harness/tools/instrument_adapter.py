"""Explicitly unsupported hardware boundary and a test-only in-memory driver.

No transport, endpoint, credential, or real-device approval path exists here.
"""
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class InstrumentCapability:
    driver_kind: str = 'in_memory_test_only'
    commands: tuple[str, ...] = ('measure',)
    supports_cancel: bool = False
    real_device_supported: bool = False


@dataclass(frozen=True)
class DeviceStateReceipt:
    state: str
    stopped_confirmed: bool = False
    evidence_scope: str = 'simulator_only'


class UnsupportedRealDriver:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError('real device unsupported: actual device, approvals and supervised validation required')


class InMemoryTestDriver:
    """Fault injection only. Unknown outcome never means stopped or retryable."""
    capability = InstrumentCapability()

    def __init__(self, fault=None):
        if fault not in {None, 'timeout', 'disconnect', 'partial', 'emergency_stop', 'expired_calibration'}:
            raise ValueError('unknown test fault')
        self.fault = fault
        self._results = {}
        self._identities = {}
        self.calls = 0

    def submit(self, key, payload):
        from ..science.contracts import canonical_hash
        identity = canonical_hash(payload)
        if key in self._results:
            if self._identities[key] != identity:
                raise ValueError('idempotency identity conflict')
            return dict(self._results[key])
        if payload.get('command') not in self.capability.commands:
            raise ValueError('command not whitelisted')
        if self.fault == 'expired_calibration':
            raise ValueError('calibration expired: simulator interlock')
        self.calls += 1
        self._identities[key] = identity
        state = 'unknown_outcome' if self.fault in {'timeout', 'disconnect'} else 'failed' if self.fault in {'partial','emergency_stop'} else 'completed'
        result = {'state':state, 'stopped_confirmed':False, 'evidence_scope':'simulator_only',
                  'partial_values':[.2] if self.fault == 'partial' else [], 'cost':{'readings':1}}
        self._results[key] = MappingProxyType(result)
        return dict(result)

    def status(self, key):
        return dict(self._results.get(key, {'state':'unknown_outcome','stopped_confirmed':False,'evidence_scope':'simulator_only'}))

    def cancel(self, key):
        return {'state':'unsupported','stopped_confirmed':False,'evidence_scope':'simulator_only'}

    def poll(self, key):
        result = self.status(key)
        return DeviceStateReceipt(result['state'], result['stopped_confirmed'])
