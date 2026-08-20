import Taro from '@tarojs/taro'

const ACCESS_TOKEN_KEY = 'fitness_access_token'
const REFRESH_TOKEN_KEY = 'fitness_refresh_token'
const AGENT_CONVERSATION_KEY = 'fitness_agent_conversation_id'
const AGENT_PENDING_REQUEST_KEY = 'fitness_agent_pending_request'

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
