"""Independent synthetic reproduction and diagnosis, never model input.

Does not modify production tasks/results or provide scientific answers to an
agent. Reference search is explicitly separate audit work, not an agent win.
"""
from pathlib import Path
import csv
import hashlib
import json
import runpy
import shutil
import sys
import time

import numpy as np
from scipy.optimize import minimize
from tmm import coh_tmm
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
BUNDLE = Path('D:/OptoMind-Article-3/incoming/20261013_R2/unpacked/OptoMind_云端修复交接_20261013_R2')

def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

def spectrum(indices, thicknesses, waves, substrate=1.52):
    return np.asarray([coh_tmm('s', [1., *indices, substrate],
        [np.inf, *thicknesses, np.inf], 0., w)['R'] for w in waves])

def probe():
    out = ROOT / 'materials/B0-principle'
    out.mkdir(parents=True, exist_ok=False)
    (out / 'PRE_EXECUTION_SPEC.md').write_text('''# B0: known synthetic principle reproduction

Reproduce the hand-selected probe already included in the cloud package.
Known published values are not a blind discovery experiment. Copy the script
before execution; preserve the package original. Independently verify all
four normal-incidence lossless spectra with tmm.coh_tmm, tolerance1e-10.
Bare reversed-specimen intensity difference <=1e-10; front-end intensity
difference >1e-3 is a known principle reproduction gate, not E01 acceptance.
Record raw spectra and plot. No model, noisy data, manufacturing tolerance,
automatic front-end generation or alternative-explanation refit. Cost0.
''', encoding='utf-8')
    src = BUNDLE / 'exploration/diagnostic_probe_sanity_20261013.py'
    local = out / src.name
    shutil.copy2(src, local)
    original = hashlib.sha256(src.read_bytes()).hexdigest()
    runpy.run_path(str(local), run_name='__main__')
    actual = np.genfromtxt(local.with_suffix('.csv'), delimiter=',', names=True)
    waves = actual['wavelength_nm']
    ns, ds = [1.46, 2.4, 1.8], [80, 110, 50]
    cases = [(ns, ds), (ns[::-1], ds[::-1]),
        ([2., 1.]+ns, [35, 250]+ds), ([2., 1.]+ns[::-1], [35, 250]+ds[::-1])]
    refs = [spectrum(n, d, waves, 1.) for n, d in cases]
    errors = [float(np.max(np.abs(actual[name]-ref))) for name, ref in
        zip(actual.dtype.names[1:], refs)]
    assert max(errors) <= 1e-10
    bare = float(np.max(np.abs(refs[0]-refs[1])))
    front = float(np.max(np.abs(refs[2]-refs[3])))
    assert bare <= 1e-10 and front > 1e-3
    assert hashlib.sha256(src.read_bytes()).hexdigest() == original
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.5), constrained_layout=True)
    for ax, curves, title in zip(axes, (refs[:2], refs[2:]), ('Bare specimen', 'Hand-selected front end')):
        ax.plot(waves, curves[0], label='Order A')
        ax.plot(waves, curves[1], '--', label='Reversed order')
        ax.set(title=title, xlabel='Wavelength (nm)', ylabel='Reflectance')
        ax.legend()
    fig.suptitle('Synthetic principle reproduction; no autonomous discovery')
    fig.savefig(out/'principle_spectra.svg'); fig.savefig(out/'principle_spectra.png', dpi=180)
    plt.close(fig)
    result = {'status': 'principle_reproduced_only', 'E01_status': 'not_implemented',
        'synthetic': True, 'hand_selected': True, 'agent_demonstration': False,
        'package_source_sha256': original, 'independent_tmm_max_error': max(errors),
        'bare_max_R_difference': bare, 'frontend_max_R_difference': front,
        'missing': ['autonomous front-end generation', 'competing explanation refit',
            'noise/manufacturing robustness', 'classification>=95%', 'tolerance success>=90%'],
        'cost_cny': 0, 'python': sys.executable}
    dump(out/'RESULT.json', result)
    (out/'RESULT.md').write_text(f'''# B0 result

Known hand-selected synthetic probe reproduced. Independent solver maximum
absolute error: {max(errors):.17g}. Bare reversed-order intensity difference:
{bare:.17g}; manually selected front-end difference: {front:.17g}.

E01 remains not implemented: no autonomous structure search, alternate
explanation refit, or noisy/tolerance classification experiment was run.
The fixed pair is not evidence that all alternative explanations are excluded.
''', encoding='utf-8')
    return result

def e10():
    out = ROOT / 'materials/B4-device-diagnosis'
    out.mkdir(parents=True, exist_ok=False)
    (out/'PRE_EXECUTION_SPEC.md').write_text('''# E10 independent failure diagnosis

The production result is already known: mean R≈0.03536, original target<0.03.
This is a post-result diagnosis, not preregistration of the agent experiment.
Never inject reference solutions into model prompts or replace production
outputs. Preserve its two layers, indices1.46/2.4 (one each, both orders),
air1/glass1.52, thickness[40,160]nm, normal s,450..700/5nm/51 points.

Compare an independent direct arithmetic-mean search against existing
production candidates and the declared squared-spectrum optimizer surrogate.
Fixed starts (nm):[80,80],[40,40],[160,160],[40,160],[160,40]. Both layer
orders; L-BFGS-B finite differences, at most64 independent forward evaluations
per start, retain best candidate when this separate diagnostic cap is reached.
No optimality/infeasibility certificate. Reference solver work is additional
offline audit effort, never counted as production's budget or an agent win.

Reproduce the production winner's declared absolute-normal1nm perturbations:
16 draws, seed17, no angle/index perturbation. Compare distributions to stored
statistics when the production sampling protocol is identical; otherwise
report protocol differences rather than loosen tolerances. Do not interpret
synthetic parameter perturbations as manufacturing yield. Do not modify any
scientific target, seed, production code, stop rule or test expectation. Cost0.
''', encoding='utf-8')
    started = time.perf_counter()
    run = ROOT/'article/resume-02'
    audit = load(run/'INDEPENDENT_ACCEPTANCE.json')
    winner = min(audit['independently_checked_candidates'], key=lambda x:x['sample_mean'])
    waves = np.arange(450., 701., 5.)
    histories, references = [], []
    class Exhausted(Exception): pass
    for ns in ([1.46, 2.4], [2.4, 1.46]):
        for start in ([80.,80.],[40.,40.],[160.,160.],[40.,160.],[160.,40.]):
            rows = []
            def evaluate(ds):
                if len(rows) >= 64:
                    raise Exhausted
                r = spectrum(ns, ds, waves)
                row = {'layer_indices': list(ns), 'start': list(start), 'call': len(rows)+1,
                    'thicknesses_nm': np.asarray(ds).tolist(), 'sample_mean_R': float(r.mean()),
                    'sample_mean_squared_R': float(np.mean(r*r))}
                rows.append(row)
                return row['sample_mean_R']
            try:
                opt = minimize(evaluate, start, method='L-BFGS-B', bounds=[(40.,160.)]*2,
                    options={'maxiter':64, 'maxfun':64, 'ftol':1e-12, 'gtol':1e-8})
                stop = str(opt.message)
            except Exhausted:
                stop = 'diagnostic_evaluation_cap_reached'
            best = min(rows, key=lambda x:x['sample_mean_R'])
            references.append({**best, 'evaluations':len(rows), 'stop':stop})
            histories.extend(rows)
    best_reference = min(references, key=lambda x:x['sample_mean_R'])
    nominal = spectrum(winner['layer_indices'], winner['thicknesses_nm'], waves)
    rng = np.random.default_rng(17)
    perturbed_ds = np.asarray(winner['thicknesses_nm'])[None,:] + rng.normal(0.,1.,size=(16,2))
    perturbations = [spectrum(winner['layer_indices'], ds, waves) for ds in perturbed_ds]
    means = [float(r.mean()) for r in perturbations]
    with (out/'reference_search.csv').open('w', newline='', encoding='utf-8') as f:
        writer=csv.DictWriter(f, fieldnames=list(histories[0])); writer.writeheader();writer.writerows(histories)
    with (out/'winner_tolerance.csv').open('w', newline='', encoding='utf-8') as f:
        writer=csv.writer(f);writer.writerow(['draw','d0_nm','d1_nm','sample_mean_R','target_below_0_03'])
        writer.writerows((i,*d,m,m<.03) for i,(d,m) in enumerate(zip(perturbed_ds,means)))
    fig, ax=plt.subplots(figsize=(7,4), constrained_layout=True)
    ax.plot(waves,nominal,label='Actual production winner')
    ax.plot(waves,spectrum(best_reference['layer_indices'],best_reference['thicknesses_nm'],waves),
        '--', label='Independent diagnostic reference')
    ax.set(xlabel='Wavelength (nm)',ylabel='Reflectance',
        title='Synthetic E10 diagnosis; reference not supplied to agent')
    ax.legend();fig.savefig(out/'device_spectra.svg');fig.savefig(out/'device_spectra.png',dpi=180);plt.close(fig)
    result = {'status':'target_not_met', 'production_mean_R':winner['sample_mean'],
        'production_thicknesses_nm':winner['thicknesses_nm'], 'production_indices':winner['layer_indices'],
        'production_target_met':winner['sample_mean']<.03, 'original_target':.03,
        'independent_reference_best':best_reference, 'reference_by_start':references,
        'reference_evaluations':len(histories), 'reference_not_agent_result':True,
        'surrogate_at_production_winner':float(np.mean(nominal**2)),
        'surrogate_at_direct_mean_reference':float(np.mean(spectrum(best_reference['layer_indices'],best_reference['thicknesses_nm'],waves)**2)),
        'diagnostic_perturbation':{'model':'absolute_normal','sigma_nm':1.,'seed':17,'draws':16,
            'sample_mean_R_mean':float(np.mean(means)),'sample_mean_R_std_population':float(np.std(means)),
            'target_met_draws':sum(m<.03 for m in means),'manufacturing_yield_claim':False},
        'weak_modules':['route refinement repeated the same compiled task in the restored run',
            'local optimizer uses mean squared spectral R surrogate, not frozen arithmetic mean R',
            'finite local optimization/search coverage cannot certify ideal-target infeasibility'],
        'cost_cny':0,'wall_seconds':time.perf_counter()-started,'python':sys.executable}
    dump(out/'RESULT.json',result)
    (out/'RESULT.md').write_text(f'''# E10 result and failure diagnosis

Actual model-generated production winner: arithmetic mean R={winner['sample_mean']:.17g};
original target<0.03 remains unmet. Its valid candidate is retained.
An independent bounded diagnostic search reached R={best_reference['sample_mean_R']:.17g}
in {len(histories)} total extra audit evaluations. This reference was never
provided to the model and is not a replacement production result or a global
certificate. No repair/ablation benefit is inferred.

Strengthen route refinement and make optimizer-surrogate choice explicit
relative to the frozen objective before any larger efficiency claim. Current
`tmm_engine/optimization.py` explicitly labels its squared-spectrum surrogate;
this is a pre-existing optimization limitation, not evidence of a new R2
ranking direction failure. Both restored rounds compiled the same task.

The 16 synthetic1nm perturbations use seed17. Their mean reflectance is
{np.mean(means):.17g}; population standard deviation {np.std(means):.17g};
{sum(m<.03 for m in means)}/16 meet the original target. This is not manufacturing
yield or experimental validation. Complete spectra/calls are retained locally.
''',encoding='utf-8')
    dump(out/'tolerance_spectra.json',{'wavelength_nm':waves.tolist(),'thicknesses_nm':perturbed_ds.tolist(),
        'R':[r.tolist() for r in perturbations],'synthetic':True})
    return {k:v for k,v in result.items() if k!='reference_by_start'}

if __name__=='__main__':
    dump(ROOT/'materials/PHYSICS_DIAGNOSTIC_RESULTS.json',{'B0':probe(),'E10':e10()})
