import { useState, useEffect, useRef } from 'react'

// Same-origin when served by FastAPI, or explicit dev URL
const API = window.location.port === '8502' ? '' : 'http://localhost:8502'

// Dashboard password — must match DASHBOARD_PASSWORD env var (default: pypoc2024)
const DASH_PASS = (window as any).__DASH_PASS__ ?? 'pypoc2024'

const WS_URL = (window.location.port === '8502'
  ? `ws://${window.location.host}/ws/live`
  : 'ws://localhost:8502/ws/live') + `?token=${encodeURIComponent(DASH_PASS)}`

const BASIC_AUTH = 'Basic ' + btoa(`admin:${DASH_PASS}`)

// ── Global request deduplication map ─────────────────────────────────────────
// Maps in-flight URL → Promise so concurrent callers share one fetch.
const _inflight = new Map<string, Promise<any>>()

// ── useSnapshot ───────────────────────────────────────────────────────────────
export function useSnapshot() {
  const [snap, setSnap] = useState<any>(null)
  const [connected, setConnected] = useState(false)
  // 'failed' is true after MAX_ATTEMPTS consecutive failures
  const [failed, setFailed] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  // Tracks the last serialised snap so we skip re-renders on identical payloads
  const lastJsonRef = useRef<string>('')
  // Exponential backoff state
  const delayRef = useRef<number>(1000)
  const attemptsRef = useRef<number>(0)
  const MAX_ATTEMPTS = 10
  const MAX_DELAY = 30000

  useEffect(() => {
    let cancelled = false

    function connect() {
      if (cancelled) return
      if (attemptsRef.current >= MAX_ATTEMPTS) {
        setFailed(true)
        return
      }

      const ws = new WebSocket(WS_URL)
      wsRef.current = ws

      ws.onopen = () => {
        if (cancelled) { ws.close(); return }
        setConnected(true)
        setFailed(false)
        // Reset backoff on successful connection
        delayRef.current = 1000
        attemptsRef.current = 0
      }

      ws.onmessage = (e) => {
        if (cancelled) return
        try {
          const parsed = JSON.parse(e.data as string)
          // Skip keepalive pings
          if (parsed && parsed.ping) return
          const json = e.data as string
          // Only update state if the payload actually changed
          if (json !== lastJsonRef.current) {
            lastJsonRef.current = json
            setSnap(parsed)
          }
        } catch {
          // ignore malformed messages
        }
      }

      ws.onclose = () => {
        if (cancelled) return
        setConnected(false)
        attemptsRef.current += 1
        const delay = delayRef.current
        // Double delay for next attempt, capped at MAX_DELAY
        delayRef.current = Math.min(delay * 2, MAX_DELAY)
        setTimeout(connect, delay)
      }

      ws.onerror = () => ws.close()
    }

    connect()

    return () => {
      cancelled = true
      wsRef.current?.close()
    }
  }, [])

  return { snap, connected, connectionFailed: failed }
}

// ── apiGet (with deduplication) ───────────────────────────────────────────────
export async function apiGet(path: string) {
  const url = API + path
  // Return the existing in-flight promise if one exists for this URL
  const existing = _inflight.get(url)
  if (existing) return existing

  const promise = fetch(url, {
    headers: {
      'Accept': 'application/json',
      'Authorization': BASIC_AUTH,
    },
  })
    .then(r => {
      if (!r.ok) throw new Error(r.statusText)
      return r.json()
    })
    .finally(() => {
      _inflight.delete(url)
    })

  _inflight.set(url, promise)
  return promise
}

export async function apiPost(path: string, body?: any) {
  const r = await fetch(API + path, {
    method: 'POST',
    headers: {
      'Accept': 'application/json',
      'Content-Type': 'application/json',
      'Authorization': BASIC_AUTH,
    },
    body: body != null ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(r.statusText)
  return r.json()
}

/**
 * useSSE — subscribe to a Server-Sent Events endpoint.
 *
 * A simpler alternative to the WebSocket-based useSnapshot() for browsers
 * that have issues authenticating WebSocket upgrades.  The SSE endpoint uses
 * standard HTTP Basic Auth (Authorization header), which browsers handle
 * transparently.
 *
 * @param path  API path relative to the base URL, e.g. "/api/events/live"
 * @returns     The last parsed JSON event payload, or null before the first event.
 */
export function useSSE(path: string) {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    // EventSource does not support custom headers natively, so we embed
    // credentials in the URL using basic-auth syntax (supported by our
    // FastAPI HTTPBasic dependency via the Authorization header injected by
    // the browser when credentials are supplied in the URL).
    // Format: http://user:pass@host/path
    const baseUrl = window.location.port === '8502'
      ? `${window.location.protocol}//${window.location.host}`
      : 'http://localhost:8502'
    const url = `${baseUrl.replace('://', `://admin:${encodeURIComponent(DASH_PASS)}@`)}${path}`

    const es = new EventSource(url)
    es.onmessage = (e) => {
      try { setData(JSON.parse(e.data)) } catch {}
    }
    es.onerror = () => {
      // EventSource auto-reconnects on transient errors; nothing to do here.
    }
    return () => es.close()
  }, [path])

  return data
}

// ── useApi (with visibility-aware polling) ────────────────────────────────────
export function useApi<T>(path: string, interval = 30000) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Skip fetching when path is empty (conditional hooks pattern)
    if (!path) {
      setLoading(false)
      setData(null)
      return
    }

    let timerId: ReturnType<typeof setInterval> | null = null

    async function load() {
      try { setData(await apiGet(path)) } catch {} finally { setLoading(false) }
    }

    function startPolling() {
      if (interval > 0 && timerId === null) {
        timerId = setInterval(load, interval)
      }
    }

    function stopPolling() {
      if (timerId !== null) {
        clearInterval(timerId)
        timerId = null
      }
    }

    function handleVisibilityChange() {
      if (document.hidden) {
        stopPolling()
      } else {
        // Immediately re-fetch when tab becomes visible again, then resume polling
        load()
        startPolling()
      }
    }

    // Initial fetch
    load()

    // Only set up polling if interval > 0 and tab is currently visible
    if (interval > 0 && !document.hidden) {
      startPolling()
    }

    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      stopPolling()
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [path, interval])

  return { data, loading }
}
