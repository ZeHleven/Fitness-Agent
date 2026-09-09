import { useRef, useState } from 'react'
import { Button, Input, Picker, Text, Textarea, View } from '@tarojs/components'
import { exerciseApi } from '../services/exercises'
import { errorMessage } from '../core/request'
import type { PersonalizedExerciseOption } from '../types/api'
import { energyCategories, energyCategoryHelp } from '../core/exercise-energy'
import EnergyLibraryEditor from './EnergyLibraryEditor'
import './custom-exercise.scss'

export const CUSTOM_EXERCISE_NOTICE = '本平台仅提供记录与计划管理，请自行核对动作方法、训练负荷及身体适用性；如有疑问，请咨询专业人士。'
const equipmentOptions = [
  ['未知 / 未填写', ''], ['自重', 'bodyweight'], ['哑铃', 'dumbbell'], ['杠铃', 'barbell'],
  ['弹力带', 'resistance_band'], ['器械', 'machine'], ['单杠', 'pull_up_bar']
]

export default function CustomExerciseEntry ({ onAdd, disabled = false }: { onAdd: (option: PersonalizedExerciseOption) => void, disabled?: boolean }) {
  const [open, setOpen] = useState(false)
  const [options, setOptions] = useState<PersonalizedExerciseOption[]>([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [muscles, setMuscles] = useState('')
  const [equipment, setEquipment] = useState(0)
  const [energyIndex, setEnergyIndex] = useState(0)
  const [contraindications, setContraindications] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const busy = useRef(false)
  const show = async () => {
    setOpen(true); setLoading(true); setError('')
    try { setOptions(await exerciseApi.custom()) }
    catch (e) { setError(errorMessage(e, '自定义动作加载失败，可重试或创建新动作')) }
    finally { setLoading(false) }
  }
  const add = (option: PersonalizedExerciseOption) => {
    if (disabled || busy.current) return
    onAdd(option)
    setOpen(false)
  }
  const create = async () => {
    if (disabled || busy.current) return
    if (!name.trim() || !description.trim()) { setError('请填写动作名称和方法；其余安全信息可留空'); return }
    busy.current = true; setSaving(true); setError('')
    const labels = (raw: string) => raw.split(/[,，、\n]/).map(x => x.trim()).filter(Boolean)
    try {
      const option = await exerciseApi.createCustom({ name: name.trim(), description: description.trim(), muscles: labels(muscles), equipment: equipmentOptions[equipment][1] ? [equipmentOptions[equipment][1]] : [], contraindications: labels(contraindications), energy_category: energyCategories[energyIndex].value })
      onAdd(option)
      setOpen(false); setName(''); setDescription(''); setMuscles(''); setContraindications('')
      setEnergyIndex(0)
    } catch (e) { setError(errorMessage(e, '动作暂时无法添加，已保留填写内容')) }
    finally { busy.current = false; setSaving(false) }
  }
  return <View className='custom-exercise-entry'>
    {!open && <Button className='secondary-button open-custom-exercise' disabled={disabled} onClick={show}>＋ 自定义动作</Button>}
    {open && <View className='custom-exercise-form'>
      <Text className='custom-title'>我的自定义动作</Text>
      <Text className='custom-help'>支持动作库中没有的新动作，仅自己可用。本轮使用组数、次数、重量和组间休息记录。</Text>
      {loading && <Text className='custom-help'>正在加载我的动作…</Text>}
      {options.length > 0 && <Picker range={options.map(item => item.exercise_name)} onChange={event => add(options[Number(event.detail.value)])}><View className='custom-select'>选择之前创建的动作 ›</View></Picker>}
      <Text>动作名称</Text><Input className='custom-exercise-input custom-exercise-name' value={name} maxlength={100} placeholder='例如：弹力带单臂划船变式' onInput={e => setName(e.detail.value)} />
      <Text>动作方法</Text><Textarea className='custom-exercise-description' value={description} maxlength={2000} placeholder='描述姿势、动作过程和负重方式' onInput={e => setDescription(e.detail.value)} />
      <Text>训练部位（选填）</Text><Input className='custom-exercise-input custom-exercise-muscles' value={muscles} maxlength={300} placeholder='多个部位用逗号分隔' onInput={e => setMuscles(e.detail.value)} />
      <Text>器械（选填）</Text><Picker range={equipmentOptions.map(x => x[0])} value={equipment} onChange={e => setEquipment(Number(e.detail.value))}><View className='custom-select'>{equipmentOptions[equipment][0]}</View></Picker>
      <Text>消耗估算分类（选填）</Text><Picker className='create-energy-picker' disabled={saving} range={energyCategories.map(row => row.label)} value={energyIndex} onChange={e => { if (!busy.current) setEnergyIndex(Number(e.detail.value)) }}><View className='custom-select'>{energyCategories[energyIndex].label}</View></Picker>
      <Text className='custom-help'>{energyCategoryHelp} 未填写仍可创建、添加和记录。</Text>
      <Text>已知禁忌（选填）</Text><Input className='custom-exercise-input custom-exercise-contraindications' value={contraindications} maxlength={500} placeholder='例如：肩关节；不知道可留空' onInput={e => setContraindications(e.detail.value)} />
      <Text className='custom-safety-notice'>{CUSTOM_EXERCISE_NOTICE}</Text>
      {error && <View className='error-banner'>{error}<Button className='secondary-button reload-custom-exercises' disabled={saving || loading} onClick={show}>重新加载我的动作</Button></View>}
      <View className='custom-actions'><Button className='secondary-button close-custom-exercise' disabled={saving} onClick={() => setOpen(false)}>收起</Button><Button className='primary-button create-custom-exercise' disabled={saving || disabled} onClick={create}>{saving ? '保存中…' : '创建并添加'}</Button></View>
      <EnergyLibraryEditor options={options} onUpdated={option => setOptions(current => current.map(row => row.exercise_id === option.exercise_id ? option : row))} />
    </View>}
  </View>
}
