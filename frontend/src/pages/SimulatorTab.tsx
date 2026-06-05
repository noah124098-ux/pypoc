import { useState, useEffect } from 'react'
import { useApi, apiPost } from '../hooks/useSnapshot'

const NIFTY_50 = [
  'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK',
  'BAJAJ-AUTO', 'BAJFINANCE', 'BAJAJFINSV', 'BEL', 'BHARTIARTL',
  'BPCL', 'BRITANNIA', 'CIPLA', 'COALINDIA', 'DRREDDY',
  'EICHERMOT', 'GRASIM', 'HCLTECH', 'HDFCBANK', 'HDFCLIFE',
  'HEROMOTOCO', 'HINDALCO', 'HINDUNILVR', 'ICICIBANK', 'INDUSINDBK',
  'INFY', 'ITC', 'JSWSTEEL', 'KOTAKBANK', 'LT',
  'M&M', 'MARUTI', 'NESTLEIND', 'NTPC', 'ONGC',
  'POWERGRID', 'RELIANCE', 'SBILIFE', 'SBIN', 'SHRIRAMFIN',
  'SUNPHARMA', 'TATACONSUM', 'TATAMOTORS', 'TATASTEEL', 'TCS',
  'TECHM', 'TITAN', 'TRENT', 'ULTRACEMCO', 'WIPRO',
]

const STRATEGIES = [
  'bb_squeeze', 'mean_reversion', 'rsi_momentum', 'trend_breakout',
  'orb', 'stat_arb', 'gap_and_hold', 'volume_breakout_confirm', 'manual',
]

function getISTMarketStatus(): 'OPEN' | 'CLOSED' {
  const now = new Date()
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000
  const ist = new Date(utcMs + 5.5 * 3600000)
  const dow = ist.getDay()
  if (dow === 0 || dow === 6) return 'CLOSED'
  const mins = ist.getHours() * 60 + ist.getMinutes()
  return mins >= 9 * 60 + 15 && mins < 15 * 60 + 30 ? 'OPEN' : 'CLOSED'
}

function fmtRupee(n: number): string {
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

function fmtTime(ts: string | number | undefined): string {
  if (!ts) return ''
  const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function SimulatorTab({ snap }: { snap: any }) {
  // --- Capital controls ---
  const [capital, setCapital] = useState(500000)
  const [riskPct, setRiskPct] = useState(1.0)
  const [maxPos, setMaxPos] = useState(5)
  const [paramsMsg, setParamsMsg] = useState('')
  const [paramsLoading, setParamsLoading] = useState(false)

  // --- Market watch ---
  const [marketStatus, setMarketStatus] = useState<'OPEN' | 'CLOSED'>(getISTMarketStatus())
  const { data: breadth } = useApi<any>('/api/nifty-breadth', 30000)

  useEffect(() => {
    const t = setInterval(() => setMarketStatus(getISTMarketStatus()), 30000)
    return () => clearInterval(t)
  }, [])

  // --- Trade form ---
  const [symbol, setSymbol] = useState('RELIANCE')
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY')
  const [qty, setQty] = useState(10)
  const [price, setPrice] = useState('')
  const [stopLoss, setStopLoss] = useState('')
  const [target, setTarget] = useState('')
  const [strategy, setStrategy] = useState('manual')
  const [tradeMsg, setTradeMsg] = useState('')
  const [tradeLoading, setTradeLoading] = useState(false)

  // --- Auto-calc qty from capital + risk ---
  useEffect(() => {
    const priceVal = parseFloat(price)
    if (priceVal > 0 && capital > 0 && riskPct > 0) {
      const riskAmount = capital * (riskPct / 100)
      const slDist = parseFloat(stopLoss) > 0 ? Math.abs(priceVal - parseFloat(stopLoss)) : priceVal * 0.02
      if (slDist > 0) {
        const autoQty = Math.max(1, Math.floor(riskAmount / slDist))
        setQty(autoQty)
      }
    }
  }, [capital, riskPct, price, stopLoss])

  // --- Auto-calc target as 2R ---
  useEffect(() => {
    const priceVal = parseFloat(price)
    const slVal = parseFloat(stopLoss)
    if (priceVal > 0 && slVal > 0) {
      const slDist = Math.abs(priceVal - slVal)
      const autoTarget = side === 'BUY'
        ? (priceVal + 2 * slDist).toFixed(2)
        : (priceVal - 2 * slDist).toFixed(2)
      setTarget(autoTarget)
    }
  }, [price, stopLoss, side])

  // --- Simulation log (last 20 events) ---
  const { data: sigData } = useApi<any>('/api/signals?limit=10', 10000)
  const { data: tradeData } = useApi<any>('/api/trades?limit=10', 10000)

  const signals: any[] = sigData ? (Array.isArray(sigData) ? sigData : sigData.data ?? []) : []
  const trades: any[] = tradeData ? (Array.isArray(tradeData) ? tradeData : tradeData.data ?? []) : []

  const logEvents = [...signals.map((s: any) => ({ ...s, _type: 'signal' })),
                    ...trades.map((t: any) => ({ ...t, _type: 'trade' }))]
    .sort((a, b) => {
      const ta = a.ts || a.opened_at || ''
      const tb = b.ts || b.opened_at || ''
      return ta < tb ? 1 : -1
    })
    .slice(0, 20)

  // --- Positions ---
  const positions: any[] = snap?.open_positions ?? snap?.positions ?? []

  const equity = snap?.equity ?? capital

  // --- Handlers ---
  async function applyParams() {
    setParamsLoading(true)
    setParamsMsg('')
    try {
      const d = await apiPost('/api/simulator/set-params', {
        capital,
        risk_pct: riskPct,
        max_positions: maxPos,
      })
      setParamsMsg(d.applied ? '✓ Parameters applied' : '✗ ' + (d.message ?? 'Failed'))
    } catch {
      setParamsMsg('✗ API not reachable')
    } finally {
      setParamsLoading(false)
      setTimeout(() => setParamsMsg(''), 5000)
    }
  }

  async function simulateTrade() {
    if (!price) { setTradeMsg('✗ Enter a price first'); return }
    setTradeLoading(true)
    setTradeMsg('')
    try {
      const d = await apiPost('/api/simulator/trade', {
        symbol,
        side,
        qty,
        price: parseFloat(price),
        stop_loss: parseFloat(stopLoss) || null,
        target: parseFloat(target) || null,
        strategy,
      })
      if (d.queued) {
        setTradeMsg(
          `ORDER QUEUED — ${qty} ${symbol} @ ₹${price}` +
          (stopLoss ? ` | SL: ₹${stopLoss}` : '') +
          (target ? ` | Target: ₹${target}` : '') +
          ` [${d.command_id?.slice(0, 8)}]`
        )
      } else {
        setTradeMsg('✗ ' + (d.message ?? JSON.stringify(d)))
      }
    } catch (e: any) {
      setTradeMsg('✗ ' + (e?.message ?? 'API error'))
    } finally {
      setTradeLoading(false)
      setTimeout(() => setTradeMsg(''), 10000)
    }
  }

  async function closePosition(pos: any) {
    try {
      const closeSide = pos.side === 'BUY' ? 'SELL' : 'BUY'
      const d = await apiPost('/api/command/place-paper-order', {
        symbol: pos.symbol,
        side: closeSide,
        qty: pos.qty,
        price: pos.current_price ?? pos.entry_price,
        strategy: pos.strategy ?? 'manual',
        stop_loss: null,
        target: null,
      })
      setTradeMsg(d.queued
        ? `✓ Close order queued for ${pos.symbol}`
        : '✗ ' + JSON.stringify(d))
    } catch {
      setTradeMsg('✗ Failed to close position')
    }
    setTimeout(() => setTradeMsg(''), 5000)
  }

  const regime = snap?.current_regime ?? '—'
  const vix = snap?.vix ?? null
  const niftyLtp = snap?.nifty_ltp ?? null

  return (
    <div className="tab-content">
      <div className="tab-title">Simulator Console</div>

      {/* ── Top grid: Capital controls + Market Watch ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 20 }}>

        {/* Capital Control Panel */}
        <div className="console-panel">
          <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', marginBottom: 12, letterSpacing: '.06em' }}>
            CAPITAL CONTROL PANEL
          </div>
          <div style={{ marginBottom: 14 }}>
            <div className="sim-label">Simulation Capital (₹)</div>
            <input
              className="sim-input"
              type="number"
              value={capital}
              min={10000}
              step={10000}
              onChange={e => setCapital(Number(e.target.value))}
            />
          </div>
          <div style={{ marginBottom: 14 }}>
            <div className="sim-label">Risk Per Trade: {riskPct.toFixed(2)}%</div>
            <input
              type="range" min={0.25} max={3} step={0.25}
              value={riskPct}
              onChange={e => setRiskPct(Number(e.target.value))}
              style={{ width: '100%', accentColor: '#4fc3f7' }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text2)' }}>
              <span>0.25%</span><span>3%</span>
            </div>
          </div>
          <div style={{ marginBottom: 14 }}>
            <div className="sim-label">Max Positions: {maxPos}</div>
            <input
              type="range" min={1} max={10} step={1}
              value={maxPos}
              onChange={e => setMaxPos(Number(e.target.value))}
              style={{ width: '100%', accentColor: '#4fc3f7' }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text2)' }}>
              <span>1</span><span>10</span>
            </div>
          </div>
          <button
            className="btn-success"
            onClick={applyParams}
            disabled={paramsLoading}
            style={{ width: '100%', marginBottom: 8 }}
          >
            {paramsLoading ? '...' : 'Apply Parameters'}
          </button>
          {paramsMsg && (
            <div style={{ fontSize: 12, color: paramsMsg.startsWith('✓') ? '#48bb78' : '#fc8181', marginTop: 4 }}>
              {paramsMsg}
            </div>
          )}
          <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text2)' }}>
            Current simulated equity:&nbsp;
            <span style={{ color: '#4fc3f7', fontWeight: 700 }}>{fmtRupee(equity)}</span>
          </div>
        </div>

        {/* Market Watch */}
        <div className="console-panel">
          <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', marginBottom: 12, letterSpacing: '.06em' }}>
            MARKET WATCH
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
            <span style={{
              padding: '3px 12px', borderRadius: 12, fontSize: 12, fontWeight: 700,
              background: marketStatus === 'OPEN' ? 'rgba(72,187,120,.2)' : 'rgba(252,129,129,.15)',
              color: marketStatus === 'OPEN' ? '#48bb78' : '#fc8181',
              border: `1px solid ${marketStatus === 'OPEN' ? 'rgba(72,187,120,.4)' : 'rgba(252,129,129,.3)'}`,
            }}>
              {marketStatus === 'OPEN' ? '● OPEN' : '○ CLOSED'}
            </span>
            <span style={{ fontSize: 11, color: 'var(--text2)' }}>NSE Market (IST)</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            <div className="kpi-card" style={{ padding: 10 }}>
              <div className="kpi-label">Regime</div>
              <div className="kpi-value" style={{ fontSize: 14, color: '#4fc3f7' }}>{regime}</div>
            </div>
            <div className="kpi-card" style={{ padding: 10 }}>
              <div className="kpi-label">India VIX</div>
              <div className="kpi-value" style={{ fontSize: 14, color: vix && vix > 20 ? '#fc8181' : '#4fc3f7' }}>
                {vix != null ? vix.toFixed(2) : '—'}
              </div>
            </div>
            <div className="kpi-card" style={{ padding: 10 }}>
              <div className="kpi-label">Nifty LTP</div>
              <div className="kpi-value" style={{ fontSize: 14, color: '#48bb78' }}>
                {niftyLtp != null ? fmtRupee(niftyLtp) : '—'}
              </div>
            </div>
            <div className="kpi-card" style={{ padding: 10 }}>
              <div className="kpi-label">Breadth</div>
              <div className="kpi-value" style={{ fontSize: 14 }}>
                {breadth?.advances != null
                  ? <span style={{ color: '#48bb78' }}>{breadth.advances}↑</span>
                  : '—'}
                {breadth?.declines != null && (
                  <span style={{ color: '#fc8181', marginLeft: 6 }}>{breadth.declines}↓</span>
                )}
              </div>
            </div>
          </div>
          <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text2)' }}>
            Open positions: {positions.length} / {maxPos}
          </div>
        </div>
      </div>

      {/* ── Manual Trade Console ── */}
      <div className="console-panel" style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', marginBottom: 12, letterSpacing: '.06em' }}>
          MANUAL TRADE CONSOLE
        </div>

        <div className="simulator-form">
          {/* Symbol */}
          <div>
            <div className="sim-label">Symbol</div>
            <select className="sim-input" value={symbol} onChange={e => setSymbol(e.target.value)}>
              {NIFTY_50.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          {/* Side */}
          <div>
            <div className="sim-label">Side</div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                className={side === 'BUY' ? 'btn-trade-buy' : 'btn-trade-inactive'}
                onClick={() => setSide('BUY')}
                style={side === 'BUY' ? {} : { opacity: 0.5 }}
              >
                BUY
              </button>
              <button
                className={side === 'SELL' ? 'btn-trade-sell' : 'btn-trade-inactive'}
                onClick={() => setSide('SELL')}
                style={side === 'SELL' ? {} : { opacity: 0.5 }}
              >
                SELL
              </button>
            </div>
          </div>

          {/* Price */}
          <div>
            <div className="sim-label">Price (₹)</div>
            <input
              className="sim-input"
              type="number"
              placeholder="e.g. 2450.00"
              value={price}
              onChange={e => setPrice(e.target.value)}
              step={0.05}
            />
          </div>

          {/* Qty */}
          <div>
            <div className="sim-label">Quantity (auto from risk)</div>
            <input
              className="sim-input"
              type="number"
              min={1}
              value={qty}
              onChange={e => setQty(Number(e.target.value))}
            />
          </div>

          {/* Stop Loss */}
          <div>
            <div className="sim-label">Stop Loss (₹)</div>
            <input
              className="sim-input"
              type="number"
              placeholder="e.g. 2400.00"
              value={stopLoss}
              onChange={e => setStopLoss(e.target.value)}
              step={0.05}
            />
          </div>

          {/* Target */}
          <div>
            <div className="sim-label">Target (₹, auto 2R)</div>
            <input
              className="sim-input"
              type="number"
              placeholder="e.g. 2550.00"
              value={target}
              onChange={e => setTarget(e.target.value)}
              step={0.05}
            />
          </div>

          {/* Strategy */}
          <div>
            <div className="sim-label">Strategy</div>
            <select className="sim-input" value={strategy} onChange={e => setStrategy(e.target.value)}>
              {STRATEGIES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>

          {/* Risk preview */}
          <div style={{ display: 'flex', flexDirection: 'column', justifyContent: 'flex-end' }}>
            <div style={{ background: 'var(--bg)', borderRadius: 6, padding: '8px 12px', fontSize: 11, color: 'var(--text2)', fontFamily: 'monospace' }}>
              <div>Risk amt: {fmtRupee(capital * riskPct / 100)}</div>
              <div>Notional: {price ? fmtRupee(qty * parseFloat(price)) : '—'}</div>
              {stopLoss && price && (
                <div>R-dist: ₹{Math.abs(parseFloat(price) - parseFloat(stopLoss)).toFixed(2)}</div>
              )}
            </div>
          </div>
        </div>

        <div style={{ marginTop: 14, display: 'flex', gap: 12, alignItems: 'center' }}>
          <button
            className={side === 'BUY' ? 'btn-trade-buy' : 'btn-trade-sell'}
            onClick={simulateTrade}
            disabled={tradeLoading || !price}
            style={{ minWidth: 180 }}
          >
            {tradeLoading ? 'Queuing...' : `Simulate ${side}`}
          </button>
          {tradeMsg && (
            <div className="console-output" style={{ flex: 1, minHeight: 'unset', padding: '8px 12px', fontSize: 11 }}>
              {tradeMsg}
            </div>
          )}
        </div>
      </div>

      {/* ── Active Simulated Positions ── */}
      <div className="console-panel" style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', marginBottom: 12, letterSpacing: '.06em' }}>
          ACTIVE SIMULATED POSITIONS ({positions.length})
        </div>
        {positions.length === 0 ? (
          <div style={{ color: 'var(--text2)', fontSize: 12, fontFamily: 'monospace', padding: '8px 0' }}>
            No open positions — simulate a trade above.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {positions.map((pos: any, i: number) => {
              const entryPrice = pos.entry_price ?? 0
              const curPrice = pos.current_price ?? entryPrice
              const unrealPnl = (curPrice - entryPrice) * (pos.qty ?? 0) * (pos.side === 'BUY' ? 1 : -1)
              return (
                <div key={pos.id ?? i} style={{
                  background: 'var(--bg)', borderRadius: 6, padding: '10px 14px',
                  border: `1px solid ${unrealPnl >= 0 ? 'rgba(72,187,120,.3)' : 'rgba(252,129,129,.3)'}`,
                  display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap',
                }}>
                  <span style={{ fontWeight: 700, color: pos.side === 'BUY' ? '#48bb78' : '#fc8181', minWidth: 40 }}>
                    {pos.side}
                  </span>
                  <span style={{ fontWeight: 700, color: '#e2e8f0', minWidth: 100 }}>{pos.symbol}</span>
                  <span style={{ fontSize: 12, color: 'var(--text2)' }}>
                    {pos.qty} @ {fmtRupee(entryPrice)}
                  </span>
                  {curPrice !== entryPrice && (
                    <span style={{ fontSize: 12, color: 'var(--text2)' }}>
                      LTP: {fmtRupee(curPrice)}
                    </span>
                  )}
                  <span style={{ fontSize: 12, fontWeight: 600, color: unrealPnl >= 0 ? '#48bb78' : '#fc8181' }}>
                    {unrealPnl >= 0 ? '+' : ''}{fmtRupee(unrealPnl)}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--text2)', flex: 1 }}>
                    {pos.strategy ?? ''}
                  </span>
                  <button
                    className="btn-danger"
                    style={{ padding: '4px 12px', fontSize: 12 }}
                    onClick={() => closePosition(pos)}
                  >
                    Close
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* ── Simulation Log ── */}
      <div className="console-panel">
        <div style={{ fontSize: 13, fontWeight: 700, color: '#4fc3f7', marginBottom: 10, letterSpacing: '.06em' }}>
          SIMULATION LOG (last 20 events)
        </div>
        <div className="console-output">
          {logEvents.length === 0 ? (
            <div style={{ color: 'var(--text2)', fontSize: 11 }}>No events yet.</div>
          ) : (
            logEvents.map((ev: any, i: number) => {
              const isSignal = ev._type === 'signal'
              const isBuy = (ev.side ?? ev.direction ?? '').toUpperCase() === 'BUY'
              const pnl = ev.pnl != null ? ev.pnl : null
              const ts = ev.ts ?? ev.opened_at ?? ev.closed_at ?? ''
              return (
                <div key={i} className="console-log-line">
                  <span style={{ color: '#718096', marginRight: 8, fontSize: 11 }}>{fmtTime(ts)}</span>
                  <span style={{
                    color: isSignal ? '#ecc94b' : '#9f7aea',
                    marginRight: 6, fontSize: 11, fontWeight: 600,
                  }}>
                    [{isSignal ? 'SIG' : 'TRD'}]
                  </span>
                  <span style={{
                    color: isBuy ? '#48bb78' : '#fc8181',
                    marginRight: 6, fontSize: 11, fontWeight: 700,
                  }}>
                    {(ev.side ?? ev.direction ?? '').toUpperCase()}
                  </span>
                  <span style={{ color: '#e2e8f0', marginRight: 6, fontSize: 11 }}>
                    {ev.symbol ?? '—'}
                  </span>
                  {ev.strategy && (
                    <span style={{ color: '#718096', fontSize: 11, marginRight: 6 }}>
                      [{ev.strategy}]
                    </span>
                  )}
                  {pnl != null && (
                    <span style={{ color: pnl >= 0 ? '#48bb78' : '#fc8181', fontSize: 11, fontWeight: 600, marginLeft: 'auto' }}>
                      {pnl >= 0 ? '+' : ''}{fmtRupee(pnl)}
                    </span>
                  )}
                </div>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}
