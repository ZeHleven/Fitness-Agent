import { validateMiniappBuildMetadata } from './miniapp-build-metadata.mjs'

const RUNTIME_ENVIRONMENT_PATTERN = /\bprocess\s*(?:\.\s*env|\[\s*['"]env['"]\s*\])/u
const REQUIRED_PAGES = ['pages/proposal-detail/index']

export function validateWeappArtifact ({
  javascriptFiles,
  appConfig,
  projectConfig = null,
  buildManifest
}) {
  const errors = []

  for (const file of javascriptFiles) {
    if (RUNTIME_ENVIRONMENT_PATTERN.test(file.content)) {
      errors.push({
        code: 'runtime_environment_reference',
        message: `${file.path} contains a runtime process.env reference`
      })
    }
  }

  const pages = Array.isArray(appConfig?.pages) ? appConfig.pages : []
  for (const page of REQUIRED_PAGES) {
    if (!pages.includes(page)) {
      errors.push({
        code: 'required_page_missing',
        message: `app.json is missing required page ${page}`
      })
    }
  }

  if (
    projectConfig &&
    projectConfig.setting?.compileHotReLoad !== false
  ) {
    errors.push({
      code: 'hot_reload_enabled',
      message: 'project.config.json must disable compileHotReLoad'
    })
  }

  if (projectConfig && projectConfig.miniprogramRoot !== './') {
    errors.push({
      code: 'invalid_miniprogram_root',
      message: 'dist/project.config.json must use ./ as miniprogramRoot'
    })
  }

  const metadataErrors = validateMiniappBuildMetadata(buildManifest)
  if (metadataErrors.length > 0) {
    errors.push({
      code: 'build_manifest_invalid',
      message: metadataErrors.join('; ')
    })
  }

  return errors
}
