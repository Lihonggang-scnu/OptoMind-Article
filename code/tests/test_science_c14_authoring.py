"""Synthetic replay of the real existing author chain, not a writing quality score."""
import pytest
from test_article_continuation import (_write_source_pipeline, load_source_pipeline, _continuation, _request,
    FakeSynthesisProvider, FakeArchitectureProvider, FakeWriter)
from optomind_optics.harness.article_result_synthesis import ResultSynthesisProviderResult
from optomind_optics.harness.science.authoring_selection import AuthoringSelection, AuthoringValueBinding, resolve_authoring_selection
from optomind_optics.harness.science.contracts import ResearchTask, ModelCandidate, Observation, ScientificClaim, SelectedResearchResult
from optomind_optics.harness.science.repository import ScienceRepository


def setup_selection(tmp_path):
    source=_write_source_pipeline(tmp_path)
    bundle=load_source_pipeline(source); asset=bundle.assets[0]; value=asset.trusted_values[0]
    repo=ScienceRepository(tmp_path/'science')
    task=ResearchTask(object_id='task',source_run_id='fixture',question='Synthetic binding verification')
    model=ModelCandidate(object_id='adopted-model',source_run_id='fixture',model_ir={'kind':'fixture'})
    obs=Observation(object_id='source-observation',source_run_id='fixture',acquisition_id='fixture',
        candidate_ref=model.ref(),derived_features={'value':float(value.rendered_value),'units':value.unit,'data_kind':'simulation'})
    claim=ScientificClaim(object_id='approved-claim',source_run_id='fixture',statement=f'Recorded absorptance reached {value.rendered_value} within fixture scope.',
        evidence_refs=(obs.ref(),),scope={'fixture_only':True},status='supported')
    selected=SelectedResearchResult(object_id='adopted-result',source_run_id='fixture',candidate_ref=model.ref(),
        claim_refs=(claim.ref(),),asset_refs=(obs.ref(),),candidate_roles={'adopted-model':'selected_design'})
    repo.commit([task,model,obs,claim,selected],task_ref=task.ref(),evidence_cutoff='fixture')
    repo.close()
    selection=AuthoringSelection(repository_root=str(tmp_path/'science'),selected_result_ref=selected.ref(),
        candidate_bindings={asset.candidates[0].candidate_id:model.ref()},artifact_bindings={value.artifact_id:obs.ref()},
        value_bindings=(AuthoringValueBinding(artifact_id=value.artifact_id,field=value.field,source_ref=obs.ref(),
            source_path=('derived_features','value'),units=value.unit,data_kind='simulation'),))
    return source,bundle,selection,selected


class ScopedSynthesis(FakeSynthesisProvider):
    def __init__(self): super().__init__(); self.inputs=[]
    def __call__(self,payload):
        self.calls+=1; self.inputs.append(payload)
        value=payload.trusted_values[0]
        return ResultSynthesisProviderResult(findings=[
            {'scientific_claim_id':'approved-claim','statement_with_value_tokens':f'Recorded absorptance reached {{{value.alias}}} within fixture scope.',
             'source_value_aliases':[value.alias],'role':'result','rationale':'Frozen source-bound fixture.','scope_limits':'Synthetic fixture only.'},
            {'scientific_claim_id':'not-adopted','statement_with_value_tokens':f'Unsupported mechanism reached {{{value.alias}}}.',
             'source_value_aliases':[value.alias],'role':'result','rationale':'Not authorized.','scope_limits':'None.'}],
            provider_model='offline-fixture')


class CapturingArchitecture(FakeArchitectureProvider):
    def __init__(self): super().__init__(); self.inputs=[]
    def __call__(self,payloads): self.inputs.extend(payloads); return super().__call__(payloads)


class CapturingWriter(FakeWriter):
    def __init__(self): super().__init__(); self.inputs=[]
    def __call__(self,payload): self.inputs.append(payload); return super().__call__(payload)


def test_actual_author_chain_consumes_adopted_science_and_excludes_other_candidate(tmp_path):
    source,bundle,selection,selected=setup_selection(tmp_path)
    synthesis=ScopedSynthesis(); architecture=CapturingArchitecture(); writer=CapturingWriter()
    continuation,state=_continuation(synthesis=synthesis,architecture=architecture,writer=writer)
    result=continuation.run(_request(source,tmp_path/'work',scientific_selection=selection))
    assert result.status in {'completed','partial'},result.errors
    assert len(result.receipts)==5,(result.receipts,result.errors,result.warnings)
    assert synthesis.inputs and architecture.inputs and writer.inputs
    expected=selected.ref().model_dump(mode='json')
    assert all(p.scientific_selection['adopted_result_ref']==expected for p in synthesis.inputs)
    assert all(p['scientific_selection']['adopted_result_ref']==expected for p in architecture.inputs+writer.inputs)
    assert all(r.adopted_result_ref==selected.ref() for r in result.receipts)
    assert {c.candidate_id for p in synthesis.inputs for c in p.candidates}==set(selection.candidate_bindings)
    assert all(len(p.trusted_values)==1 for p in synthesis.inputs)
    assert all(not c.global_role_keys for p in synthesis.inputs for c in p.candidates)
    assert all('Unsupported mechanism' not in f.statement_with_value_tokens for f in result.stage_payloads.result_synthesis.findings)
    assert result.stage_payloads.result_synthesis.scientific_selection['adopted_result_ref']==expected
    before=sum(p.calls for p in (synthesis,architecture,writer))
    replay=continuation.resume(_request(source,tmp_path/'work',scientific_selection=selection))
    assert replay.result_id==result.result_id
    assert sum(p.calls for p in (synthesis,architecture,writer))==before


def test_mismatched_numeric_binding_fails_before_provider(tmp_path):
    source,bundle,selection,selected=setup_selection(tmp_path)
    broken=selection.model_copy(update={'value_bindings':(selection.value_bindings[0].model_copy(update={'units':'wrong'}),)})
    continuation,state=_continuation()
    result=continuation.run(_request(source,tmp_path/'work',scientific_selection=broken))
    assert result.status=='failed' and 'units disagree' in str(result.errors)
    assert state['synthesis'].calls==0


def test_real_provider_message_adapters_carry_authority(tmp_path):
    import json
    from optomind_optics.harness.article_result_synthesis import QwenArticleResultClaimSynthesizer, ResultSynthesisProviderInput
    from optomind_optics.harness.article_architecture import QwenArticleArchitecturePlanner
    from optomind_optics.harness.article_writing import QwenSectionWriter
    source,_,selection,selected=setup_selection(tmp_path)
    captured={name:[] for name in ('synthesis','architecture','writing')}
    class ReplayClient:
        model_name='qwen3.7-flash'
        def __init__(self,stage): self.stage=stage
        def call(self,messages,**kwargs):
            captured[self.stage].append(messages)
            payload=json.loads(messages[1]['content'])
            if self.stage=='synthesis':
                response={'findings':ScopedSynthesis()(ResultSynthesisProviderInput.model_validate(payload)).findings}
            elif self.stage=='architecture':
                response={'stories':FakeArchitectureProvider()([payload])[0].stories}
            else:
                response=FakeWriter()(payload).response
            return {'content':json.dumps(response),'_llm_usage':{'model_name':'qwen3.7-flash','mock_llm':True}}
    continuation,_=_continuation(synthesis=QwenArticleResultClaimSynthesizer(client=ReplayClient('synthesis')),
        architecture=QwenArticleArchitecturePlanner(client=ReplayClient('architecture')),
        writer=QwenSectionWriter(client=ReplayClient('writing')))
    result=continuation.run(_request(source,tmp_path/'work',scientific_selection=selection))
    assert len(result.receipts)==5,(result.errors,result.warnings)
    for stage,messages in captured.items():
        assert messages,stage
        for message in messages:
            payload=json.loads(message[1]['content'])
            assert payload['scientific_selection']['adopted_result_ref']==selected.ref().model_dump(mode='json')
            assert payload['scientific_selection']['scientific_claims'][0]['object_id']=='approved-claim'
            assert 'Unsupported mechanism' not in message[1]['content']


def add_negative(selection):
    repo=ScienceRepository(selection.repository_root)
    selected=repo.load(selection.selected_result_ref)
    negative=ScientificClaim(object_id='unknown-outcome',source_run_id='fixture',
        statement='Mechanism remains unresolved at 0.85 within fixture scope.',status='unknown',evidence_refs=selected.asset_refs)
    data=selected.model_dump();data.update(content_hash='',revision=2,claim_refs=[*selected.claim_refs,negative.ref()],negative_result_refs=[negative.ref()])
    revised=SelectedResearchResult.model_validate(data)
    repo.commit([negative,revised],task_ref=repo.latest_snapshot().task_ref,evidence_cutoff='fixture-negative')
    repo.close()
    return selection.model_copy(update={'selected_result_ref':revised.ref()})


def test_omitted_negative_outcome_blocks_authoring(tmp_path):
    source,_,selection,_=setup_selection(tmp_path); selection=add_negative(selection)
    continuation,_=_continuation(synthesis=ScopedSynthesis())
    result=continuation.run(_request(source,tmp_path/'work',scientific_selection=selection))
    assert result.status=='failed'
    assert 'negative/unknown claims omitted' in str(result.errors)


def test_negative_source_without_adopted_claim_still_blocks_authoring(tmp_path):
    from optomind_optics.harness.science.contracts import DecisionContext
    source,bundle,selection,selected=setup_selection(tmp_path)
    repo=ScienceRepository(selection.repository_root)
    try:
        adequacy=DecisionContext(object_id='unclaimed-adequacy',source_run_id='fixture',
            input_refs=(selected.candidate_ref,),summary={'state':'model_family_inadequate'})
        data=selected.model_dump();data.update(content_hash='',revision=2,negative_result_refs=[adequacy.ref()])
        revised=SelectedResearchResult.model_validate(data)
        repo.commit([adequacy,revised],task_ref=repo.latest_snapshot().task_ref,evidence_cutoff='missing-claim-regression')
    finally:
        repo.close()
    selection=selection.model_copy(update={'selected_result_ref':revised.ref()})
    with pytest.raises(ValueError,match='every adopted negative outcome requires a source-bound scientific claim'):
        resolve_authoring_selection(selection,bundle.assets)
    continuation,state=_continuation()
    result=continuation.run(_request(source,tmp_path/'work',scientific_selection=selection))
    assert result.status=='failed' and 'source-bound scientific claim' in str(result.errors)
    assert state['synthesis'].calls==0


class NegativeSynthesis(ScopedSynthesis):
    def __call__(self,payload):
        result=super().__call__(payload); alias=payload.trusted_values[0].alias
        return result.model_copy(update={'findings':[*result.findings,{'scientific_claim_id':'unknown-outcome',
            'statement_with_value_tokens':f'Mechanism remains unresolved at {{{alias}}} within fixture scope.',
            'source_value_aliases':[alias],'role':'limitation','rationale':'Frozen unresolved outcome.','scope_limits':'Fixture only.'}]})


def test_negative_status_cannot_be_promoted_or_omitted_by_architecture(tmp_path):
    source,_,selection,_=setup_selection(tmp_path);selection=add_negative(selection)
    continuation,state=_continuation(synthesis=NegativeSynthesis())
    result=continuation.run(_request(source,tmp_path/'work',scientific_selection=selection))
    synthesis=result.stage_payloads.result_synthesis
    assert synthesis is not None
    unknown=[c for c in synthesis.ledger.claims if c.metadata.get('scientific_status')=='unknown']
    assert unknown and all(c.status.value=='draft' for c in unknown)
    assert all('scientific_claim_ref' in c.metadata for c in synthesis.ledger.claims)
    assert 'negative/unknown claims omitted by architecture' in str(result.errors)


class NegativeArchitecture(FakeArchitectureProvider):
    def __call__(self,payloads):
        results=super().__call__(payloads)
        bindings=[{'claim_id':c['claim_id'],'role':'limitation'} for c in payloads[0]['limitation_claims']]
        stories=[]
        for story in results[0].stories:
            sections=[{**s,'claim_bindings':[*s['claim_bindings'],*bindings]} for s in story['sections']]
            stories.append({**story,'sections':sections})
        return [results[0].model_copy(update={'stories':stories})]


class NegativeWriter(FakeWriter):
    def __call__(self,payload):
        from optomind_optics.harness.article_writing import WriterProviderResult
        positives=[c['claim_alias'] for c in payload['claims'] if c['status']!='draft']
        negatives=[c['claim_alias'] for c in payload['claims'] if c['status']=='draft']
        value=payload['values'][0]['token']
        paragraphs=[{'text_with_value_tokens':'The source reports a bounded response of '+value+'.',
                     'claim_aliases':positives,'figure_aliases':payload['section']['figure_aliases'],
                     'paragraph_role':'result','inference_kind':'bounded_inference','inference_note':'Source-scoped fixture.'},
                    {'text_with_value_tokens':'The mechanism remains unresolved within this limited scope.',
                     'claim_aliases':negatives,'figure_aliases':[],
                     'paragraph_role':'limitation','inference_kind':'bounded_inference','inference_note':'Unresolved source outcome preserved.'}]
        return WriterProviderResult(response={'paragraphs':paragraphs,'deferred_claim_aliases':[],'author_notes':[]},provider_model='offline-fixture')


def test_negative_outcome_survives_real_author_chain(tmp_path):
    source,_,selection,_=setup_selection(tmp_path);selection=add_negative(selection)
    continuation,_=_continuation(synthesis=NegativeSynthesis(),architecture=NegativeArchitecture(),writer=NegativeWriter())
    result=continuation.run(_request(source,tmp_path/'work',scientific_selection=selection))
    assert len(result.receipts)==5,(result.errors,result.warnings)
    assert 'mechanism remains unresolved' in result.stage_payloads.manuscript.body_markdown
    negative_ids={c.claim_id for c in result.stage_payloads.result_synthesis.ledger.claims if c.metadata.get('scientific_status')=='unknown'}
    represented={cid for row in result.stage_payloads.manuscript.source_map for cid in row.claim_ids}
    assert negative_ids <= represented


def test_caller_cannot_relabel_immutable_source_units_or_kind(tmp_path):
    _,bundle,selection,_=setup_selection(tmp_path)
    binding=selection.value_bindings[0]
    for changes in ({'units':'micrometers'},{'data_kind':'measurement'}):
        revised=binding.model_copy(update=changes)
        forged=selection.model_copy(update={'value_bindings':(revised,)})
        assets=[a.model_copy(update={'trusted_values':[v.model_copy(update={'unit':revised.units})
            if (v.artifact_id,v.field)==(binding.artifact_id,binding.field) else v for v in a.trusted_values]}) for a in bundle.assets]
        with pytest.raises(ValueError,match='immutable source metadata'):
            resolve_authoring_selection(forged,assets)
