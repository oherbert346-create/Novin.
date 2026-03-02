import { useState } from 'react'
import { Bell, BellOff, Trash2, Filter } from 'lucide-react'
import type { Verdict } from '../types'
import { AlertCard } from './AlertCard'

interface Props {
  alerts: Verdict[]
  onClear: () => void
}

const FILTER_OPTIONS = ['all', 'alert', 'suppress'] as const
const SEVERITY_OPTIONS = ['all', 'critical', 'high', 'medium', 'low', 'none'] as const

export function AlertFeed({ alerts, onClear }: Props) {
  const [actionFilter, setActionFilter] = useState<string>('all')
  const [severityFilter, setSeverityFilter] = useState<string>('all')

  const filtered = alerts.filter((a) => {
    if (actionFilter !== 'all' && a.action !== actionFilter) return false
    if (severityFilter !== 'all' && a.severity !== severityFilter) return false
    return true
  })

  const alertCount = alerts.filter((a) => a.action === 'alert').length

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Bell className="w-4 h-4 text-gray-400" />
          <h2 className="text-sm font-semibold text-gray-200">Alert Feed</h2>
          {alertCount > 0 && (
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-red-500/20 text-red-400 border border-red-500/30 font-medium">
              {alertCount}
            </span>
          )}
        </div>
        <button
          onClick={onClear}
          className="text-gray-600 hover:text-gray-400 transition-colors"
          title="Clear all"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-800/50">
        <Filter className="w-3 h-3 text-gray-600 shrink-0" />
        <div className="flex gap-1 flex-wrap">
          {FILTER_OPTIONS.map((f) => (
            <button
              key={f}
              onClick={() => setActionFilter(f)}
              className={`text-xs px-2 py-0.5 rounded transition-colors ${
                actionFilter === f
                  ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {f}
            </button>
          ))}
          <span className="text-gray-700">|</span>
          {SEVERITY_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => setSeverityFilter(s)}
              className={`text-xs px-2 py-0.5 rounded transition-colors ${
                severityFilter === s
                  ? 'bg-purple-500/20 text-purple-400 border border-purple-500/30'
                  : 'text-gray-500 hover:text-gray-300'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* Feed */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-gray-600 gap-2">
            <BellOff className="w-8 h-8" />
            <p className="text-sm">No events yet</p>
          </div>
        ) : (
          filtered.map((verdict) => (
            <AlertCard key={verdict.frame_id} verdict={verdict} />
          ))
        )}
      </div>

      {/* Count footer */}
      {filtered.length > 0 && (
        <div className="px-4 py-2 border-t border-gray-800 text-xs text-gray-600">
          Showing {filtered.length} of {alerts.length} events
        </div>
      )}
    </div>
  )
}
