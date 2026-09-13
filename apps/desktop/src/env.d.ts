/// <reference types="vite/client" />

interface Window {
  __TAURI_INTERNALS__?: unknown
  __PIXELFLOW_WORKER__?: {
    send(line: string): void
    subscribe(listener: (line: string) => void): () => void
  }
}
