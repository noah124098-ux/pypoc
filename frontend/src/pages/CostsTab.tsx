import { useApi } from "../hooks/useSnapshot"
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts"

export function CostsTab() {
  const { data: costs } = useApi("/api/costs?days=365", 60000)
  const rows = Array.isArray(costs) ? costs : []

  const totalCharges = rows.reduce((s: number, r: any) => s + (r.total ?? 0), 0)
  const totalPnl = rows.reduce((s: number, r: any) => s + (r.gross_pnl ?? 0), 0)
  const netPnl = totalPnl - totalCharges
  const chargesPct = totalPnl ? (totalCharges / Math.abs(totalPnl) * 100).toFixed(1) : "0"

  return (
    <div className="tab-content">
      <h1 className="tab-title">💰 Trade Costs & Charges</h1>

      <div className="kpi-row">
        <div className="kpi-card"><div className="kpi-label">Total Charges</div>
          <div className="kpi-value yellow">₹{totalCharges.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div></div>
        <div className="kpi-card"><div className="kpi-label">Gross P&L</div>
          <div className={totalPnl >= 0 ? "kpi-value green" : "kpi-value red"}>₹{totalPnl.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div></div>
        <div className="kpi-card"><div className="kpi-label">Net P&L</div>
          <div className={netPnl >= 0 ? "kpi-value green" : "kpi-value red"}>₹{netPnl.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</div></div>
        <div className="kpi-card"><div className="kpi-label">Charges / Gross</div>
          <div className="kpi-value">{chargesPct}%</div></div>
      </div>

      <section className="section chart-section">
        <h2>Charges by Strategy</h2>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={rows} layout="vertical">
            <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
            <XAxis type="number" tick={{ fontSize: 11, fill: "#718096" }} tickFormatter={(v: number) => "₹" + v.toLocaleString("en-IN", { maximumFractionDigits: 0 })} />
            <YAxis type="category" dataKey="strategy" tick={{ fontSize: 11, fill: "#718096" }} width={110} />
            <Tooltip formatter={(v: any) => ["₹" + Number(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })]} contentStyle={{ background: "#1a202c", border: "none" }} />
            <Bar dataKey="total" fill="#ecc94b" name="Charges" />
          </BarChart>
        </ResponsiveContainer>
      </section>

      <section className="section">
        <h2>Per-Strategy Breakdown</h2>
        <table className="trade-table">
          <thead><tr><th>Strategy</th><th>Trades</th><th>Gross P&L</th><th>Charges</th><th>Net P&L</th></tr></thead>
          <tbody>{rows.map((r: any, i: number) => {
            const net = (r.gross_pnl ?? 0) - (r.total ?? 0)
            return <tr key={i} className={net > 0 ? "win" : "loss"}>
              <td>{r.strategy}</td><td>{r.trades}</td>
              <td>₹{(r.gross_pnl ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td>
              <td className="yellow">₹{(r.total ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td>
              <td className={net > 0 ? "green" : "red"}>₹{net.toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td>
            </tr>
          })}</tbody>
        </table>
      </section>
    </div>
  )
}
