import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'

// We need to mock window.location and WebSocket before importing the module
// because useSnapshot reads them at module-load time

// Mock WebSocket
class MockWebSocket {
  static instances: MockWebSocket[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: ((e: any) => void) | null = null
  readyState = 0

  constructor(public url: string) {
    MockWebSocket.instances.push(this)
  }

  close() {
    this.readyState = 3
    this.onclose?.()
  }
}

;(globalThis as any).WebSocket = MockWebSocket

// Mock fetch
const mockFetch = vi.fn()
;(globalThis as any).fetch = mockFetch

// Mock window.location
Object.defineProperty(window, 'location', {
  value: { port: '3000', host: 'localhost:3000', protocol: 'http:' },
  writable: true,
})

// Mock btoa (available in jsdom but ensure it works)
;(globalThis as any).btoa = (s: string) => Buffer.from(s).toString('base64')

// Import hooks after setting up mocks
import { useApi, apiGet } from '../hooks/useSnapshot'

describe('useApi', () => {
  beforeEach(() => {
    MockWebSocket.instances = []
    mockFetch.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('returns loading=true initially', () => {
    // Make fetch never resolve during this check
    mockFetch.mockImplementation(() => new Promise(() => {}))

    const { result } = renderHook(() => useApi('/api/test'))
    expect(result.current.loading).toBe(true)
  })

  it('returns data=null initially', () => {
    mockFetch.mockImplementation(() => new Promise(() => {}))

    const { result } = renderHook(() => useApi('/api/test'))
    expect(result.current.data).toBeNull()
  })

  it('sets loading=false after successful fetch', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve([{ equity: 100000 }]),
    })

    const { result } = renderHook(() => useApi<any[]>('/api/equity'))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.data).toEqual([{ equity: 100000 }])
  })

  it('sets loading=false after failed fetch', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      statusText: 'Not Found',
    })

    const { result } = renderHook(() => useApi('/api/missing'))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    // data stays null on error
    expect(result.current.data).toBeNull()
  })

  it('handles fetch network error gracefully', async () => {
    mockFetch.mockRejectedValueOnce(new Error('Network error'))

    const { result } = renderHook(() => useApi('/api/error'))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.data).toBeNull()
  })
})

describe('apiGet', () => {
  beforeEach(() => {
    mockFetch.mockReset()
  })

  it('returns parsed JSON on success', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ status: 'ok' }),
    })

    const result = await apiGet('/api/status')
    expect(result).toEqual({ status: 'ok' })
  })

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      statusText: 'Unauthorized',
    })

    await expect(apiGet('/api/status')).rejects.toThrow('Unauthorized')
  })

  it('throws on network failure', async () => {
    mockFetch.mockRejectedValueOnce(new Error('connection refused'))

    await expect(apiGet('/api/status')).rejects.toThrow('connection refused')
  })

  it('sends Authorization header', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    })

    await apiGet('/api/test')

    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining('/api/test'),
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: expect.stringMatching(/^Basic /),
        }),
      })
    )
  })
})
