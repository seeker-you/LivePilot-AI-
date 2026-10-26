export interface User {
  id: number
  username: string
  display_name: string
  role: string
}

export type EventType = 'chat' | 'gift' | 'user_enter' | 'like' | 'follow'

export interface LiveEvent {
  id: number
  session_id: string
  type: EventType
  payload: Record<string, any>
  ts: number
}

export interface LiveAction {
  id: number
  session_id: string
  type: 'auto_reply' | 'welcome' | 'pin' | 'alert' | 'escalate'
  content: string
  trigger_event_id: number | null
  status: 'pending' | 'approved' | 'rejected' | 'executed'
  ts: number
}

export interface LiveSession {
  id: string
  title: string
  status: string
  host_id: number
  started_at: number | null
  ended_at: number | null
  created_at: number
  metrics?: Record<string, number>
}

export interface KnowledgeItem {
  id: number
  title: string
  product: string
  category: string
  content: string
  tags: string[]
  created_at: number
}

export interface KnowledgeDraft {
  title: string
  product: string
  category: string
  content: string
  tags: string[]
}

export interface ModerationRules {
  gift_alert_threshold: number
  gift_thank_threshold: number
  vip_welcome_level: number
  enabled_actions: string[]
  sensitive_words: string[]
}

export interface Script {
  id: number
  title: string
  content: string
  type: 'text' | 'audio'
  tags: string[]
  enabled: boolean
  play_count: number
  last_used_at: number | null
  created_at: number
}

export interface QueueItem {
  id: string
  type: string
  content: string
  source: string
  level: 'high' | 'low'
}

export interface QueueSnapshot {
  next: QueueItem | null
  high: QueueItem[]
  low: QueueItem[]
}

export interface TimelinePoint {
  minute: number
  ts: number
  chat: number
  gift_count: number
  gift_value: number
  likes: number
  enters: number
  total: number
}

export interface ReportInsights {
  peak: { minute: number; total: number; detail: string } | null
  cold_spots: { from: number; to: number }[]
  highlights: { minute: number; detail: string; score: number }[]
}

export interface ReportMetrics {
  session: LiveSession | null
  total_events: number
  chat_count: number
  gift_count: number
  gift_total: number
  like_total: number
  follow_count: number
  unique_users: number
  peak_online: number
  sentiment: Record<string, number>
  top_questions: { text: string; count: number }[]
  question_total: number
  answered_total: number
  coverage: number | null
  blind_spots: { text: string; count: number }[]
  tag_hits: { tag: string; count: number }[]
  funnel: { label: string; value: number }[]
  action_count: number
  action_breakdown: Record<string, number>
  timeline: TimelinePoint[]
  insights: ReportInsights
}
