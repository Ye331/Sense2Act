// chain_web.v1 数据契约类型(只读投影)
// 对照 agent/sense2act/chain_web_payload.py 出件结构与真实样板
// (agent/runs/chain_demo_v3.web.json、chain_web_xinzhou_r1.json)手写。
// 前端只消费,禁止改写 claim 状态/关系判定/独立根数/结论——机器判决在 Python/台账侧。

export type LaneId = 'source' | 'fact' | 'verification' | 'reasoning' | 'conclusion'
export type EvidenceState = 'supported' | 'contested' | 'unadjudicated' | 'no_direct'

export interface ChainWebLayer {
  id: LaneId
  label: string
  description: string
}

export interface ChainWebNode {
  id: string
  type: LaneId
  label: string
  /** '' | 待证实 | 已证实 | 矛盾;结论节点为 chosen / runner_up */
  status: string
  /** '泳道:三位序号',确定性布局输入 */
  position_hint: string
  /** 折叠只改视图不删材料(家族代表/同源融合) */
  collapsed: boolean
  provenance_ids: string[]
  /** 为空 = 无内容可看(internal 等),不提供抽屉 */
  detail_ref: string
  /** 编号 E1/C1/H0 等 */
  num: string
  lineage_root: string
  /** fact | zone | family | 论点组 | '' 等 */
  sub_type: string
  evidence_state?: EvidenceState
  n_direct_support?: number
  n_counter?: number
  n_unadjudicated?: number
  n_irrelevant_tried?: number
  edge_group_ids?: string[]
  edge_group_id?: string
  member_claim_ids?: string[]
  aggregate_n?: number
  relation_type?: string
  family_members?: string[]
  family_status_dist?: Record<string, number>
  family_evidence_state_dist?: Record<EvidenceState, number>
  status_dist?: Record<string, number>
  evidence_state_dist?: Record<EvidenceState, number>
  inference_text?: string
  basis_claim_ids?: string[]
  rule?: string
  target_hypothesis_ids?: string[]
  premise_status_summary?: Record<string, number>
  condition_or_gap?: string
  fingerprint?: string
  sidecar_provenance?: string
  fingerprint_verified?: boolean
  intentional_orphan?: boolean
}

export interface ChainWebEdge {
  id: string
  source: string
  target: string
  /** 直接证明/印证/佐证/传导/前提/同源共据/反证/时效更新… */
  relation_type: string
  status: string | null
  claim_ids: string[]
  evidence_ids: string[]
  ruling_ids: string[]
  polarity: string | null
  edge_group_id?: string
  aggregate?: boolean
  aggregate_n?: number
  relation_label?: string
  raw_edge_ids?: string[]
}

export interface ChainWebEdgeGroup {
  id: string
  node_id: string
  source: string
  evidence_id: string
  relation_type: string
  group: string
  members: string[]
  raw_member_claim_ids: string[]
  raw_edge_ids: string[]
  n: number
  status_dist: Record<string, number>
  evidence_state_dist: Record<EvidenceState, number>
}

export interface ChainWebEvidence {
  evidence_id: string
  num: string
  title: string
  /** 摘录(≤160 字),全文留台账不进前端 */
  excerpt: string
  source_url: string | null
  source_ref: string
  platform: string | null
  published_at: string | null
  collected_at: string
  lineage_root: string
  /** public | internal | …;internal 件前端只显示定位符 */
  sensitivity: string
  /** 权威登记源 */
  authoritative: boolean
  /** 当事方自证 */
  party: boolean
  /** 同根材料数(十家转载=一根) */
  same_root_count: number
  attachment_ref: string | null
}

export interface ChainWebClaimRulingSummary {
  support: number
  contra: number
  temporal: number
  unruled: number
  n_items: number
  n_roots: number
  n_dup_root: number
}

/** 新件的补证缺口为结构化对象；string 只保留给旧 chain_web.v1。 */
export interface ChainWebGap {
  type?: string
  label?: string
  next?: string
  channel?: string
  next_channel?: string
  tries?: number
  unadjudicated?: number
  channel_rationale?: string
  [key: string]: unknown
}

export interface ChainWebClaim {
  claim_id: string
  draft_id: string
  gist: string
  label: string
  text: string
  status: string
  category: string
  entities: string[]
  /** 人读状态史一句话(如"立项 11-09 → 查实 11-09") */
  history: string
  priority: number
  /** 证据强度锚点人话(如"强(权威单源)") */
  anchor_name: string
  family_rep: string
  family_members: string[]
  gap: ChainWebGap | string | null
  flipback_note: string | null
  reborn_from: string
  in_main: boolean
  in_zone: boolean
  corpus_bound: boolean
  interpretive: string | null
  evidence_morph: string | null
  ruling_summary: ChainWebClaimRulingSummary
  evidence_state: EvidenceState
  n_direct_support: number
  n_counter: number
  n_unadjudicated: number
  n_irrelevant_tried: number
}

export interface ChainWebVerificationItem {
  evidence_id: string
  num: string
  title: string
  /** 支持/矛盾/无关票/时效刷新/挂账未裁/未裁关联 */
  ruling: string
  root: string
  src: string
  dup_root: boolean
  /** legacy = 录制件早于 quote/reason 归档,两态如实回落 */
  legacy: boolean
  masked: boolean
  quote: string | null
  reason: string | null
}

export interface ChainWebVerification {
  claim_id: string
  status: string
  /** 独立根数——口径十家转载=一根,不把转载当独立根 */
  n_independent_roots: number
  n_items: number
  n_support: number
  n_contra: number
  n_temporal: number
  n_dup_root: number
  rep_evidence: string[]
  threshold_note: string
  unruled_nums: string[]
  morph: string | null
  evidence_state: EvidenceState
  n_direct_support: number
  n_counter: number
  n_unadjudicated: number
  n_irrelevant_tried: number
}

export interface ChainWebGroupPolarity {
  group: string
  n_pos: number
  n_rev: number
  n_none: number
}

export interface ChainWebConclusion {
  hypothesis_id: string
  label: string
  /** 资格四态:chosen / runner_up / … */
  state: string
  chosen: boolean
  score: number
  /** 票型:支持/拆台/在查 */
  ticket: { support: number; counter: number; checking: number }
  /** 四问之"部分立住:x/y 条已证实" */
  verdict_line: string
  member_ids: string[]
  n_members: number
  n_verified: number
  runners_up: string[]
  group_polarity: ChainWebGroupPolarity[]
  grouping_source: string
  /** 同源结构化结论视图；旧件可全部缺省。 */
  description?: string
  summary?: string
  supporting_claim_ids?: string[]
  pending_claim_ids?: string[]
  contradicted_claim_ids?: string[]
  alternatives?: Array<{ hypothesis_id: string; label: string; state: string; reason: string }>
  change_conditions?: string[]
  next_steps?: string[]
  source_ref?: string
  source_snapshot_version?: string
}

export interface ChainWebTransition {
  ts: string
  round: number
  claim_id: string
  evidence_id: string
  old_status: string
  new_status: string
  cause: string
}

export interface ChainWebOmission {
  key: string
  n: number
  note: string
}

export interface ChainWebStats {
  lanes: Record<LaneId, number>
  /** 材料数≠独立根数,两者必须并排显式呈现 */
  n_materials: number
  n_independent_roots: number
  n_same_root_collapsed: number
  platform_dist: Record<string, number>
  ruling_dist: Record<string, number>
  status_dist: Record<string, number>
  evidence_state_dist: Record<EvidenceState, number>
}

export interface ChainWebEvent {
  event_id: string
  kind: string
  as_of: string
  as_of_round: number | null
  kb_version: string
}

export interface ChainWebMeta {
  as_of: string
  as_of_round: number | null
  event_id: string
  kb_version: string
  generated_at?: string
  generator_revision?: string
  projection_version?: string
  web: {
    n_nodes: number
    n_edges: number
    n_evidence: number
    n_claims: number
    n_collapsed: number
    n_details: number
    n_raw_edges?: number
    n_edge_groups?: number
    web_validation: string[]
  }
  [key: string]: unknown
}

/** details 键 = detail_ref 全称(如 'claim:evt:C1'),值随类别异构,抽屉按前缀分派 */
export interface ChainWebDetails {
  [ref: string]:
    | { /* evidence:* */
        title: string
        excerpt: string
        platform: string | null
        published_at: string | null
        source_url: string
        collected_at: string
      }
    | { /* claim:* */
        gist: string
        text: string
        category: string
        entities: string[]
        transitions: ChainWebTransition[]
      }
    | { /* verification:* */
        items: ChainWebVerificationItem[]
        threshold_note: string
      }
    | ChainWebConclusion /* hypothesis:* */
    | { /* ruling:* */
        claim_id: string
        evidence_id: string
        num: string
        title: string
        ruling: string
        root: string
        src: string
        dup_root: boolean
        legacy: boolean
        masked: boolean
        quote: string | null
        reason: string | null
      }
    | { /* group:* */
        name: string
        members: string[]
        polarity: ChainWebGroupPolarity
      }
    | Record<string, unknown>
}

export interface ChainWebPayload {
  schema_version: 'chain_web.v1'
  event: ChainWebEvent
  meta: ChainWebMeta
  layers: ChainWebLayer[]
  nodes: ChainWebNode[]
  edges: ChainWebEdge[]
  raw_edges: ChainWebEdge[]
  edge_groups: ChainWebEdgeGroup[]
  evidence: ChainWebEvidence[]
  claims: ChainWebClaim[]
  verification: ChainWebVerification[]
  conclusions: ChainWebConclusion[]
  timeline: ChainWebTransition[]
  stats: ChainWebStats
  omissions: ChainWebOmission[]
  details: ChainWebDetails
}

/** 轻量运行时校验:失败返回问题清单,调用方诚实报错不渲染(方案 §七) */
export function validateChainWeb(data: unknown): { ok: true; payload: ChainWebPayload } | { ok: false; problems: string[] } {
  const problems: string[] = []
  const p = data as Partial<ChainWebPayload>
  if (typeof data !== 'object' || data === null) return { ok: false, problems: ['根不是 JSON 对象'] }
  if (p.schema_version !== 'chain_web.v1') problems.push(`schema_version 不是 chain_web.v1(实为 ${JSON.stringify(p.schema_version)})`)
  for (const key of ['event', 'meta', 'stats', 'details'] as const) if (typeof p[key] !== 'object' || p[key] === null) problems.push(`缺 ${key}`)
  for (const key of ['layers', 'nodes', 'edges', 'evidence', 'claims', 'verification', 'conclusions', 'timeline', 'omissions'] as const) if (!Array.isArray(p[key])) problems.push(`缺 ${key} 数组`)
  if (problems.length) return { ok: false, problems }
  // 旧版 chain_web.v1 没有语义投影增量键：仍允许加载，页面明示
  // 「旧版出件」，不伪造 edge_groups/新鲜度。
  const normalized = {
    ...(data as ChainWebPayload),
    raw_edges: Array.isArray((p as Partial<ChainWebPayload>).raw_edges) ? (p as Partial<ChainWebPayload>).raw_edges! : p.edges!,
    edge_groups: Array.isArray((p as Partial<ChainWebPayload>).edge_groups) ? (p as Partial<ChainWebPayload>).edge_groups! : [],
  } as ChainWebPayload
  const ids = new Set<string>()
  for (const n of normalized.nodes) {
    if (!n.id || !n.type) problems.push(`节点缺 id/type:${JSON.stringify(n).slice(0, 80)}`)
    else ids.add(n.id)
  }
  for (const e of normalized.edges) {
    if (!ids.has(e.source)) problems.push(`边 ${e.id} 的 source 不在节点集:${e.source}`)
    if (!ids.has(e.target)) problems.push(`边 ${e.id} 的 target 不在节点集:${e.target}`)
  }
  if (!normalized.layers.some(l => l.id === 'source') || !normalized.layers.some(l => l.id === 'conclusion')) problems.push('layers 缺 source/conclusion 泳道')
  if (normalized.meta.projection_version && (!normalized.meta.generated_at || !normalized.meta.generator_revision)) problems.push('新投影缺 generated_at/generator_revision')
  const rawIds = new Set(normalized.raw_edges.map(e => e.id))
  for (const g of normalized.edge_groups) {
    if (!ids.has(g.source) || !ids.has(g.node_id)) problems.push(`edge_group ${g.id} 端点不在节点集`)
    if (g.n !== g.raw_edge_ids.length) problems.push(`edge_group ${g.id} 的 n/raw_edge_ids 不一致`)
    if (g.raw_edge_ids.some(id => !rawIds.has(id))) problems.push(`edge_group ${g.id} 原边反查悬空`)
  }
  const isPlainObject = (value: unknown): value is Record<string, unknown> => {
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
    const proto = Object.getPrototypeOf(value)
    return proto === Object.prototype || proto === null
  }
  const gapTextKeys = ['type', 'label', 'next', 'channel', 'next_channel', 'channel_rationale'] as const
  const gapCountKeys = ['tries', 'unadjudicated'] as const
  for (const rawClaim of normalized.claims as unknown[]) {
    if (!isPlainObject(rawClaim)) { problems.push('claims[] 含非对象项'); continue }
    const cid = typeof rawClaim.claim_id === 'string' ? rawClaim.claim_id : '?'
    if (typeof rawClaim.claim_id !== 'string') problems.push(`claim ${cid} 缺 claim_id`)
    const gap = rawClaim.gap
    if (gap !== null && gap !== undefined && typeof gap !== 'string' && !isPlainObject(gap)) {
      problems.push(`claim ${cid} gap 必须为 null/string/plain object`)
      continue
    }
    if (isPlainObject(gap)) {
      for (const key of gapTextKeys) if (key in gap && typeof gap[key] !== 'string') problems.push(`claim ${cid} gap.${key} 必须为 string`)
      for (const key of gapCountKeys) if (key in gap && typeof gap[key] !== 'number') problems.push(`claim ${cid} gap.${key} 必须为 number`)
    }
  }
  if (problems.length) return { ok: false, problems }
  return { ok: true, payload: normalized }
}
