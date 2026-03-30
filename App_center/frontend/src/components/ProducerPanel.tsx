/**
 * ProducerPanel - Panel test gửi events tới Event Hub từ browser
 * Hỗ trợ: WebSocket hoặc REST HTTP
 */
import { useEffect, useRef, useState } from 'react'
import type { EventPriority, EventType } from '../types/event'
import { EVENT_TYPE_ICONS } from '../types/event'


type SendMode = 'websocket' | 'rest'

interface LogEntry {
  id: number
  time: string
  ok: boolean
  message: string
}

const PRESET_EVENTS: { label: string; type: EventType; source: string; payload: Record<string, unknown> }[] = [
  {
    label: '👤 Face Recognition',
    type: 'face_recognition',
    source: 'camera_entrance_01',
    payload: {
      person_id:   'EMP001',
      person_name: 'Nguyen Van A',
      confidence:  0.97,
      action:      'entry',
      location:    'main_entrance',
      camera_id:   'CAM-001',
    },
  },
  {
    label: '🖐 Fingerprint',
    type: 'fingerprint',
    source: 'fingerprint_reader_01',
    payload: {
      person_id:   'EMP001',
      person_name: 'Nguyen Van A',
      finger_id:   3,
      confidence:  0.99,
      action:      'entry',
      location:    'main_entrance',
      reader_id:   'FP-001',
    },
  },
]

let logCounter = 0

export function ProducerPanel() {
  const [mode, setMode]         = useState<SendMode>('websocket')
  const [wsStatus, setWsStatus] = useState<'disconnected' | 'connected' | 'connecting'>('disconnected')
  const [logs, setLogs]         = useState<LogEntry[]>([])
  const [source, setSource]     = useState('')
  const [eventType, setEventType] = useState<string>('')
  const [priority, setPriority] = useState<EventPriority | ''>('medium')
  const [payloadText, setPayloadText] = useState('')
  const [payloadError, setPayloadError] = useState('')

  const wsRef = useRef<WebSocket | null>(null)

  // ── WebSocket connection ──
  useEffect(() => {
    if (mode !== 'websocket') return
    connectWs()
    return () => {
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close() }
    }
  }, [mode])

  function connectWs() {
    if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close() }
    setWsStatus('connecting')
    const ws = new WebSocket(`ws://localhost:8000/ws/producer`)
    wsRef.current = ws
    ws.onopen  = () => setWsStatus('connected')
    ws.onerror = () => setWsStatus('disconnected')
    ws.onclose = () => setWsStatus('disconnected')
    ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data)
        addLog(data.status === 'ok', `ACK: event_id=${data.event_id} topic=${data.topic}`)
      } catch { /* ignore */ }
    }
  }

  function addLog(ok: boolean, message: string) {
    const now = new Date().toLocaleTimeString('vi-VN', { hour12: false })
    setLogs(prev => [{ id: ++logCounter, time: now, ok, message }, ...prev].slice(0, 30))
  }

  function clearLogs() {
    setLogs([])
  }

  function validatePayload(): Record<string, unknown> | null {
    try {
      const parsed = JSON.parse(payloadText)
      setPayloadError('')
      return parsed
    } catch (e) {
      setPayloadError('Invalid JSON')
      return null
    }
  }

  async function sendEvent() {
    if (!source.trim()) { addLog(false, 'Source is required'); return }
    if (!eventType) { addLog(false, 'Event Type is required'); return }

    const payload = validatePayload()
    if (!payload) return

    const eventData: Record<string, unknown> = {
      source: source.trim(),
      type: eventType,
      payload,
    }
    if (priority) eventData.priority = priority

    if (mode === 'websocket') {
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        addLog(false, 'WebSocket not connected')
        return
      }
      wsRef.current.send(JSON.stringify(eventData))
      addLog(true, `Sent: ${eventType} via WebSocket`)
    } else {
      // REST
      try {
        const res = await fetch('http://localhost:8000/events/ingest', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(eventData),
        })
        const data = await res.json()
        if (data.success) {
          addLog(true, `REST OK: event_id=${data.event_id}`)
        } else {
          addLog(false, `REST error: ${data.message}`)
        }
      } catch (e) {
        addLog(false, `REST failed: ${e}`)
      }
    }
  }

  function loadPreset(preset: typeof PRESET_EVENTS[0]) {
    setSource(preset.source)
    setEventType(preset.type)
    setPriority('medium')
    setPayloadText(JSON.stringify(preset.payload, null, 2))
    setPayloadError('')
  }

  function resetForm() {
    setSource('')
    setEventType('')
    setPriority('medium')
    setPayloadText('')
    setPayloadError('')
  }

  return (
    <div className="flex flex-col h-full bg-slate-900 text-sm">
      {/* Header */}
      <div className="px-3 py-2 border-b border-slate-700 bg-slate-800">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-white text-xs">🚀 Producer Test Panel</span>
          {/* Mode toggle */}
          <div className="flex gap-1">
            {(['websocket', 'rest'] as SendMode[]).map(m => (
              <button
                key={m}
                onClick={() => setMode(m)}
                className={`px-2 py-0.5 text-xs rounded transition-colors ${
                  mode === m
                    ? 'bg-sky-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {m === 'websocket' ? 'WS' : 'REST'}
              </button>
            ))}
          </div>
        </div>
        {mode === 'websocket' && (
          <div className="mt-1 flex items-center gap-1.5">
            <span className={`w-1.5 h-1.5 rounded-full ${
              wsStatus === 'connected' ? 'bg-green-400 pulse-dot'
              : wsStatus === 'connecting' ? 'bg-yellow-400 pulse-dot'
              : 'bg-slate-500'
            }`} />
            <span className={`text-xs ${
              wsStatus === 'connected' ? 'text-green-400'
              : wsStatus === 'connecting' ? 'text-yellow-400'
              : 'text-slate-500'
            }`}>{wsStatus}</span>
            {wsStatus === 'disconnected' && (
              <button onClick={connectWs} className="text-xs text-sky-400 hover:text-sky-300 ml-1">reconnect</button>
            )}
          </div>
        )}
      </div>

      {/* Presets */}
      <div className="px-3 py-2 border-b border-slate-700">
        <p className="text-xs text-slate-500 mb-1.5">Quick presets:</p>
        <div className="flex flex-col gap-1">
          {PRESET_EVENTS.map(preset => (
            <button
              key={preset.type + preset.label}
              onClick={() => loadPreset(preset)}
              className="text-left px-2 py-1 text-xs text-slate-300 hover:text-white hover:bg-slate-700 rounded transition-colors"
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      {/* Form */}
      <div className="px-3 py-2 border-b border-slate-700 flex flex-col gap-2">
        {/* Source */}
        <div>
          <label className="text-xs text-slate-500 block mb-1">Source</label>
          <input
            value={source}
            onChange={e => setSource(e.target.value)}
            className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-white font-mono focus:outline-none focus:border-sky-500"
          />
        </div>

        {/* Type + Priority */}
        <div className="flex gap-2">
          <div className="flex-1">
            <label className="text-xs text-slate-500 block mb-1">Event Type</label>
            <select
              value={eventType}
              onChange={e => setEventType(e.target.value)}
              className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-sky-500"
            >
              <option value="">-- select type --</option>
              {Object.entries(EVENT_TYPE_ICONS).map(([type, icon]) => (
                <option key={type} value={type}>{icon} {type}</option>
              ))}
            </select>
          </div>
          <div className="w-24">
            <label className="text-xs text-slate-500 block mb-1">Priority</label>
            <select
              value={priority}
              onChange={e => setPriority(e.target.value as EventPriority | '')}
              className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-xs text-white focus:outline-none focus:border-sky-500"
            >
              <option value="">-- auto --</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="urgent">urgent</option>
            </select>
          </div>
        </div>

        {/* Payload */}
        <div>
          <label className="text-xs text-slate-500 block mb-1">
            Payload (JSON)
            {payloadError && <span className="text-red-400 ml-2">{payloadError}</span>}
          </label>
          <textarea
            value={payloadText}
            onChange={e => { setPayloadText(e.target.value); setPayloadError('') }}
            rows={4}
            className={`w-full bg-slate-800 border rounded px-2 py-1 text-xs text-green-300 font-mono focus:outline-none resize-none ${
              payloadError ? 'border-red-500' : 'border-slate-600 focus:border-sky-500'
            }`}
          />
        </div>

        {/* Send + Reset buttons */}
        <div className="flex gap-2">
          <button
            onClick={sendEvent}
            className="flex-1 py-1.5 bg-sky-600 hover:bg-sky-500 text-white text-xs font-semibold rounded transition-colors"
          >
            ⚡ Send Event
          </button>
          <button
            onClick={resetForm}
            className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs rounded transition-colors"
            title="Reset form"
          >
            ↺ Reset
          </button>
        </div>
      </div>

      {/* Logs */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        <div className="flex items-center justify-between mb-1.5">
          <p className="text-xs text-slate-500">Activity log:</p>
          {logs.length > 0 && (
            <button
              onClick={clearLogs}
              className="text-xs text-slate-500 hover:text-slate-300 transition-colors"
            >
              ✕ clear
            </button>
          )}
        </div>
        {logs.length === 0 ? (
          <p className="text-xs text-slate-600 italic">No activity yet</p>
        ) : (
          <div className="flex flex-col gap-1">
            {logs.map(log => (
              <div key={log.id} className="flex gap-1.5 text-xs font-mono">
                <span className="text-slate-600 flex-shrink-0">{log.time}</span>
                <span className={log.ok ? 'text-green-400' : 'text-red-400'}>
                  {log.ok ? '✓' : '✗'}
                </span>
                <span className={`${log.ok ? 'text-slate-300' : 'text-red-300'} break-all`}>
                  {log.message}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
