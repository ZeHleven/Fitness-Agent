import { PropsWithChildren } from 'react'
import { useLaunch } from '@tarojs/taro'

import { initializeCloudTransport } from './core/request'
import './app.scss'

function App({ children }: PropsWithChildren<any>) {
  useLaunch(() => {
    initializeCloudTransport()
  })

  return children
}

export default App
