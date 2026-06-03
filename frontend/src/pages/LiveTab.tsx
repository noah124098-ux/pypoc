import { useApi } from '../hooks/useSnapshot'

function KpiCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${color ?? ''}`}>{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

function PositionCard({ p }: { p: any }) {
  const pnl = p.unrealized_pnl ?? 0
  const progress = p.stop_loss && p.target ? Math.max(0, Math.min(100,
    ((p.last_price - p.stop_loss) / (p.target - p.stop_loss)) * 100
  )) : 50
  return (
    <div className="position-card">
      <div className="pos-header">
        <strong>{p.symbol}</strong>
        <span className="badge">{p.strategy}</span>
        <span className={pnl >= 0 ? 'pnl green' : 'pnl red'}>
          ₹{pnl.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
        </span>
      </div>
      <div className="pos-row">
        <span>Avg: ₹{(p.avg_price ?? 0).toFixed(1)}</span>
        <span>LTP: ₹{(p.last_price ?? 0).toFixed(1)}</span>
        <span>Qty: {p.qty}</span>
      </div>
      <div className="progress-bar">
        <div className="progress-fill" style={{ width: `${progress}%` }} />
        <span className="sl-label">SL</span>
        <span className="tgt-label">TGT</span>
      </div>
    </div>
  )
}

export function LiveTab({ snap, connected }: { snap: any; connected: boolean }) {
  const { data: trades } = useApi<any[]>('/api/trades?limit=5', 15000)
  const equity = snap?.equity ?? 0
  const dayPnl = equity - (snap?.starting_equity_today ?? equity)
  const dayPct = snap?.starting_equity_today ? (dayPnl / snap.starting_equity_today * 100) : 0
  const positions = snap?.open_positions ?? []
  const vix = snap?.vix ? snap.vix.toFixed(1) : '—'

  return (
    <div className="tab-content">
      <div className={`status-banner ${connected ? 'green' : 'red'}`}>
        {connected ? '🟢 WebSocket connected — live 1s updates' : '⚫ Disconnected — start: service_manager.bat start-agent'}
      </div>

      <div className="kpi-row">
        <KpiCard label="Equity" value={`₹${equity.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`} />
        <KpiCard label="Day P&L" value={`₹${Math.abs(dayPnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`}
          sub={`${dayPct >= 0 ? '+' : ''}${dayPct.toFixed(2)}%`} color={dayPnl >= 0 ? 'green' : 'red'} />
        <KpiCard label="Regime" value={snap?.current_regime ?? '—'} />
        <KpiCard label="VIX" value={vix} color={parseFloat(vix) >= 20 ? 'red' : parseFloat(vix) >= 18 ? 'yellow' : 'green'} />
        <KpiCard label="Positions" value={`${positions.length}/5`} />
        <KpiCard label="Status" value={snap?.halted ? '⛔ HALTED' : '✅ Running'} color={snap?.halted ? 'red' : 'green'} />
      </div>

      <section className="section">
        <h2>Open Positions ({positions.length})</h2>
        {positions.length > 0
          ? positions.map((p: any) => <PositionCard key={p.symbol} p={p} />)
          : <div className="empty-state">No open positions — agent will enter when signals fire</div>
        }
      </section>

      <section className="section">
        <h2>Recent Trades</h2>
        {trades && trades.length > 0 ? (
          <table className="trade-table">
            <thead><tr><th>Symbol</th><th>Strategy</th><th>Exit</th><th>P&L</th></tr></thead>
            <tbody>{trades.map((t: any, i: number) => (
              <tr key={i} className={t.pnl > 0 ? 'win' : 'loss'}>
                <td>{t.symbol}</td><td>{t.strategy}</td><td>{t.exit_reason}</td>
                <td>₹{(t.pnl ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : <div className="empty-state">No trades yet</div>}
      </section>
    </div>
  )
}
