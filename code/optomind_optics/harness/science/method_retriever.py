from .contracts import StrictModel, ObjectRef, canonical_hash
from .applicability import check_applicability

class RetrievalReceipt(StrictModel):
    library_snapshot: str
    query_hash: str
    selected: tuple[ObjectRef, ...]
    rejected: tuple[tuple[ObjectRef, tuple[str, ...]], ...]

def retrieve(registry, facts, *, max_cost=float("inf")):
    snapshot=registry.snapshot(); accepted=[]; rejected=[]
    for ref in snapshot.asset_refs:
        asset=registry.repository.load(ref); check=check_applicability(asset,facts)
        reasons=check.reasons + (("cost",) if asset.cost > max_cost else ())
        if reasons: rejected.append((ref,reasons))
        else: accepted.append(asset)
    accepted.sort(key=lambda a:(len(a.negative_evidence), a.cost, -len(a.positive_evidence), a.object_id))
    return RetrievalReceipt(library_snapshot=snapshot.snapshot_id,query_hash=canonical_hash(facts),selected=tuple(a.ref() for a in accepted),rejected=tuple(rejected))
