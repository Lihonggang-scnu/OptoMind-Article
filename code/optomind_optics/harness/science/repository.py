"""Single scientific writer: atomic artifacts then SQLite reference transaction.

Unreferenced files after a crash are harmless; projections derive only from committed
snapshots. BEGIN IMMEDIATE plus expected_sequence provides cross-process CAS.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Callable, Iterable
from .contracts import ScientificObject, ObjectRef, ScienceSnapshot, ResearchTask, parse_object, canonical_hash


class ScienceRepository:
    def __init__(self, root, *, read_only=False, fault_hook: Callable[[str], None] | None = None):
        self.root = Path(root)
        self.read_only = read_only
        self.fault_hook = fault_hook or (lambda stage: None)
        if not read_only:
            (self.root / "objects").mkdir(parents=True, exist_ok=True)
        path = self.root / "science.sqlite3"
        self.db = sqlite3.connect(f"file:{path}?mode=ro" if read_only else str(path), uri=read_only, timeout=30)
        if not read_only:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript('''CREATE TABLE IF NOT EXISTS objects(object_id TEXT,revision INTEGER,hash TEXT NOT NULL,path TEXT NOT NULL,PRIMARY KEY(object_id,revision));
CREATE TABLE IF NOT EXISTS snapshots(sequence INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT NOT NULL);''')

    def close(self):
        self.db.close()

    def latest_snapshot(self):
        row = self.db.execute("SELECT payload FROM snapshots ORDER BY sequence DESC LIMIT 1").fetchone()
        return ScienceSnapshot.model_validate_json(row[0]) if row else None

    def snapshot(self, snapshot_id):
        for (payload,) in self.db.execute("SELECT payload FROM snapshots"):
            snapshot = ScienceSnapshot.model_validate_json(payload)
            if snapshot.snapshot_id == snapshot_id: return snapshot
        raise ValueError("unknown snapshot")

    def object_commit_sequence(self, ref: ObjectRef):
        self.load(ref)
        for sequence, payload in self.db.execute("SELECT sequence,payload FROM snapshots ORDER BY sequence"):
            if ref in ScienceSnapshot.model_validate_json(payload).object_refs: return sequence
        raise ValueError("object not in committed snapshot")

    def acquisition_commit_sequence(self, acquisition_id: str):
        """First authoritative evidence time survives aliases and revisions."""
        from .contracts import Observation
        for sequence, payload in self.db.execute("SELECT sequence,payload FROM snapshots ORDER BY sequence"):
            for ref in ScienceSnapshot.model_validate_json(payload).object_refs:
                obj = self.load(ref)
                if isinstance(obj, Observation) and obj.acquisition_id == acquisition_id:
                    return sequence
        raise ValueError("unknown acquisition")

    def load(self, ref: ObjectRef):
        row = self.db.execute("SELECT hash,path FROM objects WHERE object_id=? AND revision=?", (ref.object_id, ref.revision)).fetchone()
        if not row or row[0] != ref.content_hash:
            raise ValueError("uncommitted or mismatched object reference")
        obj = parse_object(json.loads((self.root / row[1]).read_text()))
        if obj.ref() != ref:
            raise ValueError("artifact identity mismatch")
        return obj

    def commit(self, objects: Iterable[ScientificObject], *, task_ref: ObjectRef, evidence_cutoff: str, expected_sequence: int | None = None):
        if self.read_only:
            raise PermissionError("legacy/read-only repository cannot be modified")
        # Revalidate mutable nested containers before persistence.
        objects = [parse_object(obj.model_dump(mode="json")) for obj in objects]
        keys = [(obj.object_id, obj.revision) for obj in objects]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate object ID/revision in commit")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            latest = self.latest_snapshot()
            sequence = latest.committed_sequence if latest else 0
            if expected_sequence is not None and sequence != expected_sequence:
                raise ValueError("snapshot sequence conflict")
            for obj in objects:
                if self.db.execute("SELECT 1 FROM objects WHERE object_id=? AND revision=?", (obj.object_id, obj.revision)).fetchone():
                    raise ValueError("duplicate committed object ID/revision")
                previous = self.db.execute("SELECT MAX(revision) FROM objects WHERE object_id=?", (obj.object_id,)).fetchone()[0]
                if obj.revision != (previous or 0) + 1:
                    raise ValueError("object revision conflict")
            incoming = {obj.ref().model_dump_json(): obj for obj in objects}
            def references(value):
                if isinstance(value, ObjectRef):
                    yield value
                elif isinstance(value, ScientificObject):
                    for name in type(value).model_fields:
                        if name != "unconsumed_metadata": yield from references(getattr(value, name))
                elif isinstance(value, dict):
                    if set(value) == {'external_scientific_ref'}:
                        external = value['external_scientific_ref']
                        if not isinstance(external, dict) or set(external) != {'repository_id','snapshot_id','object_ref'}:
                            raise ValueError('invalid external scientific reference envelope')
                        if not all(isinstance(external[key], str) and external[key] for key in ('repository_id','snapshot_id')):
                            raise ValueError('external scientific reference requires library and snapshot identity')
                        ObjectRef.model_validate(external['object_ref'])
                        # Explicitly external dependencies are validated by their frozen
                        # library consumer, never relabelled as local scientific truth.
                    elif set(value) == {'object_id', 'revision', 'content_hash'}:
                        yield ObjectRef.model_validate(value)
                    else:
                        for name, item in value.items():
                            if name != 'legacy_projection': yield from references(item)
                elif isinstance(value, (tuple, list)):
                    for item in value: yield from references(item)
            for ref in [task_ref] + [ref for obj in objects for ref in references(obj)]:
                if ref.model_dump_json() not in incoming:
                    self.load(ref)
            task = incoming.get(task_ref.model_dump_json())
            if task is None: task = self.load(task_ref)
            if not isinstance(task, ResearchTask): raise ValueError("task_ref must identify ResearchTask")
            self.fault_hook("before_artifacts")
            for obj in objects:
                relative = f"objects/{obj.content_hash}.json"
                fd, tmp = tempfile.mkstemp(dir=self.root / "objects", prefix=".pending-")
                try:
                    with os.fdopen(fd, "w") as stream:
                        stream.write(obj.model_dump_json())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(tmp, self.root / relative)
                finally:
                    if os.path.exists(tmp): os.unlink(tmp)
                self.db.execute("INSERT INTO objects VALUES(?,?,?,?)", (obj.object_id, obj.revision, obj.content_hash, relative))
            directory_fd = os.open(self.root / "objects", os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
            self.fault_hook("after_artifacts")
            all_refs = {ref.object_id: ref for ref in (latest.object_refs if latest else ())}
            all_refs.update({obj.object_id: obj.ref() for obj in objects})
            refs = tuple(all_refs[key] for key in sorted(all_refs))
            snapshot = ScienceSnapshot(snapshot_id=canonical_hash({"schema_version":"science-snapshot.v2","task_ref":task_ref.model_dump(),"sequence":sequence+1,"refs":[r.model_dump() for r in refs],"cutoff":evidence_cutoff}), committed_sequence=sequence+1, task_ref=task_ref, object_refs=refs, evidence_cutoff=evidence_cutoff)
            self.db.execute("INSERT INTO snapshots(sequence,payload) VALUES(?,?)", (sequence+1, snapshot.model_dump_json()))
            self.fault_hook("before_commit")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        self.fault_hook("after_commit")
        return snapshot

    def rebuild_projection(self):
        latest = self.latest_snapshot()
        return {} if latest is None else {ref.object_id: self.load(ref).model_dump(mode="json") for ref in latest.object_refs}
