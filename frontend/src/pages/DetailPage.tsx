import { useCallback, useEffect, useRef, useState } from 'react'
import { Icon } from '../components/Icon'
import { EmptyState, ErrorBar, NatureBadge, StatusBadge } from '../components/UI'
import { api, streamInvestigation } from '../api/client'
import type { Question } from '../api/client'
import { errorText, formatDate, investigationService, money, newsHit, reportCount, ruleNames, score, signalSubject } from '../services/investigations'
import type { ClaimNature, InvestigationTask, StageView } from '../types/domain'

const questionStatus: Record<string, string> = { open: '待核实', clarified: '已核实', unresolved: '未查清', abandoned: '已放弃' }
const natures: ClaimNature[] = ['fact', 'inference', 'speculation']
const LIVE = new Set(['queued', 'running', 'reporting'])

function InvestigationTimeline({ stages }: { stages: StageView[] }) {
  const rounds = [...new Set(stages.map(stage => stage.round))]
  return <div className="round-list">{rounds.map(round => {
    const items = stages.filter(stage => stage.round === round)
    const meaningful = items.filter(stage => stage.event !== 'budget_update')
    return <section className="round-block" key={round}><div className="round-heading"><span>第 {round} 轮</span><strong>{meaningful.some(stage => stage.event === 'reflection_updated') ? '核验与判断' : meaningful.some(stage => stage.event === 'tool_completed') ? '资料检索' : '问题分解'}</strong><small>{items.length} 条记录</small></div><div className="round-content">{meaningful.map(stage => <div className="round-step" key={stage.key}><span className="round-marker stage-done"/><div><strong>{stage.title}</strong><p>{stage.description}</p></div></div>)}</div></section>
  })}</div>
}

export function DetailPage({ id, onChange }: { id: string; onChange: () => void }) {
  const [task, setTask] = useState<InvestigationTask>()
  const [questions, setQuestions] = useState<Question[]>([])
  const [error, setError] = useState('')
  const [live, setLive] = useState(false)
  const pending = useRef<number | undefined>(undefined)

  const load = useCallback(async () => {
    const [next, detail] = await Promise.all([investigationService.getTask(id), api.investigation(id)])
    setTask(next); setQuestions(detail.questions)
    return next
  }, [id])

  useEffect(() => {
    let subscription: { close: () => void } | undefined
    load().then(initial => {
      if (!LIVE.has(initial.status)) return
      setLive(true)
      subscription = streamInvestigation(id, event => {
        // 一轮会连发多条步骤，合并成一次刷新
        window.clearTimeout(pending.current)
        pending.current = window.setTimeout(() => load().catch(err => setError(errorText(err))), 300)
        if (['investigation_completed', 'investigation_failed', 'investigation_stopped'].includes(event.event)) { setLive(false); onChange() }
      }, () => setError('调查事件流连接中断，正在自动重连…'))
    }).catch(err => setError(errorText(err)))
    return () => { subscription?.close(); window.clearTimeout(pending.current) }
  }, [id, load, onChange])

  const stop = async () => { try { await api.stopInvestigation(id); await load(); onChange() } catch (err) { setError(errorText(err)) } }

  if (!task) return error ? <EmptyState title="任务读取失败" text={error} action={<a className="button button-primary" href="#/tasks">返回任务列表</a>}/> : <div className="loading-state">正在加载任务…</div>
  const signal = task.signal
  return <div className="page detail-page"><div className="detail-top"><a className="back-link" href="#/tasks">← 返回调查任务</a><div className="detail-title-row"><div><span className="eyebrow">调查任务 / {task.id}</span><h1>{task.title}</h1><div className="title-tags"><StatusBadge status={task.status}/>{live && <span className="demo-badge compact">实时</span>}</div></div><div className="modal-actions">{(task.status === 'running' || task.status === 'reporting') && <button className="button button-secondary" onClick={stop}>停止调查</button>}{task.report && <a className="button button-primary" href={`#/reports/${task.id}`}><Icon name="report" size={18}/>查看调查报告</a>}</div></div><p className="detail-subtitle">{newsHit(signal) ? '监测主体' : '采购单位'}：{task.subject}</p></div>
    <ErrorBar message={error}/>
    {task.status === 'queued' && <div className="notice-bar subtle"><Icon name="alert" size={18}/><div><strong>等待 Agent 领取</strong><span>调查已立项，Agent 服务领取后此页会实时显示每一轮查证。</span></div></div>}
    {task.failureReason && <div className="failure-banner"><Icon name="alert" size={18}/><div><strong>失败原因</strong><span>{task.failureReason}</span></div></div>}
    <div className="detail-metrics"><div><span>调查轮次</span><strong>{task.currentRound}<small> / {task.totalRounds}</small></strong></div><div><span>证据条目</span><strong>{task.evidenceCount}<small> 条</small></strong></div><div><span>Token</span><strong>{task.investigation.token_used}<small> / {task.investigation.token_budget}</small></strong></div><div><span>待查问题</span><strong>{questions.filter(q => q.status === 'open').length}<small> / {questions.length}</small></strong></div></div>
    <div className="detail-grid"><section className="panel stage-panel"><div className="panel-heading"><div><span className="section-kicker">逐轮查证</span><h2>调查过程</h2></div><span className="panel-note">{live ? '实时事件流' : `${task.stages.length} 条留痕`}</span></div>{task.stages.length ? <InvestigationTimeline stages={task.stages}/> : <EmptyState title="调查尚未开始" text="Agent 领取后将在这里逐轮呈现。"/>}</section>
      <div className="detail-side"><section className="panel info-panel"><div className="panel-heading"><div><span className="section-kicker">研究对象</span><h2>信号信息</h2></div></div><dl>{newsHit(signal) ? <><div><dt>监测主体</dt><dd>{signalSubject(signal)}</dd></div><div><dt>报道</dt><dd>{reportCount(signal)} 篇</dd></div></> : <><div><dt>采购单位</dt><dd>{signal?.org ? <a href={`#/organizations/${signal.org.id}`}>{signal.org.name}</a> : '—'}</dd></div><div><dt>预算</dt><dd>{money(signal?.document.amount)}</dd></div></>}<div><dt>发布日期</dt><dd>{signal?.document.publish_date || '—'}</dd></div><div><dt>信号评分</dt><dd>{signal ? score(signal) : '—'}</dd></div><div><dt>建立时间</dt><dd>{formatDate(task.createdAt)}</dd></div></dl>{signal && <div className="signal-summary"><span>命中规则</span><p>{signal.hits.map(hit => ruleNames[hit.rule_type] || hit.rule_type).join('、')}</p><a href={`#/signals/${signal.id}`}>查看信号详情 →</a></div>}</section>
        {task.report && <section className="panel verdict-panel"><div className="panel-heading"><div><span className="section-kicker">报告结论</span><h2>结论性质</h2></div></div><div className="claim-status-list">{natures.map(nature => <div key={nature}><NatureBadge nature={nature}/><strong>{task.claimCounts[nature]}</strong><span>条结论</span></div>)}</div></section>}
        <section className="panel future-panel"><div className="panel-heading"><div><span className="section-kicker">待解问题</span><h2>调查问题</h2></div></div>{questions.length ? <ul>{questions.map(question => <li key={question.id}>{question.text} · {questionStatus[question.status] || question.status}{question.answer_summary && <small style={{ display: 'block' }}>{question.answer_summary}</small>}{question.evidence_ids?.map(evId => <a key={evId} href={`#/evidences/${evId}`}> · 证据 {evId.slice(-6)}</a>)}</li>)}</ul> : <p className="panel-footnote">Agent 拆解出的待查问题会显示在这里。</p>}</section></div></div></div>
}
