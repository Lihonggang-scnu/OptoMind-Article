"""Reproduce the C18/C19 numerical overlap report without network or paid calls.

Run from code/ with PYTHONPATH=.:../veritmm and optional pinned physics deps.
"""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import platform
from pathlib import Path
import numpy as np
from optomind_optics.harness.tools.rcwa_adapter import RCWAAdapter
from optomind_optics.harness.tools.fullwave_adapter import FullwaveAdapter
from optomind_optics.harness.tools.independent_registry import manifest_dicts
from tmm_engine.tmm_solver import TMM,TMMConfig


def summary(receipt):
    return {**{k:v for k,v in receipt['result'].items() if k not in ('field_Ez','reflected_amplitudes','transmitted_amplitudes')},
            'cost':receipt['cost'],'result_hash':receipt['result_hash']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report={'schema':'independent-physics-validation.v1','physical_evidence':False,
            'runtime':{'python':platform.python_version(),**{p:importlib.metadata.version(p) for p in ('numpy','scipy','grcwa','ceviche','autograd')}},
            'manifests':manifest_dicts(),'uniform':[],'periodic':[],'boundary':[]}
    tmm=TMM(TMMConfig(wavelength_unit='m'))
    R,T=tmm.rt_spectrum([np.array([1.5]),np.array([1.])],[100e-9,0],np.array([600e-9]),0,'s')
    report['uniform_reference']={'R':float(R[0]),'T':float(T[0]),'source':'embedded unmodified VeriTMM TMM.rt_spectrum',
                                 'conditions':'600 nm vacuum / n=1.5,100 nm / vacuum; normal incidence s'}
    rc=RCWAAdapter()
    p=dict(wavelength_nm=600.,period_nm=[450.,450.],layers=[dict(thickness_nm=100.,epsilon=2.25)],fourier_order=21)
    report['uniform'].append({'backend':'grcwa',**summary(rc.run(p))})
    for dx in (20.,10.,5.):
        for patterned in (False,True):
            ny=round(800/dx) if patterned else 4
            eps=np.ones((round(2000/dx),ny))
            if patterned:
                eps[round(900/dx):round(1060/dx)] = (1.8+.25*np.cos(2*np.pi*(np.arange(ny)+.5)/ny))[None,:]
            else: eps[round(900/dx):round(1000/dx)] = 2.25
            p=dict(wavelength_nm=600.,dx_nm=dx,epsilon_grid=eps.tolist(),npml=round(300/dx),source_x=round(400/dx),reflection_x=round(600/dx),transmission_x=round(1400/dx))
            r=summary(FullwaveAdapter().run(p))
            report['periodic' if patterned else 'uniform'].append({'backend':'ceviche',**r})
            if not patterned and dx==20:
                for pml in (200,250,350):
                    report['boundary'].append({'requested_pml_nm':pml,'actual_pml_nm':round(pml/dx)*dx,**summary(FullwaveAdapter().run(dict(p,npml=round(pml/dx))))})
    report['periodic_conditions']={'wavelength_nm':600.,'period_nm':800.,'thickness_nm':160.,
        'epsilon':'1.8+0.25 cos(2 pi y/P)','ports':'vacuum','angle_deg':0,
        'coordinate_mapping':'RCWA x lateral=FDFD y, RCWA z propagation=FDFD x, RCWA s=FDFD Ez'}
    ep=1.8+.25*np.cos(2*np.pi*(np.arange(128)+.5)/128)
    grid=np.repeat(ep[:,None],32,axis=1)
    for n in (13,29,49):
        p=dict(wavelength_nm=600.,period_nm=[800.,100.],layers=[dict(thickness_nm=160.,epsilon_grid=grid.tolist())],fourier_order=n)
        report['periodic'].append({'backend':'grcwa',**summary(RCWAAdapter().run(p))})
    report['limitations']=['Convergence deltas are empirical, not a rigorous error bound.',
        'No absorption, anisotropy, 3D, FDTD, automatic gradients, or surrogate enabled.',
        'No mid-flight cancellation or timeout; bounded synchronous solves.',
        'No physical experimental evidence; matching solvers can share material errors.',
        'Optional GPL-3.0 grcwa dependency requires distribution license review.']
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(args.output)


if __name__=='__main__': main()
