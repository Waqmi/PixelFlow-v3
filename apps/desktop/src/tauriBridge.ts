import { invoke } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'
import type { WorkerBridge } from './workerClient'

export async function installTauriWorkerBridge(): Promise<boolean> {
  if (!window.__TAURI_INTERNALS__ || window.__PIXELFLOW_WORKER__) return false

  const listeners = new Set<(line: string) => void>()
  const pendingLines: string[] = []
  const pendingRequests: string[] = []
  let eventListenersReady = false
  const emitLine = (line: string) => {
    if (!eventListenersReady) {
      pendingLines.push(line)
      return
    }
    listeners.forEach((listener) => listener(line))
  }
  const sendLine = (line: string) => {
    let request_id = 'worker'
    try { request_id = String(JSON.parse(line).request_id || 'worker') } catch { /* malformed sends fail globally */ }
    void invoke('worker_send', { line }).catch((error) => {
      emitLine(JSON.stringify({ protocol: 1, request_id, type: 'error', payload: { code: 'send_failed', message: String(error) } }))
    })
  }
  const bridge: WorkerBridge = {
    send(line) {
      if (!eventListenersReady) {
        pendingRequests.push(line)
        return
      }
      sendLine(line)
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
  // Publish the bridge before awaiting Tauri listener registration so Vue can
  // mount and reveal the already-themed window without waiting on IPC setup.
  window.__PIXELFLOW_WORKER__ = bridge
  await listen<string>('worker-line', (event) => {
    emitLine(event.payload)
  })
  await listen<{ message?: string; task_id?: string; retryable?: boolean } | string>('worker-exit', (event) => {
    const payload = typeof event.payload === 'string' ? { message: event.payload } : event.payload
    emitLine(JSON.stringify({
      protocol: 1,
      request_id: 'worker',
      type: 'error',
      payload: {
        code: 'worker_exit',
        message: payload?.message || 'Worker 已退出',
        task_id: payload?.task_id,
        retryable: payload?.retryable,
      },
    }))
  })
  eventListenersReady = true
  pendingLines.splice(0).forEach((line) => emitLine(line))
  pendingRequests.splice(0).forEach(sendLine)
  return true
}
