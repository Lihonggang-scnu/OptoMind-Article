"""Read-only review-component handoff/cache audit; no model or plan mutation."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parent
CASE = ROOT / 'review/formal-01'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    current = read(CASE / 'CURRENT_PLAN.json')
    packet_root = Path(current['packet_root'])
    changed = read(ROOT / 'review/CHANGED_REQUEST.json')['edits'][0]
    packets = {}
    identities = set()
    for path in sorted((packet_root / 'writer_packets').glob('*.json')):
        data = read(path)
        materials = data['source_materials']
        contracts = []
        for item in materials:
            contract = item.get('usage_limits')
            assert contract and contract.get('allowed') and contract.get('prohibited') and contract.get('unknown_rule'), (path, item.get('source_handle'))
            identities.add((item['source_handle'], item['paper_id']))
            contracts.append({'source_handle': item['source_handle'], 'paper_id': item['paper_id'], 'usage_limits': contract})
        owner = read(CASE / 'stages/affected_chapter_revision' / path.name)
        owner_materials = owner['cache_inputs']['source_materials']
        assert {x['paper_id']: x.get('usage_limits') for x in owner_materials} == {x['paper_id']: x.get('usage_limits') for x in materials}
        if path.stem == changed['chapter_id']:
            assert changed in owner['cache_inputs']['editorial_feedback_for_chapter']
            assert changed in data['editorial_feedback_for_chapter']
        packets[path.stem] = {'sha256': sha(path), 'owner_cache_signature': owner['cache_signature'], 'contracts': contracts}
    entries = sorted((ROOT / 'entries').glob('review-*.json'), key=lambda p: p.stat().st_mtime)
    receipts = [read(p) for p in entries]
    changed_receipts = [x for x in receipts if '--editorial-feedback' in x['argv']]
    assert changed_receipts and changed_receipts[-1]['exit_code'] == 0
    first_changed = changed_receipts[0]
    actual_requests = []
    for request in (ROOT / 'calls').glob('review-*/REQUEST.json'):
        if request.stat().st_mtime >= first_changed['started_at']:
            text = request.read_text(encoding='utf-8')
            if 'allowed' in text and 'prohibited' in text and 'unknown_rule' in text:
                actual_requests.append(str(request))
    assert actual_requests, 'Usage contract absent from actual provider requests'
    budget = read(ROOT / 'BUDGET_STATUS.json')
    result = {'status': 'passed', 'planning_status': current['planning_status'],
              'revision': current['revision'], 'packets': packets, 'source_identities': sorted(identities),
              'changed_feedback': changed, 'usage_contract_provider_request_count': len(actual_requests),
              'actual_provider_request_files': actual_requests,
              'provider_call_count': len(list((ROOT / 'calls').glob('*/REQUEST.json'))),
              'cost_cny': budget['actual_cny'], 'reserved_cny': budget['reserved_cny'],
              'editorial_receipt_count': len(changed_receipts)}
    destination = ROOT / 'review' / ('CACHE_BEFORE_IDENTICAL_RESUME.json' if sys.argv[1:] == ['before'] else 'HANDOFF_AND_CACHE_ACCEPTANCE.json')
    if sys.argv[1:] == ['identical']:
        before = read(ROOT / 'review/CACHE_BEFORE_IDENTICAL_RESUME.json')
        result['identical_resume_added_calls'] = result['provider_call_count'] - before['provider_call_count']
        result['identical_resume_cost_cny'] = result['cost_cny'] - before['cost_cny']
        result['identical_resume_same_packets'] = result['packets'] == before['packets']
        assert result['identical_resume_added_calls'] == 0
        assert result['identical_resume_cost_cny'] == 0
        assert result['identical_resume_same_packets']
        assert result['editorial_receipt_count'] == before['editorial_receipt_count'] + 1
    if sys.argv[1:] == ['final']:
        replay = read(ROOT / 'review/terminal-replay/after/ACCEPTANCE.json')
        result['affected_terminal_checkpoint_replay'] = replay
        result['full_identical_cli_resume_observation'] = {
            'source': changed_receipts[-1]['source_heads']['OptoMInd-Review'],
            'exit_code': changed_receipts[-1]['exit_code'],
            'outcome': 'Replanned after journal-only status changed; retained as pre-fix evidence.',
            'post_terminal_fix_full_cli_rerun': False,
        }
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k not in ('packets', 'actual_provider_request_files')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
