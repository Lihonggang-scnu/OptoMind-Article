import json
from pathlib import Path

import numpy as np


wavelength_nm = np.linspace(450, 700, 251)


def reflection_amplitude(indices, thickness_nm):
    n = np.array([1.0] + list(indices) + [1.0])
    r = np.full(wavelength_nm.shape, (n[-2] - n[-1]) / (n[-2] + n[-1]), dtype=complex)
    for j in range(len(thickness_nm) - 1, -1, -1):
        interface = (n[j] - n[j + 1]) / (n[j] + n[j + 1])
        propagation = np.exp(4j * np.pi * n[j + 1] * thickness_nm[j] / wavelength_nm)
        r = (interface + r * propagation) / (1 + interface * r * propagation)
    return r


indices = [1.46, 2.4, 1.8]
thickness_nm = [80, 110, 50]
bare_a = reflection_amplitude(indices, thickness_nm)
bare_b = reflection_amplitude(indices[::-1], thickness_nm[::-1])
front_a = reflection_amplitude([2.0, 1.0] + indices, [35, 250] + thickness_nm)
front_b = reflection_amplitude([2.0, 1.0] + indices[::-1], [35, 250] + thickness_nm[::-1])
result = {
    'status': 'independent_principle_sanity_check_not_agent_demonstration',
    'assumptions': ['normal incidence', 'coherent', 'lossless constant indices', 'air on both sides', 'manually chosen reference front end', 'no noise or manufacturing analysis'],
    'specimen_indices': indices,
    'specimen_thickness_nm': thickness_nm,
    'comparison': 'reversed specimen layer order',
    'front_end_indices': [2.0, 1.0],
    'front_end_thickness_nm': [35, 250],
    'wavelength_nm': [450, 700, 251],
    'bare_max_R_difference': float(np.max(np.abs(abs(bare_a) ** 2 - abs(bare_b) ** 2))),
    'bare_max_complex_r_difference': float(np.max(abs(bare_a - bare_b))),
    'front_end_max_R_difference': float(np.max(np.abs(abs(front_a) ** 2 - abs(front_b) ** 2))),
}
out = Path(__file__).with_suffix('.json')
out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
np.savetxt(Path(__file__).with_suffix('.csv'), np.column_stack([wavelength_nm, abs(bare_a) ** 2, abs(bare_b) ** 2, abs(front_a) ** 2, abs(front_b) ** 2]), delimiter=',', header='wavelength_nm,bare_a_R,bare_b_R,frontend_a_R,frontend_b_R', comments='')
print(json.dumps(result, ensure_ascii=False))
