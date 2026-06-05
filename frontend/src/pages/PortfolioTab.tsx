import { useApi } from "../hooks/useSnapshot"

export function PortfolioTab({ snap }: { snap?: any }) {
  const { data: portfolio, loading: loadingPortfolio } = useApi("/api/portfolio/angel-one", 60000)

  const p = portfolio as any
  // Use the snap prop passed from Layout instead of polling /api/snapshot independently
  const connected = p?.connected ?? false
  const paperEquity = snap?.equity ?? 0
  const liveEquity = p?.net_value ?? 0
  const diff = liveEquity - paperEquity

  return (
    <div className="tab-content">
      <h1 className="tab-title">Angel One Portfolio</h1>
      <div className="warning-banner">Read-Only View — No orders are placed via this dashboard</div>

      {/* Paper vs Live comparison — always shown */}
      <section className="section">
        <h2>Paper vs Live</h2>
        <table className="trade-table">
          <thead>
            <tr>
              <th>Account</th>
              <th>Equity / Net Value</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Paper Agent</td>
              <td className="green">
                ₹{paperEquity.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </td>
              <td><span className="badge green">Simulated</span></td>
            </tr>
            <tr>
              <td>Live Account (Angel One)</td>
              <td className={connected ? "" : "red"}>
                {connected
                  ? `₹${liveEquity.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`
                  : "Not connected"}
              </td>
              <td>
                <span className={connected ? "badge green" : "badge red"}>
                  {connected ? "Live" : "Offline"}
                </span>
              </td>
            </tr>
            {connected && (
              <tr>
                <td>Difference (Live − Paper)</td>
                <td className={diff >= 0 ? "green" : "red"}>
                  ₹{diff.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                </td>
                <td></td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      {loadingPortfolio && !portfolio ? (
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "20px 0" }}>
          <div className="spinner" />
          <span style={{ color: "var(--text2)", fontSize: 13 }}>Connecting to Angel One…</span>
        </div>
      ) : !connected ? (
        <>
          {/* Setup guide */}
          <section className="section">
            <h2>Setup Guide</h2>
            <div className="info-box">
              <p style={{ marginBottom: 12, lineHeight: 1.7 }}>
                Angel One credentials are not configured. This section shows your live brokerage
                account positions for reference only — the paper agent uses fully simulated positions
                above and is not affected.
              </p>

              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ background: "var(--bg3)", borderRadius: 8, padding: "12px 14px" }}>
                  <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>
                    Step 1 — Create a DATA-ONLY Angel One API app
                  </div>
                  <div style={{ color: "var(--text2)", fontSize: 12, lineHeight: 1.6 }}>
                    Log in to{" "}
                    <a
                      href="https://smartapi.angelbroking.com"
                      target="_blank"
                      rel="noopener noreferrer"
                      style={{ color: "var(--blue)" }}
                    >
                      smartapi.angelbroking.com
                    </a>
                    , create a new app, and note your API key and secret. Use a dedicated
                    app — do NOT reuse credentials from any order-placing integration.
                  </div>
                </div>

                <div style={{ background: "var(--bg3)", borderRadius: 8, padding: "12px 14px" }}>
                  <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>
                    Step 2 — Add credentials to .env
                  </div>
                  <code style={{ display: "block", background: "var(--bg2)", padding: "8px 12px", borderRadius: 6, fontSize: 12, lineHeight: 1.8 }}>
                    ANGEL_ONE_API_KEY=your_api_key<br />
                    ANGEL_ONE_CLIENT_ID=your_client_id<br />
                    ANGEL_ONE_PASSWORD=your_mpin<br />
                    ANGEL_ONE_TOTP_SECRET=your_totp_secret
                  </code>
                </div>

                <div style={{ background: "var(--bg3)", borderRadius: 8, padding: "12px 14px" }}>
                  <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>
                    Step 3 — Restart the API service
                  </div>
                  <code style={{ display: "block", background: "var(--bg2)", padding: "8px 12px", borderRadius: 6, fontSize: 12 }}>
                    python cli.py run
                  </code>
                </div>

                <div
                  style={{
                    background: "rgba(252,129,129,.07)",
                    border: "1px solid rgba(252,129,129,.2)",
                    borderRadius: 8,
                    padding: "10px 14px",
                    fontSize: 12,
                    color: "var(--text2)",
                    lineHeight: 1.6,
                  }}
                >
                  <strong style={{ color: "var(--red)" }}>Security note:</strong> These credentials
                  are for market data only. Order-placement methods are disabled at the code level
                  and raise a RuntimeError if called. Live order execution requires a separate
                  broker module.
                </div>
              </div>
            </div>
          </section>
        </>
      ) : (
        <>
          {/* KPI row when connected */}
          <div className="kpi-row">
            <div className="kpi-card">
              <div className="kpi-label">Live Net Value</div>
              <div className="kpi-value">
                ₹{liveEquity.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Paper Agent Equity</div>
              <div className="kpi-value">
                ₹{paperEquity.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Difference</div>
              <div className={diff >= 0 ? "kpi-value green" : "kpi-value red"}>
                ₹{diff.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </div>
            </div>
          </div>

          {/* Live positions */}
          <section className="section">
            <h2>Live Positions</h2>
            {p?.positions?.length > 0 ? (
              <table className="trade-table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Qty</th>
                    <th>Avg Price</th>
                    <th>LTP</th>
                    <th>P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {p.positions.map((pos: any, i: number) => (
                    <tr key={i} className={pos.pnl >= 0 ? "win" : "loss"}>
                      <td>{pos.symbol}</td>
                      <td>{pos.qty}</td>
                      <td>₹{(pos.avg_price ?? 0).toFixed(2)}</td>
                      <td>₹{(pos.ltp ?? 0).toFixed(2)}</td>
                      <td className={pos.pnl >= 0 ? "green" : "red"}>
                        ₹{(pos.pnl ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="empty-state">No live positions</div>
            )}
          </section>
        </>
      )}
    </div>
  )
}
