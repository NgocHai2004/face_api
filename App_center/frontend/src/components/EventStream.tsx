/**
 * EventStream - Live feed hiển thị danh sách events realtime
 *
 * Auto-scroll: chỉ kéo lên top khi user đang ở gần top (< 80px).
 * Khi user đang scroll xuống dưới → giữ nguyên vị trí, hiện nút "↑ New events".
 */
import { useEffect, useRef, useState } from 'react'
import type { NormalizedEvent } from '../types/event'
import { EventCard } from './EventCard'

interface EventStreamProps {
  events: NormalizedEvent[]
  autoScroll?: boolean
  onClear?: () => void
}

const SCROLL_THRESHOLD = 80 // px từ top để coi là "đang ở đầu"

export function EventStream({ events, autoScroll = true, onClear }: EventStreamProps) {
  const containerRef    = useRef<HTMLDivElement>(null)
  const prevCountRef    = useRef<number>(0)
  const userScrolledRef = useRef<boolean>(false)  // user đang scroll xuống?
  const [newCount, setNewCount] = useState(0)      // số event mới khi user đang scroll

  // Lắng nghe scroll của user
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onScroll = () => {
      userScrolledRef.current = el.scrollTop > SCROLL_THRESHOLD
      if (!userScrolledRef.current) {
        // User cuộn về top → reset badge
        setNewCount(0)
      }
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  // Auto-scroll hoặc đếm event mới khi user đang scroll
  useEffect(() => {
    if (!autoScroll) return
    const added = events.length - prevCountRef.current
    if (added > 0) {
      if (userScrolledRef.current) {
        // User đang scroll xuống → chỉ tăng badge, không nhảy
        setNewCount(n => n + added)
      } else if (containerRef.current) {
        // User ở đầu danh sách → tự scroll lên top
        containerRef.current.scrollTop = 0
      }
    }
    prevCountRef.current = events.length
  }, [events.length, autoScroll])

  const scrollToTop = () => {
    if (containerRef.current) containerRef.current.scrollTop = 0
    setNewCount(0)
    userScrolledRef.current = false
  }

  return (
    <div className="h-full flex flex-col overflow-hidden relative">
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
          className="flex-1 min-h-0 overflow-y-scroll px-3 py-3 flex flex-col gap-2"
          style={{ scrollbarWidth: 'thin', scrollbarColor: '#334155 transparent' }}
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

      {/* Badge "↑ N new events" khi user đang scroll xuống */}
      {newCount > 0 && (
        <button
          onClick={scrollToTop}
          className="
            absolute top-10 left-1/2 -translate-x-1/2
            flex items-center gap-1.5
            px-3 py-1 rounded-full text-xs font-medium
            bg-sky-600 hover:bg-sky-500 text-white shadow-lg
            transition-colors animate-bounce
          "
        >
          ↑ {newCount} new event{newCount !== 1 ? 's' : ''}
        </button>
      )}
    </div>
  )
}
