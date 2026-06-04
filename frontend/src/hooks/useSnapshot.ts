import { useState, useEffect, useRef } from 'react'

// Same-origin when served by FastAPI, or explicit dev URL
const API = window.location.port === '8502' ? '' : 'http://localhost:8502'

// Dashboard password — must match DASHBOARD_PASSWORD env var (default: pypoc2024)
const DASH_PASS = (window as any).__DASH_PASS__ ?? 'pypoc2024'

const WS_URL = (window.location.port === '8502'
  ? `ws://${window.location.host}/ws/live`
  : 'ws://localhost:8502/ws/live') + `?token=${encodeURIComponent(DASH_PASS)}`

const BASIC_AUTH = 'Basic ' + btoa(`admin:${DASH_PASS}`)

export function useSnapshot() {
  const [snap, setSnap] = useState<any>(null)
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    function connect() {
      const ws = new WebSocket(WS_URL)
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onmessage = (e) => { try { setSnap(JSON.parse(e.data)) } catch {} }
      ws.onclose = () => { setConnected(false); setTimeout(connect, 3000) }
      ws.onerror = () => ws.close()
    }
    connect()
    return () => { wsRef.current?.close() }
  }, [])

  return { snap, connected }
}

export async function apiGet(path: string) {
  const r = await fetch(API + path, {
    headers: {
      'Accept': 'application/json',
      'Authorization': BASIC_AUTH,
    },
  })
  if (!r.ok) throw new Error(r.statusText)
  return r.json()
}

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

    async function load() {
      try { setData(await apiGet(path)) } catch {} finally { setLoading(false) }
    }
    load()
    // Only set up polling if interval > 0
    if (interval > 0) {
      const t = setInterval(load, interval)
      return () => clearInterval(t)
    }
  }, [path, interval])

  return { data, loading }
}
