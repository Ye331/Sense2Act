import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { EmptyState, ErrorBar, PageHeading } from '../components/UI'
import type { Report } from '../api/client'
import { errorText, formatDate, listReports } from '../services/investigations'
import type { InvestigationTask } from '../types/domain'

export function ReportsIndex({ tasks }: { tasks: InvestigationTask[] }) {
  const [reports, setReports] = useState<Report[]>([])
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  useEffect(() => { let active = true; listReports().then(items => { if (active) setReports(items.sort((a, b) => b.created_at.localeCompare(a.created_at))) }).catch(reason => { if (active) setError(errorText(reason)) }); return () => { active = false } }, [tasks])
  const visible = reports.filter(report => `${report.title} ${report.summary} ${report.id}`.toLowerCase().includes(query.toLowerCase()))
  return <div className="page tasks-page">
    <PageHeading eyebrow="研判工作 / 成果" title="调查报告" description="从结论回溯证据和调查步骤，记录后续反馈。"/>
    <ErrorBar message={error}/>
    <section className="panel task-table-panel"><div className="task-toolbar"><div className="search-box"><Icon name="search" size={18}/><input aria-label="搜索报告" placeholder="搜索报告标题、摘要或编号" value={query} onChange={event => setQuery(event.target.value)}/></div></div><div className="table-count">显示 <strong>{visible.length}</strong> / {reports.length} 份报告</div>
      {visible.length ? <div className="report-library">{visible.map((report, index) => {
        const evidenceCount = new Set(report.claims.flatMap(claim => claim.evidences.map(evidence => evidence.id))).size
        return <article className="report-entry" key={report.id}>
          <span className="report-entry-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
          <div><h2><a href={`#/reports/${report.investigation_id}`}>{report.title}</a></h2><p>{report.summary}</p>
            <div className="report-entry-meta"><span><strong>{report.claims.length}</strong> 条调查结论</span><span><strong>{evidenceCount}</strong> 条关联证据</span><span>生成于 {formatDate(report.created_at)}</span><span>{report.id}</span></div>
          </div>
          <div className="report-entry-aside"><span className={`signal-status ${report.status === 'published' ? 'signal-status-confirmed' : 'signal-status-pending'}`}>{report.status === 'published' ? '已发布' : '草稿'}</span><a className="button button-secondary" href={`#/reports/${report.investigation_id}`}>阅读报告 <Icon name="arrow" size={15}/></a></div>
        </article>
      })}</div> : <EmptyState title="没有匹配的报告" text="调查完成后，报告会显示在这里。" action={<a className="button button-secondary" href="#/tasks">查看调查任务</a>}/>}</section>
  </div>
}
