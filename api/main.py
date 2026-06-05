from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.exceptions import RequestValidationError
import asyncio
import json
import sqlite3
import logging
import secrets
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

# Load .env on startup and re-expose a helper for endpoints that read creds at request time
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(dotenv_path=_ENV_FILE, override=True)
except ImportError:
    pass

def _reload_env():
    """Re-read .env so credentials saved after process start are visible."""
    try:
        from dotenv import load_dotenv as _ld
        _ld(dotenv_path=_ENV_FILE, override=True)
    except ImportError:
        pass

import psutil
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from contextlib import asynccontextmanager
from core.runtime_snapshot import read as read_snapshot
from mcp_server.tools import TradingAgentTools

logger = logging.getLogger(__name__)

API_VERSION = "2.0"

# ---------------------------------------------------------------------------
# Simple in-memory TTL cache (no external dependencies)
# ---------------------------------------------------------------------------

_cache: dict = {}

# ---------------------------------------------------------------------------
# Process scan cache (5-second TTL, single-pass for all services)
# ---------------------------------------------------------------------------

_proc_scan_cache: dict = {"ts": 0.0, "agent": False, "dashboard": False, "mcp": False}


def _scan_services() -> dict:
    """Scan running processes for known services. Cached for 5 seconds."""
    now = time.monotonic()
    if now - _proc_scan_cache["ts"] < 5.0:
        return _proc_scan_cache
    agent_found = dashboard_found = mcp_found = False
    try:
        for proc in psutil.process_iter(["cmdline"]):
            cmd = " ".join(proc.info.get("cmdline") or []).lower()
            if not agent_found and "cli.py" in cmd and " run" in cmd:
                agent_found = True
            if not dashboard_found and ("uvicorn" in cmd or "streamlit" in cmd):
                dashboard_found = True
            if not mcp_found and "cli.py" in cmd and "mcp-server" in cmd:
                mcp_found = True
            if agent_found and dashboard_found and mcp_found:
                break
    except Exception:
        pass
    _proc_scan_cache.update({"ts": now, "agent": agent_found, "dashboard": dashboard_found, "mcp": mcp_found})
    return _proc_scan_cache


def _cached(key: str, ttl_seconds: int, fn):
    """Return cached value for *key* if still fresh, otherwise call *fn()* and store."""
    entry = _cache.get(key)
    if entry is not None and datetime.now() - entry["ts"] < timedelta(seconds=ttl_seconds):
        return entry["data"]
    result = fn()
    _cache[key] = {"data": result, "ts": datetime.now()}
    return result


def _cache_invalidate(key: str) -> None:
    """Remove a single key from the cache (used in tests or on explicit refresh)."""
    _cache.pop(key, None)

security = HTTPBasic()


def verify(creds: HTTPBasicCredentials = Depends(security)):
    """Verify HTTP Basic credentials, with optional TOTP 2FA.

    When ``DASHBOARD_OTP_SECRET`` is set in the environment, clients must
    include a six-digit TOTP code.  HTTP Basic Auth encodes credentials as
    ``base64(username + ':' + password)`` and Starlette splits on the first
    colon only, so the OTP is embedded using one of two conventions:

    **Convention A — OTP in password field prefix (recommended for curl / scripts):**
    Send ``Authorization: Basic base64("admin:otp_code:real_password")``, i.e.:

        curl -u "admin:123456:pypoc2024" ...

    Starlette parses this as ``username="admin"``, ``password="123456:pypoc2024"``.
    The verify function splits the password on the first colon to extract
    ``otp_code="123456"`` and ``real_password="pypoc2024"``.

    **Convention B — OTP in username field suffix (legacy / some REST clients):**
    Send ``username="admin:123456"`` with the real password in the password field.
    Starlette parses ``username="admin:123456"``, ``password="pypoc2024"``.
    The verify function splits the username on the first colon.

    Both conventions are accepted.  See docs/2FA_SETUP.md for full examples.
    """
    otp_secret = os.getenv("DASHBOARD_OTP_SECRET", "").strip()
    expected_password = os.getenv("DASHBOARD_PASSWORD", "pypoc2024")

    raw_username = creds.username
    raw_password = creds.password
    otp_code: str | None = None

    if otp_secret:
        # Convention A: password field contains "otp_code:real_password"
        if ":" in raw_password:
            pw_parts = raw_password.split(":", 1)
            otp_code = pw_parts[0]
            raw_password = pw_parts[1]
        # Convention B: username field contains "admin:otp_code"
        elif ":" in raw_username:
            u_parts = raw_username.split(":", 1)
            raw_username = u_parts[0]
            otp_code = u_parts[1]

    user_ok = secrets.compare_digest(raw_username.encode(), b"admin")
    pass_ok = secrets.compare_digest(
        raw_password.encode(),
        expected_password.encode(),
    )

    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, headers={"WWW-Authenticate": "Basic"})

    # If 2FA is configured, require a valid TOTP code.
    if otp_secret:
        if not otp_code:
            raise HTTPException(
                status_code=401,
                detail="2FA required: supply credentials as 'admin:otp_code:password'",
                headers={"WWW-Authenticate": "Basic"},
            )
        try:
            import pyotp

            totp = pyotp.TOTP(otp_secret)
            if not totp.verify(otp_code, valid_window=1):
                raise HTTPException(
                    status_code=401,
                    detail="Invalid or expired OTP code",
                    headers={"WWW-Authenticate": "Basic"},
                )
        except ImportError:
            logger.error("pyotp is not installed — cannot enforce 2FA; denying request")
            raise HTTPException(
                status_code=503,
                detail="Server 2FA misconfigured: pyotp not installed",
            )

    return raw_username


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

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


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
# Request logging middleware — structured JSON
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed_ms = round((time.time() - start) * 1000, 1)
    # Derive user from Basic-Auth header when present (username only, no password).
    user: str | None = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        try:
            import base64
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
            user = decoded.split(":", 1)[0]
        except Exception:
            pass
    log_record = {
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "ms": elapsed_ms,
        "user": user,
    }
    logger.info(json.dumps(log_record))
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/ready")
def readiness():
    """Startup readiness probe — used by NSSM / health monitors.

    Returns 200 when the SQLite database is initialised (the ``trades`` table
    exists), and 503 with a JSON body when it is not.
    """
    db_path = Path("data/agent.db")
    if not db_path.exists():
        return JSONResponse(
            {"ready": False, "reason": "database file not found"},
            status_code=503,
        )
    try:
        conn = sqlite3.connect(str(db_path))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
    except Exception as exc:
        return JSONResponse(
            {"ready": False, "reason": f"database error: {exc}"},
            status_code=503,
        )
    if "trades" not in tables:
        return JSONResponse(
            {"ready": False, "reason": "trades table missing — DB not initialised"},
            status_code=503,
        )
    return {"ready": True}


@app.get("/api/metrics")
def prometheus_metrics():
    """Prometheus-compatible plain-text metrics endpoint.

    Exposes:
      pypoc_equity      — current portfolio equity (gauge)
      pypoc_positions   — open position count (gauge)
      pypoc_gate_sharpe — backtest gate Sharpe ratio (gauge)
    """
    snap = read_snapshot("data/snapshot.json") or {}
    equity = snap.get("equity", 0.0)
    mode = snap.get("mode", "paper")
    positions = len(snap.get("open_positions", []))

    gate_sharpe = 0.0
    gate_path = Path("data/backtest_gate.json")
    if gate_path.exists():
        try:
            gate_data = json.loads(gate_path.read_text())
            gate_sharpe = float(gate_data.get("sharpe", 0.0))
        except Exception:
            pass

    lines = [
        "# HELP pypoc_equity Current portfolio equity",
        "# TYPE pypoc_equity gauge",
        f'pypoc_equity{{mode="{mode}"}} {equity}',
        "",
        "# HELP pypoc_positions Open position count",
        "# TYPE pypoc_positions gauge",
        f"pypoc_positions {positions}",
        "",
        "# HELP pypoc_gate_sharpe Backtest gate Sharpe ratio",
        "# TYPE pypoc_gate_sharpe gauge",
        f"pypoc_gate_sharpe {gate_sharpe}",
        "",
    ]
    return PlainTextResponse("\n".join(lines))


@app.get("/api/snapshot")
@limiter.limit("60/minute")
def get_snapshot(request: Request, _: str = Depends(verify)):
    snap = read_snapshot("data/snapshot.json")
    return snap or {"running": False}


@app.get("/api/positions")
def get_positions(_: str = Depends(verify)):
    return TradingAgentTools().get_positions()


@app.get("/api/equity")
@limiter.limit("20/minute")
def get_equity(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    _: str = Depends(verify),
):
    """Equity snapshots with cursor-based pagination.

    Query params: ``limit`` (1–200, default 50), ``offset`` (default 0).
    Returns ``{data, total, limit, offset, has_more}``.
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return TradingAgentTools().get_equity_curve(limit=limit, offset=offset)


@app.get("/api/trades/stats")
def get_trade_stats(_: str = Depends(verify)):
    """Aggregate trade statistics — fast SQL, no full-scan of trade objects.

    Returns ``{total_trades, total_pnl, win_rate, profit_factor, sharpe, max_dd}``.
    """
    return TradingAgentTools().get_trade_stats()


@app.get("/api/trades")
def get_trades(limit: int = 50, offset: int = 0, _: str = Depends(verify)):
    """Recent closed trades with cursor-based pagination.

    Query params: ``limit`` (1–200, default 50), ``offset`` (default 0).
    Returns ``{data, total, limit, offset, has_more}``.
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return TradingAgentTools().get_recent_trades(limit=limit, offset=offset)


@app.get("/api/signals")
def get_signals(limit: int = 50, offset: int = 0, _: str = Depends(verify)):
    """Recent strategy signals with cursor-based pagination.

    Query params: ``limit`` (1–200, default 50), ``offset`` (default 0).
    Returns ``{data, total, limit, offset, has_more}``.
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return TradingAgentTools().get_recent_signals(limit=limit, offset=offset)


@app.get("/api/gate")
def get_gate(_: str = Depends(verify)):
    def _load():
        gate_path = Path("data/backtest_gate.json")
        if gate_path.exists():
            return json.loads(gate_path.read_text())
        return {"passed": False, "error": "no gate file"}

    return _cached("gate", 300, _load)


@app.get("/api/status")
def get_status():
    """Comprehensive system status — no auth required, safe for health monitors."""

    def _compute():
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

        # Detect running services via single-pass cached process scan
        svc = _scan_services()
        services = {
            "agent": svc["agent"],
            "dashboard": svc["dashboard"],
            "mcp": svc["mcp"],
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

    return _cached("status", 10, _compute)


@app.get("/api/regime")
def get_regime(limit: int = 100, _: str = Depends(verify)):
    return TradingAgentTools().get_regime_history(limit=limit)


@app.get("/api/pnl")
def get_pnl(_: str = Depends(verify)):
    return TradingAgentTools().get_pnl()


@app.get("/api/guardrails")
def get_guardrails(limit: int = 50, offset: int = 0, _: str = Depends(verify)):
    """Recent guardrail-rejected events with cursor-based pagination.

    Query params: ``limit`` (1–200, default 50), ``offset`` (default 0).
    Returns ``{data, total, limit, offset, has_more}``.
    """
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return TradingAgentTools().get_guardrail_rejections(limit=limit, offset=offset)


@app.get("/api/atm-iv")
def get_atm_iv(_: str = Depends(verify)):
    """Return the current Nifty ATM CE implied volatility from NSE option chain.

    Response
    --------
    ``{"atm_iv": <float|null>, "source": "nse_option_chain", "cached": <bool>}``

    ``atm_iv`` is expressed as a percentage (e.g. 12.5 means 12.5%).
    Returns ``null`` when NSE is unreachable (fail-open — never blocks trading).
    """
    from core.data.nse_atm_iv import get_atm_iv as _get_atm_iv, _cached_iv, _cache_timestamp
    import time as _time

    # Capture cache state before the (potentially cached) call
    had_cache = _cached_iv is not None
    iv = _get_atm_iv()

    # Determine whether this response was served from cache
    served_from_cache = had_cache and _cached_iv == iv

    return {
        "atm_iv": iv,
        "source": "nse_option_chain",
        "cached": served_from_cache,
    }


@app.get("/api/config")
def get_config(_: str = Depends(verify)):
    return _cached("config", 60, lambda: TradingAgentTools().get_config_summary())


@app.get("/api/universe")
def get_universe(_: str = Depends(verify)):
    return _cached("universe", 3600, lambda: TradingAgentTools().get_universe())


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
    def _compute():
        try:
            from core.analytics.metrics import compute_extended_metrics
            trades = _load_trades_filtered(days=days)
            m = compute_extended_metrics(trades)
            return m.to_dict()
        except Exception as exc:
            return {"error": str(exc)}

    return _cached(f"extended_metrics:{days}", 30, _compute)


@app.get("/api/analytics/sector-performance")
def get_sector_performance(days: int = 365, _: str = Depends(verify)):
    """Per-sector trade performance breakdown for Nifty 50 symbols.

    Groups all closed trades in the given window by NSE sector and returns
    for each sector: n_trades, win_rate (%), pnl, best_symbol, worst_symbol.
    """
    try:
        from core.analytics.metrics import compute_sector_performance, NIFTY50_SECTORS
        db_path = Path("data/agent.db")
        if not db_path.exists():
            return {}
        import sqlite3 as _sqlite3
        from datetime import timedelta
        conn = _sqlite3.connect(str(db_path))
        conn.row_factory = _sqlite3.Row
        cutoff = (
            (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
            if days and days > 0 else "1970-01-01T00:00:00"
        )
        rows = conn.execute(
            "SELECT symbol, pnl FROM trades WHERE closed_at >= ? ORDER BY closed_at",
            (cutoff,),
        ).fetchall()
        conn.close()
        trades = [{"symbol": r["symbol"], "pnl": float(r["pnl"])} for r in rows]
        return compute_sector_performance(trades, symbol_to_sector=NIFTY50_SECTORS)
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
# News sentiment endpoint
# ---------------------------------------------------------------------------

@app.post("/api/news/score")
def post_news_score(payload: dict, _: str = Depends(verify)):
    """Score news sentiment for one or more symbols.

    Request body: ``{"symbols": {"RELIANCE": ["headline1", ...], ...}}``
    Returns: list of ``{symbol, score, confidence, summary}``
    """
    from core.llm.news_scorer import score_batch

    symbols_with_headlines: dict[str, list[str]] = payload.get("symbols", {})
    if not symbols_with_headlines:
        return []

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse(
            {"error": "ANTHROPIC_API_KEY not configured", "requires_key": True},
            status_code=503,
        )

    results = score_batch(
        symbols_with_headlines=symbols_with_headlines,
        api_key=api_key,
    )
    return [
        {
            "symbol": v.symbol,
            "score": v.score,
            "confidence": v.confidence,
            "summary": v.summary,
        }
        for v in results.values()
    ]


# ---------------------------------------------------------------------------
# AI commentary endpoint
# ---------------------------------------------------------------------------

@app.post("/api/ai/commentary")
def post_ai_commentary(payload: dict, _: str = Depends(verify)):
    """Generate live Claude market commentary for a symbol.

    Request body: ``{"symbol": "RELIANCE", "question": "View on today", "context": "..."}``
    Returns: ``{"commentary": str, "model": str}``
    """
    symbol: str = payload.get("symbol", "NIFTY50")
    question: str = payload.get("question", "What is your view on today?")
    context: str = payload.get("context", "")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse(
            {"error": "ANTHROPIC_API_KEY not configured", "requires_key": True},
            status_code=503,
        )

    model_id = "claude-haiku-4-5-20251001"

    prompt_parts = [
        "You are a concise, experienced NSE intraday market analyst.",
        f"Symbol under review: {symbol}",
    ]
    if context.strip():
        prompt_parts.append(f"Additional context provided by the user:\n{context.strip()}")
    prompt_parts.append(f"Question: {question}")
    prompt_parts.append(
        "Answer in 3-5 sentences. Be direct and actionable. "
        "Focus on risk, momentum, and what a short-term trader should watch."
    )
    prompt = "\n\n".join(prompt_parts)

    try:
        import anthropic as _anthropic

        client = _anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model_id,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        commentary = msg.content[0].text.strip()
    except Exception as exc:
        logger.warning("AI commentary call failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    return {"commentary": commentary, "model": model_id}


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

@app.post("/api/credentials/save-angel-one")
def save_angel_one_creds(body: dict, _: str = Depends(verify)):
    """Save Angel One credentials to the .env file (append or update).

    Accepts: {api_key, client_code, password, totp_secret}
    Security: requires HTTP Basic Auth (same auth as all other endpoints).
    """
    api_key = body.get("api_key", "").strip()
    client_code = body.get("client_code", "").strip()
    password = body.get("password", "").strip()
    totp_secret = body.get("totp_secret", "").strip()

    if not all([api_key, client_code, password, totp_secret]):
        raise HTTPException(status_code=400, detail="All four credential fields are required")

    env_path = Path(".env")
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    # Map of env var name -> new value
    updates = {
        "ANGEL_ONE_API_KEY": api_key,
        "ANGEL_ONE_CLIENT_CODE": client_code,
        "ANGEL_ONE_PASSWORD": password,
        "ANGEL_ONE_TOTP_SECRET": totp_secret,
    }

    # Track which keys we updated in-place
    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        matched = False
        for key in updates:
            if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}="):
                new_lines.append(f"{key}={updates[key]}")
                updated_keys.add(key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    # Append any keys that were not already present
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    logger.info("Angel One credentials saved to .env by authenticated user")
    return {"saved": True, "message": "Credentials saved. Restart the agent to use new credentials."}


@app.get("/api/portfolio/angel-one")
def get_angel_one_portfolio(_: str = Depends(verify)):
    """Angel One live portfolio. Returns disconnected stub if credentials absent."""
    _reload_env()
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


@app.post("/api/gate/refresh")
def refresh_gate(_: str = Depends(verify)):
    """Trigger a walk-forward backtest gate refresh from the dashboard."""
    import subprocess
    try:
        result = subprocess.run(
            [".venv/Scripts/python.exe", "cli.py", "walk-forward", "--years", "3", "--end-date", "2026-05-29"],
            capture_output=True, text=True, timeout=600, cwd="C:/Users/Administrator/pypoc"
        )
        _cache_invalidate("gate")
        return {
            "returncode": result.returncode,
            "output": result.stdout[-500:] if result.stdout else "",
            "error": result.stderr[-200:] if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"returncode": -1, "output": "", "error": "Walk-forward timed out after 600s"}


@app.get("/api/preflight")
def get_preflight(_: str = Depends(verify)):
    """Run all pre-flight checks and return JSON.

    Returns ``{checks: [{name, passed, message}], all_passed: bool}``.
    Mirrors the same checks run by ``python cli.py preflight`` but returns
    structured JSON suitable for the React dashboard.
    """
    import os as _os
    import subprocess as _subprocess
    import sys as _sys
    from datetime import datetime as _dt, timezone as _tz

    checks: list[dict] = []

    def _add(name: str, passed: bool, message: str = "") -> None:
        checks.append({"name": name, "passed": passed, "message": message})

    # 1. Virtual environment active
    in_venv = (
        _os.getenv("VIRTUAL_ENV") is not None
        or hasattr(_sys, "real_prefix")
        or (hasattr(_sys, "base_prefix") and _sys.base_prefix != _sys.prefix)
    )
    _add("Virtual environment active", in_venv,
         "" if in_venv else "activate with: .venv\\Scripts\\Activate.ps1")

    # 2. Angel One credentials present
    from dotenv import load_dotenv
    load_dotenv(override=True)
    creds_required = {
        "ANGEL_ONE_API_KEY": _os.getenv("ANGEL_ONE_API_KEY", ""),
        "ANGEL_ONE_CLIENT_CODE": _os.getenv("ANGEL_ONE_CLIENT_CODE", ""),
        "ANGEL_ONE_PASSWORD": _os.getenv("ANGEL_ONE_PASSWORD", ""),
        "ANGEL_ONE_TOTP_SECRET": _os.getenv("ANGEL_ONE_TOTP_SECRET", ""),
    }
    missing_creds = [k for k, v in creds_required.items() if not v]
    creds_present = len(missing_creds) == 0
    _add("Angel One credentials present",
         creds_present,
         f"missing: {', '.join(missing_creds)}" if missing_creds else "")

    # 3. Credentials format (not placeholder values)
    PLACEHOLDER_PATTERNS = {"your_", "<", "xxx", "test", "dummy", "placeholder", "changeme"}
    bad_creds = [k for k, v in creds_required.items()
                 if v and any(p in v.lower() for p in PLACEHOLDER_PATTERNS)]
    creds_format_ok = creds_present and len(bad_creds) == 0
    _add("Angel One credentials correct format",
         creds_format_ok,
         f"likely placeholder: {', '.join(bad_creds)}" if bad_creds
         else ("credentials missing" if not creds_present else ""))

    # 4. Backtest gate passes
    try:
        from backtest.gate import GATE_MAX_AGE_DAYS, is_live_allowed, read_gate_result
        gate_data = read_gate_result()
        gate_allowed, gate_reason = is_live_allowed()
        if gate_data is None:
            gate_detail = "no gate file — run: python cli.py walk-forward --years 3"
        elif not gate_data.get("passed", False):
            failures = gate_data.get("failures", [])
            gate_detail = f"FAILED: {', '.join(failures)}"
        else:
            ts_str = gate_data.get("timestamp", "")
            gate_age_days_val: float | None = None
            if ts_str:
                try:
                    ts_g = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
                    gate_age_days_val = (_dt.now(_tz.utc) - ts_g).total_seconds() / 86400
                except ValueError:
                    pass
            if gate_age_days_val is not None and gate_age_days_val > GATE_MAX_AGE_DAYS:
                gate_detail = f"EXPIRED ({gate_age_days_val:.0f} days old > {GATE_MAX_AGE_DAYS} day limit)"
            else:
                gate_detail = f"{gate_age_days_val:.0f} days old" if gate_age_days_val is not None else ""
        _add("Backtest gate passes", gate_allowed, gate_detail)
    except Exception as exc:
        _add("Backtest gate passes", False, f"error reading gate: {exc}")

    # 5. Config validates
    config_ok = False
    config_detail = ""
    try:
        from core.config import load_settings
        load_settings("config/default.yaml")
        config_ok = True
    except Exception as exc:
        config_detail = str(exc)[:120]
    _add("Config validates", config_ok, config_detail)

    # 6. Core config imports cleanly (fast smoke — replaces slow pytest run)
    smoke_ok = False
    smoke_detail = ""
    try:
        from core.config import load_settings as _load_settings
        _load_settings("config/default.yaml")
        smoke_ok = True
        smoke_detail = "config loads cleanly"
    except Exception as exc:
        smoke_detail = str(exc)[:120]
    _add("Core config smoke test", smoke_ok, smoke_detail)

    # 7. Data directory exists and is writable
    data_dir = Path("data")
    data_exists = data_dir.exists() and data_dir.is_dir()
    data_writable = False
    if data_exists:
        try:
            probe = data_dir / ".preflight_write_probe"
            probe.write_text("ok")
            probe.unlink()
            data_writable = True
        except OSError:
            pass
    data_ok = data_exists and data_writable
    if not data_exists:
        data_detail = "directory does not exist — run: mkdir data"
    elif not data_writable:
        data_detail = "directory is not writable"
    else:
        data_detail = str(data_dir.resolve())
    _add("Data directory exists and is writable", data_ok, data_detail)

    # 8. Logs directory exists
    logs_dir = Path("logs")
    logs_ok = logs_dir.exists() and logs_dir.is_dir()
    _add("Logs directory exists", logs_ok,
         "" if logs_ok else "directory does not exist — run: mkdir logs")

    # 9. Market hours (IST 09:15-15:30 weekdays)
    try:
        from zoneinfo import ZoneInfo
        ist_tz = ZoneInfo("Asia/Kolkata")
        now_ist = _dt.now(ist_tz)
        weekday = now_ist.weekday()
        mkt_open = now_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        mkt_close = now_ist.replace(hour=15, minute=30, second=0, microsecond=0)
        market_open = weekday < 5 and mkt_open <= now_ist <= mkt_close
        if weekday >= 5:
            mkt_detail = f"today is {now_ist.strftime('%A')} — market closed on weekends"
        elif now_ist < mkt_open:
            opens_in = (mkt_open - now_ist).seconds // 60
            mkt_detail = f"pre-market — opens in {opens_in}m (IST {now_ist.strftime('%H:%M')})"
        elif now_ist > mkt_close:
            mkt_detail = f"post-market (IST {now_ist.strftime('%H:%M')}, closed at 15:30)"
        else:
            mkt_detail = f"NSE open (IST {now_ist.strftime('%H:%M')})"
        _add("Market hours (IST 09:15-15:30 weekdays)", market_open, mkt_detail)
    except Exception as exc:
        _add("Market hours (IST 09:15-15:30 weekdays)", False, str(exc)[:80])

    # 10. Snapshot freshness
    pid_file = Path("data/agent.pid")
    snapshot_file = Path("data/snapshot.json")
    snap_ok = True
    snap_detail = ""
    if pid_file.exists():
        if not snapshot_file.exists():
            snap_ok = False
            snap_detail = "agent.pid exists but data/snapshot.json missing"
        else:
            try:
                import json as _json_mod
                snap_data = _json_mod.loads(snapshot_file.read_text(encoding="utf-8"))
                ts_str = snap_data.get("ts", "")
                if ts_str:
                    ts_s = _dt.fromisoformat(ts_str.replace("Z", "+00:00"))
                    age_s = (_dt.now(_tz.utc) - ts_s).total_seconds()
                    if age_s > 60:
                        snap_ok = False
                        snap_detail = f"snapshot is {age_s:.0f}s old (>60s) — agent may be stuck"
                    else:
                        snap_detail = f"snapshot {age_s:.0f}s old — agent running OK"
                else:
                    snap_detail = "snapshot has no timestamp field"
            except Exception as exc:
                snap_ok = False
                snap_detail = f"could not read snapshot: {exc}"
    else:
        snap_detail = "no agent.pid — agent is not currently running"
    _add("Snapshot freshness", snap_ok, snap_detail)

    all_passed = all(c["passed"] for c in checks)
    return {"checks": checks, "all_passed": all_passed}


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


@app.post("/api/command/place-paper-order")
def place_paper_order_cmd(body: dict, _: str = Depends(verify)):
    """Enqueue a place_paper_order command to the command queue.

    Body: ``{symbol, side, qty, price, strategy, stop_loss?, target?}``
    Returns: ``{queued: bool, command_id: str}``
    """
    from core.command_queue import enqueue

    symbol = body.get("symbol", "").strip().upper()
    side = body.get("side", "BUY").strip().upper()
    qty = int(body.get("qty", 1))
    price = float(body.get("price", 0) or 0)
    strategy = body.get("strategy", "manual").strip()
    stop_loss = body.get("stop_loss")
    target = body.get("target")

    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    if side not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    if qty < 1:
        raise HTTPException(status_code=400, detail="qty must be >= 1")

    params: dict = {"symbol": symbol, "side": side, "qty": qty,
                    "price": price, "strategy": strategy}
    if stop_loss is not None:
        params["stop_loss"] = float(stop_loss)
    if target is not None:
        params["target"] = float(target)

    cmd = enqueue("place_paper_order", params)
    return {"queued": True, "command_id": cmd.id,
            "message": f"Order queued: {qty} {symbol} {side} @ {price}"}


# ---------------------------------------------------------------------------
# Simulator endpoints
# ---------------------------------------------------------------------------

@app.post("/api/simulator/trade")
def simulator_trade(body: dict, _: str = Depends(verify)):
    """Simulate a manual paper trade by enqueuing a place_paper_order command.

    Body: ``{symbol, side, qty, price, stop_loss?, target?, strategy?}``
    Returns: ``{queued: bool, command_id: str, message: str}``
    """
    from core.command_queue import enqueue

    symbol = (body.get("symbol") or "").strip().upper()
    side = (body.get("side") or "BUY").strip().upper()
    qty = int(body.get("qty") or 1)
    price = float(body.get("price") or 0)
    stop_loss = body.get("stop_loss")
    target = body.get("target")
    strategy = (body.get("strategy") or "manual").strip()

    if not symbol:
        raise HTTPException(status_code=400, detail="symbol is required")
    if side not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    if qty < 1:
        raise HTTPException(status_code=400, detail="qty must be >= 1")
    if price <= 0:
        raise HTTPException(status_code=400, detail="price must be > 0")

    params: dict = {"symbol": symbol, "side": side, "qty": qty,
                    "price": price, "strategy": strategy}
    if stop_loss is not None:
        try:
            params["stop_loss"] = float(stop_loss)
        except (TypeError, ValueError):
            pass
    if target is not None:
        try:
            params["target"] = float(target)
        except (TypeError, ValueError):
            pass

    cmd = enqueue("place_paper_order", params)
    sl_str = f" | SL: ₹{params['stop_loss']:.2f}" if "stop_loss" in params else ""
    tgt_str = f" | Target: ₹{params['target']:.2f}" if "target" in params else ""
    message = f"ORDER PLACED — {qty} {symbol} @ ₹{price:.2f}{sl_str}{tgt_str}"
    return {"queued": True, "command_id": cmd.id, "message": message}


@app.post("/api/simulator/set-params")
def simulator_set_params(body: dict, _: str = Depends(verify)):
    """Update simulation parameters via update_risk_param commands.

    Body: ``{capital?, risk_pct?, max_positions?}``
    Returns: ``{applied: bool, message: str}``
    """
    from core.command_queue import enqueue

    applied: list[str] = []

    capital = body.get("capital")
    risk_pct = body.get("risk_pct")
    max_positions = body.get("max_positions")

    if risk_pct is not None:
        try:
            rp = float(risk_pct)
            if 0 < rp <= 10:
                enqueue("update_risk_param", {"key": "risk_per_trade_pct", "value": rp})
                applied.append(f"risk_per_trade_pct={rp}")
        except (TypeError, ValueError):
            pass

    if max_positions is not None:
        try:
            mp = int(max_positions)
            if 1 <= mp <= 20:
                enqueue("update_risk_param", {"key": "max_open_positions", "value": mp})
                applied.append(f"max_open_positions={mp}")
        except (TypeError, ValueError):
            pass

    # capital is informational only (stored in frontend state); no live param for it
    if capital is not None:
        applied.append(f"capital={capital} (frontend only)")

    if not applied:
        return {"applied": False, "message": "No valid parameters to update"}

    return {"applied": True, "message": "Applied: " + ", ".join(applied)}


# ---------------------------------------------------------------------------
# Autonomous simulator — start/stop/status/equity-curve
# ---------------------------------------------------------------------------

import random
import uuid as _uuid
from dataclasses import dataclass, field as _dc_field


@dataclass
class _SimulatorState:
    running: bool = False
    start_time: float = 0.0
    capital: float = 500_000.0
    risk_pct: float = 1.0
    max_positions: int = 5
    use_live_data: bool = False
    equity_curve: list = _dc_field(default_factory=list)
    trade_log: list = _dc_field(default_factory=list)
    # background task handle
    _task: object = None


# Single shared simulator instance (keyed by "default")
_sim_states: dict[str, _SimulatorState] = {}

# Synthetic base prices for Nifty 50 symbols — updated each tick in the loop
_sim_prices: dict[str, float] = {}

# Tick count per open trade for auto-close logic
_sim_open_ticks: dict[str, int] = {}


def _get_or_create_sim() -> _SimulatorState:
    if "default" not in _sim_states:
        _sim_states["default"] = _SimulatorState()
    return _sim_states["default"]


async def _simulator_loop(state: _SimulatorState) -> None:
    """Background asyncio task: runs strategy evaluations on synthetic prices."""
    import math
    import pandas as pd
    from core.config import load_settings, RiskCfg, ExecutionCfg
    from core.broker.paper import PaperBroker
    from core.risk.guardrails import Guardrails, MarketContext, PortfolioState
    from core.risk.sizing import position_size
    from core.types import Regime, Side, Signal, OrderType
    from core.data.universe import resolve_universe
    from core.strategies.mean_reversion import MeanReversion
    from core.strategies.trend_breakout import TrendBreakout
    from core.strategies.volatility_compression import VolatilityCompression
    from core.strategies.rsi_bounce import RsiBounce

    # Build a minimal settings object for the simulator
    settings = load_settings()
    # Override risk params from user-supplied values
    settings.risk.per_trade_risk_pct = state.risk_pct
    settings.risk.max_open_positions = state.max_positions

    exec_cfg = settings.execution
    broker = PaperBroker(starting_cash=state.capital, exec_cfg=exec_cfg)
    guardrails = Guardrails(settings.risk, settings.market, settings.execution)

    symbols = resolve_universe("nifty50", [])

    # Initialise synthetic prices (realistic NSE mid-caps: 500–3000 range)
    _price_seeds = {
        "RELIANCE": 2900, "TCS": 3600, "HDFCBANK": 1700, "INFY": 1500, "ICICIBANK": 1200,
        "HINDUNILVR": 2400, "ITC": 450, "SBIN": 800, "BHARTIARTL": 1600, "KOTAKBANK": 1800,
        "LT": 3500, "AXISBANK": 1100, "ASIANPAINT": 2800, "MARUTI": 12000, "TITAN": 3300,
        "BAJFINANCE": 7000, "HCLTECH": 1600, "WIPRO": 550, "ONGC": 250, "POWERGRID": 330,
        "NTPC": 380, "ULTRACEMCO": 10000, "BAJAJFINSV": 1700, "INDUSINDBK": 900, "NESTLEIND": 2200,
        "TATAMOTORS": 920, "JSWSTEEL": 900, "TATASTEEL": 150, "CIPLA": 1500, "DIVISLAB": 5000,
        "DRREDDY": 5500, "EICHERMOT": 4500, "BPCL": 380, "COALINDIA": 430, "ADANIPORTS": 1300,
        "ADANIENT": 2600, "APOLLOHOSP": 7000, "BRITANNIA": 5000, "GRASIM": 2700, "HEROMOTOCO": 4800,
        "HINDALCO": 660, "M&M": 2900, "SUNPHARMA": 1700, "TATACONSUM": 1000, "TECHM": 1400,
        "TRENT": 5500, "ULTRACEMCO": 10000, "UPL": 550, "VEDL": 450, "ZOMATO": 250,
    }
    global _sim_prices, _sim_open_ticks
    _sim_prices = {}
    for sym in symbols:
        _sim_prices[sym] = float(_price_seeds.get(sym, 1000))
    _sim_open_ticks = {}

    # Build minimal OHLCV history for each symbol (60 bars of synthetic candles)
    _price_history: dict[str, list] = {}
    for sym in symbols:
        base = _sim_prices[sym]
        hist = []
        p = base * 0.9
        for _ in range(60):
            o = p
            c = o * (1 + random.gauss(0.0002, 0.005))
            h = max(o, c) * (1 + abs(random.gauss(0, 0.002)))
            l = min(o, c) * (1 - abs(random.gauss(0, 0.002)))
            v = random.randint(100_000, 2_000_000)
            hist.append({"open": o, "high": h, "low": l, "close": c, "volume": v})
            p = c
        _price_history[sym] = hist

    strategies = [
        MeanReversion(),
        TrendBreakout(),
        VolatilityCompression(),
        RsiBounce(),
    ]

    # Determine tick interval: 5s outside market hours (replay), 30s during market hours
    def _interval() -> float:
        now_ist = datetime.now()
        h, m = now_ist.hour, now_ist.minute
        in_market = (h == 9 and m >= 15) or (10 <= h <= 14) or (h == 15 and m <= 30)
        return 30.0 if (in_market and state.use_live_data) else 5.0

    def _current_regime() -> Regime:
        """Read regime from live snapshot if available, else default to RANGE."""
        try:
            snap = read_snapshot("data/snapshot.json")
            if snap and snap.current_regime:
                return Regime(snap.current_regime)
        except Exception:
            pass
        return Regime.RANGE

    tick_count = 0
    while state.running:
        interval = _interval()
        await asyncio.sleep(interval)
        if not state.running:
            break

        tick_count += 1
        regime = _current_regime()

        # Advance synthetic prices for all symbols
        for sym in symbols:
            drift = 0.0001 if regime == Regime.TREND else (-0.00005 if regime == Regime.VOLATILE else 0.0)
            _sim_prices[sym] *= (1 + drift + random.gauss(0, 0.001))
            _sim_prices[sym] = max(_sim_prices[sym], 1.0)
            # Append a new synthetic candle to history
            base = _sim_prices[sym]
            o = base
            c = base * (1 + random.gauss(drift, 0.003))
            h = max(o, c) * (1 + abs(random.gauss(0, 0.001)))
            l = min(o, c) * (1 - abs(random.gauss(0, 0.001)))
            v = random.randint(80_000, 1_500_000)
            _price_history[sym].append({"open": o, "high": h, "low": l, "close": c, "volume": v})
            # Keep history window at 120 candles max
            if len(_price_history[sym]) > 120:
                _price_history[sym] = _price_history[sym][-120:]

        # Update broker with latest prices so SL/target auto-exits fire
        broker.update_market_prices(_sim_prices)

        # Increment open-position tick counters and auto-close after 30 ticks
        for sym in list(_sim_open_ticks.keys()):
            _sim_open_ticks[sym] = _sim_open_ticks.get(sym, 0) + 1
            if _sim_open_ticks[sym] >= 30:
                pos = broker.get_position(sym)
                if pos is not None:
                    exit_px = _sim_prices.get(sym, pos.last_price)
                    # Place a closing SELL/BUY order
                    close_side = Side.SELL if sym in broker._positions else Side.BUY
                    broker.place_order(
                        symbol=sym,
                        side=close_side,
                        qty=pos.qty,
                        order_type=OrderType.MARKET,
                        stop_loss=exit_px * 0.99,
                        target=None,
                        strategy=pos.strategy,
                    )
                    # Record closed trade in our log
                    for tr in state.trade_log:
                        if tr["symbol"] == sym and tr["status"] == "OPEN":
                            pnl = (exit_px - tr["entry_price"]) * tr["qty"] if tr["side"] == "BUY" else (tr["entry_price"] - exit_px) * tr["qty"]
                            tr["exit_price"] = round(exit_px, 2)
                            tr["pnl"] = round(pnl, 2)
                            tr["status"] = "CLOSED"
                            tr["reason"] = "time_exit"
                            break
                del _sim_open_ticks[sym]

        # Check if any auto-exits happened via SL/target (broker.trade_log is the source of truth)
        # Sync status for open trades that broker has since closed
        open_symbols_in_log = {tr["symbol"] for tr in state.trade_log if tr["status"] == "OPEN"}
        broker_position_symbols = {p.symbol for p in broker.get_positions()}
        for sym in open_symbols_in_log - broker_position_symbols:
            # Broker closed the position (SL or target hit)
            exit_px = _sim_prices.get(sym, 0.0)
            for tr in state.trade_log:
                if tr["symbol"] == sym and tr["status"] == "OPEN":
                    pnl = (exit_px - tr["entry_price"]) * tr["qty"] if tr["side"] == "BUY" else (tr["entry_price"] - exit_px) * tr["qty"]
                    tr["exit_price"] = round(exit_px, 2)
                    tr["pnl"] = round(pnl, 2)
                    tr["status"] = "CLOSED"
                    tr["reason"] = "sl_or_target"
                    break
            _sim_open_ticks.pop(sym, None)

        # Evaluate strategies and generate signals
        equity = broker.equity()
        portfolio = PortfolioState(
            equity=equity,
            starting_equity_today=state.capital,
            peak_equity=max((pt["v"] for pt in state.equity_curve), default=equity),
            open_positions=broker.get_positions(),
            realized_pnl_today=broker.realized_pnl,
            last_exit_by_symbol={},
            halted=False,
            halt_reason="",
        )
        market_ctx = MarketContext(
            now=datetime.now(),
            nifty_ltp=_sim_prices.get("RELIANCE", 0.0),
            nifty_change_pct_15m=0.0,
            vix=15.0,
            vix_change_pct_15m=0.0,
            last_tick_age_seconds=2.0,
            avg_daily_volumes={sym: 1_000_000 for sym in symbols},
            spread_pct_by_symbol={sym: 0.05 for sym in symbols},
        )

        signals_this_tick = 0
        for sym in symbols:
            if sym in broker._positions or sym in broker._short_positions:
                continue  # already have a position
            hist = _price_history.get(sym, [])
            if len(hist) < 55:
                continue
            df = pd.DataFrame(hist[-80:])
            for strat in strategies:
                if not strat.supports(regime):
                    continue
                try:
                    sig = strat.evaluate(sym, df, regime)
                except Exception:
                    sig = None
                if sig is None:
                    continue

                qty = max(1, int(
                    equity * (state.risk_pct / 100.0) / abs(sig.entry_price - sig.stop_loss)
                )) if abs(sig.entry_price - sig.stop_loss) > 0 else 1

                decision = guardrails.check(sig, qty, portfolio, market_ctx)
                if not decision.allow:
                    continue

                # Place the paper order
                broker._latest_prices[sym] = _sim_prices[sym]
                order = broker.place_order(
                    symbol=sym,
                    side=sig.side,
                    qty=qty,
                    order_type=OrderType.MARKET,
                    stop_loss=sig.stop_loss,
                    target=sig.target,
                    strategy=sig.strategy,
                )
                if order.status.value == "FILLED":
                    trade_entry = {
                        "id": str(_uuid.uuid4()),
                        "symbol": sym,
                        "side": sig.side.value,
                        "qty": qty,
                        "entry_price": round(order.filled_price, 2),
                        "exit_price": None,
                        "pnl": None,
                        "strategy": sig.strategy,
                        "status": "OPEN",
                        "ts": int(time.time()),
                        "reason": sig.rationale or "",
                    }
                    state.trade_log.append(trade_entry)
                    _sim_open_ticks[sym] = 0
                    signals_this_tick += 1
                    # Update portfolio for subsequent signal checks this tick
                    portfolio = PortfolioState(
                        equity=broker.equity(),
                        starting_equity_today=state.capital,
                        peak_equity=max((pt["v"] for pt in state.equity_curve), default=broker.equity()),
                        open_positions=broker.get_positions(),
                        realized_pnl_today=broker.realized_pnl,
                        last_exit_by_symbol={},
                        halted=False,
                        halt_reason="",
                    )
                break  # one signal per symbol per tick

        # Snapshot equity curve
        state.equity_curve.append({"t": int(time.time()), "v": round(broker.equity(), 2)})
        # Keep equity curve bounded (max 2000 points)
        if len(state.equity_curve) > 2000:
            state.equity_curve = state.equity_curve[-2000:]


@app.post("/api/simulator/start")
async def simulator_start(body: dict, _: str = Depends(verify)):
    """Start the autonomous simulator loop.

    Body: ``{capital?, risk_pct?, max_positions?, use_live_data?}``
    Returns: ``{started: bool, running: bool, message: str}``
    """
    state = _get_or_create_sim()
    if state.running:
        return {"started": False, "running": True, "message": "Already running"}

    state.capital = float(body.get("capital") or 500_000.0)
    state.risk_pct = float(body.get("risk_pct") or 1.0)
    state.max_positions = int(body.get("max_positions") or 5)
    state.use_live_data = bool(body.get("use_live_data", False))
    state.running = True
    state.start_time = time.time()
    state.equity_curve = [{"t": int(time.time()), "v": round(state.capital, 2)}]
    state.trade_log = []

    task = asyncio.get_event_loop().create_task(_simulator_loop(state))
    state._task = task
    logger.info(
        "Simulator started: capital=%.0f risk_pct=%.2f max_pos=%d",
        state.capital, state.risk_pct, state.max_positions,
    )
    return {"started": True, "running": True, "message": "Simulator started"}


@app.post("/api/simulator/stop")
async def simulator_stop(_: str = Depends(verify)):
    """Stop the autonomous simulator loop.

    Returns: ``{stopped: bool, elapsed_seconds: int, total_trades: int, final_pnl: float}``
    """
    state = _get_or_create_sim()
    if not state.running:
        return {"stopped": False, "elapsed_seconds": 0, "total_trades": 0, "final_pnl": 0.0,
                "message": "Simulator is not running"}

    state.running = False
    if state._task is not None:
        try:
            state._task.cancel()
        except Exception:
            pass
        state._task = None

    elapsed = int(time.time() - state.start_time) if state.start_time else 0
    total_trades = len(state.trade_log)
    closed = [t for t in state.trade_log if t["status"] == "CLOSED" and t["pnl"] is not None]
    final_pnl = sum(t["pnl"] for t in closed)

    logger.info("Simulator stopped: elapsed=%ds trades=%d pnl=%.2f", elapsed, total_trades, final_pnl)
    return {
        "stopped": True,
        "elapsed_seconds": elapsed,
        "total_trades": total_trades,
        "final_pnl": round(final_pnl, 2),
    }


@app.get("/api/simulator/status")
def simulator_status(_: str = Depends(verify)):
    """Return the current simulator state.

    Returns running flag, trade summary, P&L, recent trades, equity curve, and open positions.
    """
    state = _get_or_create_sim()
    elapsed = int(time.time() - state.start_time) if state.start_time else 0
    closed = [t for t in state.trade_log if t["status"] == "CLOSED" and t["pnl"] is not None]
    win_trades = sum(1 for t in closed if t["pnl"] > 0)
    loss_trades = sum(1 for t in closed if t["pnl"] <= 0)
    pnl_inr = round(sum(t["pnl"] for t in closed), 2)
    recent_trades = sorted(state.trade_log, key=lambda t: t["ts"], reverse=True)[:20]

    # Build current positions list from trade_log OPEN entries
    current_positions = [
        {
            "symbol": t["symbol"],
            "side": t["side"],
            "qty": t["qty"],
            "entry_price": t["entry_price"],
            "strategy": t["strategy"],
            "ts": t["ts"],
        }
        for t in state.trade_log if t["status"] == "OPEN"
    ]

    # Last 100 equity curve points for status response
    eq_tail = state.equity_curve[-100:] if state.equity_curve else []

    return {
        "running": state.running,
        "elapsed_seconds": elapsed,
        "total_trades": len(state.trade_log),
        "win_trades": win_trades,
        "loss_trades": loss_trades,
        "pnl_inr": pnl_inr,
        "equity_curve": eq_tail,
        "recent_trades": recent_trades,
        "current_positions": current_positions,
    }


@app.get("/api/simulator/equity-curve")
def simulator_equity_curve(_: str = Depends(verify)):
    """Return the full equity curve for charting.

    Returns: ``[{t: unix_timestamp, v: float}, ...]``
    """
    state = _get_or_create_sim()
    return state.equity_curve


# ---------------------------------------------------------------------------
# Nifty breadth endpoint  — % of Nifty 50 stocks above their 50-DMA
# ---------------------------------------------------------------------------

@app.get("/api/nifty-breadth")
def get_nifty_breadth(_: str = Depends(verify)):
    """Compute percentage of Nifty 50 stocks above their 50-day moving average.

    This is the same market breadth filter used inside the backtest engine.
    Results are cached for 30 minutes to avoid slow bhavcopy I/O on every request.

    Response
    --------
    ``{above_50dma, below_50dma, total, breadth_pct, cached_at}``
    """
    import pandas as pd
    from backtest.data_loader import HistoricalLoader
    from core.data.universe import resolve_universe

    def _compute():
        loader = HistoricalLoader()
        symbols = resolve_universe("nifty50", [])
        symbol_data = loader.load_universe(symbols[:50], days=60)
        above = 0
        below = 0
        for sym, df in symbol_data.items():
            try:
                dma = df["close"].rolling(50).mean().iloc[-1]
                if not pd.isna(dma) and df["close"].iloc[-1] > dma:
                    above += 1
                else:
                    below += 1
            except Exception:
                below += 1
        total = above + below
        return {
            "above_50dma": above,
            "below_50dma": below,
            "total": total,
            "breadth_pct": round(above / total * 100, 1) if total else 0.0,
            "cached_at": datetime.now().isoformat(),
        }

    return _cached("nifty_breadth", 1800, _compute)


# ---------------------------------------------------------------------------
# Upcoming economic calendar events
# ---------------------------------------------------------------------------

@app.get("/api/calendar/upcoming")
def get_calendar_upcoming(_: str = Depends(verify)):
    """Return the next 5 economic blackout events from the built-in calendar.

    Covers RBI MPC meetings, Union Budget, and US FOMC meetings through 2026.

    Response
    --------
    List of ``{date, days_away, is_blackout, label}``
    """
    from core.data.economic_calendar import _ALL_EVENT_DATES, RBI_MPC_DATES, BUDGET_DATES, FOMC_DATES
    from datetime import date as _date

    # Build a lookup: date -> human-readable label (first match wins)
    _rbi = {_date.fromisoformat(d) for d in RBI_MPC_DATES}
    _budget = {_date.fromisoformat(d) for d in BUDGET_DATES}
    _fomc = {_date.fromisoformat(d) for d in FOMC_DATES}

    def _label(d: _date) -> str:
        if d in _rbi:
            return "RBI MPC"
        if d in _budget:
            return "Union Budget"
        if d in _fomc:
            return "US FOMC"
        return "Economic Event"

    today = _date.today()
    upcoming = sorted(d for d in _ALL_EVENT_DATES if d >= today)[:5]
    return [
        {
            "date": str(d),
            "days_away": (d - today).days,
            "is_blackout": True,
            "label": _label(d),
        }
        for d in upcoming
    ]


# ---------------------------------------------------------------------------
# Live broker connection test
# ---------------------------------------------------------------------------

@app.post("/api/live-broker/test")
def test_live_broker_connection(_: str = Depends(verify)):
    """Attempt a read-only connection to AngelOneLiveBroker and return account info.

    Uses ANGEL_ONE_LIVE_* credentials from the environment (.env).
    Calls getRMS() to retrieve available cash and net value.
    Always disconnects cleanly regardless of outcome.

    Response
    --------
    ``{connected: bool, cash: float, net: float, error: str|null}``
    """
    import os as _os

    from dotenv import load_dotenv as _load_dotenv

    _load_dotenv(override=False)

    # Check credentials are present
    live_creds = {
        "ANGEL_ONE_LIVE_API_KEY": _os.getenv("ANGEL_ONE_LIVE_API_KEY", ""),
        "ANGEL_ONE_LIVE_CLIENT_CODE": _os.getenv("ANGEL_ONE_LIVE_CLIENT_CODE", ""),
        "ANGEL_ONE_LIVE_PASSWORD": _os.getenv("ANGEL_ONE_LIVE_PASSWORD", ""),
        "ANGEL_ONE_LIVE_TOTP_SECRET": _os.getenv("ANGEL_ONE_LIVE_TOTP_SECRET", ""),
    }
    missing = [k for k, v in live_creds.items() if not v]
    if missing:
        return {
            "connected": False,
            "cash": 0.0,
            "net": 0.0,
            "error": (
                f"Missing ANGEL_ONE_LIVE_* credentials: {', '.join(missing)}. "
                "Set them in .env using a SEPARATE Angel One app with order permissions."
            ),
        }

    # Cross-contamination guard
    data_feed_key = _os.getenv("ANGEL_ONE_API_KEY", "")
    if data_feed_key and live_creds["ANGEL_ONE_LIVE_API_KEY"] == data_feed_key:
        return {
            "connected": False,
            "cash": 0.0,
            "net": 0.0,
            "error": (
                "ANGEL_ONE_LIVE_API_KEY matches ANGEL_ONE_API_KEY. "
                "You MUST use a SEPARATE Angel One app for order execution. "
                "The data-feed credentials are DATA-ONLY."
            ),
        }

    broker = None
    try:
        from core.broker.angelone_live import AngelOneLiveBroker
        from core.config import load_settings as _load_settings

        _settings = _load_settings()
        broker = AngelOneLiveBroker.from_env(_settings.execution)
        broker.connect()
    except ValueError as exc:
        return {
            "connected": False,
            "cash": 0.0,
            "net": 0.0,
            "error": f"Credential error: {exc}",
        }
    except RuntimeError as exc:
        return {
            "connected": False,
            "cash": 0.0,
            "net": 0.0,
            "error": (
                f"generateSession failed: {exc}. "
                "Check ANGEL_ONE_LIVE_CLIENT_CODE, ANGEL_ONE_LIVE_PASSWORD, "
                "and ANGEL_ONE_LIVE_TOTP_SECRET."
            ),
        }
    except Exception as exc:
        return {
            "connected": False,
            "cash": 0.0,
            "net": 0.0,
            "error": f"Unexpected error during connect: {type(exc).__name__}: {exc}",
        }

    # Connected — attempt getRMS
    cash: float = 0.0
    net: float = 0.0
    rms_error: str | None = None
    try:
        rms = broker._smart_api.getRMS()
        if rms and rms.get("status"):
            data_rms = rms.get("data") or {}
            try:
                cash = float(data_rms.get("availablecash", 0.0) or 0.0)
                net = float(data_rms.get("net", cash) or cash)
            except (ValueError, TypeError):
                rms_error = "getRMS() returned unparseable account data"
        else:
            rms_error = (
                f"getRMS() returned non-success: {(rms or {}).get('message', 'unknown')}"
            )
    except Exception as exc:
        rms_error = f"getRMS() raised: {type(exc).__name__}: {exc}"
    finally:
        try:
            if broker is not None:
                broker.disconnect()
        except Exception:
            pass

    return {
        "connected": True,
        "cash": cash,
        "net": net,
        "error": rms_error,
    }


# ---------------------------------------------------------------------------
# ENDPOINT 1: GET /api/strategy/signals-today
# ---------------------------------------------------------------------------

@app.get("/api/strategy/signals-today")
def get_signals_today(_: str = Depends(verify)):
    """Count of signals today by strategy and acceptance status.

    SQL: SELECT strategy, COUNT(*) as total, SUM(accepted) as accepted
         FROM signals WHERE ts >= date('now') GROUP BY strategy

    Returns: list of {strategy, total, accepted, rejected}
    """
    db_path = Path("data/agent.db")
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT strategy,
                   COUNT(*)       AS total,
                   SUM(accepted)  AS accepted
            FROM signals
            WHERE ts >= date('now')
            GROUP BY strategy
            ORDER BY total DESC
            """
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            total = int(r["total"] or 0)
            accepted = int(r["accepted"] or 0)
            result.append({
                "strategy": r["strategy"],
                "total": total,
                "accepted": accepted,
                "rejected": total - accepted,
            })
        return result
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# ENDPOINT 2: GET /api/performance/summary
# ---------------------------------------------------------------------------

@app.get("/api/performance/summary")
def get_performance_summary(_: str = Depends(verify)):
    """Quick performance summary from the trades table.

    Returns: {today: {trades, pnl, win_rate}, week: {...}, month: {...}, all_time: {...}}
    """
    db_path = Path("data/agent.db")

    def _period_stats(conn, cutoff_expr: str) -> dict:
        """Compute trades, pnl, win_rate for trades since *cutoff_expr* (SQL expression)."""
        row = conn.execute(
            f"""
            SELECT COUNT(*)              AS n_trades,
                   COALESCE(SUM(pnl), 0) AS pnl,
                   COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) AS wins
            FROM trades
            WHERE closed_at >= {cutoff_expr}
            """
        ).fetchone()
        n = int(row["n_trades"] or 0)
        pnl = float(row["pnl"] or 0)
        wins = int(row["wins"] or 0)
        win_rate = round(wins / n * 100, 1) if n > 0 else 0.0
        return {"trades": n, "pnl": round(pnl, 2), "win_rate": win_rate}

    empty = {"trades": 0, "pnl": 0.0, "win_rate": 0.0}

    if not db_path.exists():
        return {"today": empty, "week": empty, "month": empty, "all_time": empty}

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        today = _period_stats(conn, "date('now')")
        week = _period_stats(conn, "date('now', '-7 days')")
        month = _period_stats(conn, "date('now', '-30 days')")
        all_time = _period_stats(conn, "'1970-01-01'")
        conn.close()
        return {"today": today, "week": week, "month": month, "all_time": all_time}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# ENDPOINT 3: GET /api/backtest/debug
# ---------------------------------------------------------------------------

@app.get("/api/backtest/debug")
def get_backtest_debug(days: int = 90, _: str = Depends(verify)):
    """Return the same data as cli.py debug-rejections but as JSON.

    Query param: ``days`` — look-back window in calendar days (default 90).

    Returns:
    {regime_distribution, rejection_breakdown, strategy_signals,
     top_symbols, metrics, period_start, period_end, capital}
    """
    from datetime import timedelta
    from backtest.data_loader import HistoricalLoader
    from backtest.engine import BacktestEngine
    from backtest.metrics import compute_metrics as _compute_backtest_metrics
    from core.config import load_settings
    from core.data.universe import resolve_universe

    try:
        settings = load_settings("config/default.yaml")
        debug_capital = 500_000.0
        settings.capital.initial_inr = debug_capital

        loader = HistoricalLoader()
        nifty = loader.load_nifty(days=days + 30)
        if nifty is None or nifty.empty:
            return {"error": "Failed to load Nifty history"}

        symbols = resolve_universe(settings.universe.source, settings.universe.symbols)
        history = loader.load_universe(symbols, days=days + 30)

        end = nifty.index[-1].to_pydatetime()
        start = end - timedelta(days=days)
        engine = BacktestEngine(settings)
        r = engine.run(
            symbol_history=history,
            nifty_history=nifty,
            starting_equity=debug_capital,
            start_date=start,
            end_date=end,
        )
        m = _compute_backtest_metrics(
            trades=r.trades,
            equity_curve=r.equity_curve,
            starting_equity=debug_capital,
            period_days=(r.period_end - r.period_start).days,
        )

        # Strategy signals breakdown
        strategy_signals = []
        for strat, total in sorted(
            r.signal_count_by_strategy.items(), key=lambda kv: -kv[1]
        ):
            accepted = r.accepted_count_by_strategy.get(strat, 0)
            strategy_signals.append({
                "strategy": strat,
                "generated": total,
                "accepted": accepted,
                "rejected": total - accepted,
            })

        # Top 15 symbols by signal count
        top_symbols = [
            {"symbol": sym, "signals": cnt}
            for sym, cnt in sorted(
                r.signal_count_by_symbol.items(), key=lambda kv: -kv[1]
            )[:15]
        ]

        # Regime distribution with percentages
        total_days = sum(r.regime_distribution.values())
        regime_distribution = {}
        for regime in ["TREND", "RANGE", "VOLATILE", "UNKNOWN"]:
            count = r.regime_distribution.get(regime, 0)
            pct = round(count / total_days * 100, 1) if total_days else 0.0
            regime_distribution[regime] = {"days": count, "pct": pct}

        return {
            "period_start": r.period_start.date().isoformat(),
            "period_end": r.period_end.date().isoformat(),
            "capital": debug_capital,
            "regime_distribution": regime_distribution,
            "rejection_breakdown": dict(
                sorted(r.rejection_breakdown.items(), key=lambda kv: -kv[1])
            ),
            "signal_funnel": {
                "signals_generated": r.signal_count,
                "signals_accepted": r.accepted_count,
                "signals_rejected": r.rejected_count,
                "qty_zero_count": r.qty_zero_count,
            },
            "strategy_signals": strategy_signals,
            "top_symbols": top_symbols,
            "metrics": {
                "trades": m.n_trades,
                "win_rate_pct": round(m.win_rate_pct, 1),
                "profit_factor": round(m.profit_factor, 2),
                "sharpe": round(m.sharpe, 3),
                "max_drawdown_pct": round(m.max_drawdown_pct, 2),
                "cagr_pct": round(m.cagr_pct, 2),
            },
        }
    except Exception as exc:
        logger.exception("backtest/debug error")
        return {"error": str(exc)}


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


@app.get("/api/events/live")
async def sse_live(request: Request, _: str = Depends(verify)):
    """Server-Sent Events feed — simpler alternative to WebSocket for status updates.

    Streams the current snapshot as a JSON ``data:`` event every 2 seconds.
    Clients that have browser issues with WebSocket auth can use this endpoint
    with standard HTTP Basic Auth headers.

    Each event is formatted per the SSE spec::

        data: <json>\\n\\n

    The connection closes automatically when the client disconnects.
    """

    async def event_stream():
        while True:
            if await request.is_disconnected():
                break
            snap = read_snapshot("data/snapshot.json") or {"running": False}
            yield f"data: {json.dumps(snap)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
