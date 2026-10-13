"""A soft target must not turn minimization into target-distance matching."""
import pytest

from optomind_optics.harness.scoring_standard import (
    FixedScoreMetric, _orientation_errors, compile_formula,
)


def metric(sense, target):
    return FixedScoreMetric(variable='R', canonical_id='mean_reflectance@450-700nm',
        metric='mean_reflectance', sense=sense, target=target,
        region={'wavelength_nm': [450, 700], 'angle_deg': 0, 'polarization': 's'})


@pytest.mark.parametrize('target', [0.0001, 0.03, 0.97, 0.9999])
@pytest.mark.parametrize('sense', ['minimize', 'maximize'])
def test_direction_check_probes_both_sides_of_soft_target(target, sense):
    formula = compile_formula(f'-abs(R - {target})', variables=['R'])
    assert _orientation_errors(formula, [metric(sense, target)])


@pytest.mark.parametrize('sense,expression', [('minimize', '-R'), ('maximize', 'R'), ('match', '-abs(R - 0.03)')])
def test_actual_direction_and_explicit_match_remain_supported(sense, expression):
    assert not _orientation_errors(compile_formula(expression, variables=['R']), [metric(sense, 0.03)])
