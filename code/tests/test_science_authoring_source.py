"""Actual offline numerical multilayer research → existing authoring consumers."""
import json
from pathlib import Path
import pytest
from test_article_continuation import _continuation, _request, FakeArchitectureProvider
from test_science_c14_authoring import NegativeArchitecture
from optomind_optics.harness.science.open_research_cli import build_offline_loop
from optomind_optics.harness.science.authoring_source import prepare_scientific_authoring_source, load_scientific_authoring_source
from optomind_optics.harness.article_result_synthesis import QwenArticleResultClaimSynthesizer
from optomind_optics.harness.article_architecture import QwenArticleArchitecturePlanner
from optomind_optics.harness.article_writing import QwenSectionWriter


@pytest.fixture(scope='module')
def numerical_source(tmp_path_factory):
    root=tmp_path_factory.mktemp('numerical-source')
    loop,resources=build_offline_loop(root/'research',{'fixture':'multilayer','family':'F3','world_seed':13})
    try:
        selected=loop.run(root/'research')
    finally:
        for resource in resources:
            resource.close()
    selection=prepare_scientific_authoring_source(root/'research/science',selected.ref(),root/'source')
    return root,selection,selected


class ScientificReplayClient:
    model_name='qwen3.7-flash'
    def __init__(self,stage,seen): self.stage=stage; self.seen=seen
    def call(self,messages,**kwargs):
        payload=json.loads(messages[1]['content']); self.seen[self.stage].append(payload)
        if self.stage=='synthesis':
            findings=[]
            for claim in payload['scientific_selection']['scientific_claims']:
                refs={r['object_id'] for r in claim['evidence_refs']}
                artifacts=[a['alias'] for a in payload['artifacts'] if any(r in a['content_summary'] for r in refs)]
                values=[v['alias'] for v in payload['trusted_values'] if v['artifact_alias'] in artifacts and v['prose_safe']]
                findings.append({'scientific_claim_id':claim['object_id'],
                    'statement_with_value_tokens':claim['statement'],
                    'source_value_aliases':(values[:2] or artifacts[:1]),
                    'role':'result' if claim['status']=='supported' else 'limitation',
                    'rationale':'Exact immutable adopted claim.', 'scope_limits':json.dumps(claim['scope'])})
            response={'findings':findings}
        elif self.stage=='architecture':
            response={'stories':NegativeArchitecture()([payload])[0].stories}
        else:
            paragraphs=[]
            for claim in payload['claims']:
                negative=claim['status'] in {'draft','refuted'}
                paragraphs.append({'text_with_value_tokens':
                    'This outcome remains unresolved or unsupported within the recorded synthetic scope.' if negative else
                    'The source reports a model-conditional result within its stated synthetic scope.',
                    'claim_aliases':[claim['claim_alias']],
                    'figure_aliases':[] if negative else payload['section']['figure_aliases'],
                    'paragraph_role':'limitation' if negative else 'result',
                    'inference_kind':'bounded_inference','inference_note':'Original scientific scope and status retained.'})
            response={'paragraphs':paragraphs,'deferred_claim_aliases':[],'author_notes':[]}
        return {'content':json.dumps(response),'_llm_usage':{'model_name':self.model_name,'mock_llm':True}}


def test_real_multilayer_selected_source_reaches_existing_message_consumers(numerical_source,tmp_path):
    root,selection,selected=numerical_source
    source=load_scientific_authoring_source(root/'source',selection)
    assert source.source_kind=='scientific_result' and not source.executions
    assert not hasattr(source.assets[0].candidates[0],'certificate_id')
    assert source.assets[0].trusted_values
    assert source.assets[0].candidates[0].target_score is None
    seen={name:[] for name in ('synthesis','architecture','writing')}
    continuation,_=_continuation(
        synthesis=QwenArticleResultClaimSynthesizer(client=ScientificReplayClient('synthesis',seen)),
        architecture=QwenArticleArchitecturePlanner(client=ScientificReplayClient('architecture',seen)),
        writer=QwenSectionWriter(client=ScientificReplayClient('writing',seen)))
    request=_request(root/'source',tmp_path/'body',source_kind='scientific_result',scientific_selection=selection)
    result=continuation.run(request)
    assert len(result.receipts)==5,(result.errors,result.warnings)
    assert result.stage_payloads.manuscript is not None
    for stage,payloads in seen.items():
        assert payloads,stage
        assert all(p['scientific_selection']['adopted_result_ref']==selected.ref().model_dump(mode='json') for p in payloads)
    assert all(r.adopted_result_ref==selected.ref() for r in result.receipts)
    assert any(i.startswith('scientific_result:') for i in result.receipts[0].input_ids)
    ledger=result.stage_payloads.result_synthesis.ledger
    assert {c.metadata['scientific_claim_ref']['object_id'] for c in ledger.claims}=={r.object_id for r in selected.claim_refs}
    assert all(c.metadata['scientific_scope'] for c in ledger.claims)
    negative_ids={c.claim_id for c in ledger.claims if c.metadata.get('scientific_status')!='supported'}
    assert negative_ids <= {cid for row in result.stage_payloads.manuscript.source_map for cid in row.claim_ids}
    previous={key:len(value) for key,value in seen.items()}
    assert continuation.resume(request).result_id==result.result_id
    assert previous=={key:len(value) for key,value in seen.items()}


def test_scientific_source_tampering_fails_closed(numerical_source,tmp_path):
    import shutil
    root,selection,_=numerical_source
    shutil.copytree(root/'source',tmp_path/'source')
    artifact=next((tmp_path/'source/objects').glob('*.json'))
    artifact.write_text('{}')
    with pytest.raises(ValueError,match='modified'):
        load_scientific_authoring_source(tmp_path/'source',selection)


def test_prepare_cli_exports_without_any_provider(numerical_source,tmp_path):
    import subprocess,sys
    root,selection,selected=numerical_source
    script=Path(__file__).resolve().parents[1]/'scripts/prepare_scientific_authoring_source.py'
    help_result=subprocess.run([sys.executable,str(script),'--help'],capture_output=True,text=True)
    assert help_result.returncode==0
    ref_file=tmp_path/'ref.json';ref_file.write_text(selected.ref().model_dump_json())
    result=subprocess.run([sys.executable,str(script),'--repository-root',selection.repository_root,
        '--selected-result-ref',str(ref_file),'--out-dir',str(tmp_path/'export')],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert json.loads(result.stdout)['provider_calls']==0
    assert load_scientific_authoring_source(tmp_path/'export',selection).source_kind=='scientific_result'


def test_canonical_unknown_and_refuted_outcomes_survive_source_projection(numerical_source,tmp_path):
    import shutil
    from optomind_optics.harness.science.repository import ScienceRepository
    from optomind_optics.harness.science.contracts import ScientificClaim,SelectedResearchResult
    root,_,selected=numerical_source
    shutil.copytree(root/'research/science',tmp_path/'science')
    repo=ScienceRepository(tmp_path/'science')
    negative=[ScientificClaim(object_id='source-fixture-unknown',source_run_id=selected.source_run_id,
        statement='Manufactured-device performance remains unknown.',status='unknown',
        evidence_refs=selected.asset_refs,scope={'fixture_adjudication':True,'manufactured_design_validation':False}),
        ScientificClaim(object_id='source-fixture-refuted',source_run_id=selected.source_run_id,
        statement='Manufactured-device validation was performed.',status='refuted',
        evidence_refs=selected.asset_refs,scope={'fixture_adjudication':True,'manufactured_design_validation':False})]
    data=selected.model_dump();data.update(content_hash='',revision=selected.revision+1,
        claim_refs=[*selected.claim_refs,*(c.ref() for c in negative)],
        negative_result_refs=[*selected.negative_result_refs,*(c.ref() for c in negative)])
    revised=SelectedResearchResult.model_validate(data)
    repo.commit([*negative,revised],task_ref=repo.latest_snapshot().task_ref,evidence_cutoff='offline-negative-adjudication-fixture')
    repo.close()
    selection=prepare_scientific_authoring_source(tmp_path/'science',revised.ref(),tmp_path/'source')
    test_real_multilayer_selected_source_reaches_existing_message_consumers((tmp_path,selection,revised),tmp_path/'author')


def test_multiple_acquisitions_keep_exact_observation_origins(numerical_source,tmp_path):
    import shutil
    from optomind_optics.harness.science.repository import ScienceRepository
    from optomind_optics.harness.science.contracts import Observation,SelectedResearchResult
    root,_,selected=numerical_source
    shutil.copytree(root/'research/science',tmp_path/'science')
    repo=ScienceRepository(tmp_path/'science')
    # Reuse a real earlier acquisition that the actual numerical loop recorded.
    old=next(repo.load(ref) for ref in repo.latest_snapshot().object_refs
             if isinstance(repo.load(ref),Observation) and ref not in selected.asset_refs)
    data=selected.model_dump();data.update(content_hash='',revision=selected.revision+1,
        asset_refs=[*selected.asset_refs,old.ref()])
    revised=SelectedResearchResult.model_validate(data)
    repo.commit([revised],task_ref=repo.latest_snapshot().task_ref,evidence_cutoff='additional-real-acquisition-fixture')
    repo.close()
    selection=prepare_scientific_authoring_source(tmp_path/'science',revised.ref(),tmp_path/'source')
    bundle=load_scientific_authoring_source(tmp_path/'source',selection)
    origins=set()
    repo=ScienceRepository(tmp_path/'science',read_only=True)
    try:
        for descriptor in bundle.assets[0].descriptors:
            obj=repo.load(selection.artifact_bindings[descriptor.artifact_id])
            expected=[obj.object_id] if isinstance(obj,Observation) else []
            assert descriptor.source_observation_ids==expected
            origins.update(expected)
    finally:
        repo.close()
    assert len(origins)>=2
