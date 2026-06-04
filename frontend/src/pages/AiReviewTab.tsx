import { useState, useEffect, useRef } from 'react'

const NIFTY_50_SYMBOLS = [
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

const TOP_NIFTY_SYMBOLS = [
  'RELIANCE', 'TCS', 'HDFCBANK', 'INFY', 'ICICIBANK',
  'HCLTECH', 'WIPRO', 'BAJFINANCE', 'LTIM', 'AXISBANK',
]

const QUESTION_PRESETS = [
  'View on today',
  'Entry signal quality',
  'Risk assessment',
  'Key support and resistance levels',
  'Momentum analysis',
]

interface Suggestion {
  strategy: string
  parameter: string
  current_value: string | number
  suggested_value: string | number
  rationale: string
}

interface EodReview {
  available?: boolean
  timestamp?: string
  summary?: string
  suggestions?: Suggestion[]
  flags?: string[]
}

interface SentimentResult {
  symbol: string
  score: number
  confidence: number
  summary: string
}

function nextEodReviewLabel(): string {
  // Compute "today at 15:35 IST" or "tomorrow at 15:35 IST"
  const nowUtc = new Date()
  const nowIst = new Date(nowUtc.getTime() + 5.5 * 60 * 60 * 1000)
  const reviewHour = 15
  const reviewMin = 35
  const istReview = new Date(nowIst)
  istReview.setHours(reviewHour, reviewMin, 0, 0)

  const isWeekday = nowIst.getDay() >= 1 && nowIst.getDay() <= 5
  if (!isWeekday) return 'next weekday at 15:35 IST'

  if (nowIst < istReview) return 'today at 15:35 IST'
  return 'tomorrow at 15:35 IST'
}

function formatTs(ts?: string): string {
  if (!ts) return ''
  try {
    return new Date(ts).toLocaleString('en-IN', {
      timeZone: 'UTC',
      year: 'numeric', month: 'short', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    }) + ' UTC'
  } catch {
    return ts
  }
}

export function AiReviewTab() {
  const [review, setReview] = useState<EodReview | null>(null)
  const [loading, setLoading] = useState(true)
  const [applyMsg, setApplyMsg] = useState<Record<number, string>>({})
  const [appliedIdx, setAppliedIdx] = useState<Set<number>>(new Set())

  // News sentiment state
  const [selectedSymbols, setSelectedSymbols] = useState<string[]>(['RELIANCE', 'INFY'])
  const [headlines, setHeadlines] = useState<Record<string, string>>({})
  const [sentimentResults, setSentimentResults] = useState<SentimentResult[]>([])
  const [sentimentLoading, setSentimentLoading] = useState(false)
  const [sentimentMsg, setSentimentMsg] = useState('')

  useEffect(() => {
    fetch('http://localhost:8502/api/eod-review')
      .then(r => r.json())
      .then(d => { setReview(d); setLoading(false) })
      .catch(() => { setReview(null); setLoading(false) })
  }, [])

  async function applyParam(idx: number, param: string, value: string | number) {
    try {
      const r = await fetch('http://localhost:8502/api/command/update-risk-param', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ param, value }),
      })
      const d = await r.json()
      if (d.queued || d.ok) {
        setApplyMsg(prev => ({ ...prev, [idx]: `Applied: ${param} = ${value}` }))
        setAppliedIdx(prev => new Set([...prev, idx]))
      } else {
        setApplyMsg(prev => ({ ...prev, [idx]: 'Failed to queue' }))
      }
    } catch {
      setApplyMsg(prev => ({ ...prev, [idx]: 'API not reachable' }))
    }
    setTimeout(() => setApplyMsg(prev => { const n = { ...prev }; delete n[idx]; return n }), 6000)
  }

  async function scoreSentiment() {
    setSentimentLoading(true)
    setSentimentMsg('')
    setSentimentResults([])

    const payload: Record<string, string[]> = {}
    for (const sym of selectedSymbols) {
      const raw = headlines[sym] ?? ''
      const lines = raw.split('\n').map(l => l.trim()).filter(Boolean)
      if (lines.length > 0) payload[sym] = lines
    }

    if (Object.keys(payload).length === 0) {
      setSentimentMsg('Enter at least one headline per selected symbol.')
      setSentimentLoading(false)
      return
    }

    try {
      const r = await fetch('http://localhost:8502/api/news/score', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbols: payload }),
      })
      const d = await r.json()
      if (Array.isArray(d)) {
        setSentimentResults(d)
      } else if (d.results) {
        setSentimentResults(d.results)
      } else {
        setSentimentMsg('Unexpected response format from API.')
      }
    } catch {
      setSentimentMsg('API not reachable — ensure the FastAPI backend is running.')
    }
    setSentimentLoading(false)
  }

  function toggleSymbol(sym: string) {
    setSelectedSymbols(prev =>
      prev.includes(sym) ? prev.filter(s => s !== sym) : [...prev, sym]
    )
  }

  // Commentary state
  const [commentarySymbol, setCommentarySymbol] = useState('RELIANCE')
  const [commentaryQuestion, setCommentaryQuestion] = useState('View on today')
  const [commentaryContext, setCommentaryContext] = useState('')
  const [commentaryLoading, setCommentaryLoading] = useState(false)
  const [commentaryResult, setCommentaryResult] = useState<{ commentary: string; model: string } | null>(null)
  const [commentaryError, setCommentaryError] = useState('')
  const [displayedText, setDisplayedText] = useState('')
  const typingRef = useRef<ReturnType<typeof setInterval> | null>(null)

  function startTypingAnimation(text: string) {
    if (typingRef.current) clearInterval(typingRef.current)
    setDisplayedText('')
    let i = 0
    typingRef.current = setInterval(() => {
      if (i < text.length) {
        setDisplayedText(text.slice(0, i + 1))
        i++
      } else {
        if (typingRef.current) clearInterval(typingRef.current)
      }
    }, 12)
  }

  async function askClaude() {
    setCommentaryLoading(true)
    setCommentaryError('')
    setCommentaryResult(null)
    setDisplayedText('')
    try {
      const r = await fetch('http://localhost:8502/api/ai/commentary', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: commentarySymbol,
          question: commentaryQuestion,
          context: commentaryContext,
        }),
      })
      const d = await r.json()
      if (d.requires_key || d.error?.includes('ANTHROPIC_API_KEY')) {
        setCommentaryError('requires_key')
      } else if (d.error) {
        setCommentaryError(d.error)
      } else {
        setCommentaryResult(d)
        startTypingAnimation(d.commentary)
      }
    } catch {
      setCommentaryError('API not reachable — ensure the FastAPI backend is running.')
    }
    setCommentaryLoading(false)
  }

  // review is unavailable when API returns {available: false} or null/network error
  const reviewUnavailable = !review || review.available === false

  const suggestions = review?.suggestions ?? []
  const nextReview = nextEodReviewLabel()

  return (
    <div className="tab-content">

      {/* Auto-review countdown */}
      <div className="status-banner green" style={{ marginBottom: 16 }}>
        Next EOD review: {nextReview}
      </div>

      {/* Section 1: Last EOD Review */}
      <section className="section">
        <h2>Last EOD Review</h2>

        {loading && (
          <div className="empty-state">
            <div className="spinner" style={{ margin: "0 auto 12px" }} />
            Loading last review…
          </div>
        )}

        {!loading && reviewUnavailable && (
          <div
            className="info-card"
            style={{
              maxWidth: 540,
              padding: "20px 24px",
              borderLeft: "4px solid var(--blue)",
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 15, marginBottom: 10 }}>
              No EOD review available yet
            </div>
            <p style={{ color: "var(--text2)", marginBottom: 10, lineHeight: 1.6 }}>
              EOD review runs automatically at <strong>15:35 IST</strong> after each trading day.
              Requires <code style={{ background: "var(--bg3)", padding: "1px 5px", borderRadius: 4, fontSize: 12 }}>ANTHROPIC_API_KEY</code> in
              {" "}<code style={{ background: "var(--bg3)", padding: "1px 5px", borderRadius: 4, fontSize: 12 }}>.env</code> to generate insights.
            </p>
            <p style={{ color: "var(--text2)", marginBottom: 0, fontSize: 13 }}>
              First review will appear after the paper agent's first trading session.
            </p>
          </div>
        )}

        {!loading && !reviewUnavailable && review && (
          <>
            {review.timestamp && (
              <div className="small" style={{ marginBottom: 8, color: 'var(--text2)' }}>
                Reviewed at: {formatTs(review.timestamp)}
              </div>
            )}

            {review.summary && (
              <div className="info-box" style={{ marginBottom: 16 }}>
                <div style={{ fontWeight: 600, marginBottom: 6, fontSize: 13 }}>Summary</div>
                <p style={{ lineHeight: 1.6, fontSize: 13 }}>{review.summary}</p>
              </div>
            )}

            {review.flags && review.flags.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                {review.flags.map((flag, i) => (
                  <div
                    key={i}
                    className="status-banner red"
                    style={{ marginBottom: 6 }}
                  >
                    {flag}
                  </div>
                ))}
              </div>
            )}

            {/* Parameter suggestions table */}
            {suggestions.length > 0 ? (
              <div>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 10 }}>
                  Parameter Suggestions ({suggestions.length})
                </div>
                <table className="trade-table">
                  <thead>
                    <tr>
                      <th>Strategy</th>
                      <th>Parameter</th>
                      <th>Change</th>
                      <th>Rationale</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {suggestions.map((s, idx) => (
                      <tr key={idx}>
                        <td>{s.strategy}</td>
                        <td><code style={{ background: 'var(--bg3)', padding: '2px 6px', borderRadius: 4, fontSize: 12 }}>{s.parameter}</code></td>
                        <td>
                          <span className="red">{String(s.current_value)}</span>
                          <span style={{ color: 'var(--text2)', margin: '0 6px' }}>→</span>
                          <span className="green">{String(s.suggested_value)}</span>
                        </td>
                        <td style={{ color: 'var(--text2)', fontSize: 12, maxWidth: 260 }}>{s.rationale}</td>
                        <td>
                          {appliedIdx.has(idx) ? (
                            <span className="badge green">Applied</span>
                          ) : (
                            <button
                              className="btn-success"
                              style={{ fontSize: 12, padding: '4px 10px' }}
                              onClick={() => applyParam(idx, s.parameter, s.suggested_value)}
                            >
                              Apply
                            </button>
                          )}
                          {applyMsg[idx] && (
                            <div style={{ fontSize: 11, color: 'var(--green)', marginTop: 2 }}>
                              {applyMsg[idx]}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="small" style={{ color: 'var(--text2)' }}>
                No parameter suggestions in the last review.
              </div>
            )}
          </>
        )}
      </section>

      <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '8px 0 24px' }} />

      {/* Section 2: Live Claude Commentary */}
      <section className="section">
        <h2>Live Market Commentary</h2>
        <div className="small" style={{ marginBottom: 16 }}>
          Ask Claude Haiku for instant market analysis on any Nifty 50 symbol.
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          {/* Symbol selector */}
          <div>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>
              Symbol
            </label>
            <select
              value={commentarySymbol}
              onChange={e => setCommentarySymbol(e.target.value)}
              style={{
                width: '100%',
                background: 'var(--bg3)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                color: 'var(--text)',
                fontSize: 13,
                padding: '7px 10px',
                cursor: 'pointer',
              }}
            >
              {NIFTY_50_SYMBOLS.map(s => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
          </div>

          {/* Question input with preset buttons */}
          <div>
            <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>
              Question
            </label>
            <input
              type="text"
              value={commentaryQuestion}
              onChange={e => setCommentaryQuestion(e.target.value)}
              placeholder="What would you like to know?"
              style={{
                width: '100%',
                background: 'var(--bg3)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                color: 'var(--text)',
                fontSize: 13,
                padding: '7px 10px',
                boxSizing: 'border-box',
              }}
            />
          </div>
        </div>

        {/* Preset question chips */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
          {QUESTION_PRESETS.map(q => (
            <button
              key={q}
              onClick={() => setCommentaryQuestion(q)}
              style={{
                padding: '3px 10px',
                borderRadius: 12,
                fontSize: 11,
                cursor: 'pointer',
                border: commentaryQuestion === q
                  ? '1px solid var(--blue)'
                  : '1px solid var(--border)',
                background: commentaryQuestion === q
                  ? 'rgba(66,153,225,.15)'
                  : 'var(--bg3)',
                color: commentaryQuestion === q ? 'var(--blue)' : 'var(--text2)',
              }}
            >
              {q}
            </button>
          ))}
        </div>

        {/* Optional context */}
        <div style={{ marginBottom: 14 }}>
          <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>
            Additional context (optional)
          </label>
          <textarea
            rows={2}
            value={commentaryContext}
            onChange={e => setCommentaryContext(e.target.value)}
            placeholder="e.g. RSI is 72, broke out above 200 DMA today, heavy volume"
            style={{
              width: '100%',
              background: 'var(--bg3)',
              border: '1px solid var(--border)',
              borderRadius: 6,
              color: 'var(--text)',
              fontSize: 12,
              padding: '8px 10px',
              resize: 'vertical',
              fontFamily: 'inherit',
              lineHeight: 1.5,
              boxSizing: 'border-box',
            }}
          />
        </div>

        <button
          className="btn-success"
          onClick={askClaude}
          disabled={commentaryLoading}
          style={{ marginBottom: 16 }}
        >
          {commentaryLoading ? 'Asking Claude...' : 'Ask Claude'}
        </button>

        {/* Error states */}
        {commentaryError === 'requires_key' && (
          <div
            className="info-card"
            style={{
              maxWidth: 480,
              padding: '16px 20px',
              borderLeft: '4px solid var(--yellow)',
              marginBottom: 12,
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 6 }}>
              Requires ANTHROPIC_API_KEY
            </div>
            <p style={{ color: 'var(--text2)', fontSize: 13, margin: 0 }}>
              Add <code style={{ background: 'var(--bg3)', padding: '1px 5px', borderRadius: 4 }}>ANTHROPIC_API_KEY=sk-ant-...</code> to your <code style={{ background: 'var(--bg3)', padding: '1px 5px', borderRadius: 4 }}>.env</code> file and restart the API server.
            </p>
          </div>
        )}
        {commentaryError && commentaryError !== 'requires_key' && (
          <div className="status-banner red" style={{ marginBottom: 12 }}>
            {commentaryError}
          </div>
        )}

        {/* Commentary response box with typing animation */}
        {commentaryResult && displayedText && (
          <div
            style={{
              background: 'rgba(66,153,225,.08)',
              border: '1px solid rgba(66,153,225,.35)',
              borderLeft: '4px solid var(--blue)',
              borderRadius: 8,
              padding: '16px 20px',
              marginBottom: 12,
            }}
          >
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 10,
              fontSize: 12,
              color: 'var(--blue)',
              fontWeight: 600,
            }}>
              <span>Claude Haiku</span>
              <span style={{ color: 'var(--text2)', fontWeight: 400 }}>
                — {commentarySymbol} — {commentaryQuestion}
              </span>
            </div>
            <p style={{
              lineHeight: 1.7,
              fontSize: 14,
              color: 'var(--text)',
              margin: 0,
              whiteSpace: 'pre-wrap',
            }}>
              {displayedText}
              {displayedText.length < (commentaryResult.commentary.length) && (
                <span style={{ opacity: 0.6, animation: 'none' }}>|</span>
              )}
            </p>
          </div>
        )}
      </section>

      <hr style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '8px 0 24px' }} />

      {/* Section 3: News Sentiment Scorer */}
      <section className="section">
        <h2>News Sentiment Scorer</h2>
        <div className="small" style={{ marginBottom: 12 }}>
          Select symbols, enter headlines (one per line), then click Score Sentiment.
        </div>

        {/* Symbol multi-select */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text2)', marginBottom: 6 }}>Select symbols</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {TOP_NIFTY_SYMBOLS.map(sym => (
              <button
                key={sym}
                onClick={() => toggleSymbol(sym)}
                style={{
                  padding: '4px 10px',
                  borderRadius: 16,
                  fontSize: 12,
                  cursor: 'pointer',
                  border: selectedSymbols.includes(sym)
                    ? '1px solid var(--blue)'
                    : '1px solid var(--border)',
                  background: selectedSymbols.includes(sym)
                    ? 'rgba(66,153,225,.15)'
                    : 'var(--bg3)',
                  color: selectedSymbols.includes(sym) ? 'var(--blue)' : 'var(--text2)',
                }}
              >
                {sym}
              </button>
            ))}
          </div>
        </div>

        {/* Headline text areas */}
        {selectedSymbols.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12, marginBottom: 14 }}>
            {selectedSymbols.map(sym => (
              <div key={sym}>
                <label style={{ fontSize: 12, color: 'var(--text2)', display: 'block', marginBottom: 4 }}>
                  Headlines for {sym} (one per line)
                </label>
                <textarea
                  rows={4}
                  value={headlines[sym] ?? ''}
                  onChange={e => setHeadlines(prev => ({ ...prev, [sym]: e.target.value }))}
                  placeholder={`e.g. ${sym} Q4 profit beats estimates by 12%`}
                  style={{
                    width: '100%',
                    background: 'var(--bg3)',
                    border: '1px solid var(--border)',
                    borderRadius: 6,
                    color: 'var(--text)',
                    fontSize: 12,
                    padding: '8px 10px',
                    resize: 'vertical',
                    fontFamily: 'inherit',
                    lineHeight: 1.5,
                  }}
                />
              </div>
            ))}
          </div>
        )}

        {selectedSymbols.length === 0 && (
          <div className="empty-state" style={{ marginBottom: 14 }}>
            Select at least one symbol above to enter headlines.
          </div>
        )}

        <button
          className="btn-success"
          onClick={scoreSentiment}
          disabled={sentimentLoading || selectedSymbols.length === 0}
          style={{ marginBottom: 14 }}
        >
          {sentimentLoading ? 'Scoring...' : 'Score Sentiment'}
        </button>

        {sentimentMsg && (
          <div className="status-banner red" style={{ marginBottom: 12 }}>
            {sentimentMsg}
          </div>
        )}

        {/* Sentiment results */}
        {sentimentResults.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
            {sentimentResults.map((r, i) => {
              const scoreColor = r.score > 0.2 ? 'var(--green)' : r.score < -0.2 ? 'var(--red)' : 'var(--yellow)'
              const scoreDot = r.score > 0.2 ? '●' : r.score < -0.2 ? '●' : '●'
              return (
                <div key={i} className="kpi-card" style={{ borderColor: scoreColor }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                    <span style={{ color: scoreColor, fontSize: 10 }}>{scoreDot}</span>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>{r.symbol}</span>
                  </div>
                  <div className="kpi-value" style={{ color: scoreColor, fontSize: 24 }}>
                    {r.score >= 0 ? '+' : ''}{r.score.toFixed(2)}
                  </div>
                  <div className="kpi-sub">confidence {(r.confidence * 100).toFixed(0)}%</div>
                  {r.summary && (
                    <div style={{ fontSize: 11, color: 'var(--text2)', marginTop: 6, lineHeight: 1.4 }}>
                      {r.summary}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>

    </div>
  )
}
