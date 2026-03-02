import { useRef, useEffect, useState } from 'react'
import { Camera, WifiOff } from 'lucide-react'
import type { Verdict } from '../types'
import { severityColor, severityDot } from '../lib/utils'

interface Props {
  latestVerdict: Verdict | null
  streamLabel?: string
}

export function LiveFeed({ latestVerdict, streamLabel }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement>(new Image())
  const [hasFrame, setHasFrame] = useState(false)

  useEffect(() => {
    if (!latestVerdict?.b64_thumbnail) return

    const img = imgRef.current
    img.onload = () => {
      const canvas = canvasRef.current
      if (!canvas) return
      const ctx = canvas.getContext('2d')
      if (!ctx) return

      canvas.width = img.naturalWidth
      canvas.height = img.naturalHeight
      ctx.drawImage(img, 0, 0)

      // Draw bounding boxes
      if (latestVerdict.bbox.length > 0) {
        const color = latestVerdict.action === 'alert' ? '#ef4444' : '#22c55e'
        ctx.strokeStyle = color
        ctx.lineWidth = Math.max(2, img.naturalWidth * 0.003)
        ctx.shadowColor = color
        ctx.shadowBlur = 8

        for (const b of latestVerdict.bbox) {
          const x = b.x1 * img.naturalWidth
          const y = b.y1 * img.naturalHeight
          const w = (b.x2 - b.x1) * img.naturalWidth
          const h = (b.y2 - b.y1) * img.naturalHeight
          ctx.strokeRect(x, y, w, h)
        }
      }

      setHasFrame(true)
    }
    img.src = `data:image/jpeg;base64,${latestVerdict.b64_thumbnail}`
  }, [latestVerdict])

  const isAlert = latestVerdict?.action === 'alert'

  return (
    <div className="relative w-full h-full flex flex-col bg-gray-950">
      {/* Top overlay bar */}
      <div className="absolute top-0 left-0 right-0 z-10 flex items-center justify-between px-3 py-2 bg-gradient-to-b from-gray-950/80 to-transparent">
        <div className="flex items-center gap-2">
          <Camera className="w-3.5 h-3.5 text-gray-400" />
          <span className="text-xs text-gray-300 font-medium">{streamLabel || 'Live Feed'}</span>
        </div>
        {latestVerdict && (
          <span
            className={`text-xs px-2 py-0.5 rounded-full border font-semibold ${severityColor(latestVerdict.severity)}`}
          >
            <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1 ${severityDot(latestVerdict.severity)}`} />
            {latestVerdict.severity.toUpperCase()}
          </span>
        )}
      </div>

      {/* Canvas / placeholder */}
      <div className="flex-1 relative overflow-hidden flex items-center justify-center bg-gray-900">
        {hasFrame ? (
          <canvas
            ref={canvasRef}
            className="w-full h-full object-contain"
          />
        ) : (
          <div className="flex flex-col items-center gap-3 text-gray-700">
            <WifiOff className="w-10 h-10" />
            <p className="text-sm">Waiting for frames...</p>
          </div>
        )}

        {/* Alert flash overlay */}
        {isAlert && latestVerdict && (
          <div
            className="absolute inset-0 pointer-events-none rounded"
            style={{
              boxShadow: 'inset 0 0 30px rgba(239, 68, 68, 0.35)',
              animation: 'pulse 1.5s ease-in-out infinite',
            }}
          />
        )}
      </div>

      {/* Bottom overlay */}
      {latestVerdict && (
        <div className="absolute bottom-0 left-0 right-0 z-10 px-3 py-2 bg-gradient-to-t from-gray-950/90 to-transparent">
          <p className="text-xs text-gray-300 truncate">{latestVerdict.description}</p>
          <div className="flex items-center gap-2 mt-0.5">
            <span
              className={`text-xs font-semibold ${
                isAlert ? 'text-red-400' : 'text-green-400'
              }`}
            >
              {isAlert ? '⚠ ALERT' : '✓ SUPPRESSED'}
            </span>
            <span className="text-xs text-gray-600">
              {Math.round(latestVerdict.final_confidence * 100)}% confidence
            </span>
            <span className="text-xs text-gray-700 ml-auto">
              {latestVerdict.categories.filter((c) => c !== 'clear').join(', ')}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}
