/**
 * EventCard - Hiển thị chi tiết 1 sự kiện đã chuẩn hóa
 */
import { useState } from 'react'
import type { NormalizedEvent } from '../types/event'
import {
  EVENT_TYPE_ICONS,
  PRIORITY_COLORS,
  TOPIC_COLORS,
} from '../types/event'

interface EventCardProps {
  event: NormalizedEvent
  isNew?: boolean
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('vi-VN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      fractionalSecondDigits: 3,
    })
  } catch {
    return iso
  }
}

function formatPayload(payload: Record<string, unknown>): string {
  return JSON.stringify(payload, null, 2)
}

export function EventCard({ event, isNew = false }: EventCardProps) {
  const [expanded, setExpanded] = useState(false)

  const icon        = EVENT_TYPE_ICONS[event.type] ?? '📋'
  const topicClass  = TOPIC_COLORS[event.topic] ?? 'bg-slate-500/20 text-slate-300 border-slate-500/30'
  const priorityCls = PRIORITY_COLORS[event.priority] ?? 'text-slate-400'
  const hasPayload  = Object.keys(event.payload).length > 0

  return (
    <div
      className={`
        border border-slate-700 rounded-lg bg-slate-800/60 overflow-hidden
        transition-all duration-200 hover:border-slate-600
        ${isNew ? 'event-enter' : ''}
      `}
    >
      {/* Header row */}
      <div
        className="flex items-center gap-3 px-3 py-2 cursor-pointer select-none"
        onClick={() => hasPayload && setExpanded(v => !v)}
      >
        {/* Icon */}
        <span className="text-lg leading-none w-6 text-center flex-shrink-0">{icon}</span>

        {/* Type + Source */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm text-white truncate">{event.type}</span>
            <span className={`text-xs font-medium ${priorityCls}`}>
              [{event.priority}]
            </span>
          </div>
          <div className="text-xs text-slate-500 truncate">
            <span className="text-slate-400">{event.source}</span>
            <span className="mx-1">·</span>
            <span className="font-mono">{event.id.slice(0, 8)}…</span>
          </div>
        </div>

        {/* Topic badge */}
        <span
          className={`
            flex-shrink-0 text-xs px-2 py-0.5 rounded-full border font-medium
            ${topicClass}
          `}
        >
          {event.topic}
        </span>

        {/* Timestamp */}
        <span className="flex-shrink-0 text-xs font-mono text-slate-500 text-right w-28">
          {formatTime(event.timestamp)}
        </span>

        {/* Expand arrow */}
        {hasPayload && (
          <span
            className={`flex-shrink-0 text-slate-500 text-xs transition-transform ${expanded ? 'rotate-180' : ''}`}
          >
            ▾
          </span>
        )}
      </div>

      {/* Expanded payload */}
      {expanded && hasPayload && (
        <div className="border-t border-slate-700 px-3 py-2 bg-slate-900/60">
          <pre className="text-xs text-slate-300 font-mono overflow-x-auto whitespace-pre-wrap break-all leading-relaxed">
            {formatPayload(event.payload)}
          </pre>
          <div className="mt-1 pt-1 border-t border-slate-800 text-xs text-slate-600 font-mono">
            received: {event.metadata.received_at} · v{event.metadata.version}
          </div>
        </div>
      )}
    </div>
  )
}
