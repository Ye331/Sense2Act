import type { Investigation, Report, Signal, Step } from '../api/client'

/** 页面用的调查任务视图：由 investigation + signal + steps + report 四个后端资源拼出。 */
export type TaskStatus = 'queued' | 'running' | 'reporting' | 'completed' | 'failed' | 'stopped'
export type ClaimNature = 'fact' | 'inference' | 'speculation'

export interface StageView { key: string; event: string; title: string; description: string; round: number }

export interface InvestigationTask {
  id: string
  status: TaskStatus
  investigation: Investigation
  signal?: Signal
  report?: Report
  title: string
  subject: string
  createdAt: string
  updatedAt: string
  currentRound: number
  totalRounds: number
  evidenceCount: number
  claimCounts: Record<ClaimNature, number>
  stages: StageView[]
  failureReason?: string
}

export interface ActivityView { id: string; taskId: string; time: string; title: string; description: string }

export type { Investigation, Report, Signal, Step }
