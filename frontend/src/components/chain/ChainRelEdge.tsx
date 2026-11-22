// 自定义关系边:P3 起纯消费组件——route 直角折线点序列由 chainLayout 构图算好
// 注入,本组件不再拼几何(职责收窄,施工单 20261110 §三.A.3)。
// 唯一几何源仍在 chainLayout:折线点序列与 SVG path 一次生成同源,标签位置取同一
// 点序列按弧长参数化——禁止组件内再各自拼几何。
import { BaseEdge, EdgeLabelRenderer, type EdgeProps } from '@xyflow/react'
import { buildRelPath, pointAt } from './chainLayout'
import type { Pt } from './chainLayout'

export interface ChainRelEdgeData extends Record<string, unknown> {
  relation: string
  color: string
  weak: boolean
  /** hover/选中时常显(弱语义边默认隐藏) */
  labelOn: boolean
  /** P3-A 绝对点序列(chainLayout 唯一几何源;渲染直接消费,不经 xyflow 锚点重算) */
  route?: Pt[]
  /** P3-A 无空隙兜底注记(跳越 y 取最小重叠,可能擦卡;统计条显式呈现) */
  gapFallback?: boolean
  /** P1 标签避让位:沿线弧长参数 t + 垂直偏移 dy(贴线仍由 pointAt 保证) */
  labelPos?: { t: number; dy: number }
  /** P1 避让失败(所有候选均碰撞),统计条显式注记用 */
  labelConflict?: boolean
}

export function ChainRelEdge({ id, sourceX, sourceY, targetX, targetY, style, markerEnd, data, selected }: EdgeProps) {
  const d = (data ?? {}) as Partial<ChainRelEdgeData>
  const fallback: Pt[] = [[sourceX, sourceY], [targetX, targetY]]
  const points = d.route ?? fallback
  const path = buildRelPath(points)
  // P1 避让位:基点沿线(pointAt 用同一 points),dy 固定 -12px 锚在箭头末端上方。
  const lp = d.labelPos ?? { t: 0.5, dy: 0 }
  const base = pointAt(points, lp.t)
  const p = { x: base.x, y: base.y + lp.dy }
  const showLabel = d.labelOn || selected
  return <>
    {/* style/markerEnd 在 Edge 顶层(xyflow 契约),不在 data——曾误读 data.style 导致
        线色/粗细/虚实/箭头/淡化全程未渲染(全灰默认线,2026-11-10 探针实锤后修复) */}
    <BaseEdge id={id} path={path} style={style} markerEnd={markerEnd} />
    <EdgeLabelRenderer>
      {showLabel && (
        <div
          className={'chain-rel-label' + (d.weak ? ' chain-rel-label-weak' : '')}
          data-edge-id={id}
          style={{
            transform: `translate(-50%, -50%) translate(${p.x}px, ${p.y}px)`,
            color: d.color,
            borderColor: d.color,
          }}
        >
          {d.relation}
        </div>
      )}
    </EdgeLabelRenderer>
  </>
}
