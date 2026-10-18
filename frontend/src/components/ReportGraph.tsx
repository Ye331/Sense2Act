import type { Report } from '../api/client'
import { natureNames } from '../services/investigations'

/** 结论 ↔ 证据二部图：左列结论，右列其引用的证据（报告 claim.evidences 即连线）。 */
export function ReportGraph({ report }: { report: Report }) {
  const claims = report.claims.slice(0, 6)
  const evidence = [...new Map(claims.flatMap(claim => claim.evidences).map(ref => [ref.id, ref])).values()].slice(0, 6)
  if (!claims.length || !evidence.length) return <div className="graph-empty">暂无可展示的结论与证据关联</div>
  const height = Math.max(claims.length, evidence.length) * 74 + 30
  const yFor = (index: number) => 47 + index * 74
  const clip = (value: string, size: number) => value.length > size ? value.slice(0, size) + '…' : value
  return <div className="graph-wrap"><svg viewBox={`0 0 800 ${height}`} role="img" aria-label="证据关系图，左侧为结论，右侧为证据" className="evidence-graph">
    <text x="24" y="20" className="graph-column">调查结论</text><text x="480" y="20" className="graph-column">引用证据</text>
    {claims.flatMap((claim, ci) => claim.evidences.map(ref => {
      const ei = evidence.findIndex(item => item.id === ref.id)
      return ei < 0 ? null : <path key={`${claim.id}-${ref.id}`} d={`M 322 ${yFor(ci)} C 390 ${yFor(ci)}, 415 ${yFor(ei)}, 480 ${yFor(ei)}`} className="graph-line"/>
    }))}
    {claims.map((claim, index) => <g key={claim.id}><title>{claim.text}</title><rect x="24" y={yFor(index) - 24} width="298" height="48" rx="5" className="graph-node claim"/><text x="38" y={yFor(index) + 5} className="graph-node-label">{natureNames[claim.nature]} · {clip(claim.text, 17)}</text></g>)}
    {evidence.map((item, index) => <g key={item.id}><title>{item.title} · {item.id}</title><rect x="480" y={yFor(index) - 24} width="296" height="48" rx="5" className="graph-node evidence"/><text x="494" y={yFor(index) + 5} className="graph-node-label">{clip(item.title, 20)}</text></g>)}
  </svg></div>
}
