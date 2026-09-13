import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
import { installTauriWorkerBridge } from './tauriBridge'

void installTauriWorkerBridge().finally(() => createApp(App).mount('#app'))
