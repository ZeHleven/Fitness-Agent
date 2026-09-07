import { useEffect, useRef, useState } from 'react'
import { Button, Input, Picker, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'
import { errorMessage } from '../../core/request'
import { changeMealAmount, changeMealNutrition, existingMealDraft, foodMealDraft, mealDraftCandidate, nutrientKeys, nutrientLimit, parseMealNumber } from '../../core/meal-draft'
import type { MealDraftItem, NutrientKey } from '../../core/meal-draft'
import { nutritionApi } from '../../services/nutrition'
import type { DailyNutritionSummary, Food, MealItemInput, MealLog } from '../../types/api'
import './index.scss'

const mealTypes: MealLog['meal_type'][] = ['早餐', '午餐', '晚餐', '加餐']
const emptyCustom = () => ({ name: '', amount: '100', calories: '', protein: '0', carbs: '0', fat: '0' })
type EditorAction = { kind: 'new' | 'close' } | { kind: 'edit', meal: MealLog }

export default function NutritionPage () {
  const [today, setToday] = useState<DailyNutritionSummary | null>(null)
  const [history, setHistory] = useState<DailyNutritionSummary[]>([])
  const [foods, setFoods] = useState<Food[]>([])
  const [search, setSearch] = useState('')
  const [portion, setPortion] = useState('100')
  const [mealType, setMealType] = useState<MealLog['meal_type']>('早餐')
  const [loggedAt, setLoggedAt] = useState(localDate())
  const [editingMealId, setEditingMealId] = useState('')
  const [items, setItems] = useState<MealDraftItem[]>([])
  const [custom, setCustom] = useState(emptyCustom)
  const [editorOpen, setEditorOpen] = useState(false)
  const [customOpen, setCustomOpen] = useState(false)
  const [discardAction, setDiscardAction] = useState<EditorAction | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<MealLog | null>(null)
  const [todayLoading, setTodayLoading] = useState(true)
  const [historyLoading, setHistoryLoading] = useState(true)
  const [foodLoading, setFoodLoading] = useState(false)
  const [todayError, setTodayError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const [foodError, setFoodError] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const savingLock = useRef(false)
  const itemSequence = useRef(0)
  const todayGeneration = useRef(0)
  const historyGeneration = useRef(0)
  const searchGeneration = useRef(0)
  const foodLoaded = useRef(false)
  const editorSeed = useRef('')
  const signature = (draft: MealDraftItem[], date: string, type: MealLog['meal_type'], extra = emptyCustom()) => JSON.stringify([draft, date, type, extra])
  const dirty = editorOpen && signature(items, loggedAt, mealType, custom) !== editorSeed.current
  const nextKey = () => `meal-item-${++itemSequence.current}`

  const refreshToday = async () => {
    const generation = ++todayGeneration.current
    setTodayLoading(true); setTodayError('')
    try {
      const value = await nutritionApi.today()
      if (generation === todayGeneration.current) setToday(value)
    } catch (e) {
      if (generation === todayGeneration.current) setTodayError(errorMessage(e, '今日记录加载失败'))
    } finally { if (generation === todayGeneration.current) setTodayLoading(false) }
  }
  const refreshHistory = async () => {
    const generation = ++historyGeneration.current
    setHistoryLoading(true); setHistoryError('')
    try {
      const value = await nutritionApi.history()
      if (generation === historyGeneration.current) setHistory(value)
    } catch (e) {
      if (generation === historyGeneration.current) setHistoryError(errorMessage(e, '历史记录加载失败'))
    } finally { if (generation === historyGeneration.current) setHistoryLoading(false) }
  }
  const load = () => Promise.all([refreshToday(), refreshHistory()])
  useDidShow(() => { void load() })
  useEffect(() => () => {
    todayGeneration.current++; historyGeneration.current++; searchGeneration.current++
  }, [])

  const searchFoods = async (query: string) => {
    const generation = ++searchGeneration.current
    setFoodLoading(true); setFoodError('')
    try {
      const value = await nutritionApi.foods(query, query ? 30 : 20)
      if (generation !== searchGeneration.current) return
      setFoods(value); foodLoaded.current = true
    } catch (e) {
      if (generation === searchGeneration.current) setFoodError(errorMessage(e, '食品搜索失败，请重试'))
    } finally { if (generation === searchGeneration.current) setFoodLoading(false) }
  }
  const findFoods = () => searchFoods(search.trim())

  const applyEditorAction = (action: EditorAction, saved = false) => {
    if (savingLock.current && !saved) return
    const meal = action.kind === 'edit' ? action.meal : null
    const draft = meal ? meal.items.map(item => existingMealDraft(item, nextKey())) : []
    const date = meal?.logged_at || localDate()
    const type = meal?.meal_type || '早餐'
    setEditingMealId(meal?.id || ''); setLoggedAt(date); setMealType(type)
    setItems(draft); setCustom(emptyCustom()); setCustomOpen(false)
    setEditorOpen(action.kind !== 'close'); setDiscardAction(null); setError('')
    editorSeed.current = signature(draft, date, type)
    if (action.kind !== 'close' && !foodLoaded.current && !foodLoading) void searchFoods(search.trim())
  }
  useEffect(() => {
    if (editorOpen) void Taro.pageScrollTo({ selector: '.meal-editor', duration: 250 }).catch(() => {})
  }, [editorOpen, editingMealId])
  useEffect(() => {
    const selector = discardAction ? '.discard-prompt' : deleteTarget ? '.delete-prompt' : error ? '.editor-error' : ''
    if (selector) void Taro.pageScrollTo({ selector, duration: 200 }).catch(() => {})
  }, [discardAction, deleteTarget, error])
  const requestEditorAction = (action: EditorAction) => {
    if (savingLock.current) return
    if (action.kind === 'edit' && editorOpen && action.meal.id === editingMealId) {
      void Taro.pageScrollTo({ selector: '.meal-editor', duration: 250 }).catch(() => {})
      return
    }
    if (dirty) setDiscardAction(action)
    else applyEditorAction(action)
  }
  const editMeal = (meal: MealLog) => requestEditorAction({ kind: 'edit', meal })

  const addFood = (food: Food) => {
    if (savingLock.current) return
    const grams = parseMealNumber(portion, 10000, false)
    if (grams === null) { setError('请输入大于 0 且不超过 10000 克的有效份量'); return }
    if (items.length >= 30) { setError('每餐最多添加 30 项食物'); return }
    const draft = foodMealDraft(food, grams, nextKey())
    setItems(current => current.length < 30 ? [...current, draft] : current); setError('')
  }
  const addCustom = () => {
    if (savingLock.current) return
    const amount = parseMealNumber(custom.amount, 10000, false)
    const raw = [custom.calories, custom.protein, custom.carbs, custom.fat]
    const values = nutrientKeys.map((key, i) => parseMealNumber(raw[i], nutrientLimit(key)))
    if (!custom.name.trim() || custom.name.trim().length > 100 || amount === null || values.some(v => v === null)) {
      setError('请补全食物名称、有效克数和当前份量的营养总量。空白不能当作 0'); return
    }
    if (items.length >= 30) { setError('每餐最多添加 30 项食物'); return }
    const draft = existingMealDraft({ food_name: custom.name.trim(), amount_g: amount,
      calories: values[0]!, protein_g: values[1]!, carbs_g: values[2]!, fat_g: values[3]! }, nextKey())
    setItems(current => current.length < 30 ? [...current, draft] : current); setCustom(emptyCustom()); setError('')
  }
  const updateItemAmount = (key: string, raw: string) => {
    if (!savingLock.current) setItems(current => current.map(item => item.key === key ? changeMealAmount(item, raw) : item))
  }
  const updateCustomNutrition = (key: string, field: NutrientKey, raw: string) => {
    if (!savingLock.current) setItems(current => current.map(item => item.key === key ? changeMealNutrition(item, field, raw) : item))
  }
  const candidates = items.map(mealDraftCandidate)
  const draftValid = items.length > 0 && items.length <= 30 && candidates.every(Boolean)
  const totals = candidates.reduce((sum, item) => ({
    calories: sum.calories + (item?.calories || 0), protein: sum.protein + (item?.protein_g || 0),
    carbs: sum.carbs + (item?.carbs_g || 0), fat: sum.fat + (item?.fat_g || 0)
  }), { calories: 0, protein: 0, carbs: 0, fat: 0 })

  const saveMeal = async () => {
    if (savingLock.current) return
    if (JSON.stringify(custom) !== JSON.stringify(emptyCustom())) {
      setCustomOpen(true)
      setError('还有未添加的自定义食物。请先添加到本餐，或清空自定义表单后再保存。')
      return
    }
    if (!draftValid) { setError('请先补全每项食品的有效名称、克数和营养数据'); return }
    savingLock.current = true; setSaving(true); setError('')
    try {
      const candidate = { logged_at: loggedAt, meal_type: mealType, items: candidates as MealItemInput[] }
      if (editingMealId) await nutritionApi.updateMeal(editingMealId, candidate)
      else await nutritionApi.logMeal(candidate)
      const updated = Boolean(editingMealId)
      applyEditorAction({ kind: 'close' }, true)
      await Taro.showToast({ title: updated ? '修改已保存' : '餐次已记录', icon: 'success' }).catch(() => {})
      await load()
    } catch (e) {
      const status = (e as { statusCode?: number } | null)?.statusCode
      setError(status && status >= 400 && status < 500 && status !== 408 && status !== 429
        ? errorMessage(e, '保存未成功，请检查填写内容；草稿已保留')
        : '暂时无法确认保存结果，草稿已保留。请先刷新餐次记录核对，避免重复新增。')
    } finally { savingLock.current = false; setSaving(false) }
  }
  const deleteMeal = async () => {
    if (!deleteTarget || savingLock.current) return
    const target = deleteTarget
    savingLock.current = true; setSaving(true); setError('')
    try {
      await nutritionApi.deleteMeal(target.id)
      if (editingMealId === target.id) applyEditorAction({ kind: 'close' }, true)
      setDeleteTarget(null); await load()
    } catch (e) { setError(errorMessage(e, '删除结果尚未确认，请刷新记录核对')) }
    finally { savingLock.current = false; setSaving(false) }
  }
  const mealRows = (meals: MealLog[]) => meals.map(meal => (
    <View className='history-meal' key={meal.id}>
      <View className='history-meal-heading'><Text className='meal-name'>{meal.meal_type}</Text>
        <View className='meal-actions'>
          <Button className='edit-meal' size='mini' disabled={saving} onClick={() => editMeal(meal)}>编辑</Button>
          <Button className='delete-meal' size='mini' disabled={saving} onClick={() => setDeleteTarget(meal)}>删除</Button>
        </View>
      </View>
      <Text className='meal-items'>{meal.items.map(item => `${item.food_name} ${item.amount_g}g`).join(' · ')}</Text>
    </View>
  ))

  return (
    <View className='page nutrition-page'>
      <Text className='nutrition-eyebrow'>今天吃得怎么样</Text><Text className='nutrition-title'>饮食记录</Text>
      {todayError && <View className='error-banner'>{todayError}<Button className='secondary-button today-retry' onClick={refreshToday}>重试今日记录</Button></View>}
      {todayLoading && !today && <View className='loading-state'>正在加载今日饮食…</View>}
      {today && <View className='card daily-card'>
        <Text className='daily-label'>今日摄入</Text><Text className='daily-calories'>{formatNumber(today.total_calories)} kcal</Text>
        <View className='macro-row'><Macro label='蛋白质' value={today.total_protein_g} /><Macro label='碳水' value={today.total_carbs_g} /><Macro label='脂肪' value={today.total_fat_g} /></View>
      </View>}
      <View className='section-heading-row'><Text className='history-heading'>今天已记录</Text><Button className='refresh-meals' size='mini' onClick={load}>刷新记录</Button></View>
      {today && <View className='card today-meals'>{today.meals.length ? mealRows(today.meals) : <Text className='empty-copy'>今天还没有记录。可以记录一餐，也可以请 Agent 帮你制定方案。</Text>}</View>}
      <Button className='primary-button start-meal' disabled={saving} onClick={() => requestEditorAction({ kind: 'new' })}>记录一餐</Button>
      {discardAction && <View className='card inline-confirm discard-prompt'>
        <Text>当前有未保存的修改。放弃后才能继续，已保存的餐次不会受影响。</Text>
        <View className='confirm-actions'><Button className='secondary-button keep-draft' onClick={() => setDiscardAction(null)}>继续编辑</Button><Button className='secondary-button discard-draft' onClick={() => applyEditorAction(discardAction)}>放弃修改并继续</Button></View>
      </View>}
      {deleteTarget && <View className='card inline-confirm delete-prompt'>
        <Text>删除{deleteTarget.logged_at}的{deleteTarget.meal_type}及全部食物明细？此操作无法撤销。若正在编辑此餐，草稿也会清除。</Text>
        <View className='confirm-actions'><Button className='secondary-button' disabled={saving} onClick={() => setDeleteTarget(null)}>保留记录</Button><Button className='secondary-button confirm-delete' disabled={saving} onClick={deleteMeal}>确认删除</Button></View>
      </View>}
      {error && <View className='error-banner editor-error'>{error}</View>}
      {editorOpen && <View className='card meal-editor'>
        <View className='editor-row'><Text className='editor-label'>{editingMealId ? '编辑餐次' : '记录餐次'}</Text>
          <Picker disabled={saving} mode='selector' range={mealTypes} value={mealTypes.indexOf(mealType)} onChange={e => setMealType(mealTypes[Number(e.detail.value)])}><View className='meal-picker'>{mealType} ⌄</View></Picker>
        </View>
        <View className='editor-row date-row'><Text className='date-label'>记录日期</Text>
          <Picker disabled={saving} mode='date' value={loggedAt} start={earliestEditableDate()} end={localDate()} onChange={e => setLoggedAt(e.detail.value)}><View className='meal-picker'>{loggedAt} ⌄</View></Picker>
        </View>
        <View className='selected-items'>
          <Text className='selected-title'>本次餐次（{items.length}）</Text>
          {!items.length && <Text className='empty-copy'>从下方食品库添加食品，或展开自定义食物。</Text>}
          {items.map(item => <View className='selected-row' key={item.key}>
            <View className='selected-main'>
              {item.food_id ? <Text className='selected-name'>{item.food_name}</Text> : <Input disabled={saving} className='selected-name-input' value={item.food_name} maxlength={100} onInput={e => setItems(current => current.map(row => row.key === item.key ? { ...row, food_name: e.detail.value } : row))} />}
              <View className='selected-amount'><Input disabled={saving} className='selected-amount-input' type='digit' value={item.amountText} onInput={e => updateItemAmount(item.key, e.detail.value)} /><Text>克</Text></View>
              {!mealDraftCandidate(item) && <Text className='field-error'>请补全有效名称、克数和营养数据；空白不能当作 0。</Text>}
              {item.food_id && mealDraftCandidate(item) && <Text className='food-meta'>预估 {item.nutrients.calories} kcal · 保存时按食品库重新计算</Text>}
              {!item.food_id && <>
                <Text className='nutrition-basis-note'>以下营养为当前份量的总量；修改克数会按比例换算。</Text>
                <View className='selected-custom-grid'>{nutrientKeys.map((field, i) => <SmallInput key={field} label={['kcal', '蛋白g', '碳水g', '脂肪g'][i]} disabled={saving || parseMealNumber(item.amountText, 10000, false) === null} value={item.nutrients[field]} onInput={raw => updateCustomNutrition(item.key, field, raw)} />)}</View>
              </>}
            </View>
            <Button className='remove-item' size='mini' disabled={saving} onClick={() => setItems(current => current.filter(row => row.key !== item.key))}>删除</Button>
          </View>)}
          {!!items.length && <View className='draft-summary'>{draftValid ? <><Text>整餐预估 {formatNumber(totals.calories)} kcal</Text><Text>蛋白 {formatNumber(totals.protein)}g · 碳水 {formatNumber(totals.carbs)}g · 脂肪 {formatNumber(totals.fat)}g</Text></> : <Text>请补全上方字段后查看整餐营养并保存。</Text>}</View>}
          <Button className='primary-button save-meal' loading={saving} disabled={saving || !draftValid} onClick={saveMeal}>{editingMealId ? '保存修改' : `保存${mealType}`}</Button>
          <Button className='secondary-button cancel-edit' disabled={saving} onClick={() => requestEditorAction({ kind: 'close' })}>{editingMealId ? '取消编辑' : '收起编辑'}</Button>
        </View>
        <Text className='custom-title'>添加食品</Text>
        <View className='search-row'><Input disabled={saving} className='search-input' value={search} placeholder='搜索食品库' onInput={e => { setSearch(e.detail.value); searchGeneration.current++; setFoodLoading(false); setFoodError(''); setFoods([]); foodLoaded.current = false }} onConfirm={findFoods} /><Button className='search-button' size='mini' disabled={saving} onClick={findFoods}>搜索</Button></View>
        <View className='portion-row'><Text>添加份量</Text><Input disabled={saving} className='portion-input' type='digit' value={portion} onInput={e => setPortion(e.detail.value)} /><Text>克</Text></View>
        {foodLoading && <Text className='loading-state'>正在搜索食品…</Text>}
        {foodError && <View className='error-banner'>{foodError}<Button className='secondary-button food-retry' onClick={findFoods}>重试搜索</Button></View>}
        {!foodLoading && !foodError && !foods.length && <Text className='empty-copy'>{foodLoaded.current ? '未找到匹配食品，可以换个名称或添加自定义食物。' : '输入名称后点击搜索。'}</Text>}
        {!foodLoading && !foodError && <View className='food-results'>{foods.map(food => <View className='food-row' key={food.id}><View className='food-copy'><Text className='food-name'>{food.name_zh}</Text><Text className='food-meta'>{formatNumber(food.calories_per_100g)} kcal / 100g · 蛋白 {formatNumber(food.protein_g)}g</Text></View><Button className='food-add' size='mini' disabled={saving} onClick={() => addFood(food)}>添加</Button></View>)}</View>}
        <Button className='secondary-button toggle-custom' disabled={saving} onClick={() => setCustomOpen(!customOpen)}>{customOpen ? '收起自定义食物' : '食品库没有？添加自定义食物'}</Button>
        {customOpen && <View className='custom-form'>
          <Text className='nutrition-basis-note'>填写这份食物的克数，以及当前份量的营养总量（不是每 100 克）。</Text>
          <Input disabled={saving} className='custom-input wide' value={custom.name} maxlength={100} placeholder='食物名称' onInput={e => setCustom(current => ({ ...current, name: e.detail.value }))} />
          <View className='custom-grid'>{(['amount', 'calories', 'protein', 'carbs', 'fat'] as const).map((field, i) => <SmallInput key={field} disabled={saving} label={['克', 'kcal', '蛋白g', '碳水g', '脂肪g'][i]} value={custom[field]} onInput={raw => setCustom(current => ({ ...current, [field]: raw }))} />)}</View>
          <Button className='secondary-button custom-add' disabled={saving} onClick={addCustom}>添加自定义食物</Button>
          <Button className='secondary-button clear-custom' disabled={saving} onClick={() => setCustom(emptyCustom())}>清空未添加的自定义食物</Button>
        </View>}
      </View>}
      <Text className='history-heading'>过去 29 天</Text>
      {historyLoading && !history.length && <Text className='loading-state'>正在加载历史记录…</Text>}
      {historyError && <View className='error-banner'>{historyError}<Button className='secondary-button history-retry' onClick={refreshHistory}>重试历史记录</Button></View>}
      {!historyLoading && !historyError && !history.some(day => day.date < localDate()) && <View className='card empty-state'>过去 29 天还没有饮食记录。</View>}
      {history.filter(day => day.date < localDate()).map(day => <View className='card history-day' key={day.date}><View className='history-day-heading'><Text className='history-date'>{day.date}</Text><Text className='history-total'>{formatNumber(day.total_calories)} kcal</Text></View>{mealRows(day.meals)}</View>)}
    </View>
  )
}

function Macro ({ label, value }: { label: string, value: number }) {
  return <View className='macro'><Text className='macro-value'>{formatNumber(value)}g</Text><Text className='macro-label'>{label}</Text></View>
}
function SmallInput ({ label, value, onInput, disabled }: { label: string, value: string, onInput: (value: string) => void, disabled?: boolean }) {
  return <View className='small-input-wrap'><Input disabled={disabled} className='small-input' type='digit' value={value} onInput={e => onInput(e.detail.value)} /><Text>{label}</Text></View>
}
function formatNumber (value: number): string { return Math.round(value * 10) / 10 + '' }
function localDate (): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}
function earliestEditableDate (): string {
  const earliest = new Date(); earliest.setDate(earliest.getDate() - 29)
  return `${earliest.getFullYear()}-${String(earliest.getMonth() + 1).padStart(2, '0')}-${String(earliest.getDate()).padStart(2, '0')}`
}
