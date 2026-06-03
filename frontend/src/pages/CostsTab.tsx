import { useApi } from "../hooks/useSnapshot"
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts"

export function CostsTab() {
  const { data: costs, loading } = useApi("/api/costs?days=365", 60000)
  const rows: any[] = Array.isArray((costs as any)?.by_strategy)
    ? (costs as any).by_strategy
    : Array.isArray(costs)
      ? (costs as any[])
      : []

  const isEmpty = !loading && rows.length === 0

  const totalCharges = rows.reduce((s: number, r: any) => s + (r.total ?? 0), 0)
  const totalPnl = rows.reduce((s: number, r: any) => s + (r.gross_pnl ?? 0), 0)
  const netPnl = totalPnl - totalCharges
  const chargesPct = totalPnl ? (totalCharges / Math.abs(totalPnl) * 100).toFixed(1) : "0"

  return (
    <div className="tab-content">
      <h1 className="tab-title">💰 Trade Costs &amp; Charges</h1>

      {loading ? (
        <div className="empty-state">
          <div className="spinner" style={{ margin: "0 auto 12px" }} />
          Loading cost data…
        </div>
      ) : (
        <>
          {/* KPI row — always shown (zeroed when no trades) */}
          <div className="kpi-row">
            <div className="kpi-card">
              <div className="kpi-label">Total Charges</div>
              <div className="kpi-value yellow">
                ₹{totalCharges.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Gross P&amp;L</div>
              <div className={totalPnl >= 0 ? "kpi-value green" : "kpi-value red"}>
                ₹{totalPnl.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Net P&amp;L</div>
              <div className={netPnl >= 0 ? "kpi-value green" : "kpi-value red"}>
                ₹{netPnl.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Charges / Gross</div>
              <div className="kpi-value">{chargesPct}%</div>
            </div>
          </div>

          {isEmpty ? (
            /* Empty state with cost model explanation */
            <div
              className="info-card"
              style={{ maxWidth: 560, margin: "32px auto 0", padding: "20px 24px" }}
            >
              <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 8 }}>
                No trades yet — cost analysis will appear once the agent starts trading.
              </div>
              <p style={{ color: "var(--text2)", marginBottom: 16, lineHeight: 1.6 }}>
                Per-strategy charge breakdown and charts will populate here automatically.
              </p>
              <div
                style={{
                  background: "var(--bg3)",
                  borderRadius: 8,
                  padding: "14px 16px",
                }}
              >
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>
                  Cost model used for every trade
                </div>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <tbody>
                    <tr>
                      <td style={{ padding: "5px 0", color: "var(--text2)" }}>Brokerage</td>
                      <td style={{ padding: "5px 0", textAlign: "right", color: "var(--text)" }}>
                        ₹20 per order (flat)
                      </td>
                    </tr>
                    <tr>
                      <td style={{ padding: "5px 0", color: "var(--text2)" }}>STT</td>
                      <td style={{ padding: "5px 0", textAlign: "right", color: "var(--text)" }}>
                        0.025% on sell side
                      </td>
                    </tr>
                    <tr>
                      <td style={{ padding: "5px 0", color: "var(--text2)" }}>Exchange transaction</td>
                      <td style={{ padding: "5px 0", textAlign: "right", color: "var(--text)" }}>
                        0.00345% per leg
                      </td>
                    </tr>
                    <tr>
                      <td style={{ padding: "5px 0", color: "var(--text2)" }}>GST</td>
                      <td style={{ padding: "5px 0", textAlign: "right", color: "var(--text)" }}>
                        18% on brokerage + exchange charge
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <>
              <section className="section chart-section">
                <h2>Charges by Strategy</h2>
                <ResponsiveContainer width="100%" height={200}>
                  <BarChart data={rows} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
                    <XAxis
                      type="number"
                      tick={{ fontSize: 11, fill: "#718096" }}
                      tickFormatter={(v: number) =>
                        "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: 0 })
                      }
                    />
                    <YAxis type="category" dataKey="strategy" tick={{ fontSize: 11, fill: "#718096" }} width={110} />
                    <Tooltip
                      formatter={(v: any) => [
                        "₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 }),
                      ]}
                      contentStyle={{ background: "#1a202c", border: "none" }}
                    />
                    <Bar dataKey="total" fill="#ecc94b" name="Charges" />
                  </BarChart>
                </ResponsiveContainer>
              </section>

              <section className="section">
                <h2>Per-Strategy Breakdown</h2>
                <table className="trade-table">
                  <thead>
                    <tr>
                      <th>Strategy</th>
                      <th>Trades</th>
                      <th>Gross P&amp;L</th>
                      <th>Charges</th>
                      <th>Net P&amp;L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r: any, i: number) => {
                      const net = (r.gross_pnl ?? 0) - (r.total ?? 0)
                      return (
                        <tr key={i} className={net > 0 ? "win" : "loss"}>
                          <td>{r.strategy}</td>
                          <td>{r.trades}</td>
                          <td>
                            ₹{(r.gross_pnl ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                          </td>
                          <td className="yellow">
                            ₹{(r.total ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                          </td>
                          <td className={net > 0 ? "green" : "red"}>
                            ₹{net.toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </section>
            </>
          )}
        </>
      )}
    </div>
  )
}
