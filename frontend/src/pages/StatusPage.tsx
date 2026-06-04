import { useApi } from "../hooks/useSnapshot"

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────
interface StatusData {
  api_version?: string
  agent_running?: boolean
  agent_halted?: boolean
  equity?: number | null
  regime?: string | null
  gate_passed?: boolean
  gate_age_days?: number | null
  services?: Record<string, boolean>
  timestamp?: string
}

interface SystemData {
  cpu_pct?: number
  memory_used_gb?: number
  memory_total_gb?: number
  memory_pct?: number
  disk_used_gb?: number
  disk_free_gb?: number
  disk_pct?: number
  uptime_hours?: number
  python_processes?: number
}

interface GateData {
  passed?: boolean
  sharpe?: number
  max_drawdown_pct?: number
  win_rate?: number
  profit_factor?: number
  n_trades?: number
  timestamp?: string
  error?: string
}

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────
function StatusBadge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "3px 10px",
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 600,
        background: ok ? "rgba(72,187,120,0.15)" : "rgba(252,129,129,0.15)",
        color: ok ? "#48bb78" : "#fc8181",
        border: `1px solid ${ok ? "rgba(72,187,120,0.3)" : "rgba(252,129,129,0.3)"}`,
      }}
    >
      <span style={{ fontSize: 9 }}>{ok ? "●" : "○"}</span>
      {label}
    </span>
  )
}

function MetricBar({ pct, color = "#4299e1" }: { pct: number; color?: string }) {
  const clamped = Math.min(100, Math.max(0, pct))
  const barColor = clamped > 85 ? "#fc8181" : clamped > 65 ? "#ecc94b" : color
  return (
    <div
      style={{
        height: 6,
        background: "var(--bg3)",
        borderRadius: 3,
        overflow: "hidden",
        marginTop: 6,
      }}
    >
      <div
        style={{
          height: "100%",
          width: `${clamped}%`,
          background: barColor,
          borderRadius: 3,
          transition: "width 0.4s ease",
        }}
      />
    </div>
  )
}

function InfoRow({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "8px 0",
        borderBottom: "1px solid var(--border)",
        fontSize: 13,
      }}
    >
      <span style={{ color: "var(--text2)" }}>{label}</span>
      <span style={{ color: color ?? "var(--text)", fontWeight: 500 }}>{value}</span>
    </div>
  )
}

function Card({
  title,
  children,
  style,
}: {
  title: string
  children: React.ReactNode
  style?: React.CSSProperties
}) {
  return (
    <div
      style={{
        background: "var(--bg2)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: "16px 20px",
        ...style,
      }}
    >
      <h2
        style={{
          fontSize: 13,
          fontWeight: 700,
          color: "var(--text2)",
          textTransform: "uppercase",
          letterSpacing: "0.05em",
          marginBottom: 12,
          marginTop: 0,
        }}
      >
        {title}
      </h2>
      {children}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Sections
// ─────────────────────────────────────────────────────────────────────────────
function ServicesSection({ services }: { services: Record<string, boolean> }) {
  const entries = Object.entries(services)
  if (!entries.length) return <div className="empty-state">No service data available.</div>
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, paddingTop: 4 }}>
      {entries.map(([name, running]) => (
        <StatusBadge key={name} ok={running} label={name.charAt(0).toUpperCase() + name.slice(1)} />
      ))}
    </div>
  )
}

function SystemResourcesSection({ sys }: { sys: SystemData }) {
  return (
    <>
      <div>
        <InfoRow
          label="CPU"
          value={`${(sys.cpu_pct ?? 0).toFixed(1)}%`}
          color={(sys.cpu_pct ?? 0) > 85 ? "#fc8181" : "var(--text)"}
        />
        <MetricBar pct={sys.cpu_pct ?? 0} color="#4299e1" />
      </div>
      <div style={{ marginTop: 12 }}>
        <InfoRow
          label="Memory"
          value={`${sys.memory_used_gb ?? 0} / ${sys.memory_total_gb ?? 0} GB (${(sys.memory_pct ?? 0).toFixed(1)}%)`}
          color={(sys.memory_pct ?? 0) > 85 ? "#fc8181" : "var(--text)"}
        />
        <MetricBar pct={sys.memory_pct ?? 0} color="#9f7aea" />
      </div>
      <div style={{ marginTop: 12 }}>
        <InfoRow
          label="Disk"
          value={`${sys.disk_used_gb ?? 0} used, ${sys.disk_free_gb ?? 0} GB free (${(sys.disk_pct ?? 0).toFixed(1)}%)`}
          color={(sys.disk_pct ?? 0) > 85 ? "#fc8181" : "var(--text)"}
        />
        <MetricBar pct={sys.disk_pct ?? 0} color="#ed8936" />
      </div>
      <InfoRow
        label="Uptime"
        value={
          sys.uptime_hours != null
            ? sys.uptime_hours >= 24
              ? `${(sys.uptime_hours / 24).toFixed(1)} days`
              : `${sys.uptime_hours.toFixed(1)} hours`
            : "—"
        }
      />
      <InfoRow label="Python Processes" value={sys.python_processes ?? "—"} />
    </>
  )
}

function GateSection({ gate }: { gate: GateData }) {
  if (gate.error) {
    return <div className="empty-state">{gate.error}</div>
  }

  const passed = gate.passed ?? false
  const thresholds: { label: string; value: number | undefined; thresh: number; fmt: (v: number) => string; lte?: boolean }[] = [
    { label: "Sharpe", value: gate.sharpe, thresh: 1.2, fmt: v => v.toFixed(2) },
    { label: "Max Drawdown", value: gate.max_drawdown_pct, thresh: 15, fmt: v => `${v.toFixed(1)}%`, lte: true },
    { label: "Win Rate", value: gate.win_rate, thresh: 45, fmt: v => `${v.toFixed(1)}%` },
    { label: "Profit Factor", value: gate.profit_factor, thresh: 1.5, fmt: v => v.toFixed(2) },
    { label: "Trades", value: gate.n_trades, thresh: 100, fmt: v => String(Math.round(v)) },
  ]

  return (
    <>
      <InfoRow
        label="Gate Status"
        value={<StatusBadge ok={passed} label={passed ? "PASSED" : "FAILING"} />}
      />
      {thresholds.map(({ label, value, thresh, fmt, lte }) => {
        if (value == null) return null
        const ok = lte ? value <= thresh : value >= thresh
        const color = ok ? "#48bb78" : "#fc8181"
        return (
          <InfoRow
            key={label}
            label={label}
            value={
              <span style={{ color }}>
                {fmt(value)}{" "}
                <span style={{ fontSize: 11, opacity: 0.7 }}>
                  (need {lte ? "≤" : "≥"}{lte ? thresh + "%" : thresh})
                </span>
              </span>
            }
          />
        )
      })}
      {gate.timestamp && (
        <InfoRow
          label="Gate File Age"
          value={
            (() => {
              const ts = new Date(gate.timestamp)
              const ageDays = (Date.now() - ts.getTime()) / 86400000
              const color = ageDays > 30 ? "#fc8181" : ageDays > 20 ? "#ecc94b" : "#48bb78"
              return <span style={{ color }}>{ageDays.toFixed(1)} days old</span>
            })()
          }
        />
      )}
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Main StatusPage
// ─────────────────────────────────────────────────────────────────────────────
export function StatusPage() {
  const { data: statusRaw, loading: statusLoading } = useApi<StatusData>("/api/status", 15000)
  const { data: sysRaw, loading: sysLoading } = useApi<SystemData>("/api/system", 15000)
  const { data: gateRaw, loading: gateLoading } = useApi<GateData>("/api/gate", 30000)

  const status = (statusRaw ?? {}) as StatusData
  const sys = (sysRaw ?? {}) as SystemData
  const gate = (gateRaw ?? {}) as GateData

  const loading = statusLoading && sysLoading && gateLoading

  return (
    <div className="tab-content">
      <h1 className="tab-title">System Health</h1>

      {loading ? (
        <div className="empty-state">
          <div className="spinner" style={{ margin: "0 auto 12px" }} />
          Loading system status…
        </div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
            gap: 20,
          }}
        >
          {/* Agent & API */}
          <Card title="Agent &amp; API">
            <InfoRow label="API Version" value={`v${status.api_version ?? "—"}`} />
            <InfoRow
              label="Agent"
              value={
                <StatusBadge
                  ok={status.agent_running === true && !status.agent_halted}
                  label={
                    status.agent_halted
                      ? "HALTED"
                      : status.agent_running
                      ? "Running"
                      : "Stopped"
                  }
                />
              }
            />
            <InfoRow label="Current Regime" value={status.regime ?? "—"} />
            <InfoRow
              label="Equity"
              value={
                status.equity != null
                  ? `₹${status.equity.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`
                  : "—"
              }
            />
            {status.timestamp && (
              <InfoRow
                label="Last Checked"
                value={new Date(status.timestamp).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                  second: "2-digit",
                })}
              />
            )}
          </Card>

          {/* Services */}
          <Card title="NSSM Services">
            {status.services ? (
              <ServicesSection services={status.services} />
            ) : (
              <div className="empty-state" style={{ padding: "8px 0" }}>No service data.</div>
            )}
            <p
              style={{
                fontSize: 11,
                color: "var(--text2)",
                marginTop: 12,
                marginBottom: 0,
                lineHeight: 1.5,
              }}
            >
              Detected by scanning running processes for CLI entry points.
              Green = process found; Red = not detected.
            </p>
          </Card>

          {/* System Resources */}
          <Card title="System Resources">
            {sysLoading ? (
              <div className="empty-state">Loading…</div>
            ) : Object.keys(sys).length === 0 ? (
              <div className="empty-state">System data unavailable.</div>
            ) : (
              <SystemResourcesSection sys={sys} />
            )}
          </Card>

          {/* Backtest Gate */}
          <Card title="Backtest Gate">
            {gateLoading ? (
              <div className="empty-state">Loading…</div>
            ) : (
              <GateSection gate={gate} />
            )}
            <p
              style={{
                fontSize: 11,
                color: "var(--text2)",
                marginTop: 12,
                marginBottom: 0,
                lineHeight: 1.5,
              }}
            >
              Gate must pass before live deployment. Thresholds: Sharpe ≥ 1.2,
              MaxDD ≤ 15%, Win ≥ 45%, PF ≥ 1.5, ≥ 100 trades, file ≤ 30 days old.
            </p>
          </Card>
        </div>
      )}
    </div>
  )
}
