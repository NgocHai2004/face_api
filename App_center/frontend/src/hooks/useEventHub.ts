/**
 * useEventHub - Custom React hook kết nối WebSocket tới Event Hub consumer endpoint.
 *
 * Tính năng:
 * - Auto-connect khi mount
 * - Auto-reconnect khi mất kết nối (với backoff)
 * - Subscribe/unsubscribe topic động
 * - Giữ tối đa N events trong state
 * - Track connection status
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ConnectionStatus, NormalizedEvent, WsMessage } from '../types/event'

// Same-origin qua nginx proxy nếu không set VITE_WS_URL (Docker prod)
// Dev local: set VITE_WS_URL=ws://localhost:8000 trong .env
const _wsEnv  = import.meta.env.VITE_WS_URL
const _apiEnv = import.meta.env.VITE_API_URL
const WS_BASE_URL  = (_wsEnv  && _wsEnv  !== '')
  ? _wsEnv
  : `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
const API_BASE_URL = (_apiEnv && _apiEnv !== '')
  ? _apiEnv
  : `${window.location.protocol}//${window.location.host}`
const MAX_EVENTS   = 200   // Giữ tối đa 200 events trong bộ nhớ
const RECONNECT_DELAYS = [1000, 2000, 5000, 10000] // Backoff delays (ms)

interface UseEventHubOptions {
  topic?: string
  autoConnect?: boolean
  maxEvents?: number
}

interface UseEventHubReturn {
  events: NormalizedEvent[]
  status: ConnectionStatus
  subscribedTopic: string
  eventsPerSecond: number
  changeTopic: (topic: string) => void
  clearEvents: () => void
  connect: () => void
  disconnect: () => void
}

export function useEventHub({
  topic = '*',
  autoConnect = true,
  maxEvents = MAX_EVENTS,
}: UseEventHubOptions = {}): UseEventHubReturn {
  const [events, setEvents]               = useState<NormalizedEvent[]>([])
  const [status, setStatus]               = useState<ConnectionStatus>('disconnected')
  const [subscribedTopic, setSubscribedTopic] = useState<string>(topic)
  const [eventsPerSecond, setEventsPerSecond] = useState<number>(0)

  const wsRef           = useRef<WebSocket | null>(null)
  const reconnectCount  = useRef<number>(0)
  const reconnectTimer  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const currentTopic    = useRef<string>(topic)
  const eventCountRef   = useRef<number>(0)   // For events/sec calculation
  const epsTimer        = useRef<ReturnType<typeof setInterval> | null>(null)

  // ---------------------------------------------------------------------------
  // Events/sec tracker
  // ---------------------------------------------------------------------------
  useEffect(() => {
    epsTimer.current = setInterval(() => {
      setEventsPerSecond(eventCountRef.current)
      eventCountRef.current = 0
    }, 1000)
    return () => {
      if (epsTimer.current) clearInterval(epsTimer.current)
    }
  }, [])

  // ---------------------------------------------------------------------------
  // Connect
  // ---------------------------------------------------------------------------
  const connect = useCallback(() => {
    // Đóng connection cũ nếu có
    if (wsRef.current) {
      wsRef.current.onclose = null // Prevent auto-reconnect khi đóng thủ công
      wsRef.current.close()
    }

    const url = `${WS_BASE_URL}/ws/consumer?topic=${encodeURIComponent(currentTopic.current)}`
    setStatus('connecting')

    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      reconnectCount.current = 0
      setStatus('connected')
    }

    ws.onmessage = (ev) => {
      try {
        const msg: WsMessage = JSON.parse(ev.data)

        // Bỏ qua system messages
        if ('type' in msg && msg.type === '__system__') {
          if ('subscribed_topic' in msg && msg.subscribed_topic) {
            setSubscribedTopic(msg.subscribed_topic)
          }
          return
        }

        // NormalizedEvent
        const event = msg as NormalizedEvent
        eventCountRef.current += 1
        setEvents(prev => {
          const updated = [event, ...prev]
          return updated.slice(0, maxEvents)
        })
      } catch {
        // Bỏ qua message không parse được
      }
    }

    ws.onerror = () => {
      setStatus('error')
    }

    ws.onclose = () => {
      setStatus('disconnected')
      wsRef.current = null
      scheduleReconnect()
    }
  }, [maxEvents])

  // ---------------------------------------------------------------------------
  // Reconnect với backoff
  // ---------------------------------------------------------------------------
  const scheduleReconnect = useCallback(() => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    const delay = RECONNECT_DELAYS[Math.min(reconnectCount.current, RECONNECT_DELAYS.length - 1)]
    reconnectCount.current += 1
    reconnectTimer.current = setTimeout(() => {
      connect()
    }, delay)
  }, [connect])

  // ---------------------------------------------------------------------------
  // Disconnect (manual)
  // ---------------------------------------------------------------------------
  const disconnect = useCallback(() => {
    if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    reconnectCount.current = 999 // Prevent auto-reconnect
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
      wsRef.current = null
    }
    setStatus('disconnected')
  }, [])

  // ---------------------------------------------------------------------------
  // Clear events
  // ---------------------------------------------------------------------------
  const clearEvents = useCallback(() => setEvents([]), [])

  // ---------------------------------------------------------------------------
  // Fetch history từ MongoDB (GET /events/recent?from_db=true)
  // ---------------------------------------------------------------------------
  const fetchHistory = useCallback(async (historyTopic: string) => {
    try {
      const t = historyTopic === '*' ? '*' : encodeURIComponent(historyTopic)
      const res = await fetch(`${API_BASE_URL}/events/recent?topic=${t}&limit=200&from_db=true`)
      if (!res.ok) return
      const data = await res.json()
      const histEvents: NormalizedEvent[] = (data.events ?? [])
        .slice()
        .reverse()         // mới nhất lên đầu (API trả về cũ → mới)
      if (histEvents.length === 0) return
      setEvents(prev => {
        // Gộp: realtime events (prev) + history, loại trùng theo id
        const existingIds = new Set(prev.map(e => e.id))
        const merged = [...prev, ...histEvents.filter(e => !existingIds.has(e.id))]
        return merged.slice(0, maxEvents)
      })
    } catch {
      // Bỏ qua lỗi fetch history (MongoDB offline, v.v.)
    }
  }, [maxEvents])

  // ---------------------------------------------------------------------------
  // Change topic
  // ---------------------------------------------------------------------------
  const changeTopic = useCallback((newTopic: string) => {
    currentTopic.current = newTopic
    setEvents([]) // Clear events khi đổi topic
    // Reconnect với topic mới + load lại history
    if (wsRef.current) {
      wsRef.current.onclose = null
      wsRef.current.close()
      wsRef.current = null
    }
    connect()
    fetchHistory(newTopic)
  }, [connect, fetchHistory])

  // ---------------------------------------------------------------------------
  // Auto-connect on mount + load history
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (autoConnect) {
      connect()
      fetchHistory(currentTopic.current)
    }
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      if (wsRef.current) {
        wsRef.current.onclose = null
        wsRef.current.close()
      }
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    events,
    status,
    subscribedTopic,
    eventsPerSecond,
    changeTopic,
    clearEvents,
    connect,
    disconnect,
  }
}
