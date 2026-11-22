// 五类自定义节点 + 泳道头。统一卡头(类型徽章/编号/状态),视觉规范见 chain.css。
// 只读投影:节点只渲染 Python 侧已判定的状态,禁止前端改写三态/独立根/结论。
import type { NodeProps } from '@xyflow/react'
import { Handle, Position, useUpdateNodeInternals } from '@xyflow/react'
import { memo, useEffect } from 'react'
import type { ChainNodeData, NodePorts } from './chainLayout'

const STATUS_CLASS: Record<string, string> = { '已证实': 'verified', '待证实': 'pending', '矛盾': 'contra', chosen: 'chosen', runner_up: 'runnerup' }
const statusClass = (s: string) => STATUS_CLASS[s] ?? 'neutral'

/** P2a 端口 Handle:视觉隐藏,top=chainLayout 算好的绝对 px(相对卡顶,基准=估算卡高
 *  中点——与 labelLayout/车道分配同一锚点模型,渲染几何 ≡ 布局几何);
 *  rin=同列折返边的目标右侧锚点(回程腿从中缝直接进卡右缘,不穿卡) */
function PortHandles({ nodeId, ports }: { nodeId: string; ports: NodePorts }) {
  const updateNodeInternals = useUpdateNodeInternals()
  const sig = `${ports.out.length}/${ports.in.length}/${ports.rin.length}`
  // 度数随筛选变化时 wrapper 尺寸不变,ResizeObserver 不触发,xyflow handleBounds 不会
  // 自动重测(node_modules 实证 doUpdate=dimensionChanged||!handleBounds||force)——
  // 不强制重测则新 Handle 查不到,边 008 报错消失
  useEffect(() => { updateNodeInternals(nodeId) }, [nodeId, sig, updateNodeInternals])
  return <>
    {ports.in.map((top, i) => (
      <Handle key={`in:${i}`} type="target" id={`in:${i}`} position={Position.Left} className="chain-handle" style={{ top: `${top}px` }} />
    ))}
    {ports.rin.map((top, i) => (
      <Handle key={`rin:${i}`} type="target" id={`rin:${i}`} position={Position.Right} className="chain-handle" style={{ top: `${top}px` }} />
    ))}
    {ports.out.map((top, i) => (
      <Handle key={`out:${i}`} type="source" id={`out:${i}`} position={Position.Right} className="chain-handle" style={{ top: `${top}px` }} />
    ))}
  </>
}

function ChainCard({ chain, ports, selected, dimmed, children }: { chain: ChainNodeData['chain']; ports?: NodePorts; selected?: boolean; dimmed?: boolean; children: React.ReactNode }) {
  const cls = ['chain-card', `lane-${chain.type}`, chain.evidence_state ? `evidence-${chain.evidence_state}` : '', chain.sub_type === 'fact_cluster' ? 'is-fact-cluster' : '', dimmed ? 'is-dimmed' : '', selected ? 'is-selected' : ''].join(' ')
  // 无 ports(泳道头等)回落单锚点=历史形态;有端口时三组全量渲染(空组不渲染)
  const p = ports ?? { out: [0], in: [0], rin: [] }
  return <div className={cls} data-node-id={chain.id} data-evidence-state={chain.evidence_state ?? ''} data-edge-group-id={chain.edge_group_id ?? ''}>
    {/* Handle 是 xyflow 边的连接锚点,视觉隐藏(卷宗风不露圆点);无 Handle 则边无法连接 */}
    <PortHandles nodeId={chain.id} ports={p} />
    <div className="chain-card-head">
      <span className={`chain-lane-badge lane-${chain.type}`}>{laneLabel(chain.type)}</span>
      {chain.num && <span className="chain-num">{chain.num}</span>}
      {chain.status && <span className={`chain-status chain-status-${statusClass(chain.status)}`}>{chain.status === 'chosen' ? '主结论' : chain.status === 'runner_up' ? '次选' : chain.status}</span>}
      {chain.collapsed && <span className="chain-collapse-badge" title="折叠只改视图,不删 JSON 材料">已折叠</span>}
    </div>
    {children}
  </div>
}

function laneLabel(type: string): string {
  return ({ source: '来源', fact: '事实', verification: '验证', reasoning: '推理', conclusion: '结论' } as Record<string, string>)[type] ?? type
}

/** 来源材料:标题+平台/权威/当事方徽章+同源折叠数+谱系根 */
export const SourceNode = memo(function SourceNode({ data, selected }: NodeProps) {
  const { chain, dimmed, ports } = data as ChainNodeData & { dimmed?: boolean }
  const ev = (data as { evidence?: { platform: string | null; authoritative: boolean; party: boolean; same_root_count: number; published_at: string | null; sensitivity: string } }).evidence
  return <ChainCard chain={chain} ports={ports} selected={selected} dimmed={dimmed}>
    <div className="chain-card-title" title={chain.label}>{chain.label}</div>
    {ev && <div className="chain-meta-row">
      {ev.platform && <span className="chip chip-platform">{ev.platform}</span>}
      {ev.authoritative && <span className="chip chip-auth" title="权威登记源(单源例外)">权威</span>}
      {ev.party && <span className="chip chip-party" title="当事方自证">当事方</span>}
      {ev.sensitivity === 'internal' && <span className="chip chip-internal" title="内部材料,仅定位符">internal</span>}
      {ev.same_root_count > 1 && <span className="chip chip-same" title="同根材料数——十家转载=一根">同源 {ev.same_root_count} 件</span>}
    </div>}
    {(chain.lineage_root || ev?.published_at) && <div className="chain-foot">
      {chain.lineage_root && <span title="谱系根:同根转载折算为一根">根:{chain.lineage_root}</span>}
      {ev?.published_at && <span>{ev.published_at}</span>}
    </div>}
  </ChainCard>
})

/** 抽取事实:命题三态如实(待证实/矛盾不伪装);锚点+实体 */
export const FactNode = memo(function FactNode({ data, selected }: NodeProps) {
  const { chain, dimmed, ports } = data as ChainNodeData & { dimmed?: boolean }
  const extra = data as { anchor?: string; entities?: string[] }
  if (chain.sub_type === 'fact_cluster') return <ChainCard chain={chain} ports={ports} selected={selected} dimmed={dimmed}>
    <div className="chain-card-title" title={chain.label}>{chain.label}</div>
    <div className="chain-cluster-count"><b>{chain.aggregate_n ?? 0}</b> 条原始证明边</div>
    <div className="chain-count-row">
      <span className="count count-pos">已证实 <b>{chain.status_dist?.['已证实'] ?? 0}</b></span>
      <span className="count">待证实 <b>{chain.status_dist?.['待证实'] ?? 0}</b></span>
      {(chain.status_dist?.['矛盾'] ?? 0) > 0 && <span className="count count-neg">矛盾 <b>{chain.status_dist?.['矛盾']}</b></span>}
    </div>
    <div className="chain-foot"><span>{chain.relation_type} ×{chain.aggregate_n}</span><span className="cluster-action">点击展开明细</span></div>
  </ChainCard>
  const familyN = chain.family_members?.length ?? 1
  return <ChainCard chain={chain} ports={ports} selected={selected} dimmed={dimmed}>
    <div className="chain-card-title" title={chain.label}>{chain.label}</div>
    <EvidenceBadge state={chain.evidence_state} nCounter={chain.n_counter} nUnadjudicated={chain.n_unadjudicated} />
    {familyN > 1 && <div className="chain-family-row">
      <span className="chip chip-family">同义命题 ×{familyN}</span>
      <span>已证实 {chain.family_status_dist?.['已证实'] ?? 0} / 待证实 {chain.family_status_dist?.['待证实'] ?? 0} / 矛盾 {chain.family_status_dist?.['矛盾'] ?? 0}</span>
    </div>}
    {extra.anchor && <div className="chain-foot"><span title="证据强度锚点">{extra.anchor}</span></div>}
    {extra.entities && extra.entities.length > 0 && <div className="chain-meta-row">{extra.entities.slice(0, 2).map(e => <span key={e} className="chip chip-entity">{e}</span>)}{extra.entities.length > 2 && <span className="chip">+{extra.entities.length - 2}</span>}</div>}
  </ChainCard>
})

/** 交叉验证:独立根数/支持反证计数——只显示判决已承认的量 */
export const VerificationNode = memo(function VerificationNode({ data, selected }: NodeProps) {
  const { chain, dimmed, ports } = data as ChainNodeData & { dimmed?: boolean }
  const v = (data as { verification?: { n_independent_roots: number; n_support: number; n_contra: number; n_temporal: number; n_items: number } }).verification
  return <ChainCard chain={chain} ports={ports} selected={selected} dimmed={dimmed}>
    <div className="chain-card-title">{chain.label}</div>
    {(chain.sub_type === 'gap_hint' || chain.sub_type === 'meta_info') && <div className="chain-meta-row"><span className="chip chip-gap">核验缺口</span><span className="chip">{chain.member_claim_ids?.length ?? 0} 条原始记录</span></div>}
    {v && <div className="chain-count-row">
      <span className="count" title="独立根数(十家转载=一根)"><b>{v.n_independent_roots}</b>根</span>
      <span className="count count-pos" title="支持件数"><b>{v.n_support}</b>证</span>
      <span className={`count ${v.n_contra > 0 ? 'count-neg' : ''}`} title="矛盾件数"><b>{v.n_contra}</b>反</span>
      {v.n_temporal > 0 && <span className="count count-temporal" title="时效刷新"><b>{v.n_temporal}</b>时</span>}
    </div>}
  </ChainCard>
})

/** 推理关系:论点组/派生/融合/桥;LLM 旁车只标建议不冒充裁定 */
export const ReasoningNode = memo(function ReasoningNode({ data, selected }: NodeProps) {
  const { chain, dimmed, ports } = data as ChainNodeData & { dimmed?: boolean }
  return <ChainCard chain={chain} ports={ports} selected={selected} dimmed={dimmed}>
    <div className="chain-card-title">{chain.label}</div>
    {chain.inference_text && <div className="chain-inference-text">{chain.inference_text}</div>}
    {chain.premise_status_summary && <div className="chain-count-row">
      <span className="count count-pos">前提已支持 <b>{chain.premise_status_summary['已证实'] ?? 0}</b></span>
      <span className="count">待补证 <b>{chain.premise_status_summary['待证实'] ?? 0}</b></span>
      {(chain.premise_status_summary['矛盾'] ?? 0) > 0 && <span className="count count-neg">矛盾 <b>{chain.premise_status_summary['矛盾']}</b></span>}
    </div>}
    {chain.condition_or_gap && <div className="chain-condition" title={chain.condition_or_gap}>条件结论:{chain.condition_or_gap}</div>}
    {chain.sub_type && <div className="chain-foot"><span className="chip chip-subtype">{chain.sub_type}</span></div>}
  </ChainCard>
})

/** 结论假设:主/次选+四问结论行+票型 */
export const ConclusionNode = memo(function ConclusionNode({ data, selected }: NodeProps) {
  const { chain, dimmed, ports } = data as ChainNodeData & { dimmed?: boolean }
  const h = (data as { conclusion?: { verdict_line: string; summary?: string; pending_claim_ids?: string[]; ticket: { support: number; counter: number; checking: number } } }).conclusion
  return <ChainCard chain={chain} ports={ports} selected={selected} dimmed={dimmed}>
    <div className="chain-card-title" title={chain.label}>{chain.label}</div>
    {h?.ticket && <>
      <div className="chain-verdict" title={h.summary || h.verdict_line}>{h.summary || h.verdict_line}</div>
      <div className="chain-count-row">
        <span className="count count-pos" title="支持票"><b>{h.ticket.support}</b>支持</span>
        <span className={`count ${h.ticket.counter > 0 ? 'count-neg' : ''}`} title="拆台票"><b>{h.ticket.counter}</b>拆台</span>
        <span className="count" title="在查票"><b>{h.ticket.checking}</b>在查</span>
      </div>
      {(h.pending_claim_ids?.length ?? 0) > 0 && <div className="chain-pending-hint">待补条件 {h.pending_claim_ids!.length} 项 · 点开看详情</div>}
    </>}
  </ChainCard>
})

/** 泳道头:固定五泳道标题与说明(方案 §3.2) */
export const LaneHeaderNode = memo(function LaneHeaderNode({ data }: NodeProps) {
  const { chain, description } = data as ChainNodeData & { description?: string }
  return <div className={`chain-lane-head lane-${chain.type}`}>
    <strong>{chain.label}</strong>
    {description && <small>{description}</small>}
  </div>
})

export const chainNodeTypes = { source: SourceNode, fact: FactNode, verification: VerificationNode, reasoning: ReasoningNode, conclusion: ConclusionNode, laneHeader: LaneHeaderNode }

function EvidenceBadge({ state, nCounter = 0, nUnadjudicated = 0 }: { state?: string; nCounter?: number; nUnadjudicated?: number }) {
  if (!state || state === 'supported') return null
  const label = state === 'contested' ? `有反证${nCounter ? ` ×${nCounter}` : ''}` : state === 'unadjudicated' ? `材料待裁定${nUnadjudicated ? ` ×${nUnadjudicated}` : ''}` : '无直接证据/待补证'
  return <div className="chain-evidence-row"><span className={`chip chip-evidence-${state}`}>{label}</span></div>
}
