import { useState } from 'react'
import { Button, Input, Picker, Slider, Text, View } from '@tarojs/components'
import Taro, { useLoad } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { profileApi } from '../../services/profile'
import { workoutApi } from '../../services/workouts'
import type {
  PersonalizedPlanExercise,
  PersonalizedPlanPreview
} from '../../types/api'
import './index.scss'

const weekday = (day: number) => ['一', '二', '三', '四', '五', '六', '日'][day - 1] || day

export default function PlanBuilderPage () {
  const [preview, setPreview] = useState<PersonalizedPlanPreview | null>(null)
  const [daysPerWeek, setDaysPerWeek] = useState(3)
  const [sessionDuration, setSessionDuration] = useState(45)
  const [loading, setLoading] = useState(true)
  const [generating, setGenerating] = useState(false)
  const [savingMode, setSavingMode] = useState<'save' | 'start' | ''>('')
  const [error, setError] = useState('')

  const generate = async (days: number, duration: number, initial = false) => {
    if (initial) setLoading(true)
    else setGenerating(true)
    setError('')
    try {
      const data = await workoutApi.previewPersonalizedPlan({
        days_per_week: days,
        session_duration_min: duration,
        duration_weeks: 4
      })
      setPreview(data)
      setDaysPerWeek(data.days_per_week)
      setSessionDuration(data.session_duration_min)
    } catch (requestError) {
      setError(errorMessage(requestError, '暂时无法生成训练计划'))
    } finally {
      setLoading(false)
      setGenerating(false)
    }
  }

  useLoad(() => {
    void (async () => {
      try {
        const profile = await profileApi.get()
        if (!profile.onboarding_completed) {
          await Taro.reLaunch({ url: '/pages/onboarding/index' })
          return
        }
        await generate(
          profile.training_days_per_week || 3,
          profile.session_duration_min || 45,
          true
        )
      } catch (requestError) {
        setError(errorMessage(requestError, '训练档案加载失败'))
        setLoading(false)
      }
    })()
  })

  const patchExercise = (index: number, patch: Partial<PersonalizedPlanExercise>) => {
    setPreview(current => current
      ? {
          ...current,
          exercises: current.exercises.map((item, itemIndex) => (
            itemIndex === index ? { ...item, ...patch } : item
          ))
        }
      : current)
  }

  const replaceExercise = async (index: number, optionIndex: number) => {
    if (!preview) return
    const option = preview.exercise_options[optionIndex]
    const current = preview.exercises[index]
    if (!option || !current) return
    const duplicate = preview.exercises.some((item, itemIndex) => (
      itemIndex !== index &&
      item.day_of_week === current.day_of_week &&
      item.exercise_id === option.exercise_id
    ))
    if (duplicate) {
      await Taro.showToast({ title: '同一天已经安排了这个动作', icon: 'none' })
      return
    }
    patchExercise(index, {
      exercise_id: option.exercise_id,
      exercise_name: option.exercise_name,
      category: option.category
    })
  }

  const removeExercise = async (index: number) => {
    if (!preview) return
    const target = preview.exercises[index]
    const sameDay = preview.exercises.filter(item => item.day_of_week === target.day_of_week)
    if (sameDay.length <= 1) {
      await Taro.showToast({ title: '每个训练日至少保留一个动作', icon: 'none' })
      return
    }
    setPreview({
      ...preview,
      exercises: preview.exercises.filter((_, itemIndex) => itemIndex !== index)
    })
  }

  const confirm = async (startImmediately: boolean) => {
    if (!preview) return
    const actualDays = new Set(preview.exercises.map(item => item.day_of_week))
    if (actualDays.size !== preview.days_per_week) {
      setError('每个训练日至少需要保留一个动作')
      return
    }
    if (preview.exercises.some(item => !item.reps.trim())) {
      setError('请补全每个动作的目标次数')
      return
    }

    setSavingMode(startImmediately ? 'start' : 'save')
    setError('')
    const normalized = normalizeOrder(preview)
    try {
      const plan = await workoutApi.confirmPersonalizedPlan(normalized)
      if (!startImmediately) {
        await Taro.showToast({ title: '计划已保存', icon: 'success' })
        await Taro.reLaunch({ url: '/pages/workouts/index' })
        return
      }

      const firstDay = Math.min(...plan.exercises.map(item => item.day_of_week))
      try {
        await workoutApi.start(plan.id, firstDay)
        await Taro.redirectTo({ url: '/pages/workout-active/index' })
      } catch (startError) {
        await Taro.showModal({
          title: '计划已保存',
          content: errorMessage(startError, '暂时无法开始训练，可稍后从计划页启动。'),
          showCancel: false
        })
        await Taro.reLaunch({ url: '/pages/workouts/index' })
      }
    } catch (requestError) {
      setError(errorMessage(requestError, '计划保存失败，请检查后重试'))
    } finally {
      setSavingMode('')
    }
  }

  if (loading) return <View className='loading-state'>正在读取档案并编排训练…</View>

  return (
    <View className='page plan-builder-page'>
      <View className='builder-heading'>
        <Text className='builder-kicker'>你的首份个性化方案</Text>
        <Text className='builder-title'>{(preview && preview.name) || '训练计划'}</Text>
        <Text className='builder-subtitle'>先预览和调整，确认后才会保存。</Text>
      </View>

      {error && <View className='error-banner'>{error}</View>}

      <View className='card preference-card'>
        <View className='section-row'>
          <Text className='section-title'>训练节奏</Text>
          <Text className='section-value'>每周 {daysPerWeek} 天 · {sessionDuration} 分钟</Text>
        </View>
        <Text className='slider-label'>每周训练天数</Text>
        <Slider
          min={1}
          max={7}
          step={1}
          value={daysPerWeek}
          activeColor='#1d6b49'
          backgroundColor='#dfe8e0'
          blockSize={22}
          onChange={event => setDaysPerWeek(event.detail.value)}
        />
        <Text className='slider-label'>单次训练时长</Text>
        <Slider
          min={20}
          max={120}
          step={5}
          value={sessionDuration}
          activeColor='#1d6b49'
          backgroundColor='#dfe8e0'
          blockSize={22}
          onChange={event => setSessionDuration(event.detail.value)}
        />
        <Button
          className='secondary-button regenerate-button'
          disabled={generating || Boolean(savingMode)}
          onClick={() => generate(daysPerWeek, sessionDuration)}
        >
          {generating ? '正在重新编排…' : '按新节奏重新编排'}
        </Button>
      </View>

      {preview && (
        <>
          <View className='card rationale-card'>
            <Text className='section-title'>为什么这样安排</Text>
            {preview.rationale.map(item => <Text className='rationale-line' key={item}>• {item}</Text>)}
          </View>

          <Text className='plan-section-heading'>四周训练安排</Text>
          {[...new Set(preview.exercises.map(item => item.day_of_week))].sort().map(day => (
            <View className='card day-card' key={day}>
              <View className='day-heading'>
                <View>
                  <Text className='day-title'>周{weekday(day)}训练</Text>
                  <Text className='day-meta'>{preview.exercises.filter(item => item.day_of_week === day).length} 个动作</Text>
                </View>
                <Text className='day-duration'>约 {preview.session_duration_min} 分钟</Text>
              </View>
              {preview.exercises.map((exercise, index) => exercise.day_of_week === day && (
                <View className='exercise-editor' key={`${day}-${exercise.exercise_id}-${index}`}>
                  <View className='exercise-topline'>
                    <View className='exercise-copy'>
                      <Text className='exercise-name'>{exercise.exercise_name}</Text>
                      <Text className='exercise-category'>{exercise.category}</Text>
                    </View>
                    <View className='remove-action' onClick={() => removeExercise(index)}>移除</View>
                  </View>

                  <Picker
                    mode='selector'
                    range={preview.exercise_options.map(item => item.exercise_name)}
                    onChange={event => replaceExercise(index, Number(event.detail.value))}
                  >
                    <View className='replace-action'>换一个安全动作 ›</View>
                  </Picker>

                  <View className='prescription-grid'>
                    <View className='prescription-field'>
                      <Text className='field-label'>组数</Text>
                      <View className='stepper'>
                        <View onClick={() => patchExercise(index, { sets: Math.max(1, exercise.sets - 1) })}>−</View>
                        <Text>{exercise.sets}</Text>
                        <View onClick={() => patchExercise(index, { sets: Math.min(8, exercise.sets + 1) })}>＋</View>
                      </View>
                    </View>
                    <View className='prescription-field'>
                      <Text className='field-label'>目标次数</Text>
                      <Input
                        className='reps-input'
                        value={exercise.reps}
                        maxlength={20}
                        onInput={event => patchExercise(index, { reps: event.detail.value })}
                      />
                    </View>
                    <View className='prescription-field'>
                      <Text className='field-label'>休息</Text>
                      <View className='stepper rest-stepper'>
                        <View onClick={() => patchExercise(index, { rest_seconds: Math.max(15, exercise.rest_seconds - 15) })}>−</View>
                        <Text>{exercise.rest_seconds}s</Text>
                        <View onClick={() => patchExercise(index, { rest_seconds: Math.min(600, exercise.rest_seconds + 15) })}>＋</View>
                      </View>
                    </View>
                  </View>
                </View>
              ))}
            </View>
          ))}

          <View className='safety-card'>
            <Text className='safety-title'>训练安全提示</Text>
            {preview.safety_notes.map(item => <Text className='safety-line' key={item}>• {item}</Text>)}
          </View>

          <View className='builder-actions'>
            <Button
              className='secondary-button save-button'
              disabled={Boolean(savingMode)}
              onClick={() => confirm(false)}
            >
              {savingMode === 'save' ? '保存中…' : '仅保存计划'}
            </Button>
            <Button
              className='primary-button start-now-button'
              disabled={Boolean(savingMode)}
              onClick={() => confirm(true)}
            >
              {savingMode === 'start' ? '正在启动…' : '确认并开始第一练'}
            </Button>
          </View>
        </>
      )}
    </View>
  )
}

function normalizeOrder (preview: PersonalizedPlanPreview): PersonalizedPlanPreview {
  const orderByDay = new Map<number, number>()
  return {
    ...preview,
    exercises: preview.exercises.map(item => {
      const order = orderByDay.get(item.day_of_week) || 0
      orderByDay.set(item.day_of_week, order + 1)
      return { ...item, order_index: order }
    })
  }
}
