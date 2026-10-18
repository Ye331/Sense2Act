import { useEffect, useState } from 'react'
import { ErrorBar, PageHeading } from '../components/UI'
import { api } from '../api/client'
import type { EventGraph, EventItem } from '../api/client'
import { errorText } from '../services/investigations'

const eventNames: Record<string, string> = { construction_wave: '集中建设', procurement_cluster: '采购聚集', policy_change: '政策变化', procurement: '采购事件' }

/** 星形布局：事件居中，主体环绕；边来自后端 graph（事件-主体角色边 + 主体间关系边）。 */
function GraphView({ graph }: { graph: EventGraph }) {
  const entities = graph.nodes.filter(node => node.node_type !== 'event')
  const center = { x: 320, y: 200 }
  const positions = new Map(graph.nodes.map(node => [node.id, center]))
  entities.forEach((node, index) => {
    const angle = (2 * Math.PI * index) / Math.max(entities.length, 1) - Math.PI / 2
    positions.set(node.id, { x: center.x + 230 * Math.cos(angle), y: center.y + 150 * Math.sin(angle) })
  })
  return <div className="event-graph"><svg viewBox="0 0 640 400" role="img" aria-label="事件与主体关系图">
    {graph.edges.map((edge, index) => { const from = positions.get(edge.source); const to = positions.get(edge.target); return from && to && <g key={index}><line x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke="#9ab5c2" strokeWidth="1.5"/><text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 6} textAnchor="middle" fill="#648091" fontSize="11">{edge.relation}</text></g> })}
    {graph.nodes.map(node => { const point = positions.get(node.id)!; return <g key={node.id}><title>{node.label}</title><rect x={point.x - 80} y={point.y - 22} width="160" height="44" rx="5" className={`event-node ${node.node_type === 'event' ? 'event-node-event' : ''}`}/><text x={point.x} y={point.y + 4} textAnchor="middle" fill="#245a63" fontSize="12" fontWeight="600">{node.label.length > 12 ? `${node.label.slice(0, 12)}…` : node.label}</text></g> })}
  </svg></div>
}

export function EventsPage({ id }: { id?: string }) {
  const [rows, setRows] = useState<EventItem[]>([])
  const [graph, setGraph] = useState<EventGraph>()
  const [error, setError] = useState('')
  useEffect(() => { let active = true; setGraph(undefined); setError('')
    const work = id ? api.eventGraph(id).then(result => { if (active) setGraph(result) }) : api.events().then(result => { if (active) setRows(result.items) })
    work.catch(err => { if (active) setError(errorText(err)) })
    return () => { active = false } }, [id])
  return <div className="page"><PageHeading eyebrow="资料与关联 / 事件" title="事件图谱" description="Agent 从调查报告中抽取事件、主体与关系，后端校验落库。"/><ErrorBar message={error}/>
    {!id && <section className="panel task-table-panel"><div className="table-count">共 <strong>{rows.length}</strong> 个事件</div><div className="table-scroll"><table className="task-table"><thead><tr><th>事件</th><th>类型</th><th>开始日期</th><th>操作</th></tr></thead><tbody>{rows.map(row => <tr key={row.id}><td><strong>{row.title}</strong><small className="management-subline">{row.id}</small></td><td>{eventNames[row.type] || row.type}</td><td>{row.start_date || '—'}</td><td><a className="table-view" href={`#/events/${row.id}`}>查看图谱 →</a></td></tr>)}</tbody></table></div></section>}
    {id && <><a className="back-link" href="#/events">← 返回事件列表</a>{!graph && !error && <div className="loading-state">正在加载事件…</div>}
      {graph && <section className="panel report-section"><div className="panel-heading"><h2>{graph.event.title}</h2><span className="demo-ribbon">{eventNames[graph.event.type] || graph.event.type}</span></div><div className="management-detail"><p>事件 ID：{graph.event.id} · 开始日期：{graph.event.start_date || '—'}</p><GraphView graph={graph}/><h3>相关报告</h3>{graph.reports.length ? <div className="management-link-list">{graph.reports.map(report => <a key={report.id} className="text-link" href={`#/reports/${report.id}`} onClick={async event => { event.preventDefault(); const detail = await api.report(report.id); window.location.hash = `#/reports/${detail.investigation_id}` }}>{report.title} →</a>)}</div> : <p>暂无关联报告</p>}</div></section>}</>}
  </div>
}
