import { useEffect, useState } from 'react'
import { Icon } from '../components/Icon'
import { ErrorBar, ErrorState, StatusBadge } from '../components/UI'
import { api } from '../api/client'
import type { Dashboard, Report, Signal } from '../api/client'
import { errorText, investigationService, listReports, listSignals, ruleNames, score, signalSubject } from '../services/investigations'
import type { ActivityView, InvestigationTask } from '../types/domain'
import './OverviewPage.css'

export function OverviewPage({ tasks, taskLoadError }: { tasks: InvestigationTask[]; taskLoadError: string }) {
  const [summary, setSummary] = useState<Dashboard>()
  const [signals, setSignals] = useState<Signal[]>([])
  const [reports, setReports] = useState<Report[]>([])
  const [activities, setActivities] = useState<ActivityView[]>([])
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    let active = true
    setLoaded(false); setError('')
    // 总览只展示最新 3 份报告：只取这 3 份的详情（报告总数来自看板统计）
    Promise.all([api.dashboard(), listSignals({ status: 'pending' }), listReports(3), investigationService.listActivities(tasks)])
      .then(([totals, items, ready, recent]) => {
        if (active) { setSummary(totals); setSignals(items); setReports(ready); setActivities(recent) }
      })
      .catch(err => { if (active) setError(errorText(err)) })
      .finally(() => { if (active) setLoaded(true) })
    return () => { active = false }
  }, [tasks])
  const pending = [...signals].sort((a, b) => score(b) - score(a))
  const activeTasks = tasks.filter(task => ['queued', 'running', 'reporting'].includes(task.status))
  const latestReports = [...reports].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 3)

  return <div className="page overview-page">
    <header className="ov-header"><div className="ov-header-copy"><span className="ov-eyebrow">工作台</span><h1>情报总览</h1><p>研判线索，跟进调查，形成判断。</p></div><a className="button button-primary" href="#/signals">处理商机信号 <Icon name="arrow" size={16}/></a></header>
    {!loaded ? <div className="loading-state" role="status">正在加载情报总览…</div> : error ? <ErrorState message={error}/> : <>
    <ErrorBar message={taskLoadError ? `调查任务加载失败：${taskLoadError}` : ''}/>
    <nav className="ov-totals" aria-label="业务统计">
      <div className="ov-totals-grid">
        <a href="#/signals"><span className="ov-metric-icon ov-metric-signal"><Icon name="activity" size={23}/></span><span className="ov-metric-content"><span>待研判信号</span><strong className="ov-pending-total">{summary?.signals.by_status?.pending ?? pending.length}</strong></span></a>
        <a href="#/tasks"><span className="ov-metric-icon ov-metric-task"><Icon name="tasks" size={23}/></span><span className="ov-metric-content"><span>调查任务</span><strong>{summary?.investigations.total ?? tasks.length}</strong><small>{taskLoadError ? '进行中数量暂不可用' : `${activeTasks.length} 项进行中`}</small></span></a>
        <a href="#/reports"><span className="ov-metric-icon ov-metric-report"><Icon name="report" size={23}/></span><span className="ov-metric-content"><span>已形成报告</span><strong>{summary?.reports.total ?? reports.length}</strong></span></a>
        <a href="#/documents"><span className="ov-metric-icon ov-metric-document"><Icon name="database" size={23}/></span><span className="ov-metric-content"><span>采集文档</span><strong>{summary?.documents.total ?? 0}</strong><small>今日 {summary?.documents.today ?? 0}</small></span></a>
      </div>
    </nav>
    <div className="ov-desk">
      <div className="ov-work">
        <section className="ov-signals" aria-labelledby="ov-signals-title">
          <div className="ov-section-head"><h2 id="ov-signals-title"><i className="ov-dot" aria-hidden="true"/>待研判信号 <span>{pending.length}</span></h2><a href="#/signals">查看全部 <Icon name="arrow" size={14}/></a></div>
          {pending.length ? <div className="ov-signal-list">
            <div className="ov-signal-columns" aria-hidden="true"><span>信号与研究对象</span><span>评分</span><span>状态</span><span>操作</span></div>
            {pending.slice(0, 4).map(signal => <article className="ov-signal-row" key={signal.id}>
              <div className="ov-signal-content"><h3><a href={`#/signals/${signal.id}`}>{signal.document.title}</a></h3><div className="ov-signal-meta"><span>{signalSubject(signal)}</span><span className="ov-reasons">{signal.hits.map(hit => ruleNames[hit.rule_type] || hit.rule_type).join('、') || '商机线索'}</span></div></div>
              <span className="ov-score" aria-label={`信号评分 ${score(signal)}`}><strong>{score(signal)}</strong><small>/100</small></span><span className="ov-signal-status">待研判</span>
              <a className="ov-review-link" href={`#/signals/${signal.id}`} aria-label={`进入研判：${signal.document.title}`}>研判 <Icon name="arrow" size={14}/></a>
            </article>)}
            {pending.length > 4 && <a className="ov-more" href="#/signals">还有 {pending.length - 4} 条待研判信号，进入列表查看 →</a>}
          </div> : !loaded ? <div className="ov-empty"><p>正在加载待研判信号…</p></div> : <div className="ov-empty"><span className="ov-empty-icon" aria-hidden="true"><Icon name="activity" size={28}/></span><strong>暂无待研判信号</strong><p>新信号到达后会显示在这里。</p><a href="#/signals">查看已处理信号 <Icon name="arrow" size={15}/></a></div>}
        </section>
        <section className="ov-reports" aria-labelledby="ov-reports-title">
          <div className="ov-section-head"><h2 id="ov-reports-title"><i className="ov-dot" aria-hidden="true"/>最新报告</h2><a href="#/reports">全部报告 <Icon name="arrow" size={14}/></a></div>
          {latestReports.length ? <div className="ov-document-list">{latestReports.map(report => <article className="ov-document" key={report.id}>
            <Icon name="report" size={20}/><div className="ov-document-body"><h3><a href={`#/reports/${report.investigation_id}`}>{report.title}</a></h3><p>{report.summary}</p><div className="ov-document-meta"><span className={report.status === 'published' ? 'ov-published' : ''}>{report.status === 'published' ? '已发布' : '草稿'}</span><span>{report.claims.length} 条结论</span></div></div>
            <a className="ov-document-link" href={`#/reports/${report.investigation_id}`} aria-label={`查看报告：${report.title}`}>查看报告 <Icon name="arrow" size={14}/></a>
          </article>)}</div> : !loaded ? <div className="ov-empty"><p>正在加载最新报告…</p></div> : <div className="ov-empty"><span className="ov-empty-icon" aria-hidden="true"><Icon name="report" size={27}/></span><div><strong>暂无研究报告</strong><p>调查完成后，报告将显示在这里。</p><a href="#/tasks">查看调查任务 <Icon name="arrow" size={15}/></a></div></div>}
        </section>
      </div>
      <aside className="ov-followup" aria-label="调查跟进与业务动态">
        <section className="ov-investigations" aria-labelledby="ov-investigations-title">
          <div className="ov-section-head"><h2 id="ov-investigations-title"><i className="ov-dot" aria-hidden="true"/>调查跟进 <span>{taskLoadError ? '—' : activeTasks.length}</span></h2><a href="#/tasks">任务列表 <Icon name="arrow" size={14}/></a></div>
          {taskLoadError ? <div className="ov-task-empty"><div><p>调查跟进暂不可用</p><span>任务数据未成功加载。</span></div></div> : activeTasks.length ? <ul className="ov-task-list">{activeTasks.slice(0, 4).map(task => <li key={task.id}>
            <div className="ov-task-status"><StatusBadge status={task.status}/><span>第 {task.currentRound} 轮 · {task.evidenceCount} 条证据</span></div><h3><a href={`#/tasks/${task.id}`}>{task.title}</a></h3><div className="ov-task-meta"><span>{task.subject}</span><a href={`#/tasks/${task.id}`}>调查详情 →</a></div>
          </li>)}</ul> : <div className="ov-task-empty"><span className="ov-empty-icon" aria-hidden="true"><Icon name="tasks" size={27}/></span><div><p>暂无进行中的调查</p><span>可从待研判信号发起新调查。</span><a href="#/tasks">查看历史任务 <Icon name="arrow" size={15}/></a></div></div>}
          {activeTasks.length > 4 && <a className="ov-more" href="#/tasks">查看其余 {activeTasks.length - 4} 项任务 →</a>}
        </section>
        <section className="ov-activity" aria-labelledby="ov-activity-title">
          <div className="ov-section-head"><h2 id="ov-activity-title"><i className="ov-dot" aria-hidden="true"/>最近动态</h2></div>
          {taskLoadError ? <div className="ov-activity-empty"><div><strong>最近动态暂不可用</strong><p>任务数据未成功加载。</p></div></div> : activities.length ? <ol className="ov-timeline">{activities.slice(0, 4).map(item => <li key={item.id}><time>{item.time}</time><a href={`#/tasks/${item.taskId}`}>{item.title}<Icon name="arrow" size={13}/></a><p>{item.description}</p></li>)}</ol> : <div className="ov-activity-empty"><span className="ov-empty-icon" aria-hidden="true"><Icon name="layers" size={27}/></span><div><strong>暂无新的业务动态</strong><p>调查更新后，动态会显示在这里。</p></div></div>}
        </section>
      </aside>
    </div></>}
  </div>
}
