import { apiRequest } from '../core/request'
import type {
  AgentMessage,
  AgentArtifactAction,
  AgentRunStatus,
  AgentRunSubmission
} from '../types/api'


export const agentApi = {
  submit: (
    message: string,
    clientRequestId: string,
    conversationId?: string,
    artifactAction?: AgentArtifactAction
  ) => apiRequest<AgentRunSubmission>(
    '/agent/runs',
    {
      method: 'POST',
      data: {
        message,
        client_request_id: clientRequestId,
        ...(conversationId ? { conversation_id: conversationId } : {}),
        ...(artifactAction ? { artifact_action: artifactAction } : {})
      }
    }
  ),
  run: (runId: string) => apiRequest<AgentRunStatus>(`/agent/runs/${runId}`),
  messages: (conversationId: string) => apiRequest<AgentMessage[]>(
    `/agent/conversations/${conversationId}/messages`,
    { query: { limit: 100 } }
  )
}
