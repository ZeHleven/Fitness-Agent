import { useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import { useDidShow, useLoad } from '@tarojs/taro'

import {
  projectProposalStatus,
  proposalLocalExpiryState
} from '../../core/proposal-interaction'
import type { ProposalStatusPresentation } from '../../core/proposal-interaction'
import { errorMessage } from '../../core/request'
import { proposalsApi } from '../../services/proposals'
import type {
  PlanAdjustmentChange,
  PlanAdjustmentExerciseSnapshot,
  PlanAdjustmentPlanSnapshot,
  PlanAdjustmentProposalReadResponse
} from '../../types/plan-adjustment-proposal'
import './index.scss'


const weekday = (day: number) => (
  ['一', '二', '三', '四', '五', '六', '日'][day - 1] || String(day)
)

export default function ProposalDetailPage () {
  const [proposalId, setProposalId] = useState('')
  const [proposal, setProposal] = useState<PlanAdjustmentProposalReadResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [missing, setMissing] = useState(false)

  const loadProposal = async (id: string, refresh = false) => {
    if (refresh) setRefreshing(true)
    else setLoading(true)
    setError('')
    try {
      const value = await proposalsApi.get(id)
      setProposal(value)
      setMissing(false)
    } catch (requestError) {
      const candidate = requestError as { code?: unknown }
      if (candidate && candidate.code === 'proposal_not_found') {
        setMissing(true)
        setProposal(null)
      }
      setError(errorMessage(requestError, '暂时无法读取调整提案'))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }

  useLoad(options => {
    const id = typeof options.id === 'string' ? options.id.trim() : ''
    setProposalId(id)
    if (!id) {
      setError('缺少提案标识，请从 Agent 对话重新打开')
      setLoading(false)
      return
    }
    void loadProposal(id)
  })

  useDidShow(() => {
    if (proposalId && !loading) void loadProposal(proposalId, true)
  })

  if (loading) {
    return <View className='loading-state'>正在读取调整提案…</View>
  }

  const presentation = proposal
    ? projectProposalStatus(
        proposal.status,
        proposalLocalExpiryState(proposal.status, proposal.expires_at)
      )
    : projectProposalStatus('missing', 'irrelevant')

  return (
    <View className='page proposal-detail-page'>
      <View className='proposal-hero'>
        <View className={`proposal-status ${presentation.tone}`}>
          {presentation.label}
        </View>
        <Text className='proposal-kicker'>训练计划调整提案</Text>
        <Text className='proposal-title'>先看清全部变化，再决定是否采用</Text>
        <Text className='proposal-readonly-note'>
          当前为只读预览，本页不会修改你的训练计划。
        </Text>
      </View>

      {error && <View className='error-banner'>{error}</View>}
      {!proposal && (
        <View className='card proposal-unavailable'>
          <Text className='section-title'>
            {missing ? '这份提案已不可用' : '尚未读取到提案'}
          </Text>
          <Text className='section-copy'>
            请返回 Agent 对话核对提案，或手动刷新当前页面。
          </Text>
          {proposalId && (
            <Button
              className='secondary-button refresh-button'
              disabled={refreshing}
              onClick={() => loadProposal(proposalId, true)}
            >
              {refreshing ? '正在刷新…' : '刷新状态'}
            </Button>
          )}
        </View>
      )}

      {proposal && (
        <ProposalContent
          proposal={proposal}
          presentation={presentation}
          refreshing={refreshing}
          onRefresh={() => loadProposal(proposal.id, true)}
        />
      )}
    </View>
  )
}

function ProposalContent ({
  proposal,
  presentation,
  refreshing,
  onRefresh
}: {
  proposal: PlanAdjustmentProposalReadResponse
  presentation: ProposalStatusPresentation
  refreshing: boolean
  onRefresh: () => void
}) {
  return (
    <>
      <View className='card proposal-meta-card'>
        <View className='meta-row'>
          <Text className='meta-label'>创建时间</Text>
          <Text className='meta-value'>{formatDateTime(proposal.created_at)}</Text>
        </View>
        <View className='meta-row'>
          <Text className='meta-label'>有效期至</Text>
          <Text className='meta-value'>{formatDateTime(proposal.expires_at)}</Text>
        </View>
        <View className='meta-row'>
          <Text className='meta-label'>提案版本</Text>
          <Text className='meta-value'>v{proposal.version}</Text>
        </View>
        {presentation.refresh_required && (
          <Text className='verification-note'>
            本机时间显示提案可能已到期，需要以服务端状态为准。
          </Text>
        )}
        <Button
          className='secondary-button refresh-button'
          disabled={refreshing}
          onClick={onRefresh}
        >
          {refreshing ? '正在刷新…' : '刷新服务端状态'}
        </Button>
      </View>

      <Text className='section-heading'>调整概要</Text>
      <View className='card changes-card'>
        {proposal.payload.changes.map((change, index) => (
          <ChangeSummary
            change={change}
            index={index}
            key={`${change.stable_display_key}-${change.change_type}-${index}`}
          />
        ))}
      </View>

      <Text className='section-heading'>完整计划对比</Text>
      <PlanSnapshot
        plan={proposal.payload.before}
        stage='before'
        title='调整前'
      />
      <PlanSnapshot
        plan={proposal.payload.after}
        stage='after'
        title='调整后'
      />

      <View className='card rationale-card'>
        <Text className='section-title'>为什么建议这样调整</Text>
        {proposal.payload.rationale.map((item, index) => (
          <Text className='detail-line' key={`rationale-${index}`}>
            {index + 1}. {item}
          </Text>
        ))}
      </View>

      <View className='safety-card'>
        <Text className='safety-title'>安全提示</Text>
        {proposal.payload.safety_notes.length > 0
          ? proposal.payload.safety_notes.map((item, index) => (
              <Text className='safety-line' key={`safety-${index}`}>• {item}</Text>
            ))
          : <Text className='safety-line'>本提案没有额外安全提示，训练中仍应以无痛、可控为前提。</Text>}
      </View>

      <View className='readonly-footer'>
        <Text className='readonly-footer-title'>只读预览</Text>
        <Text className='readonly-footer-copy'>
          确认和拒绝交互尚未开放，当前操作不会写入训练计划。
        </Text>
      </View>
    </>
  )
}

function PlanSnapshot ({
  plan,
  stage,
  title
}: {
  plan: PlanAdjustmentPlanSnapshot
  stage: 'before' | 'after'
  title: string
}) {
  const exercises = [...plan.exercises].sort((left, right) => (
    left.day_of_week - right.day_of_week || left.order_index - right.order_index
  ))
  return (
    <View className={`card plan-snapshot ${stage}`}>
      <View className='snapshot-heading'>
        <View>
          <Text className='snapshot-stage'>{title}</Text>
          <Text className='snapshot-name'>{plan.name}</Text>
        </View>
        <Text className='snapshot-count'>{exercises.length} 个动作</Text>
      </View>
      <View className='snapshot-facts'>
        <SnapshotFact label='目标' value={plan.goal || '未设置'} />
        <SnapshotFact label='周期' value={`${plan.duration_weeks} 周`} />
        <SnapshotFact label='频率' value={`每周 ${plan.days_per_week} 天`} />
      </View>
      {exercises.map(exercise => (
        <ExerciseSnapshot
          exercise={exercise}
          key={`${exercise.slot_key}-${exercise.exercise_id}`}
        />
      ))}
    </View>
  )
}

function SnapshotFact ({ label, value }: { label: string, value: string }) {
  return (
    <View className='snapshot-fact'>
      <Text className='fact-label'>{label}</Text>
      <Text className='fact-value'>{value}</Text>
    </View>
  )
}

function ExerciseSnapshot ({
  exercise
}: {
  exercise: PlanAdjustmentExerciseSnapshot
}) {
  return (
    <View className='exercise-snapshot'>
      <View className='exercise-heading'>
        <View>
          <Text className='exercise-name'>{exercise.exercise_name}</Text>
          <Text className='exercise-position'>
            周{weekday(exercise.day_of_week)} · 第 {exercise.order_index + 1} 项 · 位置 {exercise.slot_key}
          </Text>
        </View>
      </View>
      <View className='exercise-prescription'>
        <SnapshotFact label='组数' value={`${exercise.sets} 组`} />
        <SnapshotFact label='次数' value={exercise.reps} />
        <SnapshotFact label='休息' value={`${exercise.rest_seconds} 秒`} />
        <SnapshotFact label='重量' value={formatWeight(exercise.recommended_weight_kg)} />
      </View>
      <Text className='exercise-identity'>动作 ID：{exercise.exercise_id}</Text>
    </View>
  )
}

function ChangeSummary ({
  change,
  index
}: {
  change: PlanAdjustmentChange
  index: number
}) {
  const values = changeValues(change)
  return (
    <View className='change-summary'>
      <View className='change-heading'>
        <Text className='change-index'>{index + 1}</Text>
        <View className='change-copy'>
          <Text className='change-title'>{changeTitle(change)}</Text>
          <Text className='change-target'>位置：{change.stable_display_key}</Text>
        </View>
        {change.safety_priority && <Text className='safety-priority'>安全优先</Text>}
      </View>
      <View className='change-values'>
        <View className='change-value before'>
          <Text className='change-value-label'>原来</Text>
          <Text className='change-value-copy'>{values.before}</Text>
        </View>
        <Text className='change-arrow'>→</Text>
        <View className='change-value after'>
          <Text className='change-value-label'>建议</Text>
          <Text className='change-value-copy'>{values.after}</Text>
        </View>
      </View>
      <Text className='change-reason'>{change.reason}</Text>
    </View>
  )
}

function changeTitle (change: PlanAdjustmentChange): string {
  if (change.change_type === 'replace_exercise') return '替换训练动作'
  if (change.change_type === 'update_plan_schedule') return '调整计划节奏'
  return '调整动作目标'
}

function changeValues (change: PlanAdjustmentChange): {
  before: string
  after: string
} {
  if (change.change_type === 'replace_exercise') {
    return {
      before: change.before.exercise_name,
      after: change.after.exercise_name
    }
  }
  const labels: Record<string, string> = {
    sets: '组数',
    reps: '次数',
    rest_seconds: '休息秒数',
    recommended_weight_kg: '建议重量',
    duration_weeks: '计划周数',
    days_per_week: '每周天数'
  }
  return {
    before: Object.entries(change.before)
      .map(([key, value]) => `${labels[key] || key}：${formatChangeValue(key, value)}`)
      .join(' · '),
    after: Object.entries(change.after)
      .map(([key, value]) => `${labels[key] || key}：${formatChangeValue(key, value)}`)
      .join(' · ')
  }
}

function formatChangeValue (key: string, value: unknown): string {
  if (key === 'recommended_weight_kg') {
    return typeof value === 'number' ? `${value} kg` : '自重/未指定'
  }
  if (key === 'rest_seconds') return `${String(value)} 秒`
  if (key === 'duration_weeks') return `${String(value)} 周`
  if (key === 'days_per_week') return `${String(value)} 天`
  if (key === 'sets') return `${String(value)} 组`
  return String(value)
}

function formatWeight (value: number | null): string {
  return value === null ? '自重/未指定' : `${value} kg`
}

function formatDateTime (value: string): string {
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return value
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
