// 五泳道确定性布局:position_hint('泳道:三位序号')→ xyflow 坐标。
// 同输入同输出;折叠节点照常占位(视觉淡化),不用力导向随机布局。
import type { Edge, Node } from '@xyflow/react'
import type { ChainWebPayload, ChainWebNode, ChainWebEdge, LaneId } from '../../types/chainWeb'
import { labelPlacement } from './labelLayout'

export const LANES: LaneId[] = ['source', 'fact', 'verification', 'reasoning', 'conclusion']

const HEAD_H = 64
// 各泳道行距(节点卡高不一,按泳道取保守值防重叠)
const ROW_H: Record<LaneId, number> = { source: 170, fact: 224, verification: 156, reasoning: 292, conclusion: 210 }

// ── P2b 动态车道与列间距(施工单 20261110 §三.P2b)────────────
// 卡宽(chain.css .chain-card width=256)与节点左缘相对列起点偏移(单一来源)
export const CARD_W = 256
const NODE_PAD_X = 22
const CARD_RIGHT = NODE_PAD_X + CARD_W   // 278 = 卡右缘相对列起点
const MIN_GAP = 300                      // 最小列间距(现状 300 → 最小中缝 44)
const LANE_STEP = 12                     // 车道间距(相邻车道竖缝 x 距)
const LANE_PAD = 6                       // 通道起点到首车道偏移;n 车道占用宽 = 12n+12
// 各泳道卡高估算(实测 avg/max 之间,偏保守覆盖真实卡高;锚点 y 与标签碰撞矩形同值)
export const CARD_H: Record<LaneId, number> = { source: 96, fact: 196, verification: 104, reasoning: 220, conclusion: 176 }
/** 卡高估算向下单侧扩(卡顶由行位固定、内容向下生长,误差单侧)——行间空隙扫描与
 *  标签避让卡矩形同一估算单一来源(原 labelLayout 局部常量, P3 提为共享) */
export const CARD_H_SLACK = 24
/** P3-A 行间跳越 y 最小间距(同列两条跳越水平腿互距下限,与车道 LANE_STEP 同量级) */
export const JUMP_MIN_GAP = 12
// ── P2a 节点端口分配(施工单 20261110 §三.P2a;拍板=溢出扇出)────────────
// 同节点出/入边按(对端行位 y, edgeId)确定性排序分端口。间距 = max(PORT_MIN_GAP,
// 卡高估算/(n+1)):低度数在卡内自然展开,高度数(实测忻州 E2 出度 15 / h2 入度 9,
// 卡内均分仅 6~7px)均匀扇出、溢出到行距空白(Handle 不可见,行距 132~210px 充足)。
// 下限取 13px 留 DOM 实测浮点余量,保「相邻锚点 ≥12px」恒真。
export const PORT_MIN_GAP = 13
// 结论入边的 hover 标签都落在末腿上;标签实高约 16px,取 18px 使相邻标签即使
// 同 x 也不相碰。只作用于结论泳道,不放大事实/验证高扇出的端口占用。
const CONCLUSION_PORT_MIN_GAP = 18

/** 端口 Handle top 表(px,相对卡顶,基准=估算卡高中点):n=1 → [CARD_H/2]。
 *  锚点故意用估算中点而非真实中点:真实卡高内容决定、JS 不可知,若锚真实中点,
 *  真实折线与避让引擎的估算折线差 ≤11px(忻州 source est 96/实 118),标签避让
 *  判定会「估算 clear/实撞」;锚估算中点后渲染几何 ≡ 布局模型几何,判定精确。 */
function portTops(n: number, lane: LaneId): number[] {
  const minGap = lane === 'conclusion' ? CONCLUSION_PORT_MIN_GAP : PORT_MIN_GAP
  const gap = Math.max(minGap, CARD_H[lane] / (n + 1))
  return Array.from({ length: n }, (_, i) => CARD_H[lane] / 2 + (i - (n - 1) / 2) * gap)
}

/** 节点三组端口 Handle top(px,相对卡顶,ChainCard 据此渲染多 Handle) */
export interface NodePorts { out: number[]; in: number[]; rin: number[] }

/** 自定义节点携带的数据(xyflow 要求 Record<string, unknown>) */
export interface ChainNodeData extends Record<string, unknown> {
  chain: ChainWebNode
  /** P2a 端口偏移(泳道头等无 Handle 节点不携带) */
  ports?: NodePorts
}

export interface ChainViewOptions {
  hiddenLanes: Set<LaneId>
  /** '' = 不筛状态 */
  statusFilter: string
  /** 搜索 gist/label/title/num,空串=不搜 */
  query: string
  /** true = 折叠节点也隐藏 */
  hideCollapsed: boolean
  /** true = 显示「同源共据」边(转载折算注记,默认隐藏=降噪大头) */
  showSameRoot: boolean
  /** 只保留 contested/unadjudicated/no_direct 事实卡 */
  onlyNeedsEvidence: boolean
  /** 已点开的高扇出汇流组 id */
  expandedEdgeGroups: Set<string>
}

/** 强语义边:标签常显(直接证明/反证/时效更新=会改变结论的信号) */
const STRONG_RELATIONS = new Set(['直接证明', '反证', '时效更新'])

/** 同源扇区/同目标汇入组的最浅档:最多向白混 45%,保留关系语义色系。 */
const MAX_MIX = 45

/** 筛选只改视图不删 JSON 材料(方案 §3.4) */
function passesFilter(n: ChainWebNode, o: ChainViewOptions, haystack: string, forceExpanded = false): boolean {
  if (o.hiddenLanes.has(n.type)) return false
  if (o.hideCollapsed && n.collapsed && !forceExpanded) return false
  if (o.onlyNeedsEvidence && n.type === 'fact') {
    if (n.sub_type === 'fact_cluster') {
      const dist = n.evidence_state_dist
      if (!dist || (dist.contested + dist.unadjudicated + dist.no_direct) === 0) return false
    } else if (n.evidence_state === 'supported') return false
  }
  if (o.statusFilter && n.status !== o.statusFilter) return false
  if (o.query && !haystack.includes(o.query.toLowerCase())) return false
  return true
}

export interface ChainGraph {
  nodes: Node<ChainNodeData>[]
  edges: Edge[]
  /** id→节点,供路径高亮反查 */
  byId: Map<string, ChainWebNode>
  /** 因泳道隐藏而被裁的跨泳道边数(显式注记用,不静默) */
  clippedByLane: number
  /** 因「同源边」开关关闭而被裁的同源边数 */
  clippedSameRoot: number
}

export function buildChainGraph(payload: ChainWebPayload, opts: ChainViewOptions): ChainGraph {
  const byId = new Map(payload.nodes.map(n => [n.id, n]))
  const expandedMembers = new Set<string>()
  const collapsedMembers = new Set<string>()
  for (const group of payload.edge_groups) {
    const dst = opts.expandedEdgeGroups.has(group.id) ? expandedMembers : collapsedMembers
    for (const cid of group.raw_member_claim_ids) dst.add(cid)
  }
  // 同一 claim 如同时落入多组，显式展开优先，避免「点开了仍不出现」。
  for (const cid of expandedMembers) collapsedMembers.delete(cid)
  const projectedNodes = payload.nodes.filter(n => {
    const gid = n.edge_group_id
    if (n.sub_type === 'fact_cluster' && gid && opts.expandedEdgeGroups.has(gid)) return false
    const fullCid = n.id.startsWith('cl:') || n.id.startsWith('vf:') ? n.id.slice(3) : ''
    return !collapsedMembers.has(fullCid)
  })
  const projectedEdges = payload.edges.filter(e => {
    if (!e.edge_group_id) return true
    const expanded = opts.expandedEdgeGroups.has(e.edge_group_id)
    return e.aggregate ? !expanded : expanded
  })
  // 搜索haystack:节点自身 + 关联 claim/evidence 文本,搜得到才显示
  const claimText = new Map<string, string>()
  for (const c of payload.claims) claimText.set(`cl:${c.claim_id}`, `${c.gist} ${c.label} ${c.text} ${c.draft_id} ${c.entities.join(' ')}`.toLowerCase())
  const evText = new Map<string, string>()
  for (const e of payload.evidence) evText.set(`ev:${e.evidence_id}`, `${e.title} ${e.excerpt} ${e.num} ${e.platform ?? ''} ${e.lineage_root}`.toLowerCase())

  const visible = projectedNodes.filter(n => {
    const fullCid = n.id.startsWith('cl:') || n.id.startsWith('vf:') ? n.id.slice(3) : ''
    if (!passesFilter(n, opts, `${n.label} ${n.num}`.toLowerCase(), expandedMembers.has(fullCid))) return false
    const hay = n.type === 'fact' || n.type === 'conclusion' ? `${n.label} ${n.num} ${claimText.get(n.id) ?? ''}` : n.type === 'source' ? `${n.label} ${n.num} ${evText.get(n.id) ?? ''}` : `${n.label} ${n.num}`
    return !opts.query || hay.toLowerCase().includes(opts.query.toLowerCase())
  })
  const visibleIds = new Set(visible.map(n => n.id))

  // 泳道内行位拓扑对齐(barycenter 启发式,2026-11-10 交叉治理):
  // 初始序=position_hint 入账序;两轮前后向迭代,每节点按可见邻接对端位置均值重排。
  // 排序键 (bary, hint) 全确定,同输入同输出;无边节点排尾保原序(不动悬空件)。
  const laneOrder: Record<string, string[]> = {}
  const nodePos = new Map<string, number>()
  const nodeHint = new Map<string, number>()
  for (const lane of LANES) {
    const arr = visible.filter(n => n.type === lane).sort((a, b) => a.position_hint.localeCompare(b.position_hint))
    laneOrder[lane] = arr.map(n => n.id)
    arr.forEach((n, i) => { nodePos.set(n.id, i); nodeHint.set(n.id, Number(n.position_hint.split(':')[1]) || 0) })
  }
  const adj = new Map<string, string[]>()
  for (const e of projectedEdges) {
    if (e.relation_type === '同源共据') continue   // 折算注记线,不参与拓扑排序
    if (!visibleIds.has(e.source) || !visibleIds.has(e.target)) continue
    const s = adj.get(e.source) ?? []
    s.push(e.target)
    adj.set(e.source, s)
    const t = adj.get(e.target) ?? []
    t.push(e.source)
    adj.set(e.target, t)
  }
  const bary = (id: string): number => {
    const ps = (adj.get(id) ?? []).map(p => nodePos.get(p)).filter((v): v is number => v !== undefined)
    return ps.length ? ps.reduce((a, b) => a + b, 0) / ps.length : Number.POSITIVE_INFINITY
  }
  for (let round = 0; round < 2; round++) {
    for (const lane of round % 2 === 0 ? LANES : [...LANES].reverse()) {
      const arr = laneOrder[lane]
      arr.sort((a, b) => {
        const [ba, bb] = [bary(a), bary(b)]
        if (ba !== bb) return ba - bb
        return (nodeHint.get(a) ?? 0) - (nodeHint.get(b) ?? 0)
      })
      arr.forEach((id, i) => nodePos.set(id, i))
    }
  }

  // 节点占位(坐标在分道 + 列前缀和之后统一回填)
  const evidenceById = new Map(payload.evidence.map(e => [`ev:${e.evidence_id}`, e]))
  const claimById = new Map(payload.claims.map(c => [`cl:${c.claim_id}`, c]))
  const verificationById = new Map(payload.verification.map(v => [`vf:${v.claim_id}`, v]))
  const conclusionById = new Map(payload.conclusions.map(h => [`hy:${h.hypothesis_id}`, h]))
  const nodes: Node<ChainNodeData>[] = visible.map(n => ({
    id: n.id,
    type: n.type,
    position: { x: 0, y: 0 }, // 占位,下方统一回填
    data: {
      chain: n,
      evidence: evidenceById.get(n.id),
      claim: claimById.get(n.id),
      verification: verificationById.get(n.id),
      conclusion: conclusionById.get(n.id),
      anchor: claimById.get(n.id)?.anchor_name,
      entities: claimById.get(n.id)?.entities,
    },
    draggable: false,
  }))
  const nodeById = new Map(nodes.map(n => [n.id, n]))

  // 裁边计数(显式注记,不静默——方案 §五 "永远显示省略数量"):
  // 先按同源开关裁,再按两端可见裁;两段各记各的数
  let clippedSameRoot = 0
  const afterSameRoot = opts.showSameRoot ? projectedEdges : projectedEdges.filter(e => {
    if (e.relation_type === '同源共据') { clippedSameRoot++; return false }
    return true
  })
  const visibleFinal = afterSameRoot.filter(e => visibleIds.has(e.source) && visibleIds.has(e.target))
  const clippedByLane = afterSameRoot.length - visibleFinal.length

  // P2a 端口分配(施工单 20261110 §三.P2a;拍板=溢出扇出):在可见边集合上统计度数
  // (筛选裁边后度数随视图变化,端口重排仍确定性)。同列入边(sLane===tLane:前提折返/
  // 同源共据)走目标卡右侧 Handle(rin 组)——回程水平腿从中缝直接进卡右缘,不再横穿卡体
  // (P0 诊断 ③ 非长线穿卡 2/6/5 的根治);其余入边走左侧。组内排序键 (对端锚点 y,
  // edgeId) 全确定;xyflow 按 Handle 实位算锚点,不用虚拟端口。
  // 位置:先于 P2b 车道分配——车道 y 区间须用端口锚点(见下)。
  const rowTopY = (nodeId: string): number | null => {
    const lane = byId.get(nodeId)?.type
    const pos = nodePos.get(nodeId)
    if (!lane || pos === undefined) return null
    return HEAD_H + 28 + pos * ROW_H[lane as LaneId]
  }
  // 锚点 y = 卡中点(行顶 + 卡高/2);用左上角会让跨列边(卡高不同)同行的 y 区间退化单点
  const nodeAnchorY = (nodeId: string): number | null => {
    const lane = byId.get(nodeId)?.type
    return lane ? (rowTopY(nodeId) ?? 0) + CARD_H[lane as LaneId] / 2 : null
  }
  const outBy = new Map<string, ChainWebEdge[]>()
  const inBy = new Map<string, ChainWebEdge[]>()
  const foldBy = new Map<string, ChainWebEdge[]>()
  for (const e of visibleFinal) {
    const out = outBy.get(e.source) ?? []
    out.push(e); outBy.set(e.source, out)
    const sType = byId.get(e.source)?.type
    const tType = byId.get(e.target)?.type
    const group = sType && sType === tType ? foldBy : inBy
    const ins = group.get(e.target) ?? []
    ins.push(e); group.set(e.target, ins)
  }
  const srcHandle = new Map<string, string>()
  const tgtHandle = new Map<string, string>()
  const srcOff = new Map<string, number>()
  const tgtOff = new Map<string, { off: number; side: 'left' | 'right' }>()
  const nodePorts = new Map<string, NodePorts>()
  // 交叉区别化:仅对跨列且度数 ≥3 的同源扇区/同目标汇入组做向白明度步进。
  // 组内序复用端口排序键(对端锚点 y,edgeId);一条边同时落入两组时取较大的混白比,
  // 两组的区别化都不被另一组覆盖,同输入仍全确定。
  const mixPctById = new Map<string, number>()
  const registerMixSteps = (group: ChainWebEdge[]) => {
    if (group.length < 3) return
    group.forEach((e, i) => {
      const pct = Number((MAX_MIX * i / (group.length - 1)).toFixed(2))
      mixPctById.set(e.id, Math.max(mixPctById.get(e.id) ?? 0, pct))
    })
  }
  for (const n of nodes) {
    const lane = byId.get(n.id)?.type as LaneId | undefined
    if (!lane) continue
    const byPeer = (a: ChainWebEdge, b: ChainWebEdge, aPeer: string, bPeer: string): number =>
      (nodeAnchorY(aPeer) ?? 0) - (nodeAnchorY(bPeer) ?? 0) || a.id.localeCompare(b.id)
    const outs = [...(outBy.get(n.id) ?? [])].sort((a, b) => byPeer(a, b, a.target, b.target))
    const ins = [...(inBy.get(n.id) ?? [])].sort((a, b) => byPeer(a, b, a.source, b.source))
    const rins = [...(foldBy.get(n.id) ?? [])].sort((a, b) => byPeer(a, b, a.source, b.source))
    registerMixSteps(outs.filter(e => byId.get(e.source)?.type !== byId.get(e.target)?.type))
    registerMixSteps(ins)
    const outOff = portTops(outs.length, lane)
    const inOff = portTops(ins.length, lane)
    const rinOff = portTops(rins.length, lane)
    nodePorts.set(n.id, { out: outOff, in: inOff, rin: rinOff })
    outs.forEach((e, i) => { srcHandle.set(e.id, `out:${i}`); srcOff.set(e.id, outOff[i]) })
    ins.forEach((e, i) => { tgtHandle.set(e.id, `in:${i}`); tgtOff.set(e.id, { off: inOff[i], side: 'left' }) })
    rins.forEach((e, i) => { tgtHandle.set(e.id, `rin:${i}`); tgtOff.set(e.id, { off: rinOff[i], side: 'right' }) })
  }
  // 节点回填端口(泳道头等无 Handle 节点不带 ports,ChainCard 回落单锚点)
  for (const n of nodes) {
    const ports = nodePorts.get(n.id)
    if (ports) n.data = { ...n.data, ports }
  }

  // ── P3-A 长线零压卡路由(施工单 20261110 §三.A;B2 已退役)────────────────
  // 几何总纲(渲染 ≡ 模型:ChainRelEdge 直吃 route 点序列,xTurn/laneIdx 退役):
  //   span0/1(同列折返/相邻列):[sx,sy]→[缝,sy]→[缝,ty]→[tx,ty],竖缝在通道 sLane
  //   (列 sLane 右侧通道,与长线各通道缝同池分道);
  //   span≥2(长线):每个中间通道一条竖缝,相邻缝间在行间空隙 y 跳越(跳越水平腿只
  //   穿越一列的行间空隙,不碰任何卡)→ 边×卡=0 是构造保证;无空隙兜底取最小重叠
  //   y 并注入 gapFallback 显式注记(省略显式呈现纪律),进 [diag] 监督读数。
  //   全图统一直角折线;span1 扇区与长线同样进入物理通道车道池,让通道容量自然撑开。
  interface EdgeEndpoints { sY: number; tY: number; sLane: number; tLane: number; tSide: 'left' | 'right' }
  const eps = new Map<string, EdgeEndpoints>()
  const laneIndexOf = (id: string): number => {
    const t = byId.get(id)?.type
    return t ? LANES.indexOf(t) : -1
  }
  for (const e of visibleFinal) {
    const sLane = laneIndexOf(e.source)
    const tLane = laneIndexOf(e.target)
    if (sLane < 0 || tLane < sLane) continue
    const sY = rowTopY(e.source)
    const tY = rowTopY(e.target)
    if (sY == null || tY == null) continue
    eps.set(e.id, { sY: sY + (srcOff.get(e.id) ?? 0), tY: tY + (tgtOff.get(e.id)?.off ?? 0), sLane, tLane, tSide: tgtOff.get(e.id)?.side ?? 'left' })
  }

  // P3-A.2 行间空隙 y 分配:对每条长线的每个被跳越列,扫描该列卡片占用带(行位+估算
  // 卡高+SLACK,与 labelLayout 同一估算单一来源)求空隙带;跳越 y 在空隙带内扫描线分配
  // (同列所有跳越腿全局互距 ≥JUMP_MIN_GAP,两条跳越腿同 y 会共线重叠),确定性序=
  // (区间 lo, edgeId)。
  const jumpY = new Map<string, number[]>()   // edgeId → 各被跳越列的跳越 y(按列升序)
  const fallbackIds = new Set<string>()
  {
    const reqsByCol = new Map<number, Array<{ id: string; sLane: number; col: number; lo: number; hi: number }>>()
    for (const [id, ep] of eps) {
      if (ep.tLane - ep.sLane < 2) continue
      const lo = Math.min(ep.sY, ep.tY)
      const hi = Math.max(ep.sY, ep.tY)
      for (let col = ep.sLane + 1; col < ep.tLane; col++) {
        const list = reqsByCol.get(col) ?? []
        list.push({ id, sLane: ep.sLane, col, lo, hi })
        reqsByCol.set(col, list)
      }
    }
    for (const [col, reqs] of [...reqsByCol].sort((a, b) => a[0] - b[0])) {
      const laneId = LANES[col]
      const bands = laneOrder[laneId].map(id => {
        const top = rowTopY(id) ?? 0
        return [top, top + CARD_H[laneId] + CARD_H_SLACK]
      })
      // 占用带间隙(含首尾开区间;行距 > 卡高+SLACK,带两两不交有序)
      const gaps: Array<[number, number]> = []
      for (let i = 0; i <= bands.length; i++)
        gaps.push([i === 0 ? Number.NEGATIVE_INFINITY : bands[i - 1][1], i === bands.length ? Number.POSITIVE_INFINITY : bands[i][0]])
      const takenByGap = new Map<number, number[]>()
      // 在空隙带 gi 内找 ≥JUMP_MIN_GAP 净距的空位:先从 lo 向上扫,不行再从 hi 向下扫
      const findFreeY = (gi: number, lo: number, hi: number): number | null => {
        const taken = [...(takenByGap.get(gi) ?? [])].sort((a, b) => a - b)
        let y = lo
        let done = false
        for (const t of taken) {
          if (y + JUMP_MIN_GAP <= t) { done = true; break }
          y = t + JUMP_MIN_GAP
        }
        if ((done || taken.length === 0) && y <= hi) return y
        let y2 = hi
        for (let i = taken.length - 1; i >= 0; i--) {
          if (y2 - JUMP_MIN_GAP >= taken[i]) return y2
          y2 = taken[i] - JUMP_MIN_GAP
        }
        return y2 >= lo ? y2 : null
      }
      for (const r of [...reqs].sort((a, b) => a.lo - b.lo || a.id.localeCompare(b.id))) {
        let placedY = NaN
        // ① 与 [lo,hi] 相交的空隙带内找位(保路由单调)
        for (let gi = 0; gi < gaps.length; gi++) {
          const lo = Math.max(gaps[gi][0], r.lo)
          const hi = Math.min(gaps[gi][1], r.hi)
          if (hi < lo) continue
          const y = findFreeY(gi, lo, hi)
          if (y != null) {
            placedY = y
            takenByGap.set(gi, [...(takenByGap.get(gi) ?? []), y])
            break
          }
        }
        // ② 全列空隙带找距 [lo,hi] 最近空位(V 形绕行:竖缝越出端点行,但不擦卡、
        //    不算兜底——[sy,ty] 区间整体落在某行卡带内时这是唯一零压卡解)
        if (placedY !== placedY) {
          let bestDist = Infinity
          let bestGi = -1
          for (let gi = 0; gi < gaps.length; gi++) {
            const y = findFreeY(gi, gaps[gi][0], gaps[gi][1])
            if (y == null) continue
            const dist = Math.max(0, r.lo - y, y - r.hi)
            if (dist < bestDist) { bestDist = dist; placedY = y; bestGi = gi }
          }
          if (bestGi >= 0) takenByGap.set(bestGi, [...(takenByGap.get(bestGi) ?? []), placedY])
        }
        if (placedY !== placedY) {
          // ③ 无空隙兜底(施工单 P3-A.2):候选 y(区间端+占用带缘,clamp 进 [lo,hi])
          //    取压带深度最小,平局取小 y;gapFallback 显式注记,不静默。
          let bestPen = Infinity
          const seen = new Set<string>()
          const cands = [r.lo, r.hi, ...bands.flat()].map(v => Math.min(Math.max(v, r.lo), r.hi)).sort((a, b) => a - b)
          for (const c of cands) {
            const key = c.toFixed(3)
            if (seen.has(key)) continue
            seen.add(key)
            let pen = 0
            for (const b of bands) if (c >= b[0] && c <= b[1]) pen = Math.max(pen, Math.min(c - b[0], b[1] - c))
            if (pen < bestPen) { bestPen = pen; placedY = c }
          }
          fallbackIds.add(r.id)
        }
        const arr = jumpY.get(r.id) ?? []
        arr[r.col - r.sLane - 1] = placedY
        jumpY.set(r.id, arr)
      }
    }
  }

  // P3-A.1 通道竖缝车道组:分道单元=物理通道 ch(列 ch 与列 ch+1 之间)——span0/1 边
  // 竖缝与长线各中间通道竖缝同池扫描线贪心。若按 (sLane,tLane,chIdx) 分组各自从车道 0
  // 起排,同物理通道不同组的车道 0 会共线(④ 与断言③ 双杀),故必须同池。
  const seamsByCh = new Map<number, Array<{ id: string; y0: number; y1: number }>>()
  const addSeam = (id: string, ch: number, a: number, b: number) => {
    const list = seamsByCh.get(ch) ?? []
    list.push({ id, y0: Math.min(a, b), y1: Math.max(a, b) })
    seamsByCh.set(ch, list)
  }
  for (const [id, ep] of eps) {
    const span = ep.tLane - ep.sLane
    if (span <= 1) addSeam(id, ep.sLane, ep.sY, ep.tY)
    else {
      const jy = jumpY.get(id) ?? []
      for (let ch = ep.sLane; ch < ep.tLane; ch++) {
        const yStart = ch === ep.sLane ? ep.sY : jy[ch - ep.sLane - 1]
        const yEnd = ch === ep.tLane - 1 ? ep.tY : jy[ch - ep.sLane]
        addSeam(id, ch, yStart, yEnd)
      }
    }
  }

  const scanlineAssign = (lists: Map<number, Array<{ id: string; y0: number; y1: number }>>) => {
    const idx = new Map<string, number>()
    const counts: number[] = LANES.map(() => 0)
    for (const [ch, list] of [...lists].sort((a, b) => a[0] - b[0])) {
      const sorted = [...list].sort((a, b) => a.y0 - b.y0 || a.id.localeCompare(b.id))
      const ends: number[] = []
      for (const s of sorted) {
        let lane = ends.findIndex(end => end <= s.y0)
        if (lane < 0) { lane = ends.length; ends.push(0) }
        ends[lane] = Math.max(ends[lane], s.y1)   // 必须 max 更新(直赋会让短边缩短占用记录致误复用)
        idx.set(`${s.id}@${ch}`, lane)
      }
      counts[ch] = ends.length
    }
    return { idx, counts }
  }
  const colXFrom = (counts: number[]): number[] => {
    const xs = [0]
    // 末端标签固定在箭头前 70px 带内。末腿留 78px,既容纳最宽关系词的完整矩形,
    // 也给同目标高入度标签提供多个水平错位候选。
    for (let k = 0; k < LANES.length - 1; k++)
      xs.push(xs[k] + Math.max(MIN_GAP, CARD_W + LANE_STEP * counts[k] + LANE_PAD * 12))
    return xs
  }

  const final = scanlineAssign(seamsByCh)
  const laneIdx = final.idx
  const laneCounts = final.counts
  const colX = colXFrom(laneCounts)

  // 回填节点坐标 + 泳道头(列 x 前缀和确定后)
  for (const lane of LANES) {
    const col = LANES.indexOf(lane)
    laneOrder[lane].forEach((id, i) => {
      const n = nodeById.get(id)
      if (n) n.position = { x: colX[col] + NODE_PAD_X, y: HEAD_H + 28 + i * ROW_H[lane] }
    })
  }
  const laneHeads: Node<ChainNodeData>[] = payload.layers
    .filter(l => !opts.hiddenLanes.has(l.id))
    .map(l => ({
      id: `head:${l.id}`,
      type: 'laneHeader' as const,
      position: { x: colX[LANES.indexOf(l.id)] + NODE_PAD_X, y: 0 },
      data: { chain: { id: `head:${l.id}`, type: l.id, label: l.label, status: '', position_hint: '', collapsed: false, provenance_ids: [], detail_ref: '', num: '', lineage_root: '', sub_type: '' }, description: l.description },
      draggable: false,
      selectable: false,
      deletable: false,
    }))

  // P3 route 点序列注入:xTurn(单竖缝 x)退役,每边算好绝对点序列(chainLayout 是
  // 唯一几何源,ChainRelEdge/labelLayout 直吃)。
  const seamXOf = (id: string, ch: number): number =>
    colX[ch] + CARD_RIGHT + LANE_PAD + (laneIdx.get(`${id}@${ch}`) ?? 0) * LANE_STEP
  const routeById = new Map<string, Pt[]>()
  for (const e of visibleFinal) {
    const ep = eps.get(e.id)
    const sNode = nodeById.get(e.source)
    const tNode = nodeById.get(e.target)
    if (!ep || !sNode || !tNode) continue
    const sx = sNode.position.x + CARD_W
    const tx = ep.tSide === 'right' ? tNode.position.x + CARD_W : tNode.position.x
    if (ep.sY === ep.tY) { routeById.set(e.id, [[sx, ep.sY], [tx, ep.tY]]); continue }
    const pts: Pt[] = [[sx, ep.sY]]
    let curY = ep.sY
    const jy = jumpY.get(e.id) ?? []
    // span0(同列折返)也要过一次通道缝:循环上界取 max(tLane-1, sLane)
    const lastCh = Math.max(ep.tLane - 1, ep.sLane)
    for (let ch = ep.sLane; ch <= lastCh; ch++) {
      const x = seamXOf(e.id, ch)
      pts.push([x, curY])
      curY = ch >= ep.tLane - 1 ? ep.tY : (jy[ch - ep.sLane] ?? curY)
      pts.push([x, curY])
    }
    pts.push([tx, ep.tY])
    routeById.set(e.id, pts)
  }

  // P1 标签避让:所有边(强语义+弱语义)预计算沿线位置,强语义优先放置(反证>直接证明>
  // 时效更新>弱语义)。P3 起避让几何=route 点序列(渲染几何 ≡ 避让几何,同一来源)。
  const nodeGeom = new Map<string, { x: number; y: number; lane: LaneId }>()
  for (const n of nodes) {
    const lane = byId.get(n.id)?.type
    if (lane) nodeGeom.set(n.id, { x: n.position.x, y: n.position.y, lane: lane as LaneId })
  }
  const labelPl = labelPlacement(visibleFinal.map(e => ({
    id: e.id,
    relation: e.relation_label ?? e.relation_type,
    weak: !STRONG_RELATIONS.has(e.relation_type),
    route: routeById.get(e.id),
  })), nodeGeom)
  const edges: Edge[] = visibleFinal.map(e => {
    const rs = RELATION_STYLE[e.relation_type] ?? FALLBACK_RELATION_STYLE
    const mixPct = mixPctById.get(e.id) ?? 0
    const stroke = mixPct > 0 ? `color-mix(in srgb, ${rs.stroke} ${100 - mixPct}%, white)` : rs.stroke
    const dim = byId.get(e.source)?.collapsed || byId.get(e.target)?.collapsed
    const weak = !STRONG_RELATIONS.has(e.relation_type)
    // P3-A 长线语义翻新:泳道差 ≥2 边走通道+行间跳越,零压卡;z=1/细线/半透明三件套
    // 保留(语义从「压卡可见」变为「顶层通道线可见」,断言② 口径不动——施工单 §三.A)。
    const sType = byId.get(e.source)?.type
    const tType = byId.get(e.target)?.type
    const sLane = sType ? LANES.indexOf(sType) : -1
    const tLane = tType ? LANES.indexOf(tType) : -1
    const longHaul = sLane >= 0 && tLane >= 0 && tLane - sLane >= 2
    const baseWidth = longHaul ? rs.strokeWidth * 0.75 : rs.strokeWidth
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      // P2a 端口:xyflow 按 Handle 实位(id 对应)算锚点;同列边目标走右侧 rin Handle
      sourceHandle: srcHandle.get(e.id),
      targetHandle: tgtHandle.get(e.id),
      type: 'chainRel' as const,
      zIndex: longHaul ? 1 : undefined,
      style: { stroke, strokeWidth: baseWidth, strokeDasharray: rs.dashed ? '6 4' : rs.dotted ? '2 4' : undefined, opacity: dim ? 0.4 : longHaul ? 0.55 : undefined },
      markerEnd: { type: 'arrowclosed' as const, color: stroke },
      data: {
        relation: e.relation_label ?? e.relation_type,
        color: stroke,
        mixPct,
        weak,
        labelPos: labelPl.pos.get(e.id),
        labelConflict: labelPl.conflicts.includes(e.id),
        labelOn: false,
        dim: !!dim,
        route: routeById.get(e.id),
        gapFallback: fallbackIds.has(e.id),
        longHaul,
        detailRef: e.ruling_ids[0] ? `ruling:${e.ruling_ids[0]}` : '',
        evidenceIds: e.evidence_ids,
      },
    }
  })

  return { nodes: [...laneHeads, ...nodes], edges, byId, clippedByLane, clippedSameRoot }
}

/** 路径高亮:一级邻接;结论节点=沿边反向回溯全部上游(结论→论点→事实→来源) */
export function upstreamClosure(g: ChainGraph, startId: string): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set<string>([startId])
  const edges = new Set<string>()
  const incoming = new Map<string, Edge[]>()
  for (const e of g.edges) {
    const list = incoming.get(e.target) ?? []
    list.push(e)
    incoming.set(e.target, list)
  }
  const walk = (id: string) => {
    for (const e of incoming.get(id) ?? []) {
      edges.add(e.id)
      if (!nodes.has(e.source)) { nodes.add(e.source); walk(e.source) }
    }
  }
  walk(startId)
  return { nodes, edges }
}

export function neighborhood(g: ChainGraph, startId: string): { nodes: Set<string>; edges: Set<string> } {
  const nodes = new Set<string>([startId])
  const edges = new Set<string>()
  for (const e of g.edges) {
    if (e.source === startId) { edges.add(e.id); nodes.add(e.target) }
    if (e.target === startId) { edges.add(e.id); nodes.add(e.source) }
  }
  return { nodes, edges }
}

/** F4 hover 邻域:端点邻接表(边 id → 与其共享 source 或 target 的边 id 集合) */
export function edgeNeighborMap(edges: Edge[]): Map<string, Set<string>> {
  const byNode = new Map<string, string[]>()
  for (const e of edges) {
    for (const p of [e.source, e.target]) {
      const list = byNode.get(p) ?? []
      list.push(e.id)
      byNode.set(p, list)
    }
  }
  const m = new Map<string, Set<string>>()
  for (const e of edges) {
    const nbrs = new Set<string>()
    for (const p of [e.source, e.target]) for (const eid of byNode.get(p) ?? []) if (eid !== e.id) nbrs.add(eid)
    m.set(e.id, nbrs)
  }
  return m
}

/** 关系词→视觉:反证暗红/时效更新琥珀虚线/同源共据点线灰/派生·前提虚线,粗细分层(方案 §五) */
export const RELATION_STYLE: Record<string, { stroke: string; strokeWidth: number; dashed?: boolean; dotted?: boolean }> = {
  '直接证明': { stroke: 'var(--s2-chain-prove)', strokeWidth: 2.4 },
  '印证': { stroke: 'var(--s2-chain-prove)', strokeWidth: 1.6 },
  '佐证': { stroke: 'var(--s2-chain-prove)', strokeWidth: 1.1 },
  '传导': { stroke: 'var(--s2-chain-infer)', strokeWidth: 1.6 },
  '前提': { stroke: 'var(--s2-chain-infer)', strokeWidth: 1.4, dashed: true },
  '同源共据': { stroke: 'var(--s2-chain-same)', strokeWidth: 1.2, dotted: true },
  '反证': { stroke: 'var(--s2-chain-against)', strokeWidth: 2.2 },
  '时效更新': { stroke: 'var(--s2-chain-temporal)', strokeWidth: 1.6, dashed: true },
  '核验': { stroke: 'var(--s2-chain-same)', strokeWidth: 1.1, dashed: true },
  '入组': { stroke: 'var(--s2-chain-same)', strokeWidth: 1.1, dashed: true },
  '依据': { stroke: 'var(--s2-chain-same)', strokeWidth: 1.1, dashed: true },
}

export const FALLBACK_RELATION_STYLE = { stroke: 'var(--s2-chain-same)', strokeWidth: 1.2 }

// ── 几何唯一来源(P3-A):buildRelPath 只做「点序列→圆角 path d」,点序列由
//    chainLayout 构图注入 route;labelLayout 候选/ChainRelEdge 标签同吃该点序列。──────
export type Pt = [number, number]

/** 拐角圆角半径(px),与原 smoothstep 视觉一致 */
const CORNER_R = 8

/**
 * 折线几何唯一来源(职责收窄后):点序列 → 带圆角 path d。
 * 点序列由调用方注入(chainLayout 构图时按通道车道/行间跳越算好);
 * 水平两点直线、零长段(跳越 y 退化等)直通不圆角。
 */
export function buildRelPath(points: Pt[]): string {
  return roundedPathD(points)
}

/** 折线 → SVG path d:每个内点处进入/离开段各留 r,圆角用 Q 指令近似 */
function roundedPathD(points: Pt[]): string {
  if (points.length < 2) return ''
  let d = `M ${points[0][0]} ${points[0][1]}`
  for (let i = 1; i < points.length - 1; i++) {
    const [px, py] = points[i - 1]
    const [cx, cy] = points[i]
    const [nx, ny] = points[i + 1]
    const dIn = Math.hypot(cx - px, cy - py)
    const dOut = Math.hypot(nx - cx, ny - cy)
    const r = Math.min(CORNER_R, dIn / 2, dOut / 2)
    if (!(r > 0)) continue   // 零长段(重合点)直通,不产 NaN
    const inX = cx - ((cx - px) / dIn) * r
    const inY = cy - ((cy - py) / dIn) * r
    const outX = cx + ((nx - cx) / dOut) * r
    const outY = cy + ((ny - cy) / dOut) * r
    d += ` L ${inX} ${inY} Q ${cx} ${cy} ${outX} ${outY}`
  }
  const last = points[points.length - 1]
  return `${d} L ${last[0]} ${last[1]}`
}

/** 沿折线按弧长取 t 点(与 path 同一 points,标签与线天然贴合) */
export function pointAt(points: Pt[], t: number): { x: number; y: number } {
  let total = 0
  const segs: number[] = []
  for (let i = 1; i < points.length; i++) {
    const d = Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
    segs.push(d)
    total += d
  }
  let rest = Math.max(0, Math.min(1, t)) * total
  for (let i = 0; i < segs.length; i++) {
    if (rest <= segs[i] || i === segs.length - 1) {
      const r = segs[i] === 0 ? 0 : rest / segs[i]
      return { x: points[i][0] + (points[i + 1][0] - points[i][0]) * r, y: points[i][1] + (points[i + 1][1] - points[i][1]) * r }
    }
    rest -= segs[i]
  }
  const [x, y] = points[points.length - 1]
  return { x, y }
}
