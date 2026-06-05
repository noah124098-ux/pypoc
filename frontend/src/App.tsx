import { BrowserRouter, Routes, Route, Navigate, NavLink, useNavigate } from 'react-router-dom'
import { LineChart, Line, ResponsiveContainer } from 'recharts'
import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react'
import { useSnapshot, useApi } from './hooks/useSnapshot'
import { useGuardrailNotifications, useToasts, type Toast } from './hooks/useNotifications'

const LiveTab = lazy(() => import('./pages/LiveTab').then(m => ({ default: m.LiveTab })))
const PnlTab = lazy(() => import('./pages/PnlTab').then(m => ({ default: m.PnlTab })))
const PositionsTab = lazy(() => import('./pages/PositionsTab').then(m => ({ default: m.PositionsTab })))
const RegimeTab = lazy(() => import('./pages/RegimeTab').then(m => ({ default: m.RegimeTab })))
const BacktestTab = lazy(() => import('./pages/BacktestTab').then(m => ({ default: m.BacktestTab })))
const ControlsTab = lazy(() => import('./pages/ControlsTab').then(m => ({ default: m.ControlsTab })))
const ReplayTab = lazy(() => import('./pages/ReplayTab').then(m => ({ default: m.ReplayTab })))
const AiReviewTab = lazy(() => import('./pages/AiReviewTab').then(m => ({ default: m.AiReviewTab })))
const CostsTab = lazy(() => import('./pages/CostsTab').then(m => ({ default: m.CostsTab })))
const PortfolioTab = lazy(() => import('./pages/PortfolioTab').then(m => ({ default: m.PortfolioTab })))
const AngelOneTab = lazy(() => import('./pages/AngelOneTab').then(m => ({ default: m.AngelOneTab })))
const AnalyticsTab = lazy(() => import('./pages/AnalyticsTab').then(m => ({ default: m.AnalyticsTab })))
const StatusPage = lazy(() => import('./pages/StatusPage').then(m => ({ default: m.StatusPage })))
const ApiDocsPage = lazy(() => import('./pages/ApiDocsPage').then(m => ({ default: m.ApiDocsPage })))

const NAV = [
  { path: 'live', label: '🟢 Live' },
  { path: 'pnl', label: '📊 P&L' },
  { path: 'positions', label: '📋 Positions' },
  { path: 'regime', label: '🌡️ Regime' },
  { path: 'backtest', label: '🔬 Backtest' },
  { path: 'replay', label: '🎬 Replay' },
  { path: 'ai-review', label: '🤖 AI Review' },
  { path: 'controls', label: '⚙️ Controls' },
  { path: 'costs', label: '💰 Costs' },
  { path: 'portfolio', label: '🏦 Portfolio' },
  { path: 'angel-one', label: '🔌 Angel One' },
  { path: 'analytics', label: '📈 Analytics' },
  { path: 'status', label: '🔧 System' },
  { path: 'api-docs', label: '📡 API' },
]

/** Returns IST (UTC+5:30) hours, minutes, and day-of-week */
function getISTTime(): { h: number; m: number; dayOfWeek: number } {
  const now = new Date()
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000
  const istMs = utcMs + 5.5 * 3600000
  const ist = new Date(istMs)
  return { h: ist.getHours(), m: ist.getMinutes(), dayOfWeek: ist.getDay() }
}

function formatCountdown(totalMins: number): string {
  const h = Math.floor(totalMins / 60)
  const m = totalMins % 60
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function useMarketCountdown(): string {
  const [label, setLabel] = useState('')

  useEffect(() => {
    function compute() {
      const { h, m, dayOfWeek } = getISTTime()
      const nowMins = h * 60 + m
      const openMins = 9 * 60 + 15   // 09:15 IST
      const closeMins = 15 * 60 + 30 // 15:30 IST
      const isWeekend = dayOfWeek === 0 || dayOfWeek === 6

      if (isWeekend) {
        // Next Monday open
        const daysToMon = dayOfWeek === 6 ? 2 : 1
        const minsToOpen = daysToMon * 24 * 60 - nowMins + openMins
        setLabel(`Opens in ${formatCountdown(minsToOpen)}`)
        return
      }

      if (nowMins < openMins) {
        setLabel(`Opens in ${formatCountdown(openMins - nowMins)}`)
      } else if (nowMins < closeMins) {
        setLabel(`Closes in ${formatCountdown(closeMins - nowMins)}`)
      } else {
        // After close — next trading day (skip weekend)
        const isFriday = dayOfWeek === 5
        const daysToNext = isFriday ? 3 : 1
        const minsToOpen = daysToNext * 24 * 60 - nowMins + openMins
        setLabel(`Opens in ${formatCountdown(minsToOpen)}`)
      }
    }

    compute()
    const id = setInterval(compute, 60000)
    return () => clearInterval(id)
  }, [])

  return label
}

// ── Notification Center ──────────────────────────────────────────────────────
function NotificationCenter() {
  const { items, unreadCount, markAllRead } = useGuardrailNotifications()
  const [open, setOpen] = useState(false)
  const dropRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    function handler(e: MouseEvent) {
      if (dropRef.current && !dropRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  function handleOpen() {
    setOpen(prev => !prev)
  }

  function handleMarkAllRead(e: React.MouseEvent) {
    e.stopPropagation()
    markAllRead()
  }

  function formatTs(ts: number): string {
    if (!ts) return ''
    const d = new Date(ts * 1000)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  return (
    <div className="notif-wrapper" ref={dropRef}>
      <button
        className="notif-bell-btn"
        onClick={handleOpen}
        aria-label="Notifications"
        title="Guardrail rejections"
      >
        <span className="notif-bell-icon" aria-hidden="true">&#128276;</span>
        {unreadCount > 0 && (
          <span className="notif-badge" aria-label={`${unreadCount} unread`}>
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>
      {open && (
        <div className="notif-dropdown" role="dialog" aria-label="Notifications panel">
          <div className="notif-dropdown-header">
            <span className="notif-dropdown-title">Guardrail Rejections</span>
            {unreadCount > 0 && (
              <button className="notif-mark-read-btn" onClick={handleMarkAllRead}>
                Mark all read
              </button>
            )}
          </div>
          <div className="notif-list">
            {items.length === 0 ? (
              <div className="notif-empty">No recent rejections</div>
            ) : (
              items.map(item => (
                <div key={item.id} className="notif-item">
                  <div className="notif-item-main">
                    <span className="notif-symbol">{item.symbol}</span>
                    <span className="notif-strategy">{item.strategy}</span>
                    <span className="notif-rule">blocked by {item.rule}</span>
                  </div>
                  {item.ts > 0 && (
                    <div className="notif-item-time">{formatTs(item.ts)}</div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Toast Container ───────────────────────────────────────────────────────────
function ToastContainer({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: string) => void }) {
  if (toasts.length === 0) return null
  return (
    <div className="toast-container" aria-live="polite">
      {toasts.map(t => (
        <div
          key={t.id}
          className={`toast toast-${t.type}${t.exiting ? ' toast-exit' : ''}`}
          role="alert"
        >
          <span className="toast-msg">{t.message}</span>
          <button className="toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss">
            &times;
          </button>
        </div>
      ))}
    </div>
  )
}

function CopyApiButton() {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(window.location.origin).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }, [])

  return (
    <button
      className="top-bar-btn"
      onClick={handleCopy}
      title="Copy API base URL to clipboard"
    >
      {copied ? 'Copied!' : 'Copy API URL'}
    </button>
  )
}

function TopBar({ snap, connected, onHamburger }: {
  snap: any
  connected: boolean
  onHamburger: () => void
}) {
  const navigate = useNavigate()
  const equity = snap?.equity ?? 500000
  const dayPnl = equity - (snap?.starting_equity_today ?? equity)
  const regime = snap?.current_regime ?? '—'
  const halted = snap?.halted ?? false
  const positions: any[] = snap?.positions ?? []
  const posCount = positions.length
  const marketLabel = useMarketCountdown()

  return (
    <div className="top-bar">
      <button className="hamburger-btn" onClick={onHamburger} aria-label="Toggle navigation">
        &#9776;
      </button>
      <span className="top-bar-logo">NSE Agent</span>
      <span className={`top-bar-status ${connected ? 'live' : 'offline'}`}>
        {connected ? '●LIVE' : '○OFFLINE'}
      </span>
      <span
        className="top-bar-item clickable"
        onClick={() => navigate('/pnl')}
        title="Go to P&L"
      >
        Equity: ₹{equity.toLocaleString('en-IN', { maximumFractionDigits: 0 })}
      </span>
      <span className={`top-bar-item ${dayPnl >= 0 ? 'green' : 'red'}`}>
        {dayPnl >= 0 ? '+' : ''}₹{Math.abs(dayPnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })} today
      </span>
      <span
        className="top-bar-item clickable top-bar-regime"
        onClick={() => navigate('/regime')}
        title="Go to Regime"
      >
        {regime}
      </span>
      <span className="top-bar-item">{posCount} position{posCount !== 1 ? 's' : ''}</span>
      {marketLabel && (
        <span className="top-bar-item top-bar-market" title="NSE market hours (IST)">
          {marketLabel}
        </span>
      )}
      {halted && (
        <span className="top-bar-halt">⚠ HALTED</span>
      )}
      <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
        <CopyApiButton />
        <NotificationCenter />
      </span>
    </div>
  )
}

function Sidebar({ snap, connected, darkMode, onToggleDark, onClose }: { snap: any, connected: boolean, darkMode: boolean, onToggleDark: () => void, onClose?: () => void }) {
  const equity = snap?.equity ?? 0
  const dayPnl = equity - (snap?.starting_equity_today ?? equity)
  const regime = snap?.current_regime ?? '—'
  const halted = snap?.halted ?? false

  const { data: eqHistoryRaw } = useApi<any>('/api/equity?limit=20', 30000)
  const eqHistory: any[] = eqHistoryRaw
    ? (Array.isArray(eqHistoryRaw) ? eqHistoryRaw : eqHistoryRaw.data ?? [])
    : []

  const sparkData = eqHistory.map((pt: any) => ({ v: pt.equity ?? pt.value ?? pt.equity_value ?? 0 }))
  const sparkTrending = sparkData.length >= 2
    ? sparkData[sparkData.length - 1].v >= sparkData[0].v
    : true
  const sparkColor = sparkTrending ? '#48bb78' : '#fc8181'

  const lastUpdatedSecs = snap?.ts
    ? Math.round((Date.now() / 1000) - snap.ts)
    : null

  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <span className="logo">NSE Agent</span>
        <span className={connected ? 'dot green' : 'dot red'} title={connected ? 'Live' : 'Disconnected'} />
      </div>

      <div className="equity-box">
        <div className="eq-label">Equity</div>
        {snap === null ? (
          <div className="eq-value" style={{ color: 'var(--text2)', fontSize: 13, fontWeight: 400 }}>— not running —</div>
        ) : (
          <>
            <div className="eq-value">₹{equity.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
            <div className={dayPnl >= 0 ? 'eq-pnl green' : 'eq-pnl red'}>
              {dayPnl >= 0 ? '+' : ''}₹{Math.abs(dayPnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })} today
            </div>
          </>
        )}
        {sparkData.length >= 2 && (
          <div style={{ width: '100%', height: 40, marginTop: 6 }}>
            <ResponsiveContainer width="100%" height={40}>
              <LineChart data={sparkData} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
                <Line
                  type="monotone"
                  dataKey="v"
                  stroke={sparkColor}
                  strokeWidth={1.5}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
        {lastUpdatedSecs !== null && (
          <div style={{ fontSize: 10, color: 'var(--text2)', marginTop: 4 }}>
            Last updated {lastUpdatedSecs}s ago
          </div>
        )}
      </div>

      {halted && <div className="halt-banner">⛔ HALTED</div>}

      <div className="regime-chip">{regime}</div>

      <nav>
        {NAV.map(n => (
          <NavLink
            key={n.path}
            to={`/${n.path}`}
            className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}
            onClick={onClose}
          >
            {n.label}
          </NavLink>
        ))}
      </nav>

      <div className="sidebar-footer">
        <CopyApiButton />
        <button
          className="top-bar-btn"
          onClick={onToggleDark}
          style={{ width: '100%', textAlign: 'left', marginTop: 2 }}
          title={darkMode ? 'Switch to light mode (current: dark)' : 'Switch to dark mode (current: light)'}
        >
          {darkMode ? '☀ Light mode' : '🌙 Dark mode'}
        </button>
        <a href="/api/docs" target="_blank" rel="noopener noreferrer" className="legacy-link">
          API Docs ↗
        </a>
        <a href="https://github.com/noah124098-ux/pypoc" target="_blank" rel="noopener noreferrer" className="legacy-link">
          GitHub ↗
        </a>
        <div className="kbd-hints">
          <span title="R = Refresh page">R</span>
          <span title="H = Home / Live tab">H</span>
          <span title="B = Backtest tab">B</span>
        </div>
        <div style={{ fontSize: 10, color: 'var(--text2)', marginTop: 6 }}>v3.0 · NSE Agent</div>
      </div>
    </aside>
  )
}

function Layout({ darkMode, onToggleDark }: { darkMode: boolean; onToggleDark: () => void }) {
  const { snap, connected } = useSnapshot()
  const navigate = useNavigate()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const { toasts, addToast, dismiss } = useToasts()

  // Track previous snap values to detect transitions
  const prevSnapRef = useRef<any>(null)

  useEffect(() => {
    const prev = prevSnapRef.current
    if (snap === null) {
      prevSnapRef.current = snap
      return
    }

    // running=false -> running=true: "Agent started"
    const prevRunning = prev?.running ?? null
    const currRunning = snap?.running ?? null
    if (prevRunning === false && currRunning === true) {
      addToast('Agent started', 'success')
    }

    // halted changed to true
    const prevHalted = prev?.halted ?? false
    const currHalted = snap?.halted ?? false
    if (!prevHalted && currHalted) {
      addToast('Agent HALTED', 'error')
    }

    // drawdown_warning became true
    const prevDDWarn = prev?.drawdown_warning ?? false
    const currDDWarn = snap?.drawdown_warning ?? false
    if (!prevDDWarn && currDDWarn) {
      addToast('Drawdown warning threshold reached', 'warning')
    }

    prevSnapRef.current = snap
  }, [snap, addToast])

  const closeSidebar = useCallback(() => setSidebarOpen(false), [])
  const toggleSidebar = useCallback(() => setSidebarOpen(prev => !prev), [])

  // Global keyboard shortcuts (skip when focus is in an input element)
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
      if (e.metaKey || e.ctrlKey || e.altKey) return

      switch (e.key.toUpperCase()) {
        case 'R':
          window.location.reload()
          break
        case 'H':
          navigate('/live')
          break
        case 'B':
          navigate('/backtest')
          break
      }
    }

    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [navigate])

  return (
    <div className="app-wrapper">
      <TopBar snap={snap} connected={connected} onHamburger={toggleSidebar} />
      <div className={`app${sidebarOpen ? ' sidebar-visible' : ''}`}>
        {/* Backdrop: clicking it closes the sidebar on mobile */}
        <div className="sidebar-backdrop" onClick={closeSidebar} />
        <Sidebar snap={snap} connected={connected} darkMode={darkMode} onToggleDark={onToggleDark} onClose={closeSidebar} />
        <main className="main-content" onClick={sidebarOpen ? closeSidebar : undefined}>
          <Suspense fallback={<div className="loading-tab">Loading...</div>}>
            <Routes>
              <Route index element={<Navigate to="/live" replace />} />
              <Route path="live" element={<LiveTab snap={snap} connected={connected} />} />
              <Route path="pnl" element={<PnlTab />} />
              <Route path="positions" element={<PositionsTab snap={snap} />} />
              <Route path="regime" element={<RegimeTab snap={snap} />} />
              <Route path="backtest" element={<BacktestTab />} />
              <Route path="replay" element={<ReplayTab />} />
              <Route path="ai-review" element={<AiReviewTab />} />
              <Route path="controls" element={<ControlsTab snap={snap} />} />
              <Route path="costs" element={<CostsTab />} />
              <Route path="portfolio" element={<PortfolioTab />} />
              <Route path="angel-one" element={<AngelOneTab />} />
              <Route path="analytics" element={<AnalyticsTab />} />
              <Route path="status" element={<StatusPage />} />
              <Route path="api-docs" element={<ApiDocsPage />} />
            </Routes>
          </Suspense>
        </main>
      </div>
      <ToastContainer toasts={toasts} dismiss={dismiss} />
    </div>
  )
}

export default function App() {
  // Persist dark mode to localStorage; default true (dark)
  const [darkMode, setDarkMode] = useState<boolean>(() => {
    try {
      const stored = localStorage.getItem('dark_mode')
      if (stored !== null) return stored === 'true'
    } catch {
      // localStorage unavailable (e.g. private browsing)
    }
    return true
  })

  // Apply / remove light-mode class on <body> when preference changes
  useEffect(() => {
    if (darkMode) {
      document.body.classList.remove('light-mode')
    } else {
      document.body.classList.add('light-mode')
    }
    try {
      localStorage.setItem('dark_mode', String(darkMode))
    } catch {
      // ignore
    }
  }, [darkMode])

  const handleToggleDark = useCallback(() => {
    setDarkMode(prev => !prev)
  }, [])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/*" element={<Layout darkMode={darkMode} onToggleDark={handleToggleDark} />} />
      </Routes>
    </BrowserRouter>
  )
}
