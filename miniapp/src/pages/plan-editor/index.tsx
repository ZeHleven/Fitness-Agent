import { useState } from 'react'
import { Button, Input, Picker, Slider, Text, View } from '@tarojs/components'
import Taro, { useLoad } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { planManagementApi } from '../../services/plan-management'
import type {
  PlanCandidateV2,
  PlanEditContext,
  PlanExerciseSnapshotV2
} from '../../types/plan-management-proposal'
import './index.scss'

const weekday = (day: number) => ['一', '二', '三', '四', '五', '六', '日'][day - 1]
let newItemCounter = 0

export default function PlanEditorPage () {
  const [planId, setPlanId] = useState('')
  const [context, setContext] = useState<PlanEditContext | null>(null)
  const [duration, setDuration] = useState(4)
  const [trainingDays, setTrainingDays] = useState<number[]>([])
  const [exercises, setExercises] = useState<PlanExerciseSnapshotV2[]>([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useLoad(options => {
    const id = typeof options.id === 'string' ? decodeURIComponent(options.id) : ''
    setPlanId(id)
    if (!id) {
      setError('缺少训练计划标识')
      return
    }
    void planManagementApi.editContext(id).then(value => {
      setContext(value)
      setDuration(value.base_plan.duration_weeks)
      setTrainingDays(value.base_plan.training_days)
      setExercises(value.base_plan.exercises)
      Taro.enableAlertBeforeUnload({ message: '尚未保存为提案，确定离开计划编辑器吗？' })
    }).catch(requestError => setError(errorMessage(requestError, '计划编辑器加载失败')))
  })

  const patchExercise = (itemKey: string, patch: Partial<PlanExerciseSnapshotV2>) => {
    setExercises(current => normalizeOrder(current.map(item => (
      item.item_key === itemKey ? { ...item, ...patch } : item
    ))))
  }

  const moveOrder = (item: PlanExerciseSnapshotV2, direction: -1 | 1) => {
    const dayItems = exercises
      .filter(value => value.day_of_week === item.day_of_week)
      .sort((left, right) => left.order_index - right.order_index)
    const index = dayItems.findIndex(value => value.item_key === item.item_key)
    const target = dayItems[index + direction]
    if (!target) return
    setExercises(current => normalizeOrder(current.map(value => {
      if (value.item_key === item.item_key) return { ...value, order_index: target.order_index }
      if (value.item_key === target.item_key) return { ...value, order_index: item.order_index }
      return value
    })))
  }

  const toggleDay = async (day: number) => {
    if (trainingDays.includes(day)) {
      if (exercises.some(item => item.day_of_week === day)) {
        setError(`请先移动或删除周${weekday(day)}的动作，再移除该训练日`)
        return
      }
      if (trainingDays.length === 1) {
        setError('计划至少需要一个训练日')
        return
      }
      setTrainingDays(current => current.filter(value => value !== day))
      return
    }
    setTrainingDays(current => [...current, day].sort())
    setError('')
  }

  const addExercise = (day: number, optionIndex: number) => {
    if (!context) return
    const option = context.exercise_options[optionIndex]
    if (exercises.some(item => item.day_of_week === day && item.exercise_id === option.exercise_id)) {
      setError(`周${weekday(day)}已经包含${option.exercise_name}`)
      return
    }
    newItemCounter += 1
    setExercises(current => normalizeOrder([...current, {
      item_key: `new:${Date.now().toString(36)}-${newItemCounter}`,
      exercise_id: option.exercise_id,
      exercise_name: option.exercise_name,
      category: option.category,
      day_of_week: day,
      sets: 3,
      reps: '8-12',
      rest_seconds: 90,
      recommended_weight_kg: null,
      order_index: current.filter(item => item.day_of_week === day).length
    }]))
    setError('')
  }

  const replaceExercise = (item: PlanExerciseSnapshotV2, optionIndex: number) => {
    if (!context) return
    const option = context.exercise_options[optionIndex]
    if (exercises.some(value => (
      value.item_key !== item.item_key &&
      value.day_of_week === item.day_of_week &&
      value.exercise_id === option.exercise_id
    ))) {
      setError(`周${weekday(item.day_of_week)}已经包含${option.exercise_name}`)
      return
    }
    patchExercise(item.item_key, {
      exercise_id: option.exercise_id,
      exercise_name: option.exercise_name,
      category: option.category
    })
    setError('')
  }

  const candidate = (): PlanCandidateV2 => ({
    duration_weeks: duration,
    training_days: [...trainingDays].sort(),
    exercises: normalizeOrder(exercises).map(item => ({
      item_key: item.item_key,
      exercise_id: item.exercise_id,
      day_of_week: item.day_of_week,
      sets: item.sets,
      reps: item.reps.trim(),
      rest_seconds: item.rest_seconds,
      recommended_weight_kg: item.recommended_weight_kg,
      order_index: item.order_index
    }))
  })

  const save = async () => {
    if (!context || !planId) return
    const emptyDay = trainingDays.find(day => !exercises.some(item => item.day_of_week === day))
    if (emptyDay) {
      setError(`请为周${weekday(emptyDay)}添加至少一个动作`)
      return
    }
    if (exercises.some(item => !item.reps.trim())) {
      setError('动作次数不能为空')
      return
    }
    setSaving(true)
    setError('')
    try {
      const proposal = await planManagementApi.createAdjustment(
        planId,
        context.base_plan_fingerprint,
        candidate()
      )
      Taro.disableAlertBeforeUnload()
      await Taro.navigateTo({
        url: `/pages/plan-proposal-detail/index?id=${encodeURIComponent(proposal.id)}`
      })
    } catch (requestError) {
      setError(errorMessage(requestError, '调整提案创建失败'))
    } finally {
      setSaving(false)
    }
  }

  if (!context && !error) return <View className='loading-state'>正在加载完整训练计划…</View>

  return (
    <View className='page plan-editor-page'>
      <Text className='editor-title'>编辑训练计划</Text>
      <Text className='editor-subtitle'>保存后先生成前后对比提案；只有再次确认才会切换活动计划。</Text>
      {error && <View className='error-banner'>{error}</View>}
      {context && (
        <>
          {!context.proposals_enabled && <View className='feature-warning'>手动计划提案功能当前未启用，暂时只能查看草稿。</View>}
          {context.active_session && <View className='session-notice'>本次进行中的训练沿用原目标，新计划从下一次训练生效。</View>}

          <View className='card schedule-card'>
            <Text className='section-title'>计划周期：{duration} 周</Text>
            <Slider min={2} max={12} step={1} value={duration} activeColor='#1d6b49' onChange={event => setDuration(event.detail.value)} />
            <Text className='section-title days-title'>每周训练日</Text>
            <View className='weekday-row'>
              {[1, 2, 3, 4, 5, 6, 7].map(day => (
                <View className={`weekday ${trainingDays.includes(day) ? 'selected' : ''}`} key={day} onClick={() => toggleDay(day)}>周{weekday(day)}</View>
              ))}
            </View>
          </View>

          {trainingDays.map(day => {
            const dayExercises = exercises
              .filter(item => item.day_of_week === day)
              .sort((left, right) => left.order_index - right.order_index)
            return (
              <View className='card day-card' key={day}>
                <View className='day-heading'>
                  <Text className='day-title'>周{weekday(day)}</Text>
                  <Text className='day-count'>{dayExercises.length} 个动作</Text>
                </View>
                {dayExercises.map((item, index) => (
                  <View className='exercise-editor' key={item.item_key}>
                    <View className='exercise-heading'>
                      <View>
                        <Text className='exercise-name'>{item.exercise_name}</Text>
                        <Text className='exercise-category'>{item.category}</Text>
                      </View>
                      <Text className='remove-exercise' onClick={() => setExercises(current => normalizeOrder(current.filter(value => value.item_key !== item.item_key)))}>删除</Text>
                    </View>

                    <View className='exercise-actions'>
                      <Button size='mini' disabled={index === 0} onClick={() => moveOrder(item, -1)}>上移</Button>
                      <Button size='mini' disabled={index === dayExercises.length - 1} onClick={() => moveOrder(item, 1)}>下移</Button>
                      <Picker mode='selector' range={trainingDays.map(value => `周${weekday(value)}`)} value={trainingDays.indexOf(item.day_of_week)} onChange={event => patchExercise(item.item_key, { day_of_week: trainingDays[Number(event.detail.value)], order_index: exercises.filter(value => value.day_of_week === trainingDays[Number(event.detail.value)]).length })}>
                        <View className='mini-picker'>移动到…</View>
                      </Picker>
                      <Picker mode='selector' range={context.exercise_options.map(option => option.exercise_name)} onChange={event => replaceExercise(item, Number(event.detail.value))}>
                        <View className='mini-picker'>替换动作</View>
                      </Picker>
                    </View>

                    <View className='target-grid'>
                      <Stepper label='组数' value={item.sets} min={1} max={8} onChange={value => patchExercise(item.item_key, { sets: value })} />
                      <View className='target-field'><Text>次数</Text><Input value={item.reps} onInput={event => patchExercise(item.item_key, { reps: event.detail.value })} /></View>
                      <Stepper label='休息秒' value={item.rest_seconds} min={15} max={600} step={15} onChange={value => patchExercise(item.item_key, { rest_seconds: value })} />
                      <View className='target-field'><Text>建议重量 kg</Text><Input type='digit' value={item.recommended_weight_kg == null ? '' : String(item.recommended_weight_kg)} placeholder='未指定' onInput={event => patchExercise(item.item_key, { recommended_weight_kg: event.detail.value.trim() === '' ? null : Number(event.detail.value) })} /></View>
                    </View>
                  </View>
                ))}
                <Picker mode='selector' range={context.exercise_options.map(option => `${option.exercise_name} · ${option.category}`)} onChange={event => addExercise(day, Number(event.detail.value))}>
                  <View className='add-exercise'>＋ 添加安全兼容动作</View>
                </Picker>
              </View>
            )
          })}

          <Button className='primary-button save-proposal' loading={saving} disabled={saving || !context.proposals_enabled} onClick={save}>保存并查看调整提案</Button>
        </>
      )}
    </View>
  )
}

function Stepper ({ label, value, min, max, step = 1, onChange }: { label: string, value: number, min: number, max: number, step?: number, onChange: (value: number) => void }) {
  return (
    <View className='target-field'>
      <Text>{label}</Text>
      <View className='stepper'>
        <Button size='mini' disabled={value <= min} onClick={() => onChange(Math.max(min, value - step))}>−</Button>
        <Text>{value}</Text>
        <Button size='mini' disabled={value >= max} onClick={() => onChange(Math.min(max, value + step))}>＋</Button>
      </View>
    </View>
  )
}

function normalizeOrder (items: PlanExerciseSnapshotV2[]): PlanExerciseSnapshotV2[] {
  const byDay = new Map<number, PlanExerciseSnapshotV2[]>()
  items.forEach(item => byDay.set(item.day_of_week, [...(byDay.get(item.day_of_week) || []), item]))
  return [...byDay.entries()].sort(([left], [right]) => left - right).flatMap(([, values]) => (
    values.sort((left, right) => left.order_index - right.order_index).map((item, index) => ({ ...item, order_index: index }))
  ))
}
