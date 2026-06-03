import { useApi } from '../hooks/useSnapshot'

const REGIME_COLORS: Record<string, string> = {
  TREND: '#4299e1',
  RANGE: '#9f7aea',
  VOLATILE: '#ed8936',
  UNKNOWN: '#718096',
}

// Merge consecutive same-regime records into segments
interface Segment {
  regime: string
  start: Date
  end: Date
  durationMs: number
}

function buildSegments(records: any[]): Segment[] {
  if (!records.length) return []
  // records are newest-first from API — reverse to get chronological order
  const sorted = [...records].reverse()
  const segments: Segment[] = []
  let i = 0
  while (i < sorted.length) {
    const regime = sorted[i].regime
    const start = new Date(sorted[i].ts)
    let j = i + 1
    while (j < sorted.length && sorted[j].regime === regime) j++
    // end = start of next segment OR now if last
    const end = j < sorted.length ? new Date(sorted[j].ts) : new Date()
    segments.push({ regime, start, end, durationMs: end.getTime() - start.getTime() })
    i = j
  }
  return segments
}

function formatDate(d: Date) {
  return d.toLocaleDateString('en-IN', { month: 'short', day: 'numeric', year: '2-digit' })
}

function RegimeGantt({ segments }: { segments: Segment[] }) {
  if (!segments.length) return null

  const totalMs = segments.reduce((s, seg) => s + seg.durationMs, 0) || 1
  const minStart = segments[0].start
  const maxEnd = segments[segments.length - 1].end

  // Group segments into rows to avoid overlap (simple greedy packing — one row is enough
  // since segments are non-overlapping and sequential, just put them all on one row)
  return (
    <div style={{ overflowX: 'auto' }}>
      <div style={{ position: 'relative', minWidth: 500 }}>
        {/* X-axis labels */}
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text2)', marginBottom: 4, paddingRight: 4 }}>
          <span>{formatDate(minStart)}</span>
          <span>{formatDate(maxEnd)}</span>
        </div>

        {/* Single-row Gantt bar */}
        <div
          style={{
            position: 'relative',
            height: 40,
            background: 'var(--bg3)',
            borderRadius: 6,
            overflow: 'hidden',
            border: '1px solid var(--border)',
          }}
        >
          {segments.map((seg, idx) => {
            const leftPct = ((seg.start.getTime() - minStart.getTime()) / totalMs) * 100
            const widthPct = (seg.durationMs / totalMs) * 100
            const color = REGIME_COLORS[seg.regime] ?? '#718096'
            return (
              <div
                key={idx}
                title={`${seg.regime}: ${formatDate(seg.start)} – ${formatDate(seg.end)}`}
                style={{
                  position: 'absolute',
                  left: `${leftPct}%`,
                  width: `${Math.max(widthPct, 0.4)}%`,
                  top: 0,
                  height: '100%',
                  background: color,
                  opacity: 0.85,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  overflow: 'hidden',
                  borderRight: '1px solid var(--bg)',
                  transition: 'opacity .15s',
                  cursor: 'default',
                }}
                onMouseEnter={e => (e.currentTarget.style.opacity = '1')}
                onMouseLeave={e => (e.currentTarget.style.opacity = '0.85')}
              >
                {widthPct > 6 && (
                  <span style={{ color: '#fff', fontSize: 10, fontWeight: 600, pointerEvents: 'none', whiteSpace: 'nowrap' }}>
                    {seg.regime}
                  </span>
                )}
              </div>
            )
          })}
        </div>

        {/* Legend */}
        <div style={{ display: 'flex', gap: 16, marginTop: 10, flexWrap: 'wrap' }}>
          {Object.entries(REGIME_COLORS).filter(([k]) => k !== 'UNKNOWN').map(([r, c]) => (
            <div key={r} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
              <div style={{ width: 12, height: 12, borderRadius: 3, background: c, flexShrink: 0 }} />
              <span style={{ fontSize: 11, color: 'var(--text2)' }}>{r}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export function RegimeTab({ snap }: { snap: any }) {
  const { data: history } = useApi<any[]>('/api/regime?limit=50', 60000)
  const { data: config } = useApi('/api/config', 300000)
  const records = history ?? []
  const dist: Record<string, number> = {}
  records.forEach((r: any) => { dist[r.regime] = (dist[r.regime] ?? 0) + 1 })
  const total = records.length || 1

  const currentRegime = snap?.current_regime ?? null
  const segments = buildSegments(records)

  return (
    <div className="tab-content">

      {/* Live Regime Badge */}
      <section className="section">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <h2 style={{ margin: 0 }}>Live Regime</h2>
          {currentRegime ? (
            <span
              style={{
                background: REGIME_COLORS[currentRegime] ?? '#718096',
                color: '#fff',
                fontSize: 13,
                fontWeight: 700,
                padding: '4px 14px',
                borderRadius: 20,
                letterSpacing: '.04em',
                boxShadow: `0 0 10px ${REGIME_COLORS[currentRegime] ?? '#718096'}55`,
              }}
            >
              {currentRegime}
            </span>
          ) : (
            <span className="badge">Unknown</span>
          )}
        </div>
      </section>

      {/* Gantt Timeline */}
      <section className="section">
        <h2>Regime Timeline</h2>
        {segments.length === 0 ? (
          <p className="empty-msg" style={{ color: 'var(--text2)', fontSize: 13 }}>
            Timeline will populate once the agent has regime history.
          </p>
        ) : (
          <div className="chart-section">
            <RegimeGantt segments={segments} />
          </div>
        )}
      </section>

      {/* Regime Distribution */}
      {records.length > 0 && (
        <section className="section">
          <h2>Regime Distribution</h2>
          <div className="regime-bars">
            {Object.entries(dist).map(([r, c]) => (
              <div key={r} className="regime-bar-row">
                <span className="regime-label" style={{ color: REGIME_COLORS[r] ?? '#718096' }}>{r}</span>
                <div className="regime-bar-bg">
                  <div className="regime-bar-fill" style={{ width: `${(c / total) * 100}%`, background: REGIME_COLORS[r] ?? '#718096' }} />
                </div>
                <span className="regime-pct">{((c / total) * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Regime Classification Rules */}
      <section className="section">
        <h2>Regime Classification Rules</h2>
        <table className="trade-table">
          <thead>
            <tr><th>Regime</th><th>Condition</th><th>Description</th></tr>
          </thead>
          <tbody>
            <tr>
              <td><span className="badge" style={{ background: REGIME_COLORS.TREND, color: '#fff' }}>TREND</span></td>
              <td>ADX &gt; {(config as any)?.regime?.adx_trend_threshold ?? 20}</td>
              <td>Strong directional movement — trend-following strategies active</td>
            </tr>
            <tr>
              <td><span className="badge" style={{ background: REGIME_COLORS.RANGE, color: '#fff' }}>RANGE</span></td>
              <td>BB width &lt; {(config as any)?.regime?.bb_width_range_threshold ?? 0.06}</td>
              <td>Low volatility, mean-reversion conditions — range strategies active</td>
            </tr>
            <tr>
              <td><span className="badge" style={{ background: REGIME_COLORS.VOLATILE, color: '#fff' }}>VOLATILE</span></td>
              <td>VIX &gt; {(config as any)?.regime?.vix_volatile_threshold ?? 20}</td>
              <td>High fear/uncertainty — reduced position sizes, tighter stops</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  )
}
