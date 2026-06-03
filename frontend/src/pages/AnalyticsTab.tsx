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
        </>
      )}
    </div>
  )
}
