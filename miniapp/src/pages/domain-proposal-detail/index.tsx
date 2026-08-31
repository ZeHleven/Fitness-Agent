import { useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidShow, useLoad } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { planManagementApi } from '../../services/plan-management'
import type { GenericProposalReadResponse } from '../../types/plan-management-proposal'
import './index.scss'

const labels: Record<string, string> = {
  age: '年龄', gender: '性别', height_cm: '身高', weight_kg: '体重',
  experience_level: '训练经验', primary_goal: '训练目标',
  training_days_per_week: '训练频率偏好', session_duration_min: '单次训练时长',
  training_location: '训练地点', diet_restriction: '饮食限制',
  injuries: '伤病情况', chronic_conditions: '慢性情况',
  current_weight_kg: '当前体重', recorded_at: '记录时间',
  logged_at: '日期', meal_type: '餐次', food_name: '食品', amount_g: '克数',
  calories: '热量', protein_g: '蛋白质', carbs_g: '碳水', fat_g: '脂肪',
  duration_weeks: '计划周期', days_per_week: '每周训练天数',
  name: '名称', goal: '目标'
}

export default function DomainProposalDetailPage () {
  const [proposalId, setProposalId] = useState('')
  const [proposal, setProposal] = useState<GenericProposalReadResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [reviewed, setReviewed] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [error, setError] = useState('')

  const load = async (id: string) => {
    setError('')
    try {
      const value = await planManagementApi.proposal(id)
      setProposal(value)
      if (value.status !== 'pending_confirmation') setReviewed(false)
    } catch (requestError) {
      setError(errorMessage(requestError, '提案读取失败'))
    } finally {
      setLoading(false)
    }
  }

  useLoad(options => {
    const id = typeof options.id === 'string' ? decodeURIComponent(options.id) : ''
    setProposalId(id)
    if (!id) {
      setError('缺少提案标识')
      setLoading(false)
      return
    }
    void load(id)
  })

  useDidShow(() => { if (proposalId && proposal) void load(proposalId) })

  const decide = async (action: 'confirm' | 'reject') => {
    if (!proposal || !reviewed || deciding) return
    const confirmed = await Taro.showModal({
      title: action === 'confirm' ? '确认执行这项变更？' : '拒绝这份提案？',
      content: action === 'confirm'
        ? '系统会再次校验数据版本和安全条件，成功后才会写入。'
        : '拒绝后不会修改任何数据。',
      confirmText: action === 'confirm' ? '确认执行' : '确认拒绝',
      confirmColor: action === 'confirm' ? '#1d6b49' : '#a13d31'
    })
    if (!confirmed.confirm) return
    setDeciding(true)
    setError('')
    try {
      const response = await planManagementApi[action](proposal.id, proposal.version)
      await Taro.showToast({
        title: response.status === 'applied' ? '变更已应用' : '提案已拒绝',
        icon: 'success'
      })
      await load(proposal.id)
    } catch (requestError) {
      setError(`${errorMessage(requestError, '提案操作结果尚未确定')}。请刷新后核对状态。`)
      await load(proposal.id)
    } finally {
      setDeciding(false)
    }
  }

  if (loading) return <View className='loading-state'>正在读取提案…</View>
  if (!proposal) return <View className='page domain-proposal-page'>{error && <View className='error-banner'>{error}</View>}</View>
  const payload = proposal.payload
  const before = objectValue(payload.before)
  const after = objectValue(payload.after)
  const pending = proposal.status === 'pending_confirmation'
  const safetyNotes = Array.isArray(payload.safety_notes) ? payload.safety_notes.map(String) : []

  return (
    <View className='page domain-proposal-page'>
      <View className={`proposal-status status-${proposal.status}`}>{statusLabel(proposal.status)}</View>
      <Text className='proposal-kicker'>{titleFor(proposal.proposal_type)}</Text>
      <Text className='proposal-title'>请核对变更前后内容</Text>
      <Text className='proposal-note'>有效期至 {formatTime(proposal.expires_at)}</Text>
      {error && <View className='error-banner'>{error}</View>}

      {before && <ProposalSection title='变更前' value={before} type={proposal.proposal_type} />}
      {after && <ProposalSection title='变更后' value={after} type={proposal.proposal_type} />}
      {!after && <View className='card deletion-card'>确认后将删除上方这条记录；此操作不会静默修改其他数据。</View>}

      {safetyNotes.length > 0 && (
        <View className='card safety-card'>
          <Text className='section-title'>安全与执行说明</Text>
          {safetyNotes.map((item, index) => <Text className='detail-line' key={index}>• {item}</Text>)}
        </View>
      )}

      <View className='decision-panel'>
        {pending ? (
          <>
            <View className={`review-row ${reviewed ? 'selected' : ''}`} onClick={() => !deciding && setReviewed(value => !value)}>
              <View className='check-box'>{reviewed ? '✓' : ''}</View>
              <Text>我已核对变更前后内容</Text>
            </View>
            <View className='decision-actions'>
              <Button className='secondary-button' disabled={!reviewed || deciding} onClick={() => decide('reject')}>拒绝</Button>
              <Button className='primary-button' disabled={!reviewed || deciding} onClick={() => decide('confirm')}>{deciding ? '提交中…' : '确认并执行'}</Button>
            </View>
          </>
        ) : <Text className='terminal-copy'>{terminalCopy(proposal.status)}</Text>}
        <Button className='refresh-button' disabled={deciding} onClick={() => load(proposal.id)}>刷新状态</Button>
      </View>
    </View>
  )
}

function ProposalSection ({ title, value, type }: { title: string, value: Record<string, unknown>, type: string }) {
  const planExercises = Array.isArray(value.exercises) ? value.exercises : []
  const mealItems = Array.isArray(value.items) ? value.items : []
  const hidden = new Set(['exercises', 'exercise_options', 'rationale', 'safety_notes', 'items'])
  return (
    <>
      <Text className='section-heading'>{title}</Text>
      <View className='card value-card'>
        {Object.entries(value).filter(([key]) => !hidden.has(key)).map(([key, item]) => (
          <ValueRow key={key} label={labels[key] || key} value={item} />
        ))}
        {mealItems.map((item, index) => <NestedItem key={index} value={objectValue(item) || {}} />)}
        {planExercises.length > 0 && <Text className='detail-line'>训练动作共 {planExercises.length} 项，将按提案中的训练日与顺序创建。</Text>}
        {type === 'weight_log_create_v1' && <Text className='detail-line'>确认后会同步更新个人档案中的当前体重和 BMI。</Text>}
      </View>
    </>
  )
}

function NestedItem ({ value }: { value: Record<string, unknown> }) {
  return (
    <View className='nested-item'>
      {Object.entries(value).filter(([key]) => key !== 'id' && key !== 'food_id').map(([key, item]) => (
        <ValueRow key={key} label={labels[key] || key} value={item} />
      ))}
    </View>
  )
}

function ValueRow ({ label, value }: { label: string, value: unknown }) {
  return (
    <View className='value-row'>
      <Text className='value-label'>{label}</Text>
      <Text className='value-text'>{display(value)}</Text>
    </View>
  )
}

function objectValue (value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}
function display (value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.map(item => typeof item === 'object' ? '详细项目' : String(item)).join('、') : '无'
  if (value == null || value === '') return '未设置'
  if (typeof value === 'object') return '详细内容'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}
function titleFor (type: string): string { return ({ plan_creation_v1: '训练计划创建提案', profile_update_v1: '个人档案与健康更新提案', weight_log_create_v1: '体重记录提案', meal_log_create_v1: '饮食记录提案', meal_log_delete_v1: '饮食删除提案' } as Record<string, string>)[type] || '数据变更提案' }
function statusLabel (status: GenericProposalReadResponse['status']): string { return ({ pending_confirmation: '待你确认', applied: '已应用', rejected: '已拒绝', expired: '已过期', stale: '已失效', failed: '执行失败' })[status] }
function terminalCopy (status: GenericProposalReadResponse['status']): string { if (status === 'applied') return '这项变更已经成功应用。'; if (status === 'rejected') return '提案已拒绝，数据没有变化。'; return '当前提案已经不能执行，请重新发起请求。' }
function formatTime (value: string): string { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, '0')}-${String(parsed.getDate()).padStart(2, '0')} ${String(parsed.getHours()).padStart(2, '0')}:${String(parsed.getMinutes()).padStart(2, '0')}` }
