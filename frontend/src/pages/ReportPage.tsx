import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { ReportGraph } from '../components/ReportGraph'
import { EmptyState, NatureBadge, PageHeading } from '../components/UI'
import { api } from '../api/client'
import type { Report, ReportAttachment } from '../api/client'
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
  const conclusions = report.conclusions ?? []
  const selected = conclusions.find(item => item.selected)
  const alternatives = conclusions.filter(item => !item.selected)
  const pending = report.pending_findings ?? []
  const processNotes = report.process_notes ?? []
  const attachments = report.attachments ?? []
  const structured = conclusions.length > 0
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

  return <div className="page report-page"><a className="back-link" href={`#/tasks/${investigationId}`}>← 返回调查详情</a><PageHeading eyebrow={`调查成果 / ${report.id}`} title="调查报告" description={report.status === 'published' ? '已发布' : '草稿（调查收尾后发布）'} action={<div className="management-actions no-print"><a className="button button-primary" href={`#/chain?investigation=${encodeURIComponent(report.investigation_id)}`}><Icon name="layers" size={17}/>查看证据链</a><button className="button button-secondary" onClick={() => window.print()}><Icon name="report" size={17}/>打印 / 存为 PDF</button><button className="button button-secondary" disabled={busy} onClick={exportMarkdown}><Icon name="download" size={17}/>下载 Markdown</button></div>}/>
    <div className="report-hero"><div><span className="report-hero-kicker">主要判断</span><h2>{structured && selected ? selected.label : report.title}</h2><p>{structured && selected ? selected.current_judgment : report.summary}</p><div className="report-hero-tags"><span>{report.claims.length} 条已核事实</span>{structured && <span>{pending.length} 条待补证</span>}<span>{evidences.length} 条证据</span><span>{report.meta.rounds} 轮调查</span></div></div><span className="report-hero-icon"><Icon name="report" size={28}/></span></div>
    <div className="report-grid"><div className="report-main">
      {structured && selected && <section className="panel report-section conclusion-sections"><div className="panel-heading"><div><span className="section-kicker">01 / 结构化结论</span><h2>结论与判断边界</h2></div></div><div className="conclusion-block conclusion-lead"><h3>{selected.label}</h3><p>{selected.current_judgment}</p></div>{alternatives.length > 0 && <div className="conclusion-alternatives"><p className="conclusion-alt-caption">次选假设(本轮未采纳)</p>{alternatives.map(item => <div key={item.hypothesis_id} className="conclusion-block conclusion-alt"><h3>{item.label}</h3><p>{item.current_judgment}</p></div>)}</div>}<ConclusionBlock title="已有依据" items={selected.basis}/><ConclusionBlock title="待补证" items={selected.gaps}/><ConclusionBlock title="改变条件" items={selected.change_conditions}/><ConclusionBlock title="下一步" items={selected.next_steps}/></section>}
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">{structured ? '02' : '01'} / 事实依据</span><h2>有专属证据的事实</h2></div></div>{report.claims.length ? <div className="claim-list">{report.claims.map(claim => <div key={claim.id}><NatureBadge nature={claim.nature}/><span>{claim.text}<small style={{ display: 'block' }}>{claim.evidences.map(ref => <a key={ref.id} href={`#/evidences/${ref.id}`}>{ref.title} </a>)}</small></span><strong>{Math.round(Number(claim.confidence) * 100)}%</strong></div>)}</div> : <p className="section-empty">暂无满足专属证据约束的事实。</p>}</section>
      {structured && <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">03 / 待补证</span><h2>尚未证实的推导</h2></div></div>{pending.length ? <div className="pending-list">{pending.map(item => <article key={item.source_claim_id}><strong>{item.text}</strong><p>{item.gap}</p><small>下一步：{item.next_step}</small></article>)}</div> : <p className="section-empty">没有待补证条目。</p>}</section>}
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">02 / 关系核验</span><h2>证据关系图</h2></div></div><ReportGraph report={report}/></section>
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">原始材料</span><h2>证据来源</h2></div></div><div className="evidence-list">{evidences.map(ref => <div key={ref.id}><a className="evidence-item" href={`#/evidences/${ref.id}`}><span className="evidence-id">{ref.id.slice(-6)}</span><div><strong>{ref.title}</strong><p>{sourceNames[ref.source_type] || ref.source_type}</p><span>查看证据与快照 →</span></div></a><AttachmentStrip reportId={report.id} items={attachments.filter(item => item.evidence_id === ref.id)}/></div>)}</div>{attachments.length > 0 && <p className="attachment-note">图片附件仅用于呈现，是否支撑结论以证据关联为准。</p>}</section>
    </div>
    <div className="report-side">
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">后续处理</span><h2>行动建议</h2></div></div><div className="recommendation-list">{report.action_suggestions.length ? report.action_suggestions.map((item, index) => <div key={item.id}><span className="recommendation-number">{String(index + 1).padStart(2, '0')}</span><strong>{item.text}</strong><p>优先级：{priorityNames[item.priority] || item.priority}{item.adopted_at ? ' · 已采纳' : ''}</p></div>) : <p className="panel-footnote">证据不足以支撑行动建议。</p>}</div></section>
      <section className="panel report-section"><div className="panel-heading"><div><span className="section-kicker">生成记录</span><h2>报告信息</h2></div></div><dl className="report-meta"><div><dt>调查 ID</dt><dd><a href={`#/tasks/${report.investigation_id}`}>{report.investigation_id}</a></dd></div><div><dt>工具调用</dt><dd>{report.meta.tool_calls}</dd></div><div><dt>Token</dt><dd>{report.meta.token_used}</dd></div>{report.event_id && <div><dt>事件</dt><dd><a href={`#/events/${report.event_id}`}>{report.event_id}</a></dd></div>}</dl><p className="panel-footnote">{report.disclaimer}</p></section>
      {structured && processNotes.length > 0 && <section className="panel report-section"><details className="process-notes"><summary>调查说明（{processNotes.length}）</summary>{processNotes.map(item => <p key={item.source_claim_id}>{item.text}</p>)}</details></section>}
      <section className="panel report-section no-print"><div className="panel-heading"><h2>报告反馈</h2></div><div className="management-detail"><label className="field"><span>评论</span><textarea aria-label="报告反馈" value={feedback} onChange={e => setFeedback(e.target.value)} placeholder="输入反馈意见"/></label>{report.action_suggestions.length > 0 && <strong>采纳行动建议</strong>}{report.action_suggestions.map(item => <label className="management-check" key={item.id}><input type="checkbox" checked={adopted.includes(item.id)} onChange={event => setAdopted(event.target.checked ? [...adopted, item.id] : adopted.filter(value => value !== item.id))}/>{item.text}</label>)}<label className="management-check"><input type="checkbox" checked={falsePositive} onChange={event => setFalsePositive(event.target.checked)}/>标记误报</label><button className="button button-primary" disabled={busy || report.status !== 'published' || (!feedback.trim() && !adopted.length && !falsePositive)} onClick={sendFeedback}>提交反馈</button>{report.status !== 'published' && <p className="panel-footnote">报告发布后可反馈。</p>}{message && <p role="status">{message}</p>}</div></section>
    </div></div>
  </div>
}

function ConclusionBlock({ title, items }: { title: string; items: string[] }) {
  return <div className="conclusion-block"><h3>{title}</h3>{items.length ? <ul>{items.map((item, index) => <li key={`${title}-${index}`}>{item}</li>)}</ul> : <p>暂无</p>}</div>
}

function AttachmentStrip({ reportId, items }: { reportId: string; items: ReportAttachment[] }) {
  return items.length ? <div className="attachment-strip">{items.map(item => <AttachmentImage key={item.id} reportId={reportId} item={item}/>)}</div> : null
}

function AttachmentImage({ reportId, item }: { reportId: string; item: ReportAttachment }) {
  const [src, setSrc] = useState<string>()
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let active = true; let objectUrl = ''
    api.reportAttachment(reportId, item.id).then(blob => { if (active) { objectUrl = URL.createObjectURL(blob); setSrc(objectUrl) } }).catch(() => { if (active) setFailed(true) })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [reportId, item.id])
  if (failed) return <span className="attachment-failed">图片读取失败</span>
  return src ? <a href={src} target="_blank" rel="noreferrer"><img src={src} alt={item.caption || '证据图片附件'}/>{item.caption && <small>{item.caption}</small>}</a> : <span className="attachment-loading">图片加载中…</span>
}
