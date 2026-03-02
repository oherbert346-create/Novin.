import { useState } from 'react'
import { ChevronDown, ChevronUp, ShieldAlert, ShieldCheck, Clock, Camera } from 'lucide-react'
import type { Verdict } from '../types'
import { severityColor, severityDot, formatTime, formatDate } from '../lib/utils'
import { ReasoningTrace } from './ReasoningTrace'

interface Props {
  verdict: Verdict
}

export function AlertCard({ verdict }: Props) {
  const [expanded, setExpanded] = useState(false)
  const isAlert = verdict.action === 'alert'
  const headline = verdict.summary?.trim() || verdict.description
  const narrativeLines = verdict.narrative_summary
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)

  return (
    <div
      className={`rounded-xl border transition-all ${
        isAlert
          ? 'border-red-500/30 bg-gray-900/80'
          : 'border-gray-700/30 bg-gray-900/40'
      }`}
    >
      {/* Header row */}
      <div
        className="flex items-start gap-3 p-3 cursor-pointer"
        onClick={() => setExpanded((e) => !e)}
      >
        {/* Action icon */}
        <div className="shrink-0 mt-0.5">
          {isAlert ? (
            <ShieldAlert className="w-4 h-4 text-red-400" />
          ) : (
            <ShieldCheck className="w-4 h-4 text-green-500" />
          )}
        </div>

        {/* Main content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            {/* Severity badge */}
            <span
              className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border font-semibold ${severityColor(verdict.severity)}`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${severityDot(verdict.severity)}`} />
              {verdict.severity.toUpperCase()}
            </span>

            {/* Categories */}
            {verdict.categories.filter((c) => c !== 'clear').map((cat) => (
              <span
                key={cat}
                className="text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700"
              >
                {cat}
              </span>
            ))}

            {/* Confidence */}
            <span className="text-xs text-gray-500 ml-auto">
              {Math.round(verdict.final_confidence * 100)}% conf
            </span>
          </div>

          <p className="text-sm text-gray-100 mt-1 line-clamp-2">{headline}</p>
          {verdict.description && verdict.description !== headline && (
            <p className="text-xs text-gray-400 mt-1 truncate">Observed: {verdict.description}</p>
          )}

          <div className="flex items-center gap-3 mt-1.5 text-xs text-gray-500">
            <span className="flex items-center gap-1">
              <Camera className="w-3 h-3" />
              {verdict.stream_id.slice(0, 8)}
            </span>
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {formatDate(verdict.timestamp)} {formatTime(verdict.timestamp)}
            </span>
          </div>
        </div>

        {/* Expand toggle */}
        <div className="shrink-0 text-gray-600">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="border-t border-gray-800 p-3 space-y-4">
          {/* Thumbnail */}
          {verdict.b64_thumbnail && (
            <div className="relative">
              <img
                src={`data:image/jpeg;base64,${verdict.b64_thumbnail}`}
                alt="Frame thumbnail"
                className="w-full rounded-lg object-cover max-h-56"
              />
              {/* BBox overlays */}
              {verdict.bbox.length > 0 && (
                <div className="absolute inset-0">
                  <svg viewBox="0 0 1 1" className="w-full h-full" preserveAspectRatio="none">
                    {verdict.bbox.map((b, i) => (
                      <rect
                        key={i}
                        x={b.x1}
                        y={b.y1}
                        width={b.x2 - b.x1}
                        height={b.y2 - b.y1}
                        fill="none"
                        stroke={isAlert ? '#ef4444' : '#22c55e'}
                        strokeWidth="0.005"
                        vectorEffect="non-scaling-stroke"
                      />
                    ))}
                  </svg>
                </div>
              )}
            </div>
          )}

          {/* Operator narrative */}
          {verdict.narrative_summary && (
            <div className="text-xs rounded-lg px-3 py-2 border bg-blue-900/20 border-blue-500/20 text-blue-200">
              <span className="font-semibold">Operator summary:</span>
              <div className="mt-1.5 space-y-1 leading-relaxed">
                {narrativeLines.map((line, index) => (
                  <p key={`${verdict.frame_id}-narrative-${index}`} className="text-xs">
                    {line}
                  </p>
                ))}
              </div>
            </div>
          )}
          
          {/* Alert/suppress reason */}
          {(verdict.alert_reason || verdict.suppress_reason) && (
            <div
              className={`text-xs rounded-lg px-3 py-2 border ${
                isAlert
                  ? 'text-red-300 bg-red-900/20 border-red-500/20'
                  : 'text-green-300 bg-green-900/20 border-green-500/20'
              }`}
            >
              <span className="font-semibold">Why this decision: </span>
              {verdict.alert_reason || verdict.suppress_reason}
            </div>
          )}

          {/* Reasoning trace */}
          <ReasoningTrace agentOutputs={verdict.agent_outputs} />
        </div>
      )}
    </div>
  )
}
