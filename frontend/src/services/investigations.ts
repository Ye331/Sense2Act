import { api } from '../api/client'
import type { Investigation, Report, Signal, Step } from '../api/client'
import type { ActivityView, ClaimNature, InvestigationTask, StageView, TaskStatus } from '../types/domain'

/** 后端调查状态 → 页面任务状态。 */
const STATUS: Record<Investigation['status'], TaskStatus> = {
  created: 'queued', investigating: 'running', reporting: 'reporting', completed: 'completed', failed: 'failed', stopped: 'stopped',
}

export const ruleNames: Record<string, string> = {
  amount_anomaly: '金额异常', frequency_burst: '频率突增', semantic_match: '业务相关', novelty: '采购新颖', composite: '组合规则',
  news_event: '新闻事件',
}
export const natureNames: Record<ClaimNature, string> = { fact: '事实', inference: '推断', speculation: '推测' }
export const sourceNames: Record<string, string> = {
  announcement: '采购公告', policy: '政策文件', news: '新闻报道', company_record: '工商记录', web: '网站资料', internal_stat: '内部统计',
}

const eventTitles: Record<string, string> = {
  investigation_started: '调查启动', round_started: '新一轮查证', questions_generated: '生成待查问题', tool_selected: '选择工具',
  tool_completed: '检索证据', reflection_updated: '核验与判断', budget_update: '预算更新', report_ready: '报告生成',
  investigation_completed: '调查完成', investigation_failed: '调查失败', investigation_stopped: '调查停止',
}

/** 步骤 content 由 Agent 上报（§9.3），这里只取人能读懂的一句话。 */
export function describeStep(step: Pick<Step, 'round' | 'content'>): string {
  const c = step.content
  switch (c.event) {
    case 'investigation_started': return `轮次上限 ${c.max_rounds}，Token 预算 ${c.token_budget}`
    case 'round_started': return c.focus ? `第 ${step.round} 轮：${c.focus}` : `第 ${step.round} 轮开始`
    case 'questions_generated': return `提出 ${(c.questions || []).length} 个待查问题：${(c.questions || []).map((q: any) => q.text).slice(0, 2).join('；')}`
    case 'tool_selected': return `${c.tool}${c.args?.query ? `：${c.args.query}` : ''}`
    case 'tool_completed': return c.result_summary || `${c.tool} ${c.ok ? '完成' : '失败'}`
    case 'reflection_updated': return c.judgment || `澄清 ${(c.clarified || []).length} 项`
    case 'budget_update': return `已用 Token ${c.token_used} / ${c.token_budget}`
    case 'report_ready': return '调查报告已提交'
    case 'investigation_completed': return `共 ${c.rounds} 轮，Token ${c.token_used}`
    case 'investigation_failed': return c.error || '调查失败'
    case 'investigation_stopped': return `停止于第 ${c.last_round} 轮`
    default: return JSON.stringify(c).slice(0, 120)
  }
}

/** 新闻信号：signal-service 新闻分支推送的 news_event 命中，detail 带监测主体、事件类型与报道文档。 */
export const newsHit = (signal?: Pick<Signal, 'hits'>) => signal?.hits.find(hit => hit.rule_type === 'news_event')
/** 信号的研究对象：招投标是采购单位，新闻是监测主体。 */
export const signalSubject = (signal?: Signal) => {
  const news = newsHit(signal)
  return news ? String(news.detail?.subject_name || '监测主体') : signal?.org?.name || '采购单位未知'
}
/** 新闻信号归组的报道数（同一事件的多家来源）。 */
export const reportCount = (signal?: Pick<Signal, 'hits'>) => {
  const ids = newsHit(signal)?.detail?.document_ids
  return Array.isArray(ids) ? ids.length : 1
}

export const toStage = (step: Step): StageView => ({
  key: `S${step.seq}`, event: step.content.event, round: step.round,
  title: eventTitles[step.content.event] || step.type, description: describeStep(step),
})

function buildTask(investigation: Investigation, signal: Signal | undefined, steps: Step[], report: Report | undefined): InvestigationTask {
  const claimCounts: Record<ClaimNature, number> = { fact: 0, inference: 0, speculation: 0 }
  for (const claim of report?.claims || []) claimCounts[claim.nature]++
  return {
    id: investigation.id,
    status: STATUS[investigation.status],
    investigation, signal, report,
    title: signal?.document.title || '调查任务',
    subject: signalSubject(signal),
    createdAt: investigation.created_at,
    updatedAt: investigation.finished_at || investigation.started_at || investigation.created_at,
    currentRound: investigation.current_round,
    totalRounds: investigation.max_rounds,
    evidenceCount: new Set((report?.claims || []).flatMap(claim => claim.evidences.map(ref => ref.id))).size
      || new Set(steps.flatMap(step => step.content.evidence_ids || [])).size,
    claimCounts,
    stages: steps.map(toStage),
    failureReason: investigation.error || undefined,
  }
}

export const listSignals = async (query: Record<string, string> = {}) => (await api.signals({ page_size: 100, ...query })).items
export const listDocuments = async () => (await api.documents({ page_size: 100 })).items
/** 报告列表项不含结论，页面需要结论数/证据数，统一取详情。 */
export const listReports = async (): Promise<Report[]> =>
  Promise.all((await api.reports({ page_size: 100 })).items.map(item => api.report(item.id)))

export const investigationService = {
  async listTasks(): Promise<InvestigationTask[]> {
    const [investigations, signals, reports] = await Promise.all([api.investigations({ page_size: 100 }), listSignals(), api.reports({ page_size: 100 })])
    const reportIdByInvestigation = new Map(reports.items.map(item => [item.investigation_id, item.id]))
    return Promise.all(investigations.items.map(item => this.assemble(item, signals.find(signal => signal.id === item.signal_id), reportIdByInvestigation.get(item.id))))
  },
  async getTask(id: string): Promise<InvestigationTask> {
    const { investigation } = await api.investigation(id)
    const [signal, reports] = await Promise.all([api.signal(investigation.signal_id), api.reports({ investigation_id: id })])
    return this.assemble(investigation, signal, reports.items[0]?.id)
  },
  async assemble(investigation: Investigation, signal: Signal | undefined, reportId: string | undefined) {
    const [steps, report] = await Promise.all([api.steps(investigation.id), reportId ? api.report(reportId) : Promise.resolve(undefined)])
    return buildTask(investigation, signal, steps, report)
  },
  async listActivities(tasks: InvestigationTask[]): Promise<ActivityView[]> {
    return tasks.flatMap(task => task.stages.filter(stage => stage.event !== 'budget_update').slice(-2).map(stage => ({
      id: `${task.id}-${stage.key}`, taskId: task.id, time: formatDate(task.updatedAt), title: stage.title, description: stage.description,
    })))
  },
}

export const score = (signal: Pick<Signal, 'score'>) => Math.round(Number(signal.score) * 100)
export const money = (amount: string | null | undefined) => amount ? `¥${Number(amount).toLocaleString('zh-CN')}` : '未披露'
export function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}
export const errorText = (error: unknown) => error instanceof Error ? error.message : String(error)
