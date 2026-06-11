import { useState } from 'react'
import { useApi, apiPost } from '../hooks/useSnapshot'
import { useToasts } from '../hooks/useNotifications'

interface PreflightCheck {
  name: string
  passed: boolean
  message: string
}

interface PreflightResult {
  checks: PreflightCheck[]
  all_passed: boolean
}

export function ControlsTab({ snap }: { snap: any }) {
  const { data: config } = useApi<any>('/api/config', 300000)
  const { data: sys } = useApi<any>('/api/system', 15000)
  const { addToast } = useToasts()
  const [msg, setMsg] = useState('')
  const [preflight, setPreflight] = useState<PreflightResult | null>(null)
  const [preflightLoading, setPreflightLoading] = useState(false)
  const [preflightError, setPreflightError] = useState('')

  const equity = snap?.equity ?? 0
  const startEquity = snap?.starting_equity_today ?? equity
  const peak = snap?.peak_equity ?? equity
  const dayLossPct = startEquity ? ((equity - startEquity) / startEquity * 100) : 0
  const drawdownPct = peak ? ((peak - equity) / peak * 100) : 0
  const isRunning = snap?.running ?? false
  const isHalted = snap?.halted ?? false

  async function sendCommand(endpoint: string, body?: any) {
    try {
      const d = await apiPost(endpoint, body)
      if (d.queued) {
        addToast('Command queued — agent will apply within 1 second', 'success')
        setMsg('')
      } else {
        addToast('Failed: ' + JSON.stringify(d), 'error')
        setMsg('❌ Failed: ' + JSON.stringify(d))
      }
    } catch {
      addToast('API not reachable — is the agent running?', 'error')
      setMsg('❌ API not reachable — is the agent running?')
    }
    setTimeout(() => setMsg(''), 6000)
  }

  async function runPreflight() {
    setPreflightLoading(true)
    setPreflightError('')
    setPreflight(null)
    try {
      const resp = await fetch('/api/preflight', {
        headers: {
          'Authorization': 'Basic ' + btoa('admin:pypoc2024'),
        },
      })
      if (!resp.ok) {
        setPreflightError(`Server returned ${resp.status}`)
        return
      }
      const data: PreflightResult = await resp.json()
      setPreflight(data)
    } catch (e) {
      setPreflightError('API not reachable — is the backend running?')
    } finally {
      setPreflightLoading(false)
    }
  }

  const risk = config?.risk ?? {}

  return (
    <div className="tab-content">
      {msg && <div className={`status-banner ${msg.startsWith('✅') ? 'green' : 'red'}`}>{msg}</div>}

      {!isRunning && (
        <div className="status-banner" style={{ background: 'rgba(160,160,160,.1)', border: '1px solid #4a5568', color: '#a0aec0' }}>
          ⚫ Agent not running — start it to enable halt/resume controls
          <div style={{ marginTop: 8, fontSize: 12 }}>
            <code>scripts\service_manager.bat start-agent</code>
          </div>
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 20 }}>

        <section className="section" style={{ marginBottom: 0 }}>
          <h2>Agent Control</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className={`status-pill ${isHalted ? 'red' : isRunning ? 'green' : 'status-pill'}`}
              style={!isRunning && !isHalted ? { background: 'var(--bg3)', color: 'var(--text2)' } : {}}>
              {isHalted ? `⛔ HALTED: ${snap?.halt_reason ?? 'unknown'}` : isRunning ? '✅ Running' : '⚫ Stopped'}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="btn-danger" onClick={() => sendCommand('/api/command/halt', { reason: 'Manual halt via React dashboard' })}>
                ⛔ Halt Agent
              </button>
              {isHalted && (
                <button className="btn-success" onClick={() => sendCommand('/api/command/resume')}>
                  ▶ Resume
                </button>
              )}
            </div>
          </div>
        </section>

        <section className="section" style={{ marginBottom: 0 }}>
          <h2>Quick Links</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <a href="/api/docs" target="_blank" className="link-card">🔧 API Swagger Docs</a>
            <a href="https://github.com/noah124098-ux/pypoc" target="_blank" className="link-card">📂 GitHub Repo</a>
          </div>
        </section>
      </div>

      <section className="section">
        <h2>Preflight Check</h2>
        <p className="small" style={{ marginBottom: 10, color: 'var(--text2)' }}>
          Validates credentials, gate, config, tests, directories, market hours, and snapshot freshness before starting the agent.
        </p>
        <button
          className="btn-success"
          onClick={runPreflight}
          disabled={preflightLoading}
          style={{ marginBottom: 12 }}
        >
          {preflightLoading ? '⏳ Running...' : '▶ Run Preflight'}
        </button>

        {preflightError && (
          <div className="status-banner red" style={{ marginBottom: 8 }}>{preflightError}</div>
        )}

        {preflight && (
          <>
            <div className={`status-banner ${preflight.all_passed ? 'green' : 'red'}`} style={{ marginBottom: 10 }}>
              {preflight.all_passed
                ? '✅ All checks passed — safe to start agent'
                : `❌ ${preflight.checks.filter(c => !c.passed).length} check(s) failed — fix before starting`}
            </div>
            <table className="trade-table">
              <thead>
                <tr>
                  <th style={{ width: 36 }}></th>
                  <th>Check</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {preflight.checks.map((c, i) => (
                  <tr key={i}>
                    <td style={{ textAlign: 'center', fontSize: 16 }}>
                      {c.passed ? '✅' : '❌'}
                    </td>
                    <td>{c.name}</td>
                    <td className="small" style={{ color: c.passed ? 'var(--text2)' : '#fc8181' }}>
                      {c.message || (c.passed ? 'OK' : '')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section className="section">
        <h2>Circuit Breakers</h2>
        <div className="circuit-row">
          <div className="circuit-item">
            <div className="circuit-label">
              Daily Loss: {dayLossPct.toFixed(2)}% / circuit at -{risk.daily_loss_circuit_pct ?? 3}%
            </div>
            <div className="progress-bar">
              <div className="progress-fill red" style={{ width: `${Math.min(100, Math.abs(dayLossPct) / (risk.daily_loss_circuit_pct ?? 3) * 100)}%` }} />
            </div>
          </div>
          <div className="circuit-item">
            <div className="circuit-label">
              Drawdown: {drawdownPct.toFixed(2)}% / circuit at {risk.drawdown_circuit_pct ?? 10}%
            </div>
            <div className="progress-bar">
              <div className="progress-fill orange" style={{ width: `${Math.min(100, drawdownPct / (risk.drawdown_circuit_pct ?? 10) * 100)}%` }} />
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <h2>Active Risk Parameters</h2>
        <table className="trade-table">
          <thead><tr><th>Parameter</th><th>Value</th><th>Description</th></tr></thead>
          <tbody>
            <tr><td>Per-trade risk</td><td><strong>{risk.per_trade_risk_pct ?? 1.0}%</strong></td><td>Max % of equity at risk per trade</td></tr>
            <tr><td>Max positions</td><td><strong>{risk.max_open_positions ?? 5}</strong></td><td>Max concurrent open positions</td></tr>
            <tr><td>Daily loss circuit</td><td><strong>-{risk.daily_loss_circuit_pct ?? 3}%</strong></td><td>Halt if day P&L drops below this</td></tr>
            <tr><td>Drawdown circuit</td><td><strong>-{risk.drawdown_circuit_pct ?? 10}%</strong></td><td>Halt if peak-to-trough exceeds this</td></tr>
            <tr><td>Spread filter</td><td><strong>{risk.max_spread_pct ?? 0.3}%</strong></td><td>Reject if bid-ask spread exceeds this</td></tr>
          </tbody>
        </table>
        <p className="small" style={{ marginTop: 8 }}>Edit <code>config/default.yaml</code> → hot-reloads within 60s (no restart needed)</p>
      </section>

      {config?.strategies_enabled && (
        <section className="section">
          <h2>Active Strategies ({config.strategies_enabled.length})</h2>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {config.strategies_enabled.map((s: string) => (
              <span key={s} className="badge green" style={{ fontSize: 12, padding: '4px 10px' }}>{s}</span>
            ))}
          </div>
        </section>
      )}

      <section className="section">
        <h2>System Resources</h2>
        {!sys ? (
          <p className="small" style={{ color: 'var(--text2)' }}>Loading system metrics...</p>
        ) : (
          <>
            {sys.disk_free_gb < 0.5 && (
              <div className="status-banner red" style={{ marginBottom: 12 }}>
                Disk critically low: {sys.disk_free_gb} GB free — free space immediately
              </div>
            )}
            {sys.memory_pct > 85 && (
              <div className="status-banner" style={{ marginBottom: 12, background: 'rgba(237,137,54,.15)', border: '1px solid #ed8935', color: '#ed8935' }}>
                Memory usage high: {sys.memory_pct}% — consider restarting background processes
              </div>
            )}

            <div className="circuit-row">
              <div className="circuit-item">
                <div className="circuit-label">
                  CPU: {sys.cpu_pct}%
                </div>
                <div className="progress-bar">
                  <div
                    className={`progress-fill ${sys.cpu_pct > 80 ? 'red' : sys.cpu_pct > 50 ? 'orange' : 'green'}`}
                    style={{ width: `${Math.min(100, sys.cpu_pct)}%` }}
                  />
                </div>
              </div>

              <div className="circuit-item">
                <div className="circuit-label">
                  Memory: {sys.memory_pct}% ({sys.memory_used_gb} / {sys.memory_total_gb} GB)
                </div>
                <div className="progress-bar">
                  <div
                    className={`progress-fill ${sys.memory_pct > 85 ? 'red' : sys.memory_pct > 70 ? 'orange' : 'green'}`}
                    style={{ width: `${Math.min(100, sys.memory_pct)}%` }}
                  />
                </div>
              </div>

              <div className="circuit-item">
                <div className="circuit-label">
                  Disk: {sys.disk_pct}% used ({sys.disk_free_gb} GB free)
                </div>
                <div className="progress-bar">
                  <div
                    className={`progress-fill ${sys.disk_free_gb < 0.5 ? 'red' : sys.disk_pct > 85 ? 'orange' : 'green'}`}
                    style={{ width: `${Math.min(100, sys.disk_pct)}%` }}
                  />
                </div>
              </div>
            </div>

            <table className="trade-table" style={{ marginTop: 12 }}>
              <tbody>
                <tr><td>Uptime</td><td><strong>{sys.uptime_hours}h</strong></td></tr>
                <tr><td>Python processes</td><td><strong>{sys.python_processes}</strong></td></tr>
              </tbody>
            </table>
            <p className="small" style={{ marginTop: 8 }}>Refreshes every 15 seconds</p>
          </>
        )}
      </section>
    </div>
  )
}
