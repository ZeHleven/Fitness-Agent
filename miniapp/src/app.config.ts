export default defineAppConfig({
  pages: [
    'pages/index/index',
    'pages/login/index',
    'pages/onboarding/index',
    'pages/plan-builder/index',
    'pages/agent/index',
    'pages/proposal-detail/index',
    'pages/workouts/index',
    'pages/workout-active/index',
    'pages/history/index'
  ],
  window: {
    backgroundTextStyle: 'dark',
    backgroundColor: '#f5f7f2',
    navigationBarBackgroundColor: '#163d2b',
    navigationBarTitleText: '训练搭子',
    navigationBarTextStyle: 'white'
  }
})
