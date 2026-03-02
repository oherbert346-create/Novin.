import { useEffect, useRef, useState, useCallback } from 'react'
import type { Verdict } from '../types'

const WS_URL = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/ws/events`
const MAX_ALERTS = 200

export function useAlertSocket() {
  const [alerts, setAlerts] = useState<Verdict[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    const ws = new WebSocket(WS_URL)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
    }

    ws.onmessage = (evt) => {
      try {
        const verdict: Verdict = JSON.parse(evt.data)
        setAlerts((prev) => {
          const next = [verdict, ...prev]
          return next.slice(0, MAX_ALERTS)
        })
      } catch {
        // ignore malformed messages
      }
    }

    ws.onclose = () => {
      setConnected(false)
      reconnectTimer.current = setTimeout(connect, 2000)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const clearAlerts = useCallback(() => setAlerts([]), [])

  return { alerts, connected, clearAlerts }
}
