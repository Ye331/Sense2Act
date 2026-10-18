import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { StatusBadge } from '../components/UI'
import { api } from '../api/client'
import type { Dashboard, Report, Signal } from '../api/client'
import { errorText, investigationService, listReports, listSignals, ruleNames, score } from '../services/investigations'
import type { ActivityView, InvestigationTask } from '../types/domain'
import './OverviewPage.css'

export function OverviewPage({ tasks }: { tasks: InvestigationTask[] }) {
  const [summary, setSummary] = useState<Dashboard>()
  const [signals, setSignals] = useState<Signal[]>([])
  const [reports, setReports] = useState<Report[]>([])
  const [activities, setActivities] = useState<ActivityView[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    Promise.all([api.dashboard(), listSignals({ status: 'pending' }), listReports(), investigationService.listActivities(tasks)])
      .then(([totals, items, ready, recent]) => {
        if (active) { setSummary(totals); setSignals(items); setReports(ready); setActivities(recent) }
      })
      .catch(err => { if (active) setError(errorText(err)) })
    return () => { active = false }
  }, [tasks])
  const pending = [...signals].sort((a, b) => score(b) - score(a))
  const activeTasks = tasks.filter(task => ['queued', 'running', 'reporting'].includes(task.status))
  const latestReports = [...reports].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 3)

  return <div className="page overview-page">
    <header className="ov-header"><div><h1>情报总览</h1><p>研判线索，跟进调查，形成判断。</p></div><a className="button button-primary" href="#/signals">处理商机信号 <Icon name="arrow" size={16}/></a></header>
    {error && <p className="form-error" role="alert">{error}</p>}
    <nav className="ov-totals" aria-label="业务统计">
      <a href="#/signals"><span>待研判信号</span><strong className="ov-pending-total">{summary?.signals.by_status?.pending ?? pending.length}</strong></a>
      <a href="#/tasks"><span>调查任务</span><strong>{summary?.investigations.total ?? tasks.length}</strong><small>{activeTasks.length} 项进行中</small></a>
      <a href="#/reports"><span>已形成报告</span><strong>{summary?.reports.total ?? reports.length}</strong></a>
      <a href="#/documents"><span>采集文档</span><strong>{summary?.documents.total ?? 0}</strong><small>今日 {summary?.documents.today ?? 0}</small></a>
    </nav>
    <div className="ov-desk">
      <div className="ov-work">
        <section className="ov-signals" aria-labelledby="ov-signals-title">
          <div className="ov-section-head"><h2 id="ov-signals-title">待研判信号 <span>{pending.length}</span></h2><a href="#/signals">全部信号 <Icon name="arrow" size={14}/></a></div>
          {pending.length ? <div className="ov-signal-list">
            <div className="ov-signal-columns" aria-hidden="true"><span>项目与采购机构</span><span>评分</span><span>状态</span><span>操作</span></div>
            {pending.slice(0, 4).map(signal => <article className="ov-signal-row" key={signal.id}>
              <div className="ov-signal-content"><h3><a href={`#/signals/${signal.id}`}>{signal.document.title}</a></h3><div className="ov-signal-meta"><span>{signal.org?.name}</span><span className="ov-reasons">{signal.hits.map(hit => ruleNames[hit.rule_type] || hit.rule_type).join('、') || '商机线索'}</span></div></div>
              <span className="ov-score" aria-label={`信号评分 ${score(signal)}`}><strong>{score(signal)}</strong><small>/100</small></span><span className="ov-signal-status">待研判</span>
              <a className="ov-review-link" href={`#/signals/${signal.id}`} aria-label={`进入研判：${signal.document.title}`}>研判 <Icon name="arrow" size={14}/></a>
            </article>)}
            {pending.length > 4 && <a className="ov-more" href="#/signals">还有 {pending.length - 4} 条待研判信号，进入列表查看 →</a>}
          </div> : <div className="ov-empty"><strong>待研判信号已处理完毕</strong><p>新信号到达后会显示在这里。</p><a href="#/signals">查看已处理信号 →</a></div>}
        </section>
        <section className="ov-reports" aria-labelledby="ov-reports-title">
          <div className="ov-section-head"><h2 id="ov-reports-title">最新报告</h2><a href="#/reports">全部报告 <Icon name="arrow" size={14}/></a></div>
          {latestReports.length ? <div className="ov-document-list">{latestReports.map(report => <article className="ov-document" key={report.id}>
            <Icon name="report" size={20}/><div className="ov-document-body"><h3><a href={`#/reports/${report.investigation_id}`}>{report.title}</a></h3><p>{report.summary}</p><div className="ov-document-meta"><span className={report.status === 'published' ? 'ov-published' : ''}>{report.status === 'published' ? '已发布' : '草稿'}</span><span>{report.claims.length} 条结论</span></div></div>
            <a className="ov-document-link" href={`#/reports/${report.investigation_id}`} aria-label={`查看报告：${report.title}`}>查看报告 <Icon name="arrow" size={14}/></a>
          </article>)}</div> : <div className="ov-empty"><p>调查完成后，报告将显示在这里。</p><a href="#/tasks">查看调查任务 →</a></div>}
        </section>
      </div>
      <aside className="ov-followup" aria-label="调查跟进与业务动态">
        <section className="ov-investigations" aria-labelledby="ov-investigations-title">
          <div className="ov-section-head"><h2 id="ov-investigations-title">调查跟进 <span>{activeTasks.length}</span></h2><a href="#/tasks">任务列表 <Icon name="arrow" size={14}/></a></div>
          {activeTasks.length ? <ul className="ov-task-list">{activeTasks.slice(0, 4).map(task => <li key={task.id}>
            <div className="ov-task-status"><StatusBadge status={task.status}/><span>第 {task.currentRound} 轮 · {task.evidenceCount} 条证据</span></div><h3><a href={`#/tasks/${task.id}`}>{task.title}</a></h3><div className="ov-task-meta"><span>{task.subject}</span><a href={`#/tasks/${task.id}`}>调查详情 →</a></div>
          </li>)}</ul> : <div className="ov-task-empty"><p>当前没有进行中的调查</p><span>可从待研判信号发起新调查。</span><a href="#/tasks">查看历史任务 →</a></div>}
          {activeTasks.length > 4 && <a className="ov-more" href="#/tasks">查看其余 {activeTasks.length - 4} 项任务 →</a>}
        </section>
        <section className="ov-activity" aria-labelledby="ov-activity-title">
          <div className="ov-section-head"><h2 id="ov-activity-title">最近动态</h2></div>
          {activities.length ? <ol className="ov-timeline">{activities.slice(0, 4).map(item => <li key={item.id}><time>{item.time}</time><a href={`#/tasks/${item.taskId}`}>{item.title}<Icon name="arrow" size={13}/></a><p>{item.description}</p></li>)}</ol> : <p className="ov-quiet">暂无新的业务动态。</p>}
        </section>
      </aside>
    </div>
  </div>
}
