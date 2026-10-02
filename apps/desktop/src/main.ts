import { createApp } from 'vue'
import App from './App.vue'
import './styles.css'
import { installTauriWorkerBridge } from './tauriBridge'

const bridgeSetup = installTauriWorkerBridge()
createApp(App).mount('#app')
void bridgeSetup
