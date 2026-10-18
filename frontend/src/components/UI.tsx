import type { ReactNode } from 'react'
import type { ClaimNature, TaskStatus } from '../types/domain'
import { Icon } from './Icon'

export const statusLabels: Record<TaskStatus, string> = { queued: '待领取', running: '调查中', reporting: '报告中', completed: '已完成', failed: '失败', stopped: '已停止' }
const statusClass: Record<TaskStatus, string> = { queued: 'queued', running: 'running', reporting: 'running', completed: 'completed', failed: 'failed', stopped: 'failed' }
export function StatusBadge({ status }: { status: TaskStatus }) { return <span className={`status-badge status-${statusClass[status]}`}><i />{statusLabels[status]}</span> }

const natureClass: Record<ClaimNature, string> = { fact: '已证实', inference: '待证实', speculation: '矛盾' }
const natureLabel: Record<ClaimNature, string> = { fact: '事实', inference: '推断', speculation: '推测' }
export function NatureBadge({ nature }: { nature: ClaimNature }) { return <span className={`claim-badge claim-${natureClass[nature]}`}>{natureLabel[nature]}</span> }

export function EmptyState({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name="file" size={26}/></span><strong>{title}</strong><p>{text}</p>{action}</div>
}
export function PageHeading({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <div className="page-heading"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h1>{title}</h1><p>{description}</p></div>{action && <div className="heading-action">{action}</div>}</div>
}
export function ErrorBar({ message }: { message: string }) { return message ? <p className="form-error" role="alert">{message}</p> : null }
