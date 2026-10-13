from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Keep this one-off harness self-contained and make the numerical threading
# policy explicit before importing NumPy/Torch.  No package installation or
# source-tree writes are performed by this file.
OUT = Path(__file__).resolve().parent
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MPLCONFIGDIR", str(OUT / "_mplconfig"))

import numpy as np

REPO = Path(r"D:\OptoMind-Article-3\repos\VeriTMM")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tmm_engine.backends import KernelSpec  # noqa: E402
from tmm_engine.backends import numpy_backend  # noqa: E402
from tmm_engine.backends import torch_backend  # noqa: E402

try:  # noqa: E402
    import torch
except Exception as exc:  # pragma: no cover - recorded as a run failure
    torch = None
    TORCH_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
else:
    TORCH_IMPORT_ERROR = None


RUN_ID = "B3-backend-pairs-20261013-01"
THRESHOLD = 1e-8
WAVELENGTHS_NM = np.linspace(450.0, 700.0, 21, dtype=np.float64)

# These are the five frozen task definitions in PRE_EXECUTION_SPEC.md.  A
# task's media values are expanded to arrays only once, then the same
# KernelSpec object is sent to both backend calls.
TASK_DEFS = (
    {
        "task_id": "E09-B3-01",
        "film_n": [2.15],
        "thicknesses_nm": [96.0],
        "incident_n": 1.0,
        "exit_n": 1.52,
        "angle_deg": 0.0,
        "polarization": "unpolarized",
    },
    {
        "task_id": "E09-B3-02",
        "film_n": [1.80, 2.30],
        "thicknesses_nm": [82.0, 133.0],
        "incident_n": 1.0,
        "exit_n": 1.45,
        "angle_deg": 23.0,
        "polarization": "s",
    },
    {
        "task_id": "E09-B3-03",
        "film_n": [1.65 + 0.010j, 2.10 + 0.020j, 1.38 + 0.005j],
        "thicknesses_nm": [57.0, 111.0, 74.0],
        "incident_n": 1.0,
        "exit_n": 1.55 + 0.003j,
        "angle_deg": 37.0,
        "polarization": "p",
    },
    {
        "task_id": "E09-B3-04",
        "film_n": [2.05, 1.34, 1.78, 2.25],
        "thicknesses_nm": [48.0, 92.0, 66.0, 121.0],
        "incident_n": 1.0,
        "exit_n": 1.50,
        "angle_deg": 49.0,
        "polarization": "unpolarized",
    },
    {
        "task_id": "E09-B3-05",
        "film_n": [1.75 + 0.015j],
        "thicknesses_nm": [143.0],
        "incident_n": 1.0,
        "exit_n": 1.62 + 0.004j,
        "angle_deg": 16.0,
        "polarization": "p",
    },
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def complex_json(value: complex) -> dict[str, float]:
    value = complex(value)
    return {"real": float(value.real), "imag": float(value.imag)}


def complex_array_json(values: Any) -> list[list[dict[str, float]]]:
    array = np.asarray(values, dtype=np.complex128)
    return [
        [complex_json(value) for value in row]
        for row in array
    ]


def scalar_json(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, complex):
        return complex_json(value)
    return value


def make_kernel(task_def: dict[str, Any]) -> KernelSpec:
    media = [task_def["incident_n"], *task_def["film_n"], task_def["exit_n"]]
    nk_stack = np.stack(
        [
            np.full(WAVELENGTHS_NM.shape, complex(value), dtype=np.complex128)
            for value in media
        ],
        axis=0,
    )
    return KernelSpec(
        nk_stack=nk_stack,
        thicknesses_nm=np.asarray(task_def["thicknesses_nm"], dtype=np.float64),
        wavelengths_nm=WAVELENGTHS_NM,
        angle_deg=float(task_def["angle_deg"]),
        polarization=str(task_def["polarization"]),
        assembly="smatrix",
    )


def task_input_json(task_def: dict[str, Any], kernel: KernelSpec) -> dict[str, Any]:
    return {
        "task_id": task_def["task_id"],
        "angle_deg": float(kernel.angle_deg),
        "polarization": kernel.polarization,
        "assembly": kernel.assembly,
        "wavelengths_nm": [float(value) for value in np.asarray(kernel.wavelengths_nm)],
        "thicknesses_nm": [float(value) for value in np.asarray(kernel.thicknesses_nm)],
        "nk_stack": complex_array_json(kernel.nk_stack),
    }


def as_spectrum(value: Any, wavelength_count: int) -> np.ndarray:
    if torch is not None and isinstance(value, torch.Tensor):
        value = value.detach().cpu().numpy()
    array = np.asarray(value, dtype=np.float64)
    if array.ndim == 2 and array.shape[0] == 1:
        array = array[0]
    array = array.reshape(-1)
    if array.shape != (wavelength_count,):
        raise ValueError(f"spectrum shape {array.shape} does not match {(wavelength_count,)}")
    return array


def evaluate_result(result: Any, wavelength_count: int) -> tuple[dict[str, list[float]], dict[str, float]]:
    r = as_spectrum(result.R, wavelength_count)
    t = as_spectrum(result.T, wavelength_count)
    a_eval = 1.0 - r - t
    a_reported = as_spectrum(result.A, wavelength_count)
    residual = np.abs(a_reported - a_eval)
    spectra = {
        "R": [float(value) for value in r],
        "T": [float(value) for value in t],
        "A_eval": [float(value) for value in a_eval],
        "A_reported": [float(value) for value in a_reported],
    }
    audit = {
        "max_abs_A_reported_minus_A_eval": float(np.max(residual)),
        "min_R": float(np.min(r)),
        "max_R": float(np.max(r)),
        "min_T": float(np.min(t)),
        "max_T": float(np.max(t)),
        "min_A_eval": float(np.min(a_eval)),
        "max_A_eval": float(np.max(a_eval)),
    }
    return spectra, audit


def backend_device(result: Any) -> str:
    value = result.R
    if torch is not None and isinstance(value, torch.Tensor):
        return str(value.device)
    return "numpy-cpu"


def run_backend(name: str, kernel: KernelSpec) -> dict[str, Any]:
    expected_internal_pol_calls = 2 if kernel.polarization == "unpolarized" else 1
    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    record: dict[str, Any] = {
        "backend": name,
        "status": "error",
        "assemble_call_count": 1,
        "evaluator_call_count": 0,
        "expected_internal_polarization_calls": expected_internal_pol_calls,
        "exception_type": None,
        "exception_message": None,
        "device": "numpy-cpu" if name == "numpy" else "unknown",
        "wall_seconds": None,
        "cpu_seconds": None,
        "spectra": None,
        "audit": None,
    }
    try:
        if name == "numpy":
            result = numpy_backend.assemble(kernel)
        elif name == "torch_cpu":
            if torch is None:
                raise RuntimeError(f"torch import failed: {TORCH_IMPORT_ERROR}")
            result = torch_backend.assemble(kernel, dtype="float64")
            record["device"] = backend_device(result)
        else:  # pragma: no cover
            raise ValueError(name)
        evaluated, audit = evaluate_result(result, len(WAVELENGTHS_NM))
        record["status"] = "ok"
        record["evaluator_call_count"] = 1
        record["spectra"] = evaluated
        record["audit"] = audit
    except Exception as exc:  # evidence is retained per pair
        record["exception_type"] = type(exc).__name__
        record["exception_message"] = str(exc)
    finally:
        record["wall_seconds"] = float(time.perf_counter() - start_wall)
        record["cpu_seconds"] = float(time.process_time() - start_cpu)
    return record


def compare_pair(numpy_record: dict[str, Any], torch_record: dict[str, Any]) -> dict[str, Any]:
    comparison: dict[str, Any] = {
        "status": "error",
        "max_abs_delta": None,
        "max_abs_delta_R": None,
        "max_abs_delta_T": None,
        "max_abs_delta_A": None,
        "threshold": THRESHOLD,
        "threshold_pass": False,
        "per_wavelength": None,
    }
    if numpy_record["status"] != "ok" or torch_record["status"] != "ok":
        return comparison
    np_spectra = numpy_record["spectra"]
    torch_spectra = torch_record["spectra"]
    rows = []
    channel_maxima = {}
    for index, wavelength in enumerate(WAVELENGTHS_NM):
        row: dict[str, Any] = {"wavelength_nm": float(wavelength)}
        for channel in ("R", "T", "A_eval"):
            numpy_value = float(np_spectra[channel][index])
            torch_value = float(torch_spectra[channel][index])
            delta = abs(numpy_value - torch_value)
            row[f"numpy_{channel}"] = numpy_value
            row[f"torch_{channel}"] = torch_value
            row[f"delta_{channel}"] = delta
            channel_maxima[channel] = max(channel_maxima.get(channel, 0.0), delta)
        rows.append(row)
    max_delta = max(channel_maxima.values())
    comparison.update(
        {
            "status": "ok",
            "max_abs_delta": float(max_delta),
            "max_abs_delta_R": float(channel_maxima["R"]),
            "max_abs_delta_T": float(channel_maxima["T"]),
            "max_abs_delta_A": float(channel_maxima["A_eval"]),
            "threshold_pass": bool(max_delta <= THRESHOLD),
            "per_wavelength": rows,
        }
    )
    return comparison


def git_value(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=REPO, text=True, stderr=subprocess.STDOUT
        ).strip()
    except Exception:
        return None


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def build_plot(tasks: list[dict[str, Any]]) -> str:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # plot absence is recorded, not hidden
        return f"plot unavailable: {type(exc).__name__}: {exc}"

    labels = [task["task_id"] for task in tasks]
    values = [task["comparison"]["max_abs_delta"] or np.nan for task in tasks]
    floor = 1e-18
    plotted = [max(float(value), floor) if np.isfinite(value) else np.nan for value in values]
    figure, axis = plt.subplots(figsize=(8.5, 4.8), constrained_layout=True)
    axis.bar(labels, plotted, color="#2f6f9f")
    axis.axhline(THRESHOLD, color="#a33b2b", linestyle="--", linewidth=1.2, label="threshold 1e-8")
    axis.set_yscale("log")
    axis.set_ylabel("max |NumPy - Torch| (R/T/A)")
    axis.set_xlabel("fixed planar TMM task")
    axis.set_title("B3/E09 synthetic forward-computed backend comparability")
    axis.tick_params(axis="x", rotation=20)
    axis.legend(loc="upper right")
    axis.text(
        0.01,
        0.01,
        "synthetic forward-computed; backend numerical comparison only; model count = 0",
        transform=axis.transAxes,
        fontsize=8,
        color="#444444",
    )
    figure.savefig(OUT / "backend_pair_max_abs_delta.svg", format="svg")
    figure.savefig(OUT / "backend_pair_max_abs_delta.png", format="png", dpi=180)
    plt.close(figure)
    return "created SVG and PNG"


def main() -> int:
    start = time.perf_counter()
    if torch is not None:
        torch.set_num_threads(1)
        try:
            torch.set_num_interop_threads(1)
        except RuntimeError:
            pass

    source_files = {
        "registry.py": REPO / "tmm_engine" / "backends" / "registry.py",
        "numpy_backend.py": REPO / "tmm_engine" / "backends" / "numpy_backend.py",
        "torch_backend.py": REPO / "tmm_engine" / "backends" / "torch_backend.py",
        "tmm_solver.py": REPO / "tmm_engine" / "tmm_solver.py",
        "differentiable.py": REPO / "tmm_engine" / "differentiable.py",
    }
    spec_path = OUT / "PRE_EXECUTION_SPEC.md"
    kernels: list[tuple[dict[str, Any], KernelSpec]] = [
        (task_def, make_kernel(task_def)) for task_def in TASK_DEFS
    ]
    task_records: list[dict[str, Any]] = []
    for task_def, kernel in kernels:
        numpy_record = run_backend("numpy", kernel)
        torch_record = run_backend("torch_cpu", kernel)
        task_records.append(
            {
                "task_id": task_def["task_id"],
                "input": task_input_json(task_def, kernel),
                "kernel_object_reused_for_pair": True,
                "backend_runs": [numpy_record, torch_record],
                "comparison": compare_pair(numpy_record, torch_record),
            }
        )

    successful_comparisons = [
        task["comparison"]
        for task in task_records
        if task["comparison"]["status"] == "ok"
    ]
    all_deltas = [
        channel_key
        for comparison in successful_comparisons
        for channel_key in (
            comparison["max_abs_delta_R"],
            comparison["max_abs_delta_T"],
            comparison["max_abs_delta_A"],
        )
    ]
    max_abs_delta = max(all_deltas) if all_deltas else None
    backend_runs = [run for task in task_records for run in task["backend_runs"]]
    exception_count = sum(run["status"] != "ok" for run in backend_runs)
    pair_pass = bool(
        len(successful_comparisons) == len(TASK_DEFS)
        and max_abs_delta is not None
        and max_abs_delta <= THRESHOLD
    )
    call_count_pass = all(
        run["assemble_call_count"] == 1 and run["evaluator_call_count"] == 1
        for run in backend_runs
        if run["status"] == "ok"
    ) and len(backend_runs) == 10
    exception_pass = exception_count == 0
    result_status = "PASS" if pair_pass and call_count_pass and exception_pass else "FAIL"
    total_wall = float(time.perf_counter() - start)

    source_hashes = {
        name: sha256_file(path) if path.exists() else None
        for name, path in source_files.items()
    }
    manifest = {
        "run_id": RUN_ID,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": result_status,
        "purpose": "B3/E09 baseline numerical backend comparability only",
        "synthetic_forward_computed": True,
        "physical_model_count": 0,
        "real_measurement": False,
        "manufacturing_evidence": False,
        "ai_autonomous_design": False,
        "traditional_tool_vs_ai_coupling_comparison": False,
        "e09_30_percent_benefit_claim": False,
        "cost": {"amount": 0, "currency": "unspecified", "basis": "local execution"},
        "network_or_api": False,
        "rcwa_or_fdfd": False,
        "hardware": False,
        "source_repo": str(REPO),
        "execution_command": (
            f"{sys.executable} -B -X utf8 {Path(__file__)}"
        ),
        "return_code": 0 if result_status == "PASS" else 1,
        "source_commit_expected": "dd9f27bf7b3f8150fdb37dc65f169ef6aab333f1",
        "source_commit_observed": git_value("rev-parse", "HEAD"),
        "source_status_observed": git_value("status", "--short"),
        "source_files_sha256": source_hashes,
        "harness_sha256": sha256_file(Path(__file__)),
        "spec_sha256": sha256_file(spec_path),
        "interpreter": {
            "path": sys.executable,
            "version": sys.version,
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
        },
        "imports": {
            "tmm_engine": str(Path(__import__("tmm_engine").__file__).resolve()),
            "numpy_backend": str(Path(numpy_backend.__file__).resolve()),
            "torch_backend": str(Path(torch_backend.__file__).resolve()),
            "numpy_version": np.__version__,
            "torch_version": None if torch is None else torch.__version__,
            "torch_import_error": TORCH_IMPORT_ERROR,
        },
        "execution_policy": {
            "cpu_only": True,
            "torch_dtype": "float64 / complex128",
            "torch_device_observed": sorted({run["device"] for run in backend_runs}),
            "omp_num_threads": os.environ.get("OMP_NUM_THREADS"),
            "mkl_num_threads": os.environ.get("MKL_NUM_THREADS"),
            "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS"),
            "torch_num_threads": None if torch is None else torch.get_num_threads(),
            "torch_num_interop_threads": None if torch is None else torch.get_num_interop_threads(),
        },
        "resources": {
            "wall_seconds_total": total_wall,
            "backend_wall_seconds_total": float(sum(run["wall_seconds"] or 0.0 for run in backend_runs)),
            "backend_cpu_seconds_total": float(sum(run["cpu_seconds"] or 0.0 for run in backend_runs)),
        },
        "thresholds": {
            "max_abs_delta_R_T_A": THRESHOLD,
            "comparison_dtype": "float64",
        },
        "primary_metrics": {
            "max_abs_delta_R_T_A": max_abs_delta,
            "backend_assembly_call_count_total": sum(run["assemble_call_count"] for run in backend_runs),
            "exception_count": exception_count,
        },
        "checks": {
            "numeric_threshold_pass": pair_pass,
            "call_count_pass": call_count_pass,
            "exception_free_pass": exception_pass,
        },
        "tasks": task_records,
    }
    write_json(OUT / "raw_results.json", manifest)

    csv_fields = [
        "task_id",
        "wavelength_nm",
        "numpy_R",
        "torch_R",
        "delta_R",
        "numpy_T",
        "torch_T",
        "delta_T",
        "numpy_A_eval",
        "torch_A_eval",
        "delta_A_eval",
    ]
    with (OUT / "raw_results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for task in task_records:
            for row in task["comparison"].get("per_wavelength") or []:
                writer.writerow({"task_id": task["task_id"], **row})

    plot_status = build_plot(task_records)
    mpl_config = Path(os.environ.get("MPLCONFIGDIR", ""))
    if mpl_config.is_dir() and mpl_config.resolve() == (OUT / "_mplconfig").resolve():
        shutil.rmtree(mpl_config)

    result_lines = [
        "# B3 / E09 后端一对一结果",
        "",
        f"- run_id: `{RUN_ID}`",
        f"- status: **{result_status}**",
        "- 标记：`synthetic forward-computed`；物理模型数 `0`；实测/制造/AI 自主设计证据均为 `false`。",
        "- 实验含义：仅比较同一 `KernelSpec` 的 NumPy 与 Torch CPU 数值后端；不等于传统工具 vs AI 友好耦合，也不证明 E09 的 `>=30%` 收益。",
        "",
        "## 三个主指标",
        "",
        f"1. R/T/A 最大绝对差：`{max_abs_delta!r}`，阈值 `<= {THRESHOLD:g}`，结果 `{'PASS' if pair_pass else 'FAIL'}`。",
        f"2. 顶层 assemble 调用总数：`{sum(run['assemble_call_count'] for run in backend_runs)}`（每后端每任务 1）；公共评价器成功调用数：`{sum(run['evaluator_call_count'] for run in backend_runs)}`。",
        f"3. 异常数：`{exception_count}`；任务对数：`{len(successful_comparisons)}/{len(TASK_DEFS)}`；结果 `{'PASS' if exception_pass else 'FAIL'}`。",
        "",
        "## 每任务证据",
        "",
        "| task_id | polarization | angle_deg | max ΔR | max ΔT | max ΔA | NumPy s | Torch s | pair |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for task in task_records:
        task_def = next(item for item in TASK_DEFS if item["task_id"] == task["task_id"])
        comparison = task["comparison"]
        np_run, torch_run = task["backend_runs"]
        result_lines.append(
            "| {task} | {pol} | {angle:g} | {dr!r} | {dt!r} | {da!r} | {nps:.6g} | {ts:.6g} | {pair} |".format(
                task=task["task_id"],
                pol=task_def["polarization"],
                angle=task_def["angle_deg"],
                dr=comparison["max_abs_delta_R"],
                dt=comparison["max_abs_delta_T"],
                da=comparison["max_abs_delta_A"],
                nps=np_run["wall_seconds"] or float("nan"),
                ts=torch_run["wall_seconds"] or float("nan"),
                pair="PASS" if comparison["threshold_pass"] else "FAIL",
            )
        )
    result_lines.extend(
        [
            "",
            f"- 资源时间：总墙钟 `{total_wall:.9f}` s；后端墙钟合计 `{manifest['resources']['backend_wall_seconds_total']:.9f}` s；费用 `0`。",
            f"- 图：{plot_status}；图题和脚注已标明 `synthetic forward-computed` 与模型数 `0`。",
            "- 详细原始证据见 `raw_results.json` 与 `raw_results.csv`；源码/解释器路径和 SHA256 见 JSON manifest。",
        ]
    )
    (OUT / "RESULT.md").write_text("\n".join(result_lines) + "\n", encoding="utf-8")

    todo_lines = [
        "# B3 / E09 待办",
        "",
        "- [ ] 由主任务汇总本批次与 E09 其他证据；本批次不外推 `>=30%` 收益。",
        "- [ ] 若要声称真实物理拟合或制造可行性，另行取得实测数据、仪器/样品记录和模型校准证据；本批次不提供这些证据。",
        "- [ ] E10 容差与独立优化诊断由 root 任务处理，本批次不重复。",
        "- [ ] 如需跨平台性能研究，另立冻结实验并固定硬件、线程、预热和统计方法；本批次时间只作执行记录。",
    ]
    (OUT / "TODO.md").write_text("\n".join(todo_lines) + "\n", encoding="utf-8")
    return 0 if result_status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
