"""Executable development fixtures for the frozen TMM Harness benchmark.

These fixtures are not a natural-language parser and are never used to answer
holdout tasks.  They provide reproducible protocol instances for DEV01--DEV05
so the general Harness can be debugged without tuning on the sealed split.
"""

from __future__ import annotations

from typing import Any, Dict

from .design_task import EngineMode, ObjectivePreference, OpticalDesignTask, TMMExperimentSpec


def _physics() -> Dict[str, Any]:
    return {
        "geometry_class": "layered_planar",
        "material_class": "isotropic",
        "excitation_class": "plane_wave",
        "time_domain_required": False,
    }


def _medium(*, material: str | None = None, n: float | None = None, provider: str | None = None, dataset_id: str | None = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"constant_k": 0.0}
    if material is not None:
        payload["material"] = material
    else:
        payload["constant_n"] = float(n)
    if provider is not None:
        payload["provider"] = provider
    if dataset_id is not None:
        payload["dataset_id"] = dataset_id
    return payload


def _layer(
    material: str | None,
    thickness_nm: float,
    *,
    n: float | None = None,
    k: float = 0.0,
    coherence: str = "coherent",
    optimizable: bool = False,
    lo: float | None = None,
    hi: float | None = None,
    label: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "material": material,
        "thickness_nm": float(thickness_nm),
        "coherence": coherence,
        "optimizable": bool(optimizable),
        "constant_k": float(k),
        "label": label,
    }
    if material is None:
        payload["constant_n"] = float(n)
    if lo is not None:
        payload["min_thickness_nm"] = float(lo)
    if hi is not None:
        payload["max_thickness_nm"] = float(hi)
    return payload


def _simulation(
    layers: list[Dict[str, Any]],
    *,
    start_nm: float,
    stop_nm: float,
    points: int,
    angles: list[float] | None = None,
    polarizations: list[str] | None = None,
    exit_medium: Dict[str, Any] | None = None,
    requested_outputs: list[str] | None = None,
    solver: str = "smatrix",
    name: str,
) -> Dict[str, Any]:
    return {
        "stack": {
            "name": name,
            "incident": _medium(n=1.0),
            "layers": layers,
            "exit": exit_medium or _medium(n=1.52),
        },
        "spectrum": {"start_nm": start_nm, "stop_nm": stop_nm, "points": points},
        "illumination": {
            "angles_deg": angles or [0.0],
            "polarizations": polarizations or ["unpolarized"],
        },
        "solver": solver,
        "allow_material_extrapolation": False,
        "requested_outputs": requested_outputs or ["R", "T", "A"],
        "physics": _physics(),
    }


def _dev01() -> OpticalDesignTask:
    simulation = _simulation(
        [_layer(None, 110.0, n=1.23, optimizable=True, lo=30.0, hi=250.0, label="matching_layer")],
        start_nm=500.0,
        stop_nm=600.0,
        points=101,
        name="single_layer_ar",
    )
    optimization = {
        "simulation": simulation,
        "targets": [{
            "observable": "R", "target": 0.0, "wavelength_min_nm": 500.0,
            "wavelength_max_nm": 600.0, "weight": 1.0, "angle_deg": 0.0,
            "polarization": "unpolarized", "constraint": "at_most", "aggregation": "mean",
            "name": "minimize_band_reflectance",
        }],
        "optimizer": {
            "method": "adam_lbfgs", "max_steps": 60, "learning_rate": 0.05,
            "starts": 3, "seed": 1701, "early_stop_patience": 15,
            "improvement_tolerance": 1e-8, "gradient_clip_norm": 10.0,
            "thickness_window_nm": 150.0, "quantization_nm": 1.0,
        },
    }
    return OpticalDesignTask(
        task_id="tmm_dev01",
        benchmark_id="DEV01",
        user_request_original="Design a single-layer antireflection coating on glass over 500-600 nm.",
        normalized_request_english="Optimize a constant-index single coating on n=1.52 glass for low mean reflectance from 500 to 600 nm and retain a 1 nm quantized design.",
        experiments=(TMMExperimentSpec(
            experiment_id="dev01_inverse_design", mode=EngineMode.optimize, tmm_task=optimization,
            objectives=(ObjectivePreference(objective_id="mean_R_500_600", metric="mean_reflectance", sense="minimize", region={"wavelength_nm": [500.0, 600.0]}),),
            tags=("inverse_design", "quantization", "antireflection"),
        ),),
    )


def _dev02() -> OpticalDesignTask:
    layers: list[Dict[str, Any]] = []
    for pair in range(8):
        layers.extend([
            _layer("tio2", 68.0, label=f"H{pair + 1}"),
            _layer("sio2", 112.0, label=f"L{pair + 1}"),
        ])
    simulation = _simulation(
        layers, start_nm=450.0, stop_nm=900.0, points=301,
        angles=[0.0, 30.0, 60.0], polarizations=["s", "p"], name="eight_pair_dbr",
    )
    return OpticalDesignTask(
        task_id="tmm_dev02", benchmark_id="DEV02",
        user_request_original="Analyze an eight-pair TiO2/SiO2 DBR at several angles and polarizations.",
        normalized_request_english="Compute dispersive reflection, transmission, and absorption of an eight-pair TiO2/SiO2 DBR from 450 to 900 nm at 0, 30, and 60 degrees for s and p polarizations.",
        experiments=(TMMExperimentSpec(
            experiment_id="dev02_forward_dbr", mode=EngineMode.simulate, tmm_task=simulation,
            objectives=(ObjectivePreference(objective_id="report_stopband", metric="reflectance_stopband", sense="report", region={"wavelength_nm": [450.0, 900.0]}),),
            tags=("dbr", "angle_sweep", "polarization_splitting"),
        ),),
    )


def _dev03() -> OpticalDesignTask:
    high = lambda label: _layer("tio2", 172.0, label=label)
    low = lambda label: _layer("sio2", 269.0, label=label)
    left: list[Dict[str, Any]] = []
    right: list[Dict[str, Any]] = []
    for pair in range(4):
        left.extend([high(f"LH{pair + 1}"), low(f"LL{pair + 1}")])
        right.extend([low(f"RL{pair + 1}"), high(f"RH{pair + 1}")])
    layers = left + [_layer("sio2", 538.0, label="half_wave_defect")] + right
    simulation = _simulation(
        layers, start_nm=1200.0, stop_nm=1800.0, points=1201,
        polarizations=["s"], requested_outputs=["R", "T", "A", "amplitudes", "phase_dispersion"],
        name="defect_cavity_1550",
    )
    return OpticalDesignTask(
        task_id="tmm_dev03", benchmark_id="DEV03",
        user_request_original="Analyze a 1550 nm TiO2/SiO2 defect cavity including phase and Q.",
        normalized_request_english="Resolve the resonance and phase dispersion of a TiO2/SiO2 defect cavity on a high-resolution 1200 to 1800 nm grid, including group delay and group-delay dispersion.",
        experiments=(TMMExperimentSpec(
            experiment_id="dev03_resonance", mode=EngineMode.simulate, tmm_task=simulation,
            objectives=(ObjectivePreference(objective_id="report_resonance", metric="resonance_q_phase", sense="report", region={"wavelength_nm": [1200.0, 1800.0]}),),
            tags=("defect_cavity", "resonance", "phase_dispersion", "convergence"),
        ),),
    )


def _dev04() -> OpticalDesignTask:
    simulation = _simulation(
        [
            _layer("ti", 12.0, optimizable=True, lo=4.0, hi=40.0, label="top_ti"),
            _layer("sio2", 120.0, optimizable=True, lo=30.0, hi=350.0, label="spacer"),
            _layer("ti", 20.0, optimizable=True, lo=5.0, hi=80.0, label="bottom_ti"),
        ],
        start_nm=400.0, stop_nm=1200.0, points=161,
        exit_medium=_medium(material="al"),
        requested_outputs=["R", "T", "A", "layer_absorption", "system_emissivity"],
        name="selective_absorber_on_al",
    )
    optimization = {
        "simulation": simulation,
        "targets": [
            {"observable": "A", "target": 0.8, "wavelength_min_nm": 450.0, "wavelength_max_nm": 850.0, "weight": 2.0, "angle_deg": 0.0, "polarization": "unpolarized", "constraint": "at_least", "aggregation": "mean", "name": "visible_absorption"},
            {"observable": "A", "target": 0.2, "wavelength_min_nm": 1000.0, "wavelength_max_nm": 1200.0, "weight": 1.0, "angle_deg": 0.0, "polarization": "unpolarized", "constraint": "at_most", "aggregation": "mean", "name": "nir_selectivity"},
        ],
        "optimizer": {"method": "adam_lbfgs", "max_steps": 45, "learning_rate": 0.04, "starts": 3, "seed": 1704, "early_stop_patience": 12, "improvement_tolerance": 1e-7, "gradient_clip_norm": 10.0, "thickness_window_nm": 100.0, "quantization_nm": 1.0},
    }
    return OpticalDesignTask(
        task_id="tmm_dev04", benchmark_id="DEV04",
        user_request_original="Design Ti/SiO2/Ti on Al as a selective absorber over 400-1200 nm.",
        normalized_request_english="Optimize the thicknesses of a lossy Ti/SiO2/Ti stack on aluminium for a soft visible-absorption and near-infrared-selectivity tradeoff from 400 to 1200 nm.",
        experiments=(TMMExperimentSpec(
            experiment_id="dev04_absorber", mode=EngineMode.optimize, tmm_task=optimization,
            objectives=(
                ObjectivePreference(objective_id="visible_A", metric="mean_absorption", sense="maximize", region={"wavelength_nm": [450.0, 850.0]}, weight=2.0),
                ObjectivePreference(objective_id="nir_A", metric="mean_absorption", sense="minimize", region={"wavelength_nm": [1000.0, 1200.0]}),
            ),
            tags=("lossy", "layer_absorption", "emissivity", "inverse_design"),
        ),),
    )


def _dev05() -> OpticalDesignTask:
    simulation = _simulation(
        [
            _layer("mgf2", 105.0, label="front_mgf2"),
            _layer("tio2", 62.0, label="front_tio2"),
            _layer(None, 1_000_000.0, n=1.52, coherence="incoherent", label="glass_substrate"),
        ],
        start_nm=400.0, stop_nm=800.0, points=161,
        exit_medium=_medium(n=1.0), solver="byrnes", name="coating_on_finite_glass",
    )
    return OpticalDesignTask(
        task_id="tmm_dev05", benchmark_id="DEV05",
        user_request_original="Analyze MgF2/TiO2 on a 1 mm incoherent glass plate with air exit.",
        normalized_request_english="Compute mixed-coherence reflection, transmission, and absorption of an MgF2/TiO2 coating on a one-millimetre incoherent glass substrate with air exit from 400 to 800 nm.",
        experiments=(TMMExperimentSpec(
            experiment_id="dev05_mixed_coherence", mode=EngineMode.simulate, tmm_task=simulation,
            objectives=(ObjectivePreference(objective_id="report_mixed_rta", metric="mixed_coherence_RTA", sense="report", region={"wavelength_nm": [400.0, 800.0]}),),
            tags=("mixed_coherence", "finite_substrate", "forward_analysis"),
        ),),
    )


_BUILDERS = {"DEV01": _dev01, "DEV02": _dev02, "DEV03": _dev03, "DEV04": _dev04, "DEV05": _dev05}


def build_dev_optical_design_task(benchmark_id: str) -> OpticalDesignTask:
    """Build one executable development task; holdout IDs are never accepted."""

    try:
        return _BUILDERS[str(benchmark_id).upper()]()
    except KeyError as exc:
        raise KeyError("Only frozen development benchmarks DEV01--DEV05 are executable here") from exc


__all__ = ["build_dev_optical_design_task"]
