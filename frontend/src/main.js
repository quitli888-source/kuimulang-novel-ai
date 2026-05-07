import { createApp } from 'vue'
import { createPinia } from 'pinia'
import naive from 'naive-ui'
import App from './App.vue'
import router from './router'
// 样式导入 - 按顺序加载确保正确优先级
import './styles/variables.css'
import './styles/animations.css'
import './styles/dark-theme.css'
import './styles/global.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(naive)
app.mount('#app')
