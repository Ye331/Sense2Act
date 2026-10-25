import { useCallback, useEffect, useState } from 'react'
import { Icon } from './components/Icon'
import { ErrorBar } from './components/UI'
import { api, onUnauthorized, session, streamGlobal } from './api/client'
import type { StreamEvent, User } from './api/client'
import { errorText, investigationService } from './services/investigations'
import type { InvestigationTask } from './types/domain'
import { OverviewPage } from './pages/OverviewPage'
import { TasksPage } from './pages/TasksPage'
import { DetailPage } from './pages/DetailPage'
import { ReportPage } from './pages/ReportPage'
import { ReportsIndex } from './pages/ReportsIndex'
import { CataloguePage } from './pages/CataloguePage'
import { SourcesPage } from './pages/SourcesPage'
import { EventsPage } from './pages/EventsPage'
import { OrganizationPage } from './pages/OrganizationPage'
import { AdminPage } from './pages/AdminPage'

const nav = [
  { label: '工作台', items: [
    { href: '#/overview', label: '情报总览', icon: 'overview' as const },
    { href: '#/signals', label: '商机信号', icon: 'activity' as const },
    { href: '#/tasks', label: '调查任务', icon: 'tasks' as const },
    { href: '#/reports', label: '调查报告', icon: 'report' as const },
  ] },
  { label: '研究资料', items: [
    { href: '#/documents', label: '文档库', icon: 'file' as const },
    { href: '#/events', label: '事件图谱', icon: 'layers' as const },
  ] },
  { label: '系统管理', items: [
    { href: '#/sources', label: '信息源管理', icon: 'database' as const },
    { href: '#/admin/rules', label: '系统管理', icon: 'shield' as const },
  ] },
]
const titles: Record<string, string> = { overview: '情报总览', signals: '商机信号', tasks: '调查任务', reports: '调查报告', documents: '文档库', evidences: '证据详情', events: '事件图谱', organizations: '机构详情', sources: '信息源管理', admin: '系统管理' }
const roleLabels: Record<string, string> = { admin: '管理员', analyst: '分析员', viewer: '访客' }
const noticeLabels: Record<string, string> = { signal_created: '发现新信号', report_ready: '报告已生成', investigation_completed: '调查已完成', source_degraded: '信息源异常' }
const noticeHref = (item: StreamEvent) => item.event === 'signal_created' ? `#/signals/${item.data.signal_id}`
  : item.event === 'source_degraded' ? '#/sources'
  : item.data.investigation_id ? `#/${item.event === 'report_ready' ? 'reports' : 'tasks'}/${item.data.investigation_id}` : '#/overview'

function useHashRoute() {
  const [route, setRoute] = useState(() => window.location.hash || '#/overview')
  useEffect(() => { const handler = () => setRoute(window.location.hash || '#/overview'); window.addEventListener('hashchange', handler); return () => window.removeEventListener('hashchange', handler) }, [])
  return route
}

export default function App() {
  const route = useHashRoute()
  const [user, setUser] = useState<User | null>(null)
  const [authChecked, setAuthChecked] = useState(false)
  const [tasks, setTasks] = useState<InvestigationTask[]>([])
  const [loading, setLoading] = useState(true)
  const [taskLoadError, setTaskLoadError] = useState('')
  const [notifications, setNotifications] = useState<StreamEvent[]>([])
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [revision, setRevision] = useState(0)

  const refresh = useCallback(() => {
    setRevision(value => value + 1)
    return investigationService.listTasks().then(items => { setTasks(items); setTaskLoadError('') }).catch(error => setTaskLoadError(errorText(error))).finally(() => setLoading(false))
  }, [])

  useEffect(() => onUnauthorized(() => setUser(null)), [])
  useEffect(() => { window.scrollTo({ top: 0, behavior: 'instant' }) }, [route])
  useEffect(() => {
    if (!session.token()) { setAuthChecked(true); return }
    api.me().then(setUser).catch(() => session.clear()).finally(() => setAuthChecked(true))
  }, [])
  useEffect(() => {
    if (!user) return
    refresh()
    // 全局流驱动刷新：新信号 / 报告 / 完成事件到达即重新拉取，页面不轮询。
    const subscription = streamGlobal(event => {
      setNotifications(current => current.some(item => item.id === event.id) ? current : [event, ...current].slice(0, 30))
      refresh()
    })
    return () => subscription.close()
  }, [user, refresh])

  if (!authChecked) return <div className="loading-state">正在连接后端…</div>
  if (!user) return <LoginScreen onLogin={setUser}/>

  const parts = route.split('?')[0].replace(/^#\/?/, '').split('/').filter(Boolean)
  const section = parts[0] || 'overview'
  const id = parts[1]
  const page = section === 'tasks' && id ? <DetailPage key={id} id={id} onChange={refresh}/>
    : section === 'tasks' ? <TasksPage tasks={tasks} loadError={taskLoadError} onCreated={created => { refresh(); window.location.hash = `#/tasks/${created.investigation_id}` }}/>
    : section === 'documents' || section === 'signals' || section === 'evidences' ? <CataloguePage key={`${section}/${id || ''}/${revision}`} section={section} id={id} onChange={refresh}/>
    : section === 'reports' && id ? <ReportPage key={id} investigationId={id}/>
    : section === 'reports' ? <ReportsIndex tasks={tasks}/>
    : section === 'sources' ? <SourcesPage canEdit={user.role === 'admin'}/>
    : section === 'events' ? <EventsPage id={id}/>
    : section === 'organizations' ? <OrganizationPage id={id}/>
    : section === 'admin' ? <AdminPage key={user.role} tab={id} id={parts[2]} isAdmin={user.role === 'admin'}/>
    : <OverviewPage tasks={tasks} taskLoadError={taskLoadError}/>

  const logout = () => { session.clear(); setUser(null); setTasks([]) }

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#/overview" aria-label="SignalX 首页"><span className="brand-mark"><span/></span><span><strong>SignalX</strong><small>招投标情报研判</small></span></a>
      <nav className="main-nav" aria-label="主导航">{nav.map(group => <div className="nav-group" key={group.label}><div className="nav-caption">{group.label}</div>{group.items.map(item => <a key={item.href} className={`nav-link ${section === item.href.slice(2).split('/')[0] ? 'active' : ''}`} href={item.href}><Icon name={item.icon} size={17}/>{item.label}</a>)}</div>)}</nav>
      <div className="sidebar-bottom"><div className="sidebar-footer">SignalX</div></div>
    </aside>
    <div className="workspace">
      <header className="topbar">
        <div className="topbar-path"><span>SignalX</span><Icon name="chevron" size={13}/><strong>{section === 'tasks' && id ? '调查详情' : titles[section] || '情报总览'}</strong></div>
        <div className="topbar-right">
          <div className="notification-anchor"><button className="topbar-action notification-button" aria-label="通知" aria-expanded={notificationsOpen} onClick={() => setNotificationsOpen(!notificationsOpen)}><Icon name="bell" size={17}/>通知 {notifications.length ? <b>{notifications.length}</b> : null}</button>
            {notificationsOpen && <div className="notification-popover"><strong>实时通知</strong>{notifications.length ? notifications.map(item => <a key={item.id} className="notification-item" href={noticeHref(item)} onClick={() => setNotificationsOpen(false)}><b>{noticeLabels[item.event] || item.event}</b><span>{String(item.data.title || item.data.name || tasks.find(task => task.id === item.data.investigation_id)?.title || item.data.investigation_id || item.data.signal_id || '')}</span></a>) : <p>暂无通知</p>}</div>}
          </div>
          <span className="topbar-divider"/>
          <button className="topbar-action user-button" title="退出登录" onClick={logout}><span className="avatar">{(roleLabels[user.role] || '用').slice(0, 1)}</span><span className="topbar-user">{user.name} · {roleLabels[user.role] || user.role}</span></button>
        </div>
      </header>
      <main className="main-content">{taskLoadError && !loading && section === 'tasks' && !id && <ErrorBar message={`调查任务加载失败：${taskLoadError}`}/>} {loading ? <div className="loading-state">正在加载数据…</div> : page}</main>
    </div>
  </div>
}

function LoginScreen({ onLogin }: { onLogin: (user: User) => void }) {
  const [email, setEmail] = useState('analyst@sense2act.local')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try { const result = await api.login(email, password); session.save(result.token); onLogin(result.user) }
    catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  return <div className="modal-backdrop" role="presentation"><form className="modal" role="dialog" aria-modal="true" aria-label="登录" onSubmit={submit}>
    <div className="modal-header"><div><h2>登录 SignalX</h2><p>使用后端账号登录（首次启动的种子账号见 README）。</p></div></div>
    <div className="form-body">
      <label className="field"><span>邮箱</span><input aria-label="邮箱" type="email" autoComplete="username" value={email} onChange={event => setEmail(event.target.value)}/></label>
      <label className="field"><span>密码</span><input aria-label="密码" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)}/></label>
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
    <div className="modal-footer"><button className="button button-primary" disabled={busy || !email || !password}>{busy ? '登录中…' : '登录'}</button></div>
  </form></div>
}
