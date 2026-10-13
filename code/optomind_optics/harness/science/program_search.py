"""Typed construction of an adaptive calibration/coarse/local-refinement program."""
from .program_ir import ProgramIR,ProgramNode


def adaptive_measurement_program(*,program_id,channel,reference_channel,coarse_grid,fine_grid,threshold,sample_id="sample",reference_id="reference",cost=1.,expected_reference=None):
    if len(coarse_grid)<2 or len(fine_grid)<2:raise ValueError("measurement grids require at least two points")
    group=f"{program_id}:calibration"
    estimate=(("calls",1.),("cost",float(cost)))
    nodes=(ProgramNode("calibrate","new_evidence",channel=reference_channel,sample_id=reference_id,arguments=(("wavelengths_nm",tuple(coarse_grid)),),estimate=estimate,correlation_group=group),
           ProgramNode("coarse","new_evidence",("calibrate",),channel=channel,sample_id=sample_id,arguments=(("wavelengths_nm",tuple(coarse_grid)),),estimate=estimate,correlation_group=group),
           ProgramNode("variation","derived_view",("coarse","calibrate") if expected_reference is not None else ("coarse",),arguments=(("statistic","range"),("calibration","reference_offset"),("expected_reference",expected_reference)) if expected_reference is not None else (("statistic","range"),)),
           ProgramNode("refine_needed","branch",("variation",),arguments=(("comparison","gt"),("threshold",threshold))),
           ProgramNode("refine","new_evidence",("coarse",),channel=channel,sample_id=sample_id,arguments=(("wavelengths_nm",tuple(fine_grid)),),condition=("refine_needed",True),estimate=estimate,correlation_group=group),
           ProgramNode("stop","terminate",condition=("refine_needed",False)))
    return ProgramIR(program_id,nodes,((sample_id,"ready"),(reference_id,"ready")))


def polarization_measurement_program(*,program_id,coarse_grid,fine_grid,threshold,expected_reference,
                                     sample_id='sample',reference_id='reference',cost=1.):
    """Compose actual registered s/p channels, shared reference, and a causal branch."""
    import math
    grids=(tuple(coarse_grid),tuple(fine_grid))
    if any(len(g)<2 or len(g)>4096 or not all(map(math.isfinite,g)) or any(b<=a for a,b in zip(g,g[1:])) for g in grids):raise ValueError('bounded ordered polarization grids required')
    if not math.isfinite(threshold) or threshold<0 or not math.isfinite(cost) or cost<0:raise ValueError('nonnegative threshold/cost required')
    group=program_id+':shared_detector_offset';estimate=(('calls',1.),('cost',float(cost)))
    def measurement(name,channel,sample,grid,inputs=(),condition=None):
        return ProgramNode(name,'new_evidence',inputs,channel=channel,sample_id=sample,arguments=(('wavelengths_nm',grid),),condition=condition,estimate=estimate,correlation_group=group)
    nodes=[measurement('calibrate','R_s',reference_id,grids[0]),
        measurement('coarse_s','R_s',sample_id,grids[0],('calibrate',)),
        measurement('coarse_p','R_p',sample_id,grids[0],('coarse_s',))]
    for pol in ('s','p'):
        nodes.append(ProgramNode('corrected_'+pol,'derived_view',('coarse_'+pol,'calibrate'),arguments=(('statistic','mean'),('calibration','reference_offset'),('expected_reference',expected_reference))))
    nodes.extend((ProgramNode('contrast','derived_view',('corrected_s','corrected_p'),arguments=(('statistic','mean_absolute_difference'),)),
        ProgramNode('refine_needed','branch',('contrast',),arguments=(('comparison','gt'),('threshold',float(threshold)))),
        measurement('refine_s','R_s',sample_id,grids[1],('coarse_s',),('refine_needed',True)),
        measurement('refine_p','R_p',sample_id,grids[1],('coarse_p',),('refine_needed',True)),
        ProgramNode('stop','terminate',condition=('refine_needed',False))))
    return ProgramIR(program_id,tuple(nodes),((sample_id,'ready'),(reference_id,'ready')))
