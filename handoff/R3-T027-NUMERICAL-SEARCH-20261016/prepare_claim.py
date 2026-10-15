"""Read-only fee reconciliation and immutable T027 claim preparation."""
import datetime, hashlib, json, sqlite3
from decimal import Decimal
from pathlib import Path
ROOT = Path(__file__).resolve().parent
def load(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, value):
    with p.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2); stream.write('\n')
ready = load(ROOT / 'delivery/READY.json')
assert (ready['task_id'], ready['message_id'], ready['status']) == ('R3-T027', 'C2L-R3-0040', 'ready')
assert ready['repositories'][0]['implementation_sha'] == 'b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b'
publication = load(ROOT / 'delivery/CLOUD_PUBLISHED.json')
assert publication['publication_only'] is True
assert sha(ROOT / 'delivery/READY.json') == publication['ready_sha256']
hashes = {}
for artifact in ready['artifacts']:
    path = ROOT / 'delivery' / artifact['name']
    if path.exists():
        hashes[path.name] = sha(path)
        assert hashes[path.name] == artifact['sha256']
save(ROOT / 'DELIVERY_HASHES_BEFORE_BUNDLE.json', hashes)
state = load(ROOT.parent.parent / 'state.json')
assert state['active_task'] is None and 'R3-T027::C2L-R3-0040' not in state['processed']
old = Path(r'D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite')
d20 = ROOT.parent / 'R3-T019-DEDICATED20/private/budget.sqlite'
ledgers = []
for path, expected in ((old, 140), (d20, 20)):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT status,amount_cny,actual_cny FROM reservations').fetchall()
        cap = Decimal(dict(db.execute('SELECT key,value FROM budget_meta'))['limit_cny'])
    assert cap == expected
    settled = sum((Decimal(str(r[2] or 0)) for r in rows if r[0] == 'settled'), Decimal(0))
    held = sum((Decimal(str(r[1] or 0)) for r in rows if r[0] != 'settled'), Decimal(0))
    ledgers.append({'path': str(path), 'cap_cny': str(cap), 'settled_cny': str(settled),
                    'held_cny': str(held), 'unresolved_count': sum(r[0] != 'settled' for r in rows), 'sha256': sha(path)})
with sqlite3.connect(d20.as_uri() + '?mode=ro', uri=True) as db:
    rows = db.execute('SELECT r.status,r.amount_cny,r.actual_cny FROM reservations r JOIN r3_task_calls c USING(reservation_id) WHERE c.task_id IN (?,?,?,?)',
                      ('R3-T022', 'R3-T025', 'R3-T026', 'R3-T027')).fetchall()
chain = sum((Decimal(str(r[2] if r[0] == 'settled' else r[1])) for r in rows), Decimal(0))
total = sum((Decimal(r['settled_cny']) + Decimal(r['held_cny']) for r in ledgers), Decimal(0))
assert chain <= 15 and total <= 140
budget = {'ledgers': ledgers, 'chain_exposure_cny': str(chain), 'aggregate_exposure_cny': str(total),
          'unresolved_count': sum(x['unresolved_count'] for x in ledgers), 'global_remaining_cny': str(Decimal(140) - total),
          'new_budget_pool': False, 'new_model_calls': 0}
save(ROOT / 'BUDGET_INITIAL.json', budget)
now = datetime.datetime.now(datetime.timezone.utc); stamp = now.strftime('%Y%m%dT%H%M%SZ')
message = 'RCP-LOCAL-T027-CLAIMED-' + stamp
claim = {'schema_version': 1, 'message_id': message, 'task_id': 'R3-T027', 'direction': 'local_to_cloud',
    'kind': 'result', 'status': 'claimed', 'owner': 'local', 'created_at': now.isoformat(), 'in_reply_to': 'C2L-R3-0040',
    'instruction': 'Claimed after START_HERE and receipt/state dedup. First targeted local Torch tests, then bounded real family search, at most one original-metered model decision, and bounded inherited refinement. Preserve all old data and stop after publication.',
    'repositories': ready['repositories'], 'budget': ready['budget'], 'budget_reconciliation': budget,
    'expected_outputs': ready['expected_outputs'], 'dedup': {'active_task': None, 'matching_local_record': None,
       'matching_remote_receipts': 'cloud implementation claim and publication only'},
    'workdir': r'D:\OptoMind-Article-3\r3t027a', 'paid_gate_passed': False}
path = ROOT / 'receipts' / (stamp + '_' + message + '.json')
save(path, claim)
save(ROOT / 'CLAIM_INDEX.json', {'path': str(path), 'name': path.name, 'message_id': message})
print(json.dumps({'claim_path': str(path), 'claim_name': path.name, 'aggregate_exposure_cny': str(total),
                  'chain_exposure_cny': str(chain), 'unresolved_count': budget['unresolved_count']}))
