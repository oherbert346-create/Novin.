import { useState, useEffect } from 'react'
import { Plus, Play, Square, Trash2, Radio, VideoOff, Loader2 } from 'lucide-react'
import type { Stream } from '../types'
import { fetchStreams, createStream, startStream, stopStream, deleteStream } from '../lib/api'

export function StreamManager() {
  const [streams, setStreams] = useState<Stream[]>([])
  const [loading, setLoading] = useState(false)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({ uri: '', label: '', site_id: 'default', zone: 'general' })
  const [busy, setBusy] = useState<string | null>(null)

  const load = async () => {
    try {
      setLoading(true)
      setStreams(await fetchStreams())
    } catch {
      // ignore
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 10000)
    return () => clearInterval(t)
  }, [])

  const handleCreate = async () => {
    if (!form.uri || !form.label) return
    setBusy('create')
    try {
      const s = await createStream(form)
      setStreams((p) => [s, ...p])
      setForm({ uri: '', label: '', site_id: 'default', zone: 'general' })
      setShowForm(false)
    } finally {
      setBusy(null)
    }
  }

  const handleStart = async (id: string) => {
    setBusy(id)
    try {
      const s = await startStream(id)
      setStreams((p) => p.map((x) => (x.id === id ? s : x)))
    } finally {
      setBusy(null)
    }
  }

  const handleStop = async (id: string) => {
    setBusy(id)
    try {
      const s = await stopStream(id)
      setStreams((p) => p.map((x) => (x.id === id ? s : x)))
    } finally {
      setBusy(null)
    }
  }

  const handleDelete = async (id: string) => {
    setBusy(id)
    try {
      await deleteStream(id)
      setStreams((p) => p.filter((x) => x.id !== id))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <div className="flex items-center gap-2">
          <Radio className="w-4 h-4 text-gray-400" />
          <h2 className="text-sm font-semibold text-gray-200">Streams</h2>
          <span className="text-xs text-gray-600">{streams.filter((s) => s.active).length} live</span>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="p-1 rounded hover:bg-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>

      {/* Add form */}
      {showForm && (
        <div className="px-3 py-3 border-b border-gray-800 space-y-2 bg-gray-900/60">
          <input
            className="w-full text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
            placeholder="URI (rtsp://, http://, /path/to/file, 0)"
            value={form.uri}
            onChange={(e) => setForm((f) => ({ ...f, uri: e.target.value }))}
          />
          <input
            className="w-full text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
            placeholder="Label (e.g. Main Entrance)"
            value={form.label}
            onChange={(e) => setForm((f) => ({ ...f, label: e.target.value }))}
          />
          <div className="flex gap-2">
            <input
              className="flex-1 text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
              placeholder="Site ID"
              value={form.site_id}
              onChange={(e) => setForm((f) => ({ ...f, site_id: e.target.value }))}
            />
            <input
              className="flex-1 text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500"
              placeholder="Zone"
              value={form.zone}
              onChange={(e) => setForm((f) => ({ ...f, zone: e.target.value }))}
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleCreate}
              disabled={busy === 'create'}
              className="flex-1 text-xs py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white font-medium transition-colors disabled:opacity-50"
            >
              {busy === 'create' ? 'Adding...' : 'Add Stream'}
            </button>
            <button
              onClick={() => setShowForm(false)}
              className="text-xs px-3 py-1.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-400 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Stream list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1.5">
        {loading && streams.length === 0 ? (
          <div className="flex items-center justify-center py-8 text-gray-600">
            <Loader2 className="w-4 h-4 animate-spin" />
          </div>
        ) : streams.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 text-gray-600 gap-2">
            <VideoOff className="w-6 h-6" />
            <p className="text-xs">No streams configured</p>
          </div>
        ) : (
          streams.map((stream) => (
            <div
              key={stream.id}
              className={`rounded-lg border p-2.5 ${
                stream.active
                  ? 'border-green-500/30 bg-green-900/10'
                  : 'border-gray-700/40 bg-gray-900/40'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                        stream.active ? 'bg-green-400 animate-pulse' : 'bg-gray-600'
                      }`}
                    />
                    <span className="text-xs font-medium text-gray-200 truncate">{stream.label}</span>
                  </div>
                  <p className="text-xs text-gray-600 truncate mt-0.5 ml-3">{stream.uri}</p>
                  <p className="text-xs text-gray-700 ml-3">{stream.zone} · {stream.site_id}</p>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {stream.active ? (
                    <button
                      onClick={() => handleStop(stream.id)}
                      disabled={busy === stream.id}
                      className="p-1 rounded hover:bg-gray-800 text-orange-400 hover:text-orange-300 transition-colors disabled:opacity-50"
                      title="Stop"
                    >
                      {busy === stream.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Square className="w-3.5 h-3.5" />}
                    </button>
                  ) : (
                    <button
                      onClick={() => handleStart(stream.id)}
                      disabled={busy === stream.id}
                      className="p-1 rounded hover:bg-gray-800 text-green-400 hover:text-green-300 transition-colors disabled:opacity-50"
                      title="Start"
                    >
                      {busy === stream.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(stream.id)}
                    disabled={busy === stream.id}
                    className="p-1 rounded hover:bg-gray-800 text-gray-600 hover:text-red-400 transition-colors disabled:opacity-50"
                    title="Delete"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
