import { useApi } from '../hooks/useSnapshot'

const REGIME_COLORS: Record<string, string> = { TREND: '#4299e1', RANGE: '#9f7aea', VOLATILE: '#ed8936', UNKNOWN: '#718096' }

export function RegimeTab() {
  const { data: history } = useApi<any[]>('/api/regime?limit=50', 60000)
  const records = history ?? []
  const dist: Record<string, number> = {}
  records.forEach((r: any) => { dist[r.regime] = (dist[r.regime] ?? 0) + 1 })
  const total = records.length || 1

  return (
    <div className="tab-content">
      <section className="section">
        <h2>Regime Distribution</h2>
        <div className="regime-bars">
          {Object.entries(dist).map(([r, c]) => (
            <div key={r} className="regime-bar-row">
              <span className="regime-label" style={{ color: REGIME_COLORS[r] ?? '#718096' }}>{r}</span>
              <div className="regime-bar-bg">
                <div className="regime-bar-fill" style={{ width: `${(c/total)*100}%`, background: REGIME_COLORS[r] ?? '#718096' }} />
              </div>
              <span className="regime-pct">{((c/total)*100).toFixed(0)}%</span>
            </div>
          ))}
        </div>
      </section>

      <section className="section">
        <h2>Recent Regime History</h2>
        <table className="trade-table">
          <thead><tr><th>Time</th><th>Regime</th><th>Rationale</th></tr></thead>
          <tbody>{records.slice(0,20).map((r: any, i: number) => (
            <tr key={i}>
              <td className="small">{r.ts ? new Date(r.ts).toLocaleString('en-IN') : '—'}</td>
              <td><span className="badge" style={{ background: REGIME_COLORS[r.regime] ?? '#718096', color: '#fff' }}>{r.regime}</span></td>
              <td className="small">{r.rationale ?? '—'}</td>
            </tr>
          ))}</tbody>
        </table>
      </section>
    </div>
  )
}
