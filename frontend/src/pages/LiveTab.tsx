import { useApi } from '../hooks/useSnapshot'
import { useNavigate } from 'react-router-dom'

function KpiCard({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${color ?? ''}`}>{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

function KpiSkeleton() {
  return (
    <div className="kpi-row kpi-row-skeleton">
      {[...Array(6)].map((_, i) => (
        <div key={i} className="kpi-card kpi-card-skeleton">
          <div className="skeleton-line skeleton-label" />
          <div className="skeleton-line skeleton-value" />
        </div>
      ))}
    </div>
  )
}

function formatSignalTime(ts: any): string {
  if (!ts) return ''
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function ActivityCard({ signal }: { signal: any }) {
  const accepted = signal.accepted ?? false
  return (
    <div className={`activity-card ${accepted ? 'activity-accepted' : 'activity-rejected'}`}>
      <div className="activity-card-header">
        <span className="activity-time">{formatSignalTime(signal.ts ?? signal.timestamp)}</span>
        <span className={accepted ? 'badge green' : 'badge red'}>
          {accepted ? 'Accepted' : 'Rejected'}
        </span>
      </div>
      <div className="activity-card-body">
        <strong>{signal.symbol}</strong>
        <span className="badge">{signal.strategy}</span>
        <span className="activity-side">{signal.side}</span>
      </div>
      {!accepted && signal.rejection_reason && (
        <div className="activity-reason">{signal.rejection_reason}</div>
      )}
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
  const navigate = useNavigate()
  const { data: tradesRaw } = useApi<any>('/api/trades?limit=5', 15000)
  const trades: any[] | null = tradesRaw
    ? (Array.isArray(tradesRaw) ? tradesRaw : tradesRaw.data ?? null)
    : null
  const { data: signalsRaw } = useApi<any>('/api/signals?limit=10', 15000)
  const signals: any[] | null = signalsRaw
    ? (Array.isArray(signalsRaw) ? signalsRaw : signalsRaw.data ?? null)
    : null

  // Handle various snap field names
  const equity = snap?.equity ?? 0
  const startEquity = snap?.starting_equity_today ?? snap?.start_equity ?? equity
  const dayPnl = equity - startEquity
  const dayPct = startEquity ? (dayPnl / startEquity * 100) : 0
  const positions = snap?.open_positions ?? snap?.positions ?? []
  const vixVal = snap?.vix ?? snap?.VIX ?? 0
  const vix = vixVal ? (typeof vixVal === 'number' ? vixVal.toFixed(1) : String(vixVal)) : '—'
  const regime = snap?.current_regime ?? snap?.regime ?? '—'
  const halted = snap?.halted ?? false

  return (
    <div className="tab-content">
      <div className={`status-banner ${connected ? 'green' : 'red'}`}>
        {connected ? '🟢 WebSocket connected — live 1s updates' : '⚫ Disconnected — start: service_manager.bat start-agent'}
      </div>

      {snap === null ? <KpiSkeleton /> : (
        <div className="kpi-row">
          <KpiCard label="Equity" value={`₹${(equity ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`} />
          <KpiCard label="Day P&L" value={`₹${Math.abs(dayPnl ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}`}
            sub={`${(dayPct ?? 0) >= 0 ? '+' : ''}${(dayPct ?? 0).toFixed(2)}%`} color={(dayPnl ?? 0) >= 0 ? 'green' : 'red'} />
          <KpiCard label="Regime" value={regime} />
          <KpiCard label="VIX" value={vix} color={vix !== '—' && parseFloat(vix) >= 20 ? 'red' : vix !== '—' && parseFloat(vix) >= 18 ? 'yellow' : 'green'} />
          <KpiCard label="Positions" value={`${(positions?.length ?? 0)}/5`} />
          <KpiCard label="Status" value={halted ? '⛔ HALTED' : '✅ Running'} color={halted ? 'red' : 'green'} />
        </div>
      )}

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

      <section className="section">
        <div className="section-header-row">
          <h2>Recent Activity</h2>
          <button className="view-all-link" onClick={() => navigate('/positions')}>
            View all &rarr;
          </button>
        </div>
        {signals && signals.length > 0 ? (
          <div className="activity-feed">
            {signals.map((s: any, i: number) => (
              <ActivityCard key={i} signal={s} />
            ))}
          </div>
        ) : (
          <div className="empty-state">No recent signals — activity will appear here as strategies fire</div>
        )}
      </section>
    </div>
  )
}
