import { useEffect, useMemo, useState } from 'react'
import { Icon } from '../components/Icon'
import { EmptyState, PageHeading, StatusBadge } from '../components/UI'
import { api } from '../api/client'
import type { InvestigateResult, Signal } from '../api/client'
import { errorText, formatDate, listSignals } from '../services/investigations'
import type { InvestigationTask, TaskStatus } from '../types/domain'

export function TasksPage({ tasks, loadError, onCreated }: { tasks: InvestigationTask[]; loadError: string; onCreated: (created: InvestigateResult) => void }) {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<'all' | TaskStatus>('all')
  const [showForm, setShowForm] = useState(() => window.location.hash.includes('?new=1'))
  const filtered = useMemo(() => tasks.filter(task => `${task.title} ${task.subject} ${task.id}`.toLowerCase().includes(query.toLowerCase()) && (status === 'all' || task.status === status)), [tasks, query, status])
  return <div className="page tasks-page tasks-worklist">
    <PageHeading eyebrow="研判工作 / 调查" title="调查任务" description="高分信号由后端自动立项，Agent 领取后逐轮查证；也可从信号手动发起。" action={<button className="button button-primary" onClick={() => setShowForm(true)}><Icon name="plus" size={17}/>发起调查</button>}/>
    {!loadError && <section className="panel task-table-panel"><div className="task-toolbar"><div className="search-box"><Icon name="search" size={19}/><input aria-label="搜索任务" placeholder="搜索项目、机构 / 监测主体或任务 ID" value={query} onChange={event => setQuery(event.target.value)}/></div><div className="select-wrap"><select aria-label="筛选任务状态" value={status} onChange={event => setStatus(event.target.value as 'all' | TaskStatus)}><option value="all">全部状态</option><option value="queued">待领取</option><option value="running">调查中</option><option value="reporting">报告中</option><option value="completed">已完成</option><option value="failed">失败</option><option value="stopped">已停止</option></select></div></div>
    <div className="table-count">显示 <strong>{filtered.length}</strong> / {tasks.length} 条任务</div>
    {filtered.length ? <div className="table-scroll"><table className="task-table"><thead><tr><th>调查项目</th><th>调查状态</th><th>轮次</th><th>证据</th><th>最近更新</th><th aria-label="操作"/></tr></thead><tbody>{filtered.map(task => <tr key={task.id} onClick={() => { window.location.hash = `#/tasks/${task.id}` }}><td data-label="调查项目"><div className="task-name-cell"><span className="task-type-icon"><Icon name="file" size={19}/></span><div><strong>{task.title}</strong><small>{task.subject}</small></div></div></td><td data-label="调查状态"><StatusBadge status={task.status}/></td><td data-label="轮次">{task.currentRound} / {task.totalRounds}</td><td data-label="证据"><span className="evidence-cell">{task.evidenceCount} 条</span></td><td data-label="最近更新" className="muted-cell">{formatDate(task.updatedAt)}</td><td data-label="操作"><a className="table-view" href={`#/tasks/${task.id}`} onClick={event => event.stopPropagation()}>查看详情 <Icon name="arrow" size={15}/></a></td></tr>)}</tbody></table></div> : <EmptyState title={tasks.length ? "没有匹配的任务" : "暂无调查任务"} text={tasks.length ? "试试其他关键词，或从信号列表发起调查。" : "可从待研判信号发起调查，或等待系统自动立项。"} action={<a className="button button-secondary" href="#/signals">查看信号</a>}/>}</section>}
    {showForm && <InvestigationModal onClose={() => { setShowForm(false); if (window.location.hash.includes('?new=1')) window.location.hash = '#/tasks' }} onCreated={onCreated}/>}
  </div>
}

function InvestigationModal({ onClose, onCreated }: { onClose: () => void; onCreated: (created: InvestigateResult) => void }) {
  const [signals, setSignals] = useState<Signal[]>([])
  const [signalId, setSignalId] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => { listSignals({ status: 'pending' }).then(items => { const available = items.filter(item => !item.investigation_id); setSignals(available); setSignalId(available[0]?.id || '') }).catch(err => setError(errorText(err))) }, [])
  useEffect(() => { const onEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }; window.addEventListener('keydown', onEscape); return () => window.removeEventListener('keydown', onEscape) }, [onClose])
  const submit = async (event: React.FormEvent) => { event.preventDefault(); setSaving(true); setError(''); try { onCreated(await api.investigateSignal(signalId)); onClose() } catch (err) { setError(errorText(err)) } finally { setSaving(false) } }
  return <div className="modal-backdrop investigation-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}><div className="modal investigation-modal" role="dialog" aria-modal="true" aria-labelledby="investigate-title"><div className="modal-header"><div><span className="eyebrow">新建调查</span><h2 id="investigate-title">发起调查</h2><p>选择待研判信号，调查进入队列后由 Agent 服务领取执行。</p></div><button className="icon-button" onClick={onClose} aria-label="关闭"><Icon name="close" size={20}/></button></div><form onSubmit={submit}><div className="form-body"><div className="field"><label htmlFor="signal-select">商机信号</label><select id="signal-select" value={signalId} onChange={e => setSignalId(e.target.value)}>{signals.map(signal => <option key={signal.id} value={signal.id}>{signal.document.title} · {signal.org?.name}</option>)}</select></div>{!signals.length && <p>暂无可调查的待研判信号。</p>}{error && <p className="form-error" role="alert">{error}</p>}</div><div className="modal-actions"><button type="button" className="button button-secondary" onClick={onClose}>取消</button><button className="button button-primary" disabled={!signalId || saving}>{saving ? '正在创建…' : '发起调查'}</button></div></form></div></div>
}
