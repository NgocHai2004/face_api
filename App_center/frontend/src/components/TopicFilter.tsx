/**
 * TopicFilter - Bộ lọc topic để subscribe vào Event Hub
 */
import type { EventTopic } from '../types/event'
import { TOPIC_COLORS } from '../types/event'

const AVAILABLE_TOPICS: { value: string; label: string; icon: string }[] = [
  { value: '*',        label: 'All Topics', icon: '🌐' },
  { value: 'security', label: 'Security',   icon: '🔒' },
]

interface TopicFilterProps {
  currentTopic: string
  onChangeTopic: (topic: string) => void
}

export function TopicFilter({ currentTopic, onChangeTopic }: TopicFilterProps) {
  return (
    <div className="flex items-center gap-2 px-4 py-2 bg-slate-900 border-b border-slate-700 overflow-x-auto">
      <span className="text-xs text-slate-500 whitespace-nowrap mr-1">Subscribe:</span>
      {AVAILABLE_TOPICS.map(({ value, label, icon }) => {
        const isActive = currentTopic === value
        const topicClass = value === '*'
          ? 'bg-sky-500/20 text-sky-300 border-sky-500/30'
          : TOPIC_COLORS[value] ?? 'bg-slate-500/20 text-slate-300 border-slate-500/30'

        return (
          <button
            key={value}
            onClick={() => onChangeTopic(value)}
            className={`
              flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs font-medium whitespace-nowrap
              transition-all duration-150 cursor-pointer
              ${isActive
                ? `${topicClass} ring-1 ring-offset-1 ring-offset-slate-900 opacity-100 scale-105`
                : 'bg-slate-800 text-slate-500 border-slate-700 hover:border-slate-500 hover:text-slate-300 opacity-60'
              }
            `}
          >
            <span>{icon}</span>
            <span>{label}</span>
            {isActive && (
              <span className="w-1.5 h-1.5 rounded-full bg-current pulse-dot" />
            )}
          </button>
        )
      })}
    </div>
  )
}
