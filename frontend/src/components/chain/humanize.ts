// 人话化纯函数(批A施工单 §三.P3):机器定位符→人话,集中一处,全部确定性。
// 拍板 2(2026-11-13):界面 DOM 零机器编码——任何解析失败都回退人话占位,
// 绝不把裸串上屏;审计反查走 chain.web.json/台账文件,机器层完整性不受影响。
// 拍板 3:短编号(E3/c07/H2)为 Tier2 默认保留;长复合串(<evt>:c07/section:/|round:)为
// Tier1 必改,全部在本层消化。
import type { ChainWebPayload } from '../../types/chainWeb'

/** `<evt>:h2` → `假设 H2 · {label}`(label 从 conclusions[] 按 hypothesis_id 反查);
 *  已是人话 label 的形态(不含冒号)原样返回(施工单 §四.2:两形态都不炸);
 *  找不到/形态不辨 → `次选假设`,绝不回退裸 ID。 */
export function formatHypothesisRef(id: string, conclusions?: ChainWebPayload['conclusions']): string {
  if (!id) return '次选假设'
  if (!id.includes(':')) return id
  const tail = id.slice(id.lastIndexOf(':') + 1)
  if (!/^h\d+$/i.test(tail)) return '次选假设'
  const found = conclusions?.find(c => c.hypothesis_id === id)
  return found?.label ? `假设 H${tail.slice(1)} · ${found.label}` : '次选假设'
}

/** `section:<evt>:S3` → `报告结论稿 · S3 段`;其余/未知前缀 → `卷宗段落引用`(不回退原串)。 */
export function formatSourceRef(ref: string): string {
  if (!ref) return '卷宗段落引用'
  if (ref.startsWith('section:')) {
    const tail = ref.slice(ref.lastIndexOf(':') + 1)
    if (/^S\d+$/i.test(tail)) return `报告结论稿 · ${tail.toUpperCase()} 段`
    return '报告结论稿段落引用'
  }
  return '卷宗段落引用'
}

/** `<ISO>|round:latest` → `2026-11-11 21:39(最新一轮)`;`|round:N` → `(第 N 轮)`;
 *  解析失败 → `快照时间未标注`(console.warn,原串不上屏)。 */
export function formatSnapshotVersion(v: string): string {
  const fallback = '快照时间未标注'
  if (!v) return fallback
  const sep = v.lastIndexOf('|')
  const iso = sep >= 0 ? v.slice(0, sep) : v
  const round = sep >= 0 ? v.slice(sep + 1) : ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) { console.warn('[humanize] 快照时间解析失败,不上屏原串'); return fallback }
  const p2 = (n: number): string => String(n).padStart(2, '0')
  const t = `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())} ${p2(d.getHours())}:${p2(d.getMinutes())}`
  if (!round) return t
  if (round === 'round:latest') return `${t}(最新一轮)`
  const m = /^round:(\d+)$/.exec(round)
  if (m) return `${t}(第 ${m[1]} 轮)`
  console.warn('[humanize] 快照轮次解析失败,不上屏原串')
  return fallback
}

/** `<evt>:E5` → `E5`(证据/命题短编号,Tier2 保留形态;去事件前缀防长复合串泄漏)。 */
export function stripEventPrefix(id: string): string {
  if (!id) return ''
  const i = id.lastIndexOf(':')
  return i >= 0 ? id.slice(i + 1) : id
}

/** 摘录截断(成员卡 text ≤160 字,施工单 §三.P1 GroupDetail;全文留台账)。 */
export function excerptText(text: string, max = 160): string {
  return text.length > max ? `${text.slice(0, max)}…` : text
}
