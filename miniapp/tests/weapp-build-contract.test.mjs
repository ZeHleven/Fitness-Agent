import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import ts from 'typescript'

import { validateWeappArtifact } from '../scripts/weapp-build-contract.mjs'

function loadTypeScriptModule (relativePath) {
  const sourceUrl = new URL(relativePath, import.meta.url)
  const sourcePath = fileURLToPath(sourceUrl)
  const output = ts.transpileModule(readFileSync(sourcePath, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020
    },
    fileName: sourcePath
  }).outputText
  const loaded = { exports: {} }
  const evaluate = new Function('exports', 'module', output)
  evaluate(loaded.exports, loaded)
  return loaded.exports
}

const runtimeEnvironment = loadTypeScriptModule('../config/runtime-env.ts')

function artifact (overrides = {}) {
  return {
    javascriptFiles: [{ path: 'common.js', content: 'const ready = true' }],
    appConfig: { pages: ['pages/proposal-detail/index'] },
    projectConfig: {
      miniprogramRoot: './',
      setting: { compileHotReLoad: false }
    },
    ...overrides
  }
}

test('missing optional transport variables become string literals', () => {
  assert.deepEqual(runtimeEnvironment.projectRuntimeDefineConstants({}), {
    __FITNESS_API_BASE_URL__: '""',
    __FITNESS_CLOUD_ENV__: '""',
    __FITNESS_CLOUD_SERVICE__: '""'
  })
})

test('runtime transport values are trimmed and safely quoted', () => {
  assert.deepEqual(runtimeEnvironment.projectRuntimeDefineConstants({
    TARO_APP_API_BASE_URL: ' https://api.example.test/api/v1/ ',
    TARO_APP_CLOUD_ENV: ' env-test ',
    TARO_APP_CLOUD_SERVICE: ' service-"quoted" '
  }), {
    __FITNESS_API_BASE_URL__: '"https://api.example.test/api/v1/"',
    __FITNESS_CLOUD_ENV__: '"env-test"',
    __FITNESS_CLOUD_SERVICE__: '"service-\\"quoted\\""'
  })
})

test('clean WeChat artifact satisfies the build contract', () => {
  assert.deepEqual(validateWeappArtifact(artifact()), [])
})

test('runtime process environment references fail the build contract', () => {
  const errors = validateWeappArtifact(artifact({
    javascriptFiles: [{
      path: 'pages/login/index.js',
      content: 'const env = process.env.TARO_APP_CLOUD_ENV'
    }]
  }))
  assert.deepEqual(errors.map(error => error.code), [
    'runtime_environment_reference'
  ])
})

test('route, hot reload, and project root violations fail independently', () => {
  const errors = validateWeappArtifact(artifact({
    appConfig: { pages: ['pages/index/index'] },
    projectConfig: {
      miniprogramRoot: 'dist/',
      setting: { compileHotReLoad: true }
    }
  }))
  assert.deepEqual(errors.map(error => error.code), [
    'required_page_missing',
    'hot_reload_enabled',
    'invalid_miniprogram_root'
  ])
})

test('project config is optional for source-only CI builds', () => {
  assert.deepEqual(validateWeappArtifact(artifact({ projectConfig: null })), [])
})
