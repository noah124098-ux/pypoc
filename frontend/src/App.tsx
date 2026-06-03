import { BrowserRouter, Routes, Route, Navigate, NavLink, useNavigate } from 'react-router-dom'
import { LineChart, Line, ResponsiveContainer } from 'recharts'
import { useState, useEffect, useCallback } from 'react'
import { LiveTab } from './pages/LiveTab'
import { PnlTab } from './pages/PnlTab'
import { PositionsTab } from './pages/PositionsTab'
import { RegimeTab } from './pages/RegimeTab'
import { BacktestTab } from './pages/BacktestTab'
import { ControlsTab } from './pages/ControlsTab'
import { ReplayTab } from './pages/ReplayTab'
import { AiReviewTab } from './pages/AiReviewTab'
import { CostsTab } from './pages/CostsTab'
import { PortfolioTab } from './pages/PortfolioTab'
import { AngelOneTab } from './pages/AngelOneTab'
import { AnalyticsTab } from './pages/AnalyticsTab'
import { useSnapshot, useApi } from './hooks/useSnapshot'

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

function TopBar({ snap, connected, darkMode, onToggleDark, onHamburger }: {
  snap: any
  connected: boolean
  darkMode: boolean
  onToggleDark: () => void
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
      <span className="top-bar-logo">pypoc</span>
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
        <button
          className="top-bar-btn"
          onClick={onToggleDark}
          title={darkMode ? 'Switch to light mode (current: dark)' : 'Switch to dark mode (current: light)'}
        >
          {darkMode ? '☀ Light' : '🌙 Dark'}
        </button>
      </span>
    </div>
  )
}

function Sidebar({ snap, connected, onClose }: { snap: any, connected: boolean, onClose?: () => void }) {
  const equity = snap?.equity ?? 0
  const dayPnl = equity - (snap?.starting_equity_today ?? equity)
  const regime = snap?.current_regime ?? '—'
  const halted = snap?.halted ?? false

  const { data: eqHistory } = useApi<any[]>('/api/equity?limit=20', 30000)

  const sparkData = (eqHistory ?? []).map((pt: any) => ({ v: pt.equity ?? pt.value ?? pt.equity_value ?? 0 }))
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
        <span className="logo">pypoc</span>
        <span className={connected ? 'dot green' : 'dot red'} title={connected ? 'Live' : 'Disconnected'} />
      </div>

      <div className="equity-box">
        <div className="eq-label">Equity</div>
        <div className="eq-value">₹{equity.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div>
        <div className={dayPnl >= 0 ? 'eq-pnl green' : 'eq-pnl red'}>
          {dayPnl >= 0 ? '+' : ''}₹{Math.abs(dayPnl).toLocaleString('en-IN', { maximumFractionDigits: 0 })} today
        </div>
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
        <a href="http://localhost:8501" target="_blank" rel="noopener noreferrer" className="legacy-link">
          Open Streamlit ↗
        </a>
        <a href="/docs" target="_blank" rel="noopener noreferrer" className="legacy-link">
          API Docs ↗
        </a>
        <div className="kbd-hints">
          <span title="R = Refresh page">R</span>
          <span title="H = Home / Live tab">H</span>
          <span title="B = Backtest tab">B</span>
        </div>
      </div>
    </aside>
  )
}

function Layout({ darkMode, onToggleDark }: { darkMode: boolean; onToggleDark: () => void }) {
  const { snap, connected } = useSnapshot()
  const navigate = useNavigate()
  const [sidebarOpen, setSidebarOpen] = useState(false)

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
      <TopBar snap={snap} connected={connected} darkMode={darkMode} onToggleDark={onToggleDark} onHamburger={toggleSidebar} />
      <div className={`app${sidebarOpen ? ' sidebar-visible' : ''}`}>
        {/* Backdrop: clicking it closes the sidebar on mobile */}
        <div className="sidebar-backdrop" onClick={closeSidebar} />
        <Sidebar snap={snap} connected={connected} onClose={closeSidebar} />
        <main className="main-content" onClick={sidebarOpen ? closeSidebar : undefined}>
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
          </Routes>
        </main>
      </div>
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
