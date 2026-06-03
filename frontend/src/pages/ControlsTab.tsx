import { useState } from 'react'
import { useApi } from '../hooks/useSnapshot'

export function ControlsTab({ snap }: { snap: any }) {
  const { data: config } = useApi<any>('/api/config', 300000)
  const [msg, setMsg] = useState('')

  const equity = snap?.equity ?? 0
  const startEquity = snap?.starting_equity_today ?? equity
  const peak = snap?.peak_equity ?? equity
  const dayLossPct = startEquity ? ((equity - startEquity) / startEquity * 100) : 0
  const drawdownPct = peak ? ((peak - equity) / peak * 100) : 0
  const isRunning = snap?.running ?? false
  const isHalted = snap?.halted ?? false

  async function sendCommand(endpoint: string, body?: any) {
    try {
      const r = await fetch('http://localhost:8502' + endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body ? JSON.stringify(body) : undefined,
      })
      const d = await r.json()
      setMsg(d.queued ? '✅ Command queued — agent will apply within 1 second' : '❌ Failed: ' + JSON.stringify(d))
    } catch {
      setMsg('❌ API not reachable — is the agent running?')
    }
    setTimeout(() => setMsg(''), 6000)
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
    </div>
  )
}
