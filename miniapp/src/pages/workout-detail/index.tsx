import { useRef, useState } from 'react'
import { Button, Text, View } from '@tarojs/components'
import Taro, { useLoad } from '@tarojs/taro'
import { workoutApi } from '../../services/workouts'
import { errorMessage } from '../../core/request'
import { actualRestLabel } from '../../core/training'
import type { WorkoutSession } from '../../types/api'
import SessionEnergyEditor from '../../components/SessionEnergyEditor'
import './index.scss'

export default function WorkoutDetailPage () {
  const [session, setSession] = useState<WorkoutSession | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const id = useRef('')
  const generation = useRef(0)
  const load = async () => {
    const ticket = ++generation.current
    if (!id.current) { setError('缺少训练记录标识，请从训练历史重新打开'); return }
    setLoading(true); setError('')
    try {
      const data = await workoutApi.detail(id.current)
      if (ticket === generation.current) setSession(data)
    } catch (e) { if (ticket === generation.current) setError(errorMessage(e, '训练记录加载失败')) }
    finally { if (ticket === generation.current) setLoading(false) }
  }
  useLoad(options => { id.current = options.id || ''; void load() })
  return <View className='page workout-detail-page'>
    {error && <View className='error-banner'>{error}<Button className='secondary-button detail-retry' onClick={load}>重试</Button></View>}
    {loading && <View className='loading-state'>正在加载本次训练…</View>}
    {session && <>
      <View className='card detail-heading'>
        <Text className='detail-state'>{session.status === 'completed' ? '已完成' : session.status === 'ended_early' ? '提前结束 · 已保留记录' : '进行中'}</Text>
        <Text className='page-title'>{session.plan_name || '本次训练'}</Text>
        <Text>{session.trained_at} · {session.duration_min || 0} 分钟</Text>
        <Text>{session.total_sets} 组 · {session.total_reps} 次 · {session.total_volume_kg} kg 训练量</Text>
      </View>
      {session.exercises.map(exercise => <View className='card detail-exercise' key={exercise.id}>
        <Text className='section-title'>{exercise.exercise_name || '未命名动作'}</Text>
        <Text className='detail-note'>计划 {exercise.target_sets || 0} 组 × {exercise.target_reps || '—'} 次 · 目标休息 {exercise.rest_seconds ?? '—'} 秒</Text>
        <Text>实际记录 {exercise.sets_data.length} 组</Text>
        {exercise.safety_notice && <Text className='detail-note'>{exercise.safety_notice}</Text>}
        {exercise.sets_data.length === 0 && <Text className='detail-note'>本动作未记录组数</Text>}
        {exercise.sets_data.map((set, index) => <View className='detail-set' key={set.set_number || index + 1}>
          <Text>第 {set.set_number || index + 1} 组 · {set.reps} 次 · {set.weight_kg == null ? '未记录重量' : `${set.weight_kg} kg`}</Text>
          <Text className='detail-note'>本组后实际休息：{actualRestLabel(set)}</Text>
        </View>)}
      </View>)}
      <SessionEnergyEditor key={session.id} session={session} onSaved={setSession} />
      <View className='card detail-notes'><Text className='detail-note'>实际休息由计时开始至点击“结束休息，开始下一组”记录，不包含下一组动作耗时。历史未记录的休息不会估算补填。</Text>{session.feedback?.feedback_notes && <Text>训练反馈：{session.feedback.feedback_notes}</Text>}{session.notes && <Text>备注：{session.notes}</Text>}</View>
      {session.adaptive_adjustment_proposal && <Button className='secondary-button detail-proposal' onClick={() => Taro.navigateTo({ url: `/pages/plan-proposal-detail/index?id=${encodeURIComponent(session.adaptive_adjustment_proposal!.id)}` })}>查看调整提案</Button>}
    </>}
  </View>
}
