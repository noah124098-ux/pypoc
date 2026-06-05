from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
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
