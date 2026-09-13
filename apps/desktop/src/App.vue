<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

type Theme = 'system' | 'light' | 'dark'

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
const status = ref('准备就绪')

const darkMode = computed(() => theme.value === 'dark' || (theme.value === 'system' && systemDark.value))
const progress = computed(() => (isRunning.value ? 18 : 0))

onMounted(() => {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const update = () => { systemDark.value = media.matches }
  update()
  media.addEventListener('change', update)
})

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
  isRunning.value = true
  status.value = '已准备任务，等待 Worker 接入'
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
        <span class="connection-pill"><i /> Worker 未连接</span>
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

        <section class="panel progress-panel panel-enter delay-2">
          <div class="section-heading"><div><p class="eyebrow">02 · RUN</p><h2>任务状态</h2></div><span class="status-text">{{ status }}</span></div>
          <div class="progress-track"><span :style="{ width: `${progress}%` }" /></div>
          <div class="progress-meta"><span>{{ isRunning ? '初始化 Worker' : '等待开始' }}</span><span>{{ progress }}%</span></div>
          <button class="primary-button" :disabled="isRunning" @click="startGeneration"><span>{{ isRunning ? '任务已准备' : '开始生成' }}</span><b>→</b></button>
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
