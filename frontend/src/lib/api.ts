import type { Stream, SystemStatus, Verdict } from '../types'

const BASE = '/api'

export async function fetchStreams(): Promise<Stream[]> {
  const res = await fetch(`${BASE}/streams`)
  if (!res.ok) throw new Error('Failed to fetch streams')
  return res.json()
}

export async function createStream(data: {
  uri: string
  label: string
  site_id: string
  zone: string
}): Promise<Stream> {
  const res = await fetch(`${BASE}/streams`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to create stream')
  return res.json()
}

export async function startStream(id: string): Promise<Stream> {
  const res = await fetch(`${BASE}/streams/${id}/start`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to start stream')
  return res.json()
}

export async function stopStream(id: string): Promise<Stream> {
  const res = await fetch(`${BASE}/streams/${id}/stop`, { method: 'POST' })
  if (!res.ok) throw new Error('Failed to stop stream')
  return res.json()
}

export async function deleteStream(id: string): Promise<void> {
  const res = await fetch(`${BASE}/streams/${id}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete stream')
}

export async function fetchEvents(params?: {
  stream_id?: string
  severity?: string
  action?: string
  limit?: number
}): Promise<Verdict[]> {
  const q = new URLSearchParams()
  if (params?.stream_id) q.set('stream_id', params.stream_id)
  if (params?.severity) q.set('severity', params.severity)
  if (params?.action) q.set('action', params.action)
  if (params?.limit) q.set('limit', String(params.limit))
  const res = await fetch(`${BASE}/events?${q}`)
  if (!res.ok) throw new Error('Failed to fetch events')
  return res.json()
}

export async function fetchStatus(): Promise<SystemStatus> {
  const res = await fetch(`${BASE}/status`)
  if (!res.ok) throw new Error('Failed to fetch status')
  return res.json()
}

export async function ingestFrame(data: {
  b64_frame: string
  stream_id: string
  label?: string
  site_id?: string
  zone?: string
}): Promise<Verdict> {
  const res = await fetch(`${BASE}/ingest/frame`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error('Failed to ingest frame')
  return res.json()
}
