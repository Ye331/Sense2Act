// 证据链线条回归验收(线条修正方案 20261110 §五,入仓防回归)。
// 驱动:playwright-core + 本机 Edge(channel:'msedge',不下载浏览器)。
// 目标:dev server 免登录壳 chain.html(vite dev 直出,public/ 映射根,
// ?src=./demo.web.json 深链直开);忻州 r1 件经隐藏 file input 上传喂入。
// 五断言(P2a 起为六断言,另有 [diag] 硬断言四项与确定性断言,见各段注):
//   1 起笔水平率 = 100%(全图统一直角折线,每条边 path 首段 dy=0)
//   2 边可见数 = 预期(demo 40 / 忻州 41,同源边默认收起)+ 长线 zIndex 压卡抽样
//   3 折返边竖缝不挤(同列边分道:任意两折返边竖缝 x 距 ≥12px 或 y 区间不相交)
//   4 标签末尾锚定(中心落在所属边终点左 70px、上 6~22px 的末端带)
//   5 hover 邻域(hover 边加粗 +0.8,邻边 opacity=0.45,无关边保持原样)
//   6 端口分配(P2a):同节点同侧锚点 y 相邻距 ≥12px + [diag] ③ 穿卡全量=0(P3-A 转正)
//     + 确定性(demo 二次加载边/卡/标签逐项一致)
// P0 布局诊断器(施工单 20261110 §三.P0):四指标 [diag] 读数,只测不治,
//   非零不阻塞门禁(治理批后逐项转正硬断言),读数回填施工单 §七 作 P1/P2 对照基线。
// 用法:npm run verify:lines [-- --xinzhou <path-to-xinzhou.web.json>]
import { spawn } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as sleep } from 'node:timers/promises'
import { chromium } from 'playwright-core'
import { DEFAULT_RUNS_DIR, syncChainRuns } from './sync-chain-runs.mjs'

const PORT = 5197                      // 避开 docker 全栈容器占用段(5190+,见 8fc3fde 注)
const BASE = `http://127.0.0.1:${PORT}`
const args = process.argv.slice(2)
const xinzhouIdx = args.indexOf('--xinzhou')
const XINZHOU = xinzhouIdx >= 0 ? args[xinzhouIdx + 1] : null
const DEMO_FILE = fileURLToPath(new URL('../public/demo.web.json', import.meta.url))

// 直查场自包含：同步合成样板，并在系统临时目录派生一份仅改 event_id 的切换探针。
// public/runs/ 全目录被 gitignore；不删用户已同步的真实出件，清单由同步器全量重建。
const directProbeDir = mkdtempSync(resolve(tmpdir(), 'sense2act-chain-runs-'))
const directProbeName = `direct-switch-probe-${process.pid}.web.json`
const directProbeFile = resolve(directProbeDir, directProbeName)
const directProbePayload = JSON.parse(readFileSync(DEMO_FILE, 'utf8'))
directProbePayload.event.event_id = `${directProbePayload.event.event_id}__direct_switch_probe`
writeFileSync(directProbeFile, `${JSON.stringify(directProbePayload)}\n`, 'utf8')
const directRunsIndex = syncChainRuns([DEMO_FILE, directProbeFile])

let failures = 0
const ok = (cond, label) => {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}`)
  if (!cond) failures++
}

// ── path 几何解析 ───────────────────────────────────────────
/** 三次贝塞尔取点(C 解析作为无害超集保留,防未来重引曲线时重踩采样坑) */
function cubicAt(p0, c1, c2, p1, t) {
  const u = 1 - t
  return [
    u * u * u * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * p1[0],
    u * u * u * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * p1[1],
  ]
}
const BEZIER_SUBDIV = 16  // 每段 C 采样数:8 段时弦偏差可达 ~2.8px(k=120),叠加渲染
                          // vs 模型 ±2px 锚差会把断言④顶到 7px 级;16 段弦偏差 ~0.7px 留足余量

/** d 字符串 → 点序(M/L 终点 + Q 控制点(终点由后继 L 覆盖,旧口径不动防扰动基线)
 *  + C 三次贝塞尔按 BEZIER_SUBDIV 采样展开;当前直角形态不用 C,解析能力保留) */
function parsePathD(d) {
  const pts = []
  const re = /([MLQC])([^MLQC]*)/g
  let m
  while ((m = re.exec(d))) {
    const nums = (m[2].match(/[\d.eE+-]+/g) ?? []).map(Number)
    if (m[1] === 'C') {
      // 三对坐标 = c1/c2/终点:采样展开成折线(标签贴合/交叉数吃真实曲线几何,
      // 控制点不入点序——控制多边形偏离曲线可达数十 px,会污染 <8px 断言)
      if (nums.length >= 6 && pts.length > 0) {
        const p0 = pts[pts.length - 1]
        for (let k = 1; k <= BEZIER_SUBDIV; k++) pts.push(cubicAt(p0, [nums[0], nums[1]], [nums[2], nums[3]], [nums[4], nums[5]], k / BEZIER_SUBDIV))
      }
      continue
    }
    for (let i = 0; i + 1 < nums.length; i += 2) pts.push([nums[i], nums[i + 1]])
  }
  return pts
}

/** 断言① 起笔水平:M 后第一条 L 的终点 dy=0;C 分支为向后兼容保留。 */
function pathStartHoriz(d) {
  const mM = d.match(/^M\s*([\d.eE+-]+)[ ,]+([\d.eE+-]+)/)
  if (!mM) return false
  const mNext = d.slice(mM[0].length).match(/^\s*([LC])\s*([\d.eE+-]+)[ ,]+([\d.eE+-]+)/)
  return !!mNext && Math.abs(Number(mNext[3]) - Number(mM[2])) < 0.01
}
/** 竖直段:[x, y0, y1](相邻点 x 相同) */
function vSegs(pts) {
  const segs = []
  for (let i = 1; i < pts.length; i++) {
    if (Math.abs(pts[i][0] - pts[i - 1][0]) < 0.01 && Math.abs(pts[i][1] - pts[i - 1][1]) > 1) {
      segs.push([pts[i][0], Math.min(pts[i][1], pts[i - 1][1]), Math.max(pts[i][1], pts[i - 1][1])])
    }
  }
  return segs
}
function rectsOverlap(a0, a1, b0, b1) { return Math.min(a1, b1) - Math.max(a0, b0) > 1 }

// ── P0 布局诊断器(施工单 20261110 §三.P0)──────────────────
// 四指标,全部 [diag] 前缀只读数,P0 阶段允许非零、不阻塞门禁:
//   ① label×label 碰撞对(常显标签 rect 两两相交,目标 0)
//   ② label×node 碰撞(标签 rect × 节点卡 rect,目标 0)
//   ③ edge×node 穿卡(边折线段 × 卡 rect;单列 zIndex=1 长线压卡子集及压住的
//     data-node-id 清单+是否压住文字区——长线穿行=11-10 拍板保留形态,读数供复议;
//     非长线边穿卡设计上应为 0,非零=新病灶)
//   ④ 跨列边非预期共线(竖缝 x 距<2px 且 y 区间相交的跨列边对;同源共据=合法共轨
//     豁免;同列折返边拥挤已由断言③ ≥12px 硬盖,此处只管跨列)
// 几何口径:与断言同源——parsePathD 解 d、getScreenCTM 转屏幕坐标后与
// getBoundingClientRect 同系可比;Q 圆角在 d 中仅存控制点(终点被后继 L 覆盖),
// 拐角近似偏差 ≤8px,诊断精度足够(勿为此改共享 parsePathD,会扰动断言③基线)。
const CARD_INSET = 2   // 卡 rect 内缩(px):Handle 锚在卡缘,贴边触点不算穿卡

/** 二维矩形相交(重叠 >1px 才算,吸掉发丝级贴边) */
function rectsHit2D(a, b) {
  return Math.min(a[2], b[2]) - Math.max(a[0], b[0]) > 1 && Math.min(a[3], b[3]) - Math.max(a[1], b[1]) > 1
}
/** 线段与矩形相交(Liang-Barsky 参数裁剪;rect=[left,top,right,bottom]) */
function segHitsRect(x1, y1, x2, y2, r) {
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

/** 一次抓齐场景原始数据(浏览器侧只取原始值,node 侧解析——$$eval 上下文隔离,实测坑) */
async function collectScene(page) {
  const edges = await page.$$eval('.react-flow__edge', els => els.map(el => {
    const p = el.querySelector('path.react-flow__edge-path')
    const ctm = p?.ownerSVGElement?.getScreenCTM()
    const markerAttr = p?.getAttribute('marker-end') ?? ''
    const markerId = markerAttr.replace(/^url\(['"]?#/, '').replace(/['"]?\)$/, '')
    const markerShape = markerId ? document.getElementById(markerId)?.querySelector('path,polyline,polygon') : null
    return {
      id: el.getAttribute('data-id'),
      z: el.closest('svg')?.style.zIndex || '',
      opacity: p?.style.opacity || '',
      stroke: p?.style.stroke || '',
      computedStroke: p ? getComputedStyle(p).stroke : '',
      markerAttr,
      markerId,
      markerExists: !!(markerId && document.getElementById(markerId)),
      markerStroke: markerShape ? getComputedStyle(markerShape).stroke : '',
      markerFill: markerShape ? getComputedStyle(markerShape).fill : '',
      d: p?.getAttribute('d') ?? '',
      ctm: ctm ? { a: ctm.a, b: ctm.b, c: ctm.c, d: ctm.d, e: ctm.e, f: ctm.f } : null,
    }
  }))
  const cards = await page.$$eval('.chain-card', els => els.map(el => {
    const r = el.getBoundingClientRect()
    // 文字区=卡内文本块行盒(标题/结论行/票型/脚注/徽章行),压住行盒≈压住文字
    const texts = [...el.querySelectorAll('.chain-card-title,.chain-verdict,.chain-count-row,.chain-foot,.chain-meta-row')]
      .map(t => { const b = t.getBoundingClientRect(); return [b.left, b.top, b.right, b.bottom] })
    return { id: el.getAttribute('data-node-id'), rect: [r.left, r.top, r.right, r.bottom], texts }
  }))
  const labels = await page.$$eval('.chain-rel-label', els => els.map(el => {
    const r = el.getBoundingClientRect()
    return { id: el.getAttribute('data-edge-id') ?? '', text: el.textContent ?? '', color: getComputedStyle(el).color, borderColor: getComputedStyle(el).borderColor, transform: el.style.transform, rect: [r.left, r.top, r.right, r.bottom] }
  }))
  return { edges, cards, labels }
}

/** 屏幕 DOM 高度折回 ReactFlow 布局坐标，用于回填保守卡高模型。 */
async function collectCardMetrics(page) {
  return page.$$eval('.chain-card', els => {
    const viewport = document.querySelector('.react-flow__viewport')
    const matrix = viewport ? new DOMMatrix(getComputedStyle(viewport).transform) : new DOMMatrix()
    const scale = Math.abs(matrix.a) || 1
    return {
      scale,
      cards: els.map(el => {
        const r = el.getBoundingClientRect()
        const laneClass = [...el.classList].find(x => x.startsWith('lane-')) ?? ''
        return { id: el.getAttribute('data-node-id') ?? '', lane: laneClass.replace(/^lane-/, ''), height: r.height / scale, rect: [r.left, r.top, r.right, r.bottom] }
      }),
    }
  })
}

/** payload → edgeId → {关系词, 源/目标泳道, 源/目标节点 id}(④判跨列+同源共据豁免;⑧ 扇区分类) */
function buildEdgeMeta(payload) {
  const laneOf = new Map(payload.nodes.map(n => [n.id, n.type]))
  return new Map(payload.edges.map(e => [e.id, { rel: e.relation_type, s: laneOf.get(e.source) ?? '', t: laneOf.get(e.target) ?? '', sid: e.source, tid: e.target }]))
}

/** 严格内点横穿(⑧ 口径:交点在两线段内部,共线/共点/端点接触不计) */
function segSegCross(p1, p2, p3, p4) {
  const d1x = p2[0] - p1[0], d1y = p2[1] - p1[1]
  const d2x = p4[0] - p3[0], d2y = p4[1] - p3[1]
  const denom = d1x * d2y - d1y * d2x
  if (denom === 0) return false
  const ex = p3[0] - p1[0], ey = p3[1] - p1[1]
  const t = (ex * d2y - ey * d2x) / denom
  const u = (ex * d1y - ey * d1x) / denom
  return t > 1e-9 && t < 1 - 1e-9 && u > 1e-9 && u < 1 - 1e-9
}

function diagCompute(raw, edgeMeta) {
  const toScreen = (x, y, m) => [m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f]
  const geoms = raw.edges
    .filter(e => !e.opacity || Number(e.opacity) > 0.05)
    .map(e => ({ id: e.id, z: e.z, pts: e.ctm ? parsePathD(e.d).map(([x, y]) => toScreen(x, y, e.ctm)) : [] }))
  const out = { nLabels: raw.labels.length, ll: [], ln: [], nn: [], en: [], col: [], cross: { '同源扇区': [], '同目标汇入': [], '长线参与': [], '其他': [] } }
  // 节点×节点：实际 DOM 卡矩形两两比较，不信任布局的估算高度。
  for (let i = 0; i < raw.cards.length; i++) {
    for (let j = i + 1; j < raw.cards.length; j++) {
      const a = raw.cards[i], b = raw.cards[j]
      const dx = Math.min(a.rect[2], b.rect[2]) - Math.max(a.rect[0], b.rect[0])
      const dy = Math.min(a.rect[3], b.rect[3]) - Math.max(a.rect[1], b.rect[1])
      if (dx > 1 && dy > 1) out.nn.push({ a: a.id, b: b.id, dx, dy, area: dx * dy })
    }
  }
  // ① label×label
  for (let i = 0; i < raw.labels.length; i++)
    for (let j = i + 1; j < raw.labels.length; j++)
      if (rectsHit2D(raw.labels[i].rect, raw.labels[j].rect)) out.ll.push([raw.labels[i].text, raw.labels[j].text])
  // ② label×node
  for (const lb of raw.labels)
    for (const c of raw.cards)
      if (rectsHit2D(lb.rect, c.rect)) out.ln.push([lb.id, lb.text, c.id])
  // ③ edge×node(命中某卡即记;记录段命中时顺查该卡文字区)
  for (const g of geoms) {
    const hitCards = []
    for (const c of raw.cards) {
      const r = [c.rect[0] + CARD_INSET, c.rect[1] + CARD_INSET, c.rect[2] - CARD_INSET, c.rect[3] - CARD_INSET]
      let hit = false, hitText = false
      for (let i = 1; i < g.pts.length; i++) {
        if (segHitsRect(g.pts[i - 1][0], g.pts[i - 1][1], g.pts[i][0], g.pts[i][1], r)) {
          hit = true
          hitText = c.texts.some(t => segHitsRect(g.pts[i - 1][0], g.pts[i - 1][1], g.pts[i][0], g.pts[i][1], t))
          break
        }
      }
      if (hit) hitCards.push(hitText ? `${c.id}(文字)` : c.id)
    }
    if (hitCards.length) out.en.push({ id: g.id, z: g.z, cards: hitCards })
  }
  // ④ 跨列边非预期共线(每边竖缝取 y 区间并集——Q 伪段按 x 合并,同断言③口径)
  const seams = geoms
    .filter(g => { const m = edgeMeta.get(g.id); return m && m.s && m.t && m.s !== m.t && m.rel !== '同源共据' })
    .map(g => {
      const byX = new Map()
      for (const [x, y0, y1] of vSegs(g.pts)) {
        const k = x.toFixed(1)
        const prev = byX.get(k)
        byX.set(k, prev ? [x, Math.min(prev[1], y0), Math.max(prev[2], y1)] : [x, y0, y1])
      }
      return { id: g.id, vs: [...byX.values()] }
    })
  for (let i = 0; i < seams.length; i++)
    for (let j = i + 1; j < seams.length; j++)
      for (const [xa, ya0, ya1] of seams[i].vs)
        for (const [xb, yb0, yb1] of seams[j].vs)
          if (Math.abs(xa - xb) < 2 && rectsOverlap(ya0, ya1, yb0, yb1))
            out.col.push({ a: seams[i].id, b: seams[j].id, x: (xa + xb) / 2, y: [Math.max(ya0, yb0), Math.min(ya1, yb1)] })
  // ⑧ 边×边横穿交点(P3-C 新增 [diag],探针算法入仓;先只测不治,残余读数拍板后再转正):
  //    方向判定=严格内点横穿,共线/共点不计;分类优先级 同源扇区>同目标汇入>长线参与>其他。
  for (let i = 0; i < geoms.length; i++) {
    for (let j = i + 1; j < geoms.length; j++) {
      const a = geoms[i], b = geoms[j]
      if (a.pts.length < 2 || b.pts.length < 2) continue
      let hits = 0
      for (let si = 1; si < a.pts.length; si++)
        for (let sj = 1; sj < b.pts.length; sj++)
          if (segSegCross(a.pts[si - 1], a.pts[si], b.pts[sj - 1], b.pts[sj])) hits++
      if (!hits) continue
      const ma = edgeMeta.get(a.id), mb = edgeMeta.get(b.id)
      let cat = '其他'
      if (ma && mb && ma.sid === mb.sid) cat = '同源扇区'
      else if (ma && mb && ma.tid === mb.tid) cat = '同目标汇入'
      else if (a.z === '1' || b.z === '1') cat = '长线参与'
      out.cross[cat].push([a.id, b.id, hits])
    }
  }
  return out
}

function printDiag(scene, out) {
  const lh = out.en.filter(e => e.z === '1')
  const normal = out.en.filter(e => e.z !== '1')
  const textHits = lh.filter(e => e.cards.some(c => c.endsWith('(文字)')))
  console.log(`[diag] ${scene} ─ P0 四指标(P0 只读数,非零不阻塞门禁)`)
  console.log(`  ① label×label 碰撞对 ${out.ll.length} / 常显标签 ${out.nLabels} 个(目标 0)`)
  for (const [a, b] of out.ll.slice(0, 20)) console.log(`     - 「${a}」×「${b}」`)
  if (out.ll.length > 20) console.log(`     - …共 ${out.ll.length} 对`)
  console.log(`  ② label×node 碰撞 ${out.ln.length}(目标 0)`)
  for (const [eid, t, id] of out.ln.slice(0, 20)) console.log(`     - ${eid} 标签「${t}」× 卡 ${id}`)
  if (out.ln.length > 20) console.log(`     - …共 ${out.ln.length} 处`)
  console.log(`  ⑤ node×node 碰撞对 ${out.nn.length}(重叠面积 >1px 即失败)`)
  for (const c of out.nn.slice(0, 20)) console.log(`     - ${c.a} × ${c.b}: x侵入 ${c.dx.toFixed(1)}px / y侵入 ${c.dy.toFixed(1)}px / 面积 ${c.area.toFixed(1)}px²`)
  if (out.nn.length > 20) console.log(`     - …共 ${out.nn.length} 对`)
  console.log(`  ③ edge×node 穿卡 ${out.en.length} 条(P3-A 后全量应 0;长线子集 ${lh.length} / 非长线 ${normal.length});其中压住文字区 ${textHits.length}(监督读数,恒 0)`)
  for (const e of out.en.slice(0, 30)) console.log(`     - ${e.id}${e.z === '1' ? ' [长线]' : ' [!非长线穿卡]'} → ${e.cards.join(', ')}`)
  if (out.en.length > 30) console.log(`     - …共 ${out.en.length} 条`)
  console.log(`  ④ 跨列边非预期共线对 ${out.col.length}(竖缝 x 距<2px 且 y 区间相交;同源共据豁免)`)
  for (const c of out.col.slice(0, 20)) console.log(`     - ${c.a} × ${c.b} @x=${c.x.toFixed(0)} y重叠[${c.y[0].toFixed(0)},${c.y[1].toFixed(0)}]`)
  if (out.col.length > 20) console.log(`     - …共 ${out.col.length} 对`)
  // ⑧ 交叉数(P3-C [diag] 只读;严格内点横穿,共线/共点不计)
  const xTotal = Object.values(out.cross).reduce((a, l) => a + l.length, 0)
  let xPts = 0
  for (const l of Object.values(out.cross)) for (const [, , h] of l) xPts += h
  console.log(`  ⑧ 边×边横穿 ${xPts} 点(${xTotal} 对)= 同源扇区 ${out.cross['同源扇区'].length} + 同目标汇入 ${out.cross['同目标汇入'].length} + 长线参与 ${out.cross['长线参与'].length} + 其他 ${out.cross['其他'].length}`)
  for (const [cat, list] of Object.entries(out.cross))
    for (const [a, b, h] of list.slice(0, 8)) console.log(`     - [${cat}] ${a} × ${b} ×${h}`)
  return { ll: out.ll.length, ln: out.ln.length, nn: out.nn.length, enLong: lh.length, enNormal: normal.length, enText: textHits.length, col: out.col.length, xPts, xPairs: xTotal, xFan: out.cross['同源扇区'].length }
}

/** P3-A 治理后转正(施工单 20261110 §五):edge×node 穿卡全量 = 0(长线走通道+行间
 * 跳越、非长线接端口后都不碰卡);①②④ 沿用 P2b 转正;压文字子集随全量归零恒 0。 */
function assertDiag(scene, out) {
  ok(out.ll.length === 0, `${scene} ① label×label 碰撞 = 0(转正)`)
  ok(out.ln.length === 0, `${scene} ② label×node 碰撞 = 0(转正)`)
  ok(out.nn.length === 0, `${scene} ⑤ node×node 碰撞 = 0(新增硬门禁)`)
  ok(out.col.length === 0, `${scene} ④ 跨列边非预期共线对 = 0(转正)`)
  ok(out.en.length === 0, `${scene} ③ edge×node 穿卡全量 = 0(P3-A 转正,长线+非长线一并)`)
}

/** 交叉区别化读数/门禁:只有跨列度数 ≥3 的同源/同目标组可出现 color-mix;
 * 每组除 0% 首档外至少 n-1 条为混色档,最浅不超过施工拍板 45%。 */
function assertMixSteps(scene, raw, edgeMeta) {
  const visible = new Set(raw.edges.map(e => e.id))
  const bySource = new Map(), byTarget = new Map()
  for (const id of visible) {
    const m = edgeMeta.get(id)
    if (!m || m.s === m.t) continue
    bySource.set(m.sid, [...(bySource.get(m.sid) ?? []), id])
    byTarget.set(m.tid, [...(byTarget.get(m.tid) ?? []), id])
  }
  const groups = [...bySource.values(), ...byTarget.values()].filter(g => g.length >= 3)
  const qualified = new Set(groups.flat())
  const mixed = raw.edges.filter(e => e.stroke.includes('color-mix'))
  const mixedIds = new Set(mixed.map(e => e.id))
  const pcts = mixed.map(e => {
    const m = e.stroke.match(/\s([\d.]+)%,\s*white/i)
    return m ? 100 - Number(m[1]) : NaN
  }).filter(Number.isFinite)
  const distinct = [...new Set(pcts.map(p => p.toFixed(2)))]
  const byId = new Map(raw.edges.map(e => [e.id, e]))
  const markerSynced = mixed.every(e => e.markerStroke === e.computedStroke || e.markerFill === e.computedStroke)
  const labelSynced = raw.labels.every(lb => {
    const e = byId.get(lb.id)
    return !e || (lb.color === e.computedStroke && lb.borderColor === e.computedStroke)
  })
  console.log(`  [mix] ${scene}:分组 ${groups.length},覆盖边 ${qualified.size},混色边 ${mixed.length},非零档 ${distinct.length}`)
  ok(mixed.every(e => qualified.has(e.id)), `${scene} 混色只施加于同源/同目标度数≥3组`)
  ok(groups.every(g => g.filter(id => mixedIds.has(id)).length >= g.length - 1), `${scene} 混色组档数与组规模一致(除 0% 首档)`)
  ok(pcts.length === mixed.length && pcts.every(p => p > 0 && p <= 45.001), `${scene} 混白步进范围 (0,45%]`)
  ok(markerSynced, `${scene} 箭头颜色与线色同步`)
  if (!markerSynced) console.log('  [mix] 箭头不同步样本:', JSON.stringify(mixed.filter(e => e.markerStroke !== e.computedStroke && e.markerFill !== e.computedStroke).slice(0, 3)))
  ok(labelSynced, `${scene} 可见标签颜色/边框与线色同步`)
}

async function assertAll(page, { expectedEdges, labelPrefix }) {
  // 1 起笔水平率 = 100%(统一直角折线:M 后首命令 L 的终点 dy=0;
  //   d 解析全在 node 侧——$$eval 浏览器上下文不可见 node 函数)
  const ds = await page.$$eval('.react-flow__edge path.react-flow__edge-path', els => els.map(p => p.getAttribute('d')))
  let horiz = 0
  for (const d of ds) if (pathStartHoriz(d)) horiz++
  ok(horiz === ds.length && ds.length > 0, `${labelPrefix} 起笔切线水平率 ${horiz}/${ds.length} = 100%`)
  // 2 边可见数 = 预期;长线 zIndex 压卡抽样(xyflow v12 每边独立 svg,zIndex 写在 svg 上)
  const visibles = await page.$$eval('.react-flow__edge path.react-flow__edge-path', els =>
    els.filter(p => { const o = p.style.opacity; return !o || Number(o) > 0.05 }).length)
  if (typeof expectedEdges === 'number') ok(visibles === expectedEdges, `${labelPrefix} 边可见数 ${visibles} = 预期 ${expectedEdges}`)
  else ok(visibles > 0, `${labelPrefix} 边可见数 ${visibles} > 0`)
  const zTops = await page.$$eval('.react-flow__edge', els => els.filter(e => (e.closest('svg')?.style.zIndex || '') === '1').length)
  ok(zTops > 0, `${labelPrefix} 长线压卡 zIndex=1 边数 ${zTops} > 0`)
  // 3 折返边竖缝不挤:折返边 = 同列边(path 终点 x ≤ 起点 x;P2a 后折返边接目标卡
  //   右侧 Handle,终点 x == 起点 x,故只豁免「终点严格在起点右侧」;相邻列短边
  //   midX 天然贴近源侧,不能按出段长度判)。Q 圆角伪段按 x 合并,
  //   每边每竖缝取 y 区间并集;任意两边竖缝 x 距 <12px 且 y 区间相交 = 违规
  const turns = []
  for (const d of ds) {
    const pts = parsePathD(d)
    if (pts.length < 4 || pts[pts.length - 1][0] > pts[0][0] + 0.5) continue
    const byX = new Map()
    for (const [x, y0, y1] of vSegs(pts)) {
      const k = x.toFixed(1)
      const prev = byX.get(k)
      byX.set(k, prev ? [x, Math.min(prev[1], y0), Math.max(prev[2], y1)] : [x, y0, y1])
    }
    for (const v of byX.values()) turns.push(v)
  }
  let crowd = 0
  for (let i = 0; i < turns.length; i++) {
    for (let j = i + 1; j < turns.length; j++) {
      const [xa, ya0, ya1] = turns[i]; const [xb, yb0, yb1] = turns[j]
      if (Math.abs(xa - xb) < 12 && rectsOverlap(ya0, ya1, yb0, yb1)) crowd++
    }
  }
  ok(crowd === 0, `${labelPrefix} 折返边竖缝拥挤对 ${crowd} = 0(共 ${turns.length} 条折返竖缝)`)
  // 4' 标签末尾锚定:标签用 data-edge-id 精确归属(同目标端口仅隔 13px时,dy=-12 后
  //    纯最近边会误认成上一端口);在 flow 坐标验收拍板值,避免 fitView 缩放把 -12
  //    图元放大成屏幕 -24px。中心须落 [endX−70,endX] × [endY−22,endY−6] 上方带。
  const labels = await page.$$eval('.chain-rel-label', els => els.map(el => {
    const r = el.getBoundingClientRect()
    return { id: el.getAttribute('data-edge-id') ?? '', transform: el.style.transform, x: r.left + r.width / 2, y: r.top + r.height / 2 }
  }))
  const linePts = await page.$$eval('.react-flow__edge path.react-flow__edge-path', els => els.map(p => {
    const ctm = p.ownerSVGElement.getScreenCTM()
    return { id: p.closest('.react-flow__edge')?.getAttribute('data-id') ?? '', d: p.getAttribute('d'), ctm: ctm ? { a: ctm.a, b: ctm.b, c: ctm.c, d: ctm.d, e: ctm.e, f: ctm.f } : null }
  }))
  const polys = linePts.map(({ id, d, ctm }) => ({ id, ctm, flowPts: parsePathD(d) }))
  const fromScreen = (x, y, m) => {
    const det = m.a * m.d - m.b * m.c
    return det ? [(m.d * (x - m.e) - m.c * (y - m.f)) / det, (-m.b * (x - m.e) + m.a * (y - m.f)) / det] : [NaN, NaN]
  }
  let anchored = 0
  const misses = []
  for (const lb of labels) {
    const owner = polys.find(p => p.id === lb.id)
    const end = owner?.flowPts.at(-1)
    const center = owner?.ctm ? fromScreen(lb.x, lb.y, owner.ctm) : null
    if (end && center && center[0] >= end[0] - 70 - 0.5 && center[0] <= end[0] + 0.5 &&
      center[1] >= end[1] - 22 - 0.5 && center[1] <= end[1] - 6 + 0.5) anchored++
    else misses.push({ lb, end, center })
  }
  ok(labels.length > 0 && anchored === labels.length, `${labelPrefix} 标签末尾锚定 ${anchored}/${labels.length} = 100%`)
  if (misses.length) console.log('  ④\' 未锚定样本:', JSON.stringify(misses.slice(0, 5)))
  // 6 端口分配(P2a):同节点同侧 Handle 锚点 y 互异且相邻距 ≥12px(溢出扇出拍板:
  //   高入出度节点 13px 均匀扇出;Handle DOM 实测中心, xyflow 自动写 data-handleid)
  const handles = await page.$$eval('.react-flow__handle', els => els.map(el => {
    const r = el.getBoundingClientRect()
    return { id: el.getAttribute('data-handleid') ?? '', node: el.closest('.chain-card')?.getAttribute('data-node-id') ?? '', y: r.top + r.height / 2 }
  }))
  const bySide = new Map()
  for (const h of handles) {
    const side = h.id.startsWith('out:') ? 'out' : h.id.startsWith('rin:') ? 'rin' : 'in'
    const k = `${h.node}|${side}`
    if (!bySide.has(k)) bySide.set(k, [])
    bySide.get(k).push(h.y)
  }
  let badGap = 0
  for (const ys of bySide.values()) {
    ys.sort((a, b) => a - b)
    for (let i = 1; i < ys.length; i++) if (ys[i] - ys[i - 1] < 11.9) badGap++
  }
  ok(badGap === 0, `${labelPrefix} 端口锚点相邻距 ≥12px:违例 ${badGap} 处(共 ${handles.length} 个 Handle)`)
}

/** 遍历当前可见节点：每次点击必须只打开局部抽屉，主图卡数不得归零。 */
async function assertAllNodeDetails(page, label, requiredSuffixes = []) {
  const seen = new Set()
  let minCards = Number.POSITIVE_INFINITY
  for (let round = 0; round < 3; round++) {
    const ids = await page.$$eval('.chain-card', els => els.map(el => el.getAttribute('data-node-id')).filter(Boolean))
    const fresh = ids.filter(id => !seen.has(id))
    if (!fresh.length) break
    for (const id of fresh) {
      seen.add(id)
      await page.$$eval('.chain-card', (els, target) => {
        const el = els.find(x => x.getAttribute('data-node-id') === target)
        el?.dispatchEvent(new MouseEvent('click', { bubbles: true }))
      }, id)
      await sleep(20)
      const cards = await page.$$eval('.chain-card', els => els.length)
      minCards = Math.min(minCards, cards)
      const drawer = await page.$('.chain-drawer')
      if (!drawer) { ok(false, `${label} 节点 ${id} 未打开详情/显式说明`); continue }
      await page.$eval('.drawer-close', el => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
      await sleep(10)
    }
  }
  ok(minCards > 0 && Number.isFinite(minCards), `${label} 逐节点点击主图卡片最少 ${minCards} > 0`)
  for (const suffix of requiredSuffixes) ok([...seen].some(id => id.endsWith(`:${suffix}`)), `${label} 专项节点 ${suffix} 已遍历`)
  return seen
}

// ── 主流程 ──────────────────────────────────────────────────
// 直接 spawn vite 的 js 入口(绕开 npm/cmd shell,Windows bash 环境取不到 cmd.exe)
const FRONTEND_ROOT = fileURLToPath(new URL('..', import.meta.url))
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--port', String(PORT), '--strictPort', '--host', '127.0.0.1', '--configLoader', 'runner'], {
  cwd: FRONTEND_ROOT,
  stdio: 'pipe',
})
server.on('error', err => { console.error('FAIL  dev server 启动失败:', err.message); process.exit(1) })
let serverOut = ''
server.stdout.on('data', c => { serverOut += c })
server.stderr.on('data', c => { serverOut += c })
try {
  let up = false
  for (let i = 0; i < 120 && !up; i++) {
    await sleep(500)
    up = await fetch(`${BASE}/chain.html`).then(r => r.ok).catch(() => false)
  }
  if (!up) { console.error('FAIL  dev server 未就绪\n' + serverOut.slice(-2000)); process.exit(1) }
  console.log(`dev server 就绪 ${BASE}`)

  const browser = await chromium.launch({ channel: 'msedge', headless: true })
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } })
  const pageErrors = []
  page.on('pageerror', err => { pageErrors.push(err.message); console.error('[browser pageerror]', err.message) })
  page.on('console', msg => { if (msg.type() === 'error') console.error('[browser console]', msg.text()) })
  const diagLog = []

  // demo 场 payload:诊断 meta(边→泳道/关系词)与 hover 步数据源共用,取一次
  const payload = await fetch(`${BASE}/demo.web.json`).then(r => r.json())
  const demoMeta = buildEdgeMeta(payload)

  // 梯度0直查场：无 ?src= 进入即自动加载最新本地出件；清单与下拉同源，切换后事件号变化。
  await page.goto(`${BASE}/chain.html`)
  await page.waitForSelector('.react-flow__node', { timeout: 30000 })
  const directNodeCount = await page.locator('.react-flow__node').count()
  ok(directNodeCount > 0, `直查 无 ?src= 自动渲染节点 ${directNodeCount}>0`)
  const runSelect = page.getByLabel('本地出件')
  ok(await runSelect.count() === 1, '直查 run 下拉存在')
  const optionCount = await runSelect.locator('option').count()
  ok(optionCount === directRunsIndex.runs.length, `直查 run 下拉条目 ${optionCount}/${directRunsIndex.runs.length}`)
  const beforeEventId = (await page.locator('.chain-toolbar-id > strong').textContent()) ?? ''
  const switchTarget = directRunsIndex.runs.find(run => run.eventId !== beforeEventId)
  if (switchTarget) {
    await runSelect.selectOption(switchTarget.file)
    await page.waitForFunction(eventId => document.querySelector('.chain-toolbar-id > strong')?.textContent === eventId, switchTarget.eventId)
    const afterEventId = (await page.locator('.chain-toolbar-id > strong').textContent()) ?? ''
    ok(afterEventId !== beforeEventId, `直查切换 event_id ${beforeEventId} → ${afterEventId}`)
  } else {
    ok(false, '直查切换缺少不同 event_id 的目标出件')
  }

  // 回落态留图但不计门禁：路由层模拟 index 404，不删除/改名用户的本地缓存。
  const fallbackPage = await browser.newPage({ viewport: { width: 1600, height: 1000 } })
  await fallbackPage.route('**/runs/index.json', route => route.fulfill({ status: 404, body: '' }))
  await fallbackPage.goto(`${BASE}/chain.html`)
  await fallbackPage.waitForSelector('.chain-splash')
  await fallbackPage.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_local_runs_fallback.png', import.meta.url)), fullPage: true })
  await fallbackPage.close()

  // demo 基线:深链 ?src=./demo.web.json(同源 fetch,顺带回归深链通道)
  await page.goto(`${BASE}/chain.html?src=./demo.web.json`)
  await page.waitForSelector('.react-flow__edge', { timeout: 30000 })
  // 旧 demo 继续使用「全量节点」几何基线;新语义件的默认态在下文
  // 忻州场景单独验收(默认隐藏折叠件+高扇出汇流)。
  await page.locator('label.chain-check', { hasText: '显示折叠件' }).locator('input').check()
  await sleep(800)   // fitView 动画落定
  await assertAll(page, { expectedEdges: 40, labelPrefix: 'demo' })
  const demoRaw = await collectScene(page)
  await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_lines_unified_demo.png', import.meta.url)), fullPage: true })
  const demoDiag = diagCompute(demoRaw, demoMeta)
  assertMixSteps('demo', demoRaw, demoMeta)
  diagLog.push({ scene: 'demo', n: printDiag('demo', demoDiag) })
  assertDiag('demo', demoDiag)

  // 分道大样:开启「同源边」开关,ev→ev 同列折返边 ×4 入场(连前提 ×1 = 5 条折返边,
  // 分道表 4 道全启用)——这是 F2 的真考验场
  await page.locator('label.chain-check', { hasText: '同源边' }).locator('input').check()
  await sleep(500)
  await assertAll(page, { expectedEdges: 44, labelPrefix: 'demo+同源' })
  const demoSRRaw = await collectScene(page)
  await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_lines_unified_demo_same_root.png', import.meta.url)), fullPage: true })
  const demoSR = diagCompute(demoSRRaw, demoMeta)
  assertMixSteps('demo+同源', demoSRRaw, demoMeta)
  diagLog.push({ scene: 'demo+同源', n: printDiag('demo+同源', demoSR) })
  assertDiag('demo+同源', demoSR)
  await page.locator('label.chain-check', { hasText: '同源边' }).locator('input').uncheck()
  await sleep(400)

  // 5 hover 邻域:取一条「印证」边(弱语义,邻域/加粗/标签联动全覆盖)。
  // 触发用派发 mouseover(React 合成 onMouseEnter=委托 over/out 模拟)——不用真实
  // mouse.move:长线 interaction 捕获带宽 20px 且 z=1 压节点,几何落点会被抢(实测坑)
  const hoverEdge = payload.edges.find(e => e.relation_type === '印证') ?? payload.edges[0]
  const before = await page.$eval(`.react-flow__edge[data-id="${hoverEdge.id}"] path.react-flow__edge-path`, p => ({
    w: Number(p.style.strokeWidth || 0), o: p.style.opacity || '',
  }))
  await page.$eval(`.react-flow__edge[data-id="${hoverEdge.id}"]`, el => {
    const t = el.querySelector('path.react-flow__edge-interaction') ?? el.querySelector('path.react-flow__edge-path')
    t.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
  })
  await sleep(300)
  const nbr = new Set(payload.edges.filter(e => e.id !== hoverEdge.id && (e.source === hoverEdge.source || e.target === hoverEdge.target || e.source === hoverEdge.target || e.target === hoverEdge.source)).map(e => e.id))
  const after = await page.$$eval('.react-flow__edge path.react-flow__edge-path', (els, hv) =>
    els.map(p => ({ id: p.closest('.react-flow__edge').getAttribute('data-id'), w: Number(p.style.strokeWidth || 0), o: p.style.opacity || '' })), hoverEdge.id)
  let nbrOk = 0, nbrBad = 0, otherBad = 0
  for (const e of after) {
    if (e.id === hoverEdge.id) continue
    const prev = before
    if (nbr.has(e.id)) { (e.o === '0.45' ? nbrOk++ : nbrBad++) }
    else if (e.o && e.o !== prev.o && e.o !== '0.55' && e.o !== '0.4') otherBad++   // 无关边只许保持原样(longHaul 0.55 / dim 0.4)
  }
  ok(nbrBad === 0, `hover 邻边 opacity=0.45:不符 ${nbrBad} 处`)
  ok(otherBad === 0, `hover 无关边保持原样:被动过的 ${otherBad} 处`)
  const hoverPath = after.find(e => e.id === hoverEdge.id)
  ok(hoverPath.o === '1', `hover 边全色 opacity=${hoverPath.o}`)
  ok(Math.abs(hoverPath.w - (before.w + 0.8)) < 0.05, `hover 边加粗 ${before.w}→${hoverPath.w}(+0.8)`)
  const labelShown = await page.$$eval('.chain-rel-label', els => els.length)
  ok(labelShown >= 4, `hover 后标签浮现(邻边标签联动)共 ${labelShown} 个`)
  // 移出恢复:对称 dispatch mouseout(真实 mouse.move 不产生对 dispatch 进入元素的
  // mouseout——浏览器层面指针从未在其上,实测坑)
  await page.$eval(`.react-flow__edge[data-id="${hoverEdge.id}"]`, el => {
    const t = el.querySelector('path.react-flow__edge-interaction') ?? el.querySelector('path.react-flow__edge-path')
    t.dispatchEvent(new MouseEvent('mouseout', { bubbles: true }))
  })
  await sleep(300)
  const restored = await page.$eval(`.react-flow__edge[data-id="${hoverEdge.id}"] path.react-flow__edge-path`, p => Number(p.style.strokeWidth || 0))
  ok(Math.abs(restored - before.w) < 0.05, `移出恢复 strokeWidth ${restored} = ${before.w}`)

  // 忻州 r1 基线(可选):隐藏 file input 上传——长链形态 + 分道大样
  if (XINZHOU) {
    await page.setInputFiles('input[type="file"]', XINZHOU)
    await page.locator('label.chain-check', { hasText: '显示折叠件' }).locator('input').uncheck()
    await page.waitForSelector('.react-flow__edge', { timeout: 30000 })
    await sleep(800)
    await assertAll(page, { expectedEdges: null, labelPrefix: '忻州语义r1·默认汇流' })
    const xzPayload = JSON.parse(readFileSync(XINZHOU, 'utf8'))
    const xzMeta = buildEdgeMeta(xzPayload)
    const xzRaw = await collectScene(page)
    const cardMetrics = await collectCardMetrics(page)
    const maxByLane = Object.fromEntries(['source', 'fact', 'verification', 'reasoning', 'conclusion'].map(lane => [lane, Math.max(0, ...cardMetrics.cards.filter(c => c.lane === lane).map(c => c.height))]))
    const c14m = cardMetrics.cards.find(c => c.id.endsWith(':c14'))
    const c19m = cardMetrics.cards.find(c => c.id.endsWith(':c19'))
    const c14c19Y = c14m && c19m ? Math.max(0, Math.min(c14m.rect[3], c19m.rect[3]) - Math.max(c14m.rect[1], c19m.rect[1])) : NaN
    console.log(`[measure] 忻州 fit scale=${cardMetrics.scale.toFixed(4)}; 布局坐标最大卡高 source=${maxByLane.source.toFixed(1)} fact=${maxByLane.fact.toFixed(1)} verification=${maxByLane.verification.toFixed(1)} reasoning=${maxByLane.reasoning.toFixed(1)} conclusion=${maxByLane.conclusion.toFixed(1)}px`)
    if (c14m && c19m) console.log(`[measure] c14=${c14m.height.toFixed(1)}px c19=${c19m.height.toFixed(1)}px 屏幕 y 重叠=${c14c19Y.toFixed(1)}px`)
    ok(maxByLane.fact <= 196.5, `忻州 事实卡实高 ${maxByLane.fact.toFixed(1)}px ≤ CARD_H.fact 196px`)
    ok(maxByLane.conclusion <= 176.5, `忻州 结论卡实高 ${maxByLane.conclusion.toFixed(1)}px ≤ CARD_H.conclusion 176px`)
    ok(!Number.isFinite(c14c19Y) || c14c19Y <= 1, `忻州 c14/c19 屏幕 y 重叠 ${Number.isFinite(c14c19Y) ? c14c19Y.toFixed(1) : 'n/a'}px = 0`)
    const defaultEdgeIds = await page.$$eval('.react-flow__edge', els => els.map(e => e.getAttribute('data-id')).sort())
    const clusterCards = await page.$$eval('.chain-card.is-fact-cluster', els => els.map(e => e.getAttribute('data-edge-group-id')))
    ok(clusterCards.length === (xzPayload.edge_groups?.length ?? 0), `忻州 默认汇流事实簇 ${clusterCards.length}/${xzPayload.edge_groups?.length ?? 0}`)
    const aggIds = new Set((xzPayload.edge_groups ?? []).map(g => `agg:${g.id}`))
    const domIds = new Set(defaultEdgeIds)
    ok([...aggIds].every(id => domIds.has(id)), `忻州 默认聚合边 ${[...aggIds].filter(id => domIds.has(id)).length}/${aggIds.size}`)
    const rawGroupIds = new Set((xzPayload.edge_groups ?? []).flatMap(g => g.raw_edge_ids))
    ok([...rawGroupIds].every(id => !domIds.has(id)), `忻州 默认原始成员边已收起 ${rawGroupIds.size}/${rawGroupIds.size}`)
    const badgeSync = await page.$$eval('.chain-card[data-node-id^="cl:"]', (els, nodes) => els.every(el => {
      const n = nodes.find(x => x.id === el.getAttribute('data-node-id'))
      return !n || (el.getAttribute('data-evidence-state') ?? '') === (n.evidence_state ?? '')
    }), xzPayload.nodes)
    ok(badgeSync, '忻州 evidence_state 徽章与 JSON 同步')
    const incomingIds = new Set(xzPayload.edges.map(e => e.target))
    const outgoingIds = new Set(xzPayload.edges.map(e => e.source))
    const orphanVisible = xzPayload.nodes.filter(n =>
      (n.type === 'verification' && !n.intentional_orphan && !incomingIds.has(n.id) && !outgoingIds.has(n.id)) ||
      (n.type === 'reasoning' && (!incomingIds.has(n.id) || !outgoingIds.has(n.id))))
    ok(orphanVisible.length === 0, `忻州 verification/reasoning 非预期孤立数 ${orphanVisible.length}=0`)
    const inferenceComplete = xzPayload.nodes.filter(n => n.sub_type === '结构化推理').every(n => {
      const ins = xzPayload.edges.filter(e => e.target === n.id).flatMap(e => e.claim_ids ?? [])
      const outs = new Set(xzPayload.edges.filter(e => e.source === n.id).map(e => e.target.replace(/^hy:/, '')))
      return (n.basis_claim_ids ?? []).every(id => ins.includes(id)) && (n.target_hypothesis_ids ?? []).every(id => outs.has(id))
    })
    ok(inferenceComplete, '忻州 推理 basis 入边与目标 conclusion 出边完整')
    await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_semantic_xinzhou_default.png', import.meta.url)), fullPage: true })

    // 详情稳定性：动态遍历（汇流簇点开后新增原命题也继续遍历）。
    const errorsBeforeClicks = pageErrors.length
    await assertAllNodeDetails(page, '忻州语义r1', ['c14', 'c19', 'c20', 'c21', 'c23'])
    ok(pageErrors.length === errorsBeforeClicks, `忻州 逐节点点击 pageerror=0(新增 ${pageErrors.length - errorsBeforeClicks})`)
    const collapseButton = page.getByRole('button', { name: /收起汇流/ })
    if (await collapseButton.count()) { await collapseButton.click(); await sleep(300) }

    // 问题节点和结论抽屉留图，同时锁定 gap 结构化展示。
    await page.$eval('.chain-card[data-node-id$=":c20"]', el => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await page.waitForSelector('.drawer-gap')
    await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_semantic_xinzhou_gap_c20.png', import.meta.url)), fullPage: true })
    await page.$eval('.drawer-close', el => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await page.$eval('.chain-card[data-node-id$=":h3"]', el => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    await page.waitForSelector('.drawer-next-steps')
    await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_semantic_xinzhou_conclusion_h3.png', import.meta.url)), fullPage: true })
    await page.$eval('.drawer-close', el => el.dispatchEvent(new MouseEvent('click', { bubbles: true })))

    // 汇流展开:一键恢复该组全部原命题+原边；收起后边 id 集逐项一致。
    const firstGroup = xzPayload.edge_groups?.[0]
    if (firstGroup) {
      await page.locator(`.chain-card[data-edge-group-id="${firstGroup.id}"]`).click()
      await page.locator('.drawer-close').click().catch(() => {})
      await page.waitForSelector(`.react-flow__edge[data-id="${firstGroup.raw_edge_ids[0]}"]`, { timeout: 30000 })
      await sleep(500)
      const expandedIds = new Set(await page.$$eval('.react-flow__edge', els => els.map(e => e.getAttribute('data-id'))))
      ok(firstGroup.raw_edge_ids.every(id => expandedIds.has(id)), `忻州 展开态原边 ${firstGroup.raw_edge_ids.filter(id => expandedIds.has(id)).length}/${firstGroup.raw_edge_ids.length}`)
      ok(!expandedIds.has(`agg:${firstGroup.id}`), '忻州 展开态聚合边退场')
      await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_semantic_xinzhou_expanded.png', import.meta.url)), fullPage: true })
      await page.getByRole('button', { name: /收起汇流/ }).click()
      await sleep(500)
      const collapsedAgain = await page.$$eval('.react-flow__edge', els => els.map(e => e.getAttribute('data-id')).sort())
      ok(JSON.stringify(collapsedAgain) === JSON.stringify(defaultEdgeIds), '忻州 收起→展开→收起边集确定一致')
    }

    // 待补证筛选:所有可见事实卡都必须是反证/待裁/无直接证据。
    await page.locator('label.chain-check', { hasText: '只看待补证' }).locator('input').check()
    await sleep(500)
    const pendingStates = await page.$$eval('.chain-card.lane-fact', els => els.map(el => el.getAttribute('data-evidence-state')).filter(Boolean))
    ok(pendingStates.length > 0 && pendingStates.every(s => s !== 'supported'), `忻州 只看待补证 ${pendingStates.length} 卡无 supported 混入`)
    await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_semantic_xinzhou_needs_evidence.png', import.meta.url)), fullPage: true })
    await page.locator('label.chain-check', { hasText: '只看待补证' }).locator('input').uncheck()
    await sleep(400)

    // 推理桥全景:仅保留推理+结论泳道拍照，不参与几何门禁读数。
    for (const lane of ['来源', '事实', '验证']) await page.getByRole('button', { name: lane, exact: true }).click()
    await sleep(400)
    await page.locator('.react-flow__controls-fitview').click().catch(() => {})
    await sleep(400)
    const inferenceCards = await page.$$eval('.chain-card.lane-reasoning', els => els.filter(e => e.textContent?.includes('结构化推理')).length)
    ok(inferenceCards >= 3, `忻州 结构化推理卡 ${inferenceCards} ≥ 3`)
    await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_semantic_xinzhou_reasoning.png', import.meta.url)), fullPage: true })
    for (const lane of ['来源', '事实', '验证']) await page.getByRole('button', { name: lane, exact: true }).click()
    await sleep(500)
    // h2 入度 9 专项目检:只为截图平移 ReactFlow viewport,不参与前面抓取的门禁数据。
    const visibleNow = new Set(await page.$$eval('.react-flow__edge', els => els.map(e => e.getAttribute('data-id'))))
    const h2Edges = xzPayload.edges.filter(e => e.target.endsWith(':h2') && visibleNow.has(e.id))
    const h2Edge = h2Edges[0]
    if (h2Edge) {
      await page.$eval(`.react-flow__edge[data-id="${h2Edge.id}"]`, el => {
        const t = el.querySelector('path.react-flow__edge-interaction') ?? el.querySelector('path.react-flow__edge-path')
        t.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }))
      })
      await sleep(150)
      const h2Ids = h2Edges.map(e => e.id)
      const h2Labels = await page.$$eval('.chain-rel-label', (els, ids) => els
        .filter(el => ids.includes(el.getAttribute('data-edge-id') ?? ''))
        .map(el => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom] }), h2Ids)
      let h2Hits = 0
      for (let i = 0; i < h2Labels.length; i++)
        for (let j = i + 1; j < h2Labels.length; j++) if (rectsHit2D(h2Labels[i], h2Labels[j])) h2Hits++
      ok(h2Labels.length === h2Ids.length && h2Labels.length > 0 && h2Hits === 0, `忻州 h2 hover 汇入标签 ${h2Labels.length}/${h2Ids.length},相互碰撞 ${h2Hits}=0`)
    }
    await page.evaluate(() => {
      const node = [...document.querySelectorAll('.react-flow__node')].find(el => el.querySelector('.chain-card[data-node-id$=":h2"]'))
      const viewport = document.querySelector('.react-flow__viewport')
      const canvas = document.querySelector('.chain-canvas .react-flow')
      if (!node || !viewport || !canvas) return
      const nm = new DOMMatrix(getComputedStyle(node).transform)
      const cr = canvas.getBoundingClientRect()
      const zoom = 1
      viewport.style.transform = `translate(${cr.width * 0.78 - nm.e * zoom}px, ${cr.height * 0.5 - nm.f * zoom}px) scale(${zoom})`
    })
    await sleep(150)
    await page.screenshot({ path: fileURLToPath(new URL('../../agent/runs/chain_lines_unified_xinzhou_h2.png', import.meta.url)), fullPage: true })
    const xzDiag = diagCompute(xzRaw, xzMeta)
    assertMixSteps('忻州r1', xzRaw, xzMeta)
    diagLog.push({ scene: '忻州r1', n: printDiag('忻州r1', xzDiag) })
    assertDiag('忻州r1', xzDiag)
  }

  // 确定性(施工单 §五):同输入两次加载,边 path/卡 rect/标签 rect 逐项一致。
  // P2a 端口随可见度数重排,确定性风险面变大,固化为脚本断言(rect 取 0.1px 吸浮点)
  await page.goto(`${BASE}/chain.html?src=./demo.web.json`)
  await page.waitForSelector('.react-flow__edge', { timeout: 30000 })
  await page.locator('label.chain-check', { hasText: '显示折叠件' }).locator('input').check()
  await sleep(800)
  const again = await collectScene(page)
  const norm = s => JSON.stringify({
    e: s.edges.map(x => [x.id, x.d, x.z]),
    c: s.cards.map(x => [x.id, x.rect.map(v => Math.round(v * 10))]),
    l: s.labels.map(x => [x.text, x.rect.map(v => Math.round(v * 10))]),
  })
  ok(norm(again) === norm(demoRaw), `确定性 demo 二次加载逐项一致(边 ${again.edges.length}/卡 ${again.cards.length}/标签 ${again.labels.length})`)

  await browser.close()
  // P0 基线汇总(⑧ 回直角后的回弹是施工单接受交换:忻州预期约 245 量级、
  // 同源扇区约 186 点,不得误判为 P3-A/P2a 回归)。
  if (diagLog.length) {
    console.log('\n[diag] 基线汇总(①lab×lab ②lab×node ⑤node×node ③穿卡:长线/非长线/压文字 ④跨列共线对 ⑧交叉点/交叉对/同源扇区对)')
    for (const { scene, n } of diagLog)
      console.log(`  ${scene}\t① ${n.ll}\t② ${n.ln}\t⑤ ${n.nn}\t③ ${n.enLong}/${n.enNormal}/${n.enText}\t④ ${n.col}\t⑧ ${n.xPts}/${n.xPairs}/扇区${n.xFan}`)
    console.log('  (①②④+穿卡全量均为硬断言;⑧ 只读,回直角后的回弹为施工单已接受交换)')
  }
  console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`)
} finally {
  server.kill()
  if (process.platform === 'win32') spawn('taskkill', ['/pid', String(server.pid), '/T', '/F'], { shell: false, stdio: 'ignore' })
  rmSync(directProbeDir, { recursive: true, force: true })
  rmSync(resolve(DEFAULT_RUNS_DIR, directProbeName), { force: true })
  syncChainRuns([])
}
process.exit(failures === 0 ? 0 : 1)
