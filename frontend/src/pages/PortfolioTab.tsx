import { useApi } from "../hooks/useSnapshot"

export function PortfolioTab() {
  const { data: portfolio } = useApi("/api/portfolio/angel-one", 60000)
  const { data: snapshot } = useApi("/api/snapshot", 5000)

  const p = portfolio as any
  const s = snapshot as any
  const connected = p?.connected ?? false
  const paperEquity = s?.equity ?? 0
  const liveEquity = p?.net_value ?? 0
  const diff = liveEquity - paperEquity

  return (
    <div className="tab-content">
      <h1 className="tab-title">🏦 Angel One Portfolio</h1>
      <div className="warning-banner">⚠️ Read-Only View — No orders are placed via this dashboard</div>

      {!connected ? (
        <div className="empty-state">
          <p>Not connected to Angel One.</p>
          <p>Add credentials to <code>.env</code> and restart the API service.</p>
          <div className="info-box" style={{ marginTop: 12, textAlign: "left" }}>
            <code>ANGEL_ONE_API_KEY=your_key</code>
          </div>
        </div>
      ) : (
        <>
          <div className="kpi-row">
            <div className="kpi-card"><div className="kpi-label">Live Net Value</div>
              <div className="kpi-value">₹{liveEquity.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div></div>
            <div className="kpi-card"><div className="kpi-label">Paper Agent Equity</div>
              <div className="kpi-value">₹{paperEquity.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div></div>
            <div className="kpi-card"><div className="kpi-label">Difference</div>
              <div className={diff >= 0 ? "kpi-value green" : "kpi-value red"}>₹{diff.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div></div>
          </div>

          <section className="section">
            <h2>Live Positions</h2>
            {p?.positions?.length > 0 ? (
              <table className="trade-table">
                <thead><tr><th>Symbol</th><th>Qty</th><th>Avg Price</th><th>LTP</th><th>P&L</th></tr></thead>
                <tbody>{p.positions.map((pos: any, i: number) => (
                  <tr key={i} className={pos.pnl >= 0 ? "win" : "loss"}>
                    <td>{pos.symbol}</td><td>{pos.qty}</td>
                    <td>₹{(pos.avg_price ?? 0).toFixed(2)}</td>
                    <td>₹{(pos.ltp ?? 0).toFixed(2)}</td>
                    <td className={pos.pnl >= 0 ? "green" : "red"}>₹{(pos.pnl ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td>
                  </tr>
                ))}</tbody>
              </table>
            ) : <div className="empty-state">No live positions</div>}
          </section>
        </>
      )}
    </div>
  )
}
