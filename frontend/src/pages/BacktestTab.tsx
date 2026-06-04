import { useState, useCallback } from 'react'
import { useApi, apiGet, apiPost } from '../hooks/useSnapshot'

export function BacktestTab() {
  const { data: gate, loading } = useApi<any>('/api/gate', 60000)
  const [refreshing, setRefreshing] = useState(false)
  const [refreshResult, setRefreshResult] = useState<{ ok: boolean; msg: string } | null>(null)
  const [gateOverride, setGateOverride] = useState<any>(null)

  const activeGate = gateOverride ?? gate
  const passed = activeGate?.passed ?? false
  const metrics = activeGate?.metrics ?? {}
  const failures = activeGate?.failures ?? []

  const handleRefreshGate = useCallback(async () => {
    setRefreshing(true)
    setRefreshResult(null)
    try {
      const res = await apiPost('/api/gate/refresh')
      if (res.returncode === 0) {
        setRefreshResult({ ok: true, msg: 'Gate refreshed successfully.' })
        // Refetch gate data
        const updated = await apiGet('/api/gate')
        setGateOverride(updated)
      } else {
        setRefreshResult({ ok: false, msg: res.error || res.output || 'Walk-forward failed.' })
      }
    } catch (e: any) {
      setRefreshResult({ ok: false, msg: e.message || 'Request failed.' })
    } finally {
      setRefreshing(false)
    }
  }, [])

  return (
    <div className="tab-content">
      <h1 className="tab-title">Backtest Gate</h1>

      {loading && !activeGate ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '20px 0' }}>
          <div className="spinner" />
          <span style={{ color: 'var(--text2)', fontSize: 13 }}>Loading gate data…</span>
        </div>
      ) : (
        <>
          {/* Gate banner — always shown once data is loaded */}
          <div className={`status-banner ${passed ? 'green' : 'red'}`}>
            {passed
              ? '✅ Gate PASSED — live trading allowed'
              : `❌ Gate FAILED — ${failures.length > 0 ? failures.join(', ') : 'see metrics below'}`}
          </div>

          {/* Period dates */}
          {(activeGate?.period_start || activeGate?.period_end) && (
            <div className="gate-meta" style={{ marginBottom: 16 }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text)' }}>
                Period:&nbsp;
                <span style={{ color: 'var(--blue)' }}>
                  {activeGate.period_start?.split('T')[0] ?? '—'} → {activeGate.period_end?.split('T')[0] ?? '—'}
                </span>
              </span>
              <span>Trades: <strong>{metrics.n_trades ?? '—'}</strong></span>
              {activeGate.file_age_days != null && (
                <span style={{ color: activeGate.file_age_days > 30 ? 'var(--red)' : 'var(--text2)' }}>
                  File age: {activeGate.file_age_days}d
                </span>
              )}
            </div>
          )}

          {/* Gate checks table */}
          {activeGate && (activeGate.checks ?? []).length > 0 && (
            <section className="section">
              <h2>Gate Checks</h2>
              <table className="trade-table">
                <thead>
                  <tr>
                    <th>Metric</th>
                    <th>Value</th>
                    <th>Threshold</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(activeGate.checks ?? []).map((c: any, i: number) => (
                    <tr key={i} className={c.pass_ ? 'win' : 'loss'}>
                      <td>{c.name}</td>
                      <td>{typeof c.actual === 'number' ? c.actual.toFixed(3) : c.actual ?? '—'}</td>
                      <td>{c.direction === 'ge' ? '≥' : '≤'} {c.threshold}</td>
                      <td>
                        <span className={c.pass_ ? 'badge green' : 'badge red'}>
                          {c.pass_ ? 'PASS' : 'FAIL'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}

          {/* Detailed metrics */}
          {activeGate && Object.keys(metrics).length > 0 && (
            <section className="section">
              <h2>Detailed Metrics</h2>
              <div className="kpi-row">
                {metrics.win_rate_pct != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Win Rate</div>
                    <div className={`kpi-value ${metrics.win_rate_pct >= 45 ? 'green' : 'red'}`}>
                      {metrics.win_rate_pct.toFixed(1)}%
                    </div>
                    <div className="kpi-sub">threshold ≥ 45%</div>
                  </div>
                )}
                {metrics.profit_factor != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Profit Factor</div>
                    <div className={`kpi-value ${metrics.profit_factor >= 1.5 ? 'green' : 'red'}`}>
                      {metrics.profit_factor.toFixed(2)}
                    </div>
                    <div className="kpi-sub">threshold ≥ 1.5</div>
                  </div>
                )}
                {metrics.sharpe != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Sharpe</div>
                    <div className={`kpi-value ${metrics.sharpe >= 1.2 ? 'green' : 'red'}`}>
                      {metrics.sharpe.toFixed(2)}
                    </div>
                    <div className="kpi-sub">threshold ≥ 1.2</div>
                  </div>
                )}
                {metrics.sortino != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Sortino</div>
                    <div className="kpi-value">{metrics.sortino.toFixed(2)}</div>
                  </div>
                )}
                {metrics.cagr_pct != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">CAGR</div>
                    <div className={`kpi-value ${metrics.cagr_pct >= 0 ? 'green' : 'red'}`}>
                      {metrics.cagr_pct.toFixed(1)}%
                    </div>
                  </div>
                )}
                {metrics.max_drawdown_pct != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Max Drawdown</div>
                    <div className={`kpi-value ${metrics.max_drawdown_pct <= 15 ? 'green' : 'red'}`}>
                      {metrics.max_drawdown_pct.toFixed(1)}%
                    </div>
                    <div className="kpi-sub">threshold ≤ 15%</div>
                  </div>
                )}
                {metrics.total_pnl != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Total P&amp;L</div>
                    <div className={`kpi-value ${metrics.total_pnl >= 0 ? 'green' : 'red'}`}>
                      ₹{metrics.total_pnl.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                    </div>
                  </div>
                )}
                {metrics.avg_win != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Avg Win</div>
                    <div className="kpi-value green">
                      ₹{metrics.avg_win.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                    </div>
                  </div>
                )}
                {metrics.avg_loss != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Avg Loss</div>
                    <div className="kpi-value red">
                      ₹{metrics.avg_loss.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                    </div>
                  </div>
                )}
                {metrics.n_trades != null && (
                  <div className="kpi-card">
                    <div className="kpi-label">Total Trades</div>
                    <div className={`kpi-value ${metrics.n_trades >= 100 ? 'green' : 'red'}`}>
                      {metrics.n_trades}
                    </div>
                    <div className="kpi-sub">threshold ≥ 100</div>
                  </div>
                )}
              </div>
            </section>
          )}

          {/* No data state */}
          {!activeGate && !loading && (
            <div className="empty-state" style={{ marginBottom: 20 }}>
              No gate data available. Run a walk-forward first.
            </div>
          )}
        </>
      )}

      <section className="section">
        <h2>Run Walk-Forward</h2>
        <div className="info-box">
          <code>python cli.py walk-forward --years 3 --end-date 2026-05-29</code>
          <p>Gate refreshes automatically every Sunday via Windows Task Scheduler.</p>
          <p style={{ marginTop: 8 }}>
            Thresholds: Sharpe ≥ 1.2 · MaxDD ≤ 15% · Win Rate ≥ 45% · Profit Factor ≥ 1.5 · Trades ≥ 100 · 3+ year walk-forward · file ≤ 30 days old
          </p>
          <div style={{ marginTop: 12 }}>
            <button
              className="btn"
              onClick={handleRefreshGate}
              disabled={refreshing}
              style={{ minWidth: 160, display: 'inline-flex', alignItems: 'center', gap: 8 }}
            >
              {refreshing && <span className="spinner" style={{ width: 14, height: 14 }} />}
              {refreshing ? 'Running Gate...' : 'Refresh Gate'}
            </button>
            {refreshResult && (
              <span style={{ marginLeft: 12, fontSize: 13, color: refreshResult.ok ? 'var(--green)' : 'var(--red)' }}>
                {refreshResult.msg}
              </span>
            )}
          </div>
        </div>
      </section>
    </div>
  )
}
