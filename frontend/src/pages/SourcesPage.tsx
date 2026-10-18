import { useEffect, useState } from 'react'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ErrorBar, PageHeading } from '../components/UI'
import { api } from '../api/client'
import type { Source } from '../api/client'
import { errorText, formatDate } from '../services/investigations'

type Form = Pick<Source, 'name' | 'type' | 'url' | 'adapter' | 'schedule_cron' | 'enabled'>
const empty: Form = { name: '', type: 'web_page', url: '', adapter: 'ccgp', schedule_cron: '0 0 6 * * *', enabled: true }
const typeNames: Record<string, string> = { web_page: '网页', api: '接口', rss: 'RSS' }
const healthNames: Record<string, [string, string]> = { ok: ['正常', 'signal-status-confirmed'], degraded: ['降级', 'signal-status-pending'], down: ['故障', 'status-failed'] }

export function SourcesPage({ canEdit }: { canEdit: boolean }) {
  const [rows, setRows] = useState<Source[]>([])
  const [form, setForm] = useState<Form>(empty)
  const [editing, setEditing] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Source | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const load = async () => setRows((await api.sources()).items)
  useEffect(() => { load().catch(err => setError(errorText(err))) }, [])
  const action = async (work: () => Promise<unknown>, success: string) => {
    setBusy(true); setError(''); setNotice('')
    try { await work(); await load(); setNotice(success) } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  const save = () => action(async () => { await (editing ? api.updateSource(editing, form) : api.createSource(form)); setEditing(null); setForm(empty) }, editing ? '信息源已更新' : '信息源已新增')
  const field = (key: keyof Form) => ({ value: String(form[key]), onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [key]: event.target.value }) })
  return <div className="page"><PageHeading eyebrow="运行管理 / 采集" title="信息源管理" description="爬虫服务按计划领取到期信息源（ingest-queue），抓取结果回报后更新健康度。"/>
    <ErrorBar message={error}/>{notice && <p role="status">{notice}</p>}
    {canEdit && <section className="panel report-section"><div className="panel-heading"><h2>{editing ? '编辑信息源' : '新增信息源'}</h2></div><div className="management-form">
      <label className="field"><span>名称 *</span><input aria-label="信息源名称" {...field('name')}/></label>
      <label className="field"><span>地址 *</span><input aria-label="信息源地址" type="url" {...field('url')}/></label>
      <label className="field"><span>类型</span><select aria-label="信息源类型" {...field('type')}>{Object.entries(typeNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="field"><span>适配器</span><input aria-label="信息源适配器" {...field('adapter')}/></label>
      <label className="field"><span>运行计划（Spring Cron，6 段）</span><input aria-label="运行计划" {...field('schedule_cron')}/></label>
      <label className="management-check"><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })}/>启用</label>
      <button className="button button-primary" disabled={busy || !form.name || !form.url} onClick={save}>{editing ? '保存修改' : '新增信息源'}</button>{editing && <button className="button button-secondary" onClick={() => { setEditing(null); setForm(empty) }}>取消编辑</button>}
    </div></section>}
    <section className="panel task-table-panel"><div className="table-count">共 <strong>{rows.length}</strong> 个信息源</div><div className="table-scroll"><table className="task-table"><thead><tr><th>名称 / 地址</th><th>类型 / 适配器</th><th>计划</th><th>健康度</th><th>最近运行</th>{canEdit && <th>操作</th>}</tr></thead><tbody>{rows.map(row => { const [label, className] = healthNames[row.health || ''] || ['未知', 'signal-status-dismissed']; return <tr key={row.id}><td><strong>{row.name}</strong><small className="management-subline">{row.url}</small></td><td>{typeNames[row.type] || row.type} · {row.adapter}</td><td>{row.schedule_cron}</td><td><span className={`signal-status ${row.enabled ? className : 'signal-status-dismissed'}`}>{row.enabled ? label : '停用'}</span></td><td>{formatDate(row.last_run_at)} {row.last_run_status || ''}</td>{canEdit && <td><div className="management-actions"><button className="button button-secondary" onClick={() => { setEditing(row.id); setForm({ name: row.name, type: row.type, url: row.url, adapter: row.adapter, schedule_cron: row.schedule_cron, enabled: row.enabled }); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>编辑</button><button className="button button-secondary" disabled={busy} onClick={() => action(() => api.runSource(row.id), '已置为到期，爬虫下次轮询即领取')}>运行</button><button className="button button-secondary" disabled={busy} onClick={() => setDeleting(row)}>删除</button></div></td>}</tr> })}</tbody></table></div></section>
    {deleting && <ConfirmDialog title="删除信息源" description={`确认删除“${deleting.name}”？该信息源将从采集列表移除。`} confirmLabel="确认删除" busy={busy} onClose={() => setDeleting(null)} onConfirm={async () => { await action(() => api.deleteSource(deleting.id), '信息源已删除'); setDeleting(null) }}/>}
  </div>
}
