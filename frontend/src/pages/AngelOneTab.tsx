import { useState } from 'react'
import { useApi, apiPost, apiGet } from '../hooks/useSnapshot'

// Persists credentials in sessionStorage (memory-only, never hits disk or network until "Connect")
const SESSION_KEY = 'ao_creds'

function loadCreds() {
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEY) || 'null') } catch { return null }
}
function saveCreds(c: any) { sessionStorage.setItem(SESSION_KEY, JSON.stringify(c)) }
function clearCreds() { sessionStorage.removeItem(SESSION_KEY) }

export function AngelOneTab() {
  const stored = loadCreds()
  const [apiKey, setApiKey] = useState(stored?.apiKey ?? '')
  const [clientCode, setClientCode] = useState(stored?.clientCode ?? '')
  const [password, setPassword] = useState(stored?.password ?? '')
  const [totpSecret, setTotpSecret] = useState(stored?.totpSecret ?? '')
  const [connected, setConnected] = useState(!!stored?.connected)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // Save to .env state
  const [saving, setSaving] = useState(false)
  const [saveResult, setSaveResult] = useState<{ ok: boolean; message: string } | null>(null)

  // Test connection state
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)

  const { data: portfolio, loading: portLoading } = useApi<any>(
    connected ? '/api/portfolio/angel-one' : '',
    30000
  )

  async function handleConnect() {
    if (!apiKey || !clientCode || !password || !totpSecret) {
      setError('All fields are required')
      return
    }
    setLoading(true)
    setError('')
    try {
      // Store creds in session state — they are sent to the backend only for portfolio fetch
      saveCreds({ apiKey, clientCode, password, totpSecret, connected: true })
      setConnected(true)
    } finally {
      setLoading(false)
    }
  }

  function handleDisconnect() {
    clearCreds()
    setConnected(false)
    setApiKey('')
    setClientCode('')
    setPassword('')
    setTotpSecret('')
    setSaveResult(null)
    setTestResult(null)
  }

  async function handleSaveToEnv() {
    if (!apiKey || !clientCode || !password || !totpSecret) {
      setSaveResult({ ok: false, message: 'All fields are required' })
      return
    }
    setSaving(true)
    setSaveResult(null)
    try {
      const res = await apiPost('/api/credentials/save-angel-one', {
        api_key: apiKey,
        client_code: clientCode,
        password: password,
        totp_secret: totpSecret,
      })
      setSaveResult({ ok: true, message: res.message || 'Credentials saved successfully.' })
    } catch (err: any) {
      setSaveResult({ ok: false, message: err?.message || 'Failed to save credentials' })
    } finally {
      setSaving(false)
    }
  }

  async function handleTestConnection() {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await apiGet('/api/portfolio/angel-one')
      if (res?.connected === true) {
        setTestResult({ ok: true, message: 'Connection successful — Angel One API is reachable.' })
      } else {
        // Detect "credentials not configured in backend" case
        const msg: string = res?.message ?? 'Connection failed — check credentials.'
        const isNotConfigured = /ANGEL_ONE_API_KEY|not configured|not set/i.test(msg)
        const displayMsg = isNotConfigured
          ? 'Credentials must be saved to .env first, then restart the API. Use "Save to .env" above, then run: python api/run.py'
          : msg
        setTestResult({ ok: false, message: displayMsg })
      }
    } catch (err: any) {
      setTestResult({ ok: false, message: err?.message || 'Connection test failed' })
    } finally {
      setTesting(false)
    }
  }

  const p = portfolio as any
  const livePositions: any[] = p?.positions ?? []

  return (
    <div className="tab-content">
      <h1 className="tab-title">Angel One Connect</h1>

      {/* Security note */}
      <div className="status-banner" style={{
        background: 'rgba(66,153,225,.1)', border: '1px solid rgba(66,153,225,.3)', color: '#4299e1', marginBottom: 20
      }}>
        <strong>DATA-ONLY mode.</strong> These credentials are used exclusively to fetch your live portfolio (read-only).
        No orders will be placed. Credentials are stored in browser session memory only — never sent to any third-party.
      </div>

      {!connected ? (
        /* -- Credential Form -- */
        <section className="section" style={{ maxWidth: 480 }}>
          <h2>Connect Angel One Account</h2>
          <p className="small" style={{ marginBottom: 16 }}>
            Enter your Angel One SmartAPI DATA-ONLY app credentials. Create a separate app at{' '}
            <a href="https://smartapi.angelbroking.com" target="_blank" rel="noopener noreferrer" style={{ color: 'var(--blue)' }}>
              smartapi.angelbroking.com
            </a>{' '}
            for read-only market data access.
          </p>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span className="small">API Key</span>
              <input
                type="password"
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="Your Angel One API Key"
                style={{ background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', color: 'var(--text)', fontSize: 13 }}
              />
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span className="small">Client Code</span>
              <input
                type="text"
                value={clientCode}
                onChange={e => setClientCode(e.target.value)}
                placeholder="e.g. A123456"
                style={{ background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', color: 'var(--text)', fontSize: 13 }}
              />
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span className="small">Password</span>
              <input
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Angel One login password"
                style={{ background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', color: 'var(--text)', fontSize: 13 }}
              />
            </label>

            <label style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span className="small">TOTP Secret (Base32)</span>
              <input
                type="password"
                value={totpSecret}
                onChange={e => setTotpSecret(e.target.value)}
                placeholder="e.g. JBSWY3DPEHPK3PXP"
                style={{ background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', color: 'var(--text)', fontSize: 13 }}
              />
              <span className="small" style={{ color: 'var(--text2)' }}>
                Found in your Angel One app settings under "Enable TOTP" → show secret
              </span>
            </label>

            {error && (
              <div className="status-banner red">{error}</div>
            )}

            <div style={{ display: 'flex', gap: 10, marginTop: 8, flexWrap: 'wrap' }}>
              <button
                onClick={handleConnect}
                disabled={loading}
                className="btn-success"
                style={{ padding: '10px 24px', fontSize: 14 }}
              >
                {loading ? 'Connecting...' : 'Connect'}
              </button>

              <button
                onClick={handleSaveToEnv}
                disabled={saving || !apiKey || !clientCode || !password || !totpSecret}
                style={{
                  padding: '10px 24px',
                  fontSize: 14,
                  background: 'rgba(66,153,225,.15)',
                  border: '1px solid rgba(66,153,225,.4)',
                  color: '#4299e1',
                  borderRadius: 6,
                  cursor: saving ? 'wait' : 'pointer',
                  opacity: (!apiKey || !clientCode || !password || !totpSecret) ? 0.5 : 1,
                }}
              >
                {saving ? 'Saving...' : 'Save to .env'}
              </button>

              <button
                onClick={handleTestConnection}
                disabled={testing}
                style={{
                  padding: '10px 24px',
                  fontSize: 14,
                  background: 'rgba(128,128,128,.1)',
                  border: '1px solid var(--border)',
                  color: 'var(--text)',
                  borderRadius: 6,
                  cursor: testing ? 'wait' : 'pointer',
                }}
              >
                {testing ? 'Testing...' : 'Test Connection'}
              </button>
            </div>

            {/* Save result toast */}
            {saveResult && (
              <div className={`status-banner ${saveResult.ok ? 'green' : 'red'}`} style={{ marginTop: 8 }}>
                {saveResult.ok ? 'Saved' : 'Error'}: {saveResult.message}
                {saveResult.ok && (
                  <p className="small" style={{ marginTop: 6, opacity: 0.85 }}>
                    Restart the agent process to use the new credentials.
                  </p>
                )}
              </div>
            )}

            {/* Test connection result */}
            {testResult && (
              <div className={`status-banner ${testResult.ok ? 'green' : 'red'}`} style={{ marginTop: 8 }}>
                {testResult.ok ? 'Success' : 'Failed'}: {testResult.message}
              </div>
            )}
          </div>

          <div className="info-box" style={{ marginTop: 24 }}>
            <p style={{ marginBottom: 8, fontWeight: 600 }}>How to get TOTP secret:</p>
            <ol style={{ paddingLeft: 16, fontSize: 12, color: 'var(--text2)', lineHeight: 1.6 }}>
              <li>Log in to Angel One → SmartAPI portal</li>
              <li>Go to "Enable TOTP" in your profile</li>
              <li>Click "Show Secret" to reveal the Base32 string</li>
              <li>Copy and paste it here</li>
            </ol>
          </div>
        </section>

      ) : (
        /* -- Connected View -- */
        <>
          <div className="status-banner green" style={{ marginBottom: 20, display: 'flex', alignItems: 'center' }}>
            Connected to Angel One — <strong style={{ marginLeft: 4 }}>{clientCode}</strong>
            <button
              onClick={handleDisconnect}
              style={{ marginLeft: 'auto', background: 'none', border: '1px solid rgba(72,187,120,.4)', color: 'var(--green)', borderRadius: 4, padding: '2px 10px', cursor: 'pointer', fontSize: 12 }}
            >
              Disconnect
            </button>
          </div>

          {/* Action buttons in connected view */}
          <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
            <button
              onClick={handleSaveToEnv}
              disabled={saving}
              style={{
                padding: '8px 18px',
                fontSize: 13,
                background: 'rgba(66,153,225,.15)',
                border: '1px solid rgba(66,153,225,.4)',
                color: '#4299e1',
                borderRadius: 6,
                cursor: saving ? 'wait' : 'pointer',
              }}
            >
              {saving ? 'Saving...' : 'Save to .env'}
            </button>

            <button
              onClick={handleTestConnection}
              disabled={testing}
              style={{
                padding: '8px 18px',
                fontSize: 13,
                background: 'rgba(128,128,128,.1)',
                border: '1px solid var(--border)',
                color: 'var(--text)',
                borderRadius: 6,
                cursor: testing ? 'wait' : 'pointer',
              }}
            >
              {testing ? 'Testing...' : 'Test Connection'}
            </button>
          </div>

          {/* Save/Test result banners in connected view */}
          {saveResult && (
            <div className={`status-banner ${saveResult.ok ? 'green' : 'red'}`} style={{ marginBottom: 12 }}>
              {saveResult.ok ? 'Saved' : 'Error'}: {saveResult.message}
              {saveResult.ok && (
                <p className="small" style={{ marginTop: 6, opacity: 0.85 }}>
                  Restart the agent process to use the new credentials.
                </p>
              )}
            </div>
          )}
          {testResult && (
            <div className={`status-banner ${testResult.ok ? 'green' : 'red'}`} style={{ marginBottom: 12 }}>
              {testResult.ok ? 'Success' : 'Failed'}: {testResult.message}
            </div>
          )}

          {/* Account summary from API */}
          {portLoading ? (
            <div className="empty-state">
              <div className="spinner" style={{ margin: '0 auto 12px' }} />
              Fetching live portfolio...
            </div>
          ) : p?.connected === false ? (
            <div className="status-banner red">
              {p?.message ?? 'Connection failed — check credentials and try again'}
            </div>
          ) : (
            <>
              <div className="kpi-row" style={{ marginBottom: 20 }}>
                <div className="kpi-card">
                  <div className="kpi-label">Net Value</div>
                  <div className="kpi-value">{'₹'}{(p?.net_value ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-label">Available Cash</div>
                  <div className="kpi-value green">{'₹'}{(p?.available_cash ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-label">Used Margin</div>
                  <div className="kpi-value yellow">{'₹'}{(p?.used_margin ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
                </div>
                <div className="kpi-card">
                  <div className="kpi-label">Unrealized P&L</div>
                  <div className={`kpi-value ${(p?.total_pnl_today ?? 0) >= 0 ? 'green' : 'red'}`}>
                    {'₹'}{(p?.total_pnl_today ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                  </div>
                </div>
              </div>

              <section className="section">
                <h2>Live Positions ({livePositions.length})</h2>
                {livePositions.length > 0 ? (
                  <table className="trade-table">
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        <th>Type</th>
                        <th>Qty</th>
                        <th>Avg Price</th>
                        <th>LTP</th>
                        <th>P&L</th>
                        <th>Change %</th>
                      </tr>
                    </thead>
                    <tbody>
                      {livePositions.map((pos: any, i: number) => (
                        <tr key={i} className={pos.pnl >= 0 ? 'win' : 'loss'}>
                          <td><strong>{pos.symbol}</strong></td>
                          <td><span className="badge">{pos.product_type}</span></td>
                          <td>{pos.qty}</td>
                          <td>{'₹'}{(pos.avg_price ?? 0).toFixed(2)}</td>
                          <td>{'₹'}{(pos.ltp ?? 0).toFixed(2)}</td>
                          <td className={pos.pnl >= 0 ? 'green' : 'red'}>
                            {'₹'}{(pos.pnl ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
                          </td>
                          <td className={pos.day_change_pct >= 0 ? 'green' : 'red'}>
                            {(pos.day_change_pct ?? 0).toFixed(2)}%
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="empty-state">No open positions in your Angel One account</div>
                )}
              </section>

              <div className="info-box" style={{ marginTop: 16 }}>
                <p className="small">
                  <strong>Read-only view.</strong> This dashboard cannot place, modify, or cancel orders.
                  Trading is done by the paper agent using simulated capital only.
                  Refreshes every 30 seconds.
                </p>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}
