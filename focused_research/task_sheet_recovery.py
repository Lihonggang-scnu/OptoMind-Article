"""Zero-model replay of T029's saved task sheets; no retrieval or physics calls."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
from . import task_sheet as ts, search_stage as st

SOURCE_COMMIT = '35c60b9f016eee6ef53a1b266b48e6366e1ec52d'
AR = 'e2e-09-si-photonics-ar-dualband'

def prepare_recovered_analysis(case_id, question, analysis):
    provenance = st.load(Path(__file__).parent/'campaigns/t029/INPUT_PROVENANCE.json')
    cases = {row['case_id']: row for row in provenance['cases']}
    if case_id not in (AR,'e2e-08-advanced-packaging-uv','e2e-02-space-qkd-cband'):
        raise ValueError('case is outside the recovered T029 scope')
    if hashlib.sha256(question.encode()).hexdigest() != cases[case_id]['question_utf8_sha256']:
        raise ValueError('original question differs from the pinned T029 question')
    sheet, audit = ts.prepare_analysis(question, analysis)
    audit = {"analysis_normalization": copy.deepcopy(audit)}
    report_moves = []
    # This exact, source-pinned request is already supported by TMM's saved T.
    # It is a report obligation, never a third optimization objective.
    if case_id == AR and isinstance(sheet.get('unsupported_requirements'),list):
        retained=[]
        for row in sheet['unsupported_requirements']:
            if (isinstance(row,dict) and set(row)=={'id','requirement','original_span','reason'}
                    and row['requirement']=='Report transmission into silicon substrate'
                    and row['original_span']=='报告进入硅基底的透射率'):
                report_moves.append(copy.deepcopy(row))
            else:
                retained.append(row)
        if report_moves:
            sheet['unsupported_requirements']=retained
            intents=sheet.get('application_intents')
            if not isinstance(intents,list):
                raise ValueError('application_intents must be a list')
            ident='ReportTIntoSilicon'
            if any(row.get('id')==ident for row in intents):
                raise ValueError('report intent identity collision')
            intents.append({'id':ident,'intent':'Report power transmission into the semi-infinite silicon substrate.',
                'original_span':'报告进入硅基底的透射率','subgoal_ids':[g['id'] for g in sheet['subgoals']],
                'operational_definition':'Report existing T spectra in both original bands. This is not a new optimization objective and is not through-device transmission.'})
            sheet['readiness']='needs_revision' if retained or not sheet['subgoals'] else 'ready'
    audit['report_only_capability_reclassification']={'source_rows':report_moves,
        'case_id':case_id,'physical_metrics_edited':False,
        'capability':'Existing certified R/T/A outputs include power entering the semi-infinite exit medium.'}
    ts.validate(sheet,question=question)
    return sheet,audit


def recover_planning(case_id, question, planning_file, *, expected_sha256, output, round_number=2):
    planning_file=Path(planning_file)
    raw=planning_file.read_bytes()
    actual=hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError('saved planning file hash mismatch')
    planning=json.loads(raw)
    rows=[r for r in planning['rounds'] if r['round']==round_number]
    if len(rows)!=1:
        raise ValueError('exactly one selected saved round is required')
    response=rows[0]['raw_response']
    analysis=json.loads(response) if isinstance(response,str) else copy.deepcopy(response)
    sheet,audit=prepare_recovered_analysis(case_id,question,analysis)
    if sheet['readiness']!='ready':
        raise ValueError('recovered sheet still has unsupported requirements')
    plan=ts.adapt(sheet)
    out=st._new_output(output,[planning_file.parent])
    st.save(out/'raw_task_sheet.json',analysis)
    st.save(out/'task_sheet.json',sheet)
    st.save(out/'query_plan.json',plan)
    st.save(out/'normalization_audit.json',audit)
    binding={'case_id':case_id,'source_commit':SOURCE_COMMIT,'source_planning_sha256':actual,
        'source_round':round_number,'source_response_sha256':st.digest(response),
        'task_sheet_sha256':st.digest(sheet),'original_question_sha256':hashlib.sha256(question.encode()).hexdigest(),
        'new_model_calls':0,'physics_executed':False,'status':'recovered_for_continuation_not_scientific_acceptance'}
    st.save(out/'recovery.json',binding)
    st._seal(out)
    return binding


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case-id',required=True)
    p.add_argument('--request',type=Path,required=True)
    p.add_argument('--planning',type=Path,required=True)
    p.add_argument('--expected-sha256',required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    result=recover_planning(args.case_id,st.load(args.request)['question'],args.planning,
        expected_sha256=args.expected_sha256,output=args.output)
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
