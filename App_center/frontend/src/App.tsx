/**
 * App.tsx - Event Hub Dashboard
 * Layout: Header | TopicFilter | StatusBar | [EventStream + ProducerPanel]
 */
import { useState } from 'react'
import { useEventHub } from './hooks/useEventHub'
import { StatusBar } from './components/StatusBar'
import { TopicFilter } from './components/TopicFilter'
import { EventStream } from './components/EventStream'
import { ProducerPanel } from './components/ProducerPanel'

export default function App() {
  const [showProducer, setShowProducer] = useState(true)

  const {
    events,
    status,
    subscribedTopic,
    eventsPerSecond,
    changeTopic,
    clearEvents,
    connect,
    disconnect,
  } = useEventHub({ topic: '*', autoConnect: true })

  return (
    <div className="h-screen flex flex-col bg-slate-900 text-slate-100 overflow-hidden">
      {/* ── Header ── */}
      <header className="flex items-center justify-between px-4 py-3 bg-slate-800 border-b border-slate-700 flex-shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-xl">⚡</span>
          <div>
            <h1 className="font-bold text-white leading-none">Event Hub</h1>
            <p className="text-xs text-slate-400 mt-0.5">Realtime Event Middleware</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowProducer(v => !v)}
            className={`
              px-3 py-1.5 text-xs rounded border transition-colors
              ${showProducer
                ? 'bg-sky-500/20 text-sky-300 border-sky-500/40'
                : 'text-slate-400 border-slate-600 hover:border-slate-500 hover:text-slate-300'
              }
            `}
          >
            🚀 Producer Panel
          </button>
          <a
            href="http://localhost:8000/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="px-3 py-1.5 text-xs text-slate-400 border border-slate-600 hover:border-slate-500 hover:text-slate-300 rounded transition-colors"
          >
            📖 API Docs
          </a>
        </div>
      </header>

      {/* ── Topic Filter ── */}
      <TopicFilter
        currentTopic={subscribedTopic}
        onChangeTopic={changeTopic}
      />

      {/* ── Status Bar ── */}
      <StatusBar
        status={status}
        subscribedTopic={subscribedTopic}
        totalEvents={events.length}
        eventsPerSecond={eventsPerSecond}
        onConnect={connect}
        onDisconnect={disconnect}
        onClear={clearEvents}
      />

      {/* ── Main content ── */}
      <div className="flex-1 flex overflow-hidden">
        {/* Event Stream */}
        <div className="flex-1 overflow-hidden">
          <EventStream events={events} autoScroll={true} onClear={clearEvents} />
        </div>

        {/* Producer Panel (collapsible) */}
        {showProducer && (
          <div className="w-80 border-l border-slate-700 flex-shrink-0 overflow-y-auto">
            <ProducerPanel />
          </div>
        )}
      </div>
    </div>
  )
}
