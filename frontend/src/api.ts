import type {
  KnowledgeDraft,
  KnowledgeItem,
  LiveAction,
  LiveEvent,
  LiveSession,
  ModerationRules,
  QueueItem,
  QueueSnapshot,
  ReportMetrics,
  Script,
  User,
} from './types'

const TOKEN_KEY = 'livepilot_token'
const USER_KEY = 'livepilot_user'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function getUser(): User | null {
  const raw = localStorage.getItem(USER_KEY)
  return raw ? (JSON.parse(raw) as User) : null
}

export function saveSession(token: string, user: User): void {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string> | undefined),
  }
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(`/api${path}`, { ...options, headers })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: '请求失败' }))
    throw new Error(body.detail || `请求失败 (${res.status})`)
  }
  return res.json() as Promise<T>
}

export const api = {
  login: (username: string, password: string) =>
    request<{ access_token: string; token_type: string; user: User }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  me: () => request<User>('/auth/me'),

  getLlmConfig: () =>
    request<{
      llm_api_key: string
      llm_base_url: string
      llm_model: string
      llm_embedding_model: string
      llm_embedding_base_url: string
      llm_embedding_api_key: string
      llm_configured: boolean
      embedding_configured: boolean
    }>('/admin/llm-config'),
  updateLlmConfig: (cfg: Partial<Record<string, string>>) =>
    request<{
      llm_api_key: string
      llm_base_url: string
      llm_model: string
      llm_embedding_model: string
      llm_embedding_base_url: string
      llm_embedding_api_key: string
      llm_configured: boolean
      embedding_configured: boolean
    }>('/admin/llm-config', {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),
  testLlmConfig: (cfg: Partial<Record<string, string>> = {}) =>
    request<{
      ok: boolean
      status: number
      model: string
      embedding?: { ok: boolean; status?: number; model: string; error?: string }
    }>('/admin/llm-config/test', {
      method: 'POST',
      body: JSON.stringify(cfg),
    }),

  getPersona: () =>
    request<Record<string, string>>('/admin/persona'),
  updatePersona: (p: Partial<Record<string, string>>) =>
    request<Record<string, string>>('/admin/persona', {
      method: 'PUT',
      body: JSON.stringify(p),
    }),

  getRetrievalConfig: () =>
    request<{ chunk_size: number; chunk_overlap: number; top_k: number }>(
      '/admin/retrieval-config',
    ),
  updateRetrievalConfig: (cfg: {
    chunk_size: number
    chunk_overlap: number
    top_k: number
  }) =>
    request<{ chunk_size: number; chunk_overlap: number; top_k: number }>(
      '/admin/retrieval-config',
      { method: 'PUT', body: JSON.stringify(cfg) },
    ),

  listUsers: () =>
    request<{ id: number; username: string; display_name: string; role: string; created_at: number }[]>(
      '/admin/users',
    ),
  createUser: (u: { username: string; display_name: string; password: string; role: string }) =>
    request<{ id: number; username: string; display_name: string; role: string }>('/admin/users', {
      method: 'POST',
      body: JSON.stringify(u),
    }),
  updateUser: (id: number, patch: { display_name?: string; role?: string }) =>
    request<{ id: number; username: string; display_name: string; role: string }>(
      `/admin/users/${id}`,
      { method: 'PATCH', body: JSON.stringify(patch) },
    ),
  resetUserPassword: (id: number, password: string) =>
    request<{ ok: boolean }>(`/admin/users/${id}/password`, {
      method: 'PUT',
      body: JSON.stringify({ password }),
    }),
  deleteUser: (id: number) =>
    request<{ ok: boolean }>(`/admin/users/${id}`, { method: 'DELETE' }),

  startSession: (title: string, event_rate: number, platform = 'mock', room_id = '') =>
    request<LiveSession>('/live/start', {
      method: 'POST',
      body: JSON.stringify({ title, event_rate, platform, room_id }),
    }),
  stopSession: (id: string) =>
    request<LiveSession>(`/live/${id}/stop`, { method: 'POST' }),
  listSessions: () => request<LiveSession[]>('/live/sessions'),
  snapshot: (id: string) =>
    request<{
      metrics: Record<string, number>
      recent_events: LiveEvent[]
      recent_actions: LiveAction[]
    }>(`/live/${id}/snapshot`),
  reviewAction: (sessionId: string, actionId: number, status: string) =>
    request<{ ok: boolean }>(`/live/${sessionId}/actions/${actionId}/review`, {
      method: 'POST',
      body: JSON.stringify({ status }),
    }),

  listKnowledge: () => request<KnowledgeItem[]>('/knowledge'),
  createKnowledge: (body: Omit<KnowledgeItem, 'id' | 'created_at'>) =>
    request<KnowledgeItem>('/knowledge', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  deleteKnowledge: (id: number) =>
    request<{ ok: boolean }>(`/knowledge/${id}`, { method: 'DELETE' }),
  organizeKnowledge: (body: { text: string; product?: string; category?: string }) =>
    request<{ items: KnowledgeDraft[] }>('/knowledge/organize', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  enrichKnowledge: (body: { title: string; content: string; product?: string; category?: string }) =>
    request<{ item: KnowledgeDraft }>('/knowledge/enrich', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  getMetrics: (sessionId: string) =>
    request<ReportMetrics>(`/reports/${sessionId}/metrics`),
  generateReport: (sessionId: string) =>
    request<{ session_id: string; report: string; metrics: ReportMetrics }>(
      `/reports/${sessionId}/generate`,
      { method: 'POST' },
    ),

  getRules: () => request<ModerationRules>('/rules'),
  updateRules: (body: Partial<ModerationRules>) =>
    request<ModerationRules>('/rules', {
      method: 'PUT',
      body: JSON.stringify(body),
    }),

  listScripts: () => request<Script[]>('/scripts'),
  createScript: (body: { title: string; content: string; type: string; tags: string[] }) =>
    request<Script>('/scripts', { method: 'POST', body: JSON.stringify(body) }),
  updateScript: (id: number, patch: Partial<Script>) =>
    request<Script>(`/scripts/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteScript: (id: number) =>
    request<{ ok: boolean }>(`/scripts/${id}`, { method: 'DELETE' }),

  getQueue: (sessionId: string) => request<QueueSnapshot>(`/live/${sessionId}/queue`),
  popQueue: (sessionId: string) =>
    request<{ popped: QueueItem | null }>(`/live/${sessionId}/queue/pop`, { method: 'POST' }),
}

export function openLiveSocket(
  sessionId: string,
  handlers: {
    onEvent: (e: LiveEvent) => void
    onAction: (a: LiveAction) => void
    onSession: (s: LiveSession) => void
    onQueue: (q: QueueSnapshot) => void
    onHello: (snapshot: any) => void
    onOpen?: () => void
    onClose?: () => void
    onError?: () => void
  },
): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const token = getToken() ?? ''
  const ws = new WebSocket(
    `${proto}://${location.host}/api/ws/live/${sessionId}?token=${encodeURIComponent(token)}`,
  )
  ws.onopen = () => handlers.onOpen?.()
  ws.onclose = () => handlers.onClose?.()
  ws.onerror = () => handlers.onError?.()
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data)
    if (msg.type === 'event') handlers.onEvent(msg.data)
    else if (msg.type === 'action') handlers.onAction(msg.data)
    else if (msg.type === 'session') handlers.onSession(msg.data)
    else if (msg.type === 'queue') handlers.onQueue(msg.data)
    else if (msg.type === 'hello') handlers.onHello(msg.data)
  }
  return ws
}

async function streamSSE(
  path: string,
  body: Record<string, any>,
  onDelta: (text: string) => void,
  onDone: (sources: string[]) => void,
): Promise<void> {
  const token = getToken()
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) throw new Error('生成失败，请稍后重试')
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      const payload = JSON.parse(line.slice(6))
      if (payload.delta) onDelta(payload.delta)
      if (payload.done) onDone(payload.sources ?? [])
    }
  }
}

export function streamAssist(
  body: { query: string; session_id?: string | null; context: any[] },
  onDelta: (text: string) => void,
  onDone: (sources: string[]) => void,
): Promise<void> {
  return streamSSE('/api/assist/stream', body, onDelta, onDone)
}

export function streamContent(
  body: { kind: string; topic: string; style: string; context: any[] },
  onDelta: (text: string) => void,
  onDone: (sources: string[]) => void,
): Promise<void> {
  return streamSSE('/api/content/generate', body, onDelta, onDone)
}
