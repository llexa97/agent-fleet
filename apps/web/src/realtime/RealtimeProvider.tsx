import { useQueryClient } from '@tanstack/react-query'
import { createContext, use, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { websocketUrl } from '../lib/api'
import type { EventEnvelope } from '../types/api'

export type RealtimeStatus = 'connecting' | 'connected' | 'disconnected'

interface RealtimeContextValue {
  status: RealtimeStatus
  events: EventEnvelope[]
  reconnect: () => void
}

const RealtimeContext = createContext<RealtimeContextValue | null>(null)

function isEventEnvelope(value: unknown): value is EventEnvelope {
  if (typeof value !== 'object' || value === null) return false
  const record = value as Record<string, unknown>
  return typeof record.event_type === 'string' && typeof record.payload === 'object'
}

export function RealtimeProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<RealtimeStatus>('connecting')
  const [events, setEvents] = useState<EventEnvelope[]>([])
  const [generation, setGeneration] = useState(0)
  const lastEventId = useRef<string | null>(null)

  const invalidateFor = useCallback(
    (event: EventEnvelope) => {
      const root = event.event_type.split('.')[0]
      if (root === 'message' || root === 'mention' || root === 'delivery' || root === 'session') {
        void queryClient.invalidateQueries({ queryKey: ['messages', event.channel_id] })
        void queryClient.invalidateQueries({ queryKey: ['agents'] })
      }
      if (root === 'task') void queryClient.invalidateQueries({ queryKey: ['tasks'] })
      if (root === 'trace') void queryClient.invalidateQueries({ queryKey: ['traces'] })
      if (root === 'worker') void queryClient.invalidateQueries({ queryKey: ['workers'] })
      if (root === 'permission') void queryClient.invalidateQueries({ queryKey: ['permissions'] })
      if (root === 'workflow') void queryClient.invalidateQueries({ queryKey: ['workflows'] })
    },
    [queryClient],
  )

  const connect = useCallback(() => {
    setGeneration((current) => current + 1)
    setStatus('connecting')
  }, [])

  useEffect(() => {
    let disposed = false
    let socket: WebSocket | null = null
    let retryTimer: number | null = null
    let heartbeatTimer: number | null = null
    let attempt = 0

    const open = () => {
      if (disposed) return
      setStatus('connecting')
      const url = new URL(websocketUrl())
      if (lastEventId.current) url.searchParams.set('after', lastEventId.current)
      socket = new WebSocket(url)
      socket.addEventListener('open', () => {
        attempt = 0
        setStatus('connected')
        heartbeatTimer = window.setInterval(() => {
          if (socket?.readyState === WebSocket.OPEN) socket.send('ping')
        }, 25_000)
      })
      socket.addEventListener('message', (message) => {
        try {
          const parsed: unknown = JSON.parse(String(message.data))
          if (!isEventEnvelope(parsed)) return
          lastEventId.current = parsed.event_id
          setEvents((current) => [parsed, ...current].slice(0, 100))
          invalidateFor(parsed)
        } catch {
          // Une trame non JSON ou d'une version inconnue est ignorée sans casser le flux.
        }
      })
      socket.addEventListener('close', () => {
        if (disposed) return
        if (heartbeatTimer !== null) window.clearInterval(heartbeatTimer)
        setStatus('disconnected')
        const delay = Math.min(30_000, 750 * 2 ** attempt) + Math.random() * 400
        attempt += 1
        retryTimer = window.setTimeout(open, delay)
      })
      socket.addEventListener('error', () => socket?.close())
    }

    open()
    return () => {
      disposed = true
      if (retryTimer !== null) window.clearTimeout(retryTimer)
      if (heartbeatTimer !== null) window.clearInterval(heartbeatTimer)
      socket?.close(1000, 'navigation')
    }
  }, [generation, invalidateFor])

  const value = useMemo(() => ({ status, events, reconnect: connect }), [connect, events, status])
  return <RealtimeContext value={value}>{children}</RealtimeContext>
}

export function useRealtime(): RealtimeContextValue {
  const context = use(RealtimeContext)
  if (!context) throw new Error('useRealtime doit être utilisé dans RealtimeProvider')
  return context
}
