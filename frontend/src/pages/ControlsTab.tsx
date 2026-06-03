import { useState } from 'react'

export function ControlsTab({ snap }: { snap: any }) {
  const [msg, setMsg] = useState('')
  const equity = snap?.equity ?? 0
  const startEquity = snap?.starting_equity_today ?? equity
  const peak = snap?.peak_equity ?? equity
  const dayLossPct = startEquity ? ((equity - startEquity) / startEquity * 100) : 0
  const drawdownPct = peak ? ((peak - equity) / peak * 100) : 0

  async function halt() {
    try {
      const r = await fetch('http://localhost:8502/api/command/halt', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason: 'Manual halt via React dashboard' }) })
      const d = await r.json()
      setMsg(d.queued ? '✅ Halt queued — agent will stop within 1 second' : '❌ Failed')
    } catch { setMsg('❌ API not reachable') }
    setTimeout(() => setMsg(''), 5000)
  }

  async function resume() {
    try {
      const r = await fetch('http://localhost:8502/api/command/resume', { method: 'POST' })
      const d = await r.json()
      setMsg(d.queued ? '✅ Resume queued' : '❌ Failed')
    } catch { setMsg('❌ API not reachable') }
    setTimeout(() => setMsg(''), 5000)
  }

  return (
    <div className="tab-content">
      {msg && <div className={`status-banner ${msg.startsWith('✅') ? 'green' : 'red'}`}>{msg}</div>}

      <section className="section">
        <h2>Agent Control</h2>
        <div className="control-row">
          <div className={`status-pill ${snap?.halted ? 'red' : 'green'}`}>
            {snap?.halted ? `⛔ HALTED: ${snap?.halt_reason}` : '✅ Running'}
          </div>
          <button className="btn-danger" onClick={halt}>⛔ Halt Agent</button>
          {snap?.halted && <button className="btn-success" onClick={resume}>▶ Resume</button>}
        </div>
      </section>

      <section className="section">
        <h2>Circuit Breakers</h2>
        <div className="circuit-row">
          <div className="circuit-item">
            <div className="circuit-label">Daily Loss ({dayLossPct.toFixed(2)}% / -3%)</div>
            <div className="progress-bar">
              <div className="progress-fill red" style={{ width: `${Math.min(100, Math.abs(dayLossPct) / 3 * 100)}%` }} />
            </div>
          </div>
          <div className="circuit-item">
            <div className="circuit-label">Drawdown ({drawdownPct.toFixed(2)}% / 10%)</div>
            <div className="progress-bar">
              <div className="progress-fill orange" style={{ width: `${Math.min(100, drawdownPct / 10 * 100)}%` }} />
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <h2>Risk Parameters</h2>
        <div className="info-box">
          <table className="trade-table">
            <tbody>
              <tr><td>Per-trade risk</td><td>1.0%</td></tr>
              <tr><td>Max positions</td><td>5</td></tr>
              <tr><td>Daily loss circuit</td><td>-3%</td></tr>
              <tr><td>Drawdown circuit</td><td>-10%</td></tr>
            </tbody>
          </table>
          <p className="small">Edit config/default.yaml → hot-reloads within 60s</p>
        </div>
      </section>

      <section className="section">
        <h2>Quick Links</h2>
        <div className="link-grid">
          <a href="http://localhost:8501" target="_blank" className="link-card">📊 Streamlit Dashboard</a>
          <a href="http://localhost:8502/docs" target="_blank" className="link-card">🔧 API Docs (Swagger)</a>
        </div>
      </section>
    </div>
  )
}
