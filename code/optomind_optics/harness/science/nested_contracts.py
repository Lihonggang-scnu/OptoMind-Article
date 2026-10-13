"""Versioned consumed nested contracts, preserving existing serialized payloads.

Validation does not add defaults or normalize historical dictionaries: hashes
stay stable. Opaque legacy projections and diagnostic/cache summaries are never
accepted as executable model/program/likelihood contracts.
"""
from __future__ import annotations
from dataclasses import fields
import math
from collections.abc import Mapping

VERSION = 'science-nested-contracts.v1'

FIELDS = {
 'research-task.v1': {
  'objective': {'kind','targets','robustness','wavelength_nm','target_R','loss','design_pool_nm','loss_transform','risk_aversion'},
  'constraints': {'open_research_config','initial_model_hash','evidence_tier','paid_network_calls','source_task','robustness','scope','max_total_nm','thickness_max_nm','thickness_min_nm','angle_deg','wavelength_nm','not_recorded'}},
 'model-candidate.v1': {'domain': {'wavelength_nm','provenance','support','task','selection','interpretation','not_recorded'}},
 'observation.v2': {
  'binding': {'candidate','sample','objective','objective_version','grid','polarization','materials','tool_version','measurement_receipt_id','task_id','evaluator_version','program_ref','dataset_hash','split','conditions','protocol_hash','preexecution_receipt_hash','manual_return','source_version','not_recorded'},
  'quality': {'audit_binding','independent_quality_audit','execution','physics','objective','explanation','execution_status','numerical_valid','noise_validated','conditions','cross_block_noise_model','evidence_tier','evidence_level','independent_measurement_purchase','new_physical_measurement','physical_range_violations','acquisition_cost','menu_access_cost','physical_provenance','quality_audit','controls_audit','V4_accepted','V5_accepted','validation_scope','not_recorded'}},
 'inference-state.v1': {
  'calibration_scope': {'description','not_recorded'},
  'noise_model': {'type','shared_calibration','derived_views','not_recorded'},
  'posterior': {'parameter_order','parameters','samples','weights','covariance','not_recorded'}},
 'prediction-spec.v1': {
  'grid': {'angle_deg','angles_deg','polarization','polarizations','coordinate_units','wavelength_units','support','session','program','wavelengths_nm','coordinate','not_recorded'},
  'distribution': {'world_ids','kernel','mean','candidate_id','parameter_uncertainty','not_recorded'}},
}
RAW_FIELDS = {'polarization','block_id','channel','wavelengths_nm','coordinate','coordinate_units','units','values','covariance','calibration_group','calibration_std','gaussian_validated','angle_deg','derived_from','sample_id','source_file','source_hash','artifact_ref'}
BOOL_QUALITY = {'numerical_valid','noise_validated','independent_measurement_purchase','new_physical_measurement','V4_accepted','V5_accepted'}


def _keys(value, allowed, label):
    if not isinstance(value, Mapping): raise ValueError(label + ' must be an object')
    unknown = set(value) - allowed
    if unknown: raise ValueError(label + ' unknown consumed fields: ' + ','.join(sorted(unknown)))


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite numeric')


def validate_raw_block(raw):
    _keys(raw, RAW_FIELDS, 'raw_block')
    if not isinstance(raw.get('block_id'),str) or not raw['block_id']:
        raise ValueError('raw_block requires block_id')
    for key in ('values','coordinate','wavelengths_nm'):
        if key in raw:
            if not isinstance(raw[key],(list,tuple)): raise ValueError(key+' must be an array')
            for value in raw[key]: _finite(value,key)
    for key in ('channel','coordinate_units','units'):
        if key in raw and (not isinstance(raw[key],str) or not raw[key]):raise ValueError(key+' must be nonempty string')
    if 'polarization' in raw and raw['polarization'] not in ('s','p','unpolarized','TE','TM','te','tm'):
        raise ValueError('unknown raw polarization')
    if 'gaussian_validated' in raw and type(raw['gaussian_validated']) is not bool:
        raise ValueError('gaussian_validated must be boolean')
    for key in ('angle_deg','calibration_std'):
        if key in raw: _finite(raw[key],key)
    if raw.get('calibration_std',0)<0: raise ValueError('negative calibration_std')
    if 'values' in raw:
        n=len(raw['values'])
        for key in ('coordinate','wavelengths_nm'):
            if key in raw and len(raw[key])!=n: raise ValueError('raw coordinate/value length mismatch')
        if 'coordinate' in raw and 'wavelengths_nm' in raw and raw.get('coordinate_units','nm')=='nm' and list(raw['coordinate'])!=list(raw['wavelengths_nm']):
            raise ValueError('raw coordinate aliases disagree')
        if 'covariance' in raw:
            c=raw['covariance']
            if len(c)!=n or any(len(row)!=n for row in c):raise ValueError('raw covariance shape mismatch')
            for row in c:
                for value in row:_finite(value,'covariance')
    return raw


def validate_model_payload(payload, parameters):
    if not payload: return  # Explicit non-executable empty legacy placeholder.
    if payload.get('schema_version') == 'legacy-candidate.v1':
        _keys(payload, {'schema_version','candidate_id','layer_materials','layer_labels','incident_medium','exit_medium'},'legacy_candidate')
        return
    if 'simulation' in payload:
        _keys(payload, {'simulation'}, 'legacy_simulation_binding')
        if not isinstance(payload['simulation'],Mapping):raise ValueError('simulation must be an object')
        # Native simulation is validated by the existing VeriTMM parser at its
        # actual consumer; it is not falsely reinterpreted as optical-ir/1.
        return
    if 'kind' in payload:
        _keys(payload, {'kind','name'}, 'finite_model_identity')
        if payload['kind'] not in {'fixture','finite_fixture'}: raise ValueError('unknown nonexecuting model identity kind')
        return
    from .model_ir import model_ir_from_dict
    ir=model_ir_from_dict(payload)
    names={p.name for p in ir.parameters}
    if set(parameters)-names:raise ValueError('candidate parameter identity not declared by ModelIR')
    for key,value in parameters.items():_finite(value,'parameter '+key)


def validate_program_payload(payload):
    if not payload: return
    if payload.get('schema_version')=='legacy-tmm-experiment.v1':
        _keys(payload,{'schema_version','experiment_id','mode','tmm_task','source_task_hash'},'legacy_tmm_experiment')
        if not isinstance(payload.get('tmm_task'),Mapping):raise ValueError('legacy tmm_task must be recorded')
        return
    if set(payload)<= {'angle'}:  # Historical non-executable C04 fragment.
        _finite(payload['angle'],'legacy angle fragment');return
    from .program_ir import ProgramIR,ProgramNode
    _keys(payload,{field.name for field in fields(ProgramIR)},'ProgramIR')
    for row in payload.get('nodes',()):
        _keys(row,{field.name for field in fields(ProgramNode)},'ProgramNode')
        ProgramNode(**row)
    if 'program_id' not in payload or not isinstance(payload['program_id'],str):raise ValueError('program_id required')


def validate_nested_payload(schema, payload):
    for field,allowed in FIELDS.get(schema,{}).items():
        if field in payload:_keys(payload[field],allowed,schema+'.'+field)
    if schema=='research-task.v1':
        objective=payload.get('objective') or {}
        for key in ('wavelength_nm','target_R','risk_aversion'):
            if key in objective:_finite(objective[key],key)
        if 'design_pool_nm' in objective:
            for value in objective['design_pool_nm']:_finite(value,'design_pool_nm')
        config=(payload.get('constraints') or {}).get('open_research_config')
        if config is not None:
            # Import only when this registered consumer variant is supplied.
            from .open_research_loop import OpenResearchConfig
            _keys(config,{f.name for f in fields(OpenResearchConfig)},'OpenResearchConfig')
            try: OpenResearchConfig(**config)
            except (TypeError, KeyError) as exc: raise ValueError('invalid consumed OpenResearchConfig') from exc
    elif schema=='model-candidate.v1':validate_model_payload(payload.get('model_ir') or {},payload.get('parameter_values') or {})
    elif schema=='experiment-program.v1':validate_program_payload(payload.get('protocol') or {})
    elif schema=='observation.v2':
        for block in payload.get('raw_blocks') or ():validate_raw_block(block)
        for key,value in (payload.get('quality') or {}).items():
            if key in BOOL_QUALITY and type(value) is not bool:raise ValueError('quality '+key+' must be boolean')
        quality=payload.get('quality') or {}
        if 'audit_binding' in quality:
            value=quality['audit_binding']
            if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('invalid audit binding hash')
        if 'independent_quality_audit' in quality:
            from ..tools.manual_audit import IndependentQualityAudit
            IndependentQualityAudit.model_validate_json(__import__('json').dumps(quality['independent_quality_audit']),strict=True)
    elif schema=='prediction-spec.v1':
        grid=payload.get('grid') or {}
        if 'angle_deg' in grid:_finite(grid['angle_deg'],'grid angle_deg')
        for angle in grid.get('angles_deg',()):_finite(angle,'grid angles_deg')
        for key in ('polarization','coordinate_units','wavelength_units'):
            if key in grid and not isinstance(grid[key],str):raise ValueError(key+' must be string')
        if 'polarizations' in grid and (not isinstance(grid['polarizations'],(list,tuple)) or any(not isinstance(p,str) for p in grid['polarizations'])):raise ValueError('polarizations must be strings')
    elif schema=='inference-state.v1':
        posterior=payload.get('posterior') or {}
        for key in ('parameters',):
            for name,value in (posterior.get(key) or {}).items():_finite(value,'posterior parameter '+name)
        for sample in posterior.get('samples') or ():
            for name,value in sample.items():_finite(value,'posterior sample '+name)
        weights=posterior.get('weights')
        if weights is not None:
            for value in weights:_finite(value,'posterior weight')
            if any(value<0 for value in weights):raise ValueError('negative posterior weight')
            if weights and abs(sum(weights)-1)>1e-8:raise ValueError('posterior weights must sum to one')
            if posterior.get('samples') and len(weights)!=len(posterior['samples']):raise ValueError('posterior sample/weight mismatch')
    return payload


def nested_json_schema(schema, field):
    """Machine-readable field-set contract; procedural consumer constraints also apply."""
    allowed=FIELDS.get(schema,{}).get(field)
    if allowed is None:return None
    return {'type':'object','additionalProperties':False,'properties':{key:{} for key in sorted(allowed)},
            'x-consumed-contract':VERSION}


def consumed_json_schemas(schema):
    result={field:nested_json_schema(schema,field) for field in FIELDS.get(schema,{})}
    if schema=='observation.v2':
        result['raw_blocks']={'type':'array','items':{'type':'object','required':['block_id'],
            'additionalProperties':False,'properties':{key:{} for key in sorted(RAW_FIELDS)},'x-consumed-contract':VERSION}}
        props=result['raw_blocks']['items']['properties']
        props['block_id']={'type':'string','minLength':1}
        for key in ('values','wavelengths_nm','coordinate'):props[key]={'type':'array','items':{'type':'number'}}
        props['covariance']={'type':'array','items':{'type':'array','items':{'type':'number'}}}
        props['gaussian_validated']={'type':'boolean'}
        props['polarization']={'enum':['s','p','unpolarized','TE','TM','te','tm']}
        for key in ('channel','coordinate_units','units'):props[key]={'type':'string','minLength':1}
        for key in BOOL_QUALITY:result['quality']['properties'][key]={'type':'boolean'}
        result['quality']['properties']['audit_binding']={'type':'string','pattern':'^[0-9a-f]{64}$'}
    if schema=='research-task.v1':
        for key in ('wavelength_nm','target_R','risk_aversion'):result['objective']['properties'][key]={'type':'number'}
        result['objective']['properties']['design_pool_nm']={'type':'array','items':{'type':'number'}}
    def obj(keys, required=()):
        return {'type':'object','additionalProperties':False,'properties':{key:{} for key in sorted(keys)},'required':list(required)}
    if schema=='model-candidate.v1':
        from .model_ir import ModelIR, IRNode, ParameterSpec
        optical=obj({f.name for f in fields(ModelIR)},('nodes','output'))
        optical['properties']['nodes']={'type':'array','items':obj({f.name for f in fields(IRNode)},('node_id','operator'))}
        optical['properties']['parameters']={'type':'array','items':obj({f.name for f in fields(ParameterSpec)},('name','lower','upper','initial'))}
        optical['properties']['version']={'const':'optical-ir/1'}
        historical=obj({'schema_version','candidate_id','layer_materials','layer_labels','incident_medium','exit_medium'},('schema_version',))
        historical['properties']['schema_version']={'const':'legacy-candidate.v1'}
        native=obj({'simulation'},('simulation',));native['properties']['simulation']={'type':'object'}
        fixture=obj({'kind','name'},('kind',));fixture['properties']['kind']={'enum':['fixture','finite_fixture']}
        result['model_ir']={'anyOf':[obj(set()),optical,historical,native,fixture],'x-consumed-contract':VERSION}
    if schema=='experiment-program.v1':
        from .program_ir import ProgramIR,ProgramNode
        program=obj({f.name for f in fields(ProgramIR)},('program_id',))
        program['properties']['nodes']={'type':'array','items':obj({f.name for f in fields(ProgramNode)},('node_id','kind'))}
        historical=obj({'schema_version','experiment_id','mode','tmm_task','source_task_hash'},('schema_version','tmm_task'))
        historical['properties']['schema_version']={'const':'legacy-tmm-experiment.v1'}
        historical['properties']['tmm_task']={'type':'object'}
        angle=obj({'angle'},('angle',));angle['properties']['angle']={'type':'number'}
        result['protocol']={'anyOf':[obj(set()),program,historical,angle],'x-consumed-contract':VERSION}
    return result
