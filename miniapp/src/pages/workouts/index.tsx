import { useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { workoutApi } from '../../services/workouts'
import { profileApi } from '../../services/profile'
import { planManagementApi } from '../../services/plan-management'
import type { WorkoutPlan, WorkoutProgress, WorkoutSession } from '../../types/api'
import './index.scss'

const dayLabel = (day: number) => ['一', '二', '三', '四', '五', '六', '日'][day - 1] || day

export default function WorkoutsPage () {
  const [plans, setPlans] = useState<WorkoutPlan[]>([])
  const [active, setActive] = useState<WorkoutSession | null>(null)
  const [progress, setProgress] = useState<WorkoutProgress | null>(null)
  const [loading, setLoading] = useState(true)
  const [startingKey, setStartingKey] = useState('')
  const [deletingPlanId, setDeletingPlanId] = useState('')
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const userProfile = await profileApi.get()
      if (!userProfile.onboarding_completed) {
        await Taro.reLaunch({ url: '/pages/onboarding/index' })
        return
      }
      const [planData, activeData, progressData] = await Promise.all([
        workoutApi.plans(),
        workoutApi.active(),
        workoutApi.progress()
      ])
      setPlans(planData)
      setActive(activeData)
      setProgress(progressData)
    } catch (requestError) {
      setError(errorMessage(requestError, '训练数据加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useDidShow(() => {
    void load()
  })

  const start = async (planId: string, day: number) => {
    const key = `${planId}-${day}`
    setStartingKey(key)
    setError('')
    try {
      await workoutApi.start(planId, day)
      await Taro.navigateTo({ url: '/pages/workout-active/index' })
    } catch (requestError) {
      setError(errorMessage(requestError, '无法开始训练'))
    } finally {
      setStartingKey('')
    }
  }

  const activePlan = plans.find(plan => plan.is_active)

  const proposeDeletion = async (planId: string) => {
    const answer = await Taro.showModal({
      title: '删除当前训练计划？',
      content: '下一步会先生成删除提案；还需要在提案详情中再次确认才会永久删除计划。'
    })
    if (!answer.confirm) return
    setDeletingPlanId(planId)
    setError('')
    try {
      const context = await planManagementApi.editContext(planId)
      const proposal = await planManagementApi.createDeletion(
        planId,
        context.base_plan_fingerprint
      )
      await Taro.navigateTo({
        url: `/pages/plan-proposal-detail/index?id=${encodeURIComponent(proposal.id)}`
      })
    } catch (requestError) {
      setError(errorMessage(requestError, '删除提案创建失败'))
    } finally {
      setDeletingPlanId('')
    }
  }

  return (
    <View className='page workouts-page'>
      <View className='page-heading'>
        <View>
          <Text className='eyebrow'>今天也要稳稳进步</Text>
          <Text className='page-title'>开始训练</Text>
        </View>
      </View>

      {error && <View className='error-banner'>{error}</View>}

      <View
        className='agent-entry card'
        onClick={() => Taro.switchTab({ url: '/pages/agent/index' })}
      >
        <View className='agent-entry-mark'>练</View>
        <View className='agent-entry-copy'>
          <Text className='agent-entry-title'>问问训练搭子</Text>
          <Text className='agent-entry-subtitle'>查计划、下一练、训练记录和进度</Text>
        </View>
        <Text className='agent-entry-action'>对话 →</Text>
      </View>

      {active && (
        <View className='active-card card' onClick={() => Taro.navigateTo({ url: '/pages/workout-active/index' })}>
          <View>
            <Text className='active-tag'>进行中</Text>
            <Text className='active-title'>{active.plan_name || '本次训练'}</Text>
            <Text className='active-meta'>{active.total_sets} 组 · {active.exercises.length} 个动作</Text>
          </View>
          <Text className='active-action'>继续 →</Text>
        </View>
      )}

      {progress && (
        <View className='progress-card card'>
          <View className='section-row'>
            <Text className='section-title'>近 {progress.weeks} 周</Text>
            <View className='history-link' onClick={() => Taro.navigateTo({ url: '/pages/history/index' })}>训练历史 →</View>
          </View>
          <View className='metric-grid'>
            <Metric value={progress.total_sessions} label='次训练' />
            <Metric value={progress.total_sets} label='完成组' />
            <Metric value={formatVolume(progress.total_volume_kg)} label='kg 训练量' />
          </View>
        </View>
      )}

      <Text className='section-heading'>我的计划</Text>
      {loading && <View className='loading-state'>正在加载训练计划…</View>}
      {!loading && !activePlan && (
        <View className='card empty-state plan-empty'>
          <Text>当前没有活动训练计划，先生成一份个性化计划再开始记录。</Text>
          <Button
            className='primary-button generate-button'
            onClick={() => Taro.navigateTo({ url: '/pages/plan-builder/index' })}
          >
            生成我的个性化计划
          </Button>
        </View>
      )}
      {!loading && plans.map(plan => {
        const days = [...new Set(plan.exercises.map(item => item.day_of_week))].sort()
        return (
          <View className='plan-card card' key={plan.id}>
            <View className='plan-heading'>
              <View>
                <Text className='plan-name'>{plan.name}</Text>
                <Text className='plan-meta'>{plan.duration_weeks} 周 · 每周 {plan.days_per_week} 天</Text>
              </View>
              {plan.is_active
                ? plan.ai_generated && <Text className='ai-tag'>智能</Text>
                : <Text className='archived-tag'>已归档</Text>}
            </View>
            {plan.is_active && plan.safety_status === 'needs_review' && (
              <View className='plan-safety-warning'>
                <Text className='plan-safety-title'>计划需复核</Text>
                {plan.safety_reasons.map(reason => (
                  <Text className='plan-safety-reason' key={reason}>· {reason}</Text>
                ))}
              </View>
            )}
            {plan.is_active && plan.manual_proposals_enabled && (
              <View className='plan-actions'>
                <Button
                  className='edit-plan-button'
                  size='mini'
                  onClick={() => Taro.navigateTo({
                    url: `/pages/plan-editor/index?id=${encodeURIComponent(plan.id)}`
                  })}
                >
                  编辑计划
                </Button>
                <Button
                  className='delete-plan-button'
                  size='mini'
                  disabled={Boolean(deletingPlanId)}
                  onClick={() => proposeDeletion(plan.id)}
                >
                  {deletingPlanId === plan.id ? '创建提案中…' : '删除计划'}
                </Button>
              </View>
            )}
            {days.map(day => {
              const exercises = plan.exercises.filter(item => item.day_of_week === day)
              const key = `${plan.id}-${day}`
              return (
                <View className='training-day' key={key}>
                  <View className='day-summary'>
                    <Text className='day-title'>周{dayLabel(day)}</Text>
                    <Text className='day-exercises'>{exercises.map(item => item.exercise_name || '未命名动作').join(' · ')}</Text>
                  </View>
                  <Button
                    className='start-button'
                    size='mini'
                    disabled={
                      !plan.is_active ||
                      plan.safety_status === 'needs_review' ||
                      Boolean(active) ||
                      Boolean(startingKey)
                    }
                    onClick={() => start(plan.id, day)}
                  >
                    {!plan.is_active
                      ? '已归档'
                      : plan.safety_status === 'needs_review'
                        ? '待复核'
                        : startingKey === key ? '启动中' : '开始'}
                  </Button>
                </View>
              )
            })}
          </View>
        )
      })}
    </View>
  )
}

function Metric ({ value, label }: { value: string | number, label: string }) {
  return (
    <View className='metric'>
      <Text className='metric-value'>{value}</Text>
      <Text className='metric-label'>{label}</Text>
    </View>
  )
}

function formatVolume (value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`
  return Math.round(value).toString()
}
