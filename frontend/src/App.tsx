import { BrowserRouter, Routes, Route, Navigate, NavLink } from 'react-router-dom'
import { LiveTab } from './pages/LiveTab'
import { PnlTab } from './pages/PnlTab'
import { PositionsTab } from './pages/PositionsTab'
import { RegimeTab } from './pages/RegimeTab'
import { BacktestTab } from './pages/BacktestTab'
import { ControlsTab } from './pages/ControlsTab'
import { useSnapshot } from './hooks/useSnapshot'

const NAV = [
  { path: 'live', label: '🟢 Live' },
  { path: 'pnl', label: '📊 P&L' },
  { path: 'positions', label: '📋 Positions' },
  { path: 'regime', label: '🌡️ Regime' },
  { path: 'backtest', label: '🔬 Backtest' },
  { path: 'controls', label: '⚙️ Controls' },
]

function Sidebar({ snap, connected }: { snap: any, connected: boolean }) {
  const equity = snap?.equity ?? 0
  const dayPnl = equity - (snap?.starting_equity_today ?? equity)
  const regime = snap?.current_regime ?? '—'
  const halted = snap?.halted ?? false

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
      </div>

      {halted && <div className="halt-banner">⛔ HALTED</div>}

      <div className="regime-chip">{regime}</div>

      <nav>
        {NAV.map(n => (
          <NavLink key={n.path} to={n.path} className={({ isActive }) => 'nav-link' + (isActive ? ' active' : '')}>
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
    <div className="app">
      <Sidebar snap={snap} connected={connected} />
      <main className="main-content">
        <Routes>
          <Route index element={<Navigate to="live" />} />
          <Route path="live" element={<LiveTab snap={snap} connected={connected} />} />
          <Route path="pnl" element={<PnlTab />} />
          <Route path="positions" element={<PositionsTab snap={snap} />} />
          <Route path="regime" element={<RegimeTab />} />
          <Route path="backtest" element={<BacktestTab />} />
          <Route path="controls" element={<ControlsTab snap={snap} />} />
        </Routes>
      </main>
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
