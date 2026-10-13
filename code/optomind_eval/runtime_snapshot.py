"""Create a self-contained read-stable offline evaluation runtime without secrets.

Whitelisted source packages only; a concurrent source change invalidates the
snapshot rather than silently mixing revisions. Existing snapshots are immutable.
"""
import hashlib,json,shutil
from pathlib import Path


def create_runtime_snapshot(implementation_root,target):
    root,target=Path(implementation_root).resolve(),Path(target).resolve()
    if target.exists():raise FileExistsError('snapshot destination exists; never overwrite prior evidence')
    ignore=shutil.ignore_patterns('__pycache__','.pytest_cache','.git','*.pyc','api_keys','.env','outputs')
    packages=[(root/'repos/OptoMind-Article/code'/name,target/'OptoMind-Article/code'/name)
              for name in ('optomind_optics','optomind_eval','config','llm','tools') if (root/'repos/OptoMind-Article/code'/name).exists()]
    packages += [(root/'repos/OptoMind-Article/veritmm',target/'OptoMind-Article/veritmm'),
                 (root/'repos/OptoMInd-Review/optomind_research',target/'OptoMInd-Review/optomind_research')]
    bindings=[]
    for source,destination in packages:
        shutil.copytree(source,destination,ignore=ignore)
        bindings.extend((source/file.relative_to(destination),file) for file in destination.rglob('*') if file.is_file())
    scripts=target/'OptoMind-Article/code/scripts';scripts.mkdir()
    for source in (root/'repos/OptoMind-Article/code/scripts').glob('*science*c17*.py'):
        destination=scripts/source.name;shutil.copy2(source,destination);bindings.append((source,destination))
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    mismatch=[str(source) for source,destination in bindings if sha(source)!=sha(destination)]
    manifest={'scope':'frozen candidate production evaluation runtime; no OS security boundary',
              'source_root':str(root),'pythonpath_order':['OptoMind-Article/code','OptoMInd-Review','OptoMind-Article/veritmm'],
              'files':{str(destination.relative_to(target)):sha(destination) for _,destination in bindings},
              'concurrent_copy_mismatches':mismatch,'valid_for_execution':not mismatch}
    (target/'RUNTIME_SNAPSHOT_MANIFEST.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    if mismatch:raise RuntimeError('source changed during snapshot copy; preserve invalid snapshot and use a new destination')
    return manifest
