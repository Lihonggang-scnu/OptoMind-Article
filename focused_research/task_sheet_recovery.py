"""Source-pinned zero-model replay of saved task sheets; no retrieval or physics calls."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
from . import task_sheet as ts, search_stage as st

SOURCE_COMMIT = '35c60b9f016eee6ef53a1b266b48e6366e1ec52d'
AR = 'e2e-09-si-photonics-ar-dualband'
UV = 'e2e-04-solarblind-uv-triple'
UV_SOURCE_COMMIT = '028b4955f75fb6e421e0e727c7451d38431b7970'
UV_PLANNING_SHA256 = '04d04afb21e081d0493b95af3e5f25449f45a0bbad2aa558680baac60a6d26e2'
UV_QUESTION_SHA256 = 'a072e2f7732cc73738382bb04f916bcd0a1f4814c526cb9c022290cd6bd73d12'
UV_CONSTRAINTS = [
    ('UV passband: 255–280 nm', '255–280 nm'),
    ('Visible rejection band: 300–700 nm', '300–700 nm'),
    ('NIR rejection band: 700–1100 nm', '700–1100 nm'),
    ('Incident medium: air', '空气入射'),
    ('Substrate: fused silica', '熔融石英基底'),
    ('No metal layers allowed', '不含金属层'),
    ('Only conventional depositable inorganic dielectric materials', '仅使用常规可沉积无机介质材料'),
    ('Planar multilayer film', '平面多层膜'),
    ('Total layer count not exceeding 30', '膜系总层数不超过 30 层'),
]


def _validate_uv_binding(question, source_commit, source_planning_sha256):
    if source_commit != UV_SOURCE_COMMIT:
        raise ValueError('case04 requires pinned T037 source commit')
    if source_planning_sha256 != UV_PLANNING_SHA256:
        raise ValueError('case04 requires pinned T037 planning hash')
    if hashlib.sha256(question.encode()).hexdigest() != UV_QUESTION_SHA256:
        raise ValueError('original question differs from the pinned T037 question')


def _validate_uv_execution(sheet):
    expected_metrics = [
        {'objective_id': ident, 'metric': 'mean_transmittance', 'sense': sense,
         'mean_reduction': 'wavelength_integral_mean',
         'region': {'wavelength_nm': band, 'angle_deg': 0, 'polarization': 'unpolarized'}}
        for ident, band, sense in [('G1', [255, 280], 'maximize'),
                                  ('G2', [300, 700], 'minimize'),
                                  ('G3', [700, 1100], 'minimize')]]
    if [g.get('executable_metric') for g in sheet.get('subgoals', [])] != expected_metrics:
        raise ValueError('case04 executable metrics differ from pinned three-band contract')
    expected_constraints = [{'id': f'C{i}', 'constraint': constraint, 'original_span': span}
                            for i, (constraint, span) in enumerate(UV_CONSTRAINTS, 1)]
    if sheet.get('explicit_constraints') != expected_constraints:
        raise ValueError('case04 constraints differ from pinned broad-material contract')


def prepare_recovered_analysis(case_id, question, analysis, *, source_commit=None,
                               source_planning_sha256=None):
    if case_id == UV:
        _validate_uv_binding(question, source_commit, source_planning_sha256)
        _validate_uv_execution(analysis)
        sheet, normalization = ts.prepare_analysis(question, analysis)
        _validate_uv_execution(sheet)
        ts.validate(sheet, question=question)
        return sheet, {'analysis_normalization': copy.deepcopy(normalization),
                       'source_binding': {'campaign': 'T037', 'source_commit': source_commit,
                                          'source_planning_sha256': source_planning_sha256},
                       'physical_metrics_edited': False,
                       'constraints_edited': False, 'queries_edited': False,
                       'material_permission': 'Conventional depositable inorganic dielectric materials; no named-material allowlist.'}
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


def recover_planning(case_id, question, planning_file, *, expected_sha256, output, round_number=2, source_commit=None):
    planning_file=Path(planning_file)
    raw=planning_file.read_bytes()
    actual=hashlib.sha256(raw).hexdigest()
    if actual != expected_sha256:
        raise ValueError('saved planning file hash mismatch')
    selected_commit = source_commit or (UV_SOURCE_COMMIT if case_id == UV else SOURCE_COMMIT)
    if case_id == UV:
        _validate_uv_binding(question, selected_commit, actual)
    elif selected_commit != SOURCE_COMMIT:
        raise ValueError('source commit differs from pinned T029 recovery source')
    planning=json.loads(raw)
    rows=[r for r in planning['rounds'] if r['round']==round_number]
    if len(rows)!=1:
        raise ValueError('exactly one selected saved round is required')
    response=rows[0]['raw_response']
    analysis=json.loads(response) if isinstance(response,str) else copy.deepcopy(response)
    sheet,audit=prepare_recovered_analysis(case_id,question,analysis,
        source_commit=selected_commit,source_planning_sha256=actual)
    if sheet['readiness']!='ready':
        raise ValueError('recovered sheet still has unsupported requirements')
    plan=ts.adapt(sheet)
    out=st._new_output(output,[planning_file.parent])
    st.save(out/'raw_response.json',response)
    st.save(out/'raw_task_sheet.json',analysis)
    st.save(out/'task_sheet.json',sheet)
    st.save(out/'query_plan.json',plan)
    st.save(out/'normalization_audit.json',audit)
    binding={'case_id':case_id,'source_commit':selected_commit,'source_planning_sha256':actual,
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
    p.add_argument('--source-commit')
    p.add_argument('--round-number',type=int,default=2)
    args=p.parse_args(argv)
    result=recover_planning(args.case_id,st.load(args.request)['question'],args.planning,
        expected_sha256=args.expected_sha256,output=args.output,
        source_commit=args.source_commit,round_number=args.round_number)
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
