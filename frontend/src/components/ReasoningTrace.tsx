import { useState } from 'react'
import { ChevronDown, ChevronUp, AlertTriangle, ShieldCheck, HelpCircle } from 'lucide-react'
import type { AgentOutput } from '../types'
import { verdictColor } from '../lib/utils'

interface Props {
  agentOutputs: AgentOutput[]
}

const VERDICT_ICON = {
  alert: <AlertTriangle className="w-3.5 h-3.5" />,
  suppress: <ShieldCheck className="w-3.5 h-3.5" />,
  uncertain: <HelpCircle className="w-3.5 h-3.5" />,
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color =
    pct >= 70 ? 'bg-red-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-green-500'
  return (
    <div className="flex items-center gap-2 mt-1">
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-400 w-8 text-right">{pct}%</span>
    </div>
  )
}

function ChainNotes({ notes }: { notes: Record<string, unknown> }) {
  if (!notes || Object.keys(notes).length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {Object.entries(notes).map(([k, v]) => (
        <span
          key={k}
          className="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700"
        >
          <span className="text-gray-500">{k}:</span>{' '}
          <span className="text-gray-200">{String(v)}</span>
        </span>
      ))}
    </div>
  )
}

function AgentCard({ output }: { output: AgentOutput }) {
  const [open, setOpen] = useState(false)
  const isAdversarial = output.agent_id === 'adversarial_challenger'

  return (
    <div
      className={`rounded-lg border p-3 ${
        isAdversarial
          ? 'border-amber-500/30 bg-amber-900/10'
          : 'border-gray-700/50 bg-gray-900/50'
      }`}
    >
      <div
        className="flex items-center justify-between cursor-pointer"
        onClick={() => setOpen((o) => !o)}
      >
        <div className="flex items-center gap-2 min-w-0">
          <span
            className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border font-medium ${verdictColor(output.verdict)}`}
          >
            {VERDICT_ICON[output.verdict]}
            {output.verdict}
          </span>
          <span className="text-sm text-gray-300 truncate">{output.role}</span>
          {isAdversarial && (
            <span className="text-xs text-amber-400 font-medium">⚔ Challenger</span>
          )}
        </div>
        <div className="flex items-center gap-2 ml-2 shrink-0">
          <span className="text-xs text-gray-500">{Math.round(output.confidence * 100)}%</span>
          {open ? (
            <ChevronUp className="w-3.5 h-3.5 text-gray-500" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 text-gray-500" />
          )}
        </div>
      </div>

      <ConfidenceBar value={output.confidence} />

      {open && (
        <div className="mt-3 space-y-2">
          <p className="text-xs text-gray-300 leading-relaxed">{output.rationale}</p>
          <ChainNotes notes={output.chain_notes} />
        </div>
      )}
    </div>
  )
}

export function ReasoningTrace({ agentOutputs }: Props) {
  if (!agentOutputs || agentOutputs.length === 0) return null

  const alertCount = agentOutputs.filter((o) => o.verdict === 'alert').length
  const suppressCount = agentOutputs.filter((o) => o.verdict === 'suppress').length
  const challenger = agentOutputs.find((o) => o.agent_id === 'adversarial_challenger')
  const arbiterOverrode = challenger?.verdict === 'suppress' && alertCount > suppressCount

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Agent Reasoning Chain
        </h4>
        <div className="flex gap-2 text-xs">
          <span className="text-red-400">{alertCount} alert</span>
          <span className="text-gray-600">·</span>
          <span className="text-green-400">{suppressCount} suppress</span>
        </div>
      </div>

      {arbiterOverrode && (
        <div className="text-xs text-amber-400 bg-amber-900/20 border border-amber-500/30 rounded px-2 py-1">
          ⚔ Adversarial challenger argued suppress — arbiter overrode based on weighted consensus
        </div>
      )}

      <div className="space-y-1.5">
        {agentOutputs.map((output) => (
          <AgentCard key={output.agent_id} output={output} />
        ))}
      </div>
    </div>
  )
}
