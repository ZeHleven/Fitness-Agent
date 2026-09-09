import { useEffect, useRef, useState } from 'react'
import { Button, Picker, Text, View } from '@tarojs/components'
import { workoutApi } from '../services/workouts'
import { errorMessage } from '../core/request'
import { classificationsApplied, energyCategories, energyCategoryHelp, energyCategoryLabel } from '../core/exercise-energy'
import type { EnergyCategory, SessionEnergyUpdate, WorkoutSession } from '../types/api'
import './energy-classification.scss'

export default function SessionEnergyEditor ({ session, onSaved }: { session: WorkoutSession, onSaved: (value: WorkoutSession) => void }) {
  const [draft, setDraft] = useState<Record<string, EnergyCategory | null>>({})
  const [future, setFuture] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uncertain, setUncertain] = useState(false)
  const [message, setMessage] = useState('')
  const base = useRef(session)
  const active = useRef(true)
  const flight = useRef(false)
  const pending = useRef<SessionEnergyUpdate | null>(null)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  // Refreshes do not replace an in-progress edit or advance its expected versions.
  useEffect(() => { if (!Object.keys(draft).length && !pending.current) base.current = session }, [session])
  const rows = session.exercises.filter(row => row.energy_classification_editable)
  if (!rows.length) return null
  const applied = (data: WorkoutSession) => {
    base.current = data; pending.current = null; setUncertain(false); setDraft({}); setFuture(false)
    setMessage('分类已保存。返回饮食页后会重新获取估算；异常计时仍需单独核对。'); onSaved(data)
  }
  const reconcile = async () => {
    const request = pending.current
    if (!request) return
    try {
      const latest = await workoutApi.detail(session.id)
      if (!active.current) return
      if (classificationsApplied(latest, request)) { applied(latest); return }
      base.current = latest; pending.current = null; setUncertain(false); onSaved(latest)
      setMessage('已读取当前结果，未确认本次保存生效或存在版本变化。选择仍保留，请核对原分类后再保存。')
    } catch (e) {
      if (active.current) { setUncertain(true); setMessage(errorMessage(e, '暂时无法核对保存结果，请恢复网络后读取核对；不会重复提交')) }
    }
  }
  const verify = async () => {
    if (flight.current) return
    flight.current = true; setBusy(true)
    try { await reconcile() } finally { flight.current = false; if (active.current) setBusy(false) }
  }
  const save = async () => {
    if (flight.current || pending.current) return
    const changes = Object.entries(draft).filter(([id, category]) => category && base.current.exercises.some(row => row.id === id && row.energy_classification_editable))
    if (!changes.length) { setMessage('请先为要修改的动作选择分类'); return }
    if (future && changes.some(([id]) => !base.current.exercises.find(row => row.id === id)?.library_energy_editable)) {
      setMessage('所选动作中有已停用的动作，请取消“同时用于今后”，仅修改本次训练。'); return
    }
    const request: SessionEnergyUpdate = {
      expected_version: base.current.energy_classification_version ?? 0, update_future: future,
      exercises: changes.map(([id, category]) => ({
        session_exercise_id: id, energy_category: category!,
        ...(future ? { expected_exercise_version: base.current.exercises.find(row => row.id === id)?.library_energy_category_version ?? undefined } : {})
      }))
    }
    flight.current = true; pending.current = request; setBusy(true); setMessage('')
    try {
      const data = await workoutApi.updateEnergyClassifications(session.id, request)
      if (active.current) applied(data)
    } catch (e) {
      if (active.current) { setUncertain(true); setMessage(errorMessage(e, '保存响应未确认，正在读取核对')); await reconcile() }
    } finally { flight.current = false; if (active.current) setBusy(false) }
  }
  return <View className='card energy-classification-editor'>
    <Text className='section-title'>补充／更正估算分类</Text>
    <Text className='energy-help'>{energyCategoryHelp}</Text>
    {rows.map(row => <View className='energy-classification-row' key={row.id}>
      <Text>{row.exercise_name || '未命名动作'}</Text>
      <Text className='energy-help'>原分类：{energyCategoryLabel(row.energy_category)}</Text>
      <Picker className='session-energy-picker' disabled={busy || uncertain} range={energyCategories.map(item => item.label)}
        value={Math.max(0, energyCategories.findIndex(item => item.value === (draft[row.id] === undefined ? row.energy_category : draft[row.id])))}
        onChange={e => { if (!flight.current && !pending.current) setDraft({ ...draft, [row.id]: energyCategories[Number(e.detail.value)]?.value || null }) }}>
        <View className='energy-category-select'>{energyCategoryLabel(draft[row.id] === undefined ? row.energy_category : draft[row.id])} ›</View>
      </Picker>
    </View>)}
    <Text className='energy-help'>默认仅修改本次训练，其他历史记录保持不变。</Text>
    <Button className='secondary-button energy-future-toggle' disabled={busy || uncertain}
      onClick={() => { if (!flight.current && !pending.current) setFuture(!future) }}>{future ? '☑' : '☐'} 同时用于这些动作今后的训练</Button>
    {message && <View className='energy-save-message'>{message}</View>}
    {uncertain && <Button className='secondary-button verify-session-energy' disabled={busy} onClick={verify}>读取核对保存结果</Button>}
    <Button className='primary-button save-session-energy' disabled={busy || uncertain || !Object.values(draft).some(Boolean)} onClick={save}>{busy ? '正在保存／核对…' : '保存分类并重新估算'}</Button>
  </View>
}
