export const WORKER_PROTOCOL = 1 as const

export type WorkerRequestType =
  | 'ping'
  | 'probe_models'
  | 'classify_materials'
  | 'start_generation'
  | 'cancel_generation'
  | 'retry_failed'
  | 'clear_cache'
  | 'shutdown'

export type WorkerEventType =
  | 'ready'
  | 'status'
  | 'progress'
  | 'preview_ready'
  | 'done'
  | 'cancelled'
  | 'error'

export interface WorkerMessage<T = Record<string, unknown>> {
  protocol: typeof WORKER_PROTOCOL
  request_id: string
  type: WorkerRequestType | WorkerEventType
  payload: T
}

export interface GenerationPayload {
  product_root: string
  output_root: string
  selected_sizes: string[]
  include_logo?: boolean
  include_vip?: boolean
  include_model_images?: boolean
  max_size_mb?: number
  logo_overrides?: Record<string, string>
  folder_category_overrides?: Record<string, string>
  include_selling_point_images?: boolean
  selling_points?: string[]
  material_confirmation_completed?: boolean
  source_type_overrides?: Record<string, string>
}

export interface ClassificationPayload {
  product_root: string
  source_filter?: string[]
  overrides?: Record<string, MaterialType>
  workers?: number
}

export type MaterialType = 'main_product' | 'detail' | 'model' | 'model_detail'

export function createRequest<T extends object>(
  requestId: string,
  type: WorkerRequestType,
  payload: T,
): WorkerMessage<T> {
  return { protocol: WORKER_PROTOCOL, request_id: requestId, type, payload }
}
