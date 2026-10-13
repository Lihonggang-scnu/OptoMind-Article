import json
import pytest
from optomind_optics.harness.science.contracts import canonical_hash
from optomind_optics.harness.science.lifetime_evaluator import LifetimeEvaluator,LifetimeProtocol,LifetimeTask,ForwardReceipt


def protocol():
    return LifetimeProtocol(protocol_id='large-closure',cost_unit='fixture_units',
        tasks=tuple(LifetimeTask(task_id=f't{i}',family='optical',provenance_group=f'g{i}',inputs=(float(i),),target=1.) for i in range(36)))


def test_large_completed_journal_replays_byte_identically_after_checkpoint_and_reopen(tmp_path):
    p=protocol();calls=[]
    def forward(view):
        calls.append(view.task.task_id)
        return ForwardReceipt(prediction=1,costs=dict(evaluation=1,learning=0,maintenance=0,revalidation=0),
                              external_receipt='measured:'+('x'*16000)+view.task.task_id)
    runner=LifetimeEvaluator(tmp_path,p,evaluator_id='frozen')
    runner.db.execute('PRAGMA wal_autocheckpoint=0')
    original=runner.run(forward)
    assert len(calls)==144
    assert original.storage_basis=='completion_closure'
    assert all(type(row['retrieval_seconds']) is float for row in original.records)
    original_json=original.model_dump_json()
    runner.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    assert runner._storage_bytes()!=original.storage
    runner.close()
    (tmp_path/'later-export.txt').write_text('separate export cannot alter frozen report')
    reopened=LifetimeEvaluator(tmp_path,p,evaluator_id='frozen')
    assert reopened.replay().model_dump_json()==original_json
    assert reopened.run(forward).model_dump_json()==original_json
    assert len(calls)==144
    reopened.close()


def test_legacy_completed_copy_is_explicit_posthoc_and_preserves_operation_rows(tmp_path):
    p=protocol();runner=LifetimeEvaluator(tmp_path,p,evaluator_id='frozen')
    runner.run(lambda view:ForwardReceipt(prediction=1,costs=dict(evaluation=1,learning=0,maintenance=0,revalidation=0),external_receipt='local'))
    before=list(runner.db.execute('SELECT * FROM operations ORDER BY key'))
    with runner.db:runner.db.execute("DELETE FROM metadata WHERE key='report_closure'")
    runner.close()
    reopened=LifetimeEvaluator(tmp_path,p,evaluator_id='frozen')
    report=reopened.replay()
    assert report.storage_basis=='legacy_posthoc_capture'
    assert list(reopened.db.execute('SELECT * FROM operations ORDER BY key'))==before
    assert report.model_dump_json()==reopened.replay().model_dump_json()
    reopened.close()


def test_closure_tampering_fails_closed(tmp_path):
    runner=LifetimeEvaluator(tmp_path,protocol(),evaluator_id='frozen')
    runner.run(lambda view:ForwardReceipt(prediction=1,costs=dict(evaluation=1,learning=0,maintenance=0,revalidation=0),external_receipt='local'))
    row=json.loads(runner.db.execute("SELECT value FROM metadata WHERE key='report_closure'").fetchone()[0]);row['storage_bytes']+=1
    with runner.db:runner.db.execute("UPDATE metadata SET value=? WHERE key='report_closure'",(json.dumps(row),))
    with pytest.raises(ValueError,match='closure integrity mismatch'):runner.replay()
    runner.close()


def test_old_report_json_remains_readable_without_claiming_a_seal(tmp_path):
    from optomind_optics.harness.science.lifetime_evaluator import LifetimeReport
    runner=LifetimeEvaluator(tmp_path,protocol(),evaluator_id='frozen')
    report=runner.run(lambda view:ForwardReceipt(prediction=1,costs=dict(evaluation=1,learning=0,maintenance=0,revalidation=0),external_receipt='local'))
    old=report.model_dump(mode='json');old.pop('storage_basis')
    assert LifetimeReport.model_validate(old).storage_basis=='historical_unsealed'
    runner.close()
