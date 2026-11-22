// 证据链工作台(Phase 1):chain_web.v1 静态 JSON 的只读交互投影。
// 数据通道一期 = 本地导入 --format web 出件;零后端依赖、零写台账。
// 前端禁止改写 claim 状态/独立根数/结论——一切状态徽章、计数都来自 Python 判决侧。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ReactFlow, ReactFlowProvider, Background, Controls, MiniMap,
  BackgroundVariant, type Node, type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import './chain.css'
import type { ChainWebPayload, LaneId } from '../types/chainWeb'
import { validateChainWeb } from '../types/chainWeb'
import { buildChainGraph, upstreamClosure, neighborhood, edgeNeighborMap, LANES } from '../components/chain/chainLayout'
import type { ChainNodeData } from '../components/chain/chainLayout'
import { chainNodeTypes } from '../components/chain/ChainNodes'
import { ChainRelEdge } from '../components/chain/ChainRelEdge'
import { ChainDrawerBoundary } from '../components/chain/ChainDrawer'
import { Icon } from '../components/Icon'
import { api, ApiError, type ChainArtifactStatus } from '../api/client'

const LANE_LABEL: Record<LaneId, string> = { source: '来源', fact: '事实', verification: '验证', reasoning: '推理', conclusion: '结论' }
const STATUS_OPTIONS = ['已证实', '待证实', '矛盾']
const chainEdgeTypes = { chainRel: ChainRelEdge }

function formatTimestamp(raw: string): string {
  if (!raw) return '未记录'
  const d = new Date(raw)
  return Number.isNaN(d.getTime()) ? raw : new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(d)
}

function evidenceStateLabel(state: string): string {
  return ({ supported: '有直接支持', contested: '有反证', unadjudicated: '材料待裁定', no_direct: '无直接证据' } as Record<string, string>)[state] ?? state
}

type LoadResult = { payload: ChainWebPayload } | { problems: string[]; fileName: string }
type LocalRun = { file: string; eventId: string; kind: string; nNodes: number; nEdges: number; generatedAt: string }

function queryParams(): URLSearchParams {
  const raw = window.location.hash.includes('?') ? window.location.hash.split('?')[1] : window.location.search.replace(/^\?/, '')
  return new URLSearchParams(raw)
}

export function ChainPage({ standalone = false }: { standalone?: boolean } = {}) {
  const [load, setLoad] = useState<LoadResult | null>(null)
  const [runsList, setRunsList] = useState<LocalRun[]>([])
  const [currentRunFile, setCurrentRunFile] = useState('')
  const [localRunNote, setLocalRunNote] = useState('')
  const [drawerRef, setDrawerRef] = useState('')
  const [focusId, setFocusId] = useState('')
  const [onlyPath, setOnlyPath] = useState(false)
  const [hiddenLanes, setHiddenLanes] = useState<Set<LaneId>>(new Set())
  const [statusFilter, setStatusFilter] = useState('')
  const [query, setQuery] = useState('')
  const [hideCollapsed, setHideCollapsed] = useState(true)
  const [onlyNeedsEvidence, setOnlyNeedsEvidence] = useState(false)
  const [expandedEdgeGroups, setExpandedEdgeGroups] = useState<Set<string>>(new Set())
  const [showSameRoot, setShowSameRoot] = useState(false)
  const [hoveredEdge, setHoveredEdge] = useState('')
  const [omissionsOpen, setOmissionsOpen] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const settleLoad = useCallback((result: LoadResult, runFile = '') => {
    setLoad(result)
    setCurrentRunFile(runFile)
    setDrawerRef(''); setFocusId(''); setOmissionsOpen(false); setExpandedEdgeGroups(new Set())
  }, [])

  const readFile = useCallback((file: File) => {
    file.text().then(text => {
      try {
        const parsed = validateChainWeb(JSON.parse(text))
        settleLoad(parsed.ok ? { payload: parsed.payload } : { problems: parsed.problems, fileName: file.name })
        setLocalRunNote('')
      } catch (err) { settleLoad({ problems: [`JSON 解析失败:${String(err)}`], fileName: file.name }) }
    })
  }, [settleLoad])

  // ?src= 相对路径加载(同源 JSON,如 dev/preview 下挂样板出件);&open= 直开某详情抽屉(深链);
  // file:// 双击打开时仍走导入按钮
  const srcRef = useRef(false)
  useEffect(() => {
    if (srcRef.current) return
    srcRef.current = true
    const qs = window.location.search.includes('src=') ? window.location.search
      : window.location.hash.includes('?') ? window.location.hash.split('?')[1] : null
    if (!qs) return
    const params = new URLSearchParams(qs)
    const raw = params.get('src')
    const open = params.get('open')
    if (!raw) { if (open) setDrawerRef(open); return }
    fetch(raw).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.text() }).then(text => {
      try {
        const parsed = validateChainWeb(JSON.parse(text))
        setLoad(parsed.ok ? { payload: parsed.payload } : { problems: parsed.problems, fileName: raw })
        if (open) setDrawerRef(open)
      } catch (err) { setLoad({ problems: [`JSON 解析失败:${String(err)}`], fileName: raw }) }
    }).catch(err => setLoad({ problems: [`加载失败:${String(err)}`], fileName: raw }))
  }, [])

  const loadLocalRun = useCallback(async (run: LocalRun) => {
    const relativePath = `./runs/${run.file}`
    try {
      const response = await fetch(relativePath, { cache: 'no-store' })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      try {
        const parsed = validateChainWeb(JSON.parse(await response.text()))
        settleLoad(parsed.ok ? { payload: parsed.payload } : { problems: parsed.problems, fileName: relativePath }, run.file)
      } catch (err) {
        settleLoad({ problems: [`JSON 解析失败:${String(err)}`], fileName: relativePath }, run.file)
      }
    } catch (err) {
      settleLoad({ problems: [`加载失败:${String(err)}`], fileName: relativePath }, run.file)
    }
  }, [settleLoad])

  // 数据源优先级：src 深链 > investigation REST > REST 最新 ready > 静态 runs > 手工导入。
  const localRunsRef = useRef(false)
  useEffect(() => {
    if (localRunsRef.current) return
    localRunsRef.current = true
    const params = queryParams()
    if (window.location.protocol === 'file:' || params.has('src')) return

    const acceptPayload = (raw: unknown, label: string) => {
      const parsed = validateChainWeb(raw)
      settleLoad(parsed.ok ? { payload: parsed.payload } : { problems: parsed.problems, fileName: label })
    }
    const loadStatic = () => fetch('./runs/index.json', { cache: 'no-store' }).then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const raw = await response.json() as { runs?: unknown }
      if (!Array.isArray(raw.runs)) throw new Error('index.json 缺少 runs 数组')
      const runs = raw.runs.filter((item): item is LocalRun => {
        if (!item || typeof item !== 'object') return false
        const run = item as Partial<LocalRun>
        return typeof run.file === 'string' && run.file.endsWith('.web.json') && !run.file.includes('/') && !run.file.includes('\\') &&
          typeof run.eventId === 'string' && typeof run.kind === 'string' && typeof run.nNodes === 'number' &&
          typeof run.nEdges === 'number' && typeof run.generatedAt === 'string'
      })
      if (runs.length !== raw.runs.length) throw new Error('index.json 含无效出件条目')
      setRunsList(runs)
      if (runs.length === 0) {
        setLocalRunNote('未发现本地出件：跑 npm run sync:runs -- <出件> 同步，或导入文件。')
        return
      }
      setLocalRunNote('')
      await loadLocalRun(runs[0])
    }).catch(() => {
      setLocalRunNote('未发现本地出件：跑 npm run sync:runs -- <出件> 同步，或导入文件。')
    })

    const showStatus = (status: ChainArtifactStatus) => {
      const message = status.status === 'failed'
        ? '报告已生成，证据链出件失败；请执行 chain-backfill 后重试。'
        : '证据链产物无效，请检查服务日志并重新补生成。'
      settleLoad({ problems: [message, status.error_message || status.error_code || status.status], fileName: status.investigation_id })
    }
    const investigation = params.get('investigation')
    if (investigation) {
      api.chainStatus(investigation).then(status => {
        if (status.status !== 'ready') { showStatus(status); return }
        return api.chainWeb<unknown>(investigation).then(raw => acceptPayload(raw, investigation))
      }).catch(err => {
        const missing = err instanceof ApiError && err.status === 404
        settleLoad({ problems: [missing ? '历史任务尚未补生成证据链。' : `证据链服务加载失败：${String(err)}`], fileName: investigation })
      })
      return
    }
    if (standalone) { void loadStatic(); return }

    api.chainRuns({ page_size: 100 }).then(page => {
      const ready = page.items.find(item => item.status === 'ready')
      if (ready) return api.chainWeb<unknown>(ready.investigation_id).then(raw => acceptPayload(raw, ready.investigation_id))
      if (page.items[0]) { showStatus(page.items[0]); return }
      return loadStatic()
    }).catch(err => {
      // 登录已失效时仍可读取本地出件；服务端 5xx/坏件必须显式失败。
      if (err instanceof ApiError && err.status === 401) { void loadStatic(); return }
      settleLoad({ problems: [`证据链服务加载失败：${String(err)}`], fileName: 'REST /chain-web' })
    })
  }, [loadLocalRun, standalone])

  const payload = load && 'payload' in load ? load.payload : null

  const view = useMemo(() => {
    if (!payload) return null
    const g = buildChainGraph(payload, { hiddenLanes, statusFilter, query, hideCollapsed, showSameRoot, onlyNeedsEvidence, expandedEdgeGroups })
    let keep: { nodes: Set<string>; edges: Set<string> } | null = null
    if (focusId) keep = (onlyPath || g.byId.get(focusId)?.type === 'conclusion') ? upstreamClosure(g, focusId) : neighborhood(g, focusId)
    // F4 hover 邻域精准淡化(线条修正方案 §F4,替换 CSS 全量淡化):只淡化与 hover 边
    // 共享端点的邻边(op 0.45),无关边不动;hover 边自身加粗+标签浮现,邻边标签也浮现
    const hovered = hoveredEdge && g.edges.some(e => e.id === hoveredEdge) ? edgeNeighborMap(g.edges).get(hoveredEdge) : null
    const nodes = g.nodes.map((n): Node<ChainNodeData> => {
      const selected = n.id === focusId
      const inKeep = !keep || keep.nodes.has(n.id) || n.id.startsWith('head:')
      return inKeep ? { ...n, selected, data: { ...n.data, dimmed: false } } : { ...n, selected: false, data: { ...n.data, dimmed: true } }
    })
    const edges = g.edges.map((e): Edge => {
      const inKeep = !keep || keep.edges.has(e.id)
      const isHover = e.id === hoveredEdge
      const isNbr = hovered?.has(e.id) ?? false
      const base: Edge = { ...e, data: { ...e.data, labelOn: !e.data!.weak || isHover || isNbr } }
      if (!inKeep) return { ...base, style: { ...e.style, opacity: 0.12 }, data: { ...base.data, labelOn: false } }
      if (!hovered) return base
      let style: Edge['style'] = { ...base.style, opacity: isHover ? 1 : isNbr ? 0.45 : e.style?.opacity }
      if (isHover && typeof style.strokeWidth === 'number') style = { ...style, strokeWidth: style.strokeWidth + 0.8 }
      return { ...base, style }
    })
    return { nodes, edges, clippedByLane: g.clippedByLane, clippedSameRoot: g.clippedSameRoot, labelConflicts: g.edges.filter(e => e.data?.labelConflict).length, routeFallbacks: g.edges.filter(e => e.data?.gapFallback).length }
  }, [payload, hiddenLanes, statusFilter, query, hideCollapsed, showSameRoot, onlyNeedsEvidence, expandedEdgeGroups, focusId, onlyPath, hoveredEdge])

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    if (node.id.startsWith('head:')) return
    const chain = (node.data as ChainNodeData).chain
    if (chain.sub_type === 'fact_cluster' && chain.edge_group_id) {
      setExpandedEdgeGroups(prev => new Set(prev).add(chain.edge_group_id!))
      setFocusId('')
      setDrawerRef(chain.detail_ref)
      return
    }
    setFocusId(node.id)
    const ref = chain.detail_ref
    setDrawerRef(ref || `missing:${chain.id}`)
  }, [])
  const onEdgeClick = useCallback((_: React.MouseEvent, edge: Edge) => {
    setFocusId(edge.source)
    setDrawerRef((edge.data as { detailRef?: string } | undefined)?.detailRef ?? '')
  }, [])
  const onEdgeMouseEnter = useCallback((_: React.MouseEvent, edge: Edge) => setHoveredEdge(edge.id), [])
  const onEdgeMouseLeave = useCallback(() => setHoveredEdge(''), [])
  const onPaneClick = useCallback(() => { setFocusId(''); setDrawerRef('') }, [])

  const toggleLane = (lane: LaneId) => setHiddenLanes(prev => { const next = new Set(prev); if (next.has(lane)) next.delete(lane); else next.add(lane); return next })

  if (!load) return <ImportSplash onPick={() => fileInput.current?.click()} onFile={readFile} inputRef={fileInput} localRunNote={localRunNote} />
  if ('problems' in load) return <ErrorSplash problems={load.problems} fileName={load.fileName} onFile={readFile} inputRef={fileInput} />
  const p = load.payload
  const omissionsActive = p.omissions.filter(o => o.n > 0)
  const oldProjection = !p.meta.generated_at || !p.meta.projection_version

  return <div className="chain-page">
    <input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={e => { const f = e.target.files?.[0]; if (f) readFile(f); e.currentTarget.value = '' }} />
    <div className="chain-toolbar">
      <div className="chain-toolbar-id">
        <strong>{p.event.event_id}</strong>
        <span className="chip">{p.event.kind}</span>
        {runsList.length > 1 && <select aria-label="本地出件" value={currentRunFile}
          onChange={e => { const run = runsList.find(item => item.file === e.target.value); if (run) void loadLocalRun(run) }}>
          {runsList.map(run => <option key={run.file} value={run.file}>{run.eventId} · {run.kind} · {run.file}</option>)}
        </select>}
        <span className="chain-asof">数据截至 {formatTimestamp(p.event.as_of)}{p.event.kb_version ? ` · KB ${p.event.kb_version}` : ''}</span>
        {oldProjection
          ? <span className="chip chip-legacy" title="该文件没有 generated_at/projection_version，可能早于当前语义投影规则">旧版出件</span>
          : <span className="chain-asof" title={`生成器 ${p.meta.generator_revision}`}>出件于 {formatTimestamp(p.meta.generated_at!)} · {p.meta.projection_version}</span>}
        <span className="chip chip-schema" title="chain_web.v1:严格校验通过的只读投影">chain_web.v1</span>
      </div>
      <div className="chain-toolbar-filters">
        <div className="lane-toggles" role="group" aria-label="泳道显隐">
          {LANES.map(lane => (
            <button key={lane} className={`lane-toggle lane-${lane} ${hiddenLanes.has(lane) ? 'off' : ''}`} onClick={() => toggleLane(lane)} aria-pressed={!hiddenLanes.has(lane)}>{LANE_LABEL[lane]}</button>
          ))}
        </div>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} aria-label="状态筛选">
          <option value="">全部状态</option>
          {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <input className="chain-search" placeholder="搜命题/证据/编号…" value={query} onChange={e => setQuery(e.target.value)} aria-label="搜索" />
        <label className="chain-check"><input type="checkbox" checked={!hideCollapsed} onChange={e => setHideCollapsed(!e.target.checked)} />显示折叠件</label>
        <label className="chain-check" title="显示有反证、材料待裁定或无直接证据的事实"><input type="checkbox" checked={onlyNeedsEvidence} onChange={e => setOnlyNeedsEvidence(e.target.checked)} />只看待补证</label>
        {expandedEdgeGroups.size > 0 && <button className="button" onClick={() => setExpandedEdgeGroups(new Set())}>收起汇流 {expandedEdgeGroups.size}</button>}
        <label className="chain-check" title="点选节点后只保留该节点的完整支撑/拆台路径,其余淡化"><input type="checkbox" checked={onlyPath} onChange={e => setOnlyPath(e.target.checked)} />只看路径</label>
        <label className="chain-check" title="「同源共据」是同根转载的折算注记(十家转载=一根),不是推理关系;默认隐藏降噪"><input type="checkbox" checked={showSameRoot} onChange={e => setShowSameRoot(e.target.checked)} />同源边</label>
        <button className="button" onClick={() => fileInput.current?.click()}><Icon name="file" size={14}/>换一份数据</button>
      </div>
    </div>

    <div className="chain-stats">
      <span className="stat" title="来源材料件数">材料 <b>{p.stats.n_materials}</b></span>
      <span className="stat stat-strong" title="谱系根数——十家转载=一根,材料数≠独立根数">独立根 <b>{p.stats.n_independent_roots}</b></span>
      <span className="stat" title="同根折算折叠数">同源折叠 <b>{p.stats.n_same_root_collapsed}</b></span>
      {Object.entries(p.stats.status_dist).map(([s, n]) => <span key={s} className={`stat stat-status-${s === '已证实' ? 'verified' : s === '矛盾' ? 'contra' : 'pending'}`}>{s} <b>{n}</b></span>)}
      {Object.entries(p.stats.evidence_state_dist ?? {}).map(([s, n]) => <span key={s} className={`stat stat-evidence-${s}`}>{evidenceStateLabel(s)} <b>{n}</b></span>)}
      {p.edge_groups.length > 0 && <span className="stat stat-strong" title="高扇出原边全量保留，默认显示汇流事实簇">汇流簇 <b>{p.edge_groups.length}</b>{expandedEdgeGroups.size ? ` · 已展开 ${expandedEdgeGroups.size}` : ''}</span>}
      {hiddenLanes.size > 0 && view && (hiddenLanes.size > 0) && (
        <span className="stat stat-clipped" title="隐藏泳道会把跨入该泳道的边一并收起——省略显式呈现,不静默裁剪">
          隐藏 {hiddenLanes.size} 泳道 · 裁边 <b>{view.clippedByLane}</b>
        </span>
      )}
      {!showSameRoot && <span className="stat stat-clipped" title="「同源共据」为转载折算注记,勾选「同源边」显示">同源边已收 <b>{view?.clippedSameRoot ?? 0}</b></span>}
      {!!view?.labelConflicts && <span className="stat stat-clipped" title="标签避让失败(所有候选位置均碰撞),不静默隐藏——对齐「省略显式呈现」纪律">标签避让失败 <b>{view.labelConflicts}</b></span>}
      {!!view?.routeFallbacks && <span className="stat stat-clipped" title="长线行间无空隙,跳越 y 取最小重叠兜底(可能擦卡,[diag] 监督)——省略显式呈现,不静默">长线跳越兜底 <b>{view.routeFallbacks}</b></span>}
      <button className={`chain-omissions-btn ${omissionsActive.length ? 'has-omissions' : ''}`} onClick={() => setOmissionsOpen(v => !v)} aria-expanded={omissionsOpen}>
        <Icon name="alert" size={13}/>省略与注记 {p.omissions.reduce((a, o) => a + (o.n > 0 ? 1 : 0), 0)} 项
      </button>
      {omissionsOpen && <div className="chain-omissions-panel" role="dialog" aria-label="省略与注记">
        <p className="drawer-note">省略显式呈现,不静默裁剪;n=0 的口径项一并如实列出。</p>
        <ul>{p.omissions.map(o => <li key={o.key} className={o.n > 0 ? 'omission-active' : ''}><b>{o.n}</b> {o.note}<code className="drawer-ref">{o.key}</code></li>)}</ul>
      </div>}
    </div>

    <div className="chain-canvas">
      {view
        ? <ReactFlowProvider>
            <ReactFlow
              nodes={view.nodes}
              edges={view.edges}
              nodeTypes={chainNodeTypes}
              edgeTypes={chainEdgeTypes}
              onEdgeMouseEnter={onEdgeMouseEnter}
              onEdgeMouseLeave={onEdgeMouseLeave}
              fitView
              minZoom={0.15}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable
              proOptions={{ hideAttribution: true }}
              onNodeClick={onNodeClick}
              onEdgeClick={onEdgeClick}
              onPaneClick={onPaneClick}
            >
              <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="var(--s2-line)" />
              <Controls showInteractive={false} position="bottom-right" />
              <MiniMap pannable zoomable position="bottom-left" nodeColor={() => 'var(--s2-line-strong)'} maskColor="rgba(245,247,250,0.8)" />
            </ReactFlow>
          </ReactFlowProvider>
        : null}
      {drawerRef && <ChainDrawerBoundary key={drawerRef} payload={p} detailRef={drawerRef}
        onNavigateClaim={claimId => { setFocusId(`cl:${claimId}`); setDrawerRef(`claim:${claimId}`) }}
        onClose={() => { setDrawerRef(''); setFocusId('') }} />}
      {focusId && !drawerRef && <div className="chain-focus-hint">已高亮路径 · 点空白处取消</div>}
    </div>
  </div>
}

function ImportSplash({ onPick, onFile, inputRef, localRunNote }: { onPick: () => void; onFile: (f: File) => void; inputRef: React.RefObject<HTMLInputElement | null>; localRunNote: string }) {
  return <div
    className="chain-splash"
    onDragOver={e => { e.preventDefault(); e.currentTarget.classList.add('drag') }}
    onDragLeave={e => e.currentTarget.classList.remove('drag')}
    onDrop={e => { e.preventDefault(); e.currentTarget.classList.remove('drag'); const f = e.dataTransfer.files?.[0]; if (f) onFile(f) }}
  >
    <input ref={inputRef} type="file" accept=".json,application/json" hidden onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f); e.currentTarget.value = '' }} />
    <div className="chain-splash-icon"><Icon name="layers" size={40}/></div>
    <h2>证据链工作台</h2>
    <p>本页默认通过只读 REST 打开最新成功出件，<br/>也可导入 <code>.web.json</code> 或把文件拖到本页。前端只读投影，不写台账。</p>
    <button className="button button-primary" onClick={onPick}>选择 JSON 文件</button>
    {localRunNote && <p className="chain-splash-hint">{localRunNote}</p>}
    <p className="chain-splash-hint">出件方法:在 agent 目录执行<br/><code>python -m sense2act chain-graph --event-id &lt;id&gt; --db data/x.db --format web --out runs/xxx.web.json</code></p>
  </div>
}

function ErrorSplash({ problems, fileName, onFile, inputRef }: { problems: string[]; fileName: string; onFile: (f: File) => void; inputRef: React.RefObject<HTMLInputElement | null> }) {
  return <div className="chain-splash chain-splash-error">
    <input ref={inputRef} type="file" accept=".json,application/json" hidden onChange={e => { const f = e.target.files?.[0]; if (f) onFile(f); e.currentTarget.value = '' }} />
    <div className="chain-splash-icon"><Icon name="alert" size={40}/></div>
    <h2>这不是有效的 chain_web.v1 出件</h2>
    <p>文件 <code>{fileName}</code> 未通过契约校验,拒绝渲染(不猜、不补、不伪装):</p>
    <ul className="chain-error-list">{problems.slice(0, 8).map((prob, i) => <li key={i}>{prob}</li>)}{problems.length > 8 && <li>…共 {problems.length} 条问题</li>}</ul>
    <button className="button button-primary" onClick={() => inputRef.current?.click()}>重新选择</button>
  </div>
}
