"""Frozen, full-production-workflow calibration and held-out audit.

Every replicate executes OpenResearchLoop.run, including fitting, adequacy,
proposals, acquisitions, selection, stopping and final validation. The calibrated
statistic is the maximum local -log(p) encountered along that whole trajectory.
It does not retroactively certify or change the original local expansion policy.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import math
from scipy.stats import beta
from .contracts import canonical_hash


def binomial_interval(k,n,alpha=.05):
    if not n:return [None,None]
    return [0. if k==0 else float(beta.ppf(alpha/2,k,n-k+1)),
            1. if k==n else float(beta.ppf(1-alpha/2,k+1,n-k))]


def runtime_source_fingerprints():
    """Fingerprint the full local production dependency closure, not just science."""
    code=Path(__file__).resolve().parents[3]
    article=code.parent
    roots=[code/name for name in ('optomind_optics','optomind_eval','config','llm','tools')]
    roots += [article/'veritmm',article.parent/'OptoMInd-Review'/'optomind_research']
    excluded={'__pycache__','.pytest_cache','.git','api_keys','outputs'}
    fingerprints={}
    for root in roots:
        if not root.exists():continue
        for file in sorted(root.rglob('*')):
            relative=file.relative_to(root)
            if not file.is_file() or any(part in excluded or part.startswith('.') for part in relative.parts) or file.suffix=='.pyc':continue
            fingerprints[str(file.relative_to(article.parent))]=hashlib.sha256(file.read_bytes()).hexdigest()
    return fingerprints


def full_adaptive_audit(output, *, base_payload, calibration_seeds, null_audit_seeds,
                        alternatives=(), alpha=.05, loop_factory=None, statistic="max_negative_log_local_p", failure_rule="invalidate"):
    """alternatives is tuple of (family,seed), disjoint from every null seed.

    Default factory is the actual offline production adapter. An injected factory
    must return the same loop type; this is useful for alternative independent
    evaluators. Failed tasks are retained as unknown rather than dropped.
    """
    from .open_research_cli import build_offline_loop
    from .open_research_loop import OpenResearchLoop
    if loop_factory is None:loop_factory=build_offline_loop
    calibration_seeds=tuple(calibration_seeds);null_audit_seeds=tuple(null_audit_seeds);alternatives=tuple(alternatives)
    all_seeds=calibration_seeds+null_audit_seeds+tuple(seed for _,seed in alternatives)
    if len(set(all_seeds))!=len(all_seeds) or not calibration_seeds or not null_audit_seeds or not 0<alpha<1:
        raise ValueError("distinct frozen calibration/audit/alternative seeds and alpha required")
    if statistic not in {"max_negative_log_local_p","max_whitened_squared_discrepancy"}:raise ValueError("unsupported frozen whole-workflow statistic")
    if failure_rule not in {"invalidate","policy_control_stop_as_outcome"}:raise ValueError("unsupported frozen failure rule")
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    if (output/'protocol.json').exists():raise ValueError("calibration protocol already exists; do not overwrite outcomes")
    sources=runtime_source_fingerprints()
    protocol={'version':'whole-adaptive-audit/1','base_payload':base_payload,'calibration_seeds':calibration_seeds,
        'null_audit_seeds':null_audit_seeds,'alternatives':alternatives,'alpha':alpha,'sources':sources,
        'null_family':'F0','statistic':statistic,
        'failure_rule':failure_rule,
        'scope':'same frozen configured synthetic workflow, seed sampling law and optical null family; no global open-world guarantee'}
    protocol['protocol_hash']=canonical_hash(protocol)
    (output/'protocol.json').write_text(json.dumps(protocol,indent=2))
    rows=[]
    cases=[('calibration','F0',s) for s in calibration_seeds]+[('null_audit','F0',s) for s in null_audit_seeds]+[('alternative',f,s) for f,s in alternatives]
    for role,family,seed in cases:
        current=runtime_source_fingerprints()
        if current!=sources:raise RuntimeError("production source changed after frozen calibration protocol")
        path=output/f'{role}-{family}-{seed}';resources=();loop=None
        row={'role':role,'family':family,'seed':seed,'status':'unknown','path':str(path)}
        try:
            payload={**base_payload,'family':family,'world_seed':seed,'config':{**base_payload.get('config',{}),'seed':seed+1000000,'run_id':f'whole-{role}-{family}-{seed}'}}
            loop,resources=loop_factory(path,payload)
            if not isinstance(loop,OpenResearchLoop):raise TypeError("full audit requires the actual production OpenResearchLoop")
            result=loop.run(path)
            trace=json.loads((path/'SCIENCE_OPEN_TRAJECTORY.json').read_text())
            iterations=trace['iterations'];ps=[r['adequacy'].get('p_value') for r in iterations]
            finite=[float(p) for p in ps if p is not None]
            score=max((-math.log(max(p,1e-300)) for p in finite),default=0.) if statistic=="max_negative_log_local_p" else max((float(r["adequacy"]["training_discrepancy"]) for r in iterations),default=0.)
            expansion=any(r['action']=='bounded_structure_search' and r.get('model_expansion_enabled',True) for r in iterations)
            first=next((i for i,r in enumerate(iterations) if r['adequacy']['state']=='model_family_inadequate'),None)
            row.update(status='completed',score=score,score_key=[0,score],local_p_values=ps,actual_structure_search=expansion,
                detection_iteration=first,selected_ref=result.ref().model_dump(mode='json'),
                trajectory_sha256=hashlib.sha256((path/'SCIENCE_OPEN_TRAJECTORY.json').read_bytes()).hexdigest())
        except Exception as exc:
            row.update(error=f'{type(exc).__name__}: {exc}')
            if failure_rule=="policy_control_stop_as_outcome" and isinstance(exc,ValueError) and str(exc)=="reference control failed; noise calibration required" and loop is not None:
                objects=[loop.repository.load(ref) for ref in loop.repository.latest_snapshot().object_refs]
                controls=[o for o in objects if hasattr(o,"summary") and o.summary.get("state")=="reference_prediction_failed"]
                if controls:
                    decisions=[o.summary for o in objects if hasattr(o,"summary") and "iteration" in o.summary and "adequacy" in o.summary and "action" in o.summary]
                    row.update(status="policy_stopped",score_key=[1,0.],control_failure_refs=[o.ref().model_dump(mode="json") for o in controls],
                        actual_structure_search=any(r["action"]=="bounded_structure_search" and r.get("model_expansion_enabled",True) for r in decisions),
                        detection_iteration=next((r["iteration"] for r in decisions if r["adequacy"]["state"]=="model_family_inadequate"),None),
                        interpretation="observed conservative control stop; omnibus workflow risk outcome, not structural refutation")
        finally:
            if loop is not None:row['compute']=loop.compute_ledger.snapshot()
            for resource in resources:resource.close()
            if runtime_source_fingerprints()!=sources:raise RuntimeError('production source changed during calibration case')
        rows.append(row)
        (output/'progress.json').write_text(json.dumps(rows,indent=2))
    calibration=[r for r in rows if r['role']=='calibration']
    evaluable={'completed','policy_stopped'} if failure_rule=='policy_control_stop_as_outcome' else {'completed'}
    valid=all(r['status'] in evaluable for r in calibration)
    scores=[tuple(r['score_key']) for r in calibration if r['status'] in evaluable]
    for row in rows:
        if row['role']!='calibration' and row['status'] in evaluable and valid:
            p=(1+sum(s>=tuple(row['score_key']) for s in scores))/(1+len(scores))
            row.update(whole_workflow_p_value=p,whole_workflow_rejected=p<=alpha)
    summaries={}
    for role in ('null_audit','alternative'):
        group=[r for r in rows if r['role']==role];completed=[r for r in group if r['status'] in evaluable]
        rejected=sum(r.get('whole_workflow_rejected',False) for r in completed)
        expansion=sum(r.get('actual_structure_search',False) for r in completed)
        summaries[role]={'n_planned':len(group),'n_completed':sum(r['status']=='completed' for r in group),'n_policy_stopped':sum(r['status']=='policy_stopped' for r in group),'n_evaluable':len(completed),'n_unknown':len(group)-len(completed),
            'calibrated_rejections':rejected if valid else None,'rejection_rate_interval':binomial_interval(rejected,len(completed)) if valid else [None,None],
            'actual_structure_searches':expansion,'structure_search_rate_interval':binomial_interval(expansion,len(completed)),
            'detection_delays':[r['detection_iteration'] for r in completed]}
    report={'protocol_hash':protocol['protocol_hash'],'calibration_valid':valid,'minimum_attainable_p':1/(len(scores)+1),
        'threshold_resolution_sufficient':len(scores)>=math.ceil(1/alpha)-1,'rows':rows,'summaries':summaries,
        'limits':['Policy-control stops, when enabled in the frozen protocol, are ranked as maximal omnibus workflow-risk outcomes; they never refute a physical model.',
                  'Calibrates whole-workflow statistic under declared F0 simulator, not adaptive scientific discovery correctness.',
                  'Actual local-policy structure searches are reported separately and are not retroactively controlled by the post-run calibrated p-value.',
                  'Finite held-out binomial intervals may be wide; synthetic seed trials do not establish physical generalization.']}
    if runtime_source_fingerprints()!=sources:raise RuntimeError('production source changed before calibration report')
    (output/'report.json').write_text(json.dumps(report,indent=2))
    return report
