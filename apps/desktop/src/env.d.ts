/// <reference types="vite/client" />

interface Window {
  __PIXELFLOW_WORKER__?: {
    send(line: string): void
    subscribe(listener: (line: string) => void): () => void
  }
}
