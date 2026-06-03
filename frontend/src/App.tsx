import { BrowserRouter, Routes, Route, Navigate, NavLink, useNavigate } from 'react-router-dom'
import { LineChart, Line, ResponsiveContainer } from 'recharts'
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
]

function TopBar({ snap, connected }: { snap: any, connected: boolean }) {
  const navigate = useNavigate()
  const equity = snap?.equity ?? 500000
  const dayPnl = equity - (snap?.starting_equity_today ?? equity)
  const regime = snap?.current_regime ?? '—'
  const halted = snap?.halted ?? false
  const positions: any[] = snap?.positions ?? []
  const posCount = positions.length

  return (
    <div className="top-bar">
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
      {halted && (
        <span className="top-bar-halt">⚠ HALTED</span>
      )}
    </div>
  )
}

function Sidebar({ snap, connected }: { snap: any, connected: boolean }) {
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
          <NavLink key={n.path} to={`/${n.path}`} className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
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
      </div>
    </aside>
  )
}

function Layout() {
  const { snap, connected } = useSnapshot()
  return (
    <div className="app-wrapper">
      <TopBar snap={snap} connected={connected} />
      <div className="app">
        <Sidebar snap={snap} connected={connected} />
        <main className="main-content">
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
          </Routes>
        </main>
      </div>
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/*" element={<Layout />} />
      </Routes>
    </BrowserRouter>
  )
}
