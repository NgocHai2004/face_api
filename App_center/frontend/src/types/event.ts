/**
 * TypeScript types cho Event Hub - mirror schema từ backend Pydantic models
 */

export type EventType =
  | 'face_recognition'
  | 'fingerprint'
  | 'card_reader'

export type EventTopic =
  | 'security'
  | '*'

export type EventPriority = 'low' | 'medium' | 'high' | 'urgent'

export interface EventMetadata {
  received_at: string
  normalized: boolean
  version: string
}

export interface NormalizedEvent {
  id: string
  timestamp: string
  source: string
  type: EventType | string
  topic: EventTopic | string
  priority: EventPriority | string
  payload: Record<string, unknown>
  metadata: EventMetadata
}

export interface SystemMessage {
  type: '__system__'
  status: string
  subscribed_topic?: string
  message?: string
}

export type WsMessage = NormalizedEvent | SystemMessage

export type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

// Topic color mapping
export const TOPIC_COLORS: Record<string, string> = {
  security: 'bg-red-500/20 text-red-300 border-red-500/30',
}

// Event type icon mapping (emoji)
export const EVENT_TYPE_ICONS: Record<string, string> = {
  face_recognition: '👤',
  fingerprint:      '🖐',
  card_reader:      '💳',
}

// Priority color mapping
export const PRIORITY_COLORS: Record<string, string> = {
  low:    'text-green-400',
  medium: 'text-yellow-400',
  high:   'text-orange-400',
  urgent: 'text-red-400',
}
