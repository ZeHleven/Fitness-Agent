import { useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { clearTokens } from '../../core/storage'
import { profileApi } from '../../services/profile'
import type { UserProfile, WeightLog } from '../../types/api'
import './index.scss'

export default function MePage () {
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [weights, setWeights] = useState<WeightLog[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [nextProfile, nextWeights] = await Promise.all([
        profileApi.get(),
        profileApi.weightHistory()
      ])
      setProfile(nextProfile)
      setWeights(nextWeights)
    } catch (requestError) {
      setError(errorMessage(requestError, '个人资料加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useDidShow(() => { void load() })

  const logout = async () => {
    const result = await Taro.showModal({
      title: '退出登录？',
      content: '本机保存的登录状态将被清除。'
    })
    if (!result.confirm) return
    clearTokens()
    await Taro.reLaunch({ url: '/pages/login/index' })
  }

  return (
    <View className='page me-page'>
      <Text className='me-eyebrow'>训练资料中心</Text>
      <Text className='me-title'>我的</Text>
      {error && <View className='error-banner'>{error}</View>}
      {loading && <View className='loading-state'>正在加载个人资料…</View>}
      {!loading && profile && (
        <>
          <View className='card profile-summary'>
            <View className='profile-mark'>我</View>
            <View className='profile-copy'>
              <Text className='profile-primary'>{goalLabel(profile.primary_goal)}</Text>
              <Text className='profile-secondary'>
                {profile.experience_level ? experienceLabel(profile.experience_level) : '训练经验待完善'}
                {' · '}{profile.training_location ? locationLabel(profile.training_location) : '地点待完善'}
              </Text>
            </View>
          </View>

          <View className='metric-row'>
            <Metric value={profile.weight_kg == null ? '—' : `${profile.weight_kg} kg`} label='当前体重' />
            <Metric value={profile.bmi == null ? '—' : profile.bmi.toFixed(1)} label='BMI' />
            <Metric value={weights.length} label='体重记录' />
          </View>

          <View className='card me-menu'>
            <MenuItem
              title='个人档案'
              subtitle='基础资料、目标和训练偏好'
              onClick={() => Taro.navigateTo({ url: '/pages/profile-edit/index' })}
            />
            <MenuItem
              title='健康与饮食偏好'
              subtitle={`${profile.injuries.length} 项伤病 · ${profile.chronic_conditions.length} 项慢性情况`}
              onClick={() => Taro.navigateTo({ url: '/pages/health-edit/index' })}
            />
            <MenuItem
              title='体重记录'
              subtitle='记录当前体重并查看变化'
              onClick={() => Taro.navigateTo({ url: '/pages/weight/index' })}
              last
            />
          </View>
          <Button className='danger-button logout-button' onClick={logout}>退出登录</Button>
        </>
      )}
    </View>
  )
}

function Metric ({ value, label }: { value: string | number, label: string }) {
  return (
    <View className='me-metric'>
      <Text className='me-metric-value'>{value}</Text>
      <Text className='me-metric-label'>{label}</Text>
    </View>
  )
}

function MenuItem ({
  title,
  subtitle,
  onClick,
  last = false
}: {
  title: string
  subtitle: string
  onClick: () => void
  last?: boolean
}) {
  return (
    <View className={`menu-item ${last ? 'last' : ''}`} onClick={onClick}>
      <View>
        <Text className='menu-title'>{title}</Text>
        <Text className='menu-subtitle'>{subtitle}</Text>
      </View>
      <Text className='menu-arrow'>›</Text>
    </View>
  )
}

function goalLabel (value?: string | null): string {
  return ({
    fat_loss: '减脂', muscle_gain: '增肌', strength: '力量提升',
    endurance: '耐力提升', flexibility: '灵活性改善', general_fitness: '综合体能'
  } as Record<string, string>)[value || ''] || '训练目标待完善'
}

function experienceLabel (value: string): string {
  return ({ beginner: '新手', intermediate: '进阶', advanced: '熟练' } as Record<string, string>)[value] || value
}

function locationLabel (value: string): string {
  return ({ gym: '健身房', home: '居家', outdoor: '户外' } as Record<string, string>)[value] || value
}
