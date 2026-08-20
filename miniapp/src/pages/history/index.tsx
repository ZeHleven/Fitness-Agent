import { useState } from 'react'
import { Text, View } from '@tarojs/components'
import { useDidShow } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { workoutApi } from '../../services/workouts'
import type { WorkoutProgress, WorkoutSession } from '../../types/api'
import './index.scss'

export default function HistoryPage () {
  const [history, setHistory] = useState<WorkoutSession[]>([])
  const [progress, setProgress] = useState<WorkoutProgress | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [historyData, progressData] = await Promise.all([
        workoutApi.history(),
        workoutApi.progress()
      ])
      setHistory(historyData.filter(item => item.status === 'completed'))
      setProgress(progressData)
    } catch (requestError) {
      setError(errorMessage(requestError, '训练历史加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useDidShow(() => {
    void load()
  })

  const maxVolume = Math.max(1, ...(progress ? progress.weekly.map(item => item.volume_kg) : [1]))

  return (
    <View className='page history-page'>
      <Text className='history-eyebrow'>长期进步来自每一次完成</Text>
      <Text className='history-title'>训练历史</Text>

      {error && <View className='error-banner'>{error}</View>}
      {loading && <View className='loading-state'>正在整理训练记录…</View>}

      {progress && (
        <View className='card trend-card'>
          <Text className='trend-title'>近 {progress.weeks} 周训练量</Text>
          <View className='trend-chart'>
            {progress.weekly.map(week => (
              <View className='week-column' key={week.week_start}>
                <View className='bar-slot'>
                  <View
                    className='volume-bar'
                    style={{ height: `${Math.max(4, week.volume_kg / maxVolume * 100)}%` }}
                  />
                </View>
                <Text className='week-label'>{shortDate(week.week_start)}</Text>
              </View>
            ))}
          </View>
          <View className='history-summary'>
            <Summary value={progress.total_sessions} label='次训练' />
            <Summary value={progress.total_sets} label='组' />
            <Summary value={Math.round(progress.total_volume_kg)} label='kg' />
          </View>
        </View>
      )}

      {!loading && history.length === 0 && (
        <View className='card empty-state'>完成第一场训练后，记录和趋势会出现在这里。</View>
      )}

      {history.map(session => {
        const records = session.exercises.reduce(
          (total, exercise) => total + exercise.sets_data.filter(set => set.is_personal_record).length,
          0
        )
        return (
          <View className='card history-card' key={session.id}>
            <View className='history-card-heading'>
              <View>
                <Text className='history-plan'>{session.plan_name || '自由训练'}</Text>
                <Text className='history-date'>{formatDate(session.trained_at)}</Text>
              </View>
              <View className='history-tags'>
                {records > 0 && <Text className='record-tag'>🏆 {records} 个纪录</Text>}
                {session.adjustments.length > 0 && <Text className='adjusted-tag'>已调整 {session.adjustments.length} 项</Text>}
              </View>
            </View>
            <View className='session-metrics'>
              <Text>{session.duration_min || 0} 分钟</Text>
              <Text>{session.total_sets} 组</Text>
              <Text>{session.total_reps} 次</Text>
              <Text>{Math.round(session.total_volume_kg)} kg</Text>
            </View>
            <View className='exercise-list'>
              {session.exercises.map(exercise => (
                <View className='exercise-line' key={exercise.id}>
                  <Text>{exercise.exercise_name || '未命名动作'}</Text>
                  <Text className='muted'>{exercise.sets_data.length} 组</Text>
                </View>
              ))}
            </View>
            {session.adjustments.length > 0 && (
              <View className='history-adjustments'>
                <Text className='history-adjustment-title'>下一练调整</Text>
                {session.adjustments.slice(0, 3).map((item, index) => (
                  <Text className='history-adjustment-line' key={`${item.exercise_id}-${index}`}>
                    {item.exercise_name}：{item.reason}
                  </Text>
                ))}
              </View>
            )}
          </View>
        )
      })}
    </View>
  )
}

function Summary ({ value, label }: { value: number, label: string }) {
  return (
    <View>
      <Text className='summary-value'>{value}</Text>
      <Text className='summary-label'>{label}</Text>
    </View>
  )
}

function shortDate (value: string): string {
  const [, month, day] = value.split('-')
  return `${Number(month)}/${Number(day)}`
}

function formatDate (value: string): string {
  const [year, month, day] = value.split('-')
  return `${year}年${Number(month)}月${Number(day)}日`
}
