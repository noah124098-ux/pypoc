from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.exceptions import RequestValidationError
import asyncio
import json
import sqlite3
import logging
import secrets
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import psutil

from contextlib import asynccontextmanager
from core.runtime_snapshot import read as read_snapshot
from mcp_server.tools import TradingAgentTools

logger = logging.getLogger(__name__)

API_VERSION = "2.0"

security = HTTPBasic()


def verify(creds: HTTPBasicCredentials = Depends(security)):
    user_ok = secrets.compare_digest(creds.username.encode(), b"admin")
    pass_ok = secrets.compare_digest(
        creds.password.encode(),
        os.getenv("DASHBOARD_PASSWORD", "pypoc2024").encode(),
    )
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


class ConnectionManager:
    """Manages all active WebSocket connections and broadcasts to all at once."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.debug("WS connected — total: %d", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.debug("WS disconnected — total: %d", len(self.active_connections))

    async def broadcast(self, data: dict) -> None:
        """Send *data* to every connected client, removing stale connections."""
        stale: List[WebSocket] = []
        for ws in list(self.active_connections):
            try:
                await ws.send_json(data)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(ws)


manager = ConnectionManager()

REACT_BUILD = Path("frontend/dist")

@asynccontextmanager
async def lifespan(app):
    asyncio.create_task(_broadcast_loop())
    yield


app = FastAPI(title="pypoc Trading API", version="2.0", docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------------------
# Global exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(req: Request, exc: RequestValidationError):
    return JSONResponse(
        {"error": str(exc), "type": "RequestValidationError", "detail": exc.errors()},
        status_code=422,
    )


@app.exception_handler(Exception)
async def global_handler(req: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", req.method, req.url.path)
    return JSONResponse(
        {"error": str(exc), "type": type(exc).__name__},
        status_code=500,
    )


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    logger.debug("%s %s %.0fms", request.method, request.url.path, (time.time() - start) * 1000)
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/snapshot")
def get_snapshot(_: str = Depends(verify)):
    snap = read_snapshot("data/snapshot.json")
    return snap or {"running": False}


@app.get("/api/positions")
def get_positions(_: str = Depends(verify)):
    return TradingAgentTools().get_positions()


@app.get("/api/equity")
def get_equity(limit: int = 200, _: str = Depends(verify)):
    return TradingAgentTools().get_equity_curve(limit=limit)


@app.get("/api/trades")
def get_trades(limit: int = 50, _: str = Depends(verify)):
    return TradingAgentTools().get_recent_trades(limit=limit)


@app.get("/api/signals")
def get_signals(limit: int = 50, _: str = Depends(verify)):
    return TradingAgentTools().get_recent_signals(limit=limit)


@app.get("/api/gate")
def get_gate(_: str = Depends(verify)):
    gate_path = Path("data/backtest_gate.json")
    if gate_path.exists():
        return json.loads(gate_path.read_text())
    return {"passed": False, "error": "no gate file"}


@app.get("/api/status")
def get_status():
    """Comprehensive system status — no auth required, safe for health monitors."""
    now = datetime.now(timezone.utc)

    # Read snapshot
    snap = read_snapshot("data/snapshot.json") or {}
    agent_running = bool(snap.get("running", False))
    agent_halted = bool(snap.get("halted", False))
    equity = snap.get("equity")
    regime = snap.get("current_regime")

    # Read gate file
    gate_path = Path("data/backtest_gate.json")
    gate_passed = False
    gate_age_days: float | None = None
    if gate_path.exists():
        try:
            gate_data = json.loads(gate_path.read_text())
            gate_passed = bool(gate_data.get("passed", False))
            ts_str = gate_data.get("timestamp")
            if ts_str:
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                gate_age_days = round((now - ts).total_seconds() / 86400, 1)
        except Exception:
            pass

    # Detect running services via process names
    def _procs_matching(keywords: list[str]) -> bool:
        try:
            for proc in psutil.process_iter(["cmdline"]):
                cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                if all(k in cmdline for k in keywords):
                    return True
        except Exception:
            pass
        return False

    services = {
        "agent": _procs_matching(["cli.py", "run"]),
        "dashboard": _procs_matching(["streamlit"]) or _procs_matching(["uvicorn"]),
        "mcp": _procs_matching(["cli.py", "mcp-server"]),
    }

    return {
        "api_version": API_VERSION,
        "agent_running": agent_running,
        "agent_halted": agent_halted,
        "equity": equity,
        "regime": regime,
        "gate_passed": gate_passed,
        "gate_age_days": gate_age_days,
        "services": services,
        "timestamp": now.isoformat(),
    }


@app.get("/api/regime")
def get_regime(limit: int = 100, _: str = Depends(verify)):
    return TradingAgentTools().get_regime_history(limit=limit)


@app.get("/api/pnl")
def get_pnl(_: str = Depends(verify)):
    return TradingAgentTools().get_pnl()


@app.get("/api/guardrails")
def get_guardrails(limit: int = 50, _: str = Depends(verify)):
    return TradingAgentTools().get_guardrail_rejections(limit=limit)


@app.get("/api/config")
def get_config(_: str = Depends(verify)):
    return TradingAgentTools().get_config_summary()


@app.get("/api/universe")
def get_universe(_: str = Depends(verify)):
    return TradingAgentTools().get_universe()


# ---------------------------------------------------------------------------
# Analytics endpoints
# ---------------------------------------------------------------------------

def _load_trades_filtered(days: int | None = None):
    """Load trades from DB, optionally filtered to the last `days` days."""
    from core.analytics.metrics import load_trades_from_db
    db_path = Path("data/agent.db")
    if not db_path.exists():
        return []
    trades = load_trades_from_db(db_path)
    if days is not None and days > 0 and trades:
        from datetime import datetime, timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        trades = [t for t in trades if t.closed_at >= cutoff]
    return trades


@app.get("/api/analytics/strategy-performance")
def get_strategy_performance(days: int = 90, _: str = Depends(verify)):
    """Per-strategy ExtendedMetrics breakdown."""
    try:
        from core.analytics.metrics import compute_strategy_attribution
        trades = _load_trades_filtered(days=days)
        attribution = compute_strategy_attribution(trades)
        return {k: v.to_dict() for k, v in attribution.items()}
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/analytics/monthly-pnl")
def get_monthly_pnl(days: int = 365, _: str = Depends(verify)):
    """Monthly P&L summary: pnl, n_trades, win_rate per month."""
    try:
        from core.analytics.metrics import compute_monthly_pnl
        trades = _load_trades_filtered(days=days)
        df = compute_monthly_pnl(trades)
        if df.empty:
            return []
        df = df.reset_index()
        return df.to_dict(orient="records")
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/analytics/extended-metrics")
def get_extended_metrics(days: int = 365, _: str = Depends(verify)):
    """Full ExtendedMetrics for all trades in the given window."""
    try:
        from core.analytics.metrics import compute_extended_metrics
        trades = _load_trades_filtered(days=days)
        m = compute_extended_metrics(trades)
        return m.to_dict()
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Costs endpoint
# ---------------------------------------------------------------------------

@app.get("/api/costs")
def get_costs(days: int = 365, _: str = Depends(verify)):
    """Charges breakdown: total charges, per-strategy charges, recent trade charges."""
    db_path = "data/agent.db"
    if not Path(db_path).exists():
        return {"total_charges": 0.0, "gross_pnl": 0.0, "net_pnl": 0.0,
                "total_trades": 0, "by_strategy": [], "recent_trades": []}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cutoff = f"datetime('now', '-{days} days')"

        # Per-strategy breakdown
        strat_rows = conn.execute(
            f"""
            SELECT strategy,
                   SUM(charges)  AS total_charges,
                   COUNT(*)      AS trades,
                   SUM(pnl)      AS gross_pnl
            FROM trades
            WHERE closed_at >= {cutoff}
            GROUP BY strategy
            ORDER BY total_charges DESC
            """
        ).fetchall()
        by_strategy = [dict(r) for r in strat_rows]

        # Overall totals
        total_row = conn.execute(
            f"""
            SELECT SUM(charges) AS total_charges,
                   SUM(pnl)     AS gross_pnl,
                   COUNT(*)     AS total_trades
            FROM trades
            WHERE closed_at >= {cutoff}
            """
        ).fetchone()
        total_charges = float(total_row["total_charges"] or 0)
        gross_pnl = float(total_row["gross_pnl"] or 0)
        total_trades = int(total_row["total_trades"] or 0)

        # Recent 20 trades with per-trade charges
        recent_rows = conn.execute(
            """
            SELECT id, closed_at, symbol, strategy, side, qty,
                   entry_price, exit_price, pnl, charges, exit_reason
            FROM trades
            ORDER BY closed_at DESC
            LIMIT 20
            """
        ).fetchall()
        recent_trades = [dict(r) for r in recent_rows]

        conn.close()
        return {
            "total_charges": total_charges,
            "gross_pnl": gross_pnl,
            "net_pnl": gross_pnl - total_charges,
            "total_trades": total_trades,
            "by_strategy": by_strategy,
            "recent_trades": recent_trades,
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Trade detail / replay endpoints
# ---------------------------------------------------------------------------

@app.get("/api/trades/list")
def list_trades(limit: int = 50, _: str = Depends(verify)):
    """List recent trades for the replay selector with P&L info."""
    db_path = "data/agent.db"
    if not Path(db_path).exists():
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, symbol, strategy, side, qty,
                   entry_price, exit_price,
                   pnl, charges,
                   opened_at, closed_at, exit_reason
            FROM trades
            ORDER BY closed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/api/trade/{trade_id}")
def get_trade(trade_id: int, _: str = Depends(verify)):
    """Full trade details for replay: entry/exit prices, strategy, regime, rationale, timing."""
    db_path = "data/agent.db"
    if not Path(db_path).exists():
        return None
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT t.*,
                   s.rationale   AS signal_rationale,
                   s.confidence  AS signal_confidence,
                   s.regime      AS signal_regime
            FROM trades t
            LEFT JOIN signals s
                   ON s.symbol = t.symbol
                  AND s.accepted = 1
                  AND s.ts <= t.opened_at
                  AND s.ts >= datetime(t.opened_at, '-5 minutes')
            WHERE t.id = ?
            ORDER BY s.ts DESC
            LIMIT 1
            """,
            (trade_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# EOD review endpoint
# ---------------------------------------------------------------------------

@app.get("/api/eod-review")
def get_eod_review(_: str = Depends(verify)):
    """Return the last EOD review from data/last_review.json, or {available: false}."""
    p = Path("data/last_review.json")
    if p.exists():
        try:
            data = json.loads(p.read_text())
            data.setdefault("available", True)
            return data
        except Exception as exc:
            return {"available": False, "error": str(exc)}
    return {"available": False}


# ---------------------------------------------------------------------------
# Portfolio endpoint
# ---------------------------------------------------------------------------

@app.get("/api/portfolio/angel-one")
def get_angel_one_portfolio(_: str = Depends(verify)):
    """Angel One live portfolio. Returns disconnected stub if credentials absent."""
    api_key = os.getenv("ANGEL_ONE_API_KEY", "")
    client_code = os.getenv("ANGEL_ONE_CLIENT_CODE", "")
    password = os.getenv("ANGEL_ONE_PASSWORD", "")
    totp_secret = os.getenv("ANGEL_ONE_TOTP_SECRET", "")

    if not all([api_key, client_code, password, totp_secret]):
        return {
            "connected": False,
            "message": "Set ANGEL_ONE_API_KEY in .env to connect",
        }
    try:
        from core.data.angelone_portfolio import fetch_live_portfolio
        result = fetch_live_portfolio(
            api_key=api_key,
            client_code=client_code,
            password=password,
            totp_secret=totp_secret,
        )
        if result is None:
            return {"connected": False, "message": "Login failed — check credentials"}
        import dataclasses
        return {"connected": True, **dataclasses.asdict(result)}
    except Exception as exc:
        return {"connected": False, "message": str(exc)}


# ---------------------------------------------------------------------------
# System metrics endpoint
# ---------------------------------------------------------------------------

@app.get("/api/system")
def get_system(_: str = Depends(verify)):
    """EC2/machine resource metrics: CPU, memory, disk, uptime, Python processes."""
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("C:/Users/Administrator/pypoc")
    boot_time = datetime.fromtimestamp(psutil.boot_time())
    uptime_hours = (datetime.now() - boot_time).total_seconds() / 3600
    return {
        "cpu_pct": cpu,
        "memory_used_gb": round(mem.used / 1e9, 2),
        "memory_total_gb": round(mem.total / 1e9, 2),
        "memory_pct": mem.percent,
        "disk_used_gb": round(disk.used / 1e9, 2),
        "disk_free_gb": round(disk.free / 1e9, 2),
        "disk_pct": disk.percent,
        "uptime_hours": round(uptime_hours, 1),
        "python_processes": len([p for p in psutil.process_iter() if "python" in p.name().lower()])
    }


@app.post("/api/command/halt")
def halt_agent(reason: str = "manual halt via API", _: str = Depends(verify)):
    from core.command_queue import enqueue
    cmd = enqueue("halt_agent", {"reason": reason})
    return {"queued": True, "command_id": cmd.id}


@app.post("/api/command/resume")
def resume_agent(_: str = Depends(verify)):
    from core.command_queue import enqueue
    cmd = enqueue("resume_agent", {})
    return {"queued": True, "command_id": cmd.id}


async def _broadcast_loop() -> None:
    """Single background task: read snapshot once per second and push to ALL clients."""
    while True:
        try:
            snap = read_snapshot("data/snapshot.json") or {"running": False}
            if manager.active_connections:
                await manager.broadcast(snap)
        except Exception as exc:
            logger.warning("broadcast_loop error: %s", exc)
        await asyncio.sleep(1)


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket, token: str = ""):
    expected = os.getenv("DASHBOARD_PASSWORD", "pypoc2024")
    if token != expected:
        await websocket.close(1008)
        return
    await manager.connect(websocket)
    try:
        # Send current snapshot immediately on connect so clients don't wait up to 1 s.
        snap = read_snapshot('data/snapshot.json') or {'running': False}
        await websocket.send_json(snap)
        while True:
            # Keep the connection alive; wait for client disconnect or any message.
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        manager.disconnect(websocket)


# ── Serve React SPA (must be last — catches all unmatched routes) ──────────
# When the React build exists, FastAPI serves the whole app on a single port:
#   http://localhost:8502  → React dashboard (SPA, handles own routing)
#   http://localhost:8502/api/*  → REST endpoints
#   ws://localhost:8502/ws/live  → WebSocket live feed
if REACT_BUILD.exists():
    app.mount("/assets", StaticFiles(directory=str(REACT_BUILD / "assets")), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        return FileResponse(str(REACT_BUILD / "favicon.svg"))

    @app.get("/icons.svg", include_in_schema=False)
    def icons():
        return FileResponse(str(REACT_BUILD / "icons.svg"))

    # Serve React SPA — index.html for root and all non-API routes
    _index = str(REACT_BUILD / "index.html")

    @app.get("/", include_in_schema=False)
    async def serve_root():
        return FileResponse(_index)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str, request: Request):
        if full_path.startswith(("api/", "ws/", "health", "docs")):
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(_index)
