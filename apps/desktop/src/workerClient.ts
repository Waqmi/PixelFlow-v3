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

export function windowWorkerBridge(): WorkerBridge | undefined {
  return window.__PIXELFLOW_WORKER__
}

export class WorkerClient {
  readonly connected: boolean
  private readonly unsubscribe: () => void
  private readonly handlers = new Set<MessageHandler>()
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
    this.bridge.send(JSON.stringify(createRequest(requestId, type, payload)))
    return requestId
  }

  requestResult<TPayload extends object, TResult>(
    type: WorkerRequestType,
    payload: TPayload,
    timeoutMs = this.requestTimeoutMs,
  ): Promise<TResult> {
    const requestId = `${this.requestPrefix}-${this.nextRequest++}`
    return new Promise((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | undefined
      const stop = this.onMessage((message) => {
        if (message.request_id !== requestId) return
        stop()
        if (timer) clearTimeout(timer)
        if (message.type === 'done') resolve(message.payload as TResult)
        else reject(new Error(String((message.payload as { message?: string }).message || 'Worker request failed')))
      })
      if (timeoutMs > 0) {
        timer = setTimeout(() => {
          stop()
          reject(new Error(`Worker request timed out after ${timeoutMs}ms`))
        }, timeoutMs)
      }
      try {
        this.bridge.send(JSON.stringify(createRequest(requestId, type, payload)))
      } catch (error) {
        stop()
        if (timer) clearTimeout(timer)
        reject(error instanceof Error ? error : new Error(String(error)))
      }
    })
  }

  dispose() {
    this.unsubscribe()
    this.handlers.clear()
  }

  private receive(line: string) {
    const message = parseWorkerMessage(line)
    if (!message || message.protocol !== WORKER_PROTOCOL || !isWorkerEventType(message.type)) return
    this.handlers.forEach((handler) => handler(message))
  }
}
