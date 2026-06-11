import { useState, useMemo, type ReactElement } from "react"
import { useApi } from "../hooks/useSnapshot"

type SortMode = "date" | "pnl" | "symbol"

/** Wrap text with <mark> spans for matching segments */
function highlight(text: string, query: string): ReactElement {
  if (!query) return <>{text}</>
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  const regex = new RegExp(`(${escaped})`, "gi")
  const parts = text.split(regex)
  return (
    <>
      {parts.map((part, i) =>
        regex.test(part) ? (
          <mark key={i} className="search-highlight">{part}</mark>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  )
}

export function ReplayTab() {
  const { data: tradesRaw, loading } = useApi("/api/trades/list?limit=200", 60000)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const { data: tradeDetailRaw } = useApi(selectedId ? "/api/trade/" + selectedId : "", 0)
  const tradeDetail = tradeDetailRaw as any

  const [searchText, setSearchText] = useState("")
  const [sortMode, setSortMode] = useState<SortMode>("date")

  const tradeList: any[] = Array.isArray(tradesRaw) ? tradesRaw : []
  const q = searchText.trim()

  const filteredAndSorted = useMemo(() => {
    const ql = q.toLowerCase()
    const filtered = tradeList.filter(t => {
      if (!ql) return true
      return (
        (t.symbol ?? "").toLowerCase().includes(ql) ||
        (t.strategy ?? "").toLowerCase().includes(ql) ||
        (t.opened_at ?? "").toLowerCase().includes(ql) ||
        (t.closed_at ?? "").toLowerCase().includes(ql)
      )
    })
    const sorted = [...filtered]
    if (sortMode === "pnl") {
      sorted.sort((a, b) => (b.pnl ?? 0) - (a.pnl ?? 0))
    } else if (sortMode === "symbol") {
      sorted.sort((a, b) => (a.symbol ?? "").localeCompare(b.symbol ?? ""))
    } else {
      // date: newest first (rely on API order, or sort by id desc)
      sorted.sort((a, b) => (b.id ?? 0) - (a.id ?? 0))
    }
    return sorted
  }, [tradeList, q, sortMode])

  const totalCount = tradeList.length
  const shownCount = filteredAndSorted.length

  return (
    <div className="tab-content">
      <h1 className="tab-title">Trade Replay</h1>

      {loading ? (
        <div className="empty-state">
          <div className="spinner" style={{ margin: "0 auto 12px" }} />
          Loading trades…
        </div>
      ) : tradeList.length === 0 ? (
        <div className="empty-card" style={{ maxWidth: 560, margin: "40px auto" }}>
          <div style={{ fontSize: 48, marginBottom: 12 }}>🎬</div>
          <h3>No trades to replay yet</h3>
          <p>Start the paper agent and let it complete some trades.</p>
          <div
            style={{
              background: "var(--bg3)",
              borderRadius: 8,
              padding: "12px 16px",
              fontSize: 13,
              color: "var(--text2)",
              lineHeight: 1.6,
              marginTop: 12,
              textAlign: "left",
            }}
          >
            <strong style={{ color: "var(--text)" }}>What you'll see here once trades appear:</strong>
            <ul style={{ margin: "8px 0 0 16px", padding: 0 }}>
              <li>Entry and exit price for every completed trade</li>
              <li>Signal rationale and strategy that generated the trade</li>
              <li>Gross P&amp;L, brokerage charges, and exit reason</li>
            </ul>
          </div>
        </div>
      ) : (
        <>
          {/* Search + sort toolbar */}
          <div className="signals-toolbar" style={{ marginBottom: 12 }}>
            <input
              className="search-input"
              type="text"
              placeholder="Filter by symbol, strategy, or date…"
              value={searchText}
              onChange={e => setSearchText(e.target.value)}
              aria-label="Search trades"
            />
            <div className="toggle-group" role="group" aria-label="Sort trades by">
              {(["date", "pnl", "symbol"] as SortMode[]).map(mode => (
                <button
                  key={mode}
                  className={"toggle-btn" + (sortMode === mode ? " active" : "")}
                  onClick={() => setSortMode(mode)}
                >
                  {mode === "date" ? "By date" : mode === "pnl" ? "By P&L" : "By symbol"}
                </button>
              ))}
            </div>
          </div>

          {/* Summary line */}
          <div className="replay-summary">
            {q
              ? `Showing ${shownCount}/${totalCount} trades matching '${q}'`
              : `${totalCount} trade${totalCount !== 1 ? "s" : ""} · sorted by ${sortMode === "date" ? "date (newest first)" : sortMode === "pnl" ? "P&L (largest first)" : "symbol"}`}
          </div>

          <div className="replay-layout">
            {/* Left: trade selector */}
            <div className="replay-list">
              <h2>Select Trade</h2>
              {filteredAndSorted.length === 0 ? (
                <div className="empty-state">No trades match '{q}'</div>
              ) : (
                filteredAndSorted.map((t: any) => (
                  <button
                    key={t.id}
                    onClick={() => setSelectedId(t.id)}
                    className={"replay-item " + (selectedId === t.id ? "active" : "") + " " + (t.pnl > 0 ? "win" : "loss")}
                  >
                    <div className="replay-symbol">{highlight(t.symbol ?? "", q)}</div>
                    <div className="replay-meta">
                      {highlight(t.strategy ?? "", q)} · {t.exit_reason}
                    </div>
                    <div className={"replay-pnl " + (t.pnl >= 0 ? "green" : "red")}>
                      ₹{(t.pnl ?? 0).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                    </div>
                  </button>
                ))
              )}
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
        </>
      )}
    </div>
  )
}
