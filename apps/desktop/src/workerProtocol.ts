export const WORKER_PROTOCOL = 1 as const

export type WorkerRequestType =
  | 'ping'
  | 'probe_models'
  | 'classify_materials'
  | 'start_generation'
  | 'cancel_generation'
  | 'retry_failed'
  | 'clear_cache'
  | 'read_preview'
  | 'shutdown'

export type WorkerEventType =
  | 'ready'
  | 'status'
  | 'progress'
  | 'preview_ready'
  | 'done'
  | 'cancelled'
  | 'error'

export const WORKER_REQUEST_TYPES = [
  'ping',
  'probe_models',
  'classify_materials',
  'start_generation',
  'cancel_generation',
  'retry_failed',
  'clear_cache',
  'read_preview',
  'shutdown',
] as const

export const WORKER_EVENT_TYPES = [
  'ready',
  'status',
  'progress',
  'preview_ready',
  'done',
  'cancelled',
  'error',
] as const

export interface WorkerMessage<T = Record<string, unknown>> {
  protocol: typeof WORKER_PROTOCOL
  request_id: string
  type: WorkerRequestType | WorkerEventType
  payload: T
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isWorkerEventType(value: unknown): value is WorkerEventType {
  return typeof value === 'string' && (WORKER_EVENT_TYPES as readonly string[]).includes(value)
}

export function isWorkerMessage(value: unknown): value is WorkerMessage {
  if (!isRecord(value) || value.protocol !== WORKER_PROTOCOL) return false
  if (typeof value.request_id !== 'string' || value.request_id.length === 0) return false
  if (!isRecord(value.payload) || typeof value.type !== 'string') return false
  return (
    (WORKER_REQUEST_TYPES as readonly string[]).includes(value.type) ||
    isWorkerEventType(value.type)
  )
}

/** Parse one JSONL message and reject lines outside packages/protocol/schema.json. */
export function parseWorkerMessage(line: string): WorkerMessage | undefined {
  try {
    const value: unknown = JSON.parse(line)
    return isWorkerMessage(value) ? value : undefined
  } catch {
    return undefined
  }
}

export interface WorkerReadyPayload {
  worker?: string
  protocol?: number
  worker_version?: string
  rules_version?: string
  build_hash?: string
}

export interface GenerationPayload {
  product_root: string
  output_root: string
  selected_sizes: string[]
  include_logo?: boolean
  include_vip?: boolean
  include_model_images?: boolean
  max_size_mb?: number
  naming_mode?: string
  logo_overrides?: Record<string, string>
  folder_category_overrides?: Record<string, string>
  include_selling_point_images?: boolean
  selling_points?: string[]
  enable_material_understanding?: boolean
  category_template?: string
  category_override?: string
  enable_category_template?: boolean
  local_model_enabled?: boolean
  local_model_path?: string
  material_confirmation_completed?: boolean
  source_type_overrides?: Record<string, string>
  source_scale_adjustments?: Record<string, number>
  excluded_model_sources?: string[]
  force_rerender?: boolean
}

export interface ClassificationPayload {
  product_root: string
  source_filter?: string[]
  overrides?: Record<string, MaterialType>
  workers?: number
  category?: string
  local_model_enabled?: boolean
  local_model_path?: string
}

export type MaterialType = 'main_product' | 'detail' | 'model' | 'model_detail'

export function createRequest<T extends object>(
  requestId: string,
  type: WorkerRequestType,
  payload: T,
): WorkerMessage<T> {
  return { protocol: WORKER_PROTOCOL, request_id: requestId, type, payload }
}
