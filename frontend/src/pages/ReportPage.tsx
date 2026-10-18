import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { ReportGraph } from '../components/ReportGraph'
import { EmptyState, NatureBadge, PageHeading } from '../components/UI'
import { api } from '../api/client'
import type { Report } from '../api/client'
import { errorText, sourceNames } from '../services/investigations'

const priorityNames: Record<string, string> = { high: '高', medium: '中', low: '低' }

/** 路由参数是调查 ID（一调查一报告），按 investigation_id 查报告。 */
export function ReportPage({ investigationId }: { investigationId: string }) {
  const [report, setReport] = useState<Report | null>()
  const [feedback, setFeedback] = useState('')
  const [message, setMessage] = useState('')
  const [adopted, setAdopted] = useState<string[]>([])
  const [falsePositive, setFalsePositive] = useState(false)
  const [busy, setBusy] = useState(false)
  const load = () => api.reports({ investigation_id: investigationId })
    .then(page => page.items[0] ? api.report(page.items[0].id) : null)
    .then(setReport).catch(err => { setReport(null); setMessage(errorText(err)) })
  useEffect(() => { load() }, [investigationId])

  if (report === undefined) return <div className="loading-state">正在加载报告…</div>
  if (report === null) return <div className="page"><a className="back-link" href={`#/tasks/${investigationId}`}>← 返回调查详情</a><EmptyState title="暂无调查报告" text={message || '调查提交报告后将在此显示。'}/></div>

  const evidences = [...new Map(report.claims.flatMap(claim => claim.evidences).map(ref => [ref.id, ref])).values()]
  const sendFeedback = async () => { setBusy(true); setMessage(''); try {
    const result = await api.feedback(report.id, { comment: feedback.trim() || undefined, adopted_suggestion_ids: adopted, ...(falsePositive ? { flag: 'false_positive' as const } : {}) })
    setMessage(`反馈已记录：新采纳 ${result.newly_adopted.length} 条建议${result.flagged ? '，已标记误报' : ''}`); setFeedback(''); setAdopted([]); setFalsePositive(false); await load()
  } catch (err) { setMessage(errorText(err)) } finally { setBusy(false) } }
  const exportMarkdown = async () => { setBusy(true); setMessage(''); try {
    const file = await api.exportMarkdown(report.id)
    const href = URL.createObjectURL(new Blob([file.content], { type: 'text/markdown' }))
    const anchor = document.createElement('a'); anchor.href = href; anchor.download = `${report.id}.md`; anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(href), 1000); setMessage('Markdown 已下载')
  } catch (err) { setMessage(errorText(err)) } finally { setBusy(false) } }

  return <div className="page report-page"><a className="back-link" href={`#/tasks/${investigationId}`}>← 返回调查详情</a><PageHeading eyebrow={`调查成果 / ${report.id}`} title="调查报告" description={report.status === 'published' ? '已发布' : '草稿（调查收尾后发布）'} action={<button className="button button-secondary" disabled={busy} onClick={exportMarkdown}><Icon name="download" size={17}/>下载 Markdown</button>}/>
    <div className="report-hero"><div><span className="report-hero-kicker">主要判断</span><h2>{report.title}</h2><p>{report.summary}</p><div className="report-hero-tags"><span>{report.claims.length} 条结论</span><span>{evidences.length} 条证据</span><span>{report.meta.rounds} 轮调查</span></div></div><span className="report-hero-icon"><Icon name="report" size={28}/></span></div>
    <div className="report-grid"><div className="report-main">
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">01 / 判断依据</span><h2>调查结论</h2></div></div><div className="claim-list">{report.claims.map(claim => <div key={claim.id}><NatureBadge nature={claim.nature}/><span>{claim.text}<small style={{ display: 'block' }}>{claim.evidences.map(ref => <a key={ref.id} href={`#/evidences/${ref.id}`}>{ref.title} </a>)}</small></span><strong>{Math.round(Number(claim.confidence) * 100)}%</strong></div>)}</div></section>
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">02 / 关系核验</span><h2>证据关系图</h2></div></div><ReportGraph report={report}/></section>
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">03 / 原始材料</span><h2>证据来源</h2></div></div><div className="evidence-list">{evidences.map(ref => <a className="evidence-item" key={ref.id} href={`#/evidences/${ref.id}`}><span className="evidence-id">{ref.id.slice(-6)}</span><div><strong>{ref.title}</strong><p>{sourceNames[ref.source_type] || ref.source_type}</p><span>查看证据与快照 →</span></div></a>)}</div></section>
    </div>
    <div className="report-side">
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">后续处理</span><h2>行动建议</h2></div></div><div className="recommendation-list">{report.action_suggestions.length ? report.action_suggestions.map((item, index) => <div key={item.id}><span className="recommendation-number">{String(index + 1).padStart(2, '0')}</span><strong>{item.text}</strong><p>优先级：{priorityNames[item.priority] || item.priority}{item.adopted_at ? ' · 已采纳' : ''}</p></div>) : <p className="panel-footnote">证据不足以支撑行动建议。</p>}</div></section>
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">生成记录</span><h2>报告信息</h2></div></div><dl className="report-meta"><div><dt>调查 ID</dt><dd><a href={`#/tasks/${report.investigation_id}`}>{report.investigation_id}</a></dd></div><div><dt>工具调用</dt><dd>{report.meta.tool_calls}</dd></div><div><dt>Token</dt><dd>{report.meta.token_used}</dd></div>{report.event_id && <div><dt>事件</dt><dd><a href={`#/events/${report.event_id}`}>{report.event_id}</a></dd></div>}</dl><p className="panel-footnote">{report.disclaimer}</p></section>
      <section className="panel report-section"><div className="panel-heading"><h2>报告反馈</h2></div><div className="management-detail"><label className="field"><span>评论</span><textarea aria-label="报告反馈" value={feedback} onChange={e => setFeedback(e.target.value)} placeholder="输入反馈意见"/></label>{report.action_suggestions.length > 0 && <strong>采纳行动建议</strong>}{report.action_suggestions.map(item => <label className="management-check" key={item.id}><input type="checkbox" checked={adopted.includes(item.id)} onChange={event => setAdopted(event.target.checked ? [...adopted, item.id] : adopted.filter(value => value !== item.id))}/>{item.text}</label>)}<label className="management-check"><input type="checkbox" checked={falsePositive} onChange={event => setFalsePositive(event.target.checked)}/>标记误报</label><button className="button button-primary" disabled={busy || report.status !== 'published' || (!feedback.trim() && !adopted.length && !falsePositive)} onClick={sendFeedback}>提交反馈</button>{report.status !== 'published' && <p className="panel-footnote">报告发布后可反馈。</p>}{message && <p role="status">{message}</p>}</div></section>
    </div></div>
  </div>
}
