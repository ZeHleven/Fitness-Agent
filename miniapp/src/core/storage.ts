import Taro from '@tarojs/taro'

import { isPendingProposalDecision } from './proposal-interaction'
import type { PendingPlanAdjustmentProposalDecision } from '../types/plan-adjustment-proposal'

const ACCESS_TOKEN_KEY = 'fitness_access_token'
const REFRESH_TOKEN_KEY = 'fitness_refresh_token'
const AGENT_CONVERSATION_KEY = 'fitness_agent_conversation_id'
const AGENT_PENDING_REQUEST_KEY = 'fitness_agent_pending_request'
const PROPOSAL_DECISION_JOURNAL_KEY = 'fitness_proposal_decision_journal_v1'
const MAX_PROPOSAL_DECISION_RECORDS = 20

type ProposalDecisionJournal = Record<
  string,
  PendingPlanAdjustmentProposalDecision
>

export interface PendingAgentRequest {
  client_request_id: string
  message: string
  conversation_id?: string
  run_id?: string
  created_at: number
}

export function getAccessToken(): string {
  return Taro.getStorageSync<string>(ACCESS_TOKEN_KEY) || ''
}

export function getRefreshToken(): string {
  return Taro.getStorageSync<string>(REFRESH_TOKEN_KEY) || ''
}

export function saveTokens(accessToken: string, refreshToken: string): void {
  Taro.setStorageSync(ACCESS_TOKEN_KEY, accessToken)
  Taro.setStorageSync(REFRESH_TOKEN_KEY, refreshToken)
}

export function clearTokens(): void {
  Taro.removeStorageSync(ACCESS_TOKEN_KEY)
  Taro.removeStorageSync(REFRESH_TOKEN_KEY)
  Taro.removeStorageSync(AGENT_CONVERSATION_KEY)
  Taro.removeStorageSync(AGENT_PENDING_REQUEST_KEY)
  Taro.removeStorageSync(PROPOSAL_DECISION_JOURNAL_KEY)
}

export function isAuthenticated(): boolean {
  return Boolean(getAccessToken())
}

export function getAgentConversationId(): string {
  return Taro.getStorageSync<string>(AGENT_CONVERSATION_KEY) || ''
}

export function saveAgentConversationId(conversationId: string): void {
  Taro.setStorageSync(AGENT_CONVERSATION_KEY, conversationId)
}

export function clearAgentConversationId(): void {
  Taro.removeStorageSync(AGENT_CONVERSATION_KEY)
}

export function getPendingAgentRequest(): PendingAgentRequest | null {
  const value = Taro.getStorageSync<PendingAgentRequest>(AGENT_PENDING_REQUEST_KEY)
  if (!value || typeof value !== 'object' || !value.client_request_id || !value.message) {
    return null
  }
  return value
}

export function savePendingAgentRequest(request: PendingAgentRequest): void {
  Taro.setStorageSync(AGENT_PENDING_REQUEST_KEY, request)
}

export function clearPendingAgentRequest(): void {
  Taro.removeStorageSync(AGENT_PENDING_REQUEST_KEY)
}

function proposalDecisionJournal (): ProposalDecisionJournal {
  const stored = Taro.getStorageSync<unknown>(PROPOSAL_DECISION_JOURNAL_KEY)
  if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return {}

  return Object.fromEntries(
    Object.entries(stored)
      .filter(([, value]) => isPendingProposalDecision(value))
      .sort((left, right) => right[1].created_at - left[1].created_at)
      .slice(0, MAX_PROPOSAL_DECISION_RECORDS)
  )
}

export function getPendingProposalDecision (
  proposalId: string
): PendingPlanAdjustmentProposalDecision | null {
  return proposalDecisionJournal()[proposalId] || null
}

export function savePendingProposalDecision (
  decision: PendingPlanAdjustmentProposalDecision
): void {
  if (!isPendingProposalDecision(decision)) {
    throw new Error('拒绝保存无效的 Proposal 决策记录')
  }
  const journal = proposalDecisionJournal()
  journal[decision.proposal_id] = decision
  const bounded = Object.fromEntries(
    Object.entries(journal)
      .sort((left, right) => right[1].created_at - left[1].created_at)
      .slice(0, MAX_PROPOSAL_DECISION_RECORDS)
  )
  Taro.setStorageSync(PROPOSAL_DECISION_JOURNAL_KEY, bounded)
}

export function clearPendingProposalDecision (proposalId: string): void {
  const journal = proposalDecisionJournal()
  if (!journal[proposalId]) return
  delete journal[proposalId]
  if (Object.keys(journal).length === 0) {
    Taro.removeStorageSync(PROPOSAL_DECISION_JOURNAL_KEY)
    return
  }
  Taro.setStorageSync(PROPOSAL_DECISION_JOURNAL_KEY, journal)
}
