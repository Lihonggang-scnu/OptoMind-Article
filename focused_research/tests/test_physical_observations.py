from copy import deepcopy
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from physical_observations import build_physical_observations, compare_predictions, costed_probe_plan


def fixture():
    goals=[{'objective_id':'G1','metric':'mean_reflectance','sense':'maximize','mean_reduction':'wavelength_integral_mean',
        'region':{'wavelength_nm':[450,650],'angle_deg':45,'polarization':'unpolarized'}},
        {'objective_id':'G2','metric':'mean_transmittance','sense':'maximize','mean_reduction':'wavelength_integral_mean',
        'region':{'wavelength_nm':[700,1000],'angle_deg':45,'polarization':'unpolarized'}}]
    b={'instance_id':'baseline','physics_accepted':True,'objectives':goals,
       'parameters':{'thicknesses_nm':[50,90],'layer_materials':['H','L']},
       'full_result_ref':{'path':'baseline.json','sha256':'a'*64},
       'raw_result':{'wavelengths_nm':[450,500,650,700,850,1000],
           'channels':{'angle=45|pol=unpolarized':{'R':[.2,.4,.2,.1,.1,.1],'T':[.8,.6,.8,.9,.9,.9],'A':[0]*6}}}}
    c=deepcopy(b);c['instance_id']='candidate';c['parameters']['thicknesses_nm'][0]=60
    c['full_result_ref']={'path':'candidate.json','sha256':'b'*64}
    c['raw_result']['channels']['angle=45|pol=unpolarized']['R']=[.3,.5,.3,.2,.1,.2]
    c['raw_result']['channels']['angle=45|pol=unpolarized']['T']=[.7,.5,.7,.8,.9,.8]
    return b,c,goals


def test_all_goals_units_conditions_integral_and_localization():
    b,c,g=fixture();o=build_physical_observations(b,c,g)
    assert [r['goal_id'] for r in o['goals']]==['G1','G2']
    a,z=o['goals'];assert a['baseline']==pytest.approx(.3)
    assert a['delta']==pytest.approx(.1)
    assert z['delta']==pytest.approx(-.05)
    assert a['spectral_change']['changed_sample_count']==5
    assert len(a['spectral_change']['changed_regions'])==2
    assert a['band_samples']['baseline']['max']['wavelengths_nm']==[500]
    assert a['expand_handles']['candidate']['channel']==g[0]['region']
    assert o['parameter_diff']['differences'][0]['changed_layers'][0]['delta_nm']==10
    assert o['brief_view']['all_goals_retained']
    assert o['full_result_refs']['candidate']==c['full_result_ref']
    assert o['claim_scope']=='sampled_grid_only'


def test_numerical_direction_is_separate_from_mechanism():
    b,c,g=fixture();o=build_physical_observations(b,c,g)
    rows=compare_predictions([{'goal_id':'G1','direction':'increase'},{'goal_id':'G2','direction':'increase'}, {'goal_id':'missing','direction':'increase'}],o)
    assert [r['status'] for r in rows]==['supports','challenged','unknown']
    assert all(r['mechanism_status']=='unknown' for r in rows)


@pytest.mark.parametrize('change',['grid','channel','certificate','nan','endpoint'])
def test_invalid_inputs_keep_both_unknown_goals(change):
    b,c,g=fixture()
    if change=='grid':c['raw_result']['wavelengths_nm'][1]=510
    if change=='channel':c['raw_result']['channels']={}
    if change=='certificate':c['physics_accepted']=False
    if change=='nan':
        c['raw_result']['channels']['angle=45|pol=unpolarized']['R'][0]=float('nan')
        c['raw_result']['channels']['angle=45|pol=unpolarized']['T'][0]=float('nan')
    if change=='endpoint':
        b['raw_result']['wavelengths_nm'][0]=449;c['raw_result']['wavelengths_nm'][0]=449
        b['raw_result']['wavelengths_nm'][-1]=1001;c['raw_result']['wavelengths_nm'][-1]=1001
    o=build_physical_observations(b,c,g)
    assert len(o['goals'])==2
    assert all(r['status']=='unknown' for r in o['goals'])


def test_nonuniform_integral_mean_not_sample_average():
    b,c,g=fixture();b['raw_result']['channels']['angle=45|pol=unpolarized']['R'][:3]=[0,0,1]
    o=build_physical_observations(b,c,g)
    assert o['goals'][0]['baseline']==pytest.approx(.375)


def test_explicit_costed_probe_never_executes():
    p=costed_probe_plan(kind='terminal_group',fractional_change=.01,layer_indices=[18,19],points_per_evaluation=101)
    assert p['cost']=={'forward_evaluations':2,'spectral_points':202}
    assert p['status']=='proposed_not_executed'
    with pytest.raises(ValueError):costed_probe_plan(kind='anything',fractional_change=.01,layer_indices=[0],points_per_evaluation=1)


def test_wrong_metric_or_comparison_cannot_support_prediction():
    b,c,g=fixture();o=build_physical_observations(b,c,g)
    rows=compare_predictions([{'goal_id':'G1','direction':'increase','metric':g[1]},
        {'goal_id':'G1','direction':'increase','baseline_instance_id':'other'}],o)
    assert all(r['status']=='unknown' for r in rows)


def test_repeated_instance_names_across_routes_cannot_be_compared():
    b,c,g=fixture();b['route_id']='R1';c['route_id']='R2'
    assert all(r['status']=='unknown' for r in build_physical_observations(b,c,g)['goals'])


def test_brief_proactively_exposes_coverage_extrema_and_main_change():
    o=build_physical_observations(*fixture());brief=o['brief_view']['goals'][0]
    assert brief['sample_grid_coverage']['sample_count']==6
    assert brief['band_extrema']['candidate']['max']['first_wavelength_nm']==500
    assert brief['largest_change_region'] is not None
    assert brief['other_changed_region_count']==1
    assert brief['expand_handles']


def test_mean_absorption_task_alias():
    b,c,g=fixture();g[0]['metric']='mean_absorption'
    o=build_physical_observations(b,c,g)
    assert o['goals'][0]['status']=='observed'
    assert o['goals'][0]['baseline']==0
    assert o['goals'][0]['spectral_change']['quantity']=='A'


def test_scoped_predictions_require_correct_complete_identity():
    b,c,g=fixture();b['route_id']=c['route_id']='R1'
    o=build_physical_observations(b,c,g)
    rows=compare_predictions([
        {'goal_id':'G1','direction':'increase','route_id':'R2','baseline_instance_id':'baseline','candidate_instance_id':'candidate'},
        {'goal_id':'G1','direction':'increase','route_id':'R1'},
        {'goal_id':'G1','direction':'increase','route_id':'R1','baseline_instance_id':'baseline','candidate_instance_id':'candidate'}],o)
    assert [r['status'] for r in rows]==['unknown','unknown','supports']
