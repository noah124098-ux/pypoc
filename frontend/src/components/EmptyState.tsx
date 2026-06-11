export interface EmptyStateProps {
  icon?: string
  title: string
  description: string
  action?: {
    label: string
    onClick: () => void
  }
}

export function EmptyState({ icon = '📭', title, description, action }: EmptyStateProps) {
  return (
    <div className="empty-card">
      <div style={{ fontSize: 48, marginBottom: 12 }}>{icon}</div>
      <h3>{title}</h3>
      <p>{description}</p>
      {action && (
        <button
          onClick={action.onClick}
          style={{
            marginTop: 16,
            padding: '8px 16px',
            background: 'rgba(66,153,225,.15)',
            color: '#4299e1',
            border: '1px solid rgba(66,153,225,.3)',
            borderRadius: 6,
            cursor: 'pointer',
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          {action.label}
        </button>
      )}
    </div>
  )
}
