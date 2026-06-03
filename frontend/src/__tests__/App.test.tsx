import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

// Mock all page components to avoid deep dependency chains
vi.mock('../pages/LiveTab', () => ({ LiveTab: () => <div>LiveTab</div> }))
vi.mock('../pages/PnlTab', () => ({ PnlTab: () => <div>PnlTab</div> }))
vi.mock('../pages/PositionsTab', () => ({ PositionsTab: () => <div>PositionsTab</div> }))
vi.mock('../pages/RegimeTab', () => ({ RegimeTab: () => <div>RegimeTab</div> }))
vi.mock('../pages/BacktestTab', () => ({ BacktestTab: () => <div>BacktestTab</div> }))
vi.mock('../pages/ControlsTab', () => ({ ControlsTab: () => <div>ControlsTab</div> }))
vi.mock('../pages/ReplayTab', () => ({ ReplayTab: () => <div>ReplayTab</div> }))
vi.mock('../pages/AiReviewTab', () => ({ AiReviewTab: () => <div>AiReviewTab</div> }))
vi.mock('../pages/CostsTab', () => ({ CostsTab: () => <div>CostsTab</div> }))
vi.mock('../pages/PortfolioTab', () => ({ PortfolioTab: () => <div>PortfolioTab</div> }))

// Mock recharts to avoid canvas/SVG issues in jsdom
vi.mock('recharts', () => ({
  LineChart: ({ children }: any) => <svg>{children}</svg>,
  Line: () => null,
  ResponsiveContainer: ({ children }: any) => <div>{children}</div>,
}))

// Mock useSnapshot and useApi
vi.mock('../hooks/useSnapshot', () => ({
  useSnapshot: () => ({ snap: null, connected: false }),
  useApi: () => ({ data: null, loading: false }),
  apiGet: vi.fn().mockResolvedValue({}),
}))

import App from '../App'

describe('App', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders without crashing', () => {
    const { container } = render(<App />)
    expect(container).toBeTruthy()
  })

  it('renders the sidebar with logo', () => {
    render(<App />)
    // Logo appears in the sidebar header — use getAllByText in case of multiple matches
    const logos = screen.getAllByText('pypoc')
    expect(logos.length).toBeGreaterThan(0)
  })

  it('renders sidebar equity box', () => {
    render(<App />)
    // Equity label is visible in the sidebar
    expect(screen.getByText('Equity')).toBeInTheDocument()
  })

  it('renders NavLinks for navigation items', () => {
    render(<App />)
    // Check a sample of nav links are present
    expect(screen.getByText('🟢 Live')).toBeInTheDocument()
    expect(screen.getByText('📊 P&L')).toBeInTheDocument()
    expect(screen.getByText('📋 Positions')).toBeInTheDocument()
    expect(screen.getByText('🌡️ Regime')).toBeInTheDocument()
  })

  it('NavLinks use absolute paths', () => {
    render(<App />)
    // NavLinks should have href attributes with leading slash
    const liveLink = screen.getByText('🟢 Live').closest('a')
    expect(liveLink).not.toBeNull()
    expect(liveLink?.getAttribute('href')).toMatch(/^\/live/)
  })

  it('shows disconnected state when not connected', () => {
    render(<App />)
    // The red dot has title="Disconnected"
    const dot = document.querySelector('.dot.red')
    expect(dot).not.toBeNull()
  })

  it('shows equity as 0 when snap is null', () => {
    render(<App />)
    // Default equity is 0
    expect(screen.getByText('₹0')).toBeInTheDocument()
  })
})
