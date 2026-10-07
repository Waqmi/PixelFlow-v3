import {
  createRequest,
  isWorkerEventType,
  parseWorkerMessage,
  WORKER_PROTOCOL,
  type WorkerMessage,
  type WorkerRequestType,
} from './workerProtocol'

type MessageHandler = (message: WorkerMessage) => void

export interface WorkerBridge {
  send(line: string): void
  subscribe(listener: (line: string) => void): () => void
}

type PendingRequest = {
  type: WorkerRequestType
  timer?: ReturnType<typeof setTimeout>
  resolve?: (payload: unknown) => void
  reject?: (error: Error) => void
}

export function windowWorkerBridge(): WorkerBridge | undefined {
  return window.__PIXELFLOW_WORKER__
}

export class WorkerClient {
  readonly connected: boolean
  private readonly unsubscribe: () => void
  private readonly handlers = new Set<MessageHandler>()
  private readonly pending = new Map<string, PendingRequest>()
  private nextRequest = 1

  constructor(
    private readonly bridge: WorkerBridge,
    onMessage?: MessageHandler,
    private readonly requestPrefix = 'ui',
    private readonly requestTimeoutMs = 30_000,
  ) {
    this.connected = true
    if (onMessage) this.handlers.add(onMessage)
    this.unsubscribe = bridge.subscribe((line) => this.receive(line))
  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  request<T extends object>(type: WorkerRequestType, payload: T): string {
    const requestId = `${this.requestPrefix}-${this.nextRequest++}`
    this.pending.set(requestId, { type })
    this.sendRequest(requestId, type, payload)
    return requestId
  }

  requestResult<TPayload extends object, TResult>(
    type: WorkerRequestType,
    payload: TPayload,
    timeoutMs = this.requestTimeoutMs,
  ): Promise<TResult> {
    const requestId = `${this.requestPrefix}-${this.nextRequest++}`
    return new Promise((resolve, reject) => {
      const pending: PendingRequest = {
        type,
        resolve: (result) => resolve(result as TResult),
        reject,
      }
      if (timeoutMs > 0) {
        pending.timer = setTimeout(() => {
          this.settleError(requestId, `Worker request timed out after ${timeoutMs}ms`, 'request_timeout')
        }, timeoutMs)
      }
      this.pending.set(requestId, pending)
      this.sendRequest(requestId, type, payload)
    })
  }

  cancelRequest(requestId: string) {
    const pending = this.removePending(requestId)
    pending?.reject?.(new Error('Worker request cancelled'))
  }

  dispose() {
    this.unsubscribe()
    for (const requestId of this.pending.keys()) this.cancelRequest(requestId)
    this.handlers.clear()
  }

  private sendRequest<T extends object>(requestId: string, type: WorkerRequestType, payload: T) {
    try {
      this.bridge.send(JSON.stringify(createRequest(requestId, type, payload)))
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      queueMicrotask(() => this.settleError(requestId, message, 'send_failed'))
    }
  }

  private receive(line: string) {
    const message = parseWorkerMessage(line)
    if (!message || !isWorkerEventType(message.type)) {
      this.failAll(this.errorMessage('worker', 'Worker returned an invalid protocol message', 'transport_error'))
      return
    }
    if (message.request_id === 'worker' && message.type === 'ready') {
      this.dispatch(message)
      return
    }
    if (message.request_id === 'worker' && message.type === 'error') {
      this.failAll(message)
      return
    }

    const pending = this.pending.get(message.request_id)
    if (!pending) return
    if (this.isTerminal(pending, message)) {
      this.removePending(message.request_id)
      if (message.type === 'error' || message.type === 'cancelled') {
        pending.reject?.(new Error(String((message.payload as { message?: string }).message || (message.type === 'cancelled' ? 'Worker request cancelled' : 'Worker request failed'))))
      } else {
        pending.resolve?.(message.payload)
      }
    }
    this.dispatch(message)
  }

  private isTerminal(pending: PendingRequest, message: WorkerMessage) {
    if (message.type === 'done' || message.type === 'cancelled' || message.type === 'error') return true
    if (pending.type === 'ping' && message.type === 'ready') return true
    if (pending.type === 'cancel_generation' && message.type === 'status') {
      const state = (message.payload as { state?: unknown }).state
      return state === 'cancelling' || state === 'idle'
    }
    return false
  }

  private settleError(requestId: string, message: string, code: string) {
    const pending = this.pending.get(requestId)
    if (!pending) return
    this.removePending(requestId)
    const event = this.errorMessage(requestId, message, code)
    pending.reject?.(new Error(message))
    this.dispatch(event)
  }

  private failAll(message: WorkerMessage) {
    const snapshot = [...this.pending.entries()]
    const pending = snapshot.map(([requestId, request]) => {
      this.removePending(requestId)
      return { requestId, request }
    })
    const payload = message.payload as { code?: unknown; message?: unknown }
    const detail = String(payload.message || 'Worker transport failed')
    const code = String(payload.code || 'transport_error')
    pending.forEach(({ request }) => request.reject?.(new Error(detail)))
    this.dispatch(message)
    pending.forEach(({ requestId }) => {
      this.dispatch({ ...message, request_id: requestId, payload: { ...message.payload, code, message: detail } })
    })
  }

  private removePending(requestId: string) {
    const pending = this.pending.get(requestId)
    if (pending?.timer !== undefined) clearTimeout(pending.timer)
    this.pending.delete(requestId)
    return pending
  }

  private errorMessage(requestId: string, message: string, code: string): WorkerMessage {
    return {
      protocol: WORKER_PROTOCOL,
      request_id: requestId,
      type: 'error',
      payload: { code, message },
    }
  }

  private dispatch(message: WorkerMessage) {
    ;[...this.handlers].forEach((handler) => {
      try { handler(message) } catch (error) { console.error('PixelFlow Worker message handler failed', error) }
    })
  }
}
