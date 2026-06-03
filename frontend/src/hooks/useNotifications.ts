import { useState, useEffect, useRef, useCallback } from 'react'
import { apiGet } from './useSnapshot'

export interface GuardrailRejection {
  id: string
  symbol: string
  strategy: string
  rule: string
  ts: number
  read?: boolean
}

export interface Toast {
  id: string
  message: string
  type: 'success' | 'error' | 'warning' | 'info'
  exiting?: boolean
}

// -- Guardrail notifications hook --
export function useGuardrailNotifications() {
  const [items, setItems] = useState<GuardrailRejection[]>([])
  const [unreadCount, setUnreadCount] = useState(0)
  const readIdsRef = useRef<Set<string>>(new Set())

  const fetchRejections = useCallback(async () => {
    try {
      const data: any[] = await apiGet('/api/guardrails?limit=20')
      const rejections: GuardrailRejection[] = (data ?? []).map((r: any, i: number) => ({
        id: r.id ?? `${r.ts ?? i}-${r.symbol ?? i}`,
        symbol: r.symbol ?? '—',
        strategy: r.strategy ?? '—',
        rule: r.rule ?? r.reason ?? '—',
        ts: r.ts ?? 0,
      }))
      setItems(rejections)
      const unread = rejections.filter(r => !readIdsRef.current.has(r.id)).length
      setUnreadCount(unread)
    } catch {
      // ignore fetch errors
    }
  }, [])

  useEffect(() => {
    fetchRejections()
    const t = setInterval(fetchRejections, 30000)
    return () => clearInterval(t)
  }, [fetchRejections])

  const markAllRead = useCallback(() => {
    items.forEach(r => readIdsRef.current.add(r.id))
    setUnreadCount(0)
  }, [items])

  return { items, unreadCount, markAllRead }
}

// -- Toast hook --
let toastIdCounter = 0

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])

  const addToast = useCallback((message: string, type: Toast['type']) => {
    const id = String(++toastIdCounter)
    setToasts(prev => [...prev, { id, message, type }])
    // Begin exit animation after 3.5s, remove after 4s
    setTimeout(() => {
      setToasts(prev => prev.map(t => t.id === id ? { ...t, exiting: true } : t))
    }, 3500)
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, 4000)
  }, [])

  const dismiss = useCallback((id: string) => {
    setToasts(prev => prev.map(t => t.id === id ? { ...t, exiting: true } : t))
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 400)
  }, [])

  return { toasts, addToast, dismiss }
}
