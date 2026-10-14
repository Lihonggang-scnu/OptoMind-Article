"""Sealed offline evaluator provider with restart-stable synthetic noise.

Only this host-side fixture receives hidden world parameters. Reconstructing its
RNG uses deterministic replay of already recorded synthetic draws, never a new
external acquisition or model call. Public scientific code gets only receipts.
"""
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from .diagnostic_world import SyntheticDiagnosticProvider
from optomind_optics.harness.science.contracts import canonical_hash
from optomind_optics.harness.science.diagnostic_design import DiagnosticDatum, action_from_dict


def _datum(payload):
    data = dict(payload)
    data['action'] = action_from_dict(data['action'])
    return DiagnosticDatum(**data)


class DurableSyntheticDiagnosticProvider:
    evidence_tier = 'V1_synthetic'
    noise_validated = True

    def __init__(self, path, *, provider_id, families, config, sealed_world):
        world = json.loads(json.dumps(sealed_world, allow_nan=False))
        if set(world) - {'family_id', 'parameters', 'seed', 'mismatch_layer'} or not {'family_id', 'parameters'} <= set(world):
            raise ValueError('invalid separate sealed diagnostic evaluator configuration')
        family = next((f for f in families if f.family_id == world['family_id']), None)
        if family is None:
            raise ValueError('sealed diagnostic evaluator family is absent from the public hypotheses')
        self.provider_id = provider_id
        self.recovery_identity = canonical_hash({'provider_id': provider_id, 'world': world,
            'family': asdict(family), 'config': asdict(config), 'adapter': 'durable-synthetic-diagnostic.v1'})
        self._provider = SyntheticDiagnosticProvider(family=family, parameters=world['parameters'],
            config=config, seed=world.get('seed', 9127), mismatch_layer=world.get('mismatch_layer'))
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY,value TEXT);'
                'CREATE TABLE IF NOT EXISTS acquisitions(sequence INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT UNIQUE,binding TEXT,datum TEXT);')
            saved = self.db.execute("SELECT value FROM config WHERE key='identity'").fetchone()
            if saved and saved[0] != self.recovery_identity:
                raise ValueError('sealed diagnostic provider changed on resume')
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO config VALUES('identity',?)", (self.recovery_identity,))
            # Restore the exact draw/session history, including front-end
            # tolerances. The evaluator alone sees these existing receipts.
            for request_id, payload in self.db.execute('SELECT request_id,datum FROM acquisitions ORDER BY sequence'):
                recorded = _datum(json.loads(payload))
                replayed = self._provider.acquire(recorded.action, request_id=request_id)
                if replayed != recorded:
                    raise ValueError('sealed diagnostic provider cannot reproduce its durable noise history')
        except BaseException:
            self.db.close()
            raise

    def acquire(self, action, *, request_id):
        binding = canonical_hash(asdict(action))
        saved = self.db.execute('SELECT binding,datum FROM acquisitions WHERE request_id=?', (request_id,)).fetchone()
        if saved:
            if saved[0] != binding:
                raise ValueError('sealed diagnostic acquisition identity changed')
            return _datum(json.loads(saved[1]))
        datum = self._provider.acquire(action, request_id=request_id)
        with self.db:
            self.db.execute('INSERT INTO acquisitions(request_id,binding,datum) VALUES(?,?,?)',
                (request_id, binding, json.dumps(asdict(datum), sort_keys=True, allow_nan=False)))
        return datum

    def status(self, request_id):
        row = self.db.execute('SELECT datum FROM acquisitions WHERE request_id=?', (request_id,)).fetchone()
        return _datum(json.loads(row[0])) if row else None

    def close(self):
        self.db.close()


def offline_diagnostic_provider_factory(spec, sealed_world_path):
    """Keep the answer file out of the public spec and production loop inputs."""
    from optomind_optics.harness.science.entry_spec import DiagnosticEntrySpec
    spec = DiagnosticEntrySpec.model_validate(spec) if isinstance(spec, dict) else spec
    families, config, _, _ = spec.components()
    world = json.loads(Path(sealed_world_path).read_text(encoding='utf-8'))
    if spec.provider.evidence_tier != 'V1_synthetic' or not spec.provider.noise_validated:
        raise ValueError('offline provider requires explicit synthetic evidence declaration')

    def create(root, descriptor):
        if descriptor != spec.provider:
            raise ValueError('offline provider public descriptor changed')
        return DurableSyntheticDiagnosticProvider(Path(root) / 'offline_evaluator' / 'diagnostic.sqlite',
            provider_id=descriptor.provider_id, families=families, config=config, sealed_world=world)
    return create
