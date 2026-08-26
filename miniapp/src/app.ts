import { PropsWithChildren } from 'react'
import { useLaunch } from '@tarojs/taro'

import { logMiniappBuildDiagnostic } from './core/build-info'
import { initializeCloudTransport } from './core/request'
import './app.scss'

function App({ children }: PropsWithChildren<any>) {
  useLaunch(() => {
    logMiniappBuildDiagnostic()
    initializeCloudTransport()
  })

  return children
}

export default App
