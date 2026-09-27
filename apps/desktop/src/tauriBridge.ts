import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { WorkerBridge } from './workerClient'

export async function installTauriWorkerBridge(): Promise<boolean> {
  if (!window.__TAURI_INTERNALS__ || window.__PIXELFLOW_WORKER__) return false

  const listeners = new Set<(line: string) => void>()
  await listen<string>('worker-line', (event) => {
    listeners.forEach((listener) => listener(event.payload))
  })
  await listen<{ message?: string; task_id?: string; retryable?: boolean } | string>('worker-exit', (event) => {
    const payload = typeof event.payload === 'string' ? { message: event.payload } : event.payload
    const line = JSON.stringify({
      protocol: 1,
      request_id: 'worker',
      type: 'error',
      payload: {
        message: payload?.message || 'Worker 已退出',
        task_id: payload?.task_id,
        retryable: payload?.retryable,
      },
    })
    listeners.forEach((listener) => listener(line))
  })
  const bridge: WorkerBridge = {
    send(line) {
      void invoke('worker_send', { line }).catch((error) => {
        const request_id = JSON.parse(line).request_id
        const failed = JSON.stringify({ protocol: 1, request_id, type: 'error', payload: { message: String(error) } })
        listeners.forEach((listener) => listener(failed))
      })
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
  window.__PIXELFLOW_WORKER__ = bridge
  return true
}
