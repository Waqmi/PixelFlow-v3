<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { windowWorkerBridge, WorkerClient } from './workerClient'
import type { WorkerMessage } from './workerProtocol'

type Theme = 'system' | 'light' | 'dark'
type MaterialType = 'main_product' | 'detail' | 'model' | 'model_detail'

interface MaterialDecision {
  source: string
  source_type: MaterialType
  confidence: number
  reason: string
}

const theme = ref<Theme>('system')
const themeOptions: Theme[] = ['system', 'light', 'dark']
const systemDark = ref(false)
const reducedMotion = ref(false)
const inputRoot = ref('')
const outputRoot = ref('')
const selectedSizes = ref(['1440x1440'])
const includeLogo = ref(true)
const includeVip = ref(true)
const isRunning = ref(false)
const isCancelling = ref(false)
const progress = ref(0)
const workerConnected = ref(false)
const status = ref('准备就绪')
const materials = ref<MaterialDecision[]>([])
const materialConfirmed = ref(false)
const classificationRunning = ref(false)
const classificationProgress = ref(0)
const generationRequestId = ref<string>()
const classificationRequestId = ref<string>()
let worker: WorkerClient | undefined
let progressTarget = 0
let progressFrame = 0

const darkMode = computed(() => theme.value === 'dark' || (theme.value === 'system' && systemDark.value))

onMounted(() => {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const update = () => { systemDark.value = media.matches }
  update()
  media.addEventListener('change', update)

  const bridge = windowWorkerBridge()
  if (bridge) {
    worker = new WorkerClient(bridge, handleWorkerMessage)
    worker.request('ping', {})
  }
})

onBeforeUnmount(() => worker?.dispose())
onBeforeUnmount(() => cancelAnimationFrame(progressFrame))

function setProgress(value: number, immediate = false) {
  progressTarget = Math.max(0, Math.min(100, value))
  if (immediate || reducedMotion.value) {
    progress.value = progressTarget
    cancelAnimationFrame(progressFrame)
    return
  }
  cancelAnimationFrame(progressFrame)
  const tick = () => {
    progress.value += (progressTarget - progress.value) * 0.24
    if (Math.abs(progressTarget - progress.value) < 0.2) {
      progress.value = progressTarget
      progressFrame = 0
      return
    }
    progressFrame = requestAnimationFrame(tick)
  }
  progressFrame = requestAnimationFrame(tick)
}

function handleWorkerMessage(message: WorkerMessage) {
  const payload = message.payload as Record<string, unknown>
  if (message.type === 'ready') {
    workerConnected.value = true
    status.value = '准备就绪'
  } else if (message.type === 'status' && message.request_id === classificationRequestId.value) {
    classificationRunning.value = true
    status.value = '正在分析素材'
  } else if (message.type === 'status' && message.request_id === generationRequestId.value && payload.state === 'started') {
    isRunning.value = true
    isCancelling.value = false
    status.value = '任务已开始'
  } else if (message.type === 'progress' && message.request_id === classificationRequestId.value) {
    const total = Number(payload.total || 0)
    classificationProgress.value = total ? Math.round((Number(payload.completed || 0) / total) * 100) : 0
    status.value = '正在分析素材'
  } else if (message.type === 'done' && message.request_id === classificationRequestId.value) {
    const result = payload.result as { decisions?: MaterialDecision[] } | undefined
    materials.value = Array.isArray(result?.decisions) ? result.decisions : []
    classificationRunning.value = false
    classificationProgress.value = 100
    materialConfirmed.value = false
    status.value = materials.value.length ? '请确认素材分类' : '未发现可分类素材'
  } else if (message.type === 'progress' && message.request_id === generationRequestId.value) {
    const total = Number(payload.total || 0)
    setProgress(total ? Math.round((Number(payload.completed || 0) / total) * 100) : 0)
    status.value = String(payload.phase || '处理中')
  } else if (message.type === 'done' && message.request_id === generationRequestId.value) {
    isRunning.value = false
    isCancelling.value = false
    setProgress(100, true)
    status.value = '生成完成'
  } else if (message.type === 'cancelled' && message.request_id === generationRequestId.value) {
    isRunning.value = false
    isCancelling.value = false
    status.value = '任务已取消'
  } else if (message.type === 'error') {
    isRunning.value = false
    isCancelling.value = false
    classificationRunning.value = false
    status.value = String(payload.message || 'Worker 执行失败')
  }
}

function classifyMaterials() {
  if (!inputRoot.value) {
    status.value = '请先填写输入目录'
    return
  }
  if (!worker) {
    status.value = 'Worker 未连接，暂不能分析素材'
    return
  }
  materials.value = []
  materialConfirmed.value = false
  classificationProgress.value = 0
  classificationRequestId.value = worker.request('classify_materials', { product_root: inputRoot.value })
}

function setMaterialType(material: MaterialDecision, type: MaterialType) {
  material.source_type = type
  materialConfirmed.value = false
}

function onMaterialTypeChange(material: MaterialDecision, event: Event) {
  setMaterialType(material, (event.target as HTMLSelectElement).value as MaterialType)
}

function confirmMaterials() {
  if (!materials.value.length) return
  materialConfirmed.value = true
  status.value = '素材分类已确认，可以生成'
}

function cancelGeneration() {
  if (!worker || !isRunning.value || isCancelling.value) return
  isCancelling.value = true
  status.value = '正在取消…'
  worker.request('cancel_generation', {})
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
  if (!materialConfirmed.value) {
    status.value = '请先分析并确认素材分类'
    return
  }
  if (!worker) {
    status.value = 'Worker 未连接，暂不能生成'
    return
  }
  setProgress(0, true)
  generationRequestId.value = worker.request('start_generation', {
    product_root: inputRoot.value,
    output_root: outputRoot.value,
    selected_sizes: selectedSizes.value,
    include_logo: includeLogo.value,
    include_vip: includeVip.value,
    material_confirmation_completed: true,
    source_type_overrides: Object.fromEntries(materials.value.map((item) => [item.source, item.source_type])),
  })
}
</script>

<template>
  <main class="app-shell" :class="{ 'theme-dark': darkMode, 'reduce-motion': reducedMotion }">
    <header class="topbar">
      <div class="brand-lockup">
        <div class="brand-mark">P</div>
        <div>
          <div class="brand-name">PixelFlow</div>
          <div class="brand-version">v3 beta · image workspace</div>
        </div>
      </div>
      <div class="topbar-actions">
        <span class="connection-pill"><i :class="{ connected: workerConnected }" /> {{ workerConnected ? 'Worker 已连接' : 'Worker 未连接' }}</span>
        <button class="icon-button" aria-label="切换夜间模式" @click="theme = darkMode ? 'light' : 'dark'">
          {{ darkMode ? '☼' : '☾' }}
        </button>
      </div>
    </header>

    <section class="content-grid">
      <div class="main-column">
        <section class="hero-card panel-enter">
          <div>
            <p class="eyebrow">NEW WORKFLOW</p>
            <h1>让每一张商品图，<em>更快到位。</em></h1>
            <p class="hero-copy">沿用 v2.6 的输出逻辑，重新组织素材确认、生成和复用流程。</p>
          </div>
          <div class="hero-orbit" aria-hidden="true"><span /><span /><span /></div>
        </section>

        <section class="panel settings-panel panel-enter delay-1">
          <div class="section-heading">
            <div><p class="eyebrow">01 · SOURCE</p><h2>素材与输出</h2></div>
            <span class="step-chip">基础设置</span>
          </div>
          <label class="field-label">输入目录<input v-model="inputRoot" placeholder="选择商品素材目录" /></label>
          <label class="field-label">输出目录<input v-model="outputRoot" placeholder="选择生成结果目录" /></label>

          <div class="setting-row">
            <div><span class="field-label plain">输出尺寸</span><span class="hint">可多选，保持 v2.6 目录兼容</span></div>
            <div class="segmented">
              <button v-for="size in ['1440x1440', '1440x1920', '1125x1500']" :key="size" :class="{ active: selectedSizes.includes(size) }" @click="toggleSize(size)">{{ size }}</button>
            </div>
          </div>

          <div class="setting-row compact-row">
            <div><span class="field-label plain">生成选项</span><span class="hint">颜色与边框沿用当前工作区风格</span></div>
            <div class="switches">
              <label class="switch-label"><input v-model="includeLogo" type="checkbox" /><span class="switch" />Logo</label>
              <label class="switch-label"><input v-model="includeVip" type="checkbox" /><span class="switch" />唯品图</label>
            </div>
          </div>
        </section>

        <section class="panel materials-panel panel-enter delay-2">
          <div class="section-heading">
            <div><p class="eyebrow">02 · CONFIRM</p><h2>素材确认</h2></div>
            <button class="outline-button" :disabled="classificationRunning" @click="classifyMaterials">{{ classificationRunning ? '分析中…' : '分析素材' }}</button>
          </div>
          <div v-if="classificationRunning" class="classification-progress">
            <div class="progress-track"><span :style="{ width: `${classificationProgress}%` }" /></div>
            <span>{{ classificationProgress }}%</span>
          </div>
          <div v-else-if="!materials.length" class="empty-materials">分析输入目录后，在这里确认白底图、细节图和模特图。</div>
          <div v-else class="material-list">
            <div v-for="material in materials" :key="material.source" class="material-row">
              <div class="material-icon">{{ material.source_type.startsWith('model') ? '人' : '图' }}</div>
              <div class="material-name"><strong>{{ material.source.split('/').pop() }}</strong><span>{{ material.reason }}</span></div>
              <select :value="material.source_type" aria-label="素材分类" @change="onMaterialTypeChange(material, $event)">
                <option value="main_product">商品主图</option>
                <option value="detail">细节图</option>
                <option value="model">模特图</option>
                <option value="model_detail">模特细节图</option>
              </select>
            </div>
            <button class="confirm-button" :class="{ confirmed: materialConfirmed }" @click="confirmMaterials">{{ materialConfirmed ? '分类已确认' : '确认分类' }}</button>
          </div>
        </section>

        <section class="panel progress-panel panel-enter delay-2">
          <div class="section-heading"><div><p class="eyebrow">03 · RUN</p><h2>任务状态</h2></div><span class="status-text">{{ status }}</span></div>
          <div class="progress-track"><span :style="{ width: `${progress}%` }" /></div>
          <div class="progress-meta"><span>{{ isRunning ? '初始化 Worker' : '等待开始' }}</span><span>{{ progress }}%</span></div>
          <div class="run-actions">
            <button class="primary-button" :disabled="isRunning" @click="startGeneration"><span>{{ isRunning ? '任务已准备' : '开始生成' }}</span><b>→</b></button>
            <button v-if="isRunning" class="cancel-button" :disabled="isCancelling" @click="cancelGeneration">{{ isCancelling ? '取消中…' : '取消任务' }}</button>
          </div>
        </section>
      </div>

      <aside class="side-column">
        <section class="panel summary-card panel-enter delay-1">
          <div class="section-heading"><div><p class="eyebrow">WORKSPACE</p><h2>当前配置</h2></div><span class="ready-dot" /></div>
          <dl><div><dt>尺寸</dt><dd>{{ selectedSizes.length }} 项</dd></div><div><dt>Logo</dt><dd>{{ includeLogo ? '开启' : '关闭' }}</dd></div><div><dt>唯品图</dt><dd>{{ includeVip ? '开启' : '关闭' }}</dd></div></dl>
          <div class="tip-box"><span>✦</span><p>先完成素材确认，Worker 才会开始渲染。</p></div>
        </section>
        <section class="panel appearance-card panel-enter delay-2">
          <div class="section-heading"><div><p class="eyebrow">APPEARANCE</p><h2>界面偏好</h2></div></div>
          <div class="theme-options"><button v-for="item in themeOptions" :key="item" :class="{ active: theme === item }" @click="theme = item">{{ item === 'system' ? '跟随系统' : item === 'light' ? '浅色' : '夜间' }}</button></div>
          <label class="motion-option"><input v-model="reducedMotion" type="checkbox" /><span class="switch" /><span>减少动态效果</span></label>
          <p class="hint motion-hint">降低位移与过渡，不影响任务状态准确性。</p>
        </section>
      </aside>
    </section>
  </main>
</template>
