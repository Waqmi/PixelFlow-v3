<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch, type ComponentPublicInstance } from 'vue'
import { invoke } from '@tauri-apps/api/core'
import { emit, emitTo, listen } from '@tauri-apps/api/event'
import { getCurrentWindow, PhysicalPosition } from '@tauri-apps/api/window'
import { WebviewWindow } from '@tauri-apps/api/webviewWindow'
import appIconUrl from '../src-tauri/icons/icon.png'
import logoSquareDarkUrl from './assets/logo-square-dark.png'
import logoSquareLightUrl from './assets/logo-square-light.png'
import logoTallDarkUrl from './assets/logo-tall-dark.png'
import logoTallLightUrl from './assets/logo-tall-light.png'
import settingsIconUrl from './assets/settings.svg'
import categoryArrowUrl from './assets/category-arrow.svg'
import folderIconUrl from './assets/folder.svg'
import { windowWorkerBridge, WorkerClient } from './workerClient'
import type { WorkerMessage } from './workerProtocol'
import { useTaskProgress } from './composables/useTaskProgress'
import SettingsAboutView from './components/SettingsAboutView.vue'

type Theme = 'system' | 'light' | 'dark'
type MaterialType = 'main_product' | 'detail' | 'model' | 'model_detail'

interface MaterialDecision {
  source: string
  source_type: MaterialType
  confidence: number
  reason: string
  metadata?: Record<string, unknown>
  previewUrl?: string
  previewFailed?: boolean
  previewLoading?: boolean
  previewReady?: boolean
  isVisible?: boolean
}

interface SettingsSnapshot {
  theme: Theme
  reducedMotion: boolean
  classificationConcurrency: string
  includeLogo: boolean
  includeVip: boolean
  enableMaterialUnderstanding: boolean
  includeModelImages: boolean
  localModelEnabled: boolean
  localModelPath: string
  showLogoOption: boolean
  showVipOption: boolean
  showMaterialOption: boolean
  outputNamingMode: string
  maxSizeMb: string
  logoOverrides: Record<string, string>
}

interface ReportSnapshot {
  result: Record<string, unknown>
  outputRoot: string
  resume?: ResumeSnapshot | null
}

type ResumePhase = 'classifying' | 'generating' | 'cancelled' | 'interrupted' | 'failed'

interface ResumeSnapshot {
  schema_version: 1
  phase: ResumePhase
  request_kind: 'classification' | 'generation'
  saved_at: string
  input_root: string
  output_root: string
  generation_payload?: Record<string, unknown>
  classification_payload?: Record<string, unknown>
  progress?: { completed: number; total: number }
  task_phase?: string
  task_detail?: string
  task_id?: string
  result_summary?: Record<string, unknown>
}

interface ConfirmSnapshot {
  inputRoot: string
  materials: MaterialDecision[]
  excludedModelSources: string[]
  scaleAdjustments: Record<string, number>
  categorySuggestion?: { category: string; source: string; matched_keyword?: string }
  preflightWarnings: string[]
}

interface ModelDiagnostic {
  state?: string
  status?: string
  error?: string
  fallback?: boolean
  model?: string
  model_version?: { path?: string; sha256?: string; size?: number; mtime_ns?: number }
}

function readInitialTheme(): Theme {
  try {
    const storedTheme = localStorage.getItem('pixelflow.theme')
    if (storedTheme === 'system' || storedTheme === 'light' || storedTheme === 'dark') return storedTheme
    const savedSettings = JSON.parse(localStorage.getItem('pixelflow.output-settings') || 'null') as { theme?: unknown } | null
    if (savedSettings?.theme === 'system' || savedSettings?.theme === 'light' || savedSettings?.theme === 'dark') {
      return savedSettings.theme
    }
  } catch {
    // Storage is optional in embedded webviews.
  }
  return 'system'
}

function readInitialSystemDark(): boolean {
  try { return window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false } catch { return false }
}

const initialTheme = readInitialTheme()
const initialSystemDark = readInitialSystemDark()
const initialDark = initialTheme === 'dark' || (initialTheme === 'system' && initialSystemDark)
if (initialDark && typeof document !== 'undefined') {
  // Apply the persisted appearance before Vue mounts. The settings window is
  // created as a new WebView, so its first paint otherwise uses the light
  // index.html background for a frame before `.theme-dark` is rendered.
  const initialBackground = '#1b1f24'
  document.documentElement.style.backgroundColor = initialBackground
  document.body?.style.setProperty('background-color', initialBackground)
  document.getElementById('app')?.style.setProperty('background-color', initialBackground)
}

const theme = ref<Theme>(initialTheme)
const systemDark = ref(initialSystemDark)
const reducedMotion = ref(false)
const { progress, setProgress } = useTaskProgress(reducedMotion)
const { progress: classificationProgress, setProgress: setClassificationProgress } = useTaskProgress(reducedMotion)
const progressComplete = ref(false)
let progressCompleteTimer: number | undefined

function clearProgressComplete() {
  progressComplete.value = false
  if (progressCompleteTimer) {
    window.clearTimeout(progressCompleteTimer)
    progressCompleteTimer = undefined
  }
}

function markProgressComplete() {
  if (progressCompleteTimer) window.clearTimeout(progressCompleteTimer)
  progressComplete.value = true
  progressCompleteTimer = window.setTimeout(() => {
    progressComplete.value = false
    progressCompleteTimer = undefined
  }, 700)
}

const classificationConcurrency = ref('auto')
const inputRoot = ref('')
const outputRoot = ref('')
const selectedSizes = ref(['1440x1440', '1440x1920', '1125x1500'])
const includeLogo = ref(true)
const includeVip = ref(true)
const enableMaterialUnderstanding = ref(false)
const includeModelImages = ref(true)
const localModelEnabled = ref(false)
const localModelPath = ref('')
const logoOverrides = ref<Record<string, string>>({})
const logoPreviews = ref<Record<string, string>>({})
const showLogoOption = ref(true)
const showVipOption = ref(true)
const showMaterialOption = ref(true)
const outputNamingMode = ref('short')
const modelStatus = ref('状态：当前使用规则判断；Seg 不可用（疑难图继续使用规则 + Pose）；Detection 未检测')
const modelTesting = ref(false)
const settingsNotice = ref('')
const categoryTemplate = ref('auto')
const categoryOpen = ref(false)
const categoryLabels: Record<string, string> = {
  none: '不启用品类模板（仍自动识别）', auto: '自动识别（规则）', shirt: '上衣', long_pants: '长裤',
  shorts: '短裤', shoes: '鞋', hat: '帽子', socks: '袜子', gloves: '手套', bag: '包', knee_pads: '护膝', other: '其他',
}
const categoryLabel = computed(() => categoryLabels[categoryTemplate.value] || categoryLabels.auto)
const categoryKeys = Object.keys(categoryLabels)
const categoryHighlight = ref(categoryTemplate.value)
const categoryTriggerElement = ref<HTMLButtonElement | null>(null)
const namingOpen = ref(false)
const namingOptions = { short: '简短模式（推荐）', original: '保留原始名称' }
const namingLabel = computed(() => namingOptions[outputNamingMode.value as keyof typeof namingOptions] || namingOptions.short)
const maxSizeMb = ref('')
const viewParam = new URLSearchParams(window.location.search).get('view')
const currentWindowLabel = (() => {
  if (!window.__TAURI_INTERNALS__) return ''
  try { return getCurrentWindow().label }
  catch { return '' }
})()
const isSettingsWindow = viewParam === 'settings' || currentWindowLabel === 'settings'
const isReportWindow = viewParam === 'report' || currentWindowLabel === 'report'
const isConfirmWindow = viewParam === 'confirm' || currentWindowLabel === 'confirm'
// ponytail: a short localStorage lease is enough for the two same-app WebViews; move this to the Worker supervisor if windows become independent clients.
const modelProbeLockKey = 'pixelflow.model-probe-lock'
const modelProbeLockTtlMs = 30_000
const modelProbeOwner = `${isSettingsWindow ? 'settings' : 'main'}-${Math.random().toString(36).slice(2)}`
const settingsOpen = ref(isSettingsWindow)
const settingsTab = ref<'brand' | 'general' | 'output' | 'model' | 'about'>('brand')
const settingsTabs: { key: 'brand' | 'general' | 'output' | 'model' | 'about'; label: string }[] = [
  { key: 'brand', label: '品牌设置' }, { key: 'general', label: '通用设置' }, { key: 'output', label: '输出选项' },
  { key: 'model', label: '模特图增强' }, { key: 'about', label: '关于' },
]
const logoRows = [
  { title: '方图 Logo', size: '1440 × 1440', variants: [
    { key: 'logo_square_dark', label: '浅色背景 · 深色 Logo', filename: '新logo 1440.png', src: logoSquareDarkUrl, tall: false },
    { key: 'logo_square_light', label: '深色背景 · 浅色 Logo', filename: '新logo 1440 2.png', src: logoSquareLightUrl, tall: false },
  ] },
  { title: '竖图 Logo', size: '1440 × 1920', variants: [
    { key: 'logo_tall_dark', label: '浅色背景 · 深色 Logo', filename: '新logo 1920.png', src: logoTallDarkUrl, tall: true },
    { key: 'logo_tall_light', label: '深色背景 · 浅色 Logo', filename: '新logo 1920 2.png', src: logoTallLightUrl, tall: true },
  ] },
]
const confirmOpen = ref(isConfirmWindow)
const reportOpen = ref(isReportWindow)
const failureOpen = ref(false)
const reviewOpen = ref(false)
const reportNotice = ref('')
const failureNotice = ref('')
const lastResult = ref<Record<string, unknown>>({})
const isRunning = ref(false)
const isCancelling = ref(false)
const workerConnected = ref(false)
const workerInterrupted = ref(false)
const resumeSnapshot = ref<ResumeSnapshot | null>(null)
const resumeAvailable = computed(() => Boolean(resumeSnapshot.value
  && ['classifying', 'generating', 'cancelled', 'interrupted', 'failed'].includes(resumeSnapshot.value.phase)))
const status = ref('准备就绪')
const taskPhase = ref('准备就绪')
const taskDetail = ref('准备就绪')
const materials = ref<MaterialDecision[]>([])
const excludedModelSources = ref<string[]>([])
const scaleAdjustments = ref<Record<string, number>>({})
const materialConfirmed = ref(false)
const classifiedInputRoot = ref('')
const classifiedOptionsKey = ref('')
let pendingClassificationKey = ''
const categorySuggestion = ref<{ category: string; source: string; matched_keyword?: string }>()
const preflightWarnings = ref<string[]>([])
const classificationRunning = ref(false)
const generationRequestId = ref<string>()
const classificationRequestId = ref<string>()
const modelProbeRequestId = ref<string>()
const cancelRequestId = ref<string>()
const cancelTargetRequestId = ref<string>()
let worker: WorkerClient | undefined
let classificationWatchdog: ReturnType<typeof window.setTimeout> | undefined
let cancelWatchdog: ReturnType<typeof window.setTimeout> | undefined
let settingsSnapshot: SettingsSnapshot | undefined
let modelProbeRoot = ''
let modelProbeRetryTimer: number | undefined
let modelProbeReadyHandled = false
let appliedConfirmSnapshotKey = ''
let systemMedia: MediaQueryList | undefined
let stopNativeThemeListener: (() => void) | undefined
let stopSettingsListener: (() => void) | undefined
let stopSettingsChangedListener: (() => void) | undefined
let stopReportReadyListener: (() => void) | undefined
let stopReportDataListener: (() => void) | undefined
let stopReportActionListener: (() => void) | undefined
let stopConfirmReadyListener: (() => void) | undefined
let stopConfirmDataListener: (() => void) | undefined
let stopConfirmResultListener: (() => void) | undefined
let stopDropListener: (() => void) | undefined
const previewUrls = new Set<string>()
const previewLoadedSources = new Set<string>()
const previewFailedSources = new Set<string>()
const previewQueue: MaterialDecision[] = []
const previewQueuedSources = new Set<string>()
let activePreviewLoads = 0
let previewSession = 0
const confirmGridElement = ref<HTMLElement | null>(null)
const previewElements = new Map<string, HTMLElement>()
let previewObserver: IntersectionObserver | undefined
let previewBackgroundTimer = 0
let previewBackgroundIndex = 0
let previewScrollTimer = 0
let previewScrolling = false
let previewScrollRoot: HTMLElement | undefined
const cardObserver = ref<IntersectionObserver | null>(null)
const isConfirmGridScrolledToBottom = ref(false)
const confirmGridHasOverflow = ref(false)

const darkMode = computed(() => theme.value === 'dark' || (theme.value === 'system' && systemDark.value))
const appVersion = '3.0.0'
const expectedWorkerVersion = appVersion
const expectedRulesVersion = 'v3-composition-11'
const workerVersion = ref(expectedWorkerVersion)
const workerBuildHash = ref('')
const workerRulesVersion = ref(expectedRulesVersion)
const modelDiagnostics = ref<Record<string, ModelDiagnostic>>({})
const workerReadyState = ref('未连接')
const expectedModels = {
  pose: { label: 'Pose', filename: 'pose/yolo11n-pose.onnx', sha256: '86e1275fed40d3fce62acb80eae74c9e190e62c7e75da982de81787c4b3ef6f8' },
  segmentation: { label: 'Seg', filename: 'segmentation/yolo11n-seg.onnx', sha256: '52269a89f1e8d258d3e170f60a30b7bcb417f44b892d23f105f37759e582db55' },
  detection: { label: 'Detection', filename: 'detection/yolov8m-worldv2-pixelflow.onnx', sha256: '467b89a95b8f9f99d81483e4d768fde008690600649e817964655ee590cff942' },
} as const
const requiredModelKeys = ['pose', 'segmentation'] as const
const modelFallbackWarning = computed(() => {
  if (!localModelEnabled.value || !localModelPath.value.trim() || modelTesting.value) return false
  return requiredModelKeys.some((key) => modelDiagnostics.value[key]?.state !== '已就绪')
})
const modelDiagnosticRows = computed(() => (['pose', 'segmentation', 'detection'] as const).map((key) => ({ key, ...expectedModels[key], diagnostic: modelDiagnostics.value[key] })))
const modelConfirmFallbackWarning = computed(() => localModelEnabled.value && materials.value.some((material) => material.metadata?.fallback === true || material.metadata?.model_status === 'fallback'))
const classificationModelStatus = computed(() => {
  if (!localModelEnabled.value || !localModelPath.value.trim()) return 'Pose：未启用；Seg：未启用'
  const statusCount = (key: 'pose_evidence' | 'segmentation_evidence') => {
    const counts = { ready: 0, fallback: 0, not_run: 0 }
    materials.value.forEach((material) => {
      const metadata = material.metadata as Record<string, unknown> | undefined
      const evidence = metadata?.[key]
      const status = String(evidence && typeof evidence === 'object' && 'status' in evidence
        ? (evidence as { status?: unknown }).status || 'not_run'
        : 'not_run')
      if (status === 'ready') counts.ready += 1
      else if (status === 'fallback') counts.fallback += 1
      else counts.not_run += 1
    })
    return counts
  }
  const pose = statusCount('pose_evidence')
  const seg = statusCount('segmentation_evidence')
  const label = (value: typeof pose) => value.fallback ? `回退 ${value.fallback}` : value.ready ? `完成 ${value.ready}` : '未运行'
  return `Pose：${label(pose)}；Seg：${label(seg)}`
})
const previewConcurrencyLimit = computed(() => classificationConcurrency.value === 'conservative' ? 2 : 4)
const mainWindowTitle = 'PixelFlow'
const canStart = computed(() => Boolean(inputRoot.value.trim() && outputRoot.value.trim() && selectedSizes.value.length && !isRunning.value && !classificationRunning.value))
const displayProgress = computed(() => classificationRunning.value ? classificationProgress.value : progress.value)
const outputSummary = computed(() => {
  const validation = lastResult.value.validation as { passed?: unknown } | undefined
  const legacyCount = ['main_images', 'model_images', 'transparent_images', 'vip_images', 'selling_point_images']
    .reduce((sum, key) => sum + Number(lastResult.value[key] || 0), 0)
  return {
    success: Number.isFinite(Number(validation?.passed)) ? Number(validation?.passed) : legacyCount,
    skipped: Number(lastResult.value.skipped_model_sources || 0),
    failures: Array.isArray(lastResult.value.failures) ? lastResult.value.failures.length : 0,
    anomalies: Number((lastResult.value.audit as Record<string, unknown> | undefined)?.anomaly_count || 0),
  }
})
const resultCancelled = computed(() => Boolean(lastResult.value.cancelled))
const resultWorkers = computed(() => Number(lastResult.value.render_workers || 1))
const resultSegUsed = computed(() => Number(lastResult.value.segmentation_used || 0))
const resultSegRatio = computed(() => Number(lastResult.value.segmentation_ratio || 0))
const resultWarnings = computed(() => {
  const validation = lastResult.value.validation as { warnings?: unknown[] } | undefined
  return validation?.warnings?.length || 0
})
const resultShortcuts = computed(() => [
  { label: '主图', path: outputRoot.value, visible: outputSummary.value.success > 0 },
  { label: '模特图', path: `${outputRoot.value}/模特图`, visible: Number(lastResult.value.model_images || 0) > 0 },
  { label: '透明图', path: `${outputRoot.value}/透明产品图`, visible: Number(lastResult.value.transparent_images || 0) > 0 },
  { label: '唯品图', path: `${outputRoot.value}/唯品专享1440`, visible: Number(lastResult.value.vip_images || 0) > 0 },
].filter((item) => item.visible))
const failures = computed(() => Array.isArray(lastResult.value.failures) ? lastResult.value.failures as Record<string, unknown>[] : [])
const reviewItems = computed(() => Array.isArray(lastResult.value.requires_review) ? lastResult.value.requires_review as Record<string, unknown>[] : [])
const fallbackItems = computed(() => Array.isArray(lastResult.value.fallback_items) ? lastResult.value.fallback_items as Record<string, unknown>[] : [])
const skippedOutputs = computed(() => Array.isArray(lastResult.value.skipped_outputs) ? lastResult.value.skipped_outputs as { source: string, sizes: string[], reason: string }[] : [])
const acceptedReviewSources = computed(() => Array.isArray(lastResult.value.accepted_review_sources) ? lastResult.value.accepted_review_sources as string[] : [])
const materialTypeLabels: Record<MaterialType, string> = {
  main_product: '商品白底图', detail: '细节图', model_detail: '模特细节图', model: '模特图',
}
const materialCounts = computed(() => materials.value.reduce((counts, material) => {
  counts[material.source_type] = (counts[material.source_type] || 0) + 1
  return counts
}, {} as Record<string, number>))
const skippedMaterialCount = computed(() => materials.value.filter((material) => ['model', 'model_detail'].includes(material.source_type) && excludedModelSources.value.includes(material.source)).length)
const processMaterialCount = computed(() => materials.value.length - skippedMaterialCount.value)
const categoryHint = computed(() => {
  const suggestion = categorySuggestion.value
  if (!suggestion) return '正在识别品类，请核对后确认。'
  const label = categoryLabels[suggestion.category] || '其他'
  if (suggestion.source === 'manual') return `已选品类：${label}`
  if (suggestion.source === 'conflict') return '检测到多个品类关键词，无法可靠自动选择；确认即采用“其他”，需修改请取消并在首页选择'
  if (suggestion.source === 'fallback') return '未识别到可靠品类；确认即采用“其他”，需修改请取消并在首页选择'
  return `自动识别建议：${label}（确认即采用；需修改请取消并在首页选择）`
})

function currentSettings(): SettingsSnapshot {
  return {
    theme: theme.value, reducedMotion: reducedMotion.value,
    classificationConcurrency: classificationConcurrency.value,
    includeLogo: includeLogo.value, includeVip: includeVip.value,
    enableMaterialUnderstanding: enableMaterialUnderstanding.value,
    includeModelImages: includeModelImages.value, localModelEnabled: localModelEnabled.value,
    localModelPath: localModelPath.value, showLogoOption: showLogoOption.value,
  showVipOption: showVipOption.value, showMaterialOption: showMaterialOption.value,
    outputNamingMode: outputNamingMode.value,
    maxSizeMb: maxSizeMb.value, logoOverrides: { ...logoOverrides.value },
  }
}

const RESUME_STORAGE_KEY = 'pixelflow.resume-task'

function compactResumePayload(payload: Record<string, unknown>): Record<string, unknown> {
  const compact = { ...payload }
  const serialized = JSON.stringify(compact)
  if (serialized.length <= 4_500_000) return compact
  // Keep the run configuration and scale choices while allowing a very large
  // batch to resume after localStorage quota limits. The worker reclassifies
  // sources when the optional per-source metadata is omitted.
  delete compact.source_metadata_overrides
  delete compact.source_type_overrides
  compact.enable_material_understanding = true
  return compact
}

function saveResumeTask(snapshot: ResumeSnapshot) {
  resumeSnapshot.value = snapshot
  try {
    localStorage.setItem(RESUME_STORAGE_KEY, JSON.stringify(snapshot))
  } catch {
    const compact = snapshot.generation_payload
      ? { ...snapshot, generation_payload: compactResumePayload(snapshot.generation_payload) }
      : snapshot
    try { localStorage.setItem(RESUME_STORAGE_KEY, JSON.stringify(compact)) } catch { /* memory state remains usable */ }
  }
}

function updateResumePhase(phase: ResumePhase, result?: Record<string, unknown>) {
  const current = resumeSnapshot.value
  if (!current) return
  saveResumeTask({
    ...current,
    phase,
    saved_at: new Date().toISOString(),
    task_phase: taskPhase.value,
    task_detail: taskDetail.value,
    result_summary: result ? {
      cancelled: Boolean(result.cancelled),
      outputs: Array.isArray(result.outputs) ? result.outputs.length : undefined,
      failures: Array.isArray(result.failures) ? result.failures.length : undefined,
      performance: result.performance,
    } : current.result_summary,
  })
}

function clearResumeTask() {
  resumeSnapshot.value = null
  try { localStorage.removeItem(RESUME_STORAGE_KEY) } catch { /* optional storage */ }
}

function readResumeTask(): ResumeSnapshot | null {
  try {
    const value = JSON.parse(localStorage.getItem(RESUME_STORAGE_KEY) || 'null') as ResumeSnapshot | null
    return value?.schema_version === 1 && value.input_root && value.output_root ? value : null
  } catch {
    return null
  }
}

function diagnosticsSnapshot() {
  return {
    generated_at: new Date().toISOString(),
    app_version: appVersion,
    user_agent: navigator.userAgent,
    platform: navigator.platform,
    worker_version: workerVersion.value,
    worker_build_hash: workerBuildHash.value || null,
    rules_version: workerRulesVersion.value,
    worker_status: workerReadyState.value,
    model_root: localModelPath.value || null,
    models: modelDiagnostics.value,
    input_root: inputRoot.value || null,
    output_root: outputRoot.value || null,
    task_state: {
      status: status.value,
      phase: taskPhase.value,
      detail: taskDetail.value,
      running: isRunning.value || classificationRunning.value,
      cancelling: isCancelling.value,
      resume_available: resumeAvailable.value,
    },
    last_result_summary: {
      success: outputSummary.value.success,
      failures: outputSummary.value.failures,
      skipped: outputSummary.value.skipped,
      performance: lastResult.value.performance || null,
    },
  }
}

function applySettings(value: Partial<SettingsSnapshot>) {
  if (value.theme === 'system' || value.theme === 'light' || value.theme === 'dark') theme.value = value.theme
  if (typeof value.reducedMotion === 'boolean') reducedMotion.value = value.reducedMotion
  if (typeof value.classificationConcurrency === 'string') classificationConcurrency.value = value.classificationConcurrency
  if (typeof value.includeLogo === 'boolean') includeLogo.value = value.includeLogo
  if (typeof value.includeVip === 'boolean') includeVip.value = value.includeVip
  if (typeof value.enableMaterialUnderstanding === 'boolean') enableMaterialUnderstanding.value = value.enableMaterialUnderstanding
  if (typeof value.includeModelImages === 'boolean') includeModelImages.value = value.includeModelImages
  if (typeof value.localModelEnabled === 'boolean') localModelEnabled.value = value.localModelEnabled
  if (typeof value.localModelPath === 'string') localModelPath.value = value.localModelPath
  if (typeof value.showLogoOption === 'boolean') showLogoOption.value = value.showLogoOption
  if (typeof value.showVipOption === 'boolean') showVipOption.value = value.showVipOption
  if (typeof value.showMaterialOption === 'boolean') showMaterialOption.value = value.showMaterialOption
  if (value.outputNamingMode === 'short' || value.outputNamingMode === 'original') outputNamingMode.value = value.outputNamingMode
  if (typeof value.maxSizeMb === 'string') maxSizeMb.value = value.maxSizeMb
  if (value.logoOverrides && typeof value.logoOverrides === 'object') logoOverrides.value = { ...value.logoOverrides }
}
async function openSettings() {
  if (isSettingsWindow) return
  if (!window.__TAURI_INTERNALS__) {
    settingsSnapshot = currentSettings()
    settingsTab.value = 'brand'
    settingsOpen.value = true
    return
  }
  const existing = await WebviewWindow.getByLabel('settings')
  if (existing) {
    const visible = await existing.isVisible().catch(() => true)
    if (visible) {
      await existing.show()
      await existing.setFocus()
      return
    }
    await existing.close().catch(() => {})
  }
  const settingsWindow = new WebviewWindow('settings', {
    url: '/?view=settings',
    title: 'PixelFlow 设置',
    width: 860,
    height: 708,
    minWidth: 820,
    minHeight: 620,
    resizable: true,
    center: true,
    decorations: true,
    shadow: true,
    theme: darkMode.value ? 'dark' : 'light',
    titleBarStyle: 'visible',
    backgroundColor: darkMode.value ? '#1b1f24' : '#f5f7fa',
  })
  settingsWindow.once('tauri://error', () => { status.value = '设置窗口打开失败' })
}
async function closeSettings(save = false) {
  if (!save && settingsSnapshot) {
    applySettings(settingsSnapshot)
    try { localStorage.setItem('pixelflow.output-settings', JSON.stringify(settingsSnapshot)) } catch { /* optional */ }
    if (isSettingsWindow && window.__TAURI_INTERNALS__) await emit('settings-changed', settingsSnapshot)
  }
  settingsSnapshot = undefined
  if (isSettingsWindow && window.__TAURI_INTERNALS__) {
    await WebviewWindow.getCurrent().close()
    return
  }
  settingsOpen.value = false
}

function reportSnapshot(): ReportSnapshot {
  return { result: { ...lastResult.value }, outputRoot: outputRoot.value, resume: resumeSnapshot.value }
}

function applyReportSnapshot(snapshot: ReportSnapshot) {
  lastResult.value = snapshot.result || {}
  outputRoot.value = snapshot.outputRoot || ''
  resumeSnapshot.value = snapshot.resume || null
  reportOpen.value = true
}

function cacheReportSnapshot() {
  try { localStorage.setItem('pixelflow.report-data', JSON.stringify(reportSnapshot())) } catch { /* optional */ }
}

async function openReportWindow() {
  if (isReportWindow) { reportOpen.value = true; return }
  if (!window.__TAURI_INTERNALS__) { reportOpen.value = true; return }
  cacheReportSnapshot()
  const existing = await WebviewWindow.getByLabel('report')
  if (existing) {
    await existing.show()
    await existing.setFocus()
    void emit('report-data', reportSnapshot())
    return
  }
  const reportWindow = new WebviewWindow('report', {
    url: '/?view=report',
    title: '输出结果',
    width: 660,
    height: 580,
    minWidth: 660,
    minHeight: 580,
    resizable: false,
    center: true,
    decorations: true,
    shadow: true,
    theme: darkMode.value ? 'dark' : 'light',
    titleBarStyle: 'visible',
    backgroundColor: darkMode.value ? '#1b1f24' : '#f5f7fa',
  })
  reportWindow.once('tauri://error', () => { reportNotice.value = '报告窗口打开失败' })
}

async function closeReport() {
  if (isReportWindow && window.__TAURI_INTERNALS__) {
    await WebviewWindow.getCurrent().close()
    return
  }
  reportOpen.value = false
}

function showReport() {
  if (window.__TAURI_INTERNALS__ && !isReportWindow) void openReportWindow()
  else reportOpen.value = true
}

function confirmSnapshot(): ConfirmSnapshot {
  return {
    inputRoot: inputRoot.value,
    materials: materials.value.map(({ previewUrl: _previewUrl, previewFailed: _previewFailed, previewLoading: _previewLoading, previewReady: _previewReady, isVisible: _isVisible, ...material }) => material),
    excludedModelSources: [...excludedModelSources.value],
    scaleAdjustments: { ...scaleAdjustments.value },
    categorySuggestion: categorySuggestion.value,
    preflightWarnings: [...preflightWarnings.value],
  }
}

function applyConfirmSnapshot(snapshot: ConfirmSnapshot) {
  const snapshotKey = JSON.stringify({
    materials: snapshot.materials,
    excludedModelSources: snapshot.excludedModelSources || [],
    scaleAdjustments: snapshot.scaleAdjustments || {},
    categorySuggestion: snapshot.categorySuggestion,
    preflightWarnings: snapshot.preflightWarnings || [],
  })
  if (isConfirmWindow && appliedConfirmSnapshotKey === snapshotKey) {
    confirmOpen.value = true
    return
  }
  if (isConfirmWindow) appliedConfirmSnapshotKey = snapshotKey
  inputRoot.value = snapshot.inputRoot || inputRoot.value
  classifiedInputRoot.value = snapshot.inputRoot || classifiedInputRoot.value
  const previousMaterials = new Map(materials.value.map((material) => [material.source, material]))
  materials.value = Array.isArray(snapshot.materials)
    ? snapshot.materials.map((material, index) => {
      const previous = previousMaterials.get(material.source)
      return previous
        ? {
          ...material,
          previewUrl: previous.previewUrl,
          previewFailed: previous.previewFailed,
          previewLoading: previous.previewLoading,
          previewReady: previous.previewReady,
          isVisible: index < 20 && previous.isVisible === true,
        }
        : { ...material, isVisible: false }
    })
    : []
  excludedModelSources.value = Array.isArray(snapshot.excludedModelSources)
    ? snapshot.excludedModelSources
    : []
  scaleAdjustments.value = snapshot.scaleAdjustments || {}
  categorySuggestion.value = snapshot.categorySuggestion
  preflightWarnings.value = snapshot.preflightWarnings || []
  materialConfirmed.value = false
  confirmOpen.value = true
  if (isConfirmWindow) {
    void nextTick(() => {
      refreshMaterialPreviews()
      startPreviewObserver()
      setupCardObserver()
    })
  }
}

async function centerWindowOnMain(window: WebviewWindow, width: number, height: number) {
  try {
    const main = getCurrentWindow()
    const [mainPosition, mainSize] = await Promise.all([main.outerPosition(), main.outerSize()])
    const childSize = await window.outerSize().catch(() => ({ width, height }))
    const childWidth = childSize.width || width
    const childHeight = childSize.height || height
    await window.setPosition(new PhysicalPosition(
      Math.round(mainPosition.x + (mainSize.width - childWidth) / 2),
      Math.round(mainPosition.y + (mainSize.height - childHeight) / 2),
    ))
  } catch {
    // Keep the platform default position if the main window geometry is unavailable.
  }
}

async function openConfirmWindow() {
  if (isConfirmWindow) { confirmOpen.value = true; return }
  if (!window.__TAURI_INTERNALS__) { confirmOpen.value = true; return }
  const snapshot = confirmSnapshot()
  try { localStorage.setItem('pixelflow.confirm-data', JSON.stringify(snapshot)) } catch { /* optional */ }
  const existing = await WebviewWindow.getByLabel('confirm')
  if (existing) {
    try {
      await existing.show()
      await existing.setFocus()
      void emitTo('confirm', 'confirm-data', snapshot)
      return
    } catch {
      // A stale WebView label can survive a crash/close. Recreate it below.
      await existing.close().catch(() => {})
    }
  }
  const confirmWindow = new WebviewWindow('confirm', {
    url: '/?view=confirm',
    title: '确认素材分类',
    width: 1220,
    height: 780,
    minWidth: 900,
    minHeight: 620,
    resizable: true,
    center: false,
    decorations: true,
    shadow: true,
    theme: darkMode.value ? 'dark' : 'light',
    titleBarStyle: 'visible',
    backgroundColor: darkMode.value ? '#1b1f24' : '#f5f7fa',
  })
  confirmWindow.once('tauri://created', () => {
    void centerWindowOnMain(confirmWindow, 1220, 780)
    void emitTo('confirm', 'confirm-data', snapshot)
  })
  confirmWindow.once('tauri://error', () => { status.value = '素材确认窗口打开失败' })
}

function clearClassificationWatchdog() {
  if (classificationWatchdog !== undefined) window.clearTimeout(classificationWatchdog)
  classificationWatchdog = undefined
}

function armClassificationWatchdog() {
  clearClassificationWatchdog()
  classificationWatchdog = window.setTimeout(() => {
    classificationWatchdog = undefined
    const requestId = classificationRequestId.value
    if (!requestId || !classificationRunning.value) return
    worker?.request('cancel_generation', { request_id: requestId })
    classificationRunning.value = false
    setClassificationProgress(0, true)
    isCancelling.value = false
    classificationRequestId.value = undefined
    status.value = '素材分析超时，请重试'
    taskPhase.value = '分析失败'
    taskDetail.value = 'Worker 在限定时间内没有返回分类结果；已请求停止当前任务。'
    updateResumePhase('failed')
  }, 120_000)
}

function clearCancelWatchdog() {
  if (cancelWatchdog !== undefined) window.clearTimeout(cancelWatchdog)
  cancelWatchdog = undefined
}

function settleCancellation(message: string, detail: string, phase = '已安全停止') {
  clearCancelWatchdog()
  clearClassificationWatchdog()
  const target = cancelTargetRequestId.value
  if (target && target === classificationRequestId.value) {
    updateResumePhase('cancelled')
    classificationRunning.value = false
    setClassificationProgress(0, true)
    classificationRequestId.value = undefined
  }
  if (target && target === generationRequestId.value) {
    updateResumePhase('cancelled')
    isRunning.value = false
    generationRequestId.value = undefined
  }
  cancelRequestId.value = undefined
  cancelTargetRequestId.value = undefined
  isCancelling.value = false
  status.value = message
  taskPhase.value = phase
  taskDetail.value = detail
}

function handleConfirmResult(payload: ConfirmSnapshot & { action?: 'confirm' | 'cancel' }) {
  applyConfirmSnapshot(payload)
  confirmOpen.value = false
  safeSetWindowTitle(mainWindowTitle)
  if (payload.action === 'confirm') {
    materialConfirmed.value = true
    status.value = '素材分类已确认，可以生成'
    taskPhase.value = '准备素材'
    taskDetail.value = '正在整理生成任务……'
    startGeneration()
    return
  }
  materialConfirmed.value = false
  status.value = '已取消素材确认，可继续确认或调整选项后重新开始'
  taskPhase.value = '准备就绪'
  taskDetail.value = '已保留当前素材分类和调整。'
}
async function saveSettings() {
  const settings = currentSettings()
  try { localStorage.setItem('pixelflow.output-settings', JSON.stringify(settings)) } catch { /* optional */ }
  if (isSettingsWindow && window.__TAURI_INTERNALS__) await emit('settings-saved', settings)
  await closeSettings(true)
}
function suggestedOutputRoot(path: string) {
  const normalized = path.replace(/[\\/]$/, '')
  const slash = Math.max(normalized.lastIndexOf('/'), normalized.lastIndexOf('\\'))
  const parent = slash >= 0 ? normalized.slice(0, slash) : ''
  const name = normalized.slice(slash + 1)
  return `${parent ? `${parent}/` : ''}${name.endsWith('主图') ? name : `${name}主图`}`
}
async function pickDirectory(target: 'input' | 'output' | 'model') {
  if (!window.__TAURI_INTERNALS__) {
    status.value = '请在桌面 App 中选择目录'
    return
  }
  try {
    const path = await invoke<string | null>('pick_directory')
    if (!path) return
    if (target === 'input') {
      inputRoot.value = path
      outputRoot.value = suggestedOutputRoot(path)
    } else if (target === 'output') outputRoot.value = path
    else {
      localModelPath.value = path
      localModelEnabled.value = true
      modelProbeReadyHandled = false
      modelDiagnostics.value = {}
      modelStatus.value = '状态：已选择外部模型目录，正在检测…'
      publishSettingsImmediately()
      if (worker) probeModels()
    }
  } catch (error) {
    status.value = String(error)
  }
}

type ModelProbeLock = { owner: string; root: string; startedAt: number }

function readModelProbeLock(): ModelProbeLock | undefined {
  try {
    const raw = localStorage.getItem(modelProbeLockKey)
    if (!raw) return undefined
    const value = JSON.parse(raw) as Partial<ModelProbeLock>
    if (typeof value.owner !== 'string' || typeof value.root !== 'string' || typeof value.startedAt !== 'number') return undefined
    return value as ModelProbeLock
  } catch {
    return undefined
  }
}

function claimModelProbe(root: string): boolean {
  const current = readModelProbeLock()
  if (current && Date.now() - current.startedAt < modelProbeLockTtlMs && current.owner !== modelProbeOwner) return false
  try {
    localStorage.setItem(modelProbeLockKey, JSON.stringify({ owner: modelProbeOwner, root, startedAt: Date.now() }))
    const claimed = readModelProbeLock()
    return claimed?.owner === modelProbeOwner && claimed.root === root
  } catch {
    return true
  }
}

function releaseModelProbe(root: string) {
  try {
    const current = readModelProbeLock()
    if (current?.owner === modelProbeOwner && current.root === root) localStorage.removeItem(modelProbeLockKey)
  } catch { /* optional storage */ }
}

function scheduleModelProbeRetry(root: string) {
  if (modelProbeRetryTimer !== undefined) return
  modelProbeRetryTimer = window.setTimeout(() => {
    modelProbeRetryTimer = undefined
    if (localModelPath.value.trim() !== root) return
    const current = readModelProbeLock()
    if (current && Date.now() - current.startedAt < modelProbeLockTtlMs) {
      scheduleModelProbeRetry(root)
      return
    }
    probeModels()
  }, 250)
}

function probeModels() {
  if (!worker) { modelStatus.value = '状态：Worker 未连接，无法检测模型'; return }
  if (!localModelPath.value.trim()) { modelStatus.value = '状态：请先选择外部模型总目录'; return }
  if (modelProbeRequestId.value && modelTesting.value) return
  const root = localModelPath.value.trim()
  if (!claimModelProbe(root)) {
    modelStatus.value = '状态：正在检测外部模型…'
    scheduleModelProbeRetry(root)
    return
  }
  modelTesting.value = true
  modelStatus.value = '状态：正在检测外部模型…'
  modelProbeRoot = root
  modelProbeRequestId.value = worker.request('probe_models', { model_root: localModelPath.value })
}

async function discoverDefaultModelPath() {
  if (!window.__TAURI_INTERNALS__ || localModelPath.value.trim()) return
  try {
    const discovered = await invoke<string | null>('discover_models_directory')
    if (!discovered || localModelPath.value.trim()) return
    localModelPath.value = discovered
    localModelEnabled.value = true
    modelStatus.value = '状态：已发现文稿中的外部模型目录，正在检测…'
    publishSettingsImmediately()
    if (workerConnected.value) probeModels()
  } catch {
    // Auto-discovery is an optional convenience; manual directory selection remains available.
  }
}

function publishSettingsImmediately() {
  const settings = currentSettings()
  try { localStorage.setItem('pixelflow.output-settings', JSON.stringify(settings)) } catch { /* optional */ }
  if (isSettingsWindow && window.__TAURI_INTERNALS__) void emit('settings-changed', settings)
}
function clearCache() {
  if (!worker) { settingsNotice.value = 'Worker 未连接'; return }
  settingsNotice.value = '正在清理缓存…'
  const requestId = worker.request('clear_cache', {})
  const stop = worker.onMessage((message) => {
    if (message.request_id !== requestId) return
    stop()
    const payload = message.payload as Record<string, unknown>
    settingsNotice.value = message.type === 'done'
      ? '缓存已清理'
      : `清理失败：${String(payload.message || '请重试')}`
  })
}
async function exportDiagnostics() {
  if (!window.__TAURI_INTERNALS__) {
    settingsNotice.value = '请在桌面 App 中导出诊断包'
    return
  }
  const snapshot = { ...diagnosticsSnapshot(), model_status: modelStatus.value }
  const entries = [
    { name: 'diagnostics.json', content: JSON.stringify(snapshot, null, 2) },
    { name: 'failures.txt', content: failureReportText() },
    { name: 'last-result.json', content: JSON.stringify(lastResult.value, null, 2) },
    { name: 'resume-task.json', content: JSON.stringify(resumeSnapshot.value, null, 2) },
  ]
  settingsNotice.value = '正在生成诊断包…'
  try {
    const path = await invoke<string | null>('export_diagnostics', {
      defaultName: 'PixelFlow-诊断包.zip',
      entries,
    })
    settingsNotice.value = path ? `诊断包已导出：${path}` : '已取消导出诊断包'
  } catch (error) {
    settingsNotice.value = `诊断包导出失败：${String(error)}`
  }
}
function resetBrand() {
  logoOverrides.value = {}
  Object.values(logoPreviews.value).forEach((url) => URL.revokeObjectURL(url))
  logoPreviews.value = {}
  settingsNotice.value = '已恢复内置品牌资源'
}
function logoFilename(key: string, fallback: string) {
  return logoOverrides.value[key]?.split(/[\\/]/).pop() || fallback
}
function logoPreviewUrl(key: string, fallback: string) { return logoPreviews.value[key] || fallback }
function previewParent(path: string) {
  const separator = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'))
  return separator > 0 ? path.slice(0, separator) : ''
}
function requestPreview(path: string, allowedRoots = [previewParent(path)]) {
  if (!worker) return Promise.reject(new Error('Worker 未连接'))
  return worker.requestResult<{ path: string; max_width: number; max_height: number; allowed_roots: string[] }, { mime_type: string; data: string }>(
    'read_preview',
    { path, max_width: 198, max_height: 138, allowed_roots: allowedRoots.filter(Boolean) },
  )
}
async function loadLogoPreview(key: string, path: string) {
  try {
    const preview = await requestPreview(path, [previewParent(path)])
    const binary = atob(preview.data)
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0))
    const previous = logoPreviews.value[key]
    logoPreviews.value[key] = URL.createObjectURL(new Blob([bytes], { type: preview.mime_type }))
    if (previous) URL.revokeObjectURL(previous)
  } catch { settingsNotice.value = 'Logo 无法预览，请重新选择' }
}
async function replaceLogo(key: string) {
  if (!window.__TAURI_INTERNALS__) return
  try {
    const path = await invoke<string | null>('pick_file', { key })
    if (!path) return
    logoOverrides.value[key] = path
    await loadLogoPreview(key, path)
    settingsNotice.value = `已选择 Logo：${logoFilename(key, path)}`
  } catch (error) {
    settingsNotice.value = String(error)
  }
}
function selectCategory(value: string) {
  categoryTemplate.value = value
  categoryHighlight.value = value
  categoryOpen.value = false
  categoryTriggerElement.value?.focus()
}
function toggleCategoryPicker() {
  categoryOpen.value = !categoryOpen.value
  if (categoryOpen.value) {
    categoryHighlight.value = categoryTemplate.value
    void focusCategoryOption()
  } else {
    categoryTriggerElement.value?.focus()
  }
}
function focusCategoryOption() {
  return nextTick(() => document.getElementById(`category-option-${categoryHighlight.value}`)?.focus())
}
function selectNaming(value: string) { outputNamingMode.value = value; namingOpen.value = false }
function onCategoryKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (categoryOpen.value) event.preventDefault()
    categoryOpen.value = false
    categoryTriggerElement.value?.focus()
    return
  }
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    if (!categoryOpen.value) toggleCategoryPicker()
    else {
      selectCategory(categoryHighlight.value)
      categoryTriggerElement.value?.focus()
    }
    return
  }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  if (!categoryOpen.value) categoryOpen.value = true
  if (event.key === 'Home') categoryHighlight.value = categoryKeys[0]
  else if (event.key === 'End') categoryHighlight.value = categoryKeys[categoryKeys.length - 1]
  else {
    const current = Math.max(0, categoryKeys.indexOf(categoryHighlight.value))
    const delta = event.key === 'ArrowDown' ? 1 : -1
    categoryHighlight.value = categoryKeys[(current + delta + categoryKeys.length) % categoryKeys.length]
  }
  void focusCategoryOption()
}
function trapDialogFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab') return
  const dialog = event.currentTarget as HTMLElement
  const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
  )).filter((element) => element.offsetParent !== null)
  if (!focusable.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
function focusFirstDialogControl(selector: string) {
  void nextTick(() => {
    const dialog = document.querySelector<HTMLElement>(selector)
    const first = dialog?.querySelector<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled])',
    )
    first?.focus()
  })
}
function onGlobalKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape') return
  event.preventDefault()
  categoryOpen.value = false
  namingOpen.value = false
  if (failureOpen.value) failureOpen.value = false
  else if (settingsOpen.value) closeSettings()
  else if (confirmOpen.value) dismissMaterialConfirmation()
  else if (reportOpen.value) closeReport()
}
function onDocumentPointerdown(event: PointerEvent) {
  if (categoryOpen.value && !(event.target as HTMLElement).closest('.category-picker')) categoryOpen.value = false
  if (namingOpen.value && !(event.target as HTMLElement).closest('.settings-naming-select')) namingOpen.value = false
}
async function copyOutputPath() {
  if (!outputRoot.value) return
  try { await navigator.clipboard.writeText(outputRoot.value); reportNotice.value = '输出路径已复制' } catch { reportNotice.value = '无法复制输出路径' }
}
function modelShaLabel(key: keyof typeof expectedModels, diagnostic?: ModelDiagnostic) {
  const actual = diagnostic?.model_version?.sha256
  if (!actual) return 'Worker 未提供校验信息'
  if (actual === expectedModels[key].sha256) return `匹配 · ${actual.slice(0, 8)}`
  return `不匹配 · ${actual.slice(0, 8)}`
}
function modelFileLabel(diagnostic?: ModelDiagnostic) {
  return diagnostic?.model || diagnostic?.model_version?.path?.split(/[\\/]/).pop() || '未检测到'
}
async function copyDiagnostics() {
  const diagnostics = diagnosticsSnapshot()
  try {
    await navigator.clipboard.writeText(JSON.stringify(diagnostics, null, 2))
    settingsNotice.value = '诊断信息已复制'
  } catch {
    settingsNotice.value = '无法复制诊断信息'
  }
}
async function openOutputFolder(path = outputRoot.value) {
  if (!path) return
  try {
    await invoke('open_path', { path })
    reportNotice.value = '已打开输出目录'
  } catch (error) {
    reportNotice.value = String(error)
  }
}
function failureReportText() {
  return ['PixelFlow 失败素材报告', '', ...failures.value.flatMap((failure, index) => [
    `${index + 1}. ${String(failure.source || '未知素材')}`,
    `   类型：${String(failure.code || 'unknown')}`,
    `   原因：${String(failure.message || '未提供原因')}`,
    '',
  ])].join('\n')
}
function reviewSources() {
  return reviewItems.value.map((item) => String(item.source || '')).filter(Boolean)
}
function reviewSizes(item: Record<string, unknown>) {
  return Array.isArray(item.sizes) ? item.sizes.map(String).join('、') : '未记录'
}
function reviewProtectedRegions(item: Record<string, unknown>) {
  if (!Array.isArray(item.protected_regions) || !item.protected_regions.length) return '未记录'
  return item.protected_regions.map((region) => {
    const value = region as Record<string, unknown>
    const bbox = Array.isArray(value.bbox) ? value.bbox.map((part) => Number(part).toFixed(0)).join(', ') : '未记录'
    return `${String(value.kind || '区域')} [${bbox}]`
  }).join('；')
}
function acceptReviewItems() {
  const accepted = Array.from(new Set([...acceptedReviewSources.value, ...reviewSources()]))
  lastResult.value = { ...lastResult.value, accepted_review_sources: accepted }
  cacheReportSnapshot()
  reportNotice.value = `已接受 ${accepted.length} 项复核结果`
  reviewOpen.value = false
}
async function copyFailureReport() {
  try { await navigator.clipboard.writeText(failureReportText()); failureNotice.value = '失败清单已复制' } catch { failureNotice.value = '无法复制失败清单' }
}
async function exportFailureReport() {
  if (!window.__TAURI_INTERNALS__) { failureNotice.value = '请在桌面 App 中导出失败报告'; return }
  try {
    const path = await invoke<string | null>('save_text_file', { defaultName: 'PixelFlow-失败素材.txt', content: failureReportText() })
    if (path) failureNotice.value = `失败报告已导出：${path}`
  } catch (error) {
    failureNotice.value = String(error)
  }
}
function retryFailures(sourceFilterOverride?: string[], forceRerender = false) {
  const sourceFilter = sourceFilterOverride || failures.value
    .map((failure) => String(failure.retry_source || failure.source || ''))
    .filter(Boolean)
  if (!sourceFilter.length) { status.value = '失败素材没有可重试路径'; return }
  if (isReportWindow && window.__TAURI_INTERNALS__) {
    void emit('report-action', { action: 'retry', sourceFilter, forceRerender })
    void closeReport()
    return
  }
  if (!worker || isRunning.value) return
  reportOpen.value = false
  failureOpen.value = false
  setProgress(0, true)
  const payload = { ...generationPayload(sourceFilter, forceRerender), material_confirmation_completed: true }
  generationRequestId.value = worker.request('retry_failed', payload)
  persistGenerationResume(payload)
  status.value = `准备重试 ${sourceFilter.length} 项失败素材`
}
function generationPayload(sourceFilter?: string[], forceRerender = false) {
  const logo_overrides = Object.fromEntries(
    Object.entries(logoOverrides.value).map(([key, path]) => [
      ({ logo_square_dark: 'logo', logo_square_light: 'white_logo', logo_tall_dark: 'tall_logo', logo_tall_light: 'tall_white_logo' } as Record<string, string>)[key] || key,
      path,
    ]),
  )
  return {
    product_root: inputRoot.value, output_root: outputRoot.value, selected_sizes: selectedSizes.value,
    mode: 'v3',
    include_logo: includeLogo.value, include_vip: includeVip.value, include_model_images: includeModelImages.value,
    local_model_enabled: localModelEnabled.value, local_model_path: localModelPath.value || undefined,
    logo_overrides,
    enable_material_understanding: enableMaterialUnderstanding.value,
    category_override: categoryTemplate.value === 'auto' || categoryTemplate.value === 'none' ? undefined : categoryTemplate.value,
    enable_category_template: categoryTemplate.value !== 'none', max_size_mb: Number(maxSizeMb.value) > 0 ? Number(maxSizeMb.value) : undefined,
    naming_mode: outputNamingMode.value, material_confirmation_completed: materialConfirmed.value,
    source_type_overrides: Object.fromEntries(materials.value.map((item) => [item.source, item.source_type])),
    source_metadata_overrides: Object.fromEntries(materials.value.filter((item) => item.metadata).map((item) => [item.source, item.metadata])),
    excluded_model_sources: excludedModelSources.value, source_scale_adjustments: scaleAdjustments.value, source_filter: sourceFilter,
    force_rerender: forceRerender,
  }
}

function persistGenerationResume(payload: Record<string, unknown>, phase: ResumePhase = 'generating') {
  saveResumeTask({
    schema_version: 1,
    phase,
    request_kind: 'generation',
    saved_at: new Date().toISOString(),
    input_root: inputRoot.value,
    output_root: outputRoot.value,
    generation_payload: compactResumePayload(payload),
    task_phase: taskPhase.value,
    task_detail: taskDetail.value,
    task_id: generationRequestId.value,
  })
}

function resumeTask() {
  const snapshot = resumeSnapshot.value
  if (!snapshot || isRunning.value || classificationRunning.value) return
  if (isReportWindow && window.__TAURI_INTERNALS__) {
    void emit('report-action', { action: 'resume' })
    void closeReport()
    return
  }
  if (!worker) return
  inputRoot.value = snapshot.input_root
  outputRoot.value = snapshot.output_root
  if (snapshot.request_kind === 'classification') {
    const classification = snapshot.classification_payload
    if (classification) {
      if (typeof classification.category === 'string') categoryTemplate.value = classification.category
      if (typeof classification.local_model_enabled === 'boolean') localModelEnabled.value = classification.local_model_enabled
      if (typeof classification.local_model_path === 'string') localModelPath.value = classification.local_model_path
    }
    classifyMaterials(true)
    return
  }
  const payload = snapshot.generation_payload
  if (!payload) {
    status.value = '未找到可恢复的任务参数，请重新开始'
    clearResumeTask()
    return
  }
  const selected = payload.selected_sizes
  if (Array.isArray(selected) && selected.every((value) => typeof value === 'string')) selectedSizes.value = selected as string[]
  materialConfirmed.value = true
  reportOpen.value = false
  workerInterrupted.value = false
  isRunning.value = true
  isCancelling.value = false
  setProgress(0, true)
  clearProgressComplete()
  status.value = '正在继续未完成任务'
  taskPhase.value = '恢复任务'
  taskDetail.value = '已生成的输出会自动复用，继续处理未完成素材。'
  const requestPayload = { ...payload, material_confirmation_completed: true, force_rerender: false }
  generationRequestId.value = worker.request('start_generation', requestPayload)
  persistGenerationResume(requestPayload)
}

async function revealMainWindow() {
  const current = getCurrentWindow()
  // A hidden Tauri window can finish WebView creation one turn after Vue
  // mounts. Retry the native reveal instead of losing the only show call.
  for (const delay of [0, 32, 128, 320, 800]) {
    if (delay) await new Promise((resolve) => window.setTimeout(resolve, delay))
    try {
      await current.show()
      await current.setFocus()
      if (await current.isVisible()) return
    } catch {
      // The next attempt covers the window-created race.
    }
  }
}

onMounted(() => {
  let hasSavedModelPath = false
  const savedResume = readResumeTask()
  if (savedResume) {
    resumeSnapshot.value = savedResume
    if (!isSettingsWindow && !isReportWindow && !isConfirmWindow) {
      inputRoot.value = savedResume.input_root
      outputRoot.value = savedResume.output_root
      const sizes = savedResume.generation_payload?.selected_sizes
      if (Array.isArray(sizes) && sizes.every((value) => typeof value === 'string')) selectedSizes.value = sizes as string[]
      status.value = '检测到未完成任务，可继续处理'
      taskPhase.value = '任务可恢复'
      taskDetail.value = '已完成的输出会自动复用。'
    }
  }
  try {
    theme.value = readInitialTheme()
    reducedMotion.value = localStorage.getItem('pixelflow.reduced-motion') === 'true'
    classificationConcurrency.value = localStorage.getItem('pixelflow.classification-concurrency') || 'auto'
    const savedSettings = localStorage.getItem('pixelflow.output-settings')
    if (savedSettings) {
      const parsed = JSON.parse(savedSettings) as Partial<SettingsSnapshot>
      hasSavedModelPath = typeof parsed.localModelPath === 'string' && Boolean(parsed.localModelPath.trim())
      applySettings(parsed)
    }
    const savedSizes = JSON.parse(localStorage.getItem('pixelflow.selected-sizes') || 'null')
    if (Array.isArray(savedSizes) && savedSizes.every((item) => ['1440x1440', '1440x1920', '1125x1500'].includes(item))) selectedSizes.value = savedSizes
    maxSizeMb.value = localStorage.getItem('pixelflow.max-size-mb') || maxSizeMb.value
  } catch {
    // Storage is optional in embedded webviews.
  }
  if (isSettingsWindow) settingsSnapshot = currentSettings()
  systemMedia = window.matchMedia('(prefers-color-scheme: dark)')
  updateSystemTheme()
  if (typeof systemMedia.addEventListener === 'function') systemMedia.addEventListener('change', onMediaThemeChange)
  else systemMedia.addListener?.(onMediaThemeChange)
  if (window.__TAURI_INTERNALS__) {
    void getCurrentWindow().onThemeChanged(({ payload }) => {
      updateSystemTheme(payload)
    }).then((unlisten) => { stopNativeThemeListener = unlisten }).catch(() => {})
  }
  window.addEventListener('keydown', onGlobalKeydown)
  document.addEventListener('pointerdown', onDocumentPointerdown)

  const bridge = windowWorkerBridge()
  if (bridge && !isReportWindow) {
    worker = new WorkerClient(bridge, isConfirmWindow ? undefined : handleWorkerMessage, isConfirmWindow ? 'confirm' : (isSettingsWindow ? 'settings' : 'main'))
    worker.request('ping', {})
    Object.entries(logoOverrides.value).forEach(([key, path]) => { void loadLogoPreview(key, path) })
  }
  if (!hasSavedModelPath && !isReportWindow && !isConfirmWindow) void discoverDefaultModelPath()
  if (!isSettingsWindow && !isReportWindow && !isConfirmWindow && window.__TAURI_INTERNALS__) {
    void listen<{ paths?: string[] }>('tauri://drag-drop', (event) => {
      applyDroppedPaths(event.payload?.paths || [])
    }).then((unlisten) => { stopDropListener = unlisten })
    void listen<SettingsSnapshot>('settings-saved', (event) => applySettings(event.payload))
      .then((unlisten) => { stopSettingsListener = unlisten })
    void listen<SettingsSnapshot>('settings-changed', (event) => applySettings(event.payload))
      .then((unlisten) => { stopSettingsChangedListener = unlisten })
    void listen('report-ready', () => { void emit('report-data', reportSnapshot()) })
      .then((unlisten) => { stopReportReadyListener = unlisten })
    void listen<{ action?: string; sourceFilter?: string[]; forceRerender?: boolean }>('report-action', (event) => {
      if (event.payload?.action === 'retry') retryFailures(event.payload.sourceFilter || [], event.payload.forceRerender)
      else if (event.payload?.action === 'resume') resumeTask()
    }).then((unlisten) => { stopReportActionListener = unlisten })
    void listen('confirm-ready', () => { void emitTo('confirm', 'confirm-data', confirmSnapshot()) })
      .then((unlisten) => { stopConfirmReadyListener = unlisten })
    void listen<ConfirmSnapshot & { action?: 'confirm' | 'cancel' }>('confirm-result', (event) => {
      handleConfirmResult(event.payload)
    }).then((unlisten) => { stopConfirmResultListener = unlisten })
  }
  if (isReportWindow && window.__TAURI_INTERNALS__) {
    void listen<ReportSnapshot>('report-data', (event) => {
      applyReportSnapshot(event.payload)
    }).then((unlisten) => {
      stopReportDataListener = unlisten
      void emit('report-ready')
    })
    try {
      const cached = localStorage.getItem('pixelflow.report-data')
      if (cached) applyReportSnapshot(JSON.parse(cached) as ReportSnapshot)
    } catch { /* optional */ }
  }
  if (isConfirmWindow && window.__TAURI_INTERNALS__) {
    void listen<ConfirmSnapshot>('confirm-data', (event) => {
      applyConfirmSnapshot(event.payload)
    }).then((unlisten) => {
      stopConfirmDataListener = unlisten
      void emitTo('main', 'confirm-ready')
    })
    try {
      const cached = localStorage.getItem('pixelflow.confirm-data')
      if (cached) applyConfirmSnapshot(JSON.parse(cached) as ConfirmSnapshot)
    } catch { /* optional */ }
  }
  window.addEventListener('storage', onStorageChange)
  // Keep the native main window hidden until the first themed Vue frame is
  // mounted. This avoids exposing Tauri's static light background while the
  // persisted/system theme is still being applied during cold startup.
  if (!isSettingsWindow && !isReportWindow && !isConfirmWindow && window.__TAURI_INTERNALS__) {
    void revealMainWindow()
  }

  if (isConfirmWindow) void nextTick(() => setupCardObserver())
  if (isSettingsWindow) focusFirstDialogControl('.settings-dialog')
  if (isConfirmWindow) focusFirstDialogControl('.confirmation-dialog')
  if (isReportWindow) focusFirstDialogControl('.report-dialog')
})

onBeforeUnmount(() => worker?.dispose())
onBeforeUnmount(() => clearProgressComplete())
onBeforeUnmount(() => {
  if (modelProbeRetryTimer !== undefined) window.clearTimeout(modelProbeRetryTimer)
})
onBeforeUnmount(() => previewUrls.forEach((url) => URL.revokeObjectURL(url)))
onBeforeUnmount(() => {
  previewObserver?.disconnect()
  cardObserver.value?.disconnect()
  if (previewBackgroundTimer) window.clearTimeout(previewBackgroundTimer)
  if (previewScrollTimer) window.clearTimeout(previewScrollTimer)
  previewScrollRoot?.removeEventListener('scroll', onPreviewScroll)
})
onBeforeUnmount(() => Object.values(logoPreviews.value).forEach((url) => URL.revokeObjectURL(url)))
onBeforeUnmount(() => stopSettingsListener?.())
onBeforeUnmount(() => stopSettingsChangedListener?.())
onBeforeUnmount(() => stopReportReadyListener?.())
onBeforeUnmount(() => stopReportDataListener?.())
onBeforeUnmount(() => stopReportActionListener?.())
onBeforeUnmount(() => stopConfirmReadyListener?.())
onBeforeUnmount(() => stopConfirmDataListener?.())
onBeforeUnmount(() => stopConfirmResultListener?.())
onBeforeUnmount(() => stopDropListener?.())
onBeforeUnmount(() => {
  if (typeof systemMedia?.removeEventListener === 'function') systemMedia.removeEventListener('change', onMediaThemeChange)
  else systemMedia?.removeListener?.(onMediaThemeChange)
})
onBeforeUnmount(() => stopNativeThemeListener?.())
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalKeydown))
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocumentPointerdown))
onBeforeUnmount(() => window.removeEventListener('storage', onStorageChange))

function updateSystemTheme(nativeTheme?: 'light' | 'dark') {
  if (theme.value !== 'system') return
  systemDark.value = nativeTheme ? nativeTheme === 'dark' : systemMedia?.matches ?? false
}

function onMediaThemeChange() { updateSystemTheme() }

function applySystemTheme() {
  if (theme.value !== 'system') return
  updateSystemTheme()
  syncNativeWindowTheme()
}

function syncNativeWindowTheme() {
  if (!window.__TAURI_INTERNALS__) return
  void getCurrentWindow().setTheme(theme.value === 'system' ? null : darkMode.value ? 'dark' : 'light').catch(() => {})
}

function safeSetWindowTitle(title: string) {
  if (!window.__TAURI_INTERNALS__) return
  void getCurrentWindow().setTitle(title).catch(() => {})
}

function onStorageChange(event: StorageEvent) {
  if (!event.newValue) return
  try {
    if (event.key === 'pixelflow.output-settings') applySettings(JSON.parse(event.newValue) as Partial<SettingsSnapshot>)
    else if (event.key === 'pixelflow.theme' && ['system', 'light', 'dark'].includes(event.newValue)) applySettings({ theme: event.newValue as Theme })
    else if (event.key === 'pixelflow.reduced-motion') applySettings({ reducedMotion: event.newValue === 'true' })
    else if (event.key === 'pixelflow.classification-concurrency') applySettings({ classificationConcurrency: event.newValue })
  } catch { /* optional storage */ }
}

watch(theme, (value) => {
  try { localStorage.setItem('pixelflow.theme', value) } catch { /* optional */ }
  if (value === 'system') applySystemTheme()
  else syncNativeWindowTheme()
})

watch(reducedMotion, (value) => {
  try { localStorage.setItem('pixelflow.reduced-motion', String(value)) } catch { /* optional */ }
})

watch(classificationConcurrency, (value) => {
  try { localStorage.setItem('pixelflow.classification-concurrency', value) } catch { /* optional */ }
})

watch(
  [theme, reducedMotion, classificationConcurrency, includeLogo, includeVip, enableMaterialUnderstanding,
    includeModelImages, localModelEnabled, localModelPath, showLogoOption, showVipOption, showMaterialOption,
    outputNamingMode, maxSizeMb],
  () => { if (isSettingsWindow) publishSettingsImmediately() },
)
watch(logoOverrides, () => { if (isSettingsWindow) publishSettingsImmediately() }, { deep: true })
watch(darkMode, () => syncNativeWindowTheme())
watch(localModelPath, (value, previous) => {
  if (value !== previous) modelProbeReadyHandled = false
})

watch(materials, () => {
  nextTick(() => {
    if (confirmGridElement.value) {
      const grid = confirmGridElement.value
      confirmGridHasOverflow.value = grid.scrollHeight > grid.clientHeight
      isConfirmGridScrolledToBottom.value = grid.scrollTop + grid.clientHeight >= grid.scrollHeight - 10
    }
  })
}, { flush: 'post' })

watch(confirmOpen, (open) => {
  if (!open) {
    previewSession += 1
    previewQueue.length = 0
    previewQueuedSources.clear()
    previewObserver?.disconnect()
    previewObserver = undefined
    if (previewBackgroundTimer) window.clearTimeout(previewBackgroundTimer)
    previewBackgroundIndex = 0
    if (previewScrollTimer) window.clearTimeout(previewScrollTimer)
    previewScrollRoot?.removeEventListener('scroll', onPreviewScroll)
    previewScrollRoot = undefined
    previewScrolling = false
    return
  }
  previewSession += 1
  void nextTick(() => {
    refreshMaterialPreviews()
    startPreviewObserver()
  })
}, { immediate: isConfirmWindow })

watch(settingsOpen, (open) => { if (open) focusFirstDialogControl('.settings-dialog') })
watch(confirmOpen, (open) => { if (open) focusFirstDialogControl('.confirmation-dialog') })
watch(reportOpen, (open) => { if (open) focusFirstDialogControl('.report-dialog') })
watch(reviewOpen, (open) => { if (open) focusFirstDialogControl('.review-dialog') })
watch(failureOpen, (open) => { if (open) focusFirstDialogControl('.failure-dialog') })

function handleWorkerMessage(message: WorkerMessage) {
  const payload = message.payload as Record<string, unknown>
  if (message.type === 'ready') {
    workerConnected.value = true
    workerReadyState.value = '已连接'
    workerVersion.value = String(payload.worker_version || '未知')
    workerRulesVersion.value = String(payload.rules_version || '未知')
    workerBuildHash.value = String(payload.build_hash || '')
    const versionMismatch = payload.worker_version !== expectedWorkerVersion
    const rulesMismatch = payload.rules_version !== expectedRulesVersion
    if (workerInterrupted.value) {
      status.value = 'Worker 已重启；上次任务中断，请重新开始'
      taskPhase.value = '任务中断'
      taskDetail.value = status.value
    } else if (versionMismatch || rulesMismatch) {
      status.value = 'Worker 版本与当前 App 不匹配，请重新构建或更新资源'
      taskPhase.value = '版本不匹配'
      taskDetail.value = `Worker ${String(payload.worker_version || '未知')} / 规则 ${String(payload.rules_version || '未知')}`
    } else {
      status.value = '准备就绪'
      taskPhase.value = '准备就绪'
      taskDetail.value = '准备就绪'
    }
    if (!isReportWindow && !isConfirmWindow && !modelProbeReadyHandled && !modelTesting.value
      && localModelEnabled.value && localModelPath.value.trim()) {
      modelProbeReadyHandled = true
      probeModels()
    }
  } else if (message.type === 'status' && message.request_id === classificationRequestId.value) {
    classificationRunning.value = true
    status.value = '正在分析素材'
    taskPhase.value = '识别分类'
    taskDetail.value = '正在识别素材类型……'
  } else if (message.type === 'status' && message.request_id === generationRequestId.value && payload.state === 'started') {
    isRunning.value = true
    isCancelling.value = false
    status.value = '任务已开始'
    taskPhase.value = '准备素材'
    taskDetail.value = '正在整理生成任务……'
  } else if (message.type === 'progress' && message.request_id === classificationRequestId.value) {
    armClassificationWatchdog()
    const total = Number(payload.total || 0)
    const completed = Number(payload.completed || 0)
    const phase = String(payload.phase || '规则分类')
    const source = String(payload.source || '').split(/[\\/]/).pop() || '当前素材'
    const decision = payload.decision as { source_type?: string } | undefined
    const typeLabels: Record<string, string> = { main_product: '商品白底图', detail: '细节图', model: '模特图', model_detail: '模特细节图' }
    if (phase === 'Pose') {
      setClassificationProgress(total ? 400 + Math.round((completed / total) * 450) : 400)
      taskDetail.value = `${completed} / ${total} · Pose 分析：${source}`
    } else if (phase === 'Seg') {
      setClassificationProgress(total ? 850 + Math.round((completed / total) * 150) : 850)
      taskDetail.value = `${completed} / ${total} · Seg 疑难图：${source}`
    } else if (phase === 'Pose/Seg') {
      setClassificationProgress(total ? 400 + Math.round((completed / total) * 600) : 400)
      taskDetail.value = `${completed} / ${total} · Pose/Seg 分析：${source}`
    } else {
      setClassificationProgress(total ? Math.round((completed / total) * 400) : 0)
      taskDetail.value = `${completed} / ${total} · 素材识别：${source} → ${typeLabels[decision?.source_type || ''] || '其他'}`
    }
    taskPhase.value = '识别分类'
    status.value = taskDetail.value
  } else if (message.type === 'done' && message.request_id === classificationRequestId.value) {
    clearClassificationWatchdog()
    const result = payload.result as { decisions?: MaterialDecision[]; category?: { category: string; source: string; matched_keyword?: string }; suggested_exclusions?: string[]; preflight_warnings?: string[] } | undefined
    materials.value = Array.isArray(result?.decisions)
      ? result.decisions.map((material) => ({
        ...material,
        previewUrl: '',
        isVisible: false,
      }))
      : []
    previewQueue.length = 0
    classificationRunning.value = false
    classifiedInputRoot.value = inputRoot.value
    classifiedOptionsKey.value = pendingClassificationKey
    categorySuggestion.value = result?.category
    preflightWarnings.value = result?.preflight_warnings || []
    // Keep the v2.6 category compatibility suggestions as the initial
    // selection; users can still clear any checkbox before confirming.
    excludedModelSources.value = Array.isArray(result?.suggested_exclusions)
      ? result.suggested_exclusions.filter((source) => materials.value.some((material) => material.source === source))
      : []
    setClassificationProgress(1000, true)
    markProgressComplete()
    clearResumeTask()
    materialConfirmed.value = false
    status.value = materials.value.length ? '请确认素材分类' : '未发现可分类素材，请确认后继续'
    taskPhase.value = '识别分类'
    taskDetail.value = status.value
    void openConfirmWindow()
    safeSetWindowTitle(`确认素材分类 · ${materials.value.length} 张`)
  } else if (message.type === 'done' && message.request_id === modelProbeRequestId.value) {
    const root = modelProbeRoot
    modelProbeRoot = ''
    modelTesting.value = false
    const models = payload.models as Record<string, ModelDiagnostic> | undefined
    modelDiagnostics.value = models || {}
    const modelLabel = (key: string) => {
      const model = models?.[key]
      return `${model?.state || '未配置'}${model?.error ? `（${model.error.split('\n')[0]}）` : ''}`
    }
    modelStatus.value = `状态：Pose ${modelLabel('pose')}；Seg ${modelLabel('segmentation')}；Detection ${modelLabel('detection')}`
    releaseModelProbe(root)
  } else if (message.type === 'status' && message.request_id === modelProbeRequestId.value) {
    modelTesting.value = true
    modelStatus.value = `状态：${String(payload.message || '正在检测外部模型…')}`
  } else if (message.type === 'done' && message.request_id.startsWith('main-') && 'cleared' in payload) {
    settingsNotice.value = '缓存已清理'
  } else if (message.type === 'progress' && message.request_id === generationRequestId.value) {
    const total = Number(payload.total || 0)
    const completed = Number(payload.completed || 0)
    const detail = String(payload.phase || '处理中')
    setProgress(total ? Math.round((completed / total) * 1000) : 0)
    taskPhase.value = /^(透明图|唯品图|卖点图)：/.test(detail) ? '整理输出' : '生成图片'
    taskDetail.value = `${completed} / ${total} · ${detail}`
    status.value = taskDetail.value
  } else if (message.type === 'done' && message.request_id === generationRequestId.value) {
    isRunning.value = false
    isCancelling.value = false
    setProgress(1000, true)
    markProgressComplete()
    status.value = '生成完成'
    taskPhase.value = '已完成'
    taskDetail.value = '所有任务已完成，输出已整理。'
    lastResult.value = (payload.result as Record<string, unknown>) || {}
    clearResumeTask()
    materialConfirmed.value = false
    showReport()
  } else if (message.type === 'cancelled' && message.request_id === generationRequestId.value) {
    clearCancelWatchdog()
    isRunning.value = false
    isCancelling.value = false
    lastResult.value = (payload.result as Record<string, unknown>) || {}
    updateResumePhase('cancelled', lastResult.value)
    materialConfirmed.value = false
    status.value = '任务已取消'
    taskPhase.value = '已安全停止'
    taskDetail.value = '已保留已完成的输出。'
    showReport()
  } else if (message.type === 'cancelled' && message.request_id === classificationRequestId.value) {
    updateResumePhase('cancelled')
    settleCancellation('素材分析已取消', '素材识别已停止，未开始生成；可以调整选项后重新开始。')
  } else if (message.type === 'status' && message.request_id === cancelRequestId.value) {
    if (payload.state === 'cancelling') {
      taskDetail.value = 'Worker 正在停止当前任务，并保留已完成的输出。'
    } else if (payload.state === 'idle') {
      settleCancellation('取消请求已完成', '当前任务已结束；已保留已有输出，可以重新开始。')
    }
  } else if (message.type === 'error') {
    if (message.request_id === cancelRequestId.value) {
      settleCancellation('取消请求失败', String(payload.message || 'Worker 未接受取消请求'), '任务失败')
      return
    }
    if (message.request_id === modelProbeRequestId.value) {
      modelTesting.value = false
      const messageText = String(payload.message || '请检查模型目录')
      modelStatus.value = `状态：模型检测失败；${messageText}`
      const root = modelProbeRoot
      modelProbeRoot = ''
      releaseModelProbe(root)
      return
    }
    if (message.request_id !== 'worker' && message.request_id !== generationRequestId.value && message.request_id !== classificationRequestId.value) return
    if (message.request_id === 'worker') {
      workerConnected.value = false
      workerReadyState.value = 'Worker 已退出'
      workerInterrupted.value = true
      const taskId = String(payload.task_id || '')
      if (modelTesting.value) {
        modelTesting.value = false
        modelStatus.value = '状态：Worker 已中断，模型检测未完成'
        const root = modelProbeRoot
        modelProbeRoot = ''
        modelProbeReadyHandled = false
        releaseModelProbe(root)
      }
      if (taskId) taskDetail.value = `任务 ${taskId} 已中断，可重试未完成项。`
      updateResumePhase('interrupted')
    } else if (message.request_id === generationRequestId.value || message.request_id === classificationRequestId.value) {
      updateResumePhase('failed')
    }
    clearClassificationWatchdog()
    isRunning.value = false
    isCancelling.value = false
    if (message.request_id === classificationRequestId.value) setClassificationProgress(0, true)
    classificationRunning.value = false
    status.value = String(payload.message || 'Worker 执行失败')
    taskPhase.value = '生成失败'
    taskDetail.value = '任务未完成，请检查素材与输出位置后重试。'
  }
}

function classifyMaterials(fromResume = false) {
  if (!inputRoot.value) {
    status.value = '请先填写输入目录'
    return
  }
  if (!worker) {
    status.value = 'Worker 未连接，暂不能分析素材'
    return
  }
  if (classificationRunning.value || isRunning.value) return
  if (!fromResume) clearResumeTask()
  workerInterrupted.value = false
  materials.value = []
  previewElements.clear()
  previewLoadedSources.clear()
  previewFailedSources.clear()
  previewQueue.length = 0
  previewQueuedSources.clear()
  previewBackgroundIndex = 0
  classifiedInputRoot.value = ''
  categorySuggestion.value = undefined
  preflightWarnings.value = []
  pendingClassificationKey = currentClassificationKey()
  excludedModelSources.value = []
  scaleAdjustments.value = {}
  materialConfirmed.value = false
  clearProgressComplete()
  setClassificationProgress(0, true)
  classificationRunning.value = true
  isCancelling.value = false
  status.value = '正在分析素材'
  taskPhase.value = '识别分类'
  taskDetail.value = '正在识别素材类型……'
  const workers = classificationConcurrency.value === 'conservative'
    ? 1
    : classificationConcurrency.value === 'performance'
      ? Math.min(4, navigator.hardwareConcurrency || 4)
      : Math.min(4, Math.max(1, Math.floor((navigator.hardwareConcurrency || 2) / 2)))
  const classificationPayload = {
    product_root: inputRoot.value,
    output_root: outputRoot.value,
    workers,
    category: categoryTemplate.value === 'auto' || categoryTemplate.value === 'none' ? undefined : categoryTemplate.value,
    local_model_enabled: localModelEnabled.value,
    local_model_path: localModelPath.value || undefined,
  }
  classificationRequestId.value = worker.request('classify_materials', classificationPayload)
  saveResumeTask({
    schema_version: 1,
    phase: 'classifying',
    request_kind: 'classification',
    saved_at: new Date().toISOString(),
    input_root: inputRoot.value,
    output_root: outputRoot.value,
    classification_payload: classificationPayload,
    task_phase: taskPhase.value,
    task_detail: taskDetail.value,
    task_id: classificationRequestId.value,
  })
  armClassificationWatchdog()
}

function currentClassificationKey() {
  return JSON.stringify([inputRoot.value, outputRoot.value, categoryTemplate.value, localModelEnabled.value, localModelPath.value])
}

function setMaterialType(material: MaterialDecision, type: MaterialType) {
  material.source_type = type
  if (type !== 'model' && type !== 'model_detail') excludedModelSources.value = excludedModelSources.value.filter((source) => source !== material.source)
  materialConfirmed.value = false
}

function onMaterialTypeChange(material: MaterialDecision, event: Event) {
  setMaterialType(material, (event.target as HTMLSelectElement).value as MaterialType)
}

function onMaterialPreviewError(material: MaterialDecision) {
  if (material.previewUrl?.startsWith('blob:')) URL.revokeObjectURL(material.previewUrl)
  material.previewUrl = ''
  material.previewFailed = true
  material.previewReady = false
  previewFailedSources.add(material.source)
}

function onPreviewLoad(material: MaterialDecision) {
  material.previewReady = true
}

function setupCardObserver() {
  if (!confirmOpen.value) return
  cardObserver.value?.disconnect()
  cardObserver.value = null
  if (typeof IntersectionObserver !== 'function') {
    materials.value.forEach((material) => { material.isVisible = true })
    return
  }
  const root = confirmGridElement.value
  cardObserver.value = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return
      const source = entry.target.getAttribute('data-source')
      const material = materials.value.find((item) => item.source === source)
      if (material) material.isVisible = true
    })
  }, { root, threshold: 0.1, rootMargin: '50px' })
  previewElements.forEach((element, source) => {
    if (!materials.value.some((item) => item.source === source)) return
    element.setAttribute('data-source', source)
    cardObserver.value?.observe(element)
  })
}

function observeCard(el: Element | ComponentPublicInstance | null, material: MaterialDecision) {
  if (!el) return
  const element = el instanceof Element ? el : (el as ComponentPublicInstance).$el
  if (element instanceof HTMLElement) {
    element.setAttribute('data-source', material.source)
    if (cardObserver.value) cardObserver.value.observe(element)
    else if (typeof IntersectionObserver !== 'function') material.isVisible = true
  }
}

function onConfirmGridScroll(event: Event) {
  const element = event.target as HTMLElement
  const scrollTop = element.scrollTop
  const scrollHeight = element.scrollHeight
  const clientHeight = element.clientHeight

  // 判断是否滚动到底部（容差 10px）
  isConfirmGridScrolledToBottom.value = scrollTop + clientHeight >= scrollHeight - 10
}

async function loadMaterialPreview(material: MaterialDecision, session = previewSession) {
  if (material.previewLoading) return
  material.previewLoading = true
  const currentMaterial = () => materials.value.find((item) => item.source === material.source)
  try {
    const workerClient = worker
    if (!workerClient) throw new Error('Worker 未连接')
    const request = workerClient.requestResult<{ path: string; max_width: number; max_height: number; allowed_roots: string[] }, { mime_type: string; data: string }>(
      'read_preview',
      { path: material.source, max_width: 198, max_height: 138, allowed_roots: [classifiedInputRoot.value || inputRoot.value] },
    )
    const preview = await Promise.race([
      request,
      new Promise<never>((_, reject) => window.setTimeout(() => reject(new Error('预览读取超时')), 15000)),
    ])
    if (session !== previewSession) return
    const target = currentMaterial() || material
    const binary = atob(preview.data)
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0))
    const url = URL.createObjectURL(new Blob([bytes], { type: preview.mime_type }))
    previewUrls.add(url)
    previewLoadedSources.add(material.source)
    target.previewReady = false
    target.previewUrl = url
    target.previewFailed = false
  } catch {
    const target = session === previewSession ? (currentMaterial() || material) : material
    target.previewUrl = ''
    target.previewFailed = true
    target.previewReady = false
    previewFailedSources.add(material.source)
  } finally {
    material.previewLoading = false
    if (session === previewSession) {
      const target = currentMaterial()
      if (target) target.previewLoading = false
    }
  }
}

function pumpMaterialPreviews() {
  if (previewScrolling) return
  while (activePreviewLoads < previewConcurrencyLimit.value && previewQueue.length) {
    const material = previewQueue.shift()
    if (!material) continue
    previewQueuedSources.delete(material.source)
    if (previewLoadedSources.has(material.source) || previewFailedSources.has(material.source) || material.previewUrl || material.previewLoading) continue
    activePreviewLoads += 1
    const session = previewSession
    void loadMaterialPreview(material, session).finally(() => {
      activePreviewLoads -= 1
      pumpMaterialPreviews()
    })
  }
}

function enqueueMaterialPreview(material: MaterialDecision, priority = false) {
  if (previewLoadedSources.has(material.source) || previewFailedSources.has(material.source) || material.previewUrl || material.previewLoading) return
  if (previewQueuedSources.has(material.source)) {
    if (!priority) return
    const index = previewQueue.findIndex((item) => item.source === material.source)
    if (index >= 0) previewQueue.splice(index, 1)
  }
  material.previewFailed = false
  if (priority) previewQueue.unshift(material)
  else previewQueue.push(material)
  previewQueuedSources.add(material.source)
  pumpMaterialPreviews()
}

function refreshMaterialPreviews(priority = false) {
  const root = confirmGridElement.value
  const rootRect = root?.getBoundingClientRect()
  let visibleCount = 0
  for (const material of materials.value) {
    const element = previewElements.get(material.source)
    if (!element || !rootRect) continue
    const rect = element.getBoundingClientRect()
    if (rect.bottom > rootRect.top && rect.top < rootRect.bottom) {
      visibleCount += 1
      enqueueMaterialPreview(material, priority)
    }
  }
  // A newly created WebView can report a zero-sized grid for one layout turn,
  // and some WebKit versions do not deliver the initial IntersectionObserver
  // callback for a nested scrolling grid. Keep the first viewport responsive
  // by queueing a small bounded prefix until geometry is available.
  if (!rootRect || rootRect.height <= 0 || visibleCount === 0) {
    materials.value.slice(0, Math.max(8, previewConcurrencyLimit.value * 2)).forEach((material) => {
      enqueueMaterialPreview(material, priority)
    })
  }
}

function setPreviewElement(material: MaterialDecision, element: unknown) {
  if (!(element instanceof HTMLElement)) return
  previewElements.set(material.source, element)
  if (previewObserver) previewObserver.observe(element)
}

function startPreviewObserver() {
  previewObserver?.disconnect()
  previewObserver = undefined
  previewBackgroundIndex = 0
  if (previewBackgroundTimer) window.clearTimeout(previewBackgroundTimer)
  if (previewScrollTimer) window.clearTimeout(previewScrollTimer)
  previewScrollRoot?.removeEventListener('scroll', onPreviewScroll)
  const root = confirmGridElement.value
  if (!root) return
  previewScrollRoot = root
  root.addEventListener('scroll', onPreviewScroll, { passive: true })
  if (typeof IntersectionObserver === 'function') {
    previewObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue
        const material = materials.value.find((item) => previewElements.get(item.source) === entry.target)
        if (material) enqueueMaterialPreview(material)
      }
    }, { root, rootMargin: '380px 0px' })
    previewElements.forEach((element) => previewObserver?.observe(element))
  }
  // Trigger once after refs are attached and once after the browser has laid
  // out the scroll grid. This covers the first-screen case without relying on
  // a scroll event to kick the observer.
  refreshMaterialPreviews()
  window.requestAnimationFrame(() => refreshMaterialPreviews())
  schedulePreviewBackground(350)
}

function schedulePreviewBackground(delay = 120, session = previewSession) {
  if (previewBackgroundTimer) window.clearTimeout(previewBackgroundTimer)
  if (previewScrolling || session !== previewSession) return
  previewBackgroundTimer = window.setTimeout(() => {
    previewBackgroundTimer = 0
    if (previewScrolling || session !== previewSession) return
    refreshMaterialPreviews()
    while (previewBackgroundIndex < materials.value.length) {
      const material = materials.value[previewBackgroundIndex]
      previewBackgroundIndex += 1
      if (previewLoadedSources.has(material.source) || previewFailedSources.has(material.source)
        || material.previewUrl || material.previewLoading || previewQueuedSources.has(material.source)) continue
      // Keep the background pass ordered and in the normal queue. The pump
      // still enforces the configured concurrency and deduplicates requests.
      enqueueMaterialPreview(material)
      break
    }
    if (previewBackgroundIndex < materials.value.length) schedulePreviewBackground(delay, session)
  }, delay)
}

function onPreviewScroll() {
  previewScrolling = true
  if (previewBackgroundTimer) window.clearTimeout(previewBackgroundTimer)
  if (previewScrollTimer) window.clearTimeout(previewScrollTimer)
  refreshMaterialPreviews(true)
  previewScrollTimer = window.setTimeout(() => {
    previewScrolling = false
    refreshMaterialPreviews(true)
    materials.value.slice(0, 8).forEach((material) => enqueueMaterialPreview(material))
    pumpMaterialPreviews()
    schedulePreviewBackground(120)
  }, 350)
}

function resetScale(source: string) {
  scaleAdjustments.value[source] = 0
}

function scaleTrack(source: string) {
  const percent = Math.max(0, Math.min(100, (Number(scaleAdjustments.value[source] || 0) + 50)))
  return { '--slider-track': `linear-gradient(to right, var(--accent) ${percent}%, var(--line) ${percent}%)` }
}

function baseScale(material: MaterialDecision) {
  const value = Number(material.metadata?.base_scale)
  return Number.isFinite(value) && value > 0 ? value : 0.75
}

function confirmMaterials() {
  if (!materials.value.length) {
    status.value = '没有可确认的图片素材'
    return
  }
  materialConfirmed.value = true
  status.value = '素材分类已确认，可以生成'
  taskPhase.value = '准备素材'
  taskDetail.value = '正在整理生成任务……'
  if (isConfirmWindow && window.__TAURI_INTERNALS__) {
    void (async () => {
      await emitTo('main', 'confirm-result', { ...confirmSnapshot(), action: 'confirm' })
      await WebviewWindow.getCurrent().close()
    })()
    return
  }
  confirmOpen.value = false
  safeSetWindowTitle(mainWindowTitle)
  startGeneration()
}

function dismissMaterialConfirmation() {
  if (isConfirmWindow && window.__TAURI_INTERNALS__) {
    void (async () => {
      await emitTo('main', 'confirm-result', { ...confirmSnapshot(), action: 'cancel' })
      await WebviewWindow.getCurrent().close()
    })()
    return
  }
  // Keep the classified cards and any manual edits so the next start can
  // reopen the same confirmation page instead of silently reclassifying.
  confirmOpen.value = false
  safeSetWindowTitle(mainWindowTitle)
  materialConfirmed.value = false
  status.value = '已取消素材确认，可继续确认或调整选项后重新开始'
  taskPhase.value = '准备就绪'
  taskDetail.value = '已保留当前素材分类和调整。'
}

function cancelGeneration() {
  if (!worker || (!isRunning.value && !classificationRunning.value) || isCancelling.value) return
  isCancelling.value = true
  status.value = '正在取消…'
  taskPhase.value = '正在安全停止'
  taskDetail.value = '会完成当前图片，并保留已经生成的输出。'
  cancelTargetRequestId.value = (classificationRunning.value ? classificationRequestId.value : generationRequestId.value) || undefined
  cancelRequestId.value = worker.request('cancel_generation', { request_id: cancelTargetRequestId.value || '' })
  clearCancelWatchdog()
  cancelWatchdog = window.setTimeout(() => {
    cancelWatchdog = undefined
    if (!isCancelling.value) return
    settleCancellation('取消请求超时', 'Worker 未在限定时间内确认停止；可以重试，已完成输出会保留。', '任务失败')
  }, 15_000)
}

function applyDroppedPaths(paths: string[]) {
  const normalized = paths.map((path) => path.trim()).filter(Boolean)
  if (!normalized.length) return
  const imageExtensions = new Set(['.jpg', '.jpeg', '.png'])
  const first = normalized[0]
  const extension = (path: string) => path.slice(path.lastIndexOf('.')).toLowerCase()
  const dirname = (path: string) => path.slice(0, Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\')))
  const folder = normalized.length === 1 && !imageExtensions.has(extension(first))
    ? first
    : normalized.every((path) => imageExtensions.has(extension(path))) && normalized.every((path) => dirname(path) === dirname(first))
      ? dirname(first)
      : ''
  if (!folder) {
    status.value = '请拖入一个素材文件夹，或同一文件夹内的 JPG、JPEG、PNG 图片'
    return
  }
  inputRoot.value = folder
  outputRoot.value = suggestedOutputRoot(folder)
  classifiedInputRoot.value = ''
  classifiedOptionsKey.value = ''
  materials.value = []
  categorySuggestion.value = undefined
  preflightWarnings.value = []
  materialConfirmed.value = false
  status.value = '已载入素材目录'
}

function onDrop(event: DragEvent) {
  const paths = Array.from(event.dataTransfer?.files || [])
    .map((file) => (file as File & { path?: string }).path || '')
  applyDroppedPaths(paths)
}

function toggleSize(size: string) {
  selectedSizes.value = selectedSizes.value.includes(size)
    ? selectedSizes.value.filter((item) => item !== size)
    : [...selectedSizes.value, size]
}

function startGeneration() {
  if (!inputRoot.value || !outputRoot.value || !selectedSizes.value.length) {
    status.value = '请先补全输入目录、输出目录和尺寸'
    return
  }
  if (maxSizeMb.value && (!Number.isFinite(Number(maxSizeMb.value)) || Number(maxSizeMb.value) < 0 || Number(maxSizeMb.value) > 100)) {
    status.value = '单张图片最大文件大小须在 0–100 MB 之间'
    return
  }
  if (!materialConfirmed.value) {
    try {
      localStorage.setItem('pixelflow.selected-sizes', JSON.stringify(selectedSizes.value))
      localStorage.setItem('pixelflow.max-size-mb', maxSizeMb.value)
    } catch { /* optional storage */ }
    if (materials.value.length && classifiedInputRoot.value === inputRoot.value && classifiedOptionsKey.value === currentClassificationKey()) {
      void openConfirmWindow()
      return
    }
    classifyMaterials()
    return
  }
  if (classifiedInputRoot.value !== inputRoot.value || classifiedOptionsKey.value !== currentClassificationKey()) {
    materialConfirmed.value = false
    classifyMaterials()
    return
  }
  if (!worker) {
    status.value = 'Worker 未连接，暂不能生成'
    return
  }
  setProgress(0, true)
  clearProgressComplete()
  isRunning.value = true
  status.value = '任务已准备'
  taskPhase.value = '准备素材'
  taskDetail.value = '正在整理生成任务……'
  const payload = generationPayload()
  generationRequestId.value = worker.request('start_generation', payload)
  persistGenerationResume(payload)
}
</script>

<template>
  <main class="app-shell" :class="{ 'theme-dark': darkMode, 'reduce-motion': reducedMotion, 'settings-window-shell': isSettingsWindow, 'report-window-shell': isReportWindow, 'confirm-window-shell': isConfirmWindow }" @dragover.prevent @drop.prevent="onDrop">
    <header v-if="!isSettingsWindow && !isReportWindow && !isConfirmWindow" class="topbar">
      <div class="brand-lockup">
        <img class="brand-mark" :src="appIconUrl" alt="" />
        <div>
          <div class="brand-name">PixelFlow</div>
          <div class="brand-version">内置当前主图制作规范、Logo 和唯品会边框资源</div>
        </div>
      </div>
      <div class="topbar-actions">
        <button class="icon-button settings-trigger" aria-label="打开设置" title="设置" @click="openSettings"><img class="settings-icon" :src="settingsIconUrl" alt="" /></button>
      </div>
    </header>

    <section v-if="!isSettingsWindow && !isReportWindow && !isConfirmWindow" class="content-grid v26-layout">
      <div class="main-column">
        <section class="panel folder-panel panel-enter delay-1">
          <div class="directory-grid">
            <label class="field-label folder-field"><span>产品素材文件夹</span><div class="field-row"><input v-model="inputRoot" placeholder="可点击选择，或拖入素材文件夹 / 图片" /><button class="choose-button" type="button" @click="pickDirectory('input')"><img :src="folderIconUrl" alt="" />选择…</button></div></label>
            <label class="field-label folder-field"><span>输出文件夹</span><div class="field-row"><input v-model="outputRoot" placeholder="" /><button class="choose-button" type="button" @click="pickDirectory('output')"><img :src="folderIconUrl" alt="" />选择…</button></div></label>
          </div>
        </section>

        <section class="options-section panel-enter delay-1">
          <div class="v26-heading"><span /> <h2>输出选项</h2></div>
          <div class="size-card-grid">
          <button v-for="size in ['1440x1440', '1440x1920', '1125x1500']" :key="size" type="button" class="option-card size-option" :class="{ active: selectedSizes.includes(size) }" :aria-pressed="selectedSizes.includes(size)" @click="toggleSize(size)"><i class="option-check" /> <strong>{{ size }}</strong><small>{{ size === '1440x1440' ? '1:1' : '3:4' }}</small></button>
          </div>
          <div class="toggle-card-grid">
            <label v-if="showLogoOption" class="option-card toggle-option" :class="{ active: includeLogo }"><input v-model="includeLogo" type="checkbox" /><i class="option-check" /><strong>生成含 Logo / 无 Logo</strong></label>
            <label v-if="showVipOption" class="option-card toggle-option" :class="{ active: includeVip }"><input v-model="includeVip" type="checkbox" /><i class="option-check" /><strong>生成唯品专享 1440</strong></label>
            <label v-if="showMaterialOption" class="option-card toggle-option wide-option" :class="{ active: enableMaterialUnderstanding }"><input v-model="enableMaterialUnderstanding" type="checkbox" /><i class="option-check" /><strong>启用素材理解层（规则识别）</strong><small>识别商品图、细节图、模特图和模特细节图；品类模板由下方选项独立控制</small></label>
          </div>
          <div class="advanced-row"><span>品类比例模板</span><div class="category-picker" @click.stop><button ref="categoryTriggerElement" class="category-trigger" type="button" :aria-expanded="categoryOpen" aria-haspopup="listbox" aria-controls="category-menu" @click="toggleCategoryPicker" @keydown="onCategoryKeydown">{{ categoryLabel }}<img :src="categoryArrowUrl" alt="" /></button><Transition name="dropdown"><div v-if="categoryOpen" id="category-menu" class="category-menu" role="listbox" tabindex="-1" @keydown="onCategoryKeydown"><button v-for="(label, value) in categoryLabels" :id="`category-option-${value}`" :key="value" type="button" role="option" :aria-selected="categoryTemplate === value" :class="{ active: categoryTemplate === value, highlighted: categoryHighlight === value }" @click="selectCategory(value)" @keydown.stop="onCategoryKeydown"><span>{{ label }}</span></button></div></Transition></div></div>
          <div class="advanced-row"><span>单张图片最大文件大小</span><label class="size-limit-field"><input v-model="maxSizeMb" type="number" min="0" max="100" step="0.01" inputmode="decimal" placeholder="不限制" aria-label="单张图片最大文件大小" /><span v-if="Number(maxSizeMb) > 0">MB</span></label></div>
        </section>

        <section class="panel progress-panel panel-enter delay-2">
          <div class="status-copy"><h2>生成状态</h2><span :key="taskPhase" class="status-phase">{{ taskPhase }}</span><strong aria-live="polite" aria-atomic="true">{{ taskDetail }}</strong></div>
          <div class="run-actions"><button v-if="resumeAvailable && !isRunning && !classificationRunning" type="button" class="outline-button resume-button" @click="resumeTask">继续未完成</button><button type="button" class="cancel-button" :disabled="(!isRunning && !classificationRunning) || isCancelling" @click="cancelGeneration">{{ isCancelling ? '取消中…' : '停止' }}</button><button type="button" class="primary-button" :class="{ busy: classificationRunning || isRunning }" :disabled="!canStart" :aria-busy="classificationRunning || isRunning" @click="startGeneration"><span>{{ classificationRunning ? '正在分析' : isRunning ? '任务已准备' : '开始生成' }}</span></button></div>
        </section>
        <div v-if="classificationRunning || isRunning || progressComplete" class="progress-track progress-bar-bottom" role="progressbar" aria-label="任务进度" aria-valuemin="0" aria-valuemax="1000" :aria-valuenow="Math.round(displayProgress)"><span :data-complete="progressComplete" :style="{ width: `${displayProgress / 10}%` }" /></div>
      </div>
    </section>

    <Transition name="settings" appear>
    <div v-if="settingsOpen" class="settings-backdrop settings-overlay" @click.self="closeSettings()">
      <section class="settings-dialog panel" role="dialog" aria-modal="true" aria-label="设置" tabindex="-1" @keydown="trapDialogFocus">
        <div class="settings-header"><button class="settings-back" aria-label="返回主界面" title="返回主界面" @click="closeSettings()"><span class="back-arrow" /></button><h2>设置</h2></div>
        <div class="settings-tabs"><button v-for="tab in settingsTabs" :key="tab.key" :class="{ active: settingsTab === tab.key }" @click="settingsTab = tab.key">{{ tab.label }}</button></div>
        <div class="settings-body" :key="settingsTab">
          <template v-if="settingsTab === 'brand'">
            <section class="settings-page-intro"><h3>品牌资源</h3><p>四个 Logo 直接显示真实预览；深色 Logo 对应浅色背景，浅色 Logo 对应深色背景。</p></section>
            <div v-for="row in logoRows" :key="row.title" class="logo-row"><div class="logo-row-label"><strong>{{ row.title }}</strong><span>{{ row.size }}</span></div><div v-for="variant in row.variants" :key="variant.label" class="logo-variant"><div class="logo-preview" :class="{ dark: variant.label.startsWith('深色'), tall: variant.tall }"><img :src="logoPreviewUrl(variant.key, variant.src)" alt="" /></div><div class="logo-meta"><strong>{{ variant.label }}</strong><span>{{ logoFilename(variant.key, variant.filename) }}</span><button @click="replaceLogo(variant.key)">更换</button></div></div></div>
            <p class="settings-rule">上传要求：PNG、RGBA 透明通道、完整尺寸画布。</p>
          </template>
          <template v-else-if="settingsTab === 'general'">
            <section class="settings-page-intro"><h3>通用设置</h3><p>控制界面反馈和处理过程中的通用行为。系统启用“减少动态效果”时会自动关闭动画。</p></section>
            <label class="general-setting-row"><span><strong>主题模式</strong><small>可跟随 macOS 外观，也可固定使用浅色或夜间模式</small></span><select v-model="theme"><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">夜间</option></select></label>
            <label class="general-setting-row"><span><strong>界面动画</strong><small>关闭确认卡片入场、核心按钮磁吸、进度过冲和状态弹性反馈</small></span><span class="general-check"><input v-model="reducedMotion" type="checkbox" />关闭动画</span></label>
            <label class="general-setting-row"><span><strong>分类并发级别</strong><small>预览并发 {{ previewConcurrencyLimit }} 张；自动按电脑性能调整分类线程，高性能最多使用 4 个分类线程，不会增加模型进程</small></span><select v-model="classificationConcurrency"><option value="auto">自动</option><option value="conservative">保守</option><option value="performance">高性能</option></select></label>
            <div class="general-setting-row"><span><strong>缓存</strong><small>清除 manifest 与 Pose/Seg 模型结果缓存；不会删除输出图片或模型文件</small></span><button class="settings-row-button" @click="clearCache">清理缓存</button></div>
          </template>
          <template v-else-if="settingsTab === 'output'">
            <section class="settings-page-intro"><h3>输出选项</h3><p>设置每个选项的默认状态，以及是否显示在主页。尺寸选择始终保留在主页。</p></section>
            <div class="settings-table-header"><span>选项</span><span>默认开启</span><span>主页显示</span></div>
            <div class="settings-option-row"><div><strong>生成含 Logo / 无 Logo</strong><small>在主页显示 Logo 输出开关</small></div><input v-model="includeLogo" aria-label="Logo 默认开启" type="checkbox" /><input v-model="showLogoOption" aria-label="Logo 主页显示" type="checkbox" /></div>
            <div class="settings-option-row"><div><strong>生成唯品专享 1440</strong><small>在主页显示唯品图输出开关</small></div><input v-model="includeVip" aria-label="唯品图默认开启" type="checkbox" /><input v-model="showVipOption" aria-label="唯品图主页显示" type="checkbox" /></div>
            <div class="settings-option-row"><div><strong>启用素材理解层（规则识别）</strong><small>在主页显示素材分类开关</small></div><input v-model="enableMaterialUnderstanding" aria-label="素材理解默认开启" type="checkbox" /><input v-model="showMaterialOption" aria-label="素材理解主页显示" type="checkbox" /></div>
            <div class="settings-option-row"><div><strong>自动生成模特主图</strong><small>模特图开关仅在设置页控制</small></div><input v-model="includeModelImages" aria-label="模特主图默认开启" type="checkbox" /><span class="not-applicable">—</span></div>
            <div class="settings-naming-row"><span><strong>文件命名</strong><small>简短模式使用编号和性别+颜色名，原始完整路径会写入输出清单；同名输出默认复用，强制重跑覆盖原路径，不自动重命名</small></span><div class="settings-naming-select"><button type="button" :aria-expanded="namingOpen" aria-haspopup="listbox" @click.stop="namingOpen = !namingOpen">{{ namingLabel }}<span class="select-chevron" /></button><Transition name="dropdown"><div v-if="namingOpen" class="compact-menu" role="listbox"><button v-for="(label, value) in namingOptions" :key="value" type="button" role="option" :aria-selected="outputNamingMode === value" @click="selectNaming(value)">{{ label }}</button></div></Transition></div></div>
          </template>
          <template v-else-if="settingsTab === 'model'">
            <section class="settings-page-intro"><h3>模特图增强</h3><p>外部 Pose/Seg 仅提供分类证据和安全构图；不可用时显示并使用规则判断。</p></section>
            <label class="settings-check"><input v-model="localModelEnabled" type="checkbox" />启用本地模型辅助判断</label>
            <div class="model-path-row"><input v-model="localModelPath" class="settings-input" readonly placeholder="尚未选择外部模型总目录" /><button class="outline-button" @click="pickDirectory('model')">选择总目录</button></div>
            <p class="settings-status">{{ modelStatus }}</p>
            <div class="model-diagnostics"><div v-for="row in modelDiagnosticRows" :key="row.key" class="model-diagnostic-row"><div><strong>{{ row.label }}</strong><small>期望文件：{{ row.filename }}</small><small>期望 SHA-256：{{ row.sha256.slice(0, 8) }}…</small><small>检测文件：{{ modelFileLabel(row.diagnostic) }}</small></div><span>{{ row.diagnostic?.state || '未检测' }} · SHA-256 {{ modelShaLabel(row.key, row.diagnostic) }}</span></div></div>
            <p v-if="modelFallbackWarning" class="settings-status settings-status-warning">模特图将使用保守构图，建议配置模型</p>
            <div class="settings-actions"><button class="text-button" :disabled="modelTesting" @click="probeModels">{{ modelTesting ? '检测中…' : '测试模型' }}</button></div>
          </template>
          <template v-else>
            <SettingsAboutView
              :expected-worker-version="expectedWorkerVersion"
              :worker-version="workerVersion"
              :worker-build-hash="workerBuildHash"
              :worker-rules-version="workerRulesVersion"
              :worker-ready-state="workerReadyState"
              @copy-diagnostics="copyDiagnostics"
              @export-diagnostics="exportDiagnostics"
            />
          </template>
        </div>
        <div class="settings-footer"><div class="settings-footer-left"><button class="text-button" @click="resetBrand">恢复默认品牌设置</button><span class="settings-notice">{{ settingsNotice }}</span></div><div><button class="outline-button" @click="closeSettings()">取消</button><button class="confirm-button" @click="saveSettings">保存设置</button></div></div>
      </section>
    </div>
    </Transition>

    <Transition name="dialog" appear>
    <div v-if="confirmOpen" class="settings-backdrop confirmation-backdrop" @click.self="dismissMaterialConfirmation">
      <section class="page-dialog panel confirmation-dialog" role="dialog" aria-modal="true" aria-label="素材确认" tabindex="-1" @keydown="trapDialogFocus">
        <div class="settings-header"><div><p class="eyebrow">CONFIRMATION</p><h2>确认素材分类 · {{ materials.length }} 张</h2><p>核对 v2.6 的自动识别结果，必要时调整分类、跳过模特素材或修改商品主体比例。</p></div><button class="modal-close" aria-label="关闭确认页" @click="dismissMaterialConfirmation">×</button></div>
        <div class="confirm-summary"><div>{{ categoryHint }}</div><div class="confirm-policy">同名输出默认复用；强制重跑会覆盖原路径，不自动重命名。可用空间低于 200 MB 时会在此处提示。</div><div class="confirm-model-status">模型状态：{{ classificationModelStatus }}</div><div v-if="modelConfirmFallbackWarning" class="confirm-warning">⚠ 模特图将使用保守构图，建议配置模型</div><div v-for="warning in preflightWarnings" :key="warning" class="confirm-warning">⚠ {{ warning }}</div>已识别 {{ materials.length }} 张 · 商品白底图 {{ materialCounts.main_product || 0 }} · 细节图 {{ materialCounts.detail || 0 }} · 模特细节图 {{ materialCounts.model_detail || 0 }} · 模特图 {{ materialCounts.model || 0 }} · 跳过 {{ skippedMaterialCount }}</div>
        <div v-if="!materials.length" class="confirm-empty">当前目录没有可识别图片，请返回主页检查素材目录。</div>
        <div class="confirm-grid-shell" :data-scroll-bottom="isConfirmGridScrolledToBottom" :data-has-overflow="confirmGridHasOverflow"><div ref="confirmGridElement" class="confirm-grid" @scroll="onConfirmGridScroll"><article v-for="(material, index) in materials" :key="material.source" :style="{ '--card-index': Math.min(index, 19) }" :data-visible="material.isVisible === true" :ref="(element) => { setPreviewElement(material, element); observeCard(element, material) }" class="confirm-card">
          <div class="confirm-preview"><div v-if="material.previewLoading && !material.previewUrl" class="preview-skeleton" /><img v-else-if="material.previewUrl" loading="lazy" decoding="async" :src="material.previewUrl" :alt="material.source" :class="{ 'preview-loaded': material.previewReady }" @load="onPreviewLoad(material)" @error="onMaterialPreviewError(material)" /><span v-else>{{ material.previewFailed ? '预览不可用' : '预览加载中…' }}</span></div>
          <div class="confirm-card-body"><strong :title="material.source">{{ material.source.split(/[\\/]/).pop() }}</strong><small class="confirm-folder">{{ material.source.split(/[\\/]/).slice(-2, -1)[0] || '素材根目录' }}</small><small v-if="material.metadata?.review_hint" class="confirm-review-hint">⚠ {{ material.metadata.review_hint }}</small><div class="material-select"><select :value="material.source_type" :title="material.reason" aria-label="素材分类" @change="onMaterialTypeChange(material, $event)"><option value="main_product">商品白底图</option><option value="detail">细节图</option><option value="model_detail">模特细节图</option><option value="model">模特图</option></select><span class="select-chevron" /></div><label class="skip-option" :class="{ hidden: material.source_type !== 'model' && material.source_type !== 'model_detail' }"><input v-model="excludedModelSources" type="checkbox" :value="material.source" />不适用于本次品类（跳过）</label><div class="scale-option" :class="{ hidden: material.source_type !== 'main_product' }"><div class="scale-row"><span>主体大小 {{ (scaleAdjustments[material.source] || 0) >= 0 ? '+' : '' }}{{ scaleAdjustments[material.source] || 0 }}%</span><button type="button" class="scale-reset" title="恢复为 0%" @click="resetScale(material.source)">↺</button></div><small>基础比例：{{ (baseScale(material) * 100).toFixed(1) }}%</small><strong>最终比例：{{ (baseScale(material) * (1 + (scaleAdjustments[material.source] || 0) / 100) * 100).toFixed(1) }}%</strong><input v-model.number="scaleAdjustments[material.source]" :style="scaleTrack(material.source)" type="range" min="-50" max="50" step="1" /></div></div>
        </article></div><div class="confirm-grid-scroll-hint" aria-hidden="true" /></div>
        <div class="page-actions"><button type="button" class="outline-button" @click="dismissMaterialConfirmation">取消</button><button type="button" class="confirm-button" :disabled="!materials.length" :class="{ confirmed: materialConfirmed }" @click="confirmMaterials">{{ materialConfirmed ? '已确认' : `确认并开始（处理 ${processMaterialCount} 张）` }}</button></div>
      </section>
    </div>
    </Transition>

    <Transition name="dialog" appear>
    <div v-if="reportOpen" class="settings-backdrop report-backdrop" @click.self="closeReport()">
      <section class="page-dialog panel report-dialog" role="dialog" aria-modal="true" aria-label="结果报告" tabindex="-1" @keydown="trapDialogFocus">
        <div class="report-header"><div class="report-success-icon">✓</div><div><p class="eyebrow">PIXELFLOW · 批量任务</p><h2>{{ resultCancelled ? '已安全停止' : '生成完成' }}</h2><p>{{ resultCancelled ? '已保留已完成的输出；未开始的任务不会继续处理。' : '输出已整理完成，可直接打开文件夹检查结果。' }}</p></div></div>
        <div class="report-summary"><div><b>{{ outputSummary.success }}</b><small>成功输出</small></div><div><b>{{ outputSummary.skipped }}</b><small>跳过素材</small></div><div><b>{{ outputSummary.failures }}</b><small>失败素材</small></div><div><b>{{ reviewItems.length }}</b><small>需要复核</small></div></div>
        <div class="report-info"><div class="report-info-row"><span>输出位置</span><span class="report-info-value">{{ outputRoot || '尚未选择' }}</span><button class="text-button report-copy-button" @click="copyOutputPath">复制</button></div><div class="report-info-row"><span>处理并发</span><span class="report-info-value">{{ resultWorkers }} 个工作线程</span></div><div class="report-info-row"><span>模型与审查</span><span class="report-info-value">Seg 疑难图 {{ resultSegUsed }} 张（{{ (resultSegRatio * 100).toFixed(1) }}%） · 尺寸提醒 {{ resultWarnings }} 条</span></div></div><p v-if="reportNotice" class="dialog-notice">{{ reportNotice }}</p>
        <p v-if="lastResult.incremental_skipped" class="report-cache-hint">检测到素材和设置均未变化，本次已复用上次输出。</p><p v-else-if="Number(lastResult.incremental_reused || 0) > 0" class="report-cache-hint">本次复用 {{ lastResult.incremental_reused }} 张未变化素材，其余素材已重新生成。</p>
        <div v-if="failures.length" class="report-failures">有 {{ failures.length }} 项失败素材；成功输出已保留，可在后续任务中重试。</div>
        <div class="page-actions report-actions"><div class="report-actions-left"><button type="button" class="confirm-button" @click="openOutputFolder()">打开输出文件夹</button></div><div class="report-actions-right"><button v-if="resumeAvailable" type="button" class="outline-button resume-button" @click="resumeTask">继续未完成</button><button v-if="reviewItems.length || fallbackItems.length || skippedOutputs.length" type="button" class="outline-button" @click="reviewOpen = true">查看复核与回退</button><button v-if="failures.length" type="button" class="outline-button" @click="failureOpen = true">查看失败素材</button><button v-if="failures.length" type="button" class="outline-button" @click="retryFailures()">仅重试失败项</button><button type="button" class="outline-button" @click="closeReport">完成</button></div></div>
      </section>
    </div>
    </Transition>

    <Transition name="dialog" appear>
    <div v-if="reviewOpen" class="settings-backdrop review-backdrop" @click.self="reviewOpen = false">
      <section class="page-dialog panel failure-dialog review-dialog" role="dialog" aria-modal="true" aria-label="复核与回退" tabindex="-1" @keydown="trapDialogFocus">
        <div class="settings-header"><div><p class="eyebrow">REVIEW QUEUE</p><h2>复核与兼容回退</h2><p>以下素材需要人工确认；已保留保护区域和回退原因。</p></div><button class="modal-close" aria-label="关闭复核列表" @click="reviewOpen = false">×</button></div>
        <div class="review-scroll-region">
          <div v-if="skippedOutputs.length" class="review-list report-skipped-list"><article v-for="item in skippedOutputs" :key="`skipped-${item.source}`" class="report-skipped-item"><strong>放弃出图：{{ item.source.split(/[\\/]/).pop() }} · 尺寸 {{ item.sizes.join('、') }}</strong><small>{{ item.reason }}</small></article></div>
          <div v-if="reviewItems.length" class="review-list"><article v-for="item in reviewItems" :key="String(item.source)"><strong>{{ String(item.source).split(/[\\/]/).pop() }}</strong><small>{{ String(item.review_hint || '建议检查构图') }} · 尺寸 {{ reviewSizes(item) }}</small><small>保护框：{{ reviewProtectedRegions(item) }}</small></article></div>
          <div v-if="fallbackItems.length" class="review-list"><article v-for="item in fallbackItems" :key="`fallback-${String(item.source)}`"><strong>兼容模式：{{ String(item.source).split(/[\\/]/).pop() }}</strong><small>{{ String(item.fallback_reason || '未提供回退原因') }}</small></article></div>
        </div>
        <p v-if="acceptedReviewSources.length" class="dialog-notice">已接受 {{ acceptedReviewSources.length }} 项复核结果</p>
        <div class="page-actions"><button class="outline-button" @click="reviewOpen = false">关闭</button><button v-if="reviewItems.length" class="outline-button" @click="acceptReviewItems">接受当前结果</button><button v-if="reviewItems.length" class="confirm-button" @click="reviewOpen = false; retryFailures(reviewSources(), true)">保守构图重跑</button></div>
      </section>
    </div>
    </Transition>

    <Transition name="dialog" appear>
    <div v-if="failureOpen" class="settings-backdrop" @click.self="failureOpen = false">
      <section class="page-dialog panel failure-dialog" role="dialog" aria-modal="true" aria-label="失败素材" tabindex="-1" @keydown="trapDialogFocus">
        <div class="settings-header"><div><p class="eyebrow">FAILED ITEMS</p><h2>失败素材</h2><p>以下素材未能输出；其余成功结果不受影响。</p></div><button class="modal-close" aria-label="关闭失败素材" @click="failureOpen = false">×</button></div>
        <pre class="failure-list">{{ failureReportText() }}</pre><p v-if="failureNotice" class="dialog-notice">{{ failureNotice }}</p>
        <div class="page-actions"><button class="outline-button" @click="copyFailureReport">复制清单</button><button class="outline-button" @click="exportFailureReport">导出报告</button><button class="outline-button" @click="failureOpen = false">关闭</button><button class="confirm-button" @click="retryFailures()">仅重试失败项</button></div>
      </section>
    </div>
    </Transition>
  </main>
</template>
