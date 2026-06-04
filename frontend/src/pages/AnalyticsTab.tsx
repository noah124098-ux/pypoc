import { useMemo } from 'react'
import { useApi } from '../hooks/useSnapshot'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ScatterChart,
  Scatter,
  Legend,
  ReferenceLine,
} from 'recharts'

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────
interface StrategyMetrics {
  n_trades?: number
  total_pnl?: number
  win_rate?: number
  profit_factor?: number
  avg_pnl?: number
  sharpe?: number
  max_drawdown_pct?: number
}

interface MonthlyPnlRow {
  month?: string
  period?: string
  pnl?: number
  n_trades?: number
  win_rate?: number
}

interface ExtendedMetrics {
  n_trades?: number
  total_pnl?: number
  win_rate?: number
  profit_factor?: number
  avg_hold_hours?: number
  avg_win?: number
  avg_loss?: number
  best_trade?: number
  worst_trade?: number
  sharpe?: number
}

interface Trade {
  id?: string | number
  symbol?: string
  strategy?: string
  pnl?: number
  opened_at?: string
  closed_at?: string
  entry_time?: string
  exit_time?: string
  side?: string
  qty?: number
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────
function fmtPnl(v: number): string {
  const sign = v >= 0 ? '+' : ''
  return sign + '₹' + Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: 0 })
}

function fmtPct(v: number | undefined): string {
  if (v == null) return '—'
  return v.toFixed(1) + '%'
}

/** Duration in hours between two ISO strings */
function holdHours(opened: string | undefined, closed: string | undefined): number | null {
  if (!opened || !closed) return null
  const ms = new Date(closed).getTime() - new Date(opened).getTime()
  if (isNaN(ms) || ms < 0) return null
  return ms / 3_600_000
}

/** Bucket a set of P&L values into histogram bins */
function buildHistogram(pnls: number[], numBins = 10): { label: string; count: number; midpoint: number }[] {
  if (!pnls.length) return []
  const min = Math.min(...pnls)
  const max = Math.max(...pnls)
  if (min === max) {
    return [{ label: fmtPnl(min), count: pnls.length, midpoint: min }]
  }
  const binWidth = (max - min) / numBins
  const bins: { label: string; count: number; midpoint: number }[] = []
  for (let i = 0; i < numBins; i++) {
    const lo = min + i * binWidth
    const hi = lo + binWidth
    const midpoint = (lo + hi) / 2
    const count = pnls.filter(p => p >= lo && (i === numBins - 1 ? p <= hi : p < hi)).length
    bins.push({
      label: (lo >= 0 ? '+' : '') + Math.round(lo / 1000) + 'k',
      count,
      midpoint,
    })
  }
  return bins
}

/** Format hold time nicely */
function fmtHoldTime(hours: number): string {
  if (hours < 1) return Math.round(hours * 60) + 'm'
  if (hours < 24) return hours.toFixed(1) + 'h'
  return (hours / 24).toFixed(1) + 'd'
}

// ─────────────────────────────────────────────────────────────────────────────
// Section: Strategy Attribution bar chart
// ─────────────────────────────────────────────────────────────────────────────
function StrategyAttribution({ attribution }: { attribution: Record<string, StrategyMetrics> }) {
  const rows = useMemo(() => {
    return Object.entries(attribution)
      .map(([name, m]) => ({
        name,
        pnl: m.total_pnl ?? 0,
        win_rate: m.win_rate ?? 0,
        n_trades: m.n_trades ?? 0,
        profit_factor: m.profit_factor ?? 0,
      }))
      .sort((a, b) => b.pnl - a.pnl)
  }, [attribution])

  if (!rows.length) return <div className="empty-state">No strategy data yet.</div>

  return (
    <section className="section chart-section">
      <h2>Strategy Attribution</h2>
      <ResponsiveContainer width="100%" height={Math.max(160, rows.length * 48 + 40)}>
        <BarChart data={rows} layout="vertical" margin={{ top: 4, right: 20, bottom: 4, left: 100 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" horizontal={false} />
          <XAxis
            type="number"
            tick={{ fontSize: 11, fill: '#718096' }}
            tickFormatter={(v: number) =>
              (v >= 0 ? '+₹' : '-₹') + Math.abs(v / 1000).toFixed(0) + 'k'
            }
          />
          <YAxis
            type="category"
            dataKey="name"
            tick={{ fontSize: 12, fill: '#e2e8f0' }}
            width={96}
          />
          <Tooltip
            contentStyle={{ background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6, fontSize: 12 }}
            formatter={(v: any) => [fmtPnl(Number(v)), 'P&L']}
          />
          <Bar dataKey="pnl" name="pnl" radius={[0, 4, 4, 0]} isAnimationActive={false}>
            {rows.map((r, i) => (
              <Cell key={i} fill={r.pnl >= 0 ? '#48bb78' : '#fc8181'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* Summary table */}
      <table className="trade-table" style={{ marginTop: 16 }}>
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Trades</th>
            <th>P&amp;L</th>
            <th>Win %</th>
            <th>PF</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className={r.pnl >= 0 ? 'win' : 'loss'}>
              <td>{r.name}</td>
              <td>{r.n_trades}</td>
              <td className={r.pnl >= 0 ? 'green' : 'red'}>{fmtPnl(r.pnl)}</td>
              <td>{fmtPct(r.win_rate)}</td>
              <td>{r.profit_factor > 0 ? r.profit_factor.toFixed(2) : '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Section: Monthly P&L heatmap (CSS grid)
// ─────────────────────────────────────────────────────────────────────────────
function MonthlyHeatmap({ rows }: { rows: MonthlyPnlRow[] }) {
  if (!rows.length) return <div className="empty-state">No monthly P&L data yet.</div>

  // Determine colour scale
  const pnls = rows.map(r => r.pnl ?? 0)
  const maxAbs = Math.max(1, Math.max(...pnls.map(Math.abs)))

  function cellBg(pnl: number): string {
    const t = Math.min(1, Math.abs(pnl) / maxAbs)
    if (pnl >= 0) {
      // green: alpha 0.15 → 0.7
      const alpha = 0.15 + t * 0.55
      return `rgba(72,187,120,${alpha.toFixed(2)})`
    } else {
      const alpha = 0.15 + t * 0.55
      return `rgba(252,129,129,${alpha.toFixed(2)})`
    }
  }

  return (
    <section className="section">
      <h2>Monthly P&amp;L Heatmap</h2>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
          gap: 8,
          marginTop: 12,
        }}
      >
        {rows.map((r, i) => {
          const pnl = r.pnl ?? 0
          const label = r.month ?? r.period ?? `Month ${i + 1}`
          return (
            <div
              key={i}
              title={`Trades: ${r.n_trades ?? '—'} | Win: ${fmtPct(r.win_rate)}`}
              style={{
                background: cellBg(pnl),
                borderRadius: 8,
                padding: '10px 12px',
                textAlign: 'center',
                cursor: 'default',
                border: '1px solid rgba(255,255,255,0.06)',
              }}
            >
              <div style={{ fontSize: 11, color: 'var(--text2)', marginBottom: 4 }}>{label}</div>
              <div
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: pnl >= 0 ? '#48bb78' : '#fc8181',
                }}
              >
                {fmtPnl(pnl)}
              </div>
              <div style={{ fontSize: 10, color: 'var(--text2)', marginTop: 2 }}>
                {r.n_trades ?? 0} trade{(r.n_trades ?? 0) !== 1 ? 's' : ''}
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Section: Win/Loss distribution histogram
// ─────────────────────────────────────────────────────────────────────────────
function PnlHistogram({ trades }: { trades: Trade[] }) {
  const histogram = useMemo(() => {
    const pnls = trades.map(t => t.pnl ?? 0)
    return buildHistogram(pnls, 12)
  }, [trades])

  if (!histogram.length) return <div className="empty-state">No trade data yet.</div>

  return (
    <section className="section chart-section">
      <h2>Win / Loss Distribution</h2>
      <p style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12, marginTop: 0 }}>
        Number of trades per P&amp;L bucket
      </p>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={histogram} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#718096' }} />
          <YAxis tick={{ fontSize: 11, fill: '#718096' }} allowDecimals={false} />
          <Tooltip
            contentStyle={{ background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6, fontSize: 12 }}
            formatter={(v: any) => [v, 'Trades']}
          />
          <Bar dataKey="count" name="Trades" radius={[3, 3, 0, 0]} isAnimationActive={false}>
            {histogram.map((b, i) => (
              <Cell key={i} fill={b.midpoint >= 0 ? '#48bb78' : '#fc8181'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Section: Hold time distribution
// ─────────────────────────────────────────────────────────────────────────────
function HoldTimeDistribution({ trades }: { trades: Trade[] }) {
  const { histogram, avgHours } = useMemo(() => {
    const hours = trades
      .map(t => holdHours(t.opened_at ?? t.entry_time, t.closed_at ?? t.exit_time))
      .filter((h): h is number => h !== null && h < 10000)

    if (!hours.length) return { histogram: [], avgHours: null }

    const avg = hours.reduce((s, h) => s + h, 0) / hours.length

    // Fixed buckets: <1h, 1-4h, 4-8h, 8-24h, 1-3d, >3d
    const buckets = [
      { label: '<1h', lo: 0, hi: 1 },
      { label: '1-4h', lo: 1, hi: 4 },
      { label: '4-8h', lo: 4, hi: 8 },
      { label: '8-24h', lo: 8, hi: 24 },
      { label: '1-3d', lo: 24, hi: 72 },
      { label: '>3d', lo: 72, hi: Infinity },
    ]

    const hist = buckets.map(b => ({
      label: b.label,
      count: hours.filter(h => h >= b.lo && h < b.hi).length,
    }))

    return { histogram: hist, avgHours: avg }
  }, [trades])

  if (!histogram.length) return <div className="empty-state">No hold time data yet.</div>

  return (
    <section className="section chart-section">
      <h2>Hold Time Distribution</h2>
      {avgHours !== null && (
        <p style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12, marginTop: 0 }}>
          Average hold time: <strong style={{ color: 'var(--text)' }}>{fmtHoldTime(avgHours)}</strong>
        </p>
      )}
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={histogram} margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
          <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#718096' }} />
          <YAxis tick={{ fontSize: 11, fill: '#718096' }} allowDecimals={false} />
          <Tooltip
            contentStyle={{ background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6, fontSize: 12 }}
            formatter={(v: any) => [v, 'Trades']}
          />
          <Bar dataKey="count" fill="#4299e1" radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Section: Best / Worst trades table
// ─────────────────────────────────────────────────────────────────────────────
function BestWorstTable({ trades }: { trades: Trade[] }) {
  const { best, worst } = useMemo(() => {
    const sorted = [...trades].sort((a, b) => (b.pnl ?? 0) - (a.pnl ?? 0))
    return {
      best: sorted.slice(0, 5),
      worst: sorted.slice(-5).reverse(),
    }
  }, [trades])

  if (!trades.length) return <div className="empty-state">No trades yet.</div>

  function TradeRow({ t, kind }: { t: Trade; kind: 'win' | 'loss' }) {
    const pnl = t.pnl ?? 0
    const closedAt = t.closed_at ?? t.exit_time ?? ''
    const dateStr = closedAt ? new Date(closedAt).toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }) : '—'
    const holdH = holdHours(t.opened_at ?? t.entry_time, t.closed_at ?? t.exit_time)
    return (
      <tr className={kind}>
        <td>{t.symbol ?? '—'}</td>
        <td>{t.strategy ?? '—'}</td>
        <td style={{ textTransform: 'uppercase', fontSize: 11 }}>{t.side ?? '—'}</td>
        <td>{dateStr}</td>
        <td>{holdH !== null ? fmtHoldTime(holdH) : '—'}</td>
        <td className={pnl >= 0 ? 'green' : 'red'} style={{ fontWeight: 600 }}>
          {fmtPnl(pnl)}
        </td>
      </tr>
    )
  }

  return (
    <section className="section">
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr',
          gap: 24,
        }}
      >
        {/* Top 5 Wins */}
        <div>
          <h2 style={{ color: '#48bb78', marginBottom: 10 }}>Top 5 Wins</h2>
          <table className="trade-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Strategy</th>
                <th>Side</th>
                <th>Date</th>
                <th>Hold</th>
                <th>P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {best.length ? (
                best.map((t, i) => <TradeRow key={i} t={t} kind="win" />)
              ) : (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', color: 'var(--text2)' }}>
                    No winning trades
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Top 5 Losses */}
        <div>
          <h2 style={{ color: '#fc8181', marginBottom: 10 }}>Top 5 Losses</h2>
          <table className="trade-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Strategy</th>
                <th>Side</th>
                <th>Date</th>
                <th>Hold</th>
                <th>P&amp;L</th>
              </tr>
            </thead>
            <tbody>
              {worst.length ? (
                worst.map((t, i) => <TradeRow key={i} t={t} kind="loss" />)
              ) : (
                <tr>
                  <td colSpan={6} style={{ textAlign: 'center', color: 'var(--text2)' }}>
                    No losing trades
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Feature 1: Trade Correlation Matrix
// Heatmap showing strategy pair co-occurrence and P&L correlation
// ─────────────────────────────────────────────────────────────────────────────

/** Assign a stable colour to a strategy name */
const STRATEGY_COLORS: Record<string, string> = {
  trend_breakout: '#4299e1',
  rsi_momentum: '#9f7aea',
  supertrend: '#48bb78',
  supertrend_short: '#ed8936',
  mean_reversion: '#ecc94b',
  momentum: '#fc8181',
}
const FALLBACK_COLORS = ['#4299e1', '#9f7aea', '#48bb78', '#ed8936', '#ecc94b', '#fc8181', '#38b2ac', '#f687b3']

function stratColor(name: string, idx: number): string {
  return STRATEGY_COLORS[name] ?? FALLBACK_COLORS[idx % FALLBACK_COLORS.length]
}

function TradeCorrelationMatrix({ trades }: { trades: Trade[] }) {
  const { strategies, matrix } = useMemo(() => {
    // Collect all unique strategies
    const stratSet = Array.from(new Set(trades.map(t => t.strategy).filter(Boolean))) as string[]
    if (stratSet.length < 2) return { strategies: stratSet, matrix: [] }

    // Group P&L arrays per strategy
    const pnlByStrategy: Record<string, number[]> = {}
    stratSet.forEach(s => { pnlByStrategy[s] = [] })
    trades.forEach(t => {
      if (t.strategy && pnlByStrategy[t.strategy] !== undefined) {
        pnlByStrategy[t.strategy].push(t.pnl ?? 0)
      }
    })

    // Build n×n correlation matrix using Pearson correlation of per-trade PnL
    // For co-occurrence: count trades where both strategies traded on the same day
    const tradeDatesByStrategy: Record<string, Set<string>> = {}
    stratSet.forEach(s => { tradeDatesByStrategy[s] = new Set() })
    trades.forEach(t => {
      if (!t.strategy) return
      const d = (t.closed_at ?? t.exit_time ?? t.opened_at ?? t.entry_time ?? '').split('T')[0]
      if (d) tradeDatesByStrategy[t.strategy].add(d)
    })

    // Correlation matrix: use Pearson between date-bucketed daily PnL series
    // Gather all unique dates across all strategies
    const allDates = Array.from(new Set(trades.map(t => (t.closed_at ?? t.exit_time ?? t.opened_at ?? t.entry_time ?? '').split('T')[0]).filter(Boolean))).sort()

    // Build daily PnL series per strategy
    const dailyPnl: Record<string, number[]> = {}
    stratSet.forEach(s => {
      dailyPnl[s] = allDates.map(d => {
        const dayTrades = trades.filter(t => t.strategy === s && (t.closed_at ?? t.exit_time ?? t.opened_at ?? t.entry_time ?? '').startsWith(d))
        return dayTrades.reduce((acc, t) => acc + (t.pnl ?? 0), 0)
      })
    })

    function pearson(xs: number[], ys: number[]): number {
      const n = xs.length
      if (n < 2) return 0
      const mx = xs.reduce((a, b) => a + b, 0) / n
      const my = ys.reduce((a, b) => a + b, 0) / n
      let num = 0, dx = 0, dy = 0
      for (let i = 0; i < n; i++) {
        const xi = xs[i] - mx, yi = ys[i] - my
        num += xi * yi
        dx += xi * xi
        dy += yi * yi
      }
      const denom = Math.sqrt(dx * dy)
      return denom === 0 ? 0 : num / denom
    }

    const mat = stratSet.map(s1 =>
      stratSet.map(s2 => ({
        s1,
        s2,
        r: s1 === s2 ? 1 : pearson(dailyPnl[s1], dailyPnl[s2]),
      }))
    )

    return { strategies: stratSet, matrix: mat }
  }, [trades])

  if (strategies.length < 2) {
    return (
      <section className="section chart-section">
        <h2>Strategy Correlation Matrix</h2>
        <div className="empty-state">Need at least 2 strategies with trades to show correlation.</div>
      </section>
    )
  }

  function correlationColor(r: number): string {
    // -1 = red, 0 = neutral, +1 = green
    if (r >= 0) {
      const alpha = 0.1 + r * 0.75
      return `rgba(72,187,120,${alpha.toFixed(2)})`
    } else {
      const alpha = 0.1 + Math.abs(r) * 0.75
      return `rgba(252,129,129,${alpha.toFixed(2)})`
    }
  }

  const cellSize = Math.min(80, Math.floor(480 / strategies.length))

  return (
    <section className="section chart-section">
      <h2>Strategy Correlation Matrix</h2>
      <p style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 14, marginTop: 0 }}>
        Pearson correlation of daily P&amp;L between strategy pairs. Green = positive correlation, Red = negative.
      </p>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ borderCollapse: 'separate', borderSpacing: 3 }}>
          <thead>
            <tr>
              <th style={{ width: cellSize, minWidth: 60 }} />
              {strategies.map((s, i) => (
                <th
                  key={s}
                  style={{
                    width: cellSize,
                    minWidth: cellSize,
                    fontSize: 10,
                    fontWeight: 600,
                    color: stratColor(s, i),
                    padding: '4px 2px',
                    textAlign: 'center',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    maxWidth: cellSize,
                  }}
                  title={s}
                >
                  {s.replace(/_/g, ' ')}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row, ri) => (
              <tr key={strategies[ri]}>
                <td
                  style={{
                    fontSize: 10,
                    fontWeight: 600,
                    color: stratColor(strategies[ri], ri),
                    paddingRight: 8,
                    textAlign: 'right',
                    whiteSpace: 'nowrap',
                    maxWidth: cellSize,
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                  title={strategies[ri]}
                >
                  {strategies[ri].replace(/_/g, ' ')}
                </td>
                {row.map((cell, ci) => (
                  <td
                    key={ci}
                    title={`${cell.s1} vs ${cell.s2}: ${cell.r.toFixed(3)}`}
                    style={{
                      width: cellSize,
                      height: cellSize,
                      background: correlationColor(cell.r),
                      borderRadius: 4,
                      textAlign: 'center',
                      fontSize: 12,
                      fontWeight: 600,
                      color: Math.abs(cell.r) > 0.5 ? '#fff' : 'var(--text)',
                      cursor: 'default',
                      border: '1px solid rgba(255,255,255,0.05)',
                    }}
                  >
                    {cell.r.toFixed(2)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {/* Legend */}
      <div style={{ display: 'flex', gap: 16, marginTop: 12, fontSize: 11, color: 'var(--text2)', alignItems: 'center' }}>
        <span>Legend:</span>
        {[[-1, 'Strong negative'], [-0.5, 'Weak negative'], [0, 'Neutral'], [0.5, 'Weak positive'], [1, 'Strong positive']].map(([r, label]) => (
          <span key={label as string} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <span style={{
              width: 14, height: 14, borderRadius: 2,
              background: correlationColor(r as number),
              border: '1px solid rgba(255,255,255,0.1)',
              display: 'inline-block',
            }} />
            {label as string}
          </span>
        ))}
      </div>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Feature 2: Risk/Reward Scatter Plot
// X = entry time (chronological index), Y = P&L
// Color by strategy, size by position size (qty)
// ─────────────────────────────────────────────────────────────────────────────

interface ScatterPoint {
  x: number      // trade index (chronological)
  y: number      // P&L
  r: number      // dot radius (normalised qty)
  label: string  // tooltip label
  strategy: string
  symbol: string
  dateStr: string
  qty: number
}

function RiskRewardScatter({ trades }: { trades: Trade[] }) {
  const { byStrategy, strategies } = useMemo(() => {
    if (!trades.length) return { byStrategy: {}, strategies: [] }

    // Sort trades chronologically
    const sorted = [...trades].sort((a, b) => {
      const ta = new Date(a.opened_at ?? a.entry_time ?? '').getTime()
      const tb = new Date(b.opened_at ?? b.entry_time ?? '').getTime()
      return ta - tb
    })

    const maxQty = Math.max(1, ...sorted.map(t => t.qty ?? 1))
    const stratSet = Array.from(new Set(sorted.map(t => t.strategy ?? 'unknown')))

    const groups: Record<string, ScatterPoint[]> = {}
    stratSet.forEach(s => { groups[s] = [] })

    sorted.forEach((t, idx) => {
      const strat = t.strategy ?? 'unknown'
      const qty = t.qty ?? 1
      const r = 4 + (qty / maxQty) * 12   // radius 4–16 px
      const openedAt = t.opened_at ?? t.entry_time ?? ''
      const dateStr = openedAt ? new Date(openedAt).toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }) : `#${idx + 1}`
      groups[strat].push({
        x: idx + 1,
        y: t.pnl ?? 0,
        r,
        label: `${t.symbol ?? '—'} (${strat})`,
        strategy: strat,
        symbol: t.symbol ?? '—',
        dateStr,
        qty,
      })
    })

    return { byStrategy: groups, strategies: stratSet }
  }, [trades])

  if (!trades.length) {
    return (
      <section className="section chart-section">
        <h2>Risk / Reward Scatter</h2>
        <div className="empty-state">No trades yet to show scatter plot.</div>
      </section>
    )
  }

  const CustomDot = (props: any) => {
    const { cx, cy, payload, fill } = props
    const r = payload?.r ?? 5
    return <circle cx={cx} cy={cy} r={r} fill={fill} fillOpacity={0.75} stroke={fill} strokeWidth={1} />
  }

  const CustomTooltip = ({ active, payload }: any) => {
    if (!active || !payload?.length) return null
    const d = payload[0].payload as ScatterPoint
    return (
      <div style={{ background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6, padding: '8px 12px', fontSize: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{d.symbol} — {d.strategy}</div>
        <div style={{ color: 'var(--text2)' }}>Date: {d.dateStr}</div>
        <div style={{ color: 'var(--text2)' }}>Qty: {d.qty}</div>
        <div style={{ color: (d.y ?? 0) >= 0 ? '#48bb78' : '#fc8181', fontWeight: 600 }}>
          P&amp;L: {fmtPnl(d.y)}
        </div>
      </div>
    )
  }

  return (
    <section className="section chart-section">
      <h2>Risk / Reward Scatter</h2>
      <p style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12, marginTop: 0 }}>
        Each dot = one trade. X = trade sequence, Y = P&amp;L. Dot size proportional to position size (qty). Color by strategy.
      </p>
      <ResponsiveContainer width="100%" height={340}>
        <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 60 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
          <XAxis
            type="number"
            dataKey="x"
            name="Trade #"
            tick={{ fontSize: 11, fill: '#718096' }}
            label={{ value: 'Trade Sequence', position: 'insideBottom', offset: -10, fill: '#718096', fontSize: 11 }}
          />
          <YAxis
            type="number"
            dataKey="y"
            name="P&L"
            tick={{ fontSize: 11, fill: '#718096' }}
            tickFormatter={(v: number) => (v >= 0 ? '+' : '') + Math.round(v / 1000) + 'k'}
          />
          <ReferenceLine y={0} stroke="#4a5568" strokeDasharray="4 4" />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
            formatter={(value) => <span style={{ color: 'var(--text)', fontSize: 12 }}>{value.replace(/_/g, ' ')}</span>}
          />
          {strategies.map((s, i) => (
            <Scatter
              key={s}
              name={s}
              data={byStrategy[s]}
              fill={stratColor(s, i)}
              shape={<CustomDot />}
              isAnimationActive={false}
            />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Feature 3: Equity Attribution Waterfall Chart
// Shows how each strategy contributed to total P&L
// ─────────────────────────────────────────────────────────────────────────────

interface WaterfallBar {
  name: string
  start: number
  end: number
  value: number
  isTotal: boolean
  fill: string
}

function EquityAttributionWaterfall({ attribution }: { attribution: Record<string, StrategyMetrics> }) {
  const bars = useMemo<WaterfallBar[]>(() => {
    const entries = Object.entries(attribution)
      .map(([name, m]) => ({ name, pnl: m.total_pnl ?? 0 }))
      .sort((a, b) => Math.abs(b.pnl) - Math.abs(a.pnl))  // largest contributors first

    if (!entries.length) return []

    const STARTING_EQUITY = 500_000
    let running = STARTING_EQUITY
    const result: WaterfallBar[] = []

    // Starting equity bar
    result.push({
      name: 'Starting',
      start: 0,
      end: STARTING_EQUITY,
      value: STARTING_EQUITY,
      isTotal: true,
      fill: '#4299e1',
    })

    entries.forEach((e) => {
      const start = running
      const end = running + e.pnl
      result.push({
        name: e.name.replace(/_/g, ' '),
        start: Math.min(start, end),
        end: Math.max(start, end),
        value: e.pnl,
        isTotal: false,
        fill: e.pnl >= 0 ? '#48bb78' : '#fc8181',
      })
      running = end
    })

    // Final total
    result.push({
      name: 'Total',
      start: 0,
      end: running,
      value: running,
      isTotal: true,
      fill: running >= STARTING_EQUITY ? '#48bb78' : '#fc8181',
    })

    return result
  }, [attribution])

  if (!bars.length) {
    return (
      <section className="section chart-section">
        <h2>Equity Attribution Waterfall</h2>
        <div className="empty-state">No strategy attribution data yet.</div>
      </section>
    )
  }

  // Custom bar rendering for waterfall effect using recharts BarChart
  // We use a stacked bar: invisible "spacer" + visible "delta"
  const chartData = bars.map(b => ({
    name: b.name,
    spacer: b.isTotal ? 0 : b.start,
    delta: b.isTotal ? b.end : (b.end - b.start),
    fill: b.fill,
    isTotal: b.isTotal,
    value: b.value,
  }))

  const CustomBarLabel = (props: any) => {
    const { x, y, width, height, value, isTotal } = props
    if (!value) return null
    const label = isTotal
      ? '₹' + Math.round(value / 1000) + 'k'
      : (value >= 0 ? '+' : '') + Math.round(value / 1000) + 'k'
    return (
      <text
        x={x + width / 2}
        y={value >= 0 || isTotal ? y - 4 : y + height + 14}
        textAnchor="middle"
        fontSize={10}
        fill={value >= 0 ? '#48bb78' : '#fc8181'}
        fontWeight={600}
      >
        {label}
      </text>
    )
  }

  const CustomTooltip = ({ active, payload }: any) => {
    if (!active || !payload?.length) return null
    const d = payload.find((p: any) => p.dataKey === 'delta')
    if (!d) return null
    const item = d.payload
    return (
      <div style={{ background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6, padding: '8px 12px', fontSize: 12 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>{item.name}</div>
        <div style={{ color: item.isTotal ? 'var(--blue)' : (item.value >= 0 ? '#48bb78' : '#fc8181'), fontWeight: 600 }}>
          {item.isTotal
            ? '₹' + item.value.toLocaleString('en-IN', { maximumFractionDigits: 0 })
            : fmtPnl(item.value)
          }
        </div>
      </div>
    )
  }

  return (
    <section className="section chart-section">
      <h2>Equity Attribution Waterfall</h2>
      <p style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 12, marginTop: 0 }}>
        How each strategy contributed to net equity. Starting equity ₹5L → individual strategy P&amp;L → final equity.
      </p>
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={chartData} margin={{ top: 20, right: 20, bottom: 5, left: 60 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" vertical={false} />
          <XAxis
            dataKey="name"
            tick={{ fontSize: 11, fill: '#718096' }}
          />
          <YAxis
            tick={{ fontSize: 11, fill: '#718096' }}
            tickFormatter={(v: number) => '₹' + Math.round(v / 1000) + 'k'}
          />
          <Tooltip content={<CustomTooltip />} />
          {/* Invisible spacer to lift the bar */}
          <Bar dataKey="spacer" stackId="wf" fill="transparent" isAnimationActive={false} />
          {/* Visible delta bar */}
          <Bar dataKey="delta" stackId="wf" isAnimationActive={false} radius={[3, 3, 0, 0]}>
            {chartData.map((entry, i) => (
              <Cell key={i} fill={entry.fill} />
            ))}
            <CustomBarLabel />
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* Summary row under waterfall */}
      <div style={{ display: 'flex', gap: 12, marginTop: 12, flexWrap: 'wrap' }}>
        {Object.entries(attribution)
          .sort(([, a], [, b]) => (b.total_pnl ?? 0) - (a.total_pnl ?? 0))
          .map(([name, m], i) => {
            const pnl = m.total_pnl ?? 0
            return (
              <div
                key={name}
                style={{
                  background: 'var(--bg3)',
                  borderRadius: 6,
                  padding: '6px 10px',
                  fontSize: 12,
                  borderLeft: `3px solid ${stratColor(name, i)}`,
                }}
              >
                <span style={{ color: 'var(--text2)', marginRight: 6 }}>{name.replace(/_/g, ' ')}</span>
                <span style={{ color: pnl >= 0 ? '#48bb78' : '#fc8181', fontWeight: 600 }}>{fmtPnl(pnl)}</span>
                {m.n_trades != null && (
                  <span style={{ color: 'var(--text2)', fontSize: 10, marginLeft: 6 }}>({m.n_trades}t)</span>
                )}
              </div>
            )
          })
        }
      </div>
    </section>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main AnalyticsTab
// ─────────────────────────────────────────────────────────────────────────────
export function AnalyticsTab() {
  const { data: attribution, loading: attrLoading } =
    useApi<Record<string, StrategyMetrics>>('/api/analytics/strategy-performance?days=365', 60000)
  const { data: monthlyRaw, loading: monthlyLoading } =
    useApi<MonthlyPnlRow[]>('/api/analytics/monthly-pnl?days=730', 60000)
  const { data: extMetrics, loading: metricsLoading } =
    useApi<ExtendedMetrics>('/api/analytics/extended-metrics?days=365', 60000)
  const { data: tradesRaw, loading: tradesLoading } =
    useApi<Trade[]>('/api/trades?limit=500', 60000)

  const loading = attrLoading || monthlyLoading || metricsLoading || tradesLoading

  const attrData = (attribution && !('error' in attribution)) ? attribution as Record<string, StrategyMetrics> : {}
  const monthlyData: MonthlyPnlRow[] = Array.isArray(monthlyRaw) ? monthlyRaw : []
  const trades: Trade[] = Array.isArray(tradesRaw) ? tradesRaw : []
  const metrics = (extMetrics && !('error' in (extMetrics as any))) ? extMetrics as ExtendedMetrics : null

  // KPI values
  const totalPnl = metrics?.total_pnl ?? trades.reduce((s, t) => s + (t.pnl ?? 0), 0)
  const winRate = metrics?.win_rate ?? (trades.length
    ? trades.filter(t => (t.pnl ?? 0) > 0).length / trades.length * 100
    : 0)
  const profitFactor = metrics?.profit_factor ?? 0
  const avgHoldHours = metrics?.avg_hold_hours ?? null
  const sharpe = metrics?.sharpe ?? null

  return (
    <div className="tab-content">
      <h1 className="tab-title">Analytics</h1>

      {loading ? (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: 'var(--text2)', gap: 10 }}>
          <div className="spinner" />
          <span>Loading analytics…</span>
        </div>
      ) : (
        <>
          {/* KPI row */}
          <div className="kpi-row">
            <div className="kpi-card">
              <div className="kpi-label">Net P&amp;L</div>
              <div className={`kpi-value ${totalPnl >= 0 ? 'green' : 'red'}`}>
                {fmtPnl(totalPnl)}
              </div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Win Rate</div>
              <div className="kpi-value">{winRate.toFixed(1)}%</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Trades</div>
              <div className="kpi-value">{trades.length}</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Profit Factor</div>
              <div className="kpi-value">{profitFactor > 0 ? profitFactor.toFixed(2) : '—'}</div>
            </div>
            {avgHoldHours !== null && (
              <div className="kpi-card">
                <div className="kpi-label">Avg Hold</div>
                <div className="kpi-value">{fmtHoldTime(avgHoldHours)}</div>
              </div>
            )}
            {sharpe !== null && (
              <div className="kpi-card">
                <div className="kpi-label">Sharpe</div>
                <div className={`kpi-value ${sharpe >= 1 ? 'green' : sharpe >= 0 ? 'yellow' : 'red'}`}>
                  {sharpe.toFixed(2)}
                </div>
              </div>
            )}
          </div>

          {/* Strategy Attribution */}
          {Object.keys(attrData).length > 0
            ? <StrategyAttribution attribution={attrData} />
            : (
              <section className="section chart-section">
                <h2>Strategy Attribution</h2>
                <div className="empty-state">No strategy attribution data yet. Start the paper agent to collect trades.</div>
              </section>
            )
          }

          {/* Monthly Heatmap */}
          <MonthlyHeatmap rows={monthlyData} />

          {/* P&L Histogram */}
          {trades.length > 0
            ? <PnlHistogram trades={trades} />
            : (
              <section className="section chart-section">
                <h2>Win / Loss Distribution</h2>
                <div className="empty-state">No trades yet to show distribution.</div>
              </section>
            )
          }

          {/* Hold Time Distribution */}
          {trades.length > 0
            ? <HoldTimeDistribution trades={trades} />
            : (
              <section className="section chart-section">
                <h2>Hold Time Distribution</h2>
                <div className="empty-state">No trades yet to show hold times.</div>
              </section>
            )
          }

          {/* Best / Worst Trades */}
          <BestWorstTable trades={trades} />

          {/* ── NEW: Trade Correlation Matrix ─────────────────────── */}
          {trades.length > 0
            ? <TradeCorrelationMatrix trades={trades} />
            : (
              <section className="section chart-section">
                <h2>Strategy Correlation Matrix</h2>
                <div className="empty-state">No trades yet to compute correlation.</div>
              </section>
            )
          }

          {/* ── NEW: Risk / Reward Scatter ────────────────────────── */}
          <RiskRewardScatter trades={trades} />

          {/* ── NEW: Equity Attribution Waterfall ────────────────── */}
          {Object.keys(attrData).length > 0
            ? <EquityAttributionWaterfall attribution={attrData} />
            : (
              <section className="section chart-section">
                <h2>Equity Attribution Waterfall</h2>
                <div className="empty-state">No strategy attribution data yet.</div>
              </section>
            )
          }
        </>
      )}
    </div>
  )
}
