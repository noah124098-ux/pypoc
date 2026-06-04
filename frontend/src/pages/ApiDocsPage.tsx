// ─────────────────────────────────────────────────────────────────────────────
// API Documentation Page
// Lists all /api/* endpoints with descriptions, auth info, and example responses.
// ─────────────────────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────
interface EndpointDef {
  method: "GET" | "POST" | "WS"
  path: string
  auth: boolean
  description: string
  params?: string
  example: string
}

// ─────────────────────────────────────────────────────────────────────────────
// Endpoint catalogue
// ─────────────────────────────────────────────────────────────────────────────
const ENDPOINTS: { group: string; endpoints: EndpointDef[] }[] = [
  {
    group: "Health & Status",
    endpoints: [
      {
        method: "GET",
        path: "/health",
        auth: false,
        description: "Simple liveness check. No auth required — safe for load balancers and uptime monitors.",
        example: '{"status": "ok"}',
      },
      {
        method: "GET",
        path: "/api/status",
        auth: false,
        description:
          "Comprehensive system status: API version, agent state (running/halted), current regime, gate status, NSSM service detection, and UTC timestamp. No auth required.",
        example: JSON.stringify(
          {
            api_version: "2.0",
            agent_running: true,
            agent_halted: false,
            equity: 512340,
            regime: "TREND",
            gate_passed: false,
            gate_age_days: 5.2,
            services: { agent: true, dashboard: true, mcp: false },
            timestamp: "2026-06-04T10:30:00+00:00",
          },
          null,
          2,
        ),
      },
      {
        method: "GET",
        path: "/api/system",
        auth: true,
        description:
          "Machine resource metrics: CPU %, memory usage (GB + %), disk usage (GB + %), system uptime, and count of Python processes.",
        example: JSON.stringify(
          {
            cpu_pct: 12.5,
            memory_used_gb: 3.1,
            memory_total_gb: 16.0,
            memory_pct: 19.4,
            disk_used_gb: 45.2,
            disk_free_gb: 186.0,
            disk_pct: 19.6,
            uptime_hours: 72.4,
            python_processes: 3,
          },
          null,
          2,
        ),
      },
    ],
  },
  {
    group: "Agent Data",
    endpoints: [
      {
        method: "GET",
        path: "/api/snapshot",
        auth: true,
        description:
          "Full live snapshot: equity, day P&L, positions, regime, drawdown, circuit state. Pushed every second over WebSocket — use /ws/live for real-time.",
        example: JSON.stringify(
          { running: true, halted: false, equity: 512340, current_regime: "TREND", positions: [] },
          null,
          2,
        ),
      },
      {
        method: "GET",
        path: "/api/positions",
        auth: true,
        description: "Current open positions: symbol, strategy, side, qty, entry price, unrealised P&L.",
        example: JSON.stringify(
          [{ symbol: "RELIANCE", strategy: "trend_breakout", side: "buy", qty: 10, entry_price: 2450.0, unrealised_pnl: 320.0 }],
          null,
          2,
        ),
      },
      {
        method: "GET",
        path: "/api/equity",
        auth: true,
        params: "limit (1–200, default 50), offset (default 0)",
        description: "Equity snapshots with cursor pagination. Returns {data, total, limit, offset, has_more}.",
        example: JSON.stringify(
          { data: [{ ts: 1748000000, equity: 510000 }], total: 1440, limit: 50, offset: 0, has_more: true },
          null,
          2,
        ),
      },
      {
        method: "GET",
        path: "/api/regime",
        auth: true,
        params: "limit (default 100)",
        description: "Recent regime classifications with timestamp, regime label, ADX, and BB-width values.",
        example: JSON.stringify([{ ts: "2026-06-04T09:15:00", regime: "TREND", adx: 28.3, bb_width: 0.042 }], null, 2),
      },
      {
        method: "GET",
        path: "/api/pnl",
        auth: true,
        description: "Aggregated P&L summary: total, today, this week, this month, win/loss counts.",
        example: JSON.stringify({ total_pnl: 12340, today_pnl: 450, wins: 22, losses: 14 }, null, 2),
      },
      {
        method: "GET",
        path: "/api/config",
        auth: true,
        description: "Live runtime config summary: risk params, strategy flags, circuit-breaker thresholds.",
        example: JSON.stringify({ max_positions: 5, risk_per_trade_pct: 1.0, daily_loss_limit_pct: 3.0 }, null, 2),
      },
      {
        method: "GET",
        path: "/api/universe",
        auth: true,
        description: "Nifty 50 trading universe: symbol list, sector tags, and any per-symbol overrides.",
        example: JSON.stringify({ symbols: ["RELIANCE", "TCS", "INFY"], count: 50 }, null, 2),
      },
      {
        method: "GET",
        path: "/api/atm-iv",
        auth: true,
        description: "Nifty ATM call implied volatility (%) from NSE option chain. Returns null when NSE is unreachable — fail-open.",
        example: JSON.stringify({ atm_iv: 12.5, source: "nse_option_chain", cached: true }, null, 2),
      },
    ],
  },
  {
    group: "Trades & Signals",
    endpoints: [
      {
        method: "GET",
        path: "/api/trades",
        auth: true,
        params: "limit (1–200, default 50), offset (default 0)",
        description: "Recent closed trades with cursor pagination: symbol, strategy, side, entry/exit prices, P&L, charges, timing.",
        example: JSON.stringify(
          { data: [{ id: 1, symbol: "TCS", strategy: "rsi_momentum", pnl: 1200, closed_at: "2026-06-03T15:20:00" }], total: 87, has_more: true },
          null,
          2,
        ),
      },
      {
        method: "GET",
        path: "/api/trades/stats",
        auth: true,
        description: "Aggregate trade statistics computed via fast SQL: total trades, P&L, win rate, profit factor, Sharpe, max drawdown.",
        example: JSON.stringify({ total_trades: 87, total_pnl: 18400, win_rate: 55.2, profit_factor: 1.78, sharpe: 0.89, max_dd: 8.1 }, null, 2),
      },
      {
        method: "GET",
        path: "/api/trades/list",
        auth: true,
        params: "limit (default 50)",
        description: "Compact trade list for the replay selector: id, symbol, strategy, P&L, timestamps.",
        example: JSON.stringify([{ id: 1, symbol: "INFY", strategy: "trend_breakout", pnl: 870 }], null, 2),
      },
      {
        method: "GET",
        path: "/api/trade/{trade_id}",
        auth: true,
        description: "Full trade detail including signal rationale, confidence score, and regime at entry time.",
        example: JSON.stringify({ id: 42, symbol: "WIPRO", signal_rationale: "ADX>25 + breakout", signal_confidence: 0.78, signal_regime: "TREND" }, null, 2),
      },
      {
        method: "GET",
        path: "/api/signals",
        auth: true,
        params: "limit (1–200, default 50), offset (default 0)",
        description: "Recent strategy signals (accepted and rejected) with cursor pagination.",
        example: JSON.stringify({ data: [{ ts: "2026-06-04T10:00:00", symbol: "HDFCBANK", strategy: "supertrend", accepted: true }], total: 210, has_more: true }, null, 2),
      },
      {
        method: "GET",
        path: "/api/guardrails",
        auth: true,
        params: "limit (1–200, default 50), offset (default 0)",
        description: "Recent guardrail rejection events: which rule blocked the order, symbol, strategy, and timestamp.",
        example: JSON.stringify({ data: [{ ts: 1748000100, symbol: "SBIN", strategy: "rsi_momentum", rule: "daily_loss_circuit" }], total: 5 }, null, 2),
      },
    ],
  },
  {
    group: "Analytics",
    endpoints: [
      {
        method: "GET",
        path: "/api/analytics/strategy-performance",
        auth: true,
        params: "days (default 90)",
        description: "Per-strategy breakdown: n_trades, total P&L, win rate, profit factor, avg P&L, Sharpe, max drawdown.",
        example: JSON.stringify({ trend_breakout: { n_trades: 30, total_pnl: 8200, win_rate: 60.0 } }, null, 2),
      },
      {
        method: "GET",
        path: "/api/analytics/monthly-pnl",
        auth: true,
        params: "days (default 365)",
        description: "Monthly P&L summary: one row per calendar month with pnl, n_trades, win_rate.",
        example: JSON.stringify([{ month: "2026-05", pnl: 4100, n_trades: 18, win_rate: 55.6 }], null, 2),
      },
      {
        method: "GET",
        path: "/api/analytics/extended-metrics",
        auth: true,
        params: "days (default 365)",
        description: "Full extended metrics for all trades in window: avg hold hours, avg win/loss, best/worst trade, Sharpe.",
        example: JSON.stringify({ n_trades: 87, total_pnl: 18400, win_rate: 55.2, avg_hold_hours: 4.2, sharpe: 0.89 }, null, 2),
      },
      {
        method: "GET",
        path: "/api/analytics/sector-performance",
        auth: true,
        params: "days (default 365)",
        description: "Per-NSE-sector breakdown: n_trades, win_rate, pnl, best_symbol, worst_symbol for Nifty 50.",
        example: JSON.stringify({ Financial: { n_trades: 22, win_rate: 59.1, pnl: 6800, best_symbol: "HDFCBANK" } }, null, 2),
      },
      {
        method: "GET",
        path: "/api/costs",
        auth: true,
        params: "days (default 365)",
        description: "Charges breakdown: total charges, gross P&L, net P&L, per-strategy charges, recent 20 trades with per-trade charges.",
        example: JSON.stringify({ total_charges: 1240, gross_pnl: 18400, net_pnl: 17160, by_strategy: [{ strategy: "trend_breakout", total_charges: 720 }] }, null, 2),
      },
    ],
  },
  {
    group: "Backtest & Gate",
    endpoints: [
      {
        method: "GET",
        path: "/api/gate",
        auth: true,
        description:
          "Backtest gate result from data/backtest_gate.json: passed, Sharpe, MaxDD, win_rate, profit_factor, n_trades, timestamp.",
        example: JSON.stringify({ passed: false, sharpe: 0.32, max_drawdown_pct: 10.5, win_rate: 35.8, profit_factor: 1.41, n_trades: 189 }, null, 2),
      },
    ],
  },
  {
    group: "Misc",
    endpoints: [
      {
        method: "GET",
        path: "/api/eod-review",
        auth: true,
        description: "Last EOD Claude review from data/last_review.json: parameter proposals, observations, and risk notes.",
        example: JSON.stringify({ available: true, summary: "Trend regime trades performing well; RSI generating excess churn.", proposals: [] }, null, 2),
      },
      {
        method: "GET",
        path: "/api/portfolio/angel-one",
        auth: true,
        description: "Live Angel One portfolio: holdings, margin, P&L. Returns disconnected stub if credentials are absent.",
        example: JSON.stringify({ connected: false, message: "Set ANGEL_ONE_API_KEY in .env to connect" }, null, 2),
      },
    ],
  },
  {
    group: "Commands",
    endpoints: [
      {
        method: "POST",
        path: "/api/command/halt",
        auth: true,
        params: "reason (query string, optional)",
        description: "Enqueue a halt_agent command via the file-based command queue. The agent will stop accepting new orders on its next tick.",
        example: JSON.stringify({ queued: true, command_id: "cmd_abc123" }, null, 2),
      },
      {
        method: "POST",
        path: "/api/command/resume",
        auth: true,
        description: "Enqueue a resume_agent command. Clears the halt flag and allows the agent to trade again.",
        example: JSON.stringify({ queued: true, command_id: "cmd_def456" }, null, 2),
      },
    ],
  },
  {
    group: "WebSocket",
    endpoints: [
      {
        method: "WS",
        path: "/ws/live",
        auth: false,
        params: "token (query string — must match DASHBOARD_PASSWORD, default: pypoc2024)",
        description:
          "WebSocket endpoint. Server pushes a full snapshot JSON every second. Connect with: ws://host:8502/ws/live?token=<password>. Closes with code 1008 on invalid token.",
        example: '{"running": true, "equity": 512340, "current_regime": "TREND", "halted": false, "positions": [...]}',
      },
    ],
  },
]

// ─────────────────────────────────────────────────────────────────────────────
// Components
// ─────────────────────────────────────────────────────────────────────────────
function MethodBadge({ method }: { method: "GET" | "POST" | "WS" }) {
  const colors: Record<string, string> = {
    GET: "#48bb78",
    POST: "#4299e1",
    WS: "#9f7aea",
  }
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 700,
        fontFamily: "monospace",
        background: `${colors[method]}22`,
        color: colors[method],
        border: `1px solid ${colors[method]}44`,
        flexShrink: 0,
      }}
    >
      {method}
    </span>
  )
}

function AuthBadge({ required }: { required: boolean }) {
  if (!required) return null
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 7px",
        borderRadius: 4,
        fontSize: 10,
        fontWeight: 600,
        background: "rgba(236,201,75,0.12)",
        color: "#ecc94b",
        border: "1px solid rgba(236,201,75,0.3)",
        flexShrink: 0,
      }}
    >
      Auth
    </span>
  )
}

function EndpointCard({ ep }: { ep: EndpointDef }) {
  return (
    <div
      style={{
        background: "var(--bg2)",
        border: "1px solid var(--border)",
        borderRadius: 8,
        padding: "14px 16px",
        marginBottom: 12,
      }}
    >
      {/* Header row */}
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 8 }}>
        <MethodBadge method={ep.method} />
        <code
          style={{
            fontSize: 13,
            color: "var(--text)",
            background: "var(--bg3)",
            padding: "2px 8px",
            borderRadius: 4,
            fontFamily: "monospace",
            fontWeight: 600,
          }}
        >
          {ep.path}
        </code>
        <AuthBadge required={ep.auth} />
      </div>

      {/* Description */}
      <p style={{ fontSize: 13, color: "var(--text2)", margin: "0 0 10px", lineHeight: 1.6 }}>
        {ep.description}
      </p>

      {/* Params */}
      {ep.params && (
        <div style={{ fontSize: 12, color: "var(--text2)", marginBottom: 10 }}>
          <span style={{ color: "var(--text)", fontWeight: 600 }}>Params: </span>
          {ep.params}
        </div>
      )}

      {/* Example response */}
      <details>
        <summary
          style={{
            fontSize: 11,
            color: "var(--text2)",
            cursor: "pointer",
            userSelect: "none",
            marginBottom: 6,
          }}
        >
          Example response
        </summary>
        <pre
          style={{
            background: "var(--bg3)",
            borderRadius: 6,
            padding: "10px 12px",
            fontSize: 11,
            color: "#a0aec0",
            overflowX: "auto",
            margin: "6px 0 0",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            fontFamily: "monospace",
            lineHeight: 1.5,
          }}
        >
          {ep.example}
        </pre>
      </details>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main ApiDocsPage
// ─────────────────────────────────────────────────────────────────────────────
export function ApiDocsPage() {
  return (
    <div className="tab-content">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, marginBottom: 8 }}>
        <h1 className="tab-title" style={{ marginBottom: 0 }}>
          API Reference
        </h1>
        <a
          href="/docs"
          target="_blank"
          rel="noopener noreferrer"
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "7px 14px",
            borderRadius: 6,
            background: "rgba(66,153,225,0.12)",
            color: "#4299e1",
            border: "1px solid rgba(66,153,225,0.3)",
            fontSize: 13,
            fontWeight: 600,
            textDecoration: "none",
          }}
        >
          Open Swagger UI
          <span style={{ fontSize: 11, opacity: 0.8 }}>↗</span>
        </a>
      </div>

      {/* Auth note */}
      <div
        style={{
          background: "rgba(236,201,75,0.08)",
          border: "1px solid rgba(236,201,75,0.25)",
          borderRadius: 8,
          padding: "12px 16px",
          marginBottom: 24,
          fontSize: 13,
          color: "var(--text2)",
          lineHeight: 1.6,
        }}
      >
        <strong style={{ color: "#ecc94b" }}>Authentication:</strong> Endpoints marked{" "}
        <span
          style={{
            display: "inline-block",
            padding: "1px 6px",
            borderRadius: 3,
            fontSize: 10,
            fontWeight: 600,
            background: "rgba(236,201,75,0.12)",
            color: "#ecc94b",
            border: "1px solid rgba(236,201,75,0.3)",
          }}
        >
          Auth
        </span>{" "}
        require HTTP Basic authentication with username <code>admin</code> and the
        dashboard password (default: <code>pypoc2024</code>, override via
        <code> DASHBOARD_PASSWORD</code> env var). Use the{" "}
        <code>Authorization: Basic &lt;base64&gt;</code> header or the{" "}
        <a href="/docs" target="_blank" rel="noopener noreferrer" style={{ color: "#4299e1" }}>
          Swagger UI
        </a>
        .
      </div>

      {ENDPOINTS.map(group => (
        <section key={group.group} style={{ marginBottom: 32 }}>
          <h2
            style={{
              fontSize: 15,
              fontWeight: 700,
              color: "var(--text)",
              marginBottom: 14,
              paddingBottom: 8,
              borderBottom: "1px solid var(--border)",
            }}
          >
            {group.group}
          </h2>
          {group.endpoints.map(ep => (
            <EndpointCard key={`${ep.method}:${ep.path}`} ep={ep} />
          ))}
        </section>
      ))}
    </div>
  )
}
