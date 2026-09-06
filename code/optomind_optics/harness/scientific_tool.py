"""O-06: ScientificTool protocol + capability registry.

VeriTMM becomes the first *replaceable provider* behind a uniform protocol
instead of a hardcoded dependency. The protocol is a typing.Protocol --
structural, no inheritance required -- so future FDTD/RCWA/literature/
measurement tools register side by side. This module only adds the shell:
VeriTMMAdapter's internals are untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Protocol, runtime_checkable

from tmm_engine.hashing import stable_sha256


class EffectClass(str, Enum):
    """Conservative effect labelling: anything that runs the engine is
    EXPENSIVE_COMPUTE. The two physical classes are reserved, not granted."""

    READ_ONLY = "READ_ONLY"
    COMPUTATIONAL = "COMPUTATIONAL"
    EXPENSIVE_COMPUTE = "EXPENSIVE_COMPUTE"
    EXTERNAL_EXPERIMENT = "EXTERNAL_EXPERIMENT"  # reserved
    PHYSICAL_ACTUATION = "PHYSICAL_ACTUATION"    # reserved


@dataclass(frozen=True)
class ToolDescriptor:
    tool_id: str
    capability_id: str
    contract_version: str
    effect_class: EffectClass
    deterministic: bool
    implementation_fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "capability_id": self.capability_id,
            "contract_version": self.contract_version,
            "effect_class": self.effect_class.value,
            "deterministic": self.deterministic,
            "implementation_fingerprint": self.implementation_fingerprint,
        }


@runtime_checkable
class ScientificTool(Protocol):
    """The five-method seam. Implementations may be synchronous today;
    status/cancel honestly declare what the current backend cannot do."""

    def descriptor(self) -> ToolDescriptor: ...

    def preflight(self, intent: Mapping[str, Any]) -> Dict[str, Any]: ...

    def run(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        """request: {task_spec, output_dir}; returns a RunHandle dict."""

    def status(self, handle: Mapping[str, Any]) -> Dict[str, Any]: ...

    def cancel(self, handle: Mapping[str, Any]) -> Dict[str, Any]: ...

    def replay(self, run_id: str) -> Dict[str, Any]: ...


# ---------------------------------------------------------------------------
# VeriTMM shell over the existing adapter
# ---------------------------------------------------------------------------


class VeriTMMTool:
    """Wraps VeriTMMAdapter behind the ScientificTool protocol.

    - preflight: cheap dry-run -- engine importability, adapter seam, and
      task-payload shape. No solver execution.
    - run: adapter.run_simulation (the one physics door), returns a handle
      carrying the result summary and the certificate path.
    - status/cancel: honest {supported: false} under the synchronous
      implementation; the seam precedes the capability.
    - replay: re-run the engine's own verify-run over the recorded output
      directory (four-state report), never a solver re-execution.
    """

    def __init__(self, adapter: Any, engine_version: str = "") -> None:
        from optomind_optics.harness.runtime_fingerprint import (
            build_runtime_fingerprint,
        )

        self._adapter = adapter
        fingerprint_payload = {
            "engine": "veritmm",
            "engine_version": str(engine_version),
            "runtime": build_runtime_fingerprint(),
        }
        self._descriptor = ToolDescriptor(
            tool_id="veritmm-adapter",
            capability_id="tmm.simulate_optimize",
            contract_version="scientific-tool.v1",
            effect_class=EffectClass.EXPENSIVE_COMPUTE,
            deterministic=True,
            implementation_fingerprint=stable_sha256(fingerprint_payload),
        )

    def descriptor(self) -> ToolDescriptor:
        return self._descriptor

    def preflight(self, intent: Mapping[str, Any]) -> Dict[str, Any]:
        checks: Dict[str, Any] = {
            "tool_id": self._descriptor.tool_id,
            "capability_id": self._descriptor.capability_id,
            "engine_importable": False,
            "task_payload_present": bool(intent.get("task") or intent.get("task_spec")),
        }
        try:
            from optomind_optics.harness.veritmm_adapter import (
                _ensure_real_veritmm_import,
            )

            _ensure_real_veritmm_import()
            import tmm_engine

            checks["engine_importable"] = True
            checks["engine_version"] = str(getattr(tmm_engine, "__version__", ""))
        except Exception as exc:
            checks["engine_error"] = f"{type(exc).__name__}: {exc}"
        checks["ok"] = bool(checks["engine_importable"] and checks["task_payload_present"])
        return checks

    def run(self, request: Mapping[str, Any]) -> Dict[str, Any]:
        output_dir = Path(request["output_dir"])
        result = self._adapter.run_simulation(
            dict(request.get("task_spec") or {}), output_dir
        )
        return {
            "handle_id": f"veritmm:{output_dir.name}",
            "tool_id": self._descriptor.tool_id,
            "status": "completed",
            "certified": bool(result.certified),
            "outcome": str(result.outcome),
            "tightest_margin": float(result.tightest_margin),
            "verification": result.verification,
            "certificate_path": str(result.certificate_path),
            "cpu_seconds": float(result.cpu_seconds),
            "output_dir": str(output_dir),
        }

    def status(self, handle: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "supported": False,
            "reason": "synchronous backend: runs complete inside run()",
            "handle_id": str(handle.get("handle_id") or ""),
        }

    def cancel(self, handle: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "supported": False,
            "reason": "synchronous backend: nothing to cancel mid-flight",
            "handle_id": str(handle.get("handle_id") or ""),
        }

    def replay(self, run_id: str) -> Dict[str, Any]:
        """Re-run verify-run over a recorded output directory."""
        output_dir = Path(run_id)
        if not output_dir.is_dir():
            return {"supported": True, "ok": False, "reason": "output_dir missing"}
        try:
            from optomind_optics.harness.veritmm_adapter import (
                _ensure_real_veritmm_import,
            )

            _ensure_real_veritmm_import()
            from tmm_engine.verify_run import verify_run_dir

            report = dict(verify_run_dir(output_dir))
            return {
                "supported": True,
                "ok": str(report.get("overall_status") or "") == "valid",
                "report": report,
            }
        except Exception as exc:
            return {
                "supported": True,
                "ok": False,
                "reason": f"{type(exc).__name__}: {exc}",
            }
