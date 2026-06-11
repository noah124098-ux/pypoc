import { useState, useMemo, type ReactElement } from 'react'
import { useApi } from '../hooks/useSnapshot'

type FilterMode = 'all' | 'accepted' | 'rejected'

/** Wrap text with <mark> spans for matching segments */
function highlight(text: string, query: string): ReactElement {
  if (!query) return <>{text}</>
  const regex = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi')
  const parts = text.split(regex)
  return (
    <>
      {parts.map((part, i) =>
        regex.test(part) ? (
          <mark key={i} className="search-highlight">{part}</mark>
        ) : (
          <span key={i}>{part}</span>
        )
      )}
    </>
  )
}

export function PositionsTab({ snap }: { snap: any }) {
  const { data: signalsRaw, loading: signalsLoading } = useApi<any>('/api/signals?limit=50', 15000)
  const signals: any[] | null = signalsRaw
    ? (Array.isArray(signalsRaw) ? signalsRaw : signalsRaw.data ?? null)
    : null
  const positions = snap?.open_positions ?? []
  const agentRunning = snap?.running ?? false
  const bothEmpty = positions.length === 0 && (!signals || signals.length === 0)

  const [filterText, setFilterText] = useState('')
  const [filterMode, setFilterMode] = useState<FilterMode>('all')

  const filteredSignals = useMemo(() => {
    const raw: any[] = signals ?? []
    return raw.filter(s => {
      const q = filterText.trim().toLowerCase()
      const matchText =
        !q ||
        (s.symbol ?? '').toLowerCase().includes(q) ||
        (s.strategy ?? '').toLowerCase().includes(q)
      const matchMode =
        filterMode === 'all' ||
        (filterMode === 'accepted' && s.accepted) ||
        (filterMode === 'rejected' && !s.accepted)
      return matchText && matchMode
    })
  }, [signals, filterText, filterMode])

  const q = filterText.trim()

  return (
    <div className="tab-content">
      {/* Agent not running banner */}
      {snap && !agentRunning && (
        <div className="banner banner-warn">
          Agent not running — start the agent to begin monitoring and generating signals.
        </div>
      )}

      <section className="section">
        <h2>Open Positions ({positions.length}/5)</h2>
        {positions.length > 0 ? positions.map((p: any) => (
          <div key={p.symbol} className="position-card">
            <div className="pos-header">
              <strong>{p.symbol}</strong>
              <span className="badge">{p.strategy}</span>
              <span className={p.unrealized_pnl >= 0 ? 'pnl green' : 'pnl red'}>
                ₹{(p.unrealized_pnl ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 0 })}
              </span>
            </div>
            <div className="pos-row">
              <span>Entry: ₹{(p.avg_price ?? 0).toFixed(1)}</span>
              <span>LTP: ₹{(p.last_price ?? 0).toFixed(1)}</span>
              <span>SL: ₹{(p.stop_loss ?? 0).toFixed(1)}</span>
              <span>Target: ₹{(p.target ?? 0).toFixed(1)}</span>
            </div>
          </div>
        )) : (
          bothEmpty ? null : (
            <div className="empty-state">
              <div style={{ fontSize: 32, marginBottom: 8 }}>📭</div>
              <strong>No open positions yet</strong>
              <p style={{ marginTop: 4, fontSize: 12 }}>Accepted signals will create positions here</p>
            </div>
          )
        )}
      </section>

      <section className="section">
        <h2>Recent Signals</h2>
        <p className="section-hint">
          Signals are generated when a strategy detects a trading opportunity.
          Accepted signals become trades. Rejected signals show which guardrail blocked them.
        </p>

        {/* Search + filter controls */}
        <div className="signals-toolbar">
          <input
            className="search-input"
            type="text"
            placeholder="Filter by symbol or strategy…"
            value={filterText}
            onChange={e => setFilterText(e.target.value)}
            aria-label="Filter signals"
          />
          <div className="toggle-group" role="group" aria-label="Signal status filter">
            {(['all', 'accepted', 'rejected'] as FilterMode[]).map(mode => (
              <button
                key={mode}
                className={'toggle-btn' + (filterMode === mode ? ' active' : '')}
                onClick={() => setFilterMode(mode)}
              >
                {mode === 'all' ? 'Show all' : mode === 'accepted' ? 'Accepted only' : 'Rejected only'}
              </button>
            ))}
          </div>
        </div>

        {signalsLoading && (!signals || signals.length === 0) ? (
          <div className="empty-state">Loading signals…</div>
        ) : filteredSignals.length > 0 ? (
          <div className="trade-table-wrap">
            <table className="trade-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Strategy</th>
                  <th>Side</th>
                  <th>Status</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {filteredSignals.map((s: any, i: number) => (
                  <tr key={i} className={s.accepted ? 'win' : 'loss'}>
                    <td>{highlight(s.symbol ?? '', q)}</td>
                    <td>{highlight(s.strategy ?? '', q)}</td>
                    <td>{s.side}</td>
                    <td>
                      <span className={s.accepted ? 'badge green' : 'badge red'}>
                        {s.accepted ? 'Accepted' : 'Rejected'}
                      </span>
                    </td>
                    <td className="small">{s.rejection_reason ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (signals ?? []).length > 0 ? (
          <div className="empty-state">No signals match the current filter.</div>
        ) : bothEmpty ? (
          <div className="empty-state combined">
            <p>
              <strong>
                {agentRunning
                  ? 'Agent is active and monitoring markets.'
                  : 'Agent is not currently running.'}
              </strong>
            </p>
            <p>
              Agent is monitoring 50 Nifty stocks. Signals will appear here when conditions are met.
            </p>
            {snap?.regime && (
              <p className="hint">Current regime: <span className="badge">{snap.regime}</span></p>
            )}
          </div>
        ) : (
          <div className="empty-state">No signals yet.</div>
        )}
      </section>
    </div>
  )
}
