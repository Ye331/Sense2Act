import { useEffect, useState } from 'react'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ErrorBar, PageHeading } from '../components/UI'
import { Icon } from '../components/Icon'
import { api } from '../api/client'
import type { Source } from '../api/client'
import { errorText, formatDate } from '../services/investigations'

type Form = Pick<Source, 'name' | 'type' | 'url' | 'adapter' | 'schedule_cron' | 'enabled'> & { config: string }
const empty: Form = { name: '', type: 'web_page', url: '', adapter: 'ccgp', schedule_cron: '0 0 */2 * * *', enabled: true, config: '{}' }
const typeNames: Record<string, string> = { web_page: '网页', api: '接口', rss: 'RSS' }
const healthNames: Record<string, [string, string]> = { ok: ['正常', 'signal-status-confirmed'], degraded: ['降级', 'signal-status-pending'], down: ['故障', 'status-failed'] }
/** 采集 worker（collector/）已注册的适配器与采集配置示例；配置键说明见 collector/README.md。 */
const adapters: { value: string; label: string; example: string }[] = [
  { value: 'ccgp', label: '中国政府采购网 · 公开招标', example: '{"category": "医疗设备", "keywords": ["医院"], "max_items": 30}' },
  { value: 'tianjin_zfcg', label: '天津市政府采购网', example: '{"region": "天津市", "stages": ["招标采购", "采购意向"]}' },
  { value: 'yifangbao', label: '乙方宝 · 聚合源（仅列表级）', example: '{"channels": ["/zbzt/41/3/"]}' },
  { value: 'toutiao', label: '今日头条 · 新闻检索', example: '{"queries": ["科大讯飞 医疗"]}' },
  { value: 'south_weekend', label: '南方周末 · 新闻检索', example: '{"queries": ["医疗 人工智能"]}' },
  { value: 'cctv_news', label: '央视新闻 · 栏目扫查', example: '{"queries": ["人工智能"]}' },
  { value: 'sogou_wechat', label: '搜狗微信 · 仅摘要', example: '{"queries": ["医疗大模型"]}' },
]
const adapterLabel = (value: string) => adapters.find(item => item.value === value)?.label || value

/** 表格里只摘要采集器认识的几个键，完整配置在编辑表单里看。 */
function configSummary(config: Source['config']) {
  if (!config) return ''
  const parts: string[] = []
  const list = (key: string) => Array.isArray(config[key]) ? (config[key] as unknown[]).map(String).join('、') : ''
  if (config.category) parts.push(`品目 ${String(config.category)}`)
  if (list('keywords')) parts.push(`关键词 ${list('keywords')}`)
  if (list('queries')) parts.push(`检索 ${list('queries')}`)
  if (list('stages')) parts.push(`阶段 ${list('stages')}`)
  return parts.join(' · ')
}

function parseConfig(text: string): Record<string, unknown> {
  let value: unknown
  try { value = JSON.parse(text.trim() || '{}') } catch { throw new Error('采集配置不是合法的 JSON') }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('采集配置必须是 JSON 对象，例如 {"keywords": ["医院"]}')
  return value as Record<string, unknown>
}

export function SourcesPage({ canEdit }: { canEdit: boolean }) {
  const [rows, setRows] = useState<Source[]>([])
  const [form, setForm] = useState<Form>(empty)
  const [editing, setEditing] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<Source | null>(null)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadFailed, setLoadFailed] = useState(false)
  const load = async () => setRows((await api.sources()).items)
  useEffect(() => { load().catch(err => { setLoadFailed(true); setError(errorText(err)) }).finally(() => setLoading(false)) }, [])
  const action = async (work: () => Promise<unknown>, success: string) => {
    setBusy(true); setError(''); setNotice('')
    try { await work(); await load(); setNotice(success) } catch (err) { setError(errorText(err)) } finally { setBusy(false) }
  }
  const save = () => action(async () => {
    const body = { ...form, config: parseConfig(form.config) }
    await (editing ? api.updateSource(editing, body) : api.createSource(body)); setEditing(null); setForm(empty)
  }, editing ? '信息源已更新' : '信息源已新增')
  const field = (key: Exclude<keyof Form, 'enabled'>) => ({ value: form[key], onChange: (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => setForm({ ...form, [key]: event.target.value }) })
  const example = adapters.find(item => item.value === form.adapter)?.example
  return <div className="page sources-workspace"><PageHeading eyebrow="运行管理 / 采集" title="信息源管理" description="采集服务按运行计划领取到期信息源（ingest-queue），抓取入库后回报运行结果，健康度随之更新。"/>
    <ErrorBar message={error}/>{notice && <p role="status">{notice}</p>}
    {canEdit && <section className="panel report-section"><div className="panel-heading"><h2>{editing ? '编辑信息源' : '新增信息源'}</h2></div><div className="management-form">
      <label className="field"><span>名称 *</span><input aria-label="信息源名称" {...field('name')}/></label>
      <label className="field"><span>地址 *</span><input aria-label="信息源地址" type="url" {...field('url')}/></label>
      <label className="field"><span>类型</span><select aria-label="信息源类型" {...field('type')}>{Object.entries(typeNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="field"><span>适配器</span><input aria-label="信息源适配器" list="source-adapters" {...field('adapter')}/><datalist id="source-adapters">{adapters.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</datalist></label>
      <label className="field"><span>运行计划（Spring Cron，6 段）</span><input aria-label="运行计划" {...field('schedule_cron')}/></label>
      <label className="management-check"><input type="checkbox" checked={form.enabled} onChange={event => setForm({ ...form, enabled: event.target.checked })}/>启用</label>
      <label className="field field-wide"><span>采集配置（JSON）{example && <small className="field-hint">示例：{example}</small>}</span><textarea className="code-input" aria-label="采集配置" rows={3} spellCheck={false} {...field('config')}/></label>
      <button className="button button-primary" disabled={busy || !form.name || !form.url} onClick={save}>{editing ? '保存修改' : '新增信息源'}</button>{editing && <button className="button button-secondary" onClick={() => { setEditing(null); setForm(empty) }}>取消编辑</button>}
    </div></section>}
    <section className="panel task-table-panel"><div className="table-count">共 <strong>{rows.length}</strong> 个信息源</div>{loading ? <div className="operations-state" role="status">正在加载信息源…</div> : loadFailed ? null : rows.length ? <div className="table-scroll"><table className="task-table"><thead><tr><th>名称 / 地址</th><th>适配器 / 配置</th><th>计划</th><th>健康度</th><th>最近运行</th>{canEdit && <th>操作</th>}</tr></thead><tbody>{rows.map(row => { const [label, className] = healthNames[row.health || ''] || ['未知', 'signal-status-dismissed']; return <tr key={row.id}><td data-label="名称 / 地址"><strong>{row.name}</strong><small className="management-subline">{row.url}</small></td><td data-label="适配器 / 配置">{adapterLabel(row.adapter)}<small className="management-subline">{typeNames[row.type] || row.type}{configSummary(row.config) && ` · ${configSummary(row.config)}`}</small></td><td data-label="计划">{row.schedule_cron ? <code className="cron-value">{row.schedule_cron}</code> : '仅手动'}</td><td data-label="健康度"><span className={`signal-status ${row.enabled ? className : 'signal-status-dismissed'}`}>{row.enabled ? label : '停用'}</span></td><td data-label="最近运行" className="operations-date">{formatDate(row.last_run_at)}{row.last_run_status && <small className="management-subline">{row.last_run_status === 'failed' ? '失败' : '成功'}</small>}</td>{canEdit && <td data-label="操作"><div className="management-actions"><button className="button button-secondary" onClick={() => { setEditing(row.id); setForm({ name: row.name, type: row.type, url: row.url, adapter: row.adapter || '', schedule_cron: row.schedule_cron || '', enabled: row.enabled, config: JSON.stringify(row.config || {}, null, 2) }); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>编辑</button><button className="button button-secondary" disabled={busy} onClick={() => action(() => api.runSource(row.id), '已置为到期，采集服务下次轮询即领取')}>运行</button><button className="button button-secondary" disabled={busy} onClick={() => setDeleting(row)}>删除</button></div></td>}</tr> })}</tbody></table></div> : <div className="operations-empty"><span className="operations-empty-icon"><Icon name="database" size={25}/></span><strong>暂无信息源</strong><p>当前尚未配置任何信息源，配置完成后会在此显示。</p></div>}</section>
    {deleting && <ConfirmDialog title="删除信息源" description={`确认删除“${deleting.name}”？该信息源将从采集列表移除。`} confirmLabel="确认删除" busy={busy} onClose={() => setDeleting(null)} onConfirm={async () => { await action(() => api.deleteSource(deleting.id), '信息源已删除'); setDeleting(null) }}/>}
  </div>
}
