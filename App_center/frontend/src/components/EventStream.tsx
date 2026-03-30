/**
 * EventStream - Live feed hiển thị danh sách events realtime
 */
import { useEffect, useRef } from 'react'
import type { NormalizedEvent } from '../types/event'
import { EventCard } from './EventCard'

interface EventStreamProps {
  events: NormalizedEvent[]
  autoScroll?: boolean
  onClear?: () => void
}

export function EventStream({ events, autoScroll = true, onClear }: EventStreamProps) {
  const containerRef  = useRef<HTMLDivElement>(null)
  const prevCountRef  = useRef<number>(0)

  // Auto-scroll khi có event mới
  useEffect(() => {
    if (!autoScroll) return
    if (events.length > prevCountRef.current && containerRef.current) {
      containerRef.current.scrollTop = 0
    }
    prevCountRef.current = events.length
  }, [events.length, autoScroll])

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Toolbar — luôn hiển thị */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-slate-700 flex-shrink-0">
        <span className="text-xs text-slate-500">
          {events.length > 0 ? `${events.length} event${events.length !== 1 ? 's' : ''}` : 'No events'}
        </span>
        {onClear && events.length > 0 && (
          <button
            onClick={onClear}
            className="text-xs text-slate-500 hover:text-red-400 transition-colors flex items-center gap-1"
          >
            🗑 Clear
          </button>
        )}
      </div>

      {/* Empty state hoặc danh sách */}
      {events.length === 0 ? (
        <div className="flex flex-col items-center justify-center flex-1 text-slate-600 gap-3">
          <div className="text-5xl opacity-30">📡</div>
          <div className="text-sm">Waiting for events...</div>
          <div className="text-xs text-slate-700">
            Connect a producer to <span className="font-mono text-slate-600">ws://localhost:8000/ws/producer</span>
          </div>
        </div>
      ) : (
        <div
          ref={containerRef}
          className="flex-1 overflow-y-auto px-3 py-3 flex flex-col gap-2"
        >
          {events.map((event, index) => (
            <EventCard
              key={event.id}
              event={event}
              isNew={index === 0}
            />
          ))}
        </div>
      )}
    </div>
  )
}
