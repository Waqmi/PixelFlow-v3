import {
  createRequest,
  WORKER_PROTOCOL,
  type WorkerEventType,
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

  constructor(private readonly bridge: WorkerBridge, onMessage?: MessageHandler) {
    this.connected = true
    if (onMessage) this.handlers.add(onMessage)
    this.unsubscribe = bridge.subscribe((line) => this.receive(line))
  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.add(handler)
    return () => this.handlers.delete(handler)
  }

  request<T extends object>(type: WorkerRequestType, payload: T): string {
    const requestId = `ui-${this.nextRequest++}`
    this.bridge.send(JSON.stringify(createRequest(requestId, type, payload)))
    return requestId
  }

  dispose() {
    this.unsubscribe()
    this.handlers.clear()
  }

  private receive(line: string) {
    try {
      const message = JSON.parse(line) as WorkerMessage
      if (message.protocol !== WORKER_PROTOCOL || !this.isEvent(message.type)) return
      this.handlers.forEach((handler) => handler(message))
    } catch {
      // A malformed line must not break the UI event loop.
    }
  }

  private isEvent(type: WorkerMessage['type']): type is WorkerEventType {
    return ['ready', 'status', 'progress', 'preview_ready', 'done', 'cancelled', 'error'].includes(type)
  }
}
