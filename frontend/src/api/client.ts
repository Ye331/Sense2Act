/**
 * Sense2Act 后端 REST + SSE 客户端（契约见 Backend/docs/api-design.md）。
 * 统一处理 {code,message,data} 包、JWT、401 失效与原始文本响应（快照 / Markdown 导出）。
 */
const BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') || '/api/v1'
const TOKEN_KEY = 'sense2act.token'

export class ApiError extends Error {
  constructor(message: string, readonly code: number, readonly status: number) { super(message) }
}

type Query = Record<string, string | number | boolean | undefined | null>
type Envelope<T> = { code: number; message: string; data: T }

export const session = {
  token: (): string | null => { try { return localStorage.getItem(TOKEN_KEY) } catch { return null } },
  save(token: string) { try { localStorage.setItem(TOKEN_KEY, token) } catch { /* 隐私模式下仅本次会话有效 */ } },
  clear() { try { localStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ } },
}

/** 401 时通知 App 弹出登录（token 过期或未登录）。 */
const unauthorizedListeners = new Set<() => void>()
export const onUnauthorized = (listener: () => void) => { unauthorizedListeners.add(listener); return () => { unauthorizedListeners.delete(listener) } }

function url(path: string, query: Query = {}) {
  const target = new URL(BASE + path, window.location.origin)
  for (const [key, value] of Object.entries(query)) if (value !== undefined && value !== null && value !== '') target.searchParams.set(key, String(value))
  return target
}

async function send(method: string, path: string, { query, body }: { query?: Query; body?: unknown } = {}): Promise<Response> {
  const token = session.token()
  const response = await fetch(url(path, query), {
    method,
    headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401) { session.clear(); unauthorizedListeners.forEach(listener => listener()) }
  return response
}

async function unwrap<T>(response: Response): Promise<T> {
  let payload: Envelope<T>
  try { payload = await response.json() } catch { throw new ApiError(`服务响应无法解析（HTTP ${response.status}）`, 50001, response.status) }
  if (payload.code !== 0) throw new ApiError(payload.message || '请求失败', payload.code, response.status)
  return payload.data
}

const json = <T>(method: string, path: string, options?: { query?: Query; body?: unknown }) => send(method, path, options).then(unwrap<T>)

async function text(path: string, query?: Query): Promise<{ contentType: string; content: string }> {
  const response = await send('GET', path, { query })
  if (!response.ok) return unwrap(response)
  return { contentType: response.headers.get('content-type') || 'text/plain', content: await response.text() }
}

const id = encodeURIComponent

// ---------- 契约类型 ----------

export interface Page<T> { items: T[]; total: number; page: number; page_size: number }
export interface Ref { id: string; name: string }
export interface User { id: string; email: string; name: string; role: 'admin' | 'analyst' | 'viewer' }
export interface DocumentItem { id: string; doc_type: string; title: string; org: Ref | null; amount: string | null; publish_date: string | null; deadline: string | null; region: string | null; category: string | null; url: string | null; signal_count: number }
export interface DocumentDetail extends DocumentItem { content_text?: string; raw?: Record<string, unknown>; related_signals?: { id: string; status: string; score: string }[] }
export interface Hit { rule_type: string; rule_id?: string | null; rule_version?: number | null; weight: number; detail?: Record<string, unknown> }
export interface Signal { id: string; document: { id: string; title: string; amount: string | null; publish_date: string | null }; org: Ref | null; hits: Hit[]; score: string; status: 'pending' | 'investigating' | 'confirmed' | 'dismissed'; investigation_id: string | null; created_at: string }
export type InvestigationStatus = 'created' | 'investigating' | 'reporting' | 'completed' | 'failed' | 'stopped'
/** POST /signals/{id}/investigate 的响应（契约 §4）：新建调查的 id 在 investigation_id。 */
export interface InvestigateResult { signal_id: string; status: string; investigation_id: string }
export interface Investigation { id: string; signal_id: string; status: InvestigationStatus; current_round: number; max_rounds: number; token_used: number; token_budget: number; cost_estimate: string; judgment: string | null; error: string | null; started_at: string | null; finished_at: string | null; created_at: string }
export interface Question { id: string; text: string; raised_in_round: number; status: 'open' | 'clarified' | 'unresolved' | 'abandoned'; answer_summary: string | null; evidence_ids: string[] | null }
export interface Step { seq: number; round: number; type: string; content: Record<string, any>; token_usage: number; created_at: string }
export interface EvidenceRef { id: string; title: string; source_type: string }
export interface Claim { id: string; seq: number; text: string; nature: 'fact' | 'inference' | 'speculation'; confidence: number; evidences: EvidenceRef[] }
export interface Suggestion { id: string; text: string; priority: 'high' | 'medium' | 'low'; adopted_at: string | null }
export interface ReportItem { id: string; investigation_id: string; title: string; status: 'draft' | 'published'; created_at: string }
export interface Report extends ReportItem { signal_id: string; event_id: string | null; summary: string | null; claims: Claim[]; action_suggestions: Suggestion[]; meta: { rounds: number; tool_calls: number; token_used: number; cost_estimate: string }; disclaimer: string }
export interface Evidence { id: string; investigation_id: string; source_type: string; title: string; url: string | null; excerpt: string; published_at: string | null; fetched_at: string; content_hash: string | null; doc_id: string | null }
export interface Source { id: string; name: string; type: 'web_page' | 'api' | 'rss'; url: string; adapter: string; schedule_cron: string; config: Record<string, unknown> | null; enabled: boolean; health: string | null; last_run_at: string | null; last_run_status: string | null }
export interface EventItem { id: string; title: string; type: string; status: string; start_date: string | null; summary?: string; entities?: { id: string; name: string; type: string; role: string }[] }
export interface EventGraph { event: EventItem; nodes: { node_type: 'event' | 'entity'; id: string; label: string; type: string }[]; edges: { source: string; target: string; relation: string }[]; reports: { id: string; title: string; status: string }[] }
export interface Organization { id: string; name: string; aliases: string[]; type: string | null; region: string | null; stats: { category: string; sample_count: number; amount_mean: string | null }[]; recent_documents: DocumentItem[] }
export interface Rule { id: string; version: number; name: string; type: string; params: Record<string, unknown>; weight: string; enabled: boolean; backtest: Record<string, unknown> | null }
export interface Policy { auto_investigate_threshold: number; max_concurrent_investigations: number; default_max_rounds: number; default_token_budget: number }
export interface Backtest { id: string; rule_id: string; param_grid: Record<string, unknown>; date_from: string; date_to: string; status: string; result: Record<string, unknown> | null }
export interface WatchProfile { id: string; name: string; enabled: boolean; note: string | null; created_at: string }
export interface Counter { total: number; today: number; by_status?: Record<string, number> }
export interface Dashboard { documents: Counter; signals: Counter; investigations: Counter; reports: Counter }
export interface StreamEvent { id: string; event: string; data: Record<string, any> }

// ---------- REST ----------

export const api = {
  login: (email: string, password: string) => json<{ token: string; user: User }>('POST', '/auth/token', { body: { email, password } }),
  me: () => json<User>('GET', '/users/me'),
  dashboard: () => json<Dashboard>('GET', '/dashboard/summary'),

  documents: (query?: Query) => json<Page<DocumentItem>>('GET', '/documents', { query }),
  document: (docId: string) => json<DocumentDetail>('GET', `/documents/${id(docId)}`),
  documentSnapshot: (docId: string) => text(`/documents/${id(docId)}/snapshot`),

  signals: (query?: Query) => json<Page<Signal>>('GET', '/signals', { query }),
  signal: (signalId: string) => json<Signal>('GET', `/signals/${id(signalId)}`),
  investigateSignal: (signalId: string) => json<InvestigateResult>('POST', `/signals/${id(signalId)}/investigate`),
  setSignalStatus: (signalId: string, status: 'confirmed' | 'dismissed', reason?: string) =>
    json<Signal>('PATCH', `/signals/${id(signalId)}/status`, { body: { status, ...(reason ? { reason } : {}) } }),

  investigations: (query?: Query) => json<Page<Investigation>>('GET', '/investigations', { query }),
  investigation: (invId: string) => json<{ investigation: Investigation; questions: Question[] }>('GET', `/investigations/${id(invId)}`),
  /** 游标翻页读全量步骤（契约 §5：next_cursor 缺省即读完）。 */
  async steps(invId: string): Promise<Step[]> {
    const all: Step[] = []
    let cursor: number | undefined
    do {
      const batch = await json<{ items: Step[]; next_cursor?: number | null }>('GET', `/investigations/${id(invId)}/steps`, { query: { cursor, limit: 100 } })
      all.push(...batch.items)
      cursor = batch.next_cursor ?? undefined
    } while (cursor !== undefined)
    return all
  },
  stopInvestigation: (invId: string) => json<Investigation>('POST', `/investigations/${id(invId)}/stop`),

  organization: (orgId: string) => json<Organization>('GET', `/organizations/${id(orgId)}`),
  evidence: (evId: string) => json<Evidence>('GET', `/evidences/${id(evId)}`),
  evidenceSnapshot: (evId: string) => text(`/evidences/${id(evId)}/snapshot`),

  reports: (query?: Query) => json<Page<ReportItem>>('GET', '/reports', { query }),
  report: (reportId: string) => json<Report>('GET', `/reports/${id(reportId)}`),
  feedback: (reportId: string, body: { adopted_suggestion_ids?: string[]; comment?: string; flag?: 'false_positive' }) =>
    json<{ newly_adopted: string[]; comment_recorded: boolean; flagged: boolean }>('POST', `/reports/${id(reportId)}/feedback`, { body }),
  exportMarkdown: (reportId: string) => text(`/reports/${id(reportId)}/export`, { format: 'md' }),

  sources: () => json<Page<Source>>('GET', '/sources', { query: { page_size: 100 } }),
  createSource: (body: Partial<Source>) => json<Source>('POST', '/sources', { body }),
  updateSource: (sourceId: string, body: Partial<Source>) => json<Source>('PATCH', `/sources/${id(sourceId)}`, { body }),
  deleteSource: (sourceId: string) => json<null>('DELETE', `/sources/${id(sourceId)}`),
  runSource: (sourceId: string) => json<Source>('POST', `/sources/${id(sourceId)}/run`),

  events: () => json<Page<EventItem>>('GET', '/events', { query: { page_size: 100 } }),
  event: (eventId: string) => json<EventItem>('GET', `/events/${id(eventId)}`),
  eventGraph: (eventId: string) => json<EventGraph>('GET', `/events/${id(eventId)}/graph`),

  rules: () => json<Rule[]>('GET', '/admin/signal-rules'),
  createRule: (body: { name: string; type: string; weight: number; params: Record<string, unknown> }) => json<Rule>('POST', '/admin/signal-rules', { body }),
  updateRule: (ruleId: string, body: Partial<{ name: string; type: string; weight: number; params: Record<string, unknown>; enabled: boolean }>) =>
    json<Rule>('PATCH', `/admin/signal-rules/${id(ruleId)}`, { body }),
  policy: () => json<Policy>('GET', '/admin/investigation-policies'),
  updatePolicy: (body: Policy) => json<Policy>('PUT', '/admin/investigation-policies', { body }),
  backtests: () => json<Page<Backtest>>('GET', '/admin/backtests', { query: { page_size: 100 } }),
  backtest: (runId: string) => json<Backtest>('GET', `/admin/backtests/${id(runId)}`),
  createBacktest: (body: { rule_id: string; param_grid: Record<string, unknown>; date_from: string; date_to: string }) => json<Backtest>('POST', '/admin/backtests', { body }),
  watchProfiles: () => json<WatchProfile[]>('GET', '/admin/watch-profiles'),
  createWatchProfile: (body: { name: string; note?: string }) => json<WatchProfile>('POST', '/admin/watch-profiles', { body }),
  deleteWatchProfile: (profileId: string) => json<null>('DELETE', `/admin/watch-profiles/${id(profileId)}`),
}

// ---------- SSE ----------

export const INVESTIGATION_EVENTS = ['investigation_started', 'round_started', 'questions_generated', 'tool_selected', 'tool_completed',
  'reflection_updated', 'budget_update', 'report_ready', 'investigation_completed', 'investigation_failed', 'investigation_stopped'] as const
export const GLOBAL_EVENTS = ['signal_created', 'investigation_completed', 'report_ready', 'source_degraded'] as const
const TERMINAL = new Set(['investigation_completed', 'investigation_failed', 'investigation_stopped'])

/**
 * 订阅命名事件；EventSource 断线自动重连并带 Last-Event-ID 补发（后端按 seq/全局 id 回放）。
 * EventSource 无法设置请求头，JWT 按契约经 ?token= 传递。
 */
function subscribe(path: string, names: readonly string[], onEvent: (event: StreamEvent) => void, onError?: () => void) {
  const source = new EventSource(url(path, { token: session.token() }))
  const seen = new Set<string>()
  for (const name of names) source.addEventListener(name, raw => {
    const message = raw as MessageEvent<string>
    if (message.lastEventId && seen.has(message.lastEventId)) return
    seen.add(message.lastEventId)
    onEvent({ id: message.lastEventId, event: name, data: JSON.parse(message.data) })
  })
  if (onError) source.onerror = onError
  return { close: () => source.close() }
}

export function streamInvestigation(invId: string, onEvent: (event: StreamEvent) => void, onError?: () => void) {
  const subscription = subscribe(`/investigations/${id(invId)}/stream`, INVESTIGATION_EVENTS, event => {
    onEvent(event)
    if (TERMINAL.has(event.event)) subscription.close()
  }, onError)
  return subscription
}

export const streamGlobal = (onEvent: (event: StreamEvent) => void, onError?: () => void) => subscribe('/stream', GLOBAL_EVENTS, onEvent, onError)
