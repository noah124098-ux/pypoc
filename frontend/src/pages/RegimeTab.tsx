import { useApi } from '../hooks/useSnapshot'

const REGIME_COLORS: Record<string, string> = { TREND: '#4299e1', RANGE: '#9f7aea', VOLATILE: '#ed8936', UNKNOWN: '#718096' }

export function RegimeTab({ snap }: { snap: any }) {
  const { data: history } = useApi<any[]>('/api/regime?limit=50', 60000)
  const { data: config } = useApi('/api/config', 300000)
  const records = history ?? []
  const dist: Record<string, number> = {}
  records.forEach((r: any) => { dist[r.regime] = (dist[r.regime] ?? 0) + 1 })
  const total = records.length || 1

  const currentRegime = snap?.current_regime ?? null

  return (
    <div className="tab-content">
      {/* Current Regime KPI */}
      <section className="section">
        <h2>Current Regime</h2>
        <div className="kpi-row">
          <div className="kpi-card">
            <div className="kpi-label">Current Regime</div>
            <div
              className="kpi-value"
              style={{ color: REGIME_COLORS[currentRegime ?? 'UNKNOWN'] ?? '#718096' }}
            >
              {currentRegime ?? 'Unknown'}
            </div>
          </div>
        </div>
      </section>

      {/* Regime Classification Rules */}
      <section className="section">
        <h2>Regime Classification Rules</h2>
        <table className="trade-table">
          <thead>
            <tr><th>Regime</th><th>Condition</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><span className="badge" style={{ background: REGIME_COLORS.TREND, color: '#fff' }}>TREND</span></td>
              <td>ADX &gt; {(config as any)?.regime?.adx_trend_threshold ?? 20}</td>
              <td>Strong directional movement — trend-following strategies active</td>
            </tr>
            <tr>
              <td><span className="badge" style={{ background: REGIME_COLORS.RANGE, color: '#fff' }}>RANGE</span></td>
              <td>BB width &lt; {(config as any)?.regime?.bb_width_range_threshold ?? 0.06}</td>
              <td>Low volatility, mean-reversion conditions — range strategies active</td>
            </tr>
            <tr>
              <td><span className="badge" style={{ background: REGIME_COLORS.VOLATILE, color: '#fff' }}>VOLATILE</span></td>
              <td>VIX &gt; {(config as any)?.regime?.vix_volatile_threshold ?? 20}</td>
              <td>High fear/uncertainty — reduced position sizes, tighter stops</td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* Regime Distribution */}
      {records.length > 0 && (
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
      )}

      {/* Recent Regime History */}
      <section className="section">
        <h2>Recent Regime History</h2>
        {records.length === 0 ? (
          <p className="empty-msg">Regime log will populate once the agent starts trading.</p>
        ) : (
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
        )}
      </section>
    </div>
  )
}
