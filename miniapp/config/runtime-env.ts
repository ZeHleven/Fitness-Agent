const readRuntimeEnvironment = (
  environment: NodeJS.ProcessEnv,
  name: keyof NodeJS.ProcessEnv
): string => {
  const value = environment[name]
  return typeof value === 'string' ? value.trim() : ''
}

export function projectRuntimeDefineConstants (
  environment: NodeJS.ProcessEnv
): Record<string, string> {
  return {
    __FITNESS_API_BASE_URL__: JSON.stringify(
      readRuntimeEnvironment(environment, 'TARO_APP_API_BASE_URL')
    ),
    __FITNESS_CLOUD_ENV__: JSON.stringify(
      readRuntimeEnvironment(environment, 'TARO_APP_CLOUD_ENV')
    ),
    __FITNESS_CLOUD_SERVICE__: JSON.stringify(
      readRuntimeEnvironment(environment, 'TARO_APP_CLOUD_SERVICE')
    )
  }
}
