"""Prepare and (only when explicitly enabled) run T028's bounded continuation.

The default ``preflight`` action is read-only with respect to both budget
databases and does not instantiate a budget guard or an HTTP client.  It
validates the full T027 refined archive, builds the exact compact decision
context, reconciles the two existing ledgers, and records the single-call
gate.  ``revise`` and ``refine`` are deliberately separate opt-in actions;
they are not run by this preparation turn.
"""
from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

TASK_ROOT = Path(r"D:\OptoMind-Article-3\local-r3-handoff\tasks\R3-T028")
SOURCE = Path(r"D:\OptoMind-Article-3\r3t028a")
T027_ROOT = TASK_ROOT.parent / "R3-T027"
T027_FULL = T027_ROOT / "refined"
T027_CACHE = T027_ROOT / "cache"
T027_WORKTREE = Path(r"D:\OptoMind-Article-3\r3t027a")
D20_ROOT = TASK_ROOT.parent / "R3-T019-DEDICATED20"
FOCUSED_ROOT = SOURCE / "focused_research"
VENDOR_ROOT = FOCUSED_ROOT / "vendor" / "review"
OLD_LEDGER = Path(r"D:\OptoMind-Article-3\local-functional-trial\20261013\budget.sqlite")
POOL_LEDGER = D20_ROOT / "private" / "budget.sqlite"
RUN_CONFIG = D20_ROOT / "RUN_CONFIG.local-only.json"
TOOLS_ROOT = TASK_ROOT.parent / "R3-T001-INTEGRATE-OFFLINE" / "release" / "local_tools"
INTERPRETER = Path(r"D:\OptoMind-Article-3\local-integration\20261013\envs\tensor\Scripts\python.exe")
ADAPTER = D20_ROOT / "transport" / "dedicated_qwen_adapter.py"
NATIVE_RUNTIME_EXPECTED_SHA = "7fa510a1b9da12a4403868259bb2bd124e36b0d2b28718226af485d74c00c005"
ADAPTER_EXPECTED_SHA = "b2a52f685d6d0c85abd56a5cdada9fd7eb787c7d2e53f2383e9a86431aee811d"
BUDGET_ENTRY_EXPECTED_SHA = "1a6832ade3f4b05d24a31102d106894d97e8e1f4a7af24883349783c4f88d512"
GUARD_EXPECTED_SHA = "d09c24cb6895790dc1c53a6807e743b09a8737135d65ec081c9b7c206060c461"
BASE_SHA = "b5f8a1dc2cfe8a203b74a742963bef7f9ac9a57b"
IMPLEMENTATION_SHA = "b6f641edea1fe124c98921251bc5f14a3dc53466"
T027_RESULT_SHA = "ff050c4c4600a0027d7a6b39c2c843956f05103c"
TASK_ID = "R3-T028"
MODEL = "qwen3.5-plus"
REPAIR_MODEL = "qwen3.7-flash"
MAX_TOKENS = 16384
TASK_CAP = Decimal("2")
PROJECT_CAP = Decimal("120")
CHAIN_CAP = Decimal("15")
POOL_CAP = Decimal("20")
MAX_HTTP = 1
MAX_FORWARD = 2048
MAX_NEW_CERTIFICATIONS = 32
MAX_WORKERS = 2
SEED = 44

# The preparation script lives outside the checkout.  Bind imports explicitly
# to the fixed clean worktree before loading any focused modules.  The current
# process may have an unrelated Article checkout on ``sys.path`` or in
# ``sys.modules``; both must be neutralized before loading the planner.
for _root in (SOURCE, VENDOR_ROOT, FOCUSED_ROOT):
    _root_text = str(_root)
    sys.path[:] = [item for item in sys.path if item != _root_text]
    sys.path.insert(0, _root_text)


class PreflightBlocked(RuntimeError):
    """A missing integrity, provenance, or budget prerequisite."""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() == encoded:
            return
        raise PreflightBlocked(f"refusing to overwrite existing evidence: {path}")
    path.write_bytes(encoded)


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str, cwd: Path = SOURCE) -> str:
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def module_from_path(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path.resolve(strict=True))
    if spec is None or spec.loader is None:
        raise PreflightBlocked(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _purge_module_tree(name: str) -> None:
    for loaded in tuple(sys.modules):
        if loaded == name or loaded.startswith(name + "."):
            del sys.modules[loaded]


def _namespace_package(name: str, directory: Path) -> Any:
    spec = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
    spec.submodule_search_locations = [str(directory.resolve(strict=True))]
    module = importlib.util.module_from_spec(spec)
    module.__path__ = list(spec.submodule_search_locations)
    module.__package__ = name
    sys.modules[name] = module
    return module


def _assert_source_identity() -> str:
    if not SOURCE.is_dir():
        raise PreflightBlocked(f"T028 worktree is missing: {SOURCE}")
    head = git("rev-parse", "HEAD")
    if head != IMPLEMENTATION_SHA:
        raise PreflightBlocked("T028 worktree is not at the fixed implementation SHA")
    tracked_status = git("status", "--porcelain=v1", "--untracked-files=no")
    if tracked_status:
        raise PreflightBlocked("T028 worktree has tracked changes")
    return head


def _bind_exact_imports() -> dict[str, Any]:
    """Bind the fixed vendor and focused modules, rejecting stale origins."""

    _assert_source_identity()
    importlib.invalidate_caches()
    for package in ("config", "llm", "optomind_research", "focused_research"):
        _purge_module_tree(package)

    # Load the package roots from exact files.  In particular, the local
    # focused checkout is a namespace package, so normal import precedence can
    # otherwise select an unrelated regular package from the caller's cwd.
    module_from_path(VENDOR_ROOT / "config" / "__init__.py", "config")
    module_from_path(VENDOR_ROOT / "llm" / "__init__.py", "llm")
    module_from_path(VENDOR_ROOT / "optomind_research" / "__init__.py", "optomind_research")
    _namespace_package("focused_research", FOCUSED_ROOT)

    module_from_path(VENDOR_ROOT / "config" / "secret_pool.py", "config.secret_pool")
    module_from_path(VENDOR_ROOT / "config" / "qwen_config.py", "config.qwen_config")
    module_from_path(VENDOR_ROOT / "config" / "model_router.py", "config.model_router")
    module_from_path(VENDOR_ROOT / "llm" / "qwen_chat_client.py", "llm.qwen_chat_client")
    planner = module_from_path(
        VENDOR_ROOT / "optomind_research" / "query_planner.py",
        "optomind_research.query_planner",
    )
    routes = importlib.import_module("focused_research.research_routes")
    stage = importlib.import_module("focused_research.search_stage")

    expected = {
        "config.qwen_config": VENDOR_ROOT / "config" / "qwen_config.py",
        "llm.qwen_chat_client": VENDOR_ROOT / "llm" / "qwen_chat_client.py",
        "optomind_research.query_planner": VENDOR_ROOT / "optomind_research" / "query_planner.py",
        "focused_research.research_routes": FOCUSED_ROOT / "research_routes.py",
        "focused_research.search_stage": FOCUSED_ROOT / "search_stage.py",
    }
    origins: dict[str, str] = {}
    for name, path in expected.items():
        loaded = importlib.import_module(name)
        actual = Path(getattr(loaded, "__file__", "")).resolve()
        wanted = path.resolve(strict=True)
        if actual != wanted:
            raise PreflightBlocked(f"stale import origin for {name}: {actual}")
        origins[name] = str(actual)
    if planner is not importlib.import_module("optomind_research.query_planner"):
        raise PreflightBlocked("planner binding changed while checking import origins")
    return {"origins": origins, "planner": planner, "routes": routes, "stage": stage}


# Keep the wrapper safe even when a caller uses ``runpy.run_path`` and then
# imports the focused modules itself.  This is module loading only: no client,
# ledger, credential, HTTP, or numerical object is constructed here.
_IMPORT_ORIGINS_AT_LOAD = _bind_exact_imports()["origins"]


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def _read_ledger(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PreflightBlocked(f"missing ledger: {path}")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        meta = dict(db.execute("SELECT key,value FROM budget_meta"))
        rows = list(db.execute("SELECT status,amount_cny,actual_cny FROM reservations"))
        cap = _decimal(meta["limit_cny"])
        settled = sum((_decimal(row["actual_cny"]) for row in rows if row["status"] == "settled"), Decimal(0))
        held = sum((_decimal(row["amount_cny"]) for row in rows if row["status"] != "settled"), Decimal(0))
    return {
        "path": str(path),
        "cap_cny": str(cap),
        "settled_cny": str(settled),
        "held_cny": str(held),
        "unresolved_count": sum(row["status"] != "settled" for row in rows),
        "reservation_count": len(rows),
        "sha256": sha(path),
    }


def _read_chain() -> dict[str, Any]:
    tasks = {"R3-T022", "R3-T025", "R3-T026", "R3-T027", TASK_ID}
    with sqlite3.connect(POOL_LEDGER.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = list(db.execute(
            "SELECT r.status,r.amount_cny,r.actual_cny,c.task_id "
            "FROM reservations r JOIN r3_task_calls c USING(reservation_id)"
        ))
    chain = sum(
        (_decimal(row["actual_cny"] if row["status"] == "settled" else row["amount_cny"])
         for row in rows if row["task_id"] in tasks),
        Decimal(0),
    )
    task_rows = [row for row in rows if row["task_id"] == TASK_ID]
    task_exposure = sum(
        (_decimal(row["actual_cny"] if row["status"] == "settled" else row["amount_cny"])
         for row in task_rows),
        Decimal(0),
    )
    return {
        "chain_tasks": sorted(tasks),
        "chain_exposure_cny": str(chain),
        "chain_call_rows": len([row for row in rows if row["task_id"] in tasks]),
        "task_exposure_cny": str(task_exposure),
        "task_call_rows": len(task_rows),
    }


def fresh_reconcile() -> dict[str, Any]:
    """Read both existing ledgers using SQLite ``mode=ro`` only."""

    old = _read_ledger(OLD_LEDGER)
    pool = _read_ledger(POOL_LEDGER)
    chain = _read_chain()
    total = _decimal(old["settled_cny"]) + _decimal(old["held_cny"])
    total += _decimal(pool["settled_cny"]) + _decimal(pool["held_cny"])
    result = {"old_140_ledger": old, "existing20_pool": pool, **chain,
              "aggregate_exposure_cny": str(total), "read_only": True}
    if old["cap_cny"] != "140.0":
        raise PreflightBlocked("old ledger metadata cap changed from 140 CNY")
    if pool["cap_cny"] != "20.0":
        raise PreflightBlocked("existing dedicated pool metadata cap changed from 20 CNY")
    if old["unresolved_count"] or pool["unresolved_count"]:
        raise PreflightBlocked("pending/held/uncertain ledger state requires stopping")
    return result


def gate_for(reconcile: Mapping[str, Any], reservation_cny: Decimal) -> dict[str, Any]:
    total = _decimal(reconcile["aggregate_exposure_cny"])
    chain = _decimal(reconcile["chain_exposure_cny"])
    pool = _decimal(reconcile["existing20_pool"]["settled_cny"]) + _decimal(reconcile["existing20_pool"]["held_cny"])
    task = _decimal(reconcile["task_exposure_cny"])
    checks = {
        "project_120_including_reservation": total + reservation_cny <= PROJECT_CAP,
        "chain_15_including_reservation": chain + reservation_cny <= CHAIN_CAP,
        "existing_pool_20_including_reservation": pool + reservation_cny <= POOL_CAP,
        "task_2_including_reservation": task + reservation_cny <= TASK_CAP,
        "task_attempt_max_1": int(reconcile["task_call_rows"]) < MAX_HTTP,
        "no_unresolved_ledger_state": bool(
            reconcile["old_140_ledger"]["unresolved_count"] == 0
            and reconcile["existing20_pool"]["unresolved_count"] == 0
        ),
        "old_metadata_140_preserved": reconcile["old_140_ledger"]["cap_cny"] == "140.0",
        "pool_metadata_20_preserved": reconcile["existing20_pool"]["cap_cny"] == "20.0",
    }
    return {
        "effective_project_cap_cny": str(PROJECT_CAP),
        "old_ledger_metadata_cap_cny": "140.0",
        "existing_pool_cap_cny": str(POOL_CAP),
        "chain_cap_cny": str(CHAIN_CAP),
        "task_cap_cny": str(TASK_CAP),
        "reservation_cny": str(reservation_cny),
        "checks": checks,
        "passed": all(checks.values()),
        "model_http_max": MAX_HTTP,
        "model_retries": 0,
        "repair_http_max": 0,
        "new_budget_pool": False,
    }


def _source_context(previous: Mapping[str, Any], stage: Any) -> dict[str, Any]:
    manifest = load_json(T027_FULL / "manifest.json")
    entries = [
        {"name": name, "canonical_json_sha256": expected,
         "bytes": (T027_FULL / name).stat().st_size}
        for name, expected in sorted(manifest.items())
    ]
    context = previous["context"]
    context_files = {}
    for key, filename in stage.CONTEXT_FILES.items():
        path = T027_FULL / filename
        context_files[key] = {"path": str(path), "bytes": path.stat().st_size,
                              "canonical_json_sha256": digest(context[key])}
    media = context["optical_media"]
    media_summary = {
        alias: {key: value[key] for key in ("provider", "dataset_id", "constant_n") if key in value}
        for alias, value in media.items()
    }
    wavelengths = context["wavelengths_nm"]
    task_sheet = context["sheet"]
    diagnostics = previous["diagnostics"]
    scopes: dict[str, int] = {}
    all_goals_retained = True
    for row in diagnostics:
        diagnosis = row.get("diagnostics", row) if isinstance(row, Mapping) else {}
        scope = str(diagnosis.get("claim_scope", ""))
        scopes[scope] = scopes.get(scope, 0) + 1
        all_goals_retained = all_goals_retained and diagnosis.get("all_goals_retained") is True
    return {
        "path": str(T027_FULL),
        "read_search_passed": True,
        "manifest_sha256": previous["manifest_sha256"],
        "manifest_entries": entries,
        "context_files": context_files,
        "input_bindings": load_json(T027_FULL / "input_bindings.json"),
        "optical_media": media_summary,
        "wavelengths_nm": {"count": len(wavelengths), "first": wavelengths[0],
                           "last": wavelengths[-1], "step": wavelengths[1] - wavelengths[0]},
        "objectives": [
            {"objective_id": metric["objective_id"], "metric": metric["metric"],
             "sense": metric["sense"], "region": metric["region"]}
            for goal in task_sheet["subgoals"]
            if "executable_metric" in goal
            for metric in [goal["executable_metric"]]
        ],
        "constraints": load_json(T027_FULL / "design_constraints.json"),
        "archive": {
            "design_count": len(previous["archive"]["designs"]),
            "evaluation_count": len(previous["archive"]["evaluations"]),
            "pareto_count": len(previous["archive"].get("pareto_design_ids", [])),
            "claim_scope": previous["archive"].get("claim_scope"),
        },
        "envelope_count": len(previous["envelopes"]),
        "diagnostics": {"count": len(diagnostics), "claim_scopes": scopes,
                        "all_goals_retained": all_goals_retained},
        "summary": {
            "status": load_json(T027_FULL / "summary.json").get("status"),
            "candidate_count": load_json(T027_FULL / "summary.json").get("candidate_count"),
            "certified_design_count": load_json(T027_FULL / "summary.json").get("certified_design_count"),
        },
    }


def _decision_prompt(previous: Mapping[str, Any], stage: Any, native: Any) -> dict[str, Any]:
    aliases = stage._aliases(previous)
    # This is the exact prompt construction used by search_stage.revise.  It is
    # prepared offline so the parent can review the wire size and cost bound.
    decision_view = importlib.import_module("focused_research.decision_context").build_decision_context(
        previous, aliases, stage._latest_local_derivative
    )
    system_prompt = (
        "Return JSON only, exactly {\"plans\":[...]}. Plan numerical refinement, never layer JSON. "
        "Each plan has parent_design_id, action (refine_family or refine_layers), diagnosis "
        "(diagnosis_id, observation_ids, explanation citing concrete measured changes), "
        "expected_goal_changes (every original objective_id mapped to increase/decrease/unchanged/unknown). "
        "Use supplied short parent_alias P001 etc as parent_design_id, and diagnosis_alias D001 etc as diagnosis_id. "
        "Choose only supplied certified parents. Preserve all original objectives and physical conditions. "
        "refine_family preserves shared variables; refine_layers explicitly creates independent layer variables "
        "within original family bounds. Expectations are untested, not outcomes. No route or plan-count ceiling. "
        "Interpret measurements, local derivative arithmetic, and hypotheses separately. "
        "For a decreasing variable reverse the derivative sign; respect each goal sense and the supplied bounds. "
        "Local directional estimates do not establish finite-step outcomes or a unique mechanism. "
        "Explain why the chosen parent and action are informative; nearby parents need a reason, not a diversity quota. "
        "Exploratory actions, predicted tradeoffs, and unsuccessful hypotheses are legitimate; do not promise improvement. "
        "An empty plans list is allowed. Do not fabricate observations, material identities or new bounds."
    )
    user_payload = decision_view["user_payload"]
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
    wire_body, _ = native.build_qwen_wire_body(
        messages, model=MODEL, max_output_tokens=MAX_TOKENS, thinking=False,
        thinking_budget=0, json_mode=True, stream=False, temperature=0.1,
    )
    estimate = native.estimate_prompt_tokens(wire_body, messages, prompt_token_counter=None)
    reservation = Decimal(str(native.estimated_cost_cny(
        {"prompt_tokens": estimate["prompt_tokens"], "completion_tokens": MAX_TOKENS},
        model=MODEL, conservative=True,
    )))
    return {
        "system_prompt": system_prompt,
        "user_payload": user_payload,
        "selection_report": decision_view["selection_report"],
        "messages": messages,
        "wire_body_sha256": hashlib.sha256(wire_body).hexdigest(),
        "wire_body_bytes": len(wire_body),
        "native_estimator": estimate,
        "prompt_upper_bound": estimate["prompt_tokens"],
        "reservation_cny": str(reservation),
        "max_output_tokens": MAX_TOKENS,
    }


def _import_origins() -> dict[str, str]:
    # This imports module definitions only; it does not construct a client or
    # read a credential value.  The exact-origin binder also purges any stale
    # packages inherited from the caller's process.
    return _bind_exact_imports()["origins"]


def _preflight() -> dict[str, Any]:
    if not INTERPRETER.is_file():
        raise PreflightBlocked(f"original tensor interpreter missing: {INTERPRETER}")
    _assert_source_identity()
    if not T027_FULL.is_dir() or not (T027_FULL / "manifest.json").is_file():
        raise PreflightBlocked("full T027 refined directory/manifest is missing")

    binding = _bind_exact_imports()
    import_stage = binding["stage"]
    previous = import_stage.read_search(T027_FULL)
    origins = binding["origins"]
    adapter = module_from_path(ADAPTER, "_t028_original_d20_adapter")
    config = load_json(RUN_CONFIG)
    native_path = Path(config["paths"]["review_runtime"])
    native_sha = sha(native_path)
    if native_sha != NATIVE_RUNTIME_EXPECTED_SHA:
        raise PreflightBlocked("native runtime hash differs from the preserved T027 supplier")
    native = adapter.load_native_runtime(native_path)
    pricing = native.model_pricing(MODEL)
    resolver, resolver_sources = adapter._load_vendored_model_resolver(
        Path(origins["optomind_research.query_planner"])
    )
    del resolver
    if not callable(getattr(importlib.import_module("optomind_research.query_planner"), "call_qwen_chat", None)):
        raise PreflightBlocked("vendored planner call seam unavailable")

    reconcile = fresh_reconcile()
    prompt = _decision_prompt(previous, import_stage, native)
    reservation = Decimal(prompt["reservation_cny"])
    gate = gate_for(reconcile, reservation)
    if not gate["passed"]:
        raise PreflightBlocked("one or more bounded budget gates failed")

    physics_failures_path = T027_ROOT / "PHYSICS_FAILURES.json"
    physics_failures = load_json(physics_failures_path)
    r7_failures = [row for row in physics_failures.get("failed_certifications", []) if row.get("route_id") == "R7"]
    test_stdout = sorted((TASK_ROOT / "prep").glob("OFFLINE_TARGETED_TESTS_*.stdout.log"), key=lambda p: p.stat().st_mtime)
    test_stdout = test_stdout[-1] if test_stdout else None
    test_stderr = test_stdout.with_name(test_stdout.name.replace(".stdout.log", ".stderr.log")) if test_stdout else None
    test_text = test_stdout.read_text(encoding="utf-8") if test_stdout else ""
    passed_match = re.search(r"(?m)^(\d+) passed in ", test_text)
    if not test_stdout or not passed_match or passed_match.group(1) != "30":
        raise PreflightBlocked("targeted offline test evidence is missing or not 30 passed")

    prepared_context = {
        "schema": "r3-t028-precall-context.v1",
        "task_id": TASK_ID,
        "source_sha": git("rev-parse", "HEAD"),
        "previous_search": str(T027_FULL),
        "previous_manifest_sha256": previous["manifest_sha256"],
        "prompt": prompt,
        "model": MODEL,
        "transport": {"http_max": MAX_HTTP, "retries": 0, "repair_http_max": 0,
                       "original_audited_wrapper": str(ADAPTER),
                       "native_runtime": str(native_path)},
    }
    context_path = TASK_ROOT / "prep" / "T028_PRECALL_CONTEXT.json"
    audit_path = TASK_ROOT / "prep" / "T028_PRECALL_SELECTION_AUDIT.json"
    review_path = TASK_ROOT / "prep" / "T028_PRECALL_REVIEW.json"
    write_new(context_path, prepared_context)
    write_new(audit_path, prompt["selection_report"])

    source_summary = _source_context(previous, import_stage)
    review = {
        "schema": "r3-t028-precall-review.v1",
        "status": "ready_for_parent_review",
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "paid_or_model_execution": False,
        "numerical_execution": False,
        "source": {
            "worktree": str(SOURCE), "implementation_sha": git("rev-parse", "HEAD"),
            "base_sha": BASE_SHA, "t027_completed_result_sha": T027_RESULT_SHA,
            "worktree_clean": not bool(git("status", "--porcelain=v1")),
            "tracked_clean": not bool(git("status", "--porcelain=v1", "--untracked-files=no")),
            "t027_worktree": str(T027_WORKTREE),
            "t027_worktree_head": git("rev-parse", "HEAD", cwd=T027_WORKTREE),
        },
        "interpreter": {
            "path": str(INTERPRETER), "version": sys.version,
            "module_origins": origins,
            "torch_expected": "2.14.1+cpu",
            "scipy_expected": "1.17.0",
            "tmm_expected": "0.2.0",
        },
        "offline_tests": {
            "command": f'"{INTERPRETER}" -B -X utf8 -m pytest -q -p no:cacheprovider --basetemp <T028-prep-temp> focused_research/tests/test_decision_context.py focused_research/tests/test_search_stage.py',
            "stdout": str(test_stdout), "stderr": str(test_stderr) if test_stderr else None,
            "passed": int(passed_match.group(1)), "stderr_bytes": test_stderr.stat().st_size if test_stderr else None,
        },
        "full_t027": source_summary,
        "r7_failure_certificates": {
            "source": str(physics_failures_path), "source_sha256": sha(physics_failures_path),
            "route_instance_pairs": [
                {"route_id": row.get("route_id"), "instance_id": row.get("instance_id"),
                 "codes": [failure.get("code") for failure in row.get("failures", [])]}
                for row in r7_failures
            ],
            "preserve_unchanged": True,
        },
        "budget_reconcile": reconcile,
        "budget_gate": gate,
        "fixed_supplier": {
            "adapter": {"path": str(ADAPTER), "sha256": sha(ADAPTER), "expected_sha256": ADAPTER_EXPECTED_SHA},
            "budget_entry": {"path": str(TOOLS_ROOT / "budgeted_entry.py"), "sha256": sha(TOOLS_ROOT / "budgeted_entry.py"), "expected_sha256": BUDGET_ENTRY_EXPECTED_SHA},
            "task_guard": {"path": str(TOOLS_ROOT / "task_budget_guard.py"), "sha256": sha(TOOLS_ROOT / "task_budget_guard.py"), "expected_sha256": GUARD_EXPECTED_SHA},
            "native_runtime": {"path": str(native_path), "sha256": native_sha, "expected_sha256": NATIVE_RUNTIME_EXPECTED_SHA},
            "model": MODEL, "repair_model": REPAIR_MODEL, "resolver_sources": {key: str(value) for key, value in resolver_sources.items()},
        },
        "continuation_contract": {
            "revision_previous_search": str(T027_FULL), "revision_output": str(TASK_ROOT / "revision"),
            "refine_output": str(TASK_ROOT / "refined"), "cache_dir": str(T027_CACHE),
            "max_forward_evaluations": MAX_FORWARD, "max_new_evals": MAX_NEW_CERTIFICATIONS,
            "max_workers": MAX_WORKERS, "backend": "torch", "seed": SEED,
            "failure_and_finite_difference_charge": "inherited from search_stage; every attempted evaluation counts",
            "cache_hits": "reported separately by search_stage metering",
            "manual_model_plan_repair": False, "forced_design_on_empty_plan": False,
            "no_new_pool": True, "no_extra_stage": True,
        },
        "artifacts": {
            "prepared_context": str(context_path), "selection_audit": str(audit_path),
            "review": str(review_path),
        },
        "next_actions": [
            "Parent reviews this checkpoint and explicitly enables the one-call revise action.",
            "Run revise with the exact audited planner injection; stop if the response is empty/invalid or the call gate changes.",
            "Only if valid plans are saved, run the exact bounded torch refine command with seed 44 and reuse T027 cache.",
        ],
    }
    write_new(review_path, review)
    return review


def _assert_review() -> dict[str, Any]:
    current_sha = _assert_source_identity()
    path = TASK_ROOT / "prep" / "T028_PRECALL_REVIEW.json"
    review = load_json(path)
    if review.get("status") != "ready_for_parent_review" or review.get("paid_or_model_execution"):
        raise PreflightBlocked("pre-call review is absent or not a no-call checkpoint")
    if review.get("source", {}).get("implementation_sha") != current_sha:
        raise PreflightBlocked("pre-call review source SHA differs from the fixed worktree")
    return review


def _prepared_wire_expectation() -> dict[str, Any]:
    path = TASK_ROOT / "prep" / "T028_PRECALL_CONTEXT.json"
    prepared = load_json(path)
    if prepared.get("source_sha") != IMPLEMENTATION_SHA:
        raise PreflightBlocked("prepared prompt source SHA differs from the fixed implementation")
    prompt = prepared.get("prompt")
    if not isinstance(prompt, Mapping):
        raise PreflightBlocked("prepared prompt is missing")
    expected_sha = str(prompt.get("wire_body_sha256") or "")
    expected_bytes = prompt.get("wire_body_bytes")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha) or type(expected_bytes) is not int:
        raise PreflightBlocked("prepared prompt wire binding is malformed")
    return {"wire_body_sha256": expected_sha, "wire_body_bytes": expected_bytes}


def _build_audited_planner(review: Mapping[str, Any]) -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    """Construct the original wrapper and native supplier after explicit opt-in."""

    current_sha = _assert_source_identity()
    if review.get("source", {}).get("implementation_sha") != current_sha:
        raise PreflightBlocked("review source SHA differs before paid planner construction")
    binding = _bind_exact_imports()
    import_stage = binding["stage"]
    planner_module = binding["planner"]
    adapter_module = module_from_path(ADAPTER, "_t028_original_d20_adapter_paid")
    config_old = load_json(RUN_CONFIG)
    native_path = Path(config_old["paths"]["review_runtime"])
    native = adapter_module.load_native_runtime(native_path)
    entry, guard_module = adapter_module._load_budget_factory(
        TOOLS_ROOT / "budgeted_entry.py", TOOLS_ROOT / "task_budget_guard.py"
    )
    ledger = native.GlobalBudgetLedger(limit_cny=20, path=POOL_LEDGER)
    guard = guard_module.TaskBudgetGuard(
        ledger, task_id=TASK_ID, lane="certified_parent_refinement_decision",
        task_limit_cny=float(TASK_CAP), max_calls=MAX_HTTP,
        checkpoint_cny=None, global_limit_cny=float(POOL_CAP),
    )
    config = adapter_module.Dedicated20Config(
        ledger_path=POOL_LEDGER, audit_root=TASK_ROOT / "private" / "audit",
        key_file=Path(config_old["paths"]["key_file"]), task_id=TASK_ID,
        model=MODEL, repair_model=REPAIR_MODEL, task_limit_cny=float(TASK_CAP),
        global_limit_cny=float(POOL_CAP), max_calls=MAX_HTTP, max_retries=0,
        max_keys=1, max_tokens=MAX_TOKENS,
    )
    resolver, _ = adapter_module._load_vendored_model_resolver(Path(planner_module.__file__))
    audited = entry.make_audited_client(native, guard, config.factory_config())
    adapter = adapter_module.FocusedQwenCallAdapter(
        audited_client_factory=audited, runtime=native, config=config,
        guard=guard, model_resolver=resolver,
    )
    return import_stage, planner_module, adapter_module, native, guard, adapter, review


def _revise() -> int:
    review = _assert_review()
    expected_wire = _prepared_wire_expectation()
    if (TASK_ROOT / "revision").exists() or (TASK_ROOT / "REVISION_STARTED.json").exists():
        raise PreflightBlocked("revision output already exists; refusing a repeated model attempt")
    reconcile_before = fresh_reconcile()
    reservation = Decimal(review["budget_gate"]["reservation_cny"])
    if not gate_for(reconcile_before, reservation)["passed"]:
        raise PreflightBlocked("fresh budget gate changed before model call")
    stage, planner_module, adapter_module, native, guard, adapter, _ = _build_audited_planner(review)
    previous = stage.read_search(T027_FULL)
    calls = 0
    original_call = planner_module.call_qwen_chat
    started = {"started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "source_sha": git("rev-parse", "HEAD"), "model_http_max": MAX_HTTP,
               "retries": 0, "repair_http_max": 0, "budget_before": reconcile_before}
    write_new(TASK_ROOT / "REVISION_STARTED.json", started)

    def protected_call(agent_name: str, messages: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        if calls >= MAX_HTTP:
            raise PreflightBlocked("T028 allows one primary model HTTP attempt")
        if str(agent_name) != "QueryPlannerAgent" or str(kwargs.get("model_tier") or "") in {"standard_model", "QueryPlannerFormatRepairAgent"}:
            raise PreflightBlocked("format-repair or alternate planner HTTP is forbidden")
        if kwargs.get("force_mock") is not False or kwargs.get("response_format") != {"type": "json_object"}:
            raise PreflightBlocked("planner transport contract changed")
        if kwargs.get("stream") not in (None, False) or kwargs.get("temperature") != 0.1:
            raise PreflightBlocked("planner transport controls changed")
        max_tokens = kwargs.get("max_tokens")
        if max_tokens != MAX_TOKENS:
            raise PreflightBlocked("planner output-token bound changed")
        body_bytes, _ = native.build_qwen_wire_body(
            messages, model=MODEL, max_output_tokens=MAX_TOKENS,
            thinking=False, thinking_budget=0, json_mode=True, stream=False, temperature=0.1,
        )
        wire_sha = hashlib.sha256(body_bytes).hexdigest()
        if wire_sha != expected_wire["wire_body_sha256"] or len(body_bytes) != expected_wire["wire_body_bytes"]:
            raise PreflightBlocked("prepared prompt wire binding differs before spending")
        status = fresh_reconcile()
        estimate = native.estimate_prompt_tokens(body_bytes, messages, prompt_token_counter=None)
        predicted = Decimal(str(native.estimated_cost_cny(
            {"prompt_tokens": estimate["prompt_tokens"], "completion_tokens": MAX_TOKENS},
            model=MODEL, conservative=True,
        )))
        gate = gate_for(status, predicted)
        if not gate["passed"]:
            raise PreflightBlocked("fresh pre-call budget gate failed")
        calls += 1
        write_new(TASK_ROOT / "CALL_1_PREFLIGHT.json", {
            "task_id": TASK_ID, "model": MODEL, "max_output_tokens": MAX_TOKENS,
            "prompt_upper_bound": estimate["prompt_tokens"],
            "expected_wire_body_sha256": expected_wire["wire_body_sha256"],
            "wire_body_sha256": wire_sha,
            "wire_body_bytes": len(body_bytes), "reservation_cny": str(predicted),
            "native_estimator": estimate, "budget": status, "budget_gate": gate,
            "transport_retries": 0, "repair_http_max": 0,
        })
        return adapter(agent_name, messages, **kwargs)

    planner_module.call_qwen_chat = protected_call
    exit_code = 2
    try:
        planner = planner_module.QueryPlannerAgent(max_tokens=MAX_TOKENS)
        exit_code = stage.main([
            "revise", "--previous-search", str(T027_FULL), "--execute",
            "--output", str(TASK_ROOT / "revision"),
        ], planner=planner)
        return exit_code
    finally:
        planner_module.call_qwen_chat = original_call
        after = fresh_reconcile()
        write_new(TASK_ROOT / "REVISION_ACCOUNTING.json", {
            "exit_code": exit_code, "stage_model_invocations": calls,
            "budget_after": after, "expected_max_http": MAX_HTTP,
            "prior_manifest_sha256": previous["manifest_sha256"],
        })


def _refine() -> int:
    review = _assert_review()
    revision = TASK_ROOT / "revision"
    plans_path = revision / "refinement_plans.json"
    if not plans_path.is_file():
        raise PreflightBlocked("revision plans are missing; stop before numerical execution")
    plans = load_json(plans_path)
    if not plans.get("plans"):
        write_new(TASK_ROOT / "T028_EMPTY_PLAN_STOP.json", {
            "task_id": TASK_ID, "status": "empty_plan_stop", "forced_design": False,
            "revision": str(revision), "reason": "audited planner returned no valid plans",
        })
        return 0
    if (TASK_ROOT / "refined").exists():
        raise PreflightBlocked("refined output already exists; refusing repeated numerical execution")
    stage = _bind_exact_imports()["stage"]
    stage_args = [
        "refine", "--previous-search", str(T027_FULL), "--revision-dir", str(revision),
        "--cache-dir", str(T027_CACHE), "--max-forward-evaluations", str(MAX_FORWARD),
        "--max-new-evals", str(MAX_NEW_CERTIFICATIONS), "--max-workers", str(MAX_WORKERS),
        "--backend", "torch", "--seed", str(SEED), "--execute", "--output", str(TASK_ROOT / "refined"),
    ]
    return stage.main(stage_args)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("preflight", "origins", "revise", "refine"), nargs="?", default="preflight")
    parser.add_argument("--allow-paid", action="store_true", help="explicitly opt into the one model HTTP attempt")
    parser.add_argument("--allow-numerical", action="store_true", help="explicitly opt into bounded Torch refinement")
    args = parser.parse_args()
    if args.stage == "preflight":
        review = _preflight()
        print(json.dumps({"status": review["status"], "review": str(TASK_ROOT / "prep" / "T028_PRECALL_REVIEW.json"),
                          "manifest_sha256": review["full_t027"]["manifest_sha256"],
                          "aggregate_exposure_cny": review["budget_reconcile"]["aggregate_exposure_cny"],
                          "reservation_cny": review["budget_gate"]["reservation_cny"],
                          "paid_or_model_execution": False}, ensure_ascii=False))
        return 0
    if args.stage == "origins":
        binding = _bind_exact_imports()
        print(json.dumps({"status": "imports_bound_no_client", "source": str(SOURCE),
                          "source_sha": git("rev-parse", "HEAD"),
                          "origins": binding["origins"], "client_constructed": False,
                          "paid_or_model_execution": False, "numerical_execution": False},
                         ensure_ascii=False))
        return 0
    if args.stage == "revise":
        if not args.allow_paid:
            raise PreflightBlocked("revise requires explicit --allow-paid after parent review")
        return _revise()
    if not args.allow_numerical:
        raise PreflightBlocked("refine requires explicit --allow-numerical after parent review")
    return _refine()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PreflightBlocked as exc:
        print("T028 preparation blocked: " + str(exc), file=sys.stderr)
        raise SystemExit(2)
