"""Bounded JSON evaluator process transport, not an adversarial OS sandbox.

Same-UID processes can inspect one another and read permitted host files. Production
still needs an externally enforced filesystem/principal boundary. No audit hook or
caller boolean is treated as evidence of that isolation.
"""
from dataclasses import asdict
import hashlib
import json
import math
import os
import secrets
from pathlib import Path
import subprocess
import sys
import threading

MAX_LINE = 1_000_000
PURCHASE = {"operation", "task_id", "request_id", "channel", "wavelengths_nm"}
TERMINAL = {"operation", "task_id", "submission_id", "selected_design"}


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("object request required")
    operation = request.get("operation")
    if set(request) != (PURCHASE if operation == "purchase" else TERMINAL if operation == "terminal" else set()):
        raise PermissionError("unsupported operation or request fields")
    if not isinstance(request["task_id"], str) or not request["task_id"]:
        raise ValueError("nonempty task ID required")
    if operation == "purchase":
        grid = request["wavelengths_nm"]
        if not isinstance(grid, list) or not 2 <= len(grid) <= 4096:
            raise ValueError("bounded grid required")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in grid):
            raise ValueError("finite numeric grid required")
        if any(a >= b for a, b in zip(grid, grid[1:])):
            raise ValueError("increasing grid required")
    else:
        design = request["selected_design"]
        if not isinstance(design, dict) or set(design) != {"film_n", "thickness_nm"}:
            raise ValueError("declared terminal design only")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in design.values()):
            raise ValueError("finite terminal design required")
        if design["film_n"] <= 0 or design["thickness_nm"] < 0:
            raise ValueError("invalid terminal design")


def _export_response(request, response):
    from optomind_optics.harness.science.world_client import MeasurementReceipt
    if "error" in response:
        # Do not return evaluator tracebacks, paths, hidden values, or exception text.
        return {"error": "evaluator request rejected"}
    expected = set(MeasurementReceipt.__dataclass_fields__) if request["operation"] == "purchase" else {"submission_id", "status", "sealed_digest"}
    if set(response) != expected:
        raise RuntimeError("unexpected evaluator response fields")
    json.dumps(response, allow_nan=False)
    return response


def serve(config_path):
    from .worlds import WorldManifest
    from .service import MeasurementService
    from optomind_optics.harness.science.world_client import TaskManifest
    config = json.loads(Path(config_path).read_text())
    worlds = []
    for row in config["worlds"]:
        row = dict(row)
        row["task"] = TaskManifest(**row["task"])
        row["provenance"] = tuple(tuple(x) for x in row.get("provenance", ()))
        worlds.append(WorldManifest(**row))
    from .world_audits import audit_physical_splits
    audit_physical_splits(worlds)
    service = MeasurementService(worlds, budget=config["budget"])
    # Default raw group IDs contain seed lineage. Opaque per-service tokens retain
    # shared calibration identity without exporting hidden lineage or a guessable hash.
    group_tokens = {w.task.group_id: "group:" + secrets.token_hex(16) for w in worlds}
    calibration_tokens = {group + ":instrument": token + ":instrument" for group, token in group_tokens.items()}
    public_tasks = []
    for world in worlds:
        task = asdict(world.task)
        task["group_id"] = group_tokens[world.task.group_id]
        public_tasks.append(task)
    print(json.dumps({"ready": True, "pid": os.getpid(), "uid": os.getuid(), "tasks": public_tasks}), flush=True)
    while True:
        line = sys.stdin.buffer.readline(MAX_LINE + 1)
        if not line:
            return
        if len(line) > MAX_LINE:
            return  # close oversized stream rather than interpret a suffix
        try:
            request = json.loads(line)
            validate_request(request)
            response = _export_response(request, service.handle(request))
            if "calibration_group" in response:
                response = dict(response)
                response["calibration_group"] = calibration_tokens[response["calibration_group"]]
        except Exception:
            response = {"error": "evaluator request rejected"}
        print(json.dumps(response, allow_nan=False), flush=True)


class EvaluatorProcess:
    """Trusted deployment launcher; give policy only request transport, not this object.

    Launcher and evaluator share a UID in this offline facility. Config is a hidden
    evaluator input by API convention, not an OS-denied file.
    """
    def __init__(self, config_path, *, python=sys.executable):
        code = Path(__file__).resolve().parents[1]
        bootstrap = "import sys;sys.path[:0]=sys.argv[1:3];from optomind_eval.process_boundary import serve;serve(sys.argv[3])"
        self._process = subprocess.Popen(
            [str(python), "-I", "-c", bootstrap, str(code), str(code.parent / "veritmm"), str(Path(config_path).resolve())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            env={k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL")})
        self._lock = threading.Lock()
        try:
            self.handshake = json.loads(self._process.stdout.readline())
        except Exception as exc:
            self.close()
            raise RuntimeError("evaluator startup failed; check trusted deployment dependencies") from exc
        if set(self.handshake) != {"ready", "pid", "uid", "tasks"} or not self.handshake.get("ready") or self.handshake["pid"] == os.getpid():
            self.close()
            raise RuntimeError("independent evaluator process did not start")

    def request(self, request):
        validate_request(request)
        line = json.dumps(request, allow_nan=False)
        if len(line.encode()) >= MAX_LINE:
            raise ValueError("request too large")
        with self._lock:
            self._process.stdin.write(line + "\n")
            self._process.stdin.flush()
            response_line = self._process.stdout.readline(MAX_LINE + 1)
            if not response_line or len(response_line) > MAX_LINE:
                raise RuntimeError("evaluator process unavailable")
            return _export_response(request, json.loads(response_line))

    def close(self):
        process = getattr(self, "_process", None)
        if process is not None:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def export_policy_capsule(directory):
    """Export the transport-only existing client, without evaluator package/data."""
    from optomind_optics.harness.science import world_client
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=False)
    data = Path(world_client.__file__).read_bytes()
    (target / "world_client.py").write_bytes(data)
    manifest = {"files": {"world_client.py": hashlib.sha256(data).hexdigest()},
                "boundary": "package export only", "os_isolation": "not established",
                "excluded": ["optomind_eval", "hidden worlds", "seeds", "sealed scores"],
                "note": "receipt_to_observation requires full scientific policy package; purchase client is standalone"}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest
