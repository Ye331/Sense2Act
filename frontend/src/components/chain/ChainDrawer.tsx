// 详情抽屉:点节点/边打开,不跳页、不查主库——只读 chain_web.v1 的 details 延迟区。
// internal 材料 detail_ref 为空(无内容可看),本抽屉根本不会被打开;字段空缺一律如实显示"未记录"。
import { Component } from 'react'
import type { ChainWebClaim, ChainWebGap, ChainWebPayload, ChainWebVerificationItem } from '../../types/chainWeb'
import { excerptText, formatHypothesisRef, formatSnapshotVersion, formatSourceRef, stripEventPrefix } from './humanize'

const TYPE_LABEL: Record<string, string> = { evidence: '来源证据', claim: '事实命题', verification: '交叉验证', hypothesis: '结论假设', ruling: '逐条裁定', group: '命题组', edge_group: '汇流事实簇', routed_gap: '验证缺口', routed_meta: '信封字段缺口', inference: '结构化推理', missing: '节点说明' }

function Empty() { return <span className="chain-empty">未记录</span> }

function KV({ k, children }: { k: string; children: React.ReactNode }) {
  return <div className="drawer-kv"><dt>{k}</dt><dd>{children}</dd></div>
}

function LegacyNote({ legacy, masked }: { legacy?: boolean; masked?: boolean }) {
  if (masked) return <p className="drawer-note">该件已脱敏(masked),原文不上前端。</p>
  if (legacy) return <p className="drawer-note">引文形态 = legacy 回落:该录制件早于 S6 归档 quote/reason 落地,属配对纪律的自然结果,以定位符(root/src/num)为准。</p>
  return null
}

type DrawerProps = { payload: ChainWebPayload; detailRef: string; onClose: () => void; onNavigateClaim?: (claimId: string) => void }

/** 详情局部错误隔离：即使某条旧件的异构 detail 超出契约，主图也不卸载。 */
export class ChainDrawerBoundary extends Component<DrawerProps, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch(error: unknown) { console.error('[chain drawer isolated]', error) }
  render() {
    if (this.state.failed) return <aside className="chain-drawer" aria-label="详情错误">
      <div className="chain-drawer-head"><span className="chain-drawer-kind">详情局部降级</span><button className="drawer-close" onClick={this.props.onClose} aria-label="关闭详情">✕</button></div>
      <div className="chain-drawer-body"><p className="drawer-note">该条详情无法按当前契约显示，主图已保留。请关闭抽屉继续查看其他节点。</p></div>
    </aside>
    return <ChainDrawer {...this.props} />
  }
}

export function ChainDrawer({ payload, detailRef, onClose, onNavigateClaim }: DrawerProps) {
  const detail = payload.details[detailRef]
  const kind = detailRef.split(':')[0]
  return <aside className="chain-drawer" aria-label="详情抽屉">
    <div className="chain-drawer-head">
      <span className="chain-drawer-kind">{TYPE_LABEL[kind] ?? '详情'}</span>
      <button className="drawer-close" onClick={onClose} aria-label="关闭详情">✕</button>
    </div>
    <div className="chain-drawer-body">
      {!detail ? <p className="drawer-note">该节点无详情内容(internal 材料仅显示定位符,全文不上前端)。</p> :
        kind === 'evidence' ? <EvidenceDetail p={payload} ref_={detailRef} d={detail as { title: string; excerpt: string; platform: string | null; published_at: string | null; source_url: string; collected_at: string }} /> :
        kind === 'claim' ? <ClaimDetail p={payload} ref_={detailRef} d={detail as { gist: string; text: string; category: string; entities: string[]; transitions: ChainWebPayload['timeline']; evidence_state_reason?: string; family_members?: FamilyMember[] }} /> :
        kind === 'verification' ? <VerificationDetail p={payload} ref_={detailRef} d={detail as { items: ChainWebVerificationItem[]; threshold_note: string; evidence_state_reason?: string }} /> :
        kind === 'hypothesis' ? <HypothesisDetail p={payload} d={detail as ChainWebPayload['conclusions'][number]} onNavigateClaim={onNavigateClaim} /> :
        kind === 'ruling' ? <RulingDetail d={detail as { ruling: string; quote: string | null; reason: string | null; legacy?: boolean; masked?: boolean; root: string; src: string; num: string; title: string }} /> :
        kind === 'edge_group' ? <EdgeGroupDetail p={payload} d={detail as EdgeGroupDetailData} /> :
        kind === 'routed_gap' || kind === 'routed_meta' ? <RoutedDetail d={detail as RoutedDetailData} /> :
        kind === 'inference' ? <InferenceDetail p={payload} d={detail as InferenceDetailData} /> :
        <GroupDetail p={payload} d={detail as { name: string; members: string[]; polarity: { n_pos: number; n_rev: number; n_none: number } }} />}
    </div>
  </aside>
}

function EvidenceDetail({ p, ref_, d }: { p: ChainWebPayload; ref_: string; d: { title: string; excerpt: string; platform: string | null; published_at: string | null; source_url: string; collected_at: string } }) {
  const ev = p.evidence.find(e => ref_.endsWith(`:${e.evidence_id}`))
  const hasContent = !!(d.title || d.excerpt)
  return <>
    {hasContent ? <h3>{d.title || '(无标题)'}</h3> : <p className="drawer-note">该件为 internal 敏感材料:仅定位符,内容不上前端。</p>}
    {d.excerpt && <blockquote className="drawer-quote">{d.excerpt}</blockquote>}
    <dl>
      <KV k="平台">{d.platform ?? <Empty />}</KV>
      <KV k="发布时间">{d.published_at ?? <Empty />}</KV>
      <KV k="采集时间">{d.collected_at || <Empty />}</KV>
      {ev && <>
        <KV k="谱系根">{ev.lineage_root || '—'}{ev.same_root_count > 1 && <span className="chip chip-same"> 同源 {ev.same_root_count} 件(折一根)</span>}</KV>
        <KV k="强度">{[ev.authoritative && '权威登记源', ev.party && '当事方自证'].filter(Boolean).join(' · ') || '普通来源'}</KV>
        <KV k="敏感性">{ev.sensitivity}</KV>
      </>}
    </dl>
    {d.source_url && <p className="drawer-url">
      <a href={d.source_url} target="_blank" rel="noreferrer noopener">查看出处链接 ↗</a>
      <small>出处仅为已记录定位;第三方页面可能随时间变化,以本页记录的标题/摘录为准。</small>
    </p>}
  </>
}

type FamilyMember = { claim_id: string; text: string; status: string; evidence_state: string; provenance_ids: string[] }

function ClaimDetail({ p, ref_, d }: { p: ChainWebPayload; ref_: string; d: { gist: string; text: string; category: string; entities: string[]; transitions: ChainWebPayload['timeline']; evidence_state_reason?: string; family_members?: FamilyMember[] } }) {
  const claim = p.claims.find(c => ref_.endsWith(`:${c.claim_id}`))
  return <>
    <h3>{d.gist}</h3>
    <dl>
      <KV k="状态">{claim?.status || '—'}</KV>
      <KV k="类别">{d.category || '—'}</KV>
      <KV k="状态史">{claim?.history || '—'}</KV>
      <KV k="证据锚点">{claim?.anchor_name || '—'}</KV>
      <KV k="直接证据状态">{claim ? evidenceStateText(claim.evidence_state) : '—'}</KV>
      {d.evidence_state_reason && <KV k="状态原因">{d.evidence_state_reason}</KV>}
      {claim?.gap && <KV k="缺口"><GapDetail gap={claim.gap} /></KV>}
      {claim?.flipback_note && <KV k="翻转史">{claim.flipback_note}</KV>}
      {d.entities.length > 0 && <KV k="涉及实体">{d.entities.join('、')}</KV>}
    </dl>
    {d.text && <><h4>原文记载</h4><blockquote className="drawer-quote">{d.text}</blockquote></>}
    {claim?.ruling_summary && <dl>
      <KV k="裁定账">支持 {claim.ruling_summary.support} / 矛盾 {claim.ruling_summary.contra} / 时效 {claim.ruling_summary.temporal} / 独立根 {claim.ruling_summary.n_roots}(材料 {claim.ruling_summary.n_items} 件,同根折叠 {claim.ruling_summary.n_dup_root})</KV>
    </dl>}
    {(d.family_members?.length ?? 0) > 1 && <>
      <h4>同义命题成员(正本全量)</h4>
      <ul className="drawer-members">{d.family_members!.map(m => <li key={m.claim_id}><b>{m.status}</b> · {evidenceStateText(m.evidence_state)}<blockquote className="drawer-quote">{m.text}</blockquote><span className="drawer-src">来源定位 {m.provenance_ids.length} 件</span></li>)}</ul>
    </>}
    {d.transitions.length > 0 && <>
      <h4>状态演变(只读 transitions)</h4>
      <ul className="drawer-transitions">{d.transitions.map((t, i) => <li key={i}><span className="mono">R{t.round}</span> {t.old_status} → <b>{t.new_status}</b>{t.evidence_id && <span> · 证据 {stripEventPrefix(t.evidence_id)}</span>}{t.cause && <span> · {t.cause}</span>}</li>)}</ul>
    </>}
  </>
}

function GapDetail({ gap }: { gap: ChainWebGap | string }) {
  if (typeof gap === 'string') return <>{gap || <Empty />}</>
  const rows: Array<[string, string | number | undefined]> = [
    ['缺口类型', gap.type], ['当前记录', gap.label], ['尚差什么', gap.next],
    ['建议通道', gap.next_channel || gap.channel], ['已尝试次数', gap.tries],
    ['未裁数', gap.unadjudicated], ['通道说明', gap.channel_rationale],
  ]
  const visible = rows.filter(([, value]) => value !== undefined && value !== '')
  if (!visible.length) return <Empty />
  return <dl className="drawer-gap">{visible.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>
}

function VerificationDetail({ p, ref_, d }: { p: ChainWebPayload; ref_: string; d: { items: ChainWebVerificationItem[]; threshold_note: string; evidence_state_reason?: string } }) {
  const v = p.verification.find(x => ref_.endsWith(`:${x.claim_id}`))
  const items = d.items ?? []
  return <>
    <h3>交叉验证</h3>
    {v && <dl>
      <KV k="当前状态">{v.status}</KV>
      <KV k="独立根 / 材料"><b>{v.n_independent_roots}</b> 根 / {v.n_items} 件(同根折叠 {v.n_dup_root})</KV>
      <KV k="计数">支持 {v.n_support} · 矛盾 {v.n_contra} · 时效刷新 {v.n_temporal}</KV>
      <KV k="直接证据">{evidenceStateText(v.evidence_state)}</KV>
      {v.unruled_nums.length > 0 && <KV k="未裁编号">{v.unruled_nums.join('、')}</KV>}
    </dl>}
    {v?.threshold_note ? <p className="drawer-note">门槛说明:{v.threshold_note}</p> : null}
    {d.evidence_state_reason && <p className="drawer-note">缺线原因:{d.evidence_state_reason}</p>}
    <h4>逐件裁定</h4>
    <ul className="drawer-rulings">
      {items.map((it, i) => <li key={i}>
        <span className={`chip chip-ruling-${it.ruling === '支持' ? 'pos' : it.ruling === '矛盾' ? 'neg' : 'other'}`}>{it.ruling}</span>
        <b>{it.num}</b> {it.title}
        <span className="drawer-src">{it.root !== it.src ? `${it.src} · 根:${it.root}` : it.src}{it.dup_root && ' · 同根折算'}</span>
        <LegacyNote legacy={it.legacy} masked={it.masked} />
        {it.quote && <blockquote className="drawer-quote">{it.quote}</blockquote>}
        {it.reason && <p className="drawer-reason">裁定理由:{it.reason}</p>}
      </li>)}
    </ul>
  </>
}

function HypothesisDetail({ p, d, onNavigateClaim }: { p: ChainWebPayload; d: ChainWebPayload['conclusions'][number]; onNavigateClaim?: (claimId: string) => void }) {
  const ticket = d.ticket ?? { support: 0, counter: 0, checking: 0 }
  const memberIds = d.member_ids ?? []
  const runnersUp = d.runners_up ?? []
  const groups = d.group_polarity ?? []
  const claimLink = (id: string) => {
    const c = p.claims.find(x => x.claim_id === id)
    return <li key={id}><button className="drawer-claim-link" onClick={() => onNavigateClaim?.(id)}><span className={`chain-status chain-status-${c?.status === '已证实' ? 'verified' : c?.status === '矛盾' ? 'contra' : 'pending'}`}>{c?.status ?? '—'}</span> {c?.gist || c?.text || '成员命题(未展开)'}</button></li>
  }
  return <>
    <h3>{d.label}{d.chosen ? '(主结论)' : '(次选假设)'}</h3>
    {d.description && <><h4>假设说明</h4><p className="drawer-hypothesis-description">{d.description}</p></>}
    <h4>当前判断</h4>
    <p className="chain-verdict">{d.summary || d.verdict_line}</p>
    <dl>
      <KV k="票型">支持 {ticket.support} · 拆台 {ticket.counter} · 在查 {ticket.checking}</KV>
      <KV k="成员命题">{d.n_members} 条,已证实 {d.n_verified} 条</KV>
      {runnersUp.length > 0 && <KV k="竞争假设">{runnersUp.map(id => formatHypothesisRef(id, p.conclusions)).join('、')}</KV>}
    </dl>
    {(d.supporting_claim_ids?.length ?? 0) > 0 && <><h4>已有依据</h4><ul className="drawer-members">{d.supporting_claim_ids!.map(claimLink)}</ul></>}
    {(d.pending_claim_ids?.length ?? 0) > 0 && <><h4>尚缺条件</h4><ul className="drawer-members">{d.pending_claim_ids!.map(claimLink)}</ul></>}
    {(d.alternatives?.length ?? 0) > 0 && <><h4>为什么不是其他假设</h4><ul className="drawer-groups">{d.alternatives!.map(a => <li key={a.hypothesis_id}><b>{a.label}</b> · {a.reason}</li>)}</ul></>}
    {(d.change_conditions?.length ?? 0) > 0 && <><h4>何时会改变</h4><ul className="drawer-members">{d.change_conditions!.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
    {(d.next_steps?.length ?? 0) > 0 && <><h4>下一步核实</h4><ol className="drawer-next-steps">{d.next_steps!.map((x, i) => <li key={i}>{x}</li>)}</ol></>}
    {d.source_ref && <p className="drawer-source-note">来源 {formatSourceRef(d.source_ref)}{d.source_snapshot_version && <> · 快照 {formatSnapshotVersion(d.source_snapshot_version)}</>}</p>}
    {groups.length > 0 && <><h4>分组票型</h4><ul className="drawer-groups">{groups.map((g, i) => <li key={i}><b>{g.group}</b> · 支持 {g.n_pos} / 拆台 {g.n_rev} / 中性 {g.n_none}</li>)}</ul></>}
    {!d.summary && <><h4>成员命题</h4><ul className="drawer-members">{memberIds.map(claimLink)}</ul></>}
  </>
}

function RulingDetail({ d }: { d: { ruling: string; quote: string | null; reason: string | null; legacy?: boolean; masked?: boolean; root: string; src: string; num: string; title: string } }) {
  return <>
    <h3>裁定:{d.ruling}</h3>
    <dl>
      <KV k="证据">{d.num} {d.title}</KV>
      <KV k="定位">{d.src}{d.root !== d.src && ` · 根:${d.root}`}</KV>
    </dl>
    <LegacyNote legacy={d.legacy} masked={d.masked} />
    {d.quote && <><h4>引用片段</h4><blockquote className="drawer-quote">{d.quote}</blockquote></>}
    {d.reason && <p className="drawer-reason">裁定理由:{d.reason}</p>}
    {!d.quote && !d.reason && !d.legacy && !d.masked && <p className="drawer-note">本条裁定未携带引文/理由字段。</p>}
  </>
}

type EdgeGroupDetailData = {
  id: string; group: string; relation_type: string; n: number
  evidence_id: string; members: string[]; raw_member_claim_ids: string[]
  raw_edge_ids: string[]; status_dist: Record<string, number>
  evidence_state_dist: Record<string, number>
}

function EdgeGroupDetail({ p, d }: { p: ChainWebPayload; d: EdgeGroupDetailData }) {
  const evTitle = p.evidence.find(e => e.evidence_id === d.evidence_id)?.title
  return <>
    <h3>{d.group}</h3>
    <dl>
      <KV k="汇流关系">{d.relation_type} ×{d.n}</KV>
      <KV k="来源材料">{stripEventPrefix(d.evidence_id)}{evTitle && ` · ${evTitle}`}</KV>
      <KV k="状态分布">已证实 {d.status_dist['已证实'] ?? 0} / 待证实 {d.status_dist['待证实'] ?? 0} / 矛盾 {d.status_dist['矛盾'] ?? 0}</KV>
      <KV k="证据分布">支持 {d.evidence_state_dist.supported ?? 0} / 反证 {d.evidence_state_dist.contested ?? 0} / 待裁 {d.evidence_state_dist.unadjudicated ?? 0} / 无直接证据 {d.evidence_state_dist.no_direct ?? 0}</KV>
    </dl>
    <p className="drawer-note">汇流只改主图投影；下列 claim 与 {d.raw_edge_ids.length} 条原始边均保留在 JSON。</p>
    <h4>原始命题</h4>
    <ul className="drawer-members">{d.raw_member_claim_ids.map(id => {
      const c = p.claims.find(x => x.claim_id === id)
      return <li key={id}><b>{c?.status ?? '—'}</b> · {c ? evidenceStateText(c.evidence_state) : '—'}<blockquote className="drawer-quote">{c?.text || '成员命题(未展开)'}</blockquote></li>
    })}</ul>
  </>
}

type RoutedDetailData = { kind: string; claim_ids: string[]; items: { claim_id: string; text: string; status: string }[] }

function RoutedDetail({ d }: { d: RoutedDetailData }) {
  return <>
    <h3>{d.kind === 'meta_info' ? '信封字段覆盖缺口' : '基础核验缺口'}</h3>
    <p className="drawer-note">这些记录属于验证/省略口径，不是客观事实；原 claim 仍全量保留。</p>
    <ul className="drawer-members">{d.items.map(item => <li key={item.claim_id}><b>{item.status}</b><blockquote className="drawer-quote">{item.text}</blockquote></li>)}</ul>
  </>
}

type InferenceDetailData = {
  label: string; inference_text: string; basis_claim_ids: string[]; rule: string
  target_hypothesis_ids: string[]; premise_status_summary: Record<string, number>
  condition_or_gap: string; fingerprint: string; sidecar_provenance: string
  fingerprint_verified: boolean; routed_claim_ids: string[]; context_claim_ids: string[]
}

function InferenceDetail({ p, d }: { p: ChainWebPayload; d: InferenceDetailData }) {
  return <>
    <h3>{d.label}</h3>
    <p className="chain-verdict">{d.inference_text}</p>
    <dl>
      <KV k="推理规则">{d.rule}</KV>
      <KV k="条件/缺口">{d.condition_or_gap}</KV>
      <KV k="前提状态">已支持 {d.premise_status_summary['已证实'] ?? 0} / 待补证 {d.premise_status_summary['待证实'] ?? 0} / 矛盾 {d.premise_status_summary['矛盾'] ?? 0}</KV>
      <KV k="目标假设">{d.target_hypothesis_ids.map(id => formatHypothesisRef(id, p.conclusions)).join('、')}</KV>
      <KV k="溯源">{d.sidecar_provenance} · 指纹{d.fingerprint_verified ? '已验证' : '未验证'}</KV>
    </dl>
    <h4>依据 claim</h4>
    <ul className="drawer-members">{d.basis_claim_ids.map(id => {
      const c = p.claims.find(x => x.claim_id === id)
      return <li key={id}><b>{c?.status ?? '—'}</b> · {c ? evidenceStateText(c.evidence_state) : '—'}<blockquote className="drawer-quote">{c?.text || '成员命题(未展开)'}</blockquote></li>
    })}</ul>
    {d.routed_claim_ids.length > 0 && <p className="drawer-note">已吸收解读记录 {d.routed_claim_ids.length} 条(原文全量保留在台账)。</p>}
    {d.context_claim_ids.length > 0 && <p className="drawer-note">关联验证缺口 {d.context_claim_ids.length} 条(原文全量保留在台账)。</p>}
  </>
}

function evidenceStateText(state: string): string {
  return ({ supported: '已有直接支持', contested: '有反证', unadjudicated: '关联材料待裁定', no_direct: '无直接证据/待补证' } as Record<string, string>)[state] ?? state
}

function GroupDetail({ p, d }: { p: ChainWebPayload; d: { name: string; members: string[]; polarity: { n_pos: number; n_rev: number; n_none: number } } }) {
  const found = d.members
    .map(m => p.claims.find(c => c.claim_id === m))
    .filter((c): c is ChainWebClaim => !!c)
  const nHidden = d.members.length - found.length
  return <>
    <h3>{d.name}</h3>
    <dl><KV k="票型">支持 {d.polarity.n_pos} · 拆台 {d.polarity.n_rev} · 中性 {d.polarity.n_none}</KV></dl>
    <h4>成员命题</h4>
    {found.length > 0 && <ul className="drawer-members">{found.map(c => <li key={c.claim_id}>
      <span className={`chain-status chain-status-${c.status === '已证实' ? 'verified' : c.status === '矛盾' ? 'contra' : 'pending'}`}>{c.status}</span> {c.gist}
      <blockquote className="drawer-quote">{excerptText(c.text || c.gist)}</blockquote>
    </li>)}</ul>}
    {nHidden > 0 && <p className="drawer-note">另有 {nHidden} 条成员命题未在当前视图展开(原文全量保留在台账)。</p>}
    {found.length === 0 && nHidden === 0 && <Empty />}
  </>
}
