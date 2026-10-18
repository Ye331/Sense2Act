import { useEffect, useState } from 'react'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { EmptyState, PageHeading } from '../components/UI'
import { api } from '../api/client'
import type { Policy } from '../api/client'
import { errorText, ruleNames } from '../services/investigations'

type Tab = 'rules' | 'policies' | 'backtests' | 'profiles'
const tabs: { id: Tab; name: string }[] = [{ id: 'rules', name: '信号规则' }, { id: 'policies', name: '调查策略' }, { id: 'backtests', name: '回测任务' }, { id: 'profiles', name: '关注画像' }]
const backtestStatus: Record<string, string> = { queued: '待执行', running: '执行中', done: '已完成', failed: '失败' }

function Rules({ onError, onNotice }: { onError: (value: string) => void; onNotice: (value: string) => void }) {
  const [rows, setRows] = useState<any[]>([])
  const [name, setName] = useState('')
  const [type, setType] = useState('amount_anomaly')
  const [weight, setWeight] = useState('0.500')
  const [editing, setEditing] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const load = async () => setRows(await api.rules())
  useEffect(() => { load().catch(error => onError(errorText(error))) }, [])
  const save = async () => { setBusy(true); onError(''); try {
    if (editing) await api.updateRule(editing, { name, type, weight: Number(weight) })
    else await api.createRule({ name, type, weight: Number(weight), params: {} })
    await load(); onNotice(editing ? '新规则版本已生成' : '规则已创建'); setEditing(null); setName(''); setWeight('0.500')
  } catch (error) { onError(errorText(error)) } finally { setBusy(false) } }
  return <><section className="panel report-section"><div className="panel-heading"><h2>{editing ? '编辑规则 · 生成新版本' : '新增信号规则'}</h2></div><div className="management-form"><label className="field"><span>名称 *</span><input aria-label="规则名称" value={name} onChange={event => setName(event.target.value)}/></label><label className="field"><span>类型</span><select aria-label="规则类型" value={type} onChange={event => setType(event.target.value)}><option value="amount_anomaly">金额异常</option><option value="frequency_burst">频率突增</option><option value="semantic_match">语义匹配</option><option value="composite">组合规则</option></select></label><label className="field"><span>权重 0–1</span><input aria-label="规则权重" type="number" min="0" max="1" step="0.001" value={weight} onChange={event => setWeight(event.target.value)}/></label><button className="button button-primary" disabled={busy} onClick={save}>{editing ? '保存新版本' : '创建规则'}</button>{editing && <button className="button button-secondary" onClick={() => { setEditing(null); setName('') }}>取消</button>}</div></section><section className="panel task-table-panel"><div className="table-count">全部版本 <strong>{rows.length}</strong> 条</div><div className="table-scroll"><table className="task-table"><thead><tr><th>名称</th><th>类型</th><th>版本</th><th>权重</th><th>状态</th><th>操作</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.id}-${row.version}-${index}`}><td>{row.name}</td><td>{ruleNames[row.type] || row.type}</td><td>v{row.version}</td><td>{row.weight}</td><td>{row.enabled ? '启用' : '停用'}</td><td><button className="button button-secondary" onClick={() => { setEditing(row.id); setName(row.name); setType(row.type); setWeight(String(row.weight)); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>编辑</button></td></tr>)}</tbody></table></div></section></>
}

function Policies({ onError, onNotice }: { onError: (value: string) => void; onNotice: (value: string) => void }) {
  const [form, setForm] = useState<Policy | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.policy().then(setForm).catch(error => onError(errorText(error))) }, [])
  const labels: Record<keyof Policy, string> = { auto_investigate_threshold: '自动调查阈值', max_concurrent_investigations: '最大并发调查数', default_max_rounds: '默认最大轮次', default_token_budget: '默认 Token 预算' }
  const save = async () => { if (!form) return; setBusy(true); onError(''); try { setForm(await api.updatePolicy(form)); onNotice('调查策略已更新') } catch (error) { onError(errorText(error)) } finally { setBusy(false) } }
  return <section className="panel report-section"><div className="panel-heading"><h2>默认调查策略</h2></div>{form ? <div className="management-form">{(Object.entries(labels) as [keyof Policy, string][]).map(([key, label]) => <label className="field" key={key}><span>{label}</span><input aria-label={label} type="number" min="0" max={key === 'auto_investigate_threshold' ? '1' : undefined} step={key === 'auto_investigate_threshold' ? '0.01' : '1'} value={form[key] ?? ''} onChange={event => setForm({ ...form, [key]: Number(event.target.value) })}/></label>)}<button className="button button-primary" disabled={busy} onClick={save}>保存策略</button></div> : <div className="loading-state">正在读取策略…</div>}</section>
}

function Backtests({ id, onError, onNotice }: { id?: string; onError: (value: string) => void; onNotice: (value: string) => void }) {
  const [rows, setRows] = useState<any[]>([])
  const [rules, setRules] = useState<any[]>([])
  const [detail, setDetail] = useState<any>()
  const [ruleId, setRuleId] = useState('')
  const [dateFrom, setDateFrom] = useState('2026-08-01')
  const [dateTo, setDateTo] = useState('2026-09-30')
  const [busy, setBusy] = useState(false)
  const load = async () => { const [tasks, ruleList] = await Promise.all([api.backtests(), api.rules()]); setRows(tasks.items); setRules(ruleList); setRuleId(current => current || ruleList.find(rule => rule.enabled)?.id || '') }
  useEffect(() => { load().catch(error => onError(errorText(error))) }, [])
  useEffect(() => { setDetail(undefined); if (id) api.backtest(id).then(setDetail).catch(error => onError(errorText(error))) }, [id])
  const create = async () => { setBusy(true); onError(''); try { const result = await api.createBacktest({ rule_id: ruleId, param_grid: {}, date_from: dateFrom, date_to: dateTo }); await load(); onNotice('回测任务已排队'); window.location.hash = `#/admin/backtests/${result.id}` } catch (error) { onError(errorText(error)) } finally { setBusy(false) } }
  return <>{id && <a className="back-link" href="#/admin/backtests">← 返回回测列表</a>}{id && detail && <section className="panel report-section"><div className="panel-heading"><h2>回测任务 {detail.id}</h2><span className="demo-ribbon">{backtestStatus[detail.status] || detail.status}</span></div><dl className="report-meta"><div><dt>规则 ID</dt><dd>{detail.rule_id}</dd></div><div><dt>日期区间</dt><dd>{detail.date_from} 至 {detail.date_to}</dd></div><div><dt>结果</dt><dd>{detail.result ? JSON.stringify(detail.result) : '等待外部评估'}</dd></div></dl><p className="panel-footnote">数据集导出（GET /admin/backtests/{'{id}'}/dataset）与结果回写由外部评估流程执行。</p></section>}{!id && <><section className="panel report-section"><div className="panel-heading"><h2>创建回测任务</h2></div><div className="management-form"><label className="field"><span>规则</span><select aria-label="回测规则" value={ruleId} onChange={event => setRuleId(event.target.value)}>{rules.filter(rule => rule.enabled).map(rule => <option key={rule.id} value={rule.id}>{rule.name} · {rule.id}</option>)}</select></label><label className="field"><span>开始日期</span><input aria-label="回测开始日期" type="date" value={dateFrom} onChange={event => setDateFrom(event.target.value)}/></label><label className="field"><span>结束日期</span><input aria-label="回测结束日期" type="date" value={dateTo} onChange={event => setDateTo(event.target.value)}/></label><button className="button button-primary" disabled={busy || !ruleId} onClick={create}>创建回测</button></div></section><section className="panel task-table-panel"><div className="table-count">共 <strong>{rows.length}</strong> 个任务</div><div className="table-scroll"><table className="task-table"><thead><tr><th>任务 ID</th><th>规则</th><th>日期区间</th><th>状态</th><th>操作</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td>{row.id}</td><td>{row.rule_id}</td><td>{row.date_from} 至 {row.date_to}</td><td>{backtestStatus[row.status] || row.status}</td><td><a className="table-view" href={`#/admin/backtests/${row.id}`}>查看详情 →</a></td></tr>)}</tbody></table></div></section></>}</>
}

function Profiles({ onError, onNotice }: { onError: (value: string) => void; onNotice: (value: string) => void }) {
  const [rows, setRows] = useState<any[]>([])
  const [deleting, setDeleting] = useState<{ id: string; name: string } | null>(null)
  const [name, setName] = useState('')
  const [noteText, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const load = async () => setRows(await api.watchProfiles())
  useEffect(() => { load().catch(error => onError(errorText(error))) }, [])
  const action = async (work: () => Promise<unknown>, message: string) => { setBusy(true); onError(''); try { await work(); await load(); onNotice(message) } catch (error) { onError(errorText(error)) } finally { setBusy(false) } }
  return <><section className="panel report-section"><div className="panel-heading"><h2>新增关注画像</h2></div><div className="management-form"><label className="field"><span>画像名称 *</span><input aria-label="画像名称" value={name} onChange={event => setName(event.target.value)}/></label><label className="field"><span>备注</span><input aria-label="画像备注" value={noteText} onChange={event => setNote(event.target.value)}/></label><button className="button button-primary" disabled={busy} onClick={() => action(async () => { await api.createWatchProfile({ name, note: noteText }); setName(''); setNote('') }, '画像已创建')}>创建画像</button></div></section><section className="panel task-table-panel"><div className="table-count">共 <strong>{rows.length}</strong> 个画像</div><div className="table-scroll"><table className="task-table"><thead><tr><th>名称</th><th>备注</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td>{row.name}</td><td>{row.note || '—'}</td><td>{row.enabled ? '启用' : '停用'}</td><td>{row.created_at}</td><td><button className="button button-secondary" disabled={busy} onClick={() => setDeleting(row)}>删除</button></td></tr>)}</tbody></table></div></section>{deleting && <ConfirmDialog title="删除关注画像" description={`确认删除“${deleting.name}”？该画像将从关注列表移除。`} confirmLabel="确认删除" busy={busy} onClose={() => setDeleting(null)} onConfirm={async () => { await action(() => api.deleteWatchProfile(deleting.id), '画像已删除'); setDeleting(null) }}/>}</>
}

export function AdminPage({ tab = 'rules', id, isAdmin }: { tab?: string; id?: string; isAdmin: boolean }) {
  const active = tabs.some(item => item.id === tab) ? tab as Tab : 'rules'
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  useEffect(() => { setError(''); setNotice('') }, [active, id])
  return <div className="page"><PageHeading eyebrow="运行管理 / 配置" title="系统管理" description="调整识别规则与调查策略，管理回测及关注画像。规则与画像随 detection-queue 下发给检测服务。"/>{!isAdmin && <EmptyState title="需要管理员权限" text="系统管理仅对 admin 账号开放。"/>}<div className="management-tabs" role="navigation" aria-label="系统管理子页面">{tabs.map(item => <a key={item.id} className={active === item.id ? 'selected' : ''} href={`#/admin/${item.id}`}>{item.name}</a>)}</div>{error && <p className="form-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {isAdmin && active === 'rules' && <Rules onError={setError} onNotice={setNotice}/ >}{isAdmin && active === 'policies' && <Policies onError={setError} onNotice={setNotice}/ >}{isAdmin && active === 'backtests' && <Backtests id={id} onError={setError} onNotice={setNotice}/ >}{isAdmin && active === 'profiles' && <Profiles onError={setError} onNotice={setNotice}/ >}
  </div>
}
