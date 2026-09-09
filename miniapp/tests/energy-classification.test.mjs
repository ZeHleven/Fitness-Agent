import assert from 'node:assert/strict'
import test from 'node:test'
import { runtime, deferred } from './helpers/page-runtime.mjs'

const row = { id: 'sx', exercise_id: 'custom', exercise_name: '弹力带划船', sets_data: [{ reps: 8 }], energy_category: null,
  energy_classification_editable: true, library_energy_editable: true, library_energy_category: null, library_energy_category_version: 0 }
const initial = () => ({ id: 'session', energy_classification_version: 0, status: 'completed', exercises: [{ ...row }], adjustments: [] })
const finished = (request) => ({ ...initial(), energy_classification_version: request.expected_version + 1, exercises: [{ ...row,
  energy_category: request.exercises[0].energy_category,
  ...(request.update_future ? { library_energy_category: request.exercises[0].energy_category, library_energy_category_version: request.exercises[0].expected_exercise_version + 1 } : {}) }] })
const choose = (page, cls, value, index = 0) => page.findAll(cls)[index].props.onChange({ detail: { value: String(value) } })
const errors = { errorMessage: e => e.message }
function editor (api, input = initial()) {
  let current = input
  const page = runtime('../../src/components/SessionEnergyEditor.tsx', { '../services/workouts': { workoutApi: api }, '../core/request': errors })
  const component = page.exports.default
  page.exports.default = () => component({ session: current, onSaved: value => { current = value } })
  page.render()
  return { ...page, current: () => current, refresh: data => { current = data; page.render() } }
}

test('history editor is initially read-only, submits selected rows only and defaults to current session', async () => {
  const writes = []
  const page = editor({ updateEnergyClassifications: async (id, request) => { writes.push([id, request]); return finished(request) } })
  assert.equal(writes.length, 0)
  assert.equal(page.find('save-session-energy').props.disabled, true)
  assert.match(page.find('energy-future-toggle').props.children.join(''), /☐/)
  choose(page, 'session-energy-picker', 1); await page.flush()
  await page.click('save-session-energy'); await page.flush()
  assert.deepEqual(writes, [['session', { expected_version: 0, update_future: false, exercises: [{ session_exercise_id: 'sx', energy_category: 'resistance_training' }] }]])
  assert.equal(page.current().exercises[0].library_energy_category, null)
  assert.match(page.text(), /分类已保存/)
})

test('explicit future choice carries the library version and repeated clicks cannot duplicate writes', async () => {
  const pending = deferred(), writes = []
  const page = editor({ updateEnergyClassifications: (id, body) => { writes.push(body); return pending.promise } })
  choose(page, 'session-energy-picker', 2); await page.flush()
  page.click('energy-future-toggle'); await page.flush()
  const first = page.click('save-session-energy'); const second = page.click('save-session-energy')
  assert.equal(writes.length, 1)
  assert.equal(writes[0].update_future, true)
  assert.equal(writes[0].exercises[0].expected_exercise_version, 0)
  pending.resolve(finished(writes[0])); await first; await second; await page.flush()
  assert.equal(page.current().exercises[0].library_energy_category, 'bodyweight_resistance')
})

test('lost save response reads back applied data without replaying the mutation', async () => {
  let request, reads = 0, writes = 0
  const page = editor({ updateEnergyClassifications: async (_, body) => { writes++; request = body; throw new Error('响应丢失') },
    detail: async () => { reads++; return finished(request) } })
  choose(page, 'session-energy-picker', 1); await page.flush()
  await page.click('save-session-energy'); await page.flush()
  assert.equal(writes, 1); assert.equal(reads, 1)
  assert.match(page.text(), /分类已保存/)
  assert.equal(page.find('verify-session-energy'), undefined)
})

test('offline reconciliation keeps selection and blocks writes until explicit read succeeds', async () => {
  let offline = true, writes = 0, request
  const page = editor({ updateEnergyClassifications: async (_, body) => { writes++; request = body; throw new Error('断网') },
    detail: async () => { if (offline) throw new Error('断网'); return finished(request) } })
  choose(page, 'session-energy-picker', 1); await page.flush()
  await page.click('save-session-energy'); await page.flush()
  assert.equal(page.find('session-energy-picker').props.value, 1)
  assert.equal(page.find('save-session-energy').props.disabled, true)
  await page.click('save-session-energy'); assert.equal(writes, 1)
  offline = false; await page.click('verify-session-energy'); await page.flush()
  assert.equal(writes, 1); assert.match(page.text(), /分类已保存/)
})

test('version conflict preserves inputs and requires a new explicit save using reread revision', async () => {
  const requests = []
  const fresh = { ...initial(), energy_classification_version: 4, exercises: [{ ...row, energy_category: 'bodyweight_resistance', library_energy_category_version: 5 }] }
  const page = editor({ updateEnergyClassifications: async (_, request) => {
    requests.push(request); if (requests.length === 1) throw new Error('版本冲突'); return finished(request)
  }, detail: async () => fresh })
  choose(page, 'session-energy-picker', 1); await page.flush()
  // A page refresh must not silently advance an already dirty form's revision.
  page.refresh(fresh)
  await page.click('save-session-energy'); await page.flush()
  assert.equal(requests[0].expected_version, 0)
  assert.equal(page.find('session-energy-picker').props.value, 1)
  assert.match(page.text(), /请核对原分类后再保存/)
  assert.equal(requests.length, 1)
  await page.click('save-session-energy'); await page.flush()
  assert.equal(requests[1].expected_version, 4)
})

test('library editor distinguishes future-only edits and reconciles lost responses without overwriting selection', async () => {
  let options = [{ exercise_id: 'custom', exercise_name: '动作', energy_category: null, energy_category_version: 0 }]
  let writes = 0, offline = true
  const page = runtime('../../src/components/EnergyLibraryEditor.tsx', {
    '../core/request': errors, '../services/exercises': { exerciseApi: {
      updateEnergyCategory: async (_, body) => { writes++; options = [{ ...options[0], ...body, energy_category_version: body.expected_version + 1 }]; throw new Error('响应丢失') },
      custom: async () => { if (offline) throw new Error('断网'); return options }
    } }
  })
  const component = page.exports.default
  page.exports.default = () => component({ options, onUpdated: value => { options = [value] } })
  page.render(); choose(page, 'library-exercise-picker', 1); await page.flush()
  choose(page, 'library-energy-picker', 1); await page.flush()
  await page.click('save-library-energy'); await page.flush()
  assert.equal(page.find('library-energy-picker').props.value, 1)
  assert.equal(page.find('save-library-energy').props.disabled, true)
  await page.click('save-library-energy'); assert.equal(writes, 1)
  offline = false; await page.click('verify-library-energy'); await page.flush()
  assert.equal(writes, 1); assert.match(page.text(), /仅用于以后开始的训练/)
})

for (const categoryIndex of [0, 1, 2]) {
  test(`creating a custom exercise carries optional category ${categoryIndex} without native modal`, async () => {
    let request, added = 0
    const page = runtime('../../src/components/CustomExerciseEntry.tsx', { '../core/request': errors,
      '../services/exercises': { exerciseApi: { custom: async () => [], createCustom: async body => { request = body; return { exercise_id: 'new', exercise_name: body.name } } } }
    })
    const component = page.exports.default
    page.exports.default = () => component({ onAdd: () => { added++ } })
    page.render(); await page.click('open-custom-exercise'); await page.flush()
    page.input('custom-exercise-name', '新动作'); page.input('custom-exercise-description', '常规分组方法'); await page.flush()
    choose(page, 'create-energy-picker', categoryIndex); await page.flush()
    await page.click('create-custom-exercise'); await page.flush()
    assert.equal(request.energy_category, [null, 'resistance_training', 'bodyweight_resistance'][categoryIndex])
    assert.equal(added, 1)
  })
}
