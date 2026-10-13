"""Verified three-product release manifests and atomic local activation history.

Activation selects an immutable manifest; it does not assert production quality,
rewrite legacy entrypoints, execute remote jobs, or silently enable new modes.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Literal
from pydantic import Field, model_validator
from .contracts import StrictModel, canonical_hash

PRODUCTS = {"Article", "Review", "VeriTMM"}


class ProductVersion(StrictModel):
    product: Literal["Article", "Review", "VeriTMM"]
    revision: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    entrypoints: tuple[str, ...] = Field(min_length=1)
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    package_version: str | None = None


class SnapshotArtifact(StrictModel):
    role: Literal["methods", "policy", "data", "tools", "reproduction", "human_delivery", "coverage"]
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license_id: str = Field(min_length=1)
    @model_validator(mode="after")
    def relative(self):
        if Path(self.path).is_absolute() or '..' in Path(self.path).parts:
            raise ValueError("release artifact path must stay inside bundle")
        return self


class CompatibilityRow(StrictModel):
    versions: dict[str, str]
    modes: tuple[Literal["legacy", "shadow", "active"], ...]
    regression_receipt: str = Field(min_length=1)
    @model_validator(mode="after")
    def complete(self):
        if set(self.versions) != PRODUCTS or not self.modes:
            raise ValueError("compatibility row requires all three products and supported modes")
        return self


class ReleaseManifest(StrictModel):
    schema_version: Literal["release-manifest.v1"] = "release-manifest.v1"
    release_id: str = Field(min_length=1)
    products: tuple[ProductVersion, ...]
    compatibility: tuple[CompatibilityRow, ...] = Field(min_length=1)
    artifacts: tuple[SnapshotArtifact, ...] = Field(min_length=1)
    allowed_modes: tuple[Literal["legacy", "shadow", "active"], ...] = ("legacy", "shadow")
    engineering_status: Literal["partial", "passed"]
    real_quality_status: Literal["not_run", "partial", "passed"]
    unsupported_claims: tuple[str, ...]
    @model_validator(mode="after")
    def closed(self):
        if len(self.products) != 3 or {p.product for p in self.products} != PRODUCTS:
            raise ValueError("release must version exactly three products")
        if len({a.path for a in self.artifacts}) != len(self.artifacts):
            raise ValueError("duplicate artifact paths")
        roles = {a.role for a in self.artifacts}
        if roles != {"methods", "policy", "data", "tools", "reproduction", "human_delivery", "coverage"}:
            raise ValueError("release missing required snapshot or dual-delivery role")
        if not self.allowed_modes or len(set(self.allowed_modes)) != len(self.allowed_modes):
            raise ValueError("mode list must be nonempty and unique")
        return self

    def verify(self, root, mode):
        if mode not in self.allowed_modes:
            raise ValueError("release mode not allowed")
        versions = {p.product:p.revision for p in self.products}
        rows = [r for r in self.compatibility if r.versions == versions and mode in r.modes]
        if not rows:
            raise ValueError("unverified version/mode compatibility")
        root = Path(root).resolve()
        for artifact in self.artifacts:
            path = (root/artifact.path).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("missing or escaping release artifact")
            if hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256:
                raise ValueError("release artifact checksum mismatch: " + artifact.path)
        # Receipts must be part of the verified bundle, not arbitrary assertion text.
        if any(row.regression_receipt not in {a.path for a in self.artifacts} for row in rows):
            raise ValueError("compatibility regression receipt is not in verified bundle")
        if mode == "active" and self.engineering_status != "passed":
            raise ValueError("active mode requires passing engineering regression")
        return canonical_hash(self)


class ReleaseManager:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root/'releases.sqlite3', timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("CREATE TABLE IF NOT EXISTS releases (id TEXT PRIMARY KEY, digest TEXT NOT NULL, manifest TEXT NOT NULL, bundle TEXT NOT NULL); CREATE TABLE IF NOT EXISTS transitions (sequence INTEGER PRIMARY KEY AUTOINCREMENT, release_id TEXT NOT NULL, mode TEXT NOT NULL, reason TEXT NOT NULL, rollback_of INTEGER);")

    def close(self):
        self.db.close()

    def current(self):
        self.db.row_factory = sqlite3.Row
        row = self.db.execute('SELECT * FROM transitions ORDER BY sequence DESC LIMIT 1').fetchone()
        return dict(row) if row else None

    def history(self):
        self.db.row_factory = sqlite3.Row
        return [dict(row) for row in self.db.execute('SELECT * FROM transitions ORDER BY sequence')]

    def preview(self, manifest, bundle, mode):
        manifest = ReleaseManifest.model_validate_json(manifest.model_dump_json())
        digest = manifest.verify(bundle, mode)
        current = self.current()
        if mode == 'active' and (not current or current['mode'] != 'shadow' or current['release_id'] != manifest.release_id):
            raise ValueError('active transition must follow shadow of the exact release')
        return {'release_id':manifest.release_id, 'manifest_hash':digest, 'mode':mode, 'previous':current, 'real_quality_status':manifest.real_quality_status, 'unsupported_claims':manifest.unsupported_claims}

    def activate(self, manifest, bundle, mode, *, expected_sequence, reason):
        if not isinstance(reason,str) or not reason.strip():
            raise ValueError("release transition requires an audit reason")
        self.db.execute('BEGIN IMMEDIATE')
        try:
            preview = self.preview(manifest, bundle, mode)
            current = preview['previous']
            if (current['sequence'] if current else 0) != expected_sequence:
                raise ValueError('release sequence conflict')
            row = self.db.execute('SELECT digest,bundle FROM releases WHERE id=?',(manifest.release_id,)).fetchone()
            resolved = str(Path(bundle).resolve())
            if row and (row[0] != preview['manifest_hash'] or row[1] != resolved):
                raise ValueError('release ID cannot be rebound')
            self.db.execute('INSERT OR IGNORE INTO releases VALUES (?,?,?,?)',(manifest.release_id,preview['manifest_hash'],manifest.model_dump_json(),resolved))
            self.db.execute('INSERT INTO transitions(release_id,mode,reason) VALUES (?,?,?)',(manifest.release_id,mode,reason))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return self.current()

    def rollback(self, sequence, *, expected_sequence, reason):
        if not isinstance(reason,str) or not reason.strip():
            raise ValueError("release rollback requires an audit reason")
        self.db.execute('BEGIN IMMEDIATE')
        try:
            current = self.current()
            if not current or current['sequence'] != expected_sequence:
                raise ValueError('release sequence conflict')
            prior = self.db.execute('SELECT release_id,mode FROM transitions WHERE sequence=?',(sequence,)).fetchone()
            if not prior or sequence >= current['sequence']:
                raise ValueError('rollback requires an earlier committed transition')
            record = self.db.execute('SELECT digest,manifest,bundle FROM releases WHERE id=?',(prior[0],)).fetchone()
            manifest = ReleaseManifest.model_validate_json(record[1])
            if manifest.verify(record[2],prior[1]) != record[0]:
                raise ValueError('stored release manifest corrupted')
            self.db.execute('INSERT INTO transitions(release_id,mode,reason,rollback_of) VALUES (?,?,?,?)',(prior[0],prior[1],reason,current['sequence']))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return self.current()


def resolve_runtime_identities():
    """Derive product identity from resolved production modules, never JSON claims.

    Git HEAD (or installed distribution version) gives provenance; source-tree
    hashes bind dirty working trees and unpacked installations. Missing runtime
    products or provenance are explicit unknowns, not caller-provided fallbacks.
    """
    import importlib.util
    import importlib.metadata
    import subprocess
    modules={"Article":"optomind_optics.harness.article_pipeline_factory",
             "Review":"optomind_research.runtime.upgrade3.manuscript_delivery",
             "VeriTMM":"tmm_engine"}
    distributions={"Article":("optomind-optics",),"Review":("optomind-research",),"VeriTMM":("veritmm",)}
    identities={}
    for product,name in modules.items():
        try:
            spec=importlib.util.find_spec(name)
        except (ImportError,ModuleNotFoundError,ValueError) as exc:
            raise ValueError("runtime identity unavailable for "+product) from exc
        if spec is None or not spec.origin or not Path(spec.origin).is_file():
            raise ValueError("runtime source identity unavailable for "+product)
        origin=Path(spec.origin).resolve()
        package_name=name.split('.')[0]
        package_root=next((parent for parent in origin.parents if parent.name==package_name),None)
        if package_root is None:
            raise ValueError("cannot identify actual runtime package root for "+product)
        package_version=None
        for distribution in distributions[product]:
            try:
                package_version=importlib.metadata.version(distribution)
                break
            except importlib.metadata.PackageNotFoundError:
                pass
        revision=None
        try:
            result=subprocess.run(['git','-C',str(package_root),'rev-parse','HEAD'],capture_output=True,text=True,check=True,timeout=5)
            revision=result.stdout.strip()
        except (OSError,subprocess.SubprocessError):
            pass
        if not revision and not package_version:
            raise ValueError("runtime provenance unknown for "+product+"; Git or installed package version required")
        suffixes={'.py','.pyi','.so','.pyd','.json','.yaml','.yml','.toml','.csv','.txt'}
        files=[]
        for path in sorted(package_root.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in suffixes or '__pycache__' in path.parts:
                continue
            files.append((str(path.relative_to(package_root)),hashlib.sha256(path.read_bytes()).hexdigest()))
        if not files:
            raise ValueError("runtime source tree cannot be fingerprinted for "+product)
        identities[product]={"revision":revision or 'package:'+package_version,
                             "source_sha256":canonical_hash(files),"package_version":package_version,
                             "module":name,"module_origin":str(origin),"package_root":str(package_root),
                             "fingerprinted_files":len(files)}
    return identities


def load_release_selection(root, *, runtime_versions=None):
    """Source-read-only selection checked against actual production identities."""
    import shutil
    import tempfile
    root=Path(root).resolve()
    sources=[root/'releases.sqlite3',root/'releases.sqlite3-wal']
    if not sources[0].is_file():
        raise ValueError('no release state database')
    def stamps():
        return tuple((p.exists(),p.stat().st_size,p.stat().st_mtime_ns) if p.exists() else (False,0,0) for p in sources)
    before=stamps()
    # mode=ro can still create WAL/SHM files. Copy a stable DB/WAL pair so all
    # SQLite recovery side effects remain in an ephemeral inspection directory.
    with tempfile.TemporaryDirectory(prefix='optomind-release-inspection-') as directory:
        temporary=Path(directory)
        for source in sources:
            if source.exists():shutil.copyfile(source,temporary/source.name)
        if stamps()!=before:
            raise ValueError('release state changed during inspection; retry stable selection')
        db=sqlite3.connect(f'file:{temporary / "releases.sqlite3"}?mode=ro',uri=True,timeout=30)
        try:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM transitions ORDER BY sequence DESC LIMIT 1').fetchone()
            if row is None:
                raise ValueError('no committed release selection')
            selection=dict(row)
            release=db.execute('SELECT digest,manifest,bundle FROM releases WHERE id=?',(selection['release_id'],)).fetchone()
            if release is None:
                raise ValueError('release selection has no manifest')
            manifest=ReleaseManifest.model_validate_json(release['manifest'])
            if manifest.release_id != selection['release_id']:
                raise ValueError('selected release identity mismatch')
            if manifest.verify(release['bundle'],selection['mode']) != release['digest']:
                raise ValueError('selected release manifest hash mismatch')
            declared={p.product:p.revision for p in manifest.products}
            if runtime_versions and runtime_versions != declared:
                raise ValueError('expected running product versions differ from selected release')
            actual=resolve_runtime_identities()
            for product in manifest.products:
                identity=actual[product.product]
                if product.revision != identity['revision'] or product.source_sha256 is None or product.source_sha256 != identity['source_sha256']:
                    raise ValueError('actual runtime revision/source differs from selected release: '+product.product)
                if product.package_version is not None and product.package_version != identity['package_version']:
                    raise ValueError('actual runtime package version differs from selected release: '+product.product)
            return {**selection,'manifest_hash':release['digest'],'runtime_versions':{k:v['revision'] for k,v in actual.items()},
                    'runtime_identities':actual,'real_quality_status':manifest.real_quality_status,
                    'unsupported_claims':list(manifest.unsupported_claims)}
        finally:
            db.close()


class ProductionReleaseBinding:
    """No-effect shadow valuation; legacy outputs remain the accepted output.

A shadow specification is data for the pure finite controller. Arbitrary policy
plan/execute/finalize callbacks are never invoked in shadow mode.
"""
    def __init__(self, root, runtime_versions, *, shadow_spec=None):
        self.root=root; self.runtime_versions=dict(runtime_versions)
        self.selection=load_release_selection(root,runtime_versions=self.runtime_versions)
        self.shadow_decision=None
        if self.selection['mode'] == 'shadow' and shadow_spec is not None:
            from dataclasses import asdict
            from .controller import decide_next
            # Freeze the pure comparison before any accepted-run outcomes exist.
            self.shadow_decision=asdict(decide_next(**shadow_spec))

    def before_run(self, work_dir):
        from optomind_research.runtime.artifact_store import atomic_write_json
        current=load_release_selection(self.root,runtime_versions=self.runtime_versions)
        if current != self.selection:
            raise ValueError('release selection changed after assembly; rebuild before execution')
        work_dir=Path(work_dir)
        # The accepted entry requires an empty run directory on first run. Bind
        # before effects in a sibling sidecar so that invariant remains intact.
        path=work_dir.parent/('.'+work_dir.name+'.science-release.json')
        if path.exists() and json.loads(path.read_text()) != self.selection:
            raise ValueError('run is bound to a different release; resume cannot change scientific mode')
        atomic_write_json(path,self.selection)

    def after_run(self, work_dir, result):
        if self.selection['mode'] != 'shadow':
            return
        from optomind_research.runtime.artifact_store import atomic_write_json
        report={'mode':'shadow','accepted_source':'legacy','legacy_result_id':result.result_id,
                'external_actions':0,'candidate_published':False,'state':'not_evaluated'}
        if self.shadow_decision is not None:
            report.update(state='evaluated',decision=self.shadow_decision)
        atomic_write_json(Path(work_dir)/'SCIENCE_SHADOW_COMPARISON.json',report)


def compile_shadow_spec(payload):
    """Deserialize a bounded, data-only finite-controller shadow specification."""
    from .terminal_problem import TerminalDecisionProblem
    from .controller import MeasurementAction, decide_next
    required={'problem','probabilities','actions','available_budget','final_validation_reserve'}
    if not isinstance(payload,dict) or not required <= payload.keys() or set(payload)-required-{'depth','unknown_action_ids'}:
        raise ValueError('invalid shadow specification fields')
    problem=dict(payload['problem'])
    if set(problem)!={'world_ids','design_ids','losses','context','units'}:
        raise ValueError('invalid shadow problem fields')
    # Keep offline preview bounded even when input JSON is accidentally huge.
    if not 1 <= len(problem['world_ids']) <= 256 or not 1 <= len(problem['design_ids']) <= 256 or len(payload['actions'])>32:
        raise ValueError('shadow specification exceeds bounded finite controller limits')
    problem['world_ids']=tuple(problem['world_ids']);problem['design_ids']=tuple(problem['design_ids'])
    actions=[]
    for row in payload['actions']:
        item=dict(row)
        item['observation_ids']=tuple(item['observation_ids'])
        item['kernel']=tuple(tuple(values) for values in item['kernel'])
        if len(item['observation_ids'])>64:
            raise ValueError('shadow observation support exceeds bounded limit')
        actions.append(MeasurementAction(**item))
    result={**payload,'problem':TerminalDecisionProblem(**problem),'probabilities':tuple(payload['probabilities']),'actions':tuple(actions)}
    decide_next(**result)  # Validate cost, probability and matrix domains now.
    return result
