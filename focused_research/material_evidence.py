"""Lossless import of Review's *verified* PreparedSnapshotProvider exports.

This is a serialization boundary, not a PDF parser or integrity verifier. The
external Review bridge must call PreparedSnapshotProvider.load() before setting
``verified``. Never mark an arbitrary JSON snapshot verified here. Source bytes
stay in Review's immutable snapshot; its complete material export stays in the
returned archive. Only explicit, bounded whole-block selections go to a reader.
Eligibility means source-linked text exists, not relevance or scientific truth.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

try:
    from .evidence_store import SCHEMA, _source
except ImportError:
    from evidence_store import SCHEMA, _source

EXPORT_SCHEMA = 'review_material_export_v1'


def _ids(value, name):
    if not isinstance(value, (list, tuple)) or any(not isinstance(v, str) or not v.strip() for v in value):
        raise ValueError(name + ' must contain nonempty string IDs')
    return list(dict.fromkeys(value))


def _text(value):
    return value if isinstance(value, str) and value.strip() else ''


def import_material_export(export, *, query_ids=(), goal_ids=()):
    """Archive one bridge envelope and derive one evidence item per native block.

    Envelope: schema_version, verified=true, review_commit, snapshot_path,
    source_record, material=PreparedSnapshotProvider(...).load().as_material().
    Native material/block/manifest fields are retained verbatim, including table
    cells, formula fidelity, references, source hashes, geometry, and gaps.
    """
    if not isinstance(export, dict) or export.get('schema_version') != EXPORT_SCHEMA or export.get('verified') is not True:
        raise ValueError('a verified review_material_export_v1 bridge export is required')
    # Also rejects non-JSON runtime objects and non-finite numeric metadata.
    archive = json.loads(json.dumps(export, ensure_ascii=False, allow_nan=False))
    material = archive.get('material')
    if not isinstance(material, dict) or not _text(material.get('snapshot_id')):
        raise ValueError('material.snapshot_id is required')
    manifest, blocks = material.get('manifest'), material.get('blocks')
    if not isinstance(manifest, dict) or not isinstance(blocks, list):
        raise ValueError('material manifest and native blocks are required')
    snapshot_id = material['snapshot_id']
    if manifest.get('snapshot_id') not in (None, snapshot_id):
        raise ValueError('material and manifest snapshot_id disagree')
    queries, goals = _ids(query_ids, 'query_ids'), _ids(goal_ids, 'goal_ids')
    record = archive.get('source_record') or {}
    if not isinstance(record, dict):
        raise ValueError('source_record must be an object')
    queries = _ids([*queries, *_ids(record.get('query_ids', []), 'source_record.query_ids')], 'query_ids')
    goals = _ids([*goals, *_ids(record.get('goal_ids', []), 'source_record.goal_ids')], 'goal_ids')
    paper, aliases, url, doi = _source(record)
    source_id = aliases[0] if aliases else ''
    items, seen = [], set()
    for block in blocks:
        if not isinstance(block, dict) or not _text(block.get('block_id')):
            raise ValueError('every native block requires block_id')
        block_id = block['block_id']
        if block_id in seen:
            raise ValueError('duplicate snapshot/block locator')
        seen.add(block_id)
        source_doc = next((d for d in manifest.get('source_documents', [])
                           if d.get('source_document_id') == block.get('source_document_id')), {})
        block_source_id = source_id or ('sha256:' + source_doc['raw_sha256']
                                       if _text(source_doc.get('raw_sha256')) else '')
        raw, normalized = _text(block.get('text_raw')), _text(block.get('text_normalized'))
        # Do not flatten cells or generate prose from formulas when text is absent.
        text, text_field = (raw, 'text_raw') if raw else (normalized, 'text_normalized')
        kind = block.get('block_type') or 'unknown'
        snippet_type = kind if kind in ('title', 'abstract') else 'body' if kind in (
            'paragraph', 'body', 'body_text', 'table', 'table_cell', 'table_caption',
            'figure_caption', 'footnote', 'formula', 'equation', 'caption') else 'unknown'
        missing = ([] if block_source_id else ['missing_source']) + ([] if text else ['missing_original_text'])
        identity = [snapshot_id, block_id, text_field, text]
        eid = 'E_' + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        mapping = dict(provider='review_prepared_snapshot', retrieval_source='verified_material_snapshot',
            snapshot_id=snapshot_id, block_id=block_id,
            source_document_id=block.get('source_document_id'), source_document=deepcopy(source_doc), locator=deepcopy(block.get('locator')),
            source_ids=deepcopy(aliases), source_url=url, source_hashes=deepcopy(manifest.get('source_documents', [])),
            query_ids=list(queries), goal_ids=list(goals), text_field=text_field,
            review_commit=archive.get('review_commit'), snapshot_path=archive.get('snapshot_path'))
        items.append(dict(evidence_id=eid, source_id=block_source_id, source_url=url,
            title=paper.get('title') or '', authors=paper.get('authors') or [], year=paper.get('year'),
            doi=doi, text=text, snippet_type=snippet_type, source_material_type=kind,
            snapshot_id=snapshot_id, block_id=block_id, query_ids=list(queries), goal_ids=list(goals),
            source_mappings=[mapping], eligible_as_evidence=not missing, missing_reasons=missing,
            material_block=deepcopy(block),
            material_assets=[deepcopy(a) for a in material.get('assets', [])
                             if a.get('asset_id') in block.get('asset_refs', [])],
            known_gaps=deepcopy(manifest.get('known_gaps', [])),
            identity_check=deepcopy(manifest.get('identity_check', {})), content_depth=material.get('content_depth'),
            reading_policy=deepcopy(material.get('reading_policy', {})),
            relevance_status='not_assessed', semantic_truth_status='not_certified'))
    sidecar = archive.get('local_formula_materials')
    if sidecar is not None:
        if not isinstance(sidecar, dict) or sidecar.get('schema_version') != 'optomind.local_formula_materials.v1':
            raise ValueError('validated local_formula_materials sidecar required')
        region_ids = set()
        for region in sidecar.get('regions', []):
            rid = _text(region.get('region_id'))
            if not rid or rid in region_ids:
                raise ValueError('duplicate or missing formula region_id')
            region_ids.add(rid)
            ordinary = [item for item in items if item.get('source_material_type') != 'formula_enhancement'
                        and (rid in ((item['material_block'].get('locator') or {}).get('region_id'),
                                    (item['material_block'].get('locator') or {}).get('owner_region_id'))
                             or (_text(region.get('source_ref')) and region.get('source_ref') == (item['material_block'].get('locator') or {}).get('docling_self_ref')))]
            locator = deepcopy(ordinary[0]['material_block'].get('locator', {})) if ordinary else {}
            native_region = next((r for r in archive.get('region_index', {}).get('regions', [])
                                  if r.get('region_id') == rid), None)
            if native_region is not None:
                locator['native_region'] = deepcopy(native_region)
            locator.update(region_id=rid, source_ref=region.get('source_ref'))
            text = _text(region.get('latex'))
            formula_source = source_id or ('sha256:' + sidecar['binding']['pdf_sha256']
                if _text(sidecar.get('binding', {}).get('pdf_sha256')) else '')
            missing = ([] if formula_source else ['missing_source']) + ([] if text else ['missing_original_text'])
            if region.get('status') != 'recognized':
                missing.append('formula_not_recognized')
            block_id = 'formula_enhancement:' + rid
            if block_id in seen:
                raise ValueError('formula enhancement locator collides with native block')
            identity = [snapshot_id, block_id, sidecar.get('content_sha256'), text]
            eid = 'E_' + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
            mapping = dict(provider='review_local_formula_materials', snapshot_id=snapshot_id,
                block_id=block_id, original_block_ids=[item['block_id'] for item in ordinary],
                region_id=rid, locator=locator, source_ids=deepcopy(aliases),
                source_hashes=deepcopy(sidecar.get('binding', {})),
                content_sha256=sidecar.get('content_sha256'), text_field='regions.latex',
                query_ids=list(queries), goal_ids=list(goals), review_commit=archive.get('review_commit'))
            items.append(dict(evidence_id=eid, source_id=formula_source, source_url=url,
                title=paper.get('title') or '', authors=paper.get('authors') or [], year=paper.get('year'), doi=doi,
                text=text, snippet_type='body', source_material_type='formula_enhancement',
                snapshot_id=snapshot_id, block_id=block_id, query_ids=list(queries), goal_ids=list(goals),
                source_mappings=[mapping], eligible_as_evidence=not missing, missing_reasons=missing,
                material_block={'block_id': block_id, 'block_type': 'formula_enhancement',
                                'locator': locator, 'text_raw': text, 'status': region.get('status')},
                formula_provenance={k: deepcopy(v) for k, v in sidecar.items() if k != 'regions'},
                fidelity_status='local_transcription_candidate_not_scientific_verification',
                relevance_status='not_assessed', semantic_truth_status='not_certified'))
    return {'schema_version': SCHEMA, 'items': items, 'material_exports': [archive]}


def select_material_evidence(payload, selections, *, max_blocks=24, max_text_chars=48000, max_payload_chars=192000):
    """Return only explicitly named whole blocks, never the full source archive.

    ``selections`` is a list of {snapshot_id, block_id} objects. Unknown, ambiguous,
    or over-budget selections raise rather than silently cutting scientific text.
    Source exports remain in the archive passed by the caller, not in this view.
    """
    if any(type(v) is not int or v <= 0 for v in (max_blocks, max_text_chars, max_payload_chars)):
        raise ValueError('selection budgets must be positive integers')
    if not isinstance(payload, dict) or payload.get('schema_version') != SCHEMA or not isinstance(payload.get('items'), list):
        raise ValueError('focused evidence payload required')
    if not isinstance(selections, list):
        raise ValueError('explicit selections list required')
    index = {}
    for item in payload['items']:
        if 'snapshot_id' not in item or 'block_id' not in item:
            continue
        key = (item['snapshot_id'], item['block_id'])
        if key in index:
            raise ValueError('ambiguous snapshot/block locator')
        index[key] = item
    result, seen = [], set()
    for selection in selections:
        if not isinstance(selection, dict) or any(not _text(selection.get(k)) for k in ('snapshot_id', 'block_id')):
            raise ValueError('selection requires snapshot_id and block_id')
        key = (selection['snapshot_id'], selection['block_id'])
        if key not in index:
            raise ValueError('unknown snapshot/block locator')
        if key not in seen:
            seen.add(key)
            result.append(deepcopy(index[key]))
    if len(result) > max_blocks or sum(len(item['text']) for item in result) > max_text_chars:
        raise ValueError('selected whole blocks exceed evidence budget; select fewer blocks explicitly')
    view = {'schema_version': SCHEMA, 'items': result}
    if len(json.dumps(view, ensure_ascii=False)) > max_payload_chars:
        raise ValueError('selected structures exceed payload budget; select fewer blocks explicitly')
    return view


def merge_evidence_items(s2_payload, material_payloads):
    """Merge archives and exact evidence occurrences without losing provenance.

    Identical IDs with conflicting scientific content fail closed. Association
    lists and provenance occurrences are unioned; no relevance is inferred.
    """
    items, archives = {}, []
    for payload in [s2_payload, *material_payloads]:
        if not isinstance(payload, dict) or payload.get('schema_version') != SCHEMA or not isinstance(payload.get('items'), list):
            raise ValueError('focused evidence payload required')
        for original in payload['items']:
            item = deepcopy(original)
            eid = item.get('evidence_id')
            if not _text(eid):
                raise ValueError('evidence_id required')
            if eid not in items:
                items[eid] = item
                continue
            previous = items[eid]
            association_keys = ('query_ids', 'goal_ids', 'source_mappings')
            if ({k: v for k, v in previous.items() if k not in association_keys} !=
                    {k: v for k, v in item.items() if k not in association_keys}):
                raise ValueError('conflicting evidence content for ' + eid)
            for key in association_keys:
                previous.setdefault(key, [])
                for value in item.get(key, []):
                    if value not in previous[key]:
                        previous[key].append(value)
        for archive in payload.get('material_exports', []):
            if archive not in archives:
                archives.append(deepcopy(archive))
    return {'schema_version': SCHEMA, 'items': list(items.values()), 'material_exports': archives}


def material_block_index(payload):
    """Small navigation index; use locators to request original blocks explicitly.

    No generated snippets, relevance labels, or whole-document reading text are
    embedded. Section paths and types are native metadata, not summaries.
    """
    return [{'snapshot_id': item['snapshot_id'], 'block_id': item['block_id'],
             'source_id': item['source_id'], 'source_material_type': item['source_material_type'],
             'section_path': deepcopy(item['material_block'].get('section_path', [])),
             'locator': deepcopy(item['material_block'].get('locator')),
             'text_chars': len(item['text']), 'eligible_as_evidence': item['eligible_as_evidence'],
             'query_ids': list(item['query_ids']), 'goal_ids': list(item['goal_ids'])}
            for item in payload['items'] if 'material_block' in item]



def _prompt_locator(locator):
    """Keep exact block/region handles; omit parser audit and duplicate word geometry."""
    if not isinstance(locator, dict):
        return deepcopy(locator)
    audit = {'docling_prov', 'parser_config_sha256', 'parser_version',
             'projection_version', 'docling_primary', 'order_index'}
    return {k: deepcopy(v) for k, v in locator.items() if k not in audit}


def prompt_evidence_view(payload, *, max_text_chars=48000, max_payload_chars=192000):
    """Whole scientific content plus deduplicated source/snapshot reading context.

    The immutable archive remains the audit authority. This versioned projection
    omits local paths, parser-run documents and redundant word geometry, never
    scientific text, table cells, formulas, references or source usage limits.
    Table references are content-addressed so differing source restrictions are
    never silently merged merely because a bibliographic ID is shared.
    """
    if any(type(v) is not int or v <= 0 for v in (max_text_chars, max_payload_chars)):
        raise ValueError('prompt evidence budgets must be positive integers')
    if not isinstance(payload, dict) or payload.get('schema_version') != SCHEMA or not isinstance(payload.get('items'), list):
        raise ValueError('focused evidence payload required')
    sources, snapshots = {}, {}
    def intern(table, value, prefix):
        serial = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        key = prefix + hashlib.sha256(serial.encode()).hexdigest()[:24]
        if key in table and table[key] != value:
            raise ValueError('prompt context hash collision')
        table[key] = value
        return key
    source_fields = ('source_id', 'source_url', 'title', 'authors', 'year', 'doi')
    snapshot_fields = ('snapshot_id', 'content_depth', 'reading_policy', 'known_gaps', 'identity_check')
    fields = ('evidence_id', 'text', 'snippet_type', 'source_material_type', 'snapshot_id', 'block_id',
              'query_ids', 'goal_ids', 'eligible_as_evidence', 'missing_reasons', 'material_assets',
              'relevance_status', 'semantic_truth_status', 'formula_provenance', 'fidelity_status')
    items = []
    for original in payload['items']:
        if not isinstance(original, dict) or not isinstance(original.get('text'), str):
            raise ValueError('every prompt evidence item requires string text')
        item = {key: deepcopy(original[key]) for key in fields if key in original}
        source = {k: deepcopy(original[k]) for k in source_fields if k in original}
        item['source_ref'] = intern(sources, source, 'source_')
        if original.get('snapshot_id'):
            context = {k: deepcopy(original[k]) for k in snapshot_fields if k in original}
            policy = context.get('reading_policy', {})
            # Identical gaps need appear only once; nonidentical lists remain intact.
            if policy.get('known_gaps') == context.get('known_gaps'):
                policy.pop('known_gaps', None)
            documents = []
            for mapping in original.get('source_mappings', []):
                native_docs = [mapping.get('source_document')]
                hashes = mapping.get('source_hashes', [])
                if isinstance(hashes, list):
                    native_docs.extend(d for d in hashes if isinstance(d, dict) and d.get('role') in {'pdf', 'wire_bytes'})
                for doc in native_docs:
                    if not isinstance(doc, dict):
                        continue
                    doc = {k: deepcopy(doc[k]) for k in ('source_document_id', 'raw_sha256',
                           'role', 'format', 'source_uri', 'legal_use_status') if k in doc}
                    if doc not in documents:
                        documents.append(doc)
            gaps = context.get('known_gaps', [])
            grouped, ordinary = {}, []
            for gap in gaps:
                if isinstance(gap, dict) and gap.get('asset_id'):
                    base = {k: deepcopy(v) for k, v in gap.items() if k != 'asset_id'}
                    key = json.dumps(base, ensure_ascii=False, sort_keys=True)
                    grouped.setdefault(key, {'gap': base, 'asset_ids': []})['asset_ids'].append(gap['asset_id'])
                else:
                    ordinary.append(deepcopy(gap))
            if grouped:
                context['known_gaps'] = ordinary
                context['asset_gap_groups'] = list(grouped.values())
            context['source_documents'] = documents
            item['snapshot_ref'] = intern(snapshots, context, 'snapshot_')
        mappings = []
        for original_mapping in original.get('source_mappings', []):
            mapping = {k: deepcopy(v) for k, v in original_mapping.items()
                       if k not in ('snapshot_path', 'source_hashes', 'source_document', 'locator')}
            if isinstance(original_mapping.get('source_hashes'), dict):
                mapping['source_hashes'] = deepcopy(original_mapping['source_hashes'])
            # Native block locator below is authoritative. S2 has no material block.
            if 'material_block' not in original and 'locator' in original_mapping:
                mapping['locator'] = _prompt_locator(original_mapping['locator'])
            if mapping not in mappings:
                mappings.append(mapping)
        item['source_mappings'] = mappings
        if 'material_block' in original:
            item['material_block'] = {key: deepcopy(value)
                for key, value in original['material_block'].items()
                if key not in ('text_raw', 'text_normalized')}
            if 'locator' in item['material_block']:
                item['material_block']['locator'] = _prompt_locator(item['material_block']['locator'])
        items.append(item)
    view = {'schema_version': SCHEMA, 'prompt_view_version': 2,
            'sources': sources, 'snapshots': snapshots, 'items': items}
    if (sum(len(item['text']) for item in items) > max_text_chars or
            len(json.dumps(view, ensure_ascii=False, allow_nan=False)) > max_payload_chars):
        raise ValueError('prompt evidence exceeds budget; provide an explicit smaller block selection')
    return view

def main(argv=None):
    """Offline archive/selection CLI; never invokes Review, networks, or models."""
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--s2', type=Path, help='existing focused evidence_items.json')
    parser.add_argument('--export', type=Path, action='append', default=[], dest='exports',
                        help='verified Review bridge export; repeat for at most five papers')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--selections', type=Path, help='JSON list of snapshot_id/block_id objects')
    parser.add_argument('--query-id', action='append', default=[])
    parser.add_argument('--goal-id', action='append', default=[])
    parser.add_argument('--max-blocks', type=int, default=24)
    parser.add_argument('--max-text-chars', type=int, default=48000)
    parser.add_argument('--max-payload-chars', type=int, default=192000)
    args = parser.parse_args(argv)
    if len(args.exports) > 5:
        parser.error('this bounded validation stage accepts at most five paper exports')
    read = lambda path: json.loads(path.read_text(encoding='utf-8'))
    s2 = read(args.s2) if args.s2 else {'schema_version': SCHEMA, 'items': []}
    imported = [import_material_export(read(path), query_ids=args.query_id, goal_ids=args.goal_id)
                for path in args.exports]
    combined = merge_evidence_items(s2, imported)
    outputs = {'combined_evidence_items.json': combined,
               'material_block_index.json': material_block_index(combined)}
    if args.selections:
        selected = select_material_evidence(combined, read(args.selections),
            max_blocks=args.max_blocks, max_text_chars=args.max_text_chars,
            max_payload_chars=args.max_payload_chars)
        # Retain S2 items alongside the explicitly selected material blocks.
        selected = merge_evidence_items(s2, [selected])
        selected.pop('material_exports', None)
        if len(json.dumps(selected, ensure_ascii=False)) > args.max_payload_chars:
            raise ValueError('combined S2 and selected material exceed payload budget')
        outputs['selected_evidence_items.json'] = selected
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in outputs.items():
        (args.output_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
