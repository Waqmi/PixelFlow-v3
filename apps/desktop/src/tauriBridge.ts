import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { WorkerBridge } from './workerClient'

export async function installTauriWorkerBridge(): Promise<boolean> {
  if (!window.__TAURI_INTERNALS__ || window.__PIXELFLOW_WORKER__) return false

  const listeners = new Set<(line: string) => void>()
  await listen<string>('worker-line', (event) => {
    listeners.forEach((listener) => listener(event.payload))
  })
  const bridge: WorkerBridge = {
    send(line) {
      void invoke('worker_send', { line })
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
  window.__PIXELFLOW_WORKER__ = bridge
  return true
}
