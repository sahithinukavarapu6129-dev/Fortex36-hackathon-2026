from __future__ import annotations

"""
Local-only FastAPI application exposing status, insights, logs, and undo endpoints.

The middleware rejects non-local clients to keep the API strictly on localhost.
"""

from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from silent_organizer.engine.organizer import Organizer


def create_app(organizer: Organizer, watcher: Any | None = None) -> FastAPI:
    app = FastAPI(title="Silent Organizer", version="1.0.0")

    @app.middleware("http")
    async def localhost_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        try:
            client = request.client
            host = client.host if client else ""
            if host not in {"127.0.0.1", "::1", "localhost"}:
                return JSONResponse(status_code=403, content={"detail": "Localhost only."})
        except Exception:
            return JSONResponse(status_code=403, content={"detail": "Localhost only."})
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return _DASHBOARD_HTML

    @app.get("/status")
    def status() -> dict[str, Any]:
        state: dict[str, Any] = {"organizer": organizer.status()}
        if watcher is not None:
            try:
                state["watcher"] = {
                    "running": bool(getattr(watcher, "is_running")()),
                }
            except Exception:
                state["watcher"] = {"running": None}
        return {"ok": True, "service": state}

    @app.get("/insights")
    def insights() -> dict[str, Any]:
        return {"ok": True, "insights": organizer.insights()}

    @app.get("/logs")
    def logs(limit: int = 200) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 2000))
        return {"ok": True, "logs": organizer.logs(limit=safe_limit)}

    @app.post("/undo")
    def undo(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
        _ = payload
        return organizer.undo()

    return app


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Silent Organizer</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin: 24px; }
    h1 { margin: 0 0 8px 0; }
    .row { display: flex; gap: 16px; flex-wrap: wrap; }
    .card { border: 1px solid rgba(127,127,127,.35); border-radius: 10px; padding: 14px; min-width: 280px; flex: 1; }
    .muted { opacity: .75; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; white-space: pre-wrap; }
    button { padding: 10px 12px; border-radius: 8px; border: 1px solid rgba(127,127,127,.5); background: transparent; cursor: pointer; }
    button:hover { border-color: rgba(127,127,127,.9); }
    .ok { color: #2f9e44; }
    .bad { color: #e03131; }
  </style>
</head>
<body>
  <h1>Silent Organizer</h1>
  <div class="muted">Local-only dashboard (127.0.0.1). No cloud. No auth.</div>
  <div style="height: 14px"></div>

  <div class="row">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
        <strong>Status</strong>
        <span id="health" class="muted">loading…</span>
      </div>
      <div style="height: 10px"></div>
      <div class="mono" id="statusBox"></div>
    </div>

    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
        <strong>Insights</strong>
        <button id="refreshBtn">Refresh</button>
      </div>
      <div style="height: 10px"></div>
      <div class="mono" id="insightsBox"></div>
    </div>
  </div>

  <div style="height: 16px"></div>

  <div class="row">
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:12px;">
        <strong>Logs</strong>
        <div style="display:flex;gap:10px;align-items:center;">
          <label class="muted">Limit <input id="limit" value="120" style="width:70px;padding:6px;border-radius:8px;border:1px solid rgba(127,127,127,.5);background:transparent;" /></label>
          <button id="undoBtn">Undo (last 24h)</button>
        </div>
      </div>
      <div style="height: 10px"></div>
      <div class="mono" id="logsBox"></div>
    </div>
  </div>

  <script>
    const $ = (id) => document.getElementById(id);
    const statusBox = $("statusBox");
    const insightsBox = $("insightsBox");
    const logsBox = $("logsBox");
    const health = $("health");

    function pretty(obj) {
      try { return JSON.stringify(obj, null, 2); } catch { return String(obj); }
    }

    async function getJson(url) {
      const res = await fetch(url, { cache: "no-store" });
      const data = await res.json();
      return { res, data };
    }

    async function refreshAll() {
      try {
        const s = await getJson("/status");
        health.textContent = s.data?.ok ? "ok" : "error";
        health.className = s.data?.ok ? "ok" : "bad";
        statusBox.textContent = pretty(s.data);
      } catch (e) {
        health.textContent = "offline";
        health.className = "bad";
        statusBox.textContent = String(e);
      }

      try {
        const i = await getJson("/insights");
        insightsBox.textContent = pretty(i.data);
      } catch (e) {
        insightsBox.textContent = String(e);
      }

      await refreshLogs();
    }

    async function refreshLogs() {
      const limit = Math.max(1, Math.min(parseInt($("limit").value || "120", 10), 2000));
      try {
        const l = await getJson(`/logs?limit=${limit}`);
        logsBox.textContent = pretty(l.data);
      } catch (e) {
        logsBox.textContent = String(e);
      }
    }

    $("refreshBtn").addEventListener("click", refreshAll);
    $("undoBtn").addEventListener("click", async () => {
      try {
        const res = await fetch("/undo", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
        const data = await res.json();
        alert(pretty(data));
      } catch (e) {
        alert(String(e));
      }
      await refreshAll();
    });

    refreshAll();
    setInterval(refreshLogs, 3000);
  </script>
</body>
</html>
"""
