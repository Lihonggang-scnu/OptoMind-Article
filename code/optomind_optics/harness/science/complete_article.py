"""Finish the accepted author chain, then reuse shared complete-Article delivery.

No Review BODY/parts/03 generation occurs: Article's existing structure and
front-matter writer/validator consume the adopted body and its paragraph map.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from .contracts import canonical_hash
from .repository import ScienceRepository
from .article_bridge import load_result_bundle, deliver_repository_article


def _fixture_json(value):
    if callable(value):
        import inspect
        return {'offline_fixture_callable':value.__module__+'.'+value.__qualname__,
                'source_sha256':hashlib.sha256(inspect.getsource(value).encode()).hexdigest()}
    raise TypeError('complete Article configuration must be JSON serializable')


def _persist_bound(path, payload):
    text=json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, default=_fixture_json)+'\n'
    if path.exists() and path.read_text(encoding='utf-8') != text:
        raise ValueError('complete-article artifact binding changed: '+path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text,encoding='utf-8')
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    checksum=path.with_name(path.name+'.sha256')
    if checksum.exists() and checksum.read_text().strip()!=digest:
        raise ValueError('complete-article checkpoint checksum mismatch')
    checksum.write_text(digest+'\n')


def _read_bound(path):
    checksum=path.with_name(path.name+'.sha256')
    if not checksum.is_file() or checksum.read_text().strip()!=hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError('complete-article checkpoint checksum mismatch: '+path.name)
    return json.loads(path.read_text())


def complete_and_deliver_article(*, continuation_result, selection, source_identity_map,
                                 citation_namespace, material_records, config, out_dir,
                                 structure_provider=None, front_matter_provider=None, **delivery_options):
    from ..article_continuation import ContinuationResult, compute_continuation_result_id
    from ..article_full_structure import build_full_structure
    from ..article_presentation import (_build_front_matter_request, _validate_front_matter,
                                       ProviderResult, ReferenceRecord)
    from .authoring_selection import AuthoringSelection
    result=ContinuationResult.model_validate(continuation_result.model_dump(mode='json') if hasattr(continuation_result,'model_dump') else continuation_result)
    selection=AuthoringSelection.model_validate(selection.model_dump(mode='json') if hasattr(selection,'model_dump') else selection)
    if result.result_id != compute_continuation_result_id(result):
        raise ValueError('continuation result hash mismatch')
    stages=result.stage_payloads
    if result.status not in {'completed','partial'} or stages.manuscript is None or stages.manuscript.errors or stages.manuscript.blocked_handoff:
        raise ValueError('complete accepted Article body required')
    if len(result.receipts)!=5 or any(r.adopted_result_ref!=selection.selected_result_ref for r in result.receipts):
        raise ValueError('author-chain adopted result lineage mismatch')
    context=stages.result_synthesis.scientific_selection
    if not context or context['binding_hash']!=canonical_hash(selection):
        raise ValueError('authoring selection binding mismatch')
    repo=ScienceRepository(selection.repository_root,read_only=True)
    try:
        load_result_bundle(repo,selection.selected_result_ref)
        output=Path(out_dir).resolve(); output.mkdir(parents=True,exist_ok=True)
        binding={'continuation_result_id':result.result_id,'selection':selection.model_dump(mode='json'),
                 'source_identity_map':source_identity_map,'citation_namespace':citation_namespace,
                 'material_records':material_records,'config':config}
        _persist_bound(output/'COMPLETE_ARTICLE_INPUT.json',binding)
        synthesis=stages.result_synthesis
        # Existing immutable-section coordinator preserves all source rows.
        structure_path=output/'FULL_STRUCTURE.json'
        if structure_path.exists():
            from ..article_full_structure import FullStructureResult
            structure=FullStructureResult.model_validate(_read_bound(structure_path))
        else:
            wrapped=None
            if structure_provider is not None:
                def wrapped(payloads):
                    requests=[{**p,'scientific_selection':context} for p in payloads]
                    _persist_bound(output/'FULL_STRUCTURE_REQUEST.json',requests)
                    responses=list(structure_provider(requests))
                    _persist_bound(output/'FULL_STRUCTURE_RESPONSE.json',[r.model_dump(mode='json') for r in responses])
                    return responses
            structure=build_full_structure(synthesis.derived_plan,synthesis.ledger,stages.architecture,stages.review,
                stages.manuscript,result.selected_story_id,provider=wrapped)
            if structure.validation_errors:
                raise ValueError('full structure rejected: '+str(structure.validation_errors))
            _persist_bound(structure_path,structure.model_dump(mode='json'))
        original={p.paragraph_id:p.model_dump(mode='json') for p in stages.manuscript.source_map}
        arranged={p['paragraph_id']:p for p in structure.source_map}
        if arranged!=original:
            raise ValueError('full structure changed immutable body or source bindings')
        references=[]; evidence_to_handles={}
        for handle,identity in source_identity_map.items():
            bare=handle.split('::')[-1]
            if '::' in handle and handle.split('::')[0]!=citation_namespace:
                raise ValueError('source namespace mismatch')
            canonical=identity.get('canonical_paper_id') or identity.get('paper_id') or identity.get('doi')
            if not canonical or not identity.get('title'):
                raise ValueError('complete article requires exact source identity and title')
            references.append(ReferenceRecord(reference_id=str(canonical),reference_alias=bare,
                paper_ids=[str(canonical)],title=identity['title'],doi=identity.get('doi',''),
                year=identity.get('year'),authors=identity.get('authors',[])))
            for evidence_id in identity.get('evidence_ids',[]):
                evidence_to_handles.setdefault(evidence_id,[]).append(bare)
        front_path=output/'FRONT_MATTER.json'; back_path=output/'BACK_MATTER.json'
        source_has_conclusion=any(s.heading.casefold().strip() in {'conclusion','conclusions','结论'} for s in stages.manuscript.body.sections)
        if front_path.exists() and back_path.exists():
            from ..article_presentation import FrontMatter
            front=FrontMatter.model_validate(_read_bound(front_path))
            back=_read_bound(back_path)
        else:
            if front_matter_provider is None:
                raise ValueError('front matter provider or bound replay required; no fabricated full draft')
            request=_build_front_matter_request(plan=synthesis.derived_plan,manuscript=stages.manuscript,references=references)
            request['scientific_selection']=context
            if not source_has_conclusion:
                request['response_contract']['conclusion_sentences']=[{'sentence':'string','paragraph_aliases':['existing paragraph ID']}]
                request['task']+=' Also provide source-bound conclusion_sentences; preserve unknown and negative outcomes.'
            envelope=front_matter_provider(request)
            envelope=ProviderResult.model_validate(envelope.model_dump(mode='json') if hasattr(envelope,'model_dump') else envelope)
            warnings=[]
            front=_validate_front_matter(envelope.response,manuscript=stages.manuscript,warnings=warnings)
            if front is None:
                raise ValueError('Article front matter failed existing source-bound validation: '+str(warnings))
            back={'sentences':[],'existing_body_conclusion':source_has_conclusion}
            if not source_has_conclusion:
                conclusion=_validate_front_matter({'title':'Conclusion','abstract_sentences':envelope.response.get('conclusion_sentences',[]),'keywords':[]},
                    manuscript=stages.manuscript,warnings=warnings)
                if conclusion is None:
                    raise ValueError('Article conclusion failed existing source-bound validation')
                back['sentences']=conclusion.abstract_sentences
            _persist_bound(output/'FRONT_MATTER_REQUEST.json',request)
            _persist_bound(output/'FRONT_MATTER_RESPONSE.json',envelope.model_dump(mode='json'))
            _persist_bound(front_path,front.model_dump(mode='json'));_persist_bound(back_path,back)
        sections={s.section_id:s for s in stages.manuscript.body.sections}
        chunks=['# '+front.title,'## Abstract',' '.join(row['sentence'] for row in front.abstract_sentences)]
        if front.keywords: chunks.append('Keywords: '+', '.join(front.keywords))
        for row in structure.section_order:
            section=sections[row.source_section_id];chunks.append('## '+section.heading)
            for paragraph in section.paragraphs:
                text=paragraph.rendered_text
                handles=[]
                for evidence_id in paragraph.literature_evidence_ids:
                    mapped=evidence_to_handles.get(evidence_id,[])
                    if len(mapped)!=1:
                        raise ValueError('missing or ambiguous paragraph literature identity: '+evidence_id)
                    handles.extend(mapped)
                if handles:
                    text+=' ['+'; '.join(citation_namespace+'::'+handle for handle in sorted(set(handles)))+']'
                chunks.append(text)
        if back['sentences']:
            chunks.extend(['## Conclusion',' '.join(row['sentence'] for row in back['sentences'])])
        draft='\n\n'.join(chunks).rstrip()+'\n'
        path=output/'ARTICLE_COMPLETE_HANDLE.md'
        if path.exists() and path.read_text(encoding='utf-8')!=draft:
            raise ValueError('complete Article manuscript changed during resume')
        path.write_text(draft,encoding='utf-8')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        _persist_bound(output/'SELECTED_COMPLETE_ARTICLE.json',{'path':str(path),'sha256':digest,
            'adopted_result_ref':selection.selected_result_ref.model_dump(mode='json'),
            'source_body_id':stages.manuscript.body_id,'structure_result_id':structure.result_id,
            'front_matter_sha256':hashlib.sha256(front_path.read_bytes()).hexdigest()})
        # Conservative opt-in preservation: the shared checker is an editing
        # executor, not a semantic entailment verifier. Never let it silently
        # remove or promote authoritative unknown/refuted source paragraphs.
        from .authoring_selection import required_negative_claim_ids
        from collections import Counter
        negative_ids = required_negative_claim_ids(synthesis.ledger)
        negative_paragraphs = {p.paragraph_id for p in stages.manuscript.source_map
                               if negative_ids.intersection(p.claim_ids)}
        protected = Counter(p.rendered_text for p in stages.manuscript.source_map
                            if p.paragraph_id in negative_paragraphs)
        for sentence in [*front.abstract_sentences, *back['sentences']]:
            if negative_paragraphs.intersection(sentence.get('paragraph_aliases', [])):
                protected[sentence['sentence']] += 1
        delivery_config = {**config, 'scientific_protected_paragraphs': [
            {'text': text, 'minimum_occurrences': count}
            for text, count in sorted(protected.items())]}
        report=deliver_repository_article(repository=repo,selected_result_ref=selection.selected_result_ref,
            manuscript_path=path,manuscript_sha256=digest,source_identity_map=source_identity_map,
            citation_namespace=citation_namespace,material_records=material_records,config=delivery_config,out_dir=output/'delivery',**delivery_options)
        report['complete_article_input']={'path':str(path),'sha256':digest}
        report['author_chain_receipts']=[r.model_dump(mode='json') for r in result.receipts]
        (output/'COMPLETE_ARTICLE_RESULT.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        return report
    finally:
        repo.close()
