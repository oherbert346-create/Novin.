import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function severityColor(severity: string): string {
  switch (severity) {
    case 'critical': return 'text-red-300 bg-red-900/40 border-red-700'
    case 'high':     return 'text-red-400 bg-red-500/20 border-red-500/30'
    case 'medium':   return 'text-orange-400 bg-orange-500/20 border-orange-500/30'
    case 'low':      return 'text-yellow-400 bg-yellow-500/20 border-yellow-500/30'
    default:         return 'text-green-400 bg-green-500/20 border-green-500/30'
  }
}

export function severityDot(severity: string): string {
  switch (severity) {
    case 'critical': return 'bg-red-400 animate-pulse'
    case 'high':     return 'bg-red-500'
    case 'medium':   return 'bg-orange-500'
    case 'low':      return 'bg-yellow-500'
    default:         return 'bg-green-500'
  }
}

export function verdictColor(verdict: string): string {
  switch (verdict) {
    case 'alert':    return 'text-red-400 bg-red-500/15 border-red-500/30'
    case 'suppress': return 'text-green-400 bg-green-500/15 border-green-500/30'
    default:         return 'text-gray-400 bg-gray-500/15 border-gray-500/30'
  }
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' })
}
