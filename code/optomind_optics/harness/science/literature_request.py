"""C13's opt-in adapter to Review's existing evidence service.

No matcher, retrieval cache, reader, credentials or live client is created here.
The host supplies already authorized Review index/resolver/reader capabilities.
Literature remains source-attributed proposal context, never raw likelihood data.
"""
from .contracts import canonical_hash


class ReviewEvidenceProvider:
    def __init__(self, *, index, source_resolver, protocol_id, directed_reader=None, supplementary_search=None):
        if not protocol_id:raise ValueError("versioned evidence provider identity required")
        self.index=index;self.source_resolver=source_resolver;self.protocol_id=protocol_id
        self.directed_reader=directed_reader;self.supplementary_search=supplementary_search

    def request(self, request):
        from optomind_research.runtime.upgrade3.article_evidence import collect_evidence
        return collect_evidence(request,index=self.index,source_resolver=self.source_resolver,
                                directed_reader=self.directed_reader,supplementary_search=self.supplementary_search)


class ReplayEvidenceProvider:
    """Read frozen source units through the same public EvidenceBundle contract."""
    def __init__(self, source_units):
        self.source_units=tuple(source_units)
        self.protocol_id="offline-evidence-replay:"+canonical_hash(self.source_units)

    def request(self, request):
        from optomind_research.runtime.upgrade3.article_evidence import build_evidence_bundle
        return build_evidence_bundle(request,self.source_units[:request.budget["max_papers"]],
            diagnostics=({"origin":"offline_source_replay","paid_calls":0},))
