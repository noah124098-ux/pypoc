import { useMemo } from 'react'
import { useApi } from '../hooks/useSnapshot'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  ZAxis,
  ComposedChart,
  Line,
} from 'recharts'

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────
interface EquityPoint {
  ts: string
  equity: number
}

interface Trade {
  id?: string | number
  entry_time?: string
  exit_time?: string
  entry_ts?: string
  exit_ts?: string
  pnl?: number
  charges?: number
  symbol?: string
  side?: string
}

interface ChartPoint {
  ts: string          // display label
  tsMs: number        // Unix ms — used for nearest-lookup
  equity: number
  drawdown: number    // % from peak (negative number)
}

interface MarkerPoint {
  tsMs: number
  equity: number
  label: string
}

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────
function toMs(s: string | undefined): number {
  if (!s) return NaN
  return new Date(s).getTime()
}

/** Find the chart point whose tsMs is closest to targetMs */
function nearestPoint(points: ChartPoint[], targetMs: number): ChartPoint | null {
  if (!points.length || isNaN(targetMs)) return null
  let best = points[0]
  let bestDiff = Math.abs(points[0].tsMs - targetMs)
  for (const p of points) {
    const d = Math.abs(p.tsMs - targetMs)
    if (d < bestDiff) { bestDiff = d; best = p }
  }
  return best
}

// ──────────────────────────────────────────────────────────────────────────────
// Custom scatter dot — green triangle up (entry) / red triangle down (exit)
// ──────────────────────────────────────────────────────────────────────────────
function EntryDot(props: any) {
  const { cx, cy } = props
  if (cx == null || cy == null) return null
  const size = 7
  // Triangle pointing up
  return (
    <polygon
      points={`${cx},${cy - size} ${cx - size},${cy + size * 0.6} ${cx + size},${cy + size * 0.6}`}
      fill="#48bb78"
      stroke="#1a202c"
      strokeWidth={1}
      opacity={0.9}
    />
  )
}

function ExitDot(props: any) {
  const { cx, cy } = props
  if (cx == null || cy == null) return null
  const size = 7
  // Triangle pointing down
  return (
    <polygon
      points={`${cx},${cy + size} ${cx - size},${cy - size * 0.6} ${cx + size},${cy - size * 0.6}`}
      fill="#fc8181"
      stroke="#1a202c"
      strokeWidth={1}
      opacity={0.9}
    />
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Tooltip for trade markers
// ──────────────────────────────────────────────────────────────────────────────
function MarkerTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  if (!d) return null
  return (
    <div style={{
      background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6,
      padding: '6px 10px', fontSize: 12, color: '#e2e8f0',
    }}>
      <div style={{ fontWeight: 600 }}>{d.label}</div>
      <div>₹{Number(d.equity).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Main equity tooltip
// ──────────────────────────────────────────────────────────────────────────────
function EquityTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const equity = payload.find((p: any) => p.dataKey === 'equity')
  return (
    <div style={{
      background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6,
      padding: '8px 12px', fontSize: 12, color: '#e2e8f0',
    }}>
      <div style={{ color: '#718096', marginBottom: 4 }}>{label}</div>
      {equity && (
        <div>Equity: <strong>₹{Number(equity.value).toLocaleString('en-IN', { maximumFractionDigits: 0 })}</strong></div>
      )}
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Drawdown tooltip
// ──────────────────────────────────────────────────────────────────────────────
function DrawdownTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const dd = payload.find((p: any) => p.dataKey === 'drawdown')
  return (
    <div style={{
      background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6,
      padding: '8px 12px', fontSize: 12, color: '#e2e8f0',
    }}>
      <div style={{ color: '#718096', marginBottom: 4 }}>{label}</div>
      {dd && (
        <div>Drawdown: <strong style={{ color: '#fc8181' }}>{Number(dd.value).toFixed(2)}%</strong></div>
      )}
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Legend item
// ──────────────────────────────────────────────────────────────────────────────
function LegendItem({ color, shape, label }: { color: string; shape: 'line' | 'tri-up' | 'tri-down'; label: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#718096' }}>
      {shape === 'line' && (
        <svg width={18} height={10}>
          <line x1={0} y1={5} x2={18} y2={5} stroke={color} strokeWidth={2} />
        </svg>
      )}
      {shape === 'tri-up' && (
        <svg width={12} height={12}>
          <polygon points="6,1 1,11 11,11" fill={color} />
        </svg>
      )}
      {shape === 'tri-down' && (
        <svg width={12} height={12}>
          <polygon points="6,11 1,1 11,1" fill={color} />
        </svg>
      )}
      <span>{label}</span>
    </div>
  )
}

// ──────────────────────────────────────────────────────────────────────────────
// Component
// ──────────────────────────────────────────────────────────────────────────────
export function PnlTab() {
  const { data: equity, loading: equityLoading } = useApi<EquityPoint[]>('/api/equity?limit=500', 30000)
  const { data: trades, loading: tradesLoading } = useApi<Trade[]>('/api/trades?limit=200', 60000)

  const loading = equityLoading || tradesLoading

  const allTrades = trades ?? []
  const totalPnl = allTrades.reduce((s, t) => s + (t.pnl ?? 0), 0)
  const totalCharges = allTrades.reduce((s, t) => s + (t.charges ?? 0), 0)
  const wins = allTrades.filter(t => (t.pnl ?? 0) > 0)
  const winRate = allTrades.length ? (wins.length / allTrades.length * 100).toFixed(1) : '0'
  const avgWin = wins.length ? wins.reduce((s, t) => s + (t.pnl ?? 0), 0) / wins.length : 0
  const losses = allTrades.filter(t => (t.pnl ?? 0) <= 0)
  const avgLoss = losses.length ? losses.reduce((s, t) => s + (t.pnl ?? 0), 0) / losses.length : 0
  const pf = avgLoss ? Math.abs(avgWin / avgLoss).toFixed(2) : '—'

  // ── Derived chart data ────────────────────────────────────────────────────
  const { chartData, startEquity, entryMarkers, exitMarkers } = useMemo(() => {
    const raw = equity ?? []
    if (raw.length < 2) {
      return { chartData: [], startEquity: 0, entryMarkers: [], exitMarkers: [] }
    }

    // Build chart points with drawdown
    let peak = raw[0].equity
    const points: ChartPoint[] = raw.map(e => {
      const v = e.equity
      if (v > peak) peak = v
      const dd = peak > 0 ? ((v - peak) / peak) * 100 : 0
      return {
        ts: new Date(e.ts).toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }),
        tsMs: new Date(e.ts).getTime(),
        equity: v,
        drawdown: parseFloat(dd.toFixed(2)),
      }
    })

    const startEq = raw[0].equity

    // Map trades to nearest equity points for markers
    const entries: MarkerPoint[] = []
    const exits: MarkerPoint[] = []

    for (const t of (trades ?? [])) {
      const entryMs = toMs(t.entry_time ?? t.entry_ts)
      const exitMs = toMs(t.exit_time ?? t.exit_ts)
      const sym = t.symbol ?? ''
      const side = t.side ?? ''

      if (!isNaN(entryMs)) {
        const pt = nearestPoint(points, entryMs)
        if (pt) entries.push({ tsMs: pt.tsMs, equity: pt.equity, label: `Entry ${sym} ${side}`.trim() })
      }
      if (!isNaN(exitMs)) {
        const pt = nearestPoint(points, exitMs)
        if (pt) exits.push({ tsMs: pt.tsMs, equity: pt.equity, label: `Exit ${sym} ${side}`.trim() })
      }
    }

    return { chartData: points, startEquity: startEq, entryMarkers: entries, exitMarkers: exits }
  }, [equity, trades])

  const hasData = chartData.length > 1
  const currentEquity = hasData ? chartData[chartData.length - 1].equity : 0
  const aboveStart = currentEquity >= startEquity

  // Dynamic gradient IDs
  const gradId = aboveStart ? 'equityGradGreen' : 'equityGradRed'
  const strokeColor = aboveStart ? '#48bb78' : '#fc8181'
  const stopColor = aboveStart ? '#48bb78' : '#fc8181'

  const plus10 = startEquity * 1.1
  const minus10 = startEquity * 0.9

  // ── Loading ───────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="tab-content">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, color: 'var(--text2)', gap: 10 }}>
          <div className="spinner" />
          <span>Loading P&L data…</span>
        </div>
      </div>
    )
  }

  return (
    <div className="tab-content">
      {/* ── KPI row ── */}
      <div className="kpi-row">
        <div className="kpi-card">
          <div className="kpi-label">Net P&L</div>
          <div className={`kpi-value ${totalPnl >= 0 ? 'green' : 'red'}`}>
            ₹{totalPnl.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
          </div>
        </div>
        <div className="kpi-card"><div className="kpi-label">Win Rate</div><div className="kpi-value">{winRate}%</div></div>
        <div className="kpi-card"><div className="kpi-label">Trades</div><div className="kpi-value">{allTrades.length}</div></div>
        <div className="kpi-card"><div className="kpi-label">Profit Factor</div><div className="kpi-value">{pf}</div></div>
        <div className="kpi-card">
          <div className="kpi-label">Charges</div>
          <div className="kpi-value yellow">₹{totalCharges.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
        </div>
      </div>

      {/* ── Equity Curve section ── */}
      <section className="section chart-section">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <h2 style={{ margin: 0 }}>Equity Curve</h2>
          {hasData && (
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
              <LegendItem color="#4299e1" shape="line" label="Equity" />
              <LegendItem color="#48bb78" shape="tri-up" label="Entry" />
              <LegendItem color="#fc8181" shape="tri-down" label="Exit" />
            </div>
          )}
        </div>

        {hasData ? (
          <>
            {/* ── Main equity area + scatter markers ── */}
            <ResponsiveContainer width="100%" height={300}>
              <ComposedChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <defs>
                  <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={stopColor} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={stopColor} stopOpacity={0} />
                  </linearGradient>
                </defs>

                <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
                <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#718096' }} />
                <YAxis
                  tick={{ fontSize: 11, fill: '#718096' }}
                  tickFormatter={(v: number) => '₹' + (v / 1000).toFixed(0) + 'k'}
                  domain={['auto', 'auto']}
                />
                <Tooltip content={<EquityTooltip />} />

                {/* Reference lines */}
                <ReferenceLine
                  y={startEquity}
                  stroke="#718096"
                  strokeDasharray="6 3"
                  label={{ value: 'Start', fill: '#718096', fontSize: 10, position: 'insideTopLeft' }}
                />
                <ReferenceLine
                  y={plus10}
                  stroke="#48bb78"
                  strokeDasharray="3 3"
                  strokeOpacity={0.7}
                  label={{ value: '+10%', fill: '#48bb78', fontSize: 10, position: 'insideTopLeft' }}
                />
                <ReferenceLine
                  y={minus10}
                  stroke="#fc8181"
                  strokeDasharray="3 3"
                  strokeOpacity={0.7}
                  label={{ value: '-10%', fill: '#fc8181', fontSize: 10, position: 'insideTopLeft' }}
                />

                {/* Equity area */}
                <Area
                  type="monotone"
                  dataKey="equity"
                  stroke={strokeColor}
                  fill={`url(#${gradId})`}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, stroke: strokeColor, strokeWidth: 1, fill: '#1a202c' }}
                  isAnimationActive={false}
                />

                {/* Entry scatter markers */}
                {entryMarkers.length > 0 && (
                  <Line
                    dataKey="__unused__"
                    dot={false}
                    activeDot={false}
                    stroke="none"
                    isAnimationActive={false}
                  />
                )}
              </ComposedChart>
            </ResponsiveContainer>

            {/* Entry / exit markers rendered as an overlay ScatterChart sharing the same X range */}
            {(entryMarkers.length > 0 || exitMarkers.length > 0) && (
              <div style={{ marginTop: -300, pointerEvents: 'none', position: 'relative', zIndex: 2 }}>
                <ResponsiveContainer width="100%" height={300}>
                  <ScatterChart margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                    <XAxis
                      dataKey="tsMs"
                      type="number"
                      domain={[chartData[0].tsMs, chartData[chartData.length - 1].tsMs]}
                      tick={false}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      dataKey="equity"
                      type="number"
                      domain={[
                        Math.min(...chartData.map(d => d.equity)) * 0.998,
                        Math.max(...chartData.map(d => d.equity)) * 1.002,
                      ]}
                      tick={false}
                      axisLine={false}
                      tickLine={false}
                    />
                    <ZAxis range={[60, 60]} />
                    <Tooltip content={<MarkerTooltip />} />
                    {entryMarkers.length > 0 && (
                      <Scatter
                        name="Entries"
                        data={entryMarkers}
                        shape={<EntryDot />}
                        isAnimationActive={false}
                      />
                    )}
                    {exitMarkers.length > 0 && (
                      <Scatter
                        name="Exits"
                        data={exitMarkers}
                        shape={<ExitDot />}
                        isAnimationActive={false}
                      />
                    )}
                  </ScatterChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* ── Drawdown subplot ── */}
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 12, color: '#718096', marginBottom: 6, fontWeight: 500 }}>Drawdown from Peak</div>
              <ResponsiveContainer width="100%" height={120}>
                <AreaChart data={chartData} margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
                  <defs>
                    <linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#fc8181" stopOpacity={0.4} />
                      <stop offset="95%" stopColor="#fc8181" stopOpacity={0.05} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
                  <XAxis dataKey="ts" tick={{ fontSize: 10, fill: '#718096' }} />
                  <YAxis
                    tick={{ fontSize: 10, fill: '#718096' }}
                    tickFormatter={(v: number) => v.toFixed(0) + '%'}
                    domain={['auto', 0]}
                  />
                  <Tooltip content={<DrawdownTooltip />} />
                  <ReferenceLine y={0} stroke="#4a5568" strokeDasharray="2 2" />
                  <Area
                    type="monotone"
                    dataKey="drawdown"
                    stroke="#fc8181"
                    fill="url(#ddGrad)"
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </>
        ) : (
          <div className="empty-card">
            <div style={{ fontSize: 48, marginBottom: 12 }}>📊</div>
            <h3>No equity history yet</h3>
            <p>Start the paper agent to begin tracking P&L.</p>
            <div className="info-box" style={{ marginTop: 12, textAlign: 'left' }}>
              <p style={{ marginBottom: 8 }}><strong>1. Add credentials:</strong></p>
              <code>copy .env.example .env</code>
              <p style={{ marginTop: 8, marginBottom: 8 }}><strong>2. Start agent:</strong></p>
              <code>scripts\service_manager.bat start-agent</code>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
