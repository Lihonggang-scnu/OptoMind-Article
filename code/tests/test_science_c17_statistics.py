import json
import pytest
from optomind_eval.protocols import EvaluationProtocol, TaskResult, seal_results
from optomind_eval.report_core import report_frozen_results


def test_frozen_paired_report_retains_failure_and_cost(tmp_path):
    p=EvaluationProtocol('test', ('t1','t2','t3'), ('a','b'), (('a','b'),), 10, ('R',), 20)
    rows=tuple(TaskResult(p.seal,task,arm, 1 if arm=='a' else 3, .1, 1, 'failed' if task=='t3' and arm=='a' else 'completed',('R',),'a'*64)
               for task in p.task_ids for arm in p.arms)
    target=tmp_path/'results.json'
    seal_results(p,rows,target)
    report=report_frozen_results(target)
    assert report['failures']['a']==1
    assert report['comparisons']['a - b']['mean']==pytest.approx(13/3)
    assert report['resource_costs']['a']==3
    payload=json.loads(target.read_text()); payload['rows'][0]['terminal_loss']=0; target.write_text(json.dumps(payload))
    with pytest.raises(ValueError,match='modified'):
        report_frozen_results(target)


def test_missing_failure_or_permission_asymmetry_rejected(tmp_path):
    p=EvaluationProtocol('test', ('t1','t2'), ('a',), (), 10, ('R',), 20)
    rows=(TaskResult(p.seal,'t1','a',1,0,1,'completed',('R',),'a'*64),)
    with pytest.raises(ValueError,match='all tasks'):
        seal_results(p,rows,tmp_path/'x')
    rows+= (TaskResult(p.seal,'t2','a',1,0,1,'completed',('R','T'),'a'*64),)
    with pytest.raises(ValueError,match='parity'):
        seal_results(p,rows,tmp_path/'x')
