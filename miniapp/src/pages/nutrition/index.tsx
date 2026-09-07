import { useState } from 'react'
import { Button, Input, Picker, Text, View } from '@tarojs/components'
import Taro, { useDidShow } from '@tarojs/taro'

import { errorMessage } from '../../core/request'
import { nutritionApi } from '../../services/nutrition'
import type { DailyNutritionSummary, Food, MealItemInput, MealLog } from '../../types/api'
import './index.scss'

const mealTypes: MealLog['meal_type'][] = ['早餐', '午餐', '晚餐', '加餐']

export default function NutritionPage () {
  const [today, setToday] = useState<DailyNutritionSummary | null>(null)
  const [history, setHistory] = useState<DailyNutritionSummary[]>([])
  const [foods, setFoods] = useState<Food[]>([])
  const [search, setSearch] = useState('')
  const [portion, setPortion] = useState('100')
  const [mealType, setMealType] = useState<MealLog['meal_type']>('早餐')
  const [loggedAt, setLoggedAt] = useState(localDate())
  const [editingMealId, setEditingMealId] = useState('')
  const [items, setItems] = useState<MealItemInput[]>([])
  const [custom, setCustom] = useState({ name: '', amount: '100', calories: '', protein: '0', carbs: '0', fat: '0' })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const [todayData, historyData, foodData] = await Promise.all([
        nutritionApi.today(), nutritionApi.history(), nutritionApi.foods('', 20)
      ])
      setToday(todayData)
      setHistory(historyData)
      setFoods(foodData)
    } catch (requestError) {
      setError(errorMessage(requestError, '饮食数据加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useDidShow(() => { void load() })

  const findFoods = async () => {
    try {
      setFoods(await nutritionApi.foods(search.trim(), 30))
    } catch (requestError) {
      setError(errorMessage(requestError, '食品搜索失败'))
    }
  }

  const addFood = (food: Food) => {
    const grams = Number(portion)
    if (!Number.isFinite(grams) || grams <= 0 || grams > 10000) {
      setError('请输入 0–10000 克之间的有效份量')
      return
    }
    const factor = grams / 100
    setItems(current => [...current, {
      food_id: food.id,
      food_name: food.name_zh,
      amount_g: grams,
      calories: round(food.calories_per_100g * factor),
      protein_g: round(food.protein_g * factor),
      carbs_g: round(food.carbs_g * factor),
      fat_g: round(food.fat_g * factor)
    }])
    setError('')
  }

  const addCustom = () => {
    const amount = Number(custom.amount)
    const calories = Number(custom.calories)
    const protein = Number(custom.protein)
    const carbs = Number(custom.carbs)
    const fat = Number(custom.fat)
    if (!custom.name.trim() || custom.name.trim().length > 100) {
      setError('请填写不超过 100 个字符的食物名称')
      return
    }
    if (![amount, calories, protein, carbs, fat].every(Number.isFinite) || amount <= 0 || amount > 10000 || Math.min(calories, protein, carbs, fat) < 0) {
      setError('请填写有效的自定义食物份量和营养数据')
      return
    }
    setItems(current => [...current, {
      food_name: custom.name.trim(), amount_g: amount, calories,
      protein_g: protein, carbs_g: carbs, fat_g: fat
    }])
    setCustom({ name: '', amount: '100', calories: '', protein: '0', carbs: '0', fat: '0' })
    setError('')
  }

  const resetEditor = () => {
    setEditingMealId('')
    setLoggedAt(localDate())
    setMealType('早餐')
    setItems([])
    setCustom({ name: '', amount: '100', calories: '', protein: '0', carbs: '0', fat: '0' })
    setError('')
  }

  const editMeal = (meal: MealLog) => {
    setEditingMealId(meal.id)
    setLoggedAt(meal.logged_at)
    setMealType(meal.meal_type)
    setItems(meal.items.map(item => ({
      food_id: item.food_id,
      food_name: item.food_name,
      amount_g: item.amount_g,
      calories: item.calories,
      protein_g: item.protein_g,
      carbs_g: item.carbs_g,
      fat_g: item.fat_g
    })))
    setError('')
    void Taro.pageScrollTo({ selector: '.meal-editor', duration: 250 })
  }

  const updateItemAmount = (index: number, rawValue: string) => {
    const amount = Number(rawValue)
    setItems(current => current.map((item, itemIndex) => {
      if (itemIndex !== index) return item
      const factor = Number.isFinite(amount) && item.amount_g > 0 ? amount / item.amount_g : 0
      return {
        ...item,
        amount_g: Number.isFinite(amount) ? amount : 0,
        calories: round(item.calories * factor),
        protein_g: round(item.protein_g * factor),
        carbs_g: round(item.carbs_g * factor),
        fat_g: round(item.fat_g * factor)
      }
    }))
  }

  const updateCustomNutrition = (
    index: number,
    field: 'calories' | 'protein_g' | 'carbs_g' | 'fat_g',
    rawValue: string
  ) => {
    const value = Number(rawValue)
    setItems(current => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, [field]: Number.isFinite(value) ? value : 0 } : item
    )))
  }

  const updateCustomName = (index: number, value: string) => {
    setItems(current => current.map((item, itemIndex) => (
      itemIndex === index ? { ...item, food_name: value } : item
    )))
  }

  const saveMeal = async () => {
    if (!items.length) {
      setError('请先添加至少一种食物')
      return
    }
    if (items.some(item => !Number.isFinite(item.amount_g) || item.amount_g <= 0 || item.amount_g > 10000)) {
      setError('每项食物请输入 0–10000 克之间的有效份量')
      return
    }
    setSaving(true)
    setError('')
    try {
      const candidate = {
        logged_at: loggedAt,
        meal_type: mealType,
        items
      }
      if (editingMealId) await nutritionApi.updateMeal(editingMealId, candidate)
      else await nutritionApi.logMeal(candidate)
      const updated = Boolean(editingMealId)
      resetEditor()
      await Taro.showToast({ title: updated ? '修改已保存' : '餐次已记录', icon: 'success' })
      await load()
    } catch (requestError) {
      setError(errorMessage(requestError, editingMealId ? '饮食记录修改失败，原记录未变' : '饮食记录保存失败'))
    } finally {
      setSaving(false)
    }
  }

  const deleteMeal = async (meal: MealLog) => {
    const answer = await Taro.showModal({
      title: `删除${meal.meal_type}记录？`,
      content: '该餐次和其中的食物明细将永久删除。'
    })
    if (!answer.confirm) return
    try {
      await nutritionApi.deleteMeal(meal.id)
      await load()
    } catch (requestError) {
      setError(errorMessage(requestError, '饮食记录删除失败'))
    }
  }

  const mealIndex = mealTypes.indexOf(mealType)
  const draftTotals = items.reduce((totals, item) => ({
    calories: totals.calories + item.calories,
    protein: totals.protein + item.protein_g,
    carbs: totals.carbs + item.carbs_g,
    fat: totals.fat + item.fat_g
  }), { calories: 0, protein: 0, carbs: 0, fat: 0 })
  return (
    <View className='page nutrition-page'>
      <Text className='nutrition-eyebrow'>今天吃得怎么样</Text>
      <Text className='nutrition-title'>饮食记录</Text>
      {error && <View className='error-banner'>{error}</View>}
      {loading && !today && <View className='loading-state'>正在加载饮食数据…</View>}

      {today && (
        <View className='card daily-card'>
          <Text className='daily-label'>今日摄入</Text>
          <Text className='daily-calories'>{formatNumber(today.total_calories)} kcal</Text>
          <View className='macro-row'>
            <Macro label='蛋白质' value={today.total_protein_g} />
            <Macro label='碳水' value={today.total_carbs_g} />
            <Macro label='脂肪' value={today.total_fat_g} />
          </View>
        </View>
      )}

      <View className='card meal-editor'>
        <View className='editor-row'>
          <Text className='editor-label'>{editingMealId ? '编辑餐次' : '记录餐次'}</Text>
          <Picker mode='selector' range={mealTypes} value={mealIndex} onChange={event => setMealType(mealTypes[Number(event.detail.value)])}>
            <View className='meal-picker'>{mealType} ⌄</View>
          </Picker>
        </View>
        <View className='editor-row date-row'>
          <Text className='date-label'>记录日期</Text>
          <Picker mode='date' value={loggedAt} start={earliestEditableDate()} end={localDate()} onChange={event => setLoggedAt(event.detail.value)}>
            <View className='meal-picker'>{loggedAt} ⌄</View>
          </Picker>
        </View>

        <View className='search-row'>
          <Input className='search-input' value={search} placeholder='搜索食品库' onInput={event => setSearch(event.detail.value)} onConfirm={findFoods} />
          <Button className='search-button' size='mini' onClick={findFoods}>搜索</Button>
        </View>
        <View className='portion-row'>
          <Text>添加份量</Text>
          <Input className='portion-input' type='digit' value={portion} onInput={event => setPortion(event.detail.value)} />
          <Text>克</Text>
        </View>
        <View className='food-results'>
          {foods.map(food => (
            <View className='food-row' key={food.id}>
              <View className='food-copy'>
                <Text className='food-name'>{food.name_zh}</Text>
                <Text className='food-meta'>{formatNumber(food.calories_per_100g)} kcal / 100g · 蛋白 {formatNumber(food.protein_g)}g</Text>
              </View>
              <Button className='food-add' size='mini' onClick={() => addFood(food)}>添加</Button>
            </View>
          ))}
        </View>

        <Text className='custom-title'>自定义食物</Text>
        <Input className='custom-input wide' value={custom.name} placeholder='食物名称' onInput={event => setCustom(current => ({ ...current, name: event.detail.value }))} />
        <View className='custom-grid'>
          <SmallInput label='克' value={custom.amount} onInput={value => setCustom(current => ({ ...current, amount: value }))} />
          <SmallInput label='kcal' value={custom.calories} onInput={value => setCustom(current => ({ ...current, calories: value }))} />
          <SmallInput label='蛋白g' value={custom.protein} onInput={value => setCustom(current => ({ ...current, protein: value }))} />
          <SmallInput label='碳水g' value={custom.carbs} onInput={value => setCustom(current => ({ ...current, carbs: value }))} />
          <SmallInput label='脂肪g' value={custom.fat} onInput={value => setCustom(current => ({ ...current, fat: value }))} />
        </View>
        <Button className='secondary-button custom-add' onClick={addCustom}>添加自定义食物</Button>

        {!!items.length && (
          <View className='selected-items'>
            <Text className='selected-title'>本次餐次（{items.length}）</Text>
            {items.map((item, index) => (
              <View className='selected-row' key={`${item.food_id || item.food_name}-${index}`}>
                <View className='selected-main'>
                  {item.food_id
                    ? <Text className='selected-name'>{item.food_name}</Text>
                    : <Input className='selected-name-input' value={item.food_name} maxlength={100} onInput={event => updateCustomName(index, event.detail.value)} />}
                  <View className='selected-amount'>
                    <Input className='selected-amount-input' type='digit' value={String(item.amount_g)} onInput={event => updateItemAmount(index, event.detail.value)} />
                    <Text>克 · {formatNumber(item.calories)} kcal</Text>
                  </View>
                  {!item.food_id && (
                    <View className='selected-custom-grid'>
                      <SmallInput label='kcal' value={String(item.calories)} onInput={value => updateCustomNutrition(index, 'calories', value)} />
                      <SmallInput label='蛋白g' value={String(item.protein_g)} onInput={value => updateCustomNutrition(index, 'protein_g', value)} />
                      <SmallInput label='碳水g' value={String(item.carbs_g)} onInput={value => updateCustomNutrition(index, 'carbs_g', value)} />
                      <SmallInput label='脂肪g' value={String(item.fat_g)} onInput={value => updateCustomNutrition(index, 'fat_g', value)} />
                    </View>
                  )}
                </View>
                <Text className='remove-item' onClick={() => setItems(current => current.filter((_, itemIndex) => itemIndex !== index))}>删除</Text>
              </View>
            ))}
            <View className='draft-summary'>
              <Text>整餐 {formatNumber(draftTotals.calories)} kcal</Text>
              <Text>蛋白 {formatNumber(draftTotals.protein)}g · 碳水 {formatNumber(draftTotals.carbs)}g · 脂肪 {formatNumber(draftTotals.fat)}g</Text>
            </View>
          </View>
        )}
        <Button className='primary-button save-meal' loading={saving} disabled={saving || !items.length} onClick={saveMeal}>
          {editingMealId ? '保存修改' : `保存${mealType}`}
        </Button>
        {editingMealId && <Button className='secondary-button cancel-edit' disabled={saving} onClick={resetEditor}>取消编辑</Button>}
      </View>

      <Text className='history-heading'>近 30 天</Text>
      {!history.length && !loading && <View className='card empty-state'>还没有饮食记录</View>}
      {history.map(day => (
        <View className='card history-day' key={day.date}>
          <View className='history-day-heading'>
            <Text className='history-date'>{day.date}</Text>
            <Text className='history-total'>{formatNumber(day.total_calories)} kcal</Text>
          </View>
          {day.meals.map(meal => (
            <View className='history-meal' key={meal.id}>
              <View className='history-meal-heading'>
                <Text className='meal-name'>{meal.meal_type}</Text>
                <View className='meal-actions'>
                  <Text className='edit-meal' onClick={() => editMeal(meal)}>编辑</Text>
                  <Text className='delete-meal' onClick={() => deleteMeal(meal)}>删除</Text>
                </View>
              </View>
              <Text className='meal-items'>{meal.items.map(item => `${item.food_name} ${item.amount_g}g`).join(' · ')}</Text>
            </View>
          ))}
        </View>
      ))}
    </View>
  )
}

function Macro ({ label, value }: { label: string, value: number }) {
  return <View className='macro'><Text className='macro-value'>{formatNumber(value)}g</Text><Text className='macro-label'>{label}</Text></View>
}

function SmallInput ({ label, value, onInput }: { label: string, value: string, onInput: (value: string) => void }) {
  return <View className='small-input-wrap'><Input className='small-input' type='digit' value={value} onInput={event => onInput(event.detail.value)} /><Text>{label}</Text></View>
}

function formatNumber (value: number): string { return Math.round(value * 10) / 10 + '' }
function round (value: number): number { return Math.round(value * 10) / 10 }
function localDate (): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

function earliestEditableDate (): string {
  const earliest = new Date()
  earliest.setDate(earliest.getDate() - 29)
  return `${earliest.getFullYear()}-${String(earliest.getMonth() + 1).padStart(2, '0')}-${String(earliest.getDate()).padStart(2, '0')}`
}
