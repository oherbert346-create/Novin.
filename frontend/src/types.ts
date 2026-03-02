export interface BoundingBox {
  x1: number
  y1: number
  x2: number
  y2: number
}

export interface AgentOutput {
  agent_id: string
  role: string
  verdict: 'alert' | 'suppress' | 'uncertain'
  confidence: number
  rationale: string
  chain_notes: Record<string, unknown>
}

export interface Verdict {
  frame_id: string
  stream_id: string
  timestamp: string
  action: 'alert' | 'suppress'
  final_confidence: number
  summary: string
  narrative_summary: string
  severity: 'none' | 'low' | 'medium' | 'high' | 'critical'
  categories: string[]
  description: string
  bbox: BoundingBox[]
  b64_thumbnail: string
  agent_outputs: AgentOutput[]
  alert_reason: string | null
  suppress_reason: string | null
}

export interface Stream {
  id: string
  uri: string
  label: string
  site_id: string
  zone: string
  created_at: string
  active: boolean
}

export interface SystemStatus {
  active_streams: number
  active_stream_ids: string[]
  ws_connections: number
  vision_model: string
  reasoning_model: string
}
