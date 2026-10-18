import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { EmptyState, ErrorBar, PageHeading } from '../components/UI'
import { api } from '../api/client'
import type { DocumentDetail, DocumentItem, Evidence, Hit, Signal } from '../api/client'
import { errorText, formatDate, listDocuments, listSignals, money, newsClues, newsHit, reportCount, ruleNames, score, signalSubject, sourceNames } from '../services/investigations'

type Section = 'documents' | 'signals' | 'evidences'
const signalStatus: Record<string, string> = { pending: '待研判', confirmed: '已确认', investigating: '调查中', dismissed: '已忽略' }
const docTypes: Record<string, string> = { announcement: '公告', policy: '政策', news: '新闻' }

/** 命中明细：优先显示检测服务写入的解释文本，否则列出数值字段。 */
function hitText(hit: Hit) {
  const detail = hit.detail || {}
  if (typeof detail.explanation === 'string') return detail.explanation
  return Object.entries(detail).filter(([, value]) => typeof value !== 'object').map(([key, value]) => `${key}: ${String(value)}`).join(' · ') || '规则命中'
}

export function CataloguePage({ section, id, onChange }: { section: Section; id?: string; onChange: () => void }) {
  const [documents, setDocuments] = useState<DocumentItem[]>([])
  const [signals, setSignals] = useState<Signal[]>([])
  const [detail, setDetail] = useState<any>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [dismissOpen, setDismissOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [signalFilter, setSignalFilter] = useState('all')
  const [snapshot, setSnapshot] = useState<{ content: string; contentType: string }>()
  useEffect(() => { let active = true; (async () => {
    if (id) {
      const result = section === 'documents' ? await api.document(id) : section === 'signals' ? await api.signal(id) : await api.evidence(id)
      if (active) setDetail(result)
    } else if (section === 'documents') { const items = await listDocuments(); if (active) setDocuments(items) }
    else if (section === 'signals') { const items = await listSignals(); if (active) setSignals(items) }
  })().catch(err => { if (active) setError(errorText(err)) }); return () => { active = false } }, [section, id])

  const act = async (operation: 'investigate' | 'confirmed' | 'dismissed', reason?: string) => { if (!id) return; setBusy(true); setError(''); try {
    if (operation === 'investigate') { const created = await api.investigateSignal(id); onChange(); window.location.hash = `#/tasks/${created.investigation_id}`; return }
    setDetail(await api.setSignalStatus(id, operation, reason)); setDismissOpen(false); onChange()
  } catch (err) { setError(errorText(err)) } finally { setBusy(false) } }
  const openSnapshot = async () => { if (!id) return; setBusy(true); setError(''); try { setSnapshot(section === 'documents' ? await api.documentSnapshot(id) : await api.evidenceSnapshot(id)) } catch (err) { setError(errorText(err)) } finally { setBusy(false) } }

  const title = section === 'documents' ? '文档库' : section === 'signals' ? '商机信号' : '证据详情'
  const matches = (text: string) => text.toLowerCase().includes(query.toLowerCase())
  const visibleDocuments = documents.filter(doc => matches(`${doc.title} ${doc.org?.name} ${doc.category} ${doc.region}`))
  const visibleSignals = signals.filter(signal => matches(`${signal.document.title} ${signal.org?.name} ${signal.id}`) && (signalFilter === 'all' || signal.status === signalFilter))
  const backHref = section === 'evidences' ? detail?.investigation_id ? `#/reports/${detail.investigation_id}` : '#/reports' : `#/${section}`

  return <div className="page tasks-page">{id && <a className="back-link" href={backHref}>← {section === 'evidences' ? '返回报告' : '返回列表'}</a>}
    <PageHeading eyebrow={section === 'signals' ? '研判工作 / 信号' : '资料与关联'} title={title} description={section === 'documents' ? '检索采集的公告与新闻，追溯关联信号与机构。' : section === 'signals' ? '检测服务推送的商机线索：确认、忽略或发起调查。' : '调查结论背后的原始证据与快照。'}/>
    <ErrorBar message={error}/>
    {!id && section !== 'evidences' && <section className="panel task-table-panel"><div className="task-toolbar"><div className="search-box"><Icon name="search" size={18}/><input aria-label="搜索" placeholder="搜索项目、机构或编号" value={query} onChange={event => setQuery(event.target.value)}/></div>{section === 'signals' && <div className="select-wrap"><select aria-label="筛选信号状态" value={signalFilter} onChange={event => setSignalFilter(event.target.value)}><option value="all">全部状态</option>{Object.entries(signalStatus).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>}</div>
      <div className="table-count">显示 <strong>{section === 'documents' ? visibleDocuments.length : visibleSignals.length}</strong> / {section === 'documents' ? documents.length : signals.length} 条</div>
      {section === 'signals' ? visibleSignals.length ? <div className="signal-brief-list">{visibleSignals.map(signal => <article className="signal-brief" key={signal.id}>
        <div className="signal-brief-score"><strong>{score(signal)}</strong><span>信号评分</span></div>
        <div><div className="signal-brief-heading"><h2><a href={`#/signals/${signal.id}`}>{signal.document.title}</a></h2><span className={`signal-status signal-status-${signal.status}`}>{signalStatus[signal.status] || signal.status}</span></div>
          <div className="signal-brief-meta">{newsHit(signal) ? <><span>监测主体 {signalSubject(signal)}</span><span>发布 {signal.document.publish_date}</span><span>{reportCount(signal)} 篇报道</span></> : <>{signal.org && <a href={`#/organizations/${signal.org.id}`}>{signal.org.name} ↗</a>}<span>发布 {signal.document.publish_date}</span><span>预算 {money(signal.document.amount)}</span></>}</div>
          <div className="signal-brief-rules">{signal.hits.map((hit, index) => <span key={`${hit.rule_type}-${index}`}>{ruleNames[hit.rule_type] || hit.rule_type}</span>)}</div></div>
        <a className="button button-secondary signal-brief-action" href={`#/signals/${signal.id}`}>进入研判 <Icon name="arrow" size={15}/></a>
      </article>)}</div> : <EmptyState title="暂无信号" text="检测服务发现商机后会出现在这里。"/>
      : visibleDocuments.length ? <div className="table-scroll"><table className="task-table"><thead><tr><th>标题</th><th>机构</th><th>金额</th><th>发布日期</th><th>操作</th></tr></thead><tbody>{visibleDocuments.map(doc => <tr key={doc.id}><td><strong>{doc.doc_type !== 'announcement' && <span className="doc-type-tag">{docTypes[doc.doc_type] || doc.doc_type}</span>}{doc.title}</strong>{(doc.category || doc.region) && <small className="management-subline">{[doc.category, doc.region].filter(Boolean).join(' · ')}</small>}</td><td>{doc.org?.name || '—'}</td><td>{doc.doc_type === 'news' ? '—' : money(doc.amount)}</td><td>{doc.publish_date}</td><td><a className="table-view" href={`#/documents/${doc.id}`}>查看详情 →</a></td></tr>)}</tbody></table></div> : <EmptyState title="暂无文档" text="爬虫推送或运行演示种子脚本后会出现在这里。"/>}
    </section>}
    {id && !detail && !error && <div className="loading-state">正在读取详情…</div>}
    {id && section === 'documents' && detail && <DocumentView doc={detail} busy={busy} onSnapshot={openSnapshot}/>}
    {id && section === 'signals' && detail && <section className="panel signal-detail"><div className="signal-detail-head"><div><span className="section-kicker">信号研判 / {detail.id}</span><h2>{detail.document.title}</h2><p>{newsHit(detail) ? `监测主体 ${signalSubject(detail)}` : signalSubject(detail)} · <span className={`signal-status signal-status-${detail.status}`}>{signalStatus[detail.status] || detail.status}</span></p></div><div className="signal-detail-score"><strong>{score(detail)}</strong><span>信号评分 / 100</span></div></div>
      <div className="signal-detail-body"><div className="signal-detail-main"><h3>关注原因</h3><p className="signal-detail-intro">检测服务命中 {detail.hits.length} 项规则，以下依据来自检测解释。</p><div className="hit-list">{(detail as Signal).hits.map((hit, index) => <div className="hit-row" key={index}><span className="hit-number">{String(index + 1).padStart(2, '0')}</span><div><strong>{ruleNames[hit.rule_type] || hit.rule_type}</strong><p>{hitText(hit)}</p></div><span>权重 {hit.weight}</span></div>)}</div>
        <div className="signal-detail-actions">{!detail.investigation_id && detail.status === 'pending' && <button className="button button-primary" disabled={busy} onClick={() => act('investigate')}>发起调查</button>}{detail.investigation_id && <a className="button button-primary" href={`#/tasks/${detail.investigation_id}`}>查看调查过程 →</a>}<button className="button button-secondary" disabled={busy || detail.status !== 'pending'} onClick={() => act('confirmed')}>确认信号</button><button className="button button-secondary" disabled={busy || detail.status !== 'pending'} onClick={() => { setError(''); setDismissOpen(true) }}>忽略信号</button></div></div>
        <aside className="signal-detail-links"><h3>关联资料</h3>{newsHit(detail) ? newsClues(detail).map(clue => <a key={clue.clue_id} href={`#/documents/${clue.clue_id}`}><strong>{clue.source} · {formatDate(clue.published_at)}</strong><span>{clue.title} →</span></a>) : <a href={`#/documents/${detail.document.id}`}><strong>原始公告</strong><span>{detail.document.title} →</span></a>}{detail.org && <a href={`#/organizations/${detail.org.id}`}><strong>采购机构</strong><span>{detail.org.name} →</span></a>}{detail.investigation_id && <a href={`#/tasks/${detail.investigation_id}`}><strong>调查任务</strong><span>{detail.investigation_id} →</span></a>}</aside></div></section>}
    {id && section === 'evidences' && detail && <EvidenceView evidence={detail} busy={busy} onSnapshot={openSnapshot}/>}
    {!id && section === 'evidences' && <EmptyState title="请选择证据" text="从调查报告的证据列表打开具体证据。"/>}
    {dismissOpen && <ConfirmDialog title="忽略此信号" description="记录忽略原因后，此信号退出待研判列表，原因将作为回测标注。" confirmLabel="确认忽略" reasonRequired busy={busy} error={error} onClose={() => setDismissOpen(false)} onConfirm={reason => act('dismissed', reason)}/>}
    {snapshot && <div className="modal-backdrop" role="presentation"><div className="modal snapshot-modal" role="dialog" aria-modal="true" aria-label="原始快照"><div className="modal-header"><div><h2>原始快照</h2><p>隔离预览 · {snapshot.contentType}</p></div><button className="icon-button" aria-label="关闭快照" onClick={() => setSnapshot(undefined)}><Icon name="close"/></button></div><iframe title="隔离的原始快照" sandbox="" referrerPolicy="no-referrer" srcDoc={snapshot.contentType.includes('html') ? snapshot.content : `<pre style="white-space:pre-wrap">${snapshot.content.replace(/[&<>]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[ch]!)}</pre>`}/></div></div>}
  </div>
}

function DocumentView({ doc, busy, onSnapshot }: { doc: DocumentDetail; busy: boolean; onSnapshot: () => void }) {
  const news = doc.doc_type === 'news'
  const source = String(doc.raw?.source_name || doc.raw?.site || '') || '—'
  return <section className="panel document-detail"><div className="document-detail-head"><span className="section-kicker">{news ? '新闻原文' : '公告原文'} / {doc.id}</span><h2>{doc.title}</h2><p>{[news ? source : doc.category, doc.region, `发布于 ${doc.publish_date || '—'}`].filter(Boolean).join(' · ')}</p></div><div className="document-detail-grid"><div className="document-detail-main"><h3>{news ? '报道正文' : '公告正文'}</h3><p>{doc.content_text || '（无正文）'}</p><div className="management-actions"><button className="button button-secondary" disabled={busy} onClick={onSnapshot}>查看原始快照</button>{doc.url && <a className="button button-secondary" href={doc.url} target="_blank" rel="noreferrer">打开原网页 ↗</a>}</div><h3>关联信号</h3>{doc.related_signals?.length ? doc.related_signals.map(signal => <a className="document-signal" key={signal.id} href={`#/signals/${signal.id}`}><span className={`signal-status signal-status-${signal.status}`}>{signalStatus[signal.status] || signal.status}</span><strong>{signal.id}</strong><span>进入信号研判 →</span></a>) : <p>暂无关联信号</p>}</div>{news ? <dl className="document-facts"><div><dt>报道来源</dt><dd>{source}</dd></div><div><dt>平台</dt><dd>{String(doc.raw?.platform || '—')}</dd></div><div><dt>发布日期</dt><dd>{doc.publish_date || '—'}</dd></div></dl>
    : <dl className="document-facts"><div><dt>采购单位</dt><dd>{doc.org ? <a href={`#/organizations/${doc.org.id}`}>{doc.org.name} →</a> : '—'}</dd></div><div><dt>采购预算</dt><dd>{money(doc.amount)}</dd></div><div><dt>发布日期</dt><dd>{doc.publish_date || '—'}</dd></div><div><dt>截止日期</dt><dd>{doc.deadline || '—'}</dd></div><div><dt>所在地区</dt><dd>{doc.region || '—'}</dd></div></dl>}</div></section>
}

function EvidenceView({ evidence, busy, onSnapshot }: { evidence: Evidence; busy: boolean; onSnapshot: () => void }) {
  return <section className="panel report-section"><div className="panel-heading"><h2>{evidence.title}</h2></div><div className="management-detail"><p>{evidence.excerpt}</p><div className="management-actions"><button className="button button-secondary" disabled={busy} onClick={onSnapshot}>查看原始快照</button>{evidence.url && <a className="button button-secondary" href={evidence.url} target="_blank" rel="noreferrer">打开来源 ↗</a>}</div></div><dl className="report-meta"><div><dt>来源类型</dt><dd>{sourceNames[evidence.source_type] || evidence.source_type}</dd></div><div><dt>发布时间</dt><dd>{formatDate(evidence.published_at)}</dd></div><div><dt>获取时间</dt><dd>{formatDate(evidence.fetched_at)}</dd></div><div><dt>SHA-256</dt><dd className="mono-value">{evidence.content_hash?.slice(0, 16)}…</dd></div></dl>{evidence.doc_id && <div className="management-detail"><a className="text-link" href={`#/documents/${evidence.doc_id}`}>查看关联文档 →</a></div>}</section>
}
