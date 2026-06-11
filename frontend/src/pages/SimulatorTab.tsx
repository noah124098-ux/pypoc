import { useState, useEffect, useRef } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { useApi, apiPost } from '../hooks/useSnapshot'
import { useToasts } from '../hooks/useNotifications'

// ── helpers ──────────────────────────────────────────────────────────────────

function fmtRupee(n: number): string {
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

function isMarketOpen(): boolean {
  const now = new Date()
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000
  const istMs = utcMs + 5.5 * 3600000
  const ist = new Date(istMs)
  const h = ist.getHours()
  const m = ist.getMinutes()
  const dow = ist.getDay()

  // Market hours: 09:15 - 15:30, Mon-Fri
  const isWeekday = dow > 0 && dow < 6
  const nowMins = h * 60 + m
  const openMins = 9 * 60 + 15  // 09:15
  const closeMins = 15 * 60 + 30 // 15:30

  return isWeekday && nowMins >= openMins && nowMins < closeMins
}

function fmtTime(ts: string | number | undefined): string {
  if (!ts) return ''
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function fmtHHMM(ts: string | number | undefined): string {
  if (!ts) return ''
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

function pct(value: number, base: number): string {
  if (!base) return '0.00%'
  return ((value / base) * 100).toFixed(2) + '%'
}

// ── types ────────────────────────────────────────────────────────────────────

interface SimStatus {
  running: boolean
  capital: number
  equity: number
  equity_curve: Array<{ ts: number; equity: number }>
  open_positions: Array<{
    id?: string
    symbol: string
    side: 'BUY' | 'SELL'
    qty: number
    entry_price: number
    current_price?: number
    strategy?: string
    opened_at?: string | number
  }>
  closed_trades: Array<{
    id?: string
    symbol: string
    side: 'BUY' | 'SELL'
    qty: number
    entry_price: number
    exit_price?: number
    pnl?: number
    strategy?: string
    closed_at?: string | number
  }>
  total_trades: number
  win_trades: number
  total_pnl: number
  max_drawdown_pct: number
  message?: string
}

// ── CSS injection (slide-in animation for trade log rows) ────────────────────

const STYLE_ID = 'sim-slide-in-style'
if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const s = document.createElement('style')
  s.id = STYLE_ID
  s.textContent = `
    @keyframes simSlideIn {
      from { opacity: 0; transform: translateY(-6px); }
      to   { opacity: 1; transform: translateY(0); }
    }
    .sim-trade-row-new { animation: simSlideIn 0.25s ease; }
    @keyframes simPulse {
      0%, 100% { box-shadow: 0 0 0 0 rgba(72,187,120,.5); }
      50%       { box-shadow: 0 0 0 6px rgba(72,187,120,0); }
    }
    .sim-running-pulse { animation: simPulse 2s infinite; }
  `
  document.head.appendChild(s)
}

// ── component ─────────────────────────────────────────────────────────────────

export function SimulatorTab({ snap }: { snap: any }) {
  const { addToast } = useToasts()

  // ── config controls ───────────────────────────────────────────────────────
  const [capital, setCapital] = useState(500000)
  const [riskPct, setRiskPct] = useState(1.0)
  const [maxPos, setMaxPos] = useState(5)

  // ── simulator state ───────────────────────────────────────────────────────
  const [running, setRunning] = useState(false)
  const [statusMsg, setStatusMsg] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [simStatus, setSimStatus] = useState<SimStatus | null>(null)

  // track which trade IDs are "new" so we can animate them
  const seenTradeIds = useRef<Set<string>>(new Set())
  const [newTradeIds, setNewTradeIds] = useState<Set<string>>(new Set())

  // ── poll /api/simulator/status every 2s when running ────────────────────
  const { data: polledStatus } = useApi<SimStatus>(
    running ? '/api/simulator/status' : '',
    2000,
  )

  useEffect(() => {
    if (!polledStatus) return
    setSimStatus(polledStatus)
    setRunning(polledStatus.running)

    // detect new closed trades for animation
    const trades = polledStatus.closed_trades ?? []
    const incoming = new Set<string>()
    trades.forEach((t, i) => {
      const tid = t.id ?? String(i)
      if (!seenTradeIds.current.has(tid)) {
        seenTradeIds.current.add(tid)
        incoming.add(tid)
      }
    })
    if (incoming.size > 0) {
      setNewTradeIds(prev => new Set([...prev, ...incoming]))
      // clear "new" flag after animation finishes
      setTimeout(() => {
        setNewTradeIds(prev => {
          const next = new Set(prev)
          incoming.forEach(id => next.delete(id))
          return next
        })
      }, 600)
    }
  }, [polledStatus])

  // ── on first mount: check current status once ────────────────────────────
  useEffect(() => {
    apiPost('/api/simulator/status', undefined)
      .then((d: SimStatus) => {
        setSimStatus(d)
        setRunning(d.running)
      })
      .catch(() => {})
  }, [])

  // ── derived values ────────────────────────────────────────────────────────
  const effectiveCapital = simStatus?.capital ?? capital
  const equity = simStatus?.equity ?? effectiveCapital
  const pnlAbs = equity - effectiveCapital
  const pnlSign = pnlAbs >= 0 ? '+' : ''
  const pnlColor = pnlAbs >= 0 ? '#48bb78' : '#fc8181'

  const totalTrades = simStatus?.total_trades ?? 0
  const winTrades = simStatus?.win_trades ?? 0
  const winRate = totalTrades > 0 ? ((winTrades / totalTrades) * 100).toFixed(1) : '—'
  const maxDD = simStatus?.max_drawdown_pct ?? 0
  const ddColor = maxDD > 10 ? '#fc8181' : maxDD > 5 ? '#ecc94b' : '#48bb78'

  const positions = simStatus?.open_positions ?? (snap?.open_positions ?? snap?.positions ?? [])
  const closedTrades = [...(simStatus?.closed_trades ?? [])].reverse().slice(0, 50)

  const equityCurve = (simStatus?.equity_curve ?? []).map(pt => ({
    ts: pt.ts,
    label: fmtHHMM(pt.ts),
    equity: pt.equity,
  }))

  // ── handlers ──────────────────────────────────────────────────────────────

  async function startSimulator() {
    if (!isMarketOpen()) {
      addToast('Simulator only runs during market hours (09:15–15:30 IST, Mon–Fri)', 'warning')
      return
    }

    setActionLoading(true)
    setStatusMsg('')
    try {
      const d = await apiPost('/api/simulator/start', {
        capital,
        risk_pct: riskPct,
        max_positions: maxPos,
      })
      if (d.started || d.running) {
        setRunning(true)
        setStatusMsg('')
        addToast('Simulator started', 'success')
      } else {
        setStatusMsg(d.message ?? 'Failed to start')
        addToast('Failed to start simulator', 'error')
      }
    } catch (e: any) {
      setStatusMsg(e?.message ?? 'API error')
      addToast('API error: ' + (e?.message ?? 'Unknown error'), 'error')
    } finally {
      setActionLoading(false)
    }
  }

  async function stopSimulator() {
    setActionLoading(true)
    try {
      await apiPost('/api/simulator/stop', {})
      setRunning(false)
      addToast('Simulator stopped', 'success')
    } catch (e: any) {
      setStatusMsg(e?.message ?? 'API error')
      addToast('Failed to stop simulator', 'error')
    } finally {
      setActionLoading(false)
    }
  }

  // ── derived values ────────────────────────────────────────────────────────
  const marketOpen = isMarketOpen()

  // ── render ────────────────────────────────────────────────────────────────

  return (
    <div className="tab-content">
      <div className="tab-title">Autonomous Simulator</div>

      {!marketOpen && (
        <div className="banner banner-warn" style={{ marginBottom: 16 }}>
          ⏰ Market hours only (09:15–15:30 IST, Mon–Fri) — simulator is disabled outside these times
        </div>
      )}

      {/* ── Config + Start/Stop row ──────────────────────────────────────── */}
      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: 20,
          alignItems: 'flex-end',
        }}>
          {/* Capital */}
          <div style={{ minWidth: 160 }}>
            <div className="sim-label">Simulation Capital (₹)</div>
            <input
              className="sim-input"
              type="number"
              value={capital}
              min={10000}
              step={10000}
              disabled={running}
              onChange={e => setCapital(Number(e.target.value))}
              style={{ opacity: running ? 0.6 : 1 }}
            />
          </div>

          {/* Risk slider */}
          <div style={{ minWidth: 180 }}>
            <div className="sim-label">Risk Per Trade: {riskPct.toFixed(2)}%</div>
            <input
              type="range" min={0.25} max={3} step={0.25}
              value={riskPct}
              disabled={running}
              onChange={e => setRiskPct(Number(e.target.value))}
              style={{ width: '100%', accentColor: '#4fc3f7', opacity: running ? 0.6 : 1 }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text2)' }}>
              <span>0.25%</span><span>3%</span>
            </div>
          </div>

          {/* Max positions slider */}
          <div style={{ minWidth: 160 }}>
            <div className="sim-label">Max Positions: {maxPos}</div>
            <input
              type="range" min={1} max={10} step={1}
              value={maxPos}
              disabled={running}
              onChange={e => setMaxPos(Number(e.target.value))}
              style={{ width: '100%', accentColor: '#4fc3f7', opacity: running ? 0.6 : 1 }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text2)' }}>
              <span>1</span><span>10</span>
            </div>
          </div>

          {/* Start / Stop + status badge */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
            {!running ? (
              <button
                className="btn-success"
                onClick={startSimulator}
                disabled={actionLoading || !marketOpen}
                style={{ padding: '10px 28px', fontSize: 14, fontWeight: 700, opacity: marketOpen ? 1 : 0.6 }}
                title={marketOpen ? '' : 'Market hours only (09:15–15:30 IST, Mon–Fri)'}
              >
                {actionLoading ? '...' : 'Start Simulator'}
              </button>
            ) : (
              <button
                className="btn-danger"
                onClick={stopSimulator}
                disabled={actionLoading}
                style={{ padding: '10px 28px', fontSize: 14, fontWeight: 700 }}
              >
                {actionLoading ? '...' : 'Stop'}
              </button>
            )}
            {!marketOpen && !running && (
              <span style={{ fontSize: 11, color: 'var(--text2)' }}>Market hours only</span>
            )}

            {/* Status badge */}
            <span style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '5px 14px', borderRadius: 20, fontSize: 12, fontWeight: 700,
              background: running ? 'rgba(72,187,120,.15)' : 'rgba(100,116,139,.15)',
              color: running ? '#48bb78' : '#94a3b8',
              border: `1px solid ${running ? 'rgba(72,187,120,.35)' : 'rgba(100,116,139,.25)'}`,
            }}
              className={running ? 'sim-running-pulse' : ''}
            >
              <span style={{
                display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                background: running ? '#48bb78' : '#64748b',
              }} />
              {running ? 'RUNNING' : 'STOPPED'}
            </span>

            {statusMsg && (
              <span style={{ fontSize: 12, color: '#fc8181' }}>{statusMsg}</span>
            )}
          </div>
        </div>
      </div>

      {/* ── Stats + Equity Curve ─────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 16, marginBottom: 16 }}>

        {/* Summary stats */}
        <div className="console-panel" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', letterSpacing: '.06em' }}>
            SUMMARY
          </div>

          <div>
            <div className="kpi-label">Equity</div>
            <div style={{ fontSize: 18, fontWeight: 700, color: pnlColor }}>
              {fmtRupee(equity)}
            </div>
            <div style={{ fontSize: 11, color: pnlColor, marginTop: 2 }}>
              {pnlSign}{fmtRupee(pnlAbs)} ({pnlSign}{pct(Math.abs(pnlAbs), effectiveCapital)})
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div className="kpi-card" style={{ padding: 8 }}>
              <div className="kpi-label">Trades</div>
              <div className="kpi-value" style={{ fontSize: 16 }}>{totalTrades}</div>
            </div>
            <div className="kpi-card" style={{ padding: 8 }}>
              <div className="kpi-label">Win Rate</div>
              <div className="kpi-value" style={{ fontSize: 16, color: '#4fc3f7' }}>
                {winRate}{totalTrades > 0 ? '%' : ''}
              </div>
            </div>
            <div className="kpi-card" style={{ padding: 8 }}>
              <div className="kpi-label">P&L</div>
              <div className="kpi-value" style={{ fontSize: 16, color: pnlColor }}>
                {pnlSign}{fmtRupee(pnlAbs)}
              </div>
            </div>
            <div className="kpi-card" style={{ padding: 8 }}>
              <div className="kpi-label">Max DD</div>
              <div className="kpi-value" style={{ fontSize: 16, color: ddColor }}>
                {maxDD.toFixed(1)}%
              </div>
            </div>
          </div>
        </div>

        {/* Equity curve */}
        <div className="console-panel">
          <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', marginBottom: 12, letterSpacing: '.06em' }}>
            EQUITY CURVE
            {running && (
              <span style={{ fontSize: 10, color: '#718096', fontWeight: 400, marginLeft: 10 }}>
                live — updating every 2s
              </span>
            )}
          </div>

          {equityCurve.length < 2 ? (
            <div style={{
              height: 160, display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--text2)', fontSize: 12, fontFamily: 'monospace',
            }}>
              {running
                ? 'Waiting for first trades...'
                : 'Click Start Simulator to begin autonomous simulation'}
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={160}>
              <LineChart data={equityCurve} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1a2035" />
                <XAxis
                  dataKey="label"
                  tick={{ fill: '#718096', fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  domain={['auto', 'auto']}
                  tick={{ fill: '#718096', fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => '₹' + (v / 1000).toFixed(0) + 'k'}
                  width={52}
                />
                <Tooltip
                  contentStyle={{ background: '#0a0e1a', border: '1px solid #2d3748', fontSize: 11 }}
                  labelStyle={{ color: '#94a3b8' }}
                  formatter={(v: unknown) => [fmtRupee(Number(v ?? 0)), 'Equity'] as [string, string]}
                />
                {/* baseline = starting capital */}
                <Line
                  type="monotone"
                  dataKey="equity"
                  stroke="#4fc3f7"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: '#4fc3f7' }}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* ── Active Positions ─────────────────────────────────────────────── */}
      <div className="console-panel" style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', marginBottom: 10, letterSpacing: '.06em' }}>
          ACTIVE POSITIONS ({positions.length} / {simStatus?.capital ? maxPos : maxPos})
        </div>

        {positions.length === 0 ? (
          <div style={{ color: 'var(--text2)', fontSize: 12, fontFamily: 'monospace', padding: '6px 0' }}>
            {running ? 'Agent scanning Nifty 50 for entry signals...' : 'No open positions.'}
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, fontFamily: 'monospace' }}>
              <thead>
                <tr style={{ color: 'var(--text2)', borderBottom: '1px solid #2d3748' }}>
                  <th style={{ textAlign: 'left', padding: '4px 8px', fontWeight: 600 }}>Symbol</th>
                  <th style={{ textAlign: 'left', padding: '4px 8px', fontWeight: 600 }}>Side</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 600 }}>Qty</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 600 }}>Entry</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 600 }}>LTP</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', fontWeight: 600 }}>Unreal P&L</th>
                  <th style={{ textAlign: 'left', padding: '4px 8px', fontWeight: 600 }}>Strategy</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((pos: any, i: number) => {
                  const entry = pos.entry_price ?? 0
                  const ltp = pos.current_price ?? entry
                  const dir = pos.side === 'BUY' ? 1 : -1
                  const unrealPnl = (ltp - entry) * (pos.qty ?? 0) * dir
                  const pnlC = unrealPnl >= 0 ? '#48bb78' : '#fc8181'
                  const pnlPct = entry > 0 ? ((ltp - entry) / entry * 100 * dir).toFixed(2) : '0.00'
                  return (
                    <tr key={pos.id ?? i} style={{ borderBottom: '1px solid #1a2035' }}>
                      <td style={{ padding: '6px 8px', color: '#e2e8f0', fontWeight: 700 }}>{pos.symbol}</td>
                      <td style={{ padding: '6px 8px', color: pos.side === 'BUY' ? '#48bb78' : '#fc8181', fontWeight: 700 }}>
                        {pos.side}
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text2)' }}>{pos.qty}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', color: 'var(--text2)' }}>{fmtRupee(entry)}</td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', color: '#e2e8f0' }}>
                        {ltp !== entry ? fmtRupee(ltp) : '—'}
                      </td>
                      <td style={{ padding: '6px 8px', textAlign: 'right', color: pnlC, fontWeight: 600 }}>
                        {unrealPnl >= 0 ? '+' : ''}{fmtRupee(unrealPnl)}
                        <span style={{ fontSize: 10, marginLeft: 4 }}>({unrealPnl >= 0 ? '+' : ''}{pnlPct}%)</span>
                      </td>
                      <td style={{ padding: '6px 8px', color: '#718096' }}>{pos.strategy ?? '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* ── Trade Log ───────────────────────────────────────────────────── */}
      <div className="console-panel">
        <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', marginBottom: 10, letterSpacing: '.06em' }}>
          TRADE LOG
          <span style={{ fontSize: 10, color: '#718096', fontWeight: 400, marginLeft: 10 }}>
            newest first · max 50
          </span>
        </div>

        <div className="console-output" style={{ maxHeight: 280, overflowY: 'auto' }}>
          {closedTrades.length === 0 ? (
            <div style={{ color: 'var(--text2)', fontSize: 11 }}>
              {running ? 'Waiting for first closed trade...' : 'No trades yet — start the simulator above.'}
            </div>
          ) : (
            closedTrades.map((t: any, i: number) => {
              const tid = t.id ?? String(i)
              const isNew = newTradeIds.has(tid)
              const tPnl = t.pnl ?? 0
              const pnlC = tPnl >= 0 ? '#48bb78' : '#fc8181'
              const side = (t.side ?? '').toUpperCase()
              const ts = t.closed_at ?? t.opened_at ?? ''
              return (
                <div
                  key={tid}
                  className={`console-log-line${isNew ? ' sim-trade-row-new' : ''}`}
                  style={{ padding: '3px 0', gap: 6 }}
                >
                  <span style={{ color: '#718096', fontSize: 11, minWidth: 50 }}>{fmtTime(ts)}</span>
                  <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: 12, minWidth: 90 }}>
                    {t.symbol ?? '—'}
                  </span>
                  <span style={{
                    color: side === 'BUY' ? '#48bb78' : '#fc8181',
                    fontSize: 11, fontWeight: 700, minWidth: 32,
                  }}>
                    {side}
                  </span>
                  <span style={{ color: 'var(--text2)', fontSize: 11, minWidth: 24 }}>
                    {t.qty ?? ''}
                  </span>
                  {t.entry_price != null && (
                    <span style={{ color: 'var(--text2)', fontSize: 11 }}>
                      @{fmtRupee(t.entry_price)}
                    </span>
                  )}
                  {t.exit_price != null && (
                    <span style={{ color: 'var(--text2)', fontSize: 11 }}>
                      {'→'}{fmtRupee(t.exit_price)}
                    </span>
                  )}
                  {t.strategy && (
                    <span style={{ color: '#4a5568', fontSize: 11 }}>[{t.strategy}]</span>
                  )}
                  <span style={{
                    color: pnlC, fontSize: 12, fontWeight: 700, marginLeft: 'auto',
                  }}>
                    {tPnl >= 0 ? '+' : ''}{fmtRupee(tPnl)}
                  </span>
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
