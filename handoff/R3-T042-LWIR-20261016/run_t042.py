"""Original-local T042 launcher.

This file is task-local wiring only.  It binds the reviewed T042 source, the
fresh case packet, the unchanged native billing adapter, and the T042 aggregate
guard.  No stage is started unless the caller supplies the explicit ``case
--execute`` action.  The preflight action performs only read-only checks and
does not construct a money guard.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import traceback
import types
from typing import Any, Callable


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent
SOURCE = Path(r"D:\OptoMind-Article-3\r3t042a")
SOURCE_INPUT = SOURCE / "focused_research" / "campaigns" / "t042" / "inputs" / "e2e-12-lwir-ge-window"
PACKET_INPUT = ROOT
CASE = "e2e-12-lwir-ge-window"
CASE_ROOT = ROOT / "cases" / CASE

# The release packet identifies the parent cloud implementation.  The reviewed
# original-local source contains the two committed Windows lifecycle changes,
# so the launcher records both identities and binds the latter.
CLOUD_IMPLEMENTATION_SHA = "21b1d994f70bf88a0bd3f650e956c207ea163a41"
IMPLEMENTATION_SHA = "e926a02b61065cebec2df28371eb5c07582d48c1"
BASE_SHA = "84b050d53e5d07207c032f7d1fc4c150d47dcf1f"
NATIVE_QUESTION_SHA = "9471a83b6c3ab5490fcb8f3ca8c414aba698f961a69b2ea05b3376df5ac66aea"
RII_SHA = "e4c1ec34cc4f765a8400ff87bb7dee8705b6da2cdb11a229cc57ad9bdd274989"

D20 = ROOT.parent / "R3-T019-DEDICATED20"
T030 = ROOT.parent / "R3-T030"
OLD_LEDGER = Path(r"D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite")
POOL_LEDGER = D20 / "private" / "budget.sqlite"
TOOLS = ROOT.parent / "R3-T001-INTEGRATE-OFFLINE" / "release" / "local_tools"
MAX_TOKENS = 16384
TASK_ID = f"R3-T042:{CASE}"

# These are the exact opaque Review-v2 bindings used by the established T022
# full-text lane.  Values are never written to receipts; only paths and the
# configured/not-exposed status are recorded.
FULLTEXT_REVIEW_ROOT = Path(r"D:\OptoMind-Article-3\r3t022r3")
FULLTEXT_REVIEW_COMMIT = "d2deb4dbe18b23837d949822ad731afbaba9414b"
FULLTEXT_REVIEW_PYTHON = Path(r"D:\OptoMind-Article-3\local-preparation\envs\review\Scripts\python.exe")
FULLTEXT_DOCLING_PYTHON = Path(r"F:\OptoMind-Review-2\outputs\e2e_optics_20261012_80cny\parser_repair\venv_docling\Scripts\python.exe")
FULLTEXT_DOCLING_CONFIG = ROOT.parent / "R3-T022" / "private" / "BASIC_PARALLEL3_CONFIG.json"
FULLTEXT_OPENALEX_KEYS = Path(r"F:\OptoMind-Review-2\api_keys\openalex.txt")
FULLTEXT_CORE_KEYS = Path(r"F:\OptoMind-Review-2\api_keys\core_api.txt")
FULLTEXT_UNPAYWALL_EMAIL = Path(r"F:\OptoMind-Review-2\api_keys\Unpaywall.txt")

INITIAL_FORWARD_CAP = 2048
INITIAL_CERT_CAP = 64
TOTAL_FORWARD_CAP = 4096
TOTAL_CERT_CAP = 128

EXPECTED_SOURCE_HASHES = {
    "focused_research/local_campaign_guard.py": "152a812c2971d2bbecbe2dc0bca226906dd3ae94052bdf67fcd3c9dc5e07b1ed",
    "focused_research/run.py": "fc58c5dd1ccb335622d47c46fea4459e3b98de6ee3130b4610d785b9abe33699",
    "focused_research/route_stage.py": "6d3d95fad8b90839efe7c36394cbcdfe3d78770a6bc0edd437f3dd0c6e666615",
    "focused_research/search_stage.py": "65f8be908d54fdfa35bd5cefeda807610f5861113148f72c527b64d041273f8f",
    "focused_research/task_sheet.py": "ef54bd80bcdd7e9076a04aeb8614838ccb31b751cb7589dc65daa2ff70029f52",
    "focused_research/material_evidence.py": "db5562831e80c42cfdc6bbe18c7d356307de442953c5d0e93aa19cb881a59a3e",
    "focused_research/fulltext_stage.py": "ee6596a5514c6956078ca4a606eab76f579bc7150034cce39b090a1210b7b94f",
    "focused_research/research_routes.py": "304034ed741c7a5bdfc34a38ad00badadf58d90b80acea7e6a20a8fb81107607",
    "veritmm/tmm_engine/material_registry.py": "4adfbf08801e79e496c21c535d28f08af4aa2481c8e29bd1c1c081a7251355f8",
    "veritmm/tmm_engine/preflight.py": "20ab14d1144c7e709b3f9db8208cf74e23a8edc88ea0923865dcbf480c2b33c5",
    # These two are the parent-reviewed local commit after the original
    # Windows SQLite handle-lifetime repair.
    "focused_research/vendor/review/optomind_research/s2_cache.py": "075fad382060b543ca8d5758ddb4f490ca64b5716f02f94e88d94d0da5821692",
    "focused_research/tests/test_s2_cooldown.py": "17c5007672327e5d30dbdccb4aad28cdfb1743ebabf4f5430a3288812342be94",
}

INPUT_HASHES = {
    "FRESH_QUESTION.txt": (434, "54831b5a941f3418f3949592f6230f4354fdcd7e7a63fdde333e6f48436ddff7"),
    "FRESH_REQUEST.json": (491, "f7b5e4dfbee01fa9ca7d21097b9f682503d780bce52a71f1d446ca46269df74e"),
    "OPTICAL_MEDIA.json": (337, "fde20c394a300ee1ea859281ec6a78144cfc50cae0c450e3097157976c7567fd"),
    "WAVELENGTHS_NM.json": (4908, "391077abc96a765dc5aca8fa386f1d00c872500fe9242e1563a00beac7a8b1a2"),
}


def load(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value: Any) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def save_new(path: Path | str, value: Any) -> None:
    """Create an artifact once; a rerun must use a new case directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def save_replace(path: Path | str, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    next_path = path.with_suffix(path.suffix + ".next")
    with next_path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(next_path, path)


def module(path: Path | str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load task-local module: {path}")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def _clear_source_modules() -> None:
    roots = {"config", "llm", "optomind_research", "focused_research", "tmm_engine",
             "task_sheet", "research_routes", "route_stage"}
    for name in list(sys.modules):
        if name.split(".")[0] in roots:
            del sys.modules[name]


def bind_source() -> tuple[Any, dict[str, str]]:
    actual_commit = subprocess.check_output(
        ["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip()
    if actual_commit != IMPLEMENTATION_SHA:
        raise RuntimeError(f"source commit drift: {actual_commit} != {IMPLEMENTATION_SHA}")
    dirty = subprocess.check_output(
        ["git", "-C", str(SOURCE), "status", "--porcelain", "--untracked-files=no"],
        text=True,
    ).strip()
    if dirty:
        raise RuntimeError(f"source worktree is dirty: {dirty}")
    for relative, expected in EXPECTED_SOURCE_HASHES.items():
        actual = sha(SOURCE / relative)
        if actual != expected:
            raise RuntimeError(f"source file hash drift: {relative}: {actual} != {expected}")

    _clear_source_modules()
    focus = SOURCE / "focused_research"
    vendor = focus / "vendor" / "review"
    sys.path[:0] = [str(vendor), str(focus), str(SOURCE / "veritmm"), str(SOURCE)]
    package = types.ModuleType("focused_research")
    package.__path__ = [str(focus)]
    package.__package__ = "focused_research"
    sys.modules["focused_research"] = package
    runner = module(focus / "run.py", "t042_fixed_runner")
    names = [
        "optomind_research.query_planner",
        "focused_research.search_stage",
        "focused_research.local_campaign_guard",
        "focused_research.material_evidence",
        "focused_research.fulltext_stage",
        "task_sheet",
        "research_routes",
        "tmm_engine.material_registry",
        "tmm_engine.preflight",
    ]
    origins = {name: str(Path(importlib.import_module(name).__file__).resolve()) for name in names}
    if not all(Path(path).is_relative_to(SOURCE) for path in origins.values()):
        raise RuntimeError(f"stale import binding: {origins}")
    return runner, origins


def read_only_budget() -> dict[str, Any]:
    aggregate = module(ROOT / "local_tools" / "aggregate_budget.py", "t042_bound_aggregate_preflight")
    history = load(ROOT / "CASE_HISTORY_FOR_GUARD.json")
    # sqlite3.Connection.__exit__ commits/rolls back but does not close the
    # handle.  Explicit closing is required for the native Windows cleanup
    # contract and keeps this read-only admission from retaining file locks.
    with contextlib.closing(sqlite3.connect(OLD_LEDGER.as_uri() + "?mode=ro", uri=True)) as original, \
            contextlib.closing(sqlite3.connect(POOL_LEDGER.as_uri() + "?mode=ro", uri=True)) as pool:
        original.row_factory = pool.row_factory = sqlite3.Row
        result = aggregate.checked_totals(
            original, pool, Decimal("0"), history, TASK_ID, admission=False,
        )
    result.update({
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "original_ledger_sha256": sha(OLD_LEDGER),
        "pool_ledger_sha256": sha(POOL_LEDGER),
        "both_ledgers_locked_across_check_and_reservation": False,
    })
    return result


def _after_fix_acceptance_gate() -> dict[str, Any]:
    """Bind the prior six-file acceptance to the current post-fix bytes.

    The saved acceptance is historical evidence from the cloud checkout.  Its
    post-fix hashes must still equal the actual current source bytes before a
    fresh local chain can be admitted.
    """
    path = ROOT / "preflight" / "T042_LOCAL_ACCEPTANCE_after_fix.json"
    report = load(path)
    if report.get("global_pass") is not True:
        raise RuntimeError("saved post-fix T042 acceptance is not globally passing")
    tests = report.get("tests") or {}
    counts = tests.get("counts") or {}
    if tests.get("exit") != 0 or counts.get("passed") != 127 or counts.get("failed") != 0 \
            or counts.get("subtests_passed") != 6:
        raise RuntimeError("saved post-fix T042 acceptance does not record 127/6 passing tests")
    post = report.get("post_fix_hashes") or []
    current = {}
    for row in post:
        relative = str(row.get("path", ""))
        expected = EXPECTED_SOURCE_HASHES.get(relative)
        if expected is None:
            raise RuntimeError("post-fix acceptance contains an unbound source path: " + relative)
        source_path = SOURCE / relative
        actual = sha(source_path)
        if actual != expected or actual != row.get("sha256") or source_path.stat().st_size != row.get("bytes"):
            raise RuntimeError("post-fix acceptance hash is stale: " + relative)
        current[relative] = {"bytes": source_path.stat().st_size, "sha256": actual}
    expected_post = {
        "focused_research/vendor/review/optomind_research/s2_cache.py",
        "focused_research/tests/test_s2_cooldown.py",
    }
    if set(current) != expected_post:
        raise RuntimeError("post-fix acceptance does not cover both repaired source files")
    return {
        "path": str(path),
        "historical_global_pass": True,
        "historical_test_exit": tests.get("exit"),
        "historical_counts": counts,
        "historical_source_sha": report.get("source_sha"),
        "current_source_implementation_sha": IMPLEMENTATION_SHA,
        "current_post_fix_hashes": current,
    }


def _fulltext_config_binding() -> dict[str, Any]:
    paths = {
        "review_root": FULLTEXT_REVIEW_ROOT,
        "review_python": FULLTEXT_REVIEW_PYTHON,
        "docling_python": FULLTEXT_DOCLING_PYTHON,
        "docling_config": FULLTEXT_DOCLING_CONFIG,
        "openalex_api_keys_file": FULLTEXT_OPENALEX_KEYS,
        "core_api_keys_file": FULLTEXT_CORE_KEYS,
        "unpaywall_email_file": FULLTEXT_UNPAYWALL_EMAIL,
    }
    missing = [name for name, value in paths.items() if not Path(value).exists()]
    if missing:
        raise RuntimeError("established T022 full-text binding missing: " + ", ".join(missing))
    return {
        "review_root": str(FULLTEXT_REVIEW_ROOT),
        "review_commit": FULLTEXT_REVIEW_COMMIT,
        "review_python": str(FULLTEXT_REVIEW_PYTHON),
        "docling_python": str(FULLTEXT_DOCLING_PYTHON),
        "docling_config": str(FULLTEXT_DOCLING_CONFIG),
        "openalex_api_keys_file": str(FULLTEXT_OPENALEX_KEYS),
        "core_api_keys_file": str(FULLTEXT_CORE_KEYS),
        "unpaywall_email_file": str(FULLTEXT_UNPAYWALL_EMAIL),
        "opaque_values_exposed": False,
        "paths_exist": True,
    }


def _configure_fulltext_env() -> dict[str, Any]:
    """Apply the established T022 provider bindings without logging secrets."""
    _fulltext_config_binding()
    os.environ["OPENALEX_API_KEYS_FILE"] = str(FULLTEXT_OPENALEX_KEYS)
    os.environ["CORE_API_KEYS_FILE"] = str(FULLTEXT_CORE_KEYS)
    contact = next((line.strip() for line in FULLTEXT_UNPAYWALL_EMAIL.read_text(
        encoding="utf-8-sig", errors="replace").splitlines()
        if line.strip() and "@" in line), "")
    if not contact:
        raise RuntimeError("established T022 Unpaywall contact configuration is empty")
    os.environ["UNPAYWALL_EMAIL"] = contact
    os.environ["OPENALEX_EMAIL"] = contact
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    return {"configured": True, "opaque_values_exposed": False}


def _native_candidate_preflight_binding() -> dict[str, Any]:
    """Verify the native route-to-material-preflight path without compiling a candidate."""
    search_path = SOURCE / "focused_research" / "search_stage.py"
    execution_path = SOURCE / "focused_research" / "research_execution.py"
    execution_engine_path = SOURCE / "veritmm" / "tmm_engine" / "execution.py"
    files = {
        "search_stage": (search_path, ("rc.prepare_tmm_inputs(", "ex.execute_prepared_batch(")),
        "research_execution": (execution_path, ("simulation_task_from_dict(", "execution.execute_task(")),
        "tmm_execution": (execution_engine_path, ("preflight_task(", "PREFLIGHT_REPORT.json")),
    }
    binding = {}
    for name, (path, tokens) in files.items():
        lines = path.read_text(encoding="utf-8").splitlines()
        locations = {}
        for token in tokens:
            matches = [index + 1 for index, line in enumerate(lines) if token in line]
            if not matches:
                raise RuntimeError(f"native candidate preflight path missing {name}:{token}")
            locations[token] = matches[:4]
        binding[name] = {"path": str(path), "sha256": sha(path), "source_lines": locations}
    binding.update({
        "route_consumer": str(SOURCE / "focused_research" / "route_consumer.py"),
        "preflight_module": str(SOURCE / "veritmm" / "tmm_engine" / "preflight.py"),
        "contract": "search_stage prepares each candidate, research_execution hands it to native execution, and tmm_engine.execution runs preflight_task before solver dispatch",
        "duplicate_launcher_compilation": False,
    })
    return binding


def _search_count_reconciliation(search_dir: Path, *, stage: str,
                                 forward_cap: int, cert_cap: int) -> dict[str, Any]:
    summary = load(Path(search_dir) / "summary.json")
    metering = summary.get("metering") or {}
    forward = metering.get("search_forward_design_rows")
    certified = metering.get("certification_new_evaluations")
    failed_reserved = metering.get("search_failed_job_reserved_rows", 0)
    if type(forward) is not int or type(certified) is not int or forward < 0 or certified < 0:
        raise RuntimeError(f"{stage} search summary lacks integral actual counters")
    if type(failed_reserved) is not int or failed_reserved < 0:
        raise RuntimeError(f"{stage} failed-job reservation counter is invalid")
    result = {
        "stage": stage,
        "summary_path": str(Path(search_dir) / "summary.json"),
        "actual_forward_design_rows": forward,
        "failed_job_reserved_rows": failed_reserved,
        "accounted_forward_design_rows": forward + failed_reserved,
        "search_metering_complete": metering.get("search_metering_complete"),
        "actual_new_certification_evaluations": certified,
        "stage_forward_cap": forward_cap,
        "stage_certification_cap": cert_cap,
        "within_stage_caps": forward + failed_reserved <= forward_cap and certified <= cert_cap,
        "status": summary.get("status"),
    }
    if not result["within_stage_caps"]:
        raise RuntimeError(f"{stage} counters exceed bounded stage caps")
    return result


def verify_inputs() -> dict[str, Any]:
    acceptance_gate = _after_fix_acceptance_gate()
    fulltext_binding = _fulltext_config_binding()
    candidate_preflight = _native_candidate_preflight_binding()
    provenance = load(ROOT / "INPUT_PROVENANCE.json")
    provenance_inputs = {
        Path(str(row.get("path", ""))).name: row
        for row in (provenance.get("inputs") or [])
        if isinstance(row, dict)
    }
    packet = {}
    source_records = {}
    for name, (expected_bytes, expected_sha) in INPUT_HASHES.items():
        packet_path = PACKET_INPUT / name
        source_path = SOURCE_INPUT / name
        for label, path in (("packet", packet_path), ("source", source_path)):
            if path.stat().st_size != expected_bytes or sha(path) != expected_sha:
                raise RuntimeError(f"input hash mismatch: {label}/{name}")
        if packet_path.read_bytes() != source_path.read_bytes():
            raise RuntimeError(f"packet/source byte mismatch: {name}")
        pinned = provenance_inputs.get(name)
        if not pinned or pinned.get("bytes") != expected_bytes or pinned.get("sha256") != expected_sha:
            raise RuntimeError(f"INPUT_PROVENANCE binding mismatch: {name}")
        packet[name] = {"bytes": expected_bytes, "sha256": expected_sha}
        source_records[name] = {"path": str(source_path), "bytes": expected_bytes, "sha256": expected_sha}

    request = load(SOURCE_INPUT / "FRESH_REQUEST.json")
    packet_request = load(PACKET_INPUT / "FRESH_REQUEST.json")
    if set(request) != {"run_id", "question"} or request != packet_request:
        raise RuntimeError("fresh request is not the exact two-key source packet")
    if request["run_id"] != CASE:
        raise RuntimeError(f"unexpected case id: {request['run_id']}")
    native_run = load(SOURCE / "replay_data" / "runs" / f"{CASE}.json")
    native_question = str(native_run["summary"]["question"])
    question_sha = hashlib.sha256(native_question.encode("utf-8")).hexdigest()
    if request["question"] != native_question or question_sha != NATIVE_QUESTION_SHA:
        raise RuntimeError("fresh question does not match native question bytes")

    media = load(SOURCE_INPUT / "OPTICAL_MEDIA.json")
    expected_media = {
        "air": {"constant_n": 1.0, "constant_k": 0.0},
        "Ge": {"material": "Ge", "provider": "rii_sqlite", "dataset_id": 221},
        "ZnS": {"material": "ZnS", "provider": "rii_sqlite", "dataset_id": 531},
        "ZnSe": {"material": "ZnSe", "provider": "rii_sqlite", "dataset_id": 562},
    }
    if media != expected_media:
        raise RuntimeError("optical media binding changed")
    grid = load(SOURCE_INPUT / "WAVELENGTHS_NM.json")
    if grid != list(range(3000, 11001, 10)):
        raise RuntimeError("wavelength grid is not the exact 801-point T042 grid")
    rii = SOURCE / "veritmm" / "tmm_engine" / "rii_cache.db"
    if sha(rii) != RII_SHA:
        raise RuntimeError("RII database hash changed")

    receipt_path = ROOT / "preflight" / "T042_MATERIAL_PREFLIGHT.json"
    receipt = load(receipt_path)
    if receipt.get("global_pass") is not True:
        raise RuntimeError("existing zero-fee material preflight is not globally passing")
    if receipt.get("source_sha256") != RII_SHA:
        raise RuntimeError("material receipt RII source hash mismatch")
    if receipt.get("new_model_http") != 0 or receipt.get("new_solver_calls") != 0 or receipt.get("new_certifications") != 0:
        raise RuntimeError("material receipt records nonzero zero-fee counters")
    material_checks = receipt.get("checks", {})
    if not material_checks.get("material_full_grid", {}).get("pass"):
        raise RuntimeError("material full-grid check is not passing")
    if receipt.get("Ge_missing_k") != "provider zero, not measured losslessness":
        raise RuntimeError("Ge missing-k convention is not explicitly preserved")

    history = load(ROOT / "CASE_HISTORY_FOR_GUARD.json")
    if not history.get("attribution_complete") or len(history.get("all_reservation_ids_at_audit", [])) != 358:
        raise RuntimeError("T042 case history attribution is incomplete")
    if history.get("ledger_writes") != 0 or history.get("model_calls") != 0:
        raise RuntimeError("history receipt contains unexpected writes or model calls")
    if set((history.get("case_reservation_ids") or {}).keys()) != {CASE}:
        raise RuntimeError("history case identity is not T042 case12 only")
    if history.get("task_id") != TASK_ID or history.get("task_max_http") != 4:
        raise RuntimeError("history task identity or HTTP cap changed")
    if history.get("not_original_T032_three_case_batch") is not True:
        raise RuntimeError("T042 was incorrectly attributed to the old T032 batch")

    cfg_module = module(T030 / "run_t030.py", "t042_original_billing_preflight")
    cfg = cfg_module.original_billing()
    local_guard = ROOT / "local_tools" / "task_budget_guard_220.py"
    original_guard = T030 / "local_tools" / "task_budget_guard_220.py"
    if local_guard.read_bytes() != original_guard.read_bytes():
        raise RuntimeError("T042 task budget guard differs from the reviewed original guard")
    return {
        "case_id": CASE,
        "task_id": TASK_ID,
        "base_sha": BASE_SHA,
        "cloud_implementation_sha": CLOUD_IMPLEMENTATION_SHA,
        "source_implementation_sha": IMPLEMENTATION_SHA,
        "source_input_records": source_records,
        "packet_input_records": packet,
        "native_question_sha256": question_sha,
        "media": media,
        "grid_count": len(grid),
        "rii_sha256": sha(rii),
        "material_receipt": str(receipt_path),
        "post_fix_acceptance_gate": acceptance_gate,
        "fulltext_config_binding": fulltext_binding,
        "native_candidate_preflight_binding": candidate_preflight,
        "history_reservations": len(history["all_reservation_ids_at_audit"]),
        "billing_config_path": str(cfg.get("paths", {}).get("review_runtime", "")),
        "budget_before": read_only_budget(),
    }


def preflight() -> dict[str, Any]:
    runner, origins = bind_source()
    del runner
    result = verify_inputs()
    result["interpreter"] = sys.executable
    result["source_import_origins"] = origins
    result["launcher_sha256"] = sha(__file__)
    result["aggregate_guard_sha256"] = sha(ROOT / "local_tools" / "aggregate_budget.py")
    result["task_guard_sha256"] = sha(ROOT / "local_tools" / "task_budget_guard_220.py")
    result["new_model_http"] = 0
    result["new_retrieval_calls"] = 0
    result["new_solver_calls"] = 0
    result["read_only"] = True
    return result


def _persist_guard_audit(case_root: Path, snapshot: dict[str, Any]) -> None:
    audit_dir = case_root / "guard_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    event_path = audit_dir / f"{snapshot['dispatch_attempts']:02d}_{digest(snapshot)}.json"
    if not event_path.exists():
        save_new(event_path, snapshot)
    save_replace(case_root / "GUARD_CURRENT.json", snapshot)


def make_audited_guard(case_root: Path, cfg: dict[str, Any]):
    aggregate = module(ROOT / "local_tools" / "aggregate_budget.py", "t042_bound_aggregate_execute")
    history = load(ROOT / "CASE_HISTORY_FOR_GUARD.json")
    adapter_module = module(D20 / "transport" / "dedicated_qwen_adapter.py", "t042_original_qwen_adapter")
    native = adapter_module.load_native_runtime(Path(cfg["paths"]["review_runtime"]))
    entry, base = adapter_module._load_budget_factory(
        TOOLS / "budgeted_entry.py", ROOT / "local_tools" / "task_budget_guard_220.py",
    )
    guard_class = aggregate.guard_class(
        base.TaskBudgetGuard, OLD_LEDGER, history, case_root / "atomic_reservations",
    )
    money = guard_class(
        native.GlobalBudgetLedger(limit_cny=220, path=POOL_LEDGER),
        task_id=TASK_ID, lane="fresh_bounded_cycle", task_limit_cny=8,
        max_calls=4, global_limit_cny=220,
    )
    config = adapter_module.Dedicated20Config(
        ledger_path=POOL_LEDGER,
        audit_root=case_root / "private" / "audit",
        key_file=Path(cfg["paths"]["key_file"]),
        task_id=TASK_ID,
        model="qwen3.5-plus",
        repair_model="qwen3.7-flash",
        task_limit_cny=8,
        global_limit_cny=220,
        max_calls=4,
        max_retries=0,
        max_keys=1,
        max_tokens=MAX_TOKENS,
    )
    planner_module = importlib.import_module("optomind_research.query_planner")
    resolver, _ = adapter_module._load_vendored_model_resolver(Path(planner_module.__file__))
    adapter = adapter_module.FocusedQwenCallAdapter(
        audited_client_factory=entry.make_audited_client(native, money, config.factory_config()),
        runtime=native, config=config, guard=money, model_resolver=resolver,
    )
    calls = {"count": 0}
    state: dict[str, Any] = {}

    def protected(agent: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        guard = state.get("guard")
        if guard is None:
            raise RuntimeError("planner dispatch without the live campaign guard")
        snapshot = guard.audit
        if snapshot["dispatch_attempts"] != calls["count"] + 1:
            raise RuntimeError("planner dispatch count is not admitted by the live campaign guard")
        event = snapshot.get("events", [])[-1] if snapshot.get("events") else {}
        if event.get("status") != "dispatch_attempt_recorded":
            raise RuntimeError("planner dispatch missing durable pre-dispatch audit")
        if agent != "QueryPlannerAgent":
            raise RuntimeError("only the original QueryPlannerAgent is admitted")
        if kwargs.get("force_mock") is not False:
            raise RuntimeError("mock planner calls are forbidden")
        if kwargs.get("max_tokens") != MAX_TOKENS:
            raise RuntimeError("planner max_tokens changed")
        if kwargs.get("temperature") != 0.1:
            raise RuntimeError("planner temperature changed")
        if kwargs.get("response_format") != {"type": "json_object"} or kwargs.get("stream") not in (None, False):
            raise RuntimeError("planner JSON wire contract changed")
        if event.get("contract_repair") and event.get("stage") not in {"task_sheet", "route_generation"}:
            raise RuntimeError("shared contract repair is not permitted during revision")

        model_tier = kwargs.get("model_tier", "")
        model = adapter_module._model_for_tier(
            model_tier, agent, config.model, config.repair_model, resolver,
        )
        body, _ = native.build_qwen_wire_body(
            messages, model=model, max_output_tokens=MAX_TOKENS,
            thinking=False, thinking_budget=0, json_mode=True, stream=False, temperature=0.1,
        )
        estimate = native.estimate_prompt_tokens(body, messages, prompt_token_counter=None)
        amount = Decimal(str(native.estimated_cost_cny(
            {"prompt_tokens": estimate["prompt_tokens"], "completion_tokens": MAX_TOKENS},
            model=model, conservative=True,
        )))
        before = read_only_budget()
        if Decimal(before["case_exposure_cny"]) + amount > Decimal("8"):
            raise RuntimeError("T042 cumulative case budget would be exceeded")
        if Decimal(before["project_exposure_cny"]) + amount > Decimal("220"):
            raise RuntimeError("project cumulative budget would be exceeded")
        calls["count"] += 1
        save_new(case_root / f"CALL_{calls['count']}_PREFLIGHT.json", {
            "source_implementation_sha": IMPLEMENTATION_SHA,
            "cloud_implementation_sha": CLOUD_IMPLEMENTATION_SHA,
            "model": model,
            "model_tier": model_tier,
            "wire_body_sha256": hashlib.sha256(body).hexdigest(),
            "wire_body_bytes": len(body),
            "native_prompt_estimate": estimate,
            "conservative_reservation_cny": str(amount),
            "budget_before": before,
            "transport_retries": 0,
            "shared_contract_repair": bool(event.get("contract_repair")),
        })
        result = adapter(agent, messages, **kwargs)
        save_replace(case_root / "BUDGET_STATUS.json", money.as_dict())
        return result

    planner_module.call_qwen_chat = protected
    planner = planner_module.QueryPlannerAgent(
        real_llm=True, temperature=0.1, max_tokens=MAX_TOKENS, use_v2_plan=True,
    )
    guard_module = importlib.import_module("focused_research.local_campaign_guard")
    guard = guard_module.CampaignPlannerGuard(
        planner, case_id=CASE,
        audit_sink=lambda snapshot: _persist_guard_audit(case_root, snapshot),
    )
    state["guard"] = guard
    return guard, money, lambda snapshot: _persist_guard_audit(case_root, snapshot)


def _step(case_root: Path, name: str, function: Callable[[], Any]) -> Any:
    start = time.monotonic()
    with (case_root / f"{name}.stdout.log").open("x", encoding="utf-8") as stdout, \
            (case_root / f"{name}.stderr.log").open("x", encoding="utf-8") as stderr:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = function()
    save_new(case_root / f"{name}.EXECUTION.json", {
        "stage": name,
        "exit_code": result if isinstance(result, int) else None,
        "wall_seconds": time.monotonic() - start,
        "source_implementation_sha": IMPLEMENTATION_SHA,
    })
    return result


def _empty_evidence() -> dict[str, Any]:
    return {"schema_version": "focused_evidence_items_v1", "items": []}


def _make_constraints(sheet: dict[str, Any], case_root: Path, question: str) -> dict[str, Any]:
    rows = sheet.get("explicit_constraints") or []
    if not isinstance(rows, list):
        raise RuntimeError("task sheet explicit_constraints is not a list")

    def quote(row: dict[str, Any]) -> str:
        span = row.get("original_span")
        if not isinstance(span, str) or not span.strip() or span not in question:
            raise RuntimeError("constraint row does not preserve an exact native-question quote")
        return span

    def has_word(text: str, word: str) -> bool:
        return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(word) + r"(?![A-Za-z0-9])", text, re.IGNORECASE))

    def supports(field: str, row: dict[str, Any]) -> bool:
        span = quote(row)
        lower = span.casefold()
        if field == "allowed_materials":
            return (has_word(span, "ZnS") and has_word(span, "ZnSe")) or ("硫化锌" in span and "硒化锌" in span)
        if field == "max_layers":
            return bool(re.search(r"(?<!\d)16(?!\d)", span)) and ("层" in span or re.search(r"\blayers?\b", lower))
        if field == "substrate":
            return (has_word(span, "Ge") or "锗" in span) and ("基底" in span or "substrate" in lower)
        if field == "incident_medium":
            return ((has_word(span, "air") or "空气" in span)
                    and ("入射" in span or "incident" in lower))
        raise RuntimeError("unknown constraint field: " + field)

    specs = ("allowed_materials", "max_layers", "substrate", "incident_medium")
    binding: dict[str, Any] = {}
    source_ids: dict[str, list[str]] = {}
    for field in specs:
        selected = [row for row in rows if isinstance(row, dict) and supports(field, row)]
        if not selected:
            raise RuntimeError(f"fresh task sheet has no source row for {field}")
        binding[field] = [{
            "id": row.get("id"),
            "constraint": row.get("constraint"),
            "original_span": quote(row),
        } for row in selected]
        source_ids[field] = [str(row.get("id")) for row in selected]
    save_new(case_root / "CONSTRAINT_BINDING_AUDIT.json", {
        "source": "fresh generated task_sheet.explicit_constraints",
        "question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
        "exact_original_spans_required": True,
        "fields": binding,
    })
    constraints = {
        "allowed_materials": ["ZnS", "ZnSe"],
        "max_layers": 16,
        "substrate": "Ge",
        "incident_medium": "air",
        "constraint_source_ids": source_ids,
        "assumptions": {},
    }
    save_new(case_root / "design_constraints.json", constraints)
    return constraints


def _validate_sheet(sheet: dict[str, Any], question: str) -> None:
    task_sheet = importlib.import_module("task_sheet")
    task_sheet.validate(sheet, question=question)
    actual = []
    for goal in sheet.get("subgoals", []):
        metric = goal["executable_metric"]
        region = metric["region"]
        actual.append((metric["metric"], metric["sense"], *region["wavelength_nm"]))
        if region.get("angle_deg") != 0 or region.get("polarization") != "unpolarized":
            raise RuntimeError("execution angle/polarization assumptions changed")
        if metric.get("mean_reduction") != "wavelength_integral_mean":
            raise RuntimeError("objective reduction is not the required wavelength integral mean")
    expected = [
        ("mean_transmittance", "maximize", 8000, 11000),
        ("mean_transmittance", "minimize", 3000, 5000),
    ]
    if sorted(actual) != sorted(expected):
        raise RuntimeError(f"fresh task goals changed: {actual}")
    assumptions = sheet.get("unknowns_assumptions") or []
    if not any(str(row.get("kind", "")).casefold() == "assumption" for row in assumptions if isinstance(row, dict)):
        raise RuntimeError("execution assumptions are absent from task sheet")
    if sheet.get("original_question") not in (None, question):
        raise RuntimeError("task sheet original question changed")


def _merge_evidence(case_root: Path, fulltext_result: dict[str, Any], *, retrieval_exit: int | None) -> Path:
    retrieval_evidence = case_root / "retrieval" / "evidence_items.json"
    if not retrieval_evidence.is_file():
        # The empty S2 schema is permitted only when retrieval did not produce
        # its evidence file.  Preserve that failure/absence explicitly so the
        # downstream merge cannot be mistaken for successful retrieval.
        save_new(case_root / "retrieval" / "S2_EVIDENCE_MISSING.json", {
            "reason": "retrieval_evidence_file_absent",
            "retrieval_exit": retrieval_exit,
            "honest_empty_s2": True,
            "downstream_support_is_not_retrieved_s2": True,
        })
        save_new(case_root / "retrieval" / "evidence_items.json", _empty_evidence())
        retrieval_evidence = case_root / "retrieval" / "evidence_items.json"
    exports = [Path(path) for path in (fulltext_result.get("exports") or [])]
    if len(exports) > 5 or any(not path.is_file() for path in exports):
        raise RuntimeError("verified fulltext exports are missing or exceed the five-paper limit")
    material = importlib.import_module("material_evidence")
    args = ["--s2", str(retrieval_evidence), "--output-dir", str(case_root / "evidence")]
    for export in exports:
        args.extend(["--export", str(export)])
    try:
        status = material.main(args)
        save_new(case_root / "EVIDENCE_MERGE_STATUS.json", {
            "return_code": status, "exports": [str(path) for path in exports],
        })
    except Exception as exc:
        save_new(case_root / "EVIDENCE_MERGE_FAILURE.json", {
            "type": type(exc).__name__, "reason": str(exc), "traceback": traceback.format_exc(),
        })
        raise
    if status != 0:
        raise RuntimeError(f"evidence merge returned nonzero status: {status}")
    combined = case_root / "evidence" / "combined_evidence_items.json"
    if not combined.is_file():
        save_new(case_root / "EVIDENCE_MERGE_FAILURE.json", {
            "type": "MissingOutput",
            "reason": "material evidence merge reported success but combined output is absent",
            "exports": [str(path) for path in exports],
        })
        raise RuntimeError("evidence merge produced no combined_evidence_items.json")
    return combined


def _candidate_inputs(case_root: Path, plan_dir: Path) -> tuple[Path, Path]:
    raw = case_root / "retrieval" / "raw_retrieval_records.json"
    raw_batches = load(raw) if raw.is_file() else []
    if isinstance(raw_batches, dict):
        raw_batches = raw_batches.get("records", [])
    if not isinstance(raw_batches, list):
        raw_batches = []
    queries_path = case_root / "retrieval" / "queries.json"
    if not queries_path.is_file():
        queries_path = plan_dir / "queries.json"
    queries = load(queries_path) if queries_path.is_file() else []
    if isinstance(queries, dict):
        queries = queries.get("queries", [])
    if not isinstance(queries, list):
        queries = []
    query_by_call = {index: query for index, query in enumerate(queries) if isinstance(query, dict)}
    records: list[dict[str, Any]] = []
    for batch_index, batch in enumerate(raw_batches):
        if not isinstance(batch, dict) or not isinstance(batch.get("rows"), list):
            continue
        call_index = batch.get("call_index", batch_index)
        query = query_by_call.get(call_index, query_by_call.get(batch_index, {}))
        task_query = query.get("task_query") if isinstance(query.get("task_query"), dict) else query
        execution_query_id = query.get("query_id", "")
        task_query_id = task_query.get("id") or execution_query_id
        goal_ids = list(task_query.get("subgoal_ids") or query.get("subgoal_ids") or [])
        accepted_limit = batch.get("accepted_limit", len(batch["rows"]))
        for row_index, row in enumerate(batch["rows"][:accepted_limit]):
            if not isinstance(row, dict):
                continue
            paper = row.get("paper") if isinstance(row.get("paper"), dict) else {}
            raw_metadata = row.get("raw_metadata") if isinstance(row.get("raw_metadata"), dict) else {}
            # Review's normalize_record accepts one paper row.  The retrieval
            # file stores per-query batches, so flatten the actual paper and
            # raw metadata while retaining both nested originals and the exact
            # current-query provenance.
            record = dict(raw_metadata)
            record.update(paper)
            record.update(row)
            query_ids = list(record.get("query_ids") or [])
            if task_query_id and task_query_id not in query_ids:
                query_ids.append(task_query_id)
            record["query_ids"] = query_ids
            record["goal_ids"] = list(dict.fromkeys(list(record.get("goal_ids") or []) + goal_ids))
            record["execution_query_id"] = execution_query_id
            record["task_query"] = task_query
            record["retrieval_batch"] = {
                key: value for key, value in batch.items() if key != "rows"
            }
            record["retrieval_call_index"] = call_index
            record["retrieval_row_index"] = row_index
            records.append(record)
    records_path = case_root / "fulltext" / "candidate_records.json"
    queries_out = case_root / "fulltext" / "fulltext_queries.json"
    save_new(records_path, {"records": records})
    save_new(queries_out, {"queries": queries})
    save_new(case_root / "fulltext" / "INPUT_BINDING.json", {
        "records_source": str(raw),
        "queries_source": str(queries_path),
        "records_sha256": sha(records_path),
        "queries_sha256": sha(queries_out),
        "record_count": len(records),
        "raw_batch_count": len(raw_batches),
        "query_count": len(queries),
        "flattened_batch_rows": True,
        "paper_and_raw_metadata_preserved": True,
        "current_task_only": True,
    })
    return records_path, queries_out


def run_case() -> int:
    prepared = preflight()
    if CASE_ROOT.exists() and any(CASE_ROOT.iterdir()):
        raise RuntimeError("T042 case output already exists; no automatic restart or overwrite")
    CASE_ROOT.mkdir(parents=True, exist_ok=True)
    save_new(CASE_ROOT / "CASE_STARTED.json", prepared)
    save_new(CASE_ROOT / "FRESH_REQUEST.json", load(SOURCE_INPUT / "FRESH_REQUEST.json"))
    cfg_module = module(T030 / "run_t030.py", "t042_original_billing_execute")
    cfg = cfg_module.original_billing()
    guard = money = persist = None
    status: dict[str, Any] = {
        "case_id": CASE,
        "task_id": TASK_ID,
        "base_sha": BASE_SHA,
        "cloud_implementation_sha": CLOUD_IMPLEMENTATION_SHA,
        "source_implementation_sha": IMPLEMENTATION_SHA,
        "engineering_status": "running",
        "scientific_status": "unassessed",
        "stages": {},
    }
    try:
        runner, _ = bind_source()
        guard, money, persist = make_audited_guard(CASE_ROOT, cfg)
        guard.set_stage("task_sheet")
        plan_dir = runner.prepare_output(CASE_ROOT / "plan")
        try:
            outcome = _step(
                CASE_ROOT, "01_plan",
                lambda: runner.generate_plan(load(SOURCE_INPUT / "FRESH_REQUEST.json")["question"], plan_dir, planner=guard),
            )
        finally:
            persist(guard.audit)
        status["stages"]["plan"] = outcome.get("status")
        if outcome.get("status") != "ok":
            raise RuntimeError(f"task-sheet contract not accepted: {outcome.get('status')}")
        question = load(SOURCE_INPUT / "FRESH_REQUEST.json")["question"]
        sheet = outcome.get("task_sheet") or load(CASE_ROOT / "plan" / "task_sheet.json")
        _validate_sheet(sheet, question)
        constraints = _make_constraints(sheet, CASE_ROOT, question)
        save_new(CASE_ROOT / "optical_media.json", load(SOURCE_INPUT / "OPTICAL_MEDIA.json"))
        save_new(CASE_ROOT / "wavelengths.json", load(SOURCE_INPUT / "WAVELENGTHS_NM.json"))
        save_new(CASE_ROOT / "THICKNESS_DOMAIN_POLICY.json", {
            "user_global_thickness_bounds": None,
            "route_domains": "model-proposed finite positive family hypotheses only",
        })

        os.environ.setdefault("SEMANTIC_SCHOLAR_API_KEYS_FILE", r"F:\OptoMind-Review-2\api_keys\semantic-scholar-api-key.txt")
        retrieval_exit = _step(CASE_ROOT, "02_retrieval", lambda: runner.main([
            "retrieve", "--plan-file", str(CASE_ROOT / "plan" / "query_plan.json"),
            "--output", str(CASE_ROOT / "retrieval"), "--per-query-limit", "10",
            "--retrieval-seconds", "60",
        ]))
        status["stages"]["retrieval_exit"] = retrieval_exit
        records_path, queries_path = _candidate_inputs(CASE_ROOT, CASE_ROOT / "plan")

        fulltext_env = _configure_fulltext_env()
        save_new(CASE_ROOT / "fulltext" / "FULLTEXT_CONFIG_BINDING.json", {
            **_fulltext_config_binding(), **fulltext_env,
        })
        fulltext = importlib.import_module("fulltext_stage")
        fulltext_result = _step(CASE_ROOT, "03_fulltext", lambda: fulltext.run_fulltext_stage(
            load(records_path)["records"], load(queries_path)["queries"], CASE_ROOT / "fulltext",
            review_root=FULLTEXT_REVIEW_ROOT,
            review_python=str(FULLTEXT_REVIEW_PYTHON),
            docling_python=str(FULLTEXT_DOCLING_PYTHON),
            docling_config=FULLTEXT_DOCLING_CONFIG,
            max_papers=5, deadline_seconds=180, providers=("openalex", "unpaywall", "core", "crossref", "arxiv"),
            resume=True,
        ))
        save_new(CASE_ROOT / "fulltext" / "FULLTEXT_RESULT_RETURN.json", fulltext_result)
        status["stages"]["fulltext_status"] = fulltext_result.get("status")
        evidence_file = _merge_evidence(CASE_ROOT, fulltext_result, retrieval_exit=retrieval_exit)

        route_prompt = _step(CASE_ROOT, "04_route_prompt", lambda: importlib.import_module("route_stage").main([
            "prompt", "--task-sheet", str(CASE_ROOT / "plan" / "task_sheet.json"),
            "--evidence-file", str(evidence_file), "--constraints-file", str(CASE_ROOT / "design_constraints.json"),
            "--output", str(CASE_ROOT / "route_prompt_check"),
        ]))
        status["stages"]["route_prompt_exit"] = route_prompt
        if route_prompt != 0:
            raise RuntimeError("pure route prompt precheck failed; paid route generation not admitted")

        guard.set_stage("route_generation")
        route_stage = importlib.import_module("route_stage")
        try:
            route_exit = _step(CASE_ROOT, "05_routes", lambda: route_stage.main([
                "generate", "--task-sheet", str(CASE_ROOT / "plan" / "task_sheet.json"),
                "--evidence-file", str(evidence_file), "--constraints-file", str(CASE_ROOT / "design_constraints.json"),
                "--output", str(CASE_ROOT / "routes"),
            ], planner=guard))
        finally:
            persist(guard.audit)
        status["stages"]["route_exit"] = route_exit
        route_file = CASE_ROOT / "routes" / "research_routes.json"
        raw_route_file = CASE_ROOT / "routes" / "research_routes.raw.json"
        if not route_file.is_file() or not raw_route_file.is_file():
            raise RuntimeError("candidate route compiler did not preserve accepted and raw route files")
        combined = load(evidence_file)
        selected = load(CASE_ROOT / "routes" / "evidence_items.json")
        by_id = {row["evidence_id"]: row for row in combined.get("items", [])}
        if any(row != by_id.get(row.get("evidence_id")) for row in selected.get("items", [])):
            raise RuntimeError("route selected evidence changed original whole items")
        thickness = importlib.import_module("focused_research.local_campaign_guard").check_route_thickness_bounds(
            load(route_file), case_id=CASE,
        )
        save_new(CASE_ROOT / "THICKNESS_PREFLIGHT.json", thickness)
        save_new(CASE_ROOT / "SELECTED_EVIDENCE_DOWNSTREAM_BINDING.json", {
            "source_implementation_sha": IMPLEMENTATION_SHA,
            "active_file": str(CASE_ROOT / "routes" / "evidence_items.json"),
            "active_sha256": sha(CASE_ROOT / "routes" / "evidence_items.json"),
            "selected_ids": [row["evidence_id"] for row in selected.get("items", [])],
            "search_evidence_argument": str(CASE_ROOT / "routes" / "evidence_items.json"),
            "search_routes_argument": str(raw_route_file),
        })
        save_new(CASE_ROOT / "NATIVE_CANDIDATE_PREFLIGHT_BINDING.json",
                 _native_candidate_preflight_binding())

        search_stage = importlib.import_module("search_stage")
        save_new(CASE_ROOT / "06_search_BUDGET_BEFORE.json", read_only_budget())
        search_exit = _step(CASE_ROOT, "06_search", lambda: search_stage.main([
            "search", "--task-sheet", str(CASE_ROOT / "plan" / "task_sheet.json"),
            "--evidence-file", str(CASE_ROOT / "routes" / "evidence_items.json"),
            "--constraints-file", str(CASE_ROOT / "design_constraints.json"),
            "--routes-file", str(raw_route_file), "--optical-media", str(CASE_ROOT / "optical_media.json"),
            "--wavelengths", str(CASE_ROOT / "wavelengths.json"), "--cache-dir", str(CASE_ROOT / "cache"),
            "--max-forward-evaluations", "2048", "--max-new-evals", "64", "--max-workers", "2",
            "--backend", "torch", "--parameterization", "family", "--seed", "42", "--execute",
            "--output", str(CASE_ROOT / "search"),
        ]))
        status["stages"]["search_exit"] = search_exit
        initial_counts = _search_count_reconciliation(
            CASE_ROOT / "search", stage="initial", forward_cap=INITIAL_FORWARD_CAP,
            cert_cap=INITIAL_CERT_CAP,
        )
        initial_counts["budget_after"] = read_only_budget()
        save_new(CASE_ROOT / "06_search_COUNTS.json", initial_counts)
        previous = search_stage.read_search(CASE_ROOT / "search")
        if not previous.get("archive", {}).get("designs"):
            raise RuntimeError("initial search produced no certified observation; revision is not admitted")

        guard.set_stage("search_revision")
        save_new(CASE_ROOT / "07_revision_BUDGET_BEFORE.json", read_only_budget())
        try:
            revision_exit = _step(CASE_ROOT, "07_revision", lambda: search_stage.main([
                "revise", "--previous-search", str(CASE_ROOT / "search"),
                "--output", str(CASE_ROOT / "revision"), "--execute",
            ], planner=guard))
        finally:
            persist(guard.audit)
        status["stages"]["revision_exit"] = revision_exit
        plans = load(CASE_ROOT / "revision" / "refinement_plans.json")
        if plans.get("plans"):
            remaining_forward = TOTAL_FORWARD_CAP - initial_counts["accounted_forward_design_rows"]
            remaining_cert = TOTAL_CERT_CAP - initial_counts["actual_new_certification_evaluations"]
            refine_forward_cap = min(INITIAL_FORWARD_CAP, remaining_forward)
            refine_cert_cap = min(INITIAL_CERT_CAP, remaining_cert)
            if refine_forward_cap < 0 or refine_cert_cap < 0:
                raise RuntimeError("initial search consumed more than the cumulative T042 capacity")
            refine_budget = {
                "budget_before": read_only_budget(),
                "initial_counts": initial_counts,
                "cumulative_caps": {
                    "forward_design_rows": TOTAL_FORWARD_CAP,
                    "new_certification_evaluations": TOTAL_CERT_CAP,
                },
                "refinement_caps": {
                    "forward_design_rows": refine_forward_cap,
                    "new_certification_evaluations": refine_cert_cap,
                },
            }
            save_new(CASE_ROOT / "08_refine_BUDGET_BEFORE.json", refine_budget)
            refine_exit = _step(CASE_ROOT, "08_refine", lambda: search_stage.main([
                "refine", "--previous-search", str(CASE_ROOT / "search"),
                "--revision-dir", str(CASE_ROOT / "revision"), "--cache-dir", str(CASE_ROOT / "cache"),
                "--max-forward-evaluations", str(refine_forward_cap),
                "--max-new-evals", str(refine_cert_cap), "--max-workers", "2",
                "--backend", "torch", "--seed", "43", "--execute", "--output", str(CASE_ROOT / "refined"),
            ]))
            status["stages"]["refine_exit"] = refine_exit
            refine_counts = _search_count_reconciliation(
                CASE_ROOT / "refined", stage="refinement", forward_cap=refine_forward_cap,
                cert_cap=refine_cert_cap,
            )
            refine_counts["cumulative_actual_forward_design_rows"] = (
                initial_counts["actual_forward_design_rows"] + refine_counts["actual_forward_design_rows"])
            refine_counts["cumulative_accounted_forward_design_rows"] = (
                initial_counts["accounted_forward_design_rows"] + refine_counts["accounted_forward_design_rows"])
            refine_counts["cumulative_actual_new_certification_evaluations"] = (
                initial_counts["actual_new_certification_evaluations"] +
                refine_counts["actual_new_certification_evaluations"])
            if (refine_counts["cumulative_accounted_forward_design_rows"] > TOTAL_FORWARD_CAP or
                    refine_counts["cumulative_actual_new_certification_evaluations"] > TOTAL_CERT_CAP):
                raise RuntimeError("initial plus refinement counters exceed cumulative T042 capacity")
            save_new(CASE_ROOT / "08_refine_COUNTS.json", refine_counts)
        else:
            status["stages"]["refine"] = "empty_plan_stop"
        status["engineering_status"] = "completed_bounded_cycle"
        status["scientific_status"] = "inspect_actual_results_and_predictions_separately"
        return 0
    except Exception as exc:
        status.update({
            "engineering_status": "blocked",
            "failure_type": type(exc).__name__,
            "reason": str(exc),
            "scientific_status": "preserve_actual_results_without_acceptance_inference",
        })
        if CASE_ROOT.exists() and not (CASE_ROOT / "FAILURE.json").exists():
            save_new(CASE_ROOT / "FAILURE.json", {
                "type": type(exc).__name__, "reason": str(exc), "traceback": traceback.format_exc(),
            })
        return 2
    finally:
        if guard is not None and persist is not None:
            try:
                persist(guard.audit)
            except Exception as exc:
                if CASE_ROOT.exists() and not (CASE_ROOT / "GUARD_AUDIT_FAILURE.json").exists():
                    save_new(CASE_ROOT / "GUARD_AUDIT_FAILURE.json", {
                        "type": type(exc).__name__, "reason": str(exc),
                    })
        if money is not None:
            try:
                save_replace(CASE_ROOT / "BUDGET_STATUS.json", money.as_dict())
            except Exception as exc:
                if CASE_ROOT.exists() and not (CASE_ROOT / "BUDGET_STATUS_FAILURE.json").exists():
                    save_new(CASE_ROOT / "BUDGET_STATUS_FAILURE.json", {
                        "type": type(exc).__name__, "reason": str(exc),
                    })
        if CASE_ROOT.exists() and not (CASE_ROOT / "BUDGET_AFTER.json").exists():
            try:
                status["budget_after"] = read_only_budget()
                save_new(CASE_ROOT / "BUDGET_AFTER.json", status["budget_after"])
            except Exception as exc:
                status["budget_after_error"] = {"type": type(exc).__name__, "reason": str(exc)}
                if not (CASE_ROOT / "BUDGET_AFTER_ERROR.json").exists():
                    save_new(CASE_ROOT / "BUDGET_AFTER_ERROR.json", status["budget_after_error"])
        if CASE_ROOT.exists() and not (CASE_ROOT / "CASE_RESULT.json").exists():
            status["guard_audit"] = guard.audit if guard is not None else None
            save_new(CASE_ROOT / "CASE_RESULT.json", status)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("preflight", "case"))
    parser.add_argument("--case", choices=(CASE,), required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.action == "preflight":
        print(json.dumps(preflight(), ensure_ascii=False))
        return 0
    if not args.execute:
        raise RuntimeError("explicit --execute is required for the T042 case chain")
    return run_case()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
