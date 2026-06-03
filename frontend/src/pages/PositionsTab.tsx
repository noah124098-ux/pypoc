import { useApi } from '../hooks/useSnapshot'

export function PositionsTab({ snap }: { snap: any }) {
  const { data: signals } = useApi<any[]>('/api/signals?limit=20', 15000)
  const positions = snap?.open_positions ?? []

  return (
    <div className="tab-content">
      <section className="section">
        <h2>Open Positions ({positions.length}/5)</h2>
        {positions.length > 0 ? positions.map((p: any) => (
          <div key={p.symbol} className="position-card">
            <div className="pos-header">
              <strong>{p.symbol}</strong>
              <span className="badge">{p.strategy}</span>
              <span className={p.unrealized_pnl >= 0 ? 'pnl green' : 'pnl red'}>
                ₹{(p.unrealized_pnl ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </span>
            </div>
            <div className="pos-row">
              <span>Entry: ₹{(p.avg_price ?? 0).toFixed(1)}</span>
              <span>LTP: ₹{(p.last_price ?? 0).toFixed(1)}</span>
              <span>SL: ₹{(p.stop_loss ?? 0).toFixed(1)}</span>
              <span>Target: ₹{(p.target ?? 0).toFixed(1)}</span>
            </div>
          </div>
        )) : <div className="empty-state">No open positions</div>}
      </section>

      <section className="section">
        <h2>Recent Signals</h2>
        <table className="trade-table">
          <thead><tr><th>Symbol</th><th>Strategy</th><th>Side</th><th>Status</th><th>Reason</th></tr></thead>
          <tbody>{(signals ?? []).map((s: any, i: number) => (
            <tr key={i} className={s.accepted ? 'win' : 'loss'}>
              <td>{s.symbol}</td><td>{s.strategy}</td><td>{s.side}</td>
              <td><span className={s.accepted ? 'badge green' : 'badge red'}>{s.accepted ? 'Accepted' : 'Rejected'}</span></td>
              <td className="small">{s.rejection_reason ?? '—'}</td>
            </tr>
          ))}</tbody>
        </table>
      </section>
    </div>
  )
}
