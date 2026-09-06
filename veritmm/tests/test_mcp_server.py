"""Agent-safe MCP stdio surface tests.

The tool implementations (``McpSurface``) are tested directly without the
MCP SDK so the allowlist, containment, and golden-consistency contracts hold
regardless of the optional dependency.  The stdio handshake smoke test runs
only when the ``mcp`` extra is installed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tmm_engine.hashing import canonical_json_bytes
from tmm_engine.mcp_server import McpSurface, McpToolError, create_server

HAS_MCP = importlib.util.find_spec("mcp") is not None

GOOD_TASK = {
    "simulation": {
        "stack": {
            "layers": [
                {"material": "tio2", "thickness_nm": 60.0},
                {"material": "sio2", "thickness_nm": 90.0},
            ],
            "incident": {"material": None, "constant_n": 1.0},
            "exit": {"material": "sio2"},
        },
        "spectrum": {"start_nm": 500.0, "stop_nm": 600.0, "points": 11},
        "illumination": {"angles_deg": [0.0], "polarizations": ["s"]},
    }
}

EXPECTED_TOOLS = {
    "veritmm_discover",
    "veritmm_catalog_get",
}
EXPECTED_LEGACY_TOOLS = {
    "describe",
    "schema",
    "examples",
    "preflight",
    "run",
    "history",
    "inspect",
    "lineage",
    "compare",
    "verify_run",
}
EXPECTED_NAMESPACED_TOOLS = {
    "veritmm_discover",
    "veritmm_catalog_get",
    "veritmm_material_search",
    "veritmm_material_explain",
    "veritmm_stack_validate",
    "veritmm_simulate_spectrum",
    "veritmm_simulate_batch",
    "veritmm_gradient_compute",
    "veritmm_sensitivity_analyze",
    "veritmm_optimize_problem",
    "veritmm_verify_run",
    "veritmm_verify_explain",
    "veritmm_job_status",
    "veritmm_job_result",
    "veritmm_intent_compile",
}


@pytest.fixture()
def surface(tmp_path: Path) -> McpSurface:
    task_root = tmp_path / "tasks"
    task_root.mkdir()
    (task_root / "good.json").write_text(
        json.dumps({"mode": "simulate", **GOOD_TASK}), encoding="utf-8"
    )
    return McpSurface(artifact_root=tmp_path / "artifacts", task_root=task_root)


def _write_reference_task(task_root: Path, relative: str, outside: Path) -> Path:
    target = task_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(outside)
    except OSError:  # pragma: no cover - Windows without symlink privilege
        pytest.skip("symbolic links are not available on this host")
    return target


# ---- allowlist and parameter rejection ---------------------------------------


def test_schema_rejects_unknown_kind(surface: McpSurface) -> None:
    with pytest.raises(McpToolError):
        surface.schema("rcwa")


def test_detail_is_confined_to_the_response_profiles(surface: McpSurface) -> None:
    with pytest.raises(McpToolError):
        surface.run(GOOD_TASK, detail="raw")


def test_history_limit_is_bounded(surface: McpSurface) -> None:
    with pytest.raises(McpToolError):
        surface.history(limit=100000)


def test_run_id_format_is_enforced(surface: McpSurface) -> None:
    with pytest.raises(McpToolError):
        surface.verify_run("../../etc")


def test_inline_task_requires_the_simulation_object(surface: McpSurface) -> None:
    with pytest.raises(McpToolError):
        surface.run({"optimization": {}})


def test_absolute_and_escaping_task_paths_are_rejected(
    surface: McpSurface, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(GOOD_TASK), encoding="utf-8")
    with pytest.raises(McpToolError):
        surface.run(str(outside))
    with pytest.raises(McpToolError):
        surface.run("../outside.json")
    with pytest.raises(McpToolError):
        surface.run("veritmm://task/../outside.json")


def test_symlinked_task_reference_is_rejected(surface: McpSurface, tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"mode": "simulate", **GOOD_TASK}), encoding="utf-8")
    _write_reference_task(surface.task_root, "link.json", outside)
    with pytest.raises(McpToolError):
        surface.run("link.json")


# ---- execution through managed execution -------------------------------------


def test_run_uses_managed_execution_and_returns_references(
    surface: McpSurface,
) -> None:
    envelope = surface.run(GOOD_TASK)
    assert envelope["ok"] is True
    assert envelope["status"] == "completed"
    kinds = {reference["kind"] for reference in envelope["artifacts"]}
    assert {"physics_certificate", "verification_evidence", "verification_policy"} <= kinds
    assert envelope["task_sha256"] and envelope["certificate_id"]

    artifact_text = surface.read_run_resource(
        envelope["run_id"], "SIMULATION_RESULT.json"
    )
    assert json.loads(artifact_text)["solver"]

    with pytest.raises(McpToolError):
        surface.read_run_resource(envelope["run_id"], "../RUN_MANIFEST.json")


def test_verify_run_tool_reports_valid_for_a_fresh_run(surface: McpSurface) -> None:
    envelope = surface.run(GOOD_TASK)
    report = surface.verify_run(envelope["run_id"])
    assert report["overall_status"] == "valid"
    assert report["replay_status"] == "passed"


def test_preflight_payload_and_rejection(surface: McpSurface) -> None:
    payload = surface.preflight(GOOD_TASK)
    assert payload["ok"] is True

    anisotropic = json.loads(json.dumps(GOOD_TASK))
    anisotropic["simulation"]["physics"] = {"material_class": "anisotropic"}
    rejected = surface.preflight(anisotropic)
    assert rejected["ok"] is False


def test_history_inspect_lineage_round_trip(surface: McpSurface) -> None:
    envelope = surface.run(GOOD_TASK)
    history = surface.history()
    assert any(run["run_id"] == envelope["run_id"] for run in history["runs"])
    inspected = surface.inspect(envelope["run_id"])
    assert inspected["record"]["run_id"] == envelope["run_id"]
    lineage = surface.lineage(envelope["run_id"])
    assert lineage["ok"] is True


# ---- golden consistency: CLI defaults vs MCP surface --------------------------


def test_mcp_surface_matches_cli_defaults(tmp_path: Path) -> None:
    task_file = tmp_path / "task.json"
    task_file.write_text(
        json.dumps({"mode": "simulate", **GOOD_TASK}), encoding="utf-8"
    )
    cli_dir = tmp_path / "cli"
    command = [
        sys.executable,
        "-m",
        "tmm_engine.cli",
        "run",
        str(task_file),
        "--output-dir",
        str(cli_dir),
        "--json",
        "--no-cache",
        "--store-dir",
        str(tmp_path / "cli-store"),
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    if completed.returncode != 0:
        # A fresh CLI process can transiently fail on Windows while newly
        # written files are scanned by external readers; retry the process
        # once.  Only infrastructure errors are retried — a content mismatch
        # below is never retried and always fails the gate.
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=600,
        )
    assert completed.returncode == 0, completed.stderr[-2000:]
    cli_envelope = json.loads(completed.stdout.strip().splitlines()[-1])

    surface = McpSurface(
        artifact_root=tmp_path / "mcp-artifacts", task_root=tmp_path / "mcp-tasks"
    )
    mcp_envelope = surface.run(json.loads(json.dumps(GOOD_TASK)))

    # The two transports must agree on identity, verdict, and science.
    assert mcp_envelope["task_sha256"] == cli_envelope["task_sha256"]
    assert mcp_envelope["certificate_id"] == cli_envelope["certificate_id"]
    assert mcp_envelope["status"] == cli_envelope["status"]
    assert (
        mcp_envelope["summary"]["physics"]["accepted"]
        == cli_envelope["summary"]["physics"]["accepted"]
    )

    def artifact_bytes(run_dir: Path, name: str) -> bytes:
        return canonical_json_bytes(json.loads((run_dir / name).read_text(encoding="utf-8")))

    mcp_run_archive = (
        tmp_path / "mcp-artifacts" / "store" / "runs" / mcp_envelope["run_id"]
    )
    assert (
        artifact_bytes(mcp_run_archive, "SIMULATION_RESULT.json")
        == artifact_bytes(cli_dir, "SIMULATION_RESULT.json")
    )

    cli_summary = {
        key: value
        for key, value in cli_envelope["summary"].items()
        if key not in ("run_id",)
    }
    mcp_summary = {
        key: value
        for key, value in mcp_envelope["summary"].items()
        if key not in ("run_id",)
    }
    assert canonical_json_bytes(mcp_summary) == canonical_json_bytes(cli_summary)


# ---- optional SDK layer -------------------------------------------------------


@pytest.mark.skipif(not HAS_MCP, reason="the optional mcp extra is not installed")
def test_create_server_builds_the_projection() -> None:
    server = create_server(
        artifact_root=Path.cwd() / "tmp-mcp-artifacts",
        task_root=Path.cwd() / "tmp-mcp-tasks",
    )
    assert server is not None

    async def tool_names() -> set[str]:
        listed = await server.list_tools()
        return {tool.name for tool in listed}

    names = asyncio.run(asyncio.wait_for(tool_names(), timeout=60))
    assert EXPECTED_TOOLS <= names


@pytest.mark.skipif(not HAS_MCP, reason="the optional mcp extra is not installed")
def test_stdio_handshake_lists_the_agent_safe_tools(tmp_path: Path) -> None:
    """End-to-end stdio smoke: spawn the server, initialize, list tools."""

    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    artifact_root = tmp_path / "artifacts"
    task_root = tmp_path / "tasks"
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "tmm_engine.mcp_server",
            "--artifact-root",
            str(artifact_root),
            "--task-root",
            str(task_root),
        ],
        cwd=str(Path.cwd()),
        env={
            **os.environ,
            "PYTHONPATH": str(Path.cwd()),
        },
    )

    async def handshake() -> set[str]:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                return {tool.name for tool in listed.tools}

    names = asyncio.run(asyncio.wait_for(handshake(), timeout=120))
    assert EXPECTED_TOOLS <= names


# ---- V-11 namespaced surface ---------------------------------------------------


def test_compact_mode_is_below_fifteen_percent_of_full_schema_bytes() -> None:
    import asyncio
    import json as jsonlib
    import tempfile

    from tmm_engine.mcp_server import create_server

    root = Path(tempfile.mkdtemp())
    compact = create_server(
        artifact_root=root / "c-artifacts", task_root=root / "c-tasks", tools="compact"
    )
    full = create_server(
        artifact_root=root / "f-artifacts", task_root=root / "f-tasks", tools="full"
    )

    def schema_bytes(server):
        tools = asyncio.run(server.list_tools())
        total = sum(
            len(
                jsonlib.dumps(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    },
                    default=str,
                )
            )
            for tool in tools
        )
        return total, sorted(tool.name for tool in tools)

    compact_bytes, compact_names = schema_bytes(compact)
    full_bytes, full_names = schema_bytes(full)
    assert set(compact_names) == EXPECTED_TOOLS
    assert EXPECTED_NAMESPACED_TOOLS | EXPECTED_LEGACY_TOOLS <= set(full_names)
    ratio = compact_bytes / full_bytes
    assert ratio < 0.15, f"compact schema bytes {ratio:.2%} >= 15% of full"


def test_full_mode_keeps_every_legacy_tool_name() -> None:
    import asyncio
    import tempfile

    from tmm_engine.mcp_server import create_server

    root = Path(tempfile.mkdtemp())
    server = create_server(
        artifact_root=root / "artifacts", task_root=root / "tasks", tools="full"
    )

    async def tool_names() -> set:
        return {tool.name for tool in await server.list_tools()}

    names = asyncio.run(asyncio.wait_for(tool_names(), timeout=60))
    assert EXPECTED_LEGACY_TOOLS <= names
    assert EXPECTED_NAMESPACED_TOOLS <= names
    assert len(names) <= 30


def test_every_tool_description_carries_an_effect_annotation() -> None:
    import asyncio
    import tempfile

    from tmm_engine.mcp_server import TOOL_CATALOG, create_server

    for tool in TOOL_CATALOG:
        assert tool["annotation"] in {
            "READ_ONLY",
            "COMPUTATIONAL",
            "EXPENSIVE_COMPUTE",
        }, tool["name"]
    root = Path(tempfile.mkdtemp())
    server = create_server(
        artifact_root=root / "artifacts", task_root=root / "tasks", tools="full"
    )

    async def tools() -> list:
        return await server.list_tools()

    listed = asyncio.run(asyncio.wait_for(tools(), timeout=60))
    annotations = {"READ_ONLY", "COMPUTATIONAL", "EXPENSIVE_COMPUTE"}
    for tool in listed:
        prefixes = tuple("[" + item + "]" for item in sorted(annotations))
        assert tool.description.startswith(prefixes), tool.name


def test_new_tools_introduce_no_operator_parameters() -> None:
    """Allowlist audit: the namespaced signatures stay free of operator and
    debug parameters (device, roots, gates, python escape)."""

    import asyncio
    import tempfile

    from tmm_engine.mcp_server import create_server

    forbidden = {
        "device",
        "output_dir",
        "store_dir",
        "skip_certificate",
        "physics_python",
        "user_metadata_json",
        "convergence_max_refinements",
        "allow_extrapolation",
    }
    root = Path(tempfile.mkdtemp())
    server = create_server(
        artifact_root=root / "artifacts", task_root=root / "tasks", tools="full"
    )

    async def tools() -> list:
        return await server.list_tools()

    listed = asyncio.run(asyncio.wait_for(tools(), timeout=60))
    for tool in listed:
        params = set((tool.input_schema or {}).get("properties", {}))
        assert params & forbidden == set(), (tool.name, params & forbidden)


def test_golden_consistency_gradient_cli_vs_mcp(tmp_path: Path) -> None:
    task_file = tmp_path / "gradient_task.json"
    task_file.write_text(
        json.dumps(
            {
                "mode": "simulate",
                "simulation": {
                    "stack": {
                        "layers": [
                            {"constant_n": 2.4, "thickness_nm": 62.5, "optimizable": True},
                            {"constant_n": 1.46, "thickness_nm": 102.7, "optimizable": True},
                        ],
                        "incident": {"constant_n": 1.0},
                        "exit": {"constant_n": 1.52},
                    },
                    "spectrum": {"start_nm": 500.0, "stop_nm": 700.0, "points": 21},
                    "illumination": {"angles_deg": [0.0], "polarizations": ["s"]},
                    "solver": "smatrix",
                    "requested_outputs": ["R", "T", "A"],
                },
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tmm_engine.cli",
            "gradient",
            str(task_file),
            "--objective",
            "mean_T",
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    cli_payload = json.loads(completed.stdout.strip().splitlines()[-1])

    surface = McpSurface(
        artifact_root=tmp_path / "mcp-artifacts", task_root=tmp_path / "mcp-tasks"
    )
    mcp_payload = surface.gradient_compute(
        json.loads(task_file.read_text(encoding="utf-8")), objective="mean_T"
    )
    assert mcp_payload["ok"] is True
    assert mcp_payload["metric_value"] == cli_payload["metric_value"]
    assert mcp_payload["layer_gradients"] == cli_payload["layer_gradients"]
    assert mcp_payload["top_influential_layers"] == cli_payload["top_influential_layers"]


def test_golden_consistency_optimize_problem_cli_vs_mcp(tmp_path: Path) -> None:
    problem = {
        "schema_version": "veritmm-design-problem-v1",
        "stack": {
            "layers": [
                {"constant_n": 2.4, "thickness_nm": 62.5},
                {"constant_n": 1.46, "thickness_nm": 102.7},
            ],
            "incident": {"constant_n": 1.0},
            "exit": {"constant_n": 1.52},
        },
        "objectives": [
            {"metric": "MeanReflectance", "band_nm": [550.0, 650.0], "pol": "s"}
        ],
        "parameters": [{"layer_selector": "all", "bounds_nm": [30.0, 160.0]}],
        "solver": {"max_steps": 2, "starts": 1},
    }
    problem_file = tmp_path / "problem.json"
    problem_file.write_text(json.dumps(problem), encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tmm_engine.cli",
            "optimize-problem",
            str(problem_file),
            "--output-dir",
            str(tmp_path / "cli"),
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr[-2000:]
    cli_payload = json.loads(completed.stdout.strip().splitlines()[-1])

    surface = McpSurface(
        artifact_root=tmp_path / "mcp-artifacts", task_root=tmp_path / "mcp-tasks"
    )
    mcp_payload = surface.optimize_problem(json.loads(json.dumps(problem)))

    assert mcp_payload["envelope"]["certificate_id"] == (
        cli_payload["envelope"]["certificate_id"]
    )
    assert mcp_payload["envelope"]["status"] == cli_payload["envelope"]["status"]
    assert mcp_payload["problem"]["compiled"] == cli_payload["problem"]["compiled"]


def test_golden_consistency_batch_mcp_matches_individual(surface: McpSurface) -> None:
    tasks = []
    for shift in (0.0, 3.0):
        task = json.loads(json.dumps(GOOD_TASK))
        task["simulation"]["stack"]["layers"][0]["thickness_nm"] += shift
        tasks.append(task)

    report = surface.simulate_batch(tasks)
    assert report["batch_size"] == 2
    assert report["ok"] is True

    for entry, task in zip(report["entries"], tasks):
        envelope = surface.run(json.loads(json.dumps(task)))
        assert entry["certificate_id"] == envelope["certificate_id"]
        assert entry["physics_accepted"] is True
