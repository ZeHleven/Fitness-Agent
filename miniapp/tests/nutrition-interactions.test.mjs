import assert from 'node:assert/strict'
import test from 'node:test'
import { deferred, runtime } from './helpers/page-runtime.mjs'

const food = { id: 'rice', name_zh: '杂粮饭', calories_per_100g: 130, protein_g: 3, carbs_g: 28, fat_g: 1 }
const item = { food_id: 'rice', food_name: '杂粮饭', amount_g: 100, calories: 130, protein_g: 3, carbs_g: 28, fat_g: 1 }
const meal = { id: 'meal', logged_at: '2026-09-07', meal_type: '午餐', items: [item] }
const summary = { date: meal.logged_at, meals: [meal], total_calories: 130, total_protein_g: 3, total_carbs_g: 28, total_fat_g: 1 }
async function createPage (overrides = {}) {
  const writes = [], reads = []
  const api = {
    today: async () => summary, history: async () => [summary], foods: async () => [food],
    logMeal: async data => { writes.push(['POST', data]); return data },
    updateMeal: async (id, data) => { writes.push(['PUT', id, data]); return data },
    deleteMeal: async id => { writes.push(['DELETE', id]) }, ...overrides
  }
  for (const key of ['today', 'history', 'foods']) {
    const original = api[key]
    api[key] = (...args) => { reads.push([key, ...args]); return original(...args) }
  }
  const page = runtime('../../src/pages/nutrition/index.tsx', {
    '../../services/nutrition': { nutritionApi: api }, '../../core/request': { errorMessage: e => e.message }
  })
  page.render(); page.hooks.useDidShow(); await page.flush()
  return { ...page, writes, reads }
}

test('first screen loads two independent sections, not the food picker', async () => {
  const page = await createPage()
  assert.deepEqual(page.reads.map(x => x[0]), ['today', 'history'])
  assert.ok(page.find('today-meals')); assert.equal(page.find('meal-editor'), undefined)
  page.click('start-meal'); await page.flush()
  assert.ok(page.find('meal-editor')); assert.equal(page.find('custom-form'), undefined)
  assert.equal(page.reads.filter(x => x[0] === 'foods').length, 1)
})

test('editing uses a full PUT candidate and keeps stable rows after deletion', async () => {
  const page = await createPage()
  page.click('edit-meal'); await page.flush()
  page.click('food-add'); await page.flush()
  const secondKey = page.findAll('selected-row')[1].key
  page.click('remove-item'); await page.flush()
  assert.equal(page.find('selected-row').key, secondKey)
  page.input('selected-amount-input', '150'); await page.flush()
  await page.click('save-meal'); await page.flush()
  assert.equal(page.writes.length, 1)
  assert.deepEqual(page.writes[0], ['PUT', 'meal', { logged_at: meal.logged_at, meal_type: '午餐', items: [{ ...item, amount_g: 150, calories: 195, protein_g: 4.5, carbs_g: 42, fat_g: 1.5 }] }])
  assert.equal(page.find('meal-editor'), undefined)
})

test('invalid amount disables save and handler independently rejects it', async () => {
  const page = await createPage()
  page.click('edit-meal'); await page.flush()
  for (const raw of ['', '0', '1.', '10001']) {
    page.input('selected-amount-input', raw); await page.flush()
    assert.equal(page.find('save-meal').props.disabled, true)
    await page.click('save-meal'); await page.flush()
    assert.equal(page.writes.length, 0)
  }
})

test('cancel and switch ask before discarding; refresh keeps the draft', async () => {
  const page = await createPage()
  page.click('edit-meal'); await page.flush()
  page.input('selected-amount-input', '180'); await page.flush()
  page.hooks.useDidShow(); await page.flush()
  assert.equal(page.find('selected-amount-input').props.value, '180')
  page.click('cancel-edit'); await page.flush()
  assert.ok(page.find('discard-prompt'))
  page.click('keep-draft'); await page.flush()
  assert.equal(page.find('selected-amount-input').props.value, '180')
  page.click('start-meal'); await page.flush()
  assert.ok(page.find('discard-prompt'))
  page.click('discard-draft'); await page.flush()
  assert.equal(page.findAll('selected-row').length, 0)
  assert.equal(page.writes.length, 0)
})

test('failed save retains the draft and successful in-flight save cannot double-submit', async () => {
  let calls = 0
  const response = deferred()
  const page = await createPage({ updateMeal: async () => { calls++; if (calls === 1) throw new Error('timeout'); return response.promise } })
  page.click('edit-meal'); await page.flush()
  page.input('selected-amount-input', '150'); await page.flush()
  await page.click('save-meal'); await page.flush()
  assert.match(page.text(), /先刷新餐次记录核对/)
  assert.equal(page.find('selected-amount-input').props.value, '150')
  const save = page.click('save-meal')
  await page.click('save-meal')
  assert.equal(calls, 2)
  response.resolve({}); await save
})

test('late search responses are ignored and errors are section-local', async () => {
  const first = deferred(), second = deferred()
  const page = await createPage({ history: async () => { throw new Error('历史暂不可用') },
    foods: query => query === '旧' ? first.promise : query === '新' ? second.promise : Promise.resolve([food]) })
  assert.ok(page.find('today-meals')); assert.ok(page.find('history-retry'))
  page.click('start-meal'); await page.flush()
  page.input('search-input', '旧'); await page.flush(); const oldSearch = page.click('search-button')
  page.input('search-input', '新'); await page.flush(); const newSearch = page.click('search-button')
  second.resolve([{ ...food, name_zh: '新结果' }]); await newSearch; await page.flush()
  first.resolve([{ ...food, name_zh: '旧结果' }]); await oldSearch; await page.flush()
  assert.match(page.text(), /新结果/); assert.doesNotMatch(page.text(), /旧结果/)
  assert.equal(page.writes.length, 0)
})

test('today failure does not hide history or prevent a new in-memory draft', async () => {
  const old = { ...summary, date: '2026-01-01', meals: [{ ...meal, logged_at: '2026-01-01' }] }
  const page = await createPage({ today: async () => { throw new Error('今日暂不可用') }, history: async () => [old] })
  assert.ok(page.find('today-retry')); assert.ok(page.find('history-day'))
  page.click('start-meal'); await page.flush()
  page.click('food-add'); await page.flush()
  assert.equal(page.findAll('selected-row').length, 1)
})

test('custom form requires explicit calories and scales actual edited inputs without replacing the row', async () => {
  const page = await createPage()
  page.click('start-meal'); await page.flush()
  page.click('toggle-custom'); await page.flush()
  page.input('custom-input', '自制餐'); await page.flush()
  page.click('custom-add'); await page.flush()
  assert.equal(page.findAll('selected-row').length, 0)
  page.input('small-input', '130', 1); await page.flush()
  page.input('small-input', '3', 2); await page.flush()
  page.input('small-input', '28', 3); await page.flush()
  page.input('small-input', '1', 4); await page.flush()
  page.click('custom-add'); await page.flush()
  const key = page.find('selected-row').key
  page.input('selected-name-input', '改名后的自制餐'); await page.flush()
  assert.equal(page.find('selected-row').key, key)
  page.input('selected-amount-input', ''); await page.flush()
  assert.equal(page.find('save-meal').props.disabled, true)
  page.input('selected-amount-input', '150'); await page.flush()
  assert.equal(page.findAll('small-input')[0].props.value, '195')
  page.input('small-input', '300', 0); await page.flush()
  page.input('selected-amount-input', '200'); await page.flush()
  assert.equal(page.findAll('small-input')[0].props.value, '400')
  await page.click('save-meal')
  assert.equal(page.writes[0][1].items[0].calories, 400)
  assert.equal(page.writes[0][1].items[0].food_name, '改名后的自制餐')
})

test('deleting a whole meal is explicit and does not submit its editor draft', async () => {
  const page = await createPage()
  page.click('edit-meal'); await page.flush()
  page.input('selected-amount-input', '200'); await page.flush()
  page.click('delete-meal'); await page.flush()
  assert.ok(page.find('delete-prompt')); assert.equal(page.writes.length, 0)
  await page.click('confirm-delete'); await page.flush()
  assert.deepEqual(page.writes, [['DELETE', 'meal']])
  assert.equal(page.find('meal-editor'), undefined)
})
test('saving cannot silently discard a not-yet-added custom food', async () => {
  const page = await createPage()
  page.click('edit-meal'); await page.flush()
  page.click('toggle-custom'); await page.flush()
  page.input('custom-input', '尚未添加的食物'); await page.flush()
  await page.click('save-meal'); await page.flush()
  assert.equal(page.writes.length, 0)
  assert.match(page.text(), /还有未添加的自定义食物/)
  assert.equal(page.find('custom-input').props.value, '尚未添加的食物')
  page.click('clear-custom'); await page.flush()
  await page.click('save-meal')
  assert.equal(page.writes.length, 1)
})
