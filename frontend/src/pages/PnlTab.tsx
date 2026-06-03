import { useApi } from '../hooks/useSnapshot'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, ResponsiveContainer } from 'recharts'

export function PnlTab() {
  const { data: equity } = useApi<any[]>('/api/equity?limit=500', 30000)
  const { data: trades } = useApi<any[]>('/api/trades?limit=200', 30000)

  const chartData = (equity ?? []).map((e: any) => ({
    ts: new Date(e.ts).toLocaleDateString('en-IN', { month: 'short', day: 'numeric' }),
    equity: e.equity,
  }))

  const allTrades = trades ?? []
  const totalPnl = allTrades.reduce((s: number, t: any) => s + (t.pnl ?? 0), 0)
  const totalCharges = allTrades.reduce((s: number, t: any) => s + (t.charges ?? 0), 0)
  const wins = allTrades.filter((t: any) => t.pnl > 0)
  const winRate = allTrades.length ? (wins.length / allTrades.length * 100).toFixed(1) : '0'
  const avgWin = wins.length ? wins.reduce((s: number, t: any) => s + t.pnl, 0) / wins.length : 0
  const losses = allTrades.filter((t: any) => t.pnl <= 0)
  const avgLoss = losses.length ? losses.reduce((s: number, t: any) => s + t.pnl, 0) / losses.length : 0
  const pf = avgLoss ? Math.abs(avgWin / avgLoss).toFixed(2) : '—'

  return (
    <div className="tab-content">
      <div className="kpi-row">
        <div className="kpi-card"><div className="kpi-label">Net P&L</div>
          <div className={`kpi-value ${totalPnl >= 0 ? 'green' : 'red'}`}>₹{totalPnl.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div></div>
        <div className="kpi-card"><div className="kpi-label">Win Rate</div><div className="kpi-value">{winRate}%</div></div>
        <div className="kpi-card"><div className="kpi-label">Trades</div><div className="kpi-value">{allTrades.length}</div></div>
        <div className="kpi-card"><div className="kpi-label">Profit Factor</div><div className="kpi-value">{pf}</div></div>
        <div className="kpi-card"><div className="kpi-label">Charges</div>
          <div className="kpi-value yellow">₹{totalCharges.toLocaleString('en-IN', { maximumFractionDigits: 0 })}</div></div>
      </div>

      <section className="section chart-section">
        <h2>Equity Curve</h2>
        {chartData.length > 1 ? (
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#2d3748" />
              <XAxis dataKey="ts" tick={{ fontSize: 11, fill: '#718096' }} />
              <YAxis tick={{ fontSize: 11, fill: '#718096' }} tickFormatter={(v: number) => '₹' + (v / 1000).toFixed(0) + 'k'} />
              <Tooltip formatter={(v: any) => ['₹' + Number(v).toLocaleString('en-IN', { maximumFractionDigits: 0 }), 'Equity']}
                contentStyle={{ background: '#1a202c', border: '1px solid #2d3748', borderRadius: 6 }} />
              <ReferenceLine y={500000} stroke="#4a5568" strokeDasharray="4 4"
                label={{ value: 'Start ₹5L', fill: '#718096', fontSize: 10, position: 'insideTopLeft' }} />
              <Area type="monotone" dataKey="equity" stroke="#4299e1" fill="url(#equityGrad)" strokeWidth={2} dot={false} />
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#4299e1" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#4299e1" stopOpacity={0} />
                </linearGradient>
              </defs>
            </AreaChart>
          </ResponsiveContainer>
        ) : <div className="empty-state">No equity history yet — run the paper agent</div>}
      </section>
    </div>
  )
}
