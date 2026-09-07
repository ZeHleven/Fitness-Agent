import { useState } from 'react'
import { Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { getAgentConversationId, saveAgentConversationId } from '../../core/storage'
import { agentApi } from '../../services/agent'
import type { AgentConversationSummary } from '../../types/api'
import './index.scss'


export default function AgentConversationsPage () {
  const [conversations, setConversations] = useState<AgentConversationSummary[]>([])
  const [currentId, setCurrentId] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useDidShow(() => {
    setCurrentId(getAgentConversationId() || '')
    void (async () => {
      setLoading(true)
      setError('')
      try {
        setConversations(await agentApi.conversations(50))
      } catch (requestError) {
        setError(errorMessage(requestError, '历史对话加载失败'))
      } finally {
        setLoading(false)
      }
    })()
  })

  const openConversation = async (conversation: AgentConversationSummary) => {
    saveAgentConversationId(conversation.id)
    await Taro.navigateBack()
  }

  return (
    <View className='page conversations-page'>
      <Text className='conversations-eyebrow'>最近 50 个会话</Text>
      <Text className='conversations-title'>历史对话</Text>
      {error && <View className='error-banner'>{error}</View>}
      {loading && <View className='loading-state'>正在加载会话…</View>}
      {!loading && conversations.length === 0 && (
        <View className='card empty-state'>还没有历史对话。</View>
      )}
      {conversations.map(conversation => (
        <View
          className={`card conversation-card ${conversation.id === currentId ? 'current' : ''}`}
          key={conversation.id}
          onClick={() => openConversation(conversation)}
        >
          <View className='conversation-heading'>
            <Text className='conversation-title'>{conversation.title || '未命名对话'}</Text>
            {conversation.id === currentId && <Text className='current-tag'>当前</Text>}
          </View>
          <Text className='conversation-summary'>{conversation.summary || '点击查看这段对话'}</Text>
          <Text className='conversation-time'>{formatDateTime(conversation.updated_at)}</Text>
        </View>
      ))}
    </View>
  )
}

function formatDateTime (value: string): string {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
