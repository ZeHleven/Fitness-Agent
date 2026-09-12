import assert from 'node:assert/strict'
import test from 'node:test'
import { deferred, runtime } from './helpers/page-runtime.mjs'

const food = { id: 'rice', name_zh: '杂粮饭', calories_per_100g: 130, protein_g: 3, carbs_g: 28, fat_g: 1 }
const item = { food_id: 'rice', food_name: '杂粮饭', amount_g: 100, calories: 130, protein_g: 3, carbs_g: 28, fat_g: 1 }
const meal = { id: 'meal', logged_at: '2026-09-07', meal_type: '午餐', items: [item] }
const summary = { date: meal.logged_at, meals: [meal], total_calories: 130, total_protein_g: 3, total_carbs_g: 28, total_fat_g: 1 }
async function createPage (overrides = {}, platform = {}) {
  const writes = [], reads = [], foodWrites = [], activityWrites = [], scrolls = [], nextTicks = []
  const hooks = {}
  const privateFood = data => ({ id: 'mine', source: 'custom', version: data.version ? data.version + 1 : 1, name_zh: data.name,
    calories_per_100g: data.calories / data.amount_g * 100, protein_g: data.protein_g / data.amount_g * 100,
    carbs_g: data.carbs_g / data.amount_g * 100, fat_g: data.fat_g / data.amount_g * 100, basis: data })
  const api = {
    today: async () => summary, history: async () => [summary], foods: async () => [food],
    createFood: async data => { foodWrites.push(['POST', data]); return privateFood(data) },
    updateFood: async (id, data) => { foodWrites.push(['PUT', id, data]); return privateFood(data) },
    deleteFood: async (id, version) => { foodWrites.push(['DELETE', id, version]) },
    logMeal: async data => { writes.push(['POST', data]); return data },
    updateMeal: async (id, data) => { writes.push(['PUT', id, data]); return data },
    deleteMeal: async id => { writes.push(['DELETE', id]) }, ...overrides
  }
  for (const key of ['today', 'history', 'foods']) {
    const original = api[key]
    api[key] = (...args) => { reads.push([key, ...args]); return original(...args) }
  }
  const page = runtime('../../src/pages/nutrition/index.tsx', {
    '../../services/nutrition': { nutritionApi: api }, '../../core/request': { errorMessage: e => e.message },
    '../../services/profile': { profileApi: { update: async data => { activityWrites.push(data) } } },
    '@tarojs/taro': { __esModule: true, useDidShow: callback => { hooks.useDidShow = callback }, useDidHide: callback => { hooks.useDidHide = callback }, default: {
      showToast: async () => {}, nextTick: callback => { if(platform.nextTick) return platform.nextTick(callback); nextTicks.push(callback) },
      navigateTo: options => platform.navigateTo ? platform.navigateTo(options) : Promise.resolve(),
      pageScrollTo: () => { throw Error('Nutrition must scroll its bounded viewport, not the native page') }
    } }
  })
  page.render(); hooks.useDidShow(); await page.flush()
  return { ...page, hooks, writes, reads, foodWrites, activityWrites, scrolls, nextTicks }
}

async function renderAndScroll (page) {
  await page.flush()
  let ticks=0
  while(page.nextTicks.length) {
    assert.ok(++ticks<10,'scroll positioning must settle')
    const before=page.find('nutrition-scroll').props.scrollIntoView
    page.nextTicks.splice(0).forEach(callback => callback())
    await page.flush()
    const anchor=page.find('nutrition-scroll').props.scrollIntoView
    if(anchor && anchor!==before)page.scrolls.push({anchor})
  }
}

test('classification correction return refreshes energy, labels and reasons while preserving unsaved meal', async () => {
  let corrected = false
  const navigations = []
  const page = await createPage({ today: async () => ({ ...summary, energy_estimate: {
    status: corrected ? 'estimated' : 'partial', reasons: [], total_kcal: 2140, balance_kcal: corrected ? -2010 : null,
    workouts: [{ session_id: 'session/special', plan_name: '原先的周三计划', trained_at: '2026-09-08', started_at: '2026-09-08T07:22:00Z',
      reason: corrected ? null : '实际训练的自定义动作尚未补充估算分类', reason_code: corrected ? null : 'classification_missing',
      unestimated_exercises: corrected ? [] : [{ session_exercise_id: 'sx', exercise_name: '弹力带划船', reason_code: 'classification_missing' }] }]
  } }) }, { navigateTo: async value => navigations.push(value) })
  page.click('edit-meal'); await page.flush()
  page.input('selected-amount-input', '150'); await page.flush()
  page.click('energy-toggle'); await page.flush()
  assert.equal(page.findAll('energy-label')[1].props.children, '已估算消耗')
  assert.match(page.text(), /2026-09-08 15:22/)
  assert.match(page.text(), /原先的周三计划/); assert.match(page.text(), /弹力带划船/)
  assert.equal(page.find('energy-workout-detail').props.children, '补充分类')
  await page.click('energy-workout-detail')
  assert.deepEqual(navigations, [{ url: '/pages/workout-detail/index?id=session%2Fspecial' }])
  corrected = true
  // This test substitutes the service; model the invalidation performed by the real PUT transport.
  page.loadSource('../../src/core/read-cache.ts').invalidateReadCache()
  page.hooks.useDidShow(); await page.flush()
  assert.equal(page.findAll('energy-label')[1].props.children, '全天消耗')
  assert.equal(page.find('energy-warning'), undefined)
  assert.equal(page.find('selected-amount-input').props.value, '150')
  assert.equal(page.writes.length, 0)
})

test('invalid training time links to details, never suggests classification can repair it', async () => {
  const page = await createPage({ today: async () => ({ ...summary, energy_estimate: { status:'partial', reasons:[], total_kcal:2136,
    workouts:[{session_id:'s', reason:'自动计时时长异常',reason_code:'invalid_timing', performed_exercises:[{session_exercise_id:'x',exercise_name:'测试深蹲'}]}] } }) })
  page.click('energy-toggle'); await page.flush()
  assert.equal(page.find('energy-workout-detail').props.children, '查看训练')
  assert.match(page.text(), /测试深蹲/)
})

test('energy heading is concise while the estimate basis and disclaimer remain available', async () => {
  const page = await createPage()
  assert.equal(page.findAll('energy-label')[0].props.children, '基础代谢')
  assert.match(page.text(), /按已记录饮食与估算消耗计算，仅供参考/)
  page.click('energy-toggle'); await page.flush()
  assert.match(page.text(), /静息能量估算/)
})

for (const source of ['standard', 'custom']) {
  test(`adding ${source} library food scrolls after render on every success without saving the meal`, async () => {
    const page = await createPage({ foods: async () => [{ ...food, source, version: 1 }] })
    page.click('edit-meal'); await renderAndScroll(page)
    page.scrolls.length = 0
    page.click('food-add')
    assert.equal(page.scrolls.length, 0, 'wait for the updated editor layout')
    await renderAndScroll(page)
    assert.deepEqual(page.scrolls, [{ anchor: 'meal-editor' }])
    assert.equal(page.findAll('selected-row').length, 2)
    page.click('food-add'); await renderAndScroll(page)
    assert.equal(page.scrolls.length, 2)
    page.input('selected-amount-input', '150'); await renderAndScroll(page)
    await page.click('refresh-meals'); await renderAndScroll(page)
    assert.equal(page.scrolls.length, 2, 'editing and refreshing do not pull the viewport away')
    assert.equal(page.writes.length, 0)
    assert.equal(page.foodWrites.length, 0)
  })
}

test('invalid portions and full meals do not trigger a successful-add scroll', async () => {
  const page = await createPage()
  page.click('start-meal'); await renderAndScroll(page); page.scrolls.length = 0
  page.input('portion-input', ''); await page.flush()
  page.click('food-add'); await renderAndScroll(page)
  assert.equal(page.scrolls.filter(x => x.anchor === 'meal-editor').length, 0)
  assert.equal(page.findAll('selected-row').length, 0)
  page.input('portion-input', '100'); await page.flush()
  for (let i = 0; i < 30; i++) { page.click('food-add'); await renderAndScroll(page) }
  page.scrolls.length = 0
  page.click('food-add'); await renderAndScroll(page)
  assert.equal(page.findAll('selected-row').length, 30)
  assert.equal(page.scrolls.filter(x => x.anchor === 'meal-editor').length, 0)
  assert.equal(page.writes.length, 0)
})

test('new custom food scrolls only after creation succeeds; a slow library refresh does not delay it', async () => {
  const gate = deferred(), refresh = deferred()
  let created = false, fail = true
  const page = await createPage({
    foods: async () => created ? refresh.promise : [food],
    createFood: async data => {
      if (fail) throw new Error('暂不可用')
      await gate.promise; created = true
      return { ...food, source: 'custom', version: 1, name_zh: data.name }
    }
  })
  page.click('start-meal'); await renderAndScroll(page)
  page.click('toggle-custom'); await page.flush()
  page.input('custom-input', '新食品'); page.input('small-input', '130', 1); await page.flush()
  page.scrolls.length = 0
  await page.click('custom-add'); await renderAndScroll(page)
  assert.equal(page.scrolls.filter(x => x.anchor === 'meal-editor').length, 0)
  assert.equal(page.find('custom-input').props.value, '新食品')
  fail = false
  const saving = page.click('custom-add'); await renderAndScroll(page)
  assert.equal(page.scrolls.filter(x => x.anchor === 'meal-editor').length, 0)
  gate.resolve(); await renderAndScroll(page)
  assert.equal(page.findAll('selected-row').length, 1)
  assert.equal(page.scrolls.filter(x => x.anchor === 'meal-editor').length, 1)
  refresh.resolve([food]); await saving; await renderAndScroll(page)
  assert.equal(page.scrolls.filter(x => x.anchor === 'meal-editor').length, 1)
  assert.equal(page.writes.length, 0)
})

test('editing an existing library food does not scroll back to or change the meal draft', async () => {
  const page = await createPage({ foods: async () => [{ ...food, source: 'custom', version: 1,
    basis: { name: '米饭', amount_g: 100, calories: 130, protein_g: 3, carbs_g: 28, fat_g: 1 } }] })
  page.click('edit-meal'); await renderAndScroll(page)
  page.click('library-edit'); await renderAndScroll(page); page.scrolls.length = 0
  page.input('custom-input', '新名称'); await page.flush()
  await page.click('custom-add'); await renderAndScroll(page)
  assert.equal(page.scrolls.length, 0)
  assert.equal(page.findAll('selected-row').length, 1)
  assert.equal(page.writes.length, 0)
})

for (const failure of ['throw', 'unavailable']) {
  test(`scroll ${failure} never discards the added food or prevents saving`, async () => {
    const page = await createPage({}, { nextTick: () => {
      if (failure === 'throw') throw new Error('scroll failed')
      // Host never delivers the layout callback.
    } })
    page.click('start-meal'); await renderAndScroll(page)
    page.click('food-add'); await renderAndScroll(page)
    assert.equal(page.findAll('selected-row').length, 1)
    assert.equal(page.find('save-meal').props.disabled, false)
    assert.equal(page.writes.length, 0)
    await page.click('save-meal'); await renderAndScroll(page)
    assert.equal(page.writes.length, 1)
  })
}

test('closing the editor cancels a queued scroll from a previous addition', async () => {
  const page = await createPage()
  page.click('start-meal'); await renderAndScroll(page); page.scrolls.length = 0
  page.click('food-add'); await page.flush()
  assert.equal(page.nextTicks.length, 1)
  page.click('cancel-edit'); await page.flush()
  page.click('discard-draft'); await page.flush()
  await renderAndScroll(page)
  assert.equal(page.find('meal-editor'), undefined)
  assert.equal(page.scrolls.filter(x => x.anchor === 'meal-editor').length, 0)
  assert.equal(page.writes.length, 0)
})

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
  await page.click('custom-add'); await page.flush()
  assert.equal(page.findAll('selected-row').length, 0)
  page.input('small-input', '130', 1); await page.flush()
  page.input('small-input', '3', 2); await page.flush()
  page.input('small-input', '28', 3); await page.flush()
  page.input('small-input', '1', 4); await page.flush()
  await page.click('custom-add'); await page.flush()
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
  assert.ok(page.find('custom-discard-prompt'))
  page.click('discard-custom'); await page.flush()
  await page.click('save-meal')
  assert.equal(page.writes.length, 1)
})

test('custom creation retries retain the same request id, lock concurrent clicks and never log a meal', async () => {
  const attempts = [], gate = deferred()
  const page = await createPage({ createFood: async data => {
    attempts.push(data)
    if (attempts.length === 1) throw new Error('响应丢失')
    await gate.promise
    return { ...food, id: 'mine', name_zh: data.name, source: 'custom', version: 1 }
  } })
  page.click('start-meal'); await page.flush(); page.click('toggle-custom'); await page.flush()
  page.input('custom-input', '我的酸奶'); page.input('small-input', '130', 1); await page.flush()
  await page.click('custom-add'); await page.flush()
  assert.equal(page.find('custom-input').props.value, '我的酸奶')
  const retry = page.click('custom-add'); await page.click('custom-add')
  assert.equal(attempts.length, 2)
  assert.equal(attempts[0].client_request_id, attempts[1].client_request_id)
  gate.resolve(); await retry; await page.flush()
  assert.equal(page.findAll('selected-row').length, 1)
  assert.equal(page.writes.length, 0)
})

test('library modification and deletion preserve an existing meal draft and use versioned writes', async () => {
  const privateFood = { ...food, id: 'mine', source: 'custom', version: 2,
    basis: { name: '我的米饭', amount_g: 100, calories: 130, protein_g: 3, carbs_g: 28, fat_g: 1 } }
  const page = await createPage({ foods: async () => [privateFood] })
  page.click('edit-meal'); await page.flush()
  page.input('selected-amount-input', '180'); await page.flush()
  page.click('library-edit'); await page.flush()
  page.input('custom-input', '我的新米饭'); await page.flush()
  await page.click('custom-add'); await page.flush()
  assert.equal(page.foodWrites[0][0], 'PUT')
  assert.equal(page.foodWrites[0][2].version, 2)
  assert.equal(page.find('selected-amount-input').props.value, '180')
  assert.equal(page.writes.length, 0)
  page.click('library-delete'); await page.flush()
  assert.ok(page.find('food-delete-prompt'))
  await page.click('confirm-food-delete'); await page.flush()
  assert.deepEqual(page.foodWrites[1], ['DELETE', 'mine', 2])
  assert.equal(page.find('selected-amount-input').props.value, '180')
})

test('switching custom food editor asks before discarding and failed save retains inputs', async () => {
  const privateFood = { ...food, source: 'custom', version: 1, basis: { name: '米饭', amount_g: 100, calories: 130, protein_g: 3, carbs_g: 28, fat_g: 1 } }
  const page = await createPage({ foods: async () => [privateFood], updateFood: async () => { throw new Error('版本冲突') } })
  page.click('start-meal'); await page.flush(); page.click('library-edit'); await page.flush()
  page.input('custom-input', '新名称'); await page.flush()
  await page.click('custom-add'); await page.flush()
  assert.match(page.text(), /版本冲突/)
  assert.equal(page.find('custom-input').props.value, '新名称')
  page.click('clear-custom'); await page.flush(); page.click('keep-custom'); await page.flush()
  assert.equal(page.find('custom-input').props.value, '新名称')
  assert.equal(page.writes.length, 0)
})

test('energy details save activity without resetting a meal draft and incomplete estimates remain unknown', async () => {
  const energy = { status: 'partial', bmr_kcal: 1780, total_kcal: 2136, balance_kcal: null,
    reasons: ['有训练未计入'], activity_level: 'sedentary', activity_defaulted: true,
    workouts: [{ session_id: 's', reason: '自定义动作未估算' }] }
  const page = await createPage({ today: async () => ({ ...summary, energy_estimate: energy }) })
  assert.match(page.text(), /部分估算/)
  page.click('edit-meal'); await page.flush(); page.input('selected-amount-input', '180'); await page.flush()
  page.click('energy-toggle'); await page.flush()
  await page.click('activity-walking'); await page.flush()
  assert.deepEqual(page.activityWrites, [{ daily_activity_level: 'walking' }])
  assert.equal(page.find('selected-amount-input').props.value, '180')
  assert.match(page.text(), /自定义动作未估算/)
  assert.equal(page.writes.length, 0)
})
