import { Component } from 'react'
import type { ReactNode, ErrorInfo } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="tab-content">
          <div className="empty-card">
            <div style={{ fontSize: 48, marginBottom: 12 }}>⚠️</div>
            <h3>Something went wrong</h3>
            <p>{this.state.error?.message || 'An unexpected error occurred'}</p>
            <button
              onClick={() => window.location.href = '/live'}
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
              Go to Live Tab
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
