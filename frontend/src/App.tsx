import { useState, useEffect } from 'react'
import { Shield, Wifi, WifiOff, Activity, AlertTriangle } from 'lucide-react'
import { useAlertSocket } from './hooks/useAlertSocket'
import { StreamManager } from './components/StreamManager'
import { AlertFeed } from './components/AlertFeed'
import { LiveFeed } from './components/LiveFeed'
import { fetchStatus } from './lib/api'
import type { SystemStatus, Verdict } from './types'

export default function App() {
  const { alerts, connected, clearAlerts } = useAlertSocket()
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [latestAlert, setLatestAlert] = useState<Verdict | null>(null)
  const [activeStreamLabel, setActiveStreamLabel] = useState<string>('')

  // Track most recent alert verdict for live feed
  useEffect(() => {
    if (alerts.length > 0) {
      setLatestAlert(alerts[0])
    }
  }, [alerts])

  // Poll system status
  useEffect(() => {
    const load = async () => {
      try {
        setStatus(await fetchStatus())
      } catch {
        // ignore
      }
    }
    load()
    const t = setInterval(load, 5000)
    return () => clearInterval(t)
  }, [])

  const totalAlerts = alerts.filter((a) => a.action === 'alert').length
  const criticalAlerts = alerts.filter(
    (a) => a.action === 'alert' && (a.severity === 'critical' || a.severity === 'high')
  ).length

  return (
    <div className="h-screen w-screen flex flex-col bg-gray-950 overflow-hidden">
      {/* Top bar */}
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-gray-800/80 bg-gray-950/95 backdrop-blur shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Shield className="w-5 h-5 text-blue-400" />
            <span className="text-sm font-bold text-white tracking-tight">NOVIN</span>
            <span className="text-xs text-gray-600 font-medium">SECURITY</span>
          </div>
          <div className="h-4 w-px bg-gray-800" />
          <span className="text-xs text-gray-500">Multi-Agent Vision System</span>
        </div>

        <div className="flex items-center gap-4">
          {/* Alerts count */}
          {totalAlerts > 0 && (
            <div className="flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 text-red-400" />
              <span className="text-xs text-red-400 font-semibold">{totalAlerts} alerts</span>
              {criticalAlerts > 0 && (
                <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-900/50 text-red-300 border border-red-700 animate-pulse">
                  {criticalAlerts} critical
                </span>
              )}
            </div>
          )}

          {/* Active streams */}
          {status && (
            <div className="flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-gray-500" />
              <span className="text-xs text-gray-500">
                {status.active_streams} stream{status.active_streams !== 1 ? 's' : ''}
              </span>
            </div>
          )}

          {/* WS connection indicator */}
          <div className="flex items-center gap-1.5">
            {connected ? (
              <>
                <Wifi className="w-3.5 h-3.5 text-green-400" />
                <span className="text-xs text-green-400">Live</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3.5 h-3.5 text-red-400 animate-pulse" />
                <span className="text-xs text-red-400">Reconnecting</span>
              </>
            )}
          </div>

          {/* Model info */}
          {status && (
            <span className="hidden lg:block text-xs text-gray-700 font-mono">
              {status.vision_model.split('-').slice(0, 3).join('-')}
            </span>
          )}
        </div>
      </header>

      {/* Main layout: 3-column */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Stream Manager */}
        <aside className="w-64 shrink-0 border-r border-gray-800/60 flex flex-col overflow-hidden">
          <StreamManager />
        </aside>

        {/* Centre: Live Feed */}
        <main className="flex-1 flex flex-col overflow-hidden bg-gray-950">
          <LiveFeed
            latestVerdict={latestAlert}
            streamLabel={
              latestAlert
                ? latestAlert.stream_id.slice(0, 16)
                : activeStreamLabel || 'No active stream'
            }
          />

          {/* Model / latency info bar */}
          {status && (
            <div className="flex items-center gap-4 px-4 py-1.5 border-t border-gray-800/60 bg-gray-950 shrink-0">
              <span className="text-xs text-gray-700">
                Vision: <span className="text-gray-500">{status.vision_model}</span>
              </span>
              <span className="text-xs text-gray-700">
                Reasoning: <span className="text-gray-500">{status.reasoning_model}</span>
              </span>
              <span className="text-xs text-gray-700 ml-auto">
                WS clients: <span className="text-gray-500">{status.ws_connections}</span>
              </span>
            </div>
          )}
        </main>

        {/* Right: Alert Feed */}
        <aside className="w-96 shrink-0 border-l border-gray-800/60 flex flex-col overflow-hidden">
          <AlertFeed alerts={alerts} onClear={clearAlerts} />
        </aside>
      </div>
    </div>
  )
}
