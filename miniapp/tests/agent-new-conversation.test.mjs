import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

// Exercise the real page's event handlers and async continuations. Platform
// dependencies and hook storage are isolated; no backend or native UI is used.
const output = ts.transpileModule(readFileSync(
  new URL('../src/pages/agent/index.tsx', import.meta.url), 'utf8'
), {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    jsx: ts.JsxEmit.ReactJSX,
    esModuleInterop: true
  },
  fileName: 'index.tsx'
}).outputText

function deferred () {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const history = [
  { id: 'old-user', role: 'user', content: '旧问题', content_data: {} },
  { id: 'old-answer', role: 'assistant', content: '旧回答', content_data: {} }
]

function createPage ({ savedId = 'old-conversation', pending = null, api = {} } = {}) {
  const slots = []
  let cursor = 0
  let onLoad
  let tree
  const state = { savedId, pending, modalCalls: 0, submissions: [], runs: [] }
  const storage = {
    getAgentConversationId: () => state.savedId,
    saveAgentConversationId: value => { state.savedId = value },
    clearAgentConversationId: () => { state.savedId = '' },
    getPendingAgentRequest: () => state.pending,
    savePendingAgentRequest: value => { state.pending = value },
    clearPendingAgentRequest: () => { state.pending = null }
  }
  const agentApi = {
    messages: api.messages || (async () => history),
    submit: async (...args) => {
      state.submissions.push(args)
      return api.submit
        ? api.submit(...args)
        : { run_id: 'new-run', conversation_id: 'new-conversation' }
    },
    run: async id => {
      state.runs.push(id)
      return api.run
        ? api.run(id)
        : { id, status: 'completed', reply: '新回答', cards: [] }
    }
  }
  const imports = {
    react: {
      useState: initial => {
        const index = cursor++
        if (!(index in slots)) slots[index] = initial
        return [slots[index], value => {
          slots[index] = typeof value === 'function' ? value(slots[index]) : value
        }]
      },
      useRef: initial => {
        const index = cursor++
        if (!(index in slots)) slots[index] = { current: initial }
        return slots[index]
      },
      useEffect: () => {}
    },
    'react/jsx-runtime': {
      jsx: (type, props) => ({ type, props }),
      jsxs: (type, props) => ({ type, props })
    },
    '@tarojs/components': Object.fromEntries(
      ['Button', 'ScrollView', 'Text', 'Textarea', 'View'].map(name => [name, name])
    ),
    '@tarojs/taro': {
      __esModule: true,
      default: {
        showModal: async () => {
          state.modalCalls++
          throw { errMsg: 'showModal:fail The "fd" argument must be of type number. Received undefined' }
        }
      },
      useLoad: callback => { onLoad = callback },
      useDidShow: () => {}
    },
    '../../core/build-info': { miniappBuildLabel: () => 'test-build' },
    '../../core/proposal-reference': { proposalReferenceFromUnknown: () => null },
    '../../core/proposal-interaction': {},
    '../../core/request': { errorMessage: error => error.message || error.errMsg },
    '../../core/storage': storage,
    '../../services/agent': { agentApi },
    '../../services/proposals': { proposalsApi: {} },
    '../../services/plan-management': { planManagementApi: {} },
    './index.scss': {}
  }
  const loaded = { exports: {} }
  new Function('require', 'exports', 'module', output)(name => {
    assert.ok(name in imports, `Unexpected import: ${name}`)
    return imports[name]
  }, loaded.exports, loaded)

  function render () {
    cursor = 0
    tree = loaded.exports.default()
  }
  function allNodes (node) {
    if (Array.isArray(node)) return node.flatMap(item => allNodes(item))
    if (!node || typeof node !== 'object') return []
    return [node, ...allNodes(node.props?.children)]
  }
  function find (className) {
    return allNodes(tree).find(node => node.props.className?.split(' ').includes(className))
  }
  render()
  return {
    state,
    find,
    render,
    mount: () => onLoad(),
    click: className => {
      const target = find(className)
      assert.ok(target, `Missing control: ${className}`)
      return target.props.onClick()
    },
    text: () => JSON.stringify(tree),
    flush: async () => {
      await new Promise(resolve => setImmediate(resolve))
      render()
    }
  }
}

test('new conversation works with a broken native modal and submits without the old ID', async () => {
  const page = createPage()
  page.mount()
  await page.flush()
  assert.match(page.text(), /旧回答/)
  page.click('new-chat')
  await page.flush()
  assert.equal(page.state.savedId, '')
  assert.doesNotMatch(page.text(), /旧回答/)
  assert.ok(page.find('quick-prompts'))
  await page.click('quick-prompt')
  await page.flush()
  assert.equal(page.state.submissions.length, 1)
  assert.equal(page.state.submissions[0][2], undefined)
  assert.match(page.text(), /新回答/)
  assert.equal(page.state.modalCalls, 0)
})

test('pending reply uses inline confirmation and continuing to wait preserves the request', async () => {
  const response = deferred()
  const pending = { client_request_id: 'pending', message: '旧问题', run_id: 'old-run', conversation_id: 'old-conversation' }
  const page = createPage({ pending, api: { run: () => response.promise } })
  page.mount()
  await page.flush()
  page.click('new-chat')
  await page.flush()
  assert.ok(page.find('new-conversation-prompt'))
  assert.equal(page.state.pending, pending)
  page.click('keep-conversation')
  await page.flush()
  assert.equal(page.find('new-conversation-prompt'), undefined)
  assert.equal(page.state.savedId, 'old-conversation')
  response.resolve({ id: 'old-run', status: 'completed', reply: '等待后完成', cards: [] })
  await page.flush()
  assert.match(page.text(), /等待后完成/)
  assert.equal(page.state.modalCalls, 0)
})

test('a late submission cannot restore an abandoned conversation or unlock a newer request', async () => {
  const first = deferred()
  const second = deferred()
  let calls = 0
  const page = createPage({ savedId: '', api: { submit: () => ++calls === 1 ? first.promise : second.promise } })
  page.mount()
  await page.flush()
  const firstSend = page.click('quick-prompt')
  await page.flush()
  page.click('new-chat')
  await page.flush()
  page.click('confirm-new-conversation')
  await page.flush()
  const secondSend = page.click('quick-prompt')
  const currentPending = page.state.pending
  first.resolve({ run_id: 'old-run', conversation_id: 'old-conversation' })
  await firstSend
  // Use the same not-yet-rerendered handler to exercise the synchronous send lock.
  await page.click('quick-prompt')
  assert.equal(page.state.submissions.length, 2)
  assert.equal(page.state.savedId, '')
  assert.equal(page.state.pending, currentPending)
  assert.deepEqual(page.state.runs, [])
  second.resolve({ run_id: 'new-run', conversation_id: 'new-conversation' })
  await secondSend
  await page.flush()
  assert.equal(page.state.savedId, 'new-conversation')
  assert.deepEqual(page.state.runs, ['new-run'])
  assert.equal(page.state.modalCalls, 0)
})

test('cancelled history restoration cannot restart its saved pending request', async () => {
  const response = deferred()
  const page = createPage({
    pending: { client_request_id: 'pending', message: '旧问题', run_id: 'old-run', conversation_id: 'old-conversation' },
    api: { messages: () => response.promise }
  })
  page.mount()
  page.click('new-chat')
  await page.flush()
  page.click('confirm-new-conversation')
  response.resolve(history)
  await page.flush()
  assert.equal(page.state.savedId, '')
  assert.equal(page.state.pending, null)
  assert.deepEqual(page.state.runs, [])
  assert.doesNotMatch(page.text(), /旧回答|新回答/)
})

for (const fail of [false, true]) {
  test(`late ${fail ? 'failed' : 'successful'} history loading cannot replace a new conversation`, async () => {
    const response = deferred()
    const page = createPage({ api: { messages: () => response.promise } })
    page.mount()
    page.click('new-chat')
    await page.flush()
    if (fail) response.reject(new Error('旧会话加载失败'))
    else response.resolve(history)
    await page.flush()
    assert.equal(page.state.savedId, '')
    assert.doesNotMatch(page.text(), /旧回答|旧会话加载失败/)
    assert.equal(page.find('loading-state'), undefined)
    assert.ok(page.find('quick-prompts'))
  })

  test(`late ${fail ? 'failed' : 'successful'} run responses cannot affect a new conversation`, async () => {
    const response = deferred()
    const page = createPage({
      pending: { client_request_id: 'pending', message: '旧问题', run_id: 'old-run', conversation_id: 'old-conversation' },
      api: { run: () => response.promise }
    })
    page.mount()
    await page.flush()
    page.click('new-chat')
    await page.flush()
    page.click('confirm-new-conversation')
    await page.flush()
    if (fail) response.reject(new Error('旧请求错误'))
    else response.resolve({ id: 'old-run', status: 'completed', reply: '过时回答', cards: [] })
    await page.flush()
    assert.equal(page.state.savedId, '')
    assert.equal(page.state.pending, null)
    assert.doesNotMatch(page.text(), /过时回答|旧请求错误/)
    assert.equal(page.find('typing'), undefined)
    assert.ok(page.find('quick-prompts'))
  })
}
