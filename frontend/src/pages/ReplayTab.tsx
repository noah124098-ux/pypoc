import { useState } from "react"
import { useApi } from "../hooks/useSnapshot"

export function ReplayTab() {
  const { data: trades } = useApi("/api/trades/list?limit=50", 60000)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const { data: tradeDetail } = useApi(selectedId ? "/api/trade/" + selectedId : "", 0)

  const tradeList = trades ?? []

  return (
    <div className="tab-content">
      <h1 className="tab-title">🎬 Trade Replay</h1>

      <div className="replay-layout">
        {/* Left: trade selector */}
        <div className="replay-list">
          <h2>Select Trade</h2>
          {tradeList.length > 0 ? tradeList.map((t: any) => (
            <button key={t.id} onClick={() => setSelectedId(t.id)}
              className={"replay-item " + (selectedId === t.id ? "active" : "") + " " + (t.pnl > 0 ? "win" : "loss")}>
              <div className="replay-symbol">{t.symbol}</div>
              <div className="replay-meta">{t.strategy} · {t.exit_reason}</div>
              <div className={"replay-pnl " + (t.pnl >= 0 ? "green" : "red")}>
                ₹{(t.pnl ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
              </div>
            </button>
          )) : <div className="empty-state">No trades yet — start the paper agent</div>}
        </div>

        {/* Right: trade detail */}
        <div className="replay-detail">
          {tradeDetail ? (
            <div className="replay-card">
              <div className="replay-header">
                <span className="replay-title">{tradeDetail.symbol}</span>
                <span className="badge">{tradeDetail.strategy}</span>
                <span className={tradeDetail.pnl > 0 ? "pnl green" : "pnl red"}>
                  ₹{(tradeDetail.pnl ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                </span>
              </div>
              <table className="trade-table">
                <tbody>
                  <tr><td>Entry Price</td><td>₹{(tradeDetail.entry_price ?? 0).toFixed(2)}</td></tr>
                  <tr><td>Exit Price</td><td>₹{(tradeDetail.exit_price ?? 0).toFixed(2)}</td></tr>
                  <tr><td>Quantity</td><td>{tradeDetail.qty}</td></tr>
                  <tr><td>Gross P&L</td><td>₹{(tradeDetail.pnl ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td></tr>
                  <tr><td>Charges</td><td>₹{(tradeDetail.charges ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}</td></tr>
                  <tr><td>Exit Reason</td><td><span className="badge">{tradeDetail.exit_reason}</span></td></tr>
                  <tr><td>Opened At</td><td className="small">{tradeDetail.opened_at}</td></tr>
                  <tr><td>Closed At</td><td className="small">{tradeDetail.closed_at}</td></tr>
                  {tradeDetail.rationale && <tr><td>Signal Rationale</td><td className="small">{tradeDetail.rationale}</td></tr>}
                  {tradeDetail.confidence && <tr><td>Confidence</td><td>{(tradeDetail.confidence * 100).toFixed(0)}%</td></tr>}
                </tbody>
              </table>
            </div>
          ) : <div className="empty-state">Select a trade to see details</div>}
        </div>
      </div>
    </div>
  )
}
