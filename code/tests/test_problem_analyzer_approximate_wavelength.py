"""Approximate background mentions must not add covered exact-point goals."""
import pytest
from optomind_optics.harness.problem_analyzer import _extract_wavelength_intervals, QwenTMMProblemAnalyzer
from test_tmm_problem_analyzer import _FakeQwenClient, _analysis_payload


@pytest.mark.parametrize('mention', [
    '355 nm 附近', '355 nm 左右', '约355 nm', '大约 355 nm',
    'near 355 nm', 'around 355 nm', 'about 355 nm',
    'approximately 355 nm', 'approx. 355 nm', '~355 nm', '≈ 355 nm',
])
def test_covered_approximate_background_does_not_add_exact_point(mention):
    assert _extract_wavelength_intervals(f'Excitation {mention}; suppress 355-365 nm and transmit 420-700 nm.') == [(355.,365.),(420.,700.)]


@pytest.mark.parametrize('mention', ['at 355 nm', 'exactly 355 nm', '在355 nm', '355 nm 处'])
def test_explicit_point_remains_mandatory_even_inside_band(mention):
    assert _extract_wavelength_intervals(f'Report reflectance {mention}; also report 350-370 nm.') == [(350.,370.),(355.,355.)]


def test_uncovered_approximate_point_is_not_silently_discarded():
    assert _extract_wavelength_intervals('near 405 nm; report 420-700 nm.') == [(420.,700.),(405.,405.)]


def test_approximation_does_not_leak_across_punctuation_or_other_words():
    assert _extract_wavelength_intervals('near the sample; exactly 355 nm, with 350-370 nm.') == [(350.,370.),(355.,355.)]


def test_unit_conversion_and_other_band_are_not_case_specific():
    assert _extract_wavelength_intervals('around 1.55 um; report 1500-1600 nm.') == [(1500.,1600.)]


def test_semantic_validation_accepts_bands_and_still_rejects_missing_exact_point():
    response = _analysis_payload(wavelengths_nm=[[350,370]], normalized_request_english='Analyze reflectance from 350-370 nm with excitation near 355 nm.')
    approx = QwenTMMProblemAnalyzer(client=_FakeQwenClient([response]), maximum_attempts=1).analyze('Excitation near 355 nm; analyze reflectance from 350-370 nm.')
    assert approx.status == 'analyzed'
    exact = QwenTMMProblemAnalyzer(client=_FakeQwenClient([response]), maximum_attempts=1).analyze('Analyze reflectance at 355 nm and from 350-370 nm.')
    assert exact.status == 'invalid'
    assert any('355-355' in warning for warning in exact.validation_warnings)
