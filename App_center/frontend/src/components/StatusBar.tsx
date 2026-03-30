/**
 * StatusBar - Hiển thị trạng thái kết nối WebSocket + thống kê
 */
import type { ConnectionStatus } from '../types/event'

interface StatusBarProps {
  status: ConnectionStatus
  subscribedTopic: string
  totalEvents: number
  eventsPerSecond: number
  onConnect: () => void
  onDisconnect: () => void
  onClear: () => void
}

const STATUS_CONFIG: Record<ConnectionStatus, { label: string; color: string; dot: string }> = {
  connected:    { label: 'Connected',    color: 'text-green-400',  dot: 'bg-green-400' },
  connecting:   { label: 'Connecting...', color: 'text-yellow-400', dot: 'bg-yellow-400' },
  disconnected: { label: 'Disconnected', color: 'text-gray-400',   dot: 'bg-gray-500' },
  error:        { label: 'Error',        color: 'text-red-400',    dot: 'bg-red-400' },
}

export function StatusBar({
  status,
  subscribedTopic,
  totalEvents,
  eventsPerSecond,
  onConnect,
  onDisconnect,
  onClear,
}: StatusBarProps) {
  const cfg = STATUS_CONFIG[status]

  return (
    <div className="flex items-center justify-between px-4 py-2 bg-slate-800 border-b border-slate-700 text-sm">
      {/* Left - Status */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2">
          <span
            className={`w-2 h-2 rounded-full ${cfg.dot} ${
              status === 'connected' || status === 'connecting' ? 'pulse-dot' : ''
            }`}
          />
          <span className={`font-medium ${cfg.color}`}>{cfg.label}</span>
        </div>

        <div className="text-slate-400">
          Topic: <span className="text-sky-400 font-mono">{subscribedTopic}</span>
        </div>
      </div>

      {/* Center - Stats */}
      <div className="flex items-center gap-6 text-slate-400">
        <div>
          Events: <span className="text-white font-mono">{totalEvents.toLocaleString()}</span>
        </div>
        <div>
          Rate:{' '}
          <span className={`font-mono ${eventsPerSecond > 0 ? 'text-green-400' : 'text-slate-500'}`}>
            {eventsPerSecond}/s
          </span>
        </div>
      </div>

      {/* Right - Actions */}
      <div className="flex items-center gap-2">
        <button
          onClick={onClear}
          className="px-3 py-1 text-xs text-slate-400 hover:text-white border border-slate-600 hover:border-slate-400 rounded transition-colors"
        >
          Clear
        </button>

        {status === 'connected' ? (
          <button
            onClick={onDisconnect}
            className="px-3 py-1 text-xs text-red-400 hover:text-red-300 border border-red-500/40 hover:border-red-400 rounded transition-colors"
          >
            Disconnect
          </button>
        ) : (
          <button
            onClick={onConnect}
            className="px-3 py-1 text-xs text-green-400 hover:text-green-300 border border-green-500/40 hover:border-green-400 rounded transition-colors"
          >
            Connect
          </button>
        )}
      </div>
    </div>
  )
}
