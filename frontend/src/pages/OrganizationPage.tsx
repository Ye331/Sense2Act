import { useEffect, useState } from 'react'
import { ErrorBar, PageHeading } from '../components/UI'
import { api } from '../api/client'
import type { Organization } from '../api/client'
import { errorText, money } from '../services/investigations'

export function OrganizationPage({ id }: { id?: string }) {
  const [org, setOrg] = useState<Organization>()
  const [error, setError] = useState('')
  useEffect(() => { let active = true; setOrg(undefined); setError(''); if (id) api.organization(id).then(result => { if (active) setOrg(result) }).catch(err => { if (active) setError(errorText(err)) }); return () => { active = false } }, [id])
  return <div className="page"><a className="back-link" href="#/documents">← 返回文档库</a><PageHeading eyebrow="资料与关联 / 机构" title="机构详情" description="机构归一结果、采购基线统计与近期公告。"/><ErrorBar message={error}/>{!org && !error && <div className="loading-state">正在加载机构…</div>}
    {org && <section className="panel report-section"><div className="panel-heading"><h2>{org.name}</h2></div><dl className="report-meta"><div><dt>机构 ID</dt><dd>{org.id}</dd></div><div><dt>地区</dt><dd>{org.region || '—'}</dd></div><div><dt>别名</dt><dd>{org.aliases?.join('、') || '—'}</dd></div></dl>
      <div className="management-detail"><h3>采购基线（检测服务的输入）</h3>{org.stats?.length ? <ul>{org.stats.map(stat => <li key={stat.category}>{stat.category}：样本 {stat.sample_count} 条，均值 {money(stat.amount_mean)}</li>)}</ul> : <p>暂无基线</p>}
        <h3>近期公告</h3><div className="management-link-list">{org.recent_documents?.map(doc => <a className="text-link" key={doc.id} href={`#/documents/${doc.id}`}>{doc.publish_date} · {doc.title} →</a>)}</div></div></section>}</div>
}
