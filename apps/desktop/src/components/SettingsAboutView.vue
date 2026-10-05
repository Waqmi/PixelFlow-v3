<script setup lang="ts">
defineProps<{
  expectedWorkerVersion: string
  workerVersion: string
  workerBuildHash: string
  workerRulesVersion: string
  workerReadyState: string
}>()

const emit = defineEmits<{
  (event: 'copy-diagnostics'): void
  (event: 'export-diagnostics'): void
}>()
</script>

<template>
  <section class="settings-page-intro"><h3>关于 PixelFlow v3.0</h3><p>PixelFlow v3.0 正式版：本地批量整理电商素材并生成规范主图。</p></section>
  <dl class="about-list">
    <div><dt>版本号</dt><dd>{{ expectedWorkerVersion }}</dd></div>
    <div><dt>Worker 版本</dt><dd>{{ workerVersion }}</dd></div>
    <div><dt>Worker 哈希</dt><dd>{{ workerBuildHash ? workerBuildHash.slice(0, 8) : '未提供' }}</dd></div>
    <div><dt>规则版本</dt><dd>{{ workerRulesVersion }}</dd></div>
    <div><dt>Worker 状态</dt><dd>{{ workerReadyState }}</dd></div>
    <div><dt>功能</dt><dd>按尺寸、品类和 Logo 输出主图、透明产品图、唯品图及模特图。</dd></div>
    <div><dt>使用</dt><dd>选择输入和输出目录，设置选项后开始生成；可在确认页修正素材分类。</dd></div>
    <div><dt>处理方式</dt><dd>图片在本机处理；本地模型不可用时自动回退到规则模式。</dd></div>
    <div><dt>发布页</dt><dd><a href="https://github.com/Waqmi/PixelFlow-Open/releases" target="_blank">查看最新版本</a></dd></div>
  </dl>
  <div class="settings-actions">
    <button class="text-button" @click="emit('copy-diagnostics')">复制诊断信息</button>
    <button class="text-button" @click="emit('export-diagnostics')">导出诊断包</button>
  </div>
</template>
