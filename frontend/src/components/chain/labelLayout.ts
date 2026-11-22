// P1 标签避让引擎(施工单 20261110 §三.P1):纯函数,确定性(候选序固定 + 碰撞集
// 遍历按 id 序),同输入同输出。只动标签的「沿线位置」,不动数据契约/三态/关系语义。
//
// P3 起几何输入=route 点序列(chainLayout 构图算好的绝对折线,渲染 ≡ 避让几何
// 同一来源),本引擎不再复现折线。输出 (t, dy):t 只在末段 [0.7,1] 候选区内避让,
// dy 固定 -12px,将关系词锚在箭头末尾上方。
// 避让决策用估算卡高(偏保守覆盖真实卡),判定「不压卡」是安全的(估算矩形 ⊇ 真实卡)。

import { pointAt, CARD_W, CARD_H, CARD_H_SLACK } from './chainLayout'
import type { Pt } from './chainLayout'
import type { LaneId } from '../../types/chainWeb'
const LABEL_H = 16        // 实测 .chain-rel-label offsetHeight
const LABEL_PAD = 14      // padding 0 6px×2 + border 1px×2
const LABEL_T_MIN = 0.7
const LABEL_DY = -12
const LABEL_END_X = 70
// 强语义优先级:反证 > 直接证明 > 时效更新;弱语义(印证/佐证/传导/前提)排其后(=3)
const STRONG_PRIORITY: Record<string, number> = { '反证': 0, '直接证明': 1, '时效更新': 2 }

export interface LabelPos { t: number; dy: number }
export interface LabelPlacement { pos: Map<string, LabelPos>; conflicts: string[] }
export interface LabelEdge {
  id: string; relation: string; weak: boolean
  /** P3 route 点序列(chainLayout 算好的绝对折线),缺边跳过避让 */
  route?: Pt[]
}
export interface NodeGeom { x: number; y: number; lane: LaneId }

/** 标签宽:实测校准——直接证明(4字)=58 / 反证(2字)=36,即 字数×11 + 14(padding+border) */
function labelWidth(text: string): number {
  return text.length * 11 + LABEL_PAD
}

function rectsHit(a: number[], b: number[]): boolean {
  return Math.min(a[2], b[2]) - Math.max(a[0], b[0]) > 1 && Math.min(a[3], b[3]) - Math.max(a[1], b[1]) > 1
}

/** 线段×矩形相交(Liang-Barsky);rect=[left,top,right,bottom] */
function segHitsRect(x1: number, y1: number, x2: number, y2: number, r: number[]): boolean {
  let t0 = 0, t1 = 1
  const dx = x2 - x1, dy = y2 - y1
  const sides = [[-dx, x1 - r[0]], [dx, r[2] - x1], [-dy, y1 - r[1]], [dy, r[3] - y1]]
  for (const [p, q] of sides) {
    if (p === 0) { if (q < 0) return false; continue }
    const t = q / p
    if (p < 0) { if (t > t1) return false; if (t > t0) t0 = t }
    else { if (t < t0) return false; if (t < t1) t1 = t }
  }
  return true
}

/** 候选 t:每段 2.5% 弧长采样后收窄到全线末段 [0.7,1]。保留逐段密采样,
 *  让同目标高入度组可在末段入腿/前一竖缝内错开;显式补两端点防采样遗漏边界。 */
function candidateTs(points: Pt[]): number[] {
  const segLens: number[] = []
  let total = 0
  for (let i = 1; i < points.length; i++) {
    const d = Math.hypot(points[i][0] - points[i - 1][0], points[i][1] - points[i - 1][1])
    segLens.push(d)
    total += d
  }
  if (total === 0) return [1]
  const ts = new Set<number>()
  let acc = 0
  for (let i = 0; i < segLens.length; i++) {
    const s = acc / total
    acc += segLens[i]
    const e = acc / total
    for (let k = 1; k < 40; k++) ts.add(Number((s + (e - s) * (k / 40)).toFixed(4)))
  }
  ts.add(LABEL_T_MIN)
  ts.add(1)
  return [...ts].filter(t => t >= LABEL_T_MIN && t <= 1).sort((a, b) => a - b)
}

export function labelPlacement(
  edges: LabelEdge[],
  nodeGeom: Map<string, NodeGeom>,
): LabelPlacement {
  // 1. 末段候选 t + 标签宽(route 点序列由 chainLayout 注入,渲染 ≡ 避让同源)
  const geoms = edges.map(e => {
    if (!e.route || e.route.length < 2) return null
    const points = e.route
    const [endX, endY] = points[points.length - 1]
    const w = labelWidth(e.relation)
    // ④' 验收带的布局侧构造:候选既属于弧长末段,又必须落在箭头前 70px 的末腿上。
    // 中心仍受验收口径约束落在箭头前 70px 带内,同时标签右缘必须停在目标卡左侧。
    // 不要求宽标签的左缘也进入 70px 带,否则「直接证明 ×10」会无解而退到箭头端点。
    const ts = candidateTs(points).filter(t => {
      const p = pointAt(points, t)
      return p.x >= endX - LABEL_END_X
        && p.x <= endX - w / 2 - 2
        && Math.abs(p.y - endY) < 0.01
    })
    if (ts.length === 0) ts.push(1)
    const priority = STRONG_PRIORITY[e.relation] ?? 3
    return { id: e.id, relation: e.relation, weak: e.weak, priority, points, ts, w }
  }).filter((g): g is NonNullable<typeof g> => g !== null)

  // 2. 所有节点卡片矩形(标签要避开任一卡,不只端点卡)。
  //    卡高估算有已知离群(忻州 source 实测 ~118 > est 96,P1「偏保守覆盖」对该值失效):
  //    向下单侧扩 CARD_H_SLACK=24px(卡顶由行位固定、内容向下生长,误差单侧),保
  //    「估算 ⊇ 真实卡」,消灭 est 判 clear / 实撞 的假清(P1 实测 ② 1 例「直接证明」
  //    ×E2 即此窗)。SLACK 常量已提为 chainLayout 共享(P3 行间空隙扫描同源)。
  const nodeRects: number[][] = []
  for (const g of nodeGeom.values()) nodeRects.push([g.x, g.y, g.x + CARD_W, g.y + CARD_H[g.lane] + CARD_H_SLACK])

  // 3. 排序:强语义优先级 → 边 id(确定性)
  geoms.sort((a, b) => a.priority - b.priority || a.id.localeCompare(b.id))

  // 4. 贪心放置:每边选代价最小候选;按「压卡 > 压标签 > 压线」作数量级分层。
  //    (N_node=压节点次数 / N_label=压已放置标签次数 / N_edge=压其他边次数;
  //    N_edge 排除所属边——标签贴线天然与所属边相交不算压边)。
  //    P2a 端口锚估算中点后渲染几何 ≡ 估算几何,判定精确。密集汇入时一个候选
  //    可能同时压过多条线,因此不能用小权重线性相加(会反过来选择标签互压);节点和
  //    标签使用足够大的数量级,只在硬碰撞数相同时才由压线数择优。
  const pos = new Map<string, LabelPos>()
  const conflicts: string[] = []
  const placed: number[][] = []
  for (const g of geoms) {
    let bestT = 0.5, bestCost = Infinity, bestHard = 0
    for (const t of g.ts) {
      const { x, y: lineY } = pointAt(g.points, t)
      const y = lineY + LABEL_DY
      const rect = [x - g.w / 2, y - LABEL_H / 2, x + g.w / 2, y + LABEL_H / 2]
      let nNode = 0
      for (const nr of nodeRects) if (rectsHit(rect, nr)) nNode++
      let nLabel = 0
      for (const pr of placed) if (rectsHit(rect, pr)) nLabel++
      let nEdge = 0
      for (const other of geoms) {
        if (other.id === g.id) continue
        let hit = false
        for (let i = 1; i < other.points.length; i++) {
          if (segHitsRect(other.points[i - 1][0], other.points[i - 1][1], other.points[i][0], other.points[i][1], rect)) { hit = true; break }
        }
        if (hit) nEdge++
      }
      const cost = 1_000_000 * nNode + 10_000 * nLabel + nEdge
      if (cost < bestCost) { bestCost = cost; bestT = t; bestHard = nNode + nLabel }
    }
    pos.set(g.id, { t: bestT, dy: LABEL_DY })
    const { x, y: lineY } = pointAt(g.points, bestT)
    const y = lineY + LABEL_DY
    placed.push([x - g.w / 2, y - LABEL_H / 2, x + g.w / 2, y + LABEL_H / 2])
    // 强语义边避让失败不隐藏(纪律:省略显式呈现,不静默裁剪),注入 conflict 供统计条注记。
    // 「失败」只数硬碰撞(压节点/压标签,与转正断言①②同口径);纯压边(线×标签,密集扇区
    // 不可避免)是软代价不算失败。
    if (bestHard > 0 && !g.weak) conflicts.push(g.id)
  }
  return { pos, conflicts }
}
