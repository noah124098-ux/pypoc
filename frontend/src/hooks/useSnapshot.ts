import { useState, useEffect, useRef } from 'react'

// Same-origin when served by FastAPI, or explicit dev URL
const API = window.location.port === '8502' ? '' : 'http://localhost:8502'

// Dashboard password — must match DASHBOARD_PASSWORD env var (default: pypoc2024)
const DASH_PASS = (window as any).__DASH_PASS__ ?? 'pypoc2024'

const BASIC_AUTH = 'Basic ' + btoa(`admin:${DASH_PASS}`)

// ── Global request deduplication map ─────────────────────────────────────────
// Maps in-flight URL → Promise so concurrent callers share one fetch.
const _inflight = new Map<string, Promise<any>>()

// ── useSnapshot ───────────────────────────────────────────────────────────────
// Uses SSE (/api/events/live) instead of WebSocket — plain HTTP, no upgrade
// handshake issues, works through any proxy or SPA catch-all route.
export function useSnapshot() {
  const [snap, setSnap] = useState<any>(null)
  const [connected, setConnected] = useState(false)
  const [failed, setFailed] = useState(false)
  const lastJsonRef = useRef<string>('')
  const esRef = useRef<EventSource | null>(null)
  const delayRef = useRef<number>(1000)
  const attemptsRef = useRef<number>(0)
  const MAX_ATTEMPTS = 10
  const MAX_DELAY = 30000

  useEffect(() => {
    let cancelled = false
    let timerId: ReturnType<typeof setTimeout> | null = null

    function connect() {
      if (cancelled) return
      if (attemptsRef.current >= MAX_ATTEMPTS) {
        setFailed(true)
        return
      }

      // SSE with Basic Auth embedded in URL
      const base = window.location.port === '8502'
        ? `${window.location.protocol}//${window.location.host}`
        : 'http://localhost:8502'
      const url = `${base.replace('://', `://admin:${encodeURIComponent(DASH_PASS)}@`)}/api/events/live`

      const es = new EventSource(url)
      esRef.current = es

      es.onopen = () => {
        if (cancelled) { es.close(); return }
        setConnected(true)
        setFailed(false)
        delayRef.current = 1000
        attemptsRef.current = 0
      }

      es.onmessage = (e) => {
        if (cancelled) return
        try {
          const json = e.data as string
          if (json !== lastJsonRef.current) {
            lastJsonRef.current = json
            const parsed = JSON.parse(json)
            if (parsed && !parsed.ping) {
              setSnap(parsed)
              setConnected(true)
            }
          }
        } catch { /* ignore */ }
      }

      es.onerror = () => {
        if (cancelled) return
        es.close()
        setConnected(false)
        attemptsRef.current += 1
        const delay = delayRef.current
        delayRef.current = Math.min(delay * 2, MAX_DELAY)
        timerId = setTimeout(connect, delay)
      }
    }

    connect()

    return () => {
      cancelled = true
      if (timerId) clearTimeout(timerId)
      esRef.current?.close()
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
