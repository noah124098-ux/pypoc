import { useApi } from '../hooks/useSnapshot'

export function BacktestTab() {
  const { data: gate } = useApi<any>('/api/gate', 60000)
  const passed = gate?.passed ?? false
  const metrics = gate?.metrics ?? {}
  const failures = gate?.failures ?? []

  return (
    <div className="tab-content">
      <div className={`status-banner ${passed ? 'green' : 'red'}`}>
        {passed ? '✅ Gate PASSED — live trading allowed' : `❌ Gate FAILED — ${failures.join(', ')}`}
      </div>

      {gate && (
        <section className="section">
          <h2>Gate Metrics</h2>
          <table className="trade-table">
            <thead><tr><th>Metric</th><th>Value</th><th>Threshold</th><th>Status</th></tr></thead>
            <tbody>
              {(gate.checks ?? []).map((c: any, i: number) => (
                <tr key={i} className={c.pass_ ? 'win' : 'loss'}>
                  <td>{c.name}</td>
                  <td>{typeof c.actual === 'number' ? c.actual.toFixed(2) : c.actual}</td>
                  <td>{c.direction === 'ge' ? '≥' : '≤'} {c.threshold}</td>
                  <td><span className={c.pass_ ? 'badge green' : 'badge red'}>{c.pass_ ? 'PASS' : 'FAIL'}</span></td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="gate-meta">
            <span>Period: {gate.period_start?.split('T')[0]} → {gate.period_end?.split('T')[0]}</span>
            <span>Trades: {metrics.n_trades}</span>
            <span>Sharpe: {metrics.sharpe?.toFixed(2)}</span>
            <span>CAGR: {metrics.cagr_pct?.toFixed(1)}%</span>
          </div>
        </section>
      )}

      <section className="section">
        <h2>Run Walk-Forward</h2>
        <div className="info-box">
          <code>python cli.py walk-forward --years 3 --end-date 2026-05-29</code>
          <p>Gate refreshes automatically every Sunday via Windows Task Scheduler.</p>
        </div>
      </section>
    </div>
  )
}
