"""O-13: research console -- API bridge + static UI over the local portal.

Read-only local console for research state, the human-in-the-loop
confirmation inbox (the first real implementation of the O-12 responder
protocol), and the event tail. The bridge is a SELF-CONTAINED stdlib
http.server (ThreadingHTTPServer) with the same loopback-only discipline as
the existing portal: one port, zero new dependencies, no build step. The
server binds 127.0.0.1 only -- no auth is added BECAUSE the listener never
leaves the loopback (enforced below and documented).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional
from urllib.parse import unquote, urlparse

CONFIRMATIONS_DIRNAME = "confirmations"
EVENTS_FILENAME = "EVENTS.jsonl"
PROJECTION_FILES = ("CURRENT_STATE.json", "BUDGET_STATE.json", "PROGRESS.json")
MAX_EVENT_TAIL = 100


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _empty_status() -> Dict[str, Any]:
    return {
        "schema_version": "console.status.v1",
        "run_id": None,
        "stage": "no_run",
        "active_hypotheses": 0,
        "finished_hypotheses": 0,
        "verified_candidates": 0,
        "budget": {"rounds_started": 0, "routes": 0},
        "open_questions": [],
        "scoring_standard": None,
    }


def api_status(run_dir: Optional[Path]) -> Dict[str, Any]:
    """Console status shape, rebuilt from the run's O-03 projections."""

    if run_dir is None:
        return _empty_status()
    run_dir = Path(run_dir)
    current = _read_json(run_dir / "CURRENT_STATE.json")
    if not isinstance(current, Mapping):
        return _empty_status()
    progress = _read_json(run_dir / "PROGRESS.json") or {}
    routes = current.get("routes") or []
    active = [row for row in routes if row.get("status") == "racing"]
    standard = current.get("scoring_standard_frozen") or {}
    return {
        "schema_version": "console.status.v1",
        "run_id": run_dir.name,
        "stage": "completed" if progress.get("run_completed") else "running",
        "active_hypotheses": len(active),
        "finished_hypotheses": len(routes) - len(active),
        "verified_candidates": sum(
            1 for row in routes if row.get("best_score") is not None
        ),
        "budget": {
            "rounds_started": current.get("rounds_started") or 0,
            "routes": len(routes),
        },
        "open_questions": [row.get("route_id") for row in active],
        "scoring_standard": standard.get("formula"),
    }


def api_runs(output_root: Optional[Path]) -> Dict[str, Any]:
    """Run summaries across the outputs root, newest first."""

    runs: List[Dict[str, Any]] = []
    if output_root is not None and Path(output_root).is_dir():
        for child in sorted(Path(output_root).iterdir(), reverse=True):
            if not child.is_dir():
                continue
            progress = _read_json(child / "PROGRESS.json") or {}
            runs.append(
                {
                    "run_id": child.name,
                    "events": progress.get("events"),
                    "routes": progress.get("routes"),
                    "rounds_started": progress.get("rounds_started"),
                    "run_completed": bool(progress.get("run_completed")),
                }
            )
    return {"schema_version": "console.runs.v1", "runs": runs}


def api_run(run_dir: Optional[Path]) -> Dict[str, Any]:
    if run_dir is None:
        return {"schema_version": "console.run.v1", "status": _empty_status(), "projections": {}}
    run_dir = Path(run_dir)
    return {
        "schema_version": "console.run.v1",
        "run_id": run_dir.name,
        "status": api_status(run_dir),
        "projections": {
            name: _read_json(run_dir / name) for name in PROJECTION_FILES
        },
    }


def api_confirmations(run_dir: Optional[Path]) -> Dict[str, Any]:
    """Every PENDING confirmation with its full payload + schema hints."""

    pending: List[Dict[str, Any]] = []
    if run_dir is not None:
        confirmations_dir = Path(run_dir) / CONFIRMATIONS_DIRNAME
        if confirmations_dir.is_dir():
            for path in sorted(confirmations_dir.glob("PENDING_*.json")):
                payload = _read_json(path)
                if isinstance(payload, Mapping):
                    pending.append(payload)
    return {"schema_version": "console.confirmations.v1", "pending": pending}


def api_resolve_confirmation(
    run_dir: Optional[Path],
    gate_id: str,
    *,
    decision: str,
    payload_after: Any = None,
    responder: str = "console",
) -> Dict[str, Any]:
    """Write the RESOLVED file. The SERVER computes the diff and timestamp;
    client-supplied diffs are never trusted. A late POST (the gate already
    auto-accepted) is refused with 409 semantics and quarantined."""

    if decision not in {"accepted", "modified", "rejected"}:
        return {"error": f"unknown decision {decision!r}", "status": 400}
    if run_dir is None:
        return {"error": "no run bound", "status": 404}
    confirmations_dir = Path(run_dir) / CONFIRMATIONS_DIRNAME
    resolved_path = confirmations_dir / f"RESOLVED_{gate_id}.json"
    pending_path = confirmations_dir / f"PENDING_{gate_id}.json"
    original = _read_json(pending_path) or {}
    original_payload = original.get("payload")
    if resolved_path.is_file():
        existing = _read_json(resolved_path) or {}
        if str(existing.get("responder") or "").startswith("auto:"):
            # Quarantine the late attempt; the auto decision stands.
            late_path = confirmations_dir / f"LATE_{gate_id}.json"
            late_path.write_text(
                json.dumps(
                    {"decision": decision, "payload_after": payload_after},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return {
                "error": "already auto-accepted by timeout",
                "status": 409,
                "quarantined_to": late_path.name,
            }
        return {"error": "already resolved", "status": 409}

    from optomind_optics.harness.confirmation_gate import compute_diff

    diff = (
        compute_diff(original_payload, payload_after)
        if decision == "modified"
        else []
    )
    resolved = {
        "gate_id": gate_id,
        "decision": decision,
        "responder": responder,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
        "payload_after": payload_after,
        "diff": diff,
    }
    confirmations_dir.mkdir(parents=True, exist_ok=True)
    resolved_path.write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {"schema_version": "console.resolve.v1", "status": 200, **resolved}


def api_events(
    run_dir: Optional[Path], *, since: int = 0, limit: int = MAX_EVENT_TAIL
) -> Dict[str, Any]:
    """The tail of the hash-chained event stream (bounded to 100)."""

    rows: List[Dict[str, Any]] = []
    if run_dir is not None:
        path = Path(run_dir) / EVENTS_FILENAME
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if int(row.get("seq") or 0) > since:
                    rows.append(row)
    rows = rows[-max(1, min(int(limit), MAX_EVENT_TAIL)):]
    return {"schema_version": "console.events.v1", "events": rows, "count": len(rows)}


# ---------------------------------------------------------------------------
# HTTP layer (self-contained stdlib server, loopback only)
# ---------------------------------------------------------------------------

CONSOLE_UI_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>OptoMind 研究控制台</title>
<style>
 body { font-family: system-ui, sans-serif; margin: 2rem; background: #111; color: #eee; }
 h1 { font-size: 1.2rem; } h2 { font-size: 1rem; margin-top: 1.5rem; color: #8cf; }
 table { border-collapse: collapse; width: 100%; }
 td, th { border: 1px solid #444; padding: 4px 8px; text-align: left; font-size: .85rem; }
 .card { border: 1px solid #555; padding: 1rem; margin: 1rem 0; background: #1b1b1b; }
 button { margin-right: .5rem; padding: 4px 10px; }
 textarea { width: 100%; min-height: 8rem; background: #222; color: #eee; }
 .ok { color: #8f8; }
</style></head><body>
<h1>OptoMind 研究控制台（127.0.0.1，无鉴权，勿暴露）</h1>
<h2>研究状态 <span id="refreshed"></span></h2>
<table id="status"></table>
<h2>确认收件箱</h2>
<div id="inbox"></div>
<h2>事件流（尾段）</h2>
<table id="events"></table>
<script>
function renderStatus(s) {
  const rows = [
    ['阶段', s.stage], ['活动假设', s.active_hypotheses], ['已完成假设', s.finished_hypotheses],
    ['已验证候选', s.verified_candidates], ['轮次', s.budget.rounds_started],
    ['评分公式', s.scoring_standard || '—']
  ];
  document.getElementById('status').innerHTML =
    rows.map(r => `<tr><th>${r[0]}</th><td>${r[1]}</td></tr>`).join('');
}
function renderInbox(pending) {
  const box = document.getElementById('inbox');
  if (!pending.length) { box.innerHTML = '<p class="ok">无待确认项。</p>'; return; }
  box.innerHTML = pending.map(p => {
    const editable = JSON.stringify((p.schema_hint || {}).editable || []);
    return `<div class="card"><b>${p.node}</b> (gate ${p.gate_id})<br>
      <textarea id="ta-${p.gate_id}">${JSON.stringify(p.payload, null, 2)}</textarea><br>
      可编辑字段: <code>${editable}</code><br>
      <button onclick="resolve('${p.gate_id}','accepted')">接受</button>
      <button onclick="resolve('${p.gate_id}','modified')">提交修改</button>
      <button onclick="resolve('${p.gate_id}','rejected')">拒绝</button>
    </div>`;
  }).join('');
}
function resolve(gateId, decision) {
  let payloadAfter = undefined;
  if (decision === 'modified') {
    try { payloadAfter = JSON.parse(document.getElementById('ta-' + gateId).value); }
    catch (e) { alert('payload 不是合法 JSON'); return; }
  }
  fetch('/api/confirmations/' + encodeURIComponent(gateId) + '/resolve', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({decision, payload_after: payloadAfter})
  }).then(r => r.json().then(j => ({status: r.status, body: j})))
    .then(({status, body}) => {
      if (status === 409) alert('已被自动接受（超时）: ' + (body.error || ''));
      else refresh();
    });
}
function renderEvents(events) {
  document.getElementById('events').innerHTML = events.map(e =>
    `<tr><td>${e.seq}</td><td>${e.event_type}</td></tr>`).join('');
}
function refresh() {
  fetch('/api/status').then(r => r.json()).then(renderStatus);
  fetch('/api/confirmations').then(r => r.json()).then(d => renderInbox(d.pending || []));
  fetch('/api/events').then(r => r.json()).then(d => renderEvents(d.events || []));
  document.getElementById('refreshed').textContent = '（' + new Date().toLocaleTimeString() + '）';
}
setInterval(refresh, 5000);
refresh();
</script></body></html>
"""


class ResearchConsoleHandler(BaseHTTPRequestHandler):
    server: "ResearchConsoleServer"

    def log_message(self, *args: Any) -> None:  # keep the console quiet
        return

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        if length > 256 * 1024:
            raise ValueError("请求体超过 256KiB 上限")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        run_dir = self.server.run_dir
        if path in {"/", "/index.html"}:
            self._send_html(CONSOLE_UI_HTML)
            return
        if path == "/api/status":
            self._send_json(api_status(run_dir))
            return
        if path == "/api/confirmations":
            self._send_json(api_confirmations(run_dir))
            return
        if path == "/api/events":
            since = 0
            for part in parsed.query.split("&"):
                if part.startswith("since="):
                    try:
                        since = int(part.split("=", 1)[1])
                    except ValueError:
                        since = 0
            self._send_json(api_events(run_dir, since=since))
            return
        if path == "/api/runs":
            self._send_json(api_runs(self.server.output_root))
            return
        if path.startswith("/api/runs/"):
            run_id = path.rsplit("/", 1)[-1]
            root = self.server.output_root
            self._send_json(api_run(root / run_id if root else None))
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if path.startswith("/api/confirmations/") and path.endswith("/resolve"):
            gate_id = path.split("/")[-2]
            try:
                payload = self._read_json_body()
            except (json.JSONDecodeError, ValueError) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                return
            result = api_resolve_confirmation(
                self.server.run_dir,
                gate_id,
                decision=str(payload.get("decision") or ""),
                payload_after=payload.get("payload_after"),
                responder=str(payload.get("responder") or "console"),
            )
            status = int(result.pop("status", 200))
            self._send_json(result, status=HTTPStatus(status))
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)


class ResearchConsoleServer(ThreadingHTTPServer):
    """Loopback-only console server (asserted at construction)."""

    def __init__(
        self,
        server_address: tuple[str, int],
        *,
        run_dir: Optional[Path] = None,
        output_root: Optional[Path] = None,
    ) -> None:
        host = server_address[0]
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("研究控制台只能监听本机回环地址（无鉴权服务）。")
        self.run_dir = Path(run_dir) if run_dir else None
        self.output_root = Path(output_root) if output_root else None
        super().__init__(server_address, ResearchConsoleHandler)


def serve_research_console(
    *,
    run_dir: Optional[Path] = None,
    output_root: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 8766,
) -> None:
    """Blocking serve loop. Loopback only -- no auth by design; the listener
    must never leave 127.0.0.1 (asserted in the server constructor)."""

    server = ResearchConsoleServer(
        (host, port), run_dir=run_dir, output_root=output_root
    )
    print(f"研究控制台: http://127.0.0.1:{port}/  (Ctrl-C 退出)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
